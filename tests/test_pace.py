"""Talking to Discord gently: requests are spaced out, and a "slow down" makes them slower for a while."""

import unittest
from unittest import mock

import helpers  # noqa: F401
from ddrive import discord_api
from ddrive.discord_api import API_BASE, DiscordAPI, Pacer


class FakeTime:
    """Stands in for the time module inside discord_api: sleeping moves the clock, nothing really waits.

    The real clock is too coarse to measure these waits (15.6 ms steps on Windows before Python 3.13)."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class PaceTest(unittest.TestCase):
    def setUp(self):
        self.time = FakeTime()
        patcher = mock.patch.object(discord_api, "time", self.time)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertSlept(self, expected):
        self.assertEqual(len(self.time.slept), len(expected), self.time.slept)
        for got, want in zip(self.time.slept, expected):
            self.assertAlmostEqual(got, want, places=6)

    def test_spacing(self):
        p = Pacer(per_minute=600)                     # one every 0.1 s
        for _ in range(4):
            p.wait()
        self.assertSlept([0.1, 0.1, 0.1])             # the first goes at once
        self.assertAlmostEqual(self.time.now, 1000.3, places=6)

    def test_slows_down_after_rate_limit(self):
        p = Pacer(per_minute=1200)                    # 0.05 s
        p.slow_down()
        p.wait()
        p.wait()
        self.assertSlept([0.075])                     # 1.5x slower now
        p.slow_down()
        p.wait()
        p.wait()
        self.assertSlept([0.075, 0.075, 0.1125])      # and slower again each time, 1.5 x 1.5
        for _ in range(5):
            p.slow_down()
        p.wait()                                      # this gap was already booked at 2.25x
        p.wait()
        self.assertSlept([0.075, 0.075, 0.1125, 0.1125, 0.2])  # never more than 4x slower
        self.time.now += 301                          # five minutes without a "slow down"
        p.wait()
        p.wait()
        self.assertSlept([0.075, 0.075, 0.1125, 0.1125, 0.2, 0.05])    # back to normal

    def test_kinds_and_settings(self):
        api = DiscordAPI("token")
        self.assertEqual(api._kind("DELETE", API_BASE + "/channels/1/messages/2"), "delete")
        self.assertEqual(api._kind("POST", API_BASE + "/channels/1/messages"), "post")
        self.assertEqual(api._kind("GET", API_BASE + "/channels/1/messages/2"), "other")
        self.assertIsNone(api._kind("GET", "https://cdn.discordapp.com/attachments/1/2/x.bin"))
        self.assertAlmostEqual(api.pacers["delete"].interval, 4.0)          # gentle by default: 15 a minute
        api.set_pace("fast", deletes=6)
        self.assertAlmostEqual(api.pacers["delete"].interval, 10.0)         # an explicit number wins
        api.set_pace("off")
        self.assertEqual(api.pacers["post"].interval, 0)


if __name__ == "__main__":
    unittest.main()
