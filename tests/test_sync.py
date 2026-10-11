"""Sync and backup folders: each mode against a real drive (local stand-in for Discord)."""

import os
import sys
import time

import helpers
from ddrive.sync import SyncError, normalize_job


class SyncTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive()
        self.here = os.path.join(self.tmp, "here")
        os.makedirs(self.here)

    def job(self, mode, remote="/Backup", **kw):
        j = normalize_job(dict(local=self.here, remote=remote, mode=mode, **kw), self.d.cfg)
        self.d.sync.reload([j])
        return j

    def run_job(self, j):
        res, error = self.d.sync.run_job(j, settle=0)
        self.assertEqual(error, "")
        return res

    def put(self, rel, data, age=100):
        p = os.path.join(self.here, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def local(self, rel):
        p = os.path.join(self.here, *rel.split("/"))
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def remote(self, path):
        return self.read(self.d, path) if self.d.index.resolve(path) is not None else None

    # ------------------------------------------------------------ one way, up
    def test_backup_copies_new_and_changed_and_keeps_deleted(self):
        j = self.job("backup")
        self.put("a.txt", b"one")
        self.put("sub/b.bin", os.urandom(200_000))
        self.put("video.mp4.crdownload", b"half a download")
        res = self.run_job(j)
        self.assertEqual(res["up"], 2)
        self.assertEqual(self.remote("/Backup/a.txt"), b"one")
        self.assertEqual(self.remote("/Backup/sub/b.bin"), self.local("sub/b.bin"))
        self.assertIsNone(self.d.index.resolve("/Backup/video.mp4.crdownload"))
        # the copy keeps the file's own modification time, so a second pass copies nothing
        self.assertAlmostEqual(self.d.index.resolve("/Backup/a.txt")["mtime"],
                               os.path.getmtime(os.path.join(self.here, "a.txt")), delta=0.01)
        self.assertEqual(self.run_job(j)["up"], 0)
        self.put("a.txt", b"two", age=50)
        os.remove(os.path.join(self.here, "sub", "b.bin"))
        res = self.run_job(j)
        self.assertEqual((res["up"], res["deleted_remote"]), (1, 0))
        self.assertEqual(self.remote("/Backup/a.txt"), b"two")
        self.assertIsNotNone(self.d.index.resolve("/Backup/sub/b.bin"))       # backup never deletes

    def test_backup_copies_each_file_once(self):
        # what is done with the copy on the drive (moved, renamed, deleted) is not undone by the backup
        j = self.job("backup", remote="/Windows/Downloads")
        self.put("movie.mkv", b"a film")
        self.put("setup.exe", b"an installer")
        self.put("album/song.mp3", b"music")
        self.assertEqual(self.run_job(j)["up"], 3)
        self.d.index.makedirs("/Films")
        self.d.fs.rename("/Windows/Downloads/movie.mkv", "/Films/movie.mkv")            # moved elsewhere on the drive
        self.d.fs.unlink("/Windows/Downloads/setup.exe")                                # deleted there
        self.d.fs.rename("/Windows/Downloads/album", "/Films/album")                    # a whole folder moved
        res = self.run_job(j)
        self.assertEqual((res["up"], res["kept_away"]), (0, 3))
        self.assertIsNone(self.d.index.resolve("/Windows/Downloads/movie.mkv"))
        self.assertIsNone(self.d.index.resolve("/Windows/Downloads/setup.exe"))
        self.assertIsNone(self.d.index.resolve("/Windows/Downloads/album"))
        self.assertIn("not copied again", self.d.sync.status[j["id"]]["problems"][0])
        self.put("movie.mkv", b"a film, director's cut", age=10)                        # changed here: copied again
        self.put("new.txt", b"new")
        res = self.run_job(j)
        self.assertEqual((res["up"], res["kept_away"]), (2, 2))
        self.assertEqual(self.remote("/Windows/Downloads/movie.mkv"), b"a film, director's cut")
        self.assertEqual(self.remote("/Films/movie.mkv"), b"a film")
        self.d.sync.run_now(j["id"], again=True)                                        # "Copy everything again"
        self.d.sync._requests.clear()
        res = self.run_job(j)
        self.assertEqual((res["up"], res["kept_away"]), (2, 0))
        self.assertIsNotNone(self.d.index.resolve("/Windows/Downloads/album/song.mp3"))

    def test_backup_made_before_this_version_learns_what_is_there(self):
        j = self.job("backup")
        self.put("a.txt", b"one")
        self.run_job(j)
        os.remove(os.path.join(self.d.sync.dir, j["id"] + ".backup.json"))             # as after an update
        self.assertEqual(self.run_job(j)["up"], 0)                                      # sees the copies, notes them
        self.d.fs.rename("/Backup/a.txt", "/elsewhere.txt")
        res = self.run_job(j)
        self.assertEqual((res["up"], res["kept_away"]), (0, 1))

    def test_empty_folder_says_so(self):
        j = self.job("backup")
        res = self.run_job(j)
        self.assertEqual(res["files"], 0)
        self.assertIn("is empty", self.d.sync.status[j["id"]]["problems"][0])

    def test_files_still_being_written_wait(self):
        j = self.job("backup")
        self.put("fresh.txt", b"still downloading", age=0)
        res, _ = self.d.sync.run_job(j)                                       # the real settle time
        self.assertEqual((res["up"], res["busy"]), (0, 1))
        self.assertIsNotNone(self.d.sync.status[j["id"]]["retry_at"])

    def test_exclude_patterns(self):
        j = self.job("backup", exclude="*.iso, cache/")
        self.put("keep.txt", b"k")
        self.put("big.ISO", b"x")
        self.put("cache/junk.dat", b"j")
        self.run_job(j)
        self.assertIsNotNone(self.d.index.resolve("/Backup/keep.txt"))
        self.assertIsNone(self.d.index.resolve("/Backup/big.ISO"))
        self.assertIsNone(self.d.index.resolve("/Backup/cache"))

    def test_mirror_deletes_and_guards_against_an_empty_folder(self):
        j = self.job("mirror")
        for n in ("a.txt", "c.txt", "d.txt"):
            self.put(n, b"a")
        self.put("old/b.txt", b"b")
        self.run_job(j)
        self.upload(self.d)
        os.remove(os.path.join(self.here, "old", "b.txt"))
        os.rmdir(os.path.join(self.here, "old"))
        res = self.run_job(j)
        self.assertEqual(res["deleted_remote"], 1)
        self.assertIsNone(self.d.index.resolve("/Backup/old"))
        self.assertTrue(any(v["path"] == "/Backup/old/b.txt" for v in self.d.index.deleted_files("/")))   # recoverable
        for n in ("a.txt", "c.txt", "d.txt"):                                # the folder is now empty
            os.remove(os.path.join(self.here, n))
        res = self.run_job(j)
        self.assertEqual((res["deleted_remote"], res["skipped_deletes"]), (0, 3))
        self.assertIsNotNone(self.d.index.resolve("/Backup/a.txt"))

    def test_missing_folder_changes_nothing(self):
        j = self.job("mirror")
        self.put("a.txt", b"a")
        self.run_job(j)
        os.remove(os.path.join(self.here, "a.txt"))
        os.rmdir(self.here)
        res, error = self.d.sync.run_job(j, settle=0)
        self.assertIn("isn't there", error)
        self.assertIsNotNone(self.d.index.resolve("/Backup/a.txt"))

    def test_move_deletes_here_once_stored_in_discord(self):
        j = self.job("move", remote="/Downloads")
        self.put("setup.exe", b"installer")
        self.put("pics/cat.jpg", b"meow")
        res = self.run_job(j)
        self.assertEqual((res["up"], res["moved"], res["waiting"]), (2, 0, 2))  # not in Discord yet: kept
        self.assertEqual(self.local("setup.exe"), b"installer")
        self.upload(self.d)
        res = self.run_job(j)
        self.assertEqual((res["up"], res["moved"]), (0, 2))
        self.assertIsNone(self.local("setup.exe"))
        self.assertFalse(os.path.exists(os.path.join(self.here, "pics")))   # emptied sub-folder removed
        self.assertTrue(os.path.isdir(self.here))
        self.assertEqual(self.remote("/Downloads/pics/cat.jpg"), b"meow")

    # ------------------------------------------------------------ one way, down
    def test_download_and_download_mirror(self):
        self.write(self.d, "/Photos/a.jpg", b"A" * 1000)
        self.write(self.d, "/Photos/2025/b.jpg", b"B" * 1000)
        self.upload(self.d)
        j = self.job("download", remote="/Photos")
        self.assertEqual(self.run_job(j)["down"], 2)
        self.assertEqual(self.local("2025/b.jpg"), b"B" * 1000)
        self.assertEqual(self.run_job(j)["down"], 0)
        self.d.fs.unlink("/Photos/a.jpg")
        self.run_job(j)
        self.assertEqual(self.local("a.jpg"), b"A" * 1000)                    # download keeps it
        j = self.job("download-mirror", remote="/Photos")
        res = self.run_job(j)
        self.assertEqual(res["deleted_local"], 1)
        self.assertIsNone(self.local("a.jpg"))
        trash = os.path.join(self.d.sync.dir, "trash", j["id"])
        kept = [f for _, _, fs in os.walk(trash) for f in fs]
        self.assertEqual(kept, ["a.jpg"])                                     # moved aside, not deleted

    # ------------------------------------------------------------ both ways
    def test_two_way(self):
        j = self.job("two-way", remote="/Shared")
        self.put("mine.txt", b"from here")
        self.write(self.d, "/Shared/theirs.txt", b"from the drive")
        res = self.run_job(j)
        self.assertEqual((res["up"], res["down"]), (1, 1))
        self.assertEqual(self.local("theirs.txt"), b"from the drive")
        self.assertEqual(self.remote("/Shared/mine.txt"), b"from here")
        self.assertEqual(sum(self.run_job(j)[k] for k in ("up", "down", "deleted_local", "deleted_remote")), 0)

        # a change on the drive comes here, a deletion here goes to the drive
        self.write(self.d, "/Shared/theirs.txt", b"edited on the drive")
        os.remove(os.path.join(self.here, "mine.txt"))
        res = self.run_job(j)
        self.assertEqual((res["down"], res["deleted_remote"]), (1, 1))
        self.assertEqual(self.local("theirs.txt"), b"edited on the drive")
        self.assertIsNone(self.d.index.resolve("/Shared/mine.txt"))

        # a deletion on the drive removes it here (kept aside)
        self.d.fs.unlink("/Shared/theirs.txt")
        self.assertEqual(self.run_job(j)["deleted_local"], 1)
        self.assertIsNone(self.local("theirs.txt"))

    def test_two_way_conflict_keeps_both(self):
        j = self.job("two-way", remote="/Shared")
        self.put("doc.txt", b"original")
        self.run_job(j)
        self.write(self.d, "/Shared/doc.txt", b"changed on the drive")        # newer
        self.put("doc.txt", b"changed here", age=60)                          # older
        res = self.run_job(j)
        self.assertEqual(res["conflicts"], 1)
        self.assertEqual(self.local("doc.txt"), b"changed on the drive")
        copies = [n for n in os.listdir(self.here) if "conflict" in n]
        self.assertEqual(len(copies), 1)
        self.assertEqual(self.local(copies[0]), b"changed here")
        self.assertEqual(self.remote("/Shared/" + copies[0]), b"changed here")
        self.assertEqual(self.run_job(j)["conflicts"], 0)

    def test_two_way_empty_side_deletes_nothing(self):
        j = self.job("two-way", remote="/Shared")
        for n in ("a.txt", "b.txt", "c.txt"):
            self.put(n, b"a")
        self.run_job(j)
        for n in ("a.txt", "b.txt", "c.txt"):
            os.remove(os.path.join(self.here, n))
        res = self.run_job(j)
        self.assertEqual((res["deleted_remote"], res["skipped_deletes"]), (0, 3))
        self.assertIsNotNone(self.d.index.resolve("/Shared/a.txt"))

    # ------------------------------------------------------------ checks
    def test_bad_jobs_are_refused(self):
        with self.assertRaises(SyncError):
            normalize_job(dict(local=self.here, remote="/", mode="mirror"), self.d.cfg)       # would wipe the drive
        with self.assertRaises(SyncError):
            normalize_job(dict(local=os.path.join(self.tmp, "nope"), remote="/x", mode="backup"), self.d.cfg)
        with self.assertRaises(SyncError):
            normalize_job(dict(local=self.d.cfg.resolved_data_dir, remote="/x", mode="backup"), self.d.cfg)
        with self.assertRaises(SyncError):
            normalize_job(dict(local=self.here, remote="/x", mode="sideways"), self.d.cfg)
        with self.assertRaises(SyncError):
            normalize_job(dict(local=self.here, remote="C:\\Users\\x", mode="backup"), self.d.cfg)   # not the drive
        remote = "Z:\\Downloads" if sys.platform == "win32" else "Downloads"
        j = normalize_job(dict(local=self.here, remote=remote, mode="backup", trigger="daily",
                               at="7:5"), self.d.cfg)
        self.assertEqual((j["remote"], j["at"], j["name"]), ("/Downloads", "07:05", "here"))
        with self.assertRaises(SyncError):
            normalize_job(dict(local=self.here, remote="/Downloads", mode="mirror"), self.d.cfg, existing=[j])
