"use strict";

/* DiscordDrive web dashboard. No framework: a small element helper, hash routes, fetch. */

const $ = (sel) => document.querySelector(sel);
const main = $("#main");
let status = null;
let route = { name: "", args: [] };

// ------------------------------------------------------------------ helpers
function h(tag, attrs, ...children) {
  const el = tag === "svg" ? document.createElementNS("http://www.w3.org/2000/svg", "svg") : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style") el.style.cssText = v;      // CSSOM, which the page's CSP allows
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;        // only ever used with constant SVG markup
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const ICONS = {
  folder: '<path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.6 1.8H16a1.5 1.5 0 0 1 1.5 1.5v7.7A1.5 1.5 0 0 1 16 16.5H4a1.5 1.5 0 0 1-1.5-1.5z"/>',
  file: '<path d="M5 2.5h6.5L15 6v11.5H5z"/><path d="M11.5 2.5V6H15"/>',
  image: '<rect x="3" y="3.5" width="14" height="13" rx="1.5"/><circle cx="7.5" cy="8" r="1.4"/><path d="m3.5 14.5 4-4 3 3 2-2 4 3.5"/>',
  video: '<rect x="2.5" y="4.5" width="11" height="11" rx="1.5"/><path d="m13.5 8.5 4-2.2v7.4l-4-2.2"/>',
  audio: '<path d="M8 14.5V4.5l8-1.5v10"/><circle cx="6" cy="14.5" r="2"/><circle cx="14" cy="13" r="2"/>',
  text: '<path d="M5 2.5h6.5L15 6v11.5H5z"/><path d="M7.5 9.5h5M7.5 12h5M7.5 14.5h3"/>',
  archive: '<path d="M5 2.5h10v15H5z"/><path d="M10 2.5v2M10 6.5v2M10 10.5v2"/>',
  more: '<circle cx="5" cy="10" r=".9"/><circle cx="10" cy="10" r=".9"/><circle cx="15" cy="10" r=".9"/>',
  open: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.2"/>',
  enter: '<path d="M8 4.5h6.5a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H8"/><path d="M3.5 10H12M9 7l3 3-3 3"/>',
  download: '<path d="M10 3.5v9M6.5 9 10 12.5 13.5 9M4 16.5h12"/>',
  upload: '<path d="M10 13V4M6.5 7.5 10 4l3.5 3.5M4 16.5h12"/>',
  link: '<path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.4-2.4a3 3 0 0 0-4.2-4.2l-.9.9"/><path d="M11.5 8.5a3 3 0 0 0-4.2 0l-2.4 2.4a3 3 0 0 0 4.2 4.2l.9-.9"/>',
  offline: '<path d="M6 15.5h8.5a3.5 3.5 0 0 0 .4-7 5 5 0 0 0-9.7 1.3A2.9 2.9 0 0 0 6 15.5z"/><path d="m8 11.5 1.6 1.6 3-3"/>',
  rename: '<path d="M4 16h3.2l8.4-8.4a2.2 2.2 0 0 0-3.2-3.2L4 12.8z"/><path d="m11.4 5.4 3.2 3.2"/>',
  move: '<path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.6 1.8H16a1.5 1.5 0 0 1 1.5 1.5v7.7A1.5 1.5 0 0 1 16 16.5H4a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M7.5 11h5M10.5 9l2 2-2 2"/>',
  copy: '<rect x="6.5" y="6.5" width="9.5" height="9.5" rx="1.5"/><path d="M13.5 6.5V5a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 5v7A1.5 1.5 0 0 0 5 13.5h1.5"/>',
  history: '<path d="M3.5 10a6.5 6.5 0 1 0 1.9-4.6"/><path d="M3.5 4v3.5H7M10 6.5V10l2.5 1.5"/>',
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v4.5M10 6.6v.1"/>',
  trash: '<path d="M4 6h12M8 6V4.5h4V6M5.5 6l.7 9.6a1 1 0 0 0 1 .9h5.6a1 1 0 0 0 1-.9L14.5 6"/>',
  restore: '<path d="M3.5 10a6.5 6.5 0 1 0 1.9-4.6"/><path d="M3.5 4v3.5H7"/>',
  newfolder: '<path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.6 1.8H16a1.5 1.5 0 0 1 1.5 1.5v7.7A1.5 1.5 0 0 1 16 16.5H4a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M10 8.5v5M7.5 11h5"/>',
  refresh: '<path d="M16 10a6 6 0 1 1-1.8-4.3"/><path d="M16.5 3.5v3.5H13"/>',
  check: '<path d="m4.5 10.5 3.5 3.5 7.5-8"/>',
  user: '<circle cx="10" cy="7" r="3.2"/><path d="M3.8 16.5a6.2 6.2 0 0 1 12.4 0"/>',
  signout: '<path d="M12 4.5H15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3"/><path d="M12.5 10H4M7 7l-3 3 3 3"/>',
  close: '<path d="m5 5 10 10M15 5 5 15"/>',
  settings: '<circle cx="10" cy="10" r="2.5"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>',
  note: '<path d="M5 2.5h10a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z"/><path d="M7 7h6M7 10h6M7 13h3.5"/>',
  lock: '<rect x="4.5" y="9" width="11" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  plus: '<path d="M10 4.5v11M4.5 10h11"/>',
  back: '<path d="M12 4.5 6.5 10l5.5 5.5"/>',
  save: '<path d="M4.5 3.5h9l2 2v11h-11z"/><path d="M7 3.5v4h6v-4M7 16.5v-5h6v5"/>',
  contacts: '<circle cx="10" cy="7.5" r="3"/><path d="M4.5 16.5a5.5 5.5 0 0 1 11 0"/>',
  phone: '<path d="M6.5 3.5h-2a1 1 0 0 0-1 1.1c.5 6.5 5.4 11.4 11.9 11.9a1 1 0 0 0 1.1-1v-2l-3-1.5-1.5 1.5a8 8 0 0 1-4.5-4.5L9 7.5z"/>',
  mail: '<rect x="3" y="4.5" width="14" height="11" rx="1.5"/><path d="m3.5 5.5 6.5 5 6.5-5"/>',
  message: '<path d="M4 4.5h12a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1H8l-4 3V5.5a1 1 0 0 1 1-1z"/>',
  pin: '<path d="M10 17.5s5.5-5.2 5.5-9.5a5.5 5.5 0 0 0-11 0c0 4.3 5.5 9.5 5.5 9.5z"/><circle cx="10" cy="8" r="2"/>',
  cake: '<path d="M4 16.5h12v-6H4zM4 13c2 1 4-1 6 0s4-1 6 0M10 10.5V7.5M10 5.5v-.5"/>',
  globe: '<circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2.2 3 4.5 3 7s-1 4.8-3 7c-2-2.2-3-4.5-3-7s1-4.8 3-7z"/>',
  minus: '<path d="M5 10h10"/>',
  play: '<path d="M7 4.5v11l9-5.5z"/>',
  bookmark: '<path d="M5.5 3.5h9a1 1 0 0 1 1 1v12.5l-5.500-3.500-5.500 3.500V4.500a1 1 0 0 1 1-1z"/>',
  star: '<path d="m10 3 2.1 4.4 4.8.6-3.5 3.3.9 4.8L10 13.8 5.7 16.1l.9-4.8L3.1 8l4.8-.6z"/>',
  grid: '<rect x="3.5" y="3.5" width="5.5" height="5.5" rx="1"/><rect x="11" y="3.5" width="5.5" height="5.5" rx="1"/><rect x="3.5" y="11" width="5.5" height="5.5" rx="1"/><rect x="11" y="11" width="5.5" height="5.5" rx="1"/>',
  list: '<path d="M4 5.5h12M4 10h12M4 14.5h12"/>',
  gallery: '<rect x="3" y="3.5" width="14" height="13" rx="1.5"/><circle cx="7.5" cy="8" r="1.3"/><path d="m3.5 14 4-3.5 3 2.5 2.5-2 3.5 3"/>',
};
const icon = (name, cls = "") => h("svg", { viewBox: "0 0 20 20", class: `ico ${cls}`, "aria-hidden": "true", html: ICONS[name] });

const EXT = {
  image: "jpg jpeg png gif webp avif bmp svg heic",
  video: "mp4 m4v webm mov mkv avi",
  audio: "mp3 m4a aac flac wav ogg opus",
  text: "txt md log csv json xml yml yaml ini conf cfg py js ts css html sh bat ps1 c h cpp rs go java toml srt",
  archive: "zip rar 7z tar gz bz2 xz zst iso",
  pdf: "pdf",
};
function kind(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  for (const [k, list] of Object.entries(EXT)) if (list.split(" ").includes(ext)) return k;
  return "file";
}

function size(n) {
  n = Number(n || 0);
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return `${n >= 100 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}
const fmtDay = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const fmtYear = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
const fmtTime = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtFull = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
function when(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime.format(d);
  return d.getFullYear() === now.getFullYear() ? fmtDay.format(d) : fmtYear.format(d);
}
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 1.5) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
function speed(bps) { return bps > 0 ? size(bps) + "/s" : ""; }
function eta(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.max(1, Math.round(sec));
  if (sec < 60) return `${sec} s left`;
  if (sec < 3600) return `${Math.round(sec / 60)} min left`;
  return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min left`;
}
const plural = (n, one, many) => `${Number(n).toLocaleString()} ${n === 1 ? one : many || one + "s"}`;

function join(dir, name) { return (dir === "/" ? "" : dir) + "/" + name; }
function parent(path) { const i = path.lastIndexOf("/"); return i <= 0 ? "/" : path.slice(0, i); }
function base(path) { return path.slice(path.lastIndexOf("/") + 1); }
function enc(path) { return path.split("/").map(encodeURIComponent).join("/"); }

// ------------------------------------------------------------------ api
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-DD": "1" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401) { location.reload(); throw new Error("Signed out"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
const q = (params) => "?" + new URLSearchParams(params).toString();
const fileUrl = (path, dl) => "/api/file" + q(dl ? { path, dl: 1 } : { path });

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
}
async function attempt(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; }
  catch (e) { toast(e.message); return null; }
}

// ------------------------------------------------------------------ dialog
function ask({ title, text = "", value = null, ok = "OK", danger = false }) {
  const dlg = $("#dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-text").textContent = text;
  const input = $("#dialog-input");
  input.hidden = value === null;
  input.value = value || "";
  const okBtn = $("#dialog-ok");
  okBtn.textContent = ok;
  okBtn.className = danger ? "btn danger" : "btn";
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); dlg.close("ok"); } };
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === "ok" ? (value === null ? true : input.value.trim()) : null);
    dlg.returnValue = "";
    dlg.showModal();
    if (value !== null) {
      input.focus();
      const dot = input.value.lastIndexOf(".");
      input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
    }
  });
}

// ------------------------------------------------------------------ context menu
// Right-click (desktop) or long press (touch) opens it: a small menu at the pointer on desktop,
// a sheet from the bottom of the screen on phones. Items: {label, icon, run, danger, checked, disabled}
// or "-" for a divider.
let ctxEl = null;
let ctxFor = null;           // the row whose menu is open (highlighted meanwhile)
let lastTouch = 0;
const sheetMode = () => matchMedia("(hover: none), (max-width: 760px)").matches;

function closeMenu() {
  if (ctxFor) { ctxFor.classList.remove("menu-on"); ctxFor = null; }
  if (!ctxEl) return;
  ctxEl.remove();
  $("#ctx-scrim")?.remove();
  ctxEl = null;
}

function openMenu(items, x, y, title, sub, anchor) {
  closeMenu();
  if (anchor && anchor.classList.contains("row")) { ctxFor = anchor; anchor.classList.add("menu-on"); }
  const asSheet = sheetMode();
  const buttons = [];
  const list = items.filter(Boolean).filter((it, i, all) => it !== "-" || (i > 0 && all[i - 1] !== "-" && i < all.length - 1));
  const el = h("div", { class: "ctx" + (asSheet ? " as-sheet" : ""), role: "menu", "aria-label": title || "Actions" },
    asSheet && title ? h("div", { class: "ctx-title" }, h("div", { class: "t" }, title), sub && h("div", { class: "s" }, sub)) : null,
    list.map((it) => {
      if (it === "-") return h("div", { class: "sep", role: "separator" });
      const b = h("button", { role: it.checked != null ? "menuitemcheckbox" : "menuitem", class: it.danger ? "danger" : "",
                              disabled: it.disabled, "aria-checked": it.checked != null ? String(!!it.checked) : null,
                              onclick: () => { closeMenu(); if (it.run) it.run(); } },
        icon(it.icon || "more"), h("span", { class: "l" }, it.label),
        it.checked != null ? h("span", { class: "on" + (it.checked ? " yes" : "") }, it.checked ? icon("check") : null) : null);
      buttons.push(b);
      return b;
    }));
  const scrim = h("div", { id: "ctx-scrim", class: "ctx-scrim" + (asSheet ? " dim" : ""), onclick: closeMenu,
                           oncontextmenu: (e) => { e.preventDefault(); closeMenu(); } });
  document.body.append(scrim, el);
  ctxEl = el;
  if (!asSheet) {
    const r = el.getBoundingClientRect();
    el.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px";
    el.style.top = Math.max(8, y + r.height > innerHeight - 8 ? y - r.height : y) + "px";
  }
  const usable = buttons.filter((b) => !b.disabled);
  if (!asSheet && usable.length) usable[0].focus({ preventScroll: true });
  el.addEventListener("keydown", (e) => {
    const i = usable.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); usable[(i + 1) % usable.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); usable[(i - 1 + usable.length) % usable.length].focus(); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeMenu(); }
  });
}
window.addEventListener("resize", closeMenu);
main.addEventListener("scroll", closeMenu, { passive: true });

/** Right-click and long press on `el` open the menu that `items()` returns. */
function bindMenu(el, items, title, sub, skip) {
  el.addEventListener("contextmenu", (e) => {
    if (skip && skip(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (Date.now() - lastTouch < 1200) return;          // the long-press timer already opened it
    openMenu(items(), e.clientX, e.clientY, title, sub, el);
  });
  let timer = null, sx = 0, sy = 0;
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" || (skip && skip(e))) return;
    sx = e.clientX; sy = e.clientY;
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      lastTouch = Date.now();
      el.dataset.longPress = "1";
      if (navigator.vibrate) navigator.vibrate(8);
      openMenu(items(), sx, sy, title, sub, el);
    }, 480);
  });
  const cancel = () => { clearTimeout(timer); timer = null; };
  el.addEventListener("pointermove", (e) => { if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) cancel(); });
  el.addEventListener("pointerup", cancel);
  el.addEventListener("pointercancel", cancel);
  el.addEventListener("click", (e) => {
    if (el.dataset.longPress) { e.preventDefault(); e.stopImmediatePropagation(); delete el.dataset.longPress; }
  }, true);
}

function menuButton(items, title, sub) {
  return h("button", { class: "icon-btn more", "aria-label": `Actions for ${title}`, "aria-haspopup": "menu", title: "Actions",
                       onclick: (e) => {
                         e.preventDefault(); e.stopPropagation();
                         const r = e.currentTarget.getBoundingClientRect();
                         openMenu(items(), r.right - 220, r.bottom + 4, title, sub, e.currentTarget.closest(".row"));
                       } }, icon("more"));
}

function itemMenu(it) {
  return [
    it.dir ? { label: "Open", icon: "enter", run: () => go("#/files" + enc(it.path)) }
           : { label: "Preview", icon: "open", run: () => openItem(it) },
    it.dir && { label: "Open as gallery", icon: "gallery", run: () => go("#/gallery" + enc(it.path)) },
    !it.dir && route.name === "gallery" && { label: "Show in Files", icon: "enter", run: () => go("#/files" + enc(parent(it.path))) },
    it.dir ? { label: "Download as ZIP", icon: "download", run: () => download("/api/zip" + q({ path: it.path }), it.name + ".zip") }
           : { label: "Download", icon: "download", run: () => download(fileUrl(it.path, true), it.name) },
    { label: "Share…", icon: "link", run: () => shareDialog(it) },
    it.dir && { label: "Request files…", icon: "upload", run: () => requestDialog(it) },
    !it.dir && kind(it.name) === "text" && { label: "Edit", icon: "rename", run: () => editText(it) },
    "-",
    { label: it.starred ? "Remove the star" : "Star", icon: "star", run: () => setStar(it, !it.starred) },
    { label: "Available offline", icon: "offline", checked: !!it.pinned, run: () => setPinned(it, !it.pinned) },
    it.locked ? { label: "Lock again now", icon: "lock", run: lockAllNow }
              : { label: "Lock with a password…", icon: "lock", run: () => lockDialog(it) },
    it.locked && { label: "Remove the lock…", icon: "lock", run: () => removeLockDialog(it) },
    "-",
    { label: "Rename…", icon: "rename", run: () => rename(it) },
    { label: "Move to…", icon: "move", run: () => moveTo(it) },
    !it.dir && { label: "Duplicate", icon: "copy", run: () => duplicate(it) },
    !it.dir && { label: "Earlier versions", icon: "history", run: () => openItem(it, "versions") },
    { label: "Details", icon: "info", run: () => openItem(it) },
    { label: "Check for problems", icon: "check", run: () => go("#/check?" + new URLSearchParams({ path: it.path })) },
    route.name === "files" && { label: sel.has(it.path) ? "Deselect" : "Select", icon: "check", run: () => toggleSel(it.path) },
    "-",
    { label: "Delete", icon: "trash", danger: true, run: () => remove(it) },
  ];
}

function backgroundMenu() {
  return [
    { label: "New folder…", icon: "newfolder", run: newFolder },
    { label: "Upload files…", icon: "upload", run: () => $("#pick").click() },
    { label: "Upload a folder…", icon: "upload", run: () => $("#pick-dir").click() },
    { label: "Save from a link…", icon: "download", run: () => saveFromLink(currentDir) },
    currentDir !== "/" && { label: "Request files into this folder…", icon: "link", run: () => requestDialog({ path: currentDir, name: base(currentDir), dir: true }) },
    currentDir !== "/" && { label: "Download this folder as ZIP", icon: "download",
                            run: () => download("/api/zip" + q({ path: currentDir }), base(currentDir) + ".zip") },
    { label: "Check this folder for problems", icon: "check", run: () => go("#/check?" + new URLSearchParams({ path: currentDir })) },
    "-",
    { label: "Refresh", icon: "refresh", run: render },
  ];
}

function download(url, name) {
  const a = h("a", { href: url, download: name, style: "display:none" });
  document.body.append(a);
  a.click();
  a.remove();
}

// ------------------------------------------------------------------ status
async function refreshStatus() {
  try { status = await api("/api/status"); }
  catch { status = null; }
  const dot = $("#sync-dot"), text = $("#sync-text");
  if (!status) { dot.className = "dot warn"; text.textContent = "Not reachable"; return; }
  const st = status.stats;
  const busy = status.uploads.length + status.queued + st.unsynced;
  if (busy) { dot.className = "dot busy"; text.textContent = `Uploading ${st.unsynced || busy}`; }
  else if (st.outbox) { dot.className = "dot busy"; text.textContent = "Syncing"; }
  else { dot.className = "dot ok"; text.textContent = "Up to date"; }
  $("#usage").textContent = `${plural(st.files, "file")} · ${size(st.bytes)}`;
  showDiscordUploads(status.uploads);
  showFetches(status.fetches);
  if (note && route.name === "notes") noteUploadState();
  const lk = $("#locks");
  lk.hidden = !(status.locks && status.locks.count);
  lk.classList.toggle("open", !!(status.locks && status.locks.open));
  lk.title = status.locks && status.locks.open ? "Locked folders are open in this browser" : "Locked folders";
  const acct = $("#account");
  if (acct && status.user) { acct.textContent = status.user.slice(0, 1).toUpperCase(); acct.title = `Signed in as ${status.user}`; }
  if (route.name === "health" && !$("#sheet").classList.contains("open")) {
    keepScroll = true;
    try { renderHealth(true); } finally { keepScroll = false; }
  }
}

// ------------------------------------------------------------------ menu
// Its sections fold, and the whole menu shrinks to a strip of icons, to leave more room for the page.
const navState = (() => { try { return JSON.parse(localStorage.getItem("dd-nav") || "{}") || {}; } catch { return {}; } })();
function applyNav(save) {
  const shut = navState.shut || {};
  document.querySelector(".app").classList.toggle("rail", !!navState.rail);
  const t = $("#nav-toggle");
  t.setAttribute("aria-pressed", String(!!navState.rail));
  t.title = navState.rail ? "Show the menu" : "Shrink the menu to icons";
  for (const head of document.querySelectorAll(".nav-head")) {
    head.setAttribute("aria-expanded", String(!shut[head.dataset.group]));
    head.nextElementSibling.classList.toggle("shut", !!shut[head.dataset.group]);
  }
  if (save) { try { localStorage.setItem("dd-nav", JSON.stringify(navState)); } catch { /* private mode */ } }
}
$("#nav-toggle").addEventListener("click", () => { navState.rail = !navState.rail; applyNav(true); });
for (const head of document.querySelectorAll(".nav-head")) {
  head.addEventListener("click", () => {
    navState.shut = { ...(navState.shut || {}), [head.dataset.group]: head.getAttribute("aria-expanded") === "true" };
    applyNav(true);
  });
}
for (const a of document.querySelectorAll("#nav a")) a.title = a.textContent.trim();
applyNav(false);

// ------------------------------------------------------------------ routing
function go(hash) { if (location.hash !== hash) location.hash = hash; else render(); }
function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [name, ...rest] = raw.split("/");
  const qs = raw.includes("?") ? new URLSearchParams(raw.slice(raw.indexOf("?") + 1)) : new URLSearchParams();
  const args = rest.join("/").split("?")[0];
  return { name: (name || "home").split("?")[0], path: "/" + decodeURIComponent(args).replace(/^\/+|\/+$/g, ""), qs, rest };
}
function render() {
  route = parseRoute();
  for (const a of document.querySelectorAll("#nav a")) {
    const n = a.dataset.nav;
    a.classList.toggle("on", n === route.name || (n === "snapshots" && route.name === "snapshot"));
  }
  closeSheet();
  leaveNotes();
  leaveMessages();
  if (route.name !== "files" || route.path !== currentDir) sel.clear();
  const views = { files: renderFiles, search: renderSearch, deleted: renderDeleted, snapshots: renderSnapshots,
                  snapshot: renderSnapshot, health: renderHealth, notes: renderNotes, shared: renderShared,
                  settings: renderSettings, log: renderLog, check: renderCheck, contacts: renderContacts, sync: renderSync,
                  gallery: renderGallery, home: renderHome, starred: renderStarred, recent: renderRecent,
                  history: renderHistory, storage: renderStorage, bookmarks: renderBookmarks, messages: renderMessages };
  (views[route.name] || renderFiles)();
}
window.addEventListener("hashchange", render);

let keepScroll = false;
function page(...children) {
  main.replaceChildren(h("div", { class: "page" }, ...children));
  if (!keepScroll) main.scrollTop = 0;
}

function crumbs(path, root, hrefFor) {
  const parts = path.split("/").filter(Boolean);
  const out = [h(parts.length ? "a" : "span", parts.length ? { href: hrefFor("/") } : {}, root)];
  let cur = "";
  parts.forEach((p, i) => {
    cur += "/" + p;
    out.push(h("span", { class: "sep" }, "/"));
    out.push(i === parts.length - 1 ? h("span", {}, p) : h("a", { href: hrefFor(cur) }, p));
  });
  return h("nav", { class: "crumbs", "aria-label": "Folder" }, out);
}

// ------------------------------------------------------------------ files
let currentDir = "/";

function itemRow(it, opts = {}) {
  const k = it.dir ? "folder" : kind(it.name);
  const tags = [];
  if (it.state && it.state !== "synced") tags.push(h("span", { class: "tag busy" }, it.state === "error" ? "Retrying" : "Uploading"));
  if (it.pinned) tags.push(h("span", { class: "tag" }, "Offline"));
  if (it.starred) tags.push(h("span", { class: "star", title: "Starred" }, icon("star")));
  if (it.locked) tags.push(h("span", { class: "tag", title: "Has a password lock (open in this browser)" }, icon("lock", "tag-ico"), "Locked"));
  const label = h("span", { class: "label" }, it.name);
  const name = h("span", { class: "name" }, icon(k === "pdf" ? "text" : k, it.dir ? "folder" : ""),
                 opts.where ? h("span", { style: "min-width:0" }, label, h("div", { class: "where" }, parent(it.path))) : label,
                 ...tags);
  const sub = it.dir ? parent(it.path) : `${size(it.size)} · ${when(it.mtime)}`;
  const more = menuButton(() => itemMenu(it), it.name, sub);
  const attrs = it.dir ? { class: "row", href: "#/files" + enc(it.path) }
                       : { class: "row click", tabindex: "0", role: "button",
                           onclick: () => openItem(it), onkeydown: (e) => { if (e.key === "Enter") openItem(it); } };
  const row = h(it.dir ? "a" : "div", attrs, name,
                h("span", { class: "size", title: it.dir && it.files != null ? plural(it.files, "file") : null },
                  it.dir ? (it.size ? size(it.size) : "") : size(it.size)),
                h("span", { class: "date" }, when(it.mtime)), more);
  bindMenu(row, () => itemMenu(it), it.name, sub);
  if (route.name === "files") {
    if (gridView()) row.prepend(thumbBox(it));
    makeDraggable(row, it);
    const box = h("button", { class: "cb row-cb", role: "checkbox", "aria-checked": String(sel.has(it.path)),
                              "aria-label": `Select ${it.name}`, tabindex: "-1",
                              onclick: (e) => { e.preventDefault(); e.stopPropagation(); toggleSel(it.path); } }, icon("check"));
    row.querySelector(".name").prepend(box);
  }
  if (it.dir) dropTarget(row, it.path);
  return row;
}

const gridView = () => { try { return localStorage.getItem("dd-view") === "grid"; } catch { return false; } };

function listHead() {
  return h("div", { class: "row head" }, h("span", {}, "Name"), h("span", { class: "size" }, "Size"),
           h("span", { class: "date" }, "Modified"), h("span"));
}

