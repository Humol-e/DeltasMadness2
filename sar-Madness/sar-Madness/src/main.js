import './style.css';
import { loadCatalog, resolveUrl } from './lib/registry.js';
import { getDiscreteSwatches, legendCssBackground, renderFrameCanvas } from './lib/rasterCanvas.js';
import {
  downloadSideBySidePng,
  downloadSingleRenderedPng,
  frameShortTitle,
  getBeforeAfterPair,
} from './lib/beforeAfterExport.js';
import {
  DEFAULT_LANG,
  getLocalizedCaseDescription,
  getLocalizedCaseTitle,
  getUiCopy,
  translateTerm,
} from './lib/i18n.js';
import {
  buildCliCommands,
  findNearestCase,
  geocodePlaces,
  parseCoordinates,
  searchLocalMatches,
} from './lib/placeSearch.js';
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

let visibleHomeCaseIds = null;
let homeCasePriority = [];
let homeCaseDisplayOrder = [];
const homeCaseHideTimers = new WeakMap();
let homeCasesReady = false;
let pageTransitionId = 0;
let pageTransitionTimers = [];

const app = document.querySelector('#app');
app.innerHTML = `
  <div class="app">
    <main class="stage">
      <div id="map"></div>
      <div class="map-overlay-top">
        <span id="status-pill" class="pill-real" hidden></span>
      </div>
      <div id="inspect-pill" class="inspect-pill" hidden></div>
      <div id="token-notice" class="token-notice" hidden>
        <div class="token-card">
          <strong>WebGL map renderer unavailable</strong>
          <p class="muted" style="margin-top:6px">
            Using open MapLibre basemaps does not require a token, but WebGL could not initialize in this browser. Enable hardware acceleration (or optionally set <code>VITE_MAPBOX_ACCESS_TOKEN</code> in <code>.env</code>).
          </p>
        </div>
      </div>
    </main>

    <header class="site-header">
      <a
        href="https://www.deltaanahuac.engineer/"
        target="_blank"
        rel="noopener noreferrer"
        class="logo"
        aria-label="Delta Anahuac website"
      >
        <img src="${resolveUrl('images/delta-logo.png')}" alt="Delta logo" class="logo-mark" />
      </a>
      <a
        href="https://github.com/Humol-e/DeltasMadness2"
        target="_blank"
        rel="noopener noreferrer"
        class="github-link"
        aria-label="View repository on GitHub"
        title="View repository on GitHub"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 .5a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.25c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.74.08-.74 1.2.09 1.83 1.23 1.83 1.23 1.07 1.83 2.8 1.3 3.48.99.11-.77.42-1.3.76-1.6-2.67-.3-5.47-1.34-5.47-5.95 0-1.31.47-2.38 1.23-3.22-.12-.3-.53-1.52.12-3.18 0 0 1-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.3-1.55 3.3-1.23 3.3-1.23.65 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.62-2.8 5.64-5.48 5.94.43.37.81 1.1.81 2.22v3.29c0 .32.22.69.83.57A12 12 0 0 0 12 .5Z"/>
        </svg>
      </a>
      <div class="header-right">
        <nav aria-label="Main navigation">
          <ul>
            <li><button type="button" class="nav-link active" data-page="home">Home</button></li>
            <li><button type="button" class="nav-link" data-page="permafrost">Cryosphere</button></li>
            <li><button type="button" class="nav-link" data-page="wildfires">Wildfires</button></li>
            <li><button type="button" class="nav-link" data-page="deforestation">Biomass</button></li>
          </ul>
        </nav>
        <div class="lang-switch" role="group" aria-label="Language selector">
          <button
            type="button"
            class="lang-btn is-active"
            data-lang="en"
            aria-pressed="true"
            aria-label="English"
          ><span class="lang-full">English</span><span class="lang-short">EN</span></button>
          <button
            type="button"
            class="lang-btn"
            data-lang="es"
            aria-pressed="false"
            aria-label="Español"
          ><span class="lang-full">Español</span><span class="lang-short">ES</span></button>
        </div>
      </div>
    </header>

    <main class="view-container">
      <section class="view active" aria-live="polite">
        <button id="monitoring-mode-toggle" class="monitoring-mode-toggle" type="button" aria-pressed="false" aria-label="Switch to 3D view" hidden>2D</button>
        <aside id="monitoring-info-card" class="monitoring-info-card" aria-labelledby="monitoring-info-title" aria-live="polite" hidden></aside>
        <aside id="monitoring-controls" class="monitoring-controls" aria-label="Image controls and downloads" hidden></aside>
        <div id="mobile-monitoring-bar" class="mobile-monitoring-bar" role="tablist" aria-label="Monitoring views" hidden>
          <button type="button" class="mobile-monitoring-tab is-active" data-mobile-pane="info" role="tab" aria-selected="true">Data &amp; Metrics</button>
          <button type="button" class="mobile-monitoring-tab" data-mobile-pane="controls" role="tab" aria-selected="false">Controls</button>
          <button type="button" class="mobile-monitoring-tab" data-mobile-pane="map" role="tab" aria-selected="false">Map</button>
        </div>
        <div class="hero-group">
          <div class="text-content">
            <p class="eyebrow">Satellite monitoring</p>
            <h1 id="hero-title">DELTA'S SAR</h1>
            <p id="hero-description">When Earth changes irreversibly, every second of anticipation saves ecosystems.</p>
            <button id="hero-data-guide-btn" type="button" class="data-guide-button">How we could use this data</button>
          </div>
          <ol id="case-indicators" class="case-indicators" aria-label="Events in the current category" hidden></ol>
          <section id="case-detail-panel" class="case-detail-panel" aria-label="Event details" hidden></section>
        </div>
        <section id="home-cases" class="home-cases" aria-label="Observed events">
          <div class="home-cases-head">
            <h2>Observed events</h2>
          </div>
          <div class="home-search-wrap">
            <input id="home-search-input" class="home-search-input" type="search" placeholder="Search event or lat, lon…" aria-label="Search events or coordinates" autocomplete="off" />
            <div id="home-search-results" class="home-search-results" hidden></div>
          </div>
          <div id="home-case-grid" class="home-case-grid"></div>
        </section>
      </section>
    </main>

    <aside class="side" hidden aria-hidden="true">
      <div class="hdr">
        <h1>Real Satellite Monitoring</h1>
        <span class="pill-real">100% Real Data</span>
      </div>
      <section class="sec">
        <label class="sec-title" for="case-select">Study site</label>
        <select id="case-select"></select>
        <p id="case-desc" class="muted"></p>
        <div id="case-metrics" class="metrics-grid"></div>
      </section>
      <section class="sec" id="layer-sec">
        <h2 class="sec-title">Layer and variable</h2>
        <div id="dataset-chips" class="chips"></div>
        <div id="var-chips" class="chips" hidden></div>
        <div id="legend-wrap" class="legend-bar" hidden>
          <div id="legend-ramp" class="legend-ramp"></div>
          <div class="legend-labels"><span id="legend-min"></span><span id="legend-max"></span></div>
        </div>
        <div class="range-row">
          <span>Map opacity</span>
          <input id="opacity-range" type="range" min="0.15" max="1" step="0.05" value="0.9" />
          <strong id="opacity-out">90%</strong>
        </div>
      </section>
      <section class="sec" id="ba-sec">
        <h2 class="sec-title">Before / After View & PNG Downloads</h2>
        <div id="ba-grid" class="ba-grid"></div>
        <div id="ba-actions" class="actions-row">
          <button type="button" id="btn-dl-comp" class="btn btn-primary">⬇ Before/After Comparison (.png)</button>
          <button type="button" id="btn-open-modal" class="btn">⛶ Expand</button>
        </div>
        <div id="map-compare-bar" hidden>
          <div class="chips" style="margin-bottom:6px">
            <button type="button" class="chip" id="btn-view-before">View Before on map</button>
            <button type="button" class="chip" id="btn-view-after">View After on map</button>
            <button type="button" class="chip" id="btn-view-swipe">Split map (Swipe)</button>
          </div>
          <div id="swipe-slider-row" class="range-row" hidden>
            <span>Split Before | After</span>
            <input id="swipe-split" type="range" min="0" max="1" step="0.01" value="0.5" />
            <strong id="swipe-out">50%</strong>
          </div>
        </div>
      </section>
      <section class="sec" id="vectors-sec" hidden>
        <h2 class="sec-title">Real GeoJSON perimeters</h2>
        <div id="vectors-list" class="vec-list"></div>
      </section>
      <section class="sec" id="downloads-sec" hidden>
        <h2 class="sec-title">Raw Real GeoTIFF Data (.tif)</h2>
        <div id="downloads-list" class="dl-list"></div>
      </section>
      <button type="button" id="world-btn" hidden>View all sites</button>
      <button type="button" id="terrain-btn" hidden>3D Terrain: ON</button>
    </aside>
  </div>

  <dialog id="compare-modal">
    <div class="modal-head">
      <strong id="modal-title"></strong>
      <button type="button" id="modal-close" class="btn">Close</button>
    </div>
    <div id="modal-grid" class="modal-grid"></div>
    <div class="modal-foot">
      <button type="button" id="modal-dl-before" class="btn">⬇ Before (.png)</button>
      <button type="button" id="modal-dl-after" class="btn">⬇ After (.png)</button>
      <button type="button" id="modal-dl-comp" class="btn btn-primary">⬇ Before/After Comparison (.png)</button>
    </div>
  </dialog>

  <dialog id="data-guide-modal" aria-labelledby="data-guide-title">
    <div class="data-guide-head">
      <div>
        <span class="data-guide-eyebrow">Simple Guide</span>
        <h2 id="data-guide-title">How We Could Use This Data</h2>
        <p>Every map layer and metric in Delta comes from real satellite measurements. Here is what each number and image tells us and how people can use it.</p>
      </div>
      <button type="button" id="data-guide-close" class="data-guide-close">Close</button>
    </div>
    <div class="data-guide-body">
      <article class="data-guide-card" data-guide-category="wildfires">
        <span class="data-guide-card-tag">Wildfires</span>
        <h3>Fire Scars, Smoke Penetration &amp; Damage</h3>
        <ul class="data-guide-list">
          <li><strong>Sentinel-2 RGB (True Color):</strong> Shows the land before and after the fire just as our eyes would see it.</li>
          <li><strong>NBR / dNBR Burn Severity:</strong> Uses infrared light to show which patches burned lightly vs. which lost almost all vegetation.</li>
          <li><strong>Sentinel-1 SAR &amp; GeoPulse Fusion:</strong> Radar sees right through dense smoke and clouds, day or night, to detect tree and terrain damage.</li>
          <li><strong>Burned Area &amp; Study Area (km²):</strong> Measures how much land actually burned inside the monitored region and during which dates.</li>
        </ul>
        <p class="data-guide-takeaway"><strong>How we use it:</strong> Guide firefighters past heavy smoke, focus tree-planting and erosion barriers on the worst-hit slopes, and speed up emergency aid or insurance claims.</p>
      </article>

      <article class="data-guide-card" data-guide-category="permafrost">
        <span class="data-guide-card-tag">Permafrost &amp; Glaciers</span>
        <h3>Ice Retreat, Meltwater &amp; Surface Heat</h3>
        <ul class="data-guide-list">
          <li><strong>Snow Cover (NDSI &amp; MODIS Snow %):</strong> Separates clean snow and ice from exposed rock or mud to measure how fast glaciers are shrinking.</li>
          <li><strong>Surface Temperature (°C):</strong> Tracks warming trends on high peaks and tundra pushing frozen basins above the melting point.</li>
          <li><strong>Sentinel-1 Radar &amp; Melt Area (km²):</strong> Detects wet snow and liquid water forming on ice—even under thick clouds—and totals the thawing surface.</li>
        </ul>
        <p class="data-guide-takeaway"><strong>How we use it:</strong> Predict seasonal drinking and irrigation water for downstream towns, spot dangerous glacial lakes before floodwaters burst, and watch permafrost zones where thawing releases greenhouse gases.</p>
      </article>

      <article class="data-guide-card" data-guide-category="deforestation">
        <span class="data-guide-card-tag">Deforestation</span>
        <h3>Canopy Mass, Clearing &amp; Stored Carbon</h3>
        <ul class="data-guide-list">
          <li><strong>NISAR Aboveground Biomass (AGB · Mg/ha):</strong> Estimates how many metric tons of wood and living plant mass stand in each hectare of forest.</li>
          <li><strong>L-Band Radar (HH / HV) &amp; ΔAGB Change:</strong> Long radar waves pierce beneath the leafy canopy to measure trunk volume and pinpoint exact patches where trees were felled or damaged.</li>
          <li><strong>Carbon Loss (Gg C) &amp; Severe Loss (km²):</strong> Converts missing wood biomass into kilotons of carbon no longer stored by the forest.</li>
        </ul>
        <p class="data-guide-takeaway"><strong>How we use it:</strong> Catch illegal logging early, verify that carbon-credit and reforestation programs really protect trees, and plan sustainable forestry without clearing intact habitats.</p>
      </article>

      <article class="data-guide-card" data-guide-category="exports">
        <span class="data-guide-card-tag">Reports &amp; GIS Exports</span>
        <h3>Turning Satellite Views into Action</h3>
        <ul class="data-guide-list">
          <li><strong>Before / After Slider &amp; Pixel Probe:</strong> Hover anywhere over a layer to read exact real values (like biomass in Mg/ha, dB backscatter, or burn class) at exact GPS coordinates.</li>
          <li><strong>Side-by-Side Comparison PNG:</strong> Downloads a clean before-and-after picture with dates and legend ready for presentations, schools, or civic reports.</li>
          <li><strong>Raw GeoTIFF (.tif) Files:</strong> Industry-standard georeferenced maps that engineers, NGOs, and disaster teams can load straight into QGIS, ArcGIS, or Python.</li>
        </ul>
        <p class="data-guide-takeaway"><strong>How we use it:</strong> Anyone—from local planners to researchers—can move smoothly from quick visual evidence to full geographic analysis in external mapping tools.</p>
      </article>
    </div>
  </dialog>
`;

