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
            "Solución: descarga la clave JSON de tu Service Account en Google Cloud Console y "
            f"guárdala en '{PROJECT_ROOT / 'service_account.json'}' (o actualiza GEE_PRIVATE_KEY_PATH "
            "en .env; también puedes dejar GEE_PRIVATE_KEY_PATH vacío para usar credenciales locales)."
        )
    if isinstance(exc, ee.EEException):
        lower_msg = msg.lower()
        if "authorize access to your earth engine" in lower_msg or "earthengine authenticate" in lower_msg:
            return (
                "Credenciales de Google Earth Engine no configuradas en local.\n"
                "Solución: ejecuta 'py -m ee.cli.eecli authenticate' en tu terminal e inicia "
                "sesión con tu cuenta de Google autorizada en el proyecto GEE_PROJECT_ID."
            )
        if "denied" in lower_msg or "permission" in lower_msg:
            return (
                f"{msg}\n"
                "Solución: verifica en Google Cloud Console (IAM) que tu cuenta activa "
                "(usuario OAuth o Service Account) tenga acceso al proyecto GEE_PROJECT_ID "
                "y que Google Earth Engine API esté habilitada."
            )
    if isinstance(exc, AuthenticationError):
        if "Study Area" in msg or "profile" in msg.lower():
            return (
                f"Autenticación NASA Earthdata/HyP3 rechazada: {msg}\n"
                "Solución: entra a https://urs.earthdata.nasa.gov/profile con tu usuario, "
                "completa los campos obligatorios (como 'Study Area') y autoriza las aplicaciones "
                "de ASF/HyP3, o configura un EARTHDATA_TOKEN válido en .env."
            )
        return (
            f"Error de autenticación NASA Earthdata/HyP3: {msg}\n"
            "Revisa EARTHDATA_TOKEN o EARTHDATA_USERNAME / EARTHDATA_PASSWORD en .env."
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
        f"[bold]Almacenamiento local:[/bold] [cyan]{report.download_dir}[/cyan]\n"
        f"[bold]Catálogo JSON:[/bold] [cyan]{report.catalog_path}[/cyan]"
    )
    for item in report.items:
        console.print(
            f"- Archivo: {item.local_path}\n"
            f"  Sensor: {item.sensor} | Capa: {item.product_layer}\n"
            f"  Ventana UTC: {item.start_time_utc or '-'} -> {item.end_time_utc or '-'}"
        )


@click.group()
def main() -> None:
    """Etapa 1: Ingesta en Google Earth Engine y descarga local (Catálogo nativo + ASF/HyP3)."""


