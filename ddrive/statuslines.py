"""The status bot's lines: what /api/monitor says, put into words.

`History` keeps what one look can't tell (upload batches, records, restarts, how long the drive was
reachable, what happened each day) and `build()` turns a look and the history into statuses, each with
a priority: problems first, then what is happening now, then the drive's numbers and the history's.
Pure functions of their input, so they can be tested without Discord or a drive.
"""

import datetime
import time

CRITICAL, WARNING, ACTIVITY, INFO, TRIVIA = 0, 1, 2, 3, 4
KIB = 1024


class Status:
    __slots__ = ("text", "level", "key")

    def __init__(self, text, level=INFO, key=None):
        self.text = text[:128]               # Discord's limit for a custom status
        self.level = level
        self.key = key or text               # the same key from one look to the next: the same status

    def __repr__(self):
        return f"Status({self.text!r}, {self.level})"


# ------------------------------------------------------------------ words
def size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}" if abs(n) < 100 else f"{n:,.0f} {unit}"
        n /= 1024


def count(n, one=None, many=None):
    n = int(n or 0)
    text = f"{n:,}"
    if one is None:
        return text
    return f"{text} {one if n == 1 else (many or one + 's')}"


def duration(seconds, short=False):
    s = int(max(0, seconds or 0))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if short:
        return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"
    if d:
        return f"{count(d, 'day')}" + (f" {count(h, 'hour')}" if h else "")
    if h:
        return count(h, "hour") + (f" {count(m, 'minute')}" if m and h < 6 else "")
    if m:
        return count(m, "minute")
    return count(s, "second")


def speed(bps):
    return f"{size(bps)}/s"


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def day_of(t):
    return datetime.date.fromtimestamp(t).isoformat()


FILE_MILESTONES = [100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 75000, 100000, 250000, 500000, 1000000,
                   2500000, 5000000, 10000000]
BYTE_MILESTONES = [x * 1024 ** 3 for x in (1, 10, 50, 100, 250, 500, 750)] + \
                  [x * 1024 ** 4 for x in (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000)]


