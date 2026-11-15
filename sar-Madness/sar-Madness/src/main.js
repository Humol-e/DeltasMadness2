import 'mapbox-gl/dist/mapbox-gl.css';
import './style.css';
import { loadCatalog, resolveUrl } from './lib/registry.js';
import { renderFrameCanvas } from './lib/rasterCanvas.js';
import {
  downloadSideBySidePng,
  downloadSingleRenderedPng,
  frameShortTitle,
  getBeforeAfterPair,
} from './lib/beforeAfterExport.js';
import { MapController } from './lib/mapController.js';

const esc = (s) =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const fmtBytes = (b) => {
  if (!Number.isFinite(b) || b <= 0) return '';
  return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`;
};

const app = document.querySelector('#app');
app.innerHTML = `
  <div class="app">
    <aside class="side">
      <div class="hdr">
        <h1>Monitoreo Satelital Real</h1>
        <span class="pill-real">100% Datos Reales</span>
      </div>

      <section class="sec">
        <label class="sec-title" for="case-select">Zona de estudio</label>
        <select id="case-select"></select>
        <p id="case-desc" class="muted"></p>
        <div id="case-metrics" class="metrics-grid"></div>
      </section>

      <section class="sec" id="layer-sec">
        <h2 class="sec-title">Capa y variable</h2>
        <div id="dataset-chips" class="chips"></div>
        <div id="var-chips" class="chips" hidden></div>
        <div id="legend-wrap" class="legend-bar" hidden>
          <div id="legend-ramp" class="legend-ramp"></div>
          <div class="legend-labels"><span id="legend-min"></span><span id="legend-max"></span></div>
        </div>
        <div class="range-row">
          <span>Opacidad en mapa</span>
          <input id="opacity-range" type="range" min="0.15" max="1" step="0.05" value="0.9" />
          <strong id="opacity-out">90%</strong>
        </div>
      </section>

      <section class="sec" id="ba-sec">
        <h2 class="sec-title">Visualización Antes / Después y Descargas PNG</h2>
        <div id="ba-grid" class="ba-grid"></div>
        <div id="ba-actions" class="actions-row">
          <button type="button" id="btn-dl-comp" class="btn btn-primary">⬇ Comparación Antes/Después (.png)</button>
          <button type="button" id="btn-open-modal" class="btn">⛶ Ampliar</button>
        </div>
        <div id="map-compare-bar" hidden>
          <div class="chips" style="margin-bottom:6px">
            <button type="button" class="chip" id="btn-view-before">Ver Antes en mapa</button>
            <button type="button" class="chip" id="btn-view-after">Ver Después en mapa</button>
            <button type="button" class="chip" id="btn-view-swipe">Dividir mapa (Swipe)</button>
          </div>
          <div id="swipe-slider-row" class="range-row" hidden>
            <span>División Antes | Después</span>
            <input id="swipe-split" type="range" min="0" max="1" step="0.01" value="0.5" />
            <strong id="swipe-out">50%</strong>
          </div>
        </div>
      </section>

      <section class="sec" id="vectors-sec" hidden>
        <h2 class="sec-title">Perímetros GeoJSON reales</h2>
        <div id="vectors-list" class="vec-list"></div>
      </section>

      <section class="sec" id="downloads-sec" hidden>
        <h2 class="sec-title">Datos crudos GeoTIFF reales (.tif)</h2>
        <div id="downloads-list" class="dl-list"></div>
      </section>
    </aside>

    <main class="stage">
      <div id="map"></div>
      <div class="map-overlay-top">
        <button type="button" id="world-btn" class="btn" hidden>🌐 Ver todas las zonas</button>
        <button type="button" id="terrain-btn" class="btn" hidden>🏔 Relieve 3D: ON</button>
        <span id="status-pill" class="pill-real" hidden></span>
      </div>
      <div id="inspect-pill" class="inspect-pill" hidden></div>
      <div id="token-notice" class="token-notice" hidden>
        <div class="token-card">
          <strong>Mapa interactivo Mapbox sin token configurado</strong>
          <p class="muted" style="margin-top:6px">
            Configura <code>VITE_MAPBOX_ACCESS_TOKEN</code> en <code>.env</code> y recarga para navegar sobre el globo 3D.
            Mientras tanto, todas las imágenes reales Antes/Después y GeoTIFFs descargables están activos en el panel izquierdo.
          </p>
        </div>
      </div>
    </main>
  </div>

  <dialog id="compare-modal">
    <div class="modal-head">
      <strong id="modal-title"></strong>
      <button type="button" id="modal-close" class="btn">Cerrar</button>
    </div>
    <div id="modal-grid" class="modal-grid"></div>
    <div class="modal-foot">
      <button type="button" id="modal-dl-before" class="btn">⬇ Antes (.png)</button>
      <button type="button" id="modal-dl-after" class="btn">⬇ Después (.png)</button>
      <button type="button" id="modal-dl-comp" class="btn btn-primary">⬇ Comparación Antes/Después (.png)</button>
    </div>
  </dialog>
`;

const el = (id) => document.querySelector(id);
const state = {
  cases: [],
  active: null,
  datasetId: null,
  varId: null,
  mapMode: 'before', // 'before' | 'after' | 'swipe'
  split: 0.5,
  renderSeq: 0,
  previews: { before: null, after: null },
};

const token = (import.meta.env.VITE_MAPBOX_ACCESS_TOKEN || '').trim();
const ctl = token
  ? new MapController({
      container: el('#map'),
      token,
      onStatus: (level, msg) => {
        const pill = el('#status-pill');
        pill.hidden = !msg;
        pill.textContent = msg || '';
        if (level === 'error') pill.style.color = '#fca5a5';
      },
    })
  : null;

if (!ctl) el('#token-notice').hidden = false;

const activeDataset = () => state.active?.datasets?.find((d) => d.id === state.datasetId) ?? null;

function setStatus(msg) {
  const pill = el('#status-pill');
  pill.hidden = !msg;
  pill.textContent = msg || '';
}

function renderCaseMetrics(def) {
  const m = def.metrics;
  const box = el('#case-metrics');
  if (!m) { box.innerHTML = ''; return; }
  const items = [];
  if (m.preWindow) items.push(['Ventana Antes', m.preWindow]);
  if (m.postWindow) items.push(['Ventana Después', m.postWindow]);
  if (m.affectedKm2 != null) items.push(['Área quemada detectada', `${m.affectedKm2} km²`]);
  if (m.observedKm2 != null) items.push(['Área observada', `${m.observedKm2} km²`]);
  if (m.snowPrePct != null && m.snowPostPct != null) items.push(['Nieve MODIS (Antes → Después)', `${m.snowPrePct}% → ${m.snowPostPct}%`]);
  if (m.lstPreC != null && m.lstPostC != null) items.push(['Temp. superficie', `${m.lstPreC}°C → ${m.lstPostC}°C`]);
  if (m.meltAreaKm2 != null) items.push(['Retroceso nival (ΔNDSI)', `${m.meltAreaKm2} km²`]);
  box.innerHTML = items.map(([k, v]) => `<div class="metric"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
}

