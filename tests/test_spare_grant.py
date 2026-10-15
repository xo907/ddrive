"""`spare grant`: every bot of the drive gets access to a spare channel in one go, and uploads go
around a bot the channel refuses."""

import io
from contextlib import redirect_stdout
from unittest import mock

import helpers

from ddrive import cli
from ddrive.backend import DiscordBackend
from ddrive.config import Config
from ddrive.discord_api import DiscordError

CHAN, GUILD = "500", "900"


def no_access():
    return DiscordError(403, '{"message": "Missing Access", "code": 50001}')


class FakeDiscord:
    """Bots by token; `allowed` is who may read the channel, `members` who is in its server."""

    def __init__(self, members, allowed):
        self.members, self.allowed, self.granted = set(members), set(allowed), []

    def api(self, token, **_k):
        world, me = self, {"id": f"id-{token}", "username": f"bot-{token}"}

        class Api:
            def get_me(self):
                return me

            def get_channel(self, chan):
                if token not in world.allowed:
                    raise no_access()
                return {"id": chan, "guild_id": GUILD}

            def get_messages(self, chan, limit=1, **_k):
                if token not in world.allowed:
                    raise no_access()
                return []

            def get_member(self, guild, user_id):
                if user_id.removeprefix("id-") not in world.members:
                    raise DiscordError(404, '{"message": "Unknown Member", "code": 10007}')
                return {}

            def allow_in_channel(self, chan, user_id, allow):
                world.granted.append(user_id)
                world.allowed.add(user_id.removeprefix("id-"))

        return Api()


class GrantTest(helpers.unittest.TestCase):
    def run_grant(self, world, extra):
        cfg = Config(bot_token="main", channel_id="1", extra_bot_tokens=extra)
        out = io.StringIO()
        with mock.patch.object(cli, "DiscordAPI", side_effect=world.api), redirect_stdout(out):
            left = cli._grant_bots(cfg, CHAN)
        return left, out.getvalue()

    def test_bots_in_the_server_are_given_access(self):
        bots = [f"b{i}" for i in range(10)]
        world = FakeDiscord(members=["main", *bots], allowed=["main", "b0"])
        left, text = self.run_grant(world, bots)
        self.assertEqual(left, 0)
        self.assertEqual(sorted(world.granted), sorted(f"id-b{i}" for i in range(1, 10)))
        self.assertEqual(text.count("given access"), 9)

    def test_a_bot_outside_the_server_gets_an_invite_link(self):
        world = FakeDiscord(members=["main", "b1"], allowed=["main"])
        left, text = self.run_grant(world, ["b1", "b2"])
        self.assertEqual(left, 1)
        self.assertIn(f"client_id=id-b2&scope=bot&permissions={cli.BOT_PERMISSIONS}&guild_id={GUILD}", text)

    def test_without_manage_permissions_it_says_what_to_do(self):
        world = FakeDiscord(members=["main", "b1"], allowed=["main"])

        def refuse(*_a):
            raise no_access()

        orig = world.api

        def api(token, **k):
            a = orig(token, **k)
            a.allow_in_channel = refuse
            return a

        world.api = api
        left, text = self.run_grant(world, ["b1"])
        self.assertEqual(left, 1)
        self.assertIn("Manage Permissions", text)


class UploadAroundRefusedBotTest(helpers.unittest.TestCase):
    def test_upload_skips_a_refused_bot(self):
        posted = []

        class Bot:
            def __init__(self, ok):
                self.ok = ok

            def send_file(self, chan, name, data, content):
                if not self.ok:
                    raise no_access()
                posted.append(self)
                return {"id": "7", "attachments": [{"id": "a", "url": "u", "size": len(data)}]}

        good, bad = Bot(True), Bot(False)
        be = DiscordBackend(good, CHAN, extra_apis=[bad])
        for _ in range(4):
            self.assertEqual(be.upload("x.bin", b"abc").message_id, "7")
        self.assertEqual(len(posted), 4)
        self.assertEqual([p["bot"] for p in be.bot_problems()], [2])


class AssignTest(helpers.unittest.TestCase):
    def test_each_spare_channel_gets_its_own_bot_at_random(self):
        bots = [f"b{i}" for i in range(5)]
        world = FakeDiscord(members=["main", *bots], allowed=["main"])
        cfg = Config(bot_token="main", channel_id="1", extra_bot_tokens=bots, spare_channels=["11", "12", "13"],
                     spare_bot_tokens=["", "b4", ""])
        out = io.StringIO()
        with mock.patch.object(cli, "DiscordAPI", side_effect=world.api), mock.patch.object(cfg, "save"), redirect_stdout(out):
            given = cli._assign_bots(cfg)
        self.assertEqual(given, 2)
        from ddrive.channels import own_bot
        owners = [own_bot(cfg, c) for c in ("11", "12", "13")]
        self.assertEqual(owners[1], "b4")                                   # kept
        self.assertEqual(len(set(owners)), 3)                               # one bot each, all different
        self.assertTrue(set(owners) <= set(bots))
        self.assertEqual(sorted(world.granted), sorted(f"id-{t}" for t in (owners[0], owners[2])))

    def test_a_bot_of_its_own_shares_its_connection(self):
        from ddrive.channels import with_spares
        from ddrive.discord_api import DiscordAPI
        main_bot, extra = DiscordAPI("main"), DiscordAPI("b1")
        main = DiscordBackend(main_bot, "1", extra_apis=[extra])
        cfg = Config(bot_token="main", channel_id="1", extra_bot_tokens=["b1"], spare_channels=["11"],
                     spare_bot_tokens=["b1"])
        spread = with_spares(cfg, main, api_for=lambda t: self.fail("a second connection for the same bot"))
        self.assertIs(spread.spares["11"].api, extra)
        self.assertEqual(spread.upload_slots, 2)