@main.command("list-aois")
def list_aois() -> None:
    """Lista los glaciares/valles preconfigurados."""
    table = Table(title="AOIs de Glaciares Preconfigurados")
    table.add_column("Clave", style="cyan", overflow="fold")
    table.add_column("Nombre", overflow="fold")
    table.add_column("País", overflow="fold")
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
    """Verifica credenciales y conectividad con GEE, GCS (opcional), NASA ASF y HyP3."""
    settings = Settings()
    table = Table(title="Diagnóstico Etapa 1 (GEE + Modo Local/GCS + NASA ASF/HyP3)")
    table.add_column("Componente", style="cyan", overflow="fold")
    table.add_column("Estado", overflow="fold")
    table.add_column("Detalle", overflow="fold")

    has_error = False

    # 1. Config & auth mode
    key_missing = False
    key_info = "OAuth / credenciales locales (`py -m ee.cli.eecli authenticate`)"
    if settings.gee_private_key_path is not None:
        resolved_key = resolve_service_account_path(settings.gee_private_key_path)
        if resolved_key.exists():
            key_info = f"Service Account JSON: {resolved_key}"
        else:
            key_missing = True
            has_error = True
            key_info = (
                f"Falta archivo: {resolved_key}. Coloca ahí tu clave JSON o deja "
                "GEE_PRIVATE_KEY_PATH vacío en .env."
            )

    bucket_label = settings.gcs_bucket or "(modo local sin bucket)"
    config_ok = bool(settings.gee_project_id) and not key_missing
    if not settings.gee_project_id:
        has_error = True
    table.add_row(
        "Config (.env)",
        "[green]OK[/green]" if config_ok else "[red]ERROR[/red]",
        f"Proyecto: {settings.gee_project_id or '-'} | Bucket: {bucket_label} | {key_info}",
    )

    # 2. Google Earth Engine
    try:
        project = initialize_earth_engine(settings)
        probe = ee.Number(1).getInfo()
        col_id = settings.resolved_asset_collection()
        col_status = "sin colección"
        if col_id and settings.gcs_bucket:
            try:
                ee.data.getAsset(col_id)
                col_status = f"colección OK ({col_id})"
            except ee.EEException:
                col_status = f"colección se creará al ingestar ({col_id})"
        elif not settings.gcs_bucket:
            col_status = "catálogo nativo activo"
        table.add_row(
            "Google Earth Engine",
            "[green]OK[/green]",
            f"Proyecto '{project}' activo (probe={probe}, {col_status})",
        )
    except Exception as exc:
        has_error = True
        table.add_row("Google Earth Engine", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 3. Google Cloud Storage (optional in local mode)
    if not settings.gcs_bucket:
        table.add_row(
            "Google Cloud Storage",
            "[cyan]LOCAL[/cyan]",
            "Omitido: modo local sin bucket (GCS_BUCKET vacío; usa `hyp3-download` / `asf-download`).",
        )
    else:
        try:
            gcs_client = build_gcs_client(settings)
            bucket_exists = gcs_client.bucket(settings.gcs_bucket).exists()
            if bucket_exists:
                table.add_row(
                    "Google Cloud Storage",
                    "[green]OK[/green]",
                    f"Bucket accesible: gs://{settings.gcs_bucket}",
                )
            else:
                has_error = True
                table.add_row(
                    "Google Cloud Storage",
                    "[red]ERROR[/red]",
                    f"El bucket 'gs://{settings.gcs_bucket}' no existe o no es accesible.",
                )
        except Exception as exc:
            has_error = True
            table.add_row("Google Cloud Storage", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 4. NASA Earthdata / ASF HyP3
    try:
        hyp3 = create_hyp3_client(settings)
        info = hyp3.my_info()
        user_id = info.get("user_id", "desconocido")
        credits_left = info.get("remaining_credits", "?")
        table.add_row(
            "NASA HyP3 / Earthdata",
            "[green]OK[/green]",
            f"Usuario: {user_id} | Créditos restantes: {credits_left}",
        )
    except Exception as exc:
        has_error = True
        table.add_row("NASA HyP3 / Earthdata", "[red]ERROR[/red]", _format_actionable_error(exc))

    # 5. NASA ASF Catalog search
    try:
        bbox = AOI_PRESETS["langtang"].bbox
        sample = search_asf_granules("sentinel1", bbox, "2026-09-01", "2026-10-15", max_results=1)
        table.add_row(
            "Catálogo NASA ASF",
            "[green]OK[/green]",
            f"Conectado (ejemplo Langtang: {sample[0].scene_name if sample else '0 resultados'})",
        )
    except Exception as exc:
        has_error = True
        table.add_row("Catálogo NASA ASF", "[red]ERROR[/red]", str(exc))

    console.print(table)
    if has_error:
        raise click.ClickException("Hay verificaciones pendientes en la configuración.")


@main.command("check-native")
@click.option(
    "--aoi",
    default="langtang",
    show_default=True,
    help="Preset ('langtang', 'khumbu') o 'min_lon,min_lat,max_lon,max_lat'.",
)
@click.option("--start", "start_date", required=True, help="Fecha inicio YYYY-MM-DD.")
@click.option("--end", "end_date", required=True, help="Fecha fin YYYY-MM-DD.")
@click.option(
    "--dataset",
    type=click.Choice(["all", *NATIVE_CATALOG.keys()]),
    default="all",
    show_default=True,
    help="Dataset nativo a verificar en GEE.",
)
@click.option(
    "--orbit-pass",
    type=click.Choice(["ASCENDING", "DESCENDING"], case_sensitive=False),
    default=None,
    help="Filtro opcional de órbita para Sentinel-1 GRD.",
)
def check_native(
    aoi: str,
    start_date: str,
    end_date: str,
    dataset: str,
    orbit_pass: str | None,
) -> None:
    """Verifica disponibilidad y última fecha de Sentinel-1 GRD y MODIS en GEE."""
    with _cli_error_boundary():
        settings = Settings()
        project = initialize_earth_engine(settings)
        aoi_name, bbox = resolve_aoi(aoi)
        console.print(f"[green]GEE inicializado[/green] (proyecto: {project}) | AOI: {aoi_name}")

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

    table = Table(title=f"Catálogo Nativo GEE ({start_date} a {end_date})")
    table.add_column("Dataset ID", style="cyan", overflow="fold")
    table.add_column("Imágenes", justify="right", overflow="fold")
    table.add_column("Más antigua (UTC)", overflow="fold")
    table.add_column("Más reciente (UTC)", overflow="fold")
    table.add_column("Bandas disponibles", overflow="fold")

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
    help="Dataset ASF: sentinel1, nisar, nisar_science, uavsar, opera_s1 o nombre ASF.",
)
@click.option("--aoi", default="langtang", show_default=True)
@click.option("--start", "start_date", required=True, help="Fecha inicio ISO/YYYY-MM-DD.")
@click.option("--end", "end_date", required=True, help="Fecha fin ISO/YYYY-MM-DD.")
@click.option("--processing-level", default=None, help="Ej. SLC, GRD_HD, RTC.")
@click.option("--max-results", default=15, show_default=True, type=int)
def asf_search_cmd(
    dataset: str,
    aoi: str,
    start_date: str,
    end_date: str,
    processing_level: str | None,
    max_results: int,
) -> None:
    """Busca gránulos en el catálogo de NASA ASF (Sentinel-1, NISAR, UAVSAR)."""
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

    table = Table(title=f"Resultados ASF ({dataset}) - {aoi_name}")
    table.add_column("Scene / Granule", style="cyan", overflow="fold")
    table.add_column("Plataforma", overflow="fold")
    table.add_column("Nivel", overflow="fold")
    table.add_column("Inicio (UTC)", overflow="fold")
    table.add_column("Órbita", overflow="fold")
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
        console.print("[yellow]No se encontraron gránulos con los filtros indicados.[/yellow]")
    else:
        console.print("[bold]Granules (copiables):[/bold]")
        for index, g in enumerate(granules, start=1):
            click.echo(f"  {index}. {g.scene_name}")


@main.command("asf-download")
@click.option(
    "--granule",
    "granules",
    multiple=True,
    required=True,
    help="Nombre exacto del gránulo ASF (ej. OPERA_L2_RTC-S1_...) para descargar en local.",
)
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filtrar GeoTIFFs por sufijo (ej. _VV.tif, _VH.tif).",
)
@click.option("--sensor", default=None, help="Etiqueta opcional de sensor.")
def asf_download_cmd(
    granules: tuple[str, ...],
    suffixes: tuple[str, ...],
    sensor: str | None,
) -> None:
    """Descarga GeoTIFFs directos de NASA ASF (ej. OPERA-S1) a local sin usar GCS."""
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
@click.option("--name", "job_name", required=True, help="Nombre para identificar el job HyP3.")
@click.option(
    "--granule",
    "granules",
    multiple=True,
    required=True,
    help="Gránulo(s) ASF. 1 para rtc, 2 para insar.",
)
def hyp3_submit_cmd(job_type: str, job_name: str, granules: tuple[str, ...]) -> None:
    """Envía un job RTC o InSAR al servicio ASF HyP3."""
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
            f"[green]Job enviado[/green] ID: [cyan]{job.job_id}[/cyan] | "
            f"Nombre: {job.name} | Estado: {job.status_code}"
        )


