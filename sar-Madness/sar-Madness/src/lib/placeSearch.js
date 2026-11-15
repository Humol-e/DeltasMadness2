// Lightweight search across real cases, regional presets, coordinates, and global places.
// Zero external dependencies and 100% compatible with static builds without a backend.

const EARTH_RADIUS_KM = 6371;

/** Regional presets preconfigured in the `incendios` CLI (not yet published to catalog.json). */
export const EXPLORE_PRESETS = [
  {
    kind: 'preset',
    key: 'maui_lahaina',
    caseId: 'wildfire-maui-lahaina',
    title: 'Lahaina / Kula Fire — Maui, HI (2023)',
    region: 'Maui, Hawaii, USA',
    category: 'Wildfire (CLI Preset)',
    bbox: [-156.72, 20.82, -156.58, 20.94],
    before: '2023-07-10/2023-08-05',
    after: '2023-08-12/2023-09-05',
    description: 'Fast-moving coastal wildfire driven by strong winds on Maui.',
  },
  {
    kind: 'preset',
    key: 'jasper_ab',
    caseId: 'wildfire-jasper-ab',
    title: 'Jasper National Park Wildfire — Alberta (2024)',
    region: 'Canadian Rockies, Canada',
    category: 'Wildfire (CLI Preset)',
    bbox: [-118.22, 52.76, -117.92, 52.96],
    before: '2024-06-20/2024-07-18',
    after: '2024-08-08/2024-09-05',
    description: 'Boreal wildfire in subalpine conifers with high dNBR burn severity.',
  },
  {
    kind: 'preset',
    key: 'valparaiso_cl',
    caseId: 'wildfire-valparaiso-cl',
    title: 'Viña del Mar / Valparaíso (2024)',
    region: 'Central Zone, Chile',
    category: 'Wildfire (CLI Preset)',
    bbox: [-71.56, -33.12, -71.36, -32.98],
    before: '2024-01-05/2024-01-31',
    after: '2024-02-06/2024-03-01',
    description: 'Megafire across forest plantations, ravines, and the urban-wildland interface.',
  },
  {
    kind: 'preset',
    key: 'evros_gr',
    caseId: 'wildfire-evros-gr',
    title: 'Dadia / Alexandroupolis — Evros (2023)',
    region: 'Eastern Thrace, Greece',
    category: 'Wildfire (CLI Preset)',
    bbox: [25.75, 40.82, 26.35, 41.2],
    before: '2023-07-15/2023-08-15',
    after: '2023-09-02/2023-09-25',
    description: 'Large Mediterranean forest wildfire assessed via optical and SAR fusion.',
  },
  {
    kind: 'preset',
    key: 'tenerife_es',
    caseId: 'wildfire-tenerife-es',
    title: 'Corona Forestal, Tenerife (2023)',
    region: 'Canary Islands, Spain',
    category: 'Wildfire (CLI Preset)',
    bbox: [-16.58, 28.26, -16.34, 28.44],
    before: '2023-07-15/2023-08-12',
    after: '2023-08-26/2023-09-20',
    description: 'Canary Island pine forest on steep volcanic slopes.',
  },
  {
    kind: 'preset',
    key: 'tarahumara_mx',
    caseId: 'wildfire-tarahumara-mx',
    title: 'Sierra Tarahumara / Bocoyna (2024)',
    region: 'Chihuahua, Mexico',
    category: 'Wildfire (CLI Preset)',
    bbox: [-107.8, 27.6, -107.45, 27.9],
    before: '2024-03-01/2024-04-05',
    after: '2024-06-01/2024-06-30',
    description: 'Temperate pine-oak forest affected by severe drought.',
  },
  {
    kind: 'preset',
    key: 'pantanal_br',
    caseId: 'wildfire-pantanal-br',
    title: 'Northern Pantanal / Poconé (2024)',
    region: 'Mato Grosso, Brazil',
    category: 'Wildfire (CLI Preset)',
    bbox: [-56.95, -16.85, -56.45, -16.45],
    before: '2024-05-01/2024-06-15',
    after: '2024-09-10/2024-10-15',
    description: 'Extensive burning in a tropical wetland under persistent smoke.',
  },
  {
    kind: 'preset',
    key: 'los_alerces_ar',
    caseId: 'wildfire-los-alerces-ar',
    title: 'Los Alerces National Park / Esquel (2024)',
    region: 'Andean Patagonia, Argentina',
    category: 'Wildfire (CLI Preset)',
    bbox: [-71.75, -42.95, -71.45, -42.72],
    before: '2024-01-01/2024-01-23',
    after: '2024-02-12/2024-03-10',
    description: 'Andean Patagonian forest affected by summer wildfires.',
  },
];

