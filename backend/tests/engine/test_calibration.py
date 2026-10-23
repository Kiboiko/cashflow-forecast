import numpy as np
import pytest

from app.engine.calibration import apply_platt, calibrate, fit_platt, load_params


def test_apply_platt_is_monotone_and_stays_inside_the_unit_interval():
    p = np.linspace(0, 1, 101)
    c = apply_platt(p, 0.54, -1.23)
    assert (np.diff(c) >= 0).all() and c.min() > 0 and c.max() < 1


def test_identity_parameters_do_not_change_the_probability():
    p = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    np.testing.assert_allclose(apply_platt(p, 1.0, 0.0), p, atol=2e-3)


def test_fit_recovers_known_overconfidence():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.01, 0.99, 20_000)
    true = apply_platt(p, 0.5, -1.0)                 # на самом деле шанс ниже заявленного
    y = (rng.random(20_000) < true).astype(int)
    a, b = fit_platt(p, y)
    assert a == pytest.approx(0.5, abs=0.06) and b == pytest.approx(-1.0, abs=0.12)


def test_shipped_calibration_lowers_mid_range_chance_and_keeps_extremes_separated():
    load_params.cache_clear()
    assert calibrate(0.5) < 0.5
    assert calibrate(0.0) < 0.05 < calibrate(0.5) < calibrate(1.0) < 1.0