const el = (id) => document.querySelector(id);
const state = {
  lang: DEFAULT_LANG,
  cases: [],
  active: null,
  currentPage: 'home',
  datasetId: null,
  varId: null,
  frameId: null,
  mapMode: 'before', // 'before' | 'after' | 'swipe'
  split: 0.5,
  renderSeq: 0,
  caseSeq: 0,
  hoverSeq: 0,
  previews: { before: null, after: null },
};

const copy = () => getUiCopy(state.lang);

const token = (import.meta.env.VITE_MAPBOX_ACCESS_TOKEN || '').trim();
let ctl = null;
let refreshInspectPoint = () => {};

async function loadMapEngine(engine) {
  if (engine === 'mapbox') {
    const [mod] = await Promise.all([
      import('mapbox-gl'),
      import('mapbox-gl/dist/mapbox-gl.css'),
    ]);
    return mod.default || mod;
  }
  const [mod, workerMod] = await Promise.all([
    import('maplibre-gl'),
    import('maplibre-gl/dist/maplibre-gl-worker.mjs?url'),
    import('maplibre-gl/dist/maplibre-gl.css'),
  ]);
  const lib = mod.default || mod;
  if (typeof lib.setWorkerUrl === 'function' && workerMod?.default) {
    lib.setWorkerUrl(workerMod.default);
  }
  return lib;
}

function attachControllerHandlers(controller) {
  controller.onVisibleCasesChange = updateHomeCaseVisibility;
  controller.onWorldTransitionEnd = () => {
    if (state.currentPage !== 'home') return;
    homeCasesReady = true;
    updateHomeCaseVisibility(visibleHomeCaseIds ? [...visibleHomeCaseIds] : []);
  };
  controller.onCasePick = openCase;
  controller.onWorldClick = () => setPage('home');
  controller.onFeature = (layer, feature, lngLat) => {
    if (!layer || !feature) {
      controller.closePopup();
      return;
    }
    const props = feature.properties ?? {};
    const formatVal = (val, unit) => {
      if (val == null || val === '') return null;
      const num = typeof val === 'number'
        ? val
        : Number.isFinite(Number(val)) && String(val).trim() !== '' ? Number(val) : null;
      const formatted = num != null
        ? num.toLocaleString(state.lang === 'es' ? 'es-MX' : 'en-US', { maximumFractionDigits: 3 })
        : String(val);
      const u = unit ? tTerm(unit) : '';
      return u ? `${formatted} ${u}` : formatted;
    };
    const fields = Array.isArray(layer.popupFields) && layer.popupFields.length
      ? layer.popupFields
      : [['name', 'Area']];
    const rows = fields
      .map((entry) => {
        const [key, label, unit] = Array.isArray(entry) ? entry : [entry, entry];
        const formatted = formatVal(props[key], unit);
        return formatted ? `<div><dt>${esc(tTerm(label || key))}</dt><dd>${esc(formatted)}</dd></div>` : '';
      })
      .filter(Boolean)
      .join('');
    const html = `<div class="delta-popup-card">
      <strong class="delta-popup-title">${esc(tTerm(layer.name || layer.id))}</strong>
      ${rows ? `<dl class="delta-popup-rows">${rows}</dl>` : ''}
    </div>`;
    controller.showPopup(lngLat, html);
  };
  const samplePoint = (lngLat, { hideOnMiss = true } = {}) => {
    state.lastInspectLngLat = lngLat;
    const hSeq = ++state.hoverSeq;
    controller.sampleAt(lngLat).then((hits) => {
      if (hSeq !== state.hoverSeq || !state.active || !el('.view').classList.contains('is-monitoring')) return;
      const pill = el('#inspect-pill');
      if (!hits.length) {
        if (hideOnMiss) pill.hidden = true;
        return;
      }
      const h = hits[0];
      const mCopy = copy().monitoring;
      const ds = activeDataset();
      const noDataText = copy().inspect.noData;
      const phaseName = h.phase === 'before' ? mCopy.before
        : h.phase === 'after' ? mCopy.after
        : h.phase === 'change' ? mCopy.change : '';
      const phaseDate = h.frame ? tFrame(h.frame, h.frame.datetime || '') : '';
      const headerText = phaseName
        ? `${phaseName}${phaseDate ? ` · ${phaseDate}` : ''} · ${lngLat.lng.toFixed(4)}, ${lngLat.lat.toFixed(4)}`
        : `${lngLat.lng.toFixed(4)}, ${lngLat.lat.toFixed(4)}`;
      const lines = h.values
        ? h.values.map((v) => {
            const variableDef = ds?.variables?.find((x) => x.label === v.label);
            const isDiscrete = Boolean(variableDef?.legend?.discrete);
            let valueHtml;
            if (v.value == null) {
              valueHtml = esc(noDataText);
            } else if (isDiscrete) {
              const intVal = Math.round(v.value);
              const localizedNote = variableDef.legend.nodata_label ? tTerm(variableDef.legend.nodata_label) : '';
              const match = getDiscreteSwatches({ ...variableDef.legend, nodata_label: localizedNote })
                ?.find((s) => Number(s.key) === intVal);
              valueHtml = match
                ? `${esc(tTerm(match.label))}`
                : `${intVal}${v.unit ? ` ${esc(tTerm(v.unit))}` : ''}`;
            } else {
              valueHtml = `${v.value.toFixed(2)}${v.unit ? ` ${esc(tTerm(v.unit))}` : ''}`;
            }
            return `${esc(tTerm(v.label))}: <strong>${valueHtml}</strong>`;
          })
        : [`${esc(tTerm(h.name))}`];
      pill.hidden = false;
      pill.innerHTML = `<div class="muted">${esc(headerText)}</div>${lines.join('<br>')}`;
    });
  };
  refreshInspectPoint = (lngLat) => samplePoint(lngLat, { hideOnMiss: false });
  let hoverTick = 0;
  controller.map.on('mousemove', (e) => {
    const now = performance.now();
    if (now - hoverTick < 70 || !state.active || !el('.view').classList.contains('is-monitoring')) return;
    hoverTick = now;
    samplePoint(e.lngLat);
  });
  controller.map.on('click', (e) => {
    if (!state.active || !el('.view').classList.contains('is-monitoring')) return;
    samplePoint(e.lngLat);
  });
  controller.map.on('mouseout', () => {
    state.hoverSeq++;
    state.lastInspectLngLat = null;
    el('#inspect-pill').hidden = true;
  });
}

async function initMapController({ forceMapLibre = false } = {}) {
  const preferredEngine = !forceMapLibre && token ? 'mapbox' : 'maplibre';
  try {
    const glLib = await loadMapEngine(preferredEngine);
    ctl = new MapController({
      container: el('#map'),
      token: preferredEngine === 'mapbox' ? token : '',
      glLib,
      engine: preferredEngine,
      lang: state.lang,
      onStatus: (level, msg) => setStatus(msg, { level }),
      onAuthError: () => {
        if (preferredEngine === 'mapbox') failoverToMapLibre();
      },
    });
    attachControllerHandlers(ctl);
    el('#token-notice').hidden = true;
  } catch (err) {
    if (preferredEngine === 'mapbox') {
      return initMapController({ forceMapLibre: true });
    }
    console.error('Map initialization failed:', err);
    ctl = null;
    el('#token-notice').hidden = false;
    homeCasesReady = true;
  }
}

async function failoverToMapLibre() {
  const prevActive = state.active;
  const isMonitoring = Boolean(prevActive && el('.view')?.classList.contains('is-monitoring'));
  ctl?.destroy();
  ctl = null;
  await initMapController({ forceMapLibre: true });
  if (!ctl) return;
  ctl.setCases(state.cases);
  if (prevActive) {
    await ctl.loadCase(prevActive, { globeOverview: !isMonitoring, hideDatasets: !isMonitoring });
    if (isMonitoring) await ctl.focusCurrentCase();
  } else {
    await ctl.showWorld();
  }
}

const activeDataset = () => state.active?.datasets?.find((d) => d.id === state.datasetId) ?? null;
const supportsBeforeAfterSwipe = (dataset) => {
  const pair = getBeforeAfterPair(dataset);
  return Boolean(dataset?.source?.type === 'image' && pair.hasPair && pair.before && pair.after);
};

