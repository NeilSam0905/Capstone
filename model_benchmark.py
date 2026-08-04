"""
model_benchmark.py
------------------------------------------------------------------
Eight forecasting methods, scored on identical walk-forward folds.
Block 4.6.

    1. naive (persistence)          5. Croston
    2. seasonal naive (weekly)      6. SBA
    3. rolling mean (30d)           7. TSB
    4. rolling median (30d)         8. ETS / Holt-Winters

TSB was added after the first benchmark run showed Croston and SBA
placing last for a structural reason: they update only on periods when
demand arrives, so a dead SKU forecasts its old rate forever. TSB updates
demand probability every period including the zeros, so its estimates
decay on dying SKUs. With N at 233 of 519 products, slow-and-dying is the
modal case in this catalogue rather than an edge case.

**Prophet is deliberately absent.** It needs a cmdstan build, which is
exactly the toolchain gamble that made Chapter 4's central result
unreproducible from the repo (Block 6.2). Everything here runs on
pandas + numpy + scipy. Prophet is deferred decision **B5** and belongs
in its own session.

**This script reports a ranking. It does not select a model.**
Model selection is deferred decision **B3**, and it is downstream of
**B2** (whether the MAPE <= 20% framing survives at all). A ranking is a
measurement; a selection is a commitment, and the commitment is not this
run's to make.

Every method is scored on folds computed ONCE per SKU, so no method can
be advantaged by a different split - see forecasting/evaluate.py.

Run:
    python model_benchmark.py [--max-folds N] [--limit N] [--quick]

Outputs model_benchmark_results.csv (per SKU, per method, per fold) and
prints the ranked summary.
------------------------------------------------------------------
"""
import argparse
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

