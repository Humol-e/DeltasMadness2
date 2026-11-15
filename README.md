# Delta Madness (`DeltasMadness2` · `sar-Madness`)

**All-weather multi-sensor geospatial satellite monitoring platform (SAR + optical + thermal + DEM) powered by deterministic physical models for Before / After analysis.**

[![NASA Space Apps](https://img.shields.io/badge/NASA_Space_Apps_2026-Dancing_with_the_SARs-0B3D91)](./Delta_Madness_Documentation_English_Version.pdf)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB)](#installation-and-quick-start)
[![Frontend](https://img.shields.io/badge/Viewer-Mapbox_GL_JS_v3.9_%2B_Vite_5-1E293B)](#4-3d-web-viewer-sar-madnesssar-madness)
[![Catalog Schema v2](https://img.shields.io/badge/Catalog-6%2F6_Cases_Validated-15803D)](#validated-study-cases)

> **Official NASA Space Apps Challenge Documentation (English PDF):** [Delta Madness Documentation](./Delta_Madness_Documentation_English_Version.pdf)<br>
> **Technical Article & NISAR Traceability (English PDF):** [Technical Article: Geospatial Ingestion and NISAR Cookbook](./Technical_Article_Geospatial_Ingestion_and_NISAR_Cookbook.pdf)

---

## Project Submission Links

* **Primary Source Code & Data Repository:** `https://github.com/Humol-e/DeltasMadness2`
* **Public 7-Slide Presentation / <= 30-Second Demo Video:** `[PENDING — INSERT PUBLIC PRESENTATION OR <= 30-SECOND VIDEO LINK]`
* **Public Hosted Web Viewer (Optional):** `[OPTIONAL — INSERT PUBLIC WEB VIEWER URL IF PUBLISHED]`

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Validated Study Cases](#validated-study-cases)
3. [Repository Architecture](#repository-architecture)
4. [Scientific Pipelines & Web Viewer](#scientific-pipelines--web-viewer)
5. [Mathematical & Physical Formulation](#mathematical--physical-formulation)
6. [Data Sources (NASA, Partner Space Agencies & Third Parties)](#data-sources-nasa-partner-space-agencies--third-parties)
7. [Installation and Quick Start](#installation-and-quick-start)
8. [Testing and Validation](#testing-and-validation)
9. [AI / Super Intelligence Transparency](#ai--super-intelligence-transparency)
10. [Team & Contributors](#team--contributors)
11. [License](#license)

---

## Project Overview

**Delta Madness** (`DeltasMadness2`) unifies **three Python scientific engines** and an **interactive 3D web viewer built with Mapbox GL JS (`sar-Madness`)** designed to monitor critical environmental changes without relying solely on clear-sky optical imagery:

* **Forest biomass & carbon (`biomasa` / `nisar-biomass-monitor`):** vectorized NumPy/SciPy implementation of the **NISAR L3 AGB ATBD** semi-empirical model using calibrated coefficients from `NISAR_Biomass/config/config.ini`. It processes **official NASA pre-launch simulated NISAR L2 GCOV** (`20 m → 1 ha`, derived from UAVSAR/ALOS-2 over NEON LENO, Alabama) to estimate aboveground biomass (`Mg/ha`), carbon density (`0.47 * AGB`), and severe forest loss (`ΔAGB <= -40 Mg/ha`) compared against PlanetScope disturbance validation.
* **Wildfires (`incendios` / `wildfire-monitor`):** deterministic physical baseline combining optical spectral indices (`dNBR`, `NDVI`, `MNDWI` from Sentinel-2 L2A) with an all-weather polarimetric SAR fallback (`VH` drop from Sentinel-1 RTC where optical observations are obscured/`NaN`), Copernicus DEM terrain slope, Key & Benson (2006) severity classes, a pre-event physical water mask, and Shannon binary-entropy uncertainty.
* **Glacier melt & alpine hydrology (`hola` / `glacier-monitor`):** multi-source composition in Google Earth Engine (Sentinel-1 GRD, Sentinel-2 SR `NDSI`, MODIS `MOD10A1` snow cover & `MOD11A1` land surface temperature) plus connectivity to NASA ASF Search / HyP3 (`SENTINEL1`, `NISAR`, `NISAR_SCIENCE_PRODUCTS`, `UAVSAR`, `OPERA_S1`) and optional upload to GCS/GEE Assets.

All components publish standardized products under **`schemaVersion: 2`** inside `sar-Madness/sar-Madness/public/data/`, where real-observation provenance (`status: "real"`) is strictly validated before rendering or building.

---

## Validated Study Cases

The active catalog [`sar-Madness/sar-Madness/public/data/catalog.json`](./sar-Madness/sar-Madness/public/data/catalog.json) integrates **6 validated cases** across three domains:

| Case ID | Domain | Location & Window | Primary Sources | Verified Key Metrics |
| :--- | :--- | :--- | :--- | :--- |
| `wildfire-camp-paradise` | Wildfire | Paradise, California (2018) | Planetary Computer (`S1 RTC` 4+4, `S2 L2A` 4+4, `COP-DEM` 56.0–558.0 m) | `54.41 km²` affected / `156.186 km²` observed; `9.201 km²` under review; severity (km²): low `19.598`, mod-low `14.242`, mod-high `10.835`, high `8.267` |
| `wildfire-martin-nv` | Wildfire | Martin Fire, Nevada (2018) | Planetary Computer (`S1 RTC` 4+4, `S2 L2A` 4+4, `COP-DEM` 1550.0–1801.0 m) | `57.675 km²` affected / `179.589 km²` observed; `13.012 km²` under review; severity (km²): low `45.085`, mod-low `12.521`, mod-high `0.069`, high `0.0` |
| `wildfire-palisades-la` | Wildfire | Palisades, Los Angeles (2025) | Planetary Computer (`S1 RTC` 4+4, `S2 L2A` 3+4, `COP-DEM` 0.0–706.0 m) | `70.146 km²` affected / `110.113 km²` observed; `7.249 km²` under review; severity (km²): low `11.845`, mod-low `11.454`, mod-high `24.105`, high `22.743` |
| `glacier-langtang` | Cryosphere | Langtang Valley, Nepal (Jan–Feb vs May–Aug 2026) | GEE (`S2 SR` 26/31, `S1 GRD IW` 14/27, `MODIS MOD10A1/MOD11A1` 58/108) | MODIS snow cover `38.9% -> 13.7%`; LST `-6.9 °C -> 6.6 °C`; net ΔNDSI melt extent `92.6 km²` |
| `glacier-khumbu` | Cryosphere | Khumbu Glacier, Nepal (Jan–Feb vs May–Aug 2026) | GEE (`S2 SR` 30/33, `S1 GRD IW` 12/18, `MODIS MOD10A1/MOD11A1` 58/108) | MODIS snow cover `20.4% -> 8.4%`; LST `-5.3 °C -> 6.1 °C`; net ΔNDSI melt extent `26.42 km²` |
| `biomass-leno-nisar` | Biomass | NEON LENO, Alabama (`2022-09-30` vs `2023-09-29`) | Simulated NISAR L2 GCOV (UAVSAR/ALOS-2 via Wasabi S3), USGS NLCD, PlanetScope | Observed `405.3 km²` (forest `270.89 km²`); mean AGB `72.7 -> 66.1 Mg/ha`; disturbed `87.3 -> 14.1 Mg/ha`; severe loss `17.53 km²` (`ΔAGB <= -40 Mg/ha`, Planet ref `5.9 km²`*); carbon loss `50.79 Gg C`; HV `-11.57 -> -11.78 dB`; model RMSE `0.53 dB` |

*\*Note on `biomass-leno-nisar`: L-band SAR inversion (`17.53 km²` at `ΔAGB <= -40 Mg/ha`) responds to volumetric canopy structure, soil moisture variation (`s`), and 1 ha aggregation, making it more sensitive to partial/structural degradation than the PlanetScope optical disturbance mask (`5.9 km²`). Additional `2021-08-06` and `2024-08-30` GCOV scenes are cached under `biomasa/data/cache/` for extended CLI analysis.*

---

## Repository Architecture

```text
+-----------------------------------------------------------------------------+
|                  SATELLITE SOURCES & EXTERNAL SERVICES                      |
|  • Wasabi S3 (Simulated NISAR L2 GCOV .h5, NLCD, Planet validation)         |
|  • Google Earth Engine (S1_GRD, S2_SR_HARMONIZED, MODIS MOD10A1/MOD11A1)    |
|  • NASA ASF Search & HyP3 (SENTINEL1, NISAR, UAVSAR, OPERA_S1)              |
|  • Microsoft Planetary Computer STAC + TiTiler (S2 L2A, S1 RTC, COP-DEM)    |
|  • Scout Alerts (NASA EONET v3, GDACS RSS, Copernicus EMS RSS)              |
+-------------------------------+---------------------------------------------+
                                |
          +---------------------+---------------------+
          |                     |                     |
          v                     v                     v
+-------------------+ +-------------------+ +-------------------+
| biomasa/          | | hola/             | | incendios/        |
| nisar-biomass-    | | glacier-monitor   | | wildfire-monitor  |
| monitor           | | GEE + ASF HyP3    | | Planetary STAC    |
| NISAR L3 Model    | | ΔNDSI + LST + SAR | | GeoPulse Fusion   |
| Forward/Inversion | | Exporter v2       | | Entropy + dNBR    |
+---------+---------+ +---------+---------+ +---------+---------+
          |                     |                     |
          +---------------------+---------------------+
                                |
                                v
+-----------------------------------------------------------------------------+
|   schemaVersion: 2 STANDARD -> sar-Madness/sar-Madness/public/data/         |
|   PNG (gray8/rgba) · GeoJSON (AOI / perimeters) · GeoTIFF · case.json       |
|   Validated by: npm run validate (scripts/validate-catalog.mjs)             |
+-------------------------------+---------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------------+
|             3D WEB VIEWER (sar-Madness/sar-Madness · Vite + Mapbox)         |
|  • 3D Globe / Flat / Terrarium DEM Terrain    • Before / After swipe view   |
|  • gray8 LUT colorization + RGB composites    • Real per-pixel readout      |
|  • Download side-by-side PNG plates and raw GeoTIFF rasters                 |
+-----------------------------------------------------------------------------+
```

### Folder Structure

```text
DeltasMadness2/
├── LICENSE                                                # MIT Open Source License
├── .gitmodules                                            # Submodule mapping for NISAR_Biomass
├── README.md                                              # Main repository overview
├── Delta_Madness_Documentation_English_Version.pdf        # Official NASA Space Apps documentation (English)
├── Technical_Article_Geospatial_Ingestion_and_NISAR_Cookbook.pdf # English ETL & NISAR Cookbook reference
├── biomasa/                                               # Python package nisar-biomass-monitor (CLI: biomass-stage1)
│   ├── README.md
│   ├── pyproject.toml
│   ├── src/nisar_biomass_monitor/
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── geo_utils.py
│   │   ├── nisar_atbd.py
│   │   ├── platform_exporter.py
│   │   └── s3_source.py
│   └── tests/                                             # 8 pytest tests
├── hola/                                                  # Python package glacier-monitor (CLI: glacier-stage1)
│   ├── README.md
│   ├── GUIA_DATOS.md
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── scripts/stage1_cli.py
│   ├── src/glacier_monitor/
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── gee_auth.py
│   │   └── ingestion/ (asf_bridge.py, gee_uploader.py, native_catalog.py, pipeline.py, platform_exporter.py)
│   └── tests/                                             # 44 pytest tests
├── incendios/                                             # Python package wildfire-monitor (CLI: wildfire-stage1)
│   ├── README.md
│   ├── GUIA_DATOS.md
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── scripts/stage1_cli.py
│   ├── src/wildfire_monitor/
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── pc_auth.py
│   │   └── ingestion/ (geopulse_bridge.py, native_catalog.py, pipeline.py, platform_exporter.py)
│   └── tests/                                             # 27 pytest tests
├── NISAR_Biomass/                                         # Reference L3 AGB ATBD science submodule (config.ini)
└── sar-Madness/sar-Madness/                               # Active 3D web viewer (Mapbox GL JS v3.9.0 + Vite v5.4.11)
    ├── README.md
    ├── package.json
    ├── index.html
    ├── .env.example
    ├── docs/ (SCHEMA.md, case.example.jsonc, case.template.json)
    ├── scripts/validate-catalog.mjs
    ├── tests/placeSearch.test.mjs                         # 3 Node tests (utility module)
    ├── public/data/ (catalog.json + 6 cases in cases/)
    └── src/
        ├── main.js
        ├── style.css
        └── lib/ (beforeAfterExport.js, i18n.js, mapController.js, placeSearch.js, rasterCanvas.js, registry.js, schema.js, timeline.js)
```

---

## Scientific Pipelines & Web Viewer

### 1. `biomasa` (`nisar-biomass-monitor` v0.1.0)
* Reads the 13 calibrated `[NISAR]` parameters (`A_HV`, `A_HH`, `B_HV`, `B_HH`, `C_HV`, `C_HH`, `alpha_HV`, `alpha_HH`, `delta_HV`, `delta_HH`, `D_HV`, `D_HH`, `s`) from `NISAR_Biomass/config/config.ini`.
* Ingests official NASA pre-launch simulated **NISAR L2 GCOV (`.h5`, 20 m)** scenes from Wasabi S3 (`https://s3.us-west-1.wasabisys.com/nisar-public-ebd`, prefix `ATBD/ecosystems/disturbance/sites/leno/`) over the NEON **LENO** site (Alabama, `EPSG:32616`, `210x193` ha = `405.30 km²` grid).
* Aggregates to **1 ha (100 m)**, masks non-forest/water/urban NLCD classes `{11, 12, 22, 23, 24}`, and applies physical SAR thresholds (`HV > 0.01`, `HV < 1`, `HH < 1`).
* Evaluates the forward backscatter model (`volume + double_bounce + surface`) and inverts aboveground biomass `AGB (Mg/ha)` in decibels with `(4, 1)` weights on `HV` and `HH` (65-point grid search + 24 golden-section refinement steps without requiring GDAL/osgeo). Estimates carbon at `0.47 * agb_mgha`, exports severe loss polygons (`ΔAGB <= -40 Mg/ha`) compared against `PLANET_VAL_FINALDIST_100M_LENO_*`, WGS84 GeoTIFFs, and PNGs to `sar-Madness/sar-Madness`.

### 2. `hola` (`glacier-monitor` v0.1.0)
* Queries **Google Earth Engine** (`COPERNICUS/S1_GRD`, `COPERNICUS/S2_SR_HARMONIZED`, `MODIS/061/MOD10A1`, `MODIS/061/MOD11A1`) and the **NASA ASF Search / HyP3** bridge (`SENTINEL1`, `NISAR`, `NISAR_SCIENCE_PRODUCTS`, `UAVSAR`, `OPERA_S1`), with optional support for `gee-community/sar-asf-to-gee` and GCS/GEE Asset uploads.
* Exports Before/After composites (`NDSI`, Sentinel-2 RGB, Sentinel-1 `VV`/`VH`, MODIS `% snow`, MODIS `LST °C`), snow-loss GeoJSON polygons (`ndsi_drop_thresh=0.16`, `pre_snow_min=0.28`), and GeoTIFFs for `langtang` and `khumbu` (Nepal).

### 3. `incendios` (`wildfire-monitor` v0.1.0)
* Connects to **Microsoft Planetary Computer** STAC (`sentinel-2-l2a`, `sentinel-1-rtc`, `cop-dem-glo-30`) + TiTiler and monitors active Scout alerts (**NASA EONET v3**, **GDACS**, **Copernicus EMS**).
* Includes a standalone pure-Python GeoTIFF codec and Krüger ($n^6$) UTM–WGS84 reprojection.
* Runs the deterministic physical baseline `compute_wildfire_physics` (`GeoPulse Physics Baseline v0.1.0`): sigmoid fusion of `dNBR` (`DNBR_BURN=0.1`) and fuel `NDVI` (`NDVI_FUEL=0.1`), all-weather Sentinel-1 `VH` drop fallback (`VH_DROP_DB=2.0`, `VH_CANOPY_DB=-17.0`) where `S2` is `NaN`, pre-event physical water constraint (`MNDWI` / `VV`), 4 Key & Benson (2006) severity classes `(0.1, 0.27, 0.44, 0.66)`, Shannon binary-entropy uncertainty $H(p)$, and `burn_extent.geojson` polygons (`P >= 0.50`). Includes **11 preset AOIs** (`palisades`, `camp_paradise`, `martin_nv`, `maui_lahaina`, `jasper_ab`, `valparaiso_cl`, `evros_gr`, `tenerife_es`, `tarahumara_mx`, `pantanal_br`, `los_alerces_ar`).

### 4. 3D Web Viewer (`sar-Madness/sar-Madness`)
* Built with **Mapbox GL JS `^3.9.0`** and **Vite `^5.4.11`**.
* Supports **3D Globe**, **Flat Map**, and **3D Terrain** views with `terrarium` elevation tiles, switchable basemaps, and runtime **Esri World Imagery** fallback when Mapbox tile requests hit HTTP `429` rate limits (requires `VITE_MAPBOX_ACCESS_TOKEN` in `.env` to initialize the 3D globe and Satellite Monitoring view).
* Performs client-side scientific LUT rendering of `gray8` rasters and `rgba` composites, real timestamp stepping without artificial cross-date interpolation, interactive **Before / After** geographic swipe comparison, real cursor pixel probing, GeoJSON perimeter overlays, comparative `.png` plate exports (`src/lib/beforeAfterExport.js`), and raw `.tif` downloads. Note: `src/lib/placeSearch.js` is an offline search/coordinate parser utility (covered by Node tests) not yet wired to a search bar in `src/main.js`.

---

## Mathematical & Physical Formulation

All scientific outputs across the repository come from deterministic physical equations implemented in `biomasa`, `hola`, `incendios`, and `sar-Madness`:

### 1. Forest Biomass Inversion (`biomasa` · NISAR L3 AGB ATBD Model)
* **Semi-empirical L-band forward backscatter model** for polarization $p \in \{\text{HH}, \text{HV}\}$ in linear power $\gamma^0$, given aboveground biomass $W$ ($\text{Mg/ha}$) and soil roughness/moisture factor $s = 0.026613616742048$:

$$\gamma^0_p(W) = \underbrace{A_p W^{\alpha_p}\bigl(1 - e^{-B_p W}\bigr)}_{\text{Volume scattering}} + \underbrace{C_p s W^{\delta_p} e^{-B_p W}}_{\text{Trunk-ground double bounce}} + \underbrace{D_p s e^{-B_p W}}_{\text{Attenuated surface scattering}}$$

* **Empirical initialization and weighted inversion in decibels** ($w_{\text{HV}} = 4.0$, $w_{\text{HH}} = 1.0$, $\gamma^0_{\text{dB}} = 10\log_{10}(\gamma^0)$), solved vectorized over a $65$-point grid search plus $24$ golden-section refinement steps ($\phi = \frac{\sqrt{5}-1}{2}$):

$$W_0 = \mathrm{clip}\!\bigl((100 \cdot \gamma^0_{\text{HV}})^2,\; 0,\; 300\bigr)$$

$$\min_{W \in [W_{\min}, W_{\max}]} \sum_{k=1}^{S} \left[ \bigl(w_{\text{HV}}(\hat{\gamma}^0_{\text{HV,dB}}(W) - \gamma^0_{\text{HV,dB},k})\bigr)^2 + \bigl(w_{\text{HH}}(\hat{\gamma}^0_{\text{HH,dB}}(W) - \gamma^0_{\text{HH,dB},k})\bigr)^2 \right]$$

* **Radar fit root-mean-square error ($\text{RMSE}_{\text{dB}}$), aboveground carbon, and regional loss ($100\text{ m} = 1\text{ ha}$):**

$$\text{RMSE}_{\text{dB}} = \sqrt{\frac{1}{2}\left(\frac{1}{S}\sum_{k=1}^S (\hat{\gamma}^0_{\text{HV,dB}} - \gamma^0_{\text{HV,dB},k})^2 + \frac{1}{S}\sum_{k=1}^S (\hat{\gamma}^0_{\text{HH,dB}} - \gamma^0_{\text{HH,dB},k})^2\right)}$$

$$C_{\text{AGB}} = 0.47 \cdot W\ [\text{Mg C/ha}], \qquad \Delta W = W_{\text{post}} - W_{\text{pre}}, \qquad \text{Carbon Loss (Gg C)} = \frac{0.47}{1000} \sum_{\Delta W \le -40} |\Delta W|$$

### 2. Cryosphere & Glacier Melt (`hola`)
* **Normalized Difference Snow Index (`Sentinel-2`) and land surface temperature (`MODIS/061/MOD11A1`):**

$$\text{NDSI} = \frac{B_3 - B_{11}}{B_3 + B_{11}}, \qquad \text{LST}\ [^\circ\text{C}] = 0.02 \cdot \mathrm{DN}_{\mathrm{LSTDay1km}} - 273.15$$

* **Relative snow loss and boolean melt-perimeter criterion:**

$$\text{MeltLoss (\%)} = \max\bigl(0,\; 100 \cdot (\text{NDSI}_{\text{pre}} - \text{NDSI}_{\text{post}})\bigr), \qquad \text{Melt} \iff (\text{NDSI}_{\text{pre}} \ge 0.28) \land (\text{NDSI}_{\text{pre}} - \text{NDSI}_{\text{post}} \ge 0.16)$$

### 3. Wildfires: Optical + SAR Fallback & Uncertainty (`incendios`)
* **Normalized spectral indices (`Sentinel-2 L2A`) and bounded sigmoid** ($\sigma(x) = (1 + e^{-\mathrm{clip}(x, -30, 30)})^{-1}$):

$$\text{NBR} = \frac{\text{NIR} - \text{SWIR}_2}{\text{NIR} + \text{SWIR}_2 + \epsilon}, \quad \Delta\text{NBR} = \text{NBR}_{\text{pre}} - \text{NBR}_{\text{post}}, \quad \text{NDVI} = \frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red} + \epsilon}, \quad \text{MNDWI} = \frac{\text{Green} - \text{SWIR}_1}{\text{Green} + \text{SWIR}_1 + \epsilon}$$

* **Optical burn probability (`S2`) and all-weather SAR `VH` drop fallback (`Sentinel-1 RTC`, used where optical pixels are `NaN`):**

$$P_{\text{burn, S2}} = \sigma\!\left(\frac{\Delta\text{NBR} - 0.10}{0.025}\right) \cdot \sigma\!\left(\frac{\text{NDVI}_{\text{pre}} - 0.10}{0.03}\right), \qquad P_{\text{burn, S1}} = \sigma\!\left(\frac{(\text{VH}_{\text{pre}} - \text{VH}_{\text{post}}) - 2.0\text{ dB}}{0.6}\right) \cdot \sigma\!\left(\frac{\text{VH}_{\text{pre}} + 17.0\text{ dB}}{1.0}\right)$$

* **Pre-event physical water constraint** ($w_{\text{water}} > 0.8 \implies P_{\text{burn}} = 0,\, U = 0$) and **multi-modal change**:

$$w_{\text{S1}} = \sigma\!\left(\frac{-18.0\text{ dB} - \text{VV}_{\text{pre}}}{1.5}\right), \qquad w_{\text{S2}} = \sigma\!\left(\frac{\text{MNDWI}_{\text{pre}}}{0.05}\right)$$

$$C_{\text{S1}} = \sigma\!\left(\frac{\max(|\Delta\text{VV}|, |\Delta\text{VH}|) - 3.0\text{ dB}}{0.5}\right), \quad C_{\text{S2}} = \sigma\!\left(\frac{\max(|\Delta\text{MNDWI}|, |\Delta\text{NDVI}|, |\Delta\text{NBR}|) - 0.20}{0.05}\right), \quad C_{\text{total}} = \mathrm{mean}(C_{\text{S1}}, C_{\text{S2}})$$

* **Key & Benson (2006) severity classes (within $P_{\text{burn}} \ge 0.50$) and Shannon binary-entropy uncertainty** ($q = \mathrm{clip}(P, 10^{-6}, 1-10^{-6})$, floor $0.5$ in SAR-only zones and review $> 0.8$):

$$H(P) = -\bigl(q\log_2(q) + (1 - q)\log_2(1 - q)\bigr)\ \text{bits}, \qquad \Delta\text{NBR} \in \{[0.10, 0.27),\ [0.27, 0.44),\ [0.44, 0.66),\ \ge 0.66\}$$

### 4. Geodesy, Topographic Slope & Raster Quantization (`sar-Madness`)
* **DEM terrain slope (`COP-DEM`):** $\theta_{\text{slope}} = \arctan\!\left(\sqrt{(\partial z/\partial x)^2 + (\partial z/\partial y)^2}\right)$.
* **Conformal UTM $\leftrightarrow$ WGS84 Krüger ($n^6$) projection:** with $n = \frac{f}{2-f}$ on the WGS84 ellipsoid ($a = 6378137\text{ m}$, $f = 1/298.257223563$) and rectifying radius $A_{\text{rect}} = \frac{a}{1+n}(1 + \frac{n^2}{4} + \frac{n^4}{64} + \frac{n^6}{256})$.
* **Client-side `gray8` raster dequantization:** $v(q) = v_{\min} + \frac{q - 1}{254}(v_{\max} - v_{\min})$ for $q \in \{1, \dots, 255\}$ ($q = 0$ reserved for *nodata*).

---

## Data Sources (NASA, Partner Space Agencies & Third Parties)

### NASA Data & Resources
* **NASA-ISRO NISAR:** ([NISAR mission](https://science.nasa.gov/mission/nisar/)) L-band 20 m simulated L2 GCOV scenes over NEON LENO on Wasabi S3 [`nisar-public-ebd`](https://s3.us-west-1.wasabisys.com/nisar-public-ebd), calibrated L3 AGB ATBD algorithm from [`NISAR-Science-Algorithms/NISAR_Biomass`](https://github.com/NISAR-Science-Algorithms/NISAR_Biomass), and `asf-search` queries for `NISAR`, `NISAR_SCIENCE_PRODUCTS`, `UAVSAR`, and `OPERA_S1`.
* **NASA MODIS:** ([MOD10A1 v061](https://nsidc.org/data/mod10a1/versions/61) 500 m snow cover and [MOD11A1 v061](https://www.earthdata.nasa.gov/data/catalog/lpcloud-mod11a1-061) 1 km LST via GEE for cryosphere).
* **NASA ASF DAAC & HyP3:** ([Vertex Search](https://search.asf.alaska.edu/#/), [`asfadmin/Discovery-asf_search`](https://github.com/asfadmin/Discovery-asf_search), and [`ASFHyP3/hyp3-sdk`](https://github.com/ASFHyP3/hyp3-sdk) connectors in `hola`).
* **NASA EONET API v3:** ([documentation](https://eonet.gsfc.nasa.gov/docs/v3) and [`/api/v3/events`](https://eonet.gsfc.nasa.gov/api/v3/events) endpoint for active wildfire alerts in `incendios`).
* **Methodological Alignment (`NISAR Cookbook`):** [`ASFOpenSARlab/NISAR_Cookbook`](https://github.com/ASFOpenSARlab/NISAR_Cookbook) (Lewandowski & White) concepts (`GCOV` covariance products, SAR subsetting, polarimetric metadata) documented in [`Technical_Article_Geospatial_Ingestion_and_NISAR_Cookbook.pdf`](./Technical_Article_Geospatial_Ingestion_and_NISAR_Cookbook.pdf).

### Partner Space Agencies (Space Apps Partners 2026), Repositories & Other References
* **ESA (European Space Agency):** Copernicus **Sentinel-1** (`S1_GRD` and `sentinel-1-rtc`), Copernicus **Sentinel-2** (`S2_SR_HARMONIZED` and `sentinel-2-l2a`), **Copernicus DEM GLO-30** (`cop-dem-glo-30`) via [Copernicus Data Space / Sentinels](https://sentinels.copernicus.eu/), and [Copernicus Emergency Management Service (CEMS) RSS](https://mapping.emergency.copernicus.eu/activations/rss/).
* **ISRO (Indian Space Research Organisation):** co-partner of the joint [NASA-ISRO NISAR](https://www.isro.gov.in/NASA_ISRO_SAR_Satellite.html) mission.
* **JAXA (Japan Aerospace Exploration Agency):** [ALOS / ALOS-2 L-Band SAR](https://www.eorc.jaxa.jp/ALOS/en/index_e.htm) (co-reference alongside UAVSAR in the simulated NISAR L2 GCOV product for `biomass-leno-nisar`).
* **Integrated / Referenced External Repositories:**
  * [`NISAR-Science-Algorithms/NISAR_Biomass`](https://github.com/NISAR-Science-Algorithms/NISAR_Biomass) (submodule in `NISAR_Biomass/`, basis of `biomasa/`).
  * [`nrahaman1/geopulse`](https://github.com/nrahaman1/geopulse) (`threshold-baseline` physical model and reference wildfires for `incendios/`).
  * [`gee-community/sar-asf-to-gee`](https://github.com/gee-community/sar-asf-to-gee) (optional ASF HyP3 → GEE bridge in `hola/pyproject.toml`).
  * SDKs & Web Viewer: [`mapbox/mapbox-gl-js`](https://github.com/mapbox/mapbox-gl-js), [`vitejs/vite`](https://github.com/vitejs/vite), [`google/earthengine-api`](https://github.com/google/earthengine-api).
* **Other Geospatial Platforms & Visual Assets:** [Microsoft Planetary Computer STAC](https://planetarycomputer.microsoft.com/api/stac/v1), [Google Earth Engine](https://developers.google.com/earth-engine/datasets), [USGS NLCD](https://www.usgs.gov/centers/eros/science/national-land-cover-database) (`LENO_LULC.tif`), PlanetScope disturbance validation (`PLANET_VAL_FINALDIST_100M_LENO_*`), [GDACS RSS](https://www.gdacs.org/xml/rss.xml), [AWS Terrain Tiles (`terrarium`)](https://registry.opendata.aws/terrain-tiles/), Esri World Imagery (429 runtime fallback), Key & Benson (2006), and curated public-case header thumbnails stored in `sar-Madness/sar-Madness/public/images/cases/`.

---

## Installation and Quick Start

### Prerequisites
* **Node.js** and **npm** (for the `sar-Madness/sar-Madness` web viewer).
* **Python >= 3.11** (for the `biomasa`, `hola`, and `incendios` scientific packages).

### 1. Run the 3D Web Viewer (`sar-Madness/sar-Madness`)

All precomputed cases are already packaged under `sar-Madness/sar-Madness/public/data/`:

```powershell
cd "sar-Madness/sar-Madness"
npm install
Copy-Item .env.example .env   # Required: paste your public pk.* token into VITE_MAPBOX_ACCESS_TOKEN
npm run validate              # Validates catalog.json and all 6 cases
npm run dev                   # Starts the local Vite dev server
```

To build the static production bundle (runs `validate` before `vite build`):

```powershell
npm run build
```

### 2. Install & Run the Python Scientific Pipelines

From the `DeltasMadness2/` root:

```powershell
# Install the three local packages in editable mode
py -m pip install -e biomasa
py -m pip install -e hola
py -m pip install -e incendios
```

Main CLI commands by module:

```powershell
# Forest biomass (public Wasabi S3 simulated NISAR GCOV, no credentials required)
biomass-stage1 list-scenes
biomass-stage1 publish-madness

# Wildfires (Planetary Computer STAC + Scout EONET/GDACS/CEMS)
wildfire-stage1 list-aois
wildfire-stage1 doctor
wildfire-stage1 scout-wildfires --source eonet
wildfire-stage1 publish-madness

# Glaciers (requires GEE project in hola/.env; ASF/HyP3 uses NASA Earthdata token)
glacier-stage1 list-aois
glacier-stage1 doctor
glacier-stage1 publish-madness
```

> **Security note:** never commit live tokens, `.env` files, or `service_account.json` credentials to version control.

---

## Testing and Validation

Verified on the codebase:

| Suite / Check | Command | Observed Result |
| :--- | :--- | :--- |
| Web catalog validation (`sar-Madness`) | `cd sar-Madness/sar-Madness && node scripts/validate-catalog.mjs` | **6 / 6 valid cases** (`Valid catalog (100% real data)`) |
| Search & coordinate utility tests (`sar-Madness`) | `cd sar-Madness/sar-Madness && node --test tests/placeSearch.test.mjs` | **3 passed** |
| Biomass unit tests (`biomasa`) | `cd biomasa && py -B -m pytest -p no:cacheprovider -q` | **8 passed** |
| Glacier unit tests (`hola`) | `cd hola && py -B -m pytest -p no:cacheprovider -q` | **44 passed** |
| Wildfire unit tests (`incendios`) | `cd incendios && py -B -m pytest -p no:cacheprovider -q` | **27 passed** |

---

## AI / Super Intelligence Transparency

* LLM/AI coding assistants were used during development solely to accelerate software implementation, refactoring, debugging, and technical documentation drafting/translation.
* Problem conception, architectural design, sensor selection, and physical/statistical formulations are authored by the team.
* **At runtime**, both the `sar-Madness` viewer and the three pipelines (`biomasa`, `hola`, `incendios`) operate **100% deterministically** on satellite data and explicit physical models, with zero generative inference or synthetic imagery synthesis (`torch>=2.4` is listed only under optional metadata in `incendios/pyproject.toml` and is unused in published cases).

---

## Team & Contributors

Developed collaboratively by **Delta Madness**:

* **Luis Emiliano Castro Reza** — [@Humol-e](https://github.com/Humol-e)
* **Raúl Isaac Santos Ruiz** — [@Peanut-Butter-Arc](https://github.com/Peanut-Butter-Arc)
* **Karla Yelitza Figueroa Garcia** — [@karlayelitza020705-ui](https://github.com/karlayelitza020705-ui)
* **Aaron Fernandez Lizarraga** — `[PENDING — INSERT GITHUB PROFILE LINK]`
* **Liv Nicole Gay De la O** — `[PENDING — INSERT GITHUB PROFILE LINK]`

---

## License

This project is released under the **MIT License** — see [`LICENSE`](./LICENSE) for full terms. Third-party algorithm files under [`NISAR_Biomass/`](./NISAR_Biomass) remain governed by [`NISAR_Biomass/LICENSE`](./NISAR_Biomass/LICENSE) (Apache License 2.0).