const categoryForCase = (def) => {
  if (def.category === 'wildfire' || def.category === 'incendio' || def.id.startsWith('wildfire-') || Boolean(def.wildfire)) {
    return 'wildfires';
  }
  if (def.category === 'biomass' || def.category === 'biomasa' || def.id.startsWith('biomass-')) {
    return 'deforestation';
  }
  return 'permafrost';
};

const caseImageFiles = {
  'wildfire-camp-paradise': 'paradise-california.jpg',
  'wildfire-martin-nv': 'martin-nevada.jpg',
  'wildfire-palisades-la': 'palisades-los-angeles.jpg',
  'glacier-langtang': 'langtang-valley-nepal.jpg',
  'glacier-khumbu': 'khumbu-glacier-nepal.jpg',
  'biomass-leno-nisar': 'lenoir-landing-alabama.jpg',
};

const tCaseTitle = (def) => getLocalizedCaseTitle(def, state.lang);
const tCaseDesc = (def) => getLocalizedCaseDescription(def, state.lang);
const tTerm = (text) => translateTerm(text, state.lang);
const tFrame = (frame, fallback) => frameShortTitle(frame, fallback, state.lang);

function getCaseCardImageUrl(def) {
  if (caseImageFiles[def.id]) return resolveUrl(`images/cases/${caseImageFiles[def.id]}`);
  const rgbaDs = (def.datasets ?? []).find((d) => d.source?.encoding === 'rgba' && d.frames?.length);
  const frame = rgbaDs?.frames.find((f) => f.phase === 'after') ?? rgbaDs?.frames[rgbaDs.frames.length - 1];
  const file = frame ? Object.values(frame.files ?? {})[0] : null;
  return file ? resolveUrl(file) : null;
}

function renderDataGuideModal() {
  const modal = el('#data-guide-modal');
  if (!modal) return;
  const highlighted = new Set(
    [...modal.querySelectorAll('.data-guide-card.is-highlighted')].map((card) => card.dataset.guideCategory),
  );
  const guide = copy().dataGuide;
  modal.innerHTML = `
    <div class="data-guide-head">
      <div>
        <span class="data-guide-eyebrow">${esc(guide.eyebrow)}</span>
        <h2 id="data-guide-title">${esc(guide.title)}</h2>
        <p>${esc(guide.intro)}</p>
      </div>
      <button type="button" id="data-guide-close" class="data-guide-close">${esc(guide.close)}</button>
    </div>
    <div class="data-guide-body">
      ${guide.cards.map((card) => `
        <article class="data-guide-card${highlighted.has(card.category) ? ' is-highlighted' : ''}" data-guide-category="${esc(card.category)}">
          <span class="data-guide-card-tag">${esc(card.tag)}</span>
          <h3>${esc(card.heading)}</h3>
          <ul class="data-guide-list">
            ${card.items.map(([lead, body]) => `<li><strong>${esc(lead)}</strong> ${esc(body)}</li>`).join('')}
          </ul>
          <p class="data-guide-takeaway"><strong>${esc(card.takeawayLabel)}</strong> ${esc(card.takeawayBody)}</p>
        </article>`).join('')}
    </div>`;
  modal.querySelector('#data-guide-close')?.addEventListener('click', () => modal.close());
}

function renderCaseSelect(cases = state.cases) {
  const select = el('#case-select');
  if (!select) return;
  const previous = state.active?.id ?? select.value;
  const wildfires = cases.filter((c) => categoryForCase(c) === 'wildfires');
  const biomass = cases.filter((c) => categoryForCase(c) === 'deforestation');
  const permafrost = cases.filter((c) => categoryForCase(c) === 'permafrost');
  const sideCopy = copy().side;
  const groups = [
    [sideCopy.groups.wildfires, wildfires],
    [sideCopy.groups.permafrost, permafrost],
    [sideCopy.groups.deforestation, biomass],
  ].filter(([, list]) => list.length);

  select.innerHTML = groups
    .map(
      ([label, list]) =>
        `<optgroup label="${esc(label)}">${list.map((c) => `<option value="${esc(c.id)}">${esc(tCaseTitle(c))}</option>`).join('')}</optgroup>`,
    )
    .join('');
  if (previous) select.value = previous;
}

function applyStaticTranslations() {
  const c = copy();
  document.documentElement.lang = c.meta.htmlLang;
  document.title = c.meta.title;

  const logo = el('.logo');
  if (logo) logo.setAttribute('aria-label', c.meta.logoAria);
  const logoImg = el('.logo-mark');
  if (logoImg) logoImg.alt = c.meta.logoAlt;

  const nav = el('.site-header nav');
  if (nav) nav.setAttribute('aria-label', c.meta.navAria);
  document.querySelectorAll('.nav-link[data-page]').forEach((btn) => {
    const label = c.nav[btn.dataset.page];
    if (label) btn.textContent = label;
  });

  const langSwitch = el('.lang-switch');
  if (langSwitch) langSwitch.setAttribute('aria-label', c.meta.langSwitchAria);
  document.querySelectorAll('.lang-btn[data-lang]').forEach((btn) => {
    const active = btn.dataset.lang === state.lang;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', String(active));
  });

  const tokenHeading = el('#token-notice strong');
  if (tokenHeading) tokenHeading.textContent = c.token.heading;
  const tokenBody = el('#token-notice p');
  if (tokenBody) tokenBody.innerHTML = c.token.bodyHtml;

  const eyebrow = el('.text-content .eyebrow');
  if (eyebrow) eyebrow.textContent = c.hero.eyebrow;
  const heroGuideBtn = el('#hero-data-guide-btn');
  if (heroGuideBtn) heroGuideBtn.textContent = c.hero.dataGuideBtn;

  const homeCasesSec = el('#home-cases');
  if (homeCasesSec) homeCasesSec.setAttribute('aria-label', c.hero.homeCasesAria);
  const homeCasesHeading = el('#home-cases h2');
  if (homeCasesHeading) homeCasesHeading.textContent = c.hero.homeCasesTitle;
  const homeSearchInput = el('#home-search-input');
  if (homeSearchInput) {
    homeSearchInput.placeholder = c.hero.searchPlaceholder;
    homeSearchInput.setAttribute('aria-label', c.hero.searchAria);
  }

  const indicators = el('#case-indicators');
  if (indicators) indicators.setAttribute('aria-label', c.hero.indicatorsAria);
  const detailPanel = el('#case-detail-panel');
  if (detailPanel) detailPanel.setAttribute('aria-label', c.hero.detailAria);

  const mobileBar = el('#mobile-monitoring-bar');
  if (mobileBar) {
    mobileBar.setAttribute('aria-label', c.mobileTabs.aria);
    mobileBar.querySelectorAll('[data-mobile-pane]').forEach((tab) => {
      const label = c.mobileTabs[tab.dataset.mobilePane];
      if (label) tab.textContent = label;
    });
  }
  const controlsPanel = el('#monitoring-controls');
  if (controlsPanel) controlsPanel.setAttribute('aria-label', c.monitoring.controlsAria);

  const modalClose = el('#modal-close');
  if (modalClose) modalClose.textContent = c.modal.close;
  const modalDlBefore = el('#modal-dl-before');
  if (modalDlBefore) modalDlBefore.textContent = c.modal.dlBefore;
  const modalDlAfter = el('#modal-dl-after');
  if (modalDlAfter) modalDlAfter.textContent = c.modal.dlAfter;
  const modalDlComp = el('#modal-dl-comp');
  if (modalDlComp) modalDlComp.textContent = c.modal.dlComp;

  // Legacy aside labels (kept in sync even while hidden)
  const sideHeader = el('.side .hdr h1');
  if (sideHeader) sideHeader.textContent = c.side.title;
  const sidePill = el('.side .pill-real');
  if (sidePill) sidePill.textContent = c.side.pillReal;
  const caseSelectLabel = el('label[for="case-select"]');
  if (caseSelectLabel) caseSelectLabel.textContent = c.side.studySite;
  const layerHeading = el('#layer-sec .sec-title');
  if (layerHeading) layerHeading.textContent = c.side.layerAndVar;
  const opacityLabel = el('#layer-sec .range-row span');
  if (opacityLabel) opacityLabel.textContent = c.side.mapOpacity;
  const baHeading = el('#ba-sec .sec-title');
  if (baHeading) baHeading.textContent = c.side.baTitle;
  const btnDlComp = el('#btn-dl-comp');
  if (btnDlComp) btnDlComp.textContent = c.side.dlCompBtn;
  const btnOpenModal = el('#btn-open-modal');
  if (btnOpenModal) btnOpenModal.textContent = c.side.expandBtn;
  const btnViewBefore = el('#btn-view-before');
  if (btnViewBefore) btnViewBefore.textContent = c.side.viewBefore;
  const btnViewAfter = el('#btn-view-after');
  if (btnViewAfter) btnViewAfter.textContent = c.side.viewAfter;
  const btnViewSwipe = el('#btn-view-swipe');
  if (btnViewSwipe) btnViewSwipe.textContent = c.side.viewSwipe;
  const swipeLabel = el('#swipe-slider-row span');
  if (swipeLabel) swipeLabel.textContent = c.side.splitLabel;
  const vecHeading = el('#vectors-sec .sec-title');
  if (vecHeading) vecHeading.textContent = c.side.vectorsTitle;
  const dlHeading = el('#downloads-sec .sec-title');
  if (dlHeading) dlHeading.textContent = c.side.downloadsTitle;
  const worldBtn = el('#world-btn');
  if (worldBtn) worldBtn.textContent = c.side.worldBtn;
  const terrainBtn = el('#terrain-btn');
  if (terrainBtn) terrainBtn.textContent = ctl?.mode === 'flat' ? c.side.terrainOff : c.side.terrainOn;

  renderDataGuideModal();
}

