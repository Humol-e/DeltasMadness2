import 'mapbox-gl/dist/mapbox-gl.css';
import './style.css';
import { loadCases, layerHasData } from './lib/registry.js';
import { MapController, BASEMAPS, VIEW_MODES } from './lib/mapController.js';

const token = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;
const app = document.getElementById('app');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ── Estado de configuración: sin token no hay mapa, pero tampoco pantalla rota ──
if (!token || token.includes('PEGA')) {
  app.innerHTML = `
    <div class="setup">
      <h1>Falta el token de Mapbox</h1>
      <p>La app necesita un token público de Mapbox para dibujar el mapa.</p>
      <ol>
        <li>Crea un token público (<code>pk.…</code>) en <a href="https://account.mapbox.com" target="_blank" rel="noopener">account.mapbox.com</a>.</li>
        <li>Copia <code>.env.example</code> como <code>.env</code> y pégalo en <code>VITE_MAPBOX_ACCESS_TOKEN</code>.</li>
        <li>Reinicia con <code>npm run dev</code>.</li>
      </ol>
      <p class="muted">Restringe el token por URL/origen y permisos en el panel de Mapbox. Nunca lo subas al repositorio.</p>
    </div>`;
  throw new Error('VITE_MAPBOX_ACCESS_TOKEN no configurado');
}

const radios = (name, options, checked) => Object.entries(options).map(([k, v]) => {
  const label = typeof v === 'string' ? v : v.label;
  return `<label class="seg"><input type="radio" name="${name}" value="${k}" ${k === checked ? 'checked' : ''}/><span>${esc(label)}</span></label>`;
}).join('');

app.innerHTML = `
  <div class="app">
    <aside class="side" id="side">
      <header>
        <h1>Cambios en superficie con SAR</h1>
        <p class="flag">DEMO / SIMULACIÓN. Las capas SAR son ilustrativas; no son observaciones reales ni detección en tiempo real.</p>
      </header>

      <section>
        <label for="case-select">Zona de estudio</label>
        <select id="case-select"></select>
        <p id="case-desc" class="muted">Gira el globo y toca un punto, o elige una zona de la lista.</p>
      </section>

      <section class="base-panel">
        <h2>Mapa base y terreno <span class="tag tag-base">BASE</span></h2>
        <div class="field"><span class="lbl">Vista</span><div class="segs" id="mode">${radios('mode', VIEW_MODES, 'globe')}</div></div>
        <div class="field"><span class="lbl">Mapa base</span><div class="segs" id="basemap">${radios('basemap', BASEMAPS, 'satellite')}</div></div>
        <label class="check"><input type="checkbox" id="hillshade" checked /> Sombreado de relieve</label>
        <div class="field"><label for="exag" class="lbl">Exageración del relieve: <output id="exag-out">1,5×</output></label>
          <input id="exag" type="range" min="0.5" max="3" step="0.1" value="1.5" /></div>
        <div class="field"><label for="sun" class="lbl">Luz solar (azimut): <output id="sun-out">315°</output></label>
          <input id="sun" type="range" min="0" max="360" step="5" value="315" /></div>
        <small class="muted">El relieve y el color del mapa base no son datos SAR.</small>
      </section>

      <section id="time-sec" hidden>
        <div class="row"><label for="slider">Antes / después</label><button id="play" type="button" aria-label="Reproducir línea de tiempo">Reproducir</button></div>
        <input id="slider" type="range" min="0" max="0" step="1" value="0" />
        <div id="steps" class="steps"></div>
        <p id="step-note" class="muted"></p>
      </section>

      <section class="sar-panel">
        <h2>Capas SAR <span class="tag tag-sar">DEMO</span></h2>
        <div id="layers"><p class="muted">Selecciona una zona para ver sus capas.</p></div>
      </section>

      <section class="sar-panel">
        <h2>Leyenda · datos SAR (DEMO)</h2>
        <div id="legend"><p class="muted">Sin capas activas.</p></div>
      </section>

      <footer class="muted">Mapa base: © Mapbox © OpenStreetMap © Maxar. Datos SAR demo sintéticos generados por <code>scripts/make_demo_data.py</code>.</footer>
    </aside>
    <main class="stage">
      <div id="map"></div>
      <div id="status" class="status" role="status" hidden></div>
      <button id="home" type="button" class="home">Ver el mundo</button>
      <button id="recenter" type="button" class="home second" hidden>Volver a la zona</button>
      <div id="info" class="info" hidden></div>
    </main>
  </div>`;

