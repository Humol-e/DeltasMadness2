# SAR Cambios · NASA Space Apps 2026 — "Dancing with the SARs"

Interactive viewer (Mapbox GL JS + Vite, vanilla JavaScript) for exploring surface changes with SAR layers.
**Current status: prototype with SYNTHETIC (DEMO) data.** It does not detect emergencies or use real-time data.

## Run
```bash
npm install
cp .env.example .env      # paste your public pk.* token into VITE_MAPBOX_ACCESS_TOKEN
npm run dev
```
`npm run demo-data` regenerates the demo data (requires Python 3).

## Structure
```
src/main.js               UI (case selector, slider, layers, legend, attributes panel)
src/lib/mapController.js  Mapbox: load case, step filtering, visibility, click handling
src/lib/registry.js       Case/layer registry and validation, GeoJSON loader
public/data/cases/        index.json + one folder per case (case.json + layers)
scripts/make_demo_data.py Synthetic demo data generator
```

## Case Format (`public/data/cases/<id>/case.json`)
```json
{
  "id": "my-case", "title": "Title", "description": "Text",
  "view": { "center": [lon, lat], "zoom": 11, "pitch": 0, "bearing": 0 },
  "timeline": [ { "label": "Before", "date": "2026-08-01", "note": "…" }, { "label": "After", "date": "2026-08-27" } ],
  "layers": [ { ...layer } ]
}
```
Layer: `id, name, type (fill|line|circle|raster), source, paint, description, popupFields, legend, attribution, demo`.
- `source`: `{ "type": "geojson", "data": "data/cases/my-case/x.geojson" }` or `{ "type": "raster", "tiles": ["https://…/{z}/{x}/{y}.png"], "tileSize": 256 }`.
- `time`: `{ "property": "step", "mode": "exact" | "cumulative" }`. Each feature includes `properties.step` (`timeline` index). `exact` = only that step (before/after); `cumulative` = up to that step (fire spread).
- `steps`: `[0, 2]` shows the layer only at those steps (useful for one raster per date).
- `popupFields`: `[["key", "Label", "unit"]]`.

## Adding a New Case
1. Create `public/data/cases/my-case/` with `case.json` and your `.geojson` files.
2. Add `"data/cases/my-case/case.json"` to `public/data/cases/index.json`.
3. Reload. If anything is missing or fails, the UI reports it while the rest keeps working.

## Adding a New Layer
Add an object to `layers` in `case.json` (see above). From code: `registerCase(def)` in `registry.js`.

## Connecting a SAR Pipeline (Legacy Hookup Notes)
The pipeline writes files matching this format:
- Vectors (InSAR velocity points, change polygons, perimeters) -> GeoJSON with `properties.step`.
- Raster (σ0 HH/HV/VV backscatter, coherence, interferograms, change maps) -> XYZ tiles (COG + titiler, or static tiles) referenced as `source.type = "raster"`.

## Real Data Needed for Scientific Validity
- Sentinel-1 / NISAR / SAOCOM / RADARSAT via ASF Vertex or NASA Earthdata (verify NISAR availability and dates for your area).
- InSAR: interferometric pairs (e.g., ASF HyP3), coherence, line-of-sight (LOS) displacement series, atmospheric and DEM corrections.
- Change: calibrated σ0 (γ0/σ0 in dB), speckle filtering, multi-date ratio/difference, polarization comparison.
- Wildfires: thermal hotspots (NASA FIRMS) for validation; SAR delineates burn scars rather than measuring heat.
- Context: optical imagery, DEM, glacier/landslide inventories.

## Limitations
- All included data is synthetic. `null` dates = undefined.
- The Langtang case is illustrative; verify the Aug 26, 2026 event against primary sources before citing it.
- Before/after works via discrete steps (no dual-map swipe or raster blending yet in this demo).
- No backend, no authentication, no real-time alerts.
