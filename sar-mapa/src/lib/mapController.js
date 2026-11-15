import mapboxgl from 'mapbox-gl';

export const BASEMAPS = {
  satellite: { label: 'Satellite', style: 'mapbox://styles/mapbox/satellite-streets-v12' },
  light: { label: 'Light', style: 'mapbox://styles/mapbox/light-v11' },
  streets: { label: 'Streets', style: 'mapbox://styles/mapbox/streets-v12' },
};

export const VIEW_MODES = { globe: '3D Globe', flat: 'Flat map', terrain: '3D Terrain' };

const DEM_SOURCE = 'base-dem';
const HILLSHADE_LAYER = 'base-hillshade';
const SKY_LAYER = 'base-sky';
const WORLD_SOURCE = 'world-cases';
const WORLD_DOT = 'world-cases-dot';
const WORLD_LABEL = 'world-cases-label';
const WORLD_VIEW = { center: [-20, 20], zoom: 1.5, pitch: 0, bearing: 0 };

export class MapController {
  constructor({ container, token, onStatus }) {
    mapboxgl.accessToken = token;
    this.onStatus = onStatus || (() => {});
    this.onFeature = () => {};
    this.onCasePick = () => {};
    this.current = null;
    this.step = 0;
    this.visible = new Set();
    this.cases = [];
    this.caseSeq = 0;

    this.basemap = 'satellite';
    this.mode = 'globe';
    this.exaggeration = 1.5;
    this.sunAzimuth = 315;
    this.hillshadeOn = true;

    this.map = new mapboxgl.Map({
      container,
      style: BASEMAPS[this.basemap].style,
      center: WORLD_VIEW.center,
      zoom: WORLD_VIEW.zoom,
      projection: 'globe',
    });
    this.map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-right');
    this.map.addControl(new mapboxgl.FullscreenControl(), 'top-right');
    this.map.addControl(new mapboxgl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    this.ready = new Promise((res) => this.map.once('load', res));
    // Re-add custom sources and layers whenever the basemap style reloads.
    this.map.on('style.load', () => this.#applyAll());
    this.map.on('error', (e) => this.onStatus('error', e?.error?.message || 'Map error'));
    this.map.on('click', (e) => this.#click(e));
    this.map.on('mousemove', (e) => {
      const ids = [...this.#activeIds(), ...this.#worldIds()];
      const hit = ids.length && this.map.queryRenderedFeatures(e.point, { layers: ids }).length;
      this.map.getCanvas().style.cursor = hit ? 'pointer' : '';
    });
  }

  // ── Helpers ──
  #mid(layer) { return `${this.current.id}:${layer.id}`; }
  #isShown(layer) {
    return this.visible.has(layer.id) && (!layer.steps || layer.steps.includes(this.step));
  }
  #activeIds() {
    if (!this.current) return [];
    return this.current.layers
      .filter((l) => l.status === 'ok' && this.#isShown(l) && this.map.getLayer(this.#mid(l)))
      .map((l) => this.#mid(l)).reverse();
  }
  #worldIds() { return this.map.getLayer(WORLD_DOT) ? [WORLD_DOT] : []; }
  #firstSarLayerId() {
    if (!this.current) return undefined;
    const l = this.current.layers.find((x) => this.map.getLayer(this.#mid(x)));
    return l ? this.#mid(l) : undefined;
  }

  // ── Reapply custom state (terrain, sky, study-area markers, and SAR layers) ──
  #applyAll() {
    this.#addTerrainBase();
    this.#addWorldMarkers();
    this.#addSarLayers();
    this.#applyMode();
    this.setStep(this.step);
  }

  #addTerrainBase() {
    const m = this.map;
    if (!m.getSource(DEM_SOURCE)) {
      m.addSource(DEM_SOURCE, { type: 'raster-dem', url: 'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize: 512, maxzoom: 14 });
    }
    // Hillshade relief: belongs to the BASE TERRAIN, not SAR data.
    if (!m.getLayer(HILLSHADE_LAYER)) {
      m.addLayer({
        id: HILLSHADE_LAYER, type: 'hillshade', source: DEM_SOURCE,
        paint: {
          'hillshade-exaggeration': 0.5,
          'hillshade-illumination-direction': this.sunAzimuth,
          'hillshade-shadow-color': '#1B2430',
          'hillshade-highlight-color': '#FFFFFF',
        },
        layout: { visibility: this.hillshadeOn ? 'visible' : 'none' },
      }, this.#firstSarLayerId());
    }
    if (!m.getLayer(SKY_LAYER)) {
      m.addLayer({
        id: SKY_LAYER, type: 'sky',
        paint: {
          'sky-type': 'atmosphere',
          'sky-atmosphere-sun': this.#sunPosition(),
          'sky-atmosphere-sun-intensity': 8,
        },
      });
    }
  }

  #sunPosition() { return [this.sunAzimuth, 60]; } // [azimuth, polar altitude]

  #addWorldMarkers() {
    const m = this.map;
    if (m.getSource(WORLD_SOURCE)) return;
    m.addSource(WORLD_SOURCE, {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: this.cases.map((c) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: c.view.center },
          properties: { id: c.id, title: c.title },
        })),
      },
    });
    m.addLayer({
      id: WORLD_DOT, type: 'circle', source: WORLD_SOURCE,
      paint: { 'circle-radius': 7, 'circle-color': '#FFFFFF', 'circle-stroke-color': '#E4572E', 'circle-stroke-width': 3 },
    });
    m.addLayer({
      id: WORLD_LABEL, type: 'symbol', source: WORLD_SOURCE,
      layout: { 'text-field': ['get', 'title'], 'text-size': 12, 'text-offset': [0, 1.4], 'text-anchor': 'top', 'text-max-width': 12 },
      paint: { 'text-color': '#1B2430', 'text-halo-color': '#FFFFFF', 'text-halo-width': 1.6 },
    });
  }

  #addSarLayers() {
    if (!this.current) return;
    const m = this.map;
    for (const l of this.current.layers) {
      if (l.status !== 'ok') continue;
      const id = this.#mid(l);
      if (m.getLayer(id)) continue;
      try {
        if (!m.getSource(id)) {
          m.addSource(id, l.source.type === 'geojson' ? { type: 'geojson', data: l.data } : l.source);
        }
        m.addLayer({ id, type: l.type, source: id, paint: l.paint || {}, layout: l.layout || {} }, WORLD_DOT);
      } catch (err) {
        l.status = 'error';
        l.error = err.message;
      }
    }
  }