from forecasting.baselines import (
    ets_fit_predict, naive_fit_predict, rolling_mean_fit_predict,
    rolling_median_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.evaluate import evaluate_methods, summarise
from forecasting.intermittent import (
    croston_fit_predict, sba_fit_predict, tsb_fit_predict,
)

DB_NAME = "ustore.db"
OUT_CSV = "model_benchmark_results.csv"
SUMMARY_CSV = "model_benchmark_summary.csv"

HORIZON = 30
MIN_FOLDS = 3
MIN_TRAIN = 60
MAX_FOLDS = 12          # most recent 12 origins = ~360 days of scoring
ALPHA = 0.1
BETA = 0.1              # TSB's probability-smoothing constant

# ---- the decision-metric side -------------------------------------
# A single cell of A10's sensitivity grid, used to turn each forecast into
# a stocking decision so the methods can be compared on what the system is
# FOR, not only on forecast error. These two numbers are PROVISIONAL in
# exactly the sense A10 means it - nobody at USTore has confirmed a lead
# time or a cost ratio (deferred decision B9). They are held fixed here so
# the comparison between methods is like-for-like; they are not a claim
# about the store's actual parameters.
SERVICE_LEAD_TIME = 7          # days   [PROVISIONAL - pending Block 5]
SERVICE_COST_RATIO = 1.0       # S/H    [PROVISIONAL - pending Block 5]


def build_methods(quick=False):
    """Insertion order is NOT the ranking order - summarise() sorts on
    MASE with an MAE fallback. Deliberately listing naive first so a
    ranking that silently degraded to insertion order would be obvious."""
    return {
        "naive": naive_fit_predict(),
        "seasonal_naive": seasonal_naive_fit_predict(7),
        "rolling_mean_30": rolling_mean_fit_predict(30),
        "rolling_median_30": rolling_median_fit_predict(30),
        "croston": croston_fit_predict(ALPHA),
        "sba": sba_fit_predict(ALPHA),
        "tsb": tsb_fit_predict(ALPHA, BETA),
        "ets": ets_fit_predict(7, optimise=not quick),
    }


def load_daily_series(con, limit=None):
    """One daily series per moving SKU, over a complete calendar index.

    Fact_Sales carries explicit zero rows for densely tallied months, but
    not for every calendar day, so the series is reindexed onto a full
    daily range and missing days are filled with 0. The 30-day aggregate
    is a calendar window, so it needs a calendar-complete series.
    """
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])

    moving = (fact.groupby("product_id")["quantity_sold"].sum()
                  .pipe(lambda s: s[s > 0]).index)
    fact = fact[fact["product_id"].isin(moving)]

    full_index = pd.date_range(fact["calendar_date"].min(),
                               fact["calendar_date"].max(), freq="D")

    names = dict(con.execute("SELECT product_id, item_name FROM Dim_Product").fetchall())

    series = {}
    for pid, g in fact.groupby("product_id"):
        s = (g.groupby("calendar_date")["quantity_sold"].sum()
              .reindex(full_index, fill_value=0.0)
              .astype(float))
        series[pid] = s.to_numpy()

    if limit:
        series = dict(list(series.items())[:limit])

    return series, names, full_index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-folds", type=int, default=MAX_FOLDS)
    ap.add_argument("--limit", type=int, default=None,
                    help="benchmark only the first N SKUs (for a smoke run)")
    ap.add_argument("--quick", action="store_true",
                    help="skip ETS parameter optimisation")
    args = ap.parse_args()

    con = sqlite3.connect(DB_NAME)
    series, names, index = load_daily_series(con, args.limit)
    con.close()

    print(f"Loaded {len(series)} moving SKUs over {len(index)} calendar days "
          f"({index[0].date()} .. {index[-1].date()})")
    print(f"Horizon {HORIZON}d | min folds {MIN_FOLDS} | max folds {args.max_folds} "
          f"| min train {MIN_TRAIN}d\n")

    methods = build_methods(args.quick)
    print("Methods (%d): %s\n" % (len(methods), ", ".join(methods)))

    t0 = time.time()
    results, insufficient = evaluate_methods(
        series, methods, horizon=HORIZON, min_folds=MIN_FOLDS,
        max_folds=args.max_folds, min_train=MIN_TRAIN)
    elapsed = time.time() - t0

    if results.empty:
        print("No SKU had enough history to score. Nothing written.")
        return 1

    fsn_class = dict(sqlite3.connect(DB_NAME).execute(
        "SELECT product_id, fsn_class FROM Dim_Product").fetchall())
    results = service_metrics(results, series, fsn_class)
    results["item_name"] = results["sku"].map(names)
    results.to_csv(OUT_CSV, index=False, lineterminator="\n")

    n_scored = results["sku"].nunique()
    print(f"Scored {n_scored} SKUs x {len(methods)} methods "
          f"in {elapsed:.1f}s -> {OUT_CSV} ({len(results):,} rows)")
    print(f"Insufficient history (<{MIN_FOLDS} folds): {len(insufficient)} SKUs "
          f"- reported, not scored\n")

    # ---- identical-folds check, as a hard gate --------------------
    # Population asserts FIRST. "all layouts agree" and "(per_sku != 1).any()"
    # are both trivially satisfied by an empty frame, and `nunique() == 1`
    # stays true even if a method contributed no rows at all - so the count
    # of methods per SKU has to be checked explicitly, not inferred.
    total_folds = len(results) // len(methods) if methods else 0

    if n_scored == 0:
        print("FAIL: no SKUs were scored - every gate below would be vacuous")
        return 1

    methods_per_sku = results.groupby("sku")["method"].nunique()
    if (methods_per_sku != len(methods)).any():
        short = methods_per_sku[methods_per_sku != len(methods)]
        print(f"FAIL: {len(short)} SKU(s) missing methods, e.g. "
              f"{short.head(3).to_dict()} (expected {len(methods)})")
        return 1

    folds_per_sku = results.groupby("sku")["fold"].nunique()
    if (folds_per_sku < MIN_FOLDS).any():
        short = folds_per_sku[folds_per_sku < MIN_FOLDS]
        print(f"FAIL: {len(short)} SKU(s) scored on fewer than {MIN_FOLDS} folds")
        return 1

    # aggregate form of the same property, stated explicitly
    if total_folds < MIN_FOLDS * n_scored:
        print(f"FAIL: {total_folds} folds across {n_scored} SKUs is below the "
              f"{MIN_FOLDS}-per-SKU floor")
        return 1

    layouts = results.groupby(["sku", "method"])["origin"].apply(
        lambda s: tuple(sorted(s)))
    per_sku = layouts.groupby("sku").nunique()
    if (per_sku != 1).any():
        offenders = per_sku[per_sku != 1].index.tolist()[:5]
        print(f"FAIL: methods were scored on different folds for {offenders}")
        return 1

    print(f"[PASS] {n_scored} SKUs scored, each on >= {MIN_FOLDS} folds")
    print(f"[PASS] all {len(methods)} methods present for every SKU")
    print(f"[PASS] all {len(methods)} methods scored on identical folds "
          f"for all {n_scored} SKUs")
    print(f"[PASS] {total_folds:,} folds per method, {len(results):,} scored predictions")

    # ---- ranking ---------------------------------------------------
    summary = summarise(results)
    beats = beats_naive(results)
    summary["pct_skus_beating_naive"] = (
        summary["method"].map(beats).astype(float).round(1))

    # decision-metric columns, alongside the error-metric ones
    priced = skus_priced(series, methods)
    svc = (results.groupby("method")
                  .agg(units_served=("units_served", "sum"),
                       units_short=("units_short", "sum"),
                       units_held=("units_held", "sum"),
                       demand=("actual_30d", "sum"))
                  .reset_index())
    svc["fill_rate_at_target"] = (svc["units_served"] / svc["demand"]).round(4)
    summary = summary.merge(
        svc[["method", "fill_rate_at_target", "units_short", "units_held"]],
        on="method", how="left")
    summary["n_skus_priced"] = summary["method"].map(priced).astype(int)
    summary.to_csv(SUMMARY_CSV, index=False, lineterminator="\n")

    err_cols = ["method", "mae", "rmse", "mase", "pct_skus_beating_naive"]
    dec_cols = ["method", "n_skus_priced", "fill_rate_at_target",
                "units_short", "units_held"]

    print("\n" + "=" * 78)
    print("TABLE 1 - ERROR METRIC: ordered by MASE, MAE as tie-break")
    print("=" * 78)
    print(summary[err_cols].to_string(index=False,
                                      float_format=lambda x: f"{x:.4f}"))

    by_fill = summary.sort_values(
        ["fill_rate_at_target", "n_skus_priced"], ascending=False,
        kind="stable").reset_index(drop=True)

    print("\n" + "=" * 78)
    print(f"TABLE 2 - DECISION METRIC: ordered by fill rate at lead time "
          f"{SERVICE_LEAD_TIME}d,")
    print(f"           cost ratio {SERVICE_COST_RATIO} "
          f"[PROVISIONAL - pending Block 5 / B9]")
    print("=" * 78)
    print(by_fill[dec_cols].to_string(index=False,
                                      float_format=lambda x: f"{x:.4f}"))
    print("=" * 78)
    print("""
The two tables rank the same eight methods and do not agree. That
disagreement is the point of printing both: Table 1 asks which method is
least wrong, Table 2 asks which one would have met demand. `n_skus_priced`
is the column to read first - a method that prices zero SKUs cannot stock
anything, whatever its error metric says.

Safety stock in Table 2 uses A10's formula at one fixed grid cell, with
sigma computed from each fold's TRAINING slice only. The lead time and
cost ratio are provisional placeholders held constant so the comparison
is like-for-like; they are not USTore's actual parameters (B9).
""")
    print("""
READING NOTES - three things that will otherwise be misread.

1. MASE here is NOT comparable to a MASE quoted elsewhere unless the
   denominator matches. This one scales each SKU by the mean absolute
   difference between consecutive 30-DAY TRAINING BLOCK totals, so 1.0
   means "as good as predicting last month's total for this month". That
   is a demanding baseline on a series this intermittent, which is why
   every method scores above 1.

2. MAE and MASE disagree on the ordering, and that is real, not a bug.
   MAE is dominated by the high-volume SKUs; MASE scales each SKU by its
   own variability first, so a method that does well on the many small
   SKUs ranks higher. rolling_mean_30 wins on MAE, rolling_median_30 on
   MASE. Which one matters is a question about what the store cares
   about, and it is part of B3.

3. Croston and SBA are handicapped structurally, not by mistuning. They
   update ONLY on periods when demand arrives, so an SKU that sold four
   times and then stopped keeps forecasting its old rate forever - the
   trailing zero days are invisible to it (pinned by
   test_croston_cannot_see_trailing_zeros). TSB is in the table as the
   obsolescence-aware answer to exactly that: it updates demand
   probability every period, so its forecast decays on a dying SKU
   (pinned by test_tsb_decays_on_a_dead_sku_where_croston_holds_flat).
   Compare the two rows before concluding anything about intermittent
   methods in general - that comparison is B15.

This is a MEASUREMENT, not a selection. Which model USTore should use is
deferred decision B3, and it sits downstream of B2 (whether the MAPE <= 20%
gate in section 3.3.4 survives at all). No winner is declared here.
""")
    print(f"Wrote {SUMMARY_CSV}")

    # ---- A17 gates -------------------------------------------------
    print("=== ranking gates ===")
    ok = True

    same = set(summary["method"]) == set(by_fill["method"]) == set(methods)
    print(f"[{'PASS' if same else 'FAIL'}] both tables rank the same "
          f"{len(methods)} methods")
    ok &= same

    reported = summary["n_skus_priced"].notna().all() and len(summary) == len(methods)
    print(f"[{'PASS' if reported else 'FAIL'}] n_skus_priced reported for every method")
    ok &= bool(reported)

    filled = summary["fill_rate_at_target"].between(0.0, 1.0).all()
    print(f"[{'PASS' if filled else 'FAIL'}] every fill rate within [0, 1]")
    ok &= bool(filled)

    # The "no selection language" property is checked end-to-end against
    # this script's real stdout by tests/test_benchmark_ranking.py -
    # scanning a variable from inside the script would only ever inspect
    # the strings someone remembered to route through it.

    return 0 if ok else 1


