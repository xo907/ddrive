"""Folder syncing on a computer that runs DDrive Lite: folders of this computer kept in step with
folders of the host's drive.

    lite sync on        say to the host that this computer syncs folders (and start doing it)
    lite sync off       stop, and take this computer off the host's Sync page
    lite sync status    whether it is on and running
    lite sync run       do the syncing in this window (what runs in the background otherwise)

Turning it on is all that is done here. The folders are chosen on the host's web dashboard, on its
Sync page, where this computer then shows up next to the host's own folders: add a pair, change it,
start it, pause it, see how it is doing. Everything said there is passed on to this computer, which
does the copying itself (it is the one that can read its folders), with the same modes and the same
rules as on the host (sync.py): it is the host's sync, run against a drive that is somewhere else.

It runs inside the program that shows the host's drive here (`lite mount`) while that is running,
and by itself otherwise. The pairs are kept on this computer, in <data dir>/lite/sync, with what
each of them has copied; the host only remembers them while it runs (syncdevices.py).

Turning it on lets whoever is signed in to the host's dashboard with the same 24 words choose any
folder of this computer to copy to the drive, or to copy drive folders into. That is the point of
it, and why it is off until it is turned on here.
"""

import errno
import json
import logging
import os
import posixpath
import sys
import threading
import time
import types

from . import sync
from .config import default_data_dir
from .lite import HostError

log = logging.getLogger("ddrive.litesync")

POLL_WAIT = 25.0          # how long the host may hold our question "anything new?" open
REPORT_EVERY = 20.0       # the host hears how the pairs are doing at least this often
RETRY = 15.0              # after the host couldn't be reached
RETRY_REFUSED = 600.0     # after the host didn't take our sign-in (asking often would lock us out)
LIST_SECONDS = 3.0        # how long what the host said is in a folder is trusted
KEEP_FREE = 1 << 30       # copying to this computer stops short of filling its disk
_ERRNO = {404: errno.ENOENT, 409: errno.EEXIST, 403: errno.EACCES, 400: errno.EINVAL, 507: errno.ENOSPC, 413: errno.EFBIG}


class _Stopped(Exception):
    pass


class _Changed(Exception):
    pass


class _Body:
    """A file on its way to the host: exactly the size announced, given up when the copy is stopped."""

    def __init__(self, f, size, stop):
        self.f, self.size, self.left, self.stop = f, size, size, stop

    def read(self, n=-1):
        if self.stop is not None and self.stop():
            raise _Stopped()
        if self.left <= 0:
            return b""
        data = self.f.read(self.left if n is None or n < 0 else min(n, self.left))
        if not data:
            raise _Changed()                 # it got shorter meanwhile
        self.left -= len(data)
        return data

    def seek(self, pos):
        self.f.seek(pos)
        self.left = self.size - pos


