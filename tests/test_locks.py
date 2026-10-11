"""Password-locked folders: gone from the drive letter and the dashboard until unlocked."""

import errno
import os

import helpers
from ddrive.locks import ALL


class LockTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive()
        self.write(self.d, "/Private/diary.txt", b"dear diary")
        self.write(self.d, "/Private/Deep/photo.jpg", b"\xff\xd8\xff")
        self.write(self.d, "/Public/readme.txt", b"hello")
        self.write(self.d, "/secret.txt", b"a single locked file")

    def names(self, path="/"):
        return sorted(n for n, _, _ in self.d.fs.readdir(path, None) if n not in (".", ".."))

    def gone(self, path):
        with self.assertRaises(OSError) as e:
            self.d.fs.getattr(path)
        return e.exception.errno == errno.ENOENT

    def test_locked_items_are_not_on_the_drive(self):
        locks = self.d.locks
        locks.add(self.d.index.resolve("/Private"), "hunter2")
        locks.add(self.d.index.resolve("/secret.txt"), "other-pw")
        self.assertEqual(self.names(), ["Public"])
        self.assertTrue(self.gone("/Private") and self.gone("/Private/Deep/photo.jpg") and self.gone("/secret.txt"))
        with self.assertRaises(OSError):
            self.d.fs.create("/Private/new.txt", 0o644)                 # nothing can be put there either
        with self.assertRaises(OSError):
            self.d.fs.mkdir("/private", 0o755)                          # whatever the capitals

        self.assertEqual(locks.matching("wrong"), [])
        opened = locks.matching("hunter2")
        self.assertEqual(len(opened), 1)                                # only the lock with this password
        locks.mount_unlock(opened)
        self.assertEqual(self.names(), ["Private", "Public"])
        self.assertEqual(self.read(self.d, "/Private/diary.txt", cold=False), b"dear diary")
        self.assertTrue(self.gone("/secret.txt"))

        self.d.fs.rename("/Private", "/Renamed")                        # the lock follows the folder
        locks.mount_relock()
        locks.changed()
        self.assertEqual(self.names(), ["Public"])
        self.assertTrue(self.gone("/Renamed/diary.txt"))

        locks.mount_unlock(opened, minutes=0.00001)                     # locks again by itself
        locks._mount = (0.0, frozenset())
        import time
        time.sleep(0.01)
        self.assertEqual(self.names(), ["Public"])

        self.assertFalse(locks.check(opened[0], "nope"))
        self.assertTrue(locks.check(opened[0], "hunter2"))
        locks.remove(opened[0])
        self.assertIn("Renamed", self.names())

    def test_locks_reach_other_devices_and_new_ones(self):
        self.upload(self.d)
        b = self.drive("bbbb")
        self.sync(self.d, b)
        self.assertIsNotNone(b.fs.getattr("/Private/diary.txt"))
        self.d.locks.add(self.d.index.resolve("/Private"), "hunter2")
        self.sync(self.d, b)
        b.locks.changed()
        with self.assertRaises(OSError):
            b.fs.getattr("/Private/diary.txt")
        self.assertEqual(len(b.locks.matching("hunter2")), 1)
        self.d.journal.checkpoint(force=True)                           # a device set up later gets it too
        c = self.drive("cccc")
        self.sync(c)
        with self.assertRaises(OSError):
            c.fs.getattr("/Private")
        self.d.locks.remove(self.d.index.resolve("/Private")["uid"])
        self.sync(self.d, b)
        b.locks.changed()
        self.assertIsNotNone(b.fs.getattr("/Private/diary.txt"))

    def test_folder_sync_still_copies_into_a_locked_folder(self):
        from ddrive.sync import normalize_job
        here = os.path.join(self.tmp, "here")
        os.makedirs(here)
        with open(os.path.join(here, "new.txt"), "wb") as f:
            f.write(b"synced")
        os.utime(os.path.join(here, "new.txt"), (1e9, 1e9))
        self.d.locks.add(self.d.index.resolve("/Private"), "hunter2")
        job = normalize_job(dict(local=here, remote="/Private", mode="backup"), self.d.cfg)
        res, error = self.d.sync.run_job(job, settle=0)
        self.assertEqual((error, res["up"]), ("", 1))
        self.assertTrue(self.gone("/Private/new.txt"))                  # still locked afterwards
        self.d.fs.scope.unlocked = ALL
        try:
            self.assertEqual(self.read(self.d, "/Private/new.txt", cold=False), b"synced")
        finally:
            self.d.fs.scope.unlocked = None
