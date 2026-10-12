"""Discord messages for an end-to-end encrypted account: the logger and archiver (chatlog.py) run on
one of the account's own computers, and what they save goes into the account sealed.

For other accounts the computer with the drive reads Discord and keeps the messages. For an
end-to-end account it may not: it would read every message. So a computer of the account does it
(DDrive or DDrive Lite, with folder syncing on), with the same code, on top of something that looks
like a drive to that code and is the sealed account underneath. The Discord token is kept on that
computer only. The account's page in a browser asks its questions through the host in sealed
messages (the same questions the dashboard asks a host, see ChatHandlers) and shows the answers.
"""

import base64
import io
import json
import logging
import os
import tempfile
import threading
import time

from . import vault

log = logging.getLogger("ddrive.chat")

SYNC_EVERY = 10.0                # how old the list of files may be when the logger looks something up
ANSWER_MAX = 8 * 1024 * 1024     # the most one answer carries (an export, a picture)


class _Scope:
    unlocked = None


class _Locks:
    @staticmethod
    def covering(path):
        return []


class _Cfg:
    def __init__(self, data_dir, device_id, token):
        self.resolved_data_dir = data_dir
        self.device_id = device_id
        self.discord_user_token = token


class _Index:
    """The account's list of files, as chatstore.py asks a drive's index."""

    def __init__(self, shim):
        self.s = shim

    @staticmethod
    def _node(n):
        if n is None:
            return None
        return {"id": n["id"], "uid": n["id"] or "root", "name": n.get("name") or "", "is_dir": bool(n.get("dir")),
                "size": n.get("size") or 0, "mtime": n.get("mtime") or 0, "version": n.get("obj") or "", "state": "synced"}

    def resolve(self, path):
        with self.s.lock:
            self.s.fresh()
            return self._node(self.s.v.resolve(path))

    def children(self, nid):
        with self.s.lock:
            return [self._node(n) for n in self.s.v._by_folder().get(nid, {}).values()]

    def makedirs(self, path):
        with self.s.lock:
            made = ""
            for part in path.strip("/").split("/"):
                made += "/" + part
                if self.s.v.resolve(made) is None:
                    self.s.v.mkdir(made)


class _Fs:
    """Reading and writing whole files in the account, as chatstore.py asks a drive's filesystem."""

    def __init__(self, shim):
        self.s = shim
        self.scope = _Scope()
        self._open = {}

    def read_file(self, nid, offset, size):
        with self.s.lock:
            n = self.s.v.nodes.get(nid)
            if n is None or n.get("dir"):
                raise FileNotFoundError(nid)
            return vault.open_range(base64.b64decode(n["key"]), lambda a, k: self.s.v._fetch(n["obj"], a, k), n["size"], offset, size)

    def put_file(self, path, src, stop=None):
        with tempfile.TemporaryFile(prefix="dd-chat-") as tmp:
            while True:
                if stop is not None and stop():
                    raise InterruptedError("stopped")
                data = src.read(256 * 1024)
                if not data:
                    break
                tmp.write(data)
            size = tmp.tell()
            tmp.seek(0)
            with self.s.lock:
                self.s.v.put(path, read=tmp.read, size=size, stop=stop)

    def create(self, path, mode=0o644):
        fh = object()
        self._open[id(fh)] = (fh, io.BytesIO())
        return id(fh)

    def write(self, path, data, offset, fh):
        buf = self._open[fh][1]
        buf.seek(offset)
        buf.write(data)

    def release(self, path, fh):
        data = self._open.pop(fh)[1].getvalue()
        with self.s.lock:
            self.s.v.put(path, data)

    def unlink(self, path):
        with self.s.lock:
            n = self.s.v.resolve(path)
            if n is not None and n["id"]:
                self.s.v.purge([n["id"]])


