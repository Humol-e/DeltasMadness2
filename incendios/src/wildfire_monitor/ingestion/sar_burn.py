"""All-weather SAR burned-area and surface-disturbance detection (pure NumPy/PIL).

Scientific & algorithmic basis
------------------------------
Combines three complementary open SAR methodologies without requiring optical bands:

1. **NASA OPERA ``DIST-S1`` / ``RTC`` normalization** (``opera-adt/dist-s1``, ``opera-adt/RTC``):
   Computes radiometrically smoothed co-/cross-pol backscatter differences in power space,
   applies robust median/MAD baseline standardization per acquisition pair, and fuses co/cross
   perturbations via a dual-polarization Mahalanobis distance metric. Because median/MAD removes
   global scene offsets and cross/co log-ratios cancel equal multiplicative gain changes, the
   metric remains resilient under dense smoke, cloud cover, and uniform soil-moisture shifts.
2. **Unsupervised SAR Burn Indices, GLCM Texture & K-Means**
   (``Jando93/Sentinel-1-unsupervised-burned-area-detection``):
   Evaluates Radar Burn Difference (``RBD``), Depolarized Log Radar Burn Ratio (``LogRBR``),
   ``ΔRVI``, ``ΔDPSVI``, ``5×5`` GLCM contrast texture change, and deterministic 4-cluster K-means
   ordered by burn severity.
3. **Vegetation Indices & Dual-Pol Covariance Omnibus Test**
   (``ErliPinto/Sentinel-1-SAR-vegetation-indices``, ``mortcanty/eesarseq``):
   Computes ``ΔRFDI`` and the Wishart/diagonal dual-pol covariance likelihood-ratio statistic
   ``-2 ln Q`` across ``VV/VH`` (Sentinel-1 C-band RTC) or ``HH/HV`` (NISAR L-band GCOV/RTC).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import contourpy
import numpy as np
import shapely
from PIL import Image
from PIL.TiffImagePlugin import ImageFileDirectory_v2
from shapely.geometry import MultiPolygon, Polygon, mapping

from wildfire_monitor.config import PROJECT_ROOT, BoundingBox

PolarizationMode = Literal["VV_VH", "HH_HV"]

POL_MODES: dict[PolarizationMode, dict[str, str]] = {
    "VV_VH": {
        "co": "VV",
        "cross": "VH",
        "pair": "VV+VH",
        "platform": "Sentinel-1A/B",
        "sensor": "C-SAR RTC",
        "provider": "Derived from Planetary Computer Sentinel-1 RTC (COPERNICUS)",
        "band_desc": "C-band VV+VH",
    },
    "HH_HV": {
        "co": "HH",
        "cross": "HV",
        "pair": "HH+HV",
        "platform": "NISAR (L-SAR)",
        "sensor": "L-SAR GCOV/RTC",
        "provider": "Derived from NASA NISAR L2 GCOV / RTC",
        "band_desc": "L-band HH+HV",
    },
}

CO_FLOOR_DB = -18.0
CROSS_FLOOR_DB = -24.0
CANOPY_MID_DB = -21.0
CANOPY_SCALE_DB = 1.5

OPERA_DIST_RANGE = (0.0, 4.0)
DELTA_RVI_RANGE = (-0.6, 0.3)
LOG_RBR_RANGE_DB = (-6.0, 3.0)
S1_PNG_VV_RANGE_DB = (-25.0, 0.0)
S1_PNG_VH_RANGE_DB = (-30.0, 0.0)

CLASS_UNBURNED = 0
CLASS_LOW_DIST = 1
CLASS_MOD_BURN = 2
CLASS_HIGH_BURN = 3
N_CLASSES = 3

DATASET_ID = "sar_burn"
VECTOR_ID = "sar-burn-extent"
FRAME_ID = "sar-burn-change"
DOWNLOAD_PREFIX = "sar-burn-"
DOWNLOAD_EXTRA_PREFIXES = ("sar-burn-", "sar-opera-", "sar-delta-")

CLASS_LEGEND_NOTE = "1 Low disturbance · 2 Moderate burn · 3 High burn"
DIST_LEGEND_NOTE = "Transparent < 0.5 σ · 1.5–2.5 Moderate · ≥ 2.5 High disturbance"
DRVI_LEGEND_NOTE = "Red/Orange = canopy structure loss · Transparent ≈ 0 · Green = regrowth"
LOGRBR_LEGEND_NOTE = "Rose/Orange = depolarization burn drop · Transparent ≈ 0 dB · Blue = moisture/gain"


# --------------------------------------------------------------------------- filtering & physics


@dataclass(frozen=True)
class SarBurnResult:
    """All-weather SAR burn classification and physical intermediate indices."""

    classes: np.ndarray  # uint8: 0=unburned/outside domain, 1=low, 2=moderate, 3=high
    opera_dist: np.ndarray  # float32 [0, 4], NaN outside domain
    rbd_db: np.ndarray  # float32 cross-pol post - pre (dB)
    log_rbr_db: np.ndarray  # float32 depolarized log ratio (dB)
    delta_rvi: np.ndarray  # float32 RVI post - pre
    delta_dpsvi: np.ndarray  # float32 DPSVI post - pre
    delta_rfdi: np.ndarray  # float32 RFDI post - pre
    delta_glcm_contrast: np.ndarray  # float32 5x5 GLCM contrast difference
    wishart_stat: np.ndarray  # float32 -2 ln Q dual-pol covariance statistic
    pol_mode: PolarizationMode


def _box_mean_nan(arr: np.ndarray, radius: int = 2) -> np.ndarray:
    """NaN-aware `(2*radius+1) x (2*radius+1)` uniform spatial filter in float32."""
    if radius <= 0:
        return np.asarray(arr, dtype=np.float32).copy()
    valid = np.isfinite(arr)
    vals = np.where(valid, arr, 0.0).astype(np.float32)
    mask = valid.astype(np.float32)
    k = 2 * radius + 1
    pad_v = np.pad(vals, radius, mode="edge")
    pad_m = np.pad(mask, radius, mode="edge")
    h, w = vals.shape
    sv = np.zeros((h, w), dtype=np.float32)
    sm = np.zeros((h, w), dtype=np.float32)
    for dy in range(k):
        for dx in range(k):
            sv += pad_v[dy : dy + h, dx : dx + w]
            sm += pad_m[dy : dy + h, dx : dx + w]
    return np.where(sm > 0.0, sv / np.maximum(sm, 1.0), np.nan).astype(np.float32)


def box_mean_power_db(db_arr: np.ndarray, radius: int = 2) -> np.ndarray:
    """Multilook/box despeckling in linear power space converted back to decibels."""
    arr = np.asarray(db_arr, dtype=np.float32)
    valid = np.isfinite(arr)
    lin = np.where(valid, 10.0 ** (np.clip(arr, -50.0, 20.0) / 10.0), np.nan).astype(np.float32)
    mean_lin = _box_mean_nan(lin, radius=radius)
    with np.errstate(all="ignore"):
        out = np.where(
            np.isfinite(mean_lin) & (mean_lin > 0.0),
            10.0 * np.log10(np.maximum(mean_lin, 1e-10)),
            np.nan,
        )
    return out.astype(np.float32)


def compute_glcm_contrast_change(
    cross_pre_db: np.ndarray,
    cross_post_db: np.ndarray,
    *,
    levels: int = 16,
    window: int = 5,
) -> np.ndarray:
    """Vectorized 4-neighbor GLCM contrast difference (post - pre) on quantized dB."""

    def _contrast(arr: np.ndarray) -> np.ndarray:
        valid = np.isfinite(arr)
        q = np.clip(
            np.floor((np.where(valid, arr, -28.0) - (-28.0)) / 26.0 * float(levels)),
            0.0,
            float(levels - 1),
        ).astype(np.float32)
        acc = np.zeros(arr.shape, dtype=np.float32)
        cnt = np.zeros(arr.shape, dtype=np.float32)

        h_ok = valid[:, :-1] & valid[:, 1:]
        dh = np.where(h_ok, (q[:, :-1] - q[:, 1:]) ** 2, 0.0)
        mh = h_ok.astype(np.float32)
        acc[:, :-1] += dh
        acc[:, 1:] += dh
        cnt[:, :-1] += mh
        cnt[:, 1:] += mh

        v_ok = valid[:-1, :] & valid[1:, :]
        dv = np.where(v_ok, (q[:-1, :] - q[1:, :]) ** 2, 0.0)
        mv = v_ok.astype(np.float32)
        acc[:-1, :] += dv
        acc[1:, :] += dv
        cnt[:-1, :] += mv
        cnt[1:, :] += mv

        local = np.where(cnt > 0.0, acc / np.maximum(cnt, 1.0), np.nan)
        return _box_mean_nan(local, radius=max(1, window // 2))

    return (_contrast(cross_post_db) - _contrast(cross_pre_db)).astype(np.float32)


def compute_wishart_change_stat(
    co_pre_lin: np.ndarray,
    co_post_lin: np.ndarray,
    cross_pre_lin: np.ndarray,
    cross_post_lin: np.ndarray,
    *,
    enl: float = 5.0,
) -> np.ndarray:
    """Diagonal dual-pol covariance omnibus test statistic `-2 ln Q` (eesarseq)."""
    eps = np.float32(1e-10)
    with np.errstate(all="ignore"):
        term_co = (
            2.0 * np.log(0.5 * (co_pre_lin + co_post_lin) + eps)
            - np.log(co_pre_lin + eps)
            - np.log(co_post_lin + eps)
        )
        term_cr = (
            2.0 * np.log(0.5 * (cross_pre_lin + cross_post_lin) + eps)
            - np.log(cross_pre_lin + eps)
            - np.log(cross_post_lin + eps)
        )
        stat = 2.0 * float(enl) * (term_co + term_cr)
    return np.where(np.isfinite(stat), np.maximum(stat, 0.0), np.nan).astype(np.float32)


def _robust_zscore(values: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """Center by scene median and scale by 1.4826*MAD (with safe constant-array fallback)."""
    sample = values[valid_mask]
    if sample.size == 0:
        return np.zeros_like(values, dtype=np.float32)
    med = float(np.median(sample))
    mad = float(np.median(np.abs(sample - med))) * 1.4826
    if mad < 1e-3:
        std = float(np.std(sample))
        scale = std if std >= 1e-3 else 0.25
    else:
        scale = mad
    return ((values - med) / scale).astype(np.float32)


def compute_opera_dist_metric(
    rbd_db: np.ndarray,
    log_rbr_db: np.ndarray,
    valid_mask: np.ndarray,
    canopy_weight: np.ndarray,
) -> np.ndarray:
    """OPERA DIST-S1 dual-pol Mahalanobis disturbance z-score in [0.0, 4.0]."""
    z1 = np.maximum(0.0, -_robust_zscore(rbd_db, valid_mask))
    z2 = np.maximum(0.0, -_robust_zscore(log_rbr_db, valid_mask))

    s1 = z1[valid_mask]
    s2 = z2[valid_mask]
    rho = 0.0
    if s1.size > 4 and float(np.std(s1)) > 1e-5 and float(np.std(s2)) > 1e-5:
        corr = float(np.corrcoef(s1, s2)[0, 1])
        if np.isfinite(corr):
            rho = float(np.clip(corr, -0.75, 0.75))

    denom = 2.0 * (1.0 - rho * rho)
    mahal_sq = np.maximum((z1 * z1 - 2.0 * rho * z1 * z2 + z2 * z2) / denom, 0.0)
    dist = np.sqrt(mahal_sq) * canopy_weight
    dist_clipped = np.clip(dist, OPERA_DIST_RANGE[0], OPERA_DIST_RANGE[1]).astype(np.float32)
    return np.where(valid_mask, dist_clipped, np.nan).astype(np.float32)


def _deterministic_kmeans(features: np.ndarray, k: int = 4, n_iter: int = 15) -> np.ndarray:
    """Quantile-seeded Euclidean K-means ordered by mean cluster magnitude."""
    n = features.shape[0]
    if n < k:
        return np.zeros(n, dtype=np.uint8)

    score = features.mean(axis=1)
    quantiles = np.quantile(score, [0.35, 0.72, 0.88, 0.96][:k])
    centers = np.empty((k, features.shape[1]), dtype=np.float32)
    used_indices: set[int] = set()
    order_all = np.argsort(score)

    for idx_k, q in enumerate(quantiles):
        best_i = int(np.argmin(np.abs(score - q)))
        if best_i in used_indices:
            pos = int(np.searchsorted(score[order_all], q))
            pos = min(max(pos, 0), n - 1)
            while order_all[pos] in used_indices and pos < n - 1:
                pos += 1
            best_i = int(order_all[pos])
        used_indices.add(best_i)
        centers[idx_k] = features[best_i]

    labels = np.zeros(n, dtype=np.int64)
    for _ in range(n_iter):
        dists = np.sum((features[:, None, :] - centers[None, :, :]) ** 2, axis=-1)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for cls in range(k):
            members = labels == cls
            if np.any(members):
                centers[cls] = features[members].mean(axis=0)

    rank_order = np.argsort(centers.mean(axis=1))
    remap = np.empty(k, dtype=np.uint8)
    remap[rank_order] = np.arange(k, dtype=np.uint8)
    return remap[labels]


def classify_sar_burn(
    co_pre_db: np.ndarray,
    co_post_db: np.ndarray,
    cross_pre_db: np.ndarray,
    cross_post_db: np.ndarray,
    *,
    pol_mode: PolarizationMode = "VV_VH",
    despeckle_radius: int = 2,
) -> SarBurnResult:
    """Detect burned area & disturbance using purely SAR dual-pol pre/post inputs in dB."""
    if pol_mode not in POL_MODES:
        raise ValueError(f"Unsupported pol_mode '{pol_mode}'. Expected one of {tuple(POL_MODES)}.")

    shapes = {np.asarray(a).shape for a in (co_pre_db, co_post_db, cross_pre_db, cross_post_db)}
    if len(shapes) != 1:
        raise ValueError(f"All SAR inputs must share one shape, got {sorted(shapes)}")

    co_pre_f = np.asarray(co_pre_db, dtype=np.float32)
    co_post_f = np.asarray(co_post_db, dtype=np.float32)
    cr_pre_f = np.asarray(cross_pre_db, dtype=np.float32)
    cr_post_f = np.asarray(cross_post_db, dtype=np.float32)

    co_pre_s = box_mean_power_db(co_pre_f, radius=despeckle_radius)
    co_post_s = box_mean_power_db(co_post_f, radius=despeckle_radius)
    cr_pre_s = box_mean_power_db(cr_pre_f, radius=despeckle_radius)
    cr_post_s = box_mean_power_db(cr_post_f, radius=despeckle_radius)

    valid = (
        np.isfinite(co_pre_s)
        & np.isfinite(co_post_s)
        & np.isfinite(cr_pre_s)
        & np.isfinite(cr_post_s)
        & (co_pre_f >= CO_FLOOR_DB)
        & (cr_pre_f >= CROSS_FLOOR_DB)
        & (co_pre_s >= CO_FLOOR_DB)
        & (cr_pre_s >= CROSS_FLOOR_DB)
    )

    canopy_w = (1.0 / (1.0 + np.exp(-np.clip((cr_pre_s - CANOPY_MID_DB) / CANOPY_SCALE_DB, -30.0, 30.0)))).astype(
        np.float32
    )

    rbd_db = (cr_post_s - cr_pre_s).astype(np.float32)
    log_rbr_db = ((cr_post_s - co_post_s) - (cr_pre_s - co_pre_s)).astype(np.float32)

    eps = np.float32(1e-10)
    co_pre_lin = np.where(valid, 10.0 ** (np.clip(co_pre_s, -50.0, 20.0) / 10.0), np.nan).astype(np.float32)
    co_post_lin = np.where(valid, 10.0 ** (np.clip(co_post_s, -50.0, 20.0) / 10.0), np.nan).astype(np.float32)
    cr_pre_lin = np.where(valid, 10.0 ** (np.clip(cr_pre_s, -50.0, 20.0) / 10.0), np.nan).astype(np.float32)
    cr_post_lin = np.where(valid, 10.0 ** (np.clip(cr_post_s, -50.0, 20.0) / 10.0), np.nan).astype(np.float32)

    with np.errstate(all="ignore"):
        rvi_pre = 4.0 * cr_pre_lin / (co_pre_lin + cr_pre_lin + eps)
        rvi_post = 4.0 * cr_post_lin / (co_post_lin + cr_post_lin + eps)
        delta_rvi = (rvi_post - rvi_pre).astype(np.float32)

        dpsvi_pre = (co_pre_lin + cr_pre_lin) / (co_pre_lin + eps)
        dpsvi_post = (co_post_lin + cr_post_lin) / (co_post_lin + eps)
        delta_dpsvi = (dpsvi_post - dpsvi_pre).astype(np.float32)

        rfdi_pre = (co_pre_lin - cr_pre_lin) / (co_pre_lin + cr_pre_lin + eps)
        rfdi_post = (co_post_lin - cr_post_lin) / (co_post_lin + cr_post_lin + eps)
        delta_rfdi = (rfdi_post - rfdi_pre).astype(np.float32)

    delta_glcm = compute_glcm_contrast_change(cr_pre_f, cr_post_f, levels=16, window=5)
    wishart = compute_wishart_change_stat(co_pre_lin, co_post_lin, cr_pre_lin, cr_post_lin, enl=5.0)
    opera_dist = compute_opera_dist_metric(rbd_db, log_rbr_db, valid, canopy_w)

    # 6-feature unsupervised pure-SAR clustering stack
    f_rbd = np.maximum(0.0, -_robust_zscore(rbd_db, valid))
    f_lrbr = np.maximum(0.0, -_robust_zscore(log_rbr_db, valid))
    f_rvi = np.maximum(0.0, -_robust_zscore(delta_rvi, valid))
    f_dpsvi = np.maximum(0.0, -_robust_zscore(delta_dpsvi, valid))
    glcm_abs = np.where(np.isfinite(delta_glcm), np.abs(delta_glcm), 0.0).astype(np.float32)
    f_glcm = np.maximum(0.0, _robust_zscore(glcm_abs, valid))
    wishart_safe = np.where(np.isfinite(wishart), wishart, 0.0).astype(np.float32)
    f_wishart = np.maximum(0.0, _robust_zscore(wishart_safe, valid))

    classes = np.zeros(co_pre_f.shape, dtype=np.uint8)
    if int(valid.sum()) >= 16:
        stack = np.stack(
            [
                f_rbd * canopy_w,
                f_lrbr * canopy_w,
                f_rvi * canopy_w,
                f_dpsvi * canopy_w,
                f_glcm * canopy_w,
                f_wishart * canopy_w,
            ],
            axis=-1,
        )
        feat_valid = np.clip(stack[valid], 0.0, 5.0).astype(np.float32)
        classes[valid] = _deterministic_kmeans(feat_valid, k=4, n_iter=15)

    mask_nan = lambda arr: np.where(valid, arr, np.nan).astype(np.float32)
    return SarBurnResult(
        classes=classes,
        opera_dist=opera_dist,
        rbd_db=mask_nan(rbd_db),
        log_rbr_db=mask_nan(log_rbr_db),
        delta_rvi=mask_nan(delta_rvi),
        delta_dpsvi=mask_nan(delta_dpsvi),
        delta_rfdi=mask_nan(delta_rfdi),
        delta_glcm_contrast=mask_nan(delta_glcm),
        wishart_stat=mask_nan(wishart),
        pol_mode=pol_mode,
    )


# --------------------------------------------------------------------------- metrics & encoders


def pixel_area_km2(bbox: BoundingBox, shape: tuple[int, int]) -> float:
    """Approximate pixel area in km² on an EPSG:4326 grid over `bbox`."""
    height, width = shape
    mid_lat = np.deg2rad((bbox.min_lat + bbox.max_lat) / 2.0)
    dlon = (bbox.max_lon - bbox.min_lon) / width
    dlat = (bbox.max_lat - bbox.min_lat) / height
    return float((110.574 * dlat) * (111.320 * np.cos(mid_lat) * dlon))


def summarize_sar_burn(result: SarBurnResult, bbox: BoundingBox) -> dict[str, float]:
    """Compute SAR burned area (class >= 2), high burn area, disturbed area & mean ΔRVI."""
    px_km2 = pixel_area_km2(bbox, result.classes.shape)
    burn_km2 = float((result.classes >= CLASS_MOD_BURN).sum()) * px_km2
    high_km2 = float((result.classes == CLASS_HIGH_BURN).sum()) * px_km2
    dist_km2 = float((result.classes >= CLASS_LOW_DIST).sum()) * px_km2

    burn_mask = (result.classes >= CLASS_MOD_BURN) & np.isfinite(result.delta_rvi)
    if np.any(burn_mask):
        mean_drvi = float(np.mean(result.delta_rvi[burn_mask]))
    else:
        valid_rvi = result.delta_rvi[np.isfinite(result.delta_rvi)]
        mean_drvi = float(np.mean(valid_rvi)) if valid_rvi.size else 0.0

    return {
        "sarBurnKm2": round(burn_km2, 2),
        "sarHighBurnKm2": round(high_km2, 2),
        "sarDisturbedKm2": round(dist_km2, 2),
        "sarMeanDeltaRvi": round(mean_drvi, 3),
    }


def encode_class_png(classes: np.ndarray, n_classes: int = N_CLASSES) -> bytes:
    """Encode discrete classes `0..n_classes` into gray8 PNG bytes (`0, 85, 170, 255`)."""
    u8 = np.rint(classes.astype(np.float32) / float(n_classes) * 255.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_continuous_png(values: np.ndarray, vmin: float, vmax: float) -> bytes:
    """Encode a 2D array into gray8 PNG (`0 = nodata`, `1..255` linear onto `[vmin, vmax]`)."""
    arr = np.asarray(values, dtype=np.float32)
    valid = np.isfinite(arr)
    u8 = np.zeros(arr.shape, dtype=np.uint8)
    span = max(float(vmax - vmin), 1e-9)
    norm = np.clip((arr[valid] - vmin) / span, 0.0, 1.0)
    u8[valid] = (1 + np.rint(norm * 254.0)).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def decode_gray8_values(u8: np.ndarray, vmin: float, vmax: float, *, nodata: int | None = 0) -> np.ndarray:
    """Decode a gray8 uint8 PNG array back to physical floats."""
    arr = np.asarray(u8, dtype=np.float32)
    if nodata is None:
        return (vmin + arr / 255.0 * (vmax - vmin)).astype(np.float32)
    out = np.where(arr != nodata, vmin + ((arr - 1.0) / 254.0) * (vmax - vmin), np.nan)
    return out.astype(np.float32)


def write_geotiff(
    path: Path,
    array: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    nodata: float,
) -> int:
    """Write a single-band LZW-compressed EPSG:4326 GeoTIFF and return file size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(array)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D raster array, got shape {arr.shape}")
    height, width = arr.shape
    west, south, east, north = bounds_wgs84

    if arr.dtype == np.uint8:
        img = Image.fromarray(arr, mode="L")
        nodata_str = str(int(nodata))
    else:
        img = Image.fromarray(np.where(np.isnan(arr), np.float32(nodata), arr.astype(np.float32)), mode="F")
        nodata_str = str(float(nodata))

    ifd = ImageFileDirectory_v2()
    ifd[33550] = ((east - west) / float(width), (north - south) / float(height), 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, float(west), float(north), 0.0)
    ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326)
    ifd[42113] = nodata_str
    img.save(path, format="TIFF", tiffinfo=ifd, compression="tiff_lzw")
    return path.stat().st_size


