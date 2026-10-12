"""Folder syncing on computers that run DDrive Lite, as the host sees it.

A lite computer that has folder syncing turned on (`lite sync on`) announces itself to the host's
dashboard and then waits there for word. The dashboard's Sync page lists it next to the host's own
folders, and what is added, changed, started or stopped there is passed on to it; the copying itself
is done by the lite computer (litesync.py), through the same requests it uses for everything else.

The pairs belong to the lite computer, which keeps them (they name its folders). The host holds what
a computer told it in memory only, for as long as it runs: nothing about another computer's folders
is written to the host's disk. A computer that comes back says what it has, and whichever of the two
saw the last change wins (`rev`, a number that goes up by one with every change).

Each computer is filed under who signed it in: the owner, or one account. Nobody sees or steers
the computers of anybody else.
"""

import re
import secrets
import threading
import time

from . import sync

POLL_WAIT = 25.0          # how long a lite computer's question "anything new?" is held open
ONLINE = 45.0             # heard from within this long: connected
ANSWER_WAIT = 15.0        # how long the dashboard waits for a lite computer to list a folder
FORGET = 86400.0          # a computer not heard from for this long is dropped from the list
MAX_DEVICES = 50          # per owner / account
MAX_JOBS = 200


class Gone(Exception):
    """That computer isn't known here (any more)."""


class Offline(Exception):
    """That computer is known but can't be reached right now."""


