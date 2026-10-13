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
  const MAGIC = enc.encode("DDV1"), CHUNK_LOG = 20, CHUNK = 1 << CHUNK_LOG, OVERHEAD = 28, HEAD = 5;
  const PART_CHUNKS = 8;                 // sealed pieces go to the server this many at a time
  const SNAPSHOT_EVERY = 400;
  const usable = !!(window.isSecureContext && window.crypto && crypto.subtle && window.indexedDB);

  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const b64 = (b) => { let s = ""; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(s); };
  const unb64 = (s) => { const t = atob(s), b = new Uint8Array(t.length); for (let i = 0; i < t.length; i++) b[i] = t.charCodeAt(i); return b; };
  const newId = () => hex(crypto.getRandomValues(new Uint8Array(16)));
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
      this.nodes = new Map();            // id -> {id, parent, name, dir, size, mtime, obj, key}
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
          this.nodes = new Map();                      // brought back from a copy): what was kept here is no
          this.seq = 0; this.since = 0;                // good, and all of it is read again
          this.kept = -1;
          continue;
        }
        const snap = got.snapshot;
        if (snap && snap.seq > this.seq) {             // behind the last sealed whole list: start from it
          const raw = await this._openAll(this.meta, snap.object, snap.size);
          this.nodes = new Map(Object.entries(JSON.parse(dec.decode(raw)).nodes));
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
        this.nodes = new Map(Object.entries(JSON.parse(dec.decode(raw)).nodes));
        this.seq = kept.seq; this.since = kept.since || 0;
        this.kept = kept.seq;
        return true;
      } catch (e) {                                    // another key, or damaged: read from the server as ever
        this.nodes = new Map(); this.seq = 0; this.since = 0;
        return false;
      }
    }
    /** Keep the list as it is now, for the next visit (nothing to do when it is the one that is kept). */
    async keep(who) {
      if (this.kept === this.seq || this._keeping) return;
      this._keeping = true;
      try {
        const seq = this.seq, since = this.since;
        const sealed = await seal(this.meta, enc.encode(JSON.stringify({ nodes: Object.fromEntries(this.nodes) })), AAD_KEPT);
        await store("readwrite", (s) => s.put({ seq, since, sealed: sealed.buffer, at: Date.now() }, "list:" + who));
        this.kept = seq;
      } catch (e) { /* no room, or a private window: the next visit reads it from the server */ }
      finally { this._keeping = false; }
    }

    _apply(op) {
      if (op.op === "put") {
        const n = { ...op, dir: !!op.dir, size: op.size || 0, mtime: op.mtime || 0 };      // every field, also ones not known here
        delete n.op;
        this.nodes.set(op.id, n);
      } else if (op.op === "set") {
        const n = this.nodes.get(op.id);
        if (n) for (const [k, val] of Object.entries(op.fields || {})) { if (val === null) delete n[k]; else if (k !== "id") n[k] = val; }
      } else if (op.op === "move") {
        const n = this.nodes.get(op.id);
        if (n) { n.parent = op.parent; n.name = op.name; }
      } else if (op.op === "del") {
        for (const id of this._under(op.id)) this.nodes.delete(id);
      }
    }
    _under(id) {
      const out = [], todo = [id];
      while (todo.length) {
        const cur = todo.pop();
        if (!this.nodes.has(cur)) continue;
        out.push(cur);
        for (const n of this.nodes.values()) if (n.parent === cur) todo.push(n.id);
      }
      return out;
    }
    async _send(...ops) {
      const blobs = [];
      let total = 0;
      for (const op of ops) { const b = await seal(this.meta, enc.encode(JSON.stringify(op)), AAD_OP); blobs.push(b); total += 4 + b.length; }
      const batch = new Uint8Array(total), view = new DataView(batch.buffer);
      let pos = 0;
      for (const b of blobs) { view.setUint32(pos, b.length); batch.set(b, pos + 4); pos += 4 + b.length; }
      await call("/api/vault/log", { method: "POST", body: batch, headers: { "Content-Type": "application/octet-stream" } });
      await this.sync();
      if (this.since >= SNAPSHOT_EVERY) await this.snapshot().catch(() => null);
    }
    async snapshot() {
      const state = enc.encode(JSON.stringify({ nodes: Object.fromEntries(this.nodes) }));
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
      const id = newId();
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
      for (const id of ids) for (const sub of this._under(id)) { const n = this.nodes.get(sub); if (n && n.obj) objects.push(n.obj); }
      await this._send(...ids.map((id) => ({ op: "del", id })));
      if (objects.length) await call("/api/vault/delete", { method: "POST", json: { ids: objects } }).catch(() => null);
    }

    // ---------- files
    /** Seal a Blob piece by piece and send it as object `id`. heard({bytes, note}) tells how it is going. */
    async _store(id, key, blob, heard = () => {}, signal, where = "/api/vault/object") {
      const size = blob.size, count = chunksOf(size), total = sealedSize(size);
      let offset = 0, part = [new Uint8Array([...MAGIC, CHUNK_LOG])];
      const post = async () => {
        const body = new Blob(part);
        part = [];
        for (;;) {
          const got = await call(`${where}?id=${id}&size=${total}&offset=${offset}`,
                                 { method: "POST", body, headers: { "Content-Type": "application/octet-stream" }, signal });
          if (got.wait) { heard({ note: "Waiting for the server: " + got.wait }); await sleep(15000); continue; }
          if (!got.ok) throw new Error("The server lost its place in this file. Upload it again.");
          offset = got.offset;
          heard({ note: "" });
          return;
        }
      };
      for (let n = 0; n < count; n++) {
        if (signal && signal.aborted) throw new DOMException("Stopped", "AbortError");
        const data = new Uint8Array(await blob.slice(n * CHUNK, Math.min(size, (n + 1) * CHUNK)).arrayBuffer());
        part.push(await seal(key, data, chunkLabel(n, n === count - 1)));
        if ((n + 1) % PART_CHUNKS === 0 || n === count - 1) { await post(); heard({ bytes: Math.min(size, (n + 1) * CHUNK) }); }
      }
    }
    /** Put a file into a folder (replacing one of the same name). Returns its node id. */
    async upload(file, parent, heard, signal, name) {
      name = name || file.name;
      const old = [...this.nodes.values()].find((n) => n.parent === parent && n.name === name);
      if (old && old.dir) throw new Error(`“${name}” is a folder here`);
      const raw = crypto.getRandomValues(new Uint8Array(32)), obj = newId();
      await this._store(obj, await fileKey(raw), file, heard, signal);
      const id = old ? old.id : newId();
      await this._send({ op: "put", id, parent, name, dir: false, size: file.size, mtime: (file.lastModified || Date.now()) / 1000,
                         obj, key: b64(raw) });
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
                 newId, fileKey, seal, unseal, chunkLabel, chunksOf, b64, unb64,
                 /** Do what follows as this account (a session the server handed out), next to who the page is signed in as. */
                 as: (session) => { also = String(session || ""); },
                 /** Seal a file and send it as an object to an address that takes them (a file request's). */
                 store: (where, id, key, blob, heard, signal) => Vault.prototype._store.call(null, id, key, blob, heard, signal, where) };
})();
