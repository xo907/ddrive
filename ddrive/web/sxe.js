"use strict";
/* Files encrypted with a password by SaveX (the browser extension that saves posts' photos and videos):
 * "<name>.sxe". They are opened here, in the browser, with the password typed here; the password and what
 * is in the file go nowhere else. The server only ever has the file as it was saved, encrypted.
 *
 * The format (SaveX: src/lib/sxe.js, which makes them; change the two together):
 *   0 "SXE1" · 4 version 1 · 5 chunk size as a power of two · 6 PBKDF2 rounds (u32 BE) · 10 password salt (16)
 *   · 26 file salt (16) · 42 length of the sealed info (u32 BE) · 46 sealed info: JSON {name, type, size}
 *   · then the content in sealed chunks of (chunk size + 16).
 * Password -> PBKDF2-SHA256(password salt) -> base key -> HKDF-SHA256(file salt, "savex sxe1 file") -> AES-256-GCM.
 * Every seal has bytes 0-45 as associated data. A chunk's nonce is its number (8 bytes BE) and a flag (4 bytes:
 * 1 on the last chunk); the info's is eight 0xff bytes and the flag 2.
 *
 * A password is asked for once per page for everything SaveX sealed with it (the files share its salt). */
(function () {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const EXT = ".sxe", HEAD = 46, TAG = 16, INFO = enc.encode("savex sxe1 file");
  const keys = new Map();                // "<password salt>:<rounds>" -> base key, for as long as this page is open

  const u32 = (b, at) => ((b[at] << 24) | (b[at + 1] << 16) | (b[at + 2] << 8) | b[at + 3]) >>> 0;
  const put32 = (b, at, v) => { b[at] = v >>> 24; b[at + 1] = v >>> 16; b[at + 2] = v >>> 8; b[at + 3] = v; };
  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  function nonce(index, flag) {
    const n = new Uint8Array(12);
    put32(n, 0, Math.floor(index / 2 ** 32)); put32(n, 4, index >>> 0); put32(n, 8, flag);
    return n;
  }
  const INFO_NONCE = (() => { const n = new Uint8Array(12).fill(0xff); put32(n, 8, 2); return n; })();

  function readHead(b) {
    if (b.length < HEAD || dec.decode(b.subarray(0, 4)) !== "SXE1" || b[4] !== 1) return null;
    return { rounds: u32(b, 6), salt: b.slice(10, 26), fileSalt: b.slice(26, 42), infoLen: u32(b, 42), chunkLog: b[5] };
  }
  async function baseKey(password, salt, rounds) {
    const pw = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: rounds }, pw, 256);
    return crypto.subtle.importKey("raw", bits, "HKDF", false, ["deriveKey"]);
  }
  const fileKey = (base, fileSalt) => crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: fileSalt, info: INFO }, base,
                                                              { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  async function openInfo(head, h, sealedInfo, base) {
    const k = await fileKey(base, h.fileSalt);
    try { return { k, info: JSON.parse(dec.decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv: INFO_NONCE, additionalData: head }, k, sealedInfo))) }; }
    catch { return null; }
  }

  /** Opens an encrypted Blob: {info: {name, type, size}, blob}. Asks for the password when it isn't known yet;
   *  null when the asking was given up. `heard(fraction)` hears how far it is. */
  async function decrypt(file, heard = () => {}) {
    if (!(window.isSecureContext && crypto.subtle)) throw new Error("This page needs HTTPS (or localhost) to open encrypted files");
    const head = new Uint8Array(await file.slice(0, HEAD).arrayBuffer());
    const h = readHead(head);
    if (!h) throw new Error("This isn't a SaveX encrypted file, or it was made by a newer SaveX");
    const sealedInfo = await file.slice(HEAD, HEAD + h.infoLen).arrayBuffer();
    const id = `${hex(h.salt)}:${h.rounds}`;
    let opened = keys.has(id) ? await openInfo(head, h, sealedInfo, keys.get(id)) : null;
    if (!opened) {
      opened = await askPassword(async (pw) => {
        const base = await baseKey(pw, h.salt, h.rounds);
        const r = await openInfo(head, h, sealedInfo, base);
        if (r) keys.set(id, base);
        return r;
      });
      if (!opened) return null;
    }
    const step = (1 << h.chunkLog) + TAG, start = HEAD + h.infoLen;
    const count = Math.max(1, Math.ceil((file.size - start) / step)), parts = [];
    for (let i = 0; i < count; i++) {
      const sealed = await file.slice(start + i * step, start + (i + 1) * step).arrayBuffer();
      try { parts.push(await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce(i, i === count - 1 ? 1 : 0), additionalData: head }, opened.k, sealed)); }
      catch { throw new Error("This file is damaged or incomplete: part of it doesn't open"); }
      heard((i + 1) / count);
    }
    return { info: opened.info, blob: new Blob(parts, { type: opened.info.type || "application/octet-stream" }) };
  }

  /** The password box. `tryIt(pw)` says whether it opens; it is asked again until it does or is given up. */
  function askPassword(tryIt) {
    return new Promise((resolve) => {
      const pw = pwInput("SaveX password");
      pw.autocomplete = "current-password";
      const note = h("p", { class: "muted small", style: "margin:8px 0 0" });
      let done = false;
      const go = h("button", { class: "btn", type: "button", onclick: async () => {
        if (!pw.value) return pw.focus();
        go.disabled = true; go.textContent = "Opening…";
        const r = await tryIt(pw.value).catch(() => null);
        go.disabled = false; go.textContent = "Open";
        if (!r) { note.textContent = "That password doesn't open it. Try again."; pw.select(); return; }
        done = true; m.close(); resolve(r);
      } }, "Open");
      const m = modal("Encrypted by SaveX",
        h("p", { class: "muted", style: "margin:0 0 16px" }, "Type the password you set in SaveX. The file is decrypted in this browser: the password isn't sent anywhere, "
          + "and it's remembered only until you close or reload this page."),
        field("Password", pw), note,
        h("div", { class: "modal-actions" }, go));
      m.dlg.addEventListener("close", () => { if (!done) resolve(null); });
      pw.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go.click(); } });
      pw.focus();
    });
  }

  /** The encrypted bytes of a file on the drive, with how far the fetching is. */
  async function fetchFile(path, heard) {
    const r = await fetch(fileUrl(path), { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error(`Could not get the file (${r.status})`);
    const total = Number(r.headers.get("Content-Length")) || 0;
    if (!r.body || !total) return r.blob();
    const reader = r.body.getReader(), parts = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value); got += value.length; heard(got / total);
    }
    return new Blob(parts);
  }

  function save(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: name, style: "display:none" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  /** The viewer: what is in an encrypted file, shown as it would be if it weren't, with a download of the original.
   *  `src` is a drive item ({path, name, size}) or a File from this computer (opened here, nothing is uploaded). */
  async function view(src) {
    const local = src instanceof Blob;
    const shownName = src.name.replace(/\.sxe$/i, "");
    const status = h("div", { class: "cin-note" }, h("b", {}, "Opening…"));
    const stage = h("div", { class: "lb-stage", onclick: (e) => { if (e.target === e.currentTarget) close(); } }, status);
    const title = h("div", { class: "lb-title" }, h("div", { class: "t" }, shownName), h("div", { class: "s" }, local ? "From this computer · encrypted by SaveX" : `Encrypted by SaveX · ${parent(src.path)}`));
    let url = null, opened = null;
    const dl = h("button", { class: "icon-btn", title: "Download decrypted", "aria-label": "Download decrypted", disabled: true,
                             onclick: () => opened && save(opened.blob, opened.info.name || shownName) }, icon("download"));
    const close = () => { document.removeEventListener("keydown", keys, true); for (const m of stage.querySelectorAll("video, audio")) m.pause(); box.remove(); if (url) URL.revokeObjectURL(url); };
    const keys = (e) => { if (e.key === "Escape" && !document.querySelector("dialog[open]")) { e.preventDefault(); e.stopPropagation(); close(); } };
    const box = h("div", { class: "lightbox", role: "dialog", "aria-label": "Encrypted file: " + shownName },
      h("div", { class: "lb-top" }, title, dl, h("button", { class: "icon-btn", title: "Close (Esc)", "aria-label": "Close", onclick: close }, icon("close"))), stage);
    document.addEventListener("keydown", keys, true);
    window.addEventListener("hashchange", close, { once: true });
    document.body.append(box);
    const say = (text) => status.replaceChildren(h("b", {}, text));
    try {
      const file = local ? src : await fetchFile(src.path, (f) => say(`Getting it… ${Math.round(f * 100)}%`));
      say("Waiting for the password…");
      opened = await decrypt(file, (f) => say(`Decrypting… ${Math.round(f * 100)}%`));
      if (!opened) return close();
    } catch (e) {
      status.replaceChildren(h("b", {}, "Couldn't open it"), h("div", {}, e.message));
      return;
    }
    const name = opened.info.name || shownName, k = kind(name);
    title.firstChild.textContent = name;
    dl.disabled = false;
    url = URL.createObjectURL(opened.blob);
    const media = k === "image" ? h("img", { src: url, alt: name })
      : k === "video" ? h("video", { src: url, controls: true, autoplay: true, playsinline: true })
      : k === "audio" ? h("audio", { src: url, controls: true, autoplay: true })
      : k === "pdf" ? h("iframe", { src: url, title: name, style: "width:min(1000px,96vw);height:84vh;border:0;background:#fff" })
      : null;
    if (media) {
      media.addEventListener("error", () => stage.replaceChildren(h("div", { class: "cin-note" }, h("b", {}, "This browser can't show this file"),
        h("button", { class: "btn", onclick: () => save(opened.blob, name) }, `Download it decrypted (${size(opened.blob.size)})`))));
      stage.replaceChildren(media);
    } else if (k === "text" && opened.blob.size < 2 * 2 ** 20) {
      stage.replaceChildren(h("pre", { class: "sxe-text" }, await opened.blob.text()));
    } else {
      stage.replaceChildren(h("div", { class: "cin-note" }, h("b", {}, name), h("div", {}, "No preview for this kind of file"),
        h("button", { class: "btn", onclick: () => save(opened.blob, name) }, `Download it decrypted (${size(opened.blob.size)})`)));
    }
  }

  /** A file on the drive, saved to this computer decrypted. */
  async function downloadFile(it) {
    try {
      toast(`Getting ${it.name}…`);
      const opened = await decrypt(await fetchFile(it.path, () => {}));
      if (!opened) return;
      save(opened.blob, opened.info.name || it.name.replace(/\.sxe$/i, ""));
    } catch (e) { toast(e.message); }
  }

  /** Every encrypted file in a folder on the drive and the folders in it, saved decrypted into a folder chosen on
   *  this computer, in the same folders. (Chrome and Edge: other browsers can't write into a folder.) */
  async function downloadFolder(it) {
    if (!window.showDirectoryPicker) return toast("Saving a whole folder decrypted needs Chrome or Edge on a computer");
    let out;
    try { out = await window.showDirectoryPicker({ mode: "readwrite", id: "sxe-decrypt" }); } catch { return; }
    const found = [];
    toast("Looking for encrypted files…");
    const walk = async (path, rel) => {
      for (const c of (await api("/api/list" + q({ path }))).items || []) {
        if (c.dir) await walk(c.path, rel.concat(c.name));
        else if (/\.sxe$/i.test(c.name)) found.push({ it: c, rel });
      }
    };
    try { await walk(it.path, [it.name]); } catch (e) { return toast(e.message); }
    if (!found.length) return toast(`No encrypted files in ${it.name}`);
    let done = 0, failed = 0;
    for (const f of found) {
      toast(`Decrypting ${done + failed + 1} of ${found.length}: ${f.it.name}`);
      try {
        const opened = await decrypt(await fetchFile(f.it.path, () => {}));
        if (!opened) { toast("Stopped: no password was given"); return; }
        let dir = out;
        for (const part of f.rel) dir = await dir.getDirectoryHandle(part, { create: true });
        const w = await (await dir.getFileHandle(opened.info.name || f.it.name.replace(/\.sxe$/i, ""), { create: true })).createWritable();
        await w.write(opened.blob); await w.close();
        done++;
      } catch (e) { failed++; console.warn("Decrypting", f.it.path, e); }
    }
    toast(`Saved ${plural(done, "file")} decrypted into ${out.name}` + (failed ? `; ${plural(failed, "file")} couldn't be opened` : ""));
  }

  /** An encrypted file picked on this computer, opened here (nothing is uploaded). */
  function openLocal() {
    const input = h("input", { type: "file", accept: ".sxe", style: "display:none", onchange: () => { if (input.files[0]) view(input.files[0]); input.remove(); } });
    document.body.append(input);
    input.click();
  }

  window.SXE = { EXT, is: (name) => /\.sxe$/i.test(name || ""), view, downloadFile, downloadFolder, openLocal, forget: () => keys.clear(), known: () => keys.size };
})();
