"""FindX's live relay: where your phones are now, "ring" and "locate" for them, and live-location links.

FindX (the Android app) seals everything before it sends it, with keys made from the 24 words that
only the phones and your browser have. So this relay never reads anything: it keeps the latest
sealed report and card of each device, hands sealed commands to the device they are for, and
passes a link's sealed report to whoever opens that link. It is kept in memory and, as sealed as it
came, in a file in the drive's data folder (findx-relay.json), so that a restart (an update) loses
nothing: the dashboard shows your phones at once, links go on working and a "ring" still reaches the
phone. History is kept in files (/FindX on the drive, written by the phones themselves). FindX's
PROTOCOL.md describes the formats.

Everything is kept apart by who is signed in (the owner, or an account) and the group a phone names
(made from the words, the same on all of one person's devices): one account can't see another's
devices, whatever group it names.

Battery is the reason for "watching": a phone sends every few seconds only while somebody has its
map or one of its links open, and next to nothing otherwise. A phone's report and its wait for
commands are answered with whether anybody is watching.
"""

import base64
import binascii
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

WAIT_MAX = 240            # seconds a phone's wait for commands is held open at most
WATCH_S = 60.0            # a map or a link counts as open this long after it last asked
COMMAND_KEEP_S = 600.0    # commands older than this are dropped (the phone ignores them anyway)
COMMANDS_MAX = 50         # per group
BLOB_MAX = 16 * 1024      # bytes of a sealed report, card or command
DEVICES_MAX = 20          # per group
GROUPS_MAX = 8            # per account
SHARES_MAX = 50           # live links per device
SHARES_TOTAL = 20000      # live links on this drive
ENDED_KEEP_S = 7 * 86400  # an ended link answers "ended" (410) this long, then "not found"
DEVICE_KEEP_S = 30 * 86400  # a device not heard from in this long is forgotten
SAVE_EVERY_S = 15.0       # what changed is written to the file at most this often (and when the drive stops)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


class RelayError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _id(value, what):
    value = str(value or "")
    if not _HEX32.match(value):
        raise RelayError(400, f"{what} must be 32 hex digits")
    return value


def _blob(value, what):
    """A sealed thing, base64: checked and size-limited, never opened."""
    if not isinstance(value, str) or not value:
        raise RelayError(400, f"{what} is missing")
    if len(value) > BLOB_MAX * 4 // 3 + 8:
        raise RelayError(413, f"{what} is too large")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise RelayError(400, f"{what} isn't base64") from None
    if len(raw) < 28 or len(raw) > BLOB_MAX:       # at least a nonce and a tag
        raise RelayError(400 if len(raw) < 28 else 413, f"{what} isn't a sealed message")
    return value


class _Device:
    __slots__ = ("card", "report", "at", "seen", "watched", "waiter")

    def __init__(self):
        self.card = None
        self.report = None
        self.at = 0.0             # when the last report came
        self.seen = 0.0           # when it was last heard from at all
        self.watched = 0.0        # its map counts as open until then
        self.waiter = 0           # the newest wait: an older one still open gives up


class _Group:
    __slots__ = ("devices", "commands", "seq")

    def __init__(self):
        self.devices = {}
        self.commands = []        # (seq, to, blob, at)
        self.seq = 0


class _Share:
    __slots__ = ("who", "group", "device", "until", "blob", "at", "watched")

    def __init__(self, who, group, device, until):
        self.who, self.group, self.device, self.until = who, group, device, until
        self.blob = None
        self.at = 0.0
        self.watched = 0.0


