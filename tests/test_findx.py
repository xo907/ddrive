"""FindX's live relay (findx.py) and its routes (findxweb.py): sealed things passed on unread."""

import base64
import json
import os
import threading
import time
import unittest

import helpers
from ddrive import findx
from ddrive.findx import Relay, RelayError
from ddrive.findxcircles import Circles
import http.client
import urllib.parse

from ddrive import words

G = "a" * 32            # a group (made from the words on the phones)
G2 = "b" * 32
D1 = "1" * 32           # devices
D2 = "2" * 32
S1 = "5" * 32           # a link


def sealed(n=40):
    """Something that looks like a sealed message (the relay never opens it)."""
    return base64.b64encode(os.urandom(n)).decode()


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.r = Relay(clock=self.clock)

    def test_devices_and_reports_are_kept_apart_by_account(self):
        card, rep = sealed(), sealed()
        self.r.hello("", G, D1, card)
        self.r.report("", G, D1, rep)
        got = self.r.devices("", G)
        self.assertEqual([(d["device"], d["card"], d["report"]) for d in got], [(D1, card, rep)])
        # Another account naming the same group sees nothing of it.
        self.assertEqual(self.r.devices("acct1", G), [])
        self.r.hello("acct1", G, D2, sealed())
        self.assertEqual([d["device"] for d in self.r.devices("acct1", G)], [D2])
        self.assertEqual([d["device"] for d in self.r.devices("", G)], [D1])
        # ...and can't send its devices commands.
        with self.assertRaises(RelayError) as e:
            self.r.command("acct1", G, D1, sealed())
        self.assertEqual(e.exception.status, 404)

    def test_a_device_without_a_card_is_not_listed(self):
        self.r.report("", G, D1, sealed())
        self.assertEqual(self.r.devices("", G), [])

    def test_commands_reach_only_their_device_in_order(self):
        for d in (D1, D2):
            self.r.hello("", G, d, sealed())
        a, b = sealed(), sealed()
        s1 = self.r.command("", G, D1, a)
        self.r.command("", G, D2, sealed())
        s2 = self.r.command("", G, D1, b)
        got, _ = self.r.wait("", G, D1, 0, 0)
        self.assertEqual([c["blob"] for c in got], [a, b])
        got, _ = self.r.wait("", G, D1, s1, 0)
        self.assertEqual([c["seq"] for c in got], [s2])
        self.assertEqual(self.r.wait("", G, D1, s2, 0)[0], [])
        # Old commands are dropped (the phone would ignore them anyway).
        self.clock.t += findx.COMMAND_KEEP_S + 1
        self.assertEqual(self.r.wait("", G, D1, 0, 0)[0], [])

    def test_a_waiting_phone_wakes_when_a_command_comes(self):
        self.r.hello("", G, D1, sealed())
        out = {}

        def wait():
            t0 = time.monotonic()
            out["got"] = self.r.wait("", G, D1, 0, 30)
            out["took"] = time.monotonic() - t0

        th = threading.Thread(target=wait)
        th.start()
        time.sleep(0.3)
        blob = sealed()
        self.r.command("", G, D1, blob)
        th.join(5)
        self.assertFalse(th.is_alive())
        self.assertEqual([c["blob"] for c in out["got"][0]], [blob])
        self.assertLess(out["took"], 5)

    def test_a_waiting_phone_hears_when_its_map_is_opened(self):
        self.r.hello("", G, D1, sealed())
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("got", self.r.wait("", G, D1, 0, 30)))
        th.start()
        time.sleep(0.3)
        self.r.devices("", G, watch=D1)                     # the dashboard's map of it is open
        th.join(5)
        self.assertFalse(th.is_alive())
        self.assertEqual(out["got"], ([], True))
        # A phone already told doesn't come straight back again and again: the next wait waits.
        t0 = time.monotonic()
        self.assertEqual(self.r.wait("", G, D1, 0, 0.4), ([], True))
        self.assertGreater(time.monotonic() - t0, 0.3)

    def test_a_newer_wait_ends_an_older_one(self):
        self.r.hello("", G, D1, sealed())
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("got", self.r.wait("", G, D1, 0, 30)))
        th.start()
        time.sleep(0.3)
        threading.Thread(target=lambda: self.r.wait("", G, D1, 0, 1), daemon=True).start()
        time.sleep(0.3)
        self.r.report("", G, D1, sealed())                 # (anything that wakes the waits)
        th.join(5)
        self.assertFalse(th.is_alive())

    def test_watching_is_said_in_reports_and_runs_out(self):
        self.r.hello("", G, D1, sealed())
        self.assertEqual(self.r.report("", G, D1, sealed()), (False, []))
        self.r.devices("", G, watch=D1)
        self.assertEqual(self.r.report("", G, D1, sealed()), (True, []))
        self.clock.t += findx.WATCH_S + 1
        self.assertEqual(self.r.report("", G, D1, sealed()), (False, []))

    def test_links(self):
        self.r.hello("", G, D1, sealed())
        with self.assertRaises(RelayError) as e:
            self.r.shared(S1)
        self.assertEqual(e.exception.status, 404)
        until = int((self.clock.t + 3600) * 1000)
        self.r.share("", G, D1, S1, until)
        self.assertEqual(self.r.shared(S1), {"until": until, "at": 0})       # nothing sent yet
        mine, other = sealed(), sealed()
        watching, open_links = self.r.report("", G, D1, sealed(), {S1: mine, "6" * 32: other})
        self.assertEqual(open_links, [S1])                  # the link's page is open
        self.assertTrue(watching)                           # (so the phone pins itself down once)
        got = self.r.shared(S1)
        self.assertEqual(got["blob"], mine)
        # Another account can't stop it, or take its id.
        with self.assertRaises(RelayError):
            self.r.unshare("acct1", G, S1)
        with self.assertRaises(RelayError) as e:
            self.r.share("acct1", G, D1, S1, 0)
        self.assertEqual(e.exception.status, 409)
        self.r.unshare("", G, S1)
        with self.assertRaises(RelayError) as e:
            self.r.shared(S1)
        self.assertEqual(e.exception.status, 410)
        with self.assertRaises(RelayError) as e:            # a stopped link can't be started again
            self.r.share("", G, D1, S1, 0)
        self.assertEqual(e.exception.status, 410)

    def test_links_end_by_themselves(self):
        self.r.hello("", G, D1, sealed())
        self.r.share("", G, D1, S1, int((self.clock.t + 60) * 1000))
        self.r.shared(S1)
        self.clock.t += 61
        with self.assertRaises(RelayError) as e:
            self.r.shared(S1)
        self.assertEqual(e.exception.status, 410)
        # Until stopped (0): still there a year on.
        s2 = "7" * 32
        self.r.share("", G, D1, s2, 0)
        self.clock.t += 365 * 86400
        self.assertIn("until", self.r.shared(s2))

    def test_what_comes_in_is_checked(self):
        bad = [("not hex", D1, sealed()), (G, "short", sealed()), (G, D1, "not base64!"), (G, D1, base64.b64encode(b"x").decode()),
               (G, D1, base64.b64encode(os.urandom(findx.BLOB_MAX + 1)).decode())]
        for group, device, blob in bad:
            with self.assertRaises(RelayError):
                self.r.report("", group, device, blob)
        with self.assertRaises(RelayError) as e:
            self.r.report("", G, D1, sealed(), {"x": sealed()})
        self.assertEqual(e.exception.status, 400)

    def test_limits(self):
        for i in range(findx.DEVICES_MAX):
            self.r.hello("", G, "%032x" % i, sealed())
        with self.assertRaises(RelayError) as e:
            self.r.hello("", G, "f" * 32, sealed())
        self.assertEqual(e.exception.status, 429)
        for i in range(findx.GROUPS_MAX - 1):
            self.r.hello("", "%032x" % (i + 100), D1, sealed())
        with self.assertRaises(RelayError):
            self.r.hello("", "e" * 32, D1, sealed())
        # Another account has its own room.
        self.r.hello("acct1", "e" * 32, D1, sealed())

    def test_forgotten_after_a_long_silence(self):
        self.r.hello("", G, D1, sealed())
        self.clock.t += findx.DEVICE_KEEP_S + 120
        self.r.hello("", G2, D2, sealed())                  # (anything: sweeping happens on the way)
        self.assertEqual(self.r.devices("", G), [])


