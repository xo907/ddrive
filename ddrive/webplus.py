"""More of the web dashboard's API (mixed into web._Handler): the home page, stars, recent files, the
activity list, storage insights, downloading a selection as one ZIP, saving a file from a web address,
file requests (links other people upload through), and WebDAV for other apps."""

import errno
import json
import logging
import mimetypes
import os
import posixpath
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from email.utils import formatdate
from xml.sax.saxutils import escape as _x

from .logbuf import tname
from .pagepreview import public_address
from . import __version__
from .crypto import check_password
from .index import ROOT_ID

log = logging.getLogger("ddrive.web")

PIECE = 1024 * 1024
REQUEST_MAX = 2 * 1024 ** 3          # a file request takes files up to this size unless the link says otherwise
FETCH_KEEP = 90.0                    # finished "save from a link" downloads stay listed this long
DAV_METHODS = ("OPTIONS", "PROPFIND", "PROPPATCH", "MKCOL", "PUT", "DELETE", "MOVE", "COPY", "LOCK", "UNLOCK")


def housekeeping(path):
    """True for files the dashboard keeps for itself (bookmark previews, logged Discord messages): they stay
    out of Recent, Activity and the Gallery, where they would only be noise. They are ordinary files in Files."""
    return (path or "").lower().startswith(("/bookmarks/", "/discord/"))


def clean_name(name, fallback="file"):
    """A file name somebody else chose, made safe to store: no folders, no control characters."""
    name = urllib.parse.unquote(str(name or "")).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip().strip(".")
    return name[:180] or fallback


class _Body:
    """The body of a request as a file to read from (a known length, or chunked)."""

    def __init__(self, handler, limit=None):
        self.f = handler.rfile
        self.chunked = "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower()
        self.left = None if self.chunked else int(handler.headers.get("Content-Length") or 0)
        self.limit = limit
        self.count = 0
        self._chunk = 0
        self._done = False

    def read(self, n=PIECE):
        if self._done:
            return b""
        if self.chunked:
            if self._chunk == 0:
                line = self.f.readline(64).strip().split(b";")[0]
                self._chunk = int(line or b"0", 16)
                if self._chunk == 0:
                    while self.f.readline(1024).strip():       # trailers
                        pass
                    self._done = True
                    return b""
            data = self.f.read(min(n, self._chunk))
            self._chunk -= len(data)
            if self._chunk == 0:
                self.f.readline(8)
        else:
            if self.left <= 0:
                return b""
            data = self.f.read(min(n, self.left))
            self.left -= len(data)
        self.count += len(data)
        if self.limit is not None and self.count > self.limit:
            raise OSError(errno.EFBIG, "too large")
        return data