function renderHomeCases(cases) {
  const c = copy();
  const grid = el('#home-case-grid');
  const orderedCases = cases
    .map((def, index) => ({
      def,
      index,
      latest: Number(def.axis?.[def.axis.length - 1]?.ms) || 0,
    }))
    .sort((a, b) => b.latest - a.latest || a.index - b.index);
  homeCasePriority = orderedCases.map(({ def }) => def.id);

  const existing = new Map(
    [...grid.querySelectorAll('.home-case-card')].map((card) => [card.dataset.caseId, card]),
  );
  if (existing.size === orderedCases.length && orderedCases.every(({ def }) => existing.has(def.id))) {
    orderedCases.forEach(({ def }) => {
      const card = existing.get(def.id);
      const title = tCaseTitle(def);
      card.setAttribute('aria-label', c.cards.openEvent(title));
      const img = card.querySelector('.home-case-image img');
      if (img) img.alt = title;
      const placeholder = card.querySelector('.home-case-placeholder');
      if (placeholder) placeholder.textContent = c.cards.imagePending;
      const typeEl = card.querySelector('.home-case-type');
      if (typeEl) typeEl.textContent = c.categories[categoryForCase(def)];
      const titleEl = card.querySelector('.home-case-copy strong');
      if (titleEl) titleEl.textContent = title;
    });
    return;
  }

  grid.innerHTML = orderedCases
    .map(({ def }) => {
      const image = getCaseCardImageUrl(def);
      const rawTitle = tCaseTitle(def);
      const title = esc(rawTitle);
      return `<button type="button" class="home-case-card" data-case-id="${esc(def.id)}" aria-label="${esc(c.cards.openEvent(rawTitle))}" hidden>
        <div class="home-case-image${image ? '' : ' is-missing'}">
          ${image ? `<img src="${esc(image)}" alt="${title}" loading="lazy" />` : ''}
          <span class="home-case-placeholder" aria-hidden="true">${esc(c.cards.imagePending)}</span>
        </div>
        <div class="home-case-copy">
          <span class="home-case-type">${esc(c.categories[categoryForCase(def)])}</span>
          <strong>${title}</strong>
        </div>
      </button>`;
    })
    .join('');

  grid.querySelectorAll('.home-case-image img').forEach((image) => {
    image.addEventListener('error', () => {
      image.hidden = true;
      image.closest('.home-case-image').classList.add('is-missing');
    });
  });
  if (visibleHomeCaseIds) updateHomeCaseVisibility([...visibleHomeCaseIds]);
  else if (!ctl) updateHomeCaseVisibility(cases.map((def) => def.id));
}

function renderCaseIndicators(cases, selectedCaseId) {
  const c = copy();
  const indicators = el('#case-indicators');
  indicators.innerHTML = cases
    .map((def) => {
      const selected = def.id === selectedCaseId;
      return `<li>
        <button type="button" class="case-indicator${selected ? ' is-selected' : ''}" data-case-id="${esc(def.id)}" aria-label="${esc(c.cards.selectEvent(tCaseTitle(def)))}" aria-pressed="${selected}">
          <span class="case-indicator-arrow" aria-hidden="true"></span>
        </button>
      </li>`;
    })
    .join('');
  indicators.hidden = cases.length === 0;
}

function renderCaseDetail(def) {
  const c = copy();
  const panel = el('#case-detail-panel');
  const rawTitle = tCaseTitle(def);
  const typeText = c.categories[categoryForCase(def)];
  const descText = tCaseDesc(def) || c.cards.descriptionFallback;
  if (panel.dataset.caseId === def.id && panel.querySelector('.case-detail-copy')) {
    const img = panel.querySelector('.case-detail-image img');
    if (img) img.alt = rawTitle;
    const placeholder = panel.querySelector('.case-detail-placeholder');
    if (placeholder) placeholder.textContent = c.cards.imageUnavailable;
    panel.querySelector('.case-detail-type').textContent = typeText;
    panel.querySelector('.case-detail-copy h2').textContent = rawTitle;
    panel.querySelector('.case-detail-copy p').textContent = descText;
    panel.querySelector('.case-monitoring-button').textContent = c.cards.monitoringBtn;
    return;
  }

  panel.dataset.caseId = def.id;
  const imageUrl = getCaseCardImageUrl(def);
  const title = esc(rawTitle);
  panel.innerHTML = `
    <div class="case-detail-image${imageUrl ? '' : ' is-missing'}">
      ${imageUrl ? `<img src="${esc(imageUrl)}" alt="${title}" loading="lazy" />` : ''}
      <span class="case-detail-placeholder">${esc(c.cards.imageUnavailable)}</span>
    </div>
    <div class="case-detail-copy">
      <span class="case-detail-type">${esc(typeText)}</span>
      <h2>${title}</h2>
      <p>${esc(descText)}</p>
      <button type="button" class="case-monitoring-button">${esc(c.cards.monitoringBtn)}</button>
    </div>`;
  const image = panel.querySelector('img');
  image?.addEventListener('error', () => {
    image.hidden = true;
    image.closest('.case-detail-image').classList.add('is-missing');
  });
}

function renderMonitoringInfo(def) {
  const c = copy();
  const mCopy = c.monitoring;
  const card = el('#monitoring-info-card');
  const metrics = def.metrics ?? {};
  const formatNumber = (value) => Number(value).toLocaleString(state.lang === 'es' ? 'es-MX' : 'en-US', { maximumFractionDigits: 2 });
  const area = (value) => value == null ? null : `${formatNumber(value)} km²`;
  const pair = (before, after, unit = '') => before == null || after == null
    ? null
    : `${formatNumber(before)}${unit} → ${formatNumber(after)}${unit}`;
  let facts;

  switch (categoryForCase(def)) {
    case 'wildfires':
      facts = [
        [mCopy.metricLabels.burnedArea, area(metrics.affectedKm2)],
        [mCopy.metricLabels.sarBurnArea, area(metrics.sarBurnKm2)],
        [mCopy.metricLabels.sarInOptical, metrics.sarBurnInOpticalPct == null ? null : `${formatNumber(metrics.sarBurnInOpticalPct)}%`],
        [mCopy.metricLabels.studyArea, area(metrics.observedKm2)],
        [mCopy.metricLabels.preWindow, metrics.preWindow],
        [mCopy.metricLabels.postWindow, metrics.postWindow],
      ];
      break;
    case 'permafrost':
      facts = [
        [mCopy.metricLabels.snowCover, pair(metrics.snowPrePct, metrics.snowPostPct, '%')],
        [mCopy.metricLabels.surfaceTemp, pair(metrics.lstPreC, metrics.lstPostC, ' °C')],
        [mCopy.metricLabels.meltArea, area(metrics.meltAreaKm2)],
        [mCopy.metricLabels.sarWetSnow, area(metrics.sarWetSnowKm2)],
        [mCopy.metricLabels.sarSubsurfaceSnow, metrics.sarWetInOpticalSnowPct == null ? null : `${formatNumber(metrics.sarWetInOpticalSnowPct)}%`],
      ];
      break;
    case 'deforestation':
      facts = [
        [mCopy.metricLabels.validForestArea, area(metrics.forestAreaKm2)],
        [mCopy.metricLabels.meanAgb, pair(metrics.agbPreMean, metrics.agbPostMean, ' Mg/ha')],
        [mCopy.metricLabels.seriesAgb, pair(metrics.agb2021Mean, metrics.agb2024Mean, ' Mg/ha')],
        [mCopy.metricLabels.nisarSevereLoss, area(metrics.biomassLossKm2)],
        [mCopy.metricLabels.planetRecall, metrics.planetDetectedLossPct == null ? null : `${formatNumber(metrics.planetDetectedLossPct)}%`],
        [mCopy.metricLabels.carbonLoss, metrics.carbonLossGg == null ? null : `${formatNumber(metrics.carbonLossGg)} Gg C`],
      ];
      break;
    default:
      facts = [];
  }

  const visibleFacts = facts.filter(([, value]) => value != null && value !== '');
  const factsMarkup = visibleFacts.length
    ? `<dl class="monitoring-info-metrics">
        ${visibleFacts.map(([label, value]) => `
          <div class="monitoring-info-metric">
            <dt>${esc(label)}</dt>
            <dd>${esc(value)}</dd>
          </div>`).join('')}
      </dl>`
    : `<p class="monitoring-info-empty">${esc(mCopy.noMetrics)}</p>`;
  const datasetChoices = (def.datasets ?? []).map((dataset) => `
    <button
      type="button"
      class="chip monitoring-satellite-option${dataset.id === state.datasetId ? ' active' : ''}"
      data-monitor-ds="${esc(dataset.id)}"
      aria-pressed="${dataset.id === state.datasetId}"
    >${esc(tTerm(dataset.name))}</button>`).join('');
  card.innerHTML = `
    <span class="monitoring-info-type">${esc(c.categories[categoryForCase(def)])}</span>
    <h2 id="monitoring-info-title">${esc(tCaseTitle(def))}</h2>
    <section class="monitoring-info-section" aria-labelledby="monitoring-facts-heading">
      <h3 id="monitoring-facts-heading">${esc(mCopy.keyMetrics)}</h3>
      ${factsMarkup}
    </section>
    <section class="monitoring-info-section monitoring-satellite-picker" aria-labelledby="monitoring-satellite-heading">
      <h3 id="monitoring-satellite-heading">${esc(mCopy.selectSatellite)}</h3>
      <div class="monitoring-satellite-options" role="group" aria-label="${esc(mCopy.sourcesAria)}">
        ${datasetChoices}
      </div>
      ${state.datasetId ? '' : `<p class="monitoring-satellite-hint">${esc(mCopy.sourceHint)}</p>`}
    </section>
    <button id="monitoring-data-guide-btn" type="button" class="data-guide-button data-guide-button--monitoring">${esc(c.hero.dataGuideBtn)}</button>`;
  card.querySelectorAll('button[data-monitor-ds]').forEach((button) => {
    button.addEventListener('click', () => selectDataset(button.dataset.monitorDs));
  });
  card.querySelector('#monitoring-data-guide-btn')?.addEventListener('click', openDataGuideModal);
}

function renderLegendMarkup(variable) {
  const legend = variable?.legend;
  if (!legend) return '';
  const localizedUnit = variable.unit ? tTerm(variable.unit) : '';
  const localizedNote = legend.nodata_label ? tTerm(legend.nodata_label) : '';
  const noteMarkup = localizedNote ? `<div class="monitoring-legend-note">${esc(localizedNote)}</div>` : '';
  if (legend.discrete) {
    const swatches = getDiscreteSwatches({ ...legend, nodata_label: localizedNote });
    if (!swatches?.length) return '';
    return `<div class="monitoring-display-legend">
      <div class="monitoring-discrete-swatches">
        ${swatches.map((s) => `<span class="monitoring-discrete-chip"><i style="background:${esc(s.cssColor)}"></i>${esc(tTerm(s.label))}</span>`).join('')}
      </div>
      ${noteMarkup}
    </div>`;
  }
  const bg = legendCssBackground(legend);
  if (bg === 'none') return '';
  return `<div class="monitoring-display-legend">
    <div class="monitoring-display-legend-ramp" style="background:${bg}"></div>
    <div><span>${esc(legend.min)}${localizedUnit ? ` ${esc(localizedUnit)}` : ''}</span><span>${esc(legend.max)}${localizedUnit ? ` ${esc(localizedUnit)}` : ''}</span></div>
    ${noteMarkup}
  </div>`;
}

