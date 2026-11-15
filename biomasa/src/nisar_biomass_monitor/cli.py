"""Interfaz CLI para listar escenas NISAR GCOV e integrar el caso de biomasa en sar-Madness."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np

from .config import LENO_SITE, find_repo_root, load_nisar_config
from .nisar_atbd import invert_agb_scene
from .platform_exporter import export_biomass_case_to_madness
from .s3_source import (
    download_scenes_for_dates,
    list_remote_gcov_scenes,
    load_leno_reference_layers,
    read_nisar_gcov_100m,
)


def _default_cache_dir(repo_root: Path) -> Path:
    return repo_root / "biomasa" / "data" / "cache"


def _default_madness_root(repo_root: Path) -> Path:
    return repo_root / "sar-Madness" / "sar-Madness"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biomasa-stage1",
        description="Pipeline NISAR L3 ATBD de biomasa aérea (AGB) e integración con sar-Madness.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-scenes", help="Lista fechas NISAR GCOV públicas disponibles para LENO en Wasabi S3.")

    pub = sub.add_parser(
        "publish-madness",
        help="Descarga/lee GCOV + LULC, ejecuta inversión AGB L3 ATBD y publica en sar-Madness.",
    )
    pub.add_argument("--pre-dates", nargs="+", default=list(LENO_SITE.pre_dates), help="Fechas YYYYMMDD ventana Antes.")
    pub.add_argument("--post-dates", nargs="+", default=list(LENO_SITE.post_dates), help="Fechas YYYYMMDD ventana Después.")
    pub.add_argument("--cache-dir", type=Path, default=None, help="Directorio caché para archivos .h5 y referencias.")
    pub.add_argument("--madness-root", type=Path, default=None, help="Ruta a sar-Madness/sar-Madness.")
    pub.add_argument("--config-ini", type=Path, default=None, help="Ruta alternativa a NISAR_Biomass/config/config.ini.")
    pub.add_argument("--loss-threshold", type=float, default=-40.0, help="Umbral ΔAGB (Mg/ha) para polígonos de pérdida.")
    return parser


def run_publish_madness(
    *,
    pre_dates: Sequence[str],
    post_dates: Sequence[str],
    cache_dir: Path | None = None,
    madness_root: Path | None = None,
    config_ini: Path | None = None,
    loss_threshold: float = -40.0,
) -> None:
    repo_root = find_repo_root()
    cache = cache_dir or _default_cache_dir(repo_root)
    madness = madness_root or _default_madness_root(repo_root)
    params = load_nisar_config(config_ini)

    print(f"[1/5] Consultando catálogo Wasabi S3 ({LENO_SITE.site_code})...")
    remote = list_remote_gcov_scenes(LENO_SITE)
    all_tags = list(dict.fromkeys([*pre_dates, *post_dates]))

    print(f"[2/5] Verificando/descargando {len(all_tags)} escenas GCOV (.h5) en {cache}...")
    fetched = download_scenes_for_dates(remote, all_tags, cache)

    print("[3/5] Cargando NLCD LULC (30 m -> Albers) y referencia óptica Planet (100 m)...")
    refs = load_leno_reference_layers(LENO_SITE, cache)

    print("[4/5] Agregando bandas HH/HV a 100 m (1 ha) e invirtiendo modelo NISAR L3 ATBD...")
    pre_scenes = [
        read_nisar_gcov_100m(fetched[d][1], LENO_SITE, date_tag=d, source_url=fetched[d][0].url)
        for d in pre_dates
    ]
    post_scenes = [
        read_nisar_gcov_100m(fetched[d][1], LENO_SITE, date_tag=d, source_url=fetched[d][0].url)
        for d in post_dates
    ]

    pre_inv = invert_agb_scene(
        np.stack([s.hh_linear for s in pre_scenes]),
        np.stack([s.hv_linear for s in pre_scenes]),
        params,
        valid_mask=refs.forest_mask,
        agb_max_mgha=LENO_SITE.agb_max_mgha,
    )
    post_inv = invert_agb_scene(
        np.stack([s.hh_linear for s in post_scenes]),
        np.stack([s.hv_linear for s in post_scenes]),
        params,
        valid_mask=refs.forest_mask,
        agb_max_mgha=LENO_SITE.agb_max_mgha,
    )

    print(f"[5/5] Exportando caso {LENO_SITE.case_id} hacia {madness}...")
    summary = export_biomass_case_to_madness(
        site=LENO_SITE,
        params=params,
        pre_scenes=pre_scenes,
        post_scenes=post_scenes,
        pre_inv=pre_inv,
        post_inv=post_inv,
        refs=refs,
        madness_root=madness,
        loss_threshold_mgha=loss_threshold,
    )
    print(f"Publicado: {summary.case_json_path}")
    print(f"Catálogo : {summary.catalog_path}")
    print("Métricas :", summary.metrics)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list-scenes":
        scenes = list_remote_gcov_scenes(LENO_SITE)
        for s in scenes:
            print(f"{s.date_tag}  {s.size_bytes:10d} bytes  {s.key}")
        return 0
    if args.command == "publish-madness":
        run_publish_madness(
            pre_dates=args.pre_dates,
            post_dates=args.post_dates,
            cache_dir=args.cache_dir,
            madness_root=args.madness_root,
            config_ini=args.config_ini,
            loss_threshold=args.loss_threshold,
        )
        return 0
    return 1
