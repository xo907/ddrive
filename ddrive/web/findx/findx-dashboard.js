"use strict";
/* FindX on the dashboard: the map's source of data (FX.source, see FindX's findx-source.js), for
 * whoever is signed in. Everything FindX keeps is sealed with keys only your phones and this browser
 * have, so it is all opened here:
 *
 * - the keys: /FindX/keys.fxk, a file your phones leave on the drive. With an end-to-end encrypted
 *   account it is sealed with the account's keys like every file (e2e-api.js opens it in this page,
 *   as the dashboard does); for the owner or an earlier account the drive has the words anyway.
 * - where your devices are now: the relay (/api/findx/devices), sealed reports opened here.
 * - where they have been: the hour files in /FindX/h, found by names made with the names key.
 * - "ring", "locate"...: sealed here with the command key, so only your phones can act on them.
 * - the people in your circles (/FindX/circles.fxc, which your phones keep: each circle's key, sealed
 *   with yours): their latest reports, opened with the circle's key, and "ring" for them.
 */
(function () {
  const FX = (window.FX = window.FX || {});
  const te = new TextEncoder();
  const HOUR = 3600e3;
  let keys = null;
  let devices = new Map();              // id -> {name, report, seen}
  let fetched = 0, watchingId = null, inflight = null;
  let circles = null;                   // [{id, name, member, me, seal, auth}] of this drive's circles
  const people = new Map();             // "c:<circle>:<member>" -> {name, group, report, seen}

  const post = (path, body) => fetch(path, {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-DD": "1" }, body: JSON.stringify(body),
  });

  class NoKeys extends Error {}

  async function getKeys() {
    if (keys) return keys;
    if (window.E2E && window.E2E.ready) await window.E2E.ready.catch(() => null);
    const r = await fetch("/api/file?path=" + encodeURIComponent("/FindX/keys.fxk"), { credentials: "same-origin" });
    if (r.status === 404) throw new NoKeys();
    if (r.status === 401) { location.replace("/"); throw new Error("signed out"); }
    if (!r.ok) throw new Error("FindX's keys couldn't be read (" + r.status + ")");
    const k = await r.json();
    keys = { report: FX.unhex(k.report), history: FX.unhex(k.history), names: FX.unhex(k.names), command: FX.unhex(k.command), group: k.group };
    return keys;
  }

  /** Your circles that live on this drive (a circle on another drive can't be asked from this page). */
  async function getCircles(k) {
    if (circles) return circles;
    circles = [];
    const r = await fetch("/api/file?path=" + encodeURIComponent("/FindX/circles.fxc"), { credentials: "same-origin" });
    if (!r.ok) return circles;
    try {
      const list = await FX.openJson(k.report, "findx circles", new Uint8Array(await r.arrayBuffer()));
      for (const c of list.circles || []) {
        if (c.host.replace(/\/+$/, "") !== location.origin) continue;
        const ck = await FX.circleKeys(FX.b64.dec(c.key));
        circles.push({ id: c.id, name: c.name || "Circle", member: c.member, me: c.myName || "", seal: ck.seal, auth: ck.auth });
      }
    } catch (e) { /* not opened: no circles */ }
    return circles;
  }

  async function refreshPeople(k, watch) {
    const got = new Map();
    for (const c of await getCircles(k)) {
      const watching = !!watch && watch.startsWith("c:" + c.id + ":");
      const r = await post("/api/findx/c/members", { circle: c.id, auth: c.auth, watch: watching }).catch(() => null);
      if (!r || !r.ok) continue;
      for (const m of (await r.json()).members || []) {
        if (m.member === c.member) continue;                 // that's you
        try {
          const rep = await FX.openJson(c.seal, "findx circle report", FX.b64.dec(m.blob));
          if (rep.m !== m.member) continue;
          const id = "c:" + c.id + ":" + m.member;
          got.set(id, {
            name: rep.p || rep.n || "Someone", group: c.name, seen: m.at, circle: c, member: m.member,
            report: { d: id, n: rep.p || rep.n, t: rep.t, fix: rep.fix, bat: rep.bat, chg: rep.chg, mode: rep.mode, still: rep.since,
                      spot: rep.place ? { name: rep.place.name } : undefined, ring: rep.ringing },
          });
        } catch (e) { /* not opened with this circle's key */ }
      }
    }
    people.clear();
    for (const [id, p] of got) people.set(id, p);
  }

  async function refresh(watch) {
    const k = await getKeys();
    await refreshPeople(k, watch).catch(() => null);
    const q = "/api/findx/devices?group=" + k.group + (watch ? "&watch=" + watch : "");
    const r = await fetch(q.replace(/&watch=c:[^&]*/, ""), { credentials: "same-origin" });
    if (!r.ok) throw new Error("DDrive answered " + r.status);
    const got = new Map();
    for (const d of (await r.json()).devices || []) {
      try {
        const card = await FX.openJson(k.report, "findx card", FX.b64.dec(d.card));
        if (card.d !== d.device) continue;                 // a card must be for the device it is filed under
        let report = null;
        if (d.report) {
          report = await FX.openJson(k.report, "findx report", FX.b64.dec(d.report)).catch(() => null);
          if (report && report.d !== d.device) report = null;
        }
        got.set(d.device, { name: card.n || "Device", model: card.model, uwb: !!card.uwb, report, seen: d.seen || d.at });
      } catch (e) { /* not opened with these keys: not ours */ }
    }
    devices = got;
    fetched = Date.now();
  }

  function fresh(watch) {
    const age = Date.now() - fetched;
    if (age < 1200 && (!watch || watch === watchingId)) return Promise.resolve();
    if (inflight) return inflight;
    watchingId = watch || watchingId;
    inflight = refresh(watchingId).finally(() => { inflight = null; });
    return inflight;
  }

  // ------------------------------------------------------------ history (Segments in FindX)

  function monthOf(hour) { const d = new Date(hour * HOUR); return d.getUTCFullYear() + "-" + (d.getUTCMonth() + 1); }

  async function listNames(folder) {
    const r = await fetch("/api/list?path=" + encodeURIComponent(folder), { credentials: "same-origin" });
    if (!r.ok) return null;
    return new Set(((await r.json()).items || []).map((i) => i.name));
  }

  async function history(id, from, to) {
    const k = await getKeys();
    const first = Math.floor(from / HOUR), last = Math.floor(to / HOUR);
    if (last - first > 24 * 62) return [];
    // Which hours there are: one listing per month (by its name), then only those files.
    const months = new Map();
    for (let h = first; h <= last; h++) { const m = monthOf(h); if (!months.has(m)) months.set(m, []); months.get(m).push(h); }
    const want = [];
    for (const [m, hours] of months) {
      const folder = "/FindX/h/" + (await FX.name(k.names, "m|" + id + "|" + m, 16));
      const there = await listNames(folder);
      if (!there) continue;
      for (const h of hours) {
        const file = (await FX.name(k.names, "h|" + id + "|" + h)) + ".fxh";
        if (there.has(file)) want.push(folder + "/" + file);
      }
    }
    const out = [];
    let i = 0;
    async function worker() {
      while (i < want.length) {
        const path = want[i++];
        try {
          const r = await fetch("/api/file?path=" + encodeURIComponent(path), { credentials: "same-origin" });
          if (!r.ok) continue;
          const seg = JSON.parse(new TextDecoder().decode(await FX.gunzip(await FX.open(k.history, "findx history", new Uint8Array(await r.arrayBuffer())))));
          if (seg.d !== id) continue;
          for (const p of seg.p) out.push([p[0] * 1000, p[1] / 1e6, p[2] / 1e6, p[3] / 10, p[4] * 1000]);
        } catch (e) { /* a damaged or foreign file: skipped */ }
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
    // A stay is written again as it grows: its latest line wins.
    const byT = new Map();
    for (const p of out.sort((a, b) => a[0] - b[0])) byT.set(p[0], p);
    return [...byT.values()].filter((p) => p[0] <= to && Math.max(p[0], p[4]) >= from);
  }

  // ------------------------------------------------------------ asking a phone

  async function act(id, what, args) {
    const k = await getKeys();
    if (id.startsWith("c:")) {
      const p = people.get(id);
      if (!p) return { error: "They aren't in the circle any more" };
      if (what !== "ring" && what !== "stop") return { error: "Only ringing, for people in your circles" };
      const cmd = { c: what, to: p.member, from: p.circle.member, fromName: p.circle.me || "DDrive", t: Date.now(), id: FX.hex(crypto.getRandomValues(new Uint8Array(8))), args: {} };
      const blob = await FX.seal(p.circle.seal, "findx circle command", te.encode(JSON.stringify(cmd)));
      const r = await post("/api/findx/c/command", { circle: p.circle.id, auth: p.circle.auth, from: p.circle.member, to: p.member, blob: FX.b64.enc(blob) });
      if (!r.ok) return { error: "DDrive answered " + r.status };
      return { said: what === "ring" ? "Ringing " + p.name + "'s phone (if they let the circle ring it)" : "Asked it to stop" };
    }
    if (what === "lost") {
      const message = prompt("Lost mode: a message for its lock screen (a number to call?)", "");
      if (message === null) return { said: "" };
      args = Object.assign({}, args, { message });
    }
    const cmd = { c: what, to: id, from: "web", t: Date.now(), id: FX.hex(crypto.getRandomValues(new Uint8Array(8))), args: args || {} };
    const blob = await FX.seal(k.command, "findx command", te.encode(JSON.stringify(cmd)));
    const r = await post("/api/findx/command", { group: k.group, to: id, blob: FX.b64.enc(blob) });
    if (!r.ok) return { error: "DDrive answered " + r.status };
    fetched = 0;
    return { said: { ring: "Ringing it now", stop: "Asked it to stop", locate: "Asked it to pin itself down", live: "Live for 10 minutes",
                     lost: "Lost mode is on", found: "Lost mode is off" }[what] || "Sent" };
  }

  // ------------------------------------------------------------ for the map

  FX.source = {
    caps: { act: true, history: true, pick: true, back: true },
    back: () => { location.href = "/"; },
    devices: async () => {
      try { await fresh(); }
      catch (e) {
        if (e instanceof NoKeys) FX.noKeys = true;
        return [];
      }
      return [...[...devices].map(([id, d]) => ({ id, name: d.name, seen: d.seen })),
              ...[...people].map(([id, p]) => ({ id, name: p.name, seen: p.seen, kind: "person", group: p.group }))];
    },
    initial: async () => {
      const want = new URLSearchParams(location.search).get("device");
      if (want && devices.has(want)) return { id: want };
      // The one heard from most recently.
      let best = null, at = -1;
      for (const [id, d] of devices) { const t = (d.report && d.report.t) || d.seen || 0; if (t > at) { at = t; best = id; } }
      return best ? { id: best } : {};
    },
    latest: async (id) => {
      await fresh(id).catch(() => null);
      if (id.startsWith("c:")) { const p = people.get(id); return p ? p.report : null; }
      const d = devices.get(id);
      return d ? d.report : null;
    },
    history: (id, from, to) => (id.startsWith("c:") ? Promise.resolve([]) : history(id, from, to)),
    act,
    watching: async (id, on) => { if (on) await fresh(id).catch(() => null); return {}; },
  };
})();
