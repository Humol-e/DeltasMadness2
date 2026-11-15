"""Tests for wildfire configuration, GeoPulse example resolution, and settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wildfire_monitor.config import (
    AOI_PRESETS,
    BoundingBox,
    Settings,
    bbox_from_geojson,
    resolve_aoi,
    resolve_event,
)


def test_bbox_bounds_center_and_geojson() -> None:
    bbox = BoundingBox(min_lon=-118.62, min_lat=34.02, max_lon=-118.46, max_lat=34.12)
    assert bbox.to_ee_bounds() == [-118.62, 34.02, -118.46, 34.12]
    assert bbox.center() == (-118.54, 34.07)
    assert 140.0 < bbox.area_km2() < 190.0
    poly = bbox.to_geojson_polygon()
    assert poly["type"] == "Polygon"
    assert bbox_from_geojson(poly) == bbox


def test_bbox_rejects_inverted_coordinates() -> None:
    with pytest.raises(ValueError):
        BoundingBox(min_lon=-118.0, min_lat=34.0, max_lon=-118.5, max_lat=34.1)
    with pytest.raises(ValueError):
        BoundingBox(min_lon=-118.5, min_lat=34.5, max_lon=-118.0, max_lat=34.0)


def test_resolve_aoi_presets_geojson_and_custom(tmp_path: Path) -> None:
    name, palisades = resolve_aoi("palisades")
    assert "Palisades" in name
    assert palisades == AOI_PRESETS["palisades"].bbox

    evt = resolve_event("WILDFIRE_CAMP_PARADISE")
    assert evt is not None
    assert evt.case_id == "wildfire-camp-paradise"

    gj_path = tmp_path / "custom.geojson"
    gj_path.write_text(json.dumps(AOI_PRESETS["martin_nv"].bbox.to_geojson_polygon()), encoding="utf-8")
    _, from_gj = resolve_aoi(str(gj_path))
    assert from_gj == AOI_PRESETS["martin_nv"].bbox

    custom_name, custom_bbox = resolve_aoi("-120.5, 38.0, -120.2, 38.3")
    assert "personalizado" in custom_name
    assert custom_bbox.to_ee_bounds() == [-120.5, 38.0, -120.2, 38.3]


def test_resolve_aoi_invalid_format() -> None:
    with pytest.raises(ValueError):
        resolve_aoi("unknown_wildfire")
    with pytest.raises(ValueError):
        resolve_aoi("-118.6,34.0,abc,34.1")


def test_settings_paths_and_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCAL_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("LOCAL_CASES_DIR", str(tmp_path / "cases"))
    settings = Settings(_env_file=None)
    assert settings.resolved_download_dir() == tmp_path / "dl"
    assert settings.resolved_cases_dir() == tmp_path / "cases"
    assert settings.max_cloud_cover == 60


def test_resolve_expanded_global_wildfire_presets() -> None:
    for alias, key in (
        ("maui-lahaina", "maui_lahaina"),
        ("jasper_ab", "jasper_ab"),
        ("valparaiso-cl", "valparaiso_cl"),
        ("evros_gr", "evros_gr"),
        ("tenerife-es", "tenerife_es"),
        ("tarahumara_mx", "tarahumara_mx"),
        ("pantanal-br", "pantanal_br"),
        ("los_alerces_ar", "los_alerces_ar"),
    ):
        name, bbox = resolve_aoi(alias)
        assert name == AOI_PRESETS[key].name
        assert bbox == AOI_PRESETS[key].bbox
        assert bbox.min_lon < bbox.max_lon and bbox.min_lat < bbox.max_lat