C1 = "c" * 32           # a circle
A1 = "d" * 32           # its invite's token
M1, M2, M3 = "e" * 32, "f" * 32, "0" * 32   # members


class CirclesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_circles_test.json")
        self.addCleanup(lambda: os.path.exists(self.tmp) and os.remove(self.tmp))
        self.clock = Clock()
        self.c = Circles(self.tmp, clock=self.clock)
        self.c.create("", C1, A1, sealed())

    def test_only_the_invited_get_in(self):
        self.c.report(C1, A1, M1, sealed())
        for circle, auth in ((C1, "9" * 32), ("9" * 32, A1), (C1, "short")):
            with self.assertRaises(RelayError):
                self.c.report(circle, auth, M2, sealed())
        with self.assertRaises(RelayError) as e:
            self.c.members(C1, "9" * 32)
        self.assertEqual(e.exception.status, 404)          # (a wrong token and no circle look the same)
        self.assertEqual([m["member"] for m in self.c.members(C1, A1)], [M1])

    def test_circles_survive_a_restart_but_not_whats_live(self):
        info = sealed()
        self.c.set_info(C1, A1, info)
        self.c.report(C1, A1, M1, sealed())
        again = Circles(self.tmp, clock=self.clock)
        self.assertEqual(again.info(C1, A1), {"info": info, "members": 0})
        with open(self.tmp) as f:
            self.assertNotIn(A1, f.read())                  # only a hash of the token is kept

    def test_ring_a_friend_and_tell_everybody(self):
        for m in (M1, M2, M3):
            self.c.report(C1, A1, m, sealed())
        ring, arrived = sealed(), sealed()
        self.c.command(C1, A1, M1, M2, ring)                # M1 rings M2
        self.c.command(C1, A1, M1, "*", arrived)            # M1 arrived somewhere: everybody hears it
        got = {m: [x["blob"] for x in self.c.wait(m, [{"circle": C1, "auth": A1, "after": 0}], 0)[0]] for m in (M1, M2, M3)}
        self.assertEqual(got[M2], [ring, arrived])
        self.assertEqual(got[M3], [arrived])
        self.assertEqual(got[M1], [])                       # not its own news
        with self.assertRaises(RelayError):
            self.c.command(C1, A1, M1, "9" * 32, sealed())

    def test_one_wait_for_all_circles_wakes_on_a_command(self):
        c2, a2 = "1" * 32, "2" * 32
        self.c.create("", c2, a2, sealed())
        self.c.report(c2, a2, M2, sealed())
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("got", self.c.wait(M2, [{"circle": C1, "auth": A1}, {"circle": c2, "auth": a2}], 30)))
        th.start()
        time.sleep(0.3)
        self.c.command(c2, a2, M1, M2, sealed())
        th.join(5)
        self.assertFalse(th.is_alive())
        self.assertEqual([x["circle"] for x in out["got"][0]], [c2])

    def test_a_map_of_the_circle_wakes_its_members(self):
        self.c.report(C1, A1, M1, sealed())
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("got", self.c.wait(M1, [{"circle": C1, "auth": A1}], 30)))
        th.start()
        time.sleep(0.3)
        self.c.members(C1, A1, watch=True)
        th.join(5)
        self.assertEqual(out["got"], ([], [C1]))
        self.assertTrue(self.c.report(C1, A1, M1, sealed()))   # and its reports hear it

    def test_making_and_deleting(self):
        with self.assertRaises(RelayError) as e:            # somebody else can't take the id
            self.c.create("acct1", C1, "9" * 32, sealed())
        self.assertEqual(e.exception.status, 409)
        self.c.create("", C1, A1, sealed())                  # made again by the same phone: fine
        with self.assertRaises(RelayError):
            self.c.delete("acct1", C1)                       # only by who made it
        self.c.delete("", C1)
        with self.assertRaises(RelayError):
            self.c.info(C1, A1)
        self.assertEqual(Circles(self.tmp).stats()["circles"], 0)

    def test_leaving_and_being_forgotten(self):
        self.c.report(C1, A1, M1, sealed())
        self.c.report(C1, A1, M2, sealed())
        self.c.leave(C1, A1, M1)
        self.assertEqual([m["member"] for m in self.c.members(C1, A1)], [M2])
        self.clock.t += 91 * 86400
        self.assertEqual(self.c.members(C1, A1), [])


