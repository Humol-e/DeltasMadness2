// Registro de casos de estudio y capas. El mapa NO conoce ningún caso concreto:
// todo se describe con JSON (ver README, sección "Formato de caso").

const cases = new Map();
const LAYER_TYPES = ['fill', 'line', 'circle', 'raster'];
const BASE = import.meta.env.BASE_URL || '/';

export const resolveUrl = (p) => (/^https?:\/\//.test(p) ? p : BASE + p.replace(/^\//, ''));

/** Devuelve la lista de problemas de un caso (vacía si es válido). */
export function validateCase(def) {
  const problems = [];
  if (!def || typeof def !== 'object') return ['El caso no es un objeto'];
  if (!def.id) problems.push('Falta "id"');
  if (!def.title) problems.push('Falta "title"');
  if (!def.view || !Array.isArray(def.view.center)) problems.push('Falta "view.center" [lon, lat]');
  if (!Array.isArray(def.timeline) || def.timeline.length < 1) problems.push('"timeline" debe tener al menos 1 paso');
  if (!Array.isArray(def.layers) || def.layers.length < 1) problems.push('"layers" debe tener al menos 1 capa');
  const seen = new Set();
  (def.layers || []).forEach((l, i) => {
    if (!l.id) problems.push(`Capa #${i}: falta "id"`);
    if (seen.has(l.id)) problems.push(`Capa "${l.id}" duplicada`);
    seen.add(l.id);
    if (!LAYER_TYPES.includes(l.type)) problems.push(`Capa "${l.id}": type inválido (${LAYER_TYPES.join(', ')})`);
    if (!l.source || !l.source.type) problems.push(`Capa "${l.id}": falta "source"`);
  });
  return problems;
}

/**
 * Registra un caso (también sirve para añadir casos desde código).
 * Cada capa trae: id, name, type, source, paint, [time], [steps], [legend],
 * [description], [popupFields], [attribution], [demo].
 */
export function registerCase(def) {
  const problems = validateCase(def);
  if (problems.length) throw new Error(`Caso "${def?.id ?? '?'}" inválido: ${problems.join('; ')}`);
  cases.set(def.id, def);
  return def;
}

/** Descarga los GeoJSON de cada capa; una capa que falla queda con status "error" sin romper el resto. */
async function hydrate(def) {
  await Promise.all(def.layers.map(async (layer) => {
    layer.status = 'ok';
    if (layer.source.type !== 'geojson') return; // raster/vector tiles los carga Mapbox directamente
    try {
      if (typeof layer.source.data === 'string') {
        const res = await fetch(resolveUrl(layer.source.data));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        layer.data = await res.json();
      } else {
        layer.data = layer.source.data; // GeoJSON inline
      }
      if (!layer.data || !Array.isArray(layer.data.features)) throw new Error('GeoJSON sin "features"');
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
  if (!res.ok) throw new Error(`No se pudo leer ${indexPath} (HTTP ${res.status})`);
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
  // Mantiene el orden del índice
  const ordered = paths.map((p) => [...cases.values()].find((c) => p.includes(`/${c.id}/`))).filter(Boolean);
  return { cases: ordered.length ? ordered : [...cases.values()], problems };
}

/** ¿La capa tiene datos en este paso? (true si no aplica o no se puede comprobar) */
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