def _host(fn):
    """What the host refuses about one file or folder is that file's problem (the pass goes on); a
    host that can't be reached is not, and ends the pass."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HostError as e:
            if e.status in _ERRNO:
                raise OSError(_ERRNO[e.status], str(e)) from None
            raise
    wrapper.__name__ = fn.__name__
    return wrapper


class RemoteDrive:
    """The host's drive, as far as a sync pass uses a drive (sync.Run): its list of files (`index`)
    and reading and changing them (`fs`), each turned into requests to the host's dashboard."""

    local_test_dir = None
    journal = None

    def __init__(self, client, data_dir):
        self.client = client
        self.cfg = types.SimpleNamespace(resolved_data_dir=data_dir, sync_jobs=[], min_free_disk_bytes=KEEP_FREE)
        self.index = self.fs = self
        self.change_listeners = []
        self.scope = types.SimpleNamespace(unlocked=None)
        self.refuse = None
        self.parts = False                   # the host takes files in parts (2.4.0 on)
        self._hear = None
        self._lock = threading.Lock()
        self._lists = {}                     # folder -> (when, {name: node})

    # ------------------------------------------------------------ what is there
    @staticmethod
    def _node(path, name, is_dir, size, mtime, state):
        return {"id": path, "name": name, "is_dir": bool(is_dir), "size": size, "mtime": mtime, "state": state or "synced",
                "version": (size, mtime)}

    def _listing(self, folder, fresh=False):
        """{name: node} of a folder on the drive; None when there is no such folder."""
        with self._lock:
            hit = self._lists.get(folder)
            if hit and not fresh and time.monotonic() - hit[0] < LIST_SECONDS:
                return hit[1]
        try:
            items = {e["name"]: self._node(e["path"], e["name"], e["dir"], e["size"], e["mtime"], e.get("state"))
                     for e in self.client.list(folder)}
        except HostError as e:
            if e.status not in (400, 404):
                raise
            items = None
        with self._lock:
            self._lists[folder] = (time.monotonic(), items)
            if len(self._lists) > 2000:
                self._lists.pop(next(iter(self._lists)))
        return items

    def _forget(self, *paths):
        with self._lock:
            for path in paths:
                self._lists.pop(posixpath.dirname(path) or "/", None)
                self._lists.pop(path, None)

    def resolve(self, path, fresh=False):
        path = "/" + path.strip("/")
        if path == "/":
            return self._node("/", "", True, 0, 0, "synced")
        folder, name = posixpath.split(path)
        return (self._listing(folder, fresh) or {}).get(name)

    def get(self, nid):
        return self.resolve(nid, fresh=True)

    def children(self, nid, limit=-1):
        items = list((self._listing(nid, fresh=True) or {}).values())
        return items[:limit] if limit and limit > 0 else items

    def scan_tree(self, root_path, excluded):
        """sync.scan_remote, answered by the host in one go."""
        tree = self.client.tree(root_path)
        files, dirs, skipped = {}, set(), set()
        if not tree.get("dir"):
            return files, dirs
        for rel in tree["dirs"]:             # parents come before what is in them
            if posixpath.dirname(rel) in skipped or excluded(rel):
                skipped.add(rel)
            else:
                dirs.add(rel)
        for rel, size, mtime, state in tree["files"]:
            if posixpath.dirname(rel) not in skipped and not excluded(rel):
                path = posixpath.join(root_path, rel)
                files[rel] = (size, mtime, self._node(path, posixpath.basename(rel), False, size, mtime, state))
        return files, dirs

    def _hidden(self, path):
        return False

    # ------------------------------------------------------------ changing it
    @_host
    def makedirs(self, path):
        made = ""
        for part in path.strip("/").split("/"):
            made += "/" + part
            node = self.resolve(made)
            if node is None:
                try:
                    self.client.mkdir(made)
                except HostError as e:
                    if e.status != 409:
                        raise
                self._forget(made)
            elif not node["is_dir"]:
                raise OSError(errno.ENOTDIR, f"{made} on the drive is a file")

    def listen(self, hear):
        """Have a pass told how far a big file is and what it is waiting for (sync.Run)."""
        self._hear = hear

    @_host
    def put_file(self, path, src, mtime=None, stop=None):
        size = os.fstat(src.fileno()).st_size
        try:
            if self.parts:
                self.client.upload_parts(path, src, size, mtime=mtime, stop=stop, hear=self._hear)
            else:
                self.client.upload(path, _Body(src, size, stop), size, mtime=mtime)
        except _Stopped:
            self.client.drop()               # the host sees a copy that was cut off and doesn't keep it
            raise InterruptedError("stopped") from None
        except _Changed:
            self.client.drop()
            raise OSError(errno.EAGAIN, "it changed while it was being copied; trying again next time") from None
        folder, name = posixpath.split(path)
        with self._lock:                     # known without asking again: it is there now, not uploaded yet
            hit = self._lists.get(folder)
            if hit and hit[1] is not None:
                hit[1][name] = self._node(path, name, False, size, mtime, "pending")
        return size

    @_host
    def read_file(self, nid, pos, size):
        return self.client.read(nid, pos, size)

    @_host
    def unlink(self, path):
        self.client.delete(path)
        self._forget(path)

    rmdir = unlink

    @_host
    def rename(self, old, new):
        self.client.move(old, new)
        self._forget(old, new)


