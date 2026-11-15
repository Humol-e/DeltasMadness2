"""Tests for exact GeoPulse color rendering, XYZ PNG slicing, and sar-platform case export."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from wildfire_monitor.config import BoundingBox
from wildfire_monitor.ingestion import platform_exporter as pe


def test_geopulse_palettes_match_exact_rgba_colors() -> None:
    # Burn probability: <0.2 transparent, 0.5 -> (235, 70, 30, 150), 1.0 -> (200, 20, 10, 235)
    vals = np.array([[0.1, 0.5, 1.0, np.nan]], dtype=np.float32)
    rgba = pe.ramp_rgba(vals, pe.PROB_BURN_PALETTE)
    assert tuple(rgba[0, 0]) == (0, 0, 0, 0)
    assert tuple(rgba[0, 1]) == (235, 70, 30, 150)
    assert tuple(rgba[0, 2]) == (200, 20, 10, 235)
    assert tuple(rgba[0, 3]) == (0, 0, 0, 0)

    # Burn severity Key & Benson discrete colors
    sev = np.array([[0, 1, 2, 3, 4, 255]], dtype=np.uint8)
    sev_rgba = pe.render_severity_rgba(sev)
    assert tuple(sev_rgba[0, 0]) == (0, 0, 0, 0)
    assert tuple(sev_rgba[0, 1]) == (127, 255, 212, 220)
    assert tuple(sev_rgba[0, 2]) == (255, 255, 0, 220)
    assert tuple(sev_rgba[0, 3]) == (255, 140, 0, 230)
    assert tuple(sev_rgba[0, 4]) == (255, 0, 0, 235)
    assert tuple(sev_rgba[0, 5]) == (0, 0, 0, 0)


def test_render_s2_and_s1_imagery_alpha_and_scaling() -> None:
    s2 = np.zeros((6, 2, 2), dtype=np.float32)
    s2[2, 0, 0] = 0.30  # B04 saturated R
    s2[1, 0, 0] = 0.0
    s2[0, 0, 0] = 0.0
    s2[:, 1, 1] = np.nan
    s2_rgba = pe.render_s2_rgb_rgba(s2)
    assert tuple(s2_rgba[0, 0]) == (255, 0, 0, 255)
    assert tuple(s2_rgba[1, 1]) == (0, 0, 0, 0)

    s1 = np.array([[[0.0, -25.0]], [[-10.0, -30.0]]], dtype=np.float32)
    s1_rgba = pe.render_s1_gray_rgba(s1)
    assert tuple(s1_rgba[0, 0]) == (255, 255, 255, 255)
    assert tuple(s1_rgba[0, 1]) == (0, 0, 0, 0)


def test_export_wildfire_case_and_xyz_tiles_compatible_with_sar_platform(tmp_path: Path) -> None:
    bbox = BoundingBox(min_lon=-118.62, min_lat=34.02, max_lon=-118.46, max_lat=34.12)
    rgba = np.zeros((32, 48, 4), dtype=np.uint8)
    rgba[4:28, 8:40] = (235, 70, 30, 180)
    sev_rgba = np.zeros((32, 48, 4), dtype=np.uint8)
    sev_rgba[4:28, 8:40] = (255, 0, 0, 235)

    exported = pe.export_wildfire_case(
        case_id="wildfire-palisades-la",
        title="Palisades Fire — Los Angeles, CA (2025)",
        description="Incendio real GeoPulse.",
        bbox=bbox,
        before_window="2024-12-01/2025-01-06",
        after_window="2025-02-01/2025-03-31",
        layer_rgba_maps={"burn_probability": rgba, "burn_severity": sev_rgba},
        aoi_geojson={"type": "FeatureCollection", "features": []},
        burn_extent_geojson={"type": "FeatureCollection", "features": []},
        summary={"affected_km2": 12.5, "observed_km2": 100.0, "review_km2": 2.1},
        provenance={"model": "threshold-baseline-v0.1.0"},
        cases_dir=tmp_path / "cases",
        min_zoom=10,
        max_zoom=11,
    )

    assert exported.tile_count >= 2
    assert exported.case_json_path.exists()
    case_doc = json.loads(exported.case_json_path.read_text(encoding="utf-8"))
    # Replicate exact sar-platform validateCase contract checks:
    assert case_doc["id"] == "wildfire-palisades-la"
    assert isinstance(case_doc["view"]["center"], list) and len(case_doc["view"]["center"]) == 2
    assert len(case_doc["timeline"]) == 3
    layer_ids = {l["id"]: l for l in case_doc["layers"]}
    assert {"aoi", "burn_probability", "burn_severity", "burn-extent-casing", "burn-extent-outline"} <= set(layer_ids)
    # Check GeoPulse exact yellow perimeter styling
    assert layer_ids["burn-extent-outline"]["paint"]["line-color"] == "#ffd60a"

    index_doc = json.loads((tmp_path / "cases" / "index.json").read_text(encoding="utf-8"))
    assert index_doc["cases"] == ["data/cases/wildfire-palisades-la/case.json"]

    # Check a generated PNG tile is valid RGBA 256x256
    pngs = list((exported.case_dir / "tiles" / "burn_probability").rglob("*.png"))
    assert len(pngs) >= 1
    with Image.open(pngs[0]) as tile_img:
        assert tile_img.size == (256, 256)
        assert tile_img.mode == "RGBA"


def test_publish_cases_preserves_existing_platform_index_entries(tmp_path: Path) -> None:
    src_cases = tmp_path / "src_cases"
    (src_cases / "wildfire-martin-nv").mkdir(parents=True)
    (src_cases / "wildfire-martin-nv" / "case.json").write_text(
        json.dumps({"id": "wildfire-martin-nv"}), encoding="utf-8"
    )

    target_cases = tmp_path / "platform_cases"
    target_cases.mkdir(parents=True)
    (target_cases / "index.json").write_text(
        json.dumps({"cases": ["data/cases/wildfire-palisades-la/case.json"]}), encoding="utf-8"
    )

    rep = pe.publish_cases_to_sar_platform(src_cases, target_cases)
    assert rep.published_case_ids == ("wildfire-martin-nv",)
    idx = json.loads((target_cases / "index.json").read_text(encoding="utf-8"))
    assert idx["cases"] == [
        "data/cases/wildfire-palisades-la/case.json",
        "data/cases/wildfire-martin-nv/case.json",
    ]


def test_extract_burn_extent_preserves_geometry_collection_polygons() -> None:
    from wildfire_monitor.ingestion.geopulse_bridge import extract_burn_extent_geojson

    prob = np.zeros((48, 48), dtype=np.float32)
    prob[6:42, 6:42] = 0.95
    prob[16:18, 16:18] = 0.1
    fc = extract_burn_extent_geojson(prob, (-118.62, 34.03, -118.50, 34.12), min_area_km2=0.01)
    assert len(fc["features"]) >= 1
    assert sum(f["properties"]["area_km2"] for f in fc["features"]) > 40.0


def test_publish_cases_to_sar_madness_v2_creates_valid_schema_v2(tmp_path: Path) -> None:
    from wildfire_monitor.ingestion.geopulse_bridge import write_sar_compatible_geotiff

    cid = "wildfire-test-fire"
    bounds = (-118.62, 34.03, -118.50, 34.12)
    src_cases = tmp_path / "cases"
    src_dl = tmp_path / "downloads"
    (src_cases / cid / "layers").mkdir(parents=True)
    (src_dl / cid).mkdir(parents=True)

    h, w = 24, 32
    bp = np.full((h, w), 0.9, dtype=np.float32)
    bs = np.full((h, w), 3, dtype=np.uint8)
    cp = np.full((h, w), 0.8, dtype=np.float32)
    unc = np.full((h, w), 0.2, dtype=np.float32)
    nbr_pre = np.full((h, w), 0.4, dtype=np.float32)
    nbr_post = np.full((h, w), -0.2, dtype=np.float32)
    vh_pre = np.full((h, w), -14.0, dtype=np.float32)
    vh_post = np.full((h, w), -17.0, dtype=np.float32)
    dem = np.linspace(50.0, 500.0, h * w, dtype=np.float32).reshape(h, w)

    for suffix, arr, nd in [
        ("burn_probability", bp, -9999.0),
        ("burn_severity", bs, 255.0),
        ("change_probability", cp, -9999.0),
        ("uncertainty", unc, -9999.0),
        ("s2_pre_nbr", nbr_pre, -9999.0),
        ("s2_post_nbr", nbr_post, -9999.0),
        ("s1_pre_vh", vh_pre, -9999.0),
        ("s1_post_vh", vh_post, -9999.0),
        ("dem_elevation", dem, -9999.0),
    ]:
        write_sar_compatible_geotiff(src_dl / cid / f"{cid}_{suffix}.tif", arr, bounds, nodata=nd)

    for rgb in ("s2_pre.png", "s2_post.png"):
        Image.fromarray(np.full((h, w, 4), 180, dtype=np.uint8), mode="RGBA").save(src_cases / cid / "layers" / rgb)
    (src_cases / cid / "aoi.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (src_cases / cid / "summary.json").write_text(json.dumps({"affected_km2": 10.0, "observed_km2": 50.0, "review_km2": 1.0}))
    (src_cases / cid / "provenance.json").write_text(json.dumps({"model": "threshold-baseline-v0.1.0", "before": "2025-01-01/2025-01-05", "after": "2025-02-01/2025-02-10"}))
    (src_cases / cid / "layers.json").write_text(json.dumps({"bounds": list(bounds)}))
    (src_cases / cid / "case.json").write_text(json.dumps({"id": cid, "title": "Test Fire", "description": "Test", "view": {"center": [-118.56, 34.075], "zoom": 11}}))

    target_data = tmp_path / "public_data"
    published = pe.publish_cases_to_sar_madness_v2(src_cases, src_dl, target_data)
    assert published == [cid]
    cat = json.loads((target_data / "catalog.json").read_text(encoding="utf-8"))
    assert cat["schemaVersion"] == 2
    assert f"data/cases/{cid}/case.json" in cat["cases"]
    c2 = json.loads((target_data / "cases" / cid / "case.json").read_text(encoding="utf-8"))
    assert c2["schemaVersion"] == 2
    assert c2["status"] == "real"
    assert c2["wildfire"]["kind"] == "geopulse-real"
    assert "suggestedIgnitions" not in c2["wildfire"]
    assert c2["simulated"] is False
    assert c2["category"] == "incendio"
    assert all(ds["id"] != "dem" for ds in c2["datasets"])
    assert len(c2["downloads"]) == 9
    assert all((target_data / dl["url"].removeprefix("data/")).exists() for dl in c2["downloads"])
    assert "simDefaults" not in c2["wildfire"]
    assert (target_data / "cases" / cid / "rasters" / "burn_prob.png").exists()
