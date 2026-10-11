"""Bookmarks: web addresses saved with a preview (title, description, site name, icon and banner
picture, read from the page the way link previews are: Open Graph / Twitter card tags, then the
plain <title> and description).

Everything is kept on the drive, in /Bookmarks: one file with the list (Bookmarks.json) and the
pictures next to it (images/). So bookmarks are encrypted, synced to every device and versioned like
any other file, and looking at them loads nothing from the sites themselves.
"""

import json
import logging
import posixpath
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import __version__
from .webplus import ApiErrorProxy, public_address

log = logging.getLogger("ddrive.web")

FOLDER = "/Bookmarks"
BOOK = FOLDER + "/Bookmarks.json"
IMAGES = FOLDER + "/images"
PAGE_MAX = 1024 * 1024            # how much of a page is read to find its preview
IMAGE_MAX = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
               "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/svg+xml": "svg"}
# Many sites answer link-preview robots with their full tags and plain programs with nothing.
AGENT = f"Mozilla/5.0 (compatible; DDrive/{__version__}; link preview) facebookexternalhit/1.1"


def normalize(url):
    """A typed or pasted address as a full one: 'example.com/x' -> 'https://example.com/x'."""
    url = (url or "").strip()
    if not url or any(c in url for c in " \n\t<>\"") or len(url) > 2000:
        raise ValueError("That doesn't look like a web address")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or "." not in u.hostname and u.hostname != "localhost":
        raise ValueError("That doesn't look like a web address")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, ""))


def same_page(a, b):
    def key(url):
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        return (host[4:] if host.startswith("www.") else host, u.path.rstrip("/"), u.query)
    return key(a) == key(b)


class _Meta(HTMLParser):
    """Collects what a page says about itself in its <head>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.icons = []            # (size, href)
        self.title = ""
        self._in_title = False
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").strip().lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower().split()
            if "icon" in rel or "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
                m = re.match(r"(\d+)", a.get("sizes", ""))
                size = int(m.group(1)) if m else (180 if "apple-touch-icon" in " ".join(rel) else 32)
                if a.get("type") == "image/svg+xml" or a["href"].lower().split("?")[0].endswith(".svg"):
                    size = 64
                self.icons.append((size, a["href"].strip()))
        elif tag == "title" and not self.title:
            self._in_title = True
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _clean(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit - 1].rstrip() + "…" if len(text) > limit else text


def parse_meta(html, url):
    """{title, description, site, image, icon, color} from a page's HTML (addresses made absolute)."""
    p = _Meta()
    try:
        p.feed(html)
    except Exception:                      # broken markup: use what was found so far
        pass
    m = p.meta
    host = urllib.parse.urlsplit(url).hostname or ""
    absolute = (lambda href: urllib.parse.urljoin(url, href) if href else "")
    icon = ""
    if p.icons:
        # the smallest one that is still sharp at the size it is shown
        good = sorted((s, h) for s, h in p.icons if s >= 32)
        icon = (good[0] if good else max(p.icons))[1]
    color = m.get("theme-color", "")
    return {
        "title": _clean(m.get("og:title") or m.get("twitter:title") or p.title, 300),
        "description": _clean(m.get("og:description") or m.get("twitter:description") or m.get("description"), 600),
        "site": _clean(m.get("og:site_name") or m.get("application-name") or "", 80) or (host[4:] if host.startswith("www.") else host),
        "image": absolute(m.get("og:image:secure_url") or m.get("og:image") or m.get("twitter:image") or m.get("twitter:image:src")),
        "icon": absolute(icon) or absolute("/favicon.ico"),
        "color": color if re.fullmatch(r"#[0-9a-fA-F]{3,8}", color) else "",
    }


def _opener(allow_private):
    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            u = urllib.parse.urlsplit(newurl)
            if u.scheme not in ("http", "https") or (not allow_private and not public_address(u.hostname or "")):
                raise OSError("it redirects to an address that isn't on the internet")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(Redirects)


def _get(url, allow_private, limit, timeout=10, accept="*/*"):
    """(bytes read (at most `limit`), content type, charset, final address). Only addresses on the internet,
    unless `allow_private`."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise OSError("not a web address")
    if not allow_private and not public_address(u.hostname):
        raise OSError("that address is on this computer or the home network")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept, "Accept-Language": "en,*;q=0.5"})
    with _opener(allow_private).open(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
        return data, resp.headers.get_content_type(), resp.headers.get_content_charset(), resp.geturl()


def fetch_preview(url, allow_private=False):
    """What the page at `url` says about itself, with its banner and icon downloaded:
    {title, description, site, color, image: (bytes, ext) | None, icon: (bytes, ext) | None}."""
    host = urllib.parse.urlsplit(url).hostname or ""
    out = {"title": "", "description": "", "site": host[4:] if host.startswith("www.") else host, "color": "",
           "image": None, "icon": None}
    try:
        data, ctype, charset, final = _get(url, allow_private, PAGE_MAX, accept="text/html,application/xhtml+xml,*/*;q=0.8")
    except Exception as e:
        log.debug("bookmark preview of %s: %s", host, e)
        return out
    links = {"image": "", "icon": urllib.parse.urljoin(final, "/favicon.ico")}
    if ctype in IMAGE_TYPES and len(data) <= IMAGE_MAX:            # the link is a picture itself
        out["image"] = (data, IMAGE_TYPES[ctype])
        out["title"] = posixpath.basename(urllib.parse.urlsplit(final).path)
    elif "html" in ctype or "xml" in ctype:
        head = data[:4096].decode("ascii", "replace")
        m = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
        try:
            text = data.decode(charset or (m.group(1) if m else "utf-8"), "replace")
        except LookupError:
            text = data.decode("utf-8", "replace")
        meta = parse_meta(text, final)
        out.update({k: meta[k] for k in ("title", "description", "site", "color")})
        links = {"image": meta["image"], "icon": meta["icon"]}
    else:
        out["title"] = posixpath.basename(urllib.parse.urlsplit(final).path)

    def picture(key, limit):
        if not links[key] or out[key]:
            return
        try:
            data, ctype, _, _ = _get(links[key], allow_private, limit, timeout=8, accept="image/*")
            if ctype in IMAGE_TYPES and 0 < len(data) <= limit:
                out[key] = (data, IMAGE_TYPES[ctype])
        except Exception as e:
            log.debug("bookmark %s of %s: %s", key, host, e)

    threads = [threading.Thread(target=picture, args=("image", IMAGE_MAX), daemon=True),
               threading.Thread(target=picture, args=("icon", 512 * 1024), daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(12)
    return out


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
        self._json({"items": items, "folder": FOLDER})

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

