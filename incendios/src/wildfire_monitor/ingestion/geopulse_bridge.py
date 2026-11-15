"""GeoPulse wildfire bridge: Planetary Computer arrays, physics baseline, GeoTIFF I/O & vectors."""

from __future__ import annotations

import math
import struct
import urllib.parse
import warnings
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

import contourpy
import numpy as np
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2
from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid

from wildfire_monitor.config import BoundingBox, Settings
from wildfire_monitor.ingestion.native_catalog import (
    NATIVE_CATALOG,
    S1_BANDS,
    S2_BANDS,
    STACSceneSummary,
    match_orbits,
    search_stac_scenes,
    select_scenes,
)
from wildfire_monitor.pc_auth import PlanetaryComputerClient, initialize_planetary_computer

S2_INVALID_SCL: frozenset[int] = frozenset({0, 1, 3, 8, 9, 10})
THRESHOLD: float = 0.5
REVIEW_UNCERTAINTY: float = 0.8
IGNORE: int = 255
SEVERITY_CLASSES: tuple[str, ...] = ("low", "moderate-low", "moderate-high", "high")

# Exact physics constants from geopulse/baseline.py and geopulse/app/src/engine.js
PHYSICS: dict[str, Any] = {
    "VV_WATER_DB": -18.0,
    "VV_DROP_DB": 3.0,
    "VV_DROP_CEILING_DB": -11.0,
    "MNDWI_WATER": 0.0,
    "DNBR_BURN": 0.1,
    "DNBR_SEVERITY": (0.1, 0.27, 0.44, 0.66),
    "NDVI_FUEL": 0.1,
    "NDVI_DROP": 0.2,
    "NDVI_CANOPY": 0.5,
    "VH_DROP_DB": 2.0,
    "VH_CANOPY_DB": -17.0,
    "SAR_ONLY_BURN_UNCERTAINTY": 0.5,
    "CHANGE_DB": 3.0,
    "CHANGE_INDEX": 0.2,
}

# Ordered longest-suffix-first for wildfire GeoTIFF metadata normalization.
WILDFIRE_PRODUCT_LAYERS: tuple[tuple[str, str], ...] = (
    ("burn_probability.tif", "burn_probability"),
    ("burn_severity.tif", "burn_severity"),
    ("change_probability.tif", "change_probability"),
    ("uncertainty.tif", "uncertainty"),
    ("s2_pre_nbr.tif", "s2_pre_nbr"),
    ("s2_post_nbr.tif", "s2_post_nbr"),
    ("s1_pre_vv.tif", "s1_pre_vv"),
    ("s1_post_vv.tif", "s1_post_vv"),
    ("s1_pre_vh.tif", "s1_pre_vh"),
    ("s1_post_vh.tif", "s1_post_vh"),
    ("dem_elevation.tif", "dem_elevation"),
)

_DATETIME_PATTERN = __import__("re").compile(r"(20\d{2}|19\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})")
_DATE_PATTERN = __import__("re").compile(r"(20\d{2}|19\d{2})-?(\d{2})-?(\d{2})")
_SANITIZE_ASSET_RE = __import__("re").compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class GeoGrid:
    """Regular geographic grid over a WGS84 bounding box with metric cell sizes."""

    bbox: BoundingBox
    width: int
    height: int
    dx_deg: float
    dy_deg: float
    pixel_width_m: float
    pixel_height_m: float

    @property
    def pixel_km2(self) -> float:
        return (self.pixel_width_m * self.pixel_height_m) / 1e6

    @property
    def crs(self) -> str:
        return "EPSG:4326"


@dataclass(frozen=True)
class GeoRasterData:
    """In-memory GeoTIFF grid decoded in pure Python."""

    data: np.ndarray
    bounds_wgs84: tuple[float, float, float, float]
    crs_epsg: int
    nodata: float | None = None
    x0: float = 0.0
    y0: float = 0.0
    rx: float = 0.0
    ry: float = 0.0


@dataclass(frozen=True)
class GeoTiffMetadata:
    """Normalized catalog metadata extracted from a wildfire GeoTIFF path."""

    local_path: Path
    asset_name: str
    sensor: str
    product_layer: str
    start_time_utc: str | None
    end_time_utc: str | None
    properties: dict[str, str | int | float] = field(default_factory=dict)


@dataclass
class WildfireAnalysisResult:
    """Complete GeoPulse wildfire outputs aligned on a GeoGrid."""

    grid: GeoGrid
    burn_probability: np.ndarray
    burn_severity: np.ndarray | None
    change_probability: np.ndarray
    uncertainty: np.ndarray
    arrays: dict[str, np.ndarray]
    scenes: dict[str, list[dict[str, Any]]]
    warnings: list[str]
    summary: dict[str, Any]
    burn_extent_geojson: dict[str, Any]
    aoi_geojson: dict[str, Any]


# ------------------------------------------------------------------ Grid & UTM Krüger math


def make_geo_grid(bbox: BoundingBox, max_dim: int = 512) -> GeoGrid:
    """Create a proportional WGS84 grid covering `bbox` with max dimension `max_dim`."""
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2.0
    width_m = (bbox.max_lon - bbox.min_lon) * 111320.0 * math.cos(math.radians(mid_lat))
    height_m = (bbox.max_lat - bbox.min_lat) * 110574.0
    if width_m >= height_m:
        width = max(32, int(max_dim))
        height = max(32, int(round(max_dim * (height_m / max(width_m, 1.0)))))
    else:
        height = max(32, int(max_dim))
        width = max(32, int(round(max_dim * (width_m / max(height_m, 1.0)))))
    return GeoGrid(
        bbox=bbox,
        width=width,
        height=height,
        dx_deg=(bbox.max_lon - bbox.min_lon) / width,
        dy_deg=(bbox.max_lat - bbox.min_lat) / height,
        pixel_width_m=width_m / width,
        pixel_height_m=height_m / height,
    )


