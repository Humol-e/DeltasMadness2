"""Command-line interface for wildfire GeoPulse ingestion & sar-platform export."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from wildfire_monitor.config import (
    AOI_PRESETS,
    Settings,
    resolve_aoi,
)
from wildfire_monitor.ingestion.native_catalog import (
    fetch_wildfire_scout_alerts,
    inspect_all_native_datasets,
)
from wildfire_monitor.ingestion.pipeline import (
    convert_geopulse_dir_to_platform,
    run_wildfire_to_platform,
    stage_local_geotiffs_catalog,
)
from wildfire_monitor.ingestion.platform_exporter import (
    publish_cases_to_sar_madness_v2,
    publish_cases_to_sar_platform,
)
from wildfire_monitor.pc_auth import check_stac_connectivity

console = Console()


def _load_settings() -> Settings:
    return Settings()


@click.group()
def main() -> None:
    """Herramienta CLI de ingesta GeoPulse de incendios adaptada para sar-platform."""


@main.command("list-aois")
def list_aois_cmd() -> None:
    """Lista las regiones preconfiguradas de incendios procedentes de GeoPulse."""
    table = Table(title="Áreas de Incendios Preconfiguradas (GeoPulse)")
    table.add_column("Clave AOI", style="cyan", no_wrap=True)
    table.add_column("Nombre", style="bold")
    table.add_column("Pre-incendio")
    table.add_column("Post-incendio")
    table.add_column("Bounding Box [W, S, E, N]")

    for key, preset in AOI_PRESETS.items():
        table.add_row(
            key,
            preset.name,
            preset.before,
            preset.after,
            str(preset.bbox.to_ee_bounds()),
        )
    console.print(table)


@main.command("doctor")
@click.option("--offline", is_flag=True, help="Omite la prueba de red hacia Planetary Computer.")
def doctor_cmd(offline: bool) -> None:
    """Verifica el repositorio GeoPulse, dependencias locales, rutas y API STAC."""
    settings = _load_settings()
    gp_dir = settings.resolved_geopulse_dir()
    console.print(f"[bold]GeoPulse dir:[/bold] {gp_dir} (existe={gp_dir.exists()})")
    console.print(f"[bold]Local downloads dir:[/bold] {settings.resolved_download_dir()}")
    console.print(f"[bold]Local cases dir:[/bold] {settings.resolved_cases_dir()}")
    console.print(f"[bold]sar-platform cases dir:[/bold] {settings.resolved_sar_platform_cases_dir()}")

    for pkg in ("numpy", "PIL", "contourpy", "shapely", "yaml"):
        ok = importlib.util.find_spec(pkg) is not None
        status = "[green]OK[/green]" if ok else "[red]FALTA[/red]"
        console.print(f"  Dependencia {pkg}: {status}")

    if not offline:
        stac_status = check_stac_connectivity(settings)
        if stac_status.get("ok"):
            console.print(f"[green]Planetary Computer STAC OK[/green] ({stac_status.get('title')})")
        else:
            console.print(f"[yellow]Aviso STAC:[/yellow] {stac_status.get('error')}")


@main.command("check-native")
@click.option("--aoi", default="palisades", show_default=True, help="Clave AOI o min_lon,min_lat,max_lon,max_lat.")
@click.option("--start-date", default="2024-12-01", show_default=True, help="Fecha inicial YYYY-MM-DD.")
@click.option("--end-date", default="2025-03-31", show_default=True, help="Fecha final YYYY-MM-DD.")
def check_native_cmd(aoi: str, start_date: str, end_date: str) -> None:
    """Consulta escenas disponibles en Planetary Computer (Sentinel-2, Sentinel-1 RTC, COP-DEM)."""
    settings = _load_settings()
    name, bbox = resolve_aoi(aoi, geopulse_dir=settings.resolved_geopulse_dir())
    statuses = inspect_all_native_datasets(
        bbox,
        start_date,
        end_date,
        max_cloud_cover=settings.max_cloud_cover,
        settings=settings,
    )

    table = Table(title=f"Catálogo STAC GeoPulse — {name} ({start_date} .. {end_date})")
    table.add_column("Colección", style="cyan")
    table.add_column("STAC ID")
    table.add_column("Escenas", justify="right", style="bold")
    table.add_column("Primera UTC")
    table.add_column("Última UTC")
    table.add_column("Res (m)", justify="right")

    for st in statuses:
        table.add_row(
            st.key,
            st.collection_id,
            str(st.scene_count),
            st.earliest_time_utc or "-",
            st.latest_time_utc or "-",
            str(st.spatial_resolution_m),
        )
    console.print(table)


@main.command("scout-wildfires")
@click.option(
    "--source",
    type=click.Choice(["eonet", "gdacs", "cems", "all"], case_sensitive=False),
    default="eonet",
    show_default=True,
)
@click.option("--limit", default=8, show_default=True, type=int)
def scout_wildfires_cmd(source: str, limit: int) -> None:
    """Consulta alertas activas de incendios desde GeoPulse Scout (EONET/GDACS/CEMS)."""
    settings = _load_settings()
    alerts = fetch_wildfire_scout_alerts(
        source=source.lower(),  # type: ignore[arg-type]
        max_events=limit,
        settings=settings,
    )
    table = Table(title=f"Alertas GeoPulse Scout ({source.upper()})")
    table.add_column("Fuente", style="cyan")
    table.add_column("Título", style="bold")
    table.add_column("Fecha UTC")
    table.add_column("BBox [W, S, E, N]")

    for alert in alerts:
        table.add_row(
            alert.source,
            alert.title,
            alert.date_utc,
            str(alert.bbox.to_ee_bounds()),
        )
    console.print(table)


@main.command("run-wildfire")
@click.option(
    "--aoi",
    default="palisades",
    show_default=True,
    help="Preset GeoPulse ('palisades', 'camp_paradise', 'martin_nv', 'all') o bbox.",
)
@click.option("--before", default=None, help="Ventana pre-incendio YYYY-MM-DD/YYYY-MM-DD (opcional en presets).")
@click.option("--after", default=None, help="Ventana post-incendio YYYY-MM-DD/YYYY-MM-DD (opcional en presets).")
@click.option("--case-id", default=None, help="Identificador de caso de salida.")
@click.option("--max-dim", default=None, type=int, help="Dimensión máxima en píxeles de la malla.")
@click.option("--dry-run", is_flag=True, help="Simula la ejecución sin descargar ni escribir tiles.")
@click.option("--publish", is_flag=True, help="Copia y registra el resultado en ../sar-platform.")
def run_wildfire_cmd(
    aoi: str,
    before: str | None,
    after: str | None,
    case_id: str | None,
    max_dim: int | None,
    dry_run: bool,
    publish: bool,
) -> None:
    """Descarga escenas reales STAC, calcula severidad GeoPulse y exporta formato sar-platform."""
    settings = _load_settings()
    targets = list(AOI_PRESETS.keys()) if aoi.strip().lower() == "all" else [aoi]

    for target in targets:
        report = run_wildfire_to_platform(
            target,
            settings,
            before=before,
            after=after,
            case_id=case_id if len(targets) == 1 else None,
            max_dim=max_dim,
            dry_run=dry_run,
            publish=publish,
        )
        if report.dry_run:
            console.print(
                f"[DRY_RUN] {report.case_id} ({report.title}) -> "
                f"before={report.summary['before']} after={report.summary['after']}"
            )
            continue

        km2 = report.summary.get("affected_km2", 0.0)
        obs = report.summary.get("observed_km2", 0.0)
        tiles_n = report.exported_case.tile_count if report.exported_case else 0
        console.print(
            f"[green]OK[/green] {report.case_id}: {km2:.2f} km² quemados (de {obs:.1f} km²) | "
            f"{len(report.staging_report.files)} GeoTIFFs | {tiles_n} tiles XYZ"
        )
        if report.publish_report:
            console.print(
                f"  -> Publicado en sar-platform: {report.publish_report.target_cases_dir}"
            )


@main.command("convert-geopulse")
@click.argument("source_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--case-id", default=None, help="ID del caso resultante.")
@click.option("--title", default=None, help="Título legible para sar-platform.")
@click.option("--publish", is_flag=True, help="Publica también en ../sar-platform.")
def convert_geopulse_cmd(
    source_dir: Path,
    case_id: str | None,
    title: str | None,
    publish: bool,
) -> None:
    """Convierte una carpeta de salida de GeoPulse al formato de sar-platform conservando colores."""
    settings = _load_settings()
    report = convert_geopulse_dir_to_platform(
        source_dir,
        settings,
        case_id=case_id,
        title=title,
        publish=publish,
    )
    tiles_n = report.exported_case.tile_count if report.exported_case else 0
    console.print(
        f"[green]Convertido[/green] {report.case_id}: {len(report.staging_report.files)} GeoTIFFs, "
        f"{tiles_n} tiles XYZ en {settings.resolved_cases_dir() / report.case_id}"
    )


@main.command("publish-platform")
@click.option("--case-id", multiple=True, help="Caso específico a publicar (por defecto todos).")
@click.option("--include-downloads", is_flag=True, help="Copia también GeoTIFFs y catalog.json.")
def publish_platform_cmd(case_id: tuple[str, ...], include_downloads: bool) -> None:
    """Copia y registra los casos ya convertidos en ../sar-platform/frontend/public/data/cases/."""
    settings = _load_settings()
    pub = publish_cases_to_sar_platform(
        settings.resolved_cases_dir(),
        settings.resolved_sar_platform_cases_dir(),
        source_downloads_dir=settings.resolved_download_dir() if include_downloads else None,
        target_downloads_dir=settings.resolved_sar_platform_downloads_dir() if include_downloads else None,
        case_ids=list(case_id) if case_id else None,
    )
    console.print(
        f"[green]Publicado(s)[/green] {', '.join(pub.published_case_ids)} en {pub.target_cases_dir}"
    )
    if include_downloads:
        console.print(f"  GeoTIFFs copiados a downloads: {pub.copied_downloads}")


@main.command("publish-madness")
@click.option("--case-id", multiple=True, help="Caso específico a exportar en esquema v2.")
@click.option(
    "--target-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Carpeta public/data de sar-Madness.",
)
def publish_madness_cmd(case_id: tuple[str, ...], target_dir: Path | None) -> None:
    """Exporta casos de incendios en esquema v2 al visor sar-Madness."""
    settings = _load_settings()
    resolved_target = target_dir or (
        Path(__file__).resolve().parents[3] / "sar-Madness" / "sar-Madness" / "public" / "data"
    )
    published = publish_cases_to_sar_madness_v2(
        settings.resolved_cases_dir(),
        settings.resolved_download_dir(),
        resolved_target,
        case_ids=list(case_id) if case_id else None,
    )
    console.print(f"[green]Publicado(s) v2:[/green] {', '.join(published)} -> {resolved_target}")


@main.command("catalog-geotiffs")
@click.argument("geotiff_paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--event-id", default=None, help="Identificador de evento para metadatos.")
def catalog_geotiffs_cmd(geotiff_paths: tuple[Path, ...], event_id: str | None) -> None:
    """Registra GeoTIFFs de incendios en data/downloads/catalog.json."""
    settings = _load_settings()
    rep = stage_local_geotiffs_catalog(
        list(geotiff_paths),
        settings.resolved_download_dir(),
        event_id=event_id,
    )
    console.print(f"[green]Catálogo actualizado:[/green] {rep.catalog_path} ({len(rep.files)} archivos)")


if __name__ == "__main__":
    main()
