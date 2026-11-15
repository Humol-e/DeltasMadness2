"""End-to-end wildfire acquisition, GeoPulse -> sar-platform conversion, and local staging pipeline."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from wildfire_monitor.config import (
    AOI_PRESETS,
    AOIPreset,
    BoundingBox,
    Settings,
    bbox_from_geojson,
    resolve_aoi,
    resolve_event,
)
from wildfire_monitor.ingestion.geopulse_bridge import (
    GeoGrid,
    GeoRasterData,
    GeoTiffMetadata,
    WildfireAnalysisResult,
    acquire_wildfire_scenes_and_physics,
    extract_burn_extent_geojson,
    make_geo_grid,
    parse_wildfire_geotiff_metadata,
    read_geopulse_geotiff,
    reproject_raster_to_wgs84_grid,
    write_sar_compatible_geotiff,
)
from wildfire_monitor.ingestion.platform_exporter import (
    CHANGE_PALETTE,
    PROB_BURN_PALETTE,
    UNCERTAINTY_PALETTE,
    ExportedCaseSummary,
    PlatformPublishReport,
    publish_cases_to_sar_platform,
    ramp_rgba,
    render_all_geopulse_layers,
    render_severity_rgba,
    export_wildfire_case,
)
from wildfire_monitor.pc_auth import PlanetaryComputerClient


@dataclass(frozen=True)
class LocalStagedFile:
    """Single GeoTIFF staged in the local catalog for `sar-platform` compatibility."""

    local_path: Path
    asset_name: str
    sensor: str
    product_layer: str
    start_time_utc: str | None
    end_time_utc: str | None
    properties: dict[str, str | int | float]


@dataclass(frozen=True)
class LocalStagingReport:
    """Summary returned after writing/updating local GeoTIFF `catalog.json`."""

    source: str
    download_dir: Path
    catalog_path: Path
    files: tuple[LocalStagedFile, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class Stage1WildfireReport:
    """Complete summary for a GeoPulse -> sar-platform wildfire run."""

    case_id: str
    title: str
    bbox: BoundingBox
    staging_report: LocalStagingReport
    exported_case: ExportedCaseSummary | None = None
    publish_report: PlatformPublishReport | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


def load_local_catalog(download_dir: Path) -> list[dict[str, Any]]:
    """Read existing `catalog.json` entries if present."""
    catalog_path = download_dir / "catalog.json"
    if not catalog_path.exists():
        return []
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else []


def _write_local_catalog(
    download_dir: Path,
    metadata_records: Sequence[GeoTiffMetadata],
    *,
    merge_existing: bool = True,
) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = download_dir / "catalog.json"

    existing: list[dict[str, Any]] = load_local_catalog(download_dir) if merge_existing else []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_keys: list[tuple[str, str]] = []
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        key = (str(entry.get("asset_name", "")), str(entry.get("product_layer", "")))
        by_key[key] = entry
        ordered_keys.append(key)

    for meta in metadata_records:
        key = (meta.asset_name, meta.product_layer)
        entry = {
            "local_path": str(meta.local_path),
            "asset_name": meta.asset_name,
            "sensor": meta.sensor,
            "product_layer": meta.product_layer,
            "start_time_utc": meta.start_time_utc,
            "end_time_utc": meta.end_time_utc,
            "properties": dict(meta.properties),
        }
        if key not in by_key:
            ordered_keys.append(key)
        by_key[key] = entry

    entries = [by_key[k] for k in ordered_keys]
    catalog_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return catalog_path


def stage_local_geotiffs_catalog(
    geotiff_paths: Sequence[Path],
    download_dir: Path,
    *,
    source: str = "geopulse-wildfire",
    event_id: str | None = None,
    sensor: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    merge_existing: bool = True,
) -> LocalStagingReport:
    """Validate and register wildfire GeoTIFF files in `catalog.json`."""
    metadata_records = [
        parse_wildfire_geotiff_metadata(
            path,
            sensor=sensor,
            event_id=event_id,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
        )
        for path in geotiff_paths
    ]
    catalog_path = _write_local_catalog(
        download_dir, metadata_records, merge_existing=merge_existing
    )
    staged = tuple(
        LocalStagedFile(
            local_path=meta.local_path,
            asset_name=meta.asset_name,
            sensor=meta.sensor,
            product_layer=meta.product_layer,
            start_time_utc=meta.start_time_utc,
            end_time_utc=meta.end_time_utc,
            properties=meta.properties,
        )
        for meta in metadata_records
    )
    return LocalStagingReport(
        source=source,
        download_dir=download_dir,
        catalog_path=catalog_path,
        files=staged,
        dry_run=False,
    )


def _window_to_utc(window: str | None) -> tuple[str | None, str | None]:
    if not window:
        return None, None
    parts = [p.strip() for p in window.split("/") if p.strip()]
    if not parts:
        return None, None
    start = parts[0] if "T" in parts[0] else f"{parts[0]}T00:00:00Z"
    end = parts[-1] if "T" in parts[-1] else f"{parts[-1]}T23:59:59Z"
    return start, end


def _compute_nbr(s2_bands: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        return ((s2_bands[3] - s2_bands[5]) / (s2_bands[3] + s2_bands[5] + 1e-6)).astype(np.float32)


def run_wildfire_to_platform(
    aoi_spec: str,
    settings: Settings,
    *,
    before: str | None = None,
    after: str | None = None,
    case_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    sensors: Sequence[str] = ("s1", "s2"),
    download_dir: Path | None = None,
    cases_dir: Path | None = None,
    max_dim: int | None = None,
    dry_run: bool = False,
    publish: bool = False,
    client: PlanetaryComputerClient | None = None,
    precomputed: WildfireAnalysisResult | None = None,
) -> Stage1WildfireReport:
    """Acquire GeoPulse wildfire data from Planetary Computer and export sar-platform formats."""
    preset: AOIPreset | None = resolve_event(aoi_spec, geopulse_dir=settings.resolved_geopulse_dir())
    if preset is not None:
        resolved_title = title or preset.name
        bbox = preset.bbox
        resolved_before = before or preset.before
        resolved_after = after or preset.after
        resolved_case_id = case_id or preset.case_id
        resolved_desc = description or preset.description
        aoi_geom = bbox.to_geojson_polygon()
        gp_aoi_path = (
            settings.resolved_geopulse_dir() / "examples" / (preset.example_dir_name or "") / "aoi.geojson"
        )
        if preset.example_dir_name and gp_aoi_path.exists():
            doc = json.loads(gp_aoi_path.read_text(encoding="utf-8"))
            aoi_geom = doc.get("geometry", aoi_geom)
    else:
        aoi_name, bbox = resolve_aoi(aoi_spec, geopulse_dir=settings.resolved_geopulse_dir())
        if not before or not after:
            raise ValueError("Custom AOIs require explicit `before` and `after` YYYY-MM-DD/YYYY-MM-DD windows.")
        resolved_title = title or aoi_name
        resolved_before = before
        resolved_after = after
        resolved_case_id = case_id or "wildfire-custom"
        resolved_desc = description or f"GeoPulse analysis for {resolved_title}."
        aoi_geom = bbox.to_geojson_polygon()

    resolved_download_dir = download_dir or settings.resolved_download_dir()
    resolved_cases_dir = cases_dir or settings.resolved_cases_dir()

    if dry_run:
        return Stage1WildfireReport(
            case_id=resolved_case_id,
            title=resolved_title,
            bbox=bbox,
            staging_report=LocalStagingReport(
                source="geopulse-stac",
                download_dir=resolved_download_dir,
                catalog_path=resolved_download_dir / "catalog.json",
                files=(),
                dry_run=True,
            ),
            summary={
                "before": resolved_before,
                "after": resolved_after,
                "sensors": list(sensors),
                "aoi_km2": bbox.area_km2(),
            },
            dry_run=True,
        )

    result = precomputed or acquire_wildfire_scenes_and_physics(
        bbox,
        resolved_before,
        resolved_after,
        sensors=sensors,
        aoi_geometry=aoi_geom,
        max_dim=max_dim or settings.grid_max_dim,
        max_cloud_cover=settings.max_cloud_cover,
        client=client,
        settings=settings,
    )

    event_download_dir = resolved_download_dir / resolved_case_id
    event_download_dir.mkdir(parents=True, exist_ok=True)
    bounds = tuple(bbox.to_ee_bounds())

    geotiff_paths: list[Path] = []
    # Core GeoPulse output GeoTIFFs written in uncompressed single-band TIFF readable by PIL & sar-platform
    geotiff_paths.append(
        write_sar_compatible_geotiff(
            event_download_dir / f"{resolved_case_id}_burn_probability.tif",
            result.burn_probability,
            bounds,  # type: ignore[arg-type]
            nodata=-9999.0,
        )
    )
    if result.burn_severity is not None:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_burn_severity.tif",
                result.burn_severity,
                bounds,  # type: ignore[arg-type]
                nodata=255.0,
            )
        )
    geotiff_paths.append(
        write_sar_compatible_geotiff(
            event_download_dir / f"{resolved_case_id}_change_probability.tif",
            result.change_probability,
            bounds,  # type: ignore[arg-type]
            nodata=-9999.0,
        )
    )
    geotiff_paths.append(
        write_sar_compatible_geotiff(
            event_download_dir / f"{resolved_case_id}_uncertainty.tif",
            result.uncertainty,
            bounds,  # type: ignore[arg-type]
            nodata=-9999.0,
        )
    )
    if "s2_pre" in result.arrays:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_s2_pre_nbr.tif",
                _compute_nbr(result.arrays["s2_pre"]),
                bounds,  # type: ignore[arg-type]
            )
        )
    if "s2_post" in result.arrays:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_s2_post_nbr.tif",
                _compute_nbr(result.arrays["s2_post"]),
                bounds,  # type: ignore[arg-type]
            )
        )
    if "s1_pre" in result.arrays:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_s1_pre_vh.tif",
                result.arrays["s1_pre"][1],
                bounds,  # type: ignore[arg-type]
            )
        )
    if "s1_post" in result.arrays:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_s1_post_vh.tif",
                result.arrays["s1_post"][1],
                bounds,  # type: ignore[arg-type]
            )
        )
    if "dem" in result.arrays:
        geotiff_paths.append(
            write_sar_compatible_geotiff(
                event_download_dir / f"{resolved_case_id}_dem_elevation.tif",
                result.arrays["dem"][0],
                bounds,  # type: ignore[arg-type]
            )
        )

    pre_start, _ = _window_to_utc(resolved_before)
    _, post_end = _window_to_utc(resolved_after)
    staging_report = stage_local_geotiffs_catalog(
        geotiff_paths,
        resolved_download_dir,
        source="geopulse-stac",
        event_id=resolved_case_id,
        start_time_utc=pre_start,
        end_time_utc=post_end,
        merge_existing=True,
    )

    provenance = {
        "model": f"{result.summary['model']['id']}-v{result.summary['model']['version']}",
        "engine": result.summary["engine"],
        "before": resolved_before,
        "after": resolved_after,
        "sensors": {k: [s.get("id") for s in v] for k, v in result.scenes.items()},
        "scenes": result.scenes,
        "target_crs": result.grid.crs,
        "bounds_wgs84": list(bounds),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    rgba_maps = render_all_geopulse_layers(
        arrays=result.arrays,
        burn_probability=result.burn_probability,
        burn_severity=result.burn_severity,
        change_probability=result.change_probability,
        uncertainty=result.uncertainty,
    )

    # Also keep GeoPulse summary/extent/layers alongside downloads for portability
    (event_download_dir / "summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (event_download_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (event_download_dir / "burn_extent.geojson").write_text(
        json.dumps(result.burn_extent_geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (event_download_dir / "aoi.geojson").write_text(
        json.dumps(result.aoi_geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    exported = export_wildfire_case(
        case_id=resolved_case_id,
        title=resolved_title,
        description=resolved_desc,
        bbox=bbox,
        before_window=resolved_before,
        after_window=resolved_after,
        layer_rgba_maps=rgba_maps,
        aoi_geojson=result.aoi_geojson,
        burn_extent_geojson=result.burn_extent_geojson,
        summary=result.summary,
        provenance=provenance,
        cases_dir=resolved_cases_dir,
        min_zoom=settings.min_zoom,
        max_zoom=settings.max_zoom,
    )

    pub_report: PlatformPublishReport | None = None
    if publish:
        pub_report = publish_cases_to_sar_platform(
            resolved_cases_dir,
            settings.resolved_sar_platform_cases_dir(),
            source_downloads_dir=resolved_download_dir,
            target_downloads_dir=settings.resolved_sar_platform_downloads_dir(),
            case_ids=[resolved_case_id],
        )

    return Stage1WildfireReport(
        case_id=resolved_case_id,
        title=resolved_title,
        bbox=bbox,
        staging_report=staging_report,
        exported_case=exported,
        publish_report=pub_report,
        summary=result.summary,
        dry_run=False,
    )


def convert_geopulse_dir_to_platform(
    source_dir: Path,
    settings: Settings,
    *,
    case_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    download_dir: Path | None = None,
    cases_dir: Path | None = None,
    publish: bool = False,
) -> Stage1WildfireReport:
    """Convert an existing local GeoPulse output directory into sar-platform formats without altering look."""
    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"GeoPulse output directory does not exist: {src}")

    prob_path = src / "burn_probability.tif"
    if not prob_path.exists():
        raise FileNotFoundError(f"Expected GeoPulse output file 'burn_probability.tif' in {src}")

    prob_raster = read_geopulse_geotiff(prob_path)
    bounds = prob_raster.bounds_wgs84
    bbox = BoundingBox(min_lon=bounds[0], min_lat=bounds[1], max_lon=bounds[2], max_lat=bounds[3])
    if prob_raster.crs_epsg == 4326:
        h_px, w_px = prob_raster.data.shape
        base_grid = make_geo_grid(bbox, max_dim=max(w_px, h_px))
        grid = GeoGrid(
            bbox=bbox,
            width=w_px,
            height=h_px,
            dx_deg=(bbox.max_lon - bbox.min_lon) / w_px,
            dy_deg=(bbox.max_lat - bbox.min_lat) / h_px,
            pixel_width_m=(base_grid.pixel_width_m * base_grid.width) / w_px,
            pixel_height_m=(base_grid.pixel_height_m * base_grid.height) / h_px,
        )
    else:
        grid = make_geo_grid(bbox, max_dim=max(prob_raster.data.shape[1], prob_raster.data.shape[0]))

    summary_path = src / "summary.json"
    summary: dict[str, Any] = (
        json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    )
    prov_path = src / "provenance.json"
    provenance: dict[str, Any] = (
        json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.exists() else {}
    )
    layers_json_path = src / "layers.json"
    layers_meta: dict[str, Any] = (
        json.loads(layers_json_path.read_text(encoding="utf-8")) if layers_json_path.exists() else {}
    )

    resolved_case_id = case_id or src.name.replace("_", "-").lower()
    req_meta = provenance.get("request") or {}
    before_win = req_meta.get("before")
    after_win = req_meta.get("after")
    if isinstance(before_win, list):
        before_win = "/".join(before_win)
    if isinstance(after_win, list):
        after_win = "/".join(after_win)

    resolved_title = title or f"GeoPulse Wildfire: {resolved_case_id}"
    resolved_desc = description or (
        f"GeoPulse data converted without altering its original visual appearance ({src.name})."
    )

    resolved_download_dir = download_dir or settings.resolved_download_dir()
    resolved_cases_dir = cases_dir or settings.resolved_cases_dir()
    event_dl_dir = resolved_download_dir / resolved_case_id
    event_dl_dir.mkdir(parents=True, exist_ok=True)

    # Reproject / convert GeoTIFFs to PIL/sar-platform-compatible uncompressed EPSG:4326 TIFFs
    def _to_wgs84(raster: GeoRasterData, *, nearest: bool = False) -> np.ndarray:
        if (
            raster.crs_epsg == 4326
            and raster.data.shape == (grid.height, grid.width)
        ):
            return raster.data
        return reproject_raster_to_wgs84_grid(raster, grid, nearest=nearest)

    staged_tifs: list[Path] = []
    prob_arr = _to_wgs84(prob_raster).astype(np.float32)
    staged_tifs.append(
        write_sar_compatible_geotiff(
            event_dl_dir / f"{resolved_case_id}_burn_probability.tif", prob_arr, bounds
        )
    )

    sev_arr: np.ndarray | None = None
    if (src / "burn_severity.tif").exists():
        sev_raster = read_geopulse_geotiff(src / "burn_severity.tif")
        sev_arr = _to_wgs84(sev_raster, nearest=True).astype(np.uint8)
        staged_tifs.append(
            write_sar_compatible_geotiff(
                event_dl_dir / f"{resolved_case_id}_burn_severity.tif",
                sev_arr,
                bounds,
                nodata=255.0,
            )
        )

    change_arr: np.ndarray | None = None
    if (src / "change_probability.tif").exists():
        chg_raster = read_geopulse_geotiff(src / "change_probability.tif")
        change_arr = _to_wgs84(chg_raster).astype(np.float32)
        staged_tifs.append(
            write_sar_compatible_geotiff(
                event_dl_dir / f"{resolved_case_id}_change_probability.tif", change_arr, bounds
            )
        )

    unc_arr: np.ndarray | None = None
    if (src / "uncertainty.tif").exists():
        unc_raster = read_geopulse_geotiff(src / "uncertainty.tif")
        unc_arr = _to_wgs84(unc_raster).astype(np.float32)
        staged_tifs.append(
            write_sar_compatible_geotiff(
                event_dl_dir / f"{resolved_case_id}_uncertainty.tif", unc_arr, bounds
            )
        )

    pre_start, _ = _window_to_utc(before_win)
    _, post_end = _window_to_utc(after_win)
    staging_report = stage_local_geotiffs_catalog(
        staged_tifs,
        resolved_download_dir,
        source="geopulse-local-convert",
        event_id=resolved_case_id,
        start_time_utc=pre_start,
        end_time_utc=post_end,
        merge_existing=True,
    )

    # Preserve exact GeoPulse PNG visual layers if already present under `<src>/layers/`
    rgba_maps: dict[str, np.ndarray] = {}
    layer_name_map = {
        "target": "burn_probability",
        "severity": "burn_severity",
        "change": "change_probability",
        "uncertainty": "uncertainty",
        "s2_pre": "s2_pre",
        "s2_post": "s2_post",
        "s1_pre": "s1_pre",
        "s1_post": "s1_post",
    }
    for gp_name, sar_name in layer_name_map.items():
        png_path = src / "layers" / f"{gp_name}.png"
        if png_path.exists():
            with Image.open(png_path) as im:
                rgba_maps[sar_name] = np.array(im.convert("RGBA"), dtype=np.uint8)

    if "burn_probability" not in rgba_maps:
        rgba_maps["burn_probability"] = ramp_rgba(prob_arr, PROB_BURN_PALETTE)
    if "burn_severity" not in rgba_maps and sev_arr is not None:
        rgba_maps["burn_severity"] = render_severity_rgba(sev_arr)
    if "change_probability" not in rgba_maps and change_arr is not None:
        rgba_maps["change_probability"] = ramp_rgba(change_arr, CHANGE_PALETTE)
    if "uncertainty" not in rgba_maps and unc_arr is not None:
        rgba_maps["uncertainty"] = ramp_rgba(unc_arr, UNCERTAINTY_PALETTE)

    extent_file = src / str(layers_meta.get("extent") or "burn_extent.geojson")
    if extent_file.exists():
        burn_extent = json.loads(extent_file.read_text(encoding="utf-8"))
        for feat in burn_extent.get("features") or []:
            props = feat.setdefault("properties", {})
            props.setdefault("step", 1)
    else:
        burn_extent = extract_burn_extent_geojson(prob_arr, bounds, step=1)

    aoi_file = src / "aoi.geojson"
    if aoi_file.exists():
        raw_aoi = json.loads(aoi_file.read_text(encoding="utf-8"))
        if raw_aoi.get("type") == "FeatureCollection":
            aoi_geojson = raw_aoi
        elif raw_aoi.get("type") == "Feature":
            aoi_geojson = {"type": "FeatureCollection", "features": [raw_aoi]}
        else:
            aoi_geojson = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"name": resolved_title}, "geometry": raw_aoi}],
            }
    else:
        aoi_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": resolved_title},
                    "geometry": bbox.to_geojson_polygon(),
                }
            ],
        }

    exported = export_wildfire_case(
        case_id=resolved_case_id,
        title=resolved_title,
        description=resolved_desc,
        bbox=bbox,
        before_window=before_win,
        after_window=after_win,
        layer_rgba_maps=rgba_maps,
        aoi_geojson=aoi_geojson,
        burn_extent_geojson=burn_extent,
        summary=summary,
        provenance=provenance,
        cases_dir=resolved_cases_dir,
        min_zoom=settings.min_zoom,
        max_zoom=settings.max_zoom,
    )

    pub_report: PlatformPublishReport | None = None
    if publish:
        pub_report = publish_cases_to_sar_platform(
            resolved_cases_dir,
            settings.resolved_sar_platform_cases_dir(),
            source_downloads_dir=resolved_download_dir,
            target_downloads_dir=settings.resolved_sar_platform_downloads_dir(),
            case_ids=[resolved_case_id],
        )

    return Stage1WildfireReport(
        case_id=resolved_case_id,
        title=resolved_title,
        bbox=bbox,
        staging_report=staging_report,
        exported_case=exported,
        publish_report=pub_report,
        summary=summary,
        dry_run=False,
    )
