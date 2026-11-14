import { toMs } from './schema.js';

/** Eje temporal = unión ordenada de las fechas de todos los frames y de los vectores con fecha. */
export function buildAxis(def) {
  const seen = new Map();
  const add = (s) => { const ms = toMs(s); if (!Number.isNaN(ms) && !seen.has(ms)) seen.set(ms, s); };
  def.datasets.forEach((d) => d.frames.forEach((f) => add(f.datetime)));
  def.vectors.forEach((v) => (v._dates || []).forEach(add));
  return [...seen].sort((a, b) => a[0] - b[0]).map(([ms, label]) => ({ ms, label }));
}

/** Último frame del dataset con fecha <= ms (null si aún no hay ninguno). */
export function frameAt(ds, ms) {
  let best = null;
  for (const f of ds.frames) { if (f.ms <= ms) best = f; else break; }
  return best;
}

/** Última fecha de un vector <= ms (string tal como está en los datos). */
export function vectorCutoff(v, ms) {
  let best = null;
  for (const d of v._dates || []) { if (toMs(d) <= ms) best = d; else break; }
  return best;
}

export const fmtTime = (s) => String(s).replace('T', ' ');

/** Diferencia legible entre la fecha elegida y la del frame; null si coinciden. */
export function deltaLabel(ms, frameMs) {
  const diff = ms - frameMs;
  if (Math.abs(diff) < 60000) return null;
  const hours = diff / 3600000;
  return hours < 48 ? `${Math.round(hours)} h` : `${Math.round(hours / 2.4) / 10} d`;
}
