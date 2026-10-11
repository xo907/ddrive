"""Operations shared by the command line and the web dashboard (restoring versions and snapshots)."""

import posixpath
import time

from .index import new_uid


def version_put_op(idx, v, uid, parent_uid, name):
    """A put operation that brings back version row `v` as file `uid` (named `name` in `parent_uid`)."""
    chunks = idx.version_chunks(v["id"])
    return {"t": "put", "u": uid, "p": parent_uid, "n": name, "s": v["size"],
            "m": v["mtime"] or time.time(),
            "c": [[c["message_id"], c["size"], c["sha256"]] for c in chunks],
            "r": 1}   # an explicit restore: allowed even though the file was deleted


def undelete_ops(idx, rows):
    """Operations bringing back deleted files (rows of Index.deleted_files) at their old paths,
    re-creating folders that were deleted with them."""
    made, ops = {}, []
    for v in rows:
        folder = posixpath.dirname(v["path"] or "/" + v["name"]) or "/"
        node = idx.get_by_uid(v["parent_uid"]) if v["parent_uid"] else None
        if node is not None and node["is_dir"]:
            puid = v["parent_uid"]
        else:
            puid, more = _folder_ops(idx, folder, made)
            ops += more
        ops.append(version_put_op(idx, v, v["uid"], puid, v["name"]))
    return ops


def _folder_ops(idx, path, made):
    """(uid of folder `path`, ops creating whichever part of it doesn't exist yet)."""
    path = "/" + path.strip("/")
    if path in made:
        return made[path], []
    node = idx.resolve(path)
    if node is not None and node["is_dir"]:
        made[path] = node["uid"]
        return node["uid"], []
    parent, name = posixpath.split(path)
    puid, ops = _folder_ops(idx, parent, made)
    uid = new_uid()
    ops.append({"t": "mkdir", "u": uid, "p": puid, "n": name, "m": time.time()})
    made[path] = uid
    return uid, ops


def snapshot_restore_ops(idx, snap_id, prefix="/", target=None, in_place=False):
    """Operations restoring snapshot `snap_id` (everything under `prefix`).

    By default into a new folder `target` (e.g. "/Restored 2026-10-07 14.00"), which never touches
    current files. `in_place` puts each file back at its original path instead; whatever is there
    now is kept as an older version, so that is reversible too. Returns (ops, files restored)."""
    prefix = "/" + prefix.strip("/")
    entries = idx.snapshot_entries(snap_id, prefix)
    if not entries:
        return [], 0
    base = posixpath.dirname(prefix) if prefix != "/" else "/"
    made, ops, files = {}, [], 0
    for e in sorted(entries, key=lambda e: (e["path"].count("/"), e["path"])):
        rel = e["path"][len(base):].lstrip("/") if base != "/" else e["path"].lstrip("/")
        dest = "/" + rel if in_place else posixpath.join("/" + target.strip("/"), rel)
        if e["is_dir"]:
            _, more = _folder_ops(idx, dest, made)
            ops += more
            continue
        parent, name = posixpath.split(dest)
        puid, more = _folder_ops(idx, parent, made)
        ops += more
        current = idx.resolve(dest) if in_place else None
        uid = current["uid"] if current is not None and not current["is_dir"] else new_uid()
        ops.append({"t": "put", "u": uid, "p": puid, "n": name, "s": e["size"], "m": e["mtime"] or time.time(),
                    "c": [list(c) for c in e["chunks"]], "r": 1})
        files += 1
    return ops, files


def intact_copy(idx, lost, path):
    """The newest copy of the file at `path` that has none of the pieces in `lost` (ids of pieces that are
    gone for good): from a snapshot or from its earlier versions. None when there is no such copy.
    {"source": "snapshot" | "version", "at", "size", "mtime", "chunks": [[piece id, size, sha256]]}"""
    node = idx.resolve(path)
    if node is None or node["is_dir"]:
        return None
    best = None
    for s in idx.snapshots():
        if best is not None and s["at"] <= best["at"]:
            break                                    # newest first: nothing later in the list beats what we have
        for e in idx._all("SELECT id, size, mtime FROM snapshot_entries WHERE snap_id=? AND is_dir=0 AND path=? COLLATE NOCASE",
                          (s["id"], path)):
            chunks = [[r["message_id"], r["size"], r["sha256"]] for r in idx._all(
                "SELECT message_id, size, sha256 FROM snapshot_chunks WHERE entry_id=? ORDER BY idx", (e["id"],))]
            if not any(c[0] in lost for c in chunks):
                best = {"source": "snapshot", "at": s["at"], "size": e["size"], "mtime": e["mtime"], "chunks": chunks}
    for v in idx.versions_for(node["uid"]):          # newest first
        if best is not None and v["superseded"] <= best["at"]:
            break
        chunks = [[c["message_id"], c["size"], c["sha256"]] for c in idx.version_chunks(v["id"])]
        if not any(c[0] in lost for c in chunks):
            best = {"source": "version", "at": v["superseded"], "size": v["size"], "mtime": v["mtime"], "chunks": chunks}
            break
    return best


def repair_op(idx, path, copy):
    """The operation that puts an intact copy (see intact_copy) in the place of the damaged file. What is
    there now is kept as an earlier version, so this can be undone."""
    node = idx.resolve(path)
    return {"t": "put", "u": node["uid"], "p": idx.get(node["parent"])["uid"], "n": node["name"], "s": copy["size"],
            "m": copy["mtime"] or time.time(), "c": copy["chunks"], "r": 1}


def default_restore_folder(snap):
    return "/Restored " + time.strftime("%Y-%m-%d %H.%M", time.localtime(snap["at"]))