const $ = (id) => document.getElementById(id);
const statusEl = $('status');
function setStatus(kind, text) {
  statusEl.hidden = !text;
  statusEl.className = `status ${kind || ''}`;
  statusEl.textContent = text || '';
}

const controller = new MapController({ container: 'map', token, onStatus: setStatus });
let cases = [];
let active = null;
let timer = null;

// ── Render ──
function renderTimeline() {
  const n = active.timeline.length;
  $('slider').max = n - 1;
  $('time-sec').hidden = n < 2;
  $('steps').innerHTML = active.timeline
    .map((s, i) => `<button type="button" data-step="${i}">${esc(s.label)}</button>`).join('');
  updateStepUI(0);
}

function updateStepUI(step) {
  const s = active.timeline[step];
  $('slider').value = step;
  document.querySelectorAll('#steps button').forEach((b) => b.classList.toggle('on', Number(b.dataset.step) === step));
  $('step-note').textContent = `${s.date ? `Fecha: ${s.date}. ` : 'Fecha: sin definir (DEMO). '}${s.note || ''}`;
}

function renderLayers() {
  $('layers').innerHTML = active.layers.map((l) => {
    const bad = l.status !== 'ok';
    return `
      <label class="layer ${bad ? 'bad' : ''}">
        <input type="checkbox" data-layer="${esc(l.id)}" ${bad ? 'disabled' : 'checked'} />
        <span><strong>${esc(l.name || l.id)}</strong>${l.demo ? ' <em class="badge">DEMO</em>' : ''}
        <small>${bad ? `Sin datos: ${esc(l.error || 'no se pudo cargar')}` : esc(l.description || '')}</small></span>
      </label>`;
  }).join('');
}

function renderLegend() {
  if (!active) { $('legend').innerHTML = '<p class="muted">Sin capas activas.</p>'; return; }
  const items = active.layers
    .filter((l) => l.legend && l.status === 'ok' && controller.isVisible(l.id) && (!l.steps || l.steps.includes(controller.step)))
    .map((l) => `
      <div class="legend-block">
        <div class="legend-title">${esc(l.legend.title || l.name)}</div>
        ${(l.legend.items || []).map((i) => `<div class="legend-item"><span class="sw" style="background:${esc(i.color)}"></span>${esc(i.label)}</div>`).join('')}
        ${l.attribution ? `<small class="muted">${esc(l.attribution)}</small>` : ''}
      </div>`);
  $('legend').innerHTML = items.join('') || '<p class="muted">Activa una capa SAR para ver su leyenda.</p>';
}

function refreshStatus() {
  if (!active) return setStatus('', '');
  const empty = active.layers.filter((l) => controller.isVisible(l.id) && l.status === 'ok' && !layerHasData(l, controller.step));
  const failed = active.layers.filter((l) => l.status !== 'ok');
  if (empty.length) setStatus('empty', `Sin datos SAR en este paso: ${empty.map((l) => l.name).join(', ')}`);
  else if (failed.length) setStatus('error', `No se pudieron cargar ${failed.length} capa(s). El resto funciona.`);
  else setStatus('', '');
}

function showInfo(layer, feature) {
  const box = $('info');
  if (!layer) { box.hidden = true; return; }
  const p = feature.properties || {};
  const fields = layer.popupFields || Object.keys(p).map((k) => [k, k]);
  const rows = fields.filter(([k]) => p[k] !== undefined && p[k] !== null)
    .map(([k, label, unit]) => `<tr><th>${esc(label || k)}</th><td>${esc(p[k])}${unit ? ` ${esc(unit)}` : ''}</td></tr>`).join('');
  const s = active.timeline[controller.step];
  box.innerHTML = `
    <button type="button" class="close" aria-label="Cerrar panel">×</button>
    <h3>${esc(p.name || p.zone || layer.name)}</h3>
    <p class="muted">Dato SAR · ${esc(layer.name)}${layer.demo ? ' · DEMO' : ''} · ${esc(s.label)}${s.date ? ` · ${esc(s.date)}` : ''}</p>
    <p>${esc(layer.description || 'Sin descripción disponible.')}</p>
    <table>${rows || '<tr><td>Sin atributos disponibles.</td></tr>'}</table>`;
  box.hidden = false;
  box.querySelector('.close').onclick = () => { box.hidden = true; };
}

