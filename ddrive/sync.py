"""Sync and backup folders: keep a folder on this computer and a folder on the drive in step,
e.g. C:\\Users\\me\\Downloads -> Z:\\Downloads.

Each job pairs a local folder with a drive folder and has a *mode* (what is copied which way) and a
*trigger* (when it runs). Jobs belong to this device (they name local folders), so they are kept in
its config file (`sync_jobs`); what they copy onto the drive syncs to every device as usual.

Modes
    backup           this computer -> drive. New and changed files are copied; files deleted here
                     stay on the drive. Each file is copied once: the job remembers what it has
                     backed up, so a copy you move, rename or delete on the drive is not put back
                     (only a file that changes here is copied again).
    mirror           this computer -> drive, exactly: deleting a file here deletes it on the drive
                     too (the drive keeps it under Deleted, so it can be brought back).
    two-way          both ways. Changes and deletions on either side are copied to the other. A file
                     changed on both sides since the last sync is kept twice (" (conflict ...)").
    move             this computer -> drive, then the file is deleted here once it is safely stored
                     in Discord (frees space, e.g. for Downloads).
    download         drive -> this computer. New and changed files are copied; files deleted on the
                     drive stay here.
    download-mirror  drive -> this computer, exactly. Files deleted on the drive are moved to a
                     holding folder here (kept 30 days), never deleted outright.

Triggers
    live      as soon as something changes (Windows tells us at once; elsewhere, and for changes on
              the drive, the folders are checked every few seconds), plus a full check now and then.
    interval  every N minutes.
    daily     once a day at HH:MM.
    manual    only when started (dashboard, menu, or `sync run`).

Files are compared by size and modification time; copies keep the original modification time, so
nothing is copied twice. A file still being written (changed in the last few seconds, or locked by
the program writing it) waits for the next pass, and partial downloads (*.crdownload, *.part, ...)
are never copied. Runs inside the drive process; the command line and the dashboard change the
config and leave requests in <data dir>/sync, and read the status the drive writes there.
"""

import errno
import fnmatch
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import socket
import sys
import threading
import time

from .config import Config, config_path

log = logging.getLogger("ddrive.sync")

MODES = {
    "backup": ("Back up", "up",
               "Copies new and changed files to the drive. Files you delete here stay on the drive."),
    "mirror": ("Mirror", "up",
               "The drive folder becomes an exact copy of this folder: deleting here deletes there too "
               "(the drive keeps deleted files under Deleted)."),
    "two-way": ("Two-way sync", "both",
                "Changes and deletions on either side are copied to the other. A file changed on both "
                "sides is kept twice."),
    "move": ("Move to the drive", "up",
             "Copies files to the drive, then deletes them here once they are safely stored in Discord. "
             "Frees space on this computer."),
    "download": ("Download backup", "down",
                 "Copies new and changed files from the drive to this folder. Nothing is deleted here."),
    "download-mirror": ("Download mirror", "down",
                        "This folder becomes an exact copy of the drive folder. Files deleted on the drive "
                        "are moved to a holding folder here (kept 30 days)."),
}
TRIGGERS = {
    "live": "As soon as something changes",
    "interval": "Every few minutes / hours",
    "daily": "Once a day",
    "manual": "Only when I start it",
}
DEFAULT_EXCLUDE = ["*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload", "*.tmp", "~$*",
                   ".~lock.*", "Thumbs.db", "desktop.ini", ".DS_Store", "*.ddsync-part"]
JOB_DEFAULTS = {"name": "", "local": "", "remote": "", "mode": "backup", "trigger": "live", "every": 60,
                "at": "03:00", "exclude": [], "enabled": True}

SETTLE = 5.0              # a file must be unchanged this long (seconds) before it is copied
LIVE_RESCAN = 30.0        # live jobs: full check this often anyway (Windows tells us about changes at once)
LIVE_POLL = 10.0          # live jobs where we can't be told about changes
DEBOUNCE = 2.0            # wait this long after a change before syncing (more changes usually follow)
MIN_GAP = 5.0             # live jobs: at least this long between two passes
RETRY = 15.0              # run again this soon when files were busy or still uploading
FAIL_RETRY = 120.0        # a pass that failed altogether is tried again after this long
TRASH_DAYS = 30.0         # files removed from this computer by a sync are kept this long
GUARD = 3                 # a side that is suddenly empty deletes nothing on the other if that'd be this many files
MTIME_SLACK = 2.0         # FAT and some network shares store times with 2 second steps
PIECE = 1 << 20
HISTORY = 10


class SyncError(Exception):
    pass


# ------------------------------------------------------------------ jobs and paths
def drive_path(p):
    """'Z:\\Downloads', 'Downloads' or '/Downloads' -> '/Downloads'."""
    p = (p or "").strip().replace("\\", "/")
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        p = p[2:]
    return "/" + "/".join(x for x in p.split("/") if x and x != ".")


def local_path(p):
    p = os.path.expandvars(os.path.expanduser((p or "").strip().strip('"')))
    if sys.platform == "win32" and len(p) == 2 and p[1] == ":":
        p += "\\"
    return os.path.normpath(os.path.abspath(p)) if p else ""


def _inside(path, folder):
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:            # different drives on Windows
        return False


