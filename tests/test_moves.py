"""Moving an end-to-end encrypted account into another one: a key exchange through the host, which
copies the sealed objects and passes the sealed list on without being able to open either."""

import base64
import http.client
import json
import os
import shutil
import subprocess
import urllib.parse

import helpers
from ddrive import lite, p256, transfer, vault, words

MARK = b"MOVED-PLAINTEXT-that-the-host-must-never-see"


class MoveTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.host = self.drive(web_token="secret-token")
        self.host.cfg.signup_mode = "public"
        self.web = self.host.start_web(port=0)
        self.acc, self.moves = self.web.accounts, self.web.moves
        self.address = f"http://127.0.0.1:{self.web.port}"
        self.skey, self.tkey = os.urandom(32), os.urandom(32)
        for k in (self.skey, self.tkey):
            self.assertEqual(self.form("/signup", {"step": "e2e", "auth": vault.auth_key(k).hex()})[0], 303)
        self.src, self.dst = self.device(self.skey), self.device(self.tkey)
        self.sid = self.acc.find(vault.auth_key(self.skey))["id"]
        self.tid = self.acc.find(vault.auth_key(self.tkey))["id"]

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

    def device(self, key):
        return vault.Vault(lite.HostClient(self.address, auth=vault.auth_key(key).hex()), key).sync()

    def fill(self):
        v = self.src
        v.mkdir("/Photos")
        big = MARK + os.urandom(2 * 1024 * 1024 + 7)
        v.put("/Photos/a.jpg", big, mtime=1_700_000_000)
        v.put("/notes.txt", b"first")
        v.put("/notes.txt", b"second")                      # an earlier version
        v.put("/gone.txt", b"deleted but kept")
        v.delete("/gone.txt")
        return big

    def run_copy(self, mid):
        while self.moves.step(mid):
            pass

    def objects(self, aid):
        d = self.acc.acquire(aid)
        try:
            top = d.index.resolve("/o")
            return {c["name"] for sub in (d.index.children(top["id"]) if top else []) for c in d.index.children(sub["id"])}
        finally:
            self.acc.release(d)

    def test_a_whole_account_moves(self):
        big = self.fill()
        before = {o for o in self.objects(self.sid)}
        # each account's page is told of the move it is in, wherever it is looked at (the card that follows it)
        told = lambda v: v.client._json("/api/status")["move"]
        self.assertEqual((told(self.src), told(self.dst)), (None, None))
        opened = self.dst.move_open()
        self.assertRegex(opened["code"], r"^[A-Z2-7]{4}-[A-Z2-7]{4}$")
        self.assertEqual((told(self.dst)["state"], told(self.dst)["side"], told(self.dst)["code"], told(self.src)),
                         ("open", "to", opened["code"], None))
        check = self.src.move_send(opened["id"], code=opened["code"])
        st = self.dst.move_status()
        self.assertEqual((st["state"], st["from"], st["check"]), ("copying", self.sid, check))
        for v, side in ((self.dst, "to"), (self.src, "from")):
            m = told(v)
            self.assertEqual((m["id"], m["side"], m["state"], m["from"], m["to"], m["check"]),
                             (opened["id"], side, "copying", self.sid, self.tid, check))
            self.assertIn("objects", m)
        # the account that moves out can't change anything now, nor be deleted
        with self.assertRaises(lite.HostError) as e:
            self.src.put("/late.txt", b"too late")
        self.assertEqual(e.exception.status, 423)
        self.run_copy(opened["id"])
        self.assertEqual(self.dst.move_status()["state"], "ready")
        self.assertEqual((told(self.dst)["state"], told(self.src)["state"]), ("ready", "ready"))
        name = self.dst.move_take(check=check)
        self.assertEqual(name, f"From account {self.sid}")
        # everything is in the new folder, with its earlier versions and what was deleted
        self.assertEqual(self.dst.read(f"/{name}/Photos/a.jpg"), big)
        self.assertEqual(self.dst.read(f"/{name}/notes.txt"), b"second")
        notes = self.dst.resolve(f"/{name}/notes.txt")
        self.assertEqual(len(notes["versions"]), 1)
        gone = [n for n in self.dst.nodes.values() if n.get("trash")]
        self.assertEqual([g["trash"]["path"] for g in gone], [f"/{name}/gone.txt"])
        # the objects are the target's now (the same ids), and the source account is deleted once they are stored
        self.assertTrue(before - {self.src.snapshot_object} <= self.objects(self.tid))
        self.upload_all(self.tid)
        self.run_copy(opened["id"])
        self.assertEqual(self.moves.status(opened["id"], None)["state"], "done")
        self.assertIsNone(told(self.dst))                   # done: nothing to follow any more
        self.assertIsNone(self.acc._recs.get(self.sid))
        with self.assertRaises(lite.HostError):
            self.device(self.skey)
        # another device of the target sees it all, and taking it in again changes nothing
        other = self.device(self.tkey)
        self.assertEqual(other.read(f"/{name}/Photos/a.jpg"), big)
        self.assertNotIn("/xin", [c["name"] for c in self.children(self.tid, "/")])

    def upload_all(self, aid):
        d = self.acc.acquire(aid)
        try:
            for nid in d.index.unsynced_ids():
                d.uploader.run_now(nid)
        finally:
            self.acc.release(d)

    def children(self, aid, path):
        d = self.acc.acquire(aid)
        try:
            return d.index.children(d.index.resolve(path)["id"])
        finally:
            self.acc.release(d)

    def test_the_host_sees_neither_files_nor_keys(self):
        self.fill()
        opened = self.dst.move_open()
        self.src.move_send(opened["id"])
        self.run_copy(opened["id"])
        rec = self.moves._moves[opened["id"]]
        # what the host kept: the public keys, and a sealed list it has no key for
        spub, sealed = self.moves.sealed_list(opened["id"], self.tid)
        self.assertNotIn(MARK, sealed)
        self.assertNotIn(b"notes.txt", sealed)
        for k in ("priv", "key", "shared"):
            self.assertNotIn(k, rec)
        with self.assertRaises(vault.VaultError):            # not with either account's keys
            vault.unseal(vault.meta_key(self.tkey), sealed, vault.AAD_MOVE)
        with self.assertRaises(vault.VaultError):
            vault.unseal(vault.meta_key(self.skey), sealed, vault.AAD_MOVE)
        # and nothing it stores for the target is in the clear
        d = self.acc.acquire(self.tid)
        try:
            for r in d.index._all("SELECT id, size FROM nodes WHERE is_dir=0"):
                self.assertNotIn(MARK, d.fs.read_file(r["id"], 0, r["size"]))
        finally:
            self.acc.release(d)

    def test_a_swapped_key_is_caught(self):
        self.fill()
        opened = self.dst.move_open()
        # the host hands the source its own key instead of the target's: the codes differ
        evil_priv, evil_pub = p256.keypair()
        self.moves._moves[opened["id"]]["tpub"] = base64.b64encode(evil_pub).decode()
        with self.assertRaises(vault.VaultError):
            self.src.move_send(opened["id"], code=opened["code"])
        self.assertEqual(self.moves._moves[opened["id"]]["state"], "open")        # nothing was sent

    def test_a_list_from_somebody_else_is_caught(self):
        self.fill()
        opened = self.dst.move_open()
        check = self.src.move_send(opened["id"], code=opened["code"])
        self.run_copy(opened["id"])
        with self.assertRaises(vault.VaultError):
            self.dst.move_take(check="AAAA-AAAA")
        self.assertEqual(self.dst.move_status()["state"], "ready")
        self.assertEqual(self.dst.move_take(check=check), f"From account {self.sid}")

    def test_cancelling_leaves_the_source_as_it_was(self):
        self.fill()
        opened = self.dst.move_open()
        self.src.move_send(opened["id"])
        self.run_copy(opened["id"])
        self.assertTrue(self.children(self.tid, "/xin"))
        self.assertEqual(self.dst.client._json("/api/vault/move/cancel", {"id": opened["id"]})["state"], "cancelled")
        self.assertIsNone(self.acc.acquire(self.tid).index.resolve("/xin"))
        self.acc.release(self.acc._open[self.tid])
        self.src.put("/after.txt", b"it can change again")
        self.assertEqual(self.device(self.skey).read("/Photos/a.jpg")[:len(MARK)], MARK)
        with self.assertRaises(vault.VaultError):
            self.dst.move_take()

    def test_what_is_refused(self):
        opened = self.dst.move_open()
        # the target can't answer its own request; a wrong code finds nothing
        with self.assertRaises(lite.HostError) as e:
            self.dst.move_send(opened["id"])
        self.assertEqual(e.exception.status, 400)
        with self.assertRaises(lite.HostError) as e:
            self.src.client._json("/api/vault/move/peer?id=" + "a" * 16)
        self.assertEqual(e.exception.status, 404)
        # accounts that aren't end-to-end encrypted aren't made any more, and the owner doesn't take part
        self.assertEqual(self.form("/signup", {"step": "new"})[0], 400)
        owner = self.sign_in_owner()
        self.assertEqual(self.http("POST", "/api/vault/move/open", {"pub": "x"}, owner)[0], 403)
        # the owner sees the moves and can stop them, but can't delete an account that is moving
        self.src.move_send(opened["id"])
        status, d = self.http("GET", "/api/accounts/moves", None, owner)
        self.assertEqual([m["id"] for m in d["moves"]], [opened["id"]])
        self.assertTrue({a["id"]: a for a in self.http("GET", "/api/accounts", None, owner)[1]["accounts"]}[self.sid]["moving"])
        self.assertEqual(self.http("POST", "/api/accounts/delete", {"id": self.sid}, owner)[0], 409)
        self.assertEqual(self.http("POST", "/api/accounts/moves/cancel", {"id": opened["id"]}, owner)[1]["state"], "cancelled")
        self.assertEqual(self.http("POST", "/api/accounts/delete", {"id": self.sid}, owner)[0], 200)

    def test_a_request_runs_out(self):
        opened = self.dst.move_open()
        self.moves._moves[opened["id"]]["created"] -= transfer.OPEN_MINUTES * 60 + 1
        with self.assertRaises(lite.HostError) as e:
            self.src.move_send(opened["id"])
        self.assertEqual(e.exception.status, 410)
        self.moves.tidy()
        self.assertEqual(self.dst.move_status()["state"], "cancelled")

    def test_it_must_fit(self):
        self.fill()
        self.host.cfg.account_quota_bytes = 3 * 1024 * 1024        # each fits alone, not both
        self.dst.put("/own.bin", os.urandom(1024 * 1024))
        opened = self.dst.move_open()
        with self.assertRaises(lite.HostError) as e:
            self.src.move_send(opened["id"])
        self.assertEqual(e.exception.status, 507)

    def test_a_copy_goes_on_after_a_restart(self):
        big = self.fill()
        opened = self.dst.move_open()
        check = self.src.move_send(opened["id"])
        rec = self.moves._moves[opened["id"]]
        stops = iter([False, True])                           # stopped after the first object
        real = self.moves._stopped
        self.moves._stopped = lambda m: next(stops, False) or real(m)
        self.moves.step(opened["id"])
        self.moves._stopped = real
        self.assertEqual(rec["state"], "copying")
        fresh = transfer.Moves(self.acc)                       # as after a restart: read from disk
        self.assertEqual(fresh._moves[opened["id"]]["state"], "copying")
        self.web.moves = self.acc.moves = self.moves = fresh
        self.run_copy(opened["id"])
        self.assertEqual(self.dst.move_take(check=check), f"From account {self.sid}")
        self.assertEqual(self.dst.read(f"/From account {self.sid}/Photos/a.jpg"), big)

    def test_the_copy_runs_by_itself(self):
        big = self.fill()
        self.moves.background = True                          # as when the drive runs: a thread of its own
        opened = self.dst.move_open()
        check = self.src.move_send(opened["id"])
        self.assertEqual(self.dst.move_take(check=check, wait=30), f"From account {self.sid}")
        self.assertEqual(self.dst.read(f"/From account {self.sid}/Photos/a.jpg"), big)
        self.upload_all(self.tid)
        import time
        end = time.time() + 30
        while self.moves.status(opened["id"], None)["state"] != "done" and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(self.moves.status(opened["id"], None)["state"], "done")
        self.assertIsNone(self.acc._recs.get(self.sid))

    def test_the_key_exchange_matches_the_browser(self):
        # fixed inputs, so that vaultcore.js (WebCrypto) and the phone app can be checked against these
        shared, tpub, spub = bytes(range(32)), b"\x04" + bytes(64), b"\x04" + bytes([1]) * 64
        self.assertEqual(len(vault.move_key(shared, tpub, spub)), 32)
        self.assertEqual(vault.move_key(shared, tpub, spub).hex(),
                         vault.hkdf_salted(shared, __import__("hashlib").sha256(tpub + spub).digest(), vault.INFO_MOVE).hex())
        self.assertEqual(transfer.key_code(tpub), transfer.key_code(tpub))
        self.assertNotEqual(transfer.check_code(tpub, spub), transfer.check_code(spub, tpub))
        # pure Python and the cryptography package agree
        a, apub = p256.pure_keypair()
        b, bpub = p256.keypair()
        self.assertEqual(p256.pure_shared(a, bpub), p256.shared(b, apub))
        with self.assertRaises(p256.BadKey):
            p256.pure_shared(a, b"\x04" + bytes(64))

    @helpers.unittest.skipUnless(shutil.which("node"), "needs Node.js to run the page's code")
    def test_the_page_and_this_client_agree(self):
        """accountmove.js (WebCrypto, run by Node here) makes the same codes, ids and key as vault.py."""
        page = os.path.join(helpers.ROOT, "ddrive", "web", "accountmove.js")
        runner = r"""
const fs = require("fs"), vm = require("vm");
const ctx = { crypto: globalThis.crypto, TextEncoder, TextDecoder, console, setTimeout };
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const c = ctx.accountMove.crypto, input = JSON.parse(fs.readFileSync(0, "utf8")), hex = (b) => Buffer.from(b).toString("hex");
(async () => {
  const t = Buffer.from(input.tpub, "hex"), s = Buffer.from(input.spub, "hex"), py = Buffer.from(input.pypub, "hex");
  const kp = await c.keypair(), key = await c.agree(kp.priv, py, new Uint8Array(kp.pub), py);
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: new Uint8Array(12) }, key, new TextEncoder().encode("hello"));
  console.log(JSON.stringify({ code: await c.keyCode(t), check: await c.checkCode(t, s), id: await c.movedId("abc", "def"),
    folder: await c.movedFolder("abc"), pub: hex(kp.pub), ct: hex(new Uint8Array(ct)) }));
})();
"""
        tpub, spub = b"\x04" + bytes(range(64)), b"\x04" + bytes(range(64, 128))
        d, pypub = p256.pure_keypair()
        out = subprocess.run(["node", "-e", runner, page], input=json.dumps({"tpub": tpub.hex(), "spub": spub.hex(), "pypub": pypub.hex()}),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual((r["code"], r["check"]), (transfer.key_code(tpub), transfer.check_code(tpub, spub)))
        self.assertEqual((r["id"], r["folder"]), (vault.moved_id("abc", "def"), vault.moved_folder("abc")))
        pub = bytes.fromhex(r["pub"])
        key = vault.move_key(p256.pure_shared(d, pub), pub, pypub)
        blob = bytes.fromhex(r["ct"])                                  # ciphertext + tag, as WebCrypto gives it
        self.assertEqual(vault.unseal(key, bytes(12) + blob[-16:] + blob[:-16], b""), b"hello")

    # ------------------------------------------------------------ helpers
    def sign_in_owner(self):
        phrase = words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key))
        status, headers, _ = self.form("/login", {"phrase": phrase})
        return headers["Set-Cookie"].split(";")[0]

    def http(self, method, path, body, cookie):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        h = {"Cookie": cookie}
        data = None
        if method == "POST":
            h.update({"X-DD": "1", "Content-Type": "application/json"})
            data = json.dumps(body).encode()
        conn.request(method, path, data, h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, (json.loads(raw) if raw[:1] == b"{" else raw)


if __name__ == "__main__":
    helpers.unittest.main()
