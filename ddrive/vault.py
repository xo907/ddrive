"""End-to-end encrypted accounts: what a device does so that the computer running the drive (the
host) never sees a file, a file's name, or a key.

An end-to-end account's 24 words never leave the device they are typed on. From the key K they stand
for, the device makes two others (HKDF-SHA256, no salt):

    auth = HKDF(K, "ddrive vault v1 auth")    what the device signs in with. The host knows this one,
                                              and only this one: it can't be turned back into K.
    meta = HKDF(K, "ddrive vault v1 meta")    encrypts the list of files (names, folders, sizes,
                                              dates, and each file's own key). Never sent.

To the host such an account is a place for sealed things with meaningless names:

    objects   a file's content, encrypted on the device with a key made for that file alone, under
              a random id. The host stores them on Discord like any file (spare pieces, repair and
              quota work as ever) and hands them back, whole or in ranges.
    the log   the list of files, as a sequence of sealed changes ("this file is called x, lies in
              folder y, is object z with key k"). The host numbers them in the order they arrive and
              gives them back in that order, so every device of the account ends up with the same
              list. Now and then a device seals the whole list as one object (a snapshot) and the
              changes before it are dropped.

What the host can still see: that sealed things exist, about how big they are, when they come and go,
and from which address. Nothing else.

Formats (the same in the browser, in the phone app and here):

    sealed    nonce (12 bytes) + tag (16) + ciphertext, AES-256-GCM, with a label that says what it
              is (so one kind of sealed thing can't be passed off as another).
    object    "DDV1" + one byte n (pieces are 2**n bytes of the file; 20 = 1 MiB), then the pieces,
              each sealed with the file's key and the label "ddv1 chunk" + its number (8 bytes) + 1
              if it is the last one, else 0. Any piece can be opened on its own, which is what
              playing a video from the middle needs, and none can be swapped, left out or cut off
              unnoticed. An empty file is one empty piece.
    a change  sealed JSON, label "ddv1 op"; several are sent together as a batch: each one preceded
              by its length (4 bytes).
"""

import base64
import hashlib
import hmac
import json
import os
import posixpath
import secrets
import struct
import time

from .crypto import AuthenticationError, CryptoEngine

INFO_AUTH = b"ddrive vault v1 auth"
INFO_META = b"ddrive vault v1 meta"
AAD_OP = b"ddv1 op"
AAD_CHUNK = b"ddv1 chunk"
MAGIC = b"DDV1"
CHUNK_LOG = 20                    # a file is sealed in pieces of 1 MiB
OVERHEAD = 28                     # nonce + tag of a sealed thing
PART = 8 * 1024 * 1024            # sealed pieces go to the host this many bytes of the file at a time
SNAPSHOT_EVERY = 400              # changes after which the whole list is sealed anew


class VaultError(Exception):
    pass