def normalize_job(job, cfg, existing=(), elsewhere=False):
    """Check a job (dict from the config, the dashboard or the command line) and fill in defaults.
    Raises SyncError with a message meant for people. elsewhere: the folder is on another computer
    (one that runs DDrive Lite, see litesync.py), so nothing about it can be looked at from here."""
    out = dict(JOB_DEFAULTS)
    out.update({k: v for k, v in (job or {}).items() if k in JOB_DEFAULTS or k == "id"})
    out["id"] = str(out.get("id") or secrets.token_hex(3))
    out["mode"] = str(out["mode"]).strip().lower()
    out["trigger"] = str(out["trigger"]).strip().lower()
    if out["mode"] not in MODES:
        raise SyncError(f"Unknown mode '{out['mode']}'. Use one of: {', '.join(MODES)}")
    if out["trigger"] not in TRIGGERS:
        raise SyncError(f"Unknown trigger '{out['trigger']}'. Use one of: {', '.join(TRIGGERS)}")
    if elsewhere:
        return _normalize_rest(dict(out, local=str(out["local"] or "").strip().strip('"')[:1000],
                                    remote=drive_path(out["remote"])), existing, elsewhere)
    out["local"] = local_path(out["local"])
    letter = str(out["remote"] or "").strip()[:2]
    mp = getattr(cfg, "mount_point", "") or ""
    if len(letter) == 2 and letter[1] == ":" and letter[0].isalpha() and \
            not (sys.platform == "win32" and letter.upper() == mp[:2].upper()):
        raise SyncError(f"{out['remote']} isn't on the drive ({mp}). Give a folder on the drive, "
                        f"e.g. {mp.rstrip(':')}:\\Downloads or /Downloads." if sys.platform == "win32"
                        else f"{out['remote']} isn't a folder on the drive. Give one like /Downloads.")
    if sys.platform != "win32" and mp and str(out["remote"]).startswith(mp.rstrip("/") + "/"):
        out["remote"] = out["remote"][len(mp.rstrip("/")):]       # /mnt/ddrive/Photos -> /Photos
    out["remote"] = drive_path(out["remote"])
    _check_folders(out)
    direction = MODES[out["mode"]][1]
    if direction != "down" and not os.path.isdir(out["local"]):
        raise SyncError(f"Folder not found on this computer: {out['local']}")
    moved = real_folder(out["local"])
    if moved and not _has_files(out["local"]):
        raise SyncError(f"{out['local']} is empty: Windows keeps this folder in {moved} (moved there by OneDrive "
                        "or in its settings). Choose that folder instead.")
    mp = getattr(cfg, "mount_point", "") or ""
    if sys.platform == "win32":
        if mp and os.path.splitdrive(out["local"])[0].upper() == mp[:2].upper():
            raise SyncError(f"{out['local']} is on the drive itself ({mp}). Choose a folder on this computer.")
    elif mp and _inside(out["local"], mp):
        raise SyncError(f"{out['local']} is inside the drive's folder ({mp}). Choose a folder on this computer.")
    if _inside(out["local"], cfg.resolved_data_dir) or _inside(cfg.resolved_data_dir, out["local"]):
        raise SyncError("That folder holds DDrive's own data (cache, index); choose another one.")
    if out["local"] in (os.path.abspath(os.sep), os.path.splitdrive(out["local"])[0] + os.sep) \
            and out["mode"] in ("download-mirror", "two-way", "move"):
        raise SyncError("Choose a folder, not a whole disk, for this mode.")
    return _normalize_rest(out, existing)


def _check_folders(out, where="this"):
    if not out["local"]:
        raise SyncError(f"Choose a folder on {where} computer.")
    if not out["remote"] or (out["remote"] == "/" and out["mode"] in ("mirror", "two-way")):
        raise SyncError("Choose a folder on the drive (not the whole drive) for mirror and two-way sync."
                        if out["remote"] == "/" else "Choose a folder on the drive.")


def _normalize_rest(out, existing, elsewhere=False):
    """The part of normalize_job that doesn't look at this computer's folders."""
    if elsewhere:
        _check_folders(out, "that")
        if re.fullmatch(r"[A-Za-z]:[\\/]*|[\\/]+", out["local"]) and out["mode"] in ("download-mirror", "two-way", "move"):
            raise SyncError("Choose a folder, not a whole disk, for this mode.")
    try:
        out["every"] = max(1, int(float(out["every"])))
    except (TypeError, ValueError):
        raise SyncError("'every' must be a number of minutes.") from None
    at = str(out["at"] or "03:00").strip()
    try:
        hh, mm = at.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        raise SyncError("The time must look like 03:00 (24-hour clock).") from None
    out["at"] = f"{hh:02d}:{mm:02d}"
    ex = out["exclude"]
    if isinstance(ex, str):
        ex = ex.replace("\n", ",").split(",")
    out["exclude"] = [str(x).strip() for x in ex or [] if str(x).strip()]
    out["enabled"] = bool(out["enabled"]) if not isinstance(out["enabled"], str) \
        else out["enabled"].lower() in ("1", "true", "yes", "on")
    out["name"] = str(out["name"] or "").strip()[:80] or re.split(r"[\\/]", out["local"].rstrip("\\/"))[-1] or out["local"]
    here = (lambda p: str(p or "").strip()) if elsewhere else (lambda p: os.path.normcase(local_path(p)))
    for other in existing:
        if other.get("id") != out["id"] and here(other.get("local")) == here(out["local"]) \
                and drive_path(other.get("remote")) == out["remote"]:
            raise SyncError("Those two folders are already paired.")
    return out


def describe(job):
    """'C:\\Users\\me\\Downloads -> Z:/Downloads, back up, live'."""
    arrow = {"up": "->", "down": "<-", "both": "<->"}[MODES[job["mode"]][1]]
    return f"{job['local']} {arrow} {job['remote']}"


def when_text(job):
    t = job["trigger"]
    if t == "interval":
        e = job["every"]
        return f"every {e // 60} h" + (f" {e % 60} min" if e % 60 else "") if e >= 60 else f"every {e} min"
    if t == "daily":
        return f"daily at {job['at']}"
    return {"live": "live", "manual": "manual"}[t]


def browse(path, mount=""):
    """Folders on this computer, for choosing one to sync: {path, parent, dirs, roots}. Without a
    path: the places to start from. mount: the drive's own letter, which is left out."""
    roots = []
    if os.name == "nt":
        for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if f"{c}:" != (mount or "")[:2].upper() and os.path.isdir(f"{c}:\\"):
                roots.append({"name": f"{c}:", "path": f"{c}:\\"})
    else:
        roots = [{"name": "Home", "path": os.path.expanduser("~")}, {"name": "/", "path": "/"}]
        for media in ("/media", "/mnt"):
            if os.path.isdir(media):
                roots.append({"name": media, "path": media})
    path = (path or "").strip()
    if not path:
        return {"path": "", "parent": None, "dirs": roots, "roots": roots}
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Folder not found: {path}")
    dirs = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith((".", "$")) and not _is_link(e):
                        dirs.append({"name": e.name, "path": e.path})
                except OSError:
                    continue
    except OSError as e:
        raise PermissionError(f"Can't open {path}: {e.strerror or e}") from None
    dirs.sort(key=lambda d: d["name"].lower())
    up = os.path.dirname(path.rstrip("\\/")) if path.rstrip("\\/") else None
    if os.name == "nt" and len(path.rstrip("\\")) == 2:
        up = ""                                                     # a disk: back to the list of disks
    return {"path": path, "parent": up if up != path else None, "dirs": dirs[:2000], "roots": roots}