function showWorldUI() {
  stopPlay();
  active = null;
  $('info').hidden = true;
  $('time-sec').hidden = true;
  $('recenter').hidden = true;
  $('case-select').value = '';
  $('case-desc').textContent = 'Gira el globo y toca un punto, o elige una zona de la lista.';
  $('layers').innerHTML = '<p class="muted">Selecciona una zona para ver sus capas.</p>';
  renderLegend();
  setStatus('', '');
}

async function selectCase(id) {
  if (!id) { await controller.showWorld(); showWorldUI(); return; }
  stopPlay();
  const def = cases.find((c) => c.id === id);
  if (!def) return;
  active = def;
  $('case-select').value = id;
  $('info').hidden = true;
  $('recenter').hidden = false;
  setStatus('loading', 'Acercando a la zona…');
  $('case-desc').textContent = def.description || '';
  try {
    await controller.loadCase(def);
  } catch (err) {
    setStatus('error', `No se pudo cargar el caso: ${err.message}`);
    return;
  }
  renderTimeline();
  renderLayers();
  renderLegend();
  refreshStatus();
}

function goTo(step) {
  controller.setStep(step);
  updateStepUI(step);
  renderLegend();
  refreshStatus();
}

function stopPlay() {
  clearInterval(timer);
  timer = null;
  $('play').textContent = 'Reproducir';
}

// ── Eventos ──
$('case-select').onchange = (e) => selectCase(e.target.value);
$('slider').oninput = (e) => { stopPlay(); goTo(Number(e.target.value)); };
$('steps').onclick = (e) => { const b = e.target.closest('button'); if (b) { stopPlay(); goTo(Number(b.dataset.step)); } };
$('layers').onchange = (e) => {
  if (!e.target.dataset.layer) return;
  controller.setVisible(e.target.dataset.layer, e.target.checked);
  renderLegend();
  refreshStatus();
};
$('home').onclick = () => selectCase('');
$('recenter').onclick = () => controller.flyToCase();
$('play').onclick = () => {
  if (timer) return stopPlay();
  $('play').textContent = 'Pausar';
  timer = setInterval(() => goTo((controller.step + 1) % active.timeline.length), 1400);
};
$('mode').onchange = (e) => controller.setMode(e.target.value);
$('basemap').onchange = (e) => controller.setBasemap(e.target.value);
$('hillshade').onchange = (e) => controller.setHillshade(e.target.checked);
$('exag').oninput = (e) => {
  const v = Number(e.target.value);
  $('exag-out').textContent = `${String(v).replace('.', ',')}×`;
  controller.setExaggeration(v);
};
$('sun').oninput = (e) => {
  $('sun-out').textContent = `${e.target.value}°`;
  controller.setSunAzimuth(Number(e.target.value));
};
controller.onFeature = showInfo;
controller.onCasePick = (id) => selectCase(id);

// ── Arranque: globo con las zonas disponibles ──
(async function init() {
  setStatus('loading', 'Cargando zonas de estudio…');
  try {
    const result = await loadCases();
    cases = result.cases;
    if (!cases.length) throw new Error(result.problems.join(' | ') || 'No hay casos registrados');
    $('case-select').innerHTML = `<option value="">Mundo (globo 3D)</option>` +
      cases.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
    await controller.ready;
    controller.setCases(cases);
    setStatus('', '');
    if (result.problems.length) setStatus('error', `Casos omitidos por error: ${result.problems.length}. Revisa la consola.`);
    result.problems.forEach((p) => console.warn('[registry]', p));
  } catch (err) {
    setStatus('error', `No se pudieron cargar los datos: ${err.message}`);
  }
})();
