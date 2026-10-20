"""Telling the owner that a newer DDrive is out, and updating to it from the dashboard.

Twice a day the drive asks GitHub which version is the newest (the version line of ddrive/__init__.py
on the main branch, and the versions of the Android apps that are published there: the VERSION files
next to DDrive.apk and FindX.apk): a few small requests that send nothing about the drive. Turned off with `update_check = false` in the settings.

How it is updated depends on how it was installed:

* a git checkout (run.bat / run.sh): from the dashboard, "Update now": `git pull --ff-only`, then the
  drive stops the way it always does (uploads finished, the index backed up, the drive letter / folder
  unmounted) and starts again with the new code, in the same process (os.execv), so a systemd service,
  a scheduled task or a terminal window carries on as it was;
* DDrive.exe or the .deb package: the dashboard links to the release to download;
* anything else (pip, a copy of the folder): the same link.

The computers that run DDrive Lite with this drive can be updated from here too, all at once, with no
need to sign in to each one ("Update all computers" on the Settings page, Computers, which updates
this drive as Update now does, too): every running Lite
asks the drive about once a minute whether it was told to update (and says which version it runs and
how it is installed). Told so, a git checkout does `git pull --ff-only` and starts again with the new
code by itself (litesync.Supervisor). The drive only says "update"; where the code comes from is the
computer's own git remote, so nobody who controls the drive can make a computer run other code.

The phone apps are published next to the code (android/release/DDrive.apk, findx/release/FindX.apk), each
with a VERSION file, which is what is compared with the version an app says it is.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

REPO = "xo907/ddrive"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
RELEASES = f"https://github.com/{REPO}/releases/latest"
APP_APK = f"https://github.com/{REPO}/raw/main/android/release/DDrive.apk"
FINDX_APK = f"https://github.com/{REPO}/raw/main/findx/release/FindX.apk"
CHECK_EVERY_S = 12 * 3600
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(version):
    """'3.10.1' -> (3, 10, 1); anything else -> (0,)."""
    try:
        return tuple(int(x) for x in str(version).strip().split("."))
    except ValueError:
        return (0,)


def newer(a, b):
    """True when version a is newer than b."""
    return parse(a) > parse(b)


def install_kind():
    """How this copy of DDrive was installed: git, exe, deb or other."""
    if getattr(sys, "frozen", False):
        return "exe"
    if ROOT.startswith("/opt/ddrive"):
        return "deb"
    if os.path.isdir(os.path.join(ROOT, ".git")) and shutil.which("git"):
        return "git"
    return "other"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": f"DDrive/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(512 * 1024).decode("utf-8", "replace")


def notes_between(changelog, current, latest):
    """The changelog's entries newer than `current`, up to `latest` (markdown, at most ~6000 characters)."""
    out, keep = [], False
    for line in changelog.splitlines():
        m = re.match(r"^## (\d+(?:\.\d+)+)", line)
        if m:
            keep = newer(m.group(1), current) and not newer(m.group(1), latest)
        if keep:
            out.append(line)
    return "\n".join(out).strip()[:6000]


