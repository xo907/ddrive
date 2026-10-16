"""What a status bot may know about the drive: /api/monitor.

Numbers and states only: no file or folder names, no account ids, no log text. A bot that shows it in
a Discord channel or in its status can't give away what is on the drive. It is read with the monitor key
(`monitor-key`), not with a sign-in, so the key opens nothing else: not the files, not Settings.
"""

import hmac
import os
import re
import shutil
import socket
import sys
import threading
import time

from . import __version__, resources

VERSION = 1

# What a warning or error in the log is about. The first pattern that matches wins.
CATEGORIES = (
    ("upload", re.compile(r"Failed uploading|Unhandled error uploading|upload", re.I)),
    ("disk", re.compile(r"No space|ENOSPC|disk|Errno 28|Errno 5\b|I/O error|Input/output|Permission denied|read-only", re.I)),
    ("ratelimit", re.compile(r"rate.?limit|\b429\b|slow(ing)? down|too many requests", re.I)),
    ("discord", re.compile(r"\b40[13]\b|refused|Missing Access|Missing Permissions|Unknown Channel|token", re.I)),
    ("network", re.compile(r"timed? ?out|timeout|connection|network|unreachable|resolve|SSL|TLS|curl|reset by peer|"
                           r"\b50[0234]\b|temporarily", re.I)),
    ("download", re.compile(r"download|read(ing)? (piece|chunk)|decrypt", re.I)),
    ("repair", re.compile(r"repair|rebuil|lost|spare|parity|scrub|check", re.I)),
    ("sync", re.compile(r"journal|publish|sync|other devices", re.I)),
)
WINDOWS = (("5m", 300), ("1h", 3600), ("24h", 86400))


def category(logger, message):
    text = f"{logger} {message}"
    for name, pattern in CATEGORIES:
        if pattern.search(text):
            return name
    return "other"


