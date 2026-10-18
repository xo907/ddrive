"""FindX circles: family and friends who see where each other are, live (as Life360 does), through this drive.

A circle is made by somebody signed in to this drive (so strangers can't fill it), and then joined by
anybody with its invite link: friends with a DDrive of their own elsewhere, or with none. The link
carries the circle's key after "#"; from it the phones make the circle's sealing key and an `auth`
token that proves they were invited. This drive keeps only a hash of that token, and only sealed
things: each member's latest report, the circle's sealed details (its name and places), and sealed
commands ("ring", "arrived at Home") on their way to the members. It can't read any of them, and it
doesn't know who the members are: a member is a random id.

The circles themselves (the token's hash, the sealed details, who made it) are kept in a small file
so they survive a restart; what's live is kept in memory only, like the rest of FindX.
"""

import hashlib
import hmac
import json
import os
import threading
import time

from .findx import BLOB_MAX, COMMAND_KEEP_S, WAIT_MAX, WATCH_S, RelayError, _blob, _id

CIRCLES_PER_ACCOUNT = 20
CIRCLES_TOTAL = 5000
MEMBERS_MAX = 30
COMMANDS_MAX = 100
MEMBER_KEEP_S = 90 * 86400     # a member not heard from in this long is forgotten
WAIT_CIRCLES_MAX = 20          # circles one phone may wait on at once


def _hash(auth):
    return hashlib.sha256(_id(auth, "auth").encode()).hexdigest()


class _Member:
    __slots__ = ("blob", "at", "seen", "waiter")

    def __init__(self):
        self.blob = None
        self.at = 0.0
        self.seen = 0.0
        self.waiter = 0


class _Circle:
    __slots__ = ("auth", "who", "info", "made", "members", "commands", "seq", "watched")

    def __init__(self, auth, who, info, made):
        self.auth, self.who, self.info, self.made = auth, who, info, made
        self.members = {}
        self.commands = []        # (seq, to: member or "*", from member, blob, at)
        self.seq = 0
        self.watched = 0.0


