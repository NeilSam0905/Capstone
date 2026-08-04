"""
tests/test_intermittent.py
------------------------------------------------------------------
Tests for forecasting/intermittent.py.

Three groups:

  * the identities that define the methods (SBA is Croston x (1-alpha/2);
    a constant series forecasts to that constant; sizes and intervals
    recombine to the point forecast)
  * hand-computed reference series, worked out independently of the
    implementation so a plausible-but-wrong smoothing order is caught
  * the intermittency behaviour that motivates using these at all
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.intermittent import (
    croston, croston_fit_predict, decompose, sba, sba_fit_predict,
)

ALPHAS = [0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]

# a canonical intermittent series
INTERMITTENT = np.array([0, 2, 0, 0, 5, 0, 0, 0, 3, 0, 1, 0, 0, 4], dtype=float)


# ---- the defining identities -----------------------------------------

@pytest.mark.parametrize("alpha", ALPHAS)
def test_sba_is_croston_scaled_by_one_minus_half_alpha(alpha):
    c = croston(INTERMITTENT, alpha).point_forecast
    s = sba(INTERMITTENT, alpha).point_forecast
    assert s == pytest.approx(c * (1 - alpha / 2), rel=1e-9)


@pytest.mark.parametrize("alpha", ALPHAS)
@pytest.mark.parametrize("seed", range(5))
def test_sba_identity_holds_on_random_series(alpha, seed):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 8, size=60).astype(float)
    y[rng.random(60) < 0.7] = 0.0            # force intermittency
    if not y.any():
        y[0] = 3.0
    c = croston(y, alpha).point_forecast
    s = sba(y, alpha).point_forecast
    assert s == pytest.approx(c * (1 - alpha / 2), rel=1e-9)


@pytest.mark.parametrize("alpha", ALPHAS)
@pytest.mark.parametrize("constant", [1.0, 4.0, 7.5, 250.0])
def test_croston_of_a_constant_nonzero_series_is_that_constant(alpha, constant):
    """Every period has demand, so every interval is 1 and every size is
    the constant: size/interval == the constant, for any alpha."""
    y = np.full(30, constant)
    assert croston(y, alpha).point_forecast == pytest.approx(constant)


@pytest.mark.parametrize("alpha", ALPHAS)
def test_intervals_and_sizes_recombine_to_the_point_forecast(alpha):
    for y in (INTERMITTENT, np.array([5.0, 0, 0, 2.0, 0, 9.0]), np.full(12, 3.0)):
        for r in (croston(y, alpha), sba(y, alpha)):
            assert r.recombine() == pytest.approx(r.point_forecast, rel=1e-12)
            assert (r.size_estimate / r.interval_estimate * r.bias_factor
                    == pytest.approx(r.point_forecast, rel=1e-12))


def test_bias_factor_is_one_for_croston_and_shrinks_for_sba():
    assert croston(INTERMITTENT, 0.2).bias_factor == 1.0
    assert sba(INTERMITTENT, 0.2).bias_factor == pytest.approx(0.9)
    assert sba(INTERMITTENT, 0.2).point_forecast < croston(INTERMITTENT, 0.2).point_forecast


# ---- decomposition ----------------------------------------------------

def test_decompose_splits_sizes_and_intervals():
    sizes, intervals = decompose([0, 0, 5, 0, 3])
    assert list(sizes) == [5.0, 3.0]
    assert list(intervals) == [3.0, 2.0]     # 3 periods to the first, then 2


def test_decompose_of_a_dense_series_gives_unit_intervals():
    sizes, intervals = decompose([2.0, 3.0, 4.0])
    assert list(sizes) == [2.0, 3.0, 4.0]
    assert list(intervals) == [1.0, 1.0, 1.0]


def test_decompose_of_an_all_zero_series_is_empty():
    sizes, intervals = decompose(np.zeros(10))
    assert sizes.size == 0 and intervals.size == 0


def test_decompose_intervals_sum_to_the_index_of_the_last_demand():
    y = INTERMITTENT
    _sizes, intervals = decompose(y)
    assert intervals.sum() == np.flatnonzero(y > 0)[-1] + 1


def test_negative_demand_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        decompose([1.0, -2.0, 3.0])


# ---- hand-computed reference -----------------------------------------

def test_reference_series_matches_a_hand_computation():
    """y = [0, 0, 4, 0, 6], alpha = 0.5, worked by hand:

        sizes     = [4, 6]      intervals = [3, 2]
        z: init 4 -> 4 + 0.5*(6-4) = 5.0
        p: init 3 -> 3 + 0.5*(2-3) = 2.5
        croston = 5.0 / 2.5 = 2.0
        sba     = 2.0 * (1 - 0.25) = 1.5
    """
    y = [0, 0, 4, 0, 6]
    c = croston(y, alpha=0.5)
    assert c.size_estimate == pytest.approx(5.0)
    assert c.interval_estimate == pytest.approx(2.5)
    assert c.point_forecast == pytest.approx(2.0)
    assert sba(y, alpha=0.5).point_forecast == pytest.approx(1.5)


def test_alpha_one_uses_only_the_most_recent_demand():
    """alpha = 1 discards all history: the estimates collapse to the last
    size and the last interval."""
    y = [9, 0, 0, 0, 0, 2]          # last size 2, last interval 5
    c = croston(y, alpha=1.0)
    assert c.size_estimate == pytest.approx(2.0)
    assert c.interval_estimate == pytest.approx(5.0)
    assert c.point_forecast == pytest.approx(0.4)


def test_single_demand_series_uses_the_initialisation_directly():
    c = croston([0, 0, 0, 7], alpha=0.3)
    assert c.size_estimate == pytest.approx(7.0)
    assert c.interval_estimate == pytest.approx(4.0)
    assert c.point_forecast == pytest.approx(1.75)


# ---- intermittency behaviour -----------------------------------------

def test_never_sold_series_forecasts_zero():
    r = croston(np.zeros(50))
    assert r.point_forecast == 0.0
    assert r.n_nonzero == 0
    assert np.isnan(r.interval_estimate)


def test_longer_gaps_lower_the_rate_for_the_same_sizes():
    """The core claim: same demand sizes, sparser arrivals, lower rate."""
    dense = croston([0, 5, 0, 5, 0, 5], alpha=0.3).point_forecast
    sparse = croston([0, 0, 0, 5, 0, 0, 0, 0, 0, 5], alpha=0.3).point_forecast
    assert sparse < dense


def test_rate_is_units_per_period_not_units_per_sale():
    """5 units every 2 periods is 2.5 units/period."""
    r = croston([0, 5, 0, 5, 0, 5, 0, 5], alpha=0.4)
    assert r.interval_estimate == pytest.approx(2.0)
    assert r.point_forecast == pytest.approx(2.5)


def test_counts_are_reported():
    r = croston(INTERMITTENT, 0.2)
    assert r.n_periods == len(INTERMITTENT)
    assert r.n_nonzero == int((INTERMITTENT > 0).sum()) == 5
    assert r.average_size == pytest.approx(INTERMITTENT[INTERMITTENT > 0].mean())


def test_str_is_informative():
    s = str(croston(INTERMITTENT, 0.2))
    assert "croston" in s and "size" in s and "interval" in s


# ---- validation -------------------------------------------------------

@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5, 2.0])
def test_alpha_outside_zero_to_one_is_rejected(bad):
    with pytest.raises(ValueError, match="alpha"):
        croston(INTERMITTENT, bad)


def test_empty_series_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        croston([])


# ---- harness wrappers -------------------------------------------------

@pytest.mark.parametrize("horizon", [1, 7, 30])
def test_fit_predict_wrappers_fill_the_horizon(horizon):
    for factory in (croston_fit_predict, sba_fit_predict):
        pred = factory(0.2)(INTERMITTENT, horizon)
        assert pred.shape == (horizon,)
        assert np.all(np.isfinite(pred))
        assert len(set(pred.tolist())) == 1        # a flat rate


def test_fit_predict_matches_the_underlying_estimator():
    assert croston_fit_predict(0.25)(INTERMITTENT, 5)[0] == pytest.approx(
        croston(INTERMITTENT, 0.25).point_forecast)
    assert sba_fit_predict(0.25)(INTERMITTENT, 5)[0] == pytest.approx(
        sba(INTERMITTENT, 0.25).point_forecast)


def test_fit_predict_on_a_never_sold_series_returns_zeros():
    pred = croston_fit_predict(0.2)(np.zeros(40), 30)
    assert np.all(pred == 0.0)
