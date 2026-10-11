"""How to start DDrive again as a new process: from source (python -m ddrive) or as the
single-file Windows program (DDrive.exe, built with PyInstaller)."""

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))


def command(*args, windowless=False):
    """Command line that runs `ddrive <args>`."""
    if FROZEN:
        return [sys.executable, *args]
    exe = sys.executable
    if windowless and sys.platform == "win32":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.exists(w) else exe
    return [exe, "-m", "ddrive", *args]


def shell_command(*args):
    """The same as one string, for the Windows registry (autostart, Explorer menu)."""
    return " ".join(f'"{a}"' if (" " in a or not a) and not a.startswith('"') else a for a in command(*args))
