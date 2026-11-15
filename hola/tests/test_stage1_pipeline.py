"""Tests for end-to-end Stage 1 pipeline and CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner
import pytest

from glacier_monitor import cli as cli_module
from glacier_monitor.cli import main as cli_main
from glacier_monitor.config import Settings
from glacier_monitor.ingestion import pipeline
from glacier_monitor.ingestion.asf_bridge import ASFGranuleSummary


def test_ingest_local_geotiffs_dry_run(tmp_path: Path) -> None:
    tif = tmp_path / "NISAR_L2_20261010T060000_langtang.tif"
    tif.write_bytes(b"tif")

    settings = Settings(
        _env_file=None,
        gee_project_id="demo-proj",
        gcs_bucket="demo-bucket",
        gcs_prefix="sar",
    )

    report = pipeline.ingest_local_geotiffs([tif], settings, dry_run=True)
    assert report.dry_run is True
    assert report.collection_id == "projects/demo-proj/assets/glacier_sar_asf"
    assert len(report.results) == 1

    res = report.results[0]
    assert res.state == "DRY_RUN"
    assert res.gcs_uri == "gs://demo-bucket/sar/NISAR_L2_20261010T060000_langtang.tif"
    assert res.asset_id == (
        "projects/demo-proj/assets/glacier_sar_asf/NISAR_L2_20261010T060000_langtang"
    )
    assert res.manifest["start_time"] == "2026-10-10T06:00:00Z"


def test_ingest_local_geotiffs_executes_upload_and_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tif = tmp_path / "UAVSAR_20261012.tif"
    tif.write_bytes(b"tif")

    settings = Settings(
        _env_file=None,
        gee_project_id="demo-proj",
        gcs_bucket="demo-bucket",
        gcs_prefix="sar",
    )

    monkeypatch.setattr(pipeline, "initialize_earth_engine", lambda _: "demo-proj")
    monkeypatch.setattr(pipeline, "ensure_image_collection", lambda col: col)
    monkeypatch.setattr(pipeline, "build_gcs_client", lambda _: MagicMock())
    monkeypatch.setattr(
        pipeline,
        "upload_geotiff_to_gcs",
        lambda client, local_path, bucket_name, gcs_object_path: (
            f"gs://{bucket_name}/{gcs_object_path}"
        ),
    )

    ingest_mock = MagicMock()
    monkeypatch.setattr(pipeline, "ingest_gcs_geotiff_to_gee", ingest_mock)

    report = pipeline.ingest_local_geotiffs([tif], settings, dry_run=False)
    assert len(report.results) == 1
    ingest_mock.assert_called_once()
    assert ingest_mock.call_args.kwargs["gcs_uri"] == "gs://demo-bucket/sar/UAVSAR_20261012.tif"


def test_ingest_hyp3_jobs_downloads_and_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tif = tmp_path / "S1AA_20260901T001122_20260820T001122_VV_INT80_unw_phase.tif"
    tif.write_bytes(b"tif")

    fake_job = MagicMock()
    fake_job.job_id = "job-42"
    fake_job.job_type = "INSAR_GAMMA"

    monkeypatch.setattr(pipeline, "create_hyp3_client", lambda _: MagicMock())
    monkeypatch.setattr(pipeline, "fetch_succeeded_jobs", lambda *a, **k: [fake_job])
    monkeypatch.setattr(
        pipeline,
        "download_and_extract_job",
        lambda *a, **k: [tif],
    )

    settings = Settings(
        _env_file=None,
        gee_project_id="demo-proj",
        gcs_bucket="demo-bucket",
        local_download_dir=tmp_path,
    )
    report = pipeline.ingest_hyp3_jobs(settings, job_id="job-42", dry_run=True)
    assert report.dry_run is True
    assert len(report.results) == 1
    assert report.results[0].gcs_uri.endswith(f"job-42/{tif.name}")
    assert report.results[0].manifest["properties"]["hyp3_job_id"] == "job-42"


def test_cli_list_aois_and_dry_run_ingest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    aoi_res = runner.invoke(cli_main, ["list-aois"])
    assert aoi_res.exit_code == 0
    assert "langtang" in aoi_res.output
    assert "khumbu" in aoi_res.output

    tif = tmp_path / "S1A_IW_20261001T001000_DVP_RTC30_vv.tif"
    tif.write_bytes(b"tif")
    monkeypatch.setenv("GEE_PROJECT_ID", "cli-proj")
    monkeypatch.setenv("GCS_BUCKET", "cli-bucket")
    monkeypatch.setenv("GCS_PREFIX", "asf-sar-ingest")
    monkeypatch.setenv("GEE_ASSET_COLLECTION", "")

    ingest_res = runner.invoke(
        cli_main,
        ["asf-ingest", "--local-file", str(tif), "--dry-run"],
    )
    assert ingest_res.exit_code == 0, ingest_res.output
    assert "gs://cli-bucket/asf-sar-ingest/" in ingest_res.output
    assert "earthengine upload image" in ingest_res.output


def test_ingest_hyp3_jobs_raises_when_no_geotiffs_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_job = MagicMock()
    fake_job.job_id = "job-empty"
    monkeypatch.setattr(pipeline, "create_hyp3_client", lambda _: MagicMock())
    monkeypatch.setattr(pipeline, "fetch_succeeded_jobs", lambda *a, **k: [fake_job])
    monkeypatch.setattr(pipeline, "download_and_extract_job", lambda *a, **k: [])

    settings = Settings(
        _env_file=None,
        gee_project_id="demo-proj",
        gcs_bucket="demo-bucket",
        local_download_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="GeoTIFF"):
        pipeline.ingest_hyp3_jobs(settings, job_id="job-empty")


def test_cli_asf_search_renders_list_polarization_without_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_scene = "OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_20261111T190434Z_S1D_30_v1.0"
    monkeypatch.setattr(
        cli_module,
        "search_asf_granules",
        lambda **_: [
            ASFGranuleSummary(
                scene_name=long_scene,
                file_id=long_scene,
                platform="Sentinel-1D",
                processing_level="RTC",
                start_time="2026-11-11T00:10:50Z",
                stop_time="2026-11-11T00:10:53Z",
                flight_direction="DESCENDING",
                polarization="VV+VH",
                url="https://example.org/item.h5",
            )
        ],
    )

    runner = CliRunner()
    res = runner.invoke(
        cli_main,
        ["asf-search", "--dataset", "opera_s1", "--start", "2026-11-01", "--end", "2026-11-14"],
    )
    assert res.exit_code == 0, res.output
    assert long_scene in res.output
    assert "VV+VH" in res.output


def test_cli_doctor_reports_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_sa = tmp_path / "missing_sa.json"
    monkeypatch.setenv("GEE_PROJECT_ID", "demo-proj")
    monkeypatch.setenv("GCS_BUCKET", "demo-bucket")
    monkeypatch.setenv("GEE_PRIVATE_KEY_PATH", str(missing_sa))
    monkeypatch.setattr(
        cli_module,
        "create_hyp3_client",
        lambda _: (_ for _ in ()).throw(RuntimeError("earthdata error")),
    )
    monkeypatch.setattr(cli_module, "search_asf_granules", lambda *a, **k: [])

    runner = CliRunner()
    res = runner.invoke(cli_main, ["doctor"])
    assert res.exit_code != 0
    assert "Missing file" in res.output
    assert "Google Earth Engine" in res.output


def test_stage_hyp3_jobs_locally_writes_catalog_without_gcs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    tif = tmp_path / "job-local" / "S1AA_20260901T001122_20260820T001122_coh.tif"
    tif.parent.mkdir(parents=True, exist_ok=True)
    tif.write_bytes(b"tif")

    fake_job = MagicMock()
    fake_job.job_id = "job-local"
    fake_job.job_type = "INSAR_GAMMA"

    monkeypatch.setattr(pipeline, "create_hyp3_client", lambda _: MagicMock())
    monkeypatch.setattr(pipeline, "fetch_succeeded_jobs", lambda *a, **k: [fake_job])
    monkeypatch.setattr(pipeline, "download_and_extract_job", lambda *a, **k: [tif])

    settings = Settings(_env_file=None, local_download_dir=tmp_path)
    report = pipeline.stage_hyp3_jobs_locally(settings, job_id="job-local")

    assert len(report.items) == 1
    assert report.catalog_path.exists()
    catalog = json.loads(report.catalog_path.read_text(encoding="utf-8"))
    assert len(catalog) == 1
    assert catalog[0]["product_layer"] == "coherence"
    assert (tmp_path / "job-local" / "local_manifest.json").exists()


def test_cli_doctor_local_mode_skips_gcs_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEE_PROJECT_ID", "demo-proj")
    monkeypatch.setenv("GCS_BUCKET", "")
    monkeypatch.setenv("GEE_PRIVATE_KEY_PATH", "")
    monkeypatch.setattr(cli_module, "initialize_earth_engine", lambda _: "demo-proj")

    fake_num = MagicMock()
    fake_num.getInfo.return_value = 1
    monkeypatch.setattr(cli_module.ee, "Number", lambda _: fake_num)

    fake_hyp3 = MagicMock()
    fake_hyp3.my_info.return_value = {"user_id": "dalta", "remaining_credits": 8000}
    monkeypatch.setattr(cli_module, "create_hyp3_client", lambda _: fake_hyp3)
    monkeypatch.setattr(cli_module, "search_asf_granules", lambda *a, **k: [])

    runner = CliRunner()
    res = runner.invoke(cli_main, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "LOCAL" in res.output
    assert "local mode without bucket" in res.output


def test_cli_asf_ingest_local_only_and_hyp3_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tif = tmp_path / "OPERA_L2_20261111T001050Z_VV.tif"
    tif.write_bytes(b"tif")
    monkeypatch.setenv("LOCAL_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("GCS_BUCKET", "")

    runner = CliRunner()
    ingest_res = runner.invoke(
        cli_main,
        ["asf-ingest", "--local-file", str(tif), "--local-only"],
    )
    assert ingest_res.exit_code == 0, ingest_res.output
    assert "catalog.json" in ingest_res.output

    fake_job = MagicMock()
    fake_job.job_id = "job-1"
    fake_job.name = "langtang"
    fake_job.job_type = "INSAR_GAMMA"
    fake_job.status_code = "SUCCEEDED"
    fake_job.expired.return_value = False
    fake_job.files = [{"filename": "a.zip"}]
    monkeypatch.setattr(cli_module, "create_hyp3_client", lambda _: MagicMock())
    monkeypatch.setattr(cli_module, "list_hyp3_jobs", lambda *a, **k: [fake_job])

    status_res = runner.invoke(cli_main, ["hyp3-status"])
    assert status_res.exit_code == 0, status_res.output
    assert "job-1" in status_res.output
    assert "SUCCEEDED" in status_res.output