async function renderFiles() {
  const path = route.path;
  currentDir = path;
  const data = await attempt(() => api("/api/list" + q({ path })));
  if (!data) {
    page(h("div", { class: "empty" }, h("b", {}, "This folder isn't there any more"), h("a", { href: "#/files/" }, "Back to the drive")));
    return;
  }
  const bar = h("div", { class: "bar" },
    crumbs(path, "Drive", (p) => "#/files" + enc(p)),
    h("div", { class: "actions" },
      h("button", { class: "icon-btn", title: gridView() ? "Show as a list" : "Show as a grid with thumbnails",
                    "aria-label": gridView() ? "Show as a list" : "Show as a grid",
                    onclick: () => { try { localStorage.setItem("dd-view", gridView() ? "list" : "grid"); } catch { /* private mode */ } renderFiles(); } },
        icon(gridView() ? "list" : "grid")),
      h("a", { class: "icon-btn", title: "Photos and videos in this folder", "aria-label": "Gallery of this folder", href: "#/gallery" + enc(path) }, icon("gallery")),
      h("button", { class: "btn ghost", onclick: newFolder }, "New folder"),
      h("button", { class: "btn", onclick: () => $("#pick").click() }, "Upload")));
  listed = data.items;
  const rows = data.items.map((it) => itemRow(it));
  const body = rows.length ? h("div", { class: "list" + (gridView() ? " grid" : "") }, listHead(), rows)
    : h("div", { class: "empty" }, h("b", {}, "Nothing here yet"), "Drop files anywhere on this page, or use Upload.");
  page(bar, selBar, body, h("div", { class: "spacer" }));
  for (const a of bar.querySelectorAll(".crumbs a")) {
    dropTarget(a, "/" + decodeURIComponent(a.getAttribute("href").replace(/^#\/files\/?/, "")).replace(/\/+$/, ""));
  }
  syncSel();
}

// ------------------------------------------------------------------ selection, drag and drop
// Ctrl/Cmd-click selects, Shift-click selects a range; drag a row (or the selection) onto a folder,
// a part of the path at the top, or "Files" to move it, onto "Deleted" to delete it. Files dragged
// in from the computer upload into the folder they are dropped on.
let listed = [];
const sel = new Set();
let anchorPath = null;
let dragging = null;
const selBar = h("div", { class: "selbar files-sel" });

function toggleSel(path) { sel.has(path) ? sel.delete(path) : sel.add(path); anchorPath = path; syncSel(); }

function syncSel() {
  for (const r of main.querySelectorAll(".row[data-path]")) {
    r.classList.toggle("sel", sel.has(r.dataset.path));
    const box = r.querySelector(".row-cb");
    if (box) box.setAttribute("aria-checked", String(sel.has(r.dataset.path)));
  }
  main.classList.toggle("selecting", sel.size > 0);
  const n = sel.size;
  selBar.hidden = !n;
  if (!n) return;
  selBar.replaceChildren(h("span", { class: "count" }, `${plural(n, "item")} selected`),
    h("div", { class: "actions" },
      h("button", { class: "btn ghost small", onclick: () => { sel.clear(); syncSel(); } }, "Clear"),
      h("button", { class: "btn ghost small", onclick: () => {
        const paths = [...sel], one = listed.find((i) => i.path === paths[0]);
        if (paths.length === 1 && one && !one.dir) download(fileUrl(one.path, true), one.name);
        else download("/api/zip" + q({ paths: JSON.stringify(paths) }), "DiscordDrive.zip");
      } }, "Download"),
      h("button", { class: "btn ghost small", onclick: () => moveMany([...sel]) }, "Move to…"),
      h("button", { class: "btn danger small", onclick: () => deleteMany([...sel]) }, "Delete")));
}

function clickSelect(e, it) {
  if (route.name !== "files") return false;
  if (e.shiftKey && anchorPath) {
    const paths = listed.map((x) => x.path);
    const [a, b] = [paths.indexOf(anchorPath), paths.indexOf(it.path)].sort((x, y) => x - y);
    if (a >= 0) paths.slice(a, b + 1).forEach((p) => sel.add(p));
    syncSel();
    return true;
  }
  if (e.ctrlKey || e.metaKey) { toggleSel(it.path); return true; }
  if (sel.size) { sel.clear(); syncSel(); }
  return false;
}

function makeDraggable(row, it) {
  row.dataset.path = it.path;
  row.addEventListener("click", (e) => {
    if (e.target.closest(".row-cb")) return;          // the checkbox toggles on its own
    if (clickSelect(e, it)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  if (sheetMode()) return;
  row.draggable = true;
  row.addEventListener("dragstart", (e) => {
    dragging = sel.has(it.path) ? [...sel] : [it.path];
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragging.join("\n"));
    const chip = h("div", { class: "drag-chip" }, dragging.length === 1 ? it.name : plural(dragging.length, "item"));
    document.body.append(chip);
    e.dataTransfer.setDragImage(chip, 12, 14);
    setTimeout(() => chip.remove(), 0);
    for (const r of main.querySelectorAll(".row[data-path]")) if (dragging.includes(r.dataset.path)) r.classList.add("dragging");
  });
  row.addEventListener("dragend", () => {
    dragging = null;
    for (const r of document.querySelectorAll(".dragging, .drop-into")) r.classList.remove("dragging", "drop-into");
  });
}

const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
function canMove(paths, dest) {
  return paths.some((p) => p !== dest && !dest.startsWith(p + "/") && parent(p) !== dest);
}

/** Dropping on `el`: move what is dragged into folder `dest` (or delete it), or upload files from the computer. */
function dropTarget(el, dest, action = "move") {
  el.addEventListener("dragover", (e) => {
    if (dragging ? (action === "move" && !canMove(dragging, dest)) : (!hasFiles(e) || action !== "move")) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = dragging ? "move" : "copy";
    el.classList.add("drop-into");
    if (!dragging) $("#drop-to").textContent = dest === "/" ? "Drive" : base(dest);
  });
  el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop-into"); });
  el.addEventListener("drop", async (e) => {
    el.classList.remove("drop-into");
    if (dragging) {
      e.preventDefault();
      e.stopPropagation();
      const paths = dragging;
      dragging = null;
      if (action === "delete") deleteMany(paths);
      else moveMany(paths, dest);
    } else if (hasFiles(e) && action === "move") {
      e.preventDefault();
      e.stopPropagation();
      dragDepth = 0;
      drop.classList.remove("on");
      uploadAll(await droppedFiles(e), dest);
    }
  });
}

async function moveMany(paths, dest) {
  if (dest === undefined) {
    dest = await pickFolder(paths.length === 1 ? `Move “${base(paths[0])}”` : `Move ${plural(paths.length, "item")}`,
                            parent(paths[0]), paths);
    if (dest == null) return;
  }
  let moved = 0;
  for (const p of paths) {
    if (p === dest || dest.startsWith(p + "/") || parent(p) === dest) continue;
    if (await attempt(() => api("/api/move", { from: p, to: join(dest, base(p)) }))) moved++;
  }
  if (moved) toast(`Moved ${moved === 1 ? base(paths[0]) : plural(moved, "item")} to ${dest === "/" ? "Drive" : base(dest)}`);
  sel.clear();
  closeSheet();
  render();
}

async function deleteMany(paths) {
  const one = paths.length === 1;
  const ok = await ask({ title: one ? `Delete “${base(paths[0])}”?` : `Delete ${plural(paths.length, "item")}?`, ok: "Delete",
                         danger: true, text: "Deleted on every device. You can recover files from Deleted for a while." });
  if (!ok) return;
  let n = 0;
  for (const p of paths) if (await attempt(() => api("/api/delete", { path: p }))) n++;
  if (n) toast(one ? "Deleted" : `${plural(n, "item")} deleted`);
  sel.clear();
  render();
}

document.addEventListener("keydown", (e) => {
  if (route.name !== "files" || e.target.closest("input, textarea, dialog")) return;
  if (e.key === "Escape" && sel.size) { sel.clear(); syncSel(); }
  else if ((e.key === "Delete" || e.key === "Backspace") && sel.size) { e.preventDefault(); deleteMany([...sel]); }
  else if (e.key.toLowerCase() === "a" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); listed.forEach((it) => sel.add(it.path)); syncSel(); }
});

// Box selection: press on empty space and drag; rows the box touches are selected
// (Ctrl/Cmd/Shift keeps what was already selected).
(function boxSelect() {
  let start = null, box = null, before = null, scrollTimer = null, last = null;
  main.addEventListener("pointerdown", (e) => {
    if (route.name !== "files" || e.button !== 0 || e.pointerType !== "mouse") return;
    if (e.target.closest(".row:not(.head), button, a, input, textarea, .selbar, .bar")) return;
    start = { x: e.clientX, y: e.clientY + main.scrollTop };
    before = e.ctrlKey || e.metaKey || e.shiftKey ? new Set(sel) : new Set();
    e.preventDefault();
  });
  function update(e) {
    last = e;
    const y = e.clientY + main.scrollTop;
    const r = { left: Math.min(start.x, e.clientX), right: Math.max(start.x, e.clientX),
                top: Math.min(start.y, y) - main.scrollTop, bottom: Math.max(start.y, y) - main.scrollTop };
    if (!box) {
      if (Math.hypot(e.clientX - start.x, y - start.y) < 6) return;
      box = h("div", { class: "marquee" });
      document.body.append(box);
    }
    Object.assign(box.style, { left: r.left + "px", top: r.top + "px", width: r.right - r.left + "px", height: r.bottom - r.top + "px" });
    sel.clear();
    before.forEach((p) => sel.add(p));
    for (const row of main.querySelectorAll(".row[data-path]")) {
      const b = row.getBoundingClientRect();
      if (b.bottom > r.top && b.top < r.bottom && b.right > r.left && b.left < r.right) sel.add(row.dataset.path);
    }
    syncSel();
  }
  window.addEventListener("pointermove", (e) => {
    if (!start) return;
    update(e);
    clearInterval(scrollTimer);
    const m = main.getBoundingClientRect();
    const dir = e.clientY < m.top + 30 ? -1 : e.clientY > m.bottom - 30 ? 1 : 0;
    if (dir && box) scrollTimer = setInterval(() => { main.scrollTop += dir * 18; if (last) update(last); }, 30);
  });
  window.addEventListener("pointerup", () => {
    clearInterval(scrollTimer);
    if (box) { box.remove(); box = null; if (sel.size) anchorPath = [...sel].pop(); }
    else if (start && !before.size && sel.size) { sel.clear(); syncSel(); }   // a plain click on empty space
    start = null;
  });
})();

// ------------------------------------------------------------------ modal + folder picker
function modal(title, ...content) {
  const dlg = h("dialog", { class: "modal" });
  const close = () => dlg.close();
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  dlg.append(h("div", { class: "modal-head" }, h("h3", {}, title),
                 h("button", { class: "icon-btn", "aria-label": "Close", onclick: close }, icon("close"))),
             h("div", { class: "modal-body" }, ...content));
  document.body.append(dlg);
  dlg.showModal();
  return { dlg, close };
}

function pickFolder(title, start, moving = [], okLabel = "Move here") {
  return new Promise((resolve) => {
    let cur = start || "/";
    let chosen = null;
    const list = h("div", { class: "picker-list" });
    const where = h("div", { class: "picker-where" });
    const here = h("button", { class: "btn", onclick: () => { chosen = cur; m.close(); } }, okLabel);
    const m = modal(title, where, list,
      h("div", { class: "modal-actions" },
        h("button", { class: "btn ghost", onclick: async () => {
          const name = await ask({ title: "New folder", value: "", ok: "Create" });
          if (name && await attempt(() => api("/api/mkdir", { path: join(cur, name) }))) load(join(cur, name));
        } }, "New folder"),
        here));
    m.dlg.addEventListener("close", () => resolve(chosen));
    async function load(path) {
      cur = path;
      const data = await api("/api/list" + q({ path })).catch(() => null);
      const parts = path.split("/").filter(Boolean);
      where.replaceChildren(...["Drive", ...parts].flatMap((name, i) => {
        const target = "/" + parts.slice(0, i).join("/");
        const el = i === parts.length ? h("span", {}, name) : h("button", { class: "link", onclick: () => load(target) }, name);
        return i ? [h("span", { class: "sep" }, "/"), el] : [el];
      }));
      const dirs = (data ? data.items : []).filter((it) => it.dir);
      list.replaceChildren(...(dirs.length ? dirs.map((d) => {
        const blocked = moving.some((p) => d.path === p || d.path.startsWith(p + "/"));
        return h("button", { class: "picker-row", disabled: blocked, onclick: () => load(d.path) },
                 icon("folder", "folder"), h("span", {}, d.name), icon("enter"));
      }) : [h("div", { class: "muted picker-empty" }, "No folders in here")]));
      here.disabled = moving.length > 0 && !canMove(moving, path);
    }
    load(cur);
  });
}
bindMenu(main, backgroundMenu, "This folder", null,
         (e) => route.name !== "files" || !!e.target.closest(".row, button, a, input"));

async function newFolder() {
  const name = await ask({ title: "New folder", value: "", ok: "Create" });
  if (!name) return;
  if (await attempt(() => api("/api/mkdir", { path: join(currentDir, name) }))) render();
}

async function renderSearch() {
  const text = route.qs.get("q") || "";
  $("#q").value = text;
  const data = await attempt(() => api("/api/search" + q({ q: text })));
  const items = data ? data.items : [];
  page(h("div", { class: "bar" }, h("div", { class: "crumbs" }, h("span", {}, `Results for “${text}”`))),
       items.length ? h("div", { class: "list" }, listHead(), items.map((it) => itemRow(it, { where: true })))
                    : h("div", { class: "empty" }, h("b", {}, "No matches"), "Try part of the name."));
}

let searchTimer;
$("#q").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const v = e.target.value.trim();
  searchTimer = setTimeout(() => {
    if (v.length >= 2) go("#/search?" + new URLSearchParams({ q: v }));
    else if (!v && route.name === "search") go("#/home");
  }, 220);
});

// ------------------------------------------------------------------ item sheet
function closeSheet() {
  $("#sheet").classList.remove("open");
  $("#sheet").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
  for (const m of $("#sheet-body").querySelectorAll("video, audio")) m.pause();
}
$("#sheet-close").onclick = closeSheet;
$("#scrim").onclick = closeSheet;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#dialog").open) closeSheet(); });

function preview(it) {
  const url = fileUrl(it.path);
  const k = kind(it.name);
  if (it.dir) return h("div", { class: "preview" }, icon("folder", "big folder"));
  if (k === "image") return h("div", { class: "preview" }, h("img", { src: url, alt: it.name }));
  if (k === "video") return h("div", { class: "preview" }, h("video", { src: url, controls: true, preload: "metadata", playsinline: true }));
  if (k === "audio") return h("div", { class: "preview" }, h("audio", { src: url, controls: true, preload: "metadata" }));
  if (k === "pdf" && it.size < 64 * 2 ** 20) return h("div", { class: "preview" }, h("iframe", { src: url, title: it.name }));
  if (k === "text" && it.size < 2 * 2 ** 20) {
    const pre = h("pre", {}, "Loading…");
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Range: "bytes=0-262143" } })
      .then((r) => r.text()).then((t) => { pre.textContent = t; }).catch(() => { pre.textContent = "Could not load a preview."; });
    return h("div", { class: "preview text" }, pre);
  }
  return h("div", { class: "preview" }, h("div", { class: "note" }, icon(k === "file" ? "file" : k, "big"), h("div", {}, "No preview for this kind of file")));
}

async function setPinned(it, on) {
  if (await attempt(() => api("/api/pin", { path: it.path, on }),
                    on ? "Downloading it to keep it available offline" : "No longer kept offline")) {
    it.pinned = on;
    if (route.name === "files" || route.name === "search") render();
    return true;
  }
  return false;
}

function openItem(it, focus) {
  $("#sheet-title").textContent = it.name;
  const body = $("#sheet-body");
  const meta = h("dl", { class: "meta" },
    h("dt", {}, "Location"), h("dd", {}, parent(it.path)),
    !it.dir && [h("dt", {}, "Size"), h("dd", {}, `${size(it.size)} (${Number(it.size).toLocaleString()} bytes)`)],
    h("dt", {}, "Modified"), h("dd", {}, it.mtime ? fmtFull.format(new Date(it.mtime * 1000)) : "—"),
    !it.dir && [h("dt", {}, "Stored"), h("dd", {}, it.state === "synced" ? "In Discord" : "Waiting to upload")]);
  const actions = h("div", { class: "sheet-actions" },
    !it.dir && h("a", { class: "btn", href: fileUrl(it.path, true), download: it.name }, "Download"),
    h("button", { class: "btn ghost", onclick: () => shareDialog(it) }, "Share…"),
    !it.dir && kind(it.name) === "text" && h("button", { class: "btn ghost", onclick: () => editText(it) }, "Edit"),
    h("button", { class: "btn ghost", onclick: () => setStar(it, !it.starred) }, it.starred ? "Remove the star" : "Star"),
    h("button", { class: "btn ghost", onclick: () => rename(it) }, "Rename"),
    h("button", { class: "btn danger", onclick: () => remove(it) }, "Delete"));
  const sw = h("button", { class: "switch", role: "switch", "aria-checked": String(!!it.pinned), "aria-label": "Available offline",
                           onclick: async () => {
                             const on = sw.getAttribute("aria-checked") !== "true";
                             const isOpen = $("#sheet").classList.contains("open");
                             if (await setPinned(it, on)) {
                               sw.setAttribute("aria-checked", String(on));
                               if (isOpen && !$("#sheet").classList.contains("open")) openItem(it);
                             }
                           } });
  const toggle = h("div", { class: "toggle" },
    h("div", {}, h("div", { class: "t" }, "Available offline"),
      h("div", { class: "s" }, it.dir ? "Everything in it, including new files, stays downloaded on the computer running the drive"
                                      : "Stays downloaded on the computer running the drive, so it opens instantly")),
    sw);
  const extra = h("div");
  body.replaceChildren(preview(it), meta, actions, toggle, extra);
  if (!it.dir) loadVersions(it, extra, focus === "versions");
  $("#sheet").classList.add("open");
  $("#sheet").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
}

async function loadVersions(it, into, focus) {
  const data = await api("/api/versions" + q({ path: it.path })).catch(() => null);
  if (!data || !data.versions.length) {
    if (focus) into.append(h("div", { class: "versions" }, h("h3", {}, "Earlier versions"), h("p", { class: "muted" }, "None kept for this file.")));
    return;
  }
  const reasons = { replaced: "Replaced", deleted: "Deleted", conflict: "Edited on two devices" };
  const box = h("div", { class: "versions" }, h("h3", {}, "Earlier versions"),
    data.versions.map((v) => h("div", { class: "v" },
      h("span", { class: "when" }, fmtFull.format(new Date(v.at * 1000)), " ", h("span", { class: "why" }, reasons[v.reason] || v.reason)),
      h("span", { class: "num muted" }, size(v.size)),
      h("button", { class: "btn ghost small", onclick: async () => {
        const ok = await ask({ title: "Bring back this version?", ok: "Restore",
                               text: "The current content is kept as an earlier version, so you can switch back." });
        if (ok && await attempt(() => api("/api/versions/restore", { path: it.path, n: v.n }), "Restored. It updates on every device in a few seconds.")) {
          closeSheet(); setTimeout(render, 2500);
        }
      } }, "Restore"))));
  into.append(box);
  if (focus) box.scrollIntoView({ block: "start", behavior: "smooth" });
}

const EXPIRY = [["1 hour", 1], ["1 day", 24], ["7 days", 168], ["30 days", 720], ["Never", 0]];
const isLocal = () => ["127.0.0.1", "localhost", "::1", "[::1]"].includes(location.hostname);
/** The address to give out: the one you are browsing from, unless that is this computer itself. */
const EMBEDS = ["image", "video", "audio"];
function directUrl(r) {
  if (!r.direct_path) return "";
  if (!isLocal()) return location.origin + r.direct_path;
  return r.direct || location.origin + r.direct_path;
}
function shareUrl(r) {
  if (!isLocal()) return location.origin + r.path;
  return r.url || (r.lan && r.lan[0]) || location.origin + r.path;
}

function switchEl(on, change, label) {
  const b = h("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(!!on), "aria-label": label,
                          onclick: () => { const v = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(v)); change(v); } });
  return b;
}

function field(label, control, hint) {
  return h("div", { class: "field" }, h("div", { class: "fl" }, label), control, hint && h("div", { class: "hint" }, hint));
}

function linkBox(url, note) {
  const input = h("input", { value: url, readonly: true, onclick: (e) => e.target.select() });
  return h("div", { class: "share-done" },
    h("div", { class: "share-url" }, input,
      h("button", { class: "btn small", type: "button", onclick: async () => {
        try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { input.select(); document.execCommand("copy"); toast("Link copied"); }
      } }, "Copy")),
    note && h("div", { class: "hint" }, note));
}

function shareDialog(it, existing) {
  let hours = existing ? null : 168;
  let download = existing ? existing.download : true;
  let clearPw = false;
  const seg = h("div", { class: "seg", role: "radiogroup" }, EXPIRY.map(([label, v]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v === hours),
                  onclick: (e) => { hours = v; for (const b of seg.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); } }, label)));
  const pw = h("input", { type: "password", autocomplete: "new-password",
                          placeholder: existing && existing.password ? "Unchanged" : "None" });
  const result = h("div");
  const form = h("div", { class: "share-form" },
    field("Link works for", seg, existing ? `Now: ${existing.expires ? "until " + fmtFull.format(new Date(existing.expires * 1000)) : "no expiry"}` : null),
    field("Password", pw, existing && existing.password
      ? h("button", { type: "button", class: "link", onclick: (e) => { clearPw = true; pw.value = ""; pw.placeholder = "None"; e.target.remove(); } }, "Remove the password")
      : "Optional. People need it to open the link."),
    h("div", { class: "toggle" },
      h("div", {}, h("div", { class: "t" }, "Allow downloading"),
        h("div", { class: "s" }, "Off: people can view it on the page but get no download button")),
      switchEl(download, (v) => { download = v; }, "Allow downloading")));
  const go = h("button", { class: "btn", type: "button", onclick: async () => {
    go.disabled = true;
    let r;
    if (existing) {
      const body = { id: existing.id, download };
      if (hours !== null) body.hours = hours;
      if (pw.value || clearPw) body.password = pw.value;
      r = await attempt(() => api("/api/shares/update", body), "Link updated");
      if (r) { m.close(); if (route.name === "shared") renderShared(); }
    } else {
      r = await attempt(() => api("/api/share", { path: it.path, hours, password: pw.value, download }));
      if (r) {
        const url = shareUrl(r);
        const direct = directUrl(r);
        form.remove();
        go.remove();
        const reach = isLocal() && !r.url && !(r.lan && r.lan.length)
          ? "It only opens on this computer. Set a domain name or turn on “On your network” in Settings to share it."
          : `Anyone with the link${r.password ? " and the password" : ""} can ${download ? "view and download" : "view"} ${it.dir ? "this folder" : "this file"}.`;
        result.replaceChildren(h("div", { class: "fl share-kind" }, "Page"), linkBox(url, reach),
          direct && h("div", { class: "fl share-kind" }, "Direct link"),
          direct && linkBox(direct, EMBEDS.includes(kind(it.name))
            ? "The file itself. Posted on its own in Discord (or anywhere that shows media), it appears as the picture, video or song, without a link."
            : "The file itself, for downloading or linking from elsewhere."));
        try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { /* the box is there to copy from */ }
        if (route.name === "shared") renderShared();
      }
    }
    go.disabled = false;
  } }, existing ? "Save" : "Create link");
  const m = modal(existing ? `Link to “${base(existing.item || "deleted item")}”` : `Share “${it.name}”`,
                  form, result, h("div", { class: "modal-actions" }, go));
}

function moveTo(it) { return moveMany([it.path]); }

async function duplicate(it) {
  const dot = it.name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [it.name.slice(0, dot), it.name.slice(dot)] : [it.name, ""];
  const name = await ask({ title: "Duplicate", value: `${stem} copy${ext}`, ok: "Duplicate",
                           text: "The copy reuses the pieces already in Discord, so nothing is uploaded again." });
  if (!name) return;
  if (await attempt(() => api("/api/copy", { from: it.path, to: join(parent(it.path), name) }), "Duplicated")) render();
}

async function rename(it) {
  const name = await ask({ title: "Rename", value: it.name, ok: "Rename" });
  if (!name || name === it.name) return;
  if (await attempt(() => api("/api/move", { from: it.path, to: join(parent(it.path), name) }))) { closeSheet(); render(); }
}

async function remove(it) {
  const ok = await ask({ title: `Delete “${it.name}”?`, ok: "Delete", danger: true,
                         text: it.dir ? "The folder and everything in it are deleted on every device. Files can be recovered from Deleted for a while."
                                      : "It is deleted on every device. You can recover it from Deleted for a while." });
  if (ok && await attempt(() => api("/api/delete", { path: it.path }), "Deleted")) { closeSheet(); render(); }
}

// ------------------------------------------------------------------ uploads
const tray = $("#tray");
function uploadFile(file, dir) {
  return new Promise((resolve) => {
    const bar = h("i", { style: "width:0%" });
    const pct = h("span", {}, "0%");
    const sub = h("div", { class: "sub" }, "Sending to the drive…");
    const item = h("div", { class: "item" }, h("div", { class: "n" }, h("span", {}, file.name), pct), h("div", { class: "meter" }, bar), sub);
    tray.prepend(item);
    const started = Date.now();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload" + q({ path: join(dir, file.name) }));
    xhr.setRequestHeader("X-DD", "1");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      const bps = e.loaded / Math.max(0.3, (Date.now() - started) / 1000);
      bar.style.width = p + "%";
      pct.textContent = p < 100 ? p + "%" : "Saving";
      sub.textContent = p < 100 ? `${size(e.loaded)} of ${size(e.total)} · ${speed(bps)} · ${eta((e.total - e.loaded) / bps)}`
                                : "Saving on the drive…";
    };
    xhr.onload = () => {
      const ok = xhr.status >= 200 && xhr.status < 300;
      pct.textContent = ok ? "Done" : "Failed";
      sub.textContent = ok ? "On the drive · uploading to Discord next" : "Not uploaded";
      if (!ok) { try { toast(JSON.parse(xhr.responseText).error); } catch { toast("Upload failed"); } }
      setTimeout(() => item.remove(), ok ? 1500 : 6000);
      const up = { name: file.name, path: join(dir, file.name), dir: false };
      if (ok && isMedia(up) && file.size < 2 ** 31) {
        const src = URL.createObjectURL(file);
        makeThumb(src, kind(file.name)).then((b) => b && storeThumb(up.path, b)).catch(() => null).finally(() => URL.revokeObjectURL(src));
      }
      resolve(ok);
    };
    xhr.onerror = () => { pct.textContent = "Failed"; setTimeout(() => item.remove(), 6000); resolve(false); };
    xhr.send(file);
  });
}

/** Uploads from the drive to Discord, live (one tray card per file). */
const discordCards = new Map();
function showDiscordUploads(list) {
  const seen = new Set();
  for (const u of list || []) {
    seen.add(u.path);
    let c = discordCards.get(u.path);
    if (!c) {
      c = { bar: h("i", { style: "width:0%" }), pct: h("span", {}), sub: h("div", { class: "sub" }) };
      c.el = h("div", { class: "item discord" }, h("div", { class: "n" }, h("span", { title: u.path }, base(u.path)), c.pct),
               h("div", { class: "meter" }, c.bar), c.sub);
      discordCards.set(u.path, c);
      tray.append(c.el);
    }
    c.bar.style.width = u.pct + "%";
    c.pct.textContent = Math.floor(u.pct) + "%";
    c.sub.textContent = u.stage === "spare pieces" ? "Adding spare pieces…"
      : `To Discord · ${size(u.bytes)} of ${size(u.size)}${u.speed ? " · " + speed(u.speed) : ""}${u.eta ? " · " + eta(u.eta) : ""}`;
  }
  for (const [path, c] of discordCards) {
    if (seen.has(path)) continue;
    discordCards.delete(path);
    c.bar.style.width = "100%";
    c.pct.textContent = "Done";
    c.sub.textContent = "Stored in Discord";
    setTimeout(() => c.el.remove(), 1500);
  }
}

async function uploadAll(entries, dir) {
  let n = 0;
  for (const { file, rel } of entries) {
    const folders = rel.split("/").slice(0, -1);
    let cur = dir;
    for (const f of folders) {
      cur = join(cur, f);
      await api("/api/mkdir", { path: cur }).catch(() => null);   // already exists: fine
    }
    if (await uploadFile(file, cur)) n++;
  }
  if (n) toast(`${plural(n, "file")} added. ${n === 1 ? "It uploads" : "They upload"} to Discord in the background.`);
  if (route.name === "files") render();
}

for (const id of ["#pick", "#pick-dir"]) {
  $(id).addEventListener("change", (e) => {
    const files = [...e.target.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }));
    e.target.value = "";
    uploadAll(files, currentDir);
  });
}

async function readEntries(entry, prefix, out) {
  if (entry.isFile) {
    await new Promise((res) => entry.file((file) => { out.push({ file, rel: prefix + file.name }); res(); }, res));
  } else if (entry.isDirectory) {
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((res) => reader.readEntries(res, () => res([])));
      if (!batch.length) break;
      for (const e of batch) await readEntries(e, prefix + entry.name + "/", out);
    }
  }
}

let dragDepth = 0;
const drop = $("#drop");
document.addEventListener("dragenter", (e) => {
  if (route.name !== "files" || ![...(e.dataTransfer?.types || [])].includes("Files")) return;
  dragDepth++;
  if (!e.target.closest || !e.target.closest(".drop-into")) $("#drop-to").textContent = currentDir === "/" ? "Drive" : base(currentDir);
  drop.classList.add("on");
});
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; drop.classList.remove("on"); } });
document.addEventListener("dragover", (e) => { if (route.name === "files") e.preventDefault(); });
document.addEventListener("drop", async (e) => {
  if (route.name !== "files") return;
  e.preventDefault();
  dragDepth = 0;
  drop.classList.remove("on");
  if (!hasFiles(e)) return;
  uploadAll(await droppedFiles(e), currentDir);
});

