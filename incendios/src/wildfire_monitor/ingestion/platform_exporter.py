"""Export GeoPulse wildfire outputs to sar-platform format preserving exact visual appearance."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from wildfire_monitor.config import BoundingBox
from wildfire_monitor.ingestion.geopulse_bridge import IGNORE, GeoRasterData, GeoGrid, reproject_raster_to_wgs84_grid

# Exact GeoPulse colormaps (geopulse/pipeline.py & geopulse/app/src/engine.js)
ColorStops = tuple[tuple[float, tuple[int, int, int, int]], ...]

PROB_BURN_PALETTE: ColorStops = (
    (0.2, (235, 70, 30, 0)),
    (0.5, (235, 70, 30, 150)),
    (1.0, (200, 20, 10, 235)),
)

CHANGE_PALETTE: ColorStops = (
    (0.2, (255, 170, 0, 0)),
    (0.5, (255, 170, 0, 140)),
    (1.0, (255, 120, 0, 230)),
)

UNCERTAINTY_PALETTE: ColorStops = (
    (0.0, (60, 20, 90, 0)),
    (0.3, (120, 40, 140, 110)),
    (0.7, (230, 80, 90, 190)),
    (1.0, (255, 210, 60, 230)),
)

SEVERITY_RGBA: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 0, 0),
    (127, 255, 212, 220),  # 1: low
    (255, 255, 0, 220),    # 2: moderate-low
    (255, 140, 0, 230),    # 3: moderate-high
    (255, 0, 0, 235),      # 4: high
)

_RASTER_LAYER_SPECS: dict[str, dict[str, Any]] = {
    "s2_pre": {
        "name": "Sentinel-2 RGB (Pre-incendio)",
        "steps": [0],
        "visible": True,
        "opacity": 1.0,
        "description": "Composición mediana Sentinel-2 L2A previa al incendio (bandas B04/B03/B02, gamma 1.4).",
        "legend": None,
    },
    "s1_pre": {
        "name": "Sentinel-1 SAR VV (Pre-incendio)",
        "steps": [0],
        "visible": False,
        "opacity": 0.9,
        "description": "Retrodispersión SAR banda C (VV en dB, rango -25 a 0 dB) previa al incendio.",
        "legend": None,
    },
    "s2_post": {
        "name": "Sentinel-2 RGB (Post-incendio)",
        "steps": [1, 2],
        "visible": True,
        "opacity": 1.0,
        "description": "Composición mediana Sentinel-2 L2A posterior al incendio mostrando cicatriz de quemado.",
        "legend": None,
    },
    "s1_post": {
        "name": "Sentinel-1 SAR VV (Post-incendio)",
        "steps": [1, 2],
        "visible": False,
        "opacity": 0.9,
        "description": "Retrodispersión SAR banda C (VV en dB) posterior al incendio.",
        "legend": None,
    },
    "burn_probability": {
        "name": "Probabilidad de quemado (GeoPulse)",
        "steps": [1],
        "visible": True,
        "opacity": 0.85,
        "description": "Probabilidad de incendio derivada de dNBR, NDVI de combustible y caída SAR VH.",
        "legend": {
            "title": "Probabilidad de quemado",
            "items": [
                {"color": "#eb461e", "label": "Moderada (0.5)"},
                {"color": "#c8140a", "label": "Alta (1.0)"},
            ],
        },
    },
    "burn_severity": {
        "name": "Severidad de quemado dNBR (Key & Benson)",
        "steps": [2],
        "visible": True,
        "opacity": 0.85,
        "description": "Clases discretas Key & Benson (2006) por dNBR: baja, moderada-baja, moderada-alta y alta.",
        "legend": {
            "title": "Severidad dNBR",
            "items": [
                {"color": "#7fffd4", "label": "Baja (0.10–0.27)"},
                {"color": "#ffff00", "label": "Moderada-baja (0.27–0.44)"},
                {"color": "#ff8c00", "label": "Moderada-alta (0.44–0.66)"},
                {"color": "#ff0000", "label": "Alta (> 0.66)"},
            ],
        },
    },
    "change_probability": {
        "name": "Probabilidad de cambio multimodal",
        "steps": [2],
        "visible": False,
        "opacity": 0.8,
        "description": "Cambio espectral/retrodispersión combinado entre ventanas pre y post incendio.",
        "legend": {
            "title": "Cambio S1/S2",
            "items": [
                {"color": "#ffaa00", "label": "Cambio moderado (0.5)"},
                {"color": "#ff7800", "label": "Cambio fuerte (1.0)"},
            ],
        },
    },
    "uncertainty": {
        "name": "Incertidumbre (entropía / solo-SAR)",
        "steps": [2],
        "visible": False,
        "opacity": 0.8,
        "description": "Entropía binaria de la estimación de quemado e incertidumbre en zonas solo-SAR.",
        "legend": {
            "title": "Incertidumbre",
            "items": [
                {"color": "#78288c", "label": "Baja–media (0.3)"},
                {"color": "#e6505a", "label": "Media–alta (0.7)"},
                {"color": "#ffd23c", "label": "Revisión requerida (≥ 0.8)"},
            ],
        },
    },
}


@dataclass(frozen=True)
class ExportedCaseSummary:
    """Result returned after exporting a GeoPulse wildfire dataset into sar-platform format."""

    case_id: str
    case_dir: Path
    case_json_path: Path
    tile_count: int
    layer_ids: tuple[str, ...]
    bounds_wgs84: tuple[float, float, float, float]


@dataclass(frozen=True)
class PlatformPublishReport:
    """Summary of publishing converted wildfire cases into `sar-platform`."""

    target_cases_dir: Path
    published_case_ids: tuple[str, ...]
    copied_downloads: int = 0


# ------------------------------------------------------------------ Exact GeoPulse RGBA Renderers


def ramp_rgba(values: np.ndarray, stops: ColorStops) -> np.ndarray:
    """Apply GeoPulse piecewise-linear RGBA palette (NaN or below first stop -> transparent)."""
    xs = [s[0] for s in stops]
    rgba_stops = [s[1] for s in stops]
    vals = np.asarray(values, dtype=np.float32)
    h, w = vals.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    valid = (~np.isnan(vals)) & (vals >= xs[0])
    if not np.any(valid):
        return out
    v = vals[valid]
    for ch in range(4):
        channel_stops = [float(c[ch]) for c in rgba_stops]
        interp = np.interp(v, xs, channel_stops, left=0.0, right=channel_stops[-1])
        out[..., ch][valid] = np.clip(np.round(interp), 0, 255).astype(np.uint8)
    return out


def render_severity_rgba(severity: np.ndarray) -> np.ndarray:
    """Render discrete Key & Benson burn severity map with exact GeoPulse colors."""
    lut = np.array(SEVERITY_RGBA, dtype=np.uint8)
    safe = np.where((severity >= 1) & (severity <= 4), severity, 0).astype(np.uint8)
    return lut[safe]


def render_s2_rgb_rgba(s2_bands: np.ndarray) -> np.ndarray:
    """Render Sentinel-2 B04/B03/B02 true color matching GeoPulse gamma 1.4 stretch."""
    h, w = s2_bands.shape[1], s2_bands.shape[2]
    out = np.zeros((h, w, 4), dtype=np.uint8)
    rgb_indices = (2, 1, 0)  # B04 (R), B03 (G), B02 (B)
    any_valid = np.zeros((h, w), dtype=bool)
    for out_ch, band_idx in enumerate(rgb_indices):
        b = s2_bands[band_idx]
        finite = np.isfinite(b)
        scaled = np.where(finite, np.clip(b / 0.3, 0.0, 1.0), 0.0) ** (1.0 / 1.4)
        px = np.clip(np.round(scaled * 255.0), 0, 255).astype(np.uint8)
        out[..., out_ch] = px
        any_valid |= finite & (px > 0)
    out[..., 3] = np.where(any_valid, 255, 0).astype(np.uint8)
    return out


def render_s1_gray_rgba(s1_bands: np.ndarray) -> np.ndarray:
    """Render Sentinel-1 VV dB [-25, 0] grayscale matching GeoPulse `grayImage`."""
    vv = s1_bands[0]
    finite = np.isfinite(vv)
    scaled = np.where(finite, np.clip((vv + 25.0) / 25.0, 0.0, 1.0), 0.0)
    px = np.clip(np.round(scaled * 255.0), 0, 255).astype(np.uint8)
    h, w = vv.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = px
    out[..., 1] = px
    out[..., 2] = px
    out[..., 3] = np.where(finite & (px > 0), 255, 0).astype(np.uint8)
    return out


def save_rgba_png(rgba: np.ndarray, path: Path) -> Path:
    """Write an RGBA NumPy array `(H, W, 4)` to a PNG file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG", optimize=True)
    return path


