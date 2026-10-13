"""The FUSE filesystem (a drive letter via WinFsp on Windows, a libfuse mount on Linux).

Data flow
---------
* Writing: content goes to a local *staging* file (<data dir>/staging).
  When the last writer closes the file it is marked `pending` and queued for upload.
  The uploader splits it into chunks and posts each chunk to the Discord channel.
* Reading: if a staging copy exists it is used; otherwise only the chunks that
  cover the requested byte range are downloaded (and cached), which makes
  streaming/seeking in large media files work without downloading everything.
* Modifying an already-uploaded file downloads it into staging first.
"""

import bisect
import errno
import logging
import os
import shutil
import stat
import threading
import time

from .cache import IntegrityError
from .crypto import AuthenticationError
from .fuse_loader import fuse
from .index import ROOT_ID
from .locks import ALL as ALL_OPEN

FuseOSError = fuse.FuseOSError
Operations = fuse.Operations

log = logging.getLogger("ddrive.fs")

O_ACCMODE = 3
O_TRUNC = getattr(os, "O_TRUNC", 0x200)


def split_path(path):
    path = path.replace("\\", "/").rstrip("/") or "/"
    parent, _, name = path.rpartition("/")
    return (parent or "/"), name


class OpenNode:
    """Shared state for all open handles of one file."""

    def __init__(self, nid):
        self.nid = nid
        self.refs = 0
        self.writers = 0
        self.f = None            # open handle on the staging file (when materialized)
        self.lock = threading.RLock()
        self.dirty = False       # written since the last time it was queued for upload
        self.deleted = False
        self.mtime = None        # mtime to record when the file is finalized