class Fetches:
    """Files being saved from a web address, straight onto the drive."""

    def __init__(self, drive):
        self.drive = drive
        self.items = []
        self._lock = threading.Lock()
        self._n = 0

    def start(self, url, folder, scope):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("Give an address that starts with http:// or https://")
        allow_private = bool(getattr(self.drive.cfg, "fetch_private", False))
        if not allow_private and not public_address(u.hostname):
            raise ValueError("That address is on this computer or the home network (or can't be found); "
                             "only addresses on the internet are fetched")
        with self._lock:
            if sum(1 for i in self.items if i["state"] == "running") >= 4:
                raise ValueError("Four downloads are already running; wait for one to finish")
            self._n += 1
            item = {"id": self._n, "url": url, "folder": folder, "name": clean_name(u.path, "download"), "path": "",
                    "bytes": 0, "total": 0, "state": "running", "error": "", "started": time.time(), "stop": False}
            self.items.append(item)
        threading.Thread(target=self._run, args=(item, scope, allow_private), name=tname("Fetch"), daemon=True).start()
        return item

    def listing(self):
        now = time.time()
        with self._lock:
            self.items = [i for i in self.items if i["state"] == "running" or now - i.get("ended", now) < FETCH_KEEP]
            return [{k: v for k, v in i.items() if k != "stop"} for i in self.items]

    def cancel(self, fid):
        for i in self.items:
            if i["id"] == fid:
                i["stop"] = True

    def _run(self, item, scope, allow_private):
        fs = self.drive.fs
        fs.scope.unlocked = scope

        class Redirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                host = urllib.parse.urlsplit(newurl).hostname or ""
                if urllib.parse.urlsplit(newurl).scheme not in ("http", "https") or \
                        (not allow_private and not public_address(host)):
                    raise OSError("it redirects to an address that isn't on the internet")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        path = ""
        try:
            opener = urllib.request.build_opener(Redirects)
            req = urllib.request.Request(item["url"], headers={"User-Agent": f"DDrive/{__version__}"})
            with opener.open(req, timeout=30) as resp:
                cd = resp.headers.get("Content-Disposition") or ""
                m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
                name = clean_name(m.group(1) if m else urllib.parse.urlsplit(resp.geturl()).path, "download")
                if "." not in name:
                    ext = mimetypes.guess_extension((resp.headers.get("Content-Type") or "").split(";")[0].strip()) or ""
                    name += ext
                item["name"] = name
                item["total"] = int(resp.headers.get("Content-Length") or 0)
                path = item["path"] = free_name(self.drive, posixpath.join(item["folder"], name))

                class Reader:
                    def read(self, n):
                        data = resp.read(n)
                        item["bytes"] += len(data)
                        return data

                fs.put_file(path, Reader(), stop=lambda: item["stop"] or self.drive._stop_event.is_set())
            item["state"] = "done"
            log.info("Saved %s from the web (%d bytes)", path, item["bytes"])
        except InterruptedError:
            item["state"], item["error"] = "error", "Cancelled"
        except Exception as e:
            item["state"] = "error"
            item["error"] = getattr(e, "reason", None) and str(e.reason) or (e.strerror if isinstance(e, OSError) and e.strerror else str(e))
            log.warning("Could not save %s from the web: %s", item["url"].split("?")[0], item["error"])
        finally:
            if item["state"] != "done" and path:
                try:
                    fs.unlink(path)
                except OSError:
                    pass
            item["ended"] = time.time()


def free_name(drive, path):
    """`path`, or 'name (2).ext' ... when something with that name is already there."""
    if drive.index.resolve(path) is None:
        return path
    folder, name = posixpath.split(path)
    stem, ext = os.path.splitext(name)
    for n in range(2, 10000):
        cand = posixpath.join(folder, f"{stem} ({n}){ext}")
        if drive.index.resolve(cand) is None:
            return cand
    raise OSError(errno.EEXIST, "too many files with that name")


