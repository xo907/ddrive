<h1 align="center">
    <img src="docs/images/logo-128.png" height="72" width="72" alt=""><br>
    DDrive
</h1>
<p align="center">
  <b>Your own private cloud drive.</b><br>
  Encrypted on your computer, stored in a Discord channel, synced across your devices,<br>
  with a dashboard that rivals the big cloud drives.
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9 or newer">
  <img src="https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20Raspberry%20Pi-informational" alt="Windows, Linux, Raspberry Pi">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT license"></a>
</p>
<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#how-it-compares">How it compares</a> ·
  <a href="#accounts">Accounts</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center"><img src="docs/images/screens/home.png" alt="The DDrive dashboard: what is on the drive, starred files with thumbnails, recent files and activity from several devices" width="100%"></p>

DDrive turns a private Discord channel into a drive of your own: a drive letter on Windows
(`Z:\`), a folder on Linux, and a web app in your browser and on your phone. Everything is encrypted
on your computer before it leaves, so Discord only ever stores random-looking pieces. There is no
subscription, no storage plan and no account with anyone but Discord, and the whole thing is one small
open-source program in plain Python that runs on your own PC, server or Raspberry Pi. If you want to,
you can let other people in: each gets [an account](#accounts) with storage of their own, encrypted
with their own key.

<table>
<tr>
<td width="33%" valign="top">

**Private by design**

AES-256-GCM encryption happens on your machine. File names, folders and contents never reach Discord
in readable form. The key is yours alone: 24 words you write down.

</td>
<td width="33%" valign="top">

**A real drive**

Open, save and stream from any program. Videos seek without downloading whole files. Files take no
local space until you open them, and every device stays in sync within seconds.

</td>
<td width="33%" valign="top">

**Looks after itself**

Spare pieces rebuild anything Discord loses. Earlier versions, deleted files and whole-drive
snapshots can be brought back. A background check finds problems before you do.

</td>
</tr>
</table>

> **Heads-up:** DDrive is an independent project. It is not affiliated with, endorsed by or
> sponsored by Discord Inc.; "Discord" is their trademark and is used here only to say where the data
> is stored. Using Discord as general-purpose file storage may violate Discord's Terms of Service, and
> Discord can delete messages, attachments, or your bot/account at any time. Treat DDrive as an
> experiment, **not** as your only copy of anything important. See [Legal](#legal).

## What you get

| | |
|---|---|
| **Drive** | A drive letter or mount folder that every app can use · streaming and seeking · files on demand · offline pinning · live sync between Windows, Linux and Raspberry Pi |
| **Dashboard** | Home overview · files with a grid view · **photo and video gallery** with thumbnails and a full-screen viewer · notes · contacts · **bookmarks with link previews** · **Discord message logger and archiver** · starred and recent · find anything with Ctrl+K · installable as an app on your phone |
| **Sharing** | Links with an expiry, a password and view-only mode · direct links that embed in Discord · **file requests**: let others upload into a folder they can't see |
| **Backup and sync** | Keep folders on your computer backed up, mirrored or two-way synced, live or on a schedule · save a file straight from a web link · WebDAV for other apps |
| **Safety net** | Self-healing with spare pieces (Reed-Solomon) · versions of every file · undelete · daily snapshots of the whole drive · an activity list of what changed, when, on which device |
| **Security** | Zero-knowledge encryption · sign-in by 24-word phrase or **passkey** (fingerprint, face, security key), no passwords · password-locked folders that vanish until unlocked · secrets in the config encrypted for your machine |
| **For others** | **Accounts**: other people get storage of their own on your dashboard, each encrypted with its own 24 words · sign-up closed, by invite or public |
| **Insight** | What takes the space, by kind and by folder · the largest files · a duplicate finder · health and repair status |
| **Efficiency** | Compression · identical pieces stored once · parallel uploads with extra bots · gentle pacing that stays inside Discord's limits |

All of it is described further down: [Web dashboard](#web-dashboard) ·
[Sync and back up folders](#sync-and-back-up-folders) · [Password-locked folders](#password-locked-folders) ·
[Self-healing](#self-healing) · [Snapshots](#snapshots) · [Accounts](#accounts) · [The config file](#the-config-file).

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/images/screens/gallery.png" alt="Gallery: photos and videos by month, with thumbnails"><br><sub><b>Gallery.</b> Every photo and video, newest first. Thumbnails are made once and kept encrypted.</sub></td>
<td width="50%"><img src="docs/images/screens/bookmarks.png" alt="Bookmarks: cards with banners, pinned ones at the top, tags"><br><sub><b>Bookmarks.</b> Paste a link; the title, icon and banner are saved on your drive. Pin, tag and filter.</sub></td>
</tr>
<tr>
<td><img src="docs/images/screens/files.png" alt="Files shown as a grid of thumbnails"><br><sub><b>Files.</b> List or grid, drag and drop, right-click for everything, folder sizes at a glance.</sub></td>
<td><img src="docs/images/screens/storage.png" alt="Storage: space by kind of file, biggest folders, identical files"><br><sub><b>Storage.</b> Space by kind and folder, the largest files, and identical copies to clean up.</sub></td>
</tr>
<tr>
<td><img src="docs/images/screens/palette.png" alt="The find box opened with Ctrl+K, showing matching files"><br><sub><b>Find anything.</b> Ctrl+K searches files, pages and actions from one box.</sub></td>
<td><img src="docs/images/screens/viewer.png" alt="A photo opened in the full-screen viewer"><br><sub><b>Viewer.</b> Full screen, arrow keys for next and previous. Videos stream and seek.</sub></td>
</tr>
</table>

<table>
<tr>
<td width="62%"><img src="docs/images/screens/accounts.png" alt="Settings, Accounts: who can make an account, storage per account, terms, and invite codes"><br><sub><b>Accounts.</b> Let others in: closed, by invite or public, with a limit per account.</sub></td>
<td width="38%"><img src="docs/images/screens/signup.png" alt="The sign-up page showing a new account's 24 words"><br><sub><b>24 words.</b> An account has no name and no password.</sub></td>
</tr>
</table>

<p align="center">
  <img src="docs/images/screens/phone.png" alt="The dashboard on a phone: home" width="270">
  &nbsp;&nbsp;
  <img src="docs/images/screens/phone-gallery.png" alt="The dashboard on a phone: gallery" width="270">
  <br><sub><b>On your phone.</b> The same dashboard, installable to the home screen.</sub>
</p>

<sub>Screenshots show a demo drive with generated pictures and made-up sites and names.</sub>

## How it compares

| | DDrive | Typical cloud drive |
|---|---|---|
| Price | Free and open source | Free for a few GB, then a subscription |
| Storage limit | None of its own (Discord's rules apply) | 2 to 15 GB free |
| Who can read your files | Only you: encrypted before upload, key never leaves your devices | The provider holds the keys |
| File names visible to the provider | No | Yes |
| Versions, undelete, snapshots | Yes | Usually, often on paid plans |
| Sharing links, file requests | Yes | Yes |
| Photo gallery, notes, contacts, bookmarks | Yes | Varies |
| Passkey sign-in | Yes | Varies |
| Repairs data the storage loses | Yes, automatically | Not your concern: the provider guarantees it |
| Guarantee that your data stays | **None.** Discord can remove it; keep another copy | Service agreement |
| Runs on | Your own PC, server or Raspberry Pi | The provider's servers |

DDrive gives you the features and the privacy; it cannot give you a provider's promise. That is
what the heads-up above is about.

<p align="center"><img src="docs/images/overview.svg" alt="Your devices sync through DDrive, which stores encrypted pieces in a private Discord channel" width="100%"></p>

Files you save are staged locally, split into pieces, compressed when that helps, encrypted, and
uploaded as attachments to your channel together with a few spare pieces. Reading downloads only the
pieces that cover what is being read. More in [How it works](#how-it-works).

<p align="center">Made by <b>XO.ST</b> · <a href="https://github.com/xo907/ddrive">github.com/xo907/ddrive</a></p>

---

## Quick start

About 10 minutes. You need a Discord account and a server you own (any empty server works:
in Discord, click **+** in the server list → **Create My Own**).

### Step 1: Create the Discord bot (same for Windows and Linux)

1. Go to <https://discord.com/developers/applications> → **New Application** → type any name → **Create**.
2. On the left click **Bot** → **Reset Token** → **Yes, do it!** → **Copy**.
   Paste the token somewhere for a minute; you need it in Step 2.
3. On the left click **OAuth2** and copy the **Client ID**. Paste it into this link in place of
   `YOUR_CLIENT_ID`, open the link, choose your server, and click **Authorize**:
   ```text
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot&permissions=109568
   ```
4. In your server, create a text channel for storage (for example `#drive`). Only you and the bot
   should be able to see it: in the channel's settings → **Permissions**, make it private and add your bot.
5. In Discord, go to **User Settings → Advanced** and turn on **Developer Mode**. Then right-click
   the channel → **Copy Channel ID**. You need this in Step 2 as well.

> **Prefer an installer?** Each [release](https://github.com/xo907/ddrive/releases) has
> `DDrive.exe` for Windows (no Python or git needed; WinFsp is still required) and a `.deb`
> for Debian, Ubuntu and Raspberry Pi OS (`sudo apt install ./ddrive_*.deb`, then run
> `ddrive`). The steps below install from source, which the menu can update by itself.

### Step 2a: Windows 10 / 11

Open a normal **PowerShell** (Start menu → type `PowerShell` → Enter; **not** "Run as administrator")
and paste:

```powershell
winget install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements; winget install -e --id WinFsp.WinFsp --source winget --accept-package-agreements --accept-source-agreements; winget install -e --id Git.Git --source winget --accept-package-agreements --accept-source-agreements
```

Click **Yes** if Windows asks for permission. "Found an existing package already installed" just means
you already have that program, which is fine. When it is done, **close PowerShell and open a new one**
(so it finds the programs you just installed), then paste:

```powershell
git clone https://github.com/xo907/ddrive.git "$HOME\DDrive"; cd "$HOME\DDrive"; .\run.bat
```

The DDrive menu opens. Choose **5 (Setup)** and answer the questions:

| Question | What to type |
|---|---|
| Discord Bot Token | the token from Step 1 |
| Private Discord Channel ID | the channel ID from Step 1 |
| Mount Point Drive Letter `[Z:]` | press **Enter** (or type another free letter) |
| Enable Zero-Knowledge Encryption? `[Y/n]` | press **Enter** |
| (setup then shows a **recovery phrase**) | **write the 24 words down** and keep them safe: they are the key to your drive |

When setup asks "Start the drive now?", press **Enter**. Open **File Explorer**: your new drive is
**Z:**. Anything you put there is stored in Discord.

From now on, just double-click **`run.bat`** in the `DDrive` folder (your user folder) to get the menu.

### Step 2b: Linux (Debian, Ubuntu, Raspberry Pi OS)

Open a terminal and paste. It works both as a normal user with `sudo` and as `root`, installs what
DDrive needs, and opens the menu:

```bash
S=$(command -v sudo); $S apt update && $S apt install -y git && git clone https://github.com/xo907/ddrive.git ~/DDrive && cd ~/DDrive && bash ddrive/scripts/linux/install.sh && ./run.sh
```

In the menu choose **5 (Setup)**. Paste the bot token and channel ID, type **`/mnt/ddrive`** as the
mount directory and press **Enter** at "Enable encryption?". Setup then shows your **recovery phrase**:
write the 24 words down. Answer **Enter** to "Start the drive now?".

Your files are in **`/mnt/ddrive`**. From now on, run **`~/DDrive/run.sh`** to get the menu.

### Everyday use

Everything is in the menu: **`run.bat`** on Windows (double-click it), **`./run.sh`** on Linux.

<p align="center"><img src="docs/images/menu.svg" alt="The DDrive menu" width="640"></p>

| Menu option | What it does |
|---|---|
| 1 / 2 | Start or stop the drive |
| 3 | Status: what is stored in Discord, what is uploading, cache and disk space |
| 4 | Update DDrive to the latest version (stops and restarts the drive for you) |
| 5 | Setup: bot token, channel, recovery phrase |
| 6 | Settings: memory-only cache, cache size, disk-space limits, max file size, drive letter |
| 7 | Old versions, deleted files, offline files |
| 8 | How to copy lots of files onto the drive |
| 9 | Tools: check files, logs, encryption keys, rebuild the file list, Explorer menu, autostart |
| 10 | Approve a new device: send it the key when it asks |
| 11 | Web dashboard: browse, preview, upload and share files in your browser (also on your phone) |
| 12 | Sync and back up folders: keep e.g. your Downloads backed up to the drive by itself |

Every option is also a direct command, handy for scripts and remote servers:
`run.bat status`, `./run.sh start`, `./run.sh stop`, `./run.sh config max_file_size 2G`, and so on
(see [Usage](#usage)).

<p align="center"><img src="docs/images/status.svg" alt="Example output of the status command" width="720"></p>

Stuck? See [Troubleshooting](#troubleshooting): it covers every problem we have run into on
Windows and Linux.

**Optional: keep nothing on this computer.** By default, files you open are cached on disk so they
open faster next time. To stream them from Discord every time instead (cached in RAM only), choose
**6 (Settings) → Read cache → `memory`** in the menu.

**Using a second computer?** Do Step 2 on it with the **same bot token and channel ID**. Setup
notices that the channel already holds your drive and offers to **request the key from one of your
other devices**:

1. On the new computer, setup shows a verification code (e.g. `4F7-2A1`) and waits.
2. On a computer that already has the drive, open the menu and choose **10 (Approve a new device)**.
   Check that it shows the same code, and accept.
3. The key is sent across, encrypted, and the new computer catches up with all your files and
   folders before it starts.

(Or choose "type the recovery phrase" and enter your 24 words.)

> **Keep your recovery phrase safe.** It is your drive's key written as 24 words: setup shows it for a
> new drive, and `recovery-phrase` shows it again on any device that has the drive (a copy of the
> config file is not a backup of it, see [The config file](#the-config-file)). With every device
> lost and no phrase, the data in Discord cannot be decrypted. Nobody can recover it for you, and
> anyone who has the phrase can read your files.

---

## Built to survive Discord

- **Self-healing.** Every group of 10 pieces is stored with 2 spare pieces (Reed-Solomon, the same
  idea as RAID 6), so any 2 lost pieces of a group can be rebuilt. If Discord deletes a piece, the
  file still opens: the piece is rebuilt on the fly, uploaded again, and every device learns where
  it now lives. A background check confirms every piece is still there (one full pass a week) and
  repairs what isn't, before losses can pile up. Spare pieces are ordinary encrypted attachments in
  your channel and cost about 20% extra space on big files.
- **Compression and de-duplication.** Pieces that shrink (documents, text, logs, many app files)
  are compressed before they are encrypted; lossless, and the encryption is exactly as strong.
  Pieces the drive already stores (a copied file, the same photo in two folders) are not uploaded
  again.
- **Faster uploads.** Several pieces upload at once, and you can add extra bots: Discord limits
  how fast *one* bot may post, so each extra bot adds speed (menu 6 → Bots, or `bots add`).
- **Snapshots.** The whole drive as it was at one moment, taken every 24 hours and kept 14 days.
  Bring back a folder (or everything) as it was on Tuesday, into a new folder so nothing current
  is touched.
- **Selective sync.** Folders marked "available offline" stay downloaded as they change, including
  new files added on other devices. Folders can be hidden on one device only (`hide`), e.g.
  `/Movies` on the Raspberry Pi.
- **Tested.** More than a hundred automated tests run on Windows and Linux for every change, and
  releases come with installers.

> **Keep every device on the same version.** Update all of them (menu option 4) before relying on a
> new feature: locks, stars and other newer records are only understood by versions that know them.

## Features

<p align="center"><img src="docs/images/what-discord-sees.svg" alt="What you see on the drive compared with what Discord stores: only random names and encrypted data" width="100%"></p>

- **Client-side encryption (AES-256-GCM).** Chunks and index backups are encrypted before upload.
  Attachments get random names (`chk_<random>.bin`) and empty message text, so Discord never sees
  file names, folder structure, or contents. It can still see how many chunks there are, their
  sizes, and when they were uploaded.
- **Self-healing.** Spare pieces rebuild what Discord loses; a background check finds losses early.
- **Compression and de-duplication** before encryption; extra bots and parallel uploads for speed.
- **Web dashboard** for your browser and phone (installable as an app): a home page, a photo and video
  gallery, bookmarks with link previews, starred and recent files, an activity list, storage insights,
  in-browser text editing, and Ctrl+K to find anything.
- **Sharing both ways**: expiring, password-protected links to give files out, and **file requests**
  that let others send files into a folder without seeing it.
- **Save from a link**: the drive downloads a web address straight into a folder.
- **Lite**: other computers can use the drive through one that runs it, with nothing stored on them
  ([more](#ddrive-lite)).
- **Discord messages**: archive your own conversations and log new messages as they arrive, including
  ones later edited or deleted; kept on the drive ([more](#discord-messages)).
- **Other apps (WebDAV)**: open the drive from phone file managers, "Map network drive", Finder, rclone.
- **Snapshots** of the whole drive, restorable folder by folder.
- **Password-locked folders**: gone from the drive letter and the dashboard until unlocked
  ([more](#password-locked-folders)).
- **Sync and back up folders** on your computer: backup, mirror, two-way sync, move, or a local copy
  of a drive folder; live, every N minutes, daily or by hand ([more](#sync-and-back-up-folders)).
- **Real drive / mount.** Uses [WinFsp](https://winfsp.dev/) on Windows and libfuse on Linux,
  so any application can use it.
- **Streaming and seeking.** Byte-range reads, read-ahead, and an on-disk LRU chunk cache.
- **Files on demand.** After upload the local copy is removed. Files take no local space until opened.
  With the memory-only cache, opened files are not stored on disk either (see below).
- **Offline pinning.** Keep chosen files or folders cached locally (on Windows, also from the
  Explorer right-click menu).
- **Live sync between devices.** Changes made on one computer appear on the others within a few
  seconds. Edits made on two devices at the same time are merged without losing either one.
- **File versions and undelete.** Replaced and deleted files are kept for 30 days (configurable).
  You can list them, bring back an older version, or recover a deleted file.
- **Crash-safe.** A local SQLite (WAL) index. Unfinished uploads resume on the next start, and
  changes made while offline are sent when the connection comes back.
- **Disaster recovery.** Encrypted index checkpoints are pinned in the channel. A fresh install
  restores the whole folder tree automatically.
- **Back-pressure.** Large copies pause while the upload backlog exceeds `staging_max_bytes` or
  free disk space drops below `min_free_disk_bytes`, so a big `rsync` cannot fill your local disk.

## Requirements

| | Windows 10/11 | Linux (Debian, Ubuntu, Raspberry Pi OS, ...) |
|---|---|---|
| Python | 3.9+ | 3.9+ |
| Filesystem driver | [WinFsp](https://winfsp.dev/rel/) | `libfuse2` (`libfuse2t64` on Debian 13+) + `fuse` |
| Encryption | built in (Windows CNG) | `python3-cryptography` |
| HTTP | `curl` (ships with Windows) | `curl` (or Python's urllib fallback) |

There are no other dependencies. A copy of [fusepy](https://github.com/fusepy/fusepy) is
vendored in `ddrive/_vendor/` (ISC license).

---

## Configuration

The bot only needs *View Channels, Send Messages, Attach Files, Read Message History* and
*Manage Messages* (plus *Pin Messages* where Discord lists it separately); that is what
`permissions=109568` in the invite link grants. No privileged gateway intents are needed.

The config is stored outside the repository:

| | Config (token + key) | Data (index, cache, staging, log) |
|---|---|---|
| Windows | `%APPDATA%\DDrive\config.json` | `%LOCALAPPDATA%\DDrive\` |
| Linux | `~/.config/DDrive/config.json` (mode 600) | `~/.local/share/DDrive/` |

Change settings in the menu (**6 Settings**), with `config <name> <value>` (run `config` alone to
list them), or by editing the file.
Sizes accept units, e.g. `config max_file_size 2G` or `config min_free_disk_bytes 4G`.

Useful limits:

| Setting | Default | What it does |
|---|---|---|
| `max_file_size` | `0` (no limit) | Refuse files larger than this ("File too large"), so one huge file can't tie up the disk and uploads for hours |
| `min_free_disk_bytes` | `2G` | Never take the local disk below this much free space |
| `staging_max_bytes` | `10G` | Pause writing while this much is waiting to upload |
| `cache_mode` | `disk` | `memory` keeps files you open in RAM only |
| `parity_enabled` | `true` | Spare pieces (self-healing). `parity_group` (10) data pieces get `parity_pieces` (2) spare pieces |
| `scrub_days` | `7` | One full check that every piece is still on Discord takes this long (`scrub_enabled` turns it off) |
| `protect_existing` | `true` | Add spare pieces to files uploaded before self-healing (downloads each such file once) |
| `compression` / `dedup` | `true` | Compress pieces that shrink; store identical pieces once |
| `snapshot_interval_hours` | `24` | Automatic snapshot of the whole drive (`0` = off), kept `snapshot_keep_days` (14) |
| `web_enabled` / `web_port` | `true` / `8765` | The web dashboard on `http://127.0.0.1:8765` |
| `web_lan` | `false` | Also open the dashboard to phones and computers on your home network |
| `signup_mode` | `closed` | Who can make an [account](#accounts) on the dashboard: `closed`, `invite` or `public` |
| `account_quota_bytes` / `accounts_max` | 5 GiB / `100` | What one account may store, and how many accounts there can be (`0` = no limit) |

`DDRIVE_CONFIG`, `DDRIVE_TOKEN` and `DDRIVE_CHANNEL` environment variables
override the config file location, token, and channel. See [`docs/config.example.json`](docs/config.example.json)
for every option.


---

## Usage

The menu (`run.bat` / `./run.sh` with nothing after it) covers everything. The same actions are
available as commands: put them after `run.bat` on Windows or `./run.sh` on Linux, e.g.
`run.bat status` or `./run.sh stop`.

```text
start                                Start the drive in the background
stop                                 Stop the drive (uploads continue on the next start)
status                               What is stored, what is uploading, cache and disk space
mount                                Run the drive in this window (Ctrl+C to stop)
setup                                Bot token, channel; shows or asks for the recovery phrase
recovery-phrase [--check]            Show this drive's 24-word recovery phrase (or check the one you wrote down)
config [<name> [<value>]]            Show or change a setting (sizes like 500M, 2G)
offline <path>                       Keep a file/folder on this computer for offline use
free-space [<path>] [--all]          Remove cached data (one path, or everything)
clear-cache                          Remove all cached data that isn't kept offline
versions <file>                      List the older versions kept for a file
restore-version <file> <n> [--as p]  Bring back version n (or save it as a new file p)
deleted [<folder>]                   List deleted files that can still be recovered
undelete <path>                      Recover a deleted file
purge <path> [--all]                 Delete a deleted file (or everything deleted under a folder) for good
verify [<path>]                      Check that files can be downloaded and decrypted
log [-n 30] [--errors] [--clear]     Show the end of the log file, or empty it
approve-keys                         Send the key to a new device that asked for it
request-key                          Ask one of your other devices for the key
export-key                           Show the encryption key, to copy it to another device
add-old-key [--from-config <file>]   Add an earlier key so files encrypted with it stay readable
restore                              Rebuild this device's file list from Discord (drive stopped)
backup                               Save an index checkpoint now (normally automatic)
context-menu install|uninstall       "Make available offline" / "Free up space" in Explorer (Windows)
web [--open]                         Show (and open) the address of the web dashboard
webdav-password                      Set the user name and password other apps use over WebDAV
health [--check] [--protect]         Self-healing status; check every piece now; protect old files now
snapshots [create [label]|delete <n>] List snapshots, take one now, or delete one
snapshot-restore <n> [path] [--to p] [--in-place]
                                     Bring back a folder (or everything) from snapshot n
bots [add [token]|remove <n>]        Extra bots for faster uploads
hide <folder> / unhide <folder>      Don't show a folder on this device (it stays everywhere else)
lock <path> / unprotect <path>       Lock a folder or file with a password (hidden everywhere) / remove the lock
unlock [--minutes N] / relock        Show locked folders on this device's drive for a while / hide them again
sync [list|add|edit|remove|run|stop|pause|resume|status|modes]
                                     Folders kept in sync with the drive (see "Sync and back up folders")
```

Paths can be given as `Z:\Folder\file.mp4`, `/mnt/ddrive/Folder/file.mp4`, or `/Folder/file.mp4`.

### Project layout

```text
run.bat / run.sh       start here: the menu, or a command
ddrive/          the program (Python)
  scripts/windows/     helpers used by run.bat (hidden start, Explorer menu)
  scripts/linux/       install.sh (requirements) and the systemd service
  web/                 the web dashboard (plain HTML, CSS and JavaScript)
docs/                  images and config.example.json
tests/                 tests (python -m unittest discover -s tests), run for every change on GitHub
packaging/             builds DDrive.exe and the .deb (see .github/workflows/release.yml)
```

### Start automatically on Linux (systemd)

The menu shows the exact commands under **9 (Tools) → "How to start the drive automatically at
boot"**. In short:

```bash
mkdir -p ~/.config/systemd/user
sed "s|%h/DDrive|$HOME/DDrive|g" ~/DDrive/ddrive/scripts/linux/ddrive.service > ~/.config/systemd/user/ddrive.service
systemctl --user daemon-reload && systemctl --user enable --now ddrive
sudo loginctl enable-linger "$USER"                 # keep running when you are logged out
```

### Sharing over Samba / with other users (Linux)

Set `"allow_other": true` in the config, or pass `mount --allow-other`. This requires
`user_allow_other` in `/etc/fuse.conf`, which the Linux installer (`install.sh`) enables.

---

## Self-healing

Discord can delete messages or attachments at any time. DDrive plans for that:

- **Spare pieces.** Each group of `parity_group` (10) pieces of a file gets `parity_pieces` (2)
  spare pieces, computed with Reed-Solomon coding. Any 2 pieces of a group can go missing and the
  group can still be rebuilt exactly (every piece has a SHA-256 checksum, so a rebuilt piece is
  verified). Small files (fewer than 4 pieces) get one spare piece. Spare pieces are encrypted and
  named like every other piece, so Discord can't tell them apart.
- **On the fly.** When a piece is missing while you read a file, it is rebuilt from the rest of
  its group and the read simply succeeds. The rebuilt piece is uploaded again, and every device
  is told where it lives now.
- **Background check.** One device (the one with the smallest device ID among those active in
  the last 3 days) asks Discord about every piece, spread over `scrub_days` (a week), and repairs
  anything missing, including lost spare pieces. `health` shows the progress; `health --check`
  checks everything right now.
- **Older files.** Files uploaded before 0.2 have no spare pieces yet. That same device adds them
  in the background, one file at a time, while nothing else is uploading (it downloads each file
  once). `health --protect` does it all now; `config protect_existing false` turns it off.

Space: about 20% extra for big files, one extra piece for small ones. `config parity_pieces 3`
survives 3 losses per group; `config parity_enabled false` turns spare pieces off for new uploads.

## Web dashboard

The drive serves a small web app on `http://127.0.0.1:8765` while it runs: menu **11**, or
`run.bat web --open` / `./run.sh web --open`. It shows your files (search, preview, download,
upload by drag and drop, new folder, rename, delete, earlier versions, "available offline"),
recently deleted files, snapshots, and a health page (protection, checks, repairs, uploads,
devices).

- **Signing in.** With 24 words. There are no user names and no passwords.
  - **The recovery phrase**: your drive's 24 words sign you in as its owner, from the first visit on.
    `recovery-phrase` shows them on the computer that runs the drive (so does Settings → **Sign-in**,
    there only).
  - **Passkeys** (so you don't have to type the words): a fingerprint, face, PIN, phone or security
    key. Add one under Settings → Sign-in; only its public key is stored. A passkey works at the
    address it was made for, and browsers allow passkeys only on https (your domain through a reverse
    proxy) or on `http://localhost:8765` on the computer itself, not on `http://127.0.0.1` or a
    plain-http network address. Add one per address you use.

  After 5 failed tries an address has to wait. The account button (top right) signs this browser
  out. Other people can have accounts of their own: see [Accounts](#accounts).
- **Right-click** (or **long-press** on a phone) any file or folder for the usual actions: preview,
  download (folders as a ZIP), copy a share link, available offline, rename, move, duplicate,
  earlier versions, details, delete. Right-click empty space for new folder and uploads (files or a
  whole folder).
- **Drag and drop** files and folders onto a folder, a part of the path at the top, or "Files" to
  move them, onto "Deleted" to delete them. Ctrl/Cmd-click and Shift-click select several. Files
  dragged in from your computer upload into the folder you drop them on.
- **Notes**: a notes tab. Notes are files in `/Notes` on the drive (encrypted, synced, with earlier
  versions). Typing saves to this computer at once and uploads to Discord when you pause; **Save**
  (Ctrl+S) uploads right away. A note changed on another device updates by itself.
- **Contacts**: an encrypted address book (one vCard file, `/Contacts/Contacts.vcf`, synced and
  versioned). Import vCard (.vcf) or Google / Outlook CSV exports from your phone, iCloud, Google
  or Outlook; create and edit contacts; export them again as .vcf.
- **Gallery**: every photo and video on the drive (or in one folder), newest first, with thumbnails and
  a full-screen viewer. Files can also be shown as a grid with thumbnails. The browser makes a
  thumbnail the first time a picture is shown (or all at once with **Create all thumbnails**, which
  reads each photo once from Discord), and the drive keeps them in `<data dir>/thumbs`, encrypted with
  your key. Formats your browser can't show (e.g. HEIC outside Safari, MKV) keep their icon.
- **Home, Starred, Recent, Activity**: the dashboard opens on an overview. Star files and folders
  (synced to every device); Activity lists what was added, changed, moved and deleted, and on which
  device.
- **Bookmarks**: paste a link and it is saved as a card with the page's title, description, icon and
  banner picture (the same tags link previews use). Tags, notes, a filter, pinned favourites at the top,
  and Ctrl+V anywhere on the page. Kept in `/Bookmarks` on the drive with their pictures: encrypted, synced, versioned, and
  nothing is loaded from the sites when you look at them.
- **Messages**: a logger and an archiver for your own Discord account, saved in `/Discord` on the
  drive ([more](#discord-messages)).
- **Storage**: space by kind, the biggest folders and files, and identical files (keep the oldest and
  delete the copies in one click). Folders show their size in Files.
- **File requests**: right-click a folder → **Request files…**. Whoever has the link can send files into
  it from a simple page, cannot see what is in the folder, and never overwrites anything. Choose how
  long it works, an optional password and the largest file. Shared lists it with the files received.
- **Edit text files** (notes, code, config) in the browser: **Edit**, Ctrl+S saves, the version before
  is kept.
- **Save from a link**: paste a web address and the drive fetches the file itself into the open folder
  (nothing passes through your browser or phone). Only addresses on the internet are fetched.
- **Find anything**: Ctrl+K searches files, pages and actions. Paste a screenshot or copied files
  anywhere to upload them; select several items and **Download** gives one ZIP.
- **Install as an app**: "Add to Home Screen" on a phone, or the install button in the browser's address
  bar, gives the dashboard its own icon and window.
- **Other apps (WebDAV)**: turn on Settings → **Other apps (WebDAV)** and connect file managers, "Map
  network drive" (Windows), "Connect to Server" (Finder), rclone or backup apps to
  `https://your-address/dav/`. Those apps can't type 24 words, so they get a user name and password
  of their own (Settings → Sign-in → **Password for other apps**, or `webdav-password`), which open
  nothing but WebDAV. Use your https address: over plain http the password is sent unencrypted and
  Windows refuses to connect. Password-locked folders are never shown over WebDAV. WebDAV is the
  owner's; accounts don't have it.
- **Sync**: folders on the computer running the drive kept in step with folders on the drive (backup,
  mirror, two-way, move, download), with live progress, Sync now, pause and recent runs. See
  [Sync and back up folders](#sync-and-back-up-folders).
- **Sharing**: choose how long a link works (1 hour to never), an optional password, and whether
  people may download or only view. The page shows photos, video, music, PDFs and text, and whole
  folders can be shared. The **Shared** tab lists your links, their views, and turns them off.
  Each shared file also has a **direct link** (`…/s/<id>/<file name>`): posted on its own in Discord,
  the image, GIF, video or song is shown without the link (needs the dashboard reachable from the
  internet, e.g. through your reverse proxy, and no password on the link).
- **Behind a reverse proxy** (e.g. Nginx Proxy Manager): add your domain under Settings → Domain
  names (or `config web_hosts drive.example.com`); share links then use that domain. Settings is in
  the account menu (top right) and in the DDrive menu.
- **Deleted** files can be selected (or all at once) and restored, or deleted forever, which
  removes them from Discord on every device straight away (`purge <path>` on the command line).
- **Phone.** `config web_lan true` (menu 6 → Web dashboard on your network), restart the drive,
  then open the "On your phone" link from `web` on a phone on the same Wi-Fi. The connection
  inside your home network is not encrypted (plain HTTP), so only do this on a network you trust.
- **Share links** give one file to someone else for 7 days. They work for whoever can reach this
  computer: on your home network with `web_lan`, or from anywhere only if you forward the port
  yourself (not recommended).

**When a part can't be repaired** (too many pieces of one group lost, or a file uploaded before spare
pieces existed), the drive names the files concerned: once in the log, and under **Health → Parts that
can't be repaired** in the dashboard and in `health`. It tells a damaged file from a damaged earlier
version or snapshot copy, and from a lost spare piece (that file still reads fine). Bring a damaged
file back from an earlier version or a snapshot, or put it on the drive again from another copy.

## Accounts

Other people can use your dashboard with storage of their own. Settings → **Accounts** (the owner's
Settings) decides who may make one:

| `signup_mode` | Who can make an account |
| --- | --- |
| `closed` (the default) | Nobody. |
| `invite` | Whoever has an invite code. **Invite somebody** makes one (for one account, several or any number; for a day, a week, a month or until you remove it) and gives a link to send. |
| `public` | Anybody who can open the dashboard, at `/signup`. |

- **An account is 24 words.** The sign-up page makes them, shows them once and asks the person to
  save them. There is no name, no email and no password, and nobody (you included) can reset them:
  without the words the account is gone. Signing in is typing them on the same page you sign in on.
- **Storage of its own, encrypted with its own words.** The 24 words are the account's encryption
  key. Everything it stores goes to your Discord channel encrypted with that key, so Discord, the
  other accounts and your own drive key can't read it. An account never sees your files, and you
  don't see theirs in your drive.
- **What an account has**: files and folders (upload, preview, download, ZIP, move, rename), the
  gallery, stars, notes, bookmarks, contacts, messages (their own Discord account), share links and
  file requests, password-locked folders, earlier versions, deleted files, snapshots, the activity
  list and the storage page. DDrive Lite works with an account's words too.
- **What it doesn't have**: Settings, Sync, Log, Health, Resources, checking and repairing, "available
  offline", WebDAV, passkeys, and anything else that manages the drive or the computer. Those pages
  aren't shown, and the drive refuses the requests behind them.
- **Limits**: *Storage per account* (`account_quota_bytes`, 5 GB unless you change it; earlier
  versions and deleted files count until they are gone) and *Most accounts* (`accounts_max`, 100).
  At most three accounts an hour are made from one address.
- **Managing them**: the list under Settings → Accounts shows each account's id, how much it stores
  and when it last signed in. **Switch off** signs it out and stops its share links without deleting
  anything; **Delete** removes it from this computer and from Discord. An account can delete itself
  under *Your account*.

**Terms and privacy policy.** A dashboard that has accounts needs to say what it does with people's
data. DDrive serves two pages for that, linked from the sign-in and sign-up pages: `/terms` and
`/privacy`. They are written around what the program really does (what is kept about an account, what
goes to Discord and in what form, cookies, the log, how deleting works) and they name you, the person
running it, with what you fill in under Settings → Accounts: who runs this, a contact address, and the
governing law (`operator_name`, `operator_contact`, `legal_region`). Making an account requires
ticking that one agrees to them. They are a starting point written by the program's authors, not
legal advice: read them, and have them checked for where you live before you open sign-up to the
public. What an account does is kept out of your Log page and log file, except error messages.

What this is and isn't: the data is unreadable for Discord and for other accounts. The computer that
runs the drive handles it while it is uploaded and shown, and keeps each account's key (encrypted for
that computer, like the secrets of the config file) so that uploads finish and share links work while
nobody is signed in. So an account trusts whoever runs the dashboard, as with any hosted storage.

Accounts live on the computer that made them, in `accounts/` of the data folder: the list
(`accounts.json`) and each account's file list. A checkpoint of every account's file list is also
kept in the channel, so a lost account folder is rebuilt at the next sign-in. A second device on the
same drive does not serve the first one's accounts.

**If that computer's disk is lost.** The list of accounts says where each account's checkpoint is, so
a copy of the list is kept in the channel as well: one pinned message per computer that serves
accounts, encrypted with your drive's key. It is written within a minute of an account being made,
switched off or deleted, and otherwise at most every ten minutes, replacing the copy before it. It
holds what the list holds except the accounts' keys. To get the accounts back:

1. Set DDrive up again on the rebuilt (or a new) computer with your 24 words, as for any device.
2. Open Settings → Accounts. A computer that has no accounts is shown the saved list under **Bring
   accounts back**, with the name of the computer that saved it and when. Press **Bring back**.
3. Each account works again once it has signed in with its 24 words (they are its key, and the saved
   list doesn't have it). Until then its share links don't work. Accounts that were switched off stay
   switched off, and invites are back too.

Nothing is brought back by itself, and that is on purpose: a second device on the same drive is
offered the same list, and two computers serving the same accounts would each keep their own record
of changes and lose each other's. Only bring the list back when the computer that saved it is gone or
no longer runs DDrive. What an account changed in its last minutes (after the last copy of the list
was saved, ten minutes at most) is not brought back: the record of that was on the lost disk. You
don't have to back up `accounts.json` yourself; a backup of the data folder still saves the accounts'
keys, so share links keep working without everybody signing in first.

## Faster uploads with more bots

Discord limits how fast each bot may post messages. Uploads already send several pieces at once,
and every extra bot adds the same again:

1. Create another application and bot exactly as in [Step 1](#step-1-create-the-discord-bot-same-for-windows-and-linux)
   and invite it to the same server, with access to the drive channel.
2. Menu **6 (Settings) → Bots for uploading → Add a bot**, or `bots add` (it asks for the token,
   checks the bot can post in the channel, and saves it). Restart the drive.

Each device can use its own set of extra bots; any bot in the channel can read every piece.

## The config file

The config file (`%APPDATA%\DDrive\config.json`, or `~/.config/DDrive/config.json`) holds
your settings and the secrets the drive needs: bot tokens, encryption keys, the dashboard sign-in, and
your Discord account's token if you logged in for [Discord messages](#discord-messages). The
secrets are not stored readable. They sit in one entry, `protected`, encrypted for the computer and
account they belong to:

- **Windows**: with the account's own protection (DPAPI). Only your Windows account on that computer
  can decrypt them.
- **Linux / Raspberry Pi**: with a key made from a key file kept apart from the config
  (`~/.local/share/DDrive/machine.key`, readable by you only), the machine's id and your user id.

So a copy of the file, in a backup, a synced folder, a screenshot or a message asking for help, gives
nothing away. It is automatic: nothing to type, the drive still starts by itself, and a config from an
earlier version is converted the first time the drive starts. Settings stay readable and editable; a
token or key you type into the file by hand is picked up and encrypted at the next start.

- **Keep your recovery phrase elsewhere.** A copy of the config is no longer a copy of the
  key: after reinstalling Windows, resetting the account's password from outside, or moving to a new
  machine, the secrets can't be read and `setup` asks for the bot token and your recovery phrase
  again (or lets another of your devices approve it).
- **What it doesn't do**: a program running as you on that computer can ask for the secrets the same
  way DDrive does, and while the drive runs the key is in memory. It protects the file, not a
  computer someone else already controls.
- `config` shows how the secrets are kept; `config protect_config false` stores them readable again.

## Discord messages

The dashboard can keep your Discord conversations on the drive: **Messages** in the menu. It is the
logger and archiver from [Pretty Suite](https://github.com/xo907/pretty-suite), with the drive as the
place where everything is stored, so it is encrypted, on all your devices, and costs nothing.

- **Archive** reads conversations back through their history: whole servers, chosen channels, direct
  messages and group chats; everyone's messages or only yours or theirs, a date range, with or without
  the attachments, or the attachments only (the files without the conversation). It can be stopped and carried on, carries on by itself after a restart, and
  archiving the same conversation again only adds what is new.
- **Live logger** records messages as they arrive and keeps the original text of ones that are later
  edited or deleted. Record everything you can see or only what you pick; set keyword alerts; save
  attachments as they are posted, or only the attachments. It listens invisibly (your status doesn't change), reconnects by
  itself, and starts again with the drive.
- **Saved** is where you read it: search, filter (deleted, edited, keyword hits, with files, yours, one
  conversation) and export as a web page, JSON, CSV or text.

**Logging in.** Settings → Discord account → **Log in with Discord**, and paste your account's token
(the dialog says where to find it). Discord's own "Log in with Discord" button only tells an app who
you are; it cannot give it your messages, and the drive's bot only sees the drive's channel. The token
is kept with the other secrets in the [config file](#the-config-file), encrypted for that computer,
and is only ever sent to Discord. Logging and archiving run on the device you logged in on; log in on
one device only.

**Where it goes.** Plain text files, one record per line, that you can also open yourself:

```text
/Discord/Servers/<server> [id]/<channel> [id]/2024-01-03 to 2024-03-09.jsonl   an archived stretch
/Discord/Direct messages/<name> [id]/...                                        the same for DMs
/Discord/.../files/                                                             saved attachments
/Discord/Live/2026-10/2026-10-10 14.05.33 <device>.jsonl                        what the logger recorded
```

The logger writes what it recorded to the drive within half a minute (until then a message is marked
"text not on the drive yet"), and joins a day's small files into one the day after. Attachments are
uploaded right away and are not kept on the computer. The dashboard shows and opens them from Discord
itself while they are still there (it checks first); the copy on your drive, and a small preview of
each picture and video, is the backup that is used once Discord no longer has them. Other
devices read these files, so Messages works on all of them. Deleting a file removes its messages
from the lists. `/Discord` can be [password-locked](#password-locked-folders): recording carries on,
and the messages are hidden in the dashboard until you unlock it.

> **Know the risk.** This acts with your own Discord login. Automating a personal account is against
> Discord's Terms of Service and can lead to action against the account. Requests are paced, but the
> risk is yours to weigh. The token is full access to your account: never share it. Logging also
> keeps what other people wrote, including what they later delete; treat it as you would any private
> conversation.

## DDrive Lite

Use a drive that runs on another computer (the **host**) without running one yourself. A lite
computer has no bot, no encryption key and no copy of the drive: it shows the host's drive as a drive
letter (Windows) or a folder (Linux) and streams, downloads and uploads through the host.

```text
run.bat lite connect http://192.168.1.20:8765     sign in to the host (asks for your 24 words)
run.bat lite mount                                the host's drive appears as Z: (or: -m Y:, -b for the background)
run.bat lite stop                                 take it away again
run.bat lite autostart on                         show it by itself when this computer starts
run.bat lite status | ls [folder] | get <file or folder> [to] | put <file or folder> [folder] | disconnect
run.bat lite menu                                 all of this as a menu (also option 13 of the main menu)
```

(`./run.sh lite ...` on Linux.) A lite computer doesn't need the whole program: each release also has
**DDriveLite.exe** for Windows (open it for the menu) and **ddrive-lite_*.deb** for Debian,
Ubuntu and Raspberry Pi OS (`ddrive-lite`), which contain only this.

- **On the host**: the full DDrive, running, with its dashboard reachable from the lite computer
  (Settings → *On your network*, or your https domain). Lite signs in with 24 words, kept encrypted
  on the lite computer: the drive's recovery phrase shows the whole drive, the words of an
  [account](#accounts) show that account's own storage.
- **On the lite computer**: this program and, for the drive letter or folder, the same driver the
  full drive uses (WinFsp on Windows, libfuse on Linux). `ls`, `get` and `put` work without it.
- **Reading** fetches only the parts a program asks for, a few MB at a time, and keeps the last ones
  in memory: videos play and seek without being downloaded, and nothing is written to disk.
- **Writing** goes to a temporary file that is handed to the host when the file is closed and then
  removed. The host stores it on the drive like any other file (encrypted, uploaded to Discord,
  versioned). Changing part of an existing file fetches it first and sends it back whole.
- The 24 words are stored encrypted for that computer and account, like the full drive's secrets.
  Over plain `http` they travel unencrypted when signing in: fine at home, use `https` elsewhere.
- Everything the host's dashboard account may see is on the drive. Password-locked folders stay
  hidden (unlocking them is done in the dashboard).

## Password-locked folders

Lock a folder or a file with a password and it is gone, with everything in it, until the password is
typed: from the drive letter / mount folder and from the dashboard (files, search, gallery, deleted
files, snapshots, shared links, the log), on all your devices. Not even its name is shown.

- **Lock**: in the dashboard right-click the folder or file → **Lock with a password…**; or
  `lock <path>` (menu 7). Locks sync to your other devices like everything else.
- **Unlock** by typing a password; everything locked with that password opens, so nothing has to list
  what is locked. In the dashboard: the padlock at the top → **Unlock…**, which opens it in that browser
  only (turn on "Also show on the drive" to see it on that computer's drive letter too). In the
  terminal: `unlock` shows it on this device's drive (`--minutes 60`, or `--minutes 0` for until the
  drive stops).
- **It locks again by itself** after `lock_timeout_minutes` (15) without use, when you sign out, with
  **Lock again now** / `relock`, and whenever the drive restarts.
- **Remove a lock** with its password: right-click → **Remove the lock…**, or `unprotect <path>`. If
  you forgot it, `unprotect <path> --forgot` asks for the drive's recovery phrase instead.
- Wrong passwords are slowed down like wrong sign-ins. Folder sync keeps copying into a locked folder,
  and a link you shared to a locked item itself keeps working (locked things *inside* a shared folder
  don't show).

What it is and isn't: a lock is a gate, not a second layer of encryption. Locked files are encrypted
with the drive's key like all others. It keeps out anyone using your computer, the drive letter or the
dashboard; it does not stop someone with technical skill who can run programs as you on that computer. While a
folder is unlocked on the drive letter, every program on that computer can read it, and Windows may
remember file names it saw (recent files, Explorer's thumbnail cache). The log *file* on disk and the
terminal commands (`deleted`, `versions`) still name locked files; the dashboard's Log tab does not.

## Sync and back up folders

Keep a folder on this computer in step with a folder on the drive, e.g. `C:\Users\you\Downloads`
backed up to `Z:\Downloads` by itself. Set it up in the dashboard (**Sync → Add a folder**, with a
folder browser for both sides), in the menu (**12**), or with the `sync` command:

```powershell
run.bat sync add C:\Users\you\Downloads Z:\Downloads --mode backup
```
```bash
./run.sh sync add ~/Pictures /Photos --mode two-way --every 30
```

**What it does** (`--mode`):

| Mode | Direction | What happens |
|---|---|---|
| `backup` (default) | computer → drive | New and changed files are copied, each one once. Files you delete here stay on the drive, and copies you move, rename or delete on the drive are not put back. |
| `mirror` | computer → drive | The drive folder becomes an exact copy: deleting here deletes there too (it stays under **Deleted**, so it can be brought back). |
| `two-way` | both ways | Changes and deletions on either side are copied to the other. A file changed on both sides since the last sync is kept twice (`name (conflict <computer> <date>).ext`). |
| `move` | computer → drive | Files are copied, then deleted here once they are safely stored in Discord. Frees space, e.g. for Downloads. |
| `download` | drive → computer | A local copy of a drive folder (e.g. on an external disk). Nothing is deleted here. |
| `download-mirror` | drive → computer | An exact local copy. Files deleted on the drive are moved to a holding folder (`<data dir>\sync\trash`, kept 30 days), never deleted outright. |

**When it runs** (`--when`): `live` (default: a few seconds after something changes; Windows tells
the drive at once, elsewhere and for changes on the drive it checks every few seconds),
`interval` (`--every 30` minutes), `daily` (`--at 03:00`) or `manual` (only `sync run` or **Sync now**).

- A backup remembers what it has copied: reorganise the copies on the drive as you like, and only a
  file that changes on the computer is copied again. `sync run <n> --again` (dashboard: **Copy
  everything again**) puts back whatever is missing in the drive folder. Use `mirror` if the drive
  folder should always match the computer exactly.
- Copies keep each file's modification time, so a file is copied once, not on every pass. A file that
  is still being written (changed in the last 5 seconds, or locked by the program writing it) waits for
  the next pass; unfinished downloads (`*.crdownload`, `*.part`, ...) and temporary files are never
  copied. Skip more with `--exclude "*.iso,Temp/"`.
- Safety: a folder that isn't there (a disk that isn't connected) changes nothing, and if one side is
  suddenly empty, nothing is deleted on the other. Mirror and two-way sync need a folder on the
  drive, not the whole drive.
- `sync list`, `sync status <n>` (result, problems, recent runs), `sync run <n>` (shows progress),
  `sync stop <n>`, `sync pause <n>` / `resume`, `sync edit <n> --mode ... --when ...`, `sync remove <n>`
  (nothing is deleted), `sync modes`.
- Syncing runs inside the drive, so the drive has to be running. The folders belong to the device they
  are on (they are kept in its config file); what they copy onto the drive appears on all your devices.
- For safety, folders on a computer can only be chosen or changed in the dashboard **on that computer
  itself**, because that means reading and writing files outside the drive. Syncing now and pausing
  work from everywhere. To allow it from other devices too, turn on "Let other devices choose folders
  here" at the bottom of Sync (on that computer), or `config sync_remote_edit true`.

## Snapshots

Every `snapshot_interval_hours` (24) one device takes a snapshot: a record of the whole drive at
that moment. Its pieces are kept until it expires after `snapshot_keep_days` (14), even if the files
are changed or deleted meanwhile. Snapshots cost no space until files change.

- `snapshots` lists them, `snapshots create` takes one now (menu 7 → Snapshots).
- `snapshots delete <n>` (or `all`) deletes a snapshot for good, on every device; in the dashboard use
  the menu on a snapshot, or **Delete all**. Your current files are not touched; contents that only
  that snapshot still kept are removed from Discord and can't be brought back.
- `snapshot-restore 2 /Photos` brings `/Photos` back as it was in snapshot 2, into a new folder
  `Restored <date>`, so nothing you have now changes. `--in-place` puts files back where they were
  instead (their current content is kept as an earlier version, so that can be undone too).
- The web dashboard can browse a snapshot like a folder and restore any folder from it.

## Using the same drive on several devices

**Sharing the key with a new device.** The easiest way is the built-in key request (setup offers it
automatically; menu **10** on the device that has the key approves it). How it stays private:

- The new device posts a request with a one-time Diffie-Hellman public value (2048-bit, RFC 3526)
  and shows a verification code derived from it.
- After you confirm the code on the other device, that device answers with its own one-time public
  value and your keys, encrypted with AES-256-GCM under a key that only those two devices can
  compute. Discord, or anyone else who has your bot token, only ever sees public values and
  ciphertext. Comparing the code stops anyone from slipping in a request of their own.
- Requests expire after 15 minutes, and both messages are deleted once the key arrives.

Every device needs the **same bot token, channel, and encryption key**. `setup` on a new device gets
the key either from a device that already has it (approval, above) or from the drive's 24-word
recovery phrase. If the channel already holds an encrypted drive, setup never generates a new key.

Devices stay in sync while they are running:

- A new or changed file appears on the other devices as soon as its upload finishes, plus up to
  `poll_interval` seconds (default 2). New folders, renames, moves, and deletes appear within a few seconds.
- A device that was off catches up when it starts.
- **Simultaneous changes are merged, never overwritten:**
  - If two devices create a file with the same name, one keeps the name and the other becomes
    `name (conflict xxxxxx).ext`.
  - Folders with the same name are merged.
  - If the same file is edited on two devices, the edit that reaches Discord last wins. The other
    edit is kept as a version (`ddrive versions <file>`).
  - **Deletes are final.** A deleted file or folder (with everything in it) disappears on every
    device and can't come back through another device's late upload or checkpoint. To get
    something back on purpose, use `deleted` / `undelete` or `versions` / `restore-version`.
- Windows Explorer does not refresh open folders by itself; press F5 to see changes made elsewhere.

**Upgrading from an older DDrive:** update and start **one** device first. It converts its
index and publishes it. Then update the others: they adopt the published index automatically. Any of
their files that were still waiting to upload are kept and uploaded afterwards.

---

## What is stored on your computer

File contents live in Discord. Locally, DDrive only uses:

| What | Location (data dir) | How long |
|---|---|---|
| Index (file list, versions) | `index.db` | Always; usually a few MB |
| Files being written | `staging/` | Until the upload finishes, then deleted. Writing pauses when more than `staging_max_bytes` (10 GiB) is waiting to upload, and resumes as uploads finish. DDrive **never takes the disk below `min_free_disk_bytes`** (2 GiB): if its own uploads can't free space, it refuses the write instead, so other programs on the machine keep running. A file must be complete before it can be split and encrypted, so this cannot be avoided. |
| Files being edited | `staging/` | An existing file is downloaded completely while it is open for writing, then removed after it is re-uploaded |
| Viewed files (read cache) | `cache/` | **Disk mode (default):** kept up to `cache_max_bytes` (5 GiB) so re-opening is instant. **Memory mode:** never written to disk |
| Offline-pinned files | `cache/` | Until you unpin them (`free-space`) |
| [Accounts](#accounts) | `accounts/` | Only if somebody has one: the list of accounts (`accounts.json`, their keys encrypted for this computer; a copy without the keys is kept in the channel) and one folder per account with the same things as above (file list, files being written, up to 256 MiB of read cache) |
| Discord messages (search list) | `chat.db` | Only if you use [Discord messages](#discord-messages). The text of the messages (no attachments), for searching: a copy of the files in `/Discord`, rebuilt from them when needed |

**Memory-only cache.** To keep viewed files off the disk entirely, set `"cache_mode": "memory"`
in the config, or start with `mount --cache memory`. Chunks you view are held only in RAM
(`memory_cache_bytes`, 256 MiB by default, enough for smooth streaming and seeking), and anything
previously cached on disk is deleted at startup. Re-opening a file downloads it from Discord again,
so it is slower.

---

## How it works

```
 application ──► WinFsp / libfuse ──► DDriveFS (fs.py)
                                         │
              ┌──────────────────────────┼─────────────────────────┐
              ▼                          ▼                         ▼
     SQLite index (index.py)    staging files (writes)    chunk cache (cache.py)
     tree, chunk map, trash              │                         ▲
              │                          ▼                         │
              │                   Uploader (uploader.py)    downloads on read
              │                   chunk → encrypt → POST           │
              ├──── change journal (ops) ◄────► Discord channel ◄┘
              └──── periodic checkpoint ───────►     (journal.py)
```

- **Writing:** data goes to a staging file. When the last handle closes, the file is queued.
  Upload workers (`upload_threads`, default 3 per bot) split it into `chunk_size` pieces (9 MiB,
  just under the 10 MiB attachment limit for non-boosted servers). A piece the drive already stores
  is reused; any other piece is compressed if that helps, encrypted, and posted as its own message,
  several at a time and spread over every bot. After each group of pieces, its spare pieces are
  computed from the local copy and uploaded too. When everything is uploaded, the chunk list in the
  index is swapped atomically. The previous content is kept as a version, and expired versions are
  deleted from Discord in the background (after a 10-minute grace period, and only if nothing else
  uses those pieces).
- **Syncing:** every change to the tree (file uploaded, folder created, rename, move, delete,
  timestamp) is recorded together with the local change and posted to the channel as a small
  encrypted "operation" message. Every device reads the channel every `poll_interval` seconds and
  applies all operations in Discord's message order, which is the same on every device, so all
  devices converge on the same tree. Files and folders have stable random IDs, so renames and edits
  on one device are matched up on the others.
- **Reading:** if a staging copy exists it is used. Otherwise the needed chunks are downloaded
  (checked against their SHA-256, with expired CDN links refreshed), cached, and read ahead.
- **Checkpoints:** every `index_backup_interval` seconds (when something changed), a device uploads
  a gzip-compressed, encrypted SQLite snapshot of the index and pins it. The snapshot records its
  position in the journal, so a new device restores it and replays only the operations after it.
  Snapshots only reference data that is fully uploaded.

| Module | Purpose |
|---|---|
| `cli.py` | Command-line interface |
| `config.py` | Configuration file handling |
| `drive.py` | Startup, mount and shutdown |
| `fs.py` | FUSE operations, staging, back-pressure |
| `uploader.py` | Upload workers, trash cleanup |
| `journal.py` | Multi-device sync: publishing, polling, and applying operations; checkpoints |
| `index.py` | SQLite metadata index, conflict handling, versions, snapshots |
| `cache.py` | On-disk LRU chunk cache with de-duplicated parallel downloads |
| `backend.py` | Discord storage adapter (and a local folder backend for testing) |
| `discord_api.py` | Minimal Discord REST client (curl or urllib) with retry and rate-limit handling |
| `crypto.py` | AES-256-GCM via Windows CNG or the `cryptography` package; scrypt for passwords |
| `codec.py` | How a piece is stored: compressed when that helps, then encrypted |
| `rs.py` | Reed-Solomon erasure coding (the spare pieces), standard library only |
| `heal.py` | Rebuilding and repairing pieces, the background check, protecting old files, snapshots |
| `web.py`, `web/` | The web dashboard (server and app) |
| `accounts.py` | Accounts: sign-up, invites, and each account's own drive on the shared channel |
| `legal.py` | The terms and the privacy policy the dashboard serves |
| `legacy.py` | Keeps a computer working that was set up before the program was renamed |
| `actions.py` | Restoring versions and snapshots (shared by the command line and the dashboard) |
| `runtime.py` | Starting DDrive again as a process (from source or as DDrive.exe) |
| `fuse_loader.py` | Finds WinFsp / libfuse and loads the vendored fusepy |

You can try it without Discord: `python -m ddrive mount --mock <folder>` stores "messages"
as files in `<folder>`.

---

## Updating

**Coming from a version before 2.0?** The program had another name then. Nothing has to be moved: a
computer that was set up under the old name keeps using its config and data folders where they are,
and the start-with-the-computer entry and the Explorer menu are renewed by themselves the next time
the drive or the menu starts. What changed is what you type and what things are called: the folder
inside the program is `ddrive` (`python -m ddrive`), the programs are `DDrive.exe`, `DDriveLite.exe`,
`ddrive` and `ddrive-lite`, and the environment variables start with `DDRIVE_` (the old ones still
work). If you wrote a systemd unit or a script of your own that names the old module, change it there.
The repository moved too. **Update DDrive** in the menu points your copy at the new address by
itself; if you update with `git pull`, do it once by hand:
`git remote set-url origin https://github.com/xo907/ddrive.git`.

In the menu choose **4 (Update DDrive)**. It stops the drive, downloads the latest version,
starts the drive again and reopens the menu. Your settings, key and data are stored outside the
program folder, so updating never touches them. (`git pull` alone isn't enough: a running drive
keeps using the old version until it is restarted.)

Copying the files over yourself instead of using `git` (e.g. from Windows with `scp`)? Copying from
Windows drops Linux's "executable" permission, so restore it, then restart the drive:

```bash
chmod +x ~/DDrive/run.sh ~/DDrive/ddrive/scripts/linux/*.sh
```

---

## Troubleshooting

If the drive doesn't start, the start script now prints the reason. Otherwise the log is the first
place to look; `log --errors` shows recent problems without hunting for the file:
`%LOCALAPPDATA%\DDrive\ddrive.log` on Windows, `~/.local/share/DDrive/ddrive.log`
on Linux (background starts also write `mount.log` next to it).

### Windows

- **`winget` fails with "Failed when searching source: msstore" / "server certificate did not match".**
  The Microsoft Store source is blocked or intercepted (antivirus, company network). The Quick Start
  command already avoids it with `--source winget`. If you typed your own `winget` command, add that.
- **`winget` says "Found an existing package already installed… No available upgrade found".**
  Not an error: that program is already installed. Carry on with the next step.
- **`git`, `py` or `python` "is not recognized" right after installing.** Close PowerShell and open
  a new one; already-open windows don't see newly installed programs.
- **The drive started, but Z: is not in File Explorer.** You probably started it from an
  **Administrator** PowerShell (the prompt shows `C:\WINDOWS\system32>`). Drives started as
  administrator are invisible to your normal Explorer. Stop it there (`run.bat stop`), then start it
  again by double-clicking `run.bat` and choosing 1 (Start).
- **The drive letter does not appear at all.** A leftover process from an earlier run can block
  the mount: in the menu choose 2 (Stop), then 1 (Start). Also check that WinFsp is installed (the
  menu warns you if it isn't).
- **New files from another device don't show in an open Explorer window.** Press **F5**. Explorer
  does not refresh network-style drives by itself.

### Linux

- **`sudo: command not found`.** You're logged in as `root` on a system without `sudo` (typical for
  a VPS). The Quick Start command and the installer handle this automatically; just leave
  `sudo` out of any command you type yourself.
- **`python: command not found`.** Linux calls it `python3`. Use `./run.sh`, which calls the
  right one.
- **`apt` fails with "404 Not Found" for `python3-cryptography` (or another package).** Seen on
  Debian 11, which has reached end of life: its security mirror lists updates whose files were
  removed. The installer retries automatically with the release's own version. By hand:
  ```bash
  apt-get install -y -t "$(. /etc/os-release; echo $VERSION_CODENAME)" python3-cryptography
  ```
- **`apt` wants to remove `fuse3`.** Don't let it: other software (Docker volumes, rclone, sshfs…)
  may need it. DDrive only needs the FUSE 2 *library* (`libfuse2`) and works with either
  `fuse` or `fuse3` tools. The installer keeps whichever one is installed.
- **`E: Unable to locate package libfuse2t64`** (or `libfuse2`). The library was renamed:
  `libfuse2` on Debian 11/12 and Ubuntu up to 22.04, `libfuse2t64` on Debian 13+ and Ubuntu 24.04+.
  The installer picks the right name.
- **The installer warns that `/dev/fuse` does not exist.** Some VPS types (OpenVZ/LXC containers)
  have no FUSE support, so the drive cannot be mounted. Ask the provider to enable FUSE, or use a
  KVM-based server.
- **"No space left on device" while copying a lot of files.** Files you copy *in* are kept on the
  local disk until they are uploaded (the memory-only setting only affects files you *read*). Current
  versions never take the disk below `min_free_disk_bytes` (2 GiB, raise it with
  `config min_free_disk_bytes 5368709120` for 5 GiB): they pause the copy while uploads free space,
  and refuse writes (instead of filling the disk) when something else used the space. Update, then
  re-run the copy (rsync skips what is already there). On a small disk you can also lower the backlog: `config staging_max_bytes 2147483648`.
  If it still happens, check what is using the disk (`df -h /`, `du -sh ~/.cache`): when copying
  *from* another cloud mount such as rclone, that mount's own cache can fill the disk. DDrive's
  log says so. Limit it, e.g. remount with `rclone mount ... --vfs-cache-mode minimal --vfs-cache-max-size 1G`.
  If you set `max_file_size`, give rsync the same limit with `--max-size=2G` so it *skips* larger
  files; otherwise rsync stops at the first one with "File too large".
  To make a long copy resume by itself after any error:
  ```bash
  until rsync -rt --info=progress2 /source/ /mnt/ddrive/target/; do sleep 60; done
  ```
- **`/mnt/ddrive` looks empty although the drive is running.** If your terminal was already
  *inside* that folder when the drive started, it keeps showing the plain folder underneath. Leave
  and come back: `cd ~ && ls /mnt/ddrive`.
- **"already mounted" after an update.** The old version is still running. Update from the menu
  (option 4), which restarts it; see [Updating](#updating).
- **Files appeared in `/mnt/ddrive` while the drive was stopped** (a program or container kept
  writing there). They're handled automatically on the next start: moved aside, the drive mounts,
  and they're copied onto the drive. The log says how many.
- **"Transport endpoint is not connected".** An earlier instance crashed. Starting again cleans
  this up automatically, or run `fusermount -uz /mnt/ddrive` (`fusermount3 -uz` on FUSE 3 systems).

### All platforms

- **Editing the config by hand broke it** ("not valid JSON", "Expecting ',' delimiter"). Every
  line except the last needs a comma at the end. Easier: change settings with
  `config <name> <value>` (e.g. `config cache_mode memory`) instead of editing the file. A comma
  after the *last* entry is tolerated.
- **Files from another device don't show up.** Run `status` on both devices and compare:
  - **key fingerprint** must be identical. If not, run `recovery-phrase` on a working device and
    `setup -k "<those 24 words>"` on this one (or run `setup` and let the working device approve it).
  - **Discord** must show the same channel.
  - **Sync position** must show a message number (not "not started"). If it says "not started",
    that device is still running an old version; see [Updating](#updating).
  - **Changes to share** on the device that made the change should be 0.
- **One device is missing files that the others have** (or shows a "Recovered files" folder),
  although its key fingerprint matches. It missed some sync messages, for example while it had a
  different key. Rebuild its file list from the drive: stop the drive, run `restore`, start it again.
- **Some files can't be decrypted on one device but work on another, or a device stopped seeing new
  files.** The devices are using different keys, usually because `setup` was run again with another
  key and replaced the original one. Compare the fingerprints in `status`. Nothing is lost:
  give each device the key the others are missing with `add-old-key`; it keeps reading with every
  key it knows while new data uses its current key. Easiest: run `export-key` on a working device,
  paste the key into `add-old-key` on the other, then stop and start the drive. Current
  versions of `setup` always keep the previous key, and older keys travel inside the encrypted
  index, so a new device can read everything.
- **"Found index checkpoint ... could not restore it" / "Decryption / authentication failed".** This
  device has a different encryption key from the one that wrote the drive, so it refuses to start
  rather than show (and later save) an empty drive. This happens when the drive was created with an
  older version, which turned the same password into a different key on each computer, or with a
  random key. Bring the real key over:
  1. On a device where the drive works: `recovery-phrase` (menu 9 → "Show my recovery phrase").
  2. On this device: `setup -k "<those 24 words>"`, then start the drive. Setup confirms the key matches.

  From Windows to a Linux machine in one step, without the key appearing on screen:
  ```powershell
  (Get-Content "$env:APPDATA\DDrive\config.json" | ConvertFrom-Json).encryption_key | ssh user@host 'read k; ~/DDrive/run.sh setup -m /mnt/ddrive -k "$k"'
  ```
- **A file won't open ("I/O error", "invalid argument", or the player just stops).** Run
  `verify <path>` (or `verify` for the whole drive; it downloads everything once). It lists every
  file that can't be downloaded or decrypted and why. "Encrypted with a different key" means that
  file was uploaded while this setup used another key, so it can only be read with that old key.
  If an older version of it exists, `versions <path>` and `restore-version` can bring it back.
  Files that can never be read again (their key is lost) can be cleared out with
  `verify <path> --remove` (menu: Tools → check files, then answer yes). Copy them onto the drive
  again from their originals if you still have them.
- **A file is stuck "uploading"** (it shows in the menu's top line or in `status` and never
  finishes). Menu → **9 (Tools) → Cancel stuck uploads**: a changed file goes back to its last
  uploaded version, a file that was never uploaded is removed.
- **After updating one device, the other says "checksum mismatch" for new files.** It still runs
  an older version, which can't read compressed pieces. Update it too (menu option 4).
- **The log says a piece "was missing from Discord; rebuilt it from its spare pieces".** Self-healing
  at work: nothing to do. "Can't be rebuilt" means a file without spare pieces (uploaded before 0.2
  and not protected yet) or a group that lost more pieces than it has spare ones; `verify` lists the
  affected files.
- **A deleted folder keeps coming back.** One of your devices runs an older version, which still
  re-uploads it. Update every device (menu option 4); deletes are final from then on.
- **A "Recovered files" folder appeared.** A change arrived for a file whose folder had been
  deleted on another device at the same time. The file was put here instead of being lost.
- **Bot invite says "successful" but the bot did not join.** The invite URL must include `scope=bot`.
- **Want to confirm encryption is on?** `status` shows `Encryption: ON (AES-256-GCM) | key
  fingerprint …`, and the log says `Zero-knowledge AES-256-GCM encryption ACTIVE` at every start.
  In the Discord channel, attachments are named `chk_<random>.bin` with no message text.

## Legal

- **Not affiliated with Discord.** DDrive is an independent open-source project. It is not affiliated
  with, endorsed by or sponsored by Discord Inc. "Discord" is a trademark of Discord Inc. and is used
  in this document only to describe where the data is stored and what has to be set up there.
- **Discord's terms apply to you.** You use your own bot and your own channel. Using Discord as file
  storage, and the [Discord messages](#discord-messages) tool (which signs in with your own Discord
  account), may be against Discord's Terms of Service. Whether to use them is your decision and your
  risk; Discord can remove the data or the account at any time.
- **If you give other people accounts, you are the one providing a service to them**, with whatever
  duties that brings where you live (terms, privacy, handling reports about shared content). The
  [terms and privacy policy](#accounts) the dashboard serves are there to help with that; they are not
  legal advice. The authors of DDrive publish software and run none of the dashboards that use it.
- **No warranty.** The software is provided as it is, under the MIT license below.

## License

[MIT](LICENSE). The vendored `ddrive/_vendor/fuse.py` keeps its original ISC license.
