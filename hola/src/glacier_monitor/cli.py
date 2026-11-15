"""Command-line interface for Stage 1 (GEE native catalog + ASF bridge)."""

from __future__ import annotations

import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import click
import ee
from hyp3_sdk.exceptions import AuthenticationError, HyP3SDKError
from rich.console import Console
from rich.table import Table

from glacier_monitor.config import AOI_PRESETS, PROJECT_ROOT, Settings, resolve_aoi
from glacier_monitor.gee_auth import (
    build_gcs_client,
    initialize_earth_engine,
    resolve_service_account_path,
)
from glacier_monitor.ingestion.asf_bridge import (
    create_hyp3_client,
    list_hyp3_jobs,
    search_asf_granules,
    submit_hyp3_job,
)
from glacier_monitor.ingestion.native_catalog import (
    NATIVE_CATALOG,
    inspect_all_native_datasets,
    inspect_collection_status,
)
from glacier_monitor.ingestion.pipeline import (
    LocalStagingReport,
    ingest_hyp3_jobs,
    ingest_local_geotiffs,
    stage_asf_granules_locally,
    stage_hyp3_jobs_locally,
    stage_local_geotiffs_catalog,
)

console = Console()


def _format_actionable_error(exc: Exception) -> str:
    msg = str(exc)
    if isinstance(exc, FileNotFoundError) and "Service Account" in msg:
        return (
            f"{msg}\n"
            "Fix: download your Service Account JSON key from Google Cloud Console and "
            f"save it to '{PROJECT_ROOT / 'service_account.json'}' (or update GEE_PRIVATE_KEY_PATH "
            "in .env; you can also leave GEE_PRIVATE_KEY_PATH empty to use local credentials)."
        )
    if isinstance(exc, ee.EEException):
        lower_msg = msg.lower()
        if "authorize access to your earth engine" in lower_msg or "earthengine authenticate" in lower_msg:
            return (
                "Local Google Earth Engine credentials are not configured.\n"
                "Fix: run 'py -m ee.cli.eecli authenticate' in your terminal and sign in "
                "with your Google account authorized for project GEE_PROJECT_ID."
            )
        if "denied" in lower_msg or "permission" in lower_msg:
            return (
                f"{msg}\n"
                "Fix: verify in Google Cloud Console (IAM) that your active account "
                "(OAuth user or Service Account) has access to project GEE_PROJECT_ID "
                "and that the Google Earth Engine API is enabled."
            )
    if isinstance(exc, AuthenticationError):
        if "Study Area" in msg or "profile" in msg.lower():
            return (
                f"NASA Earthdata/HyP3 authentication rejected: {msg}\n"
                "Fix: sign in at https://urs.earthdata.nasa.gov/profile with your account, "
                "complete required profile fields (such as 'Study Area') and authorize ASF/HyP3 apps, "
                "or set a valid EARTHDATA_TOKEN in .env."
            )
        return (
            f"NASA Earthdata/HyP3 authentication error: {msg}\n"
            "Check EARTHDATA_TOKEN or EARTHDATA_USERNAME / EARTHDATA_PASSWORD in .env."
        )
    return msg


@contextmanager
def _cli_error_boundary() -> Iterator[None]:
    try:
        yield
    except click.ClickException:
        raise
    except (
        FileNotFoundError,
        AuthenticationError,
        HyP3SDKError,
        ee.EEException,
        ValueError,
        RuntimeError,
    ) as exc:
        raise click.ClickException(_format_actionable_error(exc)) from exc


def _print_local_staging_report(report: LocalStagingReport) -> None:
    console.print(
        f"[bold]Local storage:[/bold] [cyan]{report.download_dir}[/cyan]\n"
        f"[bold]Catalog JSON:[/bold] [cyan]{report.catalog_path}[/cyan]"
    )
    for item in report.items:
        console.print(
            f"- File: {item.local_path}\n"
            f"  Sensor: {item.sensor} | Layer: {item.product_layer}\n"
            f"  UTC window: {item.start_time_utc or '-'} -> {item.end_time_utc or '-'}"
        )


@click.group()
def main() -> None:
    """Stage 1: Google Earth Engine ingestion and local download (Native catalog + ASF/HyP3)."""


