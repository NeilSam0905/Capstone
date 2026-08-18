"""
tests/test_degenerate_forecast.py
------------------------------------------------------------------
Pins the degenerate-forecast result.

The rolling 30-day median leads the benchmark on MASE and prices **0 of
266 SKUs**. This is not a pipeline defect and must not be "fixed". It
follows from three facts that are each individually unremarkable:

    1. MAE is minimised by the conditional median of the predictive
       distribution.
    2. MASE is MAE divided by a constant scale factor, so it has the
       SAME minimiser.
    3. On a majority-zero series, the median IS zero.

Therefore, on this dataset, **any selection rule that minimises MASE
converges on the forecast "nothing will sell" by construction.** The
method that minimises forecast error is the one that cannot stock
anything.

That is a result, and it is the strongest one the project has - it turns
"MAPE <= 20% is unreachable" from a concession into a statement about the
acceptance criterion itself. It needs to be pinned so a later refactor
cannot silently change it and leave an unexplained anomaly behind.

Each step of the identity chain is asserted directly, not just the final
measured instance, so the reasoning survives even if the numbers move.
------------------------------------------------------------------
"""
import os
import sqlite3

import numpy as np
import pandas as pd
import pytest

from forecasting.baselines import rolling_mean_fit_predict, rolling_median_fit_predict
from forecasting.metrics import mae

SUMMARY_CSV = "data/model_benchmark_summary.csv"
DB = "ustore.db"


# ---- step 1: MAE is minimised by the median --------------------------

@pytest.mark.parametrize("seed", range(10))
def test_mae_is_minimised_by_the_median_not_the_mean(seed):
    """The textbook property, verified numerically: no constant beats the
    median under absolute error."""
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 30, size=200).astype(float)

    med = float(np.median(y))
    best = min(np.linspace(y.min(), y.max(), 601),
               key=lambda c: mae(y, np.full(y.size, c)))

    assert mae(y, np.full(y.size, med)) <= mae(y, np.full(y.size, float(y.mean()))) + 1e-9
    assert abs(best - med) <= (y.max() - y.min()) / 60.0 + 1e-9


@pytest.mark.parametrize("zero_frac", [0.55, 0.7, 0.9, 0.99])
def test_on_a_majority_zero_series_the_mae_minimiser_is_zero(zero_frac):
    """Step 3, isolated: once more than half the observations are zero,
    the constant that minimises MAE is zero itself."""
    n = 400
    n_zero = int(n * zero_frac)
    y = np.concatenate([np.zeros(n_zero),
                        np.random.default_rng(0).integers(1, 50, n - n_zero)]).astype(float)

    assert np.median(y) == 0.0
    zero_mae = mae(y, np.zeros(n))
    for candidate in (0.5, 1.0, 5.0, float(y.mean())):
        assert zero_mae <= mae(y, np.full(n, candidate)) + 1e-9


# ---- step 2: MASE shares MAE's minimiser -----------------------------

def test_mase_has_the_same_minimiser_as_mae():
    """MASE is MAE / scale, and the scale does not depend on the forecast,
    so ranking by MASE and ranking by MAE over CONSTANT forecasts pick the
    same value. This is why 'use MASE instead' does not escape the
    problem."""
    rng = np.random.default_rng(4)
    y = np.concatenate([np.zeros(300), rng.integers(1, 40, 100)]).astype(float)
    scale = 7.3           # any positive constant

    candidates = np.linspace(0, 40, 401)
    by_mae = min(candidates, key=lambda c: mae(y, np.full(y.size, c)))
    by_mase = min(candidates, key=lambda c: mae(y, np.full(y.size, c)) / scale)

    assert by_mae == by_mase == 0.0


# ---- the structural cause, measured on the real data -----------------

@pytest.mark.skipif(not os.path.exists(DB), reason="ustore.db not built")
def test_zero_fraction_of_fact_sales_exceeds_one_half():
    """The precondition. If this ever drops below 0.5 the whole result
    stops applying, so it is asserted rather than assumed."""
    con = sqlite3.connect(DB)
    total, zeros = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN quantity_sold = 0 THEN 1 ELSE 0 END) "
        "FROM Fact_Sales").fetchone()
    con.close()

    assert total > 0
    frac = zeros / total
    assert frac > 0.5, f"only {frac:.1%} of Fact_Sales rows are zero"
    assert (total, zeros) == (84399, 68541)      # the contract's figures


