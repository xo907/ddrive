"use strict";
/* The dashboard for an end-to-end encrypted account.
 *
 * The dashboard (app.js) asks the server for everything: the list of a folder, the gallery, a note,
 * a file to show. For an end-to-end account the server can answer none of that: it holds sealed
 * objects and a sealed list it can't read. So those questions are answered here instead, in the
 * browser, from the list of files as vaultcore.js opened it, in the same words the server would use.
 * The dashboard doesn't know the difference, which is why all of it works for such an account.
 *
 * How: every request the page makes to /api/... is looked at first (fetch is wrapped). The ones that
 * are about files and names are answered here; the few that are about the account itself go on to
 * the server. Pictures and videos that the page shows by address (<img src="/api/file?...">) are
 * asked for through a service worker (sw.js), which passes the question to this page and gets the
 * opened bytes back, a piece at a time, so a video plays and seeks without being downloaded first.
 *
 * Nothing in here sends a name or a file's content anywhere: what goes to the server is sealed by
 * vaultcore.js first. For any other account this file does nothing.
 */
(function () {
  const realFetch = window.fetch.bind(window);
  const cred = { credentials: "same-origin" };
  const E = window.E2E = { active: false };
  const enc = new TextEncoder(), dec = new TextDecoder();
  const now = () => Date.now() / 1000;
  let v = null;                              // the account (DDV.Vault)

  class Fail extends Error { constructor(status, message) { super(message); this.status = status; } }
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  // ------------------------------------------------------------ the list of files, by path
  const MEDIA = new Set("jpg jpeg png gif webp avif bmp heic mp4 m4v webm mov mkv avi".split(" "));
  const TYPES = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp", avif: "image/avif", bmp: "image/bmp",
                  heic: "image/heic", svg: "image/svg+xml", mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", mov: "video/quicktime", mkv: "video/x-matroska",
                  avi: "video/x-msvideo", mp3: "audio/mpeg", m4a: "audio/mp4", ogg: "audio/ogg", wav: "audio/wav", flac: "audio/flac", pdf: "application/pdf",
                  txt: "text/plain; charset=utf-8", md: "text/plain; charset=utf-8", json: "application/json", csv: "text/csv; charset=utf-8",
                  html: "text/html; charset=utf-8", htm: "text/html; charset=utf-8", xml: "text/xml", js: "text/plain; charset=utf-8", css: "text/plain; charset=utf-8" };
  const ext = (name) => name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  const typeOf = (name) => TYPES[ext(name)] || "application/octet-stream";
  const KINDS = [["image", "jpg jpeg png gif webp avif bmp heic svg tif tiff raw dng"], ["video", "mp4 m4v webm mov mkv avi wmv"],
                 ["audio", "mp3 m4a ogg wav flac aac opus"], ["pdf", "pdf"], ["text", "doc docx xls xlsx ppt pptx odt ods txt md rtf csv json xml html"],
                 ["archive", "zip rar 7z tar gz bz2 xz iso"]].map(([k, list]) => [k, new Set(list.split(" "))]);
  const kindOf = (name) => (KINDS.find(([, set]) => set.has(ext(name))) || ["file"])[0];

  let seen = -1, touched = 0, kids, pathOf, byPath, sizes;
  /** Work out paths and folder sizes again when the list changed. */
  function index() {
    if (seen === v.seq && touched === v.since) return;
    kids = new Map(); pathOf = new Map(); byPath = new Map(); sizes = new Map();
    for (const n of v.nodes.values()) {
      if (n.trash) continue;                               // deleted: kept, but not there
      if (!kids.has(n.parent)) kids.set(n.parent, []);
      kids.get(n.parent).push(n);
    }
    const walk = (id, path) => {
      let bytes = 0, files = 0;
      for (const n of kids.get(id) || []) {
        const p = (path === "/" ? "" : path) + "/" + n.name;
        pathOf.set(n.id, p); byPath.set(p, n);
        if (n.dir) { const [b, f] = walk(n.id, p); bytes += b; files += f; } else { bytes += n.size; files += 1; }
      }
      sizes.set(id, [bytes, files]);
      return [bytes, files];
    };
    walk("", "/");
    seen = v.seq; touched = v.since;
  }
  const clean = (path) => "/" + String(path || "").split("/").filter((x) => x && x !== ".").join("/");
  function at(path, want) {
    index();
    path = clean(path);
    const n = path === "/" ? { id: "", dir: true, name: "", parent: null } : byPath.get(path);
    if (!n) throw new Fail(404, `Not found: ${path}`);
    if (want === "dir" && !n.dir) throw new Fail(400, `${path} is a file`);
    if (want === "file" && n.dir) throw new Fail(400, `${path} is a folder`);
    return n;
  }
  const live = () => { index(); return [...byPath.entries()]; };
  const liveFiles = () => live().filter(([, n]) => !n.dir);
  /** An entry as the dashboard knows one. */
  function entry(path, n) {
    const e = { name: n.name, path, dir: !!n.dir, size: n.size || 0, mtime: n.mtime || 0, state: "synced", pinned: false };
    if (n.star) e.starred = true;
    if (n.dir) { const s = sizes.get(n.id) || [0, 0]; e.size = s[0]; e.files = s[1]; }
    else if (MEDIA.has(ext(n.name))) e.thumb = n.thumb ? (n.thumb === -1 ? -1 : 1) : null;
    return e;
  }
  const objectsOf = (n) => [n.obj, ...(n.versions || []).map((x) => x.obj), n.thumb && n.thumb !== -1 ? n.thumb.obj : null].filter(Boolean);
  /** Have the server forget objects, except those something in the list still uses. */
  async function drop(objects) {
    const used = new Set();
    for (const n of v.nodes.values()) for (const o of objectsOf(n)) used.add(o);
    const gone = [...new Set(objects)].filter((o) => o && !used.has(o));
    if (gone.length) await DDV.call("/api/vault/delete", { method: "POST", json: { ids: gone } }).catch(() => null);
  }
  const under = (id) => v._under(id);
  function freeName(parent, name) {
    index();
    const taken = new Set((kids.get(parent) || []).map((n) => n.name));
    if (!taken.has(name)) return name;
    const dot = name.lastIndexOf("."), stem = dot > 0 ? name.slice(0, dot) : name, tail = dot > 0 ? name.slice(dot) : "";
    for (let i = 2; ; i++) if (!taken.has(`${stem} (${i})${tail}`)) return `${stem} (${i})${tail}`;
  }

  // ------------------------------------------------------------ reading and writing files
  /** Bytes [start, end] of a file, opened. Only the pieces that are needed are fetched. */
  async function readRange(n, start, end) {
    if (!n.size || start > end) return new Uint8Array(0);
    const key = await DDV.fileKey(DDV.unb64(n.key)), C = DDV.CHUNK, count = DDV.chunksOf(n.size);
    const first = Math.floor(start / C), last = Math.min(count - 1, Math.floor(end / C));
    let bytes = 0;
    for (let i = first; i <= last; i++) bytes += Math.min(C, n.size - i * C) + DDV.OVERHEAD;
    const raw = await DDV.call(`/api/vault/object?id=${n.obj}`, { raw: true, headers: { Range: `bytes=${DDV.HEAD + first * (C + DDV.OVERHEAD)}-${DDV.HEAD + first * (C + DDV.OVERHEAD) + bytes - 1}` } });
    const parts = [];
    let pos = 0;
    for (let i = first; i <= last; i++) {
      const want = Math.min(C, n.size - i * C) + DDV.OVERHEAD;
      parts.push(await DDV.unseal(key, raw.subarray(pos, pos + want), DDV.chunkLabel(i, i === count - 1)));
      pos += want;
    }
    const all = new Uint8Array(await new Blob(parts).arrayBuffer());
    return all.subarray(start - first * C, Math.min(all.length, end - first * C + 1));
  }
  /** Store a Blob at a path. A file that is replaced keeps what it was as an earlier version. */
  async function putFile(path, blob, mtime, heard, signal) {
    index();
    path = clean(path);
    const cut = path.lastIndexOf("/"), up = at(path.slice(0, cut) || "/", "dir"), name = path.slice(cut + 1);
    if (!name) throw new Fail(400, "missing file name");
    const old = byPath.get(path);
    if (old && old.dir) throw new Fail(400, `${path} is a folder`);
    const raw = crypto.getRandomValues(new Uint8Array(32)), obj = DDV.newId();
    await v._store(obj, await DDV.fileKey(raw), blob, heard, signal);
    let n = { id: DDV.newId(), parent: up.id, name, dir: false }, dropped = [];
    if (old) {
      n = { ...old };
      if (n.thumb && n.thumb !== -1) dropped.push(n.thumb.obj);
      delete n.thumb;
      const versions = [...(old.obj ? [{ obj: old.obj, key: old.key, size: old.size, mtime: old.mtime, at: now() }] : []), ...(old.versions || [])];
      dropped.push(...versions.slice(10).map((x) => x.obj));
      n.versions = versions.slice(0, 10);
    }
    Object.assign(n, { size: blob.size, mtime: mtime || now(), obj, key: DDV.b64(raw) });
    await v._send({ op: "put", ...n });
    await drop(dropped);
    return n;
  }
  E.snippets = new Map();                    // a note's first words, by object

  // ------------------------------------------------------------ folder syncing on the account's devices (sealed, through the server)
  const MODES = [["backup", "Back up", "up", "Copies new and changed files to your account. Files you delete on the device stay here."],
                 ["mirror", "Mirror", "up", "The folder here becomes an exact copy of the device's: deleting there deletes here too."],
                 ["two-way", "Two-way sync", "both", "Changes and deletions on either side are copied to the other. A file changed on both sides is kept twice."],
                 ["move", "Move to the drive", "up", "Copies files to your account, then deletes them on the device once they are safely stored. Frees space there."],
                 ["download", "Download backup", "down", "Copies new and changed files from your account to the device. Nothing is deleted there."],
                 ["download-mirror", "Download mirror", "down", "The device's folder becomes an exact copy of the one here. What is removed there is kept 30 days."]];
  const whenText = (j) => j.trigger === "interval" ? (j.every >= 60 ? `every ${Math.floor(j.every / 60)} h${j.every % 60 ? ` ${j.every % 60} min` : ""}` : `every ${j.every} min`)
    : j.trigger === "daily" ? `daily at ${j.at}` : j.trigger;
  async function devices() {
    const got = await DDV.call("/api/vault/sync"), out = [];
    for (const d of got.devices) {
      try {
        const info = await DDV.openMessage(v.meta, d.info) || {}, jobs = await DDV.openMessage(v.meta, d.jobs) || [], st = await DDV.openMessage(v.meta, d.status) || {};
        out.push({ id: d.id, rev: d.rev, name: info.name || "A device", windows: info.platform === "win32", home: info.home || "", version: "",
                   online: d.online, seen: d.seen, running: d.online ? st.running || null : null, raw: Array.isArray(jobs) ? jobs : [],
                   jobs: (Array.isArray(jobs) ? jobs : []).map((j) => {
                     const s = { state: "idle", ...((st.jobs || {})[j.id] || {}) };
                     if (!d.online) { delete s.progress; s.next = null; if (s.state === "running") s.state = "idle"; }
                     if (!j.enabled && s.state !== "running") s.state = "paused";
                     const m = MODES.find((x) => x[0] === j.mode) || MODES[0];
                     return { ...j, status: s, label: m[1], direction: m[2], when: whenText(j) };
                   }) });
      } catch (e) { /* sealed with another key: not this account's */ }
    }
    return out;
  }
  async function device(id) {
    const d = (await devices()).find((x) => x.id === id);
    if (!d) throw new Fail(404, "That device no longer syncs folders with this account. Turn folder syncing on again there.");
    return d;
  }
  const tell = async (d, message) => DDV.call("/api/vault/sync/tell", { method: "POST", json: { device: d.id, sealed: await DDV.sealMessage(v.meta, message) } });
  const saveJobs = async (d, jobs) => DDV.call("/api/vault/sync/jobs", { method: "POST", json: { device: d.id, rev: d.rev, jobs: await DDV.sealMessage(v.meta, jobs) } });
  const jobOf = (d, id) => { const j = d.raw.find((x) => x.id === id); if (!j) throw new Fail(404, "That folder is no longer synced"); return j; };

  // ------------------------------------------------------------ the answers
  const stats = () => {
    index();
    const files = liveFiles();
    let versions = 0, vbytes = 0;
    for (const n of v.nodes.values()) for (const x of n.versions || []) { versions++; vbytes += x.size || 0; }
    return { files: files.length, dirs: byPath.size - files.length, bytes: files.reduce((s, [, n]) => s + n.size, 0), versions, version_bytes: vbytes,
             chunks: 0, protected_chunks: 0, unsynced: 0, outbox: 0, trash: 0 };
  };
  const starred = () => live().filter(([, n]) => n.star).sort((a, b) => b[1].star - a[1].star).map(([p, n]) => entry(p, n));
  const recent = (limit) => liveFiles().filter(([p]) => !p.startsWith("/Bookmarks/")).sort((a, b) => b[1].mtime - a[1].mtime).slice(0, limit).map(([p, n]) => entry(p, n));

  const GET = {
    async status() {
      const r = await realFetch("/api/status", cred);
      if (!r.ok) return r;
      const s = await r.json();
      const names = new Map();
      for (const n of v.nodes.values()) if (n.obj) names.set(n.obj, n.name);
      s.stats = { ...s.stats, ...stats() };
      s.uploads = (s.uploads || []).map((u) => ({ ...u, path: "/" + (names.get(String(u.path || "").split("/").pop()) || "a sealed file") }));
      s.e2e = true;
      return json(s);
    },
    async home() {
      const ds = await devices().catch(() => []), jobs = ds.flatMap((d) => d.jobs);
      return { host: "", mount: "", version: (E.status || {}).version || "", stats: stats(), starred: starred().slice(0, 12), recent: recent(8), history: [],
               sync: { jobs: jobs.length, running: jobs.filter((j) => j.status.state === "running").length, problems: jobs.filter((j) => j.status.state === "error").length },
               devices: Math.max(1, ds.length), shares: 0, dav: false };
    },
    async list(q) {
      const path = clean(q.get("path")), n = at(path, "dir");
      const items = (kids.get(n.id) || []).map((c) => entry(pathOf.get(c.id), c));
      items.sort((a, b) => (b.dir - a.dir) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      return { path, items };
    },
    async search(q) {
      const term = (q.get("q") || "").trim().toLowerCase();
      if (term.length < 2) return { items: [] };
      return { items: live().filter(([, n]) => n.name.toLowerCase().includes(term)).sort((a, b) => (b[1].dir - a[1].dir) || a[1].name.localeCompare(b[1].name))
        .slice(0, 200).map(([p, n]) => entry(p, n)) };
    },
    async starred() { return { items: starred() }; },
    async recent(q) { return { items: recent(Math.max(1, Math.min(500, Number(q.get("limit")) || 100))) }; },
    async media(q) {
      const path = clean(q.get("path") || "/");
      at(path, "dir");
      const offset = Math.max(0, Number(q.get("offset")) || 0), limit = Math.max(1, Math.min(1000, Number(q.get("limit")) || 300));
      const prefix = path === "/" ? "/" : path + "/";
      const all = liveFiles().filter(([p, n]) => p.startsWith(prefix) && MEDIA.has(ext(n.name)) && !p.startsWith("/Bookmarks/")).sort((a, b) => b[1].mtime - a[1].mtime);
      return { path, total: all.length, offset, items: all.slice(offset, offset + limit).map(([p, n]) => entry(p, n)) };
    },
    async notes() {
      index();
      const folder = byPath.get("/Notes"), items = [];
      for (const c of (folder && folder.dir ? kids.get(folder.id) || [] : [])) {
        if (c.dir) continue;
        if (!E.snippets.has(c.obj)) {
          const head = await readRange(c, 0, 599).catch(() => new Uint8Array(0));
          E.snippets.set(c.obj, dec.decode(head).split(/\s+/).join(" ").slice(0, 140));
        }
        const named = /\.(md|txt)$/i.test(c.name);
        items.push({ path: "/Notes/" + c.name, name: c.name, title: named ? c.name.slice(0, c.name.lastIndexOf(".")) || c.name : c.name, mtime: c.mtime, size: c.size,
                     state: "synced", snippet: E.snippets.get(c.obj) });
      }
      items.sort((a, b) => b.mtime - a.mtime);
      return { folder: "/Notes", items };
    },
    async deleted() {
      const items = [...v.nodes.values()].filter((n) => n.trash).map((n) => ({ uid: n.id, path: n.trash.path, at: n.trash.at,
        size: n.dir ? under(n.id).reduce((s, id) => s + ((v.nodes.get(id) || {}).dir ? 0 : (v.nodes.get(id) || {}).size || 0), 0) : n.size }));
      return { items: items.sort((a, b) => b.at - a.at).slice(0, 5000) };
    },
    async versions(q) {
      const path = clean(q.get("path")), n = at(path, "file");
      return { path, current: { size: n.size, mtime: n.mtime }, versions: (n.versions || []).map((x, i) => ({ n: i + 1, at: x.at, size: x.size, reason: "changed" })) };
    },
    async insights() {
      const files = liveFiles().map(([p, n]) => entry(p, n)).sort((a, b) => b.size - a.size), kinds = new Map();
      for (const f of files) { const k = kinds.get(kindOf(f.name)) || [0, 0]; k[0]++; k[1] += f.size; kinds.set(kindOf(f.name), k); }
      const folders = live().filter(([, n]) => n.dir).map(([p, n]) => ({ path: p, bytes: (sizes.get(n.id) || [0, 0])[0], files: (sizes.get(n.id) || [0, 0])[1] }))
        .sort((a, b) => b.bytes - a.bytes).slice(0, 40);
      const s = stats();
      return { total: { files: files.length, bytes: files.reduce((t, f) => t + f.size, 0) },
               kinds: [...kinds.entries()].map(([kind, k]) => ({ kind, files: k[0], bytes: k[1] })).sort((a, b) => b.bytes - a.bytes),
               largest: files.slice(0, 40), folders, duplicates: [], duplicate_bytes: 0, versions: { count: s.versions, bytes: s.version_bytes }, spare_bytes: 0, saved: {},
               deleted: { count: [...v.nodes.values()].filter((n) => n.trash).length, bytes: 0 }, quota: (E.status || {}).quota || null };
    },
    async history() { return { items: [] }; },
    async shares() { return { items: [] }; },
    async locks() { return { count: 0, open: [], minutes: 0, mount: "", host: "" }; },
    async snapshots() { return { items: [] }; },
    async sync() {
      return { jobs: [], running: null, can_edit: false, local: false, remote_edit: false, host: "", mount: "", windows: false, home: "", account: true,
               devices: await devices(), modes: MODES.map(([id, label, direction, text]) => ({ id, label, direction, text })),
               triggers: [["live", "As soon as something changes"], ["interval", "Every few minutes / hours"], ["daily", "Once a day"], ["manual", "Only when I start it"]].map(([id, label]) => ({ id, label })),
               default_exclude: ["*.crdownload", "*.part", "*.partial", "*.download", "*.tmp", "~$*"] };
    },
    async "sync/browse"(q) {
      const d = await device(q.get("device"));
      const r = await DDV.call("/api/vault/sync/ask", { method: "POST", json: { device: d.id, sealed: await DDV.sealMessage(v.meta, { do: "browse", path: q.get("path") || "" }) } });
      const data = await DDV.openMessage(v.meta, r.sealed);
      if (!data || data.error) throw new Fail(400, (data && data.error) || "That device didn't answer.");
      return data;
    },
    async file(q, init) {
      const n = at(q.get("path"), "file"), range = /bytes=(\d*)-(\d*)/.exec((init.headers && (init.headers.Range || init.headers.range)) || "");
      let start = 0, end = n.size - 1, status = 200;
      if (range && n.size) {
        if (range[1] === "" && range[2]) start = Math.max(0, n.size - Number(range[2]));
        else { start = Number(range[1] || 0); end = range[2] ? Math.min(n.size - 1, Number(range[2])) : n.size - 1; }
        if (start > end || start >= n.size) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${n.size}` } });
        status = 206;
      }
      const data = await readRange(n, start, end);
      const headers = { "Content-Type": typeOf(n.name), "Accept-Ranges": "bytes" };
      if (status === 206) headers["Content-Range"] = `bytes ${start}-${end}/${n.size}`;
      return new Response(data, { status, headers });
    },
    async thumb(q) {
      const n = at(q.get("path"), "file");
      if (!n.thumb || n.thumb === -1) throw new Fail(404, "no thumbnail yet");
      const data = await readRange({ obj: n.thumb.obj, key: n.thumb.key, size: n.thumb.size }, 0, n.thumb.size - 1);
      return new Response(data, { headers: { "Content-Type": data[0] === 0x52 ? "image/webp" : data[0] === 0x89 ? "image/png" : "image/jpeg", "Cache-Control": "private, max-age=2592000" } });
    },
  };

  const POST = {
    async mkdir(b) {
      const path = clean(b.path), cut = path.lastIndexOf("/");
      const up = at(path.slice(0, cut) || "/", "dir");
      if (byPath.has(path)) throw new Fail(409, "Something with that name already exists");
      await v._send({ op: "put", id: DDV.newId(), parent: up.id, name: path.slice(cut + 1), dir: true, size: 0, mtime: now() });
      return { ok: true, path };
    },
    async move(b) {
      const n = at(b.from), to = clean(b.to), cut = to.lastIndexOf("/");
      if (!n.id) throw new Fail(400, "The account itself can't be moved");
      if (byPath.has(to)) throw new Fail(409, "Something with that name already exists");
      const up = at(to.slice(0, cut) || "/", "dir");
      if (under(n.id).includes(up.id)) throw new Fail(400, "A folder can't be moved into itself");
      await v._send({ op: "move", id: n.id, parent: up.id, name: to.slice(cut + 1) });
      return { ok: true, path: to };
    },
    async copy(b) {                              // the same sealed content under a second name: nothing is sent again
      const n = at(b.from, "file"), to = clean(b.to), cut = to.lastIndexOf("/");
      if (byPath.has(to)) throw new Fail(409, "Something with that name already exists");
      const up = at(to.slice(0, cut) || "/", "dir");
      await v._send({ op: "put", id: DDV.newId(), parent: up.id, name: to.slice(cut + 1), dir: false, size: n.size, mtime: n.mtime, obj: n.obj, key: n.key });
      return { ok: true, path: to };
    },
    async delete(b) {                            // kept under Deleted until it is purged there
      const path = clean(b.path), n = at(path);
      if (!n.id) throw new Fail(400, "The account itself can't be deleted");
      await v._send({ op: "set", id: n.id, fields: { trash: { at: now(), path } } });
      return { ok: true };
    },
    async star(b) {
      const n = at(b.path);
      if (!n.id) throw new Fail(400, "The account itself can't be starred");
      await v._send({ op: "set", id: n.id, fields: { star: b.on ? now() : null } });
      return { ok: true };
    },
    async undelete(b) {
      index();
      const want = b.uids ? new Set(b.uids.map(String)) : null, path = clean(b.path);
      const rows = [...v.nodes.values()].filter((n) => n.trash && (want ? want.has(n.id) : n.trash.path.toLowerCase() === path.toLowerCase())).slice(0, want ? undefined : 1);
      if (!rows.length) throw new Fail(404, "Those files are no longer kept");
      const ops = [];
      for (const n of rows) {
        const parentLive = n.parent === "" || pathOf.has(n.parent);
        const parent = parentLive ? n.parent : "", name = freeName(parent, n.name);
        ops.push({ op: "set", id: n.id, fields: { trash: null } });
        if (parent !== n.parent || name !== n.name) ops.push({ op: "move", id: n.id, parent, name });
      }
      await v._send(...ops);
      return { ok: true, count: rows.length };
    },
    async purge(b) {
      const want = b.uids ? new Set(b.uids.map(String)) : null, path = clean(b.path);
      const rows = [...v.nodes.values()].filter((n) => n.trash && (want ? want.has(n.id) : n.trash.path.toLowerCase() === path.toLowerCase()));
      const objects = rows.flatMap((n) => under(n.id).flatMap((id) => objectsOf(v.nodes.get(id) || {})));
      if (rows.length) { await v._send(...rows.map((n) => ({ op: "del", id: n.id }))); await drop(objects); }
      return { ok: true, count: rows.length };
    },
    async "versions/restore"(b) {
      const path = clean(b.path), n = at(path, "file"), i = Number(b.n) - 1, was = (n.versions || [])[i];
      if (!was) throw new Fail(400, "no such version");
      if (b.copy) {
        const dot = n.name.lastIndexOf("."), name = dot > 0 ? `${n.name.slice(0, dot)} (version ${i + 1})${n.name.slice(dot)}` : `${n.name} (version ${i + 1})`;
        await v._send({ op: "put", id: DDV.newId(), parent: n.parent, name: freeName(n.parent, name), dir: false, size: was.size, mtime: was.mtime, obj: was.obj, key: was.key });
      } else {                                   // it becomes the file again; what the file was goes to the top of the earlier versions
        const versions = [{ obj: n.obj, key: n.key, size: n.size, mtime: n.mtime, at: now() }, ...(n.versions || []).filter((_, k) => k !== i)].slice(0, 10);
        const next = { ...n, size: was.size, mtime: was.mtime, obj: was.obj, key: was.key, versions };
        const old = n.thumb && n.thumb !== -1 ? [n.thumb.obj] : [];
        delete next.thumb;
        await v._send({ op: "put", ...next });
        await drop(old);
      }
      return { ok: true };
    },
    async upload(b, q, init) {
      const body = init.body == null ? new Blob([]) : init.body instanceof Blob ? init.body : new Blob([init.body]);
      const n = await putFile(q.get("path"), body);
      return { ok: true, path: clean(q.get("path")), size: body.size, mtime: n.mtime };
    },
    async thumb(b, q, init) {
      const n = at(q.get("path"), "file"), body = init.body instanceof Blob ? init.body : new Blob([init.body || ""]);
      const old = n.thumb && n.thumb !== -1 ? [n.thumb.obj] : [];
      let thumb = -1;                            // an empty body: none can be made, don't try again
      if (body.size) {
        if (body.size > 400 * 1024) throw new Fail(413, "thumbnail too large");
        const raw = crypto.getRandomValues(new Uint8Array(32)), obj = DDV.newId();
        await v._store(obj, await DDV.fileKey(raw), body);
        thumb = { obj, key: DDV.b64(raw), size: body.size };
      }
      await v._send({ op: "set", id: n.id, fields: { thumb } });
      await drop(old);
      return { ok: true };
    },
    async "sync/save"(b) {
      const d = await device(b.device), g = b.job || {}, old = g.id ? jobOf(d, g.id) : null;
      const j = { name: "", local: "", remote: "", mode: "backup", trigger: "live", every: 60, at: "03:00", exclude: [], enabled: true, ...(old || {}), ...g };
      j.id = j.id || DDV.newId().slice(0, 6);
      j.local = String(j.local || "").trim().replace(/^"|"$/g, "");
      j.remote = clean(String(j.remote || "").replace(/^[A-Za-z]:/, "").replace(/\\/g, "/"));
      j.every = Math.max(1, Math.round(Number(j.every) || 60));
      j.exclude = (Array.isArray(j.exclude) ? j.exclude : String(j.exclude || "").split(/[,\n]/)).map((x) => String(x).trim()).filter(Boolean);
      j.name = String(j.name || "").trim().slice(0, 80) || j.local.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || j.local;
      if (!MODES.some((m) => m[0] === j.mode)) throw new Fail(400, `Unknown mode '${j.mode}'.`);
      if (!j.local) throw new Fail(400, "Choose a folder on that device.");
      if (j.remote === "/" && ["mirror", "two-way"].includes(j.mode)) throw new Fail(400, "Choose a folder in your account (not all of it) for mirror and two-way sync.");
      if (/^([A-Za-z]:[\\/]*|[\\/]+)$/.test(j.local) && ["download-mirror", "two-way", "move"].includes(j.mode)) throw new Fail(400, "Choose a folder, not a whole disk, for this mode.");
      if (!/^\d\d:\d\d$/.test(j.at)) throw new Fail(400, "The time must look like 03:00 (24-hour clock).");
      if (d.raw.some((x) => x.id !== j.id && x.local === j.local && x.remote === j.remote)) throw new Fail(400, "Those two folders are already paired.");
      await saveJobs(d, old ? d.raw.map((x) => x.id === j.id ? j : x) : [...d.raw, j]);
      if (!old && j.trigger !== "manual" && d.online) setTimeout(() => tell(d, { do: "run", id: j.id }).catch(() => null), 1500);
      return { ok: true, job: j };
    },
    async "sync/remove"(b) { const d = await device(b.device); jobOf(d, b.id); await saveJobs(d, d.raw.filter((x) => x.id !== b.id)); return { ok: true }; },
    async "sync/pause"(b) {
      const d = await device(b.device), j = jobOf(d, b.id);
      if (b.paused && d.online) await tell(d, { do: "stop", id: j.id }).catch(() => null);
      await saveJobs(d, d.raw.map((x) => x.id === j.id ? { ...x, enabled: !b.paused } : x));
      return { ok: true };
    },
    async "sync/run"(b) { const d = await device(b.device), j = jobOf(d, b.id); await tell(d, { do: "run", id: j.id, again: !!b.again }); return { ok: true, queued: !!d.running && d.running !== j.id }; },
    async "sync/stop"(b) { const d = await device(b.device); await tell(d, { do: "stop", id: jobOf(d, b.id).id }); return { ok: true }; },
    async logout() {
      await DDV.keys.clear().catch(() => null);
      if (navigator.serviceWorker) for (const r of await navigator.serviceWorker.getRegistrations().catch(() => [])) await r.unregister().catch(() => null);
      return realFetch("/api/logout", { method: "POST", ...cred, headers: { "X-DD": "1", "Content-Type": "application/json" }, body: "{}" });
    },
  };
  const PASS = /^(account|account\/delete|changelog|vault(\/.*)?)$/;        // about the account itself: the server's to answer

  async function answer(url, init) {
    const name = url.pathname.slice(5).replace(/\/+$/, ""), method = (init.method || "GET").toUpperCase();
    if (PASS.test(name)) return realFetch(url, init);
    const fn = (method === "GET" ? GET : POST)[name];
    try {
      if (!fn) throw new Fail(404, "That isn't there for end-to-end encrypted accounts yet.");
      let body = {};
      if (method === "POST" && typeof init.body === "string" && /json/.test((init.headers || {})["Content-Type"] || "")) body = JSON.parse(init.body || "{}");
      const out = method === "GET" ? await fn(url.searchParams, init) : await fn(body, url.searchParams, init);
      return out instanceof Response ? out : json(out);
    } catch (e) {
      if (e instanceof DDV.Gone) return json({ error: e.message }, 401);
      return json({ error: e.message || "That didn't work" }, e.status || 500);
    }
  }

  // ------------------------------------------------------------ what app.js uses directly
  /** Stands in for the request app.js uploads a file with (it reports how far it is as the real one does). */
  E.xhr = function () {
    const x = { upload: {}, status: 0, responseText: "", setRequestHeader() {}, open(method, url) { x.url = new URL(url, location.href); },
      send(file) {
        putFile(x.url.searchParams.get("path"), file, (file.lastModified || Date.now()) / 1000,
                (p) => { if (p.bytes !== undefined && x.upload.onprogress) x.upload.onprogress({ lengthComputable: true, loaded: p.bytes, total: file.size || 1 }); })
          .then(() => { x.status = 200; x.responseText = '{"ok":true}'; if (x.onload) x.onload(); },
                (e) => { x.status = e.status || 500; x.responseText = JSON.stringify({ error: e.message }); if (x.onload) x.onload(); });
      } };
    return x;
  };
  /** A download app.js asks for by address: opened here and saved (straight to disk where the browser can). True when it was taken on. */
  E.download = function (address, name) {
    const url = new URL(address, location.href);
    if (url.pathname === "/api/zip") { E.say("Downloading a folder as a ZIP isn't there for end-to-end encrypted accounts yet. Download its files one by one."); return true; }
    if (url.pathname !== "/api/file") return false;
    (async () => {
      const n = at(url.searchParams.get("path"), "file"), C = DDV.CHUNK * 8;
      try {
        if (window.showSaveFilePicker && n.size > 64 * 1024 * 1024) {
          let handle;
          try { handle = await window.showSaveFilePicker({ suggestedName: name || n.name }); } catch (e) { return; }
          const out = await handle.createWritable();
          try { for (let pos = 0; pos < n.size; pos += C) await out.write(await readRange(n, pos, Math.min(n.size, pos + C) - 1)); await out.close(); }
          catch (e) { await out.abort().catch(() => null); throw e; }
        } else {
          if (n.size > 1500 * 1024 * 1024) throw new Error("This browser can only download files this big with Chrome or Edge, which write them straight to disk.");
          const parts = [];
          for (let pos = 0; pos < n.size; pos += C) parts.push(await readRange(n, pos, Math.min(n.size, pos + C) - 1));
          const blob = URL.createObjectURL(new Blob(parts, { type: "application/octet-stream" }));
          const a = document.createElement("a");
          a.href = blob; a.download = name || n.name; a.style.display = "none";
          document.body.append(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(blob), 60000);
        }
        E.say(`Downloaded ${name || n.name}`);
      } catch (e) { E.say(e.message || "The download failed"); }
    })();
    return true;
  };
  E.say = (text) => { if (typeof window.toast === "function") window.toast(text); else console.log(text); };

  // ------------------------------------------------------------ pictures and videos shown by address (through sw.js)
  if (navigator.serviceWorker) navigator.serviceWorker.addEventListener("message", async (e) => {
    const m = e.data || {}, port = e.ports && e.ports[0];
    if (m.type !== "e2e-get" || !port) return;
    await E.ready.catch(() => null);
    if (!E.active) return port.postMessage({ pass: true });
    try {
      const url = new URL(m.url), n = at(url.searchParams.get("path"), "file");
      if (url.pathname === "/api/thumb") {
        const r = await GET.thumb(url.searchParams);
        const body = await r.arrayBuffer();
        return port.postMessage({ status: 200, headers: { "Content-Type": r.headers.get("Content-Type"), "Cache-Control": "private, max-age=86400" }, body }, [body]);
      }
      const size = n.size, type = typeOf(n.name), range = /bytes=(\d*)-(\d*)/.exec(m.range || "");
      const headers = { "Content-Type": type, "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store" };
      if (/html|svg|xml/.test(type)) headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'";
      let start = 0, end = size - 1, status = 200;
      if (range && size) {
        start = range[1] === "" ? Math.max(0, size - Number(range[2])) : Number(range[1]);
        end = range[1] !== "" && range[2] ? Math.min(size - 1, Number(range[2])) : size - 1;
        if (start >= size) return port.postMessage({ status: 416, headers: { "Content-Range": `bytes */${size}` } });
        end = Math.min(end, start + 4 * DDV.CHUNK - 1);       // a few pieces at a time: the player asks for the next when it wants them
        status = 206;
        headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
      }
      const data = await readRange(n, start, end);
      const body = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      port.postMessage({ status, headers, body }, [body]);
    } catch (err) {
      port.postMessage({ status: err.status || 500, headers: { "Content-Type": "text/plain" } });
    }
  });

  // ------------------------------------------------------------ starting
  function unlock(problem) {
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;inset:0;z-index:100;display:grid;place-items:center;background:var(--bg);padding:24px";
    box.innerHTML = "<form><h1>Your 24 words</h1><p class='muted'>This browser doesn't have the key to your files (any more). " +
      "The words are used here to open them; they are not sent anywhere.</p><p class='err' role='alert' hidden></p>" +
      "<textarea rows='4' autocomplete='off' autocapitalize='none' spellcheck='false' placeholder='the 24 words, in order'></textarea>" +
      "<button class='btn' type='submit'>Open my files</button> <button class='btn ghost' type='button'>Sign out</button></form>";
    box.querySelector("form").style.cssText = "max-width:420px;width:100%";
    box.querySelector("textarea").style.cssText = "width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:14px/1.5 var(--mono);margin:6px 0 14px";
    const err = box.querySelector(".err"), form = box.querySelector("form");
    if (problem) { err.textContent = problem; err.hidden = false; }
    box.querySelector(".ghost").addEventListener("click", async () => { await POST.logout().catch(() => null); location.replace("/"); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const d = await DDV.derive(await DDV.phraseToKey(box.querySelector("textarea").value));
        const r = await realFetch("/login", { method: "POST", ...cred, body: new URLSearchParams({ auth: d.auth }) });
        if (!(r.ok && r.redirected)) throw new Error("these are not this account's words");
        await DDV.keys.put(d.meta);
        location.reload();
      } catch (e2) { err.textContent = `That didn't open it: ${e2.message}.`; err.hidden = false; }
    });
    document.body.append(box);
    return new Promise(() => {});              // nothing else happens until the page is loaded again
  }

  E.ready = (async () => {
    if (!window.DDV) return;
    let s;
    try { const r = await realFetch("/api/status", cred); if (!r.ok) return; s = await r.json(); } catch (e) { return; }
    if (!s.e2e) return;
    E.status = s;
    if (!DDV.usable) { document.body.innerHTML = '<p style="padding:40px">An end-to-end encrypted account can only be opened on an https address (or on the computer itself): a browser doesn\'t encrypt on plain http.</p>'; return new Promise(() => {}); }
    const meta = await DDV.keys.get().catch(() => null);
    if (!meta) return unlock("");
    const vault = new DDV.Vault(meta);
    try { await vault.sync(); }
    catch (e) {
      if (e instanceof DDV.Gone) { location.reload(); return new Promise(() => {}); }
      return unlock(/can't be opened/.test(e.message) ? "The key kept in this browser is for another account." : `Your files could not be loaded: ${e.message}`);
    }
    v = E.vault = vault;
    if (navigator.serviceWorker) {
      try {
        await navigator.serviceWorker.register("/sw.js");
        await navigator.serviceWorker.ready;
        if (!navigator.serviceWorker.controller) await new Promise((res) => { navigator.serviceWorker.addEventListener("controllerchange", res, { once: true }); setTimeout(res, 3000); });
      } catch (e) { /* pictures are then shown without it where they can be */ }
    }
    E.active = true;
    // what was deleted more than 30 days ago goes for good
    const old = [...v.nodes.values()].filter((n) => n.trash && n.trash.at < now() - 30 * 86400).map((n) => n.id);
    if (old.length) POST.purge({ uids: old }).catch(() => null);
    setInterval(() => v.sync().catch(() => null), 15000);          // what another device of this account did shows up by itself
    document.addEventListener("visibilitychange", () => { if (!document.hidden) v.sync().catch(() => null); });
  })();

  window.fetch = async function (input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    // (what is about the account itself, sealed things included, goes straight to the server: also while this is still starting)
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/") || PASS.test(url.pathname.slice(5).replace(/\/+$/, ""))) return realFetch(input, init);
    await E.ready.catch(() => null);
    if (!E.active) return realFetch(input, init);
    return answer(url, init || {});
  };
})();
