"""Moving everything of one end-to-end encrypted account into another (a key exchange, then a copy).

The host can't do this alone: the list of files of such an account (names, folders, and each file's
own key) is sealed with a key only the account's devices have (vault.py). And the devices can't do it
alone either without fetching every file and sending it again. So it is done in two halves:

  the keys     The account that receives (T) and the account that gives (S) agree on a key with an
               Elliptic-curve Diffie-Hellman exchange (P-256), through the host, which only ever sees
               the two public keys. S seals its whole list with that key and hands it to the host,
               which passes it on to T unread. T opens it and adds it to its own list, sealed with its
               own key, in a new folder.
  the files    The objects (each file's content, sealed on S's device with that file's own key) are
               copied by the host from S's storage to T's: it can do that without opening them, and
               without anybody's device waiting. They keep their ids, so the list T received names
               them as they are.

Two codes keep the host honest: T shows a code made from its public key, and S's page shows the code
of the public key the host gave it: if they differ, the host swapped the key and S doesn't send. After
sending, both pages show a second code made from both public keys: T only takes the list in when
they match. So a host that wants to read the list (and with it every file's key) or slip in one of its
own has to be caught by the person looking at two screens.

Then S is deleted, with everything it stored: this is a move, not a copy. That happens only once T has
taken the list in and the copied objects are stored on Discord as T's.

A move goes through these states (Move.state):

  open        T asked for it and waits for S (OPEN_MINUTES).
  copying     S sent its sealed list; the host copies the objects. S can't change anything now.
  ready       everything is in T's storage, set aside (under /xin/<move>): T's page takes the list in.
  finishing   T took it in; the objects are T's now. S is deleted once they are on Discord.
  done        S is gone.
  failed / cancelled   S is as it was (and T has nothing of it).
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time

from .logbuf import quiet

log = logging.getLogger("ddrive.accounts")

OPEN_MINUTES = 30                 # how long T's request waits for S
READY_DAYS = 7                    # how long a copied move waits for T's page to take it in
LIST_MAX = 64 * 1024 * 1024       # the largest sealed list S may send
PIECE = 4 * 1024 * 1024           # objects are copied this much at a time
KEEP_DONE = 7 * 86400.0           # finished moves are listed this long

CODE_INFO = b"ddrive move v1 code"
CHECK_INFO = b"ddrive move v1 check"
ACTIVE = ("open", "copying", "ready", "finishing")


class MoveError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _code(data: bytes) -> str:
    """Eight letters and digits to compare by eye (40 bits): XXXX-XXXX."""
    s = base64.b32encode(hashlib.sha256(data).digest()[:5]).decode("ascii")
    return f"{s[:4]}-{s[4:]}"


def key_code(tpub: bytes) -> str:
    """The code of T's public key: shown on T's page and on S's page before S sends anything."""
    return _code(CODE_INFO + tpub)


def check_code(tpub: bytes, spub: bytes) -> str:
    """The code of both public keys: shown on S's page after sending and on T's page before taking the list in."""
    return _code(CHECK_INFO + tpub + spub)


def _pub(text) -> bytes:
    try:
        raw = base64.b64decode(str(text or ""), validate=True)
    except ValueError:
        raw = b""
    if len(raw) != 65 or raw[0] != 4:
        raise MoveError(400, "That isn't a public key.")
    return raw


