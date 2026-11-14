# Monitoreo Glaciar (GEE + ASF HyP3) — Etapa 1

Implementación de la **Etapa 1: Ingesta directa en Google Earth Engine (Catálogo nativo + Puente ASF)**, preparada para conectarse con las etapas siguientes (motor de análisis SAR/DEM, backend ligero FastAPI/Flask y frontend 3D).

Para una guía paso a paso sobre cómo leer las salidas de cada comando y elegir pares InSAR o series OPERA-S1, consulta [GUIA_DATOS.md](GUIA_DATOS.md).

## Estructura actual (Etapa 1)

- `src/glacier_monitor/config.py`: Configuración vía `.env` (`Settings`), bounding boxes y presets de glaciares (`langtang`, `khumbu`).
- `src/glacier_monitor/gee_auth.py`: Inicialización de Google Earth Engine y Google Cloud Storage mediante Service Account JSON o credenciales por defecto (ADC / `earthengine authenticate`).
- `src/glacier_monitor/ingestion/native_catalog.py`: Conectores y verificación de disponibilidad/frescura para:
  - `COPERNICUS/S1_GRD` (Sentinel-1 SAR GRD)
  - `MODIS/061/MOD10A1` (Cobertura de nieve diaria 500m)
  - `MODIS/061/MOD11A1` (Temperatura superficial diaria 1km)
- `src/glacier_monitor/ingestion/asf_bridge.py`: Búsqueda en catálogo ASF (`asf_search` para `SENTINEL1`, `NISAR`, `UAVSAR`), envío/consulta de jobs en ASF HyP3 (`hyp3_sdk`), descompresión de `.zip` a GeoTIFFs y adaptador opcional para `gee-community/sar-asf-to-gee`.
- `src/glacier_monitor/ingestion/gee_uploader.py`: Subida de GeoTIFFs a Google Cloud Storage y registro como Assets en GEE (`ee.data.startIngestion` + generación de comando equivalente `earthengine upload image`).
- `src/glacier_monitor/ingestion/pipeline.py` & `scripts/stage1_cli.py`: Orquestación completa e interfaz CLI.

## Configuración rápida

1. Copia `.env.example` a `.env` y completa tus datos de Google Cloud / GEE y NASA Earthdata:
   ```powershell
   Copy-Item .env.example .env
   ```
2. (Opcional) Si quieres usar el paquete comunitario `gee-community/sar-asf-to-gee` con `--use-community-bridge`:
   ```powershell
   pip install git+https://github.com/gee-community/sar-asf-to-gee.git
   ```

## Comandos de Etapa 1

```powershell
# Verificar configuración, credenciales y conectividad (GEE, GCS, ASF, HyP3)
py -m glacier_monitor doctor

# Listar glaciares/valles preconfigurados
py scripts/stage1_cli.py list-aois

# 1A. Verificar actualización y bandas disponibles en el catálogo nativo de GEE
py scripts/stage1_cli.py check-native --aoi langtang --start 2026-09-01 --end 2026-10-15

# 1B. Buscar gránulos en NASA ASF (sentinel1, nisar, nisar_science, uavsar, opera_s1)
py scripts/stage1_cli.py asf-search --dataset sentinel1 --aoi langtang --start 2026-09-01 --end 2026-10-15 --processing-level SLC

# 1B (Local sin GCS). Descargar GeoTIFFs directos de ASF (ej. OPERA-S1)
py -m glacier_monitor asf-download --granule <GRANULE_NAME> --suffix _VV.tif --suffix _VH.tif

# 1B. Enviar un job InSAR a ASF HyP3 y consultar estado
py scripts/stage1_cli.py hyp3-submit --job-type insar --name langtang-insar --granule <GRANULE_1> --granule <GRANULE_2>
py -m glacier_monitor hyp3-status --name langtang-insar

# 1B (Local sin GCS). Descargar GeoTIFFs de HyP3 e indexarlos en data/downloads/catalog.json
py -m glacier_monitor hyp3-download --job-name langtang-insar --suffix _unw_phase.tif --suffix _coh.tif

# 1B (Opcional con GCS). Simular (--dry-run) o ejecutar ingesta de job HyP3 -> GCS -> GEE
py scripts/stage1_cli.py asf-ingest --job-name langtang-insar --suffix _unw_phase.tif --suffix _coh.tif --dry-run

# 1B (Opcional con GCS). Subir un GeoTIFF local (NISAR / UAVSAR / HyP3) -> GCS -> GEE Asset
py scripts/stage1_cli.py asf-ingest --local-file ruta/al/producto.tif --sensor NISAR --wait
```

## Pruebas

```powershell
py -m pytest -v
```