class DDriveFS(Operations):
    def __init__(self, cfg, index, cache, staging_dir):
        self.cfg = cfg
        self.index = index
        self.cache = cache
        self.staging_dir = staging_dir
        os.makedirs(staging_dir, exist_ok=True)
        self.lock = threading.RLock()   # protects open_nodes / handles / _chunk_maps
        self.open_nodes = {}
        self.handles = {}               # fh -> (nid, writable)
        self._next_fh = 0
        self._chunk_maps = {}
        self.uploader = None
        self.capacity = 1 << 50         # advertised size: 1 PiB
        self._staging_usage = (0.0, 0)  # (timestamp, bytes) cache for _pending_staging_bytes
        self._space_warned = 0.0
        self._size_warned = {}
        self.change_listeners = []      # called (with self.lock held) after other devices changed something
        self.locks = None               # password-locked folders (locks.Locks)
        self.scope = threading.local()  # .unlocked: which locks are open for the current thread (see locks.py)

    # ================================================================ helpers
    def staging_path(self, nid):
        return os.path.join(self.staging_dir, f"{nid}.dat")

    def _remove_staging(self, nid):
        try:
            os.remove(self.staging_path(nid))
        except FileNotFoundError:
            pass
        except OSError as e:  # still open somewhere (e.g. uploader); cleaned up on next start
            log.debug("could not remove staging file for %s: %s", nid, e)

    def _pending_staging_bytes(self):
        """Bytes of closed files that are waiting to be uploaded (cached for 1s)."""
        t, used = self._staging_usage
        if time.time() - t < 1.0:
            return used
        with self.lock:
            open_ids = set(self.open_nodes)
        used = 0
        for nid in self.index.unsynced_ids():
            if nid in open_ids:
                continue
            try:
                used += os.path.getsize(self.staging_path(nid))
            except OSError:
                pass
        self._staging_usage = (time.time(), used)
        return used

    def _free_disk(self):
        try:
            return shutil.disk_usage(self.staging_dir).free
        except OSError:
            return None

    def _check_size_limit(self, new_size, path):
        """max_file_size: refuse to let a file grow beyond the configured limit."""
        limit = int(getattr(self.cfg, "max_file_size", 0) or 0)
        if limit > 0 and new_size > limit:
            if self._size_warned.get(path, 0) < time.time() - 60:
                self._size_warned[path] = time.time()
                log.warning("Refused to write %s: files larger than %.1f MiB are not allowed (max_file_size).",
                            path, limit / 2**20)
            raise FuseOSError(errno.EFBIG)
        quota = int(getattr(self.cfg, "quota_bytes", 0) or 0)       # an account's allowance (accounts.py)
        if quota > 0:
            st = self.index.stats(max_age=2)
            if st["bytes"] + st["version_bytes"] + new_size > quota:
                raise FuseOSError(errno.ENOSPC)

    def _ensure_room(self, nbytes, what, max_wait=1800.0):
        """Hard rule: DDrive never takes the local disk below `min_free_disk_bytes`, so
        other programs on the machine keep working. If our own uploads will free space, wait for
        them; otherwise refuse the write (ENOSPC) while the reserve is still intact."""
        reserve = int(getattr(self.cfg, "min_free_disk_bytes", 0) or 0)
        if reserve <= 0:
            return
        free = self._free_disk()
        if free is None or free - nbytes >= reserve:
            return
        start = time.time()
        waiting_logged = False
        while True:
            uploads_pending = self.uploader is not None and (
                not self.uploader.is_idle() or self._pending_staging_bytes() > 0)
            if not uploads_pending or time.time() - start > max_wait:
                if time.time() - self._space_warned > 60:
                    self._space_warned = time.time()
                    log.warning("Refused to write %s: only %.2f GiB free, and DDrive always leaves "
                                "%.2f GiB free for other programs (min_free_disk_bytes). Free up space or wait "
                                "for uploads, then try again.", what, free / 2**30, reserve / 2**30)
                raise FuseOSError(errno.ENOSPC)
            if not waiting_logged:
                log.info("Disk nearly full (%.2f GiB free); waiting for uploads to free space before writing %s",
                         free / 2**30, what)
                waiting_logged = True
            time.sleep(1.0)
            self._staging_usage = (0.0, 0)
            free = self._free_disk()
            if free is None or free - nbytes >= reserve:
                return

    def _wait_for_staging_space(self, max_wait=1800.0, min_free=None):
        """Back-pressure for big copies: block while the upload backlog is too large or the
        local disk is nearly full, until uploads catch up (they delete their local copies).

        Only closed files count towards the backlog, so an application holding several
        large files open can never deadlock itself. Waiting only happens while uploads are
        pending (otherwise nothing would free space), and gives up after `max_wait` seconds
        (e.g. when Discord is unreachable) rather than hanging the caller forever.
        """
        if self.uploader is None:
            return

        def reason():
            return self.holding_back(min_free)

        start = time.time()
        next_log = start
        why = reason()
        while why:
            now = time.time()
            if now - start > max_wait:
                log.warning("Still waiting after %.0fs (%s); continuing anyway", max_wait, why)
                return
            if now >= next_log:
                log.info("Pausing writes until uploads catch up: %s", why)
                next_log = now + 30.0
            time.sleep(1.0)
            self._staging_usage = (0.0, 0)
            why = reason()

    def holding_back(self, min_free=None, extra=0):
        """Why new files have to wait right now (the upload backlog is full, or the disk nearly is and
        uploads will free it), or None when they don't. extra: bytes the caller is about to add."""
        if self.uploader is None:
            return None
        limit = int(getattr(self.cfg, "staging_max_bytes", 0) or 0)
        if min_free is None:
            min_free = int(getattr(self.cfg, "min_free_disk_bytes", 0) or 0)
        pending = self._pending_staging_bytes()
        if not pending and self.uploader.is_idle():
            free = self._free_disk()
            if min_free > 0 and free is not None and free < min_free and time.time() - self._space_warned > 300:
                self._space_warned = time.time()
                log.warning("The local disk is almost full (%.2f GiB free), but not because of DDrive: "
                            "nothing is waiting to upload. Writes may fail with 'No space left on device'; "
                            "free up space elsewhere (other caches, e.g. rclone's VFS cache, logs, images).",
                            free / 2**30)
            return None
        if limit > 0 and pending >= limit:
            return f"upload backlog is {pending / 2**30:.1f} GiB (limit {limit / 2**30:.1f} GiB)"
        free = self._free_disk()
        if min_free > 0 and free is not None and free - extra < min_free:
            return (f"only {free / 2**30:.1f} GiB free on the local disk "
                    f"(keeping {min_free / 2**30:.1f} GiB free); {pending / 2**30:.1f} GiB waiting to upload")
        return None

    def room_for(self, nbytes):
        """None when `nbytes` more fit on this computer's disk without going below what is kept free
        (min_free_disk_bytes), else a sentence that says why not."""
        reserve = int(getattr(self.cfg, "min_free_disk_bytes", 0) or 0)
        free = self._free_disk()
        if reserve <= 0 or free is None or free - nbytes >= reserve:
            return None
        return (f"{free / 2**30:.1f} GiB are free and {reserve / 2**30:.1f} GiB of that are always kept free "
                "(min_free_disk_bytes)")

    def room(self):
        """How many bytes more fit on this computer's disk right now without going below what is always
        kept free (min_free_disk_bytes); None when nothing is kept free or the disk can't be asked."""
        reserve = int(getattr(self.cfg, "min_free_disk_bytes", 0) or 0)
        free = self._free_disk()
        return None if reserve <= 0 or free is None else max(0, free - reserve)

    def could_hold(self, nbytes):
        """False when a file of `nbytes` can't be on this computer's disk whatever else goes from it:
        the whole disk, less what is always kept free, is smaller than that."""
        reserve = int(getattr(self.cfg, "min_free_disk_bytes", 0) or 0)
        try:
            return shutil.disk_usage(self.staging_dir).total - reserve >= nbytes
        except OSError:
            return True

    def adopt_file(self, path, src, mtime=None):
        """Make the local file `src` the content of drive file `path` by moving it there (no second
        copy on this disk); it then uploads like any other. Returns its size. `src` is gone afterwards."""
        size = os.path.getsize(src)
        self._check_size_limit(size, path)
        fh = self.create(path, 0o644, wait=False)
        try:
            _, _, on = self._handle(fh)
            with on.lock:
                p = self.staging_path(on.nid)
                if on.f is not None:
                    on.f.close()
                    on.f = None
                try:
                    os.replace(src, p)
                except OSError:              # another disk, or (Windows) the old copy is open elsewhere: copy it
                    with open(src, "rb") as f, open(p, "wb") as out:
                        shutil.copyfileobj(f, out, 1 << 20)
                    os.remove(src)
                on.f = open(p, "r+b")
                on.dirty = True
                on.mtime = mtime if mtime is not None else time.time()
        finally:
            self.release(path, fh)
        return size

    def _hidden(self, path):
        """Folders this device doesn't show (hidden_folders): they stay on the drive and on
        other devices, they are just not listed or reachable here."""
        p = "/" + path.replace("\\", "/").strip("/").lower()
        for h in getattr(self.cfg, "hidden_folders", None) or ():
            h = "/" + str(h).replace("\\", "/").strip("/").lower()
            if h != "/" and (p == h or p.startswith(h + "/")):
                return True
        # Password-locked folders and files: not there until unlocked (for the drive letter: on this
        # device; for the dashboard: in that browser; background copies such as sync see everything).
        return self.locks is not None and self.locks.hidden(p, getattr(self.scope, "unlocked", None))

    def hides_below(self, path):
        """False when nothing under folder `path` can be hidden (no hidden folder and no lock below it):
        then its children need no _hidden() check each, which is what makes listing a big folder slow."""
        p = "/" + path.replace("\\", "/").strip("/").lower()
        p = "" if p == "/" else p
        for h in getattr(self.cfg, "hidden_folders", None) or ():
            h = "/" + str(h).replace("\\", "/").strip("/").lower()
            if h != "/" and h.startswith(p + "/"):
                return True
        if self.locks is None or getattr(self.scope, "unlocked", None) is ALL_OPEN:
            return False
        return any(root.startswith(p + "/") for root, _ in self.locks.roots())

    def _get(self, path):
        if self._hidden(path):
            raise FuseOSError(errno.ENOENT)
        n = self.index.resolve(path)
        if n is None:
            raise FuseOSError(errno.ENOENT)
        return n

    def read_file(self, nid, offset, size):
        """Read part of a file by node id, from its local copy if there is one (web dashboard)."""
        p = self.staging_path(nid)
        with self.lock:
            on = self.open_nodes.get(nid)
        if on is not None or os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    f.seek(offset)
                    return f.read(size)
            except FileNotFoundError:
                pass
        return self._read_remote(nid, offset, size)

    def _attrs(self, n):
        is_dir = bool(n["is_dir"])
        size, mtime = n["size"], n["mtime"]
        if not is_dir:
            with self.lock:
                on = self.open_nodes.get(n["id"])
            if on is not None and on.dirty:
                f = on.f
                if f is not None:
                    try:
                        size = os.fstat(f.fileno()).st_size
                    except (OSError, ValueError):
                        pass
                if on.mtime:
                    mtime = on.mtime
        return {
            "st_mode": (stat.S_IFDIR if is_dir else stat.S_IFREG) | 0o777,
            "st_nlink": 2 if is_dir else 1,
            "st_size": 0 if is_dir else size,
            "st_mtime": mtime,
            "st_atime": n["atime"],
            "st_ctime": n["ctime"],
            "st_birthtime": n["ctime"],
            "st_uid": os.getuid() if hasattr(os, "getuid") else 0,
            "st_gid": os.getgid() if hasattr(os, "getgid") else 0,
        }

    def _open_handle(self, nid, writable):
        """Caller must hold self.lock."""
        on = self.open_nodes.get(nid)
        if on is None:
            on = self.open_nodes[nid] = OpenNode(nid)
        on.refs += 1
        if writable:
            on.writers += 1
        self._next_fh += 1
        self.handles[self._next_fh] = (nid, writable)
        return self._next_fh

    def _handle(self, fh):
        with self.lock:
            entry = self.handles.get(fh)
            if entry is None:
                raise FuseOSError(errno.EBADF)
            return entry[0], entry[1], self.open_nodes[entry[0]]

    def _chunk_map(self, nid):
        with self.lock:
            m = self._chunk_maps.get(nid)
            if m is None:
                chunks = self.index.get_chunks(nid)
                offsets, pos = [], 0
                for c in chunks:
                    offsets.append(pos)
                    pos += c["size"]
                m = self._chunk_maps[nid] = (offsets, chunks, pos)
            return m

    def _materialize(self, on, truncate_to=None):
        """Make sure on.f is a read/write handle on a full local copy. Caller holds on.lock."""
        if on.f is None:
            p = self.staging_path(on.nid)
            if os.path.exists(p):
                on.f = open(p, "r+b")
            else:
                _, chunks, total = self._chunk_map(on.nid)
                if truncate_to == 0 or total == 0:
                    on.f = open(p, "w+b")
                else:
                    self._ensure_room(total, self.index.path_of(on.nid))
                    log.info("Downloading %s for editing (%d bytes)", self.index.path_of(on.nid), total)
                    tmp = p + ".part"
                    with open(tmp, "wb") as out:
                        for c in chunks:
                            out.write(self._read_chunk(on.nid, c, 0, c["size"]))
                    os.replace(tmp, p)
                    on.f = open(p, "r+b")
        if truncate_to is not None:
            on.f.truncate(truncate_to)

    def _read_chunk(self, nid, chunk, start, length):
        """Read part of a chunk; failures become a clear log line and an I/O error for the application."""
        try:
            return self.cache.read(chunk, start, length)
        except AuthenticationError:
            log.error("Cannot decrypt %s (message %s): it was encrypted with a different key, or the data is "
                      "damaged. Older versions may still be readable (see the 'versions' command).",
                      self.index.path_of(nid), chunk["message_id"])
        except IntegrityError:
            log.error("Downloaded data for %s (message %s) failed its checksum.", self.index.path_of(nid),
                      chunk["message_id"])
        except Exception as e:
            log.error("Could not download part of %s (message %s): %s", self.index.path_of(nid),
                      chunk["message_id"], e)
        raise FuseOSError(errno.EIO)

    def _read_remote(self, nid, offset, size):
        offsets, chunks, total = self._chunk_map(nid)
        if offset >= total or size <= 0:
            return b""
        end = min(offset + size, total)
        i = bisect.bisect_right(offsets, offset) - 1
        parts = []
        while i < len(chunks) and offsets[i] < end:
            c = chunks[i]
            s = max(offset, offsets[i]) - offsets[i]
            e = min(end, offsets[i] + c["size"]) - offsets[i]
            parts.append(self._read_chunk(nid, c, s, e - s))
            i += 1
        for j in range(i, min(i + self.cfg.prefetch_chunks, len(chunks))):
            self.cache.prefetch(chunks[j])
        return b"".join(parts)

    # ============================================== hooks used by the uploader
    def is_busy(self, nid):
        with self.lock:
            on = self.open_nodes.get(nid)
            return on is not None and on.dirty

    def commit_upload(self, nid, version, uploaded, parity=None):
        """Swap in freshly uploaded chunks unless the file changed during upload."""
        mids = [u["message_id"] for u in uploaded]
        for _, shards in (parity or {}).get("g") or []:
            mids += [s[0] for s in shards]
        with self.lock:
            node = self.index.get(nid)
            on = self.open_nodes.get(nid)
            if node is None or node["version"] != version or (on is not None and on.dirty):
                self.index.release(mids)     # reused pieces still belong to other files
                if node is None and on is None:
                    self._remove_staging(nid)
                return False
            if not self.index.replace_chunks(nid, uploaded, version, parity):
                self.index.release(mids)
                return False
            self._chunk_maps.pop(nid, None)
            if on is None:
                # Pinned files already had their chunks written to the cache by the uploader.
                self._remove_staging(nid)
            return True


    def on_remote_change(self, nids):
        """Called (with self.lock held) after operations from other devices were applied."""
        for nid in nids:
            self._chunk_maps.pop(nid, None)
            if nid in self.open_nodes:
                if self.index.get(nid) is None:
                    self.open_nodes[nid].deleted = True
                continue
            node = self.index.get(nid)
            if node is None or (not node["is_dir"] and node["state"] == "synced"):
                self._remove_staging(nid)
        if nids:
            for fn in list(self.change_listeners):
                try:
                    fn(nids)
                except Exception as e:
                    log.debug("change listener failed: %s", e)

    def put_file(self, path, src, mtime=None, piece=1 << 20, stop=None):
        """Write a whole file from the open binary file `src` (it then uploads like any other) and
        give it modification time `mtime`. Returns the number of bytes written. `stop()` returning
        true abandons the copy (the file keeps what was written so far and is fixed by the next copy)."""
        fh = self.create(path, 0o644)
        pos = 0
        try:
            while True:
                if stop is not None and stop():
                    raise InterruptedError("stopped")
                data = src.read(piece)
                if not data:
                    break
                self.write(path, data, pos, fh)
                pos += len(data)
            if mtime is not None:
                _, _, on = self._handle(fh)
                with on.lock:
                    on.mtime = mtime
        finally:
            self.release(path, fh)
        return pos

    def recover(self):
        """Called at startup: clean stale staging files and re-queue unfinished uploads."""
        for name in os.listdir(self.staging_dir):
            p = os.path.join(self.staging_dir, name)
            if name.endswith(".keep"):
                log.warning("Found %s from an interrupted index restore; left in place, recover it manually", p)
                continue
            if not name.endswith(".dat"):
                try:
                    os.remove(p)
                except OSError:
                    pass
                continue
            try:
                nid = int(name[:-4])
            except ValueError:
                continue
            node = self.index.get(nid)
            if node is None or node["is_dir"] or node["state"] == "synced":
                self._remove_staging(nid)
        requeued = 0
        for node in self.index.unsynced_files():
            if os.path.exists(self.staging_path(node["id"])):
                self.index.update(node["id"], state="pending")
                if self.uploader:
                    self.uploader.enqueue(node["id"], 0)
                requeued += 1
            elif not self.index.get_chunks(node["id"]):
                # Never uploaded and its local copy is gone: nothing left to upload or show.
                log.warning("Dropping %s: it was never uploaded and its local copy is gone",
                            self.index.path_of(node["id"]))
                self.index.delete_node(node["id"])
            else:
                size = sum(c["size"] for c in self.index.get_chunks(node["id"]))
                log.warning("Local copy of %s is missing; reverting to last uploaded version",
                            self.index.path_of(node["id"]))
                self.index.update(node["id"], state="synced", size=size)
        if requeued:
            log.info("Resuming %d unfinished upload(s)", requeued)

    # ============================================================ operations
    def getattr(self, path, fh=None):
        return self._attrs(self._get(path))

    def readdir(self, path, fh):
        n = self._get(path)
        if not n["is_dir"]:
            raise FuseOSError(errno.ENOTDIR)
        out = [(".", None, 0), ("..", None, 0)]
        base = path.replace("\\", "/").rstrip("/")
        for c in self.index.children(n["id"]):
            if self._hidden(f"{base}/{c['name']}"):
                continue
            out.append((c["name"], self._attrs(c), 0))
        return out

    def _refuse_hidden(self, path):
        """New items can't be created inside a folder this device hides (they would be invisible)."""
        if self._hidden(path):
            raise FuseOSError(errno.EACCES)

    def mkdir(self, path, mode):
        self._refuse_hidden(path)
        ppath, name = split_path(path)
        parent = self._get(ppath)
        if not parent["is_dir"]:
            raise FuseOSError(errno.ENOTDIR)
        if self.index.lookup(parent["id"], name):
            raise FuseOSError(errno.EEXIST)
        self.index.create_node(parent["id"], name, True)
        return 0

    def rmdir(self, path):
        n = self._get(path)
        if n["id"] == ROOT_ID:
            raise FuseOSError(errno.EBUSY)
        if not n["is_dir"]:
            raise FuseOSError(errno.ENOTDIR)
        if self.index.children(n["id"], limit=1):
            raise FuseOSError(errno.ENOTEMPTY)
        self.index.delete_node(n["id"])
        return 0

    def create(self, path, mode, fi=None, wait=True):
        self._refuse_hidden(path)
        ppath, name = split_path(path)
        if wait:
            self._wait_for_staging_space()
        with self.lock:
            parent = self._get(ppath)
            if not parent["is_dir"]:
                raise FuseOSError(errno.ENOTDIR)
            existing = self.index.lookup(parent["id"], name)
            if existing:
                if existing["is_dir"]:
                    raise FuseOSError(errno.EISDIR)
                nid = existing["id"]
            else:
                nid = self.index.create_node(parent["id"], name, False, state="pending")["id"]
            fh = self._open_handle(nid, True)
            on = self.open_nodes[nid]
        with on.lock:
            self._materialize(on, 0)
            on.dirty = True
            on.mtime = time.time()
        return fh

    def open(self, path, flags):
        n = self._get(path)
        if n["is_dir"]:
            raise FuseOSError(errno.EISDIR)
        writable = (flags & O_ACCMODE) != os.O_RDONLY
        with self.lock:
            fh = self._open_handle(n["id"], writable)
            on = self.open_nodes[n["id"]]
        if writable and flags & O_TRUNC:
            with on.lock:
                self._materialize(on, 0)
                on.dirty = True
                on.mtime = time.time()
        return fh

    def read(self, path, size, offset, fh):
        nid, _, on = self._handle(fh)
        with on.lock:
            if on.f is None and not on.deleted and os.path.exists(self.staging_path(nid)):
                on.f = open(self.staging_path(nid), "r+b")
            if on.f is not None:
                on.f.seek(offset)
                return on.f.read(size)
        return self._read_remote(nid, offset, size)

    def write(self, path, data, offset, fh):
        nid, _, on = self._handle(fh)
        self._check_size_limit(offset + len(data), path)
        self._ensure_room(len(data), path)
        with on.lock:
            self._materialize(on)
            on.f.seek(offset)
            on.f.write(data)
            on.dirty = True
            on.mtime = time.time()
        return len(data)

    def truncate(self, path, length, fh=None):
        self._check_size_limit(length, path)
        temp_fh = None
        with self.lock:
            if fh is not None and fh in self.handles:
                nid = self.handles[fh][0]
            else:
                n = self._get(path)
                if n["is_dir"]:
                    raise FuseOSError(errno.EISDIR)
                nid = n["id"]
                temp_fh = self._open_handle(nid, True)
            on = self.open_nodes[nid]
        try:
            with on.lock:
                if on.f is not None:
                    current = os.fstat(on.f.fileno()).st_size
                elif os.path.exists(self.staging_path(nid)):
                    current = os.path.getsize(self.staging_path(nid))
                else:
                    current = self._chunk_map(nid)[2]
                if current != length:
                    self._materialize(on, length)
                    on.dirty = True
                    on.mtime = time.time()
        finally:
            if temp_fh is not None:
                self.release(path, temp_fh)
        return 0

    def flush(self, path, fh):
        try:
            _, _, on = self._handle(fh)
        except FuseOSError:
            return 0
        with on.lock:
            if on.f is not None:
                on.f.flush()
        return 0

    fsync = lambda self, path, datasync, fh: self.flush(path, fh)

    def release(self, path, fh):
        with self.lock:
            entry = self.handles.pop(fh, None)
            if entry is None:
                return 0
            nid, writable = entry
            on = self.open_nodes.get(nid)
            if on is None:
                return 0
            on.refs -= 1
            if writable:
                on.writers -= 1
            last = on.refs <= 0
            if last:
                del self.open_nodes[nid]
            with on.lock:
                finalize = on.dirty and on.writers <= 0 and not on.deleted
                size = 0
                if finalize:
                    if on.f is not None:
                        on.f.flush()
                        size = os.fstat(on.f.fileno()).st_size
                    on.dirty = False
                    self.index.mark_modified(nid, size, on.mtime or time.time())
                if last and on.f is not None:
                    on.f.close()
                    on.f = None
                deleted = on.deleted
        if finalize and self.uploader is not None:
            self.uploader.enqueue(nid)
        if last:
            if deleted:
                self._remove_staging(nid)
            else:
                node = self.index.get(nid)
                if node and node["state"] == "synced":
                    self._remove_staging(nid)
        return 0


    def unlink(self, path):
        n = self._get(path)
        if n["is_dir"]:
            raise FuseOSError(errno.EISDIR)
        nid = n["id"]
        with self.lock:
            on = self.open_nodes.get(nid)
            if on is not None:
                on.deleted = True
            self.index.delete_node(nid)
            self._chunk_maps.pop(nid, None)
        if on is None:
            self._remove_staging(nid)
        return 0

    def rename(self, old, new):
        self._refuse_hidden(new)
        n = self._get(old)
        nparent_path, nname = split_path(new)
        with self.lock:
            np_ = self._get(nparent_path)
            if not np_["is_dir"]:
                raise FuseOSError(errno.ENOTDIR)
            if n["is_dir"] and self.index.is_ancestor_or_self(n["id"], np_["id"]):
                raise FuseOSError(errno.EINVAL)
            target = self.index.lookup(np_["id"], nname)
            if target and target["id"] != n["id"]:
                if target["is_dir"]:
                    if not n["is_dir"]:
                        raise FuseOSError(errno.EISDIR)
                    if self.index.children(target["id"], limit=1):
                        raise FuseOSError(errno.ENOTEMPTY)
                    self.index.delete_node(target["id"])
                else:
                    if n["is_dir"]:
                        raise FuseOSError(errno.ENOTDIR)
                    self.unlink(new)
            self.index.rename(n["id"], np_["id"], nname)
        return 0

    def utimens(self, path, times=None):
        n = self._get(path)
        now = time.time()
        atime, mtime = times if times else (now, now)
        self.index.touch(n["id"], atime, mtime)
        with self.lock:
            on = self.open_nodes.get(n["id"])
            if on is not None:
                on.mtime = mtime
        return 0

    # No `lock` method on purpose: without one, fusepy leaves POSIX/flock locking to the kernel
    # (and WinFsp), which is what applications such as databases expect. Defining it makes the
    # kernel route every lock request through FUSE instead, and they fail.

    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def access(self, path, mode):
        return 0

    def statfs(self, path):
        bs = 4096
        total = self.capacity // bs
        s = self.index.stats()
        used = min(total, (s["bytes"] + bs - 1) // bs)
        files = 1 << 32
        return {
            "f_bsize": bs, "f_frsize": bs, "f_blocks": total, "f_bfree": total - used,
            "f_bavail": total - used, "f_files": files, "f_ffree": files - s["files"] - s["dirs"],
            "f_favail": files - s["files"] - s["dirs"], "f_namemax": 255,
        }
