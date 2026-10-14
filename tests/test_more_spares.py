"""More spare pieces than pieces: one or several for every piece, small files included, and files
already stored get them when the setting is raised."""

import os

import helpers

from ddrive.config import Config
from ddrive.uploader import parity_shape


class ShapeTest(helpers.unittest.TestCase):
    def m(self, pieces, n, channels=1):
        return parity_shape(Config(parity_enabled=True, parity_group=10, parity_pieces=pieces), n, channels)[1]

    def test_small_files_get_their_share(self):
        self.assertEqual([self.m(2, n) for n in (1, 2, 3)], [1, 1, 1])        # as before
        self.assertEqual([self.m(10, n) for n in (1, 2, 3)], [1, 2, 3])       # one per piece
        self.assertEqual([self.m(30, n) for n in (1, 2, 3)], [3, 6, 9])       # three per piece

    def test_whole_groups(self):
        self.assertEqual(self.m(2, 10), 2)
        self.assertEqual(self.m(10, 10), 10)
        self.assertEqual(self.m(50, 10), 50)
        self.assertEqual(self.m(2, 10, channels=2), 10)                       # a spare channel needs as many


class MoreSparesTest(helpers.DriveTest):
    def mids(self, d, path):
        return [c["message_id"] for c in d.index.get_chunks(d.index.resolve(path)["id"])]

    def group(self, d, path):
        return d.index.parity_groups_for(self.mids(d, path)[0])

    def test_two_spares_for_every_piece(self):
        d = self.drive(parity_group=5, parity_pieces=10)
        data = os.urandom(helpers.PIECE * 5)
        self.write(d, "/f.bin", data)
        self.upload(d)
        g = self.group(d, "/f.bin")[0]
        self.assertEqual(len(g["shards"]), 10)
        for mid in [x[0] for x in g["members"]] + [x[0] for x in g["shards"]][:5]:     # 10 of 15 gone
            d.backend.delete(mid)
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_a_small_file_gets_a_spare_for_every_piece(self):
        d = self.drive(parity_group=10, parity_pieces=20)
        data = os.urandom(helpers.PIECE * 2)
        self.write(d, "/small.bin", data)
        self.upload(d)
        self.assertEqual(len(self.group(d, "/small.bin")[0]["shards"]), 4)

    def test_raising_the_setting_strengthens_what_is_stored(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        chans = [None]
        self.assertEqual(d.maintenance.groups_to_spread(chans), [])
        d.cfg.parity_pieces = 8
        todo = d.maintenance.groups_to_spread(chans)
        self.assertEqual(len(todo), 1)
        g = d.index.parity_group(todo[0])
        self.assertTrue(d.healer.spread(g, chans, d.maintenance.wanted(len(g["members"]), chans)))
        self.assertEqual(d.maintenance.groups_to_spread(chans), [])
        strongest = max(self.group(d, "/f.bin"), key=lambda x: len(x["shards"]))
        self.assertEqual(len(strongest["shards"]), 8)
        lose = [x[0] for x in strongest["members"]] + [x[0] for x in strongest["shards"]][:4]
        for mid in lose:                                                   # 8 of 12 gone
            d.backend.delete(mid)
        self.assertEqual(self.read(d, "/f.bin"), data)
