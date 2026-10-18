# FindX ↔ DDrive

What DDrive needs to do for FindX, and the formats both sides use. DDrive never needs to read anything FindX sends: it stores files and passes sealed messages along.

## Keys

Everything is made from the 24 words (the drive's recovery phrase or an account's), turned into 32 bytes as DDrive does (`words.phrase_to_key`, `Words.phraseToKey`):

```
key(job) = HKDF-SHA256(words key, salt = none, info = "findx v1 " + job), 32 bytes
jobs: report, history, names, command, beacon
group    = hex(HKDF-SHA256(words key, info = "findx v1 group"))[0:32]
```

`group` names your devices to the relay. It is the same for every device linked with the same words, and there's no way back from it to the words.

**Sealed** = `nonce (12) ‖ tag (16) ‖ AES-256-GCM ciphertext`, with a **label** as associated data (DDrive's `Seal.seal` layout). Labels: `findx report`, `findx card`, `findx command`, `findx history`, `findx share`, `findx uwb`. A blob only opens with the right key *and* label.

In the browser: `findx-core.js` (`FX.keys`, `FX.open`, `FX.seal`).

## Files (any DDrive, already working)

All under `/FindX`, through DDrive's own file API (an end-to-end account seals them again as usual):

| Path | What |
|---|---|
| `/FindX/d/<name("d\|" + device)>.fxd` | A device's **card**: sealed (`report` key, `findx card`) `{"d": device id, "n": name, "model", "uwb", "v"}` |
| `/FindX/h/<name("m\|" + device + "\|" + "YYYY-M", 16)>/<name("h\|" + device + "\|" + hour)>.fxh` | One **hour of history**: sealed (`history` key, `findx history`) gzip of `{"v":1,"d":device,"h":hour,"p":[[sec, lat×1e6, lon×1e6, acc dm, until sec or 0], …]}`. `hour` = ms since 1970 / 3 600 000; `YYYY-M` is the hour's UTC year and month (no leading zero). |

| `/FindX/s/<name("s\|" + device)>.fxs` | A device's **stats** (sent hourly and after a command from afar): sealed (`report` key, `findx stats`) `{"v":1, "d", "t", "profile": {the power profile used}, "days": [[day ms, use], …14], "hours": [[hour ms, use], …24], "log": [[t, text], …40], "shares": [{"id","key","name","until","approx","made"}], "spots": [names], "history": {…}}`. `use` is battery use part by part, as in the app's Battery use. |
| `/FindX/c/<name("c\|" + device)>.fxc` | A device's **circles**: sealed (`report` key, `findx circles`) list of the circles it is in, with each circle's id, key-derived seal and token (so the dashboard can show and ring their people), name, places, whether it may ring this phone and whether it is this account's own. |

`name(text, n=32)` = first *n* hex digits of HMAC-SHA256(`names` key, text). Nobody without the key can tell which device or which hour a file is.

To show history the dashboard lists `/FindX/d`, opens the cards to learn the device ids, then fetches the hour files it needs by name (a missing one is an hour with nothing). FindX re-sends an hour while it is still filling up; the newest copy wins. FindX never deletes these files.

## The live relay (to add to DDrive)

Signed-in routes (the dashboard session cookie; POSTs carry `X-DD: 1` as usual). Everything is scoped by **(account, group)**: one account can't see another's, even with the same group. It lives in memory and, as sealed as it came, in a file next to the drive's data (DDrive 3.10.1+: `findx-relay.json`, and circles with their members in `findx-circles.json`), so that a restart (an update) loses nothing: the dashboard shows the phones at once and a ring still reaches them.

| Route | Body / query | Answer |
|---|---|---|
| `POST /api/findx/hello` | `{group, device, card}` (card: base64 sealed card) | `{ok: true}` |
| `POST /api/findx/report` | `{group, device, blob, shares: {shareId: blob}}` (base64 sealed reports: `report` key / `findx report`; each share's with its own key / `findx share`) | `{ok, watching, shareWatching: [shareId…]}` |
| `GET /api/findx/wait` | `group, device, after, wait` (seconds, ≤ 240) | Held open until a command for `device` with `seq > after` arrives, or `wait` runs out: `{commands: [{seq, blob}], watching, hello}`. `hello: true`: the relay has no card for this device (an older DDrive that was restarted): the phone says hello again at once. When `after` is above the relay's last `seq`, `seq` goes on from `after`. |
| `POST /api/findx/command` | `{group, to, blob}` (sealed: `command` key / `findx command`) | `{ok, seq}`; wakes `to`'s wait |
| `GET /api/findx/devices` | `group` | `{devices: [{device, card, report, at}]}`: the latest of each (`report` may be missing) |
| `POST /api/findx/share` | `{group, device, share, until}` (`until` ms, 0 = until stopped) | `{ok}` |
| `POST /api/findx/unshare` | `{group, share}` | `{ok}` |

What to keep, per (account, group): each device's last card, last report blob and when it came; the last ~50 commands for 10 minutes, with a rising `seq`; each share's device, `until`, and the last blob for it from the device's reports. Limits that keep it small: blobs ≤ 16 KB, ≤ 20 devices per group, ≤ 50 live shares per device.

**Watching.** A device is *watched* for 60 s after the dashboard asks `/devices` for its group or someone opens its live map, and a share is watched for 60 s after its page asks for it. `report` and `wait` answers say so. That's what lets a phone send every few seconds only while someone is looking, and almost nothing otherwise.

**A host restart** loses only what's live: phones say `hello` again within minutes and re-announce their live shares; history is in files.

### Shared links (public)

| Route | What |
|---|---|
| `GET /f/<share>` | `share.html` from FindX's map assets (and the page's files under `/f/…`: `findx.css`, `findx-*.js`, `vendor/…`). No sign-in. |
| `GET /api/findx/shared?share=<id>` | `{blob, at, until}` for a live share; **404** for an unknown one, **410** for an ended one. No sign-in, no cookie. Marks the share watched. Rate-limit by address. |

The share's key is in the link after `#` (`https://host/f/<id>#<key, base64url>`); browsers never send that part, so the host only ever holds the sealed blob. A share whose `until` has passed is dropped (and the phone stops sealing for it).

Send `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex` with the share page, and a CSP that allows `connect-src` and `img-src` to `https://tiles.openfreemap.org` (and `worker-src blob:` for the map engine).

## Commands

Sealed JSON `{"c": what, "to": device, "from": device or "web", "t": ms, "id": random hex, "args": {…}}`:

| `c` | Does |
|---|---|
| `ring` | Rings (`args.ms`, ≤ 5 min), through silent and Do Not Disturb |
| `stop` | Stops ringing |
| `locate` | Pins itself down (up to 90 s of satellites), reporting as it tightens |
| `live` | Reports every few seconds while moving, for `args.ms` (30 s – 60 min) |
| `lost` | Lost mode with `args.message` on its lock screen |
| `found` | Ends lost mode |
| `beacon` | Bluetooth beacon fast for 10 minutes |

Managing from afar (the dashboard's Manage panel; `Remote.kt`). After each the phone reports and sends its stats and circles files again:

| `c` | Does |
|---|---|
| `set` | Changes any of `args.name`, `myName`, `history` (bool), `detail` (0–2), `beacon` (bool), `lostMessage` |
| `share` | Starts a live link: `args.id` (32 hex), `key` (base64url, 32 bytes), `name`, `until` (ms, 0 = until stopped), `approx` |
| `unshare` | Stops the live link `args.id` |
| `stats` | Sends the stats file now |
| `join` | Joins the circle in `args.invite` (an invitation link) |
| `leave` | Leaves circle `args.circle` |
| `circleRing` | Lets circle `args.circle` ring this phone, or not (`args.on`) |
| `circles` | Reads its circles' places again |

There is deliberately no command to turn FindX off, unlink the phone or erase it: those need the phone and its screen lock.

A phone ignores a command meant for another device, older than 10 minutes or more than 1 minute in the future, or one whose `id` it has already seen.

## Reports

Sealed JSON (`Report.toJson`):

```json
{"v":1, "d":"<device>", "n":"Name", "t":1797000000000,
 "fix":{"lat":44.6488123, "lon":-63.5752345, "acc":1.4, "t":1796999998000, "alt":30.1, "spd":0.0, "brg":220, "src":"settled", "settled":true},
 "still":1796990000000, "pose":"FaceDown", "lux":0.4, "covered":true,
 "spot":{"name":"Desk", "score":0.86}, "floor":0.0,
 "bat":64, "chg":true, "net":"wifi", "mode":"standby|moving|live|locating",
 "ring":true, "lost":true, "trail":[[lat, lon, t, acc], …]}
```

Reports to the owner's own dashboard also carry `use` (today's battery use), `cfg` (`Remote.settings`: name, myName, history, detail, beacon, lostMessage, on, v, model, android) and `ok` (what keeps it from being found: `location`, `background`, `locationOff`, `nearby`, `notifications`, `dnd`, `battery`, `fullScreen`, `beaconOff`).

A share's report (`Report.forShare`) leaves out spots, floor, trail, light and covered; a "town only" share also rounds the position to about a kilometre and drops the pose.

## The dashboard's map

`assets/map/` is the whole map: `index.html` (with `findx-core.js`, `findx-style.js`, `findx-map.js` and the MapLibre files). The dashboard provides `FX.source` (see `findx-source.js` for the shape) backed by the relay and the files above, opened with keys made from the account's words: an end-to-end account's browser has them already; for the owner or an older account the page asks for the words once and keeps the derived keys for the tab only.

## Circles (family and friends)

A circle is made on a drive by somebody signed in to it, and joined by anybody with its invitation:

```
https://<drive>/j/<circle id>#<circle key, 32 bytes, base64url>
seal = HKDF-SHA256(circle key, "findx circle v1 seal")
auth = hex(HKDF-SHA256(circle key, "findx circle v1 auth"))[0:32]     (the drive keeps sha256(auth) only)
```

The circle key is not made from anybody's words, so people from other drives (or none) can be in one
circle. Labels: `findx circle info` (`{"v":1,"name","places":[{"id","name","lat","lon","r"}]}`),
`findx circle report`, `findx circle command`. A phone is one random member id in all its circles.

| Route | Who | Body | Answer |
|---|---|---|---|
| `POST /api/findx/circle/create` | signed in | `{circle, auth, info}` | `{ok}` |
| `POST /api/findx/circle/delete` | signed in, its maker | `{circle}` | `{ok}` |
| `POST /api/findx/c/info` | the invited | `{circle, auth}` | `{info, members}` |
| `POST /api/findx/c/setinfo` | the invited | `{circle, auth, info}` | `{ok}` |
| `POST /api/findx/c/report` | the invited | `{circle, auth, member, blob}` | `{ok, watching}` |
| `POST /api/findx/c/members` | the invited | `{circle, auth, watch}` | `{members: [{member, blob, at}]}` |
| `POST /api/findx/c/command` | the invited | `{circle, auth, from, to: member or "*", blob}` | `{ok, seq}` |
| `POST /api/findx/c/wait` | the invited | `{member, wait, circles: [{circle, auth, after}]}` | `{commands: [{circle, seq, blob}], watching: [circle]}` |
| `POST /api/findx/c/leave` | the invited | `{circle, auth, member}` | `{ok}` |

A member's report: `{"v":1, "m", "p": their name, "n": device, "t", "fix", "bat", "chg", "mode",
"place": {"id","name"} or none, "since", "canRing", "ringing"}`. Commands: `ring`, `stop` (to one
member; a phone rings only if its owner lets that circle ring it) and `event` to `"*"` (everybody
but the sender) with `{"kind": "arrived" | "left", "place", "placeName"}`. A phone tells its
circles where it is when somebody has the circle's map open, when it has moved 50 m, when it arrives
or leaves a place, and otherwise every 15 minutes.

`/FindX/circles.fxc` (sealed: `report` key, `findx circles`) lists your circles for the dashboard:
`{"v":1, "circles": [{"id","key","host","name","member","myName"}]}`.
