"""The Discord message logger and archiver: against a made-up Discord account, with whole drives
that keep the messages (helpers.DriveTest)."""

import base64
import hashlib
import io
import json
import posixpath
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse

import helpers
from ddrive import chat, chatlog, chatstore, ws
import test_web

ME = {"id": "100", "username": "dennis", "global_name": "Dennis"}
ANNA = {"id": "200", "username": "anna", "global_name": "Anna"}


def msg(mid, channel, author, content, day=1, guild=None, files=(), edited=None):
    m = {"id": str(mid), "channel_id": str(channel), "author": author, "content": content, "type": 0,
         "timestamp": f"2024-03-{day:02d}T10:00:00.000000+00:00", "edited_timestamp": edited, "embeds": [],
         "attachments": [{"id": f"9{mid}{i}", "filename": name, "url": f"https://cdn.example/{name}?sig={time.time()}",
                          "size": 5,
                          "content_type": "image/png" if name.endswith(".png") else "text/plain"} for i, name in enumerate(files)]}
    if guild:
        m["guild_id"] = str(guild)
    return m


def history():
    return {
        "500": [msg(1, 500, ANNA, "hi there", 1), msg(2, 500, ME, "hello Anna", 2, files=["notes.txt"]),
                msg(3, 500, ANNA, "see you 100% soon", 3)],
        "301": [msg(10 + i, 301, ANNA if i % 2 else ME, f"chapter {i}", 1 + i % 20, guild=300) for i in range(230)],
    }


class FakeAccount:
    """Stands in for chat.AccountAPI: one server with two channels, and one direct conversation."""

    history = {}
    downloads = 0
    calls = 0
    gone = False                 # Discord no longer has the attachments
    refreshed = []
    downloaded = []              # every address something was fetched from

    def __init__(self, token):
        self.token = token

    def me(self):
        if self.token != "good-token":
            raise chat.AccountError(401, "401: Unauthorized")
        return ME

    def guilds(self):
        return [{"id": "300", "name": "Book: Club?", "approximate_member_count": 12}]

    def dms(self):
        return [{"id": "500", "type": 1, "last_message_id": "9", "recipients": [ANNA]}]

    def guild_channels(self, guild_id):
        if guild_id != "300":
            raise chat.AccountError(403, "Missing Access")
        return [{"id": "310", "type": 4, "name": "Reading", "position": 0},
                {"id": "301", "type": 0, "name": "general", "parent_id": "310", "position": 1},
                {"id": "302", "type": 0, "name": "secret", "parent_id": "310", "position": 2},
                {"id": "303", "type": 2, "name": "voice", "position": 3}]

    def messages(self, channel_id, before=None, limit=100, stop=None):
        type(self).calls += 1
        if channel_id in ("302", "303"):
            raise chat.AccountError(403, "Missing Access")
        rows = sorted(self.history.get(channel_id, []), key=lambda m: -int(m["id"]))
        return [m for m in rows if before is None or int(m["id"]) < int(before)][:limit]

    def download(self, url):
        type(self).downloads += 1
        self.downloaded.append(url)
        if self.gone or "ex=1&" in url:                               # removed, or a link that ran out
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        if "format=webp&width=400" in url:                            # the small preview Discord makes
            return io.BytesIO(b"\x89PNG from Discord")
        return io.BytesIO(b"hello")

    def refresh_urls(self, urls):
        type(self).refreshed += urls
        return {u: u.replace("ex=1&", "ex=7fffffff&") for u in urls}


class FakeGateway:
    last = None

    def __init__(self, token, on_dispatch, on_status, on_fatal):
        self.token, self.emit, self.on_status, self.on_fatal = token, on_dispatch, on_status, on_fatal
        FakeGateway.last = self

    def start(self):
        self.on_status("connected")
        return self

    def close(self):
        self.on_status("stopped")


class ChatTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        FakeAccount.history = history()
        FakeAccount.downloads = FakeAccount.calls = 0
        FakeAccount.gone, FakeAccount.refreshed = False, []

    def account(self, d):
        c = d.chat
        c.make_api, c.make_gateway = FakeAccount, FakeGateway
        c.connect("good-token")
        return c

    def archive(self, c, **config):
        config.setdefault("targets", [{"kind": "dm", "id": "500"}])
        c.archiver.start(config)
        c.archiver._thread.join(20)
        return c.archiver.state()

    def files(self, d, folder=chatstore.FOLDER):
        with d.chat.store._everything():
            return sorted(p for _, p in d.chat.store._walk().values() if p.startswith(folder))

    def texts(self, d, **q):
        return [m["content"] for m in d.chat.store.messages(q)["messages"]]

    # ------------------------------------------------------------ logging in
    def test_login(self):
        d = self.drive()
        c = d.chat
        c.make_api = FakeAccount
        self.assertFalse(c.state()["connected"])
        self.assertFalse(c.in_use())                                  # nothing is created until it is used
        with self.assertRaises(chatlog.ApiErrorProxy) as e:
            c.connect("bad-token")
        self.assertEqual(e.exception.status, 400)                     # not 401: that would sign the dashboard out
        with self.assertRaises(chatlog.ApiErrorProxy):
            c.logger.start()
        c.connect(' "good-token" ')
        state = c.state()
        self.assertEqual((state["connected"], state["user"]["name"], state["counts"]), (True, "Dennis", {"guilds": 1, "dms": 1}))
        self.assertEqual(d.cfg.discord_user_token, "good-token")
        self.assertNotIn("good-token", json.dumps(state))
        self.assertEqual([ch["name"] for ch in c.guild_channels("300")], ["voice", "general", "secret"])   # as Discord lists them: channels outside a category first
        c.disconnect()
        self.assertEqual((c.state()["connected"], d.cfg.discord_user_token), (False, ""))

    # ------------------------------------------------------------ archive
    def test_archive_goes_onto_the_drive(self):
        d = self.drive()
        c = self.account(d)
        state = self.archive(c, targets=[{"kind": "dm", "id": "500"}, {"kind": "guild", "id": "300"}], downloadAttachments=True)
        self.assertEqual(state["status"], "done", state)
        self.assertEqual((state["stats"]["saved"], state["stats"]["files"], state["stats"]["channelsTotal"]), (233, 1, 4))
        self.assertTrue(any("secret: skipped (no access)" in line["text"] for line in state["log"]))
        self.assertEqual(self.files(d), [
            "/Discord/Direct messages/Anna [500]/2024-03-01 to 2024-03-03.jsonl",
            "/Discord/Servers/Book_ Club_ [300]/general [301]/2024-03-01 to 2024-03-20.jsonl"])
        self.assertEqual(self.read(d, "/Discord/Direct messages/Anna [500]/files/920-notes.txt", cold=False), b"hello")
        self.upload(d)
        lines = [json.loads(x) for x in self.read(d, self.files(d)[0]).decode().splitlines()]
        self.assertEqual([(r["kind"], r["content"], r["channel"], r["mine"]) for r in lines],
                         [("message", "hi there", "Anna", 0), ("message", "hello Anna", "Anna", 1),
                          ("message", "see you 100% soon", "Anna", 0)])
        self.assertEqual(lines[1]["attachments"][0]["path"], "/Discord/Direct messages/Anna [500]/files/920-notes.txt")
        self.assertEqual(c.store.totals()["waiting"], 0)
        self.assertEqual(self.texts(d, channel="500"), ["see you 100% soon", "hello Anna", "hi there"])
        self.assertEqual(self.texts(d, q="100%"), ["see you 100% soon"])
        self.assertEqual(self.texts(d, mine="1", dm="1"), ["hello Anna"])
        self.assertEqual(c.store.messages({"guild": "300", "limit": "5"})["total"], 230)
        self.assertEqual([(p["name"], p["guild_name"], p["count"]) for p in c.store.places()],
                         [("general", "Book: Club?", 230), ("Anna", None, 3)])

        # Again: everything is read, nothing is kept twice, no file is written; a new message makes one small file.
        FakeAccount.history["500"].append(msg(4, 500, ANNA, "one more", 9))
        FakeAccount.downloads = 0
        state = self.archive(c, downloadAttachments=True)
        self.assertEqual((state["stats"]["saved"], state["stats"]["scanned"], FakeAccount.downloads), (1, 4, 0))
        self.assertEqual(self.files(d, "/Discord/Direct"), ["/Discord/Direct messages/Anna [500]/2024-03-01 to 2024-03-03.jsonl",
                                                            "/Discord/Direct messages/Anna [500]/2024-03-09.jsonl"])

    def test_archive_filters_and_resume(self):
        d = self.drive()
        c = self.account(d)

        def day(n):
            return chat.parse_ts(f"2024-03-{n:02d}T00:00:00+00:00")
        state = self.archive(c, scope="others", after=day(2), before=day(4))
        self.assertEqual((state["stats"]["saved"], self.texts(d)), (1, ["see you 100% soon"]))
        with self.assertRaises(chatlog.ApiErrorProxy):
            c.archiver.start({"targets": []})
        c.archiver.clear()
        self.assertIsNone(c.archiver.state())

        # Stopped in the middle of a channel: what was read is written, and resuming reads only the rest.
        real = FakeAccount.messages

        def stopping(api, channel_id, before=None, limit=100, stop=None):
            if before is not None:
                c.archiver._stop.set()
                raise InterruptedError("stopped")
            return real(api, channel_id, before, limit, stop)
        FakeAccount.messages = stopping
        try:
            state = self.archive(c, targets=[{"kind": "guild", "id": "300", "channelIds": ["301"]}])
        finally:
            FakeAccount.messages = real
        self.assertEqual((state["status"], state["stats"]["saved"], c.store.totals()["waiting"]), ("stopped", 100, 0))
        FakeAccount.calls = 0
        c.archiver.resume()
        c.archiver._thread.join(20)
        state = c.archiver.state()
        self.assertEqual((state["status"], state["stats"]["saved"], FakeAccount.calls), ("done", 230, 2))
        self.assertEqual(c.store.messages({"guild": "300"})["total"], 230)
        self.assertEqual(len(self.files(d, "/Discord/Servers")), 2)

    def test_archive_carries_on_after_a_restart(self):
        d = self.drive()
        c = self.account(d)
        real = FakeAccount.messages

        def closing(api, channel_id, before=None, limit=100, stop=None):
            if before is not None:                                    # the drive is stopped while the archive runs
                c.archiver.stop(wait=0, closing=True)
                raise InterruptedError("stopped")
            return real(api, channel_id, before, limit, stop)
        FakeAccount.messages = closing
        try:
            state = self.archive(c, targets=[{"kind": "guild", "id": "300", "channelIds": ["301"]}])
        finally:
            FakeAccount.messages = real
        self.assertEqual((state["status"], state["running"], c.store.totals()["waiting"]), ("running", False, 0))
        c.archiver = chatlog.Archiver(c)                              # what a newly started drive finds
        self.assertEqual(c.archiver.job["status"], "running")
        c.archiver.resume()
        c.archiver._thread.join(20)
        self.assertEqual((c.archiver.state()["status"], c.store.messages({})["total"]), ("done", 230))

    # ------------------------------------------------------------ logger
    def test_logger(self):
        d = self.drive()
        c = self.account(d)
        c.logger.set_rules({"keywords": "Dennis, pizza"})
        c.logger.start()
        gw = FakeGateway.last
        self.assertEqual((c.state()["logger"]["status"], gw.token), ("connected", "good-token"))
        gw.emit("READY", {"users": [ANNA], "guilds": [{"id": "300", "properties": {"name": "Book Club"},
                                                        "channels": [{"id": "301", "name": "general", "type": 0}]}],
                          "private_channels": [{"id": "500", "type": 1, "recipient_ids": ["200"]}]})
        gw.emit("MESSAGE_CREATE", msg(21, 500, ANNA, "pizza tonight?", 5))
        gw.emit("MESSAGE_CREATE", msg(22, 301, ME, "first draft", 5, guild=300))
        gw.emit("MESSAGE_CREATE", msg(23, 500, ANNA, "never mind", 5))
        gw.emit("MESSAGE_UPDATE", {"id": "22", "channel_id": "301", "embeds": [{}]})          # only a preview: not an edit
        gw.emit("MESSAGE_UPDATE", {"id": "22", "channel_id": "301", "content": "final text",
                                   "edited_timestamp": "2024-03-05T11:00:00+00:00"})
        gw.emit("MESSAGE_UPDATE", {"id": "99", "channel_id": "301", "content": "never seen"})  # not kept: nothing to compare
        gw.emit("MESSAGE_DELETE", {"id": "23", "channel_id": "500"})
        gw.emit("MESSAGE_DELETE", {"id": "98", "channel_id": "500"})
        self.assertEqual(c.state()["logger"]["captured"], 3)
        got = {m["id"]: m for m in c.store.messages({"order": "touched"})["messages"]}
        self.assertEqual(sorted(got), ["21", "22", "23"])
        self.assertEqual((got["21"]["flagged"], got["21"]["channel_name"], got["22"]["guild_name"]), (1, "Anna", "Book Club"))
        self.assertEqual((got["22"]["content"], got["22"]["edits"][0]["old_content"]), ("final text", "first draft"))
        self.assertTrue(got["23"]["deleted_ts"] and got["23"]["content"] == "never mind")     # the text of a deleted message stays
        self.assertEqual(self.texts(d, deleted="1"), ["never mind"])
        self.assertEqual(self.texts(d, edited="1"), ["final text"])
        self.assertEqual(self.texts(d, flagged="1"), ["pizza tonight?"])
        self.assertFalse(got["21"]["on_drive"])

        self.assertEqual(self.files(d), [])
        self.assertEqual(c.flush(), 5)                                # 3 messages, 1 edit, 1 deletion
        self.assertEqual((c.flush(), c.store.totals()["waiting"]), (0, 0))
        live = self.files(d)
        self.assertEqual(len(live), 1)
        self.assertRegex(live[0], r"^/Discord/Live/\d{4}-\d\d/\d{4}-\d\d-\d\d \d\d\.\d\d\.\d\d aaaa\.jsonl$")
        kinds = [json.loads(x)["kind"] for x in self.read(d, live[0], cold=False).decode().splitlines()]
        self.assertEqual(kinds, ["message", "message", "message", "edit", "delete"])

        # Only what the rules choose is recorded.
        c.logger.set_rules({"scope": "custom", "allDms": False, "captureMine": False,
                            "selection": {"dms": {}, "guilds": {"300": {"301": True}}}})
        gw.emit("MESSAGE_CREATE", msg(31, 500, ANNA, "a DM", 6))
        gw.emit("MESSAGE_CREATE", msg(32, 301, ME, "mine", 6, guild=300))
        gw.emit("MESSAGE_CREATE", msg(33, 301, ANNA, "kept", 6, guild=300))
        gw.emit("MESSAGE_CREATE", msg(34, 302, ANNA, "other channel", 6, guild=300))
        self.assertEqual(self.texts(d, after=str(chat.parse_ts("2024-03-06T00:00:00+00:00"))), ["kept"])
        time.sleep(1.1)                                               # the next file is named by the second
        c.logger.stop()                                               # stopping writes what is left
        self.assertEqual((c.state()["logger"]["status"], len(self.files(d)), c.store.totals()["waiting"]), ("stopped", 2, 0))

        c.logger.start()
        FakeGateway.last.on_fatal("Discord rejected the token")
        self.assertEqual((c.logger.status, c.logger.error), ("stopped", "Discord rejected the token"))

    def test_attachments_only(self):
        d = self.drive()
        c = self.account(d)
        state = self.archive(c, attachmentsOnly=True)                  # the files, not the conversation
        self.assertEqual((state["stats"]["saved"], state["stats"]["files"], state["stats"]["scanned"]), (1, 1, 3))
        m = c.store.messages({})["messages"]
        self.assertEqual([(x["content"], x["author_name"], x["attachments"][0]["path"]) for x in m],
                         [("", "Dennis", "/Discord/Direct messages/Anna [500]/files/920-notes.txt")])
        self.assertNotIn(b"hello Anna", self.read(d, self.files(d)[0], cold=False))
        self.archive(c)                                                # everything, later: now the text is kept...
        self.assertEqual(self.texts(d), ["see you 100% soon", "hello Anna", "hi there"])
        self.archive(c, attachmentsOnly=True)                          # ...and stays
        self.assertEqual(self.texts(d), ["see you 100% soon", "hello Anna", "hi there"])

        c.logger.set_rules({"attachmentsOnly": True})
        c.logger.start()
        gw = FakeGateway.last
        gw.emit("MESSAGE_CREATE", msg(91, 500, ANNA, "just talk", 11))
        gw.emit("MESSAGE_CREATE", msg(92, 500, ANNA, "a caption", 11, files=["clip.png"]))
        gw.emit("MESSAGE_UPDATE", {"id": "92", "channel_id": "500", "content": "a new caption"})
        got = c.store.messages({"source": "live"})["messages"]
        self.assertEqual([(x["id"], x["content"], x["edits"], x["attachments"][0]["path"]) for x in got],
                         [("92", "", [], "/Discord/Direct messages/Anna [500]/files/9920-clip.png")])

    def test_logger_saves_attachments(self):
        d = self.drive()
        c = self.account(d)
        c.logger.set_rules({"downloadAttachments": True})
        c.logger.start()
        FakeGateway.last.emit("MESSAGE_CREATE", msg(41, 500, ANNA, "", 7, files=["a b?.txt"]))
        m = c.store.messages({})["messages"][0]
        self.assertEqual(m["attachments"][0]["path"], "/Discord/Direct messages/Anna [500]/files/9410-a b_.txt")
        self.assertEqual(self.read(d, m["attachments"][0]["path"], cold=False), b"hello")

    # ------------------------------------------------------------ several devices
    def test_other_devices_read_the_files(self):
        pc, pi = self.drive("aaaa"), self.drive("bbbb")
        c = self.account(pc)
        self.archive(c)
        c.logger.start()
        FakeGateway.last.emit("MESSAGE_CREATE", msg(51, 500, ANNA, "typo", 8))
        FakeGateway.last.emit("MESSAGE_UPDATE", {"id": "51", "channel_id": "500", "content": "fixed"})
        FakeGateway.last.emit("MESSAGE_DELETE", {"id": "1", "channel_id": "500"})
        c.flush()
        self.upload(pc)
        self.sync(pc, pi)
        self.assertFalse(pi.chat.state()["connected"])                # the login stays on the device it was made on
        self.assertEqual(pi.chat.store.scan(), (2, 0))
        self.assertEqual(pi.chat.store.scan(), (0, 0))
        self.assertEqual(self.texts(pi), ["fixed", "see you 100% soon", "hello Anna", "hi there"])
        got = {m["content"]: m for m in pi.chat.store.messages({})["messages"]}
        self.assertEqual((got["fixed"]["edits"][0]["old_content"], bool(got["hi there"]["deleted_ts"]), got["fixed"]["channel_name"]),
                         ("typo", True, "Anna"))
        self.assertEqual(pi.chat.store.rebuild(), (2, 0))             # the database is only a copy of the files
        self.assertEqual(len(self.texts(pi)), 4)

        # A file deleted on one device takes its messages out of the lists everywhere.
        pc.fs.unlink(self.files(pc, "/Discord/Direct")[0])
        self.assertEqual(pc.chat.store.scan(), (0, 1))
        self.assertEqual(self.texts(pc), ["fixed"])
        self.sync(pc, pi)
        pi.chat.store.scan()
        self.assertEqual(self.texts(pi), ["fixed"])

    def test_a_days_small_files_are_joined(self):
        d = self.drive()
        c = self.account(d)
        c.logger.start()
        gw = FakeGateway.last
        real = time.localtime
        try:
            for n, clock in enumerate(("2024-03-05 09.00.00", "2024-03-05 09.10.00", "2024-03-06 08.00.00")):
                time.localtime = lambda *a, clock=clock: time.strptime(clock, "%Y-%m-%d %H.%M.%S") if not a else real(*a)
                gw.emit("MESSAGE_CREATE", msg(61 + n, 500, ANNA, f"part {n}", 5))
                c.flush()
        finally:
            time.localtime = real
        gw.emit("MESSAGE_DELETE", {"id": "61", "channel_id": "500"})
        c.flush()
        self.assertEqual(len(self.files(d)), 4)
        c.store.compact_live()
        joined = "/Discord/Live/2024-03/2024-03-05 aaaa.jsonl"
        self.assertIn(joined, self.files(d))
        self.assertEqual(len(self.files(d)), 5)                       # the small files stay until the joined one is uploaded
        c.store.compact_live()
        self.assertEqual(len(self.files(d)), 5)
        self.upload(d)
        c.store.compact_live()
        names = [p.rsplit("/", 1)[1] for p in self.files(d, "/Discord/Live/2024-03")]
        self.assertEqual(names, ["2024-03-05 aaaa.jsonl", "2024-03-06 08.00.00 aaaa.jsonl"])     # one small file: left alone
        self.assertEqual(c.store.scan(), (0, 0))
        self.assertEqual(sorted(self.texts(d)), ["part 0", "part 1", "part 2"])
        self.assertEqual(self.texts(d, deleted="1"), ["part 0"])
        self.assertEqual(c.store.rebuild()[0], 3)
        self.assertEqual(sorted(self.texts(d)), ["part 0", "part 1", "part 2"])
        self.assertEqual(self.texts(d, deleted="1"), ["part 0"])

    def test_locked_folder_still_records(self):
        d = self.drive()
        c = self.account(d)
        self.archive(c)
        d.locks.add(d.index.resolve("/Discord"), "open sesame")
        d.fs.scope.unlocked = frozenset()
        self.assertTrue(d.fs._hidden("/Discord/Direct messages"))
        c.logger.start()
        FakeGateway.last.emit("MESSAGE_CREATE", msg(71, 500, ANNA, "behind the lock", 9))
        self.assertEqual(c.flush(), 1)
        self.assertEqual(d.fs.scope.unlocked, frozenset())            # the lock is closed again for this thread
        self.assertEqual(len(self.files(d)), 2)


