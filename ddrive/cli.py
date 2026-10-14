"""Command-line interface for DDrive."""

import argparse
import json
import logging
import os
import subprocess
import sys
import time

from .backend import DiscordBackend, is_encrypted_index
from .cache import ChunkCache
from .config import Config, config_path, default_data_dir, format_size, launcher, normalize_mount_point, parse_size
from . import codec
from .crypto import AuthenticationError, KeyRing, generate_key, key_fingerprint, parse_key
from .discord_api import DiscordAPI, DiscordError, is_gone
from .drive import DDrive
from .fuse_loader import find_winfsp_dll, find_linux_fuse_lib, unmount, FUSE_ERROR
from .index import Index
from .journal import Journal
from .ui import embedded, theme_output

log = logging.getLogger("ddrive.cli")


def is_mounted(mount_point: str) -> bool:
    """True if DDrive (or anything) is mounted at the given drive letter / directory."""
    if sys.platform == "win32":
        letter = mount_point.rstrip(":\\")
        return os.path.exists(letter + ":\\")
    return os.path.ismount(mount_point)


def setup_logging(verbose: bool = False, log_to_file: bool = True):
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    if sys.stderr is not None:
        try:
            handlers.append(logging.StreamHandler(sys.stderr))
        except Exception:
            pass
    if log_to_file:
        try:
            log_dir = default_data_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "ddrive.log")
            handlers.append(logging.FileHandler(log_path, mode="a", encoding="utf-8"))
        except Exception:
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers if handlers else None,
        force=True,
    )


def normalize_virtual_path(p: str, mount_point: str = "Z:") -> str:
    p = p.strip().replace("\\", "/")
    mp = mount_point.replace("\\", "").rstrip(":")
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    p = "/" + p.lstrip("/")
    return p


def get_crypto_from_cfg(cfg: Config):
    """KeyRing for the configured key(s), or None if encryption is off.

    Exits on a broken key: silently continuing would upload plaintext.
    """
    if not cfg.encryption_enabled:
        return None
    if not cfg.encryption_key:
        raise SystemExit(f"[ERROR] Encryption is enabled but no key is configured. Run '{launcher()} setup'.")
    try:
        return KeyRing.from_hex(cfg.encryption_key, cfg.old_encryption_keys)
    except Exception as e:
        raise SystemExit(f"[ERROR] Failed to load encryption key: {e}")


def cmd_setup(args):
    setup_logging(args.verbose)
    cfg = Config.load()
    if not embedded():
        print("=" * 60)
        print("          DDrive Configuration Setup")
        print("=" * 60)
    print()

    token = args.token or cfg.bot_token
    if not token:
        try:
            token = input("Enter Discord Bot Token: ").strip()
        except EOFError:
            pass

    channel_id = args.channel or cfg.channel_id
    if not channel_id:
        try:
            channel_id = input("Enter Private Discord Channel ID: ").strip()
        except EOFError:
            pass

    mount_point = args.mount or cfg.mount_point or "Z:"
    if not args.mount:
        label = "Drive Letter" if sys.platform == "win32" else "Mount Directory"
        try:
            val = input(f"Enter Mount Point {label} [{mount_point}]: ").strip()
            if val:
                mount_point = val
        except EOFError:
            pass
    mount_point = normalize_mount_point(mount_point)

    if not token or not channel_id:
        print("\n[ERROR] Bot token and channel ID are required.")
        return 1

    print("\nValidating Discord bot credentials and permissions...")
    api = DiscordAPI(token, timeout=30, retries=3)
    try:
        bot_user = api.get_me()
        bot_name = f"{bot_user.get('username')}#{bot_user.get('discriminator', '0')}"
        print(f"  [OK] Authenticated as Bot: {bot_name} (ID: {bot_user.get('id')})")
    except DiscordError as e:
        print(f"  [FAIL] Failed to authenticate bot: {e}")
        return 1

    try:
        channel = api.get_channel(channel_id)
        ch_name = channel.get("name", channel_id)
        guild_id = channel.get("guild_id", "DM")
        print(f"  [OK] Channel accessible: #{ch_name} in Server ID: {guild_id}")
    except DiscordError as e:
        print(f"  [FAIL] Failed to access channel {channel_id}: {e}")
        print("         Make sure the bot has been invited to your server and has access to this channel!")
        return 1

    # Check WinFsp / FUSE
    if sys.platform == "win32":
        dll = find_winfsp_dll()
        if dll:
            print(f"  [OK] WinFsp driver found: {dll}")
        else:
            print("  [WARNING] WinFsp was not found! Install it from https://winfsp.dev/rel/ to mount virtual drives.")
    else:
        lib = find_linux_fuse_lib()
        if lib:
            print(f"  [OK] Linux FUSE library found: {lib}")
        else:
            print("  [WARNING] libfuse2 was not found! Open ./run.sh -> Tools -> Install requirements (or: apt install libfuse2)")

    # Configure End-to-End Encryption
    print("\nConfiguring Zero-Knowledge Encryption (AES-256-GCM)...")
    enc_enabled = True
    enc_key_hex = cfg.encryption_key
    enc_salt_hex = cfg.encryption_salt
    received_old_keys = []
    new_phrase = False
    from . import words

    # Is there already an encrypted drive in this channel? Then a new random key would be useless.
    existing_encrypted = False
    try:
        latest = DiscordBackend(api, channel_id).find_latest_index()
        existing_encrypted = latest is not None and is_encrypted_index(latest)
    except Exception as e:
        log.debug("Could not look for an existing index backup: %s", e)

    if getattr(args, "key", None):
        try:
            enc_key_hex = parse_key(args.key).hex()
        except ValueError as e:
            print(f"  [FAIL] Invalid --key: {e}")
            return 1
        enc_salt_hex = ""
        enc_enabled = True
        print(f"  [OK] Custom encryption key configured (fingerprint {key_fingerprint(enc_key_hex)}).")
    elif not enc_key_hex and existing_encrypted:
        print("  [!] This channel already holds an encrypted DDrive. How do you want to bring this device in?")
        print("      1) Ask one of your other devices to approve it (recommended)")
        print("      2) Type the drive's 24-word recovery phrase")
        try:
            how = input("Choose 1 or 2 [1]: ").strip() or "1"
        except EOFError:
            how = "1"
        if how == "2" or words.looks_like_phrase(how):
            try:
                typed = how if words.looks_like_phrase(how) else input("Recovery phrase (24 words): ")
                enc_key_hex = parse_key(typed).hex()
            except (ValueError, EOFError) as e:
                print(f"  [FAIL] {str(e) or 'Nothing entered'}.")
                return 1
            enc_salt_hex = ""
        else:
            payload = _request_key(DiscordBackend(api, channel_id))
            if payload is None:
                return 1
            enc_key_hex = payload["key"]
            received_old_keys = payload.get("old") or []
            enc_salt_hex = ""
        enc_enabled = True
        print(f"  [OK] Key ready (fingerprint {key_fingerprint(enc_key_hex)}).")
    elif not enc_key_hex:
        try:
            enc_ans = input("Enable Zero-Knowledge Encryption? (Discord will see only encrypted data) [Y/n]: ").strip().lower()
        except EOFError:
            enc_ans = "y"
        if enc_ans != "n":
            enc_key_hex = generate_key().hex()
            enc_salt_hex = ""
            enc_enabled = True
            new_phrase = True
            print("  [OK] Generated a random 256-bit key for this drive.")
        else:
            enc_enabled = False
            enc_key_hex = ""
            enc_salt_hex = ""
            print("  [WARNING] Encryption disabled.")
    else:
        print(f"  [OK] Existing encryption key retained (fingerprint {key_fingerprint(enc_key_hex)}).")

    # Never throw a key away: data written with it would become unreadable.
    old_keys = [k for k in cfg.old_encryption_keys if k != enc_key_hex]
    for k in received_old_keys:
        if k != enc_key_hex and k not in old_keys:
            old_keys.append(k)
    if cfg.encryption_key and cfg.encryption_key != enc_key_hex and cfg.encryption_key not in old_keys:
        old_keys.append(cfg.encryption_key)
        print(f"  [OK] Previous key (fingerprint {key_fingerprint(cfg.encryption_key)}) kept for reading older files.")

    # A key that cannot read the drive already in this channel would only fail later, at mount.
    if existing_encrypted and enc_enabled and enc_key_hex:
        print("Checking the key against the drive already stored in this channel...")
        crypto = KeyRing.from_hex(enc_key_hex, old_keys)
        try:
            DiscordBackend(api, channel_id, crypto=crypto).load_index_message(latest)
            print("  [OK] The key matches the existing drive.")
        except AuthenticationError:
            print("  [FAIL] This key cannot read the drive already stored in this channel. Nothing was saved.")
            print("         Use the recovery phrase of the drive that is in this channel:")
            print(f"           on a device where it works:  {launcher()} recovery-phrase")
            print(f"           on this device:              {launcher()} setup   (choose 2 and type the 24 words)")
            print("         or let that device approve this one (choose 1).")
            return 1
        except Exception as e:
            print(f"  [WARNING] Could not verify the key right now ({e}); saving it anyway.")
        finally:
            crypto.close()

    cfg.bot_token = token
    cfg.channel_id = channel_id
    cfg.mount_point = mount_point
    cfg.encryption_enabled = enc_enabled
    cfg.encryption_key = enc_key_hex
    cfg.encryption_salt = enc_salt_hex
    cfg.old_encryption_keys = old_keys
    cfg.save()

    print(f"\n[SUCCESS] Configuration saved to: {config_path()}")
    if new_phrase:
        _show_phrase(enc_key_hex, first=True)
    if existing_encrypted and not is_mounted(cfg.mount_point):
        _catch_up(cfg)
    print(f"Start the drive from the menu ({launcher()}, option 1), or run: {launcher()} start")
    return 0


def _request_key(backend):
    """New device: ask another device for the key. Returns {'key', 'old'} or None."""
    from . import keyshare
    req = keyshare.KeyRequest(backend)
    req.post()
    print()
    print("--- Waiting for another device to approve ---")
    print(f"Verification code:  {req.code}")
    print(f"On a device that already has the key, open the menu ({launcher()}) and choose")
    print("'Approve a new device'. Check it shows the same code, then accept.")
    print("Waiting up to 10 minutes. Press Ctrl+C to cancel.")
    try:
        payload = req.wait(timeout=600)
    except KeyboardInterrupt:
        req._cleanup()
        print("\n[!] Cancelled.")
        return None
    except keyshare.Rejected as e:
        print(f"[FAIL] {e}")
        return None
    except TimeoutError:
        print("[FAIL] Nobody approved the request within 10 minutes. Run it again when ready.")
        return None
    try:
        payload["key"] = parse_key(payload.get("key", "")).hex()
        payload["old"] = [parse_key(k).hex() for k in payload.get("old") or []]
    except ValueError as e:
        print(f"[FAIL] The received key is not valid: {e}")
        return None
    print(f"[OK] Key received from your other device (fingerprint {key_fingerprint(payload['key'])}).")
    return payload


def _catch_up(cfg):
    """Bring this device's file list fully up to date: latest checkpoint + every change since."""
    print()
    print("--- Catching up with your drive ---")
    idx, j, crypto = _open_journal(cfg)
    try:
        latest = j.backend.find_latest_index()
        if latest is None:
            print("[OK] The drive is still empty.")
            return
        j.restore(latest)
        j.poll()
        st = idx.stats(max_age=0)
        print(f"[OK] Up to date: {st['files']:,} files in {st['dirs']:,} folders.")
    except Exception as e:
        print(f"[WARNING] Could not catch up right now ({e}); it happens automatically on the first start.")
    finally:
        _close(idx, crypto)


def cmd_request_key(args):
    """Ask one of your other devices for the encryption key (e.g. after a key mix-up)."""
    setup_logging(args.verbose)
    cfg = Config.load()
    if not cfg.is_configured():
        print(f"[ERROR] Run '{launcher()} setup' first.")
        return 1
    backend = DiscordBackend(DiscordAPI(cfg.bot_token, timeout=60, retries=3), cfg.channel_id)
    payload = _request_key(backend)
    if payload is None:
        return 1
    saved = Config.load()
    keys = [payload["key"]] + payload["old"]
    if saved.encryption_key and saved.encryption_key not in keys:
        keys.append(saved.encryption_key)           # never lose a key
    saved.encryption_enabled = True
    saved.encryption_key = payload["key"]
    saved.old_encryption_keys = [k for k in dict.fromkeys(keys[1:] + saved.old_encryption_keys)
                                 if k != payload["key"]]
    saved.save()
    print("[OK] Saved.")
    if is_mounted(saved.mount_point):
        print(f"[!] Restart the drive (stop, then start) so it uses the new key.")
    else:
        _catch_up(saved)
    return 0


