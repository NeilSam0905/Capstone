"""
tools/robust_metric_comparison.py
------------------------------------------------------------------
Is the "pooling loses on MASE" result real, or an artifact of how MASE is
AGGREGATED across SKUs?

`forecasting.evaluate.summarise` computes MASE per SKU and takes the plain
MEAN. On this catalogue that is fragile: 90 of 266 moving SKUs have a
near-zero MASE denominator (their 30-day training blocks barely differ),
so a 1.3-unit absolute error on one near-flat SKU became a MASE of 393 and
dragged the average for all 266. Every other metric in today's runs (MAE,
RMSE, fill rate, SKUs priced) says pooling helps; only mean-MASE says it
loses. That disagreement is what this script exists to resolve.

Recomputes, for every saved run, the same per-SKU errors under five
aggregations:

    mean MASE        what summarise() reports today
    median MASE      robust to the near-zero-denominator tail
    trimmed MASE     mean after dropping the worst/best 10%
    weighted MASE    per-SKU MASE weighted by that SKU's total real demand
    global MASE      sum of all abs errors / sum of all denominators
                     (one ratio, not a mean of ratios)

plus RMSSE (M5's squared-error analogue of MASE, mean and median), which
uses mean SQUARED consecutive block differences as its scale.

Denominators are ALWAYS recomputed here from REAL training data only, per
(sku, fold), never from a stored naive_scale column - so the synthetic run
is scored on the same yardstick as every other run. Runs are joined on
`fold` rather than `origin` because the synthetic run's origins are in
augmented coordinates (shifted by the prepended pre-history) while its
fold INDICES map to the same real 30-day windows.

Read-only. Writes one CSV; touches no DB table.

Run: python tools/robust_metric_comparison.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import model_benchmark as mb
from forecasting.evaluate import aggregate_blocks, make_folds

OUT_CSV = "data/robust_metric_comparison.csv"

# variant label -> (results csv, method-name filter)
TREE_RUNS = [
    ("per_sku (committed)", "data/model_benchmark_ml_results.csv", ""),
    ("pooled_by_category", "data/model_benchmark_category_results.csv", "_pooled_cat"),
    ("pooled_by_category_speed",
     "data/model_benchmark_category_results_category_speed.csv", "_pooled_cat"),
    ("pooled_by_product_type",
     "data/model_benchmark_category_results_product_type.csv", "_pooled_cat"),
    ("pooled_by_category_speed_syn5y",
     "data/model_benchmark_category_results_category_speed_syn5y.csv", "_pooled_cat"),
]

STATISTICAL_RUN = ("committed statistical benchmark",
                   "data/model_benchmark_results.csv", "")

# the pooled-logistic-hurdle experiment (docs/SPARSE_DEMAND_EXPERIMENTS.md
# section 2's untried fix). Method names already carry _pooled_cat /
# _per_sku, so no suffix is stripped - the variant column separates the
# real-history run from the +synthetic one.
HURDLE_RUNS = [
    ("real history",
     "data/model_benchmark_category_results_category_speed_hurdle.csv", ""),
    ("+5y synthetic",
     "data/model_benchmark_category_results_category_speed_syn5y_hurdle.csv", ""),
]


def real_fold_scales(series, folds):
    """(sku, fold_index) -> (abs_scale, sq_scale) from REAL training blocks.

    abs_scale is MASE's denominator (mean |consecutive block difference|);
    sq_scale is RMSSE's (mean squared consecutive block difference)."""
    out = {}
    for sku, values in series.items():
        for f in folds:
            blocks = aggregate_blocks(values[:f.train_end], f.horizon)
            if blocks.size > 1:
                d = np.diff(blocks)
                out[(sku, f.fold_index)] = (float(np.mean(np.abs(d))),
                                            float(np.mean(d ** 2)))
            else:
                out[(sku, f.fold_index)] = (float("nan"), float("nan"))
    return out


def per_sku_metrics(df, scales):
    """One row per SKU: MAE, MSE, demand, and both scale denominators."""
    rows = []
    for sku, g in df.groupby("sku"):
        err = (g["actual_30d"] - g["pred_30d"]).to_numpy(dtype=float)
        abs_s, sq_s = [], []
        for fold in g["fold"].to_numpy():
            a, s = scales.get((sku, int(fold)), (np.nan, np.nan))
            abs_s.append(a)
            sq_s.append(s)
        rows.append({
            "sku": sku,
            "mae": float(np.mean(np.abs(err))),
            "mse": float(np.mean(err ** 2)),
            "demand": float(g["actual_30d"].sum()),
            "abs_scale": float(np.nanmean(abs_s)),
            "sq_scale": float(np.nanmean(sq_s)),
        })
    return pd.DataFrame(rows)


