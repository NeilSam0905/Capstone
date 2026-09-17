"""
scripts/holdout_8020_benchmark.py
------------------------------------------------------------------
Section 3.3.4's OTHER validation protocol: a single 80/20 chronological
train-test split, fit once and never refitted.

Why this is a separate script from benchmark_fast_raw_vs_clean.py
----------------------------------------------------------------
Section 3.3.4 and Figure 3 both say "walk-forward validation" AND
"80/20 train-test split", which are two different protocols:

  walk-forward   12 rolling origins, the model refits at every origin,
                 each forecast reaches 30 days ahead. This is what
                 forecasting/evaluate.py implements and what every other
                 number in this repo is scored on.
  80/20 holdout  fit ONCE on the first 80% of the series, then forecast
                 the whole remaining 20% without ever seeing it. The
                 last block is 150 days past the last training day.

The second is strictly harder, and it is the one the phrase "80/20
train-test split" normally means. Neither is wrong; they answer
different questions. Walk-forward asks "how good is this model in
production, refitted monthly". The holdout asks "how far can one fit be
trusted before it goes stale". Running both is how you find out whether
a ranking is an artefact of refitting.

Protocol here
-------------
Per SKU: cut = floor(0.80 * n). Train = y[:cut]. The held-out tail is
scored as consecutive non-overlapping 30-day blocks - the same 30-day
aggregate unit every other result in this repo uses - so a holdout
number and a walk-forward number are directly comparable. The model is
called ONCE with horizon = 30 * n_blocks; nothing is refitted between
blocks.

MASE uses the same denominator definition as the walk-forward harness:
the mean absolute difference between consecutive 30-day blocks of the
TRAINING slice. So MASE 1.0 means the same thing in both tables.

Run (from the repo root):
    python scripts/holdout_8020_benchmark.py [--no-ml] [--no-prophet]
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

_spec = importlib.util.spec_from_file_location(
    "_bm", os.path.join(ROOT, "scripts", "benchmark_fast_raw_vs_clean.py"))
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

from forecasting.evaluate import aggregate_blocks
from forecasting.metrics import naive_scale

DB_PATH = "ustore.db"
OUT_RESULTS = "data/holdout8020_benchmark_results.csv"
OUT_SUMMARY = "data/holdout8020_benchmark_summary.csv"
TRAIN_FRACTION = 0.80
HORIZON = 30


def score_holdout(series, methods, train_fraction=TRAIN_FRACTION, horizon=HORIZON):
    """One fit per SKU per method; the held-out tail scored in 30-day blocks."""
    rows = []
    for sku, v in series.items():
        v = np.asarray(v, dtype=float).ravel()
        n = v.size
        cut = int(np.floor(train_fraction * n))
        n_blocks = (n - cut) // horizon
        if n_blocks < 1:
            continue

        train = v[:cut]
        blocks = aggregate_blocks(train, horizon)
        denom = naive_scale(blocks) if blocks.size > 1 else float("nan")
        actual = v[cut:cut + n_blocks * horizon].reshape(n_blocks, horizon).sum(axis=1)

        for name, fn in methods.items():
            pred = np.asarray(fn(train, n_blocks * horizon), dtype=float).ravel()
            if pred.size != n_blocks * horizon:
                raise ValueError(
                    f"{name} returned {pred.size} predictions for horizon "
                    f"{n_blocks * horizon} on SKU {sku!r}")
            pred_blocks = pred.reshape(n_blocks, horizon).sum(axis=1)

            for b in range(n_blocks):
                rows.append({
                    "sku": sku, "method": name, "block": b,
                    "days_ahead": (b + 1) * horizon,
                    "n_train": cut, "horizon": horizon,
                    "actual_30d": float(actual[b]),
                    "pred_30d": float(pred_blocks[b]),
                    "abs_error": abs(float(actual[b]) - float(pred_blocks[b])),
                    "naive_scale": denom,
                })
    return pd.DataFrame(rows)


def summarise(results, label):
    """Per-method metrics, per SKU first then averaged - same scheme as
    forecasting/evaluate.py::summarise so the two tables are comparable."""
    per = []
    for (method, sku), g in results.groupby(["method", "sku"], sort=False):
        a = g["actual_30d"].to_numpy(dtype=float)
        p = g["pred_30d"].to_numpy(dtype=float)
        err = np.abs(a - p)
        denom = np.nanmean(g["naive_scale"].to_numpy(dtype=float))
        m = float(err.mean())
        per.append({
            "method": method, "sku": sku, "mae": m,
            "rmse": float(np.sqrt(np.mean((a - p) ** 2))),
            "mase": m / denom if denom and np.isfinite(denom) and denom > 0 else np.nan,
            "abs_err_sum": float(err.sum()), "total_actual": float(a.sum()),
            "n_blocks": len(g),
        })
    ps = pd.DataFrame(per)

    out = (ps.groupby("method")
             .agg(mae=("mae", "mean"), rmse=("rmse", "mean"),
                  mase=("mase", "mean"), mase_median=("mase", "median"),
                  mase_n_skus=("mase", "count"), n_skus=("sku", "nunique"),
                  n_blocks=("n_blocks", "sum"))
             .reset_index())
    pooled = (ps.groupby("method")
                .apply(lambda g: 100.0 * g["abs_err_sum"].sum() / g["total_actual"].sum()
                       if g["total_actual"].sum() > 0 else np.nan,
                       include_groups=False)
                .rename("wmape_pooled_pct").reset_index())
    out = out.merge(pooled, on="method", how="left")

    wide = ps.pivot_table(index="sku", columns="method", values="mae")
    if "naive" in wide:
        base = wide["naive"]
        out["pct_skus_beating_naive"] = out["method"].map(
            {m: round(100.0 * float((wide[m] < base).mean()), 1)
             for m in wide.columns if m != "naive"})

    out.insert(0, "stage", label)
    return out.sort_values(["mase", "mae"], na_position="last",
                           kind="stable").reset_index(drop=True), ps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ml", action="store_true")
    ap.add_argument("--no-prophet", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    clean_series, clean_meta, index = bm.load_clean(con)
    calendar = (bm.load_calendar(con, index)
                if (bm.load_calendar and not args.no_prophet) else None)
    con.close()
    raw_series, _, _ = bm.load_raw(index)

    raw_fsn, _ = bm.classify_fsn(raw_series)
    fast = {
        "CLEAN": ([p for p in clean_meta.loc[clean_meta["fsn_pipeline"] == "F",
                                             "product_id"] if p in clean_series],
                  clean_series),
        "RAW": (raw_fsn.loc[raw_fsn["fsn"] == "F", "key"].tolist(), raw_series),
    }

    methods, missing = bm.build_methods(quick=False, with_ml=not args.no_ml)
    if calendar is not None:
        missing.update(bm.add_prophet_methods(methods, index, calendar))

    n = len(index)
    cut = int(np.floor(TRAIN_FRACTION * n))
    n_blocks = (n - cut) // HORIZON
    print("=" * 78)
    print("80/20 chronological holdout - fit ONCE, never refitted")
    print("=" * 78)
    print(f"Series length {n} days ({index[0].date()} .. {index[-1].date()})")
    print(f"Train: first {cut} days ({index[0].date()} .. {index[cut - 1].date()})")
    print(f"Test : last {n - cut} days, scored as {n_blocks} x 30-day blocks "
          f"({index[cut].date()} .. {index[cut + n_blocks * HORIZON - 1].date()})")
    print(f"Methods: {len(methods)}")
    if missing:
        print(f"Unavailable (skipped): {list(missing)}")

    all_res, summaries = [], []
    for stage, (keys, series_all) in fast.items():
        series = {k: series_all[k] for k in keys}
        t0 = time.time()
        res = score_holdout(series, methods)
        res["stage"] = stage
        print(f"\n[{stage}] {len(series)} Fast SKUs -> {len(res):,} scored blocks "
              f"in {time.time() - t0:.0f}s")
        all_res.append(res)
        s, _ = summarise(res, stage)
        summaries.append(s)

    results = pd.concat(all_res, ignore_index=True)
    results["item_name"] = np.where(
        results["stage"] == "CLEAN",
        results["sku"].map(dict(zip(clean_meta["product_id"], clean_meta["item_name"]))),
        results["sku"])
    results.to_csv(OUT_RESULTS, index=False, lineterminator="\n")
    summary = pd.concat(summaries, ignore_index=True)
    summary.to_csv(OUT_SUMMARY, index=False, lineterminator="\n")
    print(f"\nWrote {OUT_RESULTS} ({len(results):,} rows)")
    print(f"Wrote {OUT_SUMMARY} ({len(summary)} rows)")

    cols = ["method", "mae", "rmse", "mase", "mase_median", "wmape_pooled_pct",
            "pct_skus_beating_naive"]
    for stage in ("CLEAN", "RAW"):
        s = summary[summary["stage"] == stage]
        print("\n" + "=" * 90)
        print(f"{stage} - 80/20 holdout, Fast-moving, ordered by MASE")
        print("=" * 90)
        print(s[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    # ---- how much of the ranking is refitting? -----------------------
    wf = pd.read_csv("data/fastmoving_benchmark_summary.csv")
    wf = wf[wf["stage"] == "CLEAN"][["method", "mae", "rmse", "mase"]]
    ho = summary[summary["stage"] == "CLEAN"][["method", "mae", "rmse", "mase"]]
    cmp = wf.merge(ho, on="method", suffixes=("_walkforward", "_holdout"))
    for m in ("mae", "rmse", "mase"):
        cmp[f"pct_worse_{m}"] = (100.0 * (cmp[f"{m}_holdout"] - cmp[f"{m}_walkforward"])
                                 / cmp[f"{m}_walkforward"])
    cmp = cmp.sort_values("mase_holdout").reset_index(drop=True)
    cmp.to_csv("data/holdout8020_vs_walkforward.csv", index=False, lineterminator="\n")
    print("\n" + "=" * 90)
    print("CLEAN: 80/20 holdout vs 12-fold walk-forward (same SKUs, same metric defn)")
    print("=" * 90)
    print(cmp[["method", "mase_walkforward", "mase_holdout", "pct_worse_mase",
               "mae_walkforward", "mae_holdout", "pct_worse_mae"]]
          .to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\nWrote data/holdout8020_vs_walkforward.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
