"""The Discord message logger and archiver, kept on the drive.

Log in with your Discord account (dashboard: Settings) and the drive can
  - archive: read conversations back through their history and keep them, and
  - log: record messages as they arrive, and keep the original text of ones later edited or deleted.

Everything is stored as files in /Discord on the drive (see chatstore.py), so it is encrypted, synced
to your other devices and costs nothing. The account token stays on this computer, with the other
secrets in the config file; logging and archiving run on the device you logged in on.
"""

import contextlib
import json
import logging
import os
import random
import secrets
import socket
import threading
import time
import urllib.parse

from . import chatstore
from .chat import TEXT_CHANNELS, AccountAPI, AccountError, Gateway, clean_token, dm_name, parse_ts, snowflake_from_ms
from .chatstore import FOLDER, ChatStore, now_ms
from .config import Config, config_path
from .webplus import ApiErrorProxy

log = logging.getLogger("discorddrive.chat")

FLUSH_SECONDS = 30.0         # what was recorded is written to the drive at most this long after it arrived
SCAN_SECONDS = 60.0          # how often files from other devices are looked for
TICK = 5.0
SEGMENT = 5000               # archived messages per file
DEFAULT_RULES = {
    "scope": "all",          # all | custom
    "allDms": True,
    "selection": {"dms": {}, "guilds": {}},
    "captureMine": True,
    "captureOthers": True,
    "keywords": [],
    "downloadAttachments": False,
    "attachmentsOnly": False,    # only messages with files, saved without their text
}


def without_text(store, m):
    """A message as it is kept in "attachments only" mode: its files, not what was written. Text that
    was kept earlier (before the mode was switched on) is left as it is."""
    kept = store.get(m["id"])
    return dict(m, content=kept["content"] if kept is not None else "", embeds=[])


def clean_rules(raw):
    raw = raw if isinstance(raw, dict) else {}
    rules = dict(DEFAULT_RULES)
    rules["scope"] = "custom" if raw.get("scope") == "custom" else "all"
    for key in ("allDms", "captureMine", "captureOthers", "downloadAttachments", "attachmentsOnly"):
        if key in raw:
            rules[key] = bool(raw[key])
    sel = raw.get("selection") if isinstance(raw.get("selection"), dict) else {}
    dms = {str(k): True for k, v in (sel.get("dms") or {}).items() if v} if isinstance(sel.get("dms"), dict) else {}
    guilds = {}
    for gid, v in (sel.get("guilds") or {}).items() if isinstance(sel.get("guilds"), dict) else ():
        if v is True:
            guilds[str(gid)] = True
        elif isinstance(v, dict) and any(v.values()):
            guilds[str(gid)] = {str(c): True for c, on in v.items() if on}
    rules["selection"] = {"dms": dms, "guilds": guilds}
    words = raw.get("keywords") or []
    words = words.split(",") if isinstance(words, str) else words
    rules["keywords"] = [w for w in (str(k).strip().lower()[:80] for k in words) if w][:50]
    return rules


