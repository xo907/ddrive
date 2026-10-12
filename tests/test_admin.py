"""Admin accounts (co-owners with 24 words of their own) and the `accounts` command."""

import argparse
import contextlib
import io
import os
import urllib.parse
from unittest import mock

import helpers
import test_accounts
from ddrive import accountscli, words
from ddrive.web import session_cookie

_A = test_accounts.AccountsTest.__dict__


class AdminTest(helpers.DriveTest):
    # the dashboard and its helpers as the accounts' tests have them (without running those tests again)
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.acc = self.web.accounts
        self.owner = self.sign_in(words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key)))

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    req, get, post, sign_in, sign_up, put, names = (_A[k] for k in ("req", "get", "post", "sign_in", "sign_up", "put", "names"))
    def make_admin(self, note="Sam"):
        status, r = self.post("/api/accounts/admin", {"note": note}, self.owner)
        self.assertEqual(status, 200, r)
        self.assertEqual(len(r["phrase"].split()), 24)
        return r["id"], r["phrase"]

    def test_an_admin_is_a_co_owner(self):
        aid, phrase = self.make_admin()
        admin = self.sign_in(phrase)
        self.assertIsNotNone(admin)
        # the owner's dashboard: the owner's files, settings and accounts
        self.assertEqual(self.put(self.owner, "/owner.txt", b"hello"), 200)
        self.assertIn("owner.txt", self.names(admin))
        status, st = self.get("/api/status", admin)
        self.assertEqual((status, st["role"], st["admin"]), (200, "owner", aid))
        self.assertEqual(self.get("/api/settings", admin)[0], 200)
        self.assertEqual(self.get("/api/accounts", admin)[0], 200)
        # ... and can make other admins
        status, other = self.post("/api/accounts/admin", {}, admin)
        self.assertEqual(status, 200)
        # but not change how the owner signs in
        for path in ("/api/signin/passkey/options", "/api/signin/passkey/register", "/api/signin/passkey/remove",
                     "/api/signin/phrase", "/api/webdav/password"):
            self.assertEqual(self.post(path, {}, admin)[0], 403, path)
        self.assertEqual(self.get("/api/signin", admin)[1]["admin"], aid)
        self.assertEqual(self.get("/api/signin", self.owner)[1]["admin"], "")
        # it is listed as an admin; it has no storage and no key is kept for it
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertTrue(listed[aid]["admin"])
        self.assertEqual(listed[aid]["note"], "Sam")
        self.assertNotIn(aid, self.acc._secrets)
        self.assertFalse(os.path.exists(self.acc.folder(aid)))
        # it isn't an account with a drive of its own: an account session for it is refused
        bogus = "dd_session=" + session_cookie(self.d.cfg.web_token, aid)
        self.assertEqual(self.get("/api/status", bogus)[0], 401)

    def test_switching_an_admin_off_signs_it_out(self):
        aid, phrase = self.make_admin()
        admin = self.sign_in(phrase)
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid, "off": True}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", admin)[0], 401)
        self.assertIsNone(self.sign_in(phrase))
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid, "off": False}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", admin)[0], 200)        # the same session works again
        self.assertEqual(self.post("/api/accounts/delete", {"id": aid}, self.owner)[0], 200)
        self.assertEqual(self.get("/api/status", admin)[0], 401)
        self.assertIsNone(self.sign_in(phrase))

    def sign_in_key(self, phrase):
        """Sign in as the page does on https: with the sign-in key made from the words, not the words."""
        from ddrive import vault
        auth = vault.auth_key(words.phrase_to_key(phrase)).hex()
        status, headers, _ = self.req("POST", "/login", urllib.parse.urlencode({"auth": auth}))
        return headers["Set-Cookie"].split(";")[0] if status == 303 else None

    def test_an_admin_signs_in_without_sending_its_words(self):
        aid, phrase = self.make_admin()
        admin = self.sign_in_key(phrase)
        self.assertIsNotNone(admin)
        status, st = self.get("/api/status", admin)
        self.assertEqual((status, st["role"], st["admin"]), (200, "owner", aid))
        self.assertNotIn(aid, self.acc._secrets)            # still no key of it here
        # the sign-in key of other words, or of the owner's, signs nobody in
        self.assertIsNone(self.sign_in_key(words.key_to_phrase(os.urandom(32))))
        self.assertIsNone(self.sign_in_key(words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))))
        # switched off: not this way either
        self.assertEqual(self.post("/api/accounts/disable", {"id": aid, "off": True}, self.owner)[0], 200)
        self.assertIsNone(self.sign_in_key(phrase))
        self.assertEqual(self.get("/api/status", admin)[0], 401)

    def test_an_admin_from_before_signs_in_with_its_words_once(self):
        aid, phrase = self.make_admin()
        with self.acc._lock:                                # as 2.16.0 to 2.17.1 made it
            del self.acc._recs[aid]["auth"]
        self.assertIsNone(self.sign_in_key(phrase))
        self.assertIsNotNone(self.sign_in(phrase))          # the earlier way, which sends the words
        self.assertIsNotNone(self.sign_in_key(phrase))

    def test_admins_dont_count_as_accounts(self):
        self.d.cfg.signup_mode = "public"
        self.d.cfg.accounts_max = 1
        self.make_admin()
        self.make_admin()
        cookie, _ = self.sign_up()
        self.assertIsNotNone(cookie)
        self.assertIsNone(self.sign_up()[0])                # the one account the limit allows is made

    def test_admins_come_back_with_the_saved_list(self):
        aid, phrase = self.make_admin()
        self.assertTrue(self.acc.backup())
        mid = self.acc.backups(max_age=0)[0]["id"]
        with self.acc._lock:                                # this computer's disk was lost
            self.acc._recs.clear()
            self.acc._save()
        self.assertEqual(self.acc.restore(mid), 1)
        self.assertIsNotNone(self.sign_in(phrase))
        self.assertTrue(self.acc.admin_ok(aid))

    def test_the_owner_makes_accounts(self):
        from ddrive import lite, vault
        self.assertEqual(self.acc.mode(), "closed")              # whoever sign-up is open to
        # end-to-end encrypted: only the sign-in key comes here
        key = os.urandom(32)
        status, r = self.post("/api/accounts/create", {"auth": vault.auth_key(key).hex(), "note": "Kim"}, self.owner)
        self.assertEqual((status, r["e2e"]), (200, True), r)
        self.assertNotIn("phrase", r)
        v = vault.Vault(lite.HostClient(f"http://127.0.0.1:{self.web.port}", auth=vault.auth_key(key).hex()), key).sync()
        v.put("/hello.txt", b"hi")
        self.assertEqual(v.read("/hello.txt"), b"hi")
        listed = {a["id"]: a for a in self.get("/api/accounts", self.owner)[1]["accounts"]}
        self.assertEqual((listed[r["id"]]["note"], listed[r["id"]]["e2e"]), ("Kim", True))
        # the same words twice: refused
        self.assertEqual(self.post("/api/accounts/create", {"auth": vault.auth_key(key).hex()}, self.owner)[0], 409)
        self.assertEqual(self.post("/api/accounts/create", {"auth": "abcd"}, self.owner)[0], 400)
        # the earlier kind: the words are made here and given back once
        status, r = self.post("/api/accounts/create", {"kind": "phrase"}, self.owner)
        self.assertEqual((status, r["e2e"]), (200, False))
        cookie = self.sign_in(r["phrase"])
        self.assertEqual(self.get("/api/account", cookie)[1]["id"], r["id"])
        # an admin can too; an account can't
        _, phrase = self.make_admin()
        self.assertEqual(self.post("/api/accounts/create", {"kind": "phrase"}, self.sign_in(phrase))[0], 200)
        self.assertEqual(self.post("/api/accounts/create", {"kind": "phrase"}, cookie)[0], 403)

    def test_the_command(self):
        cfg = self.d.cfg
        cfg.web_enabled = True
        cfg.web_port = self.web.port

        def run(*argv):
            out = io.StringIO()
            args = argparse.Namespace(action=argv[0], rest=list(argv[1:]))
            with mock.patch("ddrive.accountscli.Config.load", return_value=cfg), contextlib.redirect_stdout(out):
                code = accountscli.main(args)
            return code, out.getvalue()

        code, out = run("admin", "for", "Alex")
        self.assertEqual(code, 0, out)
        aid = next(a for a, r in self.acc._recs.items() if r.get("admin"))
        self.assertIn(aid, out)
        self.assertEqual(self.acc._recs[aid]["note"], "for Alex")
        code, out = run("list")
        self.assertEqual(code, 0)
        self.assertIn(aid, out)
        self.assertEqual(run("disable", aid)[0], 0)
        self.assertFalse(self.acc.admin_ok(aid))
        self.assertEqual(run("enable", aid)[0], 0)
        self.assertTrue(self.acc.admin_ok(aid))
        self.assertEqual(run("moves"), (0, "No moves between accounts.\n"))
        self.assertEqual(run("delete", aid)[0], 0)
        self.assertNotIn(aid, self.acc._recs)
        self.assertEqual(run("bogus")[0], 1)

    def test_the_command_while_the_drive_is_stopped(self):
        cfg = self.d.cfg
        cfg.web_enabled = False                             # nothing to ask: the list on disk is changed
        args = argparse.Namespace(action="admin", rest=[])
        out = io.StringIO()
        with mock.patch("ddrive.accountscli.Config.load", return_value=cfg), contextlib.redirect_stdout(out), \
                mock.patch("ddrive.accountscli._Stub.local_test_dir", self.channel):
            self.assertEqual(accountscli.main(args), 0)
        phrase = " ".join(out.getvalue().split("\n\n")[1].split()[1::2])
        from ddrive.accounts import Accounts
        fresh = Accounts(accountscli._Stub(cfg))            # as the drive reads it when it starts
        aid = next(a for a, r in fresh._recs.items() if r.get("admin"))
        self.assertTrue(fresh.admin_ok(aid))
        self.assertEqual(fresh.find(words.phrase_to_key(phrase))["id"], aid)


if __name__ == "__main__":
    helpers.unittest.main()