@main.command("hyp3-status")
@click.option("--name", "job_name", default=None, help="Filtrar por nombre del job HyP3.")
@click.option(
    "--status",
    type=click.Choice(["PENDING", "RUNNING", "SUCCEEDED", "FAILED"], case_sensitive=False),
    default=None,
    help="Filtrar por estado del job.",
)
@click.option("--limit", default=20, show_default=True, type=int)
def hyp3_status_cmd(job_name: str | None, status: str | None, limit: int) -> None:
    """Lista el estado de tus jobs en ASF HyP3."""
    with _cli_error_boundary():
        settings = Settings()
        hyp3 = create_hyp3_client(settings)
        jobs = list_hyp3_jobs(hyp3, job_name=job_name, status_code=status)[:limit]

    table = Table(title="Jobs en ASF HyP3")
    table.add_column("Job ID", style="cyan", overflow="fold")
    table.add_column("Nombre", overflow="fold")
    table.add_column("Tipo", overflow="fold")
    table.add_column("Estado", overflow="fold")
    table.add_column("Expirado", overflow="fold")
    table.add_column("Archivos", justify="right", overflow="fold")

    for job in jobs:
        expired_str = "Sí" if job.expired() else "No"
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
        console.print("[yellow]No hay jobs HyP3 que coincidan con los filtros.[/yellow]")