def sync_dir(cfg):
    return os.path.join(cfg.resolved_data_dir, "sync")


def load_jobs(cfg=None):
    cfg = cfg or Config.load()
    return [dict(JOB_DEFAULTS, **j) for j in (getattr(cfg, "sync_jobs", None) or []) if isinstance(j, dict) and j.get("id")]


def save_jobs(jobs, drive=None):
    """Store the jobs in the config file (and in the running drive's settings)."""
    if drive is not None:
        drive.cfg.sync_jobs = jobs
        if getattr(drive, "local_test_dir", None):
            return
    saved = Config.load()
    saved.sync_jobs = jobs
    saved.save()


def read_status(cfg):
    try:
        with open(os.path.join(sync_dir(cfg), "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def request_run(cfg, jid, cancel=False, again=False):
    """Ask the running drive to run (or stop) a job now. again: a backup forgets what it already
    copied, so everything missing on the drive is copied once more."""
    d = sync_dir(cfg)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ("cancel-" if cancel else "again-" if again else "run-") + jid), "w") as f:
        f.write(str(time.time()))


def backup_state(state_dir, jid):
    """Where a backup job remembers what it has copied."""
    return os.path.join(state_dir, f"{jid}.backup.json")


def next_daily(at, after):
    hh, mm = (int(x) for x in at.split(":"))
    t = time.localtime(after)
    cand = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))
    if cand <= after:
        t = time.localtime(after + 86400)
        cand = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))
    return cand


# ------------------------------------------------------------------ scanning
class _Excluder:
    def __init__(self, patterns):
        self.names, self.paths = [], []
        for p in list(DEFAULT_EXCLUDE) + list(patterns or []):
            p = p.replace("\\", "/").strip().strip("/").lower()
            if p:
                (self.paths if "/" in p else self.names).append(p)

    def __call__(self, rel):
        rel = rel.lower()
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(name, p) for p in self.names) or \
            any(fnmatch.fnmatchcase(rel, p) or rel.startswith(p + "/") for p in self.paths)


_LINK_TAGS = (0xA0000003, 0xA000000C)     # junction (mount point), symbolic link


def _is_link(entry):
    """A link to somewhere else (skipped). Other reparse points, such as OneDrive folders, are real folders."""
    try:
        if entry.is_symlink():
            return True
        if sys.platform == "win32":
            st = entry.stat(follow_symlinks=False)
            return bool(st.st_file_attributes & 0x400) and getattr(st, "st_reparse_tag", _LINK_TAGS[0]) in _LINK_TAGS
    except OSError:
        return True
    return False


# Windows' own folders, which OneDrive (or the user) can move elsewhere: registry value -> usual name
_KNOWN = {"Personal": "Documents", "My Pictures": "Pictures", "My Music": "Music", "My Video": "Videos",
          "Desktop": "Desktop", "{374DE290-123F-4565-9164-39C4925E467B}": "Downloads"}


def known_folders():
    """{usual path: where Windows really keeps it} for this user's Documents, Pictures, Desktop, ..."""
    if sys.platform != "win32":
        return {}
    out = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            for value, name in _KNOWN.items():
                try:
                    real = os.path.normpath(os.path.expandvars(winreg.QueryValueEx(k, value)[0]))
                except OSError:
                    continue
                out[os.path.join(os.path.expanduser("~"), name)] = real
    except OSError:
        pass
    return out


def real_folder(path):
    """Where Windows really keeps `path` when it is one of its own folders that was moved (e.g. by
    OneDrive: C:\\Users\\you\\Documents -> C:\\Users\\you\\OneDrive\\Documents), else None."""
    for usual, real in known_folders().items():
        if os.path.normcase(usual) == os.path.normcase(path) and os.path.normcase(real) != os.path.normcase(path) \
                and os.path.isdir(real):
            return real
    return None


def _has_files(path):
    try:
        with os.scandir(path) as it:
            return any(not _is_link(e) for e in it if e.name.lower() != "desktop.ini")
    except OSError:
        return False


def scan_local(root, excluded, problems):
    """{rel: (size, mtime)} of the files and the set of folders under `root` ('a/b.txt' style)."""
    files, dirs = {}, set()
    todo = [""]
    while todo:
        rel_dir = todo.pop()
        try:
            it = os.scandir(os.path.join(root, *rel_dir.split("/")) if rel_dir else root)
        except OSError as e:
            problems.append(f"Can't read {rel_dir or root}: {e.strerror or e}")
            continue
        with it:
            for entry in it:
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                if excluded(rel):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not _is_link(entry):
                            dirs.add(rel)
                            todo.append(rel)
                    elif entry.is_file():
                        st = entry.stat()
                        files[rel] = (st.st_size, st.st_mtime)
                except OSError:
                    continue
    return files, dirs


def scan_remote(drive, root_path, excluded):
    """{rel: (size, mtime, node)} of the files and the set of folders under drive folder `root_path`."""
    if hasattr(drive, "scan_tree"):          # a drive on another computer answers this in one go
        return drive.scan_tree(root_path, excluded)
    idx, fs = drive.index, drive.fs
    files, dirs = {}, set()
    root = idx.resolve(root_path)
    if root is None or not root["is_dir"]:
        return files, dirs
    todo = [(root, "")]
    while todo:
        node, rel_dir = todo.pop()
        for c in idx.children(node["id"]):
            rel = f"{rel_dir}/{c['name']}" if rel_dir else c["name"]
            if excluded(rel) or fs._hidden(posixpath.join(root_path, rel)):
                continue
            if c["is_dir"]:
                dirs.add(rel)
                todo.append((c, rel))
            else:
                files[rel] = (c["size"], c["mtime"], c)
    return files, dirs


def same(a, b):
    return a is not None and b is not None and a[0] == b[0] and abs(a[1] - b[1]) <= MTIME_SLACK