function renderMonitoringControls(def) {
  const mCopy = copy().monitoring;
  const panel = el('#monitoring-controls');
  const ds = activeDataset();
  const frames = ds?.frames ?? [];
  const pair = getBeforeAfterPair(ds);
  const canSwipe = supportsBeforeAfterSwipe(ds);
  const frame = (canSwipe && state.mapMode !== 'frame')
    ? (state.mapMode === 'before' ? pair.before : pair.after) ?? pair.before ?? pair.after
    : frames.find((item) => item.id === state.frameId) ?? pair.before ?? pair.after;
  const frameIndex = frame ? Math.max(0, frames.findIndex((item) => item.id === frame.id)) : 0;
  const splitPercent = Math.round(state.split * 100);
  const variable = ds?.variables.find((item) => item.id === state.varId);
  const opacity = ds ? ctl?.dataset(ds.id)?.opacity ?? ds.render.opacity : 0.85;
  const framePhase = (canSwipe && state.mapMode !== 'frame')
    ? (state.mapMode === 'swipe'
        ? mCopy.splitPhase(splitPercent)
        : state.mapMode === 'before' ? mCopy.before : mCopy.after)
    : frame?.phase === 'before' ? mCopy.before
    : frame?.phase === 'after' ? mCopy.after
      : frame?.phase === 'change' ? mCopy.change : mCopy.observation;
  const frameDescription = (canSwipe && state.mapMode !== 'frame')
    ? (state.mapMode === 'swipe'
        ? `${tFrame(pair.before, mCopy.before)} → ${tFrame(pair.after, mCopy.after)}`
        : tFrame(frame, frame?.datetime))
    : frame ? tFrame(frame, frame.datetime) : null;
  const timeCaptionLabel = (canSwipe && state.mapMode !== 'frame')
    ? (state.mapMode === 'swipe' ? mCopy.slideToCompare : tFrame(frame, frame?.datetime || mCopy.noImageSelected))
    : (frame ? tFrame(frame, frame.datetime) : mCopy.noImageSelected);
  const variableMarkup = ds && ds.variables.length > 1
    ? `<div class="monitoring-control-group">
        <label for="monitoring-variable-select">${esc(mCopy.dataType)}</label>
        <select id="monitoring-variable-select">
          ${ds.variables.map((item) => `<option value="${esc(item.id)}" ${item.id === state.varId ? 'selected' : ''}>${esc(tTerm(item.label))}</option>`).join('')}
        </select>
      </div>`
    : '';
  const compareModeMarkup = canSwipe
    ? `<div class="monitoring-compare-modes" role="group" aria-label="${esc(mCopy.compareTitle)}">
        <button type="button" class="chip monitoring-compare-btn${state.mapMode === 'before' ? ' active' : ''}" data-compare-mode="before">${esc(mCopy.before)}</button>
        <button type="button" class="chip monitoring-compare-btn${state.mapMode === 'swipe' ? ' active' : ''}" data-compare-mode="swipe">${esc(mCopy.swipe)}</button>
        <button type="button" class="chip monitoring-compare-btn${state.mapMode === 'after' ? ' active' : ''}" data-compare-mode="after">${esc(mCopy.after)}</button>
      </div>`
    : '';
  const seriesFramesMarkup = frames.length > 2
    ? `<div class="monitoring-series-frames" role="group" aria-label="${esc(mCopy.seriesTitle || mCopy.imageTime)}">
        ${frames.map((item) => {
          const isCurrent = state.mapMode !== 'swipe' && frame?.id === item.id;
          const shortYear = String(item.datetime || item.label).slice(0, 10);
          return `<button type="button" class="chip monitoring-frame-btn${isCurrent ? ' active' : ''}" data-series-frame="${esc(item.id)}">${esc(shortYear)}</button>`;
        }).join('')}
      </div>`
    : '';
  const localizedUnit = variable?.unit ? tTerm(variable.unit) : '';
  const details = ds ? [
    [mCopy.source, tTerm(ds.name)],
    [ds.sensor ? mCopy.sensor : mCopy.platform, ds.sensor || tTerm(ds.platform)],
    [mCopy.provider, ds.provider],
    [mCopy.data, tTerm(variable?.label)],
    [canSwipe && state.mapMode === 'swipe' ? mCopy.comparison : mCopy.date, frameDescription],
    [mCopy.phase, frame ? framePhase : null],
    [mCopy.unit, localizedUnit],
  ].filter(([, value]) => value) : [];
  const detailsMarkup = details.length
    ? `<dl class="monitoring-display-details">
        ${details.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}
      </dl>`
    : `<p class="monitoring-control-hint">${esc(mCopy.selectSatelliteHint)}</p>`;
  const legendMarkup = renderLegendMarkup(variable);
  const userVectors = (def.vectors ?? []).filter((v) => v.id !== 'burn-extent-casing');
  const vectorsMarkup = userVectors.length
    ? `<div class="monitoring-control-group">
        <span class="monitoring-group-label">${esc(mCopy.vectorsTitle)}</span>
        <div class="monitoring-vector-options">
          ${userVectors.map((v) => {
            const checked = ctl?.current?.id === def.id ? ctl.isVectorVisible(v.id) : v.defaultVisible !== false;
            return `<label class="monitoring-vector-option">
              <input type="checkbox" data-monitoring-vec="${esc(v.id)}" ${checked ? 'checked' : ''} />
              <span>${esc(tTerm(v.name) || v.id)}</span>
            </label>`;
          }).join('')}
        </div>
      </div>`
    : '';
  const geotiffs = (def.downloads ?? []).filter((item) => item.datasetId === ds?.id && item.format === 'geotiff');
  const geotiffCount = geotiffs.length;
  const geotiffsMarkup = geotiffCount
    ? `<details class="monitoring-geotiffs">
        <summary>${esc(mCopy.geotiffsBtn(geotiffCount))}</summary>
        <div class="monitoring-geotiff-list">
          ${geotiffs.map((item) => `<a class="monitoring-geotiff-link" href="${esc(resolveUrl(item.url))}" download><span>⬇ ${esc(tTerm(item.label))}</span>${fmtBytes(item.sizeBytes ?? item.bytes) ? `<span class="muted">${esc(fmtBytes(item.sizeBytes ?? item.bytes))}</span>` : ''}</a>`).join('')}
        </div>
      </details>`
    : '';

  panel.innerHTML = `
    <header class="monitoring-controls-header">
      <span>${esc(mCopy.visualization)}</span>
      <h2>${esc(mCopy.imageControls)}</h2>
    </header>
    <div class="monitoring-control-group">
      <label for="monitoring-opacity-range">${esc(mCopy.opacity)} <output id="monitoring-opacity-output">${Math.round(opacity * 100)}%</output></label>
      <input id="monitoring-opacity-range" type="range" min="0" max="1" step="0.05" value="${opacity}" ${ds ? '' : 'disabled'} />
    </div>
    <div class="monitoring-control-group">
      <label for="monitoring-time-range">${esc(canSwipe ? mCopy.compareTitle : mCopy.imageTime)}</label>
      ${compareModeMarkup}
      ${seriesFramesMarkup}
      <input id="monitoring-time-range" type="range" min="0" max="${canSwipe ? 100 : Math.max(0, frames.length - 1)}" step="1" value="${canSwipe ? splitPercent : frameIndex}" ${canSwipe || (ds && frames.length > 1) ? '' : 'disabled'} />
      <div class="monitoring-time-ends"><span>${esc(canSwipe ? mCopy.before : mCopy.initial)}</span><span>${esc(canSwipe ? mCopy.after : mCopy.final)}</span></div>
      <div class="monitoring-time-caption">
        <span id="monitoring-time-phase">${esc(framePhase)}</span>
        <strong id="monitoring-time-label">${esc(timeCaptionLabel)}</strong>
      </div>
    </div>
    ${variableMarkup}
    ${vectorsMarkup}
    <section class="monitoring-display-info" aria-labelledby="monitoring-display-heading">
      <h3 id="monitoring-display-heading">${esc(mCopy.currentlyDisplaying)}</h3>
      ${detailsMarkup}
      ${legendMarkup}
    </section>
    <div class="monitoring-action-buttons">
      <button id="monitoring-download-button" class="monitoring-download-button" type="button" ${ds ? '' : 'disabled'}>
        ${esc(canSwipe ? mCopy.downloadPngBtn : mCopy.downloadSinglePngBtn)}
      </button>
      <button id="monitoring-expand-button" class="monitoring-secondary-button" type="button" ${ds ? '' : 'disabled'}>
        ${esc(mCopy.expandPreviewBtn)}
      </button>
    </div>
    ${geotiffsMarkup}
    <p class="monitoring-download-hint">${esc(ds ? (geotiffCount ? mCopy.downloadHintCount(geotiffCount) : '') : mCopy.downloadHintNoSource)}</p>`;

  const opacityRange = el('#monitoring-opacity-range');
  opacityRange.addEventListener('input', (event) => {
    const value = Number(event.target.value);
    el('#monitoring-opacity-output').textContent = `${Math.round(value * 100)}%`;
    el('#opacity-range').value = String(value);
    el('#opacity-out').textContent = `${Math.round(value * 100)}%`;
    if (ds) ctl?.setDatasetOpacity(ds.id, value);
  });

  panel.querySelectorAll('button[data-compare-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.compareMode;
      state.mapMode = mode;
      if (mode === 'before') state.frameId = pair.before?.id ?? null;
      else if (mode === 'after') state.frameId = pair.after?.id ?? null;
      syncMapToLayer();
      renderMonitoringControls(def);
    });
  });

  panel.querySelectorAll('button[data-series-frame]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const fid = btn.dataset.seriesFrame;
      state.frameId = fid;
      if (fid === pair.before?.id) state.mapMode = 'before';
      else if (fid === pair.after?.id) state.mapMode = 'after';
      else state.mapMode = 'frame';
      syncMapToLayer();
      renderMonitoringControls(def);
    });
  });

  const timeRange = el('#monitoring-time-range');
  timeRange.addEventListener('input', () => {
    const currentCopy = copy().monitoring;
    if (canSwipe) {
      const wasNotSwipe = state.mapMode !== 'swipe';
      state.split = Number(timeRange.value) / 100;
      state.mapMode = 'swipe';
      const percent = Math.round(state.split * 100);
      el('#monitoring-time-phase').textContent = currentCopy.splitPhase(percent);
      el('#monitoring-time-label').textContent = currentCopy.slideToCompare;
      panel.querySelectorAll('button[data-compare-mode]').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.compareMode === 'swipe');
      });
      if (wasNotSwipe) syncMapToLayer();
      else ctl?.setSwipeSplit(state.split);
      return;
    }
    const selectedFrame = frames[Number(timeRange.value)];
    if (!selectedFrame) return;
    state.frameId = selectedFrame.id;
    el('#monitoring-time-label').textContent = tFrame(selectedFrame, selectedFrame.datetime);
  });
  timeRange.addEventListener('change', () => {
    if (canSwipe) {
      ctl?.setSwipeSplit(state.split);
      return;
    }
    syncMapToLayer();
    renderMonitoringControls(def);
  });

  panel.querySelectorAll('input[data-monitoring-vec]').forEach((inp) => {
    inp.addEventListener('change', () => {
      const vecId = inp.dataset.monitoringVec;
      ctl?.setVectorVisible(vecId, inp.checked);
      if (vecId === 'burn-extent-outline') ctl?.setVectorVisible('burn-extent-casing', inp.checked);
      const legacy = el(`#vectors-list input[data-vec="${CSS.escape(vecId)}"]`);
      if (legacy) legacy.checked = inp.checked;
    });
  });

  el('#monitoring-variable-select')?.addEventListener('change', (event) => {
    state.varId = event.target.value;
    renderDatasetControls(def);
    renderMonitoringControls(def);
    refreshLayer();
  });
  el('#monitoring-download-button').addEventListener('click', downloadMonitoringData);
  el('#monitoring-expand-button').addEventListener('click', openComparisonModal);
}