@main.command("hyp3-download")
@click.option("--job-id", default=None, help="ID de un job completado en ASF HyP3.")
@click.option("--job-name", default=None, help="Nombre de jobs completados en ASF HyP3.")
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filtrar GeoTIFFs de HyP3 por sufijo (ej. _unw_phase.tif, _coh.tif, _vv.tif).",
)
def hyp3_download_cmd(
    job_id: str | None,
    job_name: str | None,
    suffixes: tuple[str, ...],
) -> None:
    """Descarga jobs completados de ASF HyP3 a disco local y genera catálogo JSON (sin GCS)."""
    if not job_id and not job_name:
        raise click.UsageError("Especifica '--job-id' o '--job-name'.")

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
@click.option("--job-id", default=None, help="ID de un job completado en ASF HyP3.")
@click.option("--job-name", default=None, help="Nombre de jobs completados en ASF HyP3.")
@click.option(
    "--local-file",
    "local_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="GeoTIFF(s) locales ya descargados (ej. NISAR, UAVSAR o HyP3).",
)
@click.option("--sensor", default=None, help="Etiqueta de sensor (NISAR, UAVSAR, SENTINEL-1).")
@click.option(
    "--suffix",
    "suffixes",
    multiple=True,
    help="Filtrar GeoTIFFs de HyP3 por sufijo (ej. _unw_phase.tif, _coh.tif, _vv.tif).",
)
@click.option("--collection", default=None, help="Asset ID destino de ImageCollection en GEE.")
@click.option("--start-time", default=None, help="Timestamp UTC ISO opcional para archivos locales.")
@click.option("--end-time", default=None, help="Timestamp fin UTC ISO opcional.")
@click.option("--overwrite", is_flag=True, help="Sobrescribir asset existente en GEE.")
@click.option("--wait", is_flag=True, help="Esperar a que termine la tarea de ingesta en GEE.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Genera manifiestos y comandos `earthengine upload image` sin subir ni ingestar.",
)
@click.option(
    "--local-only",
    is_flag=True,
    help="Descarga e indexa en catálogo local (data/downloads/catalog.json) sin usar GCS ni GEE.",
)
@click.option(
    "--use-community-bridge",
    is_flag=True,
    help="Delegar jobs HyP3 al paquete gee-community/sar-asf-to-gee.",
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
    """Sube GeoTIFFs a GCS+GEE, o los prepara en local con `--local-only`."""
    if bool(local_files) == bool(job_id or job_name):
        raise click.UsageError(
            "Especifica '--local-file' O bien '--job-id' / '--job-name', no ambos."
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
        f"[bold]Colección destino:[/bold] [cyan]{report.collection_id}[/cyan] "
        f"(dry_run={report.dry_run})"
    )
    for res in report.results:
        console.print(
            f"- Archivo: {res.local_path.name}\n"
            f"  GCS URI: {res.gcs_uri}\n"
            f"  Asset ID: {res.asset_id}\n"
            f"  Task ID: {res.task_id or '-'} | Estado: {res.state}\n"
            f"  CLI eq.: {' '.join(shlex.quote(arg) for arg in res.cli_command)}"
        )


@main.command("publish-madness")
@click.option(
    "--aoi",
    "aois",
    multiple=True,
    help="Preset de glaciar ('langtang', 'khumbu') o bbox; por defecto publica langtang y khumbu.",
)
@click.option("--pre-start", default="2026-01-01", show_default=True, help="Inicio ventana Antes (YYYY-MM-DD).")
@click.option("--pre-end", default="2026-02-28", show_default=True, help="Fin ventana Antes (YYYY-MM-DD).")
@click.option("--post-start", default="2026-05-15", show_default=True, help="Inicio ventana Después (YYYY-MM-DD).")
@click.option("--post-end", default="2026-08-31", show_default=True, help="Fin ventana Después (YYYY-MM-DD).")
@click.option("--max-dim", default=420, show_default=True, type=int, help="Lado máximo en píxeles.")
@click.option(
    "--target-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directorio public/ de sar-Madness destino.",
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
    """Exporta datos reales de deshielo (GeoTIFFs + PNGs Antes/Después) a sar-Madness."""
    from glacier_monitor.ingestion.platform_exporter import export_deshielo_case_to_madness

    settings = Settings()
    selected = list(aois) if aois else list(AOI_PRESETS.keys())
    for aoi_spec in selected:
        with console.status(f"[bold cyan]Descargando GEE real y exportando {aoi_spec}...[/bold cyan]"):
            res = export_deshielo_case_to_madness(
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
            f"[green]Publicado {res.case_id}[/green] | GeoTIFFs={res.geotiff_count} | "
            f"PNGs={res.png_count} | Nieve {res.snow_pre_pct}% -> {res.snow_post_pct}% | "
            f"Temp {res.lst_pre_c}°C -> {res.lst_post_c}°C | Deshielo={res.melt_area_km2} km²"
        )


if __name__ == "__main__":
    main()
