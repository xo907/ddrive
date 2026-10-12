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
        for name in ("/vaultcore.js", "/e2e.js", "/vaultapp.js", "/vault.css"):
            self.assertEqual(get(name)[0], 200, name)
        self.device.client.status()
        mine = get("/", self.device.client.cookie)[1]                  # an end-to-end account gets its own page, not the dashboard
        self.assertIn(b"vaultapp.js", mine)
        self.assertNotIn(b"app.js\"", mine.replace(b"vaultapp.js", b""))

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
        v.snapshot()                                                    # the one before is dropped
        d = self.account_drive()
        try:
            objects = [c["name"] for sub in d.index.children(d.index.resolve("/o")["id"]) for c in d.index.children(sub["id"])]
        finally:
            self.web.accounts.release(d)
        self.assertEqual(len(objects), 6 + 1)                           # six files and one sealed list


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
        self.assertEqual(run("ls", "/no such folder")[0], 1)
        self.assertEqual(run("mount")[0], 1)                           # no drive letter for these yet, said in words
        self.assertIn("end-to-end", run("status")[1])
        # the owner's words still connect the earlier way
        owner = words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key))
        code, text = run("connect", self.address, "-p", owner)
        self.assertEqual((code, lite.load_settings()["e2e"]), (0, False), text)


if __name__ == "__main__":
    helpers.unittest.main()
