"""
scripts/compare_topdown_vs_persku.py
------------------------------------------------------------------
Step 7 of the category refactor: settle whether forecasting at CATEGORY
level and disaggregating back actually helps AT SKU LEVEL - the only
level the store can act on.

Why this script has to exist
----------------------------
`benchmark_category_level.py` established that a category series is much
easier to forecast: pooled WMAPE 49.3% against 74.8% per SKU. That result
is real but it is not transferable on its own, because nobody orders a
category. `step5_prescriptive.py` computes ROP, Safety Stock and EOQ per
SKU, so a category forecast is only worth adopting if the number that
reaches a SKU is better than the number that reaches it today.

The allocation step is where the gain can evaporate. Aggregation removes
intermittency from the target; the trailing-share weights then hand it
straight back, because those weights are estimated from the same sparse
per-SKU history the aggregation was meant to escape.

The comparison
--------------
Identical folds, identical target, one scoring path:

    per-SKU     yhat_i = fit_predict(sku_series_i[:origin], 30)
    top-down    yhat_i = fit_predict(category_series[:origin], 30) x w_i

    w_i = units_i(origin-90 .. origin) / sum_j units_j(origin-90 .. origin)

The weights are computed from data STRICTLY BEFORE the origin, from the
same per-SKU matrix being scored - so the top-down arm gets no look-ahead
the per-SKU arm does not also get.

Both arms are scored against the same actual: the SKU's own 30-day sum.
That is what makes the comparison decision-grade rather than suggestive.

Fast SKUs only, and a Fast-only category series
-----------------------------------------------
The category series here is the Fast-only one. Using the all-SKU total
while allocating to Fast members alone would credit Fast SKUs with their
category's Slow and Non-moving demand - in Outerwear that is 89% of the
units - and the top-down arm would win on inflation rather than accuracy.

Run (from the repo root, after step1b_categorize_products.py):
    python scripts/compare_topdown_vs_persku.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.baselines import ewma_fit_predict, rolling_mean_fit_predict
from forecasting.evaluate import aggregate_blocks, make_folds
from forecasting.intermittent import tsb_fit_predict
from forecasting.metrics import naive_scale

DB_PATH = os.path.join(ROOT, "ustore.db")
SERIES_FAST = "data/category_daily_series_fast.csv"
OUT_ROWS = "data/topdown_vs_persku_folds.csv"
OUT_SUMMARY = "data/topdown_vs_persku_summary.csv"

HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
SHARE_WINDOW = 90

# Same callable, two levels. rolling_mean_30 is what production runs per
# SKU today; rolling_mean_180 won the category benchmark. Each is scored
# at the level it was chosen for, plus the cross terms so the comparison
# cannot be an artefact of the window rather than the level.
PER_SKU_METHODS = {
    "persku_rolling_mean_30": rolling_mean_fit_predict(30),
    "persku_rolling_mean_180": rolling_mean_fit_predict(180),
    "persku_tsb": tsb_fit_predict(0.1, 0.1),
    "persku_ewma_a0.1": ewma_fit_predict(0.1),
}
TOPDOWN_METHODS = {
    "topdown_RM6_180": rolling_mean_fit_predict(180),
    "topdown_rolling_mean_30": rolling_mean_fit_predict(30),
    "topdown_tsb": tsb_fit_predict(0.1, 0.1),
}


def load_fast_sku_matrix(con):
    """Fast SKUs x full calendar, units per day.

    Same construction as step4_forecast_model.py::build_series - filtered
    to 'sale', reindexed onto the complete date range - so the per-SKU arm
    is scored on exactly the series production forecasts.
    """
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold,
               p.forecast_category, p.fsn_class
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'
    """, con, parse_dates=["calendar_date"])

    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")
    fast = fact[fact["fsn_class"] == "F"]

    wide = (fast.groupby(["product_id", "calendar_date"])["quantity_sold"]
                .sum().unstack(0).reindex(idx, fill_value=0.0)
                .fillna(0.0).astype(float))
    cats = (fast[["product_id", "forecast_category"]].drop_duplicates()
                .set_index("product_id")["forecast_category"])
    return wide, cats.reindex(wide.columns), idx


def shares_at(mat, cats, origin):
    """Trailing-window demand share per SKU within its category, using
    only rows strictly before `origin`.

    Equal split when a category sold nothing in the window - the same
    fallback step4b uses, so the arm scored here is the arm that ships.
    """
    lo = max(origin - SHARE_WINDOW, 0)
    units = mat.iloc[lo:origin].sum()
    out = pd.Series(0.0, index=mat.columns, dtype=float)
    for cat, members in cats.groupby(cats):
        cols = members.index
        tot = float(units[cols].sum())
        out[cols] = (units[cols] / tot) if tot > 0 else (1.0 / len(cols))
    return out


