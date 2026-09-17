"""
scripts/export_fastmoving_benchmark_csv.py
------------------------------------------------------------------
Flattens the Fast-moving benchmark into the SAME two-CSV shape
`scripts/model_benchmark.py` already writes, so the new run can be read
with whatever already reads the old one.

    data/fastmoving_benchmark_results.csv   <- model_benchmark_results.csv
    data/fastmoving_benchmark_summary.csv   <- model_benchmark_summary.csv

Column layout matches the originals exactly, with `stage` prepended
(CLEAN / RAW - the two datasets this benchmark compares) and, on the
results file only, `fsn_class` and `in_fast_set` appended so the summary
is reproducible from the results file without needing the database.

Nothing here re-scores anything. The forecasts come from
`data/fastmoving_benchmark_folds.csv`, written by
`benchmark_fast_raw_vs_clean.py`; this script adds the decision-metric
columns (`safety_stock`, `stock_level`, `units_served`, `units_short`,
`units_held`) using model_benchmark.py's own `service_metrics`, so the
two runs' service numbers are computed by one implementation rather than
two that agree until they don't.

`n_skus_priced` is the one column that does require running the models
again: it is defined as "how many SKUs does this method give a POSITIVE
30-day forecast for, on the full history" - the column that exposes a
degenerate forecast, since a method that predicts zero yields no annual
demand, no EOQ, and nothing the store can order. That is a different
question from forecast error and cannot be recovered from the fold rows.

Which SKUs each summary row covers
----------------------------------
The benchmark scored the UNION of the two Fast definitions on the clean
side (76 SKUs). The summary here reports the FAST SET per stage:
  CLEAN  Dim_Product.fsn_class == 'F' (the pipeline's own set, 58 SKUs)
  RAW    the recomputed ADUS rule, the only one raw data supports (54)
`in_fast_set` in the results file marks exactly those rows, so
    results[results.in_fast_set].groupby(['stage','method'])
reproduces the summary.

Run (from the repo root, after benchmark_fast_raw_vs_clean.py):
    python scripts/export_fastmoving_benchmark_csv.py [--no-priced]
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
sys.path.insert(0, os.path.join(ROOT, "scripts"))   # model_benchmark's own imports

_spec = importlib.util.spec_from_file_location(
    "_bm", os.path.join(ROOT, "scripts", "benchmark_fast_raw_vs_clean.py"))
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

from model_benchmark import SERVICE_RISK_PERIOD, service_metrics

DB_PATH = "ustore.db"
FOLDS_CSV = "data/fastmoving_benchmark_folds.csv"
OUT_RESULTS = "data/fastmoving_benchmark_results.csv"
OUT_SUMMARY = "data/fastmoving_benchmark_summary.csv"

# model_benchmark_results.csv's column order, so a reader written for one
# file works on the other.
RESULT_COLS = [
    "stage", "sku", "method", "fold", "origin", "n_train", "horizon",
    "actual_30d", "pred_30d", "abs_error", "naive_scale", "safety_stock",
    "stock_level", "units_served", "units_short", "units_held", "item_name",
    "fsn_class", "in_fast_set",
]
SUMMARY_COLS = [
    "stage", "method", "mae", "rmse", "mase", "mase_n_skus", "n_skus",
    "n_folds", "pct_skus_beating_naive", "fill_rate_at_target",
    "units_short", "units_held", "n_skus_priced", "n_skus_priced_at_last_sale",
]


def summarise(results):
    """model_benchmark.py's summarise(), per stage.

    MASE is computed per SKU first and then averaged, so one high-volume
    SKU cannot dominate a scale-free metric - same as the original.
    """
    rows = []
    for (stage, method, sku), g in results.groupby(["stage", "method", "sku"],
                                                   sort=False):
        a = g["actual_30d"].to_numpy(dtype=float)
        p = g["pred_30d"].to_numpy(dtype=float)
        denom = np.nanmean(g["naive_scale"].to_numpy(dtype=float))
        m = float(np.mean(np.abs(a - p)))
        rows.append({
            "stage": stage, "method": method, "sku": sku,
            "mae": m,
            "rmse": float(np.sqrt(np.mean((a - p) ** 2))),
            "mase": m / denom if denom and np.isfinite(denom) and denom > 0 else np.nan,
            "n_folds": len(g),
        })
    ps = pd.DataFrame(rows)
    return (ps.groupby(["stage", "method"])
              .agg(mae=("mae", "mean"), rmse=("rmse", "mean"),
                   mase=("mase", "mean"), mase_n_skus=("mase", "count"),
                   n_skus=("sku", "nunique"), n_folds=("n_folds", "sum"))
              .reset_index()), ps


def beats_naive(ps):
    """Share of SKUs where the method's MAE beats naive's, per stage."""
    out = {}
    for stage, g in ps.groupby("stage"):
        wide = g.pivot_table(index="sku", columns="method", values="mae")
        if "naive" not in wide:
            continue
        base = wide["naive"]
        for method in wide.columns:
            out[(stage, method)] = (
                np.nan if method == "naive"
                else round(100.0 * float((wide[method] < base).mean()), 1))
    return out


def last_sale_index(series):
    """One past the last calendar day on which ANY SKU in `series` sold.

    The daily index runs to 2026-07-31 but the last recorded sale
    anywhere in the catalogue is 2026-07-08, so the series carry 23
    trailing all-zero days. That tail is a property of when the tally
    sheets stop, not of demand - see `count_priced` for why it has to be
    measured from separately.
    """
    if not series:
        return 0
    total = np.sum(np.vstack([np.asarray(v, dtype=float) for v in series.values()]),
                   axis=0)
    nz = np.nonzero(total)[0]
    return int(nz[-1]) + 1 if nz.size else len(total)


def count_priced(series_by_stage, fast_by_stage, quick=False):
    """n_skus_priced: SKUs given a positive 30-day forecast, i.e. the SKUs
    step5_prescriptive.py could actually compute an EOQ and an order for.
    A method that forecasts zero yields no annual demand and nothing the
    store can order, whatever its error metric says.

    Counted at TWO anchors, and the difference between them matters:

      n_skus_priced                  forecasting from the end of the
                                     calendar, which is what
                                     model_benchmark.py's skus_priced()
                                     does - kept identical so the two
                                     summary files mean the same thing.
      n_skus_priced_at_last_sale     forecasting from the last day any
                                     SKU actually sold.

    The catalogue's last 23 calendar days are all-zero everywhere, so at
    the first anchor EVERY method with a window at or under 23 days sees
    nothing but zeros and prices 0 SKUs - `naive`, `seasonal_naive_7`,
    `RM3_3day`, `RM6_6day`, `rolling_mean_14`, `rolling_q75_30`. That
    zero is an artefact of when the tally sheets stop, and it is NOT the
    same thing as `rolling_median_30`'s zero in
    docs/DEGENERATE_FORECAST.md, which is structural (a median over a
    window that is majority zeros is zero, wherever you stand). Reporting
    only the first column would present the two as the same failure. The
    second column separates them: an artefact recovers there, a
    structural zero does not.

    The pooled learners cannot go through the per-SKU path (they are
    fitted across SKUs by construction), so they are fitted once on the
    Fast set and predicted for each SKU - same definition, applied the
    way that model actually works.
    """
    con = sqlite3.connect(DB_PATH)
    index = bm.load_clean(con)[2]
    calendar = bm.load_calendar(con, index) if bm.load_calendar else None
    con.close()

    methods, _ = bm.build_methods(quick=False, with_ml=True)
    if calendar is not None and not quick:
        bm.add_prophet_methods(methods, index, calendar)
    pooled = bm.pooled_factories()

    full, at_last = {}, {}
    for stage, series_all in series_by_stage.items():
        series = {k: series_all[k] for k in fast_by_stage[stage]}
        cut = last_sale_index(series)
        print(f"  [{stage}] {len(series)} Fast SKUs; last sale at index {cut - 1} "
              f"of {len(index) - 1} ({len(index) - cut} trailing all-zero days)")

        for name, fn in methods.items():
            a = sum(1 for v in series.values()
                    if float(np.sum(fn(np.asarray(v, dtype=float), bm.HORIZON))) > 0)
            b = sum(1 for v in series.values()
                    if float(np.sum(fn(np.asarray(v[:cut], dtype=float), bm.HORIZON))) > 0)
            full[(stage, name)], at_last[(stage, name)] = a, b
            tag = "  <- zero is a calendar-tail artefact" if a == 0 and b > 0 else ""
            print(f"    [{stage}] {name:22s} {a:3d} / {b:3d} of {len(series)}{tag}")

        keys = list(series)
        for anchor, store in ((len(index), full), (cut, at_last)):
            Xs, ys = [], []
            for v in series.values():
                X, y = bm.build_training_set(v[:anchor], bm.HORIZON, bm.MIN_HISTORY)
                if X.shape[0]:
                    Xs.append(X)
                    ys.append(y)
            for name, make_model in pooled.items():
                if not Xs:
                    store[(stage, name)] = 0
                    continue
                model = make_model()
                model.fit(np.vstack(Xs), np.concatenate(ys))
                Xp = np.array([bm.make_features(series[k][:anchor], anchor)
                               for k in keys])
                store[(stage, name)] = int((np.maximum(model.predict(Xp), 0.0) > 0).sum())
        for name in pooled:
            print(f"    [{stage}] {name:22s} {full[(stage, name)]:3d} / "
                  f"{at_last[(stage, name)]:3d} of {len(series)}")
    return full, at_last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-priced", action="store_true",
                    help="skip n_skus_priced (the only column that refits models)")
    ap.add_argument("--folds", default=FOLDS_CSV)
    args = ap.parse_args()

    folds = pd.read_csv(args.folds)
    print(f"Read {len(folds):,} scored predictions from {args.folds} "
          f"({folds['method'].nunique()} methods, {folds['stage'].nunique()} stages)")

    # ---- rebuild the series + labels both stages need ----------------
    con = sqlite3.connect(DB_PATH)
    clean_series, clean_meta, index = bm.load_clean(con)
    con.close()
    raw_series, _, _ = bm.load_raw(index)

    clean_fsn, _ = bm.classify_fsn(clean_series)
    raw_fsn, _ = bm.classify_fsn(raw_series)

    # The clean side keys on integer product_id and the raw side on the
    # item-name string, so a CSV round-trip reads the merged column back
    # as text. Everything is normalised to str here rather than guessing
    # the dtype back - one key space, no silent 29 != "29" misses.
    folds["sku"] = folds["sku"].astype(str)
    name_of = {str(k): v for k, v in zip(clean_meta["product_id"],
                                         clean_meta["item_name"])}
    pipeline_fsn = {str(k): v for k, v in zip(clean_meta["product_id"],
                                              clean_meta["fsn_pipeline"])}
    raw_fsn_map = {str(k): v for k, v in zip(raw_fsn["key"], raw_fsn["fsn"])}
    clean_series = {str(k): v for k, v in clean_series.items()}
    raw_series = {str(k): v for k, v in raw_series.items()}

    series_by_stage = {"CLEAN": clean_series, "RAW": raw_series}
    fast_by_stage = {
        # the pipeline's own Fast set - what the repo actually forecasts
        "CLEAN": [str(p) for p in clean_meta.loc[clean_meta["fsn_pipeline"] == "F",
                                                 "product_id"] if str(p) in clean_series],
        # the recomputed ADUS rule - the only one raw data can support
        "RAW": [str(k) for k in raw_fsn.loc[raw_fsn["fsn"] == "F", "key"]],
    }
    fsn_by_stage = {"CLEAN": pipeline_fsn, "RAW": raw_fsn_map}
    print(f"Fast sets: CLEAN {len(fast_by_stage['CLEAN'])} SKUs "
          f"(Dim_Product.fsn_class), RAW {len(fast_by_stage['RAW'])} SKUs "
          f"(recomputed ADUS rule)")

    # ---- decision-metric columns, per stage --------------------------
    # sku ids are ints on the clean side and item-name strings on the raw
    # side, so the two stages cannot share one service_metrics() call -
    # the sigma cache would collide on the key.
    parts = []
    for stage, g in folds.groupby("stage"):
        scored = service_metrics(g.drop(columns=["stage"]),
                                 series_by_stage[stage], fsn_by_stage[stage])
        scored["stage"] = stage
        parts.append(scored)
    results = pd.concat(parts, ignore_index=True)

    results["item_name"] = np.where(
        results["stage"] == "CLEAN",
        results["sku"].map(name_of),
        results["sku"])
    results["fsn_class"] = [fsn_by_stage[s].get(k) for s, k
                            in zip(results["stage"], results["sku"])]
    fast_pairs = {(s, k) for s, ks in fast_by_stage.items() for k in ks}
    results["in_fast_set"] = [(s, k) in fast_pairs for s, k
                              in zip(results["stage"], results["sku"])]

    results = results[RESULT_COLS].sort_values(
        ["stage", "sku", "method", "fold"], kind="stable")
    results.to_csv(OUT_RESULTS, index=False, lineterminator="\n")
    print(f"Wrote {OUT_RESULTS} ({len(results):,} rows x {len(RESULT_COLS)} cols)")

    # ---- summary, over the Fast set only -----------------------------
    fast = results[results["in_fast_set"]].copy()
    summary, ps = summarise(fast)
    summary["pct_skus_beating_naive"] = [
        beats_naive(ps).get((s, m)) for s, m in zip(summary["stage"], summary["method"])]

    svc = (fast.groupby(["stage", "method"])
               .agg(units_served=("units_served", "sum"),
                    units_short=("units_short", "sum"),
                    units_held=("units_held", "sum"),
                    demand=("actual_30d", "sum"))
               .reset_index())
    svc["fill_rate_at_target"] = (svc["units_served"] / svc["demand"]).round(4)
    summary = summary.merge(
        svc[["stage", "method", "fill_rate_at_target", "units_short", "units_held"]],
        on=["stage", "method"], how="left")

    if args.no_priced:
        summary["n_skus_priced"] = np.nan
        summary["n_skus_priced_at_last_sale"] = np.nan
    else:
        print("\nCounting n_skus_priced (refits each method on full history) ...")
        t0 = time.time()
        priced, priced_at_last = count_priced(series_by_stage, fast_by_stage)
        keys = list(zip(summary["stage"], summary["method"]))
        summary["n_skus_priced"] = [priced.get(k) for k in keys]
        summary["n_skus_priced_at_last_sale"] = [priced_at_last.get(k) for k in keys]
        print(f"  done in {time.time() - t0:.0f}s")

    summary = (summary[SUMMARY_COLS]
               .sort_values(["stage", "mase", "mae"], na_position="last",
                            kind="stable")
               .reset_index(drop=True))
    summary.to_csv(OUT_SUMMARY, index=False, lineterminator="\n")
    print(f"Wrote {OUT_SUMMARY} ({len(summary)} rows x {len(SUMMARY_COLS)} cols)")

    for stage in ("CLEAN", "RAW"):
        s = summary[summary["stage"] == stage]
        print("\n" + "=" * 100)
        print(f"{stage}  (risk period {SERVICE_RISK_PERIOD}d = review 30 + lead 7, "
              f"z by FSN class) - ordered by MASE")
        print("=" * 100)
        print(s.drop(columns=["stage"]).to_string(
            index=False, float_format=lambda x: f"{x:.4f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
