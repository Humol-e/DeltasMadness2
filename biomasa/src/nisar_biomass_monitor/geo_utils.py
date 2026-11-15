"""Standalone geospatial utilities (UTM <-> WGS84, Albers, resampling, and pure-Python GeoTIFF)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2


@dataclass(frozen=True)
class UtmToWgs84Remap:
    """Precomputed fractional UTM source coordinates for a regular EPSG:4326 grid."""

    src_shape: tuple[int, int]
    bounds_wgs84: tuple[float, float, float, float]
    col_f: np.ndarray
    row_f: np.ndarray
    # Bilinear interpolation index/weight cache (computed once across bands)
    inside_bi: np.ndarray
    c0c: np.ndarray
    c1: np.ndarray
    r0c: np.ndarray
    r1: np.ndarray
    w00: np.ndarray
    w01: np.ndarray
    w10: np.ndarray
    w11: np.ndarray
    # Nearest-neighbor index cache
    inside_nn: np.ndarray
    ci_nn: np.ndarray
    ri_nn: np.ndarray


def utm_to_lonlat(
    easting: np.ndarray | float,
    northing: np.ndarray | float,
    epsg: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert WGS84 UTM coordinates (`EPSG:326xx`/`327xx`) to `(lon, lat)` in degrees."""
    zone = epsg % 100
    south = 32700 <= epsg < 32800

    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    e2 = f * (2.0 - f)
    n = f / (2.0 - f)
    n2, n3, n4 = n * n, n**3, n**4

    a_rect = (a / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0)
    beta = (
        0.5 * n - (2.0 / 3.0) * n2 + (37.0 / 96.0) * n3 - (1.0 / 360.0) * n4,
        (1.0 / 48.0) * n2 + (1.0 / 15.0) * n3 - (437.0 / 1440.0) * n4,
        (17.0 / 480.0) * n3 - (37.0 / 840.0) * n4,
        (4397.0 / 161280.0) * n4,
    )
    delta = (
        2.0 * n - (2.0 / 3.0) * n2 - 2.0 * n3 + (116.0 / 45.0) * n4,
        (7.0 / 3.0) * n2 - (8.0 / 5.0) * n3 - (227.0 / 45.0) * n4,
        (56.0 / 15.0) * n3 - (136.0 / 35.0) * n4,
        (4279.0 / 630.0) * n4,
    )
    _ = e2  # documents WGS84 reference flattening

    fn = 10000000.0 if south else 0.0
    xi = (np.asarray(northing, dtype=np.float64) - fn) / (k0 * a_rect)
    eta = (np.asarray(easting, dtype=np.float64) - 500000.0) / (k0 * a_rect)

    xi_p = np.array(xi, dtype=np.float64)
    eta_p = np.array(eta, dtype=np.float64)
    for j, b_j in enumerate(beta, start=1):
        xi_p = xi_p - b_j * np.sin(2.0 * j * xi) * np.cosh(2.0 * j * eta)
        eta_p = eta_p - b_j * np.cos(2.0 * j * xi) * np.sinh(2.0 * j * eta)

    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    phi = np.array(chi, dtype=np.float64)
    for j, d_j in enumerate(delta, start=1):
        phi = phi + d_j * np.sin(2.0 * j * chi)

    lon0 = math.radians((zone - 1) * 6.0 - 180.0 + 3.0)
    lam = lon0 + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lam), np.degrees(phi)