def cmd_approve_keys(args):
    """Device with the key: approve (or reject) another device asking for it."""
    from . import keyshare
    setup_logging(args.verbose)
    cfg = Config.load()
    if not cfg.is_configured() or not cfg.encryption_key:
        print("[ERROR] This device has no encryption key to share.")
        return 1
    backend = DiscordBackend(DiscordAPI(cfg.bot_token, timeout=60, retries=3), cfg.channel_id)
    print("Looking for devices asking for the key (requests from the last 15 minutes)...")
    try:
        pending = keyshare.pending_requests(backend)
        if not pending:
            print("None yet. Start the request on the new device: setup, option 1.")
            print("Waiting for one... (Ctrl+C to stop)")
            end = time.time() + 600
            while not pending and time.time() < end:
                time.sleep(3)
                pending = keyshare.pending_requests(backend)
        if not pending:
            print("[!] No request arrived.")
            return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    print()
    for i, r in enumerate(pending, 1):
        age = max(0, int(time.time() - r["time"]))
        print(f"  {i}) {r['device']}, {age // 60} min {age % 60} s ago, code {r['code']}")
    print()
    try:
        pick = input(f"Which request? [1]: ").strip() or "1"
        req = pending[int(pick) - 1]
    except (ValueError, IndexError, EOFError):
        print("[!] Nothing chosen.")
        return 0
    print()
    print(f"The new device must show this code:  {req['code']}")
    try:
        same = input("Does it show exactly the same code? [y/N]: ").strip().lower().startswith("y")
    except EOFError:
        same = False
    if not same:
        keyshare.answer(backend, req, False)
        print("[OK] Rejected. If you didn't start this request yourself, someone else may have your bot")
        print("     token: reset it in the Discord Developer Portal.")
        return 0
    keyshare.answer(backend, req, True, {"key": cfg.encryption_key, "old": cfg.old_encryption_keys})
    print("[SUCCESS] Key sent (encrypted, only the new device can read it).")
    print("          The new device now catches up with your files by itself.")
    return 0


_SECRET_FIELDS = {"bot_token", "encryption_key", "encryption_salt", "old_encryption_keys", "extra_bot_tokens",
                  "web_token", "web_password", "discord_user_token"}
_LIST_COMMANDS = {"extra_bot_tokens": "bots add <token>", "hidden_folders": "hide <folder>", "sync_jobs": "sync add",
                  "old_encryption_keys": "add-old-key", "web_user": "webdav-password",
                  "web_password": "webdav-password"}


def cmd_config(args):
    """Show or change one setting: `config`, `config <key>`, `config <key> <value>`."""
    import dataclasses
    cfg = Config.load()
    types = {f.name: f.type for f in dataclasses.fields(Config)}
    if not args.key:
        for name in types:
            value = getattr(cfg, name)
            shown = (f"({len(value)} key(s))" if isinstance(value, list) else "(set)") \
                if name in _SECRET_FIELDS and value else value
            if name == "sync_jobs":
                shown = f"{len(value)} folder(s), see 'sync list'"
            print(f"{name:24} {shown}")
        print(f"\nConfig file: {config_path()}  (restart the drive after changing settings)")
        print(f"Secrets in it (bot tokens, keys, dashboard sign-in): {cfg.protection()}")
        return 0
    if args.key not in types:
        print(f"[ERROR] Unknown setting '{args.key}'. Run 'config' to list them.")
        return 1
    if args.value is None:
        value = getattr(cfg, args.key)
        print("(set)" if args.key in _SECRET_FIELDS and value else value)
        return 0
    if args.key == "web_hosts":
        raw = "" if args.value.strip() in ("-", "none", '""') else args.value
        cfg.web_hosts = [h.strip().lower().split("://")[-1].split("/")[0] for h in raw.split(",") if h.strip()]
        cfg.save()
        print(f"[OK] web_hosts = {', '.join(cfg.web_hosts) or '(none)'}  (restart the drive for it to take effect)")
        return 0
    if args.key in _LIST_COMMANDS:
        print(f"[ERROR] Use '{launcher()} {_LIST_COMMANDS[args.key]}' to change {args.key}.")
        return 1
    if args.key == "discord_user_token":
        print("[ERROR] Log in with Discord in the dashboard's Settings instead.")
        return 1
    if args.key in ("bot_token", "channel_id", "encryption_key", "encryption_salt", "encryption_enabled", "web_token"):
        print("[ERROR] Use 'setup' to change the bot token, channel or encryption.")
        return 1
    kind, raw = types[args.key], args.value.strip()
    try:
        if kind in (bool, "bool"):
            if raw.lower() not in ("true", "false", "yes", "no", "on", "off", "1", "0"):
                raise ValueError("expected true or false")
            value = raw.lower() in ("true", "yes", "on", "1")
        elif kind in (int, "int"):
            # sizes accept units: 500M, 2G, 1.5T ...
            value = parse_size(raw) if args.key.endswith("_bytes") or args.key == "max_file_size" else int(raw)
        elif kind in (float, "float"):
            value = float(raw)
        else:
            value = "" if raw in ("-", '""') else raw
    except ValueError as e:
        print(f"[ERROR] Invalid value for {args.key}: {e}")
        return 1
    if args.key == "discord_pace" and value not in ("gentle", "balanced", "fast", "off"):
        print("[ERROR] discord_pace must be gentle, balanced, fast or off.")
        return 1
    if args.key == "cache_mode" and value not in ("disk", "memory"):
        print("[ERROR] cache_mode must be 'disk' or 'memory'.")
        return 1
    if args.key == "mount_point":
        value = normalize_mount_point(value)
    setattr(cfg, args.key, value)
    cfg.save()
    shown = f"{value} ({format_size(value)})" if isinstance(value, int) and value >= 2**10 and (
        args.key.endswith("_bytes") or args.key == "max_file_size") else value
    print(f"[OK] {args.key} = {shown}  (restart the drive for it to take effect)")
    return 0


def cmd_verify(args):
    """Download and decrypt every chunk of the files under a path, without caching, and report failures."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    setup_logging(args.verbose, log_to_file=False)
    cfg = Config.load()
    if not cfg.is_configured():
        print("[ERROR] DDrive is not configured.")
        return 1
    idx, j, crypto = _open_journal(cfg)
    try:
        vpath = normalize_virtual_path(args.path or "/", cfg.mount_point)
        node = idx.resolve(vpath)
        if node is None:
            print(f"[ERROR] Not found in DDrive: {args.path}")
            return 1
        files, todo = [], [node]
        while todo:
            n = todo.pop()
            if n["is_dir"]:
                todo.extend(idx.children(n["id"]))
            elif n["state"] == "synced":
                files.append((idx.path_of(n["id"]), idx.get_chunks(n["id"]), n["id"]))
        files.sort()
        total = sum(c["size"] for _, chunks, _ in files for c in chunks)
        print(f"Checking {len(files)} file(s), {total / 2**20:.1f} MiB under {vpath} (downloads everything once)...")

        from .heal import Healer
        healer = Healer(cfg, idx, j.backend, crypto=crypto)
        repaired = []

        def check(chunk):
            problem = None
            try:
                data, _ = j.backend.download(chunk["message_id"], chunk.get("url"))
                data = codec.decode(data, crypto, chunk.get("sha256"))
                if chunk.get("sha256") and hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                    problem = "checksum mismatch (damaged)"
            except AuthenticationError:
                problem = "cannot decrypt: encrypted with a different key, or damaged"
            except DiscordError as e:
                if not is_gone(e):
                    return f"download failed ({e})"
                problem = "missing from Discord"
            except Exception as e:
                return f"download failed ({e})"
            if problem is None:
                return None
            if healer.recover(chunk["message_id"]) is not None:
                repaired.append(chunk["message_id"])
                return None
            return problem

        bad = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            unreadable = []
            for i, (path, chunks, nid) in enumerate(files, 1):
                problems = [p for p in pool.map(check, chunks) if p]
                if problems:
                    bad += 1
                    print(f"  [BAD] {path}: {problems[0]}")
                    if problems[0].startswith(("cannot decrypt", "missing from Discord", "checksum")):
                        unreadable.append((nid, path))
                elif args.verbose:
                    print(f"  [OK]  {path}")
                if i % 25 == 0:
                    print(f"  ... {i}/{len(files)} checked")
        healer.drain()
        if repaired:
            if not is_mounted(cfg.mount_point):
                j.flush()       # a running drive publishes the repairs itself
            print(f"\n[OK] {len(repaired)} missing or damaged piece(s) were rebuilt from spare pieces and uploaded again.")
        if bad:
            print(f"\n{bad} of {len(files)} file(s) cannot be read. Older versions may still work: "
                  f"{launcher()} versions <path>")
            if unreadable and getattr(args, "remove", False):
                from .heal import confirm_unreadable
                removed = 0
                for nid, path in unreadable:
                    if not confirm_unreadable(idx, j.backend, crypto, nid):
                        print(f"  [KEPT] {path}: it can be read now, or Discord could not be asked. Not removed.")
                        continue
                    idx.purge_node(nid)
                    removed += 1
                print(f"[OK] Removed {removed} unreadable file(s) from the drive (on every device).")
                print("     Copy them onto the drive again from their originals if you still have them.")
                return 0
            if unreadable:
                print(f"To remove the {len(unreadable)} file(s) that can never be read (e.g. encrypted with a lost key):")
                print(f"  {launcher()} verify {args.path} --remove")
            return 1
        print(f"\n[OK] All {len(files)} file(s) downloaded and decrypted correctly.")
        return 0
    finally:
        _close(idx, crypto)


def _log_message(line):
    """'12:00:00 [ERROR] ddrive.cli: text' -> 'text'."""
    msg = line.split("] ", 1)[-1]
    name, sep, rest = msg.partition(": ")
    return rest if sep and name.startswith("ddrive") else msg


def cmd_log(args):
    """Show the end of the log file (optionally only warnings and errors)."""
    path = os.path.join(default_data_dir(), "ddrive.log")
    if not os.path.exists(path):
        print(f"No log yet ({path}).")
        return 0
    if args.clear:
        from .logbuf import clear_file
        size = os.path.getsize(path)
        if not clear_file(path):
            print(f"[ERROR] Could not empty {path} (is another program holding it open?).")
            return 1
        print(f"[OK] Log cleared ({_human(size)} removed). The dashboard's Log tab empties too.")
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if args.errors:
        lines = [l for l in lines if "[ERROR]" in l or "[WARNING]" in l]
        # The same problem is often logged many times in a row; show it once with a count.
        grouped = []
        for line in lines:
            key = line.split(" ", 1)[-1]                 # ignore the time
            if grouped and grouped[-1][0] == key:
                grouped[-1][2] += 1
            else:
                grouped.append([key, line, 1])
        lines = [line + (f"  (x{n})" if n > 1 else "") for _, line, n in grouped]
        if not lines:
            print("[OK] No recent problems.")
    for line in lines[-args.lines:]:
        print(line)
    if not args.errors:
        print(f"\n({path})")
    return 0


def cmd_add_old_key(args):
    """Add an earlier encryption key so data encrypted with it can still be read."""
    cfg = Config.load()
    candidates = []
    if args.from_config:
        try:
            other = Config.load(args.from_config)
        except (OSError, ValueError, SystemExit) as e:
            print(f"[ERROR] Could not read {args.from_config}: {e}")
            return 1
        if getattr(other, "protect_error", None):
            print("[ERROR] The keys in that file are encrypted for the device it comes from, so they can't be read "
                  f"here. On that device run '{launcher()} export-key' and paste the key instead.")
            return 1
        candidates = [other.encryption_key] + list(other.old_encryption_keys or [])
    else:
        raw = args.key if args.key and args.key != "-" else sys.stdin.readline()
        candidates = [raw]
    added = 0
    for raw in candidates:
        if not (raw or "").strip():
            continue
        try:
            hex_key = parse_key(raw).hex()
        except ValueError as e:
            print(f"[ERROR] Not a valid key: {e}")
            return 1
        fp = key_fingerprint(hex_key)
        if hex_key == cfg.encryption_key or hex_key in cfg.old_encryption_keys:
            print(f"[OK] Key {fp} is already known.")
            continue
        cfg.old_encryption_keys.append(hex_key)
        added += 1
        print(f"[OK] Added older key {fp}; data encrypted with it can be read again.")
    if added:
        cfg.save()
        print("Restart the drive for this to take effect.")
    return 0


def cmd_cancel_uploads(args):
    """Cancel uploads that are stuck: a changed file goes back to its last uploaded version,
    a file that was never uploaded is removed. The drive has to be stopped."""
    setup_logging(args.verbose)
    cfg = Config.load()
    if is_mounted(cfg.mount_point):
        print("[ERROR] Stop the drive first (uploads run inside it), then try again.")
        return 1
    idx = Index(os.path.join(cfg.resolved_data_dir, "index.db"))
    try:
        pending = idx.unsynced_files()
        if not pending:
            print("[OK] Nothing is waiting to upload.")
            return 0
        for i, n in enumerate(pending, 1):
            print(f"  {i}) {idx.path_of(n['id'])}  ({_human(n['size'])})")
        if args.all:
            chosen = pending
        else:
            try:
                pick = input("\nCancel which? (a number, or 'all') [all]: ").strip().lower() or "all"
            except EOFError:
                pick = "all"
            if pick == "all":
                chosen = pending
            else:
                try:
                    chosen = [pending[int(pick) - 1]]
                except (ValueError, IndexError):
                    print("[!] Nothing cancelled.")
                    return 0
        staging = os.path.join(cfg.resolved_data_dir, "staging")
        for n in chosen:
            path = idx.path_of(n["id"])
            rec = idx.kv_get(f"upload:{n['id']}")
            if rec:
                from .uploader import Uploader
                try:
                    idx.release(Uploader.record_mids(json.loads(rec)))
                except ValueError:
                    pass
                idx.kv_delete(f"upload:{n['id']}")
            chunks = idx.get_chunks(n["id"])
            if chunks:
                idx.update(n["id"], state="synced", size=sum(c["size"] for c in chunks))
                print(f"[OK] {path}: back to its last uploaded version.")
            else:
                idx.delete_node(n["id"])
                print(f"[OK] {path}: removed (it had never been uploaded).")
            try:
                os.remove(os.path.join(staging, f"{n['id']}.dat"))
            except OSError:
                pass
        return 0
    finally:
        idx.close()


_AUTOSTART_TAG = "# DDrive autostart"


def autostart_enabled() -> bool:
    from . import legacy
    if legacy.old_autostart():                   # made before the rename: it points at files that are gone
        import argparse as _argparse
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_autostart(_argparse.Namespace(action="on"))
    if legacy.old_context_menu(remove=True):
        import argparse as _argparse
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_context_menu(_argparse.Namespace(action="install"))
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, "DDrive")
                return True
        except OSError:
            return False
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return _AUTOSTART_TAG in out


def cmd_autostart(args):
    """Start the drive in the background automatically when this computer starts (on/off)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    action = args.action or "status"
    if action == "status":
        print(f"[OK] Autostart is {'ON' if autostart_enabled() else 'OFF'}.")
        return 0
    on = action == "on"
    from . import legacy
    legacy.old_autostart(remove=True)
    if sys.platform == "win32":
        import winreg
        from .runtime import FROZEN, shell_command
        vbs = os.path.join(root, "ddrive", "scripts", "windows", "mount_hidden.vbs")
        start = shell_command("mount", "-b") if FROZEN else f'wscript.exe "{vbs}"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            if on:
                winreg.SetValueEx(k, "DDrive", 0, winreg.REG_SZ, start)
            else:
                try:
                    winreg.DeleteValue(k, "DDrive")
                except OSError:
                    pass
    else:
        try:
            current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        except OSError:
            print("[ERROR] crontab is not available. Install it: sudo apt install -y cron")
            return 1
        lines = [l for l in current.splitlines() if _AUTOSTART_TAG not in l]
        if on:
            lines.append(f'@reboot sleep 20 && "{root}/run.sh" start >/dev/null 2>&1 {_AUTOSTART_TAG}')
        new = "\n".join(lines) + "\n" if lines else ""
        r = subprocess.run(["crontab", "-"], input=new, text=True, capture_output=True)
        if r.returncode != 0:
            print(f"[ERROR] Could not update crontab: {r.stderr.strip()}")
            return 1
    print(f"[OK] Autostart is now {'ON: the drive starts in the background when this computer starts' if on else 'OFF'}.")
    return 0


