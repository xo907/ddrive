/*
 * FindX's map style: soft, quiet and clear, in the spirit of Apple Maps, drawn from OpenStreetMap data
 * (OpenFreeMap's free vector tiles, no account and no tracking).
 *
 * Every colour has a day and a night value. FX.style(t) builds the style for a daylight value t
 * (0 night, 1 day, in between at dusk and dawn), and FX.restyle(map, t) moves a map already showing to
 * a new t, fading over two seconds, so the map darkens with the evening where the phone is.
 */
(function () {
  const FX = (window.FX = window.FX || {});

  // [day, night]
  const P = {
    land: ['#f6f4ef', '#1b1e25'],
    landAlt: ['#efede6', '#20232b'],          // built-up areas, a touch deeper
    park: ['#d5ecc4', '#1d2e25'],
    wood: ['#c8e3b4', '#1b2b22'],
    grass: ['#ddefcf', '#1e2c25'],
    sand: ['#f3ead2', '#2a2924'],
    ice: ['#ffffff', '#2a2f38'],
    hospital: ['#fbe4e4', '#2c2226'],
    school: ['#f3ecd9', '#26252a'],
    water: ['#a9d4f7', '#16273d'],
    waterLine: ['#9ccbf2', '#1a2c45'],
    waterLabel: ['#3e7cb8', '#6b93c7'],
    aeroway: ['#e8e6ee', '#272a33'],
    building: ['#e9e6df', '#262932'],
    buildingTop: ['#e4e0d8', '#2b2f39'],
    buildingLine: ['#dcd8cf', '#30343e'],
    minor: ['#ffffff', '#353945'],
    minorCase: ['#e2ded5', '#272a33'],
    major: ['#ffffff', '#434857'],
    majorCase: ['#dcd5c4', '#2d303a'],
    highway: ['#fbd774', '#6b5a3a'],
    highwayCase: ['#e6b84f', '#4a3f2b'],
    path: ['#d9d4c8', '#3a3e49'],
    rail: ['#cfccc5', '#3c404b'],
    tunnel: ['#f1eee8', '#2a2d36'],
    boundary: ['#c7bfd6', '#4b4660'],
    label: ['#3a3a40', '#d6d7dd'],
    labelSoft: ['#6e6e76', '#9a9ca6'],
    halo: ['#ffffff', '#15171c'],
    road: ['#5b5b63', '#a5a8b3'],
    sky: ['#bcd9f2', '#0b1220'],
    horizon: ['#eef4fa', '#1c2638'],
  };

  function hex(c) { const n = parseInt(c.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function lin(v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
  function gam(v) { v = v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055; return Math.round(Math.max(0, Math.min(1, v)) * 255); }
  /** Mixes night and day in linear light (a straight mix of sRGB numbers turns muddy at dusk). */
  // Dusk and dawn aren't just halfway between day and night: they glow. A warm tint that is strongest
  // halfway through twilight and gone by full day or full night.
  const GLOW = [1.07, 0.97, 0.86];
  function mix(pair, t) {
    const a = hex(pair[1]), b = hex(pair[0]);
    const w = Math.max(0, 1 - Math.abs(2 * t - 1)) * 0.9;
    const c = a.map((x, i) => gam((lin(x) + (lin(b[i]) - lin(x)) * t) * (1 + (GLOW[i] - 1) * w)));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  FX.mix = mix;

  const NAME = ['coalesce', ['get', 'name:latin'], ['get', 'name']];
  const REG = ['Noto Sans Regular'], BOLD = ['Noto Sans Bold'], ITAL = ['Noto Sans Italic'];
  const line = ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false];
  const notBrunnel = ['match', ['get', 'brunnel'], ['bridge', 'tunnel'], false, true];
  const cls = (...c) => ['match', ['get', 'class'], c, true, false];
  const w = (...stops) => ['interpolate', ['exponential', 1.5], ['zoom'], ...stops];

  /** Which colour each layer's paint properties take: layer id -> {property: palette key}. */
  const THEMED = {};
  function L(layer, colours) {
    THEMED[layer.id] = colours;
    return layer;
  }

  function layers(t) {
    const c = (k) => mix(P[k], t);
    const fill = (id, srcLayer, filter, key, extra = {}) => L({
      id, type: 'fill', source: 'omt', 'source-layer': srcLayer, filter, paint: Object.assign({ 'fill-color': c(key), 'fill-antialias': true }, extra),
    }, { 'fill-color': key });

    const road = (id, filter, key, width, extra = {}, layout = {}) => L({
      id, type: 'line', source: 'omt', 'source-layer': 'transportation', filter,
      layout: Object.assign({ 'line-cap': 'round', 'line-join': 'round' }, layout),
      paint: Object.assign({ 'line-color': c(key), 'line-width': width }, extra),
    }, { 'line-color': key });

    const minorW = w(12.5, 0.5, 14, 2.4, 16, 6, 18, 16, 20, 34);
    const majorW = w(8, 0.6, 12, 2, 14, 4.5, 16, 9, 18, 22, 20, 44);
    const highW = w(5, 0.6, 9, 1.6, 12, 3, 14, 6, 16, 11, 18, 26, 20, 52);
    const caseW = (base) => ['*', base, 1.0];

    const out = [
      L({ id: 'land', type: 'background', paint: { 'background-color': c('land') } }, { 'background-color': 'land' }),
      fill('landuse-built', 'landuse', cls('residential', 'suburb', 'neighbourhood', 'commercial', 'industrial', 'retail'), 'landAlt', { 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.0, 12, 0.7] }),
      fill('landuse-hospital', 'landuse', cls('hospital'), 'hospital'),
      fill('landuse-school', 'landuse', cls('school', 'university', 'college', 'kindergarten'), 'school'),
      fill('landcover-wood', 'landcover', cls('wood', 'forest'), 'wood', { 'fill-opacity': 0.9 }),
      fill('landcover-grass', 'landcover', cls('grass', 'farmland', 'meadow', 'scrub'), 'grass', { 'fill-opacity': 0.75 }),
      fill('landcover-wetland', 'landcover', cls('wetland'), 'grass', { 'fill-opacity': 0.6 }),
      fill('landcover-sand', 'landcover', cls('sand', 'beach'), 'sand'),
      fill('landcover-ice', 'landcover', cls('ice', 'glacier'), 'ice'),
      fill('park', 'park', ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false], 'park'),
      fill('landuse-pitch', 'landuse', cls('pitch', 'playground', 'cemetery', 'garden'), 'park', { 'fill-opacity': 0.8 }),
      L({
        id: 'waterway', type: 'line', source: 'omt', 'source-layer': 'waterway', filter: notBrunnel,
        layout: { 'line-cap': 'round' }, paint: { 'line-color': c('waterLine'), 'line-width': w(8, 0.5, 13, 1.5, 18, 8) },
      }, { 'line-color': 'waterLine' }),
      fill('water', 'water', ['!=', ['get', 'brunnel'], 'tunnel'], 'water'),
      fill('aeroway-area', 'aeroway', ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false], 'aeroway', { 'fill-opacity': 0.9 }),
      L({
        id: 'aeroway-runway', type: 'line', source: 'omt', 'source-layer': 'aeroway', minzoom: 10, filter: cls('runway', 'taxiway'),
        paint: { 'line-color': c('aeroway'), 'line-width': w(10, 1, 14, 12, 18, 60) },
      }, { 'line-color': 'aeroway' }),

      // Tunnels: faint, under everything else.
      road('tunnel', ['all', line, ['==', ['get', 'brunnel'], 'tunnel'], cls('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor', 'service')],
        'tunnel', majorW, { 'line-opacity': 0.7, 'line-dasharray': [0.6, 0.4] }),

      // Paths.
      road('path', ['all', line, notBrunnel, cls('path', 'pedestrian', 'track')], 'path',
        w(14, 0.6, 16, 1.5, 18, 3.5), { 'line-dasharray': [1.2, 1.2] }),

      // Casings, then fills: minor, major, highways.
      road('minor-case', ['all', line, notBrunnel, cls('minor', 'service')], 'minorCase', w(12.5, 0.8, 14, 3.6, 16, 8, 18, 19, 20, 38),
        { 'line-opacity': ['interpolate', ['linear'], ['zoom'], 12.5, 0, 13.5, 1] }),
      road('major-case', ['all', line, notBrunnel, cls('primary', 'secondary', 'tertiary', 'trunk')], 'majorCase', w(8, 1, 12, 3, 14, 6.5, 16, 12, 18, 26, 20, 50)),
      road('highway-case', ['all', line, notBrunnel, cls('motorway')], 'highwayCase', w(5, 1, 9, 2.6, 12, 4.4, 14, 8, 16, 14, 18, 30, 20, 58)),
      road('minor', ['all', line, notBrunnel, cls('minor', 'service')], 'minor', minorW),
      road('major', ['all', line, notBrunnel, cls('primary', 'secondary', 'tertiary', 'trunk')], 'major', majorW),
      road('highway', ['all', line, notBrunnel, cls('motorway')], 'highway', highW),
      road('rail', ['all', line, notBrunnel, cls('rail', 'transit')], 'rail', w(12, 0.6, 16, 1.6, 20, 4), {}),

      // Bridges: a casing that hides what is below.
      road('bridge-case', ['all', line, ['==', ['get', 'brunnel'], 'bridge'], cls('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor', 'service')],
        'majorCase', w(12, 3, 14, 7, 16, 13, 18, 28, 20, 54)),
      road('bridge-minor', ['all', line, ['==', ['get', 'brunnel'], 'bridge'], cls('minor', 'service')], 'minor', minorW),
      road('bridge-major', ['all', line, ['==', ['get', 'brunnel'], 'bridge'], cls('primary', 'secondary', 'tertiary', 'trunk')], 'major', majorW),
      road('bridge-highway', ['all', line, ['==', ['get', 'brunnel'], 'bridge'], cls('motorway')], 'highway', highW),

      // Buildings: flat at first, then rising softly in 3D.
      L({
        id: 'building-flat', type: 'fill', source: 'omt', 'source-layer': 'building', minzoom: 13, maxzoom: 15.5,
        paint: { 'fill-color': c('building'), 'fill-outline-color': c('buildingLine'), 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0, 14, 1] },
      }, { 'fill-color': 'building', 'fill-outline-color': 'buildingLine' }),
      L({
        id: 'building-3d', type: 'fill-extrusion', source: 'omt', 'source-layer': 'building', minzoom: 15,
        paint: {
          'fill-extrusion-color': c('buildingTop'),
          'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], 15, 0, 16, ['coalesce', ['get', 'render_height'], 6]],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
          'fill-extrusion-opacity': 0.92, 'fill-extrusion-vertical-gradient': true,
        },
      }, { 'fill-extrusion-color': 'buildingTop' }),

      L({
        id: 'boundary', type: 'line', source: 'omt', 'source-layer': 'boundary', filter: ['all', ['<=', ['coalesce', ['get', 'admin_level'], 99], 4], ['!=', ['get', 'maritime'], 1]],
        paint: { 'line-color': c('boundary'), 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.6, 10, 1.6], 'line-dasharray': [3, 2] },
      }, { 'line-color': 'boundary' }),

      // Labels.
      L({
        id: 'water-name', type: 'symbol', source: 'omt', 'source-layer': 'water_name',
        layout: { 'text-field': NAME, 'text-font': ITAL, 'text-size': 13, 'text-max-width': 6, 'text-letter-spacing': 0.08 },
        paint: { 'text-color': c('waterLabel'), 'text-halo-color': c('halo'), 'text-halo-width': 1 },
      }, { 'text-color': 'waterLabel', 'text-halo-color': 'halo' }),
      L({
        id: 'road-name', type: 'symbol', source: 'omt', 'source-layer': 'transportation_name', minzoom: 13.5,
        filter: cls('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor', 'service'),
        layout: {
          'symbol-placement': 'line', 'text-field': NAME, 'text-font': REG, 'text-rotation-alignment': 'map',
          'text-size': ['interpolate', ['linear'], ['zoom'], 14, 10.5, 18, 14], 'text-letter-spacing': 0.02,
        },
        paint: { 'text-color': c('road'), 'text-halo-color': c('halo'), 'text-halo-width': 1.4 },
      }, { 'text-color': 'road', 'text-halo-color': 'halo' }),
      L({
        id: 'poi', type: 'symbol', source: 'omt', 'source-layer': 'poi', minzoom: 16,
        filter: ['all', ['<=', ['coalesce', ['get', 'rank'], 99], 14], cls('park', 'railway', 'bus', 'school', 'college', 'hospital', 'shop', 'restaurant', 'cafe', 'grocery', 'library', 'town_hall', 'museum', 'attraction')],
        layout: { 'text-field': NAME, 'text-font': REG, 'text-size': 11.5, 'text-max-width': 8, 'text-anchor': 'top' },
        paint: { 'text-color': c('labelSoft'), 'text-halo-color': c('halo'), 'text-halo-width': 1.2 },
      }, { 'text-color': 'labelSoft', 'text-halo-color': 'halo' }),
      L({
        id: 'place-small', type: 'symbol', source: 'omt', 'source-layer': 'place', minzoom: 11,
        filter: cls('suburb', 'neighbourhood', 'quarter', 'hamlet'),
        layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['interpolate', ['linear'], ['zoom'], 11, 11, 16, 14], 'text-transform': 'uppercase', 'text-letter-spacing': 0.12, 'text-max-width': 8 },
        paint: { 'text-color': c('labelSoft'), 'text-halo-color': c('halo'), 'text-halo-width': 1.2 },
      }, { 'text-color': 'labelSoft', 'text-halo-color': 'halo' }),
      L({
        id: 'place-town', type: 'symbol', source: 'omt', 'source-layer': 'place', minzoom: 6,
        filter: cls('town', 'village'),
        layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['interpolate', ['linear'], ['zoom'], 7, 11, 14, 16], 'text-max-width': 8 },
        paint: { 'text-color': c('label'), 'text-halo-color': c('halo'), 'text-halo-width': 1.4 },
      }, { 'text-color': 'label', 'text-halo-color': 'halo' }),
      L({
        id: 'place-city', type: 'symbol', source: 'omt', 'source-layer': 'place', minzoom: 3, maxzoom: 15,
        filter: cls('city'),
        layout: { 'text-field': NAME, 'text-font': BOLD, 'text-size': ['interpolate', ['exponential', 1.2], ['zoom'], 4, 12, 8, 15, 12, 20], 'text-max-width': 8 },
        paint: { 'text-color': c('label'), 'text-halo-color': c('halo'), 'text-halo-width': 1.6 },
      }, { 'text-color': 'label', 'text-halo-color': 'halo' }),
      L({
        id: 'place-region', type: 'symbol', source: 'omt', 'source-layer': 'place', maxzoom: 8,
        filter: cls('country', 'state'),
        layout: { 'text-field': NAME, 'text-font': BOLD, 'text-size': ['interpolate', ['linear'], ['zoom'], 2, 11, 6, 15], 'text-transform': 'uppercase', 'text-letter-spacing': 0.15, 'text-max-width': 7 },
        paint: { 'text-color': c('labelSoft'), 'text-halo-color': c('halo'), 'text-halo-width': 1.4 },
      }, { 'text-color': 'labelSoft', 'text-halo-color': 'halo' }),
    ];
    return out;
  }

  FX.style = function (t) {
    return {
      version: 8,
      name: 'FindX',
      glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
      sources: {
        omt: { type: 'vector', url: 'https://tiles.openfreemap.org/planet', attribution: '© <a href="https://openfreemap.org">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' },
      },
      sky: FX.sky(t),
      transition: { duration: 2000, delay: 0 },
      layers: layers(t),
    };
  };

  FX.sky = function (t) {
    return {
      'sky-color': mix(P.sky, t), 'horizon-color': mix(P.horizon, t), 'fog-color': mix(P.horizon, t),
      'sky-horizon-blend': 0.6, 'horizon-fog-blend': 0.7, 'fog-ground-blend': 0.6, 'atmosphere-blend': 0.8,
    };
  };

  /** Moves a showing map to daylight t. */
  FX.restyle = function (map, t) {
    for (const id in THEMED) {
      if (!map.getLayer(id)) continue;
      for (const prop in THEMED[id]) map.setPaintProperty(id, prop, mix(P[THEMED[id][prop]], t));
    }
    try { map.setSky(FX.sky(t)); } catch (e) { /* older engine */ }
  };

  FX.palette = P;
})();
