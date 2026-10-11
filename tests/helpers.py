"""Shared test helpers: whole drives (index, journal, uploader, cache, filesystem) running against a
local folder that stands in for the Discord channel (backend.LocalBackend)."""

import logging
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ddrive.config import Config  # noqa: E402
from ddrive.crypto import generate_key  # noqa: E402
from ddrive.drive import DDrive  # noqa: E402

logging.basicConfig(level=logging.WARNING if not os.environ.get("DD_TEST_LOG") else logging.DEBUG,
                    format="%(levelname)s %(name)s: %(message)s")

KEY = generate_key().hex()
PIECE = 64 * 1024


class DriveTest(unittest.TestCase):
    """Each test gets a fresh fake channel; `drive()` adds a device to it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dd-test-")
        self.channel = os.path.join(self.tmp, "channel")
        self.drives = []

    def tearDown(self):
        for d in self.drives:
            try:
                d.stop_background_services()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drive(self, device="aaaa", **settings):
        opts = dict(data_dir=os.path.join(self.tmp, device), chunk_size=PIECE, encryption_key=KEY,
                    device_id=device, upload_delay=0, web_enabled=False, parity_group=4, parity_pieces=2,
                    poll_interval=0.5)
        opts.update(settings)
        d = DDrive(Config(**opts), local_test_dir=self.channel)
        d.initialize()
        self.drives.append(d)
        return d

    # ------------------------------------------------------------ actions
    @staticmethod
    def write(d, path, data):
        fs = d.fs
        parent = os.path.dirname(path)
        if parent not in ("", "/") and d.index.resolve(parent) is None:
            d.index.makedirs(parent)
        fh = fs.create(path, 0o644)
        fs.write(path, data, 0, fh)
        fs.release(path, fh)
        return d.index.resolve(path)["id"]

    @staticmethod
    def upload(d):
        for nid in d.index.unsynced_ids():
            d.uploader.run_now(nid)
        d.journal.sync_once()

    @staticmethod
    def read(d, path, cold=True):
        node = d.index.resolve(path)
        assert node is not None, f"{path} not found"
        if cold:
            d.fs._chunk_maps.clear()
            d.cache.free_space(force=True)
        return d.fs.read_file(node["id"], 0, node["size"])

    @staticmethod
    def sync(*drives):
        for _ in range(2):
            for d in drives:
                d.journal.sync_once()

    @staticmethod
    def empty_trash(d):
        d.uploader._running = True
        try:
            while d.uploader.clean_trash(grace=0, limit=1000):
                pass
        finally:
            d.uploader._running = False
