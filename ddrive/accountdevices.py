"""Which devices use an end-to-end encrypted account, and keeping one out.

An end-to-end encrypted account's changes reach the server sealed, so the server can't tell what a
device writes. It can tell who writes: every request comes with how the device introduces itself.
DDrive and DDrive Lite (3.16.5 and newer) say so in headers (X-DD-Device: an id of its own, X-DD-Name,
X-DD-Version); a browser and the phone app say what they are in their User-Agent; a DDrive or Lite
from before 3.16.5 says nothing at all. That is enough to list them on the account's Settings page
(Devices using this account), with when each was last seen and last wrote something, and to:

* sign one out: the server refuses everything it asks until it is let back in (a computer that
  introduces itself, by its id; anything else by its address and what it says it is);
* refuse older computers: one that doesn't introduce itself can still read, but not change anything.

This keeps your own devices in order (an old computer somewhere that still runs an earlier DDrive);
it is not protection from somebody who has the account's 24 words, who can say they are anything.
"""

import hashlib
import json
import os
import re
import threading
import time

WRITES = ("/api/vault/log", "/api/vault/object", "/api/vault/snapshot", "/api/vault/delete")
KEEP_S = 60 * 86400              # not heard from for this long: off the list
MAX_PER_ACCOUNT = 100
SAVE_EVERY_S = 30.0


def _clean(text, n):
    return re.sub(r"[\x00-\x1f]", "", str(text or "")).strip()[:n]


def describe(headers, addr):
    """(key, what it is) for a request: the key a device is known (and kept out) by."""
    device = re.sub(r"[^0-9A-Za-z_-]", "", str(headers.get("X-DD-Device") or ""))[:64]
    ua = _clean(headers.get("User-Agent"), 200)
    if device:
        return "d:" + device, {"kind": "computer", "name": _clean(headers.get("X-DD-Name"), 60) or "A computer",
                               "version": _clean(headers.get("X-DD-Version"), 20)}
    if "Mozilla" in ua:
        browser = next((b for b in ("Edg", "OPR", "Firefox", "Chrome", "Safari") if b in ua), "A browser")
        system = next((s for s in ("Android", "iPhone", "iPad", "Windows", "Mac OS", "Linux", "CrOS") if s in ua), "")
        app = "DDrive app" if "wv)" in ua or "DDriveApp" in ua else None
        name = app or ({"Edg": "Edge", "OPR": "Opera"}.get(browser, browser) + (f" on {system.replace('Mac OS', 'Mac')}" if system else ""))
        return "b:" + hashlib.sha256((ua + "|" + addr).encode()).hexdigest()[:16], {"kind": "browser", "name": name, "version": ""}
    if "Dalvik" in ua or "okhttp" in ua.lower():
        return "a:" + hashlib.sha256((ua + "|" + addr).encode()).hexdigest()[:16], {"kind": "phone", "name": "DDrive on a phone", "version": ""}
    return "u:" + addr, {"kind": "older", "name": "A computer with DDrive older than 3.16.5", "version": ""}


class AccountDevices:
    def __init__(self, path=None, clock=time.time):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        self._data = {}          # account id -> {"blocked": [keys], "strict": bool, "devices": {key: rec}}
        self._saved = 0.0
        self._dirty = False
        try:
            with open(path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError, TypeError):
            pass

    def _acct(self, aid):
        a = self._data.setdefault(aid, {})
        a.setdefault("blocked", [])
        a.setdefault("strict", False)
        a.setdefault("devices", {})
        return a

    def _save(self, force=False):
        if not self.path or not (force or (self._dirty and self.clock() - self._saved > SAVE_EVERY_S)):
            return
        self._saved, self._dirty = self.clock(), False
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def heard(self, aid, headers, addr, method, route):
        """A request of account `aid`. Returns why it is refused, or None."""
        key, info = describe(headers, addr)
        write = method == "POST" and route in WRITES
        now = self.clock()
        with self._lock:
            a = self._acct(aid)
            rec = a["devices"].get(key)
            if rec is None:
                for k in [k for k, r in a["devices"].items() if now - r["seen"] > KEEP_S]:
                    del a["devices"][k]
                if len(a["devices"]) >= MAX_PER_ACCOUNT:
                    oldest = min(a["devices"], key=lambda k: a["devices"][k]["seen"])
                    del a["devices"][oldest]
                rec = a["devices"][key] = {"key": key, "first": now, "writes": 0, "last_write": None}
            rec.update(info, seen=now, addr=addr)
            refused = None
            if key in a["blocked"]:
                refused = ("This device was signed out of the account (its Settings, Devices using this account). "
                           "Let it back in there.")
            elif write and a["strict"] and info["kind"] == "older":
                refused = ("This account only takes changes from DDrive 3.16.5 or newer. Update DDrive on this computer "
                           "(git pull), or let older computers in again in the account's Settings.")
            if write:
                if refused:
                    rec["refused"] = now
                else:
                    rec["writes"] += 1
                    rec["last_write"] = now
            self._dirty = True
            self._save()
        return refused

    def listing(self, aid, me_key=None):
        with self._lock:
            a = self._acct(aid)
            out = []
            for key, rec in a["devices"].items():
                out.append(dict(rec, blocked=key in a["blocked"], me=key == me_key))
            return {"strict": a["strict"], "devices": sorted(out, key=lambda r: -(r["last_write"] or r["seen"]))}

    def block(self, aid, key, on=True):
        with self._lock:
            a = self._acct(aid)
            if on and key not in a["blocked"]:
                a["blocked"].append(key)
            elif not on and key in a["blocked"]:
                a["blocked"].remove(key)
            self._save(force=True)

    def set_strict(self, aid, on):
        with self._lock:
            self._acct(aid)["strict"] = bool(on)
            self._save(force=True)

    def forget(self, aid, key):
        with self._lock:
            a = self._acct(aid)
            a["devices"].pop(key, None)
            if key in a["blocked"]:
                a["blocked"].remove(key)
            self._save(force=True)

    def close(self):
        with self._lock:
            self._save(force=True)
