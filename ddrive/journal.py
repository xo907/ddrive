"""Real-time multi-device sync through a change journal kept in the Discord channel.

Every metadata change (file uploaded/replaced, folder created, move, delete,
mtime change) is recorded in the local outbox and posted as a small, encrypted
"operation" message (`DDOP1 ...`). Every device polls the channel every few
seconds and applies all operations in message order. Discord assigns message
ids in one global order, so all devices apply the same operations in the same
order and converge on the same tree, including this device's own operations,
which are applied again idempotently when they are read back.

Index checkpoints (snapshots pinned in the channel) record how far along the
journal they are, so a new device restores the latest checkpoint and replays only
the operations after it.
"""

import base64
import gzip
import json
import logging
import os
import posixpath
import secrets
import socket
import threading
import time

from .logbuf import tname
from .index import conflict_name

log = logging.getLogger("ddrive.journal")

OP_PREFIX = "DDOP1 "
OP_INLINE_MAX = 1900       # Discord message content limit is 2000 characters
GC_INTERVAL = 3600.0
HEARTBEAT = 12 * 3600.0    # a device that posted nothing for this long says it is still around
CATCH_UP_DAYS = 14         # after an upgrade, re-read this much history for what older versions skipped
FEATURES = "6"             # kv "features": this index has applied the journal with the current op set


def _hostname():
    try:
        return socket.gethostname()[:40]
    except Exception:
        return ""


