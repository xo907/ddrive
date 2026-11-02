"""Telegram: a second platform where the drive's sealed pieces rest, so Discord isn't the only one.

A Telegram channel is used like a spare channel (channels.py): pieces go there, and placement counts
Discord and Telegram as two places that can each be lost as a whole. Every group of pieces then gets
enough spare pieces on the other platform to be rebuilt from it alone: losing every Discord channel at
once (an account or bot banned, servers deleted) loses no file. Telegram sees what Discord sees: pieces
sealed on the computer that stores them, under random names.

What Telegram's bot API allows, and what follows from it:
* A bot posts files of up to 50 MB and reads back files of up to 20 MB (getFile): pieces are 9 MB.
* A bot can't read a channel's history. A piece's id here carries what reading it back needs:
  "<message id>.<unix time>.<file id>" (and channels.py puts "tg<chat>-" before it). Whether a piece is
  still there is asked of that message (editing its buttons to what they are: "not modified" means it
  is there, "not found" that it is gone), one piece at a time.
* A bot can only find one message without knowing its id: the chat's pinned one. The pinned message is
  a small directory (DIRECTORY) naming the copies of the index checkpoints and of the list of accounts
  kept here, so a drive whose Discord is gone can start again from Telegram.
* A bot deletes messages older than 48 hours only as an admin with the right to delete messages.
* About one message a second per chat: posting is paced (PACE).
"""

import io
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .backend import INDEX_FILENAME, StoredChunk, _index_content

log = logging.getLogger("ddrive.telegram")

API = "https://api.telegram.org"
PREFIX = "tg"                         # a Telegram place's name among the channels: tg<chat id, "-" as "n">
MAX_READ = 20 * 1024 * 1024           # the biggest file a bot can read back
PACE = 1.05                           # seconds between two posts in one chat
NO_ACCESS_PAUSE = 600.0               # a chat that refuses the bot is left out this long
DIRECTORY = "DDRIVE_DIRECTORY"
KEEP_INDEX = 2                        # index checkpoint copies kept in the chat


def place_of(chat_id):
    """The name a Telegram chat has among the drive's channels (no "-": that separates channel and piece)."""
    return PREFIX + str(chat_id).strip().replace("-", "n")


def is_place(chan):
    return isinstance(chan, str) and chan.startswith(PREFIX)


class TelegramError(Exception):
    def __init__(self, status, description, retry_after=None):
        super().__init__(f"Telegram {status}: {description}")
        self.status = status
        self.description = description or ""
        self.retry_after = retry_after

    @property
    def gone(self):
        d = self.description.lower()
        return self.status == 400 and ("not found" in d or "message_id_invalid" in d or "message to edit" in d)

    @property
    def refused(self):
        return self.status in (401, 403) or "chat not found" in self.description.lower()


