"""Bookmarks: web addresses saved with a preview (title, description, site name, icon and banner
picture, read from the page the way link previews are: Open Graph / Twitter card tags, then the
plain <title> and description).

Everything is kept on the drive, in /Bookmarks: one file with the list (Bookmarks.json) and the
pictures next to it (images/). So bookmarks are encrypted, synced to every device and versioned like
any other file, and looking at them loads nothing from the sites themselves.
"""

import json
import logging
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


class BookmarkHandlers:
    """The dashboard's bookmark API (mixed into web._Handler)."""

    def _marks_guard(self):
        if not self._shown(FOLDER):
            raise ApiErrorProxy(404, f"{FOLDER} is hidden or locked on this device")

    def _marks_read(self):
        node = self.drive.index.resolve(BOOK)
        if node is None or node["is_dir"]:
            return []
        key = (node["mtime"], node["size"], node["version"])
        cached = getattr(self.app, "_marks", None)
        if cached and cached[0] == key:
            return [dict(b) for b in cached[1]]
        try:
            raw = json.loads(self.drive.fs.read_file(node["id"], 0, node["size"] or 0).decode("utf-8", "replace") or "{}")
            items = [b for b in raw.get("items", []) if isinstance(b, dict) and b.get("id") and b.get("url")]
        except (ValueError, AttributeError):
            items = []
        self.app._marks = (key, items)
        return [dict(b) for b in items]

    def _marks_write(self, items):
        data = json.dumps({"v": 1, "items": items}, ensure_ascii=False, indent=1).encode("utf-8")
        self._put_file(BOOK, data, idle=6.0)
        self.app._marks = None

    def _marks_lock(self):
        lock = getattr(self.app, "_marks_lock", None)
        if lock is None:
            lock = self.app._marks_lock = threading.Lock()
        return lock

    def _marks_store(self, mark, preview):
        """Put a preview's pictures on the drive and its words into the bookmark."""
        for key in ("title", "description", "site", "color"):
            if preview.get(key) and (key != "title" or not mark.get("edited")):
                mark[key] = preview[key]
        for key, suffix in (("image", ""), ("icon", "-icon")):
            if preview.get(key):
                data, ext = preview[key]
                old = mark.get(key)
                path = f"{IMAGES}/{mark['id']}{suffix}.{ext}"
                self._put_file(path, data, idle=6.0)
                mark[key] = path
                if old and old != path:
                    self._marks_unlink(old)

    def _marks_unlink(self, path):
        if path and path.startswith(IMAGES + "/"):
            try:
                self.drive.fs.unlink(path)
            except OSError:
                pass

    def _get_api_bookmarks(self):
        if not self._shown(FOLDER):
            return self._json({"items": [], "locked": True, "folder": FOLDER})
        with self._marks_lock():
            items = self._marks_read()
        node = self.drive.index.resolve(FOLDER)
        self._json({"items": items, "folder": FOLDER,
                    "lock": bool(node is not None and node["uid"] in self._lock_ids())})   # (password-locked, and open here)

    def _post_api_bookmarks_add(self):
        self._marks_guard()
        body = self._body_json()
        try:
            url = normalize(str(body.get("url") or ""))
        except ValueError as e:
            raise ApiErrorProxy(400, str(e)) from None
        with self._marks_lock():
            old = next((b for b in self._marks_read() if same_page(b["url"], url)), None)
        if old is not None:
            return self._json({"ok": True, "bookmark": old, "existed": True})
        # Reading the page takes a moment and happens outside the lock (several links can be pasted at once).
        preview = fetch_preview(url, bool(getattr(self.drive.cfg, "fetch_private", False)))
        host = urllib.parse.urlsplit(url).hostname or url
        mark = {"id": secrets.token_hex(6), "url": url, "title": "", "description": "", "site": host, "color": "",
                "image": "", "icon": "", "tags": [str(t).strip()[:40] for t in body.get("tags") or [] if str(t).strip()][:12],
                "note": "", "added": time.time()}
        with self._marks_lock():
            self._marks_store(mark, preview)
            if not mark["title"]:
                mark["title"] = host
            items = self._marks_read()
            if not any(same_page(b["url"], url) for b in items):
                items.insert(0, mark)
                self._marks_write(items)
        self._json({"ok": True, "bookmark": mark, "preview": bool(preview["title"] or preview["image"])})

    def _post_api_bookmarks_save(self):
        self._marks_guard()
        body = self._body_json()
        with self._marks_lock():
            items = self._marks_read()
            mark = next((b for b in items if b["id"] == body.get("id")), None)
            if mark is None:
                raise ApiErrorProxy(404, "That bookmark is gone")
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
            self._marks_write(items)
        self._json({"ok": True, "bookmark": mark})

    def _post_api_bookmarks_refresh(self):
        self._marks_guard()
        bid = self._body_json().get("id")
        with self._marks_lock():
            mark = next((b for b in self._marks_read() if b["id"] == bid), None)
        if mark is None:
            raise ApiErrorProxy(404, "That bookmark is gone")
        preview = fetch_preview(mark["url"], bool(getattr(self.drive.cfg, "fetch_private", False)))
        if not (preview["title"] or preview["image"] or preview["icon"]):
            raise ApiErrorProxy(502, "The page could not be read right now")
        with self._marks_lock():
            items = self._marks_read()
            mark = next((b for b in items if b["id"] == bid), None)
            if mark is None:
                raise ApiErrorProxy(404, "That bookmark is gone")
            self._marks_store(mark, preview)
            self._marks_write(items)
        self._json({"ok": True, "bookmark": mark})

    def _post_api_bookmarks_delete(self):
        self._marks_guard()
        ids = set(map(str, self._body_json().get("ids") or []))
        with self._marks_lock():
            items = self._marks_read()
            keep = [b for b in items if b["id"] not in ids]
            for b in items:
                if b["id"] in ids:
                    self._marks_unlink(b.get("image"))
                    self._marks_unlink(b.get("icon"))
            if len(keep) != len(items):
                self._marks_write(keep)
        self._json({"ok": True, "removed": len(items) - len(keep)})

