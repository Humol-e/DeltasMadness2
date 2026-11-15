"""Unit tests for all-weather SAR burn & OPERA DIST-S1 detection."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from wildfire_monitor.config import BoundingBox
from wildfire_monitor.ingestion.sar_burn import (
    CLASS_HIGH_BURN,
    CLASS_LOW_DIST,
    CLASS_MOD_BURN,
    CLASS_UNBURNED,
    DATASET_ID,
    DELTA_RVI_RANGE,
    LOG_RBR_RANGE_DB,
    OPERA_DIST_RANGE,
    VECTOR_ID,
    attach_sar_burn_to_case,
    classify_sar_burn,
    compute_glcm_contrast_change,
    compute_opera_dist_metric,
    compute_wishart_change_stat,
    decode_gray8_values,
    encode_class_png,
    encode_continuous_png,
    majority_filter,
    publish_sar_burn_for_case,
    sar_burn_extent_geojson,
    write_sar_burn_assets,
)

BBOX = BoundingBox(min_lon=-121.78, min_lat=39.68, max_lon=-121.63, max_lat=39.79)
SHAPE = (48, 48)


def _png_array(raw: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(raw)))


def _synthetic_scene(*, seed: int = 7) -> dict[str, np.ndarray]:
    """Build a realistic forest scene with an unburned core and a severe canopy burn block."""
    rng = np.random.default_rng(seed)
    co_pre = np.full(SHAPE, -9.0, dtype=np.float32) + rng.normal(0.0, 0.25, SHAPE).astype(np.float32)
    cr_pre = np.full(SHAPE, -15.0, dtype=np.float32) + rng.normal(0.0, 0.25, SHAPE).astype(np.float32)
    co_post = co_pre.copy()
    cr_post = cr_pre.copy()

    # Moderate burn band
    cr_post[26:38, 8:20] -= 2.5
    co_post[26:38, 8:20] -= 0.5
    # High-severity canopy burn patch with strong texture + depolarization drop
    cr_post[8:22, 26:40] -= 5.5
    co_post[8:22, 26:40] -= 1.2
    cr_post[8:22, 26:40] += rng.normal(0.0, 1.8, (14, 14)).astype(np.float32)
    return {
        "co_pre_db": co_pre,
        "co_post_db": co_post,
        "cross_pre_db": cr_pre,
        "cross_post_db": cr_post,
    }


def test_polarimetric_indices_glcm_and_wishart_statistics() -> None:
    s = _synthetic_scene()
    res = classify_sar_burn(**s, pol_mode="VV_VH")

    assert res.rbd_db[15, 33] < -4.0
    assert res.log_rbr_db[15, 33] < -3.0
    assert res.delta_rvi[15, 33] < -0.15
    assert res.delta_dpsvi[15, 33] < -0.05
    assert res.delta_rfdi[15, 33] > 0.05

    # GLCM contrast jumps inside textured burn scar relative to smooth unburned forest
    assert abs(float(np.nanmean(res.delta_glcm_contrast[10:20, 28:38]))) > abs(
        float(np.nanmean(res.delta_glcm_contrast[38:46, 38:46]))
    )

    # Wishart -2 ln Q must be >= 0 everywhere finite and high on changed burn scar
    finite_w = res.wishart_stat[np.isfinite(res.wishart_stat)]
    assert np.all(finite_w >= 0.0)
    assert float(np.nanmean(res.wishart_stat[10:20, 28:38])) > float(
        np.nanmean(res.wishart_stat[38:46, 38:46])
    )


def test_opera_dist_metric_immune_to_uniform_soil_moisture_increase() -> None:
    s = _synthetic_scene()
    base = classify_sar_burn(**s, pol_mode="VV_VH")

    # Apply a uniform +3.5 dB post-event rain / soil-moisture gain to BOTH VV and VH
    moist = {
        "co_pre_db": s["co_pre_db"],
        "co_post_db": s["co_post_db"] + 3.5,
        "cross_pre_db": s["cross_pre_db"],
        "cross_post_db": s["cross_post_db"] + 3.5,
    }
    wet = classify_sar_burn(**moist, pol_mode="VV_VH")

    assert np.allclose(wet.log_rbr_db, base.log_rbr_db, atol=1e-4, equal_nan=True)
    assert np.nanmean(wet.opera_dist[10:20, 28:38]) > 1.5
    assert float(np.nanmean(wet.opera_dist[10:20, 28:38])) == pytest.approx(
        float(np.nanmean(base.opera_dist[10:20, 28:38])), abs=0.15
    )


def test_supports_both_vv_vh_and_hh_hv_nisar_modes_and_masks_water() -> None:
    s = _synthetic_scene()
    # Water body below co/cross noise floors must be excluded from domain
    s["co_pre_db"][0:6, 0:6] = -22.0
    s["cross_pre_db"][0:6, 0:6] = -27.0

    c_band = classify_sar_burn(**s, pol_mode="VV_VH")
    l_band = classify_sar_burn(**s, pol_mode="HH_HV")

    assert l_band.pol_mode == "HH_HV"
    assert (c_band.classes[0:6, 0:6] == CLASS_UNBURNED).all()
    assert np.isnan(c_band.opera_dist[2, 2])
    assert np.array_equal(c_band.classes, l_band.classes)

    # K-means class ordering: burned patch must land in burn clusters (mod/high), core unburned in 0/1
    assert float(np.mean(c_band.classes[10:20, 28:38])) >= float(CLASS_MOD_BURN)
    assert int(np.max(c_band.classes[10:20, 28:38])) == CLASS_HIGH_BURN
    assert float(np.mean(c_band.classes[38:46, 38:46])) <= float(CLASS_LOW_DIST)

    with pytest.raises(ValueError):
        classify_sar_burn(**s, pol_mode="XX_YY")  # type: ignore[arg-type]
    bad = dict(s)
    bad["cross_post_db"] = np.zeros((4, 4), dtype=np.float32)
    with pytest.raises(ValueError):
        classify_sar_burn(**bad)


def test_png_encodings_and_glcm_wishart_helpers() -> None:
    classes = np.array([[0, 1], [2, 3]], dtype=np.uint8)
    assert _png_array(encode_class_png(classes)).tolist() == [[0, 85], [170, 255]]

    vals = np.array([[np.nan, OPERA_DIST_RANGE[0]], [2.0, OPERA_DIST_RANGE[1] + 2.0]], dtype=np.float32)
    px = _png_array(encode_continuous_png(vals, *OPERA_DIST_RANGE))
    assert px[0, 0] == 0
    assert px[0, 1] == 1
    assert px[1, 1] == 255
    decoded = decode_gray8_values(px, *OPERA_DIST_RANGE, nodata=0)
    assert np.isnan(decoded[0, 0])
    assert decoded[1, 0] == pytest.approx(2.0, abs=0.03)

    arr = np.full((12, 12), -14.0, dtype=np.float32)
    assert float(np.nanmax(np.abs(compute_glcm_contrast_change(arr, arr)))) == pytest.approx(0.0)
    lin = np.full((8, 8), 0.1, dtype=np.float32)
    assert np.allclose(compute_wishart_change_stat(lin, lin, lin, lin), 0.0, atol=1e-5)
    assert float(compute_opera_dist_metric(arr - arr, arr - arr, np.ones((12, 12), dtype=bool), np.ones((12, 12), dtype=np.float32)).max()) == 0.0


def test_majority_filter_geojson_and_asset_writer(tmp_path: Path) -> None:
    mask = np.zeros((20, 20), dtype=bool)
    mask[2, 2] = True
    mask[6:16, 6:16] = True
    filtered = majority_filter(mask)
    assert not filtered[2, 2]
    assert filtered[8:14, 8:14].all()

    s = _synthetic_scene()
    res = classify_sar_burn(**s, pol_mode="HH_HV")
    case_dir = tmp_path / "public" / "data" / "cases" / "wildfire-test"
    assets = write_sar_burn_assets(res, BBOX, case_dir, "wildfire-test", post_date="2025-02-01")
    assert assets.stats["sarBurnKm2"] > 0.0
    assert len(assets.downloads) == 3
    assert all((tmp_path / "public" / dl["url"]).exists() for dl in assets.downloads)
    for png in ("sar_burn_class.png", "sar_opera_dist.png", "sar_delta_rvi.png", "sar_log_rbr.png"):
        assert (case_dir / "rasters" / png).exists()

    geo = sar_burn_extent_geojson(res.classes, BBOX, burn_area_km2=assets.stats["sarBurnKm2"], pol_mode="HH_HV")
    assert len(geo["features"]) == 1
    assert geo["features"][0]["properties"]["pol_mode"] == "HH+HV"


def test_publish_sar_burn_for_case_is_idempotent_and_wires_vv(tmp_path: Path) -> None:
    cid = "wildfire-synth"
    pub = tmp_path / "public"
    case_dir = pub / "data" / "cases" / cid
    rasters = case_dir / "rasters"
    rasters.mkdir(parents=True)

    s = _synthetic_scene()
    (rasters / "s1_pre_vh.png").write_bytes(encode_continuous_png(s["cross_pre_db"], -30.0, 0.0))
    (rasters / "s1_post_vh.png").write_bytes(encode_continuous_png(s["cross_post_db"], -30.0, 0.0))
    (rasters / "s1_pre_vv.png").write_bytes(encode_continuous_png(s["co_pre_db"], -25.0, 0.0))
    (rasters / "s1_post_vv.png").write_bytes(encode_continuous_png(s["co_post_db"], -25.0, 0.0))

    case_def = {
        "schemaVersion": 2,
        "id": cid,
        "status": "real",
        "simulated": False,
        "category": "wildfire",
        "title": "Synthetic Wildfire Test",
        "view": {"center": [-121.705, 39.735], "zoom": 11.4},
        "metrics": {"preWindow": "2024-01-01/2024-01-15", "postWindow": "2024-02-01/2024-02-15"},
        "datasets": [
            {
                "id": "s1_sar",
                "name": "Sentinel-1 RTC · VH SAR Radar (All-Weather)",
                "kind": "raster",
                "status": "real",
                "extent": BBOX.to_ee_bounds(),
                "source": {"type": "image", "encoding": "gray8", "nodata": 0},
                "variables": [
                    {
                        "id": "VH",
                        "label": "VH Backscatter",
                        "unit": "dB",
                        "legend": {"min": -30, "max": 0, "colors": ["#0b0f14", "#f8fafc"]},
                    }
                ],
                "frames": [
                    {
                        "id": "s1-pre",
                        "datetime": "2024-01-15",
                        "phase": "before",
                        "files": {"VH": f"data/cases/{cid}/rasters/s1_pre_vh.png"},
                    },
                    {
                        "id": "s1-post",
                        "datetime": "2024-02-15",
                        "phase": "after",
                        "files": {"VH": f"data/cases/{cid}/rasters/s1_post_vh.png"},
                    },
                ],
            }
        ],
        "vectors": [],
        "downloads": [],
        "wildfire": {"kind": "geopulse-real"},
    }
    (case_dir / "case.json").write_text(json.dumps(case_def), encoding="utf-8")

    publish_sar_burn_for_case(cid, pub, pol_mode="VV_VH")
    publish_sar_burn_for_case(cid, pub, pol_mode="VV_VH")

    updated = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in updated["datasets"]] == ["s1_sar", DATASET_ID]
    s1_vars = [v["id"] for v in updated["datasets"][0]["variables"]]
    assert s1_vars == ["VH", "VV"]
    assert updated["datasets"][0]["frames"][0]["files"]["VV"].endswith("s1_pre_vv.png")
    assert [v["id"] for v in updated["vectors"]] == [VECTOR_ID]
    assert len(updated["downloads"]) == 3
    assert updated["metrics"]["sarBurnKm2"] > 0.0
    assert "opera-adt/dist-s1" in updated["wildfire"]["sarBurn"]["methods"]

    # Check attach_sar_burn_to_case supports custom ranges without mutation leaks
    assert LOG_RBR_RANGE_DB == (-6.0, 3.0)
    assert DELTA_RVI_RANGE == (-0.6, 0.3)
    assert callable(attach_sar_burn_to_case)
<<<<<<< HEAD
=======

    # Check optical burn_prob cross-validation when burn_prob.png & affectedKm2 exist
    prob = np.zeros(SHAPE, dtype=np.float32)
    prob[8:22, 26:40] = 0.85
    (rasters / "burn_prob.png").write_bytes(encode_continuous_png(prob, 0.0, 1.0))
    case_with_opt = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    case_with_opt["metrics"]["affectedKm2"] = 10.0
    (case_dir / "case.json").write_text(json.dumps(case_with_opt), encoding="utf-8")
    res2 = publish_sar_burn_for_case(cid, pub, pol_mode="VV_VH")
    assert "sarBurnInOpticalPct" in res2.stats
    assert res2.stats["sarToOpticalAreaPct"] > 0.0
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
