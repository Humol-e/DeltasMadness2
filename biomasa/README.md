# biomasa (`nisar-biomass-monitor`)

Aboveground biomass (**AGB**, Mg/ha) pipeline built on the `NISAR_Biomass/` scientific module (NISAR L3 ATBD algorithm) and integrated into the 3D `sar-Madness` viewer.

## Scientific workflow

1. **Calibrated physical parameters**: reads `[NISAR]` directly from `../NISAR_Biomass/config/config.ini` (`A`, `B`, `C`, `alpha`, `delta`, `D`, `s` for HH and HV).
2. **Real S3 ingestion**: downloads or reuses locally cached (`biomasa/data/cache/`) public NISAR L2 GCOV `.h5` scenes (20 m) for the **LENO** site (Lenoir Landing, Alabama) along with `LENO_LULC.tif` (30 m NLCD) and Planet validation layers (`PLANET_VAL_FINALDIST_100M_LENO_*`).
3. **1 ha (100 m) resampling** and NISAR physical mask (`HV > 0.01`, `HV < 1`, `HH < 1`, excluding non-forest/urban/water NLCD classes `{11, 12, 22, 23, 24}`).
4. **L-band AGB inversion**: vectorized reproduction of the forward model (`volume + double + surface`) and weighted dB optimization (`weights=(4, 1)`) across HV and HH polarizations, producing `AGB Pre`, `AGB Post`, `ΔAGB`, and `HV/HH dB`.
5. **`sar-Madness` export (Schema v2)**: reprojects to `EPSG:4326`, generates `gray8`/`rgba` PNGs, biomass loss GeoJSON (`ΔAGB <= -40 Mg/ha`), and downloadable GeoTIFFs registered in `sar-Madness/sar-Madness/public/data/catalog.json`.

## Usage

```powershell
py -m pip install --no-deps -e biomasa
biomass-stage1 list-scenes
biomass-stage1 publish-madness
```
