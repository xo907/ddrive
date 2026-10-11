"""DDrive Lite: a computer that uses another computer's drive through its dashboard. Here the
host is a whole drive with its web dashboard on a free local port; the lite side is driven the way
the operating system would drive it (the calls a drive letter turns into), without mounting anything."""

import contextlib
import errno
import io
import os

import helpers
from ddrive import lite, words


class LiteTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.host = self.drive(web_token="secret-token")
        self.web = self.host.start_web(port=0)
        self.phrase = words.key_to_phrase(bytes.fromhex(self.host.cfg.encryption_key))
        self.address = f"http://127.0.0.1:{self.web.port}"
        self.client = lite.HostClient(self.address, self.phrase)
        self.fs = lite.LiteFS(self.client, tmp_dir=os.path.join(self.tmp, "lite-tmp"), block=64 * 1024, cache_bytes=256 * 1024)

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def save(self, path, data):
        fh = self.fs.create(path, 0o644)
        for off in range(0, len(data), 50000):
            self.fs.write(path, data[off:off + 50000], off, fh)
        self.fs.flush(path, fh)
        self.fs.release(path, fh)

    def load(self, path, offset=0, size=None):
        fh = self.fs.open(path, os.O_RDONLY)
        try:
            return self.fs.read(path, self.fs.getattr(path)["st_size"] if size is None else size, offset, fh)
        finally:
            self.fs.release(path, fh)

    def fails(self, code, fn, *args):
        with self.assertRaises(OSError) as e:
            fn(*args)
        self.assertEqual(e.exception.errno, code)

    def test_signing_in(self):
        with self.assertRaises(lite.HostError) as e:
            lite.HostClient(self.address, words.key_to_phrase(os.urandom(32))).login()
        self.assertEqual(e.exception.status, 401)
        with self.assertRaises(lite.HostError):
            lite.HostClient("ftp://example.com")
        with self.assertRaises(lite.HostError) as e:
            lite.HostClient("http://127.0.0.1:1", "a b", timeout=2).login()
        self.assertIn("can't be reached", str(e.exception))
        self.assertEqual(self.client.status()["role"], "owner")        # signs in by itself on the first request
        self.client.cookie = "stale.cookie"                            # ...and again when the session has run out
        self.assertEqual(self.client.list("/"), [])
        self.web.cfg.web_token = "another-secret"                      # everybody was signed out on the host
        self.assertEqual(self.client.list("/"), [])                    # it signs in again
        self.client.phrase = words.key_to_phrase(os.urandom(32))       # the words stopped being right
        self.client.cookie = "stale.cookie"
        with self.assertRaises(lite.HostError) as e:
            self.client.list("/")
        self.assertEqual(e.exception.status, 401)

    def test_the_drive_as_programs_use_it(self):
        fs = self.fs
        self.assertEqual(fs.readdir("/", None), [".", ".."])
        fs.mkdir("/Films", 0o755)
        self.fails(errno.EEXIST, fs.mkdir, "/Films", 0o755)
        big = os.urandom(300 * 1024)                                   # several of the parts it is read in
        self.save("/Films/clip.bin", big)
        self.assertEqual(os.listdir(fs.tmp_dir), [])                   # nothing is kept here once the host has it
        self.assertEqual(helpers.DriveTest.read(self.host, "/Films/clip.bin", cold=False), big)
        self.assertEqual(fs.readdir("/Films", None), [".", "..", "clip.bin"])
        attrs = fs.getattr("/films/CLIP.bin")                          # Windows doesn't care about capitals
        self.assertEqual((attrs["st_size"], attrs["st_mode"] & 0o170000), (len(big), 0o100000))
        self.assertEqual(fs.getattr("/Films")["st_mode"] & 0o170000, 0o040000)
        self.fails(errno.ENOENT, fs.getattr, "/Films/nothing.bin")

        # streaming: only the parts that are asked for, seeking anywhere, re-reading from memory
        self.assertEqual(self.load("/Films/clip.bin"), big)
        self.assertEqual(self.load("/Films/clip.bin", 200000, 5000), big[200000:205000])
        self.assertEqual(self.load("/Films/clip.bin", len(big) - 10, 500), big[-10:])
        self.assertEqual(self.load("/Films/clip.bin", len(big) + 5, 10), b"")
        self.assertLessEqual(fs._blocks_size, 256 * 1024)

        # changing part of a file: it is fetched, changed here, and handed back whole
        fh = fs.open("/Films/clip.bin", os.O_RDWR)
        self.assertEqual(fs.read("/Films/clip.bin", 4, 0, fh), big[:4])  # reading alone fetches nothing to disk
        self.assertEqual(os.listdir(fs.tmp_dir), [])
        fs.write("/Films/clip.bin", b"EDIT", 100, fh)
        self.assertEqual(fs.getattr("/Films/clip.bin")["st_size"], len(big))
        fs.release("/Films/clip.bin", fh)
        changed = big[:100] + b"EDIT" + big[104:]
        self.assertEqual(self.load("/Films/clip.bin"), changed)
        fs.truncate("/Films/clip.bin", 1000)
        self.assertEqual(self.load("/Films/clip.bin"), changed[:1000])
        fh = fs.open("/Films/clip.bin", os.O_WRONLY | lite.O_TRUNC)
        fs.write("/Films/clip.bin", b"new", 0, fh)
        fs.release("/Films/clip.bin", fh)
        self.assertEqual(self.load("/Films/clip.bin"), b"new")

        # saving over a file the way programs do: write a new one, then rename it over the old
        self.save("/Films/clip.tmp", b"saved again")
        fs.rename("/Films/clip.tmp", "/Films/clip.bin")
        self.assertEqual((fs.readdir("/Films", None), self.load("/Films/clip.bin")), ([".", "..", "clip.bin"], b"saved again"))
        fs.rename("/Films", "/Movies")
        self.assertEqual(self.load("/Movies/clip.bin"), b"saved again")
        self.fails(errno.ENOTEMPTY, fs.rmdir, "/Movies")
        self.fails(errno.EISDIR, fs.unlink, "/Movies")
        fs.unlink("/Movies/clip.bin")
        fs.rmdir("/Movies")
        self.assertEqual(fs.readdir("/", None), [".", ".."])
        self.save("/empty.txt", b"")
        self.assertEqual((fs.getattr("/empty.txt")["st_size"], self.load("/empty.txt")), (0, b""))

    def test_what_the_host_changes_shows_up(self):
        self.assertEqual(self.fs.readdir("/", None), [".", ".."])
        self.write(self.host, "/from-host.txt", b"hello")
        lite.LIST_SECONDS, keep = 0, lite.LIST_SECONDS
        try:
            self.assertEqual(self.fs.readdir("/", None), [".", "..", "from-host.txt"])
            self.assertEqual(self.load("/from-host.txt"), b"hello")
            self.write(self.host, "/from-host.txt", b"hello again")   # a newer version is not answered from memory
            self.assertEqual(self.load("/from-host.txt"), b"hello again")
        finally:
            lite.LIST_SECONDS = keep

    def test_commands(self):
        os.environ["DDRIVE_LITE_CONFIG"] = os.path.join(self.tmp, "lite.json")
        self.addCleanup(os.environ.pop, "DDRIVE_LITE_CONFIG")

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = lite.main(list(argv))
            return code, out.getvalue()
        self.assertEqual(run("ls")[0], 1)                              # not connected yet
        self.assertEqual(run("connect", self.address, "-p", words.key_to_phrase(os.urandom(32)))[0], 1)
        code, text = run("connect", self.address, "-p", self.phrase)
        self.assertEqual(code, 0, text)
        self.assertIn("as the owner", text)
        with open(os.environ["DDRIVE_LITE_CONFIG"]) as f:
            self.assertNotIn(self.phrase.split()[0] + " " + self.phrase.split()[1], f.read())   # the words are not stored readable
        src = os.path.join(self.tmp, "Album")
        os.makedirs(os.path.join(src, "sub"))
        for name, data in (("a.txt", b"one"), (os.path.join("sub", "b.txt"), b"two")):
            with open(os.path.join(src, name), "wb") as f:
                f.write(data)
        self.assertEqual(run("put", src, "/")[0], 0)
        self.assertEqual(self.read(self.host, "/Album/sub/b.txt", cold=False), b"two")
        self.assertIn("Album", run("ls")[1])
        self.assertIn("a.txt", run("ls", "/Album")[1])
        back = os.path.join(self.tmp, "back")
        self.assertEqual(run("get", "/Album", back)[0], 0)
        with open(os.path.join(back, "sub", "b.txt"), "rb") as f:
            self.assertEqual(f.read(), b"two")
        self.assertEqual(run("get", "/Album/missing.txt")[0], 1)
        self.assertIn(self.address, run("status")[1])
        run("disconnect")
        self.assertEqual(run("ls")[0], 1)


if __name__ == "__main__":
    helpers.unittest.main()
