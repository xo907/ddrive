"""DDrive Lite: use a drive that runs on another computer (the host), without running one here.

    lite connect <address>      sign in to the host's dashboard address (asks for your 24 words)
    lite mount [-m X:] [-b]     show the host's drive as a drive letter / folder on this computer
    lite stop                   take it away again
    lite autostart on|off       show it by itself when this computer starts
    lite sync on|off            let folders of this computer be synced with the drive; which ones is
                                chosen on the host's dashboard, on its Sync page (litesync.py)
    lite status | ls | get | put | disconnect, and lite menu for all of this as a menu

Nothing of the drive lives on this computer: no bot token, no encryption key, no index, no cache on
disk. Files are read from the host a part at a time as programs ask for them (so video plays and
seeks without being downloaded), and a file you save goes to a temporary file here only until it has
been handed to the host, which stores it on the drive like any other.

It talks to the host's web dashboard (the same requests the dashboard page makes), signed in the way
a browser is: with 24 words. The owner's recovery phrase shows the whole drive; the phrase of an
account on that dashboard shows that account's own storage. The words are kept encrypted for this
computer and account (secretbox.py), in lite.json next to where the full program keeps its config.
"""

import argparse
import collections
import errno
import getpass
import http.client
import json
import logging
import os
import posixpath
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

from . import secretbox
from .config import default_config_dir, default_data_dir, format_size, launcher, normalize_mount_point

log = logging.getLogger("ddrive.lite")

BLOCK = 4 * 1024 * 1024          # files are read from the host in parts of this size
CACHE_BYTES = 128 * 1024 * 1024  # parts kept in memory for re-reading and seeking back
LIST_SECONDS = 3.0               # how long a folder's listing is trusted
O_ACCMODE = 3
O_TRUNC = getattr(os, "O_TRUNC", 0x200)


