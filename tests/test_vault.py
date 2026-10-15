"""End-to-end encrypted accounts: the device seals everything, the host stores and returns sealed
things and can read none of it. Here the host is a whole drive with its dashboard on a free local
port, and the device is the reference client (ddrive/vault.py) talking to it over HTTP."""

import base64
import contextlib
import http.client
import io
import json
import os
import time
from unittest import mock
import urllib.parse

import helpers
from ddrive import lite, litesync, vault, words

DEVICE = "0123456789abcdef"

MARK = b"PLAINTEXT-MARKER-that-must-never-reach-the-host"


class SealingTest(helpers.unittest.TestCase):
    def test_keys_come_from_the_words_and_not_back(self):
        key = bytes(range(32))
        # fixed values: the browser and the phone app must make the same ones
        self.assertEqual(vault.auth_key(key).hex(), "9e77be91e22334a7c62fc9af11fe1557deb0979fd3e1fa65b111d9b0677f0370")
        self.assertEqual(vault.meta_key(key).hex(), "2e5f3da69a3530aec13f2344ba1fc6f8d5a96d6c63242920fe76955a07ef3794")
        self.assertEqual(len(vault.auth_key(key)), 32)
        self.assertNotEqual(vault.auth_key(key), vault.meta_key(key))
        self.assertNotEqual(vault.auth_key(key), key)
        # RFC 5869 test case 3 (no salt, no info) checks the HKDF itself
        okm = vault.hkdf(bytes.fromhex("0b" * 22), b"")
        self.assertEqual(okm.hex(), "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d")

    def test_sealed_things(self):
        key = os.urandom(32)
        blob = vault.seal(key, b"hello", b"label")
        self.assertEqual(len(blob), 5 + vault.OVERHEAD)
        self.assertEqual(vault.unseal(key, blob, b"label"), b"hello")
        for bad in (lambda: vault.unseal(key, blob, b"other"), lambda: vault.unseal(os.urandom(32), blob, b"label"),
                    lambda: vault.unseal(key, blob[:-1] + bytes([blob[-1] ^ 1]), b"label")):
            with self.assertRaises(vault.VaultError):
                bad()
        self.assertEqual(vault.unpack(vault.pack([b"a", b"", b"ccc"])), [b"a", b"", b"ccc"])

    def test_a_file_as_an_object(self):
        key = os.urandom(32)
        for size in (0, 1, 15, 16, 17, 48, 100):                       # pieces of 16 bytes
            data = os.urandom(size)
            obj = b"".join(vault.seal_stream(key, io.BytesIO(data).read, size, log=4))
            self.assertEqual(len(obj), vault.sealed_size(size, 4))
            asked = []

            def fetch(a, n):
                asked.append(n)
                return obj[a:a + n]
            self.assertEqual(vault.open_range(key, fetch, size, log=4), data)
            for start, length in ((0, 1), (5, 20), (16, 16), (max(0, size - 3), 10), (size, 5)):
                self.assertEqual(vault.open_range(key, fetch, size, start, length, log=4), data[start:start + length])
        # only the pieces that are needed are asked for
        asked.clear()
        vault.open_range(key, fetch, 100, 40, 5, log=4)
        self.assertEqual(asked, [16 + vault.OVERHEAD])
        # pieces can't be swapped, left out, or the file cut short
        piece = 16 + vault.OVERHEAD
        swapped = obj[:5] + obj[5 + piece:5 + 2 * piece] + obj[5:5 + piece] + obj[5 + 2 * piece:]
        for bad_obj, bad_size in ((swapped, 100), (obj[:5 + 2 * piece], 32), (obj, 96)):
            with self.assertRaises(vault.VaultError):
                vault.open_range(key, lambda a, n: bad_obj[a:a + n], bad_size, log=4)


class VaultTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.host = self.drive(web_token="secret-token")
        self.host.cfg.signup_mode = "public"
        self.web = self.host.start_web(port=0)
        self.address = f"http://127.0.0.1:{self.web.port}"
        self.key = os.urandom(32)                                      # stands for 24 words that stay on the device
        self.auth = vault.auth_key(self.key).hex()
        self.assertEqual(self.form("/signup", {"step": "e2e", "auth": self.auth})[0], 303)
        self.device = self.new_device()

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def form(self, path, fields):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        conn.request("POST", path, urllib.parse.urlencode(fields), {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def new_device(self, key=None):
        key = key or self.key
        return vault.Vault(lite.HostClient(self.address, auth=vault.auth_key(key).hex()), key).sync()

    def account_drive(self):
        aid = next(iter(self.web.accounts._recs))
        return self.web.accounts.acquire(aid)

    def upload_account(self):
        """Upload what the account's drive still has waiting (tests run its uploads themselves)."""
        d = self.account_drive()
        try:
            for nid in d.index.unsynced_ids():
                d.uploader.run_now(nid)
        finally:
            self.web.accounts.release(d)

    def objects(self):
        """The ids of the sealed objects the host holds for the account."""
        d = self.account_drive()
        try:
            top = d.index.resolve("/o")
            return [c["name"] for sub in (d.index.children(top["id"]) if top else []) for c in d.index.children(sub["id"])]
        finally:
            self.web.accounts.release(d)

    def test_files_go_there_sealed_and_come_back(self):
        v = self.device
        self.assertEqual(v.client.status()["e2e"], True)
        v.mkdir("/Tax returns")
        secret = MARK + os.urandom(3 * 1024 * 1024)                    # several pieces
        v.put("/Tax returns/2025 secret.pdf", secret, mtime=1_700_000_000)
        v.put("/empty.txt", b"")
        self.assertEqual([(n["name"], n["dir"]) for n in v.list("/")], [("Tax returns", True), ("empty.txt", False)])
        self.assertEqual(v.read("/Tax returns/2025 secret.pdf"), secret)
        self.assertEqual(v.read("/Tax returns/2025 secret.pdf", 1024 * 1024 - 5, 10), secret[1024 * 1024 - 5:1024 * 1024 + 5])
        self.assertEqual(v.read("/empty.txt"), b"")
        # another device of the same account ends up with the same list, and the changes it makes come here
        other = self.new_device()
        self.assertEqual(other.nodes, v.nodes)
        other.move("/Tax returns/2025 secret.pdf", "/renamed.pdf")
        other.put("/empty.txt", b"not empty any more")
        v.sync()
        self.assertEqual(sorted(n["name"] for n in v.list("/")), ["Tax returns", "empty.txt", "renamed.pdf"])
        self.assertEqual((v.read("/renamed.pdf")[:len(MARK)], v.read("/empty.txt")), (MARK, b"not empty any more"))
        v.delete("/Tax returns")
        v.delete("/renamed.pdf")
        self.assertEqual([n["name"] for n in other.sync().list("/")], ["empty.txt"])
        # what was deleted is kept until it is purged, and what was replaced is kept as an earlier version
        gone = [n for n in other.nodes.values() if n.get("trash")]
        self.assertEqual(sorted(n["trash"]["path"] for n in gone), ["/Tax returns", "/renamed.pdf"])
        self.assertIsNone(other.resolve("/renamed.pdf"))
        was = other.resolve("/empty.txt")["versions"]
        self.assertEqual([(x["size"]) for x in was], [0])
        held = set(self.objects())
        self.assertIn(was[0]["obj"], held)
        pdf = next(n for n in gone if n["name"] == "renamed.pdf")
        self.assertIn(pdf["obj"], held)
        other.purge([n["id"] for n in gone])                           # now they are gone, and their sealed content with them
        self.assertNotIn(pdf["obj"], set(self.objects()))
        self.assertEqual([n["name"] for n in v.sync().nodes.values()], ["empty.txt"])
        # the wrong words open nothing: there isn't even such an account
        with self.assertRaises(lite.HostError) as e:
            self.new_device(os.urandom(32))
        self.assertEqual(e.exception.status, 401)

    def test_the_host_sees_nothing(self):
        v = self.device
        v.mkdir("/Very private folder")
        v.put("/Very private folder/diary.txt", MARK * 2000, mtime=1_700_000_000)
        d = self.account_drive()
        try:
            # its list of this account's files: meaningless names only
            names, todo = [], [d.index.resolve("/")]
            while todo:
                n = todo.pop()
                for c in d.index.children(n["id"]):
                    names.append(c["name"])
                    if c["is_dir"]:
                        todo.append(c)
            self.assertTrue(names)
            for name in names:
                self.assertRegex(name, r"^(o|log|[0-9a-f]{2}|[0-9a-f]{32}|\d{12})$")
            # everything it holds for the account, in the clear as far as it can make it so: no name, no content
            for folder, _, files in os.walk(self.tmp):
                for f in files:
                    with open(os.path.join(folder, f), "rb") as fh:
                        data = fh.read()
                    for needle in (MARK, b"diary", b"Very private", self.key, self.key.hex().encode()):
                        self.assertNotIn(needle, data, os.path.join(folder, f))
            helpers.DriveTest.upload(d)                                 # ...and what goes to "Discord" is sealed twice
        finally:
            self.web.accounts.release(d)
        for folder, _, files in os.walk(self.channel):
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    self.assertNotIn(MARK, fh.read())
        # the account's list of accounts on the host says "end-to-end", and has no way to the key
        rec = next(iter(self.web.accounts._recs.values()))
        self.assertTrue(rec["e2e"])
        self.assertNotIn(self.key.hex(), json.dumps(self.web.accounts._secrets))

    def test_what_the_host_refuses(self):
        v = self.device
        v.put("/a.txt", b"x")
        c = v.client
        # everything that would need the files in the clear on the host
        for path in ("/api/list?path=/", "/api/file?path=/o", "/api/search?q=aa", "/api/zip?path=/", "/api/home", "/api/notes",
                     "/api/sync/tree?path=/", "/api/upload/part?path=/x&size=1"):
            with self.assertRaises(lite.HostError) as e:
                c.request("GET", path)
            self.assertEqual(e.exception.status, 403, path)
        for path in ("/api/upload?path=/plain.txt", "/api/mkdir", "/api/share", "/api/lite/sync/hello", "/api/fetch"):
            with self.assertRaises(lite.HostError) as e:
                c.request("POST", path, b"{}", {"Content-Type": "application/json"})
            self.assertEqual(e.exception.status, 403, path)
        # the words themselves don't sign in (they would have to be sent), and an auth key is no owner
        phrase = words.key_to_phrase(self.key)
        self.assertEqual(self.form("/login", {"phrase": phrase})[0], 401)
        owner = self.host.cfg.encryption_key
        self.assertEqual(self.form("/login", {"auth": owner})[0], 401)
        self.assertEqual(self.form("/login", {"auth": "zz"})[0], 401)
        # the owner, and an account of the earlier kind, have no vault
        boss = lite.HostClient(self.address, words.key_to_phrase(bytes.fromhex(owner)))
        with self.assertRaises(lite.HostError) as e:
            boss.request("GET", "/api/vault")
        self.assertEqual(e.exception.status, 403)
        self.assertEqual(boss.list("/"), [])                            # and nothing of the account shows in the owner's drive
        for bad in ("/api/vault/object?id=../../etc", "/api/vault/object?id=" + "0" * 32):
            with self.assertRaises(lite.HostError) as e:
                c.request("GET", bad)
            self.assertIn(e.exception.status, (400, 404))

    def test_the_pages(self):
        def get(path, cookie=""):
            conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
            conn.request("GET", path, headers={"Cookie": f"dd_session={cookie}"} if cookie else {})
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, data
        status, text = get("/wordlist.txt")                            # the words a phrase is written in: for anybody
        self.assertEqual((status, len(text.split())), (200, 2048))
        page = get("/signup")[1]
        self.assertIn(b"e2e-make", page)                               # the words are made in the browser...
        self.assertNotIn(b'name="phrase"', page)                       # ...and the page has nowhere to put them for sending
        for name in ("/vaultcore.js", "/e2e.js", "/e2e-api.js", "/sw.js", "/verify.js"):
            self.assertEqual(get(name)[0], 200, name)
        self.device.client.status()
        # its Queue page: what the host is storing on Discord for it, by the names the host has (sealed things)
        status, text = get("/api/queue", self.device.client.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(text)) & {"uploads", "queue", "queued", "downloads", "offline"},
                         {"uploads", "queue", "queued", "downloads", "offline"})
        self.assertEqual(get("/api/activity", self.device.client.cookie)[0], 403)       # (what the computer itself is doing stays the owner's)
        mine = get("/", self.device.client.cookie)[1]                  # the same dashboard as everybody, with what answers it in the browser
        self.assertIn(b"e2e-api.js", mine)
        self.assertLess(mine.index(b"e2e-api.js"), mine.index(b'"/app.js'))       # (the address carries its version: /app.js?v=..)

    def test_what_a_snapshot_refers_to_is_kept(self):
        """The dashboard keeps snapshots: the list as it was, a sealed file whose entry is in no folder.
        A device that replaces a file again and again must not have the host forget what a snapshot holds."""
        v = self.new_device()
        v.name = "my laptop"
        v.put("/diary.txt", b"as it was on the day of the snapshot", mtime=1_700_000_000)
        first = dict(v.resolve("/diary.txt"))
        self.assertEqual((first["by"], first["at"] > 1_700_000_000), ("my laptop", True))       # who added it and when, for Activity
        listing = json.dumps({"v": 1, "at": time.time(), "entries": [
            {k: first[k] for k in ("id", "parent", "name", "dir", "size", "mtime", "obj", "key")}]}).encode()
        key, oid, sid = os.urandom(32), vault.new_id(), vault.new_id()
        v._store(oid, key, listing, None, len(listing))
        v._send({"op": "put", "id": sid, "parent": "~snapshots", "name": sid, "dir": False, "size": len(listing), "mtime": time.time(),
                 "obj": oid, "key": base64.b64encode(key).decode(), "snap": {"at": time.time(), "label": "Manual", "files": 1, "bytes": 36}})
        other = self.new_device()                                      # another device: it has to find the snapshot itself
        for n in range(vault.VERSIONS_KEPT + 3):
            other.put("/diary.txt", b"rewritten %d" % n, mtime=1_700_000_100 + n)
        self.assertEqual(len(other.resolve("/diary.txt")["versions"]), vault.VERSIONS_KEPT)
        self.assertNotIn(first["obj"], [x["obj"] for x in other.resolve("/diary.txt")["versions"]])
        kept = vault.open_range(base64.b64decode(first["key"]), lambda a, n: other._fetch(first["obj"], a, n), first["size"], 0, None)
        self.assertEqual(kept, b"as it was on the day of the snapshot")
        self.assertEqual([n["name"] for n in other.files()], ["diary.txt"])          # the snapshot is in no folder
        self.assertIsNone(other.resolve("/~snapshots"))
        # without the snapshot, the next rewrite lets go of it
        other._send({"op": "del", "id": sid})
        other.put("/diary.txt", b"once more", mtime=1_700_000_200)
        other._drop([first["obj"]])
        with self.assertRaises(Exception):
            other._fetch(first["obj"], 0, 10)

    def test_a_link_hands_out_sealed_things_only(self):
        """A share link of an end-to-end account: the key is in the link and never comes here. The host
        keeps a sealed description and gives out exactly the sealed objects the link names."""
        v = self.device
        v.put("/holiday.txt", MARK + b" on the beach", mtime=1_700_000_000)
        v.put("/private.txt", MARK + b" not shared", mtime=1_700_000_000)
        shared, private = v.resolve("/holiday.txt"), v.resolve("/private.txt")
        c = v.client
        sealed = base64.b64encode(os.urandom(200)).decode()              # (the browser seals this with the link's key)
        own = base64.b64encode(os.urandom(60)).decode()
        s = c._json("/api/vault/share", {"objs": [shared["obj"]], "sealed": sealed, "own": own, "hours": 24, "password": True})
        self.assertEqual((s["own"], s["password"], s["views"]), (own, True, 0))
        self.assertAlmostEqual(s["expires"], time.time() + 86400, delta=60)
        self.assertEqual([x["id"] for x in c._json("/api/vault/shares")["items"]], [s["id"]])

        def visit(tail, headers=None):                                   # somebody with the link, signed in nowhere
            conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
            conn.request("GET", f"/v/{s['id']}{tail}", headers=headers or {})
            r = conn.getresponse()
            body = r.read()
            conn.close()
            return r.status, body, r
        status, page, r = visit("")
        self.assertEqual(status, 200)
        self.assertIn(b"/vshare.js", page)
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(json.loads(visit("/m")[1]), {"sealed": sealed, "password": True, "expires": s["expires"]})
        status, body, _ = visit(f"/o/{shared['obj']}")
        self.assertEqual(status, 200)
        self.assertEqual(vault.open_range(base64.b64decode(shared["key"]), lambda a, n: body[a:a + n], shared["size"], 0, None), MARK + b" on the beach")
        self.assertNotIn(MARK, body)
        self.assertEqual(visit(f"/o/{shared['obj']}", {"Range": "bytes=0-4"})[1], b"DDV1" + bytes([20]))
        self.assertEqual(visit(f"/o/{private['obj']}")[0], 404)          # only what the link names
        self.assertEqual(visit("/o/" + "0" * 32)[0], 404)
        self.assertEqual(c._json("/api/vault/shares")["items"][0]["views"], 1)
        # what a link hands out isn't forgotten while the link works, whatever a device asks
        c._json("/api/vault/delete", {"ids": [shared["obj"], private["obj"]]})
        self.assertEqual(visit(f"/o/{shared['obj']}")[0], 200)
        with self.assertRaises(lite.HostError):
            v._fetch(private["obj"], 0, 5)
        # a change (another password is another key: sealed again by the browser), then no link
        again = base64.b64encode(os.urandom(100)).decode()
        u = c._json("/api/vault/share/update", {"id": s["id"], "hours": 0, "sealed": again, "password": False})
        self.assertEqual((u["expires"], u["password"], u["own"]), (None, False, own))
        self.assertEqual(json.loads(visit("/m")[1])["sealed"], again)
        with self.assertRaises(lite.HostError):
            c._json("/api/vault/share", {"objs": ["../../etc"], "sealed": sealed, "own": own})
        self.assertEqual(c._json("/api/vault/share/revoke", {"id": s["id"]})["objs"], [shared["obj"]])
        self.assertEqual((visit("/m")[0], visit(f"/o/{shared['obj']}")[0]), (404, 404))
        self.assertEqual(c._json("/api/vault/shares")["items"], [])
        # the other kind of link address doesn't open these, and an owner's account has none of this
        self.assertEqual(visit("")[0], 200)                              # (the page itself says the link has expired)
        with self.assertRaises(lite.HostError) as e:
            lite.HostClient(self.address, words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key)))._json("/api/vault/shares")
        self.assertIn(e.exception.status, (403, 404))

    def test_the_account_as_a_drive_letter(self):
        """What a drive letter does with an end-to-end account (lite.LiteFS on lite.VaultClient): files are
        opened and sealed on the computer, and the host still sees nothing."""
        import errno
        fs = lite.LiteFS(lite.VaultClient(lite.HostClient(self.address, auth=self.auth), self.key, "my laptop"),
                         tmp_dir=os.path.join(self.tmp, "lite-tmp"), block=64 * 1024, cache_bytes=256 * 1024)

        def save(path, data):
            fh = fs.create(path, 0o644)
            for off in range(0, len(data), 50000):
                fs.write(path, data[off:off + 50000], off, fh)
            fs.flush(path, fh)
            fs.release(path, fh)

        def load(path, offset=0, size=None):
            fh = fs.open(path, os.O_RDONLY)
            try:
                return fs.read(path, fs.getattr(path)["st_size"] if size is None else size, offset, fh)
            finally:
                fs.release(path, fh)
        big = MARK + os.urandom(2_500_000)
        fs.mkdir("/Work", 0o755)
        save("/Work/report.bin", big)
        save("/Work/notes.txt", MARK + b" typed in an editor")
        self.assertEqual(sorted(fs.readdir("/Work", None)), [".", "..", "notes.txt", "report.bin"])
        self.assertEqual(fs.getattr("/Work/report.bin")["st_size"], len(big))
        self.assertEqual(load("/Work/report.bin"), big)
        self.assertEqual(load("/Work/report.bin", 1_048_570, 20), big[1_048_570:1_048_590])
        v = self.new_device()                                          # another device of the account
        self.assertEqual(v.read("/Work/notes.txt"), MARK + b" typed in an editor")
        self.assertEqual(v.resolve("/Work/notes.txt")["by"], "my laptop")
        v.put("/Work/from the phone.txt", b"added elsewhere", mtime=1_700_000_000)
        time.sleep(lite.LIST_SECONDS + 0.2)
        self.assertEqual(load("/Work/from the phone.txt"), b"added elsewhere")
        self.assertEqual(fs.getattr("/Work/from the phone.txt")["st_mtime"], 1_700_000_000)
        # changing a file, saving over one the way programs do, renaming, deleting
        fh = fs.open("/Work/notes.txt", os.O_RDWR)
        fs.write("/Work/notes.txt", b"TYPED", len(MARK) + 1, fh)
        fs.release("/Work/notes.txt", fh)
        self.assertEqual(load("/Work/notes.txt"), MARK + b" TYPED in an editor")
        save("/Work/notes.tmp", b"second draft")
        fs.rename("/Work/notes.tmp", "/Work/notes.txt")
        self.assertEqual(load("/Work/notes.txt"), b"second draft")
        fs.rename("/Work/report.bin", "/report.bin")
        self.assertEqual(load("/report.bin", 0, 10), big[:10])
        with self.assertRaises(OSError) as e:
            fs.rmdir("/Work")
        self.assertEqual(e.exception.errno, errno.ENOTEMPTY)
        fs.unlink("/Work/notes.txt")
        fs.unlink("/Work/from the phone.txt")
        fs.rmdir("/Work")
        self.assertEqual(sorted(fs.readdir("/", None)), [".", "..", "report.bin"])
        with self.assertRaises(OSError) as e:
            fs.getattr("/Work/notes.txt")
        self.assertEqual(e.exception.errno, errno.ENOENT)
        v.sync()                                                       # what was deleted is kept under Deleted
        self.assertIn("/Work", [n["trash"]["path"] for n in v.nodes.values() if n.get("trash")])
        for folder, _, files in os.walk(os.path.join(self.tmp, "aaaa")):
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    data = fh.read()
                for needle in (MARK, b"report.bin", b"notes.txt", b"second draft"):
                    self.assertNotIn(needle, data, os.path.join(folder, f))

    def test_a_file_request_takes_sealed_files_from_anybody(self):
        """A file request of an end-to-end account: somebody without an account sends sealed objects and,
        for each, a sealed note. The host keeps them for the account's browser and can open neither."""
        c = self.device.client
        sealed, own = base64.b64encode(os.urandom(80)).decode(), base64.b64encode(os.urandom(90)).decode()
        s = c._json("/api/vault/share", {"objs": [], "sealed": sealed, "own": own, "hours": 24, "upload": True, "max_mb": 1})
        self.assertEqual((s["upload"], s["drops"], s["received"]), (True, [], 0))

        def visitor(method, tail, body=None, headers=None, sid=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
            conn.request(method, f"/v/{sid or s['id']}{tail}", body, {"X-DD": "1", **(headers or {})})
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, data
        self.assertEqual(json.loads(visitor("GET", "/m")[1]), {"sealed": sealed, "password": False, "expires": s["expires"],
                                                                "upload": True, "max": 1024 * 1024})
        # the sender's browser seals the file with a key of its own (here: the reference client's sealing)
        key, oid = os.urandom(32), vault.new_id()
        content = MARK + os.urandom(300_000)
        src = io.BytesIO(content)
        obj = b"".join(vault.seal_stream(key, src.read, len(content)))
        self.assertEqual(len(obj), vault.sealed_size(len(content)))
        status, body = visitor("POST", f"/o?id={oid}&size={len(obj)}&offset=0", obj[:200_000])
        self.assertEqual((status, json.loads(body)["offset"]), (200, 200_000))
        status, body = visitor("POST", f"/o?id={oid}&size={len(obj)}&offset=200000", obj[200_000:])
        self.assertTrue(json.loads(body)["done"], body)
        note = base64.b64encode(os.urandom(120)).decode()               # (sealed with the link's key by the sender's browser)
        self.assertEqual(visitor("POST", "/n", json.dumps({"obj": oid, "sealed": note}))[0], 200)
        self.assertEqual(visitor("POST", "/n", json.dumps({"obj": oid, "sealed": note}))[0], 200)         # twice: kept once
        # what a sender may not do
        self.assertEqual(visitor("POST", f"/o?id={vault.new_id()}&size={vault.sealed_size(1024 * 1024) + 1}&offset=0", b"x")[0], 413)
        self.assertEqual(visitor("POST", f"/o?id={oid}&size=10&offset=0", b"x" * 10)[0], 409)             # not over what is there
        self.assertEqual(visitor("POST", "/n", json.dumps({"obj": vault.new_id(), "sealed": note}))[0], 400)
        self.assertEqual(visitor("POST", "/o?id=../../x&size=1&offset=0", b"x")[0], 400)
        self.assertEqual(visitor("GET", f"/o/{oid}")[0], 404)            # senders see nothing of the folder, not even their own file
        # the account's browser finds the note, opens the file with the key from it, and takes it into its list
        drops = c._json("/api/vault/shares")["items"][0]["drops"]
        self.assertEqual([(d["obj"], d["sealed"]) for d in drops], [(oid, note)])
        self.assertEqual(vault.open_range(key, lambda a, n: self.device._fetch(oid, a, n), len(content), 0, None), content)
        self.assertEqual(c._json("/api/vault/share/collect", {"id": s["id"], "objs": [oid]})["received"], 1)
        again = c._json("/api/vault/shares")["items"][0]
        self.assertEqual((again["drops"], again["received"]), ([], 1))
        for folder, _, files in os.walk(os.path.join(self.tmp, "aaaa")):
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    self.assertNotIn(MARK, fh.read(), os.path.join(folder, f))
        # an ordinary link takes no files, and a request that was turned off takes none either
        plain = c._json("/api/vault/share", {"objs": [], "sealed": sealed, "own": own})
        self.assertEqual(visitor("POST", f"/o?id={vault.new_id()}&size=10&offset=0", b"x" * 10, sid=plain["id"])[0], 404)
        c._json("/api/vault/share/revoke", {"id": s["id"]})
        self.assertEqual(visitor("POST", f"/o?id={vault.new_id()}&size=10&offset=0", b"x" * 10)[0], 404)

    def test_the_owners_page_signs_in_to_an_account_too(self):
        """Moving the drive into an end-to-end account from the dashboard (moving.js): the owner's page gets
        a session of the account next to its own, good for the account's sealed things and nothing else."""
        owner = lite.HostClient(self.address, words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key)))
        self.host.cfg.signup_mode = "closed"                           # the owner makes one whatever others may do
        key = os.urandom(32)
        auth = vault.auth_key(key).hex()
        with self.assertRaises(lite.HostError) as e:
            owner._json("/api/accounts/e2e", {"auth": auth})
        self.assertEqual(e.exception.status, 404)
        got = owner._json("/api/accounts/e2e", {"auth": auth, "create": True})
        self.assertTrue(got["made"])
        again = owner._json("/api/accounts/e2e", {"auth": auth})
        self.assertEqual((again["id"], again["made"]), (got["id"], False))
        with self.assertRaises(lite.HostError):
            owner._json("/api/accounts/e2e", {"auth": "zz"})
        with self.assertRaises(lite.HostError) as e:                   # an account can't hand out sessions
            self.device.client._json("/api/accounts/e2e", {"auth": auth, "create": True})
        self.assertEqual(e.exception.status, 403)
        owner._json("/api/mkdir", {"path": "/Mine"})

        def ask(path, session, cookie=owner.cookie):
            conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
            conn.request("GET", path, headers={"Cookie": f"dd_session={cookie}", **({"X-DD-Account": session} if session else {})})
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, data
        self.assertEqual(ask("/api/vault", got["session"])[0], 200)                    # the account's sealed things: as the account
        self.assertEqual(ask("/api/vault", "")[0], 403)                                # (the owner has none)
        self.assertEqual(ask("/api/vault", "made.up")[0], 401)
        self.assertEqual([i["name"] for i in json.loads(ask("/api/list?path=/", got["session"])[1])["items"]], ["Mine"])   # anything else: still the owner
        # the account works like any other from there on
        v = self.new_device(key)
        v.put("/moved.txt", MARK)
        self.assertEqual(self.new_device(key).read("/moved.txt"), MARK)

    def test_the_list_is_sealed_whole_now_and_then(self):
        v = self.device
        for i in range(5):
            v.put(f"/f{i}.txt", str(i).encode())
        state = v.client._json("/api/vault")
        self.assertEqual((state["seq"], state["snapshot"]), (5, None))
        v.snapshot()
        v.put("/after.txt", b"later")
        got = v.client._json("/api/vault/log?after=0")
        self.assertEqual((got["snapshot"]["seq"], [b[0] for b in got["batches"]]), (5, [6]))     # the changes before it are gone
        fresh = self.new_device()                                       # a new device starts from the sealed whole list
        self.assertEqual(sorted(n["name"] for n in fresh.list("/")), ["after.txt"] + [f"f{i}.txt" for i in range(5)])
        self.assertEqual(fresh.read("/f3.txt"), b"3")
        v.snapshot()           # the one before is dropped, once the new one is stored on Discord
        self.upload_account()
        v.client._json("/api/vault/log?after=0")
        self.assertEqual(len(self.objects()), 6 + 1)                    # six files and one sealed list

    def test_the_changes_stay_until_the_sealed_list_is_on_discord(self):
        v = self.device
        for i in range(3):
            v.put(f"/f{i}.txt", str(i).encode())
        d = self.account_drive()
        try:
            with mock.patch.object(d.uploader, "_process_upload", lambda nid: None):      # Discord is unreachable
                v.snapshot()
                log = d.index.resolve("/log")
                self.assertEqual(len(d.index.children(log["id"])), 3)                  # what it can be rebuilt from
                got = v.client._json("/api/vault/log?after=0")
                self.assertEqual((got["snapshot"]["seq"], got["batches"]), (3, []))   # but they aren't handed out again
        finally:
            self.web.accounts.release(d)
        self.upload_account()
        v.client._json("/api/vault/log?after=0")
        d = self.account_drive()
        try:
            self.assertEqual(d.index.children(d.index.resolve("/log")["id"]), [])    # stored: now they go
        finally:
            self.web.accounts.release(d)
        self.assertEqual(sorted(n["name"] for n in self.new_device().list("/")), ["f0.txt", "f1.txt", "f2.txt"])


