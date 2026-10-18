# FindX

Find your phone, down to the spot it's lying on. FindX works alongside SaveX and GalleryX: the same look, the same privacy, and your own DDrive as its only server.

## Install

1. Download [FindX.apk](https://github.com/xo907/ddrive/raw/main/findx/release/FindX.apk) on your phone and open it (allow installing from that source if Android asks). When a newer FindX is out, its home screen says so (your DDrive tells it).
2. Open FindX and go through setup. Allow each permission it asks for; each one says why it's needed. For **Location**, choose **Allow all the time**. For **Ring through Do Not Disturb**, find FindX in the list and switch it on.
3. Link your DDrive: its address (`https://…`) and your 24 words. Link every phone and tablet with the **same** words; that's how they know each other.

Requires Android 12 or newer. The APK is debug-signed, which is fine for your own phones.

## How precise it gets, and how

No single technology finds a phone to the centimetre everywhere, so FindX uses several in turn, from the whole map down to the table:

| From | How | How close |
|---|---|---|
| Anywhere | Satellites (GPS, Galileo and others) plus Wi-Fi and mobile network positions, from Android's own location system. **Pin it down** averages satellite fixes while the phone lies still, so the circle tightens as you watch; the error of fixes a few seconds apart is related, so it claims only what averaging honestly gains (never better than 1 m). | 1–5 m outdoors, 5–30 m indoors |
| Indoors | **Spots**: save the places it usually lies ("Desk", "Nightstand"). The phone recognises each by what it senses there: the Wi-Fi around it, the distance to Wi-Fi points that measure it (round-trip time, about 1 m), the magnetic field (steel in furniture bends it) and air pressure (which floor). | Which room, often which spot |
| How it lies | Gravity, light and proximity: face up or down, upright or propped, covered (a pocket, a cushion), in the dark, and still since when. | "Face down under something, on the desk" |
| Close by | **Nearby**: your devices hear each other's Bluetooth beacon. The signal strength is smoothed and turned into distance, warmer or colder, with a tick under your thumb that speeds up as you close in. | About a metre |
| Last metre | **Precise** (Ultra-Wideband, when both phones have it: recent Galaxy S Plus and Ultra, Pixel Pro and Fold): radio time-of-flight between the two phones gives distance and direction, an arrow on screen. | About 10 cm, a few degrees |
| By ear | **Ring**: loud chirps sweeping from 1 to 5 kHz with sharp starts. People place broadband sounds with sharp starts far better than a pure beep. The torch flashes and the phone buzzes against what it lies on. | You hear where it is |

### Ringing gets through everything

Ring plays as an alarm at full volume, so it gets through **silent and vibrate mode** and **volume turned down** (your volume comes back afterwards). With Do Not Disturb access (setup asks), it also gets through **Do Not Disturb, even "total silence"**: while it rings, Do Not Disturb is off and the ringer is on, and your exact settings come back the moment it stops. The screen lights up over the lock screen with one big **I found it** button. Picking the phone up turns the sound down. It stops by itself after 5 minutes.

## Battery

FindX is built to do next to nothing most of the time:

| When | What runs |
|---|---|
| Lying still (most of the day) | Nothing but the phone's **motion chip**, a separate low-power sensor that wakes the phone only when you carry it off. Positions other apps ask for (a map, the weather) are noted for free. |
| Moving | Positions at the pace **History detail** says (Settings): *Battery saver* (Wi-Fi and network only, every 90 s), *Balanced* (satellites every 8 s or so while moving, on foot too, plus a short fix where you stop), or *Detailed* (satellites every 3 s). When you stop, everything switches off again. Every position goes through a path filter first (`PathFilter` in Geo.kt): a Kalman filter that weighs each by its accuracy and leaves out the ones the path can't explain (a jump to a mobile mast, a reflection, a rough one among good ones, a stale one), so the path is where you went, not a scribble. The map cleans older history the same way when it draws it. |
| Someone is watching | Only while your map or a shared link is actually open does the phone send every few seconds, and only while it moves. A watched phone lying still pins itself down once and waits. |
| You ask | **Pin it down** runs satellites at full rate for up to 90 s. |

Waiting for "ring" is a single request your DDrive holds open for 4 minutes at a time: the radio sleeps in between and a command wakes the phone at once. History goes up in one batch at most every 20 minutes. The Bluetooth beacon costs about 1% a day.

### See exactly what it costs: Battery use

**This phone › Battery** (or Settings › Battery use) shows what FindX itself uses: today as a share of your battery and in mAh, today by the hour, the last 14 days stacked by part (satellites, network, processor, Bluetooth, sensors, ringing; tap a day), and what each part did in plain numbers ("on for 6 min", "38 radio wake-ups · 1.4 MB", "41 s of processing"). **Right now** puts what FindX draws next to what the whole phone draws, as the battery itself measures it.

- **Exact:** processing time and network bytes (the kernel's own counts for FindX), every time FindX woke the radio (a request while the radio is still awake from the last one doesn't count twice), and how long FindX had satellites, the Bluetooth beacon or scanning, sensors and ringing on.
- **Turned into battery** with your phone's own power profile, the figures its maker ships for what each part draws, and which Android's battery screen uses too. If a phone doesn't let apps read it, FindX uses typical figures and says so. Your battery's capacity comes from its own charge counter.
- Android's own per-app figure needs a permission only system apps have; the screen links to it to compare. The screen itself isn't in FindX's figures (Android counts screen time on its own), but FindX's time on screen is shown.
- Your other devices and the FindX page on your DDrive show each phone's "FindX used 0.4% battery today" too.

## Privacy

- **Sealed before it leaves the phone.** Every location report, every hour of history and every command is encrypted (AES-256-GCM) with keys made from your 24 words. Your DDrive, and Discord where DDrive keeps files, see random bytes. History files have names made from a keyed hash, so even *when* you were out isn't readable. With an end-to-end encrypted DDrive account, DDrive seals it all a second time.
- **Only your devices can ask.** "Ring", "locate" and "lost mode" are sealed with your command key and ignored if older than 10 minutes or seen before. Over Bluetooth, every command carries proof made with your key for a challenge the phone just made up, so a recorded command can't be replayed.
- **Nobody can follow your phone by its beacon.** Its Bluetooth name changes every 15 minutes, and so does the address Android advertises from. Without your key it's noise.
- **Shared links keep the key in the link.** It sits after the `#`, which browsers never send anywhere. DDrive serves the page and passes the report along without being able to read it. People you share with see this phone move, never its history, its spots or the names of your rooms. **Town only** links show about a kilometre.
- **No Google location SDK, no analytics, no ads, no accounts.** The internet is used only for your DDrive and the map's pictures (tiles from [OpenFreeMap](https://openfreemap.org), which keeps no logs and sets no cookies; the app's map uses no cookies at all). Viewing the map does tell the tile server which area you look at, as any online map does.
- **On the phone**, everything (your words, history, spots, links) is encrypted with a key in the phone's security hardware (StrongBox where there is one). Android's backup is off. Screenshots and the Recents preview are blocked unless you allow them in Settings.
- **Whoever has your phone can't silence it.** Turning FindX off, unlinking it, ending lost mode and erasing it all need your fingerprint, face or screen lock first.

