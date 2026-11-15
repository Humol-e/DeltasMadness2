# SAR Changes · NASA Space Apps 2026 — "Dancing with the SARs"

Interactive 3D geospatial viewer (Mapbox GL JS + Vite, vanilla JavaScript) with 3D globe/terrain relief, dated satellite rasters, interactive Before/After swipe comparator, pixel readout, PNG report downloads, and GeoJSON overlays backed 100% by real satellite observations.

## Run

```bash
npm install
cp .env.example .env      # Optional: public pk.* token in VITE_MAPBOX_ACCESS_TOKEN (in .gitignore; uses Esri fallback without token)
npm run validate          # Validate catalog.json and all real case files (also runs before build)
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
src/lib/placeSearch.js       Offline case/preset search, WGS84 coordinate parser, and geocoding.
src/lib/registry.js          Catalog and case loader.
src/main.js, style.css       Interactive UI and controls.
public/data/catalog.json     Published real-data cases registry.
public/data/cases/<id>/      case.json + rasters/*.png + geotiffs/*.tif + *.geojson
docs/                        SCHEMA.md, case.example.jsonc, case.template.json
```

See [docs/SCHEMA.md](./docs/SCHEMA.md) for full Schema v2 documentation.

## Published Cases

Six real-data Schema v2 cases are prepackaged under `public/data/cases/`:

- **Real Wildfire Events (`category: "wildfire"`):** `wildfire-camp-paradise`, `wildfire-martin-nv`, `wildfire-palisades-la`
- **Real Glacier & Snowmelt (`category: "glacier"`):** `glacier-langtang`, `glacier-khumbu`
- **NISAR Forest Biomass (`category: "biomass"`):** `biomass-leno-nisar`

Each case is validated by `npm run validate` and verified by `node --test tests/placeSearch.test.mjs`.

## Performance & Error Handling

- One Mapbox source and layer per raster dataset (`updateImage` reuses sources when changing dates).
- Shared bounded LRU caches in `rasterCanvas.js` reuse decoded `gray8`/`rgba` rasters and colorized canvases across sidebar previews, map display, swipe comparison, and PNG exports.
- Hillshade DEM requests activate from zoom 5 upward to prevent global tile flooding in globe view.
- Basemap 429 rate-limits gracefully fall back to Esri World Imagery.

## Known Scope Notes

- Local image overlays stretch across their `[west, south, east, north]` extent box (exact for local AOI study regions delivered in `EPSG:4326`).
- Interactive swipe requires `image` + `gray8` or `rgba` local image layers.