def utm_to_lonlat(x: float | np.ndarray, y: float | np.ndarray, epsg: int) -> tuple[Any, Any]:
    """WGS84 UTM -> (lon, lat) inverse Krüger n^6 series (identical to geopulse/engine.js)."""
    if epsg == 4326:
        return x, y
    if 32601 <= epsg <= 32660:
        zone, south = epsg - 32600, False
    elif 32701 <= epsg <= 32760:
        zone, south = epsg - 32700, True
    else:
        raise ValueError(f"Unsupported EPSG code for pure-Python reprojection: EPSG:{epsg}")

    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    n = f / (2.0 - f)
    e = math.sqrt(f * (2.0 - f))
    n2, n3, n4, n5, n6 = n**2, n**3, n**4, n**5, n**6
    a_rect = (a / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0 + n6 / 256.0)
    beta = (
        n / 2.0 - (2.0 * n2) / 3.0 + (37.0 * n3) / 96.0 - n4 / 360.0 - (81.0 * n5) / 512.0 + (96199.0 * n6) / 604800.0,
        n2 / 48.0 + n3 / 15.0 - (437.0 * n4) / 1440.0 + (46.0 * n5) / 105.0 - (1118711.0 * n6) / 3870720.0,
        (17.0 * n3) / 480.0 - (37.0 * n4) / 840.0 - (209.0 * n5) / 4480.0 + (5569.0 * n6) / 90720.0,
        (4397.0 * n4) / 161280.0 - (11.0 * n5) / 504.0 - (830251.0 * n6) / 7257600.0,
        (4583.0 * n5) / 161280.0 - (108847.0 * n6) / 3991680.0,
        (20648693.0 * n6) / 638668800.0,
    )
    rad = math.pi / 180.0
    lon0 = ((zone - 1) * 6.0 - 180.0 + 3.0) * rad
    fn = 10000000.0 if south else 0.0

    xi = (y - fn) / (k0 * a_rect)
    eta = (x - 500000.0) / (k0 * a_rect)
    xi_p = xi
    eta_p = eta
    for j, b_j in enumerate(beta, start=1):
        xi_p = xi_p - b_j * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p = eta_p - b_j * np.cos(2 * j * xi) * np.sinh(2 * j * eta)

    sinh_eta = np.sinh(eta_p)
    sin_xi = np.sin(xi_p)
    cos_xi = np.cos(xi_p)
    tau_p = sin_xi / np.sqrt(sinh_eta * sinh_eta + cos_xi * cos_xi)
    tau = np.array(tau_p, dtype=np.float64)
    e2 = e * e
    for _ in range(12):
        sigma = np.sinh(e * np.arctanh((e * tau) / np.sqrt(1.0 + tau * tau)))
        tau_i = tau * np.sqrt(1.0 + sigma * sigma) - sigma * np.sqrt(1.0 + tau * tau)
        d = ((tau_p - tau_i) / np.sqrt(1.0 + tau_i * tau_i)) * (
            (1.0 + (1.0 - e2) * tau * tau) / ((1.0 - e2) * np.sqrt(1.0 + tau * tau))
        )
        tau = tau + d
        if np.max(np.abs(d)) < 1e-12:
            break

    lon = (np.arctan2(sinh_eta, cos_xi) + lon0) / rad
    lat = np.arctan(tau) / rad
    return lon, lat


def lonlat_to_utm(lon: float | np.ndarray, lat: float | np.ndarray, epsg: int) -> tuple[Any, Any]:
    """WGS84 (lon, lat) -> UTM (x, y) forward Krüger n^6 series (identical to geopulse/engine.js)."""
    if epsg == 4326:
        return lon, lat
    if 32601 <= epsg <= 32660:
        zone, south = epsg - 32600, False
    elif 32701 <= epsg <= 32760:
        zone, south = epsg - 32700, True
    else:
        raise ValueError(f"Unsupported EPSG code: EPSG:{epsg}")

    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    n = f / (2.0 - f)
    e = math.sqrt(f * (2.0 - f))
    n2, n3, n4, n5, n6 = n**2, n**3, n**4, n**5, n**6
    a_rect = (a / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0 + n6 / 256.0)
    alpha = (
        n / 2.0 - (2.0 * n2) / 3.0 + (5.0 * n3) / 16.0 + (41.0 * n4) / 180.0 - (127.0 * n5) / 288.0 + (7891.0 * n6) / 37800.0,
        (13.0 * n2) / 48.0 - (3.0 * n3) / 5.0 + (557.0 * n4) / 1440.0 + (281.0 * n5) / 630.0 - (1983433.0 * n6) / 1935360.0,
        (61.0 * n3) / 240.0 - (103.0 * n4) / 140.0 + (15061.0 * n5) / 26880.0 + (167603.0 * n6) / 181440.0,
        (49561.0 * n4) / 161280.0 - (179.0 * n5) / 168.0 + (6601661.0 * n6) / 7257600.0,
        (34729.0 * n5) / 80640.0 - (3418889.0 * n6) / 1995840.0,
        (212378941.0 * n6) / 319334400.0,
    )
    rad = math.pi / 180.0
    lon0 = ((zone - 1) * 6.0 - 180.0 + 3.0) * rad
    fn = 10000000.0 if south else 0.0

    phi = np.asarray(lat, dtype=np.float64) * rad
    lam = np.asarray(lon, dtype=np.float64) * rad - lon0
    tau = np.tan(phi)
    sigma = np.sinh(e * np.arctanh((e * tau) / np.sqrt(1.0 + tau * tau)))
    tau_p = tau * np.sqrt(1.0 + sigma * sigma) - sigma * np.sqrt(1.0 + tau * tau)
    xi_p = np.arctan2(tau_p, np.cos(lam))
    eta_p = np.arcsinh(np.sin(lam) / np.sqrt(tau_p * tau_p + np.cos(lam) ** 2))
    xi = np.array(xi_p, dtype=np.float64)
    eta = np.array(eta_p, dtype=np.float64)
    for j, a_j in enumerate(alpha, start=1):
        xi = xi + a_j * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta = eta + a_j * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    return 500000.0 + k0 * a_rect * eta, fn + k0 * a_rect * xi


# ------------------------------------------------------------------ Pure-Python GeoTIFF I/O


