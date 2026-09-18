"""
scripts/benchmark_category_level.py
------------------------------------------------------------------
Steps 4 and 9 of the category refactor: score every candidate model on
the CATEGORY-level series, on the same walk-forward harness the SKU-level
benchmark uses, and answer two questions in one run.

  Step 4  Does aggregating to category actually beat forecasting per SKU?
  Step 9  Once aggregated, which model class wins - and does ARIMA or
          Prophet become viable where they were not per-SKU?

Comparability is the whole point
--------------------------------
Horizon 30, 3-12 folds, min_train 60 - identical to
`model_benchmark.py` and `benchmark_fast_raw_vs_clean.py`. The fold
layout is computed once per series and handed to every method, so no
method can be advantaged by a different split. Nothing here re-implements
scoring; `forecasting/evaluate.py` does it, which is what makes these
numbers directly comparable to the 37-method SKU table in
docs/FAST_MOVING_BENCHMARK.md.

Why ARIMA appears here and not in the SKU benchmark
---------------------------------------------------
Section 2.1.4 notes ARIMA needs a near-continuous series and 50-100 clean
observations. A per-SKU series that is ~90% zeros cannot supply that, so
ARIMA was never scored. A category series is dense - it is the one model
class that aggregation newly makes viable, which is exactly why it is
worth testing now.

The SKU-level comparison is POOLED, not per-series
--------------------------------------------------
Category MAE cannot be compared to SKU MAE: a category sells far more
units, so its absolute error is larger by construction and would look
worse while being better. WMAPE (sum|error| / sum actual) divides that
out and is the metric the two levels can be compared on. MAE and RMSE are
still reported per level, just never across levels.

Run (from the repo root, after step1b_categorize_products.py):
    python scripts/benchmark_category_level.py [--no-ml] [--no-prophet]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
import time
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.baselines import (
    ets_fit_predict, ewma_fit_predict, naive_fit_predict,
    rolling_mean_fit_predict, rolling_median_fit_predict,
    rolling_quantile_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.evaluate import evaluate_methods
from forecasting.hurdle import weekly_hurdle_fit_predict
from forecasting.intermittent import croston_fit_predict, sba_fit_predict, tsb_fit_predict

SERIES_CSV = "data/category_daily_series.csv"
OUT_SUMMARY = "data/category_benchmark_summary.csv"
OUT_FOLDS = "data/category_benchmark_folds.csv"
OUT_COMPARE = "data/category_vs_sku_level.csv"

HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
ALPHA, BETA = 0.1, 0.1


def arima_fit_predict(order=(1, 1, 1), name=None):
    """ARIMA(p,d,q) on the daily series, summed over the horizon.

    Wrapped in the same fit_predict contract as everything else. A model
    that fails to converge falls back to the trailing mean rather than
    dropping the fold - every method must cover every fold or the
    identical-folds gate fails.
    """
    def _f(train, horizon):
        from statsmodels.tsa.arima.model import ARIMA
        v = np.asarray(train, dtype=float).ravel()
        if v.size < 60 or v.sum() <= 0:
            w = v[-30:] if v.size >= 30 else v
            return np.maximum(np.full(horizon, w.mean() if w.size else 0.0), 0.0)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = ARIMA(v, order=order).fit()
                pred = np.asarray(fit.forecast(steps=horizon), dtype=float)
        except Exception:
            w = v[-30:]
            pred = np.full(horizon, w.mean())
        return np.maximum(pred, 0.0)

    _f.__name__ = name or f"arima{order}"
    return _f


def sarima_fit_predict(order=(1, 1, 1), seasonal_order=(1, 0, 1, 7), name="sarima_weekly"):
    """SARIMA with a weekly seasonal term - the store's trading rhythm."""
    def _f(train, horizon):
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        v = np.asarray(train, dtype=float).ravel()
        if v.size < 90 or v.sum() <= 0:
            w = v[-30:] if v.size >= 30 else v
            return np.maximum(np.full(horizon, w.mean() if w.size else 0.0), 0.0)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = SARIMAX(v, order=order, seasonal_order=seasonal_order,
                              enforce_stationarity=False,
                              enforce_invertibility=False).fit(disp=False)
                pred = np.asarray(fit.forecast(steps=horizon), dtype=float)
        except Exception:
            w = v[-30:]
            pred = np.full(horizon, w.mean())
        return np.maximum(pred, 0.0)

    _f.__name__ = name
    return _f


