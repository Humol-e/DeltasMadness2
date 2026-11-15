# biomasa (`nisar-biomass-monitor`)

Aboveground biomass (**AGB**, Mg/ha) pipeline built on the `NISAR_Biomass/` scientific module (NISAR L3 ATBD algorithm) and integrated into the 3D `sar-Madness` viewer.

## Scientific workflow

1. **Calibrated physical parameters**: reads `[NISAR]` directly from `../NISAR_Biomass/config/config.ini` (`A`, `B`, `C`, `alpha`, `delta`, `D`, `s` for HH and HV).
2. **Real multi-temporal S3 ingestion**: downloads or reuses locally cached (`biomasa/data/cache/`) public NISAR L2 GCOV `.h5` scenes (20 m) across the 4-year series (`2021-08-06`, `2022-09-30`, `2023-09-29`, `2024-08-30`) for the **LENO** site (Lenoir Landing, Alabama), along with `LENO_LULC.tif` (30 m NLCD) and Planet validation layers (`PLANET_VAL_FINALDIST_100M_LENO_*`).
3. **1 ha (100 m) resampling** and NISAR physical mask (`HV > 0.01`, `HV < 1`, `HH < 1`, excluding non-forest/urban/water NLCD classes `{11, 12, 22, 23, 24}`).
4. **L-band AGB inversion & Planet cross-validation**: vectorized reproduction of the forward model (`volume + double + surface`) and weighted dB optimization (`weights=(4, 1)`) across HV and HH polarizations, producing chronological `AGB` (`2021–2024`), `ΔAGB`, and `HV/HH dB` grids plus quantitative validation (`planetDetectedLossPct = 94.2%`, disturbed mean delta `-73.2 Mg/ha` vs intact `-5.7 Mg/ha`, `modelRmseDb = 0.53 dB`).
5. **`sar-Madness` export (Schema v2)**: reprojects to `EPSG:4326`, generates chronological `gray8`/`rgba` PNG frames (`d20210806`, `pre`, `post`, `d20240830`), biomass loss GeoJSON (`ΔAGB <= -40 Mg/ha`), and downloadable GeoTIFFs registered in `sar-Madness/sar-Madness/public/data/catalog.json`.

## Usage

```powershell
py -m pip install --no-deps -e biomasa
biomass-stage1 list-scenes
biomass-stage1 publish-madness
```
