"""
tests/test_evaluate.py
------------------------------------------------------------------
Property tests for forecasting/evaluate.py.

These are the gate for the harness, because a leaky harness produces
beautiful numbers and nothing downstream would question them. The four
properties the design guide names:

    no fold uses data at or after its origin        (leakage)
    every SKU has >= 3 folds, or is listed insufficient
    every method is scored on identical folds
    the scoring horizon is a 30-day aggregate

The leakage test is done two ways: structurally (index arithmetic) and
behaviourally, with a deliberately cheating model that reports any test
value it can see. If the harness ever hands a model its own test window,
the cheater's perfect score gives it away.
------------------------------------------------------------------
"""
import numpy as np
import pandas as pd
import pytest

from forecasting.evaluate import (
    DEFAULT_HORIZON, DEFAULT_MIN_FOLDS, Fold, aggregate_blocks,
    evaluate_methods, make_folds, summarise, walk_forward_evaluate,
)

RNG = np.random.default_rng(20260804)


def series(n, seed=0):
    return np.random.default_rng(seed).integers(0, 12, size=n).astype(float)


def mean_model(train, horizon):
    return np.full(horizon, train.mean() if train.size else 0.0)


def zero_model(train, horizon):
    return np.zeros(horizon)


# ---- 1. leakage -------------------------------------------------------

@pytest.mark.parametrize("n", [150, 200, 365, 500, 731])
@pytest.mark.parametrize("horizon", [7, 30, 60])
def test_no_fold_uses_data_at_or_after_its_origin(n, horizon):
    """Structural: the training window must end exactly at the origin and
    the test window must start there. Never one index earlier."""
    folds = make_folds(n, horizon=horizon, min_folds=1, min_train=30)
    for f in folds:
        assert f.train_end == f.origin
        assert f.test_start == f.origin
        assert f.test_start >= f.train_end        # no overlap
        assert f.test_end <= n                     # never past the series
        assert f.n_train > 0
        f.assert_no_leakage(n)


@pytest.mark.parametrize("n", [200, 365, 500])
def test_training_slice_never_contains_a_test_value_position(n):
    values = np.arange(n, dtype=float)      # value == its own index
    for f in make_folds(n, horizon=30, min_folds=1, min_train=30):
        train = f.train_slice(values)
        test = f.test_slice(values)
        assert train.max() < test.min(), "a training value came from at/after the origin"
        assert len(train) == f.origin


def test_a_cheating_model_cannot_see_its_test_window():
    """Behavioural: this model returns the largest value it was given. The
    series is strictly increasing, so if the harness ever leaked the test
    window the prediction would match the actual exactly."""
    n = 400
    values = np.arange(n, dtype=float)
    seen = []

    def peeker(train, horizon):
        seen.append((train.min(), train.max(), train.size))
        return np.full(horizon, train.max())

    ev = walk_forward_evaluate("sku", values, peeker, "peeker", horizon=30)
    assert ev.sufficient

    for row, (_lo, hi, size) in zip(ev.rows, seen):
        origin = row["origin"]
        # the largest value the model saw is the one just before the origin
        assert hi == origin - 1
        assert size == origin
        # and it never reproduced the true aggregate
        assert row["pred_30d"] != row["actual_30d"]


def test_fold_rejects_a_test_window_past_the_series():
    with pytest.raises(ValueError):
        Fold(0, train_end=90, horizon=30).assert_no_leakage(n_total=100)


def test_fold_rejects_an_empty_training_window():
    with pytest.raises(ValueError):
        Fold(0, train_end=0, horizon=30).assert_no_leakage(n_total=100)


# ---- 2. minimum 3 folds, or reported insufficient --------------------

def test_short_series_yields_no_folds_rather_than_one():
    """The failure this guards: silently scoring a thin SKU on a single
    fold and listing it beside SKUs that got three."""
    assert make_folds(100, horizon=30, min_folds=3, min_train=60) == []


def test_short_series_is_reported_insufficient_not_scored():
    ev = walk_forward_evaluate("thin", series(100, 1), mean_model, "mean")
    assert ev.sufficient is False
    assert ev.n_folds == 0
    assert ev.rows == []
    assert "fewer than 3 folds" in ev.reason


def test_every_scored_sku_has_at_least_min_folds():
    data = {
        "long_a": series(500, 1),
        "long_b": series(400, 2),
        "thin_a": series(100, 3),
        "thin_b": series(80, 4),
    }
    results, insufficient = evaluate_methods(data, {"mean": mean_model})

    assert set(insufficient) == {"thin_a", "thin_b"}
    assert not results.empty
    for sku, g in results.groupby("sku"):
        assert g["fold"].nunique() >= DEFAULT_MIN_FOLDS

    # and every SKU is accounted for exactly once, either scored or listed
    assert set(results["sku"]) | set(insufficient) == set(data)
    assert not (set(results["sku"]) & set(insufficient))


@pytest.mark.parametrize("n,expected_at_least", [(150, 3), (400, 3), (800, 3)])
def test_longer_series_get_more_folds_never_fewer(n, expected_at_least):
    folds = make_folds(n, horizon=30, min_folds=3, min_train=60)
    if folds:
        assert len(folds) >= expected_at_least


# ---- 3. identical folds across methods -------------------------------

