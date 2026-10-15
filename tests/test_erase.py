"""Erase everything: every file and account, here and in every channel; the bots and channels stay, and the
drive starts again empty in the same channels. Resumable, and only the owner's to ask for."""

import http.client
import json
import os
import time
import urllib.parse

import helpers

from ddrive import erase, words

SPARE = "111"


def messages(folder):
    return [n for n in os.listdir(folder) if n.endswith(".json") and n[:-5].isdigit()] if os.path.isdir(folder) else []


class EraseTest(helpers.DriveTest):
    def filled(self, **settings):
        d = self.drive(spare_channels=[SPARE], **settings)
        self.write(d, "/Docs/a.txt", b"hello")
        self.write(d, "/Big/b.bin", os.urandom(helpers.PIECE * 3))
        self.upload(d)
        d.journal.checkpoint(force=True)
        self.assertTrue(messages(self.channel) and messages(f"{self.channel}-{SPARE}"))
        return d

    def test_everything_goes_and_the_drive_starts_empty(self):
        d = self.filled()
        data_dir = d.cfg.resolved_data_dir
        d.stop_background_services()
        d.index.close()
        er = erase.Eraser(d.cfg, d.backend, save_config=False)
        self.assertTrue(er.run(local=True))
        self.assertEqual(messages(self.channel), [])
        self.assertEqual(messages(f"{self.channel}-{SPARE}"), [])
        self.assertFalse(os.path.exists(os.path.join(data_dir, "index.db")))
        self.assertFalse(erase.pending(d.cfg))
        self.drives.remove(d)
        again = self.drive(spare_channels=[SPARE])                 # same channels, same words: empty
        self.assertIsNone(again.index.resolve("/Docs/a.txt"))
        self.write(again, "/new.txt", b"fresh")
        self.upload(again)
        self.assertEqual(self.read(again, "/new.txt"), b"fresh")

    def test_an_interrupted_erase_is_finished_at_the_next_start(self):
        d = self.filled()
        d.stop_background_services()
        d.index.close()
        before = set(messages(self.channel)) | set(messages(f"{self.channel}-{SPARE}"))
        erase.request(d.cfg)                                        # asked for, then the computer went off
        self.drives.remove(d)
        again = self.drive(spare_channels=[SPARE])
        self.assertFalse(erase.pending(again.cfg))
        self.assertIsNone(again.index.resolve("/Docs/a.txt"))
        after = set(messages(self.channel)) | set(messages(f"{self.channel}-{SPARE}"))
        self.assertFalse(before & after)                            # (only the new, empty drive's own checkpoint)


class EraseFromTheDashboardTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(spare_channels=[SPARE], web_token="secret-token")
        self.write(self.d, "/a.txt", b"hello")
        self.upload(self.d)
        self.web = self.d.start_web(port=0)
        self.web.after_erase = lambda: None                          # (a test doesn't stop its own process)
        phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        status, headers, _ = self.req("POST", "/login", urllib.parse.urlencode({"phrase": phrase}))
        self.owner = headers["Set-Cookie"].split(";")[0]

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def req(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        h = {"Cookie": cookie} if cookie else {}
        if method == "POST":
            h["X-DD"] = "1"
            if isinstance(body, str):
                body, h["Content-Type"] = body.encode(), "application/x-www-form-urlencoded"
            elif isinstance(body, dict):
                body, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), data

    def test_the_owner_types_it_and_the_channels_are_emptied(self):
        self.assertEqual(self.req("POST", "/api/erase", {"confirm": "erase"}, self.owner)[0], 400)
        self.assertEqual(self.req("POST", "/api/erase", {"confirm": erase.CONFIRM})[0], 401)     # signed out
        self.assertTrue(messages(self.channel))
        self.assertEqual(self.req("POST", "/api/erase", {"confirm": erase.CONFIRM}, self.owner)[0], 200)
        end = time.time() + 30
        while time.time() < end:
            st = json.loads(self.req("GET", "/api/erase", cookie=self.owner)[2])["state"]
            if st.get("phase") == "restart" or st.get("error"):
                break
            time.sleep(0.1)
        self.assertEqual(st.get("phase"), "restart", st)
        self.assertEqual(messages(self.channel), [])
        self.assertEqual(messages(f"{self.channel}-{SPARE}"), [])
        self.assertTrue(erase.pending(self.d.cfg))                   # this computer's data goes at the next start