class LiteDevices:
    def __init__(self):
        self._cv = threading.Condition()
        self._devs = {}              # (who, device id) -> record
        self._closed = False

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    # ------------------------------------------------------------ helpers (call with the lock held)
    def _dev(self, who, device):
        d = self._devs.get((who, str(device or "")))
        if d is None:
            raise Gone()
        return d

    @staticmethod
    def _online(d):
        return d["polls"] > 0 or time.time() - d["seen"] < ONLINE

    def _changed(self, d):
        d["rev"] += 1
        self._cv.notify_all()

    def _tell(self, d, **command):
        d["commands"].append(command)
        self._cv.notify_all()

    @staticmethod
    def _job(d, jid):
        job = next((j for j in d["jobs"] if j["id"] == str(jid or "")), None)
        if job is None:
            raise sync.SyncError("That folder is no longer synced")
        return job

    def _need_online(self, d):
        if not self._online(d):
            raise Offline(f"{d['name']} isn't connected right now. It starts when that computer is on and "
                          "DDrive Lite is running there.")

    # ------------------------------------------------------------ what the lite computer asks
    def hello(self, who, device, info):
        """A lite computer says it is there: its name, what kind it is, and the pairs it keeps."""
        device = str(device or "")
        if not re.fullmatch(r"[0-9a-f]{8,32}", device):
            raise sync.SyncError("Not a device id")
        jobs = []
        for j in (info.get("jobs") or [])[:MAX_JOBS]:
            try:
                jobs.append(sync.normalize_job(j, None, jobs, elsewhere=True))
            except (sync.SyncError, TypeError, AttributeError):
                continue
        try:
            rev = max(0, int(info.get("rev") or 0))
        except (TypeError, ValueError):
            rev = 0
        with self._cv:
            now = time.time()
            for key in [k for k, d in self._devs.items() if d["polls"] <= 0 and now - d["seen"] > FORGET]:
                del self._devs[key]
            d = self._devs.get((who, device))
            if d is None:
                if sum(1 for k in self._devs if k[0] == who) >= MAX_DEVICES:
                    raise sync.SyncError("Too many computers sync folders with this drive already.")
                d = self._devs[(who, device)] = {"id": device, "jobs": jobs, "rev": rev, "status": {}, "running": None,
                                                 "commands": [], "answers": {}, "asked": set(), "polls": 0}
            elif rev >= d["rev"]:            # nothing was changed here since it last heard from us
                d["jobs"], d["rev"] = jobs, rev
            d.update(name=str(info.get("name") or "").strip()[:60] or "A computer", seen=now,
                     windows=info.get("platform") == "win32", home=str(info.get("home") or "")[:500],
                     version=str(info.get("version") or "")[:20])
            self._cv.notify_all()
            return {"rev": d["rev"], "jobs": d["jobs"]}

    def poll(self, who, device, rev, wait=POLL_WAIT):
        """Held open until there is something for the computer (changed pairs, something to do) or
        `wait` seconds went by. Its pairs come along every time. One question per computer: a new one
        ends the one before it (which is how a computer lets go of the one it has open)."""
        end = time.monotonic() + max(0.0, min(float(wait), POLL_WAIT))
        with self._cv:
            d = self._dev(who, device)
            d["polls"] += 1
            d["seen"] = time.time()
            d["turn"] = turn = d.get("turn", 0) + 1
            self._cv.notify_all()
            try:
                while not self._closed and self._devs.get((who, d["id"])) is d and d["rev"] == rev and not d["commands"]                         and d["turn"] == turn:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
                if self._devs.get((who, d["id"])) is not d:
                    raise Gone()
                commands = []
                if d["turn"] == turn:
                    commands, d["commands"] = d["commands"], []
                return {"rev": d["rev"], "jobs": d["jobs"], "commands": commands}
            finally:
                d["polls"] -= 1
                d["seen"] = time.time()

    def report(self, who, device, status, running):
        """How the computer's pairs are doing (what the dashboard shows)."""
        with self._cv:
            d = self._dev(who, device)
            d["seen"] = time.time()
            d["status"] = {str(k): v for k, v in (status or {}).items() if isinstance(v, dict)}
            d["running"] = str(running) if running else None

    def answer(self, who, device, req, result):
        with self._cv:
            d = self._dev(who, device)
            if req in d["asked"]:
                d["asked"].discard(req)
                d["answers"][req] = result if isinstance(result, dict) else {}
                self._cv.notify_all()

    def bye(self, who, device):
        """Folder syncing was turned off there: it is off the list (the computer keeps its pairs)."""
        with self._cv:
            if self._devs.pop((who, str(device or "")), None) is not None:
                self._cv.notify_all()

    # ------------------------------------------------------------ what the dashboard asks
    def listing(self, who):
        """The computers of `who` with their pairs and how those are doing."""
        out = []
        with self._cv:
            for (w, _), d in self._devs.items():
                if w != who:
                    continue
                online = self._online(d)
                jobs = []
                for j in d["jobs"]:
                    s = dict(d["status"].get(j["id"]) or {})
                    s.setdefault("state", "idle")
                    if not online:
                        s.pop("progress", None)
                        s.update(next=None, watching=False, state="idle" if s["state"] == "running" else s["state"])
                    if not j["enabled"] and s["state"] != "running":
                        s["state"] = "paused"
                    jobs.append({**j, "status": s, "label": sync.MODES[j["mode"]][0], "direction": sync.MODES[j["mode"]][1],
                                 "when": sync.when_text(j)})
                out.append({"id": d["id"], "name": d["name"], "windows": d["windows"], "home": d["home"],
                            "version": d["version"], "online": online, "seen": d["seen"],
                            "running": d["running"] if online else None, "jobs": jobs})
        return sorted(out, key=lambda x: x["name"].lower())

    def save(self, who, device, job):
        """Add a pair to a computer or change one. Returns it, filled in."""
        with self._cv:
            d = self._dev(who, device)
            old = self._job(d, job["id"]) if job.get("id") else None
            if old is None and len(d["jobs"]) >= MAX_JOBS:
                raise sync.SyncError("That computer syncs too many folders already.")
            new = sync.normalize_job({**(old or {}), **job}, None, d["jobs"], elsewhere=True)
            d["jobs"] = [new if j is old else j for j in d["jobs"]] if old is not None else d["jobs"] + [new]
            if old is None and new["trigger"] != "manual":
                d["commands"].append({"do": "run", "id": new["id"]})
            self._changed(d)
            return new

    def remove(self, who, device, jid):
        with self._cv:
            d = self._dev(who, device)
            job = self._job(d, jid)
            d["jobs"] = [j for j in d["jobs"] if j is not job]
            d["status"].pop(job["id"], None)
            self._changed(d)

    def pause(self, who, device, jid, paused):
        with self._cv:
            d = self._dev(who, device)
            job = self._job(d, jid)
            d["jobs"] = [dict(j, enabled=not paused) if j is job else j for j in d["jobs"]]
            if paused:
                d["commands"].append({"do": "stop", "id": job["id"]})
            self._changed(d)

    def run(self, who, device, jid, again=False):
        """Start a pair now. True when another pair of that computer is running (it goes after it)."""
        with self._cv:
            d = self._dev(who, device)
            job = self._job(d, jid)
            self._need_online(d)
            self._tell(d, do="run", id=job["id"], again=bool(again))
            return d["running"] not in (None, job["id"])

    def stop(self, who, device, jid):
        with self._cv:
            d = self._dev(who, device)
            self._tell(d, do="stop", id=self._job(d, jid)["id"])

    def browse(self, who, device, path, wait=ANSWER_WAIT):
        """The folders in a folder of that computer, which it lists itself."""
        end = time.monotonic() + wait
        with self._cv:
            d = self._dev(who, device)
            self._need_online(d)
            req = secrets.token_hex(4)
            d["asked"].add(req)
            self._tell(d, do="browse", req=req, path=str(path or "")[:2000])
            while req not in d["answers"] and not self._closed:
                left = end - time.monotonic()
                if left <= 0:
                    d["asked"].discard(req)
                    raise Offline(f"{d['name']} didn't answer. Type the folder's path instead, or try again.")
                self._cv.wait(left)
            got = d["answers"].pop(req, {})
        if got.get("error"):
            raise sync.SyncError(str(got["error"]))
        return {"path": str(got.get("path") or ""), "parent": got.get("parent"),
                "dirs": [x for x in got.get("dirs") or [] if isinstance(x, dict)][:2000],
                "roots": [x for x in got.get("roots") or [] if isinstance(x, dict)][:50]}


