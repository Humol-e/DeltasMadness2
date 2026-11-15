# biomasa (`nisar-biomass-monitor`)

Pipeline de biomasa aérea forestal (**AGB**, Mg/ha) basado en el módulo científico `NISAR_Biomass/` (algoritmo NISAR L3 ATBD) e integrado al visor 3D `sar-Madness`.

## Flujo científico

1. **Parámetros físicos calibrados**: lee directamente `[NISAR]` desde `../NISAR_Biomass/config/config.ini` (`A`, `B`, `C`, `alpha`, `delta`, `D`, `s` para HH y HV).
2. **Ingesta real S3**: descarga o reutiliza en caché local (`biomasa/data/cache/`) las escenas públicas NISAR L2 GCOV `.h5` (20 m) del sitio **LENO** (Lenoir Landing, Alabama) junto con `LENO_LULC.tif` (30 m NLCD) y capas de validación Planet (`PLANET_VAL_FINALDIST_100M_LENO_*`).
3. **Remuestreo a 1 ha (100 m)** y máscara física NISAR (`HV > 0.01`, `HV < 1`, `HH < 1`, excluyendo clases NLCD no forestales/urbanas/agua `{11, 12, 22, 23, 24}`).
4. **Inversión AGB Banda L**: reproduce en forma vectorizada el modelo forward (`volume + double + surface`) y la optimización en dB ponderada (`weights=(4, 1)`) entre polarizaciones HV y HH, generando productos `AGB Pre`, `AGB Post`, `ΔAGB` y `HV/HH dB`.
5. **Exportación `sar-Madness` (Schema v2)**: reproyecta a `EPSG:4326`, genera PNGs `gray8`/`rgba`, GeoJSON de pérdida de biomasa (`ΔAGB <= -40 Mg/ha`) y GeoTIFFs descargables registrados en `sar-Madness/sar-Madness/public/data/catalog.json`.

## Uso

```powershell
py -m pip install --no-deps -e biomasa
py -m nisar_biomass_monitor list-scenes
py -m nisar_biomass_monitor publish-madness
```
