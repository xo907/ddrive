"""FindX on the dashboard: the relay's API (findx.py), the FindX map page, and live-location links.

- /api/findx/...       for FindX on your phones and for the map page (signed in, as anybody: the
                       owner or an account, end-to-end encrypted or not; it only ever sees sealed things)
- /findx/              the map of your devices (signed in)
- /f/<link>            a live-location link's page (nobody signed in: the key is in the link after "#")
- /api/findx/shared    what that page asks for (nobody signed in)

The pages are FindX's own map (web/findx/, the same files as in the app), with a policy of their own
that lets them fetch map tiles from OpenFreeMap and nothing else.
"""

import os
import re
import threading
import time

from .findx import WAIT_MAX, RelayError

_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_LINK = re.compile(r"^/f/([0-9a-f]{32})$")
SHARED_PER_MIN = 240      # a link's page asks every few seconds; this is plenty for a few people on one address

# The map draws in a worker it makes from a blob, and fetches tiles and letters from OpenFreeMap.
FINDX_CSP = ("default-src 'self'; connect-src 'self' https://tiles.openfreemap.org; img-src 'self' blob: data:; "
             "worker-src blob:; child-src blob:; style-src 'self'; script-src 'self'; object-src 'none'; "
             "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def _assets():
    """The FindX files that may be served, by their path under web/ (nothing else is)."""
    out = set()
    root = os.path.join(_WEB, "findx")
    for folder, _, files in os.walk(root):
        for f in files:
            if f.endswith((".js", ".css", ".html", ".txt")):
                out.add(os.path.relpath(os.path.join(folder, f), _WEB).replace(os.sep, "/"))
    return out


_ASSETS = _assets()


class _Pace:
    """Requests per address per minute, for the link pages nobody signs in to."""

    def __init__(self, per_min):
        self.per_min = per_min
        self._lock = threading.Lock()
        self._seen = {}           # address -> (minute, count)

    def ok(self, addr):
        minute = int(time.time() // 60)
        with self._lock:
            if len(self._seen) > 10000:
                self._seen = {a: v for a, v in self._seen.items() if v[0] == minute}
            m, n = self._seen.get(addr, (minute, 0))
            n = n + 1 if m == minute else 1
            self._seen[addr] = (minute, n)
            return n <= self.per_min


_pace = _Pace(SHARED_PER_MIN)


class FindXHandlers:
    """Mixed into web._Handler."""

    def _findx_page(self, name, extra=None):
        try:
            _, _, body, gz, etag, ctype = self._static_file(name)
        except OSError:
            return self._error(404, "not found")
        headers = {"Content-Security-Policy": FINDX_CSP, "ETag": etag, "Cache-Control": "no-cache"}
        headers.update(extra or {})
        if gz is not None:
            headers["Vary"] = "Accept-Encoding"
        if etag in (self.headers.get("If-None-Match") or ""):
            return self._send(304, b"", ctype, headers)
        if gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
            body = gz
            headers["Content-Encoding"] = "gzip"
        self._send(200, body, ctype, headers)

    def _findx_public(self, method, route):
        """FindX's routes that need nobody signed in. Returns True when it answered."""
        if method != "GET":
            return False
        if _LINK.match(route):
            self._findx_page("findx/share.html", {"X-Robots-Tag": "noindex, nofollow"})
            return True
        if route.startswith("/findx/") and route[1:] in _ASSETS and not route.endswith(".html"):
            self._findx_page(route[1:])
            return True
        if route == "/api/findx/shared":
            if not _pace.ok(self._addr()):
                self._error(429, "Too many requests. Wait a minute.")
                return True
            try:
                self._json(self.server.app.findx.shared(self._query().get("share", "")), fresh=False)
            except RelayError as e:
                self._error(e.status, str(e))
            return True
        return False

    def _findx(self, method, route, who):
        """The signed-in API and page. `who`: '' for the owner, else the account."""
        relay = self.server.app.findx
        if method == "GET" and route in ("/findx", "/findx/"):
            return self._findx_page("findx/index.html")
        try:
            if method == "GET":
                q = self._query()
                if route == "/api/findx/wait":
                    commands, watching = relay.wait(who, q.get("group"), q.get("device"), q.get("after", 0),
                                                    min(float(q.get("wait", WAIT_MAX) or 0), WAIT_MAX))
                    return self._json({"commands": commands, "watching": watching})
                if route == "/api/findx/devices":
                    return self._json({"devices": relay.devices(who, q.get("group"), q.get("watch") or None)})
                return self._error(404, "not found")
            body = self._body_json()
            if not isinstance(body, dict):
                return self._error(400, "expected a JSON object")
            if route == "/api/findx/hello":
                relay.hello(who, body.get("group"), body.get("device"), body.get("card"))
                return self._json({"ok": True})
            if route == "/api/findx/report":
                watching, open_links = relay.report(who, body.get("group"), body.get("device"), body.get("blob"), body.get("shares"))
                return self._json({"ok": True, "watching": watching, "shareWatching": open_links})
            if route == "/api/findx/command":
                return self._json({"ok": True, "seq": relay.command(who, body.get("group"), body.get("to"), body.get("blob"))})
            if route == "/api/findx/share":
                relay.share(who, body.get("group"), body.get("device"), body.get("share"), body.get("until"))
                return self._json({"ok": True})
            if route == "/api/findx/unshare":
                relay.unshare(who, body.get("group"), body.get("share"))
                return self._json({"ok": True})
            return self._error(404, "not found")
        except RelayError as e:
            return self._error(e.status, str(e))
        except ValueError:
            return self._error(400, "bad request")
