"""Accounts: other people on the dashboard, each a 24-word phrase with storage of its own, and what
they are kept away from (the owner's files and everything that manages the drive)."""

import http.client
import json
import os
import re
import shutil
import unittest
import urllib.parse

import helpers
from discorddrive import accounts, codec, lite, words
from discorddrive.crypto import AuthenticationError


class AccountsTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.acc = self.web.accounts
        self.owner_phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        self.owner = self.sign_in(self.owner_phrase)

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    # ------------------------------------------------------------ helpers
    def req(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        if method == "POST":
            h["X-DD"] = "1"
            if isinstance(body, str):
                body, h["Content-Type"] = body.encode(), "application/x-www-form-urlencoded"
            elif isinstance(body, (dict, list)):
                body, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def get(self, path, cookie):
        status, _, data = self.req("GET", path, cookie=cookie)
        return status, (json.loads(data) if data[:1] in (b"{", b"[") else data)

    def post(self, path, body, cookie):
        status, _, data = self.req("POST", path, body, cookie=cookie)
        return status, (json.loads(data) if data[:1] in (b"{", b"[") else data)

    def sign_in(self, phrase):
        status, headers, _ = self.req("POST", "/login", urllib.parse.urlencode({"phrase": phrase}))
        return headers["Set-Cookie"].split(";")[0] if status == 303 else None

    def sign_up(self, invite=""):
        """Go through the two pages of making an account. Returns (cookie, phrase) or (None, the page)."""
        status, _, page = self.req("POST", "/signup", urllib.parse.urlencode({"step": "new", "invite": invite}))
        if status != 200:
            return None, page
        field = lambda name: re.search(rb'name="%s" value="([^"]*)"' % name.encode(), page).group(1).decode()
        phrase = field("phrase")
        self.assertEqual(" ".join(re.findall(r"<li>(\w+)</li>", page.decode())), phrase)        # the words are on the page
        form = {"step": "confirm", "phrase": phrase, "ticket": field("ticket"), "invite": field("invite"), "saved": "1"}
        status, headers, page = self.req("POST", "/signup", urllib.parse.urlencode(form))
        if status != 303:
            return None, page
        return headers["Set-Cookie"].split(";")[0], phrase

    def put(self, cookie, path, data):
        return self.req("POST", "/api/upload?" + urllib.parse.urlencode({"path": path}), data, cookie=cookie)[0]

    def names(self, cookie, path="/"):
        return sorted(i["name"] for i in self.get("/api/list?" + urllib.parse.urlencode({"path": path}), cookie)[1]["items"])

    def opened(self, cookie):
        """The drive of the account a cookie belongs to (open, as it is right after a request)."""
        return self.acc._open[self.get("/api/status", cookie)[1]["account"]]

    # ------------------------------------------------------------ who may make one
    def test_closed_unless_the_owner_opens_it(self):
        self.assertEqual(self.acc.mode(), "closed")
        status, _, page = self.req("GET", "/signup")
        self.assertEqual(status, 403)
        self.assertIn(b"No new accounts", page)
        self.assertEqual(self.req("POST", "/signup", "step=new")[0], 403)
        self.assertNotIn(b"/signup", self.req("GET", "/")[2])
        # the owner opens it: the sign-in page now offers it
        self.assertEqual(self.post("/api/settings", {"signup_mode": "anything"}, self.owner)[0], 400)
        self.assertEqual(self.post("/api/settings", {"signup_mode": "public"}, self.owner)[0], 200)
        self.assertIn(b'href="/signup"', self.req("GET", "/")[2])
        self.assertIn(b"Get my 24 words", self.req("GET", "/signup")[2])

    def test_an_account_is_a_phrase_this_dashboard_made(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        self.assertTrue(cookie)
        self.assertEqual(len(phrase.split()), 24)
        st = self.get("/api/status", cookie)[1]
        self.assertEqual((st["role"], st["user"], st["encrypted"], st["stats"]["files"]), ("account", "", True, 0))
        self.assertEqual(st["account"], accounts.account_id(words.phrase_to_key(phrase)))
        self.assertNotIn("resources", st)                              # nothing about the computer
        self.assertNotIn("devices", st)
        # signing out and in again: the words are all there is
        self.assertEqual(self.post("/api/logout", {}, cookie)[0], 200)
        again = self.sign_in(phrase)
        self.assertEqual(self.get("/api/status", again)[1]["account"], st["account"])
        self.assertIsNone(self.sign_in(words.key_to_phrase(os.urandom(32))))
        # words somebody chose themselves are not an account: the ticket belongs to the offered phrase
        _, _, page = self.req("POST", "/signup", "step=new")
        ticket = re.search(rb'name="ticket" value="([^"]*)"', page).group(1).decode()
        mine = words.key_to_phrase(bytes(32))
        form = {"step": "confirm", "phrase": mine, "ticket": ticket, "saved": "1"}
        self.assertEqual(self.req("POST", "/signup", urllib.parse.urlencode(form))[0], 400)
        self.assertIsNone(self.sign_in(mine))
        # the list on disk holds no words (and in a real drive the keys are encrypted for the computer)
        with open(self.acc.path, encoding="utf-8") as f:
            self.assertNotIn(phrase.split()[3] + " " + phrase.split()[4], f.read())

    def test_invites(self):
        self.d.cfg.signup_mode = "invite"
        self.assertIn(b"Invite code", self.req("GET", "/signup")[2])
        self.assertIn(b"invite code is needed", self.sign_up()[1])
        self.assertIn(b"isn&#39;t valid", self.sign_up("made-up")[1])
        self.assertEqual(self.post("/api/accounts/invite", {"uses": 1, "days": 7}, None)[0], 401)       # only the owner makes them
        status, r = self.post("/api/accounts/invite", {"uses": 1, "days": 7, "note": "for Anna"}, self.owner)
        self.assertEqual(status, 200)
        code = r["code"]
        info = self.get("/api/accounts", self.owner)[1]
        self.assertEqual((info["mode"], [(i["code"], i["note"], i["used"], i["valid"]) for i in info["invites"]]),
                         ("invite", [(code, "for Anna", 0, True)]))
        self.assertIn(code.encode(), self.req("GET", "/signup?invite=" + code)[2])      # the link fills the code in
        cookie, phrase = self.sign_up(code)
        self.assertTrue(cookie)
        self.assertIn(b"isn&#39;t valid", self.sign_up(code)[1])                         # used up
        info = self.get("/api/accounts", self.owner)[1]
        self.assertEqual([(i["used"], i["valid"]) for i in info["invites"]], [(1, False)])
        self.assertEqual([a["invite"] for a in info["accounts"]], [code])
        # one that is taken back, and one that has run out
        other = self.post("/api/accounts/invite", {"uses": 0, "days": 0}, self.owner)[1]["code"]
        self.assertTrue(self.sign_up(other)[0])
        self.assertEqual(self.post("/api/accounts/invite/revoke", {"code": other}, self.owner)[0], 200)
        self.assertIsNone(self.sign_up(other)[0])
        old = self.acc.invite_new(1, 1)
        self.acc._invites[old]["expires"] = 1.0
        self.assertIsNone(self.sign_up(old)[0])
        self.assertEqual(self.get("/api/status", cookie)[0], 200)                        # the account it made stays

    def test_limits(self):
        self.d.cfg.signup_mode = "public"
        self.d.cfg.accounts_max = 2
        self.assertTrue(self.sign_up()[0])
        self.assertTrue(self.sign_up()[0])
        self.assertIn(b"it is full", self.sign_up()[1])
        self.d.cfg.accounts_max = 0
        self.assertTrue(self.sign_up()[0])
        self.assertIn(b"Try again in an hour", self.sign_up()[1])                        # four from one address
        # behind a reverse proxy on this computer every visitor counts for themselves
        via = lambda ip, body: self.req("POST", "/signup", body, headers={"X-Forwarded-For": ip})
        self.assertEqual(via("203.0.113.9", "step=new")[0], 200)
        self.assertEqual(via("203.0.113.9, 127.0.0.1", "step=new")[0], 400)              # the proxy saw this computer's address
        # ...and a request a proxy passed on is not "on the computer itself", where the owner's words are shown
        self.assertEqual(self.req("POST", "/api/signin/phrase", {}, cookie=self.owner, headers={"Host": f"127.0.0.1:{self.web.port}"})[0], 200)
        self.assertEqual(self.req("POST", "/api/signin/phrase", {}, cookie=self.owner,
                                  headers={"Host": f"127.0.0.1:{self.web.port}", "X-Forwarded-For": "203.0.113.9"})[0], 403)

    # ------------------------------------------------------------ what an account has, and hasn't
    def test_storage_of_its_own(self):
        self.d.cfg.signup_mode = "public"
        self.write(self.d, "/Owner/private.txt", b"the owner's own file")
        self.upload(self.d)
        anna, _ = self.sign_up()
        ben, _ = self.sign_up()
        self.assertEqual(self.names(anna), [])                                           # nothing of the owner's
        self.assertEqual(self.get("/api/list?path=/Owner", anna)[0], 404)
        self.assertEqual(self.get("/api/file?path=/Owner/private.txt", anna)[0], 404)
        self.assertEqual(self.post("/api/mkdir", {"path": "/Photos"}, anna)[0], 200)
        self.assertEqual(self.put(anna, "/Photos/anna.txt", b"anna's holiday"), 200)
        self.assertEqual(self.put(ben, "/ben.txt", b"ben's notes"), 200)
        self.assertEqual((self.names(anna), self.names(anna, "/Photos"), self.names(ben), self.names(self.owner)),
                         (["Photos"], ["anna.txt"], ["ben.txt"], ["Owner"]))
        self.assertEqual(self.get("/api/file?path=/Photos/anna.txt", anna)[1], b"anna's holiday")
        self.assertEqual(self.get("/api/file?path=/Photos/anna.txt", ben)[0], 404)
        self.assertEqual(self.get("/api/file?path=/Photos/anna.txt", self.owner)[0], 404)
        self.assertEqual([i["name"] for i in self.get("/api/search?q=txt", ben)[1]["items"]], ["ben.txt"])
        # what the page works out and keeps for a moment is per account too (folder sizes, the address book)
        self.assertEqual([f["path"] for f in self.get("/api/insights", self.owner)[1]["folders"]], ["/Owner"])
        self.assertEqual(self.get("/api/insights", anna)[1]["folders"], [])              # from before /Photos: her own, a moment old
        self.opened(anna).space._tree_cache = None
        self.assertEqual([f["path"] for f in self.get("/api/insights", anna)[1]["folders"]], ["/Photos"])
        self.assertEqual(self.get("/api/insights", ben)[1]["folders"], [])
        person = {"contact": {"name": "Owner's friend", "emails": [{"value": "friend@example.com"}]}}
        self.assertEqual(self.post("/api/contacts/save", person, self.owner)[0], 200)
        self.assertEqual(len(self.get("/api/contacts", self.owner)[1]["items"]), 1)
        self.assertEqual(self.get("/api/contacts", anna)[1]["items"], [])
        self.assertEqual(self.post("/api/contacts/save", {"contact": {"name": "Anna's friend"}}, anna)[0], 200)
        self.assertEqual([c["name"] for c in self.get("/api/contacts", anna)[1]["items"]], ["Anna's friend"])
        self.assertEqual([c["name"] for c in self.get("/api/contacts", self.owner)[1]["items"]], ["Owner's friend"])
        home = self.get("/api/home", anna)[1]
        self.assertEqual((home["host"], home["mount"], home["stats"]["files"]), ("", "", 2))
        # earlier versions, deleted files and the rest work as they do for the owner
        self.assertEqual(self.post("/api/delete", {"path": "/Photos/anna.txt"}, anna)[0], 200)
        self.assertEqual(self.names(anna, "/Photos"), [])

    def test_encrypted_with_its_own_words(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        secret = b"only these 24 words open this " * 200
        self.assertEqual(self.put(cookie, "/diary.txt", secret), 200)
        acct = self.opened(cookie)
        self.upload(acct)
        mids = [c["message_id"] for c in acct.index.get_chunks(acct.index.resolve("/diary.txt")["id"])]
        self.assertTrue(mids)
        for mid in mids:                                               # what is in the channel
            raw, _ = self.d.backend.download(mid, None)
            self.assertNotIn(b"only these", raw)
            with self.assertRaises(AuthenticationError):
                codec.decode(raw, self.d.crypto)                       # the owner's key doesn't open it
            self.assertIn(b"only these", codec.decode(raw, acct.crypto))     # the account's does
        self.assertEqual(acct.crypto.key, words.phrase_to_key(phrase))
        self.assertEqual(self.read(acct, "/diary.txt"), secret)        # read back from the channel
        # the drive's own journal and checkpoints are not disturbed by an account's
        self.write(self.d, "/mine.txt", b"x")
        self.upload(self.d)
        other = self.drive("bbbb")
        self.assertEqual(sorted(c["name"] for c in other.index.children(1)), ["mine.txt"])
        self.assertIsNone(self.d.index.resolve("/diary.txt"))

    def test_nothing_that_manages_the_drive(self):
        self.d.cfg.signup_mode = "public"
        cookie, _ = self.sign_up()
        for path in ("/api/settings", "/api/log", "/api/activity", "/api/sync", "/api/sync/browse?path=/", "/api/signin",
                     "/api/accounts", "/api/check", "/api/nonsense"):
            self.assertEqual(self.get(path, cookie)[0], 403, path)
        for path, body in (("/api/settings", {"signup_mode": "public"}), ("/api/settings", {"web_lan": True}),
                           ("/api/log/clear", {}), ("/api/resources/clean", {"what": "cache"}), ("/api/repair", {"all": True}),
                           ("/api/pin", {"path": "/", "on": True}), ("/api/check/start", {"path": "/"}),
                           ("/api/sync/save", {"job": {}}), ("/api/sync/remote-edit", {"on": True}),
                           ("/api/signin/phrase", {}), ("/api/signin/passkey/options", {}), ("/api/webdav/password", {"password": "12345678"}),
                           ("/api/accounts/invite", {"uses": 0}), ("/api/accounts/delete", {"id": "x"}),
                           ("/api/accounts/disable", {"id": "x", "off": True})):
            self.assertEqual(self.post(path, body, cookie)[0], 403, path)
        self.assertEqual(self.d.cfg.signup_mode, "public")
        self.assertFalse(self.d.cfg.web_lan)
        self.d.cfg.webdav_enabled = True                               # other apps: the owner's, with the owner's password
        self.assertEqual(self.req("PROPFIND", "/dav/", cookie=cookie)[0], 401)
        self.assertEqual(self.get("/api/status", self.owner)[1]["accounts"], 1)

    def test_an_allowance(self):
        self.d.cfg.signup_mode = "public"
        self.d.cfg.account_quota_bytes = 100 * 1024
        cookie, _ = self.sign_up()
        self.assertEqual(self.put(cookie, "/small.bin", os.urandom(40 * 1024)), 200)
        st = self.get("/api/status", cookie)[1]
        self.assertEqual(st["quota"]["bytes"], 100 * 1024)
        acct = self.opened(cookie)
        acct.index._stats_cache = (0.0, None)
        self.assertEqual(self.put(cookie, "/big.bin", os.urandom(80 * 1024)), 507)       # over what an account may store
        self.assertEqual(self.put(self.owner, "/big.bin", os.urandom(300 * 1024)), 200)  # the owner has no such limit
        info = self.get("/api/account", cookie)[1]
        self.assertEqual((info["id"], info["quota"]), (acct.id, 100 * 1024))

    def test_share_links_of_an_account(self):
        self.d.cfg.signup_mode = "public"
        cookie, _ = self.sign_up()
        self.put(cookie, "/hello.txt", b"hello from an account")
        status, link = self.post("/api/share", {"path": "/hello.txt", "hours": 1}, cookie)
        self.assertEqual(status, 200)
        acct = self.opened(cookie)
        self.assertTrue(link["path"].startswith(f"/s/{acct.id}."))
        self.assertEqual(self.req("GET", link["path"] + "/raw")[2], b"hello from an account")       # no sign-in needed
        self.assertEqual([l["id"] for l in self.get("/api/shares", cookie)[1]["items"]], [link["id"]])
        self.assertEqual(self.get("/api/shares", self.owner)[1]["items"], [])
        self.upload(acct)
        self.assertTrue(self.acc._close(acct.id, force=True))                            # nobody signed in, its drive closed
        self.assertEqual(self.acc._open, {})
        self.assertEqual(self.req("GET", link["path"] + "/raw")[2], b"hello from an account")
        self.assertEqual(self.req("GET", "/s/0000000000000000." + link["id"].partition(".")[2])[0], 404)
        self.assertEqual(self.req("GET", "/s/" + link["id"].partition(".")[2])[0], 404)   # not one of the owner's links
        self.assertEqual(self.post("/api/shares/revoke", {"id": link["id"]}, cookie)[0], 200)
        self.assertEqual(self.req("GET", link["path"] + "/raw")[0], 404)

    # ------------------------------------------------------------ the owner's side
    def test_the_owner_switches_off_and_deletes(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        self.put(cookie, "/a.txt", b"a" * 5000)
        acct = self.opened(cookie)
        self.upload(acct)
        mids = set(acct.index.referenced_mids())
        folder = acct.dir
        link = self.post("/api/share", {"path": "/a.txt"}, cookie)[1]
        self.acc.release(self.acc.acquire(acct.id))
        self.acc._recs[acct.id]["usage_at"] = 0
        self.acc.release(self.acc.acquire(acct.id))
        listed = self.get("/api/accounts", self.owner)[1]["accounts"]
        self.assertEqual([(a["id"], a["files"], a["bytes"], a["disabled"]) for a in listed], [(acct.id, 1, 5000, False)])
        # switched off: signed out everywhere, links dead, the data stays
        self.assertEqual(self.post("/api/accounts/disable", {"id": acct.id, "off": True}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", cookie)[0], 401)
        self.assertIsNone(self.sign_in(phrase))
        self.assertEqual(self.req("GET", link["path"] + "/raw")[0], 404)
        self.assertEqual(self.post("/api/accounts/disable", {"id": acct.id, "off": False}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/file?path=/a.txt", cookie)[1], b"a" * 5000)
        # deleted: gone here, and its pieces are handed to the drive's clean-up
        self.assertEqual(self.post("/api/accounts/delete", {"id": acct.id}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", cookie)[0], 401)
        self.assertIsNone(self.sign_in(phrase))
        self.assertFalse(os.path.exists(folder))
        waiting = {r["message_id"] for r in self.d.index._all("SELECT message_id FROM trash")}
        self.assertTrue(mids and mids <= waiting)
        self.assertEqual(self.get("/api/accounts", self.owner)[1]["accounts"], [])
        self.assertEqual(self.post("/api/accounts/delete", {"id": acct.id}, self.owner)[0], 404)

    def test_an_account_deletes_itself(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        self.put(cookie, "/a.txt", b"a")
        self.assertEqual(self.post("/api/account/delete", {"confirm": "no"}, cookie)[0], 400)
        self.assertEqual(self.post("/api/account/delete", {"confirm": "delete"}, self.owner)[0], 400)   # the owner isn't an account
        self.assertEqual(self.post("/api/account/delete", {"confirm": "delete"}, cookie)[0], 200)
        self.assertEqual(self.get("/api/status", cookie)[0], 401)
        self.assertIsNone(self.sign_in(phrase))
        self.assertEqual(self.acc.count(), 0)

    def test_the_file_list_comes_back_from_the_channel(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        data = os.urandom(150 * 1024)
        self.put(cookie, "/Docs.bin", data)
        acct = self.opened(cookie)
        self.upload(acct)
        folder = acct.dir
        self.acc._close(acct.id, force=True)                           # saves a checkpoint, as closing always does
        shutil.rmtree(folder)                                          # the account's folder on this computer is lost
        again = self.sign_in(phrase)
        self.assertEqual(self.names(again), ["Docs.bin"])
        self.assertEqual(self.get("/api/file?path=/Docs.bin", again)[1], data)
        # ...and so are the keys, on a computer that can't read them: the phrase is the key
        self.acc.tidy(idle=0)
        self.acc._secrets.clear()
        self.assertEqual(self.get("/api/status", again)[0], 503)
        again = self.sign_in(phrase)
        self.assertEqual(self.get("/api/file?path=/Docs.bin", again)[1], data)

    def test_accounts_survive_a_restart(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        self.put(cookie, "/kept.txt", b"still here")
        self.upload(self.opened(cookie))
        self.web.stop()
        self.web = self.d.start_web(port=0)
        self.assertEqual(self.web.accounts.count(), 1)
        self.assertEqual(self.get("/api/file?path=/kept.txt", cookie)[1], b"still here")     # the session too
        self.assertTrue(self.sign_in(phrase))

    def test_lite_with_an_account(self):
        self.d.cfg.signup_mode = "public"
        cookie, phrase = self.sign_up()
        client = lite.HostClient(f"http://127.0.0.1:{self.web.port}", phrase)
        self.assertEqual(client.status()["role"], "account")
        fs = lite.LiteFS(client, tmp_dir=os.path.join(self.tmp, "lite-tmp"), block=64 * 1024, cache_bytes=256 * 1024)
        fs.mkdir("/FromLite", 0o755)
        fh = fs.create("/FromLite/x.txt", 0o644)
        fs.write("/FromLite/x.txt", b"through the lite client", 0, fh)
        fs.flush("/FromLite/x.txt", fh)
        fs.release("/FromLite/x.txt", fh)
        self.assertEqual(self.get("/api/file?path=/FromLite/x.txt", cookie)[1], b"through the lite client")
        self.assertEqual(self.names(self.owner), [])


if __name__ == "__main__":
    unittest.main()
