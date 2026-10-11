"""DiscordDrive runtime coordinator and mount manager."""

import errno
import logging
import os
import secrets
import shutil
import signal
import sys
import threading
import time

from .backend import DiscordBackend, LocalBackend, is_encrypted_index
from .cache import ChunkCache
from .config import Config, launcher
from .crypto import KeyRing, generate_key
from .discord_api import DiscordAPI
from .fs import DiscordDriveFS
from .fuse_loader import find_winfsp_dll, fuse, unmount, FUSE_ERROR
from .heal import Healer, Maintenance
from .index import Index
from .journal import Journal
from .uploader import Uploader

log = logging.getLogger("discorddrive.drive")


class DiscordDrive:
    def __init__(self, cfg: Config = None, local_test_dir: str = None):
        self.cfg = cfg or Config.load()
        self.local_test_dir = local_test_dir

        self.crypto = None
        self.api = None
        self.backend = None
        self.index = None
        self.cache = None
        self.fs = None
        self.uploader = None
        self.journal = None
        self.healer = None
        self.maintenance = None
        self.web = None
        self.sync = None
        self.chat = None
        self._stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False

    def initialize(self):
        data_dir = self.cfg.resolved_data_dir
        staging_dir = os.path.join(data_dir, "staging")
        cache_dir = os.path.join(data_dir, "cache")
        db_path = os.path.join(data_dir, "index.db")

        os.makedirs(staging_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)

        if self.local_test_dir:
            log.info("Running in mock mode using local directory: %s", self.local_test_dir)
            self.backend = LocalBackend(self.local_test_dir)
        else:
            if getattr(self.cfg, "protect_error", None):
                raise ValueError(self.cfg.protect_error)
            if not self.cfg.is_configured():
                raise ValueError(
                    f"DiscordDrive is not configured yet. Run '{launcher()} setup' or provide bot_token and channel_id."
                )
            self.api = DiscordAPI(self.cfg.bot_token).configure(self.cfg)
            extra = [DiscordAPI(t).configure(self.cfg) for t in (self.cfg.extra_bot_tokens or [])
                     if t and t != self.cfg.bot_token]
            self.backend = DiscordBackend(self.api, self.cfg.channel_id, extra_apis=extra)
            if extra:
                log.info("Uploading with %d bots.", 1 + len(extra))

        if self.cfg.encryption_enabled:
            if not self.cfg.encryption_key:
                self._generate_key_if_safe()
            # Never fall back to uploading plaintext because of a bad key.
            try:
                self.crypto = KeyRing.from_hex(self.cfg.encryption_key, self.cfg.old_encryption_keys)
            except Exception as e:
                raise RuntimeError(f"Encryption is enabled but the key could not be loaded: {e}") from e
            self.backend.crypto = self.crypto
            older = len(self.crypto.keys()) - 1
            log.info("Zero-knowledge AES-256-GCM encryption ACTIVE%s.",
                     f" (plus {older} older key(s) for reading earlier data)" if older else "")

        if not self.cfg.device_id:
            self.cfg.device_id = secrets.token_hex(4)
            if not self.local_test_dir:
                # Persist only the id, not one-off command-line overrides such as --cache.
                saved = Config.load()
                saved.device_id = self.cfg.device_id
                saved.save()

        if not self.local_test_dir:
            try:
                Config.protect_file()      # secrets written readable by an older version: encrypt them now
            except Exception as e:
                log.warning("Could not encrypt the secrets in the config file: %s", e)

        from . import logbuf
        from .config import default_data_dir
        logbuf.install(os.path.join(default_data_dir(), "discorddrive.log") if not self.local_test_dir else None)
        self.index = Index(db_path)
        self.index.keep_versions = bool(self.cfg.keep_versions)
        self.journal = Journal(self.cfg, self.index, self.backend, crypto=self.crypto, staging_dir=staging_dir)
        self.journal.bootstrap()

        self.cache = ChunkCache(
            cache_dir,
            max_bytes=self.cfg.cache_max_bytes,
            backend=self.backend,
            index=self.index,
            threads=self.cfg.download_threads,
            crypto=self.crypto,
            memory_bytes=self._memory_cache_bytes(),
            min_free=self.cfg.min_free_disk_bytes,
        )
        if self.cache.memory_mode:
            freed = self.cache.free_space()
            log.info("Memory-only cache (%d MiB of RAM): viewed files are not stored on disk%s.",
                     self.cache.memory_bytes // 2**20,
                     f"; removed {freed / 2**20:.1f} MiB of previously cached data" if freed else "")

        self.fs = DiscordDriveFS(self.cfg, self.index, self.cache, staging_dir)
        from .locks import Locks
        self.locks = self.fs.locks = Locks(self.index, self.cfg)
        self.locks.mount_relock()          # a restart locks everything again
        self.uploader = Uploader(self.cfg, self.index, self.backend, self.fs, crypto=self.crypto)
        self.fs.uploader = self.uploader
        self.uploader.journal = self.journal
        self.journal.fs = self.fs
        self.healer = Healer(self.cfg, self.index, self.backend, crypto=self.crypto, cache=self.cache,
                             wake=self.journal.wake)
        self.cache.healer = self.healer
        from .heal import Checker
        self.checker = Checker(self.index, self.backend, self.crypto, self.healer)
        self.maintenance = Maintenance(self.cfg, self.index, self.backend, self.healer, uploader=self.uploader,
                                       journal=self.journal, cache=self.cache, device_id=self.cfg.device_id)

        from .sync import SyncManager
        self.sync = SyncManager(self)
        from .chatlog import Chat
        self.chat = Chat(self)             # Discord message logger and archiver (does nothing until you log in)

        # Recover any pending staging files from previous runs
        self.fs.recover()

    def _memory_cache_bytes(self):
        mode = (self.cfg.cache_mode or "disk").lower()
        if mode not in ("disk", "memory"):
            raise ValueError(f"cache_mode must be 'disk' or 'memory', not {self.cfg.cache_mode!r}")
        if mode == "disk":
            return 0
        # Room for the chunk being read plus read-ahead, whatever the setting says.
        return max(int(self.cfg.memory_cache_bytes or 0), (self.cfg.prefetch_chunks + 2) * self.cfg.chunk_size)

    def _generate_key_if_safe(self):
        """Auto-generate a key for a brand-new drive, but never for a channel that already holds encrypted data."""
        try:
            latest = self.backend.find_latest_index()
        except Exception as e:
            raise RuntimeError(
                f"No encryption key is configured and Discord could not be checked for existing data ({e}). "
                f"Run '{launcher()} setup' to configure a key."
            ) from e
        if latest is not None and is_encrypted_index(latest):
            raise RuntimeError(
                "This channel already contains an encrypted DiscordDrive, but no encryption key is configured. "
                "Copy 'encryption_key' from the config of the machine that created it, or run "
                f"'{launcher()} setup -p <passphrase>' with the same passphrase."
            )
        self.cfg.encryption_key = generate_key().hex()
        self.cfg.save()
        log.info("Auto-generated and saved a new 256-bit AES master encryption key. Back it up!")

    def start_background_services(self):
        if self.uploader:
            self.uploader.start()
        if self.journal:
            self.journal.start()
            threading.Thread(target=self._catch_up, name="JournalCatchUp", daemon=True).start()
        if self.healer:
            self.healer.start()
        if self.maintenance:
            self.maintenance.start()
        if self.sync:
            self.sync.start()
        if self.chat and not self.local_test_dir:
            self.chat.start()
        if getattr(self.cfg, "web_enabled", False) and not self.local_test_dir:
            self.start_web()

    def start_web(self, port=None):
        from .web import WebServer
        if not self.cfg.web_token:
            self.cfg.web_token = secrets.token_urlsafe(24)
            if not self.local_test_dir:
                saved = Config.load()
                saved.web_token = self.cfg.web_token
                saved.save()
        try:
            self.web = WebServer(self, port=self.cfg.web_port if port is None else port).start()
            log.info("Web dashboard: %s", self.web.local_url())
        except OSError as e:
            log.warning("Web dashboard not started (port %s: %s)", self.cfg.web_port, e)
        return self.web

    def _catch_up(self):
        try:
            self.journal.catch_up(stop=self._stop_event.is_set)
        except Exception as e:
            log.debug("Journal catch-up failed (retried on the next start): %s", e)

    def stop_background_services(self):
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        self._stop_event.set()
        if self.web:
            self.web.stop()
        if self.chat:
            self.chat.stop()               # writes what the logger still holds to the drive
        if self.sync:
            self.sync.stop()
        if self.maintenance:
            self.maintenance.stop()
        if self.uploader:
            self.uploader.stop()
        if self.healer:
            self.healer.stop()
        if self.journal:
            self.journal.stop()  # publishes the last changes and saves a checkpoint
        if self.cache:
            self.cache.shutdown()
        if self.index:
            self.index.close()
        if self.crypto:
            self.crypto.close()

    def mount(self, mount_point: str = None, foreground: bool = True):
        if FUSE_ERROR:
            raise RuntimeError(FUSE_ERROR)
        if sys.platform == "win32":
            dll = find_winfsp_dll()
            if not dll:
                raise RuntimeError(
                    "WinFsp not found. Please install WinFsp from https://winfsp.dev/rel/ to mount virtual drives."
                )

        mp = mount_point or self.cfg.mount_point
        volname = self.cfg.volume_name or "DiscordDrive"

        if sys.platform != "win32":
            strays = _prepare_mount_dir(mp, self.cfg.resolved_data_dir)
            if strays:
                threading.Thread(target=_import_strays, args=(strays, mp), name="ImportStrays",
                                 daemon=True).start()

        self.initialize()
        self.start_background_services()

        log.info("Mounting DiscordDrive on %s...", mp)

        # Handle graceful shutdown
        def _sig_handler(sig, frame):
            log.info("Received signal %s; shutting down...", sig)
            self._stop_event.set()
            self.stop_background_services()
            sys.exit(0)

        try:
            if sys.platform == "win32":
                signal.signal(signal.SIGINT, _sig_handler)
                signal.signal(signal.SIGTERM, _sig_handler)
            else:
                # The main thread sits inside libfuse's C loop, where Python handlers never
                # run. With default dispositions libfuse installs its own SIGINT/SIGTERM/SIGHUP
                # handlers, which unmount and return from fuse_main -> cleanup in `finally`.
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
        except (ValueError, AttributeError):
            pass

        pid_file = os.path.join(self.cfg.resolved_data_dir, "discorddrive.pid")
        try:
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        fuse_kwargs = {
            "foreground": foreground,
            "fsname": "DiscordDrive",
        }
        if sys.platform == "win32":
            # The files belong to whoever uses the drive. Without this Windows sees them as somebody
            # else's and lets programs create files but not change one that exists (append, edit in place).
            fuse_kwargs.update(uid=-1, gid=-1)
        if sys.platform in ("win32", "darwin"):
            fuse_kwargs["volname"] = volname
        else:
            allow_other = getattr(self.cfg, "allow_other", False)
            if not allow_other:
                try:
                    if hasattr(os, "getuid") and os.getuid() == 0:
                        allow_other = True
                    elif os.path.exists("/etc/fuse.conf"):
                        with open("/etc/fuse.conf", "r") as f:
                            for line in f:
                                if line.strip().startswith("user_allow_other"):
                                    allow_other = True
                                    break
                except Exception:
                    pass
            if allow_other:
                fuse_kwargs["allow_other"] = True

        try:
            fuse.FUSE(
                self.fs,
                mp,
                **fuse_kwargs,
            )
        finally:
            log.info("Unmounting DiscordDrive from %s...", mp)
            try:
                if os.path.exists(pid_file):
                    os.remove(pid_file)
            except Exception:
                pass
            self.stop_background_services()


