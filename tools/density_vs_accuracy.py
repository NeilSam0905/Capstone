"""
tools/density_vs_accuracy.py
------------------------------------------------------------------
"Is the problem the models, or the data?" - answered with REAL data only,
no synthetic anything.

Two views of the same question:

  B1  fast vs slow movers, using Dim_Product.fsn_class (the store's own
      F/S tag - read-only controlled vocabulary, not derived here)
  B2  accuracy bucketed by each SKU's actual DEMAND DENSITY - the share
      of days it sold anything at all. This is the direct version of the
      question: does accuracy improve as demand gets less sparse, and how
      dense does an item have to be before these methods work?

B2 is the one to read first. It uses only observed items, so unlike a
simulation it cannot be accused of assuming its own answer.

Scores nothing itself - reads the per-fold CSVs every benchmark run
already wrote, and rescores them with the robust aggregations from
tools/robust_metric_comparison.py (mean MASE is dominated by a
near-zero-denominator tail on this catalogue; median/global are not).

Read-only. Writes one CSV.

Run: python tools/density_vs_accuracy.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import model_benchmark as mb
from forecasting.evaluate import make_folds
from tools.robust_metric_comparison import (
    aggregate_metrics, per_sku_metrics, real_fold_scales,
)

OUT_CSV = "data/density_vs_accuracy.csv"

RUNS = [
    ("data/model_benchmark_results.csv", None),              # 10 statistical
    ("data/model_benchmark_ml_results.csv", None),           # trees, per-SKU
    ("data/model_benchmark_category_results_category_speed_hurdle.csv", None),
]

# density buckets: share of the SKU's real days with any sale
BUCKETS = [
    ("almost never (<2%)", 0.00, 0.02),
    ("rare (2-5%)", 0.02, 0.05),
    ("occasional (5-10%)", 0.05, 0.10),
    ("regular (10-25%)", 0.10, 0.25),
    ("frequent (>25%)", 0.25, 1.01),
]

# the methods worth showing per bucket - one per family, else the table
# is unreadable
FOCUS = ["naive", "rolling_mean_30", "tsb", "weekly_hurdle_12w_per_sku",
         "logistic_hurdle_pooled_cat", "xgboost"]


def load_context(con):
    """Per SKU: real demand density, total demand, and the store's F/S tag."""
    series, _, index = mb.load_daily_series(con)
    fsn = dict(con.execute(
        "SELECT product_id, fsn_class FROM Dim_Product").fetchall())

    ctx = pd.DataFrame([{
        "sku": sku,
        "density": float(np.mean(np.asarray(v) > 0)),
        "total_demand": float(np.sum(v)),
        "fsn_class": fsn.get(sku),
    } for sku, v in series.items()])
    return series, index, ctx


def bucket_of(density):
    for name, lo, hi in BUCKETS:
        if lo <= density < hi:
            return name
    return BUCKETS[-1][0]


def collect(scales, ctx):
    """Per (method, subset) metrics, for every subset we care about."""
    rows = []
    for path, _ in RUNS:
        if not os.path.exists(path):
            print(f"  (skipping missing {path})")
            continue
        df = pd.read_csv(path)
        for method, g in df.groupby("method"):
            ps = per_sku_metrics(g, scales).merge(ctx, on="sku")

            for label, sub in [
                ("ALL", ps),
                ("fast movers (F)", ps[ps["fsn_class"] == "F"]),
                ("slow movers (S)", ps[ps["fsn_class"] == "S"]),
            ]:
                if sub.empty:
                    continue
                m = aggregate_metrics(sub)
                m.update(method=method, subset=label,
                         mean_density=100 * sub["density"].mean())
                rows.append(m)

            for label, lo, hi in BUCKETS:
                sub = ps[(ps["density"] >= lo) & (ps["density"] < hi)]
                if sub.empty:
                    continue
                m = aggregate_metrics(sub)
                m.update(method=method, subset=label,
                         mean_density=100 * sub["density"].mean())
                rows.append(m)
    return pd.DataFrame(rows)


def main():
    con = sqlite3.connect(mb.DB_NAME)
    series, index, ctx = load_context(con)
    con.close()

    folds = make_folds(len(index), horizon=mb.HORIZON, min_folds=mb.MIN_FOLDS,
                       max_folds=mb.MAX_FOLDS, min_train=mb.MIN_TRAIN)
    scales = real_fold_scales(series, folds)

    ctx["bucket"] = ctx["density"].map(bucket_of)
    print(f"{len(ctx)} SKUs. Demand density = share of the 821 real days with "
          f"any sale.\n")
    print("How the catalogue splits by density:")
    counts = (ctx.groupby("bucket")
                 .agg(n_skus=("sku", "size"),
                      mean_density_pct=("density", lambda s: 100 * s.mean()),
                      median_total_demand=("total_demand", "median"))
                 .reindex([b[0] for b in BUCKETS]).dropna(how="all"))
    print(counts.to_string(float_format=lambda x: f"{x:.1f}"))

    print("\nBy the store's own fast/slow tag:")
    print(ctx.groupby("fsn_class")
             .agg(n_skus=("sku", "size"),
                  mean_density_pct=("density", lambda s: 100 * s.mean()))
             .to_string(float_format=lambda x: f"{x:.1f}"))

    res = collect(scales, ctx)
    # naive appears in several run files; identical rows, keep one
    res = res.drop_duplicates(subset=["method", "subset"]).reset_index(drop=True)
    res.to_csv(OUT_CSV, index=False, lineterminator="\n")

    cols = ["method", "subset", "n_skus", "mean_density", "mae",
            "mase_median", "mase_global", "rmsse_median"]

    print("\n" + "=" * 100)
    print("B1 - FAST vs SLOW MOVERS (the store's own F/S tag)")
    print("=" * 100)
    fs = res[res["subset"].isin(["ALL", "fast movers (F)", "slow movers (S)"])]
    fs = fs[fs["method"].isin(FOCUS)]
    print(fs.sort_values(["method", "subset"])[cols].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))

    print("\n" + "=" * 100)
    print("B2 - ACCURACY BY DEMAND DENSITY (real items only)")
    print("=" * 100)
    order = {b[0]: i for i, b in enumerate(BUCKETS)}
    bk = res[res["subset"].isin(order)].copy()
    bk = bk[bk["method"].isin(FOCUS)]
    bk["_o"] = bk["subset"].map(order)
    print(bk.sort_values(["method", "_o"])[cols].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))

    print(f"\nWrote {OUT_CSV}")


if __name__ == "__main__":
    main()
