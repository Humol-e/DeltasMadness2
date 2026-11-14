// Esquema versionado de casos (v2). Sin dependencias del navegador: lo usan la app y scripts/validate-catalog.mjs.
export const SCHEMA_VERSION = 2;
const ID_RE = /^[A-Za-z0-9_-]+$/;
const FRAME_RE = /^[A-Za-z0-9_.-]+$/;

/** JSON con comentarios // y /* *\/ (los respeta dentro de cadenas). */
export function parseJsonc(text) {
  let out = '';
  let i = 0;
  let inStr = false;
  while (i < text.length) {
    const c = text[i];
    const n = text[i + 1];
    if (inStr) {
      out += c;
      if (c === '\\') { out += n ?? ''; i += 2; continue; }
      if (c === '"') inStr = false;
      i++;
      continue;
    }
    if (c === '"') { inStr = true; out += c; i++; continue; }
    if (c === '/' && n === '/') { while (i < text.length && text[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') {
      i += 2;
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    out += c;
    i++;
  }
  return JSON.parse(out);
}

export const toMs = (s) => (typeof s === 'string' ? Date.parse(s) : NaN);
const isNum = (x) => typeof x === 'number' && Number.isFinite(x);

/** Devuelve { errors, warnings }. Los errores impiden cargar el caso. */
export function validateCase(def) {
  const errors = [];
  const warnings = [];
  const err = (m) => errors.push(m);
  const warn = (m) => warnings.push(m);
  if (!def || typeof def !== 'object') return { errors: ['El caso no es un objeto'], warnings };

  if (def.schemaVersion !== SCHEMA_VERSION) err(`schemaVersion debe ser ${SCHEMA_VERSION} (recibido: ${def.schemaVersion ?? 'ausente'})`);
  if (!def.id || !ID_RE.test(def.id)) err('"id" requerido (letras, números, _ o -)');
  if (!def.title) err('Falta "title"');
  if (!def.view || !Array.isArray(def.view.center) || def.view.center.length !== 2) err('Falta "view.center" [lon, lat]');
  if (def.status && !['demo', 'real'].includes(def.status)) err('"status" debe ser "demo" o "real"');

  const datasets = def.datasets ?? [];
  const vectors = def.vectors ?? [];
  if (!datasets.length && !vectors.length) err('El caso necesita al menos un dataset o un vector');
  const ids = new Set();

  datasets.forEach((d, i) => {
    const tag = `dataset "${d?.id ?? `#${i}`}"`;
    if (!d.id || !ID_RE.test(d.id)) return err(`dataset #${i}: "id" requerido (letras, números, _ o -)`);
    if (ids.has(d.id)) err(`${tag}: id duplicado`);
    ids.add(d.id);
    if (d.kind && d.kind !== 'raster') err(`${tag}: solo se soporta kind "raster"`);
    if (d.status && !['demo', 'real'].includes(d.status)) err(`${tag}: status debe ser demo o real`);
    const type = d.source?.type;
    if (!['image', 'tiles'].includes(type)) err(`${tag}: source.type debe ser "image" o "tiles"`);
    const encoding = d.source?.encoding ?? (type === 'tiles' ? 'rgba' : 'gray8');
    if (!['gray8', 'rgba'].includes(encoding)) err(`${tag}: source.encoding debe ser gray8 o rgba`);
    if (type === 'image' || d.extent) {
      const e = d.extent;
      if (!Array.isArray(e) || e.length !== 4 || !e.every(isNum) || e[0] >= e[2] || e[1] >= e[3]) err(`${tag}: "extent" debe ser [oeste, sur, este, norte]`);
    }
    const vars = d.variables;
    if (!Array.isArray(vars) || !vars.length) return err(`${tag}: necesita al menos una variable`);
    const vids = new Set();
    vars.forEach((v) => {
      if (!v.id || !ID_RE.test(v.id)) err(`${tag}: variable con "id" inválido`);
      if (vids.has(v.id)) err(`${tag}: variable "${v.id}" duplicada`);
      vids.add(v.id);
      if (!v.label) warn(`${tag}/${v.id}: sin "label"`);
      if (v.unit == null) warn(`${tag}/${v.id}: unidad pendiente (unit = null)`);
      if (encoding === 'gray8') {
        const lg = v.legend;
        if (!lg || !isNum(lg.min) || !isNum(lg.max) || lg.min >= lg.max || !Array.isArray(lg.colors) || lg.colors.length < 2) {
          err(`${tag}/${v.id}: con gray8 la leyenda requiere min < max y colors (>= 2)`);
        }
      }
    });
    (d.composites ?? []).forEach((c) => {
      if (!c.id || !ID_RE.test(c.id)) err(`${tag}: composite con "id" inválido`);
      if (vids.has(c.id)) err(`${tag}: composite "${c.id}" repite el id de una variable`);
      const chans = Object.entries(c.channels ?? {}).filter(([, v]) => v);
      if (!chans.length) err(`${tag}/${c.id}: "channels" vacío`);
      chans.forEach(([ch, v]) => {
        if (!['r', 'g', 'b'].includes(ch)) err(`${tag}/${c.id}: canal "${ch}" inválido`);
        if (!vids.has(v)) err(`${tag}/${c.id}: el canal ${ch} usa la variable inexistente "${v}"`);
      });
      if (type !== 'image' || encoding !== 'gray8') warn(`${tag}/${c.id}: los compuestos RGB solo funcionan con source.type "image" y encoding "gray8"; se ignorará`);
    });
    if (!Array.isArray(d.frames) || !d.frames.length) warn(`${tag}: sin frames`);
    const fids = new Set();
    (d.frames ?? []).forEach((f, k) => {
      if (Number.isNaN(toMs(f.datetime))) return err(`${tag}: frame #${k} sin "datetime" ISO 8601 válido`);
      const fid = f.id ?? String(f.datetime).replace(/[^A-Za-z0-9_.-]/g, '-');
      if (!FRAME_RE.test(fid)) err(`${tag}: frame "${fid}" con id inválido`);
      if (fids.has(fid)) err(`${tag}: frame "${fid}" duplicado`);
      fids.add(fid);
      const files = f.files ?? {};
      if (!Object.keys(files).length) err(`${tag}/${fid}: "files" vacío`);
      Object.entries(files).forEach(([k2, url]) => {
        if (!vids.has(k2)) err(`${tag}/${fid}: files.${k2} no coincide con ninguna variable`);
        if (typeof url !== 'string' || !url) err(`${tag}/${fid}: files.${k2} debe ser una ruta o URL`);
      });
    });
  });

  vectors.forEach((v, i) => {
    const tag = `vector "${v?.id ?? `#${i}`}"`;
    if (!v.id || !ID_RE.test(v.id)) return err(`vector #${i}: "id" requerido (letras, números, _ o -)`);
    if (ids.has(v.id)) err(`${tag}: id duplicado`);
    ids.add(v.id);
    if (!['fill', 'line', 'circle'].includes(v.type)) err(`${tag}: type debe ser fill, line o circle`);
    if (!v.source?.data) err(`${tag}: falta "source.data" (ruta GeoJSON u objeto inline)`);
    if (v.time && (!v.time.property || !['asof', 'cumulative'].includes(v.time.mode))) err(`${tag}: time requiere "property" y mode "asof" | "cumulative"`);
    if (v.indicator && !v.indicator.simulated && !v.indicator.validation) warn(`${tag}: indicador no simulado sin "validation" definida`);
  });
  return { errors, warnings };
}

/** Rellena valores por defecto y ordena frames. Nunca asume "real": lo no declarado es "demo". */
export function normalizeCase(def) {
  def.status ??= 'demo';
  def.datasets ??= [];
  def.vectors ??= [];
  for (const d of def.datasets) {
    d.kind ??= 'raster';
    d.status ??= 'demo';
    d.render = { opacity: 0.85, resampling: 'linear', ...d.render };
    d.source.encoding ??= d.source.type === 'tiles' ? 'rgba' : 'gray8';
    d.composites = (d.source.type === 'image' && d.source.encoding === 'gray8') ? (d.composites ?? []) : [];
    d.frames = (d.frames ?? []).map((f) => ({
      ...f,
      id: f.id ?? String(f.datetime).replace(/[^A-Za-z0-9_.-]/g, '-'),
      ms: toMs(f.datetime),
    })).sort((a, b) => a.ms - b.ms);
  }
  for (const v of def.vectors) {
    v.status ??= def.status;
    v.simulated ??= false;
  }
  return def;
}
