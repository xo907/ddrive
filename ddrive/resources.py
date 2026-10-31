"""What the drive is using on this computer: processor, memory, disk. For Health in the dashboard.

Standard library only: /proc on Linux, two Windows calls through ctypes. A number that can't be had
on some system is left out (None) rather than guessed.
"""

import os
import shutil
import sys
import threading
import time

_STARTED = time.time()
_lock = threading.Lock()
_cpu = {"at": time.monotonic(), "used": time.process_time(), "pct": None}
_sizes = {"at": 0.0, "dir": None, "value": {}}
SIZES_EVERY = 30.0           # measuring folders means walking them: not on every request


def _process_cpu():
    """How much of one processor core the drive used lately, in percent (several cores: over 100)."""
    with _lock:
        now, used = time.monotonic(), time.process_time()
        if now - _cpu["at"] >= 2.0:          # several pages may ask at once: measure over a real stretch
            _cpu["pct"] = round(100.0 * (used - _cpu["used"]) / (now - _cpu["at"]), 1)
            _cpu["at"], _cpu["used"] = now, used
        return _cpu["pct"]


def _memory_linux():
    out = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["process"] = int(line.split()[1]) * 1024
        with open("/proc/meminfo") as f:
            info = {k: int(v.split()[0]) * 1024 for k, _, v in (line.partition(":") for line in f) if v.strip()}
        out["total"] = info.get("MemTotal")
        out["available"] = info.get("MemAvailable", info.get("MemFree"))
    except (OSError, ValueError, IndexError):
        pass
    return out


_WIN = None


def _win_api():
    """Windows' memory calls and their structures, made once: ctypes keeps every structure class made (asked for
    every 2 seconds by the status bot, a new one each time added up)."""
    global _WIN
    if _WIN is None:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                     "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                     "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                                                        "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        get = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        get.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        _WIN = (Counters, Status, k32, get)
    return _WIN


