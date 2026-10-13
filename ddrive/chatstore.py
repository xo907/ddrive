"""Where logged and archived Discord messages are kept.

On the drive, in /Discord, as plain text files with one JSON record per line (.jsonl), so they are
encrypted, synced to every device and cost nothing to keep:

    /Discord/Servers/<server> [id]/<channel> [id]/2024-01-03 to 2024-03-09.jsonl    an archived stretch
    /Discord/Direct messages/<name> [id]/...                                         the same for DMs
    /Discord/.../files/<id>-<name>                                                   saved attachments
    /Discord/Live/2026-10/2026-10-10 14.05.33 <device>.jsonl                         what the logger recorded

A record is a message ("kind": "message"), an edit (the text before and after) or a deletion. Files
are written once and never changed; the logger's many small files of a day are joined into one the
day after.

Next to that, each device keeps a small database (chat.db in its data folder) to search and page
through the messages quickly. It holds text only and is only a copy: it is filled from the files on
the drive, and what is in it but not on the drive yet (file_id 0) is written there by the next flush,
within half a minute. Attachments are never kept on the computer: they go through the drive's upload
folder and are gone from it once uploaded. The dashboard shows and opens them from Discord itself
while they are still there; the copy on the drive is the backup for when Discord no longer has them.
Only the attachment itself is kept, as it was sent: no smaller picture of it is made or downloaded.
(Versions before 2.19.0 also kept one of each picture and video, in files/previews/: those are still
shown where they exist, and clear_previews removes them all.)
"""

import contextlib
import json
import logging
import os
import posixpath
import re
import sqlite3
import threading
import time

from .chat import dm_name, parse_ts
from .locks import ALL

log = logging.getLogger("ddrive.chat")

FOLDER = "/Discord"
LIVE = FOLDER + "/Live"
SERVERS = FOLDER + "/Servers"
DMS = FOLDER + "/Direct messages"
PIECE = 1024 * 1024
PREVIEW_MAX = 400 * 1024
PICTURE_MAX = 8 * 1024 * 1024     # a saved picture up to this size is shown small in the list as it is

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS guilds (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, guild_id TEXT, name TEXT, type INTEGER);
CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, sig TEXT, path TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, guild_id TEXT, author_id TEXT, author_name TEXT,
    author_username TEXT, author_avatar TEXT, content TEXT, ts INTEGER, edited_ts INTEGER, type INTEGER,
    attachments TEXT, embeds TEXT, reply_to TEXT, source TEXT, mine INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0,
    saved_ts INTEGER, touched INTEGER, file_id INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS messages_channel ON messages (channel_id, ts);
CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts);
CREATE INDEX IF NOT EXISTS messages_touched ON messages (touched);
CREATE INDEX IF NOT EXISTS messages_file ON messages (file_id);
CREATE TABLE IF NOT EXISTS edits (
    message_id TEXT NOT NULL, channel_id TEXT, ts INTEGER, old_content TEXT, new_content TEXT, file_id INTEGER DEFAULT 0,
    UNIQUE (message_id, ts, new_content));
CREATE TABLE IF NOT EXISTS deletes (message_id TEXT PRIMARY KEY, channel_id TEXT, ts INTEGER, file_id INTEGER DEFAULT 0);
"""

_SELECT = """
SELECT m.*, c.name AS channel_name, g.name AS guild_name,
       (SELECT d.ts FROM deletes d WHERE d.message_id = m.id) AS deleted_ts,
       (SELECT COUNT(*) FROM edits e WHERE e.message_id = m.id) AS edit_count
FROM messages m LEFT JOIN channels c ON c.id = m.channel_id LEFT JOIN guilds g ON g.id = m.guild_id
"""


def preview_url(url):
    """Where Discord serves a small picture of an image or video attachment."""
    url = url.replace("://cdn.discordapp.com/", "://media.discordapp.net/", 1)
    return url + ("&" if "?" in url else "?") + "format=webp&width=400&height=400"


def picture_type(data):
    """The content type of a JPEG, PNG or WebP picture, else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None


def is_media(a):
    return (a.get("content_type") or "").split("/")[0] in ("image", "video")


def now_ms():
    return int(time.time() * 1000)


