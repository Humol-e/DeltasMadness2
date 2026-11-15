# Guide to Interpreting and Selecting Glacier Data (Stage 1)

This guide explains how to read real outputs from every `glacier_monitor` module, how to choose valid scenes, and what final files are saved in local mode (`data/downloads/`).

> **Note for Windows CMD (`C:\Users\Rulis>`):**
> - Change into the project folder first (`cd ...\hola`) so downloads are saved under `hola\data\downloads`.
> - **Do not paste lines starting with `#`** in CMD (unlike PowerShell, CMD tries to execute `#` as a command).
> - **Always replace `<...>`** with an actual granule name; in CMD, literal `<` and `>` cause `The syntax of the command is incorrect`.

---

## 1. System Diagnostics (`doctor`)

```cmd
py -m glacier_monitor doctor
```

What to check before fetching data:
- **Config (.env) = `OK`**: GEE project configured and local mode active.
- **Google Earth Engine = `OK`**: cloud query access enabled.
- **Google Cloud Storage = `LOCAL`**: local mode without bucket (nothing uploaded to GCS).
- **NASA HyP3 / Earthdata = `OK`**: displays available processing credits (e.g., `8000`) for running free InSAR/RTC jobs on NASA servers.
- **NASA ASF Catalog = `OK`**: active connection to NASA ASF's search catalog.

---

## 2. Native Google Earth Engine Data (`check-native`)

```cmd
py -m glacier_monitor check-native --aoi langtang --start 2026-09-01 --end 2026-10-15
```

### How to read the table
| Column | Meaning |
| --- | --- |
| `Dataset ID` | Official Google Earth Engine collection ID. |
| `Images` | Scene count covering your AOI within that date range (`--end` is exclusive in GEE). |
| `Earliest / Latest (UTC)` | Actual available data window, indicating catalog freshness. |
| `Available bands` | Measurement layers contained in each image. |

### What each collection provides
1. **`COPERNICUS/S1_GRD`** (Sentinel-1 radar GRD, 10 m, every ~6-12 days):
   - **Key bands**: `VV`, `VH`, `angle`.
   - **Use case**: penetrates clouds and operates day/night. Calm open water (glacial lakes) has low backscatter (low dB / dark tones) whereas ice and rough rock scatter strongly, helping detect lake boundary shifts and surface moisture/melt.
2. **`MODIS/061/MOD10A1`** (MODIS daily snow, 500 m):
   - **Key bands**: `NDSI_Snow_Cover` (snow percentage 0-100), `Snow_Albedo_Daily_Tile`, `NDSI_Snow_Cover_Basic_QA`.
   - **Use case**: track daily basin-scale snow accumulation or retreat.
3. **`MODIS/061/MOD11A1`** (MODIS daily land surface temperature, 1 km):
   - **Key bands**: `LST_Day_1km`, `LST_Night_1km`, `QC_Day`, `QC_Night`.
   - **Use case**: detect day/night land surface temperatures above freezing (0 °C), a direct indicator of surface melt.

---

## 3. Reading and Selecting Sentinel-1 SLC Scenes (`asf-search`)

```cmd
py -m glacier_monitor asf-search --dataset sentinel1 --aoi langtang --start 2026-09-01 --end 2026-10-15 --processing-level SLC
```

### Anatomy of a real granule name
Example output:
```text
S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700
```

| Segment | Value | Meaning |
| --- | --- | --- |
| `S1D` | Sentinel-1D | Satellite that acquired the scene (`S1A`, `S1C`, `S1D`). |
| `IW` | Interferometric Wide | Standard land acquisition mode (~250 km swath). |
| `SLC` | Single Look Complex | Contains **amplitude + phase**; required for **InSAR** interferometry. |
| `1SDV` | Dual-pol VV+VH | Vertical/horizontal polarization. |
| `20261011T001844` | `2026-10-11 00:18:44 UTC` | Exact pass start date and time. |
| `Orbit` (in table) | `DESCENDING` / `ASCENDING` | Satellite flight direction: descending (north->south) or ascending (south->north). |

### Rules for choosing a valid InSAR pair (`hyp3-submit`)
An **InSAR** job compares **2 `SLC` scenes** from two dates to measure millimeter/centimeter surface displacement. A valid pair must follow **3 rules**:

1. **Same `Orbit` direction**: never mix `ASCENDING` with `DESCENDING`.
2. **Same approximate time (same track/pass)**:
   - Over Langtang there are two distinct `DESCENDING` passes: one around **`00:18:44Z`** and another around **`00:10:37Z`**.
   - Pair `00:18Z` with `00:18Z` (or `00:10Z` with `00:10Z`) so viewing geometry matches.