# --------------------------------------------------------------------------- vector perimeter


def majority_filter(mask: np.ndarray, min_neighbors: int = 5) -> np.ndarray:
    """3×3 majority filter (self included) to despeckle raster masks before vectorizing."""
    padded = np.pad(mask.astype(np.uint8), 1, mode="constant")
    h, w = mask.shape
    total = np.zeros(mask.shape, dtype=np.uint8)
    for dy in range(3):
        for dx in range(3):
            total += padded[dy : dy + h, dx : dx + w]
    return total >= min_neighbors


def sar_burn_extent_geojson(
    classes: np.ndarray,
    bbox: BoundingBox,
    *,
    burn_area_km2: float,
    pol_mode: PolarizationMode,
    post_date: str | None = None,
) -> dict[str, Any]:
    """Extract despeckled GeoJSON outline of Moderate + High SAR burn (class >= 2)."""
    height, width = classes.shape
    mask = majority_filter(classes >= CLASS_MOD_BURN)
    features: list[dict[str, Any]] = []
    if int(mask.sum()) >= 4:
        dlon = (bbox.max_lon - bbox.min_lon) / width
        dlat = (bbox.max_lat - bbox.min_lat) / height
        xs = bbox.min_lon + (np.arange(width) + 0.5) * dlon
        ys = bbox.max_lat - (np.arange(height) + 0.5) * dlat
        z = mask[::-1, :].astype(np.float64)
        cg = contourpy.contour_generator(x=xs, y=ys[::-1], z=z, fill_type=contourpy.FillType.OuterOffset)
        verts_list, offs_list = cg.filled(0.5, 1.5)

        min_poly_deg2 = dlon * dlat * 6.0
        polys: list[Polygon] = []
        for verts, offs in zip(verts_list, offs_list):
            if len(offs) < 2:
                continue
            shell = [(float(x), float(y)) for x, y in verts[offs[0] : offs[1]]]
            holes = [
                [(float(x), float(y)) for x, y in verts[offs[i] : offs[i + 1]]]
                for i in range(1, len(offs) - 1)
            ]
            poly = Polygon(shell, holes).buffer(0)
            parts = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
            polys.extend(p for p in parts if isinstance(p, Polygon) and p.is_valid and p.area >= min_poly_deg2)

        if polys:
            merged = MultiPolygon(polys).buffer(0) if len(polys) > 1 else polys[0]
            merged = merged.simplify(min(dlon, dlat) * 0.35, preserve_topology=True)
            merged = shapely.set_precision(merged, 1e-5)
            if not merged.is_empty:
                props: dict[str, Any] = {
                    "name": "SAR unsupervised burn zone",
                    "area_km2": round(burn_area_km2, 2),
                    "pol_mode": POL_MODES[pol_mode]["pair"],
                }
                if post_date:
                    props["date"] = post_date
                features.append(
                    {
                        "type": "Feature",
                        "properties": props,
                        "geometry": mapping(merged),
                    }
                )
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------- asset writing & Schema v2


