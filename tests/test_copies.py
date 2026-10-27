"""An outdated copy of DDrive on the same computer doesn't start Lite (copies.py)."""

import io
import contextlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the program on the path)
from ddrive import copies, lite


def make_copy(parent, name, version):
    root = os.path.join(parent, name)
    os.makedirs(os.path.join(root, "ddrive"))
    with open(os.path.join(root, "ddrive", "__init__.py"), "w", encoding="utf-8") as f:
        f.write(f'"""DDrive."""\n\n__version__ = "{version}"\n')
    return root


class CopiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for patch in (mock.patch.object(copies, "_path", lambda: os.path.join(self.tmp, "data", "copies.json")),
                      mock.patch.object(copies, "_counts", lambda root: True),
                      mock.patch.object(copies, "autostart_root", lambda: None),
                      mock.patch.dict(os.environ)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("DDRIVE_ALLOW_OLD", None)
        self.old = make_copy(self.tmp, "old", "3.3.1")
        self.new = make_copy(self.tmp, "new", "3.23.1")

    def test_an_older_copy_is_refused_and_told_where_the_newer_is(self):
        copies.remember(self.new)
        self.assertIsNone(copies.refusal(self.new, "3.23.1"))
        said = copies.refusal(self.old, "3.3.1")
        self.assertIn(self.new, said)
        self.assertIn("3.23.1", said)
        copies.remember(self.old)                    # (written down too: it doesn't make the newer one older)
        self.assertIsNone(copies.refusal(self.new, "3.23.1"))

    def test_the_same_version_or_a_newer_one_starts(self):
        copies.remember(self.new)
        same = make_copy(self.tmp, "same", "3.23.1")
        self.assertIsNone(copies.refusal(same, "3.23.1"))
        self.assertIsNone(copies.refusal(make_copy(self.tmp, "newest", "3.24.0"), "3.24.0"))

    def test_the_version_on_disk_counts_and_a_deleted_copy_is_forgotten(self):
        copies.remember(self.new)
        make_copy(self.tmp, "x", "1.0")
        with open(os.path.join(self.old, "ddrive", "__init__.py"), "w", encoding="utf-8") as f:
            f.write('__version__ = "3.30.0"\n')              # the old one was updated since (git pull)
        self.assertIsNone(copies.refusal(self.old, "3.30.0"))
        with open(os.path.join(self.old, "ddrive", "__init__.py"), "w", encoding="utf-8") as f:
            f.write('__version__ = "3.3.1"\n')
        shutil.rmtree(self.new)                              # the newer one deleted: the other may start again
        self.assertIsNone(copies.refusal(self.old, "3.3.1"))
        copies.remember(self.old)
        self.assertNotIn(os.path.abspath(self.new), copies._load())

    def test_the_autostart_copy_counts_and_it_can_be_overridden(self):
        with mock.patch.object(copies, "autostart_root", lambda: self.new):
            self.assertIsNotNone(copies.refusal(self.old, "3.3.1"))
            with mock.patch.dict(os.environ, {"DDRIVE_ALLOW_OLD": "1"}):
                self.assertIsNone(copies.refusal(self.old, "3.3.1"))

    def test_lite_refuses_what_starts_but_not_what_only_looks(self):
        out = io.StringIO()
        with mock.patch.object(copies, "refusal", lambda: "an older copy"), \
                mock.patch.object(lite, "_logging", lambda verbose=False: None), \
                mock.patch.object(lite, "cmd_stop", lambda args: 0), \
                mock.patch.object(lite, "cmd_sync", lambda args: 0), contextlib.redirect_stdout(out):
            self.assertEqual(lite.main(["mount"]), 1)
            self.assertEqual(lite.main(["sync", "run"]), 1)
            self.assertEqual(lite.main(["stop"]), 0)
            self.assertEqual(lite.main(["sync", "status"]), 0)
        self.assertIn("[ERROR] an older copy", out.getvalue())


if __name__ == "__main__":
    unittest.main()