function renderLegend(ds, varId) {
  const wrap = el('#legend-wrap');
  const v = (ds?.variables ?? []).find((x) => x.id === varId);
  if (!v?.legend?.colors?.length) { wrap.hidden = true; return; }
  wrap.hidden = false;
  el('#legend-ramp').style.background = `linear-gradient(90deg, ${v.legend.colors.join(', ')})`;
  const unit = v.unit ? ` ${v.unit}` : '';
  el('#legend-min').textContent = `${v.legend.min}${unit}`;
  el('#legend-max').textContent = `${v.legend.max}${unit}`;
}

function renderDatasetControls(def) {
  el('#dataset-chips').innerHTML = def.datasets
    .map((d) => `<button type="button" class="chip${d.id === state.datasetId ? ' active' : ''}" data-ds="${esc(d.id)}">${esc(d.name)}</button>`)
    .join('');

  el('#dataset-chips').querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => selectDataset(btn.dataset.ds));
  });

  const ds = activeDataset();
  const varBox = el('#var-chips');
  if (ds && ds.variables.length > 1) {
    varBox.hidden = false;
    varBox.innerHTML = ds.variables
      .map((v) => `<button type="button" class="chip${v.id === state.varId ? ' active' : ''}" data-v="${esc(v.id)}">${esc(v.label)}</button>`)
      .join('');
    varBox.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.varId = btn.dataset.v;
        renderDatasetControls(def);
        refreshLayer();
      });
    });
  } else {
    varBox.hidden = true;
    varBox.innerHTML = '';
  }

  const opacity = ds?.render?.opacity ?? 0.9;
  el('#opacity-range').value = String(opacity);
  el('#opacity-out').textContent = `${Math.round(opacity * 100)}%`;
  renderLegend(ds, state.varId);
}