async function droppedFiles(e) {
  const out = [];
  const items = [...(e.dataTransfer.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) for (const entry of items) await readEntries(entry, "", out);
  else for (const file of e.dataTransfer.files) out.push({ file, rel: file.name });
  return out;
}

// ------------------------------------------------------------------ deleted
const picked = new Set();

async function renderDeleted() {
  const data = await attempt(() => api("/api/deleted"));
  const items = data ? data.items : [];
  for (const uid of [...picked]) if (!items.some((it) => it.uid === uid)) picked.delete(uid);
  const head = h("div", { class: "bar" }, h("h1", { style: "margin:0" }, "Deleted"));
  const lede = h("p", { class: "lede" }, "Deleted files are kept for a while (see Settings) and can be brought back on every device. ",
                 "Delete them for good to free the space now.");
  if (!items.length) {
    page(head, lede, h("div", { class: "empty" }, h("b", {}, "Nothing deleted"), "Files you delete show up here."));
    return;
  }
  const boxes = [];
  const selbar = h("div", { class: "selbar" });
  const all = checkbox(false, (on) => { items.forEach((it) => on ? picked.add(it.uid) : picked.delete(it.uid)); sync(); }, "Select all");
  function sync() {
    boxes.forEach(([it, box, row]) => { setBox(box, picked.has(it.uid)); row.classList.toggle("sel", picked.has(it.uid)); });
    setBox(all, picked.size === items.length ? true : picked.size ? "mixed" : false);
    const chosen = items.filter((it) => picked.has(it.uid));
    selbar.replaceChildren(
      h("span", { class: "count" }, chosen.length ? `${plural(chosen.length, "file")} selected · ${size(chosen.reduce((a, it) => a + it.size, 0))}`
                                                  : `${plural(items.length, "file")} · ${size(items.reduce((a, it) => a + it.size, 0))}`),
      h("div", { class: "actions" },
        chosen.length ? h("button", { class: "btn ghost small", onclick: () => { picked.clear(); sync(); } }, "Clear") : null,
        h("button", { class: "btn ghost small", disabled: !chosen.length, onclick: () => restoreMany(chosen) }, "Restore"),
        h("button", { class: "btn danger small", disabled: !chosen.length, onclick: () => purgeMany(chosen) }, "Delete forever")));
  }
  const rows = items.map((it) => {
    const box = checkbox(false, (on) => { on ? picked.add(it.uid) : picked.delete(it.uid); sync(); }, `Select ${base(it.path)}`);
    const menu = () => deletedMenu(it, sync);
    const row = h("div", { class: "row click pick", tabindex: "0",
                           onclick: (e) => { if (e.target.closest("button")) return; picked.has(it.uid) ? picked.delete(it.uid) : picked.add(it.uid); sync(); },
                           onkeydown: (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); row.click(); } } },
      h("span", { class: "name" }, box, icon(kind(it.path) === "pdf" ? "text" : kind(it.path)), h("span", { style: "min-width:0" },
        h("span", { class: "label" }, base(it.path)), h("div", { class: "where" }, parent(it.path)))),
      h("span", { class: "size" }, size(it.size)),
      h("span", { class: "date" }, when(it.at)),
      menuButton(menu, base(it.path), `Deleted ${when(it.at)}`));
    bindMenu(row, menu, base(it.path), `Deleted ${when(it.at)}`);
    boxes.push([it, box, row]);
    return row;
  });
  page(head, lede, selbar,
       h("div", { class: "list" },
         h("div", { class: "row head" }, h("span", { class: "name" }, all, h("span", {}, "Name")), h("span", { class: "size" }, "Size"),
           h("span", { class: "date" }, "Deleted"), h("span")),
         rows));
  sync();
}

function checkbox(on, change, label) {
  const b = h("button", { class: "cb", role: "checkbox", "aria-label": label, "aria-checked": String(on),
                          onclick: (e) => { e.stopPropagation(); change(b.getAttribute("aria-checked") !== "true"); } },
              icon("check"));
  return b;
}
function setBox(b, state) { b.setAttribute("aria-checked", String(state)); }

function deletedMenu(it, sync) {
  return [
    { label: "Restore", icon: "restore", run: () => restoreMany([it]) },
    { label: picked.has(it.uid) ? "Deselect" : "Select", icon: "check",
      run: () => { picked.has(it.uid) ? picked.delete(it.uid) : picked.add(it.uid); sync(); } },
    "-",
    { label: "Delete forever", icon: "trash", danger: true, run: () => purgeMany([it]) },
  ];
}

async function restoreMany(list) {
  const r = await attempt(() => api("/api/undelete", { uids: list.map((it) => it.uid) }));
  if (!r) return;
  list.forEach((it) => picked.delete(it.uid));
  toast(list.length === 1 ? `${base(list[0].path)} restored` : `${plural(r.count, "file")} restored`);
  setTimeout(() => { if (route.name === "deleted") renderDeleted(); }, 2500);
}

async function purgeMany(list) {
  const one = list.length === 1;
  const ok = await ask({ title: one ? `Delete “${base(list[0].path)}” forever?` : `Delete ${plural(list.length, "file")} forever?`,
                         ok: "Delete forever", danger: true,
                         text: `${one ? "It is" : "They are"} removed from Discord on every device and can't be recovered. ` +
                               "Snapshots taken before still contain them until those snapshots expire." });
  if (!ok) return;
  const r = await attempt(() => api("/api/purge", { uids: list.map((it) => it.uid) }));
  if (!r) return;
  list.forEach((it) => picked.delete(it.uid));
  toast(one ? "Deleted forever" : `${plural(r.count, "file")} deleted forever`);
  renderDeleted();
}

// ------------------------------------------------------------------ snapshots
async function deleteSnapshots(list, all) {
  const one = list.length === 1 && !all;
  const ok = await ask({ title: one ? `Delete the snapshot of ${fmtFull.format(new Date(list[0].at * 1000))}?` : `Delete ${all ? "all " : ""}${plural(list.length, "snapshot")}?`,
                         ok: "Delete for good", danger: true,
                         text: `${one ? "It is" : "They are"} removed on every device and can't be brought back. Your files as they are now are not touched; what only ${one ? "this snapshot" : "these snapshots"} still kept (older contents of changed and deleted files) is removed from Discord.` });
  if (!ok) return false;
  const r = await attempt(() => api("/api/snapshots/delete", all ? { all: true } : { ids: list.map((s) => s.id) }));
  if (r) toast(`${plural(r.count, "snapshot")} deleted for good`);
  return !!r;
}

async function renderSnapshots() {
  const data = await attempt(() => api("/api/snapshots"));
  const items = data ? data.items : [];
  const s = status && status.snapshots;
  const every = s && s.interval ? `A snapshot is taken every ${s.interval} hours and kept ${s.keep} days. ` : "";
  page(h("div", { class: "bar" }, h("h1", { style: "margin:0" }, "Snapshots"),
         h("div", { class: "actions" },
           items.length > 1 && h("button", { class: "btn danger", onclick: async () => { if (await deleteSnapshots(items, true)) renderSnapshots(); } }, "Delete all…"),
           h("button", { class: "btn", onclick: async (e) => {
             e.target.disabled = true;
             if (await attempt(() => api("/api/snapshots/create", { label: "Manual" }), "Snapshot taken")) renderSnapshots();
             e.target.disabled = false;
           } }, "Take snapshot now"))),
       h("p", { class: "lede" }, `${every}Each one is the whole drive exactly as it was, so you can bring back a folder as it was on a given day.`),
       items.length ? h("div", { class: "list rows-snap" }, items.map((sn) => {
         const title = fmtFull.format(new Date(sn.at * 1000));
         const menu = () => [
           { label: "Open", icon: "enter", run: () => go(`#/snapshot/${sn.id}/`) },
           "-",
           { label: "Delete for good…", icon: "trash", danger: true, run: async () => { if (await deleteSnapshots([sn])) renderSnapshots(); } },
         ];
         const row = h("a", { class: "row", href: `#/snapshot/${sn.id}/` },
           h("span", { class: "name" }, icon("folder", "folder"), h("span", { class: "label" }, title),
             sn.label && h("span", { class: "tag" }, sn.label)),
           h("span", { class: "size" }, size(sn.bytes)),
           h("span", { class: "date" }, plural(sn.files, "file")),
           h("span", { class: "date" }, sn.keep_until ? `until ${when(sn.keep_until)}` : "kept"),
           menuButton(menu, title, sn.label || "Snapshot"));
         bindMenu(row, menu, title, sn.label || "Snapshot");
         return row;
       }))
       : h("div", { class: "empty" }, h("b", {}, "No snapshots yet"), "Take one now, or wait for the first automatic one."));
}

async function renderSnapshot() {
  const [id, ...rest] = route.rest;
  const path = "/" + rest.map(decodeURIComponent).join("/").replace(/^\/+|\/+$/g, "");
  const [list, snaps] = await Promise.all([api("/api/snapshot" + q({ id, path })).catch(() => null),
                                           api("/api/snapshots").catch(() => null)]);
  const sn = snaps && snaps.items.find((x) => x.id === id);
  if (!list || !sn) { page(h("div", { class: "empty" }, h("b", {}, "Snapshot not found"), h("a", { href: "#/snapshots" }, "All snapshots"))); return; }
  const title = fmtFull.format(new Date(sn.at * 1000));
  async function restoreFrom(what) {
    const ok = await ask({ title: `Restore ${what === "/" ? "the whole drive" : "“" + base(what) + "”"}?`, ok: "Restore",
                           text: "It is copied into a new folder named after the snapshot, so nothing you have now is changed." });
    if (!ok) return;
    const r = await attempt(() => api("/api/snapshots/restore", { id, path: what }));
    if (r) { toast(`${plural(r.files, "file")} restored into ${r.folder}`); setTimeout(() => go("#/files" + enc(r.folder)), 2500); }
  }
  const restore = h("button", { class: "btn", onclick: () => restoreFrom(path) }, path === "/" ? "Restore everything" : "Restore this folder");
  page(h("div", { class: "bar" },
         crumbs(path, title, (p) => `#/snapshot/${id}` + enc(p)),
         h("div", { class: "actions" }, h("a", { class: "btn ghost", href: "#/snapshots" }, "All snapshots"),
           h("button", { class: "btn danger", onclick: async () => { if (await deleteSnapshots([sn])) go("#/snapshots"); } }, "Delete snapshot…"), restore)),
       list.items.length ? h("div", { class: "list" }, listHead(), list.items.map((it) => {
         const menu = () => [
           it.dir && { label: "Open", icon: "enter", run: () => go(`#/snapshot/${id}` + enc(it.path)) },
           { label: it.dir ? "Restore this folder" : "Restore this file", icon: "restore", run: () => restoreFrom(it.path) },
         ];
         const row = h(it.dir ? "a" : "div",
           it.dir ? { class: "row", href: `#/snapshot/${id}` + enc(it.path) } : { class: "row" },
           h("span", { class: "name" }, icon(it.dir ? "folder" : (kind(it.name) === "pdf" ? "text" : kind(it.name)), it.dir ? "folder" : ""),
             h("span", { class: "label" }, it.name)),
           h("span", { class: "size" }, it.dir ? "" : size(it.size)),
           h("span", { class: "date" }, when(it.mtime)), menuButton(menu, it.name, title));
         bindMenu(row, menu, it.name, title);
         return row;
       }))
       : h("div", { class: "empty" }, h("b", {}, "Empty folder")));
}

// ------------------------------------------------------------------ health
function stat(k, v, s, pct) {
  return h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v),
           s && h("div", { class: "s" }, s), pct != null && h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })));
}

function renderHealth(quiet) {
  if (!status) { if (!quiet) page(h("h1", {}, "Health"), h("p", { class: "lede" }, "Connecting to the drive…")); return; }
  const st = status.stats, hl = status.health, sc = hl.scrub || {}, sv = status.saved || {};
  const pct = st.chunks ? Math.floor((st.protected_chunks / st.chunks) * 1000) / 10 : 100;
  const lede = hl.parity
    ? `Every group of ${hl.group} pieces is stored with ${hl.pieces} spare pieces, so Discord can lose pieces without you losing files. Missing pieces are rebuilt and uploaded again automatically.`
    : "Spare pieces are turned off, so a piece Discord loses can't be rebuilt. Turn them on in Settings.";
  const checkPct = sc.total ? Math.min(100, Math.round(((sc.done || 0) / sc.total) * 100)) : null;
  const uploads = status.uploads.map((u) => h("div", { class: "kv-up" },
    h("div", { class: "row", style: "grid-template-columns:minmax(0,1fr) auto" },
      h("span", { class: "name" }, icon(kind(u.path)), h("span", { class: "label" }, u.path)),
      h("span", { class: "size" }, `${Math.floor((u.done / u.total) * 100)}% of ${size(u.size)}`))));
  const saved = (sv.compressed_saved || 0) + (sv.reused_bytes || 0);
  page(
    h("h1", {}, "Health"),
    h("p", { class: "lede" }, lede),
    h("div", { class: "stats" },
      stat("Protected", `${pct}%`, `${Number(st.protected_chunks).toLocaleString()} of ${Number(st.chunks).toLocaleString()} pieces`, pct),
      stat("Spare pieces", Number(st.spare_pieces).toLocaleString(), size(st.spare_bytes)),
      stat("Checked", sc.last_pass ? ago(sc.last_pass) : (sc.checked ? `${(sc.checked || 0).toLocaleString()}` : "Not yet"),
           sc.last_pass ? "last full check" : (hl.leader ? "pieces, first check running" : "another device checks"), checkPct),
      stat("Repaired", Number((sc.repaired || 0) + (hl.healer.repaired || 0)).toLocaleString(),
           (sc.lost || hl.healer.lost) ? `${sc.lost || hl.healer.lost} could not be rebuilt` : "nothing lost")),
    (hl.lost || []).length ? [
      h("h2", { class: "warn-text" }, "Parts that can't be repaired"),
      h("p", { class: "muted", style: "margin:0 0 12px;max-width:680px" }, "A part of these is gone from Discord (or unreadable) and there were not enough spare pieces to rebuild it. A damaged file can be replaced by an earlier version or a snapshot copy if one is intact, or by putting it on the drive again from another copy."),
      h("div", { class: "list rows-simple" }, hl.lost.map((e) => {
        const what = { file: "This file is damaged", version: `An earlier version (${e.at ? when(e.at) : "older"}) can't be brought back in full`,
                       snapshot: `Its copy in a snapshot (${e.at ? when(e.at) : "older"}) is damaged`, spare: "Still fully readable, with less protection" }[e.kind];
        return h("a", { class: "row", href: "#/files" + enc(parent(e.path)), style: "grid-template-columns:minmax(0,1fr) auto auto" },
          h("span", { class: "name" }, icon(e.kind === "spare" ? "info" : kind(e.path) === "pdf" ? "text" : kind(e.path)),
            h("span", { style: "min-width:0" }, h("span", { class: "label" }, base(e.path)), h("div", { class: "where" }, `${parent(e.path)} · ${what}`))),
          h("span", { class: "size" }, plural(e.pieces, "part")),
          h("span", { class: "date" }, `found ${ago(e.found)}`));
      }))] : null,
    h("div", { class: "health-actions" },
      h("a", { class: "btn ghost", href: "#/check" }, "Check files for problems…")),
    h("h2", {}, "Storage"),
    h("div", { class: "kv" },
      h("div", {}, "Files"), h("div", {}, `${plural(st.files, "file")} in ${plural(st.dirs, "folder")}`),
      h("div", {}, "Size"), h("div", {}, size(st.bytes)),
      h("div", {}, "Earlier versions"), h("div", {}, `${plural(st.versions, "version")} · ${size(st.version_bytes)}`),
      h("div", {}, "Snapshots"), h("div", {}, plural(st.snapshots, "snapshot")),
      h("div", {}, "Saved this session"), h("div", {}, saved ? `${size(saved)} not uploaded thanks to compression and identical pieces` : "—"),
      h("div", {}, "Uploading with"), h("div", {}, plural(status.bots, "bot")),
      h("div", {}, "Encryption"), h("div", {}, status.encrypted ? "On (AES-256-GCM): Discord sees only random data" : "Off")),
    uploads.length ? [h("h2", {}, "Uploading now"), h("div", { class: "list" }, uploads)] : null,
    h("h2", {}, "Devices"),
    h("div", { class: "list rows-simple" }, status.devices.map((d) => h("div", { class: "row", style: "grid-template-columns:minmax(0,1fr) auto" },
      h("span", { class: "name" }, h("span", { class: "label" }, d.name || d.id), h("span", { class: "tag" }, d.id), d.me && h("span", { class: "tag" }, "This device")),
      h("span", { class: "date" }, d.me ? "online" : `seen ${ago(d.seen)}`)))),
    status.lan.length ? [h("h2", {}, "On your network"), h("div", { class: "kv" },
      h("div", {}, "Open on your phone"), h("div", {}, h("code", {}, status.lan[0])))] : null,
    h("p", { class: "muted", style: "margin-top:40px;font-size:12px" }, `DiscordDrive ${status.version} · Made by XO.ST · `,
      h("button", { class: "link", onclick: whatsNew }, "What's new")));
}

// ------------------------------------------------------------------ shared links
async function renderShared() {
  const data = await attempt(() => api("/api/shares"));
  const items = data ? data.items : [];
  const menu = (l) => () => [
    { label: "Copy link", icon: "link", run: async () => {
      try { await navigator.clipboard.writeText(shareUrl(l)); toast("Link copied"); }
      catch { await ask({ title: "Share link", value: shareUrl(l), ok: "Done" }); } } },
    l.direct_path && { label: "Copy direct link (embeds in Discord)", icon: "link", run: async () => {
      try { await navigator.clipboard.writeText(directUrl(l)); toast("Direct link copied"); }
      catch { await ask({ title: "Direct link", value: directUrl(l), ok: "Done" }); } } },
    { label: "Open the link", icon: "open", run: () => window.open(l.path, "_blank", "noopener") },
    l.item && { label: "Show in Files", icon: "enter", run: () => go("#/files" + enc(l.dir ? l.item : parent(l.item))) },
    { label: "Link settings…", icon: "settings", run: () => shareDialog(null, l) },
    "-",
    { label: "Turn off the link", icon: "trash", danger: true, run: async () => {
      if (await ask({ title: "Turn off this link?", ok: "Turn off", danger: true,
                      text: "It stops working right away. The file itself stays where it is." })
          && await attempt(() => api("/api/shares/revoke", { id: l.id }), "Link turned off")) renderShared();
    } },
  ];
  page(h("h1", {}, "Shared"),
       h("p", { class: "lede" }, "Links you made. Anyone with a link can open what it points to until it expires or you turn it off."),
       items.length ? h("div", { class: "list" },
         h("div", { class: "row head" }, h("span", {}, "Item"), h("span", { class: "size" }, "Views"), h("span", { class: "date" }, "Works until"), h("span")),
         items.map((l) => {
           const name = l.item ? base(l.item) || "Drive" : "Deleted item";
           const row = h("div", { class: "row click", tabindex: "0", onclick: () => shareDialog(null, l) },
             h("span", { class: "name" }, icon(l.dir ? "folder" : kind(name) === "pdf" ? "text" : kind(name), l.dir ? "folder" : ""),
               h("span", { style: "min-width:0" }, h("span", { class: "label" }, name), h("div", { class: "where" }, l.item ? parent(l.item) : "")),
               l.upload && h("span", { class: "tag", title: "People send files into this folder" }, "File request"),
               l.password && h("span", { class: "tag", title: "Needs a password" }, icon("lock", "tag-ico"), "Password"),
               !l.download && h("span", { class: "tag" }, "View only")),
             h("span", { class: "size", title: l.upload ? "Files received" : "Views" }, l.upload ? `${l.views} in` : String(l.views)),
             h("span", { class: "date" }, l.expires ? when(l.expires) : "No expiry"),
             menuButton(menu(l), name, l.expires ? `until ${fmtFull.format(new Date(l.expires * 1000))}` : "no expiry"));
           bindMenu(row, menu(l), name);
           return row;
         }))
       : h("div", { class: "empty" }, h("b", {}, "No links yet"), "Right-click a file or folder and choose Share."));
}

// ------------------------------------------------------------------ settings
const SETTING_ROWS = [
  ["Web dashboard", [
    ["web_hosts", "Domain names", "Names this dashboard answers to behind a reverse proxy (e.g. drive.example.com). Several: separate with commas.", "list"],
    ["web_public_url", "Address for share links", "Used when you share from this computer itself, e.g. https://drive.example.com. Empty: the first domain name.", "text"],
    ["webdav_enabled", "Other apps (WebDAV)", "File managers, “Map network drive”, rclone and backup apps can open the drive at /dav/ with the dashboard’s user name and password.", "bool"],
    ["web_lan", "On your network", "Phones and computers at home (and a reverse proxy on another machine) can open the dashboard.", "bool"],
  ]],
  ["Discord", [
    ["discord_pace", "Pace", "How fast to talk to Discord. Gentle stays far below Discord's limits so it never has to slow you down; fast uploads quicker but can hit them.", ["gentle", "balanced", "fast"]],
    ["uploads_per_minute", "Uploads per minute (per bot)", "0 = from the pace (gentle: 30, about 4.5 MB/s per bot).", "num"],
    ["deletes_per_minute", "Deletes per minute (per bot)", "Removing old pieces happens in the background. 0 = from the pace (gentle: 15).", "num"],
    ["requests_per_minute", "Other requests per minute", "Checks, syncing and refreshing links. 0 = from the pace (gentle: 90).", "num"],
  ]],
  ["Protection", [
    ["parity_enabled", "Self-healing", "Spare pieces let lost pieces be rebuilt.", "bool"],
    ["parity_pieces", "Spare pieces per 10", "How many pieces of a group can be lost (2 is about 20% extra space).", "num"],
    ["scrub_enabled", "Background check", "Regularly confirm every piece is still on Discord, and repair what isn't.", "bool"],
    ["protect_existing", "Protect older files", "Add spare pieces to files uploaded without them.", "bool"],
  ]],
  ["Storage", [
    ["compression", "Compression", "Shrink pieces that compress well before encrypting them. Lossless.", "bool"],
    ["dedup", "Store identical pieces once", "A copy of a file uploads nothing new.", "bool"],
    ["keep_versions", "Keep earlier versions", "Replaced and deleted files can be brought back.", "bool"],
    ["version_retention_days", "Keep versions for (days)", "0 keeps them forever.", "num"],
    ["snapshot_interval_hours", "Snapshot every (hours)", "0 turns automatic snapshots off.", "num"],
    ["snapshot_keep_days", "Keep snapshots for (days)", "", "num"],
  ]],
  ["This computer", [
    ["cache_mode", "Read cache", "Disk keeps opened files for faster reopening; memory stores nothing on disk.", ["disk", "memory"]],
    ["hidden_folders", "Hidden folders", "Folders this computer doesn't show, e.g. /Movies. Separate with commas.", "list"],
    ["lock_timeout_minutes", "Lock folders again after (minutes)", "Password-locked folders close this long after they were last used. 0: only when you lock them or the drive restarts.", "num"],
  ]],
];

async function renderSettings() {
  const data = await attempt(() => api("/api/settings"));
  if (!data) return;
  const v = data.values;
  async function save(key, value) {
    const r = await attempt(() => api("/api/settings", { [key]: value }));
    if (r) { v[key] = value; toast(r.restart.length ? "Saved. Restart the drive for this to take effect." : "Saved"); }
  }
  const control = (key, type) => {
    if (type === "bool") return switchEl(v[key], (on) => save(key, on), key);
    if (Array.isArray(type)) {
      const s = h("select", { onchange: (e) => save(key, e.target.value) }, type.map((o) => h("option", { value: o, selected: v[key] === o }, o)));
      return s;
    }
    const val = type === "list" ? (v[key] || []).join(", ") : String(v[key] ?? "");
    const input = h("input", { value: val, inputmode: type === "num" ? "decimal" : null, spellcheck: "false",
                               placeholder: type === "list" ? "None" : type === "text" ? "Not set" : "",
                               onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); },
                               onchange: (e) => save(key, type === "list" ? e.target.value.split(",").map((x) => x.trim()).filter(Boolean) : e.target.value.trim()) });
    return input;
  };
  page(h("h1", {}, "Settings"),
       h("p", { class: "lede" }, "Changes are saved as you make them, for this computer's drive."),
       SETTING_ROWS.map(([title, rows]) => [h("h2", {}, title), h("div", { class: "settings" }, rows.map(([key, label, hint, type]) =>
         h("div", { class: "set-row" }, h("div", { class: "set-text" }, h("div", { class: "t" }, label), hint && h("div", { class: "s" }, hint),
           data.restart.includes(key) && h("div", { class: "s faint" }, "Takes effect after a restart")),
           h("div", { class: "set-ctl" + (type === "bool" ? "" : " wide") }, control(key, type)))))]).flat(),
       h("div", { id: "discord-settings" }),
       h("div", { id: "signin-settings" }));
  discordSection($("#discord-settings"));
  signInSection($("#signin-settings"));
}

// ------------------------------------------------------------------ notes
// Notes are files in /Notes on the drive: encrypted, synced to every device, with earlier versions.
// Typing saves to this computer at once and uploads to Discord when you pause; Save uploads now.
const NOTES = "/Notes";
let note = null;          // the open note: {path, name, mtime, dirty, gen, saving, timer}
let notesTimer = null;
let notesList = [];

function leaveNotes() {
  if (note && note.dirty) saveNote("later");
  if (route.name !== "notes") { clearInterval(notesTimer); notesTimer = null; note = null; }
}
window.addEventListener("beforeunload", () => { if (note && note.dirty) saveNote("later", true); });

const noteTitle = (name) => name.replace(/\.(md|txt)$/i, "");
const cleanTitle = (t) => (t.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim() || "Untitled").slice(0, 120);

async function renderNotes() {
  const name = route.rest.length ? decodeURIComponent(route.rest.join("/")) : "";
  const data = await attempt(() => api("/api/notes"));
  notesList = data ? data.items : [];
  const current = notesList.find((n) => n.name === name);
  const search = h("input", { type: "search", placeholder: "Search notes", class: "notes-search",
                              oninput: () => fillList(search.value.trim().toLowerCase()) });
  const list = h("div", { class: "note-list" });
  function fillList(filter = "") {
    const shown = notesList.filter((n) => !filter || (n.title + " " + n.snippet).toLowerCase().includes(filter));
    list.replaceChildren(...(shown.length ? shown.map((n) => h("a", { class: "note-item" + (n.name === name ? " on" : ""), href: "#/notes/" + encodeURIComponent(n.name) },
      h("div", { class: "nt" }, n.title), h("div", { class: "ns" }, h("span", {}, when(n.mtime)), " ", n.snippet.replace(n.title, "").trim() || "Empty note")))
      : [h("div", { class: "muted note-none" }, filter ? "No matches" : "No notes yet")]));
  }
  fillList();
  const side = h("div", { class: "notes-side" },
    h("div", { class: "notes-head" }, h("h1", {}, "Notes"), h("button", { class: "btn small", onclick: newNote }, icon("plus"), "New")),
    search, list);
  let editor;
  if (current) {
    editor = noteEditor(current);
  } else {
    editor = h("div", { class: "notes-empty" }, h("b", {}, notesList.length ? "Pick a note" : "Write your first note"),
      h("p", { class: "muted" }, "Notes save as you type and sync to all your devices."),
      h("button", { class: "btn", onclick: newNote }, "New note"));
  }
  page(h("div", { class: "notes" + (current ? " editing" : "") }, side, editor));
  clearInterval(notesTimer);
  notesTimer = setInterval(pollNotes, 4000);
}