def main():
    con = sqlite3.connect(DB_PATH)
    mat, cats, idx = load_fast_sku_matrix(con)
    con.close()

    cat_series = pd.read_csv(SERIES_FAST, index_col=0, parse_dates=True)
    assert cat_series.index.equals(idx), \
        "category series and SKU matrix are on different calendars"
    # Every Fast SKU must sit in a category the series actually carries,
    # or its top-down forecast would silently be zero.
    missing = sorted(set(cats.dropna()) - set(cat_series.columns))
    assert not missing, f"Fast SKUs in categories absent from the series: {missing}"

    folds = make_folds(len(idx), HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    print(f"Fast SKUs {mat.shape[1]} | days {len(idx)} | folds {len(folds)} "
          f"| horizon {HORIZON} | share window {SHARE_WINDOW}d")
    print(f"origins: {[f.origin for f in folds]}\n")

    rows = []
    for f in folds:
        actual = mat.iloc[f.test_start:f.test_end].sum()          # per SKU, 30d
        w = shares_at(mat, cats, f.origin)

        cat_pred = {}
        for name, fp in TOPDOWN_METHODS.items():
            cat_pred[name] = {
                c: float(np.sum(fp(cat_series[c].to_numpy(float)[:f.origin], HORIZON)))
                for c in cat_series.columns}

        for pid in mat.columns:
            train = mat[pid].to_numpy(float)[:f.origin]
            # MASE's denominator must be on the SAME unit as the error it
            # scales. The target here is a 30-day total, so the naive
            # benchmark is the month-over-month change in 30-day blocks -
            # exactly what evaluate.py:211 does. Scaling a 30-day error by
            # the mean DAILY difference instead inflates MASE by roughly
            # the horizon and makes it incomparable to every other MASE in
            # this project.
            blocks = aggregate_blocks(train, HORIZON)
            denom = naive_scale(blocks) if blocks.size > 1 else np.nan
            base = dict(fold=f.fold_index, origin=f.origin,
                        origin_date=idx[f.origin].date().isoformat(),
                        sku=int(pid), category=cats[pid],
                        actual_30d=float(actual[pid]), naive_denom=denom)

            for name, fp in PER_SKU_METHODS.items():
                yhat = float(np.sum(fp(train, HORIZON)))
                rows.append(dict(base, arm="per-SKU", method=name, yhat=yhat))

            for name in TOPDOWN_METHODS:
                yhat = cat_pred[name][cats[pid]] * float(w[pid])
                rows.append(dict(base, arm="top-down", method=name, yhat=yhat))

    res = pd.DataFrame(rows)
    res["abs_err"] = (res["yhat"] - res["actual_30d"]).abs()
    res["sq_err"] = (res["yhat"] - res["actual_30d"]) ** 2

    # Identical-folds gate. Every method must have been scored on exactly
    # the same (sku, origin) pairs or the ranking is meaningless.
    layouts = res.groupby("method").apply(
        lambda d: tuple(sorted(zip(d["sku"], d["origin"]))), include_groups=False)
    assert layouts.nunique() == 1, "methods were NOT scored on identical folds"
    print(f"[PASS] all {res['method'].nunique()} methods scored on identical "
          f"{len(layouts.iloc[0]):,} (sku, origin) pairs\n")

    # MASE is averaged per row, skipping rows whose denominator is 0 (a
    # SKU flat across its whole training slice cannot be scaled).
    res["mase"] = res["abs_err"] / res["naive_denom"].replace(0, np.nan)

    g = res.groupby(["arm", "method"])
    summ = pd.DataFrame({
        "MAE": g["abs_err"].mean(),
        "RMSE": g["sq_err"].mean() ** 0.5,
        "MASE": g["mase"].mean(),
        # Pooled, not per-series: sum of errors over sum of actuals. The
        # per-series mean would be dominated by the smallest SKUs.
        "WMAPE_pct": 100 * g["abs_err"].sum() / g["actual_30d"].sum(),
        "bias_pct": 100 * (g["yhat"].sum() - g["actual_30d"].sum()) / g["actual_30d"].sum(),
        "n_zero_yhat": g["yhat"].apply(lambda s: int((s <= 1e-9).sum())),
    }).sort_values("WMAPE_pct")

    print("=== SKU-level scoring, Fast SKUs, identical folds ===")
    print(summ.round(2).to_string())

    best_ps = summ.xs("per-SKU").iloc[0]
    best_td = summ.xs("top-down").iloc[0]
    delta = 100 * (best_td["WMAPE_pct"] - best_ps["WMAPE_pct"]) / best_ps["WMAPE_pct"]
    print(f"\nbest per-SKU  {summ.xs('per-SKU').index[0]}: "
          f"WMAPE {best_ps['WMAPE_pct']:.1f}%")
    print(f"best top-down {summ.xs('top-down').index[0]}: "
          f"WMAPE {best_td['WMAPE_pct']:.1f}%")
    print(f"top-down is {abs(delta):.1f}% "
          f"{'WORSE' if delta > 0 else 'BETTER'} at SKU level")

    res.to_csv(OUT_ROWS, index=False, lineterminator="\n")
    summ.round(4).to_csv(OUT_SUMMARY, lineterminator="\n")
    print(f"\nWrote {OUT_ROWS} and {OUT_SUMMARY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
