"""The dashboard in a real browser (Playwright, headless Chromium): what only a browser does, end to end,
against a whole drive with its dashboard on a free local port. Signing in (the owner, and an end-to-end
encrypted account made in the page), uploading and downloading (an end-to-end account's file is opened in
the page), the menu's pages left out, the update notice and the page waiting for a drive that is away, and
password locks of an end-to-end account: locking, unlocking, a wrong password, locking again by itself
after 15 minutes without use (a fake clock), and taking the lock off.

Needs `pip install playwright` and a browser: `python -m playwright install chromium` (CI does both), or an
installed Edge or Chrome (DD_BROWSER=msedge / chrome). Without one these tests are skipped."""

import os
import tempfile
import unittest

import helpers
from ddrive import words

try:
    from playwright.sync_api import sync_playwright
except ImportError:                          # (not installed: the tests are skipped)
    sync_playwright = None


def _launch(p):
    """Chromium (Playwright's own), else an installed Edge or Chrome."""
    wanted = os.environ.get("DD_BROWSER")
    tries = [wanted] if wanted else [None, "msedge", "chrome"]
    last = None
    for channel in tries:
        try:
            return p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
        except Exception as e:                  # this one isn't installed: the next
            last = e
    raise unittest.SkipTest(f"no browser for the dashboard tests ({last})")