def safe_name(name, fallback="unnamed"):
    """A server or channel name as a folder name that every system accepts."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*\[\]]', "_", str(name or "")).strip().strip(".")
    return name[:80].rstrip(" .") or fallback


def _day(ms):
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000))


class ChatStore:
    def __init__(self, drive):
        self.d = drive
        self.lock = threading.RLock()        # the database
        self.io = threading.RLock()          # writing files to the drive and reading them back in
        self.db = sqlite3.connect(os.path.join(drive.cfg.resolved_data_dir, "chat.db"), check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)
        self._dirs = {}

    def close(self):
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------ plumbing
    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def _one(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    def _all(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _run(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    @contextlib.contextmanager
    def _everything(self):
        """Reach /Discord whether or not it is password-locked (as folder sync does): the logger runs in
        the background, where nobody can type the password."""
        scope = self.d.fs.scope
        before = getattr(scope, "unlocked", None)
        scope.unlocked = ALL
        try:
            yield
        finally:
            scope.unlocked = before

    def kv_get(self, key, default=None):
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        try:
            return json.loads(row["value"]) if row else default
        except ValueError:
            return default

    def kv_set(self, key, value):
        if value is None:
            self._run("DELETE FROM kv WHERE key=?", (key,))
        else:
            self._run("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value, separators=(",", ":"))))

    # ------------------------------------------------------------ names
    def name_guild(self, gid, name, keep=False):
        if gid and name:
            self._run("INSERT INTO guilds(id, name) VALUES(?, ?) ON CONFLICT(id) DO " +
                      ("NOTHING" if keep else "UPDATE SET name=excluded.name"), (str(gid), str(name)))

    def name_channel(self, cid, guild_id, name, ctype=None, keep=False):
        if cid and name:
            self._run("INSERT INTO channels(id, guild_id, name, type) VALUES(?, ?, ?, ?) ON CONFLICT(id) DO " +
                      ("NOTHING" if keep else "UPDATE SET guild_id=COALESCE(excluded.guild_id, channels.guild_id), "
                                              "name=excluded.name, type=COALESCE(excluded.type, channels.type)"),
                      (str(cid), str(guild_id) if guild_id else None, str(name), ctype))

    def channel(self, cid):
        return self._one("SELECT c.*, g.name AS guild_name FROM channels c LEFT JOIN guilds g ON g.id = c.guild_id "
                         "WHERE c.id=?", (str(cid),))

    def guild_name(self, gid):
        row = self._one("SELECT name FROM guilds WHERE id=?", (str(gid),))
        return row["name"] if row else None

    def _child(self, parent, name, ident):
        """The folder of a server or channel under `parent`. One that exists is found by its id, so a
        renamed channel keeps its folder."""
        key = (parent, str(ident))
        if key not in self._dirs:
            path = f"{parent}/{safe_name(name, str(ident))} [{ident}]"
            node = self.d.index.resolve(parent)
            for c in self.d.index.children(node["id"]) if node else ():
                if c["is_dir"] and c["name"].endswith(f"[{ident}]"):
                    path = f"{parent}/{c['name']}"
                    break
            self._dirs[key] = path
        return self._dirs[key]

    def channel_dir(self, channel_id, guild_id=None):
        ch = self.channel(channel_id)
        guild_id = (ch["guild_id"] if ch else None) or guild_id
        name = ch["name"] if ch else None
        if guild_id:
            parent = self._child(SERVERS, self.guild_name(guild_id), guild_id)
        else:
            parent = DMS
        return self._child(parent, name, channel_id)

    # ------------------------------------------------------------ saving
    def get(self, mid):
        return self._one("SELECT * FROM messages WHERE id=?", (str(mid),))

    def save_message(self, m, source, me_id=None, guild_id=None, flagged=False):
        """Keep a message as Discord sent it. Returns False when exactly this was already kept."""
        prev = self.get(m["id"])
        old = {a.get("id"): a for a in json.loads(prev["attachments"] or "[]")} if prev else {}
        atts = []
        for a in m.get("attachments") or []:
            rec = {"id": a.get("id"), "filename": a.get("filename"), "url": a.get("url"), "size": a.get("size"),
                   "content_type": a.get("content_type")}
            for key in ("path", "preview"):      # where the copies on the drive are
                value = a.get(key) or (old.get(a.get("id")) or {}).get(key)
                if value:
                    rec[key] = value
            atts.append(rec)
        content = m.get("content") or ""
        edited = parse_ts(m.get("edited_timestamp"))
        if prev is not None:
            # Links to attachments change every time Discord hands them out: compare what was attached, not the links.
            same_files = [(a["id"], a.get("path")) for a in atts] == [(a.get("id"), a.get("path")) for a in old.values()]
            if prev["content"] == content and (prev["edited_ts"] or 0) >= (edited or 0) and same_files \
                    and (prev["flagged"] or not flagged):
                return False
        author = m.get("author") or {}
        now = now_ms()
        self._run(
            "INSERT INTO messages (id, channel_id, guild_id, author_id, author_name, author_username, author_avatar, content, ts,"
            " edited_ts, type, attachments, embeds, reply_to, source, mine, flagged, saved_ts, touched, file_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)"
            " ON CONFLICT(id) DO UPDATE SET content=excluded.content, edited_ts=excluded.edited_ts,"
            " attachments=excluded.attachments, embeds=excluded.embeds, author_name=excluded.author_name,"
            " author_avatar=excluded.author_avatar, guild_id=COALESCE(excluded.guild_id, messages.guild_id),"
            " flagged=MAX(messages.flagged, excluded.flagged), saved_ts=excluded.saved_ts, touched=excluded.touched, file_id=0",
            (str(m["id"]), str(m["channel_id"]), m.get("guild_id") or guild_id, author.get("id"),
             author.get("global_name") or author.get("username"), author.get("username"), author.get("avatar"), content,
             parse_ts(m.get("timestamp")) or now, edited, m.get("type") or 0, json.dumps(atts, separators=(",", ":")),
             json.dumps(m.get("embeds") or [], separators=(",", ":")), (m.get("message_reference") or {}).get("message_id"),
             source, 1 if me_id and author.get("id") == me_id else 0, 1 if flagged else 0, now, now))
        return True

    def save_edit(self, mid, ts, old, new):
        """The text of a kept message changed: keep both. Returns False for a message that isn't kept."""
        row = self.get(mid)
        if row is None or row["content"] == new:
            return False
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO edits (message_id, channel_id, ts, old_content, new_content, file_id) "
                       "VALUES (?, ?, ?, ?, ?, 0)", (str(mid), row["channel_id"], ts, old, new))
            db.execute("UPDATE messages SET content=?, edited_ts=?, touched=? WHERE id=?", (new, ts, now_ms(), str(mid)))
        return True

    def save_delete(self, mid, ts=None):
        row = self.get(mid)
        if row is None or self._one("SELECT 1 FROM deletes WHERE message_id=?", (str(mid),)):
            return False
        with self.tx() as db:
            db.execute("INSERT INTO deletes (message_id, channel_id, ts, file_id) VALUES (?, ?, ?, 0)",
                       (str(mid), row["channel_id"], ts or now_ms()))
            db.execute("UPDATE messages SET touched=? WHERE id=?", (now_ms(), str(mid)))
        return True

    def save_attachments(self, api, m, guild_id=None, stop=None):
        """Download a message's attachments onto the drive (next to its channel's messages) and note
        where each one went on the message (`path`). Returns how many were saved."""
        saved = 0
        for a in m.get("attachments") or []:
            if a.get("path") or not a.get("url"):
                continue
            if stop is not None and stop.is_set():
                raise InterruptedError("stopped")
            path = f"{self.channel_dir(m['channel_id'], m.get('guild_id') or guild_id)}/files/" \
                   f"{a.get('id')}-{safe_name(a.get('filename'), 'file')}"
            try:
                with self.io, self._everything():
                    if self.d.index.resolve(path) is None:
                        self._makedirs(posixpath.dirname(path))
                        with contextlib.closing(api.download(a["url"])) as src:
                            self.d.fs.put_file(path, src, stop=stop.is_set if stop is not None else None)
                        saved += 1
                a["path"] = path
            except InterruptedError:
                raise
            except Exception as e:
                log.debug("attachment %s of message %s: %s", a.get("filename"), m.get("id"), e)
        return saved

    def clear_previews(self):
        """Remove, for good, every small picture of an attachment that versions before 2.19.0 kept next
        to the attachment itself, in every conversation anywhere on the drive: in /Discord, and in a
        copy of it somewhere else (an account that was moved into this one has its own under "From
        account ..."). The attachments stay. Returns (how many files, how many bytes).

        Only what is unmistakably one of those is touched: a file <attachment id>.webp in
        <conversation> [<its id>]/files/previews/. Anything else that happens to be in a folder
        called previews is left alone."""
        idx, fs = self.d.index, self.d.fs
        purge = getattr(idx, "purge_node", None)         # (an end-to-end account's unlink is for good already)
        count = size = 0
        sync_now = getattr(self.d, "sync_now", None)     # (an end-to-end account: what its other devices wrote too)
        if sync_now is not None:
            sync_now()
        with self.io, self._everything():
            root = idx.resolve("/")
            todo = [(root, "")] if root is not None and root["is_dir"] else []
            while todo:
                node, path = todo.pop()
                for c in idx.children(node["id"]):
                    cpath = f"{path}/{c['name']}"
                    if not c["is_dir"]:
                        continue
                    if not (c["name"] == "previews" and posixpath.basename(path) == "files"
                            and re.search(r" \[\d+\]$", posixpath.basename(posixpath.dirname(path)))):
                        todo.append((c, cpath))
                        continue
                    for p in idx.children(c["id"]):
                        if p["is_dir"] or not re.fullmatch(r"\d+\.webp", p["name"]):
                            continue
                        try:
                            if purge is not None:
                                purge(p["id"])
                            else:
                                fs.unlink(f"{cpath}/{p['name']}")
                            count += 1
                            size += p["size"] or 0
                        except Exception as e:
                            log.debug("preview %s/%s could not be removed: %s", cpath, p["name"], e)
                    try:
                        fs.rmdir(cpath)                  # (left where a drive has no such thing, or it isn't empty)
                    except Exception:
                        pass
        if count:
            log.info("Removed %d saved preview(s) of Discord attachments (%d bytes).", count, size)
        return count, size

    # ------------------------------------------------------------ onto the drive
    def _makedirs(self, path):
        if self.d.index.resolve(path) is None:
            self.d.index.makedirs(path)

    def _write(self, path, data):
        fs = self.d.fs
        self._makedirs(posixpath.dirname(path))
        fh = fs.create(path, 0o644)
        try:
            for off in range(0, len(data), PIECE):
                fs.write(path, data[off:off + PIECE], off, fh)
        finally:
            fs.release(path, fh)
        return self.d.index.resolve(path)

    def _free(self, path):
        if self.d.index.resolve(path) is None:
            return path
        stem, ext = posixpath.splitext(path)
        n = 2
        while self.d.index.resolve(f"{stem} ({n}){ext}") is not None:
            n += 1
        return f"{stem} ({n}){ext}"

    @staticmethod
    def _sig(node):
        return f"{node['size']}:{int(node['mtime'] * 1000)}"

    def _register(self, node, path):
        with self.lock:
            self.db.execute("INSERT INTO files(uid, sig, path) VALUES(?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                            "sig=excluded.sig, path=excluded.path", (node["uid"], self._sig(node), path))
            return self.db.execute("SELECT id FROM files WHERE uid=?", (node["uid"],)).fetchone()["id"]

    def _record(self, row):
        ch = self.channel(row["channel_id"])
        rec = {"kind": "message", "id": row["id"], "channel_id": row["channel_id"], "guild_id": row["guild_id"],
               "guild": self.guild_name(row["guild_id"]) if row["guild_id"] else None, "channel": ch["name"] if ch else None,
               "author": {"id": row["author_id"], "name": row["author_name"], "username": row["author_username"],
                          "avatar": row["author_avatar"]},
               "content": row["content"], "ts": row["ts"], "edited_ts": row["edited_ts"], "type": row["type"],
               "attachments": json.loads(row["attachments"] or "[]"), "embeds": json.loads(row["embeds"] or "[]"),
               "reply_to": row["reply_to"], "source": row["source"], "mine": row["mine"], "flagged": row["flagged"],
               "saved_ts": row["saved_ts"]}
        return rec

    @staticmethod
    def _lines(records):
        return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records).encode("utf-8")

    def pending(self):
        """How many messages, edits and deletions are not on the drive yet."""
        return sum(self._one(f"SELECT COUNT(*) AS n FROM {t} WHERE file_id=0")["n"] for t in ("messages", "edits", "deletes"))

    def flush_live(self):
        """Write what the logger recorded since the last time as one new file. Returns the number of records."""
        with self.io, self._everything():
            msgs = self._all("SELECT * FROM messages WHERE file_id=0 AND source='live' ORDER BY ts, id")
            edits = self._all("SELECT rowid, * FROM edits WHERE file_id=0 ORDER BY ts")
            dels = self._all("SELECT * FROM deletes WHERE file_id=0 ORDER BY ts")
            if not (msgs or edits or dels):
                return 0
            records = [self._record(r) for r in msgs]
            records += [{"kind": "edit", "id": e["message_id"], "channel_id": e["channel_id"], "ts": e["ts"],
                         "old": e["old_content"], "new": e["new_content"]} for e in edits]
            records += [{"kind": "delete", "id": d["message_id"], "channel_id": d["channel_id"], "ts": d["ts"]} for d in dels]
            stamp = time.localtime()
            path = self._free(f"{LIVE}/{time.strftime('%Y-%m', stamp)}/{time.strftime('%Y-%m-%d %H.%M.%S', stamp)} "
                              f"{self.d.cfg.device_id}.jsonl")
            fid = self._register(self._write(path, self._lines(records)), path)
            with self.tx() as db:
                # A message that changed again meanwhile stays waiting for the next file.
                db.executemany("UPDATE messages SET file_id=? WHERE id=? AND touched=?", [(fid, r["id"], r["touched"]) for r in msgs])
                db.executemany("UPDATE edits SET file_id=? WHERE rowid=?", [(fid, e["rowid"]) for e in edits])
                db.executemany("UPDATE deletes SET file_id=? WHERE message_id=?", [(fid, d["message_id"]) for d in dels])
            return len(records)

    def flush_archive(self, channel_id=None):
        """Write the archived messages that aren't on the drive yet, one file per channel."""
        total = 0
        channels = [channel_id] if channel_id else [r["channel_id"] for r in self._all(
            "SELECT DISTINCT channel_id FROM messages WHERE file_id=0 AND source='archive'")]
        for cid in channels:
            with self.io, self._everything():
                msgs = self._all("SELECT * FROM messages WHERE file_id=0 AND source='archive' AND channel_id=? ORDER BY ts, id",
                                 (str(cid),))
                if not msgs:
                    continue
                first, last = _day(msgs[0]["ts"]), _day(msgs[-1]["ts"])
                name = first if first == last else f"{first} to {last}"
                path = self._free(f"{self.channel_dir(cid, msgs[0]['guild_id'])}/{name}.jsonl")
                fid = self._register(self._write(path, self._lines(self._record(r) for r in msgs)), path)
                with self.tx() as db:
                    db.executemany("UPDATE messages SET file_id=? WHERE id=? AND touched=?",
                                   [(fid, r["id"], r["touched"]) for r in msgs])
                total += len(msgs)
        return total

    def compact_live(self):
        """Join the logger's small files of each earlier day into one file per day. The small files are
        removed on a later call, once the joined one is safely on Discord."""
        me = self.d.cfg.device_id
        today = time.strftime("%Y-%m-%d")
        part = re.compile(r"^(\d{4}-\d\d-\d\d) \d\d\.\d\d\.\d\d(?: \(\d+\))? " + re.escape(me) + r"(?: \(\d+\))?\.jsonl$")
        idx, fs = self.d.index, self.d.fs
        with self.io, self._everything():
            root = idx.resolve(LIVE)
            for month in idx.children(root["id"]) if root else ():
                if not month["is_dir"]:
                    continue
                folder = f"{LIVE}/{month['name']}"
                days = {}
                for c in idx.children(month["id"]):
                    m = part.match(c["name"])
                    if m and not c["is_dir"] and m.group(1) < today:
                        days.setdefault(m.group(1), []).append(c)
                for day, parts in days.items():
                    joined = f"{folder}/{day} {me}.jsonl"
                    node = idx.resolve(joined)
                    if node is None:
                        if len(parts) < 2:
                            continue
                        data = b"".join(self._part(p) for p in sorted(parts, key=lambda p: p["name"]))
                        self._ingest(self._write(joined, data), joined)
                    elif node["state"] == "synced":
                        for p in parts:
                            fs.unlink(f"{folder}/{p['name']}")
                            self._forget(p["uid"])

    def _part(self, node):
        """What one of the logger's small files holds. For a file this device wrote, that is taken from
        the database, so that joining a day's files doesn't download each of them again."""
        row = self._one("SELECT id FROM files WHERE uid=?", (node["uid"],))
        if row is None:
            return self.d.fs.read_file(node["id"], 0, node["size"]) if node["size"] else b""
        fid = row["id"]
        records = [self._record(r) for r in self._all("SELECT * FROM messages WHERE file_id=? ORDER BY ts, id", (fid,))]
        records += [{"kind": "edit", "id": e["message_id"], "channel_id": e["channel_id"], "ts": e["ts"], "old": e["old_content"],
                     "new": e["new_content"]} for e in self._all("SELECT * FROM edits WHERE file_id=? ORDER BY ts", (fid,))]
        records += [{"kind": "delete", "id": d["message_id"], "channel_id": d["channel_id"], "ts": d["ts"]}
                    for d in self._all("SELECT * FROM deletes WHERE file_id=? ORDER BY ts", (fid,))]
        return self._lines(records)

    # ------------------------------------------------------------ from the drive
    def _forget(self, uid):
        row = self._one("SELECT id FROM files WHERE uid=?", (uid,))
        if row is None:
            return
        with self.tx() as db:
            for table in ("messages", "edits", "deletes"):
                db.execute(f"DELETE FROM {table} WHERE file_id=?", (row["id"],))
            db.execute("DELETE FROM files WHERE id=?", (row["id"],))

    def _ingest(self, node, path):
        data = self.d.fs.read_file(node["id"], 0, node["size"]) if node["size"] else b""
        fid = self._register(node, path)
        n = 0
        with self.tx() as db:
            for line in data.split(b"\n"):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    kind, mid = r.get("kind"), str(r["id"])
                except (ValueError, KeyError, AttributeError, TypeError):
                    continue
                n += 1
                if kind == "edit":
                    db.execute("INSERT INTO edits (message_id, channel_id, ts, old_content, new_content, file_id) VALUES (?, ?, ?, ?, ?, ?)"
                               " ON CONFLICT(message_id, ts, new_content) DO UPDATE SET file_id=excluded.file_id WHERE edits.file_id != 0",
                               (mid, r.get("channel_id"), r.get("ts"), r.get("old"), r.get("new"), fid))
                    db.execute("UPDATE messages SET content=?, edited_ts=? WHERE id=? AND COALESCE(edited_ts, 0) < ?",
                               (r.get("new"), r.get("ts"), mid, r.get("ts") or 0))
                elif kind == "delete":
                    db.execute("INSERT INTO deletes (message_id, channel_id, ts, file_id) VALUES (?, ?, ?, ?) ON CONFLICT(message_id)"
                               " DO UPDATE SET file_id=excluded.file_id WHERE deletes.file_id != 0",
                               (mid, r.get("channel_id"), r.get("ts"), fid))
                elif kind == "message":
                    self._ingest_message(db, r, mid, fid)
        return n

    def _ingest_message(self, db, r, mid, fid):
        if r.get("guild_id") and r.get("guild"):
            db.execute("INSERT OR IGNORE INTO guilds(id, name) VALUES(?, ?)", (r["guild_id"], r["guild"]))
        if r.get("channel"):
            db.execute("INSERT OR IGNORE INTO channels(id, guild_id, name) VALUES(?, ?, ?)",
                       (r.get("channel_id"), r.get("guild_id"), r["channel"]))
        row = db.execute("SELECT saved_ts, touched, file_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row is not None and (row["file_id"] == 0 or (r.get("saved_ts") or 0) < (row["saved_ts"] or 0)):
            return                           # what this device has is newer (or still waiting to be written)
        a = r.get("author") or {}
        db.execute(
            "INSERT OR REPLACE INTO messages (id, channel_id, guild_id, author_id, author_name, author_username, author_avatar,"
            " content, ts, edited_ts, type, attachments, embeds, reply_to, source, mine, flagged, saved_ts, touched, file_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, r.get("channel_id"), r.get("guild_id"), a.get("id"), a.get("name"), a.get("username"), a.get("avatar"),
             r.get("content") or "", r.get("ts"), r.get("edited_ts"), r.get("type") or 0,
             json.dumps(r.get("attachments") or [], separators=(",", ":")), json.dumps(r.get("embeds") or [], separators=(",", ":")),
             r.get("reply_to"), r.get("source"), 1 if r.get("mine") else 0, 1 if r.get("flagged") else 0, r.get("saved_ts"),
             max(r.get("saved_ts") or 0, (row["touched"] or 0) if row is not None else 0), fid))
        # An edit that was read before its message (from another file) still applies.
        e = db.execute("SELECT ts, new_content FROM edits WHERE message_id=? ORDER BY ts DESC LIMIT 1", (mid,)).fetchone()
        if e is not None and (e["ts"] or 0) > (r.get("edited_ts") or 0):
            db.execute("UPDATE messages SET content=?, edited_ts=? WHERE id=?", (e["new_content"], e["ts"], mid))

    def _walk(self):
        """{uid: (node, path)} of every .jsonl file under /Discord."""
        idx = self.d.index
        out = {}
        root = idx.resolve(FOLDER)
        todo = [(root, FOLDER)] if root is not None and root["is_dir"] else []
        while todo:
            node, path = todo.pop()
            for c in idx.children(node["id"]):
                cpath = f"{path}/{c['name']}"
                if c["is_dir"]:
                    if c["name"] != "files":
                        todo.append((c, cpath))
                elif c["name"].endswith(".jsonl"):
                    out[c["uid"]] = (c, cpath)
        return out

    def scan(self, stop=None):
        """Bring the database in line with the files on the drive: read new ones (from other devices, or
        after a reinstall) and drop what was only in files that are gone. Returns (files read, files gone)."""
        read = gone = 0
        with self.io, self._everything():
            self._dirs.clear()
            found = self._walk()
            known = {r["uid"]: r["sig"] for r in self._all("SELECT uid, sig FROM files")}
            # New files first: when a day's small files were joined on another device, the joined file takes
            # their messages over before the small ones are dropped.
            for uid, (node, path) in sorted(found.items(), key=lambda kv: kv[1][1]):
                if stop is not None and stop.is_set():
                    return read, gone
                if known.get(uid) != self._sig(node):
                    try:
                        self._ingest(node, path)
                        read += 1
                    except Exception as e:
                        log.warning("Could not read %s: %s", path, e)
            for uid in set(known) - set(found):
                self._forget(uid)
                gone += 1
        return read, gone

    def rebuild(self, stop=None):
        """Forget everything that was read from the drive and read it again."""
        with self.io:
            with self.tx() as db:
                for table in ("messages", "edits", "deletes"):
                    db.execute(f"DELETE FROM {table} WHERE file_id != 0")
                db.execute("DELETE FROM files")
            return self.scan(stop)

    # ------------------------------------------------------------ looking things up
    @staticmethod
    def _where(q):
        where, args = [], []

        def add(sql, *a):
            where.append(sql)
            args.extend(a)
        if q.get("q"):
            add("m.content LIKE ? ESCAPE '\\'", "%" + re.sub(r"([\\%_])", r"\\\1", str(q["q"])) + "%")
        if q.get("channel"):
            add("m.channel_id = ?", q["channel"])
        if q.get("guild"):
            add("m.guild_id = ?", q["guild"])
        if q.get("dm") == "1":
            add("m.guild_id IS NULL")
        if q.get("author"):
            add("(m.author_id = ? OR m.author_username = ? OR m.author_name = ?)", q["author"], q["author"], q["author"])
        if q.get("mine") in ("0", "1"):
            add("m.mine = ?", int(q["mine"]))
        if q.get("deleted") == "1":
            add("EXISTS (SELECT 1 FROM deletes d WHERE d.message_id = m.id)")
        if q.get("edited") == "1":
            add("EXISTS (SELECT 1 FROM edits e WHERE e.message_id = m.id)")
        if q.get("flagged") == "1":
            add("m.flagged = 1")
        if q.get("files") == "1":
            add("m.attachments != '[]'")
        if q.get("source"):
            add("m.source = ?", q["source"])
        for key, op in (("after", ">="), ("before", "<=")):
            if q.get(key):
                try:
                    add(f"m.ts {op} ?", int(float(q[key])))
                except ValueError:
                    pass
        return ("WHERE " + " AND ".join(where)) if where else "", args

    def _present(self, row):
        m = dict(row)
        m.pop("file_id", None)
        m["on_drive"] = row["file_id"] != 0
        m["attachments"] = json.loads(row["attachments"] or "[]")
        m["embeds"] = json.loads(row["embeds"] or "[]")
        m["edits"] = [dict(e) for e in self._all("SELECT ts, old_content, new_content FROM edits WHERE message_id=? ORDER BY ts",
                                                 (row["id"],))] if row["edit_count"] else []
        return m

    def messages(self, q):
        """{total, messages}: newest first (`order=touched`: what was added, edited or deleted last first)."""
        where, args = self._where(q)
        try:
            limit = max(1, min(int(q.get("limit") or 60), 500))
            offset = max(0, int(q.get("offset") or 0))
        except ValueError:
            limit, offset = 60, 0
        order = "m.touched DESC, m.id DESC" if q.get("order") == "touched" else "m.ts DESC, m.id DESC"
        rows = self._all(f"{_SELECT} {where} ORDER BY {order} LIMIT ? OFFSET ?", args + [limit, offset])
        total = self._one(f"SELECT COUNT(*) AS n FROM messages m {where}", args)["n"]
        return {"total": total, "messages": [self._present(r) for r in rows]}

    def places(self):
        """Every conversation that has kept messages, the most recently active first."""
        return [dict(r) for r in self._all(
            "SELECT m.channel_id AS id, m.guild_id, c.name, g.name AS guild_name, COUNT(*) AS count, MAX(m.ts) AS latest "
            "FROM messages m LEFT JOIN channels c ON c.id = m.channel_id LEFT JOIN guilds g ON g.id = m.guild_id "
            "GROUP BY m.channel_id ORDER BY latest DESC")]

    def totals(self):
        row = self._one("SELECT COUNT(*) AS messages, COUNT(DISTINCT channel_id) AS channels, MIN(ts) AS first, MAX(ts) AS last "
                        "FROM messages")
        out = dict(row)
        out["deleted"] = self._one("SELECT COUNT(*) AS n FROM deletes")["n"]
        out["edits"] = self._one("SELECT COUNT(*) AS n FROM edits")["n"]
        out["waiting"] = self.pending()
        return out

    def export(self, q, fmt):
        """The matching messages, oldest first, as pieces of a file in `fmt` (json, csv, txt or html)."""
        head, line, tail = _FORMATS[fmt]
        where, args = self._where(q)
        yield head.encode("utf-8")
        last, n = (-1, ""), 0
        while True:                          # in pages, so the database isn't held for the whole download
            rows = self._all(f"{_SELECT} {'WHERE' if not where else where + ' AND'} (m.ts > ? OR (m.ts = ? AND m.id > ?)) "
                             "ORDER BY m.ts, m.id LIMIT 500", args + [last[0], last[0], last[1]])
            if not rows:
                break
            out = []
            for r in rows:
                out.append(line(self._present(r), n))
                n += 1
            last = (rows[-1]["ts"] or 0, rows[-1]["id"])
            yield "".join(out).encode("utf-8")
        yield tail.encode("utf-8")


