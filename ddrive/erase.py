"""Erase everything: a hard reset of the drive that keeps how it reaches Discord.

Gone afterwards: every message in the drive's channels (the main one and every spare one), so every
file, earlier version, snapshot, spare piece, index checkpoint, change journal, account and list of
accounts stored there; and on this computer the file list, uploads still waiting, the cache, the
backups, kept-aside copies and every account. Kept: the config file (bots, channels, spare channels,
the 24 words, the dashboard's sign-in). The next start is an empty drive in the same channels.

It is resumable: a mark (erase.json in the data folder) is written first and removed last. A drive that
starts while the mark is there finishes the erasing before anything else, so an interrupted reset (the
computer restarted, the network went away) is never left half done.

Typing CONFIRM is what asks for it, in the dashboard (Settings) or with `erase-everything`.
"""

import json
import logging
import os
import shutil
import threading
import time

from .backend import snowflake_time
from .discord_api import DiscordError

log = logging.getLogger("ddrive.erase")

CONFIRM = "ERASE EVERYTHING"
MARK = "erase.json"
BULK_AGE = 13.5 * 86400      # Discord deletes messages younger than 14 days 100 at a time
DELETES_PER_MINUTE = 50      # per bot while erasing (the drive does nothing else meanwhile)


def mark_path(cfg):
    return os.path.join(cfg.resolved_data_dir, MARK)


def pending(cfg):
    """Was erasing asked for and not finished?"""
    return os.path.exists(mark_path(cfg))


def request(cfg, phase="discord"):
    """Note that everything is to be erased (before any of it is)."""
    os.makedirs(cfg.resolved_data_dir, exist_ok=True)
    with open(mark_path(cfg), "w", encoding="utf-8") as f:
        json.dump({"asked": time.time(), "phase": phase}, f)


def _phase(cfg):
    try:
        with open(mark_path(cfg), encoding="utf-8") as f:
            return json.load(f).get("phase") or "discord"
    except (OSError, ValueError):
        return "discord"


class Eraser:
    """Does the erasing; `state` says how far it is (for the dashboard)."""

    def __init__(self, cfg, backend, save_config=True):
        self.cfg = cfg
        self.backend = backend
        self.save_config = save_config        # (tests: never the real config file)
        self.stop = threading.Event()
        self.state = {"running": False, "phase": "", "channel": "", "deleted": 0, "error": "", "done": False}

    def _channels(self):
        main = getattr(self.backend, "main", self.backend)
        out = [("the main channel", main)]
        out += [(f"spare channel {c}", b) for c, b in (getattr(self.backend, "spares", None) or {}).items()]
        return out

    def run(self, local=True):
        """Erase Discord (unless that part is done already), then, with `local`, this computer's data."""
        st = self.state
        st.update(running=True, error="", done=False)
        try:
            if not pending(self.cfg):
                request(self.cfg)
            if _phase(self.cfg) == "discord":
                st["phase"] = "discord"
                for label, b in self._channels():
                    if self.stop.is_set():
                        return False
                    st["channel"] = label
                    log.warning("Erasing everything in %s...", label)
                    self._wipe_channel(b)
                request(self.cfg, phase="local")
                log.warning("Everything the drive kept on Discord is erased (%d message(s)).", st["deleted"])
            if local:
                st["phase"] = "local"
                self.wipe_local()
                st.update(phase="done", done=True)
                log.warning("Everything is erased. The drive starts empty, in the same channels.")
            return True
        except Exception as e:
            st["error"] = str(e)
            log.error("Erasing stopped: %s. It goes on where it stopped the next time it is started.", e)
            raise
        finally:
            st["running"] = False

    # ------------------------------------------------------------ Discord
    def _wipe_channel(self, b):
        api = getattr(b, "api", None)
        if api is None or not hasattr(api, "get_messages"):
            return self._wipe_folder(b)              # tests: a folder stands in for the channel
        chan = b.channel_id
        apis = list(getattr(b, "apis", None) or [api])
        for a in apis:
            pacer = a.pacers["delete"]
            if pacer.interval and pacer.interval > 60.0 / DELETES_PER_MINUTE:
                pacer.set(DELETES_PER_MINUTE)
        turn = 0
        while not self.stop.is_set():
            page = api.get_messages(chan, limit=100) or []
            if not page:
                return
            young = [m["id"] for m in page if time.time() - snowflake_time(m["id"]) < BULK_AGE]
            old = [m["id"] for m in page if m["id"] not in young]
            if len(young) >= 2:
                api.bulk_delete(chan, young)
            else:
                old += young
            for mid in old:
                if self.stop.is_set():
                    return
                turn += 1
                self._delete_one(apis[turn % len(apis)], api, chan, mid)
            self.state["deleted"] += len(page)

    @staticmethod
    def _delete_one(a, main, chan, mid):
        try:
            a.delete_message(chan, mid)
        except DiscordError as e:
            if e.status == 404:
                return
            if e.status == 403 and a is not main:     # that bot may not delete others' messages: the main one may
                try:
                    main.delete_message(chan, mid)
                except DiscordError as e2:
                    if e2.status != 404:
                        raise
                return
            raise

    def _wipe_folder(self, b):
        for mid in list(b._ids()):
            b.delete(str(mid))
            self.state["deleted"] += 1
        for name in os.listdir(b.root):
            if name.endswith(".id") or name == "pins.json":
                try:
                    os.remove(os.path.join(b.root, name))
                except OSError:
                    pass

    # ------------------------------------------------------------ this computer
    def wipe_local(self):
        """Everything in the data folder but the logs, the mark and (if it is there) the config file."""
        from .config import config_path
        data = os.path.abspath(self.cfg.resolved_data_dir)
        keep = {os.path.abspath(config_path()), os.path.abspath(mark_path(self.cfg))}
        for name in os.listdir(data) if os.path.isdir(data) else []:
            p = os.path.join(data, name)
            if os.path.abspath(p) in keep or name.endswith((".log", ".pid")):
                continue
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError as e:
                    log.warning("Could not erase %s: %s", p, e)
        if getattr(self.cfg, "lost_channels", None) and self.save_config:
            self.cfg.lost_channels = []          # the channels are empty now: nothing is left to rebuild
            try:
                self.cfg.save()
            except Exception as e:
                log.debug("Could not save the config after erasing: %s", e)
        os.remove(mark_path(self.cfg))
