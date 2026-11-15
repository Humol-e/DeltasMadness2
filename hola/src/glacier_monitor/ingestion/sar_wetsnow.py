"""SAR wet-snow detection for glacier cases (multitemporal backscatter ratio).

Physical basis
--------------
Liquid water inside a snowpack raises its dielectric loss, so C-band radar backscatter
drops sharply when dry winter snow turns wet (Nagler & Rott, 2000; Nagler et al., 2016).
The classic change-detection rule compares a melt-season acquisition against a dry-snow
reference and flags pixels whose backscatter ratio falls below a threshold (-3 dB for
ERS/Radarsat, -2 dB for Sentinel-1). In dB space the ratio is simply ``post - pre``.

Implementation notes (honest scope)
-----------------------------------
* The detector is sensor-agnostic: it takes dB arrays for a *reference* (dry snow) and a
  *melt* window, so any calibrated SAR pair (Sentinel-1 today, NISAR GCOV later) can feed it.
* Wet snow is only evaluated where snow existed in the reference window (Sentinel-2 NDSI,
  same ``0.28`` threshold already used by the melt-zone product).
* A pixel is class 3 ("wet snow") only when **both** VV and VH drop by at least the threshold;
  a drop in a single channel is class 2 ("possible wet snow"). This is a conservative
  simplification of the combined-channel ratio used operationally.
* Inputs published by this project are median composites of ``COPERNICUS/S1_GRD`` (not
  terrain-flattened), so results are qualitative and should be read together with the
  optical snow-loss layer.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import contourpy
import numpy as np
import shapely
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2
from shapely.geometry import MultiPolygon, Polygon, mapping

from glacier_monitor.config import PROJECT_ROOT, BoundingBox

WET_SNOW_THRESHOLD_DB = -2.0
PRE_SNOW_NDSI_MIN = 0.28
# Reference-window floors: below these the radar signal is shadow/noise-dominated.
REFERENCE_FLOOR_DB: dict[str, float] = {"vv": -18.0, "vh": -24.0}
RATIO_RANGE_DB = (-10.0, 5.0)

# Physical ranges used by `platform_exporter` when encoding Sentinel-1 / NDSI gray8 PNGs.
S1_PNG_RANGE_DB: dict[str, tuple[float, float]] = {"vv": (-20.0, 2.0), "vh": (-26.0, -2.0)}
NDSI_PNG_RANGE = (-0.5, 1.0)

CLASS_STABLE = 1
CLASS_POSSIBLE_WET = 2
CLASS_WET = 3
N_CLASSES = 3

DATASET_ID = "sar_wetsnow"
VECTOR_ID = "sar-wetsnow-extent"
FRAME_ID = "wetsnow-change"
DOWNLOAD_PREFIX = "wetsnow-"

CLASS_LEGEND_NOTE = "1 Stable snow · 2 Possible wet · 3 Wet snow"
RATIO_LEGEND_NOTE = "Blue = backscatter drop (wet snow) · Transparent ≈ 0 dB · Orange = increase"


# --------------------------------------------------------------------------- core algorithm


@dataclass(frozen=True)
class WetSnowResult:
    """Per-pixel wet-snow classification and the backscatter ratios that produced it."""

    classes: np.ndarray  # uint8: 0 = outside snow domain / invalid, 1..3 = see CLASS_*
    ratio_vv_db: np.ndarray  # float32, NaN outside the snow domain
    ratio_vh_db: np.ndarray  # float32, NaN outside the snow domain
    threshold_db: float


def decode_gray8_db(u8: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Invert ``encode_gray8_png`` (``0`` = nodata per Schema v2) back to physical dB or index values."""
    arr = np.asarray(u8)
    out = vmin + arr.astype(np.float32) / 255.0 * (vmax - vmin)
    return np.where(arr > 0, out, np.nan).astype(np.float32)


