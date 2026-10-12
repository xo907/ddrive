"use strict";
/* The files page of an end-to-end encrypted account. Everything that needs a file or a name in the
 * clear happens here, in the browser (vaultcore.js does the sealing and opening); the server is asked
 * for sealed things only. */
(function () {
  const $ = (sel) => document.querySelector(sel);
  const main = $("#v-main"), tray = $("#tray");
  let vault = null, here = "";               // the account; the id of the folder being shown ("" = the top)

  // ------------------------------------------------------------ small things
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }
  function svg(path, cls) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 20 20"); s.setAttribute("aria-hidden", "true");
    if (cls) s.setAttribute("class", cls);
    s.innerHTML = path;                  // only ever the two constants below
    return s;
  }
  const ICON_DIR = '<path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 1.800h6.500a1.500 1.500 0 0 1 1.500 1.500v6.200a1.500 1.500 0 0 1-1.500 1.500h-11a1.500 1.500 0 0 1-1.500-1.500z"/>';
  const ICON_FILE = '<path d="M6 2.5h5.500l3.500 3.500v10.500a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z"/><path d="M11.500 2.500v3.500h3.500"/>';
  function size(n) {
    n = Number(n) || 0;
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return i ? `${n.toFixed(n < 10 ? 1 : 0)} ${units[i]}` : `${n} bytes`;
  }
  const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  const when = (t) => t ? fmt.format(new Date(t * 1000)) : "";
  let toastTimer = null;
  function toast(text) {
    const t = $("#toast");
    t.textContent = text; t.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("on"), 3600);
  }
  /** A small dialog: a question, optionally with a field or a list to choose from. Resolves to the answer or null. */
  function ask({ title, text, value, options, ok = "OK", danger }) {
    return new Promise((resolve) => {
      const input = value != null ? h("input", { value, spellcheck: "false", autocomplete: "off" }) : null;
      const pick = options ? h("select", {}, options.map(([v, label]) => h("option", { value: v }, label))) : null;
      let answer = null;
      const dlg = h("dialog", {}, h("form", { method: "dialog", onsubmit: () => { answer = input ? input.value.trim() : pick ? pick.value : true; } },
        h("h3", {}, title), h("p", {}, text || ""), input, pick,
        h("div", { class: "modal-actions-v" },
          h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"),
          h("button", { class: "btn" + (danger ? " danger" : ""), type: "submit" }, ok))));
      dlg.addEventListener("close", () => { dlg.remove(); resolve(answer); });
      document.body.append(dlg);
      dlg.showModal();
      if (input) { input.focus(); const dot = input.value.lastIndexOf("."); input.setSelectionRange(0, dot > 0 ? dot : input.value.length); }
    });
  }
  async function attempt(fn, done) {
    try { const r = await fn(); if (done) toast(done); return r === undefined ? true : r; }
    catch (e) {
      if (e instanceof DDV.Gone) { location.reload(); return null; }
      if (e.name !== "AbortError") toast(e.message || "That didn't work");
      return null;
    }
  }

  // ------------------------------------------------------------ where we are
  const names = () => decodeURIComponent(location.hash.replace(/^#\/?/, "")).split("/").filter(Boolean);
  const hashOf = (parts) => "#/" + parts.map(encodeURIComponent).join("/");
  function go(id) { location.hash = hashOf(vault.pathOf(id)); }

  function draw() {
    const parts = names();
    const id = vault.folderAt(parts);
    if (id == null) { location.hash = "#/"; return; }
    here = id;
    const crumbs = h("nav", { class: "crumbs", "aria-label": "Folders" }, h(parts.length ? "a" : "span", { href: "#/" }, "Files"),
      parts.flatMap((name, i) => [h("span", { class: "sep" }, "/"),
        h(i < parts.length - 1 ? "a" : "span", { href: hashOf(parts.slice(0, i + 1)) }, name)]));
    const items = vault.children(here);
    const rows = items.map((n) => {
      const open = () => n.dir ? go(n.id) : view(n);
      return h("div", { class: "v-row" },
        h("div", { class: "v-name", role: "button", tabindex: "0", onclick: open, onkeydown: (e) => { if (e.key === "Enter") open(); } },
          svg(n.dir ? ICON_DIR : ICON_FILE, n.dir ? "dir" : ""), h("span", { title: n.name }, n.name)),
        h("div", { class: "v-size" }, n.dir ? "" : size(n.size)), h("div", { class: "v-date" }, n.dir ? "" : when(n.mtime)),
        h("div", { class: "v-acts" },
          !n.dir && h("button", { type: "button", onclick: () => download(n) }, "Download"),
          h("button", { type: "button", onclick: () => rename(n) }, "Rename"),
          h("button", { type: "button", onclick: () => moveTo(n) }, "Move"),
          h("button", { type: "button", class: "danger", onclick: () => remove(n) }, "Delete")));
    });
    main.replaceChildren(
      h("div", { class: "v-bar" }, crumbs,
        h("button", { class: "btn ghost", type: "button", onclick: newFolder }, "New folder"),
        h("button", { class: "btn", type: "button", onclick: () => $("#v-pick").click() }, "Upload")),
      items.length ? h("div", {}, h("div", { class: "v-row head" }, h("div", {}, "Name"), h("div", { class: "v-size" }, "Size"),
                                    h("div", { class: "v-date" }, "Changed"), h("div", {})), rows)
        : h("div", { class: "v-empty" }, h("b", {}, parts.length ? "This folder is empty" : "Nothing here yet"),
            "Drop files here or press Upload. They are encrypted in this browser before they are sent."),
      h("p", { class: "v-foot" }, "Files, folders and their names are encrypted in this browser with your 24 words and only then sent. ",
        "The server stores them and hands them back; it has no key to read them, and nobody can reset the words. ",
        h("button", { type: "button", onclick: deleteAccount }, "Delete this account…")));
    usage();
  }
  async function usage() {
    const st = await DDV.call("/api/vault").catch(() => null);
    if (st) $("#v-used").textContent = st.quota ? `${size(st.used)} of ${size(st.quota)} used` : `${size(st.used)} used`;
  }
  async function refresh() {
    if (!vault) return;
    const before = vault.seq;
    if (await attempt(() => vault.sync()) && vault.seq !== before && !document.querySelector("dialog[open]")) draw();
  }

  // ------------------------------------------------------------ doing things
  async function newFolder() {
    const name = await ask({ title: "New folder", value: "New folder", ok: "Create" });
    if (name && await attempt(() => vault.mkdir(here, cleanName(name)))) draw();
  }
  const cleanName = (name) => {
    name = String(name).replace(/[\\/]/g, " ").trim();
    if (!name || name === "." || name === "..") throw new Error("That isn't a name");
    return name.slice(0, 240);
  };
  async function rename(n) {
    const name = await ask({ title: `Rename “${n.name}”`, value: n.name, ok: "Rename" });
    if (name && name !== n.name && await attempt(() => vault.move(n.id, n.parent, cleanName(name)))) draw();
  }
  async function moveTo(n) {
    const folders = [["", "Files (the top)"]];
    const walk = (id, prefix) => {
      for (const c of vault.children(id)) {
        if (!c.dir || c.id === n.id) continue;
        folders.push([c.id, prefix + c.name]);
        walk(c.id, prefix + c.name + " / ");
      }
    };
    walk("", "");
    const to = await ask({ title: `Move “${n.name}” to`, options: folders.filter(([id]) => id !== n.parent), ok: "Move" });
    if (to != null && await attempt(() => vault.move(n.id, to, n.name), "Moved")) draw();
  }
  async function remove(n) {
    const sure = await ask({ title: `Delete “${n.name}”?`, ok: "Delete", danger: true,
                             text: n.dir ? "The folder and everything in it is deleted." : "The file is deleted." });
    if (sure && await attempt(() => vault.remove([n.id]), "Deleted")) draw();
  }
  async function deleteAccount() {
    const word = await ask({ title: "Delete this account?", value: "", ok: "Delete the account", danger: true,
                             text: "Everything in it is deleted for good. Type delete to confirm." });
    if (!word) return;
    if (await attempt(() => DDV.call("/api/account/delete", { method: "POST", json: { confirm: word } }))) {
      await DDV.keys.clear().catch(() => null);
      location.replace("/");
    }
  }

  // ------------------------------------------------------------ uploading
  const queue = [];
  let working = false, card = null, stopper = null;
  function addUploads(files, parent) {
    for (const f of files) queue.push({ file: f, parent, name: f.name });
    pump();
  }
  function showCard(text, sub, pct) {
    if (!card) {
      const bar = h("i", { style: "width:0%" }), title = h("span", {}), line = h("div", { class: "sub" });
      const stop = h("button", { class: "tray-x", type: "button", title: "Stop uploading", "aria-label": "Stop uploading",
                                 onclick: () => { queue.length = 0; if (stopper) stopper.abort(); } }, "✕");
      card = { el: h("div", { class: "item discord" }, h("div", { class: "n" }, title, stop), h("div", { class: "meter" }, bar), line), bar, title, line };
      tray.append(card.el);
    }
    card.title.textContent = text;
    card.line.textContent = sub;
    card.bar.style.width = `${pct.toFixed(1)}%`;
  }
  async function pump() {
    if (working) return;
    working = true;
    let done = 0, failed = 0;
    while (queue.length) {
      const job = queue.shift();
      stopper = new AbortController();
      let note = "";
      const say = (bytes) => showCard(`Uploading ${job.name}`, note || `Encrypted here, then sent · ${size(bytes)} of ${size(job.file.size)}` +
                                      (queue.length ? ` · ${queue.length} more` : ""), job.file.size ? (bytes / job.file.size) * 100 : 100);
      say(0);
      let sent = 0;
      const ok = await attempt(() => vault.upload(job.file, job.parent, (p) => {
        if (p.note !== undefined) note = p.note;
        if (p.bytes !== undefined) sent = p.bytes;
        say(sent);
      }, stopper.signal, job.name));
      if (ok) done++; else failed++;
      if (job.parent === here && !document.querySelector("dialog[open]")) draw();
    }
    working = false;
    stopper = null;
    if (card) { card.el.remove(); card = null; }
    if (done || failed) toast(failed ? `${done} uploaded, ${failed} not` : done === 1 ? "Uploaded" : `${done} files uploaded`);
    draw();
  }
  $("#v-pick").addEventListener("change", (e) => { addUploads([...e.target.files], here); e.target.value = ""; });
  // dropping files, and whole folders, onto the page
  let depth = 0;
  window.addEventListener("dragenter", (e) => { if (vault && e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { depth++; main.classList.add("v-drop"); e.preventDefault(); } });
  window.addEventListener("dragover", (e) => { if (vault && e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; main.classList.remove("v-drop"); } });
  window.addEventListener("drop", async (e) => {
    depth = 0; main.classList.remove("v-drop");
    if (!vault || !e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    const target = here, entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!entries.some((en) => en.isDirectory)) return addUploads([...e.dataTransfer.files], target);
    const fileOf = (en) => new Promise((res, rej) => en.file(res, rej));
    const list = (dir) => new Promise((res, rej) => { const r = dir.createReader(), all = []; (function more() { r.readEntries((b) => { if (!b.length) res(all); else { all.push(...b); more(); } }, rej); })(); });
    const walk = async (en, parent) => {
      if (en.isFile) { const f = await fileOf(en); queue.push({ file: f, parent, name: f.name }); return; }
      const existing = vault.children(parent).find((c) => c.dir && c.name === en.name);
      const id = existing ? existing.id : await vault.mkdir(parent, en.name);
      for (const child of await list(en)) await walk(child, id);
    };
    if (await attempt(async () => { for (const en of entries) await walk(en, target); })) { draw(); pump(); }
  });

  // ------------------------------------------------------------ opening and downloading
  const TYPES = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp", avif: "image/avif",
                  bmp: "image/bmp", svg: "image/svg+xml", mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", mov: "video/quicktime",
                  mp3: "audio/mpeg", m4a: "audio/mp4", ogg: "audio/ogg", wav: "audio/wav", flac: "audio/flac", pdf: "application/pdf",
                  txt: "text/plain", md: "text/plain", json: "text/plain", csv: "text/plain", log: "text/plain", xml: "text/plain",
                  js: "text/plain", py: "text/plain", html: "text/plain", css: "text/plain", yml: "text/plain", yaml: "text/plain", ini: "text/plain" };
  const typeOf = (name) => TYPES[name.slice(name.lastIndexOf(".") + 1).toLowerCase()] || "";
  const VIEW_MAX = { image: 80 << 20, text: 2 << 20, audio: 300 << 20, video: 300 << 20 };

  async function view(n) {
    const type = typeOf(n.name), kind = type.split("/")[0];
    if (!VIEW_MAX[kind] || type === "image/svg+xml" || n.size > VIEW_MAX[kind]) return download(n);   // (an SVG can carry scripts: not shown)
    const stop = new AbortController();
    const body = h("div", { class: "v-view-body" }, h("p", { class: "muted" }, "Decrypting…"));
    let url = null;
    const dlg = h("dialog", { class: "v-view" },
      h("div", { class: "v-view-head" }, h("b", {}, n.name), h("span", { class: "muted" }, size(n.size)),
        h("button", { class: "btn ghost small", type: "button", onclick: () => download(n) }, "Download"),
        h("button", { class: "btn ghost small", type: "button", onclick: () => dlg.close() }, "Close")), body);
    dlg.addEventListener("close", () => { stop.abort(); dlg.remove(); if (url) URL.revokeObjectURL(url); });
    document.body.append(dlg);
    dlg.showModal();
    const blob = await attempt(() => vault.blob(n, type, stop.signal));
    if (!blob || !dlg.isConnected) return;
    if (kind === "text") return body.replaceChildren(h("pre", {}, await blob.text()));
    url = URL.createObjectURL(blob);
    body.replaceChildren(kind === "image" ? h("img", { src: url, alt: n.name })
      : h(kind, { src: url, controls: true, autoplay: kind === "video" }));
  }

  async function download(n) {
    const stop = new AbortController();
    const bar = h("i", { style: "width:0%" }), line = h("div", { class: "sub" }, "Decrypting…");
    const el = h("div", { class: "item" }, h("div", { class: "n" }, h("span", {}, `Downloading ${n.name}`),
      h("button", { class: "tray-x", type: "button", title: "Stop", "aria-label": "Stop the download", onclick: () => stop.abort() }, "✕")),
      h("div", { class: "meter" }, bar), line);
    let got = 0;
    const step = (piece) => { got += piece.length; bar.style.width = `${n.size ? (got / n.size) * 100 : 100}%`; line.textContent = `${size(got)} of ${size(n.size)}`; };
    try {
      if (window.showSaveFilePicker) {
        // straight onto the disk, piece by piece: a file of any size, without holding it in memory
        let handle;
        try { handle = await window.showSaveFilePicker({ suggestedName: n.name }); }
        catch (e) { return; }                                  // cancelled
        tray.append(el);
        const out = await handle.createWritable();
        try {
          for await (const piece of vault.read(n, stop.signal)) { await out.write(piece); step(piece); }
          await out.close();
        } catch (e) { await out.abort().catch(() => null); throw e; }
      } else {
        if (n.size > 1500 * 1024 * 1024) throw new Error("This browser can only download files this big with Chrome or Edge, which write them straight to disk.");
        tray.append(el);
        const parts = [];
        for await (const piece of vault.read(n, stop.signal)) { parts.push(piece); step(piece); }
        const url = URL.createObjectURL(new Blob(parts, { type: "application/octet-stream" }));
        const a = h("a", { href: url, download: n.name });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
      toast(`Downloaded ${n.name}`);
    } catch (e) {
      if (e instanceof DDV.Gone) location.reload();
      else if (e.name !== "AbortError") toast(e.message || "The download failed");
    } finally {
      el.remove();
    }
  }

  // ------------------------------------------------------------ starting
  $("#v-out").addEventListener("click", async () => {
    await DDV.keys.clear().catch(() => null);
    await DDV.call("/api/logout", { method: "POST", json: {} }).catch(() => null);
    location.replace("/");
  });

  /** This browser is signed in but has no key (it was cleared, or the session came from elsewhere): ask for the words. */
  function unlock(problem) {
    const words = h("textarea", { rows: "4", autocomplete: "off", autocapitalize: "none", spellcheck: "false", placeholder: "the 24 words, in order" });
    const err = h("p", { class: "err", role: "alert", hidden: !problem }, problem || "");
    const open = h("button", { class: "btn", type: "submit" }, "Open my files");
    main.replaceChildren(h("form", { class: "v-unlock", onsubmit: async (e) => {
      e.preventDefault();
      open.disabled = true; err.hidden = true;
      try {
        const d = await DDV.derive(await DDV.phraseToKey(words.value));
        const r = await fetch("/login", { method: "POST", credentials: "same-origin", body: new URLSearchParams({ auth: d.auth }) });
        if (!(r.ok && r.redirected)) throw new Error("these are not this account's words");
        await DDV.keys.put(d.meta);
        location.reload();
      } catch (e2) {
        err.textContent = `That didn't open it: ${e2.message}.`; err.hidden = false; open.disabled = false;
      }
    } }, h("h1", {}, "Your 24 words"),
      h("p", { class: "muted" }, "This browser doesn't have the key to your files (any more). The words are used here to open them; they are not sent anywhere."),
      err, words, open));
  }

  async function start() {
    if (!DDV.usable) {
      main.replaceChildren(h("div", { class: "v-empty" }, h("b", {}, "This address can't open an end-to-end encrypted account"),
        "The browser only encrypts on an https address (or on the computer itself). Open this site by its https address."));
      return;
    }
    const meta = await DDV.keys.get().catch(() => null);
    if (!meta) return unlock("");
    const v = new DDV.Vault(meta);
    try { await v.sync(); }
    catch (e) {
      if (e instanceof DDV.Gone) return location.reload();
      if (/can't be opened/.test(e.message)) return unlock("The key kept in this browser is for another account.");
      main.replaceChildren(h("div", { class: "v-empty" }, h("b", {}, "Your files could not be loaded"), e.message,
        h("p", {}, h("button", { class: "btn ghost", type: "button", onclick: () => location.reload() }, "Try again"))));
      return;
    }
    vault = v;
    window.addEventListener("hashchange", draw);
    draw();
    setInterval(refresh, 15000);                 // what another device of this account did shows up by itself
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }
  start();
})();
