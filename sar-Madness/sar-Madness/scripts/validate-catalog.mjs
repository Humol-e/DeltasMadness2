// Valida public/data/catalog.json y cada caso: esquema v2 + que existan los archivos locales referenciados.
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
for (const rel of catalog.cases ?? []) {
  console.log(`• ${rel}`);
  let def;
  try { def = read(rel); } catch (e) { fail(`no se pudo leer/parsear: ${e.message}`); continue; }
  const { errors: errs, warnings } = validateCase(def);
  errs.forEach(fail);
  warnings.forEach((w) => console.warn(`  ! ${w}`));
  const need = (p, what) => { if (typeof p === 'string' && !isRemote(p) && !p.startsWith('data:') && !fs.existsSync(path.join(pub, p))) fail(`${what}: no existe ${p}`); };
  (def.datasets ?? []).forEach((d) => (d.frames ?? []).forEach((f) => Object.entries(f.files ?? {}).forEach(([k, p]) => { if (d.source?.type === 'image') need(p, `${d.id}/${f.id ?? f.datetime}/${k}`); })));
  (def.vectors ?? []).forEach((v) => need(v.source?.data, `vector ${v.id}`));
}
console.log(errors ? `\n${errors} error(es)` : '\nCatálogo válido');
process.exit(errors ? 1 : 0);