@pytest.mark.skipif(not os.path.exists(DB), reason="ustore.db not built")
def test_the_rolling_median_prices_nothing_on_the_real_series():
    """The measured instance, computed directly rather than read from a
    CSV: on the real daily series the trailing 30-day median is zero for
    every eligible SKU, while the trailing mean is not."""
    con = sqlite3.connect(DB)
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])
    con.close()

    idx = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    median_fn = rolling_median_fit_predict(30)
    mean_fn = rolling_mean_fit_predict(30)

    priced_median = priced_mean = eligible = 0
    for _pid, g in fact.groupby("product_id"):
        s = (g.groupby("calendar_date")["quantity_sold"].sum()
              .reindex(idx, fill_value=0.0).astype(float).to_numpy())
        if s.sum() <= 0:
            continue
        eligible += 1
        priced_median += float(np.sum(median_fn(s, 30))) > 0
        priced_mean += float(np.sum(mean_fn(s, 30))) > 0

    assert eligible == 266, "the eligible population moved"
    assert priced_median == 0, (
        f"the rolling median priced {priced_median} SKUs - the degenerate "
        f"result no longer holds and DEGENERATE_FORECAST.md needs revisiting")
    assert priced_mean > 0, "the comparison method prices nothing either"


# ---- the benchmark's own numbers, if the artifact is present ---------

@pytest.fixture
def summary():
    if not os.path.exists(SUMMARY_CSV):
        pytest.skip("model_benchmark.py has not been run")
    df = pd.read_csv(SUMMARY_CSV)
    if "n_skus_priced" not in df.columns:
        pytest.skip("summary predates the A17 decision-metric columns")
    return df


def test_rolling_median_ranks_first_by_mase(summary):
    ranked = summary.sort_values("mase", kind="stable").reset_index(drop=True)
    assert ranked.loc[0, "method"] == "rolling_median_30"


def test_rolling_median_prices_zero_skus_in_the_benchmark(summary):
    row = summary.loc[summary["method"] == "rolling_median_30"].iloc[0]
    assert int(row["n_skus_priced"]) == 0


def test_the_mase_leader_is_not_the_fill_rate_leader(summary):
    """The two rankings must disagree. If they ever agree, the tension
    this result describes has gone away and B3 gets much simpler."""
    by_mase = summary.sort_values("mase", kind="stable").iloc[0]["method"]
    by_fill = summary.sort_values("fill_rate_at_target", ascending=False,
                                  kind="stable").iloc[0]["method"]
    assert by_mase != by_fill


def test_the_cost_of_usefulness_is_about_ten_percent(summary):
    """The trade-off, as a number rather than as prose.

    TSB prices all 266 SKUs; the rolling median prices none. The error
    metric prefers the rolling median, and the size of that preference is
    the price of a usable system. Asserted so the figure quoted in
    DEGENERATE_FORECAST.md cannot drift away from the artifact.
    """
    s = summary.set_index("method")
    rm, tsb = float(s.loc["rolling_median_30", "mase"]), float(s.loc["tsb", "mase"])

    assert int(s.loc["rolling_median_30", "n_skus_priced"]) == 0
    assert int(s.loc["tsb", "n_skus_priced"]) == 266

    extra = (tsb / rm) - 1.0
    assert 0.09 < extra < 0.11, (
        f"the usefulness premium is {extra:.2%}, no longer ~10% - "
        f"DEGENERATE_FORECAST.md quotes 10.2% and needs updating")


def test_the_mase_leader_has_the_worst_fill_rate(summary):
    """Not merely 'not best' - on this data the error-metric winner is the
    service-level loser."""
    by_mase = summary.sort_values("mase", kind="stable").iloc[0]["method"]
    worst_fill = summary.sort_values("fill_rate_at_target",
                                     kind="stable").iloc[0]["method"]
    assert by_mase == worst_fill
