"""The status bot: a Discord bot that watches a drive and says what is going on.

It reads the drive's /api/monitor (with the monitor key) every few seconds and
  - shows a status that changes every ~20 seconds ("Uploading 20/100 files", "Uptime: 25 hours",
    "500 GB of files on Discord", "Experiencing I/O failures"...), with a green, yellow or red dot;
  - keeps one message in a channel up to date with the whole picture (optional);
  - posts there when something happens: problems start and end, uploads finish, the drive restarts,
    goes offline or comes back, milestones (optional);
  - answers /status, /statuses and /uptime.

It needs nothing but Python and the drive's address, so it can run on another computer than the drive,
and then it also says when the drive stops answering. Settings: `statusbot setup` (statusbot.json next
to the config file; the bot token and the monitor key are kept encrypted like the config's secrets).
"""

import json
import logging
import os
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

from . import __version__, ws
from . import statuslines as sl
from .config import config_path
from .discord_api import DiscordAPI, DiscordError

log = logging.getLogger("ddrive.statusbot")

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
SECRETS = ("token", "key")
DEFAULTS = {"url": "", "key": "", "token": "", "channel": "", "mention": "", "name": "DDrive",
            "poll": 15, "rotate": 20, "board": True, "alerts": True, "trivia": True}
COLORS = {"online": 0x3BA55D, "idle": 0xFAA61A, "dnd": 0xED4245}
COMMANDS = [
    {"name": "status", "description": "How the drive is doing right now", "type": 1},
    {"name": "statuses", "description": "Every status the bot has for the drive right now", "type": 1},
    {"name": "uptime", "description": "How long the drive has been running", "type": 1},
]


def settings_path():
    return os.path.join(os.path.dirname(config_path()), "statusbot.json")


def state_path():
    return os.path.join(os.path.dirname(config_path()), "statusbot-state.json")


def load_settings(path=None):
    path = path or settings_path()
    out = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {}
    if raw.get("protected"):
        from . import secretbox
        out.update(json.loads(secretbox.unprotect(raw["protected"])))
    out.update({k: v for k, v in raw.items() if k != "protected" and (k not in SECRETS or v)})
    for k in ("DDRIVE_STATUSBOT_TOKEN", "DDRIVE_MONITOR_KEY", "DDRIVE_MONITOR_URL"):
        if os.environ.get(k):
            out[{"DDRIVE_STATUSBOT_TOKEN": "token", "DDRIVE_MONITOR_KEY": "key", "DDRIVE_MONITOR_URL": "url"}[k]] = os.environ[k]
    return out


def save_settings(s, path=None):
    path = path or settings_path()
    data = {k: v for k, v in s.items() if k not in SECRETS}
    secrets_ = {k: s[k] for k in SECRETS if s.get(k)}
    try:
        from . import secretbox
        data["protected"] = secretbox.protect(json.dumps(secrets_).encode("utf-8"))
    except Exception:                         # no way to protect them here: kept readable, by this user only
        data.update(secrets_)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ------------------------------------------------------------------ reading the drive
class DriveDown(Exception):
    pass


