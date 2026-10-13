"""Folder syncing on a DDrive Lite computer: the lite side announces itself, its folders are chosen on
the host's dashboard, and it does the copying. Here the host is a whole drive with its dashboard on a
free local port, the dashboard is somebody else signed in to it, and the lite computer is its agent
running against a folder of the test."""

import http.client
import json
import os
import socket
import time
import urllib.parse

import helpers
from ddrive import lite, litesync, sync, syncdevices, words

DEVICE = "0123456789abcdef"


class LiteSyncTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.host = self.drive(web_token="secret-token")
        self.web = self.host.start_web(port=0)
        self.phrase = words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key))
        self.address = f"http://127.0.0.1:{self.web.port}"
        self.page = lite.HostClient(self.address, self.phrase)          # the dashboard's Sync page, as requests
        self.here = os.path.join(self.tmp, "here")
        os.makedirs(self.here)
        self.agents = []
        self.agent = self.new_agent()

    def tearDown(self):
        for a in self.agents:
            a.stop()
        self.web.stop()
        super().tearDown()

    # ------------------------------------------------------------ helpers
    def new_agent(self, phrase=None, device=DEVICE, name="laptop", start=True):
        agent = litesync.Agent(lite.HostClient(self.address, phrase or self.phrase), device, name,
                               data_dir=os.path.join(self.tmp, "lite-" + device))
        self.agents.append(agent)
        return agent.start() if start else agent

    def until(self, fn, what="it", seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            got = fn()
            if got:
                return got
            time.sleep(0.05)
        self.fail(f"{what} did not happen")

    def device(self, page=None, device=DEVICE):
        return next((d for d in (page or self.page)._json("/api/sync")["devices"] if d["id"] == device), None)

    def connected(self):
        return self.until(lambda: (self.device() or {}).get("online") and self.agent.online and self.device(), "connecting")

    def add(self, mode, remote="/Backup", **kw):
        job = dict(local=self.here, remote=remote, mode=mode, trigger="manual", **kw)
        return self.page._json("/api/sync/save", {"device": DEVICE, "job": job})["job"]

    def run_job(self, job, **extra):
        before = ((self.agent.status.get(job["id"]) or {}).get("history") or [{}])[0].get("at")

        def done():
            hist = (self.agent.status.get(job["id"]) or {}).get("history") or []
            return hist[0] if hist and hist[0]["at"] != before and self.agent.running is None else None
        self.until(lambda: self.agent.job(job["id"]), "the pair reaching the lite computer")
        self.page._json("/api/sync/run", {"device": DEVICE, "id": job["id"], **extra})
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

    def local(self, rel):
        p = os.path.join(self.here, *rel.split("/"))
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def remote(self, path):
        return self.read(self.host, path, cold=False) if self.host.index.resolve(path) is not None else None

    # ------------------------------------------------------------ the signal
    def test_a_lite_computer_shows_up_and_is_steered_from_the_dashboard(self):
        dev = self.connected()
        self.assertEqual((dev["name"], dev["jobs"], dev["windows"]), ("laptop", [], os.name == "nt"))
        # a pair is added on the dashboard, for a folder of the lite computer; that one does the copying
        self.put("a.txt", b"one")
        self.put("sub/b.bin", os.urandom(200_000))
        self.put("video.mp4.crdownload", b"half a download")
        job = self.add("backup")
        self.assertEqual((job["name"], job["remote"]), ("here", "/Backup"))
        self.assertEqual(self.run_job(job)["up"], 2)
        self.assertEqual(self.remote("/Backup/a.txt"), b"one")
        self.assertEqual(self.remote("/Backup/sub/b.bin"), self.local("sub/b.bin"))
        self.assertIsNone(self.host.index.resolve("/Backup/video.mp4.crdownload"))
        # copies keep the file's own time, so nothing is copied twice
        self.assertAlmostEqual(self.host.index.resolve("/Backup/a.txt")["mtime"],
                               os.path.getmtime(os.path.join(self.here, "a.txt")), delta=0.01)
        self.assertEqual(self.run_job(job)["up"], 0)
        # the dashboard sees how it went
        shown = self.until(lambda: [j for j in self.device()["jobs"] if j["status"].get("last_run")], "the report")[0]
        self.assertEqual((shown["label"], shown["direction"], shown["status"]["state"]), ("Back up", "up", "idle"))
        self.assertEqual(len(shown["status"]["history"]), 2)
        home = json.loads(self.page.request("GET", "/api/home"))
        self.assertEqual(home["sync"]["jobs"], 1)
        # changing, pausing and removing it there reaches the lite computer
        self.page._json("/api/sync/save", {"device": DEVICE, "job": {"id": job["id"], "mode": "mirror", "name": "Mine"}})
        self.until(lambda: self.agent.job(job["id"])["mode"] == "mirror", "the change")
        os.remove(os.path.join(self.here, "a.txt"))
        self.assertEqual(self.run_job(job)["deleted_remote"], 1)
        self.assertIsNone(self.host.index.resolve("/Backup/a.txt"))
        self.page._json("/api/sync/pause", {"device": DEVICE, "id": job["id"], "paused": True})
        self.until(lambda: not self.agent.job(job["id"])["enabled"], "the pause")
        self.assertEqual(self.device()["jobs"][0]["status"]["state"], "paused")
        self.page._json("/api/sync/remove", {"device": DEVICE, "id": job["id"]})
        self.until(lambda: not self.agent.jobs, "the removal")
        self.assertEqual(self.device()["jobs"], [])

    def test_both_ways_and_down(self):
        self.connected()
        self.write(self.host, "/Shared/from-drive.txt", b"drive")
        self.put("from-here.txt", b"here")
        job = self.add("two-way", "/Shared")
        res = self.run_job(job)
        self.assertEqual((res["up"], res["down"]), (1, 1))
        self.assertEqual((self.local("from-drive.txt"), self.remote("/Shared/from-here.txt")), (b"drive", b"here"))
        # a deletion on either side goes to the other; a file changed on both is kept twice
        os.remove(os.path.join(self.here, "from-drive.txt"))
        self.host.fs.unlink("/Shared/from-here.txt")
        res = self.run_job(job)
        self.assertEqual((res["deleted_remote"], res["deleted_local"]), (1, 1))
        self.assertEqual((self.local("from-here.txt"), self.host.index.resolve("/Shared/from-drive.txt")), (None, None))
        self.put("both.txt", b"first")
        self.run_job(job)
        self.put("both.txt", b"changed here", age=50)
        self.write(self.host, "/Shared/both.txt", b"changed on the drive")
        self.assertEqual(self.run_job(job)["conflicts"], 1)
        self.assertEqual(self.local("both.txt"), b"changed on the drive")
        self.assertEqual(sorted(len(self.local(n)) for n in os.listdir(self.here)), [12, 20])
        # drive -> this computer, exactly: what goes from the drive is moved aside here, not deleted
        self.page._json("/api/sync/save", {"device": DEVICE, "job": {"id": job["id"], "mode": "download-mirror"}})
        self.until(lambda: self.agent.job(job["id"])["mode"] == "download-mirror", "the change")
        self.put("only-here.txt", b"x")
        self.assertEqual(self.run_job(job)["deleted_local"], 1)
        self.assertIsNone(self.local("only-here.txt"))
        self.assertTrue(os.listdir(os.path.join(self.agent.dir, "trash", job["id"])))

    def test_folders_are_looked_at_on_the_lite_computer(self):
        self.connected()
        os.makedirs(os.path.join(self.here, "Photos"))
        got = json.loads(self.page.request("GET", "/api/sync/browse?" + urllib.parse.urlencode({"device": DEVICE, "path": self.here})))
        self.assertEqual(([d["name"] for d in got["dirs"]], got["path"]), (["Photos"], self.here))
        self.assertTrue(json.loads(self.page.request("GET", f"/api/sync/browse?device={DEVICE}"))["roots"])
        with self.assertRaises(lite.HostError) as e:
            self.page.request("GET", "/api/sync/browse?" + urllib.parse.urlencode({"device": DEVICE, "path": self.here + "-not"}))
        self.assertIn("Folder not found", str(e.exception))
        # what the host can check, it does; what only the lite computer can tell is told by its pair
        for bad in ({"local": "", "remote": "/A"}, {"local": self.here, "remote": "/", "mode": "mirror"},
                    {"local": "C:\\", "remote": "/A", "mode": "two-way"}, {"local": self.here, "remote": "/A", "mode": "nope"}):
            with self.assertRaises(lite.HostError) as e:
                self.page._json("/api/sync/save", {"device": DEVICE, "job": bad})
            self.assertEqual(e.exception.status, 400)
        gone = self.page._json("/api/sync/save", {"device": DEVICE, "job": {"local": self.here + "-not", "remote": "/A",
                                                                             "trigger": "manual"}})["job"]
        self.until(lambda: self.agent.job(gone["id"]), "the pair")
        self.page._json("/api/sync/run", {"device": DEVICE, "id": gone["id"]})
        self.until(lambda: "isn't there" in (self.agent.status[gone["id"]].get("error") or ""), "the error")
        self.until(lambda: self.device()["jobs"][0]["status"]["state"] == "error", "the error on the dashboard")
        part = self.page._json("/api/sync/save", {"device": DEVICE, "job": {"local": "Documents", "remote": "/B",
                                                                             "trigger": "manual"}})["job"]
        self.until(lambda: self.agent.job(part["id"]), "the pair")
        self.assertIn("whole path", self.agent.refused[part["id"]])
        with self.assertRaises(lite.HostError) as e:       # the same two folders twice
            self.page._json("/api/sync/save", {"device": DEVICE, "job": {"local": "Documents", "remote": "B"}})
        self.assertIn("already paired", str(e.exception))
        with self.assertRaises(lite.HostError) as e:
            self.page._json("/api/sync/run", {"device": "ffffffffffffffff", "id": "x"})
        self.assertEqual(e.exception.status, 404)

    def test_the_lite_computer_keeps_its_pairs(self):
        self.connected()
        job = self.add("backup")
        self.until(lambda: self.agent.job(job["id"]), "the pair")
        # a host that was restarted knows nothing: the lite computer says what it has
        self.web.lite._devs.clear()
        with self.web.lite._cv:
            self.web.lite._cv.notify_all()
        self.until(lambda: self.device() and self.device()["jobs"], "the pairs coming back")
        self.assertEqual(self.device()["jobs"][0]["id"], job["id"])
        # so does a lite computer that was restarted; what was changed on the dashboard meanwhile wins
        self.agent.stop()
        self.page._json("/api/sync/save", {"device": DEVICE, "job": {"id": job["id"], "name": "Renamed"}})
        self.web.lite._devs[("", DEVICE)]["seen"] -= 100             # (not heard from for a while)
        self.assertFalse(self.device()["online"])
        with self.assertRaises(lite.HostError) as e:
            self.page._json("/api/sync/run", {"device": DEVICE, "id": job["id"]})
        self.assertEqual(e.exception.status, 409)                      # not connected right now
        self.agent = self.new_agent()
        self.assertEqual([j["id"] for j in self.agent.jobs], [job["id"]])       # read from its own disk
        self.until(lambda: self.agent.jobs and self.agent.jobs[0]["name"] == "Renamed", "the newer change")
        # turned off: off the host's list, the pairs stay on the lite computer
        self.agent.stop()
        self.agent.client.sync_call("bye", device=DEVICE)
        self.assertIsNone(self.device())
        with open(self.agent._jobs_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["jobs"][0]["name"], "Renamed")

    def test_half_a_copy_is_not_left_on_the_drive(self):
        self.page.status()
        head = (f"POST /api/upload?path=/cut.bin&mtime=1700000000.5 HTTP/1.1\r\nHost: 127.0.0.1\r\nX-DD: 1\r\n"
                f"Cookie: dd_session={self.page.cookie}\r\nContent-Length: 1000\r\n\r\n").encode()
        s = socket.create_connection(("127.0.0.1", self.web.port))
        s.sendall(head + b"x" * 100)
        s.shutdown(socket.SHUT_WR)
        answer = b""
        while b"cut off" not in answer:
            more = s.recv(65536)
            if not more:
                break
            answer += more
        s.close()
        self.assertIn(b"cut off", answer)
        self.assertIsNone(self.host.index.resolve("/cut.bin"))
        self.page.upload("/whole.bin", __import__("io").BytesIO(b"y" * 10), 10, mtime=1700000000.5)
        self.assertEqual(self.host.index.resolve("/whole.bin")["mtime"], 1700000000.5)
        tree = self.page.tree("/")
        self.assertEqual((tree["dir"], [f[:3] for f in tree["files"]]), (True, [["whole.bin", 10, 1700000000.5]]))
        self.assertEqual(self.page.tree("/nothing"), {"exists": False, "dir": False, "files": [], "dirs": []})

    def test_a_big_file_goes_in_parts_and_goes_on_where_it_stopped(self):
        data = os.urandom(300_000)
        src = self.put("film.mp4", data)
        mtime = os.path.getmtime(src)
        part = 64 * 1024
        q = urllib.parse.urlencode({"path": "/film.mp4", "size": len(data), "mtime": repr(mtime)})
        self.page.status()
        # two parts arrive, then the connection is lost
        for off in (0, part):
            got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={off}", data[off:off + part]))
            self.assertEqual((got["ok"], got["offset"]), (True, off + part))
        self.assertIsNone(self.host.index.resolve("/film.mp4"))            # not a file before all of it is there
        self.assertEqual(json.loads(self.page.request("GET", f"/api/upload/part?{q}"))["offset"], 2 * part)
        # a part that isn't the next one is answered with where the host is
        got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={3 * part}", b"x"))
        self.assertEqual((got["ok"], got["offset"]), (False, 2 * part))
        # a part that was sent already is sent again (its answer never arrived), and one that starts in the
        # middle of what is there (the sender went back): the file goes on from where the part starts,
        # and what was there after that point is replaced. Nothing is in it twice.
        got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={part}", data[part:2 * part]))
        self.assertEqual((got["ok"], got["offset"]), (True, 2 * part))
        got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={part // 2}", data[part // 2:part]))
        self.assertEqual((got["ok"], got["offset"]), (True, part))
        self.assertEqual(json.loads(self.page.request("GET", f"/api/upload/part?{q}"))["offset"], part)
        got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={part}", data[part:2 * part]))
        self.assertEqual((got["ok"], got["offset"]), (True, 2 * part))
        # no room for the rest of it while another file is arriving too: it waits and keeps what it has;
        # with nothing else arriving it is told that there is no room
        folder = os.path.join(self.host.cfg.resolved_data_dir, "partials")
        other = os.path.join(folder, "0" * 32 + ".part")
        with open(other, "wb") as f:
            f.write(b"x")
        room, self.host.fs.room_for = self.host.fs.room_for, lambda n: "the disk has 1.0 GiB free"
        try:
            got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={2 * part}", data[2 * part:3 * part]))
            self.assertEqual((got["ok"], got["offset"]), (False, 2 * part))
            self.assertIn("other uploads have to finish first", got["wait"])
            os.remove(other)
            with self.assertRaises(lite.HostError) as e:
                self.page.request("POST", f"/api/upload/part?{q}&offset={2 * part}", data[2 * part:3 * part])
            self.assertEqual(e.exception.status, 507)
        finally:
            self.host.fs.room_for = room
        # (what was there went with that: from the start again)
        for off in (0, part):
            got = json.loads(self.page.request("POST", f"/api/upload/part?{q}&offset={off}", data[off:off + part]))
            self.assertEqual((got["ok"], got["offset"]), (True, off + part))
        # the sender goes on from there; meanwhile the host has to catch up once and says so
        heard, real = [], self.host.fs.holding_back
        holds = iter(["upload backlog is 2.0 GiB (limit 2.0 GiB)"])
        self.host.fs.holding_back = lambda *a, **kw: next(holds, None)
        keep, lite.time.sleep = lite.time.sleep, lambda s: None
        try:
            with open(src, "rb") as f:
                got = self.page.upload_parts("/film.mp4", f, len(data), mtime=mtime, hear=lambda **kw: heard.append(kw), part=part)
        finally:
            lite.time.sleep, self.host.fs.holding_back = keep, real
        self.assertTrue(got["done"])
        self.assertEqual(heard[0], {"part_bytes": 2 * part})               # what was there already
        self.assertIn("backlog", heard[1]["note"])
        self.assertEqual(sum(h.get("part_bytes", 0) for h in heard), len(data))
        self.assertEqual(self.remote("/film.mp4"), data)
        self.assertAlmostEqual(self.host.index.resolve("/film.mp4")["mtime"], mtime, delta=0.001)
        self.assertEqual(os.listdir(os.path.join(self.host.cfg.resolved_data_dir, "partials")), [])
        # a changed file starts over; an empty one is one empty part
        self.put("film.mp4", data[:1000])
        with open(src, "rb") as f:
            self.page.upload_parts("/film.mp4", f, 1000, mtime=mtime + 5, part=part)
        self.assertEqual(self.remote("/film.mp4"), data[:1000])
        self.page.upload_parts("/empty.txt", __import__("io").BytesIO(b""), 0, mtime=mtime)
        self.assertEqual(self.host.index.resolve("/empty.txt")["size"], 0)
        # no room on the host for it: said at once, and nothing is left lying there
        self.upload(self.host)                   # (nothing is waiting to go to Discord, so waiting would not help)
        self.host.cfg.min_free_disk_bytes = 1 << 60
        with self.assertRaises(lite.HostError) as e:
            self.page.upload_parts("/huge.bin", __import__("io").BytesIO(data), len(data), part=part)
        self.assertEqual(e.exception.status, 507)
        self.assertIn("no room", str(e.exception))
        self.assertEqual(os.listdir(os.path.join(self.host.cfg.resolved_data_dir, "partials")), [])
        self.assertIsNone(self.host.index.resolve("/huge.bin"))

    def test_the_commands(self):
        os.environ["DDRIVE_LITE_CONFIG"] = os.path.join(self.tmp, "lite.json")
        self.addCleanup(os.environ.pop, "DDRIVE_LITE_CONFIG")
        s = {"host": self.address, "sync": True, "device": "aaaaaaaaaaaaaaaa", "mount_point": "",
             "protected": lite.secretbox.protect(json.dumps({"phrase": self.phrase}).encode())}
        lite.save_settings(s)
        keep = litesync.default_data_dir
        litesync.default_data_dir = lambda: os.path.join(self.tmp, "lite-data")
        self.addCleanup(setattr, litesync, "default_data_dir", keep)
        sup = litesync.Supervisor()
        self.addCleanup(sup._release)
        self.assertTrue(sup.check())                                   # on: it runs
        self.assertEqual(litesync.running_pid(), os.getpid())
        self.until(lambda: self.device(device="aaaaaaaaaaaaaaaa"), "the computer showing up")
        lite.save_settings(dict(s, sync=False))
        self.assertFalse(sup.check())                                  # off: it stops, and says so to the host
        self.assertIsNone(sup.agent)
        self.assertIsNone(litesync.running_pid())
        self.assertIsNone(self.device(device="aaaaaaaaaaaaaaaa"))


class LiteSyncAccountTest(helpers.DriveTest):
    """Somebody with an account syncs folders into their own storage; the owner sees nothing of it."""

    def test_an_account_and_the_owner_are_apart(self):
        self.earlier_accounts()
        host = self.drive(web_token="secret-token")
        web = host.start_web(port=0)
        self.addCleanup(web.stop)
        host.cfg.signup_mode = "public"
        phrase, ticket = web.accounts.offer("", "127.0.0.1")
        web.accounts.create(phrase, ticket, "", "127.0.0.1")
        address = f"http://127.0.0.1:{web.port}"
        owner = lite.HostClient(address, words.key_to_phrase(bytes.fromhex(host.cfg.encryption_key)))
        mine = lite.HostClient(address, phrase)
        here = os.path.join(self.tmp, "here")
        os.makedirs(here)
        with open(os.path.join(here, "a.txt"), "wb") as f:
            f.write(b"an account's file")
        os.utime(os.path.join(here, "a.txt"), (time.time() - 100,) * 2)
        agent = litesync.Agent(lite.HostClient(address, phrase), DEVICE, "theirs", data_dir=os.path.join(self.tmp, "lite")).start()
        self.addCleanup(agent.stop)
        end = time.time() + 20
        while not mine._json("/api/sync")["devices"] and time.time() < end:
            time.sleep(0.05)
        page = mine._json("/api/sync")
        self.assertEqual((page["account"], page["jobs"], page["host"], [d["name"] for d in page["devices"]]), (True, [], "", ["theirs"]))
        self.assertEqual(owner._json("/api/sync")["devices"], [])
        for call in (lambda: owner._json("/api/sync/save", {"device": DEVICE, "job": {"local": here, "remote": "/X"}}),
                     lambda: owner.request("GET", f"/api/sync/browse?device={DEVICE}")):
            with self.assertRaises(lite.HostError) as e:
                call()
            self.assertEqual(e.exception.status, 404)
        with self.assertRaises(lite.HostError) as e:           # the host's own folders are the owner's
            mine._json("/api/sync/save", {"job": {"local": here, "remote": "/X"}})
        self.assertEqual(e.exception.status, 503)
        with self.assertRaises(lite.HostError) as e:
            mine.request("GET", "/api/sync/browse?path=")
        self.assertEqual(e.exception.status, 503)
        job = mine._json("/api/sync/save", {"device": DEVICE, "job": {"local": here, "remote": "/Mine", "trigger": "manual"}})["job"]
        end = time.time() + 20
        while time.time() < end and not ((agent.status.get(job["id"]) or {}).get("last_ok")):
            if agent.job(job["id"]) and agent.running is None and not agent._requests:
                mine._json("/api/sync/run", {"device": DEVICE, "id": job["id"]})
            time.sleep(0.3)
        self.assertEqual(mine.read("/Mine/a.txt", 0, 100), b"an account's file")
        self.assertEqual(owner.list("/"), [])


class RulesTest(helpers.unittest.TestCase):
    def test_a_folder_somewhere_else_is_not_looked_at(self):
        job = sync.normalize_job({"local": ' "C:\\Users\\me\\Downloads" ', "remote": "Z:\\Down", "mode": "MOVE"}, None, elsewhere=True)
        self.assertEqual((job["local"], job["remote"], job["name"], job["mode"]), ("C:\\Users\\me\\Downloads", "/Down", "Downloads", "move"))
        self.assertEqual(sync.normalize_job({"local": "/home/me/Docs/", "remote": "/"}, None, elsewhere=True)["name"], "Docs")
        for bad in ({"local": "/", "remote": "/A", "mode": "move"}, {"local": "D:", "remote": "/A", "mode": "download-mirror"},
                    {"local": "/a", "remote": "/A", "at": "25:00"}, {"local": "", "remote": "/A"}):
            with self.assertRaises(sync.SyncError):
                sync.normalize_job(bad, None, elsewhere=True)

    def test_the_hosts_list(self):
        devs = syncdevices.LiteDevices()
        with self.assertRaises(sync.SyncError):
            devs.hello("", "../etc", {})
        with self.assertRaises(syncdevices.Gone):
            devs.poll("", DEVICE, 0, 0)
        got = devs.hello("", DEVICE, {"name": "pc", "rev": 4, "jobs": [{"id": "j1", "local": "/a", "remote": "/A"}, {"id": "bad"}, "x"]})
        self.assertEqual((got["rev"], [j["id"] for j in got["jobs"]]), (4, ["j1"]))
        self.assertEqual(devs.poll("", DEVICE, 4, 0)["commands"], [])
        with self.assertRaises(syncdevices.Gone):              # filed under who signed it in
            devs.poll("someone", DEVICE, 4, 0)
        self.assertEqual(devs.listing("someone"), [])
        devs.pause("", DEVICE, "j1", True)
        got = devs.poll("", DEVICE, 4, 5)                      # answered at once: something changed
        self.assertEqual((got["rev"], got["jobs"][0]["enabled"], got["commands"]), (5, False, [{"do": "stop", "id": "j1"}]))
        self.assertEqual(devs.hello("", DEVICE, {"rev": 4, "jobs": []})["jobs"][0]["id"], "j1")     # ours is newer
        self.assertEqual(devs.hello("", DEVICE, {"rev": 9, "jobs": []})["jobs"], [])                 # theirs is
        devs.bye("", DEVICE)
        self.assertEqual(devs.listing(""), [])


if __name__ == "__main__":
    helpers.unittest.main()