function noteEditor(n) {
  if (!note || note.path !== n.path) note = { path: n.path, name: n.name, mtime: n.mtime, dirty: false, gen: 0, saving: false };
  const title = h("input", { class: "note-title", value: noteTitle(n.name), spellcheck: "false", "aria-label": "Title",
                             onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); body.focus(); } },
                             onchange: () => renameNote(title) });
  const body = h("textarea", { class: "note-body", placeholder: "Start writing…", "aria-label": "Note" });
  const state = h("span", { class: "note-state", id: "note-state" }, "Loading…");
  const saveBtn = h("button", { class: "btn small", title: "Back up to Discord now (Ctrl+S)", onclick: () => saveNote("now") }, icon("save"), "Save");
  const it = { path: n.path, name: n.name, dir: false, size: n.size, mtime: n.mtime, state: n.state };
  const more = menuButton(() => [
    { label: "Earlier versions", icon: "history", run: () => openItem(it, "versions") },
    { label: "Download", icon: "download", run: () => download(fileUrl(n.path, true), n.name) },
    { label: "Share…", icon: "link", run: () => shareDialog(it) },
    "-",
    { label: "Delete note", icon: "trash", danger: true, run: async () => {
      if (await ask({ title: `Delete “${noteTitle(n.name)}”?`, ok: "Delete", danger: true, text: "You can recover it from Deleted for a while." })
          && await attempt(() => api("/api/delete", { path: n.path }), "Note deleted")) { note = null; go("#/notes"); }
    } },
  ], noteTitle(n.name));
  body.addEventListener("input", () => {
    note.dirty = true;
    note.gen++;
    setState("Editing…");
    clearTimeout(note.timer);
    note.timer = setTimeout(() => saveNote("later"), 700);
  });
  body.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveNote("now"); }
  });
  loadNoteText(body, n.state);
  return h("div", { class: "note-editor" },
    h("div", { class: "note-bar" },
      h("a", { class: "icon-btn note-back", href: "#/notes", "aria-label": "All notes" }, icon("back")),
      state, h("div", { class: "actions" }, saveBtn, more)),
    title, h("div", { class: "note-conflict", id: "note-conflict", hidden: true }), body);
}

function setState(text) { const el = $("#note-state"); if (el) el.textContent = text; }
function noteUploadState() {
  const u = status && status.uploads.find((x) => x.path === note.path);
  const el = $("#note-state");
  if (!el) return;
  let bar = $("#note-progress");
  if (u && !note.dirty && !note.saving) {
    el.textContent = `Uploading to Discord ${Math.floor(u.pct)}%${u.eta ? " · " + eta(u.eta) : ""}`;
    if (!bar) { bar = h("div", { class: "meter note-meter", id: "note-progress" }, h("i")); el.after(bar); }
    bar.firstChild.style.width = u.pct + "%";
  } else if (bar) {
    bar.remove();
  }
}
function syncedText(state) { return state === "synced" ? "Backed up to Discord" : "Saved on this computer · uploading soon"; }

async function loadNoteText(body, state) {
  const r = await fetch(fileUrl(note.path), { credentials: "same-origin", cache: "no-store" }).catch(() => null);
  if (!r || !r.ok) { setState("Could not open this note"); return; }
  const text = await r.text();
  if (note.dirty) return;
  const pos = [body.selectionStart, body.selectionEnd];
  body.value = text;
  if (document.activeElement === body) body.setSelectionRange(...pos);
  setState(syncedText(state));
}

async function saveNote(mode, unloading) {
  if (!note) return;
  const ta = $(".note-body");
  if (!ta) return;
  clearTimeout(note.timer);
  if (note.saving && !unloading) { note.again = mode === "now" ? "now" : note.again || "later"; return; }
  const gen = note.gen;
  note.saving = true;
  setState("Saving…");
  try {
    const r = await fetch("/api/upload" + q({ path: note.path, save: mode }), {
      method: "POST", credentials: "same-origin", keepalive: !!unloading, headers: { "X-DD": "1" },
      body: new Blob([ta.value], { type: "text/plain;charset=utf-8" }) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "Could not save");
    if (data.mtime) note.mtime = data.mtime;
    if (note.gen === gen) note.dirty = false;
    setState(mode === "now" ? "Backing up to Discord…" : "Saved on this computer · uploading soon");
    if (mode === "now") note.backup = true;
  } catch (e) {
    setState("Not saved: " + e.message);
  } finally {
    note.saving = false;
    if (note.again) { const m = note.again; note.again = null; saveNote(m); }
  }
}

async function renameNote(input) {
  const t = cleanTitle(input.value);
  input.value = t;
  const name = t + ".md";
  if (!note || name === note.name) return;
  if (notesList.some((n) => n.name.toLowerCase() === name.toLowerCase())) { toast("A note with that title already exists"); input.value = noteTitle(note.name); return; }
  if (note.dirty) await saveNote("later");
  if (await attempt(() => api("/api/move", { from: note.path, to: join(NOTES, name) }))) {
    note.path = join(NOTES, name);
    note.name = name;
    history.replaceState(null, "", "#/notes/" + encodeURIComponent(name));
    route = parseRoute();
    refreshNoteList();
  }
}

async function newNote() {
  await api("/api/mkdir", { path: NOTES }).catch(() => null);
  const names = new Set(notesList.map((n) => n.name.toLowerCase()));
  let name = "Untitled.md";
  for (let i = 2; names.has(name.toLowerCase()); i++) name = `Untitled ${i}.md`;
  const r = await fetch("/api/upload" + q({ path: join(NOTES, name), save: "later" }),
                        { method: "POST", credentials: "same-origin", headers: { "X-DD": "1" }, body: "" });
  if (!r.ok) { toast("Could not create the note"); return; }
  note = null;
  go("#/notes/" + encodeURIComponent(name));
  setTimeout(() => { const t = $(".note-title"); if (t) { t.focus(); t.select(); } }, 300);
}

async function refreshNoteList() {
  const data = await api("/api/notes").catch(() => null);
  if (!data) return null;
  notesList = data.items;
  const search = $(".notes-search");
  const list = $(".note-list");
  if (list) {
    const filter = search ? search.value.trim().toLowerCase() : "";
    const name = note ? note.name : "";
    const shown = notesList.filter((n) => !filter || (n.title + " " + n.snippet).toLowerCase().includes(filter));
    list.replaceChildren(...(shown.length ? shown.map((n) => h("a", { class: "note-item" + (n.name === name ? " on" : ""), href: "#/notes/" + encodeURIComponent(n.name) },
      h("div", { class: "nt" }, n.title), h("div", { class: "ns" }, h("span", {}, when(n.mtime)), " ", n.snippet.replace(n.title, "").trim() || "Empty note")))
      : [h("div", { class: "muted note-none" }, "No notes yet")]));
  }
  return notesList;
}

async function pollNotes() {
  if (route.name !== "notes") return;
  const items = await refreshNoteList();
  if (!items || !note) return;
  const cur = items.find((n) => n.path === note.path);
  if (!cur) return;
  if (!note.dirty && !note.saving) setState(syncedText(cur.state));
  if (cur.mtime > note.mtime + 0.5) {
    note.mtime = cur.mtime;
    const box = $("#note-conflict");
    if (!note.dirty) {
      const ta = $(".note-body");
      if (ta) { await loadNoteText(ta, cur.state); toast("Updated with changes from another device"); }
    } else if (box) {
      box.hidden = false;
      box.replaceChildren(h("span", {}, "This note was changed somewhere else while you were typing."),
        h("button", { class: "btn ghost small", onclick: () => { note.dirty = false; box.hidden = true; loadNoteText($(".note-body"), cur.state); } }, "Load theirs"),
        h("button", { class: "btn small", onclick: () => { box.hidden = true; saveNote("now"); } }, "Keep mine"));
    }
  }
}

// ------------------------------------------------------------------ log
let logTimer = null;
let logAfter = 0;
let logLines = [];
let logPaused = false;
let logGen = null;
let logLevel = "all";
const LEVELS = { all: () => true, info: (l) => l !== "DEBUG", warn: (l) => l === "WARNING" || l === "ERROR" || l === "CRITICAL",
                 error: (l) => l === "ERROR" || l === "CRITICAL" };

function progressRow(title, sub, pct, ico) {
  return h("div", { class: "act-row" },
    h("div", { class: "act-top" }, icon(ico || kind(title)), h("span", { class: "act-name", title }, base(title) || title),
      h("span", { class: "act-pct" }, pct == null ? "" : Math.floor(pct) + "%")),
    pct == null ? null : h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })),
    h("div", { class: "act-sub" }, sub));
}

async function renderLog() {
  logAfter = 0;
  logLines = [];
  const now = h("div", { class: "activity" });
  const search = h("input", { type: "search", placeholder: "Search the log", class: "log-search", oninput: () => drawLog() });
  const chips = h("div", { class: "seg log-levels", role: "radiogroup" }, [["all", "All"], ["info", "Info"], ["warn", "Warnings"], ["error", "Errors"]]
    .map(([k, label]) => h("button", { type: "button", role: "radio", "aria-checked": String(k === logLevel),
                                       onclick: (e) => { logLevel = k; for (const b of chips.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); drawLog(); } }, label)));
  const pause = h("button", { class: "btn ghost small", onclick: () => { logPaused = !logPaused; pause.textContent = logPaused ? "Resume" : "Pause"; } }, logPaused ? "Resume" : "Pause");
  const save = h("button", { class: "btn ghost small", onclick: () => {
    const text = logLines.map((l) => `${new Date(l.t * 1000).toISOString()} [${l.l}] ${l.n}: ${l.m}`).join("\n");
    download(URL.createObjectURL(new Blob([text], { type: "text/plain" })), "discorddrive-log.txt");
  } }, "Download");
  const clear = h("button", { class: "btn ghost small", onclick: async () => {
    if (!await ask({ title: "Clear the log?", ok: "Clear", danger: true,
                     text: "Empties the list here and the log file on the computer running the drive. Download it first if you may need it." })) return;
    const r = await attempt(() => api("/api/log/clear", {}));
    if (r) { logLines = []; logAfter = 0; drawLog(); toast(r.file ? "Log cleared" : "Cleared here; the log file could not be emptied"); }
  } }, "Clear");
  const box = h("div", { class: "log-box", role: "log", "aria-live": "off" });
  page(h("h1", {}, "Log"),
       h("p", { class: "lede" }, "Everything the drive is doing, live: uploads and downloads with their progress, then every log line."),
       h("h2", {}, "Now"), now,
       h("div", { class: "log-bar" }, h("h2", { style: "margin:0" }, "Log"), chips, search, h("div", { class: "actions" }, pause, save, clear)),
       box);

  function drawLog() {
    const f = LEVELS[logLevel];
    const term = search.value.trim().toLowerCase();
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const shown = logLines.filter((l) => f(l.l) && (!term || (l.m + " " + l.n).toLowerCase().includes(term))).slice(-1500);
    box.replaceChildren(...shown.map((l) => h("div", { class: "log-line lvl-" + l.l.toLowerCase() },
      h("span", { class: "lt" }, fmtTime.format(new Date(l.t * 1000))), h("span", { class: "ll" }, l.l === "WARNING" ? "WARN" : l.l),
      h("span", { class: "ln" }, l.n), h("span", { class: "lm" }, l.m))));
    if (atBottom || !box.dataset.scrolled) { box.scrollTop = box.scrollHeight; box.dataset.scrolled = "1"; }
  }

  async function tick() {
    if (route.name !== "log") { clearInterval(logTimer); logTimer = null; return; }
    const [a, lg] = await Promise.all([api("/api/activity").catch(() => null),
                                       logPaused ? null : api("/api/log" + q({ after: logAfter })).catch(() => null)]);
    if (a) {
      const parts = [];
      a.uploads.forEach((u) => parts.push(progressRow(u.path,
        u.stage === "spare pieces" ? "Adding spare pieces" :
          `Uploading to Discord · ${size(u.bytes)} of ${size(u.size)} · piece ${u.done} of ${u.total}${u.speed ? " · " + speed(u.speed) : ""}${u.eta ? " · " + eta(u.eta) : ""}`, u.pct)));
      a.offline.forEach((j) => parts.push(progressRow(j.path, `Downloading to keep offline · ${size(j.bytes)} of ${size(j.total)}${j.speed ? " · " + speed(j.speed) : ""}${j.eta ? " · " + eta(j.eta) : ""}`, j.pct, "download")));
      a.downloads.forEach((d) => parts.push(progressRow(d.path, `Downloading ${plural(d.pieces, "piece")} (${size(d.bytes)}) for reading`, null, "download")));
      a.queue.forEach((qd) => parts.push(progressRow(qd.path, `Waiting to upload · ${size(qd.size)}${qd.due > a.time ? " · starts in " + Math.ceil(qd.due - a.time) + " s" : ""}`, null)));
      const sum = [];
      if (a.upload_speed) sum.push(`Uploading ${speed(a.upload_speed)}${a.upload_eta ? ", all done in about " + eta(a.upload_eta).replace(" left", "") : ""}`);
      if (a.download_speed) sum.push(`Downloading ${speed(a.download_speed)}`);
      if (a.sync.outbox) sum.push(`${plural(a.sync.outbox, "change")} to send to other devices`);
      if (a.sync.trash) sum.push(`${plural(a.sync.trash, "old piece")} to delete from Discord`);
      if (a.sync.repairs) sum.push(`${plural(a.sync.repairs, "repair")} queued`);
      if (a.sync.failures) sum.push("Can't reach Discord right now; retrying");
      if (a.check && a.check.total) sum.push(`Background check: ${Number(a.check.done || 0).toLocaleString()} of ${Number(a.check.total).toLocaleString()} pieces`);
      now.replaceChildren(h("div", { class: "act-sum" }, sum.length ? sum.join(" · ") : "Nothing is uploading or downloading right now."), ...parts);
    }
    if (lg && lg.gen !== undefined && lg.gen !== logGen) {       // cleared (here, in another tab or in the terminal)
      if (logGen !== null) logLines = [];
      logGen = lg.gen;
      if (!lg.lines.length) drawLog();
    }
    if (lg && lg.lines.length) {
      logLines = logLines.concat(lg.lines).slice(-5000);
      logAfter = lg.lines[lg.lines.length - 1].i;
      drawLog();
    } else if (!logLines.length) {
      drawLog();
    }
  }
  clearInterval(logTimer);
  await tick();
  logTimer = setInterval(tick, 1000);
}

// ------------------------------------------------------------------ contacts
// One address book, /Contacts/Contacts.vcf on the drive (encrypted, synced, earlier versions kept).
let book = [];
const chosenContacts = new Set();
const LABELS = ["mobile", "home", "work", "main", "other", "fax"];

function initials(c) {
  const parts = (c.name || c.org || "?").trim().split(/\s+/);
  return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
function avatar(c, big) {
  const el = h("span", { class: "avatar-c" + (big ? " big" : "") });
  if (c.photo) el.append(h("img", { src: c.photo, alt: "" }));
  else el.textContent = initials(c);
  return el;
}
const subline = (c) => [c.title, c.org].filter(Boolean).join(" · ") || (c.phones[0] || {}).value || (c.emails[0] || {}).value || "";

async function loadBook() {
  const data = await attempt(() => api("/api/contacts"));
  book = data ? data.items : [];
  return book;
}

async function renderContacts() {
  const [id, mode] = route.rest.map(decodeURIComponent);
  await loadBook();
  const current = id === "new" ? null : book.find((c) => c.uid === id);
  const search = h("input", { type: "search", class: "notes-search", placeholder: `Search ${plural(book.length, "contact")}`,
                              oninput: () => fillList() });
  const list = h("div", { class: "contact-list" });
  function fillList() {
    const t = search.value.trim().toLowerCase();
    const shown = book.filter((c) => !t || [c.name, c.org, c.title, c.note, ...c.phones.map((p) => p.value), ...c.emails.map((e) => e.value)]
      .join(" ").toLowerCase().includes(t));
    let letter = "";
    const rows = [];
    for (const c of shown) {
      const l = ((c.name || c.org || "#")[0] || "#").toUpperCase().replace(/[^A-Z]/, "#");
      if (l !== letter && !t) { letter = l; rows.push(h("div", { class: "letter" }, l)); }
      const box = h("button", { class: "cb row-cb", role: "checkbox", "aria-checked": String(chosenContacts.has(c.uid)), "aria-label": `Select ${c.name}`,
                                onclick: (e) => { e.preventDefault(); e.stopPropagation(); chosenContacts.has(c.uid) ? chosenContacts.delete(c.uid) : chosenContacts.add(c.uid); fillList(); syncContactBar(); } }, icon("check"));
      const row = h("a", { class: "contact-item" + (current && current.uid === c.uid ? " on" : "") + (chosenContacts.has(c.uid) ? " sel" : ""),
                           href: "#/contacts/" + encodeURIComponent(c.uid) },
        box, avatar(c), h("span", { class: "ci-text" }, h("span", { class: "nt" }, c.name || "No name"), h("span", { class: "ns" }, subline(c))));
      bindMenu(row, () => contactMenu(c), c.name);
      rows.push(row);
    }
    list.classList.toggle("selecting", chosenContacts.size > 0);
    list.replaceChildren(...(rows.length ? rows : [h("div", { class: "muted note-none" }, t ? "No matches" : "No contacts yet")]));
  }
  const bar = h("div", { class: "contact-selbar" });
  function syncContactBar() {
    const n = chosenContacts.size;
    bar.hidden = !n;
    bar.replaceChildren(h("span", { class: "count" }, `${n} selected`),
      h("button", { class: "btn ghost small", onclick: () => { chosenContacts.clear(); fillList(); syncContactBar(); } }, "Clear"),
      h("button", { class: "btn ghost small", onclick: () => download("/api/contacts/export" + q({ uids: [...chosenContacts].join(",") }), "contacts.vcf") }, "Export"),
      h("button", { class: "btn danger small", onclick: () => deleteContacts([...chosenContacts]) }, "Delete"));
  }
  fillList();
  syncContactBar();
  const side = h("div", { class: "notes-side" },
    h("div", { class: "notes-head" }, h("h1", {}, "Contacts"),
      h("div", { class: "actions" },
        menuButton(() => [
          { label: "Import contacts…", icon: "upload", run: () => $("#pick-contacts").click() },
          { label: "How to export from your phone", icon: "info", run: importHelp },
          { label: "Export all (.vcf)", icon: "download", run: () => download("/api/contacts/export", "contacts.vcf") },
          { label: "Earlier versions of the address book", icon: "history",
            run: () => openItem({ path: "/Contacts/Contacts.vcf", name: "Contacts.vcf", dir: false, size: 0, mtime: 0, state: "synced" }, "versions") },
        ], "Contacts"),
        h("button", { class: "btn small", onclick: () => go("#/contacts/new") }, icon("plus"), "New"))),
    search, bar, list);
  let pane;
  if (id === "new") pane = contactForm(null);
  else if (current && mode === "edit") pane = contactForm(current);
  else if (current) pane = contactView(current);
  else pane = h("div", { class: "notes-empty" },
    h("b", {}, book.length ? "Pick a contact" : "Your contacts, encrypted"),
    h("p", { class: "muted" }, book.length ? `${plural(book.length, "contact")}, synced to all your devices.`
      : "Import them from your phone, Google, iCloud or Outlook, or add them one by one."),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", onclick: () => $("#pick-contacts").click() }, "Import…"),
      h("button", { class: "btn", onclick: () => go("#/contacts/new") }, "New contact")),
    !book.length && h("button", { class: "link", onclick: importHelp }, "How do I get my contacts out of my phone?"));
  page(h("div", { class: "notes contacts" + (id ? " editing" : "") }, side, pane));
}

function contactMenu(c) {
  return [
    { label: "Open", icon: "open", run: () => go("#/contacts/" + encodeURIComponent(c.uid)) },
    { label: "Edit", icon: "rename", run: () => go(`#/contacts/${encodeURIComponent(c.uid)}/edit`) },
    { label: "Export (.vcf)", icon: "download", run: () => download("/api/contacts/export" + q({ uids: c.uid }), (c.name || "contact") + ".vcf") },
    { label: chosenContacts.has(c.uid) ? "Deselect" : "Select", icon: "check", run: () => { chosenContacts.has(c.uid) ? chosenContacts.delete(c.uid) : chosenContacts.add(c.uid); renderContacts(); } },
    "-",
    { label: "Delete", icon: "trash", danger: true, run: () => deleteContacts([c.uid]) },
  ];
}

function contactView(c) {
  const copy = (v) => h("button", { class: "icon-btn copy", title: "Copy", "aria-label": "Copy", onclick: async () => {
    try { await navigator.clipboard.writeText(v); toast("Copied"); } catch { toast(v); } } }, icon("copy"));
  const rows = [];
  const line = (ico, label, value, href) => rows.push(h("div", { class: "c-field" }, icon(ico),
    h("div", { class: "c-val" }, h("div", { class: "c-label" }, label),
      href ? h("a", { href, rel: "noopener" }, value) : h("div", { class: "c-text" }, value)), copy(value)));
  c.phones.forEach((p) => line("phone", p.label || "phone", p.value, "tel:" + p.value.replace(/[^\d+]/g, "")));
  c.emails.forEach((e) => line("mail", e.label || "email", e.value, "mailto:" + e.value));
  c.addresses.forEach((a) => {
    const text = [a.street, [a.postcode, a.city].filter(Boolean).join(" "), a.region, a.country].filter(Boolean).join("\n");
    line("pin", a.label || "address", text, "https://www.openstreetmap.org/search?query=" + encodeURIComponent(text.replace(/\n/g, ", ")));
  });
  c.urls.forEach((u) => line("globe", "website", u, /^https?:\/\//i.test(u) ? u : "https://" + u));
  if (c.bday) line("cake", "birthday", c.bday.startsWith("--") ? c.bday.slice(2) : c.bday);
  const first = c.phones[0], mail = c.emails[0];
  return h("div", { class: "note-editor contact-view" },
    h("div", { class: "note-bar" },
      h("a", { class: "icon-btn note-back", href: "#/contacts", "aria-label": "All contacts" }, icon("back")),
      h("div", { class: "actions" },
        h("button", { class: "btn ghost small", onclick: () => go(`#/contacts/${encodeURIComponent(c.uid)}/edit`) }, "Edit"),
        menuButton(() => contactMenu(c), c.name))),
    h("div", { class: "c-head" }, avatar(c, true), h("h2", {}, c.name || "No name"), subline(c) && h("div", { class: "muted" }, [c.title, c.org].filter(Boolean).join(" · ")),
      h("div", { class: "c-quick" },
        first && h("a", { class: "c-act", href: "tel:" + first.value.replace(/[^\d+]/g, "") }, icon("phone"), "Call"),
        first && h("a", { class: "c-act", href: "sms:" + first.value.replace(/[^\d+]/g, "") }, icon("message"), "Message"),
        mail && h("a", { class: "c-act", href: "mailto:" + mail.value }, icon("mail"), "Email"))),
    h("div", { class: "c-fields" }, rows),
    c.note && h("div", { class: "c-note" }, h("div", { class: "c-label" }, "Notes"), h("div", { class: "c-text" }, c.note)));
}

function contactForm(c) {
  const d = JSON.parse(JSON.stringify(c || { uid: "", name: "", first: "", last: "", org: "", title: "", bday: "", note: "",
                                                  phones: [{ label: "mobile", value: "" }], emails: [{ label: "home", value: "" }],
                                                  addresses: [], urls: [], photo: "" }));
  const text = (key, label, attrs = {}) => h("label", { class: "c-input" }, h("span", {}, label),
    h("input", { value: d[key] || "", ...attrs, oninput: (e) => { d[key] = e.target.value; } }));
  const multi = (key, label, make, render) => {
    const box = h("div", { class: "c-multi" });
    const draw = () => {
      box.replaceChildren(h("div", { class: "c-multi-head" }, h("span", {}, label),
        h("button", { type: "button", class: "link", onclick: () => { d[key].push(make()); draw(); } }, "Add")),
        ...d[key].map((item, i) => h("div", { class: "c-row" }, ...render(item),
          h("button", { type: "button", class: "icon-btn", "aria-label": "Remove", onclick: () => { d[key].splice(i, 1); draw(); } }, icon("minus")))));
    };
    draw();
    return box;
  };
  const labelSel = (item) => h("select", { onchange: (e) => { item.label = e.target.value; } },
    [...new Set([...LABELS, item.label || "other"])].map((l) => h("option", { value: l, selected: (item.label || "other") === l }, l)));
  const inp = (item, k, ph, type = "text") => h("input", { type, value: item[k] || "", placeholder: ph, oninput: (e) => { item[k] = e.target.value; } });
  const photo = h("div", { class: "c-photo" });
  const drawPhoto = () => photo.replaceChildren(avatar(d, true),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn ghost small", onclick: () => pickPhoto() }, d.photo ? "Change photo" : "Add photo"),
      d.photo && h("button", { type: "button", class: "btn ghost small", onclick: () => { d.photo = ""; drawPhoto(); } }, "Remove")));
  function pickPhoto() {
    const input = h("input", { type: "file", accept: "image/*" });
    input.onchange = () => {
      const f = input.files[0];
      if (!f) return;
      const img = new Image();
      img.onload = () => {
        const side = 256, scale = Math.max(side / img.width, side / img.height);
        const cv = h("canvas", { width: side, height: side });
        cv.getContext("2d").drawImage(img, (side - img.width * scale) / 2, (side - img.height * scale) / 2, img.width * scale, img.height * scale);
        d.photo = cv.toDataURL("image/jpeg", 0.85);
        URL.revokeObjectURL(img.src);
        drawPhoto();
      };
      img.src = URL.createObjectURL(f);
    };
    input.click();
  }
  drawPhoto();
  const saveBtn = h("button", { class: "btn small", onclick: async () => {
    d.phones = d.phones.filter((p) => p.value.trim());
    d.emails = d.emails.filter((e) => e.value.trim());
    d.addresses = d.addresses.filter((a) => ["street", "city", "region", "postcode", "country"].some((k) => (a[k] || "").trim()));
    d.urls = d.urls.map((u) => (typeof u === "string" ? u : u.value || "").trim()).filter(Boolean);
    d.name = [d.first, d.last].filter((x) => (x || "").trim()).join(" ").trim() || d.name;
    saveBtn.disabled = true;
    const r = await attempt(() => api("/api/contacts/save", { contact: d }), "Saved");
    saveBtn.disabled = false;
    if (r) go("#/contacts/" + encodeURIComponent(r.contact.uid));
  } }, "Save");
  return h("div", { class: "note-editor contact-form" },
    h("div", { class: "note-bar" },
      h("a", { class: "icon-btn note-back", href: c ? "#/contacts/" + encodeURIComponent(c.uid) : "#/contacts", "aria-label": "Cancel" }, icon("back")),
      h("span", { class: "note-state" }, c ? "Edit contact" : "New contact"),
      h("div", { class: "actions" }, h("a", { class: "btn ghost small", href: c ? "#/contacts/" + encodeURIComponent(c.uid) : "#/contacts" }, "Cancel"), saveBtn)),
    h("div", { class: "c-form" },
      photo,
      h("div", { class: "c-grid" }, text("first", "First name", { autofocus: !c }), text("last", "Last name"),
        text("org", "Company"), text("title", "Job title")),
      multi("phones", "Phone", () => ({ label: "mobile", value: "" }), (p) => [labelSel(p), inp(p, "value", "Phone number", "tel")]),
      multi("emails", "Email", () => ({ label: "home", value: "" }), (e) => [labelSel(e), inp(e, "value", "name@example.com", "email")]),
      multi("addresses", "Address", () => ({ label: "home", street: "", city: "", region: "", postcode: "", country: "" }), (a) => [labelSel(a),
        h("div", { class: "c-addr" }, inp(a, "street", "Street"), h("div", { class: "c-pair" }, inp(a, "postcode", "Postcode"), inp(a, "city", "City")),
          h("div", { class: "c-pair" }, inp(a, "region", "State / region"), inp(a, "country", "Country")))]),
      (() => { d.urls = d.urls.map((u) => (typeof u === "string" ? { value: u } : u)); const box = multi("urls", "Website", () => ({ value: "" }), (u) => [inp(u, "value", "example.com")]); return box; })(),
      h("div", { class: "c-grid" }, text("bday", "Birthday", { type: "date" })),
      h("label", { class: "c-input" }, h("span", {}, "Notes"), h("textarea", { rows: 4, oninput: (e) => { d.note = e.target.value; } }, d.note || ""))));
}

async function deleteContacts(uids) {
  const one = uids.length === 1 && book.find((c) => c.uid === uids[0]);
  if (!await ask({ title: one ? `Delete ${one.name || "this contact"}?` : `Delete ${plural(uids.length, "contact")}?`, ok: "Delete", danger: true,
                   text: "Removed on every device. The address book keeps earlier versions, so this can be undone from its history." })) return;
  const r = await attempt(() => api("/api/contacts/delete", { uids }), uids.length === 1 ? "Contact deleted" : `${plural(uids.length, "contact")} deleted`);
  if (r) { uids.forEach((u) => chosenContacts.delete(u)); go("#/contacts"); render(); }
}

function importHelp() {
  const step = (title, text) => h("div", { class: "help-step" }, h("b", {}, title), h("p", {}, text));
  modal("Get your contacts out of your phone",
    step("iPhone", "Contacts app → Lists → long-press All Contacts → Export. Save the .vcf, then Import it here. (Or iCloud.com → Contacts → select all → Export vCard.)"),
    step("Android", "Contacts app → Fix & manage (or Settings) → Export to file. That makes a .vcf in Downloads."),
    step("Google Contacts", "contacts.google.com → Export → vCard (or Google CSV)."),
    step("Outlook", "Outlook.com → People → Manage contacts → Export contacts (CSV)."),
    h("p", { class: "muted" }, "On a phone you can import straight from this page: Contacts → … → Import contacts. Duplicates are skipped. To put contacts back on a phone, use Export and open the .vcf there."));
}

$("#pick-contacts").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  let added = 0, skipped = 0;
  for (const f of files) {
    const r = await fetch("/api/contacts/import" + q({ name: f.name }), { method: "POST", credentials: "same-origin", headers: { "X-DD": "1" }, body: f });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { toast(data.error || `Could not import ${f.name}`); continue; }
    added += data.added; skipped += data.skipped;
  }
  if (added || skipped) toast(`${plural(added, "contact")} imported${skipped ? `, ${skipped} already there` : ""}`);
  if (route.name === "contacts") renderContacts(); else go("#/contacts");
});

