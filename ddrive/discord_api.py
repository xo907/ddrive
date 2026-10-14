"""Discord REST client supporting curl.exe (standard on Windows 10/11) and urllib.

Only the handful of endpoints DDrive needs are implemented. The bot never
connects to the gateway, so no privileged intents or background connections are required.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import __version__

log = logging.getLogger("ddrive.discord")

API_BASE = "https://discord.com/api/v10"
USER_AGENT = f"DDrive (https://github.com/xo907/ddrive, {__version__})"


def _ram_tmpdir():
    """A RAM-backed temp folder on Linux, so uploads keep working when the disk is full."""
    d = "/dev/shm"
    if os.name == "posix" and os.path.isdir(d) and os.access(d, os.W_OK):
        return d
    return None


_TMPDIR = _ram_tmpdir()


# Requests per minute, per bot: (posting pieces/messages, deleting messages, everything else).
# Discord allows more, but staying well below its limits means it never has to tell us to slow down.
PACES = {
    "gentle": (30, 15, 90),
    "balanced": (60, 30, 120),
    "fast": (120, 90, 300),
}
DEFAULT_PACE = "gentle"


class Pacer:
    """Spaces requests of one kind evenly; after Discord says "slow down" it goes slower for a while."""

    def __init__(self, per_minute=0):
        self._lock = threading.Lock()
        self._next = 0.0
        self._factor = 1.0
        self._slow_until = 0.0
        self.set(per_minute)

    def set(self, per_minute):
        self.interval = 60.0 / per_minute if per_minute and per_minute > 0 else 0.0

    def wait(self):
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            factor = self._factor if now < self._slow_until else 1.0
            at = max(now, self._next)
            self._next = at + self.interval * factor
        if at > now:
            time.sleep(at - now)

    def slow_down(self):
        with self._lock:
            now = time.monotonic()
            self._factor = min(4.0, self._factor * 1.5) if now < self._slow_until else 1.5
            self._slow_until = now + 300


UNKNOWN_CHANNEL = 10003     # Discord's error code: the channel doesn't exist (or this bot can't see it)


class DiscordError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Discord HTTP {status}: {message}")
        self.status = status
        self.code = None          # Discord's own error code (10008 Unknown Message, 50001 Missing Access, ...)
        try:
            body = json.loads(message)
            if isinstance(body, dict) and isinstance(body.get("code"), int):
                self.code = body["code"]
        except (TypeError, ValueError):
            pass


def is_gone(e) -> bool:
    """Does this error prove a message is gone? Only a 404 does, and not "Unknown Channel" (a wrong
    channel id or a bot that left the server: every piece would look gone). A 403 means the bot may
    not read it, which says nothing about whether it is still there."""
    return isinstance(e, DiscordError) and e.status == 404 and e.code != UNKNOWN_CHANNEL


def _encode_multipart(fields, files):
    """fields: {name: (bytes, content_type)}; files: {name: (filename, bytes, content_type)}"""
    boundary = uuid.uuid4().hex
    parts = []
    for name, (value, ctype) in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode()
        )
        parts.append(value)
        parts.append(b"\r\n")
    for name, (filename, value, ctype) in files.items():
        safe = filename.replace('"', "_")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode()
        )
        parts.append(value)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


_OUT_OF_MEMORY = (0xC0000017, 0xC000012D, 0xC000009A)   # Windows: no memory, commit limit, no resources


def _curl_failure(returncode, stderr):
    """Why curl failed, in words: its own message, or what a Windows crash code means."""
    code = returncode & 0xFFFFFFFF
    err = (stderr or b"").decode("utf-8", "replace").strip()
    if code in _OUT_OF_MEMORY or "out of memory" in err.lower():
        return "this computer is out of memory, curl could not run"
    if code >= 0x80000000:
        return f"curl crashed with Windows error 0x{code:08X}"
    return err.splitlines()[0] if err else f"curl exit code {returncode}"


class DiscordAPI:
    def __init__(self, token: str, timeout: float = 600.0, retries: int = 8):
        token = (token or "").strip()
        if token.lower().startswith("bot "):
            token = token[4:].strip()
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.curl_bin = shutil.which("curl") or shutil.which("curl.exe")
        self.has_curl = bool(self.curl_bin)
        self.pacers = {"post": Pacer(), "delete": Pacer(), "other": Pacer()}
        self.rate_limited = 0                # times Discord said "slow down" (shown in the log now and then)
        self._rl_logged = (time.monotonic(), 0)
        self.set_pace(DEFAULT_PACE)

    def set_pace(self, pace=DEFAULT_PACE, posts=0, deletes=0, other=0):
        """How many requests per minute this bot sends: a preset, each number overridable (0 = preset)."""
        p, d, o = PACES.get((pace or DEFAULT_PACE).lower(), PACES[DEFAULT_PACE]) if pace != "off" else (0, 0, 0)
        self.pacers["post"].set(posts or p)
        self.pacers["delete"].set(deletes or d)
        self.pacers["other"].set(other or o)

    def configure(self, cfg):
        self.set_pace(getattr(cfg, "discord_pace", DEFAULT_PACE), int(getattr(cfg, "uploads_per_minute", 0) or 0),
                      int(getattr(cfg, "deletes_per_minute", 0) or 0), int(getattr(cfg, "requests_per_minute", 0) or 0))
        return self

    @staticmethod
    def _kind(method, url):
        if not url.startswith(API_BASE):
            return None                      # attachment downloads from Discord's CDN aren't limited this way
        if method == "DELETE":
            return "delete"
        if method == "POST" and "/messages" in url:
            return "post"
        return "other"

    # ------------------------------------------------------------------ core
    def _send_curl(self, method, url, body=None, headers=None, auth=True, multipart_file=None):
        cmd = [self.curl_bin or "curl", "-s", "-S", "-X", method, "--max-time", str(int(self.timeout))]
        temp_files = []
        try:
            # Only two tiny files (headers); request data goes through stdin, never to disk.
            with tempfile.NamedTemporaryFile(delete=False, dir=_TMPDIR) as hf:
                hdr_path = hf.name
            temp_files.append(hdr_path)
            cmd.extend(["-D", hdr_path])
            cmd.extend(["-w", "\n__HTTP_STATUS__:%{http_code}"])

            req_headers = {"User-Agent": USER_AGENT}
            if auth and self.token:
                req_headers["Authorization"] = f"Bot {self.token}"
            if headers:
                req_headers.update(headers)

            # Pass headers through a file so the bot token never appears in the
            # process list (command lines are visible to other local users).
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="\n", dir=_TMPDIR) as rf:
                for k, v in req_headers.items():
                    rf.write(f"{k}: {v}\n")
                req_hdr_path = rf.name
            temp_files.append(req_hdr_path)
            cmd.extend(["-H", f"@{req_hdr_path}"])
            # No "Expect: 100-continue" on uploads: an upload that dies after Discord's interim
            # "100 Continue" would otherwise be reported with 100 as its status.
            cmd.extend(["-H", "Expect:"])

            stdin_data = None
            if multipart_file:
                # tuple of (field_name, filename, data_bytes, optional_payload_json)
                field_name, filename, data_bytes, payload_json = multipart_file
                stdin_data = data_bytes
                # curl's -F syntax treats ; , and " specially inside the filename
                safe_name = "".join("_" if ch in '";,\r\n' else ch for ch in filename)
                cmd.extend(["-F", f"{field_name}=@-;filename={safe_name}"])
                if payload_json:
                    # --form-string: no @file / <file / ;type= interpretation of the value
                    cmd.extend(["--form-string", f"payload_json={payload_json}"])
            elif body is not None:
                if isinstance(body, str):
                    body = body.encode("utf-8")
                stdin_data = body
                cmd.extend(["--data-binary", "@-"])

            cmd.append(url)
            proc = subprocess.run(cmd, capture_output=True, input=stdin_data)
            raw = proc.stdout
            if proc.returncode != 0:
                # curl still prints the status marker when a transfer fails midway, so the exit
                # code decides: whatever arrived is incomplete and the request is tried again.
                raise ConnectionError(_curl_failure(proc.returncode, proc.stderr))
            marker = b"\n__HTTP_STATUS__:"
            pos = raw.rfind(marker)
            if pos != -1:
                resp_body = raw[:pos]
                try:
                    status_code = int(raw[pos + len(marker):].strip())
                except ValueError:
                    status_code = 0
            else:
                resp_body = raw
                status_code = 0

            resp_headers = {}
            if os.path.exists(hdr_path):
                try:
                    with open(hdr_path, "r", encoding="latin-1", errors="replace") as f:
                        for line in f:
                            if ":" in line:
                                hk, _, hv = line.partition(":")
                                resp_headers[hk.strip().lower()] = hv.strip()
                except Exception:
                    pass

            return status_code, resp_body, resp_headers
        finally:
            for p in temp_files:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _send_urllib(self, method, url, body=None, headers=None, auth=True):
        hdrs = {"User-Agent": USER_AGENT}
        if auth and self.token:
            hdrs["Authorization"] = f"Bot {self.token}"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, data, resp_headers
        except urllib.error.HTTPError as e:
            resp_headers = {k.lower(): v for k, v in e.headers.items()}
            return e.code, e.read(), resp_headers

    def _send(self, method, url, body=None, headers=None, auth=True, multipart_file=None, retries=None):
        retries = retries or self.retries
        delay = 1.0
        last_err = None
        kind = self._kind(method, url)
        for _ in range(retries):
            if kind:
                self.pacers[kind].wait()
            try:
                if self.has_curl:
                    status, data, resp_hdrs = self._send_curl(
                        method, url, body=body, headers=headers, auth=auth, multipart_file=multipart_file
                    )
                else:
                    if multipart_file:
                        field_name, filename, data_bytes, payload_json = multipart_file
                        fields = {}
                        if payload_json:
                            fields["payload_json"] = (payload_json.encode("utf-8"), "application/json")
                        files = {field_name: (filename, data_bytes, "application/octet-stream")}
                        body, ctype = _encode_multipart(fields, files)
                        headers = dict(headers or {})
                        headers["Content-Type"] = ctype
                    status, data, resp_hdrs = self._send_urllib(
                        method, url, body=body, headers=headers, auth=auth
                    )
            except Exception as e:
                last_err = e
                log.warning("Network error reaching Discord (%s); retrying in %.0fs", e, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            if status < 200:  # nothing final arrived (DNS failure, connection reset, timeout, only "100 Continue")
                last_err = ConnectionError("no HTTP response")
                log.warning("No response from Discord; retrying in %.0fs", delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            if 200 <= status < 300:
                if resp_hdrs.get("x-ratelimit-remaining") == "0":
                    try:
                        wait = float(resp_hdrs.get("x-ratelimit-reset-after", "0"))
                        if 0 < wait < 60:
                            time.sleep(wait)
                    except ValueError:
                        pass
                return status, data

            if status == 429:
                retry_after = 1.0
                try:
                    retry_after = float(json.loads(data).get("retry_after", 1.0))
                except Exception:
                    try:
                        retry_after = float(resp_hdrs.get("retry-after", "1"))
                    except (TypeError, ValueError):
                        pass
                if kind:
                    self.pacers[kind].slow_down()
                self._note_rate_limit(method, url, retry_after)
                time.sleep(retry_after + 0.25)
                continue

            if status >= 500:
                last_err = DiscordError(status, data[:300].decode("utf-8", "replace"))
                log.warning("Discord server error %s; retrying in %.0fs", status, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            raise DiscordError(status, data[:500].decode("utf-8", "replace"))

        raise DiscordError(0, f"Giving up on {method} {url.split('?')[0]}: {last_err}")

    def _note_rate_limit(self, method, url, retry_after):
        """One line now and then instead of one per rate limit (details at debug level)."""
        self.rate_limited += 1
        log.debug("Rate limited (%s %s); waiting %.2fs", method, url.split("?")[0], retry_after)
        since, count = self._rl_logged
        if retry_after >= 5:
            log.warning("Discord asked to wait %.0f s; waiting, then slowing down for a while.", retry_after)
        elif time.monotonic() - since > 600:
            log.info("Discord asked DDrive to slow down %d time(s) in the last %d minutes; going slower for a while. "
                     "(Settings: Discord pace.)", self.rate_limited - count, max(1, int((time.monotonic() - since) / 60)))
            self._rl_logged = (time.monotonic(), self.rate_limited)

    def request(self, method, path, json_body=None):
        body = None
        headers = {}
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, data = self._send(method, API_BASE + path, body=body, headers=headers)
        if status == 204 or not data:
            return None
        return json.loads(data)

    # ------------------------------------------------------------- endpoints
    def get_me(self):
        return self.request("GET", "/users/@me")

    def get_channel(self, channel_id):
        return self.request("GET", f"/channels/{channel_id}")

    def get_member(self, guild_id, user_id):
        return self.request("GET", f"/guilds/{guild_id}/members/{user_id}")

    def allow_in_channel(self, channel_id, user_id, allow: int):
        """Let one member (a bot) do `allow` (permission bits) in a channel. Needs Manage Permissions there."""
        return self.request("PUT", f"/channels/{channel_id}/permissions/{user_id}",
                            {"type": 1, "allow": str(allow), "deny": "0"})

    def send_file(self, channel_id, filename: str, data: bytes, content: str = ""):
        payload = {
            "content": content[:2000],
            "attachments": [{"id": 0, "filename": filename}],
            "allowed_mentions": {"parse": []},
        }
        payload_json = json.dumps(payload)
        status, resp = self._send(
            "POST",
            f"{API_BASE}/channels/{channel_id}/messages",
            multipart_file=("files[0]", filename, data, payload_json),
        )
        return json.loads(resp)

    def get_message(self, channel_id, message_id):
        return self.request("GET", f"/channels/{channel_id}/messages/{message_id}")

    def get_messages(self, channel_id, before=None, after=None, limit=100):
        """With `after`, returns the `limit` messages directly after that id (in any order)."""
        q = f"?limit={limit}" + (f"&before={before}" if before else "") + (f"&after={after}" if after else "")
        return self.request("GET", f"/channels/{channel_id}/messages{q}")

    def send_message(self, channel_id, content: str):
        return self.request("POST", f"/channels/{channel_id}/messages",
                            {"content": content[:2000], "allowed_mentions": {"parse": []}})

    def delete_message(self, channel_id, message_id):
        return self.request("DELETE", f"/channels/{channel_id}/messages/{message_id}")

    def pin(self, channel_id, message_id):
        try:
            return self.request("PUT", f"/channels/{channel_id}/messages/pins/{message_id}")
        except DiscordError as e:
            if e.status not in (404, 405):
                raise
            return self.request("PUT", f"/channels/{channel_id}/pins/{message_id}")

    def unpin(self, channel_id, message_id):
        try:
            return self.request("DELETE", f"/channels/{channel_id}/messages/pins/{message_id}")
        except DiscordError as e:
            if e.status not in (404, 405):
                raise
            return self.request("DELETE", f"/channels/{channel_id}/pins/{message_id}")

    def list_pins(self, channel_id):
        try:
            r = self.request("GET", f"/channels/{channel_id}/messages/pins?limit=50")
            if isinstance(r, dict) and "items" in r:
                return [it["message"] for it in r["items"]]
            elif isinstance(r, list):
                return r
        except DiscordError as e:
            if e.status not in (404, 405):
                raise
        return self.request("GET", f"/channels/{channel_id}/pins") or []

    def download_url(self, url: str) -> bytes:
        """Download an attachment from Discord's CDN (no auth header)."""
        status, data = self._send("GET", url, auth=False, retries=min(5, self.retries))
        return data
