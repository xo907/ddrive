"""A whole DDrive host for the Android tests: a drive whose "Discord" is a local folder, with its web
dashboard on a free local port. Prints {"port": ..., "phrase": ...} and runs until its input closes.

Lines on its input are questions about an end-to-end encrypted account, answered by DDrive's own
client for those (ddrive/vault.py), which is how the tests know that what the phone sealed can be
opened by the others and the other way round:
    sha <key hex> <path>            the SHA-256 of the file at that path, as that client opens it
    put <key hex> <path> <text>     store a small file there
    upload                          have the host store everything it holds on "Discord"
"""
import hashlib
import json
import os
import sys
import tempfile

repo = sys.argv[1]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "tests"))
import helpers  # noqa: E402
from ddrive import lite, vault, words  # noqa: E402
from ddrive.config import Config  # noqa: E402
from ddrive.drive import DDrive  # noqa: E402

tmp = tempfile.mkdtemp(prefix="dd-android-test-")
drive = DDrive(Config(data_dir=os.path.join(tmp, "host"), chunk_size=helpers.PIECE, encryption_key=helpers.KEY,
                      device_id="aaaa", upload_delay=0, web_enabled=False, web_token="secret-token", poll_interval=0.5,
                      signup_mode="public"),
               local_test_dir=os.path.join(tmp, "channel"))
drive.initialize()
drive.start_background_services()       # so that what is uploaded reaches the stand-in for Discord
web = drive.start_web(port=0)
print(json.dumps({"port": web.port, "phrase": words.key_to_phrase(bytes.fromhex(helpers.KEY))}), flush=True)
for line in sys.stdin:
    cmd = line.rstrip("\n").split(" ", 3)
    try:
        if cmd[0] == "upload":
            for d in list(web.accounts._open.values()) + [drive]:
                helpers.DriveTest.upload(d)
            print("ok", flush=True)
            continue
        key = bytes.fromhex(cmd[1])
        v = vault.Vault(lite.HostClient(f"http://127.0.0.1:{web.port}", auth=vault.auth_key(key).hex()), key).sync()
        if cmd[0] == "sha":
            print(hashlib.sha256(v.read(cmd[2].replace("%20", " "))).hexdigest(), flush=True)
        elif cmd[0] == "put":
            v.put(cmd[2].replace("%20", " "), cmd[3].encode("utf-8"), mtime=1_700_000_000)
            print("ok", flush=True)
        else:
            print("what?", flush=True)
    except Exception as e:
        print("error " + str(e).replace("\n", " "), flush=True)
web.stop()
drive.stop_background_services()
