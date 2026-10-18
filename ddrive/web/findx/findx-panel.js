"use strict";
/* The Manage panel on DDrive's FindX page: everything the FindX app does, for your phones and circles,
 * from the browser. What it changes goes to the phone as sealed commands (FX.dash, findx-dashboard.js);
 * what it shows comes from the phone's sealed reports and stats file. Turning FindX off, unlinking or
 * erasing a phone are deliberately not here: only on the phone itself, with its screen lock.
 */
(function () {
  const FX = window.FX;
  if (!FX || !FX.ui || !FX.dash) return;
  const ui = FX.ui, dash = FX.dash;
  const $ = (id) => document.getElementById(id);

  /** Makes an element; strings become text (never HTML), so names from phones can't inject anything. */
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "style") e.style.cssText = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
    return e;
  }
  const ICON = {
    bell: '<path d="M12,22c1.1,0 2,-0.9 2,-2h-4c0,1.1 0.89,2 2,2zM18,16v-5c0,-3.07 -1.64,-5.64 -4.5,-6.32V4c0,-0.83 -0.67,-1.5 -1.5,-1.5s-1.5,0.67 -1.5,1.5v0.68C7.63,5.36 6,7.92 6,11v5l-2,2v1h16v-1l-2,-2z" fill="currentColor"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    target: '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></g><circle cx="12" cy="12" r="1.6" fill="currentColor"/>',
    live: '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/></g><circle cx="12" cy="12" r="2.2" fill="currentColor"/>',
    flag: '<path d="M14.4,6L14,4H5v17h2v-7h5.6l0.4,2h7V6z" fill="currentColor"/>',
    radar: '<g fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6.5-6.5" stroke-linecap="round"/></g>',
    nav: '<path d="M12,2L4.5,20.29l0.71,0.71L12,18l6.79,3 0.71,-0.71z" fill="currentColor"/>',
    copy: '<path d="M16,1H4c-1.1,0 -2,0.9 -2,2v14h2V3h12V1zM19,5H8c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h11c1.1,0 2,-0.9 2,-2V7c0,-1.1 -0.9,-2 -2,-2zM19,21H8V7h11v14z" fill="currentColor"/>',
    gear: '<path d="M19.14,12.94c0.04,-0.3 0.06,-0.61 0.06,-0.94c0,-0.32 -0.02,-0.64 -0.07,-0.94l2.03,-1.58c0.18,-0.14 0.23,-0.41 0.12,-0.61l-1.92,-3.32c-0.12,-0.22 -0.37,-0.29 -0.59,-0.22l-2.39,0.96c-0.5,-0.38 -1.03,-0.7 -1.62,-0.94L14.4,2.81c-0.04,-0.24 -0.24,-0.41 -0.48,-0.41h-3.84c-0.24,0 -0.43,0.17 -0.47,0.41L9.25,5.35C8.66,5.59 8.12,5.92 7.63,6.29L5.24,5.33c-0.22,-0.08 -0.47,0 -0.59,0.22L2.74,8.87C2.62,9.08 2.66,9.34 2.86,9.48l2.03,1.58C4.84,11.36 4.8,11.69 4.8,12s0.02,0.64 0.07,0.94l-2.03,1.58c-0.18,0.14 -0.23,0.41 -0.12,0.61l1.92,3.32c0.12,0.22 0.37,0.29 0.59,0.22l2.39,-0.96c0.5,0.38 1.03,0.7 1.62,0.94l0.36,2.54c0.05,0.24 0.24,0.41 0.48,0.41h3.84c0.24,0 0.44,-0.17 0.47,-0.41l0.36,-2.54c0.59,-0.24 1.13,-0.56 1.62,-0.94l2.39,0.96c0.22,0.08 0.47,0 0.59,-0.22l1.92,-3.32c0.12,-0.22 0.07,-0.47 -0.12,-0.61L19.14,12.94zM12,15.6c-1.98,0 -3.6,-1.62 -3.6,-3.6s1.62,-3.6 3.6,-3.6s3.6,1.62 3.6,3.6S13.98,15.6 12,15.6z" fill="currentColor"/>',
    people: '<path d="M16,11c1.66,0 2.99,-1.34 2.99,-3S17.66,5 16,5c-1.66,0 -3,1.34 -3,3s1.34,3 3,3zM8,11c1.66,0 2.99,-1.34 2.99,-3S9.66,5 8,5C6.34,5 5,6.34 5,8s1.34,3 3,3zM8,13c-2.33,0 -7,1.17 -7,3.5V19h14v-2.5c0,-2.33 -4.67,-3.5 -7,-3.5zM16,13c-0.29,0 -0.62,0.02 -0.97,0.05 1.16,0.84 1.97,1.97 1.97,3.45V19h6v-2.5c0,-2.33 -4.67,-3.5 -7,-3.5z" fill="currentColor"/>',
    pin: '<path d="M12,2C8.13,2 5,5.13 5,9c0,5.25 7,13 7,13s7,-7.75 7,-13c0,-3.87 -3.13,-7 -7,-7zM12,11.5c-1.38,0 -2.5,-1.12 -2.5,-2.5s1.12,-2.5 2.5,-2.5 2.5,1.12 2.5,2.5 -1.12,2.5 -2.5,2.5z" fill="currentColor"/>',
  };
  function icon(name) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.innerHTML = ICON[name];                                 // (our own fixed shapes)
    return s;
  }
  const initials = (name) => (name || "?").trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const face = (id, name) => h("div", { class: "m-face", style: `background:hsl(${FX.hue(id)},62%,52%)` }, initials(name));
  const toggle = (on, change) => {
    const i = h("input", { type: "checkbox" });
    i.checked = !!on;
    i.addEventListener("change", () => change(i.checked));
    return h("label", { class: "m-switch" }, i, h("i"));
  };
  const copy = (text, said) => navigator.clipboard ? navigator.clipboard.writeText(text).then(() => ui.toast(said || "Copied")) : prompt("Copy it:", text);
  const sec = (t) => h("div", { class: "m-sec" }, t);
  const row = (n, s, v, onclick) => h("div", { class: "m-row" + (onclick ? " click" : ""), onclick }, h("div", { class: "t" }, h("div", { class: "n" }, n), s ? h("div", { class: "s" }, s) : null), v || null);
  async function busy(btn, fn) {
    btn.disabled = true;
    try { return await fn(); } catch (e) { ui.toast(e.message || "That didn't work"); } finally { btn.disabled = false; }
  }

  // ------------------------------------------------------------ words

  const PROBLEMS = {
    location: ["Location isn't allowed", "FindX can't tell where this phone is."],
    background: ["Location isn't allowed all the time", "It can only be found while FindX is open."],
    locationOff: ["Location is off", "Turned off in the phone's quick settings."],
    nearby: ["Nearby devices isn't allowed", "Your other devices can't find it over Bluetooth."],
    notifications: ["Notifications are off", "Lost mode's message can't show on its lock screen."],
    dnd: ["Do Not Disturb access is off", "Ringing won't get through \"total silence\"."],
    battery: ["Battery saving holds FindX back", "A ring or locate may wait until the phone wakes."],
    fullScreen: ["Full-screen alerts are off", "The screen won't light up while it rings."],
    beaconOff: ["Findable nearby is off", "Your devices can't find it over Bluetooth (Settings tab)."],
  };
  const POSE = { FaceUp: "Face up", FaceDown: "Face down", Upright: "Upright", OnSide: "On its side", Tilted: "Propped up" };
  const DETAIL = ["Battery saver", "Balanced", "Detailed"];
  const PARTS = ["satellites", "network", "processor", "bluetooth", "sensors", "ringing"];
  const PART = { satellites: ["Satellites", "#ff2a2a"], network: ["Network", "#0a84ff"], processor: ["Processor", "#bf5af2"],
                 bluetooth: ["Bluetooth", "#30b0c7"], sensors: ["Sensors", "#ff9f0a"], ringing: ["Ringing", "#ffcc00"] };
  const dur = (ms) => { const s = Math.floor(ms / 1000); return s < 1 ? "under a second" : s < 60 ? s + " s" : s < 3600 ? Math.floor(s / 60) + " min " + (s % 60) + " s" : Math.floor(s / 3600) + " h " + Math.floor(s / 60 % 60) + " min"; };
  const bytes = (b) => b < 1024 ? b + " B" : b < 1048576 ? Math.round(b / 1024) + " KB" : (b / 1048576).toFixed(1) + " MB";
  const mah = (x) => x < 0.1 ? x.toFixed(2) + " mAh" : x < 10 ? x.toFixed(1) + " mAh" : Math.round(x) + " mAh";
  const pct = (p) => p < 0.01 ? "under 0.01%" : p < 1 ? p.toFixed(2) + "%" : p.toFixed(1) + "%";

  /** The phone's energy model (Energy.kt), with its power profile from the stats file. */
  function energy(u, p) {
    const h = (ms) => (ms || 0) / 3600e3;
    const wifiS = (u.ww || 0) * 0.3 + (u.bw || 0) / 2e6, cellS = (u.wc || 0) * 6 + (u.bc || 0) / 1e6;
    return {
      satellites: h(u.gps) * p.gps, network: (wifiS * p.wifi + cellS * p.cell) / 3600, processor: h(u.cpu) * p.cpu,
      bluetooth: h(u.bl) * p.bleLow + h(u.bf) * p.bleFast + h(u.scan) * p.scan, sensors: h(u.sens) * p.sensor, ringing: h(u.ring) * p.ring,
    };
  }
  const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);
  function measured(part, u) {
    switch (part) {
      case "satellites": return u.gps ? "On for " + dur(u.gps) : "Not used";
      case "network": return ((u.ww || 0) + (u.wc || 0)) + " radio wake-ups" + (u.wc ? " (" + u.wc + " on mobile data)" : "") + " · " + bytes((u.bw || 0) + (u.bc || 0));
      case "processor": return dur(u.cpu || 0) + " of processing";
      case "bluetooth": return [((u.bl || 0) + (u.bf || 0)) ? "Beacon on " + dur((u.bl || 0) + (u.bf || 0)) : "", u.scan ? "looking for your devices " + dur(u.scan) : ""].filter(Boolean).join(" · ") || "Not used";
      case "sensors": return u.sens ? "Short readings, " + dur(u.sens) + " in all" : "Not used";
      case "ringing": return u.ring ? "Rang for " + dur(u.ring) : "Didn't ring";
    }
    return "";
  }

  /** Stacked bars as SVG: one per slot, coloured by part. */
  function bars(slots, profile, label, pick, selected) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "m-bars");
    svg.setAttribute("viewBox", "0 0 " + slots.length * 20 + " 130");
    svg.setAttribute("preserveAspectRatio", "none");
    const parts = slots.map(([, u]) => energy(u, profile));
    const max = Math.max(1e-9, ...parts.map(sum));
    slots.forEach(([t], i) => {
      let y = 112;
      for (const p of PARTS) {
        const hgt = parts[i][p] / max * 108;
        if (hgt <= 0) continue;
        y -= hgt;
        const r = document.createElementNS(ns, "rect");
        r.setAttribute("x", i * 20 + 4); r.setAttribute("y", y); r.setAttribute("width", 12); r.setAttribute("height", hgt); r.setAttribute("rx", 2);
        r.setAttribute("fill", PART[p][1]);
        if (selected != null && t !== selected) r.setAttribute("opacity", "0.45");
        svg.append(r);
      }
      const tx = document.createElementNS(ns, "text");
      tx.setAttribute("x", i * 20 + 10); tx.setAttribute("y", 127); tx.setAttribute("text-anchor", "middle"); tx.setAttribute("font-size", "10");
      tx.setAttribute("fill", "currentColor"); tx.setAttribute("opacity", t === selected ? "1" : "0.55");
      tx.textContent = label(t);
      svg.append(tx);
      if (pick) {
        const hit = document.createElementNS(ns, "rect");
        hit.setAttribute("x", i * 20); hit.setAttribute("y", 0); hit.setAttribute("width", 20); hit.setAttribute("height", 130); hit.setAttribute("fill", "transparent");
        hit.style.cursor = "pointer";
        hit.addEventListener("click", () => pick(t));
        svg.append(hit);
      }
    });
    return h("div", {}, svg, h("div", { class: "m-note", style: "margin:2px 0 0" }, "Highest: " + mah(max)));
  }

  // ------------------------------------------------------------ the panel

  const panel = h("div", { class: "manage glass", role: "dialog", "aria-label": "Manage" });
  const title = h("h2"), sub = h("div", { class: "sub" });
  const tabs = h("div", { class: "tabs" });
  const body = h("div", { class: "body" });
  panel.append(h("header", {}, h("div", { class: "t" }, title, sub), h("button", { class: "x", type: "button", "aria-label": "Close", onclick: close }, "✕")), tabs, body);
  document.body.append(panel);
  let view = null, tab = "overview", deviceId = null, statsCache = new Map(), selDay = null;

  function close() { panel.classList.remove("open"); view = null; }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && view) close(); });

  function openDevice(id, t) {
    deviceId = id; tab = t || tab || "overview"; view = "device";
    panel.classList.add("open");
    render();
  }
  function openCircles() { view = "circles"; panel.classList.add("open"); render(); }

  function setTabs(list) {
    tabs.replaceChildren(...list.map(([key, label]) => h("button", { type: "button", class: key === tab ? "on" : "", onclick: () => { tab = key; render(); } }, label)));
    tabs.style.display = list.length ? "" : "none";
  }

  async function render() {
    if (!view) return;
    if (view === "circles") return renderCircles();
    const d = dash.device(deviceId);
    const r = d && d.report;
    title.textContent = d ? d.name : "Device";
    sub.textContent = r ? (Date.now() - r.t < 60e3 ? "Heard from just now" : "Heard from " + FX.ago(r.t)) + (r.cfg ? " · FindX " + r.cfg.v : "") : "Not heard from yet";
    setTabs([["overview", "Overview"], ["battery", "Battery"], ["settings", "Settings"], ["sharing", "Sharing"], ["activity", "Activity"]]);
    const fn = { overview: renderOverview, battery: renderBattery, settings: renderSettings, sharing: renderSharing, activity: renderActivity }[tab];
    body.replaceChildren(await fn(deviceId, d, r));
  }

  async function stats(id, again) {
    if (!again && statsCache.has(id) && Date.now() - statsCache.get(id).at < 30e3) return statsCache.get(id).s;
    const s = await dash.stats(id).catch(() => null);
    statsCache.set(id, { at: Date.now(), s });
    return s;
  }

  /** Asks the phone to send its stats now, and waits (up to half a minute) for the new file. */
  async function freshStats(id) {
    const before = (await stats(id)) ? (await stats(id)).t : 0;
    await dash.command(id, "stats", {}).catch(() => null);
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const s = await stats(id, true);
      if (s && s.t > before) return s;
    }
    return stats(id);
  }

  // ------------------------------------------------------------ overview: status, health, actions

  async function renderOverview(id, d, r) {
    const out = h("div");
    if (!r) { out.append(h("div", { class: "m-empty" }, "This phone hasn't reported to this DDrive yet. Open FindX on it and check it's linked here.")); return out; }
    const probs = (r.ok || []).filter((p) => PROBLEMS[p]);
    if (probs.length) {
      out.append(sec("Needs attention on the phone"), h("div", { class: "m-card" }, probs.map((p) => row(h("span", { class: "m-warn" }, PROBLEMS[p][0]), PROBLEMS[p][1]))));
      out.append(h("div", { class: "m-note" }, "These are fixed on the phone: FindX › Settings › Permissions."));
    }
    const fix = r.fix;
    out.append(sec("Now"));
    const tiles = [
      ["Battery", r.bat >= 0 ? r.bat + "%" : "–", r.chg ? "charging" : ""],
      ["FindX used today", r.use ? pct(r.use.pct) : "–", r.use ? mah(r.use.mah) : ""],
      ["Where", FX.describe(r) || (fix ? "On the map" : "Not known"), fix ? "±" + FX.metres(fix.acc) + (fix.settled ? ", pinned" : "") : ""],
      ["Seen", FX.ago(r.t), r.mode === "live" ? "live" : r.mode === "locating" ? "pinning it down" : r.mode],
      ["Lying", r.pose ? POSE[r.pose] || r.pose : "–", [r.covered ? "covered" : "", r.lux != null && r.lux < 3 ? "dark" : ""].filter(Boolean).join(", ")],
      ["Still since", r.still ? FX.time(r.still) : "Moving", r.still ? FX.ago(r.still) : ""],
      ["Network", { wifi: "Wi-Fi", cell: "Mobile data", wired: "Wired" }[r.net] || r.net || "–", ""],
      ["Lost mode", r.lost ? "On" : "Off", r.ring ? "ringing now" : ""],
    ];
    out.append(h("div", { class: "m-grid" }, tiles.map(([k, v, s]) => h("div", { class: "m-tile" }, h("div", { class: "k" }, k), h("div", { class: "val" }, v, s ? h("small", {}, " " + s) : null)))));

    out.append(sec("Do"));
    const act = (ic, label, what, args, cls) => h("button", { class: "m-btn" + (cls ? " " + cls : ""), type: "button", onclick: (e) => busy(e.currentTarget, async () => {
      const res = await FX.source.act(id, what, args || {});
      ui.toast((res && (res.said || res.error)) || "Sent");
      setTimeout(() => ui.refresh().then(render), 1500);
    }) }, icon(ic), label);
    out.append(h("div", { class: "m-acts" },
      r.ring ? act("stop", "Stop", "stop") : act("bell", "Ring", "ring", {}, "red"),
      act("target", "Pin down", "locate"), act("live", "Live", "live", { ms: 10 * 60e3 }), act("radar", "Nearby", "beacon"),
      r.lost ? act("flag", "Found it", "found") : act("flag", "Lost", "lost"),
      fix ? h("a", { class: "m-btn", href: "https://www.google.com/maps/dir/?api=1&destination=" + fix.lat + "," + fix.lon, target: "_blank", rel: "noopener" }, icon("nav"), "Directions") : null,
      fix ? h("button", { class: "m-btn", type: "button", onclick: () => copy(fix.lat.toFixed(6) + ", " + fix.lon.toFixed(6), "Coordinates copied") }, icon("copy"), "Copy") : null,
      fix ? h("button", { class: "m-btn", type: "button", onclick: () => ui.flyTo(fix.lat, fix.lon, 17.5) }, icon("pin"), "Show") : null,
    ));
    out.append(h("div", { class: "m-note" }, "Ring gets through silent mode and Do Not Disturb. Nearby makes it call out over Bluetooth for 10 minutes, so your other devices can lead you to it. Lost mode shows your message on its lock screen."));

    if (r.cfg) {
      out.append(sec("This phone"));
      out.append(h("div", { class: "m-card" },
        row("Model", r.cfg.model + (r.cfg.android ? " · Android " + r.cfg.android : "")),
        row("FindX", r.cfg.v + (d && d.uwb ? " · Ultra-Wideband" : "")),
        row("History", r.cfg.history ? DETAIL[r.cfg.detail] + " detail" : "Off"),
        row("Findable nearby", r.cfg.beacon ? "On" : "Off")));
    }
    return out;
  }

  // ------------------------------------------------------------ battery: what FindX itself uses

  async function renderBattery(id, d, r) {
    const out = h("div");
    let s = await stats(id);
    if (!s) {
      out.append(h("div", { class: "m-wait" }, "Asking the phone for its battery figures…"));
      freshStats(id).then(() => view === "device" && tab === "battery" && render());
      return out;
    }
    const p = s.profile;
    const days = s.days.map(([t, u]) => [t, u]);
    const today = days[days.length - 1][1];
    const sel = days.find(([t]) => t === selDay) || days[days.length - 1];
    const tp = 100 * sum(energy(today, p)) / p.capacity;
    out.append(h("div", { class: "m-card", style: "padding:16px" },
      h("div", { class: "m-big" }, pct(tp)),
      h("div", { class: "m-muted" }, "of its battery today, since midnight · " + mah(sum(energy(today, p)))),
      h("div", { class: "m-note", style: "margin:6px 0 0" }, "Figures from " + FX.ago(s.t) + ". ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); out.replaceChildren(h("div", { class: "m-wait" }, "Asking the phone…")); freshStats(id).then(render); } }, "Get them now"))));

    out.append(sec("The last 14 days"));
    const dayLabel = (t) => new Date(t).toLocaleDateString([], { weekday: "narrow" });
    out.append(h("div", { class: "m-card", style: "padding:12px 14px" },
      bars(days, p, dayLabel, (t) => { selDay = t; render(); }, sel[0]),
      h("div", { class: "m-legend" }, PARTS.map((k) => h("span", {}, h("i", { class: "m-dot", style: "background:" + PART[k][1] }), PART[k][0])))));
    out.append(sec("Today, by the hour"));
    out.append(h("div", { class: "m-card", style: "padding:12px 14px" }, bars(s.hours, p, (t) => { const hr = new Date(t).getHours(); return hr % 6 === 0 ? String(hr) : ""; })));

    const isToday = sel[0] === days[days.length - 1][0];
    out.append(sec((isToday ? "Today" : new Date(sel[0]).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })) + ", part by part"));
    const parts = energy(sel[1], p), total = Math.max(1e-9, sum(parts));
    out.append(h("div", { class: "m-card" }, PARTS.map((k) => row(
      h("span", { style: "display:inline-flex;align-items:center;gap:8px" }, h("i", { class: "m-dot", style: "background:" + PART[k][1] }), PART[k][0]),
      measured(k, sel[1]), h("div", { class: "v" }, mah(parts[k]), h("small", {}, Math.round(100 * parts[k] / total) + "%"))))));
    out.append(h("div", { class: "m-note" },
      "Measured on the phone: processing time and network from its kernel, each radio wake-up, and how long satellites, Bluetooth, sensors and ringing were on. " +
      (p.source === "phone" ? "Turned into battery with the phone's own power profile. " : "Turned into battery with typical figures (this phone doesn't share its power profile). ") +
      "Battery: " + Math.round(p.capacity) + " mAh" + (p.capacitySource === "battery" ? ", from its charge counter." : ".")));
    return out;
  }

  // ------------------------------------------------------------ settings, sent to the phone

  async function renderSettings(id, d, r) {
    const out = h("div");
    const c = (r && r.cfg) || {};
    const name = h("input", { value: c.name || (d && d.name) || "", maxlength: "40" });
    const myName = h("input", { value: c.myName || "", maxlength: "30" });
    const msg = h("textarea", { maxlength: "200" });
    msg.value = c.lostMessage || "";
    let history = c.history !== false, beacon = c.beacon !== false, detail = c.detail == null ? 1 : c.detail;
    const chips = h("div", { class: "m-chips" });
    const drawChips = () => chips.replaceChildren(...DETAIL.map((label, i) => h("button", { type: "button", class: i === detail ? "on" : "", onclick: () => { detail = i; drawChips(); } }, label)));
    drawChips();
    out.append(sec("This phone"), h("div", { class: "m-card", style: "padding:14px" },
      h("label", { class: "m-field" }, h("span", {}, "Its name"), name),
      h("label", { class: "m-field" }, h("span", {}, "Your name in your circles"), myName),
      h("label", { class: "m-field", style: "margin:0" }, h("span", {}, "Lost mode's lock-screen message"), msg)));
    out.append(sec("History"), h("div", { class: "m-card" },
      row("Keep history", "Where it has been, kept on this DDrive for good", toggle(history, (v) => { history = v; })),
      h("div", { class: "m-row" }, h("div", { class: "t" }, h("div", { class: "n" }, "Detail while moving"), h("div", { class: "s" }, "Battery saver: Wi-Fi and mobile network only. Balanced: satellites every 8 seconds or so while it moves, on foot too. Detailed: satellites every 3 seconds while moving."), h("div", { style: "margin-top:8px" }, chips)))));
    out.append(sec("Bluetooth"), h("div", { class: "m-card" },
      row("Findable nearby", "A Bluetooth beacon only your devices recognise, about 1% battery a day", toggle(beacon, (v) => { beacon = v; }))));
    const save = h("button", { class: "m-btn red wide", type: "button", style: "margin-top:16px;width:100%" }, "Save on the phone");
    save.addEventListener("click", () => busy(save, async () => {
      const args = {};
      if (name.value.trim() && name.value.trim() !== c.name) args.name = name.value.trim();
      if (myName.value.trim() !== (c.myName || "")) args.myName = myName.value.trim();
      if (msg.value !== (c.lostMessage || "")) args.lostMessage = msg.value;
      if (history !== (c.history !== false)) args.history = history;
      if (detail !== (c.detail == null ? 1 : c.detail)) args.detail = detail;
      if (beacon !== (c.beacon !== false)) args.beacon = beacon;
      if (!Object.keys(args).length) { ui.toast("Nothing changed"); return; }
      await dash.command(id, "set", args);
      ui.toast("Sent: the phone changes it as soon as it hears");
      setTimeout(() => ui.refresh().then(render), 2500);
    }));
    out.append(save, h("div", { class: "m-note" }, "Turning FindX off, unlinking or erasing it can only be done on the phone, with its screen lock: so whoever gets into your DDrive still can't silence it."));
    return out;
  }

  // ------------------------------------------------------------ sharing: live-location links

  async function renderSharing(id) {
    const out = h("div");
    const s = await stats(id);
    out.append(sec("Live links"));
    const shares = ((s && s.shares) || []).filter((x) => !x.until || x.until > Date.now());
    out.append(h("div", { class: "m-card" }, shares.length ? shares.map((x) => {
      const link = dash.link(x);
      return h("div", { class: "m-row" }, h("div", { class: "t" },
        h("div", { class: "n" }, x.name || "Link"),
        h("div", { class: "s" }, (x.until ? "Until " + FX.day(x.until) + " " + FX.time(x.until) : "Until stopped") + (x.approx ? " · town only" : " · exact"))),
        h("button", { class: "m-btn small", type: "button", onclick: () => copy(link, "Link copied") }, "Copy"),
        h("button", { class: "m-btn small", type: "button", onclick: (e) => busy(e.currentTarget, async () => {
          await dash.unshare(id, x.id); ui.toast("Stopped"); statsCache.delete(id); setTimeout(render, 2500);
        }) }, "Stop"));
    }) : h("div", { class: "m-empty" }, s ? "No live links." : "Asking the phone…")));
    if (!s) freshStats(id).then(() => view === "device" && tab === "sharing" && render());

    const who = h("input", { placeholder: "Who it's for (only you see this)", maxlength: "40" });
    const LEN = [["15 min", 15 * 60e3], ["1 hour", 3600e3], ["8 hours", 8 * 3600e3], ["24 hours", 86400e3], ["7 days", 7 * 86400e3], ["Until I stop", 0]];
    let len = 1, approx = false;
    const lens = h("div", { class: "m-chips" }), exact = h("div", { class: "m-chips" });
    const draw = () => {
      lens.replaceChildren(...LEN.map(([l], i) => h("button", { type: "button", class: i === len ? "on" : "", onclick: () => { len = i; draw(); } }, l)));
      exact.replaceChildren(h("button", { type: "button", class: approx ? "" : "on", onclick: () => { approx = false; draw(); } }, "Exact"),
        h("button", { type: "button", class: approx ? "on" : "", onclick: () => { approx = true; draw(); } }, "Town only"));
    };
    draw();
    const result = h("div");
    const make = h("button", { class: "m-btn red wide", type: "button", style: "width:100%;margin-top:12px" }, "Make the link");
    make.addEventListener("click", () => busy(make, async () => {
      const link = await dash.share(id, who.value.trim(), LEN[len][1], approx);
      result.replaceChildren(h("div", { class: "m-card", style: "padding:12px 14px;margin-top:10px" },
        h("div", { class: "m-link" }, link), h("button", { class: "m-btn small", type: "button", style: "margin-top:8px", onclick: () => copy(link, "Link copied") }, "Copy")));
      statsCache.delete(id);
      ui.toast("Live link made");
    }));
    out.append(sec("New link"), h("div", { class: "m-card", style: "padding:14px" },
      h("label", { class: "m-field" }, h("span", {}, "For"), who),
      h("div", { class: "m-field" }, h("span", {}, "How long"), lens),
      h("div", { class: "m-field", style: "margin:0" }, h("span", {}, "How exact"), exact), make), result,
      h("div", { class: "m-note" }, "Anybody with the link sees this phone move on a map, live, and nothing of its history. The key is in the link after the #, so this DDrive passes the location on without being able to read it."));
    return out;
  }

  // ------------------------------------------------------------ activity

  async function renderActivity(id) {
    const out = h("div");
    const s = await stats(id);
    if (!s) { out.append(h("div", { class: "m-wait" }, "Asking the phone…")); freshStats(id).then(() => view === "device" && tab === "activity" && render()); return out; }
    out.append(sec("What it did lately"), h("div", { class: "m-card" },
      (s.log || []).length ? s.log.map(([t, text]) => row(text, FX.day(t) + " " + FX.time(t))) : h("div", { class: "m-empty" }, "Nothing yet.")));
    out.append(sec("History on this DDrive"), h("div", { class: "m-card" },
      row("Hours kept on the phone", String((s.history && s.history.hours) || 0)),
      row("Still to come to DDrive", String((s.history && s.history.pending) || 0) + " hours"),
      (s.spots || []).length ? row("Spots it knows", s.spots.join(", ")) : null));
    return out;
  }

  // ------------------------------------------------------------ circles

  async function renderCircles() {
    title.textContent = "People";
    sub.textContent = "Your circles: family and friends, live";
    setTabs([]);
    body.replaceChildren(h("div", { class: "m-wait" }, "Opening your circles…"));
    const list = await dash.circles(true).catch(() => []);
    const mine = dash.devicesList();
    const out = h("div");
    const phoneName = (dev) => (mine.find((x) => x.id === dev) || {}).name || "A phone of yours";
    for (const c of list) {
      const info = c.info || { name: c.name, places: [] };
      out.append(sec(info.name + (c.gone ? " (gone)" : "")));
      const card = h("div", { class: "m-card" });
      for (const p of c.people) {
        const r = p.report || {};
        card.append(h("div", { class: "m-row click", onclick: () => { ui.choose(p.id); if (innerWidth < 720) close(); } },
          face(p.id, p.name), h("div", { class: "t" }, h("div", { class: "n" }, p.name),
            h("div", { class: "s" }, [r.spot ? "At " + r.spot.name : r.fix ? "On the map" : "No position yet", r.t ? FX.ago(r.t) : "", r.bat >= 0 ? r.bat + "%" + (r.chg ? " charging" : "") : ""].filter(Boolean).join(" · "))),
          p.canRing ? h("button", { class: "m-btn small", type: "button", onclick: (e) => { e.stopPropagation(); busy(e.currentTarget, async () => { const res = await dash.ringPerson(p.id, "ring"); ui.toast(res.said || res.error); }); } }, icon("bell")) : null));
      }
      for (const m of c.mine) {
        card.append(row(phoneName(m.device) + " (yours)", "Let the circle ring it", toggle(m.canRing, (v) => dash.canRing(m.device, c.id, v).then(() => ui.toast(v ? "They can ring it" : "They can't ring it"), (e) => ui.toast(e.message)))));
      }
      if (!c.people.length) card.append(h("div", { class: "m-empty" }, "Nobody else yet: send them the invitation."));
      out.append(card);

      // Places.
      const places = (info.places || []).slice();
      const pl = h("div", { class: "m-card", style: "margin-top:8px" });
      const savePlaces = (next, said) => dash.setInfo(c, info.name, next).then(() => { ui.toast(said); render(); }, (e) => ui.toast(e.message));
      for (const p of places) pl.append(row(p.name, "Within " + Math.round(p.r) + " m", h("div", { style: "display:flex;gap:6px" },
        h("button", { class: "m-btn small", type: "button", onclick: () => ui.flyTo(p.lat, p.lon, 16.5) }, "Show"),
        h("button", { class: "m-btn small", type: "button", onclick: () => confirm("Remove " + p.name + "?") && savePlaces(places.filter((x) => x.id !== p.id), "Removed") }, "Remove"))));
      const pname = h("input", { placeholder: "A new place: Home, School, Work…", maxlength: "30" });
      let radius = 150;
      const radii = h("div", { class: "m-chips" });
      const drawR = () => radii.replaceChildren(...[100, 150, 300, 500].map((x) => h("button", { type: "button", class: x === radius ? "on" : "", onclick: () => { radius = x; drawR(); } }, x + " m")));
      drawR();
      const addAt = async (lat, lng) => {
        if (!pname.value.trim()) { ui.toast("Give the place a name first"); return; }
        await savePlaces(places.concat([{ id: FX.hex(crypto.getRandomValues(new Uint8Array(8))), name: pname.value.trim(), lat, lon: lng, r: radius }]), "Place added: everybody is told when somebody arrives or leaves");
      };
      const where = mine.filter((x) => x.report && x.report.fix);
      pl.append(h("div", { class: "m-row" }, h("div", { class: "t" }, pname,
        h("div", { style: "margin-top:8px" }, radii),
        h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-top:8px" },
          h("button", { class: "m-btn small", type: "button", onclick: () => { ui.toast("Click the map where it is"); if (innerWidth < 720) close(); ui.pick().then((ll) => { addAt(ll.lat, ll.lng); if (innerWidth < 720) openCircles(); }); } }, "Pick on the map"),
          where.slice(0, 3).map((x) => h("button", { class: "m-btn small", type: "button", onclick: () => addAt(x.report.fix.lat, x.report.fix.lon) }, "Where " + x.name + " is"))))));
      out.append(h("div", { class: "m-sec", style: "margin-top:12px" }, "Places of " + info.name), pl);

      // Invitation and leaving.
      out.append(h("div", { class: "m-card", style: "margin-top:8px" },
        row("Invitation", h("span", { class: "m-link" }, c.invite), h("button", { class: "m-btn small", type: "button", onclick: () => copy("Join my circle \"" + info.name + "\" on FindX: " + c.invite, "Invitation copied") }, "Copy")),
        c.mine.map((m) => row("Leave on " + phoneName(m.device), "That phone stops sharing with " + info.name,
          h("button", { class: "m-btn small", type: "button", onclick: (e) => confirm("Leave " + info.name + " on " + phoneName(m.device) + "?") && busy(e.currentTarget, async () => { await dash.leave(m.device, c.id); ui.toast("Left"); setTimeout(render, 3000); }) }, "Leave")))));
    }
    if (!list.length) out.append(h("div", { class: "m-card" }, h("div", { class: "m-empty" }, "No circles yet. A circle is family or friends who see where each other are, live, are told when somebody arrives home, and can ring each other's phones.")));

    // A new circle, or joining one.
    if (mine.length) {
      const nm = h("input", { value: "Family", maxlength: "40" });
      const sel = h("select", {}, mine.map((x) => h("option", { value: x.id }, x.name)));
      const result = h("div");
      const make = h("button", { class: "m-btn red wide", type: "button", style: "width:100%;margin-top:4px" }, "Make the circle");
      make.addEventListener("click", () => busy(make, async () => {
        const invite = await dash.createCircle(nm.value.trim() || "Family", sel.value);
        result.replaceChildren(h("div", { class: "m-card", style: "padding:12px 14px;margin-top:10px" },
          h("div", { class: "m-muted" }, "Made. " + (mine.find((x) => x.id === sel.value) || {}).name + " is joining it. Send the others this invitation:"),
          h("div", { class: "m-link", style: "margin-top:6px" }, invite),
          h("button", { class: "m-btn small", type: "button", style: "margin-top:8px", onclick: () => copy("Join my circle \"" + (nm.value.trim() || "Family") + "\" on FindX: " + invite, "Invitation copied") }, "Copy the invitation")));
        setTimeout(render, 6000);
      }));
      out.append(sec("New circle"), h("div", { class: "m-card", style: "padding:14px" },
        h("label", { class: "m-field" }, h("span", {}, "Its name"), nm),
        h("label", { class: "m-field" }, h("span", {}, "Your phone in it"), sel), make), result);

      const inv = h("input", { placeholder: "Paste an invitation you were sent" });
      const sel2 = h("select", {}, mine.map((x) => h("option", { value: x.id }, x.name)));
      const join = h("button", { class: "m-btn wide", type: "button", style: "width:100%" }, "Join with this phone");
      join.addEventListener("click", () => busy(join, async () => {
        const m = inv.value.trim().match(/https?:\/\/\S+\/j\/[0-9a-f]{32}#[A-Za-z0-9_-]{43}|findx:\/\/join\S+/);
        if (!m) { ui.toast("That isn't a FindX invitation"); return; }
        await dash.join(sel2.value, m[0]);
        ui.toast("Sent: the phone joins as soon as it hears");
        setTimeout(render, 6000);
      }));
      out.append(sec("Join a circle"), h("div", { class: "m-card", style: "padding:14px" },
        h("label", { class: "m-field" }, h("span", {}, "Invitation"), inv), h("label", { class: "m-field" }, h("span", {}, "Your phone"), sel2), join));
    }
    out.append(h("div", { class: "m-note" }, "Each circle has a key of its own, only in its invitation: this DDrive passes everything on without being able to read names, places or where anybody is."));
    body.replaceChildren(out);
  }

  // ------------------------------------------------------------ on the map page

  // "Manage" in the sheet, a warning when the phone can't be found as well as it could be, and "People" in the tools.
  const manage = h("button", { id: "manage-open", type: "button", style: "display:none", onclick: () => openDevice(ui.current().id, "overview") }, icon("gear"), "Manage this phone");
  const health = h("div", { id: "health", onclick: () => openDevice(ui.current().id, "overview") });
  $("credit").before(health, manage);
  const people = h("button", { id: "people-open", title: "People: your circles", type: "button", onclick: openCircles }, icon("people"));
  $("tools").prepend(people);

  function sync() {
    const { id, device, report } = ui.current();
    const mineDev = !!(id && !String(id).startsWith("c:") && dash.device(id));
    manage.style.display = mineDev ? "" : "none";
    const probs = mineDev && report && (report.ok || []).filter((p) => PROBLEMS[p] && p !== "beaconOff");
    if (probs && probs.length) {
      health.replaceChildren(h("span", {}, "⚠"), h("div", {}, h("b", {}, PROBLEMS[probs[0]][0]), " " + PROBLEMS[probs[0]][1] + (probs.length > 1 ? " (and " + (probs.length - 1) + " more)" : "")));
      health.classList.add("show");
    } else health.classList.remove("show");
    if (view === "device" && id !== deviceId && mineDev) { deviceId = id; render(); }
  }
  ui.on("device", sync);
  ui.on("report", () => { sync(); if (view === "device" && tab === "overview") render(); });
  FX.panel = { openDevice, openCircles, close };
  if (new URLSearchParams(location.search).has("people")) setTimeout(openCircles, 1500);
})();
