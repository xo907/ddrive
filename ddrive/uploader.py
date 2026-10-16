"""Background upload queue, chunker and trash cleaner. (Index checkpoints live in journal.py.)

A file is uploaded in groups of `parity_group` pieces. The pieces of a group are uploaded in
parallel (spread over every configured bot), then the group's spare pieces are computed from the
local copy and uploaded too. Each piece is compressed when that helps, then encrypted (codec.py).
A piece whose exact content is already stored on the drive is not uploaded again (dedup).
"""

import collections
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .logbuf import tname
from . import codec, rs
from .channels import layout_of, spread_pieces

log = logging.getLogger("ddrive.uploader")

MAX_PIECE_WORKERS = 16     # pieces in flight at once (each holds a few copies of up to chunk_size in RAM)
PARITY_BATCH = 8           # spare pieces computed (and held in memory) at a time
PROGRESS_EVERY = 2.0       # seconds between saves of an upload's progress (always saved at the end of a group)
REVERIFY_AFTER = 300.0     # re-check reused pieces still exist if the upload took longer than this


class _Interrupted(Exception):
    pass


def _free_memory():
    """Bytes of memory free right now, or 0 if that can't be told."""
    try:
        from . import resources
        f = resources._memory_windows if os.name == "nt" else resources._memory_linux
        return int((f() or {}).get("available") or 0)
    except Exception:
        return 0