def fetch(url, key, timeout=15):
    req = urllib.request.Request(url.rstrip("/") + "/api/monitor",
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": f"DDrive-statusbot/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise DriveDown("the monitor key is wrong (monitor-key on the drive shows it)")
        if e.code in (502, 503, 504):
            raise DriveDown(f"the drive's proxy says {e.code}: the drive is probably stopped")
        raise DriveDown(f"the drive answered {e.code}")
    except (urllib.error.URLError, OSError) as e:
        why = getattr(e, "reason", e)
        raise DriveDown("no connection" if isinstance(why, ConnectionRefusedError) else f"can't reach it ({why})")
    except ValueError:
        raise DriveDown("the answer isn't a drive's (wrong address?)")


# ------------------------------------------------------------------ the embed
def board(snap, statuses, hist, name, down=False, now=None):
    """The message kept up to date in the channel."""
    now = now or time.time()
    dot = sl.mood(statuses, down)
    title = {"online": "🟢 Healthy", "idle": "🟡 Needs a look", "dnd": "🔴 Trouble"}[dot]
    if down:
        title = "🔴 Offline"
    problems = [s.text for s in statuses if s.level <= sl.WARNING]
    happening = [s.text for s in statuses if s.level == sl.ACTIVITY]
    fields = []
    if problems:
        fields.append({"name": "Problems", "value": "\n".join(problems[:10])[:1024], "inline": False})
    if happening:
        fields.append({"name": "Going on", "value": "\n".join(happening[:10])[:1024], "inline": False})
    if snap and not down:
        d, up, h = snap.get("drive") or {}, snap.get("uploads") or {}, snap.get("health") or {}
        tot = sl.totals(snap)
        e = snap.get("errors") or {}
        res = snap.get("resources") or {}
        disk = res.get("disk") or {}
        fields += [
            {"name": "Drive", "value": f"{sl.count(tot.get('files'), 'file')}\n{sl.size(tot.get('bytes'))} on Discord\n"
                                       f"{sl.count(d.get('versions'), 'version')}, {sl.count(d.get('snapshots'), 'snapshot')}",
             "inline": True},
            {"name": "Uploads", "value": f"{sl.count(up.get('active'), 'active')}, {sl.count(up.get('queued'))} queued\n"
                                         f"{sl.count(up.get('done'))} done since start\n{sl.count(up.get('failed'))} failed tries",
             "inline": True},
            {"name": "Self-healing", "value": f"{sl.count(h.get('lost'), 'lost part')}\n{sl.count(h.get('rebuilt'))} rebuilt\n"
                                              f"{sl.count(h.get('channels'), 'channel')}, {sl.count(h.get('bots'), 'bot')}",
             "inline": True},
            {"name": "Errors", "value": f"{(e.get('5m') or {}).get('errors', 0)} in 5m\n{(e.get('1h') or {}).get('errors', 0)} in 1h\n"
                                        f"{(e.get('24h') or {}).get('errors', 0)} in 24h", "inline": True},
            {"name": "Computer", "value": f"Up {sl.duration(snap.get('uptime'), short=True)}\nCPU {res.get('cpu', 0):.0f}%\n"
                                          + (f"{sl.size(disk.get('free'))} disk free" if disk else "disk: ?"), "inline": True},
            {"name": "Watched", "value": f"{sl.pct(hist.span(7, now)['up'], max(1, sum(hist.span(7, now)[k] for k in ('up', 'down')))):.2f}% online (7d)\n"
                                         f"{sl.count(hist.span(1, now)['files'], 'file')} today\n"
                                         f"{sl.count(hist.lifetime.get('restarts'), 'restart')}", "inline": True},
        ]
    elif down:
        fields.append({"name": "Last answer", "value": f"<t:{int(hist.last_seen)}:R>" if hist.last_seen else "never",
                       "inline": True})
    return {
        "title": f"{name}: {title}",
        "color": COLORS[dot],
        "fields": fields[:25],
        "footer": {"text": f"DDrive {snap.get('version') if snap else '?'} · status bot {__version__}"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }


# ------------------------------------------------------------------ the bot
class StatusBot:
    def __init__(self, settings, api=None, connect=None):
        self.s = settings
        self.api = api or DiscordAPI(settings["token"], timeout=20, retries=2)
        self._connect = connect or (lambda url: ws.WebSocket(url))
        self.hist = sl.History(self._load_state().get("history"))
        self.board_id = self._load_state().get("board")
        self.rotation = sl.Rotation()
        self.snap = None
        self.down = False
        self.statuses = []
        self.shown = None
        self._stop = threading.Event()
        self._ws = None
        self._ws_lock = threading.Lock()
        self.app_id = None
        self.session_id = self.resume_url = self.seq = None
        self._board_at = 0
        self._board_text = None

    # state between runs
    def _load_state(self):
        try:
            with open(state_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        try:
            tmp = state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": self.hist.to_json(), "board": self.board_id}, f)
            os.replace(tmp, state_path())
        except OSError as e:
            log.debug("statusbot state not saved: %s", e)

    # ---------------------------------------------------------- running
    def run(self):
        threading.Thread(target=self._gateway_loop, name="StatusGateway", daemon=True).start()
        threading.Thread(target=self._rotate_loop, name="StatusRotate", daemon=True).start()
        self._poll_loop()

    def stop(self):
        self._stop.set()
        sock = self._ws
        if sock is not None:
            try:
                sock.close(1000)
            except Exception:
                pass

    def poll_once(self, now=None):
        now = now or time.time()
        try:
            snap = fetch(self.s["url"], self.s["key"])
        except DriveDown as e:
            events = self.hist.missed(str(e), now)
            self.down, self.snap = True, None
            self.statuses = sl.offline(self.hist, now)
        else:
            events = self.hist.observe(snap, now)
            self.down, self.snap = False, snap
            self.statuses = sl.build(snap, self.hist, now)
            if not self.s.get("trivia", True):
                self.statuses = [x for x in self.statuses if x.level != sl.TRIVIA]
        self._save_state()
        return events

    def _poll_loop(self):
        first = True
        while not self._stop.is_set():
            try:
                events = self.poll_once()
                if first:
                    first = False
                    self._show()            # something to see right away, not after the first rotation
                    log.info("Watching %s: %s", self.s["url"], "offline" if self.down else
                             f"{len(self.statuses)} statuses")
                for level, text in events:
                    log.info("Event: %s", text)
                    self._alert(level, text)
                self._update_board()
            except Exception as e:
                log.warning("Status bot: %s", e, exc_info=True)
            self._stop.wait(max(5, float(self.s.get("poll") or 15)))

    def _rotate_loop(self):
        while not self._stop.wait(max(12, float(self.s.get("rotate") or 20))):
            self._show()

    def _show(self):
        st = self.rotation.next(self.statuses)
        if st is None:
            return
        self.shown = st
        self._presence(st.text, sl.mood(self.statuses, self.down))

    # ---------------------------------------------------------- channel
    def _alert(self, level, text):
        if not (self.s.get("channel") and self.s.get("alerts", True)):
            return
        if level == sl.TRIVIA and not self.s.get("trivia", True):
            return
        content = text
        mention = self.s.get("mention") or ""
        allowed = {"parse": []}
        if level == sl.CRITICAL and mention:
            content = f"{mention} {text}"
            ids = "".join(c for c in mention if c.isdigit())
            allowed = {"roles": [ids]} if "&" in mention else {"users": [ids]}
        try:
            self.api.request("POST", f"/channels/{self.s['channel']}/messages",
                             {"content": content[:2000], "allowed_mentions": allowed})
        except Exception as e:
            log.warning("Couldn't post to the status channel: %s", e)

    def _update_board(self, force=False):
        if not (self.s.get("channel") and self.s.get("board", True)):
            return
        now = time.time()
        embed = board(self.snap, self.statuses, self.hist, self.s.get("name") or "DDrive", self.down, now)
        text = json.dumps({k: v for k, v in embed.items() if k != "timestamp"}, sort_keys=True)
        if not force and text == self._board_text and now - self._board_at < 300:
            return                                   # nothing new: edited at most every 5 minutes then
        if not force and now - self._board_at < 30:
            return                                   # at most every half minute
        chan = self.s["channel"]
        try:
            if self.board_id:
                try:
                    self.api.request("PATCH", f"/channels/{chan}/messages/{self.board_id}", {"embeds": [embed]})
                except DiscordError as e:
                    if e.status != 404:
                        raise
                    self.board_id = None
            if not self.board_id:
                m = self.api.request("POST", f"/channels/{chan}/messages", {"embeds": [embed], "allowed_mentions": {"parse": []}})
                self.board_id = m["id"]
                self._save_state()
            self._board_at, self._board_text = now, text
        except Exception as e:
            log.warning("Couldn't update the status message: %s", e)

    # ---------------------------------------------------------- gateway
    def _send(self, op, d):
        sock = self._ws
        if sock is not None:
            sock.send(json.dumps({"op": op, "d": d}, separators=(",", ":")))

    def _presence_payload(self, text, status):
        return {"since": None, "afk": False, "status": status,
                "activities": [{"type": 4, "name": "Custom Status", "state": text}]}

    def _presence(self, text, status):
        self._last_presence = (text, status)
        try:
            self._send(3, self._presence_payload(text, status))
        except Exception as e:
            log.debug("presence: %s", e)

    def _gateway_loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            code = 1006
            try:
                self._listen()
                backoff = 1.0
            except ws.Closed as e:
                code = e.code
            except Exception as e:
                log.debug("gateway: %s", e)
            if self._stop.is_set():
                return
            if code in (4004, 4010, 4011, 4012, 4013, 4014):
                log.error("Discord closed the bot's connection for good (%s): check the bot token.", code)
                self._stop.set()
                return
            if code in (4007, 4009):
                self.session_id = None
            self._stop.wait(backoff + random.random())
            backoff = min(backoff * 2, 60.0)

    def _listen(self):
        resuming = bool(self.session_id and self.resume_url)
        url = (self.resume_url.rstrip("/") + "/?v=10&encoding=json") if resuming else GATEWAY
        sock = self._ws = self._connect(url)
        interval, beat_at, acked = None, None, True
        try:
            while not self._stop.is_set():
                wait = 60.0 if beat_at is None else max(0.0, beat_at - time.monotonic())
                text = sock.recv(wait) if wait > 0 else None
                if text is None:
                    if beat_at is None:
                        raise ws.Closed(1006, "no hello from Discord")
                    if time.monotonic() < beat_at:
                        continue
                    if not acked:
                        raise ws.Closed(4000, "Discord stopped answering")
                    acked = False
                    self._send(1, self.seq)
                    beat_at = time.monotonic() + interval
                    continue
                packet = json.loads(text)
                op, d = packet.get("op"), packet.get("d")
                if packet.get("s"):
                    self.seq = packet["s"]
                if op == 10:
                    interval = d["heartbeat_interval"] / 1000.0
                    beat_at = time.monotonic() + interval * random.random()
                    if resuming:
                        self._send(6, {"token": self.api.token, "session_id": self.session_id, "seq": self.seq})
                    else:
                        shown = getattr(self, "_last_presence", None) or ("👀 Starting to watch the drive", "idle")
                        self._send(2, {"token": self.api.token, "intents": 0,
                                       "properties": {"os": sys.platform, "browser": "ddrive-statusbot", "device": "ddrive-statusbot"},
                                       "presence": self._presence_payload(*shown)})
                elif op == 11:
                    acked = True
                elif op == 1:
                    self._send(1, self.seq)
                elif op == 7:
                    raise ws.Closed(4000, "Discord asked to reconnect")
                elif op == 9:
                    if not d:
                        self.session_id = None
                    raise ws.Closed(4000, "session ended")
                elif op == 0:
                    self._dispatch(packet.get("t"), d)
        finally:
            self._ws = None
            try:
                sock.close(1000)
            except Exception:
                pass

    def _dispatch(self, t, d):
        if t == "READY":
            self.session_id = d.get("session_id")
            self.resume_url = d.get("resume_gateway_url")
            self.app_id = (d.get("application") or {}).get("id")
            log.info("Status bot signed in as %s", (d.get("user") or {}).get("username"))
            threading.Thread(target=self._register, name="StatusCommands", daemon=True).start()
            if getattr(self, "_last_presence", None):
                self._presence(*self._last_presence)
        elif t == "INTERACTION_CREATE" and d.get("type") == 2:
            threading.Thread(target=self._answer, args=(d,), name="StatusAnswer", daemon=True).start()

    def _register(self):
        if not self.app_id:
            return
        try:
            self.api.request("PUT", f"/applications/{self.app_id}/commands", COMMANDS)
        except Exception as e:
            log.warning("Couldn't set up the bot's slash commands: %s", e)

    def answer_for(self, name):
        """What /status, /statuses and /uptime answer."""
        nm = self.s.get("name") or "DDrive"
        if name == "statuses":
            lines = [s.text for s in self.statuses] or ["Nothing yet: the first look at the drive hasn't come back."]
            text = "\n".join(lines)
            return {"embeds": [{"title": f"{nm}: {len(lines)} statuses", "description": text[:4000],
                                "color": COLORS[sl.mood(self.statuses, self.down)]}]}
        if name == "uptime":
            if self.down:
                return {"content": f"🔴 {nm} is offline (for {sl.duration(time.time() - (self.hist.down_since or time.time()))})."}
            up = (self.snap or {}).get("uptime") or 0
            return {"content": f"⏱️ {nm} has been up for {sl.duration(up)} (since <t:{int(time.time() - up)}:f>)."}
        return {"embeds": [board(self.snap, self.statuses, self.hist, nm, self.down)]}

    def _answer(self, d):
        name = (d.get("data") or {}).get("name")
        body = self.answer_for(name)
        body["allowed_mentions"] = {"parse": []}
        try:
            self.api.request("POST", f"/interactions/{d['id']}/{d['token']}/callback", {"type": 4, "data": body})
        except Exception as e:
            log.warning("Couldn't answer /%s: %s", name, e)


# ------------------------------------------------------------------ command line
def _ask(prompt, current="", secret=False):
    import getpass
    shown = (" [kept]" if current else "") if secret else (f" [{current}]" if current else "")
    got = (getpass.getpass if secret else input)(f"{prompt}{shown}: ").strip()
    return got or current


def setup(path=None):
    """Ask for what the bot needs; on the drive's own computer the address and the key are found by themselves."""
    s = load_settings(path)
    local_key = ""
    try:
        from .config import Config
        cfg = Config.load()
        if cfg.channel_id or cfg.bot_token:          # this is a drive's computer
            if not cfg.monitor_key:
                import secrets as _s
                cfg.monitor_key = _s.token_urlsafe(32)
                cfg.save()
                print("[OK] Made a monitor key for this drive (restart the drive if it is running).")
            local_key = cfg.monitor_key
            s["url"] = s["url"] or f"http://127.0.0.1:{cfg.web_port}"
    except Exception:
        pass
    print("The status bot is a Discord bot of its own (not the drive's bot): make one at")
    print("https://discord.com/developers/applications -> New Application -> Bot -> Reset Token, then invite it")
    print("(OAuth2 -> URL Generator: scopes 'bot' and 'applications.commands'; permissions: View Channel, Send")
    print("Messages, Embed Links) to the server that should see the drive's status.\n")
    try:
        s["token"] = _ask("Status bot's token", s.get("token"), secret=True)
        s["url"] = _ask("The drive's address (its dashboard, e.g. https://drive.example.com)", s.get("url"))
        if local_key and s["url"].startswith(("http://127.0.0.1", "http://localhost")):
            s["key"] = local_key
            print("Monitor key: taken from this drive.")
        else:
            s["key"] = _ask("Monitor key ('monitor-key' on the drive's computer shows it)", s.get("key"), secret=True)
        s["channel"] = _ask("Channel id for the status message and alerts (empty: status only)", s.get("channel"))
        if s["channel"]:
            s["mention"] = _ask("Who to mention on serious alerts, e.g. <@123> or <@&456> (empty: nobody)", s.get("mention"))
        s["name"] = _ask("Name to show for the drive", s.get("name") or "DDrive")
    except (EOFError, KeyboardInterrupt):
        print("\n[!] Nothing changed.")
        return 1
    if not (s["token"] and s["url"] and s["key"]):
        print("[!] The token, the address and the key are all needed.")
        return 1
    save_settings(s, path)
    print(f"[OK] Saved in {path or settings_path()}.")
    try:
        snap = fetch(s["url"], s["key"])
        tot = sl.totals(snap)
        print(f"[OK] The drive answers: DDrive {snap.get('version')}, {sl.count(tot.get('files'), 'file')}, {sl.size(tot.get('bytes'))}.")
    except DriveDown as e:
        print(f"[!] The drive doesn't answer yet: {e}")
    return 0


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="statusbot", description="A Discord bot that shows how a drive is doing")
    p.add_argument("action", nargs="?", default="run", choices=["run", "setup", "test", "show"])
    p.add_argument("--config", help="settings file (default: statusbot.json next to the config)")
    a = p.parse_args(argv)
    if a.action == "setup":
        return setup(a.config)
    s = load_settings(a.config)
    if a.action == "show":
        print(json.dumps({k: ("(set)" if k in SECRETS and v else v) for k, v in s.items()}, indent=2))
        return 0
    if not (s.get("url") and s.get("key")):
        print("[!] The status bot isn't set up: run 'statusbot setup' first.")
        return 1
    if a.action == "test":                 # the statuses it would show now, without Discord
        bot = StatusBot(s, api=object())
        bot.poll_once()
        print(("OFFLINE: " + bot.hist.down_error) if bot.down else f"{len(bot.statuses)} statuses, dot: {sl.mood(bot.statuses)}")
        for st in bot.statuses:
            print(f"  [{['critical', 'warning', 'activity', 'info', 'trivia'][st.level]:>8}] {st.text}")
        return 0
    if not s.get("token"):
        print("[!] No bot token: run 'statusbot setup' first.")
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    bot = StatusBot(s)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
    return 0
