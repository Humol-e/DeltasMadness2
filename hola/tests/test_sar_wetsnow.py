"""Unit tests for the SAR wet-snow product (multitemporal backscatter ratio)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glacier_monitor.config import BoundingBox
from glacier_monitor.ingestion.platform_exporter import build_glacier_case_definition, encode_gray8_png
from glacier_monitor.ingestion.sar_wetsnow import (
    CLASS_POSSIBLE_WET,
    CLASS_STABLE,
    CLASS_WET,
    DATASET_ID,
    NDSI_PNG_RANGE,
    RATIO_RANGE_DB,
    S1_PNG_RANGE_DB,
    VECTOR_ID,
    WetSnowAssets,
    attach_wet_snow_to_case,
    classify_wet_snow,
    decode_gray8_db,
    encode_class_png,
    encode_ratio_png,
    majority_filter,
    publish_sar_wet_snow_for_case,
    summarize_wet_snow,
    wet_snow_extent_geojson,
    write_geotiff,
    write_wet_snow_assets,
)

BBOX = BoundingBox(min_lon=85.50, min_lat=28.18, max_lon=85.74, max_lat=28.30)
SHAPE = (40, 40)


def _png_array(raw: bytes) -> np.ndarray:
    import io

    return np.array(Image.open(io.BytesIO(raw)))


def _scene() -> dict[str, np.ndarray]:
    """Dry-snow reference everywhere; the melt window drops in chosen blocks."""
    vv_pre = np.full(SHAPE, -8.0, dtype=np.float32)
    vh_pre = np.full(SHAPE, -14.0, dtype=np.float32)
    return {
        "vv_pre": vv_pre,
        "vv_post": vv_pre.copy(),
        "vh_pre": vh_pre,
        "vh_post": vh_pre.copy(),
        "ndsi_pre": np.full(SHAPE, 0.7, dtype=np.float32),
    }


def test_decode_gray8_db_inverts_encode_gray8_png() -> None:
    values = np.array([[-20.0, -10.0], [-3.0, np.nan]], dtype=np.float32)
    decoded = decode_gray8_db(_png_array(encode_gray8_png(values, -20.0, 2.0)), -20.0, 2.0)
<<<<<<< HEAD
    assert np.isnan(decoded[0, 0])  # encoded as 0 -> nodata by project convention
=======
    assert np.isnan(decoded[0, 0])  # encoded as 0 -> nodata sentinel
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
    assert decoded[0, 1] == pytest.approx(-10.0, abs=0.05)
    assert decoded[1, 0] == pytest.approx(-3.0, abs=0.05)
    assert np.isnan(decoded[1, 1])

<<<<<<< HEAD
=======
    # Schema v2 1..255 quantization preserves the minimum valid endpoint without treating it as 0 nodata
    schema_v2 = np.array([[0, 1, 255]], dtype=np.uint8)
    decoded_v2 = decode_gray8_db(schema_v2, -20.0, 2.0, full_range=False)
    assert np.isnan(decoded_v2[0, 0])
    assert decoded_v2[0, 1] == pytest.approx(-20.0)
    assert decoded_v2[0, 2] == pytest.approx(2.0)

>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7

def test_classify_wet_snow_assigns_classes_and_masks() -> None:
    s = _scene()
    s["vv_post"][0:10, 0:10] = -8.0 - 3.0  # both channels drop -> wet snow
    s["vh_post"][0:10, 0:10] = -14.0 - 3.0
    s["vv_post"][0:10, 10:20] = -8.0 - 3.0  # only VV drops -> possible wet
    s["ndsi_pre"][20:30, 0:10] = 0.05  # no snow in the reference -> outside the domain
    s["vv_post"][20:30, 0:10] = -8.0 - 6.0
    s["vh_post"][20:30, 0:10] = -14.0 - 6.0
    s["vh_pre"][30:40, 0:10] = -25.5  # reference below the VH noise floor -> unusable
    s["vh_post"][30:40, 0:10] = -26.0
    s["vv_post"][30:40, 10:20] = np.nan  # missing melt-season data

    res = classify_wet_snow(**s)

    assert res.classes.dtype == np.uint8
    assert (res.classes[0:10, 0:10] == CLASS_WET).all()
    assert (res.classes[0:10, 10:20] == CLASS_POSSIBLE_WET).all()
    assert (res.classes[0:10, 20:40] == CLASS_STABLE).all()
    assert (res.classes[20:30, 0:10] == 0).all()
    assert (res.classes[30:40, 0:10] == 0).all()
    assert (res.classes[30:40, 10:20] == 0).all()
    assert res.ratio_vv_db[0, 0] == pytest.approx(-3.0)
    assert np.isnan(res.ratio_vv_db[25, 5])  # ratio only reported inside the snow domain


def test_classify_wet_snow_threshold_is_inclusive_and_validated() -> None:
    s = _scene()
    s["vv_post"][:] = -8.0 - 2.0
    s["vh_post"][:] = -14.0 - 2.0
    assert (classify_wet_snow(**s).classes == CLASS_WET).all()

    s["vv_post"][:] = -8.0 - 1.9
    s["vh_post"][:] = -14.0 - 1.9
    assert (classify_wet_snow(**s).classes == CLASS_STABLE).all()

    with pytest.raises(ValueError):
        classify_wet_snow(**s, threshold_db=2.0)
    s["ndsi_pre"] = np.zeros((3, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        classify_wet_snow(**s)


def test_summarize_wet_snow_reports_areas_and_share() -> None:
    classes = np.zeros(SHAPE, dtype=np.uint8)
    classes[:, :] = CLASS_STABLE
    classes[0:10, :] = CLASS_WET  # 25 % of the snow domain
    stats = summarize_wet_snow(classes, BBOX)
    assert stats["wetSnowPct"] == pytest.approx(25.0)
    assert stats["wetSnowKm2"] > 0
    assert stats["snowReferenceKm2"] == pytest.approx(stats["wetSnowKm2"] * 4, rel=0.01)
    assert summarize_wet_snow(np.zeros(SHAPE, dtype=np.uint8), BBOX)["wetSnowPct"] == 0.0


def test_encode_ratio_png_follows_schema_v2_nodata_convention() -> None:
    vmin, vmax = RATIO_RANGE_DB
    ratio = np.array([[np.nan, vmin - 5.0], [0.0, vmax + 5.0]], dtype=np.float32)
    px = _png_array(encode_ratio_png(ratio))
    assert px[0, 0] == 0  # nodata
    assert px[0, 1] == 1  # clipped to the minimum is still valid data (never 0)
    assert px[1, 1] == 255
    zero_db = vmin + (int(px[1, 0]) - 1) / 254.0 * (vmax - vmin)  # client-side decoding
    assert zero_db == pytest.approx(0.0, abs=0.06)


def test_encode_class_png_decodes_back_to_discrete_classes() -> None:
    classes = np.array([[0, 1], [2, 3]], dtype=np.uint8)
    px = _png_array(encode_class_png(classes))
    assert px.tolist() == [[0, 85], [170, 255]]
    decoded = np.rint((px.astype(np.float32) - 1) / 254.0 * 3.0)  # client-side decoding
    assert decoded[0, 1] == 1 and decoded[1, 0] == 2 and decoded[1, 1] == 3


def test_majority_filter_removes_isolated_pixels_but_keeps_blocks() -> None:
    mask = np.zeros((20, 20), dtype=bool)
    mask[3, 3] = True
    mask[8:16, 8:16] = True
    out = majority_filter(mask)
    assert not out[3, 3]
    assert out[10:14, 10:14].all()


def test_wet_snow_extent_geojson_outlines_class_three_only() -> None:
    classes = np.full(SHAPE, CLASS_STABLE, dtype=np.uint8)
    classes[10:30, 10:30] = CLASS_WET
    classes[2:5, 2:5] = CLASS_POSSIBLE_WET
    geo = wet_snow_extent_geojson(classes, BBOX, wet_area_km2=12.3456, threshold_db=-2.0)
    assert len(geo["features"]) == 1
    props = geo["features"][0]["properties"]
    assert props["area_km2"] == 12.35
    assert geo["features"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    lon_min, lat_min, lon_max, lat_max = (
        min(x for ring in _rings(geo) for x, _ in ring),
        min(y for ring in _rings(geo) for _, y in ring),
        max(x for ring in _rings(geo) for x, _ in ring),
        max(y for ring in _rings(geo) for _, y in ring),
    )
    assert BBOX.min_lon <= lon_min < lon_max <= BBOX.max_lon
    assert BBOX.min_lat <= lat_min < lat_max <= BBOX.max_lat

    empty = wet_snow_extent_geojson(np.full(SHAPE, CLASS_STABLE, dtype=np.uint8), BBOX, wet_area_km2=0, threshold_db=-2.0)
    assert empty["features"] == []


def _rings(geo: dict) -> list[list[list[float]]]:
    geom = geo["features"][0]["geometry"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [ring for poly in polys for ring in poly]


def test_write_geotiff_roundtrips_data_and_georeferencing(tmp_path: Path) -> None:
    arr = np.zeros(SHAPE, dtype=np.uint8)
    arr[5:10, 5:10] = 3
    bounds = (BBOX.min_lon, BBOX.min_lat, BBOX.max_lon, BBOX.max_lat)
    size = write_geotiff(tmp_path / "class.tif", arr, bounds, nodata=0)
    assert size == (tmp_path / "class.tif").stat().st_size > 0
    with Image.open(tmp_path / "class.tif") as img:
        assert np.array_equal(np.array(img), arr)
        assert img.tag_v2[33922][3:5] == (BBOX.min_lon, BBOX.max_lat)
        assert img.tag_v2[33550][0] == pytest.approx((BBOX.max_lon - BBOX.min_lon) / SHAPE[1])

    flt = np.full(SHAPE, np.nan, dtype=np.float32)
    flt[0, 0] = -3.5
    write_geotiff(tmp_path / "ratio.tif", flt, bounds, nodata=-9999.0)
    with Image.open(tmp_path / "ratio.tif") as img:
        back = np.array(img)
        assert back[0, 0] == pytest.approx(-3.5)
        assert back[1, 1] == -9999.0


def _assets(tmp_path: Path) -> WetSnowAssets:
    s = _scene()
    s["vv_post"][10:30, 10:30] = -12.0
    s["vh_post"][10:30, 10:30] = -18.0
    return write_wet_snow_assets(classify_wet_snow(**s), BBOX, tmp_path / "data" / "cases" / "glacier-x", "glacier-x")


def _case_def(**kwargs) -> dict:
    return build_glacier_case_definition(
        case_id="glacier-x",
        name="X",
        region="Nepal",
        summary="Real",
        bbox=BBOX,
        zoom=11.6,
        pre_window=("2026-01-01", "2026-02-28"),
        post_window=("2026-05-15", "2026-08-31"),
        stats={
            "s2_pre_count": 1,
            "s2_post_count": 1,
            "s1_pre_count": 1,
            "s1_post_count": 1,
            "modis_pre_count": 1,
            "modis_post_count": 1,
            "snow_pre_pct": 30.0,
            "snow_post_pct": 10.0,
            "lst_pre_c": -5.0,
            "lst_post_c": 5.0,
            "melt_area_km2": 3.0,
        },
        downloads=[],
        **kwargs,
    )


def test_build_case_definition_adds_wet_snow_only_when_requested(tmp_path: Path) -> None:
    assert len(_case_def()["datasets"]) == 5  # unchanged without the new product

    case = _case_def(wet_snow=_assets(tmp_path))
    ids = [d["id"] for d in case["datasets"]]
    assert ids == ["s2_rgb", "s2_ndsi", "s1_sar", DATASET_ID, "modis_cryo", "melt_change"]
    ds = case["datasets"][3]
    assert ds["source"] == {"type": "image", "encoding": "gray8", "nodata": 0}
    assert [v["id"] for v in ds["variables"]] == ["wet_class", "ratio_vv", "ratio_vh"]
    assert ds["variables"][0]["legend"]["discrete"].keys() == {"1", "2", "3"}
    assert len(ds["frames"]) == 1 and ds["frames"][0]["phase"] == "change"
    for var in ds["variables"]:
        assert var["id"] in ds["frames"][0]["files"]
        assert var["legend"]["min"] < var["legend"]["max"] and len(var["legend"]["colors"]) >= 2
    assert VECTOR_ID in [v["id"] for v in case["vectors"]]
    assert {d["datasetId"] for d in case["downloads"]} == {DATASET_ID}
    assert case["metrics"]["sarWetSnowKm2"] > 0
    assert any(str(s).startswith("Derived · Sentinel-1 VV/VH") for s in case["sources"])


def test_attach_wet_snow_is_idempotent(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    case = _case_def(wet_snow=assets)
    snapshot = json.dumps(case, sort_keys=True)
    attach_wet_snow_to_case(
        case,
        assets,
        case_id="glacier-x",
        extent=[BBOX.min_lon, BBOX.min_lat, BBOX.max_lon, BBOX.max_lat],
        pre_window=("2026-01-01", "2026-02-28"),
        post_window=("2026-05-15", "2026-08-31"),
    )
    assert json.dumps(case, sort_keys=True) == snapshot


def test_publish_from_published_case_reuses_png_inputs_and_keeps_existing_data(tmp_path: Path) -> None:
    public = tmp_path / "public"
    case_dir = public / "data" / "cases" / "glacier-x"
    case_dir.mkdir(parents=True)
    case = _case_def()
    (case_dir / "case.json").write_text(json.dumps(case, indent=2), encoding="utf-8")

    vv_pre = np.full(SHAPE, -8.0, dtype=np.float32)
    vh_pre = np.full(SHAPE, -14.0, dtype=np.float32)
    vv_post, vh_post = vv_pre.copy(), vh_pre.copy()
    vv_post[10:30, 10:30] -= 4.0
    vh_post[10:30, 10:30] -= 4.0
    ndsi = np.full(SHAPE, 0.7, dtype=np.float32)
    (case_dir / "s1_pre_vv.png").write_bytes(encode_gray8_png(vv_pre, *S1_PNG_RANGE_DB["vv"]))
    (case_dir / "s1_post_vv.png").write_bytes(encode_gray8_png(vv_post, *S1_PNG_RANGE_DB["vv"]))
    (case_dir / "s1_pre_vh.png").write_bytes(encode_gray8_png(vh_pre, *S1_PNG_RANGE_DB["vh"]))
    (case_dir / "s1_post_vh.png").write_bytes(encode_gray8_png(vh_post, *S1_PNG_RANGE_DB["vh"]))
    (case_dir / "s2_pre_ndsi.png").write_bytes(encode_gray8_png(ndsi, *NDSI_PNG_RANGE))

    res = publish_sar_wet_snow_for_case("glacier-x", public)
    assert res.stats["wetSnowKm2"] > 0
    assert res.geotiff_count == 3

    updated = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in updated["datasets"]][:3] == ["s2_rgb", "s2_ndsi", "s1_sar"]
    assert DATASET_ID in [d["id"] for d in updated["datasets"]]
    new_ds = next(d for d in updated["datasets"] if d["id"] == DATASET_ID)
    for rel in new_ds["frames"][0]["files"].values():
        assert (public / rel).is_file(), rel
    for dl in updated["downloads"]:
        assert (public / dl["url"]).is_file(), dl["url"]
    assert (case_dir / "sar_wetsnow_extent.geojson").is_file()
    expected_share = 100.0 * (20 * 20) / (SHAPE[0] * SHAPE[1])
    assert updated["metrics"]["sarWetSnowPct"] == pytest.approx(expected_share, abs=0.2)

    first = (case_dir / "case.json").read_text(encoding="utf-8")
    publish_sar_wet_snow_for_case("glacier-x", public)
    assert (case_dir / "case.json").read_text(encoding="utf-8") == first
<<<<<<< HEAD
=======


def test_summarize_wet_snow_cross_validates_optical_ndsi_post() -> None:
    classes = np.full(SHAPE, CLASS_STABLE, dtype=np.uint8)
    classes[0:10, 0:10] = CLASS_WET  # 100 wet-snow pixels
    ndsi_post = np.full(SHAPE, 0.05, dtype=np.float32)  # bare / melted optically
    ndsi_post[0:5, 0:10] = 0.65  # half of the wet snow is still optically snow-covered (subsurface melt)
    stats = summarize_wet_snow(classes, BBOX, ndsi_post=ndsi_post, snow_ndsi_min=0.28)
    assert stats["wetSnowInOpticalSnowPct"] == pytest.approx(50.0)
    assert stats["subsurfaceWetSnowKm2"] == pytest.approx(stats["wetSnowKm2"] * 0.5, rel=0.05)
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
