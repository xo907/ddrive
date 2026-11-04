"""DDrive under systemd (service.py): the unit's settings, one launcher only, the old discorddrive unit."""

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the program on the path)
from ddrive import cli, service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GB = 1024 ** 3


class ServiceTest(unittest.TestCase):
    def test_a_hard_cap_only_and_a_watchdog(self):
        for text in (service.unit_settings(4 * GB),
                     open(os.path.join(ROOT, "ddrive", "scripts", "linux", "ddrive.service"), encoding="utf-8").read(),
                     open(os.path.join(ROOT, "packaging", "build_deb.sh"), encoding="utf-8").read()):
            lines = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
            self.assertNotIn("MemoryHigh=60%", lines)
            self.assertFalse(any(line.startswith("MemoryHigh=") and line != "MemoryHigh=infinity" for line in lines))
            self.assertFalse(any(line.startswith("MemoryMax=") and line.endswith("%") for line in lines))
            for want in ("MemorySwapMax=0", "OOMScoreAdjust=300", "Type=notify", "NotifyAccess=all", "Restart=always"):
                self.assertIn(want, lines)
            self.assertTrue(any(line.startswith("WatchdogSec=") for line in lines))
        self.assertEqual(service.memory_cap(3921 * 1024 ** 2), 1960)       # the 3.9 GB server: half
        self.assertEqual(service.memory_cap(16 * GB), 2048)                # a big one: 2 GB at most
        self.assertEqual(service.memory_cap(512 * 1024 ** 2), 512)         # a tiny one: not below 512 MB

    def test_the_cron_line_goes_but_lites_stays(self):
        crontab = ('@reboot sleep 20 && "/root/DiscordDrive/run.sh" start >/dev/null 2>&1 # DDrive autostart\n'
                   '@reboot sleep 20 && "/root/x/run.sh" lite mount -b >/dev/null 2>&1 # DDrive Lite autostart\n'
                   '0 3 * * * /usr/bin/backup\n')
        written = []

        def run(cmd, input=None, **kw):
            if cmd == ["crontab", "-l"]:
                return subprocess.CompletedProcess(cmd, 0, crontab, "")
            written.append(input)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(service.subprocess, "run", run):
            self.assertTrue(service.remove_cron_autostart())
        self.assertNotIn("run.sh\" start", written[0])
        self.assertIn("# DDrive Lite autostart", written[0])
        self.assertIn("/usr/bin/backup", written[0])

    def test_only_an_old_unit_that_runs_discorddrive_is_removed(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        folder = os.path.join(home, ".config", "systemd", "user")
        os.makedirs(folder)
        unit = os.path.join(folder, "discorddrive.service")
        with open(unit, "w") as f:
            f.write("[Service]\nExecStart=/usr/bin/python3 -m something_else\n")
        self.assertEqual(service.legacy_units(home), [])                    # not ours: left alone
        with open(unit, "w") as f:
            f.write("[Service]\nExecStart=/usr/bin/python3 -m discorddrive mount\nRestart=on-failure\n")
        self.assertEqual(service.legacy_units(home), [(["--user"], unit)])
        calls = []
        with mock.patch.object(service, "_systemctl", lambda scope, *a: calls.append((tuple(scope), a))), \
                mock.patch.object(service, "legacy_units", lambda home=None: [(["--user"], unit)]):
            self.assertEqual(service.remove_legacy_units(home), [unit])
        self.assertFalse(os.path.exists(unit))
        self.assertIn((("--user",), ("disable", "--now", "discorddrive.service")), calls)
        # and `python -m discorddrive` itself ends cleanly
        r = subprocess.run([sys.executable, "-m", "discorddrive"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("renamed to DDrive", r.stderr)

    def test_mount_outside_the_service_starts_the_service_instead(self):
        started = []
        args = argparse.Namespace(verbose=False, mount=None, mock=None, background=True, allow_other=False, cache=None)
        cfg = cli.Config(bot_token="x", channel_id="1")
        with mock.patch.object(cli, "FUSE_ERROR", None), mock.patch.object(cli.Config, "load", return_value=cfg), \
                mock.patch.object(service, "linux", lambda: True), mock.patch.object(service, "running_in_unit", lambda: False), \
                mock.patch.object(service, "enabled_scope", lambda: []), mock.patch.object(service, "is_active", lambda s: False), \
                mock.patch.object(cli.subprocess, "run", lambda cmd, **kw: started.append(cmd) or subprocess.CompletedProcess(cmd, 0)), \
                mock.patch.dict(os.environ, {"DDRIVE_IGNORE_SERVICE": ""}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_mount(args), 0)
            with mock.patch.object(service, "is_active", lambda s: True):
                self.assertEqual(cli.cmd_mount(args), 0)                    # running already: nothing more
        self.assertEqual(started, [["systemctl", "start", "ddrive.service"]])

    def test_autostart_doesnt_add_cron_next_to_the_service(self):
        with mock.patch.object(service, "enabled_scope", lambda: []), \
                mock.patch.object(service, "remove_cron_autostart", lambda: True) as _, \
                mock.patch.object(cli.sys, "platform", "linux"), \
                mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no crontab")), \
                mock.patch("ddrive.legacy.old_autostart", lambda remove=False: None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.cmd_autostart(argparse.Namespace(action="on")), 0)
        self.assertIn("no cron line", out.getvalue())

    def test_notify_reaches_systemds_socket(self):
        if not hasattr(__import__("socket"), "AF_UNIX") or os.name == "nt":
            self.skipTest("unix sockets")
        import socket
        path = os.path.join(tempfile.mkdtemp(), "notify")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(path)
        with mock.patch.dict(os.environ, {"NOTIFY_SOCKET": path}):
            self.assertTrue(service.notify("READY=1"))
        self.assertEqual(s.recv(100), b"READY=1")
        s.close()


if __name__ == "__main__":
    unittest.main()