class PlusHandlers:
    # ------------------------------------------------------------ every folder's path and size
    def _tree(self):
        """({folder id: path}, {folder id: [bytes, files]}) for the whole drive, worked out once and
        kept for a few seconds (lists and insights ask often)."""
        app = self.app
        cached = getattr(app, "_tree_cache", None)
        if cached and time.time() - cached[0] < 10:
            return cached[1], cached[2]
        idx = self.drive.index
        dirs = {r["id"]: (r["parent"], r["name"]) for r in idx._all("SELECT id, parent, name FROM nodes WHERE is_dir=1")}
        paths = {ROOT_ID: "/"}

        def path_of(nid):
            chain = []
            while nid not in paths and nid in dirs:
                chain.append(nid)
                nid = dirs[nid][0]
            base = paths.get(nid)
            for d in reversed(chain):
                base = None if base is None else posixpath.join(base, dirs[d][1])
                paths[d] = base
            return base

        for d in dirs:
            path_of(d)
        sizes = {}
        for r in idx._all("SELECT parent, COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM nodes WHERE is_dir=0 GROUP BY parent"):
            nid, hops = r["parent"], 0
            while nid is not None and hops < 100:
                s = sizes.setdefault(nid, [0, 0])
                s[0] += r["b"]
                s[1] += r["n"]
                if nid == ROOT_ID:
                    break
                nid = dirs.get(nid, (None,))[0]
                hops += 1
        app._tree_cache = (time.time(), paths, sizes)
        return paths, sizes

    def _files(self, where="", args=(), order="mtime DESC", limit=None, skip_own=False):
        """Entries of the files matching an SQL condition, without hidden and locked ones."""
        paths, _ = self._tree()
        stars = self.drive.index.stars_all()
        out = []
        for r in self.drive.index._all(f"SELECT * FROM nodes WHERE is_dir=0 {where} ORDER BY {order}", args):
            folder = paths.get(r["parent"])
            if folder is None:
                continue
            p = posixpath.join(folder, r["name"])
            if self.drive.fs._hidden(p) or (skip_own and housekeeping(p)):
                continue
            e = self._entry(p, r)
            if r["uid"] in stars:
                e["starred"] = True
            out.append(e)
            if limit and len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ stars, recent, activity
    def _post_api_star(self):
        body = self._body_json()
        path, node = self._node(body.get("path"))
        if node["id"] == ROOT_ID:
            raise ApiErrorProxy(400, "The drive itself can't be starred")
        self.drive.index.star_set(node["uid"], bool(body.get("on")))
        self.drive.journal.wake()
        self._json({"ok": True})

    def _starred(self):
        idx = self.drive.index
        items = []
        for uid, at in sorted(idx.stars_all().items(), key=lambda kv: -kv[1]):
            node = idx.get_by_uid(uid)
            if node is None:
                continue
            p = idx.path_of(node["id"])
            if p and not self.drive.fs._hidden(p):
                e = dict(self._entry(p, node), starred=True)
                if not node["is_dir"] and node["name"].rpartition(".")[2].lower() in self._media_ext():
                    e["thumb"] = self._thumb_state(node)
                items.append(e)
        return items

    def _get_api_starred(self):
        self._json({"items": self._starred()})

    def _recent(self, limit):
        items = self._files(limit=limit, skip_own=True)
        ext = self._media_ext()
        for e in items:
            if e["name"].rpartition(".")[2].lower() in ext:
                node = self.drive.index.resolve(e["path"])
                if node is not None:
                    e["thumb"] = self._thumb_state(node)
        return items

    def _get_api_recent(self):
        try:
            limit = max(1, min(500, int(self._query().get("limit") or 100)))
        except ValueError:
            limit = 100
        self._json({"items": self._recent(limit)})

    def _history(self, limit, before=None):
        idx = self.drive.index
        devices = idx.devices()
        me = self.drive.cfg.device_id
        out, cursor = [], before
        for _ in range(20):                                  # skip over entries in hidden or locked folders
            rows = idx.activity(limit * 2, cursor)
            if not rows:
                break
            for r in rows:
                cursor = r["id"]
                if self.drive.fs._hidden(r["path"]) or (r["src"] and self.drive.fs._hidden(r["src"]))                         or housekeeping(r["path"]) or r["path"].lower() == "/bookmarks":
                    continue
                dev = r["device"] or me
                r["device_name"] = (devices.get(dev) or {}).get("name") or (self._host_name() if dev == me else dev)
                r["me"] = dev == me
                r["exists"] = idx.get_by_uid(r["uid"]) is not None if r["uid"] else False
                out.append(r)
                if len(out) >= limit:
                    return out
        return out

    def _get_api_history(self):
        q = self._query()
        try:
            limit = max(1, min(300, int(q.get("limit") or 100)))
            before = int(q["before"]) if q.get("before") else None
        except ValueError:
            limit, before = 100, None
        self._json({"items": self._history(limit, before)})

    def _get_api_home(self):
        d = self.drive
        st = d.index.stats(max_age=5)
        jobs = d.sync.snapshot() if d.sync else []
        jobs += [j for dev in self.server.app.lite.listing(self._who() or "") for j in dev["jobs"]]   # DDrive Lite computers
        self._json({
            "host": self._host_name(), "mount": d.cfg.mount_point, "version": __version__,
            "stats": {k: st.get(k) for k in ("files", "dirs", "bytes", "versions", "version_bytes", "chunks",
                                             "protected_chunks", "unsynced")},
            "starred": self._starred()[:12],
            "recent": self._recent(8),
            "history": self._history(8),
            "sync": {"jobs": len(jobs), "running": sum(1 for j in jobs if j["status"].get("state") == "running"),
                     "problems": sum(1 for j in jobs if j["status"].get("state") == "error")},
            "devices": len(d.index.devices()) or 1,
            "shares": len(self.app.shares.all()),
            "dav": bool(getattr(d.cfg, "webdav_enabled", False)),
        })

    # ------------------------------------------------------------ storage insights
    def _get_api_insights(self):
        from .web import _kind
        idx = self.drive.index
        paths, sizes = self._tree()
        kinds, largest, sig_of = {}, [], {}
        files = self._files(order="size DESC")
        for e in files:
            k = kinds.setdefault(_kind(e["name"]), [0, 0])
            k[0] += 1
            k[1] += e["size"]
        largest = files[:40]
        # identical files: the same pieces in the same order
        by_id = {}
        for r in idx._all("SELECT node_id, sha256 FROM chunks ORDER BY node_id, idx"):
            by_id.setdefault(r["node_id"], []).append(r["sha256"] or "")
        groups = {}
        for r in idx._all("SELECT id, parent, name, size, mtime FROM nodes WHERE is_dir=0 AND size > 0"):
            sig = by_id.get(r["id"])
            if not sig or not all(sig):
                continue
            folder = paths.get(r["parent"])
            if folder is None:
                continue
            p = posixpath.join(folder, r["name"])
            if self.drive.fs._hidden(p):
                continue
            groups.setdefault((r["size"], tuple(sig)), []).append({"path": p, "name": r["name"], "mtime": r["mtime"]})
        dups = [{"size": key[0], "files": sorted(v, key=lambda f: f["mtime"]), "extra": key[0] * (len(v) - 1)}
                for key, v in groups.items() if len(v) > 1]
        dups.sort(key=lambda g: -g["extra"])
        folders = [{"path": paths[nid], "bytes": s[0], "files": s[1]} for nid, s in sizes.items()
                   if nid != ROOT_ID and paths.get(nid) and not self.drive.fs._hidden(paths[nid])]
        folders.sort(key=lambda f: -f["bytes"])
        st = idx.stats(max_age=5)
        self._json({
            "total": {"files": len(files), "bytes": sum(e["size"] for e in files)},
            "kinds": sorted(({"kind": k, "files": v[0], "bytes": v[1]} for k, v in kinds.items()), key=lambda x: -x["bytes"]),
            "largest": largest,
            "folders": folders[:40],
            "duplicates": dups[:200],
            "duplicate_bytes": sum(g["extra"] for g in dups),
            "versions": {"count": st.get("versions", 0), "bytes": st.get("version_bytes", 0)},
            "spare_bytes": st.get("spare_bytes", 0),
            "saved": dict(self.drive.uploader.stats) if self.drive.uploader else {},
        })

    # ------------------------------------------------------------ several items as one ZIP
    def _zip_many(self, paths):
        items = [self._node(p) for p in paths[:500]]
        if not items:
            raise ApiErrorProxy(400, "nothing chosen")
        idx = self.drive.index
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''DDrive.zip")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        from .web import _Unseekable
        used = set()
        with zipfile.ZipFile(_Unseekable(self.wfile), "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            def add_file(node, name):
                info = zipfile.ZipInfo(name, time.localtime(max(node["mtime"], 315532800))[:6])
                with zf.open(info, "w", force_zip64=node["size"] > 2 ** 31) as f:
                    pos = 0
                    while pos < node["size"]:
                        data = self.drive.fs.read_file(node["id"], pos, PIECE)
                        if not data:
                            break
                        f.write(data)
                        pos += len(data)

            for path, node in items:
                name = node["name"]
                stem, ext = os.path.splitext(name)
                n = 2
                while name.lower() in used:
                    name = f"{stem} ({n}){ext}"
                    n += 1
                used.add(name.lower())
                if not node["is_dir"]:
                    add_file(node, name)
                    continue
                todo = [(node, path, name + "/")]
                zf.writestr(zipfile.ZipInfo(name + "/", time.localtime(max(node["mtime"], 315532800))[:6]), b"")
                while todo:
                    n_, p_, rel = todo.pop()
                    for c in idx.children(n_["id"]):
                        cp = posixpath.join(p_, c["name"])
                        if self.drive.fs._hidden(cp):
                            continue
                        if c["is_dir"]:
                            zf.writestr(zipfile.ZipInfo(rel + c["name"] + "/", time.localtime(max(c["mtime"], 315532800))[:6]), b"")
                            todo.append((c, cp, rel + c["name"] + "/"))
                        else:
                            add_file(c, rel + c["name"])

    # ------------------------------------------------------------ save from a web address
    def _post_api_fetch(self):
        body = self._body_json()
        folder, _ = self._node(body.get("path") or "/", want_dir=True)
        try:
            item = self.app.fetches.start(str(body.get("url") or "").strip(), folder, self.drive.fs.scope.unlocked)
        except ValueError as e:
            raise ApiErrorProxy(400, str(e)) from None
        self._json({"ok": True, "id": item["id"], "name": item["name"]})

    def _post_api_fetch_cancel(self):
        self.app.fetches.cancel(int(self._body_json().get("id") or 0))
        self._json({"ok": True})

    def _fetch_list(self):
        return [f for f in self.app.fetches.listing() if not self.drive.fs._hidden(f["folder"])]

    # ------------------------------------------------------------ file requests (links to upload through)
    def _request_page(self, sid, rec, root):
        from .web import _SHARE_HEADERS, _esc, _human, _share_shell, _until
        limit = int(rec.get("up") or REQUEST_MAX)
        body = (f"<h1>Send files to “{_esc(root['name'])}”</h1>"
                f"<p class=m>Files you add here go straight to the owner of this folder. You can't see what is "
                f"already in it. Up to {_human(limit)} per file &middot; {_until(rec)}</p>"
                f"<div id=drop class=drop data-id='{_esc(sid)}' data-max='{limit}'><b>Drop files here</b>"
                f"<span>or</span><label class=b>Choose files<input id=pick type=file multiple hidden></label></div>"
                f"<div id=list class=list></div>"
                f"<noscript><p class=m>This page needs JavaScript to upload.</p></noscript>"
                f"<script src='/share-upload.js'></script>")
        style = ("<style>.drop{display:flex;flex-direction:column;align-items:center;gap:10px;padding:44px 16px;"
                 "margin:24px 0;border:2px dashed var(--line);border-radius:14px;color:var(--muted);text-align:center}"
                 ".drop.on{border-color:var(--ink);background:var(--hover)}.drop b{color:var(--ink);font-size:17px}"
                 ".drop label{cursor:pointer}.up{display:flex;align-items:center;gap:12px;padding:10px 4px;"
                 "border-bottom:1px solid var(--line)}.up .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;"
                 "white-space:nowrap}.up .s{color:var(--muted);font-size:13px;white-space:nowrap}"
                 ".up.bad .s{color:var(--accent)}.up progress{width:120px}</style>")
        headers = dict(_SHARE_HEADERS)
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"] + "; script-src 'self'; connect-src 'self'"
        self._send(200, _share_shell("Send files", body, style).encode("utf-8"), "text/html; charset=utf-8", headers)

    def _request_upload(self, sid, rec, root):
        """A file sent through a file request: stored in the folder under a name that is free."""
        limit = int(rec.get("up") or REQUEST_MAX)
        if "chunked" not in (self.headers.get("Transfer-Encoding") or "").lower():
            if int(self.headers.get("Content-Length") or 0) > limit:
                return self._error(413, "That file is too big for this link")
        folder = self.drive.index.path_of(root["id"])
        name = clean_name(self._query().get("name"))
        if name.lower().endswith((".ddsync-part",)) or name.startswith("."):
            name = "_" + name
        path = free_name(self.drive, posixpath.join(folder, name))
        body = _Body(self, limit)
        expected = body.left
        try:
            n = self.drive.fs.put_file(path, body)
            if expected is not None and n != expected:
                raise OSError(errno.EIO, "the upload was cut off")
        except OSError as e:
            try:
                self.drive.fs.unlink(path)
            except OSError:
                pass
            self.close_connection = True
            return self._error(413 if e.errno == errno.EFBIG else 400, "That file is too big for this link"
                               if e.errno == errno.EFBIG else "The upload did not arrive completely")
        self.app.shares.viewed(sid)             # for a file request, "views" counts the files received
        log.info("A file request received %s (%d bytes)", posixpath.basename(path), n)
        self._json({"ok": True, "name": posixpath.basename(path), "size": n})

    # ------------------------------------------------------------ WebDAV
    # The drive for other apps (file managers on phones, rclone, "map network drive"): the dashboard's
    # user name and password, at /dav/. Password-locked folders are never shown here.
    def _dav_auth(self):
        import base64
        import hashlib
        import hmac
        header = self.headers.get("Authorization") or ""
        addr = "dav:" + self._addr()
        user, pw = self.app.credentials()
        ok = False
        if header.lower().startswith("basic ") and user and pw and not self.app.locked_for(addr):
            digest = hashlib.sha256(header.encode() + pw.encode()).hexdigest()
            good = getattr(self.app, "_dav_ok", {})
            if good.get(digest, 0) > time.time():
                return True
            try:
                given_user, _, given_pw = base64.b64decode(header[6:]).decode("utf-8", "replace").partition(":")
            except ValueError:
                given_user = given_pw = ""
            ok = hmac.compare_digest(given_user.lower(), user.lower()) and check_password(given_pw, pw)
            self.app.note_login(addr, ok)
            if ok:
                good = {k: v for k, v in good.items() if v > time.time()}
                good[digest] = time.time() + 600
                self.app._dav_ok = good
        if not ok:
            self._send(401, b"Sign in with the dashboard's user name and password.", "text/plain; charset=utf-8",
                       {"WWW-Authenticate": 'Basic realm="DDrive", charset="UTF-8"', "DAV": "1, 2"})
        return ok

    def _dav_path(self, raw):
        p = urllib.parse.unquote(urllib.parse.urlsplit(raw).path)
        if not (p == "/dav" or p.startswith("/dav/")):
            raise OSError(errno.EINVAL, "outside the drive")
        return "/" + "/".join(x for x in p[4:].split("/") if x and x not in (".", ".."))

    def _dav_props(self, path, node):
        href = urllib.parse.quote("/dav" + (path if path != "/" else "") + ("/" if node["is_dir"] else ""))
        if node["is_dir"]:
            kind, more = "<D:collection/>", ""
        else:
            ctype = mimetypes.guess_type(node["name"])[0] or "application/octet-stream"
            kind = ""
            more = (f"<D:getcontentlength>{node['size']}</D:getcontentlength><D:getcontenttype>{_x(ctype)}</D:getcontenttype>"
                    f"<D:getetag>\"{node['uid']}-{node['version']}\"</D:getetag>")
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(node["ctime"] or node["mtime"] or 0))
        return (f"<D:response><D:href>{_x(href)}</D:href><D:propstat><D:prop>"
                f"<D:displayname>{_x(node['name'] or 'DDrive')}</D:displayname>"
                f"<D:resourcetype>{kind}</D:resourcetype>{more}"
                f"<D:getlastmodified>{formatdate(node['mtime'] or 0, usegmt=True)}</D:getlastmodified>"
                f"<D:creationdate>{created}</D:creationdate>"
                f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")

    def _dav_remove(self, path, node):
        fs = self.drive.fs
        if node["is_dir"]:
            for c in self.drive.index.children(node["id"]):
                self._dav_remove(posixpath.join(path, c["name"]), c)
            fs.rmdir(path)
        else:
            fs.unlink(path)

    def _dav_copy(self, src, node, dst):
        fs = self.drive.fs
        if node["is_dir"]:
            fs.mkdir(dst, 0o755)
            for c in self.drive.index.children(node["id"]):
                cp = posixpath.join(src, c["name"])
                if not fs._hidden(cp):
                    self._dav_copy(cp, c, posixpath.join(dst, c["name"]))
            return

        class Reader:
            pos = 0

            def read(r, n):
                data = fs.read_file(node["id"], r.pos, min(n, max(0, node["size"] - r.pos))) if r.pos < node["size"] else b""
                r.pos += len(data)
                return data

        fs.put_file(dst, Reader(), mtime=node["mtime"])

    def _dav(self, method):
        if not getattr(self.drive.cfg, "webdav_enabled", False):
            return self._send(404, b"WebDAV is turned off (Settings in the dashboard).", "text/plain; charset=utf-8")
        if method == "OPTIONS":
            return self._send(200, b"", "text/plain", {"DAV": "1, 2", "MS-Author-Via": "DAV",
                                                       "Allow": "GET, HEAD, " + ", ".join(DAV_METHODS)})
        if not self._dav_auth():
            return
        fs, idx = self.drive.fs, self.drive.index
        fs.scope.unlocked = frozenset()                       # locked folders stay out of reach here
        try:
            path = self._dav_path(self.path)
            node = None if fs._hidden(path) else idx.resolve(path)
            if method in ("GET", "HEAD"):
                if node is None:
                    return self._send(404, b"Not found", "text/plain")
                if node["is_dir"]:
                    names = "\n".join(c["name"] + ("/" if c["is_dir"] else "") for c in idx.children(node["id"])
                                      if not fs._hidden(posixpath.join(path, c["name"])))
                    return self._send(200, names.encode("utf-8"), "text/plain; charset=utf-8")
                return self._stream(node, download=True)
            if method == "PROPFIND":
                _Body(self).read(1 << 20)
                if node is None:
                    return self._send(404, b"Not found", "text/plain")
                parts = [self._dav_props(path, node)]
                if node["is_dir"] and (self.headers.get("Depth") or "1") != "0":
                    for c in idx.children(node["id"]):
                        cp = posixpath.join(path, c["name"])
                        if not fs._hidden(cp):
                            parts.append(self._dav_props(cp, c))
                xml = '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + "".join(parts) + "</D:multistatus>"
                return self._send(207, xml.encode("utf-8"), "application/xml; charset=utf-8")
            if method == "PROPPATCH":
                _Body(self).read(1 << 20)
                if node is None:
                    return self._send(404, b"Not found", "text/plain")
                xml = (f'<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:"><D:response><D:href>'
                       f'{_x(urllib.parse.quote("/dav" + path))}</D:href><D:propstat><D:prop/><D:status>HTTP/1.1 200 OK'
                       f'</D:status></D:propstat></D:response></D:multistatus>')
                return self._send(207, xml.encode("utf-8"), "application/xml; charset=utf-8")
            if method == "MKCOL":
                if node is not None:
                    return self._send(405, b"Already there", "text/plain")
                if idx.resolve(posixpath.dirname(path)) is None:
                    return self._send(409, b"The folder above it doesn't exist", "text/plain")
                fs.mkdir(path, 0o755)
                return self._send(201, b"", "text/plain")
            if method == "PUT":
                if path == "/" or (node is not None and node["is_dir"]):
                    return self._send(405, b"That is a folder", "text/plain")
                parent = idx.resolve(posixpath.dirname(path))
                if parent is None or not parent["is_dir"]:
                    return self._send(409, b"The folder above it doesn't exist", "text/plain")
                body = _Body(self)
                expected = body.left
                n = fs.put_file(path, body)
                if expected is not None and n != expected:
                    self.close_connection = True
                    return self._send(400, b"The upload was cut off", "text/plain")
                return self._send(204 if node is not None else 201, b"", "text/plain")
            if method == "DELETE":
                if node is None:
                    return self._send(404, b"Not found", "text/plain")
                if path == "/":
                    return self._send(403, b"The drive itself can't be deleted", "text/plain")
                self._dav_remove(path, node)
                return self._send(204, b"", "text/plain")
            if method in ("MOVE", "COPY"):
                if node is None:
                    return self._send(404, b"Not found", "text/plain")
                dst = self._dav_path(self.headers.get("Destination") or "")
                if path == "/" or dst == "/" or dst == path or (dst + "/").startswith(path + "/"):
                    return self._send(403, b"Can't be put there", "text/plain")
                if fs._hidden(dst) or idx.resolve(posixpath.dirname(dst)) is None:
                    return self._send(409, b"The folder above it doesn't exist", "text/plain")
                there = idx.resolve(dst)
                if there is not None:
                    if (self.headers.get("Overwrite") or "T").upper() == "F":
                        return self._send(412, b"Something is already there", "text/plain")
                    self._dav_remove(dst, there)
                if method == "MOVE":
                    fs.rename(path, dst)
                else:
                    self._dav_copy(path, node, dst)
                return self._send(204 if there is not None else 201, b"", "text/plain")
            if method == "LOCK":                                  # no real locking: enough for Windows and macOS to write
                _Body(self).read(1 << 20)
                token = "opaquelocktoken:" + os.urandom(16).hex()
                xml = (f'<?xml version="1.0" encoding="utf-8"?><D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
                       f'<D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope><D:depth>infinity</D:depth>'
                       f'<D:timeout>Second-3600</D:timeout><D:locktoken><D:href>{token}</D:href></D:locktoken>'
                       f'</D:activelock></D:lockdiscovery></D:prop>')
                return self._send(200, xml.encode("utf-8"), "application/xml; charset=utf-8", {"Lock-Token": f"<{token}>"})
            if method == "UNLOCK":
                return self._send(204, b"", "text/plain")
            return self._send(405, b"Not supported", "text/plain")
        except OSError as e:
            code = {errno.ENOENT: 404, errno.EEXIST: 405, errno.ENOTDIR: 409, errno.EISDIR: 405, errno.ENOTEMPTY: 409,
                    errno.ENOSPC: 507, errno.EFBIG: 413, errno.EACCES: 403, errno.EINVAL: 400}.get(e.errno or 0, 500)
            self.close_connection = True
            self._send(code, (e.strerror or str(e)).encode("utf-8", "replace"), "text/plain; charset=utf-8")


class ApiErrorProxy(Exception):
    """Raised here, turned into web.ApiError by the dispatcher (avoids importing web at the top)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
