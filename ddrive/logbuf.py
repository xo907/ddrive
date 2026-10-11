"""The last few thousand log lines in memory, for the dashboard's Log tab (live, filterable)."""

import collections
import logging
import os
import re
import threading
import time

_LINE = re.compile(r"^(\d\d:\d\d:\d\d) \[(\w+)\] ([\w.]+): (.*)$")


class LogBuffer(logging.Handler):
    def __init__(self, size=5000):
        super().__init__(logging.INFO)
        self._lines = collections.deque(maxlen=size)
        self._seq = 0
        self._lock = threading.Lock()
        self.gen = 0                      # goes up when the log is cleared (open Log tabs then start afresh)
        self._file_size = None

    def _add(self, t, level, name, msg):
        with self._lock:
            self._seq += 1
            self._lines.append({"i": self._seq, "t": t, "l": level, "n": name.replace("ddrive.", ""), "m": msg})

    def emit(self, record):
        try:
            self._add(record.created, record.levelname, record.name, record.getMessage())
        except Exception:
            pass

    def since(self, after=0, limit=1000):
        with self._lock:
            out = [r for r in self._lines if r["i"] > after]
        return out[-limit:]

    def clear(self):
        with self._lock:
            self._lines.clear()
            self.gen += 1

    def follow_file(self, path):
        """Notice the log file having been emptied from outside (`log --clear`) and forget the lines here too."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if self._file_size is not None and size < self._file_size:
            self.clear()
        self._file_size = size

    def seed_from_file(self, path, lines=400):
        """Start with the end of the log file, so the tab isn't empty right after a start."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(path) - 256 * 1024))
                tail = f.read().splitlines()[-lines:]
        except OSError:
            return
        day = time.strftime("%Y-%m-%d ")
        for line in tail:
            m = _LINE.match(line)
            if not m:
                continue
            try:
                t = time.mktime(time.strptime(day + m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                t = time.time()
            if t > time.time() + 60:
                t -= 86400
            self._add(t, m.group(2), m.group(3), m.group(4))


BUFFER = LogBuffer()
_installed = False


def clear_file(path):
    """Empty the log file (the drive keeps writing to it; new lines follow at its start)."""
    try:
        with open(path, "r+", encoding="utf-8") as f:
            f.truncate(0)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def install(log_path=None):
    global _installed
    if _installed:
        return BUFFER
    _installed = True
    if log_path:
        BUFFER.seed_from_file(log_path)
    root = logging.getLogger("ddrive")
    root.addHandler(BUFFER)
    if root.getEffectiveLevel() > logging.INFO:
        root.setLevel(logging.INFO)          # the Log tab shows everything, not only warnings
    return BUFFER


# ------------------------------------------------------------------ what accounts do stays out of the log
# The log is the owner's (the Log page, the log file). An account's drive works in threads of its own,
# whose names start with QUIET; what they write (file names, mostly) is not logged, except errors.
QUIET = "acct:"


def tname(name):
    """The name for a new thread: marked as an account's when the thread starting it is one."""
    return QUIET + name if threading.current_thread().name.startswith(QUIET) else name


class quiet:
    """`with quiet():` the current thread, and the threads it starts meanwhile, work for an account."""

    def __enter__(self):
        me = threading.current_thread()
        self.was = me.name
        if not me.name.startswith(QUIET):
            me.name = QUIET + me.name
        return self

    def __exit__(self, *exc):
        threading.current_thread().name = self.was


class _NoAccounts(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.ERROR or not (record.threadName or "").startswith(QUIET)


_no_accounts = _NoAccounts()


def hush_accounts():
    """Make every place the log goes to (the Log page, the file, the terminal) leave accounts out."""
    for logger in (logging.getLogger(), logging.getLogger("ddrive")):
        for handler in logger.handlers:
            if _no_accounts not in handler.filters:
                handler.addFilter(_no_accounts)
