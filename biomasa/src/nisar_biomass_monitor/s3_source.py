"""Descarga y lectura de escenas reales NISAR L2 GCOV (.h5) y referencias LENO desde S3."""

from __future__ import annotations

import concurrent.futures
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np
import requests
from PIL import Image

from .config import SiteDefinition
from .geo_utils import albers_forward, utm_to_lonlat

_DATE_RE = re.compile(r"_(\d{8})T\d{6}_")


@dataclass(frozen=True)
class RemoteNisarScene:
    """Metadatos de una escena NISAR GCOV en Wasabi S3."""

    date_tag: str
    key: str
    size_bytes: int
    url: str


@dataclass(frozen=True)
class ResampledGcovScene:
    """Escena GCOV agregada a resolución objetivo (100 m / 1 ha) sobre el ROI."""

    date_tag: str
    acquisition_iso: str
    hh_linear: np.ndarray
    hv_linear: np.ndarray
    valid_fraction: np.ndarray
    epsg: int
    source_path: Path
    source_url: str


@dataclass(frozen=True)
class LenoReferenceLayers:
    """Capas auxiliares reales de cobertura (NLCD LULC) y referencia Planet/FNF."""

    nlcd_lulc: np.ndarray
    forest_mask: np.ndarray
    classification_100m: np.ndarray
    planet_disturbance: np.ndarray


def list_remote_gcov_scenes(site: SiteDefinition, *, timeout_s: float = 30.0) -> list[RemoteNisarScene]:
    """Lista las escenas HDF5 GCOV públicas disponibles para el sitio NISAR."""
    prefix = f"{site.s3_prefix}GCOV/simulated/"
    resp = requests.get(
        f"{site.s3_base_url}/?list-type=2&prefix={prefix}",
        timeout=timeout_s,
    )
    resp.raise_for_status()
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    root = ET.fromstring(resp.text)

    scenes: list[RemoteNisarScene] = []
    for item in root.findall("s3:Contents", ns):
        key_el = item.find("s3:Key", ns)
        size_el = item.find("s3:Size", ns)
        if key_el is None or size_el is None or not key_el.text:
            continue
        key = key_el.text
        if not key.endswith(".h5"):
            continue
        match = _DATE_RE.search(key)
        if not match:
            continue
        scenes.append(
            RemoteNisarScene(
                date_tag=match.group(1),
                key=key,
                size_bytes=int(size_el.text or "0"),
                url=f"{site.s3_base_url}/{key}",
            )
        )
    return sorted(scenes, key=lambda s: s.date_tag)


