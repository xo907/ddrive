"""The name this program had before it became DDrive, for computers that were set up under it.

Nothing is moved or rewritten on such a computer: its config and data folders are used where they
are, the old environment variables still work, and what the old name left on the system (the entry
that starts the drive with the computer, the Explorer menu, the running drive's pid file) is found
and replaced by the new one. A new installation never sees any of this.

(A few constants elsewhere in the code also still carry the old name: the markers of a drive in its
channel and the labels keys are derived with. Changing those would make existing drives unreadable.)
"""

import os
import subprocess
import sys

OLD = "DiscordDrive"
OLD_ENV = "DISCORDDRIVE_"
OLD_PID = "discorddrive.pid"
OLD_CRON_TAG = "# DiscordDrive autostart"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def app_dir(base, name):
    """<base>/<name>, or the old name's folder there when that is the one that exists."""
    new, old = os.path.join(base, name), os.path.join(base, OLD)
    return old if os.path.isdir(old) and not os.path.isdir(new) else new


def env(name, default=None):
    """An environment variable DDRIVE_<name>, also accepted under its old name."""
    value = os.environ.get("DDRIVE_" + name)
    return os.environ.get(OLD_ENV + name, default) if value is None else value


def pid_file(data_dir, name):
    """The pid file of the running drive: an old version that is still running wrote the old one."""
    new, old = os.path.join(data_dir, name), os.path.join(data_dir, OLD_PID)
    return old if os.path.exists(old) and not os.path.exists(new) else new


def old_autostart(remove=False) -> bool:
    """True when the computer still starts the drive through an entry made under the old name
    (which points at files that no longer exist). With `remove`, that entry is taken away."""
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                winreg.QueryValueEx(k, OLD)
                if remove:
                    winreg.DeleteValue(k, OLD)
                return True
        except OSError:
            return False
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        return False
    if OLD_CRON_TAG not in current:
        return False
    if remove:
        lines = [l for l in current.splitlines() if OLD_CRON_TAG not in l]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n" if lines else "", text=True, capture_output=True)
    return True


def old_context_menu(remove=False) -> bool:
    """The same for the Explorer right-click entries (Windows)."""
    if sys.platform != "win32":
        return False
    import winreg
    found = False
    for where in ("*", "Directory"):
        for what in ("Offline", "FreeSpace"):
            key = rf"Software\Classes\{where}\shell\{OLD}_{what}"
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
            except OSError:
                continue
            found = True
            if remove:
                for sub in (key + r"\command", key):
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                    except OSError:
                        pass
    return found
