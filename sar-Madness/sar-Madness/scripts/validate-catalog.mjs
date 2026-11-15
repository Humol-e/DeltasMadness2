// Valida public/data/catalog.json y cada caso: esquema v2, datos 100% reales y archivos existentes.
// Uso: npm run validate
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseJsonc, validateCase } from '../src/lib/schema.js';

const pub = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'public');
const read = (rel) => parseJsonc(fs.readFileSync(path.join(pub, rel), 'utf8'));
const isRemote = (s) => /^(https?:)?\/\//.test(s);
let errors = 0;
const fail = (m) => { errors++; console.error(`  ✗ ${m}`); };

const catalog = read('data/catalog.json');
const caseList = catalog.cases ?? [];
if (!caseList.length) fail('El catálogo está vacío: se requieren casos con datos reales.');

for (const rel of caseList) {
  console.log(`• ${rel}`);
  let def;
  try { def = read(rel); } catch (e) { fail(`no se pudo leer/parsear: ${e.message}`); continue; }
  const { errors: errs, warnings } = validateCase(def);
  errs.forEach(fail);
  warnings.forEach((w) => console.warn(`  ! ${w}`));
  const need = (p, what) => {
    if (typeof p !== 'string' || !p || isRemote(p) || p.startsWith('data:')) return;
    const full = path.join(pub, p);
    if (!fs.existsSync(full)) fail(`${what}: no existe ${p}`);
    else if (fs.statSync(full).size === 0) fail(`${what}: archivo vacío ${p}`);
  };
  (def.datasets ?? []).forEach((d) => (d.frames ?? []).forEach((f) => Object.entries(f.files ?? {}).forEach(([k, p]) => { if (d.source?.type === 'image') need(p, `${d.id}/${f.id ?? f.datetime}/${k}`); })));
  (def.vectors ?? []).forEach((v) => need(v.source?.data, `vector ${v.id}`));
  (def.downloads ?? []).forEach((dl) => need(dl.url, `download ${dl.id}`));
}
console.log(errors ? `\n${errors} error(es)` : '\nCatálogo válido (100% datos reales)');
process.exit(errors ? 1 : 0);
