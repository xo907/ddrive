"""Chunk cache with de-duplicated parallel downloads.

Two places to keep downloaded chunks:
* disk (default): an LRU directory of decrypted chunks, up to `max_bytes`.
* memory (`memory_bytes` > 0): an LRU in RAM only, nothing is written to disk.
  Chunks of offline-pinned files are still stored on disk, since pinning is an
  explicit request to keep them locally.
"""

from .logbuf import tname
import hashlib
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from . import codec
from .crypto import AuthenticationError
from .discord_api import DiscordError, is_gone

log = logging.getLogger("ddrive.cache")


class IntegrityError(Exception):
    pass


class ChunkCache:
    def __init__(self, directory, max_bytes, backend, index, threads=4, crypto=None, memory_bytes=0, min_free=0):
        self.dir = directory
        self.max_bytes = max_bytes
        self.backend = backend
        self.index = index
        self.crypto = crypto
        self.memory_bytes = int(memory_bytes or 0)  # > 0: keep non-pinned chunks in RAM only
        self.min_free = int(min_free or 0)          # never fill the disk below this; use RAM instead
        self._low_disk_warned = 0.0

        os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._inflight = {}
        self._lru = OrderedDict()  # message_id -> size (on disk)
        self._total = 0
        self._mem = OrderedDict()  # message_id -> bytes (memory mode)
        self._mem_total = 0
        self._pinned = (0.0, set())
        self.healer = None                          # rebuilds pieces missing from Discord (heal.py)
        self.active = {}                            # message_id -> {node, idx, size, started} (log tab)
        self.recent = []                            # (time, bytes) of finished downloads, for the speed
        self.jobs = {}                              # node_id -> progress of "available offline" downloads
        self.downloaded = 0
        self.pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix=tname("download"))
        entries = []
        for name in os.listdir(directory):
            p = os.path.join(directory, name)
            if name.endswith(".tmp"):
                _silent_remove(p)
            elif name.endswith(".chunk"):
                st = os.stat(p)
                entries.append((st.st_mtime, name[:-6], st.st_size))
        for _, mid, size in sorted(entries):
            self._lru[mid] = size
            self._total += size

    @property
    def memory_mode(self):
        return self.memory_bytes > 0

    def _path(self, mid):
        return os.path.join(self.dir, f"{mid}.chunk")

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._mem.clear()
            self._mem_total = 0

    def _pinned_mids(self):
        t, mids = self._pinned
        if time.time() - t > 5.0:
            try:
                mids = self.index.get_pinned_message_ids()
            except Exception:
                mids = set()
            self._pinned = (time.time(), mids)
        return mids

    # ------------------------------------------------------------------ API
    def put(self, mid, data: bytes, persist=None):
        """Store a chunk. `persist` forces disk (True) or memory (False); by default chunks
        go to disk unless the cache is in memory mode and the chunk is not pinned."""
        if persist is None:
            persist = not self.memory_mode or mid in self._pinned_mids()
        if persist and self.min_free:
            try:
                free = shutil.disk_usage(self.dir).free
            except OSError:
                free = None
            if free is not None and free - len(data) < self.min_free:
                if time.time() - self._low_disk_warned > 300:
                    self._low_disk_warned = time.time()
                    log.warning("Disk nearly full (%.2f GiB free): caching downloaded data in memory instead.",
                                free / 2**30)
                persist = False
        if not persist:
            with self._lock:
                old = self._mem.pop(mid, None)
                self._mem[mid] = data
                self._mem_total += len(data) - (len(old) if old is not None else 0)
                # memory mode: the configured budget; disk mode falling back on a full disk: 64 MiB
                limit = self.memory_bytes if self.memory_mode else 64 * 2**20
                while self._mem_total > limit and len(self._mem) > 1:
                    _, victim = self._mem.popitem(last=False)
                    self._mem_total -= len(victim)
            return data
        p = self._path(mid)
        tmp = f"{p}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
        with self._lock:
            old = self._lru.pop(mid, 0)
            self._lru[mid] = len(data)
            self._total += len(data) - old
        self._evict()
        return p

    def read(self, chunk, start, length) -> bytes:
        for attempt in range(3):
            src = self._ensure(chunk)
            if isinstance(src, bytes):
                return src[start:start + length]
            try:
                with open(src, "rb") as f:
                    f.seek(start)
                    return f.read(length)
            except FileNotFoundError:  # evicted between ensure() and open()
                continue
        raise IOError(f"could not read chunk {chunk['message_id']}")

    def prefetch(self, chunk):
        mid = chunk["message_id"]
        with self._lock:
            if mid in self._mem or mid in self._lru or mid in self._inflight:
                return
            self._inflight[mid] = self.pool.submit(self._download, chunk)

    # ------------------------------------------------------------ internals
    def _ensure(self, chunk):
        """Returns the chunk's bytes (memory) or the path of its file (disk)."""
        mid = chunk["message_id"]
        with self._lock:
            data = self._mem.get(mid)
            if data is not None:
                self._mem.move_to_end(mid)
                return data
            if mid in self._lru and os.path.exists(self._path(mid)):
                self._lru.move_to_end(mid)
                return self._path(mid)
            fut = self._inflight.get(mid)
            if fut is None:
                fut = self.pool.submit(self._download, chunk)
                self._inflight[mid] = fut
        return fut.result()

    def _download(self, chunk, persist=None):
        mid = chunk["message_id"]
        self.active[mid] = {"node": chunk.get("node_id"), "idx": chunk.get("idx"), "size": chunk.get("size") or 0,
                            "started": time.time()}
        try:
            last_err = None
            for attempt in range(3):
                try:
                    data, new_url = self.backend.download(mid, chunk.get("url"))
                except DiscordError as e:
                    if not is_gone(e):
                        raise           # refused or busy: the piece may well be there, don't rebuild it
                    last_err = e        # gone from Discord: rebuild it from its spare pieces
                    break
                if new_url:
                    chunk["url"] = new_url
                    self.index.update_chunk_url(mid, new_url)
                try:
                    data = codec.decode(data, self.crypto, chunk.get("sha256"))
                except AuthenticationError as e:
                    last_err = e
                    log.warning("Could not decrypt piece %s: %s", mid, e)
                    break

                sha = chunk.get("sha256")
                if sha and hashlib.sha256(data).hexdigest() != sha:
                    last_err = IntegrityError(f"checksum mismatch for chunk {mid}")
                    log.warning("%s (attempt %d)", last_err, attempt + 1)
                    continue
                return self.put(mid, data, persist=persist)
            if self.healer is not None:
                data = self.healer.recover(mid)
                if data is not None:
                    return self.put(mid, data, persist=persist)
            raise last_err
        finally:
            info = self.active.pop(mid, None)
            if info is not None and (mid in self._mem or mid in self._lru):
                now = time.time()
                self.downloaded += info["size"]
                self.recent = [r for r in self.recent if now - r[0] < 10] + [(now, info["size"])]
            with self._lock:
                self._inflight.pop(mid, None)

    def peek(self, mid):
        """A cached piece's bytes, without downloading (None if it isn't cached)."""
        with self._lock:
            data = self._mem.get(mid)
            if data is not None:
                return data
            cached = mid in self._lru
        if cached:
            try:
                with open(self._path(mid), "rb") as f:
                    return f.read()
            except OSError:
                return None
        return None

    def is_chunk_cached(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._mem:
                return True
            return message_id in self._lru and os.path.exists(self._path(message_id))

    def _evict(self):
        pinned_mids = self._pinned_mids()
        with self._lock:
            victims = []
            for mid in list(self._lru.keys()):
                if self._total <= self.max_bytes:
                    break
                if mid in pinned_mids:
                    continue
                size = self._lru.pop(mid, 0)
                self._total -= size
                victims.append(mid)

        for mid in victims:
            _silent_remove(self._path(mid))

    def free_space(self, node_id=None, force=False) -> int:
        """Evicts cached chunks from disk (and memory) to free up local storage.
        If node_id is provided, evicts chunks for that node and any child nodes.
        If node_id is None, evicts all non-pinned chunks (or all chunks if force=True).
        Returns total bytes freed from disk.
        """
        if node_id is not None:
            to_visit = [node_id]
            target_mids = set()
            while to_visit:
                cur = to_visit.pop()
                to_visit.extend(c["id"] for c in self.index.children(cur))
                target_mids.update(c["message_id"] for c in self.index.get_chunks(cur))
        else:
            pinned = set() if force else self.index.get_pinned_message_ids()
            with self._lock:
                target_mids = (set(self._lru.keys()) | set(self._mem.keys())) - pinned
        self._pinned = (0.0, set())

        freed_bytes = 0
        with self._lock:
            for mid in target_mids:
                size = self._lru.pop(mid, 0)
                self._total -= size
                freed_bytes += size
                data = self._mem.pop(mid, None)
                if data is not None:
                    self._mem_total -= len(data)

        for mid in target_mids:
            _silent_remove(self._path(mid))

        return freed_bytes

    def prefetch_node(self, node_id: int) -> int:
        """Downloads and permanently caches (on disk) all chunks for node_id and, for a
        folder, everything inside it. Returns the number of chunks."""
        to_visit = [node_id]
        all_chunks = []
        while to_visit:
            cur = to_visit.pop()
            node = self.index.get(cur)
            if node and node["is_dir"]:
                to_visit.extend(c["id"] for c in self.index.children(cur))
            elif node:
                all_chunks.extend(self.index.get_chunks(cur))

        futures = []
        for c in all_chunks:
            mid = c["message_id"]
            with self._lock:
                if mid in self._lru and os.path.exists(self._path(mid)):
                    continue
                data = self._mem.get(mid)
            if data is not None:
                self.put(mid, data, persist=True)
                continue
            futures.append((c, self.pool.submit(self._download, c, True)))

        if futures:
            job = {"path": self.index.path_of(node_id), "total": sum(c["size"] for c, _ in futures), "bytes": 0,
                   "pieces": len(futures), "done": 0, "started": time.time()}
            self.jobs[node_id] = job
            try:
                for c, fut in futures:
                    fut.result()
                    job["bytes"] += c["size"]
                    job["done"] += 1
            finally:
                self.jobs.pop(node_id, None)

        return len(all_chunks)


def _silent_remove(p):
    try:
        os.remove(p)
    except OSError:
        pass
