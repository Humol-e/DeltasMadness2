import 'mapbox-gl/dist/mapbox-gl.css';
import './style.css';
import { loadCatalog } from './lib/registry.js';
import { MapController, BASEMAPS, VIEW_MODES } from './lib/mapController.js';
import { frameAt, vectorCutoff, deltaLabel, fmtTime } from './lib/timeline.js';

const token = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;
const app = document.getElementById('app');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const NA = 'No disponible';

// ── Sin token no hay mapa, pero tampoco pantalla rota ──
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
        <p class="flag">DEMO — datos sintéticos, no son observaciones satelitales ni alertas reales.</p>
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
        <h2>Tiempo</h2>
        <div class="tbtns">
          <button id="prev" type="button" aria-label="Frame anterior">◀ Anterior</button>
          <button id="play" type="button">Reproducir</button>
          <button id="next" type="button" aria-label="Frame siguiente">Siguiente ▶</button>
          <select id="speed" aria-label="Velocidad"><option value="2200">0,5×</option><option value="1300" selected>1×</option><option value="650">2×</option></select>
        </div>
        <input id="slider" type="range" min="0" max="0" step="1" value="0" aria-label="Fecha del eje temporal" />
        <div id="tlabel" class="tlabel"></div>
        <small class="muted">Cada capa muestra su último frame disponible hasta la fecha elegida. Las fuentes tienen fechas distintas: mira la fecha de cada capa.</small>
      </section>

      <section class="sar-panel" id="ds-sec" hidden>
        <h2>Rásteres <span class="tag tag-sar">DEMO</span></h2>
        <div id="datasets"></div>
      </section>

      <section class="sar-panel" id="swipe-sec" hidden>
        <h2>Comparar antes / después</h2>
        <label class="check"><input type="checkbox" id="swipe-on" /> Activar comparador (swipe)</label>
        <div id="swipe-ctl" hidden>
          <div class="field"><label for="sw-ds" class="lbl">Dataset</label><select id="sw-ds"></select></div>
          <div class="field"><label for="sw-var" class="lbl">Variable</label><select id="sw-var"></select></div>
          <div class="field"><label for="sw-before" class="lbl">Antes (izquierda)</label><select id="sw-before"></select></div>
          <div class="field"><label for="sw-after" class="lbl">Después (derecha)</label><select id="sw-after"></select></div>
          <div class="field"><label for="sw-split" class="lbl">Posición del corte en la zona</label><input id="sw-split" type="range" min="0" max="100" step="1" value="50" /></div>
          <small class="muted">El corte divide la zona (de oeste a este), no la pantalla.</small>
        </div>
      </section>

      <section class="sar-panel" id="vec-sec" hidden>
        <h2>Vectores (GeoJSON)</h2>
        <div id="vectors"></div>
      </section>

      <section class="sar-panel">
        <h2>Leyenda · datos SAR (DEMO)</h2>
        <div id="legend"><p class="muted">Sin capas activas.</p></div>
      </section>

      <footer class="muted">Mapa base: © Mapbox © OpenStreetMap © Maxar. Datos demo sintéticos generados por <code>scripts/make_demo_data.py</code>.</footer>
    </aside>
    <main class="stage">
      <div class="demo-banner" id="demo-banner">DEMO — datos sintéticos, no son observaciones satelitales ni alertas reales</div>
      <div id="map"></div>
      <div id="status" class="status" role="status" hidden><span id="status-text"></span><button id="status-act" type="button" hidden></button><button id="status-x" type="button" aria-label="Cerrar aviso">×</button></div>
      <button id="home" type="button" class="home">Ver el mundo</button>
      <button id="recenter" type="button" class="home second" hidden>Volver a la zona</button>
      <div id="swipe-labels" class="swipe-labels" hidden><span id="sl-before"></span><span id="sl-after"></span></div>
      <div id="info" class="info" hidden></div>
    </main>
  </div>`;

const $ = (id) => document.getElementById(id);

// ── Aviso de estado (con acción opcional, nunca reintentos automáticos) ──
let statusTimer = null;
function setStatus(kind, text, action) {
  clearTimeout(statusTimer);
  const el = $('status');
  el.hidden = !text;
  el.className = `status ${kind || ''}`;
  $('status-text').textContent = text || '';
  $('status-act').hidden = !action;
  $('status-act').dataset.action = action || '';
  $('status-act').textContent = 'Reintentar';
  if (text && kind !== 'loading' && kind !== 'data') statusTimer = setTimeout(() => { el.hidden = true; }, 20000);
}

const controller = new MapController({ container: 'map', token, onStatus: setStatus });
let cases = [];
let active = null;
let idx = 0;
let playing = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tickMs = () => active?.axis[idx]?.ms ?? null;

// ── Tarjetas de datasets ──
const badgeOf = (status) => (status === 'real' ? 'REAL' : 'DEMO');
const unitOf = (u) => (u == null ? 'unidad pendiente' : u);

function renderDatasets() {
  $('ds-sec').hidden = !active.datasets.length;
  $('datasets').innerHTML = active.datasets.map((d) => {
    const meta = [d.platform || 'plataforma pendiente', d.sensor || 'sensor pendiente', d.provider || 'proveedor pendiente'].join(' · ');
    const opts = d.variables.map((v) => `<option value="${esc(v.id)}">${esc(v.label || v.id)}${v.unit != null ? ` (${esc(v.unit)})` : ''}</option>`).join('')
      + d.composites.map((c) => `<option value="${esc(c.id)}">${esc(c.label || c.id)}</option>`).join('');
    return `
      <div class="card" data-ds="${esc(d.id)}">
        <label class="layer"><input type="checkbox" data-act="vis" checked />
          <span><strong>${esc(d.name || d.id)}</strong> <em class="badge">${badgeOf(d.status)}</em><small>${esc(meta)}</small></span></label>
        <div class="field"><label class="lbl">Variable</label><select data-act="sel">${opts}</select></div>
        <div class="field"><label class="lbl">Opacidad <output>${Math.round(d.render.opacity * 100)}%</output></label>
          <input type="range" data-act="op" min="0" max="100" value="${Math.round(d.render.opacity * 100)}" /></div>
        <div class="dsdate none" data-role="date"></div>
        <div class="dserr" data-role="err" hidden></div>
        <button type="button" data-act="retry" class="mini" hidden>Reintentar esta capa</button>
      </div>`;
  }).join('');
  active.datasets.forEach((d) => paintDataset(d.id));
}

function paintDataset(id) {
  const card = document.querySelector(`[data-ds="${CSS.escape(id)}"]`);
  const st = controller.dataset(id);
  if (!card || !st) return;
  const date = card.querySelector('[data-role="date"]');
  const err = card.querySelector('[data-role="err"]');
  const t = tickMs();
  if (!st.frame) {
    const first = st.def.frames[0];
    date.className = 'dsdate none';
    date.textContent = first ? `Aún sin frame: el primero es del ${fmtTime(first.datetime)}` : 'Sin frames en la configuración';
  } else {
    const dl = t == null ? null : deltaLabel(t, st.frame.ms);
    date.className = `dsdate ${dl ? 'stale' : 'sync'}`;
    date.textContent = dl
      ? `Frame del ${fmtTime(st.frame.datetime)} · ${dl} antes de la fecha elegida (no simultáneo)`
      : `Frame del ${fmtTime(st.frame.datetime)} · coincide con la fecha elegida`;
  }
  const bad = st.status === 'error' || st.status === 'novar';
  err.hidden = !bad;
  err.textContent = bad ? (st.error || 'Error') : '';
  card.querySelector('[data-act="retry"]').hidden = st.status !== 'error';
}

// ── Vectores ──
function renderVectors() {
  $('vec-sec').hidden = !active.vectors.length;
  $('vectors').innerHTML = active.vectors.map((v) => {
    const bad = v._load !== 'ok';
    const badge = v.simulated ? 'SIMULADO' : badgeOf(v.status);
    const warn = v.indicator && !v.indicator.simulated && !v.indicator.validation ? ' <em class="badge warn">sin validación</em>' : '';
    return `
      <label class="layer ${bad ? 'bad' : ''}">
        <input type="checkbox" data-vec="${esc(v.id)}" ${bad ? 'disabled' : 'checked'} />
        <span><strong>${esc(v.name || v.id)}</strong> <em class="badge">${badge}</em>${warn}
        <small>${bad ? `No cargó: ${esc(v._error || 'error')}` : esc(v.description || '')}</small>
        <small class="vdate" data-vdate="${esc(v.id)}"></small></span>
      </label>`;
  }).join('');
  paintVectorDates();
}

function paintVectorDates() {
  const t = tickMs();
  active.vectors.forEach((v) => {
    const el = document.querySelector(`[data-vdate="${CSS.escape(v.id)}"]`);
    if (!el || !v.time) return;
    const cut = t == null ? null : vectorCutoff(v, t);
    el.textContent = cut == null ? 'Aún sin datos en esta fecha' : `${v.time.mode === 'cumulative' ? 'Acumulado hasta' : 'Datos del'} ${fmtTime(cut)}`;
  });
}

// ── Leyenda ──
function renderLegend() {
  if (!active) { $('legend').innerHTML = '<p class="muted">Sin capas activas.</p>'; return; }
  const parts = [];
  for (const d of active.datasets) {
    const st = controller.dataset(d.id);
    if (!st?.visible) continue;
    const comp = d.composites.find((c) => c.id === st.selection);
    const title = `${esc(d.name)} · ${esc(comp ? comp.label : (d.variables.find((v) => v.id === st.selection)?.label || st.selection))}`;
    if (comp) {
      const rows = ['r', 'g', 'b'].map((ch) => {
        const vv = d.variables.find((v) => v.id === comp.channels[ch]);
        return `<div class="legend-item"><span class="sw ch-${ch}"></span>${ch.toUpperCase()} = ${vv ? `${esc(vv.label)} (${vv.legend ? `${vv.legend.min} a ${vv.legend.max} ` : ''}${esc(unitOf(vv.unit))})` : 'sin canal'}</div>`;
      }).join('');
      parts.push(`<div class="legend-block"><div class="legend-title">${title}</div>${rows}${comp.note ? `<small class="muted">${esc(comp.note)}</small>` : ''}</div>`);
      continue;
    }
    const v = d.variables.find((x) => x.id === st.selection);
    const lg = v?.legend;
    const ramp = lg?.colors ? `<div class="ramp" style="background:linear-gradient(90deg,${lg.colors.map(esc).join(',')})"></div>
      <div class="ramp-scale"><span>${lg.min ?? ''}</span><span>${esc(unitOf(v.unit))}</span><span>${lg.max ?? ''}</span></div>` : '<small class="muted">Leyenda no definida en la configuración</small>';
    parts.push(`<div class="legend-block"><div class="legend-title">${title}</div>${ramp}${lg?.nodata_label ? `<small class="muted">${esc(lg.nodata_label)}</small>` : ''}</div>`);
  }
  for (const v of active.vectors) {
    if (!v.legend || v._load !== 'ok' || !controller.isVectorVisible(v.id)) continue;
    parts.push(`<div class="legend-block"><div class="legend-title">${esc(v.legend.title || v.name)} ${v.simulated ? '<em class="badge">SIMULADO</em>' : ''}</div>
      ${(v.legend.items || []).map((i) => `<div class="legend-item"><span class="sw" style="background:${esc(i.color)}"></span>${esc(i.label)}</div>`).join('')}</div>`);
  }
  $('legend').innerHTML = parts.join('') || '<p class="muted">Activa una capa para ver su leyenda.</p>';
}

// ── Avisos de carga: se señala cuál capa falló y las demás siguen funcionando ──
function refreshStatus() {
  if (!active) return;
  const failed = [
    ...active.datasets.filter((d) => ['error'].includes(controller.dataset(d.id)?.status)).map((d) => d.name),
    ...active.vectors.filter((v) => v._load !== 'ok').map((v) => v.name),
  ];
  if (failed.length) setStatus('data', `No cargó: ${failed.join(', ')}. Las demás capas siguen disponibles.`);
  else if ($('status').className.includes('data')) setStatus('', '');
}

// ── Panel de atributos (vector + valores ráster en el punto) ──
const fmtVal = (x) => (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(2));

async function showInfo(layer, feature, lngLat) {
  const box = $('info');
  const samples = lngLat ? await controller.sampleAt(lngLat) : [];
  if (!layer && !samples.length) { box.hidden = true; return; }
  let html = '<button type="button" class="close" aria-label="Cerrar panel">×</button>';
  if (layer) {
    const p = feature.properties || {};
    const fields = layer.popupFields || Object.keys(p).map((k) => [k, k]);
    const rows = fields.map(([k, label, unit]) => {
      const has = p[k] !== undefined && p[k] !== null && p[k] !== '';
      return `<tr><th>${esc(label || k)}</th><td>${has ? `${esc(p[k])}${unit ? ` ${esc(unit)}` : ''}` : `<span class="na">${NA}</span>`}</td></tr>`;
    }).join('');
    const when = layer.time ? p[layer.time.property] : null;
    html += `<h3>${esc(p.name || p.zone_id || p.zone || layer.name)}</h3>
      <p class="muted">Vector · ${esc(layer.name)} · ${layer.simulated ? 'SIMULADO' : badgeOf(layer.status)}</p>
      ${layer.simulated ? '<p class="sim">SIMULADO: no es una medición ni una alerta.</p>' : ''}
      <p>${esc(layer.description || 'Sin descripción disponible.')}</p>
      <table><tr><th>Fecha del dato</th><td>${when ? esc(fmtTime(when)) : `<span class="na">${NA}</span>`}</td></tr>${rows}</table>`;
  }
  if (samples.length) {
    html += `<h4>Valores ráster en este punto</h4><table>${samples.map((s) => {
      const vals = s.values
        ? s.values.map((v) => `${esc(v.label)}: ${v.value == null ? `<span class="na">${NA}</span>` : `${fmtVal(v.value)} ${esc(unitOf(v.unit))}`}`).join('<br>')
        : `<span class="na">${NA} (ráster en teselas)</span>`;
      return `<tr><th>${esc(s.name)}<br><small>${fmtTime(s.frame.datetime)} · ${badgeOf(s.status)}</small></th><td>${vals}</td></tr>`;
    }).join('')}</table>`;
  }
  box.innerHTML = html;
  box.hidden = false;
  box.querySelector('.close').onclick = () => { box.hidden = true; };
}

// ── Comparador (swipe) ──
const swipeDatasets = () => active.datasets.filter((d) => d.source.type === 'image' && d.source.encoding === 'gray8' && d.frames.length >= 2);
const optionList = (items) => items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');

function setupSwipe() {
  const list = swipeDatasets();
  $('swipe-sec').hidden = !list.length;
  $('swipe-on').checked = false;
  $('swipe-ctl').hidden = true;
  $('swipe-labels').hidden = true;
  if (!list.length) return;
  $('sw-ds').innerHTML = optionList(list.map((d) => [d.id, d.name || d.id]));
  fillSwipeDataset();
}

function fillSwipeDataset() {
  const d = active.datasets.find((x) => x.id === $('sw-ds').value);
  $('sw-var').innerHTML = optionList([...d.variables.map((v) => [v.id, v.label || v.id]), ...d.composites.map((c) => [c.id, c.label || c.id])]);
  const frames = d.frames.map((f) => [f.id, fmtTime(f.datetime)]);
  $('sw-before').innerHTML = optionList(frames);
  $('sw-after').innerHTML = optionList(frames);
  $('sw-after').value = d.frames[d.frames.length - 1].id;
}

async function applySwipe() {
  const on = $('swipe-on').checked;
  $('swipe-ctl').hidden = !on;
  if (!on) { $('swipe-labels').hidden = true; await controller.setSwipe(null); paintAllDatasets(); return; }
  const d = active.datasets.find((x) => x.id === $('sw-ds').value);
  const label = (fid) => fmtTime(d.frames.find((f) => f.id === fid).datetime);
  try {
    await controller.setSwipe({
      datasetId: d.id, selection: $('sw-var').value, beforeId: $('sw-before').value, afterId: $('sw-after').value, split: Number($('sw-split').value) / 100,
    });
    $('sl-before').textContent = `Antes · ${label($('sw-before').value)}`;
    $('sl-after').textContent = `Después · ${label($('sw-after').value)}`;
    $('swipe-labels').hidden = false;
  } catch (err) {
    setStatus('error', `Comparador: ${err.message}`);
    $('swipe-on').checked = false;
    $('swipe-ctl').hidden = true;
    $('swipe-labels').hidden = true;
  }
}

// ── Tiempo ──
function renderTimeline() {
  const n = active.axis.length;
  $('time-sec').hidden = n < 1;
  $('slider').max = Math.max(0, n - 1);
}

async function goTo(i) {
  if (!active || !active.axis.length) return;
  idx = (i + active.axis.length) % active.axis.length;
  $('slider').value = idx;
  const tick = active.axis[idx];
  $('tlabel').textContent = `Fecha elegida: ${fmtTime(tick.label)} (${idx + 1} de ${active.axis.length})`;
  await controller.setTime(tick.ms);
  paintAllDatasets();
  paintVectorDates();
  renderLegend();
  refreshStatus();
  controller.prefetch(active.axis[(idx + 1) % active.axis.length].ms);
}

const paintAllDatasets = () => active?.datasets.forEach((d) => paintDataset(d.id));

function stopPlay() {
  playing = false;
  $('play').textContent = 'Reproducir';
}

async function play() {
  if (playing) return stopPlay();
  playing = true;
  $('play').textContent = 'Pausar';
  while (playing) {
    await sleep(Number($('speed').value));
    if (!playing) break;
    await goTo(idx + 1);
  }
}

// ── Zonas ──
function showWorldUI() {
  stopPlay();
  active = null;
  idx = 0;
  ['info', 'time-sec', 'ds-sec', 'swipe-sec', 'vec-sec', 'recenter', 'swipe-labels'].forEach((id) => { $(id).hidden = true; });
  $('case-select').value = '';
  $('case-desc').textContent = 'Gira el globo y toca un punto, o elige una zona de la lista.';
  $('legend').innerHTML = '<p class="muted">Sin capas activas.</p>';
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
  $('case-desc').textContent = def.description || '';
  $('demo-banner').hidden = def.status === 'real' && def.datasets.every((d) => d.status === 'real');
  setStatus('loading', 'Acercando a la zona…');
  try {
    await controller.loadCase(def);
  } catch (err) {
    setStatus('error', `No se pudo cargar el caso: ${err.message}`);
    return;
  }
  setStatus('', '');
  renderTimeline();
  renderDatasets();
  renderVectors();
  setupSwipe();
  await goTo(0);
}

// ── Eventos ──
$('case-select').onchange = (e) => selectCase(e.target.value);
$('home').onclick = () => selectCase('');
$('recenter').onclick = () => controller.flyToCase();
$('play').onclick = play;
$('prev').onclick = () => { stopPlay(); goTo(idx - 1); };
$('next').onclick = () => { stopPlay(); goTo(idx + 1); };
$('slider').oninput = (e) => { stopPlay(); goTo(Number(e.target.value)); };
$('status-x').onclick = () => setStatus('', '');
$('status-act').onclick = (e) => { if (e.target.dataset.action === 'retry-base') { controller.retryBase(); setStatus('', ''); } };

$('datasets').addEventListener('change', async (e) => {
  const id = e.target.closest('[data-ds]')?.dataset.ds;
  if (!id) return;
  if (e.target.dataset.act === 'vis') await controller.setDatasetVisible(id, e.target.checked);
  if (e.target.dataset.act === 'sel') await controller.setDatasetSelection(id, e.target.value);
  paintDataset(id);
  renderLegend();
  refreshStatus();
});
$('datasets').addEventListener('input', (e) => {
  if (e.target.dataset.act !== 'op') return;
  const id = e.target.closest('[data-ds]').dataset.ds;
  e.target.parentElement.querySelector('output').textContent = `${e.target.value}%`;
  controller.setDatasetOpacity(id, Number(e.target.value) / 100);
});
$('datasets').addEventListener('click', async (e) => {
  if (e.target.dataset.act !== 'retry') return;
  const id = e.target.closest('[data-ds]').dataset.ds;
  await controller.retryDataset(id);
  paintDataset(id);
  refreshStatus();
});
$('vectors').addEventListener('change', (e) => {
  if (!e.target.dataset.vec) return;
  controller.setVectorVisible(e.target.dataset.vec, e.target.checked);
  renderLegend();
});

$('swipe-on').onchange = applySwipe;
$('sw-ds').onchange = () => { fillSwipeDataset(); applySwipe(); };
['sw-var', 'sw-before', 'sw-after'].forEach((id) => { $(id).onchange = applySwipe; });
$('sw-split').oninput = (e) => controller.setSwipeSplit(Number(e.target.value) / 100);

$('mode').onchange = (e) => controller.setMode(e.target.value);
$('basemap').onchange = (e) => controller.setBasemap(e.target.value);
$('hillshade').onchange = (e) => controller.setHillshade(e.target.checked);
$('exag').oninput = (e) => { $('exag-out').textContent = `${String(e.target.value).replace('.', ',')}×`; controller.setExaggeration(Number(e.target.value)); };
$('sun').oninput = (e) => { $('sun-out').textContent = `${e.target.value}°`; controller.setSunAzimuth(Number(e.target.value)); };

controller.onFeature = showInfo;
controller.onCasePick = (id) => selectCase(id);
controller.onDatasetState = (id) => { paintDataset(id); refreshStatus(); };

// ── Arranque: globo con las zonas del catálogo ──
(async function init() {
  setStatus('loading', 'Cargando catálogo…');
  try {
    const result = await loadCatalog();
    cases = result.cases;
    result.warnings.forEach((w) => console.warn('[catálogo]', w));
    result.problems.forEach((p) => console.warn('[catálogo]', p));
    if (!cases.length) throw new Error(result.problems.join(' | ') || 'No hay casos registrados');
    $('case-select').innerHTML = `<option value="">Mundo (globo 3D)</option>${cases.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('')}`;
    await controller.ready;
    controller.setCases(cases);
    setStatus('', '');
    if (result.problems.length) setStatus('error', `Casos omitidos por error: ${result.problems.length}. Revisa la consola (o ejecuta npm run validate).`);
  } catch (err) {
    setStatus('error', `No se pudo cargar el catálogo: ${err.message}`);
  }
})();
