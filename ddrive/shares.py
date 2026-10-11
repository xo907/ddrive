"""Share links: a file or a folder for someone else, with an expiry, an optional password and a
choice whether it may be downloaded or only viewed.

Links are synced between devices through the change journal (like files), so a link made on one
device works on every device's dashboard: e.g. made on a PC at home and opened through the Raspberry
Pi that is reachable from the internet. Revoking or changing a link applies everywhere. The visitor's
page shows the content itself (photo, video, music, PDF, text) or, for a folder, its files.
"""

import json
import secrets
import threading
import time

from .crypto import check_password, hash_password

OLD_KEY = "shares"          # links of versions before 0.8 were kept per device in the index's kv table
VIEWS_EVERY = 60.0          # publish this device's view count of a link at most this often


class Shares:
    def __init__(self, index, device=""):
        self.index = index
        self.device = device or "local"
        self._lock = threading.Lock()
        self._views_posted = {}
        self._migrate()

    def _migrate(self):
        """Publish links made before they were synced, so the other devices learn them."""
        try:
            old = json.loads(self.index.kv_get(OLD_KEY) or "{}")
        except ValueError:
            old = {}
        for sid, rec in old.items():
            views = rec.pop("v", 0)
            self.index.share_put(sid, rec)
            if views:
                self.index.share_views_set(sid, self.device, views)
        if old:
            self.index.kv_delete(OLD_KEY)

    def all(self):
        """Every link that hasn't expired (with the views counted on all devices)."""
        now = time.time()
        out = {}
        for sid, rec in self.index.shares_all().items():
            if rec.get("e") and rec["e"] <= now:
                continue
            out[sid] = dict(rec, v=self.index.share_views_total(sid))
        return out

    def get(self, sid):
        rec = self.index.share_get(str(sid or ""))
        if rec is None or (rec.get("e") and rec["e"] <= time.time()):
            return None
        return rec

    def create(self, uid, is_dir, hours=24 * 7, password="", download=True, upload=None):
        """upload: make it a file request instead: people send files into the folder (up to this many
        bytes each) and can't see what is in it."""
        sid = secrets.token_urlsafe(12)
        rec = {"u": uid, "d": bool(is_dir), "c": time.time(), "e": _expiry(hours),
               "pw": hash_password(password) if password else "", "dl": bool(download)}
        if upload:
            rec["up"] = int(upload)
        self.index.share_put(sid, rec)
        return sid, dict(rec, v=0)

    def update(self, sid, hours=None, password=None, download=None):
        with self._lock:
            rec = self.index.share_get(sid)
            if rec is None:
                return None
            if hours is not None:
                rec["e"] = _expiry(hours)
            if password is not None:
                rec["pw"] = hash_password(password) if password else ""
            if download is not None:
                rec["dl"] = bool(download)
            self.index.share_put(sid, rec)
            return dict(rec, v=self.index.share_views_total(sid))

    def revoke(self, sid):
        return self.index.share_drop(sid)

    def viewed(self, sid):
        """Count a view on this device; the count is published now and then (not on every view)."""
        with self._lock:
            n = self.index.share_views_local(sid, self.device) + 1
            publish = time.time() - self._views_posted.get(sid, 0) >= VIEWS_EVERY
            if publish:
                self._views_posted[sid] = time.time()
        self.index.share_views_set(sid, self.device, n, publish=publish)

    @staticmethod
    def password_ok(rec, password):
        return check_password(password or "", rec.get("pw") or "")


def _expiry(hours):
    hours = float(hours or 0)
    if hours <= 0:
        return None
    return time.time() + min(hours, 24 * 3650) * 3600
