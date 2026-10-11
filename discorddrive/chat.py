"""Talking to Discord as your own account, for the message logger and archiver (chatlog.py).

The drive itself talks to Discord as a bot (discord_api.py). A bot can't see your direct messages or
the servers it isn't in, so reading your conversations uses your account's token instead: plain
requests for the history (AccountAPI) and one listening connection for what arrives live (Gateway).
"""

import datetime
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import ws

log = logging.getLogger("discorddrive.chat")

API = "https://discord.com/api/v9"
GATEWAY = "wss://gateway.discord.gg"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")
TEXT_CHANNELS = (0, 2, 5, 13)        # text, voice chat, announcement, stage chat
DISCORD_EPOCH = 1420070400000


class AccountError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def clean_token(token):
    token = str(token or "").strip().strip('"').strip()
    return token[4:].strip() if token.lower().startswith("bot ") else token


def snowflake_from_ms(ms):
    return str((int(ms) - DISCORD_EPOCH) << 22)


def parse_ts(text):
    """A Discord timestamp ('2024-01-03T10:00:00.123000+00:00') as milliseconds, or None."""
    if not text:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        try:
            t = datetime.datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return int(t.timestamp() * 1000)


def dm_name(channel):
    if channel.get("name"):
        return channel["name"]
    names = [u.get("global_name") or u.get("username") or "" for u in channel.get("recipients") or []]
    return ", ".join(n for n in names if n) or "Unknown DM"


