import mapboxgl from 'mapbox-gl';
import { frameAt, vectorCutoff } from './timeline.js';
import { resolveUrl } from './registry.js';
import { loadGray, grayToValue, renderFrameCanvas } from './rasterCanvas.js';
import { DEFAULT_LANG, getLocalizedCaseTitle, getUiCopy } from './i18n.js';

export const BASEMAPS = {
  satellite: { label: 'Satellite', style: 'mapbox://styles/mapbox/satellite-streets-v12' },
  light: { label: 'Light', style: 'mapbox://styles/mapbox/light-v11' },
  streets: { label: 'Streets', style: 'mapbox://styles/mapbox/streets-v12' },
};
export const VIEW_MODES = { globe: '3D Globe', flat: 'Flat Map', terrain: '3D Terrain' };

const FALLBACK_RASTER_SOURCES = {
  satellite: {
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri, Maxar, Earthstar Geographics',
  },
  light: {
    tiles: ['https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap · © CARTO',
  },
  streets: {
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: '© OpenStreetMap contributors',
  },
};

function buildFallbackStyle(key) {
  const src = FALLBACK_RASTER_SOURCES[key] || FALLBACK_RASTER_SOURCES.satellite;
  return {
    version: 8,
    sources: {
      'esri-fallback': {
        type: 'raster',
        tiles: src.tiles,
        tileSize: 256,
        maxzoom: 19,
        attribution: src.attribution,
      },
    },
    layers: [{ id: 'esri-fallback', type: 'raster', source: 'esri-fallback' }],
  };
}

const DEM_SOURCE = 'base-dem';
const HILLSHADE_LAYER = 'base-hillshade';
const SKY_LAYER = 'base-sky';
const ANCHOR_VEC = 'anchor-vectors'; // rasters are inserted below; vectors above
const WORLD_SOURCE = 'world-cases';
const WORLD_DOT = 'world-cases-dot';
const WORLD_LABEL = 'world-cases-label';
const SWIPE_SOURCE = 'swipe-img';
const SWIPE_LINE_SOURCE = 'swipe-line';
const WORLD_VIEW = { center: [-126.705, 20], zoom: 1.5, pitch: 10, bearing: 0 };
const HOME_CASE_LONGITUDE_RANGE = 45;
const DRAG_THRESHOLD = 6;
const PARADISE_AOI_BOUNDS = [-121.78, 39.68, -121.63, 39.79];
const PARADISE_REFERENCE_ZOOM = 11.4;
const MONITORING_ZOOM_OUTSET = 0.5;
const EARTH_KM_PER_DEGREE = 111.32;

const vid = (v) => `vec:${v.id}`;
const extentCoords = ([w, s, e, n]) => [[w, n], [e, n], [e, s], [w, s]];
const redact = (s) => String(s || '').replace(/access_token=[^&\s"')]+/g, 'access_token=…');
const smoothStep = (t) => t * t * (3 - 2 * t);
const longitudeDistance = (a, b) => Math.abs((((a - b + 180) % 360) + 360) % 360 - 180);
const boundsAreaKm2 = ([west, south, east, north]) => {
  const width = EARTH_KM_PER_DEGREE * Math.cos(((south + north) / 2) * Math.PI / 180) * (east - west);
  const height = EARTH_KM_PER_DEGREE * (north - south);
  return width * height;
};

function getAoiBounds(def) {
  const aoi = def.vectors.find((vector) => vector.id === 'aoi')?.data;
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  const visit = (coordinates) => {
    if (!Array.isArray(coordinates)) return;
    if (Number.isFinite(coordinates[0]) && Number.isFinite(coordinates[1])) {
      bounds[0] = Math.min(bounds[0], coordinates[0]);
      bounds[1] = Math.min(bounds[1], coordinates[1]);
      bounds[2] = Math.max(bounds[2], coordinates[0]);
      bounds[3] = Math.max(bounds[3], coordinates[1]);
      return;
    }
    coordinates.forEach(visit);
  };
  (aoi?.features ?? []).forEach((feature) => visit(feature.geometry?.coordinates));
  return bounds.every(Number.isFinite) ? bounds : null;
}

function getMonitoringZoom(def) {
  const bounds = getAoiBounds(def);
  if (!bounds) return (def.view.zoom ?? 10) - MONITORING_ZOOM_OUTSET;
  const areaRatio = boundsAreaKm2(bounds) / boundsAreaKm2(PARADISE_AOI_BOUNDS);
  if (!Number.isFinite(areaRatio) || areaRatio <= 0) {
    return (def.view.zoom ?? 10) - MONITORING_ZOOM_OUTSET;
  }
  const equivalentSideRatio = Math.sqrt(areaRatio);
  return PARADISE_REFERENCE_ZOOM - MONITORING_ZOOM_OUTSET - Math.log2(equivalentSideRatio);
}

export class MapController {
  constructor({ container, token = '', onStatus, lang = DEFAULT_LANG }) {
    this.hasToken = Boolean(token && String(token).trim());
    if (this.hasToken) mapboxgl.accessToken = token;
    this.lang = lang;
    this.onStatus = onStatus || (() => {});
    this.onFeature = () => {};
    this.onCasePick = () => {};
    this.onWorldClick = () => {};
    this.onWorldTransitionEnd = () => {};
    this.onVisibleCasesChange = () => {};
    this.onDatasetState = () => {};
    this.current = null;
    this.cases = [];
    this.time = null;
    this.caseSeq = 0;
    this.layerSeq = 0;
    this.swipeSeq = 0;
    this.datasets = new Map(); // id -> runtime dataset state (case JSON stays untouched)
    this.visibleVec = new Set();
    this.swipe = null;
    this._swipeCanvas = null;
    this._raf = 0;
    this.popup = null;
    this.errSeen = new Map();

    this.basemap = 'satellite';
    this.mode = 'globe';
    this.exaggeration = 1.5;
    this.sunAzimuth = 315;
    this.hillshadeOn = true;
    this.orbitEnabled = true;
    this.orbitTimer = null;
    this.monitoringMode = false;
    this.monitoringOrbitEnabled = false;
    this.monitoringOrbitTimer = null;
    this.mapInteractionEnabled = true;
    this.scrollZoomAllowed = false;
    this.lastVisibleCaseCheck = 0;
    this.homeTransitionId = 0;
    this.caseFocusTransitionId = 0;
    this.homeLeftPadding = container.clientWidth >= 1024 ? Math.round(Math.min(320, container.clientWidth * 0.22)) : 0;

    this.map = new mapboxgl.Map({
      container,
      style: this.#resolveStyle(this.basemap),
      center: WORLD_VIEW.center,
      zoom: WORLD_VIEW.zoom,
      pitch: WORLD_VIEW.pitch,
      projection: 'globe',
    });
    this.map.setPadding({ left: this.homeLeftPadding, top: 0, right: 0, bottom: 0 });
    this.map.scrollZoom.disable();
    this.map.dragRotate.disable();
    this.map.getCanvasContainer().addEventListener('contextmenu', (event) => event.preventDefault());

    const isInteractiveTarget = (target) => target instanceof Element
      && target.closest('button, a, input, select, textarea, [role="button"], [role="link"], [contenteditable="true"], .mapboxgl-ctrl');
    let mapPointerGesture = null;
    this.map.getContainer().addEventListener('pointerdown', (event) => {
      if (this.mapInteractionEnabled || event.button !== 0 || event.isPrimary === false || isInteractiveTarget(event.target)) {
        mapPointerGesture = null;
        return;
      }
      mapPointerGesture = {
        x: event.clientX,
        y: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        pointerId: event.pointerId,
        moved: false,
        monitoring: this.monitoringMode,
        rotatable: this.monitoringMode && this.mode === 'terrain',
      };
      if (mapPointerGesture.rotatable) this.map.getCanvas().style.cursor = 'grabbing';
    });
    window.addEventListener('pointermove', (event) => {
      if (!mapPointerGesture || event.pointerId !== mapPointerGesture.pointerId) return;
      const deltaX = event.clientX - mapPointerGesture.lastX;
      const deltaY = event.clientY - mapPointerGesture.lastY;
      if (Math.hypot(event.clientX - mapPointerGesture.x, event.clientY - mapPointerGesture.y) >= DRAG_THRESHOLD) {
        mapPointerGesture.moved = true;
      }
      if (mapPointerGesture.rotatable && mapPointerGesture.moved) {
        if (this.monitoringOrbitEnabled) this.setMonitoringOrbitEnabled(false);
        const bearing = ((this.map.getBearing() + deltaX * 0.35) % 360 + 360) % 360;
        const pitch = Math.max(20, Math.min(80, this.map.getPitch() - deltaY * 0.25));
        this.map.jumpTo({ bearing, pitch });
        this.map.getCanvas().style.cursor = 'grabbing';
      }
      mapPointerGesture.lastX = event.clientX;
      mapPointerGesture.lastY = event.clientY;
    });
    window.addEventListener('pointerup', (event) => {
      if (!mapPointerGesture || event.pointerId !== mapPointerGesture.pointerId) return;
      const { moved, monitoring } = mapPointerGesture;
      mapPointerGesture = null;
      if (monitoring && this.monitoringMode) {
        this.map.getCanvas().style.cursor = this.mode === 'terrain' ? 'grab' : '';
      }
      if (moved && !monitoring && !isInteractiveTarget(event.target) && !this.mapInteractionEnabled && !this.orbitEnabled) {
        this.onWorldClick(event);
      }
    });
    window.addEventListener('pointercancel', () => {
      mapPointerGesture = null;
      if (this.monitoringMode) this.map.getCanvas().style.cursor = this.mode === 'terrain' ? 'grab' : '';
    });

    this.map.on('dragstart', () => this.setOrbitEnabled(false));
    this.map.addControl(new mapboxgl.FullscreenControl(), 'top-right');
    this.map.addControl(new mapboxgl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    this.ready = new Promise((res) => { if (this.map.isStyleLoaded()) res(); else this.map.once('style.load', res); });
    this.map.once('load', () => this.#startOrbit());
    this.map.on('style.load', () => this.#applyAll());
    this.map.on('error', (e) => this.#onError(e));
    this.map.on('click', (e) => this.#click(e));
    this.map.on('mousemove', (e) => {
      const ids = this.mapInteractionEnabled ? [...this.#vectorIds(), ...this.#worldIds()] : this.monitoringMode ? this.#vectorIds() : [];
      const hit = ids.length && this.map.queryRenderedFeatures(e.point, { layers: ids }).length;
      if (!this.mapInteractionEnabled) {
        this.map.getCanvas().style.cursor = hit
          ? 'pointer'
          : this.monitoringMode
            ? this.mode === 'terrain' ? 'grab' : ''
            : this.orbitEnabled ? '' : 'pointer';
        return;
      }
      this.map.getCanvas().style.cursor = hit ? 'pointer' : '';
    });
    this.map.on('render', () => {
      const now = performance.now();
      if (now - this.lastVisibleCaseCheck < 350) return;
      this.lastVisibleCaseCheck = now;
      this.#reportVisibleCases();
    });
  }

  // ───────────── Errors: specific, deduplicated, and never auto-retried ─────────────
  #onError(e) {
    const err = e?.error || {};
    if (err.name === 'AbortError') return;
    const msgCopy = getUiCopy(this.lang).mapErrors;
    const status = err.status;
    const sourceId = e?.sourceId || '';
    const detail = redact(err.message) || msgCopy.noDetails;
    const key = `${sourceId}|${status}|${detail}`;
    const now = Date.now();
    if (now - (this.errSeen.get(key) || 0) < 15000) return; // avoid flooding the UI with duplicate errors
    this.errSeen.set(key, now);
    const http = status === 429 ? msgCopy.rateLimited
      : status ? `HTTP ${status}` : detail;

    if (sourceId.startsWith('ds:')) {
      if (status === 404) return; // out-of-bounds tiles are expected
      const st = this.datasets.get(sourceId.split(':')[1]);
      if (st) this.#emit(st, 'error', msgCopy.failedToLoad(http));
      return;
    }
    if (sourceId === DEM_SOURCE) {
      this.onStatus('warn', msgCopy.demUnavailable(http), 'retry-base');
      return;
    }
    if (sourceId === 'mapbox-satellite' && status === 429) {
      if (this.map.getLayer('esri-fallback')) this.map.setLayoutProperty('esri-fallback', 'visibility', 'visible');
      if (this.map.getLayer('satellite')) this.map.setLayoutProperty('satellite', 'visibility', 'none');
      return;
    }
    this.onStatus('error', msgCopy.basemapError(http, sourceId), status === 429 ? 'retry-base' : null);
  }

  // ───────────── Globe orbit and interaction guards ─────────────
  #startOrbit() {
    if (!this.orbitEnabled || this.orbitTimer !== null) return;
    let previousTime = performance.now();
    this.orbitTimer = window.setInterval(() => {
      const now = performance.now();
      const elapsed = now - previousTime;
      previousTime = now;
      if (this.map.isMoving()) return;

      const center = this.map.getCenter();
      const longitude = ((center.lng + elapsed * 0.005 + 180) % 360 + 360) % 360 - 180;
      this.map.setCenter([longitude, center.lat]);
    }, 40);
  }

  #stopOrbit() {
    if (this.orbitTimer === null) return;
    window.clearInterval(this.orbitTimer);
    this.orbitTimer = null;
  }

  #startMonitoringOrbit() {
    if (!this.monitoringOrbitEnabled || this.monitoringOrbitTimer !== null) return;
    let previousTime = performance.now();
    this.monitoringOrbitTimer = window.setInterval(() => {
      const now = performance.now();
      const elapsed = now - previousTime;
      previousTime = now;
      if (this.map.isMoving()) return;

      const bearing = (this.map.getBearing() + elapsed * 0.005) % 360;
      this.map.setBearing(bearing);
    }, 40);
  }

  #stopMonitoringOrbit() {
    if (this.monitoringOrbitTimer === null) return;
    window.clearInterval(this.monitoringOrbitTimer);
    this.monitoringOrbitTimer = null;
  }

  setOrbitEnabled(enabled) {
    this.orbitEnabled = enabled;
    if (enabled) this.#startOrbit();
    else this.#stopOrbit();
  }

  setMonitoringOrbitEnabled(enabled) {
    this.monitoringOrbitEnabled = enabled;
    if (enabled) this.#startMonitoringOrbit();
    else this.#stopMonitoringOrbit();
  }

  setMapInteractionEnabled(enabled) {
    this.mapInteractionEnabled = enabled;
    ['dragPan', 'boxZoom', 'keyboard', 'doubleClickZoom', 'touchZoomRotate', 'touchPitch'].forEach((name) => {
      const handler = this.map[name];
      if (handler) handler[enabled ? 'enable' : 'disable']();
    });
    this.map.scrollZoom[this.scrollZoomAllowed && enabled ? 'enable' : 'disable']();
  }

  setScrollZoomEnabled(enabled) {
    this.scrollZoomAllowed = enabled;
    this.map.scrollZoom[this.mapInteractionEnabled && enabled ? 'enable' : 'disable']();
  }

  // ───────────── Managed layers (re-applied after every style change) ─────────────
  #applyAll() {
    this.#addTerrainBase();
    this.#addWorldMarkers();
    this.datasets.forEach((st) => { st.tileKeys = new Set(); });
    this.#addVectors();
    this.#renderSwipe();
    this.#applyMode();
    this.#applyVectorTime();
    this.#updateRasters();
  }

  #addTerrainBase() {
    const m = this.map;
    if (!m.getSource(DEM_SOURCE)) {
      m.addSource(DEM_SOURCE, { type: 'raster-dem', tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, maxzoom: 14 });
    }
    if (!m.getSource('esri-fallback')) {
      m.addSource('esri-fallback', { type: 'raster', tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, maxzoom: 19, attribution: '© Esri, Maxar' });
    }
    if (!m.getLayer('esri-fallback')) {
      const before = m.getLayer('satellite') ? 'satellite' : undefined;
      m.addLayer({ id: 'esri-fallback', type: 'raster', source: 'esri-fallback', layout: { visibility: 'visible' } }, before);
    }
    // Hillshade: BASE TERRAIN, not SAR data. minzoom avoids requesting global DEM tiles on the globe view.
    if (!m.getLayer(HILLSHADE_LAYER)) {
      m.addLayer({
        id: HILLSHADE_LAYER, type: 'hillshade', source: DEM_SOURCE, minzoom: 5,
        paint: { 'hillshade-exaggeration': 0.5, 'hillshade-illumination-direction': this.sunAzimuth, 'hillshade-shadow-color': '#1B2430', 'hillshade-highlight-color': '#FFFFFF' },
        layout: { visibility: this.hillshadeOn ? 'visible' : 'none' },
      });
    }
    if (!m.getLayer(ANCHOR_VEC)) m.addLayer({ id: ANCHOR_VEC, type: 'background', paint: { 'background-opacity': 0 } });
    if (!m.getLayer(SKY_LAYER)) {
      m.addLayer({ id: SKY_LAYER, type: 'sky', paint: { 'sky-type': 'atmosphere', 'sky-atmosphere-sun': [this.sunAzimuth, 60], 'sky-atmosphere-sun-intensity': 8 } });
    }
  }

  #resolveStyle(key) {
    return this.hasToken ? BASEMAPS[key].style : buildFallbackStyle(key);
  }

  #addWorldMarkers() {
    const m = this.map;
    if (m.getSource(WORLD_SOURCE)) return;
    m.addSource(WORLD_SOURCE, { type: 'geojson', data: this.#worldData() });
    m.addLayer({ id: WORLD_DOT, type: 'circle', source: WORLD_SOURCE, paint: { 'circle-radius': 7, 'circle-color': '#FFFFFF', 'circle-stroke-color': '#E4572E', 'circle-stroke-width': 3 } });
    if (this.hasToken) {
      m.addLayer({
        id: WORLD_LABEL, type: 'symbol', source: WORLD_SOURCE,
        layout: { 'text-field': ['get', 'title'], 'text-size': 12, 'text-offset': [0, 1.4], 'text-anchor': 'top', 'text-max-width': 12 },
        paint: { 'text-color': '#1B2430', 'text-halo-color': '#FFFFFF', 'text-halo-width': 1.6 },
      });
    }
  }

  #worldData() {
    return {
      type: 'FeatureCollection',
      features: this.cases.map((c) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: c.view.center },
        properties: { id: c.id, title: getLocalizedCaseTitle(c, this.lang) },
      })),
    };
  }

  setLanguage(lang) {
    this.lang = lang || DEFAULT_LANG;
    this.map.getSource(WORLD_SOURCE)?.setData(this.#worldData());
  }

  #reportVisibleCases() {
    if (!this.map.getLayer(WORLD_DOT)) return;
    const centerLongitude = this.map.getCenter().lng;
    const ids = this.cases
      .filter((def) => {
        const longitude = Number(def.view?.center?.[0]);
        return Number.isFinite(longitude)
          && longitudeDistance(longitude, centerLongitude) <= HOME_CASE_LONGITUDE_RANGE;
      })
      .map((def) => def.id);
    this.onVisibleCasesChange(ids);
  }

  setCases(cases) {
    this.cases = cases;
    this.map.getSource(WORLD_SOURCE)?.setData(this.#worldData());
  }

  #vectorIds() {
    if (!this.current) return [];
    return this.current.vectors
      .filter((v) => v._load === 'ok' && this.map.getLayer(vid(v)) && this.map.getLayoutProperty(vid(v), 'visibility') !== 'none')
      .map(vid).reverse();
  }
  #worldIds() { return this.map.getLayer(WORLD_DOT) ? [WORLD_DOT] : []; }

  // ───────────── Cases & camera transitions ─────────────
  clear() {
    const m = this.map;
    this.closePopup();
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; }
    this.swipeSeq += 1;
    if (this.swipe) { this.swipe = null; this.#removeSwipeLayers(); }
    this.datasets.forEach((st) => this.#removeDatasetLayers(st));
    this.datasets.clear();
    if (this.current) {
      this.current.vectors.forEach((v) => {
        if (m.getLayer(vid(v))) m.removeLayer(vid(v));
        if (m.getSource(vid(v))) m.removeSource(vid(v));
      });
    }
    this.current = null;
  }

  async loadCase(def, { globeOverview = false, hideDatasets = false, initial = null } = {}) {
    const cSeq = ++this.caseSeq;
    this.caseFocusTransitionId += 1;
    this.monitoringMode = false;
    this.setMonitoringOrbitEnabled(false);
    await this.ready;
    if (cSeq !== this.caseSeq) return;
    this.clear();
    this.current = def;
    this.time = initial?.timeMs ?? def.axis[0]?.ms ?? null;
    this.visibleVec = new Set(def.vectors.filter((v) => v.defaultVisible !== false).map((v) => v.id));
    def.datasets.forEach((d) => {
      const isTarget = !hideDatasets && (initial?.datasetId ? d.id === initial.datasetId : d.defaultVisible !== false);
      const sel = isTarget && initial?.selection ? initial.selection : d.variables[0].id;
      const opacity = isTarget && initial?.opacity != null ? initial.opacity : d.render.opacity;
      this.datasets.set(d.id, {
        def: d, visible: isTarget, opacity,
        selection: sel, frame: null, status: 'none', error: null, req: 0, tileKeys: new Set(),
      });
    });
    this.#addVectors();
    this.#applyVectorTime();

    this.mode = globeOverview ? 'globe' : 'terrain';
    this.#applyMode();
    if (globeOverview) {
      const containerWidth = this.map.getContainer().clientWidth || window.innerWidth;
      const isNarrowViewport = containerWidth <= 900;
      this.flyToWorld(def.view.center, {
        zoom: isNarrowViewport ? WORLD_VIEW.zoom : WORLD_VIEW.zoom + Math.log2(1.3),
        leftPadding: isNarrowViewport ? 0 : 1300,
      });
    } else {
      this.flyToCase();
    }
    await this.#updateRasters();
  }

  async showWorld() {
    const cSeq = ++this.caseSeq;
    this.caseFocusTransitionId += 1;
    this.monitoringMode = false;
    this.setMonitoringOrbitEnabled(false);
    await this.ready;
    if (cSeq !== this.caseSeq) return;
    this.clear();
    this.mode = 'globe';
    this.#applyMode();
    const transitionId = ++this.homeTransitionId;
    this.setMapInteractionEnabled(false);
    this.flyToWorld(WORLD_VIEW.center, { leftPadding: this.homeLeftPadding });
    const finishWorldTransition = () => {
      if (transitionId !== this.homeTransitionId) return;
      if (this.orbitEnabled) this.setMapInteractionEnabled(true);
      this.#reportVisibleCases();
      this.onWorldTransitionEnd();
    };
    this.map.once('moveend', finishWorldTransition);
    if (!this.map.isMoving()) {
      this.map.off('moveend', finishWorldTransition);
      finishWorldTransition();
    }
  }

  flyToWorld(center = WORLD_VIEW.center, { zoom = WORLD_VIEW.zoom, leftPadding = this.homeLeftPadding } = {}) {
    const width = this.map.getContainer().clientWidth || window.innerWidth;
    const safePadding = width > 0 ? Math.max(0, Math.min(leftPadding, Math.round(width * 0.55))) : Math.max(0, leftPadding);
    this.map.flyTo({
      ...WORLD_VIEW,
      center,
      zoom,
      padding: { left: safePadding, top: 0, right: 0, bottom: 0 },
      duration: 2000,
      easing: smoothStep,
      essential: true,
    });
  }

  flyToCase({ zoomOffset = 0, padding } = {}) {
    if (!this.current) return this.map.flyTo({ ...WORLD_VIEW, duration: 1800 });
    const { center, zoom = 10, pitch = 0, bearing = 0 } = this.current.view;
    const tilt = this.mode === 'terrain' ? Math.max(pitch, 55) : this.mode === 'flat' ? 0 : Math.min(pitch, 20);
    this.map.flyTo({
      center,
      zoom: Math.max(1, zoom + zoomOffset),
      pitch: tilt,
      bearing,
      ...(padding && { padding }),
      duration: 3200,
      curve: 1.6,
      essential: true,
    });
  }

  async focusCurrentCase() {
    await this.ready;
    const def = this.current;
    if (!def) return;
    this.monitoringMode = false;
    this.setMonitoringOrbitEnabled(false);
    const transitionId = ++this.caseFocusTransitionId;
    const container = this.map.getContainer();
    const containerWidth = container.clientWidth || window.innerWidth;
    const containerHeight = container.clientHeight || window.innerHeight;
    const isMobileViewport = containerWidth <= 768;
    const targetPadding = isMobileViewport
      ? { top: 56, bottom: Math.round(containerHeight * 0.42), left: 0, right: 0 }
      : { left: 0, top: 0, right: 0, bottom: 0 };
    const centered = new Promise((resolve) => {
      this.map.easeTo({
        padding: targetPadding,
        duration: 700,
        easing: smoothStep,
        essential: true,
      });
      if (!this.map.isMoving()) {
        resolve();
        return;
      }
      this.map.once('moveend', resolve);
    });
    await centered;
    if (transitionId !== this.caseFocusTransitionId || this.current !== def) return;
    this.mode = 'flat';
    this.setHillshade(false);
    this.#applyMode();
    const mobileZoomAdjust = isMobileViewport
      ? -Math.max(0.45, Math.min(1.25, Math.log2(740 / Math.max(containerWidth, 320))))
      : 0;
    const zoomOffset = getMonitoringZoom(def) + mobileZoomAdjust - (def.view.zoom ?? 10);
    this.flyToCase({ zoomOffset, padding: targetPadding });
    if (this.map.isMoving()) {
      await new Promise((resolve) => this.map.once('moveend', resolve));
    }
    if (transitionId === this.caseFocusTransitionId && this.current === def) {
      this.monitoringMode = true;
      this.map.getCanvas().style.cursor = '';
    }
  }

  cancelCaseFocus() {
    this.caseFocusTransitionId += 1;
    this.monitoringMode = false;
    this.setMonitoringOrbitEnabled(false);
    this.map.getCanvas().style.cursor = '';
  }

  // ───────────── Vectors (GeoJSON), independent from rasters ─────────────
  #addVectors() {
    if (!this.current) return;
    const m = this.map;
    for (const v of this.current.vectors) {
      if (v._load !== 'ok' || m.getLayer(vid(v))) continue;
      try {
        if (!m.getSource(vid(v))) m.addSource(vid(v), { type: 'geojson', data: v.data });
        m.addLayer({ id: vid(v), type: v.type, source: vid(v), paint: v.paint || {}, layout: v.layout || {} }, WORLD_DOT);
      } catch (err) {
        v._load = 'error';
        v._error = err.message;
      }
    }
  }

  #applyVectorTime() {
    if (!this.current) return;
    const ms = this.time ?? -Infinity;
    for (const v of this.current.vectors) {
      if (v._load !== 'ok' || !this.map.getLayer(vid(v))) continue;
      let show = this.visibleVec.has(v.id);
      if (v.time) {
        const cut = vectorCutoff(v, ms);
        const p = ['get', v.time.property];
        if (cut === null) { show = false; this.map.setFilter(vid(v), ['==', p, '__none__']); }
        else this.map.setFilter(vid(v), [v.time.mode === 'cumulative' ? '<=' : '==', p, cut]);
      }
      this.map.setLayoutProperty(vid(v), 'visibility', show ? 'visible' : 'none');
    }
  }

  setVectorVisible(id, on) {
    on ? this.visibleVec.add(id) : this.visibleVec.delete(id);
    this.#applyVectorTime();
  }
  isVectorVisible(id) { return this.visibleVec.has(id); }

  // ───────────── Time & per-frame rasters ─────────────
  setTime(ms) {
    this.time = ms;
    this.#applyVectorTime();
    return this.#updateRasters();
  }

  #updateRasters() {
    return Promise.allSettled([...this.datasets.values()].map((st) => this.#applyDataset(st)));
  }

  dataset(id) { return this.datasets.get(id); }

  #emit(st, status, error = null) {
    st.status = status;
    st.error = error;
    this.onDatasetState(st.def.id, { status, error, frame: st.frame });
  }

  #missingVariable(st, frame) {
    const comp = st.def.composites.find((c) => c.id === st.selection);
    const need = comp ? Object.values(comp.channels).filter(Boolean) : [st.selection];
    return need.find((v) => !frame.files[v]) || null;
  }

  async #applyDataset(st) {
    const req = ++st.req;
    const frame = this.time == null ? null : frameAt(st.def, this.time);
    st.frame = frame;
    if (!frame) { this.#hideDataset(st); return this.#emit(st, 'none'); }
    const msgCopy = getUiCopy(this.lang).mapErrors;
    const missing = this.#missingVariable(st, frame);
    if (missing) { this.#hideDataset(st); return this.#emit(st, 'novar', msgCopy.missingVariable(missing)); }
    if (!st.visible || this.swipe?.st === st) { this.#hideDataset(st); return this.#emit(st, 'ok'); }
    try {
      if (st.def.source.type === 'image') {
        const r = await renderFrameCanvas(st.def, frame, st.selection);
        if (req !== st.req || this.datasets.get(st.def.id) !== st) return;
        this.#showImage(st, r.url);
      } else {
        this.#showTiles(st, frame);
      }
      if (req === st.req) this.#emit(st, 'ok');
    } catch (err) {
      if (req === st.req) { this.#hideDataset(st); this.#emit(st, 'error', msgCopy.failedToLoad(err.message)); }
    }
  }

  #paintOf(st) {
    return { 'raster-opacity': st.opacity, 'raster-resampling': st.def.render.resampling, 'raster-fade-duration': 0 };
  }

  #showImage(st, url) {
    const m = this.map;
    const id = `ds:${st.def.id}`;
    const coordinates = extentCoords(st.def.extent);
    if (!m.getSource(id)) {
      m.addSource(id, { type: 'image', url, coordinates });
      m.addLayer({ id, type: 'raster', source: id, paint: this.#paintOf(st) }, ANCHOR_VEC);
    } else {
      m.getSource(id).updateImage({ url, coordinates });
    }
    m.setPaintProperty(id, 'raster-opacity', st.opacity);
    m.setLayoutProperty(id, 'visibility', 'visible');
  }

  #ensureTileLayer(st, frame, sel) {
    const m = this.map;
    const d = st.def;
    const key = `ds:${d.id}:${frame.id}:${sel}`;
    if (!m.getSource(key)) {
      m.addSource(key, {
        type: 'raster', tiles: [resolveUrl(frame.files[sel])], tileSize: d.source.tileSize ?? 256,
        ...(d.source.minzoom != null && { minzoom: d.source.minzoom }),
        ...(d.source.maxzoom != null && { maxzoom: d.source.maxzoom }),
        ...(d.extent && { bounds: d.extent }),
      });
      m.addLayer({ id: key, type: 'raster', source: key, paint: this.#paintOf(st), layout: { visibility: 'none' } }, ANCHOR_VEC);
      st.tileKeys.add(key);
    }
    return key;
  }

  #showTiles(st, frame) {
    const key = this.#ensureTileLayer(st, frame, st.selection);
    st.tileKeys.forEach((k) => {
      if (this.map.getLayer(k)) this.map.setLayoutProperty(k, 'visibility', k === key ? 'visible' : 'none');
    });
    this.map.setPaintProperty(key, 'raster-opacity', st.opacity);
  }

  #hideDataset(st) {
    const m = this.map;
    const id = `ds:${st.def.id}`;
    if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', 'none');
    st.tileKeys.forEach((k) => { if (m.getLayer(k)) m.setLayoutProperty(k, 'visibility', 'none'); });
  }

  #removeDatasetLayers(st) {
    const m = this.map;
    [`ds:${st.def.id}`, ...st.tileKeys].forEach((id) => {
      if (m.getLayer(id)) m.removeLayer(id);
      if (m.getSource(id)) m.removeSource(id);
    });
    st.tileKeys.clear();
  }

  /** Preload the next frame (colorized canvas or hidden tile layer) for smooth playback. */
  prefetch(ms) {
    for (const st of this.datasets.values()) {
      const f = frameAt(st.def, ms);
      if (!f || f === st.frame || this.#missingVariable(st, f)) continue;
      if (st.def.source.type === 'image') renderFrameCanvas(st.def, f, st.selection).catch(() => {});
      else this.#ensureTileLayer(st, f, st.selection);
    }
  }

  setDatasetVisible(id, on) { const st = this.datasets.get(id); if (!st) return; st.visible = on; return this.#applyDataset(st); }
  setDatasetSelection(id, sel) { const st = this.datasets.get(id); if (!st) return; st.selection = sel; return this.#applyDataset(st); }
  retryDataset(id) { const st = this.datasets.get(id); if (!st) return; this.errSeen.clear(); this.#removeDatasetLayers(st); return this.#applyDataset(st); }
  setDatasetOpacity(id, value) {
    const st = this.datasets.get(id);
    if (!st) return;
    st.opacity = value;
    [`ds:${id}`, ...st.tileKeys].forEach((l) => { if (this.map.getLayer(l)) this.map.setPaintProperty(l, 'raster-opacity', value); });
    if (this.swipe?.st === st && this.map.getLayer(SWIPE_SOURCE)) this.map.setPaintProperty(SWIPE_SOURCE, 'raster-opacity', value);
  }

  /** Apply dataset visibility, variable selection, opacity, time, and swipe in one pass. */
  async applyLayerState({ datasetId, selection, opacity, timeMs, swipe } = {}) {
    if (!this.current) return;
    const lSeq = ++this.layerSeq;
    if (timeMs != null && timeMs !== this.time) {
      this.time = timeMs;
      this.#applyVectorTime();
    }
    for (const [id, st] of this.datasets) {
      if (datasetId != null) st.visible = id === datasetId;
      if (st.visible && selection != null) st.selection = selection;
      if (st.visible && opacity != null) {
        st.opacity = opacity;
        [`ds:${id}`, ...st.tileKeys].forEach((l) => {
          if (this.map.getLayer(l)) this.map.setPaintProperty(l, 'raster-opacity', opacity);
        });
        if (this.swipe?.st === st && this.map.getLayer(SWIPE_SOURCE)) {
          this.map.setPaintProperty(SWIPE_SOURCE, 'raster-opacity', opacity);
        }
      }
    }
    if (swipe) {
      await this.setSwipe(swipe);
      if (lSeq !== this.layerSeq) return;
    } else if (this.swipe) {
      this.swipeSeq += 1;
      this.swipe = null;
      this.#removeSwipeLayers();
    }
    await this.#updateRasters();
  }

  // ───────────── Before/After comparison (swipe) ─────────────
  // Splits the GEOGRAPHIC EXTENT (image columns), not the screen: works across globe, terrain, and rotation.
  async setSwipe(opts) {
    const sSeq = ++this.swipeSeq;
    if (!opts) {
      const st = this.swipe?.st;
      this.swipe = null;
      this.#removeSwipeLayers();
      if (st && this.datasets.get(st.def.id) === st) await this.#applyDataset(st);
      return;
    }
    const st = this.datasets.get(opts.datasetId);
    if (!st) return;
    const fb = st.def.frames.find((f) => f.id === opts.beforeId);
    const fa = st.def.frames.find((f) => f.id === opts.afterId);
    if (!fb || !fa) throw new Error('Swipe requires valid Before and After frames');
    const [rb, ra] = await Promise.all([
      renderFrameCanvas(st.def, fb, opts.selection),
      renderFrameCanvas(st.def, fa, opts.selection),
    ]);
    if (sSeq !== this.swipeSeq || this.datasets.get(opts.datasetId) !== st) return;
    if (!rb.canvas || !ra.canvas) throw new Error('Swipe requires a local raster (image + gray8/rgba)');
    this.swipe = {
      st,
      cb: rb.canvas,
      ca: ra.canvas,
      beforeFrame: fb,
      afterFrame: fa,
      split: opts.split ?? 0.5,
    };
    this.#hideDataset(st);
    this.#renderSwipe();
  }

  setSwipeSplit(f) {
    if (!this.swipe) return;
    this.swipe.split = f;
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.#renderSwipe(); });
  }

  #renderSwipe() {
    if (!this.swipe) return;
    const { st, cb, ca } = this.swipe;
    const m = this.map;
    const W = cb.width;
    const H = cb.height;
    const sx = Math.round(this.swipe.split * W);
    const f = sx / W; // quantized fraction so image split and divider line stay aligned
    const out = this._swipeCanvas || (this._swipeCanvas = document.createElement('canvas'));
    if (out.width !== W || out.height !== H) {
      out.width = W;
      out.height = H;
    }
    const ctx = out.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    if (sx > 0) ctx.drawImage(cb, 0, 0, cb.width * f, cb.height, 0, 0, sx, H);
    if (sx < W) ctx.drawImage(ca, ca.width * f, 0, ca.width * (1 - f), ca.height, sx, 0, W - sx, H);
    const url = out.toDataURL('image/png');
    const [w, s, e, n] = st.def.extent;
    const coordinates = extentCoords(st.def.extent);
    if (!m.getSource(SWIPE_SOURCE)) {
      m.addSource(SWIPE_SOURCE, { type: 'image', url, coordinates });
      m.addLayer({ id: SWIPE_SOURCE, type: 'raster', source: SWIPE_SOURCE, paint: this.#paintOf(st) }, ANCHOR_VEC);
    } else {
      m.getSource(SWIPE_SOURCE).updateImage({ url, coordinates });
    }
    const lon = w + f * (e - w);
    const line = { type: 'Feature', geometry: { type: 'LineString', coordinates: [[lon, s], [lon, n]] }, properties: {} };
    if (!m.getSource(SWIPE_LINE_SOURCE)) {
      m.addSource(SWIPE_LINE_SOURCE, { type: 'geojson', data: line });
      m.addLayer({ id: SWIPE_LINE_SOURCE, type: 'line', source: SWIPE_LINE_SOURCE, paint: { 'line-color': '#FFFFFF', 'line-width': 3, 'line-blur': 0.5 } }, WORLD_DOT);
    } else {
      m.getSource(SWIPE_LINE_SOURCE).setData(line);
    }
  }

  #removeSwipeLayers() {
    [SWIPE_SOURCE, SWIPE_LINE_SOURCE].forEach((id) => {
      if (this.map.getLayer(id)) this.map.removeLayer(id);
      if (this.map.getSource(id)) this.map.removeSource(id);
    });
  }

  // ───────────── Raster pixel sampling ─────────────
  async sampleAt({ lng, lat }) {
    const out = [];
    for (const st of this.datasets.values()) {
      const d = st.def;
      if (!st.visible || !st.frame || !d.extent) continue;
      const [w, s, e, n] = d.extent;
      if (lng < w || lng > e || lat < s || lat > n) continue;

      let activeFrame = st.frame;
      let phase = null;
      if (this.swipe?.st === st && this.swipe.beforeFrame && this.swipe.afterFrame) {
        const widthPx = this.swipe.cb?.width || 1;
        const frac = Math.round((this.swipe.split ?? 0.5) * widthPx) / widthPx;
        const isBefore = lng < w + frac * (e - w);
        activeFrame = isBefore ? this.swipe.beforeFrame : this.swipe.afterFrame;
        phase = isBefore ? 'before' : 'after';
      }

      const base = { name: d.name, frame: activeFrame, phase, status: d.status };
      if (d.source.type !== 'image' || d.source.encoding !== 'gray8') { out.push({ ...base, values: null }); continue; }
      const comp = d.composites.find((c) => c.id === st.selection);
      const ids = comp ? [...new Set(Object.values(comp.channels).filter(Boolean))] : [st.selection];
      const values = [];
      for (const id of ids) {
        const v = d.variables.find((x) => x.id === id);
        const file = activeFrame.files[id];
        let value = null;
        if (file) {
          try {
            const g = await loadGray(resolveUrl(file));
            const px = Math.max(0, Math.min(g.w - 1, Math.floor(((lng - w) / (e - w)) * g.w)));
            const py = Math.max(0, Math.min(g.h - 1, Math.floor(((n - lat) / (n - s)) * g.h)));
            value = grayToValue(g.g[py * g.w + px], v.legend, d.source.nodata === undefined ? 0 : d.source.nodata);
          } catch { value = null; }
        }
        values.push({ label: v.label, unit: v.unit, value });
      }
      out.push({ ...base, values });
    }
    return out;
  }

  // ───────────── Basemap, view mode, terrain, and lighting ─────────────
  setBasemap(key) {
    if (!BASEMAPS[key] || key === this.basemap) return;
    this.basemap = key;
    this.map.setStyle(this.#resolveStyle(key)); // 'style.load' reapplies terrain, sky, vectors, and rasters
  }

  setMode(mode) {
    if (!VIEW_MODES[mode]) return;
    this.mode = mode;
    this.#applyMode();
    const pitch = this.map.getPitch();
    if (mode === 'terrain' && pitch < 45) this.map.easeTo({ pitch: 60, duration: 1200 });
    if ((mode === 'flat' || mode === 'globe') && pitch > 0) this.map.easeTo({ pitch: 0, bearing: 0, duration: 1000 });
    if (mode === 'globe' && !this.current) this.map.flyTo({ ...WORLD_VIEW, duration: 1800 });
  }

  #applyMode() {
    const m = this.map;
    if (!m.getSource(DEM_SOURCE)) return;
    m.setProjection(this.mode === 'globe' ? 'globe' : 'mercator');
    m.setTerrain(this.mode === 'terrain' ? { source: DEM_SOURCE, exaggeration: this.exaggeration } : null);
    m.setFog(this.mode === 'flat' ? null : {
      color: 'rgb(186, 210, 235)', 'high-color': 'rgb(36, 92, 223)', 'horizon-blend': 0.03,
      'space-color': 'rgb(11, 11, 25)', 'star-intensity': this.basemap === 'light' ? 0 : 0.2,
    });
    if (m.getLayer(SKY_LAYER)) m.setLayoutProperty(SKY_LAYER, 'visibility', this.mode === 'terrain' ? 'visible' : 'none');
  }

  /** Manual retry (never automatic) for terrain after an error or HTTP 429. */
  retryBase() {
    const m = this.map;
    this.errSeen.clear();
    m.setTerrain(null);
    if (m.getLayer(HILLSHADE_LAYER)) m.removeLayer(HILLSHADE_LAYER);
    if (m.getSource(DEM_SOURCE)) m.removeSource(DEM_SOURCE);
    this.#addTerrainBase();
    this.#applyMode();
  }

  setExaggeration(value) {
    this.exaggeration = value;
    if (this.mode === 'terrain' && this.map.getSource(DEM_SOURCE)) this.map.setTerrain({ source: DEM_SOURCE, exaggeration: value });
  }

  setSunAzimuth(deg) {
    this.sunAzimuth = deg;
    const m = this.map;
    if (m.getLayer(HILLSHADE_LAYER)) m.setPaintProperty(HILLSHADE_LAYER, 'hillshade-illumination-direction', deg);
    if (m.getLayer(SKY_LAYER)) m.setPaintProperty(SKY_LAYER, 'sky-atmosphere-sun', [deg, 60]);
  }

  setHillshade(on) {
    this.hillshadeOn = on;
    if (this.map.getLayer(HILLSHADE_LAYER)) this.map.setLayoutProperty(HILLSHADE_LAYER, 'visibility', on ? 'visible' : 'none');
  }

  // ───────────── Interaction ─────────────
  #click(e) {
    if (!this.mapInteractionEnabled && !this.monitoringMode) {
      if (!this.orbitEnabled) this.onWorldClick(e);
      return;
    }
    const m = this.map;
    if (this.mapInteractionEnabled) {
      const world = this.#worldIds();
      if (world.length) {
        const z = m.queryRenderedFeatures(e.point, { layers: world })[0];
        if (z) return this.onCasePick(z.properties.id);
      }
    }
    const ids = this.#vectorIds();
    const f = ids.length ? m.queryRenderedFeatures(e.point, { layers: ids })[0] : null;
    const layer = f && this.current ? this.current.vectors.find((v) => vid(v) === f.layer.id) : null;
    this.onFeature(layer, f, e.lngLat);
  }

  /** Display or replace a single Mapbox GL popup anchored at `lngLat`. */
  showPopup(lngLat, html) {
    this.closePopup();
    this.popup = new mapboxgl.Popup({ className: 'delta-popup', maxWidth: '300px' })
      .setLngLat(lngLat)
      .setHTML(html)
      .addTo(this.map);
  }

  /** Close any active Mapbox GL feature popup. */
  closePopup() {
    if (this.popup) {
      this.popup.remove();
      this.popup = null;
    }
  }
}
