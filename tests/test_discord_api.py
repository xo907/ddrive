"""A request that dies midway is tried again, whatever curl says the status was."""

import subprocess
import unittest
from unittest import mock

import helpers  # noqa: F401
from ddrive import discord_api
from ddrive.discord_api import API_BASE, DiscordAPI, _curl_failure


def result(returncode, status, body=b"", stderr=b""):
    return subprocess.CompletedProcess([], returncode, body + b"\n__HTTP_STATUS__:%d" % status, stderr)


class CurlTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(discord_api, "time", mock.Mock(monotonic=lambda: 1000.0))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = DiscordAPI("token")
        self.api.curl_bin, self.api.has_curl = "curl", True
        self.api.set_pace("off")

    def upload(self, *results):
        with mock.patch.object(discord_api.subprocess, "run", side_effect=list(results)) as run:
            out = self.api._send("POST", API_BASE + "/channels/1/messages",
                                 multipart_file=("files[0]", "a.bin", b"data", None))
        return out, run

    def test_upload_cut_off_after_100_continue_is_retried(self):
        (status, data), run = self.upload(result(56, 100, stderr=b"curl: (56) Recv failure: Connection was reset"),
                                          result(0, 200, b'{"id": "5"}'))
        self.assertEqual((status, data), (200, b'{"id": "5"}'))
        self.assertEqual(run.call_count, 2)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("Expect:") - 1], "-H")     # curl doesn't ask for "100 Continue"

    def test_interim_status_alone_is_not_an_answer(self):
        (status, _), run = self.upload(result(0, 100), result(0, 200, b"{}"))
        self.assertEqual(status, 200)
        self.assertEqual(run.call_count, 2)

    def test_failure_in_words(self):
        self.assertEqual(_curl_failure(3221225773, b""), "this computer is out of memory, curl could not run")
        self.assertEqual(_curl_failure(3221225477, b""), "curl crashed with Windows error 0xC0000005")
        self.assertEqual(_curl_failure(6, b"curl: (6) Could not resolve host\nmore"), "curl: (6) Could not resolve host")
        self.assertEqual(_curl_failure(7, b""), "curl exit code 7")


if __name__ == "__main__":
    unittest.main()
