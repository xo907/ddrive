"""SQLite metadata index: directory tree, file -> Discord chunk mapping, sync journal state and file versions.

Every node has a stable `uid` that is the same on all devices. Local changes are
recorded as operations in the `outbox` table (in the same transaction as the
change) and published to the channel by the journal (journal.py). Operations
read back from the channel are applied with `apply_ops`, in Discord's message
order, which is identical on every device; that is what keeps devices in sync.

`jstate` tracks how far a node has propagated:
    0  local only (a new file whose first upload has not finished)
    1  its creating operation is queued / posted, but not yet read back
    2  known: an operation for it has been applied from the journal
"""

import gzip
import hashlib
import json
import logging
import os
import posixpath
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("ddrive.index")

ROOT_ID = 1
ROOT_UID = "root"
RECOVERED_UID = "recovered"
RECOVERED_NAME = "Recovered files"

J_LOCAL, J_QUEUED, J_KNOWN = 0, 1, 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    parent  INTEGER,
    name    TEXT NOT NULL COLLATE NOCASE,
    is_dir  INTEGER NOT NULL,
    size    INTEGER NOT NULL DEFAULT 0,
    mtime   REAL NOT NULL,
    ctime   REAL NOT NULL,
    atime   REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    state   TEXT NOT NULL DEFAULT 'synced',      -- synced | pending | error
    is_pinned INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS nodes_parent_name ON nodes(parent, name);
