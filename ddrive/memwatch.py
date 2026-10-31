"""Says in the log what a host was doing when its memory went up a lot, before it runs out.

A host on a small server was killed for memory (1.1 GB, within 45 minutes of starting) with nothing in
its log about why. Now, from FIRST_AT on and each time it has grown by STEP more, one warning says: how
much it uses, the requests being answered (method, path without its query, for how long), the open
accounts' drives, and where in DDrive each busy thread is (the innermost line of DDrive's own code).
"""

import collections
import logging
import os
import sys
import threading
import time

log = logging.getLogger("ddrive.memory")

FIRST_AT = 400 * 1024 * 1024
STEP = 150 * 1024 * 1024
EVERY = 20.0

_active = {}                     # request number -> (method, path, started)
_lock = threading.Lock()
_n = 0
_PKG = os.path.dirname(os.path.abspath(__file__))


def begin(method, path):
    """A request is being answered (web._Handler._dispatch). Returns what end() takes."""
    global _n
    with _lock:
        _n += 1
        _active[_n] = (method, (path or "").split("?")[0][:120], time.time())
        return _n


def end(n):
    with _lock:
        _active.pop(n, None)


def _process_bytes():
    from . import resources
    m = resources._memory_windows() if sys.platform == "win32" else resources._memory_linux()
    return m.get("process") or 0, m.get("available") or 0, m.get("total") or 0


def _where(frame):
    """The innermost line of DDrive's own code in a thread's stack, 'file.py:123 function'."""
    while frame is not None:
        code = frame.f_code
        if code.co_filename.startswith(_PKG) and not code.co_filename.endswith("memwatch.py"):
            return f"{os.path.basename(code.co_filename)}:{frame.f_lineno} {code.co_name}"
        frame = frame.f_back
    return None


def report(rss, available, total, accounts=None):
    """The warning itself (also what a test looks at)."""
    now = time.time()
    with _lock:
        reqs = sorted(_active.values(), key=lambda r: r[2])
    names = {t.ident: t.name for t in threading.enumerate()}
    busy = collections.Counter()
    for ident, frame in sys._current_frames().items():
        where = _where(frame)
        if where:
            name = "".join(c for c in names.get(ident, "?") if not c.isdigit()).rstrip("-_ ") or "?"
            busy[f"{name} at {where}"] += 1
    mib = 1024 * 1024
    parts = [f"DDrive uses {rss // mib} MB of memory ({available // mib} MB free of {total // mib})."]
    parts.append("Requests being answered: " + ("; ".join(f"{m} {p} for {now - t:.0f} s" for m, p, t in reqs[:15])
                                                if reqs else "none") + ".")
    if accounts is not None:
        parts.append(f"Accounts with their drive open: {accounts}.")
    parts.append("Threads in DDrive: " + "; ".join(f"{w}" + (f" (x{c})" if c > 1 else "") for w, c in busy.most_common(25)) + ".")
    return " ".join(parts)


class MemoryWatch:
    def __init__(self, accounts_open=None):
        self._accounts_open = accounts_open
        self._stop = threading.Event()
        self._said = 0

    def start(self):
        threading.Thread(target=self._loop, name="MemoryWatch", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def check(self):
        rss, available, total = _process_bytes()
        if not rss:
            return None
        if rss < self._said - STEP:                  # (it went down again: say it again when it comes back up)
            self._said = max(0, rss)
        if rss < FIRST_AT or rss < self._said + STEP:
            return None
        self._said = rss
        try:
            accounts = self._accounts_open() if self._accounts_open else None
        except Exception:
            accounts = None
        text = report(rss, available, total, accounts)
        log.warning("%s", text)
        return text

    def _loop(self):
        while not self._stop.wait(EVERY):
            try:
                self.check()
            except Exception as e:
                log.debug("memory watch: %s", e)
