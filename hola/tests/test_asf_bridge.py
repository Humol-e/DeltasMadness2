"""Tests for Stage 1B ASF search, HyP3 downloads, and metadata parsing."""

from __future__ import annotations

import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from glacier_monitor.config import AOI_PRESETS, Settings
from glacier_monitor.ingestion import asf_bridge


def test_search_asf_granules_maps_datasets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_product = MagicMock()
    fake_product.properties = {
        "sceneName": "NISAR_L2_GCOV_20261001",
        "fileID": "NISAR_L2_GCOV_20261001-FILE",
        "platform": "NISAR",
        "processingLevel": "GCOV",
        "startTime": "2026-10-01T01:02:03Z",
        "stopTime": "2026-10-01T01:03:00Z",
        "flightDirection": "DESCENDING",
        "polarization": "HH+HV",
        "url": "https://datapool.asf.alaska.edu/example.tif",
    }

    geo_search_mock = MagicMock(return_value=[fake_product])
    monkeypatch.setattr(asf_bridge.asf, "geo_search", geo_search_mock)

    results = asf_bridge.search_asf_granules(
        "nisar",
        AOI_PRESETS["langtang"].bbox,
        "2026-09-15",
        "2026-10-15",
        max_results=5,
    )

    assert len(results) == 1
    assert results[0].scene_name == "NISAR_L2_GCOV_20261001"
    assert results[0].platform == "NISAR"
    geo_search_mock.assert_called_once()
    assert geo_search_mock.call_args.kwargs["dataset"] == asf_bridge.asf.DATASET.NISAR


def test_submit_hyp3_job_validates_granule_counts() -> None:
    hyp3_mock = MagicMock()
    with pytest.raises(ValueError):
        asf_bridge.submit_hyp3_job(hyp3_mock, "rtc", ["g1", "g2"], "bad_rtc")
    with pytest.raises(ValueError):
        asf_bridge.submit_hyp3_job(hyp3_mock, "insar", ["g1"], "bad_insar")

    asf_bridge.submit_hyp3_job(hyp3_mock, "insar", ["g1", "g2"], "ok_insar")
    hyp3_mock.submit_insar_job.assert_called_once_with(
        granule1="g1",
        granule2="g2",
        name="ok_insar",
        include_displacement_maps=True,
        include_inc_map=True,
    )


def test_download_and_extract_job_unzips_and_filters(tmp_path: Path) -> None:
    zip_path = tmp_path / "S1AA_20260901T001122_20260820T001122_VV_INT80_G_uw_1234.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("product/item_unw_phase.tif", b"tifdata-unw")
        zf.writestr("product/item_coh.tif", b"tifdata-coh")
        zf.writestr("product/item_browse.png", b"pngdata")

    fake_job = MagicMock()
    fake_job.job_id = "job-abc"
    fake_job.download_files.return_value = [zip_path]

    extracted = asf_bridge.download_and_extract_job(
        fake_job,
        output_dir=tmp_path / "out",
        product_suffixes=("_unw_phase.tif",),
    )

    assert len(extracted) == 1
    assert extracted[0].name == "item_unw_phase.tif"
    assert extracted[0].read_bytes() == b"tifdata-unw"


def test_download_and_extract_job_blocks_zip_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../outside.tif", b"bad")

    fake_job = MagicMock()
    fake_job.job_id = "job-evil"
    fake_job.download_files.return_value = [zip_path]

    with pytest.raises(ValueError, match="Invalid path inside ZIP"):
        asf_bridge.download_and_extract_job(fake_job, output_dir=tmp_path / "out")


def test_parse_geotiff_metadata_extracts_dates_sensor_and_layer() -> None:
    insar_path = Path("S1AA_20260901T001122_20260820T001122_VV_INT80_G_uw_unw_phase.tif")
    meta = asf_bridge.parse_geotiff_metadata(insar_path, job_id="j1", job_type="INSAR_GAMMA")
    assert meta.sensor == "SENTINEL-1"
    assert meta.product_layer == "unwrapped_phase"
    assert meta.start_time_utc == "2026-08-20T00:11:22Z"
    assert meta.end_time_utc == "2026-09-01T00:11:22Z"
    assert meta.properties["hyp3_job_id"] == "j1"

    uavsar_path = Path("UAVSAR_langtang_20260512_HHVV.tif")
    uav_meta = asf_bridge.parse_geotiff_metadata(uavsar_path)
    assert uav_meta.sensor == "UAVSAR"
    assert uav_meta.start_time_utc == "2026-05-12T00:00:00Z"