// ------------------------------------------------------------------ check files
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false && k !== ""));
let checkTimer = null;
const unreadable = new Set();

async function renderCheck() {
  const scope = h("input", { value: route.qs.get("path") || "/", spellcheck: "false", "aria-label": "Folder to check" });
  const pick = h("button", { class: "btn ghost", onclick: async () => {
    const p = await pickFolder("Check which folder?", scope.value || "/", [], "Check this folder");
    if (p != null) scope.value = p;
  } }, "Choose…");
  const startBtn = h("button", { class: "btn", onclick: async () => {
    unreadable.clear();
    if (await attempt(() => api("/api/check/start", { path: scope.value || "/" }))) tick();
  } }, "Start check");
  const stopBtn = h("button", { class: "btn ghost", hidden: true, onclick: () => api("/api/check/stop", {}).catch(() => null) }, "Stop");
  const progress = h("div", { class: "check-progress" });
  const results = h("div", { class: "check-results" });
  page(h("h1", {}, "Check files"),
       h("p", { class: "lede" }, "Reads every file in a folder once, the way opening it would. Pieces that can be rebuilt from spare pieces are repaired on the way; files that can't be read are listed below, so you can delete them."),
       h("div", { class: "check-scope" }, scope, pick, startBtn, stopBtn),
       progress, results);

  function draw(st) {
    const running = !!st.running;
    startBtn.disabled = running;
    stopBtn.hidden = !running;
    if (!st.started) { progress.replaceChildren(); results.replaceChildren(); return; }
    const pct = st.total_bytes ? Math.min(100, (100 * st.bytes) / st.total_bytes) : (running ? 0 : 100);
    const elapsed = Math.max(1, (st.finished || st.now) - st.started);
    const bps = st.bytes / elapsed;
    fill(progress,
      h("div", { class: "act-top" }, h("span", { class: "act-name" }, running ? `Checking ${st.path}` : `Checked ${st.path}`),
        h("span", { class: "act-pct" }, Math.floor(pct) + "%")),
      h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })),
      h("div", { class: "act-sub" }, `${Number(st.checked).toLocaleString()} of ${plural(st.files, "file")} · ${size(st.bytes)} of ${size(st.total_bytes)}`
        + (running && bps ? ` · ${speed(bps)} · ${eta((st.total_bytes - st.bytes) / bps)}` : "")
        + (st.repaired ? ` · ${plural(st.repaired, "piece")} repaired` : "")
        + (st.error ? ` · stopped: ${st.error}` : "")),
      running && st.current ? h("div", { class: "act-sub faint" }, st.current) : null);
    const bad = st.bad || [];
    if (!bad.length) {
      results.replaceChildren(running ? "" : h("div", { class: "empty" }, h("b", {}, "Everything can be read"),
        st.repaired ? `${plural(st.repaired, "piece")} were rebuilt and uploaded again.` : "No problems found."));
      return;
    }
    for (const uid of [...unreadable]) if (!bad.some((b) => b.uid === uid)) unreadable.delete(uid);
    const all = checkbox(unreadable.size === bad.length, (on) => { bad.forEach((b) => on ? unreadable.add(b.uid) : unreadable.delete(b.uid)); draw(st); }, "Select all");
    setBox(all, unreadable.size === bad.length ? true : unreadable.size ? "mixed" : false);
    const chosen = bad.filter((b) => unreadable.has(b.uid));
    const keyProblem = bad.some((b) => b.kind === "key");
    fill(results,
      h("h2", {}, `${plural(bad.length, "file")} can't be read`),
      keyProblem ? h("p", { class: "muted check-hint" }, "Files that “can't be decrypted” were saved with a different encryption key. If you still have that key (an old device or config file), add it in the DiscordDrive menu (Tools → Add an older encryption key) and they become readable again. Only delete them if that key is gone.") : null,
      h("div", { class: "selbar" },
        h("span", { class: "count" }, chosen.length ? `${plural(chosen.length, "file")} selected · ${size(chosen.reduce((a, b) => a + b.size, 0))}` : "Select files to delete"),
        h("div", { class: "actions" },
          h("button", { class: "btn danger small", disabled: !chosen.length, onclick: () => removeUnreadable(chosen, st) }, "Delete for good"))),
      h("div", { class: "list" },
        h("div", { class: "row head" }, h("span", { class: "name" }, all, h("span", {}, "File")), h("span", { class: "size" }, "Size"),
          h("span", { class: "date" }, ""), h("span")),
        bad.map((b) => {
          const box = checkbox(unreadable.has(b.uid), (on) => { on ? unreadable.add(b.uid) : unreadable.delete(b.uid); draw(st); }, `Select ${base(b.path)}`);
          return h("div", { class: "row click pick" + (unreadable.has(b.uid) ? " sel" : ""),
                            onclick: (e) => { if (e.target.closest("button")) return; unreadable.has(b.uid) ? unreadable.delete(b.uid) : unreadable.add(b.uid); draw(st); } },
            h("span", { class: "name" }, box, icon(kind(b.path) === "pdf" ? "text" : kind(b.path)),
              h("span", { style: "min-width:0" }, h("span", { class: "label" }, base(b.path)), h("div", { class: "where" }, `${parent(b.path)} · ${b.reason}`))),
            h("span", { class: "size" }, size(b.size)), h("span", { class: "date" }), h("span"));
        })));
  }

  async function removeUnreadable(list, st) {
    const ok = await ask({ title: `Delete ${plural(list.length, "unreadable file")} for good?`, ok: "Delete for good", danger: true,
                           text: "They are removed on every device, and no earlier version is kept (they can't be read anyway)." });
    if (!ok) return;
    const r = await attempt(() => api("/api/check/remove", { uids: list.map((b) => b.uid) }));
    if (r) { toast(`${plural(r.removed, "file")} deleted`); list.forEach((b) => unreadable.delete(b.uid)); tick(); }
  }

  async function tick() {
    clearTimeout(checkTimer);
    if (route.name !== "check") return;
    const st = await api("/api/check").catch(() => null);
    if (!st) return;
    draw(st);
    if (st.running) checkTimer = setTimeout(tick, 1000);
  }
  tick();
}

// ------------------------------------------------------------------ password-locked folders
// A locked folder or file isn't listed anywhere until its password is typed (the padlock at the top).
// Typing a password opens everything locked with it, in this browser only, for a while.
function pwInput(placeholder) {
  return h("input", { type: "password", autocomplete: "new-password", placeholder });
}

function lockDialog(it) {
  const a = pwInput("Password"), b = pwInput("The same password again");
  const go = h("button", { class: "btn", type: "button", onclick: async () => {
    if (a.value.length < 4) return toast("Use at least 4 characters");
    if (a.value !== b.value) return toast("The two passwords are different");
    go.disabled = true;
    const r = await attempt(() => api("/api/locks/add", { path: it.path, password: a.value }));
    go.disabled = false;
    if (r) { m.close(); toast(`Locked. It stays open in this browser until you lock it again${r.minutes ? ` or ${r.minutes} minutes pass without using it` : ""}.`); await refreshStatus(); render(); }
  } }, "Lock");
  const m = modal(`Lock “${it.name}”`,
    h("p", { class: "muted", style: "margin:0 0 16px" }, `${it.dir ? "This folder and everything in it" : "This file"} disappears from the drive and from this dashboard, on all your devices, until its password is typed. `
      + "Keep the password somewhere: it can't be looked up."),
    field("Password", a), field("Again", b, "Locked folders that share a password open together."),
    h("div", { class: "modal-actions" }, go));
  a.addEventListener("keydown", (e) => { if (e.key === "Enter") b.focus(); });
  b.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  a.focus();
}

async function unlockDialog() {
  const info = await api("/api/locks").catch(() => null);
  const pw = pwInput("Password");
  let drive = false;
  const go = h("button", { class: "btn", type: "button", onclick: async () => {
    go.disabled = true;
    const r = await attempt(() => api("/api/locks/unlock", { password: pw.value, drive }));
    go.disabled = false;
    if (!r) { pw.select(); return; }
    m.close();
    toast(`${plural(r.opened, "locked item")} open${r.minutes ? ` until ${r.minutes} minutes pass without using ${r.opened === 1 ? "it" : "them"}` : ""}`);
    await refreshStatus();
    render();
  } }, "Unlock");
  const m = modal("Unlock",
    h("p", { class: "muted", style: "margin:0 0 16px" }, "Type the password of a locked folder or file. Everything locked with it shows up in this browser."),
    field("Password", pw),
    info && h("div", { class: "toggle" },
      h("div", {}, h("div", { class: "t" }, `Also show on the drive (${info.mount})`),
        h("div", { class: "s" }, `On ${info.host}, for every program there, for the same time`)),
      switchEl(false, (v) => { drive = v; }, "Also show on the drive")),
    h("div", { class: "modal-actions" }, go));
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  pw.focus();
}

async function lockAllNow() {
  if (await attempt(() => api("/api/locks/lock", {}), "Locked again")) { closeSheet(); await refreshStatus(); render(); }
}

function removeLockDialog(it) {
  const pw = pwInput("Password of the lock");
  const go = h("button", { class: "btn danger", type: "button", onclick: async () => {
    go.disabled = true;
    const r = await attempt(() => api("/api/locks/remove", { path: it.path, password: pw.value }), "Lock removed");
    go.disabled = false;
    if (r) { m.close(); await refreshStatus(); render(); } else pw.select();
  } }, "Remove the lock");
  const m = modal(`Remove the lock of “${it.name}”`,
    h("p", { class: "muted", style: "margin:0 0 16px" }, "It will be visible again on the drive and in the dashboard, on all your devices."),
    field("Password", pw), h("div", { class: "modal-actions" }, go));
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go.click(); });
  pw.focus();
}

$("#locks").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  const open = status && status.locks ? status.locks.open : 0;
  openMenu([
    { label: open ? `${plural(open, "locked item")} open here` : "Locked folders are hidden", icon: "lock", disabled: true },
    "-",
    { label: "Unlock…", icon: "lock", run: unlockDialog },
    { label: "Lock again now", icon: "check", disabled: !open, run: lockAllNow },
  ], r.right - 220, r.bottom + 6, "Locked folders");
});

// ------------------------------------------------------------------ thumbnails
// The browser makes them (it can decode photos and grab a video frame) the first time a picture is
// shown, and hands them to the drive, which keeps them encrypted; after that they load from there.
const THUMB_PX = 400;
const isMedia = (it) => !it.dir && ["image", "video"].includes(kind(it.name)) && !/\.svg$/i.test(it.name);
const thumbUrl = (it) => "/api/thumb" + q({ path: it.path, v: `${it.size}-${Math.floor(it.mtime)}` });
const thumbMade = new Map();        // path -> object URL of a thumbnail made in this tab (null: can't be made)
const thumbJobs = new Map();        // path -> {it, cbs}: waiting to be made
let thumbActive = 0;
let thumbBatch = null;              // "create all": {todo, done, stop}

function once(el, name, ms = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    el.addEventListener(name, () => { clearTimeout(t); resolve(); }, { once: true });
    el.addEventListener("error", () => { clearTimeout(t); reject(new Error("can't be shown")); }, { once: true });
  });
}

function shrink(el, w, ht) {
  const s = Math.min(1, THUMB_PX / Math.max(w, ht, 1));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w * s));
  c.height = Math.max(1, Math.round(ht * s));
  c.getContext("2d").drawImage(el, 0, 0, c.width, c.height);
  return new Promise((resolve) => c.toBlob((b) => (b && b.type === "image/webp") ? resolve(b) : c.toBlob(resolve, "image/jpeg", 0.8),
                                          "image/webp", 0.78));
}

async function makeThumb(url, k) {
  if (k === "image") {
    const img = new Image();
    img.src = url;
    await img.decode();
    return shrink(img, img.naturalWidth, img.naturalHeight);
  }
  const v = document.createElement("video");
  v.muted = true; v.playsInline = true; v.preload = "metadata";
  try {
    v.src = url;
    await once(v, "loadedmetadata");
    v.currentTime = Math.min(3, (v.duration || 0) * 0.1);
    await once(v, "seeked");
    if (!v.videoWidth) throw new Error("no picture");
    return await shrink(v, v.videoWidth, v.videoHeight);
  } finally { v.removeAttribute("src"); v.load(); }
}

async function storeThumb(path, blob) {
  await fetch("/api/thumb" + q({ path }), { method: "POST", credentials: "same-origin", headers: { "X-DD": "1" },
                                           body: blob || new Blob([]) }).catch(() => null);
}

function wantThumb(it, cb, first, batch) {
  if (thumbMade.has(it.path)) { if (cb) cb(thumbMade.get(it.path)); return; }
  let job = thumbJobs.get(it.path);
  if (!job) { job = { it, cbs: [] }; thumbJobs.set(it.path, job); }
  if (first) job.first = true;
  if (batch) job.batch = true;
  if (cb) job.cbs.push(cb);
  pumpThumbs();
}

function pumpThumbs() {
  while (thumbActive < 2 && thumbJobs.size) {
    // what is on screen goes before the rest of a "create all"
    const job = [...thumbJobs.values()].reverse().find((j) => j.first) || thumbJobs.values().next().value;
    if (!job.first && thumbBatch && thumbBatch.stop) { thumbJobs.delete(job.it.path); continue; }
    thumbJobs.delete(job.it.path);
    thumbActive++;
    (async () => {
      let blob = null;
      try { blob = await makeThumb(fileUrl(job.it.path), kind(job.it.name)); } catch { blob = null; }
      await storeThumb(job.it.path, blob);
      const url = blob ? URL.createObjectURL(blob) : null;
      thumbMade.set(job.it.path, url);
      job.it.thumb = blob ? 1 : -1;
      for (const cb of job.cbs) cb(url);
      if (thumbBatch && job.batch) { thumbBatch.done++; if (!blob) thumbBatch.failed++; thumbBatch.tick(); }
      thumbActive--;
      pumpThumbs();
    })();
  }
  if (thumbBatch && !thumbJobs.size && !thumbActive) { const b = thumbBatch; thumbBatch = null; b.tick(true); }
}

const thumbSeen = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { thumbSeen.unobserve(e.target); e.target.loadThumb(); }
}, { root: main, rootMargin: "400px" });

/** A square showing the file's thumbnail (made on first sight), or its icon. */
function thumbBox(it) {
  const k = it.dir ? "folder" : kind(it.name);
  const box = h("span", { class: "thumb" }, icon(k === "pdf" ? "text" : k, it.dir ? "folder" : ""));
  if (!isMedia(it) || it.thumb === -1) return box;
  box.loadThumb = () => {
    const show = (url) => {
      if (!url) return;
      const img = h("img", { alt: "", draggable: "false" });
      img.onload = () => { box.replaceChildren(...[img, k === "video" && h("span", { class: "play" }, icon("play"))].filter(Boolean)); box.classList.add("has"); };
      img.src = url;
    };
    if (thumbMade.get(it.path)) show(thumbMade.get(it.path));
    else if (it.thumb === 1) show(thumbUrl(it));
    else wantThumb(it, show, true);
  };
  thumbSeen.observe(box);
  return box;
}

// ------------------------------------------------------------------ gallery
// Every photo and video under a folder (the whole drive by default), newest first, month by month.
const fmtMonth = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
let gal = null;

async function renderGallery() {
  const path = route.path;
  const g = gal = { path, items: [], total: null, loading: false, month: "", grid: null };
  const body = h("div", { class: "gallery" });
  const more = h("div", { class: "g-more" });
  const info = h("span", { class: "muted g-count" });
  const prog = h("div", { class: "g-progress", hidden: true });
  const all = h("button", { class: "btn ghost", onclick: () => thumbsForAll(path, prog) }, "Create all thumbnails");
  page(h("div", { class: "bar" },
         path === "/" ? h("h1", { style: "margin:0" }, "Gallery") : crumbs(path, "Gallery", (p) => "#/gallery" + enc(p)),
         info,
         h("div", { class: "actions" }, path !== "/" && h("a", { class: "btn ghost", href: "#/files" + enc(path) }, "Open folder"), all)),
       prog, body, more);
  if (thumbBatch) thumbBatch.show(prog);

  async function load() {
    if (g.loading || gal !== g || (g.total !== null && g.items.length >= g.total)) return;
    g.loading = true;
    const data = await api("/api/media" + q({ path, offset: g.items.length, limit: 300 })).catch((e) => { toast(e.message); return null; });
    g.loading = false;
    if (!data || gal !== g) return;
    g.total = data.total;
    info.textContent = data.total ? plural(data.total, "photo and video", "photos and videos") : "";
    if (!data.total) {
      body.replaceChildren(h("div", { class: "empty" }, h("b", {}, "No photos or videos here"), "Pictures and videos you put on the drive show up here."));
      return;
    }
    for (const it of data.items) {
      const m = fmtMonth.format(new Date(it.mtime * 1000));
      if (m !== g.month) {
        g.month = m;
        g.grid = h("div", { class: "g-grid" });
        body.append(h("h2", { class: "g-month" }, m), g.grid);
      }
      const i = g.items.push(it) - 1;
      const tile = h("button", { class: "g-tile", title: it.name, onclick: () => lightbox(g, i) }, thumbBox(it));
      bindMenu(tile, () => itemMenu(it), it.name, `${size(it.size)} · ${when(it.mtime)}`);
      g.grid.append(tile);
    }
    if (g.items.length < g.total) requestAnimationFrame(() => { if (more.getBoundingClientRect().top < innerHeight + 600) load(); });
  }
  g.load = load;
  new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) load(); }, { root: main, rootMargin: "600px" }).observe(more);
  await load();
}

async function thumbsForAll(path, prog) {
  if (thumbBatch) { thumbBatch.show(prog); return; }
  let todo = [], bytes = 0;
  prog.hidden = false;
  prog.textContent = "Looking for photos and videos without a thumbnail…";
  for (let offset = 0; ; offset += 1000) {
    const data = await api("/api/media" + q({ path, offset, limit: 1000 })).catch(() => null);
    if (!data) break;
    for (const it of data.items) if (it.thumb == null && !thumbMade.has(it.path)) { todo.push(it); if (kind(it.name) === "image") bytes += it.size; }
    if (offset + data.items.length >= data.total || !data.items.length) break;
  }
  prog.hidden = true;
  if (!todo.length) { toast("Every photo and video here already has a thumbnail"); return; }
  const ok = await ask({ title: `Create ${plural(todo.length, "thumbnail")}?`, ok: "Create",
                         text: `Each photo is read once from Discord (${size(bytes)} in total; videos only a small part), so this takes a while. `
                             + "It runs while this page stays open, and you can keep using the dashboard. Thumbnails are stored encrypted on the computer running the drive." });
  if (!ok) return;
  const b = thumbBatch = { todo: todo.length, done: 0, failed: 0, stop: false, el: null,
    show(el) { b.el = el; b.tick(); },
    tick(finished) {
      const el = b.el && b.el.isConnected ? b.el : document.querySelector(".g-progress");
      if (finished) { if (el) el.hidden = true; toast(b.stop ? "Stopped. The thumbnails made so far are kept."
                                               : `${plural(b.done - b.failed, "thumbnail")} created` + (b.failed ? `; ${plural(b.failed, "file")} can't be shown by this browser` : ""));
                      return; }
      if (!el) return;
      el.hidden = false;
      el.replaceChildren(h("span", {}, b.stop ? "Stopping…" : `Creating thumbnails: ${b.done.toLocaleString()} of ${b.todo.toLocaleString()}`),
        h("div", { class: "meter" }, h("i", { style: `width:${((b.done / b.todo) * 100).toFixed(1)}%` })),
        !b.stop && h("button", { class: "btn ghost small", onclick: () => { b.stop = true; b.tick(); pumpThumbs(); } }, "Stop"));
    } };
  b.show(prog);
  for (const it of todo) wantThumb(it, null, false, true);
}

/** Full-screen viewer over the gallery: arrows (or the keyboard) go to the next and previous one. */
function lightbox(g, start) {
  let i = start;
  const stage = h("div", { class: "lb-stage" });
  const title = h("div", { class: "lb-title" });
  const close = () => { document.removeEventListener("keydown", keys, true); stage.replaceChildren(); box.remove(); };
  const step = (d) => {
    const n = i + d;
    if (n < 0 || n >= g.items.length) return;
    i = n;
    show();
    if (i > g.items.length - 20 && g.load) g.load();
  };
  const keys = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
  };
  const prev = h("button", { class: "lb-nav prev", "aria-label": "Previous", onclick: () => step(-1) }, icon("back"));
  const next = h("button", { class: "lb-nav next", "aria-label": "Next", onclick: () => step(1) }, icon("back"));
  const dl = h("a", { class: "icon-btn", title: "Download", "aria-label": "Download" }, icon("download"));
  const box = h("div", { class: "lightbox", role: "dialog", "aria-label": "Viewer" },
    h("div", { class: "lb-top" }, title,
      dl,
      h("button", { class: "icon-btn", title: "Details, sharing, versions", "aria-label": "Details", onclick: () => { const it = g.items[i]; close(); openItem(it); } }, icon("info")),
      h("button", { class: "icon-btn", title: "Close", "aria-label": "Close", onclick: close }, icon("close"))),
    stage, prev, next);
  stage.addEventListener("click", (e) => { if (e.target === stage) close(); });
  function show() {
    const it = g.items[i];
    title.replaceChildren(h("div", { class: "t" }, it.name),
      h("div", { class: "s" }, `${fmtFull.format(new Date(it.mtime * 1000))} · ${size(it.size)} · ${parent(it.path)}`));
    dl.href = fileUrl(it.path, true);
    dl.setAttribute("download", it.name);
    stage.replaceChildren(kind(it.name) === "video"
      ? h("video", { src: fileUrl(it.path), controls: true, autoplay: true, playsinline: true })
      : h("img", { src: fileUrl(it.path), alt: it.name }));
    prev.hidden = i === 0;
    next.hidden = i >= g.items.length - 1;
  }
  document.addEventListener("keydown", keys, true);
  window.addEventListener("hashchange", close, { once: true });
  document.body.append(box);
  show();
}

// ------------------------------------------------------------------ sync folders
// Folders on the computer running the drive, kept in step with folders on the drive. The jobs and
// the copying live in the drive process (sync.py); this page shows them and changes them.
let syncTimer = null;
let syncData = null;
const ARROW = { up: "→", down: "←", both: "⇄" };

function drivePathShown(p) {
  if (!syncData) return p;
  return syncData.windows ? syncData.mount.replace(/\\$/, "") + p.replace(/\//g, "\\") : syncData.mount.replace(/\/$/, "") + p;
}

function syncSummary(r, j) {
  if (j && j.direction !== "down" && r.files === 0) return "The folder on this computer is empty, so there is nothing to copy";
  const parts = [[r.up, "copied to the drive"], [r.down, "copied here"], [r.deleted_remote, "deleted on the drive"],
                 [r.deleted_local, "removed here"], [r.moved, "moved off this computer"], [r.conflicts, "kept twice (conflict)"],
                 [r.errors, "problem"]].filter(([n]) => n);
  return parts.length ? parts.map(([n, t]) => t === "problem" ? plural(n, "problem") : `${Number(n).toLocaleString()} ${t}`).join(", ")
                      : `Everything was already in step (${plural(r.files || 0, "file")})`;
}

function syncNext(j) {
  const s = j.status;
  if (!j.enabled) return "";
  if (j.trigger === "manual") return "Runs when you start it";
  if (j.trigger === "live") return s.watching ? "Watching for changes" : "Checking every few seconds";
  if (!s.next) return "";
  const sec = s.next - Date.now() / 1000;
  return sec <= 30 ? "Next run in a moment" : `Next run ${sec < 3600 ? "in " + Math.round(sec / 60) + " min" : when(s.next)}`;
}

function syncCard(j) {
  const s = j.status, p = s.progress, last = s.last || {};
  const running = s.state === "running";
  const state = !j.enabled ? ["Paused", ""] : running ? ["Syncing", "busy"] : s.state === "error" ? ["Problem", "warn"]
              : s.last_run ? ["Up to date", "ok"] : ["Waiting", ""];
  const menu = () => [
    running ? { label: "Stop", icon: "close", run: () => syncAction("stop", j, "Stopping") }
            : { label: "Sync now", icon: "refresh", run: () => syncAction("run", j, "Syncing") },
    j.mode === "backup" && !running && { label: "Copy everything again", icon: "upload", run: async () => {
      if (await ask({ title: "Copy everything again?", ok: "Copy again",
                      text: "The backup normally copies each file once, so copies you moved, renamed or deleted on the drive are not put back. This copies every file that is not in the drive folder again." }))
        syncAction("run", j, "Syncing", { again: true });
    } },
    { label: j.enabled ? "Pause" : "Resume", icon: j.enabled ? "minus" : "restore",
      run: () => syncAction("pause", j, j.enabled ? "Paused" : "Resumed", { paused: j.enabled }) },
    { label: "Open the drive folder", icon: "enter", run: () => go("#/files" + enc(j.remote)) },
    syncData.can_edit && { label: "Change…", icon: "settings", run: () => syncForm(j) },
    (s.history || []).length && { label: "Recent runs", icon: "history", run: () => syncHistory(j) },
    syncData.can_edit && "-",
    syncData.can_edit && { label: "Stop syncing this folder", icon: "trash", danger: true, run: async () => {
      if (await ask({ title: `Stop syncing “${j.name}”?`, ok: "Stop syncing", danger: true,
                      text: "Nothing is deleted: the files stay on this computer and on the drive." })
          && await attempt(() => api("/api/sync/remove", { id: j.id }), "No longer synced")) renderSync();
    } },
  ];
  let line;
  if (running && p) {
    const pct = p.total_bytes ? Math.min(100, (p.bytes / p.total_bytes) * 100) : p.total ? (p.done / p.total) * 100 : 0;
    line = [h("div", { class: "sync-line" }, p.total ? `${Number(p.done).toLocaleString()} of ${plural(p.total, "file")}` : "Comparing folders…",
              p.total_bytes ? ` · ${size(p.bytes)} of ${size(p.total_bytes)}` : "",
              p.file && h("span", { class: "sync-file" }, ` · ${p.action === "down" ? "←" : "→"} ${p.file}`)),
            h("div", { class: "meter" }, h("i", { style: `width:${pct.toFixed(1)}%` }))];
  } else if (s.error) {
    line = h("div", { class: "sync-line warn" }, s.error);
  } else if (s.last_run) {
    line = h("div", { class: "sync-line" }, `${ago(s.last_run)}: ${syncSummary(last, j)}`,
             last.waiting ? ` · ${plural(last.waiting, "file")} still uploading to Discord` : "",
             last.busy ? ` · ${plural(last.busy, "file")} in use, next pass` : "",
             last.kept_away ? ` · ${plural(last.kept_away, "file")} moved or deleted on the drive, not copied again` : "");
  } else {
    line = h("div", { class: "sync-line" }, "Not run yet");
  }
  const card = h("div", { class: "sync-card" + (j.enabled ? "" : " off") },
    h("div", { class: "sync-head" },
      h("span", { class: "sync-dir", title: j.label }, ARROW[j.direction]),
      h("div", { class: "sync-title" },
        h("div", { class: "t" }, h("span", { class: "label" }, j.name), h("span", { class: "tag" }, j.label), h("span", { class: "tag" }, j.when)),
        h("div", { class: "sync-paths" },
          h("code", { title: j.local }, j.local), h("span", { class: "a" }, ARROW[j.direction]),
          h("a", { href: "#/files" + enc(j.remote), title: "Open on the drive" }, h("code", {}, drivePathShown(j.remote))))),
      h("span", { class: "sync-state" }, h("span", { class: "dot " + state[1] }), state[0]),
      running ? h("button", { class: "btn ghost small", onclick: () => syncAction("stop", j, "Stopping") }, "Stop")
              : h("button", { class: "btn ghost small", onclick: () => syncAction("run", j, "Syncing") }, "Sync now"),
      menuButton(menu, j.name, j.label)),
    line,
    h("div", { class: "sync-foot" }, syncNext(j)),
    (s.problems || []).length ? h("details", { class: "sync-problems" },
      h("summary", {}, plural(s.problems.length, "note")), h("ul", {}, s.problems.map((t) => h("li", {}, t)))) : null);
  bindMenu(card, menu, j.name, j.label, (e) => !!e.target.closest("button, a, summary"));
  return card;
}

async function syncAction(what, j, ok, extra = {}) {
  const r = await attempt(() => api("/api/sync/" + what, { id: j.id, ...extra }));
  if (r) { toast(r.queued ? "Starts after the folder syncing now" : ok); setTimeout(renderSync, 400); }
}

function syncHistory(j) {
  modal(`Recent runs · ${j.name}`, h("div", { class: "list rows-simple" }, (j.status.history || []).map((r) =>
    h("div", { class: "row", style: "grid-template-columns:150px minmax(0,1fr)" },
      h("span", { class: "date", style: "text-align:left" }, fmtFull.format(new Date(r.at * 1000))),
      h("span", { class: r.error ? "warn-text" : "" }, r.error || syncSummary(r) + (r.bytes ? ` · ${size(r.bytes)}` : ""))))));
}

async function renderSync(quiet) {
  clearTimeout(syncTimer);
  if (route.name !== "sync") return;
  const data = await api("/api/sync").catch((e) => { if (!quiet) toast(e.message); return null; });
  if (route.name !== "sync") return;
  if (data) syncData = data;
  if (!syncData) { page(h("h1", {}, "Sync"), h("p", { class: "lede" }, "Connecting to the drive…")); return; }
  const d = syncData;
  if (!(quiet && (ctxEl || document.querySelector("dialog.modal[open]")))) {
    keepScroll = !!quiet;
    try {
      page(h("div", { class: "bar" }, h("h1", { style: "margin:0" }, "Sync"),
             d.can_edit && h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => syncForm(null) }, icon("plus"), "Add a folder"))),
           h("p", { class: "lede" }, `Folders on ${d.host} kept in step with folders on the drive: backups, mirrors and two-way sync. `
             + "Copies keep each file's date, so nothing is copied twice, and files still being written wait until they are finished."),
           !d.can_edit && h("div", { class: "notice" }, `Adding or changing folders works on ${d.host} itself (or with `, h("code", {}, "sync add"),
             " in its terminal), because it reads and writes files outside the drive. Syncing now and pausing work from here."),
           d.jobs.length ? h("div", { class: "sync-list" }, d.jobs.map(syncCard))
             : h("div", { class: "empty" }, h("b", {}, "No folders synced yet"),
                 d.can_edit ? "Add one, e.g. your Downloads folder, and it is backed up to the drive by itself." : "Add one on the computer running the drive."),
           d.local && h("div", { class: "settings", style: "margin-top:40px" }, h("div", { class: "set-row" },
             h("div", { class: "set-text" }, h("div", { class: "t" }, "Let other devices choose folders here"),
               h("div", { class: "s" }, `Off: only ${d.host} itself can add or change synced folders. On: anyone signed in to this dashboard from elsewhere can make it copy any folder of this computer.`)),
             h("div", { class: "set-ctl" }, switchEl(d.remote_edit, async (on) => {
               if (await attempt(() => api("/api/sync/remote-edit", { on }), on ? "Other devices can now change synced folders" : "Only this computer can change synced folders")) d.remote_edit = on;
             }, "Let other devices choose folders here")))));
    } finally { keepScroll = false; }
  }
  syncTimer = setTimeout(() => renderSync(true), d.running ? 1500 : 5000);
}