def download_cached_file(
    url: str,
    dest_path: Path,
    *,
    expected_size: int | None = None,
    timeout_s: float = 240.0,
) -> Path:
    """Descarga un archivo de Wasabi S3 en `dest_path` evitando descargas incompletas."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.is_file():
        size = dest_path.stat().st_size
        if size > 0 and (expected_size is None or expected_size == 0 or size == expected_size):
            return dest_path

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout_s) as resp:
        resp.raise_for_status()
        with open(tmp_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    tmp_path.replace(dest_path)
    return dest_path


def download_scenes_for_dates(
    scenes: Iterable[RemoteNisarScene],
    date_tags: Iterable[str],
    cache_dir: Path,
    *,
    max_workers: int = 3,
) -> dict[str, tuple[RemoteNisarScene, Path]]:
    """Descarga (si faltan en caché) las escenas `.h5` solicitadas en paralelo."""
    by_date = {s.date_tag: s for s in scenes}
    requested: list[RemoteNisarScene] = []
    for tag in date_tags:
        if tag not in by_date:
            raise KeyError(f"Fecha GCOV no disponible en Wasabi S3: {tag}")
        requested.append(by_date[tag])

    def _fetch(sc: RemoteNisarScene) -> tuple[str, tuple[RemoteNisarScene, Path]]:
        local_name = f"NISAR_L2_PR_GCOV_{sc.date_tag}.h5"
        path = download_cached_file(sc.url, cache_dir / local_name, expected_size=sc.size_bytes)
        return sc.date_tag, (sc, path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        results = list(pool.map(_fetch, requested))
    return dict(results)


def read_nisar_gcov_100m(
    h5_path: Path,
    site: SiteDefinition,
    *,
    date_tag: str,
    source_url: str = "",
) -> ResampledGcovScene:
    """Lee solo el recorte del ROI desde el `.h5` (20 m) y lo promedia a 100 m (1 ha)."""
    xmin, ymin, xmax, ymax = site.roi_bounds_utm
    w_out, h_out = site.roi_width_100m, site.roi_height_100m
    res_m = site.target_resolution_m

    with h5py.File(h5_path, "r") as f:
        g = f["/science/LSAR/GCOV/grids/frequencyA"]
        x_coords = g["xCoordinates"][:]
        y_coords = g["yCoordinates"][:]
        epsg = int(g["projection"][()])

        x_mask = (x_coords >= xmin) & (x_coords < xmax)
        y_mask = (y_coords > ymin) & (y_coords <= ymax)
        if not np.any(x_mask) or not np.any(y_mask):
            raise ValueError(f"{h5_path.name} no intersecta roi_bounds_utm={site.roi_bounds_utm}")

        c0, c1 = int(np.where(x_mask)[0][0]), int(np.where(x_mask)[0][-1]) + 1
        r0, r1 = int(np.where(y_mask)[0][0]), int(np.where(y_mask)[0][-1]) + 1

        hh_sub = g["HHHH"][r0:r1, c0:c1].astype(np.float64)
        hv_sub = g["HVHV"][r0:r1, c0:c1].astype(np.float64)

        start_ds = f["/science/LSAR/identification/zeroDopplerStartTime"][()]
        iso_raw = start_ds.decode("utf-8") if isinstance(start_ds, (bytes, np.bytes_)) else str(start_ds)
        acquisition_iso = iso_raw if iso_raw.endswith("Z") else f"{iso_raw[:19]}Z"

    x_sub = x_coords[c0:c1]
    y_sub = y_coords[r0:r1]
    col_100 = np.floor((x_sub - xmin) / res_m).astype(np.int64)
    row_100 = np.floor((ymax - y_sub) / res_m).astype(np.int64)

    rg, cg = np.meshgrid(row_100, col_100, indexing="ij")
    in_bounds = (rg >= 0) & (rg < h_out) & (cg >= 0) & (cg < w_out)
    finite = in_bounds & np.isfinite(hh_sub) & np.isfinite(hv_sub) & (hh_sub > 0.0) & (hv_sub > 0.0)

    n_cells = h_out * w_out
    flat_idx = (rg[finite] * w_out + cg[finite]).ravel()
    total_counts = np.bincount((rg[in_bounds] * w_out + cg[in_bounds]).ravel(), minlength=n_cells)
    valid_counts = np.bincount(flat_idx, minlength=n_cells)
    hh_sums = np.bincount(flat_idx, weights=hh_sub[finite].ravel(), minlength=n_cells)
    hv_sums = np.bincount(flat_idx, weights=hv_sub[finite].ravel(), minlength=n_cells)

    hh_out = np.full(n_cells, np.nan, dtype=np.float32)
    hv_out = np.full(n_cells, np.nan, dtype=np.float32)
    ok = valid_counts > 0
    hh_out[ok] = (hh_sums[ok] / valid_counts[ok]).astype(np.float32)
    hv_out[ok] = (hv_sums[ok] / valid_counts[ok]).astype(np.float32)

    frac = np.where(total_counts > 0, valid_counts / np.maximum(total_counts, 1), 0.0).astype(
        np.float32
    )
    return ResampledGcovScene(
        date_tag=date_tag,
        acquisition_iso=acquisition_iso,
        hh_linear=hh_out.reshape(h_out, w_out),
        hv_linear=hv_out.reshape(h_out, w_out),
        valid_fraction=frac.reshape(h_out, w_out),
        epsg=epsg,
        source_path=h5_path,
        source_url=source_url,
    )


def load_leno_reference_layers(site: SiteDefinition, cache_dir: Path) -> LenoReferenceLayers:
    """Descarga y muestrea `LENO_LULC.tif`, clasificación LENO 100 m y referencia Planet."""
    lulc_path = download_cached_file(f"{site.s3_base_url}/{site.lulc_key}", cache_dir / "LENO_LULC.tif")
    cls_path = download_cached_file(
        f"{site.s3_base_url}/{site.classification_ref_key}",
        cache_dir / Path(site.classification_ref_key).name,
    )
    dist_path = download_cached_file(
        f"{site.s3_base_url}/{site.disturbance_ref_key}",
        cache_dir / Path(site.disturbance_ref_key).name,
    )

    with Image.open(lulc_path) as img:
        lulc_arr = np.array(img, dtype=np.uint8)
        scale = img.tag_v2.get(33550, (30.0, 30.0, 0.0))
        tie = img.tag_v2.get(33922, (0.0, 0.0, 0.0, 671265.0, 1062285.0, 0.0))
        geo_d = img.tag_v2.get(34736, (29.5, 45.5, 23.0, -96.0, 0.0, 0.0, 298.257223563, 6378137.0))

    xmin, _ymin, _xmax, ymax = site.roi_bounds_utm
    res = site.target_resolution_m
    xs = xmin + (np.arange(site.roi_width_100m, dtype=np.float64) + 0.5) * res
    ys = ymax - (np.arange(site.roi_height_100m, dtype=np.float64) + 0.5) * res
    xg, yg = np.meshgrid(xs, ys)
    lons, lats = utm_to_lonlat(xg, yg, site.epsg)
    ax, ay = albers_forward(
        lons,
        lats,
        phi1=float(geo_d[0]),
        phi2=float(geo_d[1]),
        phi0=float(geo_d[2]),
        lam0=float(geo_d[3]),
        a=float(geo_d[7]),
        inv_f=float(geo_d[6]),
    )
    lc = np.floor((ax - float(tie[3])) / float(scale[0])).astype(np.int64)
    lr = np.floor((float(tie[4]) - ay) / abs(float(scale[1]))).astype(np.int64)
    in_lulc = (lc >= 0) & (lc < lulc_arr.shape[1]) & (lr >= 0) & (lr < lulc_arr.shape[0])
    nlcd = np.zeros((site.roi_height_100m, site.roi_width_100m), dtype=np.uint8)
    nlcd[in_lulc] = lulc_arr[lr[in_lulc], lc[in_lulc]]

    with Image.open(cls_path) as img:
        cls_arr = np.array(img, dtype=np.uint8)
    with Image.open(dist_path) as img:
        dist_arr = (np.array(img, dtype=np.float32) > 0.5).astype(np.uint8)

    excluded = np.isin(nlcd, np.asarray(site.nlcd_excluded_classes, dtype=np.uint8))
    # Máscara válida de bosque/vegetación compatible con notebook ATBD y cobertura LENO (cls > 0)
    forest_mask = (nlcd > 0) & (~excluded) & (cls_arr > 0)

    return LenoReferenceLayers(
        nlcd_lulc=nlcd,
        forest_mask=forest_mask,
        classification_100m=cls_arr,
        planet_disturbance=dist_arr,
    )
