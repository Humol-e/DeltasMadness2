"""Tests for GeoPulse physics parity, pure-Python GeoTIFF I/O, UTM reprojection, and contours."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from wildfire_monitor.config import BoundingBox
from wildfire_monitor.ingestion import geopulse_bridge as gb


def _synthetic_wildfire_arrays(h: int = 80, w: int = 100) -> tuple[gb.GeoGrid, dict[str, np.ndarray]]:
    bbox = BoundingBox(min_lon=-118.60, min_lat=34.02, max_lon=-118.50, max_lat=34.10)
    grid = gb.make_geo_grid(bbox, max_dim=max(h, w))
    gh, gw = grid.height, grid.width

    # Reflectance bands: [B02, B03, B04, B08, B11, B12]
    forest_s2 = np.array([0.03, 0.06, 0.04, 0.35, 0.18, 0.08], dtype=np.float32)[:, None, None]
    burned_s2 = np.array([0.05, 0.06, 0.07, 0.12, 0.20, 0.20], dtype=np.float32)[:, None, None]
    bare_pre_s2 = np.array([0.18, 0.22, 0.26, 0.28, 0.40, 0.36], dtype=np.float32)[:, None, None]
    bare_dark_s2 = np.array([0.10, 0.14, 0.18, 0.20, 0.30, 0.34], dtype=np.float32)[:, None, None]
    water_s2 = np.array([0.05, 0.07, 0.05, 0.03, 0.01, 0.01], dtype=np.float32)[:, None, None]

    s2_pre = np.broadcast_to(forest_s2, (6, gh, gw)).copy()
    s2_post = np.broadcast_to(forest_s2, (6, gh, gw)).copy()
    s1_pre = np.broadcast_to(np.array([-8.0, -14.0], dtype=np.float32)[:, None, None], (2, gh, gw)).copy()
    s1_post = s1_pre.copy()

    # True burn patch
    s2_post[:, 20:50, 25:65] = burned_s2
    s1_post[:, 20:50, 25:65] = np.array([-9.0, -17.0], dtype=np.float32)[:, None, None]

    # False-alarm bare-rock darkening (low NDVI pre): must NOT be marked burned
    s2_pre[:, 5:15, 5:25] = bare_pre_s2
    s2_post[:, 5:15, 5:25] = bare_dark_s2

    # Open water patch: pre-water constraint must keep burn_probability == 0
    s2_pre[:, 60:72, 70:90] = water_s2
    s2_post[:, 60:72, 70:90] = water_s2
    s1_pre[:, 60:72, 70:90] = np.array([-23.0, -29.0], dtype=np.float32)[:, None, None]
    s1_post[:, 60:72, 70:90] = np.array([-23.0, -29.0], dtype=np.float32)[:, None, None]

    return grid, {"s2_pre": s2_pre, "s2_post": s2_post, "s1_pre": s1_pre, "s1_post": s1_post}


def test_wildfire_physics_matches_geopulse_severity_and_constraints() -> None:
    grid, arrays = _synthetic_wildfire_arrays()
    prob, sev, chg, unc = gb.compute_wildfire_physics(arrays, grid)

    assert sev is not None
    # True burn core should be high probability and high severity
    assert float(np.mean(prob[25:45, 30:60])) > 0.85
    assert int(np.median(sev[25:45, 30:60])) >= 3
    # Bare ground look-alike suppressed by NDVI fuel gate
    assert float(np.max(prob[5:15, 5:25])) < 0.25
    # Pre-existing water constrained to zero burn & uncertainty
    assert float(np.max(prob[60:72, 70:90])) == 0.0
    assert float(np.max(unc[60:72, 70:90])) == 0.0
    assert float(np.max(chg[25:45, 30:60])) > 0.7


def test_wildfire_sar_only_fallback_sets_min_uncertainty() -> None:
    grid, arrays = _synthetic_wildfire_arrays()
    sar_only_arrays = {"s1_pre": arrays["s1_pre"], "s1_post": arrays["s1_post"]}
    prob, sev, _chg, unc = gb.compute_wildfire_physics(sar_only_arrays, grid)
    assert sev is None
    assert float(np.mean(prob[25:45, 30:60])) > 0.65
    assert float(np.min(unc[25:45, 30:60])) >= gb.PHYSICS["SAR_ONLY_BURN_UNCERTAINTY"]


def test_geotiff_roundtrip_and_pil_compatibility(tmp_path: Path) -> None:
    arr = np.linspace(0.0, 1.0, 48 * 64, dtype=np.float32).reshape(48, 64)
    arr[0:4, 0:4] = np.nan
    bounds = (-118.62, 34.02, -118.46, 34.12)
    tif_path = gb.write_sar_compatible_geotiff(tmp_path / "burn_probability.tif", arr, bounds, nodata=-9999.0)

    # 1. Pillow must open cleanly (same path sar-platform load_geotiff_raster uses)
    with Image.open(tif_path) as im:
        assert im.size == (64, 48)
        assert 34735 in im.tag_v2

    # 2. Pure-Python reader preserves CRS, bounds, and NaN mask
    loaded = gb.read_geopulse_geotiff(tif_path)
    assert loaded.crs_epsg == 4326
    assert loaded.bounds_wgs84 == pytest.approx(bounds, abs=1e-6)
    assert np.isnan(loaded.data[0, 0])
    assert float(loaded.data[-1, -1]) == pytest.approx(1.0, abs=1e-5)


def test_utm_kruger_roundtrip_and_deflate_tiff_decoding(tmp_path: Path) -> None:
    # Verify Krüger forward <-> inverse roundtrip in Los Angeles UTM zone 11N (EPSG:32611)
    lon, lat = -118.54, 34.07
    x, y = gb.lonlat_to_utm(lon, lat, 32611)
    lon_back, lat_back = gb.utm_to_lonlat(x, y, 32611)
    assert float(lon_back) == pytest.approx(lon, abs=1e-6)
    assert float(lat_back) == pytest.approx(lat, abs=1e-6)

    # Build a minimal stripped DEFLATE-compressed float32 GeoTIFF with UTM EPSG:32611 GeoKeys
    width, height = 16, 12
    data = np.linspace(0.2, 0.9, width * height, dtype="<f4").reshape(height, width)
    compressed = zlib.compress(data.tobytes())

    # Assemble single-IFD little-endian TIFF manually
    # Header: 8 bytes -> IFD at offset 8
    entries = []
    # 11 tags in ascending ID order
    ifd_offset = 8
    n_tags = 11
    data_offset = ifd_offset + 2 + n_tags * 12 + 4

    payload_parts = bytearray()

    def add_blob(blob: bytes) -> int:
        off = data_offset + len(payload_parts)
        payload_parts.extend(blob)
        return off

    scale_off = add_blob(struct.pack("<3d", 10.0, 10.0, 0.0))
    tie_off = add_blob(struct.pack("<6d", 0.0, 0.0, 0.0, 350000.0, 3775000.0, 0.0))
    geokey_off = add_blob(struct.pack("<12H", 1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, 32611))
    strip_off = add_blob(compressed)

    def tag(tid: int, ftype: int, count: int, val: int) -> bytes:
        return struct.pack("<HHII", tid, ftype, count, val)

    entries.extend(
        [
            tag(256, 4, 1, width),
            tag(257, 4, 1, height),
            tag(258, 3, 1, 32),
            tag(259, 3, 1, 8),  # DEFLATE
            tag(273, 4, 1, strip_off),
            tag(278, 4, 1, height),
            tag(279, 4, 1, len(compressed)),
            tag(339, 3, 1, 3),  # IEEE float
            tag(33550, 12, 3, scale_off),
            tag(33922, 12, 6, tie_off),
            tag(34735, 3, 12, geokey_off),
        ]
    )
    tiff_bytes = (
        b"II"
        + struct.pack("<HI", 42, ifd_offset)
        + struct.pack("<H", n_tags)
        + b"".join(entries)
        + struct.pack("<I", 0)
        + bytes(payload_parts)
    )
    deflate_tif = tmp_path / "cog_deflate_utm.tif"
    deflate_tif.write_bytes(tiff_bytes)

    parsed = gb.read_geopulse_geotiff(deflate_tif)
    assert parsed.crs_epsg == 32611
    assert parsed.data.shape == (height, width)
    np.testing.assert_allclose(parsed.data, data, atol=1e-6)


def test_extract_burn_extent_geojson_produces_valid_polygons() -> None:
    arr = np.zeros((60, 80), dtype=np.float32)
    arr[15:45, 20:60] = 0.9
    bounds = (-118.62, 34.02, -118.46, 34.12)
    fc = gb.extract_burn_extent_geojson(arr, bounds, step=1)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) >= 1
    assert fc["features"][0]["properties"]["area_km2"] > 10.0
    assert fc["features"][0]["properties"]["step"] == 1
