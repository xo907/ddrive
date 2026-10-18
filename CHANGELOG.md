# Changelog

Every change to DDrive gets a new version number and an entry here. The dashboard shows this
list under the account menu, "What's new".

## 3.11.0 (2026-10-18)

- **DDrive tells you when there is a new version, and updates itself.** Twice a day the drive asks GitHub
  which version is the newest (nothing about your drive is sent; it can be turned off in Settings › This
  computer). When a newer one is out, the owner's dashboard says so at the top, with what is new. A copy
  installed with git (run.bat / run.sh) has **Update now**: it downloads the new version, stops the drive the
  usual way (uploads finished first) and starts again with the new code, so a systemd service carries on as
  it was. DDrive.exe and the .deb package get a link to the download. *Check for updates* is in the account
  menu.
- **While the drive is restarting** (an update), the dashboard says "DDrive isn't answering… trying again"
  instead of failing, and loads itself again when it is back.
- **The DDrive app for Android is told when a newer app is out**, with a link to it.
- Notices at the top of the page stack instead of covering each other.
- Releases: the Android app built and signed on the maintainer's computer goes into the GitHub release
  when the repository has no signing key of its own.

## 3.10.1 (2026-10-18)

- **FindX after an update: your phones are there at once.** The FindX tab said "No devices yet" after every
  update or restart of the drive, for up to six hours, and ringing a phone didn't work meanwhile: the drive
  kept what the phones had told it in memory only, and a phone tells it its name and card only every few
  hours. Now it keeps it (sealed as it came, like everything of FindX's) in `findx-relay.json` in the drive's
  data folder, and circles keep their people the same way, so the map, live links, ringing and "arrived at
  Home" all go on right after a restart. A phone the drive knows nothing of is asked to say hello again.
- After a restart, rings and circle messages could also be taken by the phones for old ones (their numbers
  started again from 1): numbering goes on now.
- The FindX app (0.1.1) tells "DDrive isn't answering (it may be restarting)" apart from other problems, and
  tries again within two minutes rather than up to fifteen; a drive being restarted no longer makes it check
  for commands more often (and use more battery) from then on.

## 3.10.0 (2026-10-18)

- **Choose what is in the menu.** Settings › This browser › **In the menu** (on an account's page: under Your
  account) has a switch for each page of the menu: leave out Gallery, FindX, Queue or whatever you don't use,
  for a cleaner menu, and on a phone a bottom bar with only what you use. What is left out is in the Tools
  menu (the squares at the top) instead, and its address works as ever. Files always stays. It is kept in
  the browser (and in the phone app), so a phone and a computer can each have their own.

## 3.9.0 (2026-10-18)

- **DDrive for Android: the whole dashboard as an app.** The phone app (until now DDrive Lite, which only
  synced folders) opens your DDrive: Files, Gallery, Notes, FindX, Sync, locked folders, everything the
  dashboard has, light or dark as the phone is. It signs in by itself with the words it keeps (encrypted with
  a key in the phone's keystore): for an end-to-end encrypted account the keys are made on the phone and only
  a sign-in key goes to the host, as in a browser. Folder syncing is as before, under **This phone** in the
  account menu (or long-press the app's icon).
- Downloads go to Downloads/DDrive on the phone, an end-to-end account's opened on the phone first; uploads
  take files from anywhere on the phone; the phone's Back button closes what is open, then goes back a page.
  Links to anywhere but your drive open in the phone's browser.
- It is the same app, so it installs over DDrive Lite and keeps its sign-in and folders. Get
  [DDrive.apk](https://github.com/xo907/ddrive/raw/main/android/release/DDrive.apk).
- An end-to-end encrypted account needs the host's https address in the app (or a browser), as ever.

## 3.8.1 (2026-10-18)

- **A file replaced twice in a row no longer shows its old content for a moment.** When a file was sent to the
  drive and replaced again right after (a phone's folder sync uploads it, then it is changed on the dashboard),
  the drive could read its own first upload back from the change journal after the second one and put the old
  content back until the second was read too, about a second later. Whatever read the file in that second got
  the old one: a phone's two-way sync then gave up on it ("it changed while it was being copied") and the file
  was missing on the phone until the next run. A drive now never puts its own older upload over a newer one.
- **The Android app (DDrive Lite) is released again**, as 3.8.1: it keeps what a password-locked entry of an
  end-to-end account holds on to (it can't see or open those).

## 3.8.0 (2026-10-18)

- **Password-locked folders for end-to-end encrypted accounts.** Right-click a folder or file → **Lock with a
  password…**, as on a drive: it disappears, with everything in it and its name, from Files, search, the
  gallery, Deleted, Activity and snapshots, on every device of the account, until the password is typed
  (the padlock at the top → **Unlock…**). Everything locked with that password opens, in that page only,
  and locks again after 15 minutes without use, with **Lock again now**, or when the page is closed.
- In an end-to-end account a lock is more than a gate: what is locked is sealed again, with a key only
  its password opens (PBKDF2, 600,000 rounds). A computer or phone that syncs the account, or a page
  signed in with the 24 words, can't see or open it without the password, and neither can the server.
  So a forgotten password can't be got round, not even with the 24 words: the lock dialog says so.
- Locked things can be added to, moved into and out of, renamed, starred and deleted while open; moving an
  account into another takes them along, still locked with the same password.

## 3.7.2 (2026-10-17)

- **FindX draws the path you really took.** History on the FindX tab is cleaned as it is drawn: straight lines
  to places never been (a jump to the wrong mobile mast, a reflection), rough positions among good ones and
  zigzags are left out or smoothed, each position weighed by how accurate it is. This also cleans up walks
  recorded before. (The app now does the same as it records, and uses satellites while you walk.)
- The path no longer runs to the phone's current position when that is only a rough guess.

## 3.7.1 (2026-10-17)

- **FindX is a tab of the dashboard.** It opens beside the menu, like Files and Gallery, instead of on a page of
  its own; the old address (`/findx/`) goes to the tab.
- **FindX is light or dark as your device is**, like the rest of the dashboard, and changes with it. The map's
  sun button still offers day and night where the phone is, always light and always dark. Live-location
  links follow the viewer's device too.

## 3.7.0 (2026-10-17)

- **Everything FindX does, on the FindX page.** A phone's sheet has a new **Manage** button, and the map a
  **People** button. Manage has what the app has, for a phone you're not holding:
  - **Overview**: battery and charging, how it finds itself, when it last checked in, and what's wrong with it
    ("Location is off", "Battery saver is limiting FindX") as the app sees it; ring, pin down, live, lost mode.
  - **Settings**: rename it, your name in its circles, whether and in how much detail it keeps history, the Bluetooth beacon, lost mode's message; the phone
    applies them at once and says so in its log.
  - **Battery**: how much battery FindX used today and each day of the last two weeks, and what on (location,
    network, processing, sensors, ringing), as the app's Battery use shows it.
  - **Sharing**: make a live-location link for anybody, for 15 minutes up to "until I stop it", see the links
    it has and stop them.
  - **Activity**: what the phone has done lately.
- **People**: your circles, their people and places, made and changed from here: make a circle, invite people
  (copy the invitation), add places by tapping the map, say whether the circle may ring each of your phones,
  have a phone join or leave a circle, and ring anybody in a circle.
- Everything goes to the phone sealed with your words, as Ring does; the drive passes it on without reading it.
  A phone can't be turned off, unlinked or erased from afar.
- The FindX page needs https (or localhost): browsers only do its encryption there, and it now says so.
- A browser that can't draw maps (WebGL off) still gets the FindX page, without the map.

## 3.6.2 (2026-10-17)

- **A folder two computers made at once is one folder again.** On an end-to-end account, when two computers
  synced into the account at the same time (a PC and a Raspberry Pi, say), each could make the same folder
  before hearing of the other's, and you got two folders of one name: selecting one selected the other, and
  only one of them showed what was in it. They are now shown as one, with what is in both, and the next
  computer that syncs tidies it up for good: everything is moved into the one, and the empty twin goes. A file
  that was in both under one name is kept once when it is the same file, and otherwise also as "name (2)".
- It doesn't happen again: every computer gives a new folder the same id for the same name in the same place,
  so two of them making it at once make one folder.

## 3.6.1 (2026-10-17)

- **How much battery FindX uses, on the FindX page.** Each phone's sheet says "FindX used 0.4% battery today": FindX
  now measures its own use (processing time and network from the phone's kernel, satellites, Bluetooth, sensors and
  ringing timed as they're on, turned into charge with the phone's own power profile) and sends today's total along
  with where it is (sealed, like the rest). The app has the full picture under Battery use. Shared links don't carry it.
- The map's "always light" setting lights the 3D buildings like an afternoon, not by where the sun really is.

## 3.6.0 (2026-10-17)

- **FindX circles: family and friends, as in Life360.** People in a circle see where each other are, live,
  with their battery; they're told when somebody arrives at or leaves one of the circle's places ("Mom arrived
  at Home"); and they can ring each other's phones to find them, from the app and from the FindX page here
  (each person can switch that off for a circle). Make a circle in FindX (it lives on your drive) and send its
  invitation (`/j/…`): friends join from any DDrive, or none. Each circle has a key of its own, only in the
  invitation after the `#`, so this drive passes everything on without being able to read it.
- The FindX page shows everybody at once: your devices and the people in your circles, each with a small
  round face on the map, grouped by circle in the list. Tap one to follow them.
- **Light or dark map.** The sun button on the map: follows day and night where the phone is (as before),
  always light, or always dark. Remembered in the browser.

## 3.5.0 (2026-10-17)

- **FindX.** The drive now works with FindX, an Android app that finds your phone down to the spot it's
  lying on. The dashboard has a **FindX** page: your phones on a soft, Apple Maps-like map that follows
  day and night where each phone is, live, with where they have been and a slider to replay the day.
  Ring a phone (through silent and Do Not Disturb), pin it down, watch it live, or turn on lost mode.
- **Live-location links** (`/f/…`): a phone shares where it is for as long as you choose, or until you
  stop it, on a page anybody with the link can open. The key is in the link after the `#`, so the drive
  serves the page without being able to read the location.
- The drive never reads any of it: FindX seals everything on the phone with keys made from your 24
  words. The drive keeps each phone's latest sealed report in memory and passes sealed commands on
  (`findx.py`); history goes to `/FindX` as ordinary sealed files that are never deleted.

## 3.4.2 (2026-10-16)

- **Download in a file's details works in end-to-end encrypted accounts.** The Download button in the panel
  that opens when you click a file, and the one in the picture and video viewer, were plain links. For an
  end-to-end encrypted account the browser then asked the server for the file, which only has it sealed,
  and the download failed with "File wasn't available on site". They now open the file in the page first,
  as Download in the right-click menu always did.

## 3.4.1 (2026-10-16)

- **The status bot counts what the accounts hold.** "Files on the drive" and "of files on Discord" counted
  only the owner's own files, so a drive whose files are in dashboard accounts said "0 files, 0 B". They now
  add up the owner's and every account's files (only the numbers, still no names). Uploads, downloads and
  repairs going on in accounts count too, so "Uploading 20/100 files" also follows an account's uploads.
- "Error-free for 0 seconds" and "Watching this drive for 0 seconds" wait until there is an hour to say.

## 3.4.0 (2026-10-16)

- **Status bot.** A Discord bot of its own that watches the drive and says what is going on, in its status
  and its green / yellow / red dot: "Uploading 20/100 files", "Uploading at 4.2 MB/s", "All uploads
  completed", "Uptime: 25 hours", "25,000 files on the drive", "500 GB of files on Discord", "Experiencing
  I/O failures", "Drive offline for 5 minutes", and close to a hundred more, each shown only while it is
  true. Optionally it keeps a status message up to date in a channel, posts when something happens
  (problems starting and clearing, uploads finished, restarts, updates, milestones, the drive going offline
  and coming back, with a mention for serious ones) and answers `/status`, `/statuses` and `/uptime`. It can
  run on another computer, and then also notices the drive being down. `statusbot setup`, then `statusbot`;
  see "Status bot" in the README.
- **`monitor-key`** makes the key the bot reads the drive with (`/api/monitor`). It shows numbers and
  states only, never a name, and opens nothing else on the dashboard.

## 3.3.3 (2026-10-15)

- **"A sign-in key that belongs to no account here" no longer fills the Log.** A device still signed in
  to an account that was deleted (or a drive that was erased) asks again every few minutes, and each try
  was a warning. Now the same key (or phrase) from the same address is in the Log once an hour, with how
  many times it asked since; tries refused during the wait after too many wrong ones aren't logged at all.
- **A reverse proxy in Docker or elsewhere on your network counts as one.** Only a proxy on the same
  computer was believed about where a visitor came from, so behind Nginx Proxy Manager in another
  container every visitor was the proxy's address (172.x): one device with old words made everybody wait
  after wrong tries. With a domain name set (Settings → Domain names), a proxy at a private address is
  believed too.

## 3.3.2 (2026-10-15)

- **DDrive Lite stops trying words the host turned down.** When the host no longer knew a device's 24
  words (its account was deleted, or the drive erased), the drive letter and folder syncing kept signing
  in again on every file they asked for, until the host blocked the whole computer for too many wrong
  tries, the owner's own sign-in from it included. Now Lite asks again at most every 15 minutes (5 after
  "too many tries"), says why in the meantime, and the message says the account may have been deleted.
- **The menu no longer shows an old file list as "in Discord".** On a computer that isn't set up as a
  drive (one that only uses Lite, say), a file list left from an earlier setup was counted as if the
  drive still held it.

## 3.3.1 (2026-10-15)

- **Erasing everything keeps the bots, really.** On Linux the secrets of the config file (the bot tokens,
  the 24 words' key, the dashboard's sign-in) are encrypted with a key file kept in the data folder
  (`machine.key`), and erasing everything deleted it with the rest of the data folder: the config was
  kept, but its secrets could no longer be read. That key is kept now. A drive that was erased with
  3.2.0 to 3.3.0 on Linux needs `setup` (the main bot's token, and the 24 words or a new key) and
  `bots add` for each extra bot once; the channels and spare channels were kept.

## 3.3.0 (2026-10-15)

- **Each spare channel gets a bot of its own, picked at random.** A spare channel without a bot of its
  own was posted to by every bot of the drive, so one banned bot touched every channel. Now `spare add`
  picks one of the extra bots that has no channel yet, at random, gives it access and makes it that
  channel's bot; `bots add` gives a new bot to a spare channel that has none; and `spare assign` does it
  for the spare channels already there. A banned bot then takes at most one channel with it, which the
  spare pieces are there for. The extra bots still post in the main channel too, and a bot posting in
  two channels keeps one pace.
- Commands that look up a spare channel's own bot no longer mix up channels after one was given up.

## 3.2.0 (2026-10-15)

- **Erase everything.** A hard reset for starting over, in the dashboard (Settings → Erase everything,
  the owner's only) and as `erase-everything` in a terminal: every file, earlier version, snapshot and
  account is erased for good, on this computer and in every channel on Discord (the main one and every
  spare one). The bots, the channels, the 24 words and the dashboard's sign-in stay, and the drive starts
  again empty in the same channels. It asks for `ERASE EVERYTHING` to be typed, shows how far it is, and
  an interrupted reset is finished the next time the drive starts, never left half done. Messages younger
  than 14 days go 100 at a time.
- A test that uploads random data no longer fails now and then when that data happens to start with `{`.

## 3.1.0 (2026-10-15)

- **A deleted account can be brought back.** Deleting an account (by the owner, by itself, or the one a
  move came from) removed its record and its folder at once; only its pieces on Discord waited a week
  before being deleted, with nothing left that could use them. Now the account is kept aside for those
  `delete_after_days` (7): `accounts deleted` lists such accounts, and `accounts undelete <id>` brings one
  back as it was, its pieces taken off the list of what to delete. After that it is gone for good.

## 3.0.5 (2026-10-15)

- **Moving an account into another one keeps a copy until the move is safe.** The source account was
  deleted as soon as the files the target took over were stored on Discord. The target's own list of
  files could still exist only on the computer that runs the drive at that moment. Now the source goes
  only once everything the target holds is stored and a checkpoint of it has been saved to Discord.

## 3.0.4 (2026-10-15)

- **No more downloading for nothing in the background.** When a file is edited in place, its new version
  can share pieces with the old one. Once the old version expired, its group of spare pieces kept the
  shared pieces and lost the rest, and the background work that adds spare pieces (after the setting is
  raised, or a spare channel is added) kept trying to strengthen it: it downloaded the pieces that were
  left, found the others gone, and tried again later, over and over. Such groups are left alone now;
  the pieces they share are protected by the newer version's own group.

## 3.0.3 (2026-10-14)

- **`health` ends with one plain answer: is everything safe?** "[OK] Safe" when every piece has its
  spare pieces, any one server can be lost and the file list is backed up; otherwise exactly what isn't,
  most serious first, each with what to do: damaged files, pieces without spare pieces, groups still
  getting theirs, everything in one channel or one server, no recent local copy of the file list.

## 3.0.2 (2026-10-14)

- **An end-to-end account's file list can't be lost between two saves.** When a device sealed the whole
  list of an end-to-end account, the host dropped the changes that led to it right away, before the
  sealed list itself was on Discord. Had the host been lost in that moment, so was the account's file
  list. Now the changes and the previous sealed list stay until the new one is stored, and are dropped
  then; the host never hands out changes the sealed list already holds.
- **Releases say what is wrong with the Android app's signing key** (a key that can't be opened with
  its password, or isn't valid base64) instead of only failing, accept secrets pasted with a stray line
  break, and the Windows and Debian downloads go out even when the app can't be built.

## 3.0.1 (2026-10-14)

- **The Android app is part of every release again.** The release now signs DDriveLite.apk itself and
  attaches it next to the Windows and Debian downloads. Running the release again for a version that
  already has one adds what is missing instead of failing.

## 3.0.0 (2026-10-14)

The data-safety and speed release: DDrive guards against everything on its own side that could lose
a file, and against a whole channel or server going on Discord's side, while uploading, reading and
checking faster.

**Survive more**
- **A whole server can be lost.** DDrive learns which server each channel is in. With spare channels in
  two or more servers, every group is spread over the servers first, with enough spare pieces that any
  one server can go (deleted, or its bots banned) without losing a file: with 2 servers that is as much
  space again, not three times as much.
- **A channel that is gone is rebuilt elsewhere.** `spare lost <n>` (or `spare remove <n>` on a channel
  that holds pieces) gives a channel up: its pieces are rebuilt from the rest of their groups into the
  channels that are left, in the background. Until then they are read around it. A channel that refuses
  every bot is named on the Health page, and nothing new goes there.
- **Copies of what finds your files.** Index checkpoints, every account's checkpoint (end-to-end ones
  too) and the list of accounts are copied to up to 3 spare channels in different servers, 2 kept in each,
  marked with the drive they belong to (a channel shared with another drive is never cleaned of its
  copies). An account's checkpoint is read from a copy when the main channel's is gone.
- **Restoring never stops at one bad checkpoint.** Every checkpoint found is tried, newest first, and
  checked before it is used (an older one plus the change journal after it gives the same result).
  With no checkpoint at all in a channel that holds a drive, the file list is rebuilt from the whole
  change journal, instead of an empty drive being started over it.
- **The file list on this computer is backed up.** Once a day a copy of `index.db` goes to `backups` in
  the data folder (7 kept) and SQLite checks it; a damaged file list is said loudly and never uploaded
  as a checkpoint.
- **Crash-proof uploads.** A finished upload is written to disk before its local copy is deleted, even
  through a power cut, and an upload's progress record is only dropped once the file is committed.
  Programs asking for their data to be on disk (fsync) now really get that.
- **Nothing local is thrown away unasked.** A local copy no file knows about, and the copy of a file
  whose waiting upload is cancelled before it was ever uploaded, go to `recovered` in the data folder.
- **"Remove unreadable" tries the spare pieces first.** A file is only removed when Discord confirms a
  piece is gone *and* its spare pieces can't rebuild it; a piece that can be rebuilt is put back instead.
- **Old groups with repeated pieces are counted at their real strength** (a message filling several
  places of a group costs all of them when it goes) and get the spare pieces that makes up for it.
- **Trash cleanup can't get stuck** behind one piece it can't delete.

**Faster**
- **Groups overlap.** A group's spare pieces are computed and uploaded while the next group's pieces
  upload, instead of everything waiting for them.
- **More at once.** Up to 16 pieces upload at the same time (was 8), counting the bots of every channel,
  and never more than a quarter of the free memory can hold.
- **Spare pieces use little memory.** They are computed 8 at a time, so 50 per group no longer needs
  hundreds of megabytes; repairing one spare piece computes only that one.
- **Uploads hand their data to curl through memory** on Linux, not through its input one small write
  at a time.
- **The background check lists channels** 100 messages per request instead of asking about each piece:
  a hundred times fewer requests, so big drives are checked in full every `scrub_days`.
- **Rebuilding reads in parallel.** Pieces needed to rebuild one (and to add spare pieces) download 4 at a
  time, and spare pieces upload several at a time.
- **Read-ahead** keeps every download thread busy when a file is read straight through, and opening a
  file to edit it downloads several pieces at once.
- **The drive doesn't freeze for checkpoints:** they are copied from a connection of their own instead
  of locking the file list, and the dashboard's counts aren't recomputed on every request while files
  change. Upload progress is saved every 2 seconds instead of after every piece (big files).
- Looking for groups that still need spare pieces only happens when the settings or channels change.

## 2.40.1 (2026-10-14)

- **Accounts use the spare channels and the spare-piece setting too.** An account's files (also
  end-to-end ones, and a drive moved into an account) always went to the main channel only, so spare
  channels didn't protect them. They are now spread like the owner's files. An account that was
  already open also kept the spare-piece setting it was opened with; the protection settings now
  follow the owner's settings at once.

## 2.40.0 (2026-10-14)

- **Up to 5 spare pieces for every piece.** The "Spare pieces" setting went up to 8 per group of 10.
  It now goes up to 50: at 10 every piece has a spare (any half of a group can go, twice the space),
  at 20 two (any two thirds can go, three times the space).
- **Small files get their share.** A file of 1 to 3 pieces always got 1 spare piece. It now gets its
  share of the setting (2 pieces at 10 per 10 get 2), and still 1 with the default.
- **Raising the setting strengthens what is stored.** Groups stored with fewer spare pieces than the
  settings now ask for get the rest in the background, one group at a time while nothing uploads,
  keeping the spare pieces they have. `health` shows how many are left.
- **The setting says what it does.** Settings → Protection explains the number with examples and what
  each costs in space.

## 2.39.0 (2026-10-14)

- **Every bot gets access to a spare channel in one go.** A spare channel without a bot of its own is
  posted to by all the drive's bots, and each one needed access to it. `spare add` now gives it to
  them: the main bot sets each bot's permissions on that channel (it needs "Manage Permissions" there).
  `spare grant` does it again for every spare channel, and `bots add` does it for a new bot. Bots that
  aren't in that channel's server get an invite link with the server already chosen, since only a
  person can add a bot to a server.
- An upload that a bot isn't allowed to post goes to the next bot instead of failing, and that bot is
  left out for a while (the Health page names it).

## 2.38.0 (2026-10-14)

- **Spare channels: a whole channel can be lost without losing a file.** Spare pieces covered Discord
  losing some pieces, but every piece, spare ones included, was in one channel: a deleted or purged
  channel, or its bot banned along with its messages, took every file. Now `spare add <channel id>
  [bot token]` adds a channel (best in another server, with a bot of its own), and pieces are spread
  over all the channels with enough spare pieces that any one of them can go. With one spare channel
  that costs as much space again as the files; with two, half; with four, a quarter.
- Files already on the drive are spread in the background, one group at a time, while nothing
  uploads. Their existing spare pieces are kept and more are added in the spare channels. `health` and
  the Health page show how many groups are left.
- Index checkpoints are copied to the first spare channel. A drive whose main channel is gone starts
  again from a new, empty main channel: it takes the checkpoint from the spare channel and rebuilds the
  lost channel's pieces from the others.
- Repairs go back into the channel the piece was in, or into another channel if that one is gone.
- `spare remove` refuses while pieces are stored in that channel (`--force` removes it anyway).

## 2.37.0 (2026-10-14)

- **A refused bot no longer makes files look broken.** When Discord refused a bot access to the
  channel (403), its pieces were counted as gone, the same as deleted ones. Lookups take turns over
  the bots, so one extra bot that can't see the channel made about one read in N look missing, and
  listed intact files under "Parts that can't be repaired". Now only "unknown message" (404) means
  gone. A refused bot's lookups go to the main bot and the others instead, that bot is left out for
  10 minutes, and the Health page names it. A wrong channel id ("unknown channel") also no longer
  counts as every piece being gone.
- **"Remove unreadable" asks Discord again first.** Removing a file frees its pieces for deletion, so
  the dashboard's button and `verify --remove` now check each file's pieces again and keep any file
  that isn't proven broken right now.
- **Repeated content no longer weakens a group.** De-duplication could point several places of one
  group at the same stored message (runs of zeros in a disk image, for example). Losing that one
  message then cost more places than the group has spare pieces. Every repeat in a group now gets a
  message of its own.
- **Spare pieces always match what was uploaded.** A resumed upload whose file changed in between
  kept the old spare pieces of a group it partly uploaded again, so they could never rebuild it. Such
  groups now get new spare pieces, and spare pieces are only computed from content whose checksum
  matches the uploaded pieces.
- **Deletions wait a week and are written down.** Pieces nothing uses any more stay on Discord for
  `delete_after_days` (7, was 10 minutes) before they are deleted. Every deletion is noted in
  `deletions.log` in the data folder. `health --why <piece>` shows what a piece belongs to, whether
  this device deleted it, and what each bot gets from Discord for it. `health` says how many lost
  parts this device deleted itself.
- **Uploads are checked.** An upload whose size on Discord differs from what was sent is deleted
  and sent again. `config verify_uploads true` also reads every piece back once.
- **Faster background check.** One full pass now takes 2 days (`scrub_days`, was 7; existing
  configs keep their value). When a piece is missing, the rest of its group is checked straight
  away. Files whose spare pieces couldn't be made are tried again after a week instead of never.
- **The last 5 index checkpoints are kept** in the channel instead of only the newest.

## 2.36.0 (2026-10-13)

- **Uploads from the dashboard go in the order that fits the server's disk.** Several files chosen
  at once were started three at a time in the order they were chosen. On a computer with little room
  (every file lies there whole until it is on Discord, and some of the disk is always kept free:
  `min_free_disk_bytes`) they filled what there was together, and then all of them waited or failed.
  Now the page asks the server how much it can take in right now, and of the files that wait starts
  the biggest one that fits next to the ones being sent, then the biggest that fits next to that,
  and so on. A file that doesn't fit stays in the queue without holding a place ("Waiting for room on
  the server · needs 4.5 GB, the server can take 1.2 GB more right now") and goes when what was sent
  has moved on to Discord and made room. When nothing fits and nothing is on its way, the smallest
  one goes and waits at the server itself, which lets it through the moment there is room.
- Where the server keeps nothing free, or doesn't say, files go in the order they were chosen, as
  before. Still three at a time at most.

## 2.35.1 (2026-10-13)

- **Half-sent files from the last hour can be removed too.** "Remove half-sent files" on the
  Resources page left alone every file that something had arrived for within the hour, which is also
  what an upload that failed or crashed twenty minutes ago looks like. When that is all there is, the
  button now reads "Remove anyway" and, after asking, removes the ones nothing arrived for in the
  last minute. Only a file a part came in for within that minute, which is being sent right now, is
  still left alone.

## 2.35.0 (2026-10-13)

- **Resources: "Remove half-sent files".** What arrived of an upload that never finished (it failed,
  was stopped, or its page was closed) stays on the disk of the computer that runs the drive for a
  week, in case it is taken up again. On a small disk that is room other uploads are waiting for.
  The Resources page now says how many such files there are and how much they hold, the accounts'
  included, and removes the ones nothing has arrived for in an hour. Files still being sent to are
  left alone; no file that is on the drive is touched. An upload that is only waiting for room and
  is removed with them starts again from its beginning.

## 2.34.0 (2026-10-13)

- **An end-to-end encrypted account's upload goes on after the page was reloaded, closed or
  crashed.** A file is sealed with a key made for that upload, which was in the page's memory only:
  after a reload the same file was a new upload from the start, and what had arrived of it lay on the
  server unused for a week. Now, for a file of 32 MB or more, the browser keeps that key and the
  name of the sealed object while the file is on its way, in its own storage next to the account's
  key and sealed with it. Choose the same file again, into the same folder, and it goes on from what
  the server has of it ("Going on where it stopped…" in the Queue): what is there already is read
  once more here for the file's fingerprint, and not sealed or sent again.
- The file is known again by where it goes, its size, its date and the fingerprint of its first
  piece: another file under the same name is a new upload, not joined onto the old pieces. What is
  kept goes when the file is in the list, after a week (as long as the server keeps half a file),
  and with the account's key when signing out. It is in this browser on this device only, and not
  in a private window.

## 2.33.0 (2026-10-13)

- **A file there is no room for right now waits; it is not failed.** Uploading from the dashboard, a
  file the computer that runs the drive had no room for at that moment was refused ("There is no room
  for this file…") and what had arrived of it was deleted, although room on a disk comes and goes.
  Now it waits, keeps what has arrived, and says why in the Queue ("Waiting for the server: there is
  no room for it right now (7.1 GiB are free and 5.0 GiB of that are always kept free)"). It goes on
  by itself when there is room. Only a file the disk could never hold (bigger than the whole disk
  less what is always kept free) is refused. A folder sync from another computer or a phone is still
  told at once, so that it gets on with its other files and comes back to this one.
- **Stop, and Try again, in the Queue.** A file that is being sent has "Stop". One that failed or was
  stopped has "Try again", and that goes on from what the server still has of it instead of from the
  start: for the drive's own files as before, and now for an end-to-end encrypted account too, whose
  browser keeps what the first try sealed the file with (in the page's memory only) and asks the
  server how much of it is there. What is there already is only read once more here, for the file's
  fingerprint; it is not sealed or sent again. This holds while the page stays open: after a reload
  the same file is a new upload.

## 2.32.1 (2026-10-13)

- **A long name no longer runs over the size next to it.** In lists that show a name with the place
  it is in on a line below (Activity on Home and its own page, what a search finds, Deleted, Health,
  a check's findings, the biggest folders under Storage), a long name was not cut short and was
  drawn across the size, most of all on a phone. It is cut with "…" now. In Activity the name gives
  way first, so that what was done with the file ("added", "moved from …") stays readable, and the
  whole of it shows on hovering.

## 2.32.0 (2026-10-13)

- **The progress cards in the bottom right corner are gone: the Queue page is where progress is.**
  The cards for files being sent from the browser, for uploads to Discord, for files saved from a
  link and for an account being moved sat on top of the page's buttons and said less than the Queue
  does. None of them is shown any more. The number next to "Queue" in the menu says that something
  is going or waiting, on every page, and the Queue shows all of it.
- What the cards did besides showing progress is kept: a file that could not be uploaded is said in
  a message with its name and the reason (and stays under "Finished" in the Queue); a file saved
  from a link is said when it is there or failed, and the folder that is open shows it; a move of an
  account is a row in the Queue from the moment it is started, counted in the menu's number, with
  "Show" to open its window again.

## 2.31.3 (2026-10-13)

- **An upload no longer stalls on the browser's store for blobs.** A part of a file was handed to
  the browser as a Blob. Those are kept in a store of the browser's own, and when that is full
  (little room on the device's disk, a private window, much else held in it) every part failed with
  `ERR_BLOB_OUT_OF_MEMORY`, was sent again, and failed again: "The connection dropped. Trying
  again…" on every file, without end, with nothing wrong with the connection. A part is now plain
  bytes in the page's memory (8 MiB per file being sent), which that store has no say in.
- **A big file waits for the room other uploads are using, instead of being given up.** When the
  computer that runs the drive had no room for the rest of a file, it deleted what had arrived of it
  and answered "no room" (507). With several files being sent at once that was often only so for
  the moment, the others holding the room until they are on Discord. Now, while parts of other files
  are arriving, such a file is told to wait and keeps what it has ("Waiting for the server: other
  uploads have to finish first"). Only when nothing else is arriving and there still is no room is
  it told that there is none.

## 2.31.2 (2026-10-13)

- **Uploads from a browser no longer give their parts up over nothing.** Since 2.29.0 a part that is
  on its way is given up and sent again when the browser says the network changed, so that an upload
  doesn't hang after switching from Wi-Fi to a cable or the phone's connection. But Chrome also says
  "changed" each time its estimate of the connection's speed moves, which it does all the time while
  a big upload runs. Every such report gave up every part that was being sent, on all files at once,
  and when they came faster than a part takes to send, nothing got through: "Waiting · The connection
  dropped. Trying again…" on every file, for minutes. Now a part is only given up when the kind of
  network really is another one (where the browser says which it is), when the browser goes
  offline, or after four minutes without an answer.

## 2.31.1 (2026-10-13)

- **"The server lost its place in this file. Upload it again." is gone: the upload goes back and
  carries on by itself.** An end-to-end encrypted account's browser gave a file up when the server
  had another amount of it than the browser had sent, and a file of several GB had to be started
  over. Four things, each of which could lead there or made it worse:
  - **The browser goes back.** When the server has less of a file than was sent (it lost some of it,
    or all), the upload returns to the last whole piece the server has, seals on from there and says
    so in the Queue. The file is in the browser, so nothing has to be given up.
  - **The server takes a part from where the sender says it starts.** A part that is sent twice (its
    answer never arrived), or that starts in the middle of what is there, replaces what was there
    after that point instead of being refused. Only a part that starts further on than what is there
    is answered with where the server is.
  - **Two requests for the same file can't write into each other any more.** A part is read whole
    and then written under a lock, so the end of a connection that was given up and the try that
    follows it can't both add to the file (which left more in it than was sent, and that was the end
    of the upload).
  - **"No room" is said as that.** When the computer that runs the drive has no room for a file, the
    browser stops and shows that reason. It used to try again, find the half-sent file gone and
    report that the server had lost its place.
- An answer that doesn't take a part ("I am somewhere else", "wait") is given after the part was
  read. Answered while the sender was still sending, the connection was reset under it, and without
  a reverse proxy in between the sender never saw the answer and tried again without end.

## 2.31.0 (2026-10-13)

- **Uploading a file that is already there asks first.** Until now it was overwritten without a word.
  Now, before anything is sent, the dashboard looks at the folders the files go into, and for each
  file that is already there it shows the two side by side (size and date of the one there and of the
  one being uploaded) and asks: **Overwrite**, **Skip**, or **Cancel upload** (nothing is sent at
  all). "Do the same for the other N that are already there" applies the answer to the rest, so a
  folder uploaded twice is one question. Files that aren't there yet go up as before. It is so for
  the Upload button, folders, files dropped or pasted, for the owner and for accounts.

## 2.30.0 (2026-10-13)

- **Queue: a page that shows everything on its way, live.** In the menu, with the number of things
  going or waiting next to it. One list, in three parts that move as it goes:
  - **Going now**: what this browser is sending (for an end-to-end encrypted account: sealing and
    sending) or opening and saving, with how far, how fast and how long; what is uploading to Discord,
    piece by piece, and its spare pieces; what is being fetched back from Discord to be read or kept
    offline; files being saved from a link; an account that is being moved.
  - **Waiting**: files this browser hasn't started on (each can still be taken out), and files that
    wait to go to Discord.
  - **Finished**: what this browser sent and saved since the page was loaded, and what failed, with
    the reason. "Clear finished" empties it.
  "All / Uploads / Downloads" narrows it, and a line on top says how much is going and waiting, the
  speed up and down, and about when all of it is on Discord. A row is made once and then only changed,
  so the bars move instead of flickering; the server is asked once a second while the page is open
  and not at all otherwise.
- It is there for the owner and for accounts. An end-to-end encrypted account's page asks the server
  what it is storing for it (`/api/queue`, which names only sealed things) and puts the files' names
  to them in the browser; the many small sealed records of an account are one line.
- A download the browser does by itself (the owner's, from the drive) is listed as handed to the
  browser: no page is told how far such a download is, the browser's own list shows it.
- The card with the uploads in the corner opens the Queue (it opened the Log, which accounts don't
  have), and stays away while the Queue is open.

## 2.29.2 (2026-10-13)

- **The README says what "sealed" means next to "encrypted".** They are the same thing (AES-256-GCM)
  done in two places: sealed by an account's own device with the account's keys, encrypted by the
  computer that runs DDrive with the drive's key. An account's files get both. Nothing in the program
  changes.

## 2.29.1 (2026-10-13)

- **The README says who can read what.** The overview picture showed only the owner's drive: devices
  that run DDrive, encrypting for Discord. That is still true of the drive, but it left out accounts,
  which are sealed in the account holder's browser before the computer that runs DDrive gets them.
  The picture now shows both, and a new section, "Who can read what", sets the two side by side:
  where each is encrypted, who has the key, and what Discord, the computer that runs DDrive, the
  owner and an admin can and can't read. It also says plainly what is not the rule: a drive set up
  without encryption, accounts of the earlier kind, and that the dashboard's code comes from the
  computer that runs DDrive. Nothing in the program changes.

## 2.29.0 (2026-10-13)

- **An upload from the dashboard carries on by itself after the connection drops or the network is
  changed.** It used to hang (the card stood still for good) or fail, and the file had to be sent again
  from the start. Now:
  - **A part that doesn't arrive is sent again**, from where the server has the file up to, for as
    long as it takes. Nothing that arrived is sent twice.
  - **A request that hangs is noticed.** When the browser says the network went away or changed
    (Wi-Fi to a phone's connection, a cable pulled), the part under way is given up at once and sent
    again once there is a connection; after four minutes without an answer in any case.
  - The upload's card says what it is waiting for ("No connection. It carries on when there is one
    again.") instead of standing still.
- **The owner's drive takes uploads in parts now** (4 MiB each), as an end-to-end encrypted account
  already did. So a big file no longer depends on one long request, a reverse proxy's limit on a
  request's size no longer stops it, and what arrived is kept by the server: the same file chosen
  again, also after the page was loaded again, goes on where it stopped. The file keeps the date it
  has on your device.
- For an end-to-end encrypted account an upload that was interrupted by closing or reloading the page
  still starts over: its pieces are sealed with a key that only that page had.

## 2.28.0 (2026-10-13)

- **Messages asks Discord more carefully.** Reading your conversations uses your own account, not a
  bot, and an account that asks a lot gets noticed. Now:
  - **Requests keep a distance from each other**, all of them together: an archive and the dashboard
    at the same time are never faster than one of them alone.
  - **A limit Discord announces is waited for before it is reached.** Every answer says how many
    requests are left and when there are more; when none are left, the next request waits for that,
    instead of being sent and turned down.
  - **After a "slow down", the archive goes on at half the speed** (down to an eighth if it keeps
    happening) and comes back up slowly over the next few hundred requests. Before, it waited out
    the one pause and carried on as fast as before.
  - **An archive reads at a walking pace**: a page of 100 messages every 2 to 3 seconds instead of
    about one a second, a rest of 8 to 16 seconds after every 20 pages, a few seconds between
    conversations, and a breath between attachment downloads.
- **Settings → Discord → "Messages pace"**: gentle (the above, and what it is set to) or normal (about
  as fast as it was, still with the distance, the waiting for announced limits and the slowing down).
  An archive of a long conversation takes two to three times as long on gentle.
- Working links to attachments that Discord was asked for are no longer all forgotten at once when
  there are many, so the same ones are not asked for again.

## 2.27.0 (2026-10-13)

- **The owner decides where thumbnails are kept**: Settings → Thumbnails → "Keep thumbnails".
  - **server** (as it was): a thumbnail a browser makes is handed to the computer that runs the
    drive, which keeps it encrypted in its data folder, so that every device has it at once. (An
    end-to-end encrypted account's go sealed into the account instead, not onto that computer's disk.)
  - **browser**: each browser keeps the thumbnails it made for itself, and none take room on the
    computer that runs the drive. A new browser or device makes them again as pictures are looked
    at. They are kept by account, and signing out removes them from that browser.
  It is for everybody on the dashboard. Thumbnails the computer already has stay and are still used;
  Resources → "Thumbnails" clears them.
- **"Create all thumbnails" can be turned off**: Settings → Thumbnails. The gallery's button is then
  gone for everybody, and thumbnails are only made as pictures are looked at, as before.
- With thumbnails kept in the browser, "Create all thumbnails" skips the ones that browser has, and
  says where they go before it starts.

## 2.26.0 (2026-10-13)

- **Identical files are found in end-to-end encrypted accounts.** There, two copies of a file are each
  stored in full (the server can't tell that they are the same), and the Storage page could not say
  whether there were any. Now:
  - **Files carry a fingerprint of their content** from the moment they are uploaded in a browser.
    It is in the sealed list with the file, so the server doesn't see it. Two such files that are
    the same show up under Storage → Identical files at once.
  - **"Look for identical files"** on the Storage page compares the ones that have no fingerprint yet:
    what came from DDrive Lite or the phone app, from before this version, or with an account that
    was moved in. It is done in the browser. Only files of the same size can be the same, so only
    those are fetched, and of most of them just the first 1 MiB; the ones that start the same are
    read whole. What is found out is kept with the files, so each is read once. It can be stopped
    and carried on later; the page says how many files are still to be compared.
  - "Keep the oldest, delete the rest" works as on the owner's drive. Here it frees real space, once
    the copies are gone from Deleted too.
- A fingerprint only counts for the content it was made of: a device that replaces a file without
  knowing about fingerprints can't leave a wrong one behind.

## 2.25.2 (2026-10-13)

- **The Storage page of an end-to-end encrypted account no longer says there are no identical copies.**
  It said "0 B, none found" and "No file is stored twice", but identical files are not looked for in
  such an account at all yet. It now says "Not checked", and that two copies of a file are each
  stored in full there (the server can't tell that they are the same).

## 2.25.1 (2026-10-13)

- **The Storage page of an end-to-end encrypted account shows its spare pieces.** It said "0 B", as if
  the account had none. It has them: the server adds spare pieces to the sealed pieces it stores, as
  for any drive, so that lost pieces can be rebuilt. The page works out its figures in the browser,
  which doesn't know how much the server keeps of them, and filled in 0. It now asks the server.
  ("Identical copies" stays at 0 for these accounts: every file is sealed with a key of its own, so
  two copies of a file don't look alike to anybody.)

## 2.25.0 (2026-10-13)

Faster, most of all for end-to-end encrypted accounts:

- **An encrypted account opens without reading its whole list again.** Every load of the page
  fetched the account's whole list of files from the server and opened it, which took as long as the
  account was big. The list as it was last opened is now kept in the browser, sealed with the key
  the browser already keeps (one it can use but not read out), and the next visit asks the server
  only for what changed since. It is the account's alone and goes when you sign out. If the server
  has less than the browser remembers (it was brought back from a copy), all of it is read again.
- **Pages you have been on come back at once.** A folder, the gallery, Home, Starred, Recent, Deleted
  and search results are remembered while the page is open, with what the status said then. Going
  back while nothing has changed shows them without asking the server. Anything you change throws
  that away, and what changes elsewhere changes the status, so it is what the server would answer.
- **A page starts loading when the pointer is on its link** (a finger touches it): the menu's pages,
  and a folder in a list once the pointer rests on it. The click then finds it there.
- **Several files upload at once**: three at a time instead of one after the other, with a folder's
  folders made first. Many small files are much quicker.
- **The status that is asked for every few seconds answers "the same" in a few bytes** when nothing
  changed, instead of the whole status every time. The figures of the Resources page, which are
  different every time, are only asked for while that page is open.
- **Less work in the page for a big encrypted account**: the account is only counted again when it
  changed (it was on every status), what its devices are fetching is asked for every 20 seconds
  instead of every 3 unless something is being fetched, and two things reading the list at the same
  moment no longer read the same changes twice.
- **Parts few visits need are fetched when asked for**: the "Don't trust, verify" page and moving the
  drive into an account are no longer loaded with every page.
- **How long the drive worked on each answer** is in a `Server-Timing` header: the browser's developer
  tools (Network, a request, Timing) show it, so a slow page can be told apart: the drive, or the way
  to it.

## 2.24.1 (2026-10-13)

- **Thumbnails are loaded and made where you are looking.** Every tile that had come near the screen
  queued its thumbnail and kept its place, so after scrolling the page was busy with parts nobody was
  looking at, and what was on screen waited behind them. Now only what is on screen, and about a row
  beyond it, is worked on, from the top down. A tile that is scrolled past before its turn came stops
  waiting and asks again when it is looked at; one that only flies by during a fast scroll doesn't
  start at all. The next ones begin when you scroll to them.
- A thumbnail that is already being made is finished, also if its tile has left the screen meanwhile
  (two are made at a time).

## 2.24.0 (2026-10-13)

- **A picture or video without a thumbnail shows that one is coming.** Until its thumbnail was made, a
  tile in the gallery or the grid looked like a plain file with nothing going on. Now:
  - **Waiting its turn** (two are made at a time): a light passes over the tile and a small arc turns.
  - **Being made**: the arc becomes a ring that fills as far as it really is. For a picture that is
    how much of it has arrived; for a video, the steps (finding the frame, reading it, shrinking it).
  - **A thumbnail that is there and only has to load**: the light alone, until the picture fades in.
- Small and grey on purpose, so that a wall of new pictures doesn't flicker; with "reduce motion" set
  on the device nothing moves and the ring still fills.

## 2.23.2 (2026-10-13)

- **Uploading from the dashboard works again.** The Upload button, "Upload a folder…", dropping
  files on the page and pasting a screenshot all did nothing: the part of the dashboard that takes
  the chosen files and sends them, folders included, had been lost in 2.4.0 (a browser's console
  said "uploadAll is not defined"). It is back, for the owner's drive and for accounts, and a file
  that fails now says so in its card and in the count at the end.
- The "Connecting" notice no longer ends with "Nothing is wrong".

## 2.23.1 (2026-10-13)

- **While the page is "Connecting", it says what it is doing.** A page that was just opened was empty
  until it knew who is signed in, which for an end-to-end encrypted account takes as long as reading
  and opening its list of files. Now, if that lasts more than half a second, the place where the page
  will be shows quietly what is being done: asking the server, getting the key kept in the browser,
  reading the list (with how many files and folders so far), getting ready to show pictures; and why
  it takes time: the list is locked on the server and opened in the browser on every load.
- It is only there while the top right says "Connecting", never afterwards, and is gone the moment the
  page is drawn. A page that connects at once never shows it.

## 2.23.0 (2026-10-12)

- **Files and the Gallery keep themselves up to date.** What another device, a synced folder, the
  drive letter or somebody's upload adds, changes or removes now shows up in the folder or gallery
  that is open, a few seconds later, without reloading. It stays where you scrolled to.
- **It costs nothing while nothing happens.** No list is fetched on a timer: the status the page asks
  for every few seconds anyway now carries a number that goes up when files change, and only then is
  the open folder or gallery asked for again (at most every four seconds), and drawn again only if it
  really is different.
- **Nothing moves under your hands.** While a menu, a dialog or the picture viewer is open, something
  is being dragged or a name is being typed, the page waits and catches up afterwards.
- **It can be turned off**: Settings → This browser (for an account: Your account) → "Refresh Files and
  Gallery by themselves". The choice is kept in the browser it was made in.
- For an end-to-end encrypted account, changes made on its other devices arrive when the browser next
  reads the account's list, which it does every 15 seconds.

## 2.22.0 (2026-10-12)

- **"Don't trust, verify": a page that shows how your files are kept private, with your own files.**
  In the account button's menu. It explains in plain words what happens to a file, and then lets you
  watch and check each part yourself. Every value on it is made in your browser by the same code
  that locks an end-to-end encrypted account's files:
  - **Watch a file being locked.** Choose any file (it is not uploaded or stored): its first bytes,
    the key made for it, the pieces, each piece locked with AES-256-GCM, and exactly what a server
    would be given. Then try to get in: with the right key, with another key, and after one bit is
    changed.
  - **Check a file that is really stored** (end-to-end encrypted accounts). Pick a file in your
    account: the page fetches the server's copy and shows it as the server has it, under the random
    name it is stored by, then opens it here, and shows that a changed copy is refused.
  - **Look at what the server knows**: your list of files as the server stores it, and everything it
    knows about the account, asked for on the spot.
  - **From 24 words to keys**, with words made for the demonstration: what a server gets, and the
    browser refusing to hand out the key of the list.
  - **Check the page itself**: the rule that keeps the page from talking to any other site, as the
    browser received it, and the fingerprints of the code that is running, to compare with GitHub.
- It says what is true of who is looking: an end-to-end encrypted account, the owner's drive (which
  the computer that runs it can read), or an account of the earlier kind. And it ends with what it
  can't promise: the page comes from the server, what the server still sees, and that a lost key
  can't be rescued.

## 2.21.0 (2026-10-12)

- **Going from page to page is immediate and can't get stuck.**
  - A click is answered at once: a line runs along the top and the page before steps back until the
    new one is drawn. Until now nothing moved before the answer came, which looked as if the click
    hadn't been taken.
  - Clicking on to another page (or the same one again) drops what the page before was still waiting
    for, so its connection is free for the new page straight away.
  - A slow answer can no longer draw an old page over the one that is shown. That was what looked
    like a page stuck "loading" until other tabs were clicked: the menu said one page, and the page
    that had been asked for before it had been drawn over it.
- **Messages: a picture that is gone from Discord is shown from the copy on the drive.** The list
  shows the saved file itself, made small by the browser. Nothing is made of it and nothing more is
  stored. (While Discord still has an attachment, the small picture in the list comes from Discord,
  one request per picture, and nothing of it is kept either. A video that is gone from Discord shows
  as a file to open.)

## 2.20.4 (2026-10-12)

- **`lite connect` with the owner's words works while wrong tries are being refused.** DDrive Lite
  tries typed words as an end-to-end encrypted account's first, and as the owner's (or an earlier
  account's) only when that is turned down. While wrong tries from the address were being refused it
  stopped at the first answer, "Too many wrong tries", and never got to the second. It goes on now,
  as the sign-in page does since 2.20.3. The host has to be on 2.20.3 or later for either to help.

## 2.20.3 (2026-10-12)

- **The right 24 words always sign in.** After five wrong sign-ins from an address, every sign-in
  from it was refused for a while ("Too many failed tries"), the right words too. One device that
  kept asking with words that no longer work was enough to keep everybody behind the same address
  out for good: DDrive Lite and the phone app ask again every few minutes, and each try started the
  wait anew. That happened to anyone who moved an account into another while a device was still
  signed in to the one that moved. Now only wrong tries are refused during the wait; the right words
  get in and clear it. (They are 256 bits: letting them through helps nobody who is guessing.)
- **The words of an account that was moved into another say so.** Signing in with them answers "This
  account was moved into account … and is gone" instead of counting as a wrong try, in the browser
  and in DDrive Lite.
- The sign-in page, while wrong tries are being refused, still offers "Send the words and sign in"
  for the owner's and an earlier account's words. A sign-in key that belongs to no account is now in
  the Log, as a wrong phrase already was.

## 2.20.2 (2026-10-12)

- **Saved previews can be deleted where Messages shows no conversations.** After an account is moved
  into another, its Discord attachments (and the previews next to them) are in the "From account …"
  folder, and Messages shows none of it, so the button to delete the previews wasn't there. It now
  is: on the Messages page also while it is empty ("Delete previews of attachments saved by earlier
  versions"), and under Your account ("Previews of Discord attachments").
- **An end-to-end encrypted account needs no computer for it.** Messages needs one of the account's
  own computers to be on, and so did deleting the previews. The browser has the account's list of
  files open, so it now removes them itself.

## 2.20.1 (2026-10-12)

- **"Delete saved previews" covers the whole drive.** It only looked in `/Discord`, so the previews in
  a copy of it elsewhere were left: an account that was moved into this one brings its own, under
  "From account …". Now every conversation's `files/previews` is cleared wherever it is on the drive.
  Only files that are unmistakably such previews are removed (`<attachment id>.webp` in
  `<conversation> [<id>]/files/previews`); other folders that happen to be called previews are left
  alone.

## 2.20.0 (2026-10-12)

- **A move between two accounts can be followed from any page.** Once its window was closed, there
  was no way to see how far a move was: the account that moves out could only start again from the
  code. Now, while an account is in a move, on either side, a card at the bottom right (on a phone:
  above the menu) says how far it is: waiting for the code, how many pieces and bytes are copied,
  copied and waiting to be taken in, or taken in. It is there on every page, also after signing in
  again or on another device of the account, until the move is done or cancelled.
- Clicking the card opens the move's own window again, with its codes and "Cancel the move". Your
  account has the same as a row, "Show the move".
- On the account that receives, a browser other than the one the move was started in can follow and
  cancel it, but not take it in: the key for that is kept in the browser it was started in, and the
  window says so.

## 2.19.0 (2026-10-12)

- **Discord messages: only the attachment itself is saved.** Until now a small preview of every
  picture and video was downloaded and stored next to it (in `files/previews`), so each one was on
  the drive twice. No preview is made or downloaded any more: an attachment is saved once, as it was
  sent.
- **Delete saved previews.** Messages → the ⋯ button → "Delete saved previews" removes every preview
  that was stored before, in all conversations on the drive at once, for good (they don't go to
  Deleted). The attachments and the messages stay. It works for end-to-end encrypted accounts too,
  where the account's own computer does it.
- In the Messages list a picture is still shown small while Discord has it: that comes straight from
  Discord and nothing of it is kept. One that is gone from Discord has no small picture any more; it
  still opens from the copy on the drive.

## 2.18.2 (2026-10-12)

- **"Manage the drive" has a menu of its own.** For an admin the left (on a phone: bottom) menu was
  empty there, the top bar had a hole where the search is, and the account button was a dot. Now the
  menu holds Settings, Health, Log, Resources and "My files" (the way back), the top bar says
  "Managing the drive", and the account button shows a shield.

## 2.18.1 (2026-10-12)

- **An admin account from before 2.18.0 becomes an end-to-end encrypted one by signing in.** 2.18.0
  only said such an account can't sign in any more. Now, signing in with its 24 words (then "Send the
  words and sign in") opens a page where the browser makes 24 new words: once they are saved and
  confirmed, the account is an end-to-end encrypted admin with those, keeps its note, and the old
  words stop working. It needs an https address (or the computer itself). Under Settings → Accounts
  such an account is marked "Needs new words" and says how.

## 2.18.0 (2026-10-12)

- **Admin accounts are end-to-end encrypted.** An admin is now an ordinary end-to-end encrypted
  account, with storage of its own that the server can't read and 24 words made in the browser, that
  may also manage the drive. Signed in, it finds **Manage the drive** in the menu at the top right:
  the drive's settings, accounts, invites and other admins, Health, the Log and Resources. "Back to
  my files" leads back.
- **An admin no longer opens the owner's files.** Until now an admin signed in as the owner, files
  included. Now it manages the drive and nothing else: listing, opening, searching, uploading,
  sharing and deleting the owner's files are refused, as is changing how the owner signs in. (Health
  and the Log can still name a file of the owner's, where one is damaged or an error is about one.)
- **Admin accounts made by 2.16.0 to 2.17.2 can't sign in any more.** Their words were made by the
  server. They are marked "Can't sign in" under Settings → Accounts: delete them and make new ones
  with "Make an admin…".
- **The server makes no account's words any more.** "Make an account…" and "Make an admin…" need an
  https address (or the computer itself), where the browser makes the words; on plain http they say
  so instead of making an account whose key the server keeps. `accounts admin` in a terminal is gone
  for the same reason, and so is sign-up without the browser making the words.
- **Accounts of the earlier kind are retired.** An account whose key the server keeps (made before
  2.5.0, or since then on plain http) can only be read now: signed in, it is told so and offered
  "Move everything…", which seals all of it in the browser into a new end-to-end encrypted account
  with 24 new words; then the old one is deleted under Your account. Uploading, changing, sharing
  and syncing are refused for it. Under Settings → Accounts such accounts are marked "Earlier kind".
- What the server still reads, as before: the owner's own drive (it is that computer's drive;
  "Move everything…" under Settings → Accounts moves it into an end-to-end encrypted account too).

## 2.17.2 (2026-10-12)

- **An admin account's 24 words sign in at once.** Until now the sign-in page answered "No end-to-end
  encrypted account here has these words" and wanted a second button pressed, which sent the words to
  the server. Now the browser signs an admin in the way it signs in an end-to-end encrypted account:
  with a sign-in key made from the words, so the words stay in the browser. The server keeps a check
  that recognises that key, next to the one that recognises the words.
- An admin account made before this version is asked for the second button once more ("Send the words
  and sign in"); after that it signs in at once too. On a plain http address nothing changes.

## 2.17.1 (2026-10-12)

- **Signing in with an admin account's words no longer looks like a failure.** The sign-in page tries
  24 words as an end-to-end encrypted account first, and an admin account isn't one, so it showed "No
  end-to-end encrypted account here has these words" and named only the owner's phrase and earlier
  accounts. It names admin accounts now, and the window that shows a new admin's words says how they
  are entered: like any 24 words, then "Send the words and sign in". Nothing about signing in changed.

## 2.17.0 (2026-10-12)

- **A shorter menu.** The left (on a phone: bottom) menu has six pages now: Home, Files, Gallery,
  Starred, Shared, Deleted. Notes, Bookmarks, Messages, Contacts and Folder sync are under a new tools
  button (the squares, top right); Activity, Snapshots, Storage, Health, Log and Resources are in the
  account button's menu, with Settings, Find anything, What's new and Sign out. The button that holds
  the page you are on is highlighted.
- **A much faster dashboard**, measured on a phone-like connection and processor (80 ms away, a quarter
  of a laptop's speed):
  - A folder of 1,500 files shows in about 0.1 s instead of 2 s: the first screenful is drawn at once
    and the rest right after, only rows on screen are laid out, and icons are made once and copied.
  - Big lists (folders, search, the gallery, activity, storage) come compressed: 200 KB → 16 KB.
  - The page's own files are kept by the browser until they change (their addresses carry their
    version), so loading the dashboard again asks the server for none of them: 0.5 s → 0.3 s.
  - The dashboard no longer asks for its status twice when it opens, doesn't redraw anything when
    the status hasn't changed, and asks nothing at all while its tab is in the background.
- Unchanged: what is cached is only the program's own files (never a file's content or a list of
  yours), compression is only for browsers and never for answers that carry a secret, and the
  session cookie stays SameSite=Strict, so another site can't trigger these requests.

## 2.16.1 (2026-10-12)

- **Make accounts from the dashboard.** Settings → Accounts → "Make an account…" makes an account for
  somebody, whatever sign-up is set to (also while it is closed), with a note of who it is for. It is
  end-to-end encrypted: the 24 words are made in your browser and shown before anything is sent, and
  the server only gets the sign-in key made from them. On a plain http address, where a browser can't
  encrypt, it makes one of the earlier kind instead (its words made by the server). Admins can do it
  too.

## 2.16.0 (2026-10-12)

- **Admin accounts.** A co-owner with 24 words of its own: it signs in to the owner's dashboard and
  can do everything the owner can, except change how the owner signs in (recovery phrase, passkeys,
  WebDAV password). Settings → Accounts → "Make an admin…", or `accounts admin [note]` in a terminal.
  Switching it off or deleting it signs it out at once. Admins are listed with an "Admin" tag and
  don't count towards "Most accounts".
- **`accounts` command**: `list`, `admin`, `disable`, `enable`, `delete`, `moves`, `move-cancel`.
  It asks the running dashboard, or changes the list on disk while the drive is stopped.
- **Moving one end-to-end encrypted account into another.** "Receive…" on the account that takes
  everything, "Move out…" on the one that goes. The two pages agree on a key (ECDH P-256) through the
  server, which only sees public keys; two codes compared between the screens catch a server that
  swaps a key or a list. The server copies the sealed files between the accounts in the background
  without opening them, the receiving page adds the whole list in a new folder, and the account that
  moved out is deleted once the files are stored as the other's. Cancel any time before that; the
  owner sees and can cancel moves under Settings → Accounts.
- **Faster dashboard.** Answers no longer wait for the browser's delayed acknowledgement (Nagle's
  algorithm with headers and body sent apart): about 40 ms less per request on a kept-alive
  connection, which is most of them (status 44 → 2 ms, an end-to-end account's change 90 → 6 ms).
  Large folders list twice as fast (locks are read once per request, not once per entry, and the
  hidden/locked check is skipped where nothing below can be hidden). The page's own files are sent
  compressed (app.js 266 → 75 KB) and answered with "not modified" when the browser has them. Finding
  what still has to be uploaded uses an index instead of reading the whole file list (10 ms → 0 on a
  drive of 200,000 files, asked every few seconds).

## 2.15.3 (2026-10-12)

- A GIF (or animated WebP) on a view-only share link moves again. Such a link paints pictures instead
  of showing the file, which showed only the first frame; now the frames are opened one by one and
  painted in turn (Chrome, Edge and other browsers that can; elsewhere the first frame is shown).

## 2.15.2 (2026-10-12)

- The share link page (`/v/...`) fits in the window: no page scrollbar. A picture, film or text is
  sized to what is left of the window; a long text or a long list of files scrolls inside its own
  box. The "End-to-end encrypted" badge at the top right is gone.

## 2.15.1 (2026-10-12)

- The share link page of end-to-end encrypted accounts (`/v/...`) got a proper look: a header, a
  card, nicer rows and buttons, light and dark.
- **Opening shows how far it is**: a bar with the percentage, how much was fetched and decrypted, and
  about how long is left, and a running count while it waits for the server, so a big file doesn't
  look stuck.
- **View-only pictures are shown only while they are held** ("Press and hold to view"): let go and
  the picture is gone. A snipping tool needs the same hand to drag its rectangle. What is shown also
  disappears when the pointer leaves the page, and at once, for a few seconds, when the Windows /
  Command key or PrintScreen goes down, which is how screenshot tools are started.
- Still true: a photo of the screen, or a screenshot key pressed with the other hand while holding,
  can't be prevented by a page.

## 2.15.0 (2026-10-11)

- **A share link with downloading turned off is much harder to save from** (links of end-to-end
  encrypted accounts, the `/v/...` page). Until now "view only" just left the Download button out.
- **A picture is no longer a picture in the page.** It is painted, no bigger than it is shown, onto a
  grid of sixteen small canvases under a pane that takes the clicks. There is no image element and no
  address of a file anywhere in the page, so there is nothing to right-click, drag out, copy, or
  "Save image as", and nothing for a download helper to find: what comes from the server is sealed
  pieces, not pictures.
- On such a page the right-click menu, selecting, copying, dragging, and the keys for save, print,
  view source and the developer tools are refused; printing gives an empty page; and what is shown is
  hidden while the window is not in front, which is what happens when a snipping tool opens.
- Films and music lose their save, cast and picture-in-picture buttons, and the address they play
  from stops working once they are loaded. A PDF is not shown on a view-only link at all, because a
  browser can't show one without offering to save it.
- What this does not do, and no page can: stop a photo of the screen, a screenshot the system takes
  by itself, or somebody who writes a program around the link (the link is the key). It makes saving
  awkward for people, not impossible for a determined one.
- Links of the owner's drive and of accounts of the earlier kind (`/s/...`) are unchanged.

## 2.14.0 (2026-10-11)

- **"Move everything…" in Settings, under Accounts**: one button that copies your whole drive into an
  end-to-end encrypted account, from the dashboard.
- It is done **in the browser**, because the server may not do it: sealing needs the account's key,
  and the point of such an account is that the server never has it. The page makes the account's 24
  words (shown once, to write down) or takes the words of an account you have, and then goes through
  the drive file by file: fetched as you see it, sealed in the browser, sent back sealed.
- Folders, files with their dates and stars come along, and Notes, Contacts, Bookmarks and saved
  Messages land where the account's pages look for them. A file that is in the account already with
  the same size and date is skipped; one that changed becomes a new version there.
- **Keep the tab open** while it runs; every file passes through your connection twice. It can be
  stopped and carried on, and pressing the button again later (with the account's words) copies only
  what is new. Nothing on the drive is changed or deleted; deleting the old drive is yours to do once
  you have checked the account.
- Not carried over: earlier versions, snapshots, share links, and folders that are hidden or
  password-locked on the device you press it on (unlock them first).
- For this the owner's page signs in to the account next to its own sign-in: a session of the
  account that it sends along with the account's sealed requests only. The owner can make such an
  account whatever the sign-up setting for others is.
- `lite import` (2.13.0) does the same from a command line, without a browser tab.

## 2.13.0 (2026-10-11)

- **A drive letter for end-to-end encrypted accounts.** `lite mount` now shows such an account as a
  drive letter (Windows) or a folder (Linux), as it does for other accounts: open, save, rename and
  delete files in any program. Files are fetched sealed and opened on your computer, and what you
  save is sealed there before it is sent; the server sees none of it. What you delete goes to the
  account's Deleted page. This is the same in DDrive and in DDrive Lite (`ddrive lite mount` /
  `ddrive-lite mount`).
- **File requests for end-to-end encrypted accounts.** "Request files" on a folder makes a link for
  others to send you files, with an expiry, a size limit and an optional password. The sender needs
  no account: their browser seals each file with a key of its own and puts that key, with the name
  and size, in a note sealed with the key in the link. The server keeps both and can open neither.
  Your dashboard takes what arrived into the folder the next time it is open (nothing is ever
  overwritten), and Activity says it came through a file request.
- **Moving in: `lite import <folder> [folder in the account]`** copies a whole folder into an
  end-to-end account, keeping its folders and the files' dates. Point it at the drive letter of your
  DDrive (`ddrive lite import Z:\`) to bring everything over, including Notes, Contacts, Bookmarks
  and saved Discord messages, which the account's pages then show. Run it again to carry on where it
  stopped; only what is new or changed is copied, and nothing is deleted on either side. Stars,
  earlier versions, snapshots and share links of the old drive are not carried over.
- With this, nothing an ordinary account has is missing from an end-to-end one, except Messages on
  the phone app (it runs on a computer).

## 2.12.0 (2026-10-11)

- **End-to-end encrypted accounts get Messages**, the last page they were missing: the Discord logger
  and archiver, with search, the live logger, keyword rules, archiving conversations, attachments
  and export.
- For other accounts the server reads Discord and keeps the messages. For these it may not (it would
  read every message), so **one of your own computers does it**: DDrive or DDrive Lite, signed in to
  the account, with folder syncing on. It runs the same logger and archiver, and what they keep goes
  into the account sealed, in `/Discord`, as files every device of the account can open.
- The Messages page asks that computer what it would otherwise ask the server, through the server in
  sealed messages, and shows the sealed answers. The server passes them on and can read neither the
  questions (what you search for) nor the answers (your messages).
- **The Discord token stays on that computer**, in a file only your user can read. It is typed in the
  dashboard and goes to the computer sealed; the server never has it. A computer that is logged in
  carries on logging whenever DDrive Lite runs, with or without the dashboard open.
- With no such computer on, the page says so; what was saved is still in `/Discord` under Files.
- The phone app can't do this part (it has no Discord logger); it does previews, saving from links
  and syncing as before. DDrive Lite's package now carries what Messages needs.
- With this, an end-to-end account has every page of the dashboard. Still not there for it: file
  requests, and a drive letter through DDrive Lite.

## 2.11.0 (2026-10-11)

- **End-to-end encrypted accounts get share links**, and the Shared page with them: a file or a
  folder for someone else, with an expiry, an optional password, and view-only if you like.
- **The key is in the link.** A link ends in `#` and 43 characters: 32 random bytes that a browser
  never sends to any server. With them the browser seals a description of what is shared (names,
  sizes, and the keys of those files). The server keeps that sealed description and hands out the
  sealed files it lists, and nothing else of the account; it can open neither. The page the visitor
  gets (`/v/...`) opens them in the visitor's browser: pictures, video, music, PDF and text are shown
  there, anything can be downloaded.
- **A password is part of the key**, not something the server checks: without it the description
  can't be opened even by someone who has the link, and the server never learns it. For the same
  reason changing a link's password or its view-only setting asks for the password again.
- A shared folder is the folder as it was when the link was made (or last changed). What a link
  hands out stays on the server while the link works, also if the file is replaced or deleted
  meanwhile. Turning a link off ends it at once.
- Not for these accounts yet: file requests (links for others to send you files), which need the
  sender's browser to seal for you. Messages is the one page still to come.

## 2.10.0 (2026-10-11)

- **End-to-end encrypted accounts get Activity, Snapshots and folders as a ZIP.** All three are worked
  out in the browser; the server takes no part beyond handing over sealed pieces.
- **Activity** lists what was added, changed, moved and deleted, and by which device. It is read off
  the list of files itself: an entry now says when it came and from which device (the dashboard, a
  computer's name, the phone's name). Nothing separate is recorded, so what was purged for good is
  gone from it too, and entries from before this version show without a device.
- **Snapshots**: "Take snapshot now" keeps the whole account as it is, and while the dashboard is open
  one is taken by itself once a day and kept 30 days. Open one to browse it, restore a file, a folder
  or everything into a "Restored ..." folder. A snapshot is a sealed file holding the list of that
  moment; restoring makes new names for the same sealed content, so nothing is uploaded again.
  What a snapshot refers to stays on the server until the snapshot is deleted, also when a computer
  or the phone replaces or removes the file in the meantime (DDrive Lite and the phone app 2.10.0
  check for that; update them with the server).
- **Download as ZIP** for a folder or a selection: the files are opened and put together in the
  browser. Up to 4 GB and 65,000 files in one ZIP; in Chrome and Edge a big one is written straight
  to disk.
- Still to come for these accounts: Shared links and Messages.

## 2.9.0 (2026-10-11)

- **End-to-end encrypted accounts get Contacts, Bookmarks and "Save from a link".** The same pages as
  for everybody, with the work moved to where the key is.
- **Contacts** are one vCard file in the account (`/Contacts/Contacts.vcf`, as for other accounts), read
  and written in the browser: add, edit, delete, import a `.vcf`, export. What a contact holds that
  DDrive doesn't show (custom fields from another address book) is kept.
- **Bookmarks**: the list and the pictures are sealed files in the account. Reading a page for its
  title, description and picture is something the server did, and for these accounts it may not: it
  would learn which pages you save. **One of your own devices does it instead**: the dashboard asks a
  computer with DDrive Lite or the phone app (with folder syncing on) through the server, in a sealed
  message, and gets the sealed answer back. With no device on, the bookmark is saved without a
  picture, and "Read the preview again" fetches it later.
- **Save from a link** works the same way: the device downloads the file, seals it and sends it, and
  the dashboard shows how far it is. With no device on, the dashboard says so.
- This is in all three: DDrive on a computer, DDrive Lite, and the phone app (2.9.0). As on the
  server, a device only fetches addresses on the internet, not ones on its own network.
- The page reader is a module of its own now (`pagepreview.py`), which DDrive Lite carries.
- Still to come for these accounts: Messages, Shared, Snapshots, Activity, and a folder as one ZIP.

## 2.8.0 (2026-10-11)

- **End-to-end encrypted accounts get the whole dashboard.** The plain files page of 2.5.0 is gone. An
  end-to-end account now opens the same dashboard as everybody: Home, Files with everything one does
  there (upload, also by dropping; preview; rename, move, copy, delete; star; search; earlier
  versions), the Gallery with thumbnails, Starred, Notes, Sync, Deleted and Storage, and the account
  page. It looks and behaves the same; what differs is where the work is done.
- How: the dashboard asks the server for everything, and for these accounts the server can answer none
  of it (it holds sealed files and a sealed list). So a layer in the browser (`e2e-api.js`) answers
  the dashboard's questions instead, from the list of files as the browser opened it, in the server's
  own words. Pictures and videos shown on the page are opened through a small service worker that
  asks the page for the bytes.
- **Videos and music play without being downloaded first**, and seeking works: only the pieces that are
  played are fetched and opened.
- **Thumbnails** are made in the browser, sealed, and stored with the file, so the gallery is quick on
  every device of the account.
- **Deleted** keeps what you delete for 30 days, for bringing back. **Earlier versions**: a file that
  is replaced, by the browser or by a device that syncs, keeps what it was (the last ten). **Copy**
  makes a second name for the same sealed content; nothing is uploaded again.
- **Sync** is the dashboard's own Sync page: the account's computers and phones, their folders, the
  folder browser, start, stop, pause, as for the owner, with everything said about it sealed.
- The list of files gained what this needs (stars, thumbnails, earlier versions, deleted entries),
  and all three clients (browser, DDrive Lite, phone app) understand it: a file deleted in the browser
  is deleted on a device that syncs it both ways, and can still be brought back from Deleted.
- Not there yet for these accounts, and not shown in their menu: Bookmarks, Messages, Contacts, Shared,
  Snapshots and Activity, saving from a web link, and a folder as one ZIP. They are next; some of them
  need one of your own devices to do what the server used to do.
- The phone app (2.8.0) and DDrive Lite understand the new list. Update them together with the server:
  an older app would drop the new details when it tidies the list.

## 2.7.0 (2026-10-11)

- **The phone app works with end-to-end encrypted accounts.** Connect it with such an account's 24
  words (it tries that first and sends no words, only the sign-in key made from them) and turn on
  folder syncing: the phone shows up under **Sync** on the account's page, where its folders are
  added and run. The phone seals every photo and file before it goes, opens what comes back, and
  seals what is said about the syncing (its name, its folders, how a pair is doing), exactly as a
  computer with DDrive Lite does since 2.6.0. All six modes work, including "Move here", which frees
  the phone only once the sealed copy is stored on Discord.
- The words are still kept on the phone encrypted with a key in Android's keystore; for an end-to-end
  account they are now used on the phone only.
- The phone's sealing is tested against DDrive's own client for these accounts: what one seals the
  other opens, both ways, and the keys they make from the same words are the same.
- The account's Sync page says how to connect a phone.

## 2.6.0 (2026-10-11)

- **A computer can sync its folders into an end-to-end encrypted account.** `lite connect` with the
  account's 24 words now recognises such an account (it tries that first and sends no words: only the
  sign-in key made from them), and `lite sync on` makes the computer show up on the account's page,
  under the new **Sync** tab. There you add its folders, with a folder browser that looks at that
  computer, and choose the mode and when it runs, start, stop, pause, change and remove them, and
  see how they are doing. All six modes work.
- The computer seals every file before it goes and opens what comes back, with the account's keys,
  which it has because the words were typed on it. The computer that runs the drive stores sealed
  files, as for the browser.
- **What is said about the syncing is sealed too**: the computer's name, its folders, which folder goes
  where, how a pair is doing, the folder lists the browser is shown. The computer running the drive
  passes these messages between the device and the account's page and can read none of them; it only
  knows that a device is there and whether it is connected. Nothing of it is written to its disk.
- "Move here" deletes a file from the device only after the computer running the drive has stored its
  sealed copy on Discord, as with ordinary accounts.
- With such an account `lite ls`, `lite get` and `lite put` list, download and upload (sealed on the
  way). A drive letter (`lite mount`) is not there for these accounts yet and says so.
- The phone app does not do this yet: it still needs an account of the earlier kind, or the owner's
  words. It is next.

## 2.5.0 (2026-10-11)

- **Accounts are end-to-end encrypted.** An account's 24 words are now made in the browser and never
  sent. The browser turns them into keys, encrypts every file and the list of files (names, folders,
  sizes, dates) with them, and only then sends anything. The computer that runs the drive gets a
  sign-in key that can't be turned back into the words, and after that only sealed things with
  meaningless names. It stores them on Discord as it stores any file, and can't read them: not while
  they are uploaded, not on its disk, not in its cache, not in its list of files.
- What the computer running the drive can still see of an account: that sealed things exist, about how
  big they are, when they come and go, and from which address. And one limit that every service that
  encrypts in a web page has: it serves the page that does the encrypting, so it is trusted to serve
  the real one.
- An end-to-end account opens a **files page of its own**: folders, upload (also by dropping files or
  whole folders), download, preview of pictures, text, audio and video, rename, move and delete.
  Files of any size are encrypted piece by piece and go up in parts; a download is decrypted in the
  browser (in Chrome and Edge straight onto the disk, so size doesn't matter). Several browsers of
  the same account see each other's changes.
- **Not there yet for these accounts**: the gallery and thumbnails, playing a big video without
  downloading it, share links, notes, bookmarks, contacts, messages, earlier versions, DDrive Lite and
  the phone app. Each needs the file in the clear on the server today and is being rebuilt to work on
  the device. They come in later versions.
- It needs an `https` address (or the computer itself). On plain `http` a browser doesn't encrypt, and
  the pages say so.
- **Signing in.** The sign-in page first tries a typed phrase as an end-to-end account, without sending
  it. Only if there is none does it offer "sign in the earlier way", which sends the words: that is how
  the owner signs in, and accounts made before this version. Those older accounts keep working as
  they did (the server holds their keys); new accounts are always end-to-end encrypted.
- Your **own drive** is unchanged: the computer running it has its key, as it must to show the drive as
  a folder and sync it. To keep your own files out of its reach, keep them in an account.
- The README and the privacy policy now say exactly this. "Zero-knowledge" used to appear without
  saying towards whom; it meant Discord.
- `ddrive/vault.py` is the description of the keys and formats and a client for them, which the tests
  use to check that the server ends up with nothing readable.

## 2.4.2 (2026-10-11)

- An opened part of a page no longer closes by itself. Pages that keep themselves up to date (Sync,
  Health, Resources) draw themselves again every few seconds, and each time closed whatever had been
  opened on them, such as a folder's notes on the Sync page. What was opened now stays open, scrolled
  to where it was.
- Such a page also waits with drawing itself again while text on it is selected, so something can be
  selected and copied without the selection disappearing.

## 2.4.1 (2026-10-11)

- The one upload card at the bottom right now says everything the cards per file used to: how many
  files are going to Discord, the file that is going right now with how far it is (in percent and in
  bytes, or that its spare pieces are being added), the speed, how many go at once and how many are
  waiting. Hovering over the file's name lists all the files going at that moment.
- The card can be made small with its **–** (a short line: how many files and the percentage; a click
  opens it again, and the browser remembers the choice) or closed with its **×** (it stays away until
  the uploads are over).
- A dashboard page that was open while DDrive was updated says so and offers **Reload**. Until it is
  loaded again it is still the old page (which is how the old cards, one per file, could still be
  seen after updating to 2.4.0).

## 2.4.0 (2026-10-11)

- **A folder sync on a phone or a lite computer no longer gets stuck on a big file.** It used to send
  each file as one request and wait a minute for the answer. A big video could not make that when
  the host sits behind a reverse proxy that stores the whole request first, or when the host had to
  wait for room on its own disk: the sync failed with a timeout, started again with the same file,
  and stayed at "0 of N files" for good. Files now go to the host **in parts of 8 MB**:
  - each part is a short request, so a proxy's limits and waiting times no longer matter;
  - a file that was cut off (the connection dropped, the phone went to sleep) **goes on where it
    stopped** instead of starting over;
  - a host that has to catch up first (its upload backlog or its disk is full) says so at once, and
    the folder's card on the Sync page shows it, e.g. "Waiting for the host: upload backlog is 2.0 GiB
    (limit 2.0 GiB)", and goes on by itself when there is room;
  - a file that can never fit on the host (bigger than what its disk may take, see
    `min_free_disk_bytes`) is reported as that file's problem, with the numbers, and the sync goes on
    with the next one;
  - the progress bar moves while a big file is being sent, not only when it is finished.
  The parts are collected on the host next to the drive's own files and become the file by being
  moved, so a big file is not on the host's disk twice. Needs the host and the phone app (or the lite
  computer) on 2.4.0; with an older host the new app sends files the old way.
- **The upload popups at the bottom right are one small card now.** There used to be a card per file
  going to Discord; during a big copy they came and went constantly and sat on top of buttons. Now one
  card says how many files are uploading and how far they are, it can be put away with its ×
  (it stays away until the uploads are over), and clicking it opens the page that lists them.
  Clicks next to the card go through to the page under it.

## 2.3.0 (2026-10-11)

- **DDrive Lite for Android.** A new app (`DDriveLite.apk` in each release, Android 8 or later) that
  makes a phone one of the computers on the host's **Sync** page. In the app you give the host's
  address and your 24 words and turn on folder syncing; the phone then shows up on the Sync page, and
  its folders are added and run from there like any other computer's, with the folder browser looking
  at the phone's storage (`DCIM`, `Pictures`, `Download`, a memory card).
- All six modes work on the phone. **Back up** copies new photos and files by themselves; **Move to the
  drive** also deletes each one from the phone once the host has it safely stored, which frees the
  phone; two-way and the download modes copy to the phone as well. The gallery is told about files
  that came or went.
- It runs in the background with a notification that says what it is doing, starts again with the
  phone, copies **only on Wi-Fi** unless you turn that off, and keeps the 24 words encrypted with a key
  in Android's keystore. A live folder is looked at once a minute and synced when something in it
  changed (and every 15 minutes anyway).
- Files a sync removes from the phone are moved to `DDrive Lite/Removed by sync` and kept 30 days.
  Android's half-written and gallery-deleted files are never copied.
- The app is in `android/`; its tests run the phone's syncing against a real DDrive host. It is signed
  with a key kept outside the repository, so a release only has the APK when that key is in the
  repository's secrets.
- New in this version and so far checked by its tests only, not yet by long use on phones: start with
  a folder you have another copy of.
- Tests: the recovery-phrase test no longer fails by chance about once in 256 runs (it swapped two
  words of a random phrase and expected that to be noticed, which an 8-bit check can't promise).

## 2.2.0 (2026-10-11)

- **DDrive Lite can sync folders, and you run it all from the dashboard.** On the lite computer you
  only turn it on: `lite sync on` (or **Folder syncing** in its menu). That computer then shows up on
  the host's dashboard, on the **Sync** page, under the host's own folders, with its name and whether
  it is connected. There you add a folder of it (with a folder browser that looks at the lite
  computer), pick the drive folder, the mode (back up, mirror, two-way, move, download, download
  mirror) and when it runs, and later change it, start it, stop it, pause it or remove it, and see
  its progress, its last result and its recent runs. Nothing else is done on the lite computer.
- The lite computer does the copying itself, with the same rules as folders synced by the host: copies
  keep each file's date so nothing is copied twice, files still being written wait, unfinished
  downloads are skipped, and nothing is deleted on one side because the other is suddenly empty.
  It needs no drive letter for it: syncing works on a lite computer without WinFsp or FUSE.
- It runs in the program that shows the host's drive (`lite mount`) and by itself when that isn't
  running; `lite autostart on` covers both after a restart. `lite sync off` stops it and takes the
  computer off the Sync page; `lite sync status` says how it is; `lite sync run` does it in a window.
- The lite computer keeps its list of folders (in its data folder) and tells the host when it
  connects, so it survives a restart of either. The host keeps another computer's folders in memory
  only: nothing about them is written to its disk.
- Turning it on lets whoever is signed in to the host's dashboard with the same 24 words choose any
  folder of that computer, which is why it is off until you turn it on there. A lite computer signed
  in with an **account**'s words syncs with that account's storage and is steered from that
  account's Sync page (accounts now have one, for their own lite computers only); the owner doesn't
  see it, and an account still can't touch the folders of the host.
- For this the dashboard's upload takes a file's own date, a copy that is cut off half-way is not left
  on the drive as a new file, and a whole folder tree can be asked for in one request.
- Fixed: the lite-only Debian package was missing a file it needs since 2.0.0 and did not start.

## 2.1.1 (2026-10-11)

- Health, **Check again**, now finishes the job with one click. It used to stop at the first part
  Discord was too busy to answer for, so it took several clicks to get through a long list. It now
  runs on the drive in the background, tries again what got no answer or failed to upload, and goes
  on until nothing more can be fixed. The button shows how far it is, you can leave the page
  meanwhile, and it says what it found when it is done.

## 2.1.0 (2026-10-11)

- **Accounts survive the loss of the computer that serves them.** Until now the list of accounts was
  only on that computer (`accounts.json`): if its disk died, nothing could find the accounts' file
  lists again, although everything they stored was still on Discord. A copy of the list is now kept
  in the channel: one pinned message per computer that serves accounts, encrypted with the drive's
  key, without the accounts' keys. It is written within a minute of an account being made, switched
  off or deleted and otherwise at most every ten minutes, and each copy replaces the one before.
- **Settings, Accounts: Bring accounts back.** A computer that has no accounts is shown the saved
  list, with the name of the computer that saved it and when, and brings it back when you say so.
  Each account then works again once it has signed in with its 24 words. Nothing is brought back by
  itself: a second device on the same drive sees the list too, and two computers must not serve the
  same accounts. What an account changed after the last saved copy (ten minutes at most) is not
  brought back.
- The checkpoint of an account's file list that the saved copy names is kept until a newer copy is in
  the channel, so the copy never points at a deleted message. At most one earlier checkpoint per
  account is kept for this.
- The privacy policy says that this copy exists. README: the Accounts section no longer asks you to
  back up `accounts.json` yourself and describes getting accounts back.

## 2.0.2 (2026-10-11)

- Health, "Parts that can't be repaired", has a **Check again** button: it asks Discord for every
  listed part right away instead of waiting for the background's turn. A part that can be read after
  all comes off the list, one this computer still holds is uploaded again, and it says how many of
  each and how many are still missing. **Clear the list** empties it by hand; a part that is still
  missing is listed again the next time its file is opened or checked.

## 2.0.1 (2026-10-11)

- Health no longer calls a file damaged that plays fine. A piece was listed as lost the first time
  Discord didn't hand it over, and stayed listed for good. Now a piece that looks gone is asked for
  once more before it is listed, and listed pieces are looked at again (after 5 minutes, then less
  and less often): one that can be read after all comes off the list.
- A piece that really is gone from Discord but is still in this computer's read cache (which is why
  the file plays here) is uploaded again from there, so the file is whole for every device. Until
  now only spare pieces were used to rebuild.

## 2.0.0 (2026-10-11)

- **The program is now called DDrive.** The name, the programs (`DDrive.exe`, `DDriveLite.exe`,
  `ddrive`, `ddrive-lite`), the module (`python -m ddrive`), the service file, the environment
  variables (`DDRIVE_...`) and the repository (github.com/xo907/ddrive) changed. DDrive is not
  affiliated with Discord Inc.; the README says so, and what using Discord for storage means.
- Nothing has to be moved after updating. A computer set up under the earlier name keeps its config
  and data folders where they are, its drive and everything on it stay readable, the old environment
  variables still work, and the start-with-the-computer entry and Explorer menu are renewed the next
  time the drive or the menu starts. Only a systemd unit or script of your own that names the old
  module needs changing by hand. The drive's label becomes "DDrive" unless you chose another.
- **Terms and privacy policy.** The dashboard serves `/terms` and `/privacy`, linked from the sign-in
  and sign-up pages, and making an account requires agreeing to them. They describe what the program
  really does with an account's data and name whoever runs the dashboard: fill in who that is, a
  contact address and the governing law under Settings, Accounts. They are a starting point, not
  legal advice.
- What an account does is no longer written to the owner's log (file names showed up there). Error
  messages are still logged.
- README: new screenshots, an Accounts section with the above, and a Legal section.
- Updating from the menu moves a copy that was downloaded from the earlier repository over to the new
  one by itself; nothing has to be downloaded again.

## 1.5.0 (2026-10-11)

- **Accounts.** Other people can use the dashboard with storage of their own. An account is 24 words
  that the sign-up page makes and shows once: no name, no email, no password, and no way to reset
  them. The words are the account's encryption key, so what it stores is unreadable for Discord, for
  other accounts and with the drive's own key. An account has files, gallery, stars, notes, bookmarks,
  contacts, messages, share links, locked folders, earlier versions, deleted files and snapshots, and
  none of the pages that manage the drive or the computer (Settings, Sync, Log, Health, Resources,
  checks, WebDAV); the drive refuses those requests for it.
- Settings has an **Accounts** section for the owner: who can make an account (**closed**, the
  default; **invite only**, with codes made there, each for a number of accounts and a number of
  days; or **public**), storage per account (5 GB unless changed), the most accounts allowed, and the
  list of accounts with what they store, to switch off or delete.
- **Signing in to the dashboard is by 24 words only.** User names and passwords are gone: the drive's
  recovery phrase signs the owner in (from the first visit on; `recovery-phrase` shows it), and
  passkeys still work for the owner. Browsers signed in with a password have to sign in once more.
  A drive without encryption gets 24 sign-in words of its own, shown by `recovery-phrase`.
- The password that remains is for other apps over WebDAV only (Settings, Sign-in, "Password for
  other apps", or `webdav-password`; `web-password` still works as a name for it). It never opens the
  dashboard, and changing it no longer signs browsers out.
- DDrive Lite signs in with 24 words too: `lite connect` asks for them. Run it once more on
  computers that were connected with a name and password. With an account's words it shows that
  account's storage.
- `unprotect --forgot` asks for the recovery phrase (it used to take the dashboard password as well).

## 1.4.6 (2026-10-11)

- Health: the "Bring back from a snapshot or an earlier version" bar is always shown above the
  damaged parts, also when it has nothing to offer, and then says why: the running drive is older
  than the page and needs a restart, no snapshot or earlier version contains the file, or its older
  copies have the same missing part. Each damaged file says which of these applies.

## 1.4.5 (2026-10-11)

- Health, "Parts that can't be repaired": damaged files that still have an intact older copy, in a
  snapshot or an earlier version, say so (with the copy's date) and can be brought back from it:
  tick the ones you want and "Bring back selected", or "Bring back all". The newest copy without a
  missing part is used; it can be older than the file was. The damaged file is kept as an earlier
  version, so it can be undone. Files with no intact copy say that instead.

## 1.4.4 (2026-10-11)

- Resources has a "Tidy up" section with buttons: clear the read cache (files kept offline stay),
  delete the thumbnails, stop all downloads from web addresses, and cancel the uploads that are
  waiting or stuck. The last one asks first: a file that was never uploaded is removed, a changed
  file goes back to its last uploaded version; uploads being sent at that moment are left alone.
  Until now cancelling uploads needed the drive to be stopped (`cancel-uploads`).

## 1.4.3 (2026-10-11)

- Resources is its own page in the menu, under Health (Drive): processor, memory, disk, the drive's
  own files against their limits, and what it is doing right now. It was a section at the bottom of
  Health, where it was easy to miss.
- When the running drive is older than the page (the files were updated but the drive wasn't
  restarted), the page now says so instead of showing nothing.

## 1.4.2 (2026-10-11)

- Health shows what the drive uses on the computer it runs on, under "This computer": processor
  (the drive's share, with the system load on Linux), memory (the drive's, and how much is free),
  free disk space, and how much of the disk the drive's own files take: read cache, files waiting to
  upload, the file list, thumbnails and the Messages search list, each next to its limit.
- Without the drive driver installed, file errors carry their error number again.

## 1.4.1 (2026-10-10)

- Windows: files that are already on the drive can be changed in place (appending to a file, a
  program that edits its file instead of writing a new one). Until now Windows allowed new files but
  answered "Access is denied" for these, because it took the files to be somebody else's. Restart the
  drive for this to take effect.
- Lite: `lite autostart on|off` shows the host's drive by itself when the computer starts; `lite menu`
  (and option 13 of the main menu) offers everything as a menu; and releases now also carry
  lite-only downloads, DDriveLite.exe and ddrive-lite_*.deb.

## 1.4.0 (2026-10-10)

- DDrive Lite: use a drive that runs on another computer, on Windows and Linux. `lite connect
  <address>` signs in to the host's dashboard (its user name and password), `lite mount` shows the
  host's drive as a drive letter or folder, `lite stop` takes it away; `lite ls`, `get` and `put` list,
  download and upload without mounting. The lite computer holds no bot, key, index or copy of the
  drive: files are streamed from the host a part at a time (video plays and seeks without being
  downloaded), and a saved file is handed to the host and then removed here.

## 1.3.7 (2026-10-10)

- Live logger: hiding the options is now done on the panel itself ("‹ Hide options" above "What to
  record"; "Options ›" next to "Live feed" brings it back), instead of a button at the top of the
  page with an icon that looked like a sun.

## 1.3.6 (2026-10-10)

- Messages: a new option, **Attachments only**, for the live logger and for an archive (each has its
  own switch). Only messages that have files are taken, the files are saved to the drive, and the
  text is not kept: no conversation, just the media. Messages without a file are left out. Text that
  was already saved before stays as it is.

## 1.3.5 (2026-10-10)

- Messages → Saved updates by itself: new, edited and deleted messages appear within a few seconds,
  with your search and filters kept.

## 1.3.4 (2026-10-10)

- Scroll bars are thin and blend in (in the menu, the lists and the pages); the ones inside a panel
  only show while the pointer is over it. The menu's first entries are no longer squeezed together
  when the menu is taller than the window.
- Live logger: the gear button at the top hides "What to record" and the picker, so the messages take
  the whole width. Remembered per browser.

## 1.3.3 (2026-10-10)

- Messages: attachments are shown and opened from Discord itself while they are still there. Previews
  of pictures and videos come from Discord, and opening one checks that Discord still has it and
  sends you there (links that ran out are renewed). When it is gone from Discord, the preview and the
  file come from the copy on your drive instead. The preview is now kept on the drive as well (in
  `files/previews/`), not on the computer. Attachments that were not copied to the drive open from
  Discord for as long as they exist.
- The menu takes less room: its sections (Tools, Share and sync, History, Drive) fold, and the button
  at the top left shrinks the whole menu to a strip of icons. Both are remembered per browser.

## 1.3.2 (2026-10-10)

- Messages are written to the drive within half a minute of being recorded, instead of every ten
  minutes.
- Pictures and videos in Messages are shown as a small preview (made by Discord when the attachment
  is saved) instead of loading the whole file, which would have put it in the read cache on this
  computer. The file itself is only fetched when you open it. Attachments saved before this version
  show as a file name.
- Joining a day's small log files into one no longer downloads each of them again.

## 1.3.1 (2026-10-10)

- Messages: the mark "not on the drive yet" now reads "text not on the drive yet" and says why. It
  is about the message's text, which is written to the drive every ten minutes (when archiving: when a
  channel is finished). Attachments are saved right away, so a message could carry the mark while
  the log already showed its file as uploaded.

## 1.3.0 (2026-10-10)

- Discord messages: a logger and an archiver for your own Discord account (brought over from Pretty
  Suite), in the dashboard under **Messages**. What they collect is kept in `/Discord` on the drive, so
  it is encrypted, on all your devices, and costs nothing.
  - **Log in with Discord** in Settings → Discord account, with your account's token (Discord's own
    login button can't give an app your messages). The token is kept with the other secrets, encrypted
    for this computer; it is never shown again and never leaves for anywhere but Discord.
  - **Archive**: pick servers, channels and conversations; everyone's messages or only yours or
    theirs, a date range, attachments or not. It shows its progress, can be stopped and carried on,
    carries on by itself after a restart, and archiving a conversation again only adds what is new.
  - **Live logger**: records messages as they arrive and keeps the original text of ones later edited
    or deleted. Everything you can see or only what you pick, keyword alerts, attachments saved as
    they are posted. It stays invisible, reconnects by itself, and starts again with the drive.
  - **Saved**: search and filter (deleted, edited, keyword hits, with files, yours, one conversation)
    and export what is shown as a web page, JSON, CSV or text.
  - On the drive the messages are plain `.jsonl` files (one record per line) next to the saved
    attachments. Every device reads them, so the Messages page works everywhere; logging and archiving
    run on the device you logged in on. `/Discord` can be password-locked: recording carries on, and
    the messages are hidden until you unlock it.
- These files stay out of Recent, Activity and the Gallery.

## 1.2.6 (2026-10-10)

- Fixed uploads of large files failing over and over with "Discord HTTP 100". When a piece was cut
  off midway (a dropped connection, or the computer running out of memory), the interim "100
  Continue" from Discord was taken for the final answer and the whole file was put back in the
  queue. Now only that piece is sent again, right away.
- When the computer is out of memory, the log says so ("this computer is out of memory") instead of
  "curl exit code 3221225773", "option -F: is badly used here", or an empty reason.

## 1.2.5 (2026-10-09)

- The log can be cleared: a Clear button in the dashboard's Log tab (next to Download), `log --clear`,
  and menu 9 → Clear the log. It empties both the list in the Log tab and the log file on that
  computer; other open Log tabs empty too.

## 1.2.4 (2026-10-09)

- Snapshots can be deleted for good from the dashboard: the menu on each snapshot (or right-click),
  "Delete snapshot" while looking inside one, and "Delete all" on the Snapshots page. Also in the
  terminal menu (7 → Delete a snapshot for good), and `snapshots delete all` joins `snapshots delete
  <n>`. Deleting applies on every device; your current files are not touched, and what only that
  snapshot still kept is removed from Discord.

## 1.2.3 (2026-10-09)

- When a part can't be repaired, the drive says which files are affected. The log names them once
  ("A part of /Photos/a.jpg can't be read and can't be rebuilt") instead of only a piece number, and
  tells a damaged file apart from a damaged earlier version, a damaged snapshot copy, and a lost
  spare piece (the file is then still fully readable, with less protection).
- Health in the dashboard and the `health` command list them under "Parts that can't be repaired",
  with how many parts and since when. The list follows renames and moves, and an entry goes away when
  the file is replaced, deleted for good, or the part turns out to be repairable after all.

## 1.2.2 (2026-10-09)

- The pictures and the list that Bookmarks keeps in /Bookmarks no longer show up in Recent, Activity
  and the Gallery (they are still ordinary files in Files, and the Gallery of /Bookmarks itself still
  shows them).
- A new front page for the project: what you get, screenshots of the dashboard, and how it compares.

## 1.2.1 (2026-10-09)

- Signing in with a passkey works: it failed with "Unexpected token '<' ... is not valid JSON". The
  first of its two requests left its (empty) body unread on the connection, which garbled the second.
  The dashboard now always clears a request's unread body before the next one, which also covers Sign
  out and "Lock again now" (they could make the following request fail in the same way).

## 1.2.0 (2026-10-09)

- Recovery phrase instead of an encryption password. A drive's key is shown as 24 ordinary words (the
  standard BIP39 way, with a checksum that catches a mistyped word). New drives get a random key and
  show its phrase once setup is done; `recovery-phrase` shows it again on a device that has the drive
  (menu 9), and `recovery-phrase --check` lets you test what you wrote down. Existing drives keep
  their key: the phrase is that same key written as words, nothing is re-encrypted.
- Setting up another device: let a device that has the drive approve it (as before), or type the 24
  words. Setup no longer asks for or accepts an encryption password; `setup -k` takes the phrase (or
  the key in hex), and so do `add-old-key` and `unprotect --forgot`.
- Passkeys for the dashboard: sign in with a fingerprint, face, PIN, phone or security key (Settings →
  Sign-in → Add a passkey). Only each passkey's public key is stored. A passkey works at the address
  it was made for, and browsers allow them only on https or on `http://localhost` (the dashboard's own
  links now use localhost instead of 127.0.0.1 for that reason).
- The recovery phrase also signs in to the dashboard, as the backup when a passkey is lost or where
  passkeys aren't possible. The phrase itself is only ever shown on the computer that runs the drive.
- The password is now optional: set, change or remove it under Settings → Sign-in, and switch off
  signing in with it. It remains the way in for other apps over WebDAV. The first visit on the
  computer running the drive asks only for a name.

## 1.1.1 (2026-10-09)

- Bookmarks can be pinned: the star on a card (or "Pin to the top" in its menu) puts it in a Pinned
  section above the others, the most recently pinned first. Pins sync to your other devices with the
  bookmarks, and the tag and filter boxes apply to pinned ones too.

## 1.1.0 (2026-10-09)

- Bookmarks: a new tab in the dashboard. Paste a link (in the box, or Ctrl+V anywhere on the page;
  several at once work too) and it is saved as a card with the page's title, description, site name,
  icon and banner picture, read the way link previews are (Open Graph / Twitter card tags). Pages
  without a banner get a coloured card with the site's icon.
- Tags, notes and your own titles: Edit on a card; click a tag to show only those; a filter box
  searches titles, descriptions, sites, notes and tags. "Read the preview again" refreshes a card and
  keeps your title.
- Bookmarks live on the drive in /Bookmarks (the list and the pictures), so they are encrypted, synced
  to every device and versioned, and looking at them loads nothing from the sites themselves. Locking
  /Bookmarks with a password hides them like any folder.
- A link to something on this computer or the home network is saved without a preview (nothing is
  fetched from there).

## 1.0.2 (2026-10-09)

- Folder sync, backup mode: each file is copied once. The job remembers what it has backed up, so a
  copy you move, rename, change or delete on the drive is no longer put back by the next pass (before,
  moving files out of a backup folder made them upload again). A file is copied again only when it
  changes on the computer. The job's status says how many files this applies to.
- "Copy everything again" (the job's menu in the dashboard, or `sync run <n> --again`) puts back whatever
  is missing in the drive folder. Mirror and two-way sync are unchanged: they keep both sides the same.

## 1.0.1 (2026-10-09)

- Tests: the request pacing tests no longer fail now and then on Windows. They measured real waits
  with a clock that only moves in 15.6 ms steps there, so a 75 ms wait could read as 62.5 ms; they now
  run on a pretend clock and check the exact waits, including the 4x limit and the return to normal
  after five minutes. Nothing changes in how the drive behaves.

## 1.0.0 (2026-10-09)

- Home: the dashboard opens on an overview with what is on the drive, how well it is protected, folder
  sync and shared links, your starred items, the most recent files and the latest activity.
- Starred: star any file or folder (right-click, or in its details). Stars sync to all your devices.
- Activity: what was added, changed, renamed, moved and deleted, when, and on which device. Recent: the
  files changed last, anywhere on the drive.
- Storage: space by kind of file, the biggest folders and files, and identical files with one click to
  keep the oldest and delete the copies. Folders show their size in Files.
- File requests: right-click a folder, "Request files". People with the link send files into that folder
  from a simple page, without seeing what is in it; names never overwrite. With an expiry, an optional
  password and a size limit; listed under Shared with the number of files received.
- Edit text files in the browser (notes, code, config files): Edit in the menu or the details, Ctrl+S
  saves, and the version before is kept.
- Save from a link: paste a web address and the drive downloads the file itself into a folder, with
  progress and Cancel. Addresses on this computer or the home network are refused.
- Find anything with Ctrl+K: files, pages and actions from one box. Paste a screenshot or copied files
  anywhere to upload them. Download a selection of files and folders as one ZIP.
- Install as an app: the dashboard can be added to a phone's home screen or installed from the browser
  on a computer, and opens in its own window.
- Other apps (WebDAV): the drive can be opened by file managers on phones, "Map network drive", Finder,
  rclone and backup apps at /dav/, with the dashboard's user name and password. Off until turned on in
  Settings; password-locked folders are never shown there.

## 0.12.0 (2026-10-09)

- The config file no longer holds its secrets readable. Bot tokens, encryption keys and the dashboard
  sign-in are stored encrypted for the computer and account they belong to: on Windows with the
  account's own protection (DPAPI), on Linux and Raspberry Pi with a key file kept apart from the
  config (<data dir>/machine.key, readable by you only) together with the machine's id. A copy of the
  file (a backup, a synced folder, a screenshot, a support request) gives nothing away.
- Automatic on every device: nothing to set up or type, and the drive still starts by itself. An
  existing config is converted the first time this version of the drive starts. Ordinary settings stay
  readable and can still be edited by hand; a token or key typed into the file is picked up and
  encrypted at the next start.
- Because a copy of the config file is no longer a copy of your key, keep your encryption password (or
  the key from `export-key`) somewhere outside the computer. A config moved to another computer or
  account says clearly that its secrets can't be read there and asks for `setup`.
- `config` shows how the secrets are kept. `config protect_config false` turns this off (the secrets
  are then written readable again).

## 0.11.0 (2026-10-09)

- Password-locked folders and files. A locked item, and everything in it, is gone from the drive
  letter and from the dashboard (files, search, gallery, deleted files, snapshots, shared links, the
  log and upload progress) on every device, until its password is typed. Not even its name is shown.
- Unlocking is by password alone: every lock with that password opens. In the dashboard (the padlock
  at the top) it opens in that browser only; "Also show on the drive" or the `unlock` command shows it
  on that device's drive letter. It locks again after 15 minutes without use (Settings, or
  `config lock_timeout_minutes`), on sign-out, with "Lock again now" / `relock`, and when the drive
  restarts.
- Lock: right-click a folder or file in the dashboard, "Lock with a password", or `lock <path>`
  (menu 7). Remove a lock with its password: "Remove the lock" or `unprotect <path>`; with a forgotten
  password, `unprotect <path> --forgot` asks for the dashboard's password instead.
- Wrong passwords are slowed down like wrong sign-ins. Browsers keep no copy of pictures and files from
  locked folders. Folder sync keeps copying into a locked folder.
- A lock is a gate, not a second encryption: locked files are encrypted with the drive's key like all
  others, so it keeps out people using your computer, drive letter or dashboard, not somebody who has
  your config file. Both devices need this version for a lock to apply on both.
- Each sign-in to the dashboard is now its own session.

## 0.10.0 (2026-10-09)

- Gallery: a new tab in the dashboard with every photo and video on the drive, newest first, month by
  month. Click one for a full-screen viewer (arrow keys or the buttons go to the next and previous;
  download and details from there). Any folder opens as a gallery too: the picture button in Files, or
  right-click a folder, "Open as gallery".
- Thumbnails for photos and videos (a frame of the video). They are made the first time a picture
  comes into view, and "Create all thumbnails" in the gallery makes them for everything at once, with
  progress and Stop. Photos uploaded through the dashboard get theirs straight away.
- Thumbnails are kept on the computer running the drive (<data dir>/thumbs), encrypted with the drive's
  key, renewed when a file changes and removed when it is deleted.
- Files can be shown as a grid with thumbnails (the grid button next to New folder); selecting,
  dragging and right-click work as in the list.

## 0.9.1 (2026-10-09)

- Sync: Windows folders that OneDrive (or Windows settings) moved elsewhere are recognised. Choosing
  the empty old place, e.g. C:\Users\you\Documents when Windows keeps it in
  C:\Users\you\OneDrive\Documents, is refused with the right folder named; pairs set up that way show
  a note saying where the files really are. "Add a folder" in the menu suggests the real Downloads.
- Sync: an empty folder now says so ("the folder is empty, so there is nothing to copy") instead of
  "everything was already in step"; that message also shows how many files were compared.
- Sync: folders OneDrive keeps in the cloud (Files On-Demand) are synced; only shortcuts to other
  places (junctions, symbolic links) are skipped.

## 0.9.0 (2026-10-09)

- Sync and back up folders: keep a folder on this computer in step with a folder on the drive, e.g.
  C:\Users\you\Downloads backed up to Z:\Downloads by itself. Six ways: back up (new and changed
  files; deleting here keeps them on the drive), mirror (an exact copy), two-way sync (a file changed
  on both sides is kept twice), move (deleted here once it is safely in Discord, to free space),
  download and download mirror (a local copy of a drive folder, e.g. on an external disk).
- Runs live (a few seconds after something changes; Windows reports changes at once), every N minutes,
  daily at a set time, or only when started. Copies keep the file's date so nothing is copied twice;
  files still being written and unfinished downloads (*.crdownload, *.part, ...) wait; more files or
  folders can be skipped.
- Safe by default: a missing folder (a disk that isn't connected) changes nothing, a side that is
  suddenly empty deletes nothing on the other, files a sync removes from this computer are kept aside
  for 30 days, and files deleted on the drive stay under Deleted.
- Dashboard: a new Sync tab with folder browsers for this computer and the drive, live progress,
  Sync now, Stop, Pause, recent runs and problems. Folders can only be chosen on the computer itself,
  unless it allows other devices.
- Terminal: menu 12, and `sync list | add | edit | remove | run | stop | pause | resume | status | modes`
  (e.g. `sync add C:\Users\you\Downloads Z:\Downloads --mode backup --when live`).

## 0.8.0 (2026-10-09)

- Share links work on every device: they are synced through the change journal like files. A link
  made on a PC at home can be opened through another device's dashboard, e.g. a Raspberry Pi that is
  reachable from the internet (set "Address for share links" on the PC to the Pi's address). Changing
  or turning off a link applies everywhere, and the view count adds up the views on all devices.
- Links made with earlier versions are published to the other devices on the first start.

## 0.7.0 (2026-10-09)

- Direct links for shared files (`/s/<id>/<file name>`): the file itself instead of a page. Post one on
  its own in Discord and the picture, GIF, video or song shows up with no link text. Shown in the
  share dialog and in Shared → "Copy direct link". Works for files in shared folders too
  (`/s/<id>/<path inside the folder>`). Not available for password-protected links.
- Share pages carry preview tags (Open Graph), so posting the page link shows the photo, video or
  song as well.

## 0.6.0 (2026-10-09)

- Encrypted contacts: a Contacts tab in the dashboard. All contacts live in one vCard file,
  /Contacts/Contacts.vcf, on the drive, so they are encrypted, synced to every device and every change
  keeps the previous version.
- Import from an iPhone, Android, iCloud, Google Contacts or Outlook: vCard (.vcf, versions 2.1, 3.0
  and 4.0) and Google / Outlook CSV. Duplicates are skipped. Instructions for each are in the app.
- Create and edit contacts: name, company, job title, any number of phone numbers, emails, addresses
  and websites, birthday, notes and a photo. Call, message and email buttons; search; letters;
  select several to export or delete; export all as one .vcf to load back into a phone.

## 0.5.0 (2026-10-09)

- Check files in the dashboard (Health → Check files, or right-click a folder → Check for problems):
  reads every file under a folder in the background with live progress, repairs what spare pieces
  can rebuild, and lists files that can't be read with the reason (another encryption key, gone from
  Discord, damaged). Select some or all and delete them for good.
- Clearer message when a piece can't be read: it no longer always says "missing from Discord" (it may
  have been encrypted with another key).

## 0.4.1 (2026-10-09)

- Talks to Discord gently, so it no longer runs into Discord's rate limits: every request is spaced out
  on our side (per bot, per minute: 30 uploads, 15 deletes, 90 other requests by default), and if
  Discord still says "slow down", the drive goes slower for a few minutes by itself.
- New settings (dashboard Settings → Discord, the menu, or `config`): `discord_pace` gentle (default),
  balanced, fast or off, and `uploads_per_minute`, `deletes_per_minute`, `requests_per_minute`.
- Rate limits are no longer logged one line each; a short summary appears now and then.
- Files whose old pieces can't be read are reported once instead of after every start.

## 0.4.0 (2026-10-09)

- Live progress for every upload: a bar with the percentage, speed and time left, for files sent from
  the browser and for the upload to Discord (also in the note editor). Progress refreshes every second
  while something is moving.
- New Log tab: what is uploading and downloading right now (with progress, speed and time left), what
  is waiting, and every log line as it happens, with filters, search, pause and download.
- Box selection in Files: press on empty space and drag to select several items. Every row also has a
  checkbox (on hover, or after choosing Select on a phone).
- Version numbers and this changelog; "What's new" in the dashboard's account menu.

## 0.3.0 (2026-10-09)

- Drag and drop in the dashboard: drag files and folders onto a folder, the path at the top or Files to
  move them, onto Deleted to delete them; Ctrl/Cmd-click and Shift-click select several; files dragged
  in from the computer upload into the folder they are dropped on; a folder browser for Move to.
- Notes tab: notes are files in /Notes on the drive. Typing saves at once and uploads when you pause;
  Save (Ctrl+S) uploads right away. Changes from other devices appear by themselves.
- Share links: choose how long a link works, an optional password, and view-only. The page shows the
  photo, video, music, PDF or text; whole folders can be shared. New Shared tab to manage links.
- Settings page in the dashboard; domain names and the share-link address in the menu too. Share links
  use your domain.
- Password setup falls back to PBKDF2 when there isn't enough memory for scrypt.

## 0.2.2 (2026-10-09)

- The dashboard works behind a reverse proxy (Nginx Proxy Manager): `config web_hosts <domain>`.

## 0.2.1 (2026-10-09)

- Dashboard sign-in with a user name and password (stored as a scrypt hash), sign out, and a lockout
  after repeated wrong passwords. Replaces the secret sign-in link.
- Right-click (and long-press on phones) menus for files, folders, empty space and snapshots; folders
  download as ZIP; Duplicate.
- Deleted: select several or all, restore, or delete forever on every device (`purge`).
- Fixed: restoring a file right after deleting it could be undone by the delete arriving later.
  Restoring a file whose folder was deleted re-creates the folder.

## 0.2.0 (2026-10-09)

- Self-healing: Reed-Solomon spare pieces, rebuilt on read, repaired everywhere, background check,
  older files protected in the background.
- Faster uploads (pieces in parallel, extra bots), compression and de-duplication.
- Web dashboard; snapshots; offline folders that stay in sync; hidden folders per device; scrypt for
  new passwords; tests on Windows and Linux; Windows .exe and .deb builds.

## 0.1.0

- The encrypted drive in a Discord channel: Windows and Linux, live sync between devices, versions and
  undelete, offline files, key sharing between devices, autostart.
