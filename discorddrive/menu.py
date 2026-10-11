"""Interactive menu shown by run.bat / run.sh when they are started without arguments.

Every choice opens its own page: the screen is cleared, the XO banner and a breadcrumb are
shown, the action runs, and Enter goes back to the page it came from.
"""

import os
import shutil
import subprocess
import sys
import threading
import time

from .config import Config, default_data_dir, format_size
from .runtime import FROZEN, command
from .ui import ANSI, BOLD, GRAY, GREEN, RED, RESET, YELLOW, banner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "discorddrive", "scripts")
WINDOWS = sys.platform == "win32"
RESTART = 75          # exit code asking run.bat / run.sh to reopen the menu (after an update)
MAIN = "Main menu"


# ------------------------------------------------------------------ output
def clear():
    sys.stdout.flush()
    os.system("cls" if WINDOWS else "clear")


def page(*crumbs):
    """Start a new page: clear the screen, banner, breadcrumb and title."""
    clear()
    banner()
    if len(crumbs) > 1:
        print(f"  {GRAY}{'  >  '.join(crumbs[:-1])}{RESET}")
    print(f"  {RED}==>{RESET} {BOLD}{crumbs[-1]}{RESET}")
    print()


def step(msg):
    print(f"  {RED}==>{RESET} {msg}")


def info(msg):
    print(f"    {GRAY}{msg}{RESET}")


def done(msg):
    print(f"    {GREEN}{msg}{RESET}")


def warn(msg):
    print(f"    {YELLOW}{msg}{RESET}")


def ask(prompt, default=""):
    try:
        value = input(f"  {prompt}" + (f" {GRAY}[{default}]{RESET}" if default else "") + ": ").strip()
    except EOFError:
        raise SystemExit(0)
    return value or default


def confirm(prompt, default=True):
    answer = ask(f"{prompt} {GRAY}{'[Y/n]' if default else '[y/N]'}{RESET}").lower()
    return default if not answer else answer.startswith("y")


def back(to=MAIN):
    try:
        input(f"\n  {GRAY}Press Enter to go back to {to}...{RESET}")
    except EOFError:
        raise SystemExit(0)


def choose(title, options, live=None):
    """Print a list of (key, label) options (None = spacer) and ask for one.
    Returns the chosen key, None for back/exit, or '?' for anything else.
    live: (render_function, lines_between_it_and_the_title) keeps a status line up to date."""
    print(f"  {BOLD}{title}{RESET}")
    for key, label in options:
        if key is None:
            print()
            continue
        print(f"   {RED}{key:>2}{RESET}  {label}")
    print(f"   {RED} 0{RESET}  {'Exit' if title == MAIN else 'Back'}")
    print()
    updater = None
    if live:
        render, offset = live
        # from the prompt line up to the live line: blank, exit, options, title, the lines
        # between title and live line (offset), and one more to land on the live line itself
        updater = LiveLine(render, 1 + 1 + len(options) + 1 + offset + 1).start()
    try:
        pick = ask("Choose an option")
    finally:
        if updater:
            updater.stop()
    if pick in ("0", "q", "exit", "back"):
        return None
    return pick if any(pick == k for k, _ in options if k) else "?"


