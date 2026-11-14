"""Tests for Stage 1A native Google Earth Engine catalog module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from glacier_monitor.config import AOI_PRESETS
from glacier_monitor.ingestion import native_catalog


def test_native_catalog_contains_expected_collections() -> None:
    assert native_catalog.NATIVE_CATALOG["sentinel1_grd"].dataset_id == "COPERNICUS/S1_GRD"
    assert native_catalog.NATIVE_CATALOG["modis_snow"].dataset_id == "MODIS/061/MOD10A1"
    assert native_catalog.NATIVE_CATALOG["modis_lst"].dataset_id == "MODIS/061/MOD11A1"


def test_build_native_collection_applies_s1_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_collection = MagicMock()
    fake_collection.filterBounds.return_value = fake_collection
    fake_collection.filterDate.return_value = fake_collection
    fake_collection.filter.return_value = fake_collection
    fake_collection.select.return_value = fake_collection

    mock_ee = MagicMock()
    mock_ee.ImageCollection.return_value = fake_collection
    monkeypatch.setattr(native_catalog, "ee", mock_ee)

    bbox = AOI_PRESETS["langtang"].bbox
    result = native_catalog.build_native_collection(
        "sentinel1_grd",
        bbox,
        "2026-09-01",
        "2026-10-01",
        select_default_bands=True,
        s1_instrument_mode="IW",
        s1_orbit_pass="descending",
        s1_polarization="vv",
    )

    assert result is fake_collection
    mock_ee.ImageCollection.assert_called_once_with("COPERNICUS/S1_GRD")
    mock_ee.Geometry.Rectangle.assert_called_once_with(bbox.to_ee_bounds())
    fake_collection.filterDate.assert_called_once_with("2026-09-01", "2026-10-01")
    mock_ee.Filter.eq.assert_any_call("instrumentMode", "IW")
    mock_ee.Filter.eq.assert_any_call("orbitProperties_pass", "DESCENDING")
    mock_ee.Filter.listContains.assert_called_once_with(
        "transmitterReceiverPolarisation", "VV"
    )
    fake_collection.select.assert_called_once_with(["VV", "VH"])


def test_inspect_collection_status_parses_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_collection = MagicMock()
    fake_collection.filterBounds.return_value = fake_collection
    fake_collection.filterDate.return_value = fake_collection
    fake_collection.sort.return_value = fake_collection

    fake_dict = MagicMock()
    # 2026-10-01T00:00:00Z = 1790812800000 ms, 2026-10-15T12:00:00Z = 1792065600000 ms
    fake_dict.getInfo.return_value = {
        "count": 12,
        "earliest": 1790812800000,
        "latest": 1792065600000,
        "bands": ["NDSI_Snow_Cover", "NDSI_Snow_Cover_Basic_QA"],
    }

    mock_ee = MagicMock()
    mock_ee.ImageCollection.return_value = fake_collection
    mock_ee.Dictionary.return_value = fake_dict
    monkeypatch.setattr(native_catalog, "ee", mock_ee)

    status = native_catalog.inspect_collection_status(
        "modis_snow",
        AOI_PRESETS["khumbu"].bbox,
        "2026-10-01",
        "2026-10-16",
    )

    assert status.dataset_id == "MODIS/061/MOD10A1"
    assert status.image_count == 12
    assert status.earliest_time_utc == "2026-10-01T00:00:00Z"
    assert status.latest_time_utc == "2026-10-15T12:00:00Z"
    assert status.available_bands == ("NDSI_Snow_Cover", "NDSI_Snow_Cover_Basic_QA")
