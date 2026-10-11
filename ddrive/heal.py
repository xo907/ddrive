"""Self-healing: rebuild pieces Discord lost from their spare pieces, and put them back.

* Reading: when a piece is missing (or damaged), the cache asks the Healer, which downloads the
  other pieces of its group plus enough spare pieces, rebuilds it (rs.py), and hands it back, so
  the application never notices.
* Repairing: the rebuilt piece is uploaded again and a "fix" operation tells every device where
  it lives now (index._op_fix). Spare pieces that went missing are recomputed the same way.
* Maintenance (one device at a time, see Index.is_leader):
    - check that every piece is still on Discord, spread over `scrub_days`, and repair what isn't,
    - add spare pieces to files uploaded before self-healing existed (`protect_existing`),
    - take automatic snapshots (`snapshot_interval_hours`),
  and on every device: keep files marked "available offline" downloaded as they change.
"""

import hashlib
import json
import logging
import queue
import secrets
import threading
import time

from .logbuf import tname
from . import codec, rs
from .crypto import AuthenticationError
from .discord_api import DiscordError

log = logging.getLogger("ddrive.heal")


class Healer:
    def __init__(self, cfg, index, backend, crypto=None, cache=None, wake=None):
        self.cfg = cfg
        self.index = index
        self.backend = backend
        self.crypto = crypto
        self.cache = cache
        self.wake = wake or (lambda: None)       # tells the journal there is something to publish
        self._lock = threading.Lock()
        self._queued = set()
        self._done = set()            # pieces this process already repaired (their fix may not be applied yet)
        self._queue = queue.Queue()
        self._thread = None
        self._running = False
        self.stats = {"rebuilt": 0, "repaired": 0, "lost": 0}
        self._lost_logged = set()
        self._lost_list = (0.0, [])       # lost_files(), kept a moment (the dashboard asks every few seconds)
        self._rechecked = {}              # lost piece -> (times looked at again, when next)

    # ------------------------------------------------------------ fetching
    def _fetch(self, mid, sha):
        """A piece's original bytes, or None if it is missing or damaged. Network errors propagate."""
        data = self.cache.peek(mid) if self.cache is not None else None
        if data is not None and (not sha or hashlib.sha256(data).hexdigest() == sha):
            return data
        try:
            payload, _ = self.backend.download(mid, None)
            data = codec.decode(payload, self.crypto, sha)
        except DiscordError as e:
            if e.status in (403, 404):
                return None
            raise
        except AuthenticationError:
            return None
        if sha and hashlib.sha256(data).hexdigest() != sha:
            return None
        return data

    # ------------------------------------------------------------ rebuilding
    def rebuild_members(self, group, missing):
        """Rebuild the missing data pieces of `group`. `missing`: message ids known to be gone.
        Returns {message_id: bytes}, or None when too many pieces are gone."""
        members, shards = group["members"], group["shards"]
        k, m = len(members), len(shards)
        missing = set(missing)
        avail = {}
        for pos, (mid, _, sha) in enumerate(members):
            if mid in missing:
                continue
            if sum(1 for x in members if x[0] in missing) > m:
                return None
            data = self._fetch(mid, sha)
            if data is None:
                missing.add(mid)
                continue
            avail[pos] = data
        lost = [pos for pos, x in enumerate(members) if x[0] in missing]
        if len(lost) > m:
            return None
        if not lost:
            return {}
        for pos in list(avail):
            if members[pos][0] in missing:       # the same piece used twice in the group
                del avail[pos]
        for j, (pmid, _, psha) in enumerate(shards):
            if len(avail) >= k:
                break
            data = self._fetch(pmid, psha)
            if data is not None:
                avail[k + j] = data
        if len(avail) < k:
            return None
        size = max(x[1] for x in members)
        out = rs.decode(k, m, avail, size)
        result = {}
        for pos in lost:
            mid, sz, sha = members[pos]
            data = out[pos][:sz]
            if sha and hashlib.sha256(data).hexdigest() != sha:
                log.error("Rebuilt piece %s does not match its checksum", mid)
                return None
            result[mid] = data
        return result

    def rebuild_shard(self, group, j):
        """Recompute spare piece j of a group from its data pieces."""
        members = group["members"]
        datas = {}
        missing = set()
        for mid, _, sha in members:
            if mid in datas or mid in missing:
                continue
            data = self._fetch(mid, sha)
            if data is None:
                missing.add(mid)
            else:
                datas[mid] = data
        if missing:
            rebuilt = self.rebuild_members(group, missing)
            if rebuilt is None:
                return None
            datas.update(rebuilt)
            for mid, data in rebuilt.items():
                self.schedule_repair(mid, data)
        enc = rs.Encoder(len(members), len(group["shards"]))
        for pos, (mid, _, _) in enumerate(members):
            enc.add(pos, datas[mid])
        return enc.parity()[j]

    def recover(self, mid):
        """Rebuild piece `mid` (data or spare) and schedule its repair. Returns its bytes or None."""
        for group in self.index.parity_groups_for(mid):
            member_mids = [x[0] for x in group["members"]]
            try:
                if mid in member_mids:
                    rebuilt = self.rebuild_members(group, {mid})
                    if not rebuilt or mid not in rebuilt:
                        continue
                    for m2, data in rebuilt.items():
                        self._remember(m2, data)
                        self.schedule_repair(m2, data)
                    with self._lock:
                        self.stats["rebuilt"] += 1
                    log.warning("Piece %s was missing from Discord; rebuilt it from its spare pieces", mid)
                    return rebuilt[mid]
                for j, s in enumerate(group["shards"]):
                    if s[0] == mid:
                        data = self.rebuild_shard(group, j)
                        if data is not None:
                            self.schedule_repair(mid, data)
                            return data
            except DiscordError as e:
                log.warning("Could not rebuild piece %s right now: %s", mid, e)
                return None
        # No spare pieces can rebuild it. Before calling it lost: one failed request is not proof, and this
        # computer may still hold the piece (it then plays here while being gone for every other device).
        try:
            data = self._read_again(mid)
            if data is None:
                data = self._from_this_computer(mid)
        except DiscordError as e:
            log.warning("Could not check piece %s right now: %s", mid, e)
            return None
        if data is not None:
            return data
        with self._lock:
            self.stats["lost"] += 1
        self._note_lost(mid)
        return None

    def _sha(self, mid):
        info = self.index.piece_info(mid)
        return info[2] if info else None

    def _read_again(self, mid):
        """The piece straight from Discord, or None if it really isn't there (or isn't readable)."""
        sha = self._sha(mid)
        try:
            payload, _ = self.backend.download(mid, None)
            data = codec.decode(payload, self.crypto, sha)
        except DiscordError as e:
            if e.status in (403, 404):
                return None
            raise
        except AuthenticationError:
            return None
        return data if not sha or hashlib.sha256(data).hexdigest() == sha else None

    def _from_this_computer(self, mid):
        """The piece from this computer's read cache, if it is still there; it is then uploaded again."""
        sha = self._sha(mid)
        data = self.cache.peek(mid) if self.cache is not None else None
        if data is None or (sha and hashlib.sha256(data).hexdigest() != sha):
            return None
        log.warning("Piece %s was missing from Discord; this computer still had it and uploads it again", mid)
        self.schedule_repair(mid, data)
        return data

    def recheck_lost(self, limit=20, now=None):
        """Look again at pieces noted as lost: one that can be read after all (the request that failed was a
        passing problem) is taken off the list, one this computer still holds is uploaded again. Each piece
        is looked at after 5 minutes, then less and less often. Returns how many were cleared or repaired."""
        now = time.time() if now is None else now
        lost = self._lost()
        done = 0
        for mid in list(lost)[:max(1, limit) * 10]:
            if done >= limit:
                break
            times, due = self._rechecked.get(mid, (0, lost[mid] + 300))
            if now < due or not self.index.is_referenced(mid):
                continue
            self._rechecked[mid] = (times + 1, now + min(6 * 3600, 300 * 2 ** (times + 1)))
            try:
                if self._read_again(mid) is not None:
                    current = self._lost()
                    current.pop(mid, None)
                    self.index.kv_set("lost_pieces", json.dumps(current, separators=(",", ":")))
                    with self._lock:
                        self._lost_logged.discard(mid)
                    self._lost_list = (0.0, [])
                    self._rechecked.pop(mid, None)
                    use = self.index.piece_users(mid)
                    names = list(dict.fromkeys(use["files"] + [v["path"] for v in use["versions"]]))[:5]
                    log.info("Piece %s can be read after all%s: it is no longer listed as damaged.", mid,
                             f" ({', '.join(names)})" if names else "")
                    done += 1
                elif self._from_this_computer(mid) is not None:
                    self._lost_list = (0.0, [])
                    done += 1
            except DiscordError as e:
                log.debug("re-checking lost pieces: %s", e)
                break
        for mid in [m for m in self._rechecked if m not in lost]:
            self._rechecked.pop(mid, None)
        return done

    # ------------------------------------------------------------ what can't be repaired
    def _lost(self):
        try:
            return json.loads(self.index.kv_get("lost_pieces") or "{}")
        except ValueError:
            return {}

    def _note_lost(self, mid):
        """A piece is gone for good: remember it, and say which files that concerns (once per piece)."""
        lost = self._lost()
        first = mid not in lost
        if first:
            self._lost_list = (0.0, [])
            lost[mid] = time.time()
            if len(lost) > 5000:
                for old in sorted(lost, key=lost.get)[:len(lost) - 5000]:
                    lost.pop(old)
            self.index.kv_set("lost_pieces", json.dumps(lost, separators=(",", ":")))
        with self._lock:
            if mid in self._lost_logged:
                return
            self._lost_logged.add(mid)
        use = self.index.piece_users(mid)

        def names(paths):
            paths = list(dict.fromkeys(paths))
            return ", ".join(paths[:8]) + (f" and {len(paths) - 8} more" if len(paths) > 8 else "")

        why = "it is gone from Discord, damaged, or encrypted with another key, and there are no (or not enough) spare pieces"
        if use["files"]:
            log.error("A part of %s can't be read and can't be rebuilt (%s). %s damaged: bring back an earlier version or a "
                      "snapshot, or put the file on the drive again from another copy.", names(use["files"]), why,
                      "This file is" if len(use["files"]) == 1 else "These files are")
        if use["versions"]:
            log.warning("A part of an earlier version of %s can't be read and can't be rebuilt; that version can't be "
                        "brought back in full.", names(v["path"] for v in use["versions"]))
        if use["snapshots"] and not use["files"]:
            log.warning("A part of %s as kept in a snapshot can't be read and can't be rebuilt.",
                        names(s["path"] for s in use["snapshots"]))
        if use["protects"] and not (use["files"] or use["versions"] or use["snapshots"]):
            log.warning("A spare piece of %s is gone and can't be rebuilt. The %s still fully readable, with less protection "
                        "than before.", names(use["protects"]), "file is" if len(use["protects"]) == 1 else "files are")
        if not any(use.values()):
            log.error("Piece %s can't be read and can't be rebuilt (%s).", mid, why)

    def lost_files(self, max_age=0.0):
        """Files (and earlier versions, and snapshot copies) with a part that can't be repaired, as they are now:
        [{"path", "kind": file | version | snapshot | spare, "pieces", "found", "at"}]. Pieces that are no longer
        used, or were repaired after all, drop out."""
        if max_age and time.time() - self._lost_list[0] < max_age:
            return self._lost_list[1]
        lost = self._lost()
        out, keep = {}, {}
        for mid, found in lost.items():
            if not self.index.is_referenced(mid):
                continue
            keep[mid] = found
            use = self.index.piece_users(mid)
            entries = [("file", p, None) for p in use["files"]] + [("version", v["path"], v["at"]) for v in use["versions"]]
            if not use["files"]:
                entries += [("snapshot", s["path"], s["at"]) for s in use["snapshots"][:1]]
            if not entries:
                entries = [("spare", p, None) for p in use["protects"]]
            for kind, path, at in entries:
                e = out.setdefault((kind, path, at), {"path": path, "kind": kind, "pieces": 0, "found": found, "at": at})
                e["pieces"] += 1
                e["found"] = min(e["found"], found)
        if len(keep) != len(lost):
            self.index.kv_set("lost_pieces", json.dumps(keep, separators=(",", ":")))
        order = {"file": 0, "version": 1, "snapshot": 2, "spare": 3}
        result = sorted(out.values(), key=lambda e: (order[e["kind"]], e["path"].lower()))
        self._lost_list = (time.time(), result)
        return result

    def _remember(self, mid, data):
        """Keep a rebuilt piece in the read cache (RAM), so reading it again needs no second rebuild."""
        if self.cache is not None:
            try:
                self.cache.put(mid, data, persist=False)
            except Exception:
                pass

    # ------------------------------------------------------------ repairing
    def schedule_repair(self, mid, data):
        with self._lock:
            if mid in self._queued or mid in self._done:
                return
            self._queued.add(mid)
        self._queue.put((mid, data))
        if not self._running:
            self.drain()

    def drain(self):
        """Repair everything queued, in this thread."""
        while True:
            try:
                mid, data = self._queue.get_nowait()
            except queue.Empty:
                return
            self._repair(mid, data)

    def _repair(self, mid, data):
        try:
            if not self.index.is_referenced(mid):
                return        # already repaired by another device, or no longer needed
            payload, _ = codec.encode(data, self.crypto, bool(getattr(self.cfg, "compression", True)))
            stored = self.backend.upload(f"chk_{secrets.token_hex(8)}.bin", payload, content="")
            self.index.queue_op({"t": "fix", "o": mid, "n": stored.message_id})
            if self.cache is not None:
                try:
                    self.cache.put(stored.message_id, data, persist=False)
                except Exception:
                    pass
            with self._lock:
                self.stats["repaired"] += 1
                self._done.add(mid)
            log.info("Repaired piece %s: uploaded again as %s", mid, stored.message_id)
            self.wake()
        except Exception as e:
            log.warning("Could not repair piece %s yet (%s); will try again later", mid, e)
            with self._lock:
                self._queued.discard(mid)
            return
        with self._lock:
            self._queued.discard(mid)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=tname("Repair"), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        while self._running:
            item = self._queue.get()
            if item is None:
                continue
            self._repair(*item)

    # ------------------------------------------------------------ protecting
    def protect(self, members, m):
        """Add spare pieces to a group of already stored pieces. Returns False if a piece is unreadable."""
        datas = []
        for mid, _, sha in members:
            data = self._fetch(mid, sha)
            if data is None:
                return False
            datas.append(data)
        shards = rs.encode(datas, m)
        out = []
        for s in shards:
            payload, _ = codec.encode(s, self.crypto, bool(getattr(self.cfg, "compression", True)))
            stored = self.backend.upload(f"chk_{secrets.token_hex(8)}.bin", payload, content="")
            out.append([stored.message_id, len(s), hashlib.sha256(s).hexdigest()])
        self.index.add_parity_group(secrets.token_hex(8), [list(x) for x in members], out)
        self.wake()
        return True


