/*
 * The FindX map: where a phone is now (a dot that glides as it moves, inside a circle as wide as the
 * position is uncertain), where it has been (a trail that fades into the past, and the places it lay
 * still), and day or night as it is where the phone is.
 */
(function () {
  const FX = window.FX;
  const $ = (id) => document.getElementById(id);
  const src = FX.source;
  if (!src) return;
  const q = new URLSearchParams(location.search);
  if (q.get('top')) document.documentElement.style.setProperty('--inset-top', q.get('top') + 'px');
  if (q.get('bottom')) document.documentElement.style.setProperty('--inset-bottom', q.get('bottom') + 'px');
  if (src.caps.back) { $('back').hidden = false; $('back').onclick = () => src.back(); }
  window.addEventListener('error', (e) => console.error('FindX map: ' + e.message));

  const RED = '#ff2a2a';
  const state = {
    device: null, devices: [], report: null, span: 'now', history: [], follow: true, tilt: false,
    shown: null,             // [lat, lon] the dot is drawn at (moves smoothly toward the report)
    daylight: null, timer: null, scrubT: null,
  };

  const map = (FX.map = new maplibregl.Map({
    container: 'map', style: FX.style(1), center: [0, 20], zoom: 1.6, pitch: 0, attributionControl: false,
    maxPitch: 70, fadeDuration: 200, dragRotate: true,
  }));

  // ------------------------------------------------------------ the dot

  const el = document.createElement('div');
  el.className = 'fx-marker';
  el.innerHTML = '<div class="cone"></div><div class="pulse"></div><div class="core"></div>';
  const marker = new maplibregl.Marker({ element: el, rotationAlignment: 'map', pitchAlignment: 'map' });
  let markerOn = false;

  function circle(lat, lon, metres, steps = 72) {
    const pts = [];
    const dLat = metres / 111320;
    const dLon = metres / (111320 * Math.cos((lat * Math.PI) / 180));
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * 2 * Math.PI;
      pts.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
    }
    return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [pts] } };
  }

  const empty = { type: 'FeatureCollection', features: [] };

  function addOverlays() {
    if (map.getSource('acc')) return;
    map.addSource('acc', { type: 'geojson', data: empty });
    map.addSource('trail', { type: 'geojson', data: empty, lineMetrics: true });
    map.addSource('stays', { type: 'geojson', data: empty });
    map.addLayer({ id: 'trail-glow', type: 'line', source: 'trail', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': RED, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 6, 18, 16], 'line-opacity': 0.12, 'line-blur': 4 } });
    map.addLayer({ id: 'trail', type: 'line', source: 'trail', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 2.5, 18, 6],
        'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, 'rgba(255,42,42,0.15)', 0.6, 'rgba(255,42,42,0.6)', 1, 'rgba(255,42,42,1)'],
      } });
    map.addLayer({ id: 'stays', type: 'circle', source: 'stays',
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 17, ['+', 5, ['min', 6, ['/', ['get', 'mins'], 30]]]],
        'circle-color': '#ffffff', 'circle-stroke-color': RED, 'circle-stroke-width': 2.5,
      } });
    map.addLayer({ id: 'acc-fill', type: 'fill', source: 'acc', paint: { 'fill-color': RED, 'fill-opacity': 0.12 } });
    map.addLayer({ id: 'acc-line', type: 'line', source: 'acc', paint: { 'line-color': RED, 'line-opacity': 0.45, 'line-width': 1.2 } });
    map.on('click', 'stays', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ closeButton: false, offset: 10 }).setLngLat(e.lngLat)
        .setHTML(`<b>${FX.time(p.t)} – ${FX.time(p.until)}</b><br>${p.mins >= 60 ? Math.round(p.mins / 60) + ' h' : Math.round(p.mins) + ' min'} here`).addTo(map);
    });
    map.on('mouseenter', 'stays', () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', 'stays', () => (map.getCanvas().style.cursor = ''));
  }

  map.on('load', () => { addOverlays(); start(); });

  // ------------------------------------------------------------ day and night where the phone is

  function sky(lat, lon) {
    const now = state.scrubT || Date.now();
    let t = src.forceNight != null ? src.forceNight : FX.daylight(lat, lon, now);
    if (state.daylight != null && Math.abs(t - state.daylight) < 0.01) return;
    state.daylight = t;
    FX.restyle(map, t);
    map.once('idle', () => FX.restyle(map, state.daylight));   // again once drawn: a change made while the style settles can be lost
    document.body.classList.toggle('night', t < 0.5);
    // Light the 3D buildings from where the sun really is (or a soft moon-like light at night).
    const s = FX.sun(lat, lon, now);
    const polar = Math.max(20, Math.min(80, 90 - Math.max(5, s.altitude)));
    try {
      map.setLight({ anchor: 'map', position: [1.4, s.azimuth, polar], color: t > 0.5 ? '#fff6e8' : '#9fb4d6', intensity: t > 0.5 ? 0.42 : 0.25 });
    } catch (e) { /* fine without */ }
  }

  // ------------------------------------------------------------ the sheet

  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.classList.add('show');
    clearTimeout(toast.h);
    toast.h = setTimeout(() => t.classList.remove('show'), 2400);
  }

  function initials(name) { return (name || '?').trim().split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase(); }

  function showReport(r) {
    const dev = state.devices.find((d) => d.id === state.device) || {};
    $('name').textContent = (r && r.n) || dev.name || 'Your phone';
    $('avatar').textContent = initials((r && r.n) || dev.name);
    if (!r || !r.fix) {
      $('freshText').textContent = r ? 'No position yet' : 'Not heard from yet';
      $('place').textContent = 'Not on the map yet';
      $('detail').textContent = r ? 'It hasn’t found where it is. Try Pin down.' : '';
      $('acc').classList.add('hidden');
      $('empty').classList.toggle('show', !r);
      return;
    }
    $('empty').classList.remove('show');
    const age = Date.now() - r.t;
    const live = r.mode === 'live' && age < 30e3;
    $('dot').classList.toggle('live', live || r.mode === 'locating');
    $('freshText').textContent = r.mode === 'locating' ? 'Pinning it down…' : (live ? 'Live' : 'Seen ' + FX.ago(r.t)) + (r.ring ? ' · ringing' : '') + (r.lost ? ' · lost mode' : '');
    if (r.bat >= 0) { $('bat').hidden = false; $('bat').textContent = (r.chg ? '⚡ ' : '') + r.bat + '%'; $('bat').classList.toggle('low', r.bat <= 15 && !r.chg); }
    const d = FX.describe(r);
    $('place').textContent = d || (r.mode === 'live' || (r.fix.spd || 0) > 1 ? 'On the move' : 'Here');
    const bits = [];
    if (r.still) bits.push('Still since ' + FX.time(r.still));
    else if ((r.fix.spd || 0) > 0.8) bits.push(Math.round(r.fix.spd * 3.6) + ' km/h');
    bits.push(r.fix.lat.toFixed(6) + ', ' + r.fix.lon.toFixed(6));
    if (r.until !== undefined) bits.push(r.until ? 'Shared until ' + FX.day(r.until) + ' ' + FX.time(r.until) : 'Shared until they stop it');
    $('detail').textContent = bits.join(' · ');
    const acc = $('acc');
    acc.classList.remove('hidden');
    acc.textContent = (r.fix.settled ? 'Pinned to ±' : 'Within ±') + FX.metres(r.fix.acc);
    el.classList.toggle('stale', age > 15 * 60e3 && !live);
  }

  // ------------------------------------------------------------ moving the dot

  let anim = null;
  function moveTo(lat, lon, acc, bearing, fly) {
    if (!markerOn) { marker.setLngLat([lon, lat]).addTo(map); markerOn = true; state.shown = [lat, lon]; }
    const from = state.shown || [lat, lon];
    const t0 = performance.now();
    const dur = fly ? 0 : Math.min(1800, Math.max(300, 600));
    cancelAnimationFrame(anim);
    const step = (now) => {
      const u = dur === 0 ? 1 : Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - u, 3);
      const p = [from[0] + (lat - from[0]) * e, from[1] + (lon - from[1]) * e];
      state.shown = p;
      marker.setLngLat([p[1], p[0]]);
      const s = map.getSource('acc');
      if (s) s.setData(circle(p[0], p[1], acc));
      if (u < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
    if (bearing != null) { el.classList.add('heading'); marker.setRotation(bearing); } else el.classList.remove('heading');
    if (state.follow) {
      const z = map.getZoom();
      // Zoom so the circle fills a good part of the screen: a pinned phone gets close, a vague one doesn't.
      const want = Math.max(3, Math.min(17.6, Math.log2((40075016 * Math.cos((lat * Math.PI) / 180)) / Math.max(acc * 8, 30)) - 0.5));
      if (fly) map.flyTo({ center: [lon, lat], zoom: want, speed: 1.6, curve: 1.3, essential: true });
      else map.easeTo({ center: [lon, lat], zoom: Math.abs(want - z) > 1.5 ? want : z, duration: 900 });
    }
    sky(lat, lon);
  }

  // ------------------------------------------------------------ history

  function spanRange(span) {
    const now = Date.now();
    const d0 = new Date(); d0.setHours(0, 0, 0, 0);
    switch (span) {
      case 'today': return [d0.getTime(), now];
      case 'yesterday': return [d0.getTime() - 86400e3, d0.getTime()];
      case 'week': return [now - 7 * 86400e3, now];
      case 'month': return [now - 30 * 86400e3, now];
      default: return [now - 3 * 3600e3, now];       // "now": the last three hours as a short tail
    }
  }

  async function loadHistory() {
    const [from, to] = spanRange(state.span);
    const pts = (await src.history(state.device, from, to)) || [];
    state.history = pts.sort((a, b) => a[0] - b[0]);
    drawHistory(state.history);
    const scrub = $('scrub');
    scrub.classList.toggle('show', state.span !== 'now' && pts.length > 1);
    if (pts.length) { $('from').textContent = FX.time(pts[0][0]); $('to').textContent = FX.time(pts[pts.length - 1][Math.max(0, pts[pts.length - 1][4] ? 4 : 0)]); }
    $('slider').value = 1000;
    $('at').textContent = '';
    if (state.span !== 'now' && pts.length > 1) {
      const lons = pts.map((p) => p[2]), lats = pts.map((p) => p[1]);
      state.follow = false; $('follow').classList.remove('on');
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: { top: 110, bottom: 300, left: 50, right: 50 }, maxZoom: 17, duration: 900 });
    }
  }

  function drawHistory(pts) {
    const line = pts.map((p) => [p[2], p[1]]);
    if (state.report && state.report.fix && state.span === 'now') line.push([state.report.fix.lon, state.report.fix.lat]);
    map.getSource('trail') && map.getSource('trail').setData(line.length > 1 ? { type: 'Feature', geometry: { type: 'LineString', coordinates: line } } : empty);
    const stays = pts.filter((p) => p[4] > p[0] && p[4] - p[0] > 5 * 60e3).map((p) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [p[2], p[1]] }, properties: { t: p[0], until: p[4], mins: (p[4] - p[0]) / 60e3 },
    }));
    map.getSource('stays') && map.getSource('stays').setData({ type: 'FeatureCollection', features: stays });
  }

  $('slider').addEventListener('input', (e) => {
    const pts = state.history;
    if (pts.length < 2) return;
    const t0 = pts[0][0], t1 = Math.max(pts[pts.length - 1][0], pts[pts.length - 1][4] || 0);
    const t = t0 + ((t1 - t0) * e.target.value) / 1000;
    state.scrubT = e.target.value >= 1000 ? null : t;
    let i = pts.findIndex((p) => p[0] > t);
    if (i < 0) i = pts.length;
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i)];
    const u = b[0] === a[0] || (a[4] && t <= a[4]) ? 0 : Math.max(0, Math.min(1, (t - Math.max(a[0], a[4] || 0)) / (b[0] - Math.max(a[0], a[4] || 0))));
    const lat = a[1] + (b[1] - a[1]) * u, lon = a[2] + (b[2] - a[2]) * u;
    drawHistory(pts.filter((p) => p[0] <= t).concat([[t, lat, lon, a[3], 0]]));
    $('at').textContent = FX.day(t) + ' ' + FX.time(t);
    state.follow = false;
    moveTo(lat, lon, a[3], null, false);
    map.easeTo({ center: [lon, lat], duration: 200 });
  });

  // ------------------------------------------------------------ keeping it fresh

  async function refresh(first) {
    if (state.scrubT) return;
    const r = await src.latest(state.device);
    const prev = state.report;
    state.report = r;
    showReport(r);
    if (r && r.fix) {
      const moved = !prev || !prev.fix || prev.fix.lat !== r.fix.lat || prev.fix.lon !== r.fix.lon || prev.fix.acc !== r.fix.acc;
      if (moved || first) moveTo(r.fix.lat, r.fix.lon, r.fix.acc, r.fix.brg, first);
      if (state.span === 'now' && moved) drawHistory(state.history);
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    const r = state.report;
    // Asked often while it's live (the app answers from memory; nothing goes out for it), seldom otherwise.
    const ms = document.hidden ? 60e3 : r && (r.mode === 'live' || r.mode === 'locating') ? 1500 : 6000;
    state.timer = setTimeout(async () => { await refresh(false).catch(() => {}); schedule(); }, ms);
  }

  document.addEventListener('visibilitychange', () => {
    src.watching(state.device, !document.hidden);
    if (!document.hidden) { refresh(false); schedule(); }
  });

  async function choose(id, first) {
    state.device = id;
    state.report = null; state.daylight = state.daylight; state.scrubT = null;
    markerOn && marker.remove(); markerOn = false; state.shown = null;
    renderPicker();
    src.watching(id, true);
    await refresh(true);
    await loadHistory();
    schedule();
  }

  function renderPicker() {
    const p = $('picker');
    p.innerHTML = '';
    for (const d of state.devices) {
      const row = document.createElement('div');
      row.className = 'pick' + (d.id === state.device ? ' sel' : '');
      row.innerHTML = `<div class="a">${initials(d.name)}</div><div><div class="n"></div><div class="s">${d.me ? 'This phone' : d.seen ? 'Seen ' + FX.ago(d.seen) : ''}</div></div>`;
      row.querySelector('.n').textContent = d.name;
      row.onclick = () => { p.classList.remove('open'); if (d.id !== state.device) choose(d.id); };
      p.appendChild(row);
    }
  }

  async function start() {
    state.devices = (await src.devices()) || [];
    const init = (src.initial && (await src.initial())) || {};
    $('actions').classList.toggle('hidden', !src.caps.act);
    $('when').style.display = src.caps.history ? '' : 'none';
    const id = init.id || (state.devices.find((d) => d.me) || state.devices[0] || {}).id;
    if (!id) { $('emptyTitle').textContent = 'No devices yet'; $('emptyText').textContent = 'Devices linked to your DDrive with FindX show up here.'; $('empty').classList.add('show'); return; }
    if (init.span) { state.span = init.span; document.querySelectorAll('#when button').forEach((x) => x.classList.toggle('on', x.dataset.span === init.span)); }
    await choose(id, true);
  }

  /** A shared link that has ended (or never was): say so instead of a map. */
  FX.ended = function (text) {
    clearTimeout(state.timer);
    $('emptyTitle').textContent = text;
    $('emptyText').textContent = 'Whoever shared it can send you a new one.';
    $('empty').classList.add('show');
    $('sheet').style.display = 'none';
    markerOn && marker.remove(); markerOn = false;
  };

  // ------------------------------------------------------------ controls

  $('who').onclick = () => { if (src.caps.pick && state.devices.length > 1) $('picker').classList.toggle('open'); };
  $('follow').onclick = () => {
    state.follow = true; state.scrubT = null; $('follow').classList.add('on');
    const r = state.report;
    if (r && r.fix) moveTo(r.fix.lat, r.fix.lon, r.fix.acc, r.fix.brg, true);
  };
  map.on('dragstart', () => { state.follow = false; $('follow').classList.remove('on'); });
  $('tilt').onclick = () => {
    state.tilt = !state.tilt;
    $('tilt').classList.toggle('on', state.tilt);
    map.easeTo({ pitch: state.tilt ? 58 : 0, bearing: state.tilt ? -20 : 0, zoom: state.tilt ? Math.max(map.getZoom(), 16.2) : map.getZoom(), duration: 1100 });
  };
  document.querySelectorAll('#when button').forEach((b) => (b.onclick = () => {
    document.querySelectorAll('#when button').forEach((x) => x.classList.toggle('on', x === b));
    state.span = b.dataset.span; state.scrubT = null;
    if (state.span === 'now') { state.follow = true; $('follow').classList.add('on'); }
    loadHistory().then(() => refresh(state.span === 'now'));
  }));
  document.querySelectorAll('#actions button').forEach((b) => (b.onclick = async () => {
    const what = b.dataset.act;
    b.classList.add('busy');
    try {
      const res = await src.act(state.device, what, what === 'live' ? { ms: 10 * 60e3 } : {});
      toast((res && (res.said || res.error)) || 'Sent');
      if (what === 'locate' || what === 'live') setTimeout(() => refresh(false), 800);
    } finally { b.classList.remove('busy'); }
  }));
})();
