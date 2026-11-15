import mapboxgl from 'mapbox-gl';

const STYLE = 'mapbox://styles/mapbox/satellite-streets-v12';

export class MapController {
  constructor({ container, token, onStatus }) {
    mapboxgl.accessToken = token;
    this.onStatus = onStatus || (() => {});
    this.onFeature = () => {};
    this.current = null;
    this.step = 0;
    this.visible = new Set();
    this.caseSeq = 0;

    this.map = new mapboxgl.Map({
      container, style: STYLE, center: [0, 20], zoom: 1.6, projection: 'mercator',
    });
    this.map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-right');
    this.map.addControl(new mapboxgl.FullscreenControl(), 'top-right');
    this.map.addControl(new mapboxgl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    this.ready = new Promise((res) => this.map.once('load', res));
    this.map.on('error', (e) => this.onStatus('error', e?.error?.message || 'Map error'));
    this.map.on('click', (e) => this.#click(e));
    this.map.on('mousemove', (e) => {
      const ids = this.#activeIds();
      const hit = ids.length && this.map.queryRenderedFeatures(e.point, { layers: ids }).length;
      this.map.getCanvas().style.cursor = hit ? 'pointer' : '';
    });
  }

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
    for (const l of def.layers) {
      if (l.status !== 'ok') continue;
      const id = this.#mid(l);
      try {
        const source = l.source.type === 'geojson' ? { type: 'geojson', data: l.data } : l.source;
        this.map.addSource(id, source);
        this.map.addLayer({ id, type: l.type, source: id, paint: l.paint || {}, layout: l.layout || {} });
      } catch (err) {
        l.status = 'error';
        l.error = err.message;
      }
    }
    this.setStep(0);
    this.home(false);
  }

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

  home(animate = true) {
    if (!this.current) return;
    const { center, zoom = 10, pitch = 0, bearing = 0 } = this.current.view;
    const opts = { center, zoom, pitch, bearing };
    animate ? this.map.flyTo({ ...opts, duration: 1400 }) : this.map.jumpTo(opts);
  }

  #click(e) {
    if (!this.current) return;
    const ids = this.#activeIds();
    const f = ids.length ? this.map.queryRenderedFeatures(e.point, { layers: ids })[0] : null;
    if (!f) return this.onFeature(null, null);
    const layer = this.current.layers.find((l) => this.#mid(l) === f.layer.id);
    this.onFeature(layer, f);
  }
}
