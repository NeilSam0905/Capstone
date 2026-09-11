"""
tools/calendar_hurdle_test.py
------------------------------------------------------------------
Does giving a model the academic calendar help?

The 2026-09-08 session's diagnostics (density_vs_accuracy.py,
aggregation_density_test.py, sparsity_sensitivity_sim.py) converged on one
conclusion: sparsity is not what defeats these models - a controlled
simulation showed they handle sparse-but-STABLE demand fine at every
density tried, and aggregating real SKUs into denser series didn't move
MASE at all. What's left is that the RATE shifts - semester cycles, exam
weeks, breaks - and nothing in this project's models was ever told what
week of the school year a training or forecast day falls in, even though
Dim_Date already carries that as real, populated columns.

This scores forecasting.hurdle.calendar_logistic_hurdle_fit_predict (adds
is_enrollment_period/is_exam_week/is_event_day/is_sem_break as extra
logistic-regression columns) against the existing logistic_hurdle and
weekly_hurdle_12w, per SKU, on IDENTICAL folds. Real history only - no
synthetic augmentation, so any difference is attributable to the calendar
features alone.

Read-only against ustore.db. Writes one CSV.

Run: python tools/calendar_hurdle_test.py [--limit N]
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import model_benchmark as mb
from forecasting.evaluate import aggregate_blocks, make_folds, walk_forward_evaluate
from forecasting.hurdle import (
    calendar_logistic_hurdle_fit_predict, logistic_hurdle_fit_predict,
    weekly_hurdle_fit_predict,
)
from forecasting.metrics import mape as mape_fn

OUT_CSV = "data/calendar_hurdle_results.csv"

# Both moved to scripts/model_benchmark.py so the per-SKU calendar model
# here and the POOLED one in scripts/model_benchmark_category.py read the
# calendar through one loader - re-spelling it twice is how the two drift.
CALENDAR_COLS = mb.CALENDAR_COLS
load_calendar_features = mb.load_calendar_features


def per_sku_metrics(rows_df):
    out = []
    for sku, g in rows_df.groupby("sku"):
        actual = g["actual_30d"].to_numpy()
        pred = g["pred_30d"].to_numpy()
        mae = float(np.mean(np.abs(actual - pred)))
        denom = float(np.nanmean(g["naive_scale"].to_numpy(dtype=float)))
        m = mape_fn(actual, pred)
        out.append({"sku": sku, "mae": mae, "denom": denom, "mape": m.value})
    return pd.DataFrame(out)


def summarise(rows_df):
    ps = per_sku_metrics(rows_df)
    ok = ps["denom"].notna() & (ps["denom"] > 0)
    mase = ps.loc[ok, "mae"] / ps.loc[ok, "denom"]
    return dict(
        mae=ps["mae"].mean(),
        mase_mean=mase.mean() if len(mase) else np.nan,
        mase_median=mase.median() if len(mase) else np.nan,
        mase_global=(ps.loc[ok, "mae"].sum() / ps.loc[ok, "denom"].sum()
                    if ok.any() else np.nan),
        mape=ps["mape"].mean(skipna=True),
        n_skus=len(ps),
    )


def score(series, methods, folds):
    all_rows = []
    for name, fn in methods.items():
        rows = []
        for sku, values in series.items():
            ev = walk_forward_evaluate(sku, values, fn, name, folds=folds)
            rows.extend(ev.rows)
        df = pd.DataFrame(rows)
        all_rows.append(df)
        s = summarise(df)
        print(f"  {name:28s} MAE={s['mae']:7.2f}  MASE mean={s['mase_mean']:6.2f} "
              f"median={s['mase_median']:5.2f} global={s['mase_global']:5.2f}  "
              f"MAPE={s['mape']:6.1f}%")
    return pd.concat(all_rows, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="benchmark only the first N SKUs (for a smoke run)")
    args = ap.parse_args()

    con = sqlite3.connect(mb.DB_NAME)
    series, names, index = mb.load_daily_series(con, args.limit)
    calendar_features = load_calendar_features(con, index)
    con.close()

    n_flagged = (calendar_features.sum(axis=0)).astype(int)
    print(f"{len(series)} SKUs, {len(index)} days "
          f"({index[0].date()} .. {index[-1].date()})")
    print("Calendar flags (days set): " +
          ", ".join(f"{c}={n}" for c, n in zip(CALENDAR_COLS, n_flagged)))

    folds = make_folds(len(index), horizon=mb.HORIZON, min_folds=mb.MIN_FOLDS,
                       max_folds=mb.MAX_FOLDS, min_train=mb.MIN_TRAIN)
    print(f"{len(folds)} folds, horizon {mb.HORIZON}d\n")

    methods = {
        "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
        "logistic_hurdle": logistic_hurdle_fit_predict(),
        "calendar_logistic_hurdle": calendar_logistic_hurdle_fit_predict(calendar_features),
    }

    print("=" * 96)
    print("CALENDAR-AWARE vs. BASELINE - same SKUs, same folds, real history only")
    print("=" * 96)
    results = score(series, methods, folds)
    results.to_csv(OUT_CSV, index=False, lineterminator="\n")
    print(f"\nWrote {OUT_CSV}")

    # head-to-head: calendar_logistic_hurdle vs plain logistic_hurdle, per SKU
    base = per_sku_metrics(results[results["method"] == "logistic_hurdle"]).set_index("sku")
    cal = per_sku_metrics(results[results["method"] == "calendar_logistic_hurdle"]).set_index("sku")
    common = base.index.intersection(cal.index)
    better = (cal.loc[common, "mae"] < base.loc[common, "mae"]).sum()
    print(f"\nPer-SKU MAE: calendar version beats plain logistic_hurdle on "
          f"{better}/{len(common)} SKUs ({100*better/len(common):.1f}%)")


if __name__ == "__main__":
    main()
