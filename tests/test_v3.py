"""3.0: whole servers can be lost, given-up channels are rebuilt elsewhere, restoring never stops at
one bad checkpoint, copies of checkpoints and of account checkpoints live in spare channels, and the
background work is quicker without holding more in memory."""

import gzip
import json
import os
import shutil
from unittest import mock

import helpers

from ddrive import rs
from ddrive.accounts import AccountBackend
from ddrive.channels import Layout, channel_of, tag_of
from ddrive.heal import confirm_unreadable, needed_spares

A, B, C = "111", "222", "333"


class LayoutTest(helpers.unittest.TestCase):
    def test_servers_first(self):
        lay = Layout([None, A, B, C], {None: "s1", A: "s1", B: "s2", C: "s2"})
        self.assertTrue(lay.by_server)
        self.assertEqual(lay.places, 2)
        self.assertEqual(lay.need(10), 10)                       # any one of two servers: as much again
        self.assertEqual(lay.order(), [None, B, A, C])            # turns over the servers
        where = lay.fill([], 20)
        per_server = {"s1": 0, "s2": 0}
        for c in where:
            per_server[lay.server[c]] += 1
        self.assertEqual(per_server, {"s1": 10, "s2": 10})

    def test_one_server_is_channels(self):
        lay = Layout([None, A, B], {None: "s1", A: "s1", B: "s1"})
        self.assertFalse(lay.by_server)
        self.assertEqual(lay.places, 3)
        self.assertEqual(lay.need(10), 5)

    def test_repeated_pieces_cost_more(self):
        self.assertEqual(needed_spares(["a", "b", "c"], 2), 2)
        self.assertEqual(needed_spares(["z", "z", "z", "b"], 2), 4)     # one message, three places


class EncoderRowsTest(helpers.unittest.TestCase):
    def test_rows_are_the_same_pieces(self):
        pieces = [os.urandom(1000) for _ in range(5)]
        full = rs.encode(pieces, 9)
        enc = rs.Encoder(5, 9, [7, 2])
        for i, p in enumerate(pieces):
            enc.add(i, p)
        self.assertEqual(enc.parity(), [full[7], full[2]])