3. **Short temporal baseline (12 or 24 days)**: over glaciers and snow, longer baselines lose coherence due to melt or fresh snowfall.

#### Valid real pairs from search results (ready to run)
- **`DESCENDING` pair (`00:18Z` track, 12 days)**:
  - Scene 1: `S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700` (`2026-10-11`)
  - Scene 2: `S1D_IW_SLC__1SDV_20260929T001844_20260929T001911_004785_008F91_C12D` (`2026-09-29`)
- **`DESCENDING` pair (`00:10Z` track, 12 days)**:
  - Scene 1: `S1D_IW_SLC__1SDV_20261006T001037_20261006T001104_004887_009321_B7AE` (`2026-10-06`)
  - Scene 2: `S1D_IW_SLC__1SDV_20260924T001037_20260924T001104_004712_008D08_DDD9` (`2026-09-24`)
- **`ASCENDING` pair (`12:21Z` track, 12 days)**:
  - Scene 1: `S1D_IW_SLC__1SDV_20261003T122140_20261003T122208_004851_0091DF_22F7` (`2026-10-03`)
  - Scene 2: `S1D_IW_SLC__1SDV_20260921T122140_20260921T122208_004676_008BC6_6670` (`2026-09-21`)

Example ready to run in CMD (submits the `DESCENDING` `00:18Z` pair to HyP3):

```cmd
py -m glacier_monitor hyp3-submit --job-type insar --name langtang-test --granule S1D_IW_SLC__1SDV_20261011T001844_20261011T001911_004960_0095BA_A700 --granule S1D_IW_SLC__1SDV_20260929T001844_20260929T001911_004785_008F91_C12D
```

Monitor until it moves from `PENDING`/`RUNNING` to `SUCCEEDED`, then download locally:

```cmd
py -m glacier_monitor hyp3-status --name langtang-test
py -m glacier_monitor hyp3-download --job-name langtang-test --suffix _unw_phase.tif --suffix _coh.tif --suffix _los_disp.tif --suffix _vert_disp.tif
```

---

## 4. Reading and Selecting OPERA-S1 Products (`asf-search --dataset opera_s1`)

```cmd
py -m glacier_monitor asf-search --dataset opera_s1 --aoi langtang --start 2026-10-01 --end 2026-11-14
```

### Anatomy of an OPERA-S1 granule
Example output:
```text
OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_20261111T190434Z_S1D_30_v1.0
```

| Segment | Meaning | How to use |
| --- | --- | --- |
| `OPERA_L2_RTC-S1` | Radiometrically terrain-corrected radar product (RTC) | Delivered directly as GeoTIFFs (consumes 0 HyP3 credits). |
| `T121-258661-IW3` | **Track + Burst ID + Sub-swath** | Exact ground burst tile. To compare change over time, **choose the same burst ID** (e.g., `T121-258661-IW3` on `2026-11-11`, `2026-10-30`, and `2026-10-18`). |
| `20261111T001050Z` | Acquisition timestamp (`2026-11-11`) | Orders your time series chronologically. |
| `30` | Spatial resolution (`30` meters) | Ground sampling size per pixel. |

Example ready to run in CMD (direct local download of `VV` and `VH` bands without HyP3 or GCS):

```cmd
py -m glacier_monitor asf-download --granule OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_20261111T190434Z_S1D_30_v1.0 --suffix _VV.tif --suffix _VH.tif
```

---

## 5. Choosing GeoTIFF Layers Downstream (Stage 2)

Every downloaded file is automatically registered in `data/downloads/catalog.json` with its sensor, `product_layer`, and UTC acquisition window:

| File suffix | `product_layer` | What it measures |
| --- | --- | --- |
| `_los_disp.tif` | `los_displacement` | Line-of-sight surface displacement (meters) on slopes, moraines, or glaciers. |
| `_vert_disp.tif` | `vertical_displacement` | Estimated vertical subsidence or uplift (meters). |
| `_unw_phase.tif` | `unwrapped_phase` | Unwrapped interferometric phase (radians), base input for deformation retrieval. |
| `_coh.tif` | `coherence` | InSAR pair quality/reliability (`0.0` to `1.0`). High values (`> 0.3-0.5` depending on land cover) indicate reliable signal; low values indicate fresh snow, open water, or rapid change. |
| `_vv.tif` / `_VV.tif` | `backscatter_vv` | VV backscatter intensity: useful for mapping glacial lake open water and surface structure. |
| `_vh.tif` / `_VH.tif` | `backscatter_vh` | VH backscatter intensity: sensitive to ice roughness, debris cover, and surrounding vegetation. |
| `_inc_map.tif` | `incidence_angle` | Local radar incidence angle over steep terrain (used to mask radar shadow/layover). |
