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
    tsb, tsb_fit_predict,
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

def test_classical_init_reference_series_matches_a_hand_computation():
    """init='first', y = [0, 0, 4, 0, 6], alpha = 0.5, worked by hand:

        sizes     = [4, 6]      intervals = [3, 2]
        z: init 4 -> 4 + 0.5*(6-4) = 5.0
        p: init 3 -> 3 + 0.5*(2-3) = 2.5
        croston = 5.0 / 2.5 = 2.0
        sba     = 2.0 * (1 - 0.25) = 1.5
    """
    y = [0, 0, 4, 0, 6]
    c = croston(y, alpha=0.5, init="first")
    assert c.size_estimate == pytest.approx(5.0)
    assert c.interval_estimate == pytest.approx(2.5)
    assert c.point_forecast == pytest.approx(2.0)
    assert sba(y, alpha=0.5, init="first").point_forecast == pytest.approx(1.5)


def test_mean_init_reference_series_matches_a_hand_computation():
    """init='mean' (the default), same series, alpha = 0.5:

        z: init mean(4,6)=5 -> 5+0.5*(4-5)=4.5 -> 4.5+0.5*(6-4.5)=5.25
        p: init mean(3,2)=2.5 -> 2.5+0.5*(3-2.5)=2.75 -> 2.75+0.5*(2-2.75)=2.375
        croston = 5.25 / 2.375
    """
    y = [0, 0, 4, 0, 6]
    c = croston(y, alpha=0.5)
    assert c.size_estimate == pytest.approx(5.25)
    assert c.interval_estimate == pytest.approx(2.375)
    assert c.point_forecast == pytest.approx(5.25 / 2.375)


def test_alpha_one_uses_only_the_most_recent_demand():
    """alpha = 1 discards all history and the initialisation with it, so
    both init modes collapse to the last size and the last interval."""
    y = [9, 0, 0, 0, 0, 2]          # last size 2, last interval 5
    for init in ("first", "mean"):
        c = croston(y, alpha=1.0, init=init)
        assert c.size_estimate == pytest.approx(2.0)
        assert c.interval_estimate == pytest.approx(5.0)
        assert c.point_forecast == pytest.approx(0.4)


def test_single_demand_series_uses_the_initialisation_directly():
    for init in ("first", "mean"):
        c = croston([0, 0, 0, 7], alpha=0.3, init=init)
        assert c.size_estimate == pytest.approx(7.0)
        assert c.interval_estimate == pytest.approx(4.0)
        assert c.point_forecast == pytest.approx(1.75)


def test_mean_init_is_far_less_sensitive_to_a_thin_series():
    """The failure that made this the default. Four sales clustered at the
    start of a long series: classical init leaves the size estimate stuck
    near the first demand, mean init lands on the empirical mean."""
    y = np.zeros(600)
    y[[0, 1, 3, 4]] = [200.0, 20.0, 20.0, 25.0]

    classical = croston(y, alpha=0.1, init="first")
    mean_init = croston(y, alpha=0.1, init="mean")

    assert classical.size_estimate > 150          # still anchored to the 200
    assert mean_init.size_estimate < 100          # near mean(200,20,20,25)=66.25
    assert mean_init.point_forecast < classical.point_forecast


def test_init_must_be_a_known_mode():
    with pytest.raises(ValueError, match="init must be"):
        croston(INTERMITTENT, 0.2, init="whatever")


def test_croston_cannot_see_trailing_zeros():
    """A structural property worth pinning, because it explains the
    benchmark result: Croston updates only on periods when demand
    arrives, so an SKU that stops selling keeps its last rate forever.
    Appending 500 zero days changes nothing."""
    y = np.array([0, 5, 0, 0, 3, 0, 4], dtype=float)
    extended = np.concatenate([y, np.zeros(500)])
    assert (croston(extended, 0.2).point_forecast
            == pytest.approx(croston(y, 0.2).point_forecast))


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


# ======================================================================
# TSB (Teunter-Syntetos-Babai)
# ======================================================================

@pytest.mark.parametrize("alpha", ALPHAS)
@pytest.mark.parametrize("constant", [1.0, 4.0, 7.5, 250.0])
def test_tsb_of_a_constant_nonzero_series_is_that_constant(alpha, constant):
    """Demand every period, so probability is 1 and size is the constant."""
    y = np.full(30, constant)
    assert tsb(y, alpha, beta=alpha).point_forecast == pytest.approx(constant)


