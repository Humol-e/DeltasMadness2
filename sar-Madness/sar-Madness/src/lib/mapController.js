import mapboxgl from 'mapbox-gl';
import { frameAt, vectorCutoff } from './timeline.js';
import { resolveUrl } from './registry.js';
import { loadGray, colorizeGray, composeRgb, grayToValue } from './rasterCanvas.js';

export const BASEMAPS = {
  satellite: { label: 'Satélite', style: 'mapbox://styles/mapbox/satellite-streets-v12' },
  light: { label: 'Claro', style: 'mapbox://styles/mapbox/light-v11' },
  streets: { label: 'Calles', style: 'mapbox://styles/mapbox/streets-v12' },
};
export const VIEW_MODES = { globe: 'Globo 3D', flat: 'Mapa plano', terrain: 'Terreno 3D' };

const DEM_SOURCE = 'base-dem';
const HILLSHADE_LAYER = 'base-hillshade';
const SKY_LAYER = 'base-sky';
const ANCHOR_VEC = 'anchor-vectors'; // los rásteres se insertan debajo; los vectores, encima
const WORLD_SOURCE = 'world-cases';
const WORLD_DOT = 'world-cases-dot';
const WORLD_LABEL = 'world-cases-label';
const SWIPE_SOURCE = 'swipe-img';
const SWIPE_LINE_SOURCE = 'swipe-line';
const WORLD_VIEW = { center: [-20, 20], zoom: 1.5, pitch: 0, bearing: 0 };
const MAX_CACHE = 60;

