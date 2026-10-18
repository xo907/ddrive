/*
 * FindX in the browser: the same keys and sealed format as the phone (Secrets.kt), the sun's position
 * for day and night, and small helpers. Used by the map in the app, on DDrive's dashboard and on a
 * shared link's page. Nothing here sends anything anywhere.
 */
(function () {
  const FX = (window.FX = window.FX || {});
  const te = new TextEncoder(), td = new TextDecoder();

  FX.b64 = {
    dec(s) { const b = atob(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')); return Uint8Array.from(b, (c) => c.charCodeAt(0)); },
    enc(u) { let s = ''; u.forEach((x) => (s += String.fromCharCode(x))); return btoa(s); },
  };

  async function hmac(key, data) {
    const k = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return new Uint8Array(await crypto.subtle.sign('HMAC', k, data));
  }

  /** HKDF-SHA256 without salt, 32 bytes: as Seal.hkdf. */
  FX.hkdf = async function (key, info) {
    const prk = await hmac(new Uint8Array(32), key);
    const i = te.encode(info);
    const m = new Uint8Array(i.length + 1); m.set(i); m[i.length] = 1;
    return hmac(prk, m);
  };

  FX.keys = async function (wordsKey) {
    return {
      report: await FX.hkdf(wordsKey, 'findx v1 report'),
      history: await FX.hkdf(wordsKey, 'findx v1 history'),
      names: await FX.hkdf(wordsKey, 'findx v1 names'),
      command: await FX.hkdf(wordsKey, 'findx v1 command'),
      group: Array.from(await FX.hkdf(wordsKey, 'findx v1 group')).map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32),
    };
  };

  /** Opens nonce(12) + tag(16) + ciphertext with AES-256-GCM and a label. */
  FX.open = async function (key, label, blob) {
    const nonce = blob.slice(0, 12), tag = blob.slice(12, 28), ct = blob.slice(28);
    const joined = new Uint8Array(ct.length + 16); joined.set(ct); joined.set(tag, ct.length);
    const k = await crypto.subtle.importKey('raw', key, 'AES-GCM', false, ['decrypt']);
    return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce, additionalData: te.encode(label), tagLength: 128 }, k, joined));
  };

  FX.seal = async function (key, label, data) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const k = await crypto.subtle.importKey('raw', key, 'AES-GCM', false, ['encrypt']);
    const out = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: te.encode(label), tagLength: 128 }, k, data));
    const ct = out.slice(0, out.length - 16), tag = out.slice(out.length - 16);
    const blob = new Uint8Array(28 + ct.length); blob.set(nonce); blob.set(tag, 12); blob.set(ct, 28);
    return blob;
  };

  FX.openJson = async (key, label, blob) => JSON.parse(td.decode(await FX.open(key, label, blob)));

  FX.hex = (u) => Array.from(u, (b) => b.toString(16).padStart(2, '0')).join('');
  FX.unhex = (s) => Uint8Array.from(s.match(/../g) || [], (h) => parseInt(h, 16));

  /** A file name that gives nothing away (FxKeys.name): HMAC-SHA256 with the names key, as hex. */
  FX.name = async (namesKey, text, n = 32) => FX.hex(await hmac(namesKey, te.encode(text))).slice(0, n);

  /**
   * A circle's keys, from the key in its invite link (Circles.kt): what its members seal with, and the
   * token that shows the drive they were invited (the drive keeps only a hash of it).
   */
  FX.circleKeys = async (key) => ({
    seal: await FX.hkdf(key, 'findx circle v1 seal'),
    auth: FX.hex(await FX.hkdf(key, 'findx circle v1 auth')).slice(0, 32),
  });

  /** A soft colour of its own for each person, from their id. */
  FX.hue = (id) => { let h = 0; for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h % 360; };

  /** Opens a gzip (history segments are gzipped before they are sealed). */
  FX.gunzip = async (bytes) => new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());

  // ------------------------------------------------------------ the sun (as Sun in Geo.kt)

  const rad = Math.PI / 180;
  /** {altitude, azimuth} in degrees for a place and time (ms). Azimuth from north, clockwise. */
  FX.sun = function (lat, lon, t) {
    const d = t / 86400000 + 2440587.5 - 2451545.0;
    const g = ((357.529 + 0.98560028 * d) % 360) * rad;
    const q = (280.459 + 0.98564736 * d) % 360;
    const l = ((q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) % 360) * rad;
    const e = (23.439 - 0.00000036 * d) * rad;
    const ra = Math.atan2(Math.cos(e) * Math.sin(l), Math.cos(l));
    const dec = Math.asin(Math.sin(e) * Math.sin(l));
    const gmst = (18.697374558 + 24.06570982441908 * d) % 24;
    const ha = (gmst * 15 + lon) * rad - ra;
    const phi = lat * rad;
    const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha));
    const az = Math.atan2(Math.sin(ha), Math.cos(ha) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi));
    return { altitude: alt / rad, azimuth: (az / rad + 180 + 360) % 360 };
  };

  /** 0 at night, 1 in daylight, in between from the sun at -6° (end of dusk) to +3°. */
  FX.daylight = (lat, lon, t) => Math.max(0, Math.min(1, (FX.sun(lat, lon, t).altitude + 6) / 9));

  // ------------------------------------------------------------ words

  FX.ago = function (t, now = Date.now()) {
    const s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 10) return 'just now';
    if (s < 60) return s + ' s ago';
    const m = Math.round(s / 60);
    if (m < 60) return m + ' min ago';
    const h = Math.round(m / 60);
    if (h < 24) return h + ' h ago';
    return Math.round(h / 24) + ' d ago';
  };

  FX.metres = function (m) {
    if (m == null) return '';
    if (m < 1) return Math.round(m * 100) + ' cm';
    if (m < 10) return m.toFixed(1).replace(/\.0$/, '') + ' m';
    if (m < 1000) return Math.round(m) + ' m';
    return (m / 1000).toFixed(m < 10000 ? 1 : 0) + ' km';
  };

  // ------------------------------------------------------------ a path from positions

  /** Metres between two points. */
  FX.dist = function (aLat, aLon, bLat, bLon) {
    const r = Math.PI / 180;
    const h = Math.sin((bLat - aLat) * r / 2) ** 2 + Math.cos(aLat * r) * Math.cos(bLat * r) * Math.sin((bLon - aLon) * r / 2) ** 2;
    return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(h)));
  };

  /**
   * History points ([t, lat, lon, acc, until]) as the path really went: what the phone's PathFilter
   * (Geo.kt) does as positions come, done again here for history kept before it did. Rough positions
   * among good ones, spikes out and back, and jumps the path can't explain are left out; the rest is
   * weighed by accuracy (a Kalman filter on position and speed). Stays (until > t) are kept as they are
   * and start the path again: the phone was pinned down there.
   */
  FX.cleanPath = function (pts) {
    pts = pts.filter((p) => p[3] > 0 && (p[3] <= 150 || p[4] > p[0]));
    // Out and back: one far from both neighbours while they are close to each other.
    for (let pass = 0; pass < 2; pass++) {
      pts = pts.filter((b, i) => {
        if (i === 0 || i === pts.length - 1 || b[4] > b[0]) return true;
        const a = pts[i - 1], c = pts[i + 1];
        const ab = FX.dist(a[1], a[2], b[1], b[2]), bc = FX.dist(b[1], b[2], c[1], c[2]), ac = FX.dist(a[1], a[2], c[1], c[2]);
        return !(ab > 3 * ac + b[3] + 30 && bc > 3 * ac + b[3] + 30);
      });
    }
    const out = [];
    let o = null, t = 0, goodAt = 0, goodAcc = 0, doubted = [];
    const ax = { p: 0, v: 0, a: 0, b: 0, c: 0 }, ay = { p: 0, v: 0, a: 0, b: 0, c: 0 };
    const reset = (A, r) => { A.p = 0; A.v = 0; A.a = r; A.b = 0; A.c = 25; };
    const predict = (A, dt, q) => {
      const n = { ...A };
      n.p += n.v * dt; n.a += 2 * dt * n.b + dt * dt * n.c + q * dt ** 4 / 4; n.b += dt * n.c + q * dt ** 3 / 2; n.c += q * dt * dt;
      return n;
    };
    const update = (A, z, r) => {
      const s = A.a + r, k0 = A.a / s, k1 = A.b / s, y = z - A.p, a0 = A.a, b0 = A.b;
      A.p += k0 * y; A.v += k1 * y; A.a = (1 - k0) * a0; A.b = (1 - k0) * b0; A.c -= k1 * b0;
    };
    const local = (p) => [(p[2] - o[2]) * Math.PI / 180 * 6371008.8 * Math.cos(o[1] * Math.PI / 180), (p[1] - o[1]) * Math.PI / 180 * 6371008.8];
    const start = (p) => {
      o = p; t = p[0]; doubted = [];
      reset(ax, p[3] * p[3]); reset(ay, p[3] * p[3]);
      if (p[3] <= 25) { goodAt = p[0]; goodAcc = p[3]; }
      out.push(p);
    };
    for (const p of pts) {
      if (p[4] > p[0]) { start(p); t = Math.max(p[0], p[4]); continue; }
      if (!o || p[0] - t > 90e3) { start(p); continue; }
      if (p[0] <= t) continue;
      if (goodAt && p[0] - goodAt < 60e3 && p[3] > Math.max(60, 4 * goodAcc)) continue;
      const dt = (p[0] - t) / 1000, sp = Math.hypot(ax.v, ay.v), q = 0.05 + 0.02 * sp * sp, r = p[3] * p[3];
      const [e, n] = local(p);
      const px = predict(ax, dt, q), py = predict(ay, dt, q);
      const ix = e - px.p, iy = n - py.p;
      if ((ix * ix) / (px.a + r) + (iy * iy) / (py.a + r) > 13.8 && Math.hypot(ix, iy) > p[3] + 10) {
        doubted.push(p);
        const last3 = doubted.slice(-3);
        if (last3.length === 3 && last3.every((d, i) => d[3] <= 60 && (i === 0 ||
            FX.dist(last3[i - 1][1], last3[i - 1][2], d[1], d[2]) <= last3[i - 1][3] + d[3] + 50 * Math.max(1, (d[0] - last3[i - 1][0]) / 1000)))) start(p);
        continue;
      }
      doubted = [];
      Object.assign(ax, px); Object.assign(ay, py);
      update(ax, e, r); update(ay, n, r);
      t = p[0];
      if (p[3] <= 25) { goodAt = p[0]; goodAcc = p[3]; }
      const lat = o[1] + ay.p / 6371008.8 * 180 / Math.PI;
      const lon = o[2] + ax.p / (6371008.8 * Math.cos(o[1] * Math.PI / 180)) * 180 / Math.PI;
      out.push([p[0], lat, lon, Math.min(p[3], Math.sqrt(Math.max(ax.a, ay.a))), p[4] || 0]);
    }
    return out;
  };

  FX.time = (t) => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  FX.day = (t) => new Date(t).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

  FX.POSE = {
    FaceUp: 'Lying face up', FaceDown: 'Lying face down', Upright: 'Standing upright', OnSide: 'On its side', Tilted: 'Propped up at an angle',
  };

  /** What a report says about where the phone lies, in a few words. */
  FX.describe = function (r) {
    const parts = [];
    if (r.spot && r.spot.name) parts.push('At ' + r.spot.name);
    if (r.pose && FX.POSE[r.pose]) parts.push(FX.POSE[r.pose].toLowerCase());
    if (r.covered) parts.push('covered');
    else if (r.lux != null && r.lux < 3 && r.pose !== 'FaceDown') parts.push('in the dark');
    if (r.floor != null && Math.abs(r.floor) >= 0.7) parts.push(Math.abs(Math.round(r.floor)) + (Math.abs(Math.round(r.floor)) === 1 ? ' floor ' : ' floors ') + (r.floor > 0 ? 'up' : 'down'));
    let s = parts.join(', ');
    return s ? s[0].toUpperCase() + s.slice(1) : '';
  };
})();