class ChatWebTest(helpers.DriveTest):
    req, form, login = test_web.WebTest.req, test_web.WebTest.form, test_web.WebTest.login

    def setUp(self):
        super().setUp()
        FakeAccount.history = history()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.phrase = test_web.words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        self.cookie = None
        self.d.chat.make_api, self.d.chat.make_gateway = FakeAccount, FakeGateway
        self.login()

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def post(self, path, body):
        status, _, data = self.req("POST", path, body)
        return status, json.loads(data or b"{}")

    def get(self, path):
        status, _, data = self.req("GET", path)
        self.assertEqual(status, 200, data[:300])
        return json.loads(data)

    def attachments(self):
        """Shown and opened from Discord while it is there; from the copy on the drive when it is gone."""
        pic, doc = self.get("/api/chat/messages?files=1&source=live")["messages"][0]["attachments"]
        # only the attachments themselves are kept: no small copy of a picture is made or downloaded
        files = "/Discord/Direct messages/Anna [500]/files"
        self.assertEqual((pic.get("preview"), doc.get("preview")), (None, None))
        self.assertEqual(sorted(c["name"] for c in self.d.index.children(self.d.index.resolve(files)["id"])),
                         sorted(posixpath.basename(a["path"]) for a in (pic, doc)))
        self.assertFalse(any("width=400" in u for u in FakeAccount.downloaded))
        at = "id=82&a=" + pic["id"]
        self.assertEqual(self.req("GET", "/api/chat/preview?" + at)[2], b"\x89PNG from Discord")
        status, headers, _ = self.req("GET", "/api/chat/open?" + at)
        self.assertEqual((status, headers["Location"].split("?")[0]), (303, "https://cdn.example/pic.png"))
        self.assertEqual(self.req("GET", "/api/chat/open?id=82&a=nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/chat/preview?id=82&a=" + doc["id"])[0], 404)       # not a picture

        FakeAccount.gone = True
        self.assertEqual(self.req("GET", "/api/chat/preview?" + at)[0], 404)      # gone from Discord: no picture of it here
        # one that a version before 2.19.0 kept is still shown...
        kept = f"{files}/previews/{pic['id']}.webp"
        helpers.DriveTest.write(self.d, kept, b"\x89PNG kept on the drive")
        helpers.DriveTest.write(self.d, "/Discord/Servers/Book Club [300]/general [400]/files/previews/1.webp", b"\x89PNG another")
        helpers.DriveTest.write(self.d, "/Discord/Servers/Book Club [300]/general [400]/files/1-previews.png", b"an attachment")
        store = self.d.chat.store
        row = json.loads(store.get("82")["attachments"])
        row[0]["preview"] = kept
        store._run("UPDATE messages SET attachments=? WHERE id='82'", (json.dumps(row),))
        self.assertEqual(self.req("GET", "/api/chat/preview?" + at)[2], b"\x89PNG kept on the drive")
        # ...until all of them are removed, from every conversation, for good; the attachments stay
        self.assertEqual(self.post("/api/chat/previews/clear", {})[1], {"ok": True, "files": 2, "bytes": 34})
        self.assertIsNone(self.d.index.resolve(kept))
        self.assertIsNone(self.d.index.resolve(f"{files}/previews"))
        self.assertIsNone(self.d.index.resolve("/Discord/Servers/Book Club [300]/general [400]/files/previews"))
        self.assertIsNotNone(self.d.index.resolve("/Discord/Servers/Book Club [300]/general [400]/files/1-previews.png"))
        self.assertIsNotNone(self.d.index.resolve(pic["path"]))
        self.assertEqual(self.req("GET", "/api/chat/preview?" + at)[0], 404)
        self.assertEqual(self.post("/api/chat/previews/clear", {})[1]["files"], 0)
        status, headers, _ = self.req("GET", "/api/chat/open?" + at)
        self.assertEqual((status, headers["Location"]), (303, "/api/file?path=" + urllib.parse.quote(pic["path"])))
        self.assertEqual(self.req("GET", headers["Location"])[2], b"hello")
        self.d.fs.unlink(pic["path"])                                 # neither on Discord nor on the drive
        self.assertEqual(self.req("GET", "/api/chat/open?" + at)[0], 404)
        FakeAccount.gone = False

        # A link that ran out is renewed before it is used.
        old = msg(83, 500, ANNA, "old", 10, files=["old.png"])
        old["attachments"][0]["url"] = "https://cdn.example/old.png?ex=1&is=2&hm=3"
        self.d.chat.store.save_message(old, "live")
        self.get("/api/chat/messages?files=1")
        status, headers, _ = self.req("GET", "/api/chat/open?id=83&a=" + old["attachments"][0]["id"])
        self.assertEqual((status, headers["Location"], FakeAccount.refreshed),
                         (303, "https://cdn.example/old.png?ex=7fffffff&is=2&hm=3", ["https://cdn.example/old.png?ex=1&is=2&hm=3"]))
        self.d.chat.store._run("DELETE FROM messages WHERE id='83'")

    def wait_archive(self):
        self.d.chat.archiver._thread.join(20)
        return self.get("/api/chat")["archive"]

    def test_api(self):
        state = self.get("/api/chat")
        self.assertEqual((state["connected"], state["logger"], state["totals"]), (False, None, None))
        self.assertEqual(self.post("/api/chat/logger", {"on": True})[0], 409)
        status, r = self.post("/api/chat/connect", {"token": "nope"})
        self.assertEqual((status, r["error"]), (400, "Discord rejected that token. Check it and try again."))
        status, state = self.post("/api/chat/connect", {"token": "good-token"})
        self.assertEqual((status, state["connected"], state["user"]["username"]), (200, True, "dennis"))
        self.assertNotIn("good-token", json.dumps(state))
        self.assertNotIn("discord_user_token", json.dumps(self.get("/api/settings")))
        self.assertEqual(self.post("/api/settings", {"discord_user_token": "x"})[0], 400)
        d = self.get("/api/chat/directory")
        self.assertEqual(([g["name"] for g in d["guilds"]], [c["name"] for c in d["dms"]]), (["Book: Club?"], ["Anna"]))
        self.assertEqual([c["category"] for c in self.get("/api/chat/channels?guild=300")["channels"]], [None, "Reading", "Reading"])
        self.assertEqual(self.req("GET", "/api/chat/channels?guild=999")[0], 403)

        self.assertEqual(self.post("/api/chat/archive/start", {"targets": []})[0], 400)
        status, state = self.post("/api/chat/archive/start", {"targets": [{"kind": "dm", "id": "500"}]})
        self.assertEqual(status, 200)
        job = self.wait_archive()
        self.assertEqual((job["status"], job["stats"]["saved"], job["running"]), ("done", 3, False))
        self.assertEqual(self.post("/api/chat/archive/resume", {})[0], 409)
        self.assertEqual(self.post("/api/chat/archive/clear", {})[1]["archive"], None)

        status, r = self.post("/api/chat/logger/rules", {"keywords": ["Pizza "], "scope": "weird"})
        self.assertEqual((r["rules"]["keywords"], r["rules"]["scope"]), (["pizza"], "all"))
        self.assertEqual(self.post("/api/chat/logger", {"on": True})[1]["logger"]["status"], "connected")
        FakeGateway.last.emit("MESSAGE_CREATE", msg(81, 500, ANNA, "<b>pizza</b>, \"now\"", 10))
        FakeGateway.last.emit("MESSAGE_DELETE", {"id": "81", "channel_id": "500"})
        r = self.get("/api/chat/messages?source=live&order=touched")
        self.assertEqual((r["total"], r["messages"][0]["flagged"], bool(r["messages"][0]["deleted_ts"])), (1, 1, True))
        self.assertEqual(self.get("/api/chat/messages?author=anna")["total"], 3)
        self.assertEqual(self.get("/api/chat/places")["items"][0]["count"], 4)
        self.assertEqual(self.get("/api/chat")["totals"]["waiting"], 2)
        self.assertEqual(self.post("/api/chat/flush", {})[1]["written"], 2)
        self.post("/api/chat/logger/rules", {"downloadAttachments": True})
        FakeGateway.last.emit("MESSAGE_CREATE", msg(82, 500, ANNA, "look", 10, files=["pic.png", "doc.txt"]))
        self.attachments()
        self.d.chat.store.save_delete("82")
        self.d.chat.flush()
        self.assertEqual(self.post("/api/chat/logger", {"on": False})[1]["logger"]["status"], "stopped")

        status, headers, body = self.req("GET", "/api/chat/export?format=html&source=live")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"&lt;b&gt;pizza&lt;/b&gt;", body)
        self.assertNotIn(b"<b>pizza", body)
        rows = json.loads(self.req("GET", "/api/chat/export?format=json&channel=500")[2])
        self.assertEqual([m["content"] for m in rows][:2], ["hi there", "hello Anna"])
        csv = self.req("GET", "/api/chat/export?format=csv&source=live")[2].decode("utf-8-sig")
        self.assertIn('"<b>pizza</b>, ""now"""', csv)
        self.assertIn(b"hi there", self.req("GET", "/api/chat/export?format=txt")[2])
        self.assertEqual(self.post("/api/chat/rebuild", {})[1]["files"], 3)
        self.assertEqual(self.get("/api/chat/messages")["total"], 5)

        # Recent is not filled with message files.
        self.assertEqual([i for i in self.get("/api/recent")["items"] if i["path"].startswith("/Discord")], [])

        status, state = self.post("/api/chat/disconnect", {})
        self.assertEqual((state["connected"], self.d.cfg.discord_user_token), (False, ""))
        self.assertEqual(self.get("/api/chat/messages")["total"], 5)   # what was kept stays readable

    def test_locked_folder_hides_the_messages(self):
        self.post("/api/chat/connect", {"token": "good-token"})
        self.post("/api/chat/archive/start", {"targets": [{"kind": "dm", "id": "500"}]})
        self.wait_archive()
        self.assertEqual(self.post("/api/locks/add", {"path": "/Discord", "password": "open sesame"})[0], 200)
        self.post("/api/locks/lock", {})
        state = self.get("/api/chat")
        self.assertEqual((state["locked"], state["totals"], state["connected"]), (True, None, True))
        for path in ("/api/chat/messages", "/api/chat/places", "/api/chat/export", "/api/chat/directory"):
            self.assertEqual(self.req("GET", path)[0], 404, path)
        self.assertEqual(self.post("/api/chat/connect", {"token": "good-token"})[0], 404)