function renderVectors(def) {
  const sec = el('#vectors-sec');
  const box = el('#vectors-list');
  const vecs = def.vectors ?? [];
  if (!vecs.length) { sec.hidden = true; return; }
  sec.hidden = false;
  box.innerHTML = vecs
    .map(
      (v) => `<label class="check">
        <input type="checkbox" data-vec="${esc(v.id)}" ${v.defaultVisible !== false ? 'checked' : ''} />
        <span>${esc(v.name || v.id)}</span>
      </label>`,
    )
    .join('');
  box.querySelectorAll('input').forEach((inp) => {
    inp.addEventListener('change', () => ctl?.setVectorVisible(inp.dataset.vec, inp.checked));
  });
}

function renderDownloads(def) {
  const sec = el('#downloads-sec');
  const box = el('#downloads-list');
  const all = def.downloads ?? [];
  if (!all.length) { sec.hidden = true; return; }
  sec.hidden = false;

  const primary = all.filter((d) => d.datasetId === state.datasetId);
  const others = all.filter((d) => d.datasetId !== state.datasetId);
  const shown = primary.length ? primary : all;
  const linkHtml = (dl) =>
    `<a class="dl-item" href="${esc(resolveUrl(dl.url))}" download>
      <span>⬇ ${esc(dl.label)}</span>
      <span class="dl-size">${esc(fmtBytes(dl.sizeBytes))}</span>
    </a>`;

  let html = shown.map(linkHtml).join('');
  if (primary.length && others.length) {
    html += `<details class="dl-more"><summary>Ver otros ${others.length} GeoTIFFs reales de este caso</summary><div class="dl-list">${others.map(linkHtml).join('')}</div></details>`;
  }
  box.innerHTML = html;
}

async function renderBeforeAfterPreviews(def, ds, varId) {
  const seq = ++state.renderSeq;
  const grid = el('#ba-grid');
  const { hasPair, before, after } = getBeforeAfterPair(ds);
  const compareBar = el('#map-compare-bar');
  compareBar.hidden = !ctl || !hasPair;
  el('#btn-dl-comp').hidden = !hasPair;

  const cards = [];
  if (hasPair && before && after) {
    grid.className = 'ba-grid';
    const [rb, ra] = await Promise.all([
      renderFrameCanvas(ds, before, varId),
      renderFrameCanvas(ds, after, varId),
    ]);
    if (seq !== state.renderSeq) return;
    state.previews = {
      before: { frame: before, url: rb.url },
      after: { frame: after, url: ra.url },
    };
    cards.push({ tag: 'ANTES', cls: 'ba-tag-before', date: frameShortTitle(before, 'Antes'), url: rb.url, phase: 'antes', frame: before });
    cards.push({ tag: 'DESPUÉS', cls: 'ba-tag-after', date: frameShortTitle(after, 'Después'), url: ra.url, phase: 'despues', frame: after });
  } else {
    grid.className = 'ba-grid single';
    const only = after || before;
    if (!only) { grid.innerHTML = ''; return; }
    const rendered = await renderFrameCanvas(ds, only, varId);
    if (seq !== state.renderSeq) return;
    state.previews = { before: null, after: { frame: only, url: rendered.url } };
    cards.push({ tag: 'OBSERVACIÓN REAL', cls: 'ba-tag-after', date: frameShortTitle(only, 'Resultado'), url: rendered.url, phase: 'real', frame: only });
  }

  grid.innerHTML = cards
    .map(
      (c, i) => `<div class="ba-card">
        <div class="ba-card-head"><span class="${c.cls}">${c.tag}</span></div>
        <div class="ba-date" title="${esc(c.date)}">${esc(c.date)}</div>
        <img class="ba-thumb" src="${c.url}" alt="${esc(c.tag)}" />
        <button type="button" class="btn btn-sm" data-dl-idx="${i}">⬇ Descargar ${c.tag} (.png)</button>
      </div>`,
    )
    .join('');

  grid.querySelectorAll('button[data-dl-idx]').forEach((btn) => {
    const item = cards[Number(btn.dataset.dlIdx)];
    btn.addEventListener('click', () => downloadSingleRenderedPng(def.id, ds, item.frame, varId, item.phase));
  });
}

