# SAR Changes · NASA Space Apps 2026 — "Dancing with the SARs"

Interactive 3D geospatial viewer (MapLibre GL JS out-of-the-box + optional Mapbox GL JS upgrade, Vite, vanilla JavaScript) with 3D globe/terrain relief, dated satellite rasters, interactive Before/After swipe comparator, pixel readout, PNG report downloads, GeoTIFF downloads, and GeoJSON overlays.

## Run

```bash
npm install
# Optional only if you prefer Mapbox styles + Mapbox geocoding over open MapLibre + Esri/OSM + Nominatim:
# cp .env.example .env
npm run validate          # Validate catalog.json and all case files (also runs before build)
npm run dev               # Start Vite dev server
npm run build             # Validate catalog and build production bundle in dist/
```

## Architecture

```text
src/lib/mapController.js     Map engine (globe, 3D terrain, layers, swipe). Decoupled from concrete cases.
src/lib/schema.js            Schema v2 validation, normalization, and JSON-with-comments parser.
src/lib/timeline.js          Time axis and latest-frame <= active-date lookup.
src/lib/rasterCanvas.js      LUT gray8 colorization, RGB composites, pixel values, LRU caching.
src/lib/beforeAfterExport.js Side-by-side Before/After comparison PNG generator & downloader.
src/lib/placeSearch.js       Offline case/preset search, WGS84 coordinate parser, and CLI command generator wired to #home-search-input.
src/lib/registry.js          Catalog and case loader.
src/lib/i18n.js              English/Spanish interface localization (defaults to English).
src/main.js, style.css       Interactive UI and controls.
public/data/catalog.json     Published cases registry.
public/data/cases/<id>/      case.json + rasters/*.png + geotiffs/*.tif + *.geojson
docs/                        SCHEMA.md, case.example.jsonc, case.template.json
```

See [docs/SCHEMA.md](./docs/SCHEMA.md) for full Schema v2 documentation.

## Published Cases

Six Schema v2 cases are prepackaged under `public/data/cases/`:

- **Wildfire Events (`category: "wildfire"`):** `wildfire-camp-paradise`, `wildfire-martin-nv`, `wildfire-palisades-la` (Sentinel-2 L2A + Sentinel-1 RTC + Copernicus DEM)
- **Glacier & Snowmelt (`category: "glacier"`):** `glacier-langtang`, `glacier-khumbu` (Sentinel-2 SR NDSI + Sentinel-1 GRD + MODIS MOD10A1/MOD11A1)
- **NISAR Forest Biomass (`category: "biomass"`):** `biomass-leno-nisar` (Official NASA pre-launch simulated NISAR L2 GCOV from UAVSAR/ALOS-2 processed with the L3 AGB ATBD solver + NLCD + PlanetScope validation)

Each case is validated by `npm run validate`, all 25 automated utility/UI/i18n tests pass via `node --test tests/*.test.mjs`, multi-year NISAR GCOV scenes (`2021–2024`) expose individual chronological frames (`d20210806`, `pre`, `post`, `d20240830`), and Cryosphere/Wildfire cases include SAR wet-snow (`sar_wetsnow`) and multi-band SAR burn (`sar_burn`) cross-validation layers.

## Performance & Error Handling

- One GL image source and layer per raster dataset (`updateImage` reuses sources when changing dates).
- Shared bounded LRU caches in `rasterCanvas.js` reuse decoded `gray8`/`rgba` rasters and colorized canvases across sidebar previews, map display, swipe comparison, and PNG exports.
- Hillshade DEM requests activate from zoom 5 upward to prevent global tile flooding in globe view.
- Runs out-of-the-box without any API token using MapLibre GL JS (3D globe, AWS Terrarium 3D terrain, Esri World Imagery / CARTO / OSM, and Nominatim search); automatically upgrades to Mapbox styles when a valid `VITE_MAPBOX_ACCESS_TOKEN` is set, and falls back cleanly on auth errors or HTTP 429 rate limits.

## Known Scope Notes

- Local image overlays stretch across their `[west, south, east, north]` extent box (exact for local AOI study regions delivered in `EPSG:4326`).
- Interactive swipe requires `image` + `gray8` or `rgba` local image layers.

## License

Licensed under the MIT License — see [`../../LICENSE`](../../LICENSE).