class Circles:
    def __init__(self, path=None, clock=time.time):
        self._path = path
        self._clock = clock
        self._cv = threading.Condition()
        self._circles = {}
        self._waiters = {}        # member -> newest wait's number
        self._dirty = False
        self._stop = threading.Event()
        self._load()
        if self._path:
            threading.Thread(target=self._saver, name="FindXCirclesSave", daemon=True).start()

    # ------------------------------------------------------------ kept on disk

    def _load(self):
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for cid, c in (data.get("circles") or {}).items():
                x = self._circles[cid] = _Circle(c["auth"], c.get("who", ""), c.get("info"), c.get("made", 0))
                x.seq = int(c.get("seq") or 0)
                for mid, m in (c.get("members") or {}).items():      # (each one's latest sealed report: the map has them at once)
                    y = x.members[mid] = _Member()
                    y.blob, y.at, y.seen = m.get("blob"), float(m.get("at") or 0), float(m.get("seen") or 0)
        except (OSError, ValueError, KeyError, TypeError):
            pass                  # a damaged file: circles are made again by their members' phones

    def _save(self):
        if not self._path:
            return
        self._dirty = False
        data = {"circles": {cid: {"auth": c.auth, "who": c.who, "info": c.info, "made": c.made, "seq": c.seq,
                                  "members": {mid: {"blob": m.blob, "at": m.at, "seen": m.seen} for mid, m in c.members.items()}}
                            for cid, c in self._circles.items()}}
        tmp = self._path + ".tmp"
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self._path)

    def _saver(self):
        """What changes often (members' reports, command numbers) is written at most every 15 seconds."""
        while not self._stop.wait(15.0):
            with self._cv:
                if self._dirty:
                    try:
                        self._save()
                    except OSError:
                        self._dirty = True

    def close(self):
        self._stop.set()
        with self._cv:
            if self._dirty:
                try:
                    self._save()
                except OSError:
                    pass

    # ------------------------------------------------------------ inside

    def _circle(self, circle, auth):
        """The circle, if `auth` is its token (a wrong one and an unknown circle look the same)."""
        c = self._circles.get(_id(circle, "circle"))
        if c is None or not hmac.compare_digest(c.auth, _hash(auth)):
            raise RelayError(404, "No such circle, or not invited")
        return c

    def _sweep(self, c, now):
        c.commands = [x for x in c.commands if now - x[4] <= COMMAND_KEEP_S]
        for m in [m for m, v in c.members.items() if now - v.seen > MEMBER_KEEP_S]:
            del c.members[m]

    # ------------------------------------------------------------ signed in to this drive

    def create(self, who, circle, auth, info):
        circle = _id(circle, "circle")
        info = _blob(info, "circle details")
        with self._cv:
            if circle in self._circles:
                c = self._circles[circle]
                if c.who == who and hmac.compare_digest(c.auth, _hash(auth)):
                    return            # made again by the same phone (it didn't hear the answer): fine
                raise RelayError(409, "That circle id is taken")
            if sum(1 for c in self._circles.values() if c.who == who) >= CIRCLES_PER_ACCOUNT:
                raise RelayError(429, "Too many circles on this account")
            if len(self._circles) >= CIRCLES_TOTAL:
                raise RelayError(429, "Too many circles on this drive")
            self._circles[circle] = _Circle(_hash(auth), who, info, int(self._clock()))
            self._save()

    def delete(self, who, circle):
        with self._cv:
            c = self._circles.get(_id(circle, "circle"))
            if c is None or c.who != who:
                raise RelayError(404, "No such circle of yours")
            del self._circles[circle]
            self._save()
            self._cv.notify_all()

    def mine(self, who):
        with self._cv:
            return sorted(cid for cid, c in self._circles.items() if c.who == who)

    # ------------------------------------------------------------ any member (the invite's token)

    def info(self, circle, auth):
        with self._cv:
            c = self._circle(circle, auth)
            return {"info": c.info, "members": len(c.members)}

    def set_info(self, circle, auth, info):
        info = _blob(info, "circle details")
        with self._cv:
            c = self._circle(circle, auth)
            c.info = info
            self._save()

    def report(self, circle, auth, member, blob):
        blob = _blob(blob, "report")
        member = _id(member, "member")
        with self._cv:
            now = self._clock()
            c = self._circle(circle, auth)
            self._sweep(c, now)
            m = c.members.get(member)
            if m is None:
                if len(c.members) >= MEMBERS_MAX:
                    raise RelayError(429, "This circle is full")
                m = c.members[member] = _Member()
            m.blob, m.at, m.seen = blob, now, now
            self._dirty = True
            self._cv.notify_all()
            return c.watched > now

    def members(self, circle, auth, watch=False):
        """Every member's latest sealed report. `watch`: a map of the circle is open (its members send more
        often for a minute; asked again, for another minute)."""
        with self._cv:
            now = self._clock()
            c = self._circle(circle, auth)
            self._sweep(c, now)
            if watch:
                was = c.watched > now
                c.watched = now + WATCH_S
                if not was:
                    self._cv.notify_all()
            return [{"member": mid, "blob": m.blob, "at": int(m.at * 1000)} for mid, m in c.members.items() if m.blob]

    def command(self, circle, auth, sender, to, blob):
        """A sealed command for one member, or for everybody but the sender ("*": arrived, left)."""
        blob = _blob(blob, "command")
        sender = _id(sender, "member") if sender else ""
        to = "*" if to == "*" else _id(to, "member")
        with self._cv:
            now = self._clock()
            c = self._circle(circle, auth)
            self._sweep(c, now)
            if to != "*" and to not in c.members:
                raise RelayError(404, "Nobody with that id is in this circle")
            c.seq += 1
            c.commands.append((c.seq, to, sender, blob, now))
            self._dirty = True
            if len(c.commands) > COMMANDS_MAX:
                c.commands = c.commands[-COMMANDS_MAX:]
            self._cv.notify_all()
            return c.seq

    def leave(self, circle, auth, member):
        with self._cv:
            c = self._circle(circle, auth)
            c.members.pop(_id(member, "member"), None)

    def wait(self, member, circles, wait):
        """One phone waits on all its circles of this drive at once: [{circle, auth, after}], up to `wait`
        seconds, until a command for it comes or a map of one of them is opened. Returns
        (commands [{circle, seq, blob}], circles being watched)."""
        member = _id(member, "member")
        if not isinstance(circles, list) or len(circles) > WAIT_CIRCLES_MAX:
            raise RelayError(400, "circles must be a list of at most %d" % WAIT_CIRCLES_MAX)
        try:
            wait = max(0.0, min(float(wait or 0), WAIT_MAX))
            asked = [(x["circle"], x["auth"], int(x.get("after") or 0)) for x in circles]
        except (TypeError, ValueError, KeyError, AttributeError):
            raise RelayError(400, "bad circles") from None
        with self._cv:
            now = self._clock()
            mine = []
            for circle, auth, after in asked:
                c = self._circle(circle, auth)
                m = c.members.get(member)
                if m is not None:
                    m.seen = now
                if after > c.seq:
                    # The phone has had commands numbered further than this drive has got (an earlier version
                    # forgot them over a restart): numbers go on from there, or new ones would look old to it.
                    c.seq = after
                    self._dirty = True
                mine.append((circle, c, after))
            self._waiters[member] = me = self._waiters.get(member, 0) + 1
            end = time.monotonic() + wait
            was = {cid for cid, c, _ in mine if c.watched > now}
            while True:
                t = self._clock()
                out = []
                for cid, c, after in mine:
                    out += [{"circle": cid, "seq": x[0], "blob": x[3]} for x in c.commands
                            if x[0] > after and (x[1] == member or (x[1] == "*" and x[2] != member)) and t - x[4] <= COMMAND_KEEP_S]
                watched = sorted(cid for cid, c, _ in mine if c.watched > t)
                gone = any(self._circles.get(cid) is not c for cid, c, _ in mine)
                left = end - time.monotonic()
                if out or left <= 0 or set(watched) - was or self._waiters.get(member) != me or gone:
                    if self._waiters.get(member) == me:
                        del self._waiters[member]
                    return out, watched
                self._cv.wait(min(left, 30))

    def stats(self):
        with self._cv:
            return {"circles": len(self._circles), "members": sum(len(c.members) for c in self._circles.values())}


__all__ = ["Circles", "BLOB_MAX"]
