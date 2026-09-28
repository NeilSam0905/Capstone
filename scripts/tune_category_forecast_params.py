"""
scripts/tune_category_forecast_params.py
------------------------------------------------------------------
Parameter tuning for the category-level forecast: does adjusting a model's
own settings (not swapping the model) close any of the gap to the shipped
6-month average?

Tunes two model families, on the SAME category series and SAME walk-forward
harness scripts/step4c_category_forecast.py uses (forecasting/evaluate.py,
30-day horizon, up to 12 folds, min_train 60, MASE denominator = in-sample
naive error on 30-calendar-day blocks):

  - TSB (forecasting/intermittent.py) - alpha x beta grid. TSB is the
    intermittent-demand model raised as a candidate for the weaker
    categories (scripts vs 6-month average: docs/FORECASTING_EXPLORATION_
    NOTES.md 2.1's sibling finding). alpha/beta were never tuned before -
    every prior TSB result in this project used the textbook default
    (0.1, 0.1).
  - Prophet (forecasting/prophet_model.py) - changepoint_prior_scale
    (trend flexibility) x yearly_seasonality on/off. Category-level
    Prophet has consistently lost to the 6-month average on this data
    (docs/FORECASTING_EXPLORATION_NOTES.md 2.1); this checks whether that
    was a settings problem rather than a data-volume one.

Also reports the shipped 6-month average and its own 3/12-month siblings
as the bar to beat, all on this identical harness.

Run:  python scripts/tune_category_forecast_params.py
Takes ~2-3 minutes (Prophet fits dominate).
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.baselines import rolling_mean_fit_predict
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.intermittent import tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60


def load_category_series(con):
    """Identical construction to step4c_category_forecast.py: zero-filled
    daily series per forecast_category, trimmed to the last date ANY
    category sold (excludes the 23-day zero-padded tail)."""
    fact = pd.read_sql_query("""
        SELECT d.calendar_date, f.quantity_sold, p.forecast_category
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'
    """, con, parse_dates=["calendar_date"])
    fact["forecast_category"] = fact["forecast_category"].fillna("Uncategorised")
    idx = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    wide = (fact.groupby(["forecast_category", "calendar_date"])["quantity_sold"]
                .sum().unstack(0).reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    wide = wide.loc[:, wide.sum() > 0]
    total = wide.sum(axis=1)
    return wide.loc[:total[total > 0].index.max()]


def score(series, model, model_name):
    """category -> (mase, beats_naive_last30) on the shared harness."""
    naive = rolling_mean_fit_predict(30)
    out = {}
    for cat in series.columns:
        v = series[cat].to_numpy(float)
        folds = make_folds(v.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        if not folds:
            continue
        ev_m = walk_forward_evaluate(cat, v, model, model_name, folds=folds)
        ev_n = walk_forward_evaluate(cat, v, naive, "naive_last30", folds=folds)
        actual = np.array([r["actual_30d"] for r in ev_m.rows])
        pred = np.array([r["pred_30d"] for r in ev_m.rows])
        npred = np.array([r["pred_30d"] for r in ev_n.rows])
        scales = np.array([r["naive_scale"] for r in ev_m.rows])
        mae = np.mean(np.abs(actual - pred))
        n_mae = np.mean(np.abs(actual - npred))
        good = np.isfinite(scales) & (scales > 0)
        mase = mae / np.mean(scales[good]) if good.any() else np.nan
        out[cat] = (mase, mae < n_mae)
    return out


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    series = load_category_series(con)
    con.close()
    print("Series: %d categories x %d days (%s .. %s)\n"
          % (series.shape[1], len(series), series.index[0].date(), series.index[-1].date()))

    results = {}

    # ---- baselines: the shipped model and its untuned siblings ----------
    for label, window in [("3-month avg", 90), ("6-month avg (SHIPPED)", 180),
                          ("12-month avg", 365)]:
        results[label] = score(series, rolling_mean_fit_predict(window), label)

    # ---- TSB: alpha x beta grid -----------------------------------------
    for alpha in (0.05, 0.1, 0.2, 0.3):
        for beta in (0.05, 0.1, 0.2, 0.3):
            label = "TSB a=%.2f b=%.2f" % (alpha, beta)
            results[label] = score(series, tsb_fit_predict(alpha, beta), label)
    print("TSB grid scored (%d combinations)" % 16)

    # ---- Prophet: trend flexibility x yearly seasonality -----------------
    try:
        from forecasting.prophet_model import prophet_fit_predict
        idx = series.index
        for cps in (0.01, 0.05, 0.5):          # rigid -> default -> very flexible
            for yearly in (True, False):
                label = "Prophet cps=%.2f yearly=%s" % (cps, yearly)
                model = prophet_fit_predict(idx, calendar=None, name=label,
                                            weekly=True, yearly=yearly,
                                            changepoint_prior_scale=cps)
                results[label] = score(series, model, label)
                print("  scored:", label, flush=True)
    except ImportError:
        print("prophet not installed - skipping the Prophet grid")

    # ---- report -----------------------------------------------------------
    rows = []
    for label, per_cat in results.items():
        if not per_cat:
            continue
        mases = [v[0] for v in per_cat.values()]
        beats = [v[1] for v in per_cat.values()]
        rows.append(dict(method=label, macro_mase=np.mean(mases),
                         below_1=int(sum(m < 1 for m in mases)),
                         beats_naive_last30=int(sum(beats)), n=len(per_cat)))
    res = pd.DataFrame(rows).sort_values("macro_mase")
    pd.set_option("display.width", 200)
    print("\n" + "=" * 78)
    print("PARAMETER TUNING RESULT (lower macro MASE is better)")
    print("=" * 78)
    print(res.to_string(index=False))

    shipped = res.loc[res["method"] == "6-month avg (SHIPPED)", "macro_mase"].iloc[0]
    better = res[res["macro_mase"] < shipped]
    print("\nShipped model (6-month average) macro MASE: %.3f" % shipped)
    if len(better):
        print("Configurations that beat it:")
        print(better.to_string(index=False))
    else:
        print("Nothing in this grid beat the shipped model.")

    out_path = os.path.join(ROOT, "data", "category_forecast_param_tuning.csv")
    res.to_csv(out_path, index=False, lineterminator="\n")
    print("\nWrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
