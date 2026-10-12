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
    const used = await snapKeeps();            // (what a snapshot still refers to stays)
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
    let n = { id: DDV.newId(), parent: up.id, name, dir: false, at: now(), by: ME }, dropped = [];
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
                   fetches: d.online ? (st.fetches || []).map((f) => ({ id: f.id, name: f.name, url: f.url, state: f.state, bytes: f.bytes, total: f.total,
                                                                         folder: f.folder, error: f.error })) : [],
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
      s.fetches = E.fetches || [];
      devices().then((ds) => { E.fetches = ds.flatMap((d) => d.fetches); }, () => null);
      return json(s);
    },
    async home() {
      const ds = await devices().catch(() => []), jobs = ds.flatMap((d) => d.jobs);
      return { host: "", mount: "", version: (E.status || {}).version || "", stats: stats(), starred: starred().slice(0, 12), recent: recent(8), history: activity().slice(0, 8),
               sync: { jobs: jobs.length, running: jobs.filter((j) => j.status.state === "running").length, problems: jobs.filter((j) => j.status.state === "error").length },
               devices: Math.max(1, ds.length), shares: await DDV.call("/api/vault/shares").then((r) => r.items.length, () => 0), dav: false };
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
    async locks() { return { count: 0, open: [], minutes: 0, mount: "", host: "" }; },
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
      await v._send({ op: "put", id: DDV.newId(), parent: up.id, name: path.slice(cut + 1), dir: true, size: 0, mtime: now(), at: now(), by: ME });
      return { ok: true, path };
    },
    async move(b) {
      const n = at(b.from), to = clean(b.to), cut = to.lastIndexOf("/");
      if (!n.id) throw new Fail(400, "The account itself can't be moved");
      if (byPath.has(to)) throw new Fail(409, "Something with that name already exists");
      const up = at(to.slice(0, cut) || "/", "dir");
      if (under(n.id).includes(up.id)) throw new Fail(400, "A folder can't be moved into itself");
      await v._send({ op: "move", id: n.id, parent: up.id, name: to.slice(cut + 1) }, { op: "set", id: n.id, fields: { moved: { at: now(), from: clean(b.from) } } });
      return { ok: true, path: to };
    },
    async copy(b) {                              // the same sealed content under a second name: nothing is sent again
      const n = at(b.from, "file"), to = clean(b.to), cut = to.lastIndexOf("/");
      if (byPath.has(to)) throw new Fail(409, "Something with that name already exists");
      const up = at(to.slice(0, cut) || "/", "dir");
      await v._send({ op: "put", id: DDV.newId(), parent: up.id, name: to.slice(cut + 1), dir: false, size: n.size, mtime: n.mtime, obj: n.obj, key: n.key, at: now(), by: ME });
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
  // ------------------------------------------------------------ a small file kept in the account (the address book, the bookmarks)
  async function readText(path) {
    index();
    const n = byPath.get(path);
    return n && !n.dir ? dec.decode(await readRange(n, 0, n.size - 1)) : "";
  }
  async function ensureFolder(path) {
    index();
    let made = "";
    for (const part of path.split("/").filter(Boolean)) {
      const up = made || "/";
      made += "/" + part;
      if (!byPath.has(made)) { await POST.mkdir({ path: made }); index(); }
      else if (!byPath.get(made).dir) throw new Fail(400, `${made} is a file`);
      void up;
    }
  }
  async function writeText(path, text) {
    await ensureFolder(path.slice(0, path.lastIndexOf("/")));
    await putFile(path, new Blob([text]));
  }
  async function removePath(path) { index(); const n = byPath.get(path); if (n) await v._send({ op: "set", id: n.id, fields: { trash: { at: now(), path } } }); }

  // ------------------------------------------------------------ something one of the account's own devices has to do
  // Reading a web page for a bookmark, saving a file from a web address: the server does that for other
  // accounts, and may not for this one (it would see what is fetched). A device of the account that is
  // on does it instead, asked through the server in a sealed message.
  async function helper() {
    const d = (await devices()).find((x) => x.online);
    if (!d) throw new Fail(409, "That needs one of your own devices to be on (a computer with DDrive Lite, or the phone app, with folder syncing turned on): " +
                                "the server isn't allowed to see what is fetched.");
    return d;
  }
  async function askDevice(message, wait) {
    const d = await helper();
    const r = await DDV.call("/api/vault/sync/ask", { method: "POST", json: { device: d.id, wait, sealed: await DDV.sealMessage(v.meta, message) } });
    return DDV.openMessage(v.meta, r.sealed);
  }

  // ------------------------------------------------------------ contacts: one vCard file, /Contacts/Contacts.vcf (as contacts.py)
  const BOOK = "/Contacts/Contacts.vcf";
  const newContact = () => ({ uid: DDV.newId().slice(0, 16), name: "", first: "", last: "", org: "", title: "", bday: "", note: "", phones: [], emails: [], addresses: [], urls: [], photo: "", extra: [] });
  const vUn = (s) => s.replace(/\\n/gi, "\n").replace(/\\([,;\\])/g, "$1");
  const vEsc = (s) => String(s || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/([,;])/g, "\\$1");
  const vSplit = (s, sep) => { const out = []; let cur = ""; for (let i = 0; i < s.length; i++) { if (s[i] === "\\" && i + 1 < s.length) { cur += s[i] + s[++i]; } else if (s[i] === sep) { out.push(cur); cur = ""; } else cur += s[i]; } out.push(cur); return out; };
  const LABELS = { cell: "mobile", mobile: "mobile", home: "home", work: "work", fax: "fax", main: "main", pager: "pager", other: "other" };
  const TYPES_V = { mobile: "CELL", home: "HOME", work: "WORK", fax: "FAX", main: "MAIN", pager: "PAGER", other: "OTHER" };
  function vLabel(params) {
    for (const p of params) for (const t of p.replace(/^type=/i, "").split(",")) if (LABELS[t.toLowerCase()]) return LABELS[t.toLowerCase()];
    return "";
  }
  function parseVcards(text) {
    const lines = [];
    for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) { if (/^[ \t]/.test(raw) && lines.length) lines[lines.length - 1] += raw.slice(1); else if (raw) lines.push(raw); }
    const out = [];
    let cur = null;
    for (const line of lines) {
      const colon = line.indexOf(":");
      if (colon < 0) continue;
      const head = line.slice(0, colon).split(";"), value = line.slice(colon + 1), params = head.slice(1);
      const name = head[0].split(".").pop().toUpperCase();
      if (name === "BEGIN" && value.toUpperCase() === "VCARD") { cur = newContact(); cur.uid = ""; continue; }
      if (!cur) continue;
      if (name === "END") {
        if (!cur.uid) cur.uid = DDV.newId().slice(0, 16);
        if (!cur.name) cur.name = [cur.first, cur.last].filter(Boolean).join(" ") || cur.org || (cur.emails[0] || cur.phones[0] || {}).value || "";
        if (cur.name || cur.phones.length || cur.emails.length) out.push(cur);
        cur = null;
      } else if (name === "FN") cur.name = vUn(value).trim();
      else if (name === "N") { const f = vSplit(value, ";").map(vUn).concat(["", "", "", "", ""]); cur.last = f[0].trim(); cur.first = [f[1], f[2]].filter(Boolean).join(" ").trim(); }
      else if (name === "TEL") { const t = value.replace(/^tel:/i, ""); if (t.trim()) cur.phones.push({ label: vLabel(params), value: vUn(t).trim() }); }
      else if (name === "EMAIL") { if (value.trim()) cur.emails.push({ label: vLabel(params), value: vUn(value).trim() }); }
      else if (name === "ADR") { const f = vSplit(value, ";").map((x) => vUn(x).trim()).concat(["", "", "", "", "", "", ""]);
        cur.addresses.push({ label: vLabel(params), street: [f[2], f[1], f[0]].filter(Boolean).join(", "), city: f[3], region: f[4], postcode: f[5], country: f[6] }); }
      else if (name === "ORG") cur.org = vSplit(value, ";").map((x) => vUn(x).trim()).filter(Boolean).join(" ");
      else if (name === "TITLE") cur.title = vUn(value).trim();
      else if (name === "BDAY") { const m = /(\d{4})-?(\d{2})-?(\d{2})/.exec(value); cur.bday = m ? `${m[1]}-${m[2]}-${m[3]}` : ""; }
      else if (name === "NOTE") cur.note = vUn(value);
      else if (name === "URL") cur.urls.push(vUn(value).trim());
      else if (name === "UID") cur.uid = value.replace(/[^\w.-]/g, "").slice(0, 64);
      else if (name === "PHOTO") {
        const type = (params.map((p) => /^type=(\w+)/i.exec(p)).find(Boolean) || [])[1];
        if (/^data:image\//i.test(value)) cur.photo = value;
        else if (params.some((p) => /^encoding=b|^base64$/i.test(p)) && value.length < 400000) cur.photo = `data:image/${(type || "jpeg").toLowerCase()};base64,${value.replace(/\s+/g, "")}`;
      } else if (!["VERSION", "PRODID", "REV"].includes(name)) cur.extra.push(line);
    }
    return out;
  }
  function toVcard(c) {
    const lines = ["BEGIN:VCARD", "VERSION:3.0", "PRODID:-//DDrive//Contacts//EN", `UID:${c.uid || DDV.newId().slice(0, 16)}`];
    lines.push(`FN:${vEsc(c.name || [c.first, c.last].filter(Boolean).join(" ") || c.org || "No name")}`, `N:${vEsc(c.last)};${vEsc(c.first)};;;`);
    if (c.org) lines.push(`ORG:${vEsc(c.org)}`);
    if (c.title) lines.push(`TITLE:${vEsc(c.title)}`);
    const type = (label) => TYPES_V[String(label || "").toLowerCase()];
    for (const p of c.phones || []) if (p.value) lines.push(`TEL${type(p.label) ? ";TYPE=" + type(p.label) : ""}:${vEsc(p.value)}`);
    for (const e of c.emails || []) if (e.value) lines.push(`EMAIL;TYPE=INTERNET${type(e.label) ? "," + type(e.label) : ""}:${vEsc(e.value)}`);
    for (const a of c.addresses || []) if (["street", "city", "region", "postcode", "country"].some((k) => a[k]))
      lines.push(`ADR${type(a.label) ? ";TYPE=" + type(a.label) : ""}:;;${vEsc(a.street)};${vEsc(a.city)};${vEsc(a.region)};${vEsc(a.postcode)};${vEsc(a.country)}`);
    for (const u of c.urls || []) if (u) lines.push(`URL:${u}`);
    if (c.bday) lines.push(`BDAY:${c.bday}`);
    if (c.note) lines.push(`NOTE:${vEsc(c.note)}`);
    const m = /^data:image\/(\w+);base64,(.+)$/s.exec(c.photo || "");
    if (m) lines.push(`PHOTO;ENCODING=b;TYPE=${m[1].toUpperCase()}:${m[2].trim()}`);
    lines.push(...(c.extra || []).filter(Boolean), "END:VCARD");
    return lines.map((l) => { let out = l.slice(0, 75); for (let i = 75; i < l.length; i += 74) out += "\r\n " + l.slice(i, i + 74); return out; }).join("\r\n") + "\r\n";
  }
  const readBook = async () => parseVcards(await readText(BOOK)).sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  const writeBook = (items) => writeText(BOOK, items.map(toVcard).join(""));
  const digits = (c) => new Set([...(c.phones || []).map((p) => p.value.replace(/\D/g, "").slice(-9)), ...(c.emails || []).map((e) => e.value.toLowerCase())]);
  const samePerson = (a, b) => { if ((a.name || "").trim().toLowerCase() !== (b.name || "").trim().toLowerCase()) return false; const x = digits(a), y = digits(b); return (!x.size && !y.size) || [...x].some((k) => y.has(k)); };

  GET.contacts = async () => ({ items: (await readBook()).map(({ extra, ...c }) => c), file: BOOK });
  POST["contacts/save"] = async (b) => {
    const c = b.contact || {};
    if (![c.name, c.first, c.last, c.org].some(Boolean) && !(c.phones || []).length && !(c.emails || []).length) throw new Fail(400, "Give the contact a name, a phone number or an email");
    const items = await readBook(), old = items.find((x) => x.uid === c.uid);
    const { extra, ...given } = c;
    const merged = { ...newContact(), ...(old || {}), ...given };
    if (!merged.name) merged.name = [merged.first, merged.last].filter(Boolean).join(" ") || merged.org || "";
    await writeBook([...items.filter((x) => x.uid !== merged.uid), merged]);
    const { extra: gone, ...shown } = merged;
    return { ok: true, contact: shown };
  };
  POST["contacts/delete"] = async (b) => {
    const uids = new Set((b.uids || []).map(String)), items = await readBook(), keep = items.filter((x) => !uids.has(x.uid));
    if (keep.length !== items.length) await writeBook(keep);
    return { ok: true, removed: items.length - keep.length };
  };
  POST["contacts/import"] = async (b, q, init) => {
    const text = await (init.body instanceof Blob ? init.body : new Blob([init.body || ""])).text();
    const found = parseVcards(text);
    if (!found.length) throw new Fail(400, /BEGIN:VCARD/i.test(text) ? "No contacts found in that file." : "For an end-to-end encrypted account, import a vCard (.vcf) file. Google and Outlook can export one.");
    const items = await readBook(), uids = new Set(items.map((x) => x.uid));
    let added = 0, skipped = 0;
    for (const c of found) {
      if (items.some((x) => samePerson(c, x))) { skipped++; continue; }
      if (uids.has(c.uid)) c.uid = DDV.newId().slice(0, 16);
      uids.add(c.uid); items.push(c); added++;
    }
    if (added) await writeBook(items);
    return { ok: true, added, skipped };
  };

  // ------------------------------------------------------------ bookmarks: /Bookmarks/Bookmarks.json and the pictures next to it (as bookmarks.py)
  const MARKS = "/Bookmarks/Bookmarks.json", IMAGES = "/Bookmarks/images";
  function normalizeUrl(text) {
    let url = String(text || "").trim();
    if (!url || /[\s<>"]/.test(url) || url.length > 2000) throw new Fail(400, "That doesn't look like a web address");
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(url)) url = "https://" + url;
    let u;
    try { u = new URL(url); } catch (e) { throw new Fail(400, "That doesn't look like a web address"); }
    if (!["http:", "https:"].includes(u.protocol) || (!u.hostname.includes(".") && u.hostname !== "localhost")) throw new Fail(400, "That doesn't look like a web address");
    u.hash = "";
    return u.href;
  }
  const pageKey = (url) => { const u = new URL(url); return [u.hostname.replace(/^www\./, "").toLowerCase(), u.pathname.replace(/\/+$/, ""), u.search].join("|"); };
  const readMarks = async () => { try { return (JSON.parse(await readText(MARKS) || "{}").items || []).filter((m) => m && m.id && m.url); } catch (e) { return []; } };
  const writeMarks = (items) => writeText(MARKS, JSON.stringify({ v: 1, items }, null, 1));
  /** Put a preview's pictures into the account and its words into the bookmark. */
  async function storePreview(mark, p) {
    for (const key of ["title", "description", "site", "color"]) if (p[key] && (key !== "title" || !mark.edited)) mark[key] = p[key];
    for (const [key, suffix] of [["image", ""], ["icon", "-icon"]]) {
      if (!p[key] || !p[key].data) continue;
      const old = mark[key], path = `${IMAGES}/${mark.id}${suffix}.${p[key].ext}`;
      await ensureFolder(IMAGES);
      await putFile(path, new Blob([DDV.unb64(p[key].data)]));
      mark[key] = path;
      if (old && old !== path) await removePath(old);
    }
  }
  const noPreview = "saved without a picture: turn on one of your devices (a computer with DDrive Lite, or the phone app) and press “Read the preview again”";
  GET.bookmarks = async () => ({ items: await readMarks(), folder: "/Bookmarks" });
  POST["bookmarks/add"] = async (b) => {
    const url = normalizeUrl(b.url), items = await readMarks(), old = items.find((m) => pageKey(m.url) === pageKey(url));
    if (old) return { ok: true, bookmark: old, existed: true };
    const host = new URL(url).hostname;
    const mark = { id: DDV.newId().slice(0, 12), url, title: "", description: "", site: host.replace(/^www\./, ""), color: "", image: "", icon: "",
                   tags: (b.tags || []).map((t) => String(t).trim().slice(0, 40)).filter(Boolean).slice(0, 12), note: "", added: now() };
    const p = await askDevice({ do: "preview", url }, 45).catch(() => null);
    if (p && !p.error) await storePreview(mark, p);
    if (!mark.title) mark.title = host;
    const again = await readMarks();
    if (!again.some((m) => pageKey(m.url) === pageKey(url))) { again.unshift(mark); await writeMarks(again); }
    return { ok: true, bookmark: mark, preview: !!(p && (p.title || p.image)), note: p && !p.error ? "" : noPreview };
  };
  POST["bookmarks/save"] = async (b) => {
    const items = await readMarks(), mark = items.find((m) => m.id === b.id);
    if (!mark) throw new Fail(404, "That bookmark is gone");
    const tidy = (text, n) => String(text || "").split(/\s+/).join(" ").trim().slice(0, n);
    if ("title" in b) { const t = tidy(b.title, 300); if (t && t !== mark.title) { mark.title = t; mark.edited = true; } }
    if ("description" in b) mark.description = tidy(b.description, 600);
    if ("pinned" in b) { if (b.pinned) mark.pinned = now(); else delete mark.pinned; }
    if ("note" in b) mark.note = String(b.note || "").slice(0, 4000);
    if ("tags" in b) {
      const seen = new Set(), tags = [];
      for (let t of Array.isArray(b.tags) ? b.tags : String(b.tags).split(",")) { t = String(t).trim().replace(/^#+/, "").slice(0, 40); if (t && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); tags.push(t); } }
      mark.tags = tags.slice(0, 12);
    }
    await writeMarks(items);
    return { ok: true, bookmark: mark };
  };
  POST["bookmarks/refresh"] = async (b) => {
    const was = (await readMarks()).find((m) => m.id === b.id);
    if (!was) throw new Fail(404, "That bookmark is gone");
    const p = await askDevice({ do: "preview", url: was.url }, 45);
    if (!p || p.error || !(p.title || p.image || p.icon)) throw new Fail(502, (p && p.error) || "The page could not be read right now");
    const items = await readMarks(), mark = items.find((m) => m.id === b.id);
    if (!mark) throw new Fail(404, "That bookmark is gone");
    await storePreview(mark, p);
    await writeMarks(items);
    return { ok: true, bookmark: mark };
  };
  POST["bookmarks/delete"] = async (b) => {
    const ids = new Set((b.ids || []).map(String)), items = await readMarks(), keep = items.filter((m) => !ids.has(m.id));
    for (const m of items) if (ids.has(m.id)) for (const p of [m.image, m.icon]) if (p && p.startsWith(IMAGES + "/")) await removePath(p);
    if (keep.length !== items.length) await writeMarks(keep);
    return { ok: true, removed: items.length - keep.length };
  };

  // ------------------------------------------------------------ saving a file from a web address (a device of the account fetches it)
  let fetchId = Date.now() % 1000000;
  POST.fetch = async (b) => {
    const url = String(b.url || "").trim();
    if (!/^https?:\/\//i.test(url)) throw new Fail(400, "Give a web address that starts with http:// or https://");
    const dest = clean(b.path || "/");
    at(dest, "dir");
    const d = await helper(), id = ++fetchId;
    await tell(d, { do: "fetch", id, url, dest });
    return { ok: true, id, name: decodeURIComponent(new URL(url).pathname.split("/").pop() || "download") };
  };
  POST["fetch/cancel"] = async (b) => { for (const d of (await devices()).filter((x) => x.online)) await tell(d, { do: "fetch-cancel", id: b.id }).catch(() => null); return { ok: true }; };

  // ------------------------------------------------------------ activity: read off the list itself
  // An entry says when it came and from where (at, by), what it was before (versions), where it was moved
  // from (moved) and when it was deleted (trash). That is all there is to tell, and nothing else is kept.
  const ME = "the dashboard";
  function activity() {
    index();
    const out = [];
    for (const n of v.nodes.values()) {
      if (n.snap) continue;
      const path = pathOf.get(n.id) || (n.trash && n.trash.path), exists = pathOf.has(n.id);
      if (!path) continue;                       // inside a deleted folder: the folder's own line says it
      const row = (at, kind, more) => out.push({ at, kind, path, is_dir: !!n.dir, size: n.size || 0, exists, me: (n.by || "") === ME,
                                                 device_name: n.by || "one of your devices", ...more });
      const versions = n.versions || [];
      row(n.at || (versions.length ? versions[versions.length - 1].mtime : n.mtime) || 0, n.dir ? "mkdir" : "add");
      for (const x of versions) if (x.at) row(x.at, "edit");
      if (n.moved && n.moved.at) row(n.moved.at, "move", { src: n.moved.from });
      if (n.trash) row(n.trash.at, "delete");
    }
    out.sort((a, b) => b.at - a.at);
    out.forEach((a, i) => { a.id = out.length - i; });
    return out;
  }
  GET.history = async (q) => {
    const limit = Math.max(1, Math.min(300, +q.get("limit") || 100)), before = +q.get("before") || Infinity;
    return { items: activity().filter((a) => a.id < before).slice(0, limit) };
  };

  // ------------------------------------------------------------ snapshots: the list as it was, sealed and kept
  // A snapshot is a sealed file holding the list of that moment. Its entry sits in the list under a parent
  // that isn't a folder, so it is in no folder and every client carries it along. What a snapshot still
  // refers to is not forgotten by the server while the snapshot exists.
  const SNAPS = "~snapshots", AUTO_EVERY = 24 * 3600, AUTO_KEEP = 30 * 86400;
  const snapCache = new Map();                  // a snapshot's entries, by its object
  const snaps = () => [...v.nodes.values()].filter((n) => n.snap && !n.trash).sort((a, b) => b.snap.at - a.snap.at);
  async function snapEntries(n) {
    if (!snapCache.has(n.obj)) snapCache.set(n.obj, JSON.parse(dec.decode(await readRange(n, 0, n.size - 1))).entries || []);
    return snapCache.get(n.obj);
  }
  /** The objects all snapshots refer to. */
  async function snapKeeps() {
    const used = new Set();
    for (const n of snaps()) for (const e of await snapEntries(n).catch(() => [])) if (e.obj) used.add(e.obj);
    return used;
  }
  function snapPaths(entries) {                 // [path, entry] for everything in a snapshot
    const kids = new Map(), out = [];
    for (const e of entries) { if (!kids.has(e.parent)) kids.set(e.parent, []); kids.get(e.parent).push(e); }
    const walk = (id, path) => { for (const e of kids.get(id) || []) { const p = (path === "/" ? "" : path) + "/" + e.name; out.push([p, e]); if (e.dir) walk(e.id, p); } };
    walk("", "/");
    return out;
  }
  async function takeSnapshot(label, keep) {
    index();
    const entries = [...byPath.values()].map((n) => ({ id: n.id, parent: n.parent, name: n.name, dir: !!n.dir, size: n.size || 0, mtime: n.mtime || 0,
                                                       ...(n.dir ? {} : { obj: n.obj, key: n.key }) }));
    const files = entries.filter((e) => !e.dir), at = now();
    const blob = new Blob([JSON.stringify({ v: 1, at, entries })]);
    const raw = crypto.getRandomValues(new Uint8Array(32)), obj = DDV.newId(), id = DDV.newId();
    await v._store(obj, await DDV.fileKey(raw), blob);
    await v._send({ op: "put", id, parent: SNAPS, name: id, dir: false, size: blob.size, mtime: at, obj, key: DDV.b64(raw),
                    snap: { at, label, files: files.length, bytes: files.reduce((s, e) => s + e.size, 0), keep_until: keep ? at + keep : null } });
    snapCache.set(obj, entries);
    return id;
  }
  async function dropSnapshots(list) {
    if (!list.length) return;
    const objects = [];
    for (const n of list) { objects.push(n.obj); for (const e of await snapEntries(n).catch(() => [])) if (e.obj) objects.push(e.obj); snapCache.delete(n.obj); }
    await v._send(...list.map((n) => ({ op: "del", id: n.id })));
    await drop(objects);
  }
  /** Once a day while the dashboard is open: a snapshot, kept 30 days. */
  E.autoSnapshot = async () => {
    const all = snaps(), t = now();
    await dropSnapshots(all.filter((n) => n.snap.keep_until && n.snap.keep_until < t));
    index();
    if (byPath.size && !all.some((n) => t - n.snap.at < AUTO_EVERY)) await takeSnapshot("Automatic", AUTO_KEEP);
  };
  const snapOf = (id) => { const n = v.nodes.get(String(id)); if (!n || !n.snap || n.trash) throw new Fail(404, "no such snapshot"); return n; };
  GET.snapshots = async () => ({ items: snaps().map((n) => ({ id: n.id, ...n.snap })) });
  GET.snapshot = async (q) => {
    const path = clean(q.get("path")), depth = path === "/" ? 0 : path.split("/").length - 1;
    const items = snapPaths(await snapEntries(snapOf(q.get("id")))).filter(([p]) => p.split("/").length - 1 === depth + 1 && (path === "/" || p.startsWith(path + "/")))
      .map(([p, e]) => ({ name: e.name, path: p, dir: !!e.dir, size: e.size, mtime: e.mtime }));
    items.sort((a, b) => (b.dir - a.dir) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    return { path, items };
  };
  POST["snapshots/create"] = async (b) => ({ ok: true, id: await takeSnapshot(String(b.label || "Manual").slice(0, 60), 0) });
  POST["snapshots/delete"] = async (b) => {
    const want = new Set((b.ids || []).map(String)), list = snaps().filter((n) => b.all || want.has(n.id));
    if (!list.length) throw new Fail(404, "no such snapshot");
    await dropSnapshots(list);
    return { ok: true, count: list.length };
  };
  POST["snapshots/restore"] = async (b) => {     // copied into a new folder: the same sealed content under new names, nothing is sent again
    const sn = snapOf(b.id), from = clean(b.path), d = new Date(sn.snap.at * 1000), two = (x) => String(x).padStart(2, "0");
    const all = snapPaths(await snapEntries(sn)), picked = all.filter(([p]) => from === "/" || p === from || p.startsWith(from + "/"));
    if (!picked.length) throw new Fail(404, "nothing to restore there");
    index();
    const folder = "/" + freeName("", `Restored ${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}.${two(d.getMinutes())}`);
    const top = DDV.newId(), ids = new Map(), ops = [{ op: "put", id: top, parent: "", name: folder.slice(1), dir: true, size: 0, mtime: now(), at: now(), by: ME }];
    const root = from === "/" ? "" : (all.find(([p]) => p === from) || [0, {}])[1].parent;
    for (const [, e] of picked) ids.set(e.id, DDV.newId());
    for (const [, e] of picked) ops.push({ op: "put", id: ids.get(e.id), parent: e.parent === root ? top : ids.get(e.parent) || top, name: e.name, dir: !!e.dir, size: e.size, mtime: e.mtime,
                                           at: now(), by: ME, ...(e.dir ? {} : { obj: e.obj, key: e.key }) });
    for (let i = 0; i < ops.length; i += 200) await v._send(...ops.slice(i, i + 200));
    return { ok: true, files: picked.filter(([, e]) => !e.dir).length, folder };
  };

  // ------------------------------------------------------------ a folder (or a few things) as one ZIP, put together here
  const CRC = (() => { const t = new Uint32Array(256); for (let i = 0; i < 256; i++) { let c = i; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[i] = c >>> 0; } return t; })();
  const crc32 = (crc, bytes) => { let c = crc ^ -1; for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; };
  function zipMembers(paths) {
    index();
    const out = [];
    for (const path of paths) {
      const n = at(path), cut = path === "/" ? 0 : path.lastIndexOf("/") + 1;
      for (const [p, m] of live()) if (p === path || path === "/" || p.startsWith(path + "/")) out.push([p.slice(cut).replace(/^\/+/, "") + (m.dir ? "/" : ""), m]);
      void n;
    }
    return out;
  }
  async function zipTo(members, write) {
    const le = (n, ...vals) => { const b = new Uint8Array(n), dv = new DataView(b.buffer); let o = 0; for (const [size, val] of vals) { if (size === 2) dv.setUint16(o, val, true); else dv.setUint32(o, val >>> 0, true); o += size; } return b; };
    const central = [];
    let offset = 0;
    const put = async (bytes) => { await write(bytes); offset += bytes.length; };
    for (const [name, n] of members) {
      const nb = enc.encode(name), d = new Date((n.mtime || now()) * 1000), start = offset;
      const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1), date = (Math.max(0, d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
      const size = n.dir ? 0 : n.size, flags = 0x0800 | (n.dir ? 0 : 8);     // names in UTF-8; the checksum follows the content
      await put(le(30, [4, 0x04034b50], [2, 20], [2, flags], [2, 0], [2, time], [2, date], [4, 0], [4, n.dir ? 0 : 0], [4, n.dir ? 0 : 0], [2, nb.length], [2, 0]));
      await put(nb);
      let crc = 0;
      if (!n.dir) {
        const C = DDV.CHUNK * 4;
        for (let pos = 0; pos < size; pos += C) { const part = await readRange(n, pos, Math.min(size, pos + C) - 1); crc = crc32(crc, part); await put(part); }
        await put(le(16, [4, 0x08074b50], [4, crc], [4, size], [4, size]));
      }
      central.push([le(46, [4, 0x02014b50], [2, 0x031e], [2, 20], [2, flags], [2, 0], [2, time], [2, date], [4, crc], [4, size], [4, size], [2, nb.length], [2, 0], [2, 0], [2, 0], [2, 0],
                       [4, n.dir ? 0x41ed0010 : 0x81a40000], [4, start]), nb]);
    }
    const dirStart = offset;
    for (const [head, nb] of central) { await put(head); await put(nb); }
    await put(le(22, [4, 0x06054b50], [2, 0], [2, 0], [2, central.length], [2, central.length], [4, offset - dirStart], [4, dirStart], [2, 0]));
  }
  async function downloadZip(paths, name) {
    const members = zipMembers(paths), total = members.reduce((s, [, n]) => s + (n.dir ? 0 : n.size), 0);
    if (!members.length) throw new Error("There is nothing in there to download");
    if (members.length > 65000 || total > 3900 * 1024 * 1024) throw new Error("That is too much for one ZIP (4 GB or 65,000 files at most). Download it folder by folder.");
    if (window.showSaveFilePicker && total > 64 * 1024 * 1024) {
      let handle;
      try { handle = await window.showSaveFilePicker({ suggestedName: name }); } catch (e) { return false; }
      const out = await handle.createWritable();
      try { await zipTo(members, (b) => out.write(b)); await out.close(); } catch (e) { await out.abort().catch(() => null); throw e; }
    } else {
      if (total > 1500 * 1024 * 1024) throw new Error("This browser can only download a ZIP this big with Chrome or Edge, which write it straight to disk.");
      const parts = [];
      await zipTo(members, (b) => { parts.push(b); });
      const blob = URL.createObjectURL(new Blob(parts, { type: "application/zip" }));
      const a = document.createElement("a");
      a.href = blob; a.download = name; a.style.display = "none";
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(blob), 60000);
    }
    return true;
  }
  E.zip = (paths) => { const parts = []; return zipTo(zipMembers(paths), (b) => { parts.push(b); }).then(() => new Blob(parts)); };      // (for tests)

  // ------------------------------------------------------------ share links: the key is in the link
  // A link is 32 random bytes after the # of its address, which a browser sends nowhere. With them (and the
  // password, if one was set, which makes it part of the key) the description of what is shared is sealed:
  // names, sizes and the keys of the files. The server keeps that sealed description and hands out the sealed
  // files it lists; it can open neither. What we need to show the link again is kept there sealed for us.
  const AAD_SHARE = enc.encode("ddv1 share");
  async function shareKey(secret, password) {
    const base = await crypto.subtle.importKey("raw", enc.encode(password || ""), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: secret, iterations: password ? 300000 : 1 }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  /** What a link to an entry hands out now: {objs, sealed}. */
  async function shareSealed(n, path, secret, password, download) {
    index();
    const files = n.dir ? liveFiles().filter(([p]) => p.startsWith(path + "/")).map(([p, f]) => [p.slice(path.length + 1), f]) : [[n.name, n]];
    const listed = { v: 1, name: n.name, dir: !!n.dir, download: !!download, files: files.map(([p, f]) => ({ path: p, size: f.size, mtime: f.mtime, obj: f.obj, key: f.key })) };
    return { objs: [...new Set(files.map(([, f]) => f.obj))], sealed: DDV.b64(await DDV.seal(await shareKey(secret, password), enc.encode(JSON.stringify(listed)), AAD_SHARE)) };
  }
  function shareJson(s, own, base) {
    const path = `/v/${s.id}#${own.r.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`, n = v.nodes.get(own.id);
    index();
    return { id: s.id, path, url: base ? base + path : "", direct_path: "", direct: "", lan: [], item: n && pathOf.get(n.id) || null, dir: !!own.dir,
             created: s.created, expires: s.expires, password: !!own.pw, download: own.download !== false, views: s.views || 0, upload: false };
  }
  async function sharesNow() {
    const got = await DDV.call("/api/vault/shares"), out = [];
    for (const s of got.items) { try { out.push([s, await DDV.openMessage(v.meta, s.own), got.base]); } catch (e) { /* not sealed by this account */ } }
    return out;
  }
  GET.shares = async () => ({ items: (await sharesNow()).map(([s, own, base]) => shareJson(s, own, base)) });
  POST.share = async (b) => {
    if (b.upload) throw new Fail(400, "File requests aren't there for end-to-end encrypted accounts yet: the files would have to be sealed for you by whoever sends them.");
    const path = clean(b.path), n = at(path);
    if (!n.id) throw new Fail(400, "Share a folder or file, not the whole account");
    const secret = crypto.getRandomValues(new Uint8Array(32)), download = b.download !== false;
    const own = { r: DDV.b64(secret), id: n.id, dir: !!n.dir, pw: !!b.password, download };
    const s = await DDV.call("/api/vault/share", { method: "POST", json: { ...(await shareSealed(n, path, secret, b.password, download)), own: await DDV.sealMessage(v.meta, own),
                                                                            hours: b.hours === undefined ? 168 : b.hours, password: !!b.password } });
    return shareJson(s, own, s.base);
  };
  POST["shares/update"] = async (b) => {
    const found = (await sharesNow()).find(([s]) => s.id === b.id);
    if (!found) throw new Fail(404, "That link no longer exists");
    const [, own] = found, body = { id: b.id };
    if ("hours" in b) body.hours = b.hours;
    const download = "download" in b ? b.download !== false : own.download !== false;
    if ("password" in b || download !== (own.download !== false)) {
      // a new password is a new key: what is shared is sealed again (as it is now). Keeping the old password isn't possible without it.
      if (!("password" in b) && own.pw) throw new Fail(400, "Type the link's password again to change this (it is part of the key, and isn't kept anywhere).");
      const n = v.nodes.get(own.id);
      index();
      if (!n || !pathOf.has(n.id)) throw new Fail(404, "What this link pointed to was deleted. Turn the link off.");
      Object.assign(own, { pw: !!b.password, download });
      Object.assign(body, await shareSealed(n, pathOf.get(n.id), DDV.unb64(own.r), b.password || "", download), { own: await DDV.sealMessage(v.meta, own), password: !!b.password });
    }
    const s = await DDV.call("/api/vault/share/update", { method: "POST", json: body });
    return shareJson(s, own, s.base);
  };
  POST["shares/revoke"] = async (b) => {
    const r = await DDV.call("/api/vault/share/revoke", { method: "POST", json: { id: b.id } });
    await drop(r.objs || []);
    return { ok: true };
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
    if (url.pathname === "/api/zip") {
      const paths = url.searchParams.get("paths") ? JSON.parse(url.searchParams.get("paths")) : [url.searchParams.get("path") || "/"];
      E.say("Putting the ZIP together…");
      downloadZip(paths.map(clean), name || "DDrive.zip").then((ok) => { if (ok) E.say(`Downloaded ${name || "DDrive.zip"}`); }, (e) => E.say(e.message || "The download failed"));
      return true;
    }
    if (url.pathname === "/api/contacts/export") {
      const want = new Set((url.searchParams.get("uids") || "").split(",").filter(Boolean));
      readBook().then((items) => {
        const blob = URL.createObjectURL(new Blob(items.filter((c) => !want.size || want.has(c.uid)).map(toVcard), { type: "text/vcard" }));
        const a = document.createElement("a");
        a.href = blob; a.download = name || "contacts.vcf";
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(blob), 60000);
      }, (e) => E.say(e.message));
      return true;
    }
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
    setTimeout(() => E.autoSnapshot().catch(() => null), 30000);
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