def hkdf(key: bytes, info: bytes, n: int = 32) -> bytes:
    """HKDF-SHA256 with no salt, for up to 32 bytes."""
    prk = hmac.new(b"\0" * 32, key, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def auth_key(key: bytes) -> bytes:
    """What an end-to-end account signs in with: made from its key, but no way back to it."""
    return hkdf(key, INFO_AUTH)


def meta_key(key: bytes) -> bytes:
    return hkdf(key, INFO_META)


_engines = {}


def _engine(key: bytes) -> CryptoEngine:
    e = _engines.get(key)
    if e is None:
        if len(_engines) > 64:
            _engines.clear()
        e = _engines[key] = CryptoEngine(key)
    return e


def seal(key: bytes, data: bytes, label: bytes) -> bytes:
    """nonce + tag + ciphertext."""
    return _engine(key).encrypt(data, aad=label)[4:]         # (the engine's own four-letter mark is left off)


def unseal(key: bytes, blob: bytes, label: bytes) -> bytes:
    try:
        return _engine(key).decrypt(b"DENC" + blob, aad=label)
    except AuthenticationError:
        raise VaultError("This can't be opened with these 24 words (or it was changed on the way).") from None


def _label(n: int, last: bool) -> bytes:
    return AAD_CHUNK + struct.pack(">Q", n) + (b"\x01" if last else b"\x00")


def sealed_size(size: int, log: int = CHUNK_LOG) -> int:
    """How big a file of `size` bytes is as an object."""
    return len(MAGIC) + 1 + size + OVERHEAD * max(1, -(-size // (1 << log)))


def seal_stream(key: bytes, read, size: int, log: int = CHUNK_LOG):
    """A file as an object, a part at a time: yields bytes. read(n) gives the next n bytes of the file."""
    chunk = 1 << log
    yield MAGIC + bytes([log])
    count = max(1, -(-size // chunk))
    for n in range(count):
        want = min(chunk, size - n * chunk)
        data = read(want) if want else b""
        if len(data) != want:
            raise VaultError("The file changed while it was being sealed.")
        yield seal(key, data, _label(n, n == count - 1))


def open_range(key: bytes, fetch, size: int, start: int = 0, length: int = None, log: int = CHUNK_LOG) -> bytes:
    """Bytes [start, start+length) of a file of `size` bytes from its object. fetch(a, n) gives n
    bytes of the object from offset a. Only the pieces that are needed are fetched."""
    chunk = 1 << log
    end = size if length is None else min(size, start + length)
    if start >= end:
        if size == 0 and start == 0:         # an empty file is still checked: its one empty piece has to be there
            unseal(key, fetch(len(MAGIC) + 1, OVERHEAD), _label(0, True))
        return b""
    count = max(1, -(-size // chunk))
    first, last = start // chunk, (end - 1) // chunk
    a = len(MAGIC) + 1 + first * (chunk + OVERHEAD)
    b = len(MAGIC) + 1 + last * (chunk + OVERHEAD) + min(chunk, size - last * chunk) + OVERHEAD
    raw = fetch(a, b - a)
    out, pos = [], 0
    for n in range(first, last + 1):
        want = min(chunk, size - n * chunk) + OVERHEAD
        out.append(unseal(key, raw[pos:pos + want], _label(n, n == count - 1)))
        pos += want
    data = b"".join(out)
    return data[start - first * chunk:end - first * chunk]


def pack(blobs) -> bytes:
    """Several sealed changes as one batch."""
    return b"".join(struct.pack(">I", len(b)) + b for b in blobs)


def unpack(batch: bytes):
    pos, out = 0, []
    while pos + 4 <= len(batch):
        (n,) = struct.unpack_from(">I", batch, pos)
        out.append(batch[pos + 4:pos + 4 + n])
        pos += 4 + n
    return out


def new_id() -> str:
    return secrets.token_hex(16)


class Vault:
    """An end-to-end account as a device uses it: the list of files, kept here in the clear and on
    the host sealed, and files going there and back sealed. `client` is a lite.HostClient signed in
    with the account's auth key (HostClient(address, auth=vault.auth_key(K).hex()))."""

    def __init__(self, client, key: bytes):
        self.client = client
        self.meta = meta_key(key)
        self.nodes = {}                  # id -> {id, parent, name, dir, size, mtime, obj, key}
        self.seq = 0                     # the number of the last change that is in `nodes`
        self.since_snapshot = 0
        self.snapshot_object = None
        self._kids = None                # folder id -> {name: node}, made when asked for

    # ------------------------------------------------------------ the list of files
    def sync(self):
        """Bring the list up to date with what the host has."""
        while True:
            got = self.client._json(f"/api/vault/log?after={self.seq}")
            snap = got.get("snapshot")
            if snap and snap["seq"] > self.seq:          # we are behind the last sealed whole list: start from it
                raw = self._fetch_all(snap["object"], snap["size"])
                state = json.loads(raw)
                self.nodes, self.seq, self.since_snapshot = state["nodes"], snap["seq"], 0
                self.snapshot_object = snap["object"]
                self._kids = None
                continue
            for seq, b64 in got["batches"]:
                for blob in unpack(base64.b64decode(b64)):
                    self._apply(json.loads(unseal(self.meta, blob, AAD_OP)))
                    self.since_snapshot += 1
                self.seq = seq
            if not got.get("more"):
                return self

    def _apply(self, op):
        self._kids = None
        kind = op.get("op")
        if kind == "put":
            self.nodes[op["id"]] = {k: op.get(k) for k in ("id", "parent", "name", "dir", "size", "mtime", "obj", "key", "thumb")}
        elif kind == "move":
            node = self.nodes.get(op["id"])
            if node is not None:
                node["parent"], node["name"] = op["parent"], op["name"]
        elif kind == "del":
            gone, todo = set(), [op["id"]]
            while todo:
                nid = todo.pop()
                if nid in self.nodes and nid not in gone:
                    gone.add(nid)
                    todo.extend(n["id"] for n in self.nodes.values() if n["parent"] == nid)
            for nid in gone:
                del self.nodes[nid]

    def _send(self, *ops):
        """Seal changes, hand them to the host, and take them (and what others did meanwhile) into the list."""
        batch = pack(seal(self.meta, json.dumps(op, separators=(",", ":")).encode("utf-8"), AAD_OP) for op in ops)
        self.client.request("POST", "/api/vault/log", batch, {"Content-Type": "application/octet-stream"})
        self.sync()
        if self.since_snapshot >= SNAPSHOT_EVERY:
            self.snapshot()

    def snapshot(self):
        """Seal the whole list as it is now; the host then drops the changes that led to it."""
        state = json.dumps({"nodes": self.nodes}, separators=(",", ":")).encode("utf-8")
        oid = new_id()
        self._store(oid, self.meta, state)
        self.client._json("/api/vault/snapshot", {"seq": self.seq, "object": oid, "size": len(state)})
        self.since_snapshot, self.snapshot_object = 0, oid

    # ------------------------------------------------------------ paths
    def _by_folder(self):
        if self._kids is None:
            kids = {}
            for n in self.nodes.values():
                kids.setdefault(n["parent"], {})[n["name"]] = n
            self._kids = kids
        return self._kids

    def _children(self, parent):
        return list(self._by_folder().get(parent, {}).values())

    def resolve(self, path):
        """The node at a path ('' is the top folder), or None."""
        nid = ""
        node = {"id": "", "dir": True, "name": "", "parent": None}
        kids = self._by_folder()
        for name in [p for p in path.strip("/").split("/") if p]:
            node = kids.get(nid, {}).get(name)
            if node is None:
                return None
            nid = node["id"]
        return node

    def list(self, path="/"):
        node = self.resolve(path)
        if node is None or not node["dir"]:
            raise VaultError(f"Not found: {path}")
        return sorted(self._children(node["id"]), key=lambda n: (not n["dir"], n["name"].lower()))

    def mkdir(self, path):
        parent, name = posixpath.split("/" + path.strip("/"))
        up = self.resolve(parent)
        if up is None or not up["dir"]:
            raise VaultError(f"Not found: {parent}")
        if self.resolve(path) is not None:
            raise VaultError("Something with that name already exists")
        self._send({"op": "put", "id": new_id(), "parent": up["id"], "name": name, "dir": True, "size": 0, "mtime": time.time()})

    def move(self, src, dst):
        node = self.resolve(src)
        parent, name = posixpath.split("/" + dst.strip("/"))
        up = self.resolve(parent)
        if node is None or not node["id"] or up is None or not up["dir"]:
            raise VaultError(f"Not found: {src if node is None else parent}")
        if self.resolve(dst) is not None:
            raise VaultError("Something with that name already exists")
        self._send({"op": "move", "id": node["id"], "parent": up["id"], "name": name})

    def delete(self, path):
        node = self.resolve(path)
        if node is None or not node["id"]:
            raise VaultError(f"Not found: {path}")
        objects, todo = [], [node]
        while todo:
            n = todo.pop()
            if n.get("obj"):
                objects.append(n["obj"])
            todo.extend(self._children(n["id"]))
        self._send({"op": "del", "id": node["id"]})
        if objects:
            self.client._json("/api/vault/delete", {"ids": objects})

    # ------------------------------------------------------------ files
    def _store(self, oid, key, data=None, read=None, size=None, log=CHUNK_LOG, stop=None, hear=None):
        """Seal a file (bytes, or read(n) with its size) and send it to the host as object `oid`.
        stop() returning true gives up (InterruptedError); hear(part_bytes=n) / hear(note=why) tell how it goes."""
        if data is not None:
            src, size = [data], len(data)

            def read(n):
                out, src[0] = src[0][:n], src[0][n:]
                return out
        total = sealed_size(size, log)
        offset, buf, plain = 0, b"", 0

        def check():
            if stop is not None and stop():
                raise InterruptedError("stopped")

        def post(part, done):
            nonlocal offset
            while True:
                check()
                got = json.loads(self.client.request("POST", f"/api/vault/object?id={oid}&size={total}&offset={offset}", part,
                                                     {"Content-Type": "application/octet-stream"}))
                if got.get("wait"):
                    if hear:
                        hear(note="Waiting for the host: " + str(got["wait"]))
                    for _ in range(15):
                        check()
                        time.sleep(1.0)
                    continue
                if not got.get("ok"):
                    raise VaultError("The host lost its place in this file; send it again.")
                offset = got["offset"]
                if hear:
                    hear(part_bytes=done, note="")
                return

        sent = 0
        for piece in seal_stream(key, read, size, log):
            buf += piece
            plain += max(0, len(piece) - OVERHEAD) if piece[:4] != MAGIC or len(piece) != 5 else 0
            if len(buf) >= PART:
                post(buf, plain - sent)
                sent, buf = plain, b""
        post(buf, plain - sent)

    def _fetch(self, oid, a, n):
        return self.client.request("GET", f"/api/vault/object?id={oid}", headers={"Range": f"bytes={a}-{a + n - 1}"})

    def _fetch_all(self, oid, size):
        return open_range(self.meta, lambda a, n: self._fetch(oid, a, n), size)

    def pending(self):
        """The ids of the objects the host has but hasn't stored on Discord yet."""
        return set(self.client._json("/api/vault/pending")["ids"])

    def put(self, path, data=None, read=None, size=None, mtime=None, stop=None, hear=None):
        """Store a file (bytes, or read(n) and its size) at a path, replacing what was there."""
        parent, name = posixpath.split("/" + path.strip("/"))
        up = self.resolve(parent)
        if up is None or not up["dir"]:
            raise VaultError(f"Not found: {parent}")
        old = self.resolve(path)
        if old is not None and old["dir"]:
            raise VaultError(f"{path} is a folder")
        size = len(data) if data is not None else size
        key, oid = os.urandom(32), new_id()
        self._store(oid, key, data, read, size, stop=stop, hear=hear)
        self._send({"op": "put", "id": old["id"] if old else new_id(), "parent": up["id"], "name": name, "dir": False,
                    "size": size, "mtime": mtime if mtime is not None else time.time(), "obj": oid,
                    "key": base64.b64encode(key).decode("ascii")})
        if old is not None and old.get("obj"):
            self.client._json("/api/vault/delete", {"ids": [old["obj"]]})

    def read(self, path, start=0, length=None):
        node = self.resolve(path)
        if node is None or node["dir"]:
            raise VaultError(f"Not found: {path}")
        return open_range(base64.b64decode(node["key"]), lambda a, n: self._fetch(node["obj"], a, n), node["size"], start, length)