# ------------------------------------------------------------------ what one look can't tell
class History:
    """Kept by the bot between looks (and saved, so a restart of the bot keeps it)."""

    KEEP_DAYS = 30

    def __init__(self, data=None):
        data = dict(data or {})
        self.started = data.get("started")             # when the drive last started, as it said
        self.last_seen = data.get("last_seen", 0)      # the last look that got an answer
        self.last_try = data.get("last_try", 0)
        self.down_since = data.get("down_since")       # the drive didn't answer since
        self.down_error = data.get("down_error", "")
        self.first_seen = data.get("first_seen") or time.time()
        self.batch = data.get("batch")                 # uploads going on: {start, base, base_bytes, base_failed, peak}
        self.finished = data.get("finished")           # the last batch: {at, files, bytes, took, failed}
        self.prev = data.get("prev") or {}             # counters of the last look
        self.lifetime = data.get("lifetime") or {"files": 0, "bytes": 0, "failed": 0, "restarts": 0, "outages": 0}
        self.days = data.get("days") or {}             # date -> {files, bytes, failed, up, down, errors, peak}
        self.records = data.get("records") or {}       # speed, uptime, day_files, day_bytes, batch_files
        self.events = []                               # what happened at this look (alerts): [(level, text)]
        self.version = data.get("version")
        self.errors_since = data.get("errors_since")   # failures lately, since
        self.streak_clean = data.get("streak_clean")   # no errors since

    def to_json(self):
        return {k: getattr(self, k) for k in ("started", "last_seen", "last_try", "down_since", "down_error",
                                              "first_seen", "batch", "finished", "prev", "lifetime", "days",
                                              "records", "version", "errors_since", "streak_clean")}

    def _day(self, t):
        return self.days.setdefault(day_of(t), {"files": 0, "bytes": 0, "failed": 0, "up": 0, "down": 0,
                                                "errors": 0, "peak": 0})

    def _record(self, name, value, label=None):
        old = self.records.get(name)
        if value > (old or 0):
            self.records[name] = value
            return old is not None and old > 0      # a new record only counts once there was one before
        return False

    def _event(self, level, text):
        self.events.append((level, text))

    def observe(self, snap, now=None):
        """A look that got an answer."""
        now = now or time.time()
        self.events = []
        gap = min(300.0, now - self.last_try) if self.last_try else 0
        self.last_try = now
        if self.down_since is not None:
            self._event(ACTIVITY, f"✅ Back online after {duration(now - self.down_since)}")
            self.down_since, self.down_error = None, ""
        self.last_seen = now
        today = self._day(now)
        today["up"] += gap
        up = snap.get("uploads") or {}
        done, done_bytes, failed = up.get("done", 0), up.get("done_bytes", 0), up.get("failed", 0)
        if self.started is not None and abs((snap.get("started") or 0) - self.started) > 5:
            self.lifetime["restarts"] += 1
            self._event(WARNING, f"🔄 The drive restarted (it had been up {duration((self.prev.get('uptime') or 0))})")
            if self.batch:                           # counters start again at 0 after a restart
                self.batch.update(base=0, base_bytes=0, base_failed=0, carried=self.batch.get("carried", 0) +
                                  max(0, (self.prev.get("done") or 0) - self.batch.get("base", 0)))
            self.prev = {k: v for k, v in self.prev.items() if k in ("files", "bytes", "lost", "disk_free")}
        self.started = snap.get("started")
        if self.version and snap.get("version") and snap["version"] != self.version:
            self._event(INFO, f"⬆️ Updated to DDrive {snap['version']} (was {self.version})")
        self.version = snap.get("version") or self.version
        # what was uploaded since the last look
        d_files = max(0, done - self.prev.get("done", 0))
        d_bytes = max(0, done_bytes - self.prev.get("done_bytes", 0))
        d_failed = max(0, failed - self.prev.get("failed", 0))
        for k, v in (("files", d_files), ("bytes", d_bytes), ("failed", d_failed)):
            self.lifetime[k] += v
            today[k] += v
        if self._record("day_files", today["files"]) and today["files"] >= 100 and not today.get("rec_files"):
            today["rec_files"] = 1
            self._event(INFO, f"🏆 Busiest day yet: {count(today['files'], 'file')} uploaded today")
        self._record("day_bytes", today["bytes"])
        sp = up.get("speed") or 0
        today["peak"] = max(today["peak"], sp)
        if self._record("speed", sp) and sp > 1024 * 1024:
            self._event(TRIVIA, f"⚡ New top upload speed: {speed(sp)}")
        self._record("uptime", snap.get("uptime") or 0)
        # upload batches: from the first file waiting to the moment nothing waits
        busy = (up.get("active") or 0) + (up.get("queued") or 0)
        if busy and not self.batch:
            self.batch = {"start": now, "base": done, "base_bytes": done_bytes, "base_failed": failed, "carried": 0}
        if self.batch:
            b = self.batch
            b["files"] = b.get("carried", 0) + done - b["base"]
            b["bytes"] = done_bytes - b["base_bytes"]
            b["failed"] = failed - b["base_failed"]
            b["total"] = max(b.get("total", 0), b["files"] + busy)
        if not busy and self.batch:
            b = self.batch
            if b["files"] > 0:
                self.finished = {"at": now, "files": b["files"], "bytes": b["bytes"], "took": now - b["start"],
                                 "failed": b["failed"]}
                if b["files"] >= 1:
                    self._event(ACTIVITY, f"✅ All uploads completed: {count(b['files'], 'file')}, {size(b['bytes'])} "
                                          f"in {duration(now - b['start'])}")
                self._record("batch_files", b["files"])
            self.batch = None
        # milestones
        drive = snap.get("drive") or {}
        before_files, before_bytes = self.prev.get("files"), self.prev.get("bytes")
        if before_files is not None:
            for m in FILE_MILESTONES:
                if before_files < m <= drive.get("files", 0):
                    self._event(INFO, f"🎉 {count(m)} files on the drive")
            for m in BYTE_MILESTONES:
                if before_bytes < m <= drive.get("bytes", 0):
                    self._event(INFO, f"🎉 {size(m)} of files on Discord")
        # problems starting and ending
        errs = ((snap.get("errors") or {}).get("5m") or {})
        bad = errs.get("errors", 0) + (snap.get("sync") or {}).get("failures", 0)
        today["errors"] += max(0, ((snap.get("errors") or {}).get("24h") or {}).get("errors", 0) - self.prev.get("err24", 0)) \
            if self.prev.get("err24") is not None else 0
        if bad and self.errors_since is None:
            self.errors_since = now
            self._event(WARNING, "⚠️ Problems started: " + (describe_errors(errs) or "errors in the log"))
        elif not bad and self.errors_since is not None:
            self._event(ACTIVITY, f"✅ Problems cleared after {duration(now - self.errors_since)}")
            self.errors_since = None
        if ((snap.get("errors") or {}).get("1h") or {}).get("errors", 0) == 0:
            if self.streak_clean is None:
                self.streak_clean = now
        else:
            self.streak_clean = None
        health = snap.get("health") or {}
        if health.get("lost", 0) > self.prev.get("lost", 0):
            self._event(CRITICAL, f"🩹 {count(health['lost'] - self.prev.get('lost', 0), 'part')} can't be repaired "
                                  "(Health in the dashboard names the files)")
        disk = (snap.get("resources") or {}).get("disk") or {}
        if disk.get("free") is not None and disk.get("free") < 2 * 1024 ** 3 <= self.prev.get("disk_free", 1 << 62):
            self._event(CRITICAL, f"💽 Low disk space on the drive's computer: {size(disk['free'])} free")
        self.prev = {"done": done, "done_bytes": done_bytes, "failed": failed, "files": drive.get("files", 0),
                     "bytes": drive.get("bytes", 0), "uptime": snap.get("uptime"), "lost": health.get("lost", 0),
                     "err24": ((snap.get("errors") or {}).get("24h") or {}).get("errors", 0),
                     "disk_free": disk.get("free", 1 << 62)}
        self._trim(now)
        return self.events

    def missed(self, error, now=None):
        """A look that got no answer."""
        now = now or time.time()
        self.events = []
        gap = min(300.0, now - self.last_try) if self.last_try else 0
        self.last_try = now
        self._day(now)["down"] += gap
        if self.down_since is None:
            self.down_since = now
            self.lifetime["outages"] += 1
            self._event(CRITICAL, f"🔴 The drive isn't answering: {error}")
        self.down_error = error
        self._trim(now)
        return self.events

    def _trim(self, now):
        if len(self.days) > self.KEEP_DAYS:
            for k in sorted(self.days)[:-self.KEEP_DAYS]:
                del self.days[k]

    # numbers over several days
    def span(self, days, now=None):
        now = now or time.time()
        keys = {day_of(now - 86400 * i) for i in range(days)}
        out = {"files": 0, "bytes": 0, "failed": 0, "up": 0, "down": 0, "errors": 0}
        for k, v in self.days.items():
            if k in keys:
                for f in out:
                    out[f] += v.get(f, 0)
        return out


