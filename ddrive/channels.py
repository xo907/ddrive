"""Spare channels: pieces spread over more than one Discord channel, so that losing a whole channel
(deleted, purged, or its bot banned) loses no file.

A piece stored in a spare channel has the id "<channel id>-<message id>"; a plain message id is a
piece in the main channel, as it always was. Nothing else in the drive needs to know where a piece
lives: `SpreadBackend` sends every call about a piece to the channel its id names, and everything
else (the journal, index checkpoints, pins) to the main channel. Index checkpoints are also copied
to the first spare channel, so a drive whose main channel is gone can start again from there.

How many spare pieces it takes: with C channels in all, a group of n pieces with m spare pieces
spread evenly holds at most ceil((n + m) / C) pieces in one channel, and survives losing that whole
channel when that is at most m, i.e. m >= n / (C - 1). One spare channel costs as much as a second
copy (m = n); two cost half that, four a quarter (see `spread_pieces`).
"""

import logging

from .backend import DiscordBackend, LocalBackend, StoredChunk

log = logging.getLogger("ddrive.channels")

SEP = "-"


def split(mid):
    """(spare channel id or None for the main channel, message id) of a piece id."""
    mid = str(mid)
    if SEP in mid:
        chan, _, raw = mid.partition(SEP)
        return chan, raw
    return None, mid


def channel_of(mid):
    return split(mid)[0]


def spread_pieces(n, channels):
    """Spare pieces a group of n data pieces needs so that any one of `channels` channels can be lost."""
    if channels < 2:
        return 0
    return -(-n // (channels - 1))


def channels_of(backend):
    """Where `backend` can put pieces: [None] (the main channel only) or [None, spare ids...]."""
    return list(getattr(backend, "channels", None) or [None])


def counts(mids):
    """How many of `mids` (places of one group) each channel holds."""
    out = {}
    for mid in mids:
        c = channel_of(mid)
        out[c] = out.get(c, 0) + 1
    return out


def fill(have, extra, chans):
    """Channels for `extra` more pieces of a group whose pieces are counted in `have`: each goes to the
    channel holding fewest so far, so no channel ends up with more than it must."""
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
    """The configured spare channel ids (strings), without the main channel and without repeats."""
    out = []
    for c in getattr(cfg, "spare_channels", None) or []:
        c = str(c).strip()
        if c and c != str(getattr(cfg, "channel_id", "")) and c not in out:
            out.append(c)
    return out


class SpreadBackend:
    """The main channel's backend plus one backend per spare channel, behind the backend interface."""

    def __init__(self, main, spares):
        self.main = main
        self.spares = dict(spares)            # channel id -> backend for that channel

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
    def channels(self):
        """Where pieces can go: None (the main channel) and every spare channel id."""
        return [None, *self.spares]

    def route(self, mid):
        """(backend, message id in that channel) for a piece id."""
        chan, raw = split(mid)
        if chan is None:
            return self.main, raw
        b = self.spares.get(chan)
        if b is None:
            from .discord_api import DiscordError
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
        b, raw = self.route(message_id)
        return b.message_info(raw)

    def delete(self, message_id):
        b, raw = self.route(message_id)
        return b.delete(raw)

    def time_of(self, mid):
        b, raw = self.route(mid)
        return b.time_of(raw)

    # ------------------------------------------------------ index checkpoints
    def save_index(self, data, previous_message_id=None, cursor=None):
        mid = self.main.save_index(data, previous_message_id=previous_message_id, cursor=cursor)
        self._mirror(data, cursor)
        return mid

    def _mirror(self, data, cursor):
        """A copy of the newest checkpoint in the first spare channel (only the newest is kept there)."""
        if not self.spares:
            return
        chan, b = next(iter(self.spares.items()))
        try:
            old = b.find_latest_index()
            b.save_index(data, previous_message_id=None, cursor=cursor)
            if old is not None and hasattr(b, "drop_checkpoints"):
                b.drop_checkpoints([old["id"]])
        except Exception as e:
            log.warning("Could not copy the index checkpoint to spare channel %s: %s", chan, e)

    def find_latest_index(self, *args):
        """The main channel's newest checkpoint; if it has none (the channel is new or empty), the copy in
        a spare channel."""
        latest = self.main.find_latest_index(*args)
        if latest is not None:
            return latest
        for chan, b in self.spares.items():
            try:
                latest = b.find_latest_index(*args)
            except Exception as e:
                log.debug("No checkpoint from spare channel %s: %s", chan, e)
                continue
            if latest is not None:
                log.warning("The main channel has no index checkpoint; using the copy in spare channel %s", chan)
                return dict(latest, spare=chan)
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


def with_spares(cfg, main, api_for=None, local_root=None):
    """`main`, or a SpreadBackend over it and the spare channels in `cfg`.

    `api_for(token)` makes the API object of a spare channel's own bot; a spare channel without one
    uses the main channel's bots. `local_root`: tests (a folder per channel next to the main one)."""
    chans = spare_channels(cfg)
    if not chans:
        return main
    tokens = list(getattr(cfg, "spare_bot_tokens", None) or [])
    spares = {}
    for i, chan in enumerate(chans):
        if local_root is not None:
            spares[chan] = LocalBackend(f"{local_root}{SEP}{chan}", crypto=getattr(main, "crypto", None))
            continue
        token = tokens[i] if i < len(tokens) else ""
        if token:
            spares[chan] = DiscordBackend(api_for(token), chan, crypto=main.crypto)
        else:
            spares[chan] = DiscordBackend(main.api, chan, crypto=main.crypto, extra_apis=main.apis[1:])
    log.info("Pieces are spread over %d channels (the main one and %d spare).", 1 + len(spares), len(spares))
    return SpreadBackend(main, spares)