# ------------------------------------------------------------------ Web Mercator XYZ tile generator


def _lon_to_tile_x(lon: float, zoom: int) -> int:
    n = 2**zoom
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    return max(0, min(n - 1, x))


def _lat_to_tile_y(lat: float, zoom: int) -> int:
    lat_clipped = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat_clipped)
    n = 2**zoom
    y = int(
        math.floor(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        )
    )
    return max(0, min(n - 1, y))


def export_rgba_xyz_tiles(
    rgba: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    tiles_dir: Path,
    *,
    min_zoom: int = 10,
    max_zoom: int = 14,
    tile_size: int = 256,
) -> int:
    """Slice a WGS84 RGBA overlay into Web Mercator XYZ 256x256 PNG tiles."""
    west, south, east, north = bounds_wgs84
    src_h, src_w, _ = rgba.shape
    tiles_written = 0
    pix = np.arange(tile_size, dtype=np.float64) + 0.5

    for z in range(min_zoom, max_zoom + 1):
        n_tiles = float(2**z)
        tx_min = _lon_to_tile_x(west, z)
        tx_max = _lon_to_tile_x(east, z)
        ty_min = _lat_to_tile_y(north, z)
        ty_max = _lat_to_tile_y(south, z)

        for tx in range(tx_min, tx_max + 1):
            gx = (tx + pix / tile_size) / n_tiles
            lons = gx * 360.0 - 180.0
            src_cols = np.floor((lons - west) / (east - west) * src_w).astype(np.int64)
            col_ok = (src_cols >= 0) & (src_cols < src_w)
            if not np.any(col_ok):
                continue

            for ty in range(ty_min, ty_max + 1):
                gy = (ty + pix / tile_size) / n_tiles
                lats = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * gy))))
                src_rows = np.floor((north - lats) / (north - south) * src_h).astype(np.int64)
                row_ok = (src_rows >= 0) & (src_rows < src_h)
                if not np.any(row_ok):
                    continue

                valid_mask = np.outer(row_ok, col_ok)
                tile_rgba = np.zeros((tile_size, tile_size, 4), dtype=np.uint8)
                rr = np.clip(src_rows, 0, src_h - 1)[:, None]
                cc = np.clip(src_cols, 0, src_w - 1)[None, :]
                sampled = rgba[rr, cc]
                tile_rgba[valid_mask] = sampled[valid_mask]

                if int(tile_rgba[..., 3].max()) == 0:
                    continue

                out_file = tiles_dir / str(z) / str(tx) / f"{ty}.png"
                out_file.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(tile_rgba, mode="RGBA").save(out_file, format="PNG")
                tiles_written += 1

    return tiles_written


def _estimate_zoom_for_bbox(bbox: BoundingBox) -> float:
    span = max(bbox.max_lon - bbox.min_lon, bbox.max_lat - bbox.min_lat)
    if span <= 0.08:
        return 12.0
    if span <= 0.16:
        return 11.4
    if span <= 0.30:
        return 10.6
    return 9.8


