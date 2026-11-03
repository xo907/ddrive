"""Small files packed together (uploader.py, backend.py): many in one stored piece, each file its part of it."""

import os
from concurrent.futures import ThreadPoolExecutor

import helpers

from ddrive.backend import pack_slice, physical
from ddrive.config import Config
from ddrive.drive import DDrive
from ddrive.heal import confirm_unreadable


class PackTest(helpers.DriveTest):
    def pack_upload(self, d):
        """Upload what waits the way the running drive does: small files gathered first, then packed together."""
        u = d.uploader
        if u._pool is None:
            u._pool = ThreadPoolExecutor(max_workers=4)
            u._parity_pool = ThreadPoolExecutor(max_workers=1)
        was, u._running = u._running, True
        try:
            for nid in d.index.unsynced_ids():
                u._process_upload(nid)
            u._flush_packs()
        finally:
            u._running = was
        d.journal.sync_once()

    def mid(self, d, path):
        return d.index.get_chunks(d.index.resolve(path)["id"])[0]["message_id"]

    def files(self, d, n=40, size=300):
        out = {}
        for i in range(n):
            data = os.urandom(size + i)
            self.write(d, f"/small/f{i}.js", data)
            out[f"/small/f{i}.js"] = data
        return out

    def test_many_small_files_are_one_stored_piece(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        before = d.backend.uploads
        files = self.files(d)
        self.pack_upload(d)
        mids = {self.mid(d, p) for p in files}
        self.assertEqual(len(mids), len(files))                             # each file its own part...
        self.assertEqual(len({physical(m) for m in mids}), 1)               # ...of one pack
        self.assertTrue(all(pack_slice(m)[1] is not None for m in mids))
        groups = d.index.parity_groups_for(next(iter(mids)))
        self.assertEqual(len(groups), 1)                                     # the pack has its spare pieces
        # 40 files: 1 piece and its spare pieces (and one message of the journal saying what changed)
        self.assertEqual(d.backend.uploads - before, 1 + len(groups[0]["shards"]) + 1)
        for p, data in files.items():
            self.assertEqual(self.read(d, p), data)
        # a big file and one too big for a pack keep pieces of their own
        big = os.urandom(helpers.PIECE * 3)
        self.write(d, "/big.bin", big)
        self.write(d, "/quarter.bin", os.urandom(helpers.PIECE // 2))
        self.pack_upload(d)
        self.assertIsNone(pack_slice(self.mid(d, "/big.bin"))[1])
        self.assertIsNone(pack_slice(self.mid(d, "/quarter.bin"))[1])
        self.assertEqual(self.read(d, "/big.bin"), big)
        self.assertFalse(confirm_unreadable(d.index, d.backend, d.crypto, d.index.resolve("/small/f3.js")["id"]))

    def test_a_pack_stays_while_any_of_its_files_needs_it(self):
        d = self.drive(parity_group=4, parity_pieces=2, upload_delay=0)
        files = self.files(d, n=5)
        self.pack_upload(d)
        pack = physical(self.mid(d, "/small/f0.js"))
        shards = [s[0] for s in d.index.parity_groups_for(pack)[0]["shards"]]
        d.uploader._running = True
        try:
            for i in range(4):
                d.index.purge_node(d.index.resolve(f"/small/f{i}.js")["id"])
            d.uploader.clean_trash(grace=0, limit=100)
            self.assertIsNotNone(d.backend.message_info(pack))              # f4 still uses it
            self.assertEqual(self.read(d, "/small/f4.js"), files["/small/f4.js"])
            d.index.purge_node(d.index.resolve("/small/f4.js")["id"])
            d.uploader.clean_trash(grace=0, limit=100)
        finally:
            d.uploader._running = False
        self.assertIsNone(d.backend.message_info(pack))                     # the last one gone: the pack goes...
        self.assertTrue(all(d.backend.message_info(s) is None for s in shards))   # ...with its spare pieces

    def test_an_earlier_version_keeps_its_pack(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        self.write(d, "/note.txt", b"first words")
        self.pack_upload(d)
        first = self.mid(d, "/note.txt")
        self.write(d, "/note.txt", b"second words, longer")
        self.pack_upload(d)
        self.assertNotEqual(physical(self.mid(d, "/note.txt")), physical(first))
        self.assertTrue(d.index.is_referenced(physical(first)))           # (kept as the earlier version)
        self.assertEqual(self.read(d, "/note.txt"), b"second words, longer")

    def test_a_lost_pack_is_rebuilt_and_every_file_follows_the_repair(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        files = self.files(d, n=12)
        self.pack_upload(d)
        pack = physical(self.mid(d, "/small/f0.js"))
        d.backend.delete(pack)                                              # Discord loses it
        for p, data in files.items():                                       # every file still reads (rebuilt)
            self.assertEqual(self.read(d, p), data)
        d.journal.sync_once()
        new = physical(self.mid(d, "/small/f0.js"))
        self.assertNotEqual(new, pack)                                       # repaired: stored again...
        self.assertEqual({physical(self.mid(d, p)) for p in files}, {new})   # ...and every part moved with it
        self.assertIsNotNone(d.backend.message_info(new))
        for p, data in files.items():
            self.assertEqual(self.read(d, p), data)

    def test_the_background_check_finds_a_lost_pack(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        files = self.files(d, n=6)
        self.pack_upload(d)
        pack = physical(self.mid(d, "/small/f0.js"))
        self.assertIn(pack, d.index.referenced_mids())                     # (checked as the pack, not per file)
        self.assertFalse(any(pack_slice(m)[1] is not None for m in d.index.referenced_mids()))
        d.backend.delete(pack)
        m = d.maintenance
        for _ in range(100):
            m._next_scrub = 0
            m.scrub_step(force=True)
            if m.scrub_state().get("passes"):
                break
        self.assertEqual((m.scrub_state().get("missing"), m.scrub_state().get("repaired")), (1, 1))
        for p, data in files.items():
            self.assertEqual(self.read(d, p), data)

    def test_a_copy_refers_to_the_part_already_stored(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        self.write(d, "/a.txt", b"the same small file")
        self.pack_upload(d)
        before = d.backend.uploads
        self.write(d, "/b.txt", b"the same small file")
        self.pack_upload(d)
        self.assertEqual(d.backend.uploads, before)                        # nothing new uploaded
        self.assertEqual(self.mid(d, "/b.txt"), self.mid(d, "/a.txt"))
        self.assertEqual(self.read(d, "/b.txt"), b"the same small file")

    def test_another_device_reads_the_packed_files(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        files = self.files(d, n=8)
        self.pack_upload(d)
        d2 = self.drive(device="bbbb", parity_group=4, parity_pieces=2)
        d2.journal.sync_once()
        for p, data in files.items():
            self.assertEqual(self.read(d2, p), data)
        # it knows the pack's spare pieces too: it can rebuild it
        pack = physical(self.mid(d2, "/small/f0.js"))
        self.assertEqual(len(d2.index.parity_groups_for(pack)), 1)

    def test_packing_can_be_turned_off(self):
        d = self.drive(parity_group=4, parity_pieces=2, pack_small_files=False)
        self.files(d, n=3)
        self.pack_upload(d)
        self.assertTrue(all(pack_slice(self.mid(d, f"/small/f{i}.js"))[1] is None for i in range(3)))

    def test_a_new_device_starting_from_a_checkpoint_reads_them(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        files = self.files(d, n=6)
        self.pack_upload(d)
        d.journal.checkpoint(force=True)
        cfg = Config(data_dir=os.path.join(self.tmp, "fresh"), chunk_size=helpers.PIECE, encryption_key=helpers.KEY,
                     device_id="cccc", upload_delay=0, web_enabled=False, parity_group=4, parity_pieces=2, poll_interval=0.5)
        d3 = DDrive(cfg, local_test_dir=self.channel)
        d3.initialize()
        self.drives.append(d3)
        for p, data in files.items():
            self.assertEqual(self.read(d3, p), data)


if __name__ == "__main__":
    helpers.unittest.main()
