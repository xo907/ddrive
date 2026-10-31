"""What a host keeps in memory stays bounded, however long it runs and however many accounts upload at once."""

import threading
import time
import unittest
from unittest import mock

import helpers
from ddrive import fs, uploader


class MemoryTest(helpers.DriveTest):
    def test_every_drive_shares_one_budget_of_pieces_in_flight(self):
        with mock.patch.object(uploader, "_budget", None), mock.patch.object(uploader, "_total_memory", lambda: 4 << 30):
            budget = uploader.piece_budget(9 << 20)
            self.assertIs(uploader.piece_budget(1 << 20), budget)          # (one for the whole program)
            most, now, lock = [0], [0], threading.Lock()

            def piece():
                with budget:
                    with lock:
                        now[0] += 1
                        most[0] = max(most[0], now[0])
                    time.sleep(0.05)
                    with lock:
                        now[0] -= 1
            ts = [threading.Thread(target=piece) for _ in range(40)]       # (two drives' worth of uploads at once)
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(most[0], 7)                   # 4 GB / 16 / (4 x 9 MB): not 16 for each drive
        with mock.patch.object(uploader, "_budget", None), mock.patch.object(uploader, "_total_memory", lambda: 256 << 20):
            self.assertEqual(uploader.piece_budget(9 << 20)._value, 2)    # (a tiny server still uploads)

    def test_the_lists_of_pieces_kept_are_capped(self):
        d = self.drive()
        with mock.patch.object(fs, "CHUNK_MAPS", 5):
            for i in range(12):
                self.write(d, f"/f{i}.bin", b"x" * 10)
            for i in range(12):
                d.fs._chunk_map(d.index.resolve(f"/f{i}.bin")["id"])     # (what reading and writing it look up)
            self.assertEqual(len(d.fs._chunk_maps), 5)
            last = d.index.resolve("/f11.bin")["id"]
            self.assertIn(last, d.fs._chunk_maps)                         # (the ones used last are kept)


if __name__ == "__main__":
    unittest.main()
