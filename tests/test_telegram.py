"""Telegram as a second platform for the pieces (telegram.py): against a stand-in for Telegram's bot API
(fake_telegram.py), the pieces, the copies of the file list found again through the pinned message, and a
drive that loses every Discord channel at once and still has every file."""

import os
import shutil
from unittest import mock

import helpers

from fake_telegram import FakeTelegram
from ddrive import telegram
from ddrive.channels import SpreadBackend, channel_of, split
from ddrive.config import Config
from ddrive.crypto import KeyRing
from ddrive.drive import DDrive
from ddrive.telegram import TelegramAPI, TelegramBackend, TelegramError, check_chat, place_of

CHAT = "-1001234567890"
TOKEN = "123:secret"


class TelegramTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.stop)
        self.fake.add_chat(CHAT)
        patch = mock.patch.object(telegram, "PACE", 0.0)            # (no second between posts in a test)
        patch.start()
        self.addCleanup(patch.stop)

    def backend(self, crypto=None):
        return TelegramBackend(TelegramAPI(TOKEN, timeout=10, retries=2, base=self.fake.base), CHAT, crypto=crypto)

    # ------------------------------------------------------------ the backend itself
    def test_a_piece_goes_there_comes_back_and_goes(self):
        b = self.backend()
        data = os.urandom(5000)
        piece = b.upload("chk_abc.bin", data)
        self.assertEqual(b.download(piece.message_id, piece.url), (data, None))
        self.assertEqual(b.download(piece.message_id, None)[0], data)       # (its id alone is enough)
        self.assertIsNotNone(b.message_info(piece.message_id))
        self.assertAlmostEqual(b.time_of(piece.message_id), self.fake.documents(CHAT)[0]["date"], delta=1)
        b.delete(piece.message_id)
        self.assertIsNone(b.message_info(piece.message_id))
        b.delete(piece.message_id)                                           # (gone already: fine)
        # Its place in the drive: no "-" in the name, and the piece's own "-"s (file ids have them) stay its own
        place = place_of(CHAT)
        self.assertNotIn("-", place)
        self.assertEqual(split(f"{place}-{piece.message_id}"), (place, piece.message_id))
        self.assertIn("-", piece.url + "-")

    def test_too_many_requests_are_waited_out_and_too_big_is_refused(self):
        b = self.backend()
        self.fake.too_many = 2
        piece = b.upload("x.bin", b"hello")
        self.assertEqual(b.download(piece.message_id, piece.url)[0], b"hello")
        with self.assertRaises(TelegramError):
            b.upload("big.bin", b"x" * (telegram.MAX_READ + 1))

    def test_a_chat_that_refuses_the_bot_is_left_out_for_a_while(self):
        b = TelegramBackend(TelegramAPI(TOKEN, timeout=10, retries=0, base=self.fake.base), "-100999", crypto=None)
        with self.assertRaises(TelegramError):
            b.upload("x.bin", b"x")
        self.assertTrue(b.all_refused())
        self.assertEqual(len(b.bot_problems()), 1)

    def test_copies_of_the_file_list_are_found_again_through_the_pinned_message(self):
        crypto = KeyRing.from_hex(helpers.KEY, [])
        b = self.backend(crypto)
        for n in range(3):
            b.save_index(f"index {n}".encode(), cursor=100 + n, tag="555")
        found = b.index_candidates()
        self.assertEqual(len(found), telegram.KEEP_INDEX)                  # (the oldest went)
        self.assertIn("c=102", found[0]["content"])
        self.assertIn("m=555", found[0]["content"])
        self.assertEqual(b.load_index_message(found[0]), b"index 2")
        b.save_pinned("accounts.bin", b"the accounts", "DDRIVE_ACCOUNTS v1\nlist")
        # A new start (another computer): everything is found from the pinned directory alone
        again = self.backend(crypto)
        self.assertEqual(again.load_latest_index()[1], b"index 2")
        got = again.pinned("DDRIVE_ACCOUNTS")
        self.assertEqual(len(got), 1)
        self.assertEqual(again.fetch_attachment(got[0]), b"the accounts")
        again.unpin_delete(got[0]["id"])
        self.assertEqual(self.backend(crypto).pinned("DDRIVE_ACCOUNTS"), [])
        # Only the pinned directory, the index copies left and nothing else stay in the chat
        self.assertEqual(len(self.fake.documents(CHAT)), telegram.KEEP_INDEX + 1)

    def test_erasing_deletes_every_message(self):
        b = self.backend()
        for n in range(5):
            b.upload(f"{n}.bin", b"x")
        b.save_index(b"index", cursor=1)
        b.wipe()
        self.assertEqual(self.fake.chats[CHAT]["messages"], {})

    def test_a_chat_is_checked_before_it_is_used(self):
        chat_id, title, bot, problems = check_chat(TOKEN, CHAT, base=self.fake.base)
        self.assertEqual((chat_id, title, bot, problems), (CHAT, "Pieces", "@ddrive_test_bot", []))
        self.fake.can_delete = False
        self.assertTrue(any("delete" in p for p in check_chat(TOKEN, CHAT, base=self.fake.base)[3]))
        self.fake.admin = False
        self.assertTrue(any("admin" in p for p in check_chat(TOKEN, CHAT, base=self.fake.base)[3]))
        self.fake.add_chat("-100777", kind="supergroup")
        self.assertTrue(any("group" in p for p in check_chat(TOKEN, "-100777", base=self.fake.base)[3]))
        with self.assertRaises(TelegramError):
            check_chat(TOKEN, "-100404", base=self.fake.base)
        self.assertEqual(self.fake.documents(CHAT), [])                       # (the test files went again)

    def test_the_chats_a_bot_has_heard_of_are_found(self):
        from ddrive.telegram import chats_seen
        bot, seen = chats_seen(TOKEN, base=self.fake.base)
        self.assertEqual((bot, seen), ("@ddrive_test_bot", [(CHAT, "Pieces", "channel")]))

    # ------------------------------------------------------------ a drive with Telegram
    def tg_drive(self, device="aaaa", **more):
        return self.drive(device=device, telegram_chats=[CHAT], telegram_bot_tokens=[TOKEN], telegram_api=self.fake.base, **more)

    def groups(self, d, path):
        out = []
        for c in d.index.get_chunks(d.index.resolve(path)["id"]):
            for g in d.index.parity_groups_for(c["message_id"]):
                out.append(([x[0] for x in g["members"]], [x[0] for x in g["shards"]]))
        return out

    def lose(self, d, path, telegram_side):
        """Every piece of `path` on one platform disappears (Discord: every channel at once)."""
        place = place_of(CHAT)
        for members, shards in self.groups(d, path):
            for mid in members + shards:
                if (channel_of(mid) == place) == telegram_side:
                    d.backend.delete(mid)

    def test_a_file_survives_losing_all_of_discord_or_all_of_telegram(self):
        for side in (False, True):
            d = self.tg_drive(device=f"d{side}", spare_channels=["111"])
            self.assertIsInstance(d.backend, SpreadBackend)
            self.assertIsInstance(d.backend.spares[place_of(CHAT)], TelegramBackend)
            data = os.urandom(helpers.PIECE * 6 + 77)
            self.write(d, "/f.bin", data)
            self.upload(d)
            place = place_of(CHAT)
            for members, shards in self.groups(d, "/f.bin"):
                on_tg = sum(channel_of(m) == place for m in members + shards)
                self.assertGreaterEqual(len(shards), len(members))            # as many spare pieces as data pieces
                self.assertGreaterEqual(on_tg, len(members))                   # Telegram alone can rebuild it
                self.assertGreaterEqual(len(members) + len(shards) - on_tg, len(members))   # and so can Discord
            self.assertTrue(self.fake.documents(CHAT))
            self.lose(d, "/f.bin", telegram_side=side)
            self.assertEqual(self.read(d, "/f.bin"), data)

    def test_files_stored_before_get_their_share_on_telegram(self):
        d = self.drive()
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        from ddrive.channels import with_spares
        cfg = Config(**dict(d.cfg.__dict__, telegram_chats=[CHAT], telegram_bot_tokens=[TOKEN], telegram_api=self.fake.base))
        spread = with_spares(cfg, d.backend, local_root=self.channel)
        for o in (d, d.healer, d.maintenance, d.uploader, d.cache, d.journal):
            o.backend = spread
        chans = spread.channels
        todo = d.maintenance.groups_to_spread(chans)
        self.assertEqual(len(todo), 1)
        for gid in todo:
            self.assertTrue(d.healer.spread(d.index.parity_group(gid), chans))
        self.assertEqual(d.maintenance.groups_to_spread(chans), [])
        self.lose(d, "/f.bin", telegram_side=False)                          # every Discord piece gone
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_the_background_check_asks_about_telegram_pieces_one_by_one(self):
        d = self.tg_drive()
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/f.bin", data)
        self.upload(d)
        place = place_of(CHAT)
        gone = next(m for members, shards in self.groups(d, "/f.bin") for m in members + shards if channel_of(m) == place)
        d.backend.delete(gone)
        m = d.maintenance
        for _ in range(200):
            m._next_scrub = 0
            m.scrub_step(force=True)
            if m.scrub_state().get("passes"):
                break
        st = m.scrub_state()
        self.assertEqual(st.get("missing"), 1)
        self.assertEqual(st.get("repaired"), 1)
        self.assertEqual(self.read(d, "/f.bin"), data)

    def test_a_new_start_after_discord_is_gone_finds_the_file_list_on_telegram(self):
        d = self.tg_drive()
        data = os.urandom(helpers.PIECE * 4)
        self.write(d, "/Docs/f.bin", data)
        self.upload(d)
        d.journal.checkpoint(force=True)
        d.stop_background_services()
        # Every Discord channel is gone: a new, empty one is made. Telegram still has its pieces and the copy.
        fresh = os.path.join(self.tmp, "new-channel")
        cfg = Config(data_dir=os.path.join(self.tmp, "new-device"), chunk_size=helpers.PIECE, encryption_key=helpers.KEY,
                     device_id="bbbb", upload_delay=0, web_enabled=False, parity_group=4, parity_pieces=2, poll_interval=0.5,
                     telegram_chats=[CHAT], telegram_bot_tokens=[TOKEN], telegram_api=self.fake.base)
        d2 = DDrive(cfg, local_test_dir=fresh)
        d2.initialize()
        self.drives.append(d2)
        self.assertIsNotNone(d2.index.resolve("/Docs/f.bin"))
        self.assertEqual(self.read(d2, "/Docs/f.bin"), data)
        shutil.rmtree(fresh, ignore_errors=True)


if __name__ == "__main__":
    helpers.unittest.main()