def build_methods(with_ml=True, with_stats=True):
    m = {
        "naive": naive_fit_predict(),
        "seasonal_naive_7": seasonal_naive_fit_predict(7),
        "rolling_mean_14": rolling_mean_fit_predict(14),
        "rolling_mean_30": rolling_mean_fit_predict(30),
        "rolling_mean_60": rolling_mean_fit_predict(60),
        "RM3_3month_90d": rolling_mean_fit_predict(90),
        "RM6_6month_180d": rolling_mean_fit_predict(180),
        "rolling_median_30": rolling_median_fit_predict(30),
        "rolling_q75_30": rolling_quantile_fit_predict(30, 0.75),
        "ewma_a0.1": ewma_fit_predict(0.1),
        "ewma_a0.3": ewma_fit_predict(0.3),
        "croston": croston_fit_predict(ALPHA),
        "sba": sba_fit_predict(ALPHA),
        "tsb": tsb_fit_predict(ALPHA, BETA),
        "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
        "ets": ets_fit_predict(7, optimise=True),
    }
    missing = {}
    if with_stats:
        try:
            import statsmodels  # noqa: F401
            m["arima_111"] = arima_fit_predict((1, 1, 1), "arima_111")
            m["arima_212"] = arima_fit_predict((2, 1, 2), "arima_212")
            m["sarima_weekly"] = sarima_fit_predict()
        except ImportError as exc:
            missing["arima/sarima"] = str(exc)
    if with_ml:
        from forecasting.ml_models_fastmoving import available_ml_methods
        ml, miss = available_ml_methods()
        m.update(ml)
        missing.update(miss)
    return m, missing