## Circles: family and friends

Like Life360: the people in a circle see where each other are, live, with their battery, and are told when somebody arrives at or leaves one of the circle's places.

- **Make a circle** (People › +): it lives on your DDrive. **Invite** sends a link; whoever opens it on their phone joins with **Open in FindX**, or pastes it under People › Join. Friends can be on any DDrive, or none: only making a circle needs one.
- **Everybody, at a glance**: People lists each circle's people with a coloured face, where they are ("At Home since 3:12 PM", "2.4 km away"), how long ago and their battery. The map shows them all at once; tap a face to follow that person live. Somebody opening the circle's map is what makes the others send often, and only while it's open.
- **Ring a friend's phone** from People, the map, or the FindX page on your DDrive: it rings as loud as when you ring your own (through silent and Do Not Disturb). Each person decides per circle whether the circle may ring them (People › the circle › *Let them ring this phone*).
- **Places**: add where you are as a place of the circle (Home, School, Work; 100, 200 or 500 m). When somebody arrives or leaves, everybody else gets a notification. It is worked out on each phone from the positions it already has, so it costs no extra battery, and only the arrival or leaving is shared.
- **Private**: each circle has a key of its own, in the invitation after the `#` (not your words). Everything is sealed with it, so the DDrive in between can't read names, places or where anybody is; it sees random member ids and sealed bytes. People in a circle never see your history, your other devices or your rooms' spots, only where this phone is now. **Leave** any time; the one who made a circle can also delete it for everybody.

## Appearance

Settings › Appearance: like the phone, light or dark. The map has its own setting, the sun button on it: it follows day and night where the phone is (and fades through dusk and dawn), follows this device's light or dark setting, or is always light or always dark. On DDrive the FindX tab and shared links follow the viewer's device unless switched.

## History, kept for good

Every point goes into the phone's journal first. Then each hour goes to your DDrive as one sealed file (`/FindX/h/…/….fxh`), sent again as more of that hour comes in. A journal hour is only let go of after DDrive has all of it, and even then the phone keeps 45 days to show offline. FindX never deletes anything from DDrive. History is compact: a night on the nightstand is a single "stay" entry, not thousands of points.

## The map