class Agent(sync.SyncManager):
    """The host's sync scheduler, run here: the pairs come from the host's Sync page instead of a
    config file, and how they are doing is told back to it."""

    def __init__(self, client, device, name, data_dir=None, mount_point=""):
        self.client, self.device, self.name, self.mount_point = client, device, name, mount_point
        self.online = False
        self.rev = 0                         # the number of the pairs' last change that we know of
        self.raw = []                        # the pairs as the host and we keep them
        self.refused = {}                    # pair id -> why its folder can't be used on this computer
        self._greeted = False
        self._report = threading.Event()
        self._threads = []
        drive = RemoteDrive(client, data_dir or os.path.join(default_data_dir(), "lite"))
        drive.refuse = lambda job: self.refused.get(job["id"])
        super().__init__(drive)
        try:
            with open(self._jobs_file(), encoding="utf-8") as f:
                saved = json.load(f)
            self.rev = int(saved.get("rev") or 0)
            self.apply([j for j in saved.get("jobs") or [] if isinstance(j, dict) and j.get("id")])
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    # ------------------------------------------------------------ the pairs
    def _jobs_file(self):
        return os.path.join(self.dir, "jobs.json")

    def _store(self):
        tmp = self._jobs_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"rev": self.rev, "jobs": self.raw}, f, indent=1)
        os.replace(tmp, self._jobs_file())

    def apply(self, raw):
        """Take the pairs as the host has them. Their folders are looked at here, where they are."""
        self.raw = [dict(j) for j in raw]
        jobs, refused = [], {}
        for j in self.raw:
            j = dict(sync.JOB_DEFAULTS, **j)
            local = os.path.expandvars(os.path.expanduser(str(j["local"] or "").strip().strip('"')))
            mp = self.mount_point or ""
            if not os.path.isabs(local) and not (sys.platform == "win32" and len(local) == 2 and local[1] == ":"):
                refused[j["id"]] = (f"“{j['local']}” isn't a whole path on {self.name}. Give one like "
                                    + ("C:\\Users\\you\\Documents." if sys.platform == "win32" else "/home/you/Documents."))
            else:
                j["local"] = local = sync.local_path(local)
                on_drive = (os.path.splitdrive(local)[0].upper() == mp[:2].upper() if sys.platform == "win32"
                            else sync._inside(local, mp)) if mp else False
                if on_drive:
                    refused[j["id"]] = f"{local} is the drive itself as {self.name} shows it ({mp}). Choose a folder of that computer."
                elif sync._inside(local, self.cfg.resolved_data_dir) or sync._inside(self.cfg.resolved_data_dir, local):
                    refused[j["id"]] = "That folder holds DDrive's own data; choose another one."
            jobs.append(j)
        self.refused = refused
        self.reload(jobs)

    def _take(self, got):
        jobs = [j for j in got.get("jobs") or [] if isinstance(j, dict) and j.get("id")]
        rev = int(got.get("rev") or 0)
        if jobs != self.raw or rev != self.rev:
            self.rev = rev
            if jobs != self.raw:
                self.apply(jobs)
            self._store()

    # ------------------------------------------------------------ talking to the host
    def hello(self):
        """Say to the host that this computer is there. Raises HostError with a message for people."""
        try:
            got = self.client.sync_call("hello", device=self.device, name=self.name, platform=sys.platform,
                                        home=os.path.expanduser("~"), rev=self.rev, jobs=self.raw)
        except HostError as e:
            if e.status == 404:
                raise HostError(404, f"The host at {self.client.address} runs a DDrive from before folder syncing for "
                                     "lite computers (2.2.0). Update it there first.") from None
            raise
        self._greeted = True
        try:
            self.d.parts = tuple(int(x) for x in str(got.get("version") or "0").split(".")[:3]) >= (2, 4, 0)
        except ValueError:
            self.d.parts = False
        self._take(got)
        return got

    def _do(self, c):
        what = c.get("do")
        if what == "run":
            self.run_now(str(c.get("id")), again=bool(c.get("again")))
        elif what == "stop":
            self.cancel(str(c.get("id")))
        elif what == "browse":
            try:
                result = sync.browse(c.get("path"), self.mount_point)
            except OSError as e:
                result = {"error": str(e)}
            self.client.sync_call("answer", device=self.device, req=c.get("req"), result=result)

    def _link(self):
        """Wait at the host for word: changed pairs, a pair to start or stop, a folder to list."""
        told = None
        while not self._stop.is_set():
            wait = RETRY
            try:
                if not self._greeted:
                    self.hello()
                if not self.online:
                    self.online, told = True, None
                    log.info("Folder syncing: connected to %s as %s", self.client.address, self.name)
                    self._report.set()
                    self._wake.set()
                got = self.client.sync_call("poll", device=self.device, rev=self.rev, wait=POLL_WAIT)
                if self._stop.is_set():
                    break
                self._take(got)
                for c in got.get("commands") or []:
                    self._do(c)
                continue
            except HostError as e:
                self.online = False
                if e.status == 404 and self._greeted:          # the host was restarted: it doesn't know us any more
                    self._greeted = False
                    continue
                self._greeted = False
                if e.status in (401, 429):
                    wait = RETRY_REFUSED
                if str(e) != told:
                    told = str(e)
                    log.warning("Folder syncing waits: %s", e)
            except Exception as e:
                self.online = False
                log.warning("Folder syncing: %s", e, exc_info=True)
            self._stop.wait(wait)
        self.online = False

    def _reporter(self):
        while not self._stop.is_set():
            self._report.wait(REPORT_EVERY)
            self._report.clear()
            if not self.online or self._stop.is_set():
                continue
            try:
                self.client.sync_call("report", device=self.device, running=self.running,
                                      status={j["id"]: j["status"] for j in self.snapshot()})
            except HostError:
                pass                             # the other thread notices and connects again
            except Exception as e:               # a pair's status changed while it was being read: next time
                log.debug("Folder syncing: report: %s", e)
            self._stop.wait(1.0)

    # ------------------------------------------------------------ the scheduler, here
    def start(self):
        super().start()
        for fn, name in ((self._link, "SyncLink"), (self._reporter, "SyncReport")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        super().stop()
        self._report.set()
        if self.online:                      # let go of the question we have open at the host
            try:
                self.client.sync_call("poll", device=self.device, rev=self.rev, wait=0)
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=5)

    def _poll_files(self):
        pass                                 # the pairs and what to do come from the host, not from files

    def _due(self, now):
        return super()._due(now) if self.online else None      # nothing can be copied without the host

    def _save(self, force=False):
        super()._save(force)
        self._report.set()


