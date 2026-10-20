"use strict";
/* End-to-end encrypted accounts, in the browser: the same keys and formats as ddrive/vault.py (which
 * describes them). The 24 words are turned into keys here and go nowhere else: the server gets a
 * sign-in key that can't be turned back into them, and only sealed things after that. The key that
 * opens the list of files is kept in this browser (IndexedDB) in a form that can be used but not read
 * out, so that the words needn't be typed on every visit. */
(function () {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const INFO_AUTH = enc.encode("ddrive vault v1 auth"), INFO_META = enc.encode("ddrive vault v1 meta");
  const AAD_OP = enc.encode("ddv1 op"), AAD_CHUNK = enc.encode("ddv1 chunk"), AAD_SYNC = enc.encode("ddv1 sync");
  const AAD_KEPT = enc.encode("ddv1 list kept in this browser");
  const AAD_UPLOAD = enc.encode("ddv1 upload kept in this browser"), UPLOAD_KEPT = 7 * 86400 * 1000;
  const AAD_LOCKED = enc.encode("ddv1 locked"), AAD_LOCK_KEY = enc.encode("ddv1 lock key");
  // A lock may also be opened with the account's 24 words (when that was chosen as it was made): its key is
  // sealed a second time, with a key made from the words alone (HKDF, "ddrive vault v1 lock recovery").
  const AAD_LOCK_REC = enc.encode("ddv1 lock recovery"), INFO_LOCK_REC = enc.encode("ddrive vault v1 lock recovery");
  // Password-locked folders and files (vault.py says how they are kept): a lock's record sits under LOCKS,
  // a locked folder or file under LOCKED (its own folder in `home`), and what is in it under it, as ever.
  // None of these is under a folder any device shows, so a device that doesn't know of locks never sees them.
  const LOCKS = "~locks", LOCKED = "~locked", LOCK_ITER = 600000;
  const MAGIC = enc.encode("DDV1"), CHUNK_LOG = 20, CHUNK = 1 << CHUNK_LOG, OVERHEAD = 28, HEAD = 5;
  const PART_CHUNKS = 8;                 // sealed pieces go to the server this many at a time
  const SNAPSHOT_EVERY = 400;
  const usable = !!(window.isSecureContext && window.crypto && crypto.subtle && window.indexedDB);

  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const b64 = (b) => { let s = ""; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(s); };
  const unb64 = (s) => { const t = atob(s), b = new Uint8Array(t.length); for (let i = 0; i < t.length; i++) b[i] = t.charCodeAt(i); return b; };
  const newId = () => hex(crypto.getRandomValues(new Uint8Array(16)));
  /** The id a new folder gets: the same on every device for the same name in the same folder, so two
   *  devices that make it at once make one folder (vault.folder_id). */
  /** What a locked folder or file says of its name, in the clear (vault.py, "Password locks"): so that a device
   *  that can't open it doesn't make one of the same name in the clear next to it. */
  const lockedTag = async (parent, name) => hex(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(`ddrive locked v1\n${parent}\n${name}`)))).slice(0, 32);
  const folderId = async (parent, name) => hex(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(`ddrive folder v1\n${parent}\n${name}`)))).slice(0, 32);
  /** The list as it is shown, the same on every device (vault.view): folders that two devices made at
   *  once are one (the smallest id shows, with what is in all of them), what went `into` another folder
   *  shows there, and of two files of one name the folder, else the newest, shows. folder id -> [entry]. */
  function view(nodes) {
    const raw = new Map(), went = new Map(), kids = new Map(), done = new Set();
    for (const n of nodes.values()) {
      if (!raw.has(n.parent)) raw.set(n.parent, []);
      raw.get(n.parent).push(n);
      if (n.into && n.into !== n.id && nodes.has(n.into)) { if (!went.has(n.into)) went.set(n.into, []); went.get(n.into).push(n.id); }
    }
    const todo = [["", [""]]];
    while (todo.length) {
      const [fid, members] = todo.pop();
      if (done.has(fid)) continue;
      done.add(fid);
      const group = new Set();
      while (members.length) { const m = members.pop(); if (!group.has(m)) { group.add(m); members.push(...(went.get(m) || [])); } }
      const byName = new Map();
      for (const m of group) for (const c of raw.get(m) || []) {
        if (c.trash || c.into || done.has(c.id)) continue;
        const name = c.name || "";
        if (!byName.has(name)) byName.set(name, []);
        byName.get(name).push(c);
      }
      const out = [];
      for (const cs of byName.values()) {
        const dirs = cs.filter((c) => c.dir).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
        const files = cs.filter((c) => !c.dir).sort((a, b) => ((b.mtime || 0) - (a.mtime || 0)) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
        out.push(dirs.length ? dirs[0] : files[0]);
        if (dirs.length) todo.push([dirs[0].id, dirs.map((d) => d.id)]);
      }
      kids.set(fid, out);
    }
    return kids;
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------ the 24 words (as words.py: BIP39)
  let listP = null;
  function wordlist() {
    return listP || (listP = fetch("/wordlist.txt").then((r) => r.text()).then((t) => {
      const w = t.split(/\s+/).filter(Boolean);
      if (w.length !== 2048) throw new Error("The word list could not be loaded. Reload the page.");
      return w;
    }));
  }
  async function keyToPhrase(key) {
    const w = await wordlist();
    const sum = new Uint8Array(await crypto.subtle.digest("SHA-256", key))[0];
    let n = 0n;
    for (const b of key) n = (n << 8n) | BigInt(b);
    n = (n << 8n) | BigInt(sum);
    const out = [];
    for (let i = 23; i >= 0; i--) out.push(w[Number((n >> BigInt(11 * i)) & 0x7FFn)]);
    return out.join(" ");
  }
  async function phraseToKey(text) {
    const given = String(text || "").toLowerCase().match(/[a-z]+/g) || [];
    if (given.length !== 24) throw new Error(`a recovery phrase has 24 words; this has ${given.length}`);
    const w = await wordlist(), at = new Map(w.map((x, i) => [x, i]));
    let n = 0n;
    for (let i = 0; i < 24; i++) {
      if (!at.has(given[i])) {
        const close = w.find((x) => x.slice(0, 4) === given[i].slice(0, 4));
        throw new Error(`word ${i + 1}, '${given[i]}', is not one of the words used${close ? ` (did you mean '${close}'?)` : ""}`);
      }
      n = (n << 11n) | BigInt(at.get(given[i]));
    }
    const key = new Uint8Array(32);
    let k = n >> 8n;
    for (let i = 31; i >= 0; i--) { key[i] = Number(k & 0xFFn); k >>= 8n; }
    const sum = new Uint8Array(await crypto.subtle.digest("SHA-256", key))[0];
    if (sum !== Number(n & 0xFFn)) throw new Error("the words don't fit together: one is wrong or out of order");
    return key;
  }
  const newKey = () => crypto.getRandomValues(new Uint8Array(32));

  // ------------------------------------------------------------ keys
  /** From the key the words stand for: what to sign in with (hex), and the key of the list of files. */
  async function derive(key) {
    const base = await crypto.subtle.importKey("raw", key, "HKDF", false, ["deriveBits", "deriveKey"]);
    const how = (info) => ({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info });
    const auth = new Uint8Array(await crypto.subtle.deriveBits(how(INFO_AUTH), base, 256));
    const meta = await crypto.subtle.deriveKey(how(INFO_META), base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    return { auth: hex(auth), meta };
  }
  const fileKey = (raw) => crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
  /** The key a lock's password makes (PBKDF2-SHA256: slow on purpose, so passwords can't be guessed quickly). */
  async function passwordKey(password, salt, iterations) {
    const base = await crypto.subtle.importKey("raw", enc.encode(String(password).normalize("NFC")), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  /** The key that opens locks with the account's words (from the key the words stand for). */
  async function recoveryKey(wordsKey) {
    const base = await crypto.subtle.importKey("raw", wordsKey, "HKDF", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: INFO_LOCK_REC }, base,
                                   { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  /** The objects an entry holds on to: its content, its earlier versions, its small picture. */
  const objectsOf = (n) => [n.obj, ...(n.versions || []).map((x) => x.obj), n.thumb && n.thumb !== -1 ? n.thumb.obj : null].filter(Boolean);

  /** nonce + tag + ciphertext */
  async function seal(key, data, label) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const out = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: label, tagLength: 128 }, key, data));
    const n = out.length - 16, blob = new Uint8Array(OVERHEAD + n);
    blob.set(nonce, 0); blob.set(out.subarray(n), 12); blob.set(out.subarray(0, n), 28);
    return blob;
  }
  async function unseal(key, blob, label) {
    const n = blob.length - OVERHEAD;
    if (n < 0) throw new Error("This is cut short.");
    const data = new Uint8Array(n + 16);
    data.set(blob.subarray(28), 0); data.set(blob.subarray(12, 28), n);
    try {
      return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: blob.subarray(0, 12), additionalData: label, tagLength: 128 }, key, data));
    } catch (e) {
      throw new Error("This can't be opened with these 24 words (or it was changed on the way).");
    }
  }
  function chunkLabel(n, last) {
    const l = new Uint8Array(AAD_CHUNK.length + 9);
    l.set(AAD_CHUNK, 0);
    new DataView(l.buffer).setBigUint64(AAD_CHUNK.length, BigInt(n));
    l[l.length - 1] = last ? 1 : 0;
    return l;
  }
  /** What is said between this page and a device that syncs folders, sealed (the server passes it on unread). */
  const sealMessage = async (meta, obj) => b64(await seal(meta, enc.encode(JSON.stringify(obj)), AAD_SYNC));
  const openMessage = async (meta, text) => text ? JSON.parse(dec.decode(await unseal(meta, unb64(text), AAD_SYNC))) : null;
  // A file's fingerprints, to tell whether two files are the same without comparing them byte by byte.
  // A file of any size has to be fingerprinted as it goes by, piece by piece, and a browser can only
  // fingerprint something it has whole. So: the SHA-256 of each 1 MiB piece, and the SHA-256 of those
  // in a row ("sum": the same for the same content, and for nothing else). "head" is the first piece's
  // alone: two files whose heads differ are different, which is known after reading 1 MiB of each.
  const pieceSum = async (data) => new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  async function prints(sums) {
    const all = new Uint8Array(sums.length * 32);
    sums.forEach((s, i) => all.set(s, i * 32));
    return { sum: "t1:" + hex(await pieceSum(all)), head: "h1:" + hex(sums[0] || await pieceSum(new Uint8Array(0))) };
  }
  const chunksOf = (size) => Math.max(1, Math.ceil(size / CHUNK));
  const sealedSize = (size) => HEAD + size + OVERHEAD * chunksOf(size);

  // ------------------------------------------------------------ the key kept in this browser
  function db() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("ddrive-vault", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("keys");
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function store(mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("keys", mode), req = fn(tx.objectStore("keys"));
      tx.oncomplete = () => { d.close(); resolve(req && req.result); };
      tx.onerror = tx.onabort = () => { d.close(); reject(tx.error); };
    });
  }
  const keys = {
    put: (meta) => store("readwrite", (s) => s.put(meta, "meta")),
    get: () => store("readonly", (s) => s.get("meta")),
    clear: () => store("readwrite", (s) => s.clear()),
  };

  // ------------------------------------------------------------ a connection that comes and goes
  // A request that is under way when the network changes (Wi-Fi to the phone's, a cable pulled) often
  // neither finishes nor fails: it just hangs, and with it whatever waited for it. So a request that
  // carries a part of a file is given up the moment the browser says the network went away or changed,
  // and after a long while without an answer in any case; who sent it then sends it again (reconnect).
  // "Changed" is the kind of network becoming another (Wi-Fi, cable, the phone's), where the browser
  // says which it is. It is not every "change" the browser reports: Chrome reports one each time its
  // guess of the connection's speed moves, which it does all the time while a big upload is running,
  // and giving up the parts on each of those kept an upload from ever getting a part through
  // ("The connection dropped. Trying again…" on every file at once, for as long as one watched).
  const STALLED = 240000;                // a part is 8 MiB at most: longer than this, and it isn't coming
  async function steady(send, signal) {
    const mine = new AbortController();
    const cut = () => mine.abort();
    const net = navigator.connection, kind = net && net.type;
    const moved = () => { if (net.type !== kind) cut(); };
    const timer = setTimeout(cut, STALLED);
    window.addEventListener("offline", cut);
    if (net && net.addEventListener) net.addEventListener("change", moved);
    if (signal) { signal.addEventListener("abort", cut); if (signal.aborted) cut(); }
    try { return await send(mine.signal); }
    finally {
      clearTimeout(timer);
      window.removeEventListener("offline", cut);
      if (net && net.removeEventListener) net.removeEventListener("change", moved);
      if (signal) signal.removeEventListener("abort", cut);
    }
  }
  /** Wait before trying again: `ms`, or shorter when the connection is back before that. */
  function reconnect(ms, signal) {
    return new Promise((resolve, reject) => {
      const done = () => { clearTimeout(timer); window.removeEventListener("online", back); if (signal) signal.removeEventListener("abort", stop); };
      const back = () => { done(); setTimeout(resolve, 800); };        // (a moment for it to settle)
      const stop = () => { done(); reject(new DOMException("Stopped", "AbortError")); };
      const timer = setTimeout(() => { done(); resolve(); }, navigator.onLine === false ? Math.max(ms, 60000) : ms);
      window.addEventListener("online", back);
      if (signal) { if (signal.aborted) return stop(); signal.addEventListener("abort", stop); }
    });
  }

  /** Do something that needs the server until it works, waiting for the connection in between. What the
   *  server refuses for a reason of its own (4xx) is not tried again, and neither is anything once `signal` says stop. */
  async function patient(fn, signal, told = () => {}) {
    for (let tries = 0; ;) {
      try { return await fn(); } catch (e) {
        if ((signal && signal.aborted) || e instanceof Gone || e.status === 507 || (e.status && e.status < 500 && e.status !== 408 && e.status !== 429)) throw e;      // (507: no room, and the server says so)
        tries++;
        told(navigator.onLine === false ? "No connection. It carries on when there is one again." : "The connection dropped. Trying again…");
        await reconnect(Math.min(30000, 1500 * 2 ** Math.min(tries, 5)), signal);
      }
    }
  }

  // ------------------------------------------------------------ talking to the server
  class Gone extends Error {}            // signed out (the session ran out)
  let also = "";                         // a session of the account, for a page that is signed in as somebody else too (moving.js)
  async function call(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (opts.method === "POST") headers["X-DD"] = "1";
    if (also) headers["X-DD-Account"] = also;
    if (opts.json !== undefined) headers["Content-Type"] = "application/json";
    const r = await fetch(path, { method: opts.method || "GET", credentials: "same-origin", headers, signal: opts.signal,
                                  body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body });
    if (r.status === 401) throw new Gone("Signed out. Reload the page to sign in.");
    if (!r.ok) {
      let message = `The server answered with ${r.status}`;
      try { message = (await r.json()).error || message; } catch (e) { /* not JSON */ }
      const err = new Error(message);
      err.status = r.status;
      throw err;
    }
    return opts.raw ? new Uint8Array(await r.arrayBuffer()) : r.json();
  }

  /** The account as this browser uses it: the list of files in the clear here, sealed on the server. */
  class Vault {
    constructor(meta) {
      this.meta = meta;
      this.raw = new Map();              // id -> the entry as it is kept: {id, parent, name, dir, size, mtime, obj, key}, or sealed by a lock
      this.nodes = new Map();            // the same as this page shows it: what is locked left out, what is unlocked here opened
      this.opened = new Map();           // lock id -> its key, for the locks unlocked in this page
      this.boxes = new Map();            // a locked entry's sealed content -> what it says (for the locks that are open)
      this.gen = 0;                      // goes up whenever `nodes` is made again
      this.seq = 0;
      this.since = 0;                    // changes since the last sealed whole list
    }

    // ---------- the list
    /** Bring the list up to date with the server. One at a time: two of these at once (a file uploaded
     *  while the page looks for what other devices did) would read the same changes twice. */
    sync() {
      const run = () => this._sync();
      const p = (this._syncing || Promise.resolve()).then(run, run);
      this._syncing = p.catch(() => null);
      return p;
    }
    async _sync() {
      for (;;) {
        const got = await call(`/api/vault/log?after=${this.seq}`);
        if (this.seq > (got.seq || 0)) {               // the server has less than this list knows of (it was
          this.raw = new Map();                        // brought back from a copy): what was kept here is no
          this.seq = 0; this.since = 0;                // good, and all of it is read again
          this.kept = -1;
          continue;
        }
        const snap = got.snapshot;
        if (snap && snap.seq > this.seq) {             // behind the last sealed whole list: start from it
          const raw = await this._openAll(this.meta, snap.object, snap.size);
          this.raw = new Map(Object.entries(JSON.parse(dec.decode(raw)).nodes));
          this.seq = snap.seq; this.since = 0;
          if (this.heard) this.heard(this);
          continue;
        }
        for (const [seq, batch] of got.batches) {
          const b = unb64(batch), view = new DataView(b.buffer);
          for (let pos = 0; pos + 4 <= b.length;) {
            const n = view.getUint32(pos);
            this._apply(JSON.parse(dec.decode(await unseal(this.meta, b.subarray(pos + 4, pos + 4 + n), AAD_OP))));
            this.since++;
            pos += 4 + n;
          }
          this.seq = seq;
        }
        if (!got.more) { await this._openBoxes(); this._derive(); }
        if (this.heard) this.heard(this);          // (whoever shows the list being read: how far it is)
        if (!got.more) return this;
      }
    }
    // ---------- the list, kept in this browser between visits
    // Reading the whole list from the server and opening it takes as long as the account is big, and
    // was done every time the page was loaded. So the list as it was last opened is kept here
    // (IndexedDB, next to the key), sealed with the same key that seals it on the server: a key this
    // browser can use but not read out. The next visit opens that and asks the server only for what
    // changed since. It is this account's alone (`who`), and goes when the key goes (signing out).
    /** Start from the list kept in this browser, if there is one that opens. True when there was. */
    async restore(who) {
      try {
        const kept = await store("readonly", (s) => s.get("list:" + who));
        if (!kept || !kept.sealed || !(kept.seq > 0)) return false;
        const raw = await unseal(this.meta, new Uint8Array(kept.sealed), AAD_KEPT);
        this.raw = new Map(Object.entries(JSON.parse(dec.decode(raw)).nodes));
        this.seq = kept.seq; this.since = kept.since || 0;
        this.kept = kept.seq;
        this._derive();
        return true;
      } catch (e) {                                    // another key, or damaged: read from the server as ever
        this.raw = new Map(); this.seq = 0; this.since = 0; this._derive();
        return false;
      }
    }
    /** Keep the list as it is now, for the next visit (nothing to do when it is the one that is kept). */
    async keep(who) {
      if (this.kept === this.seq || this._keeping) return;
      this._keeping = true;
      try {
        const seq = this.seq, since = this.since;
        const sealed = await seal(this.meta, enc.encode(JSON.stringify({ nodes: Object.fromEntries(this.raw) })), AAD_KEPT);
        await store("readwrite", (s) => s.put({ seq, since, sealed: sealed.buffer, at: Date.now() }, "list:" + who));
        this.kept = seq;
      } catch (e) { /* no room, or a private window: the next visit reads it from the server */ }
      finally { this._keeping = false; }
    }

    _apply(op) {
      if (op.op === "put") {
        const n = { ...op, dir: !!op.dir, size: op.size || 0, mtime: op.mtime || 0 };      // every field, also ones not known here
        delete n.op;
        this.raw.set(op.id, n);
      } else if (op.op === "set") {
        const n = this.raw.get(op.id);
        if (n) for (const [k, val] of Object.entries(op.fields || {})) { if (val === null) delete n[k]; else if (k !== "id") n[k] = val; }
      } else if (op.op === "move") {
        const n = this.raw.get(op.id);
        if (n) { n.parent = op.parent; n.name = op.name; }
      } else if (op.op === "del") {
        for (const id of this._under(op.id)) this.raw.delete(id);
      }
    }
    /** An entry and everything in it (also what is locked in it), by id. */
    _under(id) {
      const top = this.raw.get(id);
      if (!top) return [];
      if (!top.dir && !top.box) return [id];   // a file: nothing is in it (deleting many files is no longer a walk through the whole list each)
      const out = [], todo = [id], seen = new Set();
      while (todo.length) {
        const cur = todo.pop();
        if (!this.raw.has(cur) || seen.has(cur)) continue;
        seen.add(cur); out.push(cur);
        for (const n of this.raw.values()) if (n.parent === cur || (n.parent === LOCKED && n.home === cur)) todo.push(n.id);
      }
      return out;
    }

    // ---------- password locks
    /** Open what is locked with the locks open here (once each: what it says is kept while they are open). */
    async _openBoxes() {
      if (!this.opened.size) return;
      for (const n of this.raw.values()) {
        if (!n.box || this.boxes.has(n.box) || !this.opened.has(n.in)) continue;
        try { this.boxes.set(n.box, JSON.parse(dec.decode(await unseal(this.opened.get(n.in), unb64(n.box), AAD_LOCKED)))); }
        catch (e) { /* damaged, or sealed with another key: it stays out of sight */ }
      }
    }
    /** `nodes` from `raw`: locked entries left out, unless their lock is open here. */
    _derive() {
      const out = new Map(), roots = [];
      for (const n of this.raw.values()) {
        if (n.parent === LOCKS) continue;
        if (!n.box) { out.set(n.id, n); continue; }
        const inner = this.opened.has(n.in) && this.boxes.get(n.box);
        if (!inner) continue;
        const root = n.parent === LOCKED;
        const e = { ...inner, id: n.id, parent: root ? n.home || "" : n.parent, lock: n.in };
        if (root) { e.lockRoot = true; roots.push(e); }
        out.set(n.id, e);
      }
      // A device that can't see a locked folder may make one of the same name next to it: this one shows as "name (locked)".
      for (const r of roots) {
        for (const n of out.values()) {
          if (n !== r && !n.lockRoot && !n.trash && n.parent === r.parent && n.name === r.name) { r._name = r.name; r.name = `${r.name} (locked)`; break; }
        }
      }
      this.nodes = out;
      this.gen++;
    }
    /** The lock a new entry in this folder is sealed with (null: none). */
    _lockIn(folder) { const f = this.nodes.get(folder); return (f && f.lock) || null; }
    /** An entry as the change that keeps it sealed with lock `lock` (as the locked one itself, when `root`). */
    async _boxed(e, lock, root) {
      const inner = { ...e, name: e._name || e.name };
      for (const k of ["id", "parent", "lock", "lockRoot", "_name", "op", "home", "in", "box", "objs"]) delete inner[k];
      const key = this.opened.get(lock);
      if (!key) throw new Error("That is locked: unlock it first.");
      const op = { op: "put", id: e.id, parent: root ? LOCKED : e.parent, in: lock, objs: objectsOf(inner),
                   box: b64(await seal(key, enc.encode(JSON.stringify(inner)), AAD_LOCKED)) };
      if (root) {
        op.home = e.parent || "";
        op.tag = await lockedTag(op.home, inner.name || "");
        if (inner.trash) op.gone = 1;
      }
      return op;
    }
    /** Locks opened here that were made before tags, or whose folder was renamed or deleted since: their tag
     *  (and `gone`) set as they are now. (Best effort: an older page left them so.) */
    async _tagRoots() {
      const ops = [];
      for (const n of this.nodes.values()) {
        if (!n.lockRoot) continue;
        const r = this.raw.get(n.id), tag = await lockedTag(n.parent || "", n._name || n.name), gone = n.trash ? 1 : undefined;
        if (r && (r.tag !== tag || r.gone !== gone)) ops.push({ op: "set", id: n.id, fields: { tag, gone: gone || null } });
      }
      if (ops.length) await this._post(...ops).catch(() => null);
    }
    /** An entry as the change that keeps it in the clear. */
    _plain(e) {
      const op = { ...e, op: "put", name: e._name || e.name };
      for (const k of ["lock", "lockRoot", "_name", "home", "in", "box", "objs"]) delete op[k];
      return op;
    }
    /** What is in an entry as this page shows it (it first), by id. */
    _openUnder(id) {
      const kids = new Map();
      for (const n of this.nodes.values()) {
        if (n.lockRoot) continue;
        if (!kids.has(n.parent)) kids.set(n.parent, []);
        kids.get(n.parent).push(n.id);
      }
      const out = [], todo = [id], seen = new Set();
      while (todo.length) {
        const cur = todo.pop();
        if (!this.nodes.has(cur) || seen.has(cur)) continue;
        seen.add(cur); out.push(cur);
        todo.push(...(kids.get(cur) || []));
      }
      return out;
    }
    /** Changes as they are to be kept: one that touches something locked here is the entry sealed again. */
    async _seal(ops) {
      const out = [];
      for (const op of ops) {
        if (op.op === "del" || String(op.parent || "").startsWith("~") || op.box) { out.push(op); continue; }
        const n = this.nodes.get(op.id);
        if (op.op === "put") {
          if (n && n.lockRoot) out.push(await this._boxed(op, n.lock, true));
          else {
            const lock = this._lockIn(op.parent);
            out.push(lock ? await this._boxed(op, lock, false) : op.lock ? this._plain(op) : op);
          }
        } else if (op.op === "set") {
          if (!n || !n.lock) { out.push(op); continue; }
          const e = { ...n };
          for (const [k, val] of Object.entries(op.fields || {})) { if (val === null) delete e[k]; else if (k !== "id") e[k] = val; }
          out.push(await this._boxed(e, n.lock, !!n.lockRoot));
        } else if (op.op === "move") {
          if (!n) { out.push(op); continue; }
          const to = this._lockIn(op.parent);
          if (n.lockRoot) {
            if (to) throw new Error("A locked folder can't go into another locked folder.");
            const e = { ...n, parent: op.parent, name: op.name };
            delete e._name;
            out.push(await this._boxed(e, n.lock, true));
          } else if (n.lock === to) {
            out.push(to ? await this._boxed({ ...n, parent: op.parent, name: op.name }, to, false) : op);
          } else {                                 // into or out of a locked folder: it and what is in it are sealed, or opened, again
            for (const id of this._openUnder(op.id)) {
              const e = id === op.id ? { ...n, parent: op.parent, name: op.name } : this.nodes.get(id);
              out.push(to ? await this._boxed(e, to, false) : this._plain(e));
            }
          }
        } else out.push(op);
      }
      return out;
    }
    /** Whether these are this account's words (what they seal, this page's key opens). */
    async ownWords(wordsKey) {
      const probe = await seal((await derive(wordsKey)).meta, enc.encode("ddrive"), AAD_SYNC);
      try { await unseal(this.meta, probe, AAD_SYNC); return true; } catch (e) { return false; }
    }
    /** Lock an entry (and what is in it) with a password. It stays open in this page. `wordsKey`: the key the
     *  account's words stand for, when they are to open it too (a forgotten password); otherwise only the password does. */
    async lock(id, password, wordsKey = null) {
      const n = this.nodes.get(id);
      if (!n) throw new Error("That isn't there.");
      if (n.lockRoot) throw new Error("That has a lock already.");
      if (n.lock) throw new Error("That is in a locked folder already.");
      const all = this._openUnder(id), inside = new Set(all);
      if ([...this.raw.values()].some((r) => r.parent === LOCKED && inside.has(r.home)))
        throw new Error("There is a locked folder in it. Remove that lock first, or lock something else.");
      const raw = newKey(), salt = crypto.getRandomValues(new Uint8Array(16)), lock = newId();
      const wrap = await seal(await passwordKey(password, salt, LOCK_ITER), raw, AAD_LOCK_KEY);
      this.opened.set(lock, await fileKey(raw));
      const rec = { v: 1, salt: b64(salt), iter: LOCK_ITER, wrap: b64(wrap) };
      if (wordsKey) rec.rec = b64(await seal(await recoveryKey(wordsKey), raw, AAD_LOCK_REC));
      const ops = [{ op: "put", id: lock, parent: LOCKS, name: "", dir: false, size: 0, mtime: Date.now() / 1000, lock: rec }];
      for (const x of all) ops.push(await this._boxed(this.nodes.get(x), lock, x === id));
      await this._postAll(ops);
      return lock;
    }
    /** The locks this password opens (every one of them): how many locked things are open now. */
    async unlock(password) {
      const recs = [...this.raw.values()].filter((n) => n.parent === LOCKS && n.lock && n.lock.wrap && !this.opened.has(n.id));
      const got = await Promise.all(recs.map(async (r) => {
        try {
          const pk = await passwordKey(password, unb64(r.lock.salt), r.lock.iter || LOCK_ITER);
          return [r.id, await fileKey(await unseal(pk, unb64(r.lock.wrap), AAD_LOCK_KEY))];
        } catch (e) { return null; }
      }));
      const now = new Set();
      for (const g of got) if (g) { this.opened.set(g[0], g[1]); now.add(g[0]); }
      if (now.size) { await this._openBoxes(); this._derive(); await this._tagRoots(); }
      return [...this.raw.values()].filter((n) => n.parent === LOCKED && now.has(n.in)).length;
    }
    /** The locks the account's words open (those made so): how many locked things are open now. */
    async unlockWithWords(wordsKey) {
      const rk = await recoveryKey(wordsKey);
      const now = new Set();
      for (const r of this.raw.values()) {
        if (r.parent !== LOCKS || !r.lock || !r.lock.rec || this.opened.has(r.id)) continue;
        try { this.opened.set(r.id, await fileKey(await unseal(rk, unb64(r.lock.rec), AAD_LOCK_REC))); now.add(r.id); }
        catch (e) { /* made with other words, or damaged */ }
      }
      if (now.size) { await this._openBoxes(); this._derive(); await this._tagRoots(); }
      return [...this.raw.values()].filter((n) => n.parent === LOCKED && now.has(n.in)).length;
    }
    /** Every lock closed again in this page. */
    relock() { this.opened.clear(); this.boxes.clear(); this._derive(); }
    /** Take the lock off a locked entry: it and what is in it are in the clear again, on every device. */
    async unprotect(id, password) {
      const n = this.nodes.get(id), rec = n && this.raw.get(n.lock);
      if (!n || !n.lockRoot || !rec) throw new Error("That isn't locked.");
      try { await unseal(await passwordKey(password, unb64(rec.lock.salt), rec.lock.iter || LOCK_ITER), unb64(rec.lock.wrap), AAD_LOCK_KEY); }
      catch (e) { throw new Error("That isn't its password."); }
      const ops = this._openUnder(id).map((x) => this._plain(this.nodes.get(x)));
      const others = [...this.raw.values()].some((r) => r.in === n.lock && r.parent === LOCKED && r.id !== id);
      if (!others) ops.push({ op: "del", id: n.lock });
      await this._postAll(ops);
    }
    /** {count: locked things, open: those open here}. */
    lockCount() {
      let count = 0, open = 0;
      for (const n of this.raw.values()) {
        if (n.parent !== LOCKED) continue;
        count++;
        const e = this.nodes.get(n.id);
        if (e && !e.trash) open++;
      }
      return { count, open };
    }
    /** Locked things whose folder is gone for good (another device deleted it, which can't see them). */
    strays() {
      return [...this.raw.values()].filter((n) => n.parent === LOCKED && n.home && !this.raw.has(n.home));
    }

    async _send(...ops) { return this._post(...(await this._seal(ops))); }
    /** Many changes (locking a big folder): sent in batches as big as the server takes, and the whole list
     *  sealed once at the end. (Sealing it after every 400 sent the whole list again and again: on a big
     *  account that was hundreds of MB, and locking looked stuck.) */
    async _postAll(ops, sealFirst = false) {
      const BATCH = 2 * 1024 * 1024;           // (the server takes up to 4 MB)
      let batch = [], size = 0;
      const flush = async () => {
        if (!batch.length) return;
        const go = sealFirst ? await this._seal(batch) : batch;
        batch = []; size = 0;
        await this._post(...go);
      };
      this._bulk = true;
      try {
        for (const op of ops) {
          const n = JSON.stringify(op).length * (sealFirst ? 2 : 1) + 64;
          if (size + n > BATCH) await flush();
          batch.push(op); size += n;
        }
        await flush();
      } finally { this._bulk = false; }
      if (this.since >= SNAPSHOT_EVERY) await this.snapshot().catch(() => null);
    }
    async _sendAll(ops) { return this._postAll(ops, true); }
    async _post(...ops) {
      const blobs = [];
      let total = 0;
      for (const op of ops) { const b = await seal(this.meta, enc.encode(JSON.stringify(op)), AAD_OP); blobs.push(b); total += 4 + b.length; }
      const batch = new Uint8Array(total), view = new DataView(batch.buffer);
      let pos = 0;
      for (const b of blobs) { view.setUint32(pos, b.length); batch.set(b, pos + 4); pos += 4 + b.length; }
      await call("/api/vault/log", { method: "POST", body: batch, headers: { "Content-Type": "application/octet-stream" } });
      await this.sync();
      if (!this._bulk && this.since >= SNAPSHOT_EVERY) await this.snapshot().catch(() => null);
    }
    async snapshot() {
      const state = enc.encode(JSON.stringify({ nodes: Object.fromEntries(this.raw) }));
      const id = newId();
      await this._store(id, this.meta, new Blob([state]));
      await call("/api/vault/snapshot", { method: "POST", json: { seq: this.seq, object: id, size: state.length } });
      this.since = 0;
    }

    // ---------- finding things
    children(parent) {
      return [...this.nodes.values()].filter((n) => n.parent === parent && !n.trash)
        .sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }));
    }
    /** The folder a path names ("" is the top), or null. */
    folderAt(names) {
      let id = "";
      for (const name of names) {
        const n = [...this.nodes.values()].find((x) => x.parent === id && x.name === name && x.dir);
        if (!n) return null;
        id = n.id;
      }
      return id;
    }
    pathOf(id) {
      const names = [];
      for (let n = this.nodes.get(id); n; n = this.nodes.get(n.parent)) names.unshift(n.name);
      return names;
    }
    taken(parent, name, except) {
      for (const n of this.nodes.values()) if (n.parent === parent && n.name === name && n.id !== except && !n.trash) return true;
      return false;
    }
    freeName(parent, name) {
      if (!this.taken(parent, name)) return name;
      const dot = name.lastIndexOf("."), stem = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : "";
      for (let i = 2; ; i++) if (!this.taken(parent, `${stem} (${i})${ext}`)) return `${stem} (${i})${ext}`;
    }

    // ---------- changing things
    async mkdir(parent, name) {
      if (this.taken(parent, name)) throw new Error("Something with that name already exists");
      let id = await folderId(parent, name);
      if (this.raw.has(id)) id = newId();            // (that folder was there once, and was deleted or moved)
      await this._send({ op: "put", id, parent, name, dir: true, size: 0, mtime: Date.now() / 1000 });
      return id;
    }
    async move(id, parent, name) {
      if (this.taken(parent, name, id)) throw new Error("Something with that name already exists");
      if (this._under(id).includes(parent)) throw new Error("A folder can't be moved into itself");
      await this._send({ op: "move", id, parent, name });
    }
    async remove(ids) {
      const objects = [];
      for (const id of ids) for (const sub of this._under(id)) { const n = this.raw.get(sub); const o = n && (n.obj || (n.objs || [])[0]); if (o) objects.push(o); }
      await this._send(...ids.map((id) => ({ op: "del", id })));
      if (objects.length) await call("/api/vault/delete", { method: "POST", json: { ids: objects } }).catch(() => null);
    }

    // ---------- an upload that is taken up again after the page was closed
    // A file is sealed with a key made for that upload, which is in this page's memory until the file
    // is in the list. A page that is reloaded, closed or crashes halfway has lost it, and what had
    // arrived on the server with it: the same file sent again is another object under another key.
    // So for a big file the key and the object's name are kept in this browser while it is on its
    // way (IndexedDB, next to the account's key and sealed with it, like the list above): chosen
    // again, the file goes on from what the server has. It is known again by where it goes, its size
    // and its date, and by the fingerprint of its first piece, so that another file under the same
    // name is not joined onto it. Kept for as long as the server keeps half a file (a week), and
    // gone with the key (signing out).
    async _upload(who, path, blob, mtime) {
      const name = hex(await pieceSum(enc.encode(`${who}
${path}
${blob.size}
${Math.round((mtime || 0) * 1000)}`)));
      const first = hex(await pieceSum(new Uint8Array(await blob.slice(0, CHUNK).arrayBuffer())));
      return { at: "upload:" + name, first };
    }
    /** What an earlier try of this upload was sealed with, {raw, obj}, or null. */
    async resumable(who, path, blob, mtime) {
      try {
        const { at, first } = await this._upload(who, path, blob, mtime);
        const kept = await store("readonly", (s) => s.get(at));
        if (!kept || !kept.sealed || Date.now() - (kept.at || 0) > UPLOAD_KEPT) return null;
        const rec = JSON.parse(dec.decode(await unseal(this.meta, new Uint8Array(kept.sealed), AAD_UPLOAD)));
        // (not the same content after all, or an object that became a file's meanwhile: a new upload)
        const used = [...this.raw.values()].some((n) => n.obj === rec.obj || (n.versions || []).some((x) => x.obj === rec.obj) || (n.objs || []).includes(rec.obj));
        if (rec.first !== first || rec.size !== blob.size || used) { await store("readwrite", (s) => s.delete(at)).catch(() => null); return null; }
        return { raw: unb64(rec.raw), obj: rec.obj };
      } catch (e) { return null; }               // (another key, a private window: a new upload)
    }
    async remember(who, path, blob, mtime, raw, obj) {
      try {
        const { at, first } = await this._upload(who, path, blob, mtime), now = Date.now();
        const sealed = await seal(this.meta, enc.encode(JSON.stringify({ obj, raw: b64(raw), first, size: blob.size })), AAD_UPLOAD);
        await store("readwrite", (s) => {
          s.openCursor(IDBKeyRange.bound("upload:", "upload:￿")).onsuccess = (e) => {      // what nobody came back for
            const c = e.target.result;
            if (!c) return;
            if (now - ((c.value || {}).at || 0) > UPLOAD_KEPT) c.delete();
            c.continue();
          };
          return s.put({ sealed: sealed.buffer, at: now }, at);
        });
      } catch (e) { /* no room, or a private window: then it can't be taken up again after a reload */ }
    }
    async forget(who, path, blob, mtime) {
      try { const { at } = await this._upload(who, path, blob, mtime); await store("readwrite", (s) => s.delete(at)); } catch (e) { /* nothing kept */ }
    }

    // ---------- files
    /** Seal a Blob piece by piece and send it as object `id`. heard({bytes, note}) tells how it is going.
     *  Returns the content's fingerprints (prints), worked out on the way from the pieces as they are
     *  before sealing: they go into the sealed list with the file, to find identical files by. */
    async _store(id, key, blob, heard = () => {}, signal, where = "/api/vault/object", resume = false) {
      const size = blob.size, count = chunksOf(size), total = sealedSize(size), sums = [];
      const head = () => new Uint8Array([...MAGIC, CHUNK_LOG]);
      let offset = 0, part = [head()], back = 0, skip = 0;
      // (`patient`: this is somebody's page, which says why it waits and can be stopped; the server
      // then lets a file wait for room instead of refusing it)
      const to = (at) => `${where}?id=${id}&size=${total}&offset=${at}&patient=1`;
      // An upload that is taken up again (`resume`: the same object and key as the try before, in
      // this page) goes on after the last whole piece the server still has of it. Asked for with a
      // part that starts at the end, which no server takes: it answers with how much it has.
      if (resume && count > 1) {
        const got = await patient(() => steady((s) => call(to(total), { method: "POST", body: new Uint8Array(0), headers: { "Content-Type": "application/octet-stream" }, signal: s }), signal),
                                  signal, (note) => heard({ note })).catch((e) => { if (signal && signal.aborted) throw e; return null; });
        const have = got && !got.ok ? Number(got.offset) || 0 : 0;
        skip = have < HEAD ? 0 : Math.min(count - 1, Math.floor((have - HEAD) / (CHUNK + OVERHEAD)));
        if (skip) { offset = HEAD + skip * (CHUNK + OVERHEAD); part = []; }
      }
      // A part that doesn't arrive (the connection dropped, the network was changed, the server was
      // restarted) is sent again, from where the server says it has the file up to, for as long as it
      // takes: an upload waits for the connection, it doesn't give up or hang. The very bytes that were
      // being sent are kept for that (a piece sealed again would not fit the half of it that arrived).
      // And when the server has less of the file than was sent (it lost what it had, or all of it),
      // the upload goes back to the piece the server has it up to and seals on from there: post()
      // answers with how much the server has, and the loop below goes back. The file is here, so
      // nothing has to be given up for that.
      const post = async () => {
        // The part as plain bytes in this page's memory, not as a Blob: those are kept by the browser
        // in a store of its own, which can be full (little room on this device's disk, a private
        // window), and then every part fails with ERR_BLOB_OUT_OF_MEMORY however often it is sent.
        let body = new Uint8Array(part.reduce((s, p) => s + p.length, 0)), tries = 0;
        part.reduce((at, p) => (body.set(p, at), at + p.length), 0);
        part = [];
        for (;;) {
          let got;
          try {
            got = await steady((s) => call(to(offset),
                                           { method: "POST", body, headers: { "Content-Type": "application/octet-stream" }, signal: s }), signal);
          } catch (e) {
            // (507: the server has no room for the file, and says so. Trying again would only hide that.)
            if ((signal && signal.aborted) || e instanceof Gone || e.status === 507 || (e.status && e.status < 500 && e.status !== 408 && e.status !== 429)) throw e;
            tries++;
            heard({ note: navigator.onLine === false ? "No connection. It carries on when there is one again." : "The connection dropped. Trying again…" });
            await reconnect(Math.min(30000, 1500 * 2 ** Math.min(tries, 5)), signal);
            continue;
          }
          if (got.wait) {
            heard({ note: "Waiting for the server: " + got.wait });
            for (let i = 0; i < 15; i++) { await sleep(1000); if (signal && signal.aborted) throw new DOMException("Stopped", "AbortError"); }
            continue;
          }
          if (!got.ok) {                         // the server has another amount of the file than this part starts at
            const have = Number(got.offset) || 0;
            if (have >= offset && have <= offset + body.length) {      // some or all of this part arrived before it was cut off
              body = body.subarray(have - offset);
              offset = have;
              if (body.length) continue;
              heard({ note: "" });
              return;
            }
            // (the last part arrived whole and only the answer was lost: then the object is there)
            if (where === "/api/vault/object" && offset + body.length === total
                && await call(`${where}?id=${id}`, { raw: true, headers: { Range: "bytes=0-0" } }).then(() => true, () => false)) { offset = total; heard({ note: "" }); return; }
            // (again and again without ever getting further: something is wrong that going back doesn't mend)
            if (have < offset && ++back <= 25) return have;
            throw new Error("The server keeps losing what was sent of this file. Upload it again.");
          }
          offset = got.offset;
          heard({ note: "" });
          return;
        }
      };
      for (let n = 0; n < count; n++) {
        if (signal && signal.aborted) throw new DOMException("Stopped", "AbortError");
        const data = new Uint8Array(await blob.slice(n * CHUNK, Math.min(size, (n + 1) * CHUNK)).arrayBuffer());
        sums.push(await pieceSum(data));
        if (n < skip) {                          // the server has this piece from the try before: only its fingerprint is needed
          if (n % 32 === 31 || n === skip - 1) heard({ bytes: (n + 1) * CHUNK, note: n === skip - 1 ? "" : "Going on where it stopped: looking at what is there already…" });
          continue;
        }
        part.push(await seal(key, data, chunkLabel(n, n === count - 1)));
        if ((n + 1) % PART_CHUNKS === 0 || n === count - 1) {
          const have = await post();
          if (have !== undefined) {              // the server has less: back to the last whole piece it has
            const from = have < HEAD ? 0 : Math.min(n, Math.floor((have - HEAD) / (CHUNK + OVERHEAD)));
            offset = from ? HEAD + from * (CHUNK + OVERHEAD) : 0;
            part = from ? [] : [head()];
            sums.length = from;
            skip = 0;
            n = from - 1;
            heard({ bytes: from * CHUNK, note: "The server had less of this file than was sent. Going on from where it is…" });
            continue;
          }
          heard({ bytes: Math.min(size, (n + 1) * CHUNK) });
        }
      }
      return prints(sums);
    }
    /** Put a file into a folder (replacing one of the same name). Returns its node id. */
    async upload(file, parent, heard, signal, name) {
      name = name || file.name;
      const old = [...this.nodes.values()].find((n) => n.parent === parent && n.name === name);
      if (old && old.dir) throw new Error(`“${name}” is a folder here`);
      const raw = crypto.getRandomValues(new Uint8Array(32)), obj = newId();
      const print = await this._store(obj, await fileKey(raw), file, heard, signal);
      const id = old ? old.id : newId();
      // (the fingerprints name the object they are of: a device that replaces the content without
      // knowing about them leaves them behind, and then they count for nothing)
      await this._send({ op: "put", id, parent, name, dir: false, size: file.size, mtime: (file.lastModified || Date.now()) / 1000,
                         obj, key: b64(raw), print: { obj, ...print } });
      if (old && old.obj) call("/api/vault/delete", { method: "POST", json: { ids: [old.obj] } }).catch(() => null);
      return id;
    }
    async _range(obj, a, n) {
      return call(`/api/vault/object?id=${obj}`, { raw: true, headers: { Range: `bytes=${a}-${a + n - 1}` } });
    }
    async _openAll(key, obj, size) {
      const out = [];
      for await (const piece of this._pieces(key, obj, size)) out.push(piece);
      return new Uint8Array(await new Blob(out).arrayBuffer());
    }
    /** The pieces of a file, opened, from piece `first` on. */
    async *_pieces(key, obj, size, first = 0, signal) {
      const count = chunksOf(size);
      for (let n = first; n < count; n += PART_CHUNKS) {
        if (signal && signal.aborted) throw new DOMException("Stopped", "AbortError");
        const until = Math.min(count, n + PART_CHUNKS);
        let bytes = 0;
        for (let i = n; i < until; i++) bytes += Math.min(CHUNK, size - i * CHUNK) + OVERHEAD;
        const raw = await this._range(obj, HEAD + n * (CHUNK + OVERHEAD), bytes);
        if (raw.length !== bytes) throw new Error("The server gave back less than the file. Try again.");
        let pos = 0;
        for (let i = n; i < until; i++) {
          const want = Math.min(CHUNK, size - i * CHUNK) + OVERHEAD;
          yield await unseal(key, raw.subarray(pos, pos + want), chunkLabel(i, i === count - 1));
          pos += want;
        }
      }
    }
    /** A file's content, piece by piece. */
    async *read(node, signal) {
      yield* this._pieces(await fileKey(unb64(node.key)), node.obj, node.size, 0, signal);
    }
    async blob(node, type, signal) {
      const out = [];
      for await (const piece of this.read(node, signal)) out.push(piece);
      return new Blob(out, { type: type || "application/octet-stream" });
    }
  }

  window.DDV = { usable, wordlist, keyToPhrase, phraseToKey, newKey, derive, keys, call, Gone, Vault, CHUNK, OVERHEAD, HEAD, sealMessage, openMessage,
                 newId, folderId, view, fileKey, seal, unseal, objectsOf, passwordKey, LOCKS, LOCKED, chunkLabel, chunksOf, b64, unb64, pieceSum, prints, steady, reconnect, patient,
                 /** Do what follows as this account (a session the server handed out), next to who the page is signed in as. */
                 as: (session) => { also = String(session || ""); },
                 /** Seal a file and send it as an object to an address that takes them (a file request's). */
                 store: (where, id, key, blob, heard, signal) => Vault.prototype._store.call(null, id, key, blob, heard, signal, where) };
})();
