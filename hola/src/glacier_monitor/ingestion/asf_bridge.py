"""Stage 1B (ASF & HyP3): Search granules, run HyP3 jobs, and stage GeoTIFFs."""

from __future__ import annotations

import importlib
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

import asf_search as asf
from hyp3_sdk import HyP3, Job

from glacier_monitor.config import BoundingBox, Settings

ASF_DATASET_MAP: dict[str, str] = {
    "sentinel1": asf.DATASET.SENTINEL1,
    "nisar": asf.DATASET.NISAR,
    "nisar_science": asf.DATASET.NISAR_SCIENCE_PRODUCTS,
    "uavsar": asf.DATASET.UAVSAR,
    "opera_s1": asf.DATASET.OPERA_S1,
}

# Ordered longest-suffix-first so multi-part suffixes match before generic ones.
HYP3_PRODUCT_LAYERS: tuple[tuple[str, str], ...] = (
    ("_unw_phase.tif", "unwrapped_phase"),
    ("_wrapped_phase.tif", "wrapped_phase"),
    ("_los_disp.tif", "los_displacement"),
    ("_vert_disp.tif", "vertical_displacement"),
    ("_water_mask.tif", "water_mask"),
    ("_sc_area.tif", "scattering_area"),
    ("_inc_map.tif", "incidence_angle"),
    ("_los_theta.tif", "look_vector_theta"),
    ("_los_phi.tif", "look_vector_phi"),
    ("_coh.tif", "coherence"),
    ("_amp.tif", "amplitude"),
    ("_dem.tif", "dem"),
    ("_mask.tif", "layover_shadow_mask"),
    ("_rgb.tif", "rgb_decomposition"),
    ("_vv.tif", "backscatter_vv"),
    ("_vh.tif", "backscatter_vh"),
    ("_hh.tif", "backscatter_hh"),
    ("_hv.tif", "backscatter_hv"),
)

_DATETIME_PATTERN = re.compile(r"(20\d{2}|19\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})")
_DATE_PATTERN = re.compile(r"(20\d{2}|19\d{2})(\d{2})(\d{2})")
_SANITIZE_ASSET_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ASFGranuleSummary:
    """Normalized metadata summary for a granule returned by asf_search."""

    scene_name: str
    file_id: str
    platform: str | None
    processing_level: str | None
    start_time: str | None
    stop_time: str | None
    flight_direction: str | None
    polarization: str | None
    url: str | None


@dataclass(frozen=True)
class GeoTiffMetadata:
    """Normalized metadata extracted from a NASA/ASF/HyP3 GeoTIFF path."""

    local_path: Path
    asset_name: str
    sensor: str
    product_layer: str
    start_time_utc: str | None
    end_time_utc: str | None
    properties: dict[str, str | int | float] = field(default_factory=dict)


def resolve_asf_dataset(dataset: str) -> str:
    """Resolve user-friendly dataset key into an asf_search DATASET constant."""
    key = dataset.strip().lower()
    if key in ASF_DATASET_MAP:
        return ASF_DATASET_MAP[key]
    return dataset


def _normalize_optional_str(value: Any, *, joiner: str = "+") -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if item is not None and str(item).strip()]
        return joiner.join(parts) if parts else None
    text = str(value).strip()
    return text or None


def search_asf_granules(
    dataset: str,
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    processing_level: str | None = None,
    max_results: int = 20,
) -> list[ASFGranuleSummary]:
    """Search ASF catalog (Sentinel-1, NISAR, UAVSAR, etc.) over a bounding box."""
    resolved_dataset = resolve_asf_dataset(dataset)
    kwargs: dict[str, Any] = {
        "dataset": resolved_dataset,
        "intersectsWith": bbox.to_wkt(),
        "start": start_date,
        "end": end_date,
        "maxResults": max_results,
    }
    if processing_level:
        kwargs["processingLevel"] = processing_level

    results = asf.geo_search(**kwargs)
    summaries: list[ASFGranuleSummary] = []
    for product in results:
        props = getattr(product, "properties", {}) or {}
        scene_name = str(props.get("sceneName") or props.get("fileID") or "")
        file_id = str(props.get("fileID") or scene_name)
        summaries.append(
            ASFGranuleSummary(
                scene_name=scene_name,
                file_id=file_id,
                platform=_normalize_optional_str(props.get("platform"), joiner=", "),
                processing_level=_normalize_optional_str(props.get("processingLevel"), joiner=", "),
                start_time=_normalize_optional_str(props.get("startTime"), joiner=", "),
                stop_time=_normalize_optional_str(props.get("stopTime"), joiner=", "),
                flight_direction=_normalize_optional_str(props.get("flightDirection"), joiner=", "),
                polarization=_normalize_optional_str(props.get("polarization"), joiner="+"),
                url=_normalize_optional_str(props.get("url"), joiner=", "),
            )
        )
    return summaries


