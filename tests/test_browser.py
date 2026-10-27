"""The dashboard in a real browser (Playwright, headless Chromium): what only a browser does, end to end,
against a whole drive with its dashboard on a free local port. Signing in (the owner, and an end-to-end
encrypted account made in the page), uploading and downloading (an end-to-end account's file is opened in
the page), the menu's pages left out, the update notice and the page waiting for a drive that is away, and
password locks of an end-to-end account: locking, unlocking, a wrong password, locking again by itself
after 15 minutes without use (a fake clock), and taking the lock off.

Needs `pip install playwright` and a browser: `python -m playwright install chromium` (CI does both), or an
installed Edge or Chrome (DD_BROWSER=msedge / chrome). Without one these tests are skipped."""

import json
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
    def test_the_recorder_key_is_made_in_the_settings(self):
        self.sign_in_owner()
        p = self.page
        p.goto(self.base + "/#/settings/advanced")
        p.click("#recorder-settings button:has-text('Make a key')")
        p.wait_for_selector("text=New key made")
        key = p.input_value("#recorder-settings input[readonly]")
        self.assertTrue(key)
        self.assertEqual(self.web.cfg.recorder_key, key)
        r = self.ctx.request.post(self.base + "/api/rec/open", headers={"Authorization": "Bearer " + key},
                                  data={"name": "call.webm", "mime": "audio/webm"})
        self.assertEqual(r.status, 200, r.text())
        r = self.ctx.request.post(self.base + "/api/rec/open", headers={"Authorization": "Bearer wrong"}, data={"name": "x.webm"})
        self.assertEqual(r.status, 401)
        p.click("#recorder-settings button:has-text('Turn off')")
        p.wait_for_selector("text=Live upload is off")
        self.assertEqual(p.input_value("#recorder-settings input[readonly]"), "")
        self.assertEqual(self.errors, [])

    def test_every_computer_is_updated_from_the_settings(self):
        self.web.computers.heard("", {"device": "pi1", "name": "pi-xo", "version": "3.0.0", "kind": "git", "result": ""})
        self.web.computers.heard("", {"device": "pc2", "name": "laptop", "version": "3.0.0", "kind": "exe"})
        self.web.computers.heard("", {"device": "pi3", "name": "pi-two", "version": "3.0.0", "disk": "3.0.1", "kind": "git",
                                      "phase": "Stopping the drive to start it again with DDrive 3.0.1…"})
        self.sign_in_owner()
        p = self.page
        p.goto(self.base + "/#/settings/computers")
        p.wait_for_selector("text=Update all computers >> nth=0")
        self.assertIn("pi-xo", p.inner_text("#computers-settings"))
        self.assertIn("updates by download only", p.inner_text("#computers-settings"))
        self.assertIn("Stopping the drive", p.inner_text("#computers-settings .live-phase"))      # what it is doing, live
        if os.environ.get("DD_SHOT"):
            p.locator("#computers-settings").screenshot(path=os.environ["DD_SHOT"])
        self.web.computers.heard("", {"device": "pi3", "name": "pi-two", "version": "3.0.1", "kind": "git"})
        p.wait_for_function("() => !document.querySelector('#computers-settings .live-phase')", timeout=10000)   # (it looks again every 3 s)
        p.click("#computers-settings button:has-text('Update all computers')")
        p.click("#dialog-ok")
        p.wait_for_selector("text=Told every computer to update")
        self.assertGreater(self.web.computers.at, 0)
        self.assertEqual(self.errors, [])

    def test_an_accounts_devices_and_who_writes_into_discord(self):
        self.sign_up_e2e()
        p = self.page
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Discord"})["status"], 200)
        aid = next(iter(self.web.accounts._recs))
        self.web.account_devices.heard(aid, {}, "192.168.1.50", "POST", "/api/vault/log")     # an old computer somewhere
        p.goto(self.base + "/#/settings/devices")
        p.wait_for_selector("text=Devices using this account")
        p.wait_for_selector("text=Discord (not locked)")
        box = p.locator("h2:has-text('Devices using this account') >> xpath=..")
        text = box.inner_text()
        self.assertIn("This browser", text)
        self.assertIn("192.168.1.50", text)
        self.assertIn("Made by the dashboard", text)
        if os.environ.get("DD_SHOT"):
            box.screenshot(path=os.environ["DD_SHOT"])
        p.once("dialog", lambda d: d.accept())
        box.locator(".set-row:has-text('192.168.1.50') button:has-text('Sign out')").click()
        p.click("#dialog-ok")
        p.wait_for_selector("text=Let back in")
        self.assertIn("u:192.168.1.50", self.web.account_devices._data[aid]["blocked"])
        self.assertEqual(self.errors, [])

    def test_a_locked_folder_and_the_one_that_came_back_are_merged(self):
        p = self.page
        self.sign_up_e2e()
        for folder in ("/Discord", "/Discord/Live"):
            self.assertEqual(self.api("POST", "/api/mkdir", {"path": folder})["status"], 200)
        self.upload_text("/Discord/Live/a.jsonl", "A")
        self.upload_text("/Discord/note.txt", "locked one")
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Discord", "password": "blue whale"})["status"], 200)
        self.api("POST", "/api/locks/lock")
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Discord"})["status"], 423)      # (the page doesn't make it next to the lock)
        p.evaluate("async () => { const id = await E2E.vault.mkdir('', 'Discord'); await E2E.vault.mkdir(id, 'Live'); }")   # (an older computer does)
        self.upload_text("/Discord/Live/b.jsonl", "B")
        self.upload_text("/Discord/note.txt", "the one that came back")
        self.api("POST", "/api/locks/unlock", {"password": "blue whale"})
        self.assertEqual(self.names(), ["Discord", "Discord (locked)"])
        p.goto(self.base + "/#/files/")
        p.wait_for_selector("text=Discord (locked)")
        for path in ("/Discord", "/Discord (locked)"):
            p.locator(f'.row[data-path="{path}"] .row-cb').click()
        p.locator('.row[data-path="/Discord"]').click(button="right")
        p.click("text=Merge the 2 folders…")
        p.wait_for_selector("dialog[open] >> text=What moves into a locked folder is locked with it")
        self.assertTrue(p.locator("dialog[open] input[type=radio]").nth(1).is_checked())       # the locked one is kept
        p.click("dialog[open] button:has-text('Merge')")
        p.wait_for_selector("text=Merged into Discord (locked)")
        self.assertEqual(self.names(), ["Discord"])
        self.assertEqual(self.names("/Discord"), ["Live", "note (2).txt", "note.txt"])
        self.assertEqual(self.names("/Discord/Live"), ["a.jsonl", "b.jsonl"])
        self.assertEqual(self.download("/Discord/Live/b.jsonl"), ("b.jsonl", b"B"))
        self.api("POST", "/api/locks/lock")
        self.assertEqual(self.names(), [])                                       # all of it locked, the one that came back too
        self.assertEqual(self.errors, [])

    def test_the_channels_picked_for_the_logger_are_all_kept(self):
        """Ticking channels one after another, quicker than the answers come back (from the account's computer,
        through the server, in any order): every tick is saved, and the last one saved is what is on screen."""
        self.sign_in_owner()
        got = self.page.evaluate("""async () => {
            const saved = [];
            window.api = async (path, body) => {
                if (path === "/api/chat/logger/rules") {
                    const sent = JSON.parse(JSON.stringify(body));
                    saved.push(sent);
                    await new Promise((r) => setTimeout(r, [300, 50, 200, 20][saved.length % 4]));     // (answers out of order)
                    return { rules: sent };
                }
                return { messages: [] };
            };
            chat = { connected: true, logger: { status: "connected", rules: { scope: "custom", selection: { dms: {}, guilds: {} }, keywords: [],
                     captureMine: true, captureOthers: true, allDms: false, downloadAttachments: false, attachmentsOnly: false } } };
            chatDir = { guilds: [{ id: "g1", name: "Server" }], dms: [] };
            chatChannels.set("g1", ["c1", "c2", "c3", "c4"].map((id) => ({ id, name: id })));
            chatOpen.add("g1");
            const body = document.createElement("div");
            document.body.append(body);
            drawLive(body);
            for (const id of ["c1", "c2", "c3", "c4"]) {
                body.querySelector(`.ch-pick-row.sub [aria-label="${id}"]`).click();
                await new Promise((r) => setTimeout(r, 30));
            }
            await new Promise((r) => setTimeout(r, 1500));
            clearInterval(chatTimer);
            const last = saved[saved.length - 1].selection.guilds.g1;
            return { last: Object.keys(last || {}).sort(), shown: Object.keys(chat.logger.rules.selection.guilds.g1 || {}).sort(),
                     ticked: [...body.querySelectorAll('.ch-pick-row.sub [aria-checked="true"]')].map((b) => b.getAttribute("aria-label")).sort() };
        }""")
        self.assertEqual(got["ticked"], ["c1", "c2", "c3", "c4"])
        self.assertEqual(got["last"], ["c1", "c2", "c3", "c4"])          # what the computer keeps
        self.assertEqual(got["shown"], ["c1", "c2", "c3", "c4"])
        self.assertEqual(self.errors, [])

    def test_sorting_the_files_and_folder_colours(self):
        self.sign_in_owner()
        p = self.page
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Alpha"})["status"], 200)
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/beta"})["status"], 200)
        self.upload_text("/small.txt", "x")
        self.upload_text("/big.txt", "x" * 5000)
        self.upload_text("/file10.txt", "xx")
        self.upload_text("/file9.txt", "xxx")
        p.goto(self.base + "/#/files/")
        p.wait_for_selector("text=file10.txt")
        order = lambda: p.eval_on_selector_all(".list .row[data-path] .label", "els => els.map((e) => e.textContent)")
        self.assertEqual(order(), ["Alpha", "beta", "big.txt", "file9.txt", "file10.txt", "small.txt"])     # (folders first; 9 before 10)
        p.click(".row.head .sort-col.size")                                # biggest first
        p.wait_for_function("() => document.querySelector('.row.head .sort-col.size[aria-sort=descending]')")
        self.assertEqual(order()[2:], ["big.txt", "file9.txt", "file10.txt", "small.txt"])
        p.click(".row.head .sort-col.size")                                # smallest first
        p.wait_for_function("() => document.querySelector('.row.head .sort-col.size[aria-sort=ascending]')")
        self.assertEqual(order()[2:], ["small.txt", "file10.txt", "file9.txt", "big.txt"])
        p.reload()                                                         # kept in this browser
        p.wait_for_selector("text=file10.txt")
        self.assertEqual(order()[2:], ["small.txt", "file10.txt", "file9.txt", "big.txt"])
        p.click("button[aria-label=Sort]")                                 # (the grid and phones have no header: the menu)
        p.click(".ctx >> text=Name")
        p.wait_for_function("() => document.querySelector('.row.head .sort-col[aria-sort=ascending]')")
        self.assertEqual(order()[2:], ["big.txt", "file9.txt", "file10.txt", "small.txt"])
        # A folder's colour, from its menu.
        p.locator('.row[data-path="/Alpha"]').click(button="right")
        p.click("text=Colour…")
        p.click("dialog[open] .color-pick[aria-label=Blue]")
        p.wait_for_selector('.row[data-path="/Alpha"] .ico.folder.fc-blue')
        self.assertEqual(self.api("GET", "/api/list?path=/")["json"]["items"][0].get("color"), "blue")
        if os.environ.get("DD_SHOT"):
            p.locator('.row[data-path="/Alpha"]').click(button="right")
            p.click("text=Colour…")
            p.locator("dialog[open]").screenshot(path=os.environ["DD_SHOT"].replace(".png", "-dialog.png"))
            p.keyboard.press("Escape")
            p.locator(".list").screenshot(path=os.environ["DD_SHOT"])
        self.assertEqual(self.errors, [])

    def test_an_end_to_end_accounts_folders_have_colours(self):
        self.sign_up_e2e()
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Photos"})["status"], 200)
        self.assertEqual(self.api("POST", "/api/color", {"path": "/Photos", "color": "green"})["status"], 200)
        self.assertEqual(self.api("GET", "/api/list?path=/")["json"]["items"][0]["color"], "green")
        self.assertEqual(self.api("POST", "/api/color", {"path": "/Photos", "color": "plaid"})["status"], 400)
        self.assertEqual(self.api("POST", "/api/color", {"path": "/Photos", "color": ""})["status"], 200)
        self.assertNotIn("color", self.api("GET", "/api/list?path=/")["json"]["items"][0])

    def _a_folder_on_this_computer(self):
        top = os.path.join(self.tmp, "pick", "Project")
        for rel, text in (("readme.txt", "top"), ("src/main.py", "code"), ("src/lib/util.py", "util"), ("docs/a/b/deep.txt", "deep")):
            os.makedirs(os.path.dirname(os.path.join(top, rel)), exist_ok=True)
            with open(os.path.join(top, rel), "w") as f:
                f.write(text)
        return top

    def _upload_a_folder_keeps_its_folders(self):
        p = self.page
        p.goto(self.base + "/#/files/")
        p.wait_for_selector("text=Nothing here yet")
        p.set_input_files("#pick-dir", self._a_folder_on_this_computer())
        p.wait_for_selector('.row[data-path="/Project"]')
        p.wait_for_function("() => [...document.querySelectorAll('.toast, #toast')].some((t) => /4 files added/.test(t.textContent))")
        self.assertEqual(self.names(), ["Project"])
        self.assertEqual(self.names("/Project"), ["docs", "readme.txt", "src"])
        self.assertEqual(self.names("/Project/src"), ["lib", "main.py"])
        self.assertEqual(self.names("/Project/src/lib"), ["util.py"])
        self.assertEqual(self.names("/Project/docs/a/b"), ["deep.txt"])
        self.assertEqual(self.errors, [])

    def test_uploading_a_folder_keeps_its_folders(self):
        self.sign_in_owner()
        self._upload_a_folder_keeps_its_folders()

    def test_a_folder_picked_without_its_folders_is_said_and_kept_together(self):
        """A phone's picker hands over files, not a folder: that is said, and they go into a folder of their own."""
        self.sign_in_owner()
        p = self.page
        p.goto(self.base + "/#/files/")
        p.wait_for_selector("text=Nothing here yet")
        top = self._a_folder_on_this_computer()
        p.evaluate("document.querySelector('#pick-dir').removeAttribute('webkitdirectory')")     # (as a phone's picker: files only)
        p.set_input_files("#pick-dir", [os.path.join(top, "readme.txt"), os.path.join(top, "src", "main.py")])
        p.wait_for_selector("#dialog[open] >> text=The folders inside weren't kept")
        p.fill("#dialog-input", "From my phone")
        p.click("#dialog-ok")
        p.wait_for_selector('.row[data-path="/From my phone"]')
        p.wait_for_function("() => [...document.querySelectorAll('.toast, #toast')].some((t) => /2 files added/.test(t.textContent))")
        self.assertEqual(self.names(), ["From my phone"])
        self.assertEqual(self.names("/From my phone"), ["main.py", "readme.txt"])
        self.assertEqual(self.errors, [])

    def test_uploading_a_folder_keeps_its_folders_in_an_end_to_end_account(self):
        self.sign_up_e2e()
        self._upload_a_folder_keeps_its_folders()

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

    def test_notes_and_bookmarks_with_a_password_in_an_end_to_end_account(self):
        p = self.page
        self.sign_up_e2e()
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Notes"})["status"], 200)
        self.upload_text("/Notes/plain.md", "shopping")
        self.upload_text("/Notes/secret.md", "the safe code")
        # One note locked: marked while open here, gone once locked again, and not made anew in the clear
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Notes/secret.md", "password": "blue whale"})["status"], 200)
        notes = self.api("GET", "/api/notes")["json"]
        self.assertEqual({n["name"]: n["locked"] for n in notes["items"]}, {"plain.md": False, "secret.md": True})
        self.api("POST", "/api/locks/lock")
        self.assertEqual([n["name"] for n in self.api("GET", "/api/notes")["json"]["items"]], ["plain.md"])
        again = self.api("POST", "/api/upload?path=%2FNotes%2Fsecret.md&save=later", "overwritten")
        self.assertEqual(again["status"], 423, again)
        p.evaluate("() => refreshStatus()")                # (the dialogs do this; here it was locked through the API)
        p.goto(self.base + "/#/notes")
        p.wait_for_selector("text=Unlock locked notes")
        # The bookmarks locked as a whole: the page says so, and nothing new is saved next to them in the clear
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Bookmarks"})["status"], 200)
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Bookmarks", "password": "red fox"})["status"], 200)
        self.assertTrue(self.api("GET", "/api/bookmarks")["json"]["lock"])
        self.api("POST", "/api/locks/lock")
        self.assertTrue(self.api("GET", "/api/bookmarks")["json"]["locked"])
        self.assertEqual(self.api("POST", "/api/bookmarks/add", {"url": "example.com"})["status"], 423)
        self.assertNotIn("Bookmarks", self.names())
        p.goto(self.base + "/#/bookmarks")
        p.wait_for_selector("text=Bookmarks are locked")
        # The whole notes folder locked: the Notes page shows the way in instead of an empty list
        self.api("POST", "/api/locks/unlock", {"password": "blue whale"})
        self.assertEqual(self.api("POST", "/api/locks/remove", {"path": "/Notes/secret.md", "password": "blue whale"})["status"], 200)
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Notes", "password": "green owl"})["status"], 200)
        self.api("POST", "/api/locks/lock")
        self.assertTrue(self.api("GET", "/api/notes")["json"]["locked"])
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Notes"})["status"], 423)
        p.goto(self.base + "/#/files/")
        p.goto(self.base + "/#/notes")
        p.wait_for_selector("text=Notes are locked")
        self.assertEqual(self.errors, [])

    def test_private_bookmarks_in_an_end_to_end_account(self):
        p = self.page
        self.sign_up_e2e()
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Bookmarks"})["status"], 200)
        marks = [{"id": "aaa", "url": "https://secret.example/", "title": "Secret", "site": "secret.example", "image": "/Bookmarks/images/aaa.png"},
                 {"id": "bbb", "url": "https://plain.example/", "title": "Plain", "site": "plain.example"}]
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Bookmarks/images"})["status"], 200)
        self.upload_text("/Bookmarks/images/aaa.png", "picture")
        self.upload_text("/Bookmarks/Bookmarks.json", json.dumps({"v": 1, "items": marks[1:]}))
        self.upload_text("/Bookmarks/Bookmarks.json", json.dumps({"v": 1, "items": marks}))     # (an earlier version without it, one with it)
        self.assertEqual(self.api("GET", "/api/bookmarks")["json"]["private"], "none")
        self.assertEqual(self.api("POST", "/api/mkdir", {"path": "/Bookmarks/Private"})["status"], 200)
        self.assertEqual(self.api("POST", "/api/locks/add", {"path": "/Bookmarks/Private", "password": "blue whale"})["status"], 200)
        got = self.api("POST", "/api/bookmarks/private", {"ids": ["aaa"], "on": True})
        self.assertEqual((got["status"], got["json"]["moved"]), (200, 1), got)
        items = {b["id"]: b for b in self.api("GET", "/api/bookmarks")["json"]["items"]}
        self.assertEqual((items["aaa"]["private"], items["aaa"]["image"]), (True, "/Bookmarks/Private/images/aaa.png"))
        # the plain list forgot its earlier versions: they had the private one in them
        self.assertEqual(self.api("GET", "/api/versions?path=%2FBookmarks%2FBookmarks.json")["json"]["versions"], [])
        self.assertEqual(self.names("/Bookmarks"), ["Bookmarks.json", "Private", "images"])
        self.assertEqual(self.names("/Bookmarks/images"), [])
        # saved privately straight away (no device reads the page here: no preview)
        added = self.api("POST", "/api/bookmarks/add", {"url": "https://other.example/", "private": True})
        self.assertEqual((added["status"], added["json"]["bookmark"]["private"]), (200, True))
        p.evaluate("() => refreshStatus()")
        p.goto(self.base + "/#/bookmarks")
        p.wait_for_selector(".bm-head >> text=Private")
        self.assertEqual(p.locator(".bm-card .tag:has-text('Private')").count(), 2)
        self.assertTrue(p.locator("#bm-private").is_visible())
        # locked: gone, name and all
        self.api("POST", "/api/locks/lock")
        got = self.api("GET", "/api/bookmarks")["json"]
        self.assertEqual((got["private"], [b["id"] for b in got["items"]]), ("locked", ["bbb"]))
        self.assertEqual(self.names("/Bookmarks"), ["Bookmarks.json", "images"])
        self.assertEqual(self.api("POST", "/api/bookmarks/add", {"url": "https://x.example/", "private": True})["status"], 423)
        p.goto(self.base + "/#/files/")
        p.goto(self.base + "/#/bookmarks")
        p.wait_for_selector("text=Plain")
        self.assertEqual(p.locator("text=Secret").count(), 0)
        self.assertFalse(p.locator("#bm-private").is_visible())
        self.assertEqual(self.errors, [])

    def test_settings_are_in_tabs(self):
        p = self.page
        self.sign_up_e2e()
        with self.web.accounts._lock:                      # (an admin: its account has the recorder, and the drive's settings)
            for rec in self.web.accounts._recs.values():
                rec["admin"] = True
        p.goto(self.base + "/#/settings")
        p.reload()
        tabs = lambda: [t.strip() for t in p.locator(".set-tab").all_inner_texts()]
        p.wait_for_selector(".set-tab.on")
        self.assertEqual(tabs(), ["Account", "Preferences", "Discord", "Devices", "Move or delete"])
        for tab, wait in [("account", "Manage the drive"), ("preferences", "In the menu"), ("discord", "Call recorder for Discord"),
                          ("devices", "Devices using this account"), ("move", "Delete my account")]:
            p.goto(self.base + "/#/settings/" + tab)
            p.wait_for_selector(f".set-body >> text={wait}")
            p.wait_for_timeout(300)
            self.assertNotIn("false", p.inner_text(".set-body"))
            self.assertEqual(p.inner_text(".set-tab.on").strip(), p.inner_text(".set-title").strip())
        # managing the drive
        p.goto(self.base + "/?server#/settings")
        p.wait_for_selector(".set-tab.on")
        self.assertEqual(tabs(), ["General", "Access and sign-in", "Storage", "Discord", "Accounts", "Sign-up", "Computers"])
        for tab, wait in [("accounts", "Make an admin…"), ("signup", "Governing law"), ("storage", "Spare pieces (per 10 pieces)")]:
            p.goto(self.base + "/?server#/settings/" + tab)
            p.wait_for_selector(f".set-body >> text={wait}")
            p.wait_for_timeout(300)
            self.assertNotIn("false", p.inner_text(".set-body"))
        self.assertEqual(p.locator(".set-body >> text=Governing law").count(), 0)          # (each tab has its own)
        # a long hint shows its first sentence; the rest is one click away
        before = p.locator(".set-body .more-hint").count()
        self.assertGreater(before, 0)
        p.locator(".set-body .more-hint").first.click()
        self.assertEqual(p.locator(".set-body .more-hint").count(), before - 1)
        # a phone: the tabs are a bar along the top
        p.set_viewport_size({"width": 390, "height": 800})
        box = p.locator(".set-tabs").bounding_box()
        self.assertLess(box["height"], 80)
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
        p.goto(self.base + "/#/settings/discord")
        p.wait_for_selector("text=Computers logged in to Discord")
        p.wait_for_selector("text=None of your computers does Messages yet.")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
