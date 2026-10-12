"""Accounts: other people on this drive's dashboard, each with storage of their own.

An account is a 24-word phrase and nothing else: no name, no password, no email. The dashboard makes
the phrase when the account is created (words.py: 256 random bits written as words), shows it once,
and signing in means typing it. The phrase is the account's encryption key: everything the account
stores is encrypted with it before it goes to Discord, so two accounts can't read each other's data
and the owner's own drive key doesn't open it either.

Each account is a small drive of its own (AccountDrive): its own file list, earlier versions,
snapshots, share links, locks, notes, bookmarks, contacts and messages, in <data dir>/accounts/<id>.
It shares the drive's Discord channel and bots, and so their pace: an account's pieces are ordinary
messages there, which only that account's file list knows about. An account's drive is opened when
it is used and closed again a while after its last upload is done.

Only the computer that serves an account knows its file list, so its change journal stays on that
computer (there is nobody to sync with). A checkpoint of the file list still goes to Discord, as the
drive's own does: if the account's folder here is lost, the next sign-in rebuilds it from there.

The list of accounts says where each checkpoint is, so a copy of it is kept in the channel too
(Accounts.backup): one pinned message per computer that serves accounts, encrypted with the drive's
key, replaced when the list has changed. It has no account keys in it. If the computer's disk is
lost, the owner brings the list back on the rebuilt computer (Settings, Accounts) and each account
works again once it has signed in with its words. Nothing brings it back by itself: a second device
on the same channel sees the copy too, and two computers serving the same accounts would each keep
a journal of their own.

What the computer keeps of an account (accounts.json): its id (a hash of the key, which doesn't give
the key away), when it was made and used, and the key itself, encrypted for this computer like the
secrets of the config file (secretbox.py). The key has to be here for the things an account's drive
does while nobody is signed in: finishing uploads, share links, logging messages. So this is storage
that Discord and other accounts can't read; whoever runs the server could.

Who may make an account is `signup_mode`: closed (nobody), invite (with a code made in the owner's
Settings) or public (anybody who can reach the dashboard).
"""

import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import asdict

from .backend import LocalBackend
from .config import Config, MiB
from .crypto import KeyRing
from .journal import Journal, OP_PREFIX, _hostname
from .logbuf import hush_accounts, quiet
from .shares import Shares
from . import words

log = logging.getLogger("ddrive.accounts")

MODES = ("closed", "invite", "public")
MARKER = "DISCORDDRIVE_ACCOUNT"     # an account's index checkpoint in the channel (the drive's own: backend.INDEX_MARKER)
LIST_MARKER = "DDRIVE_ACCOUNTS"     # a computer's copy of its list of accounts in the channel (pinned)
BACKUP_EVERY = 600.0                # the copy is written at most this often, unless something can't wait
IDLE_CLOSE = 600.0                  # an account's drive is closed this long after it was last used
JOURNAL_KEEP = 15 * 86400.0         # how long an account's applied journal entries are kept on this computer
OFFER_MINUTES = 30                  # how long somebody has to write down a new phrase and confirm it
SIGNUPS_PER_HOUR = 3                # new accounts from one address
CACHE_BYTES = 256 * MiB             # read cache of one open account
STAGING_BYTES = 2 * 1024 * MiB      # what one account may have waiting to upload


class AccountError(Exception):
    """Something the person at the sign-up or sign-in page should be told."""


def account_id(key: bytes) -> str:
    """An account's public name: it is in its folder's name and in its share links. Not secret."""
    return hashlib.sha256(b"DiscordDrive-account:" + key).hexdigest()[:16]


def _check(key: bytes) -> str:
    return hashlib.sha256(b"DiscordDrive-account-check:" + key).hexdigest()


class _AccountConfig(Config):
    """The settings an account's drive runs with: the drive's own, with the account's key and folder.
    It is never written anywhere (the file on disk is the owner's)."""

    owner = None

    @property
    def quota_bytes(self):
        return int(getattr(self.owner, "account_quota_bytes", 0) or 0)

    def save(self, *args, **kwargs):
        raise RuntimeError("an account's settings are not a config file")


class _AccountJournal(Journal):
    """An account has one key for good, so no list of earlier keys is kept (or looked for in the config)."""

    def _store_keyring(self):
        pass

    def _learn_keys(self):
        pass

    def bootstrap(self):
        try:
            super().bootstrap()
        except Exception as e:
            if not self.index.kv_get("journal_cursor"):
                raise
            log.warning("An account's first checkpoint could not be saved yet (%s); it is retried.", e)