class Relay:
    def __init__(self, clock=time.time, path=None):
        self._clock = clock
        self._cv = threading.Condition()
        self._groups = {}         # (who, group) -> _Group
        self._shares = {}         # share id -> _Share
        self._ended = {}          # share id -> when it ended
        self._swept = 0.0
        self._path = path         # where it is kept between restarts (None: memory only)
        self._dirty = False
        self._saved = 0.0
        self._stop = threading.Event()
        self._load()
        if path:
            threading.Thread(target=self._saver, name="FindXRelaySave", daemon=True).start()

    # ------------------------------------------------------------ kept on disk (everything in it is sealed)

    def _load(self):
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for g in data.get("groups") or []:
                grp = _Group()
                grp.seq = int(g.get("seq") or 0)
                for dev, d in (g.get("devices") or {}).items():
                    x = grp.devices[dev] = _Device()
                    x.card, x.report = d.get("card"), d.get("report")
                    x.at, x.seen = float(d.get("at") or 0), float(d.get("seen") or 0)
                self._groups[(g["who"], g["group"])] = grp
            for sid, x in (data.get("shares") or {}).items():
                sh = self._shares[sid] = _Share(x["who"], x["group"], x["device"], int(x.get("until") or 0))
                sh.blob, sh.at = x.get("blob"), float(x.get("at") or 0)
            self._ended = {sid: float(at) for sid, at in (data.get("ended") or {}).items()}
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("FindX: the relay's file couldn't be read (%s); the phones fill it again", e)
            self._groups, self._shares, self._ended = {}, {}, {}

    def _changed(self):
        """(Under the lock.) Something worth keeping changed: written soon, by the saver."""
        self._dirty = True

    def _snapshot(self):
        return {
            "v": 1,
            "groups": [{"who": who, "group": group, "seq": g.seq,
                        "devices": {dev: {"card": d.card, "report": d.report, "at": d.at, "seen": d.seen} for dev, d in g.devices.items()}}
                       for (who, group), g in self._groups.items()],
            "shares": {sid: {"who": x.who, "group": x.group, "device": x.device, "until": x.until, "blob": x.blob, "at": x.at}
                       for sid, x in self._shares.items()},
            "ended": self._ended,
        }

    def save(self):
        """Write what changed (now). Called by the saver, and when the drive stops."""
        if not self._path:
            return
        with self._cv:
            if not self._dirty:
                return
            data = self._snapshot()
            self._dirty = False
        try:
            tmp = self._path + ".tmp"
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp, self._path)
            self._saved = time.time()
        except OSError as e:
            with self._cv:
                self._dirty = True
            log.warning("FindX: the relay's file couldn't be written: %s", e)

    def _saver(self):
        while not self._stop.wait(SAVE_EVERY_S):
            self.save()

    def close(self):
        self._stop.set()
        self.save()

    # ------------------------------------------------------------ inside

    def _group(self, who, group, make=True):
        key = (who, _id(group, "group"))
        g = self._groups.get(key)
        if g is None and make:
            if sum(1 for w, _ in self._groups if w == who) >= GROUPS_MAX:
                raise RelayError(429, "Too many FindX groups on this account")
            g = self._groups[key] = _Group()
        return g

    def _device(self, g, device, make=True):
        device = _id(device, "device")
        d = g.devices.get(device)
        if d is None and make:
            if len(g.devices) >= DEVICES_MAX:
                raise RelayError(429, "Too many devices in this FindX group")
            d = g.devices[device] = _Device()
        return d

    def _sweep(self, now):
        """Drops what has run out (now and then, not on every request)."""
        if now - self._swept < 60:
            return
        self._swept = now
        for sid, s in list(self._shares.items()):
            if s.until and s.until / 1000.0 <= now:
                del self._shares[sid]
                self._ended[sid] = now
        for sid, at in list(self._ended.items()):
            if now - at > ENDED_KEEP_S:
                del self._ended[sid]
        for key, g in list(self._groups.items()):
            g.commands = [c for c in g.commands if now - c[3] <= COMMAND_KEEP_S]
            for dev, d in list(g.devices.items()):
                if now - d.seen > DEVICE_KEEP_S:
                    del g.devices[dev]
            if not g.devices and not g.commands:
                del self._groups[key]
        self._dirty = True        # (what ran out is gone from the file too)

    def _watching(self, g, device, now):
        d = g.devices.get(device)
        return bool(d and d.watched > now)

    # ------------------------------------------------------------ what a phone does

    def hello(self, who, group, device, card):
        card = _blob(card, "card")
        with self._cv:
            now = self._clock()
            self._sweep(now)
            d = self._device(self._group(who, group), device)
            d.card, d.seen = card, now
            self._changed()

    def report(self, who, group, device, blob, shares=None):
        """A device's latest report, and one sealed for each of its live links. Returns
        (watching, the links somebody has open)."""
        blob = _blob(blob, "report")
        shares = shares or {}
        if not isinstance(shares, dict) or len(shares) > SHARES_MAX:
            raise RelayError(400, "shares must be an object of at most %d links" % SHARES_MAX)
        sealed = {_id(k, "share"): _blob(v, "shared report") for k, v in shares.items()}
        with self._cv:
            now = self._clock()
            self._sweep(now)
            g = self._group(who, group)
            device = _id(device, "device")
            d = self._device(g, device)
            d.report, d.at, d.seen = blob, now, now
            self._changed()
            open_links = []
            for sid, b in sealed.items():
                s = self._shares.get(sid)
                if s is None or (s.who, s.group, s.device) != (who, group, device):
                    continue                     # not this device's link (or ended): nothing to keep
                s.blob, s.at = b, now
                if s.watched > now:
                    open_links.append(sid)
            self._cv.notify_all()
            return self._watching(g, device, now), open_links

    def wait(self, who, group, device, after, wait):
        """Commands for a device after `after`, held open up to `wait` seconds until there are some.
        Returns (commands, watching)."""
        try:
            after = int(after or 0)
            wait = max(0.0, min(float(wait or 0), WAIT_MAX))
        except (TypeError, ValueError):
            raise RelayError(400, "after and wait must be numbers") from None
        device = _id(device, "device")
        with self._cv:
            now = self._clock()
            self._sweep(now)
            g = self._group(who, group)
            d = self._device(g, device)
            d.seen = now
            if after > g.seq:
                # The phone has had commands numbered further than this relay has got (it was emptied: an
                # earlier version kept nothing over a restart). Numbers go on from there, or new ones
                # would look old to the phone.
                g.seq = after
                self._changed()
            d.waiter += 1
            me = d.waiter
            end = time.monotonic() + wait
            was = self._watching(g, device, now)
            while True:
                out = [{"seq": c[0], "blob": c[2]} for c in g.commands
                       if c[0] > after and c[1] == device and self._clock() - c[3] <= COMMAND_KEEP_S]
                watching = self._watching(g, device, self._clock())
                left = end - time.monotonic()
                # Somebody just opened its map or a link: the phone hears it now, not at its next report.
                if out or left <= 0 or (watching and not was) or d.waiter != me or g is not self._groups.get((who, group)):
                    return out, watching
                self._cv.wait(min(left, 30))

    # ------------------------------------------------------------ what your devices and the dashboard do

    def command(self, who, group, to, blob):
        blob = _blob(blob, "command")
        with self._cv:
            now = self._clock()
            self._sweep(now)
            g = self._group(who, group)
            to = _id(to, "device")
            if to not in g.devices:
                raise RelayError(404, "No device with that id has said hello here")
            g.seq += 1
            g.commands.append((g.seq, to, blob, now))
            self._changed()
            if len(g.commands) > COMMANDS_MAX:
                g.commands = g.commands[-COMMANDS_MAX:]
            self._cv.notify_all()
            return g.seq

    def devices(self, who, group, watch=None):
        """Every device of a group as last heard. `watch`: the device whose map is open (it then sends
        often for a minute; asked again, for another minute)."""
        with self._cv:
            now = self._clock()
            self._sweep(now)
            g = self._group(who, group, make=False)
            if g is None:
                return []
            if watch:
                d = g.devices.get(_id(watch, "device"))
                if d is not None:
                    was = d.watched > now
                    d.watched = now + WATCH_S
                    if not was:
                        self._cv.notify_all()        # (a phone waiting hears it next time it reports)
            out = []
            for dev, d in g.devices.items():
                if d.card is None:
                    continue
                item = {"device": dev, "card": d.card, "at": int(d.at * 1000), "seen": int(d.seen * 1000)}
                if d.report is not None:
                    item["report"] = d.report
                out.append(item)
            return out

    def share(self, who, group, device, share, until):
        try:
            until = int(until or 0)
        except (TypeError, ValueError):
            raise RelayError(400, "until must be a time in milliseconds") from None
        with self._cv:
            now = self._clock()
            self._sweep(now)
            g = self._group(who, group)
            device = _id(device, "device")
            self._device(g, device)
            share = _id(share, "share")
            s = self._shares.get(share)
            if s is not None and (s.who, s.group, s.device) != (who, group, device):
                raise RelayError(409, "That link id is taken")
            if until and until / 1000.0 <= now:
                raise RelayError(400, "That link has already ended")
            if share in self._ended:
                raise RelayError(410, "That link was stopped")
            if s is None:
                mine = sum(1 for x in self._shares.values() if (x.who, x.group, x.device) == (who, group, device))
                if mine >= SHARES_MAX or len(self._shares) >= SHARES_TOTAL:
                    raise RelayError(429, "Too many live links")
                s = self._shares[share] = _Share(who, group, device, until)
            s.until = until
            self._changed()

    def unshare(self, who, group, share):
        with self._cv:
            share = _id(share, "share")
            _id(group, "group")
            s = self._shares.get(share)
            if s is None:
                return
            if (s.who, s.group) != (who, group):
                raise RelayError(404, "No such link")
            del self._shares[share]
            self._ended[share] = self._clock()
            self._changed()

    # ------------------------------------------------------------ a link's page (nobody signed in)

    def shared(self, share):
        """{blob, at, until} for an open link (blob missing until the phone has sent one). It counts as
        open for a minute. Raises 404 for a link that never was, 410 for one that ended."""
        with self._cv:
            now = self._clock()
            self._sweep(now)
            try:
                share = _id(share, "share")
            except RelayError:
                raise RelayError(404, "No such link") from None
            s = self._shares.get(share)
            if s is not None and s.until and s.until / 1000.0 <= now:
                del self._shares[share]
                self._ended[share] = now
                s = None
            if s is None:
                if share in self._ended:
                    raise RelayError(410, "This link has ended")
                raise RelayError(404, "No such link")
            was = s.watched > now
            s.watched = now + WATCH_S
            if not was:
                # The phone sends often only while a link is open: it hears it in the answer to its next
                # report. Mark the device watched too, so a phone lying still pins itself down once.
                g = self._groups.get((s.who, s.group))
                d = g.devices.get(s.device) if g else None
                if d is not None:
                    d.watched = max(d.watched, now + WATCH_S)
                self._cv.notify_all()
            out = {"until": s.until, "at": int(s.at * 1000)}
            if s.blob is not None:
                out["blob"] = s.blob
            return out

    def knows(self, who, group, device):
        """True when the relay has this device's card (else the phone is asked to say hello again)."""
        with self._cv:
            g = self._groups.get((who, _id(group, "group")))
            d = g.devices.get(_id(device, "device")) if g else None
            return bool(d and d.card is not None)

    def stats(self):
        with self._cv:
            return {"groups": len(self._groups), "devices": sum(len(g.devices) for g in self._groups.values()),
                    "links": len(self._shares)}