def _prepare_mount_dir(mp, data_dir):
    """Create the mount directory and clear a stale FUSE mount left by a crash. Files found in
    the (unmounted) folder were written there while the drive was down: they are moved to a
    holding folder (returned) and copied onto the drive once it is mounted."""
    try:
        os.makedirs(mp, exist_ok=True)
        entries = os.listdir(mp)
    except OSError as e:
        if e.errno != errno.ENOTCONN:
            raise
        log.warning("Clearing stale mount on %s (previous instance did not exit cleanly)", mp)
        unmount(mp, lazy=True)
        entries = os.listdir(mp)
    if os.path.ismount(mp):
        raise RuntimeError(f"Something is already mounted on {mp}. Run '{launcher()} stop' first.")
    if not entries:
        return None
    holding = os.path.join(data_dir, "left-in-mount-folder-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(holding, exist_ok=True)
    for name in entries:
        shutil.move(os.path.join(mp, name), os.path.join(holding, name))
    log.warning("Found %d item(s) in %s while the drive was unmounted (written there while it was down); "
                "they will be copied onto the drive. Kept meanwhile in %s", len(entries), mp, holding)
    return holding


def _import_strays(holding, mp, wait=120):
    """Copy files that were left in the unmounted mount folder onto the mounted drive."""
    end = time.time() + wait
    while time.time() < end and not os.path.ismount(mp):
        time.sleep(1)
    if not os.path.ismount(mp):
        log.warning("Drive did not mount; files that were left in the mount folder stay in %s", holding)
        return
    copied = skipped = 0
    try:
        for dirpath, dirnames, filenames in os.walk(holding):
            rel = os.path.relpath(dirpath, holding)
            target_dir = mp if rel == "." else os.path.join(mp, rel)
            os.makedirs(target_dir, exist_ok=True)
            for fn in filenames:
                src, dst = os.path.join(dirpath, fn), os.path.join(target_dir, fn)
                if os.path.exists(dst):
                    if os.path.getsize(dst) == os.path.getsize(src):
                        skipped += 1
                        continue
                    stem, ext = os.path.splitext(fn)
                    dst = os.path.join(target_dir, f"{stem} (from mount folder){ext}")
                shutil.copy2(src, dst)
                copied += 1
        shutil.rmtree(holding, ignore_errors=True)
        log.info("Copied %d file(s) that were left in the mount folder onto the drive (%d already there).",
                 copied, skipped)
    except Exception as e:
        log.warning("Could not copy all files left in the mount folder (%s); the rest are in %s", e, holding)