class Logger:
    """Records messages as they arrive (a port of Pretty Suite's Live Logger)."""

    def __init__(self, chat):
        self.chat = chat
        self.status = "stopped"
        self.error = None
        self.captured = 0
        self.gateway = None
        self._rules = None
        self._looked_up = set()

    @property
    def rules(self):
        if self._rules is None:
            self._rules = clean_rules(self.chat.store.kv_get("logger.rules", {}))
        return self._rules

    def set_rules(self, raw):
        self._rules = clean_rules(raw)
        self.chat.store.kv_set("logger.rules", self._rules)
        return self._rules

    def state(self):
        return {"status": self.status, "error": self.error, "captured": self.captured, "rules": self.rules}

    def start(self):
        chat = self.chat
        chat.require()
        if self.gateway is not None:
            return
        self.error = None
        self.gateway = chat.make_gateway(chat.api.token, self._dispatch, self._on_status, self._on_fatal)
        self.gateway.start()
        chat.store.kv_set("logger.enabled", True)
        log.info("Discord message logger: started.")

    def stop(self, remember=False):
        gateway, self.gateway = self.gateway, None
        if gateway is not None:
            gateway.close()
            log.info("Discord message logger: stopped.")
        self.status = "stopped"
        if self.chat.in_use():
            if not remember:
                self.chat.store.kv_set("logger.enabled", None)
            try:
                self.chat.flush()
            except Exception as e:
                log.warning("Discord message logger: could not write to the drive: %s", e)

    def _on_status(self, status):
        self.status = status if self.gateway is not None else "stopped"

    def _on_fatal(self, message):
        self.error = message
        self.gateway = None
        self.status = "stopped"
        self.chat.store.kv_set("logger.enabled", None)
        log.warning("Discord message logger: %s", message)

    # ------------------------------------------------------------ what arrives
    def _matches(self, m):
        rules = self.rules
        mine = (m.get("author") or {}).get("id") == self.chat.me["id"]
        if not (rules["captureMine"] if mine else rules["captureOthers"]):
            return False
        if rules["scope"] == "all":
            return True
        sel = rules["selection"]
        if not m.get("guild_id"):
            return rules["allDms"] or bool(sel["dms"].get(m["channel_id"]))
        guild = sel["guilds"].get(m["guild_id"])
        return guild is True or bool(guild and guild.get(m["channel_id"]))

    def _flagged(self, content):
        text = (content or "").lower()
        return any(k in text for k in self.rules["keywords"])

    def _directory(self, ready):
        """The first thing Discord sends lists every server and conversation: learn their names."""
        store = self.chat.store
        users = {u["id"]: u for u in ready.get("users") or []}
        for g in ready.get("guilds") or []:
            store.name_guild(g.get("id"), (g.get("properties") or {}).get("name") or g.get("name"))
            for c in (g.get("channels") or []) + (g.get("threads") or []):
                store.name_channel(c.get("id"), g.get("id"), c.get("name"), c.get("type"))
        for c in ready.get("private_channels") or []:
            people = c.get("recipients") or [users[i] for i in c.get("recipient_ids") or [] if i in users]
            store.name_channel(c.get("id"), None, dm_name(dict(c, recipients=people)), c.get("type"))

    def _create(self, m):
        if not m.get("author") or not m.get("id") or not self._matches(m):
            return
        chat = self.chat
        media_only = self.rules["attachmentsOnly"]
        if media_only:
            if not m.get("attachments"):
                return
            m = without_text(chat.store, m)
        if (media_only or self.rules["downloadAttachments"]) and m.get("attachments"):
            chat.store.save_attachments(chat.api, m)
        gid = m.get("guild_id")
        if gid and chat.store.channel(m["channel_id"]) is None and gid not in self._looked_up:
            self._looked_up.add(gid)                 # a channel never seen before: learn the server's channel names once
            try:
                chat.guild_channels(gid)
            except AccountError:
                pass
        if chat.store.save_message(m, "live", chat.me["id"], flagged=self._flagged(m.get("content"))):
            self.captured += 1

    def _update(self, m):
        if m.get("content") is None:                 # only a link preview changed
            return
        store = self.chat.store
        stored = store.get(m["id"])
        if stored is None:
            return self._create(m)
        if self.rules["attachmentsOnly"]:            # the text isn't being kept, so neither are changes to it
            return
        store.save_edit(m["id"], parse_ts(m.get("edited_timestamp")) or now_ms(), stored["content"], m["content"])

    def _dispatch(self, kind, data):
        if kind == "READY":
            self._directory(data)
        elif kind == "MESSAGE_CREATE":
            self._create(data)
        elif kind == "MESSAGE_UPDATE":
            self._update(data)
        elif kind == "MESSAGE_DELETE":
            self.chat.store.save_delete(data["id"])
        elif kind == "MESSAGE_DELETE_BULK":
            for mid in data.get("ids") or []:
                self.chat.store.save_delete(mid)
        elif kind in ("CHANNEL_CREATE", "THREAD_CREATE"):
            self.chat.store.name_channel(data.get("id"), data.get("guild_id"), data.get("name") or dm_name(data), data.get("type"))


