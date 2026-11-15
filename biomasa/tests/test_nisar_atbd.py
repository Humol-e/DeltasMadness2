"""Unit tests for the NISAR L3 AGB ATBD physical algorithm."""

from __future__ import annotations

import numpy as np
import pytest

from nisar_biomass_monitor.config import load_nisar_config
from nisar_biomass_monitor.nisar_atbd import (
    build_sar_valid_mask,
    compute_initial_agb,
    double_bounce_scattering,
    forward_nisar_hh_hv,
    invert_agb_scene,
    optimize_pixel_lbfgsb,
    surface_scattering,
    volume_scattering,
)


def test_load_nisar_config_matches_calibrated_ini() -> None:
    params = load_nisar_config()
    assert params.A_HV == pytest.approx(0.0079244779005501)
    assert params.A_HH == pytest.approx(0.0926524445492314)
    assert params.D_HV == pytest.approx(1.0)
    assert params.s == pytest.approx(0.026613616742048)
    assert len(params.as_vector()) == 13


def test_forward_components_sum_consistently() -> None:
    params = load_nisar_config()
    agb = np.array([20.0, 60.0, 120.0, 180.0])
    hh, hv = forward_nisar_hh_hv(agb, params)
    hv_sum = (
        volume_scattering(agb, params.A_HV, params.B_HV, params.alpha_HV)
        + double_bounce_scattering(agb, params.B_HV, params.C_HV, params.delta_HV, params.s)
        + surface_scattering(agb, params.B_HV, params.D_HV, params.s)
    )
    np.testing.assert_allclose(hv, hv_sum, rtol=1e-12)
    assert np.all(np.diff(hv) > 0.0)
    assert np.all(hh > hv)


def test_synthetic_agb_roundtrip_and_lbfgsb_agreement() -> None:
    params = load_nisar_config()
    true_agb = np.array([[25.0, 60.0], [110.0, 175.0]], dtype=np.float64)
    hh, hv = forward_nisar_hh_hv(true_agb, params)

    inv = invert_agb_scene(hh, hv, params)
    assert np.all(inv.valid_mask)
    np.testing.assert_allclose(inv.agb_mgha, true_agb, atol=0.05)
    np.testing.assert_allclose(inv.carbon_mgha, 0.47 * inv.agb_mgha, rtol=1e-5)

    # Verify exact match against scipy.optimize.minimize(L-BFGS-B)
    w0 = compute_initial_agb(hv)
    for r in range(2):
        for c in range(2):
            w_ref = optimize_pixel_lbfgsb(hh[r, c], hv[r, c], float(w0[r, c]), params)
            assert abs(float(inv.agb_mgha[r, c]) - w_ref) < 0.01


def test_sar_valid_mask_filters_bounds_and_nans() -> None:
    hh = np.array([[0.25, 1.25], [0.30, np.nan]], dtype=np.float32)
    hv = np.array([[0.06, 0.08], [0.005, 0.05]], dtype=np.float32)
    mask = build_sar_valid_mask(hh, hv)
    expected = np.array([[True, False], [False, False]])
    np.testing.assert_array_equal(mask, expected)


def test_compute_initial_agb_clipping() -> None:
    hv = np.array([-0.1, 0.02, 0.10, 0.30])
    init = compute_initial_agb(hv, min_agb=0.0, max_agb=300.0)
    assert np.isnan(init[0])
    assert init[1] == pytest.approx(4.0)
    assert init[2] == pytest.approx(100.0)
    assert init[3] == pytest.approx(300.0)
