"""Recordings written live into the drive (liverec.py) and the recorder's part of the web API."""

import http.client
import json
import os
import time
from unittest import mock

import helpers
from ddrive import liverec


class LiveRecTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.web.save_cfg(recorder_key="rec-key")

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def req(self, method, path, body=None, key="rec-key", cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=30)
        h = {}
        if key:
            h["Authorization"] = "Bearer " + key
        if cookie:
            h["Cookie"], h["X-DD"] = cookie, "1"
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data or b"{}")

    def staged(self):
        folder = self.d.fs.staging_dir
        return [n for n in os.listdir(folder) if not n.startswith(".")]

    def test_a_recording_goes_to_discord_without_a_local_copy(self):
        st, opened = self.req("POST", "/api/rec/open", {"folder": "2026-10-23 Call", "name": "Call audio.webm"})
        self.assertEqual(st, 200, opened)
        self.assertEqual(opened["path"], "/Recordings/2026-10-23 Call/Call audio.webm")
        rid = opened["id"]
        data = os.urandom(5 * helpers.PIECE + 1234)
        pos = 0
        for size in (1000, 70000, 30000, 200000, 999999):
            part = data[pos:pos + size]
            for _ in range(200):                               # Discord is slower: the recorder waits a moment
                st, got = self.req("POST", f"/api/rec/data?id={rid}&offset={pos}", part)
                if not got.get("wait"):
                    break
                time.sleep(0.05)
            self.assertEqual((st, got["ok"]), (200, True), got)
            pos += len(part)
            self.assertEqual(self.staged(), [])                # nothing of it is on this computer's disk
            if pos >= len(data):
                break
        # the same part again changes nothing; a part after a gap is told where to go on from
        st, got = self.req("POST", f"/api/rec/data?id={rid}&offset=0", data[:1000])
        self.assertEqual(got["offset"], len(data))
        st, got = self.req("POST", f"/api/rec/data?id={rid}&offset={len(data) + 10}", b"x")
        self.assertEqual((got["ok"], got["offset"]), (False, len(data)))
        st, got = self.req("POST", f"/api/rec/close?id={rid}&offset={len(data)}&wait=30")
        self.assertEqual((st, got["ok"], got["done"], got["size"]), (200, True, True, len(data)), got)
        node = self.d.index.resolve(opened["path"])
        self.assertEqual(node["size"], len(data))
        self.assertEqual(len(self.d.index.get_chunks(node["id"])), 6)
        self.assertEqual(self.d.fs.read_file(node["id"], 0, len(data)), data)
        self.assertEqual(self.d.index.versions_for(node["uid"]), [])     # growing kept no earlier versions
        self.assertEqual(self.staged(), [])
        # gone after it was closed
        self.assertEqual(self.req("POST", f"/api/rec/data?id={rid}&offset={len(data)}", b"more")[0], 404)

    def test_the_file_grows_while_it_is_recorded(self):
        with mock.patch.object(liverec, "SAVE_EVERY", 0):
            opened = self.web.recordings.open("", "Screen.webm")
            data = os.urandom(3 * helpers.PIECE)
            self.web.recordings.data(opened["id"], 0, data[:2 * helpers.PIECE + 5])
            node = None
            for _ in range(100):
                node = self.d.index.resolve("/Recordings/Screen.webm")
                if node and node["size"] == 2 * helpers.PIECE:
                    break
                time.sleep(0.05)
            self.assertEqual(node["size"], 2 * helpers.PIECE)       # two whole pieces are on the drive already
            self.web.recordings.data(opened["id"], 2 * helpers.PIECE + 5, data[2 * helpers.PIECE + 5:])
            self.assertTrue(self.web.recordings.close(opened["id"], wait=30)["done"])
        self.assertEqual(self.d.fs.read_file(node["id"], 0, len(data)), data)

    def test_names_never_leave_the_recordings_folder(self):
        opened = self.web.recordings.open("../../etc", "../passwd")
        self.assertTrue(opened["path"].startswith("/Recordings/"), opened)
        self.assertNotIn("..", opened["path"].split("/"))
        second = self.web.recordings.open("../../etc", "../passwd")
        self.assertNotEqual(opened["path"], second["path"])         # a name in use gets " (2)"
        for o in (opened, second):                                  # nothing recorded: no empty file stays
            self.assertTrue(self.web.recordings.close(o["id"], wait=10)["done"])
            self.assertIsNone(self.d.index.resolve(o["path"]))

    def test_only_the_recorder_key_opens_it(self):
        self.assertEqual(self.req("POST", "/api/rec/open", {"name": "x"}, key="wrong")[0], 401)
        self.assertEqual(self.req("POST", "/api/rec/open", {"name": "x"}, key=None)[0], 401)
        self.web.save_cfg(recorder_key="")
        self.assertEqual(self.req("POST", "/api/rec/open", {"name": "x"}, key="")[0], 401)
        # and the key opens nothing else of the drive
        self.web.save_cfg(recorder_key="rec-key")
        self.assertEqual(self.req("GET", "/api/list?path=/")[0], 401)

    def test_owner_makes_and_turns_off_the_key(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        from ddrive import words
        phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))
        conn.request("POST", "/login", body=f"phrase={phrase.replace(' ', '+')}",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        r.read()
        cookie = r.getheader("Set-Cookie").split(";")[0]
        st, got = self.req("POST", "/api/recorder", {"key": "new", "folder": "Calls/Discord"}, key=None, cookie=cookie)
        self.assertEqual(st, 200, got)
        self.assertEqual(got["folder"], "/Calls/Discord")
        self.assertGreater(len(got["key"]), 30)
        self.assertEqual(self.req("POST", "/api/rec/open", {"name": "a.webm"}, key=got["key"])[1]["path"], "/Calls/Discord/a.webm")
        st, got = self.req("POST", "/api/recorder", {"key": "off"}, key=None, cookie=cookie)
        self.assertEqual(got["key"], "")
        self.assertEqual(self.req("POST", "/api/rec/open", {"name": "b.webm"}, key="rec-key")[0], 401)


if __name__ == "__main__":
    import unittest
    unittest.main()