class Updates:
    """What GitHub says is the newest, asked now and then in the background."""

    def __init__(self, cfg, fetch=_get, clock=time.time):
        self.cfg = cfg
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self.latest = None            # the newest DDrive's version, once known
        self.findx = None             # the newest FindX's version (the published FindX.apk)
        self.app = None               # the newest DDrive app's (the published DDrive.apk)
        self.notes = ""
        self.checked = 0.0
        self.error = ""
        self.updating = None          # what "Update now" is doing: None, or {"state", "message"}
        self._stop = threading.Event()

    def enabled(self):
        return getattr(self.cfg, "update_check", True) is not False

    def start(self):
        if self.enabled():
            threading.Thread(target=self._loop, name="UpdateCheck", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        if self._stop.wait(30):          # not while the drive is still starting
            return
        while True:
            self.check()
            if self._stop.wait(CHECK_EVERY_S):
                return

    def check(self):
        """Ask GitHub now. Returns the newest version (or None when it couldn't be asked)."""
        try:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', self._fetch(RAW + "ddrive/__init__.py"))
            latest = m.group(1) if m else None
            def published(path):
                try:
                    v = self._fetch(RAW + path).strip()
                    return v if re.fullmatch(r"\d+(\.\d+)+", v) else None
                except Exception:
                    return None
            findx, app = published("findx/release/VERSION"), published("android/release/VERSION")
            notes = ""
            if latest and newer(latest, __version__):
                try:
                    notes = notes_between(self._fetch(RAW + "CHANGELOG.md"), __version__, latest)
                except Exception:
                    notes = ""
            with self._lock:
                self.latest, self.findx, self.app, self.notes = latest, findx, app, notes
                self.checked, self.error = self._clock(), ""
            return latest
        except Exception as e:
            with self._lock:
                self.checked, self.error = self._clock(), str(e)[:200]
            log.debug("Update check failed: %s", e)
            return None

    def status(self):
        """For the owner's status: what is newer, and how it can be had (None when nothing is)."""
        with self._lock:
            out = {"current": __version__, "kind": install_kind(), "findx": self.findx, "findx_apk": FINDX_APK,
                   "app": self.app, "app_apk": APP_APK}
            if self.updating:
                out["updating"] = dict(self.updating)
            if self.latest and newer(self.latest, __version__):
                out.update({"latest": self.latest, "notes": self.notes, "download": RELEASES})
            return out

    # ------------------------------------------------------------ updating (a git checkout)

    def apps(self):
        """What any signed-in page may know: the newest published apps (for "a newer app is out")."""
        with self._lock:
            return {"app": self.app, "app_apk": APP_APK, "findx": self.findx, "findx_apk": FINDX_APK}

    def pull(self):
        """git pull --ff-only in the program's folder. Returns (ok, what git said)."""
        if install_kind() != "git":
            return False, "This copy wasn't installed with git: download the new version instead."
        try:
            from . import legacy
            legacy.move_remote(ROOT)
        except Exception:
            pass
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        said = (r.stdout + r.stderr).strip()[-2000:]
        return r.returncode == 0, said


class Computers:
    """The DDrive Lite computers that use this drive, as each last said (they ask about once a minute
    whether to update), and the word to update: a time, which each computer acts on once."""

    ONLINE_S = 180                # heard from within this long: connected
    KEEP_S = 7 * 86400            # not heard from for this long: off the list
    MAX = 500

    def __init__(self, path=None, clock=time.time):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        self._seen = {}           # (who, device) -> what it said
        self.at = 0.0             # when "update all" was last pressed
        try:
            with open(path, encoding="utf-8") as f:
                self.at = float(json.load(f).get("at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def heard(self, who, q):
        """A computer asked (who: '' for the owner, else the account it is signed in to). Returns the answer."""
        device = re.sub(r"[^0-9A-Za-z_-]", "", str(q.get("device") or ""))[:64]
        now = self.clock()
        if device:
            with self._lock:
                for key in [k for k, c in self._seen.items() if now - c["seen"] > self.KEEP_S]:
                    del self._seen[key]
                if (who, device) in self._seen or len(self._seen) < self.MAX:
                    self._seen[(who, device)] = {
                        "id": device, "who": who, "seen": now,
                        "name": str(q.get("name") or "").strip()[:60] or "A computer",
                        "version": str(q.get("version") or "")[:20], "kind": str(q.get("kind") or "")[:10],
                        "result": str(q.get("result") or "")[:300]}
        return {"at": self.at}

    def update_all(self):
        """Tell every computer to update (each does when it next asks, within about a minute)."""
        with self._lock:
            self.at = max(self.clock(), self.at + 0.001)
            if self.path:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"at": self.at}, f)
                os.replace(tmp, self.path)
            return self.at

    def listing(self):
        now = self.clock()
        with self._lock:
            out = [dict(c, online=now - c["seen"] < self.ONLINE_S) for c in self._seen.values() if now - c["seen"] <= self.KEEP_S]
        return {"at": self.at, "computers": sorted(out, key=lambda c: (not c["online"], c["name"].lower()))}


def version_on_disk():
    """The version of the code in the program's folder now (newer than what runs, after a git pull)."""
    try:
        with open(os.path.join(ROOT, "ddrive", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def restart_command():
    """How this process was started, to start it again with the new code (os.execv)."""
    orig = getattr(sys, "orig_argv", None)          # (Python 3.10+: exactly what was run)
    if orig:
        return [sys.executable] + list(orig[1:])
    return [sys.executable, "-m", "ddrive"] + sys.argv[1:]