class HostError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class HostClient:
    """The host's dashboard, as a program sees it. Keeps one connection per thread and signs in again
    by itself when the session has run out."""

    def __init__(self, address, phrase="", cookie="", timeout=60.0):
        address = address.strip().rstrip("/")
        u = urllib.parse.urlsplit(address if "://" in address else "http://" + address)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise HostError(0, f"'{address}' is not an address like http://192.168.1.20:8765 or https://drive.example.com")
        self.secure = u.scheme == "https"
        self.hostname, self.port = u.hostname, u.port or (443 if self.secure else 80)
        self.address = f"{u.scheme}://{u.netloc}"
        self.phrase, self.cookie = phrase, cookie
        self.timeout = timeout
        self._local = threading.local()
        self._login_lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    def _send(self, method, path, body, headers):
        for attempt in (0, 1):
            conn = getattr(self._local, "conn", None)
            if conn is None:
                cls = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
                conn = self._local.conn = cls(self.hostname, self.port, timeout=self.timeout)
            try:
                conn.request(method, path, body=body, headers=headers)
                return conn.getresponse()
            except (OSError, http.client.HTTPException) as e:
                conn.close()
                self._local.conn = None
                if attempt:                  # the first failure is usually just a connection the host closed meanwhile
                    raise HostError(0, f"The host at {self.address} can't be reached ({e})") from None
                if hasattr(body, "seek"):
                    body.seek(0)

    def request(self, method, path, body=None, headers=None, raw=False):
        """The answer's body (or, with `raw`, the open answer, which the caller reads to the end)."""
        for attempt in (0, 1):
            h = {"Cookie": f"dd_session={self.cookie}", **(headers or {})}
            if method == "POST":
                h["X-DD"] = "1"
            resp = self._send(method, path, body, h)
            if resp.status == 401 and attempt == 0 and self.phrase:
                resp.read()
                self.login(stale=h["Cookie"])
                if hasattr(body, "seek"):
                    body.seek(0)
                continue
            if raw and resp.status < 400:
                return resp
            data = resp.read()
            if resp.status >= 400:
                try:
                    message = json.loads(data).get("error") or ""
                except (ValueError, AttributeError):
                    message = ""
                raise HostError(resp.status, message or f"The host answered with HTTP {resp.status}")
            return data

    def login(self, stale=None):
        with self._login_lock:
            if stale is not None and stale != f"dd_session={self.cookie}":
                return                       # another thread signed in meanwhile
            form = urllib.parse.urlencode({"phrase": self.phrase})
            resp = self._send("POST", "/login", form, {"Content-Type": "application/x-www-form-urlencoded"})
            resp.read()
            cookie = ""
            for part in (resp.getheader("Set-Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "dd_session":
                    cookie = v
            if resp.status == 303 and cookie:
                self.cookie = cookie
                return
            if resp.status == 429:
                raise HostError(429, "Too many wrong tries from this computer. Wait a few minutes and try again.")
            if resp.status == 401:
                raise HostError(401, "The host doesn't know those 24 words. Use the drive's recovery phrase, or the "
                                     "phrase of your account on that dashboard.")
            if resp.status == 421:
                raise HostError(421, "The host doesn't answer to that name. Use its number address, or add the name to "
                                     "\"Domain names\" in the host's Settings.")
            raise HostError(resp.status, f"That address doesn't answer like a DDrive dashboard (HTTP {resp.status})")

    def _json(self, path, body=None):
        if body is None:
            return json.loads(self.request("GET", path) or b"{}")
        return json.loads(self.request("POST", path, json.dumps(body), {"Content-Type": "application/json"}) or b"{}")

    @staticmethod
    def _q(**params):
        return "?" + urllib.parse.urlencode(params)

    # ------------------------------------------------------------ the drive
    def status(self):
        return self._json("/api/status")

    def list(self, path):
        return self._json("/api/list" + self._q(path=path))["items"]

    def read(self, path, start, size):
        if size <= 0:
            return b""
        try:
            return self.request("GET", "/api/file" + self._q(path=path), headers={"Range": f"bytes={start}-{start + size - 1}"})
        except HostError as e:
            if e.status == 416:              # past the end
                return b""
            raise

    def download(self, path, out, progress=None):
        resp = self.request("GET", "/api/file" + self._q(path=path, dl=1), raw=True)
        done = 0
        while True:
            data = resp.read(1024 * 1024)
            if not data:
                return done
            out.write(data)
            done += len(data)
            if progress:
                progress(done)

    def upload(self, path, src, size, mtime=None):
        """mtime: the file's own modification time, which a folder sync keeps (it tells copies apart by it)."""
        q = self._q(path=path) if mtime is None else self._q(path=path, mtime=repr(float(mtime)))
        return json.loads(self.request("POST", "/api/upload" + q, src,
                                       {"Content-Length": str(size), "Content-Type": "application/octet-stream"}))

    def tree(self, path):
        """Everything under a folder of the drive: {exists, dir, files: [[path in it, size, time, state]], dirs}."""
        return self._json("/api/sync/tree" + self._q(path=path))

    def sync_call(self, what, **body):
        """Folder syncing on this computer, as said to the host (litesync.py)."""
        return self._json("/api/lite/sync/" + what, body)

    def drop(self):
        """Close this thread's connection (after a request that was given up half-way)."""
        conn, self._local.conn = getattr(self._local, "conn", None), None
        if conn is not None:
            conn.close()

    def mkdir(self, path):
        self._json("/api/mkdir", {"path": path})

    def move(self, src, dst):
        self._json("/api/move", {"from": src, "to": dst})

    def delete(self, path):
        self._json("/api/delete", {"path": path})


# ---------------------------------------------------------------------- the drive letter / folder
try:
    from .fuse_loader import FUSE_ERROR, fuse, unmount
    Operations, FuseOSError = fuse.Operations, fuse.FuseOSError
except Exception as e:                       # no FUSE on this system: everything but `mount` still works
    FUSE_ERROR, fuse, unmount = str(e), None, None
    Operations = object

    class FuseOSError(OSError):
        def __init__(self, code):
            super().__init__(code, os.strerror(code))

_ERRNO = {404: errno.ENOENT, 409: errno.EEXIST, 403: errno.EACCES, 400: errno.EINVAL, 507: errno.ENOSPC, 413: errno.EFBIG}


def _translated(fn):
    """A host error becomes the error a program expects from a file system."""
    def wrapper(self, *args, **kwargs):
        log.debug("%s %s", fn.__name__, args[:1] + args[2:3] if fn.__name__ == "write" else args)
        try:
            return fn(self, *args, **kwargs)
        except HostError as e:
            if e.status not in (404,):
                log.warning("%s %s: %s", fn.__name__, args[0] if args else "", e)
            raise FuseOSError(_ERRNO.get(e.status, errno.EIO)) from None
        except OSError as e:
            if e.errno not in (errno.ENOENT, errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR):
                log.warning("%s %s: %s", fn.__name__, args[0] if args else "", e, exc_info=type(e) is not FuseOSError)
            raise
        except Exception as e:
            log.warning("%s %s failed: %s", fn.__name__, args[0] if args else "", e, exc_info=True)
            raise FuseOSError(errno.EIO) from None
    wrapper.__name__ = fn.__name__
    return wrapper


class _Open:
    """An open file. Reading needs nothing here; the first write brings in a temporary file."""

    def __init__(self, path, entry, writable):
        self.path, self.entry, self.writable = path, entry, writable
        self.tmp = None
        self.dirty = False
        self.lock = threading.RLock()


class LiteFS(Operations):
    def __init__(self, client, tmp_dir=None, block=BLOCK, cache_bytes=CACHE_BYTES):
        self.client = client
        self.block, self.cache_bytes = block, cache_bytes
        self.tmp_dir = tmp_dir or os.path.join(default_data_dir(), "lite-tmp")
        shutil.rmtree(self.tmp_dir, ignore_errors=True)       # left over from a run that was cut off
        os.makedirs(self.tmp_dir, exist_ok=True)
        self.lock = threading.RLock()
        self._lists = {}                     # folder -> (when, {name: entry})
        self._blocks = collections.OrderedDict()               # (path, size, mtime, n) -> bytes
        self._blocks_size = 0
        self._handles = {}
        self._next_fh = 0
        self._writing = {}                   # path -> the open file being written (not on the host yet)

    # ------------------------------------------------------------ what is there
    def _listing(self, folder):
        with self.lock:
            hit = self._lists.get(folder)
            if hit and time.monotonic() - hit[0] < LIST_SECONDS:
                return hit[1]
        items = {e["name"]: e for e in self.client.list(folder)}
        with self.lock:
            self._lists[folder] = (time.monotonic(), items)
            if len(self._lists) > 2000:
                self._lists.pop(next(iter(self._lists)))
        return items

    def _entry(self, path):
        path = "/" + path.strip("/")
        if path == "/":
            return {"name": "", "path": "/", "dir": True, "size": 0, "mtime": 0}
        folder, name = posixpath.split(path)
        items = self._listing(folder)
        entry = items.get(name)
        if entry is None:                    # Windows asks without caring about capitals
            entry = next((e for n, e in items.items() if n.lower() == name.lower()), None)
        if entry is None:
            raise FuseOSError(errno.ENOENT)
        return entry

    def _changed(self, *paths):
        with self.lock:
            for path in paths:
                self._lists.pop(posixpath.dirname("/" + path.strip("/")) or "/", None)
                self._lists.pop("/" + path.strip("/"), None)
                for key in [k for k in self._blocks if k[0] == path]:
                    self._blocks_size -= len(self._blocks.pop(key))

    @staticmethod
    def _attrs(is_dir, size, mtime):
        mtime = mtime or time.time()
        return {"st_mode": (stat.S_IFDIR if is_dir else stat.S_IFREG) | 0o777, "st_nlink": 2 if is_dir else 1,
                "st_size": 0 if is_dir else size, "st_mtime": mtime, "st_atime": mtime, "st_ctime": mtime,
                "st_birthtime": mtime, "st_uid": os.getuid() if hasattr(os, "getuid") else 0,
                "st_gid": os.getgid() if hasattr(os, "getgid") else 0}

    @_translated
    def getattr(self, path, fh=None):
        with self.lock:
            h = self._writing.get(path)
        if h is not None:
            with h.lock:                     # (waits while the file is being handed to the host)
                if h.tmp is not None:
                    return self._attrs(False, os.fstat(h.tmp.fileno()).st_size, time.time())
        e = self._entry(path)
        return self._attrs(e["dir"], e["size"], e["mtime"])

    @_translated
    def readdir(self, path, fh):
        if not self._entry(path)["dir"]:
            raise FuseOSError(errno.ENOTDIR)
        names = set(self._listing("/" + path.strip("/")))
        with self.lock:
            names.update(posixpath.basename(p) for p in self._writing if posixpath.dirname(p) == "/" + path.strip("/"))
        return [".", ".."] + sorted(names)

    # ------------------------------------------------------------ reading
    def _block(self, entry, n):
        key = (entry["path"], entry["size"], entry["mtime"], n)
        with self.lock:
            data = self._blocks.get(key)
            if data is not None:
                self._blocks.move_to_end(key)
                return data
        data = self.client.read(entry["path"], n * self.block, self.block)
        with self.lock:
            if key not in self._blocks:
                self._blocks[key] = data
                self._blocks_size += len(data)
            while self._blocks_size > self.cache_bytes and len(self._blocks) > 1:
                self._blocks_size -= len(self._blocks.popitem(last=False)[1])
        return data

    def _handle(self, fh):
        with self.lock:
            h = self._handles.get(fh)
        if h is None:
            raise FuseOSError(errno.EBADF)
        return h

    def _new_handle(self, h):
        with self.lock:
            self._next_fh += 1
            self._handles[self._next_fh] = h
            return self._next_fh

    @_translated
    def open(self, path, flags):
        entry = self._entry(path)
        if entry["dir"]:
            raise FuseOSError(errno.EISDIR)
        h = _Open(entry["path"], entry, (flags & O_ACCMODE) != os.O_RDONLY)
        if h.writable and flags & O_TRUNC:
            self._materialize(h, empty=True)
        return self._new_handle(h)

    @_translated
    def read(self, path, size, offset, fh):
        h = self._handle(fh)
        with h.lock:
            if h.tmp is not None:
                h.tmp.seek(offset)
                return h.tmp.read(size)
        out, end = [], min(offset + size, h.entry["size"])
        while offset < end:
            n, start = divmod(offset, self.block)
            piece = self._block(h.entry, n)[start:start + end - offset]
            if not piece:
                break
            out.append(piece)
            offset += len(piece)
        return b"".join(out)

    # ------------------------------------------------------------ writing
    def _materialize(self, h, empty=False):
        """Give the open file its temporary copy: what the host has (unless it is being replaced)."""
        with h.lock:
            if h.tmp is None:
                h.tmp = tempfile.NamedTemporaryFile(dir=self.tmp_dir, prefix="up-")
                if not empty and h.entry is not None and h.entry["size"]:
                    self.client.download(h.entry["path"], h.tmp)
                else:
                    h.dirty = True
                with self.lock:
                    self._writing[h.path] = h
            elif empty:
                h.tmp.seek(0)
                h.tmp.truncate()
                h.dirty = True

    @_translated
    def create(self, path, mode, fi=None):
        path = "/" + path.strip("/")
        if not self._entry(posixpath.dirname(path))["dir"]:
            raise FuseOSError(errno.ENOTDIR)
        h = _Open(path, None, True)
        self._materialize(h, empty=True)
        return self._new_handle(h)

    @_translated
    def write(self, path, data, offset, fh):
        h = self._handle(fh)
        self._materialize(h)
        with h.lock:
            h.tmp.seek(offset)
            h.tmp.write(data)
            h.dirty = True
        return len(data)

    @_translated
    def truncate(self, path, length, fh=None):
        own = fh is None
        if own:
            entry = self._entry(path)
            h = _Open(entry["path"], entry, True)
        else:
            h = self._handle(fh)
        self._materialize(h, empty=length == 0)
        with h.lock:
            h.tmp.truncate(length)
            h.dirty = True
        if own:
            self._finish(h, close=True)
        return 0

    def _finish(self, h, close):
        """Hand a changed file to the host; with `close`, drop the temporary copy."""
        with h.lock:
            if h.tmp is None:
                return
            try:
                if h.dirty:
                    h.tmp.flush()
                    size = os.fstat(h.tmp.fileno()).st_size
                    h.tmp.seek(0)
                    self.client.upload(h.path, h.tmp, size)
                    h.dirty = False
                    self._changed(h.path)
                    log.info("Sent %s to the host (%s)", h.path, format_size(size))
            finally:
                if close:
                    h.tmp.close()            # a temporary file: closing removes it
                    h.tmp = None
                    with self.lock:
                        if self._writing.get(h.path) is h:
                            del self._writing[h.path]

    @_translated
    def flush(self, path, fh):
        self._finish(self._handle(fh), close=False)
        return 0

    @_translated
    def release(self, path, fh):
        with self.lock:
            h = self._handles.pop(fh, None)
        if h is not None:
            self._finish(h, close=True)
        return 0

    # ------------------------------------------------------------ changing
    @_translated
    def mkdir(self, path, mode):
        self.client.mkdir("/" + path.strip("/"))
        self._changed(path)
        return 0

    @_translated
    def rmdir(self, path):
        entry = self._entry(path)
        if not entry["dir"]:
            raise FuseOSError(errno.ENOTDIR)
        self._changed(entry["path"])
        if self._listing(entry["path"]):
            raise FuseOSError(errno.ENOTEMPTY)
        self.client.delete(entry["path"])
        self._changed(entry["path"])
        return 0

    @_translated
    def unlink(self, path):
        entry = self._entry(path)
        if entry["dir"]:
            raise FuseOSError(errno.EISDIR)
        self.client.delete(entry["path"])
        self._changed(entry["path"])
        return 0

    @_translated
    def rename(self, old, new):
        new = "/" + new.strip("/")
        entry = self._entry(old)
        try:
            there = self._entry(new)
        except FuseOSError:
            there = None
        if there is not None and there["path"] != entry["path"]:
            if there["dir"]:
                raise FuseOSError(errno.EEXIST)
            self.client.delete(there["path"])        # saving over a file: programs write a new one and rename it
        self.client.move(entry["path"], new)
        self._changed(entry["path"], new)
        return 0

    def utimens(self, path, times=None):
        return 0

    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def access(self, path, mode):
        return 0

    def statfs(self, path):
        bs, total = 4096, (1 << 50) // 4096
        return {"f_bsize": bs, "f_frsize": bs, "f_blocks": total, "f_bfree": total, "f_bavail": total,
                "f_files": 1 << 32, "f_ffree": 1 << 31, "f_favail": 1 << 31, "f_namemax": 255}


# ---------------------------------------------------------------------- settings and commands
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTOSTART_TAG = "# DDrive Lite autostart"


def me():
    """How this program is run here, for messages: 'run.bat lite', or the name of the lite-only program."""
    from .runtime import FROZEN
    if FROZEN:
        name = os.path.splitext(os.path.basename(sys.executable))[0]
        return name if "lite" in name.lower() else name + " lite"
    return ("ddrive-lite" if os.environ.get("DDRIVE_LITE_ONLY") else launcher() + " lite")


def _logging(verbose=False):
    handlers = [logging.StreamHandler(sys.stderr)] if sys.stderr is not None else []
    try:
        os.makedirs(default_data_dir(), exist_ok=True)
        handlers.append(logging.FileHandler(os.path.join(default_data_dir(), "ddrive.log"), mode="a", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers or None, force=True,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")


def settings_path():
    return os.environ.get("DDRIVE_LITE_CONFIG") or os.path.join(default_config_dir(), "lite.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path)


def client_from(s):
    if not s.get("host"):
        raise HostError(0, f"Not connected to a host yet. Run '{me()} connect <address>' first.")
    try:
        secret = json.loads(secretbox.unprotect(s.get("protected") or ""))
    except (secretbox.ProtectError, ValueError):
        raise HostError(0, "The saved sign-in can't be read on this computer or account. Run "
                           f"'{me()} connect {s['host']}' again.") from None
    if not secret.get("phrase"):             # saved by a version that signed in with a name and password
        raise HostError(0, f"Sign-in is by 24 words now. Run '{me()} connect {s['host']}' again.")
    return HostClient(s["host"], secret["phrase"], secret.get("cookie", ""))


def _pid_file():
    return os.path.join(default_data_dir(), "lite.pid")


def device_name():
    return (socket.gethostname() or "A computer").split(".")[0]


def _spawn(*argv):
    """Start `lite <argv>` as a program of its own that goes on after this one ends."""
    from .runtime import FROZEN, command
    if os.environ.get("DDRIVE_LITE_ONLY") and not FROZEN:        # the lite-only package has no `python -m ddrive`
        cmd = [sys.executable, "-c", "import sys; from ddrive.lite import main; sys.exit(main(sys.argv[1:]) or 0)", *argv]
    else:
        cmd = command("lite", *argv, windowless=True)
    cwd = None if FROZEN else SOURCE_ROOT        # from source, the program is found from its own folder
    os.makedirs(default_data_dir(), exist_ok=True)
    if sys.platform == "win32":
        return subprocess.Popen(cmd, cwd=cwd, close_fds=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                | getattr(subprocess, "DETACHED_PROCESS", 0))
    with open(os.path.join(default_data_dir(), "lite.log"), "a") as out:
        return subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, close_fds=True)


def _mount_pid():
    """The program showing the host's drive on this computer, if one is running."""
    from .litesync import alive
    try:
        with open(_pid_file()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


def _is_mounted(mp):
    if sys.platform == "win32":
        return os.path.exists(mp.rstrip(":\\") + ":\\")
    return os.path.ismount(mp)


def cmd_connect(args):
    address = args.address or input("Address of the host's dashboard (e.g. http://192.168.1.20:8765): ").strip()
    phrase = args.phrase or getpass.getpass("Your 24 words (the drive's recovery phrase, or your account's; input is hidden): ")
    phrase = " ".join(phrase.split())
    client = HostClient(address, phrase)
    client.login()
    st = client.status()
    user = "the owner" if st.get("role", "owner") == "owner" else f"account {st.get('account')}"
    s = load_settings()
    s.update(host=client.address, user=user,
             protected=secretbox.protect(json.dumps({"phrase": phrase, "cookie": client.cookie}).encode("utf-8")))
    s.setdefault("mount_point", normalize_mount_point(""))
    save_settings(s)
    print(f"[SUCCESS] Connected to {client.address} as {user}: DDrive {st.get('version')}, "
          f"{st['stats']['files']} files ({format_size(st['stats']['bytes'])}).")
    if not client.secure and client.hostname not in ("localhost", "127.0.0.1", "::1"):
        print("          This address is plain http: fine on your own network; over the internet use the host's https address.")
    print(f"          Next: '{me()} mount' shows it as {s['mount_point']} on this computer.")
    return 0


def cmd_mount(args):
    s = load_settings()
    client = client_from(s)
    mp = normalize_mount_point(args.mount or s.get("mount_point") or "")
    if args.mount and mp != s.get("mount_point"):
        s["mount_point"] = mp
        save_settings(s)
    if FUSE_ERROR and s.get("sync"):         # nothing to show the drive with, but folders sync without that
        print(f"[INFO] The drive can't be shown here ({FUSE_ERROR.splitlines()[0]}). Folder syncing runs without it.")
        return cmd_sync(argparse.Namespace(state="run", background=args.background, verbose=getattr(args, "verbose", False)))
    if FUSE_ERROR:
        print(f"[ERROR] The drive can't be shown here yet:\n{FUSE_ERROR}\n        ('lite ls', 'lite get' and 'lite put' work without it.)")
        return 1
    if _is_mounted(mp):
        print(f"[INFO] Something is already mounted on {mp}.")
        return 0
    if args.background:
        proc = _spawn("mount", "-m", mp)
        deadline = time.time() + 30
        while time.time() < deadline and not _is_mounted(mp) and proc.poll() is None:
            time.sleep(0.3)
        if _is_mounted(mp):
            print(f"[SUCCESS] The host's drive is on {mp}.")
            return 0
        print(f"[ERROR] It did not come up. See {os.path.join(default_data_dir(), 'ddrive.log')}")
        return 1
    _logging(getattr(args, "verbose", False))
    client.status()                          # fails here, with a clear message, rather than inside the mount
    if sys.platform != "win32":
        os.makedirs(mp, exist_ok=True)
    with open(_pid_file(), "w") as f:
        f.write(str(os.getpid()))
    kwargs = {"foreground": True, "fsname": "DDriveLite"}
    if sys.platform in ("win32", "darwin"):
        kwargs["volname"] = "DDrive"
    if sys.platform == "win32":
        # The files belong to whoever uses the drive. Without this Windows sees them as somebody else's
        # and lets programs create files but not change one that exists.
        kwargs.update(uid=-1, gid=-1)
    log.info("Showing the drive of %s on %s", client.address, mp)
    from .litesync import Supervisor
    syncing = Supervisor().start()           # folder syncing, when it is on, runs in here too
    try:
        fuse.FUSE(LiteFS(client), mp, **kwargs)
    finally:
        syncing.stop()
        try:
            os.remove(_pid_file())
        except OSError:
            pass
    return 0


def cmd_stop(args):
    mp = normalize_mount_point(load_settings().get("mount_point") or "")
    pid = None
    try:
        with open(_pid_file()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        if pid:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        if unmount:
            unmount(mp)
        if pid:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        if os.path.ismount(mp) and unmount:
            unmount(mp, lazy=True)
    try:
        os.remove(_pid_file())
    except OSError:
        pass
    for _ in range(20):
        if not _is_mounted(mp):
            break
        time.sleep(0.25)
    print(f"[SUCCESS] {mp} is gone from this computer." if not _is_mounted(mp) else f"[ERROR] {mp} is still there.")
    if load_settings().get("sync") and not _is_mounted(mp):
        _sync_ensure()                       # the folder syncing ran in the program that showed the drive
    return 0 if not _is_mounted(mp) else 1


# ---------------------------------------------------------------------- folder syncing (litesync.py)
def _sync_ensure():
    """Have something on this computer doing the folder syncing; True when it had to be started."""
    from .litesync import running_pid
    if running_pid() or _mount_pid():        # (the program showing the drive takes it up by itself)
        return False
    _spawn("sync", "run")
    return True


def _sync_running():
    from .litesync import running_pid
    for _ in range(25):
        if running_pid():
            return True
        time.sleep(0.2)
    return False


def cmd_sync(args):
    """Folders of this computer kept in step with the host's drive; they are chosen on the host's dashboard."""
    from . import litesync
    s = load_settings()
    action = args.state or "status"
    if action == "status":
        on = bool(s.get("sync"))
        print(f"[OK] Folder syncing is {'ON' if on else 'OFF'}"
              + (f" and {'running' if litesync.running_pid() else 'not running (start it: ' + me() + ' sync on)'}." if on else "."))
        if on:
            print(f"     Folders are added and changed on the Sync page of {s.get('host')}, where this computer is "
                  f"listed as {device_name()}.")
        return 0
    if action == "off":
        had = bool(s.get("sync"))
        if had:
            s["sync"] = False
            save_settings(s)
            try:
                client_from(s).sync_call("bye", device=s.get("device"))
            except HostError:
                pass                         # an unreachable host forgets by itself
        pid = litesync.running_pid()
        if pid and pid != _mount_pid():      # the program that only did the syncing ends by itself
            for _ in range(40):
                if not litesync.running_pid():
                    break
                time.sleep(0.25)
        print("[OK] Folder syncing is now OFF. This computer is off the host's Sync page; the folders it synced are kept "
              "in mind here and come back when it is turned on again." if had else "[OK] Folder syncing is OFF.")
        return 0
    client = client_from(s)                  # on and run: only when there is a host
    if action == "on":
        s.setdefault("device", secrets.token_hex(8))
        agent = litesync.Agent(client, s["device"], device_name(), mount_point=s.get("mount_point") or "")
        agent.hello()                        # fails here, with a clear message, when the host can't do this
        s["sync"] = True
        save_settings(s)
        _sync_ensure()
        ok = _sync_running()
        print(f"[OK] Folder syncing is now ON{'' if ok else ', but it did not start (see ' + os.path.join(default_data_dir(), 'ddrive.log') + ')'}.")
        print(f"     Open {client.address}, go to Sync, and add folders of {device_name()} there: everything about them\n"
              "     is done on that page. It lets whoever is signed in there with these 24 words choose any folder of\n"
              f"     this computer; '{me()} sync off' ends that.")
        if not autostart_enabled():
            print(f"     It runs until this computer is restarted. To have it start with the computer: '{me()} autostart on'.")
        return 0 if ok else 1
    # run: do the syncing in this program, until it is turned off or this is closed
    if not s.get("sync"):
        print(f"[INFO] Folder syncing is OFF. Turn it on first: '{me()} sync on'.")
        return 1
    if getattr(args, "background", False):
        started = _sync_ensure()
        print("[OK] Folder syncing " + ("started in the background." if started else "is already running."))
        return 0
    if litesync.running_pid() or _mount_pid():
        print("[INFO] Folder syncing is already running on this computer.")
        return 0
    _logging(getattr(args, "verbose", False))
    sup = litesync.Supervisor()
    try:
        while sup.check():
            time.sleep(2.0)
    finally:
        sup._release()
    return 0


def cmd_status(args):
    s = load_settings()
    if not s.get("host"):
        print(f"Not connected to a host. Run '{me()} connect <address>'.")
        return 0
    mp = normalize_mount_point(s.get("mount_point") or "")
    print(f"Host:        {s['host']} (signed in as {s.get('user')})")
    print(f"On this PC:  {mp} ({'shown' if _is_mounted(mp) else 'not shown; lite mount'})")
    if s.get("sync"):
        from .litesync import running_pid
        print(f"Folders:     syncing is on{'' if running_pid() else ', but not running (lite sync on starts it)'}; "
              "they are chosen on the host's Sync page")
    st = client_from(s).status()
    busy = len(st.get("uploads") or []) + (st.get("queued") or 0)
    print(f"Drive:       DDrive {st.get('version')}, {st['stats']['files']} files, {format_size(st['stats']['bytes'])}"
          f"{', uploading ' + str(busy) + ' to Discord' if busy else ', everything is on Discord'}")
    return 0


def cmd_ls(args):
    path = "/" + (args.path or "").strip("/")
    for e in client_from(load_settings()).list(path):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e["mtime"])) if e.get("mtime") else " " * 16
        print(f"{'<folder>' if e['dir'] else format_size(e['size']):>12}  {when}  {e['name']}")
    return 0


def _progress(name, total):
    last = [0.0]

    def show(done):
        if sys.stdout.isatty() and time.monotonic() - last[0] > 0.2:
            last[0] = time.monotonic()
            print(f"\r  {name}: {format_size(done)}{' of ' + format_size(total) if total else ''}   ", end="", flush=True)
    return show


def cmd_get(args):
    client = client_from(load_settings())
    remote = "/" + args.remote.strip("/")
    folder, name = posixpath.split(remote)
    entry = next((e for e in client.list(folder) if e["name"] == name), None)
    if entry is None:
        raise HostError(404, f"Not found on the host: {remote}")

    def fetch(e, target):
        if e["dir"]:
            os.makedirs(target, exist_ok=True)
            for child in client.list(e["path"]):
                fetch(child, os.path.join(target, child["name"]))
            return
        part = target + ".part"
        with open(part, "wb") as out:
            client.download(e["path"], out, _progress(e["name"], e["size"]))
        os.replace(part, target)
        print(f"\r  {e['path']} -> {target} ({format_size(e['size'])})")
    local = args.local or name
    fetch(entry, os.path.join(local, name) if os.path.isdir(local) and not entry["dir"] else local)
    return 0


def cmd_put(args):
    client = client_from(load_settings())
    local = os.path.abspath(args.local)
    if not os.path.exists(local):
        print(f"[ERROR] Not found: {local}")
        return 1
    base = "/" + (args.remote or "").strip("/")

    def send(src, dst):
        if os.path.isdir(src):
            try:
                client.mkdir(dst)
            except HostError as e:
                if e.status != 409:
                    raise
            for name in sorted(os.listdir(src)):
                send(os.path.join(src, name), posixpath.join(dst, name))
            return
        size = os.path.getsize(src)
        with open(src, "rb") as f:
            client.upload(dst, f, size)
        print(f"  {src} -> {dst} ({format_size(size)})")
    send(local, posixpath.join(base, os.path.basename(local)))
    return 0


def autostart_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, "DDriveLite")
                return True
        except OSError:
            return False
    try:
        return AUTOSTART_TAG in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        return False


def cmd_autostart(args):
    """Show the host's drive by itself when this computer starts (Windows: when you log in)."""
    from .runtime import FROZEN
    action = args.state or "status"
    if action == "status":
        print(f"[OK] Lite autostart is {'ON' if autostart_enabled() else 'OFF'}.")
        return 0
    on = action == "on"
    if on:
        client_from(load_settings())             # only when there is a host to connect to
    if sys.platform == "win32":
        import winreg
        if FROZEN:
            start = f'"{sys.executable}" lite mount -b'
        else:
            # No window: Python's windowless program runs the mount itself, from wherever Windows starts it.
            exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            exe = exe if os.path.exists(exe) else sys.executable
            start = (f'"{exe}" -c "import sys; sys.path.insert(0, r\'{SOURCE_ROOT}\'); '
                     f'from ddrive.lite import main; main([\'mount\'])"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            if on:
                winreg.SetValueEx(k, "DDriveLite", 0, winreg.REG_SZ, start)
            else:
                try:
                    winreg.DeleteValue(k, "DDriveLite")
                except OSError:
                    pass
    else:
        try:
            current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        except OSError:
            print("[ERROR] crontab is not available. Install it: sudo apt install -y cron")
            return 1
        lines = [line for line in current.splitlines() if AUTOSTART_TAG not in line]
        if on:
            lines.append(f'@reboot sleep 20 && "{SOURCE_ROOT}/run.sh" lite mount -b >/dev/null 2>&1 {AUTOSTART_TAG}')
        r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n" if lines else "", text=True, capture_output=True)
        if r.returncode != 0:
            print(f"[ERROR] Could not update crontab: {r.stderr.strip()}")
            return 1
    print("[OK] Lite autostart is now " + ("ON: the host's drive appears when this computer starts." if on else "OFF."))
    return 0


def cmd_menu(args):
    """Everything above as a small menu (what the lite-only program shows when it is just opened)."""
    def run(*argv):
        print()
        try:
            main(list(argv), getattr(args, "verbose", False))
        except SystemExit:
            pass
        try:
            input("\n  Press Enter to go back...")
        except EOFError:
            raise SystemExit(0) from None
    while True:
        s = load_settings()
        mp = normalize_mount_point(s.get("mount_point") or "")
        shown = bool(s.get("host")) and _is_mounted(mp)
        print("\n  DDrive Lite: a drive that runs on another computer\n")
        print(f"  Host:  {s['host'] + ' (as ' + str(s.get('user')) + ')' if s.get('host') else 'not connected yet'}")
        if s.get("host"):
            print(f"  Here:  {mp + ' is showing the drive' if shown else 'not shown'}"
                  f"{' · starts with this computer' if autostart_enabled() else ''}"
                  f"{' · syncs folders' if s.get('sync') else ''}")
        print("\n   1  Connect to a host" + (" (another one, or sign in again)" if s.get("host") else ""))
        if s.get("host"):
            print(f"   2  {'Take the drive away from this computer' if shown else 'Show the drive on ' + mp}")
            print("   3  Choose another drive letter or folder")
            print("   4  Status of the host")
            print(f"   5  Start with this computer: turn {'off' if autostart_enabled() else 'on'}")
            print(f"   6  Folder syncing: turn {'off' if s.get('sync') else 'on (its folders are then chosen on the host, under Sync)'}")
            print("   7  Forget the host")
        print("   0  Exit\n")
        try:
            pick = input("  Choose an option: ").strip()
        except EOFError:
            return 0
        if pick in ("0", "q", "exit"):
            return 0
        if pick == "1":
            run("connect")
        elif not s.get("host"):
            continue
        elif pick == "2":
            run("stop") if shown else run("mount", "-b")
        elif pick == "3":
            where = input("  Drive letter (Windows) or folder (Linux): ").strip()
            if where:
                s["mount_point"] = normalize_mount_point(where)
                save_settings(s)
        elif pick == "4":
            run("status")
        elif pick == "5":
            run("autostart", "off" if autostart_enabled() else "on")
        elif pick == "6":
            run("sync", "off" if s.get("sync") else "on")
        elif pick == "7":
            if shown:
                main(["stop"])
            if autostart_enabled():
                main(["autostart", "off"])
            run("disconnect")


def cmd_disconnect(args):
    if load_settings().get("sync"):
        cmd_sync(argparse.Namespace(state="off"))
    try:
        os.remove(settings_path())
    except OSError:
        pass
    print("The host's address and sign-in are removed from this computer.")
    return 0


def main(argv, verbose=False):
    if argv[:1] == ["lite"]:                 # how the lite-only program starts itself again in the background
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog="ddrive lite", description="Use a DDrive that runs on another computer "
                                     "(the host): its files as a drive here, without a bot, a key or a copy of the drive on this one.")
    sub = parser.add_subparsers(dest="action")
    p = sub.add_parser("connect", help="Sign in to a host: the address of its web dashboard")
    p.add_argument("address", nargs="?")
    p.add_argument("-p", "--phrase", help="Your 24 words. Asked for when left out (better: they then stay out of your command history)")
    p = sub.add_parser("mount", help="Show the host's drive as a drive letter / folder on this computer")
    p.add_argument("-m", "--mount", help="Drive letter (Windows) or folder (Linux)")
    p.add_argument("-b", "--background", action="store_true", help="Keep it running in the background")
    sub.add_parser("stop", help="Take the drive away from this computer")
    sub.add_parser("status", help="The host, and how its drive is doing")
    p = sub.add_parser("ls", help="List a folder on the host")
    p.add_argument("path", nargs="?", default="/")
    p = sub.add_parser("get", help="Download a file or folder from the host")
    p.add_argument("remote")
    p.add_argument("local", nargs="?")
    p = sub.add_parser("put", help="Upload a file or folder to the host")
    p.add_argument("local")
    p.add_argument("remote", nargs="?", default="/", help="Folder on the host to put it in (default: the top)")
    sub.add_parser("disconnect", help="Forget the host and the sign-in")
    p = sub.add_parser("autostart", help="Show the drive by itself when this computer starts: on, off or status")
    p.add_argument("state", nargs="?", choices=["on", "off", "status"])
    p = sub.add_parser("sync", help="Sync folders of this computer with the drive (chosen on the host's Sync page): "
                                    "on, off, status, or run to do it in this window")
    p.add_argument("state", nargs="?", choices=["on", "off", "status", "run"])
    p.add_argument("-b", "--background", action="store_true", help="With run: in the background")
    sub.add_parser("menu", help="All of this as a menu")
    args = parser.parse_args(argv)
    args.verbose = verbose
    if not args.action:
        parser.print_help()
        return 0
    try:
        return {"connect": cmd_connect, "mount": cmd_mount, "stop": cmd_stop, "status": cmd_status, "ls": cmd_ls,
                "get": cmd_get, "put": cmd_put, "disconnect": cmd_disconnect, "autostart": cmd_autostart,
                "sync": cmd_sync, "menu": cmd_menu}[args.action](args)
    except HostError as e:
        print(f"[ERROR] {e}")
        return 1
    except KeyboardInterrupt:
        print()
        return 130