def _show_phrase(key_hex, first=False):
    from . import words
    print()
    print("--- Your recovery phrase ---")
    print(words.grid(words.key_to_phrase(bytes.fromhex(key_hex))))
    print()
    if first:
        print("[!] WRITE THESE 24 WORDS DOWN, in this order, and keep them somewhere safe and offline.")
        print("    They are the key to this drive. With them you can set up another device, sign in to the")
        print("    dashboard and get your files back if this computer is lost. Without them (and with no other")
        print("    device still set up) the files in Discord can never be read again. Nobody can reset them.")
        print("    Anyone who has them can read your files.")
        print(f"    Show them again any time on this computer with: {launcher()} recovery-phrase")
    else:
        print(f"Key fingerprint {key_fingerprint(key_hex)}. Anyone with these words can read your files.")
        print(f"On a new device: {launcher()} setup, then choose \"type the recovery phrase\".")


def cmd_recovery_phrase(args):
    """Show the 24-word recovery phrase of this drive (its encryption key, as words)."""
    cfg = Config.load()
    if not (cfg.encryption_enabled and cfg.encryption_key):
        # A drive without encryption has no key to write as words; the dashboard still signs in with 24.
        from . import words
        from .web import owner_key
        key = owner_key(cfg)
        if not key:
            print("[ERROR] No encryption key is configured on this device, and the dashboard has not run yet.")
            print(f"        Start the drive once ({launcher()} start), then try again.")
            return 1
        if args.check:
            try:
                ok = words.phrase_to_key(input("Type your 24 words to check them: ")).hex() == key
            except (ValueError, EOFError) as e:
                print(f"[ERROR] {str(e) or 'Nothing entered'}.")
                return 1
            print("[OK] Those are the words that sign in to this dashboard." if ok else
                  "[ERROR] Those are 24 valid words, but they do NOT sign in here.")
            return 0 if ok else 1
        print()
        print("--- The 24 words that sign you in to the dashboard ---")
        print(words.grid(words.key_to_phrase(bytes.fromhex(key))))
        print()
        print("This drive is not encrypted, so these words are no encryption key: they only sign you in to")
        print("the web dashboard, as its owner, on this computer. Keep them to yourself.")
        return 0
    if args.check:
        from . import words
        try:
            typed = input("Type your recovery phrase to check it (24 words): ")
            ok = words.phrase_to_key(typed).hex() == cfg.encryption_key
        except (ValueError, EOFError) as e:
            print(f"[ERROR] {str(e) or 'Nothing entered'}.")
            return 1
        print("[OK] That is this drive's recovery phrase." if ok else
              "[ERROR] Those are 24 valid words, but they are NOT this drive's phrase.")
        return 0 if ok else 1
    _show_phrase(cfg.encryption_key)
    return 0


def cmd_export_key(args):
    """Print the encryption key so it can be copied to another device (setup -k <key>)."""
    cfg = Config.load()
    if not cfg.encryption_key:
        print("[ERROR] No encryption key is configured on this device.")
        return 1
    if sys.stdout.isatty():
        print("Your encryption key (keep it secret; anyone with it and your bot token can read your files):",
              file=sys.stderr)
    print(cfg.encryption_key)
    if sys.stdout.isatty():
        print(f"Fingerprint {key_fingerprint(cfg.encryption_key)}. On the other device run: "
              f"{launcher()} setup -k <key>", file=sys.stderr)
    return 0