def _memory_windows():
    import ctypes
    out = {}
    try:
        Counters, Status, k32, get = _win_api()
        c = Counters()
        c.cb = ctypes.sizeof(c)
        if get(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            out["process"] = int(c.WorkingSetSize)
        s = Status()
        s.dwLength = ctypes.sizeof(s)
        if k32.GlobalMemoryStatusEx(ctypes.byref(s)):
            out["total"], out["available"] = int(s.ullTotalPhys), int(s.ullAvailPhys)
    except (OSError, AttributeError):
        pass
    return out


def _folder_bytes(path):
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _data_sizes(data_dir):
    """How much each part of the drive's own folder holds (measured now and then, not every time)."""
    with _lock:
        if _sizes["dir"] == data_dir and time.monotonic() - _sizes["at"] < SIZES_EVERY:
            return _sizes["value"]
    value = {"cache": _folder_bytes(os.path.join(data_dir, "cache")), "staging": _folder_bytes(os.path.join(data_dir, "staging")),
             "thumbs": _folder_bytes(os.path.join(data_dir, "thumbs")), "index": 0, "messages": 0}
    try:
        for name in os.listdir(data_dir):
            if name.startswith("index.db"):
                value["index"] += os.path.getsize(os.path.join(data_dir, name))
            elif name.startswith("chat.db"):
                value["messages"] += os.path.getsize(os.path.join(data_dir, name))
    except OSError:
        pass
    with _lock:
        _sizes.update(at=time.monotonic(), dir=data_dir, value=value)
    return value


# ---------------------------------------------------------------------- tidying up (buttons on the Resources page)
def _forget_sizes():
    with _lock:
        _sizes["at"] = 0.0


def clear_cache(drive):
    """Remove what was kept from files that were opened (not what is kept for offline use). Returns bytes freed."""
    freed = drive.cache.free_space() if drive.cache is not None else 0
    _forget_sizes()
    return freed


def clear_thumbnails(drive):
    """Remove the stored thumbnails (they are made again when pictures are looked at). Returns bytes freed."""
    root, freed = os.path.join(drive.cfg.resolved_data_dir, "thumbs"), 0
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            try:
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
            except OSError:
                pass
    _forget_sizes()
    return freed


PARTIAL_IDLE = 3600.0     # a half-sent file nothing has arrived for in this long is taken to be left behind
PARTIAL_QUIET = 60.0      # ...and one that got something this recently is being sent this moment
PARTIAL_ABANDONED = 3 * 86400.0   # ...and one nothing has arrived for in this long is removed by itself


def _partials(data_dir):
    """The files that parts of uploads are collected in (web.py, "a file sent in parts"): the drive's own
    and those of every account on it. Yields (path, size, seconds since something was added)."""
    folders = [os.path.join(data_dir, "partials")]
    try:
        with os.scandir(os.path.join(data_dir, "accounts")) as it:
            folders += [os.path.join(e.path, "partials") for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        pass
    now = time.time()
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        yield e.path, st.st_size, now - st.st_mtime
                    except OSError:
                        pass
        except OSError:
            pass


def half_sent(data_dir):
    """What half-sent files hold on this disk: all of them, and the ones that look left behind."""
    out = {"count": 0, "bytes": 0, "idle_count": 0, "idle_bytes": 0, "quiet_count": 0, "quiet_bytes": 0}
    for path, size, idle in _partials(data_dir):
        if not path.endswith(".part"):
            continue
        out["count"] += 1
        out["bytes"] += size
        if idle > PARTIAL_IDLE:
            out["idle_count"] += 1
            out["idle_bytes"] += size
        if idle > PARTIAL_QUIET:
            out["quiet_count"] += 1
            out["quiet_bytes"] += size
    return out


def clear_half_sent(drive, recent=False):
    """Remove the half-sent files nothing has arrived for in an hour (an upload that was given up, or
    whose page was closed). One that is still being sent to is left alone; one that waits for room and
    is removed here starts again from its beginning. With `recent`, also the ones that got something
    within the hour, all but those a part arrived for in the last minute: whoever asks knows that
    nothing of theirs is on its way. Returns (files, bytes freed)."""
    return remove_half_sent(drive.cfg.resolved_data_dir, PARTIAL_QUIET if recent else PARTIAL_IDLE)


def remove_half_sent(data_dir, idle_for):
    """Remove the half-sent files (and their notes) in `data_dir` that nothing has arrived for in
    `idle_for` seconds. Returns (files, bytes freed)."""
    count = freed = 0
    for path, size, idle in _partials(data_dir):
        if idle <= idle_for:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        if path.endswith(".part"):
            count += 1
            freed += size
    return count, freed


def cancel_waiting_uploads(drive):
    """Give up on the uploads that are waiting or stuck (not the ones being sent this moment): a changed
    file goes back to its last uploaded version, a file that was never uploaded is removed.
    Returns (cancelled, left alone because they are being sent or written right now, bytes freed)."""
    import json
    up, idx, fs = drive.uploader, drive.index, drive.fs
    with up._lock:
        active = set(up._active)
    cancelled = skipped = freed = 0
    for n in idx.unsynced_files():
        nid = n["id"]
        if nid in active or fs.is_busy(nid):
            skipped += 1
            continue
        with up._lock:
            if nid in up._active:            # a worker took it meanwhile
                skipped += 1
                continue
            up._pending.pop(nid, None)
            up._failures.pop(nid, None)
        rec = idx.kv_get(f"upload:{nid}")
        if rec:
            try:
                idx.release(up.record_mids(json.loads(rec)))
            except ValueError:
                pass
            idx.kv_delete(f"upload:{nid}")
        try:
            freed += os.path.getsize(fs.staging_path(nid))
        except OSError:
            pass
        chunks = idx.get_chunks(nid)
        path = idx.path_of(nid)
        with fs.lock:
            fs._chunk_maps.pop(nid, None)
            if chunks:
                idx.update(nid, state="synced", size=sum(c["size"] for c in chunks))
            else:
                idx.delete_node(nid)
        if chunks:
            fs._remove_staging(nid)
        else:      # never uploaded: its local copy is the only one, so it is kept in 'recovered'
            from .fs import keep_aside
            keep_aside(fs.staging_dir, fs.staging_path(nid), os.path.basename(path or f"{nid}.dat"))
        up.progress.pop(nid, None)
        cancelled += 1
    _forget_sizes()
    return cancelled, skipped, freed


def snapshot(drive):
    cfg = drive.cfg
    data_dir = cfg.resolved_data_dir
    memory = _memory_windows() if sys.platform == "win32" else _memory_linux()
    try:
        disk = shutil.disk_usage(data_dir)
        disk = {"total": disk.total, "free": disk.free}
    except OSError:
        disk = None
    try:
        load = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):        # no such thing on Windows
        load = None
    memory_cache = drive.cache is not None and getattr(drive.cache, "memory_mode", False)
    return {
        "cpu": _process_cpu(), "cores": os.cpu_count() or 1, "load": load,
        "memory": memory, "threads": threading.active_count(), "started": _STARTED,
        "disk": disk, "folder": data_dir, "sizes": _data_sizes(data_dir), "half_sent": half_sent(data_dir),
        "waiting": len(drive.index.unsynced_ids()) if drive.index is not None else 0,
        "limits": {"cache": 0 if memory_cache else cfg.cache_max_bytes, "staging": cfg.staging_max_bytes,
                   "min_free": cfg.min_free_disk_bytes, "memory_cache": getattr(drive.cache, "memory_bytes", 0) if memory_cache else 0},
    }