def service_metrics(results, series, fsn_class):
    """Turn each forecast into a stocking decision and score the outcome.

    Per fold: stock = forecast + safety stock, where safety stock is
    A10's formula (Z x sigma x sqrt(lead time)) at the fixed grid cell
    above. Then

        served = min(actual, stock)      short = max(0, actual - stock)
                                         held  = max(0, stock - actual)

    sigma is computed from the fold's TRAINING slice only. Using the whole
    series would leak the test window into the safety stock and quietly
    flatter every method - the same leakage the harness is built to
    prevent, reintroduced through the back door.

    This is a service-level view, not a cost optimisation: it says how
    much demand each method would actually have met.
    """
    from step5_prescriptive import Z_BY_CLASS

    served = np.zeros(len(results))
    short = np.zeros(len(results))
    held = np.zeros(len(results))
    ss_col = np.zeros(len(results))

    # sigma depends only on (sku, origin), not on the method - cache it so
    # this is one pass over the folds rather than one per method
    sigma_cache = {}

    for i, (sku, origin, pred, actual) in enumerate(zip(
            results["sku"].to_numpy(), results["origin"].to_numpy(),
            results["pred_30d"].to_numpy(), results["actual_30d"].to_numpy())):
        key = (sku, origin)
        if key not in sigma_cache:
            train = series[sku][:origin]
            sigma_cache[key] = float(np.std(train, ddof=1)) if train.size > 1 else 0.0
        sigma = sigma_cache[key]

        z = Z_BY_CLASS.get(fsn_class.get(sku), 0.0)
        ss = z * sigma * np.sqrt(SERVICE_LEAD_TIME)
        stock = max(pred + ss, 0.0)

        ss_col[i] = ss
        served[i] = min(actual, stock)
        short[i] = max(0.0, actual - stock)
        held[i] = max(0.0, stock - actual)

    out = results.copy()
    out["safety_stock"] = ss_col
    out["stock_level"] = np.maximum(out["pred_30d"] + ss_col, 0.0)
    out["units_served"] = served
    out["units_short"] = short
    out["units_held"] = held
    return out