def write_sar_compatible_geotiff(
    path: Path,
    array: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    nodata: float = -9999.0,
    epsg: int = 4326,
) -> Path:
    """Write an uncompressed single-band GeoTIFF readable by `PIL.Image` and `sar-platform`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D raster array, got shape {arr.shape}")
    height, width = arr.shape
    west, south, east, north = bounds_wgs84

    if np.issubdtype(arr.dtype, np.integer) and arr.dtype == np.uint8:
        out_arr = arr.astype(np.uint8)
        pil_mode = "L"
        nodata_str = str(int(nodata))
    else:
        out_arr = np.where(np.isnan(arr), np.float32(nodata), arr.astype(np.float32))
        pil_mode = "F"
        nodata_str = str(float(nodata))

    img = Image.fromarray(out_arr, mode=pil_mode)
    ifd = ImageFileDirectory_v2()
    scale_x = (east - west) / float(width)
    scale_y = (north - south) / float(height)
    ifd[33550] = (scale_x, scale_y, 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, float(west), float(north), 0.0)
    if epsg == 4326:
        ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326)
    else:
        ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, int(epsg))
    ifd[42113] = nodata_str
    img.save(path, format="TIFF", tiffinfo=ifd)
    return path


_TIFF_TYPES: dict[int, tuple[str, int]] = {
    1: ("B", 1),
    2: ("c", 1),
    3: ("H", 2),
    4: ("I", 4),
    5: ("II", 8),
    6: ("b", 1),
    7: ("B", 1),
    8: ("h", 2),
    9: ("i", 4),
    10: ("ii", 8),
    11: ("f", 4),
    12: ("d", 8),
}


def _parse_tiff_tags(raw: bytes) -> tuple[str, dict[int, Any]]:
    endian = "<" if raw[:2] == b"II" else ">" if raw[:2] == b"MM" else ""
    if not endian:
        raise ValueError("Not a valid TIFF file.")
    magic, ifd_offset = struct.unpack(f"{endian}HI", raw[2:8])
    if magic != 42:
        raise ValueError(f"Unsupported BigTIFF/TIFF magic number: {magic}")
    count = struct.unpack(f"{endian}H", raw[ifd_offset : ifd_offset + 2])[0]
    tags: dict[int, Any] = {}
    pos = ifd_offset + 2
    for _ in range(count):
        tag_id, field_type, n_vals, val_offset = struct.unpack(
            f"{endian}HHII", raw[pos : pos + 12]
        )
        pos += 12
        fmt, unit_size = _TIFF_TYPES.get(field_type, ("B", 1))
        total_bytes = n_vals * unit_size
        if total_bytes <= 4:
            chunk = raw[pos - 4 : pos - 4 + total_bytes]
        else:
            chunk = raw[val_offset : val_offset + total_bytes]
        if field_type == 2:
            tags[tag_id] = chunk.rstrip(b"\x00").decode("utf-8", errors="replace")
        elif field_type in (5, 10):
            pairs = [
                struct.unpack(f"{endian}{fmt}", chunk[i * 8 : (i + 1) * 8])
                for i in range(n_vals)
            ]
            tags[tag_id] = tuple(a / b if b else 0.0 for a, b in pairs)
        else:
            vals = struct.unpack(f"{endian}{n_vals * fmt}", chunk)
            tags[tag_id] = vals[0] if n_vals == 1 else vals
    return endian, tags


def _unpredict_horizontal(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    np.cumsum(out, axis=1, out=out)
    return out


def _unpredict_floating_point(raw_bytes: bytes, width: int, rows: int, bps: int, endian: str) -> np.ndarray:
    bytes_per_sample = bps // 8
    stride = width * bytes_per_sample
    buf = np.frombuffer(raw_bytes[: rows * stride], dtype=np.uint8).reshape(rows, stride).copy()
    np.cumsum(buf, axis=1, out=buf)
    # GDAL / TIFF predictor=3 stores bytes in big-endian byte planes per row.
    unshuffled = buf.reshape(rows, bytes_per_sample, width).transpose(0, 2, 1)
    if endian == "<":
        unshuffled = unshuffled[:, :, ::-1]
    dtype = np.dtype(f"{endian}f{bytes_per_sample}")
    return np.frombuffer(unshuffled.tobytes(), dtype=dtype).reshape(rows, width).astype(np.float32)


def _decode_compressed_single_band_tiff(raw: bytes, endian: str, tags: dict[int, Any]) -> np.ndarray:
    width = int(tags[256])
    height = int(tags[257])
    bps = int(tags[258] if isinstance(tags[258], int) else tags[258][0])
    compression = int(tags.get(259, 1))
    sample_format = int(tags.get(339, 1) if isinstance(tags.get(339, 1), int) else tags[339][0])
    predictor = int(tags.get(317, 1))

    if sample_format == 3 and bps == 32:
        np_dtype = np.dtype(f"{endian}f4")
    elif sample_format == 2 and bps == 16:
        np_dtype = np.dtype(f"{endian}i2")
    elif bps == 16:
        np_dtype = np.dtype(f"{endian}u2")
    else:
        np_dtype = np.dtype("u1")

    def _decompress(chunk: bytes) -> bytes:
        if compression == 1:
            return chunk
        if compression in (8, 32946):
            return zlib.decompress(chunk)
        raise ValueError(f"Unsupported TIFF compression code: {compression}")

    if 324 in tags and 325 in tags:
        # Tiled TIFF (e.g. Cloud Optimized GeoTIFF from GeoPulse)
        tw = int(tags[322])
        th = int(tags[323])
        offsets = (tags[324],) if isinstance(tags[324], int) else tags[324]
        counts = (tags[325],) if isinstance(tags[325], int) else tags[325]
        tiles_across = math.ceil(width / tw)
        canvas = np.zeros((height, width), dtype=np.float32 if sample_format == 3 else np_dtype)
        for idx, (off, cnt) in enumerate(zip(offsets, counts)):
            ty, tx = divmod(idx, tiles_across)
            r0, c0 = ty * th, tx * tw
            dec = _decompress(raw[off : off + cnt])
            if predictor == 3 and sample_format == 3:
                tile_arr = _unpredict_floating_point(dec, tw, th, bps, endian)
            else:
                tile_arr = np.frombuffer(dec[: tw * th * (bps // 8)], dtype=np_dtype).reshape(th, tw)
                if predictor == 2:
                    tile_arr = _unpredict_horizontal(tile_arr)
            rh = min(th, height - r0)
            cw = min(tw, width - c0)
            canvas[r0 : r0 + rh, c0 : c0 + cw] = tile_arr[:rh, :cw]
        return canvas

    # Stripped TIFF
    rows_per_strip = int(tags.get(278, height))
    offsets = (tags[273],) if isinstance(tags[273], int) else tags[273]
    counts = (tags[279],) if isinstance(tags[279], int) else tags[279]
    canvas = np.zeros((height, width), dtype=np.float32 if sample_format == 3 else np_dtype)
    for idx, (off, cnt) in enumerate(zip(offsets, counts)):
        r0 = idx * rows_per_strip
        rh = min(rows_per_strip, height - r0)
        if rh <= 0:
            break
        dec = _decompress(raw[off : off + cnt])
        if predictor == 3 and sample_format == 3:
            strip_arr = _unpredict_floating_point(dec, width, rh, bps, endian)
        else:
            strip_arr = np.frombuffer(dec[: width * rh * (bps // 8)], dtype=np_dtype).reshape(rh, width)
            if predictor == 2:
                strip_arr = _unpredict_horizontal(strip_arr)
        canvas[r0 : r0 + rh, :] = strip_arr
    return canvas


def _extract_georef_from_tags(
    tags: dict[int, Any],
    width: int,
    height: int,
) -> tuple[int, float, float, float, float, tuple[float, float, float, float], float | None]:
    epsg = 4326
    geokeys = tags.get(34735)
    if geokeys and len(geokeys) >= 4:
        num_keys = int(geokeys[3])
        for idx in range(num_keys):
            base = 4 + idx * 4
            if base + 3 >= len(geokeys):
                break
            key_id, _loc, _cnt, val_offset = geokeys[base : base + 4]
            if key_id == 3072:
                epsg = int(val_offset)
                break
            if key_id == 2048 and epsg == 4326:
                epsg = int(val_offset)

    scale = tags.get(33550)
    tie = tags.get(33922)
    matrix = tags.get(34264)
    if scale and tie and len(scale) >= 2 and len(tie) >= 6:
        rx = float(scale[0])
        ry = -abs(float(scale[1]))
        x0 = float(tie[3]) - float(tie[0]) * rx
        y0 = float(tie[4]) - float(tie[1]) * ry
    elif matrix and len(matrix) >= 8:
        rx = float(matrix[0])
        ry = float(matrix[5])
        x0 = float(matrix[3])
        y0 = float(matrix[7])
    else:
        raise ValueError("GeoTIFF is missing georeferencing tags (33550/33922 or 34264).")

    x1 = x0 + width * rx
    y1 = y0 + height * ry
    if epsg == 4326:
        bounds = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    else:
        corners_x = np.array([x0, x1, x0, x1], dtype=np.float64)
        corners_y = np.array([y0, y0, y1, y1], dtype=np.float64)
        lons, lats = utm_to_lonlat(corners_x, corners_y, epsg)
        bounds = (float(np.min(lons)), float(np.min(lats)), float(np.max(lons)), float(np.max(lats)))

    nodata_raw = tags.get(42113)
    nodata: float | None = None
    if nodata_raw is not None:
        try:
            nodata = float(str(nodata_raw).strip())
        except ValueError:
            nodata = None
    return epsg, x0, y0, rx, ry, bounds, nodata


def read_geopulse_geotiff(path: Path) -> GeoRasterData:
    """Read an uncompressed or DEFLATE GeoPulse GeoTIFF in pure Python."""
    raw = Path(path).read_bytes()
    endian, tags = _parse_tiff_tags(raw)
    width = int(tags[256])
    height = int(tags[257])
    epsg, x0, y0, rx, ry, bounds, nodata = _extract_georef_from_tags(tags, width, height)

    compression = int(tags.get(259, 1))
    if compression == 1:
        try:
            with Image.open(path) as img:
                arr = np.array(img)
        except Exception:
            arr = _decode_compressed_single_band_tiff(raw, endian, tags)
    else:
        arr = _decode_compressed_single_band_tiff(raw, endian, tags)

    if nodata is not None:
        if math.isnan(nodata):
            pass
        elif np.issubdtype(arr.dtype, np.floating):
            arr = np.where(arr == nodata, np.nan, arr)

    return GeoRasterData(
        data=arr,
        bounds_wgs84=bounds,
        crs_epsg=epsg,
        nodata=nodata,
        x0=x0,
        y0=y0,
        rx=rx,
        ry=ry,
    )


def reproject_raster_to_wgs84_grid(raster: GeoRasterData, grid: GeoGrid, *, nearest: bool = False) -> np.ndarray:
    """Resample a GeoRasterData (EPSG:4326 or UTM) onto a target WGS84 GeoGrid."""
    west, south, east, north = grid.bbox.to_ee_bounds()
    cols = np.arange(grid.width, dtype=np.float64) + 0.5
    rows = np.arange(grid.height, dtype=np.float64) + 0.5
    lons = west + cols * grid.dx_deg
    lats = north - rows * grid.dy_deg
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    if raster.crs_epsg == 4326:
        sx, sy = lon_grid, lat_grid
    else:
        sx, sy = lonlat_to_utm(lon_grid, lat_grid, raster.crs_epsg)

    src_col = (sx - raster.x0) / raster.rx
    src_row = (sy - raster.y0) / raster.ry
    src_h, src_w = raster.data.shape
    ci = np.floor(src_col).astype(np.int64)
    ri = np.floor(src_row).astype(np.int64)
    valid = (ci >= 0) & (ci < src_w) & (ri >= 0) & (ri < src_h)

    if np.issubdtype(raster.data.dtype, np.integer) or nearest:
        fill = int(raster.nodata) if raster.nodata is not None else 0
        out = np.full((grid.height, grid.width), fill, dtype=raster.data.dtype)
        out[valid] = raster.data[ri[valid], ci[valid]]
        return out

    out_f = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    out_f[valid] = raster.data[ri[valid], ci[valid]].astype(np.float32)
    return out_f


# ------------------------------------------------------------------ Planetary Computer TiTiler fetch & composite


def _fetch_titiler_npy(
    client: PlanetaryComputerClient,
    collection: str,
    item_id: str,
    bbox: BoundingBox,
    width: int,
    height: int,
    assets: Sequence[str],
    resampling: Literal["bilinear", "nearest"] = "bilinear",
) -> np.ndarray:
    bbox_str = f"{bbox.min_lon},{bbox.min_lat},{bbox.max_lon},{bbox.max_lat}"
    params = [("collection", collection), ("item", item_id), ("resampling", resampling)]
    for asset in assets:
        params.append(("assets", asset))
    query = urllib.parse.urlencode(params)
    url = f"{client.config.titiler_api_url}/item/bbox/{bbox_str}/{width}x{height}.npy?{query}"
    raw = client.request_bytes(url)
    import io

    return np.load(io.BytesIO(raw), allow_pickle=False)


def fetch_s2_composite(
    scenes: Sequence[STACSceneSummary],
    grid: GeoGrid,
    client: PlanetaryComputerClient,
) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    """Fetch and median-composite Sentinel-2 L2A bands matching GeoPulse SCL & PB04 rules."""
    west, south, east, north = grid.bbox.to_ee_bounds()
    bbox = BoundingBox(min_lon=west, min_lat=south, max_lon=east, max_lat=north)
    stack: list[np.ndarray] = []
    used: list[dict[str, Any]] = []
    invalid_vals = list(S2_INVALID_SCL)

    for scene in scenes:
        scl_arr = _fetch_titiler_npy(
            client, scene.collection, scene.item_id, bbox, grid.width, grid.height, ["SCL"], resampling="nearest"
        )
        scl = scl_arr[0]
        scl_mask = scl_arr[1] > 0 if scl_arr.shape[0] > 1 else np.ones_like(scl, dtype=bool)
        ok = scl_mask & (~np.isin(scl, invalid_vals))
        frac = float(np.mean(ok))
        if frac < 0.02:
            continue

        bands_raw = _fetch_titiler_npy(
            client, scene.collection, scene.item_id, bbox, grid.width, grid.height, S2_BANDS, resampling="bilinear"
        )
        has_alpha = bands_raw.shape[0] == len(S2_BANDS) + 1
        alpha_ok = bands_raw[-1] > 0 if has_alpha else ok
        valid = ok & alpha_ok
        offset = -1000.0 if scene.processing_baseline >= 4.0 else 0.0
        bands = np.full((len(S2_BANDS), grid.height, grid.width), np.nan, dtype=np.float32)
        for idx in range(len(S2_BANDS)):
            raw_band = bands_raw[idx].astype(np.float32)
            bands[idx, valid] = (raw_band[valid] + offset) / 10000.0
        stack.append(bands)
        used.append(scene.to_geopulse_record(valid_fraction=frac))

    if not stack:
        return None, []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(all="ignore"):
            composite = np.nanmedian(np.stack(stack, axis=0), axis=0).astype(np.float32)
    return composite, used


def fetch_s1_composite(
    scenes: Sequence[STACSceneSummary],
    grid: GeoGrid,
    client: PlanetaryComputerClient,
) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    """Fetch and median-composite Sentinel-1 RTC linear intensity -> dB (matching GeoPulse)."""
    bbox = grid.bbox
    stack: list[np.ndarray] = []
    used: list[dict[str, Any]] = []

    for scene in scenes:
        raw = _fetch_titiler_npy(
            client, scene.collection, scene.item_id, bbox, grid.width, grid.height, S1_BANDS, resampling="bilinear"
        )
        has_alpha = raw.shape[0] == len(S1_BANDS) + 1
        alpha_ok = raw[-1] > 0 if has_alpha else np.ones((grid.height, grid.width), dtype=bool)
        bands = raw[: len(S1_BANDS)].astype(np.float32).copy()
        valid = alpha_ok & np.all(bands > 0, axis=0)
        frac = float(np.mean(valid))
        if frac < 0.02:
            continue
        for idx in range(len(S1_BANDS)):
            bands[idx] = np.where((bands[idx] > 0) & alpha_ok, bands[idx], np.nan)
        stack.append(bands)
        used.append(scene.to_geopulse_record(valid_fraction=frac))

    if not stack:
        return None, []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(all="ignore"):
            med = np.nanmedian(np.stack(stack, axis=0), axis=0).astype(np.float32)
            db = np.where(med > 0, 10.0 * np.log10(np.maximum(med, 1e-10)), np.nan).astype(np.float32)
    return db, used


def fetch_dem_and_slope(
    scenes: Sequence[STACSceneSummary],
    grid: GeoGrid,
    client: PlanetaryComputerClient,
) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    """Fetch Copernicus GLO-30 DEM and compute terrain slope in degrees."""
    if not scenes:
        return None, []
    tiles: list[np.ndarray] = []
    used: list[dict[str, Any]] = []
    for scene in scenes[:4]:
        raw = _fetch_titiler_npy(
            client, scene.collection, scene.item_id, grid.bbox, grid.width, grid.height, ["data"], resampling="bilinear"
        )
        has_alpha = raw.shape[0] > 1
        elev = raw[0].astype(np.float32).copy()
        if has_alpha:
            elev[raw[1] == 0] = np.nan
        tiles.append(elev)
        used.append(scene.to_geopulse_record())

    with np.errstate(all="ignore"):
        elev_mosaic = np.nanmean(np.stack(tiles, axis=0), axis=0).astype(np.float32)
    elev_filled = np.where(np.isnan(elev_mosaic), 0.0, elev_mosaic)
    gy, gx = np.gradient(elev_filled, grid.pixel_height_m, grid.pixel_width_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
    return np.stack([elev_mosaic, slope], axis=0).astype(np.float32), used


# ------------------------------------------------------------------ Exact GeoPulse Wildfire Physics & Contours


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    clipped = np.clip(x, -30.0, 30.0)
    return (1.0 / (1.0 + np.exp(-clipped))).astype(np.float32)


def _norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a - b) / (a + b + 1e-6)


def binary_entropy(prob: np.ndarray) -> np.ndarray:
    """Compute binary Shannon entropy in bits matching GeoPulse `binary_entropy`."""
    q = np.clip(prob, 1e-6, 1.0 - 1e-6)
    return (-(q * np.log2(q) + (1.0 - q) * np.log2(1.0 - q))).astype(np.float32)


def compute_wildfire_physics(
    arrays: dict[str, np.ndarray],
    grid: GeoGrid,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray]:
    """Run GeoPulse's exact wildfire physics baseline (dNBR + NDVI fuel + VH drop + Key & Benson severity)."""
    h, w = grid.height, grid.width
    has_s2 = "s2_pre" in arrays and "s2_post" in arrays
    has_s1 = "s1_pre" in arrays and "s1_post" in arrays
    if not (has_s2 or has_s1):
        raise ValueError("Wildfire baseline requires both pre and post observations from S2 or S1.")

    p_cfg = PHYSICS
    with np.errstate(all="ignore"):
        if has_s2:
            s2_pre, s2_post = arrays["s2_pre"], arrays["s2_post"]
            nbr_pre = _norm_diff(s2_pre[3], s2_pre[5])
            nbr_post = _norm_diff(s2_post[3], s2_post[5])
            dnbr = nbr_pre - nbr_post
            ndvi_pre = _norm_diff(s2_pre[3], s2_pre[2])
            ndvi_post = _norm_diff(s2_post[3], s2_post[2])
            mndwi_pre = _norm_diff(s2_pre[1], s2_pre[4])
            mndwi_post = _norm_diff(s2_post[1], s2_post[4])
            s2_burn = _sigmoid((dnbr - p_cfg["DNBR_BURN"]) / 0.025) * _sigmoid(
                (ndvi_pre - p_cfg["NDVI_FUEL"]) / 0.03
            )
            raw_sev = np.zeros((h, w), dtype=np.uint8)
            for thresh in p_cfg["DNBR_SEVERITY"]:
                raw_sev += (dnbr >= thresh).astype(np.uint8)
            raw_sev = np.where(np.isnan(dnbr), IGNORE, raw_sev).astype(np.uint8)
        else:
            s2_burn = np.full((h, w), np.nan, dtype=np.float32)
            mndwi_pre = None
            raw_sev = None

        if has_s1:
            s1_pre, s1_post = arrays["s1_pre"], arrays["s1_post"]
            s1_burn = _sigmoid((s1_pre[1] - s1_post[1] - p_cfg["VH_DROP_DB"]) / 0.6) * _sigmoid(
                (s1_pre[1] - p_cfg["VH_CANOPY_DB"]) / 1.0
            )
        else:
            s1_burn = np.full((h, w), np.nan, dtype=np.float32)

        sar_only = np.isnan(s2_burn) & ~np.isnan(s1_burn)
        target = np.where(sar_only, s1_burn, s2_burn).astype(np.float32)

        # Pre-event open water physical constraint: water never burns.
        w_pre = np.full((h, w), np.nan, dtype=np.float32)
        if "s1_pre" in arrays:
            w_pre = _sigmoid((p_cfg["VV_WATER_DB"] - arrays["s1_pre"][0]) / 1.5)
        if mndwi_pre is not None:
            w_s2 = _sigmoid((mndwi_pre - p_cfg["MNDWI_WATER"]) / 0.05)
            w_pre = np.where(~np.isnan(w_s2), w_s2, w_pre)
        pre_water_mask = np.nan_to_num(w_pre, nan=0.0) > 0.8

        uncertainty = binary_entropy(target)
        uncertainty = np.where(
            sar_only,
            np.fmax(uncertainty, np.float32(p_cfg["SAR_ONLY_BURN_UNCERTAINTY"])),
            uncertainty,
        ).astype(np.float32)

        target = np.where(pre_water_mask & ~np.isnan(target), np.float32(0.0), target)
        uncertainty = np.where(pre_water_mask & ~np.isnan(uncertainty), np.float32(0.0), uncertainty)
        uncertainty = np.where(np.isnan(target), np.nan, uncertainty)

        change_terms: list[np.ndarray] = []
        if has_s1:
            d_s1 = np.maximum(np.abs(s1_post[0] - s1_pre[0]), np.abs(s1_post[1] - s1_pre[1]))
            change_terms.append(_sigmoid((d_s1 - p_cfg["CHANGE_DB"]) / 0.5))
        if has_s2:
            d_s2 = np.maximum.reduce(
                [
                    np.abs(mndwi_post - mndwi_pre),
                    np.abs(ndvi_post - ndvi_pre),
                    np.abs(nbr_post - nbr_pre),
                ]
            )
            change_terms.append(_sigmoid((d_s2 - p_cfg["CHANGE_INDEX"]) / 0.05))
        if change_terms:
            change = np.nanmean(np.stack(change_terms, axis=0), axis=0).astype(np.float32)
        else:
            change = target.copy()
        change = np.where(np.isnan(target), np.nan, change)

        if raw_sev is not None:
            valid_sev = (~np.isnan(target)) & (raw_sev != IGNORE)
            severity = np.where(
                valid_sev,
                np.where(target >= THRESHOLD, raw_sev, 0),
                IGNORE,
            ).astype(np.uint8)
        else:
            severity = None

    return target, severity, change, uncertainty


