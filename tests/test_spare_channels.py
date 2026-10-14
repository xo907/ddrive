"""Spare channels: pieces spread over several channels so that losing any one whole channel (deleted,
purged, its bot banned) loses no file, for new uploads and for what was stored before."""

import os
import shutil
from unittest import mock

import helpers

from ddrive.channels import SpreadBackend, channel_of, counts, spread_pieces, with_spares, worst
from ddrive.config import Config
from ddrive.discord_api import DiscordError
from ddrive.drive import DDrive

SPARE, SPARE2 = "111", "222"


class ChannelMathTest(helpers.unittest.TestCase):
    def test_spare_pieces_needed(self):
        self.assertEqual(spread_pieces(10, 1), 0)
        self.assertEqual(spread_pieces(10, 2), 10)       # one spare channel: as much again
        self.assertEqual(spread_pieces(10, 3), 5)        # two: half
        self.assertEqual(spread_pieces(10, 5), 3)

    def test_ids(self):
        self.assertIsNone(channel_of("1234"))
        self.assertEqual(channel_of("111-1234"), "111")
        self.assertEqual(counts(["1", "2", "111-3"]), {None: 2, "111": 1})


class SpareChannelsTest(helpers.DriveTest):
    def chunks(self, d, path):
        return d.index.get_chunks(d.index.resolve(path)["id"])

    def group_mids(self, d, path):
        out = []
        for c in self.chunks(d, path):
            for g in d.index.parity_groups_for(c["message_id"]):
                out.append([x[0] for x in g["members"]] + [x[0] for x in g["shards"]])
        return out

    def lose_channel(self, d, chan):
        """Every piece in `chan` (None: the main channel) disappears."""
        mids = {m for ms in self.group_mids(d, "/f.bin") for m in ms}
        for mid in mids:
            if channel_of(mid) == chan:
                d.backend.delete(mid)

    def test_new_files_survive_losing_the_main_channel(self):
        d = self.drive(spare_channels=[SPARE])
        self.assertIsInstance(d.backend, SpreadBackend)
        data = os.urandom(helpers.PIECE * 6 + 123)
        self.write(d, "/f.bin", data)
        self.upload(d)
        for c in self.chunks(d, "/f.bin"):
            for g in d.index.parity_groups_for(c["message_id"]):     # no channel holds more than can be lost
                mids = [x[0] for x in g["members"]] + [x[0] for x in g["shards"]]
                self.assertLessEqual(worst(mids, [None, SPARE]), len(g["shards"]))
        self.lose_channel(d, None)
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_with_two_spare_channels_any_one_can_go(self):
        for lost in (None, SPARE, SPARE2):
            d = self.drive(device=f"d{lost}", spare_channels=[SPARE, SPARE2])
            data = os.urandom(helpers.PIECE * 4)
            self.write(d, "/f.bin", data)
            self.upload(d)
            for g in d.index.parity_groups_for(self.chunks(d, "/f.bin")[0]["message_id"]):
                self.assertEqual(len(g["shards"]), 2)     # half again, not as much again
            self.lose_channel(d, lost)
            self.assertEqual(self.read(d, "/f.bin"), data)

    def test_files_stored_before_are_spread(self):
        d = self.drive()
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        cfg = Config(**dict(d.cfg.__dict__, spare_channels=[SPARE]))
        spread = with_spares(cfg, d.backend, local_root=self.channel)
        for o in (d, d.healer, d.maintenance, d.uploader, d.cache, d.journal):
            o.backend = spread
        chans = spread.channels
        todo = d.maintenance.groups_to_spread(chans)
        self.assertEqual(len(todo), 1)
        for gid in todo:
            self.assertTrue(d.healer.spread(d.index.parity_group(gid), chans))
        self.assertEqual(d.maintenance.groups_to_spread(chans), [])
        self.lose_channel(d, None)
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_a_refused_spare_channel_is_read_around_and_nothing_is_counted_lost(self):
        d = self.drive(spare_channels=[SPARE])
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)

        def refused(*_a, **_k):
            raise DiscordError(403, '{"message": "Missing Access", "code": 50001}')

        with mock.patch.object(d.backend.spares[SPARE], "download", side_effect=refused):
            self.assertEqual(self.read(d, "/f.bin"), data)
        self.assertEqual(d.healer._lost(), {})
        self.assertEqual(d.healer.stats["repaired"], 0)

    def test_an_accounts_pieces_are_spread_too(self):
        from ddrive.accounts import AccountBackend
        from ddrive.channels import channels_of
        d = self.drive(spare_channels=[SPARE])
        acct = AccountBackend(d.backend, os.path.join(self.tmp, "acct-journal"), d.crypto)
        self.assertEqual(channels_of(acct), [None, SPARE])
        stored = acct.upload("chk_1.bin", b"sealed piece", channel=SPARE)
        self.assertEqual(channel_of(stored.message_id), SPARE)
        self.assertEqual(acct.download(stored.message_id, None)[0], b"sealed piece")
        self.assertIsNone(channel_of(acct.upload("chk_2.bin", b"main").message_id))

    def test_an_open_account_follows_the_owners_protection_settings(self):
        from dataclasses import asdict
        from ddrive.accounts import _AccountConfig
        owner = Config(parity_pieces=2, channel_id="1")
        cfg = _AccountConfig(**dict(asdict(owner), data_dir=self.tmp))
        cfg.owner = owner
        self.assertEqual(cfg.parity_pieces, 2)
        owner.parity_pieces = 50                       # changed on the dashboard while the account is open
        self.assertEqual(cfg.parity_pieces, 50)
        self.assertEqual(cfg.data_dir, self.tmp)       # its own settings stay its own

    def test_a_repair_goes_elsewhere_when_its_channel_is_gone(self):
        d = self.drive(spare_channels=[SPARE])
        real = d.backend.upload

        def upload(name, data, content="", channel=None):
            if channel == SPARE:
                raise DiscordError(404, '{"message": "Unknown Channel", "code": 10003}')
            return real(name, data, content, channel)

        with mock.patch.object(d.backend, "upload", side_effect=upload):
            stored = d.healer._upload_near(f"{SPARE}-123", b"x")
        self.assertIsNone(channel_of(stored.message_id))

    def test_a_new_main_channel_starts_from_the_spare_one(self):
        d = self.drive(spare_channels=[SPARE])
        data = os.urandom(helpers.PIECE * 5)
        self.write(d, "/Docs/f.bin", data)
        self.upload(d)
        d.journal.checkpoint(force=True)
        d.stop_background_services()
        # The main channel is deleted; a new, empty one takes its place. The spare channel is still there.
        fresh = os.path.join(self.tmp, "new-channel")
        shutil.copytree(f"{self.channel}-{SPARE}", f"{fresh}-{SPARE}")
        cfg = Config(data_dir=os.path.join(self.tmp, "new-device"), chunk_size=helpers.PIECE,
                     encryption_key=helpers.KEY, device_id="bbbb", upload_delay=0, web_enabled=False,
                     parity_group=4, parity_pieces=2, poll_interval=0.5, spare_channels=[SPARE])
        d2 = DDrive(cfg, local_test_dir=fresh)
        d2.initialize()
        self.drives.append(d2)
        self.assertIsNotNone(d2.index.resolve("/Docs/f.bin"))
        self.assertEqual(self.read(d2, "/Docs/f.bin"), data)
