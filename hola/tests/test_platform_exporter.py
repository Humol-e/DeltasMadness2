"""Unit tests for the real glacier melt exporter to sar-Madness."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from glacier_monitor.config import BoundingBox
from glacier_monitor.ingestion.platform_exporter import (
    build_glacier_case_definition,
    compute_grid_spec,
    compute_melt_loss_array,
    encode_gray8_png,
    encode_s2_rgb_png,
    extract_melt_extent_geojson,
    register_case_in_catalog_v2,
)


def test_compute_grid_spec_preserves_aspect_ratio() -> None:
    bbox = BoundingBox(min_lon=85.50, min_lat=28.18, max_lon=85.74, max_lat=28.30)
    spec = compute_grid_spec(bbox, max_dim=400)
    assert spec["width"] == 400
    assert spec["height"] == 200
    assert spec["bounds"] == [85.50, 28.18, 85.74, 28.30]
    assert spec["grid"]["crsCode"] == "EPSG:4326"


def test_encode_gray8_png_uses_fixed_physical_range() -> None:
    arr = np.array([[0.0, 50.0], [100.0, np.nan]], dtype=np.float32)
    raw = encode_gray8_png(arr, 0.0, 100.0)
    img = Image.open(io.BytesIO(raw))
    assert img.mode == "L"
    px = np.array(img)
    assert px[0, 0] == 0
    assert px[0, 1] in (127, 128)
    assert px[1, 0] == 255


def test_encode_s2_rgb_png_transparent_on_nan() -> None:
    r = np.array([[0.01, 0.90], [0.45, np.nan]], dtype=np.float32)
    g = np.array([[0.01, 0.90], [0.45, 0.2]], dtype=np.float32)
    b = np.array([[0.01, 0.90], [0.45, 0.2]], dtype=np.float32)
    raw = encode_s2_rgb_png(r, g, b)
    img = Image.open(io.BytesIO(raw))
    assert img.mode == "RGBA"
    px = np.array(img)
    assert px[0, 0, 0] == 0
    assert px[0, 1, 0] == 255
    assert px[0, 0, 3] == 255
    assert px[1, 1, 3] == 0


def test_extract_melt_extent_geojson_generates_valid_polygon() -> None:
    bbox = BoundingBox(min_lon=85.50, min_lat=28.18, max_lon=85.74, max_lat=28.30)
    pre = np.full((60, 120), 0.1, dtype=np.float32)
    post = np.full((60, 120), 0.1, dtype=np.float32)
    pre[15:45, 30:90] = 0.75
    post[15:45, 30:90] = 0.20

    loss = compute_melt_loss_array(pre, post)
    assert np.nanmax(loss) > 50.0

    geo, area_km2 = extract_melt_extent_geojson(pre, post, bbox)
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 1
    assert area_km2 > 1.0


def test_register_catalog_strips_demo_and_deduplicates(tmp_path: Path) -> None:
    cat = tmp_path / "catalog.json"
    cat.write_text(
        json.dumps({"schemaVersion": 2, "cases": ["data/cases/langtang-demo/case.json"]}),
        encoding="utf-8",
    )
    register_case_in_catalog_v2(cat, "data/cases/glacier-langtang/case.json")
    register_case_in_catalog_v2(cat, "data/cases/glacier-langtang/case.json")
    data = json.loads(cat.read_text(encoding="utf-8"))
    assert data["cases"] == ["data/cases/glacier-langtang/case.json"]


def test_build_glacier_case_definition_is_strictly_real() -> None:
    bbox = BoundingBox(min_lon=85.50, min_lat=28.18, max_lon=85.74, max_lat=28.30)
    case_def = build_glacier_case_definition(
        case_id="glacier-langtang",
        name="Langtang",
        region="Nepal",
        summary="Real",
        bbox=bbox,
        zoom=11.6,
        pre_window=("2026-01-01", "2026-02-28"),
        post_window=("2026-05-15", "2026-08-31"),
        stats={
            "s2_pre_count": 26,
            "s2_post_count": 29,
            "s1_pre_count": 14,
            "s1_post_count": 24,
            "modis_pre_count": 58,
            "modis_post_count": 91,
            "snow_pre_pct": 38.9,
            "snow_post_pct": 10.6,
            "lst_pre_c": -6.9,
            "lst_post_c": 6.4,
            "melt_area_km2": 18.4,
        },
        downloads=[],
    )
    assert case_def["status"] == "real"
    assert case_def["simulated"] is False
    assert case_def["category"] == "glacier"
    assert case_def["title"] == "Langtang"
    assert len(case_def["view"]["center"]) == 2
    assert len(case_def["datasets"]) == 5
    for ds in case_def["datasets"]:
        assert len(ds["extent"]) == 4
        if ds["source"]["encoding"] == "gray8":
            assert ds["source"]["nodata"] is None
            for var in ds["variables"]:
                assert var["legend"]["min"] < var["legend"]["max"]
                assert len(var["legend"]["colors"]) >= 2
    for ds in case_def["datasets"][:4]:
        assert len(ds["frames"]) == 2