@dataclass(frozen=True)
class SarBurnAssets:
    """Generated SAR-burn files plus viewer summary metrics."""

    stats: dict[str, float]
    downloads: list[dict[str, Any]]
    pol_mode: PolarizationMode
    has_vv_rasters: bool


def write_sar_burn_assets(
    result: SarBurnResult,
    bbox: BoundingBox,
    case_dir: Path,
    case_id: str,
    *,
    post_date: str | None = None,
    co_pre_db: np.ndarray | None = None,
    co_post_db: np.ndarray | None = None,
) -> SarBurnAssets:
    """Write SAR burn PNGs, GeoTIFFs, GeoJSON perimeter and optional co-pol PNGs."""
    case_dir.mkdir(parents=True, exist_ok=True)
    rasters_dir = case_dir / "rasters"
    rasters_dir.mkdir(parents=True, exist_ok=True)
    tif_dir = case_dir / "geotiffs"
    bounds = (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)

    stats = summarize_sar_burn(result, bbox)
    (rasters_dir / "sar_burn_class.png").write_bytes(encode_class_png(result.classes))
    (rasters_dir / "sar_opera_dist.png").write_bytes(encode_continuous_png(result.opera_dist, *OPERA_DIST_RANGE))
    (rasters_dir / "sar_delta_rvi.png").write_bytes(encode_continuous_png(result.delta_rvi, *DELTA_RVI_RANGE))
    (rasters_dir / "sar_log_rbr.png").write_bytes(encode_continuous_png(result.log_rbr_db, *LOG_RBR_RANGE_DB))

    has_vv_rasters = False
    if co_pre_db is not None and co_post_db is not None and result.pol_mode == "VV_VH":
        (rasters_dir / "s1_pre_vv.png").write_bytes(encode_continuous_png(co_pre_db, *S1_PNG_VV_RANGE_DB))
        (rasters_dir / "s1_post_vv.png").write_bytes(encode_continuous_png(co_post_db, *S1_PNG_VV_RANGE_DB))
        has_vv_rasters = True
    elif (rasters_dir / "s1_pre_vv.png").exists() and (rasters_dir / "s1_post_vv.png").exists():
        has_vv_rasters = True

    geojson = sar_burn_extent_geojson(
        result.classes,
        bbox,
        burn_area_km2=stats["sarBurnKm2"],
        pol_mode=result.pol_mode,
        post_date=post_date,
    )
    (case_dir / "sar_burn_extent.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    tif_specs: list[tuple[str, str, str, np.ndarray, float]] = [
        ("sar-burn-class-tif", f"{case_id}_sar_burn_class.tif", "SAR Unsupervised Burn Class (.tif)", result.classes, 0),
        (
            "sar-opera-dist-tif",
            f"{case_id}_sar_opera_dist.tif",
            "OPERA DIST-S1 Disturbance (.tif)",
            result.opera_dist,
            -9999.0,
        ),
        (
            "sar-delta-rvi-tif",
            f"{case_id}_sar_delta_rvi.tif",
            "SAR ΔRVI Vegetation Change (.tif)",
            result.delta_rvi,
            -9999.0,
        ),
    ]
    downloads: list[dict[str, Any]] = []
    for dl_id, filename, label, array, nodata in tif_specs:
        size = write_geotiff(tif_dir / filename, array, bounds, nodata=nodata)
        downloads.append(
            {
                "id": dl_id,
                "label": label,
                "datasetId": DATASET_ID,
                "phase": "change",
                "format": "geotiff",
                "url": f"data/cases/{case_id}/geotiffs/{filename}",
                "sizeBytes": size,
            }
        )

    return SarBurnAssets(
        stats=stats,
        downloads=downloads,
        pol_mode=result.pol_mode,
        has_vv_rasters=has_vv_rasters,
    )