class LiveLine:
    """Rewrites one line further up the screen every few seconds while the menu waits for input.

    Uses ANSI 'save cursor / move up / clear line / restore cursor', so whatever the user is
    typing at the prompt stays where it is. Only active in a real terminal with ANSI support.
    """

    def __init__(self, render, lines_up, interval=2.0):
        self.render = render
        self.lines_up = lines_up
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._last = None

    def start(self):
        if not ANSI or self.lines_up <= 0:
            return self
        size = shutil.get_terminal_size((0, 0))
        # Wrapped lines (narrow window) or a scrolled-off status line (short window) would make
        # the cursor land on the wrong line, so only update when everything fits.
        if size.columns < 90 or size.lines <= self.lines_up + 1:
            return self
        self._thread = threading.Thread(target=self._run, name="LiveStatus", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                text = self.render()
            except Exception:
                continue
            if self._stop.is_set() or text == self._last:
                continue
            self._last = text
            sys.stdout.write(f"\0337\033[{self.lines_up}A\r\033[2K  {text}\0338")
            sys.stdout.flush()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


# ------------------------------------------------------------- the drive
def cli(*args, stdin_text=None):
    """Run a DiscordDrive command in a fresh process; its output appears on the current page,
    in the same theme (DISCORDDRIVE_EMBEDDED tells it the page already has a banner and title)."""
    cmd = command(*args)
    env = dict(os.environ, DISCORDDRIVE_EMBEDDED="1")
    cwd = None if FROZEN else ROOT
    sys.stdout.flush()   # the page header must appear before the command's own output
    if stdin_text is None:
        return subprocess.call(cmd, cwd=cwd, env=env)
    return subprocess.run(cmd, cwd=cwd, env=env, input=stdin_text, text=True).returncode


def is_mounted(cfg):
    from .cli import is_mounted as _m
    return _m(cfg.mount_point)


def _winfsp():
    from .fuse_loader import find_winfsp_dll
    return find_winfsp_dll()


def drive_line(cfg):
    """'Drive Z: RUNNING  |  4,372 files, 141.4 GB in Discord  |  3 uploading'"""
    running = is_mounted(cfg)
    state = f"{GREEN}RUNNING{RESET}" if running else f"{GRAY}not running{RESET}"
    parts = [f"Drive {BOLD}{cfg.mount_point}{RESET} {state}"]
    db = os.path.join(cfg.resolved_data_dir, "index.db")
    if os.path.exists(db):
        try:
            from .index import Index
            from .cli import _human
            idx = Index(db)
            try:
                st = idx.stats(max_age=0)
            finally:
                idx.close()
            parts.append(f"{st['files']:,} files, {_human(st['bytes'])} in Discord")
            if st["unsynced"]:
                parts.append(f"{YELLOW}{st['unsynced']:,} uploading{RESET}")
        except Exception:
            pass
    return "  |  ".join(parts)


def quick_status(cfg):
    """Print the status lines at the top of the main menu. Returns how many lines were printed
    after the drive line (so the live updater can find it again)."""
    if not cfg.is_configured():
        print(f"  {YELLOW}Not set up yet: choose {BOLD}5{RESET}{YELLOW} (Setup) first.{RESET}")
    print(f"  {drive_line(cfg)}")
    after = []
    if not WINDOWS:
        from .fuse_loader import find_linux_fuse_lib
        if not find_linux_fuse_lib():
            after.append(f"{YELLOW}FUSE is not installed: choose 9 (Tools), then 'Install requirements'.{RESET}")
    elif not _winfsp():
        after.append(f"{YELLOW}WinFsp is not installed: get it from https://winfsp.dev/rel/ "
                     f"(or: winget install -e --id WinFsp.WinFsp --source winget){RESET}")
    for line in after:
        print(f"  {line}")
    print()
    return len(after) + 1


def _log_path():
    return os.path.join(default_data_dir(), "discorddrive.log")


def _new_log_errors(since):
    try:
        with open(_log_path(), "r", encoding="utf-8", errors="replace") as f:
            f.seek(since)
            text = f.read()
    except OSError:
        return []
    from .cli import _log_message
    return [_log_message(l) for l in text.splitlines() if "[ERROR]" in l]


def start_drive():
    cfg = Config.load()
    if not cfg.is_configured():
        warn("DiscordDrive is not set up yet. Choose 5 (Setup) first.")
        return
    if is_mounted(cfg):
        done(f"Already running on {cfg.mount_point}.")
        return
    step(f"Starting DiscordDrive on {cfg.mount_point}")
    if not WINDOWS or FROZEN:
        cli("mount", "-b")
        return
    # Windows: start hidden (no console window stays open) through the VBScript helper.
    try:
        since = os.path.getsize(_log_path())
    except OSError:
        since = 0
    subprocess.Popen(["wscript.exe", os.path.join(SCRIPTS, "windows", "mount_hidden.vbs")])
    for _ in range(120):
        time.sleep(0.5)
        if is_mounted(cfg):
            done(f"Running. Open File Explorer: your drive is {cfg.mount_point}")
            return
        errors = _new_log_errors(since)
        if errors:
            print(f"    {RED}DiscordDrive stopped while starting:{RESET}")
            for e in dict.fromkeys(errors):
                print(f"      {e}")
            return
    warn("Still starting (the first start can take a minute). Check again with Status.")


def stop_drive():
    step("Stopping DiscordDrive")
    cli("stop")


def update():
    if FROZEN or not os.path.isdir(os.path.join(ROOT, ".git")):
        warn("This copy wasn't downloaded with git, so it can't update itself.")
        info("Download the latest version from https://github.com/xo907/discord-drive/releases")
        if not WINDOWS:
            info("(With the .deb package: download the new one and install it with sudo apt install ./<file>.deb)")
        return False
    if not shutil.which("git"):
        warn("git is not installed." + (" Install it: winget install -e --id Git.Git --source winget"
                                         if WINDOWS else " Install it: sudo apt install -y git"))
        return False
    cfg = Config.load()
    was_running = is_mounted(cfg)
    if was_running:
        stop_drive()
        print()
    step("Downloading the latest version")
    result = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, text=True)
    for line in (result.stdout + result.stderr).strip().splitlines():
        info(line)
    ok = result.returncode == 0
    if not ok:
        warn("Could not update (see above). Your current version is unchanged.")
    if was_running:
        print()
        cli("menu-start")   # a fresh process, so the new code is used
    return ok


