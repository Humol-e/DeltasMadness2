// Versioned case schema (v2). Browser-independent: shared by the viewer app and scripts/validate-catalog.mjs.
export const SCHEMA_VERSION = 2;
const ID_RE = /^[A-Za-z0-9_-]+$/;
const FRAME_RE = /^[A-Za-z0-9_.-]+$/;

/** Parse JSON with // and /* *\/ comments (preserved inside strings). */
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

/** Return { errors, warnings }. Errors prevent the case from loading. */
export function validateCase(def) {
  const errors = [];
  const warnings = [];
  const err = (m) => errors.push(m);
  const warn = (m) => warnings.push(m);
  if (!def || typeof def !== 'object') return { errors: ['Case definition must be an object'], warnings };

  if (def.schemaVersion !== SCHEMA_VERSION) err(`schemaVersion must be ${SCHEMA_VERSION} (got: ${def.schemaVersion ?? 'missing'})`);
  if (!def.id || !ID_RE.test(def.id)) err('"id" is required (letters, numbers, _ or -)');
  if (!def.title) err('Missing "title"');
  if (!def.view || !Array.isArray(def.view.center) || def.view.center.length !== 2) err('Missing "view.center" [lon, lat]');
  if (def.status !== 'real' || def.simulated === true) err('Only real-data cases are allowed (status: "real", simulated != true)');

  const datasets = def.datasets ?? [];
  const vectors = def.vectors ?? [];
  if (!datasets.length && !vectors.length) err('Case requires at least one dataset or one vector');
  const ids = new Set();

  datasets.forEach((d, i) => {
    const tag = `dataset "${d?.id ?? `#${i}`}"`;
    if (!d.id || !ID_RE.test(d.id)) return err(`dataset #${i}: "id" is required (letters, numbers, _ or -)`);
    if (ids.has(d.id)) err(`${tag}: duplicate id`);
    ids.add(d.id);
    if (d.kind && d.kind !== 'raster') err(`${tag}: only kind "raster" is supported`);
    if (d.status !== 'real') err(`${tag}: only datasets with status "real" are allowed`);
    const type = d.source?.type;
    if (!['image', 'tiles'].includes(type)) err(`${tag}: source.type must be "image" or "tiles"`);
    const encoding = d.source?.encoding ?? (type === 'tiles' ? 'rgba' : 'gray8');
    if (!['gray8', 'rgba'].includes(encoding)) err(`${tag}: source.encoding must be gray8 or rgba`);
    if (type === 'image' || d.extent) {
      const e = d.extent;
      if (!Array.isArray(e) || e.length !== 4 || !e.every(isNum) || e[0] >= e[2] || e[1] >= e[3]) err(`${tag}: "extent" must be [west, south, east, north]`);
    }
    const vars = d.variables;
    if (!Array.isArray(vars) || !vars.length) return err(`${tag}: requires at least one variable`);
    const vids = new Set();
    vars.forEach((v) => {
      if (!v.id || !ID_RE.test(v.id)) err(`${tag}: variable has an invalid "id"`);
      if (vids.has(v.id)) err(`${tag}: duplicate variable "${v.id}"`);
      vids.add(v.id);
      if (!v.label) warn(`${tag}/${v.id}: missing "label"`);
      if (v.unit == null) warn(`${tag}/${v.id}: unit pending (unit = null)`);
      if (encoding === 'gray8') {
        const lg = v.legend;
        if (!lg || !isNum(lg.min) || !isNum(lg.max) || lg.min >= lg.max || !Array.isArray(lg.colors) || lg.colors.length < 2) {
          err(`${tag}/${v.id}: gray8 legend requires min < max and colors (>= 2)`);
        }
      }
    });
    (d.composites ?? []).forEach((c) => {
      if (!c.id || !ID_RE.test(c.id)) err(`${tag}: composite has an invalid "id"`);
      if (vids.has(c.id)) err(`${tag}: composite "${c.id}" collides with a variable id`);
      const chans = Object.entries(c.channels ?? {}).filter(([, v]) => v);
      if (!chans.length) err(`${tag}/${c.id}: empty "channels"`);
      chans.forEach(([ch, v]) => {
        if (!['r', 'g', 'b'].includes(ch)) err(`${tag}/${c.id}: invalid channel "${ch}"`);
        if (!vids.has(v)) err(`${tag}/${c.id}: channel ${ch} references missing variable "${v}"`);
      });
      if (type !== 'image' || encoding !== 'gray8') warn(`${tag}/${c.id}: RGB composites only work with source.type "image" and encoding "gray8"; ignoring`);
    });
    if (!Array.isArray(d.frames) || !d.frames.length) warn(`${tag}: no frames`);
    const fids = new Set();
    (d.frames ?? []).forEach((f, k) => {
      if (Number.isNaN(toMs(f.datetime))) return err(`${tag}: frame #${k} missing valid ISO 8601 "datetime"`);
      const fid = f.id ?? String(f.datetime).replace(/[^A-Za-z0-9_.-]/g, '-');
      if (!FRAME_RE.test(fid)) err(`${tag}: frame "${fid}" has an invalid id`);
      if (fids.has(fid)) err(`${tag}: duplicate frame "${fid}"`);
      fids.add(fid);
      const files = f.files ?? {};
      if (!Object.keys(files).length) err(`${tag}/${fid}: empty "files"`);
      Object.entries(files).forEach(([k2, url]) => {
        if (!vids.has(k2)) err(`${tag}/${fid}: files.${k2} matches no declared variable`);
        if (typeof url !== 'string' || !url) err(`${tag}/${fid}: files.${k2} must be a path or URL`);
      });
    });
  });

  vectors.forEach((v, i) => {
    const tag = `vector "${v?.id ?? `#${i}`}"`;
    if (!v.id || !ID_RE.test(v.id)) return err(`vector #${i}: "id" is required (letters, numbers, _ or -)`);
    if (ids.has(v.id)) err(`${tag}: duplicate id`);
    ids.add(v.id);
    if (!['fill', 'line', 'circle'].includes(v.type)) err(`${tag}: type must be fill, line, or circle`);
    if (!v.source?.data) err(`${tag}: missing "source.data" (GeoJSON path or inline object)`);
    if (v.time && (!v.time.property || !['asof', 'cumulative'].includes(v.time.mode))) err(`${tag}: time requires "property" and mode "asof" | "cumulative"`);
    if (v.indicator && !v.indicator.simulated && !v.indicator.validation) warn(`${tag}: non-simulated indicator without "validation" defined`);
  });
  vectors.forEach((v) => {
    if (v.status && v.status !== 'real') errors.push(`vector "${v.id}": must have status "real"`);
    if (v.simulated === true) errors.push(`vector "${v.id}": simulated vectors are not allowed`);
  });

  (def.downloads ?? []).forEach((dl, i) => {
    const tag = `download "${dl?.id ?? `#${i}`}"`;
    if (!dl?.id || !dl?.label || typeof dl?.url !== 'string' || !dl.url) errors.push(`${tag}: requires id, label, and url`);
    if (dl?.format && !['geotiff', 'png', 'geojson'].includes(dl.format)) errors.push(`${tag}: invalid format (${dl.format})`);
  });

  return { errors, warnings };
}

/** Populate safe defaults and sort frames chronologically. */
export function normalizeCase(def) {
  def.status ??= 'real';
  def.downloads ??= [];
  def.datasets ??= [];
  def.vectors ??= [];
  for (const d of def.datasets) {
    d.kind ??= 'raster';
    d.status ??= 'real';
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
