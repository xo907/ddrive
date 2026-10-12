// A link to something in an end-to-end encrypted account, as the person who got the link sees it.
// The key is the part of the address after the # (a browser never sends that part anywhere). The
// server hands out sealed pieces; they are opened here.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const enc = new TextEncoder(), dec = new TextDecoder();
  const sid = location.pathname.split("/")[2] || "";
  const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(2)} GB`;
  const ext = (name) => name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  const TYPES = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp", avif: "image/avif", bmp: "image/bmp",
                  mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", mov: "video/quicktime", mp3: "audio/mpeg", m4a: "audio/mp4", ogg: "audio/ogg",
                  wav: "audio/wav", flac: "audio/flac", opus: "audio/ogg", pdf: "application/pdf" };
  const TEXT = new Set("txt md log csv json xml yml yaml ini conf py js ts c h cpp java kt rs go sh html css sql".split(" "));
  const SHOW_MAX = 200 * 1024 * 1024;
  function say(title, text) { $("title").textContent = title; $("note").textContent = text || ""; $("files").replaceChildren(); $("view").replaceChildren(); }
  function el(tag, props, ...kids) { const e = Object.assign(document.createElement(tag), props || {}); e.append(...kids.filter(Boolean)); return e; }

  async function keyFrom(secret, password) {
    const base = await crypto.subtle.importKey("raw", enc.encode(password || ""), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: secret, iterations: password ? 300000 : 1 }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function readRange(f, start, end, key) {
    if (!f.size || start > end) return new Uint8Array(0);
    const C = DDV.CHUNK, count = DDV.chunksOf(f.size), first = Math.floor(start / C), last = Math.min(count - 1, Math.floor(end / C));
    let bytes = 0;
    for (let i = first; i <= last; i++) bytes += Math.min(C, f.size - i * C) + DDV.OVERHEAD;
    const from = DDV.HEAD + first * (C + DDV.OVERHEAD);
    const r = await fetch(`/v/${sid}/o/${f.obj}`, { headers: { Range: `bytes=${from}-${from + bytes - 1}` } });
    if (!r.ok) throw new Error(r.status === 404 ? "This link has expired." : "The file could not be fetched.");
    const raw = new Uint8Array(await r.arrayBuffer()), parts = [];
    let pos = 0;
    for (let i = first; i <= last; i++) {
      const want = Math.min(C, f.size - i * C) + DDV.OVERHEAD;
      parts.push(await DDV.unseal(key, raw.subarray(pos, pos + want), DDV.chunkLabel(i, i === count - 1)));
      pos += want;
    }
    const all = new Uint8Array(await new Blob(parts).arrayBuffer());
    return all.subarray(start - first * C, Math.min(all.length, end - first * C + 1));
  }
  async function whole(f, heard) {
    const key = await DDV.fileKey(DDV.unb64(f.key)), C = DDV.CHUNK * 8, parts = [];
    for (let pos = 0; pos < f.size; pos += C) { parts.push(await readRange(f, pos, Math.min(f.size, pos + C) - 1, key)); if (heard) heard(Math.min(f.size, pos + C)); }
    return parts;
  }
  async function download(f, button) {
    const name = f.path.split("/").pop(), was = button.textContent;
    button.disabled = true;
    try {
      if (window.showSaveFilePicker && f.size > 64 * 1024 * 1024) {
        let handle;
        try { handle = await window.showSaveFilePicker({ suggestedName: name }); } catch (e) { return; }
        const out = await handle.createWritable(), key = await DDV.fileKey(DDV.unb64(f.key)), C = DDV.CHUNK * 8;
        try {
          for (let pos = 0; pos < f.size; pos += C) { await out.write(await readRange(f, pos, Math.min(f.size, pos + C) - 1, key)); button.textContent = `${Math.floor(Math.min(f.size, pos + C) / f.size * 100)}%`; }
          await out.close();
        } catch (e) { await out.abort().catch(() => null); throw e; }
      } else {
        if (f.size > 1500 * 1024 * 1024) throw new Error("A file this big can only be downloaded with Chrome or Edge.");
        const url = URL.createObjectURL(new Blob(await whole(f, (n) => { button.textContent = `${Math.floor(n / f.size * 100)}%`; }), { type: "application/octet-stream" }));
        const a = el("a", { href: url, download: name });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
    } catch (e) { $("note").textContent = e.message; }
    finally { button.disabled = false; button.textContent = was; }
  }
  async function show(f) {
    const name = f.path.split("/").pop(), type = TYPES[ext(name)], view = $("view");
    view.replaceChildren(el("p", { className: "muted", textContent: "Opening…" }));
    try {
      if (f.size > SHOW_MAX) throw new Error("Too big to show here. Download it instead.");
      const parts = await whole(f);
      if (TEXT.has(ext(name)) && !type) { view.replaceChildren(el("pre", { textContent: dec.decode(await new Blob(parts).arrayBuffer()).slice(0, 400000) })); return; }
      const url = URL.createObjectURL(new Blob(parts, { type }));
      const kind = type.split("/")[0];
      view.replaceChildren(kind === "image" ? el("img", { src: url, alt: name }) : kind === "video" ? el("video", { src: url, controls: true })
        : kind === "audio" ? el("audio", { src: url, controls: true }) : el("iframe", { src: url, title: name }));
    } catch (e) { view.replaceChildren(el("p", { className: "err", textContent: e.message })); }
  }

  // A file request: files go into somebody's folder. Each is sealed here with a key of its own; that key,
  // with the file's name and size, goes in a note sealed with the link's key. The server can open neither.
  function request(m, key) {
    document.title = `Send files · DDrive`;
    $("title").textContent = `Send files into “${m.name}”`;
    $("note").textContent = `Files up to ${size(m.max)} each. They are encrypted in your browser before they leave it; you can't see what is in the folder.`;
    const list = el("div"), pick = el("input", { type: "file", multiple: true });
    const box = el("div", { className: "pw" }, pick);
    let busy = Promise.resolve();
    async function send(file, row, state) {
      if (file.size > m.max) { state.textContent = "too big"; state.className = "size err"; return; }
      try {
        const raw = crypto.getRandomValues(new Uint8Array(32)), obj = DDV.newId();
        await DDV.store(`/v/${sid}/o`, obj, await DDV.fileKey(raw), file, (p) => {
          if (p.bytes !== undefined) state.textContent = `${Math.floor(p.bytes / (file.size || 1) * 100)}%`;
          if (p.note) state.textContent = "waiting…";
        });
        const note = { name: file.name, size: file.size, mtime: (file.lastModified || Date.now()) / 1000, obj, key: DDV.b64(raw) };
        const r = await fetch(`/v/${sid}/n`, { method: "POST", headers: { "Content-Type": "application/json", "X-DD": "1" },
                                              body: JSON.stringify({ obj, sealed: DDV.b64(await DDV.seal(key, enc.encode(JSON.stringify(note)), enc.encode("ddv1 drop"))) }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "It could not be sent");
        state.textContent = "sent";
      } catch (e) { state.textContent = e.message || "failed"; state.className = "size err"; }
    }
    const add = (files) => {
      for (const file of files) {
        const state = el("span", { className: "size", textContent: "waiting" }), row = el("div", { className: "row" }, el("span", { className: "name", textContent: file.name }),
                                                                                           el("span", { className: "size", textContent: size(file.size) }), state);
        list.append(row);
        busy = busy.then(() => send(file, row, state));
      }
    };
    pick.onchange = () => { add(pick.files); pick.value = ""; };
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer && e.dataTransfer.files) add(e.dataTransfer.files); });
    $("files").replaceChildren(box, list);
  }

  function render(m) {
    document.title = `${m.name} · DDrive`;
    $("title").textContent = m.name;
    const total = m.files.reduce((s, f) => s + f.size, 0);
    $("note").textContent = m.dir ? `${m.files.length} file${m.files.length === 1 ? "" : "s"}, ${size(total)}` : size(total);
    const showable = (f) => TYPES[ext(f.path)] || TEXT.has(ext(f.path));
    $("files").replaceChildren(...m.files.map((f) => {
      const dl = m.download !== false && el("button", { className: "btn", type: "button", textContent: "Download" });
      if (dl) dl.onclick = () => download(f, dl);
      const open = showable(f) && el("button", { className: "btn ghost", type: "button", textContent: "View", onclick: () => show(f) });
      return el("div", { className: "row" }, el("span", { className: "name", textContent: f.path }), el("span", { className: "size", textContent: size(f.size) }), open, dl);
    }));
    if (!m.files.length) $("files").replaceChildren(el("p", { className: "muted", textContent: "This folder is empty." }));
    if (!m.dir && m.files[0] && showable(m.files[0]) && m.files[0].size <= SHOW_MAX) show(m.files[0]);
  }

  async function start() {
    if (!(window.isSecureContext && window.crypto && crypto.subtle && window.DDV)) return say("This link can't be opened here", "It needs an https address: a browser doesn't decrypt on plain http.");
    let secret;
    try { secret = DDV.unb64(location.hash.slice(1).replace(/-/g, "+").replace(/_/g, "/")); } catch (e) { secret = new Uint8Array(0); }
    if (secret.length !== 32) return say("This link is incomplete", "The part after the # is the key. Ask for the whole link again.");
    const r = await fetch(`/v/${sid}/m`).catch(() => null);
    if (!r || !r.ok) return say("This link has expired", "Ask for a new one.");
    const info = await r.json(), sealed = DDV.unb64(info.sealed);
    let key;
    const open = async (password) => { key = await keyFrom(secret, password); return JSON.parse(dec.decode(await DDV.unseal(key, sealed, enc.encode("ddv1 share")))); };
    const render0 = render;
    render = (m) => (m.upload ? request(m, key) : render0(m));
    if (!info.password) {
      try { render(await open("")); } catch (e) { say("This link can't be opened", "Its key doesn't fit. Ask for the whole link again."); }
      return;
    }
    say("This link needs a password", "");
    const input = el("input", { type: "password", autocomplete: "off", placeholder: "Password" }), go = el("button", { className: "btn", type: "submit", textContent: "Open" });
    const form = el("form", { className: "pw" }, input, go);
    form.onsubmit = async (e) => {
      e.preventDefault();
      go.disabled = true;
      try { const m = await open(input.value); form.remove(); render(m); }
      catch (e2) { $("note").textContent = "That password isn't right."; go.disabled = false; }
    };
    $("files").replaceChildren(form);
    input.focus();
  }
  window.addEventListener("hashchange", () => location.reload());       // another key: another link
  start();
})();
