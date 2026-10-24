"""Live upload: files streamed into the drive while they are being made (a recording, a log, a capture).

A program with the recorder key (Settings, Live upload) sends a file a few seconds at a time while it
is being made. It never touches a disk: what arrives is held in memory until it makes a whole piece
(chunk_size), and that piece goes straight to Discord, encrypted like every other piece. The file is on the drive from its first piece and
grows as the recording goes on (the list of its pieces is saved now and then, and at the end), so a
recording that is cut off (the computer, the recorder or the connection gives up) keeps what was
uploaded. Spare pieces are added afterwards by the background check, as for any file without them.

The recorder sends:  open (a new file)  ->  data at an offset, again and again  ->  close.
Each step says where it is (the offset), so a part that was sent twice, or that went missing, is
noticed: a repeat is skipped, and after a gap the recorder is told where to go on from.
"""

import hashlib
import logging
import posixpath
import re
import secrets
import threading
import time

from .uploader import _Job

log = logging.getLogger("ddrive.liverec")

MAX_UPLOADING = 2          # pieces of one recording on their way to Discord at once; more: the recorder waits
SAVE_EVERY = 120.0         # seconds between saves of a recording's list of pieces (each save is published)
IDLE_CLOSE = 180.0         # a recording nobody sent anything to for this long is finished with what it has
MAX_OPEN = 12              # recordings open at once
TRIES = 6                  # tries per piece before the recording is given up as failed


def clean_name(name, fallback="Recording"):
    """A file or folder name from what a recorder says (no slashes, nothing a drive can't hold)."""
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(name or "")).strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or fallback


class LiveRecordingError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class _Rec:
    def __init__(self, rid, nid, path, version, mime):
        self.id, self.nid, self.path, self.version, self.mime = rid, nid, path, version, mime
        self.lock = threading.Lock()
        self.buf = bytearray()          # what arrived after the last whole piece
        self.offset = 0                 # bytes received (and taken) so far
        self.next_idx = 0               # number of the next piece
        self.chunks = {}                # idx -> uploaded piece
        self.uploading = 0
        self.saved = 0                  # pieces in the list saved last
        self.saved_at = time.time()
        self.seen = time.time()
        self.started = time.time()
        self.closing = False
        self.failed = None              # why it can't go on
        self.done = threading.Event()   # set when it is finished