class Checker:
    """Reads every file under a folder once (like `verify`), in the background, for the dashboard:
    which files can't be read and why. Pieces that can be rebuilt from spare pieces are repaired."""

    def __init__(self, index, backend, crypto, healer):
        self.index, self.backend, self.crypto, self.healer = index, backend, crypto, healer
        self.state = {"running": False}
        self._stop = threading.Event()

    def start(self, path):
        if self.state.get("running"):
            return False
        self._stop.clear()
        self.state = {"running": True, "path": path, "files": 0, "checked": 0, "bytes": 0, "total_bytes": 0,
                      "bad": [], "repaired": 0, "started": time.time(), "finished": None, "current": ""}
        threading.Thread(target=self._run, args=(path,), name=tname("CheckFiles"), daemon=True).start()
        return True

    def stop(self):
        self._stop.set()

    def _files(self, path):
        node = self.index.resolve(path)
        out, todo = [], [node] if node else []
        while todo:
            n = todo.pop()
            if n["is_dir"]:
                todo.extend(self.index.children(n["id"]))
            elif n["state"] == "synced":
                out.append(n)
        return sorted(out, key=lambda n: self.index.path_of(n["id"]).lower())

    def _problem(self, chunk):
        try:
            payload, _ = self.backend.download(chunk["message_id"], chunk.get("url"))
            data = codec.decode(payload, self.crypto, chunk.get("sha256"))
            if chunk.get("sha256") and hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                problem = "damaged"
            else:
                return None
        except AuthenticationError:
            problem = "key"
        except DiscordError as e:
            if e.status != 404:
                raise
            problem = "missing"
        if self.healer is not None and self.healer.recover(chunk["message_id"]) is not None:
            self.state["repaired"] += 1
            return None
        return problem

    _REASONS = {"key": "Can't be decrypted: made with a different encryption key (or damaged)",
                "missing": "Part of it is gone from Discord",
                "damaged": "Part of it is damaged"}

    def _run(self, path):
        st = self.state
        try:
            files = self._files(path)
            st["files"] = len(files)
            st["total_bytes"] = sum(n["size"] for n in files)
            for n in files:
                if self._stop.is_set():
                    break
                fpath = self.index.path_of(n["id"])
                st["current"] = fpath
                problems = []
                for c in self.index.get_chunks(n["id"]):
                    if self._stop.is_set():
                        break
                    try:
                        p = self._problem(c)
                    except Exception as e:
                        p = f"error: {e}"
                    if p:
                        problems.append(p)
                    st["bytes"] += c["size"]
                if problems:
                    kind = "key" if "key" in problems else problems[0]
                    st["bad"].append({"path": fpath, "uid": n["uid"], "size": n["size"], "kind": kind,
                                      "reason": self._REASONS.get(kind, kind), "pieces": len(problems)})
                    log.warning("Check: %s can't be read (%s)", fpath, self._REASONS.get(kind, kind))
                st["checked"] += 1
            if self.healer is not None:
                self.healer.drain() if not self.healer._running else None
            log.info("Check of %s done: %d of %d file(s) can't be read%s", path, len(st["bad"]), st["checked"],
                     f", {st['repaired']} piece(s) repaired" if st["repaired"] else "")
        except Exception as e:
            st["error"] = str(e)
            log.warning("Checking files stopped: %s", e)
        finally:
            st["running"] = False
            st["current"] = ""
            st["finished"] = time.time()


