"""The web dashboard: browse, preview, upload and share files, and see how the drive is doing.

Runs inside the drive process (standard library only) on http://127.0.0.1:<web_port>, or on every
network interface with `web_lan` so a phone on the same network can use it.

Sign-in is by 24-word phrase, never by name and password. The owner signs in with the drive's
recovery phrase (or a passkey added afterwards) and sees everything. Other people can have accounts
(accounts.py): each is a phrase the dashboard made, with storage of its own, and sees files, notes,
bookmarks, contacts, messages, share links and history, but nothing that manages the drive or the
computer (ACCOUNT_GET / ACCOUNT_POST below list what an account may ask for; everything else is the
owner's). A successful sign-in sets a signed session cookie (HttpOnly, SameSite=Strict, 30 days).
Repeated wrong phrases lock that address out for a while. POST requests also need an `X-DD` header,
which a page on another site can't send. Share links carry their own id and only give access to the
one item.
"""

import base64
import errno
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import posixpath
import re
import socket
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .actions import default_restore_folder, intact_copy, older_copies, repair_op, snapshot_restore_ops, undelete_ops, version_put_op
from .config import Config, config_path, launcher as _launcher
from .accounts import MODES, AccountError, Accounts, AccountShares
from .crypto import hash_password
from .index import ROOT_ID, new_uid
from . import legal, passkeys, resources, words
from .shares import Shares
from .syncdevices import Gone, LiteDevices, Offline
from .bookmarks import BookmarkHandlers
from .chatlog import ChatHandlers
from .logbuf import QUIET
from .webplus import ApiErrorProxy, Fetches, PlusHandlers, housekeeping

log = logging.getLogger("ddrive.web")

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
COOKIE = "dd_session"
PIECE = 1024 * 1024
SESSION_DAYS = 30
MAX_FAILURES = 5          # wrong phrases (or passwords of a lock) from one address before it has to wait
LOCKOUT = 60.0            # seconds, doubled for every further wrong one (up to an hour)
NOTES = "/Notes"          # where notes are kept on the drive (ordinary files, so they sync and keep versions)
_BACKGROUND_ROUTES = ("/api/status", "/api/activity", "/api/log", "/api/sync", "/api/check", "/api/locks", "/api/notes",
                      "/api/chat", "/api/sync/tree", "/api/lite/sync/poll", "/api/lite/sync/report", "/api/upload/part")
