"""The host's 24 words forgotten on its computer and typed in again (cli: forget-words, words)."""

import argparse
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the program on the path)
from ddrive import cli, lite, words
from ddrive.config import Config
from ddrive.crypto import generate_key


class WordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dd-words-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.dict(os.environ, {"DDRIVE_CONFIG": os.path.join(self.tmp, "config.json")})
        patch.start()
        self.addCleanup(patch.stop)
        self.key = generate_key().hex()
        self.old = generate_key().hex()
        cfg = Config(bot_token="bot", channel_id="123", encryption_key=self.key, old_encryption_keys=[self.old],
                     encryption_salt="c2FsdA==", data_dir=os.path.join(self.tmp, "data"), mount_point=os.path.join(self.tmp, "mnt"))
        os.makedirs(cfg.data_dir)
        cfg.save()
        self.phrase = words.key_to_phrase(bytes.fromhex(self.key))
        for p in (mock.patch.object(cli, "is_mounted", lambda mp: False),):
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, fn, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fn(argparse.Namespace(**{"phrase": None, "wipe": False, "account": False, "forget": False, "mount": None, **kw}))
        return code, out.getvalue()

    def test_forgotten_and_typed_in_again(self):
        other = words.key_to_phrase(generate_key())
        code, text = self.run_cmd(cli.cmd_forget_words, phrase=other)
        self.assertEqual(code, 1)                                       # (only with the drive's own words)
        self.assertEqual(Config.load().encryption_key, self.key)
        code, text = self.run_cmd(cli.cmd_forget_words, phrase=self.phrase)
        self.assertEqual(code, 0, text)
        cfg = Config.load()
        self.assertEqual((cfg.encryption_key, cfg.old_encryption_keys, cfg.encryption_salt, cfg.words_forgotten), ("", [], "", True))
        with open(os.environ["DDRIVE_CONFIG"], encoding="utf-8") as f:
            saved = f.read()
        self.assertNotIn(self.key, saved)
        self.assertNotIn(self.old, saved)                               # (sealed under the words, not readable)
        # Nothing starts meanwhile, and no new key is made for the drive
        with mock.patch.object(cli, "FUSE_ERROR", None):
            code, text = self.run_cmd(cli.cmd_mount, verbose=False, mount=None, mock=None, background=False, allow_other=False, cache=None)
        self.assertEqual(code, cli.WORDS_FORGOTTEN)
        self.assertIn("words", text)
        from ddrive.drive import DDrive
        with self.assertRaisesRegex(RuntimeError, "forgotten"):
            DDrive(Config.load()).initialize()
        self.assertEqual(Config.load().encryption_key, "")
        # Other words don't open it; its own bring everything back, the older keys too
        self.assertEqual(self.run_cmd(cli.cmd_words, phrase=other)[0], 1)
        code, text = self.run_cmd(cli.cmd_words, phrase=self.phrase)
        self.assertEqual(code, 0, text)
        cfg = Config.load()
        self.assertEqual((cfg.encryption_key, cfg.old_encryption_keys, cfg.encryption_salt, cfg.words_forgotten),
                         (self.key, [self.old], "c2FsdA==", False))

    def test_wiping_the_readable_copies_waits_for_what_isnt_on_discord_yet(self):
        data = Config.load().data_dir
        from ddrive.index import Index
        idx = Index(os.path.join(data, "index.db"))
        idx.close()
        for d in ("cache", "thumbs", "backups"):
            os.makedirs(os.path.join(data, d))
        with mock.patch.object(cli.Index, "outbox_count", lambda self: 2):
            code, text = self.run_cmd(cli.cmd_forget_words, phrase=self.phrase, wipe=True)
        self.assertEqual(code, 1)
        self.assertIn("haven't reached Discord", text)
        self.assertEqual(Config.load().encryption_key, self.key)
        code, text = self.run_cmd(cli.cmd_forget_words, phrase=self.phrase, wipe=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(os.listdir(data)), [])

    def test_the_running_drive_is_not_left_without_its_words(self):
        with mock.patch.object(cli, "is_mounted", lambda mp: True):
            code, text = self.run_cmd(cli.cmd_forget_words, phrase=self.phrase)
        self.assertEqual(code, 1)
        self.assertIn("running", text)
        self.assertEqual(Config.load().encryption_key, self.key)

    def test_an_accounts_words_show_its_files_through_the_running_drive(self):
        account = words.key_to_phrase(generate_key())
        code, text = self.run_cmd(cli.cmd_words, phrase=account)
        self.assertEqual(code, 1)                                        # (the drive isn't running)
        self.assertIn("isn't running", text)
        asked = []
        with mock.patch.object(cli, "is_mounted", lambda mp: True), \
                mock.patch.object(lite, "main", lambda argv: asked.append(argv) or 0), \
                mock.patch.object(lite, "load_settings", lambda: {}):
            code, text = self.run_cmd(cli.cmd_words, phrase=account)
        self.assertEqual(code, 0, text)
        self.assertEqual(asked[0], ["connect", "http://127.0.0.1:8765", "-p", account])
        self.assertEqual(asked[1], ["mount", "-m", cli._account_mount(Config.load(), argparse.Namespace(mount=None)), "-b"])
        # its own words are not taken for an account's
        with mock.patch.object(cli, "is_mounted", lambda mp: True):
            code, text = self.run_cmd(cli.cmd_words, phrase=self.phrase)
        self.assertEqual(code, 0)
        self.assertIn("own words", text)


if __name__ == "__main__":
    unittest.main()