@main.command("list-aois")
def list_aois() -> None:
    """List preconfigured glacier/valley AOIs."""
    table = Table(title="Preconfigured Glacier AOIs")
    table.add_column("Key", style="cyan", overflow="fold")
    table.add_column("Name", overflow="fold")
    table.add_column("Country", overflow="fold")
    table.add_column("Bounding Box [W, S, E, N]", overflow="fold")
    for preset in AOI_PRESETS.values():
        table.add_row(
            preset.key,
            preset.name,
            preset.country,
            str(preset.bbox.to_ee_bounds()),
        )
    console.print(table)


@main.command("doctor")
def doctor() -> None:
    """Verify credentials and connectivity with GEE, GCS (optional), NASA ASF, and HyP3."""
    settings = Settings()
    table = Table(title="Stage 1 Diagnostics (GEE + Local/GCS Mode + NASA ASF/HyP3)")
    table.add_column("Component", style="cyan", overflow="fold")
    table.add_column("Status", overflow="fold")
    table.add_column("Detail", overflow="fold")

    has_error = False

    # 1. Config & auth mode
    key_missing = False
    key_info = "OAuth / local credentials (`py -m ee.cli.eecli authenticate`)"
    if settings.gee_private_key_path is not None:
        resolved_key = resolve_service_account_path(settings.gee_private_key_path)
        if resolved_key.exists():
            key_info = f"Service Account JSON: {resolved_key}"
        else:
            key_missing = True
            has_error = True
            key_info = (
                f"Missing file: {resolved_key}. Place your JSON key there or leave "
                "GEE_PRIVATE_KEY_PATH empty in .env."
            )

    bucket_label = settings.gcs_bucket or "(local mode without bucket)"
    config_ok = bool(settings.gee_project_id) and not key_missing
    if not settings.gee_project_id:
        has_error = True
    table.add_row(
        "Config (.env)",
        "[green]OK[/green]" if config_ok else "[red]ERROR[/red]",
        f"Project: {settings.gee_project_id or '-'} | Bucket: {bucket_label} | {key_info}",
    )

    # 2. Google Earth Engine
    try:
        project = initialize_earth_engine(settings)
        probe = ee.Number(1).getInfo()
        col_id = settings.resolved_asset_collection()
        col_status = "no collection"
        if col_id and settings.gcs_bucket:
            try:
                ee.data.getAsset(col_id)
                col_status = f"collection OK ({col_id})"
            except ee.EEException:
                col_status = f"collection will be created on ingest ({col_id})"
        elif not settings.gcs_bucket:
            col_status = "native catalog active"
        table.add_row(
            "Google Earth Engine",
            "[green]OK[/green]",
            f"Project '{project}' active (probe={probe}, {col_status})",
        )
    except Exception as exc:
        has_error = True
        table.add_row("Google Earth Engine", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 3. Google Cloud Storage (optional in local mode)
    if not settings.gcs_bucket:
        table.add_row(
            "Google Cloud Storage",
            "[cyan]LOCAL[/cyan]",
            "Skipped: local mode without bucket (GCS_BUCKET empty; use `hyp3-download` / `asf-download`).",
        )
    else:
        try:
            gcs_client = build_gcs_client(settings)
            bucket_exists = gcs_client.bucket(settings.gcs_bucket).exists()
            if bucket_exists:
                table.add_row(
                    "Google Cloud Storage",
                    "[green]OK[/green]",
                    f"Bucket reachable: gs://{settings.gcs_bucket}",
                )
            else:
                has_error = True
                table.add_row(
                    "Google Cloud Storage",
                    "[red]ERROR[/red]",
                    f"Bucket 'gs://{settings.gcs_bucket}' does not exist or is not accessible.",
                )
        except Exception as exc:
            has_error = True
            table.add_row("Google Cloud Storage", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 4. NASA Earthdata / ASF HyP3
    try:
        hyp3 = create_hyp3_client(settings)
        info = hyp3.my_info()
        user_id = info.get("user_id", "unknown")
        credits_left = info.get("remaining_credits", "?")
        table.add_row(
            "NASA HyP3 / Earthdata",
            "[green]OK[/green]",
            f"User: {user_id} | Remaining credits: {credits_left}",
        )
    except Exception as exc:
        has_error = True
        table.add_row("NASA HyP3 / Earthdata", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 5. NASA ASF Catalog search
    try:
        bbox = AOI_PRESETS["langtang"].bbox
        sample = search_asf_granules("sentinel1", bbox, "2026-09-01", "2026-10-15", max_results=1)
        table.add_row(
            "NASA ASF Catalog",
            "[green]OK[/green]",
            f"Connected (Langtang sample: {sample[0].scene_name if sample else '0 results'})",
        )
    except Exception as exc:
        has_error = True
        table.add_row("NASA ASF Catalog", "[red]ERROR[/red]", str(exc))

    console.print(table)
    if has_error:
        raise click.ClickException("There are pending configuration checks.")


@main.command("check-native")
@click.option(
    "--aoi",
    default="langtang",
    show_default=True,
    help="Preset ('langtang', 'khumbu') or 'min_lon,min_lat,max_lon,max_lat'.",
)
@click.option("--start", "start_date", required=True, help="Start date YYYY-MM-DD.")
@click.option("--end", "end_date", required=True, help="End date YYYY-MM-DD.")
@click.option(
    "--dataset",
    type=click.Choice(["all", *NATIVE_CATALOG.keys()]),
    default="all",
    show_default=True,
    help="Native dataset to inspect in GEE.",
)
@click.option(
    "--orbit-pass",
    type=click.Choice(["ASCENDING", "DESCENDING"], case_sensitive=False),
    default=None,
    help="Optional orbit pass filter for Sentinel-1 GRD.",
)
def check_native(
    aoi: str,
    start_date: str,
    end_date: str,
    dataset: str,
    orbit_pass: str | None,
) -> None:
    """Check availability and latest timestamp of Sentinel-1 GRD and MODIS in GEE."""
    with _cli_error_boundary():
        settings = Settings()
        project = initialize_earth_engine(settings)
        aoi_name, bbox = resolve_aoi(aoi)
        console.print(f"[green]GEE initialized[/green] (project: {project}) | AOI: {aoi_name}")

        if dataset == "all":
            statuses = inspect_all_native_datasets(
                bbox, start_date, end_date, s1_orbit_pass=orbit_pass
            )
        else:
            statuses = [
                inspect_collection_status(
                    dataset, bbox, start_date, end_date, s1_orbit_pass=orbit_pass
                )
            ]

    table = Table(title=f"Native GEE Catalog ({start_date} to {end_date})")
    table.add_column("Dataset ID", style="cyan", overflow="fold")
    table.add_column("Images", justify="right", overflow="fold")
    table.add_column("Earliest (UTC)", overflow="fold")
    table.add_column("Latest (UTC)", overflow="fold")
    table.add_column("Available bands", overflow="fold")

    for item in statuses:
        table.add_row(
            item.dataset_id,
            str(item.image_count),
            item.earliest_time_utc or "-",
            item.latest_time_utc or "-",
            ", ".join(item.available_bands[:6])
            + (" ..." if len(item.available_bands) > 6 else ""),
        )
    console.print(table)


@main.command("asf-search")
@click.option(
    "--dataset",
    default="sentinel1",
    show_default=True,
    help="ASF dataset: sentinel1, nisar, nisar_science, uavsar, opera_s1, or ASF dataset name.",
)
@click.option("--aoi", default="langtang", show_default=True)
@click.option("--start", "start_date", required=True, help="Start date ISO/YYYY-MM-DD.")
@click.option("--end", "end_date", required=True, help="End date ISO/YYYY-MM-DD.")
@click.option("--processing-level", default=None, help="E.g. SLC, GRD_HD, RTC.")
@click.option("--max-results", default=15, show_default=True, type=int)
def asf_search_cmd(
    dataset: str,
    aoi: str,
    start_date: str,
    end_date: str,
    processing_level: str | None,
    max_results: int,
) -> None:
    """Search granules in the NASA ASF catalog (Sentinel-1, NISAR, UAVSAR)."""
    with _cli_error_boundary():
        aoi_name, bbox = resolve_aoi(aoi)
        granules = search_asf_granules(
            dataset=dataset,
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
            processing_level=processing_level,
            max_results=max_results,
        )

    table = Table(title=f"ASF Results ({dataset}) - {aoi_name}")
    table.add_column("Scene / Granule", style="cyan", overflow="fold")
    table.add_column("Platform", overflow="fold")
    table.add_column("Level", overflow="fold")
    table.add_column("Start (UTC)", overflow="fold")
    table.add_column("Orbit", overflow="fold")
    table.add_column("Pol", overflow="fold")

    for g in granules:
        table.add_row(
            g.scene_name,
            g.platform or "-",
            g.processing_level or "-",
            g.start_time or "-",
            g.flight_direction or "-",
            g.polarization or "-",
        )
    console.print(table)
    if not granules:
        console.print("[yellow]No granules found matching the specified filters.[/yellow]")
    else:
        console.print("[bold]Granules (copyable):[/bold]")
        for index, g in enumerate(granules, start=1):
            click.echo(f"  {index}. {g.scene_name}")


@main.command("asf-download")
@click.option(
    "--granule",
    "granules",
    multiple=True,
    required=True,
    help="Exact ASF granule name (e.g. OPERA_L2_RTC-S1_...) to download locally.",
)
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filter GeoTIFFs by filename suffix (e.g. _VV.tif, _VH.tif).",
)
@click.option("--sensor", default=None, help="Optional sensor label.")
def asf_download_cmd(
    granules: tuple[str, ...],
    suffixes: tuple[str, ...],
    sensor: str | None,
) -> None:
    """Download direct NASA ASF GeoTIFFs (e.g. OPERA-S1) locally without GCS."""
    with _cli_error_boundary():
        settings = Settings()
        report = stage_asf_granules_locally(
            list(granules),
            settings,
            product_suffixes=list(suffixes) if suffixes else None,
            sensor=sensor,
        )
    _print_local_staging_report(report)


@main.command("hyp3-submit")
@click.option(
    "--job-type",
    type=click.Choice(["insar", "rtc"], case_sensitive=False),
    required=True,
)
@click.option("--name", "job_name", required=True, help="Name to identify the HyP3 job.")
@click.option(
    "--granule",
    "granules",
    multiple=True,
    required=True,
    help="ASF granule(s). 1 for rtc, 2 for insar.",
)
def hyp3_submit_cmd(job_type: str, job_name: str, granules: tuple[str, ...]) -> None:
    """Submit an RTC or InSAR job to the ASF HyP3 service."""
    with _cli_error_boundary():
        settings = Settings()
        hyp3 = create_hyp3_client(settings)
        batch = submit_hyp3_job(
            hyp3=hyp3,
            job_type=job_type.lower(),  # type: ignore[arg-type]
            granules=list(granules),
            job_name=job_name,
        )
    for job in batch.jobs:
        console.print(
            f"[green]Job submitted[/green] ID: [cyan]{job.job_id}[/cyan] | "
            f"Name: {job.name} | Status: {job.status_code}"
        )


@main.command("hyp3-status")
@click.option("--name", "job_name", default=None, help="Filter by HyP3 job name.")
@click.option(
    "--status",
    type=click.Choice(["PENDING", "RUNNING", "SUCCEEDED", "FAILED"], case_sensitive=False),
    default=None,
    help="Filter by job status.",
)
@click.option("--limit", default=20, show_default=True, type=int)
def hyp3_status_cmd(job_name: str | None, status: str | None, limit: int) -> None:
    """List the status of your jobs in ASF HyP3."""
    with _cli_error_boundary():
        settings = Settings()
        hyp3 = create_hyp3_client(settings)
        jobs = list_hyp3_jobs(hyp3, job_name=job_name, status_code=status)[:limit]

    table = Table(title="ASF HyP3 Jobs")
    table.add_column("Job ID", style="cyan", overflow="fold")
    table.add_column("Name", overflow="fold")
    table.add_column("Type", overflow="fold")
    table.add_column("Status", overflow="fold")
    table.add_column("Expired", overflow="fold")
    table.add_column("Files", justify="right", overflow="fold")

    for job in jobs:
        expired_str = "Yes" if job.expired() else "No"
        file_count = str(len(job.files or ()))
        table.add_row(
            job.job_id,
            job.name or "-",
            job.job_type,
            job.status_code,
            expired_str,
            file_count,
        )
    console.print(table)
    if not jobs:
        console.print("[yellow]No HyP3 jobs match the filters.[/yellow]")


@main.command("hyp3-download")
@click.option("--job-id", default=None, help="ID of a completed ASF HyP3 job.")
@click.option("--job-name", default=None, help="Name of completed ASF HyP3 jobs.")
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filter HyP3 GeoTIFFs by suffix (e.g. _unw_phase.tif, _coh.tif, _vv.tif).",
)
def hyp3_download_cmd(
    job_id: str | None,
    job_name: str | None,
    suffixes: tuple[str, ...],
) -> None:
    """Download completed ASF HyP3 jobs locally and build a JSON catalog (without GCS)."""
    if not job_id and not job_name:
        raise click.UsageError("Specify '--job-id' or '--job-name'.")

    with _cli_error_boundary():
        settings = Settings()
        report = stage_hyp3_jobs_locally(
            settings,
            job_id=job_id,
            job_name=job_name,
            product_suffixes=list(suffixes) if suffixes else None,
        )
    _print_local_staging_report(report)


@main.command("asf-ingest")
@click.option("--job-id", default=None, help="ID of a completed ASF HyP3 job.")
@click.option("--job-name", default=None, help="Name of completed ASF HyP3 jobs.")
@click.option(
    "--local-file",
    "local_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Already downloaded local GeoTIFF(s) (e.g. NISAR, UAVSAR, or HyP3).",
)
@click.option("--sensor", default=None, help="Sensor label (NISAR, UAVSAR, SENTINEL-1).")
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filter HyP3 GeoTIFFs by suffix (e.g. _unw_phase.tif, _coh.tif, _vv.tif).",
)
@click.option("--collection", default=None, help="Target GEE ImageCollection Asset ID.")
@click.option("--start-time", default=None, help="Optional ISO UTC start timestamp for local files.")
@click.option("--end-time", default=None, help="Optional ISO UTC end timestamp.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing asset in GEE.")
@click.option("--wait", is_flag=True, help="Wait for the GEE ingestion task to finish.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Generate manifests and `earthengine upload image` commands without uploading or ingesting.",
)
@click.option(
    "--local-only",
    is_flag=True,
    help="Download and index into the local catalog (data/downloads/catalog.json) without GCS or GEE.",
)
@click.option(
    "--use-community-bridge",
    is_flag=True,
    help="Delegate HyP3 jobs to the gee-community/sar-asf-to-gee package.",
)
def asf_ingest_cmd(
    job_id: str | None,
    job_name: str | None,
    local_files: tuple[Path, ...],
    sensor: str | None,
    suffixes: tuple[str, ...],
    collection: str | None,
    start_time: str | None,
    end_time: str | None,
    overwrite: bool,
    wait: bool,
    dry_run: bool,
    local_only: bool,
    use_community_bridge: bool,
) -> None:
    """Upload GeoTIFFs to GCS+GEE, or stage them locally with `--local-only`."""
    if bool(local_files) == bool(job_id or job_name):
        raise click.UsageError(
            "Specify '--local-file' OR '--job-id' / '--job-name', not both."
        )

    with _cli_error_boundary():
        settings = Settings()
        if local_only:
            if local_files:
                local_report = stage_local_geotiffs_catalog(
                    list(local_files),
                    settings,
                    sensor=sensor,
                    start_time_utc=start_time,
                    end_time_utc=end_time,
                )
            else:
                local_report = stage_hyp3_jobs_locally(
                    settings,
                    job_id=job_id,
                    job_name=job_name,
                    product_suffixes=list(suffixes) if suffixes else None,
                )
            _print_local_staging_report(local_report)
            return

        if local_files:
            report = ingest_local_geotiffs(
                list(local_files),
                settings,
                sensor=sensor,
                collection_id=collection,
                start_time_utc=start_time,
                end_time_utc=end_time,
                allow_overwrite=overwrite,
                wait=wait,
                dry_run=dry_run,
            )
        else:
            report = ingest_hyp3_jobs(
                settings,
                job_id=job_id,
                job_name=job_name,
                product_suffixes=list(suffixes) if suffixes else None,
                collection_id=collection,
                allow_overwrite=overwrite,
                wait=wait,
                dry_run=dry_run,
                use_community_bridge=use_community_bridge,
            )

    console.print(
        f"[bold]Target collection:[/bold] [cyan]{report.collection_id}[/cyan] "
        f"(dry_run={report.dry_run})"
    )
    for res in report.results:
        console.print(
            f"- File: {res.local_path.name}\n"
            f"  GCS URI: {res.gcs_uri}\n"
            f"  Asset ID: {res.asset_id}\n"
            f"  Task ID: {res.task_id or '-'} | Status: {res.state}\n"
            f"  CLI eq.: {' '.join(shlex.quote(arg) for arg in res.cli_command)}"
        )