def backscatter_ratio_db(melt_db: np.ndarray, reference_db: np.ndarray) -> np.ndarray:
    """Backscatter ratio in dB (melt / reference) for two co-registered dB arrays."""
    return (np.asarray(melt_db, dtype=np.float32) - np.asarray(reference_db, dtype=np.float32)).astype(
        np.float32
    )


def classify_wet_snow(
    vv_pre: np.ndarray,
    vv_post: np.ndarray,
    vh_pre: np.ndarray,
    vh_post: np.ndarray,
    ndsi_pre: np.ndarray,
    *,
    threshold_db: float = WET_SNOW_THRESHOLD_DB,
    pre_snow_ndsi_min: float = PRE_SNOW_NDSI_MIN,
) -> WetSnowResult:
    """Classify wet snow from dry-snow reference (pre) and melt-season (post) SAR in dB."""
    shapes = {a.shape for a in (vv_pre, vv_post, vh_pre, vh_post, ndsi_pre)}
    if len(shapes) != 1:
        raise ValueError(f"All wet-snow inputs must share one shape, got {sorted(shapes)}")
    if threshold_db >= 0:
        raise ValueError("threshold_db must be negative (a backscatter drop).")

    ratio_vv = backscatter_ratio_db(vv_post, vv_pre)
    ratio_vh = backscatter_ratio_db(vh_post, vh_pre)

    snow_reference = np.isfinite(ndsi_pre) & (ndsi_pre >= pre_snow_ndsi_min)
    usable_sar = (
        np.isfinite(ratio_vv)
        & np.isfinite(ratio_vh)
        & (vv_pre >= REFERENCE_FLOOR_DB["vv"])
        & (vh_pre >= REFERENCE_FLOOR_DB["vh"])
    )
    domain = snow_reference & usable_sar

    vv_wet = ratio_vv <= threshold_db
    vh_wet = ratio_vh <= threshold_db

    classes = np.zeros(vv_pre.shape, dtype=np.uint8)
    classes[domain] = CLASS_STABLE
    classes[domain & (vv_wet ^ vh_wet)] = CLASS_POSSIBLE_WET
    classes[domain & vv_wet & vh_wet] = CLASS_WET

    return WetSnowResult(
        classes=classes,
        ratio_vv_db=np.where(domain, ratio_vv, np.nan).astype(np.float32),
        ratio_vh_db=np.where(domain, ratio_vh, np.nan).astype(np.float32),
        threshold_db=float(threshold_db),
    )


def pixel_area_km2(bbox: BoundingBox, shape: tuple[int, int]) -> float:
    """Approximate pixel area (km²) for an EPSG:4326 grid covering `bbox`."""
    height, width = shape
    mid_lat_rad = np.deg2rad((bbox.min_lat + bbox.max_lat) / 2.0)
    dlon_pix = (bbox.max_lon - bbox.min_lon) / width
    dlat_pix = (bbox.max_lat - bbox.min_lat) / height
    return float((111.32 * dlat_pix) * (111.32 * np.cos(mid_lat_rad) * dlon_pix))


def summarize_wet_snow(
    classes: np.ndarray,
    bbox: BoundingBox,
    *,
    ndsi_post: np.ndarray | None = None,
    snow_ndsi_min: float = PRE_SNOW_NDSI_MIN,
) -> dict[str, float]:
    """Areas (km²), wet-snow share, and optional optical-snow cross-validation metrics."""
    px = pixel_area_km2(bbox, classes.shape)
    snow_km2 = float((classes > 0).sum()) * px
    wet_mask = classes == CLASS_WET
    wet_km2 = float(wet_mask.sum()) * px
    possible_km2 = float((classes == CLASS_POSSIBLE_WET).sum()) * px
    stats: dict[str, float] = {
        "snowReferenceKm2": round(snow_km2, 2),
        "wetSnowKm2": round(wet_km2, 2),
        "possibleWetKm2": round(possible_km2, 2),
        "wetSnowPct": round(100.0 * wet_km2 / snow_km2, 1) if snow_km2 > 0 else 0.0,
    }
    if ndsi_post is not None and ndsi_post.shape == classes.shape:
        opt_snow_post = np.isfinite(ndsi_post) & (ndsi_post >= snow_ndsi_min)
        subsurface_wet = wet_mask & opt_snow_post
        subsurface_km2 = float(subsurface_wet.sum()) * px
        n_wet = int(wet_mask.sum())
        stats["subsurfaceWetSnowKm2"] = round(subsurface_km2, 2)
        stats["wetSnowInOpticalSnowPct"] = (
            round(100.0 * float(subsurface_wet.sum()) / float(n_wet), 1) if n_wet > 0 else 0.0
        )
    return stats