def describe_errors(window):
    by = (window or {}).get("by") or {}
    if not by:
        return ""
    names = {"upload": "upload failures", "disk": "I/O failures on the local disk", "ratelimit": "Discord rate limits",
             "discord": "Discord refusing requests", "network": "network trouble reaching Discord",
             "download": "read failures", "repair": "repair trouble", "sync": "sync trouble", "other": "errors"}
    top = sorted(by.items(), key=lambda kv: -kv[1])
    return ", ".join(f"{names.get(k, k)} ({v})" for k, v in top[:2])


# ------------------------------------------------------------------ the statuses
def build(snap, hist, now=None):
    """Every status that is true right now, most important first."""
    now = now or time.time()
    out = []
    add = lambda text, level=INFO, key=None: out.append(Status(text, level, key))

    up = snap.get("uploads") or {}
    dl = snap.get("downloads") or {}
    drive = snap.get("drive") or {}
    health = snap.get("health") or {}
    sync = snap.get("sync") or {}
    errs = snap.get("errors") or {}
    e5, e1h, e24 = errs.get("5m") or {}, errs.get("1h") or {}, errs.get("24h") or {}
    res = snap.get("resources") or {}
    devices = snap.get("devices") or {}
    settings = snap.get("settings") or {}
    by5 = e5.get("by") or {}

    # ---- problems
    if by5.get("disk"):
        add(f"💥 Experiencing I/O failures ({count(by5['disk'], 'error')} in 5m)", CRITICAL, "io")
    if by5.get("upload") or up.get("retrying"):
        n = up.get("retrying") or by5.get("upload")
        add(f"⚠️ {count(n, 'upload')} failing, retrying", CRITICAL if n > 3 else WARNING, "upfail")
    if by5.get("network"):
        add("📡 Network trouble reaching Discord", WARNING, "net")
    if by5.get("ratelimit"):
        add("🐢 Discord is rate-limiting, slowing down", WARNING, "rate")
    if by5.get("discord"):
        add("🚫 Discord is refusing some requests", WARNING, "refused")
    if by5.get("download"):
        add(f"⚠️ {count(by5['download'], 'read')} failed in the last 5m", WARNING, "readfail")
    if sync.get("failures"):
        add(f"🔁 Publishing changes keeps failing ({sync['failures']}×)", WARNING, "syncfail")
    if health.get("lost"):
        lb = health.get("lost_by") or {}
        what = lb.get("file", 0)
        add(f"🩹 {count(health['lost'], 'part')} can't be repaired" + (f" ({count(what, 'file')})" if what else ""),
            CRITICAL if what else WARNING, "lost")
    if health.get("refused_bots"):
        add(f"🤖 {count(health['refused_bots'], 'bot')} refused by the channel", WARNING, "bots")
    if health.get("unreachable_channels"):
        add(f"📵 {count(health['unreachable_channels'], 'spare channel')} unreachable", WARNING, "chan")
    if health.get("given_up_channels"):
        add(f"🏚️ {count(health['given_up_channels'], 'channel')} given up, rebuilding elsewhere", WARNING, "givenup")
    disk = res.get("disk") or {}
    if disk.get("free") is not None:
        if disk["free"] < 2 * 1024 ** 3:
            add(f"💽 Low disk space: {size(disk['free'])} free", CRITICAL, "disk")
        elif disk.get("total") and disk["free"] / disk["total"] < 0.1:
            add(f"💽 Disk {pct(disk['total'] - disk['free'], disk['total']):.0f}% full", WARNING, "disk")
    mem = res.get("memory") or {}
    if mem.get("available") and mem.get("total") and mem["available"] / mem["total"] < 0.05:
        add(f"🧠 Memory nearly full ({size(mem['available'])} left)", WARNING, "mem")
    if up.get("room") == 0 and (up.get("queued") or 0):
        add("⏸️ Upload queue full, new files wait", WARNING, "room")
    if e5.get("errors", 0) >= 5 and not out:
        add(f"⚠️ {count(e5['errors'], 'error')} in the last 5 minutes", WARNING, "err5")
    if (res.get("cpu") or 0) > 90 * (res.get("cores") or 1) * 0.9:
        add(f"🔥 CPU busy: {res['cpu']:.0f}%", WARNING, "cpu")

    # ---- what is happening
    active, queued = up.get("active") or 0, up.get("queued") or 0
    b = hist.batch if hist else None
    if active or queued:
        if b and b.get("total"):
            add(f"⬆️ Uploading {count(b.get('files', 0))}/{count(b['total'])} files", ACTIVITY, "batch")
        else:
            add(f"⬆️ Uploading {count(active, 'file')}", ACTIVITY, "batch")
        if up.get("speed"):
            add(f"🚀 Uploading at {speed(up['speed'])}", ACTIVITY, "speed")
        if up.get("eta"):
            add(f"⏳ About {duration(up['eta'])} of uploads left", ACTIVITY, "eta")
        if up.get("left"):
            add(f"📦 {size(up['left'])} left to upload", ACTIVITY, "left")
        if queued:
            add(f"🗂️ {count(queued, 'file')} waiting in the queue", ACTIVITY, "queued")
        if active > 1:
            add(f"🧵 Sending {count(active)} files at once", ACTIVITY, "parallel")
        if b and b.get("start"):
            add(f"⏱️ Upload batch running for {duration(now - b['start'])}", ACTIVITY, "batchtime")
        if b and b.get("bytes"):
            add(f"📤 {size(b['bytes'])} sent in this batch", ACTIVITY, "batchbytes")
        if b and b.get("failed"):
            add(f"🔁 {count(b['failed'], 'retry', 'retries')} in this batch", ACTIVITY, "batchfail")
    elif hist and hist.finished and now - hist.finished["at"] < 900:
        f = hist.finished
        add(f"✅ All uploads completed ({count(f['files'], 'file')})", ACTIVITY, "done")
        add(f"✅ Last batch: {size(f['bytes'])} in {duration(f['took'])}", ACTIVITY, "donebytes")
    if dl.get("files"):
        add(f"⬇️ Reading {count(dl['files'], 'file')} from Discord", ACTIVITY, "dl")
    if dl.get("speed") and dl["speed"] > 1024:
        add(f"📥 Downloading at {speed(dl['speed'])}", ACTIVITY, "dlspeed")
    if dl.get("jobs"):
        add(f"📌 Making {count(dl['jobs'], 'file')} available offline", ACTIVITY, "offline")
    if health.get("repairs_queued"):
        add(f"🛠️ Repairing {count(health['repairs_queued'], 'piece')}", ACTIVITY, "repairing")
    rc = health.get("recheck") or {}
    if rc.get("running"):
        add(f"🔎 Re-checking lost parts: {count(rc.get('checked', 0))}/{count(rc.get('total', 0))}", ACTIVITY, "recheck")
    states = sync.get("states") or {}
    if states.get("running"):
        add(f"🔄 Syncing {count(states['running'], 'folder')}", ACTIVITY, "syncing")
    if drive.get("unsynced") and not (active or queued):
        add(f"🕓 {count(drive['unsynced'], 'file')} not on Discord yet", ACTIVITY, "unsynced")
    if drive.get("outbox"):
        add(f"📨 {count(drive['outbox'], 'change')} waiting to reach other devices", ACTIVITY, "outbox")

    # ---- the drive's numbers
    uptime = snap.get("uptime") or 0
    add(f"⏱️ Uptime: {duration(uptime)}", INFO, "uptime")
    add(f"📁 {count(drive.get('files'), 'file')} on the drive", INFO, "files")
    add(f"☁️ {size(drive.get('bytes'))} of files on Discord", INFO, "bytes")
    if drive.get("dirs"):
        add(f"🗃️ {count(drive['dirs'], 'folder')}", INFO, "dirs")
    if drive.get("unique_chunks"):
        add(f"🧩 {count(drive['unique_chunks'], 'piece')} stored on Discord", INFO, "chunks")
    if up.get("done"):
        add(f"📤 {count(up['done'], 'file')} uploaded since start", INFO, "donesince")
    if up.get("uploaded_bytes"):
        add(f"📤 {size(up['uploaded_bytes'])} sent to Discord since start", INFO, "sentsince")
    if drive.get("versions"):
        add(f"🕰️ {count(drive['versions'], 'earlier version')} kept ({size(drive.get('version_bytes'))})", INFO, "versions")
    if drive.get("snapshots"):
        add(f"📸 {count(drive['snapshots'], 'snapshot')} of the whole drive", INFO, "snaps")
    if drive.get("trash"):
        add(f"🗑️ {count(drive['trash'], 'item')} in Deleted", TRIVIA, "trash")
    if drive.get("pinned_files"):
        add(f"📌 {count(drive['pinned_files'], 'file')} kept offline ({size(drive.get('pinned_bytes'))})", TRIVIA, "pinned")
    if drive.get("files"):
        add(f"📏 Average file: {size(drive.get('bytes', 0) / drive['files'])}", TRIVIA, "avg")

    # ---- protection
    if health.get("parity"):
        chunks, prot = drive.get("chunks") or 0, drive.get("protected_chunks") or 0
        if chunks:
            p = pct(prot, chunks)
            add("🛡️ Every piece has spares" if p >= 99.95 else f"🛡️ {p:.1f}% of pieces protected by spares",
                INFO if p >= 99 else WARNING if p < 50 else INFO, "protected")
        if drive.get("spare_pieces"):
            add(f"🧱 {count(drive['spare_pieces'], 'spare piece')} ({size(drive.get('spare_bytes'))})", TRIVIA, "spares")
        add(f"🧮 {health.get('pieces')} spare per {health.get('group')} pieces", TRIVIA, "ratio")
    else:
        add("🛡️ Self-healing is off", WARNING, "noparity")
    sc = health.get("scrub") or {}
    if sc.get("total"):
        add(f"🔍 Background check {pct(sc.get('done', 0), sc['total']):.0f}% through", INFO, "scrub")
    if sc.get("passes"):
        add(f"🔍 {count(sc['passes'], 'full check')} of every piece done", TRIVIA, "passes")
    if sc.get("last_pass"):
        add(f"🔍 Last full check {duration(now - sc['last_pass'])} ago", TRIVIA, "lastpass")
    if health.get("rebuilt"):
        add(f"♻️ {count(health['rebuilt'], 'piece')} rebuilt from spares", INFO, "rebuilt")
    if health.get("repaired"):
        add(f"🛠️ {count(health['repaired'], 'piece')} repaired since start", INFO, "repaired")
    if not health.get("lost") and not e1h.get("errors") and not sync.get("failures"):
        add("💚 Healthy: no errors in the last hour", INFO, "healthy")
    if not e24.get("errors"):
        add("💚 No errors in 24 hours", TRIVIA, "clean24")
    elif e24.get("errors"):
        add(f"📋 {count(e24['errors'], 'error')} in the last 24 hours", TRIVIA, "err24")
    if hist and hist.streak_clean:
        add(f"💚 Error-free for {duration(now - hist.streak_clean)}", TRIVIA, "streak")

    # ---- how it is set up
    add(f"🤖 {count(health.get('bots', 1), 'bot')} uploading", TRIVIA, "botcount")
    if (health.get("channels") or 1) > 1:
        add(f"📡 Spread over {count(health['channels'], 'channel')} on {count(health.get('servers', 1), 'server')}",
            TRIVIA, "spread")
    if devices.get("count", 1) > 1:
        add(f"💻 {count(devices['count'], 'device')} share this drive ({devices.get('online', 1)} online)", TRIVIA, "devices")
    if devices.get("lite"):
        add(f"🪶 {count(devices['lite'], 'DDrive Lite computer')} syncing", TRIVIA, "lite")
    if snap.get("accounts"):
        add(f"👥 {count(snap['accounts'], 'account')} on the dashboard", TRIVIA, "accounts")
    if sync.get("folders"):
        add(f"🔄 {count(sync['folders'], 'folder')} kept in sync", TRIVIA, "folders")
    if up.get("reused_bytes"):
        add(f"♊ {size(up['reused_bytes'])} not uploaded twice (dedup)", TRIVIA, "dedup")
    if up.get("compressed_saved"):
        add(f"🗜️ Compression saved {size(up['compressed_saved'])}", TRIVIA, "compress")
    if settings.get("encrypted"):
        add("🔒 Everything encrypted before it leaves", TRIVIA, "enc")
    add(f"🏷️ DDrive {snap.get('version', '?')}", TRIVIA, "version")
    if settings.get("pace"):
        add(f"🚦 Discord pace: {settings['pace']}", TRIVIA, "pace")
    if res.get("cpu") is not None:
        add(f"⚙️ CPU {res['cpu']:.0f}%", TRIVIA, "cpuinfo")
    if mem.get("process"):
        add(f"🧠 Using {size(mem['process'])} of memory", TRIVIA, "meminfo")
    if disk.get("free") is not None:
        add(f"💽 {size(disk['free'])} free on the local disk", TRIVIA, "diskinfo")
    if res.get("threads"):
        add(f"🧵 {count(res['threads'], 'thread')} running", TRIVIA, "threads")

    # ---- from the history
    if hist:
        today = hist.days.get(day_of(now)) or {}
        if today.get("files"):
            add(f"📅 {count(today['files'], 'file')} uploaded today ({size(today.get('bytes'))})", INFO, "today")
        week = hist.span(7, now)
        if week["files"]:
            add(f"📆 {count(week['files'], 'file')} uploaded this week", TRIVIA, "week")
        if week["bytes"]:
            add(f"📆 {size(week['bytes'])} uploaded this week", TRIVIA, "weekbytes")
        seen = week["up"] + week["down"]
        if seen > 3600:
            a = pct(week["up"], seen)
            add(f"📶 Online {a:.2f}% this week" if a < 100 else "📶 Online 100% this week", INFO, "avail")
        if hist.lifetime.get("files"):
            add(f"🧾 {count(hist.lifetime['files'], 'file')} uploaded while watched", TRIVIA, "lifefiles")
        if hist.lifetime.get("restarts"):
            add(f"🔄 Restarted {count(hist.lifetime['restarts'], 'time')} while watched", TRIVIA, "restarts")
        r = hist.records
        if r.get("speed"):
            add(f"⚡ Top upload speed: {speed(r['speed'])}", TRIVIA, "recspeed")
        if r.get("uptime") and r["uptime"] > uptime + 3600:
            add(f"🏆 Longest uptime: {duration(r['uptime'])}", TRIVIA, "recuptime")
        elif uptime > 86400 and r.get("uptime", 0) <= uptime + 60:
            add("🏆 Longest uptime ever, right now", TRIVIA, "recuptime")
        if r.get("day_files"):
            add(f"🏆 Busiest day: {count(r['day_files'], 'file')}", TRIVIA, "recday")
        if r.get("batch_files"):
            add(f"🏆 Biggest batch: {count(r['batch_files'], 'file')}", TRIVIA, "recbatch")
        if hist.finished and not (active or queued) and now - hist.finished["at"] >= 900:
            add(f"💤 Idle: last upload {duration(now - hist.finished['at'])} ago", INFO, "idle")
        nxt = next((m for m in FILE_MILESTONES if m > drive.get("files", 0)), None)
        if nxt and drive.get("files"):
            add(f"🎯 {count(nxt - drive['files'], 'file')} to {count(nxt)}", TRIVIA, "nextfiles")
        nxtb = next((m for m in BYTE_MILESTONES if m > drive.get("bytes", 0)), None)
        if nxtb and drive.get("bytes"):
            add(f"🎯 {size(nxtb - drive['bytes'])} to {size(nxtb)}", TRIVIA, "nextbytes")
        if hist.first_seen:
            add(f"👀 Watching this drive for {duration(now - hist.first_seen)}", TRIVIA, "watching")
    if not (active or queued) and not (hist and hist.finished):
        add("💤 Idle: nothing to upload", INFO, "idle")
    out.sort(key=lambda s: s.level)
    return out