class V3Test(helpers.DriveTest):
    def mids(self, d, path):
        return [c["message_id"] for c in d.index.get_chunks(d.index.resolve(path)["id"])]

    def group_mids(self, d, path):
        out = set()
        for mid in self.mids(d, path):
            for g in d.index.parity_groups_for(mid):
                out.update(x[0] for x in g["members"])
                out.update(x[0] for x in g["shards"])
        return out

    def test_a_whole_server_can_be_lost(self):
        d = self.drive(channel_id="1", spare_channels=[A, B, C],
                       channel_servers={"1": "s1", A: "s1", B: "s2", C: "s2"})
        data = os.urandom(helpers.PIECE * 8)
        self.write(d, "/f.bin", data)
        self.upload(d)
        for mid in self.group_mids(d, "/f.bin"):                  # server s1 (main and A) is gone
            if channel_of(mid) in (None, A):
                d.backend.delete(mid)
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_a_given_up_channel_is_rebuilt_elsewhere(self):
        d = self.drive(spare_channels=[A, B])
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        self.assertGreater(d.index.pieces_in_channel(A), 0)
        shutil.rmtree(f"{self.channel}-{A}")                      # the channel is deleted...
        d.backend.lost.add(A)                                     # ...and given up (spare lost)
        del d.backend.spares[A]
        for _ in range(10):
            d.maintenance._next_rehome = 0
            d.maintenance.rehome_step()
            d.journal.sync_once()
            if not d.index.pieces_in_channel(A):
                break
        self.assertEqual(d.index.pieces_in_channel(A), 0)
        self.assertEqual(self.read(d, "/f.bin"), data)
        self.assertEqual(d.healer._lost(), {})

    def test_a_damaged_newest_checkpoint_is_not_the_end(self):
        d = self.drive()
        self.write(d, "/Docs/a.txt", b"first")
        self.upload(d)
        d.journal.checkpoint(force=True)
        self.write(d, "/Docs/b.txt", b"second")
        self.upload(d)
        d.journal.checkpoint(force=True)
        newest = d.index.kv_get("last_backup_mid")
        with open(os.path.join(self.channel, f"{newest}.bin"), "wb") as f:
            f.write(b"DENC" + os.urandom(200))                     # damaged on Discord
        d.stop_background_services()
        d2 = self.drive("bbbb")
        self.assertIsNotNone(d2.index.resolve("/Docs/a.txt"))
        self.assertIsNotNone(d2.index.resolve("/Docs/b.txt"))      # the journal after the older one brings it back

    def test_no_checkpoint_at_all_rebuilds_from_the_journal(self):
        d = self.drive()
        self.write(d, "/Docs/a.txt", b"hello")
        self.upload(d)
        d.stop_background_services()
        for name in os.listdir(self.channel):                       # every checkpoint is gone
            p = os.path.join(self.channel, name)
            if name.endswith(".json"):
                with open(p, encoding="utf-8") as f:
                    meta = json.load(f)
                content = (meta.get("content") or "") if isinstance(meta, dict) else ""
                if content.startswith("DISCORDDRIVE_INDEX"):
                    os.remove(p)
                    os.remove(p[:-5] + ".bin")
        d2 = self.drive("bbbb")
        self.assertIsNotNone(d2.index.resolve("/Docs/a.txt"))
        self.assertEqual(self.read(d2, "/Docs/a.txt"), b"hello")

    def test_checkpoints_are_copied_and_a_foreign_one_is_left_alone(self):
        d = self.drive(channel_id="1", spare_channels=[A])
        spare = d.backend.spares[A]
        foreign = spare.save_index(gzip.compress(b"x"), cursor="5", tag="999")   # another drive's copy
        for i in range(4):
            self.write(d, f"/f{i}.txt", b"x")
            self.upload(d)
            d.journal.checkpoint(force=True)
        mine = [m for m in spare.index_candidates(0) if tag_of(m) == "1"]
        self.assertEqual(len(mine), 2)
        self.assertIn(foreign, [m["id"] for m in spare.index_candidates(0)])

    def test_unreadable_only_when_spare_pieces_cant_help(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        data = os.urandom(helpers.PIECE * 4)
        nid = self.write(d, "/f.bin", data)
        self.upload(d)
        first = self.mids(d, "/f.bin")[0]
        d.backend.delete(first)
        self.assertTrue(confirm_unreadable(d.index, d.backend, d.crypto, nid))              # as before, without help
        self.assertFalse(confirm_unreadable(d.index, d.backend, d.crypto, nid, d.healer))   # it can be rebuilt

    def test_daily_copy_of_the_file_list(self):
        d = self.drive()
        self.write(d, "/a.txt", b"x")
        self.upload(d)
        d.maintenance._next_backup = 0
        d.maintenance.backup_step()
        folder = os.path.join(os.path.dirname(d.index.path), "backups")
        names = os.listdir(folder)
        self.assertEqual(len(names), 1)
        with gzip.open(os.path.join(folder, names[0])) as f:
            self.assertTrue(f.read(16).startswith(b"SQLite format 3"))

    def test_an_accounts_checkpoint_has_copies(self):
        d = self.drive(spare_channels=[A, B])
        acct = AccountBackend(d.backend, os.path.join(self.tmp, "acct-journal"), d.crypto)
        acct.save_index(gzip.compress(b"index"), cursor="7")
        latest = acct.find_latest_index()
        self.assertEqual(len(latest["copies"]), 2)
        d.backend.delete(latest["id"])                            # the main channel's copy is gone
        self.assertEqual(gzip.decompress(acct.load_index_message(latest)), b"index")
        for _ in range(4):                                       # older generations are cleaned up
            acct.save_index(gzip.compress(b"index"), cursor="8")
        self.assertEqual(len(acct.checkpoint["older"]), 2)

    def test_the_background_check_lists_channels(self):
        d = self.drive(spare_channels=[A])
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        gone = next(m for m in self.group_mids(d, "/f.bin") if channel_of(m) == A)
        d.backend.delete(gone)
        asked = []
        real = d.backend.message_info
        with mock.patch.object(d.backend, "message_info", side_effect=lambda m: asked.append(m) or real(m)):
            for _ in range(20):
                d.maintenance.scrub_step(force=True)
        self.assertEqual(asked[0], gone)                         # the one missing from the list, then its group
        self.assertLessEqual(len(asked), len(self.group_mids(d, "/f.bin")))
        self.assertEqual(d.maintenance.scrub_state().get("repaired"), 1)

    def test_many_groups_overlap_and_all_get_spare_pieces(self):
        d = self.drive(parity_group=4, parity_pieces=3)
        data = os.urandom(helpers.PIECE * 18 + 5)
        self.write(d, "/big.bin", data)
        self.upload(d)
        mids = self.mids(d, "/big.bin")
        groups = {g["gid"] for m in mids for g in d.index.parity_groups_for(m)}
        self.assertEqual(len(groups), 5)
        for m in mids[::4][:5]:                                  # one piece of every group lost
            d.backend.delete(m)
        self.assertEqual(self.read(d, "/big.bin"), data)

    def test_a_staging_copy_nothing_knows_is_kept(self):
        d = self.drive()
        orphan = os.path.join(d.fs.staging_dir, "424242.dat")
        with open(orphan, "wb") as f:
            f.write(b"only copy")
        d.fs.recover()
        kept = os.listdir(os.path.join(os.path.dirname(d.fs.staging_dir), "recovered"))
        self.assertEqual(len(kept), 1)
        self.assertFalse(os.path.exists(orphan))


class VerdictTest(helpers.DriveTest):
    def test_says_what_is_not_safe(self):
        from ddrive.cli import safety_verdict
        d = self.drive()
        st = {"chunks": 10, "protected_chunks": 8}
        lines = safety_verdict(d.cfg, st, [], 3, Layout([None]))
        text = "\n".join(lines)
        self.assertIn("Not fully safe", text)
        self.assertIn("2 piece(s) have no spare pieces", text)
        self.assertIn("one channel", text)
        os.makedirs(os.path.join(d.cfg.resolved_data_dir, "backups"), exist_ok=True)
        with open(os.path.join(d.cfg.resolved_data_dir, "backups", "index-x.db.gz"), "wb") as f:
            f.write(b"x")
        lay = Layout([None, A], {None: "s1", A: "s2"})
        self.assertEqual(safety_verdict(d.cfg, {"chunks": 4, "protected_chunks": 4}, [], 0, lay)[0][:4], "[OK]")


class ReleasedMembersTest(helpers.DriveTest):
    def test_a_group_of_an_expired_version_is_not_strengthened(self):
        d = self.drive(parity_group=4, parity_pieces=2, keep_versions=False)
        a, b = os.urandom(helpers.PIECE), os.urandom(helpers.PIECE)
        self.write(d, "/f.bin", a + b + os.urandom(helpers.PIECE * 2))
        self.upload(d)
        self.write(d, "/f.bin", a + b + os.urandom(helpers.PIECE * 2))     # shares two pieces with the old one
        self.upload(d)
        self.assertEqual(len(d.index.all_groups()), 2)                       # the old group still holds a and b
        d.cfg.parity_pieces = 4
        todo = d.maintenance.groups_to_spread([None])
        self.assertEqual(len(todo), 1)
        g = d.index.parity_group(todo[0])
        self.assertTrue(d.healer.spread(g, [None], 4))                       # the current version's: works