async function syncMapToLayer() {
  if (!ctl || !state.active) return;
  const ds = activeDataset();
  if (!ds) return;
  const { hasPair, before, after } = getBeforeAfterPair(ds);

  if (state.mapMode === 'swipe' && hasPair && before && after) {
    el('#swipe-slider-row').hidden = false;
    await ctl.setSwipe({
      datasetId: ds.id,
      beforeId: before.id,
      afterId: after.id,
      selection: state.varId,
      split: state.split,
    });
  } else {
    el('#swipe-slider-row').hidden = true;
    await ctl.setSwipe(null);
    const frame = state.mapMode === 'after' || !hasPair ? after || before : before;
    if (frame) await ctl.setTime(frame.ms);
  }

  for (const d of state.active.datasets) {
    await ctl.setDatasetVisible(d.id, d.id === ds.id);
  }
  await ctl.setDatasetSelection(ds.id, state.varId);
  ctl.setDatasetOpacity(ds.id, Number(el('#opacity-range').value));

  el('#btn-view-before').classList.toggle('active', state.mapMode === 'before');
  el('#btn-view-after').classList.toggle('active', state.mapMode === 'after');
  el('#btn-view-swipe').classList.toggle('active', state.mapMode === 'swipe');
}

async function refreshLayer() {
  const def = state.active;
  const ds = activeDataset();
  if (!def || !ds) return;
  renderLegend(ds, state.varId);
  renderDownloads(def);
  const { hasPair } = getBeforeAfterPair(ds);
  if (!hasPair && state.mapMode === 'swipe') state.mapMode = 'after';
  await Promise.all([renderBeforeAfterPreviews(def, ds, state.varId), syncMapToLayer()]);
}

function selectDataset(datasetId) {
  const ds = state.active?.datasets?.find((d) => d.id === datasetId);
  if (!ds) return;
  state.datasetId = ds.id;
  state.varId = ds.variables[0].id;
  if (getBeforeAfterPair(ds).hasPair && state.mapMode === 'before') state.mapMode = 'before';
  renderDatasetControls(state.active);
  refreshLayer();
}

async function selectCase(caseId) {
  const def = state.cases.find((c) => c.id === caseId);
  if (!def) return;
  state.active = def;
  el('#case-select').value = def.id;
  el('#case-desc').textContent = def.description || '';
  el('#world-btn').hidden = !ctl;
  el('#terrain-btn').hidden = !ctl;
  el('#terrain-btn').textContent = '🏔 Relieve 3D: ON';
  history.replaceState(null, '', `#${def.id}`);
  renderCaseMetrics(def);

  const preferred = def.datasets.find((d) => getBeforeAfterPair(d).hasPair) ?? def.datasets[0];
  state.datasetId = preferred?.id ?? null;
  state.varId = preferred?.variables?.[0]?.id ?? null;
  state.mapMode = getBeforeAfterPair(preferred).hasPair ? 'before' : 'after';

  renderDatasetControls(def);
  renderVectors(def);
  renderDownloads(def);

  const previewsJob = preferred ? renderBeforeAfterPreviews(def, preferred, state.varId) : Promise.resolve();
  const mapJob = ctl ? ctl.loadCase(def).then(() => syncMapToLayer()) : Promise.resolve();
  await Promise.all([previewsJob, mapJob]);
}

function openComparisonModal() {
  const def = state.active;
  const ds = activeDataset();
  if (!def || !ds) return;
  const { hasPair, before, after } = getBeforeAfterPair(ds);
  const v = ds.variables.find((x) => x.id === state.varId);
  el('#modal-title').textContent = `${def.title} · ${ds.name}${v ? ` (${v.label})` : ''}`;

  const modalGrid = el('#modal-grid');
  const cards = [];
  if (hasPair && state.previews.before && state.previews.after) {
    modalGrid.className = 'modal-grid';
    cards.push({ title: `ANTES · ${frameShortTitle(before, 'Pre')}`, url: state.previews.before.url });
    cards.push({ title: `DESPUÉS · ${frameShortTitle(after, 'Post')}`, url: state.previews.after.url });
    el('#modal-dl-before').hidden = false;
    el('#modal-dl-comp').hidden = false;
  } else if (state.previews.after) {
    modalGrid.className = 'modal-grid single';
    cards.push({ title: frameShortTitle(after, 'Observación real'), url: state.previews.after.url });
    el('#modal-dl-before').hidden = true;
    el('#modal-dl-comp').hidden = true;
  }
  modalGrid.innerHTML = cards
    .map((c) => `<div class="modal-card"><div class="ba-card-head"><span>${esc(c.title)}</span></div><img src="${c.url}" alt="${esc(c.title)}" /></div>`)
    .join('');

  el('#compare-modal').showModal();
}

