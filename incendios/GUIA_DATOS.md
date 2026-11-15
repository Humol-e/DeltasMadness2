# Guía de Datos: Incendios GeoPulse → Programa Principal (`sar-platform`)

Esta guía describe cada archivo que genera el flujo de `incendios/` y cómo se conecta con `sar-platform` sin cambiar la interfaz ni alterar los colores originales de GeoPulse.

## 1. Casos pre-generados desde `geopulse/examples/`

- **`wildfire-palisades-la`** — *Palisades Fire — Los Angeles, CA (2025)* (`2024-12-01/2025-01-06` vs `2025-02-01/2025-03-31`)
- **`wildfire-camp-paradise`** — *Camp Fire — Paradise, CA (2018)* (`2018-09-15/2018-11-07` vs `2018-11-26/2019-01-31`)
- **`wildfire-martin-nv`** — *Martin Fire — sagebrush steppe, NV (2018)* (`2018-05-15/2018-07-04` vs `2018-07-25/2018-09-15`)

## 2. Formato en `data/downloads/` (catálogo GeoTIFF estilo `hola`)

Archivo principal: `data/downloads/catalog.json`

Cada entrada sigue exactamente el esquema de catálogo consumido por `sar-platform` (`publish-catalog` / `load_local_catalog`):

- `local_path`: ruta al `.tif` monobanda descomprimido en `EPSG:4326` compatible con `PIL.Image` y `load_geotiff_raster`.
- `asset_name` y `product_layer`:
  - `burn_probability`: probabilidad continua `[0, 1]` derivada de `dNBR`, `NDVI` de combustible y retrodispersión SAR `VH`.
  - `burn_severity`: clase discreta `uint8` Key & Benson (`1=low`, `2=moderate-low`, `3=moderate-high`, `4=high`, `0=sin daño`, `255=nodata`).
  - `change_probability`: cambio multimodal Sentinel-1 + Sentinel-2 `[0, 1]`.
  - `uncertainty`: entropía binaria e incertidumbre en zonas solo-SAR `[0, 1]`.
  - `s2_pre_nbr`, `s2_post_nbr`: índice normalizado de área quemada (`NBR`) previo y posterior.
  - `s1_pre_vh`, `s1_post_vh`: retrodispersión Sentinel-1 RTC polarización `VH` en decibeles (`dB`).
  - `dem_elevation`: elevación Copernicus DEM 30 m en metros.

## 3. Formato en `data/cases/` (visor 3D de `sar-platform`)

- `data/cases/index.json`: lista de casos registrados en formato `{"cases": ["data/cases/<id>/case.json", ...]}`.
- `data/cases/<id>/case.json`: definición de cámara (`view`), línea de tiempo (`timeline`: *Pre-incendio*, *Post-incendio (GeoPulse)*, *Severidad e Incertidumbre*), capas raster XYZ y vectores GeoJSON.
- `data/cases/<id>/tiles/<capa>/{z}/{x}/{y}.png`: teselas Web Mercator 256×256 prerenderizadas con las paletas exactas de GeoPulse para evitar re-coloreado genérico.
- `data/cases/<id>/burn_extent.geojson`: polígonos vectorizados donde `burn_probability >= 0.5` (con área mínima `0.002 km²`), estilizados exactamente en `#ffd60a` con borde negro.
- `data/cases/<id>/aoi.geojson`: contorno del área de estudio.
- `data/cases/<id>/summary.json` y `provenance.json`: métricas de superficie quemada (`affected_km2`, `severity_km2`, `review_km2`) y escenas STAC empleadas.
