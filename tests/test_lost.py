"""When a piece can't be repaired, the files it belongs to are named."""

import time

import helpers


class LostPiecesTest(helpers.DriveTest):
    def mids(self, d, path):
        return [c["message_id"] for c in d.index.get_chunks(d.index.resolve(path)["id"])]

    def test_names_the_files(self):
        d = self.drive(parity_enabled=False, dedup=False)
        self.write(d, "/Docs/report.pdf", b"first version " * 5000)
        self.write(d, "/Docs/fine.txt", b"untouched")
        self.upload(d)
        old = self.mids(d, "/Docs/report.pdf")
        self.write(d, "/Docs/report.pdf", b"second version " * 5000)
        self.upload(d)
        new = self.mids(d, "/Docs/report.pdf")
        self.assertEqual(d.healer.lost_files(), [])
        d.backend.delete(new[0])                                          # Discord loses a piece of the file...
        d.backend.delete(old[0])                                          # ...and one of its earlier version
        with self.assertLogs("ddrive.heal", "WARNING") as logs:
            self.assertIsNone(d.healer.recover(new[0]))
            self.assertIsNone(d.healer.recover(old[0]))
            self.assertIsNone(d.healer.recover(new[0]))                   # said once, not on every read
        text = "\n".join(logs.output)
        self.assertEqual(text.count("/Docs/report.pdf"), 2)
        self.assertIn("A part of /Docs/report.pdf can't be read and can't be rebuilt", text)
        self.assertIn("earlier version of /Docs/report.pdf", text)
        self.assertNotIn("fine.txt", text)
        lost = d.healer.lost_files()
        self.assertEqual([(e["kind"], e["path"], e["pieces"]) for e in lost],
                         [("file", "/Docs/report.pdf", 1), ("version", "/Docs/report.pdf", 1)])
        d.fs.rename("/Docs/report.pdf", "/Docs/final.pdf")                # the list follows the file
        self.assertEqual(d.healer.lost_files()[0]["path"], "/Docs/final.pdf")
        d.index.purge_node(d.index.resolve("/Docs/final.pdf")["id"])      # deleted for good: nothing left to report
        self.assertEqual([e["kind"] for e in d.healer.lost_files()], ["version"])

    def test_a_piece_that_can_be_read_after_all_is_taken_off_the_list(self):
        d = self.drive(parity_enabled=False, dedup=False)
        self.write(d, "/clip.mp4", b"video " * 5000)
        self.upload(d)
        d.cache.free_space(force=True)
        mid = self.mids(d, "/clip.mp4")[0]
        path = d.backend._p(mid, "bin")
        with open(path, "rb") as f:
            stored = f.read()
        d.backend.delete(mid)                                             # for a moment Discord says it isn't there
        self.assertIsNone(d.healer.recover(mid))
        self.assertEqual([e["path"] for e in d.healer.lost_files()], ["/clip.mp4"])
        with open(path, "wb") as f:                                       # ...but it is
            f.write(stored)
        self.assertEqual(d.healer.recheck_lost(), 0)                      # not looked at again right away
        self.assertEqual(d.healer.recheck_lost(now=time.time() + 400), 1)
        self.assertEqual(d.healer.lost_files(), [])
        self.assertEqual(self.read(d, "/clip.mp4"), b"video " * 5000)
        d.cache.free_space(force=True)
        d.backend.delete(mid)                                             # really gone: it stays listed, and is asked about less often
        self.assertIsNone(d.healer.recover(mid))
        self.assertEqual(d.healer.recheck_lost(now=time.time() + 400), 0)
        self.assertEqual(d.healer.recheck_lost(now=time.time() + 500), 0)
        self.assertEqual(d.healer._rechecked[mid][0], 1)
        self.assertEqual(len(d.healer.lost_files()), 1)

    def test_a_piece_this_computer_still_holds_is_uploaded_again(self):
        d = self.drive(parity_enabled=False, dedup=False)
        self.write(d, "/clip.mp4", b"video " * 5000)
        self.upload(d)
        self.assertEqual(self.read(d, "/clip.mp4"), b"video " * 5000)    # opened here: in this computer's read cache
        mid = self.mids(d, "/clip.mp4")[0]
        d.backend.delete(mid)                                             # gone from Discord, no spare pieces
        self.assertIsNotNone(d.healer.recover(mid))                       # not lost: this computer has it
        d.journal.sync_once()
        self.assertEqual(d.healer.lost_files(), [])
        self.assertNotEqual(self.mids(d, "/clip.mp4")[0], mid)            # stored again under a new message
        other = self.drive("bbbb")                                        # so another device can read it too
        self.assertEqual(self.read(other, "/clip.mp4"), b"video " * 5000)
        # one that was already listed (found by an earlier version) is repaired the same way
        d.fs._chunk_maps.clear()
        self.assertEqual(self.read(d, "/clip.mp4", cold=False), b"video " * 5000)
        mid = self.mids(d, "/clip.mp4")[0]
        d.backend.delete(mid)
        d.healer._note_lost(mid)
        self.assertEqual(len(d.healer.lost_files()), 1)
        self.assertEqual(d.healer.recheck_lost(now=time.time() + 400), 1)
        d.journal.sync_once()
        self.assertEqual(d.healer.lost_files(), [])

    def test_a_lost_spare_piece_is_not_a_damaged_file(self):
        d = self.drive()
        self.write(d, "/big.bin", bytes(range(256)) * 1200)
        self.upload(d)
        shard = d.index._all("SELECT message_id FROM parity_shards LIMIT 1")[0]["message_id"]
        self.assertEqual(d.index.piece_users(shard)["protects"], ["/big.bin"])
        member = self.mids(d, "/big.bin")[0]
        d.backend.delete(member)                                          # one data piece gone: rebuilt from the spares
        self.assertIsNotNone(d.healer.recover(member))
        self.assertEqual(d.healer.lost_files(), [])