const foldText = (s) =>
  String(s ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const centerFromBbox = ([w, s, e, n]) => [
  Number(((w + e) / 2).toFixed(5)),
  Number(((s + n) / 2).toFixed(5)),
];

/** Build a bounded WGS84 bounding box around `[lon, lat]`. */
export function buildPointBbox(lon, lat) {
  const cosLat = Math.max(0.25, Math.cos((lat * Math.PI) / 180));
  const dLon = clamp(0.08 / cosLat, 0.08, 0.25);
  const dLat = 0.07;
  return [
    Number(clamp(lon - dLon, -180, 180).toFixed(4)),
    Number(clamp(lat - dLat, -85, 85).toFixed(4)),
    Number(clamp(lon + dLon, -180, 180).toFixed(4)),
    Number(clamp(lat + dLat, -85, 85).toFixed(4)),
  ];
}

function normalizeBbox(rawBbox, center) {
  if (Array.isArray(rawBbox) && rawBbox.length === 4 && rawBbox.every(Number.isFinite)) {
    const [w, s, e, n] = rawBbox;
    if (w < e && s < n && Math.abs(e - w) >= 0.02 && Math.abs(n - s) >= 0.02) {
      return [
        Number(clamp(w, -180, 180).toFixed(4)),
        Number(clamp(s, -85, 85).toFixed(4)),
        Number(clamp(e, -180, 180).toFixed(4)),
        Number(clamp(n, -85, 85).toFixed(4)),
      ];
    }
  }
  return buildPointBbox(center[0], center[1]);
}

/**
 * Parse direct coordinate queries:
 * - `"lat, lon"` (e.g. `"19.4326, -99.1332"`)
 * - `"min_lon, min_lat, max_lon, max_lat"` (e.g. `"-107.8, 27.6, -107.45, 27.9"`)
 */
export function parseCoordinates(query) {
  const cleaned = String(query ?? '').trim();
  if (!cleaned || /[a-z]/i.test(cleaned)) return null;
  const parts = cleaned
    .split(/[\s,;]+/)
    .filter(Boolean)
    .map(Number);
  if (parts.some((n) => !Number.isFinite(n))) return null;

  if (parts.length === 2) {
    const [lat, lon] = parts;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    const center = [Number(lon.toFixed(5)), Number(lat.toFixed(5))];
    return {
      kind: 'coords',
      id: `coords:${lat.toFixed(4)},${lon.toFixed(4)}`,
      title: `Coordinates ${lat.toFixed(4)}°, ${lon.toFixed(4)}°`,
      subtitle: 'WGS84 Geographic Point · 3D Explore',
      center,
      bbox: buildPointBbox(center[0], center[1]),
      source: 'WGS84',
    };
  }

  if (parts.length === 4) {
    const [w, s, e, n] = parts;
    if (w < -180 || e > 180 || s < -90 || n > 90 || w >= e || s >= n) return null;
    const bbox = [w, s, e, n].map((x) => Number(x.toFixed(4)));
    return {
      kind: 'coords',
      id: `bbox:${bbox.join(',')}`,
      title: `BBox [${bbox.join(', ')}]`,
      subtitle: 'WGS84 Bounding Box · 3D Explore',
      center: centerFromBbox(bbox),
      bbox,
      source: 'WGS84 BBox',
    };
  }

  return null;
}

/** Compute surface distance in kilometers between two `[lon, lat]` points. */
export function haversineKm([lon1, lat1], [lon2, lat2]) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Find the closest validated real case to `[lon, lat]`. */
export function findNearestCase(center, cases) {
  if (!center || !Array.isArray(cases) || !cases.length) return null;
  let best = null;
  for (const item of cases) {
    const c = item?.view?.center;
    if (!Array.isArray(c) || c.length !== 2) continue;
    const distanceKm = haversineKm(center, c);
    if (!best || distanceKm < best.distanceKm) best = { item, distanceKm };
  }
  return best;
}

/** Instant offline search across published real cases and CLI presets. */
export function searchLocalMatches(query, cases = []) {
  const q = foldText(query);
  if (!q) return [];
  const tokens = q.split(/\s+/).filter(Boolean);
  const matchesAll = (haystack) => {
    const text = foldText(haystack);
    return tokens.every((t) => text.includes(t));
  };

  const realHits = [];
  for (const c of cases) {
    const hay = [
      c.id,
      c.title,
      c.subtitle,
      c.description,
      c.region,
      c.country,
      c.category,
    ].join(' ');
    if (matchesAll(hay)) {
      realHits.push({
        kind: 'case',
        id: c.id,
        title: c.title,
        subtitle: c.region || c.subtitle || 'Pre-validated real case',
        center: c.view?.center,
      });
    }
  }

  const publishedIds = new Set(cases.map((c) => c.id));
  const presetHits = [];
  for (const p of EXPLORE_PRESETS) {
    if (publishedIds.has(p.caseId)) continue;
    const hay = [p.key, p.title, p.region, p.description, 'wildfire', 'fire'].join(' ');
    if (matchesAll(hay)) {
      presetHits.push({
        ...p,
        id: `preset:${p.key}`,
        subtitle: `${p.region} · ${p.before} vs ${p.after}`,
        center: centerFromBbox(p.bbox),
      });
    }
  }

  return [...realHits, ...presetHits];
}

async function geocodeMapbox(query, token, signal) {
  const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(query)}.json?limit=5&language=en&access_token=${encodeURIComponent(token)}`;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Mapbox HTTP ${res.status}`);
  const data = await res.json();
  return (data.features ?? []).map((f, idx) => {
    const center = [Number(f.center[0].toFixed(5)), Number(f.center[1].toFixed(5))];
    return {
      kind: 'place',
      id: f.id || `mb-${idx}`,
      title: f.text || f.place_name,
      subtitle: f.place_name,
      center,
      bbox: normalizeBbox(f.bbox, center),
      source: 'Mapbox Geocoding',
    };
  });
}

