"""Passkeys (WebAuthn) for signing in to the dashboard: a fingerprint, face, PIN, phone or security
key instead of a password. Nothing secret is stored here, only each passkey's public key.

Standard library only, so this file carries the few pieces WebAuthn needs: reading CBOR, and
checking ECDSA P-256 (ES256) and RSA PKCS#1 v1.5 (RS256, what Windows Hello often uses) signatures.
Checking a signature involves no secrets, so plain Python integers are fine for it.

A passkey belongs to the address it was made for (the "relying party id", the host name): one made
at localhost does not work at drive.example.com. Browsers only offer passkeys on https or localhost.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import struct
import time


class PasskeyError(Exception):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64u(text) -> bytes:
    text = str(text or "")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise PasskeyError("badly formed data") from None


# ------------------------------------------------------------------ CBOR (what authenticators send)
def cbor(data: bytes, pos=0):
    """Decode one CBOR item: (value, position after it)."""
    try:
        first = data[pos]
        major, info = first >> 5, first & 31
        pos += 1
        if info < 24:
            n = info
        elif info in (24, 25, 26, 27):
            size = 1 << (info - 24)
            n = int.from_bytes(data[pos:pos + size], "big")
            pos += size
        else:
            raise PasskeyError("unsupported CBOR")
        if major == 0:
            return n, pos
        if major == 1:
            return -1 - n, pos
        if major in (2, 3):
            raw = data[pos:pos + n]
            if len(raw) != n:
                raise PasskeyError("truncated CBOR")
            return (raw if major == 2 else raw.decode("utf-8")), pos + n
        if major == 4:
            out = []
            for _ in range(n):
                v, pos = cbor(data, pos)
                out.append(v)
            return out, pos
        if major == 5:
            out = {}
            for _ in range(n):
                k, pos = cbor(data, pos)
                v, pos = cbor(data, pos)
                out[k] = v
            return out, pos
        if major == 7 and info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], pos
    except (IndexError, UnicodeDecodeError, TypeError):
        pass
    raise PasskeyError("unsupported CBOR")


# ------------------------------------------------------------------ signatures
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    (x1, y1), (x2, y2) = p, q
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k, p):
    out = None
    while k:
        if k & 1:
            out = _add(out, p)
        p = _add(p, p)
        k >>= 1
    return out


def on_curve(x, y):
    return 0 < x < P and 0 < y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def _der_ints(sig: bytes):
    """The two integers of a DER-encoded ECDSA signature."""
    try:
        if sig[0] != 0x30:
            raise ValueError
        pos = 2 if sig[1] < 0x80 else 2 + (sig[1] & 0x7F)
        out = []
        for _ in range(2):
            if sig[pos] != 0x02:
                raise ValueError
            n = sig[pos + 1]
            out.append(int.from_bytes(sig[pos + 2:pos + 2 + n], "big"))
            pos += 2 + n
        return out
    except (IndexError, ValueError):
        raise PasskeyError("badly formed signature") from None


def verify_es256(x, y, message: bytes, signature: bytes) -> bool:
    r, s = _der_ints(signature)
    if not (0 < r < N and 0 < s < N) or not on_curve(x, y):
        return False
    e = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, N)
    point = _add(_mul(e * w % N, (GX, GY)), _mul(r * w % N, (x, y)))
    return point is not None and point[0] % N == r


_SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_rs256(n, e, message: bytes, signature: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if k < 256 or len(signature) != k or e < 3:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(_SHA256_INFO) - 32) + b"\x00" + _SHA256_INFO + hashlib.sha256(message).digest()
    return hmac.compare_digest(em, want)


# ------------------------------------------------------------------ WebAuthn
def rp_id(host):
    """The host name passkeys are tied to, or None where browsers don't allow them (bare IP addresses)."""
    host = (host or "").strip().lower()
    if not host or host.startswith("["):           # an IPv6 address
        return None
    host = host.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        return host


