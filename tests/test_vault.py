"""End-to-end encrypted accounts: the device seals everything, the host stores and returns sealed
things and can read none of it. Here the host is a whole drive with its dashboard on a free local
port, and the device is the reference client (ddrive/vault.py) talking to it over HTTP."""

import http.client
import io
import json
import os
import urllib.parse

import helpers
from ddrive import lite, vault, words

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


if __name__ == "__main__":
    helpers.unittest.main()
