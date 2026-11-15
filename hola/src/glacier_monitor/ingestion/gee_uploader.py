"""Stage 1B (GCS & GEE): Upload GeoTIFFs to GCS and register Earth Engine assets."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import ee
from google.cloud import storage


@dataclass(frozen=True)
class IngestionResult:
    """Result of staging a GeoTIFF to GCS and registering it in Google Earth Engine."""

    local_path: Path
    gcs_uri: str
    asset_id: str
    task_id: str | None
    state: str
    cli_command: list[str]
    manifest: dict[str, Any]


def upload_geotiff_to_gcs(
    client: storage.Client,
    local_path: Path,
    bucket_name: str,
    gcs_object_path: str,
) -> str:
    """Upload a local GeoTIFF file to Google Cloud Storage and return its gs:// URI."""
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError(f"Local GeoTIFF not found: {local_path}")
    if local_path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError(f"File '{local_path}' does not have a GeoTIFF extension (.tif/.tiff).")

    normalized_object = gcs_object_path.lstrip("/")
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(normalized_object)
    blob.upload_from_filename(str(local_path))
    return f"gs://{bucket_name}/{normalized_object}"


def ensure_image_collection(collection_id: str) -> str:
    """Ensure the destination GEE ImageCollection exists, creating it if missing."""
    normalized_id = ee.data.convert_asset_id_to_asset_name(collection_id.strip("/"))
    try:
        asset = ee.data.getAsset(normalized_id)
        asset_type = str(asset.get("type", "")).upper()
        if asset_type and asset_type != "IMAGE_COLLECTION":
            raise ValueError(
                f"Asset '{normalized_id}' already exists with type '{asset_type}', "
                "not 'IMAGE_COLLECTION'."
            )
        return normalized_id
    except ee.EEException:
        ee.data.create_assets([normalized_id], ee.data.ASSET_TYPE_IMAGE_COLL, True)
        return normalized_id


def _normalize_iso_utc(timestamp: str) -> str:
    cleaned = timestamp.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_gee_ingestion_manifest(
    gcs_uri: str,
    asset_id: str,
    *,
    properties: dict[str, Any] | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    bands: Sequence[str] | None = None,
    pyramiding_policy: str = "MEAN",
    nodata_value: float | None = None,
    crs: str | None = None,
) -> dict[str, Any]:
    """Build a GEE ImageManifest payload compatible with ee.data.startIngestion."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Source URI must start with 'gs://': {gcs_uri}")

    normalized_asset_id = ee.data.convert_asset_id_to_asset_name(asset_id.strip("/"))
    tileset: dict[str, Any] = {
        "id": "ts",
        "sources": [{"uris": [gcs_uri]}],
    }
    if crs:
        tileset["crs"] = crs

    manifest: dict[str, Any] = {
        "name": normalized_asset_id,
        "tilesets": [tileset],
        "properties": dict(properties or {}),
        "pyramidingPolicy": pyramiding_policy.upper(),
    }

    if start_time_utc:
        manifest["start_time"] = _normalize_iso_utc(start_time_utc)
    if end_time_utc:
        manifest["end_time"] = _normalize_iso_utc(end_time_utc)
    if bands:
        manifest["bands"] = [
            {"id": band_name, "tilesetId": "ts", "tilesetBandIndex": index}
            for index, band_name in enumerate(bands)
        ]
    if nodata_value is not None:
        manifest["missingData"] = {"values": [nodata_value]}

    return manifest


def build_earthengine_upload_command(
    gcs_uri: str,
    asset_id: str,
    *,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    pyramiding_policy: str | None = None,
    nodata_value: float | None = None,
    allow_overwrite: bool = False,
    wait: bool = False,
) -> list[str]:
    """Construct the equivalent `earthengine upload image` CLI command."""
    cmd = ["earthengine", "upload", "image", f"--asset_id={asset_id}"]
    if start_time_utc:
        cmd.append(f"--time_start={start_time_utc}")
    if end_time_utc:
        cmd.append(f"--time_end={end_time_utc}")
    if pyramiding_policy:
        cmd.append(f"--pyramiding_policy={pyramiding_policy.lower()}")
    if nodata_value is not None:
        cmd.append(f"--nodata_value={nodata_value}")
    if allow_overwrite:
        cmd.append("--force")
    if wait:
        cmd.append("--wait")
    cmd.append(gcs_uri)
    return cmd


def _wait_for_ingestion_operation(
    operation_name: str,
    timeout_sec: int,
    poll_interval_sec: float = 10.0,
) -> str:
    deadline = time.monotonic() + timeout_sec
    while True:
        status = ee.data.getOperation(operation_name)
        metadata = status.get("metadata", {}) or {}
        state = str(metadata.get("state") or "UNKNOWN")
        if status.get("done", False):
            error_msg = (status.get("error") or {}).get("message")
            if error_msg:
                raise RuntimeError(f"GEE ingestion failed ({operation_name}): {error_msg}")
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out ({timeout_sec}s) waiting for GEE task {operation_name} "
                f"(last state: {state})."
            )
        time.sleep(poll_interval_sec)


def ingest_gcs_geotiff_to_gee(
    gcs_uri: str,
    asset_id: str,
    local_path: Path,
    *,
    project_id: str | None = None,
    properties: dict[str, Any] | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    bands: Sequence[str] | None = None,
    pyramiding_policy: str = "MEAN",
    nodata_value: float | None = None,
    allow_overwrite: bool = False,
    wait: bool = False,
    timeout_sec: int = 900,
) -> IngestionResult:
    """Start a Google Earth Engine asset ingestion task from a GCS GeoTIFF URI."""
    manifest = build_gee_ingestion_manifest(
        gcs_uri=gcs_uri,
        asset_id=asset_id,
        properties=properties,
        start_time_utc=start_time_utc,
        end_time_utc=end_time_utc,
        bands=bands,
        pyramiding_policy=pyramiding_policy,
        nodata_value=nodata_value,
    )
    cli_command = build_earthengine_upload_command(
        gcs_uri=gcs_uri,
        asset_id=manifest["name"],
        start_time_utc=manifest.get("start_time"),
        end_time_utc=manifest.get("end_time"),
        pyramiding_policy=pyramiding_policy,
        nodata_value=nodata_value,
        allow_overwrite=allow_overwrite,
        wait=wait,
    )

    request_id = ee.data.newTaskId()[0]
    response = ee.data.startIngestion(request_id, manifest, allow_overwrite=allow_overwrite)
    task_id = str(response.get("id") or request_id)
    state = "SUBMITTED"

    if wait:
        operation_name = response.get("name")
        if not operation_name:
            if not project_id:
                raise ValueError(
                    "project_id is required to poll the GEE ingestion operation status."
                )
            operation_name = f"projects/{project_id}/operations/{task_id}"
        state = _wait_for_ingestion_operation(operation_name, timeout_sec=timeout_sec)

    return IngestionResult(
        local_path=local_path,
        gcs_uri=gcs_uri,
        asset_id=manifest["name"],
        task_id=task_id,
        state=state,
        cli_command=cli_command,
        manifest=manifest,
    )
