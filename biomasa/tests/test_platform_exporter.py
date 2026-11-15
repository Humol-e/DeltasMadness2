"""Unit tests for reprojection, GeoTIFF, GeoJSON, and Schema v2 export to sar-Madness."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from nisar_biomass_monitor.config import LENO_SITE, load_nisar_config
from nisar_biomass_monitor.geo_utils import (
    albers_forward,
    lonlat_to_utm,
    utm_to_lonlat,
    write_wgs84_geotiff,
)
from nisar_biomass_monitor.nisar_atbd import forward_nisar_hh_hv, invert_agb_scene
from nisar_biomass_monitor.platform_exporter import (
    encode_gray8_png,
    export_biomass_case_to_madness,
    extract_mask_polygons_geojson,
    register_case_in_catalog_v2,
)
from nisar_biomass_monitor.s3_source import LenoReferenceLayers, ResampledGcovScene


def test_utm_lonlat_roundtrip_and_albers_leno() -> None:
    e_in = np.array([378225.0, 399225.0])
    n_in = np.array([3568056.0, 3548756.0])
    lon, lat = utm_to_lonlat(e_in, n_in, 32616)
    e_out, n_out = lonlat_to_utm(lon, lat, 32616)
    np.testing.assert_allclose(e_out, e_in, atol=1e-3)
    np.testing.assert_allclose(n_out, n_in, atol=1e-3)

    ax, ay = albers_forward(lon, lat)
    assert 720000.0 < ax.min() < ax.max() < 750000.0
    assert 1020000.0 < ay.min() < ay.max() < 1060000.0


def test_encode_gray8_png_and_geotiff_tags(tmp_path: Path) -> None:
    arr = np.array([[np.nan, 0.0], [110.0, 220.0]], dtype=np.float32)
    g8 = encode_gray8_png(arr, vmin=0.0, vmax=220.0)
    assert g8[0, 0] == 0
    assert g8[0, 1] == 1
    assert g8[1, 1] == 255

    tif = write_wgs84_geotiff(tmp_path / "test_agb.tif", arr, (-88.3, 32.0, -88.0, 32.3))
    with Image.open(tif) as img:
        assert img.tag_v2[34735][-1] == 4326
        assert float(img.tag_v2[42113]) == -9999.0
        loaded = np.array(img)
        assert loaded[0, 0] == -9999.0
        assert loaded[1, 1] == 220.0


def test_extract_mask_polygons_and_export_schema_v2(tmp_path: Path) -> None:
    bounds_wgs = (-88.30, 32.06, -88.06, 32.25)
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:25, 10:25] = 1
    gj = extract_mask_polygons_geojson(mask, bounds_wgs, min_area_km2=0.5)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 1
    assert gj["features"][0]["properties"]["area_km2"] > 1.0

    # Full export test on a synthetic LENO-like ROI grid (mini-site)
    mini_site = LENO_SITE.model_copy(
        update={
            "roi_width_100m": 20,
            "roi_height_100m": 20,
            "roi_bounds_utm": (378225.0, 3566056.0, 380225.0, 3568056.0),
        }
    )
    params = load_nisar_config()
    agb_pre = np.full((20, 20), 110.0, dtype=np.float64)
    agb_post = np.full((20, 20), 108.0, dtype=np.float64)
    agb_post[5:12, 5:12] = 20.0

    hh_pre, hv_pre = forward_nisar_hh_hv(agb_pre, params)
    hh_post, hv_post = forward_nisar_hh_hv(agb_post, params)

    forest_mask = np.ones((20, 20), dtype=bool)
    pre_inv = invert_agb_scene(hh_pre, hv_pre, params, valid_mask=forest_mask)
    post_inv = invert_agb_scene(hh_post, hv_post, params, valid_mask=forest_mask)

    nlcd = np.full((20, 20), 42, dtype=np.uint8)
    cls_100 = np.ones((20, 20), dtype=np.uint8)
    planet_dist = np.zeros((20, 20), dtype=np.uint8)
    planet_dist[5:12, 5:12] = 1
    refs = LenoReferenceLayers(nlcd, forest_mask, cls_100, planet_dist)

    pre_scene = ResampledGcovScene(
        "20220930", "2022-09-30T05:47:58Z", hh_pre.astype(np.float32), hv_pre.astype(np.float32),
        np.ones((20, 20), dtype=np.float32), 32616, tmp_path / "pre.h5", "https://example.org/pre.h5",
    )
    post_scene = ResampledGcovScene(
        "20230929", "2023-09-29T05:47:58Z", hh_post.astype(np.float32), hv_post.astype(np.float32),
        np.ones((20, 20), dtype=np.float32), 32616, tmp_path / "post.h5", "https://example.org/post.h5",
    )

    madness_root = tmp_path / "sar-Madness"
    cat = madness_root / "public" / "data" / "catalog.json"
    register_case_in_catalog_v2(cat, "data/cases/wildfire-camp-paradise/case.json")

    summary = export_biomass_case_to_madness(
        site=mini_site,
        params=params,
        pre_scenes=[pre_scene],
        post_scenes=[post_scene],
        pre_inv=pre_inv,
        post_inv=post_inv,
        refs=refs,
        madness_root=madness_root,
    )

    case_doc = json.loads(summary.case_json_path.read_text(encoding="utf-8"))
    assert case_doc["schemaVersion"] == 2
    assert case_doc["status"] == "real"
    assert case_doc["simulated"] is False
    assert case_doc["category"] == "biomass"
    assert len(case_doc["view"]["center"]) == 2
    assert all(isinstance(s, str) for s in case_doc["sources"])
    assert len(case_doc["datasets"]) == 5
    for ds in case_doc["datasets"]:
        var_ids = {v["id"] for v in ds["variables"]}
        for fr in ds["frames"]:
            assert set(fr["files"].keys()) == var_ids
    assert len(case_doc["vectors"]) == 3
    assert len(case_doc["downloads"]) == 8
    assert case_doc["metrics"]["biomassLossKm2"] > 0.3

    catalog_doc = json.loads(summary.catalog_path.read_text(encoding="utf-8"))
    assert catalog_doc["cases"] == [
        "data/cases/wildfire-camp-paradise/case.json",
        f"data/cases/{mini_site.case_id}/case.json",
    ]


def test_export_multitemporal_series_produces_four_chronological_frames(tmp_path: Path) -> None:
    mini_site = LENO_SITE.model_copy(
        update={
            "roi_width_100m": 16,
            "roi_height_100m": 16,
            "roi_bounds_utm": (378225.0, 3566456.0, 379825.0, 3568056.0),
        }
    )
    params = load_nisar_config()
    forest_mask = np.ones((16, 16), dtype=bool)
    refs = LenoReferenceLayers(
        np.full((16, 16), 42, dtype=np.uint8),
        forest_mask,
        np.ones((16, 16), dtype=np.uint8),
        np.zeros((16, 16), dtype=np.uint8),
    )

    series = []
    for tag, iso, agb_val in [
        ("20210806", "2021-08-06T05:47:00Z", 118.0),
        ("20220930", "2022-09-30T05:47:00Z", 112.0),
        ("20230929", "2023-09-29T05:47:00Z", 104.0),
        ("20240830", "2024-08-30T05:47:00Z", 109.0),
    ]:
        agb = np.full((16, 16), agb_val, dtype=np.float64)
        hh, hv = forward_nisar_hh_hv(agb, params)
        sc = ResampledGcovScene(
            tag, iso, hh.astype(np.float32), hv.astype(np.float32),
            np.ones((16, 16), dtype=np.float32), 32616, tmp_path / f"{tag}.h5", f"https://example.org/{tag}.h5",
        )
        inv = invert_agb_scene(hh, hv, params, valid_mask=forest_mask)
        series.append((sc, inv))

    summary = export_biomass_case_to_madness(
        site=mini_site,
        params=params,
        pre_scenes=[series[1][0]],
        post_scenes=[series[2][0]],
        pre_inv=series[1][1],
        post_inv=series[2][1],
        refs=refs,
        madness_root=tmp_path / "mad",
        series_scenes=series,
    )

    case_doc = json.loads(summary.case_json_path.read_text(encoding="utf-8"))
    for ds_id in ("nisar_agb", "nisar_hv", "nisar_hh"):
        ds = next(d for d in case_doc["datasets"] if d["id"] == ds_id)
        assert [f["id"] for f in ds["frames"]] == ["d20210806", "pre", "post", "d20240830"]
        assert ds["frames"][1]["phase"] == "before"
        assert ds["frames"][2]["phase"] == "after"
        for fr in ds["frames"]:
            rel = next(iter(fr["files"].values()))
            assert (tmp_path / "mad" / "public" / rel).is_file()

    assert case_doc["metrics"]["seriesSceneCount"] == 4
    assert case_doc["metrics"]["agb2021Mean"] == 118.0
    assert case_doc["metrics"]["agb2024Mean"] == 109.0
    assert len(case_doc["metrics"]["agbSeriesMeans"]) == 4
