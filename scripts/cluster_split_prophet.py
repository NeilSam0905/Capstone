"""
scripts/cluster_split_prophet.py
------------------------------------------------------------------
Does grouping SKUs by DEMAND-BEHAVIOR CLUSTER (forecasting/clustering.py,
k=4 - density/mean_nz/cv_nz/log_total/log_price, the same clustering
scripts/model_benchmark_category.py already uses to decide which SKUs
share a POOLED model) beat grouping by SEMANTIC CATEGORY, when both are
tested with the SAME technique: sum the group's sales into one series,
forecast that series, and add the groups' forecasts back up?

Why this comparison, and not another
-------------------------------------
Two different things have been tested separately so far:

  - cluster-based grouping, tested by POOLING training rows across a
    shared per-SKU model (scripts/model_benchmark_category.py --by
    cluster) - beat category-based pooling by 30-49%, but the pooled
    models still never beat the best single per-SKU baseline.
  - category-based grouping, tested by COMBINING sales into one series
    per category before forecasting (forecast_category_prophet.py) -
    beat naive on 8 of 12 categories, the best result in the project.

Those two results are NOT comparable as-is: they differ in both the
GROUPING (cluster vs. category) and the TECHNIQUE (pool-then-predict-
per-SKU vs. combine-then-predict-per-group) at once, so neither one says
whether cluster is a better or worse grouping than category - only that
combine-then-predict is a stronger technique than pool-then-predict.
This script holds the technique fixed (combine-then-predict, exactly
forecast_category_prophet.py's method) and swaps only the grouping, to
isolate that one variable.

Method
------
Fold-scoped, causal, no leakage - same discipline
scripts/category_split_prophet.py already applies: cluster membership is
recomputed at EVERY fold from only that fold's pre-origin sales
(forecasting.clustering.sku_features / cluster_skus, chained via
match_clusters_to_reference for label stability, exactly as
model_benchmark_category.py's `--by cluster` grouper already does) -
never from the whole series. Both schemes are then reconstructed to a
single WHOLE-CATALOGUE number over the SAME fold origins and the SAME
naive_scale denominator (derived once from the whole-catalogue series),
so "category wins" or "cluster wins" reflects the grouping choice alone.

Reuses fit_predict / sufficiency_tier / plausibility_cap / walk_forward
from forecast_category_prophet.py and score_one_origin from
category_split_prophet.py - the model, regressors and scoring are
byte-identical to both already-validated runs.

EXPERIMENTAL - see requirements/requirements-ml-experimental.txt
(Prophet, scikit-learn). Run (from the repo root, after
forecast_category_prophet.py has produced data/category_prophet_forecast.csv):
    python scripts/cluster_split_prophet.py [--k 4] [--horizon 30]
        [--folds 12] [--min-train 60]

Writes:
    data/cluster_split_prophet_folds.csv     (per cluster, per fold)
    data/cluster_split_prophet_comparison.csv (cluster-based vs. category-based,
                                               whole-catalogue, same origins)
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(SCRIPTS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fcp = _load_module("fcp", "forecast_category_prophet.py")
csp = _load_module("csp", "category_split_prophet.py")

from forecasting.clustering import sku_features, cluster_skus

DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "ustore.db")
SALES_CSV = os.path.join(DATA_DIR, "rebuild_sales_long.csv")
DAYS_CSV = os.path.join(DATA_DIR, "rebuild_day_status.csv")
CATEGORY_FOLDS_CSV = os.path.join(DATA_DIR, "category_prophet_forecast.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=4,
                    help="number of clusters (default: 4, matching "
                         "model_benchmark_category.py's --by cluster default)")
    ap.add_argument("--cluster-seed", type=int, default=0)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--folds", type=int, default=12)
    ap.add_argument("--min-train", type=int, default=60)
    args = ap.parse_args()
    fcp._quiet()

    try:
        import prophet  # noqa: F401
    except ImportError:
        print("prophet is not installed - install it rather than silently "
              "substituting another model.")
        return 2
    if not os.path.exists(CATEGORY_FOLDS_CSV):
        print(f"missing {CATEGORY_FOLDS_CSV} - run "
              f"scripts/forecast_category_prophet.py first")
        return 1

    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod = pd.read_sql_query("SELECT item_name, unit_price_php FROM Dim_Product", con)
    cal = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(fcp.CALENDAR_REGRESSORS),
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    con.close()

    prices = (prod.dropna(subset=["item_name"])
                  .assign(key=lambda d: d.item_name.astype(str).str.strip().str.upper())
                  .drop_duplicates("key")
                  .set_index("key")["unit_price_php"].to_dict())

    sales = pd.read_csv(SALES_CSV, parse_dates=["calendar_date"])
    days = pd.read_csv(DAYS_CSV, parse_dates=["calendar_date"])
    sales["key"] = sales["item_name"].astype(str).str.strip().str.upper()

    observable = set(days.loc[days["demand_observable"] == 1, "calendar_date"])
    sales = sales[sales["calendar_date"].isin(observable)]

    trading = sorted(pd.to_datetime(
        days.loc[days["demand_observable"] == 1, "calendar_date"]).unique())
    trading = [pd.Timestamp(d) for d in trading]

    print("=" * 92)
    print(f"CLUSTER-BASED vs CATEGORY-BASED aggregate forecasting - same "
          f"technique, whole catalogue")
    print("=" * 92)

    # ---- whole-catalogue series: origins + baseline naive_scale --------
    whole = (sales.groupby("calendar_date", as_index=False)["quantity_sold"]
                  .sum().rename(columns={"quantity_sold": "y",
                                         "calendar_date": "ds"}))
    baseline_folds = fcp.walk_forward(whole, trading, cal, args.horizon,
                                      args.folds, args.min_train)
    if baseline_folds is None:
        print("insufficient whole-catalogue history to fold at all.")
        return 1
    origins = sorted(baseline_folds["origin"])
    print(f"whole-catalogue folds: {len(origins)} origins, "
          f"{origins[0].date()} .. {origins[-1].date()}\n")

    last_sale = whole["ds"].max()
    trading_clipped = [d for d in trading if d <= last_sale]
    pos = {d: i for i, d in enumerate(trading_clipped)}

    per_sku = (sales.groupby(["key", "calendar_date"], as_index=False)
                    ["quantity_sold"].sum())
    series = {}
    for item, g in per_sku.groupby("key"):
        arr = np.zeros(len(trading_clipped), dtype=float)
        for d, q in zip(g["calendar_date"], g["quantity_sold"]):
            if d in pos:
                arr[pos[d]] = q
        series[item] = arr
    item_daily = per_sku.rename(columns={"calendar_date": "ds",
                                         "quantity_sold": "y"})

    # ---- fold-scoped cluster assignment, chained for label stability ---
    detail_rows = []
    chain_state = {"centers": None}
    for cut in origins:
        train_end = sum(1 for d in trading_clipped if d <= cut)
        sliced = {sku: v[:train_end] for sku, v in series.items()}
        feat = sku_features(sliced, prices)
        labels, _km, _scaler, centers = cluster_skus(
            feat, k=args.k, seed=args.cluster_seed,
            reference_centers=chain_state["centers"])
        chain_state["centers"] = centers

        win_lo = cut + pd.Timedelta(days=1)
        win_hi = cut + pd.Timedelta(days=args.horizon)
        future_ds = [d for d in trading if win_lo <= d <= win_hi]

        for cluster_label in sorted(set(labels.values())):
            items = [sku for sku, lab in labels.items() if lab == cluster_label]
            if not items:
                continue
            bucket_daily = item_daily[item_daily["key"].isin(items)]
            bucket_daily = bucket_daily.groupby("ds", as_index=False)["y"].sum()
            train_df = bucket_daily[bucket_daily["ds"] <= cut]
            actual_bucket = float(bucket_daily.loc[
                (bucket_daily["ds"] >= win_lo) & (bucket_daily["ds"] <= win_hi), "y"
            ].sum())
            row = csp.score_one_origin(train_df, future_ds, cal, args.horizon)
            detail_rows.append({
                "cluster": cluster_label, "origin": cut, "n_items": len(items),
                "actual_30d": actual_bucket, "pred_30d": row["pred_30d"],
                "tier": row["tier"], "fallback": row["fallback"],
            })
        print(f"  fold {cut.date()}: {len(set(labels.values()))} clusters, "
              f"sizes {sorted([sum(1 for l in labels.values() if l==c) for c in set(labels.values())], reverse=True)}")

    detail_df = pd.DataFrame(detail_rows)
    detail_df.to_csv(os.path.join(DATA_DIR, "cluster_split_prophet_folds.csv"),
                     index=False, lineterminator="\n")

    # ---- reconstruct whole-catalogue totals, only where all k clusters present
    counts = detail_df.groupby("origin")["cluster"].nunique()
    full_origins = counts[counts == args.k].index
    cluster_pooled = (detail_df[detail_df["origin"].isin(full_origins)]
                      .groupby("origin")
                      .agg(actual_30d=("actual_30d", "sum"),
                           pred_30d=("pred_30d", "sum"))
                      .reset_index())

    # SAME naive_scale denominator for both schemes: the whole-catalogue's
    # own baseline, at the SAME origins.
    scale_lookup = baseline_folds.set_index("origin")["naive_scale"]
    cluster_pooled["naive_scale"] = cluster_pooled["origin"].map(scale_lookup)
    cluster_metrics = fcp.score(cluster_pooled["actual_30d"],
                               cluster_pooled["pred_30d"],
                               cluster_pooled["naive_scale"])

    # ---- the same reconstruction for the ALREADY-COMMITTED category run,
    # restricted to the SAME origins, for a true apples-to-apples number.
    cat_folds = pd.read_csv(CATEGORY_FOLDS_CSV, parse_dates=["origin"])
    n_categories = cat_folds["forecast_category"].nunique()
    cat_counts = cat_folds.groupby("origin")["forecast_category"].nunique()
    # Home & Novelty's own last recorded sale sits 1 day off the other 11
    # categories', so its fold origins never land on the same date as
    # everyone else's - no origin has all 12. Requiring n_categories-1
    # instead means Home & Novelty (the smallest category, ~37 units/30d,
    # a small fraction of the whole catalogue) drops out of the
    # category-based total at every fold; noted rather than silently
    # absorbed, since the cluster-based total is NOT missing those SKUs.
    cat_full_origins = cat_counts[cat_counts >= n_categories - 1].index
    dropped = set(cat_folds["forecast_category"]) - set(
        cat_folds[cat_folds["origin"].isin(cat_full_origins)]["forecast_category"])
    if dropped:
        print(f"  NOTE: {', '.join(dropped)} excluded from the category-based "
              f"total at every shared origin (its own fold origins never "
              f"align with the other categories').")
    common_origins = sorted(set(full_origins) & set(cat_full_origins) & set(origins))

    cat_pooled = (cat_folds[cat_folds["origin"].isin(common_origins)]
                 .groupby("origin")
                 .agg(actual_30d=("actual_30d", "sum"),
                      pred_30d=("pred_30d", "sum"))
                 .reset_index())
    cat_pooled["naive_scale"] = cat_pooled["origin"].map(scale_lookup)
    cat_metrics = fcp.score(cat_pooled["actual_30d"], cat_pooled["pred_30d"],
                            cat_pooled["naive_scale"])

    cluster_pooled_common = cluster_pooled[cluster_pooled["origin"].isin(common_origins)]
    cluster_metrics_common = fcp.score(cluster_pooled_common["actual_30d"],
                                       cluster_pooled_common["pred_30d"],
                                       cluster_pooled_common["naive_scale"])

    print("\n" + "=" * 92)
    print(f"WHOLE-CATALOGUE COMPARISON on {len(common_origins)} shared fold origins")
    print("=" * 92)
    print(f"  category-based (12 groups)  MASE: {cat_metrics['mase']:.3f}  "
          f"MAE: {cat_metrics['mae']:.1f}")
    print(f"  cluster-based  ({args.k} groups)   MASE: {cluster_metrics_common['mase']:.3f}  "
          f"MAE: {cluster_metrics_common['mae']:.1f}")
    better = ("cluster-based" if cluster_metrics_common["mase"] < cat_metrics["mase"]
             else "category-based")
    print(f"\n  {better} grouping wins on this comparison.")

    comp = pd.DataFrame([
        {"scheme": "category (12 groups)", "n_folds": cat_metrics["n_folds"],
         "mase": cat_metrics["mase"], "mae": cat_metrics["mae"]},
        {"scheme": f"cluster ({args.k} groups)", "n_folds": cluster_metrics_common["n_folds"],
         "mase": cluster_metrics_common["mase"], "mae": cluster_metrics_common["mae"]},
    ])
    comp_out = os.path.join(DATA_DIR, "cluster_split_prophet_comparison.csv")
    comp.to_csv(comp_out, index=False, lineterminator="\n")
    print(f"\nWrote {comp_out}")
    print(f"Wrote {os.path.join(DATA_DIR, 'cluster_split_prophet_folds.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
