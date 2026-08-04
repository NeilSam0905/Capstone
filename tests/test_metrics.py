"""
tests/test_metrics.py
------------------------------------------------------------------
Property tests for forecasting/metrics.py.

These are mostly identities that must hold for any correct
implementation - a perfect forecast scores zero, the naive forecast
scores MASE 1.0, a rate stays inside [0, 1] - plus the two behaviours
this project specifically needs: MAPE counting its undefined cases
instead of dropping them, and the sem-break/standard-period split.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.metrics import (
    cycle_service_level, evaluate, evaluate_by_period, fill_rate,
    mae, mape, mape_undefined_count, mase, naive_scale, rmse,
)

RNG = np.random.default_rng(20260804)


# ---- perfect forecast -------------------------------------------------

def test_perfect_forecast_scores_zero():
    y = np.array([3.0, 0.0, 7.0, 12.0, 0.0, 5.0])
    assert mae(y, y) == 0
    assert rmse(y, y) == 0


@pytest.mark.parametrize("n", [1, 5, 50, 500])
def test_perfect_forecast_scores_zero_at_any_length(n):
    y = RNG.integers(0, 40, size=n).astype(float)
    assert mae(y, y) == 0
    assert rmse(y, y) == 0


def test_perfect_forecast_has_zero_mape_where_defined():
    y = np.array([4.0, 9.0, 2.0])          # no zeros -> fully defined
    r = mape(y, y)
    assert r.value == pytest.approx(0.0)
    assert r.n_undefined == 0
    assert r.coverage == 1.0


def test_rmse_penalises_a_single_large_miss_more_than_mae():
    y = np.zeros(10)
    spread = np.full(10, 2.0)
    spiky = np.zeros(10)
    spiky[0] = 20.0
    assert mae(y, spread) == pytest.approx(mae(y, spiky))
    assert rmse(y, spiky) > rmse(y, spread)


# ---- MASE -------------------------------------------------------------

def test_mase_of_the_naive_forecast_is_exactly_one():
    """The defining property: scoring the naive forecast against a scale
    derived from that same naive forecast must give exactly 1.0."""
    series = np.array([5.0, 8.0, 3.0, 9.0, 4.0, 11.0, 6.0, 2.0, 7.0, 10.0])
    train, test = series[:6], series[6:]

    # one-step naive: each prediction is the previous actual
    naive_forecast = np.concatenate([[train[-1]], test[:-1]])
    same = mae(test, naive_forecast)

    assert mase(test, naive_forecast, naive_denominator=same) == 1.0


def test_mase_below_one_beats_naive_and_above_one_loses():
    train = np.array([10.0, 12.0, 8.0, 14.0, 9.0])
    test = np.array([11.0, 13.0])
    denom = naive_scale(train)

    assert mase(test, test, naive_denominator=denom) == 0.0
    assert mase(test, test + 100, naive_denominator=denom) > 1.0


def test_naive_scale_is_mean_absolute_first_difference():
    y = np.array([1.0, 4.0, 2.0, 8.0])          # diffs 3, 2, 6 -> mean 11/3
    assert naive_scale(y, seasonality=1) == pytest.approx(11.0 / 3.0)


def test_naive_scale_honours_seasonality():
    y = np.array([1.0, 10.0, 3.0, 12.0])        # lag-2 diffs 2, 2 -> mean 2
    assert naive_scale(y, seasonality=2) == pytest.approx(2.0)


def test_mase_on_flat_training_series_is_nan_not_a_crash():
    """A constant training series gives a zero denominator. NaN is the
    honest answer; a ZeroDivisionError or a silent 0.0 is not."""
    flat = np.full(8, 5.0)
    assert np.isnan(mase(np.array([5.0, 6.0]), np.array([5.0, 5.0]), y_train=flat))


def test_mase_requires_a_denominator_or_training_data():
    with pytest.raises(ValueError):
        mase(np.array([1.0, 2.0]), np.array([1.0, 2.0]))


def test_naive_scale_rejects_a_too_short_series():
    with pytest.raises(ValueError):
        naive_scale(np.array([4.0]), seasonality=1)


# ---- MAPE's undefined cases are counted, never dropped ----------------

def test_mape_undefined_count_equals_the_number_of_zero_actuals():
    y = np.array([0.0, 3.0, 0.0, 8.0, 0.0, 0.0, 1.0])
    assert mape_undefined_count(y) == int((y == 0).sum())


@pytest.mark.parametrize("n_zeros", [0, 1, 17, 99, 100])
def test_mape_undefined_count_tracks_zeros_at_any_density(n_zeros):
    y = np.concatenate([np.zeros(n_zeros), np.full(100 - n_zeros, 4.0)])
    RNG.shuffle(y)
    assert mape_undefined_count(y) == int((y == 0).sum()) == n_zeros


def test_mape_reports_undefined_count_alongside_its_value():
    y_true = np.array([0.0, 10.0, 0.0, 20.0])
    y_pred = np.array([1.0, 11.0, 2.0, 18.0])
    r = mape(y_true, y_pred)

    assert r.n_undefined == 2
    assert r.n_used == 2
    assert r.n_total == 4
    assert r.coverage == 0.5
    # computed only on the two defined points: 10% and 10%
    assert r.value == pytest.approx(10.0)


def test_mape_is_nan_when_every_actual_is_zero():
    """A whole sem-break window of zero demand. This is a real outcome,
    so it must not raise and must not silently read as 0% error."""
    r = mape(np.zeros(5), np.array([1.0, 0.0, 2.0, 0.0, 1.0]))
    assert np.isnan(r.value)
    assert r.n_undefined == 5
    assert r.n_used == 0
    assert "undefined" in str(r)


def test_mape_does_not_quietly_drop_points_from_its_denominator():
    """Guards the specific bug this design exists to prevent: computing
    the mean over the surviving points while reporting it as if it
    covered the whole series."""
    y_true = np.array([0.0, 0.0, 0.0, 100.0])
    y_pred = np.array([50.0, 50.0, 50.0, 150.0])
    r = mape(y_true, y_pred)
    assert r.value == pytest.approx(50.0)   # the one defined point
    assert r.coverage == 0.25               # and it says so


# ---- service levels stay inside [0, 1] --------------------------------

@pytest.mark.parametrize("seed", range(25))
def test_fill_rate_is_always_a_proportion(seed):
    rng = np.random.default_rng(seed)
    demand = rng.integers(0, 30, size=40).astype(float)
    supplied = rng.integers(0, 30, size=40).astype(float)
    assert 0.0 <= fill_rate(demand, supplied) <= 1.0


@pytest.mark.parametrize("seed", range(25))
def test_cycle_service_level_is_always_a_proportion(seed):
    rng = np.random.default_rng(seed)
    demand = rng.integers(0, 30, size=40).astype(float)
    supplied = rng.integers(0, 30, size=40).astype(float)
    assert 0.0 <= cycle_service_level(demand, supplied) <= 1.0


def test_fill_rate_endpoints():
    demand = np.array([5.0, 10.0, 0.0])
    assert fill_rate(demand, demand) == 1.0
    assert fill_rate(demand, np.zeros(3)) == 0.0
    assert fill_rate(demand, np.full(3, 100.0)) == 1.0     # oversupply caps at 1


def test_fill_rate_with_no_demand_is_one_not_zero():
    """No demand is not a service failure."""
    assert fill_rate(np.zeros(4), np.zeros(4)) == 1.0


def test_fill_rate_is_unit_weighted_and_csl_is_period_weighted():
    """One badly-missed high-volume day: fill rate collapses, cycle
    service level loses exactly one period out of four."""
    demand = np.array([1.0, 1.0, 1.0, 100.0])
    supplied = np.array([1.0, 1.0, 1.0, 0.0])
    assert fill_rate(demand, supplied) == pytest.approx(3.0 / 103.0)
    assert cycle_service_level(demand, supplied) == pytest.approx(0.75)


# ---- the standard_period / sem_break split ---------------------------

def test_evaluate_by_period_separates_the_two_pools():
    y_true = np.array([10.0, 12.0, 0.0, 0.0])
    y_pred = np.array([11.0, 11.0, 1.0, 0.0])
    is_break = np.array([False, False, True, True])

    out = evaluate_by_period(y_true, y_pred, is_break)

    assert out["standard_period"]["n"] == 2
    assert out["sem_break"]["n"] == 2
    assert out["overall"]["n"] == 4

    # section 3.3.4: MAE leads during breaks, MAPE during term time
    assert out["standard_period"]["primary_metric"] == "mape"
    assert out["sem_break"]["primary_metric"] == "mae"

    # the break pool is all-zero demand, so its MAPE is undefined - and
    # that is exactly why MAE is primary there
    assert np.isnan(out["sem_break"]["mape"])
    assert out["sem_break"]["mape_n_undefined"] == 2


def test_evaluate_by_period_reports_an_empty_pool_as_none():
    y = np.array([4.0, 5.0])
    out = evaluate_by_period(y, y, np.array([False, False]))
    assert out["sem_break"] is None
    assert out["standard_period"]["n"] == 2


def test_evaluate_by_period_pools_partition_the_series():
    rng = np.random.default_rng(7)
    y_true = rng.integers(0, 20, 60).astype(float)
    y_pred = rng.integers(0, 20, 60).astype(float)
    mask = rng.random(60) < 0.3

    out = evaluate_by_period(y_true, y_pred, mask)
    assert out["standard_period"]["n"] + out["sem_break"]["n"] == out["overall"]["n"] == 60


# ---- shape and input validation --------------------------------------

def test_length_mismatch_is_rejected():
    for fn in (mae, rmse, mape, fill_rate, cycle_service_level):
        with pytest.raises(ValueError):
            fn(np.array([1.0, 2.0]), np.array([1.0]))


def test_empty_series_is_rejected():
    with pytest.raises(ValueError):
        mae(np.array([]), np.array([]))


def test_evaluate_returns_the_full_metric_set():
    train = np.array([4.0, 6.0, 5.0, 9.0])
    y_true = np.array([5.0, 7.0])
    y_pred = np.array([6.0, 6.0])

    out = evaluate(y_true, y_pred, y_train=train)
    for key in ("n", "mae", "rmse", "mape", "mape_n_undefined",
                "mape_n_used", "mape_coverage", "mase"):
        assert key in out
    assert out["mae"] == pytest.approx(1.0)