class Journal:
    def __init__(self, cfg, index, backend, crypto=None, staging_dir=None):
        self.cfg = cfg
        self.index = index
        self.backend = backend
        self.crypto = crypto
        self.staging_dir = staging_dir
        self.fs = None
        self.device = getattr(cfg, "device_id", "") or "unknown"
        index.device = self.device

        self._sync_lock = threading.Lock()   # one flush/poll/checkpoint at a time
        self._wake = threading.Event()
        self._running = False
        self._thread = None
        self._last_posted = 0
        self._last_checkpoint_counter = None
        self._last_checkpoint_time = time.time()
        self._last_gc = 0.0
        self._failures = 0
        self._needs_rebuild = False

    # ------------------------------------------------------------ encoding
    def _encode(self, ops) -> bytes:
        raw = gzip.compress(json.dumps({"v": 1, "d": self.device, "h": _hostname(), "ops": ops},
                                       separators=(",", ":")).encode("utf-8"), 6)
        return self.crypto.encrypt(raw) if self.crypto else raw

    def _decode_env(self, raw: bytes):
        if raw.startswith(b"DENC"):
            if not self.crypto:
                raise ValueError("journal entry is encrypted but no encryption key is configured")
            raw = self.crypto.decrypt(raw)
        env = json.loads(gzip.decompress(raw))
        ops = env.get("ops")
        if not isinstance(ops, list):
            raise ValueError("malformed journal entry")
        dev = str(env.get("d") or "")
        for op in ops:
            if isinstance(op, dict) and op.get("t") == "snap":
                op["_d"] = dev
        return env

    def _decode(self, raw: bytes):
        return self._decode_env(raw)["ops"]

    def _read_entry(self, m):
        """(envelope, ops) of a journal message, or (None, []) if it can't be read."""
        payload = (m.get("content") or "")[len(OP_PREFIX):].strip()
        # Network errors propagate (retried on the next poll); bad data is skipped.
        raw = self.backend.fetch_attachment(m) if payload == "@" else None
        try:
            env = self._decode_env(raw if raw is not None else base64.b64decode(payload))
            return env, env["ops"]
        except Exception as e:
            log.error("Skipping unreadable journal entry %s: %s", m["id"], e)
            return None, []

    def post_ops(self, ops) -> str:
        """Publish operations directly (used for the outbox and by CLI commands)."""
        raw = self._encode(ops)
        text = base64.b64encode(raw).decode("ascii")
        if len(OP_PREFIX) + len(text) <= OP_INLINE_MAX:
            mid = self.backend.post_text(OP_PREFIX + text)
        else:
            mid = self.backend.upload(f"ops_{secrets.token_hex(6)}.bin", raw, content=OP_PREFIX + "@").message_id
        self._last_posted = max(self._last_posted, int(mid))
        try:
            self.index.kv_set("last_post", str(time.time()))
        except Exception:
            pass
        return mid

    # ------------------------------------------------------- flush & poll
    def flush(self) -> int:
        """Post queued local operations. Returns how many were posted."""
        posted = 0
        while True:
            batch = self.index.outbox_peek(200)
            if not batch:
                return posted
            self.post_ops([op for _, op in batch])
            # A crash right here posts the batch twice on restart; applying is idempotent.
            self.index.outbox_remove([i for i, _ in batch])
            posted += len(batch)

    def poll(self) -> int:
        """Apply new operations from the channel. Returns the number of changed nodes."""
        cursor = self.index.kv_get("journal_cursor") or "0"
        changed_total = 0
        while True:
            msgs = self.backend.messages_after(cursor)
            if not msgs:
                break
            seen = {}
            for m in msgs:
                content = m.get("content") or ""
                if content.startswith(OP_PREFIX):
                    env, ops = self._read_entry(m)
                    if env is not None and env.get("d"):
                        seen[str(env["d"])] = (self._time_of(m["id"]), str(env.get("h") or ""))
                    changed_total += self._apply(ops, m["id"], str(env.get("d") or "") if env else "")
                cursor = m["id"]
            self.index.kv_set("journal_cursor", cursor)
            self.index.note_devices(seen)
            if len(msgs) < 100:
                break
        return changed_total

    def _time_of(self, mid):
        try:
            return self.backend.time_of(mid)
        except Exception:
            return time.time()

    def catch_up(self, days=CATCH_UP_DAYS, stop=None):
        """After upgrading from a version without spare pieces / repairs / snapshots, re-read the
        recent journal and apply just those parts (everything else was applied the first time)."""
        if self.index.kv_get("features") == FEATURES:
            return 0
        end = int(self.index.kv_get("journal_cursor") or "0")
        cursor = self.backend.id_for_time(time.time() - days * 86400)
        applied = 0
        log.info("Reading the last %d days of the change journal for spare pieces and repairs...", days)
        while int(cursor) < end:
            if stop is not None and stop():
                return applied
            msgs = self.backend.messages_after(cursor)
            if not msgs:
                break
            for m in msgs:
                if int(m["id"]) > end:
                    msgs = []          # past where the normal sync had got to: done
                    break
                if (m.get("content") or "").startswith(OP_PREFIX):
                    _, ops = self._read_entry(m)
                    if any(isinstance(o, dict) and (o.get("t") in self.index.EXTRA_OPS or o.get("x")) for o in ops):
                        fs = self.fs
                        if fs is not None:
                            with fs.lock:
                                fs.on_remote_change(self.index.apply_ops(ops, None, extras_only=True))
                        else:
                            self.index.apply_ops(ops, None, extras_only=True)
                        applied += 1
                cursor = m["id"]
            if len(msgs) < 100:
                break
        self.index.kv_set("features", FEATURES)
        log.info("Journal catch-up done (%d entries had something new).", applied)
        return applied

    def create_snapshot(self, label="", keep_days=14.0):
        """Record the whole drive as it is now (posted in parts; every device applies them)."""
        with self._sync_lock:
            self.flush()
            self.poll()
            ops = self.index.snapshot_ops(label, keep_days)
            for op in ops:
                self.post_ops([op])
            self.index.kv_set("snapshot_posted", str(time.time()))
            self.poll()
        files = sum(1 for op in ops for e in op["e"] if not e[1])
        log.info("Snapshot taken: %d file(s)%s.", files, f" ({label})" if label else "")
        return ops[0]["i"]

    def _apply(self, ops, mid, device="") -> int:
        fs = self.fs
        if fs is None:
            return len(self.index.apply_ops(ops, mid, device=device))
        with fs.lock:  # keep FUSE operations from interleaving with the batch
            changed = self.index.apply_ops(ops, mid, busy=fs.is_busy, device=device)
            fs.on_remote_change(changed)
        return len(changed)

    # ---------------------------------------------------------- checkpoint
    def checkpoint(self, force=False) -> bool:
        """Upload an index snapshot if everything local has been published and read back."""
        if self.index.outbox_count():
            return False
        cursor = self.index.kv_get("journal_cursor") or "0"
        if self._last_posted and int(cursor) < self._last_posted:
            return False
        counter = self.index.change_counter
        if not force and counter == self._last_checkpoint_counter:
            return False
        self._store_keyring()
        data = self.index.snapshot_bytes()
        prev = self.index.kv_get("last_backup_mid")
        mid = self.backend.save_index(data, previous_message_id=prev, cursor=cursor)
        self.index.kv_set("last_backup_mid", mid)
        self._last_checkpoint_counter = counter
        self._last_checkpoint_time = time.time()
        log.info("Saved index checkpoint to Discord (message %s, %d bytes, journal position %s)",
                 mid, len(data), cursor)
        return True

    def _index_empty(self):
        st = self.index.stats(max_age=0)
        return st["files"] == 0 and st["dirs"] == 0

    def _rebuild_empty_index(self):
        """The local file list is empty although Discord holds the drive: restore it automatically."""
        try:
            latest = self.backend.find_latest_index()
            if latest is None:
                return
            log.info("This device's file list is empty; rebuilding it from Discord...")
            if self.fs is not None:
                with self.fs.lock:
                    self.restore(latest)
                    self.fs._chunk_maps.clear()
            else:
                self.restore(latest)
            with self._sync_lock:
                self.poll()
            self._needs_rebuild = False
        except Exception as e:
            log.warning("Could not rebuild the file list yet (%s); will retry.", e)

    def sync_once(self):
        if self._needs_rebuild and self._index_empty():
            self._rebuild_empty_index()
        with self._sync_lock:
            self.flush()
            changed = self.poll()
        if changed:
            log.info("Applied %d change(s) from other devices", changed)
        now = time.time()
        if now - float(self.index.kv_get("last_post") or 0) >= HEARTBEAT:
            with self._sync_lock:
                self.post_ops([{"t": "hb"}])
        if now - self._last_checkpoint_time >= max(60.0, float(self.cfg.index_backup_interval or 600)):
            with self._sync_lock:
                self.checkpoint()
        if now - self._last_gc >= GC_INTERVAL:
            self._last_gc = now
            n = self.index.gc_versions(float(self.cfg.version_retention_days or 0), int(self.cfg.max_versions or 0))
            if n:
                log.info("Expired %d old file version(s)", n)

    # ------------------------------------------------------------- thread
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=tname("JournalSync"), daemon=True)
        self._thread.start()

    def wake(self):
        self._wake.set()

    def stop(self, timeout=30.0):
        if not self._running:
            return
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        # Publish whatever is left and leave a fresh checkpoint, but don't hang
        # for minutes on retries if Discord is unreachable.
        api = getattr(self.backend, "api", None)
        if api is not None:
            api.retries = 2
        try:
            with self._sync_lock:
                self.flush()
                self.poll()
                self.checkpoint()
        except Exception as e:
            log.warning("Final sync with Discord failed (%s); queued changes are sent on the next start.", e)

    def _loop(self):
        interval = max(0.5, float(getattr(self.cfg, "poll_interval", 2.0) or 2.0))
        while self._running:
            delay = interval if not self._failures else min(60.0, interval * 2 ** self._failures)
            self._wake.wait(delay)
            self._wake.clear()
            if not self._running:
                break
            try:
                self.sync_once()
                if self._failures:
                    log.info("Connection to Discord restored.")
                self._failures = 0
            except Exception as e:
                self._failures = min(self._failures + 1, 6)
                if self._failures == 1:
                    log.warning("Sync with Discord failed (%s); retrying in the background.", e)
                else:
                    log.debug("Sync with Discord failed again: %s", e)

    # ------------------------------------------------------------ bootstrap
    def bootstrap(self):
        """Bring the local index up to date before mounting."""
        if self.index.kv_get("journal_cursor"):
            # Right after a reboot the network may not be up yet: keep trying for about a minute.
            online = False
            for attempt in range(12):
                try:
                    with self._sync_lock:
                        self.flush()
                        n = self.poll()
                    if n:
                        log.info("Caught up with %d change(s) made on other devices.", n)
                    online = True
                    break
                except Exception as e:
                    if attempt == 0:
                        log.info("Waiting for the network to reach Discord (%s)...", e)
                    time.sleep(5)
            if not online:
                log.warning("Could not reach Discord to catch up; starting with the local file list.")
            if self._index_empty():
                if online:
                    self._rebuild_empty_index()
                else:
                    self._needs_rebuild = True   # done as soon as Discord is reachable
            return

        # First start with the journal: a fresh install, or an upgrade from an older version.
        try:
            latest = self.backend.find_latest_index()
        except Exception as e:
            raise RuntimeError(f"Could not reach Discord to set up the drive: {e}") from e

        stats = self.index.stats(max_age=0)
        local_empty = stats["files"] == 0 and stats["dirs"] == 0

        if latest is not None and latest.get("cursor"):
            # Another device already runs the journal: adopt its state, keep anything only we had.
            self.restore(latest, merge_local=not local_empty)
            with self._sync_lock:
                self.flush()
                self.poll()
            return

        if latest is not None:
            # Only old-style backups exist. Unless it is our own latest backup, combine it with ours.
            if local_empty or self.index.kv_get("last_backup_mid") != latest["id"]:
                self.restore(latest, merge_local=not local_empty)
        elif local_empty:
            log.info("No existing drive found in the channel. Starting with a fresh drive.")

        cursor = self.backend.latest_message_id() or "0"
        self.index.kv_set("journal_cursor", cursor)
        log.info("Change journal started at message %s.", cursor)
        with self._sync_lock:
            self.flush()
            self.poll()
            self.checkpoint(force=True)

    def restore(self, msg, merge_local=False):
        """Replace the local index with checkpoint `msg`, keeping un-uploaded local files.

        With `merge_local`, files and folders that only the local index knows about
        (e.g. two devices upgrading from versions without the journal) are published
        again instead of being dropped; their already-uploaded data is reused.
        """
        try:
            gz = self.backend.load_index_message(msg)
        except Exception as e:
            raise RuntimeError(
                f"Found index checkpoint {msg['id']} on Discord but could not restore it: {e}. "
                "If the drive is encrypted, check that this device uses the same encryption key."
            ) from e
        pending = self._stash_pending()
        local = self._collect_local() if merge_local else ([], [])
        self.index.restore_from_snapshot(gz)
        self.index.kv_set("last_backup_mid", msg["id"])
        if msg.get("cursor"):
            self.index.kv_set("journal_cursor", msg["cursor"])
        self._reattach(pending)
        self._merge_local(*local)
        self._learn_keys()
        s = self.index.stats(max_age=0)
        log.info("Restored index checkpoint %s: %d files, %d folders.", msg["id"], s["files"], s["dirs"])

    def _collect_local(self):
        idx = self.index
        dirs, files = [], []
        for row in idx._all("SELECT * FROM nodes WHERE id != 1 AND state='synced' ORDER BY id"):
            if row["uid"] == "recovered":
                continue
            path = idx.path_of(row["id"])
            if row["is_dir"]:
                dirs.append(path)
                continue
            chunks = [(c["message_id"], c["size"], c["sha256"]) for c in idx.get_chunks(row["id"])]
            files.append({"uid": row["uid"], "path": path, "mtime": row["mtime"], "size": row["size"],
                          "chunks": chunks})
        return dirs, files

    def _merge_local(self, dirs, files):
        idx = self.index
        added = kept = 0
        for path in dirs:
            if idx.resolve(path) is None:
                idx.makedirs(path)
                added += 1
        for f in files:
            if idx.is_tombstoned(f["uid"]):
                continue   # deleted on another device: don't bring it back
            node = idx.get_by_uid(f["uid"]) or idx.resolve(f["path"])
            local_mids = [c[0] for c in f["chunks"]]
            if node is not None and node["is_dir"]:
                node = None
                f["path"] = conflict_name(f["path"], f["uid"])
            if node is None:
                parent_path, name = posixpath.split(f["path"])
                parent = idx.makedirs(parent_path)
                uid = f["uid"] if idx.get_by_uid(f["uid"]) is None else None
                node = idx.create_node(parent["id"], name, False, state="pending", uid=uid)
            elif [c["message_id"] for c in idx.get_chunks(node["id"])] == local_mids:
                continue
            elif f["mtime"] <= node["mtime"]:
                idx.keep_as_version(node["id"], f["chunks"], "conflict", size=f["size"], mtime=f["mtime"])
                kept += 1
                continue
            # Publish the local content (the remote one becomes a version).
            idx.update(node["id"], mtime=f["mtime"])
            node = idx.get(node["id"])
            chunk_dicts = [{"idx": i, "message_id": m, "attachment_id": None, "url": None, "size": s, "sha256": h}
                           for i, (m, s, h) in enumerate(f["chunks"])]
            idx.replace_chunks(node["id"], chunk_dicts, node["version"])
            added += 1
        if added or kept:
            log.info("Merged this device's previous index: %d item(s) re-published, %d older edit(s) kept as versions.",
                     added, kept)

    def _store_keyring(self):
        """Record every key this device knows inside the (encrypted) index, so devices that
        restore a checkpoint can read data written with keys they never had."""
        if not self.crypto or not hasattr(self.crypto, "keys"):
            return
        try:
            known = set(json.loads(self.index.kv_get("keyring") or "[]"))
        except ValueError:
            known = set()
        mine = {k.hex() for k in self.crypto.keys()}
        if not mine <= known:
            self.index.kv_set("keyring", json.dumps(sorted(known | mine)))

    def _learn_keys(self):
        """Pick up older keys stored in a restored checkpoint (see _store_keyring)."""
        if not self.crypto or not hasattr(self.crypto, "add"):
            return
        try:
            stored = json.loads(self.index.kv_get("keyring") or "[]")
        except ValueError:
            return
        new = []
        for h in stored:
            try:
                if self.crypto.add(bytes.fromhex(h)):
                    new.append(h)
            except ValueError:
                continue
        if not new:
            return
        log.info("Learned %d older encryption key(s) from the drive's index.", len(new))
        try:
            from .config import Config
            saved = Config.load()
            for h in new:
                if h != saved.encryption_key and h not in saved.old_encryption_keys:
                    saved.old_encryption_keys.append(h)
            saved.save()
            self.cfg.old_encryption_keys = list(saved.old_encryption_keys)
        except Exception as e:
            log.warning("Could not save the older keys to the config: %s", e)

    def _stash_pending(self):
        if not self.staging_dir or not os.path.isdir(self.staging_dir):
            return []
        out = []
        for node in self.index.unsynced_files():
            src = os.path.join(self.staging_dir, f"{node['id']}.dat")
            if not os.path.exists(src):
                continue
            tmp = os.path.join(self.staging_dir, f"reattach-{node['id']}.dat.keep")
            os.replace(src, tmp)
            out.append((self.index.path_of(node["id"]), tmp, node["mtime"]))
        return out

    def _reattach(self, pending):
        for path, tmp, mtime in pending:
            parent_path, name = posixpath.split(path)
            parent = self.index.makedirs(parent_path)
            node = self.index.lookup(parent["id"], name)
            if node is not None and node["is_dir"]:
                name, node = conflict_name(name, "local"), None
            if node is None:
                node = self.index.create_node(parent["id"], name, False, state="pending")
            os.replace(tmp, os.path.join(self.staging_dir, f"{node['id']}.dat"))
            self.index.mark_modified(node["id"], os.path.getsize(os.path.join(self.staging_dir, f"{node['id']}.dat")),
                                     mtime)
            log.info("Kept local changes to %s that were not uploaded yet.", path)