@main.command("publish-madness")
@click.option(
    "--aoi",
    "aois",
    multiple=True,
    help="Glacier preset ('langtang', 'khumbu') or bbox; publishes langtang and khumbu by default.",
)
@click.option("--pre-start", default="2026-01-01", show_default=True, help="Before window start (YYYY-MM-DD).")
@click.option("--pre-end", default="2026-02-28", show_default=True, help="Before window end (YYYY-MM-DD).")
@click.option("--post-start", default="2026-05-15", show_default=True, help="After window start (YYYY-MM-DD).")
@click.option("--post-end", default="2026-08-31", show_default=True, help="After window end (YYYY-MM-DD).")
@click.option("--max-dim", default=420, show_default=True, type=int, help="Maximum side dimension in pixels.")
@click.option(
    "--target-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Destination sar-Madness public/ directory.",
)
def publish_madness_cmd(
    aois: tuple[str, ...],
    pre_start: str,
    pre_end: str,
    post_start: str,
    post_end: str,
    max_dim: int,
    target_dir: Path | None,
) -> None:
    """Export real glacier melt data (GeoTIFFs + Before/After PNGs) to sar-Madness."""
    from glacier_monitor.ingestion.platform_exporter import export_glacier_case_to_madness

    with _cli_error_boundary():
        settings = Settings()
        selected = list(aois) if aois else list(AOI_PRESETS.keys())
        for aoi_spec in selected:
            with console.status(f"[bold cyan]Downloading real GEE data and exporting {aoi_spec}...[/bold cyan]"):
                res = export_glacier_case_to_madness(
                    aoi_spec,
                    settings=settings,
                    pre_start=pre_start,
                    pre_end=pre_end,
                    post_start=post_start,
                    post_end=post_end,
                    max_dim=max_dim,
                    target_public_dir=target_dir,
                )
            console.print(
                f"[green]Published {res.case_id}[/green] | GeoTIFFs={res.geotiff_count} | "
                f"PNGs={res.png_count} | Snow {res.snow_pre_pct}% -> {res.snow_post_pct}% | "
                f"Temp {res.lst_pre_c}°C -> {res.lst_post_c}°C | Melt={res.melt_area_km2} km² | "
                f"SAR wet snow={res.sar_wet_snow_km2} km²"
            )