PART_MAX = 64 * 1024 * 1024   # the largest part of a file sent in parts (/api/upload/part)
PART_DAYS = 7.0               # a file of which only some parts came is given up after this long
_MEDIA_EXT = ("jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "heic", "mp4", "m4v", "webm", "mov", "mkv", "avi")
NOTE_IDLE = 20.0          # a note is uploaded this long after the last autosave ("Save" uploads right away)

# What somebody signed in to an account (not the owner) may ask for: their own files and tools.
# Anything not listed here answers 403 for them, so a new route is the owner's until it is added.
ACCOUNT_GET = frozenset("""status account list search file zip thumb media notes versions deleted snapshots snapshot shares
    contacts contacts/export locks starred recent history home insights bookmarks changelog
    sync sync/tree sync/browse upload/part""".split())
ACCOUNT_POST = frozenset("""logout account/delete upload copy mkdir move delete share shares/update shares/revoke
    contacts/save contacts/delete contacts/import locks/add locks/unlock locks/lock locks/remove thumb versions/restore
    undelete purge snapshots/create snapshots/restore snapshots/delete star fetch fetch/cancel bookmarks/add
    bookmarks/save bookmarks/refresh bookmarks/delete
    sync/save sync/remove sync/pause sync/run sync/stop upload/part
    lite/sync/hello lite/sync/poll lite/sync/report lite/sync/answer lite/sync/bye""".split())


# An end-to-end encrypted account (vault.py) only ever hands this computer sealed things, so of all of
# the above it gets next to nothing: what it could ask for would need the files in the clear.
E2E_GET = frozenset("status account changelog vault vault/log vault/object".split())
E2E_POST = frozenset("logout account/delete vault/log vault/snapshot vault/object vault/delete".split())


def account_may(method, route, e2e=False):
    name = route[5:].strip("/") if route.startswith("/api/") else None
    if name is None:
        return False
    if e2e:
        return name in (E2E_GET if method == "GET" else E2E_POST)
    if name == "vault" or name.startswith("vault/"):
        return False
    if name == "chat" or name.startswith("chat/"):          # their own Discord messages
        return True
    return name in (ACCOUNT_GET if method == "GET" else ACCOUNT_POST)


def owner_key(cfg):
    """The key whose 24 words sign the owner in, in hex: the drive's own key, or, for a drive that
    isn't encrypted, one made from the dashboard's secret. '' before either exists."""
    if cfg.encryption_enabled and cfg.encryption_key:
        return cfg.encryption_key
    if not cfg.web_token:
        return ""
    return hashlib.sha256(b"DiscordDrive-owner:" + cfg.web_token.encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def lan_addresses():
    """This computer's addresses on the local network (best effort)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))     # nothing is sent; this just picks the outgoing interface
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


class WebServer:
    def __init__(self, drive, port=8765):
        self.drive = drive
        self.cfg = drive.cfg
        host = "0.0.0.0" if getattr(self.cfg, "web_lan", False) else "127.0.0.1"
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self._thread = None
        self._cfg_mtime = None
        self._fail_lock = threading.Lock()
        self._failures = {}       # address -> (count, locked until)
        self.shares = Shares(drive.index, getattr(drive.cfg, "device_id", ""))
        self.contacts_lock = threading.Lock()
        self._open_locks = {}             # browser session -> [ids of the locks it has open, last use]
        self.fetches = Fetches(drive)     # files being saved from a web address
        self.challenges = passkeys.Challenges()
        self._book = (None, [])           # (mtime, size) of the address book file, its contacts
        self.accounts = Accounts(drive)   # other people's accounts, each with a drive of its own
        self.lite = LiteDevices()         # computers running DDrive Lite that sync folders with this drive

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="WebDashboard", daemon=True)
        self._thread.start()
        threading.Thread(target=self._clean_thumbs, name="Thumbs", daemon=True).start()
        self.accounts.start()
        return self

    def _clean_thumbs(self):
        """Drop the thumbnails of files that are no longer on the drive."""
        root = os.path.join(self.cfg.resolved_data_dir, "thumbs")
        try:
            for folder in os.listdir(root):
                for name in os.listdir(os.path.join(root, folder)):
                    if name.endswith(".tmp") or self.drive.index.get_by_uid(name[:-4]) is None:
                        try:
                            os.remove(os.path.join(root, folder, name))
                        except OSError:
                            pass
        except Exception as e:           # nothing made yet, or the drive is stopping
            log.debug("thumbnail clean-up: %s", e)

    def stop(self):
        self.lite.close()
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        self.accounts.stop()

    def local_url(self):
        return f"http://localhost:{self.port}/"

    # ------------------------------------------------------------ signing in
    def _reload_cfg(self):
        """Pick up what a command changed in the config file while the drive runs."""
        if getattr(self.drive, "local_test_dir", None):
            return
        try:
            mtime = os.path.getmtime(config_path())
        except OSError:
            mtime = None
        if mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            try:
                saved = Config.load()
                for k in ("web_user", "web_password", "web_passkeys", "signup_mode", "account_quota_bytes", "accounts_max",
                          "operator_name", "operator_contact", "legal_region"):
                    setattr(self.cfg, k, getattr(saved, k))
            except (Exception, SystemExit) as e:
                log.warning("Could not read the dashboard's settings from the config: %s", e)

    def credentials(self):
        """(user, password hash) other apps sign in with over WebDAV. The dashboard itself has neither."""
        self._reload_cfg()
        return self.cfg.web_user or "", self.cfg.web_password or ""

    def set_credentials(self, user, password):
        self.save_cfg(web_user=user, web_password=hash_password(password) if password else "")

    def user_name(self):
        """What the owner is called on the page."""
        return self.cfg.web_user or "Owner"

    def owner_key(self):
        return owner_key(self.cfg)

    def save_cfg(self, **changes):
        for k, v in changes.items():
            setattr(self.cfg, k, v)
        if not getattr(self.drive, "local_test_dir", None):
            saved = Config.load()
            for k, v in changes.items():
                setattr(saved, k, v)
            saved.save()
            self._cfg_mtime = os.path.getmtime(config_path())

    def _secret(self, purpose=b"") -> bytes:
        return hashlib.sha256(b"DiscordDrive-web:" + (self.cfg.web_token or "").encode() + purpose).digest()

    def new_session(self, account=""):
        """A session cookie for the owner ('') or for an account (its id). "n" makes every sign-in its
        own session (each browser unlocks locked folders for itself)."""
        payload = _b64(json.dumps({"a": account, "t": int(time.time()), "n": _b64(os.urandom(9))},
                                  separators=(",", ":")).encode())
        sig = _b64(hmac.new(self._secret(b"session"), payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}"

    def session(self, value):
        """Who a session cookie belongs to: '' the owner, an account's id, or None (signed out)."""
        if not value:
            return None
        try:
            payload, sig = value.split(".", 1)
            good = _b64(hmac.new(self._secret(b"session"), payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(good, sig):
                return None
            data = json.loads(_unb64(payload))
            if time.time() - data.get("t", 0) > SESSION_DAYS * 86400:
                return None
            account = data.get("a")
            if account is None:
                return "" if data.get("u") else None       # a session from before accounts: the owner's
            if account == "":
                return ""
            return account if self.accounts.active(str(account)) is not None else None
        except Exception:
            return None

    def locked_for(self, addr):
        with self._fail_lock:
            count, until = self._failures.get(addr, (0, 0.0))
        return max(0.0, until - time.time())

    def note_login(self, addr, ok):
        with self._fail_lock:
            if ok:
                self._failures.pop(addr, None)
                return
            count, _ = self._failures.get(addr, (0, 0.0))
            count += 1
            until = time.time() + min(3600.0, LOCKOUT * 2 ** (count - MAX_FAILURES)) if count >= MAX_FAILURES else 0.0
            self._failures[addr] = (count, until)

    # ------------------------------------------------------------ password-locked folders
    @staticmethod
    def _session_key(cookie):
        return hashlib.sha256((cookie or "").encode()).hexdigest()[:24]

    def open_locks(self, cookie, touch=True):
        """The locks this browser has open (they close after `lock_timeout_minutes` without use)."""
        key = self._session_key(cookie)
        with self._fail_lock:
            entry = self._open_locks.get(key)
            if entry is None:
                return frozenset()
            timeout = self.drive.locks.timeout()
            if timeout and time.time() - entry[1] > timeout:
                del self._open_locks[key]
                return frozenset()
            if touch:
                entry[1] = time.time()
            return frozenset(entry[0])

    def open_locks_add(self, cookie, uids):
        with self._fail_lock:
            entry = self._open_locks.setdefault(self._session_key(cookie), [set(), time.time()])
            entry[0].update(uids)
            entry[1] = time.time()

    def open_locks_clear(self, cookie, uids=None):
        with self._fail_lock:
            key = self._session_key(cookie)
            if uids is None:
                self._open_locks.pop(key, None)
            elif key in self._open_locks:
                self._open_locks[key][0].difference_update(uids)

    def share_unlock_value(self, sid, rec):
        """Cookie proving a visitor typed the password of share `sid` (void when the password changes)."""
        msg = f"share:{sid}:{rec.get('pw', '')}".encode()
        return _b64(hmac.new(self._secret(b"share"), msg, hashlib.sha256).digest())


class _Space:
    """What a request sees as `self.app` while it serves an account: the account's own share links,
    downloads from links and caches. Of the dashboard itself only what SHARED lists comes through, so
    nothing a request of the owner cached (folder sizes, contacts, bookmarks) can reach an account."""

    SHARED = frozenset(("port", "accounts", "open_locks", "open_locks_add", "open_locks_clear", "locked_for", "note_login",
                        "share_unlock_value", "new_session"))

    def __init__(self, server, drive):
        self._server = server
        self.drive, self.cfg = drive, drive.cfg
        self.shares = AccountShares(drive.index, drive.id)
        self.fetches = Fetches(drive)
        self.contacts_lock = threading.Lock()
        self._book = (None, [])
        drive.busy.append(lambda: any(i["state"] == "running" for i in self.fetches.items))

    def __getattr__(self, name):
        if name in _Space.SHARED:
            return getattr(self._server, name)
        raise AttributeError(name)


class _Counted:
    """The connection's input, counting what is read, so that a request body nobody read can be
    cleared away before the next request on the same connection (it would be taken for its start)."""

    def __init__(self, raw):
        self._raw = raw
        self.n = 0

    def read(self, *args):
        data = self._raw.read(*args)
        self.n += len(data)
        return data

    def readline(self, *args):
        data = self._raw.readline(*args)
        self.n += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _Handler(PlusHandlers, BookmarkHandlers, ChatHandlers, BaseHTTPRequestHandler):
    server_version = "DDrive"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing
    # A request is served from the owner's drive, or, for somebody signed in to an account (and for an
    # account's share links), from that account's own drive: `drive` and `app` are then the account's.
    _acct = None
    _space = None
    _e2e = False                 # the account being served is end-to-end encrypted (vault.py)

    @property
    def app(self):
        return self._space or self.server.app

    @property
    def drive(self):
        return self._acct or self.server.app.drive

    def _enter(self, aid):
        """Serve the rest of this request from an account's drive."""
        server = self.server.app
        d = server.accounts.acquire(aid)
        with server.accounts._lock:
            if d.space is None:
                d.space = _Space(server, d)
        d.fs.scope.unlocked = frozenset()
        self._acct, self._space = d, d.space
        me = threading.current_thread()          # what it does for an account stays out of the owner's log
        if not me.name.startswith(QUIET):
            me.name = QUIET + me.name

    def _leave(self):
        d, self._acct, self._space = self._acct, None, None
        if d is not None:
            self.server.app.accounts.release(d)
            me = threading.current_thread()
            if me.name.startswith(QUIET):
                me.name = me.name[len(QUIET):]

    def _host_name(self):
        """This computer's name: for the owner's pages only."""
        return "" if self._acct is not None else socket.gethostname()

    def log_message(self, fmt, *args):
        log.debug("web %s - %s", self.address_string(), fmt % args)

    def _host_ok(self):
        """Refuse requests addressed to some other name (a DNS-rebinding site, for instance)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        name = socket.gethostname().lower()
        extra = [str(h).lower().strip() for h in (getattr(self.drive.cfg, "web_hosts", None) or [])]
        return host in (name, name + ".local", name + ".lan") or host in extra

    def _cookie(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _who(self):
        """'' for the owner, an account's id, or None when nobody is signed in."""
        return self.server.app.session(self._cookie())

    def _local_request(self):
        """From the computer running the drive (the only place the owner's phrase is shown)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if self.headers.get("X-Forwarded-For") or self.headers.get("Forwarded"):
            return False                         # passed on by a reverse proxy: it came from somewhere else
        return self.client_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "::1")

    def _addr(self):
        """Where a request comes from, for slowing down wrong guesses and limiting new accounts: behind a
        reverse proxy on this computer that is the address the proxy saw, so each visitor counts for
        themselves instead of everybody counting as the proxy."""
        peer = self.client_address[0]
        seen = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        return seen[:64] if seen and peer in ("127.0.0.1", "::1") else peer

    def _form(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 16 * 1024:
            raise ApiError(413, "request too large")
        return {k: v[-1] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}

    def _redirect(self, where, cookie=None):
        headers = {"Location": where}
        if cookie is not None:
            headers["Set-Cookie"] = cookie
        self._send(303, b"", "text/plain", headers)

    def _signin(self, error="", status=200):
        rp, _ = self._rp()
        hint = f"{_launcher()} recovery-phrase" if self._local_request() else ""
        html = _signin_page(error, passkey=bool(rp and self._here(rp)), signup=self.server.app.accounts.mode(), hint=hint)
        self._send(status, html.encode("utf-8"), "text/html; charset=utf-8", _PAGE_HEADERS)

    # ------------------------------------------------------------ signing in
    def _rp(self):
        """(passkey address of this request or None, the origins a passkey answer may come from)."""
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        rp = passkeys.rp_id(host)
        return rp, {f"https://{host}", f"http://{host}"} if rp else set()

    def _here(self, rp):
        return [p for p in self.drive.cfg.web_passkeys or [] if p.get("rp") == rp]

    def _passkey_options(self):
        """What the browser needs to sign in with a passkey (asked before signing in)."""
        rp, _ = self._rp()
        mine = self._here(rp) if rp else []
        if not mine:
            return self._error(404, "No passkey is set up for this address")
        self._json({"challenge": self.app.challenges.new("login"), "rpId": rp, "allow": [p["id"] for p in mine]})

    def _passkey_login(self):
        addr = self._addr()
        wait = self.app.locked_for(addr)
        if wait:
            return self._error(429, f"Too many failed tries. Wait {int(wait) + 1} seconds.")
        body = self._body_json()
        rp, origins = self._rp()
        key = next((p for p in self._here(rp) if p["id"] == body.get("id")), None) if rp else None
        try:
            if key is None:
                raise passkeys.PasskeyError("this passkey isn't registered here")
            count = passkeys.verify(key, passkeys.unb64u(body.get("authenticatorData")), passkeys.unb64u(body.get("clientDataJSON")),
                                    passkeys.unb64u(body.get("signature")), rp, origins, self.app.challenges)
        except passkeys.PasskeyError as e:
            self.app.note_login(addr, False)
            log.warning("Web dashboard: a passkey sign-in from %s failed: %s", addr, e)
            return self._error(401, f"That passkey didn't work: {e}.")
        self.app.note_login(addr, True)
        keys = [dict(p, count=count, last=time.time()) if p is key else p for p in self.drive.cfg.web_passkeys]
        self.app.save_cfg(web_passkeys=keys)
        self._send(200, b'{"ok":true}', "application/json; charset=utf-8",
                   {"Set-Cookie": self._cookie_for(self.app.new_session())})

    def _get_api_signin(self):
        rp, _ = self._rp()
        cfg = self.drive.cfg
        secure = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip() == "https" or rp == "localhost"
        self._json({
            "user": self.app.user_name(), "rp": rp, "can_passkey": bool(rp) and secure, "local": self._local_request(),
            "passkeys": [{"id": p["id"], "name": p.get("name") or "Passkey", "rp": p.get("rp"), "created": p.get("created"),
                          "last": p.get("last"), "here": p.get("rp") == rp} for p in cfg.web_passkeys or []],
            "encrypted": bool(cfg.encryption_enabled and cfg.encryption_key), "webdav": bool(cfg.webdav_enabled),
            "dav_user": cfg.web_user or "", "dav_password": bool(cfg.web_password),
        })

    def _post_api_signin_passkey_options(self):
        rp, _ = self._rp()
        if not rp:
            raise ApiError(400, "Passkeys need a name in the address: open the dashboard as http://localhost:"
                                f"{self.app.port}/ on this computer, or through your https address.")
        user = self.app.user_name()
        self._json({"challenge": self.app.challenges.new("register"), "rp": {"id": rp, "name": "DDrive"},
                    "user": {"id": _b64(hashlib.sha256(b"dd-user:" + user.encode()).digest()[:16]), "name": user,
                             "displayName": user},
                    "exclude": [p["id"] for p in self._here(rp)]})

    def _post_api_signin_passkey_register(self):
        body = self._body_json()
        rp, origins = self._rp()
        try:
            if not rp:
                raise passkeys.PasskeyError("passkeys don't work at this address")
            key = passkeys.register(passkeys.unb64u(body.get("attestationObject")), passkeys.unb64u(body.get("clientDataJSON")),
                                    rp, origins, self.app.challenges)
        except passkeys.PasskeyError as e:
            raise ApiError(400, f"The passkey could not be added: {e}.") from None
        if any(p["id"] == key["id"] for p in self.drive.cfg.web_passkeys or []):
            raise ApiError(409, "That passkey is already added")
        key.update(rp=rp, name=str(body.get("name") or "Passkey").strip()[:60] or "Passkey", created=time.time(), last=None)
        self.app.save_cfg(web_passkeys=list(self.drive.cfg.web_passkeys or []) + [key])
        log.info("Web dashboard: a passkey was added for %s", rp)
        self._json({"ok": True})

    def _post_api_signin_passkey_remove(self):
        pid = str(self._body_json().get("id") or "")
        keys = [p for p in self.drive.cfg.web_passkeys or [] if p["id"] != pid]
        if len(keys) == len(self.drive.cfg.web_passkeys or []):
            raise ApiError(404, "That passkey is already gone")
        self.app.save_cfg(web_passkeys=keys)
        self._json({"ok": True})

    def _post_api_webdav_password(self):
        """Set, change or (empty) remove the name and password other apps use over WebDAV."""
        body = self._body_json()
        password = str(body.get("password") or "")
        name = str(body.get("user") or self.drive.cfg.web_user or "drive").strip()[:64]
        if password and len(password) < 8:
            raise ApiError(400, "Use a password of at least 8 characters")
        if password and not name:
            raise ApiError(400, "Choose a user name for the apps to sign in with")
        self.app.set_credentials(name, password)
        self.app._dav_ok = {}
        self._json({"ok": True})

    def _post_api_signin_phrase(self):
        """The owner's 24 words, shown only on the computer that runs the drive."""
        if not self._local_request():
            raise ApiError(403, "The recovery phrase is only shown on the computer that runs the drive "
                                f"(open the dashboard there, or run '{_launcher()} recovery-phrase').")
        key = self.app.owner_key()
        if not key:
            raise ApiError(404, "This drive has no key yet")
        self._json({"phrase": words.key_to_phrase(bytes.fromhex(key))})

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _query(self):
        return {k: v[-1] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ApiError(413, "request too large")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "invalid JSON") from None

    # ------------------------------------------------------------ routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _do_other(self):
        self._dispatch(self.command)

    def setup(self):
        super().setup()
        self.rfile = _Counted(self.rfile)

    def _dispatch(self, method):
        self.rfile.n = 0                         # from here on: bytes of this request's body
        try:
            self._route(method)
        finally:
            self._leave()
            # Leave nothing of this request behind on the connection.
            try:
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    left = 0
                else:
                    left = int(self.headers.get("Content-Length") or 0) - self.rfile.n
                if left > 1 << 20:
                    self.close_connection = True
                elif left > 0 and not self.close_connection:
                    self.rfile.read(left)
            except (OSError, ValueError):
                self.close_connection = True

    do_OPTIONS = do_PROPFIND = do_PROPPATCH = do_MKCOL = do_PUT = do_DELETE = do_MOVE = do_COPY = do_LOCK = do_UNLOCK = _do_other

    @staticmethod
    def _media_ext():
        return _MEDIA_EXT

    def _route(self, method):
        try:
            self.drive.fs.scope.unlocked = frozenset()     # locked folders: nothing is open until we know who asks
            if not self._host_ok():
                return self._send(421, b"Misdirected request", "text/plain")
            route = urllib.parse.urlparse(self.path).path
            if route == "/dav" or route.startswith("/dav/"):
                return self._dav(self.command)
            if method not in ("GET", "POST"):
                return self._send(405, b"Method not allowed", "text/plain")
            if method == "POST" and route == "/login":
                return self._login()
            if route == "/signup":
                return self._signup(method)
            if method == "GET" and route in ("/terms", "/privacy"):
                return self._send(200, legal.page(route[1:], self.server.app.cfg).encode("utf-8"),
                                  "text/html; charset=utf-8", _PAGE_HEADERS)
            if method == "POST" and route == "/passkey/options":
                return self._passkey_options()
            if method == "POST" and route == "/passkey/login":
                return self._passkey_login()
            if route.startswith("/s/") and method in ("GET", "POST"):
                return self._shared(route[3:], method)
            if method == "GET" and route in ("/", "/index.html", "/app.css", "/app.js", "/logo.svg", "/icon.png",
                                             "/manifest.webmanifest", "/share-upload.js", "/signin.js"):
                if route in ("/", "/index.html") and self._who() is None:
                    return self._signin()
                return self._static(route.lstrip("/") or "index.html")
            who = self._who()
            if who is None:
                return self._error(401, "Signed out. Reload the page to sign in.")
            if method == "POST" and self.headers.get("X-DD") != "1":
                return self._error(403, "missing X-DD header")
            if who:                              # an account: its own drive, and only what accounts may do
                e2e = bool((self.server.app.accounts.active(who) or {}).get("e2e"))
                if not account_may(method, route, e2e):
                    return self._error(403, "An end-to-end encrypted account's files are only opened on your own device."
                                       if e2e else "Only the owner of this drive can do that.")
                self._enter(who)
                self._e2e = e2e
            # Pages that refresh by themselves don't count as using an unlocked folder.
            self.drive.fs.scope.unlocked = self.app.open_locks(self._cookie(), touch=route not in _BACKGROUND_ROUTES)
            fn = getattr(self, f"_{method.lower()}_{route.strip('/').replace('/', '_').replace('-', '_')}", None)
            if fn is None:
                return self._error(404, "not found")
            fn()
        except (ApiError, ApiErrorProxy) as e:
            self._error(e.status, str(e))
        except AccountError as e:
            self._error(503, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as e:
            code = {errno.ENOENT: 404, errno.EEXIST: 409, errno.ENOTDIR: 400, errno.EISDIR: 400,
                    errno.ENOTEMPTY: 409, errno.ENOSPC: 507, errno.EFBIG: 413}.get(e.errno or 0, 500)
            self._error(code, os.strerror(e.errno) if e.errno else str(e))
        except Exception as e:
            log.warning("Web request %s %s failed: %s", method, self.path.split("?")[0], e, exc_info=True)
            try:
                self._error(500, str(e))
            except Exception:
                pass

    def _static(self, name):
        path = os.path.join(STATIC, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if name.endswith(".js"):
            ctype = "application/javascript"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype, _PAGE_HEADERS)

    def _cookie_for(self, value, days=SESSION_DAYS):
        return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={int(days * 86400)}"

    def _login(self):
        """Sign in with 24 words: the drive's recovery phrase is the owner, an account's phrase is that account."""
        app = self.server.app
        addr = self._addr()
        wait = app.locked_for(addr)
        form = self._form()
        if wait:
            return self._signin(f"Too many failed tries. Try again in {int(wait) + 1} seconds.", 429)
        if form.get("auth"):
            # An end-to-end encrypted account: its words stayed on the device, this is what it made from them.
            try:
                rec = app.accounts.find(bytes.fromhex(form["auth"]))
            except ValueError:
                rec = None
            ok = rec is not None and bool(rec.get("e2e"))
            app.note_login(addr, ok)
            if not ok:
                return self._signin("Nobody here has an end-to-end encrypted account with those 24 words.", 401)
            return self._redirect("/", self._cookie_for(app.new_session(rec["id"])))
        try:
            key = words.phrase_to_key(form.get("phrase") or "")
        except ValueError as e:
            app.note_login(addr, False)
            return self._signin(f"That phrase isn't right: {e}.", 401)
        owner = app.owner_key()
        if owner and hmac.compare_digest(key.hex(), owner):
            who = ""
        else:
            rec = app.accounts.find(key)
            who = rec["id"] if rec is not None else None
        app.note_login(addr, who is not None)
        if who is None:
            log.warning("Web dashboard: a phrase that belongs to nobody here, from %s", addr)
            return self._signin("Those are 24 valid words, but nobody here has that phrase.", 401)
        self._redirect("/", self._cookie_for(app.new_session(who)))

    def _signup(self, method):
        """Making an account: the page gives 24 new words, and the account exists once they are confirmed."""
        app = self.server.app
        accounts, addr = app.accounts, self._addr()

        def page(html, status=200):
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8", _PAGE_HEADERS)

        mode = accounts.mode()
        if mode == "closed":
            if method == "POST":
                self._form()
            return page(_signup_page("closed"), 403)
        if method == "GET":
            return page(_signup_page(mode, invite=self._query().get("invite") or ""))
        form = self._form()
        invite = (form.get("invite") or "").strip()
        wait = app.locked_for("signup:" + addr)
        if wait:
            return page(_signup_page(mode, invite, f"Too many tries. Try again in {int(wait) + 1} seconds."), 429)
        try:
            if form.get("step") == "e2e":            # made in the browser: the words never came here
                try:
                    auth = bytes.fromhex(form.get("auth") or "")
                except ValueError:
                    auth = b""
                rec = accounts.create_e2e(auth, invite, addr)
                return self._redirect("/", self._cookie_for(app.new_session(rec["id"])))
            if form.get("step") == "confirm":
                rec = accounts.create(form.get("phrase"), form.get("ticket"), invite, addr)
                return self._redirect("/", self._cookie_for(app.new_session(rec["id"])))
            phrase, ticket = accounts.offer(invite, addr)
        except AccountError as e:
            app.note_login("signup:" + addr, False)
            return page(_signup_page(mode, invite, str(e)), 400)
        page(_signup_page("phrase", invite, phrase=phrase, ticket=ticket))

    def _post_api_logout(self):
        self.app.open_locks_clear(self._cookie())
        self._send(200, b'{"ok":true}', "application/json; charset=utf-8", {"Set-Cookie": self._cookie_for("", 0)})

    # ------------------------------------------------------------ helpers
    def _node(self, path, want_dir=None):
        path = "/" + (path or "").strip("/")
        node = self.drive.index.resolve(path)
        if node is None or self.drive.fs._hidden(path):
            raise ApiError(404, f"Not found: {path}")
        if want_dir is True and not node["is_dir"]:
            raise ApiError(400, f"{path} is a file")
        if want_dir is False and node["is_dir"]:
            raise ApiError(400, f"{path} is a folder")
        return path, node

    def _entry(self, path, n):
        e = {"name": n["name"], "path": path, "dir": bool(n["is_dir"]), "size": n["size"], "mtime": n["mtime"],
             "state": n["state"], "pinned": bool(n["is_pinned"])}
        if self.drive.locks.is_root(n["uid"]):
            e["locked"] = True            # has a password lock (open in this browser, or it wouldn't be listed)
        return e

    def _shown(self, path):
        """False for a path under a hidden or locked folder (for lists that don't go through _node)."""
        return not self.drive.fs._hidden(path or "/")

    def _names_locked(self, text):
        return self.drive.locks.hides_text(text or "", self.drive.fs.scope.unlocked)

    def _publish(self, ops):
        self.drive.index.queue_ops(ops)
        self.drive.journal.wake()

    # ------------------------------------------------------------ status
    def _get_api_status(self):
        d = self.drive
        st = d.index.stats(max_age=2)
        if self._acct is not None:               # an account sees its own storage, nothing of the computer
            return self._json({
                "version": __version__, "role": "account", "account": d.id, "user": "", "encrypted": True, "e2e": self._e2e,
                "stats": st, "uploads": self._uploads(), "downloads": len(d.cache.active), "queued": len(d.uploader._pending),
                "quota": {"bytes": d.cfg.quota_bytes, "used": st["bytes"] + st["version_bytes"]},
                "locks": {"count": d.locks.count(), "open": len(d.fs.scope.unlocked)},
                "fetches": self._fetch_list(), "public": self.public_base(),
            })
        devices = []
        for dev, info in sorted(d.index.devices().items(), key=lambda kv: -kv[1].get("seen", 0)):
            devices.append({"id": dev, "name": info.get("name") or "", "seen": info.get("seen", 0),
                            "me": dev == d.cfg.device_id})
        if not any(x["me"] for x in devices):
            devices.insert(0, {"id": d.cfg.device_id, "name": socket.gethostname(), "seen": time.time(), "me": True})
        scrub = d.maintenance.scrub_state() if d.maintenance else {}
        self._json({
            "version": __version__,
            "role": "owner",
            "user": self.app.user_name(),
            "signup": self.app.accounts.mode(),
            "accounts": self.app.accounts.count(),
            "mount": d.cfg.mount_point,
            "device": d.cfg.device_id,
            "encrypted": d.crypto is not None,
            "bots": getattr(d.backend, "upload_slots", 1),
            "stats": st,
            "uploads": self._uploads(),
            "downloads": len(d.cache.active) if d.cache else 0,
            "queued": len(d.uploader._pending) if d.uploader else 0,
            "saved": dict(d.uploader.stats) if d.uploader else {},
            "health": {
                "parity": bool(d.cfg.parity_enabled),
                "group": d.cfg.parity_group, "pieces": d.cfg.parity_pieces,
                "scrub": scrub, "healer": dict(d.healer.stats) if d.healer else {},
                "lost": self._lost(),
                "recheck": d.healer.recheck if d.healer else None,
                "leader": d.maintenance.leader() if d.maintenance else False,
            },
            "devices": devices,
            "lan": [f"http://{ip}:{self.app.port}/" for ip in lan_addresses()] if d.cfg.web_lan else [],
            "public": self.public_base(),
            "snapshots": {"interval": d.cfg.snapshot_interval_hours, "keep": d.cfg.snapshot_keep_days},
            "locks": {"count": d.locks.count(), "open": len(d.fs.scope.unlocked)},
            "fetches": self._fetch_list(),
            "resources": resources.snapshot(d),
        })

    def _lost(self):
        """Parts that can't be repaired; a damaged file also says whether an intact older copy exists ("fix")."""
        d = self.drive
        entries = [e for e in (d.healer.lost_files(max_age=20) if d.healer else []) if self._shown(e["path"])][:500]
        lost = None
        for e in entries:
            if e["kind"] == "file" and "fix" not in e:      # worked out once per list (the list is kept for a while)
                lost = d.healer._lost() if lost is None else lost
                copy = intact_copy(d.index, lost, e["path"])
                e["fix"] = {"source": copy["source"], "at": copy["at"], "size": copy["size"]} if copy else None
                e["copies"] = older_copies(d.index, e["path"])       # so the page can say why nothing can be brought back
        return entries

    def _post_api_repair(self):
        """Put the newest intact copy (from a snapshot or an earlier version) in the place of damaged files:
        the ones in `paths`, or with `all` every damaged file that has such a copy."""
        body = self._body_json()
        d = self.drive
        lost = d.healer._lost() if d.healer else {}
        damaged = [e["path"] for e in (d.healer.lost_files() if d.healer else []) if e["kind"] == "file" and self._shown(e["path"])]
        want = damaged if body.get("all") else [p for p in map(str, body.get("paths") or []) if p in damaged]
        ops, done, without = [], [], []
        for path in want:
            copy = intact_copy(d.index, lost, path)
            if copy is None:
                without.append(path)
                continue
            ops.append(repair_op(d.index, path, copy))
            done.append({"path": path, "source": copy["source"], "at": copy["at"]})
            log.info("Web dashboard: %s is replaced by its intact copy from %s (a %s)", path,
                     time.strftime("%Y-%m-%d %H:%M", time.localtime(copy["at"])), "snapshot" if copy["source"] == "snapshot" else "earlier version")
        if ops:
            self._publish(ops)
            d.journal.sync_once()                    # apply it here right away, so the list is up to date
            d.healer._lost_list = (0.0, [])
        self._json({"ok": True, "repaired": done, "without": without})

    def _post_api_repair_recheck(self):
        """Health, "Check again": ask Discord once more for every part listed as lost. What can be read after
        all comes off the list, what this computer still holds is uploaded again. With `forget`, the list is
        emptied instead (what is still missing is listed again when it is next read or checked)."""
        d = self.drive
        if d.healer is None:
            raise ApiError(503, "Not available")
        if self._body_json().get("forget"):
            n = d.healer.forget_lost()
            log.info("Web dashboard: the list of parts that can't be repaired was cleared (%d).", n)
            return self._json({"ok": True, "forgotten": n})
        # It can take a while (every part is asked for, repaired ones are uploaded at Discord's pace, and what
        # got no answer is tried again), so it runs in the background; /api/status says how far it is.
        started = d.healer.recheck_start(sync=d.journal.sync_once)
        self._json({"ok": True, "started": started, "recheck": d.healer.recheck})

    def _post_api_resources_clean(self):
        """The buttons on the Resources page."""
        what = self._body_json().get("what")
        d = self.drive
        if what == "cache":
            freed = resources.clear_cache(d)
            log.info("Web dashboard: the read cache was cleared (%d bytes)", freed)
            return self._json({"ok": True, "freed": freed})
        if what == "thumbs":
            return self._json({"ok": True, "freed": resources.clear_thumbnails(d)})
        if what == "fetches":
            running = [i for i in self.app.fetches.items if i["state"] == "running"]
            for i in running:
                self.app.fetches.cancel(i["id"])
            return self._json({"ok": True, "count": len(running)})
        if what == "uploads":
            count, skipped, freed = resources.cancel_waiting_uploads(d)
            log.warning("Web dashboard: %d waiting upload(s) were cancelled (%d being sent were left alone)", count, skipped)
            return self._json({"ok": True, "count": count, "skipped": skipped, "freed": freed})
        raise ApiError(400, "Unknown action")

    # ------------------------------------------------------------ accounts
    def _get_api_account(self):
        """The account that is signed in, for its own page."""
        d = self._acct
        if d is None:
            raise ApiError(404, "The owner has no account page")
        rec = self.app.accounts.active(d.id) or {}
        st = d.index.stats(max_age=2)
        self._json({"id": d.id, "created": rec.get("created"), "files": st["files"], "bytes": st["bytes"],
                    "version_bytes": st["version_bytes"], "quota": d.cfg.quota_bytes})

    def _post_api_account_delete(self):
        """An account deletes itself, with everything in it."""
        d = self._acct
        if d is None or str(self._body_json().get("confirm") or "").strip().lower() != "delete":
            raise ApiError(400, "Type delete to confirm")
        accounts = self.app.accounts
        self._leave()
        accounts.delete(d.id)
        self._send(200, b'{"ok":true}', "application/json; charset=utf-8", {"Set-Cookie": self._cookie_for("", 0)})

    def _get_api_accounts(self):
        """Everything about accounts, for the owner's Settings."""
        acc, cfg, base = self.app.accounts, self.drive.cfg, self.public_base()
        invites = [dict(i, path=f"/signup?invite={i['code']}", url=f"{base}/signup?invite={i['code']}" if base else "")
                   for i in acc.invites()]
        self._json({"mode": acc.mode(), "quota": int(cfg.account_quota_bytes or 0), "max": int(cfg.accounts_max or 0),
                    "legal": {k: getattr(cfg, k) or "" for k in ("operator_name", "operator_contact", "legal_region")},
                    "accounts": acc.listing(), "invites": invites, "path": "/signup", "url": f"{base}/signup" if base else "",
                    "backups": acc.restorable()})

    def _post_api_accounts_restore(self):
        """Bring back the list of accounts another computer (or this one, before its disk was lost) saved."""
        try:
            count = self.app.accounts.restore(str(self._body_json().get("id") or ""))
        except AccountError as e:
            raise ApiError(409, str(e)) from None
        log.warning("Web dashboard: the list of %d account(s) was brought back from Discord.", count)
        self._json({"ok": True, "count": count})

    def _post_api_accounts_invite(self):
        body = self._body_json()
        try:
            uses, days = max(0, min(10000, int(body.get("uses", 1)))), max(0.0, min(3650.0, float(body.get("days", 7))))
        except (TypeError, ValueError):
            raise ApiError(400, "bad number") from None
        code = self.app.accounts.invite_new(uses, days, str(body.get("note") or ""))
        log.info("Web dashboard: an invite was made (%s use(s), %s).", uses or "any number of", f"{days:g} day(s)" if days else "no expiry")
        self._json({"ok": True, "code": code})

    def _post_api_accounts_invite_revoke(self):
        if not self.app.accounts.invite_revoke(self._body_json().get("code")):
            raise ApiError(404, "That invite is already gone")
        self._json({"ok": True})

    def _post_api_accounts_disable(self):
        body = self._body_json()
        if not self.app.accounts.set_disabled(str(body.get("id") or ""), bool(body.get("off"))):
            raise ApiError(404, "No such account")
        self._json({"ok": True})

    def _post_api_accounts_delete(self):
        if not self.app.accounts.delete(str(self._body_json().get("id") or "")):
            raise ApiError(404, "No such account")
        self._json({"ok": True})

    # ------------------------------------------------------------ activity and log
    def _uploads(self):
        """Uploads to Discord in progress, with percent, speed and time left."""
        d = self.drive
        out, now = [], time.time()
        for p in list(d.uploader.progress.values()) if d.uploader else []:
            if not self._shown(p.get("path")):
                continue
            sent = p.get("bytes", 0) - p.get("base", 0)
            elapsed = max(0.5, now - p.get("started", now))
            speed = sent / elapsed if sent > 0 else 0.0
            left = max(0, p.get("size", 0) - p.get("bytes", 0))
            out.append({**p, "pct": round(100.0 * p.get("bytes", 0) / max(1, p.get("size", 1)), 1),
                        "speed": speed, "eta": left / speed if speed > 0 else None})
        return out

    def _get_api_activity(self):
        d = self.drive
        now = time.time()
        cache = d.cache
        downloads = {}
        for mid, a in list(cache.active.items()):
            node = a.get("node")
            if node and not self._shown(d.index.path_of(node)):
                continue
            g = downloads.setdefault(node, {"path": d.index.path_of(node) if node else "(piece)", "pieces": 0,
                                            "bytes": 0, "since": a["started"]})
            g["pieces"] += 1
            g["bytes"] += a.get("size") or 0
            g["since"] = min(g["since"], a["started"])
        recent = [r for r in cache.recent if now - r[0] < 10]
        dl_speed = sum(b for _, b in recent) / 10.0
        jobs = []
        for job in list(cache.jobs.values()):
            elapsed = max(0.5, now - job["started"])
            speed = job["bytes"] / elapsed if job["bytes"] else 0.0
            jobs.append({**job, "pct": round(100.0 * job["bytes"] / max(1, job["total"]), 1), "speed": speed,
                         "eta": (job["total"] - job["bytes"]) / speed if speed else None})
        uploads = self._uploads()
        queue = [x for x in (d.uploader.queue_info() if d.uploader else []) if self._shown(x.get("path"))]
        jobs = [j for j in jobs if not self._names_locked(json.dumps(j))]
        up_speed = sum(u["speed"] for u in uploads)
        queued_bytes = sum(q["size"] for q in queue) + sum(max(0, u["size"] - u["bytes"]) for u in uploads)
        st = d.index.stats(max_age=2)
        self._json({
            "time": now,
            "uploads": uploads,
            "queue": queue,
            "upload_speed": up_speed,
            "upload_eta": queued_bytes / up_speed if up_speed else None,
            "downloads": list(downloads.values()),
            "download_speed": dl_speed,
            "offline": jobs,
            "sync": {"outbox": st["outbox"], "trash": st["trash"], "unsynced": st["unsynced"],
                     "failures": getattr(d.journal, "_failures", 0),
                     "repairs": d.healer._queue.qsize() if d.healer else 0},
            "check": d.maintenance.scrub_state() if d.maintenance else {},
        })

    def _get_api_log(self):
        from .logbuf import BUFFER
        q = self._query()
        try:
            after = int(q.get("after") or 0)
        except ValueError:
            after = 0
        if not getattr(self.drive, "local_test_dir", None):
            BUFFER.follow_file(self._log_path())
        self._json({"gen": BUFFER.gen, "lines": [r for r in BUFFER.since(after, 2000 if not after else 1000)
                                                 if not self._names_locked(r.get("m") or json.dumps(r))]})

    @staticmethod
    def _log_path():
        from .config import default_data_dir
        return os.path.join(default_data_dir(), "ddrive.log")

    def _post_api_log_clear(self):
        """Empty the log: the lines shown in the Log tab and the log file on this computer."""
        from .logbuf import BUFFER, clear_file
        self._body_json()
        ok = True
        if not getattr(self.drive, "local_test_dir", None):
            ok = clear_file(self._log_path())
            BUFFER._file_size = 0
        BUFFER.clear()
        log.info("The log was cleared.")
        self._json({"ok": True, "file": ok})

    def _get_api_changelog(self):
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(getattr(sys, "_MEIPASS", ""), "CHANGELOG.md"), os.path.join(os.path.dirname(here), "CHANGELOG.md"),
                  os.path.join(here, "CHANGELOG.md")):
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return self._json({"version": __version__, "text": f.read()})
        self._json({"version": __version__, "text": ""})

    # ------------------------------------------------------------ browsing
    def _get_api_list(self):
        path, node = self._node(self._query().get("path"), want_dir=True)
        items = []
        stars = self.drive.index.stars_all()
        _, sizes = self._tree()
        for c in self.drive.index.children(node["id"]):
            cpath = posixpath.join(path, c["name"])
            if not self.drive.fs._hidden(cpath):
                e = self._entry(cpath, c)
                if c["uid"] in stars:
                    e["starred"] = True
                if c["is_dir"]:
                    e["size"], e["files"] = sizes.get(c["id"], (0, 0))
                if not c["is_dir"] and c["name"].rpartition(".")[2].lower() in _MEDIA_EXT:
                    e["thumb"] = self._thumb_state(c)
                items.append(e)
        items.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        self._json({"path": path, "items": items})

    def _get_api_search(self):
        q = (self._query().get("q") or "").strip()
        if len(q) < 2:
            return self._json({"items": []})
        idx = self.drive.index
        rows = idx._all("SELECT * FROM nodes WHERE id != ? AND name LIKE ? ESCAPE '\\' ORDER BY is_dir DESC, name LIMIT 200",
                        (ROOT_ID, "%" + re.sub(r"([%_\\])", r"\\\1", q) + "%"))
        items = []
        for r in rows:
            p = idx.path_of(r["id"])
            if not self.drive.fs._hidden(p):
                items.append(self._entry(p, r))
        self._json({"items": items})

    def _get_api_file(self):
        q = self._query()
        path, node = self._node(q.get("path"), want_dir=False)
        self._stream(node, download=q.get("dl") == "1")

    def _stream(self, node, download=False, public=False):
        total = node["size"]
        on = self.drive.fs.open_nodes.get(node["id"])
        staging = self.drive.fs.staging_path(node["id"])
        if on is not None or os.path.exists(staging):
            try:
                total = os.path.getsize(staging)
            except OSError:
                pass
        start, end = 0, total - 1
        status = 200
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if m and total > 0:
            a, b = m.groups()
            if a == "" and b:
                start = max(0, total - int(b))
            else:
                start = int(a or 0)
                end = min(total - 1, int(b)) if b else total - 1
            if start > end or start >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        ctype = mimetypes.guess_type(node["name"])[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        # Share links may be cached by Discord's media proxy (and other link previewers).
        if not public and self.drive.locks.covering(self.drive.index.path_of(node["id"]) or "/"):
            self.send_header("Cache-Control", "no-store")          # locked: the browser keeps no copy
        else:
            self.send_header("Cache-Control", "public, max-age=3600" if public else "private, max-age=60")
        if public:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.split(";")[0] in _ACTIVE_TYPES:
            # A stored web page or SVG must not run scripts with the dashboard's rights.
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; img-src 'self' data:; "
                                                        "style-src 'unsafe-inline'")
        disp = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(node['name'])}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        if self.command == "HEAD":
            return
        pos = start
        while pos <= end:
            n = min(PIECE, end - pos + 1)
            data = self.drive.fs.read_file(node["id"], pos, n)
            if not data:
                break
            self.wfile.write(data)
            pos += len(data)

    # ------------------------------------------------------------ changing
    def _post_api_upload(self):
        path = "/" + (self._query().get("path") or "").strip("/")
        parent, name = posixpath.split(path)
        if not name:
            raise ApiError(400, "missing file name")
        self._node(parent, want_dir=True)
        fs = self.drive.fs
        remaining = int(self.headers.get("Content-Length") or 0)
        # mtime: the time the file has where it comes from (a folder sync on another computer, which
        # tells its copies apart by size and time)
        try:
            mtime = float(self._query()["mtime"]) if self._query().get("mtime") else None
        except ValueError:
            raise ApiError(400, "bad mtime") from None
        new = self.drive.index.resolve(path) is None
        fh = fs.create(path, 0o644)
        offset = 0
        try:
            while remaining > 0:
                data = self.rfile.read(min(PIECE, remaining))
                if not data:
                    break
                fs.write(path, data, offset, fh)
                offset += len(data)
                remaining -= len(data)
            if mtime is not None and not remaining:
                _, _, on = fs._handle(fh)
                with on.lock:
                    on.mtime = mtime
        finally:
            fs.release(path, fh)
        if remaining:
            if mtime is not None and new:        # half a copy is not left behind looking like the file
                try:
                    fs.unlink(path)
                except OSError:
                    pass
            raise ApiError(400, "upload was cut off")
        save = self._query().get("save")
        node = self.drive.index.resolve(path)
        if save in ("now", "later") and node is not None and self.drive.uploader is not None:
            # notes: autosaves wait until typing pauses; "Save" sends it to Discord right away
            self.drive.uploader.enqueue(node["id"], delay=0 if save == "now" else NOTE_IDLE)
        self._json({"ok": True, "path": path, "size": offset, "mtime": node["mtime"] if node else None})

    # ---------- a file sent in parts
    # A folder sync on another computer or a phone sends big files a few MB at a time. Each part is a
    # short request, so nothing between here and there (a reverse proxy that stores a whole request
    # before passing it on, a limit on a request's size, a connection that drops) gets in the way, and
    # a file that was cut off goes on where it stopped. The parts are kept next to the drive's own
    # files (<data dir>/partials) and become the file, by being moved, when the last one is there.
    # When this computer has to catch up first (its upload backlog or its disk is full) the answer
    # says so at once ({"wait": why}) instead of keeping the request open.
    def _partial(self, path, size, mtime):
        """(the file the parts of `path` are collected in, its note file, how much of it is there)."""
        folder = os.path.join(self.drive.cfg.resolved_data_dir, "partials")
        os.makedirs(folder, exist_ok=True)
        part = os.path.join(folder, hashlib.sha256(path.encode("utf-8")).hexdigest()[:32] + ".part")
        note = part[:-5] + ".json"
        try:
            with open(note, encoding="utf-8") as f:
                was = json.load(f)
            same = (was.get("path"), was.get("size"), was.get("mtime")) == (path, size, mtime)
        except (OSError, ValueError):
            same = False
        if not same:                             # parts of another version of the file (or none): start over
            self._drop_partial(part)
            return part, note, 0
        try:
            return part, note, os.path.getsize(part)
        except OSError:
            return part, note, 0

    @staticmethod
    def _drop_partial(part):
        for p in (part, part[:-5] + ".json"):
            try:
                os.remove(p)
            except OSError:
                pass

    def _part_args(self):
        q = self._query()
        path = "/" + (q.get("path") or "").strip("/")
        try:
            size = int(q.get("size") or 0)
            offset = int(q.get("offset") or 0)
            mtime = float(q["mtime"]) if q.get("mtime") else None
        except ValueError:
            raise ApiError(400, "bad size, offset or mtime") from None
        if not posixpath.basename(path) or size < 0 or offset < 0:
            raise ApiError(400, "bad path, size or offset")
        return path, size, offset, mtime

    def _get_api_upload_part(self):
        """How much of a file sent in parts is here already (where to go on after it was cut off)."""
        path, size, _, mtime = self._part_args()
        self._json({"offset": self._partial(path, size, mtime)[2]})

    def _post_api_upload_part(self):
        path, size, offset, mtime = self._part_args()
        self._take_part(path, size, offset, mtime)

    def _take_part(self, path, size, offset, mtime):
        """One part of a file that is sent in parts; answers how far it is, and "done" with the last one."""
        n = int(self.headers.get("Content-Length") or 0)
        if n > PART_MAX or offset + n > size:
            raise ApiError(413 if n > PART_MAX else 400, "that part is too big")
        self._node(posixpath.dirname(path), want_dir=True)
        fs = self.drive.fs
        there = self.drive.index.resolve(path)
        if there is not None and there["is_dir"]:
            raise ApiError(400, f"{path} is a folder")
        fs._refuse_hidden(path)
        fs._check_size_limit(size, path)
        part, note, have = self._partial(path, size, mtime)
        if offset != have:
            if offset:                           # not where we are: say where that is, and the sender goes on from there
                return self._json({"ok": False, "offset": have})
            self._drop_partial(part)             # from the start again
            have = 0
        why = fs.holding_back(extra=n)
        full = None if why else fs.room_for(size - have)
        if full and (fs._pending_staging_bytes() or not self.drive.uploader.is_idle()):
            why = f"its uploads have to free room first ({full})"
        if why:
            return self._json({"ok": False, "offset": have, "wait": why})
        if full:
            self._drop_partial(part)
            raise ApiError(507, f"There is no room for this file ({size / 2**30:.2f} GiB) on the computer that runs the "
                                f"drive: {full}.")
        if not have:
            now = time.time()
            for old in os.listdir(os.path.dirname(part)):        # what nobody came back for
                p = os.path.join(os.path.dirname(part), old)
                try:
                    if now - os.path.getmtime(p) > PART_DAYS * 86400:
                        os.remove(p)
                except OSError:
                    pass
            with open(note, "w", encoding="utf-8") as f:
                json.dump({"path": path, "size": size, "mtime": mtime}, f)
        left = n
        with open(part, "ab") as out:
            while left > 0:
                data = self.rfile.read(min(PIECE, left))
                if not data:
                    break
                out.write(data)
                left -= len(data)
        if left:
            raise ApiError(400, "upload was cut off")           # (what came is kept: the sender asks how much)
        if have + n < size:
            return self._json({"ok": True, "offset": have + n})
        fs.adopt_file(path, part, mtime)
        self._drop_partial(part)
        node = self.drive.index.resolve(path)
        self._json({"ok": True, "done": True, "offset": size, "path": path, "size": size,
                    "mtime": node["mtime"] if node else None})

    # ---------- end-to-end encrypted accounts (vault.py)
    # To this computer such an account is sealed objects under random names (/o/..) and a numbered
    # sequence of sealed changes to a list it can't read (/log/..), all of it files of the account's
    # drive like any others, so that they are stored on Discord, repaired and counted the same way.
    _vault_lock = threading.Lock()

    def _vault(self):
        if not self._e2e:
            raise ApiError(403, "This is for end-to-end encrypted accounts.")
        return self.drive

    @staticmethod
    def _object_path(oid):
        oid = str(oid or "")
        if not re.fullmatch(r"[0-9a-f]{32}", oid):
            raise ApiError(400, "bad object id")
        return f"/o/{oid[:2]}/{oid}"

    def _vault_state(self, d):
        """(the number of the last change, where the last sealed whole list is or None)."""
        snap = None
        node = d.index.resolve("/snap")
        if node is not None and not node["is_dir"]:
            try:
                snap = json.loads(d.fs.read_file(node["id"], 0, node["size"]))
            except ValueError:
                snap = None
        last = int(snap["seq"]) if snap else 0
        folder = d.index.resolve("/log")
        if folder is not None:
            for c in d.index.children(folder["id"]):
                if c["name"].isdigit():
                    last = max(last, int(c["name"]))
        return last, snap

    def _vault_write(self, d, path, data):
        fh = d.fs.create(path, 0o644, wait=False)
        try:
            d.fs.write(path, data, 0, fh)
        finally:
            d.fs.release(path, fh)

    def _get_api_vault(self):
        d = self._vault()
        last, snap = self._vault_state(d)
        st = d.index.stats(max_age=2)
        self._json({"seq": last, "snapshot": snap, "quota": d.cfg.quota_bytes, "used": st["bytes"] + st["version_bytes"]})

    def _get_api_vault_log(self):
        """The sealed changes after number `after`, in order (and where the last sealed whole list is)."""
        d = self._vault()
        try:
            after = int(self._query().get("after") or 0)
        except ValueError:
            after = 0
        last, snap = self._vault_state(d)
        batches, size, more = [], 0, False
        folder = d.index.resolve("/log")
        if folder is not None:
            for c in sorted((c for c in d.index.children(folder["id"]) if c["name"].isdigit()), key=lambda c: int(c["name"])):
                if int(c["name"]) <= after:
                    continue
                if size > 8 * 1024 * 1024:
                    more = True
                    break
                data = d.fs.read_file(c["id"], 0, c["size"])
                size += len(data)
                batches.append([int(c["name"]), base64.b64encode(data).decode("ascii")])
        self._json({"seq": last, "snapshot": snap, "batches": batches, "more": more})

    def _post_api_vault_log(self):
        """Take a batch of sealed changes and give it the next number."""
        d = self._vault()
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 4 * 1024 * 1024:
            raise ApiError(413 if n else 400, "that batch of changes is too big" if n else "nothing to add")
        data = self.rfile.read(n)
        if len(data) != n:
            raise ApiError(400, "upload was cut off")
        with self._vault_lock:
            seq = self._vault_state(d)[0] + 1
            d.index.makedirs("/log")
            self._vault_write(d, f"/log/{seq:012d}", data)
        self._json({"ok": True, "seq": seq})

    def _post_api_vault_snapshot(self):
        """The list as it was after change `seq` is in object `object` now: the changes up to it can go."""
        d = self._vault()
        body = self._body_json()
        try:
            seq, size = int(body.get("seq")), int(body.get("size"))
        except (TypeError, ValueError):
            raise ApiError(400, "bad snapshot") from None
        path = self._object_path(body.get("object"))
        if d.index.resolve(path) is None:
            raise ApiError(404, "that object isn't here")
        with self._vault_lock:
            last, old = self._vault_state(d)
            if seq > last or (old and seq < int(old["seq"])):
                raise ApiError(409, "that is not where the list is")
            self._vault_write(d, "/snap", json.dumps({"seq": seq, "object": body["object"], "size": size}).encode("utf-8"))
            folder = d.index.resolve("/log")
            for c in (d.index.children(folder["id"]) if folder is not None else []):
                if c["name"].isdigit() and int(c["name"]) <= seq:
                    d.fs.unlink(f"/log/{c['name']}")
            if old and old.get("object") != body["object"]:
                try:
                    d.fs.unlink(self._object_path(old["object"]))
                except (OSError, ApiError):
                    pass
        self._json({"ok": True})

    def _get_api_vault_object(self):
        d = self._vault()
        path = self._object_path(self._query().get("id"))
        node = d.index.resolve(path)
        if node is None or node["is_dir"]:
            raise ApiError(404, "that object isn't here")
        self._stream(node, download=True)

    def _post_api_vault_object(self):
        """A sealed object, sent in parts (as files are, see above): ?id=&size=&offset=."""
        d = self._vault()
        q = self._query()
        path = self._object_path(q.get("id"))
        try:
            size, offset = int(q.get("size") or 0), int(q.get("offset") or 0)
        except ValueError:
            raise ApiError(400, "bad size or offset") from None
        if size < 0 or offset < 0:
            raise ApiError(400, "bad size or offset")
        d.index.makedirs(posixpath.dirname(path))
        self._take_part(path, size, offset, None)

    def _post_api_vault_delete(self):
        d = self._vault()
        ids = self._body_json().get("ids") or []
        gone = 0
        for oid in ids[:10000]:
            try:
                d.fs.unlink(self._object_path(oid))
                gone += 1
            except OSError:
                pass
        self._json({"ok": True, "deleted": gone})

    def _get_api_zip(self):
        q = self._query()
        if q.get("paths"):                       # a selection: several files and folders in one ZIP
            try:
                paths = [str(p) for p in json.loads(q["paths"])]
            except (ValueError, TypeError):
                raise ApiError(400, "bad selection") from None
            return self._zip_many(paths)
        path, node = self._node(q.get("path"), want_dir=True)
        self._zip(node, path)

    def _zip(self, node, path):
        """A folder as one .zip download (streamed: nothing is stored on the way)."""
        idx = self.drive.index
        name = (node["name"] or "DDrive") + ".zip"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        out = _Unseekable(self.wfile)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            todo = [(node, "")]
            while todo:
                n, rel = todo.pop()
                for c in idx.children(n["id"]):
                    crel = f"{rel}{c['name']}"
                    if self.drive.fs._hidden(posixpath.join(path, crel)):
                        continue
                    if c["is_dir"]:
                        zf.writestr(zipfile.ZipInfo(crel + "/", time.localtime(c["mtime"])[:6]), b"")
                        todo.append((c, crel + "/"))
                        continue
                    info = zipfile.ZipInfo(crel, time.localtime(max(c["mtime"], 315532800))[:6])
                    with zf.open(info, "w", force_zip64=c["size"] > 2 ** 31) as f:
                        pos = 0
                        while pos < c["size"]:
                            data = self.drive.fs.read_file(c["id"], pos, PIECE)
                            if not data:
                                break
                            f.write(data)
                            pos += len(data)

    def _post_api_copy(self):
        """Duplicate a file (pieces the drive already stores are reused, so this uploads nothing new)."""
        body = self._body_json()
        src, node = self._node(body.get("from"), want_dir=False)
        dst = "/" + (body.get("to") or "").strip("/")
        if self.drive.index.resolve(dst) is not None:
            raise ApiError(409, "Something with that name already exists")
        fs = self.drive.fs
        fh = fs.create(dst, 0o644)
        try:
            pos = 0
            while pos < node["size"]:
                data = fs.read_file(node["id"], pos, PIECE)
                if not data:
                    break
                fs.write(dst, data, pos, fh)
                pos += len(data)
        finally:
            fs.release(dst, fh)
        self._json({"ok": True, "path": dst})

    def _post_api_mkdir(self):
        path = "/" + (self._body_json().get("path") or "").strip("/")
        self.drive.fs.mkdir(path, 0o755)
        self._json({"ok": True, "path": path})

    def _post_api_move(self):
        body = self._body_json()
        src, _ = self._node(body.get("from"))
        dst = "/" + (body.get("to") or "").strip("/")
        if self.drive.index.resolve(dst) is not None:
            raise ApiError(409, "Something with that name already exists")
        if dst.lower().startswith(src.lower() + "/"):
            raise ApiError(400, "A folder can't be moved into itself")
        self._node(posixpath.dirname(dst), want_dir=True)
        self.drive.fs.rename(src, dst)
        self._json({"ok": True, "path": dst})

    def _post_api_delete(self):
        path, node = self._node(self._body_json().get("path"))
        if node["id"] == ROOT_ID:
            raise ApiError(400, "The drive itself can't be deleted")
        fs = self.drive.fs

        def remove(p, n):
            if n["is_dir"]:
                for c in self.drive.index.children(n["id"]):
                    remove(posixpath.join(p, c["name"]), c)
                fs.rmdir(p)
            else:
                fs.unlink(p)
        remove(path, node)
        self._json({"ok": True})

    def _post_api_pin(self):
        body = self._body_json()
        path, node = self._node(body.get("path"))
        on = bool(body.get("on"))
        d = self.drive
        d.index.set_pinned(node["id"], pinned=on, recursive=True)
        if on:
            threading.Thread(target=d.cache.prefetch_node, args=(node["id"],), name="Pin", daemon=True).start()
        else:
            d.cache.free_space(node_id=node["id"], force=True)
        self._json({"ok": True})

    def public_base(self):
        """Where people reach this dashboard from outside: web_public_url, else the first domain in
        web_hosts (https, as a reverse proxy serves it), else a network address with web_lan."""
        cfg = self.server.app.cfg
        if (cfg.web_public_url or "").strip():
            return cfg.web_public_url.strip().rstrip("/")
        if cfg.web_hosts:
            return "https://" + str(cfg.web_hosts[0]).strip().strip("/")
        if cfg.web_lan:
            ips = lan_addresses()
            if ips:
                return f"http://{ips[0]}:{self.app.port}"
        return ""

    def _share_json(self, sid, rec):
        node = self.drive.index.get_by_uid(rec["u"])
        pub = self.public_base()
        direct = ""
        if node is not None and not node["is_dir"] and not rec.get("pw") and not rec.get("up"):
            direct = f"/s/{sid}/{urllib.parse.quote(node['name'])}"     # the file itself, for embedding
        return {"id": sid, "path": f"/s/{sid}", "url": f"{pub}/s/{sid}" if pub else "",
                "direct_path": direct, "direct": f"{pub}{direct}" if pub and direct else "",
                "lan": [f"http://{ip}:{self.app.port}/s/{sid}" for ip in lan_addresses()] if self.server.app.cfg.web_lan else [],
                "item": self.drive.index.path_of(node["id"]) if node else None, "dir": rec.get("d", False),
                "created": rec.get("c"), "expires": rec.get("e"), "password": bool(rec.get("pw")),
                "download": rec.get("dl", True), "views": rec.get("v", 0), "upload": bool(rec.get("up"))}

    def _post_api_share(self):
        body = self._body_json()
        path, node = self._node(body.get("path"))
        if node["id"] == ROOT_ID:
            raise ApiError(400, "Share a folder or file, not the whole drive")
        hours = body.get("hours", 24 * 7)
        upload = None
        if body.get("upload"):                   # a file request: people upload into the folder and see nothing of it
            if not node["is_dir"]:
                raise ApiError(400, "A file request needs a folder for the files to arrive in")
            try:
                upload = max(1, min(100 * 1024, int(body.get("max_mb") or 2048))) * 1024 * 1024
            except (TypeError, ValueError):
                raise ApiError(400, "bad size limit") from None
        sid, rec = self.app.shares.create(node["uid"], node["is_dir"], hours=hours, upload=upload,
                                          password=str(body.get("password") or ""), download=body.get("download", True))
        self._json(self._share_json(sid, rec))

    def _get_api_shares(self):
        items = [self._share_json(sid, rec) for sid, rec in self.app.shares.all().items()]
        items = [i for i in items if not i["item"] or self._shown(i["item"])]
        items.sort(key=lambda x: -(x["created"] or 0))
        self._json({"items": items})

    def _post_api_shares_update(self):
        body = self._body_json()
        rec = self.app.shares.update(str(body.get("id")), hours=body.get("hours"),
                                     password=body.get("password"), download=body.get("download"))
        if rec is None:
            raise ApiError(404, "That link no longer exists")
        self._json(self._share_json(str(body.get("id")), rec))

    def _post_api_shares_revoke(self):
        if not self.app.shares.revoke(str(self._body_json().get("id"))):
            raise ApiError(404, "That link no longer exists")
        self._json({"ok": True})

    # ------------------------------------------------------------ contacts
    def _put_file(self, path, data, save="later", idle=NOTE_IDLE):
        """Write a whole file on the drive (it then uploads like any other)."""
        fs = self.drive.fs
        parent = posixpath.dirname(path)
        if self.drive.index.resolve(parent) is None:
            self.drive.index.makedirs(parent)
        fh = fs.create(path, 0o644)
        try:
            for off in range(0, max(1, len(data)), PIECE):
                if data[off:off + PIECE]:
                    fs.write(path, data[off:off + PIECE], off, fh)
        finally:
            fs.release(path, fh)
        node = self.drive.index.resolve(path)
        if node is not None and self.drive.uploader is not None:
            self.drive.uploader.enqueue(node["id"], delay=0 if save == "now" else idle)

    def _book_read(self):
        from . import contacts
        node = self.drive.index.resolve(contacts.BOOK)
        if node is None:
            return []
        key = (node["mtime"], node["size"], node["version"])
        if self.app._book[0] == key:
            return [dict(c) for c in self.app._book[1]]
        text = self.drive.fs.read_file(node["id"], 0, node["size"] or 0).decode("utf-8", "replace") if node["size"] else ""
        items = contacts.parse_vcards(text)
        self.app._book = (key, items)
        return [dict(c) for c in items]

    def _book_write(self, items):
        from . import contacts
        items = sorted(items, key=lambda c: (c.get("name") or "").lower())
        self._put_file(contacts.BOOK, "".join(contacts.to_vcard(c) for c in items).encode("utf-8"), idle=8.0)
        self.app._book = (None, [])

    def _get_api_contacts(self):
        from . import contacts
        with self.app.contacts_lock:
            items = self._book_read()
        for c in items:
            c.pop("extra", None)
        self._json({"items": items, "file": contacts.BOOK})

    def _post_api_contacts_save(self):
        from . import contacts
        c = self._body_json().get("contact") or {}
        if not any((c.get("name"), c.get("first"), c.get("last"), c.get("org"), c.get("phones"), c.get("emails"))):
            raise ApiError(400, "Give the contact a name, a phone number or an email")
        with self.app.contacts_lock:
            items = self._book_read()
            old = next((x for x in items if x["uid"] == c.get("uid")), None)
            merged = {**contacts.new_contact(), **(old or {}), **{k: v for k, v in c.items() if k != "extra"}}
            if not merged.get("name"):
                merged["name"] = " ".join(x for x in (merged.get("first"), merged.get("last")) if x) or merged.get("org") or ""
            items = [x for x in items if x["uid"] != merged["uid"]] + [merged]
            self._book_write(items)
        merged.pop("extra", None)
        self._json({"ok": True, "contact": merged})

    def _post_api_contacts_delete(self):
        uids = set(map(str, self._body_json().get("uids") or []))
        with self.app.contacts_lock:
            items = self._book_read()
            keep = [x for x in items if x["uid"] not in uids]
            if len(keep) != len(items):
                self._book_write(keep)
        self._json({"ok": True, "removed": len(items) - len(keep)})

    def _post_api_contacts_import(self):
        from . import contacts
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 2 ** 20:
            raise ApiError(413, "That file is too big for contacts (64 MB at most)")
        raw = self.rfile.read(n)
        bom16 = (bytes([0xFF, 0xFE]), bytes([0xFE, 0xFF]))          # Outlook saves CSV as UTF-16
        text = raw.decode("utf-16", "replace") if raw[:2] in bom16 else raw.decode("utf-8-sig", "replace")
        found = contacts.parse_any(text, self._query().get("name") or "")
        if not found:
            raise ApiError(400, "No contacts found. Use a vCard (.vcf) or a Google / Outlook CSV export.")
        with self.app.contacts_lock:
            items = self._book_read()
            added = skipped = 0
            uids = {x["uid"] for x in items}
            for c in found:
                if any(contacts.same_person(c, x) for x in items):
                    skipped += 1
                    continue
                if c["uid"] in uids:
                    c["uid"] = contacts.new_contact()["uid"]
                uids.add(c["uid"])
                items.append(c)
                added += 1
            if added:
                self._book_write(items)
        self._json({"ok": True, "added": added, "skipped": skipped})

    def _get_api_contacts_export(self):
        from . import contacts
        wanted = set(filter(None, (self._query().get("uids") or "").split(",")))
        with self.app.contacts_lock:
            items = self._book_read()
        chosen = [c for c in items if not wanted or c["uid"] in wanted]
        name = "contacts.vcf" if len(chosen) != 1 else (chosen[0]["name"] or "contact") + ".vcf"
        body = "".join(contacts.to_vcard(c) for c in chosen).encode("utf-8")
        self._send(200, body, "text/vcard; charset=utf-8",
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    # ------------------------------------------------------------ checking files
    def _get_api_check(self):
        st = dict(self.drive.checker.state)
        st["now"] = time.time()
        st["bad"] = [b for b in st.get("bad") or [] if self._shown(b.get("path"))]
        if self._names_locked(str(st.get("current") or "")):
            st["current"] = ""
        self._json(st)

    def _post_api_check_start(self):
        path, node = self._node(self._body_json().get("path") or "/", want_dir=None)
        if not self.drive.checker.start(path):
            raise ApiError(409, "A check is already running")
        self._json({"ok": True})

    def _post_api_check_stop(self):
        self.drive.checker.stop()
        self._json({"ok": True})

    def _post_api_check_remove(self):
        """Delete unreadable files for good (no earlier version is kept of something that can't be read)."""
        uids = set(map(str, self._body_json().get("uids") or []))
        idx = self.drive.index
        removed = 0
        for uid in uids:
            node = idx.get_by_uid(uid)
            if node is not None and not node["is_dir"]:
                with self.drive.fs.lock:
                    idx.purge_node(node["id"])
                    self.drive.fs._chunk_maps.pop(node["id"], None)
                removed += 1
        st = self.drive.checker.state
        if st.get("bad"):
            st["bad"] = [b for b in st["bad"] if b["uid"] not in uids]
        self.drive.journal.wake()
        self._json({"ok": True, "removed": removed})

    # ------------------------------------------------------------ password-locked folders
    def _lock_attempt(self, check):
        """Run a password check, slowing down guessing like the sign-in does."""
        addr = "lock:" + self._addr()
        wait = self.app.locked_for(addr)
        if wait:
            raise ApiError(429, f"Too many wrong passwords. Try again in {int(wait) + 1} seconds.")
        result = check()
        self.app.note_login(addr, bool(result))
        return result

    def _get_api_locks(self):
        locks = self.drive.locks
        mine = self.drive.fs.scope.unlocked
        on_drive = locks.mount_unlocked()
        open_here = []
        for path, uid in locks.roots():
            if uid in mine:
                node = self.drive.index.get_by_uid(uid)
                if node is not None:
                    open_here.append({"path": self.drive.index.path_of(node["id"]), "dir": bool(node["is_dir"]),
                                      "on_drive": uid in on_drive})
        self._json({"count": locks.count(), "open": open_here, "minutes": self.drive.cfg.lock_timeout_minutes,
                    "mount": self.drive.cfg.mount_point, "host": self._host_name()})

    def _post_api_locks_add(self):
        body = self._body_json()
        path, node = self._node(body.get("path"))
        password = str(body.get("password") or "")
        if node["id"] == ROOT_ID:
            raise ApiError(400, "Lock a folder or a file, not the whole drive")
        if len(password) < 4:
            raise ApiError(400, "Use a password of at least 4 characters")
        if self.drive.locks.is_root(node["uid"]):
            raise ApiError(409, "It already has a lock. Remove that one first to change the password.")
        self.drive.locks.add(node, password)
        self.app.open_locks_add(self._cookie(), [node["uid"]])       # still open here, where it was just locked
        self.drive.journal.wake()
        log.info("A password lock was added to a %s.", "folder" if node["is_dir"] else "file")
        self._json({"ok": True, "minutes": self.drive.cfg.lock_timeout_minutes})

    def _post_api_locks_unlock(self):
        body = self._body_json()
        uids = self._lock_attempt(lambda: self.drive.locks.matching(str(body.get("password") or "")))
        if not uids:
            raise ApiError(403, "No locked folder opens with that password")
        self.app.open_locks_add(self._cookie(), uids)
        if body.get("drive"):
            self.drive.locks.mount_unlock(uids)
        self._json({"ok": True, "opened": len(uids), "minutes": self.drive.cfg.lock_timeout_minutes})

    def _post_api_locks_lock(self):
        """Lock everything again: in this browser and on this device's drive."""
        self.app.open_locks_clear(self._cookie())
        self.drive.locks.mount_relock()
        self._json({"ok": True})

    def _post_api_locks_remove(self):
        body = self._body_json()
        path, node = self._node(body.get("path"))
        if not self.drive.locks.is_root(node["uid"]):
            raise ApiError(404, "It has no lock")
        if not self._lock_attempt(lambda: self.drive.locks.check(node["uid"], str(body.get("password") or ""))):
            raise ApiError(403, "Wrong password")
        self.drive.locks.remove(node["uid"])
        self.app.open_locks_clear(self._cookie(), [node["uid"]])
        self.drive.journal.wake()
        self._json({"ok": True})

    # ------------------------------------------------------------ gallery and thumbnails
    # Thumbnails are made in the browser (it can decode photos and video; the drive has no image
    # library) and kept here in <data dir>/thumbs, encrypted with the drive's key like everything else.
    # A file's thumbnail is tied to its size and time, so a changed file gets a new one.
    def _thumb_file(self, node):
        uid = str(node["uid"])
        if not re.fullmatch(r"[0-9A-Za-z_-]{4,64}", uid):
            raise ApiError(400, "no thumbnail for this file")
        return os.path.join(self.drive.cfg.resolved_data_dir, "thumbs", uid[:2], uid + ".bin")

    @staticmethod
    def _thumb_key(node):
        return f"{node['size']}:{int(node['mtime'])}".encode()

    def _thumb_read(self, node):
        """The thumbnail of a file: image bytes, b"" when none can be made (remembered), None when not made yet."""
        try:
            with open(self._thumb_file(node), "rb") as f:
                blob = f.read()
            if self.drive.crypto is not None:
                blob = self.drive.crypto.decrypt(blob, b"thumb")
        except (OSError, ApiError):
            return None
        except Exception:                 # another key, or damaged: make it again
            return None
        key, _, data = blob.partition(b"\n")
        return data if key == self._thumb_key(node) else None

    def _get_api_thumb(self):
        path, node = self._node(self._query().get("path"), want_dir=False)
        data = self._thumb_read(node)
        if not data:
            return self._error(404, "no thumbnail yet")
        ctype = "image/webp" if data[:4] == b"RIFF" else "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # the address changes with the file; pictures in a locked folder are not kept by the browser
        self.send_header("Cache-Control", "no-store" if self.drive.locks.covering(path) else "private, max-age=2592000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _post_api_thumb(self):
        """Store the thumbnail the browser made (an empty body: none can be made, don't try again)."""
        path, node = self._node(self._query().get("path"), want_dir=False)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 400 * 1024:
            raise ApiError(413, "thumbnail too large")
        data = self.rfile.read(n)
        if data and not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
            raise ApiError(400, "not an image")
        blob = self._thumb_key(node) + b"\n" + data
        if self.drive.crypto is not None:
            blob = self.drive.crypto.encrypt(blob, b"thumb")
        target = self._thumb_file(node)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = f"{target}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, target)
        self._json({"ok": True})

    def _thumb_state(self, node):
        data = self._thumb_read(node)
        return None if data is None else (1 if data else -1)

    def _get_api_media(self):
        """Photos and videos under a folder (everything below it), newest first."""
        q = self._query()
        path, root = self._node(q.get("path") or "/", want_dir=True)
        try:
            offset, limit = max(0, int(q.get("offset") or 0)), max(1, min(1000, int(q.get("limit") or 300)))
        except ValueError:
            raise ApiError(400, "bad offset or limit") from None
        idx, fs = self.drive.index, self.drive.fs
        # every folder's path, worked out once (instead of walking up from each file)
        dirs = {r["id"]: (r["parent"], r["name"]) for r in idx._all("SELECT id, parent, name FROM nodes WHERE is_dir=1")}
        paths = {ROOT_ID: "/"}

        def dir_path(nid):
            if nid not in paths:
                chain = []
                while nid not in paths and nid in dirs:
                    chain.append(nid)
                    nid = dirs[nid][0]
                base = paths.get(nid)
                for d in reversed(chain):
                    base = None if base is None else posixpath.join(base, dirs[d][1])
                    paths[d] = base
                return paths.get(chain[0]) if chain else None
            return paths[nid]

        like = " OR ".join("name LIKE ?" for _ in _MEDIA_EXT)
        rows = idx._all(f"SELECT * FROM nodes WHERE is_dir=0 AND ({like}) ORDER BY mtime DESC, id DESC",
                        tuple("%." + e for e in _MEDIA_EXT))
        prefix = path.rstrip("/") + "/"
        items, total = [], 0
        for r in rows:
            folder = dir_path(r["parent"])
            if folder is None or not (folder + "/").replace("//", "/").startswith(prefix):
                continue
            p = posixpath.join(folder, r["name"])
            if fs._hidden(p) or (housekeeping(p) and not housekeeping(prefix)):
                continue                         # bookmark banners aren't photos (unless that folder itself is opened)
            total += 1
            if offset < total <= offset + limit:
                items.append(dict(self._entry(p, r), thumb=self._thumb_state(r)))
        self._json({"path": path, "total": total, "offset": offset, "items": items})

    # ------------------------------------------------------------ sync folders
    # Choosing folders on this computer (and what is done with them) reads and writes files outside the
    # drive, so it is only allowed from this computer itself, unless `sync_remote_edit` is on. Running,
    # stopping and pausing a pair works from everywhere.
    def _sync_can_edit(self):
        return self._local_request() or bool(getattr(self.drive.cfg, "sync_remote_edit", False))

    def _sync_mgr(self, edit=False):
        if self.drive.sync is None:
            raise ApiError(503, "Syncing isn't available")
        if edit and not self._sync_can_edit():
            raise ApiError(403, "For safety, folders on this computer can only be chosen on this computer itself "
                                f"(open http://127.0.0.1:{self.app.port}/ there, or use '{_launcher()} sync'). "
                                "It can allow other devices under Sync.")
        return self.drive.sync

    def _sync_job(self, m, jid):
        job = m.job(str(jid or ""))
        if job is None:
            raise ApiError(404, "That folder is no longer synced")
        return job

    def _sync_store(self, m, jobs):
        from . import sync
        sync.save_jobs(jobs, self.drive)
        m.reload(jobs)

    def _lite(self, fn, *args, **kw):
        """Ask something about the DDrive Lite computers of whoever is signed in (syncdevices.py)."""
        from . import sync
        try:
            return fn(self._who() or "", *args, **kw)
        except Gone:
            raise ApiError(404, "That computer no longer syncs folders with this drive. Turn folder syncing on "
                                "again in DDrive Lite there.") from None
        except Offline as e:
            raise ApiError(409, str(e)) from None
        except sync.SyncError as e:
            raise ApiError(400, str(e)) from None

    def _get_api_sync(self):
        from . import sync
        out = {"jobs": [], "running": None, "can_edit": False, "local": False, "remote_edit": False, "host": "",
               "mount": "", "windows": os.name == "nt", "home": "", "account": self._acct is not None,
               "devices": self._lite(self.server.app.lite.listing),
               "modes": [{"id": k, "label": v[0], "direction": v[1], "text": v[2]} for k, v in sync.MODES.items()],
               "triggers": [{"id": k, "label": v} for k, v in sync.TRIGGERS.items()],
               "default_exclude": sync.DEFAULT_EXCLUDE}
        if self._acct is None:                   # the folders of this computer itself: the owner's
            m = self._sync_mgr()
            cfg = self.drive.cfg
            out.update(jobs=m.snapshot(), running=m.running, can_edit=self._sync_can_edit(), local=self._local_request(),
                       remote_edit=bool(cfg.sync_remote_edit), host=socket.gethostname(), mount=cfg.mount_point,
                       home=os.path.expanduser("~") if self._sync_can_edit() else "")
        self._json(out)

    # With `device`, each of these is about a pair of that DDrive Lite computer instead of this one.
    def _post_api_sync_save(self):
        from . import sync
        body = self._body_json()
        new = body.get("job") or {}
        if body.get("device"):
            return self._json({"ok": True, "job": self._lite(self.server.app.lite.save, body["device"], new)})
        m = self._sync_mgr(edit=True)
        body = new
        jobs = [dict(j) for j in m.jobs]
        old = self._sync_job(m, body["id"]) if body.get("id") else None
        try:
            job = sync.normalize_job({**(old or {}), **body}, self.drive.cfg, jobs)
        except sync.SyncError as e:
            raise ApiError(400, str(e)) from None
        if old is not None:
            jobs = [job if j["id"] == job["id"] else j for j in jobs]
        else:
            jobs.append(job)
        self._sync_store(m, jobs)
        if old is None and job["trigger"] != "manual":
            m.run_now(job["id"])
        self._json({"ok": True, "job": job})

    def _post_api_sync_remove(self):
        body = self._body_json()
        if body.get("device"):
            self._lite(self.server.app.lite.remove, body["device"], body.get("id"))
            return self._json({"ok": True})
        m = self._sync_mgr(edit=True)
        job = self._sync_job(m, body.get("id"))
        m.cancel(job["id"])
        self._sync_store(m, [j for j in m.jobs if j["id"] != job["id"]])
        self._json({"ok": True})

    def _post_api_sync_pause(self):
        body = self._body_json()
        if body.get("device"):
            self._lite(self.server.app.lite.pause, body["device"], body.get("id"), bool(body.get("paused")))
            return self._json({"ok": True})
        m = self._sync_mgr()
        job = self._sync_job(m, body.get("id"))
        on = not bool(body.get("paused"))
        if not on:
            m.cancel(job["id"])
        self._sync_store(m, [dict(j, enabled=on) if j["id"] == job["id"] else j for j in m.jobs])
        self._json({"ok": True})

    def _post_api_sync_run(self):
        body = self._body_json()
        if body.get("device"):
            queued = self._lite(self.server.app.lite.run, body["device"], body.get("id"), bool(body.get("again")))
            return self._json({"ok": True, "queued": queued})
        m = self._sync_mgr()
        job = self._sync_job(m, body.get("id"))
        m.run_now(job["id"], again=bool(body.get("again")))
        self._json({"ok": True, "queued": m.running not in (None, job["id"])})

    def _post_api_sync_stop(self):
        body = self._body_json()
        if body.get("device"):
            self._lite(self.server.app.lite.stop, body["device"], body.get("id"))
            return self._json({"ok": True})
        m = self._sync_mgr()
        m.cancel(self._sync_job(m, body.get("id"))["id"])
        self._json({"ok": True})

    # ---------- what a DDrive Lite computer with folder syncing on asks (litesync.py)
    def _post_api_lite_sync_hello(self):
        body = self._body_json()
        self._json(dict(self._lite(self.server.app.lite.hello, body.get("device"), body), ok=True, version=__version__))

    def _post_api_lite_sync_poll(self):
        body = self._body_json()
        try:
            wait = float(body.get("wait") or 0)
        except (TypeError, ValueError):
            wait = 0.0
        self._json(self._lite(self.server.app.lite.poll, body.get("device"), body.get("rev"), wait))

    def _post_api_lite_sync_report(self):
        body = self._body_json()
        self._lite(self.server.app.lite.report, body.get("device"), body.get("status"), body.get("running"))
        self._json({"ok": True})

    def _post_api_lite_sync_answer(self):
        body = self._body_json()
        self._lite(self.server.app.lite.answer, body.get("device"), body.get("req"), body.get("result"))
        self._json({"ok": True})

    def _post_api_lite_sync_bye(self):
        self._lite(self.server.app.lite.bye, self._body_json().get("device"))
        self._json({"ok": True})

    def _get_api_sync_tree(self):
        """Everything under a folder of the drive in one answer: what a folder sync on another computer
        compares its own folder with. files: [path inside the folder, size, time, state]."""
        path = "/" + (self._query().get("path") or "").strip("/")
        idx, fs = self.drive.index, self.drive.fs
        root = idx.resolve(path)
        if root is None or fs._hidden(path):
            return self._json({"exists": False, "dir": False, "files": [], "dirs": []})
        files, dirs = [], []
        todo = [(root, "")] if root["is_dir"] else []
        while todo:
            node, rel_dir = todo.pop()
            for c in idx.children(node["id"]):
                rel = f"{rel_dir}/{c['name']}" if rel_dir else c["name"]
                if fs._hidden(posixpath.join(path, rel)):
                    continue
                if c["is_dir"]:
                    dirs.append(rel)
                    todo.append((c, rel))
                else:
                    files.append([rel, c["size"], c["mtime"], c["state"]])
        self._json({"exists": True, "dir": bool(root["is_dir"]), "files": files, "dirs": dirs})

    def _post_api_sync_remote_edit(self):
        if not self._local_request():
            raise ApiError(403, "Only this computer itself can change this")
        on = bool(self._body_json().get("on"))
        self.drive.cfg.sync_remote_edit = on
        if not getattr(self.drive, "local_test_dir", None):
            saved = Config.load()
            saved.sync_remote_edit = on
            saved.save()
        self._json({"ok": True})

    def _get_api_sync_browse(self):
        """Folders on this computer (or, with `device`, on a DDrive Lite one), for choosing one to sync."""
        from . import sync
        q = self._query()
        if q.get("device"):
            return self._json(self._lite(self.server.app.lite.browse, q["device"], q.get("path") or ""))
        self._sync_mgr(edit=True)
        try:
            self._json(sync.browse(q.get("path"), self.drive.cfg.mount_point))
        except FileNotFoundError as e:
            raise ApiError(404, str(e)) from None
        except PermissionError as e:
            raise ApiError(403, str(e)) from None

    # ------------------------------------------------------------ settings
    # name -> (type, needs a restart, check)
    SETTINGS = {
        "web_hosts": (list, False, None),
        "discord_pace": (str, False, lambda v: v in ("gentle", "balanced", "fast", "off")),
        "uploads_per_minute": (int, False, lambda v: 0 <= v <= 600),
        "deletes_per_minute": (int, False, lambda v: 0 <= v <= 600),
        "requests_per_minute": (int, False, lambda v: 0 <= v <= 1200),
        "web_public_url": (str, False, lambda v: not v or v.startswith(("http://", "https://"))),
        "web_lan": (bool, True, None),
        "web_port": (int, True, lambda v: 1 <= v <= 65535),
        "parity_enabled": (bool, False, None),
        "parity_pieces": (int, False, lambda v: 1 <= v <= 8),
        "scrub_enabled": (bool, False, None),
        "protect_existing": (bool, False, None),
        "compression": (bool, False, None),
        "dedup": (bool, False, None),
        "keep_versions": (bool, True, None),
        "version_retention_days": (float, False, lambda v: v >= 0),
        "snapshot_interval_hours": (float, False, lambda v: v >= 0),
        "snapshot_keep_days": (float, False, lambda v: v >= 0),
        "cache_mode": (str, True, lambda v: v in ("disk", "memory")),
        "hidden_folders": (list, False, None),
        "lock_timeout_minutes": (float, False, lambda v: 0 <= v <= 100000),
        "webdav_enabled": (bool, False, None),
        "signup_mode": (str, False, lambda v: v in MODES),
        "account_quota_bytes": (int, False, lambda v: v >= 0),
        "accounts_max": (int, False, lambda v: v >= 0),
        "operator_name": (str, False, lambda v: len(v) <= 200),
        "operator_contact": (str, False, lambda v: len(v) <= 200),
        "legal_region": (str, False, lambda v: len(v) <= 200),
    }

    def _get_api_settings(self):
        cfg = self.drive.cfg
        self._json({"values": {k: getattr(cfg, k) for k in self.SETTINGS},
                    "restart": [k for k, (_, r, _) in self.SETTINGS.items() if r]})

    def _post_api_settings(self):
        body = self._body_json()
        changes = {}
        for key, value in body.items():
            if key not in self.SETTINGS:
                raise ApiError(400, f"Unknown setting {key}")
            kind, _, ok = self.SETTINGS[key]
            try:
                if kind is list:
                    if isinstance(value, str):
                        value = value.split(",")
                    value = [str(v).strip() for v in value if str(v).strip()]
                    if key == "web_hosts":
                        value = [v.lower().split("://")[-1].split("/")[0] for v in value]
                elif kind is bool:
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                else:
                    value = kind(str(value).strip()) if kind is not str else str(value).strip()
            except ValueError:
                raise ApiError(400, f"{key}: not a valid value") from None
            if ok and not ok(value):
                raise ApiError(400, f"{key}: not a valid value")
            changes[key] = value
        cfg = self.drive.cfg
        for k, v in changes.items():
            setattr(cfg, k, v)
        if any(k in changes for k in ("discord_pace", "uploads_per_minute", "deletes_per_minute", "requests_per_minute")):
            for api in getattr(self.drive.backend, "apis", []):
                api.configure(cfg)               # takes effect right away
        k_restart = [k for k in changes if self.SETTINGS[k][1]]
        if not getattr(self.drive, "local_test_dir", None):
            saved = Config.load()
            for k, v in changes.items():
                setattr(saved, k, v)
            saved.save()
            self.app._cfg_mtime = os.path.getmtime(config_path())
        self._json({"ok": True, "restart": k_restart})

    # ------------------------------------------------------------ notes
    def _get_api_notes(self):
        idx = self.drive.index
        folder = idx.resolve(NOTES)
        items = []
        if folder is not None and folder["is_dir"] and self._shown(NOTES):
            for c in idx.children(folder["id"]):
                if c["is_dir"]:
                    continue
                try:
                    head = self.drive.fs.read_file(c["id"], 0, 600).decode("utf-8", "replace")
                except Exception:
                    head = ""
                title, _, rest = c["name"].rpartition(".") if c["name"].lower().endswith((".md", ".txt")) else (c["name"], "", "")
                items.append({"path": posixpath.join(NOTES, c["name"]), "name": c["name"], "title": title or c["name"],
                              "mtime": c["mtime"], "size": c["size"], "state": c["state"],
                              "snippet": " ".join(head.split())[:140]})
        items.sort(key=lambda n: -n["mtime"])
        self._json({"folder": NOTES, "items": items})

    # ------------------------------------------------------------ history
    def _get_api_versions(self):
        path, node = self._node(self._query().get("path"), want_dir=False)
        rows = self.drive.index.versions_for(node["uid"])
        self._json({"path": path, "current": {"size": node["size"], "mtime": node["mtime"]},
                    "versions": [{"n": i, "at": v["superseded"], "size": v["size"], "reason": v["reason"]}
                                 for i, v in enumerate(rows, 1)]})

    def _post_api_versions_restore(self):
        body = self._body_json()
        path, node = self._node(body.get("path"), want_dir=False)
        idx = self.drive.index
        rows = idx.versions_for(node["uid"])
        n = int(body.get("n") or 0)
        if not 1 <= n <= len(rows):
            raise ApiError(400, "no such version")
        parent_uid = idx.get(node["parent"])["uid"]
        if body.get("copy"):
            stem, ext = os.path.splitext(node["name"])
            name = f"{stem} (version {n}){ext}"
            op = version_put_op(idx, rows[n - 1], new_uid(), parent_uid, name)
        else:
            op = version_put_op(idx, rows[n - 1], node["uid"], parent_uid, node["name"])
        self._publish([op])
        self._json({"ok": True})

    def _get_api_deleted(self):
        rows = [v for v in self.drive.index.deleted_files("/") if self._shown(v["path"])]
        self._json({"items": [{"uid": v["uid"], "path": v["path"], "size": v["size"], "at": v["superseded"]}
                              for v in rows[:5000]]})

    def _deleted_by_uid(self, body):
        """Deleted files picked by uid ({"uids": [...]}), or one by path ({"path": ...})."""
        rows = [v for v in self.drive.index.deleted_files("/") if self._shown(v["path"])]
        if body.get("uids"):
            wanted = set(map(str, body["uids"]))
            return [v for v in rows if v["uid"] in wanted]
        path = "/" + (body.get("path") or "").strip("/")
        return [v for v in rows if (v["path"] or "").lower() == path.lower()][:1]

    def _post_api_undelete(self):
        idx = self.drive.index
        rows = self._deleted_by_uid(self._body_json())
        if not rows:
            raise ApiError(404, "Those files are no longer kept")
        self._publish(undelete_ops(idx, rows))
        self._json({"ok": True, "count": len(rows)})

    def _post_api_purge(self):
        rows = self._deleted_by_uid(self._body_json())
        n = self.drive.index.purge_deleted([v["uid"] for v in rows])
        self.drive.journal.wake()
        self._json({"ok": True, "count": n})

    # ------------------------------------------------------------ snapshots
    def _get_api_snapshots(self):
        self._json({"items": [{"id": s["id"], "at": s["at"], "label": s["label"], "files": s["files"],
                               "bytes": s["bytes"], "keep_until": s["keep_until"]}
                              for s in self.drive.index.snapshots()]})

    def _get_api_snapshot(self):
        q = self._query()
        sid, path = q.get("id"), "/" + (q.get("path") or "").strip("/")
        entries = self.drive.index.snapshot_entries(sid, path)
        depth = 0 if path == "/" else path.count("/")
        items = [{"name": posixpath.basename(e["path"]), "path": e["path"], "dir": e["is_dir"], "size": e["size"],
                  "mtime": e["mtime"]} for e in entries if e["path"].count("/") == depth + 1 and self._shown(e["path"])]
        items.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        self._json({"path": path, "items": items})

    def _post_api_snapshots_create(self):
        label = str(self._body_json().get("label") or "")[:60]
        sid = self.drive.journal.create_snapshot(label or "Manual", float(self.drive.cfg.snapshot_keep_days or 0))
        self._json({"ok": True, "id": sid})

    def _post_api_snapshots_delete(self):
        """Delete snapshots for good, on every device. Whatever only they still kept is removed from Discord."""
        body = self._body_json()
        idx = self.drive.index
        have = {s["id"] for s in idx.snapshots()}
        wanted = have if body.get("all") else {str(i) for i in body.get("ids") or []} & have
        if not wanted:
            raise ApiError(404, "no such snapshot")
        for sid in wanted:
            idx.delete_snapshot(sid)
        self.drive.journal.wake()
        log.info("%d snapshot(s) deleted for good.", len(wanted))
        self._json({"ok": True, "count": len(wanted)})

    def _post_api_snapshots_restore(self):
        body = self._body_json()
        snap = next((s for s in self.drive.index.snapshots() if s["id"] == body.get("id")), None)
        if snap is None:
            raise ApiError(404, "no such snapshot")
        target = default_restore_folder(snap)
        ops, files = snapshot_restore_ops(self.drive.index, snap["id"], body.get("path") or "/", target)
        if not ops:
            raise ApiError(404, "nothing to restore there")
        self._publish(ops)
        self._json({"ok": True, "files": files, "folder": target})

    # ------------------------------------------------------------ share links
    def _shared(self, rest, method):
        sid, _, tail = rest.partition("/")
        gone = False
        if "." in sid:                           # an account's link, /s/<account>.<link>: served from its drive
            try:
                self._enter(sid.partition(".")[0])
            except AccountError:
                gone = True
        rec = None if gone else self.app.shares.get(sid)
        root = self.drive.index.get_by_uid(rec["u"]) if rec else None
        if root is None:
            return self._send(404, _share_message("This link has expired", "Ask for a new one.").encode(),
                              "text/html; charset=utf-8", _SHARE_HEADERS)
        self.drive.fs.scope.unlocked = frozenset(self.drive.locks.covering(self.drive.index.path_of(root["id"])))
        sending = tail == "upload" and method == "POST"       # a file arriving through a file request
        if rec.get("pw"):
            if method == "POST" and not sending:
                addr = "share:" + self._addr()
                wait = self.app.locked_for(addr)
                ok = not wait and self.app.shares.password_ok(rec, self._form().get("password"))
                self.app.note_login(addr, ok)
                if not ok:
                    msg = f"Too many tries. Wait {int(wait) + 1} seconds." if wait else "That password isn't right."
                    return self._send(401, _share_password(sid, rec, msg).encode(), "text/html; charset=utf-8",
                                      _SHARE_HEADERS)
                cookie = f"dds_{sid}={self.app.share_unlock_value(sid, rec)}; Path=/s/{sid}; HttpOnly; SameSite=Lax; Max-Age=43200"
                return self._redirect(f"/s/{sid}", cookie)
            jar = {k.strip(): v for k, _, v in (p.partition("=") for p in (self.headers.get("Cookie") or "").split(";"))}
            if not hmac.compare_digest(jar.get(f"dds_{sid}", ""), self.app.share_unlock_value(sid, rec)):
                if sending:
                    return self._error(403, "Type the link's password first (reload the page)")
                return self._send(200, _share_password(sid, rec).encode(), "text/html; charset=utf-8", _SHARE_HEADERS)
        elif method == "POST" and not sending:
            return self._redirect(f"/s/{sid}")
        if rec.get("up"):
            if not root["is_dir"]:
                return self._send(404, _share_message("This link has expired", "Ask for a new one.").encode(),
                                  "text/html; charset=utf-8", _SHARE_HEADERS)
            if sending:
                return self._request_upload(sid, rec, root)
            if tail:
                return self._send(404, _share_message("Not found", "This link is for sending files.").encode(),
                                  "text/html; charset=utf-8", _SHARE_HEADERS)
            return self._request_page(sid, rec, root)
        if sending:
            return self._error(404, "This link doesn't take files")
        direct = tail not in ("", "raw", "zip")
        if direct:
            # /s/<id>/<file name>: the file itself (an image, GIF, video or song embeds where it is posted).
            # In a shared folder the rest of the address is the path inside it.
            rel = urllib.parse.unquote(tail).strip("/") if root["is_dir"] else ""
        else:
            rel = (self._query().get("p") or "").strip("/")
        node, shown = self._share_target(root, rel)
        if node is None:
            return self._send(404, _share_message("Not found", "That file isn't in this share.").encode(),
                              "text/html; charset=utf-8", _SHARE_HEADERS)
        if (tail == "raw" or direct) and not node["is_dir"]:
            if self._query().get("dl") == "1" and not rec.get("dl", True):
                return self._send(403, b"Downloading is turned off for this link.", "text/plain; charset=utf-8")
            if direct:
                self.app.shares.viewed(sid)
            return self._stream(node, download=self._query().get("dl") == "1", public=not rec.get("pw"))
        if tail == "zip" and node["is_dir"]:
            if not rec.get("dl", True):
                return self._send(403, b"Downloading is turned off for this link.", "text/plain; charset=utf-8")
            return self._zip(node, shown)
        if not rel:
            self.app.shares.viewed(sid)
        children = []
        if node["is_dir"]:
            for c in self.drive.index.children(node["id"]):
                if not self.drive.fs._hidden(posixpath.join(shown, c["name"])):
                    children.append(c)
            children.sort(key=lambda c: (not c["is_dir"], c["name"].lower()))
        text = None
        if not node["is_dir"] and _kind(node["name"]) == "text" and node["size"] <= 2 * 2 ** 20:
            text = self.drive.fs.read_file(node["id"], 0, 256 * 1024).decode("utf-8", "replace")
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        origin = f"{proto}://{self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or ''}"
        html = _share_page(sid, rec, root, node, rel, children, text, origin)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", _SHARE_HEADERS)

    def _share_target(self, root, rel):
        """The node at `rel` inside a shared item (never outside it), and its path on the drive."""
        idx = self.drive.index
        node = root
        shown = idx.path_of(root["id"])
        for part in [p for p in rel.split("/") if p]:
            if part in (".", "..") or not node["is_dir"]:
                return None, None
            node = idx.lookup(node["id"], part)
            if node is None:
                return None, None
            shown = posixpath.join(shown, node["name"])
        if self.drive.fs._hidden(shown):
            return None, None
        return node, shown

class _Unseekable:
    """A response stream for zipfile: write-only, so it adds data descriptors instead of seeking back."""

    def __init__(self, raw):
        self._raw = raw
        self._pos = 0

    def write(self, data):
        self._raw.write(data)
        self._pos += len(data)
        return len(data)

    def tell(self):
        return self._pos

    def flush(self):
        self._raw.flush()

    def seekable(self):
        return False


_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                               "style-src 'self'; script-src 'self'; frame-src 'self'; object-src 'none'; "
                               "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


def _plain_page(body):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>DDrive</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/app.css"></head>
<body class="signin"><main>
  <img src="/logo.svg" alt="" width="40" height="40">
  {body}
</main></body></html>"""


def _signin_page(error="", passkey=False, signup="closed", hint=""):
    err = f'<p class="err" role="alert">{_esc(error)}</p>' if error else ""
    form = """<form method="post" action="/login">
    <label>Your 24 words<textarea name="phrase" rows="4" autocomplete="off" autocapitalize="none" autocorrect="off"
      spellcheck="false" placeholder="the 24 words, in order" required></textarea></label>
    <button class="btn" type="submit">Sign in</button>
  </form>"""
    parts = []
    if passkey:
        parts.append('<div id="pk" hidden><button class="btn big" id="pk-go" type="button">Sign in with a passkey</button>'
                     '<p class="muted small">The owner\'s fingerprint, face, PIN, phone or security key.</p>'
                     '<p class="err" id="pk-err" role="alert" hidden></p></div>')
        parts.append(f"<details{' open' if error else ''}><summary>Use your 24 words</summary>{form}</details>")
    else:
        parts.append(form)
    if signup != "closed":
        parts.append('<p class="muted small">New here? <a href="/signup">Create an account</a></p>')
    if hint:
        parts.append(f'<p class="muted small">You are on the computer that runs the drive: <code>{_esc(hint)}</code> '
                     "shows the owner's words.</p>")
    script = '<script src="/signin.js"></script>' if passkey else ""
    return _plain_page(f"""<h1>Sign in</h1>
  <p class="muted">DDrive</p>
  {err}
  {''.join(parts)}{_LEGAL_LINKS}{script}""")


def _signup_page(mode, invite="", error="", phrase="", ticket=""):
    err = f'<p class="err" role="alert">{_esc(error)}</p>' if error else ""
    back = '<p class="muted small"><a href="/">I already have an account</a></p>'
    if mode == "closed":
        return _plain_page(f"""<h1>No new accounts</h1>
  <p class="muted">New accounts can't be made here. If you have one, sign in with its 24 words.</p>
  {back}""")
    hidden = f'<input type="hidden" name="invite" value="{_esc(invite)}">'
    if mode == "phrase":
        items = "".join(f"<li>{_esc(w)}</li>" for w in phrase.split())
        return _plain_page(f"""<h1>Your 24 words</h1>
  <p class="muted">These words <b>are</b> your account. They sign you in, and they are the key your files are
  encrypted with. Write them down in this order, or keep them in a password manager.</p>
  <ol class="phrase" id="words">{items}</ol>
  <p class="muted small"><button class="link" id="copy" type="button" hidden>Copy the words</button></p>
  <p class="muted"><b>They are shown only now.</b> Nobody can reset them: without them the account and
  everything in it is gone.</p>
  <form method="post" action="/signup">
    <input type="hidden" name="step" value="confirm">
    <input type="hidden" name="phrase" value="{_esc(phrase)}">
    <input type="hidden" name="ticket" value="{_esc(ticket)}">{hidden}
    <label class="tick"><input type="checkbox" name="saved" value="1" required> I have saved these 24 words</label>
    <label class="tick"><input type="checkbox" name="agreed" value="1" required> <span>I agree to the
    <a href="/terms" target="_blank" rel="noopener">terms</a> and the
    <a href="/privacy" target="_blank" rel="noopener">privacy policy</a></span></label>
    <button class="btn" type="submit">Create my account</button>
  </form><script src="/signin.js"></script>""")
    code = (f'<label>Invite code<input name="invite" value="{_esc(invite)}" autocomplete="off" autocapitalize="none" '
            'spellcheck="false" required autofocus></label>') if mode == "invite" else hidden
    return _plain_page(f"""<h1>Create an account</h1>
  <p class="muted">An account here is 24 words that this page makes for you: no name, no email, no password.
  You get storage of your own, encrypted with those words.{' You need an invite code.' if mode == 'invite' else ''}</p>
  {err}
  <form method="post" action="/signup">
    <input type="hidden" name="step" value="new">
    {code}
    <button class="btn" type="submit">Get my 24 words</button>
  </form>
  <p class="muted small">By making an account you agree to the <a href="/terms">terms</a> and the
  <a href="/privacy">privacy policy</a>.</p>
  {back}""")


_LEGAL_LINKS = '<p class="muted small legal-links"><a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></p>'


_ACTIVE_TYPES = {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _human(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


_KINDS = {
    "image": "jpg jpeg png gif webp avif bmp svg heic",
    "video": "mp4 m4v webm mov mkv",
    "audio": "mp3 m4a aac flac wav ogg opus",
    "pdf": "pdf",
    "text": "txt md log csv json xml yml yaml ini conf cfg py js ts css sh bat ps1 c h cpp rs go java toml srt",
}


def _kind(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return next((k for k, exts in _KINDS.items() if ext in exts.split()), "file")


_SHARE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'; "
                               "frame-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

_SHARE_STYLE = """
*{box-sizing:border-box}
:root{--bg:#fbfbfa;--panel:#fff;--ink:#141414;--muted:#6b6b6b;--faint:#a1a1a1;--line:#ebebe8;--hover:#f3f3f1;--accent:#e5484d}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f10;--panel:#151516;--ink:#ececec;--muted:#9b9b9b;--faint:#626262;
--line:#232325;--hover:#1b1b1d;--accent:#ff6369}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI Variable Text",
"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit}
main{width:min(880px,calc(100% - 32px));margin:0 auto;padding:40px 0 64px}
.top{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px;margin-bottom:28px}
.top img{width:20px;height:20px;border-radius:5px}
h1{font-size:22px;font-weight:600;letter-spacing:-.015em;margin:0 0 4px;word-break:break-word}
.m{color:var(--muted);margin:0 0 24px;font-size:14px}
.crumbs{font-size:14px;color:var(--muted);margin:-14px 0 20px}.crumbs a{text-decoration:none}.crumbs a:hover{color:var(--ink)}
.pv{display:grid;place-items:center;background:var(--hover);border-radius:10px;overflow:hidden;margin-bottom:20px;min-height:120px}
.pv img,.pv video{display:block;max-width:100%;max-height:72vh}.pv audio{width:calc(100% - 32px);margin:28px 16px}
.pv iframe{width:100%;height:78vh;border:0;background:#fff}
.pv pre{width:100%;max-height:72vh;overflow:auto;margin:0;padding:18px 20px;font:13px/1.6 ui-monospace,"SF Mono",Consolas,monospace;
white-space:pre-wrap;word-break:break-word}
.btns{display:flex;flex-wrap:wrap;gap:8px}
.b{display:inline-flex;align-items:center;height:38px;padding:0 16px;border-radius:8px;background:var(--ink);color:var(--bg);
text-decoration:none;font-weight:500;border:0;font-size:14px;cursor:pointer}
.b.g{background:none;color:var(--ink);border:1px solid var(--line)}
.list{border-top:1px solid var(--line);margin-bottom:24px}
.row{display:flex;align-items:center;gap:12px;min-height:46px;padding:0 6px;border-bottom:1px solid var(--line);text-decoration:none}
.row:hover{background:var(--hover)}.row .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .s{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.ic{width:18px;height:18px;flex:none;color:var(--muted)}
.lock{max-width:360px;margin:12vh auto 0}
.lock input{display:block;width:100%;height:40px;margin:6px 0 14px;padding:0 12px;border:1px solid var(--line);border-radius:8px;
background:var(--panel);color:var(--ink);font-size:15px;outline:none}
.lock label{color:var(--muted);font-size:13px}.lock .b{width:100%;justify-content:center}
.err{color:var(--accent);font-size:14px}
.f{margin-top:48px;color:var(--faint);font-size:12px}
"""

_ICON_FOLDER = ('<svg class="ic" viewBox="0 0 20 20" fill="currentColor" fill-opacity=".18" stroke="currentColor" '
                'stroke-width="1.5"><path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.6 1.8H16a1.5 1.5 0 0 1 1.5 1.5v7.7A1.5 '
                '1.5 0 0 1 16 16.5H4a1.5 1.5 0 0 1-1.5-1.5z"/></svg>')
_ICON_FILE = ('<svg class="ic" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" '
              'stroke-linejoin="round"><path d="M5 2.5h6.5L15 6v11.5H5z"/><path d="M11.5 2.5V6H15"/></svg>')


def _share_shell(title, body, head=""):
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport "
            f"content='width=device-width,initial-scale=1'><meta name=robots content=noindex><title>{_esc(title)}</title>{head}"
            f"<link rel=icon href='/logo.svg'><style>{_SHARE_STYLE}</style></head><body><main>"
            f"<div class=top><img src='/logo.svg' alt=''>Shared with you</div>{body}"
            f"<p class=f>Shared from DDrive</p></main></body></html>")


def _share_message(title, text):
    return _share_shell(title, f"<h1>{_esc(title)}</h1><p class=m>{_esc(text)}</p>")


def _share_password(sid, rec, error=""):
    err = f"<p class=err role=alert>{_esc(error)}</p>" if error else ""
    return _share_shell("Password needed", f"""<form class=lock method=post action="/s/{sid}">
<h1>Password needed</h1><p class=m>Whoever shared this protected it with a password.</p>{err}
<label>Password<input name=password type=password autocomplete=current-password required autofocus></label>
<button class=b type=submit>Open</button></form>""")


def _until(rec):
    if not rec.get("e"):
        return "no expiry"
    return "link works until " + time.strftime("%d %b %Y, %H:%M", time.localtime(rec["e"]))


def _preview_tags(sid, node, rel, origin):
    """Open Graph tags, so Discord (and other apps) show the photo, video or song when the page link is posted."""
    kind = _kind(node["name"])
    path = f"/s/{sid}/" + urllib.parse.quote(rel if rel else node["name"])
    url = origin + path
    ctype = mimetypes.guess_type(node["name"])[0] or "application/octet-stream"
    tags = [("og:title", node["name"]), ("og:site_name", "DDrive"), ("og:url", f"{origin}/s/{sid}")]
    if kind == "image":
        tags += [("og:type", "website"), ("og:image", url), ("og:image:type", ctype), ("twitter:card", "summary_large_image"),
                 ("twitter:image", url)]
    elif kind == "video":
        tags += [("og:type", "video.other"), ("og:video", url), ("og:video:url", url), ("og:video:secure_url", url),
                 ("og:video:type", ctype), ("og:video:width", "1280"), ("og:video:height", "720"), ("twitter:card", "player")]
    elif kind == "audio":
        tags += [("og:type", "music.song"), ("og:audio", url), ("og:audio:type", ctype)]
    else:
        tags += [("og:type", "website"), ("og:description", f"{_human(node['size'])}, shared from DDrive")]
    return "".join(f"<meta property='{k}' content='{_esc(v)}'>" for k, v in tags)


def _share_page(sid, rec, root, node, rel, children, text, origin=""):
    base = f"/s/{sid}"
    q = (lambda r: "?" + urllib.parse.urlencode({"p": r}) if r else "")
    crumbs = ""
    if rel:
        parts = rel.split("/")
        links = [f"<a href='{base}'>{_esc(root['name'])}</a>"]
        for i, part in enumerate(parts[:-1]):
            links.append(f"<a href='{base}{q('/'.join(parts[:i + 1]))}'>{_esc(part)}</a>")
        crumbs = f"<p class=crumbs>{' / '.join(links)}</p>"
    name = _esc(node["name"])
    if node["is_dir"]:
        rows = []
        for c in children:
            crel = f"{rel}/{c['name']}".strip("/")
            icon = _ICON_FOLDER if c["is_dir"] else _ICON_FILE
            size = "" if c["is_dir"] else _human(c["size"])
            rows.append(f"<a class=row href='{base}{q(crel)}'>{icon}<span class=n>{_esc(c['name'])}</span>"
                        f"<span class=s>{size}</span></a>")
        listing = f"<div class=list>{''.join(rows)}</div>" if rows else "<p class=m>This folder is empty.</p>"
        files = sum(1 for c in children if not c["is_dir"])
        btn = f"<div class=btns><a class=b href='{base}/zip{q(rel)}'>Download all (ZIP)</a></div>" if rec.get("dl", True) else ""
        return _share_shell(node["name"], f"{crumbs}<h1>{name}</h1><p class=m>Folder &middot; {files} file(s) &middot; "
                                          f"{_until(rec)}</p>{listing}{btn}")
    raw = f"{base}/raw{q(rel)}"
    kind = _kind(node["name"])
    if kind == "image":
        pv = f"<div class=pv><img src='{raw}' alt='{name}'></div>"
    elif kind == "video":
        pv = f"<div class=pv><video src='{raw}' controls playsinline preload=metadata></video></div>"
    elif kind == "audio":
        pv = f"<div class=pv><audio src='{raw}' controls preload=metadata></audio></div>"
    elif kind == "pdf":
        pv = f"<div class=pv><iframe src='{raw}' title='{name}'></iframe></div>"
    elif text is not None:
        pv = f"<div class=pv><pre>{_esc(text)}</pre></div>"
    else:
        pv = ""
    sep = "&" if rel else "?"
    btns = []
    if rec.get("dl", True):
        btns.append(f"<a class=b href='{raw}{sep}dl=1'>Download</a>")
    if rel:
        up = "/".join(rel.split("/")[:-1])
        btns.append(f"<a class='b g' href='{base}{q(up)}'>Back to the folder</a>")
    if not rec.get("dl", True) and not pv:
        btns.append("<span class=m>This kind of file can't be shown here, and downloading is turned off.</span>")
    return _share_shell(node["name"], f"{crumbs}<h1>{name}</h1><p class=m>{_human(node['size'])} &middot; {_until(rec)}</p>"
                                      f"{pv}<div class=btns>{''.join(btns)}</div>",
                        _preview_tags(sid, node, rel, origin) if origin and not rec.get("pw") else "")