function schedulePageTransition(id, delay, callback) {
  const timer = window.setTimeout(() => {
    if (id === pageTransitionId) callback();
  }, delay);
  pageTransitionTimers.push(timer);
}

function beginPageTransition(content) {
  pageTransitionTimers.forEach((timer) => window.clearTimeout(timer));
  pageTransitionTimers = [];
  const id = ++pageTransitionId;
  const text = el('.text-content');
  text.classList.remove('is-entering', 'is-leaving');
  text.classList.add('is-leaving');
  schedulePageTransition(id, 180, () => {
    el('#hero-title').textContent = content.title;
    el('#hero-description').textContent = content.description;
    text.classList.remove('is-leaving');
    text.classList.add('is-entering');
    requestAnimationFrame(() => {
      if (id === pageTransitionId) text.classList.remove('is-entering');
    });
  });
  return id;
}

function selectCaseIndicator(caseId) {
  el('#case-indicators').querySelectorAll('.case-indicator').forEach((indicator) => {
    const selected = indicator.dataset.caseId === caseId;
    indicator.classList.toggle('is-selected', selected);
    indicator.setAttribute('aria-pressed', String(selected));
  });
}

function syncMonitoringModeToggle() {
  const mCopy = copy().monitoring;
  const toggle = el('#monitoring-mode-toggle');
  const is3d = ctl?.mode === 'terrain';
  toggle.textContent = is3d ? '3D' : '2D';
  toggle.setAttribute('aria-pressed', String(is3d));
  toggle.setAttribute('aria-label', is3d ? mCopy.mode2dAria : mCopy.mode3dAria);
}

function setMobileMonitoringPane(pane = 'info') {
  const validPane = pane === 'controls' || pane === 'map' ? pane : 'info';
  el('.view').dataset.mobilePane = validPane;
  el('#mobile-monitoring-bar')?.querySelectorAll('[data-mobile-pane]').forEach((tab) => {
    const active = tab.dataset.mobilePane === validPane;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
  });
}

function updateHomeCaseVisibility(ids) {
  visibleHomeCaseIds = new Set(ids);
  const shouldShow = homeCasesReady && state.currentPage === 'home';
  const prioritizedVisibleIds = new Set(
    shouldShow ? homeCasePriority.filter((id) => visibleHomeCaseIds.has(id)).slice(0, 4) : [],
  );
  homeCaseDisplayOrder = homeCaseDisplayOrder.filter((id) => prioritizedVisibleIds.has(id));
  homeCasePriority.forEach((id) => {
    if (prioritizedVisibleIds.has(id) && !homeCaseDisplayOrder.includes(id)) {
      homeCaseDisplayOrder.push(id);
    }
  });

  const grid = el('#home-case-grid');
  const cards = new Map(
    [...grid.querySelectorAll('.home-case-card')].map((card) => [card.dataset.caseId, card]),
  );
  homeCaseDisplayOrder.forEach((id) => {
    const card = cards.get(id);
    if (card) grid.appendChild(card);
  });
  cards.forEach((card) => {
    setHomeCaseCardVisible(card, prioritizedVisibleIds.has(card.dataset.caseId));
  });
  el('#home-cases').hidden = !shouldShow;
}

function setHomeCaseCardVisible(card, visible) {
  const grid = card.parentElement;
  if (visible) {
    const timer = homeCaseHideTimers.get(card);
    if (timer) {
      clearTimeout(timer);
      homeCaseHideTimers.delete(card);
    }
    const wasHidden = card.hidden;
    const wasHiding = card.classList.contains('is-hiding');
    card.hidden = false;
    if (wasHiding) resetHomeCaseCardPosition(card);
    card.classList.remove('is-hiding');
    if (wasHidden) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!card.hidden && !card.classList.contains('is-hiding')) card.classList.add('is-visible');
      }));
    } else {
      card.classList.add('is-visible');
    }
    return;
  }

  if (card.hidden || card.classList.contains('is-hiding')) return;
  const cardRect = card.getBoundingClientRect();
  const gridRect = grid.getBoundingClientRect();
  card.style.position = 'absolute';
  card.style.top = `${cardRect.top - gridRect.top}px`;
  card.style.left = `${cardRect.left - gridRect.left}px`;
  card.style.width = `${cardRect.width}px`;
  card.style.height = `${cardRect.height}px`;
  card.style.zIndex = '1';
  card.classList.remove('is-visible');
  card.classList.add('is-hiding');
  homeCaseHideTimers.set(card, window.setTimeout(() => {
    if (!card.classList.contains('is-visible')) {
      card.hidden = true;
      card.classList.remove('is-hiding');
      resetHomeCaseCardPosition(card);
    }
    homeCaseHideTimers.delete(card);
  }, 350));
}

function resetHomeCaseCardPosition(card) {
  card.style.removeProperty('position');
  card.style.removeProperty('top');
  card.style.removeProperty('left');
  card.style.removeProperty('width');
  card.style.removeProperty('height');
  card.style.removeProperty('z-index');
}

function setPage(page, { caseId } = {}) {
  const content = copy().pages[page];
  if (!content) return;
  const transitionId = beginPageTransition(content);
  state.currentPage = page;
  state.hoverSeq++;
  setStatus('');
  el('#inspect-pill').hidden = true;
  if (el('#home-search-results')) el('#home-search-results').hidden = true;
  ctl?.closePopup();
  ctl?.cancelCaseFocus();
  el('.view').classList.remove('is-monitoring');
  delete el('.view').dataset.mobilePane;
  el('#monitoring-mode-toggle').hidden = true;
  el('#monitoring-info-card').hidden = true;
  el('#monitoring-controls').hidden = true;
  el('#mobile-monitoring-bar').hidden = true;
  ctl?.setOrbitEnabled(page === 'home');
  ctl?.setScrollZoomEnabled(page !== 'home');
  ctl?.setMapInteractionEnabled(false);
  el('.view').classList.toggle('has-case-indicators', page !== 'home');
  el('#case-indicators').hidden = page === 'home';
  el('#case-detail-panel').hidden = page === 'home';
  el('#case-detail-panel').classList.remove('is-revealed');
  if (page !== 'home') el('#home-cases').hidden = true;
  document.querySelectorAll('.nav-link, .logo[data-page]').forEach((link) => {
    link.classList.toggle('active', link.dataset.page === page);
  });

  if (page === 'home') {
    state.caseSeq++;
    state.active = null;
    homeCasesReady = !ctl;
    const homeCaseIds = ctl ? [] : state.cases.map((def) => def.id);
    updateHomeCaseVisibility(homeCaseIds);
    ctl?.setCases(state.cases);
    ctl?.showWorld();
    return;
  }
  const categoryCases = state.cases.filter((def) => categoryForCase(def) === page);
  const matchingCase = categoryCases.find((def) => def.id === caseId) ?? categoryCases[0];
  renderCaseIndicators(categoryCases, matchingCase?.id);
  if (matchingCase) selectCase(matchingCase.id, { globeOverview: true });
  else ctl?.setCases([]);
  schedulePageTransition(transitionId, 430, () => {
    el('#case-indicators .is-selected')?.classList.add('is-revealed');
  });
  schedulePageTransition(transitionId, 770, () => {
    el('#case-detail-panel').classList.add('is-revealed');
  });
  schedulePageTransition(transitionId, 1110, () => {
    el('#case-indicators').querySelectorAll('.case-indicator:not(.is-selected)').forEach((indicator) => {
      indicator.classList.add('is-revealed');
    });
  });
}

function openCase(caseId) {
  const def = state.cases.find((item) => item.id === caseId);
  if (!def) return;
  setPage(categoryForCase(def), { caseId: def.id });
}

let statusTimer = 0;

function setStatus(msg, { level = 'info', autoHideMs = 0 } = {}) {
  if (statusTimer) {
    window.clearTimeout(statusTimer);
    statusTimer = 0;
  }
  const pill = el('#status-pill');
  pill.hidden = !msg;
  pill.textContent = msg || '';
  pill.style.color = level === 'error' ? '#fca5a5' : '';
  if (msg && autoHideMs > 0) {
    statusTimer = window.setTimeout(() => {
      statusTimer = 0;
      setStatus('');
    }, autoHideMs);
  }
}

function renderCaseMetrics(def) {
  const m = def.metrics;
  const box = el('#case-metrics');
  if (!m) { box.innerHTML = ''; return; }
  const labels = copy().monitoring.metricLabels;
  const items = [];
  if (m.preWindow) items.push([labels.preWindow, m.preWindow]);
  if (m.postWindow) items.push([labels.postWindow, m.postWindow]);
  if (m.affectedKm2 != null) items.push([labels.burnedArea, `${m.affectedKm2} km²`]);
  if (m.observedKm2 != null) items.push([labels.studyArea, `${m.observedKm2} km²`]);
  if (m.snowPrePct != null && m.snowPostPct != null) items.push([labels.snowCover, `${m.snowPrePct}% → ${m.snowPostPct}%`]);
  if (m.lstPreC != null && m.lstPostC != null) items.push([labels.surfaceTemp, `${m.lstPreC}°C → ${m.lstPostC}°C`]);
  if (m.meltAreaKm2 != null) items.push([labels.meltArea, `${m.meltAreaKm2} km²`]);
  if (m.forestAreaKm2 != null) items.push([labels.validForestArea, `${m.forestAreaKm2} km²`]);
  if (m.agbPreMean != null && m.agbPostMean != null) items.push([labels.meanAgb, `${m.agbPreMean} → ${m.agbPostMean} Mg/ha`]);
  if (m.agbDisturbedPre != null && m.agbDisturbedPost != null) items.push([labels.meanAgb, `${m.agbDisturbedPre} → ${m.agbDisturbedPost} Mg/ha`]);
  if (m.biomassLossKm2 != null) items.push([labels.nisarSevereLoss, `${m.biomassLossKm2} km²`]);
  if (m.planetDistKm2 != null) items.push(['Planet Ref.', `${m.planetDistKm2} km²`]);
  if (m.carbonLossGg != null) items.push([labels.carbonLoss, `${m.carbonLossGg} Gg C`]);
  if (m.hvPreDb != null && m.hvPostDb != null) items.push(['L-Band HV', `${m.hvPreDb} → ${m.hvPostDb} dB`]);
  box.innerHTML = items.map(([k, v]) => `<div class="metric"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
}

function renderLegend(ds, varId) {
  const wrap = el('#legend-wrap');
  const v = (ds?.variables ?? []).find((x) => x.id === varId);
  const bg = legendCssBackground(v?.legend);
  if (bg === 'none') { wrap.hidden = true; return; }
  wrap.hidden = false;
  el('#legend-ramp').style.background = bg;
  const unit = v.unit ? ` ${tTerm(v.unit)}` : '';
  el('#legend-min').textContent = `${v.legend.min}${unit}`;
  el('#legend-max').textContent = `${v.legend.max}${unit}`;
}

function renderDatasetControls(def) {
  el('#dataset-chips').innerHTML = def.datasets
    .map((d) => `<button type="button" class="chip${d.id === state.datasetId ? ' active' : ''}" data-ds="${esc(d.id)}">${esc(tTerm(d.name))}</button>`)
    .join('');

  el('#dataset-chips').querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => selectDataset(btn.dataset.ds));
  });

  const ds = activeDataset();
  const varBox = el('#var-chips');
  if (ds && ds.variables.length > 1) {
    varBox.hidden = false;
    varBox.innerHTML = ds.variables
      .map((v) => `<button type="button" class="chip${v.id === state.varId ? ' active' : ''}" data-v="${esc(v.id)}">${esc(tTerm(v.label))}</button>`)
      .join('');
    varBox.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.varId = btn.dataset.v;
        renderDatasetControls(def);
        renderMonitoringControls(def);
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
        <span>${esc(tTerm(v.name) || v.id)}</span>
      </label>`,
    )
    .join('');
  box.querySelectorAll('input').forEach((inp) => {
    inp.addEventListener('change', () => ctl?.setVectorVisible(inp.dataset.vec, inp.checked));
  });
}