The same page runs in the app, on your DDrive's dashboard and behind a shared link. It's a soft, quiet map in the spirit of Apple Maps, drawn with [MapLibre](https://maplibre.org) from OpenStreetMap data, with 3D buildings when you zoom in.

- **Day and night follow the sun where the phone is.** The map fades to night at dusk (with a warm glow through twilight), and the 3D buildings are lit from where the sun really is.
- **Live movement**: the dot glides between positions inside a circle as wide as the position is uncertain, with a cone for the direction it's heading.
- **History**: Today, Yesterday, 7 or 30 days. The trail fades into the past, places it lay still are dots (tap for how long), and a slider replays the day.

## Using it

- **This phone**: where FindX thinks it is and how sure, **Pin it down**, **Test ring**, the map, how it lies, and what it costs in battery.
- **Devices**: your other phones and tablets linked with the same words. Open one to **Ring** it, **Pin down**, watch it **live**, **Find nearby**, turn on **Lost mode** (your message on its lock screen; it reports more often and its beacon calls out) or get **Directions**.
- **Nearby**: your devices within Bluetooth range, nearest first. Open one for warmer and colder, **Ring** it over Bluetooth (no internet needed), or **Precise** with Ultra-Wideband.
- **Share**: a live-location link for 15 minutes, 1, 8 or 24 hours, 7 days, or **until you stop it**, exact or town only. Send it again, copy it, or stop it any time.
- **Settings › Spots**: lay the phone where it usually lies and save the spot. Save it again now and then; Wi-Fi changes.
- **Battery low**: at the low-battery warning the phone sends one last precise position and all its history.

## With DDrive

FindX works with any DDrive for **history** (sealed files in `/FindX`) and for **finding nearby** (which needs no internet at all). Seeing your devices **live**, ringing them from afar and **shared links** need **DDrive 3.5.0 or later**, which has FindX's relay and its **FindX** tab (in the dashboard's menu, or `https://your-drive/#/findx`): your phones on the same map, live, with their history. With **DDrive 3.7.0 or later** that tab also manages a phone from afar (its settings, battery use, live links, activity and circles) and has a **People** view for your circles. With an older DDrive, FindX says "history only" and checks again every few hours. [PROTOCOL.md](PROTOCOL.md) describes what passes between them; DDrive never reads any of it.

## Limits

- **Satellites can't see indoors.** Inside, the satellite circle is 5–30 m. Spots, how it lies, Nearby and Precise are what narrow it down there.
- **Precise needs Ultra-Wideband on both phones.** Without it, Nearby's Bluetooth gets you to about a metre, and Ring does the rest.
- **A phone that's switched off, out of battery or in airplane mode can't answer.** Its last report and history show where it was; the low-battery report is there for that.
- **A wiped or reset phone** loses FindX. That's Android's own anti-theft (Factory Reset Protection) territory.

## Build

FindX lives in DDrive's repository, in `findx/`. `powershell -ExecutionPolicy Bypass -File build-apk.ps1` runs the unit tests and builds the shrunk release, `release/FindX.apk` with its version in `release/VERSION`, using the toolchain in `E:\SaveX-toolchain` (FindX needs the Android 36 SDK platform there, for Ultra-Wideband). It is signed with the build computer's Android debug key (`~/.android/debug.keystore`), the key FindX has always been signed with: an update only installs over the app signed with the same key, so that file is kept safe (`FINDX_KEYSTORE` and friends point the build at another). After changing the map, `python tools/sync_ddrive.py` copies it into DDrive's dashboard.

| File | Job |
|---|---|
| `Geo.kt` | The arithmetic: distances, averaging a still phone (`Settle`), Bluetooth signal smoothing, pose, spot matching, the sun |
| `Secrets.kt` | Keys from your words, sealing, the beacon's changing name, command proofs |
| `Model.kt` | Reports, commands, history segments and their names |
| `Store.kt`, `History.kt` | What's kept on the phone (sealed with the hardware key), and history to DDrive |
| `Locator.kt`, `Sensors.kt`, `Tracker.kt` | Positions, what the phone senses, and how hard to look when |
| `Service.kt` | FindX in the background: commands, reports, history uploads, battery-low |
| `Ring.kt`, `RingActivity.kt` | Ringing through silent and Do Not Disturb |
| `Beacon.kt`, `Finder.kt`, `Precision.kt` | Bluetooth beacon and finder, Ultra-Wideband |
| `Link.kt` | DDrive: files, and the live relay |
| `MapScreen.kt`, `assets/map/` | The map (shared with DDrive's dashboard and shared links) |
| `*Screen*.kt`, `MainActivity.kt` | The app |
| `st/xo/ddrive/core/` | DDrive's Android client (sign-in, end-to-end accounts), copied from the DDrive repository |

Map: MapLibre GL JS 5 (BSD-3-Clause, `assets/map/vendor/LICENSE.txt`). Map data © OpenStreetMap contributors, tiles by OpenFreeMap.