def sar_burn_dataset_definition(
    *,
    case_id: str,
    extent: list[float],
    before_win: str,
    after_win: str,
    post_date: str,
    pol_mode: PolarizationMode,
) -> dict[str, Any]:
    """Build Schema v2 dataset entry for pure-SAR burn & OPERA disturbance detection."""
    meta = POL_MODES[pol_mode]
    base = f"data/cases/{case_id}"
    pair = meta["pair"]
    return {
        "id": DATASET_ID,
        "name": "SAR Burn & Disturbance · OPERA RTC + K-Means",
        "kind": "raster",
        "status": "real",
        "platform": meta["platform"],
        "sensor": meta["sensor"],
        "provider": meta["provider"],
        "product": f"Pure-SAR OPERA DIST Mahalanobis + K-Means ({meta['band_desc']} + GLCM + Wishart)",
        "attribution": "NASA OPERA DIST-S1/RTC · Jando93 · ErliPinto · mortcanty/eesarseq",
        "extent": extent,
        "crs": "EPSG:4326",
        "resolution": {"value": 30, "unit": "m"},
        "source": {"type": "image", "encoding": "gray8", "nodata": 0},
        "render": {"opacity": 0.88, "resampling": "nearest"},
        "defaultVisible": False,
        "variables": [
            {
                "id": "sar_burn_class",
                "label": "SAR Burn Class (K-Means + OPERA DIST)",
                "quantity": "burn_severity_sar",
                "polarization": pair,
                "unit": "class 0–3",
                "legend": {
                    "min": 0,
                    "max": N_CLASSES,
                    "colors": ["#fbbf24", "#f97316", "#db2777"],
                    "discrete": {"1": "#fbbf24b8", "2": "#f97316dc", "3": "#db2777f2"},
                    "nodata_label": CLASS_LEGEND_NOTE,
                },
            },
            {
                "id": "opera_dist",
                "label": "OPERA DIST-S1 Disturbance Metric",
                "quantity": "surface_disturbance",
                "polarization": pair,
                "unit": "z-score (0–4)",
                "legend": {
                    "min": OPERA_DIST_RANGE[0],
                    "max": OPERA_DIST_RANGE[1],
                    "colors": ["#1e293b", "#f59e0b", "#ea580c", "#be123c"],
                    "stops": [
                        [0.5, "#1e293b00"],
                        [1.5, "#f59e0bb8"],
                        [2.5, "#ea580cdc"],
                        [4.0, "#be123cf2"],
                    ],
                    "nodata_label": DIST_LEGEND_NOTE,
                },
            },
            {
                "id": "delta_rvi",
                "label": "ΔRVI Radar Vegetation Change",
                "quantity": "vegetation_index_change",
                "polarization": pair,
                "unit": "ΔRVI",
                "legend": {
                    "min": DELTA_RVI_RANGE[0],
                    "max": DELTA_RVI_RANGE[1],
                    "colors": ["#9f1239", "#f97316", "#94a3b8", "#22c55e"],
                    "stops": [
                        [-0.6, "#9f1239f0"],
                        [-0.25, "#f97316d0"],
                        [-0.05, "#94a3b800"],
                        [0.05, "#94a3b800"],
                        [0.3, "#22c55ecc"],
                    ],
                    "nodata_label": DRVI_LEGEND_NOTE,
                },
            },
            {
                "id": "log_rbr",
                "label": "LogRBR Depolarized Burn Ratio",
                "quantity": "backscatter_ratio",
                "polarization": pair,
                "unit": "dB",
                "legend": {
                    "min": LOG_RBR_RANGE_DB[0],
                    "max": LOG_RBR_RANGE_DB[1],
                    "colors": ["#881337", "#e11d48", "#fb923c", "#94a3b8", "#38bdf8"],
                    "stops": [
                        [-6.0, "#881337f0"],
                        [-2.5, "#e11d48d8"],
                        [-1.0, "#fb923cb0"],
                        [-0.4, "#94a3b800"],
                        [0.4, "#94a3b800"],
                        [3.0, "#38bdf8c8"],
                    ],
                    "nodata_label": LOGRBR_LEGEND_NOTE,
                },
            },
        ],
        "frames": [
            {
                "id": FRAME_ID,
                "datetime": post_date,
                "label": f"SAR Change · {before_win} → {after_win}",
                "phase": "change",
                "files": {
                    "sar_burn_class": f"{base}/rasters/sar_burn_class.png",
                    "opera_dist": f"{base}/rasters/sar_opera_dist.png",
                    "delta_rvi": f"{base}/rasters/sar_delta_rvi.png",
                    "log_rbr": f"{base}/rasters/sar_log_rbr.png",
                },
            }
        ],
    }