@pytest.mark.parametrize("alpha", ALPHAS)
def test_probability_and_size_recombine_to_the_point_forecast(alpha):
    for y in (INTERMITTENT, np.array([5.0, 0, 0, 2.0, 0, 9.0]), np.full(12, 3.0)):
        r = tsb(y, alpha, beta=alpha)
        assert r.recombine() == pytest.approx(r.point_forecast, rel=1e-12)
        assert (r.probability_estimate * r.size_estimate
                == pytest.approx(r.point_forecast, rel=1e-12))


# ---- the obsolescence property: the whole reason TSB is here ---------

DEAD = [5.0, 5.0, 5.0] + [0.0] * 200


def test_tsb_decays_on_a_dead_sku_where_croston_holds_flat():
    """Three sales, then 200 periods of nothing. Croston never updates on
    a zero period, so its forecast is exactly what it was after the third
    sale. TSB keeps updating the probability and decays toward zero."""
    t = tsb(DEAD).point_forecast
    c = croston(DEAD).point_forecast

    assert t < c
    assert t < 0.5 * c          # decays, not merely lower
    assert t == pytest.approx(0.0, abs=1e-6)


def test_croston_is_unchanged_by_the_trailing_zeros_but_tsb_is_not():
    """States the contrast directly: the same 200 zeros are invisible to
    one method and decisive for the other."""
    alive = [5.0, 5.0, 5.0]
    assert croston(DEAD).point_forecast == pytest.approx(croston(alive).point_forecast)
    assert tsb(DEAD).point_forecast < tsb(alive).point_forecast


@pytest.mark.parametrize("n_zeros", [0, 25, 50, 100, 200, 400])
def test_tsb_forecast_falls_monotonically_with_the_length_of_the_dead_tail(n_zeros):
    """More trailing zeros must never raise the forecast."""
    short = tsb([5.0, 5.0, 5.0] + [0.0] * n_zeros).point_forecast
    longer = tsb([5.0, 5.0, 5.0] + [0.0] * (n_zeros + 25)).point_forecast
    assert longer <= short + 1e-12


def test_tsb_probability_tracks_the_share_of_selling_periods():
    """A dense series keeps a high probability; a sparse one does not."""
    dense = tsb(np.full(60, 3.0))
    sparse = tsb([3.0] + [0.0] * 59)
    assert dense.probability_estimate > 0.9
    assert sparse.probability_estimate < 0.1
    assert dense.point_forecast > sparse.point_forecast


def test_tsb_implied_interval_is_the_reciprocal_of_probability():
    r = tsb(INTERMITTENT, 0.2, 0.2)
    assert r.implied_interval == pytest.approx(1.0 / r.probability_estimate)


def test_tsb_never_sold_series_forecasts_zero():
    r = tsb(np.zeros(50))
    assert r.point_forecast == 0.0
    assert r.probability_estimate == 0.0
    assert r.n_nonzero == 0


def test_tsb_counts_are_reported():
    r = tsb(INTERMITTENT, 0.2, 0.2)
    assert r.n_periods == len(INTERMITTENT)
    assert r.n_nonzero == int((INTERMITTENT > 0).sum()) == 5
    assert r.average_size == pytest.approx(INTERMITTENT[INTERMITTENT > 0].mean())


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_tsb_rejects_bad_smoothing_constants(bad):
    with pytest.raises(ValueError, match="alpha"):
        tsb(INTERMITTENT, alpha=bad)
    with pytest.raises(ValueError, match="beta"):
        tsb(INTERMITTENT, beta=bad)


def test_tsb_rejects_empty_and_negative_series():
    with pytest.raises(ValueError, match="empty"):
        tsb([])
    with pytest.raises(ValueError, match="negative"):
        tsb([1.0, -2.0])


def test_tsb_str_is_informative():
    s = str(tsb(INTERMITTENT, 0.2, 0.2))
    assert "tsb" in s and "p " in s and "size" in s


@pytest.mark.parametrize("horizon", [1, 7, 30])
def test_tsb_fit_predict_fills_the_horizon(horizon):
    pred = tsb_fit_predict(0.2, 0.2)(INTERMITTENT, horizon)
    assert pred.shape == (horizon,)
    assert np.all(np.isfinite(pred)) and np.all(pred >= 0)
    assert pred[0] == pytest.approx(tsb(INTERMITTENT, 0.2, 0.2).point_forecast)