class FindXWebTest(helpers.DriveTest):
    """Against a real dashboard on a free local port (as test_web.py, whose tests aren't run again here)."""

    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        self.cookie = None

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def req(self, method, path, body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        h = {"X-DD": "1"} if method == "POST" else {}
        h.update(headers or {})
        if auth and self.cookie:
            h["Cookie"] = self.cookie
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def login(self):
        st, h, _ = self.req("POST", "/login", urllib.parse.urlencode({"phrase": self.phrase}), auth=False,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 303)
        self.cookie = h["Set-Cookie"].split(";")[0]

    def post(self, path, body):
        st, _, data = self.req("POST", path, body)
        return st, json.loads(data or b"{}")

    def get(self, path, auth=True):
        st, h, data = self.req("GET", path, auth=auth)
        return st, h, data

    def test_relay_over_http(self):
        self.assertEqual(self.req("POST", "/api/findx/hello", {"group": G, "device": D1, "card": sealed()})[0], 401)
        self.login()
        card, rep = sealed(), sealed()
        self.assertEqual(self.post("/api/findx/hello", {"group": G, "device": D1, "card": card}), (200, {"ok": True}))
        st, got = self.post("/api/findx/report", {"group": G, "device": D1, "blob": rep, "shares": {}})
        self.assertEqual((st, got), (200, {"ok": True, "watching": False, "shareWatching": []}))
        st, _, data = self.get(f"/api/findx/devices?group={G}&watch={D1}")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["devices"][0]["report"], rep)
        st, got = self.post("/api/findx/command", {"group": G, "to": D1, "blob": sealed()})
        self.assertEqual(st, 200)
        st, _, data = self.get(f"/api/findx/wait?group={G}&device={D1}&after=0&wait=0")
        got = json.loads(data)
        self.assertEqual(len(got["commands"]), 1)
        self.assertTrue(got["watching"])
        # A POST without the dashboard's header (another site's form) is refused.
        self.assertEqual(self.req("POST", "/api/findx/hello", {"group": G, "device": D1, "card": card}, headers={"X-DD": "0"})[0], 403)
        self.assertEqual(self.post("/api/findx/report", {"group": "x", "device": D1, "blob": rep})[0], 400)
        self.assertEqual(self.post("/api/findx/nothing", {})[0], 404)

    def test_link_page_and_report_need_nobody_signed_in(self):
        self.login()
        self.post("/api/findx/hello", {"group": G, "device": D1, "card": sealed()})
        self.assertEqual(self.post("/api/findx/share", {"group": G, "device": D1, "share": S1, "until": 0})[0], 200)
        blob = sealed()
        self.post("/api/findx/report", {"group": G, "device": D1, "blob": sealed(), "shares": {S1: blob}})
        st, h, data = self.get(f"/f/{S1}", auth=False)
        self.assertEqual(st, 200)
        self.assertIn(b"findx-share.js", data)
        self.assertIn("https://tiles.openfreemap.org", h["Content-Security-Policy"])
        self.assertIn("noindex", h["X-Robots-Tag"])
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])   # nobody may frame a link's page
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        st, _, data = self.get(f"/api/findx/shared?share={S1}", auth=False)
        self.assertEqual((st, json.loads(data)["blob"]), (200, blob))
        self.assertEqual(self.get("/api/findx/shared?share=" + "9" * 32, auth=False)[0], 404)
        self.assertEqual(self.get("/api/findx/shared?share=../etc", auth=False)[0], 404)
        self.post("/api/findx/unshare", {"group": G, "share": S1})
        self.assertEqual(self.get(f"/api/findx/shared?share={S1}", auth=False)[0], 410)
        # The page's files are there for anybody; nothing else of the folder is.
        st, h, data = self.get("/findx/findx-map.js", auth=False)
        self.assertEqual(st, 200)
        self.assertIn(b"FindX map", data)
        self.assertEqual(self.get("/findx/vendor/maplibre-gl.js", auth=False)[0], 200)
        for path in ("/findx/../web.py", "/findx/%2e%2e/web.py", "/findx/index.html", "/findx/nothing.js", "/f/short"):
            self.assertIn(self.get(path, auth=False)[0], (401, 404), path)

    def test_map_page_is_signed_in_only(self):
        st, h, _ = self.get("/findx/", auth=False)
        self.assertEqual((st, h.get("Location")), (303, "/"))
        self.login()
        st, h, data = self.get("/findx/")
        self.assertEqual(st, 200)
        self.assertIn(b"findx-dashboard.js", data)
        self.assertIn(b"/e2e-api.js", data)                 # an end-to-end account's keys open things in the page
        self.assertIn("worker-src blob:", h["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"])   # the dashboard's FindX tab frames it
        st, _, data = self.get("/")
        self.assertIn(b'href="#/findx"', data)              # a tab in the dashboard's menu

    def test_circles_over_http(self):
        # Making one needs somebody signed in to this drive...
        st, _ = self.post("/api/findx/circle/create", {"circle": C1, "auth": A1, "info": sealed()})
        self.assertEqual(st, 401)
        self.login()
        self.assertEqual(self.post("/api/findx/circle/create", {"circle": C1, "auth": A1, "info": sealed()})[0], 200)
        # ...being in one needs only the invite's token (a friend with no account here).
        self.cookie = None
        st, got = self.post("/api/findx/c/report", {"circle": C1, "auth": A1, "member": M1, "blob": sealed()})
        self.assertEqual((st, got["ok"]), (200, True))
        self.assertEqual(self.post("/api/findx/c/report", {"circle": C1, "auth": "9" * 32, "member": M1, "blob": sealed()})[0], 404)
        st, got = self.post("/api/findx/c/members", {"circle": C1, "auth": A1, "watch": True})
        self.assertEqual([m["member"] for m in got["members"]], [M1])
        self.assertEqual(self.post("/api/findx/c/command", {"circle": C1, "auth": A1, "from": M2, "to": M1, "blob": sealed()})[0], 200)
        st, got = self.post("/api/findx/c/wait", {"member": M1, "wait": 0, "circles": [{"circle": C1, "auth": A1, "after": 0}]})
        self.assertEqual((len(got["commands"]), got["watching"]), (1, [C1]))
        self.assertEqual(self.post("/api/findx/c/nothing", {})[0], 401)
        st, h, data = self.get("/j/" + C1, auth=False)
        self.assertEqual(st, 200)
        self.assertIn(b"findx-join.js", data)
        # The circle is still there after the drive restarts (where its members are isn't).
        self.web.stop()
        self.web = self.d.start_web(port=0)
        st, got = self.post("/api/findx/c/info", {"circle": C1, "auth": A1})
        self.assertEqual((st, got["members"]), (200, 0))

    def test_the_public_page_is_paced(self):
        old = findxweb_pace().per_min
        findxweb_pace()._seen.clear()                       # (other tests asked from this address this minute too)
        findxweb_pace().per_min = 3
        try:
            codes = [self.get("/api/findx/shared?share=" + "9" * 32, auth=False)[0] for _ in range(5)]
        finally:
            findxweb_pace().per_min = old
        self.assertEqual(codes[:3], [404] * 3)
        self.assertEqual(codes[-1], 429)


def findxweb_pace():
    from ddrive import findxweb
    return findxweb._pace


if __name__ == "__main__":
    unittest.main()
