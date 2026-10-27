"""Which copies of DDrive this computer runs Lite from, so that an outdated one doesn't start.

Two copies of the program on one computer share its data folder and its sign-in. Started from the older
one (an old folder's run.bat, say), Lite shows a drive it can no longer read: an end-to-end encrypted
account has entries that version doesn't know, and every folder fails with "I/O device error". So each
copy that shows the drive or syncs writes down where it is (lite/copies.json in the data folder), and
one that is older than another copy still on this computer refuses to start, and says where the newer
one is. The copy Windows starts by itself (autostart) counts as well, whether or not it ran yet.

Versions are read from each copy's own files each time, so a copy that was updated (git pull) since
counts as what it is now. DDRIVE_ALLOW_OLD=1 starts an older one anyway.
"""

import json
import os
import re
import sys
import tempfile
import time

from . import __version__
from .config import default_data_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path():
    return os.path.join(default_data_dir(), "lite", "copies.json")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def version_at(root):
    """The version of the copy of DDrive in folder `root`, from its files; None when there isn't one."""
    try:
        with open(os.path.join(root, "ddrive", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    path = _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _counts(root):
    """Only a copy of the program as installed: not a frozen one (no folder of its own) and not one in the
    temporary folder (a test run, a checkout made for a moment)."""
    if getattr(sys, "frozen", False):
        return False
    tmp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    return not os.path.normcase(os.path.abspath(root)).startswith(tmp + os.sep)


def remember(root=ROOT):
    """This copy shows the drive or syncs on this computer: written down for the others to see."""
    if not _counts(root):
        return
    data = {k: v for k, v in _load().items() if version_at(k) and not _same(k, root)}
    data[os.path.abspath(root)] = {"version": __version__ if _same(root, ROOT) else version_at(root), "seen": int(time.time())}
    _save(data)


def autostart_root():
    """The folder of the copy Windows starts by itself when you log in (lite autostart on), if any."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            start = winreg.QueryValueEx(k, "DDriveLite")[0]
    except OSError:
        return None
    m = re.search(r"sys\.path\.insert\(0, r'([^']+)'\)", start or "")
    return m.group(1) if m else None


def parse(version):
    """'3.10.1' -> (3, 10, 1); anything else -> (0,). (As updates.parse: the lite-only package has no updates.py.)"""
    try:
        return tuple(int(x) for x in str(version).strip().split("."))
    except ValueError:
        return (0,)


def newer_copy(root=ROOT, version=__version__):
    """(folder, version) of the newest copy of DDrive on this computer that is newer than this one, or None."""
    if not _counts(root) or os.environ.get("DDRIVE_ALLOW_OLD"):
        return None
    best = None
    for other in [*_load(), autostart_root()]:
        if not other or _same(other, root):
            continue
        v = version_at(other)
        if v and parse(v) > parse(version) and (best is None or parse(v) > parse(best[1])):
            best = (os.path.abspath(other), v)
    return best


def refusal(root=ROOT, version=__version__):
    """What to say instead of starting, when this copy is outdated; None when it may start."""
    found = newer_copy(root, version)
    if not found:
        return None
    where, newer = found
    return (f"This copy of DDrive ({version}, in {os.path.abspath(root)}) is older than the one in {where} ({newer}), "
            f"so Lite doesn't start from it: it may not read your files right. Start Lite from {where} instead, "
            f"and delete this old copy. (To start it anyway: set DDRIVE_ALLOW_OLD=1.)")


def tell(message):
    """Said where it is seen: in the window, or, started without one (autostart, pythonw), in a message box."""
    if sys.stdout is not None:
        print(f"[ERROR] {message}")
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "DDrive Lite", 0x10)
        except Exception:
            pass
