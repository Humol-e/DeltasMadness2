// Study-case and layer registry. The map has NO hardcoded case knowledge:
// everything is described via JSON (see README, "Case format" section).

const cases = new Map();
const LAYER_TYPES = ['fill', 'line', 'circle', 'raster'];
const BASE = import.meta.env.BASE_URL || '/';

export const resolveUrl = (p) => (/^https?:\/\//.test(p) ? p : BASE + p.replace(/^\//, ''));

/** Returns the list of validation issues for a case (empty if valid). */
export function validateCase(def) {
  const problems = [];
  if (!def || typeof def !== 'object') return ['Case definition must be an object'];
  if (!def.id) problems.push('Missing "id"');
  if (!def.title) problems.push('Missing "title"');
  if (!def.view || !Array.isArray(def.view.center)) problems.push('Missing "view.center" [lon, lat]');
  if (!Array.isArray(def.timeline) || def.timeline.length < 1) problems.push('"timeline" must contain at least 1 step');
  if (!Array.isArray(def.layers) || def.layers.length < 1) problems.push('"layers" must contain at least 1 layer');
  const seen = new Set();
  (def.layers || []).forEach((l, i) => {
    if (!l.id) problems.push(`Layer #${i}: missing "id"`);
    if (seen.has(l.id)) problems.push(`Duplicate layer "${l.id}"`);
    seen.add(l.id);
    if (!LAYER_TYPES.includes(l.type)) problems.push(`Layer "${l.id}": invalid type (${LAYER_TYPES.join(', ')})`);
    if (!l.source || !l.source.type) problems.push(`Layer "${l.id}": missing "source"`);
  });
  return problems;
}

/**
 * Registers a study case (also useful for adding cases programmatically).
 * Each layer supports: id, name, type, source, paint, [time], [steps], [legend],
 * [description], [popupFields], [attribution], [demo].
 */
export function registerCase(def) {
  const problems = validateCase(def);
  if (problems.length) throw new Error(`Invalid case "${def?.id ?? '?'}": ${problems.join('; ')}`);
  cases.set(def.id, def);
  return def;
}

/** Fetches GeoJSON data for each layer; a failing layer gets status "error" without breaking the rest. */
async function hydrate(def) {
  await Promise.all(def.layers.map(async (layer) => {
    layer.status = 'ok';
    if (layer.source.type !== 'geojson') return; // raster/vector tiles are loaded directly by Mapbox
    try {
      if (typeof layer.source.data === 'string') {
        const res = await fetch(resolveUrl(layer.source.data));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        layer.data = await res.json();
      } else {
        layer.data = layer.source.data; // inline GeoJSON
      }
      if (!layer.data || !Array.isArray(layer.data.features)) throw new Error('GeoJSON missing "features"');
    } catch (err) {
      layer.status = 'error';
      layer.error = err.message;
    }
  }));
  return def;
}

export async function loadCases(indexPath = 'data/cases/index.json') {
  const problems = [];
  const res = await fetch(resolveUrl(indexPath));
  if (!res.ok) throw new Error(`Failed to read ${indexPath} (HTTP ${res.status})`);
  const { cases: paths } = await res.json();
  await Promise.all(paths.map(async (p) => {
    try {
      const r = await fetch(resolveUrl(p));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      registerCase(await hydrate(await r.json()));
    } catch (err) {
      problems.push(`${p}: ${err.message}`);
    }
  }));
  // Preserve index order
  const ordered = paths.map((p) => [...cases.values()].find((c) => p.includes(`/${c.id}/`))).filter(Boolean);
  return { cases: ordered.length ? ordered : [...cases.values()], problems };
}

/** Whether the layer has features for this step (true when not applicable or unverifiable). */
export function layerHasData(layer, step) {
  if (layer.status !== 'ok') return false;
  if (layer.steps && !layer.steps.includes(step)) return true;
  if (layer.source.type !== 'geojson' || !layer.time) return true;
  const { property, mode } = layer.time;
  return layer.data.features.some((f) => {
    const v = f.properties?.[property];
    return mode === 'cumulative' ? v <= step : v === step;
  });
}