class TelegramAPI:
    """Calls to Telegram's bot API for one bot (no library: urllib), with 429s and dropped connections retried."""

    def __init__(self, token, timeout=120.0, retries=4, base=None):
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.base = (base or os.environ.get("DDRIVE_TELEGRAM_API") or API).rstrip("/")
        self._pace = {}                # chat -> when it may be posted to again
        self._pace_lock = threading.Lock()

    def _wait_turn(self, chat):
        with self._pace_lock:
            now = time.time()
            at = max(now, self._pace.get(chat, 0.0))
            self._pace[chat] = at + PACE
        if at > now:
            time.sleep(at - now)

    def call(self, method, params=None, files=None):
        url = f"{self.base}/bot{self.token}/{method}"
        params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in (params or {}).items() if v is not None}
        last = None
        for attempt in range(self.retries + 1):
            if files:
                boundary = uuid.uuid4().hex
                body = io.BytesIO()
                for k, v in params.items():
                    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
                for field, (name, data) in files.items():
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                               f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"))
                    body.write(data)
                    body.write(b"\r\n")
                body.write(f"--{boundary}--\r\n".encode("ascii"))
                req = urllib.request.Request(url, data=body.getvalue(), method="POST",
                                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            else:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    got = json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                try:
                    got = json.loads(e.read() or b"{}")
                except ValueError:
                    got = {"ok": False, "error_code": e.code, "description": e.reason}
            except (OSError, ValueError) as e:              # a dropped connection, a timeout
                last = TelegramError(0, f"can't be reached ({e})")
                time.sleep(min(30, 2 ** attempt))
                continue
            if got.get("ok"):
                return got.get("result")
            err = TelegramError(int(got.get("error_code") or 0), got.get("description") or "",
                                (got.get("parameters") or {}).get("retry_after"))
            if err.status == 429 and attempt < self.retries:                 # too fast: told how long to wait
                time.sleep(min(120, float(err.retry_after or 5)))
                last = err
                continue
            if err.status >= 500 and attempt < self.retries:
                time.sleep(min(30, 2 ** attempt))
                last = err
                continue
            raise err
        raise last

    def send_document(self, chat, name, data, caption=""):
        self._wait_turn(str(chat))
        return self.call("sendDocument", {"chat_id": chat, "caption": caption or None, "disable_notification": "true",
                                          "disable_content_type_detection": "true"}, files={"document": (name, data)})

    def download(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        path = info.get("file_path")
        if not path:
            raise TelegramError(400, "the file can't be read back (bigger than 20 MB?)")
        last = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(f"{self.base}/file/bot{self.token}/{path}", timeout=self.timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    raise TelegramError(e.code, f"reading the file back: {e.reason}") from None
                last = TelegramError(e.code, str(e.reason))
            except OSError as e:
                last = TelegramError(0, f"can't be reached ({e})")
            time.sleep(min(30, 2 ** attempt))
        raise last


def _raw(message_id, file_id, at=None):
    return f"{int(message_id)}.{int(at if at is not None else time.time())}.{file_id}"


def _parse(raw):
    """(message id, unix time, file id) of a piece's id here."""
    mid, _, rest = str(raw).partition(".")
    t, _, file_id = rest.partition(".")
    return int(mid), int(t or 0), file_id


class TelegramBackend:
    """One Telegram chat, behind the backend interface channels.py uses for a spare channel."""

    can_list = False                  # (heal.py asks about its pieces one by one)
    keeps_copies = True               # (index checkpoints and the list of accounts are copied here too)

    def __init__(self, api, chat_id, crypto=None):
        self.api = api
        self.apis = [api]
        self.chat_id = str(chat_id)
        self.crypto = crypto
        self.no_access = {}           # 0 -> (time, error) while the chat refuses the bot
        self._lock = threading.RLock()
        self._dir = None              # the directory (pinned), as last read or written

    # ------------------------------------------------------------- the bot's access
    @property
    def upload_slots(self):
        return 1

    def _note(self, e):
        if isinstance(e, TelegramError) and e.refused:
            self.no_access[0] = (time.time(), str(e))
        return e

    def all_refused(self):
        got = self.no_access.get(0)
        return bool(got) and time.time() - got[0] <= NO_ACCESS_PAUSE

    def bot_problems(self):
        got = self.no_access.get(0)
        return [{"bot": 1, "since": got[0], "error": got[1]}] if got else []

    def _call(self, method, params=None, files=None):
        try:
            out = self.api.call(method, params, files) if method != "sendDocument" else \
                self.api.send_document(self.chat_id, files["document"][0], files["document"][1], (params or {}).get("caption"))
        except TelegramError as e:
            raise self._note(e)
        self.no_access.pop(0, None)
        return out

    # ------------------------------------------------------------- pieces
    def upload(self, filename, data, content=""):
        if len(data) > MAX_READ:
            raise TelegramError(413, f"{len(data)} bytes: Telegram gives a bot back files of up to 20 MB")
        msg = self._call("sendDocument", {"caption": None}, files={"document": (filename, data)})
        doc = msg.get("document") or {}
        if doc.get("file_size") is not None and int(doc["file_size"]) != len(data):
            self.delete(_raw(msg["message_id"], doc.get("file_id", "")))
            raise TelegramError(500, f"upload of {filename} arrived with {doc['file_size']} of {len(data)} bytes")
        return StoredChunk(_raw(msg["message_id"], doc["file_id"], msg.get("date")), doc.get("file_unique_id", ""), doc["file_id"])

    def download(self, message_id, url):
        """(data, None): a piece is read by its file id (in its id; `url` is the same)."""
        file_id = url if url and "://" not in str(url) else _parse(message_id)[2]
        try:
            return self.api.download(file_id), None
        except TelegramError as e:
            raise self._note(e)

    def message_info(self, message_id):
        """Is the piece still there? Its message is asked to drop buttons it never had: "not modified" says it is."""
        mid, _, file_id = _parse(message_id)
        try:
            self._call("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": mid})
        except TelegramError as e:
            if "not modified" not in e.description.lower():
                if e.gone:
                    return None
                raise
        return {"filename": "chk_telegram" if self.crypto else "telegram", "url": file_id, "attachment_id": ""}

    def delete(self, message_id):
        mid = _parse(message_id)[0]
        try:
            self._call("deleteMessage", {"chat_id": self.chat_id, "message_id": mid})
        except TelegramError as e:
            if e.gone:
                return
            if e.status == 400 and "can't be deleted" in e.description.lower():
                # (older than 48 hours, and the bot isn't allowed to delete messages: it stays, unread)
                log.warning("Telegram won't let the bot delete a message in %s (make it an admin that may delete "
                            "messages): %s", self.chat_id, e)
                return
            raise

    def time_of(self, mid):
        return float(_parse(mid)[1])

    def messages_after(self, after, limit=100):
        raise TelegramError(0, "a bot can't read a Telegram chat's messages")

    def fetch_attachment(self, msg):
        atts = msg.get("attachments") or []
        file_id = atts[0].get("url") if atts else _parse(msg["id"])[2]
        return self.download(msg["id"], file_id)[0]

    # ------------------------------------------------------------- the directory (pinned)
    def _read_dir(self):
        with self._lock:
            if self._dir is not None:
                return self._dir
            chat = self._call("getChat", {"chat_id": self.chat_id})
            pin = chat.get("pinned_message") or {}
            doc = pin.get("document") or {}
            d = {"index": [], "pinned": []}
            if (pin.get("caption") or "").startswith(DIRECTORY) and doc.get("file_id"):
                try:
                    got = json.loads(self.api.download(doc["file_id"]))
                    d = {"index": list(got.get("index") or []), "pinned": list(got.get("pinned") or [])}
                except (TelegramError, ValueError) as e:
                    log.warning("The directory pinned in Telegram chat %s can't be read: %s", self.chat_id, e)
            d["at"] = pin.get("message_id")
            self._dir = d
            return d

    def _write_dir(self, d):
        """Post the directory, pin it, and delete the one before (pinned messages go with them)."""
        with self._lock:
            body = json.dumps({"v": 1, "index": d["index"], "pinned": d["pinned"]}).encode("utf-8")
            msg = self._call("sendDocument", {"caption": f"{DIRECTORY}\nDDrive: what is kept in this chat. Keep it pinned."},
                             files={"document": ("ddrive-directory.json", body)})
            self._call("pinChatMessage", {"chat_id": self.chat_id, "message_id": msg["message_id"], "disable_notification": "true"})
            old = d.get("at")
            d["at"] = msg["message_id"]
            self._dir = d
            if old:
                try:
                    self._call("deleteMessage", {"chat_id": self.chat_id, "message_id": old})
                except TelegramError:
                    pass

    def _sealed(self, data):
        return self.crypto.encrypt(data) if self.crypto else data

    # ------------------------------------------------------------- index checkpoints (copies)
    def save_index(self, data, previous_message_id=None, cursor=None, tag=None):
        name = f"idx_{secrets.token_hex(8)}.bin" if self.crypto else INDEX_FILENAME
        caption = _index_content(bool(self.crypto), cursor, tag)
        chunk = self.upload(name, self._sealed(data))
        with self._lock:
            d = self._read_dir()
            d["index"].insert(0, {"id": chunk.message_id, "content": caption, "filename": name})
            drop, d["index"] = d["index"][KEEP_INDEX:], d["index"][:KEEP_INDEX]
            self._write_dir(d)
        for e in drop:
            self.delete(e["id"])
        return str(_parse(chunk.message_id)[0])

    def _entries(self, kind, marker=None):
        out = []
        for e in self._read_dir()[kind]:
            if marker and not (e.get("content") or "").startswith(marker):
                continue
            mid, _, file_id = _parse(e["id"])
            out.append({"id": str(mid), "raw": e["id"], "content": e.get("content") or "",
                        "attachments": [{"url": file_id, "filename": e.get("filename") or ""}]})
        return out

    def index_candidates(self, scan_pages=0):
        return self._entries("index")

    def find_latest_index(self, *args):
        found = self.index_candidates()
        return found[0] if found else None

    def load_index_message(self, msg):
        data = self.api.download(msg["attachments"][0]["url"])
        if data.startswith(b"DENC"):
            if not self.crypto:
                raise RuntimeError("The index copy in Telegram is encrypted, but no encryption key is configured.")
            data = self.crypto.decrypt(data)
        return data

    def load_latest_index(self, *args):
        latest = self.find_latest_index()
        return (latest["id"], self.load_index_message(latest)) if latest else None

    def drop_checkpoints(self, mids):
        self.unpin_delete_many(mids)

    # ------------------------------------------------------------- other things found again (the list of accounts)
    def save_pinned(self, filename, data, content):
        chunk = self.upload(filename, data)
        with self._lock:
            d = self._read_dir()
            d["pinned"].insert(0, {"id": chunk.message_id, "content": content, "filename": filename})
            self._write_dir(d)
        return str(_parse(chunk.message_id)[0])

    def pinned(self, marker):
        return self._entries("pinned", marker)

    def unpin_delete(self, mid):
        self.unpin_delete_many([mid])

    def unpin_delete_many(self, mids):
        want = {str(m).rpartition("-")[2].partition(".")[0] for m in mids}
        with self._lock:
            d = self._read_dir()
            gone = [e for k in ("index", "pinned") for e in d[k] if str(_parse(e["id"])[0]) in want]
            if not gone:
                return
            for k in ("index", "pinned"):
                d[k] = [e for e in d[k] if str(_parse(e["id"])[0]) not in want]
            self._write_dir(d)
        for e in gone:
            try:
                self.delete(e["id"])
            except TelegramError as err:
                log.debug("Could not delete %s in Telegram: %s", e["id"], err)

    # ------------------------------------------------------------- erasing everything
    def wipe(self, progress=None):
        """Delete every message the bot can in the chat (the hard reset): its message ids are counted up from 1,
        to the newest the bot knows of (a message it posts now), 100 at a time."""
        probe = self._call("sendMessage", {"chat_id": self.chat_id, "text": "DDrive: erasing", "disable_notification": "true"})
        top = int(probe["message_id"])
        done = 0
        for start in range(1, top + 1, 100):
            ids = list(range(start, min(top, start + 99) + 1))
            try:
                self._call("deleteMessages", {"chat_id": self.chat_id, "message_ids": ids})
            except TelegramError as e:
                if not e.gone:
                    raise
            done += len(ids)
            if progress:
                progress(done)
        self._dir = {"index": [], "pinned": [], "at": None}
        return done


def check_chat(token, chat_id, base=None):
    """Can this bot keep pieces in this chat? (its numeric id, its name, the bot's name, problems) — posts and
    deletes one test file. `chat_id`: the numeric id (-100...) or a public channel's @name."""
    api = TelegramAPI(token, timeout=60, retries=1, base=base)
    problems = []
    me = api.call("getMe")
    chat = api.call("getChat", {"chat_id": chat_id})
    if chat.get("type") not in ("channel", "supergroup", "group", "private"):
        problems.append(f"it is a {chat.get('type')}")
    if chat.get("type") in ("group", "supergroup"):
        problems.append("it is a group: Telegram lets a bot post only 20 messages a minute there (a channel is about 60)")
    try:
        member = api.call("getChatMember", {"chat_id": chat_id, "user_id": me["id"]})
        if chat.get("type") == "channel" and member.get("status") != "administrator":
            problems.append("the bot isn't an admin of the channel")
        elif member.get("status") == "administrator" and not member.get("can_delete_messages"):
            problems.append("the bot may not delete messages (needed to remove pieces older than 48 hours)")
    except TelegramError as e:
        problems.append(f"the bot's rights can't be read ({e.description})")
    chat_id = str(chat.get("id") or chat_id)
    b = TelegramBackend(api, chat_id)
    piece = b.upload("ddrive-check.bin", os.urandom(1024))
    if b.download(piece.message_id, piece.url)[0] is None or b.message_info(piece.message_id) is None:
        problems.append("a test file couldn't be read back")
    b.delete(piece.message_id)
    if b.message_info(piece.message_id) is not None:
        problems.append("the test file couldn't be deleted")
    return chat_id, chat.get("title") or chat.get("username") or chat_id, "@" + (me.get("username") or "?"), problems