def _human(n):
    """Readable size: 0 bytes, 512 KB, 94.3 MB, 129.9 GB (binary units)."""
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def _dir_size(path, suffix=None):
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.is_file() and (suffix is None or e.name.endswith(suffix)):
                    try:
                        total += e.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def cmd_status(args):
    import shutil
    setup_logging(args.verbose)
    cfg = Config.load()
    data_dir = cfg.resolved_data_dir
    w = 20

    def row(label, value):
        print(f"{label + ':':{w}}{value}")

    if not embedded():
        print("=" * 60)
        print("DDrive Status".center(60))
        print("=" * 60)

    # --- overview -------------------------------------------------------
    if not cfg.is_configured():
        row("Setup", f"NOT CONFIGURED - run '{launcher()} setup'")
    mounted = is_mounted(cfg.mount_point)
    row("Drive", f"{cfg.mount_point}  " + ("RUNNING (mounted)" if mounted else "not running"))

    if cfg.encryption_enabled and cfg.encryption_key:
        try:
            enc = f"ON (AES-256-GCM) | key fingerprint {key_fingerprint(cfg.encryption_key)}"
            if cfg.old_encryption_keys:
                enc += f" + {len(cfg.old_encryption_keys)} older key(s): " + ", ".join(
                    key_fingerprint(k) for k in cfg.old_encryption_keys)
        except ValueError:
            enc = "ON, but the configured key is INVALID (run setup again)"
    elif cfg.encryption_enabled:
        enc = f"ON (no key yet: one is generated on first start, or run '{launcher()} setup')"
    else:
        enc = "OFF - files are uploaded unencrypted"
    row("Encryption", enc)

    if cfg.is_configured():
        api = DiscordAPI(cfg.bot_token, timeout=20, retries=2)
        try:
            bot = api.get_me()
            ch = api.get_channel(cfg.channel_id)
            row("Discord", f"connected as {bot.get('username')}, channel #{ch.get('name')}")
        except Exception as e:
            row("Discord", f"NOT REACHABLE ({e})")
        if cfg.extra_bot_tokens:
            row("Upload bots", f"{1 + len(cfg.extra_bot_tokens)} (uploads are spread over all of them)")
    if cfg.web_enabled:
        row("Web dashboard", f"http://localhost:{cfg.web_port}/" + (" (also on your network)" if cfg.web_lan else "")
            + ", sign in with your recovery phrase"
            + ("" if cfg.signup_mode in ("", "closed") else f"; accounts for others: {cfg.signup_mode}"))
    if cfg.hidden_folders:
        row("Hidden here", ", ".join(cfg.hidden_folders))

    # --- index ----------------------------------------------------------
    db_path = os.path.join(data_dir, "index.db")
    stats, idx = None, None
    if os.path.exists(db_path):
        try:
            idx = Index(db_path)
            stats = idx.stats(max_age=0)
        except Exception as e:
            print(f"\nCould not read the file list: {e}")

    if stats:
        print("\n--- Your files (stored in Discord) ---")
        row("Files", f"{stats['files']:,} in {stats['dirs']:,} folders")
        row("Total size", _human(stats["bytes"]))
        if cfg.keep_versions:
            row("Old versions", f"{stats['versions']:,} ({_human(stats['version_bytes'])}), "
                                f"kept {cfg.version_retention_days:g} days" if cfg.version_retention_days
                                else f"{stats['versions']:,} ({_human(stats['version_bytes'])}), kept forever")
        else:
            row("Old versions", "off")
        if stats["trash"]:
            row("Being deleted", f"{stats['trash']:,} old pieces, removed from Discord in the background")
        if cfg.parity_enabled:
            pct = 100.0 * stats["protected_chunks"] / stats["chunks"] if stats["chunks"] else 100.0
            row("Self-healing", f"ON: {pct:.1f}% of pieces have spare pieces ({_human(stats['spare_bytes'])} extra)")
        else:
            row("Self-healing", "OFF (spare pieces are turned off)")
        row("Snapshots", f"{stats['snapshots']:,}" + (f", one every {cfg.snapshot_interval_hours:g} h, kept "
                                                       f"{cfg.snapshot_keep_days:g} days" if cfg.snapshot_interval_hours
                                                       else ", automatic snapshots off"))

        print("\n--- Syncing ---")
        row("Waiting to upload", f"{stats['unsynced']:,} file(s)")
        for key in idx.kv_keys("upload:"):
            try:
                rec = json.loads(idx.kv_get(key) or "null") or {}
                nid, size, cs = int(key.split(":", 1)[1]), int(rec["size"]), int(rec["chunk_size"])
            except (ValueError, KeyError, TypeError):
                continue
            total = max(1, (size + cs - 1) // cs)
            done = len(rec.get("chunks") or [])
            row("  Uploading now", f"{idx.path_of(nid)}  {done * 100 // total}% "
                                   f"({done} of {total} pieces, {_human(size)})")
        row("Changes to share", f"{stats['outbox']:,} (new/changed/deleted items not yet sent to other devices)")
        cursor = idx.kv_get("journal_cursor")
        row("Sync position", f"message {cursor}" if cursor else "not started (start the drive once)")
        row("This device", cfg.device_id or "(gets an ID on first start)")

    # --- local ----------------------------------------------------------
    print("\n--- On this computer ---")
    if (cfg.cache_mode or "disk").lower() == "memory":
        row("Read cache", f"memory only (up to {_human(cfg.memory_cache_bytes)} RAM), nothing stored on disk")
    else:
        used = _dir_size(os.path.join(data_dir, "cache"), ".chunk")
        row("Read cache", f"{_human(used)} used of {_human(cfg.cache_max_bytes)} (recently opened files)")
    if stats:
        row("Offline files", f"{stats.get('pinned_files', 0):,} ({_human(stats.get('pinned_bytes', 0))}) "
                             f"kept for offline use" if stats.get("pinned_files") else "none")
    queue = _dir_size(os.path.join(data_dir, "staging"), ".dat")
    row("Upload queue", f"{_human(queue)} waiting to upload (limit {_human(cfg.staging_max_bytes)})"
        if cfg.staging_max_bytes else f"{_human(queue)} waiting to upload")
    try:
        free = shutil.disk_usage(data_dir if os.path.isdir(data_dir) else os.path.expanduser("~")).free
        reserve = f" (DDrive always leaves {_human(cfg.min_free_disk_bytes)} free)" if cfg.min_free_disk_bytes else ""
        row("Free disk space", _human(free) + reserve)
    except OSError:
        pass
    if cfg.max_file_size:
        row("Max file size", _human(cfg.max_file_size))
    if sys.platform == "win32":
        dll = find_winfsp_dll()
        row("Drive software", "WinFsp installed" if dll else "WinFsp NOT FOUND - install it from https://winfsp.dev/rel/")
    else:
        lib = find_linux_fuse_lib()
        row("Drive software", f"libfuse ({lib})" if lib else "libfuse NOT FOUND - ./run.sh -> Tools -> Install requirements")
    row("Settings file", config_path())
    row("Data folder", data_dir)
    if not embedded():
        print("=" * 60)
    if idx is not None:
        idx.close()
    return 0


def cmd_mount(args):
    setup_logging(args.verbose)
    if FUSE_ERROR:
        print(f"\n[ERROR] FUSE driver is not ready:\n{FUSE_ERROR}\n")
        return 1

    cfg = Config.load()
    if not cfg.is_configured() and not args.mock:
        print(f"[ERROR] DDrive is not configured. Run '{launcher()} setup' first.")
        return 1

    mount_point = normalize_mount_point(args.mount or cfg.mount_point)
    try:
        autostart_enabled()              # renews a start-with-the-computer entry made under the program's earlier name
    except Exception as e:
        log.debug("autostart check: %s", e)

    if is_mounted(mount_point):
        print(f"[INFO] DDrive is already mounted and active on {mount_point}!")
        return 0

    if args.background:
        print(f"Starting DDrive in background on {mount_point}...")
        from .runtime import command
        # Global flags (-v) must come before the subcommand.
        sub_args = command(*(["-v"] if args.verbose else []), "mount", "-m", mount_point, windowless=True)
        if getattr(args, "allow_other", False):
            sub_args.append("--allow-other")
        if getattr(args, "cache", None):
            sub_args += ["--cache", args.cache]
        # Remember where the logs end, to show only what the new process writes.
        main_log = os.path.join(default_data_dir(), "ddrive.log")
        mount_log = os.path.join(cfg.resolved_data_dir, "mount.log")
        log_starts = {p: (os.path.getsize(p) if os.path.exists(p) else 0) for p in (main_log, mount_log)}
        if sys.platform == "win32":
            proc = subprocess.Popen(
                sub_args,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        else:
            os.makedirs(cfg.resolved_data_dir, exist_ok=True)
            with open(mount_log, "a") as log_f:
                proc = subprocess.Popen(
                    sub_args,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
        # The first start on a device can take a while (it downloads the index).
        deadline = time.time() + 60
        while time.time() < deadline and not is_mounted(mount_point) and proc.poll() is None:
            time.sleep(0.5)
        if is_mounted(mount_point):
            print(f"[SUCCESS] Drive {mount_point} mounted in background.")
            return 0
        if proc.poll() is not None:
            print("[ERROR] DDrive stopped while starting:")
            shown = set()
            for path, start in log_starts.items():
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(start)
                        new = f.read()
                except OSError:
                    continue
                for line in new.splitlines():
                    msg = _log_message(line) if "[ERROR]" in line or "[WARNING]" in line else None
                    if msg is None and line.startswith("[ERROR]"):
                        msg = line
                    if msg and msg not in shown:
                        shown.add(msg)
                        print("  " + msg)
            if not shown:
                print(f"  (no details; see {main_log})")
            return 1
        print("DDrive is still starting up. Check progress with the 'status' command or the log file:")
        print(f"  {main_log}")
        return 0

    if getattr(args, "allow_other", False):
        cfg.allow_other = True
    if getattr(args, "cache", None):
        cfg.cache_mode = args.cache

    drive = DDrive(cfg, local_test_dir=args.mock)
    print("=" * 60)
    print(f"Starting DDrive on {mount_point}...")
    if cfg.encryption_enabled:
        print("Zero-Knowledge Encryption: AES-256-GCM (Discord sees no file names or contents)")
    print("Press Ctrl+C to unmount and exit cleanly.")
    print("=" * 60)
    try:
        drive.mount(mount_point=mount_point, foreground=True)
    except (RuntimeError, ValueError) as e:
        log.error("%s", e)
        print(f"\n[ERROR] {e}")
        return 1
    return 0


def cmd_stop(args):
    """Stops the running DDrive background daemon and unmounts the virtual drive."""
    import signal
    setup_logging(args.verbose)
    cfg = Config.load()
    from . import legacy
    pid_file = legacy.pid_file(cfg.resolved_data_dir, "ddrive.pid")
    stopped = False

    if sys.platform == "win32":
        # 1. Try stopping via PID file
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
                res = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)
                if res.returncode == 0:
                    stopped = True
                try:
                    os.remove(pid_file)
                except OSError:
                    pass
            except Exception as e:
                log.debug("Could not stop via PID file: %s", e)

        # 2. Also search for any running python processes with ddrive mount
        ps_cmd = (
            'Get-CimInstance Win32_Process | '
            f'Where-Object {{ $_.CommandLine -like "*ddrive*mount*" -and $_.ProcessId -ne {os.getpid()} -and $_.ProcessId -ne $PID }} | '
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output $_.ProcessId }'
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.stdout.strip():
            stopped = True
        mp = cfg.mount_point.rstrip(":\\")
    else:
        mp = cfg.mount_point
        pid = None
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
            except (OSError, ValueError):
                pid = None
        # Unmounting makes the FUSE loop return so the daemon shuts down cleanly
        # (flushing uploads and backing up the index).
        unmount(mp)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped = True
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
        res = subprocess.run(["pkill", "-TERM", "-f", "ddrive (-v |--verbose )?mount"], capture_output=True)
        if res.returncode == 0:
            stopped = True
        # Give the daemon time to finish its final index backup.
        for _ in range(30):
            alive = subprocess.run(["pgrep", "-f", "ddrive (-v |--verbose )?mount"], capture_output=True).returncode == 0
            if not alive:
                break
            time.sleep(0.5)
        if os.path.ismount(mp):
            unmount(mp, lazy=True)
        try:
            os.remove(pid_file)
        except OSError:
            pass
        is_mounted_now = os.path.ismount(mp)

    if sys.platform == "win32":
        time.sleep(1.5)
        is_mounted_now = os.path.exists(f"{mp}:\\")

    if stopped:
        print(f"[SUCCESS] DDrive daemon stopped. Drive {mp} unmounted.")
    elif not is_mounted_now:
        print("[INFO] DDrive is not currently running.")
    else:
        print("[WARNING] Could not find running DDrive process to terminate.")
    return 0


def _open_journal(cfg):
    """(index, journal, crypto) for CLI commands that talk to the channel."""
    db_path = os.path.join(cfg.resolved_data_dir, "index.db")
    crypto = get_crypto_from_cfg(cfg)
    extra = [DiscordAPI(t, timeout=60, retries=3).configure(cfg) for t in cfg.extra_bot_tokens or []
             if t and t != cfg.bot_token]
    backend = DiscordBackend(DiscordAPI(cfg.bot_token, timeout=60, retries=3).configure(cfg), cfg.channel_id,
                             crypto=crypto, extra_apis=extra)
    idx = Index(db_path)
    idx.keep_versions = bool(cfg.keep_versions)
    j = Journal(cfg, idx, backend, crypto=crypto, staging_dir=os.path.join(cfg.resolved_data_dir, "staging"))
    if not cfg.device_id:
        j.device = "cli"
    return idx, j, crypto


def _close(idx, crypto):
    idx.close()
    if crypto:
        crypto.close()


def _fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def _fmt_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def cmd_backup(args):
    setup_logging(args.verbose)
    cfg = Config.load()
    if not cfg.is_configured():
        print("[ERROR] DDrive is not configured.")
        return 1
    if not os.path.exists(os.path.join(cfg.resolved_data_dir, "index.db")):
        print("[ERROR] No local index database found.")
        return 1
    idx, j, crypto = _open_journal(cfg)
    try:
        if not idx.kv_get("journal_cursor"):
            print("[ERROR] This index has not been synced yet. Mount the drive once first.")
            return 1
        if idx.outbox_count():
            print("[ERROR] There are local changes that have not been published yet. "
                  "Mount the drive (or wait for it to sync) and try again.")
            return 1
        print("Uploading index checkpoint to Discord...")
        j.poll()
        j.checkpoint(force=True)
        print(f"[SUCCESS] Checkpoint saved (message {idx.kv_get('last_backup_mid')}).")
        return 0
    finally:
        _close(idx, crypto)


def cmd_restore(args):
    setup_logging(args.verbose)
    cfg = Config.load()
    if not cfg.is_configured():
        print("[ERROR] DDrive is not configured.")
        return 1
    if is_mounted(cfg.mount_point) and not getattr(args, "force", False):
        print(f"[ERROR] The drive is currently mounted on {cfg.mount_point}. Stop it first "
              "(restoring underneath a running drive can corrupt its index), or pass --force.")
        return 1

    print("Searching Discord channel for the newest index checkpoint...")
    idx, j, crypto = _open_journal(cfg)
    try:
        latest = j.backend.find_latest_index()
        if latest is None:
            print("[FAIL] No index checkpoint found in the Discord channel.")
            return 1
        j.restore(latest)
        if latest.get("cursor"):
            n = j.poll()
            print(f"Replayed changes made after the checkpoint ({n} updated).")
        else:
            idx.kv_set("journal_cursor", "")
            print("Restored a backup from an older version; the journal starts on the next mount.")
        stats = idx.stats(max_age=0)
        print(f"[SUCCESS] Restored {stats['files']} files and {stats['dirs']} folders from Discord.")
        return 0
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        return 1
    finally:
        _close(idx, crypto)


def _resolve_file(idx, cfg, path):
    vpath = normalize_virtual_path(path, cfg.mount_point)
    node = idx.resolve(vpath)
    if node is None:
        print(f"[ERROR] Not found in DDrive: {path}")
    elif node["is_dir"]:
        print(f"[ERROR] {path} is a folder; versions are kept per file.")
        node = None
    return vpath, node


def _put_op_for_version(idx, v, uid, parent_uid, name):
    from .actions import version_put_op
    return version_put_op(idx, v, uid, parent_uid, name)


def _publish(j, ops, what):
    j.index.queue_ops(ops)              # after anything still queued (e.g. the delete it undoes)
    if not is_mounted(Config.load().mount_point):
        j.flush()                       # a running drive sends it within a few seconds
    print(f"[SUCCESS] {what}")
    print("It appears on every running device within a few seconds (others pick it up when they start).")


def cmd_versions(args):
    setup_logging(args.verbose, log_to_file=False)
    cfg = Config.load()
    idx = Index(os.path.join(cfg.resolved_data_dir, "index.db"))
    try:
        vpath, node = _resolve_file(idx, cfg, args.path)
        if node is None:
            return 1
        rows = idx.versions_for(node["uid"])
        print(f"{vpath}")
        print(f"  current  {_fmt_time(node['mtime'])}  {_fmt_size(node['size']):>10}")
        if not rows:
            print("  (no older versions)")
        labels = {"replaced": "replaced", "deleted": "deleted", "conflict": "conflicting edit"}
        for i, v in enumerate(rows, 1):
            print(f"  {i:>7}  {_fmt_time(v['superseded'])}  {_fmt_size(v['size']):>10}  "
                  f"{labels.get(v['reason'], v['reason'])}")
        if rows:
            print(f"\nRestore one with: {launcher()} restore-version <path> <number> [--as <new path>]")
        return 0
    finally:
        idx.close()


def cmd_restore_version(args):
    setup_logging(args.verbose)
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        vpath, node = _resolve_file(idx, cfg, args.path)
        if node is None:
            return 1
        rows = idx.versions_for(node["uid"])
        if not 1 <= args.number <= len(rows):
            print(f"[ERROR] {vpath} has {len(rows)} version(s); pick a number from '{launcher()} versions'.")
            return 1
        v = rows[args.number - 1]
        if args.as_path:
            target = normalize_virtual_path(args.as_path, cfg.mount_point)
            parent_path, name = target.rsplit("/", 1)
            parent = idx.resolve(parent_path or "/")
            if parent is None or not parent["is_dir"] or not name:
                print(f"[ERROR] Folder not found: {parent_path or '/'}")
                return 1
            from .index import new_uid
            op = _put_op_for_version(idx, v, new_uid(), parent["uid"], name)
            what = f"Version {args.number} of {vpath} restored as {target}."
        else:
            parent_uid = idx.get(node["parent"])["uid"]
            op = _put_op_for_version(idx, v, node["uid"], parent_uid, node["name"])
            what = f"{vpath} restored to version {args.number} (the current content is kept as a version)."
        _publish(j, [op], what)
        return 0
    finally:
        _close(idx, crypto)


def cmd_deleted(args):
    setup_logging(args.verbose, log_to_file=False)
    cfg = Config.load()
    idx = Index(os.path.join(cfg.resolved_data_dir, "index.db"))
    try:
        prefix = normalize_virtual_path(args.path or "/", cfg.mount_point)
        rows = idx.deleted_files(prefix)
        if not rows:
            print("No deleted files are being kept" + (f" under {prefix}." if prefix != "/" else "."))
            return 0
        for v in rows:
            print(f"{_fmt_time(v['superseded'])}  {_fmt_size(v['size']):>10}  {v['path']}")
        print(f"\nRecover one with: {launcher()} undelete <path>")
        return 0
    finally:
        idx.close()


def cmd_undelete(args):
    setup_logging(args.verbose)
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        vpath = normalize_virtual_path(args.path, cfg.mount_point)
        matches = [v for v in idx.deleted_files(vpath) if (v["path"] or "").lower() == vpath.lower()]
        if not matches:
            print(f"[ERROR] No deleted file kept at {vpath}. See '{launcher()} deleted'.")
            return 1
        from .actions import undelete_ops
        _publish(j, undelete_ops(idx, matches[:1]), f"{vpath} recovered.")
        return 0
    finally:
        _close(idx, crypto)


# --------------------------------------------------------------------- extra bots
def _bot_name(token):
    try:
        me = DiscordAPI(token, timeout=20, retries=2).get_me()
        return me.get("username") or "?", me.get("id")
    except Exception as e:
        return f"not reachable ({e})", None


def cmd_bots(args):
    """Extra bots in the same channel: each one adds upload speed."""
    cfg = Config.load()
    action = args.action or "list"
    if action == "list":
        if not cfg.is_configured():
            print(f"[ERROR] Run '{launcher()} setup' first.")
            return 1
        print(f"  Main bot:     {_bot_name(cfg.bot_token)[0]}")
        for i, t in enumerate(cfg.extra_bot_tokens or [], 1):
            print(f"  Extra bot {i}:  {_bot_name(t)[0]}")
        n = 1 + len(cfg.extra_bot_tokens or [])
        print(f"\nUploading with {n} bot(s). Add one with: {launcher()} bots add")
        return 0
    if action == "remove":
        try:
            i = int(args.token or 0)
            removed = cfg.extra_bot_tokens.pop(i - 1)
        except (ValueError, IndexError):
            print(f"[ERROR] Give the number of the extra bot to remove (see '{launcher()} bots').")
            return 1
        cfg.save()
        print(f"[OK] Removed extra bot {i} ({_bot_name(removed)[0]}). Restart the drive for it to take effect.")
        return 0
    token = (args.token or "").strip()
    if not token:
        try:
            import getpass
            token = getpass.getpass("Token of the extra bot (input is hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    if not token:
        print("[!] Nothing entered.")
        return 1
    print("Checking the bot...")
    api = DiscordAPI(token, timeout=30, retries=2)
    try:
        me = api.get_me()
        api.get_channel(cfg.channel_id)
    except DiscordError as e:
        print(f"  [FAIL] {e}")
        print("         Invite this bot to your server with the same link as the main bot (Step 1 of the")
        print("         README, with this bot's Client ID), and give it access to the drive channel.")
        return 1
    ids = {_bot_name(t)[1] for t in [cfg.bot_token] + list(cfg.extra_bot_tokens or [])}
    if me.get("id") in ids:
        print("  [FAIL] That bot is already in use. Each extra bot has to be a different bot (a new application).")
        return 1
    try:
        msg = api.send_message(cfg.channel_id, "DDrive: checking a new bot (removed right away)")
        api.delete_message(cfg.channel_id, msg["id"])
    except DiscordError as e:
        print(f"  [FAIL] The bot can't post in the drive channel: {e}")
        return 1
    cfg.extra_bot_tokens = list(cfg.extra_bot_tokens or []) + [token]
    cfg.save()
    print(f"[SUCCESS] Added {me.get('username')}. Uploads now use {1 + len(cfg.extra_bot_tokens)} bots.")
    if is_mounted(cfg.mount_point):
        print("Restart the drive (stop, then start) to use it.")
    return 0


# --------------------------------------------------------------------- snapshots
def _snapshot_by_number(idx, n):
    snaps = idx.snapshots()
    if not 1 <= n <= len(snaps):
        print(f"[ERROR] There are {len(snaps)} snapshot(s); pick a number from '{launcher()} snapshots'.")
        return None
    return snaps[n - 1]


def cmd_snapshots(args):
    """List snapshots, take one now, or delete one."""
    setup_logging(args.verbose, log_to_file=args.action == "create")
    cfg = Config.load()
    action = args.action or "list"
    if action == "create":
        idx, j, crypto = _open_journal(cfg)
        try:
            print("Taking a snapshot of the whole drive...")
            running = is_mounted(cfg.mount_point)
            if not running:
                j.flush()
                j.poll()
            ops = idx.snapshot_ops(args.label or "Manual", float(cfg.snapshot_keep_days or 0))
            for op in ops:
                j.post_ops([op])
            if not running:
                j.poll()
            files = sum(1 for op in ops for e in op["e"] if not e[1])
            print(f"[SUCCESS] Snapshot taken: {files:,} file(s). Kept for {cfg.snapshot_keep_days:g} days."
                  if cfg.snapshot_keep_days else f"[SUCCESS] Snapshot taken: {files:,} file(s).")
            return 0
        finally:
            _close(idx, crypto)
    idx = Index(os.path.join(cfg.resolved_data_dir, "index.db"))
    try:
        snaps = idx.snapshots()
        if action == "delete":
            if (args.label or "").strip().lower() == "all":
                for sn in snaps:
                    idx.delete_snapshot(sn["id"])
                print(f"[OK] {len(snaps)} snapshot(s) deleted for good (on every device once the drive syncs).")
                return 0
            if not (args.label or "").isdigit():
                print(f"[ERROR] Give the snapshot's number from '{launcher()} snapshots', or 'all'.")
                return 1
            snap = _snapshot_by_number(idx, int(args.label))
            if snap is None:
                return 1
            idx.delete_snapshot(snap["id"])
            print(f"[OK] Snapshot of {_fmt_time(snap['at'])} deleted for good (on every device once the drive syncs).")
            print("     Your current files are untouched; what only this snapshot still kept is removed from Discord.")
            return 0
        if not snaps:
            print("No snapshots yet." + (f" One is taken automatically every {cfg.snapshot_interval_hours:g} hours."
                                         if cfg.snapshot_interval_hours else ""))
            print(f"Take one now with: {launcher()} snapshots create")
            return 0
        for i, sn in enumerate(snaps, 1):
            keep = f"kept until {_fmt_time(sn['keep_until'])}" if sn["keep_until"] else "kept"
            print(f"  {i:>3}  {_fmt_time(sn['at'])}  {sn['files']:>7,} files  {_fmt_size(sn['bytes']):>10}  "
                  f"{(sn['label'] or ''):<10} {keep}")
        print(f"\nRestore with: {launcher()} snapshot-restore <number> [folder] [--in-place]")
        return 0
    finally:
        idx.close()


def cmd_snapshot_restore(args):
    """Bring back a folder (or everything) as it was in a snapshot."""
    from .actions import default_restore_folder, snapshot_restore_ops
    setup_logging(args.verbose)
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        snap = _snapshot_by_number(idx, args.number)
        if snap is None:
            return 1
        prefix = normalize_virtual_path(args.path or "/", cfg.mount_point)
        target = normalize_virtual_path(args.to, cfg.mount_point) if args.to else default_restore_folder(snap)
        ops, files = snapshot_restore_ops(idx, snap["id"], prefix, target, in_place=args.in_place)
        if not ops:
            print(f"[ERROR] The snapshot has nothing under {prefix}.")
            return 1
        j.index.queue_ops(ops)
        if not is_mounted(cfg.mount_point):
            j.flush()
        where = "to their original places (what is there now is kept as an older version)" if args.in_place \
            else f"into {target}"
        print(f"[SUCCESS] {files:,} file(s) from the snapshot of {_fmt_time(snap['at'])} restored {where}.")
        print("They appear on every running device within a few seconds.")
        return 0
    finally:
        _close(idx, crypto)


# --------------------------------------------------------------------- health
def _deleted_here(cfg, mids):
    """Which of `mids` this device's deletion log names."""
    from .uploader import DELETIONS_LOG
    mids, found = set(mids), set()
    try:
        with open(os.path.join(cfg.resolved_data_dir, DELETIONS_LOG), encoding="utf-8") as f:
            for line in f:
                parts = line.split("\t")
                if len(parts) > 2 and parts[2] in mids:
                    found.add(parts[2])
    except OSError:
        pass
    return found


def _health_why(cfg, idx, backend, mid):
    """Everything known about one piece: who uses it, whether it was listed as lost, whether this device
    deleted it, and what Discord says about it now (asked with every bot separately)."""
    from .uploader import find_deletion
    print(f"--- Piece {mid} ---")
    try:
        print(f"{'Uploaded:':20}{_fmt_time(backend.time_of(mid))}")
    except Exception:
        pass
    use = idx.piece_users(mid)
    names = {"files": "file", "versions": "earlier version", "snapshots": "snapshot copy", "protects": "spare piece of"}
    used = False
    for key, label in names.items():
        for e in use.get(key) or []:
            path = e if isinstance(e, str) else e.get("path")
            print(f"{'Used by:':20}{label} {path}")
            used = True
    if not used:
        print(f"{'Used by:':20}nothing on this drive any more")
    try:
        lost = json.loads(idx.kv_get("lost_pieces") or "{}")
    except ValueError:
        lost = {}
    if mid in lost:
        print(f"{'Listed as lost:':20}since {_fmt_time(lost[mid])}")
    line = find_deletion(cfg, mid)
    print(f"{'Deleted by DDrive:':20}" + ("YES, by this device: " + "  ".join(line.split("\t")[:1] + line.split("\t")[3:])
                                          if line else "not by this device"))
    apis = getattr(backend, "apis", None)
    if apis:
        for n, api in enumerate(apis, 1):
            try:
                msg = api.get_message(backend.channel_id, mid)
                answer = "there" + ("" if msg.get("attachments") else ", but without its attachment")
            except DiscordError as e:
                answer = ("GONE (Discord: unknown message)" if is_gone(e) else
                          f"can't tell: this bot is refused ({e})" if e.status in (401, 403) else f"can't tell ({e})")
            print(f"{f'Discord, bot {n}:':20}{answer}")
    else:
        print(f"{'Discord:':20}{'there' if backend.message_info(mid) else 'GONE'}")
    return 0


def cmd_health(args):
    """How well the drive is protected; optionally check (and repair) every piece now."""
    setup_logging(args.verbose, log_to_file=bool(args.check or args.protect))
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        from .heal import Healer
        if getattr(args, "why", None):
            return _health_why(cfg, idx, j.backend, args.why)
        st = idx.stats(max_age=0)
        pct = 100.0 * st["protected_chunks"] / st["chunks"] if st["chunks"] else 100.0
        print("--- Self-healing ---")
        if cfg.parity_enabled:
            print(f"{'Spare pieces:':20}ON: {cfg.parity_pieces} per group of {cfg.parity_group} pieces "
                  f"(any {cfg.parity_pieces} lost pieces of a group can be rebuilt)")
        else:
            print(f"{'Spare pieces:':20}OFF - pieces Discord loses can't be rebuilt")
        print(f"{'Protected:':20}{pct:.1f}% of pieces ({st['protected_chunks']:,} of {st['chunks']:,})")
        print(f"{'Stored spare:':20}{st['spare_pieces']:,} pieces, {_human(st['spare_bytes'])}")
        try:
            sc = json.loads(idx.kv_get("scrub") or "{}")
        except ValueError:
            sc = {}
        if sc.get("last_pass"):
            print(f"{'Last full check:':20}{_fmt_time(sc['last_pass'])}")
        elif sc.get("checked"):
            print(f"{'Checking:':20}{sc.get('done', 0):,} of {sc.get('total', 0):,} pieces so far")
        else:
            print(f"{'Checking:':20}not started yet (runs in the background while the drive is running)")
        if sc.get("missing"):
            print(f"{'Found missing:':20}{sc['missing']:,} (rebuilt: {sc.get('repaired', 0):,}, "
                  f"lost: {sc.get('lost', 0):,})")
        print(f"{'Snapshots:':20}{st['snapshots']:,}")
        healer = Healer(cfg, idx, j.backend, crypto=crypto)
        damaged = healer.lost_files()
        if damaged:
            print()
            print("--- Parts that can't be repaired ---")
            words_ = {"file": "this file is damaged", "version": "an earlier version can't be brought back in full",
                      "snapshot": "its copy in a snapshot is damaged", "spare": "still readable, with less protection"}
            for e in damaged[:200]:
                print(f"  [!] {e['path']}  ({e['pieces']} part(s): {words_[e['kind']]})")
            if len(damaged) > 200:
                print(f"  ... and {len(damaged) - 200} more")
            print("Bring a damaged file back from an earlier version or a snapshot, or put it on the drive again.")
            ours = _deleted_here(cfg, healer._lost())
            if ours:
                print(f"[!] {len(ours)} of these part(s) were deleted from Discord by this device "
                      f"(see {launcher()} health --why <piece>).")
        if args.protect:
            from .uploader import parity_shape
            k, _ = parity_shape(cfg, 10)
            if not k:
                print("\n[ERROR] Spare pieces are turned off (parity_enabled).")
                return 1
            print("\nAdding spare pieces to files uploaded without them (downloads each file once)...")
            done, skipped = 0, set()
            while True:
                work = idx.unprotected_slices(k, limit=1, skip=skipped)
                if not work:
                    break
                nid, slices = work[0]
                for members in slices:
                    if not healer.protect(members, parity_shape(cfg, len(members))[1]):
                        print(f"  [BAD] {idx.path_of(nid)}: part of it can't be read; skipped "
                              f"('{launcher()} verify' shows more)")
                        skipped.add(nid)
                        break
                else:
                    done += 1
                    print(f"  [OK]  {idx.path_of(nid)}")
            if not is_mounted(cfg.mount_point):
                j.flush()
            print(f"[SUCCESS] {done} file(s) protected.")
        if args.check:
            mids = idx.referenced_mids()
            print(f"\nChecking all {len(mids):,} pieces on Discord...")
            missing = rebuilt = lost = 0
            for i, mid in enumerate(mids, 1):
                if j.backend.message_info(mid) is None and idx.is_referenced(mid):
                    missing += 1
                    if healer.recover(mid) is not None:
                        rebuilt += 1
                    else:
                        lost += 1
                if i % 500 == 0:
                    print(f"  ... {i:,} of {len(mids):,} checked")
            healer.drain()
            if not is_mounted(cfg.mount_point):
                j.flush()
            if not missing:
                print(f"[OK] All {len(mids):,} pieces are on Discord.")
            else:
                print(f"[!] {missing} piece(s) were missing: {rebuilt} rebuilt and uploaded again, {lost} lost.")
                return 1 if lost else 0
        return 0
    finally:
        _close(idx, crypto)


# --------------------------------------------------------------------- sync and backup folders
def _shown_remote(cfg, remote):
    """'/Downloads' as people see it here: Z:\\Downloads on Windows, /mnt/ddrive/Downloads on Linux."""
    if sys.platform == "win32":
        return cfg.mount_point.rstrip("\\") + remote.replace("/", "\\")
    return cfg.mount_point.rstrip("/") + (remote if remote != "/" else "/")


def _ago(t):
    if not t:
        return "never"
    s = time.time() - t
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return _fmt_time(t)


def _sync_summary(res):
    parts = [(res.get("up"), "copied to the drive"), (res.get("down"), "copied here"),
             (res.get("deleted_remote"), "deleted on the drive"), (res.get("deleted_local"), "removed here"),
             (res.get("moved"), "moved off this computer"), (res.get("conflicts"), "conflict(s) kept twice"),
             (res.get("errors"), "problem(s)")]
    text = ", ".join(f"{n:,} {what}" for n, what in parts if n)
    if not text and res.get("files") == 0 and res.get("remote_files", 0) == 0:
        return "the folder is empty, so there is nothing to copy"
    return text or f"nothing to do, everything was in step ({res.get('files', 0):,} files)"


def _pick_job(jobs, which):
    """A job by its number in 'sync list', its id or its name."""
    if not which:
        print("[ERROR] Which folder? Give its number from 'sync list'.")
        return None
    w = str(which).strip()
    if w.isdigit() and 1 <= int(w) <= len(jobs):
        return jobs[int(w) - 1]
    for j in jobs:
        if w in (j["id"], j["name"]) or w.lower() == j["name"].lower():
            return j
    print(f"[ERROR] No synced folder '{w}'. See '{launcher()} sync list'.")
    return None


def _job_options(args, base):
    job = dict(base)
    for key, attr in (("mode", "mode"), ("trigger", "when"), ("every", "every"), ("at", "at"), ("name", "name")):
        if getattr(args, attr, None) is not None:
            job[key] = getattr(args, attr)
    if args.local_folder:
        job["local"] = args.local_folder
    if args.drive_folder:
        job["remote"] = args.drive_folder
    if args.exclude is not None:
        job["exclude"] = [x.strip() for e in args.exclude for x in e.split(",") if x.strip()]
    if args.when is None and args.every is not None:
        job["trigger"] = "interval"
    if args.when is None and args.at is not None:
        job["trigger"] = "daily"
    return job


def _sync_detail(cfg, sync, job, n):
    s = sync.read_status(cfg).get("jobs", {}).get(job["id"]) or {}
    print(f"--- {n}) {job['name']} ---")
    print(f"{'Mode:':16}{sync.MODES[job['mode']][0]} ({job['mode']}): {sync.MODES[job['mode']][2]}")
    print(f"{'This computer:':16}{job['local']}")
    print(f"{'Drive:':16}{_shown_remote(cfg, job['remote'])}")
    print(f"{'When:':16}{sync.when_text(job)}{'' if job['enabled'] else ' (PAUSED)'}")
    if job["exclude"]:
        print(f"{'Skips:':16}{', '.join(job['exclude'])}")
    print(f"{'Last run:':16}{_ago(s.get('last_run'))}" + (f" (took {s['duration']:.0f} s)" if s.get("duration") else ""))
    if s.get("state") == "running" and s.get("progress"):
        p = s["progress"]
        print(f"{'Now:':16}{p.get('done', 0):,} of {p.get('total', 0):,} files, "
              f"{_human(p.get('bytes', 0))} of {_human(p.get('total_bytes', 0))}  {p.get('file', '')}")
    if s.get("error"):
        print(f"[!] {s['error']}")
    elif s.get("last"):
        r = s["last"]
        print(f"{'Result:':16}{_sync_summary(r)}")
        print(f"{'Files:':16}{r.get('files', 0):,} here, {r.get('remote_files', 0):,} on the drive"
              + (f", {r['waiting']:,} still uploading to Discord" if r.get("waiting") else "")
              + (f", {r['busy']:,} busy (being written; next pass)" if r.get("busy") else "")
              + (f", {r['kept_away']:,} moved or deleted on the drive (not copied again)" if r.get("kept_away") else ""))
    for p in (s.get("problems") or [])[:20]:
        print(f"  [!] {p}")
    if s.get("history"):
        print()
        print("--- Recent runs ---")
        for h in s["history"]:
            print(f"  {_fmt_time(h['at'])}  {h.get('error') or _sync_summary(h)}")


def _sync_follow(cfg, sync, job, n, asked):
    """Show the progress of a run the drive was asked to do, until it is finished."""
    last_line, started, s = "", False, {}
    try:
        while True:
            time.sleep(0.5)
            s = sync.read_status(cfg).get("jobs", {}).get(job["id"]) or {}
            if s.get("state") == "running" and (s.get("started") or 0) >= asked - 1:
                started = True
                p = s.get("progress") or {}
                line = (f"  {p.get('done', 0):,} of {p.get('total', 0):,} files, {_human(p.get('bytes', 0))}"
                        f" of {_human(p.get('total_bytes', 0))}  {p.get('file', '')}")
                if line != last_line:
                    print(line)
                    last_line = line
            elif (s.get("last_run") or 0) >= asked:
                break
            elif not started and time.time() - asked > 30:
                print(f"[!] The drive hasn't started it yet (busy with another folder?). "
                      f"Follow it with: {launcher()} sync status {n}")
                return 0
    except KeyboardInterrupt:
        print(f"\nIt keeps running in the drive. Follow it with: {launcher()} sync status {n}")
        return 0
    if s.get("error"):
        print(f"[ERROR] {s['error']}")
        return 1
    r = s.get("last") or {}
    print(f"[OK] {_sync_summary(r)}.")
    if r.get("waiting"):
        print(f"     {r['waiting']:,} file(s) are still uploading to Discord in the background.")
    for p in (s.get("problems") or [])[:10]:
        print(f"  [!] {p}")
    return 0


def _sync_modes(sync):
    print("--- What a synced folder does (--mode) ---")
    for key, (label, _, text) in sync.MODES.items():
        print(f"  {key:16} {label}: {text}")
    print()
    print("--- When it runs (--when) ---")
    print(f"  {'live':16} As soon as something changes (default)")
    print(f"  {'interval':16} Every N minutes: --every 30 (or --every 120 for every 2 hours)")
    print(f"  {'daily':16} Once a day: --at 03:00")
    print(f"  {'manual':16} Only when you start it: {launcher()} sync run <n>")
    print()
    print(f"Always skipped: {', '.join(sync.DEFAULT_EXCLUDE)}")
    print('Skip more with --exclude "*.iso,Temp/" (names, or paths inside the folder).')


def _sync_list(cfg, sync, jobs, running):
    st = sync.read_status(cfg).get("jobs", {})
    if not jobs:
        print("No folders are synced yet. For example:")
        ex = "C:\\Users\\me\\Downloads Z:\\Downloads" if sys.platform == "win32" else "~/Documents /Documents"
        print(f"  {launcher()} sync add {ex} --mode backup")
        print(f"See '{launcher()} sync modes' for every option.")
        return 0
    for i, j in enumerate(jobs, 1):
        s = st.get(j["id"]) or {}
        arrow = {"up": "->", "down": "<-", "both": "<->"}[sync.MODES[j["mode"]][1]]
        state = "PAUSED" if not j["enabled"] else {"running": "RUNNING", "error": "PROBLEM"}.get(s.get("state"), "ON")
        print(f"  {i}) {j['name']}  [{sync.MODES[j['mode']][0]}, {sync.when_text(j)}]  {state}")
        print(f"     {j['local']}  {arrow}  {_shown_remote(cfg, j['remote'])}")
        if s.get("state") == "running" and s.get("progress"):
            p = s["progress"]
            print(f"     Now: {p.get('done', 0):,} of {p.get('total', 0):,} files"
                  + (f" ({p['file']})" if p.get("file") else ""))
        elif s.get("last_run"):
            print(f"     Last run {_ago(s['last_run'])}: {s.get('error') or _sync_summary(s.get('last') or {})}")
        else:
            print("     Not run yet.")
    print()
    if not running:
        print(f"[!] The drive isn't running, so nothing syncs right now. Start it: {launcher()} start")
    else:
        print(f"Details: {launcher()} sync status <n>    Run now: {launcher()} sync run <n>")
    return 0


def cmd_sync(args):
    """Folders kept in sync with the drive: list, add, edit, remove, run, stop, pause, resume, status, modes."""
    from . import sync
    cfg = Config.load()
    jobs = sync.load_jobs(cfg)
    action = args.action or "list"
    running = is_mounted(cfg.mount_point)
    if action == "modes":
        _sync_modes(sync)
        return 0
    if action == "list":
        return _sync_list(cfg, sync, jobs, running)
    if action == "add":
        if not args.target or not args.second:
            print(f"[ERROR] Usage: {launcher()} sync add <folder on this computer> <folder on the drive> "
                  "[--mode ...] [--when ...]   (see 'sync modes')")
            return 1
        try:
            job = sync.normalize_job(_job_options(args, dict(local=args.target, remote=args.second)), cfg, jobs)
        except sync.SyncError as e:
            print(f"[ERROR] {e}")
            return 1
        jobs.append(job)
        sync.save_jobs(jobs)
        print(f"[OK] Added {len(jobs)}) {job['name']}: {sync.MODES[job['mode']][0]}, {sync.when_text(job)}")
        print(f"     {job['local']}  ->  {_shown_remote(cfg, job['remote'])}" if sync.MODES[job["mode"]][1] != "down"
              else f"     {_shown_remote(cfg, job['remote'])}  ->  {job['local']}")
        print(f"     {sync.MODES[job['mode']][2]}")
        if not running:
            print(f"[!] It starts once the drive is running ({launcher()} start).")
        elif job["trigger"] == "manual":
            print(f"Run it with: {launcher()} sync run {len(jobs)}")
        else:
            print("The drive picks it up within a few seconds.")
        return 0

    job = _pick_job(jobs, args.target)
    if job is None:
        return 1
    n = jobs.index(job) + 1
    if action == "edit":
        try:
            new = sync.normalize_job(_job_options(args, job), cfg, jobs)
        except sync.SyncError as e:
            print(f"[ERROR] {e}")
            return 1
        jobs[n - 1] = new
        sync.save_jobs(jobs)
        print(f"[OK] {n}) {new['name']}: {sync.MODES[new['mode']][0]}, {sync.when_text(new)}")
        return 0
    if action == "remove":
        jobs.pop(n - 1)
        sync.save_jobs(jobs)
        print(f"[OK] Stopped syncing {job['name']}. Nothing was deleted: the files stay here and on the drive.")
        return 0
    if action in ("pause", "resume"):
        job["enabled"] = action == "resume"
        sync.save_jobs(jobs)
        print(f"[OK] {job['name']} is {'paused' if action == 'pause' else 'on again'}.")
        return 0
    if action == "status":
        _sync_detail(cfg, sync, job, n)
        return 0
    # run / stop
    if not running:
        print(f"[ERROR] The drive isn't running; syncing happens inside it. Start it first: {launcher()} start")
        return 1
    asked = time.time()
    sync.request_run(cfg, job["id"], cancel=action == "stop", again=action == "run" and args.again)
    if action == "stop":
        print(f"[OK] Asked {job['name']} to stop. Files copied so far stay copied.")
        return 0
    print(f"Syncing {job['name']}...")
    if args.no_wait:
        print(f"Follow it with: {launcher()} sync status {n}")
        return 0
    return _sync_follow(cfg, sync, job, n, asked)


# --------------------------------------------------------------------- password-locked folders
def _ask_password(prompt):
    import getpass
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def cmd_lock(args):
    """lock <path>: protect a folder or file with a password | unlock: show locked items on this
    device's drive | relock: hide them again | unprotect <path>: remove a lock."""
    from .locks import Locks
    setup_logging(args.verbose, log_to_file=False)
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        locks = Locks(idx, cfg)
        running = is_mounted(cfg.mount_point)
        if args.command == "relock":
            locks.mount_relock()
            print(f"[OK] Locked folders are hidden on {cfg.mount_point} again.")
            return 0
        if args.command == "unlock":
            if not locks.count():
                print(f"[!] Nothing is locked. Lock a folder with: {launcher()} lock <folder>")
                return 1
            pw = _ask_password("Password of the locked folder (input is hidden): ")
            uids = locks.matching(pw) if pw else []
            if not uids:
                print("[ERROR] No locked folder opens with that password.")
                return 1
            seconds = locks.mount_unlock(uids, args.minutes)
            for uid in uids:
                node = idx.get_by_uid(uid)
                if node is not None:
                    print(f"  {_shown_remote(cfg, idx.path_of(node['id']))}")
            until = f"for {seconds / 60:.0f} minutes" if seconds else "until the drive is stopped"
            print(f"[OK] Open on this device's drive {until}. Lock again sooner with: {launcher()} relock")
            if not running:
                print(f"[!] The drive isn't running; start it and unlock again ({launcher()} start).")
            return 0
        vpath = normalize_virtual_path(args.path, cfg.mount_point)
        node = idx.resolve(vpath)
        if node is None or vpath == "/":
            print(f"[ERROR] Not found on the drive: {args.path}" if vpath != "/" else
                  "[ERROR] Lock a folder or a file, not the whole drive.")
            return 1
        if args.command == "lock":
            if locks.is_root(node["uid"]):
                print(f"[ERROR] {vpath} already has a lock. Remove it first ({launcher()} unprotect) to change the password.")
                return 1
            pw = _ask_password("Password for it (at least 4 characters, input is hidden): ")
            if not pw or len(pw) < 4:
                print("[!] Too short; nothing locked.")
                return 1
            if _ask_password("The same password again: ") != pw:
                print("[!] The two passwords are different; nothing locked.")
                return 1
            locks.add(node, pw)
            if not running:
                j.flush()
            print(f"[OK] {vpath} is locked on every device: it is gone from the drive and the dashboard until it is")
            print(f"     unlocked with this password ({launcher()} unlock, or the padlock in the dashboard).")
            print("     There is no way to look the password up, so keep it somewhere.")
            return 0
        # unprotect
        if not locks.is_root(node["uid"]):
            print(f"[ERROR] {vpath} has no lock.")
            return 1
        if args.forgot:
            from . import words
            from .web import owner_key
            typed = _ask_password("The drive's recovery phrase (input is hidden): ") or ""
            try:
                ok = bool(owner_key(cfg)) and words.phrase_to_key(typed).hex() == owner_key(cfg)
            except ValueError as e:
                print(f"[ERROR] {e}.")
                return 1
        else:
            ok = locks.check(node["uid"], _ask_password("Password of the lock (input is hidden): ") or "")
        if not ok:
            print(("[ERROR] Those are not this drive's 24 words. Nothing changed." if args.forgot else "[ERROR] Wrong password. Nothing changed.")
                  + ("" if args.forgot else f" (Forgot it? {launcher()} unprotect <path> --forgot asks for the recovery phrase instead.)"))
            return 1
        locks.remove(node["uid"])
        if not running:
            j.flush()
        print(f"[OK] The lock on {vpath} is removed, on every device.")
        return 0
    finally:
        _close(idx, crypto)


# --------------------------------------------------------------------- web, hidden folders
def cmd_web(args):
    """Print (and open) the address of the web dashboard."""
    cfg = Config.load()
    if not cfg.web_enabled:
        print(f"[!] The web dashboard is off. Turn it on with: {launcher()} config web_enabled true")
        return 1
    url = f"http://localhost:{cfg.web_port}/"      # by name: browsers only allow passkeys there, not on 127.0.0.1
    running = is_mounted(cfg.mount_point)
    print(f"Web dashboard:  {url}")
    if cfg.web_lan:
        from .web import lan_addresses
        for ip in lan_addresses():
            print(f"On your phone:  http://{ip}:{cfg.web_port}/")
    else:
        print(f"(To open it on your phone too: {launcher()} config web_lan true, then restart the drive.)")
    if cfg.web_passkeys:
        print(f"Sign in with a passkey ({len(cfg.web_passkeys)} added), or with your recovery phrase.")
    else:
        print(f"Sign in with your 24-word recovery phrase ({launcher()} recovery-phrase shows it). There is no user name or")
        print("password. To skip typing the words next time, add a passkey under Settings -> Sign-in.")
    if cfg.signup_mode == "public":
        print("Accounts: anybody who can open the dashboard can make one (Settings -> Accounts).")
    elif cfg.signup_mode == "invite":
        print("Accounts: others can make one with an invite code from Settings -> Accounts.")
    if not running:
        print(f"[!] The drive isn't running: start it first ({launcher()} start).")
        return 0
    if args.open:
        import webbrowser
        webbrowser.open(url)
    return 0


def cmd_webdav_password(args):
    """Set the user name and password other apps use over WebDAV (they don't sign in to the dashboard)."""
    import getpass
    from .crypto import hash_password
    cfg = Config.load()
    print("For apps that open the drive over WebDAV (file managers, rclone, \"Map network drive\").")
    print("The dashboard itself is opened with your 24-word recovery phrase, never with this password.")
    try:
        current = cfg.web_user or "drive"
        user = input(f"User name [{current}]: ").strip() or current
        while True:
            pw = getpass.getpass("New password (at least 8 characters, input is hidden): ")
            if len(pw) < 8:
                print("[!] Too short; use at least 8 characters.")
                continue
            if getpass.getpass("The same password again: ") != pw:
                print("[!] The two passwords are different; try again.")
                continue
            break
    except (EOFError, KeyboardInterrupt):
        print("\n[!] Nothing changed.")
        return 1
    cfg.web_user = user
    cfg.web_password = hash_password(pw)
    cfg.save()
    print(f"[OK] Other apps now sign in as '{user}' with this password"
          + ("." if cfg.webdav_enabled else f". WebDAV is off: turn it on with {launcher()} config webdav_enabled true"))
    return 0


def cmd_purge(args):
    """Delete deleted files for good (no recovery afterwards)."""
    setup_logging(args.verbose)
    cfg = Config.load()
    idx, j, crypto = _open_journal(cfg)
    try:
        prefix = normalize_virtual_path(args.path, cfg.mount_point)
        rows = [v for v in idx.deleted_files(prefix)
                if args.all or (v["path"] or "").lower() == prefix.lower()]
        if not rows:
            print(f"[ERROR] No deleted file kept at {prefix}. See '{launcher()} deleted'."
                  + ("" if args.all else " (Add --all for everything under a folder.)"))
            return 1
        for v in rows:
            print(f"  {v['path']}  ({_fmt_size(v['size'])})")
        if not args.yes:
            try:
                ok = input(f"Delete {len(rows)} file(s) for good? They can't be recovered afterwards. [y/N]: ")
            except EOFError:
                ok = ""
            if not ok.strip().lower().startswith("y"):
                print("[!] Nothing deleted.")
                return 0
        n = idx.purge_deleted([v["uid"] for v in rows])
        if not is_mounted(cfg.mount_point):
            j.flush()
        print(f"[SUCCESS] {n} file(s) deleted for good, on every device. (Snapshots taken before still "
              "contain them until those expire.)")
        return 0
    finally:
        _close(idx, crypto)


def cmd_hide(args):
    """Hide a folder on this device only (or show it again)."""
    cfg = Config.load()
    path = normalize_virtual_path(args.path, cfg.mount_point).rstrip("/") or "/"
    if path == "/":
        print("[ERROR] Pick a folder, not the whole drive.")
        return 1
    hidden = list(cfg.hidden_folders or [])
    lower = [h.lower() for h in hidden]
    if args.command == "unhide":
        if path.lower() not in lower:
            print(f"[!] {path} isn't hidden. Hidden here: {', '.join(hidden) or 'nothing'}")
            return 1
        hidden.pop(lower.index(path.lower()))
        msg = f"[OK] {path} is shown on this device again."
    else:
        if path.lower() in lower:
            print(f"[OK] {path} is already hidden on this device.")
            return 0
        hidden.append(path)
        msg = f"[OK] {path} is hidden on this device. It stays on the drive and on your other devices."
    cfg.hidden_folders = hidden
    cfg.save()
    print(msg)
    if is_mounted(cfg.mount_point):
        print("Restart the drive (stop, then start) for this to take effect.")
    return 0


def cmd_offline(args):
    """Marks a file or folder as available offline and prefetches all chunks."""
    setup_logging(args.verbose)
    cfg = Config.load()
    if not cfg.is_configured():
        print("[ERROR] DDrive is not configured.")
        return 1

    vpath = normalize_virtual_path(args.path, cfg.mount_point)
    db_path = os.path.join(cfg.resolved_data_dir, "index.db")
    cache_dir = os.path.join(cfg.resolved_data_dir, "cache")

    idx = Index(db_path)
    node = idx.resolve(vpath)
    if not node:
        print(f"[ERROR] Path not found in DDrive: {args.path}")
        idx.close()
        return 1

    print(f"Saving '{vpath}' for offline usage...")
    idx.set_pinned(node["id"], pinned=True, recursive=True)

    crypto = get_crypto_from_cfg(cfg)
    api = DiscordAPI(cfg.bot_token)
    backend = DiscordBackend(api, cfg.channel_id, crypto=crypto)
    cache = ChunkCache(cache_dir, max_bytes=cfg.cache_max_bytes, backend=backend, index=idx, crypto=crypto)

    try:
        count = cache.prefetch_node(node["id"])
        print(f"[SUCCESS] '{vpath}' is now pinned and saved offline! ({count} chunk(s) cached)")
    finally:
        cache.shutdown()
        idx.close()
        if crypto:
            crypto.close()
    return 0


def cmd_free_space(args):
    """Evicts cached chunks from local disk to free up space."""
    setup_logging(args.verbose)
    cfg = Config.load()
    db_path = os.path.join(cfg.resolved_data_dir, "index.db")
    cache_dir = os.path.join(cfg.resolved_data_dir, "cache")

    idx = Index(db_path)
    crypto = get_crypto_from_cfg(cfg)
    api = DiscordAPI(cfg.bot_token) if cfg.is_configured() else None
    backend = DiscordBackend(api, cfg.channel_id, crypto=crypto) if api else None
    cache = ChunkCache(cache_dir, max_bytes=cfg.cache_max_bytes, backend=backend, index=idx, crypto=crypto)

    try:
        if args.all or not args.path:
            print("Freeing up local cache across all files...")
            freed = cache.free_space(node_id=None, force=args.force)
            print(f"[SUCCESS] Freed {freed / (1024 * 1024):.2f} MiB ({freed} bytes) of local cache!")
        else:
            vpath = normalize_virtual_path(args.path, cfg.mount_point)
            node = idx.resolve(vpath)
            if not node:
                print(f"[ERROR] Path not found in DDrive: {args.path}")
                return 1

            idx.set_pinned(node["id"], pinned=False, recursive=True)
            freed = cache.free_space(node_id=node["id"], force=True)
            print(f"[SUCCESS] Freed {freed / (1024 * 1024):.2f} MiB for '{vpath}'. File is now online-only!")
    finally:
        cache.shutdown()
        idx.close()
        if crypto:
            crypto.close()
    return 0


def cmd_clear_cache(args):
    """Convenience command for clearing all locally cached chunks across the drive."""
    args.all = True
    args.path = ""
    return cmd_free_space(args)


def cmd_context_menu(args):
    """Installs or uninstalls Windows Explorer right-click context menu entries."""
    if sys.platform != "win32":
        print("[ERROR] The context menu integration is only available on Windows.")
        return 1
    import winreg

    from .runtime import FROZEN, shell_command
    launch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "windows", "launch.cmd")
    run = shell_command() if FROZEN else f'"{launch}"'

    reg_entries = [
        (
            r"Software\Classes\*\shell\DDrive_Offline",
            "DDrive: Make available offline",
            f'{run} offline "%1"',
        ),
        (
            r"Software\Classes\*\shell\DDrive_FreeSpace",
            "DDrive: Free up space",
            f'{run} free-space "%1"',
        ),
        (
            r"Software\Classes\Directory\shell\DDrive_Offline",
            "DDrive: Make available offline",
            f'{run} offline "%1"',
        ),
        (
            r"Software\Classes\Directory\shell\DDrive_FreeSpace",
            "DDrive: Free up space",
            f'{run} free-space "%1"',
        ),
    ]

    from . import legacy
    legacy.old_context_menu(remove=True)
    if args.action == "uninstall":
        for subkey, _, _ in reg_entries:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey + r"\command")
            except OSError:
                pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
            except OSError:
                pass
        print("[SUCCESS] Windows Explorer context menu entries removed.")
        return 0

    # Install into HKCU (no admin privileges required)
    for subkey, title, cmd_str in reg_entries:
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, title)
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, "shell32.dll,275")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey + r"\command") as ck:
                winreg.SetValueEx(ck, "", 0, winreg.REG_SZ, cmd_str)
        except Exception as e:
            print(f"[FAIL] Could not register {subkey}: {e}")
            return 1

    print("[SUCCESS] Windows Explorer context menu installed!")
    print("You can now right-click any file or folder in Windows Explorer to:")
    print("  - 'DDrive: Make available offline'")
    print("  - 'DDrive: Free up space'")
    return 0


