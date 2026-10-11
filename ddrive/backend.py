"""Storage backends with zero-knowledge encryption support.

`DiscordBackend` stores every chunk as an attachment on its own message in the
configured channel; journal entries and index checkpoints live in the same channel.
`LocalBackend` mimics the same behaviour on a local folder and is used for
testing without touching Discord.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from .discord_api import DiscordAPI, DiscordError

log = logging.getLogger("ddrive.backend")

INDEX_MARKER = "DISCORDDRIVE_INDEX"
INDEX_FILENAME = "discorddrive-index.db.gz"
_CURSOR_RE = re.compile(r"\bc=(\d+)")


@dataclass
class StoredChunk:
    message_id: str
    attachment_id: str
    url: str


def url_expired(url: str, margin: float = 300.0) -> bool:
    """Discord CDN links carry an `ex` (hex unix time) expiry parameter."""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        ex = q.get("ex")
        if not ex:
            return False
        return int(ex[0], 16) < time.time() + margin
    except Exception:
        return True


def _index_content(encrypted: bool, cursor) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    c = f" c={cursor}" if cursor is not None else ""
    if encrypted:
        return f"{INDEX_MARKER} ENC_V3{c} {stamp}\nEncrypted index checkpoint."
    return f"{INDEX_MARKER} v3{c} {stamp}\nDDrive index checkpoint - do not delete or unpin."


def _with_cursor(m):
    m = dict(m)
    match = _CURSOR_RE.search((m.get("content") or "").split("\n", 1)[0])
    m["cursor"] = match.group(1) if match else None
    return m


def _pick_latest(candidates):
    """Newest checkpoint: the one furthest along the journal; legacy backups only if there is none."""
    candidates = [_with_cursor(m) for m in candidates]
    journal = [m for m in candidates if m["cursor"] is not None]
    if journal:
        return max(journal, key=lambda m: (int(m["cursor"]), int(m["id"])))
    return max(candidates, key=lambda m: int(m["id"])) if candidates else None


def is_encrypted_index(msg) -> bool:
    return "ENC_" in (msg.get("content") or "").split("\n", 1)[0]


def _is_index_msg(m) -> bool:
    return (m.get("content") or "").startswith(INDEX_MARKER) and bool(m.get("attachments"))


def snowflake_time(mid) -> float:
    """Unix time a Discord message id ("snowflake") was created."""
    return ((int(mid) >> 22) + 1420070400000) / 1000.0


class DiscordBackend:
    """Stores pieces in one channel. Extra bots (`extra_apis`) share the uploading: Discord limits
    how fast each bot may post, so N bots post up to N times as fast. All bots must be in the
    channel; any of them can read and delete every piece."""

    def __init__(self, api: DiscordAPI, channel_id: str, crypto=None, extra_apis=()):
        self.api = api
        self.channel_id = str(channel_id)
        self.crypto = crypto
        self.apis = [api, *extra_apis]
        self._next = 0
        self._rr_lock = threading.Lock()

    @property
    def upload_slots(self) -> int:
        return len(self.apis)

    def _pick(self) -> DiscordAPI:
        with self._rr_lock:
            api = self.apis[self._next % len(self.apis)]
            self._next += 1
        return api

    def upload(self, filename: str, data: bytes, content: str = "") -> StoredChunk:
        msg = self._pick().send_file(self.channel_id, filename, data, content)
        att = msg["attachments"][0]
        return StoredChunk(msg["id"], att["id"], att["url"])

    def download(self, message_id: str, url: str):
        """Returns (data, refreshed_url_or_None)."""
        if url and not url_expired(url):
            try:
                return self.api.download_url(url), None
            except DiscordError as e:
                if e.status not in (401, 403, 404, 410):
                    raise
                log.debug("CDN link for %s rejected (%s); refreshing", message_id, e.status)
        msg = self._pick().get_message(self.channel_id, message_id)
        atts = msg.get("attachments") or []
        if not atts:
            raise DiscordError(404, f"message {message_id} has no attachment")
        new_url = atts[0]["url"]
        return self.api.download_url(new_url), new_url

    def message_info(self, message_id: str):
        """{'filename', 'url', 'attachment_id'} of a stored piece, or None if it is gone from Discord."""
        try:
            msg = self._pick().get_message(self.channel_id, message_id)
        except DiscordError as e:
            if e.status == 404:
                return None
            raise
        atts = msg.get("attachments") or []
        if not atts:
            return None
        return {"filename": atts[0].get("filename", ""), "url": atts[0].get("url"), "attachment_id": atts[0].get("id")}

    @staticmethod
    def time_of(mid) -> float:
        return snowflake_time(mid)

    def delete(self, message_id: str) -> None:
        try:
            self.api.delete_message(self.channel_id, message_id)
        except DiscordError as e:
            if e.status != 404:
                raise

    # ------------------------------------------------------------- journal
    def post_text(self, content: str) -> str:
        return self.api.send_message(self.channel_id, content)["id"]

    def messages_after(self, after, limit=100):
        """The next `limit` messages after id `after`, oldest first."""
        page = self.api.get_messages(self.channel_id, after=after or "0", limit=limit) or []
        return sorted(page, key=lambda m: int(m["id"]))

    def latest_message_id(self):
        page = self.api.get_messages(self.channel_id, limit=1) or []
        return page[0]["id"] if page else None

    @staticmethod
    def id_for_time(t: float) -> str:
        """The Discord message id ("snowflake") for unix time t, for 'messages since then'."""
        return str(max(0, int(t * 1000) - 1420070400000) << 22)

    def fetch_attachment(self, msg) -> bytes:
        atts = msg.get("attachments") or []
        if not atts:
            raise DiscordError(404, f"message {msg.get('id')} has no attachment")
        data, _ = self.download(msg["id"], atts[0].get("url"))
        return data

    # ---------------------------------------------------------- index backup
    def save_index(self, data: bytes, previous_message_id=None, cursor=None) -> str:
        if self.crypto:
            upload_data = self.crypto.encrypt(data)
            fn = f"idx_{secrets.token_hex(8)}.bin"
        else:
            upload_data = data
            fn = INDEX_FILENAME
        msg = self.api.send_file(self.channel_id, fn, upload_data, _index_content(bool(self.crypto), cursor))
        try:
            self.api.pin(self.channel_id, msg["id"])
        except DiscordError as e:
            log.warning("Could not pin index checkpoint: %s", e)
        if previous_message_id and previous_message_id != msg["id"]:
            for fn_act in (self.api.unpin, self.api.delete_message):
                try:
                    fn_act(self.channel_id, previous_message_id)
                except DiscordError:
                    pass
        return msg["id"]

    def find_latest_index(self, scan_pages: int = 10):
        """Returns the newest index checkpoint message (dict with a 'cursor' key), or None."""
        candidates = []
        try:
            candidates = [m for m in self.api.list_pins(self.channel_id) if _is_index_msg(m)]
        except DiscordError as e:
            log.warning("Could not list pins: %s", e)
        if not candidates:
            before = None
            for _ in range(scan_pages):
                page = self.api.get_messages(self.channel_id, before=before)
                if not page:
                    break
                candidates = [m for m in page if _is_index_msg(m)]
                if candidates:
                    break
                before = page[-1]["id"]
        return _pick_latest(candidates)

    def load_latest_index(self, scan_pages: int = 10):
        """Returns (message_id, data) for the newest index backup, or None."""
        latest = self.find_latest_index(scan_pages)
        if latest is None:
            return None
        return latest["id"], self.load_index_message(latest)

    # ------------------------------------------------- other pinned messages
    def save_pinned(self, filename: str, data: bytes, content: str) -> str:
        """Store a message that has to be found again without knowing its id (the backup of the list
        of accounts): it is pinned, as index checkpoints are. `content` starts with its marker."""
        msg = self.api.send_file(self.channel_id, filename, data, content)
        try:
            self.api.pin(self.channel_id, msg["id"])
        except DiscordError:
            self.delete(msg["id"])          # not pinned, nothing would ever find it
            raise
        return msg["id"]

    def pinned(self, marker: str):
        """The pinned messages whose text starts with `marker`."""
        return [m for m in self.api.list_pins(self.channel_id)
                if (m.get("content") or "").startswith(marker) and m.get("attachments")]

    def load_index_message(self, msg) -> bytes:
        raw_data = self.api.download_url(msg["attachments"][0]["url"])
        if raw_data.startswith(b"DENC"):
            if not self.crypto:
                raise RuntimeError("The index backup on Discord is encrypted, but no encryption key is configured.")
            raw_data = self.crypto.decrypt(raw_data)
        return raw_data


class LocalBackend:
    """Fake Discord: every 'message' is a file in `root`. For tests, and for the change journal of an
    account, which stays on the computer that serves it (accounts.py)."""

    upload_slots = 1

    def __init__(self, root: str, max_attachment: int = 10 * 1024 * 1024, latency: float = 0.0, crypto=None):
        self.root = root
        self.max_attachment = max_attachment
        self.latency = latency
        self.crypto = crypto
        os.makedirs(root, exist_ok=True)
        self._lock = threading.Lock()
        self._last = 0
        self.uploads = 0          # counters, for tests
        self.uploaded_bytes = 0

    def _new_id(self) -> str:
        # Several LocalBackend instances (simulated devices) may share `root`:
        # reserve each id with an exclusive marker file so ids stay unique and ordered.
        with self._lock:
            mid = max(self._last + 1, time.time_ns())
            while True:
                try:
                    os.close(os.open(self._p(mid, "id"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                    break
                except FileExistsError:
                    mid += 1
            self._last = mid
            return str(mid)

    def _p(self, mid, ext):
        return os.path.join(self.root, f"{mid}.{ext}")

    def _msg(self, mid):
        with open(self._p(mid, "json"), encoding="utf-8") as f:
            meta = json.load(f)
        atts = [{"url": f"local://{mid}"}] if os.path.exists(self._p(mid, "bin")) else []
        return {"id": mid, "content": meta.get("content", ""), "attachments": atts}

    def upload(self, filename, data, content=""):
        if len(data) > self.max_attachment:
            raise DiscordError(413, "Request entity too large")
        time.sleep(self.latency)
        mid = self._new_id()
        with open(self._p(mid, "bin"), "wb") as f:
            f.write(data)
        with open(self._p(mid, "json"), "w", encoding="utf-8") as f:
            json.dump({"filename": filename, "content": content}, f)
        with self._lock:
            self.uploads += 1
            self.uploaded_bytes += len(data)
        return StoredChunk(mid, mid, f"local://{mid}")

    def download(self, message_id, url):
        time.sleep(self.latency)
        try:
            with open(self._p(message_id, "bin"), "rb") as f:
                return f.read(), None
        except FileNotFoundError:
            raise DiscordError(404, f"Unknown message {message_id}") from None

    def message_info(self, message_id):
        if not os.path.exists(self._p(message_id, "bin")):
            return None
        try:
            with open(self._p(message_id, "json"), encoding="utf-8") as f:
                name = json.load(f).get("filename", "")
        except (OSError, ValueError):
            name = ""
        return {"filename": name, "url": f"local://{message_id}", "attachment_id": message_id}

    @staticmethod
    def time_of(mid) -> float:
        return int(mid) / 1e9

    def delete(self, message_id):
        for ext in ("bin", "json"):
            try:
                os.remove(self._p(message_id, ext))
            except FileNotFoundError:
                pass

    def _ids(self):
        return sorted(int(n[:-5]) for n in os.listdir(self.root) if n.endswith(".json") and n[:-5].isdigit())

    def post_text(self, content):
        mid = self._new_id()
        with open(self._p(mid, "json"), "w", encoding="utf-8") as f:
            json.dump({"content": content}, f)
        return mid

    def messages_after(self, after, limit=100):
        after = int(after or 0)
        return [self._msg(str(i)) for i in self._ids() if i > after][:limit]

    def latest_message_id(self):
        ids = self._ids()
        return str(ids[-1]) if ids else None

    @staticmethod
    def id_for_time(t: float) -> str:
        return str(int(t * 1e9))   # LocalBackend ids are nanosecond timestamps

    def fetch_attachment(self, msg):
        return self.download(msg["id"], None)[0]

    def _pins(self):
        try:
            with open(os.path.join(self.root, "pins.json")) as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def save_index(self, data, previous_message_id=None, cursor=None):
        if self.crypto:
            data = self.crypto.encrypt(data)
            fn = f"idx_{secrets.token_hex(8)}.bin"
        else:
            fn = INDEX_FILENAME
        chunk = self.upload(fn, data, _index_content(bool(self.crypto), cursor))
        pins = [p for p in self._pins() if p != previous_message_id] + [chunk.message_id]
        with open(os.path.join(self.root, "pins.json"), "w") as f:
            json.dump(pins, f)
        if previous_message_id and previous_message_id != chunk.message_id:
            self.delete(previous_message_id)
        return chunk.message_id

    def _pinned_msgs(self):
        msgs = []
        for mid in self._pins():
            try:
                msgs.append(self._msg(mid))
            except FileNotFoundError:
                pass
        return msgs

    def find_latest_index(self):
        return _pick_latest([m for m in self._pinned_msgs() if _is_index_msg(m)])

    def save_pinned(self, filename, data, content):
        mid = self.upload(filename, data, content).message_id
        pins = self._pins() + [mid]
        with open(os.path.join(self.root, "pins.json"), "w") as f:
            json.dump(pins, f)
        return mid

    def pinned(self, marker):
        return [m for m in self._pinned_msgs() if (m.get("content") or "").startswith(marker) and m.get("attachments")]

    def load_index_message(self, msg) -> bytes:
        data, _ = self.download(msg["id"], None)
        if data.startswith(b"DENC"):
            if not self.crypto:
                raise RuntimeError("The index backup is encrypted, but no encryption key is configured.")
            data = self.crypto.decrypt(data)
        return data

    def load_latest_index(self):
        latest = self.find_latest_index()
        if latest is None:
            return None
        return latest["id"], self.load_index_message(latest)