def lonlat_to_utm(
    lon_deg: np.ndarray | float,
    lat_deg: np.ndarray | float,
    epsg: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert `(lon, lat)` in WGS84 degrees to UTM `(easting, northing)`."""
    zone = epsg % 100
    south = 32700 <= epsg < 32800

    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    e = math.sqrt(f * (2.0 - f))
    n = f / (2.0 - f)
    n2, n3, n4, n5, n6 = n * n, n**3, n**4, n**5, n**6

    a_rect = (a / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0 + n6 / 256.0)
    alpha = (
        0.5 * n
        - (2.0 / 3.0) * n2
        + (5.0 / 16.0) * n3
        + (41.0 / 180.0) * n4
        - (127.0 / 288.0) * n5
        + (7891.0 / 37800.0) * n6,
        (13.0 / 48.0) * n2
        - (3.0 / 5.0) * n3
        + (557.0 / 1440.0) * n4
        + (281.0 / 630.0) * n5
        - (1983433.0 / 1935360.0) * n6,
        (61.0 / 240.0) * n3 - (103.0 / 140.0) * n4 + (15061.0 / 26880.0) * n5 + (167603.0 / 181440.0) * n6,
        (49561.0 / 161280.0) * n4 - (179.0 / 168.0) * n5 + (6601661.0 / 7257600.0) * n6,
        (34729.0 / 80640.0) * n5 - (3418889.0 / 1995840.0) * n6,
        (212378941.0 * n6) / 319334400.0,
    )
    rad = math.pi / 180.0
    lon0 = ((zone - 1) * 6.0 - 180.0 + 3.0) * rad
    fn = 10000000.0 if south else 0.0

    phi = np.asarray(lat_deg, dtype=np.float64) * rad
    lam = np.asarray(lon_deg, dtype=np.float64) * rad - lon0
    tau = np.tan(phi)
    sigma = np.sinh(e * np.arctanh((e * tau) / np.sqrt(1.0 + tau * tau)))
    tau_p = tau * np.sqrt(1.0 + sigma * sigma) - sigma * np.sqrt(1.0 + tau * tau)
    xi_p = np.arctan2(tau_p, np.cos(lam))
    eta_p = np.arcsinh(np.sin(lam) / np.sqrt(tau_p * tau_p + np.cos(lam) ** 2))
    xi = np.array(xi_p, dtype=np.float64)
    eta = np.array(eta_p, dtype=np.float64)
    for j, a_j in enumerate(alpha, start=1):
        xi = xi + a_j * np.sin(2.0 * j * xi_p) * np.cosh(2.0 * j * eta_p)
        eta = eta + a_j * np.cos(2.0 * j * xi_p) * np.sinh(2.0 * j * eta_p)
    return 500000.0 + k0 * a_rect * eta, fn + k0 * a_rect * xi


def albers_forward(
    lon_deg: np.ndarray | float,
    lat_deg: np.ndarray | float,
    *,
    phi1: float = 29.5,
    phi2: float = 45.5,
    phi0: float = 23.0,
    lam0: float = -96.0,
    a: float = 6378137.0,
    inv_f: float = 298.257223563,
) -> tuple[np.ndarray, np.ndarray]:
    """Ellipsoidal Albers Equal Area projection (CONUS NLCD) from `(lon, lat)` to meters."""
    f = 1.0 / inv_f
    e2 = 2.0 * f - f * f
    e = math.sqrt(e2)

    def _q(phi: np.ndarray | float) -> np.ndarray | float:
        s = np.sin(phi)
        return (1.0 - e2) * (s / (1.0 - e2 * s * s) - (1.0 / (2.0 * e)) * np.log((1.0 - e * s) / (1.0 + e * s)))

    def _m(phi: np.ndarray | float) -> np.ndarray | float:
        return np.cos(phi) / np.sqrt(1.0 - e2 * np.sin(phi) ** 2)

    p0, p1, p2 = np.radians([phi0, phi1, phi2])
    l0 = math.radians(lam0)
    q0, q1, q2 = _q(p0), _q(p1), _q(p2)
    m1, m2 = _m(p1), _m(p2)
    n = (m1 * m1 - m2 * m2) / (q2 - q1)
    c_val = m1 * m1 + n * q1
    rho0 = a * math.sqrt(c_val - n * q0) / n

    phi = np.radians(np.asarray(lat_deg, dtype=np.float64))
    lam = np.radians(np.asarray(lon_deg, dtype=np.float64))
    rho = a * np.sqrt(c_val - n * _q(phi)) / n
    theta = n * (lam - l0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


def utm_bounds_to_wgs84(
    bounds_utm: tuple[float, float, float, float],
    epsg: int,
) -> tuple[float, float, float, float]:
    """Compute outer bounding box `(west, south, east, north)` in EPSG:4326 from UTM bounds."""
    xmin, ymin, xmax, ymax = bounds_utm
    xs = np.linspace(xmin, xmax, 9, dtype=np.float64)
    ys = np.linspace(ymin, ymax, 9, dtype=np.float64)
    xg, yg = np.meshgrid(xs, ys)
    lons, lats = utm_to_lonlat(xg, yg, epsg)
    return float(np.min(lons)), float(np.min(lats)), float(np.max(lons)), float(np.max(lats))


def build_utm_to_wgs84_remap(
    src_shape: tuple[int, int],
    bounds_utm: tuple[float, float, float, float],
    epsg: int,
    *,
    width: int,
    height: int,
    bounds_wgs84: tuple[float, float, float, float] | None = None,
) -> UtmToWgs84Remap:
    """Precompute source pixel indices and bilinear weights for UTM -> EPSG:4326 reprojection."""
    wgs = bounds_wgs84 or utm_bounds_to_wgs84(bounds_utm, epsg)
    west, south, east, north = wgs
    xmin, ymin, xmax, ymax = bounds_utm
    src_h, src_w = src_shape
    dx_u = (xmax - xmin) / float(src_w)
    dy_u = (ymax - ymin) / float(src_h)

    lons = west + (np.arange(width, dtype=np.float64) + 0.5) * ((east - west) / float(width))
    lats = north - (np.arange(height, dtype=np.float64) + 0.5) * ((north - south) / float(height))
    lon_g, lat_g = np.meshgrid(lons, lats)
    ux, uy = lonlat_to_utm(lon_g, lat_g, epsg)

    col_f = (ux - xmin) / dx_u - 0.5
    row_f = (ymax - uy) / dy_u - 0.5

    ci_nn = np.rint(col_f).astype(np.int64)
    ri_nn = np.rint(row_f).astype(np.int64)
    inside_nn = (ci_nn >= 0) & (ci_nn < src_w) & (ri_nn >= 0) & (ri_nn < src_h)

    c0 = np.floor(col_f).astype(np.int64)
    r0 = np.floor(row_f).astype(np.int64)
    fc = np.clip(col_f - c0, 0.0, 1.0)
    fr = np.clip(row_f - r0, 0.0, 1.0)
    c1 = np.clip(c0 + 1, 0, src_w - 1)
    r1 = np.clip(r0 + 1, 0, src_h - 1)
    c0c = np.clip(c0, 0, src_w - 1)
    r0c = np.clip(r0, 0, src_h - 1)
    inside_bi = (col_f >= -0.5) & (col_f <= src_w - 0.5) & (row_f >= -0.5) & (row_f <= src_h - 0.5)

    return UtmToWgs84Remap(
        src_shape=src_shape,
        bounds_wgs84=wgs,
        col_f=col_f,
        row_f=row_f,
        inside_bi=inside_bi,
        c0c=c0c,
        c1=c1,
        r0c=r0c,
        r1=r1,
        w00=(1.0 - fr) * (1.0 - fc),
        w01=(1.0 - fr) * fc,
        w10=fr * (1.0 - fc),
        w11=fr * fc,
        inside_nn=inside_nn,
        ci_nn=ci_nn,
        ri_nn=ri_nn,
    )


def reproject_utm_to_wgs84_grid(
    arr_utm: np.ndarray,
    bounds_utm: tuple[float, float, float, float],
    epsg: int,
    *,
    width: int,
    height: int,
    bounds_wgs84: tuple[float, float, float, float] | None = None,
    nearest: bool = False,
    remap: UtmToWgs84Remap | None = None,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Reproject a UTM raster `(H_u, W_u)` onto a regular EPSG:4326 `(height, width)` grid."""
    rm = remap
    if rm is None or rm.src_shape != arr_utm.shape or rm.col_f.shape != (height, width):
        rm = build_utm_to_wgs84_remap(
            arr_utm.shape,
            bounds_utm,
            epsg,
            width=width,
            height=height,
            bounds_wgs84=bounds_wgs84,
        )

    if nearest or np.issubdtype(arr_utm.dtype, np.integer):
        fill = 0 if np.issubdtype(arr_utm.dtype, np.integer) else np.nan
        out = np.full((height, width), fill, dtype=arr_utm.dtype)
        inside = rm.inside_nn
        out[inside] = arr_utm[rm.ri_nn[inside], rm.ci_nn[inside]]
        return out, rm.bounds_wgs84

    # NaN-aware bilinear interpolation across the 4 neighbors
    src = arr_utm.astype(np.float64)
    v00, v01 = src[rm.r0c, rm.c0c], src[rm.r0c, rm.c1]
    v10, v11 = src[rm.r1, rm.c0c], src[rm.r1, rm.c1]
    f00, f01 = np.isfinite(v00), np.isfinite(v01)
    f10, f11 = np.isfinite(v10), np.isfinite(v11)
    w00 = rm.w00 * f00
    w01 = rm.w01 * f01
    w10 = rm.w10 * f10
    w11 = rm.w11 * f11
    wsum = w00 + w01 + w10 + w11
    val = (
        np.where(f00, v00, 0.0) * w00
        + np.where(f01, v01, 0.0) * w01
        + np.where(f10, v10, 0.0) * w10
        + np.where(f11, v11, 0.0) * w11
    )
    out_f = np.full((height, width), np.nan, dtype=np.float32)
    ok = rm.inside_bi & (wsum > 1e-6)
    out_f[ok] = (val[ok] / wsum[ok]).astype(np.float32)
    return out_f, rm.bounds_wgs84


def write_wgs84_geotiff(
    path: Path,
    array: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    nodata: float = -9999.0,
) -> Path:
    """Write an uncompressed single-band EPSG:4326 GeoTIFF readable by Pillow and QGIS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError(f"Expected a 2D array, got shape={arr.shape}")
    height, width = arr.shape
    west, south, east, north = bounds_wgs84

    if np.issubdtype(arr.dtype, np.integer) and arr.dtype == np.uint8:
        out_arr = arr.astype(np.uint8)
        mode = "L"
        nodata_str = str(int(nodata))
    else:
        out_arr = np.where(np.isnan(arr), np.float32(nodata), arr.astype(np.float32))
        mode = "F"
        nodata_str = str(float(nodata))

    img = Image.fromarray(out_arr, mode=mode)
    ifd = ImageFileDirectory_v2()
    scale_x = (east - west) / float(width)
    scale_y = (north - south) / float(height)
    ifd[33550] = (scale_x, scale_y, 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, float(west), float(north), 0.0)
    ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326)
    ifd[42113] = nodata_str
    img.save(path, format="TIFF", tiffinfo=ifd)
    return path