/** Choose a folder on the computer running the drive. */
function pickLocalFolder(start) {
  return new Promise((resolve) => {
    let cur = start || "", chosen = null;
    const list = h("div", { class: "picker-list" });
    const where = h("input", { class: "picker-path", spellcheck: "false", placeholder: "Type a path, or pick below",
                               onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); load(where.value.trim()); } } });
    const here = h("button", { class: "btn", onclick: () => { chosen = cur; m.close(); } }, "Use this folder");
    const m = modal("Folder on " + (syncData ? syncData.host : "this computer"), where, list, h("div", { class: "modal-actions" }, here));
    m.dlg.addEventListener("close", () => resolve(chosen));
    async function load(path) {
      const data = await api("/api/sync/browse" + q({ path })).catch((e) => { toast(e.message); return null; });
      if (!data) return;
      cur = data.path;
      where.value = data.path;
      here.disabled = !data.path;
      const rows = [];
      if (data.parent != null) rows.push(h("button", { class: "picker-row", onclick: () => load(data.parent) }, icon("back"), h("span", {}, "Up one level")));
      for (const d of data.dirs) rows.push(h("button", { class: "picker-row", onclick: () => load(d.path) }, icon("folder", "folder"), h("span", {}, d.name), icon("enter")));
      if (!data.dirs.length) rows.push(h("div", { class: "muted picker-empty" }, "No folders in here"));
      list.replaceChildren(...rows);
    }
    load(cur);
  });
}

function syncForm(j) {
  const d = syncData;
  const v = j ? { ...j, exclude: (j.exclude || []).join(", ") }
              : { name: "", local: "", remote: "", mode: "backup", trigger: "live", every: 60, at: "03:00", exclude: "" };
  const inp = (key, attrs = {}) => h("input", { value: v[key] ?? "", spellcheck: "false", autocomplete: "off", ...attrs,
                                                 oninput: (e) => { v[key] = e.target.value; } });
  const local = inp("local", { placeholder: d.windows ? "C:\\Users\\you\\Downloads" : "/home/you/Documents" });
  const remote = inp("remote", { placeholder: d.windows ? `${d.mount}\\Downloads` : "/Downloads" });
  if (v.remote) remote.value = drivePathShown(v.remote);
  const pickRow = (input, onPick) => h("div", { class: "pick-row" }, input, h("button", { class: "btn ghost", type: "button", onclick: onPick }, "Browse…"));
  const modes = h("div", { class: "modes", role: "radiogroup" }, d.modes.map((mo) => {
    const b = h("button", { type: "button", role: "radio", class: "mode", "aria-checked": String(v.mode === mo.id),
                            onclick: () => { v.mode = mo.id; for (const x of modes.children) x.setAttribute("aria-checked", String(x === b)); } },
      h("span", { class: "mode-a" }, ARROW[mo.direction]),
      h("span", {}, h("span", { class: "t" }, mo.label), h("span", { class: "s" }, mo.text)));
    return b;
  }));
  const unit = h("select", {}, h("option", { value: "1" }, "minutes"), h("option", { value: "60", selected: v.every % 60 === 0 }, "hours"));
  const every = h("input", { type: "number", min: "1", value: String(v.every % 60 === 0 ? v.every / 60 : v.every), class: "every" });
  const at = h("input", { type: "time", value: v.at, class: "at" });
  const extra = h("div", { class: "when-extra" });
  const showExtra = () => extra.replaceChildren(
    v.trigger === "interval" ? h("div", { class: "pick-row" }, h("span", { class: "muted" }, "Every"), every, unit) :
    v.trigger === "daily" ? h("div", { class: "pick-row" }, h("span", { class: "muted" }, "At"), at) :
    h("div", { class: "hint" }, v.trigger === "live" ? "Starts a few seconds after something changes; a full check also runs every so often."
                                                     : "Only when you press Sync now (or run it from the terminal)."));
  const seg = h("div", { class: "seg", role: "radiogroup" }, d.triggers.map((t) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v.trigger === t.id), onclick: (e) => {
      v.trigger = t.id; for (const b of seg.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); showExtra();
    } }, { live: "Live", interval: "Every…", daily: "Daily", manual: "Manual" }[t.id])));
  showExtra();
  const save = h("button", { class: "btn", type: "button", onclick: async () => {
    const job = { ...v, local: local.value.trim(), remote: remote.value.trim(), exclude: v.exclude,
                  every: Math.max(1, Math.round(Number(every.value || 1) * Number(unit.value))), at: at.value || "03:00" };
    if (j) job.id = j.id;
    save.disabled = true;
    const r = await attempt(() => api("/api/sync/save", { job }));
    save.disabled = false;
    if (r) { m.close(); toast(j ? "Saved" : "Added. The first sync starts now."); renderSync(); }
  } }, j ? "Save" : "Add and sync");
  const m = modal(j ? `Change “${j.name}”` : "Sync a folder",
    field(`Folder on ${d.host}`, pickRow(local, async () => { const p = await pickLocalFolder(local.value.trim() || d.home); if (p) { local.value = p; v.local = p; } })),
    field("Folder on the drive", pickRow(remote, async () => {
      const p = await pickFolder("Folder on the drive", (remote.value.trim().replace(/^[A-Za-z]:/, "").replace(/\\/g, "/")) || "/", [], "Use this folder");
      if (p) { remote.value = drivePathShown(p); v.remote = p; }
    }), "It is created if it isn't there yet."),
    field("What to do", modes),
    field("When", h("div", {}, seg, extra)),
    field("Skip these", inp("exclude", { placeholder: "e.g. *.iso, Temp/" }),
          `Names or paths inside the folder, separated by commas. Always skipped: unfinished downloads and temporary files (${d.default_exclude.slice(0, 5).join(", ")}…).`),
    field("Name", inp("name", { placeholder: "Optional, e.g. Downloads" })),
    h("div", { class: "modal-actions" }, save));
  m.dlg.classList.add("wide");
}

// ------------------------------------------------------------------ bookmarks
// Web addresses saved with a preview. Kept in /Bookmarks on the drive (the list and the pictures), so
// they are encrypted, synced and versioned, and showing them loads nothing from the sites themselves.
let marks = [];
let markTag = "";
let markFilter = "";
const markPending = new Map();      // temporary id -> address being read
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };
const looksLikeLink = (t) => /^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(\/\S*)?$/i.test(t.trim()) || /^https?:\/\/\S+$/i.test(t.trim());

function hue(text) {
  let n = 0;
  for (const c of text) n = (n * 31 + c.charCodeAt(0)) % 360;
  return n;
}

function markCard(b) {
  const host = hostOf(b.url);
  const v = Math.floor(b.added || 0);
  const banner = h("div", { class: "bm-banner" + (b.image ? "" : " plain"), style: b.image ? null
      : `background:linear-gradient(135deg, hsl(${hue(host)} 55% 42%), hsl(${(hue(host) + 48) % 360} 60% 30%))` },
    b.image ? h("img", { src: fileUrl(b.image) + "&v=" + v, alt: "", loading: "lazy", onerror: (e) => { e.target.remove(); banner.classList.add("plain"); banner.style.cssText = `background:linear-gradient(135deg, hsl(${hue(host)} 55% 42%), hsl(${(hue(host) + 48) % 360} 60% 30%))`; banner.prepend(h("span", { class: "bm-letter" }, host.slice(0, 1).toUpperCase())); } })
            : b.icon ? h("span", { class: "bm-badge" }, h("img", { src: fileUrl(b.icon) + "&v=" + v, alt: "", loading: "lazy",
                         onerror: (e) => e.target.parentNode.replaceWith(h("span", { class: "bm-letter" }, host.slice(0, 1).toUpperCase())) }))
            : h("span", { class: "bm-letter" }, host.slice(0, 1).toUpperCase()));
  const menu = () => [
    { label: "Open", icon: "open", run: () => window.open(b.url, "_blank", "noopener,noreferrer") },
    { label: "Copy the link", icon: "link", run: async () => { try { await navigator.clipboard.writeText(b.url); toast("Link copied"); } catch { await ask({ title: "Link", value: b.url, ok: "Done" }); } } },
    { label: b.pinned ? "Unpin" : "Pin to the top", icon: "star", run: () => pinMark(b, !b.pinned) },
    { label: "Edit…", icon: "rename", run: () => markEdit(b) },
    { label: "Read the preview again", icon: "refresh", run: async () => {
      toast("Reading the page…");
      const r = await attempt(() => api("/api/bookmarks/refresh", { id: b.id }), "Preview updated");
      if (r) { Object.assign(b, r.bookmark, { added: Date.now() / 1000 }); drawMarks(); }
    } },
    "-",
    { label: "Delete", icon: "trash", danger: true, run: async () => {
      if (await attempt(() => api("/api/bookmarks/delete", { ids: [b.id] }), "Bookmark deleted")) { marks = marks.filter((x) => x.id !== b.id); drawMarks(); }
    } },
  ];
  const card = h("a", { class: "bm-card", href: b.url, target: "_blank", rel: "noopener noreferrer", title: b.url },
    banner,
    h("div", { class: "bm-body" },
      h("div", { class: "bm-site" },
        b.icon ? h("img", { class: "bm-icon", src: fileUrl(b.icon) + "&v=" + v, alt: "", loading: "lazy", onerror: (e) => e.target.replaceWith(icon("globe", "bm-icon")) })
               : icon("globe", "bm-icon"),
        h("span", { class: "s" }, b.site || host), h("span", { class: "d" }, ago(b.added))),
      h("div", { class: "bm-title" }, b.title || host),
      b.description && h("div", { class: "bm-desc" }, b.description),
      b.note && h("div", { class: "bm-note" }, b.note),
      (b.tags || []).length ? h("div", { class: "bm-tags" }, b.tags.map((t) =>
        h("button", { class: "bm-tag", type: "button", onclick: (e) => { e.preventDefault(); e.stopPropagation(); markTag = markTag === t ? "" : t; drawMarks(); } }, t))) : null),
    h("button", { class: "icon-btn bm-pin" + (b.pinned ? " on" : ""), "aria-label": b.pinned ? "Unpin" : "Pin to the top",
                  "aria-pressed": String(!!b.pinned), title: b.pinned ? "Pinned: click to unpin" : "Pin to the top",
                  onclick: (e) => { e.preventDefault(); e.stopPropagation(); pinMark(b, !b.pinned); } }, icon("star")),
    h("button", { class: "icon-btn bm-more", "aria-label": `Actions for ${b.title || host}`, title: "Actions",
                  onclick: (e) => { e.preventDefault(); e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); openMenu(menu(), r.right - 220, r.bottom + 4, b.title || host, host); } }, icon("more")));
  bindMenu(card, menu, b.title || host, host, (e) => !!e.target.closest(".bm-more, .bm-pin"));
  return card;
}

function drawMarks() {
  const grid = $("#bm-grid");
  if (!grid) return;
  const tags = new Map();
  for (const b of marks) for (const t of b.tags || []) tags.set(t, (tags.get(t) || 0) + 1);
  if (markTag && !tags.has(markTag)) markTag = "";
  const f = markFilter.toLowerCase();
  const shown = marks.filter((b) => (!markTag || (b.tags || []).includes(markTag))
    && (!f || [b.title, b.description, b.site, b.url, b.note, ...(b.tags || [])].some((x) => (x || "").toLowerCase().includes(f))));
  $("#bm-tags").replaceChildren(...(tags.size ? [h("button", { class: "bm-chip" + (markTag ? "" : " on"), onclick: () => { markTag = ""; drawMarks(); } }, "All", h("i", {}, marks.length)),
    ...[...tags].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([t, n]) =>
      h("button", { class: "bm-chip" + (markTag === t ? " on" : ""), onclick: () => { markTag = markTag === t ? "" : t; drawMarks(); } }, t, h("i", {}, n)))] : []));
  const loading = [...markPending.values()].map((url) => h("div", { class: "bm-card loading" },
    h("div", { class: "bm-banner" }), h("div", { class: "bm-body" }, h("div", { class: "bm-site" }, h("span", { class: "s" }, hostOf(url))),
      h("div", { class: "bm-title" }, "Reading the page…"), h("div", { class: "bm-line" }), h("div", { class: "bm-line short" }))));
  const pinned = shown.filter((b) => b.pinned).sort((a, b) => b.pinned - a.pinned);
  const rest = shown.filter((b) => !b.pinned);
  const cards = (list, extra = []) => h("div", { class: "bm-grid" }, ...extra, ...list.map(markCard));
  grid.replaceChildren(...(pinned.length
    ? [h("div", { class: "bm-head" }, icon("star"), "Pinned"), cards(pinned),
       (loading.length || rest.length) ? h("div", { class: "bm-head" }, "Everything else") : null, cards(rest, loading)]
    : [cards(rest, loading)]).filter(Boolean));
  $("#bm-empty").hidden = loading.length + shown.length > 0;
  $("#bm-empty").replaceChildren(...(marks.length ? [h("b", {}, "Nothing matches"), "Try another word or tag."]
    : [h("b", {}, "No bookmarks yet"), "Paste a link above, or press Ctrl+V anywhere on this page."]));
  $("#bm-count").textContent = marks.length ? plural(marks.length, "bookmark") : "";
}

async function pinMark(b, on) {
  const r = await attempt(() => api("/api/bookmarks/save", { id: b.id, pinned: on }), on ? "Pinned to the top" : "Unpinned");
  if (r) { Object.assign(b, r.bookmark); if (!on) delete b.pinned; drawMarks(); }
}

async function addMark(text) {
  const urls = text.split(/\s+/).map((t) => t.trim()).filter(looksLikeLink).slice(0, 20);
  if (!urls.length) return toast("That doesn't look like a web address");
  await Promise.all(urls.map(async (url) => {
    const key = Math.random().toString(36).slice(2);
    markPending.set(key, url);
    drawMarks();
    const r = await api("/api/bookmarks/add", { url, tags: markTag ? [markTag] : [] }).catch((e) => { toast(e.message); return null; });
    markPending.delete(key);
    if (r) {
      marks = [r.bookmark, ...marks.filter((b) => b.id !== r.bookmark.id)];
      if (r.existed) toast("Already saved: moved to the front");
      else if (!r.preview) toast("Saved. The page gave no preview.");
    }
    drawMarks();
  }));
}

function markEdit(b) {
  const title = h("input", { value: b.title || "", spellcheck: "true" });
  const desc = h("textarea", { class: "bm-area", rows: "3" });
  desc.value = b.description || "";
  const tags = h("input", { value: (b.tags || []).join(", "), placeholder: "e.g. recipes, to read", spellcheck: "false" });
  const note = h("textarea", { class: "bm-area", rows: "3", placeholder: "Why you saved it, what to look at…" });
  note.value = b.note || "";
  const save = h("button", { class: "btn", type: "button", onclick: async () => {
    save.disabled = true;
    const r = await attempt(() => api("/api/bookmarks/save", { id: b.id, title: title.value, description: desc.value, note: note.value,
                                                             tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean) }), "Saved");
    save.disabled = false;
    if (r) { Object.assign(b, r.bookmark); m.close(); drawMarks(); }
  } }, "Save");
  const m = modal("Edit bookmark", h("p", { class: "muted bm-url" }, b.url),
    field("Title", title), field("Description", desc), field("Tags", tags, "Separate with commas. Click a tag on a card to show only those."),
    field("Note", note), h("div", { class: "modal-actions" }, save));
  title.focus();
}

async function renderBookmarks() {
  const input = h("input", { id: "bm-input", type: "url", placeholder: "Paste a link to save it", spellcheck: "false", autocomplete: "off",
                             onkeydown: (e) => { if (e.key === "Enter" && input.value.trim()) { const v = input.value; input.value = ""; addMark(v); } },
                             onpaste: (e) => { const t = (e.clipboardData.getData("text") || "").trim(); if (looksLikeLink(t) || /\s/.test(t)) { e.preventDefault(); input.value = ""; addMark(t); } } });
  const filter = h("input", { class: "bm-filter", type: "search", placeholder: "Filter", value: markFilter, spellcheck: "false",
                              oninput: (e) => { markFilter = e.target.value.trim(); drawMarks(); } });
  page(h("div", { class: "bar" }, h("h1", { style: "margin:0" }, "Bookmarks"), h("span", { class: "muted g-count", id: "bm-count" }),
         h("div", { class: "actions" }, filter)),
       h("div", { class: "bm-add" }, icon("link"), input,
         h("button", { class: "btn", onclick: () => { if (input.value.trim()) { const v = input.value; input.value = ""; addMark(v); } else input.focus(); } }, "Save")),
       h("div", { class: "bm-chips", id: "bm-tags" }),
       h("div", { id: "bm-grid" }),
       h("div", { class: "empty", id: "bm-empty", hidden: true }));
  const d = await api("/api/bookmarks").catch((e) => { toast(e.message); return null; });
  if (route.name !== "bookmarks") return;
  if (d && d.locked) {
    $("#bm-empty").hidden = false;
    $("#bm-empty").replaceChildren(h("b", {}, "Bookmarks are locked"), `${d.folder} is hidden or password-locked here. Unlock it with the padlock at the top.`);
    return;
  }
  marks = d ? d.items : [];
  drawMarks();
}

// Ctrl+V anywhere on the Bookmarks page saves the copied link.
document.addEventListener("paste", (e) => {
  if (route.name !== "bookmarks" || (e.target.closest && e.target.closest("input, textarea, [contenteditable]"))) return;
  const t = ((e.clipboardData && e.clipboardData.getData("text")) || "").trim();
  if (t && t.split(/\s+/).some(looksLikeLink)) { e.preventDefault(); addMark(t); }
});

// ------------------------------------------------------------------ signing in: passkeys, phrase, password
const b64uBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
const b64uText = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function addPasskey(redraw) {
  if (!window.PublicKeyCredential) return toast("This browser doesn't support passkeys");
  const name = await ask({ title: "Add a passkey", value: /Windows/.test(navigator.userAgent) ? "Windows Hello" : /iPhone|iPad|Mac/.test(navigator.userAgent) ? "Apple device" : "This device",
                           ok: "Continue", text: "Give it a name you'll recognise. Your browser then asks for your fingerprint, face, PIN, phone or security key." });
  if (!name) return;
  try {
    const o = await api("/api/signin/passkey/options", {});
    const cred = await navigator.credentials.create({ publicKey: {
      challenge: b64uBytes(o.challenge), rp: o.rp, user: { id: b64uBytes(o.user.id), name: o.user.name, displayName: o.user.displayName },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { residentKey: "preferred", userVerification: "preferred" }, attestation: "none", timeout: 120000,
      excludeCredentials: o.exclude.map((id) => ({ type: "public-key", id: b64uBytes(id) })) } });
    await api("/api/signin/passkey/register", { name, clientDataJSON: b64uText(cred.response.clientDataJSON),
                                              attestationObject: b64uText(cred.response.attestationObject) });
    toast("Passkey added. Use it the next time you sign in here.");
    redraw();
  } catch (e) {
    toast(e && e.name === "NotAllowedError" ? "Cancelled" : e && e.name === "InvalidStateError" ? "This device already has a passkey here" : (e.message || "The passkey could not be added"));
  }
}

function setPasswordDialog(info, redraw) {
  const user = h("input", { value: info.user || "", autocomplete: "username", spellcheck: "false" });
  const a = pwInput("New password (at least 8 characters)"), b = pwInput("The same password again");
  const go_ = h("button", { class: "btn", type: "button", onclick: async () => {
    if (a.value.length < 8) return toast("Use at least 8 characters");
    if (a.value !== b.value) return toast("The two passwords are different");
    if (await attempt(() => api("/api/signin/password", { user: user.value.trim(), password: a.value }), "Password set. Other browsers have to sign in again.")) { m.close(); redraw(); }
  } }, info.password ? "Change the password" : "Set the password");
  const m = modal(info.password ? "Change the password" : "Set a password",
    h("p", { class: "muted", style: "margin:0 0 16px" }, "A fallback for places where passkeys don't work (plain http on your home network) and for other apps over WebDAV."),
    field("User name", user), field("Password", a), field("Again", b), h("div", { class: "modal-actions" }, go_));
  a.focus();
}

async function showPhrase() {
  if (!await ask({ title: "Show the recovery phrase?", ok: "Show it",
                   text: "These 24 words are the key to your drive: anyone who sees them can read your files. Make sure nobody is looking at your screen." })) return;
  const r = await attempt(() => api("/api/signin/phrase", {}));
  if (!r) return;
  const words = r.phrase.split(" ");
  modal("Your recovery phrase",
    h("p", { class: "muted", style: "margin:0 0 14px" }, "Write these 24 words down in this order and keep them somewhere safe and offline. They set up a new device, sign in here, and are the only way back if every device is lost."),
    h("ol", { class: "phrase" }, words.map((w) => h("li", {}, w))),
    h("div", { class: "modal-actions" }, h("button", { class: "btn ghost", type: "button", onclick: async () => {
      try { await navigator.clipboard.writeText(r.phrase); toast("Copied. Paste it into a password manager, then clear your clipboard."); } catch { toast("Could not copy"); }
    } }, "Copy")));
}

async function signInSection(into) {
  const info = await api("/api/signin").catch(() => null);
  if (!info) return;
  const redraw = () => signInSection(into);
  const row = (t, s, ctl) => h("div", { class: "set-row" }, h("div", { class: "set-text" }, h("div", { class: "t" }, t), s && h("div", { class: "s" }, s)), h("div", { class: "set-ctl" }, ctl));
  const why = !window.PublicKeyCredential ? "This browser doesn't support passkeys."
            : !info.rp ? `Passkeys need a name in the address. On this computer open http://localhost:${location.port || 80}/ instead of the number address.`
            : !info.can_passkey ? "Browsers only allow passkeys on https (your domain through a reverse proxy) or on localhost." : "";
  into.replaceChildren(
    h("h2", {}, "Sign-in"),
    h("p", { class: "muted", style: "margin:0 0 12px" }, `Signed in as ${info.user || "you"}. Passkeys are the easy way in; your drive's recovery phrase always works as the backup.`),
    h("div", { class: "settings" },
      row("Passkeys", why || `Sign in with a fingerprint, face, PIN, phone or security key. A passkey works at the address it was made for (this one: ${info.rp}).`,
          h("button", { class: "btn small", disabled: !!why, onclick: () => addPasskey(redraw) }, "Add a passkey")),
      info.passkeys.map((p) => h("div", { class: "set-row sub" },
        h("div", { class: "set-text" }, h("div", { class: "t" }, icon("lock", "tag-ico"), " ", p.name, !p.here && h("span", { class: "tag", style: "margin-left:8px" }, p.rp)),
          h("div", { class: "s" }, `Added ${p.created ? when(p.created) : ""}${p.last ? " · last used " + ago(p.last) : " · not used yet"}${p.here ? "" : " · for another address"}`)),
        h("div", { class: "set-ctl" }, h("button", { class: "btn ghost small", onclick: async () => {
          if (await ask({ title: `Remove “${p.name}”?`, ok: "Remove", danger: true, text: "It can no longer sign in here. Delete it from your device's passkey list too." })
              && await attempt(() => api("/api/signin/passkey/remove", { id: p.id }), "Passkey removed")) redraw();
        } }, "Remove")))),
      info.phrase && row("Recovery phrase", info.local ? "The 24 words that are your drive's key. They sign in here and set up new devices."
                                                       : "The 24 words that are your drive's key. They are only shown on the computer that runs the drive (or with recovery-phrase in its terminal).",
          h("button", { class: "btn ghost small", disabled: !info.local, onclick: showPhrase }, "Show")),
      row("Password", info.password ? "A fallback for plain http and for other apps (WebDAV)." : "None set. Not needed with a passkey; other apps over WebDAV need one.",
          h("div", { style: "display:flex;gap:8px;justify-content:flex-end" },
            h("button", { class: "btn ghost small", onclick: () => setPasswordDialog(info, redraw) }, info.password ? "Change" : "Set"),
            info.password && h("button", { class: "btn ghost small", onclick: async () => {
              if (await ask({ title: "Remove the password?", ok: "Remove", danger: true, text: "Signing in is then by passkey or recovery phrase only. Other apps over WebDAV stop working." })
                  && await attempt(() => api("/api/signin/password", { password: "" }), "Password removed")) redraw();
            } }, "Remove"))),
      info.password && row("Allow signing in with the password", "Off: only passkeys and the recovery phrase open the dashboard. The password then only serves other apps (WebDAV).",
          switchEl(info.password_login, async (on) => { await attempt(() => api("/api/signin/password-login", { on }), on ? "Password sign-in is on" : "Password sign-in is off"); }, "Allow signing in with the password"))));
}