def create_hyp3_client(settings: Settings) -> HyP3:
    """Instantiate hyp3_sdk.HyP3 using token, username/password, or ~/.netrc."""
    if settings.earthdata_token:
        return HyP3(token=settings.earthdata_token)
    if settings.earthdata_username and settings.earthdata_password:
        return HyP3(
            username=settings.earthdata_username,
            password=settings.earthdata_password,
        )
    return HyP3()


def submit_hyp3_job(
    hyp3: HyP3,
    job_type: Literal["insar", "rtc"],
    granules: Sequence[str],
    job_name: str,
    *,
    include_displacement_maps: bool = True,
    include_inc_map: bool = True,
    rtc_resolution: Literal[10, 20, 30] = 30,
    rtc_scale: Literal["amplitude", "decibel", "power"] = "power",
):
    """Submit an InSAR or RTC processing job to ASF HyP3."""
    normalized_type = job_type.lower()
    if normalized_type == "rtc":
        if len(granules) != 1:
            raise ValueError(
                f"Un job RTC requiere exactamente 1 gránulo; recibidos {len(granules)}."
            )
        return hyp3.submit_rtc_job(
            granule=granules[0],
            name=job_name,
            resolution=rtc_resolution,
            scale=rtc_scale,
            include_inc_map=include_inc_map,
        )

    if normalized_type == "insar":
        if len(granules) != 2:
            raise ValueError(
                f"Un job InSAR requiere exactamente 2 gránulos (referencia y secundario); "
                f"recibidos {len(granules)}."
            )
        return hyp3.submit_insar_job(
            granule1=granules[0],
            granule2=granules[1],
            name=job_name,
            include_displacement_maps=include_displacement_maps,
            include_inc_map=include_inc_map,
        )

    raise ValueError(f"Tipo de job HyP3 no soportado: '{job_type}'. Usa 'insar' o 'rtc'.")


def list_hyp3_jobs(
    hyp3: HyP3,
    *,
    job_name: str | None = None,
    status_code: str | None = None,
) -> list[Job]:
    """List HyP3 jobs filtered optionally by job name or status code."""
    kwargs: dict[str, Any] = {}
    if job_name:
        kwargs["name"] = job_name
    if status_code:
        kwargs["status_code"] = status_code.upper()
    batch = hyp3.find_jobs(**kwargs)
    return list(batch.jobs)


def fetch_succeeded_jobs(
    hyp3: HyP3,
    *,
    job_name: str | None = None,
    job_id: str | None = None,
    exclude_expired: bool = True,
) -> list[Job]:
    """Fetch completed HyP3 jobs filtered by job_id or job_name."""
    if job_id:
        job = hyp3.get_job_by_id(job_id)
        if not job.succeeded():
            raise RuntimeError(
                f"El job HyP3 '{job_id}' no ha finalizado con éxito (estado: {job.status_code})."
            )
        if exclude_expired and job.expired():
            raise RuntimeError(f"Los archivos del job HyP3 '{job_id}' ya expiraron.")
        return [job]

    batch = hyp3.find_jobs(name=job_name, status_code="SUCCEEDED")
    jobs = list(batch.jobs)
    if exclude_expired:
        jobs = [job for job in jobs if not job.expired()]
    return jobs


def _is_geotiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"}


def _matches_suffixes(path: Path, product_suffixes: Sequence[str] | None) -> bool:
    if not product_suffixes:
        return True
    lower_name = path.name.lower()
    return any(lower_name.endswith(suffix.lower()) for suffix in product_suffixes)