@unittest.skipUnless(sync_playwright, "needs Playwright (pip install playwright)")
class BrowserTest(helpers.DriveTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = _launch(cls.pw)
        except unittest.SkipTest:
            cls.pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.d.cfg.signup_mode = "public"
        self.web = self.d.start_web(port=0)
        self.base = f"http://localhost:{self.web.port}"     # (localhost: a secure context, so the page may encrypt)
        self.ctx = self.browser.new_context(accept_downloads=True)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(30000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.ctx.close()
        try:
            self.web.stop()
        except Exception:
            pass
        super().tearDown()

    # ------------------------------------------------------------ helpers
    def sign_in_owner(self):
        p = self.page
        p.goto(self.base + "/")
        p.fill('form[action="/login"] textarea', words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key)))
        p.click('form[action="/login"] button[type=submit]')
        # (no end-to-end account has these words: the page offers to send them, the owner's way)
        p.click("text=Send the words and sign in")
        p.wait_for_selector("#nav a[data-nav=files]")

    def sign_up_e2e(self):
        """A new end-to-end encrypted account, signed in in the page. Returns its 24 words."""
        p = self.page
        p.goto(self.base + "/signup")
        p.click("text=Get my 24 words")
        p.wait_for_function("() => document.querySelectorAll('#words li').length === 24")     # (made in the page: not at once)
        phrase = " ".join(li.inner_text() for li in p.query_selector_all("#words li"))
        self.assertEqual(len(phrase.split()), 24)
        for box in p.query_selector_all("main input[type=checkbox]"):
            box.check()
        p.click("text=Create my account")
        p.wait_for_selector("#nav a[data-nav=files]")
        p.wait_for_function("() => window.E2E && E2E.active")            # (the account's list is opened in the page)
        return phrase

    def api(self, method, path, body=None):
        return self.page.evaluate("""async ([method, path, body]) => {
            const r = await fetch(path, method === 'GET' ? {} : { method, headers: { 'Content-Type': 'application/json', 'X-DD': '1' }, body: JSON.stringify(body || {}) });
            let j = null; try { j = await r.json(); } catch (e) {}
            return { status: r.status, json: j };
        }""", [method, path, body])

    def upload_text(self, path, text):
        r = self.page.evaluate("""async ([path, text]) => {
            const r = await fetch('/api/upload?path=' + encodeURIComponent(path), { method: 'POST', headers: { 'X-DD': '1' }, body: new Blob([text]) });
            return r.status;
        }""", [path, text])
        self.assertEqual(r, 200)

    def names(self, path="/"):
        got = self.api("GET", "/api/list?path=" + path)
        return sorted(i["name"] for i in got["json"]["items"])

    def download(self, path):
        with self.page.expect_download() as info:
            self.page.evaluate("p => download(fileUrl(p, true), p.split('/').pop())", path)
        d = info.value
        target = os.path.join(tempfile.mkdtemp(prefix="dd-dl-"), d.suggested_filename)
        d.save_as(target)
        with open(target, "rb") as f:
            return d.suggested_filename, f.read()

    # ------------------------------------------------------------ the owner
    def test_the_owners_dashboard(self):
        p = self.page
        self.sign_in_owner()
        # A file chosen in the page's own file picker is uploaded and listed.
        src = os.path.join(tempfile.mkdtemp(prefix="dd-up-"), "hello.txt")
        with open(src, "w") as f:
            f.write("hello from the browser")
        p.goto(self.base + "/#/files/")
        p.set_input_files("#pick", src)
        p.wait_for_selector("text=hello.txt")
        self.assertEqual(self.download("/hello.txt"), ("hello.txt", b"hello from the browser"))
        # A video clicked: it plays in the cinema, streamed in parts (a range of it asked for), and Escape closes it.
        self.upload_text("/clip.mp4", "not really a video " * 1000)
        p.goto(self.base + "/#/files/")
        with p.expect_request(lambda r: "/api/file" in r.url and "clip.mp4" in r.url):
            p.click(".row >> text=clip.mp4")
        p.wait_for_selector(".cinema video")
        p.wait_for_selector(".cinema >> text=This browser can't play this video")       # (it isn't one: offered as a download)
        p.keyboard.press("Escape")
        p.wait_for_selector(".cinema", state="detached")
        # The menu without Gallery and FindX, also after loading the page again; they are in the Tools menu instead.
        p.goto(self.base + "/#/settings")
        p.wait_for_selector(".nav-picks")
        p.evaluate("() => { setNavOff('gallery', true); setNavOff('findx', true); }")
        p.reload()
        p.wait_for_selector("#nav a[data-nav=files]")
        self.assertFalse(p.is_visible("#nav a[data-nav=gallery]"))
        self.assertFalse(p.is_visible("#nav a[data-nav=findx]"))
        self.assertTrue(p.is_visible("#nav a[data-nav=files]"))
        p.click("#apps")
        p.wait_for_selector("text=Left out of the menu")
        self.assertTrue(p.is_visible(".ctx >> text=Gallery"))
        p.keyboard.press("Escape")
        # A newer DDrive: said at the top, with what is new.
        up = self.web.updates
        up.latest, up.notes = "99.0.0", "## 99.0.0 (2099-01-01)\n\n- **Something new.**"
        p.wait_for_selector("#bars >> text=DDrive 99.0.0 is out")
        p.click("#bars >> text=What's new")
        p.wait_for_selector("dialog[open] >> text=Something new.")
        p.keyboard.press("Escape")
        # The drive goes away (an update restarting it): the page says so instead of failing, and stops saying
        # so when it is back. (Its answers are cut off in the browser: a stopped server's open connections linger.)
        p.route("**/api/status*", lambda route: route.abort())
        p.wait_for_selector("#bars >> text=DDrive isn't answering", timeout=20000)
        p.unroute("**/api/status*")
        p.wait_for_selector("#bars >> text=DDrive isn't answering", state="detached", timeout=20000)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ an end-to-end encrypted account
    def test_an_end_to_end_account_and_its_password_locks(self):
        p = self.page
        p.clock.install()                        # (the page's own time, moved on below: locks close after 15 minutes)
        phrase = self.sign_up_e2e()
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Private"})["status"], 200)
        self.upload_text("/Private/diary.txt", "Dear diary")
        self.upload_text("/note.txt", "in the open")
        # Opened in the page and handed to the browser as a download.
        self.assertEqual(self.download("/note.txt"), ("note.txt", b"in the open"))
        # Locked: gone from the list, and back with its password only.
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Private", "password": "blue whale"})["status"], 200)
        self.assertEqual(self.names(), ["Private", "note.txt"])                 # (open in this page until it is locked again)
        self.api("POST", "/api/locks/lock")
        self.assertEqual(self.names(), ["note.txt"])
        wrong = self.api("POST", "/api/locks/unlock", {"password": "wrong"})
        self.assertEqual(wrong["status"], 403)
        right = self.api("POST", "/api/locks/unlock", {"password": "blue whale"})
        self.assertEqual((right["status"], right["json"]["opened"]), (200, 1))
        self.assertEqual(self.download("/Private/diary.txt"), ("diary.txt", b"Dear diary"))
        # 15 minutes without using it: locked again by itself.
        p.clock.fast_forward("16:00")
        p.wait_for_function("() => true")
        self.assertEqual(self.names(), ["note.txt"])
        # A forgotten password: what was locked so that the account's words open it too, they open.
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Taxes"})["status"], 200)
        other = words.key_to_phrase(bytes(32))
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Taxes", "password": "red fox", "words": other})["status"], 400)
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Taxes", "password": "red fox", "words": phrase})["status"], 200)
        self.api("POST", "/api/locks/lock")
        self.assertNotIn("Taxes", self.names())
        self.assertEqual(self.api("POST", "/api/locks/recover", {"words": other})["status"], 403)
        got = self.api("POST", "/api/locks/recover", {"words": phrase})
        self.assertEqual((got["status"], got["json"]["opened"]), (200, 1))          # (Private wasn't locked so: it stays shut)
        self.assertEqual(self.names(), ["Taxes", "note.txt"])
        self.api("POST", "/api/locks/lock")
        # The lock taken off: in the clear again.
        self.api("POST", "/api/locks/unlock", {"password": "blue whale"})
        self.assertEqual(self.api("POST", "/api/locks/remove", {"path": "/Private", "password": "blue whale"})["status"], 200)
        self.api("POST", "/api/locks/lock")
        self.assertEqual(self.names(), ["Private", "note.txt"])
        # The dialogs: the lock one offers the words, the unlock one the way in with them.
        p.goto(self.base + "/#/files/")
        p.wait_for_selector("text=note.txt")
        p.evaluate("() => lockDialog({ path: '/note.txt', name: 'note.txt', dir: false })")
        p.wait_for_selector("dialog[open] >> text=Let my 24 words open it too")
        p.keyboard.press("Escape")
        p.evaluate("() => unlockDialog()")
        p.wait_for_selector("dialog[open] >> text=Forgot the password?")
        self.assertEqual(self.errors, [])

    def test_a_big_video_of_an_end_to_end_link_streams(self):
        p = self.page
        self.sign_up_e2e()
        film = "".join("%08d" % i for i in range(1_300_000)).encode()          # 10.4 MB: more than one answer holds
        self.upload_text("/film.mp4", film.decode())
        link = self.api("POST", "/api/share", {"path": "/film.mp4", "download": True})
        self.assertEqual(link["status"], 200, link)
        viewer = self.ctx.new_page()
        viewer.set_default_timeout(30000)
        viewer.goto(self.base + link["json"]["path"])
        src = viewer.wait_for_selector("video").get_attribute("src")
        self.assertIn("/play/", src)                          # streamed, not fetched whole first
        got = viewer.evaluate("""async (src) => {
            const r = await fetch(src, { headers: { Range: 'bytes=9000000-9000099' } });
            return { status: r.status, range: r.headers.get('Content-Range'), body: await r.text() };
        }""", src)
        self.assertEqual((got["status"], got["range"]), (206, f"bytes 9000000-9000099/{len(film)}"))
        self.assertEqual(got["body"].encode(), film[9000000:9000100])
        whole = viewer.evaluate("async (src) => { const r = await fetch(src); return [r.status, (await r.arrayBuffer()).byteLength]; }", src)
        self.assertEqual(whole[0], 206)                        # (an answer of a few pieces: the player asks for more)
        self.assertLess(whole[1], len(film))
        # The account's settings list its computers logged in to Discord (none here: no computer does Messages).
        p.goto(self.base + "/#/settings")
        p.wait_for_selector("text=Computers logged in to Discord")
        p.wait_for_selector("text=None of your computers does Messages yet.")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
