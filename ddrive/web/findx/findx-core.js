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