def conflict_copy_name(rel, host):
    folder, name = posixpath.split(rel)
    stem, ext = os.path.splitext(name)
    if not stem:
        stem, ext = name, ""
    new = f"{stem} (conflict {host} {time.strftime('%Y-%m-%d %H.%M')}){ext}"
    return posixpath.join(folder, new) if folder else new


# ------------------------------------------------------------------ one pass
class Run:
    """One pass of one job."""

    def __init__(self, drive, job, state_dir, settle=SETTLE, stop=None, progress=None):
        self.d = drive
        self.job = job
        self.state_dir = state_dir
        self.settle = settle
        self.stop = stop or (lambda: False)
        self.progress = progress or (lambda **kw: None)
        self.local = job["local"]
        self.remote = job["remote"]
        self.mode = job["mode"]
        self.excluded = _Excluder(job.get("exclude"))
        self.r = {"up": 0, "down": 0, "deleted_remote": 0, "deleted_local": 0, "moved": 0, "conflicts": 0,
                  "bytes": 0, "busy": 0, "waiting": 0, "errors": 0, "skipped_deletes": 0, "kept_away": 0,
                  "files": 0, "remote_files": 0}
        self.problems = []
        self.settled_at = None        # when the newest file held back because it was just written can go
        self.trash = None
        if hasattr(drive, "listen"):  # a drive on another computer says how far a big file is, and when it waits
            drive.listen(lambda **kw: self.progress(**kw))
        self.host = (socket.gethostname() or "this computer").split(".")[0]

    # -------------------------------------------------- helpers
    def _l(self, rel):
        return os.path.join(self.local, *rel.split("/"))

    def _r(self, rel):
        return posixpath.join(self.remote, rel) if self.remote != "/" else "/" + rel

    def _problem(self, rel, e):
        self.r["errors"] += 1
        msg = f"{rel}: {e.strerror if isinstance(e, OSError) and e.strerror else e}"
        if len(self.problems) < 50:
            self.problems.append(msg)
        log.warning("Sync %s: %s", self.job["name"], msg)

    def _settled(self, rel, l):
        if self.settle and time.time() - l[1] < self.settle:
            self.r["busy"] += 1
            self.settled_at = min(self.settled_at or float("inf"), l[1] + self.settle)
            return False
        return True

    def _check(self):
        if self.stop():
            raise InterruptedError("stopped")

    # -------------------------------------------------- copying
    def upload(self, rel, l):
        """Copy local file `rel` onto the drive, keeping its modification time."""
        path = self._r(rel)
        src = self._l(rel)
        self.progress(file=rel, action="up", size=l[0])
        try:
            parent = posixpath.dirname(path)
            node = self.d.index.resolve(parent)
            if node is None:
                self.d.index.makedirs(parent)
            elif not node["is_dir"]:
                raise OSError(errno.ENOTDIR, f"{parent} on the drive is a file")
            existing = self.d.index.resolve(path)
            if existing is not None and existing["is_dir"]:
                raise OSError(errno.EISDIR, "the drive has a folder with that name")
            with open(src, "rb") as f:
                before = os.fstat(f.fileno())
                n = self.d.fs.put_file(path, f, mtime=before.st_mtime, stop=self.stop)
        except InterruptedError:
            raise
        except PermissionError as e:
            if sys.platform == "win32" and getattr(e, "winerror", None) in (32, 33):
                self.r["busy"] += 1          # open in another program; next pass
                return False
            self._problem(rel, e)
            return False
        except OSError as e:
            self._problem(rel, e)
            return False
        self.r["up"] += 1
        self.r["bytes"] += n
        self.progress(done_bytes=n)
        return True

    def download(self, rel, rf):
        """Copy drive file `rel` into the local folder, keeping its modification time."""
        size, mtime, node = rf
        dst = self._l(rel)
        tmp = dst + ".ddsync-part"
        self.progress(file=rel, action="down", size=size)
        try:
            free = shutil.disk_usage(os.path.dirname(dst) if os.path.isdir(os.path.dirname(dst)) else self.local).free
            reserve = int(getattr(self.d.cfg, "min_free_disk_bytes", 0) or 0)
            if free - size < reserve:
                raise OSError(errno.ENOSPC, f"not enough free space here ({free / 2**30:.1f} GiB free, "
                                            f"keeping {reserve / 2**30:.1f} GiB free)")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(dst):
                raise OSError(errno.EISDIR, "this computer has a folder with that name")
            with open(tmp, "wb") as out:
                pos = 0
                while pos < size:
                    self._check()
                    data = self.d.fs.read_file(node["id"], pos, PIECE)
                    if not data:
                        break
                    out.write(data)
                    pos += len(data)
            now = self.d.index.get(node["id"])
            if pos != size or now is None or now["version"] != node["version"]:
                raise OSError(errno.EAGAIN, "it changed while it was being copied; trying again next time")
            os.utime(tmp, (time.time(), mtime))
            os.replace(tmp, dst)
        except InterruptedError:
            _unlink(tmp)
            raise
        except OSError as e:
            _unlink(tmp)
            if isinstance(e, PermissionError) and sys.platform == "win32" and getattr(e, "winerror", None) in (5, 32, 33):
                self.r["busy"] += 1
                return False
            self._problem(rel, e)
            return False
        self.r["down"] += 1
        self.r["bytes"] += size
        self.progress(done_bytes=size)
        return True

    def delete_remote(self, rel):
        try:
            self.d.fs.unlink(self._r(rel))
            self.r["deleted_remote"] += 1
            return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                return True
            self._problem(rel, e)
            return False

    def delete_local(self, rel):
        """Move a local file into the holding folder (never deleted outright)."""
        src = self._l(rel)
        if self.trash is None:
            self.trash = os.path.join(self.state_dir, "trash", self.job["id"], time.strftime("%Y%m%d-%H%M%S"))
        dst = os.path.join(self.trash, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            self.r["deleted_local"] += 1
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            self._problem(rel, e)
            return False

    # -------------------------------------------------- the pass
    def run(self):
        direction = MODES[self.mode][1]
        refused = getattr(self.d, "refuse", None)
        if refused is not None and refused(self.job):
            raise SyncError(refused(self.job))
        if direction != "down" and not os.path.isdir(self.local):
            raise SyncError(f"The folder {self.local} isn't there (a disk that isn't connected?). "
                            "Nothing was changed.")
        if direction == "down":
            os.makedirs(self.local, exist_ok=True)
        if self.d.fs._hidden(self.remote):
            raise SyncError(f"{self.remote} is hidden on this device (hidden folders); show it again to sync it.")
        rnode = self.d.index.resolve(self.remote)
        if rnode is not None and not rnode["is_dir"]:
            raise SyncError(f"{self.remote} on the drive is a file, not a folder.")
        if rnode is None:
            if direction == "down" and self.mode == "download-mirror":
                raise SyncError(f"{self.remote} doesn't exist on the drive. Nothing was changed.")
            if direction != "down":
                self.d.index.makedirs(self.remote)

        L, Ldirs = scan_local(self.local, self.excluded, self.problems)
        R, Rdirs = scan_remote(self.d, self.remote, self.excluded)
        self.r["files"], self.r["remote_files"] = len(L), len(R)
        if not L and direction != "down":
            moved = real_folder(self.local)
            self.problems.append(f"{self.local} is empty. Windows keeps this folder in {moved} (moved there by "
                                 "OneDrive or in its settings): change this pair to use that folder." if moved else
                                 f"{self.local} is empty, so there is nothing to copy.")
        getattr(self, "_" + self.mode.replace("-", "_"))(L, Ldirs, R, Rdirs)
        if direction != "down":        # how many files are still on their way to Discord
            R2, _ = scan_remote(self.d, self.remote, self.excluded)
            self.r["waiting"] = sum(1 for v in R2.values() if v[2]["state"] != "synced")
        return self.r

    def _plan(self, todo):
        self.progress(total=len(todo), total_bytes=sum(s for _, s in todo))

    # ---------- this computer -> drive
    def _up(self, L, Ldirs, R, Rdirs, exact, known=None):
        """known: {rel: (size, mtime)} of what this job has already copied (backup mode). A file that
        hasn't changed here since then is left alone, wherever its copy on the drive went."""
        uploads = []
        for rel, l in L.items():
            if same(l, R.get(rel)):
                if known is not None:
                    known[rel] = l
            elif known is not None and same(l, known.get(rel)):
                self.r["kept_away"] += 1         # moved, renamed, changed or deleted on the drive by its owner
            elif self._settled(rel, l):
                uploads.append((rel, l))
        self._plan([(rel, l[0]) for rel, l in uploads])
        for rel, l in sorted(uploads):
            self._check()
            if self.upload(rel, l) and known is not None:
                known[rel] = l
        for rel in sorted(Ldirs - Rdirs):
            if known is not None and not any(r == rel or r.startswith(rel + "/") for r, _ in uploads) \
                    and any(k.startswith(rel + "/") for k in known):
                continue                         # a folder that was backed up and then moved away on the drive
            self._check()
            try:
                self.d.index.makedirs(self._r(rel))
            except OSError as e:
                self._problem(rel, e)
        if not exact:
            return
        gone = [rel for rel in R if rel not in L]
        gone_dirs = [rel for rel in Rdirs if rel not in Ldirs]
        if len(gone) >= GUARD and not L and not Ldirs:
            # An empty source almost always means a disk that isn't there or a mistake: don't wipe the drive.
            self.r["skipped_deletes"] = len(gone)
            self.problems.append(f"This folder is empty, so the {len(gone)} file(s) in {self.remote} were not "
                                 "deleted. Delete them on the drive yourself if that is what you want.")
            return
        for rel in sorted(gone):
            self._check()
            self.delete_remote(rel)
        for rel in sorted(gone_dirs, key=lambda p: -p.count("/")):
            node = self.d.index.resolve(self._r(rel))
            if node is not None and node["is_dir"] and not self.d.index.children(node["id"], limit=1):
                try:
                    self.d.fs.rmdir(self._r(rel))
                except OSError as e:
                    self._problem(rel, e)

    def _backup(self, L, Ldirs, R, Rdirs):
        state_file = backup_state(self.state_dir, self.job["id"])
        try:
            with open(state_file, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("local") != self.local or st.get("remote") != self.remote:
                st = {}
        except (OSError, ValueError):
            st = {}
        known = {k: tuple(v) for k, v in (st.get("files") or {}).items() if k in L}
        try:
            self._up(L, Ldirs, R, Rdirs, exact=False, known=known)
        finally:
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"local": self.local, "remote": self.remote, "files": known}, f, separators=(",", ":"))
            os.replace(tmp, state_file)
        if self.r["kept_away"]:
            self.problems.append(f"{self.r['kept_away']} file(s) backed up earlier are no longer where the backup put "
                                 "them on the drive (moved, renamed or deleted there). They are not copied again "
                                 "unless they change here; “Copy everything again” puts them back.")

    def _mirror(self, L, Ldirs, R, Rdirs):
        self._up(L, Ldirs, R, Rdirs, exact=True)

    def _move(self, L, Ldirs, R, Rdirs):
        self._up(L, Ldirs, R, Rdirs, exact=False)
        # Delete what is safely in Discord (uploaded, same size and time) and hasn't changed since.
        emptied = set()
        for rel, l in sorted(L.items()):
            self._check()
            node = self.d.index.resolve(self._r(rel))
            if node is None or node["is_dir"] or node["state"] != "synced":
                continue
            src = self._l(rel)
            try:
                st = os.stat(src)
            except OSError:
                continue
            if not same((st.st_size, st.st_mtime), (node["size"], node["mtime"])):
                continue
            try:
                os.remove(src)
                self.r["moved"] += 1
                emptied.add(posixpath.dirname(rel))
            except OSError as e:
                if not (isinstance(e, PermissionError) and getattr(e, "winerror", None) in (32, 33)):
                    self._problem(rel, e)
        # Remove sub-folders the move left empty (never the folder itself).
        for rel in sorted(Ldirs, key=lambda p: -p.count("/")):
            if any(e == rel or e.startswith(rel + "/") for e in emptied):
                try:
                    os.rmdir(self._l(rel))
                except OSError:
                    pass

    # ---------- drive -> this computer
    def _down(self, L, Ldirs, R, Rdirs, exact):
        downloads = [(rel, rf) for rel, rf in R.items() if not same(rf, L.get(rel))]
        self._plan([(rel, rf[0]) for rel, rf in downloads])
        for rel, rf in sorted(downloads):
            self._check()
            self.download(rel, rf)
        for rel in sorted(Rdirs - Ldirs):
            os.makedirs(self._l(rel), exist_ok=True)
        if not exact:
            return
        gone = [rel for rel in L if rel not in R]
        if len(gone) >= GUARD and not R and not Rdirs:
            self.r["skipped_deletes"] = len(gone)
            self.problems.append(f"{self.remote} is empty on the drive, so the {len(gone)} file(s) here were "
                                 "not removed.")
            return
        for rel in sorted(gone):
            self._check()
            self.delete_local(rel)
        for rel in sorted(Ldirs - Rdirs, key=lambda p: -p.count("/")):
            try:
                os.rmdir(self._l(rel))
            except OSError:
                pass

    def _download(self, L, Ldirs, R, Rdirs):
        self._down(L, Ldirs, R, Rdirs, exact=False)

    def _download_mirror(self, L, Ldirs, R, Rdirs):
        self._down(L, Ldirs, R, Rdirs, exact=True)

    # ---------- both ways
    def _two_way(self, L, Ldirs, R, Rdirs):
        state_file = os.path.join(self.state_dir, f"{self.job['id']}.state.json")
        try:
            with open(state_file, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("local") != self.local or st.get("remote") != self.remote:
                st = {}
        except (OSError, ValueError):
            st = {}
        B = {k: tuple(v) for k, v in (st.get("files") or {}).items()}
        Bdirs = set(st.get("dirs") or [])
        newB = {}
        plan = []
        lost_local = not L and not Ldirs and len(B) >= GUARD
        lost_remote = not R and not Rdirs and len(B) >= GUARD
        for rel in sorted(set(L) | set(R) | set(B)):
            l, rf, b = L.get(rel), R.get(rel), B.get(rel)
            r = rf[:2] if rf else None
            if l is not None and not self._settled(rel, l):
                if b is not None:
                    newB[rel] = b
                continue
            if l and r:
                if same(l, r):
                    newB[rel] = l
                elif b is not None and same(r, b):
                    plan.append(("up", rel))
                elif b is not None and same(l, b):
                    plan.append(("down", rel))
                else:
                    plan.append(("conflict", rel))
            elif l:
                if b is None or not same(l, b):
                    plan.append(("up", rel))
                elif lost_remote:
                    newB[rel] = b
                    self.r["skipped_deletes"] += 1
                else:
                    plan.append(("del-local", rel))
            elif r:
                if b is None or not same(r, b):
                    plan.append(("down", rel))
                elif lost_local:
                    newB[rel] = b
                    self.r["skipped_deletes"] += 1
                else:
                    plan.append(("del-remote", rel))
        if self.r["skipped_deletes"]:
            side = "This folder" if lost_local else f"{self.remote} on the drive"
            self.problems.append(f"{side} is empty, so nothing was deleted on the other side. Delete the files "
                                 "there yourself if that is what you want.")
        self._plan([(rel, (L.get(rel) or R.get(rel) or (0,))[0]) for a, rel in plan if a in ("up", "down", "conflict")])

        def save():
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"local": self.local, "remote": self.remote, "files": newB,
                           "dirs": sorted(dirs_now)}, f, separators=(",", ":"))
            os.replace(tmp, state_file)

        dirs_now = set(Bdirs)
        try:
            for action, rel in plan:
                self._check()
                if action == "up":
                    if self.upload(rel, L[rel]):
                        newB[rel] = L[rel]
                elif action == "down":
                    if self.download(rel, R[rel]):
                        newB[rel] = R[rel][:2]
                elif action == "del-local":
                    if not self.delete_local(rel):
                        newB[rel] = B[rel]
                elif action == "del-remote":
                    if not self.delete_remote(rel):
                        newB[rel] = B[rel]
                else:
                    self._conflict(rel, L[rel], R[rel], newB)
            # Folders: new ones are created on the other side; one removed on one side goes on the
            # other once nothing is left in it.
            one_side = Ldirs ^ Rdirs
            removed = {rel for rel in one_side if rel in Bdirs and not (lost_local or lost_remote)}
            for rel in sorted(one_side - removed, key=lambda p: p.count("/")):
                if rel in Ldirs:
                    self.d.index.makedirs(self._r(rel))
                else:
                    os.makedirs(self._l(rel), exist_ok=True)
            for rel in sorted(removed, key=lambda p: -p.count("/")):
                if rel in Ldirs:
                    try:
                        os.rmdir(self._l(rel))        # only when empty
                    except OSError:
                        pass
                    continue
                node = self.d.index.resolve(self._r(rel))
                if node is not None and node["is_dir"] and not self.d.index.children(node["id"], limit=1):
                    try:
                        self.d.fs.rmdir(self._r(rel))
                    except OSError:
                        pass
            L2, Ld2 = scan_local(self.local, self.excluded, [])
            _, Rd2 = scan_remote(self.d, self.remote, self.excluded)
            dirs_now = Ld2 & Rd2
            for rel in list(newB):
                if rel not in L2:
                    newB.pop(rel)
        finally:
            save()

    def _conflict(self, rel, l, rf, newB):
        """Both sides changed: the newer one keeps the name, the other is kept as a conflict copy."""
        self.r["conflicts"] += 1
        copy = conflict_copy_name(rel, self.host)
        try:
            if l[1] >= rf[1]:
                self.d.fs.rename(self._r(rel), self._r(copy))
                node = self.d.index.resolve(self._r(copy))
                if self.upload(rel, l):
                    newB[rel] = l
                if node is not None and self.download(copy, (node["size"], node["mtime"], node)):
                    newB[copy] = (node["size"], node["mtime"])
            else:
                os.replace(self._l(rel), self._l(copy))
                st = os.stat(self._l(copy))
                if self.download(rel, rf):
                    newB[rel] = rf[:2]
                if self.upload(copy, (st.st_size, st.st_mtime)):
                    newB[copy] = (st.st_size, st.st_mtime)
            log.info("Sync %s: %s was changed on both sides; the older one is kept as %s",
                     self.job["name"], rel, copy)
        except OSError as e:
            self._problem(rel, e)