class LiveRecordings:
    """The recordings being written into one drive right now."""

    def __init__(self, drive, folder_of=None):
        self.drive = drive
        self.folder_of = folder_of or (lambda: "/Recordings")
        self._lock = threading.Lock()
        self._recs = {}
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap, name="LiveRecordings", daemon=True)
        self._reaper.start()

    # ----------------------------------------------------------- the three steps
    def open(self, folder, name, mime=""):
        """Make a new (empty) file for a recording: {id, path}. `folder` is below the recordings folder."""
        if self._stop.is_set():
            raise LiveRecordingError(503, "The drive is stopping.")
        d = self.drive
        if d.uploader is None or d.backend is None:
            raise LiveRecordingError(503, "This drive can't upload right now.")
        with self._lock:
            if sum(1 for r in self._recs.values() if not r.closing) >= MAX_OPEN:
                raise LiveRecordingError(429, f"{MAX_OPEN} recordings are being written already.")
        base = "/" + "/".join(clean_name(p, "Recordings") for p in self.folder_of().split("/") if p.strip())
        parts = [clean_name(p) for p in str(folder or "").replace("\\", "/").split("/") if p.strip()]
        where = posixpath.join(base, *parts) if parts else base
        d.fs._refuse_hidden(where)
        parent = d.index.makedirs(where)
        name = clean_name(name)
        stem, ext = posixpath.splitext(name)
        n, final = 1, name
        while d.index.lookup(parent["id"], final) is not None:
            n += 1
            final = f"{stem} ({n}){ext}"
        node = d.index.create_node(parent["id"], final, False, state="synced")
        rec = _Rec(secrets.token_urlsafe(18), node["id"], posixpath.join(where, final), node["version"], str(mime)[:100])
        with self._lock:
            self._recs[rec.id] = rec
        log.info("Recording into %s", rec.path)
        return {"id": rec.id, "path": rec.path}

    def data(self, rid, offset, body):
        """Take what was sent from `offset` on: {ok, offset} (how much is there now), or ok False with
        the offset to go on from, and `wait` when the recorder should send it again in a moment."""
        rec = self._get(rid)
        with rec.lock:
            rec.seen = time.time()
            if rec.failed:
                raise LiveRecordingError(410, rec.failed)
            if rec.closing:
                raise LiveRecordingError(409, "This recording is finished.")
            if offset > rec.offset:
                return {"ok": False, "offset": rec.offset}
            if rec.uploading >= MAX_UPLOADING and len(rec.buf) + len(body) >= self._piece():
                return {"ok": False, "offset": rec.offset, "wait": "Discord is slower than the recording"}
            new = body[rec.offset - offset:]
            rec.buf += new
            rec.offset += len(new)
            self._cut(rec)
            return {"ok": True, "offset": rec.offset}

    def close(self, rid, offset=None, wait=0.0):
        """No more data: the last piece is sent and the file is finished. With `wait`, waits up to that
        many seconds for it. {ok, path, size, done}."""
        rec = self._get(rid)
        with rec.lock:
            if offset is not None and offset != rec.offset and not rec.failed:
                return {"ok": False, "offset": rec.offset}
            first = not rec.closing
            rec.closing = True
            if first:
                self._cut(rec, last=True)
                self._settle(rec)
        if wait:
            rec.done.wait(wait)
        return {"ok": not rec.failed, "path": rec.path, "size": rec.offset, "done": rec.done.is_set(),
                "error": rec.failed}

    def status(self):
        """What is being recorded right now (for the dashboard)."""
        with self._lock:
            recs = list(self._recs.values())
        return [{"path": r.path, "bytes": r.offset, "started": r.started, "seen": r.seen,
                 "uploaded": sum(c["size"] for c in r.chunks.values()), "closing": r.closing, "error": r.failed}
                for r in recs]

    def stop(self, timeout=60.0):
        """The drive stops: every recording is finished with what it has (its last piece is sent)."""
        self._stop.set()
        with self._lock:
            recs = list(self._recs.values())
        for r in recs:
            try:
                self.close(r.id)
            except LiveRecordingError:
                pass
        end = time.time() + timeout
        for r in recs:
            r.done.wait(max(0.0, end - time.time()))

    # ----------------------------------------------------------- inside
    def _piece(self):
        return int(self.drive.cfg.chunk_size)

    def _get(self, rid):
        with self._lock:
            rec = self._recs.get(str(rid or ""))
        if rec is None:
            raise LiveRecordingError(404, "No such recording (the drive may have restarted): start a new file.")
        return rec

    def _cut(self, rec, last=False):
        """Send every whole piece in the buffer (and with `last`, what is left). Holds rec.lock."""
        size = self._piece()
        while len(rec.buf) >= size or (last and rec.buf):
            data = bytes(rec.buf[:size])
            del rec.buf[:size]
            idx = rec.next_idx
            rec.next_idx += 1
            rec.uploading += 1
            threading.Thread(target=self._send, args=(rec, idx, data), name=f"LiveRec-{idx}", daemon=True).start()

    def _send(self, rec, idx, data):
        up = self.drive.uploader
        sha = hashlib.sha256(data).hexdigest()
        job = _Job(rec.nid, rec.path, posixpath.basename(rec.path), rec.version, 0, self._piece(), False)
        chunk, why = None, ""
        for attempt in range(TRIES):
            if rec.failed:
                break
            try:
                chunk = up._store_piece(job, idx, data, sha, up._channel_for(idx, 0))
                break
            except Exception as e:
                why = str(e) or type(e).__name__
                log.warning("Recording %s: piece %d didn't upload (%s); trying again", rec.path, idx, why)
                time.sleep(min(60, 5 * 2 ** attempt))
        with rec.lock:
            rec.uploading -= 1
            if chunk is None:
                rec.failed = rec.failed or f"Pieces of it couldn't be uploaded: {why}"
                log.error("Recording %s stopped: %s", rec.path, rec.failed)
            else:
                rec.chunks[idx] = chunk
                if time.time() - rec.saved_at >= SAVE_EVERY or rec.saved == 0:
                    self._save(rec)
            self._settle(rec)

    def _ready(self, rec):
        """The pieces uploaded so far that have no gap before them."""
        out = []
        while len(out) in rec.chunks:
            out.append(rec.chunks[len(out)])
        return out

    def _save(self, rec):
        """Save (and publish) the list of the pieces that are uploaded. Holds rec.lock."""
        ready = self._ready(rec)
        if len(ready) <= rec.saved:
            return
        idx = self.drive.index
        if idx.get(rec.nid) is None:
            rec.failed = rec.failed or "The file was deleted while it was being recorded."
            idx.release([c["message_id"] for c in rec.chunks.values()])
            return
        if idx.replace_chunks(rec.nid, [dict(c, idx=i) for i, c in enumerate(ready)], rec.version, append=True):
            rec.saved, rec.saved_at = len(ready), time.time()
            j = self.drive.journal
            if j is not None:
                j.wake()
        else:
            rec.failed = rec.failed or "The file was changed elsewhere while it was being recorded."

    def _settle(self, rec):
        """A closing recording whose last piece is in: save it and let it go. Holds rec.lock."""
        if not rec.closing or rec.uploading or rec.done.is_set():
            return
        if rec.chunks:
            self._save(rec)
        idx = self.drive.index
        if not rec.chunks and idx.get(rec.nid) is not None and not idx.get_chunks(rec.nid):
            try:
                idx.purge_node(rec.nid)          # nothing was recorded: no empty file is left
            except Exception as e:
                log.debug("Could not remove empty recording %s: %s", rec.path, e)
        else:
            try:
                idx.touch(rec.nid, time.time(), time.time())
            except Exception:
                pass
        rec.done.set()
        with self._lock:
            self._recs.pop(rec.id, None)
        log.info("Recording %s finished: %d bytes%s", rec.path, rec.offset, f" ({rec.failed})" if rec.failed else "")

    def _reap(self):
        while not self._stop.wait(15):
            now = time.time()
            with self._lock:
                idle = [r for r in self._recs.values() if not r.closing and now - r.seen > IDLE_CLOSE]
            for r in idle:
                log.info("Recording %s: nothing arrived for %d s, finishing it", r.path, IDLE_CLOSE)
                try:
                    self.close(r.id)
                except LiveRecordingError:
                    pass
