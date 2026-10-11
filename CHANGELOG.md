# Changelog

Every change to DDrive gets a new version number and an entry here. The dashboard shows this
list under the account menu, "What's new".

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