const vid = (v) => `vec:${v.id}`;
const extentCoords = ([w, s, e, n]) => [[w, n], [e, n], [e, s], [w, s]];
const redact = (s) => String(s || '').replace(/access_token=[^&\s"')]+/g, 'access_token=…');

export class MapController {
  constructor({ container, token, onStatus }) {
    mapboxgl.accessToken = token;
    this.onStatus = onStatus || (() => {});
    this.onFeature = () => {};
    this.onCasePick = () => {};
    this.onDatasetState = () => {};
    this.current = null;
    this.cases = [];
    this.time = null;
    this.datasets = new Map(); // id → estado de ejecución del dataset (el JSON del caso no se modifica)
    this.visibleVec = new Set();
    this.cache = new Map(); // frames ya renderizados (canvas + url)
    this.swipe = null;
    this.errSeen = new Map();

    this.basemap = 'satellite';
    this.mode = 'globe';
    this.exaggeration = 1.5;
    this.sunAzimuth = 315;
    this.hillshadeOn = true;

    this.map = new mapboxgl.Map({
      container, style: BASEMAPS[this.basemap].style,
      center: WORLD_VIEW.center, zoom: WORLD_VIEW.zoom, projection: 'globe',
    });
    this.map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-right');
    this.map.addControl(new mapboxgl.FullscreenControl(), 'top-right');
    this.map.addControl(new mapboxgl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    this.ready = new Promise((res) => this.map.once('load', res));
    this.map.on('style.load', () => this.#applyAll());
    this.map.on('error', (e) => this.#onError(e));
    this.map.on('click', (e) => this.#click(e));
    this.map.on('mousemove', (e) => {
      const ids = [...this.#vectorIds(), ...this.#worldIds()];
      const hit = ids.length && this.map.queryRenderedFeatures(e.point, { layers: ids }).length;
      this.map.getCanvas().style.cursor = hit ? 'pointer' : '';
    });
  }

  // ───────────── Errores: específicos, deduplicados y sin reintentos automáticos ─────────────
  #onError(e) {
    const err = e?.error || {};
    if (err.name === 'AbortError') return;
    const status = err.status;
    const sourceId = e?.sourceId || '';
    const detail = redact(err.message) || 'sin detalle';
    const key = `${sourceId}|${status}|${detail}`;
    const now = Date.now();
    if (now - (this.errSeen.get(key) || 0) < 15000) return; // evita inundar la interfaz con el mismo error
    this.errSeen.set(key, now);
    const http = status === 429 ? 'HTTP 429: el servidor limitó las peticiones; espera un momento y reintenta'
      : status ? `HTTP ${status}` : detail;

    if (sourceId.startsWith('ds:')) {
      if (status === 404) return; // teselas fuera de cobertura: normal
      const st = this.datasets.get(sourceId.split(':')[1]);
      if (st) this.#emit(st, 'error', `No cargó: ${http}`);
      return;
    }
    if (sourceId === DEM_SOURCE) {
      this.onStatus('warn', `Relieve (DEM) no disponible: ${http}.`, 'retry-base');
      return;
    }
    this.onStatus('error', `Mapa base: ${http}${sourceId ? ` (fuente ${sourceId})` : ''}.`, status === 429 ? 'retry-base' : null);
  }

  // ───────────── Capas propias (se reaplican tras cada cambio de estilo) ─────────────
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
      m.addSource(DEM_SOURCE, { type: 'raster-dem', url: 'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize: 512, maxzoom: 14 });
    }
    // Sombreado: TERRENO BASE, no es dato SAR. minzoom evita pedir teselas DEM de todo el planeta en la vista de globo.
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

  #addWorldMarkers() {
    const m = this.map;
    if (m.getSource(WORLD_SOURCE)) return;
    m.addSource(WORLD_SOURCE, { type: 'geojson', data: this.#worldData() });
    m.addLayer({ id: WORLD_DOT, type: 'circle', source: WORLD_SOURCE, paint: { 'circle-radius': 7, 'circle-color': '#FFFFFF', 'circle-stroke-color': '#E4572E', 'circle-stroke-width': 3 } });
    m.addLayer({
      id: WORLD_LABEL, type: 'symbol', source: WORLD_SOURCE,
      layout: { 'text-field': ['get', 'title'], 'text-size': 12, 'text-offset': [0, 1.4], 'text-anchor': 'top', 'text-max-width': 12 },
      paint: { 'text-color': '#1B2430', 'text-halo-color': '#FFFFFF', 'text-halo-width': 1.6 },
    });
  }

  #worldData() {
    return {
      type: 'FeatureCollection',
      features: this.cases.map((c) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: c.view.center }, properties: { id: c.id, title: c.title } })),
    };
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

  // ───────────── Casos ─────────────
  clear() {
    const m = this.map;
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

  async loadCase(def) {
    await this.ready;
    this.clear();
    this.current = def;
    this.time = def.axis[0]?.ms ?? null;
    this.visibleVec = new Set(def.vectors.filter((v) => v.defaultVisible !== false).map((v) => v.id));
    def.datasets.forEach((d) => {
      this.datasets.set(d.id, {
        def: d, visible: d.defaultVisible !== false, opacity: d.render.opacity,
        selection: d.variables[0].id, frame: null, status: 'none', error: null, req: 0, tileKeys: new Set(),
      });
    });
    this.#addVectors();
    this.#applyVectorTime();
    this.#updateRasters();
    this.flyToCase();
  }

  async showWorld() {
    await this.ready;
    this.clear();
    this.map.flyTo({ ...WORLD_VIEW, duration: 2200, essential: true });
    if (this.mode === 'terrain') this.setMode('globe');
  }

  flyToCase() {
    if (!this.current) return this.map.flyTo({ ...WORLD_VIEW, duration: 1800 });
    const { center, zoom = 10, pitch = 0, bearing = 0 } = this.current.view;
    const tilt = this.mode === 'terrain' ? Math.max(pitch, 55) : this.mode === 'flat' ? 0 : Math.min(pitch, 20);
    this.map.flyTo({ center, zoom, pitch: tilt, bearing, duration: 3200, curve: 1.6, essential: true });
  }

  // ───────────── Vectores (GeoJSON), independientes de los rásteres ─────────────
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

  // ───────────── Tiempo y rásteres por frame ─────────────
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
    const missing = this.#missingVariable(st, frame);
    if (missing) { this.#hideDataset(st); return this.#emit(st, 'novar', `La variable ${missing} no viene en este frame`); }
    if (!st.visible || this.swipe?.st === st) { this.#hideDataset(st); return this.#emit(st, 'ok'); }
    try {
      if (st.def.source.type === 'image') {
        const r = await this.#rendered(st, frame, st.selection);
        if (req !== st.req) return;
        this.#showImage(st, r.url);
      } else {
        this.#showTiles(st, frame);
      }
      if (req === st.req) this.#emit(st, 'ok');
    } catch (err) {
      if (req === st.req) { this.#hideDataset(st); this.#emit(st, 'error', `No cargó: ${err.message}`); }
    }
  }

  /** Imagen coloreada de un frame (con caché). Con encoding "rgba" se usa el archivo tal cual. */
  async #rendered(st, frame, sel) {
    const key = `${st.def.id}|${frame.id}|${sel}`;
    if (!this.cache.has(key)) {
      const d = st.def;
      const nodata = d.source.nodata ?? 0;
      const job = (async () => {
        if (d.source.encoding === 'rgba') return { canvas: null, url: resolveUrl(frame.files[sel]) };
        const comp = d.composites.find((c) => c.id === sel);
        let canvas;
        if (comp) {
          const grays = {};
          for (const ch of ['r', 'g', 'b']) grays[ch] = comp.channels[ch] ? await loadGray(resolveUrl(frame.files[comp.channels[ch]])) : null;
          canvas = composeRgb(grays, nodata);
        } else {
          const v = d.variables.find((x) => x.id === sel);
          canvas = colorizeGray(await loadGray(resolveUrl(frame.files[sel])), v.legend.colors, nodata);
        }
        return { canvas, url: canvas.toDataURL('image/png') };
      })();
      this.cache.set(key, job);
      job.catch(() => this.cache.delete(key));
      if (this.cache.size > MAX_CACHE) this.cache.delete(this.cache.keys().next().value);
    }
    return this.cache.get(key);
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
      m.getSource(id).updateImage({ url, coordinates }); // reutiliza fuente y capa: no se duplican
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

  /** Precarga el siguiente frame (colorizado o capa de teselas oculta) para una reproducción fluida. */
  prefetch(ms) {
    for (const st of this.datasets.values()) {
      const f = frameAt(st.def, ms);
      if (!f || f === st.frame || this.#missingVariable(st, f)) continue;
      if (st.def.source.type === 'image') this.#rendered(st, f, st.selection).catch(() => {});
      else this.#ensureTileLayer(st, f, st.selection);
    }
  }

  setDatasetVisible(id, on) { const st = this.datasets.get(id); st.visible = on; return this.#applyDataset(st); }
  setDatasetSelection(id, sel) { const st = this.datasets.get(id); st.selection = sel; return this.#applyDataset(st); }
  retryDataset(id) { const st = this.datasets.get(id); this.errSeen.clear(); this.#removeDatasetLayers(st); return this.#applyDataset(st); }
  setDatasetOpacity(id, value) {
    const st = this.datasets.get(id);
    st.opacity = value;
    [`ds:${id}`, ...st.tileKeys].forEach((l) => { if (this.map.getLayer(l)) this.map.setPaintProperty(l, 'raster-opacity', value); });
    if (this.swipe?.st === st && this.map.getLayer(SWIPE_SOURCE)) this.map.setPaintProperty(SWIPE_SOURCE, 'raster-opacity', value);
  }

  // ───────────── Comparador antes/después (swipe) ─────────────
  // Se divide la ZONA (columnas de la imagen), no la pantalla: funciona con globo, terreno y rotación.
  async setSwipe(opts) {
    if (!opts) {
      const st = this.swipe?.st;
      this.swipe = null;
      this.#removeSwipeLayers();
      if (st) await this.#applyDataset(st);
      return;
    }
    const st = this.datasets.get(opts.datasetId);
    const fb = st.def.frames.find((f) => f.id === opts.beforeId);
    const fa = st.def.frames.find((f) => f.id === opts.afterId);
    const [rb, ra] = await Promise.all([this.#rendered(st, fb, opts.selection), this.#rendered(st, fa, opts.selection)]);
    if (!rb.canvas || !ra.canvas) throw new Error('El swipe requiere un ráster local (image + gray8)');
    this.swipe = { st, cb: rb.canvas, ca: ra.canvas, split: opts.split ?? 0.5 };
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
    const f = sx / W; // fracción cuantizada: el corte de la imagen y la línea coinciden
    const out = document.createElement('canvas');
    out.width = W;
    out.height = H;
    const ctx = out.getContext('2d');
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

  // ───────────── Valor de los rásteres en un punto ─────────────
  async sampleAt({ lng, lat }) {
    const out = [];
    for (const st of this.datasets.values()) {
      const d = st.def;
      if (!st.visible || !st.frame || !d.extent) continue;
      const [w, s, e, n] = d.extent;
      if (lng < w || lng > e || lat < s || lat > n) continue;
      const base = { name: d.name, frame: st.frame, status: d.status };
      if (d.source.type !== 'image' || d.source.encoding !== 'gray8') { out.push({ ...base, values: null }); continue; }
      const comp = d.composites.find((c) => c.id === st.selection);
      const ids = comp ? [...new Set(Object.values(comp.channels).filter(Boolean))] : [st.selection];
      const values = [];
      for (const id of ids) {
        const v = d.variables.find((x) => x.id === id);
        const file = st.frame.files[id];
        let value = null;
        if (file) {
          try {
            const g = await loadGray(resolveUrl(file));
            const px = Math.min(g.w - 1, Math.floor(((lng - w) / (e - w)) * g.w));
            const py = Math.min(g.h - 1, Math.floor(((n - lat) / (n - s)) * g.h));
            value = grayToValue(g.g[py * g.w + px], v.legend, d.source.nodata ?? 0);
          } catch { value = null; }
        }
        values.push({ label: v.label, unit: v.unit, value });
      }
      out.push({ ...base, values });
    }
    return out;
  }

  // ───────────── Base: mapa base, vista, relieve y luz ─────────────
  setBasemap(key) {
    if (!BASEMAPS[key] || key === this.basemap) return;
    this.basemap = key;
    this.map.setStyle(BASEMAPS[key].style); // 'style.load' reaplica terreno, cielo, vectores y rásteres
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
      'space-color': 'rgb(11, 11, 25)', 'star-intensity': this.basemap === 'light' ? 0 : 0.5,
    });
    if (m.getLayer(SKY_LAYER)) m.setLayoutProperty(SKY_LAYER, 'visibility', this.mode === 'terrain' ? 'visible' : 'none');
  }

  /** Reintento manual (nunca automático) del relieve tras un error o un 429. */
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

  // ───────────── Interacción ─────────────
  #click(e) {
    const m = this.map;
    const world = this.#worldIds();
    if (world.length) {
      const z = m.queryRenderedFeatures(e.point, { layers: world })[0];
      if (z) return this.onCasePick(z.properties.id);
    }
    const ids = this.#vectorIds();
    const f = ids.length ? m.queryRenderedFeatures(e.point, { layers: ids })[0] : null;
    const layer = f ? this.current.vectors.find((v) => vid(v) === f.layer.id) : null;
    this.onFeature(layer, f, e.lngLat);
  }
}