def place_name(m):
    """Where a message was written, in words: 'Server #channel' or the name of the conversation."""
    name = m.get("channel_name") or m.get("channel_id") or ""
    return f"{m['guild_name']} #{name}" if m.get("guild_name") else name


def _iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((ms or 0) / 1000)) if ms else ""


def _html(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _csv(*cells):
    return ",".join('"' + str(c if c is not None else "").replace('"', '""') + '"' for c in cells) + "\n"


_HTML_HEAD = """<!doctype html><meta charset="utf-8"><title>Discord messages</title><style>
body{background:#0f0f10;color:#ececec;font:14px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 16px}
.m{padding:10px 14px;border-radius:8px;margin:6px 0;background:#151516;border:1px solid #232325}
.h{font-size:12px;color:#9b9b9b}.h b{color:#ececec;font-size:14px}.c{white-space:pre-wrap;word-break:break-word}
.d{border-color:#ff636966}.tag{color:#ff6369;font-size:11px;margin-left:6px}.o{color:#9b9b9b;text-decoration:line-through}a{color:#ececec}
</style><h1>Discord messages</h1>
"""

_FORMATS = {
    "json": ("[\n", lambda m, i: (",\n" if i else "") + json.dumps(m, ensure_ascii=False, separators=(",", ":")), "\n]\n"),
    "csv": ("﻿id,timestamp,where,author,author_id,content,attachments,edited,deleted\n",
            lambda m, i: _csv(m["id"], _iso(m["ts"]), place_name(m), m["author_name"], m["author_id"], m["content"],
                              " ".join(a.get("path") or a.get("url") or "" for a in m["attachments"]), _iso(m["edited_ts"]),
                              _iso(m["deleted_ts"])), ""),
    "txt": ("", lambda m, i: f"[{_iso(m['ts'])}] ({place_name(m)}) {m['author_name']}: {m['content']}"
            + "".join(f"\n    attachment: {a.get('path') or a.get('url')}" for a in m["attachments"])
            + "".join(f"\n    before an edit: {e['old_content']}" for e in m["edits"])
            + ("\n    (deleted)" if m["deleted_ts"] else "") + "\n", ""),
    "html": (_HTML_HEAD, lambda m, i: f'<div class="m{" d" if m["deleted_ts"] else ""}"><div class="h"><b>{_html(m["author_name"])}</b>'
             f' · {_html(place_name(m))} · {_iso(m["ts"])}' + ('<span class="tag">deleted</span>' if m["deleted_ts"] else "")
             + ('<span class="tag">edited</span>' if m["edits"] else "") + f'</div><div class="c">{_html(m["content"])}</div>'
             + "".join(f'<div class="c o">{_html(e["old_content"])}</div>' for e in m["edits"])
             + "".join(f'<div>{_html(a.get("filename"))}{" · " + _html(a["path"]) if a.get("path") else ""}</div>' for a in m["attachments"])
             + "</div>\n", ""),
}
EXPORT_TYPES = {"json": "application/json", "csv": "text/csv", "txt": "text/plain", "html": "text/html"}

__all__ = ["ChatStore", "FOLDER", "EXPORT_TYPES", "place_name", "dm_name"]
