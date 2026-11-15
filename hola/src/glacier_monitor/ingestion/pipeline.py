"""Stage 1 orchestration pipeline: ASF/HyP3 or local GeoTIFFs -> GCS -> GEE."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from glacier_monitor.config import Settings
from glacier_monitor.gee_auth import build_gcs_client, initialize_earth_engine
from glacier_monitor.ingestion.asf_bridge import (
    GeoTiffMetadata,
    create_hyp3_client,
    download_and_extract_job,
    download_asf_granule_geotiffs,
    fetch_succeeded_jobs,
    parse_geotiff_metadata,
    transfer_with_sar_asf_to_gee,
)
from glacier_monitor.ingestion.gee_uploader import (
    IngestionResult,
    build_earthengine_upload_command,
    build_gee_ingestion_manifest,
    ensure_image_collection,
    ingest_gcs_geotiff_to_gee,
    upload_geotiff_to_gcs,
)


@dataclass(frozen=True)
class Stage1IngestionReport:
    """Summary of all files staged to GCS and registered in Google Earth Engine."""

    collection_id: str
    dry_run: bool
    results: tuple[IngestionResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LocalStagedFile:
    """Normalized metadata record for a GeoTIFF staged locally without GCS."""

    local_path: Path
    asset_name: str
    sensor: str
    product_layer: str
    start_time_utc: str | None
    end_time_utc: str | None
    properties: dict[str, str | int | float] = field(default_factory=dict)

    @classmethod
    def from_metadata(cls, meta: GeoTiffMetadata) -> LocalStagedFile:
        return cls(
            local_path=meta.local_path,
            asset_name=meta.asset_name,
            sensor=meta.sensor,
            product_layer=meta.product_layer,
            start_time_utc=meta.start_time_utc,
            end_time_utc=meta.end_time_utc,
            properties=dict(meta.properties),
        )

    def to_json_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["local_path"] = str(self.local_path)
        return payload


@dataclass(frozen=True)
class LocalStagingReport:
    """Summary of GeoTIFF files downloaded and indexed locally without GCS."""

    download_dir: Path
    catalog_path: Path
    items: tuple[LocalStagedFile, ...] = field(default_factory=tuple)


def _require_storage_and_collection(
    settings: Settings,
    collection_override: str | None,
) -> tuple[str, str]:
    bucket = settings.gcs_bucket
    if not bucket:
        raise ValueError("GCS_BUCKET is required to upload GeoTIFFs to Cloud Storage.")

    collection_id = (collection_override or settings.resolved_asset_collection() or "").strip("/")
    if not collection_id:
        raise ValueError(
            "Set GEE_ASSET_COLLECTION or GEE_PROJECT_ID to determine the target collection."
        )
    return bucket, collection_id


def _build_gcs_object_path(prefix: str, job_id: str | None, filename: str) -> str:
    clean_prefix = prefix.strip("/")
    parts = [part for part in (clean_prefix, job_id, filename) if part]
    return "/".join(parts)


def ingest_local_geotiffs(
    geotiff_paths: Sequence[Path],
    settings: Settings,
    *,
    sensor: str | None = None,
    job_id: str | None = None,
    job_type: str | None = None,
    collection_id: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
    allow_overwrite: bool = False,
    wait: bool = False,
    dry_run: bool = False,
) -> Stage1IngestionReport:
    """Upload local NASA/ASF GeoTIFF files (NISAR, UAVSAR, HyP3) to GCS and GEE."""
    if not geotiff_paths:
        raise ValueError("You must provide at least one GeoTIFF file (.tif/.tiff).")

    bucket_name, target_collection = _require_storage_and_collection(
        settings, collection_id
    )

    project_id = settings.gee_project_id
    gcs_client = None
    if not dry_run:
        project_id = initialize_earth_engine(settings)
        target_collection = ensure_image_collection(target_collection)
        gcs_client = build_gcs_client(settings)

    results: list[IngestionResult] = []
    for path in geotiff_paths:
        meta = parse_geotiff_metadata(
            path,
            sensor=sensor,
            job_id=job_id,
            job_type=job_type,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
        )
        gcs_object = _build_gcs_object_path(settings.gcs_prefix, job_id, path.name)
        gcs_uri = f"gs://{bucket_name}/{gcs_object}"
        asset_id = f"{target_collection}/{meta.asset_name}"

        if dry_run:
            manifest = build_gee_ingestion_manifest(
                gcs_uri=gcs_uri,
                asset_id=asset_id,
                properties=meta.properties,
                start_time_utc=meta.start_time_utc,
                end_time_utc=meta.end_time_utc,
            )
            cli_cmd = build_earthengine_upload_command(
                gcs_uri=gcs_uri,
                asset_id=manifest["name"],
                start_time_utc=manifest.get("start_time"),
                end_time_utc=manifest.get("end_time"),
                pyramiding_policy=manifest.get("pyramidingPolicy"),
                allow_overwrite=allow_overwrite,
                wait=wait,
            )
            results.append(
                IngestionResult(
                    local_path=path,
                    gcs_uri=gcs_uri,
                    asset_id=manifest["name"],
                    task_id=None,
                    state="DRY_RUN",
                    cli_command=cli_cmd,
                    manifest=manifest,
                )
            )
            continue

        assert gcs_client is not None
        uploaded_uri = upload_geotiff_to_gcs(
            gcs_client,
            local_path=path,
            bucket_name=bucket_name,
            gcs_object_path=gcs_object,
        )
        result = ingest_gcs_geotiff_to_gee(
            gcs_uri=uploaded_uri,
            asset_id=asset_id,
            local_path=path,
            project_id=project_id,
            properties=meta.properties,
            start_time_utc=meta.start_time_utc,
            end_time_utc=meta.end_time_utc,
            allow_overwrite=allow_overwrite,
            wait=wait,
            timeout_sec=settings.gee_ingestion_timeout_sec,
        )
        results.append(result)

    return Stage1IngestionReport(
        collection_id=target_collection,
        dry_run=dry_run,
        results=tuple(results),
    )


def ingest_hyp3_jobs(
    settings: Settings,
    *,
    job_name: str | None = None,
    job_id: str | None = None,
    product_suffixes: Sequence[str] | None = None,
    collection_id: str | None = None,
    allow_overwrite: bool = False,
    wait: bool = False,
    dry_run: bool = False,
    use_community_bridge: bool = False,
) -> Stage1IngestionReport:
    """Download completed ASF HyP3 jobs and ingest their GeoTIFFs into GCS + GEE."""
    hyp3 = create_hyp3_client(settings)
    jobs = fetch_succeeded_jobs(hyp3, job_name=job_name, job_id=job_id)
    if not jobs:
        raise RuntimeError(
            "No completed HyP3 jobs (SUCCEEDED) were found matching the specified filters."
        )

    if use_community_bridge:
        if dry_run:
            raise ValueError("--dry-run mode does not apply with --use-community-bridge.")
        target_collection = (
            collection_id or settings.resolved_asset_collection() or ""
        ).strip("/")
        for job in jobs:
            transfer_with_sar_asf_to_gee(
                job,
                settings,
                collection_name=target_collection,
            )
        return Stage1IngestionReport(
            collection_id=target_collection,
            dry_run=False,
            results=(),
        )

    all_results: list[IngestionResult] = []
    final_collection = (
        collection_id or settings.resolved_asset_collection() or ""
    ).strip("/")

    for job in jobs:
        geotiffs = download_and_extract_job(
            job,
            output_dir=settings.local_download_dir,
            product_suffixes=product_suffixes,
        )
        if not geotiffs:
            continue
        job_report = ingest_local_geotiffs(
            geotiffs,
            settings,
            job_id=job.job_id,
            job_type=getattr(job, "job_type", None),
            collection_id=collection_id,
            allow_overwrite=allow_overwrite,
            wait=wait,
            dry_run=dry_run,
        )
        final_collection = job_report.collection_id
        all_results.extend(job_report.results)

    if not all_results:
        raise RuntimeError(
            "Downloaded HyP3 jobs contain no GeoTIFFs matching the filters (--suffix)."
        )

    return Stage1IngestionReport(
        collection_id=final_collection,
        dry_run=dry_run,
        results=tuple(all_results),
    )


def _write_local_catalog(download_dir: Path, new_items: Sequence[LocalStagedFile]) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = download_dir / "catalog.json"

    existing_by_path: dict[str, dict[str, object]] = {}
    if catalog_path.exists():
        try:
            loaded = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                for entry in loaded:
                    if isinstance(entry, dict) and entry.get("local_path"):
                        existing_by_path[str(entry["local_path"])] = entry
        except (OSError, json.JSONDecodeError):
            existing_by_path = {}

    for item in new_items:
        record = item.to_json_dict()
        existing_by_path[str(record["local_path"])] = record

    ordered = [existing_by_path[key] for key in sorted(existing_by_path)]
    catalog_path.write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return catalog_path


def _build_local_items(
    geotiff_paths: Sequence[Path],
    *,
    sensor: str | None = None,
    job_id: str | None = None,
    job_type: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
) -> list[LocalStagedFile]:
    staged: list[LocalStagedFile] = []
    for path in geotiff_paths:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Local GeoTIFF not found: {path}")
        if path.suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError(f"File '{path}' does not have a GeoTIFF extension (.tif/.tiff).")
        meta = parse_geotiff_metadata(
            path,
            sensor=sensor,
            job_id=job_id,
            job_type=job_type,
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
        )
        staged.append(LocalStagedFile.from_metadata(meta))
    return staged


def stage_local_geotiffs_catalog(
    geotiff_paths: Sequence[Path],
    settings: Settings,
    *,
    sensor: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
) -> LocalStagingReport:
    """Index existing local GeoTIFFs into the local Stage 1 catalog without GCS."""
    if not geotiff_paths:
        raise ValueError("You must provide at least one GeoTIFF file (.tif/.tiff).")
    items = _build_local_items(
        geotiff_paths,
        sensor=sensor,
        start_time_utc=start_time_utc,
        end_time_utc=end_time_utc,
    )
    catalog_path = _write_local_catalog(settings.local_download_dir, items)
    return LocalStagingReport(
        download_dir=settings.local_download_dir,
        catalog_path=catalog_path,
        items=tuple(items),
    )


def stage_hyp3_jobs_locally(
    settings: Settings,
    *,
    job_name: str | None = None,
    job_id: str | None = None,
    product_suffixes: Sequence[str] | None = None,
) -> LocalStagingReport:
    """Download completed ASF HyP3 jobs and index GeoTIFFs locally without GCS."""
    hyp3 = create_hyp3_client(settings)
    jobs = fetch_succeeded_jobs(hyp3, job_name=job_name, job_id=job_id)
    if not jobs:
        raise RuntimeError(
            "No completed HyP3 jobs (SUCCEEDED) were found matching the specified filters."
        )

    all_items: list[LocalStagedFile] = []
    for job in jobs:
        geotiffs = download_and_extract_job(
            job,
            output_dir=settings.local_download_dir,
            product_suffixes=product_suffixes,
        )
        if not geotiffs:
            continue
        job_items = _build_local_items(
            geotiffs,
            job_id=job.job_id,
            job_type=getattr(job, "job_type", None),
        )
        job_manifest_path = settings.local_download_dir / job.job_id / "local_manifest.json"
        job_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        job_manifest_path.write_text(
            json.dumps([item.to_json_dict() for item in job_items], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        all_items.extend(job_items)

    if not all_items:
        raise RuntimeError(
            "Downloaded HyP3 jobs contain no GeoTIFFs matching the filters (--suffix)."
        )

    catalog_path = _write_local_catalog(settings.local_download_dir, all_items)
    return LocalStagingReport(
        download_dir=settings.local_download_dir,
        catalog_path=catalog_path,
        items=tuple(all_items),
    )


def stage_asf_granules_locally(
    granule_names: Sequence[str],
    settings: Settings,
    *,
    product_suffixes: Sequence[str] | None = None,
    sensor: str | None = None,
) -> LocalStagingReport:
    """Download direct ASF GeoTIFF granules (e.g. OPERA-S1) and index locally without GCS."""
    if not granule_names:
        raise ValueError("You must specify at least one ASF granule name (--granule).")

    all_items: list[LocalStagedFile] = []
    for granule in granule_names:
        geotiffs = download_asf_granule_geotiffs(
            granule,
            output_dir=settings.local_download_dir,
            settings=settings,
            product_suffixes=product_suffixes,
        )
        if not geotiffs:
            continue
        all_items.extend(_build_local_items(geotiffs, sensor=sensor))

    if not all_items:
        raise RuntimeError(
            "No downloaded GeoTIFF files matched the filters (--suffix)."
        )

    catalog_path = _write_local_catalog(settings.local_download_dir, all_items)
    return LocalStagingReport(
        download_dir=settings.local_download_dir,
        catalog_path=catalog_path,
        items=tuple(all_items),
    )