class AccountAsDrive:
    """What chatlog.Chat needs of a drive, on top of an end-to-end encrypted account."""

    local_test_dir = None
    web = None

    def __init__(self, client, key, data_dir, device_id):
        os.makedirs(data_dir, exist_ok=True)
        self.v = vault.Vault(client, key)
        self.v.name = device_id
        self.lock = threading.RLock()
        self._synced = 0.0
        self._token_file = os.path.join(data_dir, "discord-token")
        token = ""
        try:
            with open(self._token_file, encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            pass
        self.cfg = _Cfg(data_dir, device_id, token)
        self.index, self.fs, self.locks = _Index(self), _Fs(self), _Locks()

    def fresh(self):
        """Bring the list of files up to date now and then (other devices of the account write there too)."""
        if time.monotonic() - self._synced > SYNC_EVERY:
            self.v.sync()
            self._synced = time.monotonic()

    def sync_now(self):
        """Bring it up to date right now: before something that has to see all there is."""
        with self.lock:
            self.v.sync()
            self._synced = time.monotonic()

    def save_token(self, token):
        """The Discord token stays on this computer: in a file only this user can read."""
        if not token:
            try:
                os.remove(self._token_file)
            except OSError:
                pass
            return
        fd = os.open(self._token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)


class _Done(Exception):
    pass


class ChatService:
    """Answers the questions of the account's Messages page, on the computer that does the work."""

    def __init__(self, client, key, data_dir, device_name):
        from . import chatlog                    # (only loaded where Messages is used)
        self._chatlog = chatlog
        self.drive = AccountAsDrive(client, key, data_dir, device_name)
        self.drive.chat = self.chat = chatlog.Chat(self.drive)
        self.name = device_name
        self.chat.start()

    def stop(self):
        try:
            self.chat.flush()
        except Exception as e:
            log.debug("Discord messages: %s", e)
        self.chat.stop()

    def call(self, method, route, query=None, body=None):
        """One question of the dashboard (`route` as in /api/<route>): {status, json} or {status, type, data}
        (bytes, base64) or {redirect}."""
        chatlog = self._chatlog
        out = {}
        raw = io.BytesIO()

        class Handler(chatlog.ChatHandlers):
            drive = self.drive
            _acct = None
            wfile = raw
            command = method

            def _shown(self, path):
                return True

            def _query(self):
                return {str(k): str(v) for k, v in (query or {}).items()}

            def _body_json(self):
                return body if isinstance(body, dict) else {}

            def _json(self, obj, status=200):
                out.update(status=status, json=obj)

            def _error(self, status, message):
                out.update(status=status, json={"error": message})

            def _send(self, status, data, ctype="text/plain", headers=None):
                out.update(status=status, type=ctype)
                raw.write(data)

            def _redirect(self, url, cookie=None):
                out.update(redirect=url)

            def send_response(self, status):
                out["status"] = status

            def send_header(self, name, value):
                if name.lower() == "content-type":
                    out["type"] = value

            def end_headers(self):
                pass

        fn = getattr(Handler, f"_{method.lower()}_api_{route.strip('/').replace('/', '_')}", None)
        if fn is None or not route.startswith("chat"):
            return {"status": 404, "json": {"error": "not found"}}
        try:
            fn(Handler())
        except chatlog.ApiErrorProxy as e:
            return {"status": e.status, "json": {"error": str(e)}}
        except Exception as e:
            log.warning("Discord messages: %s %s: %s", method, route, e)
            return {"status": 500, "json": {"error": str(e) or "That didn't work"}}
        if "json" in out:
            if route == "chat" and isinstance(out["json"], dict):
                out["json"]["device"] = self.name
            return out
        if "redirect" in out:
            return out
        data = raw.getvalue()
        if len(data) > ANSWER_MAX:
            return {"status": 413, "json": {"error": "That is too much to send in one piece. Narrow down what is shown and try again."}}
        out["data"] = base64.b64encode(data).decode("ascii")
        out.setdefault("status", 200)
        return out