class Challenges:
    """One-time random values the browser has to sign (kept a few minutes)."""

    def __init__(self):
        self._live = {}

    def new(self, purpose):
        now = time.time()
        self._live = {c: v for c, v in self._live.items() if v[1] > now}
        c = b64u(os.urandom(32))
        self._live[c] = (purpose, now + 300)
        return c

    def use(self, challenge, purpose):
        got = self._live.pop(str(challenge), None)
        return got is not None and got[0] == purpose and got[1] > time.time()


def _client_data(raw: bytes, kind, challenges, purpose, origins):
    try:
        data = json.loads(raw)
    except ValueError:
        raise PasskeyError("badly formed data") from None
    if data.get("type") != kind:
        raise PasskeyError("wrong kind of answer")
    if not challenges.use(data.get("challenge"), purpose):
        raise PasskeyError("that took too long; try again")
    if data.get("origin") not in origins:
        raise PasskeyError("the answer was made for another address")


def _auth_data(raw: bytes, rp):
    if len(raw) < 37 or not hmac.compare_digest(raw[:32], hashlib.sha256(rp.encode()).digest()):
        raise PasskeyError("the passkey belongs to another address")
    flags = raw[32]
    if not flags & 0x01:
        raise PasskeyError("the passkey was not confirmed by a person")
    return flags, struct.unpack(">I", raw[33:37])[0]


def register(attestation: bytes, client_data: bytes, rp, origins, challenges):
    """Check a new passkey: {"id", "alg", and its public key: "x","y" (ES256) or "n","e" (RS256)}."""
    _client_data(client_data, "webauthn.create", challenges, "register", origins)
    obj, _ = cbor(attestation)
    auth = obj.get("authData") if isinstance(obj, dict) else None
    if not isinstance(auth, bytes):
        raise PasskeyError("badly formed data")
    flags, count = _auth_data(auth, rp)
    if not flags & 0x40 or len(auth) < 55:
        raise PasskeyError("no passkey in the answer")
    n = struct.unpack(">H", auth[53:55])[0]
    cred_id = auth[55:55 + n]
    key, _ = cbor(auth, 55 + n)
    if not isinstance(key, dict) or len(cred_id) != n or not cred_id:
        raise PasskeyError("badly formed data")
    out = {"id": b64u(cred_id), "count": count}
    if key.get(1) == 2 and key.get(3) == -7 and key.get(-1) == 1:
        x, y = int.from_bytes(key.get(-2) or b"", "big"), int.from_bytes(key.get(-3) or b"", "big")
        if not on_curve(x, y):
            raise PasskeyError("the passkey's key is not valid")
        out.update(alg=-7, x=b64u(key[-2]), y=b64u(key[-3]))
    elif key.get(1) == 3 and key.get(3) == -257:
        out.update(alg=-257, n=b64u(key.get(-1) or b""), e=b64u(key.get(-2) or b""))
    else:
        raise PasskeyError("this kind of passkey isn't supported (ES256 or RS256 is needed)")
    return out


def verify(passkey, auth_data: bytes, client_data: bytes, signature: bytes, rp, origins, challenges):
    """Check a sign-in made with `passkey`. Returns its new use counter."""
    _client_data(client_data, "webauthn.get", challenges, "login", origins)
    _, count = _auth_data(auth_data, rp)
    signed = auth_data + hashlib.sha256(client_data).digest()
    if passkey.get("alg") == -7:
        ok = verify_es256(int.from_bytes(unb64u(passkey["x"]), "big"), int.from_bytes(unb64u(passkey["y"]), "big"), signed, signature)
    elif passkey.get("alg") == -257:
        ok = verify_rs256(int.from_bytes(unb64u(passkey["n"]), "big"), int.from_bytes(unb64u(passkey["e"]), "big"), signed, signature)
    else:
        ok = False
    if not ok:
        raise PasskeyError("the passkey's signature doesn't match")
    old = int(passkey.get("count") or 0)
    if count and old and count <= old:
        raise PasskeyError("this passkey seems to have been copied; remove it and make a new one")
    return count