class Maintenance:
    """Background chores of a running drive (see the module docstring)."""

    TICK = 2.0

    def __init__(self, cfg, index, backend, healer, uploader=None, journal=None, cache=None, device_id=""):
        self.cfg = cfg
        self.index = index
        self.backend = backend
        self.healer = healer
        self.uploader = uploader
        self.journal = journal
        self.cache = cache
        self.device_id = device_id
        self._stop = threading.Event()
        self._thread = None
        self._scrub_list = []
        self._scrub_pos = 0
        self._next_scrub = 0.0
        self._next_protect = time.time() + 60
        self._next_snapshot = time.time() + 120
        self._next_offline = time.time() + 30
        self._leader = (0.0, False)
        try:                          # files protect_step gave up on (kept across restarts)
            self._unreadable = set(json.loads(index.kv_get("unprotectable") or "[]"))
        except ValueError:
            self._unreadable = set()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name=tname("Maintenance"), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def leader(self):
        t, value = self._leader
        if time.time() - t > 300:
            value = self.index.is_leader(self.device_id)
            self._leader = (time.time(), value)
        return value

    def _loop(self):
        while not self._stop.wait(self.TICK):
            for name, fn in (("check", self.scrub_step), ("protect", self.protect_step),
                             ("snapshot", self.snapshot_step), ("offline", self.offline_step),
                             ("recheck", self.recheck_step)):
                if self._stop.is_set():
                    return
                try:
                    fn()
                except Exception as e:
                    log.debug("Background %s step failed: %s", name, e)

    def _uploads_busy(self):
        return self.uploader is not None and not self.uploader.is_idle()

    def recheck_step(self):
        """Now and then look again at the pieces listed as lost (see Healer.recheck_lost)."""
        now = time.time()
        if now < getattr(self, "_next_recheck", 0.0):
            return
        self._next_recheck = now + 60
        self.healer.recheck_lost()

    # ------------------------------------------------------------ checking
    def scrub_state(self):
        try:
            return json.loads(self.index.kv_get("scrub") or "{}")
        except ValueError:
            return {}

    def _save_scrub(self, st):
        self.index.kv_set("scrub", json.dumps(st, separators=(",", ":")))

    def scrub_step(self, force=False):
        if not getattr(self.cfg, "scrub_enabled", True) or (not force and not self.leader()):
            return
        now = time.time()
        if not force and now < self._next_scrub:
            return
        st = self.scrub_state()
        if self._scrub_pos >= len(self._scrub_list):
            if self._scrub_list:
                st["last_pass"] = now
                st["passes"] = st.get("passes", 0) + 1
            self._scrub_list = self.index.referenced_mids()
            last = st.pop("pos", None) if self._scrub_list and not self._scrub_pos else None
            self._scrub_pos = 0
            if last:
                # carry on after the piece the previous run (before a restart) stopped at
                key = (len(last), last)
                self._scrub_pos = next((i for i, m in enumerate(self._scrub_list) if (len(m), m) > key), 0)
            if not self._scrub_list:
                self._next_scrub = now + 600
                return
        days = max(0.01, float(getattr(self.cfg, "scrub_days", 7.0) or 7.0))
        self._next_scrub = now + max(1.0, days * 86400 / len(self._scrub_list))
        mid = self._scrub_list[self._scrub_pos]
        self._scrub_pos += 1
        info = self.backend.message_info(mid)     # network errors: try this piece again later
        st["pos"] = mid
        st["checked"] = st.get("checked", 0) + 1
        st["total"] = len(self._scrub_list)
        st["done"] = self._scrub_pos
        if info is None and self.index.is_referenced(mid):
            st["missing"] = st.get("missing", 0) + 1
            if self.healer.recover(mid) is not None:
                st["repaired"] = st.get("repaired", 0) + 1
            else:
                st["lost"] = st.get("lost", 0) + 1
        self._save_scrub(st)

    # ------------------------------------------------------------ protecting
    def protect_step(self):
        from .uploader import parity_shape
        now = time.time()
        if now < self._next_protect:
            return
        self._next_protect = now + 20
        if not (getattr(self.cfg, "protect_existing", True) and getattr(self.cfg, "parity_enabled", True)):
            return
        if not self.leader() or self._uploads_busy():
            return
        k, _ = parity_shape(self.cfg, 10)
        if not k:
            return
        work = self.index.unprotected_slices(k, limit=1, skip=self._unreadable)
        if not work:
            self._next_protect = now + 600
            return
        nid, slices = work[0]
        for members in slices:
            if self._stop.is_set() or self._uploads_busy():
                return
            _, m = parity_shape(self.cfg, len(members))
            if not self.healer.protect(members, m):
                log.warning("Could not add spare pieces to %s: part of it can't be read. 'verify' shows what is "
                            "wrong with it; it isn't tried again.", self.index.path_of(nid))
                self._unreadable.add(nid)
                self.index.kv_set("unprotectable", json.dumps(sorted(self._unreadable)))
                return
        log.info("Added spare pieces to %s", self.index.path_of(nid))
        self._next_protect = time.time()

    # ------------------------------------------------------------ snapshots
    def snapshot_step(self):
        now = time.time()
        if now < self._next_snapshot:
            return
        self._next_snapshot = now + 600
        hours = float(getattr(self.cfg, "snapshot_interval_hours", 0) or 0)
        if hours <= 0 or self.journal is None or not self.leader():
            return
        last = max(self.index.last_snapshot_time(), float(self.index.kv_get("snapshot_posted") or 0))
        if now - last < hours * 3600:
            return
        if self.index.stats()["files"] == 0:
            return
        self.journal.create_snapshot("Automatic", float(getattr(self.cfg, "snapshot_keep_days", 14) or 0))

    # ------------------------------------------------------------ offline files
    def offline_step(self):
        now = time.time()
        if now < self._next_offline or self.cache is None:
            return
        self._next_offline = now + 60
        for nid in self.index.pinned_roots():
            if self._stop.is_set():
                return
            self.cache.prefetch_node(nid)