function renderDownloads(def) {
  const sideCopy = copy().side;
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
      <span>⬇ ${esc(tTerm(dl.label))}</span>
      <span class="dl-size">${esc(fmtBytes(dl.sizeBytes))}</span>
    </a>`;

  let html = shown.map(linkHtml).join('');
  if (primary.length && others.length) {
    html += `<details class="dl-more"><summary>${esc(sideCopy.moreGeotiffs(others.length))}</summary><div class="dl-list">${others.map(linkHtml).join('')}</div></details>`;
  }
  box.innerHTML = html;
}

async function renderBeforeAfterPreviews(def, ds, varId) {
  const c = copy();
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
    if (seq !== state.renderSeq || !state.active) return;
    state.previews = {
      before: { frame: before, url: rb.url },
      after: { frame: after, url: ra.url },
    };
    cards.push({ tag: c.export.beforeTag, cls: 'ba-tag-before', date: tFrame(before, c.monitoring.before), url: rb.url, phase: 'before', frame: before });
    cards.push({ tag: c.export.afterTag, cls: 'ba-tag-after', date: tFrame(after, c.monitoring.after), url: ra.url, phase: 'after', frame: after });
  } else {
    grid.className = 'ba-grid single';
    const only = after || before;
    if (!only) { grid.innerHTML = ''; return; }
    const rendered = await renderFrameCanvas(ds, only, varId);
    if (seq !== state.renderSeq || !state.active) return;
    state.previews = { before: null, after: { frame: only, url: rendered.url } };
    cards.push({ tag: c.side.realObsTag, cls: 'ba-tag-after', date: tFrame(only, c.side.resultFallback), url: rendered.url, phase: 'real', frame: only });
  }

  grid.innerHTML = cards
    .map(
      (item, i) => `<div class="ba-card">
        <div class="ba-card-head"><span class="${item.cls}">${esc(item.tag)}</span></div>
        <div class="ba-date" title="${esc(item.date)}">${esc(item.date)}</div>
        <img class="ba-thumb" src="${item.url}" alt="${esc(item.tag)}" />
        <button type="button" class="btn btn-sm" data-dl-idx="${i}">${esc(c.side.downloadTagBtn(item.tag))}</button>
      </div>`,
    )
    .join('');

  grid.querySelectorAll('button[data-dl-idx]').forEach((btn) => {
    const item = cards[Number(btn.dataset.dlIdx)];
    btn.addEventListener('click', () => downloadSingleRenderedPng(def.id, ds, item.frame, varId, item.phase));
  });
}

async function downloadMonitoringData() {
  const def = state.active;
  const ds = activeDataset();
  const variableId = state.varId ?? ds?.variables[0]?.id;
  if (!def || !ds || !variableId) return;
  const exportCopy = copy().export;
  const { hasPair, before, after } = getBeforeAfterPair(ds);
  const singleFrame = ds.frames?.find((f) => f.id === state.frameId) ?? ds.frames?.[0];

  try {
    if (hasPair && before && after) {
      await downloadSideBySidePng(def, ds, variableId, state.lang);
    } else if (singleFrame) {
      await downloadSingleRenderedPng(def.id, ds, singleFrame, variableId, singleFrame.phase ?? 'frame');
    } else {
      throw new Error('No raster frame available');
    }
  } catch (error) {
    setStatus(exportCopy.failedPng(error.message));
    return;
  }

  setStatus(exportCopy.downloadOnlyPng, { autoHideMs: 4500 });
}

async function syncMapToLayer() {
  if (!ctl || !state.active) return;
  const ds = activeDataset();
  if (!ds) return;
  const { hasPair, before, after } = getBeforeAfterPair(ds);
  const monitoringOpacity = el('#monitoring-opacity-range');
  const opacity = monitoringOpacity && !monitoringOpacity.disabled
    ? Number(monitoringOpacity.value)
    : Number(el('#opacity-range').value);
  const isSwipe = state.mapMode === 'swipe' && hasPair && before && after;

  el('#swipe-slider-row').hidden = !isSwipe;
  el('#btn-view-before').classList.toggle('active', state.mapMode === 'before');
  el('#btn-view-after').classList.toggle('active', state.mapMode === 'after');
  el('#btn-view-swipe').classList.toggle('active', state.mapMode === 'swipe');

  const targetFrame = isSwipe
    ? after
    : ds.frames.find((item) => item.id === state.frameId)
      || (state.mapMode === 'after' || !hasPair ? after || before : before);

  const split = state.split;
  await ctl.applyLayerState({
    datasetId: ds.id,
    selection: state.varId,
    opacity,
    timeMs: targetFrame?.ms,
    swipe: isSwipe
      ? {
          datasetId: ds.id,
          beforeId: before.id,
          afterId: after.id,
          selection: state.varId,
          split,
        }
      : null,
  });
  if (isSwipe && state.split !== split && activeDataset()?.id === ds.id && state.mapMode === 'swipe') {
    ctl.setSwipeSplit(state.split);
  }
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
  const pair = getBeforeAfterPair(ds);
  state.frameId = (pair.before ?? pair.after)?.id ?? null;
  state.mapMode = supportsBeforeAfterSwipe(ds) ? 'swipe' : 'before';
  renderDatasetControls(state.active);
  renderMonitoringInfo(state.active);
  renderMonitoringControls(state.active);
  refreshLayer();
}

async function selectCase(caseId, { globeOverview = false } = {}) {
  const def = state.cases.find((c) => c.id === caseId);
  if (!def) return;
  const cSeq = ++state.caseSeq;
  state.hoverSeq++;
  el('#inspect-pill').hidden = true;
  ctl?.closePopup();
  if (globeOverview) {
    ctl?.cancelCaseFocus();
    el('.view').classList.remove('is-monitoring');
    delete el('.view').dataset.mobilePane;
    el('#monitoring-mode-toggle').hidden = true;
    el('#monitoring-info-card').hidden = true;
    el('#monitoring-controls').hidden = true;
    el('#mobile-monitoring-bar').hidden = true;
  }
  state.active = def;
  const preferred = def.datasets.find((d) => getBeforeAfterPair(d).hasPair) ?? def.datasets[0];
  state.datasetId = null;
  state.varId = null;
  state.frameId = null;
  state.mapMode = 'before';
  if (state.currentPage !== 'home') renderCaseDetail(def);
  renderMonitoringInfo(def);
  renderMonitoringControls(def);
  if (state.currentPage !== 'home') {
    selectCaseIndicator(def.id);
    ctl?.setCases([def]);
  }
  el('#case-select').value = def.id;
  el('#case-desc').textContent = tCaseDesc(def);
  el('#world-btn').hidden = !ctl;
  el('#terrain-btn').hidden = !ctl;
  el('#terrain-btn').textContent = copy().side.terrainOn;
  history.replaceState(null, '', `#${def.id}`);
  renderCaseMetrics(def);

  renderDatasetControls(def);
  renderVectors(def);
  renderDownloads(def);

  const previewsJob = preferred ? renderBeforeAfterPreviews(def, preferred, preferred.variables[0]?.id) : Promise.resolve();
  const mapJob = ctl ? ctl.loadCase(def, { globeOverview, hideDatasets: true }).then(() => {
    if (cSeq === state.caseSeq) {
      renderMonitoringControls(def);
      return syncMapToLayer();
    }
  }) : Promise.resolve();
  await Promise.all([previewsJob, mapJob]);
}

function openComparisonModal() {
  const c = copy();
  const def = state.active;
  const ds = activeDataset();
  if (!def || !ds) return;
  const { hasPair, before, after } = getBeforeAfterPair(ds);
  const v = ds.variables.find((x) => x.id === state.varId);
  el('#modal-title').textContent = `${tCaseTitle(def)} · ${tTerm(ds.name)}${v ? ` (${tTerm(v.label)})` : ''}`;

  const modalGrid = el('#modal-grid');
  const cards = [];
  if (hasPair && state.previews.before && state.previews.after) {
    modalGrid.className = 'modal-grid';
    cards.push({ title: `${c.modal.beforePrefix}${tFrame(before, c.export.preFallback)}`, url: state.previews.before.url });
    cards.push({ title: `${c.modal.afterPrefix}${tFrame(after, c.export.postFallback)}`, url: state.previews.after.url });
    el('#modal-dl-before').hidden = false;
    el('#modal-dl-comp').hidden = false;
  } else if (state.previews.after) {
    modalGrid.className = 'modal-grid single';
    cards.push({ title: tFrame(after, c.modal.realObs), url: state.previews.after.url });
    el('#modal-dl-before').hidden = true;
    el('#modal-dl-comp').hidden = true;
  }
  modalGrid.innerHTML = cards
    .map((item) => `<div class="modal-card"><div class="ba-card-head"><span>${esc(item.title)}</span></div><img src="${item.url}" alt="${esc(item.title)}" /></div>`)
    .join('');

  el('#compare-modal').showModal();
}

function openDataGuideModal() {
  const modal = el('#data-guide-modal');
  const activeCategory = state.active
    ? categoryForCase(state.active)
    : state.currentPage !== 'home'
      ? state.currentPage
      : null;

  modal.querySelectorAll('.data-guide-card').forEach((card) => {
    card.classList.toggle('is-highlighted', card.dataset.guideCategory === activeCategory);
  });

  if (!modal.open) modal.showModal();
  const body = modal.querySelector('.data-guide-body');
  if (body) body.scrollTop = 0;
}

