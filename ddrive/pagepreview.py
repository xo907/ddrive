"""Reading what a web page says about itself, the way link previews do: Open Graph / Twitter card tags,
then the plain <title> and description, with its banner picture and icon downloaded.

The computer with the drive does this for a bookmark. For an end-to-end encrypted account it may not
(it would see which pages are saved), so one of the account's own devices does, which is why this
module stands alone: DDrive Lite carries it without the rest of the dashboard.
"""

import ipaddress
import logging
import posixpath
import re
import socket
import threading
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import __version__

log = logging.getLogger("ddrive.web")


def public_address(host):
    """True when `host` is somewhere on the internet (not this computer or the home network)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


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
        log.debug("preview of %s: %s", host, e)
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
            log.debug("%s of %s: %s", key, host, e)

    threads = [threading.Thread(target=picture, args=("image", IMAGE_MAX), daemon=True),
               threading.Thread(target=picture, args=("icon", 512 * 1024), daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(12)
    return out
