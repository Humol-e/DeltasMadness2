# Wildfire Monitor (GeoPulse -> sar-platform / sar-Madness)

Wildfire ingestion, analysis, and conversion module based on [geopulse](../geopulse), structured alongside [hola](../hola) so that the web viewer ([sar-Madness](../sar-Madness/sar-Madness) / `sar-platform`) can load its outputs directly while preserving original colors and visual fidelity.

## Project structure

```text
incendios/
├── README.md
├── GUIA_DATOS.md
├── pyproject.toml
├── requirements.txt
├── scripts/
│   └── stage1_cli.py
├── src/
│   └── wildfire_monitor/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── pc_auth.py
│       ├── cli.py
│       └── ingestion/
│           ├── __init__.py
│           ├── native_catalog.py
│           ├── geopulse_bridge.py
│           ├── platform_exporter.py
│           ├── pipeline.py
│           └── sar_burn.py
├── tests/
│   ├── test_config.py
│   ├── test_pc_auth.py
│   ├── test_native_catalog.py
│   ├── test_geopulse_bridge.py
│   ├── test_platform_exporter.py
│   ├── test_sar_burn.py
│   └── test_stage1_pipeline.py
└── data/
    ├── downloads/   # Single-band PIL-compatible GeoTIFFs + catalog.json
    └── cases/       # case.json + GeoJSON + XYZ tiles + index.json
```

## How GeoPulse data is adapted for `sar-platform` / `sar-Madness`

1. **GeoTIFFs and `catalog.json` (`data/downloads/`):**
   - `sar-platform` reads local GeoTIFFs through `Pillow` and metadata from `catalog.json`.
   - `wildfire_monitor` decodes Microsoft Planetary Computer STAC scenes (`sentinel-2-l2a`, `sentinel-1-rtc`, `cop-dem-glo-30`) or compressed GeoPulse COG folders and writes uncompressed single-band `EPSG:4326` GeoTIFFs with standard GeoKeys (`34735`, `33550`, `33922`, `42113`) plus `catalog.json`.
2. **Cases (`case.json`) and XYZ tiles preserving exact visual appearance (`data/cases/`):**
   - Each wildfire generates `case.json`, `aoi.geojson`, `burn_extent.geojson`, `summary.json`, `provenance.json`, `layers/*.png`, and Web Mercator tiles `tiles/<layer>/{z}/{x}/{y}.png`.
   - All GeoPulse visual palettes are preserved pixel-for-pixel:
     - **Burn probability:** GeoPulse gradient `#eb461e` -> `#c8140a`.
     - **dNBR burn severity (Key & Benson, 2006):** Low `#7fffd4`, Moderate-low `#ffff00`, Moderate-high `#ff8c00`, High `#ff0000`.
     - **Multimodal change probability:** `#ffaa00` -> `#ff7800`.
     - **Uncertainty:** `#3c145a` -> `#e6505a` -> `#ffd23c`.
     - **Burn perimeter:** `#ffd60a` yellow outline with `#000000` casing.

## Main commands

```powershell
# List preconfigured GeoPulse wildfire presets
wildfire-stage1 list-aois
# Or run directly without installing the script entrypoint:
py scripts/stage1_cli.py list-aois

# Check dependencies, local paths, and STAC API connectivity
wildfire-stage1 doctor

# Inspect available Planetary Computer scenes
wildfire-stage1 check-native --aoi palisades --start-date 2024-12-01 --end-date 2025-03-31

# Process and convert the 3 core GeoPulse wildfire cases (Palisades, Camp Fire, Martin NV)
wildfire-stage1 run-wildfire --aoi all

# Convert an existing geopulse output folder
wildfire-stage1 convert-geopulse ../geopulse/outputs/my_wildfire

# Export Schema v2 cases + gray8 rasters + GeoTIFFs into sar-Madness
wildfire-stage1 publish-madness

# Derive all-weather SAR-only burn & OPERA DIST-S1 layers (VV/VH or NISAR HH/HV)
wildfire-stage1 sar-burn --case all --polarization VV_VH
```

## All-Weather Pure-SAR Burn & Surface Disturbance (`sar_burn.py`)

Alongside the Sentinel-2 + Sentinel-1 optical fusion baseline, `wildfire_monitor.ingestion.sar_burn` implements a **pure-SAR burned-area and disturbance detector** (operating under dense smoke or cloud cover across both **Sentinel-1 `VV/VH`** and **NISAR `HH/HV`**):

1. **NASA OPERA `DIST-S1` / `RTC` normalization:** smooths pre/post backscatter in linear power space (`5×5` box filter), standardizes `RBD` and `LogRBR` via scene median/MAD, and computes a dual-polarization Mahalanobis disturbance z-score (`opera_dist`, `[0, 4]`) gated by pre-fire canopy backscatter.
2. **Radar burn & vegetation indices:** computes `RBD`, depolarized `LogRBR`, `ΔRVI`, `ΔDPSVI`, `ΔRFDI`, `5×5` GLCM contrast change, and the dual-pol covariance test statistic `-2 ln Q` (`mortcanty/eesarseq`).
3. **Unsupervised K-means clustering:** quantile-seeded 4-cluster K-means ordered into discrete classes (`0` unburned/outside domain, `1` low disturbance, `2` moderate burn, `3` high burn), exporting `sar_burn_extent.geojson` and 3 GeoTIFFs per wildfire case.

## Automated tests

```powershell
py -m pytest -v
```