def _format_window_date(window: str | None) -> str | None:
    if not window:
        return None
    parts = [p.strip() for p in window.split("/") if p.strip()]
    return parts[-1] if parts else None


# ------------------------------------------------------------------ Case JSON & index.json builders


def register_case_in_index(cases_dir: Path, case_id: str) -> Path:
    """Register `data/cases/<case_id>/case.json` inside `<cases_dir>/index.json`."""
    cases_dir.mkdir(parents=True, exist_ok=True)
    index_path = cases_dir / "index.json"
    rel_entry = f"data/cases/{case_id}/case.json"
    existing: list[str] = []
    if index_path.exists():
        try:
            doc = json.loads(index_path.read_text(encoding="utf-8"))
            raw_list = doc.get("cases") if isinstance(doc, dict) else []
            if isinstance(raw_list, list):
                existing = [str(item) for item in raw_list if isinstance(item, str)]
        except json.JSONDecodeError:
            existing = []
    if rel_entry not in existing:
        existing.append(rel_entry)
    index_path.write_text(json.dumps({"cases": existing}, indent=2), encoding="utf-8")
    return index_path


def export_wildfire_case(
    *,
    case_id: str,
    title: str,
    description: str,
    bbox: BoundingBox,
    before_window: str | None,
    after_window: str | None,
    layer_rgba_maps: dict[str, np.ndarray],
    aoi_geojson: dict[str, Any],
    burn_extent_geojson: dict[str, Any],
    summary: dict[str, Any],
    provenance: dict[str, Any],
    cases_dir: Path,
    min_zoom: int = 10,
    max_zoom: int = 14,
) -> ExportedCaseSummary:
    """Write a complete `sar-platform` Mapbox 3D case directory preserving GeoPulse colors."""
    case_dir = cases_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    bounds = tuple(bbox.to_ee_bounds())
    center_lon, center_lat = bbox.center()

    # Write vector GeoJSONs & native GeoPulse JSON/PNG artifacts
    (case_dir / "aoi.geojson").write_text(
        json.dumps(aoi_geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "burn_extent.geojson").write_text(
        json.dumps(burn_extent_geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    layers_dir = case_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Layer ordering matches GeoPulse's visual stacking:
    # optical/SAR imagery -> change -> uncertainty -> severity -> target probability -> perimeter outline
    ordered_raster_ids = (
        "s2_pre",
        "s1_pre",
        "s2_post",
        "s1_post",
        "change_probability",
        "uncertainty",
        "burn_severity",
        "burn_probability",
    )

    total_tiles = 0
    case_layers: list[dict[str, Any]] = [
        {
            "id": "aoi",
            "name": "Área de estudio (AOI)",
            "type": "line",
            "source": {"type": "geojson", "data": f"data/cases/{case_id}/aoi.geojson"},
            "paint": {
                "line-color": "#38bdf8",
                "line-width": 2,
                "line-dasharray": [3, 2],
            },
            "description": "Polígono límite del incendio configurado en GeoPulse.",
            "popupFields": [["name", "Área"]],
            "visible": True,
        }
    ]

    exported_raster_ids: list[str] = []
    for layer_id in ordered_raster_ids:
        rgba = layer_rgba_maps.get(layer_id)
        if rgba is None:
            continue
        exported_raster_ids.append(layer_id)
        save_rgba_png(rgba, layers_dir / f"{layer_id}.png")
        tile_count = export_rgba_xyz_tiles(
            rgba,
            bounds,  # type: ignore[arg-type]
            case_dir / "tiles" / layer_id,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
        )
        total_tiles += tile_count

        spec = _RASTER_LAYER_SPECS[layer_id]
        layer_entry: dict[str, Any] = {
            "id": layer_id,
            "name": spec["name"],
            "type": "raster",
            "source": {
                "type": "raster",
                "tiles": [f"data/cases/{case_id}/tiles/{layer_id}/{{z}}/{{x}}/{{y}}.png"],
                "tileSize": 256,
                "minzoom": min_zoom,
                "maxzoom": max_zoom,
                "bounds": list(bounds),
            },
            "paint": {"raster-opacity": spec["opacity"]},
            "steps": list(spec["steps"]),
            "visible": bool(spec["visible"]),
            "description": spec["description"],
        }
        if spec["legend"] is not None:
            layer_entry["legend"] = spec["legend"]
        case_layers.append(layer_entry)

    # Burn extent perimeter polygon outline (exact GeoPulse yellow #ffd60a + #000000 casing)
    case_layers.extend(
        [
            {
                "id": "burn-extent-casing",
                "name": "Perímetro de incendio (contorno)",
                "type": "line",
                "source": {"type": "geojson", "data": f"data/cases/{case_id}/burn_extent.geojson"},
                "paint": {"line-color": "#000000", "line-opacity": 0.6, "line-width": 3.4},
                "steps": [1, 2],
                "visible": True,
                "description": "Sombra base del perímetro de quemado GeoPulse (probabilidad ≥ 0.5).",
            },
            {
                "id": "burn-extent-outline",
                "name": "Perímetro de incendio (GeoPulse)",
                "type": "line",
                "source": {"type": "geojson", "data": f"data/cases/{case_id}/burn_extent.geojson"},
                "paint": {"line-color": "#ffd60a", "line-width": 1.6},
                "steps": [1, 2],
                "visible": True,
                "legend": {
                    "title": "Perímetro de quemado",
                    "items": [{"color": "#ffd60a", "label": "Probabilidad ≥ 0.5"}],
                },
                "popupFields": [["area_km2", "Área quemada", "km²"]],
                "description": "Contorno amarillo GeoPulse donde la probabilidad de quemado supera el umbral 0.5.",
            },
        ]
    )

    (case_dir / "layers.json").write_text(
        json.dumps(
            {
                "bounds": list(bounds),
                "layers": exported_raster_ids,
                "target": "burn",
                "extent": "burn_extent.geojson",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    affected_km2 = summary.get("affected_km2", 0.0)
    observed_km2 = summary.get("observed_km2", 0.0)
    review_km2 = summary.get("review_km2", 0.0)
    enriched_desc = (
        f"{description} "
        f"[Superficie quemada estimada: {affected_km2:.2f} km² de {observed_km2:.1f} km² observados; "
        f"zona de revisión por incertidumbre: {review_km2:.2f} km²]."
    )

    pre_date = _format_window_date(before_window)
    post_date = _format_window_date(after_window)
    case_payload: dict[str, Any] = {
        "id": case_id,
        "title": title,
        "description": enriched_desc,
        "demo": False,
        "source_origin": "geopulse",
        "view": {
            "center": [center_lon, center_lat],
            "zoom": _estimate_zoom_for_bbox(bbox),
            "pitch": 0,
            "bearing": 0,
        },
        "timeline": [
            {
                "label": "Pre-incendio",
                "date": pre_date,
                "note": f"Ventana previa ({before_window or 'pre'}). Compuestos Sentinel-2 RGB y Sentinel-1 VV.",
            },
            {
                "label": "Post-incendio (GeoPulse)",
                "date": post_date,
                "note": f"Ventana posterior ({after_window or 'post'}). Probabilidad de quemado y perímetro GeoPulse.",
            },
            {
                "label": "Severidad e Incertidumbre",
                "date": post_date,
                "note": "Severidad dNBR Key & Benson (2006), probabilidad de cambio multimodal e incertidumbre.",
            },
        ],
        "layers": case_layers,
    }

    case_json_path = case_dir / "case.json"
    case_json_path.write_text(
        json.dumps(case_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    register_case_in_index(cases_dir, case_id)

    return ExportedCaseSummary(
        case_id=case_id,
        case_dir=case_dir,
        case_json_path=case_json_path,
        tile_count=total_tiles,
        layer_ids=tuple(layer["id"] for layer in case_layers),
        bounds_wgs84=bounds,  # type: ignore[arg-type]
    )


def _merge_catalog_json(source_catalog: Path, target_catalog: Path, target_downloads_dir: Path) -> int:
    if not source_catalog.exists():
        return 0
    src_entries = json.loads(source_catalog.read_text(encoding="utf-8"))
    if not isinstance(src_entries, list):
        return 0

    existing: list[dict[str, Any]] = []
    if target_catalog.exists():
        try:
            loaded = json.loads(target_catalog.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except json.JSONDecodeError:
            existing = []

    seen = {(e.get("asset_name"), e.get("product_layer")) for e in existing}
    copied = 0
    for entry in src_entries:
        if not isinstance(entry, dict):
            continue
        src_file = Path(str(entry.get("local_path", "")))
        if not src_file.is_absolute():
            src_file = source_catalog.parent / src_file
        if not src_file.exists():
            continue
        rel_sub = (
            Path(src_file.parent.name) / src_file.name
            if src_file.parent != source_catalog.parent
            else Path(src_file.name)
        )
        dst_file = target_downloads_dir / rel_sub
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1

        new_entry = dict(entry)
        new_entry["local_path"] = str(dst_file)
        sig = (new_entry.get("asset_name"), new_entry.get("product_layer"))
        if sig not in seen:
            existing.append(new_entry)
            seen.add(sig)

    target_catalog.parent.mkdir(parents=True, exist_ok=True)
    target_catalog.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return copied


def publish_cases_to_sar_platform(
    source_cases_dir: Path,
    target_cases_dir: Path,
    *,
    source_downloads_dir: Path | None = None,
    target_downloads_dir: Path | None = None,
    case_ids: Sequence[str] | None = None,
) -> PlatformPublishReport:
    """Copy already-adapted wildfire cases (and optional GeoTIFF catalog) to sar-platform."""
    if not source_cases_dir.exists():
        raise FileNotFoundError(f"Source cases directory does not exist: {source_cases_dir}")

    available = [
        d.name
        for d in sorted(source_cases_dir.iterdir())
        if d.is_dir() and (d / "case.json").exists()
    ]
    selected = list(case_ids) if case_ids else available
    if not selected:
        raise ValueError(f"No wildfire cases found under {source_cases_dir}")

    target_cases_dir.mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    for cid in selected:
        src_dir = source_cases_dir / cid
        if not (src_dir / "case.json").exists():
            raise FileNotFoundError(f"Wildfire case '{cid}' not found at {src_dir}")
        dst_dir = target_cases_dir / cid
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        register_case_in_index(target_cases_dir, cid)
        published.append(cid)

    copied_downloads = 0
    if source_downloads_dir and target_downloads_dir:
        copied_downloads = _merge_catalog_json(
            source_downloads_dir / "catalog.json",
            target_downloads_dir / "catalog.json",
            target_downloads_dir,
        )

    return PlatformPublishReport(
        target_cases_dir=target_cases_dir,
        published_case_ids=tuple(published),
        copied_downloads=copied_downloads,
    )


def render_all_geopulse_layers(
    *,
    arrays: dict[str, np.ndarray],
    burn_probability: np.ndarray,
    burn_severity: np.ndarray | None,
    change_probability: np.ndarray,
    uncertainty: np.ndarray,
) -> dict[str, np.ndarray]:
    """Render all available GeoPulse arrays into RGBA maps using exact GeoPulse palettes."""
    rendered: dict[str, np.ndarray] = {
        "burn_probability": ramp_rgba(burn_probability, PROB_BURN_PALETTE),
        "change_probability": ramp_rgba(change_probability, CHANGE_PALETTE),
        "uncertainty": ramp_rgba(uncertainty, UNCERTAINTY_PALETTE),
    }
    if burn_severity is not None:
        rendered["burn_severity"] = render_severity_rgba(burn_severity)
    for period in ("pre", "post"):
        if f"s2_{period}" in arrays:
            rendered[f"s2_{period}"] = render_s2_rgb_rgba(arrays[f"s2_{period}"])
        if f"s1_{period}" in arrays:
            rendered[f"s1_{period}"] = render_s1_gray_rgba(arrays[f"s1_{period}"])
    return rendered


# ------------------------------------------------------------------ Schema v2 exporter (sar-Madness)


def encode_gray8(
    values: np.ndarray,
    vmin: float,
    vmax: float,
    *,
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Encode a 2D physical array to uint8 gray8 (0=nodata, 1..255 -> [vmin, vmax])."""
    arr = np.asarray(values, dtype=np.float32)
    ok = np.isfinite(arr) & (arr > -9000.0)
    if valid_mask is not None:
        ok &= valid_mask
    out = np.zeros(arr.shape, dtype=np.uint8)
    if not np.any(ok) or vmax <= vmin:
        return out
    norm = np.clip((arr[ok] - vmin) / (vmax - vmin), 0.0, 1.0)
    out[ok] = (1 + np.round(norm * 254.0)).astype(np.uint8)
    return out


def _save_gray8_png(gray: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray.astype(np.uint8), mode="L").save(path, format="PNG", optimize=True)
    return path


def _encode_severity_gray8(severity: np.ndarray) -> np.ndarray:
    out = np.zeros(severity.shape, dtype=np.uint8)
    lut = {1: 64, 2: 128, 3: 191, 4: 255}
    for cls, gval in lut.items():
        out[severity == cls] = gval
    return out


_WILDFIRE_GEOTIFF_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("s2_pre_nbr", "Sentinel-2 NBR Antes (.tif)", "s2_nbr", "before"),
    ("s2_post_nbr", "Sentinel-2 NBR Después (.tif)", "s2_nbr", "after"),
    ("s1_pre_vh", "Sentinel-1 VH Antes (.tif)", "s1_sar", "before"),
    ("s1_post_vh", "Sentinel-1 VH Después (.tif)", "s1_sar", "after"),
    ("burn_severity", "Severidad dNBR (.tif)", "geopulse_burn", "change"),
    ("burn_probability", "Probabilidad de quema (.tif)", "geopulse_burn", "change"),
    ("change_probability", "Probabilidad de cambio (.tif)", "geopulse_burn", "change"),
    ("uncertainty", "Incertidumbre (.tif)", "geopulse_burn", "change"),
    ("dem_elevation", "Copernicus DEM (.tif)", "reference", "reference"),
)


def _export_wildfire_geotiff_downloads(
    cid: str,
    src_case_dl_dir: Path,
    dst_case_dir: Path,
    base_prefix: str,
) -> list[dict[str, Any]]:
    """Copia GeoTIFFs reales de incendio a public/data y genera metadatos de descarga."""
    tif_dir = dst_case_dir / "geotiffs"
    tif_dir.mkdir(parents=True, exist_ok=True)
    downloads: list[dict[str, Any]] = []
    for suffix, label, dataset_id, phase in _WILDFIRE_GEOTIFF_SPECS:
        fname = f"{cid}_{suffix}.tif"
        src_tif = src_case_dl_dir / fname
        if not src_tif.exists():
            continue
        dst_tif = tif_dir / fname
        shutil.copy2(src_tif, dst_tif)
        downloads.append(
            {
                "id": suffix.replace("_", "-") + "-tif",
                "label": label,
                "datasetId": dataset_id,
                "phase": phase,
                "format": "geotiff",
                "url": f"{base_prefix}/geotiffs/{fname}",
                "sizeBytes": dst_tif.stat().st_size,
            }
        )
    return downloads


def register_case_in_catalog_v2(catalog_path: Path, case_rel_path: str, *, prepend: bool = False) -> Path:
    """Register a Schema v2 `case.json` path inside `public/data/catalog.json`."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    cases: list[str] = []
    if catalog_path.exists():
        try:
            doc = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("cases"), list):
                cases = [str(c) for c in doc["cases"] if isinstance(c, str) and "demo" not in c.lower()]
        except json.JSONDecodeError:
            cases = []
    if case_rel_path not in cases:
        if prepend:
            cases.insert(0, case_rel_path)
        else:
            cases.append(case_rel_path)
    catalog_path.write_text(
        json.dumps({"schemaVersion": 2, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog_path


def publish_cases_to_sar_madness_v2(
    source_cases_dir: Path,
    source_downloads_dir: Path,
    target_public_data_dir: Path,
    *,
    case_ids: Sequence[str] | None = None,
) -> list[str]:
    """Convert GeoPulse wildfire cases + GeoTIFFs into Schema v2 cases for `sar-Madness`."""
    from wildfire_monitor.config import AOI_PRESETS
    from wildfire_monitor.ingestion.geopulse_bridge import extract_burn_extent_geojson

    presets_by_id = {p.case_id: p for p in AOI_PRESETS.values()}
    available = [
        d.name
        for d in sorted(source_cases_dir.iterdir())
        if d.is_dir() and (d / "case.json").exists()
    ]
    selected = list(case_ids) if case_ids else available
    target_cases_dir = target_public_data_dir / "cases"
    catalog_path = target_public_data_dir / "catalog.json"
    published: list[str] = []

    for cid in selected:
        src_case_dir = source_cases_dir / cid
        src_dl_dir = source_downloads_dir / cid
        if not src_case_dir.exists() or not src_dl_dir.exists():
            continue

        old_case = json.loads((src_case_dir / "case.json").read_text(encoding="utf-8"))
        summary = json.loads((src_case_dir / "summary.json").read_text(encoding="utf-8"))
        provenance = json.loads((src_case_dir / "provenance.json").read_text(encoding="utf-8"))
        layers_meta = json.loads((src_case_dir / "layers.json").read_text(encoding="utf-8"))
        bounds = tuple(float(x) for x in layers_meta["bounds"])

        preset = presets_by_id.get(cid)
        before_win = provenance.get("before") or (preset.before if preset else "2024-01-01/2024-02-01")
        after_win = provenance.get("after") or (preset.after if preset else "2024-03-01/2024-04-01")
        pre_date = _format_window_date(before_win) or "2024-01-01"
        post_date = _format_window_date(after_win) or "2024-03-01"

        dst_case_dir = target_cases_dir / cid
        dst_rasters_dir = dst_case_dir / "rasters"
        dst_rasters_dir.mkdir(parents=True, exist_ok=True)

        # Read real physical GeoTIFF arrays
        bp = np.array(Image.open(src_dl_dir / f"{cid}_burn_probability.tif"), dtype=np.float32)
        bs = np.array(Image.open(src_dl_dir / f"{cid}_burn_severity.tif"), dtype=np.uint8)
        cp = np.array(Image.open(src_dl_dir / f"{cid}_change_probability.tif"), dtype=np.float32)
        unc = np.array(Image.open(src_dl_dir / f"{cid}_uncertainty.tif"), dtype=np.float32)
        nbr_pre = np.array(Image.open(src_dl_dir / f"{cid}_s2_pre_nbr.tif"), dtype=np.float32)
        nbr_post = np.array(Image.open(src_dl_dir / f"{cid}_s2_post_nbr.tif"), dtype=np.float32)
        vh_pre = np.array(Image.open(src_dl_dir / f"{cid}_s1_pre_vh.tif"), dtype=np.float32)
        vh_post = np.array(Image.open(src_dl_dir / f"{cid}_s1_post_vh.tif"), dtype=np.float32)
        dem = np.array(Image.open(src_dl_dir / f"{cid}_dem_elevation.tif"), dtype=np.float32)

        dem_valid = np.isfinite(dem) & (dem > -9000.0)
        dem_min = float(np.floor(np.min(dem[dem_valid]))) if np.any(dem_valid) else 0.0
        dem_max = float(np.ceil(np.max(dem[dem_valid]))) if np.any(dem_valid) else 1000.0
        if dem_max <= dem_min:
            dem_max = dem_min + 100.0

        # Encode gray8 PNG rasters preserving real physical ranges & GeoPulse masks
        _save_gray8_png(encode_gray8(bp, 0.0, 1.0), dst_rasters_dir / "burn_prob.png")
        _save_gray8_png(_encode_severity_gray8(bs), dst_rasters_dir / "burn_sev.png")
        _save_gray8_png(encode_gray8(cp, 0.0, 1.0), dst_rasters_dir / "change_prob.png")
        _save_gray8_png(encode_gray8(unc, 0.0, 1.0), dst_rasters_dir / "uncertainty.png")
        _save_gray8_png(
            encode_gray8(nbr_pre, -1.0, 1.0, valid_mask=(nbr_pre >= -1.05) & (nbr_pre <= 1.05)),
            dst_rasters_dir / "s2_pre_nbr.png",
        )
        _save_gray8_png(
            encode_gray8(nbr_post, -1.0, 1.0, valid_mask=(nbr_post >= -1.05) & (nbr_post <= 1.05)),
            dst_rasters_dir / "s2_post_nbr.png",
        )
        _save_gray8_png(encode_gray8(vh_pre, -30.0, 0.0), dst_rasters_dir / "s1_pre_vh.png")
        _save_gray8_png(encode_gray8(vh_post, -30.0, 0.0), dst_rasters_dir / "s1_post_vh.png")
        _save_gray8_png(encode_gray8(dem, dem_min, dem_max, valid_mask=dem_valid), dst_rasters_dir / "dem_elevation.png")

        # Copy S2 true color RGBA PNGs
        for rgb_name in ("s2_pre.png", "s2_post.png"):
            src_png = src_case_dir / "layers" / rgb_name
            if src_png.exists():
                shutil.copy2(src_png, dst_rasters_dir / rgb_name)

        # Copy AOI + regenerate clean simplified burn_extent.geojson with `date` property
        shutil.copy2(src_case_dir / "aoi.geojson", dst_case_dir / "aoi.geojson")
        extent_fc = extract_burn_extent_geojson(
            bp,
            bounds,  # type: ignore[arg-type]
            min_area_km2=0.01,
            simplify_deg=0.00012,
            step=1,
        )
        for feat in extent_fc["features"]:
            feat["properties"]["date"] = post_date
            feat["properties"]["threshold"] = 0.5
        (dst_case_dir / "burn_extent.geojson").write_text(
            json.dumps(extent_fc, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

        # Save summary & compact provenance
        (dst_case_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        compact_prov = {
            "model": provenance.get("model"),
            "engine": provenance.get("engine"),
            "before": before_win,
            "after": after_win,
            "sensors": provenance.get("sensors", {}),
            "bounds_wgs84": list(bounds),
        }
        (dst_case_dir / "provenance.json").write_text(
            json.dumps(compact_prov, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        view = old_case.get("view", {})
        center = view.get("center", [(bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0])
        zoom = view.get("zoom", 11.4)
        base_prefix = f"data/cases/{cid}"

        datasets: list[dict[str, Any]] = [
            {
                "id": "geopulse_burn",
                "name": "GeoPulse · Fusión SAR + Óptico",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2 L2A + Sentinel-1 RTC",
                "sensor": "MSI + C-SAR",
                "provider": "Microsoft Planetary Computer",
                "product": "GeoPulse Physics Baseline v0.1.0",
                "attribution": "Copernicus Sentinel-1/2 · GeoPulse",
                "variables": [
                    {
                        "id": "burn_prob",
                        "label": "Probabilidad de quemado",
                        "quantity": "burn_probability",
                        "polarization": None,
                        "unit": "prob (0–1)",
                        "legend": {
                            "min": 0,
                            "max": 1,
                            "colors": ["#eb461e", "#c8140a"],
                            "stops": [[0.2, "#eb461e00"], [0.5, "#eb461e96"], [1.0, "#c8140aeb"]],
                            "nodata_label": "Transparente = sin quemado (< 0.2)",
                        },
                    },
                    {
                        "id": "burn_sev",
                        "label": "Severidad dNBR (Key & Benson)",
                        "quantity": "burn_severity",
                        "polarization": None,
                        "unit": "clase 0–4",
                        "legend": {
                            "min": 0,
                            "max": 4,
                            "colors": ["#7fffd4", "#ffff00", "#ff8c00", "#ff0000"],
                            "discrete": {
                                "1": "#7fffd4dc",
                                "2": "#ffff00dc",
                                "3": "#ff8c00e6",
                                "4": "#ff0000eb",
                            },
                            "nodata_label": "1 Baja · 2 Mod-baja · 3 Mod-alta · 4 Alta",
                        },
                    },
                    {
                        "id": "change_prob",
                        "label": "Cambio multimodal S1/S2",
                        "quantity": "change_probability",
                        "polarization": None,
                        "unit": "prob (0–1)",
                        "legend": {
                            "min": 0,
                            "max": 1,
                            "colors": ["#ffaa00", "#ff7800"],
                            "stops": [[0.2, "#ffaa0000"], [0.5, "#ffaa008c"], [1.0, "#ff7800e6"]],
                            "nodata_label": "Transparente = sin cambio (< 0.2)",
                        },
                    },
                    {
                        "id": "uncertainty",
                        "label": "Incertidumbre Shannon H(p)",
                        "quantity": "uncertainty",
                        "polarization": None,
                        "unit": "bits (0–1)",
                        "legend": {
                            "min": 0,
                            "max": 1,
                            "colors": ["#3c145a", "#78288c", "#e6505a", "#ffd23c"],
                            "stops": [[0.0, "#3c145a00"], [0.3, "#78288c6e"], [0.7, "#e6505abe"], [1.0, "#ffd23ce6"]],
                            "nodata_label": "≥ 0.80 bits = revisión · ≥ 0.50 = solo-SAR",
                        },
                    },
                ],
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "extent": list(bounds),
                "crs": "EPSG:4326",
                "resolution": {"value": 30, "unit": "m"},
                "render": {"opacity": 0.88, "resampling": "linear"},
                "defaultVisible": True,
                "frames": [
                    {
                        "id": "gp-post",
                        "datetime": post_date,
                        "files": {
                            "burn_prob": f"{base_prefix}/rasters/burn_prob.png",
                            "burn_sev": f"{base_prefix}/rasters/burn_sev.png",
                            "change_prob": f"{base_prefix}/rasters/change_prob.png",
                            "uncertainty": f"{base_prefix}/rasters/uncertainty.png",
                        },
                    }
                ],
            },
            {
                "id": "s2_rgb",
                "name": "Sentinel-2 L2A · Óptico RGB",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2A/2B/2C",
                "sensor": "MSI",
                "provider": "Microsoft Planetary Computer",
                "product": "True Color B04/B03/B02 (gamma 1.4)",
                "attribution": "ESA Copernicus Sentinel-2",
                "variables": [
                    {
                        "id": "rgb",
                        "label": "Color verdadero (B04/B03/B02)",
                        "quantity": "reflectance",
                        "polarization": None,
                        "unit": "RGB",
                    }
                ],
                "source": {"type": "image", "encoding": "rgba"},
                "extent": list(bounds),
                "crs": "EPSG:4326",
                "resolution": {"value": 30, "unit": "m"},
                "render": {"opacity": 1.0, "resampling": "linear"},
                "defaultVisible": True,
                "frames": [
                    {"id": "s2-pre", "datetime": pre_date, "label": f"Antes · {before_win}", "phase": "before", "files": {"rgb": f"{base_prefix}/rasters/s2_pre.png"}},
                    {"id": "s2-post", "datetime": post_date, "label": f"Después · {after_win}", "phase": "after", "files": {"rgb": f"{base_prefix}/rasters/s2_post.png"}},
                ],
            },
            {
                "id": "s2_nbr",
                "name": "Sentinel-2 · Índice NBR (Vegetación/Quemado)",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-2",
                "sensor": "MSI B8A/B12",
                "provider": "Microsoft Planetary Computer",
                "product": "Normalized Burn Ratio",
                "attribution": "ESA Copernicus Sentinel-2",
                "variables": [
                    {
                        "id": "NBR",
                        "label": "Índice NBR",
                        "quantity": "spectral_index",
                        "polarization": None,
                        "unit": "índice",
                        "legend": {
                            "min": -1,
                            "max": 1,
                            "colors": ["#7a1f1f", "#e4572e", "#f4e9a8", "#66bb6a", "#1b5e20"],
                            "nodata_label": "Transparente = nube / sin dato óptico",
                        },
                    }
                ],
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "extent": list(bounds),
                "crs": "EPSG:4326",
                "resolution": {"value": 30, "unit": "m"},
                "render": {"opacity": 0.8, "resampling": "linear"},
                "defaultVisible": False,
                "frames": [
                    {"id": "nbr-pre", "datetime": pre_date, "label": f"Antes · {before_win}", "phase": "before", "files": {"NBR": f"{base_prefix}/rasters/s2_pre_nbr.png"}},
                    {"id": "nbr-post", "datetime": post_date, "label": f"Después · {after_win}", "phase": "after", "files": {"NBR": f"{base_prefix}/rasters/s2_post_nbr.png"}},
                ],
            },
            {
                "id": "s1_sar",
                "name": "Sentinel-1 RTC · Radar SAR VH (Todo-Clima)",
                "kind": "raster",
                "status": "real",
                "platform": "Sentinel-1A",
                "sensor": "C-SAR",
                "provider": "Microsoft Planetary Computer",
                "product": "RTC Gamma0 VH",
                "attribution": "ESA Copernicus Sentinel-1",
                "variables": [
                    {
                        "id": "VH",
                        "label": "Retrodispersión VH",
                        "quantity": "backscatter",
                        "polarization": "VH",
                        "unit": "dB",
                        "legend": {
                            "min": -30,
                            "max": 0,
                            "colors": ["#0b0f14", "#64748b", "#f8fafc"],
                            "nodata_label": "Transparente = sin dato",
                        },
                    }
                ],
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "extent": list(bounds),
                "crs": "EPSG:4326",
                "resolution": {"value": 30, "unit": "m"},
                "render": {"opacity": 0.85, "resampling": "linear"},
                "defaultVisible": False,
                "frames": [
                    {"id": "s1-pre", "datetime": pre_date, "label": f"Antes · {before_win}", "phase": "before", "files": {"VH": f"{base_prefix}/rasters/s1_pre_vh.png"}},
                    {"id": "s1-post", "datetime": post_date, "label": f"Después · {after_win}", "phase": "after", "files": {"VH": f"{base_prefix}/rasters/s1_post_vh.png"}},
                ],
            },
            {
                "id": "dem",
                "name": "Copernicus DEM 30m · Elevación",
                "kind": "raster",
                "status": "real",
                "platform": "Copernicus GLO-30",
                "sensor": "DEM",
                "provider": "Microsoft Planetary Computer",
                "product": "Elevación superficial",
                "attribution": "Copernicus GLO-30 DEM",
                "variables": [
                    {
                        "id": "elev",
                        "label": "Elevación",
                        "quantity": "elevation",
                        "polarization": None,
                        "unit": "m",
                        "legend": {
                            "min": dem_min,
                            "max": dem_max,
                            "colors": ["#1e3a5f", "#2a9d8f", "#e9c46a", "#e76f51", "#f8fafc"],
                        },
                    }
                ],
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "extent": list(bounds),
                "crs": "EPSG:4326",
                "resolution": {"value": 30, "unit": "m"},
                "render": {"opacity": 0.7, "resampling": "linear"},
                "defaultVisible": False,
                "frames": [
                    {"id": "dem-ref", "datetime": pre_date, "files": {"elev": f"{base_prefix}/rasters/dem_elevation.png"}}
                ],
            },
        ]

        vectors: list[dict[str, Any]] = [
            {
                "id": "aoi",
                "name": "Área de estudio (AOI)",
                "type": "line",
                "status": "real",
                "simulated": False,
                "source": {"type": "geojson", "data": f"{base_prefix}/aoi.geojson"},
                "paint": {"line-color": "#38bdf8", "line-width": 2, "line-dasharray": [3, 2]},
                "description": "Polígono límite del incendio configurado en GeoPulse.",
                "popupFields": [["name", "Área"]],
            },
            {
                "id": "burn-extent-casing",
                "name": "Perímetro quemado (contorno)",
                "type": "line",
                "status": "real",
                "simulated": False,
                "source": {"type": "geojson", "data": f"{base_prefix}/burn_extent.geojson"},
                "time": {"property": "date", "mode": "asof"},
                "paint": {"line-color": "#000000", "line-opacity": 0.65, "line-width": 3.4},
                "description": "Sombra base del perímetro GeoPulse (probabilidad ≥ 0.50).",
            },
            {
                "id": "burn-extent-outline",
                "name": "Perímetro de quemado (GeoPulse)",
                "type": "line",
                "status": "real",
                "simulated": False,
                "source": {"type": "geojson", "data": f"{base_prefix}/burn_extent.geojson"},
                "time": {"property": "date", "mode": "asof"},
                "paint": {"line-color": "#ffd60a", "line-width": 1.8},
                "legend": {
                    "title": "Perímetro GeoPulse",
                    "items": [{"color": "#ffd60a", "label": "Probabilidad ≥ 0.50"}],
                },
                "description": "Contorno amarillo GeoPulse donde la probabilidad de quemado supera 0.50.",
                "popupFields": [["area_km2", "Área del polígono", "km²"], ["threshold", "Umbral P(quemado)"]],
            },
        ]

        datasets = [ds for ds in datasets if ds.get("id") != "dem"]
        downloads = _export_wildfire_geotiff_downloads(cid, src_dl_dir, dst_case_dir, base_prefix)

        case_v2: dict[str, Any] = {
            "simulated": False,
            "category": "incendio",
            "metrics": {
                "preWindow": before_win,
                "postWindow": after_win,
                "affectedKm2": summary.get("affected_km2"),
                "observedKm2": summary.get("observed_km2"),
            },
            "downloads": downloads,
            "schemaVersion": 2,
            "id": cid,
            "status": "real",
            "title": old_case.get("title", cid),
            "description": old_case.get("description", ""),
            "view": {
                "center": center,
                "zoom": zoom,
                "pitch": 46,
                "bearing": 14,
            },
            "datasets": datasets,
            "vectors": vectors,
            "wildfire": {
                "kind": "geopulse-real",
                "beforeWindow": before_win,
                "afterWindow": after_win,
                "summary": summary,
                "provenance": compact_prov,
                "extent": list(bounds),
                "demRange": [dem_min, dem_max],
                "nbrRange": [-1.0, 1.0],
                "vhRange": [-30.0, 0.0],
                "physicsFiles": {
                    "s2_pre_nbr": f"{base_prefix}/rasters/s2_pre_nbr.png",
                    "s2_post_nbr": f"{base_prefix}/rasters/s2_post_nbr.png",
                    "s1_pre_vh": f"{base_prefix}/rasters/s1_pre_vh.png",
                    "s1_post_vh": f"{base_prefix}/rasters/s1_post_vh.png",
                    "dem_elevation": f"{base_prefix}/rasters/dem_elevation.png",
                    "burn_prob": f"{base_prefix}/rasters/burn_prob.png",
                    "burn_sev": f"{base_prefix}/rasters/burn_sev.png",
                    "uncertainty": f"{base_prefix}/rasters/uncertainty.png",
                },
            },
        }

        (dst_case_dir / "case.json").write_text(
            json.dumps(case_v2, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        register_case_in_catalog_v2(catalog_path, f"data/cases/{cid}/case.json")
        published.append(cid)

    return published
