"""DDrive as a systemd service (Linux): its settings, one launcher only, and proof of life.

What a live server taught (2026-11): a soft memory limit (MemoryHigh) turned "too much memory" into an hour-long
silent hang that Restart= never saw; MemoryMax=75% of a 3.9 GB box with no swap was never reached before the whole
server froze; a cron @reboot line started a second copy outside the unit's limits; and a pre-rename
discorddrive.service restarted 1,700 times a boot. So, when the drive runs in ddrive.service:

* the unit gets a drop-in (UNIT_DROPIN) with a hard memory cap only, swap kept out, the OOM killer told to pick DDrive
  first (it restarts cleanly; a database or sshd doesn't), and a watchdog: systemd restarts DDrive when it stops
  answering, whatever the reason. The unit file itself isn't edited: the drop-in wins, and it is rewritten when
  DDrive's defaults change. It takes effect at the next start.
* the cron line `# DDrive autostart` is removed (the service starts it), and a leftover discorddrive.service whose
  ExecStart is `-m discorddrive` is disabled and removed.
* READY=1 goes to systemd once the drive is mounted; WATCHDOG=1 every half watchdog period, but only while the mounted
  drive and the dashboard answer (a stat of the mount goes through DDrive's own FUSE loop; the dashboard is asked over
  HTTP), so a hung drive is restarted, not kept alive by a timer.

Nothing here runs on Windows or macOS, or outside systemd (except `ddrive mount` saying the service exists).
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request

log = logging.getLogger("ddrive.service")

UNIT = "ddrive.service"
LEGACY_UNIT = "discorddrive.service"
CRON_TAG = "# DDrive autostart"        # (Lite's is "# DDrive Lite autostart": another program, left alone)
DROPIN_NAME = "50-ddrive-defaults.conf"
WATCHDOG_SEC = 180
CAP_MAX = 2 * 1024 ** 3


def linux():
    return sys.platform.startswith("linux")


def total_memory():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def memory_cap(total=None):
    """The hard cap: half the memory, at most 2 GB. 2 GB held the peaks of a busy host (1.1-1.6 GB) with room; half
    leaves the rest of a small shared server (databases, web apps) what it needs on a 2-4 GB box."""
    total = total_memory() if total is None else total
    cap = min(CAP_MAX, total // 2) if total else CAP_MAX
    return max(512 * 1024 ** 2, cap) // (1024 ** 2)          # (MiB)


def unit_settings(total=None):
    """The [Service] settings DDrive wants (the drop-in, and the shipped unit)."""
    return (f"[Service]\n"
            f"# Written by DDrive; it is rewritten when DDrive's defaults change. Your own settings: another drop-in.\n"
            f"# A hard cap only: past it DDrive is killed and Restart= brings it back in seconds. A soft limit\n"
            f"# (MemoryHigh) throttled it into an hour-long silent hang instead.\n"
            f"MemoryHigh=infinity\n"
            f"MemoryMax={memory_cap(total)}M\n"
            f"MemorySwapMax=0\n"
            f"# Short of memory, the OOM killer (or earlyoom) takes DDrive, which restarts cleanly, before databases or sshd.\n"
            f"OOMScoreAdjust=300\n"
            f"# Fewer malloc arenas: dozens of threads (long polls) otherwise each keep freed memory of their own.\n"
            f"Environment=MALLOC_ARENA_MAX=2\n"
            f"# Restarted when it stops answering, whatever the reason (the mounted drive and the dashboard are asked).\n"
            f"Type=notify\nNotifyAccess=all\nWatchdogSec={WATCHDOG_SEC}\nTimeoutStartSec=900\n"
            f"Restart=always\nRestartSec=10\n")


# ---------------------------------------------------------------- where we run
def running_in_unit():
    """True when this process belongs to ddrive.service (system or user)."""
    try:
        with open("/proc/self/cgroup") as f:
            return "/" + UNIT in f.read()
    except OSError:
        return False


def _systemctl(scope, *args):
    try:
        return subprocess.run(["systemctl", *scope, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def enabled_scope():
    """[] (system), ["--user"], or None: where ddrive.service is enabled."""
    if not linux():
        return None
    for scope in ([], ["--user"]):
        r = _systemctl(scope, "is-enabled", UNIT)
        if r is not None and r.stdout.strip() in ("enabled", "enabled-runtime", "linked", "alias"):
            return scope
    return None


def is_active(scope):
    r = _systemctl(scope, "is-active", UNIT)
    return r is not None and r.stdout.strip() in ("active", "activating", "reloading")


# ---------------------------------------------------------------- one launcher
def remove_cron_autostart():
    """Drop the `# DDrive autostart` @reboot line (the service starts the drive). True when one was removed."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if cur.returncode != 0 or CRON_TAG not in cur.stdout:
        return False
    keep = [line for line in cur.stdout.splitlines() if not line.rstrip().endswith(CRON_TAG)]
    try:
        r = subprocess.run(["crontab", "-"], input="\n".join(keep) + ("\n" if keep else ""), text=True,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode == 0:
        log.warning("Removed the cron @reboot line that started a second copy of DDrive (%s starts it).", UNIT)
        return True
    return False


def legacy_units(home=None):
    """[(scope, path)] of discorddrive.service files from before the rename that run `-m discorddrive`."""
    home = home or os.path.expanduser("~")
    out = []
    for scope, path in ((["--user"], os.path.join(home, ".config", "systemd", "user", LEGACY_UNIT)),
                        ([], os.path.join("/etc", "systemd", "system", LEGACY_UNIT))):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if any(line.strip().startswith("ExecStart") and "-m discorddrive" in line for line in text.splitlines()):
            out.append((scope, path))
    return out


def remove_legacy_units(home=None):
    removed = []
    for scope, path in legacy_units(home):
        if not scope and os.geteuid() != 0:
            continue
        _systemctl(scope, "disable", "--now", LEGACY_UNIT)
        try:
            os.remove(path)
        except OSError as e:
            log.warning("Could not remove the old %s (%s): %s", LEGACY_UNIT, path, e)
            continue
        _systemctl(scope, "daemon-reload")
        _systemctl(scope, "reset-failed", LEGACY_UNIT)
        log.warning("Removed the old %s (%s): it ran `python -m discorddrive`, which no longer exists, and restarted "
                    "forever.", LEGACY_UNIT, path)
        removed.append(path)
    return removed


def ensure_dropin(total=None):
    """Write the drop-in with DDrive's unit settings next to the unit that runs us. True when it changed."""
    system = os.geteuid() == 0 and _in_system_slice()
    base = "/etc/systemd/system" if system else os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")
    folder = os.path.join(base, UNIT + ".d")
    path = os.path.join(folder, DROPIN_NAME)
    text = unit_settings(total)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.warning("Could not write the service settings to %s: %s", path, e)
        return False
    _systemctl([] if system else ["--user"], "daemon-reload")
    log.warning("Updated %s's settings (%s: hard memory cap %s MiB, watchdog). They take effect at the next start.",
                UNIT, path, memory_cap(total))
    return True


def _in_system_slice():
    try:
        with open("/proc/self/cgroup") as f:
            return "/system.slice/" in f.read()
    except OSError:
        return False


# ---------------------------------------------------------------- talking to systemd
def notify(message):
    """sd_notify: tell systemd something (READY=1, WATCHDOG=1, STATUS=...). Nothing when not asked for."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode("utf-8"), addr)
        return True
    except OSError:
        return False


def _within(fn, seconds):
    """Did fn() finish (without raising) within `seconds`? (It goes on in its thread if not: that's the point.)"""
    done = threading.Event()
    ok = [False]

    def run():
        try:
            fn()
            ok[0] = True
        except Exception:
            ok[0] = False
        finally:
            done.set()
    threading.Thread(target=run, name="Liveness", daemon=True).start()
    return done.wait(seconds) and ok[0]


def alive(mount_point, web_port=None):
    """Does the drive answer? A stat of the mount runs through DDrive's own FUSE loop; the dashboard is asked over HTTP
    (any answer counts, a sign-in page too)."""
    if not _within(lambda: os.stat(mount_point), 30):
        return False
    if web_port:
        def ask():
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{web_port}/", timeout=25).read(64)
            except urllib.error.HTTPError:
                pass                                            # (an answer)
        if not _within(ask, 30):
            return False
    return True


def watch(mount_point, web_port=None, stop=None):
    """READY=1 once the drive is mounted, then WATCHDOG=1 while it answers. Runs in a thread; nothing without systemd."""
    if not os.environ.get("NOTIFY_SOCKET"):
        return
    stop = stop or threading.Event()

    def loop():
        while not stop.is_set() and not os.path.ismount(mount_point):
            stop.wait(1)
        if stop.is_set():
            return
        notify("READY=1")
        usec = int(os.environ.get("WATCHDOG_USEC") or 0)
        if not usec:
            return
        every = max(5.0, usec / 1e6 / 3)
        while not stop.wait(every):
            if alive(mount_point, web_port):
                notify("WATCHDOG=1")
            else:
                log.warning("The drive or its dashboard didn't answer: the watchdog ping is held back (systemd "
                            "restarts DDrive if this goes on).")
    threading.Thread(target=loop, name="SystemdWatchdog", daemon=True).start()


def on_mount(cfg, mount_point, stop=None):
    """What `ddrive mount` does about systemd before mounting (Linux)."""
    if not linux():
        return
    try:
        remove_legacy_units()
        if running_in_unit():
            ensure_dropin()
            remove_cron_autostart()
    except Exception as e:                                       # (never in the way of the drive itself)
        log.debug("service upkeep: %s", e)
    watch(mount_point, getattr(cfg, "web_port", None) if getattr(cfg, "web_enabled", False) else None, stop)
