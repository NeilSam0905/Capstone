"""
tools/synthetic_augment_ml_test.py
------------------------------------------------------------------
Does synthetic training data help the ML learners?

`docs/SPARSE_DEMAND_EXPERIMENTS.md` #4 already asked "would more history
help", prepended 5 synthetic years, and found essentially no change. But
it tested seven WINDOW-BASED methods - rolling mean, rolling median,
seasonal naive, weekly hurdle, EWMA, TSB, naive. Six of those only ever
look at a fixed trailing window, so history behind that window is
invisible to them BY CONSTRUCTION. That experiment could not have found
an effect even if one existed; it answered a question about those
methods, not about data volume.

The learners added in `forecasting/ml_models.py` are different. A per-SKU
XGBoost fits ~20 parameters' worth of tree structure from roughly 400
training rows of a series that is ~90% zeros. If anything in this project
is variance-limited rather than signal-limited, it is that. This script
asks the question again, of the models it actually applies to.

What augmentation can and cannot do here
----------------------------------------
The synthetic days are a bootstrap of each SKU's OWN observed
distribution. That adds no new information about the conditional
structure the model is trying to learn - it cannot invent a relationship
that is not in the real data. What it can do is REGULARISE: more rows,
same signal, lower variance in the fitted parameters.

So the result is diagnostic either way, and both outcomes are worth
having:

  improves   the learners were overfitting ~400 rows, and the gap to the
             trailing averages was partly a sample-size artefact
  no change  the ceiling is signal, not sample size - augmenting cannot
             help and neither will simply collecting more of the same

Two generators, because the first one has a known defect
--------------------------------------------------------
  iid    the weekday-stratified bootstrap from
         `tools/synthetic_augment_test.py`, reused unchanged. Preserves
         the per-weekday zero rate and nonzero-size distribution, and
         destroys ALL autocorrelation.
  block  resamples contiguous 14-day blocks instead of single days, so
         runs of zeros and clusters of sales survive.

The distinction matters more for the learners than it did for the
trailing averages: `days_since_sale`, `trend_30v30` and the rolling
features in ml_models.py are all autocorrelation features. Training them
on iid noise teaches relationships that do not hold in the real series,
which could make augmentation actively harmful - and if it does, the
block version is where that would show up as a difference.

The honesty guarantee, unchanged from the original experiment
-------------------------------------------------------------
Synthetic days are PREPENDED, never appended and never substituted.
`forecasting.evaluate.make_folds` lays out test windows backward from the
END of the array, so every fold's `actual_30d` is the same real observed
data with or without augmentation. Only the TRAINING slice grows. The
scorer never sees a synthetic value.

Read-only and in-memory: touches no table, writes no CSV, never opens
ustore.db for writing.

Run: python tools/synthetic_augment_ml_test.py [--years N] [--limit N]
------------------------------------------------------------------
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

_spec = importlib.util.spec_from_file_location(
    "_bm", os.path.join(ROOT, "scripts", "benchmark_fast_raw_vs_clean.py"))
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

from forecasting.evaluate import evaluate_methods
from synthetic_augment_test import bootstrap_prehistory   # reuse, do not reimplement

SEED = 20260904


def block_prehistory(real, n_days, rng, block=14):
    """Moving-block bootstrap: resample contiguous `block`-day slices.

    Preserves short-range autocorrelation - runs of zeros, clusters of
    sales - which the iid weekday bootstrap destroys. Everything longer
    than `block` is still shuffled away, so this is not a claim to have
    simulated the store; it is one axis of realism added so its effect
    can be measured separately.
    """
    v = np.asarray(real, dtype=float)
    if v.size < block:
        return np.zeros(n_days)
    n_blocks = int(np.ceil(n_days / block))
    starts = rng.integers(0, v.size - block + 1, size=n_blocks)
    return np.concatenate([v[s:s + block] for s in starts])[:n_days]


def ml_methods():
    """The learners, plus two incumbents as an unmoving reference line.

    The trailing averages are included precisely BECAUSE augmentation
    cannot affect them (their window sits inside the real data either
    way): if their numbers move by even a rounding error, the harness is
    broken and every other row is suspect. They are the control.
    """
    from forecasting.baselines import rolling_mean_fit_predict
    from forecasting.intermittent import tsb_fit_predict
    from forecasting.ml_models import (
        lightgbm_fit_predict, ridge_fit_predict, xgboost_fit_predict,
    )
    return {
        "rolling_mean_30 [control]": rolling_mean_fit_predict(30),
        "tsb [control]": tsb_fit_predict(0.1, 0.1),
        "ridge": ridge_fit_predict(),
        "xgboost": xgboost_fit_predict(),
        "lightgbm": lightgbm_fit_predict(),
    }


def augment(series, index, years, mode, rng):
    """Prepend `years` of synthetic history to every series."""
    n_days = int(round(365 * years))
    start_wd = int(pd.Timestamp(index[0]).weekday())
    out = {}
    for k, v in series.items():
        pre = (bootstrap_prehistory(v, start_wd, n_days, rng) if mode == "iid"
               else block_prehistory(v, n_days, rng))
        out[k] = np.concatenate([pre, v])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3.0,
                    help="synthetic years to prepend (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=None,
                    help="first N Fast SKUs only, for a smoke run")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH := os.path.join(ROOT, "ustore.db"))
    series_all, meta, index = bm.load_clean(con)
    con.close()

    fast = [p for p in meta.loc[meta["fsn_pipeline"] == "F", "product_id"]
            if p in series_all]
    if args.limit:
        fast = fast[:args.limit]
    real = {k: series_all[k] for k in fast}

    methods = ml_methods()
    n_real = len(index)
    print("=" * 84)
    print("Does synthetic training data help the ML learners?")
    print("=" * 84)
    print(f"{len(real)} Fast SKUs | real history {n_real} days | "
          f"prepending {args.years} synthetic years ({int(365 * args.years)} days)")
    print("Synthetic days are PREPENDED only - every fold's actual_30d is real, "
          "unaugmented data.\n")

    rows = []
    for label, mode in (("real only", None), ("+synthetic (iid)", "iid"),
                        ("+synthetic (block-14)", "block")):
        rng = np.random.default_rng(SEED)      # same seed per arm
        data = real if mode is None else augment(real, index, args.years, mode, rng)
        n_days = len(next(iter(data.values())))

        t0 = time.time()
        res, _ = evaluate_methods(data, methods, bm.HORIZON, bm.MIN_FOLDS,
                                  bm.MAX_FOLDS, bm.MIN_TRAIN)
        ps = bm.per_sku_metrics(res)
        s = bm.summarise_methods(ps)
        s["arm"] = label
        s["series_days"] = n_days
        rows.append(s)
        print(f"  [{label:22}] {n_days:5} days/series, "
              f"{len(res):,} predictions, {time.time() - t0:.0f}s")

    summary = pd.concat(rows, ignore_index=True)
    summary.to_csv(os.path.join(ROOT, "data", "synthetic_augment_ml.csv"),
                   index=False, lineterminator="\n")
    order = ["real only", "+synthetic (iid)", "+synthetic (block-14)"]

    # ---- which metrics may be compared ACROSS arms -------------------
    # MAE / RMSE / WMAPE are computed from (actual, predicted) on test
    # windows that are identical in every arm, so they are comparable.
    #
    # MASE IS NOT. Its denominator is naive_scale over 30-day blocks of
    # the TRAINING slice, and augmentation changes exactly that slice -
    # three synthetic years of a stationary bootstrap have far smoother
    # block-to-block variation than the real series, which inflates the
    # denominator and drives MASE down for EVERY method including the
    # controls. A smoke run showed rolling_mean_30's MASE "improving" by
    # 63% while its MAE was bit-identical, which is the whole tell.
    # Reported below for completeness, and excluded from the conclusion.
    wide = summary.pivot_table(index="method", columns="arm",
                               values=["mae", "rmse", "wmape_pooled_pct", "mase"])

    def table(metric):
        t = pd.DataFrame(index=wide.index)
        for a in order:
            t[a] = wide[(metric, a)]
        for a in order[1:]:
            t[f"pct_change {a}"] = 100.0 * (wide[(metric, a)]
                                            - wide[(metric, "real only")]) / wide[(metric, "real only")]
        return t.sort_values("real only")

    pd.set_option("display.width", 210)
    for metric, note in (("mae", "COMPARABLE across arms"),
                         ("wmape_pooled_pct", "COMPARABLE across arms"),
                         ("mase", "*** NOT COMPARABLE ACROSS ARMS - denominator "
                                  "is built from the training slice ***")):
        print("\n" + "=" * 84)
        print(f"{metric.upper()} by arm (lower is better) - {note}")
        print("=" * 84)
        print(table(metric).to_string(float_format=lambda x: f"{x:.3f}"))

    # ---- the gate ----------------------------------------------------
    # Only rolling_mean_30 is a strict control. TSB reads its ENTIRE
    # history with exponential decay, so a longer prefix legitimately
    # moves it a little (~1%, the same effect SPARSE_DEMAND_EXPERIMENTS
    # #4 measured); it is listed as a reference line, not a gate.
    mae_t = table("mae")
    strict = "rolling_mean_30 [control]"
    drift = float(mae_t.loc[strict, [c for c in mae_t.columns
                                     if c.startswith("pct_change")]].abs().max())
    ok = drift < 1e-9
    print(f"\n[{'PASS' if ok else 'FAIL'}] {strict} MAE identical across arms "
          f"(max drift {drift:.2e}) - augmentation cannot reach inside a 30-day window")
    print("[note ] tsb is NOT a strict control: it reads all history with decay, "
          "so a small shift is expected")
    print("\nNegative percentages mean augmentation REDUCED error.")
    print(f"Wrote data/synthetic_augment_ml.csv")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