# --------------------------------------------------------- settings page
SETTINGS = [
    ("cache_mode", "Read cache", "disk = keep opened files on disk (faster), memory = RAM only (nothing on disk)"),
    ("cache_max_bytes", "Read cache size (disk mode)", "e.g. 5G"),
    ("min_free_disk_bytes", "Always keep this much disk free", "e.g. 2G (protects your other programs)"),
    ("staging_max_bytes", "Upload queue limit", "e.g. 2G (pause copying while this much waits to upload)"),
    ("max_file_size", "Max file size", "e.g. 100M, 2G, or 0 for no limit"),
    ("version_retention_days", "Keep old versions for (days)", "0 = forever"),
    ("mount_point", "Drive letter / mount folder", "e.g. Z: on Windows, /mnt/discord on Linux"),
    ("discord_pace", "Discord pace", "gentle (default, never rushes Discord), balanced, or fast"),
    ("deletes_per_minute", "Deletes per minute (0 = from the pace)", "removing old pieces in the background; gentle = 15"),
    ("uploads_per_minute", "Uploads per minute per bot (0 = from the pace)", "gentle = 30 (about 4.5 MB/s per bot)"),
    ("parity_enabled", "Self-healing (spare pieces)", "true = lost pieces can be rebuilt (recommended), false = off"),
    ("snapshot_interval_hours", "Automatic snapshot every (hours)", "e.g. 24, or 0 to turn them off"),
    ("snapshot_keep_days", "Keep snapshots for (days)", "e.g. 14"),
    ("web_hosts", "Web dashboard domain names (reverse proxy)",
     "e.g. drive.example.com (several: separate with commas, '-' clears). Needed behind Nginx Proxy Manager"),
    ("web_public_url", "Address used in share links", "e.g. https://drive.example.com (empty: from the domain names)"),
    ("web_lan", "Web dashboard on your network (phone)", "true = phones and computers at home can open it, "
                                                         "false = only this computer"),
]


