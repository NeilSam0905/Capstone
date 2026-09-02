"""
tools/synthetic_augment_test.py
------------------------------------------------------------------
Reproduces the "would N more years of history improve forecasts of the
REAL data we already have" experiment in docs/SPARSE_DEMAND_EXPERIMENTS.md.

Touches no table, writes nothing back - read-only against ustore.db. Never
writes anything to ustore.db, Fact_Sales, or any committed CSV: this is a
pure in-memory sandbox.

Design, and why it is honest
-----------------------------
Synthetic days are PREPENDED before each series's real history, never
appended and never substituted for real days. `forecasting.evaluate.
make_folds` lays out test windows by stepping backward from the END of the
array, so every fold's test target (`actual_30d`) is unaffected by how much
extra history sits in front of it - only the TRAINING slice available to
each fold grows. It is not possible for this design to manufacture an
improvement by feeding the scorer fabricated "actuals", because the scorer
never sees the synthetic days at all.

Each SKU's synthetic pre-history is a weekday-stratified bootstrap of that
SAME SKU's own real days: same zero-rate per weekday, same nonzero-size
distribution per weekday, resampled with replacement. That preserves the
real marginal statistics (including the ~81%-zero intermittency) without
literally repeating real sequences. It does NOT reproduce the semester-break
calendar pattern - a real simplification of the simulation, not the model.

Run: python tools/synthetic_augment_test.py [--years N]
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import (
    ewma_fit_predict, naive_fit_predict, rolling_mean_fit_predict,
    rolling_median_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.hurdle import weekly_hurdle_fit_predict
from forecasting.intermittent import tsb_fit_predict
from forecasting.metrics import mae, mape, rmse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ustore.db")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
SEED = 20260902     # fixed - reproducible, not cherry-picked


def bootstrap_prehistory(real: np.ndarray, real_start_weekday: int, n_days: int, rng):
    """Weekday-stratified bootstrap of `real`, `n_days` long, phased so it
    connects seamlessly (weekday-wise) to the day immediately before
    `real`'s first day."""
    wd_of_real = (real_start_weekday + np.arange(real.size)) % 7
    by_wd = {}
    for wd in range(7):
        vals = real[wd_of_real == wd]
        p0 = float(np.mean(vals == 0)) if vals.size else 1.0
        by_wd[wd] = (p0, vals[vals > 0])

    pool_nz = real[real > 0]     # fallback for a weekday this SKU never sold on

    wd_of_syn = (real_start_weekday - n_days + np.arange(n_days)) % 7
    out = np.zeros(n_days)
    for wd in range(7):
        mask = wd_of_syn == wd
        n = int(mask.sum())
        if n == 0:
            continue
        p0, nz = by_wd[wd]
        sells = rng.random(n) >= p0
        vals = np.zeros(n)
        source = nz if nz.size else pool_nz
        if source.size and sells.any():
            vals[sells] = rng.choice(source, size=int(sells.sum()), replace=True)
        out[mask] = vals
    return out


def score(series_by_key, model_fn, method_name):
    rows = []
    for key, values in series_by_key.items():
        folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        if not folds:
            continue
        ev = walk_forward_evaluate(key, values, model_fn, method_name, folds=folds)
        rows.extend(ev.rows)
    return pd.DataFrame(rows)


def summarise_df(df):
    per_mae, per_rmse, per_mape = [], [], []
    n_undef = n_total = 0
    for _, g in df.groupby("sku"):
        per_mae.append(mae(g["actual_30d"], g["pred_30d"]))
        per_rmse.append(rmse(g["actual_30d"], g["pred_30d"]))
        m = mape(g["actual_30d"], g["pred_30d"])
        n_undef += m.n_undefined
        n_total += m.n_total
        if not np.isnan(m.value):
            per_mape.append(m.value)
    return dict(mae=np.mean(per_mae), rmse=np.mean(per_rmse),
               mape=np.mean(per_mape) if per_mape else float("nan"),
               mape_pct_folds=100 * (n_total - n_undef) / n_total if n_total else float("nan"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    args = ap.parse_args()
    syn_days = 365 * args.years
    rng = np.random.default_rng(SEED)

    con = sqlite3.connect(DB_PATH)
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])
    con.close()

    moving = fact.groupby("product_id")["quantity_sold"].sum().pipe(lambda s: s[s > 0]).index
    fact = fact[fact["product_id"].isin(moving)]
    full_index = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    start_weekday = int(full_index[0].dayofweek)

    real_series = {}
    for pid, g in fact.groupby("product_id"):
        real_series[pid] = (g.groupby("calendar_date")["quantity_sold"].sum()
                              .reindex(full_index, fill_value=0.0).astype(float).to_numpy())

    whole_store_real = np.sum(list(real_series.values()), axis=0)

    augmented_series = {}
    for pid, real in real_series.items():
        pre = bootstrap_prehistory(real, start_weekday, syn_days, rng)
        augmented_series[pid] = np.concatenate([pre, real])
    whole_store_aug = np.concatenate(
        [bootstrap_prehistory(whole_store_real, start_weekday, syn_days, rng), whole_store_real])

    methods = {
        "naive": naive_fit_predict(),
        "seasonal_naive": seasonal_naive_fit_predict(7),
        "rolling_mean_30": rolling_mean_fit_predict(30),
        "rolling_median_30": rolling_median_fit_predict(30),
        "ewma_a0.1": ewma_fit_predict(0.1),
        "tsb": tsb_fit_predict(0.1, 0.1),
        "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
    }

    print(f"Real span: {full_index[0].date()} .. {full_index[-1].date()} ({len(full_index)} days)")
    print(f"Synthetic pre-history added: {syn_days} days (~{args.years} years), "
          f"weekday-stratified bootstrap of each series's own real distribution, seed={SEED}\n")

    header = (f"{'method':20s} {'MAE(real)':>10s} {'MAE(+syn)':>10s} {'RMSE(real)':>11s} "
             f"{'RMSE(+syn)':>10s} {'MAPE(real)':>11s} {'MAPE(+syn)':>10s}")
    print(header)
    for name, fn in methods.items():
        r = summarise_df(score(real_series, fn, name))
        a = summarise_df(score(augmented_series, fn, name))
        print(f"{name:20s} {r['mae']:10.3f} {a['mae']:10.3f} {r['rmse']:11.3f} "
              f"{a['rmse']:10.3f} {r['mape']:10.1f}% {a['mape']:9.1f}%")

    print("\n--- whole-store aggregate, single series ---")
    print(header)
    for name, fn in methods.items():
        r = summarise_df(score({"WHOLE_STORE": whole_store_real}, fn, name))
        a = summarise_df(score({"WHOLE_STORE": whole_store_aug}, fn, name))
        print(f"{name:20s} {r['mae']:10.3f} {a['mae']:10.3f} {r['rmse']:11.3f} "
              f"{a['rmse']:10.3f} {r['mape']:10.1f}% {a['mape']:9.1f}%")


if __name__ == "__main__":
    main()