def main():
    theme_output()   # the same colours and style for every command (menu pages or direct)
    parser = argparse.ArgumentParser(
        prog="ddrive",
        description="DDrive: an encrypted virtual drive (Windows/Linux) backed by a private Discord channel.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    subparsers = parser.add_subparsers(dest="command")

    # mount
    p_mount = subparsers.add_parser("mount", help="Mount the virtual drive")
    p_mount.add_argument("-m", "--mount", help="Mount point or drive letter")
    p_mount.add_argument("-b", "--background", action="store_true", help="Run in background")
    p_mount.add_argument("--allow-other", action="store_true", help="Allow other users/processes to access mount")
    p_mount.add_argument("--mock", help="Test mode using local directory instead of Discord")
    p_mount.add_argument("--cache", choices=["disk", "memory"],
                         help="Where viewed files are cached: disk (default) or memory (nothing stored on disk)")

    # setup
    p_setup = subparsers.add_parser("setup", help="Configure Discord bot token and channel ID")
    p_setup.add_argument("-t", "--token", help="Discord bot token")
    p_setup.add_argument("-c", "--channel", help="Discord channel ID")
    p_setup.add_argument("-m", "--mount", help="Drive letter (e.g. Z:)")
    p_setup.add_argument("-k", "--key", help="The drive's recovery phrase (24 words, in quotes) or its key in hex")

    # status
    subparsers.add_parser("status", help="Show current status and sync statistics")
    subparsers.add_parser("mountpoint", help="Print the configured mount point (for scripts)")
    subparsers.add_parser("menu", help="Open the interactive menu (what run.bat / run.sh show)")
    subparsers.add_parser("start", help="Start the drive in the background")
    subparsers.add_parser("menu-start", help=argparse.SUPPRESS)
    subparsers.add_parser("export-key", help="Show the encryption key in hex (see also recovery-phrase)")
    p_phrase = subparsers.add_parser("recovery-phrase", aliases=["phrase"],
                                     help="Show this drive's 24-word recovery phrase (its key, as words)")
    p_phrase.add_argument("--check", action="store_true", help="Type your phrase to check you wrote it down right")
    subparsers.add_parser("approve-keys", help="Send the key to a new device that asked for it")
    p_auto = subparsers.add_parser("autostart", help="Start the drive automatically at startup: on, off or status")
    p_auto.add_argument("action", nargs="?", choices=["on", "off", "status"])
    subparsers.add_parser("request-key", help="Ask one of your other devices for the key")
    p_old = subparsers.add_parser("add-old-key", help="Add an earlier encryption key, to read data encrypted with it")
    p_old.add_argument("key", nargs="?", default="-", help="The key in hex, or - to read it from input (default)")
    p_old.add_argument("--from-config", help="Take the key(s) from another device's config.json")
    p_verify = subparsers.add_parser("verify", help="Check that files can be downloaded and decrypted")
    p_verify.add_argument("path", nargs="?", default="/", help="File or folder to check (default: everything)")
    p_verify.add_argument("--remove", action="store_true",
                          help="Remove files that can never be read (lost key, missing from Discord)")
    p_cancel = subparsers.add_parser("cancel-uploads", help="Cancel uploads that are stuck (drive must be stopped)")
    p_cancel.add_argument("--all", action="store_true", help="Cancel every pending upload without asking")
    p_log = subparsers.add_parser("log", help="Show the end of the log file")
    p_log.add_argument("-n", "--lines", type=int, default=30, help="Number of lines (default 30)")
    p_log.add_argument("--errors", action="store_true", help="Only warnings and errors")
    p_log.add_argument("--clear", action="store_true", help="Empty the log")
    p_config = subparsers.add_parser("config", help="Show or change a setting (e.g. config cache_mode memory)")
    p_config.add_argument("key", nargs="?", help="Setting name")
    p_config.add_argument("value", nargs="?", help="New value")

    # stop
    subparsers.add_parser("stop", help="Stop the background DDrive daemon and unmount")

    p_bots = subparsers.add_parser("bots", help="Extra bots for faster uploads: list, add [token], remove <n>")
    p_bots.add_argument("action", nargs="?", choices=["list", "add", "remove"])
    p_bots.add_argument("token", nargs="?", help="Bot token (add) or number (remove)")
    p_snaps = subparsers.add_parser("snapshots", help="Snapshots of the whole drive: list, create [label], delete <n>|all")
    p_snaps.add_argument("action", nargs="?", choices=["list", "create", "delete"])
    p_snaps.add_argument("label", nargs="?", help="Label (create), or number or 'all' (delete)")
    p_sr = subparsers.add_parser("snapshot-restore", help="Restore a folder (or everything) from a snapshot")
    p_sr.add_argument("number", type=int, help="Snapshot number from 'snapshots'")
    p_sr.add_argument("path", nargs="?", default="/", help="Folder or file in the snapshot (default: everything)")
    p_sr.add_argument("--to", help="Folder to restore into (default: 'Restored <date>')")
    p_sr.add_argument("--in-place", action="store_true",
                      help="Put files back where they were (current content is kept as a version)")
    p_health = subparsers.add_parser("health", help="Self-healing status; --check every piece now, --protect old files")
    p_health.add_argument("--check", action="store_true", help="Check every piece is on Discord and repair what isn't")
    p_health.add_argument("--protect", action="store_true", help="Add spare pieces to files uploaded without them")
    p_health.add_argument("--why", metavar="PIECE", help="Why one piece (message id) is missing: who deleted it, who uses it")
    p_web = subparsers.add_parser("web", help="Show the address of the web dashboard")
    p_web.add_argument("--open", action="store_true", help="Also open it in the browser")
    subparsers.add_parser("webdav-password", aliases=["web-password"],
                          help="Set the user name and password other apps use over WebDAV (not for the dashboard)")
    p_purge = subparsers.add_parser("purge", help="Delete a deleted file for good (no recovery afterwards)")
    p_purge.add_argument("path", help="Original path of the deleted file (or a folder with --all)")
    p_purge.add_argument("--all", action="store_true", help="Every deleted file under that folder")
    p_purge.add_argument("--yes", action="store_true", help="Don't ask for confirmation")
    p_sync = subparsers.add_parser(
        "sync", help="Keep folders on this computer in sync with the drive (backup, mirror, two-way, ...)",
        description="Folders on this computer kept in sync with folders on the drive, e.g. "
                    "'sync add C:\\Users\\me\\Downloads Z:\\Downloads --mode backup', 'sync list', 'sync run 1'. "
                    "'sync modes' explains every mode and trigger.")
    p_sync.add_argument("action", nargs="?", choices=["list", "add", "edit", "remove", "run", "stop", "pause",
                                                      "resume", "status", "modes"])
    p_sync.add_argument("target", nargs="?", help="add: the folder on this computer; otherwise its number from 'sync list'")
    p_sync.add_argument("second", nargs="?", help="add: the folder on the drive (e.g. Z:\\Downloads or /Downloads)")
    p_sync.add_argument("--mode", choices=["backup", "mirror", "two-way", "move", "download", "download-mirror"],
                        help="What is copied which way (default: backup)")
    p_sync.add_argument("--when", choices=["live", "interval", "daily", "manual"], help="When it runs (default: live)")
    p_sync.add_argument("--every", type=int, help="Minutes between runs (implies --when interval)")
    p_sync.add_argument("--at", help="Time of day, e.g. 03:00 (implies --when daily)")
    p_sync.add_argument("--exclude", action="append", help='Skip matching files or folders, e.g. "*.iso,Temp/"')
    p_sync.add_argument("--name", help="A name for it")
    p_sync.add_argument("--local-folder", help="edit: another folder on this computer")
    p_sync.add_argument("--drive-folder", help="edit: another folder on the drive")
    p_sync.add_argument("--no-wait", action="store_true", help="run: don't wait for it to finish")
    p_sync.add_argument("--again", action="store_true",
                        help="run: a backup copies everything again that is missing on the drive (also what you moved away)")
    p_lock = subparsers.add_parser("lock", help="Lock a folder or file with a password (hidden everywhere until unlocked)")
    p_lock.add_argument("path")
    p_unlock = subparsers.add_parser("unlock", help="Show password-locked folders on this device's drive for a while")
    p_unlock.add_argument("--minutes", type=float, help="How long (default: lock_timeout_minutes; 0 = until the drive stops)")
    subparsers.add_parser("relock", help="Hide password-locked folders on this device's drive again now")
    p_unprot = subparsers.add_parser("unprotect", help="Remove the password lock of a folder or file")
    p_unprot.add_argument("path")
    p_unprot.add_argument("--forgot", action="store_true", help="Forgot its password: ask for the drive's recovery phrase instead")
    p_hide = subparsers.add_parser("hide", help="Don't show a folder on this device")
    p_hide.add_argument("path")
    p_unhide = subparsers.add_parser("unhide", help="Show a hidden folder on this device again")
    p_unhide.add_argument("path")

    # clear-cache
    p_clear = subparsers.add_parser("clear-cache", aliases=["clearcache"], help="Clear all locally cached chunks")
    p_clear.add_argument("--force", action="store_true", help="Force evict even pinned offline files")

    # backup / restore
    subparsers.add_parser("backup", help="Manually upload index backup to Discord")
    p_restore = subparsers.add_parser("restore", help="Restore local index from Discord backup (drive must be stopped)")
    p_restore.add_argument("--force", action="store_true", help="Restore even if the drive appears to be mounted")

    # versions
    p_versions = subparsers.add_parser("versions", help="List the older versions kept for a file")
    p_versions.add_argument("path", help="File path (e.g. Z:\\Docs\\report.docx or /Docs/report.docx)")
    p_rv = subparsers.add_parser("restore-version", help="Bring back an older version of a file")
    p_rv.add_argument("path", help="File path")
    p_rv.add_argument("number", type=int, help="Version number from 'versions'")
    p_rv.add_argument("--as", dest="as_path", help="Restore as a new file instead of replacing the current one")
    p_deleted = subparsers.add_parser("deleted", help="List deleted files that can still be recovered")
    p_deleted.add_argument("path", nargs="?", default="/", help="Only show files under this folder")
    p_undelete = subparsers.add_parser("undelete", help="Recover a deleted file")
    p_undelete.add_argument("path", help="Original path of the deleted file")

    # offline / free-space
    p_offline = subparsers.add_parser("offline", aliases=["pin"], help="Save a file or folder for offline use")
    p_offline.add_argument("path", help="Path of the file or folder (e.g. Z:\\MyFolder or /MyFolder)")

    p_free = subparsers.add_parser("free-space", aliases=["unpin"], help="Free up local space by evicting cached chunks")
    p_free.add_argument("path", nargs="?", default="", help="Path of file or folder to free up")
    p_free.add_argument("--all", action="store_true", help="Free up all cached space across the drive")
    p_free.add_argument("--force", action="store_true", help="Force evict even pinned offline files")

    # context-menu
    p_menu = subparsers.add_parser("context-menu", help="Install Windows Explorer right-click menu")
    p_menu.add_argument("action", choices=["install", "uninstall"], default="install", nargs="?")

    p_acc = subparsers.add_parser("accounts", help="Accounts on the dashboard: list, disable|enable|delete <id>, "
                                                    "moves, move-cancel <id>")
    p_acc.add_argument("action", nargs="?", default="list")
    p_acc.add_argument("rest", nargs="*")

    p_lite = subparsers.add_parser("lite", add_help=False,
                                   help="Use a drive that runs on another computer: lite connect <address>, lite mount, lite stop")
    p_lite.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command == "lite":
        from .lite import main as lite_main
        return lite_main(args.rest, args.verbose)
    if args.command == "mount":
        return cmd_mount(args)
    elif args.command == "stop":
        return cmd_stop(args)
    elif args.command in ("clear-cache", "clearcache"):
        return cmd_clear_cache(args)
    elif args.command == "setup":
        return cmd_setup(args)
    elif args.command == "config":
        return cmd_config(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "log":
        return cmd_log(args)
    elif args.command == "add-old-key":
        return cmd_add_old_key(args)
    elif args.command == "cancel-uploads":
        return cmd_cancel_uploads(args)
    elif args.command == "autostart":
        return cmd_autostart(args)
    elif args.command == "approve-keys":
        return cmd_approve_keys(args)
    elif args.command == "request-key":
        return cmd_request_key(args)
    elif args.command == "export-key":
        return cmd_export_key(args)
    elif args.command in ("recovery-phrase", "phrase"):
        return cmd_recovery_phrase(args)
    elif args.command == "menu":
        from .menu import main as menu_main
        try:
            return menu_main()
        except KeyboardInterrupt:
            print()
            return 0
    elif args.command in ("start", "menu-start"):
        from .menu import menu_start
        return menu_start()
    elif args.command == "mountpoint":
        print(Config.load().mount_point)
        return 0
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "backup":
        return cmd_backup(args)
    elif args.command == "restore":
        return cmd_restore(args)
    elif args.command == "versions":
        return cmd_versions(args)
    elif args.command == "restore-version":
        return cmd_restore_version(args)
    elif args.command == "deleted":
        return cmd_deleted(args)
    elif args.command == "undelete":
        return cmd_undelete(args)
    elif args.command in ("offline", "pin"):
        return cmd_offline(args)
    elif args.command in ("free-space", "unpin"):
        return cmd_free_space(args)
    elif args.command == "context-menu":
        return cmd_context_menu(args)
    elif args.command == "bots":
        return cmd_bots(args)
    elif args.command == "snapshots":
        return cmd_snapshots(args)
    elif args.command == "snapshot-restore":
        return cmd_snapshot_restore(args)
    elif args.command == "health":
        return cmd_health(args)
    elif args.command == "web":
        return cmd_web(args)
    elif args.command in ("webdav-password", "web-password"):
        return cmd_webdav_password(args)
    elif args.command == "purge":
        return cmd_purge(args)
    elif args.command in ("hide", "unhide"):
        return cmd_hide(args)
    elif args.command == "sync":
        return cmd_sync(args)
    elif args.command in ("lock", "unlock", "relock", "unprotect"):
        return cmd_lock(args)
    elif args.command == "accounts":
        from .accountscli import main as accounts_main
        return accounts_main(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
