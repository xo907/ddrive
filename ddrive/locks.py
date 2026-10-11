"""Password-locked folders and files.

A locked item (and everything in it) is not there until it is unlocked with its password: it is not
listed and can't be opened, on the drive letter / mount folder and in the web dashboard (files, search,
gallery, deleted files, snapshots, shared links, the log). Locks are synced between devices like share
links, so an item locked on one device is locked on all of them.

Unlocking is by password alone: every lock with that password opens, so nothing has to show which
folders are locked, or that a particular one exists. Each place unlocks for itself and locks again
after `lock_timeout_minutes` without use:

* the drive letter / mount folder of a device: `unlock` (or the dashboard, "also show on the drive"),
  kept in the index's local settings (kv "unlocked"); a restart of the drive locks everything again;
* a browser signed in to the dashboard: its own set (web.py).

Which locks are open for the code that is running is in `fs.scope.unlocked` (per thread): None means
"this device's drive letter", a set of lock ids is a browser, and ALL is for background work that
must see the whole drive (folder sync).

This is a gate, not a second layer of encryption: the files are encrypted with the drive's key like
all others. It keeps out anyone who uses the computer, the drive letter or the dashboard; it does not
stop somebody who has the config file (the key) and reads the data from Discord or the local index.
"""

import json
import threading
import time

from .crypto import check_password, hash_password

ALL = object()        # scope: every lock is open


class Locks:
    def __init__(self, index, cfg):
        self.index = index
        self.cfg = cfg
        self._lock = threading.Lock()
        self._roots = (0.0, [])          # (read at, [(lower-case path, uid)])
        self._mount = (0.0, frozenset())

    # ------------------------------------------------------------ which paths are locked
    def roots(self):
        """[(path in lower case, uid)] of every locked item that still exists (cached for a moment:
        folders can be renamed, and other devices add and remove locks)."""
        at, roots = self._roots
        if time.time() - at < 2.0:
            return roots
        roots = []
        for uid in self.index.locks_all():
            node = self.index.get_by_uid(uid)
            if node is not None:
                path = self.index.path_of(node["id"])
                if path and path != "/":
                    roots.append((path.lower(), uid))
        self._roots = (time.time(), roots)
        return roots

    def changed(self):
        self._roots = (0.0, [])
        self._mount = (0.0, frozenset())

    def covering(self, path):
        """Ids of the locks `path` is under (its own, or a folder's above it)."""
        roots = self.roots()
        if not roots:
            return ()
        p = "/" + path.replace("\\", "/").strip("/").lower()
        return [uid for root, uid in roots if p == root or p.startswith(root + "/")]

    def hidden(self, path, unlocked=None):
        """True when `path` is locked for whoever has `unlocked` open (None: this device's drive)."""
        if unlocked is ALL:
            return False
        cover = self.covering(path)
        if not cover:
            return False
        if unlocked is None:
            unlocked = self.mount_unlocked()
        return any(uid not in unlocked for uid in cover)

    def hides_text(self, text, unlocked):
        """True when a line of text (a log line, an upload's path) names something locked."""
        if unlocked is ALL:
            return False
        if unlocked is None:
            unlocked = self.mount_unlocked()
        low = text.lower()
        return any(uid not in unlocked and root in low for root, uid in self.roots())

    # ------------------------------------------------------------ locking and passwords
    def is_root(self, uid):
        return uid in self.index.locks_all()

    def add(self, node, password):
        self.index.lock_put(node["uid"], {"pw": hash_password(password), "c": time.time()})
        self.changed()

    def remove(self, uid):
        found = self.index.lock_drop(uid)
        self.changed()
        return found

    def check(self, uid, password):
        rec = self.index.locks_all().get(uid)
        return rec is not None and check_password(password or "", rec.get("pw") or "")

    def matching(self, password):
        """Ids of the locks this password opens."""
        if not password:
            return []
        return [uid for uid, rec in self.index.locks_all().items() if check_password(password, rec.get("pw") or "")]

    def count(self):
        return len(self.roots())

    def timeout(self):
        return max(0.0, float(getattr(self.cfg, "lock_timeout_minutes", 15.0) or 0.0)) * 60.0

    # ------------------------------------------------------------ this device's drive letter
    def mount_unlocked(self):
        at, uids = self._mount
        if time.time() - at < 1.0:
            return uids
        try:
            raw = json.loads(self.index.kv_get("unlocked") or "{}")
        except ValueError:
            raw = {}
        now = time.time()
        uids = frozenset(uid for uid, until in raw.items() if not until or until > now)
        self._mount = (now, uids)
        return uids

    def mount_unlock(self, uids, minutes=None):
        """Show these locked items on this device's drive for a while (0 = until the drive restarts)."""
        seconds = self.timeout() if minutes is None else max(0.0, float(minutes)) * 60.0
        try:
            raw = json.loads(self.index.kv_get("unlocked") or "{}")
        except ValueError:
            raw = {}
        now = time.time()
        raw = {u: t for u, t in raw.items() if not t or t > now}
        for uid in uids:
            raw[uid] = now + seconds if seconds else 0
        self.index.kv_set("unlocked", json.dumps(raw))
        self._mount = (0.0, frozenset())
        return seconds

    def mount_relock(self):
        if self.index.kv_get("unlocked"):
            self.index.kv_delete("unlocked")
        self._mount = (0.0, frozenset())
