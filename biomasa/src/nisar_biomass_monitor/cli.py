"""CLI interface to list NISAR GCOV scenes and publish the biomass case to sar-Madness."""

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
        prog="biomass-stage1",
        description="NISAR L3 ATBD aboveground biomass (AGB) pipeline and sar-Madness integration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-scenes", help="List public NISAR GCOV dates available for LENO on Wasabi S3.")

    pub = sub.add_parser(
        "publish-madness",
        help="Download/read GCOV + LULC, run L3 ATBD AGB inversion, and publish to sar-Madness.",
    )
    pub.add_argument("--pre-dates", nargs="+", default=list(LENO_SITE.pre_dates), help="YYYYMMDD dates for the Before window.")
    pub.add_argument("--post-dates", nargs="+", default=list(LENO_SITE.post_dates), help="YYYYMMDD dates for the After window.")
    pub.add_argument("--cache-dir", type=Path, default=None, help="Cache directory for .h5 files and reference layers.")
    pub.add_argument("--madness-root", type=Path, default=None, help="Path to sar-Madness/sar-Madness.")
    pub.add_argument("--config-ini", type=Path, default=None, help="Alternate path to NISAR_Biomass/config/config.ini.")
    pub.add_argument("--loss-threshold", type=float, default=-40.0, help="ΔAGB threshold (Mg/ha) for biomass loss polygons.")
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

    print(f"[1/5] Querying Wasabi S3 catalog ({LENO_SITE.site_code})...")
    remote = list_remote_gcov_scenes(LENO_SITE)
    all_tags = list(dict.fromkeys([*pre_dates, *post_dates]))

    print(f"[2/5] Checking/downloading {len(all_tags)} GCOV (.h5) scene(s) in {cache}...")
    fetched = download_scenes_for_dates(remote, all_tags, cache)

    print("[3/5] Loading NLCD LULC (30 m -> Albers) and Planet optical reference (100 m)...")
    refs = load_leno_reference_layers(LENO_SITE, cache)

    print("[4/5] Aggregating HH/HV bands to 100 m (1 ha) and inverting the NISAR L3 ATBD model...")
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

    print(f"[5/5] Exporting case {LENO_SITE.case_id} to {madness}...")
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
    print(f"Published: {summary.case_json_path}")
    print(f"Catalog  : {summary.catalog_path}")
    print("Metrics  :", summary.metrics)


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
