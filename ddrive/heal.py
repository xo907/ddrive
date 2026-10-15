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
import os
import logging
import queue
import secrets
import threading
import time

from .logbuf import tname
from . import codec, rs
from concurrent.futures import ThreadPoolExecutor

from .channels import Layout, channel_of, layout_of, split
from .crypto import AuthenticationError
from .discord_api import DiscordError, is_gone

log = logging.getLogger("ddrive.heal")

UNPROTECTABLE_RETRY = 7 * 86400     # files whose spare pieces couldn't be made are tried again after this
FETCH_AT_ONCE = 4                   # pieces a rebuild downloads at the same time
SPARE_BATCH = 8                     # spare pieces computed (and held in memory) at a time
SPREAD_RESCAN = 6 * 3600            # what still needs spare pieces is looked for again after this (or a change)
PAGE = 100                          # messages per request when the background check lists a channel
BACKUP_EVERY = 86400                # a local copy of index.db is made this often...
BACKUPS_KEPT = 7                    # ...and this many are kept (in <data>/backups)
REHOME_AT_ONCE = 20                 # pieces of a given-up channel rebuilt per step


def _as_layout(chans):
    return chans if isinstance(chans, Layout) else Layout(chans)


def needed_spares(mids, want):
    """Spare pieces a group with places `mids` needs so that any `want` lost messages can be rebuilt: a
    message that fills several places (repeated content stored once, before that was prevented) takes
    them all when it goes."""
    times = {}
    for mid in mids:
        times[mid] = times.get(mid, 0) + 1
    return sum(sorted(times.values(), reverse=True)[:want])


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
            if is_gone(e):
                return None
            raise          # refused (403), wrong channel, Discord busy: not proof the piece is gone
        except AuthenticationError:
            return None
        if sha and hashlib.sha256(data).hexdigest() != sha:
            return None
        return data

    # ------------------------------------------------------------ rebuilding
    def _fetch_all(self, items, fetch=None):
        """[(mid, sha)] -> [bytes or None], FETCH_AT_ONCE at a time (a rebuild waits for the slowest
        piece instead of the sum of all of them). Errors propagate as with `fetch`."""
        fetch = fetch or self._fetch
        if len(items) <= 1:
            return [fetch(m, s) for m, s in items]
        with ThreadPoolExecutor(max_workers=min(FETCH_AT_ONCE, len(items)), thread_name_prefix=tname("Rebuild")) as pool:
            return list(pool.map(lambda x: fetch(*x), items))

    def _fetch_soft(self, mid, sha):
        """_fetch, with a piece that can't be asked about (its channel refuses access) counted as missing."""
        try:
            return self._fetch(mid, sha)
        except DiscordError:
            return None

    def rebuild_members(self, group, missing, soft=False):
        """Rebuild the missing data pieces of `group`. `missing`: message ids known to be gone.
        Returns {message_id: bytes}, or None when too many pieces are gone. `soft`: pieces that can't
        be asked about are left out instead of stopping (only for reading, see rebuild_for_read)."""
        fetch = self._fetch_soft if soft else self._fetch
        members, shards = group["members"], group["shards"]
        k, m = len(members), len(shards)
        missing = set(missing)
        avail = {}
        if sum(1 for x in members if x[0] in missing) > m:
            return None
        todo = list(dict.fromkeys((mid, sha) for mid, _, sha in members if mid not in missing))
        got = dict(zip([t[0] for t in todo], self._fetch_all(todo, fetch)))
        for pos, (mid, _, sha) in enumerate(members):
            if mid in missing:
                continue
            if got.get(mid) is None:
                missing.add(mid)
                continue
            avail[pos] = got[mid]
        del got
        lost = [pos for pos, x in enumerate(members) if x[0] in missing]
        if len(lost) > m:
            return None
        if not lost:
            return {}
        for pos in list(avail):
            if members[pos][0] in missing:       # the same piece used twice in the group
                del avail[pos]
        j = 0
        while len(avail) < k and j < len(shards):         # only as many spare pieces as are still needed
            batch = shards[j:j + (k - len(avail))]
            for t, data in enumerate(self._fetch_all([(s[0], s[2]) for s in batch], fetch)):
                if data is not None:
                    avail[k + j + t] = data
            j += len(batch)
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
        todo = list(dict.fromkeys((mid, sha) for mid, _, sha in members))
        for (mid, _), data in zip(todo, self._fetch_all(todo)):
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
        enc = rs.Encoder(len(members), len(group["shards"]), [j])    # only the one that is missing
        for pos, (mid, _, _) in enumerate(members):
            enc.add(pos, datas[mid])
        return enc.parity()[0]

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

    def rebuild_for_read(self, mid):
        """Piece `mid` rebuilt from the rest of its group, only to be read: nothing is uploaded and nothing
        is counted lost (its channel refused access; the piece itself may well still be there)."""
        for group in self.index.parity_groups_for(mid):
            if mid not in [x[0] for x in group["members"]]:
                continue
            try:
                rebuilt = self.rebuild_members(group, {mid}, soft=True)
            except DiscordError:
                continue
            if rebuilt and mid in rebuilt:
                self._remember(mid, rebuilt[mid])
                return rebuilt[mid]
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
            if is_gone(e):
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

    def _recheck_one(self, mid):
        """Look at one listed piece again: "readable" (Discord has it after all: off the list), "repaired"
        (this computer still held it: being uploaded again) or None (still missing). Network errors propagate."""
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
            return "readable"
        if self._from_this_computer(mid) is not None:
            self._lost_list = (0.0, [])
            return "repaired"
        return None

    recheck = None        # the dashboard's "Check again": how far it is, and what the last one found

    def recheck_all(self, sync=None, rounds=6, pause=3.0):
        """"Check again", to the end: every listed piece is looked at, and the ones that could not be asked
        about (Discord was busy) or whose upload failed are tried again, until a round changes nothing.
        `sync()` applies what was repaired (the journal). Progress is in `self.recheck`."""
        st = self.recheck = {"running": True, "started": time.time(), "total": 0, "checked": 0, "readable": 0,
                             "repaired": 0, "left": None, "round": 0}
        try:
            fixed_before = set()
            for n in range(rounds):
                pending = [m for m in self._lost() if self.index.is_referenced(m) and m not in fixed_before]
                st.update(round=n + 1, total=len(pending), checked=0)
                if not pending:
                    break
                retry = 0
                for mid in pending:
                    how = None
                    try:
                        how = self._recheck_one(mid)
                    except DiscordError as e:
                        retry += 1                    # not an answer: ask again in the next round
                        log.debug("checking piece %s again: %s", mid, e)
                    st["checked"] += 1
                    if how:
                        st[how] += 1
                        fixed_before.add(mid)
                if not self._running:
                    self.drain()
                end = time.time() + 600               # uploads of the repaired pieces, at Discord's pace
                while self._queued and time.time() < end:
                    time.sleep(0.2)
                with self._lock:                      # an upload that failed: its piece is tried again
                    failed = {m for m in fixed_before if m not in self._done and m in self._lost()
                              and self.index.is_referenced(m) and self.cache is not None and self.cache.peek(m) is not None}
                if failed:
                    st["repaired"] -= len(failed)
                    fixed_before -= failed
                    retry += len(failed)
                if sync is not None:
                    try:
                        sync()
                    except Exception as e:
                        log.debug("after re-checking: %s", e)
                if not retry:
                    break                             # everything got an answer: nothing a further round would change
                time.sleep(pause)
            self._lost_list = (0.0, [])
            st["left"] = len(self.lost_files())
            log.info("Parts listed as lost were checked again: %d can be read after all, %d uploaded again from this "
                     "computer, %d file(s) or copies still have a missing part.", st["readable"], st["repaired"], st["left"])
        finally:
            st.update(running=False, finished=time.time())
        return st

    def recheck_start(self, sync=None):
        """Run recheck_all in the background (once at a time). False when one is already running."""
        with self._lock:
            if self.recheck and self.recheck.get("running"):
                return False
            self.recheck = {"running": True, "started": time.time(), "total": 0, "checked": 0, "readable": 0, "repaired": 0,
                            "left": None, "round": 0}
        threading.Thread(target=self.recheck_all, kwargs={"sync": sync}, name=tname("CheckAgain"), daemon=True).start()
        return True

    def recheck_lost(self, limit=20, now=None, force=False, report=None):
        """Look again at pieces noted as lost: one that can be read after all (the request that failed was a
        passing problem) is taken off the list, one this computer still holds is uploaded again. Each piece
        is looked at after 5 minutes, then less and less often; `force` looks at all of them now (the
        dashboard's "Check again"). Returns how many were cleared or repaired; `report`, a dict, gets
        "readable" and "repaired" counted separately."""
        now = time.time() if now is None else now
        lost = self._lost()
        done = 0
        report = {} if report is None else report
        report.update(readable=0, repaired=0)
        for mid in list(lost)[:max(1, limit) * 10]:
            if done >= limit:
                break
            times, due = self._rechecked.get(mid, (0, lost[mid] + 300))
            if (now < due and not force) or not self.index.is_referenced(mid):
                continue
            self._rechecked[mid] = (times + 1, now + min(6 * 3600, 300 * 2 ** (times + 1)))
            try:
                how = self._recheck_one(mid)
            except DiscordError as e:
                log.debug("re-checking lost pieces: %s", e)
                break
            if how:
                done += 1
                report[how] += 1
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

    def forget_lost(self):
        """Empty the list of lost pieces. Nothing is repaired by this: a piece that is still missing is
        listed again the next time it is read or checked. Returns how many were listed."""
        n = len(self._lost())
        self.index.kv_set("lost_pieces", "{}")
        with self._lock:
            self._lost_logged.clear()
        self._rechecked.clear()
        self._lost_list = (0.0, [])
        return n

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
            stored = self._upload_near(mid, payload)
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

    def _put(self, payload, channel):
        name = f"chk_{secrets.token_hex(8)}.bin"
        if channel is None:
            return self.backend.upload(name, payload, content="")
        return self.backend.upload(name, payload, content="", channel=channel)

    def _upload_near(self, mid, payload):
        """Upload a rebuilt piece into the channel it was in. If that channel can't take it (deleted, or
        the bot is refused there), into the others in turn, so a lost channel doesn't stop repairs."""
        lay = layout_of(self.backend)
        if channel_of(mid) in lay.chans:
            first = channel_of(mid)
        else:            # its channel was given up: where the rest of its group has fewest pieces
            others = [x for g in self.index.parity_groups_for(mid) for x in
                      [y[0] for y in g["members"]] + [y[0] for y in g["shards"]] if x != mid]
            first = lay.fill(others, 1)[0] if len(lay.chans) > 1 else None
        order = [first] + [c for c in lay.chans if c != first]
        for n, chan in enumerate(order):
            try:
                return self._put(payload, chan)
            except DiscordError as e:
                if e.status not in (403, 404) or n == len(order) - 1:
                    raise
                log.warning("Could not put piece %s back into %s (%s); trying another channel", mid,
                            f"spare channel {chan}" if chan else "the main channel", e)

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
        datas = self._fetch_all([(mid, sha) for mid, _, sha in members])
        if any(d is None for d in datas):
            return False
        lay = layout_of(self.backend)
        where = lay.fill([x[0] for x in members], m) if len(lay.chans) > 1 else [None] * m
        out = self._make_spares(datas, len(members), m, range(m), where)
        self.index.add_parity_group(secrets.token_hex(8), [list(x) for x in members], out)
        self.wake()
        return True

    def _make_spares(self, datas, k, m, rows, where, check=None):
        """Compute spare pieces `rows` of a group of k pieces (`datas`) SPARE_BATCH at a time and upload
        them to the channels `where` (one per row, in order), several at once. `check`: {row: sha} of
        spare pieces already stored, computed again to make sure they still match (ValueError if not).
        Returns [[mid, size, sha], ...] for the rows that were not only checked."""
        rows = list(rows)
        todo = list(rows) + [j for j in sorted(check or {}) if j not in rows]
        out = []
        compress = bool(getattr(self.cfg, "compression", True))
        for first in range(0, len(todo), SPARE_BATCH):
            batch = todo[first:first + SPARE_BATCH]
            enc = rs.Encoder(k, m, batch)
            for pos, data in enumerate(datas):
                enc.add(pos, data)
            made = dict(zip(batch, enc.parity()))
            del enc
            for j in batch:
                if check and j in check and check[j] and hashlib.sha256(made[j]).hexdigest() != check[j]:
                    raise ValueError("the stored spare pieces don't match the pieces")
            put = [j for j in batch if j in rows]

            def up(j, made=made):
                payload, _ = codec.encode(made[j], self.crypto, compress)
                stored = self._put(payload, where[rows.index(j)])
                return [stored.message_id, len(made[j]), hashlib.sha256(made[j]).hexdigest()]

            if put:
                with ThreadPoolExecutor(max_workers=min(FETCH_AT_ONCE, len(put)), thread_name_prefix=tname("Spare")) as pool:
                    out += list(pool.map(up, put))
            del made
        return out

    @staticmethod
    def spread_shape(mids, k, m, chans, want=0):
        """(spare pieces the group needs in all, channels of the ones to add): at least `want`, and with
        several channels (or servers) enough that no channel or server holds more of it than it has spare
        pieces, i.e. any one of them can be lost. None if that can't be done."""
        lay = _as_layout(chans)
        start = min(max(m, want), rs.MAX_PIECES - k)
        if len(lay.chans) < 2:
            return (start, [None] * (start - m)) if start >= m else (m, [])
        for m2 in range(max(m, start), rs.MAX_PIECES - k + 1):
            extra = lay.fill(list(mids), m2 - m)
            if lay.worst(list(mids) + [f"{c}-x{n}" if c else f"x{n}" for n, c in enumerate(extra)]) <= m2:
                return m2, extra
        return None

    def spread(self, group, chans, want=0):
        """Give a stored group more spare pieces: at least `want` (the settings were raised since it was
        stored), and with spare channels enough, in the channels holding fewest of it, to survive losing any
        one channel. Its spare pieces so far are kept: a spare piece's content depends only on its place, so
        they are the first ones of the larger group. Recorded as a new group over the same pieces. False if a
        piece can't be read now."""
        members, shards = group["members"], group["shards"]
        k, m = len(members), len(shards)
        shape = self.spread_shape([x[0] for x in members] + [x[0] for x in shards], k, m, chans, want)
        if shape is None:
            return False
        m2, where = shape
        if m2 == m:
            return True
        datas = self._fetch_all([(mid, sha) for mid, _, sha in members])
        if any(d is None for d in datas):
            return False
        try:   # the first stored spare piece is made again alongside, to be sure the old ones fit
            new = self._make_spares(datas, k, m2, range(m, m2), where, check={0: shards[0][2]} if shards else None)
        except ValueError:
            log.warning("The spare pieces of group %s don't match its pieces; not spreading it", group.get("gid"))
            return False
        del datas
        out = [list(s) for s in shards] + new
        self.index.add_parity_group(secrets.token_hex(8), [list(x) for x in members], out)
        self.wake()
        return True