class SocketTest(helpers.unittest.TestCase):
    """ws.WebSocket against a tiny server on this computer."""

    def serve(self, script):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        self.got = []

        def frame(opcode, payload, fin=True):
            n = len(payload)
            size = bytes([n]) if n < 126 else b"\x7e" + struct.pack("!H", n) if n < 65536 else b"\x7f" + struct.pack("!Q", n)
            return bytes([(0x80 if fin else 0) | opcode]) + size + payload

        def exactly(conn, n):
            data = b""
            while len(data) < n:
                data += conn.recv(n - len(data))
            return data

        def read(conn):
            head = exactly(conn, 2)
            n = head[1] & 0x7F
            if n == 126:
                n = struct.unpack("!H", exactly(conn, 2))[0]
            elif n == 127:
                n = struct.unpack("!Q", exactly(conn, 8))[0]
            mask = exactly(conn, 4)
            data = exactly(conn, n)
            return head[0] & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))

        def run():
            conn, _ = srv.accept()
            with conn:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += conn.recv(4096)
                key = [line.split(b": ")[1] for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")][0]
                accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
                conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                script(conn, frame, read)
        threading.Thread(target=run, daemon=True).start()
        return f"ws://127.0.0.1:{srv.getsockname()[1]}/?v=9"

    def test_messages_both_ways(self):
        big = "é" * 40000

        def script(conn, frame, read):
            first = frame(1, b"hel", fin=False)
            conn.sendall(first[:3])                                    # a message in two frames, the first in two packets
            time.sleep(0.4)
            conn.sendall(first[3:] + frame(9, b"ping") + frame(0, b"lo"))
            self.got.append(read(conn))                                # the pong
            self.got.append(read(conn))
            conn.sendall(frame(1, big.encode()))
            self.got.append(read(conn))
            conn.sendall(frame(8, struct.pack("!H", 4004) + b"Authentication failed."))
        sock = ws.WebSocket(self.serve(script), timeout=5)
        self.assertIsNone(sock.recv(0.1))                              # nothing whole yet, and nothing is lost by waiting
        self.assertEqual(sock.recv(5), "hello")
        sock.send("hi")
        self.assertEqual(sock.recv(5), big)
        sock.send(big)
        with self.assertRaises(ws.Closed) as e:
            sock.recv(5)
        self.assertEqual(e.exception.code, 4004)
        self.assertEqual(self.got, [(0xA, b"ping"), (1, b"hi"), (1, big.encode())])

    def test_refused(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def run():
            conn, _ = srv.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            conn.close()
        threading.Thread(target=run, daemon=True).start()
        with self.assertRaises(ws.Closed):
            ws.WebSocket(f"ws://127.0.0.1:{srv.getsockname()[1]}/", timeout=5)


class ScriptedSocket:
    def __init__(self, steps):
        self.steps, self.sent, self.closed = list(steps), [], False

    def recv(self, timeout=None):
        if not self.steps:
            time.sleep(min(timeout or 0.05, 0.05))
            return None
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return json.dumps(step)

    def send(self, text):
        self.sent.append(json.loads(text))

    def close(self, code=1000):
        self.closed = True


class GatewayTest(helpers.unittest.TestCase):
    def test_identify_resume_and_rejected_token(self):
        hello = {"op": 10, "d": {"heartbeat_interval": 60000}}
        first = ScriptedSocket([hello, {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "abc", "resume_gateway_url": "wss://resume.example"}},
                                {"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": {"id": "5"}}, ws.Closed(1006)])
        second = ScriptedSocket([hello, {"op": 0, "t": "RESUMED", "s": 3, "d": {}}, {"op": 1}, ws.Closed(4004)])
        sockets, urls, events, statuses, fatal = [first, second], [], [], [], []

        def connect(url):
            urls.append(url)
            return sockets.pop(0)
        g = chat.Gateway("tok", lambda t, d: events.append(t), statuses.append, fatal.append, connect=connect)
        g.backoff = 0.01
        g.start()
        g._thread.join(10)
        self.assertFalse(g._thread.is_alive())
        self.assertEqual(urls, ["wss://gateway.discord.gg/?v=9&encoding=json", "wss://resume.example/?v=9&encoding=json"])
        self.assertEqual((first.sent[0]["op"], first.sent[0]["d"]["token"], first.sent[0]["d"]["presence"]["status"]), (2, "tok", "invisible"))
        self.assertEqual(second.sent, [{"op": 6, "d": {"token": "tok", "session_id": "abc", "seq": 2}}, {"op": 1, "d": 3}])
        self.assertEqual(events, ["READY", "MESSAGE_CREATE", "RESUMED"])
        self.assertEqual(statuses, ["connecting", "connected", "reconnecting", "reconnecting", "connected", "stopped"])
        self.assertEqual(len(fatal), 1)
        self.assertTrue(first.closed and second.closed)

    def test_heartbeat_and_silence(self):
        sock = ScriptedSocket([{"op": 10, "d": {"heartbeat_interval": 40}}])
        g = chat.Gateway("tok", lambda t, d: None, connect=lambda url: sock)
        with self.assertRaises(ws.Closed) as e:                       # no answer to a heartbeat: the connection is dead
            g._listen()
        self.assertEqual(e.exception.code, 4000)
        self.assertEqual([p["op"] for p in sock.sent], [2, 1])


if __name__ == "__main__":
    helpers.unittest.main()
