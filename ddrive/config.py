"""Configuration handling.

Windows: config in %APPDATA%\\DDrive\\config.json, data (index, caches) in
%LOCALAPPDATA%\\DDrive.
Linux:   config in ~/.config/DDrive/config.json, data in ~/.local/share/DDrive.

Nothing is ever read from the current directory or the source checkout, so the
bot token and encryption key never end up inside a git repository or synced folder.

The secrets in the file (SECRET_FIELDS) are not stored readable: they are kept together in one
entry, "protected", encrypted for this computer and account (see secretbox.py). A file written by
hand or by an older version, with the secrets in the clear, is accepted; it is rewritten protected
when the drive starts (Config.protect_file), not by other commands: an older version of the drive
that is still running would not understand the new form and could lose the secrets.
"""

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, fields

from . import legacy

APP_NAME = "DDrive"


# A computer set up before the program was renamed keeps its folders where they are (legacy.py).
def default_config_dir() -> str:
    if sys.platform == "win32":
        return legacy.app_dir(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return legacy.app_dir(xdg, APP_NAME)


def default_data_dir() -> str:
    if sys.platform == "win32":
        return legacy.app_dir(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return legacy.app_dir(xdg, APP_NAME)


DEFAULT_CONFIG_PATH = os.path.join(default_config_dir(), "config.json")


def parse_size(text: str) -> int:
    """'1500', '500M', '2G', '1.5T', '2GB' or '2GiB' -> bytes (binary units)."""
    t = text.strip().upper().replace(" ", "")
    for suffix in ("IB", "B"):
        if t.endswith(suffix) and len(t) > len(suffix) and t[-len(suffix) - 1] in "KMGT":
            t = t[:-len(suffix)]
            break
    mult = {"K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}.get(t[-1:], 1)
    if mult > 1:
        t = t[:-1]
    value = float(t)
    if value < 0:
        raise ValueError("must not be negative")
    return int(value * mult)


def format_size(n: int) -> str:
    for unit, mult in (("T", 2**40), ("G", 2**30), ("M", 2**20), ("K", 2**10)):
        if n >= mult:
            return f"{n / mult:.4g}{unit}"
    return f"{n} bytes"


def launcher() -> str:
    """How users run DDrive commands on this system (for messages)."""
    return "run.bat" if sys.platform == "win32" else "./run.sh"


def config_path() -> str:
    """The config file in use (DDRIVE_CONFIG overrides the default location)."""
    return legacy.env("CONFIG") or DEFAULT_CONFIG_PATH

MiB = 1024 * 1024
SECRET_FIELDS = ("bot_token", "encryption_key", "encryption_salt", "old_encryption_keys", "extra_bot_tokens", "spare_bot_tokens",
                 "web_token", "web_password", "discord_user_token", "monitor_key",
                 "recorder_key")
_warned = set()


def _note(text):
    """Tell the person at the terminal (once per run), and the log."""
    if text in _warned:
        return
    _warned.add(text)
    import logging
    logging.getLogger("ddrive.config").warning(text)      # with no log set up yet, this goes to the terminal


@dataclass
class Config:
    bot_token: str = ""
    channel_id: str = ""
    mount_point: str = "Z:"
    volume_name: str = "DDrive"
    # Discord's attachment limit for bots in a non-boosted server is 10 MiB.
    # Raise this if your server is boosted (Level 2: 50 MiB, Level 3: 100 MiB).
    chunk_size: int = 9 * MiB
    data_dir: str = ""                 # default: %LOCALAPPDATA%\DDrive
    cache_max_bytes: int = 5 * 1024 * MiB
    cache_mode: str = "disk"           # "disk": keep viewed chunks on disk | "memory": RAM only, nothing on disk
    memory_cache_bytes: int = 256 * MiB  # RAM used for viewed chunks in memory mode
    upload_delay: float = 2.0          # seconds to wait after a file is closed before uploading
    prefetch_chunks: int = 2           # chunks to read ahead while streaming
    download_threads: int = 4
    upload_threads: int = 3            # chunks/files uploaded in parallel
    staging_max_bytes: int = 10 * 1024 * MiB  # pause new file creation while this much is waiting to upload (0 = no limit)
    min_free_disk_bytes: int = 2 * 1024 * MiB  # also pause while the local disk has less free space than this
    max_file_size: int = 0             # refuse files larger than this (0 = no limit)
    delete_remote: bool = True         # delete Discord messages when files are deleted/overwritten
    delete_after_days: float = 7.0     # a piece nothing uses any more stays on Discord this long before it is deleted
    verify_uploads: bool = False       # download every uploaded piece once and compare it (twice the traffic)
    index_backup_interval: float = 600.0  # seconds between index checkpoints (when something changed)
    poll_interval: float = 2.0         # seconds between checks for changes made on other devices
    keep_versions: bool = True         # keep replaced/deleted file contents as restorable versions
    version_retention_days: float = 30.0  # drop versions older than this (0 = keep forever)
    max_versions: int = 0              # max versions kept per file (0 = no limit)
    device_id: str = ""                # random id of this device (generated on first mount)
    encryption_enabled: bool = True    # Zero-knowledge AES-256-GCM encryption
    encryption_key: str = ""           # 64-char hex string (256-bit key)
    encryption_salt: str = ""          # 32-char hex string
    old_encryption_keys: list = field(default_factory=list)  # earlier keys, kept to read older data
    allow_other: bool = False          # Allow other users/services to access mount on Linux
    extra_bot_tokens: list = field(default_factory=list)  # more bots in the same channel = faster uploads
    spare_channels: list = field(default_factory=list)    # more channels: pieces are spread so any one can be lost
    spare_bot_tokens: list = field(default_factory=list)  # a spare channel's own bot (same order; "" = the main bots)
    lost_channels: list = field(default_factory=list)     # spare channels given up: their pieces are rebuilt elsewhere
    channel_servers: dict = field(default_factory=dict)   # channel id -> server (guild) id, learnt from Discord
    discord_pace: str = "gentle"       # how fast to talk to Discord: gentle | balanced | fast | off
    uploads_per_minute: int = 0        # per bot; 0 = from discord_pace (gentle: 30)
    deletes_per_minute: int = 0        # per bot; 0 = from discord_pace (gentle: 15)
    requests_per_minute: int = 0       # other requests per bot; 0 = from discord_pace (gentle: 90)
    compression: bool = True           # compress pieces that shrink (lossless, before encryption)
    dedup: bool = True                 # store identical pieces once
    parity_enabled: bool = True        # spare pieces, so lost pieces can be rebuilt (self-healing)
    parity_group: int = 10             # data pieces per group...
    parity_pieces: int = 2             # ...and spare pieces per group, 1-50 (10 = one per piece; small files get their share)
    scrub_enabled: bool = True         # check in the background that every piece is still on Discord
    scrub_days: float = 2.0            # how long one full check of every piece takes
    protect_existing: bool = True      # add spare pieces to files uploaded before self-healing existed
    snapshot_interval_hours: float = 24.0  # automatic snapshot of the whole drive (0 = off)
    snapshot_keep_days: float = 14.0   # how long snapshots are kept
    hidden_folders: list = field(default_factory=list)  # folders this device doesn't show
    web_enabled: bool = True           # the web dashboard (http://127.0.0.1:<web_port>)
    web_port: int = 8765
    web_hosts: list = field(default_factory=list)  # extra names the dashboard answers to (reverse proxy domains)
    web_public_url: str = ""           # address share links use, e.g. https://drive.example.com (default: from web_hosts)
    web_lan: bool = False              # also reachable from other devices on your network (phone)
    web_token: str = ""                # secret that signs dashboard sessions and share links (generated)
    web_user: str = ""                 # other apps over WebDAV sign in with this name...
    web_password: str = ""             # ...and this password (a scrypt hash; set with 'webdav-password'). Not for the dashboard.
    web_passkeys: list = field(default_factory=list)  # passkeys that sign the owner in to the dashboard (public keys only)
    webdav_enabled: bool = False       # the drive for other apps at /dav/ (web_user and web_password)
    chat_pace: str = "gentle"          # how fast Discord is asked as your account (Messages): gentle | normal
    thumbs_store: str = "server"       # where the dashboard keeps the thumbnails it makes: server | browser (each visitor's own)
    thumbs_bulk: bool = True           # the gallery's "Create all thumbnails" (off: they are made as pictures are looked at)
    signup_mode: str = "closed"        # who may make an account on the dashboard: closed | invite | public
    account_quota_bytes: int = 5 * 1024 * MiB  # what one account may store, earlier versions included (0 = no limit)
    accounts_max: int = 100            # no more accounts than this are made (0 = no limit)
    operator_name: str = ""            # who runs this dashboard, as named in its terms and privacy policy (/terms, /privacy)
    operator_contact: str = ""         # how to reach them (an email address, usually), shown there
    legal_region: str = ""             # whose law the terms are under, e.g. "England and Wales" (empty: not said)
    fetch_private: bool = False        # "save from a link" may also fetch from this computer and the home network
    lock_timeout_minutes: float = 15.0  # password-locked folders lock again after this long (0 = until the drive restarts)
    update_check: bool = True          # ask GitHub twice a day whether a newer DDrive is out (updates.py; nothing is sent)
    sync_jobs: list = field(default_factory=list)  # folders kept in sync with the drive ('sync add', dashboard Sync)
    sync_remote_edit: bool = False     # let the dashboard on other devices add or change sync folders
    discord_user_token: str = ""       # your own Discord account, for the message logger and archiver (dashboard: Settings)
    protect_config: bool = True        # keep the secrets in this file encrypted for this computer and account
    monitor_key: str = ""              # lets a status bot read /api/monitor (numbers only, no names); 'monitor-key' makes one
    recorder_key: str = ""             # lets a program stream files into the drive as it makes them (/api/rec, nothing else); dashboard: Settings
    recorder_folder: str = "/Recordings"   # where files streamed in with the recorder key go

    protect_error = None               # why the protected secrets could not be read (not saved)
    _unreadable = None

    @property
    def resolved_data_dir(self) -> str:
        return self.data_dir or default_data_dir()

    @classmethod
    def load(cls, path: str = None) -> "Config":
        path = path or config_path()
        cfg = cls()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as e:
                try:
                    # Forgive a comma after the last entry, the most common hand-editing slip.
                    raw = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
                except json.JSONDecodeError:
                    raise SystemExit(
                        f"[ERROR] The config file {path} is not valid JSON (line {e.lineno}, column {e.colno}: "
                        f"{e.msg}).\n        Usually a missing comma between two entries."
                    ) from None
            known = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in known:
                    setattr(cfg, k, v)
            in_clear = [k for k in SECRET_FIELDS if raw.get(k)]
            if raw.get("protected"):
                from . import secretbox
                try:
                    secrets_ = json.loads(secretbox.unprotect(raw["protected"]))
                    for k in SECRET_FIELDS:
                        if k in secrets_ and k not in in_clear:      # something typed into the file by hand wins
                            setattr(cfg, k, secrets_[k])
                except (secretbox.ProtectError, ValueError) as e:
                    # Keep what we can't read (it may be readable again as the right user) and say so.
                    cfg._unreadable = raw["protected"]
                    cfg.protect_error = (
                        f"The secrets in {path} (bot token, encryption key) can't be read here: {str(e).rstrip('.')}. They are "
                        "encrypted for the computer and account that wrote the file. On that account they work; "
                        f"anywhere else run '{launcher()} setup' and enter the bot token and your encryption "
                        "password (or the key from 'export-key').")
                    _note(cfg.protect_error)
        # Environment overrides (handy for scripts / not storing the token on disk).
        cfg.bot_token = legacy.env("TOKEN", cfg.bot_token)
        cfg.channel_id = legacy.env("CHANNEL", cfg.channel_id)
        if cfg.volume_name == legacy.OLD:        # the label every drive had before the rename
            cfg.volume_name = APP_NAME
        cfg.mount_point = normalize_mount_point(cfg.mount_point)
        return cfg

    @staticmethod
    def _in_clear(path, partly=False) -> bool:
        """True when the file at `path` holds its secrets readable (written by hand or an older version).
        partly: also when only some were typed into an otherwise protected file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return False
        return isinstance(raw, dict) and (partly or not raw.get("protected")) and any(raw.get(k) for k in SECRET_FIELDS)

    def protection(self, path: str = None) -> str:
        """How the secrets of the saved file are kept, in words."""
        from . import secretbox
        if not self.protect_config:
            return "not encrypted (protect_config is off)"
        if self._in_clear(path or config_path()):
            return "not encrypted yet (they will be when the drive is next started)"
        return secretbox.HOW[secretbox.METHOD]

    @classmethod
    def protect_file(cls, path: str = None) -> bool:
        """Rewrite a file that holds its secrets readable so that they are encrypted (when the drive starts)."""
        path = path or config_path()
        if not cls._in_clear(path, partly=True):
            return False
        cfg = cls.load(path)
        if not cfg.protect_config or not cfg.save(path, protect=True):
            return False
        _note(f"The secrets in {path} (bot tokens, encryption keys) are now {cfg.protection(path)}; a copy of "
              "the file no longer contains them. Keep your encryption password (or the key shown by "
              f"'{launcher()} export-key') somewhere safe outside this computer.")
        return True

    def save(self, path: str = None, protect: bool = None) -> bool:
        """Write the file. Returns True when its secrets were stored encrypted.

        A file that still has readable secrets keeps that form unless `protect` is true (see the
        note at the top about older versions); new files and protected ones are written protected."""
        path = path or config_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = asdict(self)
        protected = False
        secrets_ = {k: data[k] for k in SECRET_FIELDS if data[k]}
        if protect is None:
            protect = not self._in_clear(path)
        if self.protect_config and protect and secrets_:
            from . import secretbox
            try:
                data["protected"] = secretbox.protect(json.dumps(secrets_, separators=(",", ":")).encode("utf-8"))
                for k in secrets_:
                    data[k] = [] if isinstance(data[k], list) else ""
                protected = True
            except secretbox.ProtectError as e:
                _note(f"The secrets in the config file could not be encrypted ({e}); they are stored readable.")
        elif not secrets_ and getattr(self, "_unreadable", None):
            data["protected"] = self._unreadable          # written elsewhere and unreadable here: don't destroy it
        tmp = f"{path}.{os.getpid()}.tmp"
        # The file holds the bot token and encryption key: keep it private on POSIX.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        if sys.platform != "win32":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return protected

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.channel_id)


def normalize_mount_point(mp: str) -> str:
    mp = (mp or "").strip()
    if sys.platform == "win32":
        if len(mp) == 1 and mp.isalpha():
            return mp.upper() + ":"
        if len(mp) in (2, 3) and mp[0].isalpha() and mp[1] == ":":
            return mp[0].upper() + ":"
        return mp or "Z:"
    else:
        if not mp or (len(mp) <= 2 and mp.endswith(":")):
            return os.path.expanduser("~/ddrive")
        return os.path.abspath(os.path.expanduser(mp))
