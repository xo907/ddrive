"""Spare channels: pieces spread over more than one Discord channel, so that losing a whole channel
(deleted, purged, or its bot banned), or a whole server, loses no file.

A piece stored in a spare channel has the id "<channel id>-<message id>"; a plain message id is a
piece in the main channel, as it always was. Nothing else in the drive needs to know where a piece
lives: `SpreadBackend` sends every call about a piece to the channel its id names, and everything
else (the journal, index checkpoints, pins) to the main channel. Index checkpoints and the list of
accounts are also copied to a few spare channels (MIRRORS), so a drive whose main channel is gone can
start again from there.

How many spare pieces it takes: with D places that can fail on their own, a group of n pieces with m
spare pieces spread evenly holds at most ceil((n + m) / D) pieces in one of them, and survives losing
that one when that is at most m, i.e. m >= n / (D - 1). A place is a server when the channels are in
two servers or more (losing a server loses all its channels at once), else a channel (`Layout`). One
spare channel or server costs as much as a second copy (m = n); two cost half that, four a quarter.

A channel given up for good (`lost_channels`: deleted, its server gone, or removed while it held
pieces) is left out of placement, and its pieces count as gone: reads rebuild them, and repairs put
them in the channels that are left.
"""

import logging

from .backend import DiscordBackend, LocalBackend, StoredChunk, sort_checkpoints, tag_of
from .discord_api import DiscordError

log = logging.getLogger("ddrive.channels")

SEP = "-"
MIRRORS = 3            # spare channels that also keep copies of index checkpoints and the list of accounts
MIRROR_KEEP = 2        # copies of each kept in each of them


def split(mid):
    """(spare channel id or None for the main channel, message id) of a piece id."""
    mid = str(mid)
    if SEP in mid:
        chan, _, raw = mid.partition(SEP)
        return chan, raw
    return None, mid


def channel_of(mid):
    return split(mid)[0]