def _shown(key, value):
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "none"
    if value == "":
        return "not set"
    if isinstance(value, int) and not isinstance(value, bool) and (key.endswith("_bytes") or key == "max_file_size"):
        return "no limit" if value == 0 and key == "max_file_size" else format_size(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def settings_menu():
    while True:
        page(MAIN, "Settings")
        cfg = Config.load()
        options = [(str(i), f"{label}: {BOLD}{_shown(k, getattr(cfg, k))}{RESET}")
                   for i, (k, label, _) in enumerate(SETTINGS, 1)]
        from .cli import autostart_enabled
        auto = autostart_enabled()
        n = len(SETTINGS)
        bots, hidden = 1 + len(cfg.extra_bot_tokens or []), cfg.hidden_folders or []
        k_bots, k_hidden, k_auto, k_all, k_web = (str(n + i) for i in range(1, 6))
        options += [(None, None),
                    (k_web, f"Web dashboard sign-in: {BOLD}{cfg.web_user or 'not set'}{RESET}"),
                    (k_bots, f"Bots for uploading (more = faster): {BOLD}{bots}{RESET}"),
                    (k_hidden, f"Folders hidden on this device: {BOLD}{len(hidden) or 'none'}{RESET}"),
                    (k_auto, f"Start automatically when this computer starts: {BOLD}{'ON' if auto else 'OFF'}{RESET}"),
                    (k_all, "Show every setting")]
        pick = choose("Choose a setting to change", options)
        if pick is None:
            return
        if pick == k_auto:
            page(MAIN, "Settings", "Start automatically")
            cli("autostart", "off" if auto else "on")
            back("Settings")
            continue
        if pick == k_all:
            page(MAIN, "Settings", "Every setting")
            cli("config")
            back("Settings")
            continue
        if pick == k_bots:
            bots_page()
            continue
        if pick == k_web:
            page(MAIN, "Settings", "Web dashboard sign-in")
            cli("web-password")
            back("Settings")
            continue
        if pick == k_hidden:
            hidden_page()
            continue
        if not pick.isdigit() or not 1 <= int(pick) <= len(SETTINGS):
            continue
        key, label, hint = SETTINGS[int(pick) - 1]
        page(MAIN, "Settings", label)
        print(f"  Current value: {BOLD}{_shown(key, getattr(cfg, key))}{RESET}")
        info(hint)
        print()
        value = ask("New value (Enter = keep)", "")
        if value:
            print()
            if cli("config", key, value) == 0 and is_mounted(cfg):
                print()
                if confirm("Restart the drive now so this takes effect?"):
                    print()
                    stop_drive()
                    start_drive()
        back("Settings")


def bots_page():
    page(MAIN, "Settings", "Bots for uploading")
    info("Discord limits how fast one bot may post. Each extra bot (a new application in the")
    info("Developer Portal, invited to your server like the first one) adds upload speed.")
    print()
    cli("bots")
    print()
    pick = choose("What would you like to do?", [("1", "Add a bot"), ("2", "Remove a bot")])
    if pick == "1":
        print()
        cli("bots", "add")
    elif pick == "2":
        n = ask("Number of the extra bot to remove")
        if n:
            print()
            cli("bots", "remove", n)
    else:
        return
    back("Settings")


def hidden_page():
    page(MAIN, "Settings", "Hidden folders")
    info("A hidden folder isn't shown on this device. It stays on the drive and on your other devices,")
    info("e.g. hide /Movies on the Raspberry Pi.")
    print()
    cfg = Config.load()
    for h in cfg.hidden_folders or []:
        print(f"    {h}")
    if not cfg.hidden_folders:
        info("Nothing is hidden here.")
    print()
    pick = choose("What would you like to do?", [("1", "Hide a folder"), ("2", "Show a hidden folder again")])
    if pick in ("1", "2"):
        p = ask("Folder (e.g. Z:\\Movies or /Movies)")
        if p:
            print()
            cli("hide" if pick == "1" else "unhide", p)
    else:
        return
    back("Settings")


# ----------------------------------------------------------- files page
def files_menu():
    title = "Files: versions, recovery and offline"
    while True:
        page(MAIN, title)
        pick = choose("What would you like to do?", [
            ("1", "List the old versions of a file"),
            ("2", "Bring back an old version of a file"),
            ("3", "List deleted files that can be recovered"),
            ("4", "Recover a deleted file"),
            ("11", "Delete a deleted file for good"),
            (None, None),
            ("5", "Make a file or folder available offline"),
            ("6", "Free up space used by a file or folder"),
            ("7", "Clear the whole read cache"),
            (None, None),
            ("8", "Snapshots of the whole drive"),
            ("9", "Restore a folder from a snapshot"),
            ("10", "Take a snapshot now"),
            ("16", "Delete a snapshot for good"),
            (None, None),
            ("12", "Lock a folder or file with a password"),
            ("13", "Unlock: show locked folders on this device's drive"),
            ("14", "Lock them again now"),
            ("15", "Remove a lock"),
        ])
        if pick is None:
            return
        if pick == "1":
            page(MAIN, title, "Old versions of a file")
            p = ask("File (e.g. Z:\\Docs\\a.docx or /Docs/a.docx)")
            if p:
                print()
                cli("versions", p)
        elif pick == "2":
            page(MAIN, title, "Bring back an old version")
            p = ask("File")
            if p:
                print()
                cli("versions", p)
                print()
                n = ask("Version number to bring back (Enter = cancel)")
                if n:
                    target = ask("Save as a new file instead? Enter a path, or leave empty to replace", "")
                    print()
                    cli("restore-version", p, n, *(["--as", target] if target else []))
        elif pick == "3":
            page(MAIN, title, "Deleted files")
            folder = ask("Only inside this folder (Enter = everywhere)", "/")
            print()
            cli("deleted", folder)
        elif pick == "4":
            page(MAIN, title, "Recover a deleted file")
            p = ask("Original path of the deleted file")
            if p:
                print()
                cli("undelete", p)
        elif pick == "5":
            page(MAIN, title, "Make available offline")
            p = ask("File or folder to keep on this computer")
            if p:
                print()
                cli("offline", p)
        elif pick == "6":
            page(MAIN, title, "Free up space")
            p = ask("File or folder (Enter = everything that isn't kept offline)", "")
            print()
            if p:
                cli("free-space", p)
            else:
                cli("free-space", "--all")
        elif pick == "7":
            page(MAIN, title, "Clear the read cache")
            cli("clear-cache")
        elif pick == "11":
            page(MAIN, title, "Delete for good")
            p = ask("Original path of the deleted file")
            if p:
                print()
                cli("purge", p)
        elif pick == "8":
            page(MAIN, title, "Snapshots")
            cli("snapshots")
        elif pick == "9":
            page(MAIN, title, "Restore from a snapshot")
            cli("snapshots")
            print()
            n = ask("Snapshot number (Enter = cancel)")
            if n:
                folder = ask("Folder to bring back (Enter = everything)", "/")
                info("It is copied into a new folder named after the snapshot; nothing you have now changes.")
                print()
                cli("snapshot-restore", n, folder)
        elif pick == "10":
            page(MAIN, title, "Take a snapshot")
            cli("snapshots", "create")
        elif pick == "16":
            page(MAIN, title, "Delete a snapshot")
            cli("snapshots")
            print()
            info("Deleting a snapshot can't be undone. Your files as they are now are not touched; what only that")
            info("snapshot still kept (older contents of changed and deleted files) is removed from Discord.")
            print()
            n = ask("Snapshot number to delete, or 'all' (Enter = cancel)")
            if n and confirm(f"Delete {'every snapshot' if n == 'all' else 'snapshot ' + n} for good?", default=False):
                print()
                cli("snapshots", "delete", n)
        elif pick == "12":
            page(MAIN, title, "Lock with a password")
            info("A locked folder (or file) is gone from the drive and the dashboard, on every device, until it is")
            info("unlocked with its password. Keep the password somewhere: it can't be looked up.")
            print()
            p = ask("Folder or file to lock (e.g. Z:\\Private or /Private)")
            if p:
                print()
                cli("lock", p)
        elif pick == "13":
            page(MAIN, title, "Unlock")
            info("Type a lock's password: everything locked with it shows up on this device's drive for a while.")
            print()
            cli("unlock")
        elif pick == "14":
            page(MAIN, title, "Lock again")
            cli("relock")
        elif pick == "15":
            page(MAIN, title, "Remove a lock")
            p = ask("Folder or file whose lock to remove")
            if p:
                forgot = not confirm("Do you know its password?")
                print()
                cli("unprotect", p, *(["--forgot"] if forgot else []))
        else:
            continue
        back(title)


# ----------------------------------------------------------- sync page
def _ask_mode(current=None):
    from .sync import MODES
    keys = list(MODES)
    for i, (key, (label, _, text)) in enumerate(MODES.items(), 1):
        print(f"   {RED}{i}{RESET}  {BOLD}{label}{RESET}{GRAY} ({key}){RESET}")
        info(f"   {text}")
    default = str(keys.index(current) + 1) if current in keys else "1"
    pick = ask("What should it do?", default)
    return keys[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(keys) else None


def _ask_when(current=None):
    """Arguments for 'sync add' / 'sync edit' describing when it runs, or None."""
    print()
    print(f"   {RED}1{RESET}  As soon as something changes (live)")
    print(f"   {RED}2{RESET}  Every few minutes or hours")
    print(f"   {RED}3{RESET}  Once a day")
    print(f"   {RED}4{RESET}  Only when I start it")
    default = {"live": "1", "interval": "2", "daily": "3", "manual": "4"}.get(current, "1")
    pick = ask("When?", default)
    if pick == "2":
        every = ask("Every how many minutes? (e.g. 30, or 120 for two hours)", "60")
        return ["--when", "interval", "--every", every] if every.isdigit() else None
    if pick == "3":
        return ["--when", "daily", "--at", ask("At what time? (24-hour clock)", "03:00")]
    return {"1": ["--when", "live"], "4": ["--when", "manual"]}.get(pick)


def _pick_sync_job():
    from .sync import load_jobs
    jobs = load_jobs()
    if not jobs:
        warn("No folders are synced yet; add one first.")
        return None, None
    cli("sync", "list")
    print()
    n = ask("Which folder? (its number above)", "1" if len(jobs) == 1 else "")
    if not n.isdigit() or not 1 <= int(n) <= len(jobs):
        return None, None
    return n, jobs[int(n) - 1]


def sync_menu():
    title = "Sync and back up folders"
    while True:
        page(MAIN, title)
        info("Folders on this computer kept in step with folders on the drive, e.g. your Downloads")
        info("backed up to the drive by themselves. The web dashboard has the same under Sync.")
        print()
        cli("sync", "list")
        print()
        pick = choose("What would you like to do?", [
            ("1", "Add a folder"),
            ("2", "Sync a folder now"),
            ("3", "Details and recent runs of a folder"),
            ("4", "Pause or resume a folder"),
            ("5", "Change a folder (what it does, when, files to skip)"),
            ("6", "Stop syncing a folder"),
            (None, None),
            ("7", "What each mode does"),
        ])
        if pick is None:
            return
        cfg = Config.load()
        if pick == "1":
            page(MAIN, title, "Add a folder")
            from .sync import real_folder
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            downloads = real_folder(downloads) or downloads
            local = ask("Folder on this computer", downloads if os.path.isdir(downloads) else "")
            if not local:
                continue
            name = os.path.basename(local.rstrip("\\/")) or "Backup"
            sample = f"{cfg.mount_point}\\{name}" if WINDOWS else f"/{name}"
            remote = ask("Folder on the drive (created if needed)", sample)
            print()
            mode = _ask_mode()
            when = _ask_when() if mode else None
            if not mode or not when:
                warn("Nothing added.")
            else:
                skip = ask("Files or folders to skip, e.g. *.iso, Temp/ (Enter = none)", "")
                print()
                cli("sync", "add", local, remote, "--mode", mode, *when, *(["--exclude", skip] if skip else []))
        elif pick == "2":
            page(MAIN, title, "Sync now")
            n, job = _pick_sync_job()
            if job:
                print()
                cli("sync", "run", n)
        elif pick == "3":
            page(MAIN, title, "Details")
            n, job = _pick_sync_job()
            if job:
                print()
                cli("sync", "status", n)
        elif pick == "4":
            page(MAIN, title, "Pause or resume")
            n, job = _pick_sync_job()
            if job:
                print()
                cli("sync", "pause" if job["enabled"] else "resume", n)
        elif pick == "5":
            page(MAIN, title, "Change a folder")
            n, job = _pick_sync_job()
            if job:
                print()
                mode = _ask_mode(job["mode"])
                when = _ask_when(job["trigger"]) if mode else None
                if not mode or not when:
                    warn("Nothing changed.")
                else:
                    skip = ask("Files or folders to skip (- = none)", ", ".join(job["exclude"]) or "-")
                    print()
                    cli("sync", "edit", n, "--mode", mode, *when, "--exclude", "" if skip == "-" else skip)
        elif pick == "6":
            page(MAIN, title, "Stop syncing")
            n, job = _pick_sync_job()
            if job and confirm(f"Stop syncing {job['name']}? (no files are deleted)"):
                print()
                cli("sync", "remove", n)
        elif pick == "7":
            page(MAIN, title, "Modes")
            cli("sync", "modes")
        else:
            continue
        back(title)


# ----------------------------------------------------------- tools page
def tools_menu():
    title = "Tools and troubleshooting"
    while True:
        page(MAIN, title)
        options = [
            ("1", "Check that files can be downloaded and decrypted (verify)"),
            ("2", "Show recent problems"),
            ("3", "Show the log"),
            ("15", "Clear the log"),
            (None, None),
            ("4", "Show my recovery phrase (the 24 words that are this drive's key)"),
            ("5", "Add an older encryption key"),
            ("6", "Rebuild this device's file list from Discord"),
            ("7", "Save an index checkpoint now"),
            ("10", "Request the key from another device"),
            ("11", "Cancel stuck uploads"),
            (None, None),
            ("12", "Self-healing status"),
            ("13", "Check every piece on Discord now and repair what's missing"),
            ("14", "Add spare pieces to files uploaded before self-healing"),
            (None, None),
        ]
        if WINDOWS:
            options += [("8", "Add 'offline / free up space' to the Explorer right-click menu"),
                        ("9", "Remove it from the Explorer right-click menu")]
        else:
            options += [("8", "Install / repair requirements (FUSE, cryptography; needs root)"),
                        ("9", "How to start the drive automatically at boot")]
        pick = choose("What would you like to do?", options)
        if pick is None:
            return
        labels = {k: v for k, v in options if k}
        if pick not in labels:
            continue
        page(MAIN, title, labels[pick].split(" (")[0])
        if pick == "1":
            p = ask("File or folder to check (Enter = everything; downloads it all once)", "") or "/"
            print()
            if cli("verify", p) != 0:
                print()
                if confirm("Remove the files that can never be read (lost key / missing) from the drive?",
                           default=False):
                    print()
                    cli("verify", p, "--remove")
        elif pick == "2":
            cli("log", "--errors", "-n", "25")
        elif pick == "3":
            cli("log", "-n", "40")
        elif pick == "15":
            if confirm("Empty the log file and the dashboard's Log tab?", default=False):
                print()
                cli("log", "--clear")
        elif pick == "4":
            warn("These 24 words are the key to your drive: anyone who has them can read your files.")
            info("Make sure nobody is looking at your screen.")
            print()
            if confirm("Show them?", default=False):
                cli("recovery-phrase")
        elif pick == "5":
            info("Type the recovery phrase (24 words) of the earlier key, or paste the key in hex.")
            print()
            value = ask("Key or file")
            if value:
                print()
                if os.path.isfile(value):
                    cli("add-old-key", "--from-config", value)
                else:
                    cli("add-old-key", "-", stdin_text=value + "\n")
        elif pick == "6":
            info("Use this when this device is missing files that your other devices show.")
            print()
            cfg = Config.load()
            if is_mounted(cfg):
                if not confirm("The drive has to stop for this. Stop it now?"):
                    back(title)
                    continue
                print()
                stop_drive()
                print()
            cli("restore")
            print()
            if confirm("Start the drive again?"):
                print()
                start_drive()
        elif pick == "7":
            cli("backup")
        elif pick == "12":
            cli("health")
        elif pick == "13":
            info("Asks Discord about every piece once; with many files this takes a while.")
            print()
            cli("health", "--check")
        elif pick == "14":
            info("A running drive does this by itself in the background; this does it all now.")
            print()
            cli("health", "--protect")
        elif pick == "11":
            cfg = Config.load()
            running = is_mounted(cfg)
            if running:
                if not confirm("The drive has to stop for this. Stop it now?"):
                    back(title)
                    continue
                print()
                stop_drive()
                print()
            cli("cancel-uploads")
            if running:
                print()
                start_drive()
        elif pick == "10":
            info("For a device that is missing the key, or has the wrong one. Another device approves it")
            info("from its menu: 'Approve a new device'.")
            print()
            cli("request-key")
        elif pick == "8":
            if WINDOWS:
                cli("context-menu", "install")
            else:
                subprocess.call(["bash", os.path.join(SCRIPTS, "linux", "install.sh")], cwd=ROOT)
        elif pick == "9":
            if WINDOWS:
                cli("context-menu", "uninstall")
            else:
                service = os.path.join(SCRIPTS, "linux", "discorddrive.service")
                print("  Run these once, as the user that runs the drive:\n")
                print(f"    {BOLD}mkdir -p ~/.config/systemd/user{RESET}")
                print(f"    {BOLD}sed \"s|%h/DiscordDrive|{ROOT}|g\" {service} > "
                      f"~/.config/systemd/user/discorddrive.service{RESET}")
                print(f"    {BOLD}systemctl --user daemon-reload && systemctl --user enable --now discorddrive{RESET}")
                print(f"    {BOLD}sudo loginctl enable-linger \"$USER\"{RESET}")
        back(title)


# ------------------------------------------------------------- copy help
def copy_help(cfg):
    mp = cfg.mount_point
    print(f"  Copy files onto {BOLD}{mp}{RESET} like any other drive: they upload in the background.")
    print()
    if WINDOWS:
        info("Drag and drop in File Explorer works. For very large folders, robocopy can resume:")
        print()
        print(f"    {BOLD}robocopy \"C:\\Source\\Folder\" \"{mp}\\Folder\" /E /Z{RESET}")
    else:
        info("For big copies, rsync skips what is already there and can be re-run any time.")
        info("This keeps retrying by itself after errors, and stops when everything is copied:")
        print()
        print(f"    {BOLD}nohup sh -c 'until rsync -rt --info=progress2 /source/folder/ "
              f"{mp}/folder/; do sleep 60; done; echo COPY COMPLETE' > ~/copy.log 2>&1 &{RESET}")
        print()
        info("Watch it with:  tail -f ~/copy.log")
    print()
    info("Status (option 3) shows uploads in progress. Files appear on your other devices as soon")
    info("as their upload finishes.")


# ------------------------------------------------------------- main menu
MAIN_OPTIONS = [
    ("1", "Start the drive"),
    ("2", "Stop the drive"),
    ("3", "Status (everything in detail)"),
    ("4", "Update DiscordDrive"),
    (None, None),
    ("5", "Setup (bot token, channel, recovery phrase)"),
    ("6", "Settings (cache, limits, drive letter...)"),
    ("7", "Files: old versions, deleted files, offline"),
    ("8", "Copy files onto the drive (help)"),
    ("9", "Tools and troubleshooting"),
    ("10", "Approve a new device (send it the key)"),
    ("11", "Web dashboard (browse, preview and share files; works on your phone)"),
    ("12", "Sync and back up folders (e.g. Downloads -> the drive)"),
    ("13", "Lite: use a drive that runs on another computer (nothing is set up here)"),
]
PAGE_TITLES = {"1": "Start the drive", "2": "Stop the drive", "3": "Status", "4": "Update DiscordDrive",
               "5": "Setup", "8": "Copy files onto the drive", "10": "Approve a new device",
               "11": "Web dashboard"}


def main():
    while True:
        clear()
        banner()
        cfg = Config.load()
        offset = quick_status(cfg)
        pick = choose(MAIN, MAIN_OPTIONS, live=(lambda: drive_line(cfg), offset))
        if pick is None:
            clear()
            return 0
        if pick == "6":
            settings_menu()
            continue
        if pick == "7":
            files_menu()
            continue
        if pick == "9":
            tools_menu()
            continue
        if pick == "12":
            sync_menu()
            continue
        if pick == "13":
            clear()
            banner()
            cli("lite", "menu")
            continue
        if pick not in PAGE_TITLES:
            continue
        page(MAIN, PAGE_TITLES[pick])
        if pick == "1":
            start_drive()
        elif pick == "2":
            stop_drive()
        elif pick == "3":
            cli("status")
        elif pick == "4":
            if update():
                print()
                done("Updated. Reopening the menu with the new version...")
                time.sleep(1.5)
                return RESTART
        elif pick == "5":
            if cli("setup") == 0 and not is_mounted(Config.load()):
                print()
                if confirm("Start the drive now?"):
                    print()
                    start_drive()
        elif pick == "8":
            copy_help(cfg)
        elif pick == "10":
            info("Use this when you set up DiscordDrive on another device and it asks for the key.")
            print()
            cli("approve-keys")
        elif pick == "11":
            if not (cfg.web_user and cfg.web_password):
                info("The dashboard asks for a user name and password. Choose them now:")
                print()
                if cli("web-password") != 0:
                    back()
                    continue
                print()
            if not is_mounted(cfg):
                warn("The dashboard runs inside the drive: start the drive first (option 1).")
            else:
                cli("web", "--open")
        back()


def menu_start():
    """'start' / 'menu-start': start the drive the same way the menu does."""
    start_drive()
    return 0