class Archiver:
    """Reads conversations back through their history (a port of Pretty Suite's Archive). One job at a
    time; it saves its place after every page, so it can be stopped and carried on, also after a restart."""

    def __init__(self, chat):
        self.chat = chat
        self._job = None
        self._loaded = False
        self._thread = None
        self._stop = threading.Event()
        self._closing = False             # the drive is stopping: the job carries on by itself at the next start
        self._lock = threading.Lock()

    @property
    def job(self):
        if not self._loaded:
            self._loaded = True
            self._job = self.chat.store.kv_get("archive.job")
        return self._job

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def state(self):
        job = self.job
        if job is None:
            return None
        out = {k: job.get(k) for k in ("id", "status", "stats", "error", "started", "finished")}
        out["log"] = job.get("log", [])[-60:]
        out["running"] = self.running()
        return out

    def _save(self):
        self.chat.store.kv_set("archive.job", self._job)

    def _log(self, level, text):
        self._job.setdefault("log", []).append({"t": time.time(), "level": level, "text": text})
        del self._job["log"][:-200]
        log.log(logging.WARNING if level == "warn" else logging.INFO, "Discord archive: %s", text)

    def start(self, config):
        self.chat.require()
        config = config if isinstance(config, dict) else {}
        targets = []
        for t in config.get("targets") or []:
            if isinstance(t, dict) and t.get("id") and t.get("kind") in ("dm", "guild"):
                ids = [str(c) for c in t["channelIds"]] if isinstance(t.get("channelIds"), list) else None
                targets.append({"kind": t["kind"], "id": str(t["id"]), "channelIds": ids})
        if not targets:
            raise ApiErrorProxy(400, "Pick at least one server or conversation.")

        def when(value):
            try:
                return int(float(value)) if value not in (None, "") else None
            except (TypeError, ValueError):
                raise ApiErrorProxy(400, "That date isn't valid.") from None
        people = config.get("userIds") or ""
        people = people if isinstance(people, list) else str(people).replace(",", " ").split()
        with self._lock:
            if self.running():
                raise ApiErrorProxy(409, "An archive is already running.")
            self._loaded = True
            self._job = {
                "id": secrets.token_hex(4), "status": "running", "started": time.time(), "finished": None, "error": None,
                "config": {"targets": targets, "scope": config.get("scope") if config.get("scope") in ("mine", "others") else "both",
                           "userIds": [str(p) for p in people][:200], "after": when(config.get("after")),
                           "before": when(config.get("before")), "downloadAttachments": bool(config.get("downloadAttachments")),
                           "attachmentsOnly": bool(config.get("attachmentsOnly"))},
                "channels": None, "index": 0, "cursor": None, "log": [],
                "stats": {"channelsTotal": 0, "channelsDone": 0, "scanned": 0, "saved": 0, "files": 0, "current": None},
            }
            self._go()

    def resume(self):
        self.chat.require()
        with self._lock:
            if self.running():
                return
            if self.job is None or self.job["status"] not in ("stopped", "error", "running"):
                raise ApiErrorProxy(409, "There is no archive to carry on with.")
            self._job.update(status="running", error=None)
            self._go()

    def _go(self):
        self._save()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="DiscordArchive", daemon=True)
        self._thread.start()

    def stop(self, wait=10.0, closing=False):
        self._closing = closing
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)

    def clear(self):
        with self._lock:
            if self.running():
                raise ApiErrorProxy(409, "Stop the archive first.")
            self._job = None
            self._loaded = True
            self.chat.store.kv_set("archive.job", None)

    # ------------------------------------------------------------ the work
    def _expand(self, job):
        """Whole servers become their channels."""
        chat, store = self.chat, self.chat.store
        channels = []
        for target in job["config"]["targets"]:
            if self._stop.is_set():
                raise InterruptedError("stopped")
            if target["kind"] == "dm":
                ch = store.channel(target["id"])
                channels.append({"id": target["id"], "guildId": None, "name": ch["name"] if ch else target["id"]})
                continue
            guild = store.guild_name(target["id"]) or target["id"]
            try:
                for c in chat.guild_channels(target["id"]):
                    if target["channelIds"] is None or c["id"] in target["channelIds"]:
                        channels.append({"id": c["id"], "guildId": target["id"], "name": f"{guild} › #{c['name']}"})
            except AccountError as e:
                self._log("warn", f"{guild}: could not list the channels ({e}).")
        return channels

    def _run(self):
        job = self._job
        try:
            self._work(job)
            job.update(status="done", finished=time.time())
        except InterruptedError:
            if not self._closing:
                job["status"] = "stopped"
                self._log("info", "Stopped. It carries on from here when you resume.")
        except Exception as e:
            job.update(status="error", error=str(e))
            self._log("warn", f"Stopped by a problem: {e}")
            if not isinstance(e, AccountError):
                log.warning("Discord archive failed", exc_info=True)
        finally:
            job["stats"]["current"] = None
            try:
                self.chat.flush()
                self._save()
            except Exception as e:
                log.warning("Discord archive: could not write to the drive: %s", e)

    def _work(self, job):
        chat, store, stats, cfg = self.chat, self.chat.store, job["stats"], job["config"]
        api, me = chat.api, chat.me["id"]
        only = set(cfg["userIds"])
        if job["channels"] is None:
            job["channels"] = self._expand(job)
            stats["channelsTotal"] = len(job["channels"])
            self._log("info", f"Archiving {len(job['channels'])} channel(s)…")
            self._save()
        channels = job["channels"]
        while job["index"] < len(channels):
            ch = channels[job["index"]]
            stats["current"] = ch["name"]
            cursor = job["cursor"] or (snowflake_from_ms(cfg["before"]) if cfg["before"] else None)
            saved_here = unwritten = 0
            try:
                while True:
                    page = api.messages(ch["id"], before=cursor, stop=self._stop)
                    reached_start = False
                    for m in page:
                        cursor = m["id"]
                        if cfg["after"] and (parse_ts(m.get("timestamp")) or 0) < cfg["after"]:
                            reached_start = True
                            break
                        author = (m.get("author") or {}).get("id")
                        if (cfg["scope"] == "mine" and author != me) or (cfg["scope"] == "others" and author == me):
                            continue
                        if only and author not in only:
                            continue
                        if cfg.get("attachmentsOnly"):
                            if not m.get("attachments"):
                                continue
                            m = without_text(store, m)
                        if (cfg["downloadAttachments"] or cfg.get("attachmentsOnly")) and m.get("attachments"):
                            stats["files"] += store.save_attachments(api, m, ch["guildId"], self._stop)
                        if store.save_message(m, "archive", me, guild_id=ch["guildId"]):
                            saved_here += 1
                            unwritten += 1
                            stats["saved"] += 1
                    stats["scanned"] += len(page)
                    job["cursor"] = cursor
                    if unwritten >= SEGMENT:
                        store.flush_archive(ch["id"])
                        unwritten = 0
                    self._save()
                    if reached_start or len(page) < 100:
                        break
                    if self._stop.wait(random.uniform(0.4, 1.0)):      # read like a person scrolling back, not a burst
                        raise InterruptedError("stopped")
                self._log("info", f"{ch['name']}: {saved_here} new message(s).")
            except AccountError as e:
                if e.status not in (403, 404):
                    raise
                self._log("warn", f"{ch['name']}: skipped ({'no access' if e.status == 403 else e}).")
            store.flush_archive(ch["id"])
            job.update(index=job["index"] + 1, cursor=None)
            stats["channelsDone"] = job["index"]
            self._save()
        self._log("success", f"Done. {stats['saved']} new message(s) and {stats['files']} file(s) are in {FOLDER} on your drive.")


