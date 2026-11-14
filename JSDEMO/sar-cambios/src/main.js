import 'mapbox-gl/dist/mapbox-gl.css';
import './style.css';
import { loadCases, layerHasData } from './lib/registry.js';
import { MapController } from './lib/mapController.js';

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

app.innerHTML = `
  <div class="app">
    <aside class="side" id="side">
      <header>
        <h1>Cambios en superficie con SAR</h1>
        <p class="flag">DEMO / SIMULACIÓN. Las capas son ilustrativas; no son observaciones reales ni detección en tiempo real.</p>
      </header>
      <section>
        <label for="case-select">Caso de estudio</label>
        <select id="case-select"></select>
        <p id="case-desc" class="muted"></p>
      </section>
      <section id="time-sec">
        <div class="row"><label for="slider">Antes / después</label><button id="play" type="button" aria-label="Reproducir línea de tiempo">Reproducir</button></div>
        <input id="slider" type="range" min="0" max="0" step="1" value="0" />
        <div id="steps" class="steps"></div>
        <p id="step-note" class="muted"></p>
      </section>
      <section>
        <h2>Capas</h2>
        <div id="layers"></div>
      </section>
      <section>
        <h2>Leyenda</h2>
        <div id="legend"></div>
      </section>
      <footer class="muted">Mapa base: © Mapbox © OpenStreetMap © Maxar. Datos demo sintéticos generados por <code>scripts/make_demo_data.py</code>.</footer>
    </aside>
    <main class="stage">
      <div id="map"></div>
      <div id="status" class="status" role="status" hidden></div>
      <button id="home" type="button" class="home">Vista inicial</button>
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
  const slider = $('slider');
  slider.max = n - 1;
  slider.value = 0;
  $('time-sec').hidden = n < 2;
  $('steps').innerHTML = active.timeline
    .map((s, i) => `<button type="button" data-step="${i}" class="${i === 0 ? 'on' : ''}">${esc(s.label)}</button>`).join('');
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
  const items = active.layers
    .filter((l) => l.legend && l.status === 'ok' && controller.isVisible(l.id) && (!l.steps || l.steps.includes(controller.step)))
    .map((l) => `
      <div class="legend-block">
        <div class="legend-title">${esc(l.legend.title || l.name)}</div>
        ${(l.legend.items || []).map((i) => `<div class="legend-item"><span class="sw" style="background:${esc(i.color)}"></span>${esc(i.label)}</div>`).join('')}
        ${l.attribution ? `<small class="muted">${esc(l.attribution)}</small>` : ''}
      </div>`);
  $('legend').innerHTML = items.join('') || '<p class="muted">Activa una capa para ver su leyenda.</p>';
}

function refreshStatus() {
  const empty = active.layers.filter((l) => controller.isVisible(l.id) && l.status === 'ok' && !layerHasData(l, controller.step));
  const failed = active.layers.filter((l) => l.status !== 'ok');
  if (empty.length) setStatus('empty', `Sin datos en este paso: ${empty.map((l) => l.name).join(', ')}`);
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
    <p class="muted">${esc(layer.name)}${layer.demo ? ' · DEMO' : ''} · ${esc(s.label)}${s.date ? ` · ${esc(s.date)}` : ''}</p>
    <p>${esc(layer.description || 'Sin descripción disponible.')}</p>
    <table>${rows || '<tr><td>Sin atributos disponibles.</td></tr>'}</table>`;
  box.hidden = false;
  box.querySelector('.close').onclick = () => { box.hidden = true; };
}

async function selectCase(id) {
  stopPlay();
  active = cases.find((c) => c.id === id);
  $('info').hidden = true;
  setStatus('loading', 'Cargando caso…');
  $('case-desc').textContent = active.description || '';
  try {
    await controller.loadCase(active);
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
$('home').onclick = () => controller.home();
$('play').onclick = () => {
  if (timer) return stopPlay();
  $('play').textContent = 'Pausar';
  timer = setInterval(() => goTo((controller.step + 1) % active.timeline.length), 1400);
};
controller.onFeature = showInfo;

// ── Arranque ──
(async function init() {
  setStatus('loading', 'Cargando casos de estudio…');
  try {
    const result = await loadCases();
    cases = result.cases;
    if (!cases.length) throw new Error(result.problems.join(' | ') || 'No hay casos registrados');
    $('case-select').innerHTML = cases.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('');
    await selectCase(cases[0].id);
    if (result.problems.length) setStatus('error', `Casos omitidos por error: ${result.problems.length}. Revisa la consola.`);
    result.problems.forEach((p) => console.warn('[registry]', p));
  } catch (err) {
    setStatus('error', `No se pudieron cargar los datos: ${err.message}`);
  }
})();