def _unlink(p):
    try:
        os.remove(p)
    except OSError:
        pass


# ------------------------------------------------------------------ watching folders (Windows)
class _WinWatch:
    """Tells us when anything changes under a folder (FindFirstChangeNotification)."""

    def __init__(self, path, callback):
        self.path, self.callback = path, callback
        self._stop = threading.Event()
        self.ok = False
        self._thread = threading.Thread(target=self._run, name="SyncWatch", daemon=True)
        self._thread.start()

    def _run(self):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        k32.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        k32.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        flags = 0x1 | 0x2 | 0x8 | 0x10          # file name, folder name, size, last write
        h = k32.FindFirstChangeNotificationW(self.path, True, flags)
        if not h or h == wintypes.HANDLE(-1).value:
            return
        self.ok = True
        try:
            while not self._stop.is_set():
                r = k32.WaitForSingleObject(h, 1000)
                if r == 0:
                    self.callback()
                    if not k32.FindNextChangeNotification(h):
                        break
                elif r != 0x102:                    # not a timeout: the folder went away
                    break
        finally:
            self.ok = False
            k32.FindCloseChangeNotification(h)

    def close(self):
        self._stop.set()


# ------------------------------------------------------------------ the scheduler
class SyncManager:
    """Runs the jobs of this device inside the drive process, one at a time."""

    def __init__(self, drive):
        self.d = drive
        self.cfg = drive.cfg
        self.dir = sync_dir(drive.cfg)
        os.makedirs(self.dir, exist_ok=True)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._cfg_mtime = None
        self._watches = {}           # job id -> (local path, watcher)
        self._dirty = {}             # job id -> time of the first change not synced yet
        self._requests = set()       # job ids asked to run now
        self._cancel = None          # id of the job asked to stop
        self.running = None
        self._last_save = 0.0
        self._last_trash = 0.0
        saved = read_status(drive.cfg) if not getattr(drive, "local_test_dir", None) else {}
        self.status = {jid: s for jid, s in (saved.get("jobs") or {}).items() if isinstance(s, dict)}
        for s in self.status.values():
            if s.get("state") == "running":
                s["state"] = "idle"
            s.pop("progress", None)
        self.jobs = load_jobs(drive.cfg)
        self._seen()
        drive.fs.change_listeners.append(self._on_remote_change)

    # -------------------------------------------------- outside interface
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="Sync", daemon=True)
        self._thread.start()
        self._update_watches()
        return self

    def stop(self):
        self._stop.set()
        self._cancel = self.running
        self._wake.set()
        for _, w in self._watches.values():
            w.close()
        self._watches.clear()
        if self._thread:
            self._thread.join(timeout=5)
        self._save(force=True)

    def reload(self, jobs=None):
        self.jobs = list(jobs) if jobs is not None else load_jobs(self.d.cfg)
        known = {j["id"] for j in self.jobs}
        for jid in list(self.status):
            if jid not in known:
                self.status.pop(jid)
        self._seen()
        self._update_watches()
        self._wake.set()
        self._save(force=True)

    def _seen(self):
        """Remember when each job was first seen (a daily job's first run is the next time after it)."""
        for j in self.jobs:
            self.status.setdefault(j["id"], {"since": time.time()})

    def run_now(self, jid, again=False):
        if again:
            _unlink(backup_state(self.dir, jid))
        self._requests.add(jid)
        self._wake.set()

    def cancel(self, jid):
        if self.running == jid:
            self._cancel = jid
        self._requests.discard(jid)

    def job(self, jid):
        return next((j for j in self.jobs if j["id"] == jid), None)

    def snapshot(self):
        """Jobs with their status, for the dashboard."""
        out = []
        now = time.time()
        for j in self.jobs:
            s = dict(self.status.get(j["id"]) or {})
            s.setdefault("state", "idle" if j["enabled"] else "paused")
            if not j["enabled"] and s["state"] != "running":
                s["state"] = "paused"
            s["next"] = self._next_time(j, now) if j["enabled"] else None
            s["watching"] = j["id"] in self._watches and self._watches[j["id"]][1].ok
            out.append({**j, "status": s, "label": MODES[j["mode"]][0], "direction": MODES[j["mode"]][1],
                        "when": when_text(j)})
        return out

    # -------------------------------------------------- events
    def _on_remote_change(self, nids):
        now = time.time()
        for j in self.jobs:
            if j["enabled"] and j["trigger"] == "live" and MODES[j["mode"]][1] in ("down", "both"):
                self._dirty.setdefault(j["id"], now)
        self._wake.set()

    def _on_local_change(self, jid):
        self._dirty.setdefault(jid, time.time())
        self._wake.set()

    def _update_watches(self):
        if sys.platform != "win32" or self._stop.is_set():
            return
        want = {j["id"]: j["local"] for j in self.jobs
                if j["enabled"] and j["trigger"] == "live" and MODES[j["mode"]][1] in ("up", "both")
                and os.path.isdir(j["local"])}
        for jid, (path, w) in list(self._watches.items()):
            if want.get(jid) != path:
                w.close()
                self._watches.pop(jid)
        for jid, path in want.items():
            if jid not in self._watches:
                try:
                    self._watches[jid] = (path, _WinWatch(path, lambda jid=jid: self._on_local_change(jid)))
                except Exception as e:
                    log.debug("Can't watch %s: %s", path, e)

    # -------------------------------------------------- scheduling
    def _next_time(self, j, now):
        s = self.status.get(j["id"]) or {}
        last = s.get("last_run") or 0
        retry = s.get("retry_at")
        t = j["trigger"]
        if t == "manual":
            nxt = None
        elif t == "live":
            watched = j["id"] in self._watches and self._watches[j["id"]][1].ok
            nxt = last + (LIVE_RESCAN if watched else LIVE_POLL)
            if j["id"] in self._dirty:
                nxt = min(nxt, max(self._dirty[j["id"]] + DEBOUNCE, last + MIN_GAP))
        elif t == "interval":
            nxt = last + j["every"] * 60 if last else now
        else:
            nxt = next_daily(j["at"], last or s.get("since", now))
        if retry and t != "manual":
            nxt = min(nxt, retry) if nxt else retry
        return nxt

    def _due(self, now):
        for jid in list(self._requests):
            j = self.job(jid)
            self._requests.discard(jid)
            if j is not None:
                return j
        for j in self.jobs:
            if not j["enabled"]:
                continue
            nxt = self._next_time(j, now)
            if nxt is not None and nxt <= now:
                return j
        return None

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self._poll_files()
                now = time.time()
                job = self._due(now)
                if job is not None:
                    self.run_job(job)
                if now - self._last_trash > 3600:
                    self._last_trash = now
                    self._clean_trash()
            except Exception as e:
                log.warning("Sync: %s", e, exc_info=True)
                time.sleep(5)

    def _poll_files(self):
        """Config changes and run / stop requests from the command line or the menu."""
        if getattr(self.d, "local_test_dir", None):
            return
        try:
            mtime = os.path.getmtime(config_path())
        except OSError:
            mtime = None
        if mtime != self._cfg_mtime:
            first = self._cfg_mtime is None
            self._cfg_mtime = mtime
            if not first:
                try:
                    self.d.cfg.sync_jobs = Config.load().sync_jobs
                    self.reload()
                except (Exception, SystemExit) as e:
                    log.warning("Sync: could not read the folders to sync from the config: %s", e)
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for name in names:
            if name.startswith(("run-", "cancel-", "again-")):
                _unlink(os.path.join(self.dir, name))
                kind, _, jid = name.partition("-")
                if kind == "cancel":
                    self.cancel(jid)
                else:
                    self.run_now(jid, again=kind == "again")

    # -------------------------------------------------- running a job
    def run_job(self, job, settle=SETTLE):
        jid = job["id"]
        s = self.status.setdefault(jid, {"since": time.time()})
        s.update(state="running", started=time.time(), error="", retry_at=None,
                 progress={"done": 0, "total": 0, "bytes": 0, "total_bytes": 0, "file": ""})
        self._dirty.pop(jid, None)
        self.running = jid
        self._cancel = None
        self._save(force=True)
        prog = s["progress"]
        within = [0]                  # bytes of the file being copied that are counted already

        def progress(file=None, action=None, size=None, total=None, total_bytes=None, done_bytes=None,
                     part_bytes=None, note=None):
            """part_bytes: that much more of the current file was copied; note: what it is waiting for ('' = nothing)."""
            if total is not None:
                prog["total"], prog["total_bytes"] = total, total_bytes or 0
            if file is not None:
                prog["file"], prog["action"] = file, action
                prog.pop("note", None)
                within[0] = 0
            if part_bytes:
                prog["bytes"] += part_bytes
                within[0] += part_bytes
            if note is not None:
                prog["note"] = note
            if done_bytes is not None:
                prog["done"] += 1
                prog["bytes"] += done_bytes - within[0]
                within[0] = 0
            self._save()

        run = Run(self.d, job, self.dir, settle=settle, stop=lambda: self._cancel == jid or self._stop.is_set(),
                  progress=progress)
        result, error = None, ""
        scope = self.d.fs.scope
        before = getattr(scope, "unlocked", None)
        try:
            from .locks import ALL
            scope.unlocked = ALL          # a synced folder is copied whether or not it is password-locked
            result = run.run()
        except InterruptedError:
            error = "Stopped before it was finished."
        except SyncError as e:
            error = str(e)
        except Exception as e:
            log.warning("Sync %s failed: %s", job["name"], e, exc_info=True)
            error = f"Failed: {e}"
        finally:
            scope.unlocked = before
            self.running = None
        now = time.time()
        res = result or run.r
        s.update(last_run=now, duration=round(now - s["started"], 1), last=res, problems=run.problems[:50],
                 error=error, state="error" if error and not error.startswith("Stopped") else "idle")
        s.pop("progress", None)
        if not error:
            s["last_ok"] = now
        if error and not error.startswith("Stopped"):
            s["retry_at"] = now + FAIL_RETRY
        elif res.get("busy") or (job["mode"] == "move" and res.get("waiting")):
            s["retry_at"] = now + RETRY
            if run.settled_at:
                s["retry_at"] = min(s["retry_at"], max(now + 1.0, run.settled_at + 0.5))
        hist = s.setdefault("history", [])
        hist.insert(0, {"at": now, "error": error, **{k: res.get(k, 0) for k in
                                                       ("up", "down", "deleted_remote", "deleted_local", "moved",
                                                        "conflicts", "errors", "bytes")}})
        del hist[HISTORY:]
        changed = sum(res.get(k, 0) for k in ("up", "down", "deleted_remote", "deleted_local", "moved", "conflicts"))
        if error:
            log.warning("Sync %s: %s", job["name"], error)
        elif changed or res.get("errors"):
            log.info("Sync %s: %d copied to the drive, %d copied here, %d deleted on the drive, %d removed here, "
                     "%d moved, %d conflict(s), %d problem(s)", job["name"], res["up"], res["down"],
                     res["deleted_remote"], res["deleted_local"], res["moved"], res["conflicts"], res["errors"])
        self._save(force=True)
        if self.d.journal is not None and changed:
            self.d.journal.wake()
        return res, error

    # -------------------------------------------------- housekeeping
    def _save(self, force=False):
        if getattr(self.d, "local_test_dir", None):
            return
        now = time.time()
        if not force and now - self._last_save < 1.0:
            return
        self._last_save = now
        with self._lock:
            try:
                tmp = os.path.join(self.dir, "status.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"time": now, "pid": os.getpid(), "running": self.running, "jobs": self.status},
                              f, separators=(",", ":"))
                os.replace(tmp, os.path.join(self.dir, "status.json"))
            except OSError as e:
                log.debug("Sync: can't save the status: %s", e)

    def _clean_trash(self):
        root = os.path.join(self.dir, "trash")
        cutoff = time.time() - TRASH_DAYS * 86400
        try:
            jobs = os.listdir(root)
        except OSError:
            return
        for jid in jobs:
            try:
                runs = os.listdir(os.path.join(root, jid))
            except OSError:
                continue
            for r in runs:
                p = os.path.join(root, jid, r)
                try:
                    if os.path.getmtime(p) < cutoff:
                        shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
