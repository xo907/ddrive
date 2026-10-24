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

The list is a set of entries, one per file or folder, each with an id of its own:

    {id, parent (a folder's id, "" for the top), name, dir, size, mtime, obj, key}   and, when they apply,
    star      when it was starred
    thumb     {obj, key} of its small picture (or -1: none can be made)
    versions  what the file was before it was last replaced, newest first: [{obj, key, size, mtime, at}]
    trash     {at, path}: it was deleted then, from there. It is kept (and can be brought back) until
              it is purged; meanwhile it and what is in it count as not there.

Password locks (made in the dashboard, which is the only place that opens them): a folder or file
locked with a password is kept sealed with a key of its own, with everything in it, so that the
account's words alone don't open it. The lock's record is an entry under "~locks" whose `lock` is
{v, salt, iter, wrap, rec}: the key, sealed (label "ddv1 lock key") with PBKDF2-SHA256(password, salt,
iter), and, when the account's words may open it too, sealed again (rec, label "ddv1 lock recovery") with
HKDF-SHA256(the words' key, info "ddrive vault v1 lock recovery"). A locked entry is {id, parent, in: the lock's id, box, objs}: `box` is the rest of the entry,
sealed with the lock's key (label "ddv1 locked"); `objs` the objects it holds on to (so that they
are not forgotten by a device that can't open it). The locked folder or file itself has the parent
"~locked" and its folder in `home`; what is in it keeps its parent. No device shows anything under
"~locks" or "~locked", so a device that doesn't know of locks never sees what is locked, and one
that does sees it only after its password was typed there.

A device must not make a folder or file in the clear where a locked one of that name is: that would
bring the name back for everybody to see (the Discord logger, or folder sync, saving into a folder
that was locked meanwhile). It knows one is there when the lock was made so that the words open it
too (it opens it then, and what it saves there is sealed with the lock, see Vault.into_locks), or by
the locked folder's `tag`: locked_tag(its folder, its name), which a page that has the lock open
keeps up to date (and `gone`: 1 while it is deleted), or, for a lock older than tags, when it has the
id a folder of that name gets (folder_id). Like those ids, a tag lets somebody with the account's
words check whether a locked folder has a name they guess; it shows nothing else.

and the changes are: "put" (an entry as it now is), "move" (id, parent, name), "set" (id, fields: the
ones given replace the entry's, null removes one), "del" (id: the entry and what is in it are gone for
good). An entry may carry fields a client doesn't know; it keeps them as they are.
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
import urllib.parse

from .crypto import AuthenticationError, CryptoEngine

INFO_AUTH = b"ddrive vault v1 auth"
INFO_META = b"ddrive vault v1 meta"
AAD_OP = b"ddv1 op"
AAD_CHUNK = b"ddv1 chunk"
AAD_LOCKED = b"ddv1 locked"
AAD_LOCK_REC = b"ddv1 lock recovery"
INFO_LOCK_REC = b"ddrive vault v1 lock recovery"
LOCKS = "~locks"                  # password locks (see "Password locks" above): their records
LOCKED = "~locked"                # what is locked
MAGIC = b"DDV1"
CHUNK_LOG = 20                    # a file is sealed in pieces of 1 MiB
OVERHEAD = 28                     # nonce + tag of a sealed thing
PART = 8 * 1024 * 1024            # sealed pieces go to the host this many bytes of the file at a time
SNAPSHOT_EVERY = 400              # changes after which the whole list is sealed anew
VERSIONS_KEPT = 10                # earlier versions of a file that are kept


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


def folder_id(parent: str, name: str) -> str:
    """The id a new folder gets: the same on every device for the same name in the same folder, so
    two devices that make it at once (two computers syncing into the account) make one folder, not
    two. (A folder that had that id and was deleted or moved meanwhile: a new id, see Vault.mkdir.)"""
    return hashlib.sha256(f"ddrive folder v1\n{parent}\n{name}".encode("utf-8")).hexdigest()[:32]


def locked_tag(parent: str, name: str) -> str:
    """What a locked folder or file says of its name, in the clear (see "Password locks")."""
    return hashlib.sha256(f"ddrive locked v1\n{parent}\n{name}".encode("utf-8")).hexdigest()[:32]


def view(nodes):
    """The list as it is shown: {folder id: {name: entry}}, and what doesn't fit in it.

    Two devices that make the same folder at once (each before it has heard of the other's) leave
    two entries of that name side by side. They are one folder: the one with the smallest id shows,
    with what is in all of them, and the others are `merged` into it. An entry that says it went
    `into` another folder (what Vault.tidy leaves behind) has what is in it shown there. Of two
    files of one name (or a file and a folder) the folder, else the newest file, shows; the others
    are `hidden` until tidy gives them a name of their own. Every device shows the same list.

    merged: [(entry, the folder it is one with)], hidden: [(entry, folder id, folder path, the entry shown)],
    strays: [(entry, the folder it shows in)] for what shows in a folder it isn't in."""
    raw, went = {}, {}
    for n in nodes.values():
        raw.setdefault(n.get("parent"), []).append(n)
        if n.get("into") and n.get("into") in nodes and n.get("into") != n["id"]:
            went.setdefault(n["into"], []).append(n["id"])
    kids, merged, hidden, strays, done = {}, [], [], [], set()
    todo = [("", "/", [""])]
    while todo:
        fid, path, members = todo.pop()
        if fid in done:
            continue
        done.add(fid)
        group, seen = [], set()
        while members:
            m = members.pop()
            if m not in seen:
                seen.add(m)
                group.append(m)
                members.extend(went.get(m, ()))
        by_name = {}
        for m in group:
            for c in raw.get(m, ()):
                # (what is locked is sealed, with no name: only a page with its password shows it)
                if not c.get("trash") and not c.get("into") and c["id"] not in done and not c.get("box") and c.get("name"):
                    by_name.setdefault(c.get("name") or "", []).append(c)
        out = {}
        for name, cs in by_name.items():
            dirs = sorted((c for c in cs if c.get("dir")), key=lambda c: c["id"])
            files = sorted((c for c in cs if not c.get("dir")), key=lambda c: (-(c.get("mtime") or 0), c["id"]))
            shown = dirs[0] if dirs else files[0]
            out[name] = shown
            merged.extend((d, shown) for d in dirs[1:])
            hidden.extend((f, fid, path, shown) for f in files if f is not shown)
            if shown.get("parent") != fid:
                strays.append((shown, fid))
            if dirs:
                todo.append((shown["id"], posixpath.join(path, name), [d["id"] for d in dirs]))
        kids[fid] = out
    return kids, merged, hidden, strays


# ------------------------------------------------------------ moving one account into another (transfer.py)
# The account that moves out (S) and the one that receives (T) agree on a key with ECDH (P-256):
#
#     shared  = ECDH(own private key, other's public key)             32 bytes (the x coordinate)
#     K       = HKDF-SHA256(shared, salt=SHA256(tpub + spub), info="ddrive move v1 key")
#
# S seals {"v": 1, "nodes": its whole list} with K and the label "ddv1 move". T opens it and adds it
# to its own list in a new folder, every entry under a new id: SHA256("ddrive move v1 id" + move + old
# id), the folder's SHA256("ddrive move v1 folder" + move), so taking it in twice changes nothing.
AAD_MOVE = b"ddv1 move"
INFO_MOVE = b"ddrive move v1 key"
MOVE_BATCH = 2 * 1024 * 1024      # sealed changes sent together while taking a list in


def hkdf_salted(key: bytes, salt: bytes, info: bytes, n: int = 32) -> bytes:
    """HKDF-SHA256 with a salt, for up to 32 bytes."""
    prk = hmac.new(salt, key, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def move_key(shared: bytes, tpub: bytes, spub: bytes) -> bytes:
    return hkdf_salted(shared, hashlib.sha256(tpub + spub).digest(), INFO_MOVE)


def moved_id(move: str, old: str) -> str:
    return hashlib.sha256(b"ddrive move v1 id" + move.encode() + old.encode()).hexdigest()[:32]


def moved_folder(move: str) -> str:
    return hashlib.sha256(b"ddrive move v1 folder" + move.encode()).hexdigest()[:32]


def moved_ops(move: str, nodes: dict, folder_name: str, at: float, by: str = ""):
    """The changes that put a moved account's list into a new folder of this one."""
    top = moved_folder(move)
    ops = [{"op": "put", "id": top, "parent": "", "name": folder_name, "dir": True, "size": 0, "mtime": at, "at": at, "by": by}]
    for old, n in nodes.items():
        kept = n.get("parent") in (LOCKS, LOCKED)        # (what is locked moves sealed as it is: its lock and folder get their new ids)
        e = dict(n, id=moved_id(move, old), parent=n["parent"] if kept else top if not n.get("parent") else moved_id(move, n["parent"]))
        if n.get("in"):
            e["in"] = moved_id(move, n["in"])
        if n.get("parent") == LOCKED:
            e["home"] = moved_id(move, n["home"]) if n.get("home") else top
        if e.get("trash") and isinstance(e["trash"], dict) and e["trash"].get("path"):
            e["trash"] = dict(e["trash"], path="/" + folder_name + "/" + e["trash"]["path"].lstrip("/"))
        ops.append(dict(e, op="put"))
    return ops


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
        self.name = ""                   # this device, for the account's list of what happened
        self._snaps = {}                 # a snapshot's object -> the objects that snapshot refers to
        self._view = None                # (merged, hidden, strays) as view() found them, with _kids
        self._tidying = False
        self._rec_key = hkdf(key, INFO_LOCK_REC)
        self._lock_keys = {}             # lock id -> its key (None: the words don't open it)
        self._boxes = {}                 # a locked entry's sealed content -> what it says
        self._open = None                # (id -> locked entry as opened, folder id -> {name: entry}), with _kids
        self.into_locks = False          # paths reach into what is locked (when the words open it)

    # ------------------------------------------------------------ the list of files
    def sync(self):
        """Bring the list up to date with what the host has (and tidy it, see tidy)."""
        self._pull()
        self.tidy()
        return self

    def _pull(self):
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
                return

    def _apply(self, op):
        self._kids = None
        kind = op.get("op")
        if kind == "put":
            self.nodes[op["id"]] = {k: v for k, v in op.items() if k != "op"}
        elif kind == "set":
            node = self.nodes.get(op["id"])
            if node is not None:
                for k, v in (op.get("fields") or {}).items():
                    if v is None:
                        node.pop(k, None)
                    elif k not in ("id",):
                        node[k] = v
        elif kind == "move":
            node = self.nodes.get(op["id"])
            if node is not None:
                node["parent"], node["name"] = op["parent"], op["name"]
        elif kind == "del":
            node = self.nodes.get(op["id"])
            if node is not None and not node.get("dir") and not node.get("box"):
                del self.nodes[op["id"]]         # a file: nothing is in it
                return
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
        self._pull()
        self.tidy()
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
        if self._kids is None:                   # (deleted entries are kept, but not there)
            self._kids, *self._view = view(self.nodes)
            self._open = None
        return self._kids

    # ------------------------------------------------------------ password locks
    def _opened(self):
        """What is locked with a lock that this account's words open: (id -> the entry as it is, with
        `lock` and, for the locked folder or file itself, `lock_root` and its folder as `parent`;
        folder id -> {name: entry} of those that are there)."""
        self._by_folder()
        if self._open is None:
            for n in self.nodes.values():
                rec = n.get("lock") if n.get("parent") == LOCKS else None
                if isinstance(rec, dict) and n["id"] not in self._lock_keys:
                    try:
                        self._lock_keys[n["id"]] = unseal(self._rec_key, base64.b64decode(rec["rec"]), AAD_LOCK_REC) \
                            if rec.get("rec") else None
                    except (VaultError, ValueError, TypeError):
                        self._lock_keys[n["id"]] = None
            entries, kids = {}, {}
            for n in self.nodes.values():
                key = self._lock_keys.get(n.get("in")) if n.get("box") else None
                if not key:
                    continue
                if n["box"] not in self._boxes:
                    try:
                        self._boxes[n["box"]] = json.loads(unseal(key, base64.b64decode(n["box"]), AAD_LOCKED))
                    except (VaultError, ValueError, TypeError):
                        continue
                root = n.get("parent") == LOCKED
                e = dict(self._boxes[n["box"]], id=n["id"], parent=(n.get("home") or "") if root else n["parent"], lock=n["in"])
                if root:
                    e["lock_root"] = True
                entries[n["id"]] = e
            for e in entries.values():
                if not e.get("trash") and e.get("name"):
                    kids.setdefault(e["parent"], {})[e["name"]] = e
            tags = {((n.get("home") or ""), n["tag"]) for n in self.nodes.values()
                    if n.get("parent") == LOCKED and n.get("tag") and not n.get("gone")}
            self._open = (entries, kids, tags)
        return self._open

    def _locked_here(self, parent, name):
        """True when a locked folder or file of this name is in folder `parent` (as far as this device
        can tell: see "Password locks" above)."""
        entries, kids, tags = self._opened()
        if name in kids.get(parent, {}) or (parent, locked_tag(parent, name)) in tags:
            return True
        n = self.nodes.get(folder_id(parent, name))
        if n is None or n.get("parent") != LOCKED or (n.get("home") or "") != parent or n.get("tag"):
            return False                         # (one with a tag says its name above: renamed since, or deleted)
        e = entries.get(n["id"])
        return e is None or (not e.get("trash") and e.get("name") == name)

    def _boxed(self, e, lock, root=False):
        """An entry as the change that keeps it sealed with lock `lock` (as the locked one itself, when `root`)."""
        key = self._lock_keys.get(lock)
        if not key:
            raise VaultError("That is password-locked.")
        inner = {k: v for k, v in e.items() if k not in ("id", "parent", "lock", "lock_root", "op", "home", "in", "box", "objs")}
        box = seal(key, json.dumps(inner, separators=(",", ":")).encode("utf-8"), AAD_LOCKED)
        op = {"op": "put", "id": e["id"], "parent": LOCKED if root else e["parent"], "in": lock,
              "objs": self._objects(inner), "box": base64.b64encode(box).decode("ascii")}
        if root:
            op["home"] = e["parent"] or ""
            op["tag"] = locked_tag(op["home"], inner.get("name") or "")
            if inner.get("trash"):
                op["gone"] = 1
        return op

    def entry(self, nid):
        """An entry by id (what is locked as it is, with into_locks)."""
        if self.into_locks and nid in self._opened()[0]:
            return self._opened()[0][nid]
        return self.nodes.get(nid)

    def children(self, parent):
        """What is in a folder (also what is locked in it, with into_locks)."""
        out = dict(self._by_folder().get(parent, {}))
        if self.into_locks:
            out.update(self._opened()[1].get(parent, {}))
        return list(out.values())

    def tidy(self):
        """Make the list say what view() shows, also for devices that don't know of merging: what is
        in a folder that was made twice is moved into the one that shows, and the other is left
        behind deleted, saying where it went (so that what a device that hadn't heard yet puts into
        it still shows). A file that lost its name to another is kept under a name of its own
        ("x (2).txt"), or deleted (it can be brought back) when it is the same file: same size and
        date. Every device that sees it does the same, so it doesn't matter which one does."""
        if self._tidying:
            return
        self._tidying = True
        try:
            for _ in range(10):                  # each round may bring folders of one name together a level down
                self._by_folder()
                merged, hidden, strays = self._view
                if not merged and not hidden and not strays:
                    return
                now, ops, taken = time.time(), {}, {}
                for c, fid in strays:
                    ops[c["id"]] = {"op": "move", "id": c["id"], "parent": fid, "name": c.get("name") or ""}
                for d, into in merged:
                    ops[d["id"]] = {"op": "set", "id": d["id"], "fields": {
                        "into": into["id"], "trash": {"at": now, "path": self._path_of(into["id"])}}}
                for f, fid, path, shown in hidden:
                    name = f.get("name") or ""
                    if not shown.get("dir") and shown.get("size") == f.get("size") and shown.get("mtime") == f.get("mtime"):
                        ops[f["id"]] = {"op": "set", "id": f["id"], "fields": {"trash": {"at": now, "path": posixpath.join(path, name)}}}
                        continue
                    names = taken.setdefault(fid, set(self._kids.get(fid, {})))
                    stem, ext = posixpath.splitext(name)
                    if not stem:
                        stem, ext = name, ""
                    i = 2
                    while f"{stem} ({i}){ext}" in names:
                        i += 1
                    names.add(f"{stem} ({i}){ext}")
                    ops[f["id"]] = {"op": "move", "id": f["id"], "parent": fid, "name": f"{stem} ({i}){ext}"}
                self._send(*ops.values())
        finally:
            self._tidying = False

    def _path_of(self, nid):
        names = []
        for _ in range(256):
            n = self.nodes.get(nid)
            if n is None or not nid:
                break
            names.append(n.get("name") or "")
            nid = n.get("parent")
        return "/" + "/".join(reversed(names))

    def _children(self, parent):
        return list(self._by_folder().get(parent, {}).values())

    def resolve(self, path):
        """The node at a path ('' is the top folder), or None. With into_locks, what is locked (with a lock
        the words open) is found too, before anything of the same name in the clear."""
        nid = ""
        node = {"id": "", "dir": True, "name": "", "parent": None}
        kids = self._by_folder()
        locked = self._opened()[1] if self.into_locks else {}
        for name in [p for p in path.strip("/").split("/") if p]:
            node = locked.get(nid, {}).get(name)
            if node is None and self.into_locks and self._locked_here(nid, name):
                return None                      # locked, and the words don't open it: not the one in the clear either
            node = node or kids.get(nid, {}).get(name)
            if node is None:
                return None
            nid = node["id"]
        return node

    def files(self):
        """The files that are there (not the deleted ones, nor what is in a deleted folder)."""
        out, todo = [], [""]
        while todo:
            for n in self._children(todo.pop()):
                if n["dir"]:
                    todo.append(n["id"])
                else:
                    out.append(n)
        return out

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
        if self._locked_here(up["id"], name):
            raise VaultError(f"{path} is password-locked, and this device can't open it: nothing is put there in the clear")
        nid = folder_id(up["id"], name)
        if nid in self.nodes:                    # (that folder was there once, and was deleted or moved)
            nid = new_id()
        op = {"op": "put", "id": nid, "parent": up["id"], "name": name, "dir": True, "size": 0, "mtime": time.time(),
              "at": time.time(), "by": self.name}
        self._send(self._boxed(op, up["lock"]) if up.get("lock") else op)

    def move(self, src, dst):
        node = self.resolve(src)
        parent, name = posixpath.split("/" + dst.strip("/"))
        up = self.resolve(parent)
        if node is None or not node["id"] or up is None or not up["dir"]:
            raise VaultError(f"Not found: {src if node is None else parent}")
        if self.resolve(dst) is not None:
            raise VaultError("Something with that name already exists")
        if node.get("lock") or up.get("lock") or self._locked_here(up["id"], name):
            raise VaultError(f"{src if node.get('lock') else dst} is password-locked")
        self._send({"op": "move", "id": node["id"], "parent": up["id"], "name": name})

    def delete(self, path):
        """Delete a file or a folder with what is in it. It is kept as deleted (and can be brought back
        from the account's Deleted page) until it is purged."""
        node = self.resolve(path)
        if node is None or not node["id"]:
            raise VaultError(f"Not found: {path}")
        trash = {"trash": {"at": time.time(), "path": "/" + path.strip("/")}}
        self._send(self._boxed(dict(node, **trash), node["lock"], node.get("lock_root")) if node.get("lock")
                   else {"op": "set", "id": node["id"], "fields": trash})

    @staticmethod
    def _objects(node):
        """The objects an entry holds on to: its content, its earlier versions, its small picture."""
        out = [node.get("obj")] + [v.get("obj") for v in node.get("versions") or []]
        if isinstance(node.get("thumb"), dict):
            out.append(node["thumb"].get("obj"))
        out += node.get("objs") or []           # (a locked entry's, which this device can't open)
        return [o for o in out if o]

    def _drop(self, objects):
        """Have the host forget objects, except those something in the list still uses, and those a
        snapshot of the account (the list as it was on a day, a sealed file) still refers to."""
        used = {o for n in self.nodes.values() for o in self._objects(n)}
        try:
            for n in self.nodes.values():
                if n.get("snap") and n.get("obj"):
                    if n["obj"] not in self._snaps:
                        data = open_range(base64.b64decode(n["key"]), lambda a, k, o=n["obj"]: self._fetch(o, a, k), n["size"], 0, None)
                        self._snaps[n["obj"]] = {e.get("obj") for e in json.loads(data).get("entries") or [] if e.get("obj")}
                    used |= self._snaps[n["obj"]]
        except Exception:                        # a snapshot can't be read right now: nothing is forgotten
            return
        gone = [o for o in dict.fromkeys(objects) if o not in used]
        if gone:
            self.client._json("/api/vault/delete", {"ids": gone})

    def purge(self, ids):
        """Remove entries (deleted ones, usually) for good, with what is in them and their objects."""
        objects, ops = [], []
        for nid in ids:
            todo = [nid]
            while todo:
                cur = todo.pop()
                n = self.nodes.get(cur)
                if n is None:
                    continue
                objects.extend(self._objects(n))
                todo.extend(x["id"] for x in self.nodes.values() if x["parent"] == cur)
            ops.append({"op": "del", "id": nid})
        if ops:
            self._send(*ops)
            self._drop(objects)

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
        if buf or offset < total:                # (a file that ended with a whole part is there already: nothing more is sent)
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
        if old is None and self._locked_here(up["id"], name):
            raise VaultError(f"{path} is password-locked, and this device can't open it: nothing is put there in the clear")
        size = len(data) if data is not None else size
        key, oid = os.urandom(32), new_id()
        self._store(oid, key, data, read, size, stop=stop, hear=hear)
        self.put_object(path, oid, key, size, mtime)

    def put_object(self, path, oid, key, size, mtime=None):
        """Make the file at `path` the object `oid` that is on the host already, sealed with `key`
        (put, or a recording sealed while it was made: litelive.py), replacing what was there."""
        parent, name = posixpath.split("/" + path.strip("/"))
        up = self.resolve(parent)
        if up is None or not up["dir"]:
            raise VaultError(f"Not found: {parent}")
        old = self.resolve(path)
        if old is not None and old["dir"]:
            raise VaultError(f"{path} is a folder")
        if old is None and self._locked_here(up["id"], name):
            raise VaultError(f"{path} is password-locked, and this device can't open it: nothing is put there in the clear")
        entry = {"id": new_id(), "parent": up["id"], "name": name, "dir": False, "at": time.time(), "by": self.name}
        dropped = []
        if old is not None:                      # replaced: what it was is kept as an earlier version
            entry = {k: v for k, v in old.items() if k != "thumb"}
            if isinstance(old.get("thumb"), dict):
                dropped.append(old["thumb"].get("obj"))
            versions = ([{"obj": old["obj"], "key": old["key"], "size": old["size"], "mtime": old["mtime"], "at": time.time()}]
                        if old.get("obj") else []) + list(old.get("versions") or [])
            dropped += [v.get("obj") for v in versions[VERSIONS_KEPT:]]
            entry["versions"] = versions[:VERSIONS_KEPT]
        entry.update(size=size, mtime=mtime if mtime is not None else time.time(), obj=oid,
                     key=base64.b64encode(key).decode("ascii"))
        lock = (old or up).get("lock")
        self._send(self._boxed(entry, lock, bool(old and old.get("lock_root"))) if lock else dict(entry, op="put"))
        self._drop(dropped)

    def read(self, path, start=0, length=None):
        node = self.resolve(path)
        if node is None or node["dir"]:
            raise VaultError(f"Not found: {path}")
        return open_range(base64.b64decode(node["key"]), lambda a, n: self._fetch(node["obj"], a, n), node["size"], start, length)

    # ------------------------------------------------------------ moving into another account
    def move_open(self):
        """This account receives: ask for a move. Returns {"id" (to type on the other account), "code"
        (to compare with the other account's page)}; keep it for move_take()."""
        from . import p256
        priv, pub = p256.keypair()
        got = self.client._json("/api/vault/move/open", {"pub": base64.b64encode(pub).decode("ascii")})
        self._moving = (got["id"], priv, pub)
        return got

    def move_send(self, move_id, code=None):
        """This account moves out, into the account that asked (its move id). `code`: what the other
        account's page shows; if the host handed over another key than that account's, nothing is sent.
        Returns the check code both pages show next. After this the account can't change, and the host
        deletes it once the other account has taken everything in."""
        from . import p256
        from .transfer import check_code, key_code
        peer = self.client._json(f"/api/vault/move/peer?id={move_id}")
        tpub = base64.b64decode(peer["tpub"])
        if code is not None and key_code(tpub) != code:
            raise VaultError("The codes don't match: the server handed over a key that isn't the other account's. Nothing was sent.")
        self.sync()
        priv, spub = p256.keypair()
        key = move_key(p256.shared(priv, tpub), tpub, spub)
        payload = json.dumps({"v": 1, "nodes": self.nodes}, separators=(",", ":")).encode("utf-8")
        q = urllib.parse.urlencode({"id": move_id, "pub": base64.b64encode(spub).decode("ascii")})
        got = json.loads(self.client.request("POST", "/api/vault/move/send?" + q, seal(key, payload, AAD_MOVE),
                                             {"Content-Type": "application/octet-stream"}))
        if got.get("check") != check_code(tpub, spub):
            raise VaultError("The server's check code is not the one these keys make.")
        return got["check"]

    def move_status(self, move_id=None):
        return self.client._json(f"/api/vault/move?id={move_id or self._moving[0]}")

    def move_take(self, check=None, folder_name=None, wait=0.0):
        """Take in what the other account sent, once the host has copied its files: its whole list goes
        into a new folder here. `check`: the code the other page shows (refused when it differs).
        Returns the new folder's name."""
        from . import p256
        from .transfer import check_code
        mid, priv, tpub = self._moving
        end = time.time() + wait
        while True:
            st = self.move_status(mid)
            if st["state"] in ("ready", "finishing", "done") or time.time() >= end:
                break
            time.sleep(0.2)
        if st["state"] not in ("ready", "finishing"):
            raise VaultError(f"The move is {st['state']}" + (f" ({st['error']})" if st.get("error") else "") + ".")
        spub = base64.b64decode(st["spub"])
        if check is not None and check_code(tpub, spub) != check:
            raise VaultError("The codes don't match: what came isn't from the account you moved. Nothing was taken in.")
        sealed = self.client.request("GET", f"/api/vault/move/list?id={mid}")
        data = json.loads(unseal(move_key(p256.shared(priv, spub), tpub, spub), sealed, AAD_MOVE))
        self.sync()
        top = moved_folder(mid)
        name = self.nodes[top]["name"] if top in self.nodes else self._free_name("", folder_name or f"From account {st['from']}")
        ops = moved_ops(mid, data["nodes"], name, time.time(), self.name)
        batch, size = [], 0
        for op in ops:
            n = len(json.dumps(op))
            if batch and size + n > MOVE_BATCH:
                self._send(*batch)
                batch, size = [], 0
            batch.append(op)
            size += n
        if batch:
            self._send(*batch)
        self.client._json("/api/vault/move/finish", {"id": mid})
        return name

    def _free_name(self, parent, name):
        taken = {n["name"] for n in self._children(parent)}
        if name not in taken:
            return name
        i = 2
        while f"{name} ({i})" in taken:
            i += 1
        return f"{name} ({i})"