// ------------------------------------------------------------------ stars
async function setStar(it, on) {
  if (await attempt(() => api("/api/star", { path: it.path, on }), on ? "Starred" : "Star removed")) { it.starred = on; render(); }
}

// ------------------------------------------------------------------ home
const hello = () => { const hr = new Date().getHours(); return hr < 5 ? "Good night" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening"; };
const ACT = { add: ["upload", "added"], edit: ["rename", "changed"], mkdir: ["newfolder", "created"], move: ["move", "moved"],
              delete: ["trash", "deleted"], restore: ["restore", "restored"] };

function tile(it) {
  const el = h(it.dir ? "a" : "button", it.dir ? { class: "h-tile", href: "#/files" + enc(it.path), title: it.path }
                                               : { class: "h-tile", title: it.path, onclick: () => openItem(it) },
               thumbBox(it), h("span", { class: "n" }, it.name), h("span", { class: "w" }, parent(it.path)));
  bindMenu(el, () => itemMenu(it), it.name);
  return el;
}

function activityRow(a) {
  const [ico, verb] = ACT[a.kind] || ["info", a.kind];
  const name = base(a.path);
  const where = a.kind === "move" && a.src && parent(a.src) === parent(a.path) ? `renamed from ${base(a.src)}`
              : a.kind === "move" && a.src ? `moved from ${parent(a.src)}` : verb;
  const target = a.is_dir ? a.path : parent(a.path);
  return h(a.exists ? "a" : "div", a.exists ? { class: "row act", href: "#/files" + enc(target) } : { class: "row act gone" },
    h("span", { class: "name" }, icon(ico), h("span", { style: "min-width:0" },
      h("span", { class: "label" }, name, h("span", { class: "verb" }, " " + where)),
      h("div", { class: "where" }, `${parent(a.path)} · ${a.me ? "this device" : a.device_name}`))),
    h("span", { class: "size" }, a.is_dir || a.kind === "delete" ? "" : size(a.size)),
    h("span", { class: "date", title: fmtFull.format(new Date(a.at * 1000)) }, ago(a.at)), h("span"));
}

async function renderHome() {
  const d = await api("/api/home").catch(() => null);
  if (route.name !== "home") return;
  if (!d) { page(h("h1", {}, "Home"), h("p", { class: "lede" }, "Connecting to the drive…")); return; }
  const st = d.stats;
  const pct = st.chunks ? Math.floor((st.protected_chunks / st.chunks) * 1000) / 10 : 100;
  const card = (href, k, v, s) => h("a", { class: "stat link", href }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), h("div", { class: "s" }, s));
  page(
    h("div", { class: "bar" }, h("h1", { style: "margin:0" }, `${hello()}${status && status.user ? ", " + status.user : ""}`),
      h("div", { class: "actions" },
        h("button", { class: "btn ghost", onclick: openPalette }, "Find", h("kbd", {}, "Ctrl K")),
        h("button", { class: "btn ghost", onclick: () => saveFromLink("/") }, "Save from a link"),
        h("button", { class: "btn", onclick: () => { currentDir = "/"; $("#pick").click(); } }, "Upload"))),
    h("div", { class: "stats home-stats" },
      card("#/storage", "On the drive", size(st.bytes), `${plural(st.files, "file")} in ${plural(st.dirs, "folder")}`),
      card("#/health", "Protected", `${pct}%`, st.unsynced ? `${plural(st.unsynced, "file")} uploading` : "everything is in Discord"),
      card("#/sync", "Folder sync", d.sync.jobs ? plural(d.sync.jobs, "folder") : "Off",
           d.sync.problems ? plural(d.sync.problems, "problem") : d.sync.running ? "syncing now" : d.sync.jobs ? "up to date" : "back up a folder by itself"),
      card("#/shared", "Shared", plural(d.shares, "link"), `${plural(d.devices, "device")} on this drive`)),
    h("div", { class: "h-head" }, h("h2", {}, "Starred"), d.starred.length ? h("a", { class: "link", href: "#/starred" }, "All starred") : null),
    d.starred.length ? h("div", { class: "h-tiles" }, d.starred.map(tile))
      : h("p", { class: "muted" }, "Right-click a file or folder and choose Star to keep it here."),
    h("div", { class: "h-head" }, h("h2", {}, "Recent files"), h("a", { class: "link", href: "#/recent" }, "More")),
    d.recent.length ? h("div", { class: "list rows-plain" }, d.recent.map((it) => itemRow(it, { where: true })))
      : h("p", { class: "muted" }, "Nothing yet. Drop files anywhere to add them."),
    h("div", { class: "h-head" }, h("h2", {}, "Activity"), h("a", { class: "link", href: "#/history" }, "All activity")),
    d.history.length ? h("div", { class: "list" }, d.history.map(activityRow))
      : h("p", { class: "muted" }, "What is added, changed and deleted, on any of your devices, shows up here."),
    h("p", { class: "muted", style: "margin-top:40px;font-size:12px" }, `DiscordDrive ${d.version} on ${d.host} · `,
      h("button", { class: "link", onclick: whatsNew }, "What's new"), " · ",
      h("button", { class: "link", onclick: connectApps }, "Use it from other apps")));
}

async function renderStarred() {
  const d = await attempt(() => api("/api/starred"));
  if (!d || route.name !== "starred") return;
  page(h("h1", {}, "Starred"), h("p", { class: "lede" }, "Files and folders you starred, on any of your devices."),
       d.items.length ? h("div", { class: "list" }, listHead(), d.items.map((it) => itemRow(it, { where: true })))
         : h("div", { class: "empty" }, h("b", {}, "Nothing starred yet"), "Right-click a file or folder and choose Star."));
}

async function renderRecent() {
  const d = await attempt(() => api("/api/recent?limit=200"));
  if (!d || route.name !== "recent") return;
  page(h("h1", {}, "Recent"), h("p", { class: "lede" }, "The files changed most recently, anywhere on the drive."),
       d.items.length ? h("div", { class: "list" }, listHead(), d.items.map((it) => itemRow(it, { where: true })))
         : h("div", { class: "empty" }, h("b", {}, "Nothing here yet")));
}

async function renderHistory() {
  const list = h("div", { class: "list" });
  const more = h("button", { class: "btn ghost", style: "margin-top:16px" }, "Show older");
  let last = null;
  async function load() {
    const d = await attempt(() => api("/api/history" + q(last ? { limit: 100, before: last } : { limit: 100 })));
    if (!d || route.name !== "history") return;
    let day = list.dataset.day || "";
    for (const a of d.items) {
      const label = new Date(a.at * 1000).toDateString() === new Date().toDateString() ? "Today" : fmtYear.format(new Date(a.at * 1000));
      if (label !== day) { day = label; list.append(h("div", { class: "row head day" }, h("span", {}, label))); }
      list.append(activityRow(a));
      last = a.id;
    }
    list.dataset.day = day;
    more.hidden = d.items.length < 100;
    if (!list.children.length) list.replaceWith(h("div", { class: "empty" }, h("b", {}, "No activity yet"), "Changes made from now on are listed here."));
  }
  more.onclick = load;
  page(h("h1", {}, "Activity"), h("p", { class: "lede" }, "What was added, changed, moved and deleted, and on which device. Deleted files can be brought back under Deleted."),
       list, more);
  await load();
}

// ------------------------------------------------------------------ storage insights
const KIND_NAMES = { image: "Photos", video: "Videos", audio: "Music", text: "Documents and text", pdf: "PDFs", archive: "Archives", file: "Other files" };
const KIND_COLORS = { image: "#e5484d", video: "#3e63dd", audio: "#30a46c", text: "#f5a524", pdf: "#ab4aba", archive: "#8d8d8d", file: "#5b5bd6" };

async function renderStorage() {
  page(h("h1", {}, "Storage"), h("p", { class: "lede" }, "Working out what takes the space…"));
  const d = await attempt(() => api("/api/insights"));
  if (!d || route.name !== "storage") return;
  const total = Math.max(1, d.total.bytes);
  const saved = (d.saved.compressed_saved || 0) + (d.saved.reused_bytes || 0);
  async function removeCopies(g) {
    const extra = g.files.slice(1);
    const ok = await ask({ title: `Delete ${plural(extra.length, "copy", "copies")}?`, ok: "Delete the copies", danger: true,
                           text: `Keeps the oldest one (${g.files[0].path}) and deletes the ${extra.length === 1 ? "other" : "others"}. They can be brought back under Deleted.` });
    if (!ok) return;
    for (const f of extra) await api("/api/delete", { path: f.path }).catch((e) => toast(e.message));
    toast(`${plural(extra.length, "copy", "copies")} deleted`);
    renderStorage();
  }
  page(h("h1", {}, "Storage"),
    h("p", { class: "lede" }, "What is on the drive and what takes the space. Discord gives no storage limit, so this is about keeping things tidy."),
    h("div", { class: "stats" },
      stat("Files", size(d.total.bytes), plural(d.total.files, "file")),
      stat("Earlier versions", size(d.versions.bytes), plural(d.versions.count, "version") + " kept"),
      stat("Identical copies", size(d.duplicate_bytes), d.duplicates.length ? plural(d.duplicates.length, "file") + " stored more than once" : "none found"),
      stat("Spare pieces", size(d.spare_bytes), saved ? `${size(saved)} saved this session` : "for self-healing")),
    h("h2", {}, "By kind"),
    h("div", { class: "kindbar" }, d.kinds.map((k) => h("i", { style: `width:${(k.bytes / total) * 100}%;background:${KIND_COLORS[k.kind] || "#888"}`, title: KIND_NAMES[k.kind] || k.kind }))),
    h("div", { class: "list rows-simple" }, d.kinds.map((k) => h("div", { class: "row" },
      h("span", { class: "name" }, h("i", { class: "swatch", style: `background:${KIND_COLORS[k.kind] || "#888"}` }), h("span", { class: "label" }, KIND_NAMES[k.kind] || k.kind)),
      h("span", { class: "size" }, size(k.bytes)), h("span", { class: "date" }, plural(k.files, "file")),
      h("span", { class: "date" }, `${((k.bytes / total) * 100).toFixed(1)}%`)))),
    h("h2", {}, "Biggest folders"),
    d.folders.length ? h("div", { class: "list rows-simple" }, d.folders.slice(0, 15).map((f) => h("a", { class: "row", href: "#/files" + enc(f.path) },
      h("span", { class: "name" }, icon("folder", "folder"), h("span", { style: "min-width:0" }, h("span", { class: "label" }, base(f.path)), h("div", { class: "where" }, parent(f.path)))),
      h("span", { class: "size" }, size(f.bytes)), h("span", { class: "date" }, plural(f.files, "file")),
      h("span", { class: "date" }, h("div", { class: "meter", style: "margin:0;width:90px" }, h("i", { style: `width:${Math.max(2, (f.bytes / total) * 100)}%` })))))) : h("p", { class: "muted" }, "No folders yet."),
    h("h2", {}, "Largest files"),
    d.largest.length ? h("div", { class: "list" }, listHead(), d.largest.slice(0, 20).map((it) => itemRow(it, { where: true }))) : h("p", { class: "muted" }, "No files yet."),
    h("h2", {}, "Identical files"),
    d.duplicates.length ? [h("p", { class: "muted", style: "margin:0 0 12px" }, "The same content under several names. Identical pieces are stored once in Discord, so copies cost almost nothing there; removing them only tidies up."),
      h("div", { class: "dups" }, d.duplicates.slice(0, 50).map((g) => h("div", { class: "dup" },
        h("div", { class: "dup-head" }, h("b", {}, `${g.files.length} × ${size(g.size)}`),
          h("button", { class: "btn ghost small", onclick: () => removeCopies(g) }, "Keep the oldest, delete the rest")),
        g.files.map((f, i) => h("a", { class: "dup-file", href: "#/files" + enc(parent(f.path)) }, icon(kind(f.name) === "pdf" ? "text" : kind(f.name)),
          h("span", {}, f.path), i === 0 && h("span", { class: "tag" }, "Oldest"))))))]
      : h("p", { class: "muted" }, "No file is stored twice."));
}

// ------------------------------------------------------------------ editing text files
async function editText(it) {
  if (it.size > 2 * 2 ** 20) return toast("That file is too big to edit here (2 MB at most)");
  const text = await fetch(fileUrl(it.path), { credentials: "same-origin", cache: "no-store" }).then((r) => r.ok ? r.text() : Promise.reject()).catch(() => null);
  if (text === null) return toast("Could not open the file");
  closeSheet();
  const area = h("textarea", { class: "editor", spellcheck: "false" });
  area.value = text;
  let saved = text;
  const state = h("span", { class: "muted", style: "margin-right:auto;font-size:13px" }, "");
  const save = h("button", { class: "btn", type: "button", onclick: async () => {
    save.disabled = true;
    const r = await fetch("/api/upload" + q({ path: it.path, save: "now" }), { method: "POST", credentials: "same-origin",
                          headers: { "X-DD": "1" }, body: new Blob([area.value]) }).catch(() => null);
    save.disabled = false;
    if (!r || !r.ok) return toast("Could not save");
    saved = area.value;
    state.textContent = "Saved. The version before is kept under Earlier versions.";
    if (route.name === "files") render();
  } }, "Save");
  const m = modal(it.name, area, h("div", { class: "modal-actions" }, state, h("button", { class: "btn ghost", type: "button", onclick: () => m.close() }, "Close"), save));
  m.dlg.classList.add("wide", "tall");
  area.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save.click(); }
    if (e.key === "Tab") { e.preventDefault(); area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end"); }
  });
  area.addEventListener("input", () => { state.textContent = area.value === saved ? "" : "Not saved yet"; });
  m.dlg.addEventListener("cancel", (e) => { if (area.value !== saved && !confirm("Close without saving your changes?")) e.preventDefault(); });
  area.focus();
}

// ------------------------------------------------------------------ save from a web address
function saveFromLink(dir) {
  const url = h("input", { type: "url", placeholder: "https://example.com/file.zip", spellcheck: "false", autocomplete: "off" });
  const go_ = h("button", { class: "btn", type: "button", onclick: async () => {
    go_.disabled = true;
    const r = await attempt(() => api("/api/fetch", { url: url.value.trim(), path: dir }));
    go_.disabled = false;
    if (r) { m.close(); toast(`Fetching ${r.name}. It appears in ${dir === "/" ? "the drive" : base(dir)} when it is done.`); refreshStatus(); }
  } }, "Save to the drive");
  const m = modal("Save from a link",
    h("p", { class: "muted", style: "margin:0 0 16px" }, `The drive downloads the file itself and puts it in ${dir === "/" ? "the top folder" : "“" + base(dir) + "”"}. Nothing passes through this browser, so it also works from a phone.`),
    field("Web address", url), h("div", { class: "modal-actions" }, go_));
  url.addEventListener("keydown", (e) => { if (e.key === "Enter") go_.click(); });
  url.focus();
}

const fetchCards = new Map();
function showFetches(list) {
  const seen = new Set();
  for (const f of list || []) {
    seen.add(f.id);
    let c = fetchCards.get(f.id);
    if (!c) {
      c = { bar: h("i", { style: "width:0%" }), pct: h("span", {}), sub: h("div", { class: "sub" }), state: "" };
      c.el = h("div", { class: "item" }, h("div", { class: "n" }, h("span", { title: f.url }, f.name), c.pct), h("div", { class: "meter" }, c.bar), c.sub);
      fetchCards.set(f.id, c);
      tray.append(c.el);
    }
    const pct = f.total ? Math.min(100, (f.bytes / f.total) * 100) : 0;
    c.bar.style.width = (f.state === "done" ? 100 : pct) + "%";
    c.pct.textContent = f.state === "done" ? "Done" : f.state === "error" ? "Failed" : f.total ? Math.floor(pct) + "%" : size(f.bytes);
    c.sub.replaceChildren(f.state === "error" ? (f.error || "Could not be fetched") : f.state === "done" ? `Saved in ${f.folder}`
      : `From the web · ${size(f.bytes)}${f.total ? " of " + size(f.total) : ""} `,
      f.state === "running" && h("button", { class: "link", onclick: () => api("/api/fetch/cancel", { id: f.id }) }, "Cancel"));
    if (f.state === "done" && c.state === "running" && route.name === "files") render();
    c.state = f.state;
  }
  for (const [id, c] of fetchCards) if (!seen.has(id)) { fetchCards.delete(id); c.el.remove(); }
}

// ------------------------------------------------------------------ file requests
function requestDialog(it) {
  let hours = 168, mb = 2048;
  const seg = h("div", { class: "seg", role: "radiogroup" }, EXPIRY.map(([label, v]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(v === hours),
                  onclick: (e) => { hours = v; for (const b of seg.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); } }, label)));
  const sizes = h("select", { onchange: (e) => { mb = Number(e.target.value); } },
    [[100, "100 MB"], [1024, "1 GB"], [2048, "2 GB"], [10240, "10 GB"], [51200, "50 GB"]].map(([v, l]) => h("option", { value: v, selected: v === mb }, l)));
  const pw = h("input", { type: "password", autocomplete: "new-password", placeholder: "None" });
  const result = h("div");
  const form = h("div", {}, h("p", { class: "muted", style: "margin:0 0 16px" }, `People with the link can send files into “${it.name}”. They can't see what is in it, and nothing is ever overwritten.`),
    field("Link works for", seg), field("Largest file", sizes), field("Password", pw, "Optional. People need it to send files."));
  const go_ = h("button", { class: "btn", type: "button", onclick: async () => {
    go_.disabled = true;
    const r = await attempt(() => api("/api/share", { path: it.path, hours, password: pw.value, upload: true, max_mb: mb }));
    go_.disabled = false;
    if (!r) return;
    form.remove(); go_.remove();
    const url = shareUrl(r);
    result.replaceChildren(linkBox(url, isLocal() && !r.url && !(r.lan && r.lan.length)
      ? "It only opens on this computer. Set a domain name or turn on “On your network” in Settings to let others reach it."
      : "Send this link to whoever should send you files. Find and turn it off under Shared."));
    try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { /* the box is there */ }
  } }, "Create the link");
  modal(`Request files into “${it.name}”`, form, result, h("div", { class: "modal-actions" }, go_));
}

// ------------------------------------------------------------------ other apps (WebDAV)
async function connectApps() {
  const s = await api("/api/settings").catch(() => null);
  const on = !!(s && s.values.webdav_enabled);
  const addr = `${location.origin}/dav/`;
  const body = h("div");
  const draw = (enabled) => body.replaceChildren(
    h("p", { class: "muted", style: "margin:0 0 16px" }, "Other apps can open the drive over WebDAV: file managers on phones, “Map network drive” in Windows, Finder’s “Connect to Server”, rclone, backup apps. They sign in with the dashboard’s user name and password. Password-locked folders are never shown there."),
    h("div", { class: "toggle" }, h("div", {}, h("div", { class: "t" }, "Let other apps connect"), h("div", { class: "s" }, enabled ? "On" : "Off")),
      switchEl(enabled, async (v) => { if (await attempt(() => api("/api/settings", { webdav_enabled: v }), v ? "Other apps can connect now" : "Turned off")) draw(v); }, "Let other apps connect")),
    enabled && linkBox(addr, location.protocol === "https:" ? "Server address. User name and password: the dashboard’s."
      : "Server address. Over plain http the password travels unencrypted, and Windows refuses it: use your https address (a reverse proxy) when you can."));
  draw(on);
  modal("Use the drive from other apps", body);
}

// ------------------------------------------------------------------ find anything (Ctrl+K)
function openPalette() {
  if (document.querySelector("dialog.palette")) return;
  const pages = [["Home", "#/home"], ["Files", "#/files/"], ["Gallery", "#/gallery/"], ["Starred", "#/starred"], ["Recent", "#/recent"],
                 ["Activity", "#/history"], ["Storage", "#/storage"], ["Notes", "#/notes"], ["Bookmarks", "#/bookmarks"], ["Messages", "#/messages"], ["Contacts", "#/contacts"], ["Shared links", "#/shared"],
                 ["Folder sync", "#/sync"], ["Deleted files", "#/deleted"], ["Snapshots", "#/snapshots"], ["Log", "#/log"], ["Health", "#/health"],
                 ["Settings", "#/settings"]].map(([label, href]) => ({ label, hint: "Go to", icon: "enter", run: () => go(href) }));
  const actions = [
    { label: "Upload files", hint: "Action", icon: "upload", run: () => $("#pick").click() },
    { label: "New folder", hint: "Action", icon: "newfolder", run: () => { if (route.name !== "files") go("#/files/"); setTimeout(newFolder, 150); } },
    { label: "Save from a link", hint: "Action", icon: "download", run: () => saveFromLink(route.name === "files" ? currentDir : "/") },
    { label: "Unlock locked folders", hint: "Action", icon: "lock", run: unlockDialog },
    { label: "Lock folders again now", hint: "Action", icon: "lock", run: lockAllNow },
    { label: "Use the drive from other apps (WebDAV)", hint: "Action", icon: "globe", run: connectApps },
    { label: "What's new", hint: "Action", icon: "info", run: whatsNew },
  ];
  const input = h("input", { placeholder: "Find a file, a page or an action…", spellcheck: "false", autocomplete: "off" });
  const list = h("div", { class: "pal-list" });
  const dlg = h("dialog", { class: "palette" }, input, list);
  let items = [], at = 0, timer = null, gen = 0;
  const close = () => dlg.close();
  function draw() {
    at = Math.max(0, Math.min(at, items.length - 1));
    list.replaceChildren(...(items.length ? items.map((it, i) => h("button", { class: "pal-row" + (i === at ? " on" : ""), type: "button",
        onclick: () => { close(); it.run(); }, onmousemove: () => { if (at !== i) { at = i; draw(); } } },
      icon(it.icon), h("span", { class: "l" }, it.label), h("span", { class: "hint" }, it.hint)))
      : [h("div", { class: "muted pal-empty" }, "Nothing found")]));
    const on = list.querySelector(".on");
    if (on) on.scrollIntoView({ block: "nearest" });
  }
  function update() {
    const v = input.value.trim().toLowerCase();
    const fixed = [...pages, ...actions].filter((x) => !v || x.label.toLowerCase().includes(v));
    items = v ? fixed.slice(0, 6) : fixed;
    at = 0;
    draw();
    clearTimeout(timer);
    if (v.length < 2) return;
    const mine = ++gen;
    timer = setTimeout(async () => {
      const r = await api("/api/search" + q({ q: v })).catch(() => null);
      if (!r || mine !== gen) return;
      const found = r.items.slice(0, 30).map((it) => ({ label: it.name, hint: parent(it.path), icon: it.dir ? "folder" : (kind(it.name) === "pdf" ? "text" : kind(it.name)),
                                                         run: () => { go("#/files" + enc(it.dir ? it.path : parent(it.path))); if (!it.dir) setTimeout(() => openItem(it), 250); } }));
      items = [...found, ...fixed.slice(0, 6)];
      draw();
    }, 180);
  }
  input.addEventListener("input", update);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); at++; draw(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); at--; draw(); }
    else if (e.key === "Enter" && items[at]) { e.preventDefault(); const it = items[at]; close(); it.run(); }
  });
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  document.body.append(dlg);
  dlg.showModal();
  update();
  input.focus();
}
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
});

// Paste a screenshot or copied files anywhere to upload them into the folder that is open.
document.addEventListener("paste", (e) => {
  if (e.target.closest && e.target.closest("input, textarea, [contenteditable]")) return;
  const files = [...(e.clipboardData ? e.clipboardData.files : [])];
  if (!files.length) return;
  e.preventDefault();
  const stamp = new Date().toISOString().slice(0, 19).replace("T", " ").replace(/:/g, ".");
  const dir = route.name === "files" ? currentDir : "/";
  uploadAll(files.map((file, i) => {
    const pasted = /^image\.(png|jpe?g)$/i.test(file.name);      // screenshots all arrive as "image.png"
    const name = pasted ? `Pasted ${stamp}${files.length > 1 ? " " + (i + 1) : ""}.${file.name.split(".").pop()}` : file.name;
    return { file: pasted ? new File([file], name, { type: file.type }) : file, rel: name };
  }), dir);
});

// ------------------------------------------------------------------ what's new
async function whatsNew() {
  const data = await attempt(() => api("/api/changelog"));
  if (!data) return;
  const body = h("div", { class: "changelog" });
  let list = null;
  for (const line of (data.text || "No changelog found.").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("# ")) { list = null; continue; }
    if (t.startsWith("## ")) { list = null; body.append(h("h4", {}, t.slice(3))); }
    else if (t.startsWith("- ")) { if (!list) { list = h("ul"); body.append(list); } list.append(h("li", {}, t.slice(2).replace(/\*\*/g, ""))); }
    else if (list && line.startsWith("  ")) list.lastChild && list.lastChild.append(" " + t);
    else { list = null; body.append(h("p", { class: "muted" }, t)); }
  }
  modal(`What's new · version ${data.version}`, body);
}

// ------------------------------------------------------------------ Discord messages
// The logger and archiver: conversations of your own Discord account, kept as files in /Discord on
// the drive (encrypted, synced, free). Logging in is in Settings; the account's token never comes back here.
let chat = null;                      // the answer of /api/chat
let chatTimer = null;
let chatDir = null;                   // {guilds, dms} of the account
const chatChannels = new Map();       // server id -> its channels (loaded when the server is opened)
const chatOpen = new Set();           // servers opened in the picker
const chatView = { q: "", channel: "", kind: "all", shown: 60, live: "all" };
const archiveForm = { selection: { dms: {}, guilds: {} }, scope: "both", userIds: "", after: "", before: "", downloadAttachments: false, attachmentsOnly: false };
const MSG_KINDS = [["all", "All"], ["deleted", "Deleted"], ["edited", "Edited"], ["flagged", "Keyword hits"], ["files", "With files"], ["mine", "Mine"]];

function leaveMessages() { if (route.name !== "messages") { clearInterval(chatTimer); chatTimer = null; } }

function segmented(options, value, change) {
  const seg = h("div", { class: "seg", role: "radiogroup" }, options.map(([k, label]) =>
    h("button", { type: "button", role: "radio", "aria-checked": String(k === value),
                  onclick: (e) => { for (const b of seg.children) b.setAttribute("aria-checked", String(b === e.currentTarget)); change(k); } }, label)));
  return seg;
}

function discordLogin(done) {
  const token = h("input", { type: "password", placeholder: "Paste your token", autocomplete: "off", spellcheck: "false",
                             onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); ok.click(); } } });
  const ok = h("button", { class: "btn", type: "button", onclick: async () => {
    if (!token.value.trim()) return token.focus();
    ok.disabled = true;
    ok.textContent = "Logging in…";
    const r = await attempt(() => api("/api/chat/connect", { token: token.value }));
    ok.disabled = false;
    ok.textContent = "Log in";
    if (r) { chat = r; chatDir = null; chatChannels.clear(); m.close(); toast(`Logged in as ${r.user.name}`); if (route.name === "messages") render(); else if (done) done(); }
  } }, "Log in");
  const m = modal("Log in with Discord",
    h("p", { class: "muted ch-p" }, "Discord's own “Log in with Discord” button only tells an app who you are; it can't read your messages. "
      + "So the drive logs in the way your browser does, with your account's token. It is kept encrypted on this computer and is only ever sent to Discord."),
    field("Account token", token),
    h("details", { class: "ch-help" }, h("summary", {}, "Where do I find my token?"),
      h("ol", {}, h("li", {}, "Open Discord in your browser and sign in."), h("li", {}, "Press F12 and open the Network tab."),
        h("li", {}, "Click any channel, then pick a request to ", h("code", {}, "discord.com/api"), "."),
        h("li", {}, "Under Request Headers, copy the value of ", h("code", {}, "authorization"), "."))),
    h("div", { class: "notice ch-risk" }, "The token is full access to your account: never share it. Automating a personal account is against "
      + "Discord's Terms of Service and can lead to action against the account. Requests are paced, but the risk is yours to weigh."),
    h("div", { class: "modal-actions" }, ok));
  token.focus();
}