class AccountBackend:
    """Where an account's drive stores things: pieces go to the drive's own backend (the Discord
    channel); the change journal is a folder on this computer; index checkpoints go to the channel
    under a marker of their own, so the drive's journal and checkpoints never take them for theirs."""

    def __init__(self, base, journal_dir, crypto, checkpoint=None, on_checkpoint=None):
        self.base = base
        self.crypto = crypto
        self.local = LocalBackend(journal_dir, max_attachment=1 << 40)
        self.checkpoint = dict(checkpoint or {})       # {"mid", "cursor"} of the newest one
        self.on_checkpoint = on_checkpoint

    @property
    def upload_slots(self):
        return getattr(self.base, "upload_slots", 1)

    # ------------------------------------------------------------ pieces: the channel
    def upload(self, filename, data, content=""):
        if (content or "").startswith(OP_PREFIX):      # a journal entry too long for one message
            return self.local.upload(filename, data, content)
        return self.base.upload(filename, data, content)

    def download(self, message_id, url):
        return self.base.download(message_id, url)

    def message_info(self, message_id):
        return self.base.message_info(message_id)

    def delete(self, message_id):
        return self.base.delete(message_id)

    # ------------------------------------------------------------ the journal: this computer
    def post_text(self, content):
        return self.local.post_text(content)

    def messages_after(self, after, limit=100):
        return self.local.messages_after(after, limit)

    def latest_message_id(self):
        return self.local.latest_message_id()

    def fetch_attachment(self, msg):
        return self.local.fetch_attachment(msg)

    def time_of(self, mid):
        return self.local.time_of(mid)

    def id_for_time(self, t):
        return self.local.id_for_time(t)

    def prune(self, cursor):
        """Forget journal entries that are in a checkpoint and older than JOURNAL_KEEP."""
        limit = min(int(cursor or 0), int(self.local.id_for_time(time.time() - JOURNAL_KEEP)))
        try:
            names = os.listdir(self.local.root)
        except OSError:
            return
        for name in names:
            stem = name.partition(".")[0]
            if stem.isdigit() and int(stem) <= limit:
                try:
                    os.remove(os.path.join(self.local.root, name))
                except OSError:
                    pass

    # ------------------------------------------------------------ checkpoints: the channel
    def save_index(self, data, previous_message_id=None, cursor=None):
        chunk = self.base.upload(f"acct_{secrets.token_hex(8)}.bin", self.crypto.encrypt(data),
                                 f"{MARKER} c={cursor}\nAn account's index checkpoint.")
        old = self.checkpoint.get("mid") or previous_message_id
        self.checkpoint = {"mid": chunk.message_id, "cursor": str(cursor or "0")}
        # The copy of the list of accounts in the channel may still name the previous one: then it
        # stays until a newer copy has been written (Accounts.backup deletes it).
        keep = self.on_checkpoint is not None and self.on_checkpoint(dict(self.checkpoint))
        if old and old != chunk.message_id and not keep:
            try:
                self.base.delete(old)
            except Exception as e:
                log.debug("an account's previous checkpoint could not be deleted: %s", e)
        self.prune(cursor)
        return chunk.message_id

    def find_latest_index(self, *args):
        if not self.checkpoint.get("mid"):
            return None
        return {"id": self.checkpoint["mid"], "content": f"{MARKER} ENC c={self.checkpoint.get('cursor') or 0}",
                "attachments": [{"url": None}], "cursor": str(self.checkpoint.get("cursor") or "0")}

    def load_index_message(self, msg):
        data, _ = self.base.download(msg["id"], (msg.get("attachments") or [{}])[0].get("url"))
        return self.crypto.decrypt(data) if data.startswith(b"DENC") else data

    def load_latest_index(self, *args):
        latest = self.find_latest_index()
        return None if latest is None else (latest["id"], self.load_index_message(latest))


class AccountShares(Shares):
    """An account's share links. Their addresses start with the account's id (/s/<account>.<link>):
    that is how a visitor's request finds the account a link belongs to."""

    def __init__(self, index, account):
        super().__init__(index, "account")
        self.prefix = account + "."

    def _own(self, sid):
        sid = str(sid or "")
        return sid[len(self.prefix):] if sid.startswith(self.prefix) else sid

    def all(self):
        return {self.prefix + sid: rec for sid, rec in super().all().items()}

    def get(self, sid):
        return super().get(self._own(sid))

    def create(self, *args, **kwargs):
        sid, rec = super().create(*args, **kwargs)
        return self.prefix + sid, rec

    def update(self, sid, **kwargs):
        return super().update(self._own(sid), **kwargs)

    def revoke(self, sid):
        return super().revoke(self._own(sid))

    def viewed(self, sid):
        super().viewed(self._own(sid))