# ---------------------------------------------------------------------- keeping it running
def pid_file():
    return os.path.join(default_data_dir(), "lite-sync.pid")


def alive(pid):
    """Whether a process with this number is running."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass
    return True


def running_pid():
    """The process that does the folder syncing on this computer right now, or None."""
    try:
        with open(pid_file()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


class Supervisor:
    """Does the folder syncing for as long as it is turned on in the lite settings, in whichever
    program on this computer got to it first (the one showing the drive, or `lite sync run`)."""

    def __init__(self):
        self.agent = None
        self._sig = None
        self._stop = threading.Event()
        self._thread = None
        self._told = None

    def _claim(self):
        other = running_pid()
        if other not in (None, os.getpid()):
            return False
        if other is None:
            os.makedirs(default_data_dir(), exist_ok=True)
            with open(pid_file(), "w") as f:
                f.write(str(os.getpid()))
        return True

    def _release(self, bye=False):
        agent, self.agent = self.agent, None
        if agent is not None:
            agent.stop()
            if bye:                              # turned off: off the host's Sync page (said once more here, because
                try:                             # the agent may just have said hello again)
                    agent.client.sync_call("bye", device=agent.device)
                except Exception:
                    pass
        if running_pid() == os.getpid():
            try:
                os.remove(pid_file())
            except OSError:
                pass

    def check(self):
        """Start, stop or restart the syncing as the settings say. True while it is turned on."""
        from . import lite
        s = lite.load_settings()
        want = bool(s.get("sync") and s.get("host") and s.get("device"))
        sig = (s.get("host"), s.get("protected"), s.get("device"), s.get("mount_point"))
        if self.agent is not None and (not want or sig != self._sig):
            self._release(bye=not s.get("sync"))
        if want and self.agent is None and self._claim():
            try:
                self.agent = Agent(lite.client_from(s), s["device"], lite.device_name(),
                                   mount_point=s.get("mount_point") or "").start()
                self._sig, self._told = sig, None
                log.info("Folder syncing is on: its folders are chosen on the Sync page of %s", s["host"])
            except HostError as e:
                if str(e) != self._told:
                    self._told = str(e)
                    log.warning("Folder syncing can't start: %s", e)
        return want

    def start(self):
        def loop():
            while not self._stop.is_set():
                try:
                    self.check()
                except Exception as e:
                    log.warning("Folder syncing: %s", e, exc_info=True)
                self._stop.wait(2.0)
            self._release()
        self._thread = threading.Thread(target=loop, name="SyncSupervisor", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=8)
