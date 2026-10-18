"use strict";
/* FindX on the dashboard: the map's source of data (FX.source, see FindX's findx-source.js) and what
 * the Manage panel (findx-panel.js) does (FX.dash), for whoever is signed in. Everything FindX keeps is
 * sealed with keys only your phones and this browser have, so it is all opened and sealed here:
 *
 * - the keys: /FindX/keys.fxk, a file your phones leave on the drive. With an end-to-end encrypted
 *   account it is sealed with the account's keys like every file (e2e-api.js opens it in this page,
 *   as the dashboard does); for the owner or an earlier account the drive has the words anyway.
 * - where your devices are now, how they're set up and how healthy: the relay (/api/findx/devices),
 *   sealed reports opened here. Their battery use, activity and live links: /FindX/s/<name>.fxs.
 * - where they have been: the hour files in /FindX/h, found by names made with the names key.
 * - "ring", "locate", settings, links...: sealed here with the command key, so only your phones can act.
 * - your circles (/FindX/c/<name>.fxc, one per phone: each circle's key, sealed with yours): their
 *   people, opened with the circle's key; ringing them; the circle's places.
 */
(function () {
  const FX = (window.FX = window.FX || {});
  const te = new TextEncoder();
  const HOUR = 3600e3;
  let keys = null;
  let devices = new Map();              // id -> {name, model, report, seen}
  let fetched = 0, watchingId = null, inflight = null;
  let circles = null, circlesAt = 0;    // [{id, key, name, seal, auth, mine: [{device, member, myName, canRing}]}]
  const people = new Map();             // "c:<circle>:<member>" -> {name, group, report, seen, circle, member}
  const newId = (n = 16) => FX.hex(crypto.getRandomValues(new Uint8Array(n)));
  const b64url = (u) => FX.b64.enc(u).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

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

  async function readFile(path) {
    const r = await fetch("/api/file?path=" + encodeURIComponent(path), { credentials: "same-origin" });
    return r.ok ? new Uint8Array(await r.arrayBuffer()) : null;
  }

  async function listNames(folder) {
    const r = await fetch("/api/list?path=" + encodeURIComponent(folder), { credentials: "same-origin" });
    if (!r.ok) return null;
    return ((await r.json()).items || []).map((i) => i.name);
  }

  // ------------------------------------------------------------ circles

  /** Your circles that live on this drive (a circle on another drive can't be asked from this page), from all your phones. */
  async function getCircles(k, again) {
    if (circles && !again && Date.now() - circlesAt < 60e3) return circles;
    const files = [];
    for (const n of (await listNames("/FindX/c")) || []) if (n.endsWith(".fxc")) files.push("/FindX/c/" + n);
    files.push("/FindX/circles.fxc");                      // (where FindX 0.1 kept them)
    const byId = new Map();
    for (const f of files) {
      const blob = await readFile(f).catch(() => null);
      if (!blob) continue;
      try {
        const list = await FX.openJson(k.report, "findx circles", blob);
        for (const c of list.circles || []) {
          if (String(c.host).replace(/\/+$/, "") !== location.origin) continue;
          let e = byId.get(c.id);
          if (!e) {
            const ck = await FX.circleKeys(FX.b64.dec(c.key));
            e = { id: c.id, key: c.key, name: c.name || "Circle", seal: ck.seal, auth: ck.auth, mine: [], made: false };
            byId.set(c.id, e);
          }
          if (c.mine) e.made = true;
          if (!e.mine.some((m) => m.member === c.member && m.device === c.device))
            e.mine.push({ device: c.device || "", member: c.member, myName: c.myName || "", canRing: c.canRing !== false });
        }
      } catch (e) { /* not opened: not ours */ }
    }
    circles = [...byId.values()];
    circlesAt = Date.now();
    return circles;
  }

  async function circleInfo(c) {
    const r = await post("/api/findx/c/info", { circle: c.id, auth: c.auth });
    if (!r.ok) return null;
    const o = await r.json();
    const info = await FX.openJson(c.seal, "findx circle info", FX.b64.dec(o.info));
    return { name: info.name || c.name, places: info.places || [], members: o.members };
  }

  async function refreshPeople(k, watch) {
    const got = new Map();
    for (const c of await getCircles(k)) {
      const watching = !!watch && watch.startsWith("c:" + c.id + ":");
      const r = await post("/api/findx/c/members", { circle: c.id, auth: c.auth, watch: watching }).catch(() => null);
      if (!r || !r.ok) continue;
      for (const m of (await r.json()).members || []) {
        if (c.mine.some((x) => x.member === m.member)) continue;       // one of your own phones: it's under Your devices
        try {
          const rep = await FX.openJson(c.seal, "findx circle report", FX.b64.dec(m.blob));
          if (rep.m !== m.member) continue;
          const id = "c:" + c.id + ":" + m.member;
          got.set(id, {
            name: rep.p || rep.n || "Someone", group: c.name, seen: m.at, circle: c, member: m.member, canRing: rep.canRing !== false,
            report: { d: id, n: rep.p || rep.n, t: rep.t, fix: rep.fix, bat: rep.bat, chg: rep.chg, mode: rep.mode, still: rep.since,
                      spot: rep.place ? { name: rep.place.name } : undefined, ring: rep.ringing },
          });
        } catch (e) { /* not opened with this circle's key */ }
      }
    }
    people.clear();
    for (const [id, p] of got) people.set(id, p);
  }

  // ------------------------------------------------------------ your devices

  async function refresh(watch) {
    const k = await getKeys();
    await refreshPeople(k, watch).catch(() => null);
    const q = "/api/findx/devices?group=" + k.group + (watch && !watch.startsWith("c:") ? "&watch=" + watch : "");
    const r = await fetch(q, { credentials: "same-origin" });
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
        got.set(d.device, { name: (report && report.cfg && report.cfg.name) || card.n || "Device", model: card.model, uwb: !!card.uwb, v: card.v, report, seen: d.seen || d.at });
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
      const there = new Set((await listNames(folder)) || []);
      if (!there.size) continue;
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
          const blob = await readFile(path);
          if (!blob) continue;
          const seg = JSON.parse(new TextDecoder().decode(await FX.gunzip(await FX.open(k.history, "findx history", blob))));
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

  async function command(id, what, args) {
    const k = await getKeys();
    const cmd = { c: what, to: id, from: "web", t: Date.now(), id: newId(8), args: args || {} };
    const blob = await FX.seal(k.command, "findx command", te.encode(JSON.stringify(cmd)));
    const r = await post("/api/findx/command", { group: k.group, to: id, blob: FX.b64.enc(blob) });
    if (!r.ok) throw new Error(r.status === 404 ? "That phone hasn't said hello to this DDrive lately" : "DDrive answered " + r.status);
    fetched = 0;
  }

  async function ringPerson(id, what) {
    const p = people.get(id);
    if (!p) return { error: "They aren't in the circle any more" };
    const me = p.circle.mine[0] || { member: "", myName: "" };
    const cmd = { c: what, to: p.member, from: me.member, fromName: me.myName || "DDrive", t: Date.now(), id: newId(8), args: {} };
    const blob = await FX.seal(p.circle.seal, "findx circle command", te.encode(JSON.stringify(cmd)));
    const r = await post("/api/findx/c/command", { circle: p.circle.id, auth: p.circle.auth, from: me.member, to: p.member, blob: FX.b64.enc(blob) });
    if (!r.ok) return { error: "DDrive answered " + r.status };
    return { said: what === "ring" ? (p.canRing ? "Ringing " + p.name + "'s phone" : p.name + " doesn't let the circle ring their phone") : "Asked it to stop" };
  }

  async function act(id, what, args) {
    if (id.startsWith("c:")) {
      if (what !== "ring" && what !== "stop") return { error: "Only ringing, for people in your circles" };
      return ringPerson(id, what);
    }
    if (what === "lost") {
      const d = devices.get(id);
      const had = (d && d.report && d.report.cfg && d.report.cfg.lostMessage) || "";
      const message = prompt("Lost mode: a message for its lock screen (a number to call?)", had);
      if (message === null) return { said: "" };
      args = Object.assign({}, args, { message });
    }
    try { await command(id, what, args); } catch (e) { return { error: e.message }; }
    return { said: { ring: "Ringing it now", stop: "Asked it to stop", locate: "Asked it to pin itself down", live: "Live for 10 minutes",
                     lost: "Lost mode is on", found: "Lost mode is off", beacon: "It calls out over Bluetooth for 10 minutes" }[what] || "Sent" };
  }

  // ------------------------------------------------------------ for the Manage panel

  FX.dash = {
    device: (id) => devices.get(id) || null,
    devicesList: () => [...devices].map(([id, d]) => ({ id, ...d })),
    person: (id) => people.get(id) || null,
    command,

    /** A phone's stats file: battery use by day and hour, what it did lately, its live links, spots. */
    async stats(id) {
      const k = await getKeys();
      const blob = await readFile("/FindX/s/" + (await FX.name(k.names, "s|" + id)) + ".fxs");
      if (!blob) return null;
      const s = await FX.openJson(k.report, "findx stats", blob);
      return s.d === id ? s : null;
    },

    /** A live-location link: made here (its key never leaves this browser and the phone), told to the relay and the phone. */
    async share(id, name, ms, approx) {
      const k = await getKeys();
      const share = { id: newId(16), key: b64url(crypto.getRandomValues(new Uint8Array(32))), name: name || "", until: ms ? Date.now() + ms : 0, approx: !!approx };
      const r = await post("/api/findx/share", { group: k.group, device: id, share: share.id, until: share.until });
      if (!r.ok) throw new Error("DDrive answered " + r.status);
      await command(id, "share", share);
      return location.origin + "/f/" + share.id + "#" + share.key;
    },
    async unshare(id, shareId) {
      const k = await getKeys();
      await post("/api/findx/unshare", { group: k.group, share: shareId });
      await command(id, "unshare", { id: shareId });
    },
    link: (s) => location.origin + "/f/" + s.id + "#" + s.key,

    async circles(again) {
      const k = await getKeys();
      const list = await getCircles(k, again);
      const out = [];
      for (const c of list) {
        const info = await circleInfo(c).catch(() => null);
        out.push({ ...c, info, gone: !info, invite: location.origin + "/j/" + c.id + "#" + c.key,
                   people: [...people].filter(([, p]) => p.circle.id === c.id).map(([pid, p]) => ({ id: pid, ...p })) });
      }
      return out;
    },

    /** Makes a circle on this drive, and has [device] join it. Returns the invitation for the others. */
    async createCircle(name, device) {
      const raw = crypto.getRandomValues(new Uint8Array(32));
      const c = { id: newId(16), key: b64url(raw) };
      const ck = await FX.circleKeys(raw);
      const info = FX.b64.enc(await FX.seal(ck.seal, "findx circle info", te.encode(JSON.stringify({ v: 1, name: name || "Family", places: [] }))));
      const r = await post("/api/findx/circle/create", { circle: c.id, auth: ck.auth, info });
      if (!r.ok) throw new Error("DDrive answered " + r.status);
      const invite = location.origin + "/j/" + c.id + "#" + c.key;
      await command(device, "join", { invite });
      circlesAt = 0;
      return invite;
    },

    /** Changes a circle's places (and name): sealed for everybody, and your phones in it look again at once. */
    async setInfo(c, name, places) {
      const info = FX.b64.enc(await FX.seal(c.seal, "findx circle info", te.encode(JSON.stringify({ v: 1, name, places }))));
      const r = await post("/api/findx/c/setinfo", { circle: c.id, auth: c.auth, info });
      if (!r.ok) throw new Error("DDrive answered " + r.status);
      for (const m of c.mine) if (m.device) await command(m.device, "circles", {}).catch(() => null);
    },
    join: (device, invite) => command(device, "join", { invite }),
    leave: (device, circleId) => command(device, "leave", { circle: circleId }).then(() => { circlesAt = 0; }),
    canRing: (device, circleId, on) => command(device, "circleRing", { circle: circleId, on }).then(() => { circlesAt = 0; }),
    ringPerson,
  };

  // ------------------------------------------------------------ for the map

  FX.source = {
    caps: { act: true, history: true, pick: true, back: true },
    back: () => { location.href = "/"; },
    devices: async () => {
      // Opening sealed things takes the browser's cryptography, which it only offers on https (or on this computer).
      if (!window.isSecureContext || !(window.crypto && crypto.subtle)) {
        FX.source.emptyTitle = "FindX needs https here";
        FX.source.emptyText = "Your phones' locations are sealed, and only opened in the browser, which browsers allow on https addresses (or on the computer running DDrive). Open this dashboard at its https address.";
        return [];
      }
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
