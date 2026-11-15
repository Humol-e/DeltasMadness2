"""Tests for end-to-end wildfire pipeline, GeoPulse dir conversion, and Click CLI."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from click.testing import CliRunner

from wildfire_monitor.cli import main as cli_main
from wildfire_monitor.config import BoundingBox, Settings
from wildfire_monitor.ingestion import geopulse_bridge as gb
from wildfire_monitor.ingestion import pipeline
from wildfire_monitor.ingestion import platform_exporter as pe


def _build_precomputed_result() -> gb.WildfireAnalysisResult:
    bbox = BoundingBox(min_lon=-118.62, min_lat=34.02, max_lon=-118.46, max_lat=34.12)
    grid = gb.make_geo_grid(bbox, max_dim=48)
    h, w = grid.height, grid.width

    s2_pre = np.broadcast_to(
        np.array([0.03, 0.06, 0.04, 0.35, 0.18, 0.08], dtype=np.float32)[:, None, None],
        (6, h, w),
    ).copy()
    s2_post = s2_pre.copy()
    s2_post[:, 10:25, 12:36] = np.array([0.05, 0.06, 0.07, 0.12, 0.20, 0.20], dtype=np.float32)[:, None, None]

    s1_pre = np.broadcast_to(
        np.array([-8.0, -14.0], dtype=np.float32)[:, None, None], (2, h, w)
    ).copy()
    s1_post = s1_pre.copy()
    s1_post[:, 10:25, 12:36] = np.array([-9.0, -17.0], dtype=np.float32)[:, None, None]

    dem = np.stack([np.full((h, w), 120.0, dtype=np.float32), np.full((h, w), 8.0, dtype=np.float32)])
    arrays = {"s2_pre": s2_pre, "s2_post": s2_post, "s1_pre": s1_pre, "s1_post": s1_post, "dem": dem}

    prob, sev, chg, unc = gb.compute_wildfire_physics(arrays, grid)
    extent = gb.extract_burn_extent_geojson(prob, tuple(bbox.to_ee_bounds()), step=1)
    aoi_fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": "Palisades"}, "geometry": bbox.to_geojson_polygon()}],
    }
    scenes = {k: [{"id": f"mock-{k}"}] for k in arrays}
    summary = gb.summarize_wildfire_result(
        grid=grid,
        burn_probability=prob,
        uncertainty=unc,
        burn_severity=sev,
        scenes=scenes,
        warnings=[],
    )
    return gb.WildfireAnalysisResult(
        grid=grid,
        burn_probability=prob,
        burn_severity=sev,
        change_probability=chg,
        uncertainty=unc,
        arrays=arrays,
        scenes=scenes,
        warnings=[],
        summary=summary,
        burn_extent_geojson=extent,
        aoi_geojson=aoi_fc,
    )


def test_run_wildfire_to_platform_dry_run_and_full_export(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        local_download_dir=tmp_path / "downloads",
        local_cases_dir=tmp_path / "cases",
        min_zoom=10,
        max_zoom=11,
    )

    dry = pipeline.run_wildfire_to_platform("palisades", settings, dry_run=True)
    assert dry.dry_run is True
    assert dry.case_id == "wildfire-palisades-la"

    precomputed = _build_precomputed_result()
    report = pipeline.run_wildfire_to_platform("palisades", settings, precomputed=precomputed)
    assert report.dry_run is False
    assert report.exported_case is not None
    assert (tmp_path / "downloads" / "catalog.json").exists()
    catalog = pipeline.load_local_catalog(tmp_path / "downloads")
    layers = {entry["product_layer"] for entry in catalog}
    assert {"burn_probability", "burn_severity", "change_probability", "uncertainty"} <= layers
    assert (tmp_path / "cases" / "wildfire-palisades-la" / "case.json").exists()
    assert (tmp_path / "cases" / "index.json").exists()


def test_convert_geopulse_dir_preserves_layer_png_appearance(tmp_path: Path) -> None:
    gp_dir = tmp_path / "gp_out"
    (gp_dir / "layers").mkdir(parents=True)
    bounds = (-121.78, 39.68, -121.63, 39.79)

    prob = np.zeros((32, 40), dtype=np.float32)
    prob[8:24, 10:30] = 0.92
    sev = np.where(prob >= 0.5, 4, 0).astype(np.uint8)

    gb.write_sar_compatible_geotiff(gp_dir / "burn_probability.tif", prob, bounds)
    gb.write_sar_compatible_geotiff(gp_dir / "burn_severity.tif", sev, bounds, nodata=255.0)
    (gp_dir / "summary.json").write_text(
        json.dumps({"affected_km2": 8.4, "observed_km2": 95.0, "review_km2": 0.5}),
        encoding="utf-8",
    )
    (gp_dir / "provenance.json").write_text(
        json.dumps({"request": {"before": "2018-09-15/2018-11-07", "after": "2018-11-26/2019-01-31"}}),
        encoding="utf-8",
    )
    # Create an exact sentinel RGBA pixel in GeoPulse layers/target.png to prove preservation
    custom_rgba = pe.ramp_rgba(prob, pe.PROB_BURN_PALETTE)
    pe.save_rgba_png(custom_rgba, gp_dir / "layers" / "target.png")

    settings = Settings(
        _env_file=None,
        local_download_dir=tmp_path / "downloads",
        local_cases_dir=tmp_path / "cases",
        min_zoom=10,
        max_zoom=10,
    )
    rep = pipeline.convert_geopulse_dir_to_platform(gp_dir, settings, case_id="camp-converted")
    assert rep.case_id == "camp-converted"
    out_png = tmp_path / "cases" / "camp-converted" / "layers" / "burn_probability.png"
    assert out_png.exists()
    # Exact pixel-for-pixel visual preservation
    from PIL import Image

    with Image.open(gp_dir / "layers" / "target.png") as orig_im, Image.open(out_png) as conv_im:
        np.testing.assert_array_equal(np.array(orig_im), np.array(conv_im))


def test_cli_list_aois_doctor_and_dry_run_commands(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    res_list = runner.invoke(cli_main, ["list-aois"])
    assert res_list.exit_code == 0
    assert "palisades" in res_list.output
    assert "camp_paradise" in res_list.output
    assert "martin_nv" in res_list.output

    res_doc = runner.invoke(cli_main, ["doctor", "--offline"])
    assert res_doc.exit_code == 0
    assert "GeoPulse dir:" in res_doc.output

    monkeypatch.setenv("LOCAL_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("LOCAL_CASES_DIR", str(tmp_path / "cases"))
    res_dry = runner.invoke(cli_main, ["run-wildfire", "--aoi", "all", "--dry-run"])
    assert res_dry.exit_code == 0
    assert "wildfire-palisades-la" in res_dry.output
    assert "wildfire-camp-paradise" in res_dry.output
    assert "wildfire-martin-nv" in res_dry.output