function setLanguage(nextLang) {
  const normalized = nextLang === 'es' ? 'es' : DEFAULT_LANG;
  if (normalized === state.lang) return;
  state.lang = normalized;
  applyStaticTranslations();

  const c = copy();
  const pageCopy = c.pages[state.currentPage];
  if (pageCopy) {
    el('#hero-title').textContent = pageCopy.title;
    el('#hero-description').textContent = pageCopy.description;
  }

  ctl?.setLanguage(state.lang);

  if (state.cases.length) {
    renderCaseSelect(state.cases);
    renderHomeCases(state.cases);
  } else {
    el('#case-desc').textContent = c.modal.noDataCases;
  }

  if (state.active) {
    const def = state.active;
    if (state.currentPage !== 'home') {
      const categoryCases = state.cases.filter((item) => categoryForCase(item) === state.currentPage);
      el('#case-indicators')?.querySelectorAll('button[data-case-id]').forEach((btn) => {
        const match = categoryCases.find((item) => item.id === btn.dataset.caseId);
        if (match) btn.setAttribute('aria-label', c.cards.selectEvent(tCaseTitle(match)));
      });
      renderCaseDetail(def);
    }
    el('#case-desc').textContent = tCaseDesc(def);
    renderCaseMetrics(def);
    renderDatasetControls(def);
    renderVectors(def);
    renderDownloads(def);
    renderMonitoringInfo(def);
    renderMonitoringControls(def);
    syncMonitoringModeToggle();
    const previewDataset = activeDataset()
      ?? (def.datasets.find((d) => getBeforeAfterPair(d).hasPair) ?? def.datasets[0]);
    const previewVar = activeDataset() ? state.varId : previewDataset?.variables[0]?.id;
    if (previewDataset) renderBeforeAfterPreviews(def, previewDataset, previewVar);
    if (el('#compare-modal')?.open) openComparisonModal();
    if (!el('#inspect-pill').hidden && state.lastInspectLngLat && refreshInspectPoint) {
      refreshInspectPoint(state.lastInspectLngLat);
    }
  }
}

function bindEvents() {
  document.querySelectorAll('[data-page]').forEach((link) => {
    link.addEventListener('click', () => setPage(link.dataset.page));
  });
  document.querySelectorAll('.lang-btn[data-lang]').forEach((btn) => {
    btn.addEventListener('click', () => setLanguage(btn.dataset.lang));
  });
  el('#home-case-grid').addEventListener('click', (event) => {
    const card = event.target.closest('button[data-case-id]');
    if (card) openCase(card.dataset.caseId);
  });

  const searchInput = el('#home-search-input');
  const searchResults = el('#home-search-results');
  let searchAbort = null;
  let searchTimer = 0;

  const renderSearchResults = (items, query) => {
    if (!searchResults) return;
    const heroCopy = copy().hero;
    if (!query.trim()) {
      searchResults.hidden = true;
      searchResults.innerHTML = '';
      return;
    }
    searchResults.hidden = false;
    if (!items.length) {
      searchResults.innerHTML = `<div class="home-search-empty">${esc(heroCopy.searchNoResults)}</div>`;
      return;
    }
    searchResults.innerHTML = items.slice(0, 6).map((item, idx) => `
      <button type="button" class="home-search-item" data-search-idx="${idx}">
        <strong>${esc(item.title)}</strong>
        <span>${esc(item.subtitle || '')}</span>
      </button>
    `).join('');
    searchResults.querySelectorAll('button[data-search-idx]').forEach((btn) => {
      const hit = items[Number(btn.dataset.searchIdx)];
      btn.addEventListener('click', () => {
        searchResults.hidden = true;
        if (!hit) return;
        if (hit.kind === 'case') {
          openCase(hit.id);
          return;
        }
        if (hit.center && ctl?.map) {
          ctl.map.flyTo({ center: hit.center, zoom: hit.bbox ? 8 : 9, duration: 1300, essential: true });
        }
        const cmds = buildCliCommands(hit);
        const cliCmd = hit.category === 'glacier' ? cmds.glacierCmd : cmds.wildfireCmd;
        if (cliCmd && navigator.clipboard?.writeText) {
          navigator.clipboard.writeText(cliCmd).catch(() => {});
        }
        const nearest = hit.center ? findNearestCase(hit.center, state.cases) : null;
        const cmdNote = cliCmd ? ` · CLI: ${cliCmd}` : '';
        if (nearest) {
          setStatus(
            `${heroCopy.searchNearest(tCaseTitle(nearest.item), Math.round(nearest.distanceKm))}${cmdNote}`,
            { autoHideMs: 8500 },
          );
        } else if (cliCmd) {
          setStatus(`${heroCopy.searchPresetNote}${cmdNote}`, { autoHideMs: 8500 });
        }
      });
    });
  };

  searchInput?.addEventListener('input', () => {
    const q = searchInput.value;
    if (searchTimer) window.clearTimeout(searchTimer);
    if (searchAbort) { searchAbort.abort(); searchAbort = null; }
    if (!q.trim()) { renderSearchResults([], ''); return; }

    const coordHit = parseCoordinates(q);
    const localizedCases = state.cases.map((c) => ({
      ...c,
      title: tCaseTitle(c) || c.title,
      subtitle: `${c.title ?? ''} ${c.subtitle ?? ''}`.trim(),
    }));
    const localHits = searchLocalMatches(q, localizedCases);
    const immediate = coordHit ? [coordHit, ...localHits] : localHits;
    if (immediate.length) {
      renderSearchResults(immediate, q);
      return;
    }
    renderSearchResults([], q);
    if (q.trim().length >= 3) {
      searchTimer = window.setTimeout(async () => {
        searchAbort = new AbortController();
        try {
          const remoteHits = await geocodePlaces(q, { token, signal: searchAbort.signal });
          if (searchInput.value === q) renderSearchResults(remoteHits, q);
        } catch (err) {
          if (err?.name !== 'AbortError') console.warn('Geocoding failed:', err);
        }
      }, 280);
    }
  });
  searchInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      searchResults.hidden = true;
      searchInput.blur();
    } else if (event.key === 'Enter') {
      const firstBtn = searchResults?.querySelector('button[data-search-idx]');
      if (firstBtn) {
        event.preventDefault();
        firstBtn.click();
      }
    }
  });
  document.addEventListener('click', (event) => {
    if (searchResults && !event.target.closest('.home-search-wrap')) searchResults.hidden = true;
  });
  el('#case-indicators').addEventListener('click', (event) => {
    const indicator = event.target.closest('button[data-case-id]');
    if (indicator) selectCase(indicator.dataset.caseId, { globeOverview: true });
  });
  el('#case-detail-panel').addEventListener('click', (event) => {
    const monitoringButton = event.target.closest('.case-monitoring-button');
    const view = el('.view');
    if (!monitoringButton || !state.active || !ctl || view.classList.contains('is-monitoring')) return;
    view.classList.add('is-monitoring');
    const modeToggle = el('#monitoring-mode-toggle');
    modeToggle.hidden = true;
    ctl.focusCurrentCase().then(() => {
      if (!view.classList.contains('is-monitoring')) return;
      modeToggle.hidden = false;
      el('#monitoring-info-card').hidden = false;
      el('#monitoring-controls').hidden = false;
      el('#mobile-monitoring-bar').hidden = false;
      setMobileMonitoringPane('info');
      syncMonitoringModeToggle();
    });
  });
  el('#mobile-monitoring-bar').addEventListener('click', (event) => {
    const tab = event.target.closest('button[data-mobile-pane]');
    if (tab && el('.view').classList.contains('is-monitoring')) {
      setMobileMonitoringPane(tab.dataset.mobilePane);
    }
  });
  el('#monitoring-mode-toggle').addEventListener('click', () => {
    if (!ctl || !el('.view').classList.contains('is-monitoring')) return;
    const is3d = ctl.mode !== 'terrain';
    ctl.setMonitoringOrbitEnabled(false);
    ctl.setMode(is3d ? 'terrain' : 'flat');
    ctl.setHillshade(is3d);
    if (is3d) ctl.setMonitoringOrbitEnabled(true);
    ctl.map.getCanvas().style.cursor = is3d ? 'grab' : '';
    syncMonitoringModeToggle();
  });
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
    const sideCopy = copy().side;
    el('#terrain-btn').textContent = next === 'terrain' ? sideCopy.terrainOn : sideCopy.terrainOff;
  });

  el('#opacity-range').addEventListener('input', (e) => {
    const val = Number(e.target.value);
    el('#opacity-out').textContent = `${Math.round(val * 100)}%`;
    const monitoringOpacity = el('#monitoring-opacity-range');
    if (monitoringOpacity && !monitoringOpacity.disabled) {
      monitoringOpacity.value = String(val);
      el('#monitoring-opacity-output').textContent = `${Math.round(val * 100)}%`;
    }
    if (state.datasetId) ctl?.setDatasetOpacity(state.datasetId, val);
  });

  el('#btn-view-before').addEventListener('click', () => {
    state.mapMode = 'before';
    state.frameId = getBeforeAfterPair(activeDataset()).before?.id ?? null;
    syncMapToLayer();
  });
  el('#btn-view-after').addEventListener('click', () => {
    state.mapMode = 'after';
    state.frameId = getBeforeAfterPair(activeDataset()).after?.id ?? null;
    syncMapToLayer();
  });
  el('#btn-view-swipe').addEventListener('click', () => { state.mapMode = 'swipe'; syncMapToLayer(); });

  el('#swipe-split').addEventListener('input', (e) => {
    state.split = Number(e.target.value);
    el('#swipe-out').textContent = `${Math.round(state.split * 100)}%`;
    ctl?.setSwipeSplit(state.split);
  });

  el('#btn-dl-comp').addEventListener('click', () => {
    const ds = activeDataset();
    if (state.active && ds) downloadSideBySidePng(state.active, ds, state.varId, state.lang);
  });
  el('#btn-open-modal').addEventListener('click', openComparisonModal);
  el('#hero-data-guide-btn').addEventListener('click', openDataGuideModal);
  el('#data-guide-modal').addEventListener('click', (e) => {
    if (e.target === el('#data-guide-modal')) el('#data-guide-modal').close();
  });
  el('#modal-close').addEventListener('click', () => el('#compare-modal').close());
  el('#compare-modal').addEventListener('click', (e) => {
    if (e.target === el('#compare-modal')) el('#compare-modal').close();
  });

  el('#modal-dl-before').addEventListener('click', () => {
    const ds = activeDataset();
    const { before } = getBeforeAfterPair(ds);
    if (state.active && ds && before) downloadSingleRenderedPng(state.active.id, ds, before, state.varId, 'before');
  });
  el('#modal-dl-after').addEventListener('click', () => {
    const ds = activeDataset();
    const { after, before } = getBeforeAfterPair(ds);
    const frame = after || before;
    if (state.active && ds && frame) downloadSingleRenderedPng(state.active.id, ds, frame, state.varId, 'after');
  });
  el('#modal-dl-comp').addEventListener('click', () => {
    const ds = activeDataset();
    if (state.active && ds) downloadSideBySidePng(state.active, ds, state.varId, state.lang);
  });
}

async function boot() {
  applyStaticTranslations();
  await initMapController();
  bindEvents();
  const { cases, problems } = await loadCatalog('data/catalog.json');
  state.cases = cases;
  renderHomeCases(cases);
  if (problems.length) setStatus(`${copy().modal.warningPrefix}${problems[0]}`);
  if (!cases.length) {
    el('#case-desc').textContent = copy().modal.noDataCases;
    return;
  }

  renderCaseSelect(cases);

  history.replaceState(null, '', `${location.pathname}${location.search}`);
  ctl?.setCases(cases);
  await ctl?.showWorld();
}

boot();
