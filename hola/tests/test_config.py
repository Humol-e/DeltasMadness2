"""Tests for configuration, settings, and glacier AOI helpers."""

from __future__ import annotations

import pytest

from glacier_monitor.config import AOI_PRESETS, BoundingBox, Settings, resolve_aoi


def test_bbox_wkt_and_ee_bounds() -> None:
    bbox = BoundingBox(min_lon=85.5, min_lat=28.18, max_lon=85.74, max_lat=28.30)
    assert bbox.to_ee_bounds() == [85.5, 28.18, 85.74, 28.3]
    assert bbox.to_wkt() == (
        "POLYGON((85.5 28.18, 85.74 28.18, 85.74 28.3, 85.5 28.3, 85.5 28.18))"
    )


def test_bbox_rejects_inverted_coordinates() -> None:
    with pytest.raises(ValueError):
        BoundingBox(min_lon=86.0, min_lat=28.0, max_lon=85.0, max_lat=28.5)
    with pytest.raises(ValueError):
        BoundingBox(min_lon=85.0, min_lat=28.5, max_lon=86.0, max_lat=28.0)


def test_resolve_aoi_presets_and_custom() -> None:
    name, langtang = resolve_aoi("langtang")
    assert "Langtang" in name
    assert langtang == AOI_PRESETS["langtang"].bbox

    _, khumbu = resolve_aoi("KHUMBU")
    assert khumbu == AOI_PRESETS["khumbu"].bbox

    custom_name, custom_bbox = resolve_aoi("85.1, 28.0, 85.4, 28.2")
    assert "personalizado" in custom_name
    assert custom_bbox.to_ee_bounds() == [85.1, 28.0, 85.4, 28.2]


def test_resolve_aoi_invalid_format() -> None:
    with pytest.raises(ValueError):
        resolve_aoi("unknown_glacier")
    with pytest.raises(ValueError):
        resolve_aoi("85.1,28.0,abc,28.2")


def test_settings_empty_strings_and_collection_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEE_PROJECT_ID", "glacier-proj")
    monkeypatch.setenv("EARTHDATA_TOKEN", "   ")
    monkeypatch.setenv("GEE_PRIVATE_KEY_PATH", "")
    monkeypatch.setenv("GEE_ASSET_COLLECTION", "")

    settings = Settings(_env_file=None)
    assert settings.earthdata_token is None
    assert settings.gee_private_key_path is None
    assert settings.resolved_asset_collection() == "projects/glacier-proj/assets/glacier_sar_asf"
