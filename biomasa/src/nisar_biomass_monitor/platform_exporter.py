"""Exporter of NISAR L3 AGB physical products to the sar-Madness 3D viewer (Schema v2)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import contourpy
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from .config import NisarAtbdParameters, SiteDefinition
from .geo_utils import (
    build_utm_to_wgs84_remap,
    reproject_utm_to_wgs84_grid,
    utm_bounds_to_wgs84,
    utm_to_lonlat,
    write_wgs84_geotiff,
)
from .nisar_atbd import NisarInversionResult
from .s3_source import LenoReferenceLayers, ResampledGcovScene

# Official NLCD palette for LENO_LULC
_NLCD_COLORS: dict[int, tuple[int, int, int, int]] = {
    11: (2, 132, 199, 225),   # Open Water
    12: (224, 242, 254, 225), # Perennial Ice/Snow
    21: (203, 213, 225, 190), # Developed Open Space
    22: (148, 163, 184, 210), # Developed Low
    23: (100, 116, 139, 225), # Developed Med
    24: (71, 85, 105, 235),   # Developed High
    31: (214, 211, 209, 200), # Barren
    41: (74, 222, 128, 220),  # Deciduous Forest
    42: (21, 128, 61, 230),   # Evergreen Forest
    43: (34, 197, 94, 225),   # Mixed Forest
    52: (163, 230, 53, 205),  # Shrub/Scrub
    71: (217, 249, 157, 195), # Grassland/Herbaceous
    81: (250, 204, 21, 205),  # Pasture/Hay
    82: (202, 138, 4, 215),   # Cultivated Crops
    90: (13, 148, 136, 230),  # Woody Wetlands
    95: (20, 184, 166, 215),  # Emergent Herbaceous Wetlands
}


@dataclass(frozen=True)
class ExportedCaseSummary:
    """Export summary for `sar-Madness/public/data/cases/<case-id>`."""

    case_id: str
    case_dir: Path
    case_json_path: Path
    catalog_path: Path
    metrics: dict[str, Any]


def encode_gray8_png(
    arr: np.ndarray,
    *,
    vmin: float,
    vmax: float,
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Encode a float physical matrix to uint8 `[1..255]`, reserving `0` for transparent nodata."""
    data = np.asarray(arr, dtype=np.float64)
    finite = np.isfinite(data)
    if valid_mask is not None:
        finite = finite & np.asarray(valid_mask, dtype=bool)
    span = max(float(vmax - vmin), 1e-9)
    norm = np.clip((np.where(finite, data, vmin) - vmin) / span, 0.0, 1.0)
    px = np.round(norm * 254.0 + 1.0).astype(np.uint8)
    return np.where(finite, px, np.uint8(0))


def _save_gray8_png(gray: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray.astype(np.uint8), mode="L").save(path, format="PNG", optimize=True)
    return path


def _save_rgba_png(rgba: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba.astype(np.uint8), mode="RGBA").save(path, format="PNG", optimize=True)
    return path


def _render_nlcd_rgba(nlcd_wgs84: np.ndarray) -> np.ndarray:
    h, w = nlcd_wgs84.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    for code, color in _NLCD_COLORS.items():
        mask = nlcd_wgs84 == code
        if np.any(mask):
            rgba[mask] = color
    return rgba


def _render_disturbance_rgba(dist_wgs84: np.ndarray) -> np.ndarray:
    h, w = dist_wgs84.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    mask = dist_wgs84 > 0
    if np.any(mask):
        rgba[mask] = (244, 63, 94, 235)
    return rgba


def _ring_area_km2(poly: Polygon) -> float:
    _, lat_c = poly.centroid.x, poly.centroid.y
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * max(math.cos(math.radians(lat_c)), 0.1)
    return float(poly.area * km_per_deg_lon * km_per_deg_lat)