class Chat:
    """The Discord account connected to this device's drive, with its logger and archiver."""

    def __init__(self, drive):
        self.d = drive
        self._store = None
        self._store_lock = threading.Lock()
        self.api = None
        self.me = None
        self.guilds = []
        self.dms = []
        self.error = None                 # why the saved login isn't working
        self.logger = Logger(self)
        self.archiver = Archiver(self)
        self.make_api = AccountAPI        # replaced in tests
        self.make_gateway = Gateway
        self._stop = threading.Event()
        self._flushed = time.monotonic()
        self._urls = {}                   # attachment id -> (a working link or None, until when that holds)

    @property
    def store(self):
        with self._store_lock:
            if self._store is None:
                self._store = ChatStore(self.d)
            return self._store

    def in_use(self):
        """False on a device where none of this was ever used (nothing is created for it then)."""
        return self._store is not None or self.api is not None or self.d.index.resolve(FOLDER) is not None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        threading.Thread(target=self._background, name="DiscordMessages", daemon=True).start()

    def stop(self):
        self._stop.set()
        self.archiver.stop(closing=True)
        self.logger.stop(remember=True)
        if self._store is not None:
            self._store.close()

    def _background(self):
        token = clean_token(getattr(self.d.cfg, "discord_user_token", ""))
        while token and self.api is None and not self._stop.is_set():
            try:
                self.connect(token, save=False)
                if self.store.kv_get("logger.enabled"):
                    self.logger.start()
                if (self.archiver.job or {}).get("status") == "running":      # it was running when the drive stopped
                    self.archiver.resume()
            except AccountError as e:
                # Only a rejected token is final. Being offline is not: keep it and keep trying.
                if e.status == 401:
                    self.error = "Discord no longer accepts the saved login. Log in again."
                    log.warning("Discord messages: %s", self.error)
                    break
                self._stop.wait(30)
            except Exception as e:
                log.warning("Discord messages: could not start: %s", e)
                break
        compacted = scanned = 0.0
        while not self._stop.wait(TICK):
            try:
                if not self.in_use():
                    continue
                if time.monotonic() - self._flushed >= FLUSH_SECONDS and self.store.pending():
                    self.flush()
                if time.monotonic() - scanned < SCAN_SECONDS:
                    continue
                scanned = time.monotonic()
                self.store.scan(self._stop)
                if time.monotonic() - compacted >= 3600:
                    compacted = time.monotonic()
                    self.store.compact_live()
            except Exception as e:
                if not self._stop.is_set():
                    log.warning("Discord messages: %s", e)

    def flush(self):
        """Write everything that is only on this computer so far to the drive."""
        self._flushed = time.monotonic()
        store = self.store
        return store.flush_live() + store.flush_archive()

    # ------------------------------------------------------------ the account
    def require(self):
        if self.api is None:
            raise ApiErrorProxy(409, "No Discord account is connected. Log in with Discord in Settings.")

    def _save_token(self, token):
        self.d.cfg.discord_user_token = token
        if not self.d.local_test_dir:
            saved = Config.load()
            saved.discord_user_token = token
            saved.save()
            if self.d.web is not None:
                self.d.web._cfg_mtime = os.path.getmtime(config_path())

    def connect(self, token, save=True):
        token = clean_token(token)
        if not token:
            raise ApiErrorProxy(400, "Paste your Discord token to log in.")
        api = self.make_api(token)
        try:
            me = api.me()
        except AccountError as e:
            if e.status == 401 and save:
                raise ApiErrorProxy(400, "Discord rejected that token. Check it and try again.") from None
            raise
        if self.api is not None:              # logging in again, perhaps as somebody else
            self.archiver.stop()
            self.logger.stop()
        self.api, self.me, self.error = api, me, None
        if save:
            self._save_token(token)
            log.info("Discord messages: logged in as %s.", me.get("username"))
        try:
            self.sync_directory()
        except AccountError as e:
            log.warning("Discord messages: the list of servers could not be read yet: %s", e)

    def disconnect(self):
        self.archiver.stop()
        self.logger.stop()
        self.api = self.me = self.error = None
        self.guilds, self.dms = [], []
        self._save_token("")
        log.info("Discord messages: logged out.")

    # ------------------------------------------------------------ attachments, from Discord itself
    @staticmethod
    def _expires(url):
        """When a Discord attachment link stops working (they carry it), or None when it doesn't say."""
        try:
            return int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["ex"][0], 16)
        except (KeyError, ValueError, IndexError):
            return None

    def refresh(self, attachments):
        """Ask Discord for working links for those whose link has run out (several at once)."""
        now = time.time()
        stale = [a for a in attachments if a.get("url") and self._urls.get(a.get("id"), (None, 0))[1] <= now
                 and (self._expires(a["url"]) or now + 600) < now + 300]
        if len(self._urls) > 5000:
            self._urls.clear()
        for i in range(0, len(stale), 50):
            some, got = stale[i:i + 50], {}
            if self.api is not None:
                try:
                    got = self.api.refresh_urls([a["url"] for a in some])
                except Exception as e:
                    log.debug("refreshing attachment links: %s", e)
            for a in some:
                url = got.get(a["url"])
                self._urls[a.get("id")] = (url, (self._expires(url) or now + 3600) if url else now + 600)

    def live_url(self, a):
        """A link to the attachment on Discord that should still work, or None."""
        url, until = self._urls.get(a.get("id"), (None, 0))
        if until > time.time():
            return url
        url = a.get("url")
        return url if url and (self._expires(url) or time.time() + 600) > time.time() + 60 else None

    def fetch(self, url):
        return (self.api or self.make_api("")).download(url)

    def sync_directory(self):
        self.require()
        store = self.store
        self.guilds = sorted(({"id": g["id"], "name": g.get("name") or g["id"], "members": g.get("approximate_member_count")}
                              for g in self.api.guilds()), key=lambda g: g["name"].lower())
        dms = sorted(self.api.dms(), key=lambda c: -int(c.get("last_message_id") or 0))
        self.dms = [{"id": c["id"], "name": dm_name(c), "group": c.get("type") == 3} for c in dms]
        for g in self.guilds:
            store.name_guild(g["id"], g["name"])
        for c in self.dms:
            store.name_channel(c["id"], None, c["name"], 3 if c["group"] else 1)
        return {"guilds": self.guilds, "dms": self.dms}

    def guild_channels(self, guild_id):
        self.require()
        everything = self.api.guild_channels(guild_id)
        groups = {c["id"]: c for c in everything if c.get("type") == 4}
        out = []
        for c in everything:
            if c.get("type") in TEXT_CHANNELS:
                group = groups.get(c.get("parent_id")) or {}
                out.append({"id": c["id"], "name": c.get("name") or c["id"], "type": c["type"], "category": group.get("name"),
                            "order": (group.get("position", -1)) * 1000 + (c.get("position") or 0)})
        out.sort(key=lambda c: c["order"])
        for c in out:
            self.store.name_channel(c["id"], guild_id, c["name"], c["type"])
        return out

    def state(self):
        me = self.me
        out = {"connected": self.api is not None, "error": self.error, "folder": FOLDER, "device": socket.gethostname(),
               "user": me and {"id": me.get("id"), "username": me.get("username"),
                               "name": me.get("global_name") or me.get("username")},
               "counts": {"guilds": len(self.guilds), "dms": len(self.dms)}, "logger": None, "archive": None, "totals": None}
        if self.in_use():
            out.update(logger=self.logger.state(), archive=self.archiver.state(), totals=self.store.totals())
        return out