def aggregate_metrics(ps):
    """The five MASE aggregations plus RMSSE, from a per-SKU frame."""
    ok = ps["abs_scale"].notna() & (ps["abs_scale"] > 0)
    v = ps[ok].copy()
    v["mase"] = v["mae"] / v["abs_scale"]

    ok_sq = v["sq_scale"].notna() & (v["sq_scale"] > 0)
    rmsse = np.sqrt(v.loc[ok_sq, "mse"] / v.loc[ok_sq, "sq_scale"])

    w = v["demand"].to_numpy(dtype=float)
    weighted = (float(np.average(v["mase"], weights=w))
                if w.sum() > 0 else float("nan"))

    lo, hi = v["mase"].quantile([0.05, 0.95])
    trimmed = v.loc[v["mase"].between(lo, hi), "mase"].mean()

    # share of the mean that the 10 worst SKUs alone contribute
    worst10 = v["mase"].nlargest(10).sum()
    share_worst10 = 100.0 * worst10 / v["mase"].sum() if v["mase"].sum() else np.nan

    return {
        "mae": ps["mae"].mean(),
        "mase_mean": v["mase"].mean(),
        "mase_median": v["mase"].median(),
        "mase_trimmed": trimmed,
        "mase_weighted": weighted,
        "mase_global": v["mae"].sum() / v["abs_scale"].sum(),
        "rmsse_mean": rmsse.mean() if len(rmsse) else np.nan,
        "rmsse_median": rmsse.median() if len(rmsse) else np.nan,
        "pct_mase_from_worst10": share_worst10,
        "n_skus": len(v),
    }


def collect(runs, scales, methods_wanted=None):
    rows = []
    for variant, path, suffix in runs:
        if not os.path.exists(path):
            print(f"  (skipping missing {path})")
            continue
        df = pd.read_csv(path)
        for method, g in df.groupby("method"):
            base = method.replace(suffix, "") if suffix else method
            if methods_wanted and base not in methods_wanted:
                continue
            m = aggregate_metrics(per_sku_metrics(g, scales))
            m["method"] = base
            m["variant"] = variant
            rows.append(m)
    return pd.DataFrame(rows)


def show(df, title, sort_col="mase_median"):
    cols = ["method", "variant", "mae", "mase_mean", "mase_median",
            "mase_trimmed", "mase_weighted", "mase_global",
            "rmsse_mean", "rmsse_median", "pct_mase_from_worst10"]
    print("\n" + "=" * 118)
    print(title)
    print("=" * 118)
    print(df.sort_values(sort_col)[cols].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))


def main():
    con = sqlite3.connect(mb.DB_NAME)
    series, _, index = mb.load_daily_series(con)
    con.close()

    folds = make_folds(len(index), horizon=mb.HORIZON, min_folds=mb.MIN_FOLDS,
                       max_folds=mb.MAX_FOLDS, min_train=mb.MIN_TRAIN)
    print(f"{len(series)} SKUs, {len(folds)} folds (real coordinates: origins "
          f"{folds[0].origin}..{folds[-1].origin})")

    scales = real_fold_scales(series, folds)
    small = sum(1 for (a, _) in scales.values() if a is not None and a == a and a < 1.0)
    print(f"(sku, fold) pairs with a MASE denominator < 1.0: {small} of {len(scales)} "
          f"- this is the tail that makes mean-MASE fragile\n")

    tree = collect(TREE_RUNS, scales,
                   methods_wanted={"xgboost", "lightgbm", "random_forest"})
    show(tree, "TREE MODELS - every MASE aggregation, all of today's runs")

    stat = collect([STATISTICAL_RUN], scales)
    show(stat, "COMMITTED STATISTICAL BENCHMARK - does the ranking survive "
               "a robust aggregation?")

    hurdle = collect(HURDLE_RUNS, scales)
    show(hurdle, "POOLED LOGISTIC HURDLE - the fix docs/SPARSE_DEMAND_"
                 "EXPERIMENTS.md section 2 asked for")

    out = pd.concat([tree, stat, hurdle], ignore_index=True)
    out.to_csv(OUT_CSV, index=False, lineterminator="\n")
    print(f"\nWrote {OUT_CSV}")


if __name__ == "__main__":
    main()