def _extract_geotiffs_from_zip(zip_path: Path, target_dir: Path) -> list[Path]:
    extracted: list[Path] = []
    extract_root = target_dir / zip_path.stem
    extract_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            if not _is_geotiff(member_path):
                continue
            dest = (extract_root / member_path).resolve()
            if not dest.is_relative_to(extract_root.resolve()):
                raise ValueError(f"Ruta inválida dentro del ZIP: {member.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(dest)
    return extracted


def download_and_extract_job(
    job: Job,
    output_dir: Path,
    *,
    product_suffixes: Sequence[str] | None = None,
) -> list[Path]:
    """Download a completed HyP3 job and extract GeoTIFF files from any ZIP archives."""
    job_dir = output_dir / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths = job.download_files(location=job_dir, create=True)

    geotiffs: list[Path] = []
    for path in downloaded_paths:
        if path.suffix.lower() == ".zip":
            geotiffs.extend(_extract_geotiffs_from_zip(path, job_dir))
        elif _is_geotiff(path):
            geotiffs.append(path)

    return sorted(
        path for path in geotiffs if _matches_suffixes(path, product_suffixes)
    )


def create_asf_session(settings: Settings) -> asf.ASFSession:
    """Create an authenticated asf_search ASFSession from token or username/password."""
    session = asf.ASFSession()
    if settings.earthdata_token:
        return session.auth_with_token(settings.earthdata_token)
    if settings.earthdata_username and settings.earthdata_password:
        return session.auth_with_creds(
            settings.earthdata_username, settings.earthdata_password
        )
    return session


def _collect_asf_download_urls(
    product: Any,
    product_suffixes: Sequence[str] | None,
) -> list[str]:
    candidates: list[str] = []
    for file_type in (asf.FileDownloadType.DEFAULT_FILE, asf.FileDownloadType.ADDITIONAL_FILES):
        try:
            urls = product.get_urls(fileType=file_type)
        except Exception:
            urls = []
        for url in urls or ():
            if url not in candidates:
                candidates.append(url)

    if not candidates:
        fallback_url = (getattr(product, "properties", {}) or {}).get("url")
        if fallback_url:
            candidates.append(str(fallback_url))

    selected: list[str] = []
    for url in candidates:
        filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
        lower_name = filename.lower()
        if lower_name.endswith(".zip"):
            selected.append(url)
        elif lower_name.endswith((".tif", ".tiff")) and _matches_suffixes(
            Path(filename), product_suffixes
        ):
            selected.append(url)
    return selected


def download_asf_granule_geotiffs(
    granule_name: str,
    output_dir: Path,
    settings: Settings,
    *,
    product_suffixes: Sequence[str] | None = None,
) -> list[Path]:
    """Download GeoTIFFs (direct or zipped) for an ASF granule without using GCS."""
    results = asf.granule_search([granule_name])
    if not results:
        raise RuntimeError(f"No se encontró el gránulo '{granule_name}' en el catálogo ASF.")

    product = results[0]
    urls = _collect_asf_download_urls(product, product_suffixes)
    if not urls:
        raise RuntimeError(
            f"El gránulo '{granule_name}' no tiene URLs GeoTIFF/ZIP descargables directamente."
        )

    session = create_asf_session(settings)
    granule_dir = output_dir / "asf" / sanitize_asset_id_segment(granule_name)
    granule_dir.mkdir(parents=True, exist_ok=True)

    geotiffs: list[Path] = []
    for url in urls:
        filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
        target_path = granule_dir / filename
        asf.download_url(
            url=url,
            path=str(granule_dir),
            filename=filename,
            session=session,
        )
        if target_path.suffix.lower() == ".zip":
            geotiffs.extend(_extract_geotiffs_from_zip(target_path, granule_dir))
        elif _is_geotiff(target_path):
            geotiffs.append(target_path)

    return sorted(
        path for path in geotiffs if _matches_suffixes(path, product_suffixes)
    )


def _detect_sensor(filename_upper: str, explicit_sensor: str | None) -> str:
    if explicit_sensor:
        return explicit_sensor.upper()
    if filename_upper.startswith("NISAR"):
        return "NISAR"
    if filename_upper.startswith("OPERA"):
        return "OPERA-S1"
    if filename_upper.startswith("UAVSAR") or "_L090" in filename_upper:
        return "UAVSAR"
    if filename_upper.startswith("S1"):
        return "SENTINEL-1"
    return "ASF-SAR"


def _detect_product_layer(filename_lower: str) -> str:
    for suffix, layer in HYP3_PRODUCT_LAYERS:
        if filename_lower.endswith(suffix):
            return layer
    return "sar_geotiff"


def _extract_timestamps_utc(stem: str) -> tuple[str | None, str | None]:
    dt_matches = _DATETIME_PATTERN.findall(stem)
    parsed_dts: list[datetime] = []
    for y, m, d, hh, mm, ss in dt_matches:
        try:
            parsed_dts.append(
                datetime(
                    int(y), int(m), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc
                )
            )
        except ValueError:
            continue
    if parsed_dts:
        parsed_dts.sort()
        return (
            parsed_dts[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
            parsed_dts[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    date_matches = _DATE_PATTERN.findall(stem)
    parsed_dates: list[datetime] = []
    for y, m, d in date_matches:
        try:
            parsed_dates.append(datetime(int(y), int(m), int(d), tzinfo=timezone.utc))
        except ValueError:
            continue
    if parsed_dates:
        parsed_dates.sort()
        return (
            parsed_dates[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
            parsed_dates[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    return None, None


def sanitize_asset_id_segment(name: str) -> str:
    """Convert an arbitrary filename stem into a valid Earth Engine asset ID segment."""
    cleaned = _SANITIZE_ASSET_RE.sub("_", name).strip("_")
    return cleaned or "sar_asset"


def parse_geotiff_metadata(
    path: Path,
    *,
    sensor: str | None = None,
    job_id: str | None = None,
    job_type: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
) -> GeoTiffMetadata:
    """Extract normalized GEE asset name, timestamps, and properties from a GeoTIFF path."""
    detected_sensor = _detect_sensor(path.name.upper(), sensor)
    product_layer = _detect_product_layer(path.name.lower())
    inferred_start, inferred_end = _extract_timestamps_utc(path.stem)
    resolved_start = start_time_utc or inferred_start
    resolved_end = end_time_utc or inferred_end

    properties: dict[str, str | int | float] = {
        "source": "ASF_BRIDGE",
        "sensor": detected_sensor,
        "product_layer": product_layer,
        "source_filename": path.name,
    }
    if job_id:
        properties["hyp3_job_id"] = job_id
    if job_type:
        properties["hyp3_job_type"] = job_type

    return GeoTiffMetadata(
        local_path=path,
        asset_name=sanitize_asset_id_segment(path.stem),
        sensor=detected_sensor,
        product_layer=product_layer,
        start_time_utc=resolved_start,
        end_time_utc=resolved_end,
        properties=properties,
    )


def transfer_with_sar_asf_to_gee(
    job: Job,
    settings: Settings,
    *,
    collection_name: str | None = None,
) -> Any:
    """Delegate HyP3 -> GCS -> GEE transfer to gee-community/sar-asf-to-gee if installed."""
    if not settings.gcs_bucket:
        raise ValueError("GCS_BUCKET es obligatorio para usar sar-asf-to-gee.")
    if not settings.gee_project_id:
        raise ValueError("GEE_PROJECT_ID es obligatorio para usar sar-asf-to-gee.")

    try:
        asf_hyp3 = importlib.import_module("sar_asf_to_gee.asf_hyp3")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "El paquete opcional 'sar-asf-to-gee' no está instalado. "
            "Instálalo con: pip install git+https://github.com/gee-community/sar-asf-to-gee.git"
        ) from exc

    target_collection = collection_name or settings.resolved_asset_collection()
    transfer = asf_hyp3.Transfer(
        job_dict=job.to_dict(),
        gcs_bucket=settings.gcs_bucket,
        gee_gcp_project=settings.gee_project_id,
        gee_image_collection=target_collection,
        local_storage=str(settings.local_download_dir),
    )
    transfer.to_local()
    transfer.to_gcs()
    transfer.create_gee_asset()
    return transfer
