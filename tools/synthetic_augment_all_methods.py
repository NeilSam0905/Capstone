"""
tools/synthetic_augment_all_methods.py
------------------------------------------------------------------
Extends tools/synthetic_augment_test.py's "does N more years of bootstrapped
synthetic pre-history help" question to the 5 methods that script left out:
croston, sba, ets, rolling_q75_30, logistic_hurdle. Same honest design (see
that file's docstring and docs/SPARSE_DEMAND_EXPERIMENTS.md section 4):
synthetic days are PREPENDED, never appended/substituted, so every fold's
actual_30d target is exactly the real observed data either way - only the
training slice grows. Read-only against ustore.db; writes nothing back.

Adds MASE alongside MAE/RMSE/MAPE (the original tool only had the latter
three) since MASE is this project's primary ranking metric.

MASE denominator note - this matters, read it before trusting the numbers
--------------------------------------------------------------------------
MASE's denominator is supposed to measure "how hard is this SKU's real
demand to predict", using only information available before the forecast.
If it were computed from the AUGMENTED training slice (real +
bootstrapped synthetic days), synthetic block-to-block noise inflates that
denominator - which shrinks MASE with NO actual forecast improvement. This
was caught empirically: ets and rolling_q75_30 only ever look at a fixed
trailing window, so their predictions (and MAE) are IDENTICAL with or
without synthetic data - yet a first pass of this script that scaled by
the augmented training's naive_scale showed MASE dropping ~3x for both.
That is proof of a measurement artifact, not a real result.

The fix: MASE for BOTH the real and +synthetic columns is always scaled by
the SAME real-only denominator (from the real-history run). Only the
numerator (MAE, from whichever run's predictions) differs between columns.

logistic_hurdle is the one to watch: it's the project's only per-SKU FITTED
statistical method (L-BFGS-B logistic regression), and
docs/SPARSE_DEMAND_EXPERIMENTS.md section 2 already diagnosed its underperformance
as "too little signal ... for a 10-parameter model to learn from" on thin
per-SKU history - exactly the condition more history could address, unlike
the window-based methods that ignore it by construction.

Run: python tools/synthetic_augment_all_methods.py [--years N]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import (
    ets_fit_predict, rolling_quantile_fit_predict,
)
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.hurdle import logistic_hurdle_fit_predict
from forecasting.intermittent import croston_fit_predict, sba_fit_predict
from forecasting.metrics import mae, mape, rmse
from tools.synthetic_augment_test import (
    DB_PATH, HORIZON, MAX_FOLDS, MIN_FOLDS, MIN_TRAIN, SEED,
    bootstrap_prehistory,
)

ALPHA = 0.1   # same smoothing constant model_benchmark.py uses


def score(series_by_key, model_fn, method_name):
    rows = []
    for key, values in series_by_key.items():
        folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        if not folds:
            continue
        ev = walk_forward_evaluate(key, values, model_fn, method_name, folds=folds)
        rows.extend(ev.rows)
    return pd.DataFrame(rows)


def per_sku_frame(df):
    """mae/rmse/mape/denom per sku. `denom` is this run's own naive_scale -
    correct to use as-is for a REAL-only run; for an augmented run, the
    caller must override it with the matching real-only run's denom (see
    summarise_pair) rather than use the column returned here."""
    rows = []
    for sku, g in df.groupby("sku"):
        m = mape(g["actual_30d"], g["pred_30d"])
        rows.append({
            "sku": sku,
            "mae": mae(g["actual_30d"], g["pred_30d"]),
            "rmse": rmse(g["actual_30d"], g["pred_30d"]),
            "denom": np.nanmean(g["naive_scale"].to_numpy(dtype=float)),
            "mape": m.value,
        })
    return pd.DataFrame(rows).set_index("sku")


def summarise_pair(df_real, df_aug):
    """real vs +synthetic summary, both MASE columns scaled by the SAME
    real-only denominator - see the module docstring's MASE note."""
    r = per_sku_frame(df_real)
    a = per_sku_frame(df_aug)

    valid = r["denom"].notna() & (r["denom"] > 0)
    r_mase = (r["mae"] / r["denom"])[valid]
    a_mase = (a["mae"] / r["denom"])[valid & a["mae"].notna()]

    return dict(
        mae_real=r["mae"].mean(), mae_aug=a["mae"].mean(),
        rmse_real=r["rmse"].mean(), rmse_aug=a["rmse"].mean(),
        mase_real=r_mase.mean() if len(r_mase) else float("nan"),
        mase_aug=a_mase.mean() if len(a_mase) else float("nan"),
        mape_real=r["mape"].mean(skipna=True), mape_aug=a["mape"].mean(skipna=True),
    )


def main():
    import sqlite3
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

    augmented_series = {}
    for pid, real in real_series.items():
        pre = bootstrap_prehistory(real, start_weekday, syn_days, rng)
        augmented_series[pid] = np.concatenate([pre, real])

    methods = {
        "croston": croston_fit_predict(ALPHA),
        "sba": sba_fit_predict(ALPHA),
        "ets": ets_fit_predict(7, optimise=False),   # quick mode - see model_benchmark.py --quick
        "rolling_q75_30": rolling_quantile_fit_predict(30, 0.75),
        "logistic_hurdle": logistic_hurdle_fit_predict(),
    }

    print(f"Real span: {full_index[0].date()} .. {full_index[-1].date()} ({len(full_index)} days), "
          f"{len(real_series)} moving SKUs")
    print(f"Synthetic pre-history added: {syn_days} days (~{args.years} years), "
          f"weekday-stratified bootstrap of each series's own real distribution, seed={SEED}")
    print("ets runs with optimise=False (fixed smoothing constants) for speed, matching "
          "model_benchmark.py --quick - not the optimised numbers in the committed benchmark.\n")

    header = (f"{'method':18s} {'MAE(real)':>10s} {'MAE(+syn)':>10s} {'MASE(real)':>11s} "
             f"{'MASE(+syn)':>10s} {'RMSE(real)':>11s} {'RMSE(+syn)':>10s} "
             f"{'MAPE(real)':>11s} {'MAPE(+syn)':>10s}")
    print(header)
    for name, fn in methods.items():
        s = summarise_pair(score(real_series, fn, name), score(augmented_series, fn, name))
        print(f"{name:18s} {s['mae_real']:10.3f} {s['mae_aug']:10.3f} {s['mase_real']:11.3f} "
              f"{s['mase_aug']:10.3f} {s['rmse_real']:11.3f} {s['rmse_aug']:10.3f} "
              f"{s['mape_real']:10.1f}% {s['mape_aug']:9.1f}%")


if __name__ == "__main__":
    main()