def extract_mask_polygons_geojson(
    binary_mask_wgs84: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    min_area_km2: float = 0.03,
    label_name: str = "disturbance",
    source_name: str = "NISAR L3 AGB",
) -> dict[str, Any]:
    """Extract EPSG:4326 GeoJSON polygons from a binary mask using `contourpy`."""
    mask = np.asarray(binary_mask_wgs84, dtype=np.float64)
    h, w = mask.shape
    west, south, east, north = bounds_wgs84
    padded = np.pad(mask, 1, mode="constant", constant_values=0.0)
    dx = (east - west) / float(w)
    dy = (north - south) / float(h)
    xs = west + (np.arange(w + 2, dtype=np.float64) - 0.5) * dx
    ys = north - (np.arange(h + 2, dtype=np.float64) - 0.5) * dy

    cg = contourpy.contour_generator(x=xs, y=ys, z=padded, line_type="SeparateCode", fill_type="OuterOffset")
    filled = cg.filled(0.5, 1.5)

    geoms: list[Polygon] = []
    if len(filled) == 2:
        pts_list, offsets_list = filled
        for pts, offs in zip(pts_list, offsets_list):
            if len(offs) < 2:
                continue
            exterior = pts[offs[0] : offs[1]]
            holes = [pts[offs[i] : offs[i + 1]] for i in range(1, len(offs) - 1)]
            if len(exterior) < 3:
                continue
            poly = Polygon(exterior, holes)
            if not poly.is_valid:
                poly = poly.buffer(0.0)
            if not poly.is_empty and poly.area > 0:
                if poly.geom_type == "MultiPolygon":
                    geoms.extend(p for p in poly.geoms if p.area > 0)
                elif poly.geom_type == "Polygon":
                    geoms.append(poly)

    kept: list[Polygon] = []
    if geoms:
        merged = unary_union(geoms)
        candidates = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        for geom in candidates:
            if geom.geom_type == "Polygon" and _ring_area_km2(geom) >= min_area_km2:
                simp = geom.simplify(0.0002, preserve_topology=True)
                if not simp.is_empty:
                    kept.append(simp)

    kept.sort(key=lambda g: g.area, reverse=True)
    features = []
    for idx, poly in enumerate(kept, start=1):
        area_km2 = round(_ring_area_km2(poly), 4)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"{label_name}-{idx}",
                    "source": source_name,
                    "area_km2": area_km2,
                    "area_ha": round(area_km2 * 100.0, 2),
                },
                "geometry": mapping(poly),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_aoi_geojson(site: SiteDefinition) -> dict[str, Any]:
    """Build the study area polygon by projecting the 4 UTM corners to WGS84."""
    xmin, ymin, xmax, ymax = site.roi_bounds_utm
    corners_x = np.array([xmin, xmax, xmax, xmin, xmin], dtype=np.float64)
    corners_y = np.array([ymax, ymax, ymin, ymin, ymax], dtype=np.float64)
    lons, lats = utm_to_lonlat(corners_x, corners_y, site.epsg)
    ring = [[round(float(lo), 6), round(float(la), 6)] for lo, la in zip(lons, lats)]
    area_km2 = round(((xmax - xmin) * (ymax - ymin)) / 1e6, 2)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": f"{site.site_code.lower()}-aoi",
                    "name": f"{site.site_code} Study Area (100 m / 1 ha)",
                    "area_km2": area_km2,
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }


def register_case_in_catalog_v2(catalog_path: Path, case_rel_path: str) -> None:
    """Register `case_rel_path` in `public/data/catalog.json`, preserving existing cases."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog_path.is_file():
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        cases = list(data.get("cases", []))
    else:
        cases = []
    if case_rel_path not in cases:
        cases.append(case_rel_path)
    payload = {"schemaVersion": 2, "cases": cases}
    catalog_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_masked_mean(arr: np.ndarray, mask: np.ndarray, digits: int) -> float | None:
    if not np.any(mask):
        return None
    vals = arr[mask]
    finite = np.isfinite(vals)
    if not np.any(finite):
        return None
    return round(float(np.mean(vals[finite])), digits)


def export_biomass_case_to_madness(
    *,
    site: SiteDefinition,
    params: NisarAtbdParameters,
    pre_scenes: list[ResampledGcovScene],
    post_scenes: list[ResampledGcovScene],
    pre_inv: NisarInversionResult,
    post_inv: NisarInversionResult,
    refs: LenoReferenceLayers,
    madness_root: Path,
    loss_threshold_mgha: float = -40.0,
<<<<<<< HEAD
=======
    series_scenes: list[tuple[ResampledGcovScene, NisarInversionResult]] | None = None,
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
) -> ExportedCaseSummary:
    """Export WGS84 layers (PNG + GeoTIFF + GeoJSON + case.json v2 + provenance.json)."""
    case_dir = madness_root / "public" / "data" / "cases" / site.case_id
    geotiff_dir = case_dir / "geotiffs"
    case_dir.mkdir(parents=True, exist_ok=True)
    geotiff_dir.mkdir(parents=True, exist_ok=True)

    bounds_wgs84 = utm_bounds_to_wgs84(site.roi_bounds_utm, site.epsg)
    extent = [round(v, 6) for v in bounds_wgs84]
    w_out = site.roi_width_100m * 2   # 420 px (~50 m in WGS84 to smooth UTM convergence)
    h_out = site.roi_height_100m * 2  # 386 px

    delta_agb_utm = np.where(
        np.isfinite(pre_inv.agb_mgha) & np.isfinite(post_inv.agb_mgha),
        post_inv.agb_mgha - pre_inv.agb_mgha,
        np.nan,
    ).astype(np.float32)

    # Precompute UTM 16N -> regular EPSG:4326 grid remap once across all layers of the same ROI grid.
    utm_remap = build_utm_to_wgs84_remap(
        pre_inv.agb_mgha.shape,
        site.roi_bounds_utm,
        site.epsg,
        width=w_out,
        height=h_out,
        bounds_wgs84=bounds_wgs84,
    )
    agb_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.agb_mgha, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    agb_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.agb_mgha, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    agb_delta_w, _ = reproject_utm_to_wgs84_grid(
        delta_agb_utm, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    hv_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.hv_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    hv_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.hv_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    hh_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.hh_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    hh_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.hh_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84, remap=utm_remap
    )
    nlcd_w, _ = reproject_utm_to_wgs84_grid(
        refs.nlcd_lulc,
        site.roi_bounds_utm,
        site.epsg,
        width=w_out,
        height=h_out,
        bounds_wgs84=bounds_wgs84,
        nearest=True,
        remap=utm_remap,
    )
    planet_w, _ = reproject_utm_to_wgs84_grid(
        refs.planet_disturbance,
        site.roi_bounds_utm,
        site.epsg,
        width=w_out,
        height=h_out,
        bounds_wgs84=bounds_wgs84,
        nearest=True,
        remap=utm_remap,
    )

    # Viewer PNGs (gray8 with real physical ranges + rgba for categorical overlays)
    _save_gray8_png(encode_gray8_png(agb_pre_w, vmin=0.0, vmax=220.0), case_dir / "agb_pre.png")
    _save_gray8_png(encode_gray8_png(agb_post_w, vmin=0.0, vmax=220.0), case_dir / "agb_post.png")
    _save_gray8_png(encode_gray8_png(agb_delta_w, vmin=-120.0, vmax=60.0), case_dir / "agb_delta.png")
    _save_gray8_png(encode_gray8_png(hv_pre_w, vmin=-22.0, vmax=-6.0), case_dir / "hv_pre.png")
    _save_gray8_png(encode_gray8_png(hv_post_w, vmin=-22.0, vmax=-6.0), case_dir / "hv_post.png")
    _save_gray8_png(encode_gray8_png(hh_pre_w, vmin=-16.0, vmax=0.0), case_dir / "hh_pre.png")
    _save_gray8_png(encode_gray8_png(hh_post_w, vmin=-16.0, vmax=0.0), case_dir / "hh_post.png")
    _save_rgba_png(_render_nlcd_rgba(nlcd_w), case_dir / "lulc_nlcd.png")
    _save_rgba_png(_render_disturbance_rgba(planet_w), case_dir / "planet_disturbance.png")

<<<<<<< HEAD
=======
    primary_pre_tag = pre_scenes[-1].date_tag
    primary_post_tag = post_scenes[-1].date_tag
    ordered_series = sorted(series_scenes or [], key=lambda pair: pair[0].date_tag)

    def _build_series_frames(
        var_key: str,
        pre_rel: str,
        post_rel: str,
        prefix: str,
        label_prefix: str,
        arr_getter: Any,
        vmin: float,
        vmax: float,
    ) -> list[dict[str, Any]]:
        if not ordered_series:
            return [
                {
                    "id": "pre",
                    "label": f"{label_prefix}Before · {pre_scenes[0].acquisition_iso[:10] if len(pre_scenes) == 1 else f'{pre_scenes[0].acquisition_iso[:10]} → {pre_scenes[-1].acquisition_iso[:10]}'}",
                    "phase": "before",
                    "datetime": pre_scenes[-1].acquisition_iso,
                    "files": {var_key: pre_rel},
                },
                {
                    "id": "post",
                    "label": f"{label_prefix}After · {post_scenes[-1].acquisition_iso[:10]}",
                    "phase": "after",
                    "datetime": post_scenes[-1].acquisition_iso,
                    "files": {var_key: post_rel},
                },
            ]
        frames_out: list[dict[str, Any]] = []
        for sc, inv in ordered_series:
            d_str = sc.acquisition_iso[:10]
            if sc.date_tag == primary_pre_tag:
                frames_out.append(
                    {
                        "id": "pre",
                        "label": f"{label_prefix}Before · {d_str}",
                        "phase": "before",
                        "datetime": sc.acquisition_iso,
                        "files": {var_key: pre_rel},
                    }
                )
            elif sc.date_tag == primary_post_tag:
                frames_out.append(
                    {
                        "id": "post",
                        "label": f"{label_prefix}After · {d_str}",
                        "phase": "after",
                        "datetime": sc.acquisition_iso,
                        "files": {var_key: post_rel},
                    }
                )
            else:
                fname = f"{prefix}_{sc.date_tag}.png"
                w_arr, _ = reproject_utm_to_wgs84_grid(
                    arr_getter(inv),
                    site.roi_bounds_utm,
                    site.epsg,
                    width=w_out,
                    height=h_out,
                    bounds_wgs84=bounds_wgs84,
                    remap=utm_remap,
                )
                _save_gray8_png(encode_gray8_png(w_arr, vmin=vmin, vmax=vmax), case_dir / fname)
                frames_out.append(
                    {
                        "id": f"d{sc.date_tag}",
                        "label": f"{label_prefix}{d_str}",
                        "phase": "series",
                        "datetime": sc.acquisition_iso,
                        "files": {var_key: f"data/cases/{site.case_id}/{fname}"},
                    }
                )
        return frames_out

    agb_frames = _build_series_frames(
        "agb",
        f"data/cases/{site.case_id}/agb_pre.png",
        f"data/cases/{site.case_id}/agb_post.png",
        "agb",
        "",
        lambda inv: inv.agb_mgha,
        0.0,
        220.0,
    )
    hv_frames = _build_series_frames(
        "hv_db",
        f"data/cases/{site.case_id}/hv_pre.png",
        f"data/cases/{site.case_id}/hv_post.png",
        "hv",
        "HV ",
        lambda inv: inv.hv_db,
        -22.0,
        -6.0,
    )
    hh_frames = _build_series_frames(
        "hh_db",
        f"data/cases/{site.case_id}/hh_pre.png",
        f"data/cases/{site.case_id}/hh_post.png",
        "hh",
        "HH ",
        lambda inv: inv.hh_db,
        -16.0,
        0.0,
    )

>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
    # GeoJSON vectors
    aoi_gj = build_aoi_geojson(site)
    (case_dir / "aoi.geojson").write_text(json.dumps(aoi_gj, indent=2), encoding="utf-8")

    loss_mask_w = np.isfinite(agb_delta_w) & (agb_delta_w <= loss_threshold_mgha)
    nisar_loss_gj = extract_mask_polygons_geojson(
        loss_mask_w.astype(np.uint8),
        bounds_wgs84,
        min_area_km2=0.03,
        label_name="nisar-loss",
        source_name="NISAR L3 ΔAGB <= -40 Mg/ha",
    )
    (case_dir / "biomass_loss_nisar.geojson").write_text(json.dumps(nisar_loss_gj, indent=2), encoding="utf-8")

    planet_gj = extract_mask_polygons_geojson(
        (planet_w > 0).astype(np.uint8),
        bounds_wgs84,
        min_area_km2=0.02,
        label_name="planet-dist",
        source_name="Planet Optical Reference 2022–2023",
    )
    (case_dir / "planet_disturbance.geojson").write_text(json.dumps(planet_gj, indent=2), encoding="utf-8")

    # Real downloadable EPSG:4326 GeoTIFFs
    geotiff_specs = [
        ("agb-pre", "NISAR Aboveground Biomass Pre (Mg/ha) — EPSG:4326", "nisar_agb", "before", "agb_pre_wgs84.tif", agb_pre_w, -9999.0),
        ("agb-post", "NISAR Aboveground Biomass Post (Mg/ha) — EPSG:4326", "nisar_agb", "after", "agb_post_wgs84.tif", agb_post_w, -9999.0),
        ("agb-delta", "Biomass Change ΔAGB (Mg/ha) — EPSG:4326", "agb_change", "change", "agb_delta_wgs84.tif", agb_delta_w, -9999.0),
        ("hv-pre", "SAR L-band HV Pre (dB) — EPSG:4326", "nisar_hv", "before", "hv_pre_db_wgs84.tif", hv_pre_w, -9999.0),
        ("hv-post", "SAR L-band HV Post (dB) — EPSG:4326", "nisar_hv", "after", "hv_post_db_wgs84.tif", hv_post_w, -9999.0),
        ("hh-pre", "SAR L-band HH Pre (dB) — EPSG:4326", "nisar_hh", "before", "hh_pre_db_wgs84.tif", hh_pre_w, -9999.0),
        ("hh-post", "SAR L-band HH Post (dB) — EPSG:4326", "nisar_hh", "after", "hh_post_db_wgs84.tif", hh_post_w, -9999.0),
        ("nlcd-lulc", "LENO NLCD Land Cover (uint8 classes) — EPSG:4326", "lulc_fnf", "before", "nlcd_lulc_wgs84.tif", nlcd_w, 0),
    ]
    downloads: list[dict[str, Any]] = []
    for dl_id, label, ds_id, phase, fname, arr_data, nodata_val in geotiff_specs:
        out_tif = write_wgs84_geotiff(geotiff_dir / fname, arr_data, bounds_wgs84, nodata=nodata_val)
        downloads.append(
            {
                "id": dl_id,
                "label": label,
                "datasetId": ds_id,
                "phase": phase,
                "format": "geotiff",
                "url": f"data/cases/{site.case_id}/geotiffs/{fname}",
                "sizeBytes": int(out_tif.stat().st_size),
            }
        )

    # Scientific metrics computed on exact 100 m metric grid (1 ha/pixel = 0.01 km²/pixel)
    ok_both = np.isfinite(pre_inv.agb_mgha) & np.isfinite(post_inv.agb_mgha)
    dist_px = ok_both & (refs.planet_disturbance == 1)
    intact_px = ok_both & (refs.planet_disturbance == 0) & (refs.classification_100m == 1)
    loss_px = ok_both & (delta_agb_utm <= loss_threshold_mgha)
    cell_km2 = (site.target_resolution_m ** 2) / 1e6

    pre_start = pre_scenes[0].acquisition_iso[:10]
    post_end = post_scenes[-1].acquisition_iso[:10]
    metrics = {
        "preWindow": pre_start if len(pre_scenes) == 1 else f"{pre_scenes[0].acquisition_iso[:10]} → {pre_scenes[-1].acquisition_iso[:10]}",
        "postWindow": post_end if len(post_scenes) == 1 else f"{post_scenes[0].acquisition_iso[:10]} → {post_scenes[-1].acquisition_iso[:10]}",
        "observedKm2": round(float(site.roi_width_100m * site.roi_height_100m) * cell_km2, 2),
        "forestAreaKm2": round(float(np.sum(ok_both)) * cell_km2, 2),
        "agbPreMean": _safe_masked_mean(pre_inv.agb_mgha, ok_both, 1),
        "agbPostMean": _safe_masked_mean(post_inv.agb_mgha, ok_both, 1),
        "agbIntactPre": _safe_masked_mean(pre_inv.agb_mgha, intact_px, 1),
        "agbIntactPost": _safe_masked_mean(post_inv.agb_mgha, intact_px, 1),
<<<<<<< HEAD
        "agbDisturbedPre": _safe_masked_mean(pre_inv.agb_mgha, dist_px, 1),
        "agbDisturbedPost": _safe_masked_mean(post_inv.agb_mgha, dist_px, 1),
=======
        "agbIntactDeltaMean": _safe_masked_mean(delta_agb_utm, intact_px, 1),
        "agbDisturbedPre": _safe_masked_mean(pre_inv.agb_mgha, dist_px, 1),
        "agbDisturbedPost": _safe_masked_mean(post_inv.agb_mgha, dist_px, 1),
        "agbDisturbedDeltaMean": _safe_masked_mean(delta_agb_utm, dist_px, 1),
        "planetDetectedLossPct": (
            round(float(np.sum(dist_px & (delta_agb_utm <= -15.0))) / float(np.sum(dist_px)) * 100.0, 1)
            if np.any(dist_px)
            else 0.0
        ),
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
        "biomassLossKm2": round(float(np.sum(loss_px)) * cell_km2, 2),
        "planetDistKm2": round(float(np.sum(refs.planet_disturbance == 1)) * cell_km2, 2),
        # 1 pixel at 100 m = 1 ha -> sum(ΔAGB Mg/ha) over loss / 1000 = Gg (kt) biomass, * 0.47 = Gg C
        "carbonLossGg": round(abs(float(np.sum(delta_agb_utm[loss_px]))) * 0.47 / 1000.0, 2) if np.any(loss_px) else 0.0,
        "hvPreDb": _safe_masked_mean(pre_inv.hv_db, ok_both, 2),
        "hvPostDb": _safe_masked_mean(post_inv.hv_db, ok_both, 2),
        "modelRmseDb": _safe_masked_mean(pre_inv.rmse_db, ok_both, 2),
    }
<<<<<<< HEAD
=======
    if ordered_series:
        series_means = {
            sc.acquisition_iso[:10]: _safe_masked_mean(inv.agb_mgha, ok_both, 1)
            for sc, inv in ordered_series
        }
        metrics["agbSeriesMeans"] = series_means
        metrics["seriesSceneCount"] = len(ordered_series)
        for sc, inv in ordered_series:
            if sc.date_tag.startswith("2021"):
                metrics["agb2021Mean"] = _safe_masked_mean(inv.agb_mgha, ok_both, 1)
            elif sc.date_tag.startswith("2024"):
                metrics["agb2024Mean"] = _safe_masked_mean(inv.agb_mgha, ok_both, 1)
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7

    center_lon = round((extent[0] + extent[2]) / 2.0, 5)
    center_lat = round((extent[1] + extent[3]) / 2.0, 5)
    pre_dt = pre_scenes[-1].acquisition_iso
    post_dt = post_scenes[-1].acquisition_iso

    case_doc = {
        "schemaVersion": 2,
        "id": site.case_id,
        "status": "real",
        "simulated": False,
        "category": "biomass",
        "title": site.title,
        "region": f"{site.region} · {site.country}",
        "description": (
            f"{site.subtitle}. Physical L3 ATBD inversion on L-band GCOV (20 m → 100 m / 1 ha) "
            "with NLCD land cover and PlanetScope 2022–2023 optical validation."
        ),
        "view": {
            "center": [center_lon, center_lat],
            "zoom": 11.1,
            "pitch": 42,
            "bearing": 8,
        },
        "sources": [
<<<<<<< HEAD
            "NASA NISAR L2 Simulated L-band GCOV (UAVSAR/ALOS-2 · Public LENO S3 Site)",
=======
            "NASA NISAR L2 Simulated L-band GCOV (UAVSAR/ALOS-2 · Official NASA EBD ATBD Validation Dataset, 2021–2024)",
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
            "NISAR L3 AGB ATBD Physical Model (calibrated config.ini · weighted HH/HV dB inversion)",
            "USGS NLCD (LENO_LULC 30 m Albers) & Planet Optical Validation (2022-09-30 → 2023-09-18)",
        ],
        "metrics": metrics,
        "datasets": [
            {
                "id": "nisar_agb",
                "name": "NISAR L3 Aboveground Biomass (AGB)",
                "kind": "raster",
                "status": "real",
<<<<<<< HEAD
                "platform": "NISAR (Simulated UAVSAR/ALOS-2)",
=======
                "platform": "NISAR L-SAR (Simulated UAVSAR/ALOS-2)",
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
                "sensor": "L-SAR GCOV",
                "provider": "Wasabi S3 Public / NISAR ATBD",
                "product": "L3 Aboveground Biomass (100 m / 1 ha)",
                "attribution": "NASA-ISRO NISAR ATBD",
                "extent": extent,
                "crs": "EPSG:4326",
                "resolution": {"value": 100, "unit": "m"},
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "render": {"opacity": 0.9, "resampling": "linear"},
                "defaultVisible": True,
                "variables": [
                    {
                        "id": "agb",
                        "label": "Aboveground Biomass (AGB)",
                        "quantity": "aboveground_biomass",
                        "polarization": "HH+HV",
                        "unit": "Mg/ha",
                        "legend": {
                            "min": 0.0,
                            "max": 220.0,
                            "colors": ["#1e1b4b", "#1d4ed8", "#0d9488", "#22c55e", "#eab308", "#fef08a"],
                            "nodata_label": "Transparent = water / urban / no data",
                        },
                    }
                ],
<<<<<<< HEAD
                "frames": [
                    {
                        "id": "pre",
                        "label": f"Before · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"agb": f"data/cases/{site.case_id}/agb_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"After · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"agb": f"data/cases/{site.case_id}/agb_post.png"},
                    },
                ],
=======
                "frames": agb_frames,
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
            },
            {
                "id": "nisar_hv",
                "name": "SAR L-band HV · Canopy Volume",
                "kind": "raster",
                "status": "real",
<<<<<<< HEAD
                "platform": "NISAR (Simulated)",
=======
                "platform": "NISAR L-SAR (Simulated)",
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
                "sensor": "L-SAR GCOV",
                "provider": "Wasabi S3 Public / NISAR ATBD",
                "product": "Gamma0 HV (100 m mean)",
                "attribution": "NASA NISAR ATBD",
                "extent": extent,
                "crs": "EPSG:4326",
                "resolution": {"value": 100, "unit": "m"},
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "render": {"opacity": 0.88, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "hv_db",
                        "label": "Gamma0 HV",
                        "quantity": "backscatter",
                        "polarization": "HV",
                        "unit": "dB",
                        "legend": {
                            "min": -22.0,
                            "max": -6.0,
                            "colors": ["#090d16", "#334155", "#64748b", "#cbd5e1", "#f8fafc"],
                        },
                    }
                ],
<<<<<<< HEAD
                "frames": [
                    {
                        "id": "pre",
                        "label": f"HV Before · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"hv_db": f"data/cases/{site.case_id}/hv_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"HV After · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"hv_db": f"data/cases/{site.case_id}/hv_post.png"},
                    },
                ],
=======
                "frames": hv_frames,
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
            },
            {
                "id": "nisar_hh",
                "name": "SAR L-band HH · Trunk–Ground",
                "kind": "raster",
                "status": "real",
<<<<<<< HEAD
                "platform": "NISAR (Simulated)",
=======
                "platform": "NISAR L-SAR (Simulated)",
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
                "sensor": "L-SAR GCOV",
                "provider": "Wasabi S3 Public / NISAR ATBD",
                "product": "Gamma0 HH (100 m mean)",
                "attribution": "NASA NISAR ATBD",
                "extent": extent,
                "crs": "EPSG:4326",
                "resolution": {"value": 100, "unit": "m"},
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "render": {"opacity": 0.88, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "hh_db",
                        "label": "Gamma0 HH",
                        "quantity": "backscatter",
                        "polarization": "HH",
                        "unit": "dB",
                        "legend": {
                            "min": -16.0,
                            "max": 0.0,
                            "colors": ["#090d16", "#3b2f2f", "#92400e", "#f59e0b", "#fef3c7"],
                        },
                    }
                ],
<<<<<<< HEAD
                "frames": [
                    {
                        "id": "pre",
                        "label": f"HH Before · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"hh_db": f"data/cases/{site.case_id}/hh_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"HH After · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"hh_db": f"data/cases/{site.case_id}/hh_post.png"},
                    },
                ],
=======
                "frames": hh_frames,
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
            },
            {
                "id": "agb_change",
                "name": "Net Biomass Change (ΔAGB)",
                "kind": "raster",
                "status": "real",
                "platform": "NISAR (Simulated)",
                "sensor": "L-SAR L3 Diff",
                "provider": "NISAR_Biomass L3 Pipeline",
                "product": "ΔAGB = After − Before (Mg/ha)",
                "attribution": "DeltasMadness2 / NISAR_Biomass",
                "extent": extent,
                "crs": "EPSG:4326",
                "resolution": {"value": 100, "unit": "m"},
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "render": {"opacity": 0.9, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "delta_agb",
                        "label": "ΔAGB (After − Before)",
                        "quantity": "biomass_change",
                        "polarization": "HH+HV",
                        "unit": "Mg/ha",
                        "legend": {
                            "min": -120.0,
                            "max": 60.0,
                            "colors": ["#991b1b", "#ef4444", "#f97316", "#64748b", "#22c55e", "#15803d"],
                            "stops": [
                                [-120.0, "#991b1b"],
                                [-60.0, "#ef4444"],
                                [-20.0, "#f97316"],
                                [0.0, "#64748b"],
                                [25.0, "#22c55e"],
                                [60.0, "#15803d"],
                            ],
                            "nodata_label": "Red = loss · Gray = 0 · Green = gain",
                        },
                    }
                ],
                "frames": [
                    {
                        "id": "change",
                        "label": f"ΔAGB ({pre_start} → {post_end})",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"delta_agb": f"data/cases/{site.case_id}/agb_delta.png"},
                    }
                ],
            },
            {
                "id": "lulc_fnf",
                "name": "NLCD Land Cover & Planet Reference",
                "kind": "raster",
                "status": "real",
                "platform": "USGS NLCD / PlanetScope",
                "sensor": "Multispectral",
                "provider": "Wasabi S3 Public / NEON LENO",
                "product": "LENO LULC + Disturbance Validation",
                "attribution": "USGS / Planet Labs Validation",
                "extent": extent,
                "crs": "EPSG:4326",
                "resolution": {"value": 100, "unit": "m"},
                "source": {"type": "image", "encoding": "rgba"},
                "render": {"opacity": 0.85, "resampling": "nearest"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "lulc_dist",
                        "label": "NLCD Land Cover / Planet Disturbance",
                        "quantity": "land_cover",
                        "polarization": None,
                        "unit": "class",
                    }
                ],
                "frames": [
                    {
                        "id": "pre",
                        "label": "Before · LENO NLCD Land Cover",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"lulc_dist": f"data/cases/{site.case_id}/lulc_nlcd.png"},
                    },
                    {
                        "id": "post",
                        "label": "After · Planet Reference Disturbance",
                        "phase": "after",
                        "datetime": "2023-09-18T00:00:00Z",
                        "files": {"lulc_dist": f"data/cases/{site.case_id}/planet_disturbance.png"},
                    },
                ],
            },
        ],
        "vectors": [
            {
                "id": "aoi",
                "name": "LENO Study Area (405.30 km²)",
                "status": "real",
                "simulated": False,
                "type": "line",
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/aoi.geojson"},
                "paint": {"line-color": "#38bdf8", "line-width": 1.8, "line-dasharray": [3, 2]},
            },
            {
                "id": "biomass_loss_nisar",
                "name": "NISAR Severe Loss (ΔAGB ≤ −40 Mg/ha)",
                "status": "real",
                "simulated": False,
                "type": "fill",
                "defaultVisible": False,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/biomass_loss_nisar.geojson"},
                "paint": {"fill-color": "#f97316", "fill-opacity": 0.45, "fill-outline-color": "#fdba74"},
                "legend": {
                    "title": "NISAR L3 Detection",
                    "items": [{"color": "#f97316", "label": "ΔAGB ≤ −40 Mg/ha"}],
                },
                "popupFields": [
                    ["area_km2", "Loss area", "km²"],
                    ["area_ha", "Surface", "ha"],
                ],
            },
            {
                "id": "planet_disturbance_vec",
                "name": "Planet Optical Validation (2022–2023)",
                "status": "real",
                "simulated": False,
                "type": "fill",
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/planet_disturbance.geojson"},
                "paint": {"fill-color": "#f43f5e", "fill-opacity": 0.55, "fill-outline-color": "#ffe4e6"},
                "legend": {
                    "title": "Planet Optical Reference",
                    "items": [{"color": "#f43f5e", "label": "Confirmed disturbance"}],
                },
                "popupFields": [
                    ["area_km2", "Disturbed area", "km²"],
                    ["area_ha", "Surface", "ha"],
                ],
            },
        ],
        "downloads": downloads,
    }

    case_json_path = case_dir / "case.json"
    case_json_path.write_text(json.dumps(case_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    provenance = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "pipeline": "nisar_biomass_monitor (based on DeltasMadness2/NISAR_Biomass)",
        "siteCode": site.site_code,
        "epsgNative": site.epsg,
        "resolutionMeters": site.target_resolution_m,
        "roiBoundsUtm": list(site.roi_bounds_utm),
        "boundsWgs84": extent,
        "nisarConfig": params.model_dump(),
        "preScenes": [{"dateTag": s.date_tag, "iso": s.acquisition_iso, "url": s.source_url} for s in pre_scenes],
        "postScenes": [{"dateTag": s.date_tag, "iso": s.acquisition_iso, "url": s.source_url} for s in post_scenes],
<<<<<<< HEAD
=======
        "seriesScenes": [
            {"dateTag": sc.date_tag, "iso": sc.acquisition_iso, "url": sc.source_url}
            for sc, _ in ordered_series
        ],
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
        "metrics": metrics,
        "vectorCounts": {
            "nisarLossPolygons": len(nisar_loss_gj["features"]),
            "planetDisturbancePolygons": len(planet_gj["features"]),
        },
    }
    (case_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    catalog_path = madness_root / "public" / "data" / "catalog.json"
    register_case_in_catalog_v2(catalog_path, f"data/cases/{site.case_id}/case.json")

    return ExportedCaseSummary(
        case_id=site.case_id,
        case_dir=case_dir,
        case_json_path=case_json_path,
        catalog_path=catalog_path,
        metrics=metrics,
    )