def summarise(results, label):
    """Per-series metrics then aggregated. WMAPE pooled is the number the
    SKU and category levels may be compared on; MAE/RMSE are level-local."""
    per = []
    for (method, sku), g in results.groupby(["method", "sku"], sort=False):
        a = g["actual_30d"].to_numpy(float)
        p = g["pred_30d"].to_numpy(float)
        e = np.abs(a - p)
        d = a != 0
        per.append({
            "method": method, "series": sku,
            "mae": e.mean(), "rmse": float(np.sqrt((e ** 2).mean())),
            "abs_err_sum": e.sum(), "total_actual": a.sum(),
            "mape": float(np.mean(e[d] / a[d]) * 100) if d.any() else np.nan,
            "n_zero_targets": int((~d).sum()), "n_folds": len(g),
        })
    ps = pd.DataFrame(per)
    out = (ps.groupby("method")
             .agg(mae=("mae", "mean"), rmse=("rmse", "mean"),
                  mape_pct=("mape", "mean"),
                  n_series=("series", "nunique"), n_folds=("n_folds", "sum"),
                  zero_targets=("n_zero_targets", "sum"),
                  abs_err_sum=("abs_err_sum", "sum"),
                  total_actual=("total_actual", "sum"))
             .reset_index())
    out["wmape_pooled_pct"] = 100 * out["abs_err_sum"] / out["total_actual"]
    out.insert(0, "level", label)
    return out.sort_values("wmape_pooled_pct").reset_index(drop=True), ps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ml", action="store_true")
    ap.add_argument("--no-prophet", action="store_true")
    ap.add_argument("--no-stats", action="store_true")
    args = ap.parse_args()

    series_df = pd.read_csv(SERIES_CSV, index_col=0, parse_dates=True)
    series = {c: series_df[c].to_numpy(float) for c in series_df.columns}
    idx = series_df.index

    methods, missing = build_methods(not args.no_ml, not args.no_stats)
    if not args.no_prophet:
        try:
            from forecasting.prophet_model import load_calendar, prophet_fit_predict
            con = sqlite3.connect(os.path.join(ROOT, "ustore.db"))
            cal = load_calendar(con, idx)
            con.close()
            methods["prophet_plain"] = prophet_fit_predict(idx, None, "prophet_plain")
            methods["prophet_cal"] = prophet_fit_predict(idx, cal, "prophet_cal")
        except ImportError as exc:
            missing["prophet"] = str(exc)

    print("=" * 92)
    print("CATEGORY-LEVEL BENCHMARK")
    print("=" * 92)
    print(f"{len(series)} categories x {len(idx)} days "
          f"({idx[0].date()} .. {idx[-1].date()})")
    print(f"Horizon {HORIZON} | folds {MIN_FOLDS}-{MAX_FOLDS} | min_train {MIN_TRAIN} "
          f"(identical to the SKU benchmark)")
    print(f"Methods ({len(methods)}): {', '.join(methods)}")
    if missing:
        print(f"Unavailable: {list(missing)}")

    t0 = time.time()
    res, insufficient = evaluate_methods(series, methods, HORIZON, MIN_FOLDS,
                                         MAX_FOLDS, MIN_TRAIN)
    print(f"\nScored {len(res):,} predictions in {time.time() - t0:.0f}s")
    if insufficient:
        print(f"Insufficient history: {list(insufficient)}")

    layouts = res.groupby(["sku", "method"])["origin"].apply(lambda s: tuple(sorted(s)))
    assert (layouts.groupby("sku").nunique() == 1).all(), \
        "methods were NOT scored on identical folds"
    print("[PASS] all methods scored on identical folds")

    res.to_csv(OUT_FOLDS, index=False, lineterminator="\n")
    summary, per_series = summarise(res, "category")
    summary.to_csv(OUT_SUMMARY, index=False, lineterminator="\n")

    cols = ["method", "mae", "rmse", "mape_pct", "wmape_pooled_pct", "zero_targets"]
    print("\n" + "=" * 92)
    print("CATEGORY LEVEL - ordered by pooled WMAPE (the level-comparable metric)")
    print("=" * 92)
    print(summary[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # ---- Step 4: does category beat SKU? -----------------------------
    sku_path = "data/fastmoving_benchmark_results.csv"
    if os.path.exists(sku_path):
        r = pd.read_csv(sku_path)
        r = r[(r["stage"] == "CLEAN") & (r["in_fast_set"])]
        e = (r["actual_30d"] - r["pred_30d"]).abs()
        sku = (r.assign(e=e).groupby("method")
                 .apply(lambda g: pd.Series({
                     "wmape_pooled_pct": 100 * g["e"].sum() / g["actual_30d"].sum(),
                     "zero_targets": int((g["actual_30d"] == 0).sum()),
                     "n_folds": len(g)}), include_groups=False)
                 .reset_index())
        cmp = (summary[["method", "wmape_pooled_pct", "zero_targets"]]
               .merge(sku, on="method", suffixes=("_category", "_sku"), how="inner"))
        cmp["wmape_change_pct"] = (100 * (cmp["wmape_pooled_pct_category"]
                                          - cmp["wmape_pooled_pct_sku"])
                                   / cmp["wmape_pooled_pct_sku"])
        cmp = cmp.sort_values("wmape_pooled_pct_category").reset_index(drop=True)
        cmp.to_csv(OUT_COMPARE, index=False, lineterminator="\n")
        print("\n" + "=" * 92)
        print("STEP 4 - CATEGORY vs SKU level, same methods, same harness")
        print("(pooled WMAPE only; MAE is not comparable across levels)")
        print("=" * 92)
        print(cmp[["method", "wmape_pooled_pct_sku", "wmape_pooled_pct_category",
                   "wmape_change_pct", "zero_targets_sku", "zero_targets_category"]]
              .to_string(index=False, float_format=lambda x: f"{x:.1f}"))
        print(f"\nWrote {OUT_COMPARE}")

    print(f"Wrote {OUT_SUMMARY} and {OUT_FOLDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
