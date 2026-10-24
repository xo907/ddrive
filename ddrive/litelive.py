"""Live upload into an end-to-end encrypted account, through DDrive Lite (`lite recorder`).

A recorder on this computer (the Discord call recorder, say) sends a file a few seconds at a time while
it makes it, the same way it would to a drive (liverec.py: open -> data at an offset, again and again ->
close), but to Lite, here, signed in with the recorder key Lite made. The host can't seal anything for
an end-to-end encrypted account, so Lite does: what arrives is sealed here a piece (1 MiB) at a time,
as vault.py seals any file, and the sealed bytes go to the host as one object that grows (the host takes
it in parts without knowing its size: web.py, ?grow=1) until the recording ends. Then the last piece is
sealed as the last one, and the file is added to the account's list with its size.

Nothing goes to this computer's disk: what isn't on the host yet is held in memory (a piece being
filled, and sealed bytes on their way). A recorder that stops sending (Discord closed) is finished
with what it sent after a while, like a drive does. If Lite itself is stopped halfway, the recording
is lost: the account only gets a file when it is finished.
"""

import hmac
import json
import logging
import os
import posixpath
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import vault
from .liverec import IDLE_CLOSE, MAX_OPEN, LiveRecordingError, clean_name

log = logging.getLogger("ddrive.litelive")

PORT = 8766                       # Lite's recorder listens here, on this computer only
PIECE = 1 << vault.CHUNK_LOG      # sealed a piece of this many bytes at a time
SEND_AT = vault.PART              # sealed bytes go to the host in parts of this size
HELD_MAX = 6 * vault.PART         # more than this not on the host yet: the recorder is asked to wait
PART_MAX = 16 * 1024 * 1024       # the most one request of the recorder may send
GIVE_UP = 30 * 60                 # the host can't be reached for this long: the recording is given up
KEEP_FROM = 4096                  # smaller recordings are only a file's beginning (a recorder that stalled): not kept


class _Rec:
    def __init__(self, rid, folder, name):
        self.id, self.folder, self.name = rid, folder, name
        self.key, self.oid = os.urandom(32), vault.new_id()
        self.lock = threading.Condition()
        self.plain = bytearray()          # what arrived after the last sealed piece
        self.n = 0                        # number of the next piece
        self.sealed = bytearray(vault.MAGIC + bytes([vault.CHUNK_LOG]))     # sealed, not on the host yet
        self.sent = 0                     # bytes of the object the host has
        self.offset = 0                   # bytes of the recording received
        self.started = self.seen = time.time()
        self.closing = False
        self.last_sealed = False
        self.failed = None
        self.path = posixpath.join(folder, name)
        self.done = threading.Event()