def _iter_polygons(geom: Any) -> list[Polygon]:
    """Recursively extract non-empty Polygon parts from any Shapely geometry."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for sub in geom.geoms:
            out.extend(_iter_polygons(sub))
        return out
    return []


def _round_coords(coords: Sequence[Sequence[float]], decimals: int = 6) -> list[list[float]]:
    return [[round(float(x), decimals), round(float(y), decimals)] for x, y in coords]


def extract_burn_extent_geojson(
    burn_probability: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    threshold: float = THRESHOLD,
    min_area_km2: float = 0.002,
    max_features: int = 5000,
    step: int = 1,
    simplify_deg: float = 0.0,
) -> dict[str, Any]:
    """Extract GeoJSON Polygon/MultiPolygon burn perimeter contours (GeoPulse threshold >= 0.5)."""
    values = np.nan_to_num(burn_probability.astype(np.float64), nan=0.0)
    height, width = values.shape
    west, south, east, north = bounds_wgs84
    dx = (east - west) / float(width)
    dy = (north - south) / float(height)
    mid_lat = (south + north) / 2.0
    m_per_lon = 111320.0 * math.cos(math.radians(mid_lat))
    m_per_lat = 110574.0

    # Contour generator expects cell-center ascending y coordinates.
    xs = west + (np.arange(width) + 0.5) * dx
    ys = south + (np.arange(height) + 0.5) * dy
    cg = contourpy.contour_generator(x=xs, y=ys, z=values[::-1, :], fill_type=contourpy.FillType.OuterOffset)
    verts_chunks, offsets_chunks = cg.filled(float(threshold), 1.01)

    features: list[dict[str, Any]] = []
    for poly_verts, ring_offsets in zip(verts_chunks, offsets_chunks):
        if poly_verts is None or ring_offsets is None or len(ring_offsets) < 2:
            continue
        ext_ring = [
            (float(x), float(y))
            for x, y in poly_verts[ring_offsets[0] : ring_offsets[1]]
        ]
        if len(ext_ring) < 4:
            continue
        if ext_ring[0] != ext_ring[-1]:
            ext_ring.append(ext_ring[0])
        hole_rings = []
        for idx in range(1, len(ring_offsets) - 1):
            hole = [
                (float(x), float(y))
                for x, y in poly_verts[ring_offsets[idx] : ring_offsets[idx + 1]]
            ]
            if len(hole) >= 4:
                if hole[0] != hole[-1]:
                    hole.append(hole[0])
                hole_poly = Polygon(hole)
                if (hole_poly.area * m_per_lon * m_per_lat) / 1e6 >= min_area_km2 * 0.25:
                    hole_rings.append(hole)

        poly = Polygon(ext_ring, hole_rings)
        if not poly.is_valid:
            poly = make_valid(poly)
        for g in _iter_polygons(poly):
            if simplify_deg > 0:
                g = g.simplify(simplify_deg, preserve_topology=True)
            for pg in _iter_polygons(g):
                area_km2 = (pg.area * m_per_lon * m_per_lat) / 1e6
                if area_km2 < min_area_km2:
                    continue
                geom_dict = mapping(pg)
                geom_dict["coordinates"] = [
                    _round_coords(ring, 6) for ring in geom_dict["coordinates"]
                ]
                features.append(
                    {
                        "type": "Feature",
                        "geometry": geom_dict,
                        "properties": {
                            "area_km2": round(float(area_km2), 5),
                            "step": step,
                        },
                    }
                )

    features.sort(key=lambda feat: feat["properties"]["area_km2"], reverse=True)
    return {
        "type": "FeatureCollection",
        "features": features[:max_features],
    }


def summarize_wildfire_result(
    *,
    grid: GeoGrid,
    burn_probability: np.ndarray,
    uncertainty: np.ndarray,
    burn_severity: np.ndarray | None,
    scenes: dict[str, list[dict[str, Any]]],
    warnings: list[str],
    runtime_s: float = 0.0,
    sensors: Sequence[str] = ("s1", "s2"),
    model_id: str = "threshold-baseline",
    model_version: str = "0.1.0",
) -> dict[str, Any]:
    """Compute exact GeoPulse `summary.json` statistics."""
    valid = ~np.isnan(burn_probability)
    observed = int(np.count_nonzero(valid))
    hit_mask = valid & (burn_probability >= THRESHOLD)
    hit = int(np.count_nonzero(hit_mask))
    review = int(np.count_nonzero(valid & (uncertainty >= REVIEW_UNCERTAINTY)))
    px_km2 = grid.pixel_km2

    if hit > 0:
        conf_vals = np.maximum(burn_probability[hit_mask], 1.0 - burn_probability[hit_mask])
        mean_conf = round(float(np.mean(conf_vals)), 3)
    else:
        mean_conf = None
    mean_unc = round(float(np.mean(uncertainty[valid])), 3) if observed > 0 else None

    summary: dict[str, Any] = {
        "task": "wildfire",
        "target": "burn",
        "affected_label": "burned",
        "model": {"id": model_id, "version": model_version},
        "aoi_km2": grid.bbox.area_km2(),
        "observed_km2": round(observed * px_km2, 3),
        "affected_km2": round(hit * px_km2, 3),
        "review_km2": round(review * px_km2, 3),
        "mean_confidence_affected": mean_conf,
        "mean_uncertainty": mean_unc,
        "modality_weights": None,
        "scenes": {
            key: len(scenes.get(key, []))
            for key in ("s1_pre", "s1_post", "s2_pre", "s2_post")
        },
        "warnings": list(warnings),
        "runtime_s": round(runtime_s, 2),
        "engine": "wildfire-monitor · GeoPulse physics baseline",
        "decision_policy": {
            "threshold": THRESHOLD,
            "review_if_uncertainty_ge": REVIEW_UNCERTAINTY,
        },
        "files": {
            "probability": "burn_probability.tif",
            "extent": "burn_extent.geojson",
        },
    }
    if burn_severity is not None:
        summary["severity_km2"] = {
            name: round(int(np.count_nonzero(burn_severity == idx)) * px_km2, 3)
            for idx, name in enumerate(SEVERITY_CLASSES, start=1)
        }
    return summary


# ------------------------------------------------------------------ Catalog metadata helpers (hola parity)


def _detect_sensor(filename_upper: str, explicit_sensor: str | None) -> str:
    if explicit_sensor:
        return explicit_sensor.upper()
    if filename_upper.startswith("S2"):
        return "SENTINEL-2"
    if filename_upper.startswith("S1"):
        return "SENTINEL-1"
    if filename_upper.startswith("DEM") or "COP" in filename_upper:
        return "COP-DEM"
    return "GEOPULSE"


def _detect_product_layer(filename_lower: str) -> str:
    for suffix, layer in WILDFIRE_PRODUCT_LAYERS:
        if filename_lower.endswith(suffix):
            return layer
    return "wildfire_geotiff"


def _extract_timestamps_utc(stem: str) -> tuple[str | None, str | None]:
    dt_matches = _DATETIME_PATTERN.findall(stem)
    parsed: list[datetime] = []
    for y, m, d, hh, mm, ss in dt_matches:
        try:
            parsed.append(datetime(int(y), int(m), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc))
        except ValueError:
            continue
    if not parsed:
        for y, m, d in _DATE_PATTERN.findall(stem):
            try:
                parsed.append(datetime(int(y), int(m), int(d), tzinfo=timezone.utc))
            except ValueError:
                continue
    if not parsed:
        return None, None
    parsed.sort()
    return parsed[0].strftime("%Y-%m-%dT%H:%M:%SZ"), parsed[-1].strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_asset_id_segment(name: str) -> str:
    """Convert an arbitrary filename stem into a valid catalog/asset identifier."""
    cleaned = _SANITIZE_ASSET_RE.sub("_", name).strip("_")
    return cleaned or "wildfire_asset"


def parse_wildfire_geotiff_metadata(
    path: Path,
    *,
    sensor: str | None = None,
    event_id: str | None = None,
    start_time_utc: str | None = None,
    end_time_utc: str | None = None,
) -> GeoTiffMetadata:
    """Extract normalized `catalog.json` metadata matching `sar-platform` & `hola`."""
    detected_sensor = _detect_sensor(path.name.upper(), sensor)
    product_layer = _detect_product_layer(path.name.lower())
    inferred_start, inferred_end = _extract_timestamps_utc(path.stem)
    resolved_start = start_time_utc or inferred_start
    resolved_end = end_time_utc or inferred_end

    props: dict[str, str | int | float] = {
        "source": "GEOPULSE_BRIDGE",
        "sensor": detected_sensor,
        "product_layer": product_layer,
        "source_filename": path.name,
    }
    if event_id:
        props["geopulse_event_id"] = event_id

    return GeoTiffMetadata(
        local_path=path,
        asset_name=sanitize_asset_id_segment(path.stem),
        sensor=detected_sensor,
        product_layer=product_layer,
        start_time_utc=resolved_start,
        end_time_utc=resolved_end,
        properties=props,
    )


def acquire_wildfire_scenes_and_physics(
    bbox: BoundingBox,
    before: str,
    after: str,
    *,
    sensors: Sequence[str] = ("s1", "s2"),
    aoi_geometry: dict[str, Any] | None = None,
    max_dim: int = 512,
    max_cloud_cover: int = 60,
    client: PlanetaryComputerClient | None = None,
    settings: Settings | None = None,
) -> WildfireAnalysisResult:
    """Fetch STAC scenes + TiTiler arrays from Planetary Computer and run GeoPulse wildfire physics."""
    import time

    t0 = time.perf_counter()
    resolved_settings = settings or Settings()
    pc_client = client or initialize_planetary_computer(resolved_settings)
    grid = make_geo_grid(bbox, max_dim=max_dim)
    warnings: list[str] = []
    arrays: dict[str, np.ndarray] = {}
    scenes_meta: dict[str, list[dict[str, Any]]] = {}

    if "s2" in sensors:
        s2_pre_items = search_stac_scenes(
            "sentinel2_l2a", bbox, before, max_cloud_cover=max_cloud_cover, client=pc_client
        )
        s2_post_items = search_stac_scenes(
            "sentinel2_l2a", bbox, after, max_cloud_cover=max_cloud_cover, client=pc_client
        )
        s2_pre_sel = select_scenes(s2_pre_items, "pre", how="closest")
        s2_post_sel = select_scenes(s2_post_items, "post", how="closest")
        s2_pre_arr, s2_pre_used = fetch_s2_composite(s2_pre_sel, grid, pc_client)
        s2_post_arr, s2_post_used = fetch_s2_composite(s2_post_sel, grid, pc_client)
        scenes_meta["s2_pre"] = s2_pre_used
        scenes_meta["s2_post"] = s2_post_used
        if s2_pre_arr is not None:
            arrays["s2_pre"] = s2_pre_arr
        else:
            warnings.append("s2_pre: no usable Sentinel-2 scenes (cloud cover or empty window).")
        if s2_post_arr is not None:
            arrays["s2_post"] = s2_post_arr
        else:
            warnings.append("s2_post: no usable Sentinel-2 scenes (cloud cover or empty window).")

    if "s1" in sensors:
        s1_pre_items = search_stac_scenes(
            "sentinel1_rtc", bbox, before, max_cloud_cover=None, client=pc_client
        )
        s1_post_items = search_stac_scenes(
            "sentinel1_rtc", bbox, after, max_cloud_cover=None, client=pc_client
        )
        s1_pre_sel, s1_post_sel, restricted = match_orbits(s1_pre_items, s1_post_items, how="closest")
        if restricted:
            warnings.append(f"s1: matched relative orbit(s) {restricted}.")
        s1_pre_arr, s1_pre_used = fetch_s1_composite(s1_pre_sel, grid, pc_client)
        s1_post_arr, s1_post_used = fetch_s1_composite(s1_post_sel, grid, pc_client)
        scenes_meta["s1_pre"] = s1_pre_used
        scenes_meta["s1_post"] = s1_post_used
        if s1_pre_arr is not None:
            arrays["s1_pre"] = s1_pre_arr
        else:
            warnings.append("s1_pre: no usable Sentinel-1 scenes.")
        if s1_post_arr is not None:
            arrays["s1_post"] = s1_post_arr
        else:
            warnings.append("s1_post: no usable Sentinel-1 scenes.")

    dem_items = search_stac_scenes("cop_dem", bbox, None, max_cloud_cover=None, limit=8, client=pc_client)
    dem_arr, dem_used = fetch_dem_and_slope(dem_items, grid, pc_client)
    if dem_arr is not None:
        arrays["dem"] = dem_arr
        scenes_meta["dem"] = dem_used

    burn_prob, burn_sev, change_prob, unc = compute_wildfire_physics(arrays, grid)
    extent_fc = extract_burn_extent_geojson(burn_prob, tuple(bbox.to_ee_bounds()), step=1)  # type: ignore[arg-type]

    if aoi_geometry is None:
        aoi_geometry = bbox.to_geojson_polygon()
    aoi_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Área de estudio"},
                "geometry": aoi_geometry,
            }
        ],
    }
    runtime_s = time.perf_counter() - t0
    summary = summarize_wildfire_result(
        grid=grid,
        burn_probability=burn_prob,
        uncertainty=unc,
        burn_severity=burn_sev,
        scenes=scenes_meta,
        warnings=warnings,
        runtime_s=runtime_s,
        sensors=sensors,
    )
    return WildfireAnalysisResult(
        grid=grid,
        burn_probability=burn_prob,
        burn_severity=burn_sev,
        change_probability=change_prob,
        uncertainty=unc,
        arrays=arrays,
        scenes=scenes_meta,
        warnings=warnings,
        summary=summary,
        burn_extent_geojson=extent_fc,
        aoi_geojson=aoi_fc,
    )