def test_every_method_is_scored_on_identical_folds():
    data = {"a": series(500, 5), "b": series(430, 6)}
    methods = {"mean": mean_model, "zero": zero_model,
               "last": lambda t, h: np.full(h, t[-1])}

    results, _ = evaluate_methods(data, methods)

    for sku, g in results.groupby("sku"):
        layouts = {
            m: sorted(map(tuple, mg[["fold", "origin", "n_train", "horizon"]].values.tolist()))
            for m, mg in g.groupby("method")
        }
        reference = next(iter(layouts.values()))
        for method, layout in layouts.items():
            assert layout == reference, f"{method} was scored on different folds for {sku}"


def test_every_method_sees_the_same_actuals():
    data = {"a": series(400, 7)}
    methods = {"mean": mean_model, "zero": zero_model}
    results, _ = evaluate_methods(data, methods)

    pivot = results.pivot_table(index="fold", columns="method", values="actual_30d")
    for col in pivot.columns[1:]:
        pd.testing.assert_series_equal(
            pivot[pivot.columns[0]], pivot[col], check_names=False)


def test_reused_folds_are_the_same_objects_across_methods():
    values = series(400, 8)
    folds = make_folds(values.size)
    a = walk_forward_evaluate("s", values, mean_model, "mean", folds=folds)
    b = walk_forward_evaluate("s", values, zero_model, "zero", folds=folds)
    assert [r["origin"] for r in a.rows] == [r["origin"] for r in b.rows]
    assert [r["actual_30d"] for r in a.rows] == [r["actual_30d"] for r in b.rows]


# ---- 4. the scoring horizon is a 30-day aggregate --------------------

def test_scoring_horizon_is_a_30_day_aggregate():
    """One score per fold, and it is the SUM over 30 days - not 30 daily
    scores, and not a daily average."""
    values = np.ones(400)                      # 1 unit/day
    ev = walk_forward_evaluate("s", values, mean_model, "mean", horizon=30)

    for row in ev.rows:
        assert row["horizon"] == DEFAULT_HORIZON == 30
        assert row["actual_30d"] == 30.0        # 30 days x 1 unit
        assert row["pred_30d"] == pytest.approx(30.0)


def test_actual_aggregate_equals_the_sum_of_the_test_window():
    values = series(400, 9)
    ev = walk_forward_evaluate("s", values, zero_model, "zero", horizon=30)
    for row in ev.rows:
        o = row["origin"]
        assert row["actual_30d"] == pytest.approx(values[o:o + 30].sum())


def test_zero_model_abs_error_equals_the_actual_aggregate():
    values = series(400, 10)
    ev = walk_forward_evaluate("s", values, zero_model, "zero")
    for row in ev.rows:
        assert row["abs_error"] == pytest.approx(row["actual_30d"])


@pytest.mark.parametrize("horizon", [7, 14, 30, 60])
def test_horizon_is_configurable_and_respected(horizon):
    values = np.ones(600)
    ev = walk_forward_evaluate("s", values, zero_model, "zero",
                               horizon=horizon, min_train=60)
    for row in ev.rows:
        assert row["horizon"] == horizon
        assert row["actual_30d"] == float(horizon)


def test_a_model_returning_the_wrong_length_is_rejected():
    with pytest.raises(ValueError, match="predictions for horizon"):
        walk_forward_evaluate("s", series(400, 11),
                              lambda t, h: np.zeros(h - 1), "short")


# ---- aggregate_blocks -------------------------------------------------

def test_aggregate_blocks_sums_non_overlapping_windows():
    v = np.arange(1, 13, dtype=float)          # 1..12
    assert list(aggregate_blocks(v, 4)) == [1 + 2 + 3 + 4, 5 + 6 + 7 + 8, 9 + 10 + 11 + 12]


def test_aggregate_blocks_drops_the_oldest_remainder_not_the_newest():
    v = np.array([99.0, 1.0, 1.0, 1.0, 1.0])   # ragged: 5 values, block 2
    assert list(aggregate_blocks(v, 2)) == [2.0, 2.0]   # the 99 is dropped


def test_aggregate_blocks_returns_empty_when_too_short():
    assert aggregate_blocks(np.ones(3), 30).size == 0


# ---- summarise --------------------------------------------------------

def test_summarise_ranks_a_better_method_ahead_of_a_worse_one():
    data = {"s": series(400, 21), "t": series(400, 22)}
    methods = {"bad": lambda t, h: np.zeros(h), "good": mean_model}
    results, _ = evaluate_methods(data, methods)

    s = summarise(results)
    assert s.iloc[0]["method"] == "good"
    assert s[s["method"] == "good"]["mae"].iloc[0] < s[s["method"] == "bad"]["mae"].iloc[0]


def test_summarise_falls_back_to_mae_when_mase_is_undefined():
    """A flat series makes every MASE denominator zero, so MASE is NaN for
    every method. The ranking must degrade to MAE, not to the order the
    methods happened to be defined in - note 'bad' is inserted first."""
    data = {"s": np.full(400, 5.0)}
    methods = {"bad": lambda t, h: np.zeros(h), "good": lambda t, h: np.full(h, 5.0)}
    results, _ = evaluate_methods(data, methods)

    s = summarise(results)
    assert s["mase"].isna().all()             # the degenerate case
    assert s.iloc[0]["method"] == "good"      # ranked by MAE regardless
    assert s.iloc[0]["mae"] < s.iloc[1]["mae"]


def test_summarise_counts_skus_and_folds():
    data = {"a": series(500, 12), "b": series(500, 13)}
    results, _ = evaluate_methods(data, {"mean": mean_model})
    s = summarise(results)
    assert s.iloc[0]["n_skus"] == 2
    assert s.iloc[0]["n_folds"] == len(results)


def test_summarise_of_an_empty_frame_is_empty():
    assert summarise(pd.DataFrame()).empty
