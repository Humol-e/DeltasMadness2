"""Exportador de productos físicos NISAR L3 AGB hacia el visor 3D sar-Madness (schema v2)."""

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
from .geo_utils import reproject_utm_to_wgs84_grid, utm_bounds_to_wgs84, utm_to_lonlat, write_wgs84_geotiff
from .nisar_atbd import NisarInversionResult
from .s3_source import LenoReferenceLayers, ResampledGcovScene

# Paleta oficial NLCD para LENO_LULC
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
    """Resumen de exportación hacia `sar-Madness/public/data/cases/<case-id>`."""

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
    """Codifica matriz física float a uint8 `[1..255]` dejando `0` como nodata transparente."""
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
    """Extrae polígonos GeoJSON EPSG:4326 desde una máscara binaria usando `contourpy`."""
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
    """Construye el polígono del área de estudio LENO proyectando las 4 esquinas UTM a WGS84."""
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
                    "name": f"Área de estudio {site.site_code} (100 m / 1 ha)",
                    "area_km2": area_km2,
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }


def register_case_in_catalog_v2(catalog_path: Path, case_rel_path: str) -> None:
    """Registra `case_rel_path` en `public/data/catalog.json` preservando casos existentes."""
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
) -> ExportedCaseSummary:
    """Exporta capas WGS84 (PNG + GeoTIFF + GeoJSON + case.json v2 + provenance.json)."""
    case_dir = madness_root / "public" / "data" / "cases" / site.case_id
    geotiff_dir = case_dir / "geotiffs"
    case_dir.mkdir(parents=True, exist_ok=True)
    geotiff_dir.mkdir(parents=True, exist_ok=True)

    bounds_wgs84 = utm_bounds_to_wgs84(site.roi_bounds_utm, site.epsg)
    extent = [round(v, 6) for v in bounds_wgs84]
    w_out = site.roi_width_100m * 2   # 420 px (~50 m en WGS84 para suavizar convergencia UTM)
    h_out = site.roi_height_100m * 2  # 386 px

    delta_agb_utm = np.where(
        np.isfinite(pre_inv.agb_mgha) & np.isfinite(post_inv.agb_mgha),
        post_inv.agb_mgha - pre_inv.agb_mgha,
        np.nan,
    ).astype(np.float32)

    # Reproyección UTM 16N -> malla regular EPSG:4326
    agb_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.agb_mgha, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    agb_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.agb_mgha, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    agb_delta_w, _ = reproject_utm_to_wgs84_grid(
        delta_agb_utm, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    hv_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.hv_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    hv_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.hv_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    hh_pre_w, _ = reproject_utm_to_wgs84_grid(
        pre_inv.hh_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    hh_post_w, _ = reproject_utm_to_wgs84_grid(
        post_inv.hh_db, site.roi_bounds_utm, site.epsg, width=w_out, height=h_out, bounds_wgs84=bounds_wgs84
    )
    nlcd_w, _ = reproject_utm_to_wgs84_grid(
        refs.nlcd_lulc,
        site.roi_bounds_utm,
        site.epsg,
        width=w_out,
        height=h_out,
        bounds_wgs84=bounds_wgs84,
        nearest=True,
    )
    planet_w, _ = reproject_utm_to_wgs84_grid(
        refs.planet_disturbance,
        site.roi_bounds_utm,
        site.epsg,
        width=w_out,
        height=h_out,
        bounds_wgs84=bounds_wgs84,
        nearest=True,
    )

    # PNGs para visor Mapbox (gray8 con rangos físicos reales + rgba para categóricos)
    _save_gray8_png(encode_gray8_png(agb_pre_w, vmin=0.0, vmax=220.0), case_dir / "agb_pre.png")
    _save_gray8_png(encode_gray8_png(agb_post_w, vmin=0.0, vmax=220.0), case_dir / "agb_post.png")
    _save_gray8_png(encode_gray8_png(agb_delta_w, vmin=-120.0, vmax=60.0), case_dir / "agb_delta.png")
    _save_gray8_png(encode_gray8_png(hv_pre_w, vmin=-22.0, vmax=-6.0), case_dir / "hv_pre.png")
    _save_gray8_png(encode_gray8_png(hv_post_w, vmin=-22.0, vmax=-6.0), case_dir / "hv_post.png")
    _save_gray8_png(encode_gray8_png(hh_pre_w, vmin=-16.0, vmax=0.0), case_dir / "hh_pre.png")
    _save_gray8_png(encode_gray8_png(hh_post_w, vmin=-16.0, vmax=0.0), case_dir / "hh_post.png")
    _save_rgba_png(_render_nlcd_rgba(nlcd_w), case_dir / "lulc_nlcd.png")
    _save_rgba_png(_render_disturbance_rgba(planet_w), case_dir / "planet_disturbance.png")

    # Vectores GeoJSON
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

    # GeoTIFFs descargables reales EPSG:4326
    geotiff_specs = [
        ("agb-pre", "Biomasa Aérea Pre NISAR (Mg/ha) — EPSG:4326", "nisar_agb", "before", "agb_pre_wgs84.tif", agb_pre_w, -9999.0),
        ("agb-post", "Biomasa Aérea Post NISAR (Mg/ha) — EPSG:4326", "nisar_agb", "after", "agb_post_wgs84.tif", agb_post_w, -9999.0),
        ("agb-delta", "Cambio de Biomasa ΔAGB (Mg/ha) — EPSG:4326", "agb_change", "change", "agb_delta_wgs84.tif", agb_delta_w, -9999.0),
        ("hv-pre", "SAR Banda L HV Pre (dB) — EPSG:4326", "nisar_hv", "before", "hv_pre_db_wgs84.tif", hv_pre_w, -9999.0),
        ("hv-post", "SAR Banda L HV Post (dB) — EPSG:4326", "nisar_hv", "after", "hv_post_db_wgs84.tif", hv_post_w, -9999.0),
        ("hh-pre", "SAR Banda L HH Pre (dB) — EPSG:4326", "nisar_hh", "before", "hh_pre_db_wgs84.tif", hh_pre_w, -9999.0),
        ("hh-post", "SAR Banda L HH Post (dB) — EPSG:4326", "nisar_hh", "after", "hh_post_db_wgs84.tif", hh_post_w, -9999.0),
        ("nlcd-lulc", "Cobertura NLCD LENO (clases uint8) — EPSG:4326", "lulc_fnf", "before", "nlcd_lulc_wgs84.tif", nlcd_w, 0),
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

    # Métricas científicas calculadas en malla métrica exacta de 100 m (1 ha/píxel = 0.01 km²/píxel)
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
        "agbPreMean": round(float(np.nanmean(pre_inv.agb_mgha[ok_both])), 1),
        "agbPostMean": round(float(np.nanmean(post_inv.agb_mgha[ok_both])), 1),
        "agbIntactPre": round(float(np.nanmean(pre_inv.agb_mgha[intact_px])), 1) if np.any(intact_px) else None,
        "agbIntactPost": round(float(np.nanmean(post_inv.agb_mgha[intact_px])), 1) if np.any(intact_px) else None,
        "agbDisturbedPre": round(float(np.nanmean(pre_inv.agb_mgha[dist_px])), 1) if np.any(dist_px) else None,
        "agbDisturbedPost": round(float(np.nanmean(post_inv.agb_mgha[dist_px])), 1) if np.any(dist_px) else None,
        "biomassLossKm2": round(float(np.sum(loss_px)) * cell_km2, 2),
        "planetDistKm2": round(float(np.sum(refs.planet_disturbance == 1)) * cell_km2, 2),
        # 1 píxel 100m = 1 ha -> suma(ΔAGB Mg/ha) en pérdida / 1000 = Gg (kt) biomasa, * 0.47 = Gg C
        "carbonLossGg": round(abs(float(np.sum(delta_agb_utm[loss_px]))) * 0.47 / 1000.0, 2) if np.any(loss_px) else 0.0,
        "hvPreDb": round(float(np.nanmean(pre_inv.hv_db[ok_both])), 2),
        "hvPostDb": round(float(np.nanmean(post_inv.hv_db[ok_both])), 2),
        "modelRmseDb": round(float(np.nanmean(pre_inv.rmse_db[ok_both])), 2),
    }

    center_lon = round((extent[0] + extent[2]) / 2.0, 5)
    center_lat = round((extent[1] + extent[3]) / 2.0, 5)
    pre_dt = pre_scenes[-1].acquisition_iso
    post_dt = post_scenes[-1].acquisition_iso

    case_doc = {
        "schemaVersion": 2,
        "id": site.case_id,
        "status": "real",
        "simulated": False,
        "category": "biomasa",
        "title": site.title,
        "region": f"{site.region} · {site.country}",
        "description": (
            f"{site.subtitle}. Inversión física L3 ATBD sobre GCOV Banda L (20 m → 100 m / 1 ha) "
            "con cobertura NLCD y validación óptica PlanetScope 2022–2023."
        ),
        "view": {
            "center": [center_lon, center_lat],
            "zoom": 11.1,
            "pitch": 42,
            "bearing": 8,
        },
        "sources": [
            "NASA NISAR L2 Simulated GCOV Banda L (UAVSAR/ALOS-2 · Sitio LENO S3 Público)",
            "Modelo Físico NISAR L3 AGB ATBD (config.ini calibrado · inversión dB ponderada HH/HV)",
            "USGS NLCD (LENO_LULC 30 m Albers) y Validación Óptica Planet (2022-09-30 → 2023-09-18)",
        ],
        "metrics": metrics,
        "datasets": [
            {
                "id": "nisar_agb",
                "name": "Biomasa Aérea NISAR L3 (AGB)",
                "kind": "raster",
                "status": "real",
                "platform": "NISAR (Simulado UAVSAR/ALOS-2)",
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
                        "label": "Biomasa Aérea (AGB)",
                        "quantity": "aboveground_biomass",
                        "polarization": "HH+HV",
                        "unit": "Mg/ha",
                        "legend": {
                            "min": 0.0,
                            "max": 220.0,
                            "colors": ["#1e1b4b", "#1d4ed8", "#0d9488", "#22c55e", "#eab308", "#fef08a"],
                            "nodata_label": "Transparente = agua / urbano / sin cobertura",
                        },
                    }
                ],
                "frames": [
                    {
                        "id": "pre",
                        "label": f"Antes · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"agb": f"data/cases/{site.case_id}/agb_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"Después · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"agb": f"data/cases/{site.case_id}/agb_post.png"},
                    },
                ],
            },
            {
                "id": "nisar_hv",
                "name": "SAR Banda L HV · Volumen Dosel",
                "kind": "raster",
                "status": "real",
                "platform": "NISAR (Simulado)",
                "sensor": "L-SAR GCOV",
                "provider": "Wasabi S3 Public / NISAR ATBD",
                "product": "Gamma0 HV (100 m promedio)",
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
                "frames": [
                    {
                        "id": "pre",
                        "label": f"HV Antes · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"hv_db": f"data/cases/{site.case_id}/hv_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"HV Después · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"hv_db": f"data/cases/{site.case_id}/hv_post.png"},
                    },
                ],
            },
            {
                "id": "nisar_hh",
                "name": "SAR Banda L HH · Tronco–Suelo",
                "kind": "raster",
                "status": "real",
                "platform": "NISAR (Simulado)",
                "sensor": "L-SAR GCOV",
                "provider": "Wasabi S3 Public / NISAR ATBD",
                "product": "Gamma0 HH (100 m promedio)",
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
                "frames": [
                    {
                        "id": "pre",
                        "label": f"HH Antes · {pre_start}",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"hh_db": f"data/cases/{site.case_id}/hh_pre.png"},
                    },
                    {
                        "id": "post",
                        "label": f"HH Después · {post_end}",
                        "phase": "after",
                        "datetime": post_dt,
                        "files": {"hh_db": f"data/cases/{site.case_id}/hh_post.png"},
                    },
                ],
            },
            {
                "id": "agb_change",
                "name": "Cambio Neto de Biomasa (ΔAGB)",
                "kind": "raster",
                "status": "real",
                "platform": "NISAR (Simulado)",
                "sensor": "L-SAR L3 Diff",
                "provider": "NISAR_Biomass L3 Pipeline",
                "product": "ΔAGB = Post − Antes (Mg/ha)",
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
                        "label": "ΔAGB (Post − Antes)",
                        "quantity": "biomass_change",
                        "polarization": "HH+HV",
                        "unit": "Mg/ha",
                        "legend": {
                            "min": -120.0,
                            "max": 60.0,
                            "colors": ["#991b1b", "#ef4444", "#f97316", "#64748b", "#22c55e", "#15803d"],
                            "nodata_label": "Rojo = pérdida severa · Verde = ganancia",
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
                "name": "Cobertura NLCD & Referencia Planet",
                "kind": "raster",
                "status": "real",
                "platform": "USGS NLCD / PlanetScope",
                "sensor": "Multispectral",
                "provider": "Wasabi S3 Public / NEON LENO",
                "product": "LENO LULC + Validación de Perturbación",
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
                        "label": "Cobertura NLCD / Perturbación Planet",
                        "quantity": "land_cover",
                        "polarization": None,
                        "unit": "clase",
                    }
                ],
                "frames": [
                    {
                        "id": "pre",
                        "label": "Antes · Cobertura NLCD LENO",
                        "phase": "before",
                        "datetime": pre_dt,
                        "files": {"lulc_dist": f"data/cases/{site.case_id}/lulc_nlcd.png"},
                    },
                    {
                        "id": "post",
                        "label": "Después · Perturbación Referencia Planet",
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
                "name": "Área de estudio LENO (405.30 km²)",
                "status": "real",
                "simulated": False,
                "type": "line",
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/aoi.geojson"},
                "paint": {"line-color": "#38bdf8", "line-width": 1.8, "line-dasharray": [3, 2]},
            },
            {
                "id": "biomass_loss_nisar",
                "name": "Pérdida Severa NISAR (ΔAGB ≤ −40 Mg/ha)",
                "status": "real",
                "simulated": False,
                "type": "fill",
                "defaultVisible": False,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/biomass_loss_nisar.geojson"},
                "paint": {"fill-color": "#f97316", "fill-opacity": 0.45, "fill-outline-color": "#fdba74"},
                "legend": {
                    "title": "Detección NISAR L3",
                    "items": [{"color": "#f97316", "label": "ΔAGB ≤ −40 Mg/ha"}],
                },
                "popupFields": [
                    ["area_km2", "Área de pérdida", "km²"],
                    ["area_ha", "Superficie", "ha"],
                ],
            },
            {
                "id": "planet_disturbance_vec",
                "name": "Validación Óptica Planet (2022–2023)",
                "status": "real",
                "simulated": False,
                "type": "fill",
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"data/cases/{site.case_id}/planet_disturbance.geojson"},
                "paint": {"fill-color": "#f43f5e", "fill-opacity": 0.55, "fill-outline-color": "#ffe4e6"},
                "legend": {
                    "title": "Referencia Óptica Planet",
                    "items": [{"color": "#f43f5e", "label": "Perturbación confirmada"}],
                },
                "popupFields": [
                    ["area_km2", "Área perturbada", "km²"],
                    ["area_ha", "Superficie", "ha"],
                ],
            },
        ],
        "downloads": downloads,
    }

    case_json_path = case_dir / "case.json"
    case_json_path.write_text(json.dumps(case_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    provenance = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "pipeline": "nisar_biomass_monitor (basado en DeltasMadness2/NISAR_Biomass)",
        "siteCode": site.site_code,
        "epsgNative": site.epsg,
        "resolutionMeters": site.target_resolution_m,
        "roiBoundsUtm": list(site.roi_bounds_utm),
        "boundsWgs84": extent,
        "nisarConfig": params.model_dump(),
        "preScenes": [{"dateTag": s.date_tag, "iso": s.acquisition_iso, "url": s.source_url} for s in pre_scenes],
        "postScenes": [{"dateTag": s.date_tag, "iso": s.acquisition_iso, "url": s.source_url} for s in post_scenes],
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
