"""DDrive itself never loses a piece: a refused bot is not a deleted piece, a group never leans on one
message twice, spare pieces always match, nothing is removed or deleted without proof, and every
deletion is written down."""

import hashlib
import json
import os
import time
from unittest import mock

import helpers

from ddrive.backend import DiscordBackend
from ddrive.discord_api import DiscordError, is_gone
from ddrive.heal import confirm_unreadable
from ddrive.uploader import find_deletion


def refused(*_a, **_k):
    raise DiscordError(403, '{"message": "Missing Access", "code": 50001}')


class FakeBot:
    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def get_message(self, channel_id, message_id):
        self.asked += 1
        return self.answer(message_id)


class BotsTest(helpers.unittest.TestCase):
    def test_only_a_real_404_means_gone(self):
        self.assertTrue(is_gone(DiscordError(404, '{"message": "Unknown Message", "code": 10008}')))
        self.assertTrue(is_gone(DiscordError(404, "message 1 has no attachment")))
        self.assertFalse(is_gone(DiscordError(404, '{"message": "Unknown Channel", "code": 10003}')))
        self.assertFalse(is_gone(DiscordError(403, '{"message": "Missing Access", "code": 50001}')))
        self.assertFalse(is_gone(DiscordError(500, "busy")))

    def test_a_refused_bot_is_not_the_last_word(self):
        good = FakeBot(lambda mid: {"id": mid, "attachments": [{"url": "u", "id": "a", "filename": "chk_1.bin"}]})
        bad = FakeBot(refused)
        be = DiscordBackend(good, "1", extra_apis=[bad])
        for _ in range(4):                       # the rotation lands on the refused bot too
            self.assertIsNotNone(be.message_info("42"))
        self.assertEqual([p["bot"] for p in be.bot_problems()], [2])
        asked = bad.asked
        for _ in range(4):
            be.message_info("42")
        self.assertEqual(bad.asked, asked)       # left out of the rotation for a while

    def test_every_bot_refused_is_an_error_not_a_missing_piece(self):
        be = DiscordBackend(FakeBot(refused), "1", extra_apis=[FakeBot(refused)])
        with self.assertRaises(DiscordError) as e:
            be.message_info("42")
        self.assertEqual(e.exception.status, 403)


class SafetyTest(helpers.DriveTest):
    def mids(self, d, path):
        return [c["message_id"] for c in d.index.get_chunks(d.index.resolve(path)["id"])]

    def test_a_refused_read_does_not_list_the_file_as_lost(self):
        d = self.drive(parity_enabled=False, dedup=False)
        self.write(d, "/a.bin", os.urandom(helpers.PIECE * 2))
        self.upload(d)
        with mock.patch.object(d.backend, "download", side_effect=refused):
            with self.assertRaises(Exception):
                self.read(d, "/a.bin")
            self.assertIsNone(d.healer.recover(self.mids(d, "/a.bin")[0]))
        self.assertEqual(d.healer._lost(), {})
        self.assertEqual(d.healer.lost_files(), [])

    def test_repeated_content_gets_a_message_per_position(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        block = b"\0" * helpers.PIECE
        self.write(d, "/zeros.bin", block)               # already stored: the next file could reuse it
        self.upload(d)
        self.write(d, "/disk.img", block * 4 + os.urandom(100))
        self.upload(d)
        group = self.mids(d, "/disk.img")[:4]
        self.assertEqual(len(set(group)), 4)
        for mid in group[:2]:                            # as many as the group has spare pieces
            d.backend.delete(mid)
        self.assertEqual(self.read(d, "/disk.img")[:len(block) * 4], block * 4)

    def test_a_resumed_group_with_a_changed_piece_gets_new_spare_pieces(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        nid = self.write(d, "/f.bin", os.urandom(helpers.PIECE * 4))
        node = d.index.get(nid)
        path = d.fs.staging_path(nid)
        with open(path, "rb") as f:
            pieces = [f.read(helpers.PIECE) for _ in range(4)]
        chunks = [{"idx": i, "message_id": f"m{i}", "size": len(p), "sha256": hashlib.sha256(p).hexdigest()}
                  for i, p in enumerate(pieces)]
        chunks[1]["sha256"] = "0" * 64                   # what was uploaded before is not what the file holds now
        d.index.kv_set(f"upload:{nid}", json.dumps({"v": node["version"], "size": node["size"],
                                                     "chunk_size": helpers.PIECE, "chunks": chunks, "k": 4,
                                                     "parity": {"0": ["g", [["s0", 1, "x"], ["s1", 1, "y"]]]}}))
        with open(path, "rb") as f:
            good, parity = d.uploader._resume_point(nid, f, node["version"], node["size"], helpers.PIECE, 4)
        self.assertEqual(sorted(good), [0, 2, 3])
        self.assertEqual(parity, {})

    def test_deleting_waits_and_is_written_down(self):
        d = self.drive(parity_enabled=False, dedup=False)
        self.write(d, "/a.txt", b"one")
        self.upload(d)
        mid = self.mids(d, "/a.txt")[0]
        d.index.purge_node(d.index.resolve("/a.txt")["id"])
        d.uploader._running = True
        try:
            self.assertEqual(d.uploader.clean_trash(limit=100), 0)       # a week, by default
            self.assertIsNotNone(d.backend.message_info(mid))
            self.assertIsNone(find_deletion(d.cfg, mid))
            self.assertEqual(d.uploader.clean_trash(grace=0, limit=100), 1)
        finally:
            d.uploader._running = False
        self.assertIsNone(d.backend.message_info(mid))
        line = find_deletion(d.cfg, mid)
        self.assertIsNotNone(line)
        self.assertIn("deleted", line)

    def test_remove_unreadable_needs_proof(self):
        d = self.drive(parity_enabled=False, dedup=False)
        nid = self.write(d, "/a.txt", b"hello")
        self.upload(d)
        self.assertFalse(confirm_unreadable(d.index, d.backend, d.crypto, nid))       # it reads fine
        with mock.patch.object(d.backend, "download", side_effect=refused):
            self.assertFalse(confirm_unreadable(d.index, d.backend, d.crypto, nid))   # a refusal proves nothing
        d.backend.delete(self.mids(d, "/a.txt")[0])
        self.assertTrue(confirm_unreadable(d.index, d.backend, d.crypto, nid))        # gone: proven

    def test_a_missing_piece_has_its_group_checked_at_once(self):
        d = self.drive(parity_group=4, parity_pieces=2)
        self.write(d, "/f.bin", os.urandom(helpers.PIECE * 4))
        self.upload(d)
        mids = self.mids(d, "/f.bin")
        d.backend.delete(mids[0])
        d.backend.delete(mids[3])
        st = {}
        d.maintenance._scrub_missing(mids[0], st)
        d.maintenance._scrub_group_of(mids[0], st)
        self.assertEqual(st.get("missing"), 2)
        self.assertEqual(st.get("repaired"), 2)

    def test_old_checkpoints_are_kept(self):
        d = self.drive()
        for i in range(8):
            self.write(d, f"/f{i}.txt", b"x")
            self.upload(d)
            d.journal.checkpoint(force=True)
        older = json.loads(d.index.kv_get("older_backup_mids"))
        self.assertEqual(len(older), 4)
        for mid in older:
            self.assertTrue(os.path.exists(os.path.join(self.channel, f"{mid}.bin")))