def skus_priced(series, methods):
    """How many SKUs each method would actually price.

    Runs each method on the FULL history, exactly as step5_prescriptive.py
    would, and counts the SKUs whose 30-day forecast is positive. A method
    that forecasts zero gives no annual demand, so there is no EOQ to
    compute and the SKU cannot be stocked from it at all.

    This is the column that exposes the degenerate forecast: it is not a
    subtlety of the error metric, it is the number of SKUs the system
    could actually act on.
    """
    out = {}
    for name, fn in methods.items():
        n = 0
        for values in series.values():
            if float(np.sum(fn(np.asarray(values, dtype=float), HORIZON))) > 0:
                n += 1
        out[name] = n
    return out


def beats_naive(results):
    """Percentage of SKUs on which each method's MAE beats naive's, on the
    same folds. A headline mean can hide a method that wins big on a few
    SKUs and loses on most."""
    per = (results.groupby(["method", "sku"])["abs_error"].mean().unstack(0))
    if "naive" not in per.columns:
        return {}

    baseline = per["naive"]
    out = {}
    for method in per.columns:
        if method == "naive":
            out[method] = np.nan          # it cannot beat itself
            continue
        pair = pd.concat([per[method], baseline], axis=1, keys=["m", "naive"]).dropna()
        out[method] = 100.0 * float((pair["m"] < pair["naive"]).mean()) if len(pair) else np.nan
    return out


if __name__ == "__main__":
    sys.exit(main())