class AccountAPI:
    def __init__(self, token, timeout=30.0):
        self.token = clean_token(token)
        self.timeout = timeout
        self.rate_limited = 0

    def _open(self, req):
        return urllib.request.urlopen(req, timeout=self.timeout)

    def request(self, method, path, query=None, stop=None, body=None):
        """The decoded answer of one request. Waits out "slow down" answers; `stop` (an Event) ends the waiting."""
        data_out = json.dumps(body).encode("utf-8") if body is not None else None
        pairs = [(k, v) for k, v in (query or {}).items() if v not in (None, "")]
        url = API + path + ("?" + urllib.parse.urlencode(pairs) if pairs else "")
        attempt = 0
        while True:
            if stop is not None and stop.is_set():
                raise InterruptedError("stopped")
            req = urllib.request.Request(url, data=data_out, method=method, headers={
                "Authorization": self.token, "User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                **({"Content-Type": "application/json"} if data_out is not None else {})})
            try:
                with self._open(req) as resp:
                    status, data = resp.status, resp.read()
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
            except OSError as e:
                attempt += 1
                if attempt > 4:
                    raise AccountError(0, f"Discord could not be reached ({getattr(e, 'reason', e)})") from None
                self._wait(2.0 ** attempt, stop)
                continue
            try:
                body = json.loads(data) if data else None
            except ValueError:
                body = None
            if status == 429:
                self.rate_limited += 1
                self._wait(float((body or {}).get("retry_after") or 2) + 0.3, stop)
                continue
            if status >= 500 and attempt < 4:
                attempt += 1
                self._wait(2.0 ** attempt, stop)
                continue
            if status >= 400:
                raise AccountError(status, (body or {}).get("message") or f"Discord answered with HTTP {status}")
            return body

    @staticmethod
    def _wait(seconds, stop):
        if stop is not None:
            if stop.wait(seconds):
                raise InterruptedError("stopped")
        else:
            time.sleep(seconds)

    def me(self):
        return self.request("GET", "/users/@me")

    def guilds(self):
        return self.request("GET", "/users/@me/guilds", {"with_counts": "true"}) or []

    def dms(self):
        return self.request("GET", "/users/@me/channels") or []

    def guild_channels(self, guild_id):
        return self.request("GET", f"/guilds/{guild_id}/channels") or []

    def messages(self, channel_id, before=None, limit=100, stop=None):
        return self.request("GET", f"/channels/{channel_id}/messages", {"limit": limit, "before": before}, stop=stop) or []

    def refresh_urls(self, urls):
        """{old link: working link} for attachment links that ran out (Discord's links last about a day)."""
        r = self.request("POST", "/attachments/refresh-urls", body={"attachment_urls": list(urls)}) or {}
        return {x.get("original"): x.get("refreshed") for x in r.get("refreshed_urls") or [] if x.get("refreshed")}

    def download(self, url):
        """An attachment, as an open stream (the caller closes it)."""
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=self.timeout)


class Gateway:
    """The listening connection: heartbeat, resume after a drop, and waiting longer between failed tries.

    Calls on_status(text), on_dispatch(type, data) and on_fatal(message) from its own thread."""

    def __init__(self, token, on_dispatch, on_status=None, on_fatal=None, connect=None):
        self.token = token
        self.on_dispatch = on_dispatch
        self.on_status = on_status or (lambda s: None)
        self.on_fatal = on_fatal or (lambda m: None)
        self._connect = connect or (lambda url: ws.WebSocket(url))
        self.seq = None
        self.session_id = None
        self.resume_url = None
        self.backoff = 1.0
        self._stop = threading.Event()
        self._ws = None
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="DiscordGateway", daemon=True)
        self._thread.start()
        return self

    def close(self):
        self._stop.set()
        sock = self._ws
        if sock is not None:
            try:
                sock.close(1000)
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(5)
        self.on_status("stopped")

    def _run(self):
        while not self._stop.is_set():
            self.on_status("reconnecting" if self.session_id else "connecting")
            code = 1006
            try:
                self._listen()
            except ws.Closed as e:
                code = e.code
            except Exception as e:
                log.debug("Discord listening connection: %s", e)
            if self._stop.is_set():
                return
            if code == 4004:
                self._stop.set()
                self.on_status("stopped")
                self.on_fatal("Discord rejected the token (it may have been reset). Log in again in Settings.")
                return
            if code in (4007, 4009):       # the session is gone: start a new one
                self.session_id = None
            self.on_status("reconnecting")
            self._stop.wait(self.backoff)
            self.backoff = min(self.backoff * 2, 30.0)

    def _send(self, op, d):
        self._ws.send(json.dumps({"op": op, "d": d}, separators=(",", ":")))

    def _listen(self):
        resuming = bool(self.session_id)
        sock = self._ws = self._connect(f"{(resuming and self.resume_url) or GATEWAY}/?v=9&encoding=json")
        interval, beat_at, acked = None, None, True
        try:
            while not self._stop.is_set():
                wait = 60.0 if beat_at is None else max(0.0, beat_at - time.monotonic())
                text = sock.recv(wait) if wait > 0 else None
                if text is None:
                    if beat_at is None or time.monotonic() < beat_at:
                        if beat_at is None:
                            raise ws.Closed(1006, "no hello from Discord")
                        continue
                    if not acked:
                        raise ws.Closed(4000, "Discord stopped answering")
                    acked = False
                    self._send(1, self.seq)
                    beat_at = time.monotonic() + interval
                    continue
                packet = json.loads(text)
                op, d = packet.get("op"), packet.get("d")
                if packet.get("s"):
                    self.seq = packet["s"]
                if op == 10:
                    interval = d["heartbeat_interval"] / 1000.0
                    beat_at = time.monotonic() + interval * random.random()
                    if self.session_id:
                        self._send(6, {"token": self.token, "session_id": self.session_id, "seq": self.seq})
                    else:
                        self._send(2, {
                            "token": self.token,
                            "properties": {"os": "Windows", "browser": "Chrome", "device": ""},
                            # Invisible, so that logging never changes how your status looks to others.
                            "presence": {"status": "invisible", "since": 0, "activities": [], "afk": False},
                            "compress": False,
                        })
                elif op == 11:
                    acked = True
                elif op == 1:
                    self._send(1, self.seq)
                elif op == 7:
                    raise ws.Closed(4000, "Discord asked to reconnect")
                elif op == 9:
                    if not d:
                        self.session_id = None
                    raise ws.Closed(4000, "the session ended")
                elif op == 0:
                    t = packet.get("t")
                    if t == "READY":
                        self.session_id = d.get("session_id")
                        self.resume_url = d.get("resume_gateway_url")
                    if t in ("READY", "RESUMED"):
                        self.backoff = 1.0
                        self.on_status("connected")
                    try:
                        self.on_dispatch(t, d)
                    except Exception as e:
                        log.warning("The message logger failed on %s: %s", t, e, exc_info=True)
        finally:
            self._ws = None
            try:
                sock.close(1000)
            except Exception:
                pass