function bindEvents() {
  el('#case-select').addEventListener('change', (e) => selectCase(e.target.value));
  el('#world-btn').addEventListener('click', () => {
    el('#world-btn').hidden = true;
    el('#terrain-btn').hidden = true;
    ctl?.showWorld();
  });
  el('#terrain-btn').addEventListener('click', () => {
    if (!ctl) return;
    const next = ctl.mode === 'terrain' ? 'flat' : 'terrain';
    ctl.setMode(next);
    ctl.setHillshade(next === 'terrain');
    el('#terrain-btn').textContent = next === 'terrain' ? '🏔 Relieve 3D: ON' : '🏔 Relieve 3D: OFF';
  });

  el('#opacity-range').addEventListener('input', (e) => {
    const val = Number(e.target.value);
    el('#opacity-out').textContent = `${Math.round(val * 100)}%`;
    if (state.datasetId) ctl?.setDatasetOpacity(state.datasetId, val);
  });

  el('#btn-view-before').addEventListener('click', () => { state.mapMode = 'before'; syncMapToLayer(); });
  el('#btn-view-after').addEventListener('click', () => { state.mapMode = 'after'; syncMapToLayer(); });
  el('#btn-view-swipe').addEventListener('click', () => { state.mapMode = 'swipe'; syncMapToLayer(); });

  el('#swipe-split').addEventListener('input', (e) => {
    state.split = Number(e.target.value);
    el('#swipe-out').textContent = `${Math.round(state.split * 100)}%`;
    ctl?.setSwipeSplit(state.split);
  });

  el('#btn-dl-comp').addEventListener('click', () => {
    const ds = activeDataset();
    if (state.active && ds) downloadSideBySidePng(state.active, ds, state.varId);
  });
  el('#btn-open-modal').addEventListener('click', openComparisonModal);
  el('#modal-close').addEventListener('click', () => el('#compare-modal').close());
  el('#compare-modal').addEventListener('click', (e) => {
    if (e.target === el('#compare-modal')) el('#compare-modal').close();
  });

  el('#modal-dl-before').addEventListener('click', () => {
    const ds = activeDataset();
    const { before } = getBeforeAfterPair(ds);
    if (state.active && ds && before) downloadSingleRenderedPng(state.active.id, ds, before, state.varId, 'antes');
  });
  el('#modal-dl-after').addEventListener('click', () => {
    const ds = activeDataset();
    const { after, before } = getBeforeAfterPair(ds);
    const frame = after || before;
    if (state.active && ds && frame) downloadSingleRenderedPng(state.active.id, ds, frame, state.varId, 'despues');
  });
  el('#modal-dl-comp').addEventListener('click', () => {
    const ds = activeDataset();
    if (state.active && ds) downloadSideBySidePng(state.active, ds, state.varId);
  });

  if (ctl) {
    ctl.onCasePick = (id) => selectCase(id);
    let hoverTick = 0;
    ctl.map.on('mousemove', (e) => {
      const now = performance.now();
      if (now - hoverTick < 70 || !state.active) return;
      hoverTick = now;
      ctl.sampleAt(e.lngLat).then((hits) => {
        const pill = el('#inspect-pill');
        if (!hits.length) { pill.hidden = true; return; }
        const h = hits[0];
        const lines = h.values
          ? h.values.map((v) => `${esc(v.label)}: <strong>${v.value == null ? 'sin dato' : `${v.value.toFixed(2)} ${esc(v.unit || '')}`}</strong>`)
          : [`${esc(h.name)}`];
        pill.hidden = false;
        pill.innerHTML = `<div class="muted">${e.lngLat.lng.toFixed(4)}, ${e.lngLat.lat.toFixed(4)}</div>${lines.join('<br>')}`;
      });
    });
    ctl.map.on('mouseout', () => { el('#inspect-pill').hidden = true; });
  }
}

async function boot() {
  bindEvents();
  const { cases, problems } = await loadCatalog('data/catalog.json');
  state.cases = cases;
  if (problems.length) setStatus(`Aviso: ${problems[0]}`);
  if (!cases.length) {
    el('#case-desc').textContent = 'No hay casos con datos reales disponibles en data/catalog.json.';
    return;
  }

  const incendios = cases.filter((c) => c.category === 'incendio' || c.id.startsWith('wildfire-') || Boolean(c.wildfire));
  const deshielo = cases.filter((c) => !incendios.includes(c));
  const groups = [
    ['Incendios forestales reales', incendios],
    ['Deshielo glaciar real', deshielo],
  ].filter(([, list]) => list.length);

  el('#case-select').innerHTML = groups
    .map(
      ([label, list]) =>
        `<optgroup label="${esc(label)}">${list.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join('')}</optgroup>`,
    )
    .join('');

  ctl?.setCases(cases);
  const hashId = location.hash.slice(1);
  const initial = cases.find((c) => c.id === hashId) ?? cases[0];
  await selectCase(initial.id);
}

boot();