def test_transfer_with_sar_asf_to_gee_missing_and_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        gcs_bucket="my-bucket",
        gee_project_id="my-proj",
    )
    fake_job = MagicMock()
    fake_job.to_dict.return_value = {"job_id": "123"}

    def raise_missing(name: str):
        raise ModuleNotFoundError("No module named 'sar_asf_to_gee'")

    monkeypatch.setattr(asf_bridge.importlib, "import_module", raise_missing)
    with pytest.raises(RuntimeError, match="sar-asf-to-gee"):
        asf_bridge.transfer_with_sar_asf_to_gee(fake_job, settings)

    transfer_instance = MagicMock()
    transfer_cls = MagicMock(return_value=transfer_instance)
    fake_mod = types.SimpleNamespace(Transfer=transfer_cls)
    monkeypatch.setattr(asf_bridge.importlib, "import_module", lambda _: fake_mod)

    res = asf_bridge.transfer_with_sar_asf_to_gee(fake_job, settings)
    assert res is transfer_instance
    transfer_instance.to_local.assert_called_once()
    transfer_instance.to_gcs.assert_called_once()
    transfer_instance.create_gee_asset.assert_called_once()


def test_search_asf_granules_normalizes_list_polarization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_product = MagicMock()
    fake_product.properties = {
        "sceneName": "OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_S1D_30_v1.0",
        "fileID": "OPERA_L2_RTC-S1_T121-258661-IW3_20261111T001050Z_S1D_30_v1.0",
        "platform": "Sentinel-1D",
        "processingLevel": "RTC",
        "startTime": "2026-11-11T00:10:50Z",
        "stopTime": "2026-11-11T00:10:53Z",
        "flightDirection": "DESCENDING",
        "polarization": ["VV", "VH"],
        "url": "https://example.org/opera.h5",
    }
    monkeypatch.setattr(asf_bridge.asf, "geo_search", lambda **_: [fake_product])

    results = asf_bridge.search_asf_granules(
        "opera_s1",
        AOI_PRESETS["langtang"].bbox,
        "2026-11-01",
        "2026-11-14",
    )

    assert len(results) == 1
    assert results[0].polarization == "VV+VH"


def test_parse_geotiff_metadata_ignores_invalid_date_digits() -> None:
    weird_path = Path("OPERA_20269999_20261015_vv.tif")
    meta = asf_bridge.parse_geotiff_metadata(weird_path)
    assert meta.sensor == "OPERA-S1"
    assert meta.start_time_utc == "2026-10-15T00:00:00Z"
    assert meta.end_time_utc == "2026-10-15T00:00:00Z"


def test_list_hyp3_jobs_passes_filters() -> None:
    batch = types.SimpleNamespace(jobs=["j1", "j2"])
    hyp3_mock = MagicMock()
    hyp3_mock.find_jobs.return_value = batch

    jobs = asf_bridge.list_hyp3_jobs(hyp3_mock, job_name="langtang", status_code="succeeded")
    assert jobs == ["j1", "j2"]
    hyp3_mock.find_jobs.assert_called_once_with(name="langtang", status_code="SUCCEEDED")


def test_download_asf_granule_geotiffs_filters_additional_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_product = MagicMock()
    fake_product.properties = {"url": "https://example.org/item.h5"}

    def fake_get_urls(fileType):
        if fileType == asf_bridge.asf.FileDownloadType.DEFAULT_FILE:
            return ["https://example.org/item.h5"]
        return [
            "https://example.org/OPERA_20261111T001050Z_VV.tif",
            "https://example.org/OPERA_20261111T001050Z_VH.tif",
            "https://example.org/OPERA_20261111T001050Z_mask.tif",
        ]

    fake_product.get_urls.side_effect = fake_get_urls
    monkeypatch.setattr(asf_bridge.asf, "granule_search", lambda _: [fake_product])
    monkeypatch.setattr(asf_bridge, "create_asf_session", lambda _: MagicMock())

    def fake_download_url(url: str, path: str, filename: str | None = None, session=None) -> None:
        dest = Path(path) / (filename or url.rsplit("/", 1)[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"tif")

    monkeypatch.setattr(asf_bridge.asf, "download_url", fake_download_url)

    settings = Settings(_env_file=None, earthdata_token="tok")
    files = asf_bridge.download_asf_granule_geotiffs(
        "OPERA_GRANULE_1",
        tmp_path,
        settings,
        product_suffixes=("_VV.tif",),
    )

    assert len(files) == 1
    assert files[0].name == "OPERA_20261111T001050Z_VV.tif"
    assert files[0].exists()
