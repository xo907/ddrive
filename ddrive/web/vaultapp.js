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
    const onSync = location.hash.startsWith("#sync");
    $("#v-nav-files").classList.toggle("on", !onSync);
    $("#v-nav-sync").classList.toggle("on", onSync);
    if (onSync) return drawSync();
    clearTimeout(syncTimer);
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
    if (await attempt(() => vault.sync()) && vault.seq !== before && !document.querySelector("dialog[open]") && !location.hash.startsWith("#sync")) draw();
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
      if (job.parent === here && !document.querySelector("dialog[open]") && !location.hash.startsWith("#sync")) draw();
    }
    working = false;
    stopper = null;
    if (card) { card.el.remove(); card = null; }
    if (done || failed) toast(failed ? `${done} uploaded, ${failed} not` : done === 1 ? "Uploaded" : `${done} files uploaded`);
    if (!location.hash.startsWith("#sync")) draw();
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

  // ------------------------------------------------------------ folder syncing on this account's devices
  // A computer running DDrive Lite (and soon the phone app) that is signed in to this account and has
  // folder syncing turned on waits at the server for word. This page gives it that word: which folder
  // goes where and how, start, stop. Everything said either way is sealed with the account's key, so
  // the server passes it on without learning a device's name, a folder or a file name.
  const MODES = [
    ["backup", "Back up", "→", "Copies new and changed files to your account. Files you delete on the device stay here."],
    ["mirror", "Mirror", "→", "The folder here becomes an exact copy of the device's: deleting there deletes here too."],
    ["two-way", "Two-way sync", "⇄", "Changes and deletions on either side are copied to the other. A file changed on both sides is kept twice."],
    ["move", "Move here", "→", "Copies files to your account, then deletes them on the device once they are safely stored. Frees space there."],
    ["download", "Download backup", "←", "Copies new and changed files from your account to the device. Nothing is deleted there."],
    ["download-mirror", "Download mirror", "←", "The device's folder becomes an exact copy of the one here. What is removed there is kept 30 days."],
  ];
  const modeOf = (id) => MODES.find((m) => m[0] === id) || MODES[0];
  const WHEN = { live: "live", manual: "manual" };
  const whenText = (j) => j.trigger === "interval" ? (j.every >= 60 && j.every % 60 === 0 ? `every ${j.every / 60} h` : `every ${j.every} min`)
    : j.trigger === "daily" ? `daily at ${j.at}` : WHEN[j.trigger] || j.trigger;
  let devices = [], syncTimer = null;

  async function loadDevices() {
    const got = await DDV.call("/api/vault/sync");
    const out = [];
    for (const d of got.devices) {
      try {
        const info = await DDV.openMessage(vault.meta, d.info) || {};
        const jobs = await DDV.openMessage(vault.meta, d.jobs) || [];
        const status = await DDV.openMessage(vault.meta, d.status) || {};
        out.push({ id: d.id, online: d.online, seen: d.seen, rev: d.rev, name: info.name || "A device", windows: info.platform === "win32",
                   home: info.home || "", jobs: Array.isArray(jobs) ? jobs : [], status: status.jobs || {}, running: status.running || null });
      } catch (e) { /* sealed with another key: not this account's */ }
    }
    devices = out;
  }
  const say = async (dev, message) => DDV.call("/api/vault/sync/tell", { method: "POST", json: { device: dev.id, sealed: await DDV.sealMessage(vault.meta, message) } });
  async function saveJobs(dev, jobs) {
    await DDV.call("/api/vault/sync/jobs", { method: "POST", json: { device: dev.id, rev: dev.rev, jobs: await DDV.sealMessage(vault.meta, jobs) } });
  }
  function summary(r) {
    const parts = [[r.up, "copied to your account"], [r.down, "copied to the device"], [r.deleted_remote, "deleted here"], [r.deleted_local, "removed on the device"],
                   [r.moved, "moved off the device"], [r.conflicts, "kept twice (conflict)"]].filter(([n]) => n).map(([n, t]) => `${Number(n).toLocaleString()} ${t}`);
    if (r.errors) parts.push(r.errors === 1 ? "1 problem" : `${r.errors} problems`);
    return parts.length ? parts.join(", ") : `Everything was already in step (${Number(r.files || 0).toLocaleString()} files)`;
  }
  function ago(t) {
    const s = Math.max(0, Date.now() / 1000 - t);
    return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
  }

  function jobCard(dev, j) {
    const s = dev.status[j.id] || {}, p = s.progress, last = s.last || {}, mode = modeOf(j.mode);
    const running = dev.online && s.state === "running";
    const state = !j.enabled ? ["Paused", ""] : !dev.online ? ["Not connected", ""] : running ? ["Syncing", "busy"]
      : s.state === "error" ? ["Problem", "warn"] : s.last_run ? ["Up to date", "ok"] : ["Waiting", ""];
    const act = (fn, done) => async () => { if (await attempt(fn, done)) setTimeout(drawSync, 500); };
    let line;
    if (running && p) {
      const pct = p.total_bytes ? Math.min(100, (p.bytes / p.total_bytes) * 100) : p.total ? (p.done / p.total) * 100 : 0;
      line = [h("div", { class: "sync-line" }, p.total ? `${Number(p.done).toLocaleString()} of ${Number(p.total).toLocaleString()} files` : "Comparing folders…",
                p.total_bytes ? ` · ${size(p.bytes)} of ${size(p.total_bytes)}` : "", p.file ? h("span", { class: "sync-file" }, ` · ${p.action === "down" ? "←" : "→"} ${p.file}`) : ""),
              h("div", { class: "meter" }, h("i", { style: `width:${pct.toFixed(1)}%` })),
              p.note && h("div", { class: "sync-line warn sync-note" }, p.note)];
    } else if (s.error) line = h("div", { class: "sync-line warn" }, s.error);
    else if (s.last_run) line = h("div", { class: "sync-line" }, `${ago(s.last_run)}: ${summary(last)}`,
      last.waiting ? ` · ${last.waiting} still being stored` : "", last.busy ? ` · ${last.busy} in use, next pass` : "");
    else line = h("div", { class: "sync-line" }, "Not run yet");
    const others = dev.jobs.filter((x) => x.id !== j.id);
    return h("div", { class: "sync-card" + (j.enabled ? "" : " off") },
      h("div", { class: "sync-head" },
        h("span", { class: "sync-dir", title: mode[1] }, mode[2]),
        h("div", { class: "sync-title" },
          h("div", { class: "t" }, h("span", { class: "label" }, j.name), h("span", { class: "tag" }, mode[1]), h("span", { class: "tag" }, whenText(j))),
          h("div", { class: "sync-paths" }, h("code", { title: j.local }, j.local), h("span", { class: "a" }, mode[2]), h("code", {}, j.remote))),
        h("span", { class: "sync-state" }, h("span", { class: "dot " + state[1] }), state[0]),
        running ? h("button", { class: "btn ghost small", type: "button", onclick: act(() => say(dev, { do: "stop", id: j.id }), "Stopping") }, "Stop")
                : h("button", { class: "btn ghost small", type: "button", disabled: !dev.online, onclick: act(() => say(dev, { do: "run", id: j.id }), "Syncing") }, "Sync now")),
      line,
      (s.problems || []).length ? h("details", { class: "sync-problems", "data-keep": `${dev.id}-${j.id}` },
        h("summary", {}, s.problems.length === 1 ? "1 note" : `${s.problems.length} notes`), h("ul", {}, s.problems.map((t) => h("li", {}, t)))) : null,
      h("div", { class: "v-job-acts" },
        h("button", { type: "button", onclick: act(async () => {
          if (j.enabled && dev.online) await say(dev, { do: "stop", id: j.id }).catch(() => null);
          await saveJobs(dev, dev.jobs.map((x) => x.id === j.id ? { ...x, enabled: !j.enabled } : x));
        }, j.enabled ? "Paused" : "Resumed") }, j.enabled ? "Pause" : "Resume"),
        h("button", { type: "button", onclick: () => jobForm(dev, j) }, "Change"),
        j.mode === "backup" && h("button", { type: "button", disabled: !dev.online, onclick: act(() => say(dev, { do: "run", id: j.id, again: true }), "Copying everything again") }, "Copy everything again"),
        h("button", { type: "button", class: "danger", onclick: async () => {
          if (await ask({ title: `Stop syncing “${j.name}”?`, ok: "Stop syncing", danger: true, text: `Nothing is deleted: the files stay on ${dev.name} and in your account.` })
              && await attempt(() => saveJobs(dev, others), "No longer synced")) drawSync();
        } }, "Stop syncing this folder")));
  }

  async function drawSync(quiet) {
    clearTimeout(syncTimer);
    if (!location.hash.startsWith("#sync")) return;
    const open = new Set([...main.querySelectorAll("details[open]")].map((d) => d.dataset.keep));
    if (quiet && (document.querySelector("dialog[open]") || !(window.getSelection() || {}).isCollapsed)) { syncTimer = setTimeout(() => drawSync(true), 3000); return; }
    if (!await attempt(loadDevices) && !quiet) return;
    main.replaceChildren(...[
      h("div", { class: "v-bar" }, h("nav", { class: "crumbs" }, h("span", {}, "Sync"))),
      h("p", { class: "muted", style: "max-width:640px;margin:0 0 8px" }, "Folders on your devices kept in step with folders in your account. ",
        "The device does the copying and seals every file before it goes. What is said between this page and the device, down to its name and its folders, is sealed too."),
      devices.flatMap((dev) => [
        h("div", { class: "v-dev" },
          h("h2", {}, dev.name, h("span", { class: "sync-state" }, h("span", { class: "dot " + (dev.online ? "ok" : "") }),
            dev.online ? "Connected" : `Not connected · last heard from ${ago(dev.seen)}`)),
          h("button", { class: "btn ghost", type: "button", onclick: () => jobForm(dev, null) }, "Add a folder")),
        dev.jobs.length ? h("div", { class: "sync-list" }, dev.jobs.map((j) => jobCard(dev, j)))
          : h("div", { class: "v-empty", style: "padding:28px 0" }, h("b", {}, `No folders of ${dev.name} synced yet`), "Add one, e.g. its Documents folder."),
      ]),
      !devices.length && h("div", { class: "v-empty" }, h("b", {}, "No device syncs folders with this account yet"),
        "On a computer, connect DDrive Lite to this site with your 24 words (", h("code", {}, "lite connect"), ") and turn folder syncing on (",
        h("code", {}, "lite sync on"), "). It then shows up here.")].flat().filter(Boolean));
    for (const d of main.querySelectorAll("details")) if (open.has(d.dataset.keep)) d.open = true;
    syncTimer = setTimeout(() => drawSync(true), devices.some((d) => d.running) ? 1500 : 5000);
  }

  /** Pick a folder of a device: it lists its own folders, sealed, through the server. */
  function pickDeviceFolder(dev, start) {
    return new Promise((resolve) => {
      let cur = start || "", chosen = null;
      const list = h("div", { class: "v-folders" }), where = h("input", { spellcheck: "false", placeholder: "Type a path, or pick below" });
      const use = h("button", { class: "btn", type: "button", onclick: () => { chosen = cur; dlg.close(); } }, "Use this folder");
      const dlg = h("dialog", { class: "v-form" }, h("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); load(where.value.trim()); } },
        h("h3", {}, `Folder on ${dev.name}`), where, h("div", { style: "height:10px" }), list,
        h("div", { class: "modal-actions-v" }, h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"), use)));
      dlg.addEventListener("close", () => { dlg.remove(); resolve(chosen); });
      async function load(path) {
        const got = await attempt(async () => {
          const r = await DDV.call("/api/vault/sync/ask", { method: "POST", json: { device: dev.id, sealed: await DDV.sealMessage(vault.meta, { do: "browse", path }) } });
          const data = await DDV.openMessage(vault.meta, r.sealed);
          if (data.error) throw new Error(data.error);
          return data;
        });
        if (!got) return;
        cur = got.path; where.value = got.path; use.disabled = !got.path;
        const rows = [];
        if (got.parent != null) rows.push(h("button", { type: "button", onclick: () => load(got.parent) }, "← Up one level"));
        for (const d of got.dirs || []) rows.push(h("button", { type: "button", onclick: () => load(d.path) }, svg(ICON_DIR, "dir"), d.name));
        if (!(got.dirs || []).length) rows.push(h("button", { type: "button", disabled: true }, "No folders in here"));
        list.replaceChildren(...rows);
      }
      document.body.append(dlg);
      dlg.showModal();
      load(cur);
    });
  }

  function jobForm(dev, j) {
    const v = j ? { ...j } : { name: "", local: "", remote: "", mode: "backup", trigger: "live", every: 60, at: "03:00", exclude: [], enabled: true };
    const field = (label, control, hint) => [h("label", {}, label, control), hint && h("div", { class: "v-hint" }, hint)];
    const local = h("input", { value: v.local, spellcheck: "false", placeholder: dev.windows ? "C:\\Users\\you\\Documents" : (dev.home || "/home/you") + "/Documents" });
    const remote = h("input", { value: v.remote, spellcheck: "false", placeholder: "/Documents" });
    const folders = [];
    const walk = (id, path) => { for (const c of vault.children(id)) if (c.dir) { folders.push(path + "/" + c.name); walk(c.id, path + "/" + c.name); } };
    walk("", "");
    const known = h("datalist", { id: "v-known" }, folders.map((f) => h("option", { value: f })));
    remote.setAttribute("list", "v-known");
    const mode = h("select", {}, MODES.map((m) => h("option", { value: m[0], selected: m[0] === v.mode }, `${m[1]}  ${m[2]}`)));
    const about = h("div", { class: "v-hint" }, modeOf(v.mode)[3]);
    mode.addEventListener("change", () => { about.textContent = modeOf(mode.value)[3]; });
    const trigger = h("select", {}, [["live", "As soon as something changes"], ["interval", "Every few minutes or hours"], ["daily", "Once a day"], ["manual", "Only when I start it"]]
      .map(([id, label]) => h("option", { value: id, selected: id === v.trigger }, label)));
    const every = h("input", { type: "number", min: "1", value: String(v.every) }), at = h("input", { type: "time", value: v.at });
    const everyRow = h("label", {}, "Every (minutes)", every), atRow = h("label", {}, "At", at);
    const show = () => { everyRow.hidden = trigger.value !== "interval"; atRow.hidden = trigger.value !== "daily"; };
    trigger.addEventListener("change", show);
    show();
    const skip = h("input", { value: (v.exclude || []).join(", "), spellcheck: "false", placeholder: "e.g. *.iso, Temp/" });
    const name = h("input", { value: v.name, placeholder: "Optional, e.g. Documents" });
    const err = h("p", { class: "err", role: "alert", hidden: true });
    const dlg = h("dialog", { class: "v-form" }, h("form", { method: "dialog", onsubmit: async (e) => {
      e.preventDefault();
      const job = { id: v.id || DDV.newId().slice(0, 6), enabled: v.enabled !== false, mode: mode.value, trigger: trigger.value,
                    local: local.value.trim().replace(/^"|"$/g, ""), remote: "/" + remote.value.trim().replace(/\\/g, "/").split("/").filter((x) => x && x !== ".").join("/"),
                    every: Math.max(1, Math.round(Number(every.value) || 60)), at: at.value || "03:00",
                    exclude: skip.value.split(/[,\n]/).map((x) => x.trim()).filter(Boolean), name: name.value.trim().slice(0, 80) };
      job.name = job.name || job.local.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || job.local;
      const problem = !job.local ? `Choose a folder on ${dev.name}.`
        : job.remote === "/" && ["mirror", "two-way"].includes(job.mode) ? "Choose a folder in your account (not all of it) for mirror and two-way sync."
        : /^([A-Za-z]:[\\/]*|[\\/]+)$/.test(job.local) && ["download-mirror", "two-way", "move"].includes(job.mode) ? "Choose a folder, not a whole disk, for this mode."
        : dev.jobs.some((x) => x.id !== job.id && x.local === job.local && x.remote === job.remote) ? "Those two folders are already paired." : "";
      if (problem) { err.textContent = problem; err.hidden = false; return; }
      const jobs = j ? dev.jobs.map((x) => x.id === j.id ? job : x) : [...dev.jobs, job];
      if (!await attempt(() => saveJobs(dev, jobs))) return;
      if (!j && job.trigger !== "manual" && dev.online) setTimeout(() => say(dev, { do: "run", id: job.id }).catch(() => null), 1500);
      dlg.close();
      toast(j ? "Saved" : dev.online ? "Added. The first sync starts now." : `Added. It starts when ${dev.name} is connected.`);
      drawSync();
    } }, h("h3", {}, j ? `Change “${j.name}”` : "Sync a folder"), err,
      h("label", {}, `Folder on ${dev.name}`, h("div", { class: "v-pick" }, local,
        h("button", { class: "btn ghost", type: "button", disabled: !dev.online, onclick: async () => { const p = await pickDeviceFolder(dev, local.value.trim() || dev.home); if (p) local.value = p; } }, "Browse…"))),
      field("Folder in your account", remote, "It is created if it isn't there yet."), known,
      field("What to do", mode), about,
      field("When", trigger), everyRow, atRow,
      field("Skip these", skip, "Names or paths inside the folder, separated by commas. Unfinished downloads and temporary files are always skipped."),
      field("Name", name),
      h("div", { class: "modal-actions-v" }, h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"),
        h("button", { class: "btn", type: "submit" }, j ? "Save" : "Add and sync"))));
    dlg.addEventListener("close", () => dlg.remove());
    document.body.append(dlg);
    dlg.showModal();
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
