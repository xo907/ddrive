"""Bookmarks: web addresses saved with a preview (title, description, site name, icon and banner
picture, read from the page the way link previews are: Open Graph / Twitter card tags, then the
plain <title> and description).

Everything is kept on the drive, in /Bookmarks: one file with the list (Bookmarks.json) and the
pictures next to it (images/). So bookmarks are encrypted, synced to every device and versioned like
any other file, and looking at them loads nothing from the sites themselves.

Private bookmarks are a second list of the same kind in /Bookmarks/Private, a password-locked folder:
closed, nothing of them is listed. They show (marked "private") while it is open.
"""

import json
import logging
import posixpath
import secrets
import threading
import time
import urllib.parse

from .pagepreview import _clean, fetch_preview, normalize, parse_meta, same_page  # noqa: F401 (parse_meta: for whoever imports it from here)
from .webplus import ApiErrorProxy

log = logging.getLogger("ddrive.web")

FOLDER = "/Bookmarks"
BOOK = FOLDER + "/Bookmarks.json"
IMAGES = FOLDER + "/images"
PRIVATE = FOLDER + "/Private"
PRIVATE_BOOK = PRIVATE + "/Bookmarks.json"
PRIVATE_IMAGES = PRIVATE + "/images"


class BookmarkHandlers:
    """The dashboard's bookmark API (mixed into web._Handler)."""

    def _marks_guard(self):
        if not self._shown(FOLDER):
            raise ApiErrorProxy(404, f"{FOLDER} is hidden or locked on this device")

    def _marks_private(self):
        """The private list: "none" (not made), "locked" (closed here), "open" (its lock is open here),
        "unlocked" (its lock was removed)."""
        if not self._shown(PRIVATE):
            return "locked"
        node = self.drive.index.resolve(PRIVATE)
        if node is None or not node["is_dir"]:
            return "none"
        return "open" if node["uid"] in self._lock_ids() else "unlocked"

    def _marks_lists(self):
        """The lists that can be read here: the plain one, and the private one while it shows."""
        return [False, True] if self._marks_private() in ("open", "unlocked") else [False]

    def _marks_read(self, priv=False):
        node = self.drive.index.resolve(PRIVATE_BOOK if priv else BOOK)
        if node is None or node["is_dir"]:
            return []
        key = (node["id"], node["mtime"], node["size"], node["version"])
        cache = getattr(self.app, "_marks_cache", None)
        if cache is None:
            cache = self.app._marks_cache = {}
        if cache.get(priv, (None,))[0] == key:
            return [dict(b) for b in cache[priv][1]]
        try:
            raw = json.loads(self.drive.fs.read_file(node["id"], 0, node["size"] or 0).decode("utf-8", "replace") or "{}")
            items = [b for b in raw.get("items", []) if isinstance(b, dict) and b.get("id") and b.get("url")]
        except (ValueError, AttributeError):
            items = []
        cache[priv] = (key, items)
        return [dict(b) for b in items]

    def _marks_write(self, items, priv=False):
        data = json.dumps({"v": 1, "items": items}, ensure_ascii=False, indent=1).encode("utf-8")
        self._put_file(PRIVATE_BOOK if priv else BOOK, data, idle=6.0)
        getattr(self.app, "_marks_cache", {}).pop(priv, None)

    def _marks_find(self, bid):
        """(the list, the bookmark, whether it is the private list)."""
        for priv in self._marks_lists():
            items = self._marks_read(priv)
            mark = next((b for b in items if b["id"] == bid), None)
            if mark is not None:
                return items, mark, priv
        raise ApiErrorProxy(404, "That bookmark is gone")

    @staticmethod
    def _marks_shown(mark, priv):
        return dict(mark, private=True) if priv else mark

    def _marks_lock(self):
        lock = getattr(self.app, "_marks_lock", None)
        if lock is None:
            lock = self.app._marks_lock = threading.Lock()
        return lock

    def _marks_store(self, mark, preview, priv=False):
        """Put a preview's pictures on the drive and its words into the bookmark."""
        images = PRIVATE_IMAGES if priv else IMAGES
        for key in ("title", "description", "site", "color"):
            if preview.get(key) and (key != "title" or not mark.get("edited")):
                mark[key] = preview[key]
        for key, suffix in (("image", ""), ("icon", "-icon")):
            if preview.get(key):
                data, ext = preview[key]
                old = mark.get(key)
                path = f"{images}/{mark['id']}{suffix}.{ext}"
                self._put_file(path, data, idle=6.0)
                mark[key] = path
                if old and old != path:
                    self._marks_unlink(old)

    def _marks_unlink(self, path):
        if path and (path.startswith(IMAGES + "/") or path.startswith(PRIVATE_IMAGES + "/")):
            try:
                self.drive.fs.unlink(path)
            except OSError:
                pass

    def _get_api_bookmarks(self):
        if not self._shown(FOLDER):
            return self._json({"items": [], "locked": True, "folder": FOLDER})
        priv = self._marks_private()
        with self._marks_lock():
            items = self._marks_read()
            if priv in ("open", "unlocked"):
                items += [dict(b, private=True) for b in self._marks_read(True)]
        node = self.drive.index.resolve(FOLDER)
        self._json({"items": items, "folder": FOLDER,
                    "lock": bool(node is not None and node["uid"] in self._lock_ids()),   # (password-locked, and open here)
                    "private": priv, "private_folder": PRIVATE})

    def _post_api_bookmarks_add(self):
        self._marks_guard()
        body = self._body_json()
        priv = bool(body.get("private"))
        if priv and self._marks_private() not in ("open", "unlocked"):
            raise ApiErrorProxy(423, "Open the private bookmarks first.")
        try:
            url = normalize(str(body.get("url") or ""))
        except ValueError as e:
            raise ApiErrorProxy(400, str(e)) from None
        with self._marks_lock():
            old = next((b for b in self._marks_read(priv) if same_page(b["url"], url)), None)
        if old is not None:
            return self._json({"ok": True, "bookmark": self._marks_shown(old, priv), "existed": True})
        # Reading the page takes a moment and happens outside the lock (several links can be pasted at once).
        preview = fetch_preview(url, bool(getattr(self.drive.cfg, "fetch_private", False)))
        host = urllib.parse.urlsplit(url).hostname or url
        mark = {"id": secrets.token_hex(6), "url": url, "title": "", "description": "", "site": host, "color": "",
                "image": "", "icon": "", "tags": [str(t).strip()[:40] for t in body.get("tags") or [] if str(t).strip()][:12],
                "note": "", "added": time.time()}
        with self._marks_lock():
            self._marks_store(mark, preview, priv)
            if not mark["title"]:
                mark["title"] = host
            items = self._marks_read(priv)
            if not any(same_page(b["url"], url) for b in items):
                items.insert(0, mark)
                self._marks_write(items, priv)
        self._json({"ok": True, "bookmark": self._marks_shown(mark, priv), "preview": bool(preview["title"] or preview["image"])})

    def _post_api_bookmarks_private(self):
        """Bookmarks into the private list, or out of it, with their pictures. (The plain list's earlier
        versions still have a bookmark that went private, until they expire: `versions_kept`.)"""
        self._marks_guard()
        body = self._body_json()
        on, ids = bool(body.get("on")), set(map(str, body.get("ids") or []))
        if self._marks_private() not in ("open", "unlocked"):
            raise ApiErrorProxy(423, "Open the private bookmarks first.")
        fs, images = self.drive.fs, PRIVATE_IMAGES if on else IMAGES
        with self._marks_lock():
            src, dst = self._marks_read(not on), self._marks_read(on)
            moving = [b for b in src if b["id"] in ids]
            for mark in moving:
                for key in ("image", "icon"):
                    old = mark.get(key) or ""
                    if not old.startswith((PRIVATE_IMAGES if not on else IMAGES) + "/"):
                        continue
                    new = f"{images}/{posixpath.basename(old)}"
                    try:
                        if self.drive.index.resolve(images) is None:
                            self.drive.index.makedirs(images)
                        fs.rename(old, new)
                        mark[key] = new
                    except OSError:
                        mark[key] = ""
                if not any(b["id"] == mark["id"] for b in dst):
                    dst.insert(0, mark)
            if moving:
                self._marks_write(dst, on)
                self._marks_write([b for b in src if b["id"] not in ids], not on)
        self._json({"ok": True, "moved": len(moving), "versions_kept": bool(on and moving)})

    def _post_api_bookmarks_save(self):
        self._marks_guard()
        body = self._body_json()
        with self._marks_lock():
            items, mark, priv = self._marks_find(body.get("id"))
            if "title" in body:
                title = _clean(body["title"], 300)
                if title and title != mark.get("title"):
                    mark["title"], mark["edited"] = title, True
            if "description" in body:
                mark["description"] = _clean(body["description"], 600)
            if "pinned" in body:                     # pinned bookmarks are shown first
                if body["pinned"]:
                    mark["pinned"] = time.time()
                else:
                    mark.pop("pinned", None)
            if "note" in body:
                mark["note"] = str(body["note"] or "")[:4000]
            if "tags" in body:
                tags = body["tags"] if isinstance(body["tags"], list) else str(body["tags"]).split(",")
                seen, clean = set(), []
                for t in tags:
                    t = str(t).strip().lstrip("#")[:40]
                    if t and t.lower() not in seen:
                        seen.add(t.lower())
                        clean.append(t)
                mark["tags"] = clean[:12]
            self._marks_write(items, priv)
        self._json({"ok": True, "bookmark": self._marks_shown(mark, priv)})

    def _post_api_bookmarks_refresh(self):
        self._marks_guard()
        bid = self._body_json().get("id")
        with self._marks_lock():
            mark = self._marks_find(bid)[1]
        preview = fetch_preview(mark["url"], bool(getattr(self.drive.cfg, "fetch_private", False)))
        if not (preview["title"] or preview["image"] or preview["icon"]):
            raise ApiErrorProxy(502, "The page could not be read right now")
        with self._marks_lock():
            items, mark, priv = self._marks_find(bid)
            self._marks_store(mark, preview, priv)
            self._marks_write(items, priv)
        self._json({"ok": True, "bookmark": self._marks_shown(mark, priv)})

    def _post_api_bookmarks_delete(self):
        self._marks_guard()
        ids = set(map(str, self._body_json().get("ids") or []))
        removed = 0
        with self._marks_lock():
            for priv in self._marks_lists():
                items = self._marks_read(priv)
                keep = [b for b in items if b["id"] not in ids]
                for b in items:
                    if b["id"] in ids:
                        self._marks_unlink(b.get("image"))
                        self._marks_unlink(b.get("icon"))
                if len(keep) != len(items):
                    self._marks_write(keep, priv)
                    removed += len(items) - len(keep)
        self._json({"ok": True, "removed": removed})