def sar_burn_vector_definition(case_id: str) -> dict[str, Any]:
    return {
        "id": VECTOR_ID,
        "name": "SAR Unsupervised Burn Zone",
        "type": "line",
        "status": "real",
        "simulated": False,
        "defaultVisible": False,
        "source": {"type": "geojson", "data": f"data/cases/{case_id}/sar_burn_extent.geojson"},
        "paint": {"line-color": "#ec4899", "line-width": 1.6},
        "legend": {
            "title": "SAR Unsupervised Burn",
            "items": [{"color": "#ec4899", "label": "OPERA DIST + K-Means (Class ≥ 2)"}],
        },
        "description": "All-weather burn perimeter derived strictly from dual-pol SAR indices and unsupervised K-means.",
        "popupFields": [
            ["area_km2", "SAR burned area", "km²"],
            ["pol_mode", "Polarization"],
        ],
    }


def _ensure_vv_on_s1_dataset(case_def: dict[str, Any], case_id: str) -> None:
    """Register the `VV` variable and before/after PNG paths on `s1_sar` when present."""
    for ds in case_def.get("datasets", []):
        if ds.get("id") != "s1_sar":
            continue
        vv_var = {
            "id": "VV",
            "label": "VV Backscatter",
            "quantity": "backscatter",
            "polarization": "VV",
            "unit": "dB",
            "legend": {
                "min": -25,
                "max": 0,
                "colors": ["#0b0f14", "#64748b", "#f8fafc"],
                "nodata_label": "Transparent = no data",
            },
        }
        vars_list = [v for v in ds.get("variables", []) if v.get("id") != "VV"]
        vars_list.append(vv_var)
        ds["variables"] = vars_list
        for fr in ds.get("frames", []):
            files = fr.setdefault("files", {})
            if fr.get("phase") == "before":
                files["VV"] = f"data/cases/{case_id}/rasters/s1_pre_vv.png"
            elif fr.get("phase") == "after":
                files["VV"] = f"data/cases/{case_id}/rasters/s1_post_vv.png"
        break