class Moves:
    """The moves this computer runs (one dashboard: WebServer.moves). Kept in <accounts>/moves.json,
    so a copy that was cut short by a restart goes on from where it was."""

    def __init__(self, accounts):
        self.accounts = accounts
        self.path = os.path.join(accounts.root, "moves.json")
        self._lock = threading.RLock()
        self._moves = {}
        self._workers = {}                       # move id -> thread
        self._stop = threading.Event()
        self.background = accounts.background    # tests run each step themselves (step())
        self._load()

    # ------------------------------------------------------------ the list on disk
    @staticmethod
    def read_all(root):
        try:
            with open(os.path.join(root, "moves.json"), encoding="utf-8") as f:
                return [Moves._public(m) for m in (json.load(f).get("moves") or {}).values()]
        except (OSError, ValueError):
            return []

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self._moves = dict(json.load(f).get("moves") or {})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("The list of moves between accounts can't be read (%s); moves start afresh.", e)

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"moves": self._moves}, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _public(m):
        return {k: m.get(k) for k in ("id", "to", "from", "state", "created", "objects", "copied", "bytes", "done_bytes",
                                      "error", "updated")}

    def listing(self):
        with self._lock:
            return sorted((self._public(m) for m in self._moves.values()), key=lambda m: -(m["created"] or 0))

    def moving_ids(self):
        with self._lock:
            return {m["from"] for m in self._moves.values() if m.get("from") and m["state"] in ("copying", "ready", "finishing")}

    def moving(self, aid):
        """The move account `aid` is given away in (it may not change anything meanwhile), or None."""
        with self._lock:
            for m in self._moves.values():
                if m.get("from") == aid and m["state"] in ("copying", "ready", "finishing"):
                    return m["id"]
        return None

    def _get(self, mid):
        m = self._moves.get(str(mid or ""))
        if m is None:
            raise MoveError(404, "There is no such move (or it ran out). Start again on the account that receives.")
        return m

    def _set(self, m, **changes):
        m.update(changes, updated=time.time())
        self._save()

    def _e2e(self, aid):
        rec = self.accounts.active(aid)
        if rec is None or not rec.get("e2e"):
            raise MoveError(403, "Only end-to-end encrypted accounts can be moved this way.")
        return rec

    # ------------------------------------------------------------ the account that receives (T)
    def open(self, to, tpub_b64):
        """T asks for a move: its public key. Returns the move's id (what S types in) and T's key code."""
        self._e2e(to)
        tpub = _pub(tpub_b64)
        with self._lock:
            for m in self._moves.values():
                if m["state"] in ACTIVE and to in (m.get("from"), m.get("to")) and m["state"] != "open":
                    raise MoveError(409, "This account is already in a move. Finish or cancel that one first.")
            for m in list(self._moves.values()):          # an earlier request of T's that nobody answered
                if m["to"] == to and m["state"] == "open":
                    self._set(m, state="cancelled", error="replaced by a new request")
            mid = base64.b32encode(secrets.token_bytes(10)).decode("ascii").lower()
            self._moves[mid] = {"id": mid, "to": to, "from": "", "tpub": base64.b64encode(tpub).decode("ascii"), "spub": "",
                                "state": "open", "created": time.time(), "objects": 0, "copied": 0, "bytes": 0, "done_bytes": 0,
                                "error": "", "updated": time.time()}
            self._save()
        log.info("Account %s is waiting for another account to move in (move %s).", to, mid)
        return {"id": mid, "code": key_code(tpub), "expires": time.time() + OPEN_MINUTES * 60}

    def status(self, mid, who):
        """How a move is going, for either of its accounts (or the owner: who=None)."""
        with self._lock:
            m = self._get(mid)
            if who is not None and who not in (m["to"], m["from"]):
                raise MoveError(404, "There is no such move (or it ran out). Start again on the account that receives.")
            out = self._public(m)
            tpub = base64.b64decode(m["tpub"])
            out["code"] = key_code(tpub)
            if m.get("spub"):
                out["check"] = check_code(tpub, base64.b64decode(m["spub"]))
                out["spub"] = m["spub"]
            if m["state"] == "open":
                out["expires"] = m["created"] + OPEN_MINUTES * 60
            return out

    def sealed_list(self, mid, who):
        """What S sent (sealed with the agreed key), for T's page: (S's public key, the sealed bytes)."""
        with self._lock:
            m = self._get(mid)
            if who != m["to"]:
                raise MoveError(404, "There is no such move.")
            if m["state"] not in ("ready", "finishing"):
                raise MoveError(409, "The files are still being copied." if m["state"] == "copying" else f"This move is {m['state']}.")
        d = self.accounts.acquire(m["to"])
        try:
            node = d.index.resolve(f"/xin/{mid}/list")
            if node is None:
                raise MoveError(410, "What the other account sent is gone. Cancel the move and start again.")
            return m["spub"], d.fs.read_file(node["id"], 0, node["size"])
        finally:
            self.accounts.release(d)

    def finish(self, mid, who):
        """T took the list in: the copied objects become T's, and S is deleted once they are on Discord."""
        with self._lock:
            m = self._get(mid)
            if who != m["to"]:
                raise MoveError(404, "There is no such move.")
            if m["state"] == "finishing" or m["state"] == "done":
                return self._public(m)
            if m["state"] != "ready":
                raise MoveError(409, f"This move is {m['state']}, not ready.")
            self._set(m, state="finishing")
        self._adopt(m)
        if self.background:
            self._run(mid)                       # waits for the uploads, then deletes S
        else:
            self.step(mid)
        return self._public(m)

    # ------------------------------------------------------------ the account that gives (S)
    def peer(self, mid, who):
        """For S's page: who it would move into, and the public key to check and to agree on a key with."""
        self._e2e(who)
        with self._lock:
            m = self._get(mid)
            if m["state"] != "open" or time.time() - m["created"] > OPEN_MINUTES * 60:
                raise MoveError(410, "That request ran out or was answered already. Start again on the account that receives.")
            if m["to"] == who:
                raise MoveError(400, "That is this account's own request: type it on the account that moves out.")
            return {"id": mid, "to": m["to"], "tpub": m["tpub"], "code": key_code(base64.b64decode(m["tpub"]))}

    def send(self, mid, who, spub_b64, sealed: bytes):
        """S sends its public key and its whole list sealed with the agreed key. From here on S can't
        change anything, and the host copies S's objects to T."""
        self._e2e(who)
        spub = _pub(spub_b64)
        if not sealed or len(sealed) > LIST_MAX:
            raise MoveError(413, "The list of files is too big to move this way.")
        def check():
            m = self._get(mid)
            if m["state"] != "open" or time.time() - m["created"] > OPEN_MINUTES * 60:
                raise MoveError(410, "That request ran out or was answered already. Start again on the account that receives.")
            if m["to"] == who:
                raise MoveError(400, "An account can't move into itself.")
            for o in self._moves.values():
                if o["state"] in ACTIVE and o["id"] != mid and who in (o.get("from"), o.get("to")):
                    raise MoveError(409, "This account is already in another move.")
            return m

        with self._lock:
            m = check()
        self._e2e(m["to"])
        # Does it fit? (T's allowance, with what S holds.)
        quota = int(getattr(self.accounts.cfg, "account_quota_bytes", 0) or 0)
        if quota:
            used = 0
            for aid in (who, m["to"]):
                d = self.accounts.acquire(aid)
                try:
                    st = d.index.stats(max_age=0)
                    used += st["bytes"] + st["version_bytes"]
                finally:
                    self.accounts.release(d)
            if used > quota:
                raise MoveError(507, "The account that receives doesn't have room for everything in this one.")
        with self._lock:
            m = check()
            self._set(m, state="copying", spub=base64.b64encode(spub).decode("ascii"), **{"from": who})
        try:
            d = self.accounts.acquire(m["to"])
            try:
                d.index.makedirs(f"/xin/{mid}")
                fh = d.fs.create(f"/xin/{mid}/list", 0o644, wait=False)
                try:
                    d.fs.write(f"/xin/{mid}/list", sealed, 0, fh)
                finally:
                    d.fs.release(f"/xin/{mid}/list", fh)
            finally:
                self.accounts.release(d)
        except Exception as e:
            with self._lock:
                self._set(m, state="failed", error=f"the list could not be kept: {e}")
            raise MoveError(503, "The list could not be kept on the server. Nothing has changed; try again.") from None
        log.info("Account %s is moving into account %s (move %s).", who, m["to"], mid)
        self._run(mid)
        return {"ok": True, "check": check_code(base64.b64decode(m["tpub"]), spub)}

    # ------------------------------------------------------------ either, or the owner
    def cancel(self, mid, who=None):
        """Stop a move before T took the list in. S stays as it was; what was copied to T goes."""
        with self._lock:
            m = self._get(mid)
            if who is not None and who not in (m["to"], m["from"]):
                raise MoveError(404, "There is no such move.")
            if m["state"] in ("finishing", "done"):
                raise MoveError(409, "The files are the other account's already: this move can't be undone.")
            if m["state"] in ("failed", "cancelled"):
                return self._public(m)
            was = m["state"]
            self._set(m, state="cancelled", error=m.get("error") or ("cancelled by the owner" if who is None else "cancelled"))
        worker = self._workers.get(mid)
        if was != "open" and (worker is None or not worker.is_alive() or worker is threading.current_thread()):
            self._clean_target(m)            # (a copy that is running cleans up after itself when it sees this)
        log.info("Move %s was cancelled.", mid)
        return self._public(m)

    # ------------------------------------------------------------ the copy, in the background
    def start(self):
        if self.background:
            for mid, m in list(self._moves.items()):
                if m["state"] in ("copying", "finishing"):
                    self._run(mid)
        return self

    def stop(self):
        self._stop.set()
        for t in list(self._workers.values()):
            t.join(timeout=10)

    def _run(self, mid):
        if not self.background:
            return
        with self._lock:
            t = self._workers.get(mid)
            if t is not None and t.is_alive():
                return
            t = self._workers[mid] = threading.Thread(target=self._work, args=(mid,), name=f"Move-{mid}", daemon=True)
        t.start()

    def _work(self, mid):
        while not self._stop.is_set():
            try:
                again = self.step(mid)
            except Exception as e:                              # noqa: BLE001 - written down on the move
                from .accounts import AccountError
                log.warning("Move %s: %s", mid, e)
                with self._lock:
                    m = self._moves.get(mid)
                    failed = m is not None and isinstance(e, AccountError) and m["state"] == "copying"
                    if failed:
                        self._set(m, state="failed", error=f"an account of this move isn't available: {e}")
                    elif m is not None:
                        self._set(m, error=str(e))
                if failed:
                    self._clean_target(m)
                    return
                again = 30.0
            if not again:
                return
            self._stop.wait(again)

    def step(self, mid):
        """Do what the move needs next. Returns seconds to wait before the next step, or 0 when done."""
        with self._lock:
            m = self._moves.get(mid)
            state = m and m["state"]
        if state == "copying":
            with quiet():
                self._copy(m)
            return 0 if m["state"] != "copying" else 5.0
        if state == "finishing":
            with quiet():
                return self._conclude(m)
        return 0

    def _stopped(self, m):
        return self._stop.is_set() or m["state"] != "copying"

    def _objects(self, d):
        """[(object id, node)] of an account's objects, except the sealed whole list (the host's copy of the list)."""
        snap = None
        node = d.index.resolve("/snap")
        if node is not None and not node["is_dir"]:
            try:
                snap = json.loads(d.fs.read_file(node["id"], 0, node["size"])).get("object")
            except (ValueError, AttributeError):
                snap = None
        top = d.index.resolve("/o")
        out = []
        for sub in (d.index.children(top["id"]) if top else []):
            for c in d.index.children(sub["id"]):
                if not c["is_dir"] and re.fullmatch(r"[0-9a-f]{32}", c["name"]) and c["name"] != snap:
                    out.append((c["name"], c))
        return out

    def _copy(self, m):
        try:
            self._copy_all(m)
        finally:
            if m["state"] == "cancelled":
                self._clean_target(m)

    def _copy_all(self, m):
        mid, accounts = m["id"], self.accounts
        src = accounts.acquire(m["from"])
        try:
            dst = accounts.acquire(m["to"])
            try:
                todo = self._objects(src)
                with self._lock:
                    self._set(m, objects=len(todo), bytes=sum(n["size"] for _, n in todo))
                copied = done_bytes = 0
                tmp_dir = os.path.join(dst.dir, "moving")
                os.makedirs(tmp_dir, exist_ok=True)
                for oid, node in todo:
                    if self._stopped(m):
                        return
                    path = f"/xin/{mid}/o/{oid[:2]}/{oid}"
                    there = dst.index.resolve(path)
                    if there is None or there["is_dir"] or there["size"] != node["size"]:
                        while True:
                            why = dst.fs.holding_back(extra=node["size"])
                            if not why:
                                break
                            if self._stopped(m):
                                return
                            self._stop.wait(5.0)
                        tmp = os.path.join(tmp_dir, f"{mid}-{oid}.part")
                        with open(tmp, "wb") as out:
                            pos = 0
                            while pos < node["size"]:
                                if self._stopped(m):
                                    out.close()
                                    os.remove(tmp)
                                    return
                                data = src.fs.read_file(node["id"], pos, min(PIECE, node["size"] - pos))
                                if not data:
                                    raise OSError(f"object {oid} was cut short")
                                out.write(data)
                                pos += len(data)
                        dst.index.makedirs(f"/xin/{mid}/o/{oid[:2]}")
                        dst.fs.adopt_file(path, tmp, node["mtime"])
                    copied += 1
                    done_bytes += node["size"]
                    with self._lock:
                        if copied % 20 == 0 or copied == len(todo):
                            self._set(m, copied=copied, done_bytes=done_bytes)
                        else:
                            m.update(copied=copied, done_bytes=done_bytes)
                with self._lock:
                    if m["state"] == "copying":
                        self._set(m, state="ready", copied=copied, done_bytes=done_bytes)
                        log.info("Move %s: %d object(s) copied; waiting for the account that receives.", mid, copied)
            finally:
                accounts.release(dst)
        finally:
            accounts.release(src)

    def _adopt(self, m):
        """The objects set aside for T become T's: /xin/<move>/o/.. -> /o/.."""
        d = self.accounts.acquire(m["to"])
        try:
            base = d.index.resolve(f"/xin/{m['id']}/o")
            for sub in (d.index.children(base["id"]) if base else []):
                d.index.makedirs(f"/o/{sub['name']}")
                for c in d.index.children(sub["id"]):
                    dest = f"/o/{sub['name']}/{c['name']}"
                    if d.index.resolve(dest) is None:
                        d.fs.rename(f"/xin/{m['id']}/o/{sub['name']}/{c['name']}", dest)
            self._remove_tree(d, f"/xin/{m['id']}")
        finally:
            self.accounts.release(d)

    def _conclude(self, m):
        """Delete S once the objects T now has are stored on Discord (so they are safe without S)."""
        d = self.accounts.acquire(m["to"])
        try:
            waiting = set(d.index.unsynced_ids())
            if waiting:
                top = d.index.resolve("/o")
                mine = {c["id"] for sub in (d.index.children(top["id"]) if top else []) for c in d.index.children(sub["id"])}
                if waiting & mine:
                    return 10.0
        finally:
            self.accounts.release(d)
        if m.get("from") and self.accounts._recs.get(m["from"]) is not None:
            self.accounts.delete(m["from"])
        with self._lock:
            self._set(m, state="done")
        log.info("Move %s is done: account %s is now in account %s, and was deleted.", m["id"], m["from"], m["to"])
        return 0

    def _clean_target(self, m):
        try:
            d = self.accounts.acquire(m["to"])
        except Exception:
            return
        try:
            self._remove_tree(d, f"/xin/{m['id']}")
        finally:
            self.accounts.release(d)

    @staticmethod
    def _remove_tree(d, path):
        node = d.index.resolve(path)
        if node is None:
            return
        todo, dirs = [(path, node)], []
        while todo:
            p, n = todo.pop()
            if n["is_dir"]:
                dirs.append(p)
                todo.extend((f"{p}/{c['name']}", c) for c in d.index.children(n["id"]))
            else:
                try:
                    d.fs.unlink(p)
                except OSError:
                    pass
        for p in sorted(dirs, key=len, reverse=True):
            try:
                d.fs.rmdir(p)
            except OSError:
                pass
        top = d.index.resolve("/xin")
        if top is not None and not d.index.children(top["id"]):
            try:
                d.fs.rmdir("/xin")
            except OSError:
                pass

    def tidy(self):
        """Requests nobody answered, copies nobody took in, and finished moves that are old."""
        now = time.time()
        with self._lock:
            for m in list(self._moves.values()):
                if m["state"] == "open" and now - m["created"] > OPEN_MINUTES * 60:
                    self._set(m, state="cancelled", error="nobody answered in time")
                elif m["state"] == "ready" and now - m.get("updated", now) > READY_DAYS * 86400:
                    self._set(m, state="cancelled", error="the account that receives didn't take it in")
                    threading.Thread(target=self._clean_target, args=(m,), daemon=True).start()
                elif m["state"] not in ACTIVE and now - m.get("updated", now) > KEEP_DONE:
                    del self._moves[m["id"]]
                    self._save()
            todo = [mid for mid, m in self._moves.items() if m["state"] in ("copying", "finishing")]
        for mid in todo:
            self._run(mid)
