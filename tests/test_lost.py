"""When a piece can't be repaired, the files it belongs to are named."""

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