def attach_sar_burn_to_case(
    case_def: dict[str, Any],
    assets: SarBurnAssets,
    *,
    case_id: str,
    extent: list[float],
    before_win: str,
    after_win: str,
    post_date: str,
) -> dict[str, Any]:
    """Idempotently wire the `sar_burn` dataset, vector, GeoTIFF downloads & metrics."""
    if assets.has_vv_rasters:
        _ensure_vv_on_s1_dataset(case_def, case_id)

    dataset = sar_burn_dataset_definition(
        case_id=case_id,
        extent=extent,
        before_win=before_win,
        after_win=after_win,
        post_date=post_date,
        pol_mode=assets.pol_mode,
    )
    datasets = [d for d in case_def.get("datasets", []) if d.get("id") != DATASET_ID]
    insert_at = next((i + 1 for i, d in enumerate(datasets) if d.get("id") == "s1_sar"), len(datasets))
    datasets.insert(insert_at, dataset)
    case_def["datasets"] = datasets

    vectors = [v for v in case_def.get("vectors", []) if v.get("id") != VECTOR_ID]
    vectors.append(sar_burn_vector_definition(case_id))
    case_def["vectors"] = vectors

    downloads = [
        d for d in case_def.get("downloads", []) if not str(d.get("id", "")).startswith(DOWNLOAD_EXTRA_PREFIXES)
    ]
    downloads.extend(assets.downloads)
    case_def["downloads"] = downloads

    metrics = case_def.setdefault("metrics", {})
    metrics["sarBurnKm2"] = assets.stats["sarBurnKm2"]
    metrics["sarHighBurnKm2"] = assets.stats["sarHighBurnKm2"]
    metrics["sarDisturbedKm2"] = assets.stats["sarDisturbedKm2"]
    metrics["sarMeanDeltaRvi"] = assets.stats["sarMeanDeltaRvi"]
