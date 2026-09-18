"""
scripts/category_split_prophet.py
------------------------------------------------------------------
Does splitting a category into Fast / Slow / Non-moving sub-series,
forecasting EACH sub-series' own aggregate separately, and summing the
three back together beat forecasting the category as one blob?

Why this exists
----------------
scripts/forecast_category_prophet.py showed that aggregating SKUs into
one category-level series before forecasting is the single biggest
accuracy lever measured in this project (8 of 12 categories beat naive).
But 4 categories still don't: Shirts & Tops, Lanyards & IDs,
Home & Novelty, Uncategorised. The working theory is that these are the
categories where aggregation itself works against the model - a handful
of fast, spiky sellers get summed together with a long tail of
near-dead SKUs, and the fast movers' bursts dominate a series that is
mostly quiet. Splitting by demand speed before aggregating should let
each sub-series find its own, more homogeneous signal.

An exploratory (non-fold-scoped) check of exactly this on Shirts & Tops
found a real improvement: MASE 1.36 -> ~1.14 when split into
Fast/Slow/Non-moving and summed back. This script makes that check
rigorous and repeatable:

  1. FOLD-SCOPED grouping. Which bucket a SKU falls into is recomputed
     PER FOLD from only that fold's pre-origin sales
     (forecasting.category.fold_scoped_fsn_labels) - never from the
     whole series. Using a static, full-history Fast/Slow/Non-moving
     label here would be the identical leak
     scripts/model_benchmark_category.py was corrected for: an early
     fold's bucket assignment would be partly decided by sales that
     happen after that fold's own origin.
  2. SAME MASE denominator as the un-split run. A fold's naive_scale
     depends only on the category's own training series, which is
     identical whether you forecast it as one series or as three that
     sum to it - so the split and un-split MASE for the same fold are
     divided by the SAME number, and only the numerator (the error)
     can differ. (An earlier, quicker version of this check summed each
     sub-group's OWN naive_scale instead, which double-counts by the
     triangle inequality and modestly overstates the improvement - fixed
     here.)
  3. SAME 12 fold origins as the un-split run, by construction: both are
     derived from the same category-level series in the same call.

Reuses load_inputs / walk_forward / fit_predict / sufficiency_tier /
plausibility_cap / score straight from forecast_category_prophet.py, so
the model, regressors and scoring are byte-identical to the validated
category-level run - only the grouping changes.

EXPERIMENTAL - see requirements/requirements-ml-experimental.txt
(Prophet). Run (from the repo root, after forecast_category_prophet.py):
    python scripts/category_split_prophet.py
        [--categories "Shirts & Tops,Lanyards & IDs"] [--horizon 30]
        [--folds 12] [--min-train 60]

Writes:
    data/category_split_prophet_folds.csv       (per category, sub_group, fold)
    data/category_split_prophet_summary.csv     (per category, sub_group, pooled metrics)
    data/category_split_prophet_comparison.csv  (per category: split vs. un-split MASE)
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

_spec = importlib.util.spec_from_file_location(
    "fcp", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "forecast_category_prophet.py"))
fcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fcp)

from forecasting.category import fold_scoped_fsn_labels

DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "ustore.db")
SALES_CSV = os.path.join(DATA_DIR, "rebuild_sales_long.csv")
DAYS_CSV = os.path.join(DATA_DIR, "rebuild_day_status.csv")

# The 4 of 12 categories forecast_category_prophet.py found still worse
# than naive as one series - the candidates this split is meant to help.
DEFAULT_CATEGORIES = ["Shirts & Tops", "Lanyards & IDs", "Home & Novelty",
                      "Uncategorised"]

LABELS = {"fast": "Fast", "slow": "Slow", "nonmoving": "NonMoving"}


def score_one_origin(train_df, future_ds, cal, horizon):
    """One fold's tier -> cap -> fit_predict -> fallback, exactly mirroring
    the body of forecast_category_prophet.walk_forward's per-origin loop -
    factored out so it can be called once per (sub_group, fold) instead of
    only once per (category, fold) as that script does."""
    naive_scale = fcp.naive_scale_blocks(train_df["y"], horizon)
    tier = fcp.sufficiency_tier(len(train_df))
    if tier == "none":
        rate = float(train_df["y"].tail(30).mean()) if len(train_df) else 0.0
        return {"pred_30d": rate * len(future_ds), "tier": tier,
                "fallback": 1, "capped": 0, "naive_scale": naive_scale}

    cap = fcp.plausibility_cap(train_df["y"], horizon)
    try:
        yhat, _used = fcp.fit_predict(train_df, future_ds, cal,
                                      fcp.CALENDAR_REGRESSORS, tier)
        raw = float(np.sum(yhat))
    except Exception:                                    # a Stan fit can fail
        rate = float(train_df["y"].tail(30).mean()) if len(train_df) else 0.0
        return {"pred_30d": rate * len(future_ds), "tier": tier,
                "fallback": 1, "capped": 0, "naive_scale": naive_scale}

    pred = min(raw, cap)
    return {"pred_30d": pred, "tier": tier, "fallback": 0,
            "capped": int(pred < raw), "naive_scale": naive_scale}


def run_category(category, sales, days, cal, trading, horizon, folds, min_train):
    """Returns (comparison_row, detail_rows) or (None, []) if the category
    doesn't have enough history to fold at all."""
    cat_sales = sales[sales["category"] == category]
    if cat_sales.empty:
        print(f"  [skip] {category:<20} no sales rows mapped to this category")
        return None, []

    whole = (cat_sales.groupby("calendar_date", as_index=False)["quantity_sold"]
                       .sum().rename(columns={"quantity_sold": "y",
                                              "calendar_date": "ds"}))
    baseline_folds = fcp.walk_forward(whole, trading, cal, horizon, folds, min_train)
    if baseline_folds is None:
        print(f"  [skip] {category:<20} insufficient history for even the "
              f"un-split series")
        return None, []
    baseline_metrics = fcp.score(baseline_folds["actual_30d"],
                                 baseline_folds["pred_30d"],
                                 baseline_folds["naive_scale"])

    # Reindex every SKU onto the trading calendar UP TO this category's own
    # last recorded sale, zero-filled - a day with no sale for a SKU is a
    # real zero, and every SKU needs the same index positions for
    # fold_scoped_fsn_labels' train_end to mean the same cutoff for all of
    # them.
    last_sale = whole["ds"].max()
    trading_clipped = [d for d in trading if d <= last_sale]
    pos = {d: i for i, d in enumerate(trading_clipped)}

    per_sku = (cat_sales.groupby(["item_name", "calendar_date"], as_index=False)
                        ["quantity_sold"].sum())
    series = {}
    for item, g in per_sku.groupby("item_name"):
        arr = np.zeros(len(trading_clipped), dtype=float)
        for d, q in zip(g["calendar_date"], g["quantity_sold"]):
            if d in pos:
                arr[pos[d]] = q
        series[item] = arr
    item_daily = per_sku.rename(columns={"calendar_date": "ds",
                                         "quantity_sold": "y"})

    detail_rows = []
    recon_actual, recon_pred, recon_scale = [], [], []
    for cut in sorted(baseline_folds["origin"]):
        train_end = sum(1 for d in trading_clipped if d <= cut)
        labels = fold_scoped_fsn_labels(series, train_end)
        win_lo = cut + pd.Timedelta(days=1)
        win_hi = cut + pd.Timedelta(days=horizon)
        future_ds = [d for d in trading if win_lo <= d <= win_hi]
        base_row = baseline_folds[baseline_folds["origin"] == cut].iloc[0]

        bucket_pred_total = 0.0
        for label_key, label_name in LABELS.items():
            items = [sku for sku, lab in labels.items() if lab == label_key]
            if not items:
                continue
            bucket_daily = item_daily[item_daily["item_name"].isin(items)]
            bucket_daily = (bucket_daily.groupby("ds", as_index=False)["y"].sum())
            train_df = bucket_daily[bucket_daily["ds"] <= cut]
            actual_bucket = float(bucket_daily.loc[
                (bucket_daily["ds"] >= win_lo) & (bucket_daily["ds"] <= win_hi), "y"
            ].sum())

            row = score_one_origin(train_df, future_ds, cal, horizon)
            bucket_pred_total += row["pred_30d"]
            detail_rows.append({
                "category": category, "sub_group": label_name, "origin": cut,
                "n_items": len(items), "actual_30d": actual_bucket,
                "pred_30d": row["pred_30d"], "tier": row["tier"],
                "fallback": row["fallback"], "capped": row["capped"],
            })

        recon_actual.append(base_row["actual_30d"])
        recon_pred.append(bucket_pred_total)
        recon_scale.append(base_row["naive_scale"])

    split_metrics = fcp.score(pd.Series(recon_actual), pd.Series(recon_pred),
                              pd.Series(recon_scale))

    comparison_row = {
        "category": category,
        "n_folds": baseline_metrics["n_folds"],
        "unsplit_mase": baseline_metrics["mase"],
        "split_mase": split_metrics["mase"],
        "delta_mase": split_metrics["mase"] - baseline_metrics["mase"],
        "pct_improvement": 100 * (baseline_metrics["mase"] - split_metrics["mase"])
                          / baseline_metrics["mase"],
        "split_beats_naive": split_metrics["mase"] < 1.0,
        "unsplit_beats_naive": baseline_metrics["mase"] < 1.0,
    }
    return comparison_row, detail_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", default=None,
                    help="comma-separated category list (default: the 4 "
                         "categories forecast_category_prophet.py found "
                         "worse than naive)")
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

    categories = ([c.strip() for c in args.categories.split(",")]
                 if args.categories else DEFAULT_CATEGORIES)

    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod = pd.read_sql_query("SELECT item_name, category FROM Dim_Product", con)
    cal = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(fcp.CALENDAR_REGRESSORS),
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    con.close()

    cat_map = (prod.dropna(subset=["item_name"])
                   .assign(key=lambda d: d.item_name.astype(str).str.strip().str.upper())
                   .drop_duplicates("key")
                   .set_index("key")["category"].to_dict())

    sales = pd.read_csv(SALES_CSV, parse_dates=["calendar_date"])
    days = pd.read_csv(DAYS_CSV, parse_dates=["calendar_date"])
    sales["key"] = sales["item_name"].astype(str).str.strip().str.upper()
    sales["category"] = sales["key"].map(cat_map).fillna("Uncategorised")

    observable = set(days.loc[days["demand_observable"] == 1, "calendar_date"])
    sales = sales[sales["calendar_date"].isin(observable)]

    trading = sorted(pd.to_datetime(
        days.loc[days["demand_observable"] == 1, "calendar_date"]).unique())
    trading = [pd.Timestamp(d) for d in trading]

    print("=" * 92)
    print("CATEGORY SPLIT TEST: Fast / Slow / Non-moving, fold-scoped, summed back")
    print("=" * 92)
    print(f"categories: {', '.join(categories)}")
    print(f"harness: horizon {args.horizon}, up to {args.folds} folds, "
          f"min_train {args.min_train}\n")

    comparisons, all_details = [], []
    for category in categories:
        row, details = run_category(category, sales, days, cal, trading,
                                    args.horizon, args.folds, args.min_train)
        if row is None:
            continue
        comparisons.append(row)
        all_details.extend(details)
        verdict = ("BEATS naive now" if row["split_beats_naive"] else
                  "still worse than naive")
        print(f"  {category:<20} un-split MASE {row['unsplit_mase']:.3f}  ->  "
              f"split MASE {row['split_mase']:.3f}  "
              f"({row['pct_improvement']:+.1f}%, {verdict})")

    if not comparisons:
        print("\nNo category had enough history to evaluate.")
        return 1

    comp_df = pd.DataFrame(comparisons)
    detail_df = pd.DataFrame(all_details)

    summary_rows = []
    for (category, sub_group), g in detail_df.groupby(["category", "sub_group"]):
        a, p = g["actual_30d"].to_numpy(float), g["pred_30d"].to_numpy(float)
        defined = a != 0
        summary_rows.append({
            "category": category, "sub_group": sub_group, "n_folds": len(g),
            "mape_pct": float(np.mean(np.abs(a[defined] - p[defined]) / a[defined]) * 100)
                       if defined.any() else np.nan,
            "mae": float(np.mean(np.abs(a - p))),
            "n_items_last_fold": int(g["n_items"].iloc[-1]),
            "n_fallback": int(g["fallback"].sum()),
        })
    summary_df = pd.DataFrame(summary_rows)[
        ["category", "sub_group", "n_folds", "mape_pct", "mae",
         "n_items_last_fold", "n_fallback"]
    ]

    comp_out = os.path.join(DATA_DIR, "category_split_prophet_comparison.csv")
    summary_out = os.path.join(DATA_DIR, "category_split_prophet_summary.csv")
    folds_out = os.path.join(DATA_DIR, "category_split_prophet_folds.csv")
    comp_df.to_csv(comp_out, index=False, lineterminator="\n")
    summary_df.to_csv(summary_out, index=False, lineterminator="\n")
    detail_df.to_csv(folds_out, index=False, lineterminator="\n")

    print("\n" + "=" * 92)
    print("COMPARISON: split vs. un-split MASE")
    print("=" * 92)
    print(comp_df.to_string(index=False, float_format=lambda x: "%.3f" % x))
    n_helped = int(comp_df["delta_mase"].lt(0).sum())
    n_flipped = int(comp_df["split_beats_naive"].sum() - comp_df["unsplit_beats_naive"].sum())
    print(f"\n  splitting helped {n_helped} of {len(comp_df)} categories "
          f"(lower MASE); {n_flipped} newly beat naive that didn't before.")

    print(f"\nWrote {comp_out}")
    print(f"Wrote {summary_out}")
    print(f"Wrote {folds_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