class ChatHandlers:
    """The dashboard's API for Discord messages (mixed into web._Handler)."""

    @property
    def chat(self) -> Chat:
        return self.drive.chat

    def _chat_guard(self):
        if not self._shown(FOLDER):
            raise ApiErrorProxy(404, f"{FOLDER} is hidden or locked on this device")

    def _chat_state(self):
        state = self.chat.state()
        if not self._shown(FOLDER):
            state.update(locked=True, totals=None)
        self._json(state)

    def _get_api_chat(self):
        self._chat_state()

    def _post_api_chat_connect(self):
        self._chat_guard()
        try:
            self.chat.connect(self._body_json().get("token"))
        except AccountError as e:
            raise ApiErrorProxy(502, str(e)) from None
        self._chat_state()

    def _post_api_chat_disconnect(self):
        self._body_json()
        self.chat.disconnect()
        self._chat_state()

    def _get_api_chat_directory(self):
        self._chat_guard()
        chat = self.chat
        chat.require()
        try:
            if self._query().get("refresh") == "1" or not (chat.guilds or chat.dms):
                chat.sync_directory()
        except AccountError as e:
            raise ApiErrorProxy(502, str(e)) from None
        self._json({"guilds": chat.guilds, "dms": chat.dms})

    def _get_api_chat_channels(self):
        self._chat_guard()
        try:
            self._json({"channels": self.chat.guild_channels(str(self._query().get("guild") or ""))})
        except AccountError as e:
            raise ApiErrorProxy(403 if e.status == 403 else 502, str(e)) from None

    def _post_api_chat_logger(self):
        self._chat_guard()
        if self._body_json().get("on"):
            self.chat.logger.start()
        else:
            self.chat.logger.stop()
        self._chat_state()

    def _post_api_chat_logger_rules(self):
        self._chat_guard()
        self._json({"rules": self.chat.logger.set_rules(self._body_json())})

    def _post_api_chat_archive_start(self):
        self._chat_guard()
        self.chat.archiver.start(self._body_json())
        self._chat_state()

    def _post_api_chat_archive_stop(self):
        self._body_json()
        self.chat.archiver.stop()
        self._chat_state()

    def _post_api_chat_archive_resume(self):
        self._chat_guard()
        self._body_json()
        self.chat.archiver.resume()
        self._chat_state()

    def _post_api_chat_archive_clear(self):
        self._body_json()
        self.chat.archiver.clear()
        self._chat_state()

    def _post_api_chat_flush(self):
        self._chat_guard()
        self._body_json()
        self._json({"ok": True, "written": self.chat.flush()})

    def _post_api_chat_rebuild(self):
        self._chat_guard()
        self._body_json()
        read, _ = self.chat.store.rebuild()
        self._json({"ok": True, "files": read})

    def _get_api_chat_messages(self):
        self._chat_guard()
        found = self.chat.store.messages(self._query())
        # the pictures on this page are about to be asked for: get working links for them in one go
        self.chat.refresh([a for m in found["messages"] for a in m["attachments"] if chatstore.is_media(a)])
        self._json(found)

    def _chat_attachment(self):
        self._chat_guard()
        q = self._query()
        row = self.chat.store.get(q.get("id") or "")
        for a in json.loads(row["attachments"] or "[]") if row is not None else ():
            if str(a.get("id")) == q.get("a"):
                self.chat.refresh([a])
                return a
        raise ApiErrorProxy(404, "That attachment is not known")

    def _get_api_chat_preview(self):
        """A small picture of an attachment: from Discord, or the copy on the drive when Discord has none."""
        a, chat, data = self._chat_attachment(), self.chat, None
        if not chatstore.is_media(a):
            return self._error(404, "no preview")
        url = chat.live_url(a)
        if url:
            try:
                with contextlib.closing(chat.fetch(chatstore.preview_url(url))) as src:
                    data = src.read(chatstore.PREVIEW_MAX + 1)
                if len(data) > chatstore.PREVIEW_MAX or not chatstore.picture_type(data):
                    data = None
            except Exception:
                data = None
        if data is None and a.get("preview"):
            node = self.drive.index.resolve(a["preview"])
            if node is not None and not node["is_dir"]:
                data = self.drive.fs.read_file(node["id"], 0, min(node["size"], chatstore.PREVIEW_MAX))
        if data is None and a.get("path"):           # saved by 1.3.2, with the dashboard's own thumbnails
            node = self.drive.index.resolve(a["path"])
            data = self._thumb_read(node) if node is not None else None
        if not data or not chatstore.picture_type(data):
            return self._error(404, "no preview")
        self.send_response(200)
        self.send_header("Content-Type", chatstore.picture_type(data))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if self.drive.locks.covering(FOLDER) else "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _get_api_chat_open(self):
        """Open an attachment: on Discord when it is still there (checked first), else the copy on the drive."""
        a = self._chat_attachment()
        url = self.chat.live_url(a)
        if url:
            try:
                self.chat.fetch(url).close()
                return self._redirect(url)
            except Exception as e:
                log.debug("attachment %s is not on Discord any more: %s", a.get("filename"), e)
        if a.get("path") and self.drive.index.resolve(a["path"]) is not None:
            return self._redirect("/api/file?path=" + urllib.parse.quote(a["path"]))
        self._send(404, b"This attachment is gone from Discord and there is no copy of it on the drive.", "text/plain; charset=utf-8")

    def _get_api_chat_places(self):
        self._chat_guard()
        self._json({"items": self.chat.store.places()})

    def _get_api_chat_export(self):
        self._chat_guard()
        q = self._query()
        fmt = q.get("format") if q.get("format") in chatstore.EXPORT_TYPES else "json"
        self.send_response(200)
        self.send_header("Content-Type", chatstore.EXPORT_TYPES[fmt] + "; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="discord-messages-{time.strftime("%Y-%m-%d")}.{fmt}"')
        self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            for piece in self.chat.store.export(q, fmt):
                self.wfile.write(piece)