class VaultRecordings:
    """The recordings being sealed into one account right now."""

    def __init__(self, open_vault, folder_of=None):
        self._open_vault = open_vault     # () -> vault.Vault of the account (its list read)
        self.folder_of = folder_of or (lambda: "/Recordings")
        self._v = None
        self._vlock = threading.Lock()
        self._lock = threading.Lock()
        self._recs = {}
        self._stop = threading.Event()
        threading.Thread(target=self._reap, name="LiteRecordings", daemon=True).start()

    def _vault(self):
        """The account, its list brought up to date (holds _vlock)."""
        if self._v is None:
            self._v = self._open_vault()
        else:
            self._v.sync()
        return self._v

    @staticmethod
    def _makedirs(v, path):
        here = "/"
        for part in [p for p in path.split("/") if p]:
            here = posixpath.join(here, part)
            node = v.resolve(here)
            if node is None:
                v.mkdir(here)
            elif not node["dir"]:
                raise LiveRecordingError(409, f"{here} is a file, not a folder")

    @staticmethod
    def _free(v, folder, name):
        stem, ext = posixpath.splitext(name)
        n, final = 1, name
        while v.resolve(posixpath.join(folder, final)) is not None:
            n += 1
            final = f"{stem} ({n}){ext}"
        return final

    # ----------------------------------------------------------- the three steps (as liverec.py)
    def open(self, folder, name, mime=""):
        if self._stop.is_set():
            raise LiveRecordingError(503, "Lite is stopping.")
        with self._lock:
            if sum(1 for r in self._recs.values() if not r.closing) >= MAX_OPEN:
                raise LiveRecordingError(429, f"{MAX_OPEN} recordings are being written already.")
        base = "/" + "/".join(clean_name(p, "Recordings") for p in self.folder_of().split("/") if p.strip())
        parts = [clean_name(p) for p in str(folder or "").replace("\\", "/").split("/") if p.strip()]
        where = posixpath.join(base, *parts) if parts else base
        try:
            with self._vlock:
                v = self._vault()
                self._makedirs(v, where)
                name = self._free(v, where, clean_name(name))
        except vault.VaultError as e:
            raise LiveRecordingError(409, str(e)) from None
        except Exception as e:
            raise LiveRecordingError(503, f"The host can't be reached: {e}") from None
        rec = _Rec(secrets.token_urlsafe(18), where, name)
        with self._lock:
            self._recs[rec.id] = rec
        threading.Thread(target=self._send, args=(rec,), name="LiteRec-send", daemon=True).start()
        log.info("Recording into %s (end-to-end encrypted)", rec.path)
        return {"id": rec.id, "path": rec.path}

    def data(self, rid, offset, body):
        rec = self._get(rid)
        with rec.lock:
            rec.seen = time.time()
            if rec.failed:
                raise LiveRecordingError(410, rec.failed)
            if rec.closing:
                raise LiveRecordingError(409, "This recording is finished.")
            if offset > rec.offset:
                return {"ok": False, "offset": rec.offset}
            if len(rec.sealed) + len(rec.plain) > HELD_MAX:
                return {"ok": False, "offset": rec.offset, "wait": "the host is slower than the recording"}
            new = body[rec.offset - offset:]
            rec.plain += new
            rec.offset += len(new)
            # A whole piece is sealed once more has arrived after it: only then is it sure not to be the last.
            while len(rec.plain) > PIECE:
                rec.sealed += vault.seal(rec.key, bytes(rec.plain[:PIECE]), vault._label(rec.n, False))
                del rec.plain[:PIECE]
                rec.n += 1
            if len(rec.sealed) >= SEND_AT:
                rec.lock.notify_all()
            return {"ok": True, "offset": rec.offset}

    def close(self, rid, offset=None, wait=0.0):
        rec = self._get(rid)
        with rec.lock:
            if offset is not None and offset != rec.offset and not rec.failed:
                return {"ok": False, "offset": rec.offset}
            if not rec.closing:
                rec.closing = True
                if rec.offset >= KEEP_FROM:
                    rec.sealed += vault.seal(rec.key, bytes(rec.plain), vault._label(rec.n, True))
                    rec.plain.clear()
                    rec.last_sealed = True
                rec.lock.notify_all()
        if wait:
            rec.done.wait(wait)
        return {"ok": not rec.failed, "path": rec.path, "size": rec.offset, "done": rec.done.is_set(), "error": rec.failed}

    def status(self):
        with self._lock:
            recs = list(self._recs.values())
        return [{"path": r.path, "bytes": r.offset, "started": r.started, "seen": r.seen, "uploaded": r.sent,
                 "closing": r.closing, "error": r.failed} for r in recs]

    def stop(self, timeout=60.0):
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
    def _get(self, rid):
        with self._lock:
            rec = self._recs.get(str(rid or ""))
        if rec is None:
            raise LiveRecordingError(404, "No such recording (Lite may have restarted): start a new file.")
        return rec

    def _send(self, rec):
        """Sends the sealed bytes to the host as they come, and adds the file when the last are there."""
        trouble = None                    # since when the host can't be reached
        try:
            while True:
                with rec.lock:
                    while not (len(rec.sealed) >= SEND_AT or rec.closing):
                        rec.lock.wait(5)
                    if rec.closing and not rec.last_sealed:          # nothing (worth keeping) was recorded
                        if rec.offset:
                            log.info("Recording %s: only %d bytes arrived, not kept", rec.path, rec.offset)
                        break
                    end = rec.closing and len(rec.sealed) <= SEND_AT
                    part = bytes(rec.sealed[:SEND_AT])
                    at = rec.sent
                if not part and not end:
                    continue
                try:
                    got = json.loads(self._v.client.request(
                        "POST", f"/api/vault/object?id={rec.oid}&offset={at}&grow=1" + ("&end=1" if end else ""), part,
                        {"Content-Type": "application/octet-stream"}))
                except Exception as e:
                    trouble = trouble or time.time()
                    if time.time() - trouble > GIVE_UP:
                        raise RuntimeError(f"the host couldn't be reached for {GIVE_UP // 60} minutes ({e})") from None
                    log.warning("Recording %s: the host didn't take a part (%s); trying again", rec.path, e)
                    time.sleep(10)
                    continue
                trouble = None
                if got.get("wait"):
                    time.sleep(3)
                    continue
                have = int(got.get("offset") or 0)
                if not got.get("ok") and have < at:
                    raise RuntimeError("the host lost what it had of the recording")
                with rec.lock:
                    del rec.sealed[:max(0, have - rec.sent)]
                    rec.sent = max(rec.sent, have)
                if got.get("done"):
                    with self._vlock:
                        v = self._vault()
                        rec.name = self._free(v, rec.folder, rec.name)
                        rec.path = posixpath.join(rec.folder, rec.name)
                        v.put_object(rec.path, rec.oid, rec.key, rec.offset, time.time())
                    break
        except Exception as e:
            rec.failed = f"It couldn't be saved: {e}"
            log.error("Recording %s stopped: %s", rec.path, e)
        finally:
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


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DDriveLite"

    def log_message(self, fmt, *args):
        log.debug("recorder: " + fmt, *args)

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self._json(405, {"error": "POST only"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > PART_MAX:
            self.close_connection = True
            return self._json(413, {"error": "that part is too big"})
        body = self.rfile.read(n) if n else b""
        key = self.server.key_of()
        scheme, _, given = (self.headers.get("Authorization") or "").partition(" ")
        if not key or scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), key.encode()):
            return self._json(401, {"error": "Wrong recorder key." if key else "Live upload is off: run 'lite recorder on'."})
        url = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        recs = self.server.recordings
        try:
            if url.path == "/api/rec/open":
                b = json.loads(body or b"{}")
                return self._json(200, recs.open(b.get("folder"), b.get("name"), b.get("mime", "")))
            offset = int(q["offset"]) if q.get("offset") not in (None, "") else None
            if url.path == "/api/rec/data":
                if len(body) < n or offset is None or offset < 0:
                    return self._json(400, {"error": "bad offset"})
                return self._json(200, recs.data(q.get("id"), offset, body))
            if url.path == "/api/rec/close":
                return self._json(200, recs.close(q.get("id"), offset, min(float(q.get("wait") or 0), 60.0)))
            return self._json(404, {"error": "not found"})
        except LiveRecordingError as e:
            return self._json(e.status, {"error": str(e)})
        except ValueError:
            return self._json(400, {"error": "bad request"})


class RecorderServer:
    """Lite's recorder endpoint on this computer (127.0.0.1 only): /api/rec/open, data and close."""

    def __init__(self, recordings, key_of, port=PORT):
        self.recordings = recordings
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.recordings, self.httpd.key_of = recordings, key_of
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="LiteRecorder", daemon=True)

    def start(self):
        self._thread.start()
        log.info("Live upload (recorder) on http://127.0.0.1:%d", self.port)
        return self

    def stop(self):
        self.recordings.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
