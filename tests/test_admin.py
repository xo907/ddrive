"""Admin accounts (end-to-end encrypted accounts that may also manage the drive), accounts of the
earlier kind now that they are retired, and the `accounts` command."""

import argparse
import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import time
import urllib.parse
from unittest import mock

import helpers
import test_accounts
from ddrive import accounts, accountscli, lite, vault, words
from ddrive import web as webmod
from ddrive.web import session_cookie

_A = test_accounts.AccountsTest.__dict__
ADMIN = {"X-DD-Admin": "1"}        # what an admin's page sends with what it asks of the drive itself


class AdminTest(helpers.DriveTest):
    # the dashboard and its helpers as the accounts' tests have them (without running those tests again)
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.acc = self.web.accounts
        self.address = f"http://127.0.0.1:{self.web.port}"
        self.owner = self.sign_in(words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key)))

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    req, get, post, sign_in, put, names = (_A[k] for k in ("req", "get", "post", "sign_in", "put", "names"))

    def ask(self, method, path, cookie, body=None):
        """What an admin's page asks of the drive itself."""
        status, _, data = self.req(method, path, body, cookie=cookie, headers=ADMIN)
        return status, (json.loads(data) if data[:1] in (b"{", b"[") else data)

    def sign_in_key(self, key):
        """Sign in as a browser does: with the sign-in key made from the 24 words, which stay behind."""
        status, headers, _ = self.req("POST", "/login", urllib.parse.urlencode({"auth": vault.auth_key(key).hex()}))
        return headers["Set-Cookie"].split(";")[0] if status == 303 else None

    def make_admin(self, note="Sam", by=None, headers=None):
        key = os.urandom(32)                                # stands for 24 words made in a browser
        status, _, data = self.req("POST", "/api/accounts/admin", {"auth": vault.auth_key(key).hex(), "note": note},
                                   cookie=by or self.owner, headers=headers)
        r = json.loads(data)
        self.assertEqual(status, 200, r)
        self.assertNotIn("phrase", r)                       # no words came from here
        return r["id"], key

    def earlier_account(self):
        """An account of the earlier kind with a file in it, as one made before 2.18.0 is found."""
        self.d.cfg.signup_mode = "public"
        with mock.patch("ddrive.web.RETIRE_EARLIER_ACCOUNTS", False):
            phrase, ticket = self.acc.offer("", "127.0.0.1")
            rec = self.acc.create(phrase, ticket, "", "127.0.0.1")
            cookie = self.sign_in(phrase)
            self.assertEqual(self.put(cookie, "/old.txt", b"from before"), 200)
        self.d.cfg.signup_mode = "closed"
        return rec["id"], phrase, cookie

    # ------------------------------------------------------------ admin accounts
    def test_an_admin_is_an_end_to_end_encrypted_account(self):
        aid, key = self.make_admin()
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual((listed[aid]["admin"], listed[aid]["e2e"], listed[aid]["note"]), (True, True, "Sam"))
        # this computer has the sign-in key made from the words, and nothing of the words themselves
        self.assertEqual(self.acc._secrets[aid]["key"], vault.auth_key(key).hex())
        with open(self.acc.path, encoding="utf-8") as f:
            self.assertNotIn(key.hex(), f.read())
        # the words, sent as they are, sign nobody in: only the key made from them does
        self.assertIsNone(self.sign_in(words.key_to_phrase(key)))
        admin = self.sign_in_key(key)
        self.assertIsNotNone(admin)
        # signed in, it is an account like any other end-to-end encrypted one, with storage of its own...
        status, st = self.get("/api/status", admin)
        self.assertEqual((status, st["role"], st["account"], st["e2e"], st["admin"]), (200, "account", aid, True, True))
        v = vault.Vault(lite.HostClient(self.address, auth=vault.auth_key(key).hex()), key).sync()
        v.put("/mine.txt", b"an admin's own file")
        self.assertEqual(v.read("/mine.txt"), b"an admin's own file")
        # ...which the owner doesn't see, and which this computer only has sealed
        self.assertEqual(self.names(self.owner), [])
        self.assertEqual(self.get("/api/list?path=/", admin)[0], 403)
        self.assertEqual(self.get("/api/file?path=/mine.txt", admin)[0], 403)

    def test_an_admin_manages_the_drive(self):
        aid, key = self.make_admin()
        admin = self.sign_in_key(key)
        # as the drive's: its status, settings, accounts and log
        status, st = self.ask("GET", "/api/status", admin)
        self.assertEqual((status, st["role"], st["admin"], st["user"], st["uploads"]), (200, "owner", aid, "", []))
        for path in ("/api/settings", "/api/accounts", "/api/accounts/moves", "/api/log", "/api/changelog"):
            self.assertEqual(self.ask("GET", path, admin)[0], 200, path)
        self.assertEqual(self.ask("POST", "/api/settings", admin, {"signup_mode": "invite"})[0], 200)
        self.assertEqual(self.acc.mode(), "invite")
        # it makes accounts and other admins, and switches them off
        other, other_key = self.make_admin("Kim", by=admin, headers=ADMIN)
        status, r = self.ask("POST", "/api/accounts/create", admin, {"auth": vault.auth_key(os.urandom(32)).hex()})
        self.assertEqual((status, r["e2e"], r["admin"]), (200, True, False))
        self.assertEqual(self.ask("POST", "/api/accounts/disable", admin, {"id": other, "off": True})[0], 200)
        self.assertIsNone(self.sign_in_key(other_key))
        self.assertEqual(self.ask("GET", "/api/signin", admin)[1], {"admin": aid})         # nothing of how the owner signs in
        # without saying it asks as an admin it is its own account, which may ask none of that
        for path in ("/api/settings", "/api/accounts", "/api/log"):
            self.assertEqual(self.get(path, admin)[0], 403, path)
        self.assertEqual(self.post("/api/accounts/admin", {"auth": vault.auth_key(os.urandom(32)).hex()}, admin)[0], 403)

    def test_an_admin_doesnt_open_the_owners_files(self):
        aid, key = self.make_admin()
        admin = self.sign_in_key(key)
        self.assertEqual(self.put(self.owner, "/owner.txt", b"the owner's"), 200)
        for path in ("/api/list?path=/", "/api/file?path=/owner.txt", "/api/search?q=owner", "/api/zip?path=/", "/api/thumb?path=/owner.txt",
                     "/api/home", "/api/recent", "/api/history", "/api/activity", "/api/deleted", "/api/snapshots", "/api/shares",
                     "/api/notes", "/api/contacts", "/api/bookmarks", "/api/versions?path=/owner.txt", "/api/chat", "/api/sync"):
            self.assertEqual(self.ask("GET", path, admin)[0], 403, path)
        for path in ("/api/upload?path=/x.txt", "/api/delete", "/api/move", "/api/copy", "/api/mkdir", "/api/share", "/api/purge",
                     "/api/snapshots/restore", "/api/accounts/e2e", "/api/locks/unlock", "/api/fetch"):
            self.assertEqual(self.ask("POST", path, admin, {})[0], 403, path)
        self.assertEqual(self.names(self.owner), ["owner.txt"])
        # nor change how the owner signs in
        for path in ("/api/signin/passkey/options", "/api/signin/passkey/register", "/api/signin/passkey/remove",
                     "/api/signin/phrase", "/api/webdav/password"):
            self.assertEqual(self.ask("POST", path, admin, {})[0], 403, path)

    def test_only_an_admin_asks_as_one(self):
        # an account that isn't an admin, the owner, and nobody at all
        key = os.urandom(32)
        self.assertEqual(self.post("/api/accounts/create", {"auth": vault.auth_key(key).hex()}, self.owner)[0], 200)
        account = self.sign_in_key(key)
        self.assertFalse(self.get("/api/status", account)[1]["admin"])
        for cookie in (account, self.owner):
            self.assertEqual(self.ask("GET", "/api/settings", cookie)[0], 403)
            self.assertEqual(self.ask("GET", "/api/status", cookie)[0], 403)
            self.assertEqual(self.ask("POST", "/api/accounts/admin", cookie, {"auth": vault.auth_key(os.urandom(32)).hex()})[0], 403)
        self.assertEqual(self.ask("GET", "/api/settings", None)[0], 401)
        # a session of the owner's that names an admin (how admins were signed in before 2.18.0) is no session
        aid, _ = self.make_admin()
        self.assertEqual(self.get("/api/status", "dd_session=" + session_cookie(self.d.cfg.web_token))[0], 200)
        payload = webmod._b64(json.dumps({"a": "", "m": aid, "t": int(time.time()), "n": "x"}).encode())
        secret = hashlib.sha256(b"DiscordDrive-web:" + self.d.cfg.web_token.encode() + b"session").digest()
        old = f"dd_session={payload}.{webmod._b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())}"
        self.assertEqual(self.get("/api/status", old)[0], 401)
        self.assertEqual(self.get("/api/list?path=/", old)[0], 401)

    def test_switching_an_admin_off_signs_it_out(self):
        aid, key = self.make_admin()
        admin = self.sign_in_key(key)
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid, "off": True}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", admin)[0], 401)
        self.assertEqual(self.ask("GET", "/api/settings", admin)[0], 401)
        self.assertIsNone(self.sign_in_key(key))
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid, "off": False}, self.owner)[0], 200)
        self.assertEqual(self.ask("GET", "/api/settings", admin)[0], 200)        # the same session works again
        self.assertEqual(self.post("/api/accounts/delete", {"id": aid}, self.owner)[0], 200)
        self.assertEqual(self.ask("GET", "/api/settings", admin)[0], 401)
        self.assertIsNone(self.sign_in_key(key))

    def test_admins_dont_count_as_accounts(self):
        self.d.cfg.signup_mode = "public"
        self.d.cfg.accounts_max = 1
        self.make_admin()
        self.make_admin()
        sign_up = lambda: self.req("POST", "/signup", urllib.parse.urlencode({"step": "e2e", "auth": vault.auth_key(os.urandom(32)).hex()}))[0]
        self.assertEqual(sign_up(), 303)                    # the one account the limit allows is made
        self.assertEqual(sign_up(), 400)

    def test_admins_come_back_with_the_saved_list(self):
        aid, key = self.make_admin()
        self.assertTrue(self.acc.backup())
        mid = self.acc.backups(max_age=0)[0]["id"]
        with self.acc._lock:                                # this computer's disk was lost
            self.acc._recs.clear()
            self.acc._secrets.clear()
            self.acc._save()
        self.assertEqual(self.acc.restore(mid), 1)
        admin = self.sign_in_key(key)
        self.assertIsNotNone(admin)
        self.assertTrue(self.acc.admin_ok(aid))
        self.assertEqual(self.ask("GET", "/api/settings", admin)[0], 200)

    def old_admin(self):
        """An admin as 2.16.0 to 2.17.2 made them: words made here, no storage, signed in as the owner."""
        key = os.urandom(32)
        aid = accounts.account_id(key)
        with self.acc._lock:
            self.acc._recs[aid] = {"id": aid, "check": accounts._check(key), "created": 1.0, "seen": None, "invite": "",
                                   "admin": True, "note": "old"}
            self.acc._save()
        return aid, key

    def old_sign_in(self, key):
        """An admin from before signs in with its words: (status, the page, the ticket on it or None)."""
        status, headers, page = self.req("POST", "/login", urllib.parse.urlencode({"phrase": words.key_to_phrase(key)}))
        self.assertNotIn("Set-Cookie", headers)             # the old words sign nothing in
        ticket = re.search(rb'name="ticket" value="([^"]*)"', page)
        return status, page, ticket.group(1).decode() if ticket else None

    def test_an_admin_from_before_cant_sign_in(self):
        aid, key = self.old_admin()
        status, page, ticket = self.old_sign_in(key)
        self.assertEqual(status, 200)
        self.assertIn(b"New words for your admin account", page)
        self.assertIn(b"e2e-make", page)                    # the browser makes them (e2e.js)
        self.assertNotIn(b'name="phrase"', page)
        self.assertIsNone(self.sign_in_key(key))
        self.assertFalse(self.acc.admin_ok(aid))
        self.assertIsNone(self.acc.active(aid))
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual((listed[aid]["admin"], listed[aid]["e2e"]), (True, False))
        self.assertEqual(self.post("/api/accounts/delete", {"id": aid}, self.owner)[0], 200)
        self.assertNotIn(aid, self.acc._recs)
        # deleted: its ticket is worth nothing
        form = {"ticket": ticket, "auth": vault.auth_key(os.urandom(32)).hex()}
        self.assertEqual(self.req("POST", "/login/admin", urllib.parse.urlencode(form))[0], 400)
        self.assertEqual(self.acc.count(), 0)

    def test_an_admin_from_before_gets_new_words(self):
        aid, key = self.old_admin()
        _, _, ticket = self.old_sign_in(key)
        new = os.urandom(32)                                # the 24 new words its browser makes
        auth = vault.auth_key(new).hex()
        # not without the ticket its old words gave it, nor with one made up or for another account
        for bad in ("", "x.y.z", ticket[:-1] + ("0" if ticket[-1] != "0" else "1"), ticket.replace(aid, "0" * 16),
                    f"{aid}.{int(time.time()) - 5}.{ticket.rsplit('.', 1)[1]}"):
            self.assertEqual(self.req("POST", "/login/admin", urllib.parse.urlencode({"ticket": bad, "auth": auth}))[0], 400, bad)
        self.assertEqual(self.req("POST", "/login/admin", urllib.parse.urlencode({"ticket": ticket, "auth": "abcd"}))[0], 400)
        self.assertIn(aid, self.acc._recs)
        status, headers, _ = self.req("POST", "/login/admin", urllib.parse.urlencode({"ticket": ticket, "auth": auth}))
        self.assertEqual(status, 303)
        admin = headers["Set-Cookie"].split(";")[0]
        # it is an end-to-end encrypted admin now, under a new id, with the note it had
        st = self.get("/api/status", admin)[1]
        self.assertEqual((st["role"], st["e2e"], st["admin"]), ("account", True, True))
        self.assertNotEqual(st["account"], aid)
        self.assertEqual(self.ask("GET", "/api/settings", admin)[0], 200)
        self.assertEqual(self.ask("GET", "/api/list?path=/", admin)[0], 403)
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual(list(listed), [st["account"]])
        self.assertEqual((listed[st["account"]]["admin"], listed[st["account"]]["e2e"], listed[st["account"]]["note"]), (True, True, "old"))
        self.assertIsNotNone(self.sign_in_key(new))
        # the old words, and the ticket, are used up
        status, page, again = self.old_sign_in(key)
        self.assertEqual((status, again), (401, None))
        self.assertEqual(self.req("POST", "/login/admin", urllib.parse.urlencode({"ticket": ticket, "auth": vault.auth_key(os.urandom(32)).hex()}))[0], 400)
        # one that is switched off gets no new words
        aid2, key2 = self.old_admin()
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid2, "off": True}, self.owner)[0], 200)
        self.assertEqual(self.old_sign_in(key2)[::2], (401, None))

    # ------------------------------------------------------------ no account this computer can read is made
    def test_the_owner_makes_accounts(self):
        self.assertEqual(self.acc.mode(), "closed")              # whoever sign-up is open to
        # end-to-end encrypted: only the sign-in key comes here
        key = os.urandom(32)
        status, r = self.post("/api/accounts/create", {"auth": vault.auth_key(key).hex(), "note": "Kim"}, self.owner)
        self.assertEqual((status, r["e2e"]), (200, True), r)
        self.assertNotIn("phrase", r)
        v = vault.Vault(lite.HostClient(self.address, auth=vault.auth_key(key).hex()), key).sync()
        v.put("/hello.txt", b"hi")
        self.assertEqual(v.read("/hello.txt"), b"hi")
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual((listed[r["id"]]["note"], listed[r["id"]]["e2e"], listed[r["id"]]["admin"]), ("Kim", True, False))
        # the same words twice: refused
        self.assertEqual(self.post("/api/accounts/create", {"auth": vault.auth_key(key).hex()}, self.owner)[0], 409)
        self.assertEqual(self.post("/api/accounts/create", {"auth": "abcd"}, self.owner)[0], 400)
        # no words are made here any more: not for an account, not for an admin
        before = self.acc.count()
        for path in ("/api/accounts/create", "/api/accounts/admin"):
            for body in ({"kind": "phrase"}, {}, {"note": "x"}):
                status, r = self.post(path, body, self.owner)
                self.assertEqual(status, 400, (path, body))
                self.assertNotIn("phrase", r)
        self.assertEqual(self.acc.count(), before)

    def test_signing_up_makes_no_words_here(self):
        self.d.cfg.signup_mode = "public"
        for form in ({"step": "new"}, {"step": "confirm", "phrase": words.key_to_phrase(os.urandom(32)), "ticket": "1.x"}, {}):
            status, _, page = self.req("POST", "/signup", urllib.parse.urlencode(form))
            self.assertEqual(status, 400, form)
            self.assertNotIn(b'name="phrase"', page)
        self.assertEqual(self.acc.count(), 0)
        self.assertEqual(self.req("POST", "/signup", urllib.parse.urlencode({"step": "e2e", "auth": vault.auth_key(os.urandom(32)).hex()}))[0], 303)

    # ------------------------------------------------------------ accounts of the earlier kind
    def test_an_account_of_the_earlier_kind_is_only_read(self):
        aid, phrase, cookie = self.earlier_account()
        st = self.get("/api/status", cookie)[1]
        self.assertEqual((st["role"], st["e2e"], st["retired"], st["admin"]), ("account", False, True, False))
        # what is in it is still there to look at and to download, also after signing in again
        cookie = self.sign_in(phrase)
        self.assertEqual(self.names(cookie), ["old.txt"])
        self.assertEqual(self.req("GET", "/api/file?path=/old.txt", cookie=cookie)[2], b"from before")
        # nothing in it changes any more
        self.assertEqual(self.put(cookie, "/new.txt", b"x"), 403)
        for path, body in (("/api/mkdir", {"path": "/d"}), ("/api/delete", {"paths": ["/old.txt"]}), ("/api/move", {"src": "/old.txt", "dst": "/b.txt"}),
                           ("/api/share", {"path": "/old.txt"}), ("/api/star", {"path": "/old.txt", "on": True}), ("/api/chat/connect", {})):
            status, r = self.post(path, body, cookie)
            self.assertEqual(status, 403, path)
        self.assertIn("end-to-end encrypted", r["error"])
        self.assertEqual(self.names(cookie), ["old.txt"])
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual((listed[aid]["e2e"], listed[aid]["admin"], listed[aid]["files"]), (False, False, 1))

    def test_an_account_of_the_earlier_kind_moves_out(self):
        aid, phrase, cookie = self.earlier_account()
        self.assertEqual(self.acc.mode(), "closed")              # (whoever sign-up is open to: it replaces itself)
        # its page makes 24 words and signs in to the new account next to the old one (moving.js)
        key = os.urandom(32)
        auth = vault.auth_key(key).hex()
        self.assertEqual(self.post("/api/accounts/e2e", {"auth": auth}, cookie)[0], 404)
        status, r = self.post("/api/accounts/e2e", {"auth": auth, "create": True}, cookie)
        self.assertEqual((status, r["made"]), (200, True), r)
        new = r["id"]
        self.assertTrue(self.acc.active(new)["e2e"])
        self.assertEqual(self.acc.successor(aid), new)
        # the files are read as the old account and sealed into the new one, with its session
        v = vault.Vault(lite.HostClient(self.address, auth=auth), key).sync()
        v.put("/old.txt", self.req("GET", "/api/file?path=/old.txt", cookie=cookie)[2])
        self.assertEqual(v.read("/old.txt"), b"from before")
        self.assertEqual(self.get("/api/status", "dd_session=" + r["session"])[1]["account"], new)
        # one account to move into, not as many as it likes; signing in to that one again is fine
        status, r2 = self.post("/api/accounts/e2e", {"auth": vault.auth_key(os.urandom(32)).hex(), "create": True}, cookie)
        self.assertEqual(status, 409, r2)
        self.assertEqual(self.post("/api/accounts/e2e", {"auth": auth}, cookie)[1]["id"], new)
        # then the old one deletes itself
        self.assertEqual(self.post("/api/account/delete", {"confirm": "delete"}, cookie)[0], 200)
        self.assertNotIn(aid, self.acc._recs)
        self.assertIsNone(self.sign_in(phrase))
        self.assertEqual(vault.Vault(lite.HostClient(self.address, auth=auth), key).sync().read("/old.txt"), b"from before")

    # ------------------------------------------------------------ the command
    def run_command(self, *argv, cfg=None):
        out = io.StringIO()
        args = argparse.Namespace(action=argv[0], rest=list(argv[1:]))
        with mock.patch("ddrive.accountscli.Config.load", return_value=cfg or self.d.cfg), contextlib.redirect_stdout(out), \
                mock.patch("sys.stdin", io.StringIO("")):
            code = accountscli.main(args)
        return code, out.getvalue()

    def test_the_command(self):
        cfg = self.d.cfg
        cfg.web_enabled = True
        cfg.web_port = self.web.port
        run = self.run_command
        # it makes no admin: the words would be made on this computer
        code, out = run("admin", "for", "Alex")
        self.assertEqual(code, 1)
        self.assertIn("made in a browser", out)
        self.assertEqual(self.acc.count(), 0)
        aid, key = self.make_admin("for Alex")
        code, out = run("list")
        self.assertEqual(code, 0)
        self.assertIn(aid, out)
        self.assertIn("for Alex", out)
        self.assertEqual(run("disable", aid)[0], 0)
        self.assertFalse(self.acc.admin_ok(aid))
        self.assertEqual(run("enable", aid)[0], 0)
        self.assertTrue(self.acc.admin_ok(aid))
        self.assertEqual(run("moves"), (0, "No moves between accounts.\n"))
        self.assertEqual(run("delete", aid)[0], 0)
        self.assertNotIn(aid, self.acc._recs)
        self.assertEqual(run("bogus")[0], 1)

    def test_the_command_while_the_drive_is_stopped(self):
        aid, key = self.make_admin()
        cfg = self.d.cfg
        cfg.web_enabled = False                             # nothing to ask: the list on disk is read and changed
        with mock.patch("ddrive.accountscli._Stub.local_test_dir", self.channel):
            code, out = self.run_command("list")
            self.assertEqual(code, 0, out)
            self.assertIn(aid, out)
            self.assertEqual(self.run_command("admin")[0], 1)
            self.assertEqual(self.run_command("disable", aid)[0], 0)
            fresh = accounts.Accounts(accountscli._Stub(cfg))       # as the drive reads it when it starts
            self.assertFalse(fresh.admin_ok(aid))
            self.assertEqual(self.run_command("enable", aid)[0], 0)
            fresh = accounts.Accounts(accountscli._Stub(cfg))
            self.assertTrue(fresh.admin_ok(aid))
            self.assertEqual(fresh.find(vault.auth_key(key))["id"], aid)
            code, out = self.run_command("delete", aid)             # it has storage: the drive removes that
            self.assertEqual(code, 1)
            self.assertIn("needs the drive running", out)


if __name__ == "__main__":
    helpers.unittest.main()
