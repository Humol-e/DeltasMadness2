# Glacier Monitoring (GEE + ASF HyP3) — Stage 1

Implementation of **Stage 1: Direct Google Earth Engine Ingestion (Native Catalog + ASF Bridge)**, ready to connect with downstream stages (SAR/DEM analysis engine, lightweight FastAPI/Flask backend, and 3D frontend).

For a step-by-step guide on interpreting each command's output and choosing InSAR pairs or OPERA-S1 time series, see [DATA_GUIDE.md](DATA_GUIDE.md) (mirrored in [GUIA_DATOS.md](GUIA_DATOS.md)).

### SAR Wet-Snow & Optical Cross-Validation (`sar-wetsnow`)
The `glacier_monitor.ingestion.sar_wetsnow` module computes Sentinel-1 C-band wet-snow masks (Nagler & Rott 2000 backscatter ratio) and cross-validates SAR subsurface liquid-water zones against optical Sentinel-2 post-event snow cover (`ndsi_post`), publishing `sarSubsurfaceWetSnowKm2` and `sarWetInOpticalSnowPct` (`81.2%` on Langtang, `83.4%` on Khumbu).

## Current Structure (Stage 1)

- `src/glacier_monitor/config.py`: `.env` configuration (`Settings`), bounding boxes, and glacier AOI presets (`langtang`, `khumbu`).
- `src/glacier_monitor/gee_auth.py`: Google Earth Engine and Google Cloud Storage initialization via Service Account JSON or default credentials (ADC / `earthengine authenticate`).
- `src/glacier_monitor/ingestion/native_catalog.py`: Connectors and availability/freshness checks for:
  - `COPERNICUS/S1_GRD` (Sentinel-1 SAR GRD)
  - `MODIS/061/MOD10A1` (Daily snow cover 500m)
  - `MODIS/061/MOD11A1` (Daily land surface temperature 1km)
- `src/glacier_monitor/ingestion/asf_bridge.py`: ASF catalog search (`asf_search` for `SENTINEL1`, `NISAR`, `UAVSAR`), ASF HyP3 job submission/status (`hyp3_sdk`), `.zip` extraction to GeoTIFFs, and optional adapter for `gee-community/sar-asf-to-gee`.
- `src/glacier_monitor/ingestion/gee_uploader.py`: Upload GeoTIFFs to Google Cloud Storage and register them as GEE Assets (`ee.data.startIngestion` + equivalent `earthengine upload image` command generation).
- `src/glacier_monitor/ingestion/pipeline.py` & `scripts/stage1_cli.py`: Full orchestration and CLI interface.
- `src/glacier_monitor/ingestion/sar_wetsnow.py`: SAR wet-snow product. Multitemporal backscatter-ratio classifier (dry-snow reference vs. melt window, wet snow when both `VV` and `VH` drop by ≤ -2 dB inside the optical snow mask), PNG/GeoTIFF/GeoJSON writers and the schema-v2 dataset/vector definitions. Used automatically by `publish-madness` and, without GEE, by `sar-wetsnow`.

## Quick Setup

1. Copy `.env.example` to `.env` and fill in your Google Cloud / GEE and NASA Earthdata credentials:
   ```powershell
   Copy-Item .env.example .env
   ```
2. (Optional) If you want to use the community `gee-community/sar-asf-to-gee` package with `--use-community-bridge`:
   ```powershell
   pip install git+https://github.com/gee-community/sar-asf-to-gee.git
   ```

## Stage 1 Commands

```powershell
# Derive the SAR wet-snow layer (Sentinel-1 VV/VH ratio) from the glacier cases already published in sar-Madness.
# Offline: reads each case's own Before/After PNGs, so no GEE/Earthdata credentials are needed.
py scripts/stage1_cli.py sar-wetsnow                       # langtang + khumbu
py scripts/stage1_cli.py sar-wetsnow --case glacier-khumbu --threshold-db -3
```

Interpretation: class `3` = wet snow (`VV` and `VH` both ≤ threshold), `2` = possible wet snow (one channel), `1` = stable snow. Values are qualitative: inputs are non-terrain-flattened `S1_GRD` median composites.

```powershell
# Verify configuration, credentials, and connectivity (GEE, GCS, ASF, HyP3)
py -m glacier_monitor doctor

# List preconfigured glaciers/valleys
py scripts/stage1_cli.py list-aois

# 1A. Check availability and available bands in GEE's native catalog
py scripts/stage1_cli.py check-native --aoi langtang --start 2026-09-01 --end 2026-10-15

# 1B. Search granules in NASA ASF (sentinel1, nisar, nisar_science, uavsar, opera_s1)
py scripts/stage1_cli.py asf-search --dataset sentinel1 --aoi langtang --start 2026-09-01 --end 2026-10-15 --processing-level SLC

# 1B (Local without GCS). Download direct ASF GeoTIFFs (e.g. OPERA-S1)
py -m glacier_monitor asf-download --granule <GRANULE_NAME> --suffix _VV.tif --suffix _VH.tif

# 1B. Submit an InSAR job to ASF HyP3 and check status
py scripts/stage1_cli.py hyp3-submit --job-type insar --name langtang-insar --granule <GRANULE_1> --granule <GRANULE_2>
py -m glacier_monitor hyp3-status --name langtang-insar

# 1B (Local without GCS). Download HyP3 GeoTIFFs and index them in data/downloads/catalog.json
py -m glacier_monitor hyp3-download --job-name langtang-insar --suffix _unw_phase.tif --suffix _coh.tif

# 1B (Optional with GCS). Dry-run (--dry-run) or ingest a HyP3 job -> GCS -> GEE
py scripts/stage1_cli.py asf-ingest --job-name langtang-insar --suffix _unw_phase.tif --suffix _coh.tif --dry-run

# 1B (Optional with GCS). Upload a local GeoTIFF (NISAR / UAVSAR / HyP3) -> GCS -> GEE Asset
py scripts/stage1_cli.py asf-ingest --local-file path/to/product.tif --sensor NISAR --wait

# Export real glacier melt cases to sar-Madness
py -m glacier_monitor publish-madness
```

## Tests

57 unit tests cover GEE configuration, ASF/HyP3 connectors, catalog schema v2, exporter idempotency, and optical-vs-SAR wet-snow cross-validation:

```powershell
py -B -m pytest -p no:cacheprovider -q
```
