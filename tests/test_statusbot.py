"""The status bot: /api/monitor, the statuses it makes of it, and what the bot sends to Discord."""

import http.client
import json
import os
import time
from unittest import mock

import helpers
from ddrive import monitor, statusbot
from ddrive import statuslines as sl


def snap(**over):
    s = {
        "v": 1, "time": 1000.0, "version": "3.4.0", "host": "box", "started": 0.0, "uptime": 25 * 3600,
        "drive": {"files": 25000, "dirs": 300, "bytes": 500 * 1024 ** 3, "unsynced": 0, "pinned_files": 0, "pinned_bytes": 0,
                  "chunks": 1000, "unique_chunks": 990, "trash": 2, "outbox": 0, "versions": 40, "version_bytes": 1 << 30,
                  "protected_chunks": 1000, "spare_pieces": 200, "spare_bytes": 1 << 30, "snapshots": 3},
        "uploads": {"active": 0, "queued": 0, "sent": 0, "left": 0, "speed": 0.0, "eta": None, "size": 0, "done": 0,
                    "done_bytes": 0, "failed": 0, "retrying": 0, "last_done": 0, "last_failed": 0, "uploaded_bytes": 0,
                    "reused_bytes": 0, "compressed_saved": 0, "spare_bytes": 0, "room": 5},
        "downloads": {"pieces": 0, "files": 0, "jobs": 0, "speed": 0.0},
        "health": {"parity": True, "group": 10, "pieces": 2, "lost": 0, "lost_by": {}, "repairs_queued": 0, "rebuilt": 0,
                   "repaired": 0, "lost_found": 0, "recheck": None, "scrub": {}, "leader": True, "bots": 1,
                   "refused_bots": 0, "channels": 1, "servers": 1, "unreachable_channels": 0, "given_up_channels": 0},
        "sync": {"failures": 0, "outbox": 0, "folders": 0, "states": {}},
        "errors": {"5m": {"errors": 0, "warnings": 0, "by": {}}, "1h": {"errors": 0, "warnings": 0, "by": {}},
                   "24h": {"errors": 0, "warnings": 0, "by": {}}, "last_error": None},
        "resources": {"cpu": 3.0, "cores": 4, "memory": {}, "disk": {"total": 1 << 40, "free": 1 << 39}, "threads": 20},
        "devices": {"count": 1, "online": 1, "lite": 0},
        "accounts": 0,
        "settings": {"pace": "gentle", "encrypted": True},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    return s


T = 1_800_000_000  # (times near 0 are refused on Windows)


def texts(statuses):
    return [s.text for s in statuses]


class WordsTest(helpers.unittest.TestCase):
    def test_numbers_in_words(self):
        self.assertEqual(sl.size(500 * 1024 ** 3), "500 GB")
        self.assertEqual(sl.size(1536), "1.5 KB")
        self.assertEqual(sl.count(25000, "file"), "25,000 files")
        self.assertEqual(sl.count(1, "file"), "1 file")
        self.assertEqual(sl.duration(25 * 3600), "1 day 1 hour")
        self.assertEqual(sl.duration(5 * 3600 + 120), "5 hours 2 minutes")
        self.assertEqual(sl.duration(90, short=True), "1m 30s")

    def test_log_lines_get_a_category(self):
        self.assertEqual(monitor.category("ddrive.uploader", "Failed uploading /a: boom"), "upload")
        self.assertEqual(monitor.category("ddrive.cache", "[Errno 28] No space left on device"), "disk")
        self.assertEqual(monitor.category("ddrive.api", "Discord asked to wait 30 s (rate limit)"), "ratelimit")
        self.assertEqual(monitor.category("ddrive.api", "Connection reset by peer"), "network")


class StatusesTest(helpers.unittest.TestCase):
    def test_the_drive_in_numbers(self):
        h = sl.History()
        h.observe(snap(), T + 1000)
        t = texts(sl.build(snap(), h, T + 1000))
        self.assertIn("⏱️ Uptime: 1 day 1 hour", t)
        self.assertIn("📁 25,000 files on the drive", t)
        self.assertIn("☁️ 500 GB of files on Discord", t)
        self.assertIn("💚 Healthy: no errors in the last hour", t)
        self.assertGreater(len(t), 25)
        self.assertEqual(sl.mood(sl.build(snap(), h, T + 1000)), "online")
        self.assertTrue(all(len(x) <= 128 for x in t))

    def test_a_batch_of_uploads_counts_up_and_ends(self):
        h = sl.History()
        h.observe(snap(), T + 1000)
        busy = snap(uploads={"active": 3, "queued": 97, "speed": 2 * 1024 * 1024, "eta": 300, "left": 600 << 20})
        h.observe(busy, T + 1015)
        t = texts(sl.build(busy, h, T + 1015))
        self.assertIn("⬆️ Uploading 0/100 files", t)
        self.assertIn("🚀 Uploading at 2.0 MB/s", t)
        self.assertIn("🗂️ 97 files waiting in the queue", t)
        later = snap(uploads={"active": 3, "queued": 77, "done": 20, "done_bytes": 20 << 20})
        h.observe(later, T + 1030)
        self.assertIn("⬆️ Uploading 20/100 files", texts(sl.build(later, h, T + 1030)))
        done = snap(uploads={"done": 100, "done_bytes": 100 << 20})
        events = h.observe(done, T + 1100)
        self.assertTrue(any("All uploads completed: 100 files" in e[1] for e in events))
        self.assertIn("✅ All uploads completed (100 files)", texts(sl.build(done, h, T + 1100)))
        self.assertIsNone(h.batch)

    def test_problems_come_first_and_turn_the_dot_red(self):
        h = sl.History()
        bad = snap(errors={"5m": {"errors": 4, "warnings": 0, "by": {"disk": 4}}})
        events = h.observe(bad, T + 1000)
        st = sl.build(bad, h, T + 1000)
        self.assertEqual(st[0].text, "💥 Experiencing I/O failures (4 errors in 5m)")
        self.assertEqual(sl.mood(st), "dnd")
        self.assertTrue(any("Problems started" in e[1] for e in events))
        events = h.observe(snap(), T + 1300)
        self.assertTrue(any("Problems cleared after 5 minutes" in e[1] for e in events))

    def test_offline_and_back(self):
        h = sl.History()
        h.observe(snap(), T + 1000)
        events = h.missed("no connection", T + 1015)
        self.assertEqual(events[0][0], sl.CRITICAL)
        self.assertIn("🔴 Drive offline for 5 minutes", texts(sl.offline(h, T + 1315)))
        events = h.observe(snap(), T + 1600)
        self.assertIn("✅ Back online after 9 minutes", [e[1] for e in events])

    def test_a_restart_milestones_and_an_update(self):
        h = sl.History()
        h.observe(snap(drive={"files": 24990}), T + 1000)
        events = [e[1] for e in h.observe(snap(started=500.0, version="3.5.0", uptime=10), T + 1015)]
        self.assertTrue(any(e.startswith("🔄 The drive restarted") for e in events))
        self.assertIn("⬆️ Updated to DDrive 3.5.0 (was 3.4.0)", events)
        self.assertIn("🎉 25,000 files on the drive", events)
        self.assertEqual(h.lifetime["restarts"], 1)

    def test_history_survives_being_saved(self):
        h = sl.History()
        h.observe(snap(uploads={"done": 5, "done_bytes": 50}), T + 1000)
        again = sl.History(json.loads(json.dumps(h.to_json())))
        self.assertEqual(again.lifetime, h.lifetime)
        self.assertEqual(again.days, h.days)

    def test_rotation_shows_every_status_in_turn(self):
        h = sl.History()
        h.observe(snap(), T + 1000)
        st = sl.build(snap(), h, T + 1000)
        seen = {sl.Rotation().next(st).key}
        r = sl.Rotation()
        for _ in range(len(st) * 3):
            seen.add(r.next(st).key)
        self.assertEqual(seen, {s.key for s in st})

    def test_rotation_keeps_coming_back_to_a_problem(self):
        bad = snap(errors={"5m": {"errors": 1, "warnings": 0, "by": {"disk": 1}}})
        st = sl.build(bad, sl.History(), T + 1000)
        r = sl.Rotation()
        shown = [r.next(st).key for _ in range(9)]
        self.assertGreaterEqual(shown.count("io"), 6)


class FakeAPI:
    token = "t"

    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {"id": "42"} if method == "POST" else None


class BotTest(helpers.unittest.TestCase):
    def setUp(self):
        self.tmp = helpers.tempfile.mkdtemp(prefix="dd-bot-")
        p = mock.patch("ddrive.statusbot.config_path", lambda: os.path.join(self.tmp, "config.json"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(helpers.shutil.rmtree, self.tmp, True)

    def bot(self, **s):
        settings = dict(statusbot.DEFAULTS, url="http://x", key="k", token="t", channel="9", **s)
        self.api = FakeAPI()
        return statusbot.StatusBot(settings, api=self.api)

    def test_board_alerts_and_slash_commands(self):
        b = self.bot(mention="<@&5>")
        with mock.patch("ddrive.statusbot.fetch", return_value=snap()):
            b.poll_once()
        b._update_board()
        self.assertEqual(self.api.calls[-1][1], "/channels/9/messages")
        self.assertIn("Healthy", self.api.calls[-1][2]["embeds"][0]["title"])
        with mock.patch("ddrive.statusbot.fetch", side_effect=statusbot.DriveDown("no connection")):
            events = b.poll_once()
        for level, text in events:
            b._alert(level, text)
        msg = self.api.calls[-1][2]
        self.assertTrue(msg["content"].startswith("<@&5> 🔴"))
        self.assertEqual(msg["allowed_mentions"], {"roles": ["5"]})
        self.assertIn("offline", b.answer_for("uptime")["content"])
        self.assertIn("Offline", b.answer_for("status")["embeds"][0]["title"])
        self.assertTrue(b.answer_for("statuses")["embeds"][0]["description"])

    def test_settings_keep_the_secrets_protected(self):
        path = os.path.join(self.tmp, "statusbot.json")
        statusbot.save_settings(dict(statusbot.DEFAULTS, url="http://x", key="the-key", token="the-token"), path)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("the-token", raw)
        self.assertNotIn("the-key", raw)
        s = statusbot.load_settings(path)
        self.assertEqual((s["token"], s["key"], s["url"]), ("the-token", "the-key", "http://x"))


class MonitorEndpointTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token", monitor_key="mon-key")
        self.web = self.d.start_web(port=0)

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def get(self, key=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        conn.request("GET", "/api/monitor", headers={"Authorization": f"Bearer {key}"} if key else {})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_only_with_the_key_and_only_numbers(self):
        self.write(self.d, "/Secret plans/holiday.txt", b"x" * 1000)
        self.upload(self.d)
        self.d.index._stats_cache = (0.0, None)
        self.d.index._stats_last = None
        self.assertEqual(self.get()[0], 401)
        self.assertEqual(self.get("wrong")[0], 401)
        status, body = self.get("mon-key")
        self.assertEqual(status, 200)
        self.assertNotIn(b"holiday", body)
        self.assertNotIn(b"Secret plans", body)
        s = json.loads(body)
        self.assertEqual(s["drive"]["files"], 1)
        self.assertEqual(s["uploads"]["done"], 1)
        self.assertGreater(len(sl.build(s, sl.History(), time.time())), 20)
        # the key opens nothing else
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        conn.request("GET", "/api/status", headers={"Authorization": "Bearer mon-key"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_no_key_set_means_nobody(self):
        self.web.cfg.monitor_key = ""
        self.assertEqual(self.get("")[0], 401)
        self.assertEqual(self.get("anything")[0], 401)


class FakeSocket:
    """Discord's side of the bot's connection: hands out `incoming`, keeps what the bot sends."""

    def __init__(self, incoming, bot):
        self.incoming = list(incoming)
        self.sent = []
        self.bot = bot

    def recv(self, timeout=None):
        if self.incoming:
            return json.dumps(self.incoming.pop(0))
        self.bot._stop.set()
        return None

    def send(self, text):
        self.sent.append(json.loads(text))

    def close(self, code=1000):
        pass


class GatewayTest(BotTest):
    def test_signs_in_with_a_status_and_answers_commands(self):
        b = self.bot()
        b._last_presence = ("⏱️ Uptime: 2 hours", "online")
        sock = FakeSocket([
            {"op": 10, "d": {"heartbeat_interval": 40000}},
            {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "s", "resume_gateway_url": "wss://r",
                                                 "application": {"id": "77"}, "user": {"username": "bot"}}},
            {"op": 0, "s": 2, "t": "INTERACTION_CREATE", "d": {"type": 2, "id": "5", "token": "tok", "data": {"name": "uptime"}}},
        ], b)
        b._connect = lambda url: sock
        with mock.patch("ddrive.statusbot.threading.Thread") as thread:
            thread.side_effect = lambda target, args=(), **kw: mock.Mock(start=lambda: target(*args))
            b._listen()
        hello = sock.sent[0]
        self.assertEqual(hello["op"], 2)
        self.assertEqual(hello["d"]["presence"]["activities"][0], {"type": 4, "name": "Custom Status", "state": "⏱️ Uptime: 2 hours"})
        self.assertEqual(hello["d"]["intents"], 0)
        self.assertIn(("PUT", "/applications/77/commands", statusbot.COMMANDS), self.api.calls)
        answer = next(c for c in self.api.calls if c[1] == "/interactions/5/tok/callback")
        self.assertEqual(answer[2]["type"], 4)
        self.assertEqual(b.session_id, "s")