def confirm_unreadable(index, backend, crypto, nid, healer=None):
    """Ask Discord again, piece by piece, before a file is removed as unreadable: True only when a
    piece is proven gone (404) or can't be decrypted or doesn't match its checksum, and (with `healer`)
    its spare pieces can't rebuild it either. A refused bot, a busy Discord or a network error proves
    nothing, and then the file is kept."""
    for chunk in index.get_chunks(nid):
        mid = chunk["message_id"]
        try:
            payload, _ = backend.download(mid, None)
            data = codec.decode(payload, crypto, chunk.get("sha256"))
            broken = bool(chunk.get("sha256")) and hashlib.sha256(data).hexdigest() != chunk["sha256"]
        except AuthenticationError:
            broken = True
        except DiscordError as e:
            if not is_gone(e):
                return False
            broken = True
        except Exception:
            return False
        if not broken:
            continue
        if healer is None:
            return True
        for group in index.parity_groups_for(mid):
            if mid not in [x[0] for x in group["members"]]:
                continue
            try:
                rebuilt = healer.rebuild_members(group, {mid})
            except Exception:
                return False                  # couldn't ask about the rest of its group: proves nothing
            if rebuilt and mid in rebuilt:
                healer.schedule_repair(mid, rebuilt[mid])
                break                         # it can be rebuilt (and is being put back): not lost
        else:
            return True
    return False


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
            if not is_gone(e):
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
        self._next_spread = time.time() + 90
        self._spread_todo = []
        self._listing = None
        self._next_rehome = time.time() + 30
        self._next_backup = time.time() + 300
        self._servers_learned = False
        self._spread_shape, self._spread_scanned = None, 0.0
        self.spread_state = {}
        self._leader = (0.0, False)
        try:                          # files protect_step gave up on (kept across restarts)
            self._unreadable = set(json.loads(index.kv_get("unprotectable") or "[]"))
        except ValueError:
            self._unreadable = set()
        if self._unreadable and not index.kv_get("unprotectable_since"):
            index.kv_set("unprotectable_since", str(time.time()))

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
            for name, fn in (("servers", self.servers_step), ("check", self.scrub_step),
                             ("rehome", self.rehome_step), ("protect", self.protect_step),
                             ("spread", self.spread_step), ("backup", self.backup_step),
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

    def servers_step(self):
        """Once: learn which server each channel is in, so groups are spread over servers."""
        if self._servers_learned:
            return
        self._servers_learned = True
        learn = getattr(self.backend, "learn_servers", None)
        if learn is not None:
            learn(self.cfg)

    def rehome_step(self):
        """Pieces in spare channels that were given up (`spare lost`): rebuild them from the rest of their
        groups and put them in the channels that are left, a few at a time, without waiting for the
        background check to come by."""
        lost = sorted(getattr(self.backend, "lost", ()) or ())
        now = time.time()
        if not lost or now < self._next_rehome or not self.leader():
            return
        self._next_rehome = now + 5
        for chan in lost:
            todo = self.index.pieces_in(chan, REHOME_AT_ONCE)
            if not todo:
                continue
            for mid in todo:
                if self._stop.is_set():
                    return
                if self.healer.recover(mid) is None:
                    log.warning("A piece of given-up channel %s can't be rebuilt now (see Health)", chan)
            self.healer.drain() if not self.healer._running else None
            return
        if not getattr(self, "_rehome_said", False):
            self._rehome_said = True
            log.info("Every piece of the given-up channel(s) has been rebuilt into the others")
        self._next_rehome = now + 600

    def backup_step(self):
        """Once a day: a copy of this computer's index.db in <data>/backups (7 kept), and SQLite's own check
        of it. A damaged file list is said loudly, while good copies of it still exist."""
        now = time.time()
        if now < self._next_backup:
            return
        self._next_backup = now + 3600
        folder = os.path.join(os.path.dirname(os.path.abspath(self.index.path)), "backups")
        try:
            os.makedirs(folder, exist_ok=True)
            names = sorted(n for n in os.listdir(folder) if n.startswith("index-") and n.endswith(".db.gz"))
            if names and now - os.path.getmtime(os.path.join(folder, names[-1])) < BACKUP_EVERY:
                return
            verdict = self.index.backup_to(os.path.join(folder, time.strftime("index-%Y%m%d-%H%M%S.db.gz")))
            if verdict != "ok":
                log.error("This computer's file list (index.db) is damaged (%s). The drive goes on, but restore it: "
                          "stop the drive and put back a copy from %s (or start with an empty data folder to "
                          "take the newest checkpoint from Discord).", verdict, folder)
            names = sorted(n for n in os.listdir(folder) if n.startswith("index-") and n.endswith(".db.gz"))
            for old in names[:-BACKUPS_KEPT]:
                os.remove(os.path.join(folder, old))
        except Exception as e:
            log.warning("Could not make the daily copy of the file list: %s", e)

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
        """One step of the background check. Where the backend can list a channel, it reads the channel 100
        messages at a time and compares with the pieces the drive expects there (a hundred times fewer
        requests than asking about each piece); each piece missing from the list is asked about on its own
        before anything is repaired. Elsewhere (accounts' drives), it asks about one piece per step."""
        if not getattr(self.cfg, "scrub_enabled", True) or (not force and not self.leader()):
            return
        now = time.time()
        if not force and now < self._next_scrub:
            return
        if hasattr(self.backend, "messages_in"):
            return self._scrub_listing(now)
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
        days = max(0.01, float(getattr(self.cfg, "scrub_days", 2.0) or 2.0))
        self._next_scrub = now + max(1.0, days * 86400 / len(self._scrub_list))
        mid = self._scrub_list[self._scrub_pos]
        self._scrub_pos += 1
        info = self.backend.message_info(mid)     # network errors: try this piece again later
        st["pos"] = mid
        st["checked"] = st.get("checked", 0) + 1
        st["total"] = len(self._scrub_list)
        st["done"] = self._scrub_pos
        if info is None and self.index.is_referenced(mid):
            self._scrub_missing(mid, st)
            self._scrub_group_of(mid, st)
        self._save_scrub(st)

    def _scrub_listing(self, now):
        st = self.scrub_state()
        L = self._listing
        if L is None:
            lay = layout_of(self.backend)
            expected = {}
            for mid in self.index.referenced_mids():
                chan, raw = split(mid)
                if chan in lay.chans:            # (given-up channels are rebuilt by rehome_step)
                    expected.setdefault(chan, set()).add(raw)
            total = sum(len(v) for v in expected.values())
            if not total:
                self._next_scrub = now + 600
                return
            L = self._listing = {"chans": [c for c in lay.chans if c in expected], "ci": 0, "after": "0",
                                 "seen": set(), "expected": expected, "missing": [], "total": total, "done": 0}
        days = max(0.01, float(getattr(self.cfg, "scrub_days", 2.0) or 2.0))
        self._next_scrub = now + max(self.TICK, days * 86400 / max(1, L["total"] / PAGE))
        if L["missing"]:                       # pieces the list didn't have: asked about one by one
            for mid in L["missing"][:20]:
                with self.healer._lock:
                    repairing = mid in self.healer._queued or mid in self.healer._done
                if repairing:                  # already put back (its new place isn't in the index yet)
                    continue
                if self.backend.message_info(mid) is None and self.index.is_referenced(mid):
                    self._scrub_missing(mid, st)
                    self._scrub_group_of(mid, st)
                st["checked"] = st.get("checked", 0) + 1
            del L["missing"][:20]
            self._save_scrub(st)
            self._next_scrub = now
            return
        if L["ci"] >= len(L["chans"]):
            st["last_pass"] = now
            st["passes"] = st.get("passes", 0) + 1
            st["done"] = st["total"] = L["total"]
            self._save_scrub(st)
            self._listing = None
            return
        chan = L["chans"][L["ci"]]
        page = self.backend.messages_in(chan, L["after"], PAGE)     # network errors: this page again later
        want = L["expected"][chan]
        for m in page:
            raw = str(m["id"])
            if m.get("attachments") and raw in want:
                L["seen"].add(raw)
        if page:
            L["after"] = str(max(int(m["id"]) for m in page))
        st["checked"] = st.get("checked", 0) + sum(1 for m in page if str(m["id"]) in want)
        st["total"] = L["total"]
        st["done"] = L["done"] + len(L["seen"])
        if len(page) < PAGE:                   # the end of this channel
            gone = sorted(want - L["seen"], key=int)
            L["missing"] = [raw if chan is None else f"{chan}-{raw}" for raw in gone]
            L["done"] += len(want)
            L["ci"] += 1
            L["after"], L["seen"] = "0", set()
            self._next_scrub = now
        self._save_scrub(st)

    def _scrub_missing(self, mid, st):
        st["missing"] = st.get("missing", 0) + 1
        if self.healer.recover(mid) is not None:
            st["repaired"] = st.get("repaired", 0) + 1
        else:
            st["lost"] = st.get("lost", 0) + 1

    def _scrub_group_of(self, mid, st):
        """One piece of a group is gone: check the rest of its group now rather than days later, while
        its spare pieces can still rebuild everything (losses often come together)."""
        seen = {mid}
        for group in self.index.parity_groups_for(mid):
            for other in [x[0] for x in group["members"]] + [x[0] for x in group["shards"]]:
                if other in seen or self._stop.is_set():
                    continue
                seen.add(other)
                if self.backend.message_info(other) is None and self.index.is_referenced(other):
                    self._scrub_missing(other, st)

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
        if self._unreadable and now - float(self.index.kv_get("unprotectable_since") or now) > UNPROTECTABLE_RETRY:
            # pieces that couldn't be read may be readable now (Discord was refused or busy): try again
            log.info("Trying again to add spare pieces to %d file(s) that could not be read before", len(self._unreadable))
            self._unreadable.clear()
            self.index.kv_set("unprotectable", "[]")
            self.index.kv_set("unprotectable_since", "")
        chans = layout_of(self.backend).places
        k, _ = parity_shape(self.cfg, 10, chans)
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
            _, m = parity_shape(self.cfg, len(members), chans)
            if not self.healer.protect(members, m):
                log.warning("Could not add spare pieces to %s: part of it can't be read. 'verify' shows what is "
                            "wrong with it; it is tried again in a week.", self.index.path_of(nid))
                self._unreadable.add(nid)
                self.index.kv_set("unprotectable", json.dumps(sorted(self._unreadable)))
                if not self.index.kv_get("unprotectable_since"):
                    self.index.kv_set("unprotectable_since", str(time.time()))
                return
        log.info("Added spare pieces to %s", self.index.path_of(nid))
        self._next_protect = time.time()

    # ------------------------------------------------------------ spare channels
    def wanted(self, n, chans):
        """Spare pieces the settings ask for a group of n pieces."""
        from .uploader import parity_shape
        return parity_shape(self.cfg, n, _as_layout(chans).places)[1]

    def need(self, group_mids, n, chans):
        """Spare pieces a group (its places `group_mids`, n data pieces) needs: what the settings ask for,
        counted in messages (a message filling several places costs them all, see needed_spares)."""
        return needed_spares(group_mids, self.wanted(n, chans))

    def groups_to_spread(self, chans):
        """Groups with fewer spare pieces than the settings ask for, or (with spare channels) that losing one
        channel or server would break, and that no other group over the same pieces covers."""
        lay = _as_layout(chans)
        groups = self.index.all_groups()
        # A group some of whose pieces are no longer used (an earlier version expired, keeping only the pieces
        # the file still shares with it) is not worth strengthening: those pieces are in the newer version's own
        # groups, and the released ones can't be read to compute anything (it would be tried again and again).
        used = self.index.data_mids()
        groups = {gid: g for gid, g in groups.items() if all(x[0] in used for x in g[0])}

        def strong(mem, sh):
            mids = [x[0] for x in mem] + [x[0] for x in sh]
            if len(sh) < self.need(mids, len(mem), lay):
                return False
            return len(lay.chans) < 2 or lay.worst(mids) <= len(sh)

        safe = {gid for gid, (mem, sh) in groups.items() if strong(mem, sh)}
        by_member = {}
        for gid in safe:
            for x in groups[gid][0]:
                by_member.setdefault(x[0], set()).add(gid)
        todo = []
        for gid, (mem, sh) in groups.items():
            if gid in safe or not mem:
                continue
            need = {x[0] for x in mem}
            if any(need <= {x[0] for x in groups[h][0]} for h in by_member.get(mem[0][0], ())):
                continue
            todo.append(gid)
        self.spread_state = {"groups": len(groups), "left": len(todo), "checked": time.time()}
        return todo

    def spread_step(self):
        """One group at a time (on the leader, while nothing uploads), give groups stored with fewer spare
        pieces than the settings now ask for, or stored before a spare channel was added, the extra spare
        pieces they need (Healer.spread)."""
        lay = layout_of(self.backend)
        now = time.time()
        if now < self._next_spread:
            return
        self._next_spread = now + 5
        if not (getattr(self.cfg, "parity_enabled", True) and getattr(self.cfg, "protect_existing", True)):
            return
        if not self.leader() or self._uploads_busy():
            return
        if not self._spread_todo:
            # Looking through every group is not free on a big drive: only when something that decides how
            # many spare pieces a group needs has changed, or now and then.
            shape = (getattr(self.cfg, "parity_group", 10), getattr(self.cfg, "parity_pieces", 2),
                     tuple(lay.chans), tuple(lay.server[c] for c in lay.chans))
            if shape == self._spread_shape and now - self._spread_scanned < SPREAD_RESCAN:
                self._next_spread = now + 60
                return
            self._spread_shape, self._spread_scanned = shape, now
            self._spread_todo = self.groups_to_spread(lay)
            if not self._spread_todo:
                self._next_spread = now + 600
                return
            log.info("Adding spare pieces to %d group(s) stored with fewer than the settings ask for", len(self._spread_todo))
        gid = self._spread_todo.pop()
        group = self.index.parity_group(gid)
        if group is None:
            return
        mids = [x[0] for x in group["members"]] + [x[0] for x in group["shards"]]
        if self.healer.spread(group, lay, self.need(mids, len(group["members"]), lay)):
            self.spread_state["left"] = len(self._spread_todo)
            self._next_spread = time.time()
        else:
            log.info("Could not add spare pieces to a group of %s yet (a piece can't be read now); trying again later",
                     ", ".join(self.index.piece_users(group["members"][0][0])["files"][:3]) or "a file")
        if not self._spread_todo:
            log.info("Every group of pieces has the spare pieces the settings ask for")
            self._spread_scanned = 0.0          # look once more: what was added may cover groups still listed

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
