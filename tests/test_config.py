"""The config file keeps its secrets encrypted for this computer and account."""

import json
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (puts the project on the path)
from ddrive import secretbox
from ddrive.config import SECRET_FIELDS, Config

TOKEN = "MTIzNDU2.bot-token.secret"
KEY = "ab" * 32


class ConfigProtectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dd-cfg-")
        self.path = os.path.join(self.tmp, "config.json")
        self.env = {k: os.environ.get(k) for k in ("DDRIVE_CONFIG", "XDG_DATA_HOME")}
        os.environ["DDRIVE_CONFIG"] = self.path
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")      # where Linux keeps machine.key

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def cfg(self, **more):
        return Config(bot_token=TOKEN, channel_id="42", encryption_key=KEY, extra_bot_tokens=["second.token"],
                      web_password="scrypt$hash", mount_point="Y:", **more)

    def test_secrets_are_not_readable_in_the_file(self):
        self.assertTrue(self.cfg().save())
        text = self.text()
        for secret in (TOKEN, KEY, "second.token", "scrypt$hash"):
            self.assertNotIn(secret, text)
        raw = json.loads(text)
        self.assertTrue(raw["protected"].startswith(secretbox.METHOD + ":"))
        self.assertEqual((raw["channel_id"], raw["bot_token"], raw["extra_bot_tokens"]), ("42", "", []))  # settings stay readable
        back = Config.load()
        self.assertEqual((back.bot_token, back.encryption_key, back.extra_bot_tokens, back.web_password),
                         (TOKEN, KEY, ["second.token"], "scrypt$hash"))
        self.assertTrue(back.is_configured())
        self.assertIsNone(back.protect_error)

    def test_a_readable_file_is_taken_and_protected(self):
        with open(self.path, "w", encoding="utf-8") as f:      # written by hand or by an older version
            json.dump({"bot_token": TOKEN, "channel_id": "42", "encryption_key": KEY, "cache_mode": "memory"}, f)
        cfg = Config.load()
        self.assertEqual((cfg.bot_token, cfg.encryption_key, cfg.cache_mode), (TOKEN, KEY, "memory"))
        cfg.cache_mode = "disk"
        self.assertFalse(cfg.save())                           # other commands leave the form alone...
        self.assertIn(TOKEN, self.text())
        self.assertIn("not encrypted yet", cfg.protection())
        self.assertTrue(Config.protect_file())                 # ...the drive protects it when it starts
        self.assertFalse(Config.protect_file())
        self.assertNotIn(TOKEN, self.text())
        self.assertNotIn(KEY, self.text())
        self.assertEqual(Config.load().bot_token, TOKEN)
        # a secret typed into the file later replaces the stored one
        raw = json.loads(self.text())
        raw["bot_token"] = "new.token"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        self.assertEqual(Config.load().bot_token, "new.token")
        self.assertTrue(Config.protect_file())
        self.assertNotIn("new.token", self.text())
        self.assertEqual(Config.load().bot_token, "new.token")
        self.assertEqual(Config.load().encryption_key, KEY)

    def test_machine_key_method(self):
        # what Linux uses (Windows has its own); the key file lives apart from the config
        saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = os.path.join(self.tmp, "data")
        try:
            blob = secretbox._machine(b"the secrets", True)
            self.assertNotIn(b"the secrets", blob)
            self.assertEqual(secretbox._machine(blob, False), b"the secrets")
            keyfile = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(self.tmp, "data")) for f in fs]
            self.assertEqual([os.path.basename(k) for k in keyfile], ["machine.key"])
            os.remove(keyfile[0])                               # the config alone is worth nothing
            with self.assertRaises(secretbox.ProtectError):
                secretbox._machine(blob, False)
        finally:
            if saved is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = saved

    def test_secrets_from_elsewhere_are_reported_and_kept(self):
        self.cfg().save()
        raw = json.loads(self.text())
        blob = raw["protected"][:-12] + "AAAAAAAAAAAA"          # as if written on another computer or account
        raw["protected"] = blob
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        cfg = Config.load()
        self.assertFalse(cfg.is_configured())
        self.assertIn("can't be read here", cfg.protect_error)
        cfg.mount_point = "X:"
        cfg.save()                                              # changing a setting doesn't destroy them
        self.assertEqual(json.loads(self.text())["protected"], blob)
        cfg.bot_token, cfg.encryption_key = TOKEN, KEY          # setup run again
        cfg.save()
        self.assertEqual(Config.load().bot_token, TOKEN)

    def test_can_be_turned_off(self):
        self.assertFalse(self.cfg(protect_config=False).save())
        self.assertIn(TOKEN, self.text())
        self.assertEqual(Config.load().bot_token, TOKEN)
        self.assertIn(TOKEN, self.text())                       # and stays readable

    def test_all_secret_fields_exist(self):
        self.assertTrue(all(hasattr(Config(), k) for k in SECRET_FIELDS))

    def test_a_computer_set_up_before_the_rename_keeps_working(self):
        from ddrive import config, legacy
        base = os.path.join(self.tmp, "appdata")
        self.assertEqual(legacy.app_dir(base, "DDrive"), os.path.join(base, "DDrive"))          # a new installation
        os.makedirs(os.path.join(base, legacy.OLD))
        self.assertEqual(legacy.app_dir(base, "DDrive"), os.path.join(base, legacy.OLD))        # its folder is used where it is
        os.makedirs(os.path.join(base, "DDrive"))
        self.assertEqual(legacy.app_dir(base, "DDrive"), os.path.join(base, "DDrive"))
        # the old environment variables still say where the config is and which bot to use
        del os.environ["DDRIVE_CONFIG"]
        old = legacy.OLD_ENV + "CONFIG"
        os.environ[old] = self.path
        self.addCleanup(os.environ.pop, old, None)
        self.assertEqual(config.config_path(), self.path)
        os.environ[legacy.OLD_ENV + "TOKEN"] = "old-way"
        self.addCleanup(os.environ.pop, legacy.OLD_ENV + "TOKEN", None)
        Config(channel_id="1", volume_name=legacy.OLD, protect_config=False).save(self.path)
        cfg = Config.load()
        self.assertEqual((cfg.bot_token, cfg.volume_name), ("old-way", "DDrive"))               # and the drive gets its new label
        os.environ["DDRIVE_TOKEN"] = "new-way"
        self.addCleanup(os.environ.pop, "DDRIVE_TOKEN", None)
        self.assertEqual(Config.load().bot_token, "new-way")
        Config(channel_id="1", volume_name="Holiday", protect_config=False).save(self.path)
        self.assertEqual(Config.load().volume_name, "Holiday")                                  # a label somebody chose stays
        # the running drive of the old version is still found, to stop it
        data = os.path.join(self.tmp, "d")
        os.makedirs(data)
        self.assertTrue(legacy.pid_file(data, "ddrive.pid").endswith("ddrive.pid"))
        open(os.path.join(data, legacy.OLD_PID), "w").close()
        self.assertTrue(legacy.pid_file(data, "ddrive.pid").endswith(legacy.OLD_PID))


if __name__ == "__main__":
    unittest.main()