class VaultSyncTest(VaultTest):
    """Folder syncing on a device of an end-to-end encrypted account: the files go sealed, and so does
    everything said about them between the device and the account's page in a browser."""

    def setUp(self):
        super().setUp()
        self.here = os.path.join(self.tmp, "Secret project")
        os.makedirs(self.here)
        self.agent = litesync.VaultAgent(lite.HostClient(self.address, auth=self.auth), self.key, DEVICE, "my laptop",
                                         data_dir=os.path.join(self.tmp, "lite")).start()
        self.addCleanup(self.agent.stop)
        self.page = self.device.client                                 # the account's page in a browser, as requests
        self.until(lambda: self.agent.online and self.shown(), "connecting")

    # what the browser does: seal and open with the account's key
    def seal(self, obj):
        data = json.dumps(obj).encode()
        return base64.b64encode(vault.seal(vault.meta_key(self.key), data, litesync.AAD_SYNC)).decode()

    def open(self, text):
        return json.loads(vault.unseal(vault.meta_key(self.key), base64.b64decode(text), litesync.AAD_SYNC)) if text else None

    def shown(self):
        devs = self.page._json("/api/vault/sync")["devices"]
        return devs[0] if devs else None

    def until(self, fn, what, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            got = fn()
            if got:
                return got
            time.sleep(0.05)
        self.fail(f"{what} did not happen")

    def set_jobs(self, jobs):
        d = self.shown()
        self.page._json("/api/vault/sync/jobs", {"device": DEVICE, "rev": d["rev"], "jobs": self.seal(jobs)})

    def run_job(self, jid):
        self.until(lambda: self.agent.job(jid), "the pair reaching the device")
        before = ((self.agent.status.get(jid) or {}).get("history") or [{}])[0].get("at")
        self.page._json("/api/vault/sync/tell", {"device": DEVICE, "sealed": self.seal({"do": "run", "id": jid})})

        def done():
            hist = (self.agent.status.get(jid) or {}).get("history") or []
            return hist[0] if hist and hist[0]["at"] != before and self.agent.running is None else None
        res = self.until(done, "the pair running")
        self.assertEqual(res["error"], "")
        return res

    def put(self, rel, data, age=100):
        p = os.path.join(self.here, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_a_device_syncs_into_the_account_and_the_host_learns_nothing(self):
        d = self.shown()
        self.assertEqual(self.open(d["info"])["name"], "my laptop")    # the browser can read who it is...
        self.put("plans.txt", MARK + b" plans")
        self.put("drafts/chapter one.txt", MARK + b" chapter")
        job = {"id": "aa11bb", "name": "Project", "local": self.here, "remote": "/Backed up/Project", "mode": "backup",
               "trigger": "manual", "every": 60, "at": "03:00", "exclude": [], "enabled": True}
        self.set_jobs([job])
        self.assertEqual(self.run_job("aa11bb")["up"], 2)
        v = self.new_device()                                          # another device of the account sees the files
        self.assertEqual(v.read("/Backed up/Project/plans.txt"), MARK + b" plans")
        self.assertEqual(v.read("/Backed up/Project/drafts/chapter one.txt"), MARK + b" chapter")
        self.assertAlmostEqual(v.resolve("/Backed up/Project/plans.txt")["mtime"],
                               os.path.getmtime(os.path.join(self.here, "plans.txt")), delta=0.01)
        self.assertEqual(self.run_job("aa11bb")["up"], 0)              # nothing is copied twice
        # how it went reaches the browser, sealed
        def reported():
            st = self.open(self.shown()["status"]) or {}
            return len(((st.get("jobs") or {}).get("aa11bb") or {}).get("history") or []) == 2
        self.until(reported, "the report")
        # the browser looks into a folder of the device, through the host
        got = self.page._json("/api/vault/sync/ask", {"device": DEVICE, "sealed": self.seal({"do": "browse", "path": self.here})})
        self.assertEqual([x["name"] for x in self.open(got["sealed"])["dirs"]], ["drafts"])
        # ...and all the host ever held of this is sealed: no device name, no folder, no file name
        held = json.dumps(self.web.sealed._devs[(next(iter(self.web.accounts._recs)), DEVICE)], default=str)
        for needle in ("my laptop", "Secret project", "Backed up", "plans", "chapter", "drafts"):
            self.assertNotIn(needle, held)
        for folder, _, files in os.walk(os.path.join(self.tmp, "aaaa")):          # the host's own folder
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    data = fh.read()
                for needle in (MARK, b"Secret project", b"plans.txt", b"chapter one", b"Backed up"):
                    self.assertNotIn(needle, data, os.path.join(folder, f))
        # both ways, and a change in the browser's copy of the pairs reaches the device
        self.set_jobs([dict(job, mode="two-way")])
        self.until(lambda: self.agent.job("aa11bb")["mode"] == "two-way", "the change")
        v.put("/Backed up/Project/from the phone.txt", b"added elsewhere", mtime=1_700_000_000)
        self.assertEqual(self.run_job("aa11bb")["down"], 1)
        with open(os.path.join(self.here, "from the phone.txt"), "rb") as f:
            self.assertEqual(f.read(), b"added elsewhere")
        # what isn't sealed with the account's key is not acted on
        self.page._json("/api/vault/sync/tell", {"device": DEVICE, "sealed": "bm90IHNlYWxlZA=="})
        other = base64.b64encode(vault.seal(os.urandom(32), b'{"do":"run","id":"aa11bb"}', litesync.AAD_SYNC)).decode()
        self.page._json("/api/vault/sync/tell", {"device": DEVICE, "sealed": other})
        time.sleep(1.5)
        self.assertEqual(len(self.agent.status["aa11bb"]["history"]), 3)
        # a change made from a stale copy is refused, so two browsers don't undo each other
        with self.assertRaises(lite.HostError) as e:
            self.page._json("/api/vault/sync/jobs", {"device": DEVICE, "rev": 0, "jobs": self.seal([])})
        self.assertEqual(e.exception.status, 409)

    def test_moving_waits_until_the_host_has_stored_it(self):
        self.put("film.mp4", os.urandom(200_000))
        self.set_jobs([{"id": "cc22dd", "name": "Films", "local": self.here, "remote": "/Films", "mode": "move",
                        "trigger": "manual", "every": 60, "at": "03:00", "exclude": [], "enabled": True}])
        res = self.run_job("cc22dd")
        self.assertEqual((res["up"], res["moved"]), (1, 0))            # sent, but the host hasn't stored it on Discord yet
        self.assertTrue(os.path.exists(os.path.join(self.here, "film.mp4")))
        d = self.account_drive()
        try:
            helpers.DriveTest.upload(d)                                 # now it has
        finally:
            self.web.accounts.release(d)
        time.sleep(3.5)                                                 # (the device asks the host about this every few seconds)
        self.assertEqual(self.run_job("cc22dd")["moved"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.here, "film.mp4")))
        self.assertEqual(len(self.new_device().read("/Films/film.mp4")), 200_000)

    def test_a_device_fetches_what_the_host_may_not_see(self):
        """A bookmark's preview and a file saved from a web address: the account's own device reads the
        site, the host only passes sealed messages on."""
        import http.server
        import threading
        png = b"\x89PNG\r\n\x1a\n" + b"pixels" * 50
        page = b"""<!doctype html><html><head><meta charset="utf-8"><title>Plain title</title>
            <meta property="og:title" content="How to bake bread &amp; more"><meta property="og:site_name" content="Bread Weekly">
            <meta property="og:image" content="/img/banner.png"><link rel="icon" href="icon.png" sizes="32x32"></head><body></body></html>"""

        class Site(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body, ctype = (png, "image/png") if self.path.endswith(".png") else \
                    (MARK * 3000, "application/octet-stream") if self.path.startswith("/files/") else (page, "text/html; charset=utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        self.addCleanup(site.shutdown)
        base = f"http://127.0.0.1:{site.server_address[1]}"

        def ask(message):
            got = self.page._json("/api/vault/sync/ask", {"device": DEVICE, "wait": 30, "sealed": self.seal(message)})
            return self.open(got["sealed"])

        def fetched(fid):
            st = self.open(self.shown()["status"]) or {}
            return next((f for f in st.get("fetches") or [] if f["id"] == fid and f["state"] != "running"), None)
        # an address on the device's own network is left alone, as the host leaves its own alone
        self.assertEqual(ask({"do": "preview", "url": base + "/recipes/secret-bread"})["title"], "")
        self.page._json("/api/vault/sync/tell", {"device": DEVICE, "sealed": self.seal({"do": "fetch", "id": 1, "url": base + "/files/a.bin", "dest": "/"})})
        self.assertIn("home network", self.until(lambda: fetched(1), "the refusal")["error"])
        self.agent.fetch_private = True
        p = ask({"do": "preview", "url": base + "/recipes/secret-bread"})
        self.assertEqual((p["title"], p["site"]), ("How to bake bread & more", "Bread Weekly"))
        self.assertEqual((base64.b64decode(p["image"]["data"]), p["image"]["ext"], p["icon"]["ext"]), (png, "png", "png"))
        self.assertIn("error", ask({"do": "preview", "url": "not a link"}))
        for n in (2, 3):                                                    # the same address twice: the second gets a name of its own
            self.page._json("/api/vault/sync/tell", {"device": DEVICE, "sealed": self.seal(
                {"do": "fetch", "id": n, "url": base + "/files/secret%20report.bin", "dest": "/Saved/From the web"})})
            done = self.until(lambda: fetched(n), "the download")
            self.assertEqual((done["state"], done["error"], done["bytes"]), ("done", "", len(MARK) * 3000))
        v = self.new_device()
        self.assertEqual(v.read("/Saved/From the web/secret report.bin"), MARK * 3000)
        self.assertEqual(v.read("/Saved/From the web/secret report (2).bin"), MARK * 3000)
        held = json.dumps(self.web.sealed._devs[(next(iter(self.web.accounts._recs)), DEVICE)], default=str)
        for needle in ("secret", "bread", "Saved", "127.0.0.1"):
            self.assertNotIn(needle, held)
        for folder, _, files in os.walk(os.path.join(self.tmp, "aaaa")):
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    data = fh.read()
                for needle in (MARK, b"secret report", b"From the web"):
                    self.assertNotIn(needle, data, os.path.join(folder, f))

    def test_messages_are_kept_by_the_device_sealed(self):
        """Discord messages for an end-to-end account: the account's computer reads Discord (a made-up one
        here) and seals what it keeps; the page asks it through the host, which learns nothing."""
        import test_chat
        fake = test_chat.FakeAccount
        fake.history, fake.downloads, fake.calls, fake.gone, fake.refreshed = test_chat.history(), 0, 0, False, []
        svc = self.agent.chat_service()
        svc.chat.make_api, svc.chat.make_gateway = fake, test_chat.FakeGateway

        def ask(method, route, query=None, body=None):
            got = self.page._json("/api/vault/sync/ask", {"device": DEVICE, "wait": 30, "sealed": self.seal(
                {"do": "chat", "method": method, "route": route, "query": query or {}, "body": body})})
            return self.open(got["sealed"])
        self.assertTrue(self.open(self.shown()["info"])["chat"])       # the page knows this device can do it
        state = ask("GET", "chat")["json"]
        self.assertEqual((state["connected"], state["device"]), (False, "my laptop"))
        self.assertEqual(ask("POST", "chat/connect", body={"token": "wrong"})["status"], 400)
        self.assertEqual(ask("POST", "chat/connect", body={"token": "good-token"})["status"], 200)
        self.assertEqual(ask("GET", "chat")["json"]["user"]["username"], "dennis")
        self.assertEqual([d["name"] for d in ask("GET", "chat/directory")["json"]["dms"]], ["Anna"])
        token_file = os.path.join(self.tmp, "lite", "messages-" + DEVICE, "discord-token")
        with open(token_file) as f:
            self.assertEqual(f.read(), "good-token")                   # the token stays on the device
        # a conversation is archived, with its attachment
        self.assertEqual(ask("POST", "chat/archive/start", body={"targets": [{"kind": "dm", "id": "500"}], "downloadAttachments": True})["status"], 200)

        def archived():
            a = ask("GET", "chat")["json"]["archive"]
            return a if a and not a["running"] else None
        done = self.until(archived, "the archive")
        self.assertEqual((done["status"], done["stats"]["saved"], done["stats"]["files"]), ("done", 3, 1), done)
        found = ask("GET", "chat/messages", {"channel": "500"})["json"]
        self.assertEqual([m["content"] for m in found["messages"]], ["see you 100% soon", "hello Anna", "hi there"])
        self.assertEqual([m["content"] for m in ask("GET", "chat/messages", {"q": "100%"})["json"]["messages"]], ["see you 100% soon"])
        self.assertEqual([(x["name"], x["count"]) for x in ask("GET", "chat/places")["json"]["items"]], [("Anna", 3)])
        out = ask("GET", "chat/export", {"format": "txt", "channel": "500"})
        self.assertIn(b"hello Anna", base64.b64decode(out["data"]))
        self.assertIn("text/plain", out["type"])
        self.assertTrue(ask("GET", "chat/open", {"id": "2", "a": "920"})["redirect"].startswith("https://cdn.example/notes.txt"))
        fake.gone = True                                               # Discord no longer has it: the sealed copy in the account
        self.assertEqual(ask("GET", "chat/open", {"id": "2", "a": "920"})["redirect"],
                         "/api/file?path=/Discord/Direct%20messages/Anna%20%5B500%5D/files/920-notes.txt")
        # a small copy of a picture that a version before 2.19.0 kept is removed; the attachment stays
        old = self.new_device()
        old.mkdir("/Discord/Direct messages/Anna [500]/files/previews")
        old.put("/Discord/Direct messages/Anna [500]/files/previews/920.webp", b"small")
        self.assertEqual(ask("POST", "chat/previews/clear", body={})["json"], {"ok": True, "files": 1, "bytes": 5})
        self.assertEqual(ask("POST", "chat/previews/clear", body={})["json"]["files"], 0)
        self.assertIsNone(old.sync().resolve("/Discord/Direct messages/Anna [500]/files/previews/920.webp"))
        # the logger records what arrives, with what an edited message said before
        self.assertEqual(ask("POST", "chat/logger", body={"on": True})["status"], 200)
        gw = test_chat.FakeGateway.last
        gw.emit("MESSAGE_CREATE", test_chat.msg(21, 500, test_chat.ANNA, "pizza tonight?", 5))
        gw.emit("MESSAGE_UPDATE", {"id": "21", "channel_id": "500", "content": "pasta tonight?", "edited_timestamp": "2024-03-05T11:00:00+00:00"})
        self.assertGreaterEqual(ask("POST", "chat/flush", body={})["json"]["written"], 2)
        # another device of the account finds it all in /Discord, as ordinary sealed files
        v = self.new_device()
        lines = [json.loads(x) for x in v.read("/Discord/Direct messages/Anna [500]/2024-03-01 to 2024-03-03.jsonl").decode().splitlines()]
        self.assertEqual([r["content"] for r in lines], ["hi there", "hello Anna", "see you 100% soon"])
        self.assertEqual(v.read("/Discord/Direct messages/Anna [500]/files/920-notes.txt"), b"hello")
        live = [n for n in v.files() if n["name"].endswith("my laptop.jsonl")]
        self.assertEqual(len(live), 1)
        # and the host has none of it: not a message, not a name, not the token
        held = json.dumps(self.web.sealed._devs[(next(iter(self.web.accounts._recs)), DEVICE)], default=str)
        for folder, _, files in os.walk(os.path.join(self.tmp, "aaaa")):
            for f in files:
                with open(os.path.join(folder, f), "rb") as fh:
                    held += fh.read().decode("latin-1")
        for needle in ("good-token", "hello Anna", "pizza", "Anna", "Direct messages", "dennis", "notes.txt"):
            self.assertNotIn(needle, held)
        # logging out forgets the token
        self.assertEqual(ask("POST", "chat/disconnect", body={})["status"], 200)
        self.assertFalse(os.path.exists(token_file))
        self.assertEqual(ask("GET", "chat/nothing")["status"], 404)
        self.assertEqual(ask("GET", "status")["status"], 404)          # only Messages is answered this way

    def test_the_commands(self):
        os.environ["DDRIVE_LITE_CONFIG"] = os.path.join(self.tmp, "lite.json")
        self.addCleanup(os.environ.pop, "DDRIVE_LITE_CONFIG")

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = lite.main(list(argv))
            return code, out.getvalue()
        phrase = words.key_to_phrase(self.key)
        code, text = run("connect", self.address, "-p", phrase)
        self.assertEqual(code, 0, text)
        self.assertIn("end-to-end encrypted", text)
        self.assertTrue(lite.load_settings()["e2e"])
        src = self.put("notes.txt", b"typed on the laptop")
        self.assertEqual(run("put", src, "/")[0], 0)
        self.assertIn("notes.txt", run("ls")[1])
        back = os.path.join(self.tmp, "back.txt")
        self.assertEqual(run("get", "/notes.txt", back)[0], 0)
        with open(back, "rb") as f:
            self.assertEqual(f.read(), b"typed on the laptop")
        self.assertEqual(run("get", "/nothing.txt")[0], 1)
        # moving in: a whole folder (the drive letter of a DDrive, say) is copied into the account, and again only what is new
        self.put("Photos/2019/beach.jpg", MARK + b" a photo", age=5_000_000)
        self.put("Contacts/Contacts.vcf", b"BEGIN:VCARD\r\nFN:Ada\r\nEND:VCARD\r\n")
        code, text = run("import", self.here, "/")
        self.assertEqual(code, 0, text)
        self.assertIn("2 file(s) copied", text)                        # (notes.txt is there already: not copied twice)
        v = self.new_device()
        self.assertEqual(v.read("/Photos/2019/beach.jpg"), MARK + b" a photo")
        self.assertAlmostEqual(v.resolve("/Photos/2019/beach.jpg")["mtime"], time.time() - 5_000_000, delta=5)
        self.assertEqual(v.read("/Contacts/Contacts.vcf"), b"BEGIN:VCARD\r\nFN:Ada\r\nEND:VCARD\r\n")
        self.put("Photos/2020/new.jpg", b"one more")
        code, text = run("import", self.here, "/")
        self.assertIn("1 file(s) copied", text)
        self.assertTrue(os.path.exists(os.path.join(self.here, "Photos", "2019", "beach.jpg")))     # nothing is deleted here
        self.assertEqual(run("import", os.path.join(self.tmp, "nowhere"))[0], 1)
        self.assertEqual(run("ls", "/no such folder")[0], 1)
        self.assertIn("end-to-end", run("status")[1])
        # the owner's words still connect the earlier way
        owner = words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key))
        code, text = run("connect", self.address, "-p", owner)
        self.assertEqual((code, lite.load_settings()["e2e"]), (0, False), text)


if __name__ == "__main__":
    helpers.unittest.main()