# --------------------------------------------------------------------------- encoders


def encode_ratio_png(ratio_db: np.ndarray, vmin: float = RATIO_RANGE_DB[0], vmax: float = RATIO_RANGE_DB[1]) -> bytes:
    """gray8 PNG where 0 = nodata and 1..255 map linearly onto vmin..vmax (SCHEMA v2)."""
    span = max(float(vmax - vmin), 1e-9)
    valid = np.isfinite(ratio_db)
    u8 = np.zeros(ratio_db.shape, dtype=np.uint8)
    norm = np.clip((ratio_db[valid] - vmin) / span, 0.0, 1.0)
    u8[valid] = 1 + np.rint(norm * 254.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_class_png(classes: np.ndarray, n_classes: int = N_CLASSES) -> bytes:
    """gray8 PNG of discrete classes scaled over 0..n_classes (same convention as burn severity)."""
    u8 = np.rint(classes.astype(np.float32) / float(n_classes) * 255.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_geotiff(
    path: Path,
    array: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    nodata: float,
) -> int:
    """Write a compressed single-band EPSG:4326 GeoTIFF (uint8 or float32); returns file size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D raster array, got shape {arr.shape}")
    height, width = arr.shape
    west, south, east, north = bounds_wgs84

    if arr.dtype == np.uint8:
        img = Image.fromarray(arr, mode="L")
        nodata_str = str(int(nodata))
    else:
        img = Image.fromarray(np.where(np.isnan(arr), np.float32(nodata), arr.astype(np.float32)), mode="F")
        nodata_str = str(float(nodata))

    ifd = ImageFileDirectory_v2()
    ifd[33550] = ((east - west) / float(width), (north - south) / float(height), 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, float(west), float(north), 0.0)
    ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326)
    ifd[42113] = nodata_str
    img.save(path, format="TIFF", tiffinfo=ifd, compression="tiff_lzw")
    return path.stat().st_size


# --------------------------------------------------------------------------- vector extent


def majority_filter(mask: np.ndarray, min_neighbors: int = 5) -> np.ndarray:
    """3×3 majority filter (self included) to remove speckle before vectorising."""
    padded = np.pad(mask.astype(np.uint8), 1, mode="constant")
    h, w = mask.shape
    total = np.zeros(mask.shape, dtype=np.uint8)
    for dy in range(3):
        for dx in range(3):
            total += padded[dy : dy + h, dx : dx + w]
    return total >= min_neighbors


def wet_snow_extent_geojson(
    classes: np.ndarray,
    bbox: BoundingBox,
    *,
    wet_area_km2: float,
    threshold_db: float,
) -> dict[str, Any]:
    """Despeckled outline (GeoJSON) of class-3 wet snow."""
    height, width = classes.shape
    mask = majority_filter(classes == CLASS_WET)
    features: list[dict[str, Any]] = []
    if int(mask.sum()) >= 4:
        dlon_pix = (bbox.max_lon - bbox.min_lon) / width
        dlat_pix = (bbox.max_lat - bbox.min_lat) / height
        xs = bbox.min_lon + (np.arange(width) + 0.5) * dlon_pix
        ys = bbox.max_lat - (np.arange(height) + 0.5) * dlat_pix
        z = mask[::-1, :].astype(np.float64)
        cg = contourpy.contour_generator(x=xs, y=ys[::-1], z=z, fill_type=contourpy.FillType.OuterOffset)
        verts_list, offs_list = cg.filled(0.5, 1.5)

        min_poly_deg2 = dlon_pix * dlat_pix * 6.0
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
            parts = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
            polys.extend(p for p in parts if isinstance(p, Polygon) and p.is_valid and p.area >= min_poly_deg2)

        if polys:
            merged = MultiPolygon(polys).buffer(0) if len(polys) > 1 else polys[0]
            merged = merged.simplify(min(dlon_pix, dlat_pix) * 0.35, preserve_topology=True)
            merged = shapely.set_precision(merged, 1e-5)
            if not merged.is_empty:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "SAR wet-snow zone",
                            "area_km2": round(wet_area_km2, 2),
                            "ratio_threshold_db": threshold_db,
                        },
                        "geometry": mapping(merged),
                    }
                )
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------- asset export


@dataclass(frozen=True)
class WetSnowAssets:
    """Files written for one case plus the numbers shown in the viewer."""

    stats: dict[str, float]
    downloads: list[dict[str, Any]]
    threshold_db: float
    pre_snow_ndsi_min: float


def write_wet_snow_assets(
    result: WetSnowResult,
    bbox: BoundingBox,
    case_dir: Path,
    case_id: str,
    *,
    pre_snow_ndsi_min: float = PRE_SNOW_NDSI_MIN,
    ndsi_post: np.ndarray | None = None,
) -> WetSnowAssets:
    """Write PNGs, GeoTIFFs and GeoJSON under `case_dir` (a public/data/cases/<id> folder)."""
    case_dir.mkdir(parents=True, exist_ok=True)
    tif_dir = case_dir / "geotiffs"
    bounds = (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
    stats = summarize_wet_snow(
        result.classes, bbox, ndsi_post=ndsi_post, snow_ndsi_min=pre_snow_ndsi_min
    )

    (case_dir / "sar_wetsnow_class.png").write_bytes(encode_class_png(result.classes))
    (case_dir / "sar_wetsnow_ratio_vv.png").write_bytes(encode_ratio_png(result.ratio_vv_db))
    (case_dir / "sar_wetsnow_ratio_vh.png").write_bytes(encode_ratio_png(result.ratio_vh_db))

    geojson = wet_snow_extent_geojson(
        result.classes, bbox, wet_area_km2=stats["wetSnowKm2"], threshold_db=result.threshold_db
    )
    (case_dir / "sar_wetsnow_extent.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    tif_specs: list[tuple[str, str, str, np.ndarray, float]] = [
        ("wetsnow-class-tif", f"{case_id}_sar_wetsnow_class.tif", "SAR Wet Snow Class (.tif)", result.classes, 0),
        (
            "wetsnow-ratio-vv-tif",
            f"{case_id}_sar_wetsnow_ratio_vv.tif",
            "SAR Backscatter Ratio VV dB (.tif)",
            result.ratio_vv_db,
            -9999.0,
        ),
        (
            "wetsnow-ratio-vh-tif",
            f"{case_id}_sar_wetsnow_ratio_vh.tif",
            "SAR Backscatter Ratio VH dB (.tif)",
            result.ratio_vh_db,
            -9999.0,
        ),
    ]
    downloads: list[dict[str, Any]] = []
    for dl_id, filename, label, array, nodata in tif_specs:
        size = write_geotiff(tif_dir / filename, array, bounds, nodata=nodata)
        downloads.append(
            {
                "id": dl_id,
                "label": label,
                "datasetId": DATASET_ID,
                "phase": "change",
                "format": "geotiff",
                "url": f"data/cases/{case_id}/geotiffs/{filename}",
                "sizeBytes": size,
            }
        )
    return WetSnowAssets(
        stats=stats,
        downloads=downloads,
        threshold_db=result.threshold_db,
        pre_snow_ndsi_min=pre_snow_ndsi_min,
    )


# --------------------------------------------------------------------------- case definition


def wet_snow_dataset_definition(
    *,
    case_id: str,
    extent: list[float],
    pre_window: tuple[str, str],
    post_window: tuple[str, str],
    threshold_db: float,
) -> dict[str, Any]:
    """schemaVersion=2 dataset for the SAR wet-snow product (single 'change' frame)."""
    base = f"data/cases/{case_id}"
    thr = f"{threshold_db:g}".replace("-", "−")
    return {
        "id": DATASET_ID,
        "name": "SAR Wet Snow · Sentinel-1 ΔdB",
        "kind": "raster",
        "status": "real",
        "platform": "Sentinel-1",
        "sensor": "C-SAR IW VV+VH",
        "provider": "Derived from Google Earth Engine COPERNICUS/S1_GRD + Sentinel-2 NDSI",
        "product": f"Multitemporal backscatter ratio, wet snow when ΔVV and ΔVH ≤ {thr} dB",
        "attribution": "ESA Copernicus Sentinel-1/2 · method after Nagler & Rott (2000), Nagler et al. (2016)",
        "extent": extent,
        "source": {"type": "image", "encoding": "gray8", "nodata": 0},
        "render": {"opacity": 0.9, "resampling": "nearest"},
        "defaultVisible": False,
        "variables": [
            {
                "id": "wet_class",
                "label": "Wet-snow class (VV+VH ratio)",
                "quantity": "snow_wetness_class",
                "polarization": "VV+VH",
                "unit": "class 0–3",
                "legend": {
                    "min": 0,
                    "max": N_CLASSES,
                    "colors": ["#cbd5e1", "#22d3ee", "#2563eb"],
                    "discrete": {"1": "#cbd5e199", "2": "#22d3eecc", "3": "#2563ebf0"},
                    "nodata_label": CLASS_LEGEND_NOTE,
                },
            },
            _ratio_variable("ratio_vv", "VV ratio (After − Before)", "VV"),
            _ratio_variable("ratio_vh", "VH ratio (After − Before)", "VH"),
        ],
        "frames": [
            {
                "id": FRAME_ID,
                "datetime": f"{post_window[1]}T00:00:00Z",
                "label": f"Change {pre_window[0]} → {post_window[1]}",
                "phase": "change",
                "files": {
                    "wet_class": f"{base}/sar_wetsnow_class.png",
                    "ratio_vv": f"{base}/sar_wetsnow_ratio_vv.png",
                    "ratio_vh": f"{base}/sar_wetsnow_ratio_vh.png",
                },
            }
        ],
    }


def _ratio_variable(var_id: str, label: str, polarization: str) -> dict[str, Any]:
    vmin, vmax = RATIO_RANGE_DB
    return {
        "id": var_id,
        "label": label,
        "quantity": "backscatter_ratio",
        "polarization": polarization,
        "unit": "dB",
        "legend": {
            "min": vmin,
            "max": vmax,
            "colors": ["#312e81", "#2563eb", "#22d3ee", "#94a3b8", "#fbbf24", "#f97316"],
            "stops": [
                [-10.0, "#312e81f2"],
                [-6.0, "#2563ebe6"],
                [-3.0, "#22d3eecc"],
                [-2.0, "#67e8f9a0"],
                [-0.8, "#94a3b800"],
                [0.8, "#94a3b800"],
                [2.0, "#fbbf2499"],
                [5.0, "#f97316e6"],
            ],
            "nodata_label": RATIO_LEGEND_NOTE,
        },
    }


def wet_snow_vector_definition(case_id: str, threshold_db: float) -> dict[str, Any]:
    thr = f"{threshold_db:g}".replace("-", "−")
    return {
        "id": VECTOR_ID,
        "name": "SAR Wet Snow Zone",
        "type": "line",
        "status": "real",
        "simulated": False,
        "defaultVisible": False,
        "source": {"type": "geojson", "data": f"data/cases/{case_id}/sar_wetsnow_extent.geojson"},
        "paint": {"line-color": "#6366f1", "line-width": 1.6},
        "legend": {
            "title": "SAR wet snow",
            "items": [{"color": "#6366f1", "label": f"ΔVV and ΔVH ≤ {thr} dB"}],
        },
        "popupFields": [["area_km2", "Wet snow area", "km²"]],
    }


def attach_wet_snow_to_case(
    case_def: dict[str, Any],
    assets: WetSnowAssets,
    *,
    case_id: str,
    extent: list[float],
    pre_window: tuple[str, str],
    post_window: tuple[str, str],
) -> dict[str, Any]:
    """Idempotently add the wet-snow dataset, vector, downloads and metrics to a case dict."""
    dataset = wet_snow_dataset_definition(
        case_id=case_id,
        extent=extent,
        pre_window=pre_window,
        post_window=post_window,
        threshold_db=assets.threshold_db,
    )
    datasets = [d for d in case_def.get("datasets", []) if d.get("id") != DATASET_ID]
    insert_at = next((i + 1 for i, d in enumerate(datasets) if d.get("id") == "s1_sar"), len(datasets))
    datasets.insert(insert_at, dataset)
    case_def["datasets"] = datasets

    vectors = [v for v in case_def.get("vectors", []) if v.get("id") != VECTOR_ID]
    vectors.append(wet_snow_vector_definition(case_id, assets.threshold_db))
    case_def["vectors"] = vectors

    downloads = [d for d in case_def.get("downloads", []) if not str(d.get("id", "")).startswith(DOWNLOAD_PREFIX)]
    downloads.extend(assets.downloads)
    case_def["downloads"] = downloads

    metrics = case_def.setdefault("metrics", {})
    metrics["sarWetSnowKm2"] = assets.stats["wetSnowKm2"]
    metrics["sarPossibleWetKm2"] = assets.stats["possibleWetKm2"]
    metrics["sarWetSnowPct"] = assets.stats["wetSnowPct"]
    if "subsurfaceWetSnowKm2" in assets.stats:
        metrics["sarSubsurfaceWetSnowKm2"] = assets.stats["subsurfaceWetSnowKm2"]
    if "wetSnowInOpticalSnowPct" in assets.stats:
        metrics["sarWetInOpticalSnowPct"] = assets.stats["wetSnowInOpticalSnowPct"]

    source_note = (
        f"Derived · Sentinel-1 VV/VH backscatter ratio vs dry-snow reference "
        f"(threshold {assets.threshold_db:g} dB, snow mask NDSI ≥ {assets.pre_snow_ndsi_min:g})"
    )
    sources = [s for s in case_def.get("sources", []) if not str(s).startswith("Derived · Sentinel-1 VV/VH")]
    sources.append(source_note)
    case_def["sources"] = sources
    return case_def


# --------------------------------------------------------------------------- offline publish


def default_public_dir() -> Path:
    """Default sar-Madness `public/` directory (sibling project)."""
    return (PROJECT_ROOT.parent / "sar-Madness" / "sar-Madness" / "public").resolve()


@dataclass(frozen=True)
class WetSnowPublishResult:
    case_id: str
    case_dir: Path
    stats: dict[str, float]
    threshold_db: float
    png_count: int
    geotiff_count: int


def _load_gray8(public_dir: Path, rel_path: str) -> np.ndarray:
    with Image.open(public_dir / rel_path) as img:
        return np.array(img.convert("L"))


def _dataset(case_def: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    for ds in case_def.get("datasets", []):
        if ds.get("id") == dataset_id:
            return ds
    raise KeyError(f"Case has no '{dataset_id}' dataset; run publish-madness first.")


def _frame(dataset: dict[str, Any], phase: str) -> dict[str, Any]:
    for fr in dataset.get("frames", []):
        if fr.get("phase") == phase:
            return fr
    raise KeyError(f"Dataset '{dataset.get('id')}' has no '{phase}' frame.")


def publish_sar_wet_snow_for_case(
    case_id: str,
    public_dir: Path,
    *,
    threshold_db: float = WET_SNOW_THRESHOLD_DB,
    pre_snow_ndsi_min: float = PRE_SNOW_NDSI_MIN,
) -> WetSnowPublishResult:
    """Derive the wet-snow product from an already published glacier case (no GEE needed).

    Reads the case's own Sentinel-1 VV/VH and Sentinel-2 NDSI Before/After PNGs, so the
    product is reproducible from the repository contents alone and the original case is
    otherwise left untouched.
    """
    case_dir = public_dir / "data" / "cases" / case_id
    case_path = case_dir / "case.json"
    case_def = json.loads(case_path.read_text(encoding="utf-8"))

    s1 = _dataset(case_def, "s1_sar")
    s2 = _dataset(case_def, "s2_ndsi")
    s1_pre, s1_post = _frame(s1, "before")["files"], _frame(s1, "after")["files"]
    ndsi_pre_path = _frame(s2, "before")["files"]["ndsi"]
    ndsi_post_frame = next((fr for fr in s2.get("frames", []) if fr.get("phase") == "after"), None)
    ndsi_post_rel = ndsi_post_frame.get("files", {}).get("ndsi") if ndsi_post_frame else None
    ndsi_post_arr = (
        decode_gray8_db(_load_gray8(public_dir, ndsi_post_rel), *NDSI_PNG_RANGE)
        if ndsi_post_rel and (public_dir / ndsi_post_rel).is_file()
        else None
    )

    extent = [float(v) for v in s1["extent"]]
    bbox = BoundingBox(min_lon=extent[0], min_lat=extent[1], max_lon=extent[2], max_lat=extent[3])
    windows = case_def["metrics"]
    pre_window = tuple(str(windows["preWindow"]).split(".."))
    post_window = tuple(str(windows["postWindow"]).split(".."))
    if len(pre_window) != 2 or len(post_window) != 2:
        raise ValueError("metrics.preWindow / metrics.postWindow must look like 'YYYY-MM-DD..YYYY-MM-DD'")

    def sar(path: str, pol: str) -> np.ndarray:
        return decode_gray8_db(_load_gray8(public_dir, path), *S1_PNG_RANGE_DB[pol])

    result = classify_wet_snow(
        sar(s1_pre["vv"], "vv"),
        sar(s1_post["vv"], "vv"),
        sar(s1_pre["vh"], "vh"),
        sar(s1_post["vh"], "vh"),
        decode_gray8_db(_load_gray8(public_dir, ndsi_pre_path), *NDSI_PNG_RANGE),
        threshold_db=threshold_db,
        pre_snow_ndsi_min=pre_snow_ndsi_min,
    )
    assets = write_wet_snow_assets(
        result,
        bbox,
        case_dir,
        case_id,
        pre_snow_ndsi_min=pre_snow_ndsi_min,
        ndsi_post=ndsi_post_arr,
    )
    attach_wet_snow_to_case(
        case_def,
        assets,
        case_id=case_id,
        extent=extent,
        pre_window=(pre_window[0], pre_window[1]),
        post_window=(post_window[0], post_window[1]),
    )
    case_path.write_text(json.dumps(case_def, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return WetSnowPublishResult(
        case_id=case_id,
        case_dir=case_dir,
        stats=assets.stats,
        threshold_db=threshold_db,
        png_count=3,
        geotiff_count=len(assets.downloads),
    )