def parity_shape(cfg, n, channels=1):
    """(group size, spare pieces for a group of n) under the current settings; (0, 0) when off.
    Spread over `channels` channels, a group gets enough spare pieces to lose any one of them."""
    if not getattr(cfg, "parity_enabled", False):
        return 0, 0
    k = max(1, int(getattr(cfg, "parity_group", 10) or 10))
    m = max(0, int(getattr(cfg, "parity_pieces", 2) or 0))
    if m == 0:
        return 0, 0
    if n < 4:
        m = max(1, -(-m * n // k))      # their share: 1 with the default 2 per 10, 3 for 3 pieces at 10 per 10
    m = max(m, spread_pieces(min(k, n), channels))
    return k, min(m, rs.MAX_PIECES - min(k, n))


class Uploader:
    def __init__(self, cfg, index, backend, fs, crypto=None):
        self.cfg = cfg
        self.index = index
        self.backend = backend
        self.fs = fs
        self.crypto = crypto
        self.journal = None

        self._lock = threading.Lock()
        self._pending = {}  # nid -> scheduled_time
        self._active = set()  # set of nids currently being uploaded
        self._failures = {}  # nid -> consecutive failed attempts (for back-off)
        self._cv = threading.Condition(self._lock)
        self._running = False

        self._upload_threads = []
        self._trash_thread = None
        self._pool = None
        self._parity_pool = None  # computes and uploads a group's spare pieces while the next group uploads
        self._reserved = collections.Counter()   # reused pieces of uploads in progress: never trash them
        self.progress = {}                         # nid -> {"path", "done", "total", "size"} (status page)
        self.stats = {"uploaded_bytes": 0, "reused_bytes": 0, "compressed_saved": 0, "spare_bytes": 0,
                      "files": 0, "file_bytes": 0, "failed": 0, "last_done": 0.0, "last_failed": 0.0}   # since the drive started

    # ----------------------------------------------------- resumable uploads
    # After every uploaded piece, what is done so far is saved under "upload:<nid>" in the index,
    # so an interrupted upload (restart, crash, network error) continues where it stopped instead
    # of starting over. A record only applies to the same file version.

    @staticmethod
    def _progress_key(nid):
        return f"upload:{nid}"

    def _load_progress(self, nid):
        try:
            return json.loads(self.index.kv_get(self._progress_key(nid)) or "null")
        except ValueError:
            return None

    def _save_progress(self, nid, version, size, chunk_size, done, k, parity):
        chunks = [done[i] for i in sorted(done)]
        self.index.kv_set(self._progress_key(nid), json.dumps(
            {"v": version, "size": size, "chunk_size": chunk_size, "chunks": chunks, "k": k, "parity": parity},
            separators=(",", ":")))

    @staticmethod
    def record_mids(rec):
        """Every piece an upload record refers to (data and spare pieces)."""
        if not rec:
            return []
        mids = [c["message_id"] for c in rec.get("chunks") or []]
        for _, shards in (rec.get("parity") or {}).values():
            mids += [s[0] for s in shards]
        return mids

    def _drop_progress(self, nid, trash=True):
        rec = self._load_progress(nid)
        self.index.kv_delete(self._progress_key(nid))
        if trash:
            self.index.release(self.record_mids(rec))

    def _cleanup_stale_progress(self):
        for key in self.index.kv_keys("upload:"):
            try:
                nid = int(key.split(":", 1)[1])
            except ValueError:
                continue
            node = self.index.get(nid)
            if node is None or node["is_dir"] or node["state"] == "synced":
                self._drop_progress(nid)

    def _piece_workers(self):
        """Pieces uploaded at once: upload_threads per bot (over every channel's bots), at most
        MAX_PIECE_WORKERS, and no more than a quarter of the free memory can hold."""
        per_bot = max(1, int(getattr(self.cfg, "upload_threads", 1) or 1))
        n = min(MAX_PIECE_WORKERS, per_bot * int(getattr(self.backend, "upload_slots", 1) or 1))
        free = _free_memory()
        if free:
            n = min(n, max(4, free // 4 // max(1, 4 * int(self.cfg.chunk_size or 1))))
        return max(1, n)

    def start(self):
        try:
            self._cleanup_stale_progress()
        except Exception as e:
            log.debug("Could not clean up old upload records: %s", e)
        with self._lock:
            if self._running:
                return
            self._running = True
            self._pool = ThreadPoolExecutor(max_workers=self._piece_workers(), thread_name_prefix=tname("UploadPiece"))
            n = max(1, int(getattr(self.cfg, "upload_threads", 1) or 1))
            self._parity_pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix=tname("SparePieces"))
            self._upload_threads = [
                threading.Thread(target=self._upload_loop, name=tname(f"UploaderWorker-{i}"), daemon=True)
                for i in range(n)
            ]
            self._trash_thread = threading.Thread(target=self._trash_loop, name=tname("TrashCleanerWorker"), daemon=True)
            for t in self._upload_threads:
                t.start()
            self._trash_thread.start()
        log.info("Uploader started (%d file worker(s), %d piece(s) at a time over %d bot(s)).",
                 len(self._upload_threads), self._piece_workers(), getattr(self.backend, "upload_slots", 1))

    def stop(self, timeout: float = 10.0):
        with self._lock:
            was_running = self._running
            self._running = False
            self._cv.notify_all()
        if not was_running:
            return

        for t in (*self._upload_threads, self._trash_thread):
            if t and t.is_alive():
                t.join(timeout=timeout)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        if self._parity_pool is not None:
            self._parity_pool.shutdown(wait=False, cancel_futures=True)

        log.info("Uploader services stopped.")

    def run_now(self, nid):
        """Upload one file in the calling thread (used by tests and one-off tools)."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._piece_workers(), thread_name_prefix=tname("UploadPiece"))
        if self._parity_pool is None:
            self._parity_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=tname("SparePieces"))
        was = self._running
        self._running = True
        try:
            with self._lock:
                self._pending.pop(nid, None)
            self._process_upload(nid)
        finally:
            self._running = was

    def enqueue(self, nid: int, delay: float = None):
        if delay is None:
            delay = self.cfg.upload_delay
        when = time.time() + max(0.0, delay)
        with self._lock:
            self._pending[nid] = when
            self._cv.notify_all()

    def is_idle(self) -> bool:
        with self._lock:
            return len(self._pending) == 0 and len(self._active) == 0

    def wait_idle(self, timeout: float = None) -> bool:
        end = time.time() + timeout if timeout is not None else float("inf")
        with self._lock:
            while len(self._pending) > 0 or len(self._active) > 0:
                now = time.time()
                if now >= end:
                    return False
                wait_sec = min(1.0, end - now) if end != float("inf") else 1.0
                self._cv.wait(wait_sec)
            return True

    def is_reserved(self, mid) -> bool:
        with self._lock:
            return self._reserved[mid] > 0

    # ------------------------------------------------------------- upload loop
    def _upload_loop(self):
        while True:
            nid = None
            with self._lock:
                while self._running:
                    now = time.time()
                    # Find a candidate that is due and not being uploaded by another worker
                    for cand, due in self._pending.items():
                        if due <= now and cand not in self._active:
                            nid = cand
                            break
                    if nid is not None:
                        del self._pending[nid]
                        self._active.add(nid)
                        break

                    # Wait for next scheduled time or notification
                    if self._pending:
                        next_due = min(self._pending.values())
                        timeout = min(1.0, max(0.1, next_due - now))
                    else:
                        timeout = 1.0
                    self._cv.wait(timeout)

                if not self._running:
                    break

            try:
                self._process_upload(nid)
            except Exception as e:
                log.error("Unhandled error uploading node %s: %s", nid, e, exc_info=True)
            finally:
                with self._lock:
                    self._active.discard(nid)
                    self._cv.notify_all()

    def _retry_later(self, nid):
        with self._lock:
            n = self._failures.get(nid, 0) + 1
            self._failures[nid] = n
        delay = min(300.0, 10.0 * (2 ** (n - 1)))
        self.enqueue(nid, delay=delay)
        return delay

    def _check(self, nid):
        if not self._running:
            raise _Interrupted("shutting down")
        if self.fs.is_busy(nid):
            raise _Interrupted("file is being written to")
        if self.index.get(nid) is None:
            raise _Interrupted("file was deleted")

    def _process_upload(self, nid: int):
        # 1. Check if the file is still being actively written to
        if self.fs.is_busy(nid):
            log.debug("Node %s is still busy; delaying upload", nid)
            self.enqueue(nid, delay=self.cfg.upload_delay)
            return

        # 2. Lookup node in index
        node = self.index.get(nid)
        if node is None or node["is_dir"] or node["state"] == "synced":
            self._failures.pop(nid, None)
            self._drop_progress(nid)
            return

        staging_path = self.fs.staging_path(nid)
        if not os.path.exists(staging_path):
            log.warning("Staging file missing for node %s (%s)", nid, self.index.path_of(nid))
            return

        version = node["version"]
        path = self.index.path_of(nid)
        file_size = os.path.getsize(staging_path)

        # 3. Handle empty (0-byte) files
        if file_size == 0:
            if self.fs.commit_upload(nid, version, []):
                log.info("Uploaded empty file %s", path)
            return

        # 4. Upload the pieces group by group, each group followed by its spare pieces
        chunk_size = self.cfg.chunk_size
        total = (file_size + chunk_size - 1) // chunk_size
        k, _ = parity_shape(self.cfg, total, self._layout().places)
        group = k or total
        job = _Job(nid, path, node["name"], version, file_size, chunk_size, self.index.is_pinned(nid))
        job.k = k
        try:
            with open(staging_path, "rb") as f:
                job.done, job.parity = self._resume_point(nid, f, version, file_size, chunk_size, k)
            with self._lock:
                for c in job.done.values():
                    if c.get("reused"):
                        self._reserved[c["message_id"]] += 1
                        job.reserved.append(c["message_id"])
            if job.done:
                log.info("Resuming upload of %s at %d of %d pieces", path, len(job.done), total)
            else:
                log.info("Starting upload of %s (%d bytes, version %d)", path, file_size, version)
            job.base = sum(c["size"] for c in job.done.values())
            self._report(job, "uploading")
            for start in range(0, total, group):
                idxs = list(range(start, min(start + group, total)))
                self._check(nid)
                todo = [i for i in idxs if i not in job.done]
                if todo:
                    self._upload_pieces(job, staging_path, todo, k)
                gkey = str(start // group)
                if k:
                    self._one_message_per_position(job, staging_path, idxs, k)
                if k and gkey not in job.parity:
                    self._check(nid)
                    # one group's spare pieces at a time go on in the background (memory stays bounded)
                    self._settle_parity(job)
                    existing = None
                    if all(job.done[i].get("reused") for i in idxs):
                        existing = self.index.find_group([job.done[i]["message_id"] for i in idxs])
                    if existing:
                        job.parity[gkey] = existing
                        self._save_progress(nid, version, file_size, chunk_size, job.done, k, job.parity)
                    else:
                        job.pending = (gkey, self._parity_pool.submit(
                            self._upload_parity, staging_path, idxs, chunk_size,
                            [job.done[i]["sha256"] for i in idxs], [job.done[i]["message_id"] for i in idxs]))
            if job.pending:
                self._report(job, "spare pieces")
            self._settle_parity(job)
            self._report(job, "uploading")
            chunks = [job.done[i] for i in range(total)]
            self._reverify_reused(job, staging_path, chunks)
        except _Interrupted as why:
            # The pieces uploaded so far are recorded; the next attempt continues from there
            # (if the file is changed meanwhile, the record no longer matches and is discarded).
            self._settle_parity(job, quiet=True)
            self._unreserve(job)
            self.progress.pop(nid, None)
            if self.index.get(nid) is None:
                log.info("Stopped uploading %s: the file was deleted", path)
                self._drop_progress(nid)   # its uploaded pieces go to the trash
                self.fs._remove_staging(nid)  # Windows can't delete it while we had it open
                return
            if self._running:
                log.info("Upload of %s interrupted (%s); will continue later", path, why)
                self.enqueue(nid, delay=self.cfg.upload_delay)
            return
        except Exception as e:
            self._settle_parity(job, quiet=True)
            self._unreserve(job)
            self.progress.pop(nid, None)
            if self.index.get(nid) is None:
                self._drop_progress(nid)
            else:
                self.index.update(nid, state="error")
            delay = self._retry_later(nid)
            why = "this computer is out of memory" if isinstance(e, MemoryError) else str(e) or type(e).__name__
            self.stats["failed"] += 1
            self.stats["last_failed"] = time.time()
            log.error("Failed uploading %s: %s (retrying in %.0fs)", path, why, delay)
            return

        # 5. Commit freshly uploaded chunks (commit_upload trashes them if the file changed meanwhile)
        parity = {"k": k, "g": [job.parity[str(g)] for g in range((total + group - 1) // group)]} if k else None
        for c in chunks:
            c.pop("reused", None)
        try:
            ok = self.fs.commit_upload(nid, version, chunks, parity)
        finally:
            self._unreserve(job)
            self.progress.pop(nid, None)
        # only now: until the file is committed, the record is what says which pieces are already uploaded
        self._drop_progress(nid, trash=False)
        if ok:
            self._failures.pop(nid, None)
            self.stats["files"] += 1
            self.stats["file_bytes"] += file_size
            self.stats["last_done"] = time.time()
            if self.journal is not None:
                self.journal.wake()  # publish the new file to other devices right away
            log.info("Successfully uploaded %s (%d bytes across %d chunk(s)%s)", path, file_size, len(chunks),
                     f", {sum(len(g[1]) for g in parity['g'])} spare" if parity else "")
        elif self.index.get(nid) is not None:
            log.info("%s changed during upload; re-queued", path)
            self.enqueue(nid, delay=self.cfg.upload_delay)

    def _settle_parity(self, job, quiet=False):
        """Wait for the group whose spare pieces are being made in the background, and record them. With
        `quiet` (the upload stopped anyway), a failure there is only logged."""
        pending, job.pending = job.pending, None
        if not pending:
            return
        gkey, fut = pending
        try:
            job.parity[gkey] = fut.result()
        except Exception as e:
            if not quiet:
                raise
            log.debug("Spare pieces of %s, group %s: %s", job.path, gkey, e)
            return
        self._save_progress(job.nid, job.version, job.size, job.chunk_size, job.done, job.k, job.parity)

    def _report(self, job, stage):
        """Live progress of one upload (status page, dashboard, log tab)."""
        done = sum(c["size"] for c in job.done.values())
        self.progress[job.nid] = {
            "path": job.path, "size": job.size, "bytes": done, "base": job.base, "started": job.started,
            "done": len(job.done), "total": (job.size + job.chunk_size - 1) // job.chunk_size,
            "stage": stage, "updated": time.time()}

    def queue_info(self):
        """Files waiting to upload (not started yet): [{path, size, due}]."""
        with self._lock:
            pending = sorted(self._pending.items(), key=lambda kv: kv[1])
        out = []
        for nid, due in pending[:200]:
            node = self.index.get(nid)
            if node is not None:
                out.append({"path": self.index.path_of(nid), "size": node["size"], "due": due})
        return out

    def _unreserve(self, job):
        with self._lock:
            for mid in job.reserved:
                self._reserved[mid] -= 1
                if self._reserved[mid] <= 0:
                    del self._reserved[mid]
            job.reserved = []

    # ------------------------------------------------------------- pieces
    def _upload_pieces(self, job, path, idxs, k):
        """Upload pieces `idxs` in parallel; record each as it finishes."""
        futures = {self._pool.submit(self._upload_piece, job, path, i): i for i in idxs}
        error = None
        try:
            pending = set(futures)
            while pending:
                finished, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in finished:
                    try:
                        c = fut.result()
                    except Exception as e:
                        error = error or e
                        continue
                    job.done[c["idx"]] = c
                    if time.time() - job.saved_at >= PROGRESS_EVERY:      # rewriting it is not free on big files
                        job.saved_at = time.time()
                        self._save_progress(job.nid, job.version, job.size, job.chunk_size, job.done, k, job.parity)
                    self._report(job, "uploading")
                if error is None and pending:
                    try:
                        self._check(job.nid)
                    except _Interrupted as why:
                        error = why
                if error is not None:
                    for fut in pending:
                        fut.cancel()
        finally:
            # Record whatever still finishes, so nothing uploaded is forgotten.
            for fut, i in futures.items():
                if fut.cancelled() or i in job.done:
                    continue
                try:
                    c = fut.result()
                except Exception:
                    continue
                job.done[c["idx"]] = c
            self._save_progress(job.nid, job.version, job.size, job.chunk_size, job.done, k, job.parity)
        if error is not None:
            raise error
        total = (job.size + job.chunk_size - 1) // job.chunk_size
        done = len(job.done)
        if total > 1 and done < total and time.time() - job.last_report >= 30:
            job.last_report = time.time()
            log.info("Uploading %s: %d of %d pieces (%d%%)", job.path, done, total, done * 100 // total)

    def _read_piece(self, path, i, chunk_size):
        with open(path, "rb") as f:
            f.seek(i * chunk_size)
            return f.read(chunk_size)

    def _upload_piece(self, job, path, i, allow_reuse=True):
        if not self._running:
            raise _Interrupted("shutting down")
        data = self._read_piece(path, i, job.chunk_size)
        sha256 = hashlib.sha256(data).hexdigest()
        reused = self._reuse(sha256, len(data)) if allow_reuse else None
        if reused is not None:
            with self._lock:
                self._reserved[reused["message_id"]] += 1
                job.reserved.append(reused["message_id"])
                self.stats["reused_bytes"] += len(data)
            return {"idx": i, "size": len(data), "sha256": sha256, "reused": time.time(), **reused}
        return self._store_piece(job, i, data, sha256, self._channel_for(i, getattr(job, "k", 0)))

    def _layout(self):
        return layout_of(self.backend)

    def _put(self, filename, payload, content, channel):
        if channel is None:
            return self.backend.upload(filename, payload, content=content)
        return self.backend.upload(filename, payload, content=content, channel=channel)

    def _channel_for(self, i, k):
        """Where data piece i goes: the places of a group take turns over the servers and channels."""
        order = self._layout().order()
        return order[(i % k if k else i) % len(order)] if len(order) > 1 else None

    def _store_piece(self, job, i, data, sha256, channel=None):
        if self.crypto:
            payload, packed = codec.encode(data, self.crypto, self._compress())
            # Discord sees neither the original name nor the extension
            filename = f"chk_{secrets.token_hex(8)}.bin"
            content = ""
        else:
            payload, packed = codec.encode(data, None, self._compress())
            filename = f"{job.name.replace(' ', '_')}.part{i:04d}"
            content = (
                f"DDrive chunk #{i} for `{job.name}`\n"
                f"Size: {len(data)} bytes | Hash: {sha256[:16]}"
            )
        stored = self._put(filename, payload, content, channel)
        self._verify_upload(stored.message_id, data)
        with self._lock:
            self.stats["uploaded_bytes"] += len(payload)
            if packed:
                self.stats["compressed_saved"] += max(0, len(data) - len(payload))
        if job.pinned:
            # Offline-pinned: keep a local copy so it never has to be downloaded again
            try:
                self.fs.cache.put(stored.message_id, data, persist=True)
            except Exception as ce:
                log.debug("Could not cache chunk of pinned node %s: %s", job.nid, ce)
        return {"idx": i, "message_id": stored.message_id, "attachment_id": stored.attachment_id,
                "url": stored.url, "size": len(data), "sha256": sha256}

    def _verify_upload(self, mid, data):
        """With `verify_uploads`: read a piece back right after uploading it, and compare."""
        if not getattr(self.cfg, "verify_uploads", False):
            return
        try:
            payload, _ = self.backend.download(mid, None)
            ok = codec.decode(payload, self.crypto, None) == data
        except Exception as e:
            ok, why = False, e
        else:
            why = "it reads back differently"
        if not ok:
            try:
                self.backend.delete(mid)
            except Exception:
                pass
            raise RuntimeError(f"an uploaded piece could not be read back ({why})")

    def _one_message_per_position(self, job, path, idxs, k):
        """A group survives the loss of any m messages only if no message fills two of its positions.
        De-duplication can reuse one stored piece for repeated content (runs of zeros in a disk image):
        each repeat after the first gets its own message."""
        lay = self._layout()
        _, m = parity_shape(self.cfg, len(idxs), lay.places)
        seen, held = set(), []
        for i in idxs:
            c = job.done[i]
            # With spare channels, a reused piece can also put more of the group in one channel (or server)
            # than the group can lose: it is uploaded again where the group has fewest.
            crowded = len(lay.chans) > 1 and lay.worst(held + [c["message_id"]]) > m
            if c["message_id"] in seen or crowded:
                data = self._read_piece(path, i, job.chunk_size)
                if hashlib.sha256(data).hexdigest() != c["sha256"]:
                    raise _Interrupted("file changed during upload")
                chan = lay.fill(held, 1)[0] if len(lay.chans) > 1 else None
                job.done[i] = self._store_piece(job, i, data, c["sha256"], chan)
                self._save_progress(job.nid, job.version, job.size, job.chunk_size, job.done, k, job.parity)
            seen.add(job.done[i]["message_id"])
            held.append(job.done[i]["message_id"])

    def _compress(self):
        return bool(getattr(self.cfg, "compression", True))

    def _reuse(self, sha256, size):
        """An identical piece that is already stored (and still on Discord), or None."""
        if not getattr(self.cfg, "dedup", True):
            return None
        mid = self.index.find_dedup(sha256, size)
        if mid is None:
            return None
        try:
            info = self.backend.message_info(mid)
        except Exception as e:
            log.debug("Could not check stored piece %s for reuse: %s", mid, e)
            return None
        if info is None:
            return None
        if self.crypto and not (info.get("filename") or "").startswith("chk_"):
            return None      # stored before encryption was turned on: don't reuse plaintext
        return {"message_id": mid, "attachment_id": info.get("attachment_id"), "url": info.get("url")}

    def _reverify_reused(self, job, path, chunks):
        """A piece reused long ago may have been deleted since (its other file deleted on another
        device); upload any such piece again before publishing the file."""
        now = time.time()
        for c in chunks:
            if not c.get("reused") or now - c["reused"] < REVERIFY_AFTER:
                continue
            if self.backend.message_info(c["message_id"]) is not None:
                continue
            log.info("A reused piece of %s is gone from Discord; uploading it again", job.path)
            fresh = self._upload_piece(job, path, c["idx"], allow_reuse=False)
            c.clear()
            c.update(fresh)

    # -------------------------------------------------------- spare pieces
    def _upload_parity(self, path, idxs, chunk_size, shas=None, mids=()):
        """Compute and upload the spare pieces of one group. Returns [gid, [[mid, size, sha], ...]].
        `shas`: the checksums of the pieces as uploaded; spare pieces computed from anything else
        could never rebuild them. `mids`: where the data pieces are (the spare pieces fill up the
        channels holding fewest)."""
        lay = self._layout()
        _, m = parity_shape(self.cfg, len(idxs), lay.places)
        where = lay.fill(list(mids), m) if len(lay.chans) > 1 else [None] * m
        out = []
        for first in range(0, m, PARITY_BATCH):
            rows = list(range(first, min(m, first + PARITY_BATCH)))
            enc = rs.Encoder(len(idxs), m, rows)
            with open(path, "rb") as f:
                for pos, i in enumerate(idxs):
                    f.seek(i * chunk_size)
                    data = f.read(chunk_size)
                    if shas and hashlib.sha256(data).hexdigest() != shas[pos]:
                        raise _Interrupted("file changed during upload")
                    enc.add(pos, data)
            shards = enc.parity()
            del enc, data
            futures = [self._pool.submit(self.upload_blob, s, where[j]) for j, s in zip(rows, shards)]
            del shards
            out += [fut.result() for fut in futures]
        return [secrets.token_hex(8), out]

    def upload_blob(self, data, channel=None):
        """Upload a spare (or repaired) piece. Returns [message_id, size, sha256]."""
        payload, _ = codec.encode(data, self.crypto, self._compress())
        stored = self._put(f"chk_{secrets.token_hex(8)}.bin", payload, "", channel)
        self._verify_upload(stored.message_id, data)
        with self._lock:
            self.stats["uploaded_bytes"] += len(payload)
            self.stats["spare_bytes"] += len(payload)
        return [stored.message_id, len(data), hashlib.sha256(data).hexdigest()]

    def _resume_point(self, nid, f, version, file_size, chunk_size, k):
        """Pieces (and spare pieces) already uploaded for exactly this content, checked against the local file."""
        rec = self._load_progress(nid)
        if not rec:
            return {}, {}
        if rec.get("v") != version or rec.get("size") != file_size or rec.get("chunk_size") != chunk_size:
            self._drop_progress(nid)
            return {}, {}
        good, stale = {}, []
        for c in rec.get("chunks") or []:
            f.seek(c["idx"] * chunk_size)
            data = f.read(chunk_size)
            if len(data) == c["size"] and hashlib.sha256(data).hexdigest() == c["sha256"]:
                good[c["idx"]] = c
            else:
                stale.append(c["message_id"])
        parity = rec.get("parity") or {}
        if rec.get("k", 0) != k:
            for _, shards in parity.values():
                stale += [s[0] for s in shards]
            parity = {}
        elif k:
            # a group with a piece that has to be uploaded again: its spare pieces were computed from
            # the old content and could never rebuild the new one
            redo = {str(c["idx"] // k) for c in rec.get("chunks") or [] if c["idx"] not in good}
            for gkey in redo & set(parity):
                stale += [s[0] for s in parity.pop(gkey)[1]]
        if stale:
            self.index.release(stale)
        return good, parity

    # -------------------------------------------------------- backup & trash
    def _trash_loop(self):
        while self._running:
            try:
                time.sleep(15.0)
                if not self._running:
                    break
                self.clean_trash(limit=5)
            except Exception as e:
                log.error("Error during trash cleanup: %s", e)

    def clean_trash(self, grace=None, limit=10):
        """Delete released pieces from Discord (after `delete_after_days`, and only if nothing uses them now)."""
        if not self.cfg.delete_remote:
            return 0
        if grace is None:
            grace = max(0.0, float(getattr(self.cfg, "delete_after_days", 7.0) or 0)) * 86400
        deleted = failed = 0
        for mid, released in self.index.trash_batch(limit, grace=grace, with_time=True):
            if not self._running:
                break
            if self.index.is_referenced(mid) or self.is_reserved(mid):
                self.index.trash_remove(mid)      # used again (e.g. by a new identical file)
                continue
            try:
                self.backend.delete(mid)
                self.index.trash_remove(mid)
                deleted += 1
            except Exception as e:
                # this one (its channel refused, say) is tried again later, behind the others: one piece that
                # can't be deleted must not hold up all the rest
                log.debug("Failed deleting trashed message %s: %s", mid, e)
                self.index.trash_later(mid)
                failed += 1
                if failed >= 3:
                    break  # Discord itself is unreachable: back off until next round
                continue
            log_deletion(self.cfg, mid, released)
        return deleted


DELETIONS_LOG = "deletions.log"


def log_deletion(cfg, mid, released):
    """Note a piece this device deleted from Discord, for good (the file is never trimmed: a line is
    about 80 bytes). A piece found missing later is then known to be ours, or Discord's (`health --why`)."""
    try:
        path = os.path.join(cfg.resolved_data_dir, DELETIONS_LOG)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(released)) if released else "-"
        with open(path, "a", encoding="utf-8") as f:
            f.write("\t".join([stamp, "deleted", str(mid), f"released {when}", f"device {cfg.device_id or '-'}"]) + "\n")
    except Exception as e:
        log.debug("Could not note the deletion of %s: %s", mid, e)


def find_deletion(cfg, mid):
    """The deletion log's line about piece `mid`, or None if this device never deleted it."""
    try:
        with open(os.path.join(cfg.resolved_data_dir, DELETIONS_LOG), encoding="utf-8") as f:
            for line in f:
                if line.split("\t")[2:3] == [str(mid)]:
                    return line.rstrip("\n")
    except OSError:
        pass
    return None


class _Job:
    """One file being uploaded."""

    def __init__(self, nid, path, name, version, size, chunk_size, pinned):
        self.nid, self.path, self.name, self.version = nid, path, name, version
        self.size, self.chunk_size, self.pinned = size, chunk_size, pinned
        self.k = 0             # data pieces per group (0: no spare pieces)
        self.pending = None    # (group number, future) of spare pieces being made in the background
        self.saved_at = 0.0    # when its progress was last saved
        self.done = {}         # idx -> chunk dict
        self.parity = {}       # group number (str) -> [gid, [[mid, size, sha], ...]]
        self.reserved = []
        self.last_report = time.time()
        self.started = time.time()
        self.base = 0
