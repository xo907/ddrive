"""Share the encryption key with a new device through the Discord channel.

1. The new device posts a request (DDKEYREQ1) holding a one-time Diffie-Hellman public value,
   and shows a verification code derived from it.
2. A device that has the key lists recent requests; the user checks the code on both screens
   and accepts or rejects.
3. On accept, that device posts a reply (DDKEYRESP1) with its own one-time public value and the
   keys, encrypted with AES-256-GCM under a key both devices derive from the Diffie-Hellman
   secret. Discord (or anyone with the bot token) only ever sees public values and ciphertext.
4. The new device decrypts the keys and removes both messages from the channel.

Only the Python standard library is used for the exchange (RFC 3526 2048-bit MODP group), so it
works wherever DDrive's AES-GCM works.
"""

import base64
import hashlib
import json
import secrets
import sys
import time

from .crypto import CryptoEngine

REQ = "DDKEYREQ1 "
RESP = "DDKEYRESP1 "
MAX_AGE = 15 * 60          # requests older than this are ignored

# RFC 3526, group 14 (2048-bit MODP), generator 2
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
G = 2


class Rejected(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _int_bytes(n: int) -> bytes:
    return n.to_bytes(256, "big")


def _keypair():
    priv = secrets.randbits(256) | 1
    return priv, pow(G, priv, P)


def _valid_public(y: int) -> bool:
    return 2 <= y <= P - 2


def verification_code(request_pub: int) -> str:
    """Short code shown on both devices, derived from the request's public value."""
    h = hashlib.sha256(b"DiscordDrive-keyshare-code" + _int_bytes(request_pub)).hexdigest().upper()
    return f"{h[:3]}-{h[3:6]}"


def _session_key(rid: str, shared: int) -> bytes:
    return hashlib.sha256(b"DiscordDrive-keyshare-v1" + rid.encode() + _int_bytes(shared)).digest()


def _post(backend, prefix, obj):
    """Post a message; if it would exceed Discord's 2000-character limit, send it as an attachment."""
    raw = json.dumps(obj, separators=(",", ":")).encode()
    text = prefix + _b64(raw)
    if len(text) <= 1900:
        return backend.post_text(text)
    return backend.upload(f"keyshare_{secrets.token_hex(4)}.bin", raw, content=prefix + "@").message_id


def _decode(backend, m, prefix):
    try:
        body = (m.get("content") or "")[len(prefix):].strip()
        raw = backend.fetch_attachment(m) if body == "@" else base64.b64decode(body)
        return json.loads(raw)
    except Exception:
        return None


def device_label():
    return {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux") + " device"


# --------------------------------------------------------------- new device
class KeyRequest:
    """The new device's side."""

    def __init__(self, backend, label=None):
        self.backend = backend
        self.rid = secrets.token_hex(8)
        self._priv, self.pub = _keypair()
        self.code = verification_code(self.pub)
        self.label = label or device_label()
        self.mid = None

    def post(self):
        self.mid = _post(self.backend, REQ, {
            "v": 1, "id": self.rid, "pub": _b64(_int_bytes(self.pub)), "dev": self.label, "t": int(time.time())})
        return self.mid

    def wait(self, timeout=600.0, interval=3.0, on_wait=None):
        """Poll for the answer. Returns {'key': hex, 'old': [hex, ...]}; raises Rejected / TimeoutError."""
        end = time.time() + timeout
        cursor = self.mid
        while time.time() < end:
            for m in self.backend.messages_after(cursor):
                cursor = m["id"]
                content = m.get("content") or ""
                if not content.startswith(RESP):
                    continue
                msg = _decode(self.backend, m, RESP)
                if not msg or msg.get("id") != self.rid:
                    continue
                self._cleanup(m["id"])
                if msg.get("rejected"):
                    raise Rejected("The request was rejected on the other device.")
                try:
                    their = int.from_bytes(base64.b64decode(msg["pub"]), "big")
                    if not _valid_public(their):
                        raise ValueError("invalid public value")
                    engine = CryptoEngine(_session_key(self.rid, pow(their, self._priv, P)))
                    try:
                        payload = json.loads(engine.decrypt(base64.b64decode(msg["data"])))
                    finally:
                        engine.close()
                except Exception as e:
                    raise Rejected(f"The answer could not be decrypted ({e}); ask again.") from None
                return payload
            if on_wait:
                on_wait()
            time.sleep(interval)
        self._cleanup()
        raise TimeoutError("No answer in time.")

    def _cleanup(self, *extra):
        for mid in (self.mid, *extra):
            if mid:
                try:
                    self.backend.delete(mid)
                except Exception:
                    pass


# ------------------------------------------------------- device with the key
def pending_requests(backend, max_age=MAX_AGE, scan=500):
    """Recent, unanswered key requests (newest first)."""
    since = backend.id_for_time(time.time() - max_age)
    requests, answered = {}, set()
    cursor, seen = since, 0
    while seen < scan:
        page = backend.messages_after(cursor)
        if not page:
            break
        for m in page:
            content = m.get("content") or ""
            if content.startswith(REQ):
                msg = _decode(backend, m, REQ)
                if msg and msg.get("v") == 1:
                    try:
                        pub = int.from_bytes(base64.b64decode(msg["pub"]), "big")
                    except Exception:
                        continue
                    if _valid_public(pub) and time.time() - msg.get("t", 0) < max_age:
                        requests[msg["id"]] = {"id": msg["id"], "pub": pub, "device": str(msg.get("dev", ""))[:40],
                                               "time": msg.get("t", 0), "mid": m["id"],
                                               "code": verification_code(pub)}
            elif content.startswith(RESP):
                msg = _decode(backend, m, RESP)
                if msg:
                    answered.add(msg.get("id"))
        seen += len(page)
        cursor = page[-1]["id"]
        if len(page) < 100:
            break
    return sorted((r for rid, r in requests.items() if rid not in answered), key=lambda r: -r["time"])


def answer(backend, request, accept, keys=None):
    """Accept (send `keys` = {'key': hex, 'old': [...]}) or reject a request."""
    if not accept:
        _post(backend, RESP, {"v": 1, "id": request["id"], "rejected": True})
        return
    priv, pub = _keypair()
    engine = CryptoEngine(_session_key(request["id"], pow(request["pub"], priv, P)))
    try:
        data = engine.encrypt(json.dumps(keys, separators=(",", ":")).encode())
    finally:
        engine.close()
    _post(backend, RESP, {"v": 1, "id": request["id"], "pub": _b64(_int_bytes(pub)), "data": _b64(data)})
