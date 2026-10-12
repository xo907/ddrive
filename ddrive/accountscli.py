"""`accounts` on the command line: the list of accounts, admin accounts, switching off and deleting,
and moves between end-to-end encrypted accounts.

The list belongs to the dashboard (accounts.py), which runs inside the drive. While it runs, these
commands ask it, signed in as the owner with a session made from the dashboard's secret in the
config file (whoever can read that file is the owner anyway). While the drive is stopped they change
the list on disk themselves; the drive picks that up when it starts.
"""

import http.client
import json
import sys
import time

from .config import Config, launcher

USAGE = f"""Usage: {launcher()} accounts <command>

  list                   the accounts, admin accounts and moves
  admin [note]           make an admin account (a co-owner) and show its 24 words once
  disable <id>           switch an account off (an admin is signed out at once)
  enable <id>            switch it on again
  delete <id>            delete an account for good (with everything it stored)
  moves                  moves between end-to-end encrypted accounts in progress
  move-cancel <id>       stop a move (the account it came from is left as it was)"""


class Unreachable(Exception):
    pass


class _Remote:
    """The running dashboard, signed in as the owner."""

    def __init__(self, cfg):
        from .web import session_cookie
        self.port = int(cfg.web_port)
        self.cookie = "dd_session=" + session_cookie(cfg.web_token)

    def call(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Cookie": self.cookie, "Host": "127.0.0.1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({"X-DD": "1", "Content-Type": "application/json"})
        try:
            conn.request("POST" if body is not None else "GET", path, data, headers)
            r = conn.getresponse()
            raw = r.read()
        except OSError as e:
            raise Unreachable(str(e)) from None
        finally:
            conn.close()
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            out = {"error": raw.decode("utf-8", "replace")[:200]}
        if r.status >= 400:
            raise RuntimeError(out.get("error") or f"HTTP {r.status}")
        return out

    def listing(self):
        return self.call("/api/accounts")

    def admin(self, note):
        r = self.call("/api/accounts/admin", {"note": note})
        return r["id"], r["phrase"]

    def disable(self, aid, off):
        self.call("/api/accounts/disable", {"id": aid, "off": off})

    def delete(self, aid):
        self.call("/api/accounts/delete", {"id": aid})

    def moves(self):
        return self.call("/api/accounts/moves")["moves"]

    def move_cancel(self, mid):
        self.call("/api/accounts/moves/cancel", {"id": mid})


class _Stub:
    """Just enough of a drive for the list on disk (accounts.Accounts) while the drive is stopped."""
    local_test_dir = None
    backend = None
    crypto = None

    def __init__(self, cfg):
        self.cfg = cfg


class _Local:
    """The list on disk, changed here: only while the drive (and so the dashboard) is stopped."""

    def __init__(self, cfg):
        from .accounts import Accounts
        self.acc = Accounts(_Stub(cfg))
        if self.acc._broken:
            raise RuntimeError("The list of accounts on this computer can't be read.")

    def listing(self):
        return {"accounts": self.acc.listing(), "mode": self.acc.mode()}

    def admin(self, note):
        phrase, rec = self.acc.create_admin(note)
        return rec["id"], phrase

    def disable(self, aid, off):
        with self.acc._lock:
            rec = self.acc._recs.get(aid)
            if rec is None:
                raise RuntimeError("No such account")
            rec["disabled"] = bool(off)
            self.acc._save()

    def delete(self, aid):
        with self.acc._lock:
            rec = self.acc._recs.get(aid)
            if rec is None:
                raise RuntimeError("No such account")
            if not rec.get("admin"):
                raise RuntimeError(f"Deleting an account with storage needs the drive running ({launcher()} start): "
                                   "what it stored on Discord is removed by the drive.")
            self.acc._recs.pop(aid)
            self.acc._save()

    def moves(self):
        from .transfer import Moves
        return Moves.read_all(self.acc.root)

    def move_cancel(self, mid):
        raise RuntimeError(f"Moves are run by the drive: start it first ({launcher()} start).")


def _target(cfg):
    """The running dashboard if there is one, else the list on disk."""
    if cfg.web_enabled and cfg.web_token:
        remote = _Remote(cfg)
        try:
            remote.call("/api/accounts")
            return remote
        except Unreachable:
            pass
    from .cli import is_mounted
    if cfg.web_enabled and cfg.mount_point and is_mounted(cfg.mount_point):
        raise RuntimeError("The drive is running but its dashboard can't be reached, so the list of accounts "
                           "can't be changed safely now.")
    return _Local(cfg)


def _ago(t):
    if not t:
        return "never"
    s = max(0, time.time() - float(t))
    for n, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= n:
            k = int(s // n)
            return f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


def _print_list(d):
    accts = d.get("accounts") or []
    admins = [a for a in accts if a.get("admin")]
    others = [a for a in accts if not a.get("admin")]
    print(f"Sign-up: {d.get('mode', 'closed')}")
    print(f"\nAdmin accounts ({len(admins)}):")
    for a in admins:
        print(f"  {a['id']}  {'OFF ' if a['disabled'] else ''}made {_ago(a['created'])}, last signed in {_ago(a['seen'])}"
              + (f"  ({a['note']})" if a.get("note") else ""))
    print(f"\nAccounts ({len(others)}):")
    from .config import format_size
    for a in others:
        kind = "end-to-end" if a.get("e2e") else "phrase"
        print(f"  {a['id']}  {kind:10s} {'OFF ' if a['disabled'] else ''}{a['files']} files, {format_size(a['bytes'])}, "
              f"last signed in {_ago(a['seen'])}{'  MOVING' if a.get('moving') else ''}")


def _print_moves(moves):
    if not moves:
        print("No moves between accounts.")
        return
    for m in moves:
        done = f"{m.get('copied', 0)}/{m.get('objects', 0)} objects" if m.get("objects") else ""
        print(f"  {m['id']}  {m.get('from') or '?'} -> {m['to']}  {m['state']}  {done}"
              + (f"  ({m['error']})" if m.get("error") else ""))


def main(args):
    cfg = Config.load()
    action = (args.action or "list").lower()
    rest = list(args.rest or [])
    try:
        t = _target(cfg)
        if action == "list":
            _print_list(t.listing())
            return 0
        if action == "admin":
            aid, phrase = t.admin(" ".join(rest))
            print(f"[OK] Admin account {aid} was made. It signs in to the dashboard with these 24 words:\n")
            ws = phrase.split()
            for i in range(0, 24, 6):
                print("   " + "  ".join(f"{i + j + 1:2d}. {w:<10s}" for j, w in enumerate(ws[i:i + 6])))
            print("\nThey are shown only now. An admin can do everything the owner can, except change how the owner")
            print(f"signs in. Switch it off with: {launcher()} accounts disable {aid}")
            return 0
        if action in ("disable", "enable", "delete", "move-cancel"):
            if not rest:
                print(f"[ERROR] Which one? {launcher()} accounts {action} <id>")
                return 1
            if action == "delete":
                if sys.stdin.isatty() and input(f"Delete account {rest[0]} for good? Type delete: ").strip().lower() != "delete":
                    print("Nothing was deleted.")
                    return 1
                t.delete(rest[0])
                print(f"[OK] Account {rest[0]} was deleted.")
            elif action == "move-cancel":
                t.move_cancel(rest[0])
                print(f"[OK] The move {rest[0]} was stopped.")
            else:
                t.disable(rest[0], action == "disable")
                print(f"[OK] Account {rest[0]} is {'switched off' if action == 'disable' else 'on again'}.")
            return 0
        if action == "moves":
            _print_moves(t.moves())
            return 0
    except (RuntimeError, ValueError) as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:                       # noqa: BLE001 - shown, not hidden
        from .accounts import AccountError
        if isinstance(e, AccountError):
            print(f"[ERROR] {e}")
            return 1
        raise
    print(USAGE)
    return 1
