"""Implementación vectorizada del algoritmo físico NISAR L3 AGB ATBD.

Reproduce fielmente las ecuaciones forward (`volume + double + surface`) y la
inversión en dB ponderada (`weights=(4, 1)`) de `NISAR_Biomass/algorithm/bin/model_radar.py`,
sin requerir GDAL/osgeo/sklearn en tiempo de ejecución.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from .config import NisarAtbdParameters


@dataclass(frozen=True)
class NisarInversionResult:
    """Resultado espacial de inversión AGB (Mg/ha) para una escena o ventana temporal."""

    agb_mgha: np.ndarray
    agb_init_mgha: np.ndarray
    carbon_mgha: np.ndarray
    hh_linear: np.ndarray
    hv_linear: np.ndarray
    hh_db: np.ndarray
    hv_db: np.ndarray
    hh_pred_linear: np.ndarray
    hv_pred_linear: np.ndarray
    rmse_db: np.ndarray
    valid_mask: np.ndarray


def volume_scattering(
    agb: np.ndarray | float,
    a: float,
    b: float,
    alpha: float,
) -> np.ndarray | float:
    """Componente de dispersión volumétrica: `A * W^alpha * (1 - exp(-B * W))`."""
    w = np.maximum(np.asarray(agb, dtype=np.float64), 1e-9)
    return a * np.power(w, alpha) * (1.0 - np.exp(-b * w))


def double_bounce_scattering(
    agb: np.ndarray | float,
    b: float,
    c: float,
    delta: float,
    soil: float,
) -> np.ndarray | float:
    """Componente de doble rebote tronco-suelo: `C * S * W^delta * exp(-B * W)`."""
    w = np.maximum(np.asarray(agb, dtype=np.float64), 1e-9)
    return c * soil * np.power(w, delta) * np.exp(-b * w)


def surface_scattering(
    agb: np.ndarray | float,
    b: float,
    d: float,
    soil: float,
) -> np.ndarray | float:
    """Componente superficial atenuada por dosel: `D * S * exp(-B * W)`."""
    w = np.maximum(np.asarray(agb, dtype=np.float64), 1e-9)
    return d * soil * np.exp(-b * w)


def forward_nisar_channel(
    agb: np.ndarray | float,
    a: float,
    b: float,
    c: float,
    alpha: float,
    delta: float,
    d: float,
    soil: float,
) -> np.ndarray:
    """Modelo forward semi-empírico NISAR Banda L para un canal polarimétrico."""
    vol = volume_scattering(agb, a, b, alpha)
    dbl = double_bounce_scattering(agb, b, c, delta, soil)
    srf = surface_scattering(agb, b, d, soil)
    return np.asarray(vol + dbl + srf, dtype=np.float64)


def forward_nisar_hh_hv(
    agb_mgha: np.ndarray | float,
    params: NisarAtbdParameters,
) -> tuple[np.ndarray, np.ndarray]:
    """Evalúa `(HH, HV)` en potencia lineal gamma0 a partir de AGB (`Mg/ha`)."""
    hv = forward_nisar_channel(
        agb_mgha,
        params.A_HV,
        params.B_HV,
        params.C_HV,
        params.alpha_HV,
        params.delta_HV,
        params.D_HV,
        params.s,
    )
    hh = forward_nisar_channel(
        agb_mgha,
        params.A_HH,
        params.B_HH,
        params.C_HH,
        params.alpha_HH,
        params.delta_HH,
        params.D_HH,
        params.s,
    )
    return hh, hv


def linear_to_db(gamma0: np.ndarray, floor_linear: float = 1e-6) -> np.ndarray:
    """Convierte potencia lineal gamma0 a decibelios `10 * log10(gamma0)` preservando NaN."""
    arr = np.asarray(gamma0, dtype=np.float64)
    out = np.full_like(arr, np.nan, dtype=np.float32)
    valid = np.isfinite(arr) & (arr > floor_linear)
    out[valid] = (10.0 * np.log10(arr[valid])).astype(np.float32)
    return out


def compute_initial_agb(
    hv_linear: np.ndarray,
    *,
    min_agb: float = 0.0,
    max_agb: float = 300.0,
) -> np.ndarray:
    """Estimación empírica inicial de AGB (`functions.py:380`: `clip((100 * HV)^2, 0, 300)`)."""
    hv = np.asarray(hv_linear, dtype=np.float64)
    out = np.full_like(hv, np.nan, dtype=np.float64)
    valid = np.isfinite(hv) & (hv > 0.0)
    out[valid] = np.clip((100.0 * hv[valid]) ** 2, min_agb, max_agb)
    return out


def build_sar_valid_mask(
    hh_stack: np.ndarray,
    hv_stack: np.ndarray,
    *,
    hv_min: float = 0.01,
    hv_max: float = 1.0,
    hh_max: float = 1.0,
) -> np.ndarray:
    """Aplica la máscara física NISAR (`HV_MIN > 0.01`, `HV_MAX < 1`, `HH_MAX < 1`)."""
    hh = np.asarray(hh_stack, dtype=np.float64)
    hv = np.asarray(hv_stack, dtype=np.float64)
    if hh.ndim == 2:
        hh = hh[None, ...]
        hv = hv[None, ...]

    finite = np.all(np.isfinite(hh) & np.isfinite(hv) & (hh > 0.0) & (hv > 0.0), axis=0)
    with np.errstate(invalid="ignore"):
        hv_lo = np.min(hv, axis=0) > hv_min
        hv_hi = np.max(hv, axis=0) < hv_max
        hh_hi = np.max(hh, axis=0) < hh_max
    return finite & hv_lo & hv_hi & hh_hi


def _objective_db_weighted(
    w_grid: np.ndarray,
    hh_obs_db: np.ndarray,
    hv_obs_db: np.ndarray,
    params: NisarAtbdParameters,
    hv_weight: float,
    hh_weight: float,
) -> np.ndarray:
    """Evalúa el costo en dB ponderado para `w_grid` de forma `(K, N)` y obs `(S, N)`."""
    hh_hat, hv_hat = forward_nisar_hh_hv(w_grid, params)
    hh_hat_db = 10.0 * np.log10(np.maximum(hh_hat, 1e-12))
    hv_hat_db = 10.0 * np.log10(np.maximum(hv_hat, 1e-12))
    # Broadcasting: (1, K, N) vs (S, 1, N) -> suma sobre escenas S
    err_hv = (hv_hat_db[None, :, :] - hv_obs_db[:, None, :]) * hv_weight
    err_hh = (hh_hat_db[None, :, :] - hh_obs_db[:, None, :]) * hh_weight
    return np.sum(err_hv * err_hv + err_hh * err_hh, axis=0)


def _solve_agb_vectorized_1d(
    hh_obs: np.ndarray,
    hv_obs: np.ndarray,
    w_init: np.ndarray,
    params: NisarAtbdParameters,
    *,
    w_bounds_mult: tuple[float, float] = (0.2, 1.8),
    agb_max_mgha: float = 350.0,
    hv_weight: float = 4.0,
    hh_weight: float = 1.0,
    n_grid: int = 65,
    n_golden_iters: int = 24,
) -> np.ndarray:
    """Resuelve Stage 1 del modelo NISAR sobre N píxeles simultáneamente (~1e-3 Mg/ha de error vs L-BFGS-B)."""
    if hh_obs.shape[1] == 0:
        return np.empty((0,), dtype=np.float64)

    hh_obs_db = 10.0 * np.log10(np.maximum(hh_obs, 1e-12))
    hv_obs_db = 10.0 * np.log10(np.maximum(hv_obs, 1e-12))

    w_base = np.maximum(w_init, 1.0)
    lo = np.maximum(w_base * w_bounds_mult[0], 0.5)
    hi = np.minimum(np.maximum(w_base * w_bounds_mult[1], lo + 2.0), agb_max_mgha)

    t = np.linspace(0.0, 1.0, n_grid, dtype=np.float64)[:, None]
    w_samples = lo[None, :] + t * (hi - lo)[None, :]
    losses = _objective_db_weighted(w_samples, hh_obs_db, hv_obs_db, params, hv_weight, hh_weight)

    best_idx = np.argmin(losses, axis=0)
    cols = np.arange(hh_obs.shape[1])
    idx_left = np.maximum(best_idx - 1, 0)
    idx_right = np.minimum(best_idx + 1, n_grid - 1)
    a = w_samples[idx_left, cols]
    b = w_samples[idx_right, cols]

    phi = (np.sqrt(5.0) - 1.0) / 2.0
    x1 = b - phi * (b - a)
    x2 = a + phi * (b - a)
    f1 = _objective_db_weighted(x1[None, :], hh_obs_db, hv_obs_db, params, hv_weight, hh_weight)[0]
    f2 = _objective_db_weighted(x2[None, :], hh_obs_db, hv_obs_db, params, hv_weight, hh_weight)[0]

    for _ in range(n_golden_iters):
        choose_left = f1 <= f2
        b = np.where(choose_left, x2, b)
        x2 = np.where(choose_left, x1, x2)
        f2 = np.where(choose_left, f1, f2)
        a = np.where(~choose_left, x1, a)
        x1 = np.where(~choose_left, x2, x1)
        f1 = np.where(~choose_left, f2, f1)

        new_x1 = b - phi * (b - a)
        new_x2 = a + phi * (b - a)
        new_f1 = _objective_db_weighted(
            new_x1[None, :], hh_obs_db, hv_obs_db, params, hv_weight, hh_weight
        )[0]
        new_f2 = _objective_db_weighted(
            new_x2[None, :], hh_obs_db, hv_obs_db, params, hv_weight, hh_weight
        )[0]
        x1 = np.where(choose_left, new_x1, x1)
        f1 = np.where(choose_left, new_f1, f1)
        x2 = np.where(~choose_left, new_x2, x2)
        f2 = np.where(~choose_left, new_f2, f2)

    return 0.5 * (a + b)


def optimize_pixel_lbfgsb(
    hh_obs: np.ndarray | float,
    hv_obs: np.ndarray | float,
    w_init: float,
    params: NisarAtbdParameters,
    *,
    w_bounds_mult: tuple[float, float] = (0.2, 1.8),
    agb_max_mgha: float = 350.0,
    hv_weight: float = 4.0,
    hh_weight: float = 1.0,
) -> float:
    """Referencia exacta por píxel con `scipy.optimize.minimize(L-BFGS-B)` (Stage 1 de model_radar.py)."""
    hh_arr = np.atleast_1d(np.asarray(hh_obs, dtype=np.float64))
    hv_arr = np.atleast_1d(np.asarray(hv_obs, dtype=np.float64))
    hh_db = 10.0 * np.log10(np.maximum(hh_arr, 1e-12))
    hv_db = 10.0 * np.log10(np.maximum(hv_arr, 1e-12))

    w_base = max(float(w_init), 1.0)
    lo = max(w_base * w_bounds_mult[0], 0.5)
    hi = min(max(w_base * w_bounds_mult[1], lo + 2.0), agb_max_mgha)

    def obj(x: np.ndarray) -> float:
        hh_hat, hv_hat = forward_nisar_hh_hv(float(x[0]), params)
        err_hv = (10.0 * np.log10(max(float(hv_hat), 1e-12)) - hv_db) * hv_weight
        err_hh = (10.0 * np.log10(max(float(hh_hat), 1e-12)) - hh_db) * hh_weight
        return float(np.sum(err_hv**2 + err_hh**2))

    res = minimize(obj, np.array([np.clip(w_base, lo, hi)]), method="L-BFGS-B", bounds=[(lo, hi)])
    return float(res.x[0])


def invert_agb_scene(
    hh_stack: np.ndarray,
    hv_stack: np.ndarray,
    params: NisarAtbdParameters,
    *,
    valid_mask: np.ndarray | None = None,
    w_bounds_mult: tuple[float, float] = (0.2, 1.8),
    agb_max_mgha: float = 350.0,
    hv_weight: float = 4.0,
    hh_weight: float = 1.0,
) -> NisarInversionResult:
    """Ejecuta la inversión NISAR L3 AGB sobre una escena `(H, W)` o pila `(S, H, W)`."""
    hh = np.asarray(hh_stack, dtype=np.float64)
    hv = np.asarray(hv_stack, dtype=np.float64)
    if hh.ndim == 2:
        hh = hh[None, ...]
        hv = hv[None, ...]
    if hh.shape != hv.shape:
        raise ValueError(f"Shapes incompatibles HH={hh.shape} y HV={hv.shape}")

    sar_ok = build_sar_valid_mask(hh, hv)
    final_mask = sar_ok if valid_mask is None else (sar_ok & np.asarray(valid_mask, dtype=bool))

    hh_mean = np.nanmean(hh, axis=0)
    hv_mean = np.nanmean(hv, axis=0)
    agb_init = compute_initial_agb(hv_mean, min_agb=0.0, max_agb=min(300.0, agb_max_mgha))

    _, height, width = hh.shape
    agb_out = np.full((height, width), np.nan, dtype=np.float32)
    carbon_out = np.full((height, width), np.nan, dtype=np.float32)
    hh_pred = np.full((height, width), np.nan, dtype=np.float32)
    hv_pred = np.full((height, width), np.nan, dtype=np.float32)
    rmse_db = np.full((height, width), np.nan, dtype=np.float32)

    if np.any(final_mask):
        hh_vec = hh[:, final_mask]
        hv_vec = hv[:, final_mask]
        w0_vec = agb_init[final_mask]
        w_sol = _solve_agb_vectorized_1d(
            hh_vec,
            hv_vec,
            w0_vec,
            params,
            w_bounds_mult=w_bounds_mult,
            agb_max_mgha=agb_max_mgha,
            hv_weight=hv_weight,
            hh_weight=hh_weight,
        )
        hh_hat_v, hv_hat_v = forward_nisar_hh_hv(w_sol, params)

        hh_obs_db = 10.0 * np.log10(np.maximum(hh_vec, 1e-12))
        hv_obs_db = 10.0 * np.log10(np.maximum(hv_vec, 1e-12))
        hh_hat_db = 10.0 * np.log10(np.maximum(hh_hat_v, 1e-12))[None, :]
        hv_hat_db = 10.0 * np.log10(np.maximum(hv_hat_v, 1e-12))[None, :]
        mse_db = 0.5 * (
            np.mean((hv_hat_db - hv_obs_db) ** 2, axis=0)
            + np.mean((hh_hat_db - hh_obs_db) ** 2, axis=0)
        )

        agb_out[final_mask] = w_sol.astype(np.float32)
        carbon_out[final_mask] = (0.47 * w_sol).astype(np.float32)
        hh_pred[final_mask] = hh_hat_v.astype(np.float32)
        hv_pred[final_mask] = hv_hat_v.astype(np.float32)
        rmse_db[final_mask] = np.sqrt(mse_db).astype(np.float32)

    hh_masked = np.where(final_mask, hh_mean, np.nan).astype(np.float32)
    hv_masked = np.where(final_mask, hv_mean, np.nan).astype(np.float32)
    return NisarInversionResult(
        agb_mgha=agb_out,
        agb_init_mgha=np.where(final_mask, agb_init, np.nan).astype(np.float32),
        carbon_mgha=carbon_out,
        hh_linear=hh_masked,
        hv_linear=hv_masked,
        hh_db=linear_to_db(hh_masked),
        hv_db=linear_to_db(hv_masked),
        hh_pred_linear=hh_pred,
        hv_pred_linear=hv_pred,
        rmse_db=rmse_db,
        valid_mask=final_mask,
    )