async function discordLogout(done) {
  if (!await ask({ title: "Log out of Discord?", ok: "Log out", danger: true,
                   text: "Logging and archiving stop and the token is removed from this computer. The messages already on your drive stay." })) return;
  const r = await attempt(() => api("/api/chat/disconnect", {}), "Logged out of Discord");
  if (r) { chat = r; chatDir = null; if (done) done(); }
}

async function discordSection(into) {
  const s = await api("/api/chat").catch(() => null);
  if (!s || !into.isConnected) return;
  const redraw = () => discordSection(into);
  const row = (t, text, ctl) => h("div", { class: "set-row" }, h("div", { class: "set-text" }, h("div", { class: "t" }, t), text && h("div", { class: "s" }, text)), h("div", { class: "set-ctl" }, ctl));
  into.replaceChildren(
    h("h2", {}, "Discord account"),
    h("div", { class: "settings" },
      s.connected
        ? row(`Logged in as ${s.user.name}`, `@${s.user.username} · ${plural(s.counts.guilds, "server")}, ${plural(s.counts.dms, "conversation")}. Logging and archiving run on this computer (${s.device}).`,
              h("button", { class: "btn ghost small", onclick: () => discordLogout(redraw) }, "Log out"))
        : row("Log in with Discord", s.error || "Connect your own Discord account to record messages as they arrive and to archive whole conversations onto your drive.",
              h("button", { class: "btn small", disabled: !!s.locked, onclick: () => discordLogin(redraw) }, "Log in")),
      (s.connected || (s.totals && s.totals.messages)) && row("Messages", s.locked ? `${s.folder} is locked in this browser. Unlock it with the padlock at the top.`
          : `${s.totals ? plural(s.totals.messages, "message") : "Nothing"} kept in ${s.folder} on your drive: encrypted, on every device, at no cost.`,
          h("a", { class: "btn ghost small", href: "#/messages" }, "Open"))));
}

function msgRow(m) {
  const name = m.author_name || m.author_username || "Unknown";
  const where = m.guild_name ? `${m.guild_name} › #${m.channel_name || m.channel_id}` : (m.channel_name || m.channel_id);
  return h("div", { class: "ch-msg" + (m.deleted_ts ? " gone" : "") },
    h("span", { class: "avatar-c ch-av", style: `background:hsl(${hue(String(m.author_id || name))} 45% 42%)` }, name.slice(0, 1).toUpperCase()),
    h("div", { class: "ch-main" },
      h("div", { class: "ch-head" }, h("b", {}, name), h("span", {}, where), h("span", { title: fmtFull.format(m.ts) }, when(m.ts / 1000)),
        m.deleted_ts && h("span", { class: "tag busy", title: fmtFull.format(m.deleted_ts) }, "deleted"),
        m.edits.length ? h("span", { class: "tag" }, "edited") : null,
        m.flagged ? h("span", { class: "tag" }, "keyword") : null,
        !m.on_drive && h("span", { class: "tag", title: "The text of this message was just recorded and is written to the drive within half a minute. Its attachments are saved to the drive right away." }, "text not on the drive yet")),
      m.content && h("div", { class: "ch-text" }, m.content),
      m.edits.map((e) => h("div", { class: "ch-old" }, h("span", {}, `Before the edit${e.ts ? " of " + fmtFull.format(e.ts) : ""}`), e.old_content)),
      m.attachments.map((a) => attachmentEl(m, a)),
      !m.content && !m.attachments.length ? h("div", { class: "ch-text muted" }, m.embeds.length ? "(a link preview)" : "(no text)") : null));
}

// Shown and opened from Discord while it is still there; the copy on the drive is the backup (the server decides).
function attachmentEl(m, a) {
  const name = a.filename || "file";
  const open = "/api/chat/open" + q({ id: m.id, a: a.id });
  const note = a.path ? "A copy is on your drive" : "Not copied to the drive: it opens from Discord while it is still there";
  const chip = () => h("a", { class: "ch-file" + (a.path ? "" : " off"), href: open, target: "_blank", rel: "noopener", title: note },
                       icon(a.path ? "file" : "link"), name, a.size ? h("span", { class: "muted" }, size(a.size)) : null);
  if (!/^(image|video)\//.test(a.content_type || "")) return chip();
  const box = h("a", { class: "ch-thumb", href: open, target: "_blank", rel: "noopener", title: `${name}${a.size ? " · " + size(a.size) : ""} · ${note}` },
    h("img", { src: "/api/chat/preview" + q({ id: m.id, a: a.id }), alt: name, loading: "lazy", onerror: () => box.replaceWith(chip()) }),
    (a.content_type || "").startsWith("video/") && h("span", { class: "play" }, icon("play")));
  return box;
}

const msgFilter = (kindKey) => kindKey === "all" ? {} : { [kindKey]: 1 };

function chipRow(options, value, change) {
  const row = h("div", { class: "bm-chips" }, options.map(([k, label]) =>
    h("button", { class: "bm-chip" + (k === value ? " on" : ""), type: "button",
                  onclick: (e) => { for (const b of row.children) b.classList.toggle("on", b === e.currentTarget); change(k); } }, label)));
  return row;
}

// ---- choosing servers and conversations (the same shape the logger's rules use)
const countChosen = (sel) => Object.keys(sel.dms).length + Object.keys(sel.guilds).length;
function toTargets(sel) {
  return [...Object.keys(sel.dms).map((id) => ({ kind: "dm", id })),
          ...Object.entries(sel.guilds).map(([id, v]) => v === true ? { kind: "guild", id } : { kind: "guild", id, channelIds: Object.keys(v) })];
}

function targetPicker(sel, change) {
  const list = h("div", { class: "ch-pick-list" });
  const filter = h("input", { class: "ch-input", type: "search", placeholder: "Filter", spellcheck: "false", oninput: () => draw() });
  const count = h("span", { class: "muted" });
  const changed = () => { change(); draw(); };
  const box = (state, toggle, label) => { const b = checkbox(state === true, toggle, label); setBox(b, state); return b; };
  async function open(g) {
    if (chatOpen.has(g.id)) chatOpen.delete(g.id);
    else {
      chatOpen.add(g.id);
      if (!chatChannels.has(g.id)) {
        const r = await api("/api/chat/channels" + q({ guild: g.id })).catch((e) => { toast(e.message); return null; });
        if (r) chatChannels.set(g.id, r.channels); else chatOpen.delete(g.id);
      }
    }
    draw();
  }
  function draw() {
    count.textContent = countChosen(sel) ? `${countChosen(sel)} chosen` : "Nothing chosen";
    if (!chatDir) return list.replaceChildren(h("div", { class: "ch-pick-none" }, "Loading…"));
    const f = filter.value.trim().toLowerCase();
    const dms = chatDir.dms.filter((c) => !f || c.name.toLowerCase().includes(f));
    const guilds = chatDir.guilds.filter((g) => !f || g.name.toLowerCase().includes(f));
    const rows = [];
    if (guilds.length) rows.push(h("div", { class: "ch-pick-head" }, "Servers"));
    for (const g of guilds) {
      const cur = sel.guilds[g.id];
      const isOpen = chatOpen.has(g.id) && chatChannels.has(g.id);
      rows.push(h("div", { class: "ch-pick-row" },
        box(cur === true ? true : cur ? "mixed" : false, () => { if (cur) delete sel.guilds[g.id]; else sel.guilds[g.id] = true; changed(); }, g.name),
        h("button", { class: "ch-pick-name", type: "button", "aria-expanded": String(isOpen), title: "Show the channels", onclick: () => open(g) },
          h("span", {}, g.name), icon("back", "ch-chev" + (isOpen ? " open" : "")))));
      for (const c of isOpen ? chatChannels.get(g.id) : []) {
        rows.push(h("div", { class: "ch-pick-row sub" },
          box(cur === true || !!(cur && cur[c.id]), (on) => {
            let next = cur === true ? Object.fromEntries(chatChannels.get(g.id).map((x) => [x.id, true])) : { ...(cur || {}) };
            if (on) next[c.id] = true; else delete next[c.id];
            if (Object.keys(next).length) sel.guilds[g.id] = next; else delete sel.guilds[g.id];
            changed();
          }, c.name),
          h("span", { class: "ch-pick-label" }, "#" + c.name), c.category && h("span", { class: "muted" }, c.category)));
      }
    }
    if (dms.length) rows.push(h("div", { class: "ch-pick-head" }, "Direct messages"));
    for (const c of dms) {
      rows.push(h("div", { class: "ch-pick-row" },
        box(!!sel.dms[c.id], (on) => { if (on) sel.dms[c.id] = true; else delete sel.dms[c.id]; changed(); }, c.name),
        h("span", { class: "ch-pick-label" }, c.name), c.group && h("span", { class: "tag" }, "group")));
    }
    list.replaceChildren(...(rows.length ? rows : [h("div", { class: "ch-pick-none" }, f ? "Nothing matches" : "No servers or conversations")]));
  }
  async function load(refresh) {
    const d = await api("/api/chat/directory" + (refresh ? "?refresh=1" : "")).catch((e) => { toast(e.message); return null; });
    if (d) { chatDir = d; if (refresh) chatChannels.clear(); }
    draw();
  }
  draw();
  if (!chatDir) load(false);
  return h("div", { class: "ch-pick" },
    h("div", { class: "ch-pick-top" }, filter,
      h("button", { class: "icon-btn", type: "button", "aria-label": "Read the list again", title: "Read the list again from Discord", onclick: () => load(true) }, icon("refresh"))),
    list, h("div", { class: "ch-pick-foot" }, count));
}

// ---- saved messages
function drawSaved(body) {
  const list = h("div", { class: "ch-list" });
  const none = h("div", { class: "empty", hidden: true });
  const more = h("button", { class: "btn ghost", type: "button", hidden: true, onclick: () => { chatView.shown += 100; load(); } }, "Show more");
  const place = h("select", { class: "ch-input ch-place", "aria-label": "Conversation", onchange: (e) => { chatView.channel = e.target.value; chatView.shown = 60; load(); } },
                  h("option", { value: "" }, "All conversations"));
  let typing;
  const search = h("input", { class: "ch-input ch-search", type: "search", placeholder: "Search the text", value: chatView.q, spellcheck: "false",
                              oninput: (e) => { clearTimeout(typing); typing = setTimeout(() => { chatView.q = e.target.value.trim(); chatView.shown = 60; load(); }, 250); } });
  const filters = () => ({ ...(chatView.q ? { q: chatView.q } : {}), ...(chatView.channel ? { channel: chatView.channel } : {}), ...msgFilter(chatView.kind) });
  let shown = null;
  async function load(quiet) {
    const r = await api("/api/chat/messages" + q({ ...filters(), limit: chatView.shown })).catch((e) => { if (!quiet) toast(e.message); return null; });
    if (!r || !list.isConnected) return;
    const now = r.total + "|" + r.messages.map((m) => `${m.id}:${m.touched}:${m.on_drive}`).join();
    if (now === shown) return;               // nothing new: leave the list (and what is selected in it) alone
    shown = now;
    list.replaceChildren(...r.messages.map(msgRow));
    more.hidden = r.messages.length >= r.total;
    none.hidden = r.total > 0;
    const any = chat.totals && chat.totals.messages;
    none.replaceChildren(...(any ? [h("b", {}, "Nothing matches"), "Try another word, conversation or filter."]
      : [h("b", {}, "No messages yet"), "Start the live logger, or archive a conversation: what they collect shows up here."]));
    const c = $("#ch-count");
    if (c) c.textContent = r.total ? plural(r.total, "message") : "";
  }
  api("/api/chat/places").then((r) => {
    for (const p of r.items) {
      place.append(h("option", { value: p.id, selected: p.id === chatView.channel },
        `${p.guild_name ? p.guild_name + " › #" : ""}${p.name || p.id} (${p.count.toLocaleString()})`));
    }
  }).catch(() => null);
  const act = $("#ch-actions");
  if (act) act.replaceChildren(
    h("button", { class: "btn ghost small", type: "button", onclick: (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      openMenu([["html", "A web page"], ["json", "JSON"], ["csv", "A spreadsheet (CSV)"], ["txt", "Plain text"]].map(([fmt, label]) =>
        ({ label, icon: "download", run: () => download("/api/chat/export" + q({ ...filters(), format: fmt }), `discord-messages.${fmt}`) })),
        r.right - 220, r.bottom + 4, "Export what is shown");
    } }, icon("download"), "Export"),
    h("button", { class: "icon-btn", type: "button", "aria-label": "More", title: "More", onclick: (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      openMenu([
        { label: "Open the folder on the drive", icon: "folder", run: () => go("#/files" + enc(chat.folder)) },
        { label: "Write to the drive now", icon: "upload", run: async () => {
          const w = await attempt(() => api("/api/chat/flush", {}));
          if (w) { toast(w.written ? `${plural(w.written, "record")} written to the drive` : "Everything is on the drive already"); load(); }
        } },
        { label: "Read everything again from the drive", icon: "refresh", run: async () => {
          toast("Reading the files…");
          const w = await attempt(() => api("/api/chat/rebuild", {}));
          if (w) { toast(`Read ${plural(w.files, "file")}`); load(); }
        } },
      ], r.right - 220, r.bottom + 4, "Messages");
    } }, icon("more")));
  body.replaceChildren(h("div", { class: "ch-filters" }, search, place),
    chipRow(MSG_KINDS, chatView.kind, (k) => { chatView.kind = k; chatView.shown = 60; load(); }),
    list, none, h("div", { class: "ch-more" }, more));
  load();
  // New, edited and deleted messages show up by themselves.
  chatTimer = setInterval(() => { if (!document.hidden && route.name === "messages" && list.isConnected) load(true); }, 4000);
}

// ---- live logger
const LOG_STATUS = { connected: "Recording", connecting: "Connecting", reconnecting: "Reconnecting", stopped: "Stopped" };

function drawLive(body) {
  clearInterval(chatTimer);
  chatTimer = null;
  const act = $("#ch-actions");
  if (!chat.connected) {
    if (act) act.replaceChildren();
    return body.replaceChildren(h("div", { class: "empty" }, h("b", {}, "Log in to record messages"), "The live logger listens with your Discord account.",
      h("div", { class: "ch-cta" }, h("button", { class: "btn", onclick: () => discordLogin(render) }, "Log in with Discord"))));
  }
  const rules = chat.logger.rules;
  const save = async (patch) => {
    Object.assign(rules, patch);
    const r = await attempt(() => api("/api/chat/logger/rules", rules));
    if (r) Object.assign(rules, r.rules);
  };
  const pill = h("span", { class: "ch-status" });
  const toggle = h("button", { class: "btn small", type: "button", onclick: async () => {
    toggle.disabled = true;
    const r = await attempt(() => api("/api/chat/logger", { on: chat.logger.status === "stopped" }));
    toggle.disabled = false;
    if (r) { chat = r; state(); }
  } });
  const problem = h("div", { class: "notice", hidden: true });
  const captured = h("span", { class: "muted" });
  function state() {
    const lg = chat.logger, on = lg.status !== "stopped";
    pill.className = "ch-status " + lg.status;
    pill.replaceChildren(h("i"), LOG_STATUS[lg.status] || lg.status);
    toggle.className = on ? "btn ghost small" : "btn small";
    toggle.textContent = on ? "Stop" : "Start logging";
    problem.hidden = !lg.error;
    problem.textContent = lg.error || "";
    const waiting = chat.totals ? chat.totals.waiting : 0;
    captured.textContent = `${plural(lg.captured, "message")} recorded since the drive started${waiting ? ` · ${waiting.toLocaleString()} waiting to be written to the drive` : ""}`;
  }
  const optionsShown = () => { try { return localStorage.getItem("dd-live-options") !== "hidden"; } catch { return true; } };
  const options = h("button", { class: "icon-btn", type: "button", onclick: () => {
    try { localStorage.setItem("dd-live-options", optionsShown() ? "hidden" : "shown"); } catch { /* private mode */ }
    fold();
  } }, icon("settings"));
  function fold() {
    const on = optionsShown();
    options.title = on ? "Hide the options: more room for the messages" : "Show what is recorded";
    options.setAttribute("aria-label", options.title);
    options.setAttribute("aria-expanded", String(on));
    const cols = body.querySelector(".ch-cols");
    if (cols) cols.classList.toggle("solo", !on);
  }
  if (act) act.replaceChildren(pill, toggle, options);
  const opt = (label, hint, key) => h("div", { class: "ch-opt" }, h("div", { class: "t" }, label, hint && h("div", { class: "s" }, hint)),
    switchEl(rules[key], (on) => save({ [key]: on }), label));
  const words = h("input", { class: "ch-input", value: rules.keywords.join(", "), placeholder: "e.g. my name, project x", spellcheck: "false",
                             onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); },
                             onchange: (e) => save({ keywords: e.target.value.split(",").map((k) => k.trim()).filter(Boolean) }) });
  const list = h("div", { class: "ch-list" });
  const none = h("div", { class: "empty", hidden: true });
  let shown = null;
  async function feed() {
    const r = await api("/api/chat/messages" + q({ source: "live", order: "touched", limit: 80, ...msgFilter(chatView.live) })).catch(() => null);
    if (!r || !list.isConnected) return;
    const now = r.messages.map((m) => `${m.id}:${m.touched}:${m.on_drive}`).join() + chat.logger.status;
    if (now === shown) return;               // nothing new: leave the list (and what is selected in it) alone
    shown = now;
    list.replaceChildren(...r.messages.map(msgRow));
    none.hidden = r.messages.length > 0;
    none.replaceChildren(...(chat.logger.status === "stopped" ? [h("b", {}, "The logger is off"), "Start logging to record messages as they arrive."]
      : [h("b", {}, "Listening…"), "New messages appear here as they happen."]));
  }
  body.replaceChildren(problem,
    h("div", { class: "ch-cols" },
      h("div", { class: "ch-side" },
        h("div", { class: "ch-card" }, h("h3", {}, "What to record"),
          segmented([["all", "Everything I can see"], ["custom", "Only what I pick"]], rules.scope, async (scope) => { await save({ scope }); drawLive(body); }),
          h("div", { class: "ch-opts" },
            opt("My messages", "", "captureMine"), opt("Other people's messages", "", "captureOthers"),
            rules.scope === "custom" && opt("All direct messages", "Also conversations that start later.", "allDms"),
            opt("Save attachments", "Download files as they are posted, before they can be removed.", "downloadAttachments"),
            opt("Attachments only", "Record only messages that have files, and save the files without the text. Other messages are left out.", "attachmentsOnly")),
          h("div", { class: "fl ch-fl" }, "Keyword alerts"), words,
          h("div", { class: "hint" }, "Messages containing one of these words are marked. Changes apply right away, also while recording.")),
        rules.scope === "custom" && targetPicker(rules.selection, () => save({ selection: rules.selection }))),
      h("div", {},
        h("div", { class: "ch-feed-top" }, h("h3", {}, "Live feed"), captured),
        chipRow(MSG_KINDS.slice(0, 4), chatView.live, (k) => { chatView.live = k; feed(); }), list, none)));
  fold();
  state();
  feed();
  chatTimer = setInterval(async () => {
    if (document.hidden) return;
    const s = await api("/api/chat").catch(() => null);
    if (!s || route.name !== "messages" || !list.isConnected) return;
    chat = Object.assign(s, { logger: Object.assign(s.logger || chat.logger, { rules }) });
    state();
    feed();
  }, 3000);
}

// ---- archive
const JOB_LABEL = { running: "Archiving", stopped: "Paused", done: "Finished", error: "Stopped by a problem" };

function jobPanel(job, redraw) {
  if (!job) return null;
  const st = job.stats;
  const active = job.status === "running" && job.running;
  const label = job.status === "running" && !job.running ? "Interrupted" : JOB_LABEL[job.status] || job.status;
  const pct = job.status === "done" ? 100 : st.channelsTotal ? 100 * st.channelsDone / st.channelsTotal : 0;
  const act = async (what, ok) => { const r = await attempt(() => api("/api/chat/archive/" + what, {}), ok); if (r) { chat = r; redraw(); } };
  return h("div", { class: "ch-card ch-job" },
    h("div", { class: "ch-job-top" }, h("b", {}, label), h("span", { class: "muted ch-job-now" }, active && st.current ? st.current : ""),
      active ? h("button", { class: "btn ghost small", onclick: () => act("stop") }, "Stop")
             : [job.status !== "done" && h("button", { class: "btn small", disabled: !chat.connected, onclick: () => act("resume") }, "Carry on"),
                h("button", { class: "btn ghost small", onclick: () => act("clear") }, "Clear")]),
    h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })),
    h("div", { class: "ch-job-stats muted" }, `${st.channelsDone} of ${plural(st.channelsTotal, "channel")} · ${plural(st.scanned, "message")} read · ${st.saved.toLocaleString()} new`
      + (st.files ? ` · ${plural(st.files, "file")}` : "")),
    h("div", { class: "ch-job-log" }, job.log.slice(-40).map((l) =>
      h("div", { class: "lvl-" + l.level }, h("span", {}, fmtTime.format(l.t * 1000)), l.text))));
}

function drawArchive(body) {
  clearInterval(chatTimer);
  chatTimer = null;
  const act = $("#ch-actions");
  if (act) act.replaceChildren(h("a", { class: "btn ghost small", href: "#/files" + enc(chat.folder) }, icon("folder"), "Open the folder"));
  const job = h("div", { class: "ch-job-box" });
  const redraw = () => { job.replaceChildren(...[jobPanel(chat.archive, redraw)].filter(Boolean)); sync(); };
  const f = archiveForm;
  const start = h("button", { class: "btn ch-start", type: "button", onclick: async () => {
    const ms = (v) => v ? new Date(v).getTime() : "";
    const r = await attempt(() => api("/api/chat/archive/start", { targets: toTargets(f.selection), scope: f.scope, userIds: f.userIds,
                                                                   after: ms(f.after), before: ms(f.before), downloadAttachments: f.downloadAttachments, attachmentsOnly: f.attachmentsOnly }));
    if (r) { chat = r; redraw(); }
  } }, icon("download"), "Start archive");
  function sync() {
    const busy = chat.archive && chat.archive.status === "running" && chat.archive.running;
    start.disabled = busy || !countChosen(f.selection);
  }
  if (!chat.connected) {
    body.replaceChildren(job, h("div", { class: "empty" }, h("b", {}, "Log in to archive conversations"), "The archiver reads your conversations with your Discord account.",
      h("div", { class: "ch-cta" }, h("button", { class: "btn", onclick: () => discordLogin(render) }, "Log in with Discord"))));
    return redraw();
  }
  const date = (key) => h("input", { class: "ch-input", type: "datetime-local", value: f[key], onchange: (e) => { f[key] = e.target.value; } });
  body.replaceChildren(job,
    h("div", { class: "ch-cols wide" },
      targetPicker(f.selection, sync),
      h("div", {},
        h("div", { class: "ch-card" }, h("h3", {}, "Whose messages"),
          segmented([["both", "Everyone"], ["mine", "Only mine"], ["others", "Only other people's"]], f.scope, (v) => { f.scope = v; }),
          h("div", { class: "fl ch-fl" }, "Only these people (optional)"),
          h("input", { class: "ch-input", value: f.userIds, placeholder: "User IDs, separated by commas", spellcheck: "false", oninput: (e) => { f.userIds = e.target.value; } })),
        h("div", { class: "ch-card" }, h("h3", {}, "What to save"),
          h("div", { class: "ch-dates" }, h("label", {}, h("div", { class: "fl" }, "From"), date("after")), h("label", {}, h("div", { class: "fl" }, "Until"), date("before"))),
          h("div", { class: "ch-opt" }, h("div", { class: "t" }, "Download attachments", h("div", { class: "s" }, "Store pictures, videos and files on the drive too, so they survive when the originals are deleted.")),
            switchEl(f.downloadAttachments, (on) => { f.downloadAttachments = on; }, "Download attachments")),
          h("div", { class: "ch-opt" }, h("div", { class: "t" }, "Attachments only", h("div", { class: "s" }, "Save only the files, without the conversation: messages without a file are left out, and no text is kept.")),
            switchEl(f.attachmentsOnly, (on) => { f.attachmentsOnly = on; }, "Attachments only"))),
        start,
        h("div", { class: "hint" }, `Messages are read at a calm pace and saved in ${chat.folder} on your drive. Archiving a conversation again only adds what is new.`))));
  redraw();
  chatTimer = setInterval(async () => {
    if (document.hidden || !(chat.archive && chat.archive.running)) return;
    const s = await api("/api/chat").catch(() => null);
    if (!s || route.name !== "messages" || !job.isConnected) return;
    chat = s;
    redraw();
  }, 1500);
}

async function renderMessages() {
  clearInterval(chatTimer);
  chatTimer = null;
  const s = await attempt(() => api("/api/chat"));
  if (!s || route.name !== "messages") return;
  chat = s;
  const tab = ["live", "archive"].includes(route.rest[0]) ? route.rest[0] : "saved";
  const head = h("div", { class: "bar" }, h("h1", { style: "margin:0" }, "Messages"), h("span", { class: "muted g-count", id: "ch-count" }),
                 h("div", { class: "actions", id: "ch-actions" }));
  if (s.locked) {
    return page(head, h("div", { class: "empty" }, h("b", {}, "Messages are locked"), `${s.folder} is hidden or password-locked here. Unlock it with the padlock at the top.`));
  }
  if (!s.connected && !(s.totals && s.totals.messages) && !s.archive) {
    return page(head, h("div", { class: "empty" }, h("b", {}, "Keep your Discord conversations on your drive"),
      s.error || "Record messages as they arrive, with the original text of ones later edited or deleted, and archive whole conversations. "
        + `Everything is saved in ${s.folder}: encrypted, on all your devices, at no cost.`,
      h("div", { class: "ch-cta" }, h("button", { class: "btn", onclick: () => discordLogin(render) }, "Log in with Discord"))));
  }
  const body = h("div", {});
  page(head,
    !s.connected && h("div", { class: "notice ch-note" }, s.error || "Not logged in to Discord: what is saved can be read, but nothing new is recorded. ",
      h("button", { class: "btn ghost small", onclick: () => discordLogin(render) }, "Log in")),
    h("div", { class: "seg ch-tabs", role: "tablist" }, [["saved", "Saved"], ["live", "Live logger"], ["archive", "Archive"]].map(([k, label]) =>
      h("a", { href: "#/messages" + (k === "saved" ? "" : "/" + k), role: "tab", "aria-selected": String(k === tab) }, label,
        k === "live" && s.logger && s.logger.status !== "stopped" && h("i", { class: "ch-dot " + s.logger.status }),
        k === "archive" && s.archive && s.archive.running && h("i", { class: "ch-dot connected" })))),
    body);
  ({ saved: drawSaved, live: drawLive, archive: drawArchive })[tab](body);
}

// ------------------------------------------------------------------ account
$("#account").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  openMenu([
    { label: status && status.user ? `Signed in as ${status.user}` : "Signed in", icon: "user", disabled: true },
    "-",
    { label: "Settings", icon: "settings", run: () => go("#/settings") },
    { label: "What's new", icon: "info", run: whatsNew },
    { label: "Sign out", icon: "signout", run: async () => { await api("/api/logout", {}).catch(() => null); location.reload(); } },
  ], r.right - 220, r.bottom + 6, "Account");
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// ------------------------------------------------------------------ start
dropTarget(document.querySelector('#nav a[data-nav="files"]'), "/");
dropTarget(document.querySelector('#nav a[data-nav="deleted"]'), "/", "delete");
render();
refreshStatus();
(function poll() {
  const busy = status && (status.uploads.length || status.downloads || status.queued || (status.fetches || []).some((f) => f.state === "running"));
  setTimeout(async () => { await refreshStatus(); poll(); }, busy ? 1000 : 3000);
})();
