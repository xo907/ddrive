"""The web dashboard's HTTP API, against a real server on a free local port."""

import http.client
import io
import json
import logging
import os
import time
import urllib.parse
import zipfile

import helpers
from ddrive import locks, words

STRANGER = words.key_to_phrase(os.urandom(32))       # 24 valid words that belong to nobody


class WebTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.web.set_credentials("Dennis", "correct horse")       # what other apps use over WebDAV
        self.phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        self.cookie = None

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def req(self, method, path, body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        h = dict(headers or {})
        if auth and self.cookie:
            h["Cookie"] = self.cookie
        if method == "POST":
            h.setdefault("X-DD", "1")
            if isinstance(body, str):
                body = body.encode()
                h["Content-Type"] = "application/x-www-form-urlencoded"
            elif isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def form(self, **fields):
        return urllib.parse.urlencode(fields)

    def login(self, phrase=None):
        status, headers, body = self.req("POST", "/login", self.form(phrase=phrase or self.phrase), auth=False)
        if status == 303:
            self.cookie = headers["Set-Cookie"].split(";")[0]
        return status, body

    def test_sign_in_required(self):
        self.assertEqual(self.req("GET", "/api/status")[0], 401)
        status, _, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'action="/login"', body)                       # the sign-in form
        self.assertNotIn(b'type="password"', body)                    # no name, no password: 24 words
        self.assertEqual(self.login(STRANGER)[0], 401)
        self.assertEqual(self.login("just a few words")[0], 401)
        self.assertEqual(self.req("POST", "/login", self.form(user="dennis", password="correct horse"), auth=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/status")[0], 401)
        self.assertEqual(self.login()[0], 303)
        self.assertEqual(self.req("GET", "/api/status")[0], 200)
        status, _, body = self.req("GET", "/")
        self.assertIn(b"app.js", body)
        status = json.loads(self.req("GET", "/api/status")[2])
        self.assertEqual((status["user"], status["role"]), ("Dennis", "owner"))
        # asked again with the mark it came with, and nothing changed: only "the same"
        self.assertNotIn("resources", status)
        st, headers, body = self.req("GET", "/api/status")
        mark = headers["ETag"]
        self.assertIn("drive;dur=", headers["Server-Timing"])
        st, headers, body = self.req("GET", "/api/status", headers={"If-None-Match": mark})
        self.assertEqual((st, body, headers["ETag"]), (304, b"", mark))
        self.assertEqual(self.req("POST", "/api/mkdir", {"path": "/Changed"})[0], 200)
        st, headers, body = self.req("GET", "/api/status", headers={"If-None-Match": mark})
        self.assertEqual(st, 200)
        self.assertNotEqual(headers["ETag"], mark)
        self.assertGreater(json.loads(body)["rev"], status["rev"])
        r = json.loads(self.req("GET", "/api/status?res=1")[2])["resources"]      # what the drive uses, for the page that shows it
        self.assertGreater(r["memory"]["process"], 1 << 20)
        self.assertGreater(r["memory"]["total"], r["memory"]["available"])
        self.assertGreater(r["disk"]["total"], r["disk"]["free"])
        self.assertEqual((sorted(r["sizes"]), r["limits"]["staging"] > 0, r["cores"] >= 1),
                         (["cache", "index", "messages", "staging", "thumbs"], True, True))
        self.assertGreater(r["sizes"]["index"], 0)

    def test_damaged_files_come_back_from_an_intact_copy(self):
        self.login()
        d = self.d
        post = lambda body: json.loads(self.req("POST", "/api/repair", body)[2])
        lost = lambda: {(e["kind"], e["path"]): e for e in json.loads(self.req("GET", "/api/status")[2])["health"]["lost"]}
        d.cfg.parity_enabled, d.cfg.dedup = False, False
        for name in ("a", "b", "c"):
            self.write(d, f"/Docs/{name}.txt", f"{name} as it was in the snapshot".encode() * 300)
        self.upload(d)
        self.assertEqual(self.req("POST", "/api/snapshots/create", {"label": "before"})[0], 200)
        self.write(d, "/Docs/a.txt", b"a, changed after the snapshot" * 300)                # its older copy: an earlier version too
        self.write(d, "/Docs/new.txt", b"made after the snapshot, never changed" * 300)     # no older copy anywhere
        self.upload(d)
        mids = lambda path: [c["message_id"] for c in d.index.get_chunks(d.index.resolve(path)["id"])]
        for path in ("/Docs/a.txt", "/Docs/b.txt", "/Docs/new.txt"):
            mid = mids(path)[0]
            d.backend.delete(mid)                                                             # Discord loses a part
            self.assertIsNone(d.healer.recover(mid))
        d.healer._lost_list = (0.0, [])
        found = lost()
        self.assertEqual((found[("file", "/Docs/a.txt")]["fix"]["source"], found[("file", "/Docs/new.txt")]["fix"]), ("version", None))
        self.assertIsNone(found[("file", "/Docs/b.txt")]["fix"])       # unchanged since the snapshot: that copy has the same lost part
        self.assertEqual((found[("file", "/Docs/b.txt")]["copies"], found[("file", "/Docs/new.txt")]["copies"]), (1, 0))
        self.assertNotIn(("file", "/Docs/c.txt"), found)

        r = post({"paths": ["/Docs/a.txt", "/Docs/new.txt", "/Docs/c.txt"]})                 # c is not damaged: left alone
        self.assertEqual(([x["path"] for x in r["repaired"]], r["without"]), (["/Docs/a.txt"], ["/Docs/new.txt"]))
        self.assertEqual(self.read(d, "/Docs/a.txt"), b"a as it was in the snapshot" * 300)
        self.assertNotIn(("file", "/Docs/a.txt"), lost())                                     # it is whole again
        self.assertEqual(self.read(d, "/Docs/c.txt"), b"c as it was in the snapshot" * 300)
        versions = json.loads(self.req("GET", "/api/versions?path=%2FDocs%2Fa.txt")[2])["versions"]
        self.assertGreaterEqual(len(versions), 2)                      # the damaged one is kept, so this can be undone

        # a file changed after a snapshot and then damaged comes back from the snapshot when it has no version left
        self.write(d, "/Docs/c.txt", b"c, changed" * 300)
        self.upload(d)
        time.sleep(0.1)                                                # (on a fast computer they would be too new to expire)
        d.index.gc_versions(0.0000001, 0)                              # its earlier versions have expired
        mid = mids("/Docs/c.txt")[0]
        d.backend.delete(mid)
        self.assertIsNone(d.healer.recover(mid))
        d.healer._lost_list = (0.0, [])
        self.assertEqual(lost()[("file", "/Docs/c.txt")]["fix"]["source"], "snapshot")
        r = post({"all": True})
        self.assertEqual(([(x["path"], x["source"]) for x in r["repaired"]], sorted(r["without"])),
                         ([("/Docs/c.txt", "snapshot")], ["/Docs/b.txt", "/Docs/new.txt"]))
        self.assertEqual(self.read(d, "/Docs/c.txt"), b"c as it was in the snapshot" * 300)

    def test_checking_lost_parts_again(self):
        self.login()
        d = self.d
        def post(body):
            r = json.loads(self.req("POST", "/api/repair/recheck", body)[2])
            for _ in range(200):                                       # it works in the background and says how far it is
                state = json.loads(self.req("GET", "/api/status")[2])["health"]["recheck"]
                if body.get("forget") or not state["running"]:
                    return r if body.get("forget") else state
                time.sleep(0.05)
            self.fail("the check did not finish")
        lost = lambda: [e["path"] for e in json.loads(self.req("GET", "/api/status")[2])["health"]["lost"]]
        d.cfg.parity_enabled, d.cfg.dedup = False, False
        for name in ("there", "cached", "gone"):
            self.write(d, f"/{name}.bin", name.encode() * 4000)
        self.upload(d)
        d.cache.free_space(force=True)
        self.read(d, "/cached.bin", cold=False)                        # this computer has a copy of this one
        mid = lambda name: d.index.get_chunks(d.index.resolve(f"/{name}.bin")["id"])[0]["message_id"]
        path = d.backend._p(mid("there"), "bin")
        with open(path, "rb") as f:
            stored = f.read()
        for name in ("there", "gone", "cached"):
            d.backend.delete(mid(name))
            d.healer._note_lost(mid(name))
        with open(path, "wb") as f:                                    # Discord has this one after all
            f.write(stored)
        d.healer._lost_list = (0.0, [])
        self.assertEqual(sorted(lost()), ["/cached.bin", "/gone.bin", "/there.bin"])
        r = post({})                                                   # no waiting for the background's turn
        self.assertEqual((r["readable"], r["repaired"], r["left"], r["checked"]), (1, 1, 1, 3))
        self.assertEqual(lost(), ["/gone.bin"])
        self.assertEqual(self.read(d, "/cached.bin"), b"cached" * 4000)     # stored again, readable from Discord
        self.assertEqual(self.read(d, "/there.bin"), b"there" * 4000)
        self.assertEqual(post({})["left"], 1)
        self.assertEqual(post({"forget": True})["forgotten"], 1)       # the list can be emptied by hand
        self.assertEqual(lost(), [])

    def test_tidying_up(self):
        self.login()
        post = lambda what: json.loads(self.req("POST", "/api/resources/clean", {"what": what})[2])
        self.write(self.d, "/kept.txt", b"the uploaded version")
        self.upload(self.d)
        self.write(self.d, "/kept.txt", b"a change that never made it to Discord")
        self.write(self.d, "/new.txt", b"never uploaded")
        self.assertEqual(json.loads(self.req("GET", "/api/status?res=1")[2])["resources"]["waiting"], 2)
        r = post("uploads")
        self.assertEqual((r["count"], r["skipped"], r["freed"] > 0), (2, 0, True))
        self.assertIsNone(self.d.index.resolve("/new.txt"))            # never uploaded: gone
        self.assertEqual(self.read(self.d, "/kept.txt"), b"the uploaded version")
        self.assertEqual((self.d.uploader._pending, self.d.index.unsynced_ids()), ({}, []))
        self.assertEqual(post("uploads")["count"], 0)
        self.read(self.d, "/kept.txt", cold=False)                     # now in the read cache
        self.assertGreater(post("cache")["freed"], 0)
        self.assertEqual(post("cache")["freed"], 0)
        self.assertEqual(self.read(self.d, "/kept.txt", cold=False), b"the uploaded version")
        self.assertEqual((post("thumbs")["freed"], post("fetches")["count"]), (0, 0))
        # half-sent files: the drive's own and an account's. The ones nothing arrived for in an hour go,
        # with their notes; one that is still being sent to stays.
        data = self.d.cfg.resolved_data_dir
        mine, theirs = os.path.join(data, "partials"), os.path.join(data, "accounts", "0123456789abcdef", "partials")
        long_ago = __import__("time").time() - 7200
        for folder, name, n, old in ((mine, "a" * 32, 5000, True), (theirs, "b" * 32, 7000, True), (mine, "c" * 32, 900, False)):
            os.makedirs(folder, exist_ok=True)
            for ext, body in ((".part", b"x" * n), (".json", b"{}")):
                with open(os.path.join(folder, name + ext), "wb") as f:
                    f.write(body)
                if old:
                    os.utime(os.path.join(folder, name + ext), (long_ago, long_ago))
        hs = json.loads(self.req("GET", "/api/status?res=1")[2])["resources"]["half_sent"]
        self.assertEqual(hs, {"count": 3, "bytes": 12900, "idle_count": 2, "idle_bytes": 12000, "quiet_count": 2, "quiet_bytes": 12000})
        self.assertEqual(post("partials"), {"ok": True, "count": 2, "freed": 12000})
        self.assertEqual((sorted(os.listdir(mine)), os.listdir(theirs)), (["c" * 32 + ".json", "c" * 32 + ".part"], []))
        self.assertEqual(post("partials")["count"], 0)
        # one that got something within the hour only goes when that is asked for, and not while it is being sent this minute
        clean = lambda: json.loads(self.req("POST", "/api/resources/clean", {"what": "partials", "recent": True})[2])["count"]
        self.assertEqual(clean(), 0)
        a_while = __import__("time").time() - 600
        for ext in (".part", ".json"):
            os.utime(os.path.join(mine, "c" * 32 + ext), (a_while, a_while))
        self.assertEqual((post("partials")["count"], clean(), os.listdir(mine)), (0, 1, []))
        self.assertEqual(self.req("POST", "/api/resources/clean", {"what": "everything"})[0], 400)

    def test_forged_or_old_sessions_fail(self):
        self.login()
        self.cookie = self.cookie[:-4] + "AAAA"
        self.assertEqual(self.req("GET", "/api/status")[0], 401)
        self.login()
        self.web.set_credentials("Dennis", "a new password")           # the WebDAV password has nothing to do with it
        self.assertEqual(self.req("GET", "/api/status")[0], 200)
        self.web.cfg.web_token = "another-secret"                      # a new secret signs everyone out
        self.assertEqual(self.req("GET", "/api/status")[0], 401)
        self.assertEqual(self.login()[0], 303)

    def test_the_dashboard_has_what_uploading_needs(self):
        """The Upload button only opens the file chooser; what takes the chosen files and sends them was once
        lost from the page without any test noticing (2.4.0 to 2.23.1)."""
        page = self.req("GET", "/app.js", auth=False)[2].decode()
        for needed in ("async function uploadAll(", "function uploadFile(", "async function droppedFiles(",
                       "entries = await settleExisting(entries, dir);", "function askExisting(",       # (a file that is already there is asked about)
                       'for (const id of ["#pick", "#pick-dir"])', 'document.addEventListener("drop"'):
            self.assertIn(needed, page)
        html = self.req("GET", "/", auth=False)[2]
        self.login()
        html = self.req("GET", "/")[2]
        for needed in (b'id="pick"', b'id="pick-dir"', b'id="drop"'):
            self.assertIn(needed, html)

    def test_the_owner_says_where_thumbnails_are_kept(self):
        self.login()
        rule = lambda: json.loads(self.req("GET", "/api/status")[2])["thumbs"]
        self.assertEqual(rule(), {"store": "server", "bulk": True})          # as it always was
        self.write(self.d, "/pic.png", b"\x89PNG a picture")
        thumb = "/api/thumb?" + urllib.parse.urlencode({"path": "/pic.png"})
        self.assertEqual(self.req("POST", thumb, body=b"\x89PNG small")[0], 200)
        self.assertEqual(self.req("GET", thumb)[2], b"\x89PNG small")
        # in the browsers: this computer takes no more of them (the ones it has are still given out)
        self.assertEqual(self.req("POST", "/api/settings", {"thumbs_store": "nowhere"})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"thumbs_store": "browser", "thumbs_bulk": False})[0], 200)
        self.assertEqual(rule(), {"store": "browser", "bulk": False})
        self.write(self.d, "/other.png", b"\x89PNG another picture")
        other = "/api/thumb?" + urllib.parse.urlencode({"path": "/other.png"})
        self.assertEqual(self.req("POST", other, body=b"\x89PNG small")[0], 409)
        self.assertEqual(self.req("GET", other)[0], 404)
        self.assertEqual(self.req("GET", thumb)[2], b"\x89PNG small")
        self.assertEqual(self.req("POST", "/api/settings", {"thumbs_store": "server"})[0], 200)
        self.assertEqual(self.req("POST", other, body=b"\x89PNG small")[0], 200)

    def test_two_folders_are_merged(self):
        self.login()
        d = self.d
        for folder in ("/A", "/A/sub", "/B", "/B/sub", "/B/only-b"):
            d.fs.mkdir(folder, 0o755)
        self.write(d, "/A/sub/one.txt", b"one")
        self.write(d, "/A/note.txt", b"from A")
        self.write(d, "/B/sub/two.txt", b"two")
        self.write(d, "/B/note.txt", b"from B, longer")
        self.assertEqual(self.req("POST", "/api/merge", {"from": "/A", "into": "/A/sub"})[0], 400)       # (not into itself)
        status, _, body = self.req("POST", "/api/merge", {"from": "/B", "into": "/A"})
        self.assertEqual(status, 200, body)
        got = json.loads(body)
        self.assertEqual((got["moved"], got["merged"], got["renamed"]), (2, 1, 1))     # only-b and two.txt; sub; note.txt
        names = lambda path: sorted(c["name"] for c in d.index.children(d.index.resolve(path)["id"]))
        self.assertEqual(names("/A"), ["note (2).txt", "note.txt", "only-b", "sub"])
        self.assertEqual(names("/A/sub"), ["one.txt", "two.txt"])
        self.assertIsNone(d.index.resolve("/B"))
        self.assertEqual(d.fs.read_file(d.index.resolve("/A/note (2).txt")["id"], 0, 100), b"from B, longer")

    def test_the_status_says_when_files_changed(self):
        """Pages that list files look again when `rev` went up, instead of fetching their lists on a timer."""
        self.login()
        rev = lambda: json.loads(self.req("GET", "/api/status")[2])["rev"]
        first = rev()
        self.assertEqual(rev(), first)                                  # nothing happened: the same
        self.assertEqual(self.req("POST", "/api/mkdir", {"path": "/New"})[0], 200)
        second = rev()
        self.assertGreater(second, first)
        self.assertEqual(self.req("POST", "/api/move", {"from": "/New", "to": "/Renamed"})[0], 200)
        third = rev()
        self.assertGreater(third, second)                               # a rename changes no count and no size: this does
        self.assertEqual(self.req("POST", "/api/delete", {"path": "/Renamed"})[0], 200)
        self.assertGreater(rev(), third)

    def test_lockout(self):
        for _ in range(5):
            self.assertEqual(self.login(STRANGER)[0], 401)
        status, body = self.login(STRANGER)                             # more wrong ones aren't even looked at now
        self.assertEqual(status, 429)
        self.assertIn(b"Too many", body)
        auth = self.form(auth="ab" * 32)
        self.assertEqual(self.req("POST", "/login", auth, auth=False)[0], 429)
        self.assertEqual(self.req("POST", "/login", self.form(phrase="not words"), auth=False)[0], 429)
        # the right phrase still gets in: somebody else's wrong tries from the same address (or a device
        # that still has the words of an account that is gone) keep nobody out
        self.assertEqual(self.login()[0], 303)
        self.assertEqual(self.req("GET", "/api/status")[0], 200)
        self.assertEqual(self.login(STRANGER)[0], 401)                  # and with it the count starts again

    def test_logout(self):
        self.login()
        _, headers, _ = self.req("POST", "/api/logout", {})
        self.assertIn("Max-Age=0", headers["Set-Cookie"])

    def test_nothing_is_set_up_without_the_phrase(self):
        self.web.cfg.web_user = self.web.cfg.web_password = ""          # a new drive: nothing to choose, the phrase signs in
        status, _, body = self.req("GET", "/")
        self.assertIn(b'action="/login"', body)
        self.assertIn(b"recovery-phrase", body)                         # on the computer itself: where to find the words
        self.assertNotIn(b"/signup", body)                              # accounts are closed unless the owner opens them
        self.assertEqual(self.req("POST", "/setup", self.form(user="me", password="long enough", password2="long enough"),
                                  auth=False)[0], 401)
        self.assertEqual(self.login()[0], 303)
        self.assertEqual(json.loads(self.req("GET", "/api/status")[2])["user"], "Owner")

    def test_zip_copy_and_purge(self):
        self.login()
        self.write(self.d, "/Album/a.txt", b"first file")
        self.write(self.d, "/Album/sub/b.txt", b"second file")
        self.upload(self.d)
        status, _, body = self.req("GET", "/api/zip?path=%2FAlbum")
        self.assertEqual(status, 200)
        z = zipfile.ZipFile(io.BytesIO(body))
        self.assertEqual(z.read("a.txt"), b"first file")
        self.assertEqual(z.read("sub/b.txt"), b"second file")

        self.assertEqual(self.req("POST", "/api/copy", {"from": "/Album/a.txt", "to": "/Album/a copy.txt"})[0], 200)
        self.assertEqual(self.req("GET", "/api/file?path=%2FAlbum%2Fa%20copy.txt")[2], b"first file")
        self.assertEqual(self.req("POST", "/api/move", {"from": "/Album", "to": "/Album/sub/Album"})[0], 400)
        before = self.d.backend.uploads
        self.upload(self.d)
        self.assertEqual(self.d.backend.uploads, before)               # the duplicate reused every piece

        self.assertEqual(self.req("POST", "/api/delete", {"path": "/Album"})[0], 200)
        deleted = json.loads(self.req("GET", "/api/deleted")[2])["items"]
        self.assertEqual(len(deleted), 3)
        uids = [it["uid"] for it in deleted if it["path"] != "/Album/a.txt"]
        r = json.loads(self.req("POST", "/api/purge", {"uids": uids})[2])
        self.assertEqual(r["count"], 2)
        left = json.loads(self.req("GET", "/api/deleted")[2])["items"]
        self.assertEqual([it["path"] for it in left], ["/Album/a.txt"])
        r = json.loads(self.req("POST", "/api/undelete", {"uids": [left[0]["uid"]]})[2])
        self.assertEqual(r["count"], 1)
        self.d.journal.sync_once()
        self.assertEqual(self.read(self.d, "/Album/a.txt"), b"first file")

    def test_post_needs_header(self):
        self.login()
        self.assertEqual(self.req("POST", "/api/mkdir", {"path": "/x"}, headers={"X-DD": ""})[0], 403)

    def test_foreign_host_refused(self):
        self.login()
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "evil.example"})[0], 421)

    def test_files(self):
        self.login()
        self.assertEqual(self.req("POST", "/api/mkdir", {"path": "/Music"})[0], 200)
        data = os.urandom(300_000)
        status, _, body = self.req("POST", "/api/upload?" + urllib.parse.urlencode({"path": "/Music/song.mp3"}), data)
        self.assertEqual(status, 200, body)
        listing = json.loads(self.req("GET", "/api/list?path=%2FMusic")[2])
        self.assertEqual([i["name"] for i in listing["items"]], ["song.mp3"])

        status, headers, body = self.req("GET", "/api/file?path=%2FMusic%2Fsong.mp3", headers={"Range": "bytes=100-199"})
        self.assertEqual(status, 206)
        self.assertEqual(body, data[100:200])
        self.assertEqual(headers["Content-Range"], f"bytes 100-199/{len(data)}")

        # after upload to "Discord", reads come from the pieces
        self.upload(self.d)
        status, _, body = self.req("GET", "/api/file?path=%2FMusic%2Fsong.mp3")
        self.assertEqual(body, data)

        found = json.loads(self.req("GET", "/api/search?q=son")[2])["items"]
        self.assertEqual(found[0]["path"], "/Music/song.mp3")

        self.assertEqual(self.req("POST", "/api/move", {"from": "/Music/song.mp3", "to": "/Music/tune.mp3"})[0], 200)
        self.assertIsNotNone(self.d.index.resolve("/Music/tune.mp3"))
        self.assertEqual(self.req("POST", "/api/delete", {"path": "/Music"})[0], 200)
        self.assertIsNone(self.d.index.resolve("/Music"))

    def test_share_link(self):
        self.login()
        self.write(self.d, "/a.txt", b"shared text")
        self.upload(self.d)
        share = json.loads(self.req("POST", "/api/share", {"path": "/a.txt", "hours": 1})[2])
        status, _, body = self.req("GET", share["path"], auth=False)
        self.assertEqual(status, 200)
        self.assertIn(b"a.txt", body)
        status, _, body = self.req("GET", share["path"] + "/raw", auth=False)
        self.assertEqual(body, b"shared text")
        tampered = share["path"][:-3] + ("AAA" if not share["path"].endswith("AAA") else "BBB")
        self.assertEqual(self.req("GET", tampered, auth=False)[0], 404)

    def test_share_options(self):
        self.login()
        self.write(self.d, "/Trip/photo.txt", b"a view of the sea")
        self.write(self.d, "/Trip/day 2/notes.txt", b"second day")
        self.upload(self.d)
        # a password-protected, view-only link to a folder
        link = json.loads(self.req("POST", "/api/share", {"path": "/Trip", "hours": 1, "password": "open sesame",
                                                          "download": False})[2])
        self.assertTrue(link["password"])
        status, _, body = self.req("GET", link["path"], auth=False)
        self.assertIn(b"Password needed", body)
        self.assertEqual(self.req("GET", link["path"] + "/raw?p=photo.txt", auth=False)[0], 200)   # still the form
        status, _, _ = self.req("POST", link["path"], self.form(password="wrong"), auth=False)
        self.assertEqual(status, 401)
        status, headers, _ = self.req("POST", link["path"], self.form(password="open sesame"), auth=False)
        self.assertEqual(status, 303)
        unlock = headers["Set-Cookie"].split(";")[0]
        status, _, body = self.req("GET", link["path"], headers={"Cookie": unlock}, auth=False)
        self.assertIn(b"photo.txt", body)
        self.assertIn(b"day 2", body)
        self.assertNotIn(b"Download all", body)                     # view only
        status, _, body = self.req("GET", link["path"] + "?p=photo.txt", headers={"Cookie": unlock}, auth=False)
        self.assertIn(b"a view of the sea", body)                   # text shown on the page
        self.assertEqual(self.req("GET", link["path"] + "/raw?p=photo.txt&dl=1", headers={"Cookie": unlock}, auth=False)[0], 403)
        self.assertEqual(self.req("GET", link["path"] + "?p=..", headers={"Cookie": unlock}, auth=False)[0], 404)
        # change it: downloads allowed, no password
        r = json.loads(self.req("POST", "/api/shares/update", {"id": link["id"], "download": True, "password": ""})[2])
        self.assertFalse(r["password"])
        status, _, body = self.req("GET", link["path"] + "/raw?p=day%202/notes.txt&dl=1", auth=False)
        self.assertEqual(body, b"second day")
        status, _, body = self.req("GET", link["path"] + "/zip", auth=False)
        self.assertEqual(zipfile.ZipFile(io.BytesIO(body)).read("photo.txt"), b"a view of the sea")
        listed = json.loads(self.req("GET", "/api/shares")[2])["items"]
        self.assertEqual([x["item"] for x in listed], ["/Trip"])
        self.assertGreaterEqual(listed[0]["views"], 1)
        self.assertEqual(self.req("POST", "/api/shares/revoke", {"id": link["id"]})[0], 200)
        self.assertEqual(self.req("GET", link["path"], auth=False)[0], 404)

    def test_share_link_uses_domain(self):
        self.login()
        self.write(self.d, "/x.txt", b"x")
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "drive.example.com"})[0], 421)
        self.assertEqual(self.req("POST", "/api/settings", {"web_hosts": "https://Drive.Example.com/"})[0], 200)
        self.assertEqual(self.d.cfg.web_hosts, ["drive.example.com"])
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "drive.example.com"})[0], 200)
        link = json.loads(self.req("POST", "/api/share", {"path": "/x.txt"})[2])
        self.assertTrue(link["url"].startswith("https://drive.example.com/s/"))
        self.req("POST", "/api/settings", {"web_public_url": "https://files.example.org"})
        link = json.loads(self.req("POST", "/api/share", {"path": "/x.txt"})[2])
        self.assertTrue(link["url"].startswith("https://files.example.org/s/"))
        self.assertEqual(self.req("POST", "/api/settings", {"web_public_url": "not a url"})[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"bot_token": "x"})[0], 400)
        values = json.loads(self.req("GET", "/api/settings")[2])["values"]
        self.assertEqual(values["web_public_url"], "https://files.example.org")

    def test_notes(self):
        self.login()
        self.assertEqual(json.loads(self.req("GET", "/api/notes")[2])["items"], [])
        self.req("POST", "/api/mkdir", {"path": "/Notes"})
        self.assertEqual(self.req("POST", "/api/upload?path=%2FNotes%2FShopping.md&save=later", b"Milk, eggs")[0], 200)
        items = json.loads(self.req("GET", "/api/notes")[2])["items"]
        self.assertEqual([(n["title"], n["snippet"]) for n in items], [("Shopping", "Milk, eggs")])
        nid = self.d.index.resolve("/Notes/Shopping.md")["id"]
        self.assertGreater(self.d.uploader._pending[nid], __import__("time").time() + 5)   # waits for a pause
        self.req("POST", "/api/upload?path=%2FNotes%2FShopping.md&save=now", b"Milk, eggs, bread")
        self.assertLessEqual(self.d.uploader._pending[nid], __import__("time").time() + 0.5)  # Save: right away
        self.upload(self.d)
        self.assertEqual(self.read(self.d, "/Notes/Shopping.md"), b"Milk, eggs, bread")

    def test_activity_log_and_changelog(self):
        import logging
        self.login()
        logging.getLogger("ddrive.test").warning("a line for the log tab")
        lines = json.loads(self.req("GET", "/api/log")[2])["lines"]
        self.assertTrue(any(l["m"] == "a line for the log tab" and l["l"] == "WARNING" for l in lines))
        after = lines[-1]["i"]
        self.assertEqual(json.loads(self.req("GET", f"/api/log?after={after}")[2])["lines"], [])
        # a file being uploaded shows up with progress, speed and time left
        self.write(self.d, "/big.bin", os.urandom(300_000))
        nid = self.d.index.resolve("/big.bin")["id"]
        self.d.uploader.progress[nid] = {"path": "/big.bin", "size": 300_000, "bytes": 100_000, "base": 0,
                                         "started": __import__("time").time() - 2, "done": 1, "total": 3,
                                         "stage": "uploading", "updated": 0}
        a = json.loads(self.req("GET", "/api/activity")[2])
        u = a["uploads"][0]
        self.assertAlmostEqual(u["pct"], 33.3, places=1)
        self.assertGreater(u["speed"], 0)
        self.assertGreater(u["eta"], 0)
        self.assertEqual(json.loads(self.req("GET", "/api/status")[2])["uploads"][0]["path"], "/big.bin")
        # the Queue page asks for the same, with what waits behind it and nothing about the computer
        self.write(self.d, "/next.bin", os.urandom(1000))
        qd = json.loads(self.req("GET", "/api/queue")[2])
        self.assertEqual(qd["uploads"][0]["path"], "/big.bin")
        self.assertIn("/next.bin", [w["path"] for w in qd["queue"]])
        self.assertGreaterEqual(qd["queued"], len(qd["queue"]))
        self.assertEqual((qd["downloads"], qd["offline"]), ([], []))
        self.assertNotIn("sync", qd)
        # how much more this computer can take in, for a page that has several files to send: what is free less what is always kept free
        self.d.cfg.min_free_disk_bytes, kept = 1024, self.d.cfg.min_free_disk_bytes
        free, self.d.fs._free_disk = self.d.fs._free_disk, lambda: 5000
        try:
            self.assertEqual(json.loads(self.req("GET", "/api/queue")[2])["room"], 3976)
            self.d.fs._free_disk = lambda: 500
            self.assertEqual(json.loads(self.req("GET", "/api/queue")[2])["room"], 0)
            self.d.cfg.min_free_disk_bytes = 0
            self.assertIsNone(json.loads(self.req("GET", "/api/queue")[2])["room"])
        finally:
            self.d.cfg.min_free_disk_bytes, self.d.fs._free_disk = kept, free
        page = self.req("GET", "/app.js")[2].decode()
        for needed in ("async function renderQueue(", "queue: renderQueue", "function track("):
            self.assertIn(needed, page)
        self.assertIn(b'data-nav="queue"', self.req("GET", "/")[2])
        log = json.loads(self.req("GET", "/api/changelog")[2])
        self.assertIn(log["version"], log["text"])

    def test_check_and_remove_unreadable(self):
        import time as _t
        self.login()
        self.write(self.d, "/Box/good.bin", os.urandom(200_000))
        self.write(self.d, "/Box/bad.bin", os.urandom(200_000))
        self.d.cfg.parity_enabled = False
        self.upload(self.d)
        for c in self.d.index.get_chunks(self.d.index.resolve("/Box/bad.bin")["id"]):
            self.d.backend.delete(c["message_id"])
        self.assertEqual(self.req("POST", "/api/check/start", {"path": "/Box"})[0], 200)
        for _ in range(100):
            st = json.loads(self.req("GET", "/api/check")[2])
            if not st["running"]:
                break
            _t.sleep(0.1)
        self.assertEqual(st["checked"], 2)
        self.assertEqual([b["path"] for b in st["bad"]], ["/Box/bad.bin"])
        self.assertEqual(st["bad"][0]["kind"], "missing")
        r = json.loads(self.req("POST", "/api/check/remove", {"uids": [st["bad"][0]["uid"]]})[2])
        self.assertEqual(r["removed"], 1)
        self.assertIsNone(self.d.index.resolve("/Box/bad.bin"))
        self.assertIsNotNone(self.d.index.resolve("/Box/good.bin"))

    def test_contacts(self):
        self.login()
        self.assertEqual(json.loads(self.req("GET", "/api/contacts")[2])["items"], [])
        vcf = "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", "FN:Ada Lovelace", "N:Lovelace;Ada;;;",
                           "TEL;TYPE=CELL:+44 7700 900000", "END:VCARD", "BEGIN:VCARD", "VERSION:3.0",
                           "FN:Grace Hopper", "EMAIL:grace@example.mil", "END:VCARD", ""])
        r = json.loads(self.req("POST", "/api/contacts/import?name=phone.vcf", vcf.encode())[2])
        self.assertEqual((r["added"], r["skipped"]), (2, 0))
        r = json.loads(self.req("POST", "/api/contacts/import?name=phone.vcf", vcf.encode())[2])
        self.assertEqual((r["added"], r["skipped"]), (0, 2))                       # duplicates skipped
        items = json.loads(self.req("GET", "/api/contacts")[2])["items"]
        self.assertEqual([c["name"] for c in items], ["Ada Lovelace", "Grace Hopper"])
        ada = items[0]
        ada["title"] = "Programmer"
        ada["phones"].append({"label": "work", "value": "+44 20 7946 0000"})
        self.assertEqual(self.req("POST", "/api/contacts/save", {"contact": ada})[0], 200)
        r = json.loads(self.req("POST", "/api/contacts/save", {"contact": {"first": "Alan", "last": "Turing"}})[2])
        self.assertEqual(r["contact"]["name"], "Alan Turing")
        self.assertEqual(self.req("POST", "/api/contacts/save", {"contact": {}})[0], 400)
        items = json.loads(self.req("GET", "/api/contacts")[2])["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(next(c for c in items if c["name"] == "Ada Lovelace")["title"], "Programmer")
        _, headers, body = self.req("GET", "/api/contacts/export?uids=" + ada["uid"])
        self.assertIn("text/vcard", headers["Content-Type"])
        self.assertEqual(body.count(b"BEGIN:VCARD"), 1)
        self.assertIn(b"TEL;TYPE=WORK:+44 20 7946 0000", body)
        self.req("POST", "/api/contacts/delete", {"uids": [ada["uid"]]})
        self.assertEqual(len(json.loads(self.req("GET", "/api/contacts")[2])["items"]), 2)
        # stored on the drive as one encrypted vCard file
        self.upload(self.d)
        self.assertIn(b"Alan Turing", self.read(self.d, "/Contacts/Contacts.vcf"))

    def test_direct_links_for_embedding(self):
        self.login()
        png = bytes([0x89]) + b"PNG fake image bytes" * 50
        self.write(self.d, "/Pics/cat.png", png)
        self.write(self.d, "/Pics/clip.mp4", os.urandom(50_000))
        self.upload(self.d)
        link = json.loads(self.req("POST", "/api/share", {"path": "/Pics/cat.png"})[2])
        self.assertEqual(link["direct_path"], link["path"] + "/cat.png")
        status, headers, body = self.req("GET", link["direct_path"], auth=False)
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", png))
        self.assertIn("public", headers["Cache-Control"])
        self.assertIn("inline", headers["Content-Disposition"])
        status, headers, body = self.req("HEAD", link["direct_path"], auth=False)
        self.assertEqual((status, body), (200, b""))
        status, headers, body = self.req("GET", link["direct_path"], headers={"Range": "bytes=0-9"}, auth=False)
        self.assertEqual((status, body), (206, png[:10]))
        # the page carries preview tags pointing at the direct file
        _, _, page = self.req("GET", link["path"], headers={"X-Forwarded-Proto": "https", "Host": "127.0.0.1"}, auth=False)
        self.assertIn(b"property='og:image' content='https://127.0.0.1" + link["direct_path"].encode(), page)
        video = json.loads(self.req("POST", "/api/share", {"path": "/Pics/clip.mp4"})[2])
        _, _, page = self.req("GET", video["path"], auth=False)
        self.assertIn(b"og:video:type' content='video/mp4'", page)
        # a whole folder: /s/<id>/<path inside it>
        folder = json.loads(self.req("POST", "/api/share", {"path": "/Pics"})[2])
        self.assertEqual(folder["direct_path"], "")
        self.assertEqual(self.req("GET", folder["path"] + "/cat.png", auth=False)[2], png)
        self.assertEqual(self.req("GET", folder["path"] + "/..%2Fsecret", auth=False)[0], 404)
        # with a password there is no direct link (Discord couldn't type it)
        locked = json.loads(self.req("POST", "/api/share", {"path": "/Pics/cat.png", "password": "pw"})[2])
        self.assertEqual(locked["direct_path"], "")
        self.assertNotEqual(self.req("GET", locked["path"] + "/cat.png", auth=False)[2], png)

    def test_links_work_on_every_device(self):
        """A link made on the PC is served by the Pi (reachable from the internet), and vice versa."""
        import http.client as hc
        self.login()
        self.write(self.d, "/Pics/sunset.png", b"pixels" * 1000)
        self.upload(self.d)
        link = json.loads(self.req("POST", "/api/share", {"path": "/Pics/sunset.png", "hours": 24})[2])
        pi = self.drive("pi01", web_token="pi-token")
        self.sync(self.d, pi)
        pi_web = pi.start_web(port=0)
        try:
            def get(path):
                c = hc.HTTPConnection("127.0.0.1", pi_web.port, timeout=10)
                c.request("GET", path)
                r = c.getresponse()
                return r.status, r.read()
            self.assertEqual(get(link["direct_path"]), (200, b"pixels" * 1000))
            self.assertEqual(get(link["path"])[0], 200)
            self.sync(pi, self.d)
            self.assertGreaterEqual(self.d.index.share_views_total(link["id"]), 1)   # views counted on the Pi
            # turning it off on the PC turns it off on the Pi
            self.req("POST", "/api/shares/revoke", {"id": link["id"]})
            self.sync(self.d, pi)
            self.assertEqual(get(link["direct_path"])[0], 404)
        finally:
            pi_web.stop()

    def test_old_links_are_published(self):
        from ddrive.shares import Shares
        self.d.index.kv_set("shares", json.dumps({"oldlink": {"u": "x", "d": False, "c": 1, "e": None, "pw": "",
                                                               "dl": True, "v": 3}}))
        sh = Shares(self.d.index, "aaaa")
        self.assertIn("oldlink", sh.all())
        self.assertEqual(sh.all()["oldlink"]["v"], 3)
        self.assertIsNone(self.d.index.kv_get("shares"))
        self.assertTrue(any(op["t"] == "share" for _, op in self.d.index.outbox_peek()))

    def test_html_files_are_sandboxed(self):
        self.login()
        self.write(self.d, "/page.html", b"<script>alert(1)</script>")
        _, headers, _ = self.req("GET", "/api/file?path=%2Fpage.html")
        self.assertIn("sandbox", headers.get("Content-Security-Policy", ""))

    def test_snapshots_and_versions(self):
        self.login()
        self.write(self.d, "/v.txt", b"first")
        self.upload(self.d)
        self.write(self.d, "/v.txt", b"second")
        self.upload(self.d)
        versions = json.loads(self.req("GET", "/api/versions?path=%2Fv.txt")[2])["versions"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.req("POST", "/api/versions/restore", {"path": "/v.txt", "n": 1})[0], 200)
        self.d.journal.sync_once()
        self.assertEqual(self.read(self.d, "/v.txt"), b"first")

        self.assertEqual(self.req("POST", "/api/snapshots/create", {"label": "t"})[0], 200)
        snaps = json.loads(self.req("GET", "/api/snapshots")[2])["items"]
        self.assertEqual(len(snaps), 1)
        status, _, body = self.req("POST", "/api/snapshots/restore", {"id": snaps[0]["id"], "path": "/"})
        self.assertEqual(status, 200, body)
        self.d.journal.sync_once()
        folder = json.loads(body)["folder"]
        self.assertEqual(self.read(self.d, folder + "/v.txt"), b"first")

        # snapshots can be deleted for good
        self.assertEqual(self.req("POST", "/api/snapshots/create", {"label": "second"})[0], 200)
        self.assertEqual(self.req("POST", "/api/snapshots/create", {"label": "third"})[0], 200)
        snaps = json.loads(self.req("GET", "/api/snapshots")[2])["items"]
        self.assertEqual(len(snaps), 3)
        self.assertEqual(self.req("POST", "/api/snapshots/delete", {"ids": ["nope"]})[0], 404)
        status, _, body = self.req("POST", "/api/snapshots/delete", {"ids": [snaps[0]["id"]]})
        self.assertEqual((status, json.loads(body)["count"]), (200, 1))
        left = [s["id"] for s in json.loads(self.req("GET", "/api/snapshots")[2])["items"]]
        self.assertEqual(left, [snaps[1]["id"], snaps[2]["id"]])
        b = self.drive("bbbb")                                           # and they go on the other devices too
        self.sync(self.d, b)
        self.assertEqual({s["id"] for s in b.index.snapshots()}, set(left))
        self.assertEqual(json.loads(self.req("POST", "/api/snapshots/delete", {"all": True})[2])["count"], 2)
        self.sync(self.d, b)
        self.assertEqual(b.index.snapshots(), [])
        self.assertEqual(self.read(self.d, "/v.txt"), b"first")           # the files themselves are untouched

    def test_clear_the_log(self):
        self.login()
        logging.getLogger("ddrive.test").warning("something worth clearing")
        first = json.loads(self.req("GET", "/api/log")[2])
        self.assertIn("something worth clearing", " ".join(r["m"] for r in first["lines"]))
        self.assertEqual(self.req("POST", "/api/log/clear", {})[0], 200)
        after = json.loads(self.req("GET", "/api/log")[2])
        self.assertEqual(after["gen"], first["gen"] + 1)
        self.assertEqual([r["m"] for r in after["lines"]], ["The log was cleared."])
        # the file being emptied from the terminal is noticed too
        from ddrive.logbuf import BUFFER, clear_file
        path = os.path.join(self.tmp, "drive.log")
        with open(path, "w") as f:
            f.write("12:00:00 [INFO] ddrive.x: a line\n" * 50)
        BUFFER._file_size = None
        BUFFER.follow_file(path)
        self.assertEqual(BUFFER.gen, after["gen"])
        self.assertTrue(clear_file(path))
        self.assertEqual(os.path.getsize(path), 0)
        BUFFER.follow_file(path)
        self.assertEqual(BUFFER.gen, after["gen"] + 1)

    def test_status(self):
        self.login()
        st = json.loads(self.req("GET", "/api/status")[2])
        for key in ("version", "stats", "health", "devices", "uploads"):
            self.assertIn(key, st)
        self.assertTrue(any(d["me"] for d in st["devices"]))

    def test_gallery_and_thumbnails(self):
        self.login()
        self.write(self.d, "/Photos/2025/a.jpg", b"\xff\xd8\xff photo a")
        self.write(self.d, "/Photos/b.PNG", b"\x89PNG photo b")
        self.write(self.d, "/Photos/clip.mp4", b"video")
        self.write(self.d, "/Docs/notes.txt", b"not media")
        self.write(self.d, "/Other/c.jpg", b"\xff\xd8\xff photo c")
        media = json.loads(self.req("GET", "/api/media?path=%2FPhotos")[2])
        self.assertEqual(media["total"], 3)
        self.assertEqual({i["path"] for i in media["items"]}, {"/Photos/2025/a.jpg", "/Photos/b.PNG", "/Photos/clip.mp4"})
        self.assertTrue(all(i["thumb"] is None for i in media["items"]))
        self.assertEqual(json.loads(self.req("GET", "/api/media")[2])["total"], 4)             # the whole drive
        page = json.loads(self.req("GET", "/api/media?offset=3&limit=2")[2])
        self.assertEqual((page["total"], len(page["items"])), (4, 1))

        thumb = b"RIFF\x00\x00\x00\x00WEBP small picture"
        url = "/api/thumb?path=%2FPhotos%2F2025%2Fa.jpg"
        self.assertEqual(self.req("GET", url)[0], 404)
        self.assertEqual(self.req("POST", url, body=b"<script>")[0], 400)                      # only images
        self.assertEqual(self.req("POST", url, body=thumb)[0], 200)
        status, headers, body = self.req("GET", url)
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/webp", thumb))
        stored = os.path.join(self.d.cfg.resolved_data_dir, "thumbs")
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(stored) for f in fs]
        self.assertEqual(len(files), 1)
        with open(files[0], "rb") as f:
            self.assertNotIn(b"small picture", f.read())                                       # encrypted on disk
        self.assertEqual(self.req("POST", "/api/thumb?path=%2FPhotos%2Fclip.mp4", body=b"")[0], 200)  # can't be made
        states = {i["name"]: i.get("thumb") for i in json.loads(self.req("GET", "/api/list?path=%2FPhotos")[2])["items"]}
        self.assertEqual((states["b.PNG"], states["clip.mp4"]), (None, -1))
        self.assertEqual(json.loads(self.req("GET", "/api/media?path=%2FPhotos%2F2025")[2])["items"][0]["thumb"], 1)
        self.write(self.d, "/Photos/2025/a.jpg", b"\xff\xd8\xff a different photo")           # changed: made again
        self.assertEqual(self.req("GET", url)[0], 404)

    def test_sync_folders(self):
        self.login()
        here = os.path.join(self.tmp, "Downloads")
        os.makedirs(here)
        with open(os.path.join(here, "report.pdf"), "wb") as f:
            f.write(b"%PDF report")
        os.utime(os.path.join(here, "report.pdf"), (1e9, 1e9))
        info = json.loads(self.req("GET", "/api/sync")[2])
        self.assertTrue(info["can_edit"])
        self.assertEqual(len(info["modes"]), 6)
        listing = json.loads(self.req("GET", "/api/sync/browse?" + urllib.parse.urlencode({"path": self.tmp}))[2])
        self.assertIn("Downloads", [d["name"] for d in listing["dirs"]])

        status, _, body = self.req("POST", "/api/sync/save", {"job": {"local": here, "remote": "/", "mode": "mirror"}})
        self.assertEqual(status, 400, body)                                  # mirroring onto the whole drive
        self.d.sync.start()                                                  # the scheduler: runs a new job at once
        status, _, body = self.req("POST", "/api/sync/save", {"job": {"local": here, "remote": "/Downloads", "mode": "backup"}})
        self.assertEqual(status, 200, body)
        jid = json.loads(body)["job"]["id"]
        for _ in range(100):
            if self.d.index.resolve("/Downloads/report.pdf") is not None and self.d.sync.running is None:
                break
            time.sleep(0.1)
        self.assertEqual(self.read(self.d, "/Downloads/report.pdf"), b"%PDF report")
        job = json.loads(self.req("GET", "/api/sync")[2])["jobs"][0]
        self.assertEqual((job["status"]["state"], job["status"]["last"]["up"]), ("idle", 1))

        self.assertEqual(self.req("POST", "/api/sync/pause", {"id": jid, "paused": True})[0], 200)
        self.assertFalse(self.d.cfg.sync_jobs[0]["enabled"])
        self.assertEqual(self.req("POST", "/api/sync/run", {"id": jid})[0], 200)

        # From another device (through a domain name) folders here can't be chosen, unless allowed here.
        self.d.cfg.web_hosts = ["drive.example.com"]
        remote = {"Host": "drive.example.com"}
        self.assertFalse(json.loads(self.req("GET", "/api/sync", headers=remote)[2])["can_edit"])
        self.assertEqual(self.req("GET", "/api/sync/browse?path=%2F", headers=remote)[0], 403)
        self.assertEqual(self.req("POST", "/api/sync/save", {"job": {"id": jid, "local": self.tmp}}, headers=remote)[0], 403)
        self.assertEqual(self.req("POST", "/api/sync/remote-edit", {"on": True}, headers=remote)[0], 403)
        self.assertEqual(self.req("POST", "/api/sync/pause", {"id": jid, "paused": False}, headers=remote)[0], 200)
        self.assertEqual(self.req("POST", "/api/sync/remote-edit", {"on": True})[0], 200)
        self.assertEqual(self.req("POST", "/api/sync/remove", {"id": jid}, headers=remote)[0], 200)
        self.assertEqual(self.d.cfg.sync_jobs, [])
        self.assertIsNotNone(self.d.index.resolve("/Downloads/report.pdf"))  # removing a pair deletes nothing


    # ------------------------------------------------------------ password-locked folders
    def post(self, path, body):
        status, _, data = self.req("POST", path, body)
        return status, json.loads(data or b"{}")

    def listing(self, path="/"):
        status, _, data = self.req("GET", "/api/list?path=" + path)
        return status, [i["name"] for i in json.loads(data).get("items", [])]

    def test_locked_folders(self):
        self.login()
        self.write(self.d, "/Private/diary.txt", b"dear diary")
        self.write(self.d, "/Private/pic.jpg", b"\xff\xd8\xff")
        self.write(self.d, "/Public/readme.txt", b"hello")
        self.upload(self.d)
        self.assertEqual(self.post("/api/locks/add", {"path": "/Private", "password": "abc"})[0], 400)   # too short
        self.assertEqual(self.post("/api/locks/add", {"path": "/Private", "password": "hunter2"})[0], 200)
        self.assertEqual(self.listing()[1], ["Private", "Public"])       # still open where it was locked
        self.assertEqual(self.post("/api/locks/lock", {})[0], 200)

        # locked: not in any list, and its files can't be fetched
        self.assertEqual(self.listing()[1], ["Public"])
        self.assertEqual(self.listing("%2FPrivate")[0], 404)
        self.assertEqual(self.req("GET", "/api/file?path=%2FPrivate%2Fdiary.txt")[0], 404)
        self.assertEqual(json.loads(self.req("GET", "/api/search?q=diary")[2])["items"], [])
        self.assertEqual(json.loads(self.req("GET", "/api/media")[2])["total"], 0)
        self.assertEqual(self.req("POST", "/api/upload?path=%2FPrivate%2Fx.txt", body=b"x")[0], 404)
        status = json.loads(self.req("GET", "/api/status")[2])
        self.assertEqual(status["locks"], {"count": 1, "open": 0})
        logging.getLogger("ddrive.test").warning("Successfully uploaded /Private/diary.txt")
        logging.getLogger("ddrive.test").warning("Successfully uploaded /Public/readme.txt")
        lines = " ".join(r["m"] for r in json.loads(self.req("GET", "/api/log")[2])["lines"])
        self.assertNotIn("diary", lines)
        self.assertIn("/Public/readme.txt", lines)

        # unlock with the password: this browser only
        self.assertEqual(self.post("/api/locks/unlock", {"password": "wrong"})[0], 403)
        status, r = self.post("/api/locks/unlock", {"password": "hunter2"})
        self.assertEqual((status, r["opened"]), (200, 1))
        self.assertEqual(self.listing()[1], ["Private", "Public"])
        self.assertEqual(self.req("GET", "/api/file?path=%2FPrivate%2Fdiary.txt")[2], b"dear diary")
        entry = [i for i in json.loads(self.req("GET", "/api/list?path=%2F")[2])["items"] if i["name"] == "Private"][0]
        self.assertTrue(entry["locked"])
        with self.assertRaises(OSError):
            self.d.fs.getattr("/Private")                                # the drive letter stays locked
        other, self.cookie = self.cookie, None
        self.login()                                                     # another browser: still locked
        self.assertEqual(self.listing()[1], ["Public"])
        self.cookie = other

        # deleted files of a locked folder stay out of Deleted
        self.d.fs.scope.unlocked = locks.ALL
        self.d.fs.unlink("/Private/pic.jpg")
        self.d.fs.scope.unlocked = None
        self.assertEqual(len(json.loads(self.req("GET", "/api/deleted")[2])["items"]), 1)
        self.post("/api/locks/lock", {})
        self.assertEqual(json.loads(self.req("GET", "/api/deleted")[2])["items"], [])

        # "also on the drive", then removing the lock
        self.post("/api/locks/unlock", {"password": "hunter2", "drive": True})
        self.assertIsNotNone(self.d.fs.getattr("/Private/diary.txt"))
        self.assertEqual(self.post("/api/locks/remove", {"path": "/Private", "password": "nope"})[0], 403)
        self.assertEqual(self.post("/api/locks/remove", {"path": "/Private", "password": "hunter2"})[0], 200)
        self.assertEqual(json.loads(self.req("GET", "/api/status")[2])["locks"]["count"], 0)

    def test_locked_folder_guessing_is_slowed_down(self):
        self.login()
        self.write(self.d, "/Private/a.txt", b"a")
        self.post("/api/locks/add", {"path": "/Private", "password": "hunter2"})
        self.post("/api/locks/lock", {})
        for _ in range(5):
            self.assertEqual(self.post("/api/locks/unlock", {"password": "guess"})[0], 403)
        self.assertEqual(self.post("/api/locks/unlock", {"password": "hunter2"})[0], 429)


    # ------------------------------------------------------------ 1.0: home, stars, activity, insights, requests, WebDAV
    def get(self, path):
        status, _, data = self.req("GET", path)
        self.assertEqual(status, 200, data[:300])
        return json.loads(data)

    def test_stars_recent_and_home(self):
        self.login()
        self.write(self.d, "/Docs/plan.txt", b"the plan")
        self.write(self.d, "/Docs/old.txt", b"older")
        self.write(self.d, "/Pics/cat.jpg", b"\xff\xd8\xff cat")
        self.upload(self.d)
        self.assertEqual(self.post("/api/star", {"path": "/Docs/plan.txt", "on": True})[0], 200)
        self.assertEqual(self.post("/api/star", {"path": "/Pics", "on": True})[0], 200)
        self.assertEqual({i["path"] for i in self.get("/api/starred")["items"]}, {"/Docs/plan.txt", "/Pics"})
        docs = {i["name"]: i for i in self.get("/api/list?path=%2FDocs")["items"]}
        self.assertTrue(docs["plan.txt"].get("starred"))
        self.assertFalse(docs["old.txt"].get("starred"))
        root = {i["name"]: i for i in self.get("/api/list?path=%2F")["items"]}
        self.web._tree_cache = None
        root = {i["name"]: i for i in self.get("/api/list?path=%2F")["items"]}
        self.assertEqual((root["Docs"]["size"], root["Docs"]["files"]), (13, 2))             # folder sizes
        self.assertEqual(len(self.get("/api/recent?limit=2")["items"]), 2)
        home = self.get("/api/home")
        self.assertEqual((home["stats"]["files"], len(home["starred"]), len(home["recent"])), (3, 2, 3))
        b = self.drive("bbbb")                                                               # stars reach other devices
        self.sync(self.d, b)
        self.assertEqual(len(b.index.stars_all()), 2)
        self.post("/api/star", {"path": "/Pics", "on": False})
        self.sync(self.d, b)
        self.assertEqual(len(b.index.stars_all()), 1)
        self.post("/api/locks/add", {"path": "/Docs", "password": "hunter2"})                # locked: not in any of them
        self.post("/api/locks/lock", {})
        self.assertEqual(self.get("/api/starred")["items"], [])
        self.assertEqual([i["path"] for i in self.get("/api/recent")["items"]], ["/Pics/cat.jpg"])

    def test_activity(self):
        self.login()
        b = self.drive("bbbb")
        self.write(self.d, "/Docs/a.txt", b"one")
        self.upload(self.d)
        self.write(self.d, "/Docs/a.txt", b"two!")
        self.upload(self.d)
        self.d.fs.rename("/Docs/a.txt", "/Docs/b.txt")
        self.sync(self.d, b)
        self.write(b, "/Docs/from-b.txt", b"hello from b")
        self.upload(b)
        b.fs.unlink("/Docs/b.txt")
        self.sync(b, self.d)
        items = self.get("/api/history")["items"]
        seen = [(i["kind"], i["path"], i["me"]) for i in reversed(items)]
        self.assertEqual(seen, [("mkdir", "/Docs", True), ("add", "/Docs/a.txt", True), ("edit", "/Docs/a.txt", True),
                                ("move", "/Docs/b.txt", True), ("add", "/Docs/from-b.txt", False),
                                ("delete", "/Docs/b.txt", False)])
        self.assertEqual(items[2]["src"], "/Docs/a.txt")
        other = [(i["kind"], i["path"]) for i in b.index.activity() if i["device"] == "aaaa"]
        self.assertIn(("add", "/Docs/a.txt"), other)                                         # b saw what a did
        older = self.get(f"/api/history?limit=2&before={items[1]['id']}")["items"]
        self.assertEqual([i["id"] for i in older], [items[2]["id"], items[3]["id"]])

    def test_storage_insights_and_zip_of_a_selection(self):
        self.login()
        same = os.urandom(5000)
        self.write(self.d, "/A/one.bin", same)
        self.write(self.d, "/B/copy.bin", same)
        self.write(self.d, "/B/song.mp3", b"m" * 300)
        self.write(self.d, "/big.mp4", b"v" * 9000)
        self.upload(self.d)
        ins = self.get("/api/insights")
        self.assertEqual(ins["total"], {"files": 4, "bytes": 19300})
        self.assertEqual(ins["kinds"][0]["kind"], "file")
        self.assertEqual(ins["largest"][0]["path"], "/big.mp4")
        self.assertEqual([(f["path"], f["bytes"]) for f in ins["folders"]], [("/B", 5300), ("/A", 5000)])
        self.assertEqual(len(ins["duplicates"]), 1)
        self.assertEqual({f["path"] for f in ins["duplicates"][0]["files"]}, {"/A/one.bin", "/B/copy.bin"})
        self.assertEqual(ins["duplicate_bytes"], 5000)
        status, _, data = self.req("GET", "/api/zip?paths=" + urllib.parse.quote(json.dumps(["/B", "/big.mp4"])))
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(sorted(zf.namelist()), ["B/", "B/copy.bin", "B/song.mp3", "big.mp4"])
            self.assertEqual(zf.read("B/copy.bin"), same)

    def test_file_request(self):
        self.login()
        self.write(self.d, "/Inbox/already-here.txt", b"private")
        self.assertEqual(self.post("/api/share", {"path": "/Inbox/already-here.txt", "upload": True})[0], 400)
        status, link = self.post("/api/share", {"path": "/Inbox", "upload": True, "max_mb": 1, "hours": 24})
        self.assertEqual((status, link["upload"]), (200, True))
        self.cookie = None                                                                    # a visitor
        status, _, page = self.req("GET", link["path"], auth=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Send files to", page)
        self.assertNotIn(b"already-here", page)                                               # sees nothing of the folder
        self.assertEqual(self.req("GET", link["path"] + "/raw?p=already-here.txt", auth=False)[0], 404)
        self.assertEqual(self.req("GET", link["path"] + "/zip", auth=False)[0], 404)
        up = link["path"] + "/upload?name="
        status, _, data = self.req("POST", up + "holiday.jpg", body=b"JPEGDATA", headers={"X-DD": ""}, auth=False)
        self.assertEqual((status, json.loads(data)["name"]), (200, "holiday.jpg"))
        status, _, data = self.req("POST", up + "holiday.jpg", body=b"another", auth=False)
        self.assertEqual(json.loads(data)["name"], "holiday (2).jpg")                         # never overwrites
        status, _, data = self.req("POST", up + urllib.parse.quote("../../evil/..\\x.txt"), body=b"x", auth=False)
        self.assertEqual(json.loads(data)["name"], "x.txt")                                   # stays inside the folder
        try:
            self.assertEqual(self.req("POST", up + "huge.bin", body=b"x" * (1024 * 1024 + 1), auth=False)[0], 413)
        except (BrokenPipeError, ConnectionResetError):
            pass                         # refused before all of it was sent: the server had already hung up
        self.assertEqual(self.read(self.d, "/Inbox/holiday.jpg", cold=False), b"JPEGDATA")
        self.assertEqual(sorted(n for n, _, _ in self.d.fs.readdir("/Inbox", None) if n[0] != "."),
                         ["already-here.txt", "holiday (2).jpg", "holiday.jpg", "x.txt"])
        self.assertIsNone(self.d.index.resolve("/evil"))
        # an ordinary share link takes no files
        self.login()
        _, plain = self.post("/api/share", {"path": "/Inbox"})
        self.assertEqual(self.req("POST", plain["path"] + "/upload?name=a.txt", body=b"x", auth=False)[0], 404)

    def test_save_from_a_web_address(self):
        import http.server
        import threading

        class Site(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"downloaded content" * 100
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", 'attachment; filename="report final.pdf"')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        site = http.server.HTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        try:
            self.login()
            url = f"http://127.0.0.1:{site.server_address[1]}/get?id=7"
            status, r = self.post("/api/fetch", {"url": url, "path": "/"})
            self.assertEqual(status, 400)                                                     # this computer: refused
            self.assertEqual(self.post("/api/fetch", {"url": "file:///etc/passwd", "path": "/"})[0], 400)
            self.d.cfg.fetch_private = True
            self.assertEqual(self.post("/api/fetch", {"url": url, "path": "/"})[0], 200)
            for _ in range(100):
                fetches = self.get("/api/status")["fetches"]
                if fetches and fetches[0]["state"] != "running":
                    break
                time.sleep(0.05)
            self.assertEqual((fetches[0]["state"], fetches[0]["name"]), ("done", "report final.pdf"))
            self.assertEqual(self.read(self.d, "/report final.pdf", cold=False), b"downloaded content" * 100)
        finally:
            site.shutdown()
            site.server_close()

    def dav(self, method, path, body=None, headers=None, auth="Dennis:correct horse"):
        import base64
        h = dict(headers or {})
        if auth:
            h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        conn.request(method, urllib.parse.quote(path), body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def test_webdav(self):
        self.assertEqual(self.dav("PROPFIND", "/dav/")[0], 404)                               # off unless turned on
        self.d.cfg.webdav_enabled = True
        self.assertEqual(self.dav("OPTIONS", "/dav/", auth=None)[0], 200)
        self.assertEqual(self.dav("PROPFIND", "/dav/", auth=None)[0], 401)
        self.assertEqual(self.dav("PROPFIND", "/dav/", auth="Dennis:wrong")[0], 401)
        self.assertEqual(self.dav("MKCOL", "/dav/Docs")[0], 201)
        self.assertEqual(self.dav("MKCOL", "/dav/Docs")[0], 405)
        self.assertEqual(self.dav("PUT", "/dav/Docs/my notes.txt", body=b"from another app")[0], 201)
        self.assertEqual(self.dav("PUT", "/dav/Docs/my notes.txt", body=b"changed")[0], 204)
        self.assertEqual(self.dav("PUT", "/dav/Nope/x.txt", body=b"x")[0], 409)
        self.assertEqual(self.dav("GET", "/dav/Docs/my notes.txt"), (200, b"changed"))
        status, xml = self.dav("PROPFIND", "/dav/Docs", headers={"Depth": "1"})
        self.assertEqual(status, 207)
        self.assertIn(b"<D:href>/dav/Docs/</D:href>", xml)
        self.assertIn(b"<D:href>/dav/Docs/my%20notes.txt</D:href>", xml)
        self.assertIn(b"<D:getcontentlength>7</D:getcontentlength>", xml)
        self.assertEqual(xml.count(b"<D:response>"), 2)
        self.assertEqual(self.dav("PROPFIND", "/dav/Docs", headers={"Depth": "0"})[1].count(b"<D:response>"), 1)
        dest = {"Destination": f"http://127.0.0.1:{self.web.port}/dav/Docs/renamed.txt"}
        self.assertEqual(self.dav("COPY", "/dav/Docs/my notes.txt", headers=dest)[0], 201)
        self.assertEqual(self.dav("MOVE", "/dav/Docs/my notes.txt", headers=dict(dest, Overwrite="F"))[0], 412)
        self.assertEqual(self.dav("MOVE", "/dav/Docs/my notes.txt", headers=dest)[0], 204)
        self.assertEqual(self.dav("GET", "/dav/Docs/my notes.txt")[0], 404)
        self.assertEqual(self.read(self.d, "/Docs/renamed.txt", cold=False), b"changed")
        self.assertEqual(self.dav("LOCK", "/dav/Docs/renamed.txt", body=b"<lockinfo/>")[0], 200)
        # a password-locked folder is never reachable this way
        self.d.locks.add(self.d.index.resolve("/Docs"), "hunter2")
        self.assertEqual(self.dav("GET", "/dav/Docs/renamed.txt")[0], 404)
        self.assertNotIn(b"Docs", self.dav("PROPFIND", "/dav/")[1])
        self.d.locks.remove(self.d.index.resolve("/Docs")["uid"])
        self.assertEqual(self.dav("DELETE", "/dav/Docs")[0], 204)
        self.assertIsNone(self.d.index.resolve("/Docs"))
        self.assertEqual(self.dav("DELETE", "/dav/")[0], 403)

    def test_installable(self):
        status, headers, body = self.req("GET", "/manifest.webmanifest", auth=False)
        self.assertEqual((status, headers["Content-Type"]), (200, "application/manifest+json"))
        self.assertEqual(json.loads(body)["display"], "standalone")
        self.assertEqual(self.req("GET", "/icon.png", auth=False)[0], 200)


    def test_the_page_loads_fast_and_safely(self):
        import gzip
        import re
        self.login()
        # the page's files are named with their version, kept by the browser for good, and sent compressed
        page = self.req("GET", "/")[2].decode()
        app = re.search(r'src="(/app\.js\?v=[0-9a-f]+)"', page).group(1)
        status, headers, body = self.req("GET", app, headers={"Accept-Encoding": "gzip"}, auth=False)
        self.assertEqual((status, headers["Cache-Control"], headers["Content-Encoding"]), (200, "public, max-age=31536000, immutable", "gzip"))
        self.assertIn(b"function render", gzip.decompress(body))
        self.assertEqual(self.req("GET", "/app.js?v=old", auth=False)[1]["Cache-Control"], "no-cache")     # not that version: asked again
        etag = self.req("GET", "/app.js", auth=False)[1]["ETag"]
        self.assertEqual(self.req("GET", "/app.js", headers={"If-None-Match": etag}, auth=False)[0], 304)
        self.assertEqual(self.req("GET", "/")[1]["Cache-Control"], "no-cache")
        # a big list goes compressed to a browser, and never into a cache
        self.d.index.makedirs("/big")
        for i in range(60):
            self.write(self.d, f"/big/a-rather-long-file-name-{i:03d}.txt", b"x")
        browser = {"Accept-Encoding": "gzip", "Sec-Fetch-Site": "same-origin"}
        status, headers, body = self.req("GET", "/api/list?path=/big", headers=browser)
        self.assertEqual((status, headers.get("Content-Encoding"), headers["Cache-Control"]), (200, "gzip", "no-store"))
        self.assertEqual(len(json.loads(gzip.decompress(body))["items"]), 60)
        # ...but as ever to anything else (DDrive Lite, the phone app), and for POST
        self.assertNotIn("Content-Encoding", self.req("GET", "/api/list?path=/big", headers={"Accept-Encoding": "gzip"})[1])
        self.assertEqual(len(json.loads(self.req("GET", "/api/list?path=/big")[2])["items"]), 60)

    def test_bookmarks(self):
        import http.server
        import threading
        png = b"\x89PNG\r\n\x1a\n" + b"pixels" * 50
        page = ("""<!doctype html><html><head><meta charset="utf-8"><title>Plain title</title>
            <meta property="og:title" content="How to bake bread &amp; more">
            <meta property="og:description" content="A   long guide
            to baking.">
            <meta property="og:site_name" content="Bread Weekly"><meta name="theme-color" content="#aa5500">
            <meta property="og:image" content="/img/banner.png"><link rel="icon" href="icon.png" sizes="32x32">
            </head><body><meta property="og:title" content="ignored, not in the head"></body></html>""").encode()

        class Site(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith(".png"):
                    body, ctype = png, "image/png"
                elif self.path.startswith("/bare"):
                    body, ctype = b"just text", "text/plain"
                else:
                    body, ctype = page, "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{site.server_address[1]}"
        try:
            self.login()
            self.assertEqual(self.get("/api/bookmarks")["items"], [])
            self.assertEqual(self.post("/api/bookmarks/add", {"url": "not a link"})[0], 400)
            self.assertEqual(self.post("/api/bookmarks/add", {"url": "javascript:alert(1)"})[0], 400)
            # an address on this computer is saved, but nothing is fetched from it
            status, r = self.post("/api/bookmarks/add", {"url": base + "/recipes/bread"})
            self.assertEqual((status, r["preview"], r["bookmark"]["image"]), (200, False, ""))
            self.post("/api/bookmarks/delete", {"ids": [r["bookmark"]["id"]]})
            self.d.cfg.fetch_private = True
            status, r = self.post("/api/bookmarks/add", {"url": base + "/recipes/bread", "tags": ["food"]})
            b = r["bookmark"]
            self.assertEqual((b["title"], b["description"], b["site"], b["color"], b["tags"]),
                             ("How to bake bread & more", "A long guide to baking.", "Bread Weekly", "#aa5500", ["food"]))
            self.assertEqual(self.read(self.d, b["image"], cold=False), png)                   # the banner is on the drive
            self.assertEqual(self.read(self.d, b["icon"], cold=False), png)
            self.assertTrue(self.post("/api/bookmarks/add", {"url": base + "/recipes/bread/"})[1]["existed"])
            _, bare = self.post("/api/bookmarks/add", {"url": base + "/bare.txt"})
            self.assertEqual(bare["bookmark"]["title"], "bare.txt")
            self.assertEqual([x["title"] for x in self.get("/api/bookmarks")["items"]], ["bare.txt", "How to bake bread & more"])
            status, r = self.post("/api/bookmarks/save", {"id": b["id"], "title": "Bread", "tags": "food, #Baking, food", "note": "try it"})
            self.assertEqual((r["bookmark"]["title"], r["bookmark"]["tags"], r["bookmark"]["note"]), ("Bread", ["food", "Baking"], "try it"))
            self.assertTrue(self.post("/api/bookmarks/save", {"id": b["id"], "pinned": True})[1]["bookmark"]["pinned"])
            self.assertTrue(self.get("/api/bookmarks")["items"][1]["pinned"])                  # kept in the list on the drive
            self.assertNotIn("pinned", self.post("/api/bookmarks/save", {"id": b["id"], "pinned": False})[1]["bookmark"])
            self.post("/api/bookmarks/save", {"id": b["id"], "pinned": True})
            status, r = self.post("/api/bookmarks/refresh", {"id": b["id"]})
            self.assertTrue(r["bookmark"]["pinned"])                                           # a refresh keeps the pin
            self.assertEqual((status, r["bookmark"]["title"], r["bookmark"]["site"]), (200, "Bread", "Bread Weekly"))  # your title stays
            self.assertEqual(json.loads(self.read(self.d, "/Bookmarks/Bookmarks.json", cold=False))["items"][1]["note"], "try it")
            # the preview pictures and the list are housekeeping: not in Recent, Activity or the Gallery
            self.assertEqual([i["path"] for i in self.get("/api/recent")["items"]], [])
            self.assertEqual(self.get("/api/media")["total"], 0)
            self.assertEqual(self.get("/api/history")["items"], [])
            self.assertEqual(self.get("/api/media?path=%2FBookmarks")["total"], 2)             # still there when asked for
            self.assertEqual(self.post("/api/bookmarks/delete", {"ids": [b["id"]]})[1]["removed"], 1)
            self.assertIsNone(self.d.index.resolve(b["image"]))
            self.assertEqual(len(self.get("/api/bookmarks")["items"]), 1)
            self.post("/api/locks/add", {"path": "/Bookmarks", "password": "hunter2"})
            self.post("/api/locks/lock", {})
            self.assertTrue(self.get("/api/bookmarks")["locked"])
            self.assertEqual(self.post("/api/bookmarks/add", {"url": "example.com"})[0], 404)
        finally:
            site.shutdown()
            site.server_close()


if __name__ == "__main__":
    import unittest
    unittest.main()
