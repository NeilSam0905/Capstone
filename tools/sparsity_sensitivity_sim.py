"""
tools/sparsity_sensitivity_sim.py
------------------------------------------------------------------
Option A: a SIMULATION, not a result about UST Store.

Question: how dense would demand have to be before these forecasting
methods reach usable accuracy? tools/density_vs_accuracy.py answers that
with real items; this answers it on controlled data where sparsity is the
only thing that changes, so the relationship is clean.

Design, and why it is honest
-----------------------------
For each target density d (share of days with a sale), synthesise N series
of the same length as the real catalogue (821 days). A day sells with
probability d; when it sells, the amount is drawn from the REAL
catalogue's pooled nonzero sale sizes, so magnitudes stay realistic.

Then TRAIN AND TEST INSIDE THAT SAME SYNTHETIC WORLD, on the same
walk-forward harness. Nothing here is trained on fake data and scored on
real data, and nothing here claims a UST Store result - the only claim is
about how these METHODS behave as sparsity varies. Making the data easier
and reporting the improved score as a finding would be circular; keeping
train and test in one world is what stops that.

Deliberate simplification: days are independent (no trend, no seasonality,
no lifecycle). So this measures the effect of sparsity ALONE, which is the
point - but it also means the absolute numbers are optimistic compared to
real demand, which has all of that structure on top.

Fixed seed - reproduces exactly. Read-only; touches no DB table.

Run: python tools/sparsity_sensitivity_sim.py [--n-series N]
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
from forecasting.baselines import naive_fit_predict, rolling_mean_fit_predict
from forecasting.evaluate import (
    aggregate_blocks, make_folds, walk_forward_evaluate,
)
from forecasting.hurdle import weekly_hurdle_fit_predict
from forecasting.intermittent import tsb_fit_predict

OUT_CSV = "data/sparsity_sensitivity_sim.csv"
SEED = 20260904
DENSITIES = [0.01, 0.03, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75]

METHODS = {
    "naive": naive_fit_predict(),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "tsb": tsb_fit_predict(0.1, 0.1),
    "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-series", type=int, default=40,
                    help="synthetic series per density level")
    args = ap.parse_args()

    con = sqlite3.connect(mb.DB_NAME)
    series, _, index = mb.load_daily_series(con)
    con.close()

    # realistic sale sizes: every nonzero quantity in the real catalogue
    size_pool = np.concatenate(
        [np.asarray(v)[np.asarray(v) > 0] for v in series.values()])
    n_days = len(index)
    print(f"Simulating {args.n_series} series x {len(DENSITIES)} density levels, "
          f"{n_days} days each (seed={SEED})")
    print(f"Sale sizes drawn from the real catalogue's {size_pool.size:,} "
          f"nonzero observations (median {np.median(size_pool):.0f} units)")
    print(f"Real catalogue for reference: mean per-SKU density 7.3%\n")

    rng = np.random.default_rng(SEED)
    folds = make_folds(n_days, mb.HORIZON, mb.MIN_FOLDS, mb.MAX_FOLDS, mb.MIN_TRAIN)

    rows = []
    for d in DENSITIES:
        world = {}
        for i in range(args.n_series):
            sells = rng.random(n_days) < d
            v = np.zeros(n_days)
            if sells.any():
                v[sells] = rng.choice(size_pool, size=int(sells.sum()), replace=True)
            world[f"d{d}_{i}"] = v

        for name, fn in METHODS.items():
            per = []
            for key, values in world.items():
                ev = walk_forward_evaluate(key, values, fn, name, folds=folds)
                g = pd.DataFrame(ev.rows)
                err = (g["actual_30d"] - g["pred_30d"]).to_numpy(dtype=float)

                sc_a, sc_s = [], []
                for f in folds:
                    b = aggregate_blocks(values[:f.train_end], f.horizon)
                    if b.size > 1:
                        dd = np.diff(b)
                        sc_a.append(np.mean(np.abs(dd)))
                        sc_s.append(np.mean(dd ** 2))
                a_s = float(np.mean(sc_a)) if sc_a else np.nan
                s_s = float(np.mean(sc_s)) if sc_s else np.nan
                per.append({"mae": float(np.mean(np.abs(err))),
                            "mse": float(np.mean(err ** 2)),
                            "abs_scale": a_s, "sq_scale": s_s})

            ps = pd.DataFrame(per)
            ok = ps["abs_scale"].notna() & (ps["abs_scale"] > 0)
            ok_sq = ps["sq_scale"].notna() & (ps["sq_scale"] > 0)
            mase = ps.loc[ok, "mae"] / ps.loc[ok, "abs_scale"]
            rmsse = np.sqrt(ps.loc[ok_sq, "mse"] / ps.loc[ok_sq, "sq_scale"])

            rows.append({
                "density_pct": 100 * d,
                "method": name,
                "mae": ps["mae"].mean(),
                "mase_median": mase.median() if len(mase) else np.nan,
                "mase_global": (ps.loc[ok, "mae"].sum() / ps.loc[ok, "abs_scale"].sum()
                                if ok.any() else np.nan),
                "rmsse_median": rmsse.median() if len(rmsse) else np.nan,
            })

    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False, lineterminator="\n")

    print("=" * 84)
    print("OPTION A - SIMULATION: accuracy vs demand density (synthetic worlds)")
    print("=" * 84)
    piv = out.pivot(index="density_pct", columns="method", values="mase_median")
    print("MASE (median across series) - 1.0 would mean 'as good as the naive scale'")
    print(piv.to_string(float_format=lambda x: f"{x:.2f}"))
    print("\nRMSSE (median):")
    print(out.pivot(index="density_pct", columns="method",
                    values="rmsse_median").to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nWrote {OUT_CSV}")
    print("\nREMINDER: synthetic worlds, independent days, no seasonality or "
          "trend. This says how these METHODS respond to sparsity; it is not "
          "a claim about UST Store's achievable accuracy.")


if __name__ == "__main__":
    main()
