// Catalog and case loader (Schema v2). The map controller stays decoupled from specific cases.
import { parseJsonc, validateCase, normalizeCase, toMs } from './schema.js';
import { buildAxis } from './timeline.js';

const BASE = import.meta.env.BASE_URL || '/';
export const resolveUrl = (p) => (/^(https?:)?\/\//.test(p) || p.startsWith('data:') ? p : BASE + p.replace(/^\//, ''));

async function fetchJson(path) {
  const res = await fetch(resolveUrl(path));
  if (!res.ok) throw new Error(res.status === 429 ? 'HTTP 429 (too many requests to server)' : `HTTP ${res.status}`);
  try {
    return parseJsonc(await res.text());
  } catch {
    throw new Error('file is not valid JSON or could not be found (check path)');
  }
}

/** Fetch vector GeoJSONs. A failing vector is marked as error without breaking the case. */
async function hydrateVectors(def) {
  await Promise.all(def.vectors.map(async (v) => {
    v._load = 'ok';
    try {
      v.data = typeof v.source.data === 'string' ? await fetchJson(v.source.data) : v.source.data;
      if (!v.data || !Array.isArray(v.data.features)) throw new Error('GeoJSON is missing "features"');
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

/** Load catalog.json -> validated and normalized cases. Collects problems instead of retrying blindly. */
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