-- what still has to be uploaded (asked for every few seconds; a full scan of a big drive otherwise)
CREATE INDEX IF NOT EXISTS nodes_unsynced ON nodes(id) WHERE is_dir=0 AND state!='synced';
CREATE TABLE IF NOT EXISTS chunks(
    node_id       INTEGER NOT NULL,
    idx           INTEGER NOT NULL,
    message_id    TEXT NOT NULL,
    attachment_id TEXT,
    url           TEXT,
    size          INTEGER NOT NULL,
    sha256        TEXT,
    PRIMARY KEY(node_id, idx)
);
CREATE INDEX IF NOT EXISTS chunks_message ON chunks(message_id);
CREATE TABLE IF NOT EXISTS trash(message_id TEXT PRIMARY KEY, added REAL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alias(uid TEXT PRIMARY KEY, target TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tombstones(uid TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT NOT NULL,
    parent_uid TEXT,
    name       TEXT NOT NULL,
    path       TEXT,
    size       INTEGER NOT NULL,
    mtime      REAL,
    superseded REAL NOT NULL,
    reason     TEXT NOT NULL                     -- replaced | deleted | conflict
);
CREATE INDEX IF NOT EXISTS versions_uid ON versions(uid);
CREATE TABLE IF NOT EXISTS version_chunks(
    version_id INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT,
    PRIMARY KEY(version_id, idx)
);
CREATE INDEX IF NOT EXISTS version_chunks_message ON version_chunks(message_id);
CREATE INDEX IF NOT EXISTS chunks_sha ON chunks(sha256);
CREATE INDEX IF NOT EXISTS version_chunks_sha ON version_chunks(sha256);
-- Spare pieces: a group of data pieces (members, in order) and its Reed-Solomon parity pieces.
CREATE TABLE IF NOT EXISTS parity_groups(gid TEXT PRIMARY KEY, created REAL);
CREATE TABLE IF NOT EXISTS parity_members(
    gid        TEXT NOT NULL,
    pos        INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT,
    PRIMARY KEY(gid, pos)
);
CREATE INDEX IF NOT EXISTS parity_members_message ON parity_members(message_id);
CREATE TABLE IF NOT EXISTS parity_shards(
    gid        TEXT NOT NULL,
    j          INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT,
    PRIMARY KEY(gid, j)
);
CREATE INDEX IF NOT EXISTS parity_shards_message ON parity_shards(message_id);
-- Snapshots: the whole tree as it was at one moment; their pieces are kept until they expire.
CREATE TABLE IF NOT EXISTS snapshots(
    id         TEXT PRIMARY KEY,
    at         REAL NOT NULL,
    label      TEXT,
    device     TEXT,
    keep_until REAL,
    parts      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS snapshot_parts(snap_id TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(snap_id, n));
CREATE TABLE IF NOT EXISTS snapshot_entries(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    snap_id TEXT NOT NULL,
    path    TEXT NOT NULL,
    is_dir  INTEGER NOT NULL,
    size    INTEGER NOT NULL DEFAULT 0,
    mtime   REAL
);
CREATE INDEX IF NOT EXISTS snapshot_entries_snap ON snapshot_entries(snap_id);
CREATE TABLE IF NOT EXISTS snapshot_chunks(
    entry_id   INTEGER NOT NULL,
    snap_id    TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT
);
CREATE INDEX IF NOT EXISTS snapshot_chunks_entry ON snapshot_chunks(entry_id);
CREATE INDEX IF NOT EXISTS snapshot_chunks_snap ON snapshot_chunks(snap_id);
CREATE INDEX IF NOT EXISTS snapshot_chunks_message ON snapshot_chunks(message_id);
-- Share links (synced between devices) and how often each device served them.
CREATE TABLE IF NOT EXISTS shares(id TEXT PRIMARY KEY, rec TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS share_views(id TEXT NOT NULL, device TEXT NOT NULL, n INTEGER NOT NULL,
                                       PRIMARY KEY(id, device));
-- Password-locked folders and files (synced between devices): uid of the item -> {"pw": hash, "c": created}.
CREATE TABLE IF NOT EXISTS locks(uid TEXT PRIMARY KEY, rec TEXT NOT NULL);
-- Starred items (synced between devices).
CREATE TABLE IF NOT EXISTS stars(uid TEXT PRIMARY KEY, at REAL NOT NULL);
-- What happened on the drive, as this device saw it (kept here only; the newest few thousand).
CREATE TABLE IF NOT EXISTS activity(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    device  TEXT NOT NULL DEFAULT '',      -- '' = this device
    kind    TEXT NOT NULL,                 -- add | edit | mkdir | move | delete | restore
    path    TEXT NOT NULL,
    src     TEXT,                          -- move: where it was
    size    INTEGER NOT NULL DEFAULT 0,
    is_dir  INTEGER NOT NULL DEFAULT 0,
    uid     TEXT
);
"""

# Tables that point at stored pieces; a "fix" (a piece re-uploaded after Discord lost it) updates them all.
_PIECE_TABLES = ("chunks", "version_chunks", "snapshot_chunks", "parity_members", "parity_shards")
TRASH_GRACE = 600.0   # seconds a released piece waits before it is deleted from Discord

_UPDATABLE = {"size", "mtime", "ctime", "atime", "version", "state", "is_pinned"}


def new_uid() -> str:
    return secrets.token_hex(8)


def _legacy_uid(path: str) -> str:
    # Deterministic, so two devices upgrading the same tree agree on every uid.
    return hashlib.sha256(("legacy:" + path.lower()).encode("utf-8")).hexdigest()[:16]


def conflict_name(name: str, uid: str) -> str:
    stem, ext = os.path.splitext(name)
    if not stem:  # dotfiles such as ".bashrc"
        stem, ext = name, ""
    return f"{stem} (conflict {uid[:6]}){ext}"


class Index:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.keep_versions = True
        self.device = ""                 # this device's id (set by the drive): its own changes read back aren't logged twice
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self._migrate()
        # Counts metadata changes made during this session (used to decide when to checkpoint).
        self.change_counter = 1
        self.last_change = time.time()
        self._stats_cache = (0.0, None)

    def _migrate(self):
        with self.lock:
            db = self.db
            db.executescript(SCHEMA)
            cols = {r[1] for r in db.execute("PRAGMA table_info(nodes)")}
            if "is_pinned" not in cols:
                db.execute("ALTER TABLE nodes ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")
            if "uid" not in cols:
                db.execute("ALTER TABLE nodes ADD COLUMN uid TEXT")
            if "jstate" not in cols:
                # Nodes from before the journal existed count as known everywhere.
                db.execute("ALTER TABLE nodes ADD COLUMN jstate INTEGER NOT NULL DEFAULT 2")
            now = time.time()
            db.execute(
                "INSERT OR IGNORE INTO nodes(id, parent, name, is_dir, mtime, ctime, atime, uid, jstate) "
                "VALUES(?, NULL, '', 1, ?, ?, ?, ?, 2)",
                (ROOT_ID, now, now, now, ROOT_UID),
            )
            missing = db.execute("SELECT COUNT(*) FROM nodes WHERE uid IS NULL").fetchone()[0]
            if missing:
                tree = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, parent, name FROM nodes")}

                def path(nid):
                    parts = []
                    while nid is not None and nid != ROOT_ID and nid in tree:
                        parent, name = tree[nid]
                        parts.append(name)
                        nid = parent
                    return "/" + "/".join(reversed(parts))

                db.execute("BEGIN")
                for (nid,) in db.execute("SELECT id FROM nodes WHERE uid IS NULL").fetchall():
                    uid = ROOT_UID if nid == ROOT_ID else _legacy_uid(path(nid))
                    db.execute("UPDATE nodes SET uid=? WHERE id=?", (uid, nid))
                db.execute("COMMIT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS nodes_uid ON nodes(uid)")

    def close(self):
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------- plumbing
    def _changed(self):
        self.change_counter += 1
        self.last_change = time.time()
        self._stats_cache = (0.0, None)

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")
                self._changed()

    def _one(self, sql, args=()):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _queue(self, db, op):
        db.execute("INSERT INTO outbox(op) VALUES(?)", (json.dumps(op, separators=(",", ":")),))

    @staticmethod
    def _uid(db, nid):
        row = db.execute("SELECT uid FROM nodes WHERE id=?", (nid,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _chunk_list(db, nid):
        return [(r[0], r[1], r[2]) for r in
                db.execute("SELECT message_id, size, sha256 FROM chunks WHERE node_id=? ORDER BY idx", (nid,))]

    @staticmethod
    def _path(db, nid):
        parts = []
        while nid is not None and nid != ROOT_ID:
            row = db.execute("SELECT parent, name FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is None:
                break
            parts.append(row[1])
            nid = row[0]
        return "/" + "/".join(reversed(parts))

    # ---------------------------------------------------------------- nodes
    def get(self, nid):
        return self._one("SELECT * FROM nodes WHERE id=?", (nid,))

    def get_by_uid(self, uid):
        with self.lock:
            nid = self._nid(self.db, uid)
            return self.get(nid) if nid is not None else None

    def lookup(self, parent_id, name):
        return self._one("SELECT * FROM nodes WHERE parent=? AND name=?", (parent_id, name))

    def resolve(self, path: str):
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        with self.lock:
            node = self.get(ROOT_ID)
            for p in parts:
                if node is None or not node["is_dir"]:
                    return None
                node = self.lookup(node["id"], p)
            return node

    def children(self, nid, limit=-1):
        return self._all("SELECT * FROM nodes WHERE parent=? ORDER BY name LIMIT ?", (nid, limit))

    def create_node(self, parent_id, name, is_dir, state="synced", uid=None):
        """Create a node locally. Directories are published right away; files are
        published by `replace_chunks` once their content is uploaded."""
        now = time.time()
        uid = uid or new_uid()
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO nodes(parent, name, is_dir, size, mtime, ctime, atime, state, uid, jstate) "
                "VALUES(?,?,?,0,?,?,?,?,?,?)",
                (parent_id, name, 1 if is_dir else 0, now, now, now, state, uid, J_QUEUED if is_dir else J_LOCAL),
            )
            nid = cur.lastrowid
            if is_dir:
                self._queue(db, {"t": "mkdir", "u": uid, "p": self._uid(db, parent_id), "n": name, "m": now})
                self._act(db, "mkdir", self._path(db, nid), 0, True, uid)
        return self.get(nid)

    def makedirs(self, path):
        """Return the directory node at `path`, creating missing directories."""
        node = self.get(ROOT_ID)
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            child = self.lookup(node["id"], part)
            if child is not None and not child["is_dir"]:
                child = self.lookup(node["id"], conflict_name(part, "dir"))
                part = conflict_name(part, "dir")
            node = child or self.create_node(node["id"], part, True)
        return node

    def update(self, nid, **fields):
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"cannot update {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as db:
            db.execute(f"UPDATE nodes SET {cols} WHERE id=?", (*fields.values(), nid))

    def touch(self, nid, atime, mtime):
        """utimens: also publishes the new mtime of an already uploaded file."""
        with self.tx() as db:
            db.execute("UPDATE nodes SET atime=?, mtime=? WHERE id=?", (atime, mtime, nid))
            row = db.execute("SELECT uid, is_dir, state, jstate FROM nodes WHERE id=?", (nid,)).fetchone()
            if row and not row["is_dir"] and row["state"] == "synced" and row["jstate"] >= J_QUEUED:
                self._queue(db, {"t": "mt", "u": row["uid"], "m": mtime})

    def mark_modified(self, nid, size, mtime):
        with self.tx() as db:
            db.execute(
                "UPDATE nodes SET size=?, mtime=?, version=version+1, state='pending' WHERE id=?",
                (size, mtime, nid),
            )

    def rename(self, nid, new_parent, new_name):
        with self.tx() as db:
            row = db.execute("SELECT uid, jstate, size, is_dir FROM nodes WHERE id=?", (nid,)).fetchone()
            src = self._path(db, nid)
            db.execute("UPDATE nodes SET parent=?, name=?, ctime=? WHERE id=?",
                       (new_parent, new_name, time.time(), nid))
            if row and row["jstate"] >= J_QUEUED:
                self._act(db, "move", self._path(db, nid), row["size"], row["is_dir"], row["uid"], src=src)
            # A file that was never published is published later under its new name.
            if row and row["jstate"] >= J_QUEUED:
                self._queue(db, {"t": "mv", "u": row["uid"], "p": self._uid(db, new_parent), "n": new_name})

    def delete_node(self, nid):
        """Delete a file or an empty directory. File content is kept as a version (or trashed)."""
        with self.tx() as db:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is None:
                return
            old = self._chunk_list(db, nid)
            if row["jstate"] >= J_QUEUED:
                self._act(db, "delete", self._path(db, nid), row["size"], row["is_dir"], row["uid"])
            if old:
                # Record the version before deleting, while its path still resolves.
                vid = self._add_version(db, row, old, "deleted")
            db.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
            db.execute("DELETE FROM nodes WHERE id=?", (nid,))
            if old and vid is None:
                self._release(db, [c[0] for c in old])
            db.execute("INSERT OR REPLACE INTO tombstones(uid, at) VALUES(?, ?)", (row["uid"], time.time()))
            if row["jstate"] >= J_QUEUED:
                self._queue(db, {"t": "rm", "u": row["uid"]})

    def purge_node(self, nid):
        """Remove a file for good (no old version kept), e.g. when its data can't be decrypted."""
        with self.tx() as db:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is None or row["is_dir"]:
                return
            mids = [c[0] for c in self._chunk_list(db, nid)]
            db.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
            db.execute("DELETE FROM nodes WHERE id=?", (nid,))
            self._release(db, mids)
            db.execute("INSERT OR REPLACE INTO tombstones(uid, at) VALUES(?, ?)", (row["uid"], time.time()))
            if row["jstate"] >= J_QUEUED:
                self._queue(db, {"t": "rm", "u": row["uid"]})

    def is_tombstoned(self, uid):
        with self.lock:
            return self.db.execute("SELECT 1 FROM tombstones WHERE uid=?", (uid,)).fetchone() is not None

    def is_ancestor_or_self(self, ancestor_id, nid):
        with self.lock:
            return self._is_ancestor(self.db, ancestor_id, nid)

    @staticmethod
    def _is_ancestor(db, ancestor_id, nid):
        cur = nid
        while cur is not None:
            if cur == ancestor_id:
                return True
            row = db.execute("SELECT parent FROM nodes WHERE id=?", (cur,)).fetchone()
            cur = row[0] if row else None
        return False

    def path_of(self, nid):
        with self.lock:
            return self._path(self.db, nid)

    def unsynced_files(self):
        return self._all("SELECT * FROM nodes WHERE is_dir=0 AND state!='synced'")

    def unsynced_ids(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT id FROM nodes WHERE is_dir=0 AND state!='synced'")]

    # --------------------------------------------------------------- chunks
    def get_chunks(self, nid):
        return self._all("SELECT * FROM chunks WHERE node_id=? ORDER BY idx", (nid,))

    def replace_chunks(self, nid, chunks, version, parity=None):
        """Atomically swap a file's chunk list and publish it. Returns False if the file changed meanwhile.

        parity: {"k": group size, "g": [[gid, [[message_id, size, sha256], ...]], ...]}, one entry
        per group of `k` consecutive chunks, or None for a file without spare pieces."""
        with self.tx() as db:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is None or row["version"] != version:
                return False
            old = self._chunk_list(db, nid)
            size = sum(c["size"] for c in chunks)
            new_mids = [c["message_id"] for c in chunks]
            vid = None
            if old and [c[0] for c in old] != new_mids:
                vid = self._add_version(db, row, old, "replaced")
            db.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
            db.executemany(
                "INSERT INTO chunks(node_id, idx, message_id, attachment_id, url, size, sha256) VALUES(?,?,?,?,?,?,?)",
                [(nid, c["idx"], c["message_id"], c["attachment_id"], c["url"], c["size"], c["sha256"]) for c in chunks],
            )
            db.execute("UPDATE nodes SET state='synced', size=?, jstate=MAX(jstate, 1) WHERE id=?", (size, nid))
            triples = [(c["message_id"], c["size"], c["sha256"]) for c in chunks]
            self._add_parity(db, triples, parity)
            if old and vid is None:
                self._release(db, [c[0] for c in old if c[0] not in new_mids])
            op = {
                "t": "put", "u": row["uid"], "p": self._uid(db, row["parent"]), "n": row["name"],
                "s": size, "m": row["mtime"],
                "c": [list(t) for t in triples],
            }
            if parity:
                op["x"] = parity
            self._queue(db, op)
            if not old or [c[0] for c in old] != new_mids:
                self._act(db, "edit" if old else "add", self._path(db, nid), size, False, row["uid"])
        return True

    def update_chunk_url(self, message_id, url):
        with self.lock:
            self.db.execute("UPDATE chunks SET url=? WHERE message_id=?", (url, message_id))

    # --------------------------------------------------- piece references
    @staticmethod
    def _data_used(db, mid):
        """Does a file, an old version or a snapshot still need this piece?"""
        return db.execute(
            "SELECT 1 FROM chunks WHERE message_id=? UNION ALL "
            "SELECT 1 FROM version_chunks WHERE message_id=? UNION ALL "
            "SELECT 1 FROM snapshot_chunks WHERE message_id=? LIMIT 1", (mid, mid, mid)).fetchone() is not None

    @classmethod
    def _used(cls, db, mid):
        return cls._data_used(db, mid) or db.execute(
            "SELECT 1 FROM parity_shards WHERE message_id=? LIMIT 1", (mid,)).fetchone() is not None

    def is_referenced(self, mid):
        with self.lock:
            return self._used(self.db, mid)

    def release(self, mids):
        """Trash pieces (uploaded but not used after all) unless something else uses them."""
        mids = [m for m in mids if m]
        if mids:
            with self.tx() as db:
                self._release(db, mids)

    def find_dedup(self, sha256, size):
        """A stored piece with exactly this content, if the drive already has one."""
        row = self._one(
            "SELECT message_id FROM chunks WHERE sha256=? AND size=? UNION ALL "
            "SELECT message_id FROM version_chunks WHERE sha256=? AND size=? LIMIT 1",
            (sha256, size, sha256, size))
        return row["message_id"] if row else None

    def queue_op(self, op):
        """Publish an operation through the journal (e.g. a repaired piece)."""
        self.queue_ops([op])

    def queue_ops(self, ops):
        """Publish operations after everything already queued, so they keep their order (a restore
        right after a delete must come after that delete)."""
        with self.tx() as db:
            for op in ops:
                self._queue(db, op)

    # ---------------------------------------------------------------- trash
    def trash_add(self, mids):
        now = time.time()
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO trash(message_id, added) VALUES(?, ?)", [(m, now) for m in mids])

    def trash_batch(self, n=10, grace=None, with_time=False):
        """Trashed pieces that have waited out the grace period (oldest first); with `with_time`,
        (message id, when it was released) pairs."""
        cutoff = time.time() - (TRASH_GRACE if grace is None else grace)
        rows = self._all("SELECT message_id, added FROM trash WHERE added <= ? ORDER BY added LIMIT ?", (cutoff, n))
        return [(r["message_id"], r["added"]) if with_time else r["message_id"] for r in rows]

    def trash_remove(self, mid):
        with self.lock:
            self.db.execute("DELETE FROM trash WHERE message_id=?", (mid,))

    @classmethod
    def _release(cls, db, mids):
        """Trash pieces that nothing (files, versions, snapshots) references any more, and the
        spare pieces of groups that no longer protect anything."""
        now = time.time()
        gids = set()
        for mid in mids:
            if not cls._used(db, mid):
                db.execute("INSERT OR IGNORE INTO trash(message_id, added) VALUES(?, ?)", (mid, now))
            gids.update(r[0] for r in db.execute("SELECT gid FROM parity_members WHERE message_id=?", (mid,)))
        cls._gc_groups(db, gids)

    # ---------------------------------------------------------- spare pieces
    @staticmethod
    def _parity_group_shape(n, k):
        return [(g, min(g + k, n)) for g in range(0, n, k)]

    @classmethod
    def _add_parity(cls, db, triples, parity):
        """Record the spare pieces described by a put operation's "x" field. Returns their group ids."""
        if not parity or not triples:
            return []
        k = int(parity.get("k") or 0)
        groups = parity.get("g") or []
        if k <= 0 or len(groups) != len(cls._parity_group_shape(len(triples), k)):
            log.warning("Ignoring spare pieces that don't match their file")
            return []
        gids = []
        for (start, end), (gid, shards) in zip(cls._parity_group_shape(len(triples), k), groups):
            cls._insert_group(db, str(gid), triples[start:end], shards)
            gids.append(str(gid))
        return gids

    @staticmethod
    def _insert_group(db, gid, members, shards):
        if db.execute("INSERT OR IGNORE INTO parity_groups(gid, created) VALUES(?, ?)", (gid, time.time())).rowcount:
            db.executemany("INSERT INTO parity_members(gid, pos, message_id, size, sha256) VALUES(?,?,?,?,?)",
                           [(gid, i, str(m[0]), int(m[1]), m[2]) for i, m in enumerate(members)])
            db.executemany("INSERT INTO parity_shards(gid, j, message_id, size, sha256) VALUES(?,?,?,?,?)",
                           [(gid, j, str(s[0]), int(s[1]), s[2]) for j, s in enumerate(shards)])

    @classmethod
    def _gc_groups(cls, db, gids):
        """Drop groups none of whose data pieces are used any more, trashing their spare pieces."""
        now = time.time()
        for gid in gids:
            members = [r[0] for r in db.execute("SELECT message_id FROM parity_members WHERE gid=?", (gid,))]
            if any(cls._data_used(db, m) for m in members):
                continue
            shards = [r[0] for r in db.execute("SELECT message_id FROM parity_shards WHERE gid=?", (gid,))]
            for t in ("parity_groups", "parity_members", "parity_shards"):
                db.execute(f"DELETE FROM {t} WHERE gid=?", (gid,))
            for mid in shards:
                if not cls._used(db, mid):
                    db.execute("INSERT OR IGNORE INTO trash(message_id, added) VALUES(?, ?)", (mid, now))

    def add_parity_group(self, gid, members, shards):
        """Record spare pieces for already stored pieces, and publish them."""
        with self.tx() as db:
            self._insert_group(db, gid, members, shards)
            self._queue(db, {"t": "par", "g": gid, "m": members, "p": shards})
            self._gc_groups(db, [gid])

    def find_group(self, member_mids):
        """An existing group with exactly these data pieces in this order: [gid, shards] or None
        (a copy of a file reuses its spare pieces along with its pieces)."""
        if not member_mids:
            return None
        with self.lock:
            for (gid,) in self.db.execute("SELECT gid FROM parity_members WHERE message_id=? AND pos=0",
                                          (member_mids[0],)).fetchall():
                mids = [r[0] for r in self.db.execute(
                    "SELECT message_id FROM parity_members WHERE gid=? ORDER BY pos", (gid,))]
                if mids == list(member_mids):
                    shards = [list(r) for r in self.db.execute(
                        "SELECT message_id, size, sha256 FROM parity_shards WHERE gid=? ORDER BY j", (gid,))]
                    return [gid, shards]
        return None

    def parity_groups_for(self, mid):
        """Every group that can rebuild piece `mid`: [{gid, members: [(mid, size, sha)], shards: [...]}]."""
        with self.lock:
            out = []
            for (gid,) in self.db.execute(
                    "SELECT DISTINCT gid FROM parity_members WHERE message_id=? UNION "
                    "SELECT DISTINCT gid FROM parity_shards WHERE message_id=?", (mid, mid)).fetchall():
                members = [tuple(r) for r in self.db.execute(
                    "SELECT message_id, size, sha256 FROM parity_members WHERE gid=? ORDER BY pos", (gid,))]
                shards = [tuple(r) for r in self.db.execute(
                    "SELECT message_id, size, sha256 FROM parity_shards WHERE gid=? ORDER BY j", (gid,))]
                out.append({"gid": gid, "members": members, "shards": shards})
            return out

    def all_groups(self):
        """Every spare-piece group: {gid: ([(mid, size, sha)] data pieces, [(mid, size, sha)] spare pieces)}."""
        out = {}
        with self.lock:
            for gid, mid, size, sha in self.db.execute(
                    "SELECT gid, message_id, size, sha256 FROM parity_members ORDER BY gid, pos"):
                out.setdefault(gid, ([], []))[0].append((mid, size, sha))
            for gid, mid, size, sha in self.db.execute(
                    "SELECT gid, message_id, size, sha256 FROM parity_shards ORDER BY gid, j"):
                out.setdefault(gid, ([], []))[1].append((mid, size, sha))
        return out

    def pieces_in_channel(self, chan):
        """How many stored pieces the drive keeps in spare channel `chan`."""
        like = f"{chan}-%"
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM (SELECT message_id FROM chunks WHERE message_id LIKE ? UNION "
                "SELECT message_id FROM version_chunks WHERE message_id LIKE ? UNION "
                "SELECT message_id FROM snapshot_chunks WHERE message_id LIKE ? UNION "
                "SELECT message_id FROM parity_shards WHERE message_id LIKE ?)", (like,) * 4).fetchone()[0]

    def parity_group(self, gid):
        """One group as parity_groups_for gives them, or None."""
        with self.lock:
            members = [tuple(r) for r in self.db.execute(
                "SELECT message_id, size, sha256 FROM parity_members WHERE gid=? ORDER BY pos", (gid,))]
            shards = [tuple(r) for r in self.db.execute(
                "SELECT message_id, size, sha256 FROM parity_shards WHERE gid=? ORDER BY j", (gid,))]
        return {"gid": gid, "members": members, "shards": shards} if members else None

    def piece_info(self, mid):
        """(kind, size, sha256) of a stored piece: kind is 'data' or 'parity'; None if unknown."""
        with self.lock:
            for table in ("chunks", "version_chunks", "snapshot_chunks"):
                row = self.db.execute(f"SELECT size, sha256 FROM {table} WHERE message_id=? LIMIT 1", (mid,)).fetchone()
                if row:
                    return "data", row[0], row[1]
            row = self.db.execute("SELECT size, sha256 FROM parity_shards WHERE message_id=? LIMIT 1", (mid,)).fetchone()
            return ("parity", row[0], row[1]) if row else None

    def piece_users(self, mid):
        """What a stored piece belongs to: {"files": [paths], "versions": [{path, at, reason}],
        "snapshots": [{path, at}], "protects": [paths of files it is a spare piece for]}."""
        with self.lock:
            db = self.db
            files = sorted({self._path(db, r[0]) for r in db.execute("SELECT DISTINCT node_id FROM chunks WHERE message_id=?", (mid,))})
            versions = [{"path": r["path"] or "/" + r["name"], "at": r["superseded"], "reason": r["reason"]} for r in db.execute(
                "SELECT DISTINCT v.id, v.path, v.name, v.superseded, v.reason FROM version_chunks c JOIN versions v ON v.id = c.version_id "
                "WHERE c.message_id=? ORDER BY v.superseded DESC", (mid,))]
            snapshots = [{"path": r["path"], "at": r["at"]} for r in db.execute(
                "SELECT DISTINCT e.path, s.at FROM snapshot_chunks c JOIN snapshot_entries e ON e.id = c.entry_id "
                "JOIN snapshots s ON s.id = c.snap_id WHERE c.message_id=? ORDER BY s.at DESC LIMIT 50", (mid,))]
            protects = sorted({self._path(db, r[0]) for r in db.execute(
                "SELECT DISTINCT k.node_id FROM parity_shards s JOIN parity_members m ON m.gid = s.gid "
                "JOIN chunks k ON k.message_id = m.message_id WHERE s.message_id=?", (mid,))})
        return {"files": files, "versions": versions, "snapshots": snapshots, "protects": protects}

    def referenced_mids(self):
        """Every stored piece the drive needs (for the background check), in upload order."""
        with self.lock:
            mids = {r[0] for r in self.db.execute(
                "SELECT message_id FROM chunks UNION SELECT message_id FROM version_chunks UNION "
                "SELECT message_id FROM snapshot_chunks UNION SELECT message_id FROM parity_shards")}
        return sorted(mids, key=lambda m: (len(m), m))

    def unprotected_slices(self, k, limit=1, skip=()):
        """Files with groups of `k` chunks that have no spare pieces yet:
        [(nid, [[(mid, size, sha), ...] per unprotected group])], at most `limit` files, not those in `skip`."""
        out = []
        with self.lock:
            nids = [r[0] for r in self.db.execute(
                "SELECT DISTINCT c.node_id FROM chunks c JOIN nodes n ON n.id = c.node_id "
                "WHERE n.state='synced' AND NOT EXISTS "
                "(SELECT 1 FROM parity_members p WHERE p.message_id = c.message_id) LIMIT ?",
                (limit * 4 + len(skip),))]
            for nid in nids:
                if nid in skip:
                    continue
                triples = self._chunk_list(self.db, nid)
                todo = []
                for start, end in self._parity_group_shape(len(triples), k):
                    part = triples[start:end]
                    if not all(self.db.execute("SELECT 1 FROM parity_members WHERE message_id=? LIMIT 1",
                                               (m[0],)).fetchone() for m in part):
                        todo.append(part)
                if todo:
                    out.append((nid, todo))
                if len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------- versions
    def _add_version(self, db, row, chunks, reason, size=None, mtime=None):
        """Keep `chunks` as an old version of node `row`. Returns the version id, or None
        when versioning is off (the caller then releases the chunks)."""
        if not chunks:
            return None
        # Conflicting remote content is always kept: another device may still be using it.
        if not self.keep_versions and reason != "conflict":
            return None
        cur = db.execute(
            "INSERT INTO versions(uid, parent_uid, name, path, size, mtime, superseded, reason) VALUES(?,?,?,?,?,?,?,?)",
            (row["uid"], self._uid(db, row["parent"]), row["name"], self._path(db, row["id"]),
             size if size is not None else sum(c[1] for c in chunks),
             mtime if mtime is not None else (row["mtime"] if reason == "deleted" else None),
             time.time(), reason),
        )
        vid = cur.lastrowid
        db.executemany(
            "INSERT INTO version_chunks(version_id, idx, message_id, size, sha256) VALUES(?,?,?,?,?)",
            [(vid, i, c[0], c[1], c[2]) for i, c in enumerate(chunks)],
        )
        return vid

    def keep_as_version(self, nid, chunks, reason="conflict", size=None, mtime=None):
        """Store `chunks` [(message_id, size, sha256)] as a version of node `nid`."""
        with self.tx() as db:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is not None:
                self._add_version(db, row, chunks, reason, size=size, mtime=mtime)

    def versions_for(self, uid):
        return self._all("SELECT * FROM versions WHERE uid=? ORDER BY superseded DESC, id DESC", (uid,))

    def version_chunks(self, vid):
        return self._all("SELECT * FROM version_chunks WHERE version_id=? ORDER BY idx", (vid,))

    @classmethod
    def _drop_versions(cls, db, uid):
        vids = [r[0] for r in db.execute("SELECT id FROM versions WHERE uid=?", (uid,))]
        mids = []
        for vid in vids:
            mids += [r[0] for r in db.execute("SELECT message_id FROM version_chunks WHERE version_id=?", (vid,))]
            db.execute("DELETE FROM version_chunks WHERE version_id=?", (vid,))
            db.execute("DELETE FROM versions WHERE id=?", (vid,))
        cls._release(db, mids)
        return len(vids)

    def purge_deleted(self, uids):
        """Delete deleted files for good: drop every kept version of them, on every device.
        Files that exist (again) are left alone. Returns how many were purged."""
        n = 0
        with self.tx() as db:
            for uid in uids:
                if self._nid(db, uid) is not None:
                    continue
                if self._drop_versions(db, uid):
                    self._queue(db, {"t": "purge", "u": uid})
                    n += 1
        return n

    # ------------------------------------------------------------ stars
    def stars_all(self):
        with self.lock:
            return {r[0]: r[1] for r in self.db.execute("SELECT uid, at FROM stars")}

    def star_set(self, uid, on):
        """Star or unstar an item here and on every device."""
        with self.tx() as db:
            if on:
                db.execute("INSERT OR REPLACE INTO stars(uid, at) VALUES(?, ?)", (uid, time.time()))
            else:
                db.execute("DELETE FROM stars WHERE uid=?", (uid,))
            self._queue(db, {"t": "star", "u": uid, "on": 1 if on else 0})

    def _op_star(self, db, op, busy, changed):
        if op.get("on"):
            db.execute("INSERT OR IGNORE INTO stars(uid, at) VALUES(?, ?)", (str(op["u"]), time.time()))
        else:
            db.execute("DELETE FROM stars WHERE uid=?", (str(op["u"]),))

    # ------------------------------------------------------------ activity
    def _act(self, db, kind, path, size=0, is_dir=False, uid=None, device="", src=None):
        if not path or path == "/":
            return
        cur = db.execute("INSERT INTO activity(at, device, kind, path, src, size, is_dir, uid) VALUES(?,?,?,?,?,?,?,?)",
                         (time.time(), device or "", kind, path, src, int(size or 0), 1 if is_dir else 0, uid))
        if cur.lastrowid % 200 == 0:
            db.execute("DELETE FROM activity WHERE id <= ?", (cur.lastrowid - 5000,))

    def rev(self):
        """A number that goes up whenever something is added, changed, moved or deleted, here or on
        another device: a page that shows files asks for this (it costs next to nothing) to know when
        to look again, instead of fetching the lists over and over."""
        with self.lock:
            r = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='activity'").fetchone()
        return int(r[0]) if r else 0

    def activity(self, limit=100, before=None):
        """The newest entries first: [{id, at, device, kind, path, src, size, is_dir, uid}]."""
        return [dict(r) for r in self._all("SELECT * FROM activity WHERE id < ? ORDER BY id DESC LIMIT ?",
                                           (before or 1 << 62, int(limit)))]

    def _act_before(self, db, t, op):
        """What a journal operation from another device is about to do (looked at before it is applied)."""
        if t not in ("put", "mkdir", "mv", "rm"):
            return None
        nid = self._nid(db, op.get("u"))
        row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone() if nid is not None else None
        if t == "rm":
            return None if row is None else {"kind": "delete", "path": self._path(db, nid), "size": row["size"],
                                             "is_dir": row["is_dir"]}
        if t == "mkdir":
            return None if row is not None else {"kind": "mkdir"}
        if t == "mv":
            return None if row is None else {"kind": "move", "src": self._path(db, nid)}
        had = row is not None and db.execute("SELECT 1 FROM chunks WHERE node_id=? LIMIT 1", (nid,)).fetchone()
        return {"kind": "restore" if op.get("r") else ("edit" if had else "add"),
                "old": [c[0] for c in self._chunk_list(db, nid)] if had else None}

    def _act_after(self, db, note, op, device):
        uid = op.get("u")
        if note["kind"] == "delete":
            return self._act(db, "delete", note["path"], note["size"], note["is_dir"], uid, device)
        nid = self._nid(db, uid)
        row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone() if nid is not None else None
        if row is None:
            return
        path = self._path(db, nid)
        if note["kind"] == "move" and note["src"] == path:
            return
        if note.get("old") is not None and note["old"] == [c[0] for c in self._chunk_list(db, nid)]:
            return                          # the same content again (e.g. read back, or only its time changed)
        self._act(db, note["kind"], path, row["size"], row["is_dir"], uid, device, note.get("src"))

    # ------------------------------------------------------------ password locks
    def locks_all(self):
        with self.lock:
            out = {}
            for uid, rec in self.db.execute("SELECT uid, rec FROM locks").fetchall():
                try:
                    out[uid] = json.loads(rec)
                except ValueError:
                    pass
            return out

    def lock_put(self, uid, rec):
        """Lock an item (or change its lock) here and on every device."""
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO locks(uid, rec) VALUES(?, ?)", (uid, json.dumps(rec, separators=(",", ":"))))
            self._queue(db, {"t": "lock", "u": uid, "r": rec})

    def lock_drop(self, uid):
        with self.tx() as db:
            found = db.execute("DELETE FROM locks WHERE uid=?", (uid,)).rowcount > 0
            self._queue(db, {"t": "unlock", "u": uid})
        return found

    def _op_lock(self, db, op, busy, changed):
        rec = op["r"]
        if not isinstance(rec, dict) or not rec.get("pw"):
            raise ValueError("malformed lock")
        db.execute("INSERT OR REPLACE INTO locks(uid, rec) VALUES(?, ?)", (str(op["u"]), json.dumps(rec, separators=(",", ":"))))

    def _op_unlock(self, db, op, busy, changed):
        db.execute("DELETE FROM locks WHERE uid=?", (str(op["u"]),))

    # ------------------------------------------------------------ share links
    def shares_all(self):
        with self.lock:
            out = {}
            for sid, rec in self.db.execute("SELECT id, rec FROM shares").fetchall():
                try:
                    out[sid] = json.loads(rec)
                except ValueError:
                    pass
            return out

    def share_get(self, sid):
        row = self._one("SELECT rec FROM shares WHERE id=?", (sid,))
        try:
            return json.loads(row["rec"]) if row else None
        except ValueError:
            return None

    def share_put(self, sid, rec):
        """Create or change a link here and on every device."""
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO shares(id, rec) VALUES(?, ?)", (sid, json.dumps(rec, separators=(",", ":"))))
            self._queue(db, {"t": "share", "i": sid, "r": rec})

    def share_drop(self, sid):
        with self.tx() as db:
            found = db.execute("DELETE FROM shares WHERE id=?", (sid,)).rowcount > 0
            db.execute("DELETE FROM share_views WHERE id=?", (sid,))
            self._queue(db, {"t": "unshare", "i": sid})
        return found

    def share_views_local(self, sid, device):
        row = self._one("SELECT n FROM share_views WHERE id=? AND device=?", (sid, device))
        return row["n"] if row else 0

    def share_views_total(self, sid):
        row = self._one("SELECT COALESCE(SUM(n), 0) AS n FROM share_views WHERE id=?", (sid,))
        return row["n"] if row else 0

    def share_views_set(self, sid, device, n, publish=True):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO share_views(id, device, n) VALUES(?, ?, ?)", (sid, device, int(n)))
            if publish:
                self._queue(db, {"t": "sharev", "i": sid, "d": device, "n": int(n)})

    def _op_share(self, db, op, busy, changed):
        rec = op["r"]
        if not isinstance(rec, dict) or not rec.get("u"):
            raise ValueError("malformed share link")
        db.execute("INSERT OR REPLACE INTO shares(id, rec) VALUES(?, ?)", (str(op["i"]), json.dumps(rec, separators=(",", ":"))))

    def _op_unshare(self, db, op, busy, changed):
        db.execute("DELETE FROM shares WHERE id=?", (str(op["i"]),))
        db.execute("DELETE FROM share_views WHERE id=?", (str(op["i"]),))

    def _op_sharev(self, db, op, busy, changed):
        if db.execute("SELECT 1 FROM shares WHERE id=?", (str(op["i"]),)).fetchone():
            db.execute("INSERT INTO share_views(id, device, n) VALUES(?, ?, ?) ON CONFLICT(id, device) "
                       "DO UPDATE SET n=MAX(n, excluded.n)", (str(op["i"]), str(op["d"]), int(op["n"])))

    def _op_purge(self, db, op, busy, changed):
        if self._nid(db, op["u"]) is None:
            self._drop_versions(db, op["u"])

    def deleted_files(self, prefix="/"):
        """Most recent 'deleted' version of every file that no longer exists."""
        prefix = "/" + prefix.strip("/")
        rows = self._all(
            "SELECT v.* FROM versions v WHERE v.reason='deleted' "
            "AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.uid=v.uid) "
            "AND v.id = (SELECT v2.id FROM versions v2 WHERE v2.uid=v.uid AND v2.reason='deleted' "
            "            ORDER BY v2.superseded DESC, v2.id DESC LIMIT 1) "
            "ORDER BY v.superseded DESC")
        if prefix == "/":
            return rows
        p = prefix.lower()
        return [r for r in rows if (r["path"] or "").lower() == p or (r["path"] or "").lower().startswith(p + "/")]

    def gc_versions(self, retention_days: float, max_versions: int) -> int:
        """Drop versions older than `retention_days` / beyond `max_versions` per file. 0 = no limit."""
        with self.tx() as db:
            expired = set()
            if retention_days and retention_days > 0:
                cutoff = time.time() - retention_days * 86400
                expired |= {r[0] for r in db.execute("SELECT id FROM versions WHERE superseded < ?", (cutoff,))}
            if max_versions and max_versions > 0:
                expired |= {r[0] for r in db.execute(
                    "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY uid ORDER BY superseded DESC, id DESC) rn "
                    "FROM versions) WHERE rn > ?", (max_versions,))}
            db.execute("DELETE FROM tombstones WHERE at < ?", (time.time() - 180 * 86400,))
            for vid in expired:
                mids = [r[0] for r in db.execute("SELECT message_id FROM version_chunks WHERE version_id=?", (vid,))]
                db.execute("DELETE FROM version_chunks WHERE version_id=?", (vid,))
                db.execute("DELETE FROM versions WHERE id=?", (vid,))
                self._release(db, mids)
            for (sid,) in db.execute("SELECT id FROM snapshots WHERE keep_until IS NOT NULL AND keep_until < ?",
                                     (time.time(),)).fetchall():
                self._drop_snapshot(db, sid)
        return len(expired)

    # ------------------------------------------------------------ snapshots
    SNAPSHOT_PART_PIECES = 4000      # pieces per snapshot message (keeps each one well under 10 MiB)

    def snapshot_ops(self, label="", keep_days=14.0, snap_id=None, at=None):
        """Operations that record the whole tree as it is now (only fully uploaded content)."""
        snap_id = snap_id or new_uid()
        at = at or time.time()
        keep_until = at + keep_days * 86400 if keep_days and keep_days > 0 else None
        entries = []
        with self.lock:
            db = self.db
            for row in db.execute("SELECT * FROM nodes WHERE id != ? AND jstate >= 1 ORDER BY id", (ROOT_ID,)).fetchall():
                if row["uid"] == RECOVERED_UID:
                    continue
                path = self._path(db, row["id"])
                if row["is_dir"]:
                    entries.append([path, 1, 0, row["mtime"], []])
                    continue
                chunks = self._chunk_list(db, row["id"])
                if not chunks and row["size"]:
                    continue        # never uploaded: nothing to keep
                entries.append([path, 0, sum(c[1] for c in chunks), row["mtime"], [list(c) for c in chunks]])
        parts, cur, pieces = [], [], 0
        for e in entries:
            if cur and pieces + len(e[4]) + 1 > self.SNAPSHOT_PART_PIECES:
                parts.append(cur)
                cur, pieces = [], 0
            cur.append(e)
            pieces += len(e[4]) + 1
        parts.append(cur)
        return [{"t": "snap", "i": snap_id, "at": at, "l": label or "", "k": keep_until,
                 "n": n, "N": len(parts), "e": part} for n, part in enumerate(parts)]

    def _op_snap(self, db, op, busy, changed):
        sid = str(op["i"])
        keep_until = op.get("k")
        if keep_until is not None and float(keep_until) < time.time():
            return       # already expired
        db.execute("INSERT OR IGNORE INTO snapshots(id, at, label, device, keep_until, parts) VALUES(?,?,?,?,?,?)",
                   (sid, float(op["at"]), str(op.get("l") or "")[:100], op.get("_d"), keep_until, int(op.get("N", 1))))
        if not db.execute("INSERT OR IGNORE INTO snapshot_parts(snap_id, n) VALUES(?, ?)",
                          (sid, int(op.get("n", 0)))).rowcount:
            return       # this part was applied before
        for path, is_dir, size, mtime, chunks in op.get("e") or []:
            eid = db.execute("INSERT INTO snapshot_entries(snap_id, path, is_dir, size, mtime) VALUES(?,?,?,?,?)",
                             (sid, str(path), 1 if is_dir else 0, int(size), mtime)).lastrowid
            db.executemany(
                "INSERT INTO snapshot_chunks(entry_id, snap_id, idx, message_id, size, sha256) VALUES(?,?,?,?,?,?)",
                [(eid, sid, i, str(c[0]), int(c[1]), c[2]) for i, c in enumerate(chunks)])

    def _drop_snapshot(self, db, sid):
        mids = [r[0] for r in db.execute("SELECT DISTINCT message_id FROM snapshot_chunks WHERE snap_id=?", (sid,))]
        for t, col in (("snapshot_chunks", "snap_id"), ("snapshot_entries", "snap_id"),
                       ("snapshot_parts", "snap_id"), ("snapshots", "id")):
            db.execute(f"DELETE FROM {t} WHERE {col}=?", (sid,))
        self._release(db, mids)

    def delete_snapshot(self, sid):
        with self.tx() as db:
            self._drop_snapshot(db, sid)
            self._queue(db, {"t": "unsnap", "i": sid})

    def _op_unsnap(self, db, op, busy, changed):
        if db.execute("SELECT 1 FROM snapshots WHERE id=?", (str(op["i"]),)).fetchone():
            self._drop_snapshot(db, str(op["i"]))

    def snapshots(self):
        """Complete snapshots, newest first."""
        return self._all(
            "SELECT s.*, (SELECT COUNT(*) FROM snapshot_entries e WHERE e.snap_id=s.id AND e.is_dir=0) AS files, "
            "(SELECT COALESCE(SUM(size), 0) FROM snapshot_entries e WHERE e.snap_id=s.id) AS bytes "
            "FROM snapshots s WHERE (SELECT COUNT(*) FROM snapshot_parts p WHERE p.snap_id=s.id) >= s.parts "
            "ORDER BY s.at DESC")

    def snapshot_entries(self, sid, prefix="/"):
        """Entries of a snapshot under `prefix`: [{path, is_dir, size, mtime, chunks: [(mid, size, sha)]}]."""
        prefix = "/" + prefix.strip("/")
        p = prefix.lower()
        out = []
        with self.lock:
            for e in self.db.execute("SELECT * FROM snapshot_entries WHERE snap_id=? ORDER BY path", (sid,)).fetchall():
                path = e["path"]
                if prefix != "/" and path.lower() != p and not path.lower().startswith(p + "/"):
                    continue
                chunks = [tuple(r) for r in self.db.execute(
                    "SELECT message_id, size, sha256 FROM snapshot_chunks WHERE entry_id=? ORDER BY idx", (e["id"],))]
                out.append({"path": path, "is_dir": bool(e["is_dir"]), "size": e["size"], "mtime": e["mtime"],
                            "chunks": chunks})
        return out

    def last_snapshot_time(self):
        row = self._one("SELECT MAX(at) AS at FROM snapshots")
        return (row or {}).get("at") or 0.0

    # -------------------------------------------------------------- devices
    def note_devices(self, seen):
        """seen: {device_id: (unix_time, name)} from journal entries that were just read."""
        if not seen:
            return
        try:
            known = json.loads(self.kv_get("devices") or "{}")
        except ValueError:
            known = {}
        for dev, (t, name) in seen.items():
            cur = known.get(dev) or {}
            if t >= cur.get("seen", 0):
                known[dev] = {"seen": t, "name": name or cur.get("name") or ""}
        self.kv_set("devices", json.dumps(known, separators=(",", ":")))

    def devices(self):
        try:
            return json.loads(self.kv_get("devices") or "{}")
        except ValueError:
            return {}

    def is_leader(self, me, window=3 * 86400):
        """Background chores (checking pieces, protecting old files, automatic snapshots) run on
        one device: the one with the smallest id among devices seen recently."""
        cutoff = time.time() - window
        active = [d for d, v in self.devices().items() if v.get("seen", 0) >= cutoff and d not in ("cli", "unknown")]
        return not active or me <= min(active)

    # --------------------------------------------------------------- outbox
    def outbox_peek(self, n=200):
        with self.lock:
            return [(r[0], json.loads(r[1])) for r in
                    self.db.execute("SELECT id, op FROM outbox ORDER BY id LIMIT ?", (n,))]

    def outbox_remove(self, ids):
        with self.lock:
            self.db.executemany("DELETE FROM outbox WHERE id=?", [(i,) for i in ids])

    def outbox_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    # ------------------------------------------------------- journal apply
    @staticmethod
    def _nid(db, uid):
        for _ in range(16):
            if uid is None:
                return None
            row = db.execute("SELECT id FROM nodes WHERE uid=?", (uid,)).fetchone()
            if row:
                return row[0]
            a = db.execute("SELECT target FROM alias WHERE uid=?", (uid,)).fetchone()
            if not a:
                return None
            uid = a[0]
        return None

    def _recovered_dir(self, db):
        nid = self._nid(db, RECOVERED_UID)
        if nid is not None:
            return nid
        name = self._free_name(db, ROOT_ID, RECOVERED_NAME)
        now = time.time()
        return db.execute(
            "INSERT INTO nodes(parent, name, is_dir, size, mtime, ctime, atime, uid, jstate) VALUES(?,?,1,0,?,?,?,?,2)",
            (ROOT_ID, name, now, now, now, RECOVERED_UID)).lastrowid

    def _parent_for(self, db, uid):
        nid = self._nid(db, uid)
        if nid is not None:
            row = db.execute("SELECT is_dir FROM nodes WHERE id=?", (nid,)).fetchone()
            if row and row[0]:
                return nid
        return self._recovered_dir(db)

    @staticmethod
    def _free_name(db, parent, name):
        cand, i = name, 2
        while db.execute("SELECT 1 FROM nodes WHERE parent=? AND name=?", (parent, cand)).fetchone():
            stem, ext = os.path.splitext(name)
            cand = f"{stem} ({i}){ext}"
            i += 1
        return cand

    def _place(self, db, parent, name, uid, changed):
        """Name for node `uid` in `parent`. On a clash the side that comes later in the
        journal order gets a conflict name; a local node that is not in the journal yet
        is always later, so it is the one renamed."""
        row = db.execute("SELECT * FROM nodes WHERE parent=? AND name=?", (parent, name)).fetchone()
        if row is None or row["uid"] == uid:
            return name
        if row["jstate"] < J_KNOWN:
            new = self._free_name(db, parent, conflict_name(row["name"], row["uid"]))
            db.execute("UPDATE nodes SET name=? WHERE id=?", (new, row["id"]))
            changed.add(row["id"])
            log.info("Name clash with a change from another device: renamed local '%s' to '%s'", row["name"], new)
            return name
        return self._free_name(db, parent, conflict_name(name, uid))

    # Operations a device running an older version skipped: replayed after an upgrade (see Journal.catch_up).
    EXTRA_OPS = ("par", "fix", "snap", "unsnap", "purge", "share", "unshare", "sharev", "lock", "unlock", "star")

    def apply_ops(self, ops, cursor, busy=None, extras_only=False, device=None):
        """Apply journal operations (from message `cursor`) and advance the cursor.
        Returns the set of local node ids that changed (including deleted ones).

        extras_only: apply only what older versions ignored (spare pieces, repairs, snapshots)
        and leave the cursor alone."""
        busy = busy or (lambda nid: False)
        changed = set()
        with self.tx() as db:
            for op in ops:
                t = str(op.get("t")) if isinstance(op, dict) else ""
                if extras_only:
                    if t == "put":
                        t = "put_extras"
                    elif t not in self.EXTRA_OPS:
                        continue
                fn = getattr(self, "_op_" + t, None) if t else None
                if fn is None:
                    log.warning("Ignoring unknown journal operation: %r", op)
                    continue
                db.execute("SAVEPOINT op")
                try:
                    # what other devices did goes into the activity list (ours is noted when we do it)
                    note = self._act_before(db, t, op) if device and device != self.device and not extras_only else None
                    fn(db, op, busy, changed)
                    if note:
                        self._act_after(db, note, op, device)
                    db.execute("RELEASE op")
                except (KeyError, TypeError, ValueError, IndexError, sqlite3.IntegrityError) as e:
                    db.execute("ROLLBACK TO op")
                    db.execute("RELEASE op")
                    log.warning("Skipping journal operation %r: %s", op, e)
            if not extras_only:
                db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('journal_cursor', ?)", (str(cursor),))
        return changed

    def _op_hb(self, db, op, busy, changed):
        """Heartbeat: a device saying it is alive (the journal notes who posted it)."""

    def _op_put_extras(self, db, op, busy, changed):
        chunks = [(str(c[0]), int(c[1]), c[2]) for c in op["c"]]
        self._gc_groups(db, self._add_parity(db, chunks, op.get("x")))

    def _op_par(self, db, op, busy, changed):
        """Spare pieces added to a file that was uploaded without them."""
        members = [(str(m[0]), int(m[1]), m[2]) for m in op["m"]]
        self._insert_group(db, str(op["g"]), members, op["p"])
        self._gc_groups(db, [str(op["g"])])

    def _op_fix(self, db, op, busy, changed):
        """Piece `o` went missing from Discord and was uploaded again as `n`. Applied in journal
        order, so when two devices repair the same piece, every device keeps the first repair."""
        old, new = str(op["o"]), str(op["n"])
        if old == new:
            return
        nodes = {r[0] for r in db.execute("SELECT node_id FROM chunks WHERE message_id=?", (old,))}
        updated = 0
        for table in _PIECE_TABLES:
            updated += db.execute(f"UPDATE {table} SET message_id=? WHERE message_id=?", (new, old)).rowcount
        if updated:
            db.execute("UPDATE chunks SET url=NULL, attachment_id=NULL WHERE message_id=?", (new,))
            db.execute("DELETE FROM trash WHERE message_id=?", (new,))
            self._release(db, [old])     # in case it wasn't really gone
            changed.update(nodes)
            log.info("Repaired piece %s (now stored as %s)", old, new)
        elif not self._used(db, new):
            # Another device repaired it first: this copy isn't needed.
            db.execute("INSERT OR IGNORE INTO trash(message_id, added) VALUES(?, ?)", (new, time.time()))

    @staticmethod
    def _dead(db, uid):
        return db.execute("SELECT 1 FROM tombstones WHERE uid=?", (uid,)).fetchone() is not None

    def _op_mkdir(self, db, op, busy, changed):
        uid = op["u"]
        if self._dead(db, uid):
            return  # deleted on some device: never comes back
        nid = self._nid(db, uid)
        if nid is not None:
            db.execute("UPDATE nodes SET jstate=2 WHERE id=? AND jstate<2", (nid,))
            return
        parent = self._parent_for(db, op.get("p"))
        same = db.execute("SELECT * FROM nodes WHERE parent=? AND name=?", (parent, op["n"])).fetchone()
        if same is not None and same["is_dir"]:
            # Two devices created the same folder: treat them as one.
            db.execute("INSERT OR REPLACE INTO alias(uid, target) VALUES(?, ?)", (uid, same["uid"]))
            return
        name = self._place(db, parent, op["n"], uid, changed)
        m = float(op.get("m") or time.time())
        nid = db.execute(
            "INSERT INTO nodes(parent, name, is_dir, size, mtime, ctime, atime, uid, jstate) VALUES(?,?,1,0,?,?,?,?,2)",
            (parent, name, m, m, m, uid)).lastrowid
        changed.add(nid)

    def _op_put(self, db, op, busy, changed):
        chunks = [(str(c[0]), int(c[1]), c[2]) for c in op["c"]]
        gids = self._add_parity(db, chunks, op.get("x"))
        self._op_put_content(db, op, busy, changed)
        self._gc_groups(db, gids)    # e.g. a late upload of something deleted elsewhere

    def _op_put_content(self, db, op, busy, changed):
        uid = op["u"]
        chunks = [(str(c[0]), int(c[1]), c[2]) for c in op["c"]]
        size = int(op.get("s", sum(c[1] for c in chunks)))
        mtime = float(op.get("m") or time.time())
        if self._dead(db, uid):
            if not op.get("r"):
                return  # a late upload of something deleted elsewhere: ignore it
            db.execute("DELETE FROM tombstones WHERE uid=?", (uid,))   # undelete / restore-version
        nid = self._nid(db, uid)
        if nid is not None:
            row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row["is_dir"]:
                return
            db.execute("UPDATE nodes SET jstate=2 WHERE id=? AND jstate<2", (nid,))
            old = self._chunk_list(db, nid)
            local_edit = row["state"] != "synced" or busy(nid)
            if [c[0] for c in old] == [c[0] for c in chunks]:
                if not local_edit and row["mtime"] != mtime:
                    db.execute("UPDATE nodes SET mtime=? WHERE id=?", (mtime, nid))
                    changed.add(nid)
                return
            if local_edit:
                # This device has newer edits that will be published after this op and win;
                # keep the other device's content as a version so nothing is lost.
                self._add_version(db, row, chunks, "conflict", size=size, mtime=mtime)
                log.info("Conflicting edit of %s from another device kept as a version", self._path(db, nid))
                return
            vid = self._add_version(db, row, old, "replaced") if old else None
            self._set_chunks(db, nid, chunks)
            db.execute("UPDATE nodes SET size=?, mtime=?, version=version+1, state='synced' WHERE id=?",
                       (size, mtime, nid))
            if old and vid is None:
                self._release(db, [c[0] for c in old])
            changed.add(nid)
            return
        parent = self._parent_for(db, op.get("p"))
        name = self._place(db, parent, op["n"], uid, changed)
        nid = db.execute(
            "INSERT INTO nodes(parent, name, is_dir, size, mtime, ctime, atime, state, uid, jstate) "
            "VALUES(?,?,0,?,?,?,?,'synced',?,2)",
            (parent, name, size, mtime, mtime, mtime, uid)).lastrowid
        self._set_chunks(db, nid, chunks)
        changed.add(nid)

    @staticmethod
    def _set_chunks(db, nid, chunks):
        db.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
        db.executemany(
            "INSERT INTO chunks(node_id, idx, message_id, attachment_id, url, size, sha256) VALUES(?,?,?,NULL,NULL,?,?)",
            [(nid, i, c[0], c[1], c[2]) for i, c in enumerate(chunks)])

    def _op_mv(self, db, op, busy, changed):
        nid = self._nid(db, op["u"])
        parent = self._nid(db, op.get("p"))
        if nid is None or parent is None or nid == ROOT_ID:
            return
        prow = db.execute("SELECT is_dir FROM nodes WHERE id=?", (parent,)).fetchone()
        row = db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        if not prow or not prow[0]:
            return
        if row["is_dir"] and self._is_ancestor(db, nid, parent):
            return  # concurrent moves would create a cycle
        if row["parent"] == parent and row["name"] == op["n"]:
            return
        name = self._place(db, parent, op["n"], row["uid"], changed)
        db.execute("UPDATE nodes SET parent=?, name=?, ctime=? WHERE id=?", (parent, name, time.time(), nid))
        changed.add(nid)

    def _op_rm(self, db, op, busy, changed):
        """Deletes are final: the item (a folder with everything in it) is removed on every
        device and remembered, so a device that still had it can't upload it again."""
        db.execute("INSERT OR REPLACE INTO tombstones(uid, at) VALUES(?, ?)", (op["u"], time.time()))
        nid = self._nid(db, op["u"])
        if nid is None or nid == ROOT_ID:
            return
        stack, order = [nid], []
        while stack:
            cur = stack.pop()
            order.append(cur)
            stack.extend(r[0] for r in db.execute("SELECT id FROM nodes WHERE parent=?", (cur,)))
        for cur in reversed(order):          # children before their folder
            row = db.execute("SELECT * FROM nodes WHERE id=?", (cur,)).fetchone()
            if row is None:
                continue
            old = [] if row["is_dir"] else self._chunk_list(db, cur)
            vid = self._add_version(db, row, old, "deleted") if old else None
            db.execute("DELETE FROM chunks WHERE node_id=?", (cur,))
            db.execute("DELETE FROM nodes WHERE id=?", (cur,))
            db.execute("INSERT OR REPLACE INTO tombstones(uid, at) VALUES(?, ?)", (row["uid"], time.time()))
            if old and vid is None:
                self._release(db, [c[0] for c in old])
            changed.add(cur)

    def _op_mt(self, db, op, busy, changed):
        nid = self._nid(db, op["u"])
        if nid is None:
            return
        row = db.execute("SELECT is_dir, state FROM nodes WHERE id=?", (nid,)).fetchone()
        if not row["is_dir"] and row["state"] == "synced" and not busy(nid):
            db.execute("UPDATE nodes SET mtime=? WHERE id=?", (float(op["m"]), nid))
            changed.add(nid)

    # ------------------------------------------------------------------- kv
    def kv_get(self, key, default=None):
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def kv_set(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, value))

    def kv_delete(self, key):
        with self.lock:
            self.db.execute("DELETE FROM kv WHERE key=?", (key,))

    def kv_keys(self, prefix):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT key FROM kv WHERE key LIKE ?", (prefix + "%",))]

    # ---------------------------------------------------------------- stats
    def stats(self, max_age=5.0):
        t, cached = self._stats_cache
        if cached is not None and time.time() - t < max_age:
            return cached
        with self.lock:
            r = self.db.execute(
                "SELECT COUNT(*) FILTER (WHERE is_dir=0), COUNT(*) FILTER (WHERE is_dir=1) - 1, "
                "COALESCE(SUM(size) FILTER (WHERE is_dir=0), 0), "
                "COUNT(*) FILTER (WHERE is_dir=0 AND state!='synced'), "
                "COUNT(*) FILTER (WHERE is_dir=0 AND is_pinned=1), "
                "COALESCE(SUM(size) FILTER (WHERE is_dir=0 AND is_pinned=1), 0) FROM nodes"
            ).fetchone()
            chunks = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            trash = self.db.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
            outbox = self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            v = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM versions").fetchone()
            protected = self.db.execute(
                "SELECT COUNT(*) FROM chunks c WHERE EXISTS "
                "(SELECT 1 FROM parity_members p WHERE p.message_id = c.message_id)").fetchone()[0]
            spare = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM parity_shards").fetchone()
            snaps = self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            stored = self.db.execute("SELECT COUNT(DISTINCT message_id) FROM chunks").fetchone()[0]
        s = {
            "files": r[0], "dirs": r[1], "bytes": r[2], "unsynced": r[3],
            "pinned_files": r[4], "pinned_bytes": r[5],
            "chunks": chunks, "trash": trash, "outbox": outbox,
            "versions": v[0], "version_bytes": v[1],
            "protected_chunks": protected, "spare_pieces": spare[0], "spare_bytes": spare[1],
            "snapshots": snaps, "unique_chunks": stored,
        }
        self._stats_cache = (time.time(), s)
        return s

    # ------------------------------------------------------------- pinning
    def set_pinned(self, nid: int, pinned: bool = True, recursive: bool = True):
        val = 1 if pinned else 0
        with self.tx() as db:
            if not recursive:
                db.execute("UPDATE nodes SET is_pinned=? WHERE id=?", (val, nid))
            else:
                to_visit = [nid]
                all_ids = []
                while to_visit:
                    cur = to_visit.pop()
                    all_ids.append(cur)
                    to_visit.extend(r[0] for r in db.execute("SELECT id FROM nodes WHERE parent=?", (cur,)))
                db.executemany("UPDATE nodes SET is_pinned=? WHERE id=?", [(val, i) for i in all_ids])

    def is_pinned(self, nid: int) -> bool:
        with self.lock:
            cur = nid
            while cur is not None:
                row = self.db.execute("SELECT parent, is_pinned FROM nodes WHERE id=?", (cur,)).fetchone()
                if not row:
                    break
                if row[1]:
                    return True
                cur = row[0]
            return False

    def pinned_roots(self):
        """Pinned files and folders whose parent isn't pinned (each covers everything inside it)."""
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT n.id FROM nodes n LEFT JOIN nodes p ON p.id = n.parent "
                "WHERE n.is_pinned=1 AND COALESCE(p.is_pinned, 0)=0")]

    def get_pinned_message_ids(self) -> set:
        with self.lock:
            pinned_nodes = {r[0] for r in self.db.execute("SELECT id FROM nodes WHERE is_pinned=1").fetchall()}
            if not pinned_nodes:
                return set()
            all_pinned = set(pinned_nodes)
            to_visit = list(pinned_nodes)
            while to_visit:
                cur = to_visit.pop()
                for (c,) in self.db.execute("SELECT id FROM nodes WHERE parent=?", (cur,)).fetchall():
                    if c not in all_pinned:
                        all_pinned.add(c)
                        to_visit.append(c)
            placeholders = ",".join("?" * len(all_pinned))
            rows = self.db.execute(
                f"SELECT message_id FROM chunks WHERE node_id IN ({placeholders})", tuple(all_pinned)
            ).fetchall()
            return {r[0] for r in rows}

    # --------------------------------------------------------------- backup
    def snapshot_bytes(self) -> bytes:
        """Consistent, gzip-compressed copy of the index for upload as a checkpoint.

        Only state that is fully published is included: files whose first upload
        has not finished are left out, files with unpublished edits are rolled back
        to their last uploaded content, and device-local state (outbox, trash queue,
        offline pins, settings) is stripped. The journal cursor is kept, so a device
        restoring the checkpoint replays exactly the operations that came after it.
        """
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            dst = sqlite3.connect(tmp)
            with self.lock:
                self.db.backup(dst)
            dst.execute("DELETE FROM nodes WHERE id != ? AND jstate < 2", (ROOT_ID,))
            dst.execute(
                "DELETE FROM nodes WHERE is_dir=0 AND state!='synced' "
                "AND NOT EXISTS (SELECT 1 FROM chunks WHERE chunks.node_id=nodes.id)"
            )
            while dst.execute("DELETE FROM nodes WHERE id != ? AND parent NOT IN (SELECT id FROM nodes)",
                              (ROOT_ID,)).rowcount:
                pass
            dst.execute("DELETE FROM chunks WHERE node_id NOT IN (SELECT id FROM nodes)")
            dst.execute(
                "UPDATE nodes SET size=(SELECT COALESCE(SUM(size), 0) FROM chunks WHERE chunks.node_id=nodes.id), "
                "state='synced' WHERE state!='synced'"
            )
            dst.execute("UPDATE nodes SET is_pinned=0")
            dst.execute("DELETE FROM outbox")
            dst.execute("DELETE FROM trash")
            dst.execute("DELETE FROM kv WHERE key NOT IN ('journal_cursor', 'keyring', 'devices')")
            # Spare pieces of files left out above are published with those files later.
            dst.execute(
                "DELETE FROM parity_groups WHERE NOT EXISTS (SELECT 1 FROM parity_members p WHERE p.gid = parity_groups.gid "
                "AND (p.message_id IN (SELECT message_id FROM chunks) "
                "OR p.message_id IN (SELECT message_id FROM version_chunks) "
                "OR p.message_id IN (SELECT message_id FROM snapshot_chunks)))")
            dst.execute("DELETE FROM parity_members WHERE gid NOT IN (SELECT gid FROM parity_groups)")
            dst.execute("DELETE FROM parity_shards WHERE gid NOT IN (SELECT gid FROM parity_groups)")
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("VACUUM")
            dst.close()
            with open(tmp, "rb") as f:
                return gzip.compress(f.read(), 6)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def restore_from_snapshot(self, gz_data: bytes):
        """Replace the index with a snapshot, keeping this device's offline pins and trash queue."""
        raw_db = gzip.decompress(gz_data)
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(raw_db)
            src = sqlite3.connect(tmp)
            with self.lock:
                pinned = [r[0] for r in self.db.execute("SELECT uid FROM nodes WHERE is_pinned=1")]
                trash = self.db.execute("SELECT message_id, added FROM trash").fetchall()
                src.backup(self.db)
                self._migrate()
                self.db.executemany("UPDATE nodes SET is_pinned=1 WHERE uid=?", [(u,) for u in pinned])
                self.db.executemany("INSERT OR IGNORE INTO trash(message_id, added) VALUES(?, ?)",
                                    [tuple(t) for t in trash])
                self._changed()
            src.close()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
