"""Tests for Stage 1B GCS upload and Google Earth Engine asset ingestion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from glacier_monitor.ingestion import gee_uploader


def test_upload_geotiff_to_gcs(tmp_path: Path) -> None:
    tif = tmp_path / "nisar_sample.tif"
    tif.write_bytes(b"II*\x00")

    blob_mock = MagicMock()
    bucket_mock = MagicMock()
    bucket_mock.blob.return_value = blob_mock
    client_mock = MagicMock()
    client_mock.bucket.return_value = bucket_mock

    uri = gee_uploader.upload_geotiff_to_gcs(
        client_mock,
        local_path=tif,
        bucket_name="glacier-bucket",
        gcs_object_path="/asf/nisar_sample.tif",
    )

    assert uri == "gs://glacier-bucket/asf/nisar_sample.tif"
    bucket_mock.blob.assert_called_once_with("asf/nisar_sample.tif")
    blob_mock.upload_from_filename.assert_called_once_with(str(tif))


def test_upload_geotiff_rejects_non_tif(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("not a geotiff", encoding="utf-8")

    with pytest.raises(ValueError, match="GeoTIFF"):
        gee_uploader.upload_geotiff_to_gcs(
            MagicMock(), txt, "glacier-bucket", "asf/notes.txt"
        )


def test_build_manifest_and_cli_command(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ee = MagicMock()
    mock_ee.data.convert_asset_id_to_asset_name.side_effect = lambda x: x
    monkeypatch.setattr(gee_uploader, "ee", mock_ee)

    manifest = gee_uploader.build_gee_ingestion_manifest(
        gcs_uri="gs://my-bucket/sar/file_unw_phase.tif",
        asset_id="projects/my-proj/assets/glacier_sar_asf/item1",
        properties={"sensor": "SENTINEL-1"},
        start_time_utc="2026-08-20T00:11:22+00:00",
        bands=["unw_phase"],
        nodata_value=-9999.0,
    )

    assert manifest["name"] == "projects/my-proj/assets/glacier_sar_asf/item1"
    assert manifest["start_time"] == "2026-08-20T00:11:22Z"
    assert manifest["tilesets"][0]["sources"][0]["uris"] == [
        "gs://my-bucket/sar/file_unw_phase.tif"
    ]
    assert manifest["bands"] == [
        {"id": "unw_phase", "tilesetId": "ts", "tilesetBandIndex": 0}
    ]
    assert manifest["missingData"] == {"values": [-9999.0]}

    cmd = gee_uploader.build_earthengine_upload_command(
        gcs_uri="gs://my-bucket/sar/file_unw_phase.tif",
        asset_id=manifest["name"],
        start_time_utc=manifest["start_time"],
        pyramiding_policy="MEAN",
        allow_overwrite=True,
    )
    assert cmd == [
        "earthengine",
        "upload",
        "image",
        "--asset_id=projects/my-proj/assets/glacier_sar_asf/item1",
        "--time_start=2026-08-20T00:11:22Z",
        "--pyramiding_policy=mean",
        "--force",
        "gs://my-bucket/sar/file_unw_phase.tif",
    ]


def test_ensure_image_collection_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEEException(Exception):
        pass

    mock_ee = MagicMock()
    mock_ee.EEException = FakeEEException
    mock_ee.data.ASSET_TYPE_IMAGE_COLL = "ImageCollection"
    mock_ee.data.convert_asset_id_to_asset_name.side_effect = lambda x: x
    mock_ee.data.getAsset.side_effect = FakeEEException("Not found")
    monkeypatch.setattr(gee_uploader, "ee", mock_ee)

    col_id = gee_uploader.ensure_image_collection("projects/p/assets/col")
    assert col_id == "projects/p/assets/col"
    mock_ee.data.create_assets.assert_called_once_with(
        ["projects/p/assets/col"], "ImageCollection", True
    )


def test_ingest_gcs_geotiff_to_gee_starts_and_waits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tif = tmp_path / "uavsar.tif"
    tif.write_bytes(b"tif")

    mock_ee = MagicMock()
    mock_ee.data.convert_asset_id_to_asset_name.side_effect = lambda x: x
    mock_ee.data.newTaskId.return_value = ["REQ123"]
    mock_ee.data.startIngestion.return_value = {
        "id": "TASK999",
        "name": "projects/p/operations/TASK999",
    }
    mock_ee.data.getOperation.return_value = {
        "done": True,
        "metadata": {"state": "SUCCEEDED"},
    }
    monkeypatch.setattr(gee_uploader, "ee", mock_ee)

    res = gee_uploader.ingest_gcs_geotiff_to_gee(
        gcs_uri="gs://bucket/uavsar.tif",
        asset_id="projects/p/assets/col/uavsar",
        local_path=tif,
        project_id="p",
        wait=True,
    )

    assert res.task_id == "TASK999"
    assert res.state == "SUCCEEDED"
    mock_ee.data.startIngestion.assert_called_once()
