"""Exportador real de casos de deshielo (GEE + GeoTIFFs + PNGs Antes/Después) para sar-Madness."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import contourpy
import ee
import numpy as np
from PIL import Image
from pydantic import BaseModel
from shapely.geometry import MultiPolygon, Polygon, mapping

from glacier_monitor.config import AOI_PRESETS, PROJECT_ROOT, BoundingBox, Settings, resolve_aoi
from glacier_monitor.gee_auth import initialize_earth_engine

DEFAULT_SAR_MADNESS_PUBLIC = (PROJECT_ROOT.parent / "sar-Madness" / "sar-Madness" / "public").resolve()

PRE_DEFAULT = ("2026-01-01", "2026-02-28")
POST_DEFAULT = ("2026-05-15", "2026-08-31")


@dataclass(frozen=True)
class GlacierPresetMeta:
    """Metadatos geográficos y editoriales de cada zona glaciar real."""

    key: str
    case_id: str
    name: str
    region: str
    zoom: float
    summary: str


GLACIER_CASE_META: dict[str, GlacierPresetMeta] = {
    "langtang": GlacierPresetMeta(
        key="langtang",
        case_id="glacier-langtang",
        name="Valle de Langtang · Deshielo glaciar",
        region="Himalaya central, Nepal",
        zoom=11.6,
        summary=(
            "Retroceso estacional de cobertura nival y transición de nieve seca invernal "
            "(ene–feb 2026) a deshielo estival sobre 0 °C (may–ago 2026) observado con "
            "Sentinel-2, Sentinel-1 GRD y MODIS."
        ),
    ),
    "khumbu": GlacierPresetMeta(
        key="khumbu",
        case_id="glacier-khumbu",
        name="Glaciar de Khumbu · Deshielo alpino",
        region="Región del Everest, Nepal",
        zoom=12.0,
        summary=(
            "Comparación real Antes/Después de cobertura nival NDSI, temperatura superficial "
            "MODIS y retrodispersión SAR Sentinel-1 entre acumulación invernal y ablación de "
            "verano 2026."
        ),
    ),
}


class GlacierExportResult(BaseModel):
    """Resultado completo de exportación real de un caso de deshielo."""

    case_id: str
    name: str
    pre_window: str
    post_window: str
    case_dir: Path
    catalog_path: Path
    geotiff_count: int
    png_count: int
    melt_area_km2: float
    snow_pre_pct: float
    snow_post_pct: float
    lst_pre_c: float
    lst_post_c: float


def compute_grid_spec(bbox: BoundingBox, max_dim: int = 420) -> dict[str, Any]:
    """Calcula dimensiones proporcionales EPSG:4326 y rejilla compatible con GEE computePixels."""
    dlon = bbox.max_lon - bbox.min_lon
    dlat = bbox.max_lat - bbox.min_lat
    if dlon >= dlat:
        width = max_dim
        height = max(120, int(round(max_dim * (dlat / dlon))))
    else:
        height = max_dim
        width = max(120, int(round(max_dim * (dlon / dlat))))

    scale_x = dlon / width
    scale_y = dlat / height
    return {
        "width": width,
        "height": height,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "bounds": [bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat],
        "grid": {
            "dimensions": {"width": width, "height": height},
            "affineTransform": {
                "scaleX": scale_x,
                "shearX": 0.0,
                "translateX": bbox.min_lon,
                "shearY": 0.0,
                "scaleY": -scale_y,
                "translateY": bbox.max_lat,
            },
            "crsCode": "EPSG:4326",
        },
    }


def encode_gray8_png(arr: np.ndarray, vmin: float, vmax: float) -> bytes:
    """Codifica una matriz real 2D a PNG de 8 bits (escala física fija entre Antes y Después)."""
    span = max(float(vmax - vmin), 1e-9)
    valid = np.isfinite(arr)
    norm = np.zeros(arr.shape, dtype=np.float32)
    norm[valid] = np.clip((arr[valid] - vmin) / span, 0.0, 1.0)
    u8 = np.rint(norm * 255.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_s2_rgb_png(
    red: np.ndarray,
    green: np.ndarray,
    blue: np.ndarray,
    min_refl: float = 0.01,
    max_refl: float = 0.90,
    gamma: float = 0.85,
) -> bytes:
    """Codifica reflectancias Sentinel-2 [B4,B3,B2] a RGBA PNG con estiramiento físico fijo."""
    valid = np.isfinite(red) & np.isfinite(green) & np.isfinite(blue)
    span = max(float(max_refl - min_refl), 1e-6)

    def _chan(band: np.ndarray) -> np.ndarray:
        out = np.zeros(band.shape, dtype=np.uint8)
        scaled = np.clip((band[valid] - min_refl) / span, 0.0, 1.0)
        if gamma != 1.0:
            scaled = np.power(scaled, gamma)
        out[valid] = np.rint(scaled * 255.0).astype(np.uint8)
        return out

    rgba = np.stack(
        [
            _chan(red),
            _chan(green),
            _chan(blue),
            np.where(valid, 255, 0).astype(np.uint8),
        ],
        axis=-1,
    )
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def compute_melt_loss_array(ndsi_pre: np.ndarray, ndsi_post: np.ndarray) -> np.ndarray:
    """Calcula pérdida real de índice de nieve (en %) solo donde existía nieve previa o retroceso."""
    valid = np.isfinite(ndsi_pre) & np.isfinite(ndsi_post)
    loss = np.zeros(ndsi_pre.shape, dtype=np.float32)
    diff = ndsi_pre - ndsi_post
    loss[valid] = np.clip(diff[valid] * 100.0, 0.0, 100.0)
    return np.where(valid, loss, np.nan)


def extract_melt_extent_geojson(
    ndsi_pre: np.ndarray,
    ndsi_post: np.ndarray,
    bbox: BoundingBox,
    ndsi_drop_thresh: float = 0.16,
    pre_snow_min: float = 0.28,
) -> tuple[dict[str, Any], float]:
    """Extrae polígonos GeoJSON de pérdida real de nieve/hielo y calcula el área real en km²."""
    height, width = ndsi_pre.shape
    valid = np.isfinite(ndsi_pre) & np.isfinite(ndsi_post)
    delta = np.where(valid, ndsi_pre - ndsi_post, 0.0)
    melt_mask = valid & (ndsi_pre >= pre_snow_min) & (delta >= ndsi_drop_thresh)

    mid_lat_rad = np.deg2rad((bbox.min_lat + bbox.max_lat) / 2.0)
    dlon_pix = (bbox.max_lon - bbox.min_lon) / width
    dlat_pix = (bbox.max_lat - bbox.min_lat) / height
    pix_area_km2 = (111.32 * dlat_pix) * (111.32 * np.cos(mid_lat_rad) * dlon_pix)
    melt_area_km2 = float(int(melt_mask.sum()) * pix_area_km2)

    features: list[dict[str, Any]] = []
    if int(melt_mask.sum()) >= 4:
        xs = bbox.min_lon + (np.arange(width) + 0.5) * dlon_pix
        ys = bbox.max_lat - (np.arange(height) + 0.5) * dlat_pix
        # Para contornos limpios sin invertir orientación geoespacial, se ordena lat ascendente.
        z = melt_mask[::-1, :].astype(np.float64)
        cg = contourpy.contour_generator(x=xs, y=ys[::-1], z=z, fill_type=contourpy.FillType.OuterOffset)
        verts_list, offs_list = cg.filled(0.5, 1.5)

        min_poly_deg2 = dlon_pix * dlat_pix * 3.0
        polys: list[Polygon] = []
        for verts, offs in zip(verts_list, offs_list):
            if len(offs) < 2:
                continue
            shell = [(float(x), float(y)) for x, y in verts[offs[0] : offs[1]]]
            holes = [
                [(float(x), float(y)) for x, y in verts[offs[i] : offs[i + 1]]]
                for i in range(1, len(offs) - 1)
            ]
            poly = Polygon(shell, holes).buffer(0)
            if isinstance(poly, Polygon) and poly.is_valid and poly.area >= min_poly_deg2:
                polys.append(poly)
            elif isinstance(poly, MultiPolygon):
                polys.extend(
                    p for p in poly.geoms if p.is_valid and p.area >= min_poly_deg2
                )

        if polys:
            merged = MultiPolygon(polys).buffer(0) if len(polys) > 1 else polys[0]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Zona de retroceso nival y deshielo real",
                        "area_km2": round(melt_area_km2, 2),
                        "ndsi_drop_threshold": ndsi_drop_thresh,
                    },
                    "geometry": mapping(merged),
                }
            )

    return {"type": "FeatureCollection", "features": features}, round(melt_area_km2, 2)


def build_aoi_geojson(bbox: BoundingBox, name: str, case_id: str) -> dict[str, Any]:
    """Construye el FeatureCollection GeoJSON del AOI real."""
    coords = [
        [
            [bbox.min_lon, bbox.min_lat],
            [bbox.max_lon, bbox.min_lat],
            [bbox.max_lon, bbox.max_lat],
            [bbox.min_lon, bbox.max_lat],
            [bbox.min_lon, bbox.min_lat],
        ]
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": case_id, "name": name},
                "geometry": {"type": "Polygon", "coordinates": coords},
            }
        ],
    }


def _s2_clean_collection(geom: ee.Geometry, d0: str, d1: str) -> ee.ImageCollection:
    def _mask_scl(img: ee.Image) -> ee.Image:
        scl = img.select("SCL")
        good = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        return img.select(["B4", "B3", "B2", "B11"]).updateMask(good)

    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geom)
        .filterDate(d0, d1)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
        .map(_mask_scl)
    )


def _s1_iw_collection(geom: ee.Geometry, d0: str, d1: str) -> ee.ImageCollection:
    return (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(geom)
        .filterDate(d0, d1)
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
    )


def _gee_npy(image: ee.Image, grid: dict[str, Any], nodata: float = -9999.0) -> Any:
    raw = ee.data.computePixels(
        {
            "expression": image.unmask(nodata).toFloat(),
            "fileFormat": "NPY",
            "grid": grid,
        }
    )
    return np.load(io.BytesIO(raw))


def _gee_geotiff(image: ee.Image, grid: dict[str, Any]) -> bytes:
    return ee.data.computePixels(
        {
            "expression": image.toFloat(),
            "fileFormat": "GEO_TIFF",
            "grid": grid,
        }
    )


def _collection_info(col: ee.ImageCollection, limit: int = 4) -> tuple[int, list[str]]:
    count = int(col.size().getInfo())
    ids = list(col.limit(limit).aggregate_array("system:index").getInfo() or [])
    return count, [str(item) for item in ids]


def _extract_band(structured: Any, band: str, nodata: float = -9999.0) -> np.ndarray:
    arr = structured[band].astype(np.float32)
    return np.where(arr == nodata, np.nan, arr)


def build_deshielo_case_definition(
    *,
    case_id: str,
    name: str,
    region: str,
    summary: str,
    bbox: BoundingBox,
    zoom: float,
    pre_window: tuple[str, str],
    post_window: tuple[str, str],
    stats: dict[str, Any],
    downloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construye la definición schemaVersion=2 estrictamente real para un caso de deshielo."""
    extent = [bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat]
    center = [
        round((bbox.min_lon + bbox.max_lon) / 2.0, 5),
        round((bbox.min_lat + bbox.max_lat) / 2.0, 5),
    ]
    base = f"data/cases/{case_id}"
    pre_label = f"Antes · {pre_window[0]} a {pre_window[1]}"
    post_label = f"Después · {post_window[0]} a {post_window[1]}"

    return {
        "schemaVersion": 2,
        "id": case_id,
        "status": "real",
        "simulated": False,
        "category": "deshielo",
        "title": name,
        "region": region,
        "description": summary,
        "view": {
            "center": center,
            "zoom": zoom,
            "pitch": 40,
            "bearing": 0,
        },
        "sources": [
            f"Google Earth Engine · COPERNICUS/S2_SR_HARMONIZED ({stats['s2_pre_count']} pre / {stats['s2_post_count']} post)",
            f"Google Earth Engine · COPERNICUS/S1_GRD IW ({stats['s1_pre_count']} pre / {stats['s1_post_count']} post)",
            f"Google Earth Engine · MODIS/061/MOD10A1 y MOD11A1 ({stats['modis_pre_count']} pre / {stats['modis_post_count']} post)",
        ],
        "metrics": {
            "preWindow": f"{pre_window[0]}..{pre_window[1]}",
            "postWindow": f"{post_window[0]}..{post_window[1]}",
            "snowPrePct": stats["snow_pre_pct"],
            "snowPostPct": stats["snow_post_pct"],
            "lstPreC": stats["lst_pre_c"],
            "lstPostC": stats["lst_post_c"],
            "meltAreaKm2": stats["melt_area_km2"],
        },
        "datasets": [
            {
                "id": "s2_rgb",
                "name": "Sentinel-2 RGB · Color real",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2A/2B/2C",
                "sensor": "MSI",
                "provider": "Google Earth Engine",
                "product": "Compuesto sin nubes B4/B3/B2",
                "attribution": "ESA Copernicus Sentinel-2",
                "extent": extent,
                "source": {"type": "image", "encoding": "rgba"},
                "render": {"opacity": 1.0, "resampling": "linear"},
                "defaultVisible": True,
                "variables": [
                    {
                        "id": "rgb",
                        "label": "Color verdadero (B4/B3/B2)",
                        "quantity": "reflectance",
                        "polarization": None,
                        "unit": "RGB",
                    }
                ],
                "frames": [
                    {
                        "id": "s2-pre",
                        "datetime": f"{pre_window[1]}T00:00:00Z",
                        "label": pre_label,
                        "phase": "before",
                        "files": {"rgb": f"{base}/s2_pre_rgb.png"},
                    },
                    {
                        "id": "s2-post",
                        "datetime": f"{post_window[1]}T00:00:00Z",
                        "label": post_label,
                        "phase": "after",
                        "files": {"rgb": f"{base}/s2_post_rgb.png"},
                    },
                ],
            },
            {
                "id": "s2_ndsi",
                "name": "Índice de nieve NDSI · Sentinel-2",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2",
                "sensor": "MSI B3/B11",
                "provider": "Google Earth Engine",
                "product": "Normalized Difference Snow Index",
                "attribution": "ESA Copernicus Sentinel-2",
                "extent": extent,
                "source": {"type": "image", "encoding": "gray8", "nodata": None},
                "render": {"opacity": 0.88, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "ndsi",
                        "label": "Índice NDSI de nieve",
                        "quantity": "spectral_index",
                        "polarization": None,
                        "unit": "NDSI",
                        "legend": {
                            "min": -0.5,
                            "max": 1.0,
                            "colors": ["#3b2f2f", "#8c7a6b", "#cbd5e1", "#7dd3fc", "#ffffff"],
                        },
                    }
                ],
                "frames": [
                    {
                        "id": "ndsi-pre",
                        "datetime": f"{pre_window[1]}T00:00:00Z",
                        "label": pre_label,
                        "phase": "before",
                        "files": {"ndsi": f"{base}/s2_pre_ndsi.png"},
                    },
                    {
                        "id": "ndsi-post",
                        "datetime": f"{post_window[1]}T00:00:00Z",
                        "label": post_label,
                        "phase": "after",
                        "files": {"ndsi": f"{base}/s2_post_ndsi.png"},
                    },
                ],
            },
            {
                "id": "s1_sar",
                "name": "Radar SAR · Sentinel-1 GRD",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-1",
                "sensor": "C-SAR IW",
                "provider": "Google Earth Engine",
                "product": "Retrodispersión σ⁰ en invierno vs deshielo",
                "attribution": "ESA Copernicus Sentinel-1",
                "extent": extent,
                "source": {"type": "image", "encoding": "gray8", "nodata": None},
                "render": {"opacity": 0.88, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "vh",
                        "label": "Polarización VH",
                        "quantity": "backscatter",
                        "polarization": "VH",
                        "unit": "dB",
                        "legend": {
                            "min": -26.0,
                            "max": -2.0,
                            "colors": ["#090d16", "#334155", "#94a3b8", "#f8fafc"],
                        },
                    },
                    {
                        "id": "vv",
                        "label": "Polarización VV",
                        "quantity": "backscatter",
                        "polarization": "VV",
                        "unit": "dB",
                        "legend": {
                            "min": -20.0,
                            "max": 2.0,
                            "colors": ["#090d16", "#334155", "#94a3b8", "#f8fafc"],
                        },
                    },
                ],
                "frames": [
                    {
                        "id": "s1-pre",
                        "datetime": f"{pre_window[1]}T00:00:00Z",
                        "label": pre_label,
                        "phase": "before",
                        "files": {
                            "vh": f"{base}/s1_pre_vh.png",
                            "vv": f"{base}/s1_pre_vv.png",
                        },
                    },
                    {
                        "id": "s1-post",
                        "datetime": f"{post_window[1]}T00:00:00Z",
                        "label": post_label,
                        "phase": "after",
                        "files": {
                            "vh": f"{base}/s1_post_vh.png",
                            "vv": f"{base}/s1_post_vv.png",
                        },
                    },
                ],
            },
            {
                "id": "modis_cryo",
                "name": "MODIS Nieve y Temperatura (LST)",
                "kind": "raster",
                "status": "real",
                "platform": "Terra / Aqua",
                "sensor": "MODIS",
                "provider": "Google Earth Engine / NASA LP DAAC",
                "product": "MOD10A1 + MOD11A1",
                "attribution": "NASA MODIS",
                "extent": extent,
                "source": {"type": "image", "encoding": "gray8", "nodata": None},
                "render": {"opacity": 0.85, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "snow",
                        "label": "Cobertura de nieve",
                        "quantity": "snow_cover",
                        "polarization": None,
                        "unit": "%",
                        "legend": {
                            "min": 0.0,
                            "max": 100.0,
                            "colors": ["#2d2420", "#64748b", "#38bdf8", "#e0f2fe", "#ffffff"],
                        },
                    },
                    {
                        "id": "lst",
                        "label": "Temperatura superficial día",
                        "quantity": "temperature",
                        "polarization": None,
                        "unit": "°C",
                        "legend": {
                            "min": -25.0,
                            "max": 20.0,
                            "colors": ["#1e3a8a", "#06b6d4", "#fef08a", "#f97316", "#dc2626"],
                        },
                    },
                ],
                "frames": [
                    {
                        "id": "modis-pre",
                        "datetime": f"{pre_window[1]}T00:00:00Z",
                        "label": pre_label,
                        "phase": "before",
                        "files": {
                            "snow": f"{base}/mod10_pre_snow.png",
                            "lst": f"{base}/mod11_pre_lst.png",
                        },
                    },
                    {
                        "id": "modis-post",
                        "datetime": f"{post_window[1]}T00:00:00Z",
                        "label": post_label,
                        "phase": "after",
                        "files": {
                            "snow": f"{base}/mod10_post_snow.png",
                            "lst": f"{base}/mod11_post_lst.png",
                        },
                    },
                ],
            },
            {
                "id": "melt_change",
                "name": "Pérdida neta de nieve (ΔNDSI)",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2",
                "sensor": "MSI",
                "provider": "Google Earth Engine",
                "product": "Diferencia real Pre − Post NDSI",
                "attribution": "ESA Copernicus Sentinel-2",
                "extent": extent,
                "source": {"type": "image", "encoding": "gray8", "nodata": None},
                "render": {"opacity": 0.85, "resampling": "linear"},
                "defaultVisible": False,
                "variables": [
                    {
                        "id": "loss",
                        "label": "Pérdida de nieve (ΔNDSI × 100)",
                        "quantity": "snow_loss",
                        "polarization": None,
                        "unit": "%",
                        "legend": {
                            "min": 0.0,
                            "max": 80.0,
                            "colors": ["#0f172a", "#3b82f6", "#facc15", "#f97316", "#ef4444"],
                        },
                    }
                ],
                "frames": [
                    {
                        "id": "melt-net",
                        "datetime": f"{post_window[1]}T00:00:00Z",
                        "label": f"Cambio {pre_window[0]} → {post_window[1]}",
                        "phase": "change",
                        "files": {"loss": f"{base}/melt_snow_loss.png"},
                    }
                ],
            },
        ],
        "vectors": [
            {
                "id": "aoi",
                "name": "Área de estudio (AOI)",
                "type": "line",
                "status": "real",
                "simulated": False,
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"{base}/aoi.geojson"},
                "paint": {"line-color": "#38bdf8", "line-width": 1.6, "line-dasharray": [3, 2]},
            },
            {
                "id": "melt-extent",
                "name": "Zona de deshielo detectada",
                "type": "line",
                "status": "real",
                "simulated": False,
                "defaultVisible": True,
                "source": {"type": "geojson", "data": f"{base}/melt_extent.geojson"},
                "paint": {"line-color": "#fb923c", "line-width": 1.8},
                "legend": {
                    "title": "Retroceso nival real",
                    "items": [{"color": "#fb923c", "label": "Pérdida ΔNDSI ≥ 0.16"}],
                },
                "popupFields": [["area_km2", "Área de deshielo", "km²"]],
            },
        ],
        "downloads": downloads,
    }


