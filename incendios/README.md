# Incendios Forestales (GeoPulse → sar-platform)

Módulo de ingesta, análisis y conversión de datos de incendios forestales basado en [geopulse](../geopulse), organizado con la misma estructura modular de [hola](../hola) para que el programa principal ([sar-platform](../sar-platform)) pueda leerlos directamente sin alterar su apariencia visual ni sus colores originales.

## Estructura del proyecto

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
│           └── pipeline.py
├── tests/
│   ├── test_config.py
│   ├── test_pc_auth.py
│   ├── test_native_catalog.py
│   ├── test_geopulse_bridge.py
│   ├── test_platform_exporter.py
│   └── test_stage1_pipeline.py
└── data/
    ├── downloads/   # GeoTIFFs compatibles con PIL/sar-platform + catalog.json
    └── cases/       # Casos case.json + GeoJSON + tiles XYZ + index.json
```

## Cómo adaptamos los datos de GeoPulse para `sar-platform`

1. **GeoTIFFs y `catalog.json` compatibles con `sar-platform` (`data/downloads/`):**
   - `sar-platform` lee GeoTIFFs locales mediante `Pillow` (`glacier_monitor.bridge.geotiff.load_geotiff_raster`) y metadatos desde `catalog.json`.
   - `wildfire_monitor` decodifica las escenas STAC de Microsoft Planetary Computer (`sentinel-2-l2a`, `sentinel-1-rtc`, `cop-dem-glo-30`) o carpetas COG comprimidas de GeoPulse y genera GeoTIFFs monobanda sin compresión con etiquetas GeoKey estándar (`34735`, `33550`, `33922`, `42113`) y un `catalog.json` con exactamente el mismo esquema de `hola`.
2. **Casos `case.json` y teselas XYZ conservando exactamente cómo se ven (`data/cases/`):**
   - Cada incendio genera `case.json`, `aoi.geojson`, `burn_extent.geojson`, `summary.json`, `provenance.json`, `layers/*.png` y teselas Web Mercator `tiles/<capa>/{z}/{x}/{y}.png`.
   - Todas las paletas visuales de GeoPulse se conservan pixel a pixel:
     - **Probabilidad de quemado:** degradado GeoPulse `#eb461e` → `#c8140a`.
     - **Severidad dNBR (Key & Benson, 2006):** Baja `#7fffd4`, Moderada-baja `#ffff00`, Moderada-alta `#ff8c00`, Alta `#ff0000`.
     - **Probabilidad de cambio multimodal:** `#ffaa00` → `#ff7800`.
     - **Incertidumbre:** `#3c145a` → `#e6505a` → `#ffd23c`.
     - **Perímetro de quemado:** contorno amarillo `#ffd60a` con borde `#000000`.

## Comandos principales

```powershell
# Ver incendios preconfigurados de GeoPulse
py scripts/stage1_cli.py list-aois

# Diagnóstico de dependencias, rutas y API STAC
py scripts/stage1_cli.py doctor

# Consultar escenas disponibles en Planetary Computer
py scripts/stage1_cli.py check-native --aoi palisades --start-date 2024-12-01 --end-date 2025-03-31

# Procesar y convertir los 3 casos reales de GeoPulse (Palisades, Camp Fire, Martin NV)
py scripts/stage1_cli.py run-wildfire --aoi all

# Convertir una carpeta de resultados ya generada por geopulse
py scripts/stage1_cli.py convert-geopulse ../geopulse/outputs/mi_incendio

# Copiar/registrar los casos listos en ../sar-platform cuando quieras verlos en el visor principal
py scripts/stage1_cli.py publish-platform --include-downloads
```

## Pruebas automatizadas

```powershell
py -m pytest -q tests
```