def spread_pieces(n, places):
    """Spare pieces a group of n data pieces needs so that any one of `places` can be lost."""
    if places < 2:
        return 0
    return -(-n // (places - 1))


class Layout:
    """The channels pieces can go to, and the servers they are in. `server_of`: channel -> server id
    (unknown ones count as one server together, which is what a single server looks like)."""

    def __init__(self, chans, server_of=None):
        self.chans = list(chans) or [None]
        server_of = server_of or {}
        self.server = {c: str(server_of.get(c) or "?") for c in self.chans}
        self.servers = list(dict.fromkeys(self.server[c] for c in self.chans))

    @property
    def by_server(self):
        return len(self.servers) >= 2

    @property
    def places(self):
        """How many places can fail on their own: servers, or channels when they are all in one."""
        return len(self.servers) if self.by_server else len(self.chans)

    def need(self, n):
        return spread_pieces(n, self.places)

    def order(self):
        """The channels taking turns over the servers (one of each server, then the next of each...)."""
        groups = [[c for c in self.chans if self.server[c] == s] for s in self.servers]
        out = []
        for i in range(max(len(g) for g in groups)):
            out += [g[i] for g in groups if i < len(g)]
        return out

    def counts(self, mids):
        """(pieces per channel, pieces per server) of a group's pieces `mids`."""
        per_chan, per_server = {}, {}
        for mid in mids:
            c = channel_of(mid)
            per_chan[c] = per_chan.get(c, 0) + 1
            s = self.server.get(c, f"?{c}")
            per_server[s] = per_server.get(s, 0) + 1
        return per_chan, per_server

    def worst(self, mids):
        """Most of a group's pieces that losing one place (a channel, or a server) would take."""
        per_chan, per_server = self.counts(mids)
        out = max([per_chan.get(c, 0) for c in self.chans] + [0])
        if self.by_server:
            out = max([out] + [per_server.get(s, 0) for s in self.servers])
        return out

    def fill(self, mids, extra):
        """Channels for `extra` more pieces of a group whose pieces are `mids`: each goes to the server, and in
        it the channel, holding fewest so far."""
        per_chan, per_server = self.counts(mids)
        out = []
        order = self.order()
        for _ in range(extra):
            if self.by_server:
                s = min(self.servers, key=lambda x: per_server.get(x, 0))
                pick = min((c for c in order if self.server[c] == s), key=lambda c: per_chan.get(c, 0))
            else:
                pick = min(order, key=lambda c: per_chan.get(c, 0))
            per_chan[pick] = per_chan.get(pick, 0) + 1
            per_server[self.server[pick]] = per_server.get(self.server[pick], 0) + 1
            out.append(pick)
        return out


def layout_of(backend):
    """The Layout of a backend (a single channel when it has no spare channels)."""
    lay = getattr(backend, "layout", None)
    return lay if isinstance(lay, Layout) else Layout([None])


# Kept for callers that only think in channels.
def channels_of(backend):
    """Where `backend` can put pieces: [None] (the main channel only) or [None, spare ids...]."""
    return layout_of(backend).chans


def counts(mids):
    """How many of `mids` (places of one group) each channel holds."""
    return Layout([None]).counts(mids)[0]


def fill(have, extra, chans):
    """Channels for `extra` more pieces, given per-channel counts `have` (channel-level only)."""
    have = {c: have.get(c, 0) for c in chans}
    out = []
    for _ in range(extra):
        c = min(chans, key=lambda x: have[x])
        have[c] += 1
        out.append(c)
    return out


def worst(mids, chans):
    """Most places of a group (data and spare pieces, `mids`) that any one of `chans` holds."""
    n = counts(mids)
    return max([n.get(c, 0) for c in chans] + [0])


def spare_channels(cfg):
    """The configured spare channel ids (strings), without the main channel, lost ones and repeats."""
    lost = lost_channels(cfg)
    out = []
    for c in getattr(cfg, "spare_channels", None) or []:
        c = str(c).strip()
        if c and c != str(getattr(cfg, "channel_id", "")) and c not in out and c not in lost:
            out.append(c)
    return out


def lost_channels(cfg):
    return {str(c).strip() for c in getattr(cfg, "lost_channels", None) or [] if str(c).strip()}


def _gone(what):
    return DiscordError(404, '{"message": "Unknown Message (%s)", "code": 10008}' % what)


class SpreadBackend:
    """The main channel's backend plus one backend per spare channel, behind the backend interface."""

    def __init__(self, main, spares, server_of=None, lost=(), tag=None):
        self.main = main
        self.spares = dict(spares)            # channel id -> backend for that channel
        self.server_of = dict(server_of or {})
        self.lost = set(lost)
        self.tag = tag                        # marks this drive's copies in spare channels (its main channel id)

    # Everything that isn't about one piece (journal, checkpoints, pins, bots) is the main channel's.
    def __getattr__(self, name):
        return getattr(self.main, name)

    @property
    def crypto(self):
        return self.main.crypto

    @crypto.setter
    def crypto(self, value):
        self.main.crypto = value
        for b in self.spares.values():
            b.crypto = value

    @property
    def upload_slots(self):
        """Bots that can post, over all channels (a spare channel with bots of its own adds them)."""
        seen = {id(a) for a in getattr(self.main, "apis", [])} or {0}
        for b in self.spares.values():
            seen.update(id(a) for a in getattr(b, "apis", []))
        return max(1, len(seen), getattr(self.main, "upload_slots", 1))

    @property
    def channels(self):
        """Where pieces can go: None (the main channel) and every spare channel that some bot can use."""
        return [None, *[c for c, b in self.spares.items() if not _all_refused(b)]]

    @property
    def layout(self):
        return Layout(self.channels, self.server_of)

    def route(self, mid):
        """(backend, message id in that channel) for a piece id."""
        chan, raw = split(mid)
        if chan is None:
            return self.main, raw
        if chan in self.lost:
            raise _gone(f"spare channel {chan} was given up")
        b = self.spares.get(chan)
        if b is None:
            raise DiscordError(0, f"piece {mid} is in channel {chan}, which is not among the spare channels "
                                  f"in this device's settings")
        return b, raw

    # ------------------------------------------------------------- pieces
    def upload(self, filename, data, content="", channel=None):
        if channel is None:
            return self.main.upload(filename, data, content)
        s = self.spares[channel].upload(filename, data, content)
        return StoredChunk(f"{channel}{SEP}{s.message_id}", s.attachment_id, s.url)

    def download(self, message_id, url):
        b, raw = self.route(message_id)
        return b.download(raw, url)

    def message_info(self, message_id):
        if channel_of(message_id) in self.lost:
            return None
        b, raw = self.route(message_id)
        return b.message_info(raw)

    def delete(self, message_id):
        if channel_of(message_id) in self.lost:
            return None                       # nothing left there to delete
        b, raw = self.route(message_id)
        return b.delete(raw)

    def time_of(self, mid):
        b, raw = self.route(mid)
        return b.time_of(raw)

    def messages_in(self, chan, after, limit=100):
        """A page of a channel's messages (the background check lists channels instead of asking per piece)."""
        b = self.main if chan is None else self.spares[chan]
        return b.messages_after(after, limit)

    def fetch_attachment(self, msg):
        b, raw = self.route(msg["id"])
        return b.fetch_attachment(dict(msg, id=raw))

    # ------------------------------------------------------ copies in spare channels
    def mirrors(self):
        """The spare channels that keep copies, over as many servers as there are."""
        order = Layout([c for c in self.channels if c is not None], self.server_of).order()
        return [(c, self.spares[c]) for c in order[:MIRRORS]] if order != [None] else []

    def save_index(self, data, previous_message_id=None, cursor=None):
        mid = self.main.save_index(data, previous_message_id=previous_message_id, cursor=cursor)
        for chan, b in self.mirrors():
            try:
                b.save_index(data, previous_message_id=None, cursor=cursor, tag=self.tag)
                mine = [m for m in b.index_candidates(scan_pages=0) if tag_of(m) == self.tag]
                if len(mine) > MIRROR_KEEP and hasattr(b, "drop_checkpoints"):
                    b.drop_checkpoints([m["id"] for m in mine[MIRROR_KEEP:]])
            except Exception as e:
                log.warning("Could not copy the index checkpoint to spare channel %s: %s", chan, e)
        return mid

    def index_candidates(self, scan_pages=10):
        """Every checkpoint that could restore the drive, best first: the main channel's (pinned, then older
        ones it still holds) and this drive's copies in spare channels (tagged with the channel they are in)."""
        out = []
        try:
            out += self.main.index_candidates(scan_pages)
        except Exception as e:
            log.warning("Could not look for index checkpoints in the main channel: %s", e)
        for chan, b in self.spares.items():
            try:
                out += [dict(m, spare=chan) for m in b.index_candidates(scan_pages=0)
                        if tag_of(m) in (None, self.tag)]
            except Exception as e:
                log.debug("No checkpoint from spare channel %s: %s", chan, e)
        return sort_checkpoints(out)

    def find_latest_index(self, *args):
        """The main channel's newest checkpoint; if it has none (the channel is new, empty or gone), the
        newest copy in a spare channel."""
        try:
            latest = self.main.find_latest_index(*args)
        except DiscordError as e:
            log.warning("Could not read the main channel's checkpoint (%s); looking in the spare channels", e)
            latest = None
        if latest is not None:
            return latest
        copies = [m for m in self.index_candidates(scan_pages=0) if m.get("spare")]
        if copies:
            log.warning("The main channel has no index checkpoint; using the copy in spare channel %s", copies[0]["spare"])
            return copies[0]
        return None

    def load_index_message(self, msg):
        if msg.get("spare") in self.spares:
            return self.spares[msg["spare"]].load_index_message(msg)
        return self.main.load_index_message(msg)

    def load_latest_index(self, *args):
        latest = self.find_latest_index(*args)
        if latest is None:
            return None
        return latest["id"], self.load_index_message(latest)

    # The list of accounts and other pinned things: kept in the main channel, copied to the mirrors.
    def save_pinned(self, filename, data, content):
        mid = self.main.save_pinned(filename, data, content)
        first = (content or "").split("\n", 1)[0]
        for chan, b in self.mirrors():
            try:
                b.save_pinned(filename, data, content)
                same = sorted((m for m in b.pinned(first) if (m.get("content") or "").split("\n", 1)[0] == first),
                              key=lambda m: int(m["id"]), reverse=True)
                for m in same[MIRROR_KEEP:]:
                    b.unpin_delete(m["id"])
            except Exception as e:
                log.warning("Could not copy %s to spare channel %s: %s", filename, chan, e)
        return mid

    def pinned(self, marker):
        """The main channel's pinned messages starting with `marker`; if it has none (or can't be read), the
        copies in the spare channels (their ids name the channel, so reading and deleting them works)."""
        try:
            found = self.main.pinned(marker)
            if found:
                return found
        except DiscordError as e:
            log.warning("Could not read the main channel's pinned messages (%s); using the spare channels' copies", e)
        for chan, b in self.mirrors():
            try:
                found = b.pinned(marker)
            except Exception:
                continue
            if found:
                return [dict(m, id=f"{chan}{SEP}{m['id']}") for m in found]
        return []

    def channel_problems(self):
        """Spare channels Discord refuses every bot of lately (nothing new goes there meanwhile)."""
        return [c for c, b in self.spares.items() if _all_refused(b)]

    # ------------------------------------------------------ servers
    def learn_servers(self, cfg=None):
        """Ask Discord which server each channel is in (once; kept in `channel_servers`). Placement then
        spreads a group over servers first, so a whole server can be lost."""
        known = dict(getattr(cfg, "channel_servers", None) or {}) if cfg is not None else {}
        main_id = str(getattr(self.main, "channel_id", "") or "")
        changed = False
        for chan, b in [(None, self.main), *self.spares.items()]:
            key = main_id if chan is None else chan
            if not key or key in known:
                self.server_of[chan] = known.get(key)
                continue
            api = getattr(b, "api", None)
            if api is None or not hasattr(api, "get_channel"):
                continue
            try:
                guild = (api.get_channel(key) or {}).get("guild_id")
            except Exception as e:
                log.debug("Could not ask which server channel %s is in: %s", key, e)
                continue
            if guild:
                known[key] = str(guild)
                self.server_of[chan] = str(guild)
                changed = True
        if changed and cfg is not None:
            cfg.channel_servers = known
            try:
                cfg.save()
            except Exception as e:
                log.debug("Could not save which servers the channels are in: %s", e)
        servers = {s for s in self.server_of.values() if s}
        if len(servers) >= 2:
            log.info("Pieces are spread over %d servers, so a whole server can be lost.", len(servers))
        return dict(self.server_of)


def _all_refused(b):
    """Has Discord refused every bot of this channel lately? (Then nothing new is put there for now.)"""
    f = getattr(b, "all_refused", None)
    return bool(f()) if callable(f) else False


def with_spares(cfg, main, api_for=None, local_root=None):
    """`main`, or a SpreadBackend over it and the spare channels in `cfg`.

    `api_for(token)` makes the API object of a spare channel's own bot; a spare channel without one
    uses the main channel's bots. `local_root`: tests (a folder per channel next to the main one)."""
    chans = spare_channels(cfg)
    lost = lost_channels(cfg)
    if not chans and not lost:
        return main
    tokens = list(getattr(cfg, "spare_bot_tokens", None) or [])
    full = [str(c).strip() for c in getattr(cfg, "spare_channels", None) or []]
    spares = {}
    for chan in chans:
        i = full.index(chan) if chan in full else -1
        if local_root is not None:
            spares[chan] = LocalBackend(f"{local_root}{SEP}{chan}", crypto=getattr(main, "crypto", None))
            continue
        token = tokens[i] if 0 <= i < len(tokens) else ""
        if token:
            spares[chan] = DiscordBackend(api_for(token), chan, crypto=main.crypto)
        else:
            spares[chan] = DiscordBackend(main.api, chan, crypto=main.crypto, extra_apis=main.apis[1:])
    known = dict(getattr(cfg, "channel_servers", None) or {})
    main_id = str(getattr(cfg, "channel_id", "") or "")
    server_of = {None: known.get(main_id), **{c: known.get(c) for c in spares}}
    if spares:
        log.info("Pieces are spread over %d channels (the main one and %d spare).", 1 + len(spares), len(spares))
    if lost:
        log.warning("%d spare channel(s) given up: their pieces are rebuilt into the channels that are left.", len(lost))
    return SpreadBackend(main, spares, server_of=server_of, lost=lost, tag=main_id or "main")