def register_case_in_catalog_v2(catalog_path: Path, case_rel_path: str) -> None:
    """Registra o actualiza una entrada de caso en public/data/catalog.json sin duplicados."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    else:
        catalog = {"schemaVersion": 2, "cases": []}

    cases = [str(item) for item in catalog.get("cases", []) if "demo" not in str(item).lower()]
    if case_rel_path not in cases:
        cases.append(case_rel_path)
    catalog["schemaVersion"] = 2
    catalog["cases"] = cases
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _update_local_download_catalog(
    dl_root: Path,
    case_id: str,
    entries: list[dict[str, Any]],
) -> None:
    cat_path = dl_root / "catalog.json"
    if cat_path.exists():
        data = json.loads(cat_path.read_text(encoding="utf-8"))
    else:
        data = {"cases": {}}
    if "cases" not in data or not isinstance(data["cases"], dict):
        data["cases"] = {}
    data["cases"][case_id] = entries
    cat_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_deshielo_case_to_madness(
    aoi_spec: str,
    *,
    settings: Settings | None = None,
    pre_start: str = PRE_DEFAULT[0],
    pre_end: str = PRE_DEFAULT[1],
    post_start: str = POST_DEFAULT[0],
    post_end: str = POST_DEFAULT[1],
    max_dim: int = 420,
    target_public_dir: Path | None = None,
) -> GlacierExportResult:
    """Descarga datos reales de GEE (Antes/Después), genera GeoTIFFs y PNGs y publica en sar-Madness."""
    cfg = settings or Settings()
    initialize_earth_engine(cfg)

    resolved_name, bbox = resolve_aoi(aoi_spec)
    preset = GLACIER_CASE_META.get(aoi_spec.strip().lower())
    if preset is not None:
        case_id = preset.case_id
        name = preset.name
        region = preset.region
        zoom = preset.zoom
        summary = preset.summary
    else:
        slug = "".join(c if c.isalnum() else "-" for c in aoi_spec.lower()).strip("-")
        case_id = f"glacier-{slug}"
        name = resolved_name
        region = "Zona personalizada"
        zoom = 11.5
        summary = f"Observaciones satelitales reales Antes ({pre_start}..{pre_end}) y Después ({post_start}..{post_end})."

    public_dir = (target_public_dir or DEFAULT_SAR_MADNESS_PUBLIC).resolve()
    case_dir = public_dir / "data" / "cases" / case_id
    tif_public_dir = case_dir / "geotiffs"
    dl_root = cfg.local_download_dir if cfg.local_download_dir.is_absolute() else (PROJECT_ROOT / cfg.local_download_dir)
    tif_local_dir = dl_root / case_id
    for folder in (case_dir, tif_public_dir, tif_local_dir):
        folder.mkdir(parents=True, exist_ok=True)

    geom = ee.Geometry.Rectangle([bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat])
    grid_spec = compute_grid_spec(bbox, max_dim=max_dim)
    grid = grid_spec["grid"]

    # Colecciones reales GEE
    s2_pre_col = _s2_clean_collection(geom, pre_start, pre_end)
    s2_post_col = _s2_clean_collection(geom, post_start, post_end)
    s1_pre_col = _s1_iw_collection(geom, pre_start, pre_end)
    s1_post_col = _s1_iw_collection(geom, post_start, post_end)
    mod10_pre_col = ee.ImageCollection("MODIS/061/MOD10A1").filterBounds(geom).filterDate(pre_start, pre_end).select("NDSI_Snow_Cover")
    mod10_post_col = ee.ImageCollection("MODIS/061/MOD10A1").filterBounds(geom).filterDate(post_start, post_end).select("NDSI_Snow_Cover")
    mod11_pre_col = ee.ImageCollection("MODIS/061/MOD11A1").filterBounds(geom).filterDate(pre_start, pre_end).select("LST_Day_1km")
    mod11_post_col = ee.ImageCollection("MODIS/061/MOD11A1").filterBounds(geom).filterDate(post_start, post_end).select("LST_Day_1km")

    s2_pre_count, s2_pre_ids = _collection_info(s2_pre_col)
    s2_post_count, s2_post_ids = _collection_info(s2_post_col)
    s1_pre_count, s1_pre_ids = _collection_info(s1_pre_col)
    s1_post_count, s1_post_ids = _collection_info(s1_post_col)
    m10_pre_count, _ = _collection_info(mod10_pre_col)
    m10_post_count, _ = _collection_info(mod10_post_col)

    s2_pre_med = s2_pre_col.median().clip(geom)
    s2_post_med = s2_post_col.median().clip(geom)
    s2_pre_rgb_img = s2_pre_med.select(["B4", "B3", "B2"]).divide(10000.0)
    s2_post_rgb_img = s2_post_med.select(["B4", "B3", "B2"]).divide(10000.0)
    s2_pre_ndsi_img = s2_pre_med.normalizedDifference(["B3", "B11"]).rename("NDSI")
    s2_post_ndsi_img = s2_post_med.normalizedDifference(["B3", "B11"]).rename("NDSI")

    s1_pre_img = s1_pre_col.select(["VV", "VH"]).median().clip(geom)
    s1_post_img = s1_post_col.select(["VV", "VH"]).median().clip(geom)

    mod10_pre_img = mod10_pre_col.median().clip(geom)
    mod10_post_img = mod10_post_col.median().clip(geom)
    mod11_pre_img = mod11_pre_col.median().multiply(0.02).subtract(273.15).clip(geom)
    mod11_post_img = mod11_post_col.median().multiply(0.02).subtract(273.15).clip(geom)
    melt_loss_img = s2_pre_ndsi_img.subtract(s2_post_ndsi_img).multiply(100.0).max(0.0).rename("MELT_PCT")

    # Descargar arrays sincronizados desde GEE
    arr_s2_pre = _gee_npy(s2_pre_rgb_img.addBands(s2_pre_ndsi_img), grid)
    arr_s2_post = _gee_npy(s2_post_rgb_img.addBands(s2_post_ndsi_img), grid)
    arr_s1_pre = _gee_npy(s1_pre_img, grid)
    arr_s1_post = _gee_npy(s1_post_img, grid)
    arr_m10_pre = _gee_npy(mod10_pre_img, grid)
    arr_m10_post = _gee_npy(mod10_post_img, grid)
    arr_m11_pre = _gee_npy(mod11_pre_img, grid)
    arr_m11_post = _gee_npy(mod11_post_img, grid)

    ndsi_pre = _extract_band(arr_s2_pre, "NDSI")
    ndsi_post = _extract_band(arr_s2_post, "NDSI")
    snow_pre = _extract_band(arr_m10_pre, "NDSI_Snow_Cover")
    snow_post = _extract_band(arr_m10_post, "NDSI_Snow_Cover")
    lst_pre = _extract_band(arr_m11_pre, "LST_Day_1km")
    lst_post = _extract_band(arr_m11_post, "LST_Day_1km")
    melt_loss = compute_melt_loss_array(ndsi_pre, ndsi_post)

    # Escribir PNGs visuales (Antes, Después y Cambio)
    png_specs: list[tuple[str, bytes]] = [
        (
            "s2_pre_rgb.png",
            encode_s2_rgb_png(
                _extract_band(arr_s2_pre, "B4"),
                _extract_band(arr_s2_pre, "B3"),
                _extract_band(arr_s2_pre, "B2"),
            ),
        ),
        (
            "s2_post_rgb.png",
            encode_s2_rgb_png(
                _extract_band(arr_s2_post, "B4"),
                _extract_band(arr_s2_post, "B3"),
                _extract_band(arr_s2_post, "B2"),
            ),
        ),
        ("s2_pre_ndsi.png", encode_gray8_png(ndsi_pre, -0.5, 1.0)),
        ("s2_post_ndsi.png", encode_gray8_png(ndsi_post, -0.5, 1.0)),
        ("s1_pre_vh.png", encode_gray8_png(_extract_band(arr_s1_pre, "VH"), -26.0, -2.0)),
        ("s1_post_vh.png", encode_gray8_png(_extract_band(arr_s1_post, "VH"), -26.0, -2.0)),
        ("s1_pre_vv.png", encode_gray8_png(_extract_band(arr_s1_pre, "VV"), -20.0, 2.0)),
        ("s1_post_vv.png", encode_gray8_png(_extract_band(arr_s1_post, "VV"), -20.0, 2.0)),
        ("mod10_pre_snow.png", encode_gray8_png(snow_pre, 0.0, 100.0)),
        ("mod10_post_snow.png", encode_gray8_png(snow_post, 0.0, 100.0)),
        ("mod11_pre_lst.png", encode_gray8_png(lst_pre, -25.0, 20.0)),
        ("mod11_post_lst.png", encode_gray8_png(lst_post, -25.0, 20.0)),
        ("melt_snow_loss.png", encode_gray8_png(melt_loss, 0.0, 80.0)),
    ]
    for filename, payload in png_specs:
        (case_dir / filename).write_bytes(payload)

    # Descargar GeoTIFFs reales de Earth Engine
    tif_specs: list[tuple[str, str, str, str, ee.Image]] = [
        ("s2-pre-rgb-tif", f"{case_id}_s2_pre_rgb.tif", "Sentinel-2 RGB Antes (.tif)", "s2_rgb", "before", s2_pre_rgb_img),
        ("s2-post-rgb-tif", f"{case_id}_s2_post_rgb.tif", "Sentinel-2 RGB Después (.tif)", "s2_rgb", "after", s2_post_rgb_img),
        ("s2-pre-ndsi-tif", f"{case_id}_s2_pre_ndsi.tif", "Sentinel-2 NDSI Antes (.tif)", "s2_ndsi", "before", s2_pre_ndsi_img),
        ("s2-post-ndsi-tif", f"{case_id}_s2_post_ndsi.tif", "Sentinel-2 NDSI Después (.tif)", "s2_ndsi", "after", s2_post_ndsi_img),
        ("s1-pre-sar-tif", f"{case_id}_s1_pre_sar.tif", "Sentinel-1 VV/VH Antes (.tif)", "s1_sar", "before", s1_pre_img),
        ("s1-post-sar-tif", f"{case_id}_s1_post_sar.tif", "Sentinel-1 VV/VH Después (.tif)", "s1_sar", "after", s1_post_img),
        ("mod10-pre-snow-tif", f"{case_id}_mod10_pre_snow.tif", "MODIS Nieve Antes (.tif)", "modis_cryo", "before", mod10_pre_img),
        ("mod10-post-snow-tif", f"{case_id}_mod10_post_snow.tif", "MODIS Nieve Después (.tif)", "modis_cryo", "after", mod10_post_img),
        ("mod11-pre-lst-tif", f"{case_id}_mod11_pre_lst.tif", "MODIS Temp °C Antes (.tif)", "modis_cryo", "before", mod11_pre_img),
        ("mod11-post-lst-tif", f"{case_id}_mod11_post_lst.tif", "MODIS Temp °C Después (.tif)", "modis_cryo", "after", mod11_post_img),
        ("melt-loss-tif", f"{case_id}_melt_snow_loss.tif", "Pérdida Nieve Neta (.tif)", "melt_change", "change", melt_loss_img),
    ]
    downloads: list[dict[str, Any]] = []
    local_cat_entries: list[dict[str, Any]] = []
    for dl_id, fname, label, dataset_id, phase, ee_img in tif_specs:
        tif_bytes = _gee_geotiff(ee_img, grid)
        (tif_public_dir / fname).write_bytes(tif_bytes)
        (tif_local_dir / fname).write_bytes(tif_bytes)
        downloads.append(
            {
                "id": dl_id,
                "label": label,
                "datasetId": dataset_id,
                "phase": phase,
                "format": "geotiff",
                "url": f"data/cases/{case_id}/geotiffs/{fname}",
                "sizeBytes": len(tif_bytes),
            }
        )
        local_cat_entries.append(
            {
                "id": dl_id,
                "filename": fname,
                "local_path": str((tif_local_dir / fname).resolve()),
                "bounds": grid_spec["bounds"],
                "size_bytes": len(tif_bytes),
            }
        )

    _update_local_download_catalog(dl_root, case_id, local_cat_entries)

    # Vectores GeoJSON reales
    aoi_geo = build_aoi_geojson(bbox, name, case_id)
    melt_geo, melt_area_km2 = extract_melt_extent_geojson(ndsi_pre, ndsi_post, bbox)
    (case_dir / "aoi.geojson").write_text(json.dumps(aoi_geo, indent=2, ensure_ascii=False), encoding="utf-8")
    (case_dir / "melt_extent.geojson").write_text(json.dumps(melt_geo, indent=2, ensure_ascii=False), encoding="utf-8")

    snow_pre_pct = round(float(np.nanmean(snow_pre)), 1)
    snow_post_pct = round(float(np.nanmean(snow_post)), 1)
    lst_pre_c = round(float(np.nanmean(lst_pre)), 1)
    lst_post_c = round(float(np.nanmean(lst_post)), 1)

    stats = {
        "s2_pre_count": s2_pre_count,
        "s2_post_count": s2_post_count,
        "s1_pre_count": s1_pre_count,
        "s1_post_count": s1_post_count,
        "modis_pre_count": m10_pre_count,
        "modis_post_count": m10_post_count,
        "snow_pre_pct": snow_pre_pct,
        "snow_post_pct": snow_post_pct,
        "lst_pre_c": lst_pre_c,
        "lst_post_c": lst_post_c,
        "melt_area_km2": melt_area_km2,
    }

    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "status": "real",
        "source": "Google Earth Engine (computePixels NPY + GEO_TIFF)",
        "gee_project_id": cfg.gee_project_id,
        "pre_window": [pre_start, pre_end],
        "post_window": [post_start, post_end],
        "bbox": grid_spec["bounds"],
        "dimensions": [grid_spec["width"], grid_spec["height"]],
        "scene_samples": {
            "s2_pre": s2_pre_ids,
            "s2_post": s2_post_ids,
            "s1_pre": s1_pre_ids,
            "s1_post": s1_post_ids,
        },
        "stats": stats,
    }
    (case_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    case_def = build_deshielo_case_definition(
        case_id=case_id,
        name=name,
        region=region,
        summary=summary,
        bbox=bbox,
        zoom=zoom,
        pre_window=(pre_start, pre_end),
        post_window=(post_start, post_end),
        stats=stats,
        downloads=downloads,
    )
    (case_dir / "case.json").write_text(
        json.dumps(case_def, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    catalog_path = public_dir / "data" / "catalog.json"
    register_case_in_catalog_v2(catalog_path, f"data/cases/{case_id}/case.json")

    return GlacierExportResult(
        case_id=case_id,
        name=name,
        pre_window=f"{pre_start}..{pre_end}",
        post_window=f"{post_start}..{post_end}",
        case_dir=case_dir,
        catalog_path=catalog_path,
        geotiff_count=len(tif_specs),
        png_count=len(png_specs),
        melt_area_km2=melt_area_km2,
        snow_pre_pct=snow_pre_pct,
        snow_post_pct=snow_post_pct,
        lst_pre_c=lst_pre_c,
        lst_post_c=lst_post_c,
    )