async function geocodeNominatim(query, signal) {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&accept-language=en&q=${encodeURIComponent(query)}`;
  const res = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`Nominatim HTTP ${res.status}`);
  const rows = await res.json();
  return (rows ?? []).map((r) => {
    const lon = Number(Number(r.lon).toFixed(5));
    const lat = Number(Number(r.lat).toFixed(5));
    const center = [lon, lat];
    // Nominatim returns boundingbox as stringified [south, north, west, east].
    const bb = Array.isArray(r.boundingbox) && r.boundingbox.length === 4
      ? [Number(r.boundingbox[2]), Number(r.boundingbox[0]), Number(r.boundingbox[3]), Number(r.boundingbox[1])]
      : null;
    const parts = String(r.display_name || '').split(',').map((s) => s.trim());
    return {
      kind: 'place',
      id: `osm-${r.place_id}`,
      title: parts[0] || r.name || query,
      subtitle: parts.slice(1).join(', ') || `${lat.toFixed(4)}, ${lon.toFixed(4)}`,
      center,
      bbox: normalizeBbox(bb, center),
      source: 'OpenStreetMap Nominatim',
    };
  });
}

/** Geocode places using Mapbox Geocoding (if token is present) with free Nominatim fallback. */
export async function geocodePlaces(query, { token = '', signal } = {}) {
  const q = String(query ?? '').trim();
  if (q.length < 3) return [];
  if (token) {
    try {
      const hits = await geocodeMapbox(q, token, signal);
      if (hits.length) return hits;
    } catch (err) {
      if (err?.name === 'AbortError') throw err;
    }
  }
  return geocodeNominatim(q, signal);
}

/** Build ready-to-run CLI commands to process a region in `incendios` or `hola`. */
export function buildCliCommands({ key, bbox }) {
  const bboxStr = (bbox ?? []).map((n) => Number(n).toFixed(4)).join(',');
  const wildfireCmd = key
    ? `wildfire-stage1 run-wildfire --aoi ${key} && wildfire-stage1 publish-madness`
    : `wildfire-stage1 run-wildfire --aoi "${bboxStr}" --before 2025-01-01/2025-02-01 --after 2025-02-15/2025-03-31 && wildfire-stage1 publish-madness`;
  const glacierCmd = `glacier-stage1 publish-madness --aoi "${bboxStr}"`;
  return { bboxStr, wildfireCmd, glacierCmd };
}