<<<<<<< HEAD
=======
    if "sarBurnInOpticalPct" in assets.stats:
        metrics["sarBurnInOpticalPct"] = assets.stats["sarBurnInOpticalPct"]
    if "sarToOpticalAreaPct" in assets.stats:
        metrics["sarToOpticalAreaPct"] = assets.stats["sarToOpticalAreaPct"]
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7

    wf_block = case_def.setdefault("wildfire", {})
    wf_block["sarBurn"] = {
        "polMode": assets.pol_mode,
        "methods": [
            "opera-adt/dist-s1",
            "Jando93/Sentinel-1-unsupervised-burned-area-detection",
            "ErliPinto/Sentinel-1-SAR-vegetation-indices",
            "mortcanty/eesarseq",
        ],
        "stats": dict(assets.stats),
    }
    return case_def


# --------------------------------------------------------------------------- offline publish


@dataclass(frozen=True)
class SarBurnPublishResult:
    case_id: str
    case_dir: Path
    stats: dict[str, float]
    pol_mode: PolarizationMode
    png_count: int
    geotiff_count: int


def default_public_dir() -> Path:
    """Default `sar-Madness/sar-Madness/public` directory alongside `incendios`."""
    return (PROJECT_ROOT.parent / "sar-Madness" / "sar-Madness" / "public").resolve()


def _load_gray8(public_dir: Path, rel_path: str) -> np.ndarray:
    with Image.open(public_dir / rel_path) as img:
        return np.array(img.convert("L"))