class AccountDrive:
    """One account's drive: what a drive is made of (file list, journal, uploader, cache, filesystem),
    without a drive letter, folder sync or anything else that belongs to the computer."""

    sync = None
    api = None
    web = None
    space = None          # what the dashboard keeps per account (web._Space)

    def __init__(self, accounts, rec, key: bytes, token=""):
        owner = accounts.drive
        self.accounts = accounts
        self.id = rec["id"]
        self.local_test_dir = owner.local_test_dir
        self.dir = accounts.folder(self.id)
        base = asdict(owner.cfg)
        base.update(data_dir=self.dir, encryption_enabled=True, encryption_key=key.hex(), encryption_salt="",
                    old_encryption_keys=[], device_id="account", mount_point="", hidden_folders=[], sync_jobs=[],
                    bot_token="", extra_bot_tokens=[], web_token="", web_user="", web_password="", web_passkeys=[],
                    webdav_enabled=False, fetch_private=False, sync_remote_edit=False, cache_mode="disk",
                    cache_max_bytes=min(int(owner.cfg.cache_max_bytes or CACHE_BYTES), CACHE_BYTES),
                    staging_max_bytes=min(int(owner.cfg.staging_max_bytes or STAGING_BYTES), STAGING_BYTES),
                    signup_mode="closed", discord_user_token=token or "")
        self.cfg = _AccountConfig(**base)
        self.cfg.owner = owner.cfg
        self.crypto = KeyRing(key)
        self.backend = AccountBackend(owner.backend, os.path.join(self.dir, "journal"), self.crypto,
                                      checkpoint=rec.get("checkpoint"),
                                      on_checkpoint=lambda cp: accounts._checkpointed(self.id, cp))
        self.users = 0                      # requests using it right now
        self.last_used = time.time()
        self.busy = []                      # more reasons to stay open: callables (the dashboard's downloads from links)
        self._stop_event = threading.Event()
        self._started = False
        self.index = self.journal = self.cache = self.fs = self.locks = self.uploader = None
        self.healer = self.checker = self.maintenance = self.chat = None

    def open(self):
        from .cache import ChunkCache
        from .chatlog import Chat
        from .fs import DDriveFS
        from .heal import Checker, Healer, Maintenance
        from .index import Index
        from .locks import Locks
        from .uploader import Uploader
        cfg = self.cfg
        staging, cache_dir = os.path.join(self.dir, "staging"), os.path.join(self.dir, "cache")
        os.makedirs(staging, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)
        with quiet():                       # what an account does is not written to the owner's log
            self._open(cfg, staging, cache_dir, ChunkCache, Chat, DDriveFS, Checker, Healer, Maintenance, Index,
                       Locks, Uploader)
        return self

    def _open(self, cfg, staging, cache_dir, ChunkCache, Chat, DDriveFS, Checker, Healer, Maintenance, Index, Locks,
              Uploader):
        self.index = Index(os.path.join(self.dir, "index.db"))
        try:
            self.index.keep_versions = bool(cfg.keep_versions)
            self.journal = _AccountJournal(cfg, self.index, self.backend, crypto=self.crypto, staging_dir=staging)
            self.journal.bootstrap()
            self.cache = ChunkCache(cache_dir, max_bytes=cfg.cache_max_bytes, backend=self.backend, index=self.index,
                                    threads=2, crypto=self.crypto, min_free=cfg.min_free_disk_bytes)
            self.fs = DDriveFS(cfg, self.index, self.cache, staging)
            self.locks = self.fs.locks = Locks(self.index, cfg)
            self.locks.mount_relock()
            self.uploader = Uploader(cfg, self.index, self.backend, self.fs, crypto=self.crypto)
            self.fs.uploader = self.uploader
            self.uploader.journal = self.journal
            self.journal.fs = self.fs
            self.healer = Healer(cfg, self.index, self.backend, crypto=self.crypto, cache=self.cache, wake=self.journal.wake)
            self.cache.healer = self.healer
            self.checker = Checker(self.index, self.backend, self.crypto, self.healer)
            self.maintenance = Maintenance(cfg, self.index, self.backend, self.healer, uploader=self.uploader,
                                           journal=self.journal, cache=self.cache, device_id=cfg.device_id)
            self.chat = Chat(self)
            self.fs.recover()
        except Exception:
            self.close()
            raise

    def start(self):
        """The background work of a drive in use (not in tests, which do each step themselves)."""
        self._started = True
        with quiet():
            self.uploader.start()
            self.journal.start()
            self.healer.start()
            self.maintenance.start()
            self.chat.start()

    def save_token(self, token):
        self.accounts._set_token(self.id, token)

    def in_use(self):
        """True while something would be cut short by closing it."""
        if self.users > 0 or any(fn() for fn in self.busy):
            return True
        try:
            if not self.uploader.is_idle() or self.index.unsynced_ids() or self.index.outbox_count():
                return True
            chat = self.chat
            return chat.api is not None and (chat.logger.status != "stopped" or chat.archiver.running())
        except Exception:
            return False

    def usage(self):
        st = self.index.stats(max_age=5)
        return {"files": st["files"], "bytes": st["bytes"] + st["version_bytes"]}

    def close(self):
        with quiet():
            self._close()

    def _close(self):
        self._stop_event.set()
        started = self._started
        for part, stop in ((self.chat, "stop"), (self.maintenance, "stop"), (self.uploader, "stop"), (self.healer, "stop")):
            if part is not None and started:
                try:
                    getattr(part, stop)()
                except Exception as e:
                    log.debug("closing an account: %s", e)
        if self.chat is not None and not started and self.chat._store is not None:
            self.chat._store.close()
        if self.journal is not None:
            if started:
                self.journal.stop()
            else:
                try:
                    with self.journal._sync_lock:
                        self.journal.flush()
                        self.journal.poll()
                        self.journal.checkpoint()
                except Exception as e:
                    log.debug("closing an account: %s", e)
        if self.cache is not None:
            self.cache.shutdown()
        if self.index is not None:
            self.index.close()
        self.crypto.close()