def offline(hist, now=None):
    """What can be said while the drive doesn't answer."""
    now = now or time.time()
    out = []
    since = hist.down_since or now
    out.append(Status(f"🔴 Drive offline for {duration(now - since)}", CRITICAL, "offline"))
    if hist.down_error:
        out.append(Status(f"🔴 {hist.down_error}", CRITICAL, "offwhy"))
    if hist.last_seen:
        out.append(Status(f"🕓 Last answer {duration(now - hist.last_seen)} ago", CRITICAL, "lastseen"))
    return out


def mood(statuses, down=False):
    """The bot's own dot: green when fine, yellow when something needs a look, red when it is serious."""
    if down or any(s.level == CRITICAL for s in statuses):
        return "dnd"
    if any(s.level == WARNING for s in statuses):
        return "idle"
    return "online"


class Rotation:
    """Which status to show next. Problems are shown most (each in turn), what is happening next, and the
    rest goes round so every status gets its turn: one in three slots is a problem while there is one,
    and while something is happening every other slot shows it."""

    def __init__(self):
        self._turn = 0
        self._pos = {}

    def _take(self, level_group, items):
        if not items:
            return None
        i = self._pos.get(level_group, 0) % len(items)
        self._pos[level_group] = i + 1
        return items[i]

    def next(self, statuses):
        if not statuses:
            return None
        self._turn += 1
        bad = [s for s in statuses if s.level <= WARNING]
        busy = [s for s in statuses if s.level == ACTIVITY]
        rest = [s for s in statuses if s.level >= INFO]
        crit = [s for s in statuses if s.level == CRITICAL]
        if crit and self._turn % 3 != 0:
            return self._take("crit", crit)
        if bad and self._turn % 3 == 1:
            return self._take("bad", bad)
        if busy and self._turn % 2 == 0:
            return self._take("busy", busy)
        info = [s for s in rest if s.level == INFO]
        trivia = [s for s in rest if s.level == TRIVIA]
        if info and (not trivia or self._turn % 3 != 0):
            return self._take("info", info)
        return self._take("trivia", trivia) or self._take("busy", busy) or self._take("bad", bad)
