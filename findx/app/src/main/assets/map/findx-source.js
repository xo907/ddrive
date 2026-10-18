/*
 * Where the map gets its data. The map only knows FX.source:
 *
 *   devices()               -> [{id, name, me}]
 *   latest(id)              -> report (Report.toJson on the phone) or null
 *   history(id, from, to)   -> [[t, lat, lon, acc, until], ...]
 *   act(id, what, args)     -> asks a device to ring / locate / go live / lost mode
 *   watching(id, on)        -> the map is showing that device live (so it sends more often)
 *   caps                    -> {act, history, pick}
 *
 * In the app it is the app (FindXApp, a bridge to Kotlin that answers asynchronously); with ?demo it is
 * a made-up walk, for trying the map in a browser. DDrive's dashboard and shared links bring their own
 * (they read the live relay and the sealed files with the keys from your words, in the browser).
 */
(function () {
  const FX = (window.FX = window.FX || {});

  // ------------------------------------------------------------ the app

  if (window.FindXApp) {
    let n = 0;
    const waiting = {};
    FX.reply = (id, json) => { const w = waiting[id]; if (!w) return; delete waiting[id]; try { w(JSON.parse(json)); } catch (e) { w(null); } };
    const ask = (method, args) => new Promise((resolve) => {
      const id = 'r' + ++n;
      waiting[id] = resolve;
      window.FindXApp.request(id, method, JSON.stringify(args || {}));
    });
    FX.source = {
      caps: { act: true, history: true, pick: true, back: true },
      back: () => ask('back'),
      devices: () => ask('devices'),
      latest: (id) => ask('latest', { id }),
      history: (id, from, to) => ask('history', { id, from, to }),
      act: (id, what, args) => ask('act', { id, what, args: args || {} }),
      watching: (id, on) => ask('watching', { id, on }),
      initial: () => ask('initial'),
    };
    return;
  }

  // ------------------------------------------------------------ a demo walk (?demo)

  if (/[?&]demo\b/.test(location.search)) {
    const home = [37.7955, -122.3937];                 // by the water in San Francisco
    const start = Date.now() - 3 * 3600e3;
    const route = [[37.7955, -122.3937], [37.7937, -122.3965], [37.7920, -122.3990], [37.7898, -122.4010], [37.7880, -122.4035],
      [37.7868, -122.4062], [37.7877, -122.4090], [37.7899, -122.4101], [37.7921, -122.4083], [37.7940, -122.4052], [37.7951, -122.4010], [37.7955, -122.3970]];
    function along(f) {
      const seg = Math.min(route.length - 2, Math.floor(f * (route.length - 1)));
      const u = f * (route.length - 1) - seg;
      const a = route[seg], b = route[seg + 1];
      return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
    }
    const hist = [[start, home[0], home[1], 4, start + 3600e3]];
    for (let i = 0; i <= 120; i++) { const p = along(i / 120); hist.push([start + 3600e3 + i * 40e3, p[0], p[1], 6, 0]); }
    hist.push([start + 3600e3 + 121 * 40e3, home[0], home[1], 3, Date.now() - 60e3]);
    const q = (u) => new URLSearchParams(location.search).get(u);
    FX.source = {
      caps: { act: true, history: true, pick: true },
      devices: async () => [{ id: 'a', name: 'Galaxy S24 Ultra', me: true }, { id: 'b', name: 'Tab S9' },
        { id: 'p1', name: 'Mom', kind: 'person', group: 'Family' }, { id: 'p2', name: 'Alex Rivera', kind: 'person', group: 'Family' }],
      latest: async (id) => {
        const t = Date.now();
        const walking = q('walk') != null;
        const f = walking ? ((t / 1000) % 600) / 600 : null;
        const PEOPLE = { p1: [37.7929, -122.3971], p2: [37.7978, -122.4062] };
        if (PEOPLE[id]) return { d: id, n: id === 'p1' ? 'Mom' : 'Alex Rivera', t: t - 5 * 60e3, fix: { lat: PEOPLE[id][0], lon: PEOPLE[id][1], acc: 12, t: t - 5 * 60e3 },
          spot: id === 'p1' ? { name: 'Work' } : undefined, bat: id === 'p1' ? 81 : 23, mode: 'standby' };
        const p = walking ? along(f) : id === 'a' ? home : [37.7990, -122.4010];
        const night = q('night') != null;
        return {
          d: id, n: id === 'a' ? 'Galaxy S24 Ultra' : 'Tab S9', t: night ? t : t - 40e3,
          fix: { lat: p[0], lon: p[1], acc: walking ? 5 : 1.4, t: t - 3000, settled: !walking, brg: walking ? 230 : undefined },
          pose: walking ? undefined : 'FaceDown', covered: false, lux: 1.2, spot: walking ? undefined : { name: 'Desk in the study', score: 0.86 },
          bat: id === 'a' ? 64 : 12, mode: walking ? 'live' : 'standby', still: walking ? 0 : t - 47 * 60e3,
        };
      },
      history: async () => hist,
      act: async (id, what) => ({ ok: true, said: what === 'ring' ? 'Ringing' : what === 'locate' ? 'Pinning it down' : 'Done' }),
      watching: async () => ({}),
      initial: async () => ({ id: 'a' }),
      forceNight: q('t') != null ? Number(q('t')) : q('night') != null ? 0 : q('day') != null ? 1 : null,
    };
    return;
  }

  FX.source = null;   // DDrive's dashboard or a shared link provides its own before this page's map starts
})();
