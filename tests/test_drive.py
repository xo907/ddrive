"""Whole-drive tests: upload, sync between devices, versions, dedup, self-healing, snapshots."""

import errno
import os
import unittest

import helpers
from helpers import PIECE
from ddrive.actions import snapshot_restore_ops


def data_of(n_pieces, compressible=False):
    if compressible:
        return (b"DDrive " * (n_pieces * PIECE // 13 + 1))[:n_pieces * PIECE - 123]
    return os.urandom(n_pieces * PIECE - 123)


class UploadTest(helpers.DriveTest):
    def test_roundtrip_with_spare_pieces(self):
        d = self.drive()
        data = data_of(10)                   # groups of 4 + 4 + 2
        self.write(d, "/a/file.bin", data)
        self.upload(d)
        st = d.index.stats(max_age=0)
        self.assertEqual(st["chunks"], 10)
        self.assertEqual(st["protected_chunks"], 10)
        self.assertEqual(st["spare_pieces"], 2 + 2 + 1)     # the group of 2 gets one spare piece
        self.assertEqual(self.read(d, "/a/file.bin"), data)

    def test_compression_shrinks_uploads(self):
        d = self.drive(parity_enabled=False)
        data = data_of(6, compressible=True)
        self.write(d, "/text.txt", data)
        self.upload(d)
        self.assertLess(d.backend.uploaded_bytes, len(data) // 5)
        self.assertEqual(self.read(d, "/text.txt"), data)

    def test_resume_after_failure(self):
        d = self.drive()
        data = data_of(9)
        self.write(d, "/big.bin", data)
        real = d.backend.upload
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 7:
                raise ConnectionError("network down")
            return real(*a, **kw)
        d.backend.upload = flaky
        nid = d.index.resolve("/big.bin")["id"]
        d.uploader.run_now(nid)                  # fails part-way
        self.assertNotEqual(d.index.get(nid)["state"], "synced")
        before = d.backend.uploads
        d.index.update(nid, state="pending")
        d.uploader.run_now(nid)                  # continues where it stopped
        self.assertEqual(d.index.get(nid)["state"], "synced")
        self.assertLess(d.backend.uploads - before, 9 + 5)      # not everything again
        self.assertEqual(self.read(d, "/big.bin"), data)


class SyncTest(helpers.DriveTest):
    def test_two_devices(self):
        a = self.drive("aaaa")
        b = self.drive("bbbb")
        data = data_of(5)
        self.write(a, "/docs/report.bin", data)
        self.upload(a)
        self.sync(a, b)
        self.assertEqual(self.read(b, "/docs/report.bin"), data)
        # parity knowledge travels with the file
        self.assertEqual(b.index.stats(max_age=0)["protected_chunks"], 5)

        b.fs.unlink("/docs/report.bin")
        self.sync(b, a)
        self.assertIsNone(a.index.resolve("/docs/report.bin"))
        self.assertEqual(len(a.index.deleted_files()), 1)

    def test_delete_forever_everywhere(self):
        a = self.drive("aaaa")
        b = self.drive("bbbb")
        self.write(a, "/old.bin", data_of(2))
        self.upload(a)
        self.sync(a, b)
        a.fs.unlink("/old.bin")
        self.sync(a, b)
        uid = b.index.deleted_files()[0]["uid"]
        self.assertEqual(b.index.purge_deleted([uid]), 1)
        self.sync(b, a)
        for d in (a, b):
            self.assertEqual(d.index.deleted_files(), [])
            self.empty_trash(d)
            self.assertFalse(d.index.referenced_mids())

    def test_versions(self):
        a = self.drive()
        self.write(a, "/f.txt", b"one" * 1000)
        self.upload(a)
        self.write(a, "/f.txt", b"two" * 1000)
        self.upload(a)
        node = a.index.resolve("/f.txt")
        versions = a.index.versions_for(node["uid"])
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.read(a, "/f.txt"), b"two" * 1000)


class DedupTest(helpers.DriveTest):
    def test_copy_is_not_uploaded_again(self):
        d = self.drive(keep_versions=False)
        data = data_of(8)
        self.write(d, "/orig.bin", data)
        self.upload(d)
        before = d.backend.uploads
        self.write(d, "/copy.bin", data)
        self.upload(d)
        self.assertEqual(d.backend.uploads - before, 0, "pieces and spare pieces should be reused")

        # deleting the original must not delete the pieces the copy uses
        d.fs.unlink("/orig.bin")
        d.journal.sync_once()
        self.empty_trash(d)
        self.assertEqual(self.read(d, "/copy.bin"), data)

        # deleting the copy too frees everything
        d.fs.unlink("/copy.bin")
        d.journal.sync_once()
        self.empty_trash(d)
        self.assertEqual(d.index.stats(max_age=0)["spare_pieces"], 0)
        leftover = [n for n in os.listdir(self.channel) if n.endswith(".bin")]
        stored = {m for m in d.index.referenced_mids()}
        self.assertFalse(stored)
        self.assertLessEqual(len(leftover), 3)    # only checkpoints / journal attachments remain


class HealTest(helpers.DriveTest):
    def test_lost_pieces_are_rebuilt_and_repaired_everywhere(self):
        a = self.drive("aaaa")
        b = self.drive("bbbb")
        data = data_of(8)
        self.write(a, "/movie.bin", data)
        self.upload(a)
        self.sync(a, b)
        chunks = a.index.get_chunks(a.index.resolve("/movie.bin")["id"])
        lost = [chunks[1]["message_id"], chunks[3]["message_id"]]     # two pieces of the first group
        for mid in lost:
            a.backend.delete(mid)
        self.assertEqual(self.read(a, "/movie.bin"), data)            # read works anyway
        self.sync(a, b)
        new_b = [c["message_id"] for c in b.index.get_chunks(b.index.resolve("/movie.bin")["id"])]
        for mid in lost:
            self.assertNotIn(mid, new_b)                              # b learned where the repaired pieces are
        self.assertEqual(self.read(b, "/movie.bin"), data)

    def test_too_many_lost(self):
        d = self.drive()
        data = data_of(4)
        self.write(d, "/x.bin", data)
        self.upload(d)
        for c in d.index.get_chunks(d.index.resolve("/x.bin")["id"])[:3]:
            d.backend.delete(c["message_id"])
        with self.assertRaises(OSError) as e:
            self.read(d, "/x.bin")
        self.assertEqual(e.exception.errno, errno.EIO)

    def test_check_repairs_missing_spare_piece(self):
        d = self.drive()
        data = data_of(4)
        self.write(d, "/y.bin", data)
        self.upload(d)
        group = d.index.parity_groups_for(d.index.get_chunks(d.index.resolve("/y.bin")["id"])[0]["message_id"])[0]
        gone = group["shards"][0][0]
        d.backend.delete(gone)
        for _ in range(len(d.index.referenced_mids())):
            d.maintenance.scrub_step(force=True)
        d.journal.sync_once()
        self.assertFalse(d.index.is_referenced(gone))
        self.assertEqual(d.maintenance.scrub_state().get("repaired"), 1)
        # and the new spare piece really works: lose two data pieces now
        for c in d.index.get_chunks(d.index.resolve("/y.bin")["id"])[:2]:
            d.backend.delete(c["message_id"])
        self.assertEqual(self.read(d, "/y.bin"), data)

    def test_protect_old_files(self):
        d = self.drive(parity_enabled=False)
        data = data_of(6)
        self.write(d, "/old.bin", data)
        self.upload(d)
        self.assertEqual(d.index.stats(max_age=0)["protected_chunks"], 0)
        d.cfg.parity_enabled = True
        d.maintenance._next_protect = 0
        d.maintenance._leader = (1e18, True)
        d.uploader._running = False
        d.maintenance.protect_step()
        d.journal.sync_once()
        self.assertEqual(d.index.stats(max_age=0)["protected_chunks"], 6)
        d.backend.delete(d.index.get_chunks(d.index.resolve("/old.bin")["id"])[0]["message_id"])
        self.assertEqual(self.read(d, "/old.bin"), data)


class SnapshotTest(helpers.DriveTest):
    def test_restore_after_delete(self):
        a = self.drive("aaaa", keep_versions=False)
        b = self.drive("bbbb", keep_versions=False)
        data = data_of(3)
        self.write(a, "/photos/2024/beach.bin", data)
        self.write(a, "/photos/2024/sea.bin", b"small")
        self.upload(a)
        sid = a.journal.create_snapshot("test", 14)
        self.sync(a, b)
        self.assertEqual(len(b.index.snapshots()), 1)

        a.fs.unlink("/photos/2024/beach.bin")
        self.sync(a, b)
        for d in (a, b):
            self.empty_trash(d)                         # the snapshot keeps the pieces alive

        ops, files = snapshot_restore_ops(a.index, sid, "/photos", "/Restored")
        self.assertEqual(files, 2)
        a.journal.post_ops(ops)
        self.sync(a, b)
        self.assertEqual(self.read(b, "/Restored/photos/2024/beach.bin"), data)
        self.assertEqual(self.read(a, "/Restored/photos/2024/sea.bin"), b"small")

    def test_expired_snapshot_frees_pieces(self):
        d = self.drive(keep_versions=False)
        self.write(d, "/f.bin", data_of(2))
        self.upload(d)
        d.journal.create_snapshot("x", 14)
        d.fs.unlink("/f.bin")
        d.journal.sync_once()
        self.assertTrue(d.index.referenced_mids())
        d.index.db.execute("UPDATE snapshots SET keep_until=1")
        d.index.gc_versions(0, 0)
        self.assertFalse(d.index.referenced_mids())


class DeviceTest(helpers.DriveTest):
    def test_hidden_folders(self):
        d = self.drive()
        self.write(d, "/Movies/a.bin", b"x")      # e.g. added on another device
        self.write(d, "/Docs/b.bin", b"y")
        d.cfg.hidden_folders = ["/Movies"]
        names = [n for n, _, _ in d.fs.readdir("/", None)]
        self.assertIn("Docs", names)
        self.assertNotIn("Movies", names)
        with self.assertRaises(OSError):
            d.fs.getattr("/movies/a.bin")
        with self.assertRaises(OSError):
            d.fs.create("/Movies/new.bin", 0o644)

    def test_devices_and_leader(self):
        a = self.drive("aaaa")
        b = self.drive("bbbb")
        self.write(a, "/x", b"1")
        self.upload(a)
        self.write(b, "/y", b"2")
        self.upload(b)
        self.sync(a, b)
        self.assertEqual(set(a.index.devices()), {"aaaa", "bbbb"})
        self.assertTrue(a.index.is_leader("aaaa"))
        self.assertFalse(b.index.is_leader("bbbb"))

    def test_catch_up_after_upgrade(self):
        a = self.drive("aaaa")
        b = self.drive("bbbb")
        self.write(a, "/z.bin", data_of(4))
        self.upload(a)
        self.sync(a, b)
        # pretend b ran an older version that ignored spare pieces
        for t in ("parity_groups", "parity_members", "parity_shards"):
            b.index.db.execute(f"DELETE FROM {t}")
        b.index.kv_delete("features")
        b.journal.catch_up()
        self.assertEqual(b.index.stats(max_age=0)["protected_chunks"], 4)


if __name__ == "__main__":
    unittest.main()
