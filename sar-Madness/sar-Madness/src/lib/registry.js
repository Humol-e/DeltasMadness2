// Carga del catálogo y de los casos (esquema v2). El motor del mapa no sabe nada de casos concretos.
import { parseJsonc, validateCase, normalizeCase, toMs } from './schema.js';
import { buildAxis } from './timeline.js';

const BASE = import.meta.env.BASE_URL || '/';
export const resolveUrl = (p) => (/^(https?:)?\/\//.test(p) || p.startsWith('data:') ? p : BASE + p.replace(/^\//, ''));

async function fetchJson(path) {
  const res = await fetch(resolveUrl(path));
  if (!res.ok) throw new Error(res.status === 429 ? 'HTTP 429 (demasiadas peticiones al servidor)' : `HTTP ${res.status}`);
  try {
    return parseJsonc(await res.text());
  } catch {
    throw new Error('el archivo no es JSON válido o no existe (revisa la ruta)');
  }
}

/** Descarga los GeoJSON de los vectores. Un vector que falla queda marcado y no rompe el resto. */
async function hydrateVectors(def) {
  await Promise.all(def.vectors.map(async (v) => {
    v._load = 'ok';
    try {
      v.data = typeof v.source.data === 'string' ? await fetchJson(v.source.data) : v.source.data;
      if (!v.data || !Array.isArray(v.data.features)) throw new Error('GeoJSON sin "features"');
      if (v.time) {
        const set = new Set(v.data.features.map((f) => f.properties?.[v.time.property]).filter((x) => x != null));
        v._dates = [...set].filter((s) => !Number.isNaN(toMs(s))).sort((a, b) => toMs(a) - toMs(b));
      }
    } catch (err) {
      v._load = 'error';
      v._error = err.message;
    }
  }));
}

/** Lee catalog.json → casos validados y normalizados. No reintenta en bucle: devuelve los problemas. */
export async function loadCatalog(path = 'data/catalog.json') {
  const catalog = await fetchJson(path);
  const problems = [];
  const warnings = [];
  const loaded = [];
  await Promise.all((catalog.cases || []).map(async (p, i) => {
    try {
      const def = await fetchJson(p);
      const { errors, warnings: w } = validateCase(def);
      if (errors.length) throw new Error(errors.join('; '));
      w.forEach((x) => warnings.push(`${def.id}: ${x}`));
      normalizeCase(def);
      await hydrateVectors(def);
      def.axis = buildAxis(def);
      loaded[i] = def;
    } catch (err) {
      problems.push(`${p}: ${err.message}`);
    }
  }));
  return { cases: loaded.filter(Boolean), problems, warnings };
}
