"""A whole DDrive host for the Android tests: a drive whose "Discord" is a local folder, with its web
dashboard on a free local port. Prints {"port": ..., "phrase": ...} and runs until its input closes."""
import json
import os
import sys
import tempfile

repo = sys.argv[1]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "tests"))
import helpers  # noqa: E402
from ddrive import words  # noqa: E402
from ddrive.config import Config  # noqa: E402
from ddrive.drive import DDrive  # noqa: E402

tmp = tempfile.mkdtemp(prefix="dd-android-test-")
drive = DDrive(Config(data_dir=os.path.join(tmp, "host"), chunk_size=helpers.PIECE, encryption_key=helpers.KEY,
                      device_id="aaaa", upload_delay=0, web_enabled=False, web_token="secret-token", poll_interval=0.5),
               local_test_dir=os.path.join(tmp, "channel"))
drive.initialize()
drive.start_background_services()       # so that what is uploaded reaches the stand-in for Discord
web = drive.start_web(port=0)
print(json.dumps({"port": web.port, "phrase": words.key_to_phrase(bytes.fromhex(helpers.KEY))}), flush=True)
sys.stdin.read()
web.stop()
drive.stop_background_services()
