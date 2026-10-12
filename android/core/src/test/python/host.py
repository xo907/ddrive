"""A whole DDrive host for the Android tests: a drive whose "Discord" is a local folder, with its web
dashboard on a free local port. Prints {"port": ..., "phrase": ...} and runs until its input closes.

Lines on its input are questions about an end-to-end encrypted account, answered by DDrive's own
client for those (ddrive/vault.py), which is how the tests know that what the phone sealed can be
opened by the others and the other way round:
    sha <key hex> <path>            the SHA-256 of the file at that path, as that client opens it
    put <key hex> <path> <text>     store a small file there
    upload                          have the host store everything it holds on "Discord"
    snap <key hex> <path>           keep a snapshot of the account that holds the file at that path (as the dashboard does)
    kept <key hex>                  the SHA-256 of that file as the snapshot has it
"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import time

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
        elif cmd[0] == "snap":
            was = dict(v.resolve(cmd[2]))
            listing = json.dumps({"v": 1, "at": time.time(), "entries": [
                {k: was[k] for k in ("id", "parent", "name", "dir", "size", "mtime", "obj", "key")}]}).encode()
            key2, oid, sid = os.urandom(32), vault.new_id(), vault.new_id()
            v._store(oid, key2, listing, None, len(listing))
            v._send({"op": "put", "id": sid, "parent": "~snapshots", "name": sid, "dir": False, "size": len(listing), "mtime": time.time(),
                     "obj": oid, "key": base64.b64encode(key2).decode(), "snap": {"at": time.time(), "label": "Manual", "files": 1, "bytes": was["size"]}})
            print("ok " + was.get("by", ""), flush=True)
        elif cmd[0] == "kept":
            snap = next(n for n in v.nodes.values() if n.get("snap"))
            e = json.loads(vault.open_range(base64.b64decode(snap["key"]), lambda a, n: v._fetch(snap["obj"], a, n), snap["size"], 0, None))["entries"][0]
            data = vault.open_range(base64.b64decode(e["key"]), lambda a, n: v._fetch(e["obj"], a, n), e["size"], 0, None)
            print(hashlib.sha256(data).hexdigest(), flush=True)
        elif cmd[0] == "put":
            v.put(cmd[2].replace("%20", " "), cmd[3].encode("utf-8"), mtime=1_700_000_000)
            print("ok", flush=True)
        else:
            print("what?", flush=True)
    except Exception as e:
        print("error " + str(e).replace("\n", " "), flush=True)
web.stop()
drive.stop_background_services()
