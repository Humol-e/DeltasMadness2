# Guía para Interpretar y Determinar los Datos (Etapa 1)

Esta guía explica cómo leer las salidas reales de cada módulo de `glacier_monitor`, cómo elegir las escenas correctas y qué archivos finales te entrega en modo local (`data/downloads/`).

> **Nota importante para Windows CMD (`C:\Users\Rulis>`):**
> - Cambia primero a la carpeta del proyecto (`cd C:\Users\Rulis\Desktop\hola`) para que las descargas queden en `C:\Users\Rulis\Desktop\hola\data\downloads`.
> - **No copies comentarios con `#`** en CMD (a diferencia de PowerShell, CMD intenta ejecutar `#` como programa).
> - **Reemplaza siempre `<...>`** por el nombre real del gránulo; en CMD, los símbolos `<` y `>` provocan `The syntax of the command is incorrect` .

---

## 1. Diagnóstico del sistema (`doctor`)

```cmd
py -m glacier_monitor doctor
```

Qué debes verificar antes de pedir datos:
- **Config (.env) = `OK`**: proyecto GEE configurado (`sarshack`) y modo local activo.
- **Google Earth Engine = `OK`**: consulta en nube habilitada.
- **Google Cloud Storage = `LOCAL`**: modo local sin bucket (no sube nada a Google Cloud Storage).
- **NASA HyP3 / Earthdata = `OK`**: muestra tus créditos disponibles (ej. `8000`) para procesar InSAR/RTC gratis en servidores de la NASA.
- **Catálogo NASA ASF = `OK`**: conexión activa al catálogo de búsqueda de NASA ASF.

---

## 2. Datos nativos en Google Earth Engine (`check-native`)

```cmd
py -m glacier_monitor check-native --aoi langtang --start 2026-09-01 --end 2026-10-15
```

### Cómo leer la tabla
| Columna | Qué significa |
| --- | --- |
| `Dataset ID` | Colección oficial en Google Earth Engine. |
| `Imágenes` | Cantidad de escenas que cubren tu AOI en ese rango de fechas (el día `--end` es exclusivo en GEE). |
| `Más antigua / Más reciente (UTC)` | Ventana real con datos disponibles. Te indica si el catálogo está al día. |
| `Bandas disponibles` | Capas de medición dentro de cada imagen. |

### Qué determina cada colección
1. **`COPERNICUS/S1_GRD`** (Sentinel-1 radar GRD, 10 m, cada ~6–12 días):
   - **Bandas clave**: `VV`, `VH`, `angle`.
   - **Para qué sirve**: atraviesa nubes y funciona de noche. El agua tranquila (lagos glaciares) refleja poca energía (valores bajos en dB / tonos oscuros) y el hielo/roca rugosa refleja más. Ayuda a detectar cambios de contorno de lagunas y humedad superficial (deshielo).
2. **`MODIS/061/MOD10A1`** (MODIS nieve diaria, 500 m):
   - **Bandas clave**: `NDSI_Snow_Cover` (porcentaje de nieve 0–100), `Snow_Albedo_Daily_Tile`, `NDSI_Snow_Cover_Basic_QA`.
   - **Para qué sirve**: seguir acumulación o pérdida diaria de nieve en la cuenca.
3. **`MODIS/061/MOD11A1`** (MODIS temperatura superficial diaria, 1 km):
   - **Bandas clave**: `LST_Day_1km`, `LST_Night_1km`, `QC_Day`, `QC_Night`.
   - **Para qué sirve**: detectar días/noches con temperatura superficial sobre punto de fusión (0 °C), señal directa de deshielo.

---

## 3. Cómo leer y elegir escenas Sentinel-1 SLC (`asf-search`)

```cmd
py -m glacier_monitor asf-search --dataset sentinel1 --aoi langtang --start 2026-09-01 --end 2026-10-15 --processing-level SLC
```

### Anatomía de un nombre real de gránulo
Ejemplo de tu salida:
```text
S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700
```

| Segmento | Valor | Significado |
| --- | --- | --- |
| `S1D` | Sentinel-1D | Satélite que tomó la escena (`S1A`, `S1C`, `S1D`). |
| `IW` | Interferometric Wide | Modo estándar sobre tierra firme (franja de ~250 km). |
| `SLC` | Single Look Complex | Contiene **amplitud + fase**; obligatorio para hacer interferometría **InSAR**. |
| `1SDV` | Dual-pol VV+VH | Polarización vertical/horizontal. |
| `20261011T001844` | `2026-10-11 00:18:44 UTC` | Fecha y hora exacta de inicio de la pasada. |
| `Órbita` (en tabla) | `DESCENDING` / `ASCENDING` | Dirección del satélite: bajando (norte→sur) o subiendo (sur→norte). |

### Reglas para elegir un par InSAR válido (`hyp3-submit`)
Un job **InSAR** compara **2 escenas `SLC`** de dos fechas distintas para medir desplazamiento milimétrico/centimétrico. Para que el par funcione bien debe cumplir **3 reglas**:

1. **Misma dirección de `Órbita`**: nunca mezcles `ASCENDING` con `DESCENDING`.
2. **Misma hora aproximada (mismo track/pasada)**:
   - En tu salida hay dos pasadas `DESCENDING` distintas sobre Langtang: unas pasan a las **`00:18:44Z`** y otras a las **`00:10:37Z`**.
   - Empareja `00:18Z` con `00:18Z` (o `00:10Z` con `00:10Z`), porque tienen la misma geometría de vista.
3. **Separación de fechas corta (12 o 24 días)**: en glaciares y nieve, pares muy lejanos pierden coherencia por derretimiento o nevadas.

#### Pares reales válidos de tu búsqueda (listos para usar)
- **Par `DESCENDING` (track `00:18Z`, 12 días)**:
  - Escena 1: `S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700` (`2026-10-11`)
  - Escena 2: `S1D_IW_SLC__1SDV_20260929T001844_20260929T001911_004785_008F91_C12D` (`2026-09-29`)
- **Par `DESCENDING` (track `00:10Z`, 12 días)**:
  - Escena 1: `S1D_IW_SLC__1SDV_20261006T001037_20261006T001104_004887_009321_B7AE` (`2026-10-06`)
  - Escena 2: `S1D_IW_SLC__1SDV_20260924T001037_20260924T001104_004712_008D08_DDD9` (`2026-09-24`)
- **Par `ASCENDING` (track `12:21Z`, 12 días)**:
  - Escena 1: `S1D_IW_SLC__1SDV_20261003T122140_20261003T122208_004851_0091DF_22F7` (`2026-10-03`)
  - Escena 2: `S1D_IW_SLC__1SDV_20260921T122140_20260921T122208_004676_008BC6_6670` (`2026-09-21`)

Ejemplo listo para copiar en CMD (envía el par `DESCENDING` `00:18Z` a HyP3):

```cmd
py -m glacier_monitor hyp3-submit --job-type insar --name langtang-test --granule S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700 --granule S1D_IW_SLC__1SDV_20260929T001844_20260929T001911_004785_008F91_C12D
```

Monitorear hasta que pase de `PENDING`/`RUNNING` a `SUCCEEDED` y descargar en local:

```cmd
py -m glacier_monitor hyp3-status --name langtang-test
py -m glacier_monitor hyp3-download --job-name langtang-test --suffix _unw_phase.tif --suffix _coh.tif --suffix _los_disp.tif --suffix _vert_disp.tif
```

---

## 4. Cómo leer y elegir productos OPERA-S1 (`asf-search --dataset opera_s1`)

```cmd
py -m glacier_monitor asf-search --dataset opera_s1 --aoi langtang --start 2026-10-01 --end 2026-11-14
```

### Anatomía de un gránulo OPERA-S1
Ejemplo de tu salida:
```text
OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_20261111T190434Z_S1D_30_v1.0
```

| Segmento | Qué indica | Cómo usarlo |
| --- | --- | --- |
| `OPERA_L2_RTC-S1` | Producto radar ya corregido por relieve (RTC) | Viene listo como GeoTIFF directo (no gasta créditos HyP3). |
| `T121-258661-IW3` | **Track + Burst ID + Sub-franja** | Es la "baldosa" exacta del terreno. Para comparar cambios entre fechas, **elige el mismo ID de burst** (ej. `T121-258661-IW3` el `2026-11-11`, `2026-10-30` y `2026-10-18`). |
| `20261111T001050Z` | Fecha y hora de adquisición (`2026-11-11`) | Ordena tu serie temporal. |
| `30` | Resolución espacial (`30` metros) | Tamaño de cada píxel. |

Ejemplo listo para copiar en CMD (descarga directa de bandas `VV` y `VH` a local sin usar HyP3 ni bucket):

```cmd
py -m glacier_monitor asf-download --granule OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_20261111T190434Z_S1D_30_v1.0 --suffix _VV.tif --suffix _VH.tif
```

---

## 5. Qué archivo GeoTIFF elegir después (para Etapa 2)

Todos los archivos descargados quedan registrados automáticamente en `data/downloads/catalog.json` con su sensor, capa (`product_layer`) y ventana temporal UTC:

| Sufijo del archivo | `product_layer` | Qué te permite determinar |
| --- | --- | --- |
| `_los_disp.tif` | `los_displacement` | Desplazamiento en la línea de vista del satélite (metros). Mide movimiento en laderas, morrenas o glaciares. |
| `_vert_disp.tif` | `vertical_displacement` | Estimación de hundimiento o levantamiento vertical (metros). |
| `_unw_phase.tif` | `unwrapped_phase` | Fase interferométrica desenvuelta (radianes), insumo base para calcular deformación. |
| `_coh.tif` | `coherence` | Calidad/confiabilidad del par InSAR (`0.0` a `1.0`). Valores altos (`> 0.3–0.5` según cobertura) indican señal confiable; valores bajos indican nieve fresca, agua o cambio brusco. |
| `_vv.tif` / `_VV.tif` | `backscatter_vv` | Intensidad radar VV: útil para mapear espejos de agua (lagos glaciares) y estructura superficial. |
| `_vh.tif` / `_VH.tif` | `backscatter_vh` | Intensidad radar VH: sensible a rugosidad de hielo, escombros y vegetación circundante. |
| `_inc_map.tif` | `incidence_angle` | Ángulo local de incidencia radar sobre la montaña (sirve para corregir/enmascarar sombras y geometría). |