class Accounts:
    def __init__(self, drive):
        self.drive = drive
        self.cfg = drive.cfg
        self.root = os.path.join(drive.cfg.resolved_data_dir, "accounts")
        self.path = os.path.join(self.root, "accounts.json")
        self.background = not drive.local_test_dir     # tests run each step themselves
        self._lock = threading.RLock()
        self._gates = {}                    # account id -> lock held while its drive opens or closes
        self._open = {}                     # account id -> AccountDrive
        self._recs = {}
        self._invites = {}
        self._secrets = {}                  # account id -> {"key": hex, "token": its Discord login}
        self._signups = {}                  # address -> [times of the accounts made from it]
        self._dirty = False
        self._replaces = ""                 # the copy in the channel this list was brought back from
        self._backup_lock = threading.Lock()    # one copy of the list is written at a time
        self._backup_wanted = False         # the list changed since its copy in the channel was written
        self._backup_soon = False           # ...in a way that shouldn't wait for BACKUP_EVERY
        self._backup_at = 0.0
        self._found = (0.0, [])             # (when looked, the copies in the channel)
        self._stop = threading.Event()
        self._thread = None
        self._load()

    # ------------------------------------------------------------ the list on disk
    def folder(self, aid):
        if not re.fullmatch(r"[0-9a-f]{16}", str(aid)):
            raise AccountError("not an account")
        return os.path.join(self.root, aid)

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.error("The list of accounts (%s) can't be read: %s. Accounts are unavailable until it is repaired.", self.path, e)
            self._broken = True
            return
        self._recs = dict(raw.get("accounts") or {})
        self._invites = dict(raw.get("invites") or {})
        self._replaces = str(raw.get("replaces") or "")
        self._backup_wanted = bool(self._recs or self._invites)     # whatever changed last may not be in the copy
        kept = raw.get("secrets") or {}
        if raw.get("protected"):
            from . import secretbox
            try:
                kept = json.loads(secretbox.unprotect(raw["protected"]))
            except (secretbox.ProtectError, ValueError) as e:
                # Every account still opens: with its phrase, which is its key.
                log.warning("The accounts' keys can't be read on this computer (%s). Each account works again once "
                            "it has signed in with its phrase; until then its share links don't.", e)
        self._secrets = {aid: dict(s) for aid, s in kept.items() if aid in self._recs}

    _broken = False

    def _save(self):
        """Write the list (call with the lock held)."""
        if self._broken:
            raise AccountError("the list of accounts on this computer is damaged")
        data = {"accounts": self._recs, "invites": self._invites}
        if self._replaces:
            data["replaces"] = self._replaces
        plain = json.dumps(self._secrets, separators=(",", ":"))
        if self.drive.local_test_dir or not getattr(self.cfg, "protect_config", True):
            data["secrets"] = self._secrets
        else:
            from . import secretbox
            try:
                data["protected"] = secretbox.protect(plain.encode("utf-8"))
            except secretbox.ProtectError as e:
                log.warning("The accounts' keys could not be encrypted on disk (%s); they are stored readable.", e)
                data["secrets"] = self._secrets
        os.makedirs(self.root, exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.path)
        self._dirty = False

    def _checkpointed(self, aid, checkpoint):
        """An account's file list has a new checkpoint. True: its previous one has to stay for now."""
        with self._lock:
            rec = self._recs.get(aid)
            if rec is None:
                return False
            old = (rec.get("checkpoint") or {}).get("mid")
            rec["checkpoint"] = checkpoint
            self._save()
            self._backup_wanted = True
            return bool(old) and old in (rec.get("kept") or [])

    def _set_token(self, aid, token):
        with self._lock:
            if aid in self._secrets:
                self._secrets[aid]["token"] = token or ""
                self._save()

    # ------------------------------------------------------------ who may sign up
    def mode(self):
        mode = str(getattr(self.cfg, "signup_mode", "") or "closed").lower()
        return mode if mode in MODES else "closed"

    def _secret(self):
        return hashlib.sha256(b"DiscordDrive-signup:" + (self.cfg.web_token or "").encode()).digest()

    def _invite_ok(self, code):
        inv = self._invites.get(str(code or "").strip())
        if inv is None or (inv.get("expires") and inv["expires"] <= time.time()):
            return False
        return not inv.get("uses") or inv.get("used", 0) < inv["uses"]

    def _may_sign_up(self, invite, addr):
        mode = self.mode()
        if self._broken:
            raise AccountError("Accounts are unavailable right now.")
        if mode == "closed":
            raise AccountError("New accounts can't be made here.")
        if mode == "invite" and not self._invite_ok(invite):
            raise AccountError("That invite code isn't valid (any more)." if str(invite or "").strip()
                               else "An invite code is needed to make an account here.")
        limit = int(getattr(self.cfg, "accounts_max", 0) or 0)
        if limit and len(self._recs) >= limit:
            raise AccountError("No more accounts can be made here: it is full.")
        recent = [t for t in self._signups.get(addr, []) if t > time.time() - 3600]
        if addr is not None and len(recent) >= SIGNUPS_PER_HOUR:
            raise AccountError("Several accounts were just made from your address. Try again in an hour.")

    def _sign(self, key: bytes, invite, until):
        msg = f"{key.hex()}:{str(invite or '').strip()}:{int(until)}".encode()
        return hmac.new(self._secret(), msg, hashlib.sha256).hexdigest()

    def offer(self, invite="", addr=None):
        """A new phrase for somebody who may sign up: (the 24 words, a ticket to confirm it with).
        Nothing is kept until it is confirmed (create)."""
        with self._lock:
            self._may_sign_up(invite, addr)
        key = secrets.token_bytes(32)
        until = int(time.time() + OFFER_MINUTES * 60)
        return words.key_to_phrase(key), f"{until}.{self._sign(key, invite, until)}"

    def create(self, phrase, ticket, invite="", addr=None):
        """Make the account of a phrase this dashboard offered. Returns its record."""
        try:
            key = words.phrase_to_key(phrase)
            until, _, sig = str(ticket or "").partition(".")
            fresh = int(until) > time.time() and hmac.compare_digest(sig, self._sign(key, invite, int(until)))
        except ValueError:
            fresh = False
        if not fresh:
            raise AccountError("That took too long. Start again to get a new phrase.")
        return self._make(key, invite, addr)

    def create_e2e(self, auth: bytes, invite="", addr=None, owner=False):
        """Make an end-to-end encrypted account (vault.py). Its 24 words were made on the device and
        stay there; `auth` is what the device made from them to sign in with. To this computer that
        is the account's key: what it stores for the account is sealed once more with it, but
        everything it is handed was sealed on the device first, with keys it never gets."""
        if len(auth) != 32:
            raise AccountError("That isn't a sign-in key.")
        return self._make(auth, invite, addr, e2e=True, owner=owner)

    def _make(self, key, invite, addr, e2e=False, owner=False):
        """owner: the owner makes it from their dashboard (to move their own files in): whether and how
        many others may sign up doesn't come into it."""
        aid = account_id(key)
        with self._lock:
            if aid in self._recs:
                return self._recs[aid]          # confirmed twice (a double click, a reload)
            if not owner:
                self._may_sign_up(invite, addr)
            code = str(invite or "").strip()
            rec = {"id": aid, "check": _check(key), "created": time.time(), "seen": time.time(),
                   "invite": code if code in self._invites else ""}
            if e2e:
                rec["e2e"] = True
            self._recs[aid] = rec
            self._secrets[aid] = {"key": key.hex(), "token": ""}
            if code in self._invites:
                self._invites[code]["used"] = self._invites[code].get("used", 0) + 1
            self._backup_wanted = self._backup_soon = True
            if addr is not None:
                self._signups[addr] = [t for t in self._signups.get(addr, []) if t > time.time() - 3600] + [time.time()]
            self._save()
        try:
            self.release(self.acquire(aid))      # set its drive up now, so a problem shows here and not later
        except Exception as e:
            log.warning("A new account's storage could not be set up: %s", e)
            with self._lock:
                self._recs.pop(aid, None)
                self._secrets.pop(aid, None)
                if code in self._invites:
                    self._invites[code]["used"] = max(0, self._invites[code].get("used", 1) - 1)
                self._save()
            shutil.rmtree(self.folder(aid), ignore_errors=True)
            raise AccountError("The account's storage could not be set up right now. Try again in a moment.") from None
        log.info("Account %s was created%s%s.", aid, " with an invite" if rec["invite"] else "",
                 " (end-to-end encrypted)" if e2e else "")
        return rec

    # ------------------------------------------------------------ signing in
    def find(self, key: bytes):
        """The account a phrase's key belongs to (None: no such account, or it is switched off)."""
        aid = account_id(key)
        with self._lock:
            rec = self._recs.get(aid)
            if rec is None or not hmac.compare_digest(rec.get("check") or "", _check(key)) or rec.get("disabled"):
                return None
            if (self._secrets.get(aid) or {}).get("key") != key.hex():      # the keys could not be read here
                self._secrets[aid] = {"key": key.hex(), "token": (self._secrets.get(aid) or {}).get("token", "")}
                self._save()
            rec["seen"] = time.time()
            self._dirty = True
            return rec

    def active(self, aid):
        """The record of an account that may be used right now, or None."""
        with self._lock:
            rec = self._recs.get(aid)
            return rec if rec is not None and not rec.get("disabled") else None

    # ------------------------------------------------------------ an account's drive
    def acquire(self, aid):
        """The account's drive, opened if it wasn't. Give it back with release()."""
        while True:
            with self._lock:
                rec = self.active(aid)
                key = (self._secrets.get(aid) or {}).get("key")
                if rec is None or not key:
                    raise AccountError("That account isn't available.")
                d = self._open.get(aid)
                if d is not None:
                    d.users += 1
                    d.last_used = time.time()
                    return d
                gate = self._gates.setdefault(aid, threading.Lock())
                token = (self._secrets.get(aid) or {}).get("token", "")
            with gate:
                with self._lock:
                    if aid in self._open or self.active(aid) is None:
                        continue
                d = AccountDrive(self, rec, bytes.fromhex(key), token).open()
                if self.background:
                    d.start()
                with self._lock:
                    self._open[aid] = d

    def release(self, d):
        with self._lock:
            d.users = max(0, d.users - 1)
            d.last_used = time.time()
            rec = self._recs.get(d.id)
            if rec is not None and time.time() - rec.get("usage_at", 0) > 30:
                try:
                    rec["usage"], rec["usage_at"] = d.usage(), time.time()
                    self._dirty = True
                except Exception:
                    pass

    def _close(self, aid, force=False, idle=IDLE_CLOSE):
        with self._lock:
            gate = self._gates.setdefault(aid, threading.Lock())
        with gate:
            with self._lock:
                d = self._open.get(aid)
                if d is None or (not force and (d.in_use() or time.time() - d.last_used < idle)):
                    return False
                rec = self._recs.get(aid)
                if rec is not None:
                    try:
                        rec["usage"], rec["usage_at"] = d.usage(), time.time()
                    except Exception:
                        pass
                del self._open[aid]
            end = time.time() + 20
            while d.users > 0 and time.time() < end:       # a request that is still being answered
                time.sleep(0.1)
            d.close()
        return True

    def tidy(self, idle=IDLE_CLOSE):
        """Close the drives of accounts nobody is using, and write down what changed."""
        for aid in list(self._open):
            try:
                self._close(aid, idle=idle)
            except Exception as e:
                log.debug("closing account %s: %s", aid, e)
        with self._lock:
            if self._dirty:
                try:
                    self._save()
                except Exception as e:
                    log.debug("saving the list of accounts: %s", e)
        if self._backup_wanted and (self._backup_soon or time.time() - self._backup_at >= BACKUP_EVERY):
            self.backup()

    def start(self):
        hush_accounts()
        if self._thread is None and self.background:
            self._thread = threading.Thread(target=self._loop, name="Accounts", daemon=True)
            self._thread.start()
        return self

    def _loop(self):
        # Accounts that log their Discord messages are opened right away: that is work for when nobody is here.
        for aid, s in list(self._secrets.items()):
            if s.get("token") and s.get("key") and self.active(aid) is not None and not self._stop.is_set():
                try:
                    self.release(self.acquire(aid))
                except Exception as e:
                    log.warning("Account %s could not be opened: %s", aid, e)
        while not self._stop.wait(60):
            self.tidy()

    def stop(self):
        self._stop.set()
        for aid in list(self._open):
            try:
                self._close(aid, force=True)
            except Exception as e:
                log.debug("closing account %s: %s", aid, e)
        with self._lock:
            if self._dirty and not self._broken:
                try:
                    self._save()
                except Exception:
                    pass
        if self._backup_wanted:
            self.backup()

    # ------------------------------------------------------------ the copy of the list in the channel
    # Each account's record says which message holds the checkpoint of its file list, and every new
    # checkpoint replaces the one before. So a copy of the list is only good while the checkpoints it
    # names exist: a record's "kept" are the checkpoints a copy names or is about to, and they are
    # deleted when a newer copy is in the channel, never before.
    def _pack(self, payload) -> bytes:
        raw = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), 6)
        crypto = self.drive.crypto
        return crypto.encrypt(raw) if crypto else raw

    def _unpack(self, data: bytes):
        if data.startswith(b"DENC"):
            if not self.drive.crypto:
                raise ValueError("it is encrypted, and this drive has no key")
            data = self.drive.crypto.decrypt(data)
        payload = json.loads(gzip.decompress(data))
        if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), dict):
            raise ValueError("not a list of accounts")
        return payload

    def _drop(self, mids):
        for mid in mids:
            try:
                self.drive.backend.delete(mid)
            except Exception as e:
                log.debug("a message that is no longer needed could not be deleted: %s", e)

    def backup(self):
        """Write the list of accounts, without their keys, to the channel. True if it is there now."""
        if self._broken:
            return False
        backend, device = self.drive.backend, self.cfg.device_id or ""
        with self._backup_lock:
            with self._lock:
                self._backup_wanted = self._backup_soon = False
                named, added = {}, {}
                for aid, rec in self._recs.items():
                    mid = named[aid] = (rec.get("checkpoint") or {}).get("mid") or ""
                    if mid and mid not in (rec.get("kept") or []):
                        rec.setdefault("kept", []).append(mid)
                        added[aid] = mid
                payload = {"v": 1, "device": device, "host": _hostname(), "saved": time.time(), "invites": self._invites,
                           "accounts": {aid: {k: v for k, v in rec.items() if k != "kept"} for aid, rec in self._recs.items()}}
                data = self._pack(payload) if named or self._invites else None
                try:
                    if added:
                        self._save()
                except Exception as e:
                    log.debug("saving the list of accounts: %s", e)
            mine = f"{LIST_MARKER} d={device}\n"
            try:
                new = None
                if data is not None:
                    new = backend.save_pinned(f"accounts_{secrets.token_hex(8)}.bin", data,
                                              mine + "The list of accounts of one computer.")
                # ...and the copies it replaces: this computer's earlier ones, and the one it was brought back from.
                gone = {self._replaces}
                try:
                    gone.update(m["id"] for m in backend.pinned(LIST_MARKER) if (m.get("content") or "").startswith(mine))
                except Exception:
                    if new is None:             # nothing to keep any more, and the copy may still be there
                        raise
                gone = sorted(gone - {new, ""})
            except Exception as e:
                log.warning("The list of accounts could not be saved to Discord (%s); it is tried again.", e)
                drop = []
                with self._lock:
                    self._backup_wanted = True
                    self._backup_at = time.time()
                    for aid, mid in added.items():      # as it was: nothing in the channel names these
                        rec = self._recs.get(aid)
                        if rec is not None and mid in (rec.get("kept") or []):
                            rec["kept"].remove(mid)
                            if (rec.get("checkpoint") or {}).get("mid") != mid:
                                drop.append(mid)
                    try:
                        self._save()
                    except Exception:
                        pass
                self._drop(drop)
                return False
            drop = list(gone)
            with self._lock:
                for aid, mid in named.items():
                    rec = self._recs.get(aid)
                    if rec is None:
                        continue
                    drop += [m for m in rec.get("kept") or [] if m != mid and m != (rec.get("checkpoint") or {}).get("mid")]
                    rec["kept"] = [mid] if mid else []
                self._replaces = ""
                self._backup_at = time.time()
                self._found = (0.0, [])
                try:
                    self._save()
                except Exception as e:
                    log.debug("saving the list of accounts: %s", e)
            self._drop(drop)
            return True

    def backups(self, max_age=30.0):
        """The lists of accounts kept in the channel, the newest of each computer: [{"id", "device",
        "host", "saved", "accounts", "here"}]. For bringing one back on a computer that has none."""
        when, found = self._found
        if time.time() - when < max_age:
            return found
        backend, newest = self.drive.backend, {}
        try:
            for m in backend.pinned(LIST_MARKER):
                try:
                    p = self._unpack(backend.download(m["id"], (m.get("attachments") or [{}])[0].get("url"))[0])
                except Exception as e:
                    log.warning("A saved list of accounts (message %s) can't be read: %s", m["id"], e)
                    continue
                dev, saved = str(p.get("device") or ""), float(p.get("saved") or 0)
                if p["accounts"] and (dev not in newest or saved > newest[dev]["saved"]):
                    newest[dev] = {"id": str(m["id"]), "device": dev, "host": str(p.get("host") or ""), "saved": saved,
                                   "accounts": len(p["accounts"]), "here": dev == (self.cfg.device_id or "")}
        except Exception as e:
            log.debug("looking for saved lists of accounts: %s", e)
            return []
        found = sorted(newest.values(), key=lambda b: -b["saved"])
        self._found = (time.time(), found)
        return found

    def restorable(self):
        """What the owner can bring back here: only a computer without accounts is offered any."""
        with self._lock:
            if self._recs or self._broken:
                return []
        return self.backups()

    def restore(self, mid):
        """Bring back the list of accounts saved in message `mid`: the owner's decision, on a computer
        that has no accounts, when the computer that served them is gone. Returns how many there are."""
        backend = self.drive.backend
        if str(mid) not in {b["id"] for b in self.backups(max_age=0)}:
            raise AccountError("That saved list of accounts isn't there any more.")
        try:
            p = self._unpack(backend.download(str(mid), None)[0])
        except Exception as e:
            raise AccountError(f"The saved list of accounts can't be read: {e}") from None
        recs = {}
        for aid, rec in p["accounts"].items():
            if re.fullmatch(r"[0-9a-f]{16}", str(aid)) and isinstance(rec, dict) and rec.get("check"):
                rec = dict(rec, id=aid)
                cp = (rec.get("checkpoint") or {}).get("mid")
                rec["kept"] = [cp] if cp else []
                recs[aid] = rec
        with self._lock:
            if self._broken:
                raise AccountError("the list of accounts on this computer is damaged")
            if self._recs:
                raise AccountError("This computer already has accounts.")
            self._recs = recs
            self._secrets = {}
            invites = p.get("invites") if isinstance(p.get("invites"), dict) else {}
            self._invites = {**invites, **self._invites}
            self._replaces = str(mid)
            self._save()
            self._backup_wanted = self._backup_soon = True
        log.info("The list of %d account(s) saved by %s was brought back to this computer.", len(recs),
                 p.get("host") or p.get("device") or "another computer")
        self.backup()                        # from now on this computer keeps the copy
        return len(recs)

    # ------------------------------------------------------------ for the owner
    def listing(self):
        with self._lock:
            out = []
            for aid, rec in self._recs.items():
                d = self._open.get(aid)
                usage = rec.get("usage") or {}
                if d is not None:
                    try:
                        usage = d.usage()
                    except Exception:
                        pass
                out.append({"id": aid, "created": rec.get("created"), "seen": rec.get("seen"), "disabled": bool(rec.get("disabled")),
                            "invite": rec.get("invite") or "", "files": usage.get("files", 0), "bytes": usage.get("bytes", 0),
                            "open": d is not None, "ready": bool((self._secrets.get(aid) or {}).get("key"))})
            return sorted(out, key=lambda a: -(a["created"] or 0))

    def count(self):
        return len(self._recs)

    def set_disabled(self, aid, off):
        with self._lock:
            rec = self._recs.get(aid)
            if rec is None:
                return False
            rec["disabled"] = bool(off)
            self._save()
            self._backup_wanted = self._backup_soon = True      # a list brought back later has it switched off too
        if off:
            self._close(aid, force=True)
        log.info("Account %s was switched %s.", aid, "off" if off else "on again")
        return True

    def delete(self, aid):
        """Remove an account for good: its record, its folder here, and everything it stored on Discord
        (handed to the drive's own clean-up, which deletes it in the background)."""
        with self._lock:
            rec = self._recs.get(aid)
            if rec is None:
                return False
            rec["disabled"] = True           # nothing opens it again while it is taken apart
        self._close(aid, force=True)
        mids = set()
        folder = self.folder(aid)
        db = os.path.join(folder, "index.db")
        if os.path.exists(db):
            from .index import Index
            from .uploader import Uploader
            idx = Index(db)
            try:
                mids.update(idx.referenced_mids())
                mids.update(r["message_id"] for r in idx._all("SELECT message_id FROM trash"))
                for k in idx.kv_keys("upload:"):
                    try:
                        mids.update(Uploader.record_mids(json.loads(idx.kv_get(k) or "null")))
                    except (ValueError, TypeError, AttributeError):
                        pass
            finally:
                idx.close()
        with self._lock:
            mids.add((rec.get("checkpoint") or {}).get("mid"))
            mids.update(rec.get("kept") or [])
            self._recs.pop(aid, None)
            self._secrets.pop(aid, None)
            self._save()
            self._backup_wanted = self._backup_soon = True      # a list brought back later doesn't have it any more
        self.drive.index.trash_add([m for m in mids if m])
        shutil.rmtree(folder, ignore_errors=True)
        log.info("Account %s was deleted (%d piece(s) are being removed from Discord).", aid, len(mids))
        return True

    # ------------------------------------------------------------ invites
    def invites(self):
        with self._lock:
            now = time.time()
            return sorted(({"code": code, "created": inv.get("created"), "expires": inv.get("expires"), "uses": inv.get("uses", 1),
                            "used": inv.get("used", 0), "note": inv.get("note") or "", "valid": self._invite_ok(code),
                            "expired": bool(inv.get("expires") and inv["expires"] <= now)}
                           for code, inv in self._invites.items()), key=lambda i: -(i["created"] or 0))

    def invite_new(self, uses=1, days=7, note=""):
        code = secrets.token_urlsafe(9)
        days = max(0.0, float(days or 0))
        with self._lock:
            self._invites[code] = {"created": time.time(), "expires": time.time() + days * 86400 if days else None,
                                   "uses": max(0, int(uses)), "used": 0, "note": str(note or "")[:80]}
            self._save()
            self._backup_wanted = True
        return code

    def invite_revoke(self, code):
        with self._lock:
            if self._invites.pop(str(code or ""), None) is None:
                return False
            self._save()
            self._backup_wanted = True
        return True
