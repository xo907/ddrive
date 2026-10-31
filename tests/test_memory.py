"""What a host keeps in memory stays bounded, however long it runs and however many accounts upload at once."""

import threading
import time
import unittest
from unittest import mock

import helpers
from ddrive import fs, memwatch, uploader


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

    def test_what_was_going_on_is_said_when_memory_goes_up(self):
        gate, inside = threading.Event(), threading.Event()

        def slow_request():                      # (stands for a request that holds a lot)
            n = memwatch.begin("GET", "/api/vault/object?id=secret")
            inside.set()
            gate.wait(10)
            memwatch.end(n)
        t = threading.Thread(target=slow_request, name="Thread-7")
        t.start()
        inside.wait(5)
        watch, rss = memwatch.MemoryWatch(lambda: 2), [100 << 20]
        with mock.patch.object(memwatch, "_process_bytes", lambda: (rss[0], 1 << 30, 4 << 30)):
            self.assertIsNone(watch.check())                         # (well under 400 MB: nothing said)
            rss[0] = 450 << 20
            with self.assertLogs("ddrive.memory", "WARNING") as said:
                text = watch.check()
            self.assertIn("450 MB", text)
            self.assertIn("GET /api/vault/object for", text)
            self.assertNotIn("secret", text)                         # (no query: it may hold ids or keys)
            self.assertIn("Accounts with their drive open: 2", text)
            self.assertEqual(said.output[0].split(":", 2)[2], text)
            rss[0] = 500 << 20
            self.assertIsNone(watch.check())                         # (said again only 150 MB further up)
            rss[0] = 620 << 20
            self.assertIsNotNone(watch.check())
        gate.set()
        t.join()

    def test_a_threads_place_in_ddrive_is_found(self):
        import sys
        from ddrive import lite
        self.assertIsNone(memwatch._where(sys._getframe()))         # (a test's own code is not DDrive's)
        stop = threading.Event()

        class Client:                            # a host that takes its time to answer
            def request(self, method, path):
                stop.wait(10)
        t = threading.Thread(target=lite._wait_for_host, args=(Client(), 10), name="Busy-3")
        t.start()
        try:
            time.sleep(0.1)
            self.assertRegex(memwatch.report(1 << 30, 1 << 29, 1 << 32), r"Busy at lite\.py:\d+ _wait_for_host")
        finally:
            stop.set()
            t.join()

if __name__ == "__main__":
    unittest.main()
