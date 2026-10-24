"""Live upload into an end-to-end encrypted account through Lite (litelive.py): a recorder sends to Lite's
endpoint on this computer while it records, Lite seals it a piece at a time and sends the sealed bytes to
the host as an object that grows (web.py, ?grow=1); the account gets the file when the recording ends.
The host is a whole drive with its dashboard on a free local port; Lite's endpoint too."""

import http.client
import json
import os
from unittest import mock

import helpers
import test_vault
from ddrive import lite, litelive, vault

KEY = "recorder-test-key"


class LiteLiveTest(test_vault.VaultTest):
    def setUp(self):
        super().setUp()
        self.recs = litelive.VaultRecordings(self.new_device)
        self.server = litelive.RecorderServer(self.recs, lambda: KEY, port=0).start()

    def tearDown(self):
        self.server.stop()
        super().tearDown()

    def rec(self, path, body=b"", key=KEY, query=""):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=60)
        conn.request("POST", path + query, body, {"Authorization": f"Bearer {key}"})
        r = conn.getresponse()
        out = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, out

    def test_a_recording_is_sealed_into_the_account_while_it_is_made(self):
        data = os.urandom(3 * litelive.PIECE + 12345)
        st, opened = self.rec("/api/rec/open", json.dumps({"folder": "Discord Calls", "name": "call.webm"}).encode())
        self.assertEqual(st, 200, opened)
        rid, at = opened["id"], 0
        step = 300_000
        while at < len(data):                     # (one part sent twice, and one too far on, on the way)
            part = data[at:at + step]
            st, got = self.rec("/api/rec/data", part, query=f"?id={rid}&offset={at}")
            self.assertEqual((st, got["ok"], got["offset"]), (200, True, at + len(part)))
            if at == step:
                self.assertEqual(self.rec("/api/rec/data", part, query=f"?id={rid}&offset={at}")[1]["offset"], at + len(part))
                self.assertFalse(self.rec("/api/rec/data", b"x", query=f"?id={rid}&offset={at + len(part) + 10}")[1]["ok"])
            at += len(part)
        st, closed = self.rec("/api/rec/close", query=f"?id={rid}&offset={at}&wait=60")
        self.assertEqual(st, 200, closed)
        self.assertTrue(closed["ok"] and closed["done"], closed)
        self.assertEqual((closed["path"], closed["size"]), ("/Recordings/Discord Calls/call.webm", len(data)))
        other = self.new_device()                 # another device of the account opens it
        self.assertEqual(other.read("/Recordings/Discord Calls/call.webm"), data)
        node = other.resolve("/Recordings/Discord Calls/call.webm")
        self.assertEqual(node["size"], len(data))
        # the host has only the sealed object, of the size a file sealed at once would have
        d = self.account_drive()
        try:
            obj = d.index.resolve(f"/o/{node['obj'][:2]}/{node['obj']}")
            self.assertEqual(obj["size"], vault.sealed_size(len(data)))
        finally:
            self.web.accounts.release(d)

    def test_a_second_recording_of_that_name_and_a_whole_piece_at_the_end(self):
        for _ in range(2):
            data = os.urandom(2 * litelive.PIECE)  # (ends with a whole piece: that one is the last)
            rid = self.rec("/api/rec/open", json.dumps({"name": "a.webm"}).encode())[1]["id"]
            self.assertTrue(self.rec("/api/rec/data", data, query=f"?id={rid}&offset=0")[1]["ok"])
            closed = self.rec("/api/rec/close", query=f"?id={rid}&offset={len(data)}&wait=60")[1]
            self.assertTrue(closed["done"], closed)
            self.assertEqual(self.new_device().read(closed["path"]), data)
        self.assertEqual(sorted(n["name"] for n in self.new_device().list("/Recordings")), ["a (2).webm", "a.webm"])

    def test_a_wrong_key_and_nothing_recorded(self):
        self.assertEqual(self.rec("/api/rec/open", b"{}", key="wrong")[0], 401)
        rid = self.rec("/api/rec/open", json.dumps({"name": "empty.webm"}).encode())[1]["id"]
        closed = self.rec("/api/rec/close", query=f"?id={rid}&offset=0&wait=30")[1]
        self.assertTrue(closed["done"])
        self.assertIsNone(self.new_device().resolve("/Recordings/empty.webm"))       # no empty file is left
        self.assertEqual(self.rec("/api/rec/data", b"x", query=f"?id={rid}&offset=0")[0], 404)

    def test_the_host_takes_a_growing_object_only_in_order(self):
        client = lite.HostClient(self.address, auth=self.auth)
        oid = vault.new_id()
        got = json.loads(client.request("POST", f"/api/vault/object?id={oid}&offset=0&grow=1", b"a" * 10))
        self.assertEqual((got["ok"], got["offset"]), (True, 10))
        got = json.loads(client.request("POST", f"/api/vault/object?id={oid}&offset=20&grow=1", b"b"))
        self.assertEqual((got["ok"], got["offset"]), (False, 10))
        got = json.loads(client.request("POST", f"/api/vault/object?id={oid}&offset=10&grow=1&end=1", b"c" * 5))
        self.assertEqual((got["ok"], got.get("done"), got["size"]), (True, True, 15))


# The host and the account as the vault tests have them, but not their tests (they run in test_vault).
for _name in dir(test_vault.VaultTest):
    if _name.startswith("test") and _name not in LiteLiveTest.__dict__:
        setattr(LiteLiveTest, _name, None)


class LiteCommandTest(helpers.unittest.TestCase):
    def test_the_recorder_key_is_made_and_turned_off(self):
        settings = {"host": "http://127.0.0.1:1", "e2e": True}
        with mock.patch.object(lite, "load_settings", lambda: dict(settings)), \
                mock.patch.object(lite, "save_settings", lambda s: settings.update(s) or [settings.pop(k) for k in list(settings) if k not in s]), \
                mock.patch.object(lite, "client_from", lambda s: None), \
                mock.patch.object(lite, "_recorder_up", lambda port: True), \
                mock.patch.object(lite, "autostart_enabled", lambda: True):
            self.assertEqual(lite.main(["recorder", "on"]), 0)
            key = settings["recorder_key"]
            self.assertTrue(key)
            self.assertEqual(lite.main(["recorder"]), 0)
            self.assertEqual(settings["recorder_key"], key)            # shown, not changed
            lite.main(["recorder", "new"])
            self.assertNotEqual(settings["recorder_key"], key)
            lite.main(["recorder", "off"])
            self.assertNotIn("recorder_key", settings)