class SealedDevices:
    """The same for the devices of end-to-end encrypted accounts (vault.py), of which this computer
    may know nothing: not a device's name, not its folders, not which folder goes where, not how a
    pair is doing. All of that is sealed on the device or in the account's browser with a key this
    computer doesn't have, and passed on as it is. What is left for it to do is what a post office
    does: keep each device's last sealed word and the browser's, say whether the device is there,
    and carry sealed messages between the two. Nothing is checked or filled in here (the browser
    and the device do that) and nothing is written to disk."""

    def __init__(self):
        self._cv = threading.Condition()
        self._devs = {}              # (account, device id) -> record
        self._closed = False

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _dev(self, who, device):
        d = self._devs.get((who, str(device or "")))
        if d is None:
            raise Gone()
        return d

    @staticmethod
    def _online(d):
        return d["polls"] > 0 or time.time() - d["seen"] < ONLINE

    @staticmethod
    def _sealed(value, limit=2 * 1024 * 1024):
        value = value if isinstance(value, str) else ""
        if len(value) > limit:
            raise sync.SyncError("That is too big to pass on.")
        return value

    # ------------------------------------------------------------ what the device asks
    def hello(self, who, device, rev, info, jobs):
        """A device says it is there: who it is and which pairs it keeps, both sealed."""
        device = str(device or "")
        if not re.fullmatch(r"[0-9a-f]{8,32}", device):
            raise sync.SyncError("Not a device id")
        try:
            rev = max(0, int(rev or 0))
        except (TypeError, ValueError):
            rev = 0
        with self._cv:
            now = time.time()
            for key in [k for k, d in self._devs.items() if d["polls"] <= 0 and now - d["seen"] > FORGET]:
                del self._devs[key]
            d = self._devs.get((who, device))
            if d is None:
                if sum(1 for k in self._devs if k[0] == who) >= MAX_DEVICES:
                    raise sync.SyncError("Too many devices sync folders with this account already.")
                d = self._devs[(who, device)] = {"id": device, "jobs": self._sealed(jobs), "rev": rev, "status": "",
                                                 "commands": [], "answers": {}, "asked": set(), "polls": 0, "turn": 0}
            elif rev >= d["rev"]:            # nothing was changed in the browser since it last heard from us
                d["jobs"], d["rev"] = self._sealed(jobs), rev
            d.update(info=self._sealed(info, 4096), seen=now)
            self._cv.notify_all()
            return {"rev": d["rev"], "jobs": d["jobs"]}

    def poll(self, who, device, rev, wait=POLL_WAIT):
        end = time.monotonic() + max(0.0, min(float(wait), POLL_WAIT))
        with self._cv:
            d = self._dev(who, device)
            d["polls"] += 1
            d["seen"] = time.time()
            d["turn"] = turn = d["turn"] + 1
            self._cv.notify_all()
            try:
                while not self._closed and self._devs.get((who, d["id"])) is d and d["rev"] == rev and not d["commands"] \
                        and d["turn"] == turn:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
                if self._devs.get((who, d["id"])) is not d:
                    raise Gone()
                commands = []
                if d["turn"] == turn:
                    commands, d["commands"] = d["commands"], []
                return {"rev": d["rev"], "jobs": d["jobs"], "commands": commands}
            finally:
                d["polls"] -= 1
                d["seen"] = time.time()

    def report(self, who, device, status):
        with self._cv:
            d = self._dev(who, device)
            d["seen"] = time.time()
            d["status"] = self._sealed(status)

    def answer(self, who, device, req, result):
        with self._cv:
            d = self._dev(who, device)
            if req in d["asked"]:
                d["asked"].discard(req)
                d["answers"][req] = self._sealed(result, 12 * 1024 * 1024)       # (an answer may carry a page's pictures)
                self._cv.notify_all()

    def bye(self, who, device):
        with self._cv:
            if self._devs.pop((who, str(device or "")), None) is not None:
                self._cv.notify_all()

    # ------------------------------------------------------------ what the account's browser asks
    def listing(self, who):
        with self._cv:
            return [{"id": d["id"], "online": self._online(d), "seen": d["seen"], "rev": d["rev"], "info": d["info"],
                     "jobs": d["jobs"], "status": d["status"] if self._online(d) else ""}
                    for (w, _), d in sorted(self._devs.items()) if w == who]

    def set_jobs(self, who, device, rev, jobs):
        """The browser changed a device's pairs. `rev` is the number it started from: if the pairs
        changed meanwhile it has to look again first (False)."""
        with self._cv:
            d = self._dev(who, device)
            if int(rev) != d["rev"]:
                return None
            d["jobs"], d["rev"] = self._sealed(jobs), d["rev"] + 1
            self._cv.notify_all()
            return d["rev"]

    def tell(self, who, device, sealed):
        """Pass a sealed message on to a device (start this pair, stop that one)."""
        with self._cv:
            d = self._dev(who, device)
            if not self._online(d):
                raise Offline("That device isn't connected right now.")
            d["commands"].append({"sealed": self._sealed(sealed, 65536)})
            self._cv.notify_all()

    def ask(self, who, device, sealed, wait=ANSWER_WAIT):
        """Pass a sealed question on to a device and wait for its sealed answer (the folders in a folder of
        it, what a web page says about itself)."""
        end = time.monotonic() + max(1.0, min(float(wait or ANSWER_WAIT), 60.0))
        with self._cv:
            d = self._dev(who, device)
            if not self._online(d):
                raise Offline("That device isn't connected right now.")
            req = secrets.token_hex(4)
            d["asked"].add(req)
            d["commands"].append({"req": req, "sealed": self._sealed(sealed, 65536)})
            self._cv.notify_all()
            while req not in d["answers"] and not self._closed:
                left = end - time.monotonic()
                if left <= 0:
                    d["asked"].discard(req)
                    raise Offline("That device didn't answer. Try again.")
                self._cv.wait(left)
            return d["answers"].pop(req, "")