  // ── Study cases / areas ──
  setCases(cases) {
    this.cases = cases;
    const src = this.map.getSource(WORLD_SOURCE);
    if (src) {
      src.setData({
        type: 'FeatureCollection',
        features: cases.map((c) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: c.view.center }, properties: { id: c.id, title: c.title } })),
      });
    }
  }

  clear() {
    if (!this.current) return;
    for (const l of this.current.layers) {
      const id = this.#mid(l);
      if (this.map.getLayer(id)) this.map.removeLayer(id);
      if (this.map.getSource(id)) this.map.removeSource(id);
    }
    this.current = null;
    this.map.getCanvas().style.cursor = '';
  }

  async loadCase(def) {
    const seq = ++this.caseSeq;
    await this.ready;
    if (seq !== this.caseSeq) return;
    this.clear();
    this.current = def;
    this.step = 0;
    this.visible = new Set(def.layers.filter((l) => l.defaultVisible !== false).map((l) => l.id));
    this.#addSarLayers();
    this.setStep(0);
    this.flyToCase();
  }

  /** Returns to the full globe and clears the active case. */
  async showWorld() {
    const seq = ++this.caseSeq;
    await this.ready;
    if (seq !== this.caseSeq) return;
    this.clear();
    this.step = 0;
    this.map.flyTo({ ...WORLD_VIEW, duration: 2200, essential: true });
    if (this.mode === 'terrain') this.setMode('globe');
  }

  flyToCase() {
    if (!this.current) return this.map.flyTo({ ...WORLD_VIEW, duration: 1800 });
    const { center, zoom = 10, pitch = 0, bearing = 0 } = this.current.view;
    // In 3D terrain use the case tilt; in globe/flat view from above.
    const tilt = this.mode === 'terrain' ? Math.max(pitch, 55) : this.mode === 'flat' ? 0 : Math.min(pitch, 20);
    this.map.flyTo({ center, zoom, pitch: tilt, bearing, duration: 3200, curve: 1.6, essential: true });
  }

  // ── Timeline steps and SAR layer visibility ──
  setStep(step) {
    this.step = step;
    if (!this.current) return;
    for (const l of this.current.layers) {
      const id = this.#mid(l);
      if (l.status !== 'ok' || !this.map.getLayer(id)) continue;
      if (l.time && l.type !== 'raster') {
        const { property, mode } = l.time;
        this.map.setFilter(id, [mode === 'cumulative' ? '<=' : '==', ['get', property], step]);
      }
      this.map.setLayoutProperty(id, 'visibility', this.#isShown(l) ? 'visible' : 'none');
    }
  }

  setVisible(layerId, on) {
    if (!this.current) return;
    on ? this.visible.add(layerId) : this.visible.delete(layerId);
    this.setStep(this.step);
  }

  isVisible(layerId) { return this.visible.has(layerId); }

  // ── Basemap, view mode, terrain relief, and sunlight ──
  setBasemap(key) {
    if (!BASEMAPS[key] || key === this.basemap) return;
    this.basemap = key;
    this.map.setStyle(BASEMAPS[key].style); // 'style.load' reapplies terrain, sky, and SAR layers
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
    if (!m.isStyleLoaded() && !m.getSource(DEM_SOURCE)) return;
    m.setProjection(this.mode === 'globe' ? 'globe' : 'mercator');
    if (this.mode === 'terrain') {
      m.setTerrain({ source: DEM_SOURCE, exaggeration: this.exaggeration });
    } else {
      m.setTerrain(null);
    }
    m.setFog(this.mode === 'flat' ? null : {
      color: 'rgb(186, 210, 235)', 'high-color': 'rgb(36, 92, 223)', 'horizon-blend': 0.03,
      'space-color': 'rgb(11, 11, 25)', 'star-intensity': this.basemap === 'light' ? 0 : 0.5,
    });
    if (m.getLayer(SKY_LAYER)) m.setLayoutProperty(SKY_LAYER, 'visibility', this.mode === 'terrain' ? 'visible' : 'none');
  }

  setExaggeration(value) {
    this.exaggeration = value;
    if (this.mode === 'terrain' && this.map.getSource(DEM_SOURCE)) {
      this.map.setTerrain({ source: DEM_SOURCE, exaggeration: value });
    }
  }

  setSunAzimuth(deg) {
    this.sunAzimuth = ((deg % 360) + 360) % 360;
    const m = this.map;
    if (m.getLayer(HILLSHADE_LAYER)) m.setPaintProperty(HILLSHADE_LAYER, 'hillshade-illumination-direction', this.sunAzimuth);
    if (m.getLayer(SKY_LAYER)) m.setPaintProperty(SKY_LAYER, 'sky-atmosphere-sun', this.#sunPosition());
  }

  setHillshade(on) {
    this.hillshadeOn = on;
    if (this.map.getLayer(HILLSHADE_LAYER)) this.map.setLayoutProperty(HILLSHADE_LAYER, 'visibility', on ? 'visible' : 'none');
  }

  // ── Interaction ──
  #click(e) {
    const m = this.map;
    // 1) Click on a globe marker -> select that case
    const world = this.#worldIds();
    if (world.length) {
      const z = m.queryRenderedFeatures(e.point, { layers: world })[0];
      if (z) return this.onCasePick(z.properties.id);
    }
    // 2) Click on SAR data -> attribute panel
    const ids = this.#activeIds();
    const f = ids.length ? m.queryRenderedFeatures(e.point, { layers: ids })[0] : null;
    if (!f) return this.onFeature(null, null);
    const layer = this.current.layers.find((l) => this.#mid(l) === f.layer.id);
    this.onFeature(layer, f);
  }
}