@main.command("sar-wetsnow")
@click.option(
    "--case",
    "case_ids",
    multiple=True,
    help="Published glacier case id (e.g. glacier-khumbu); processes every published glacier preset by default.",
)
@click.option(
    "--threshold-db",
    default=-2.0,
    show_default=True,
    type=float,
    help="Backscatter-ratio threshold in dB (negative); -2 dB for Sentinel-1.",
)
@click.option(
    "--target-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="sar-Madness public/ directory containing data/cases/.",
)
def sar_wetsnow_cmd(case_ids: tuple[str, ...], threshold_db: float, target_dir: Path | None) -> None:
    """Derive the SAR wet-snow layer from already published glacier cases (no GEE required)."""
    from glacier_monitor.ingestion.platform_exporter import GLACIER_CASE_META
    from glacier_monitor.ingestion.sar_wetsnow import default_public_dir, publish_sar_wet_snow_for_case

    with _cli_error_boundary():
        public_dir = (target_dir or default_public_dir()).resolve()
        selected = list(case_ids) if case_ids else [meta.case_id for meta in GLACIER_CASE_META.values()]
        for case_id in selected:
            res = publish_sar_wet_snow_for_case(case_id, public_dir, threshold_db=threshold_db)
            s = res.stats
            console.print(
                f"[green]SAR wet snow {res.case_id}[/green] | threshold {res.threshold_db:g} dB | "
                f"wet={s['wetSnowKm2']} km² ({s['wetSnowPct']}% of {s['snowReferenceKm2']} km² snow) | "
                f"possible={s['possibleWetKm2']} km² | GeoTIFFs={res.geotiff_count}"
            )


if __name__ == "__main__":
    main()
