"""Telling the owner about a newer DDrive (updates.py): what GitHub says is newest, what is new since this
version, and the dashboard's "Update now" (which a test drive refuses: it is never updated)."""

import json
import sys
import unittest

import helpers
from ddrive import __version__, updates

CHANGELOG = """# Changelog

## 99.1.0 (2099-01-02)

- **Newest.** Something new.

## 99.0.0 (2099-01-01)

- Something before it.

## 1.0.0 (2020-01-01)

- Long ago.
"""


class Fetcher:
    def __init__(self, version="99.1.0", findx="9.9.9", fail=False):
        self.version, self.findx, self.fail, self.asked = version, findx, fail, []

    def __call__(self, url, timeout=15):
        self.asked.append(url)
        if self.fail:
            raise OSError("no network")
        if url.endswith("ddrive/__init__.py"):
            return f'"""DDrive"""\n\n__version__ = "{self.version}"\n'
        if url.endswith("findx/release/VERSION"):
            return self.findx + "\n"
        if url.endswith("android/release/VERSION"):
            return "99.0.0\n"
        if url.endswith("CHANGELOG.md"):
            return CHANGELOG
        raise OSError("not found")


class VersionTest(unittest.TestCase):
    def test_versions_compare_as_numbers(self):
        self.assertTrue(updates.newer("3.10.0", "3.9.9"))
        self.assertFalse(updates.newer("3.9.0", "3.10.0"))
        self.assertFalse(updates.newer("3.10.0", "3.10.0"))
        self.assertTrue(updates.newer("4.0", "3.99.99"))
        self.assertFalse(updates.newer("nonsense", "1.0.0"))

    def test_whats_new_is_what_came_after_this_version(self):
        notes = updates.notes_between(CHANGELOG, "1.0.0", "99.1.0")
        self.assertIn("99.1.0", notes)
        self.assertIn("99.0.0", notes)
        self.assertNotIn("Long ago", notes)
        self.assertNotIn("99.0.0", updates.notes_between(CHANGELOG, "99.0.0", "99.1.0"))


class CheckTest(unittest.TestCase):
    def test_a_newer_version_is_told_with_its_notes(self):
        f = Fetcher()
        u = updates.Updates(object(), fetch=f)
        self.assertEqual(u.check(), "99.1.0")
        st = u.status()
        self.assertEqual((st["latest"], st["current"], st["findx"], st["app"]), ("99.1.0", __version__, "9.9.9", "99.0.0"))
        self.assertEqual(u.apps()["app"], "99.0.0")
        self.assertIn("Newest", st["notes"])
        self.assertIn(st["kind"], ("git", "exe", "deb", "other"))
        self.assertTrue(all(a.startswith("https://raw.githubusercontent.com/xo907/ddrive/main/") for a in f.asked))

    def test_nothing_is_said_when_this_is_the_newest(self):
        u = updates.Updates(object(), fetch=Fetcher(version=__version__))
        u.check()
        self.assertNotIn("latest", u.status())

    def test_no_network_is_no_problem(self):
        u = updates.Updates(object(), fetch=Fetcher(fail=True))
        self.assertIsNone(u.check())
        self.assertNotIn("latest", u.status())
        self.assertIn("no network", u.error)

    def test_it_can_be_turned_off(self):
        cfg = type("Cfg", (), {"update_check": False})()
        self.assertFalse(updates.Updates(cfg).enabled())
        self.assertTrue(updates.Updates(object()).enabled())


class WebUpdateTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.web.updates._fetch = Fetcher()
        import http.client, urllib.parse
        from ddrive import words
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        conn.request("POST", "/login", urllib.parse.urlencode({"phrase": words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); r.read(); conn.close()
        self.cookie = r.getheader("Set-Cookie").split(";")[0]

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def call(self, method, path, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=20)
        h = {"Cookie": self.cookie, "X-DD": "1"}
        if body is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data or b"{}")

    def test_the_owner_hears_of_a_newer_version_and_a_test_drive_isnt_updated(self):
        st, got = self.call("POST", "/api/update/check", {})
        self.assertEqual((st, got["latest"]), (200, "99.1.0"))
        st, got = self.call("GET", "/api/status")
        self.assertEqual(got["update"]["latest"], "99.1.0")
        st, got = self.call("POST", "/api/update/install", {})
        self.assertEqual(st, 400)                        # (a test drive, and not installed with git anyway here)
        st, got = self.call("GET", "/api/settings")
        self.assertIs(got["values"]["update_check"], True)
        self.assertEqual(self.call("POST", "/api/settings", {"update_check": False})[0], 200)
        self.assertIs(self.d.cfg.update_check, False)

    def test_update_all_computers_updates_this_drive_too(self):
        from unittest import mock
        import time
        self.d.local_test_dir = None                    # (as a real drive: only the restart itself is left out)
        pulls = []
        with mock.patch.object(updates, "install_kind", return_value="git"), \
                mock.patch.object(updates.Updates, "pull", lambda self: pulls.append(1) or (True, "Already up to date.")), \
                mock.patch("os.kill") as kill:
            with mock.patch.object(updates, "version_on_disk", return_value=__version__):
                st, got = self.call("POST", "/api/lite/computers/update", {})
                self.assertEqual((st, got["host"]["state"] in ("pulling", "done")), (200, True))
                for _ in range(100):
                    if self.web.updates.updating["state"] != "pulling":
                        break
                    time.sleep(0.05)
            self.assertEqual(self.web.updates.updating["state"], "done")       # the newest already: not restarted
            kill.assert_not_called()
            self.assertGreater(self.web.computers.at, 0)
            with mock.patch.object(updates, "version_on_disk", return_value="99.0.0"):
                self.call("POST", "/api/lite/computers/update", {})
                for _ in range(100):
                    if kill.called or self.web.updates.updating["state"] == "done":
                        break
                    time.sleep(0.05)
            if sys.platform == "win32":                                      # (it can't restart itself there: it says to)
                self.assertIn("Restart DDrive", self.web.updates.updating["message"])
            else:
                kill.assert_called_once()                                    # newer code: the drive starts again with it
                self.assertTrue(self.d.restart_requested)
        self.assertEqual(len(pulls), 2)
        self.d.restart_requested = False

    def test_after_an_update_the_drive_starts_again_as_it_was_started(self):
        from unittest import mock
        from ddrive import cli
        cmd = updates.restart_command()
        self.assertTrue(cmd[0])
        self.assertGreaterEqual(len(cmd), 2)
        with mock.patch("os.execv") as execv:
            cli.restart_if_asked(type("D", (), {})())                       # nothing asked: nothing happens
            execv.assert_not_called()
            cli.restart_if_asked(type("D", (), {"restart_requested": True})())
            execv.assert_called_once_with(cmd[0], cmd)


if __name__ == "__main__":
    unittest.main()