def _dataset(case_def: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    for ds in case_def.get("datasets", []):
        if ds.get("id") == dataset_id:
            return ds
    raise KeyError(f"Case has no '{dataset_id}' dataset; run publish-madness first.")


def _frame(dataset: dict[str, Any], phase: str) -> dict[str, Any]:
    for fr in dataset.get("frames", []):
        if fr.get("phase") == phase:
            return fr
    raise KeyError(f"Dataset '{dataset.get('id')}' has no '{phase}' frame.")


def _read_vv_from_case_or_staging(case_id: str, public_dir: Path, phase: Literal["before", "after"]) -> np.ndarray | None:
    """Read VV dB from published `rasters/s1_{pre,post}_vv.png` or hydrate from `incendios/data/cases`."""
    raster_name = "s1_pre_vv.png" if phase == "before" else "s1_post_vv.png"
    pub_vv = public_dir / "data" / "cases" / case_id / "rasters" / raster_name
    if pub_vv.exists():
        with Image.open(pub_vv) as im:
            u8 = np.array(im.convert("L"))
        return decode_gray8_values(u8, *S1_PNG_VV_RANGE_DB, nodata=0)

    legacy_name = "s1_pre.png" if phase == "before" else "s1_post.png"
    legacy_vv = PROJECT_ROOT / "data" / "cases" / case_id / "layers" / legacy_name
    if legacy_vv.exists():
        with Image.open(legacy_vv) as im:
            rgba = np.array(im.convert("RGBA"))
        # `render_s1_gray_rgba` mapped [-25, 0] dB onto 0..255 with alpha=0 for nodata
        vals = decode_gray8_values(rgba[..., 0], *S1_PNG_VV_RANGE_DB, nodata=None)
        return np.where(rgba[..., 3] > 0, vals, np.nan).astype(np.float32)
    return None


def publish_sar_burn_for_case(
    case_id: str,
    public_dir: Path,
    *,
    pol_mode: PolarizationMode = "VV_VH",
) -> SarBurnPublishResult:
    """Derive and attach all-weather SAR burn products onto an already published wildfire case."""
    case_dir = public_dir / "data" / "cases" / case_id
    case_path = case_dir / "case.json"
    case_def = json.loads(case_path.read_text(encoding="utf-8"))

    s1 = _dataset(case_def, "s1_sar")
    s1_pre_fr = _frame(s1, "before")
    s1_post_fr = _frame(s1, "after")
    pre_files = s1_pre_fr["files"]
    post_files = s1_post_fr["files"]

    cross_key = "VH" if "VH" in pre_files else "HV"
    co_key = "VV" if "VV" in pre_files else ("HH" if "HH" in pre_files else None)

    cross_pre_db = decode_gray8_values(_load_gray8(public_dir, pre_files[cross_key]), *S1_PNG_VH_RANGE_DB, nodata=0)
    cross_post_db = decode_gray8_values(_load_gray8(public_dir, post_files[cross_key]), *S1_PNG_VH_RANGE_DB, nodata=0)

    if co_key is not None and co_key in post_files:
        co_pre_db = decode_gray8_values(_load_gray8(public_dir, pre_files[co_key]), *S1_PNG_VV_RANGE_DB, nodata=0)
        co_post_db = decode_gray8_values(_load_gray8(public_dir, post_files[co_key]), *S1_PNG_VV_RANGE_DB, nodata=0)
    else:
        co_pre_db = _read_vv_from_case_or_staging(case_id, public_dir, "before")
        co_post_db = _read_vv_from_case_or_staging(case_id, public_dir, "after")

    if co_pre_db is None or co_post_db is None:
        raise FileNotFoundError(
            f"Case '{case_id}' lacks co-pol VV/HH pre/post rasters in {case_dir / 'rasters'} "
            f"and under {PROJECT_ROOT / 'data' / 'cases' / case_id / 'layers'}."
        )

    extent = [float(v) for v in s1["extent"]]
    bbox = BoundingBox(min_lon=extent[0], min_lat=extent[1], max_lon=extent[2], max_lat=extent[3])
    metrics = case_def.get("metrics", {})
    before_win = str(metrics.get("preWindow") or "pre")
    after_win = str(metrics.get("postWindow") or "post")
    post_date = str(s1_post_fr["datetime"])

    result = classify_sar_burn(
        co_pre_db,
        co_post_db,
        cross_pre_db,
        cross_post_db,
        pol_mode=pol_mode,
    )
    assets = write_sar_burn_assets(
        result,
        bbox,
        case_dir,
        case_id,
        post_date=post_date,
        co_pre_db=co_pre_db if pol_mode == "VV_VH" else None,
        co_post_db=co_post_db if pol_mode == "VV_VH" else None,
    )
<<<<<<< HEAD
=======
    opt_prob_path = case_dir / "rasters" / "burn_prob.png"
    if opt_prob_path.exists():
        opt_prob = decode_gray8_values(_load_gray8(public_dir, f"data/cases/{case_id}/rasters/burn_prob.png"), 0.0, 1.0, nodata=0)
        if opt_prob.shape == result.classes.shape:
            opt_mask = np.isfinite(opt_prob) & (opt_prob >= 0.5)
            sar_burn_mask = result.classes >= CLASS_MOD_BURN
            if int(sar_burn_mask.sum()) > 0 and int(opt_mask.sum()) > 0:
                overlap = int((sar_burn_mask & opt_mask).sum())
                assets.stats["sarBurnInOpticalPct"] = round(100.0 * overlap / float(sar_burn_mask.sum()), 1)
    affected = metrics.get("affectedKm2")
    if isinstance(affected, (int, float)) and affected > 0:
        assets.stats["sarToOpticalAreaPct"] = round(
            100.0 * assets.stats["sarDisturbedKm2"] / float(affected), 1
        )
>>>>>>> b7c736ee41eb815900476c441dca15ecbc5038b7
    attach_sar_burn_to_case(
        case_def,
        assets,
        case_id=case_id,
        extent=extent,
        before_win=before_win,
        after_win=after_win,
        post_date=post_date,
    )
    case_path.write_text(json.dumps(case_def, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return SarBurnPublishResult(
        case_id=case_id,
        case_dir=case_dir,
        stats=assets.stats,
        pol_mode=pol_mode,
        png_count=4 + (2 if assets.has_vv_rasters else 0),
        geotiff_count=len(assets.downloads),
    )