class ErrorTally:
    """Warnings and errors from the log, by category and time: the log keeps a few thousand lines, so
    the counts for a day are kept here as they come (each line counted once)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._seen = 0
        self._events = []          # (time, level, category), the last day

    def update(self, lines):
        cut = time.time() - 86400
        with self._lock:
            for r in lines:
                if r["i"] <= self._seen:
                    continue
                self._seen = r["i"]
                if r.get("l") in ("WARNING", "ERROR", "CRITICAL"):
                    self._events.append((r["t"], r["l"], category(r.get("n") or "", r.get("m") or "")))
            if self._events and self._events[0][0] < cut:
                self._events = [e for e in self._events if e[0] >= cut]

    def reset_after_clear(self):
        with self._lock:
            self._seen = 0

    def counts(self):
        now = time.time()
        out = {}
        with self._lock:
            events = list(self._events)
        for name, span in WINDOWS:
            w = {"errors": 0, "warnings": 0, "by": {}}
            for t, level, cat in events:
                if now - t <= span:
                    w["errors" if level != "WARNING" else "warnings"] += 1
                    w["by"][cat] = w["by"].get(cat, 0) + 1
            out[name] = w
        last = next((e for e in reversed(events) if e[1] != "WARNING"), None)
        out["last_error"] = {"t": last[0], "category": last[2]} if last else None
        return out


_TALLY = ErrorTally()
_gen = [None]


def errors():
    from .logbuf import BUFFER
    if _gen[0] != BUFFER.gen:              # the log was cleared: its line numbers start again
        _gen[0] = BUFFER.gen
        _TALLY.reset_after_clear()
    _TALLY.update(BUFFER.since(_TALLY._seen, 5000))
    return _TALLY.counts()


def key_ok(cfg, header):
    """`Authorization: Bearer <monitor key>`; no key set means nobody may read it."""
    key = getattr(cfg, "monitor_key", "") or ""
    if not key or not header:
        return False
    scheme, _, given = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), key.encode())


def _num(x):
    return x if isinstance(x, (int, float)) else 0


def _drives(app):
    """The owner's drive and every account's drive open right now (each has an uploader, cache, healer of
    its own: what happens in an account is part of what the computer is doing)."""
    out = [app.drive]
    accounts = getattr(app, "accounts", None)
    if accounts is not None:
        with accounts._lock:
            out += [ad for ad in accounts._open.values() if getattr(ad, "index", None) is not None]
    return out


def _totals(app, own):
    """Files and bytes on the drive: the owner's and every account's (an end-to-end encrypted account's
    files are sealed objects here, one per file)."""
    files, size, n = own.get("files", 0), own.get("bytes", 0) + own.get("version_bytes", 0), 0
    accounts = getattr(app, "accounts", None)
    if accounts is not None:
        try:
            for a in accounts.listing():
                files += a.get("files") or 0
                size += a.get("bytes") or 0
                n += 1
        except Exception:
            pass
    return {"files": files, "bytes": size, "accounts": n}


def snapshot(app):
    """Everything a status bot shows, from the drive itself and its accounts (only ever counted)."""
    d = app.drive
    now = time.time()
    st = d.index.stats(max_age=5, lazy=True)
    drives = _drives(app)
    ups = [x.uploader for x in drives if getattr(x, "uploader", None) is not None]
    progress = [p for up in ups for p in list(up.progress.values())]
    speed = 0.0
    sent = 0
    for p in progress:
        moved = p.get("bytes", 0) - p.get("base", 0)
        if moved > 0:
            sent += moved
            speed += moved / max(0.5, now - p.get("started", now))
    queued = sum(len(up._pending) for up in ups)
    queue = [q for up in ups for q in up.queue_info()]
    left = sum(q["size"] for q in queue) + sum(max(0, p.get("size", 0) - p.get("bytes", 0)) for p in progress)
    ustats = {}
    for up in ups:
        for k, v in up.stats.items():
            ustats[k] = max(ustats.get(k, 0), v) if k.startswith("last_") else ustats.get(k, 0) + v
    caches = [x.cache for x in drives if getattr(x, "cache", None) is not None]
    active_dl = [a for c in caches for a in list(c.active.values())]
    recent = [r for c in caches for r in list(c.recent) if now - r[0] < 10]
    cache = d.cache
    healer = d.healer
    healers = [x.healer for x in drives if getattr(x, "healer", None) is not None]
    heal = {}
    for h in healers:
        for k, v in h.stats.items():
            heal[k] = heal.get(k, 0) + v
    lost = healer.lost_files(max_age=60) if healer else []
    lost_by = {}
    for e in lost:
        lost_by[e["kind"]] = lost_by.get(e["kind"], 0) + 1
    recheck = dict(healer.recheck) if healer and healer.recheck else None
    if recheck:
        recheck = {k: v for k, v in recheck.items() if isinstance(v, (int, float, bool))}
    scrub = d.maintenance.scrub_state() if d.maintenance else {}
    scrub = {k: v for k, v in scrub.items() if isinstance(v, (int, float)) and k != "pos"}
    backend = d.backend
    devices = d.index.devices()
    sync_jobs = []
    try:
        sync_jobs = d.sync.snapshot() if d.sync is not None else []
    except Exception:
        pass
    sync_states = {}
    for j in sync_jobs:
        state = (j.get("status") or {}).get("state") or "idle"
        sync_states[state] = sync_states.get(state, 0) + 1
    try:
        usage = shutil.disk_usage(d.cfg.resolved_data_dir)
        disk = {"total": usage.total, "free": usage.free}
    except OSError:
        disk = None
    memory = resources._memory_windows() if sys.platform == "win32" else resources._memory_linux()
    try:
        lite = len(app.lite.listing())
    except Exception:
        lite = 0
    return {
        "v": VERSION,
        "time": now,
        "version": __version__,
        "host": socket.gethostname(),
        "started": resources._STARTED,
        "uptime": now - resources._STARTED,
        "drive": {k: _num(st.get(k)) for k in (
            "files", "dirs", "bytes", "unsynced", "pinned_files", "pinned_bytes", "chunks", "unique_chunks", "trash",
            "outbox", "versions", "version_bytes", "protected_chunks", "spare_pieces", "spare_bytes", "snapshots")},
        "uploads": {
            "active": len(progress), "queued": queued, "sent": sent, "left": left, "speed": speed,
            "eta": left / speed if speed > 0 else None,
            "size": sum(p.get("size", 0) for p in progress),
            "done": ustats.get("files", 0), "done_bytes": ustats.get("file_bytes", 0),
            "failed": ustats.get("failed", 0), "retrying": sum(len(up._failures) for up in ups),
            "last_done": ustats.get("last_done", 0), "last_failed": ustats.get("last_failed", 0),
            "uploaded_bytes": ustats.get("uploaded_bytes", 0), "reused_bytes": ustats.get("reused_bytes", 0),
            "compressed_saved": ustats.get("compressed_saved", 0), "spare_bytes": ustats.get("spare_bytes", 0),
            "room": d.fs.room() if hasattr(d.fs, "room") else None,
        },
        "total": _totals(app, st),               # the owner's files and every account's
        "downloads": {
            "pieces": len(active_dl),
            "files": len({(id(c), a.get("node")) for c in caches for a in list(c.active.values())}),
            "jobs": sum(len(c.jobs) for c in caches),
            "speed": sum(b for _, b in recent) / 10.0,
        },
        "health": {
            "parity": bool(d.cfg.parity_enabled), "group": d.cfg.parity_group, "pieces": d.cfg.parity_pieces,
            "lost": len(lost), "lost_by": lost_by,
            "repairs_queued": sum(h._queue.qsize() for h in healers),
            "rebuilt": heal.get("rebuilt", 0), "repaired": heal.get("repaired", 0), "lost_found": heal.get("lost", 0),
            "recheck": recheck, "scrub": scrub,
            "leader": d.maintenance.leader() if d.maintenance else False,
            "bots": getattr(backend, "upload_slots", 1),
            "refused_bots": len(backend.bot_problems()) if hasattr(backend, "bot_problems") else 0,
            "channels": len(getattr(backend, "channels", None) or [None]),
            "servers": len(getattr(getattr(backend, "layout", None), "servers", None) or [1]),
            "unreachable_channels": len(backend.channel_problems()) if hasattr(backend, "channel_problems") else 0,
            "given_up_channels": len(getattr(backend, "lost", None) or []),
        },
        "sync": {
            "failures": getattr(d.journal, "_failures", 0), "outbox": _num(st.get("outbox")),
            "folders": len(sync_jobs), "states": sync_states,
        },
        "errors": errors(),
        "resources": {
            "cpu": resources._process_cpu(), "cores": os.cpu_count() or 1,
            "memory": {k: v for k, v in memory.items() if isinstance(v, (int, float))},
            "disk": disk, "threads": threading.active_count(),
        },
        "devices": {
            "count": len(devices) or 1,
            "online": sum(1 for v in devices.values() if now - (v.get("seen") or 0) < 900) or 1,
            "lite": lite,
        },
        "accounts": app.accounts.count() if getattr(app, "accounts", None) else 0,
        "settings": {"pace": d.cfg.discord_pace, "snapshot_hours": d.cfg.snapshot_interval_hours,
                     "encrypted": d.crypto is not None, "compression": bool(d.cfg.compression),
                     "dedup": bool(d.cfg.dedup), "verify": bool(d.cfg.verify_uploads)},
    }
