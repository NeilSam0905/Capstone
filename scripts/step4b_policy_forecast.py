"""
scripts/step4b_policy_forecast.py
------------------------------------------------------------------
Publish the predictive stage the prescriptive layer actually consumes.

THE PROBLEM THIS CLOSES
-----------------------
The manuscript's progression is descriptive -> predictive -> prescriptive.
Measured stage by stage it was broken at both joints:

  descriptive    Fact_Sales, FSN, density        266 SKUs   consumed
  predictive     Result_Forecast (rolling mean)   58 SKUs,  **consumed by
                 of which 32 forecast a flat zero  26 usable   NOTHING**
  prescriptive   ROP / EOQ / tiers               266 scored, 208 priced
                                                  - it computed its own rate

`backend/pipeline.py` stated the break in a comment rather than hiding it:
*"step5a/step5, neither of which read Result_Forecast."* So the predictive
stage was not missing - it was BURIED. `forecasting.policy.resolve_rates`
produces a demand rate for 208 SKUs with an explicit `insufficient_data`
state for the rest, and `empirical_buffer` produces an uncertainty for each
of them. That is a forecast. It was simply computed inside the prescriptive
script and thrown away.

This step materialises it into `Result_Forecast` as `model_type='policy_rate'`,
and `step5_prescriptive.py` now READS it instead of recomputing. Predictive
coverage goes from 26 usable SKUs to **208 priced + 58 flagged = 266**, and
the chain becomes literal.

WHAT IT DOES NOT DO
-------------------
It does not replace step4. `rolling_mean_30`'s rows stay exactly where they
are, untouched, and the Demand Forecast screen still draws them. The two
model types live side by side because they are different objects answering
different questions - a 30-day point forecast for a chart, and a rate plus a
lead-time interval for a stocking decision - and collapsing them into one
row would lose the distinction this project spent a chapter establishing.

It does not change a number. `Result_Prescriptive` must come out identical
row-for-row whether step5 reads these rows or recomputes them; that is
verified rather than asserted (`--verify` on step5, and
`tests/test_policy_forecast.py`).

It does not re-derive anything step5 owns. The population comes from
`step5_prescriptive.eligible_population`, the rates from `resolve_rates`,
the tiers from `assign_service_tier` and the buffers from `empirical_buffer`
- the same committed callables, at the same settings, in the same order.
This script is a publisher, not a second opinion.

WHY IT RUNS AFTER step5a
------------------------
The interval is measured at each SKU's OWN lead-time horizon, not at 30
days, because the reorder point is continuous review - `rate*L + buffer` -
so the quantity at risk is demand over one lead time. Taking a 30-day
quantile and scaling it by sqrt(L/30) would reintroduce exactly the
distributional assumption the empirical buffer exists to remove. Lead times
are set by `step5a_set_lead_times.py`, so this step cannot run before it.

WHAT IS WRITTEN
---------------
Per PRICED SKU, one row per day across its lead time L:

    yhat          the demand rate, units/day - NOT rounded (step4 rounds to
                  3dp because its rows are a display artifact; these are a
                  pipeline input, and a rounded rate moves every ROP)
    yhat_lower    the rate - the policy's downside is the expectation, since
                  policy errors are floored at zero
    yhat_upper    rate + buffer/L, so SUM(yhat_upper) across the L rows is
                  rate*L + buffer - the reorder point, exactly
    buffer_units  the buffer over L, stored whole so step5 reads it back
                  with no arithmetic
    horizon_days  L
    rate_source / service_tier / buffer_quantile / buffer_source

Per FLAGGED SKU, one row, yhat NULL and `rate_source='insufficient_data'`:
the predictive stage saying it has nothing, in the table, rather than being
absent from it. A zero would read as "nothing will sell", which is a
forecast this pipeline has no evidence for - the same reason
`Result_Prescriptive` carries a flagged row instead of a zero reorder point.

Run (from the repo root):
    python scripts/step5a_set_lead_times.py      # lead times first
    python scripts/step4b_policy_forecast.py
    python scripts/step5_prescriptive.py
------------------------------------------------------------------
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecasting.policy import (
    DEFAULT_BUFFER_QUANTILE, DEFAULT_CLUSTER_K, DEFAULT_TIER_MAX_Q,
    DEFAULT_TIER_TARGET, DEFAULT_TIER_MIN_EFFICIENCY, INSUFFICIENT,
    NOT_STOCKABLE, PARTIAL, assign_service_tier, empirical_buffer,
    policy_fold_errors, resolve_rates, trailing_rate_fn,
)
from step5_prescriptive import (
    BUFFER_EMPIRICAL, BUFFER_NORMAL_FALLBACK, BUFFER_NOT_STOCKED, DAYS_PER_YEAR,
    DB_NAME, MIN_SALE_DAYS_FOR_RATE, POLICY_MODEL_TYPE, build_observed_mask,
    eligible_population, ensure_forecast_policy_columns, load_series,
)

INSERT_COLUMNS = (
    "product_id, forecast_date, yhat, yhat_lower, yhat_upper, model_type, "
    "is_heuristic, snapshot_date, horizon_days, buffer_units, rate_source, "
    "service_tier, buffer_quantile, buffer_source"
)


def build_rows(eligible, prod_ix, prices, observed, args, last_date, snapshot):
    """The policy's rate and interval per eligible SKU, ready to insert.

    Deliberately the same call sequence step5_prescriptive.main() used to run
    inline: resolve_rates -> assign_service_tier -> policy_fold_errors ->
    empirical_buffer, at the same defaults. Anything that diverges here
    diverges from the prescription.
    """
    rates = resolve_rates(eligible, prices,
                          window=int(DAYS_PER_YEAR),
                          min_sale_days=MIN_SALE_DAYS_FOR_RATE,
                          shrink=args.shrink,
                          k=args.cluster_k,
                          observed=observed)
    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed)

    priced, flagged, missing_lt = {}, {}, []
    for pid in eligible:
        if pd.isna(prod_ix.loc[pid, "lead_time_days"]):
            missing_lt.append(prod_ix.loc[pid, "item_name"])
            continue
        rec = rates[pid]
        lt = int(prod_ix.loc[pid, "lead_time_days"])
        if rec["rate_source"] == INSUFFICIENT or not rec["rate"] or rec["rate"] <= 0:
            flagged[pid] = lt
        else:
            priced[pid] = (float(rec["rate"]), lt, rec["rate_source"])

    if missing_lt:
        raise SystemExit(
            f"ABORTING: {len(missing_lt)} eligible SKU(s) have no lead_time_days - "
            f"run scripts/step5a_set_lead_times.py first: {missing_lt[:5]}")

    tiers = {}
    if not args.flat_quantile:
        for pid, (_, lt, _) in priced.items():
            tiers[pid] = assign_service_tier(
                eligible[pid], lt, rate_fn,
                target=args.tier_target,
                min_efficiency=args.tier_min_efficiency,
                max_q=DEFAULT_TIER_MAX_Q,
                default_q=args.buffer_quantile)

    rows, n_fallback = [], 0
    for pid, (rate, lt, rate_source) in priced.items():
        tier = tiers.get(pid, {}).get("tier", PARTIAL)
        tier_q = tiers.get(pid, {}).get("q", args.buffer_quantile)

        if tier == NOT_STOCKABLE:
            # Covers expected lead-time demand and buys no buffer: on its own
            # history every extra unit returns less than the efficiency floor.
            buffer, buffer_src, buffer_q = 0.0, BUFFER_NOT_STOCKED, None
        else:
            q = tier_q if tier_q is not None else args.buffer_quantile
            errors = policy_fold_errors(eligible[pid], lt, rate_fn)
            if errors.size:
                buffer, buffer_src = float(empirical_buffer(errors, q)), BUFFER_EMPIRICAL
            else:
                # Too little history to fold at this horizon. The normal buffer
                # is a function of population-wide sigma medians that step5
                # owns, so this row records the FALLBACK and carries no buffer;
                # step5 substitutes its own z*sigma and the provenance survives.
                buffer, buffer_src = None, BUFFER_NORMAL_FALLBACK
                n_fallback += 1
            buffer_q = q

        band = 0.0 if not buffer else buffer / lt
        for d in pd.date_range(last_date + pd.Timedelta(days=1), periods=lt, freq="D"):
            rows.append((int(pid), d.strftime("%Y-%m-%d"), rate, rate, rate + band,
                         POLICY_MODEL_TYPE, 0, snapshot, lt, buffer,
                         rate_source, tier, buffer_q, buffer_src))

    for pid, lt in flagged.items():
        # One row, no horizon: there is nothing to project across. The row
        # exists so the predictive stage reports the SKU as unknown rather
        # than being silent about it.
        rows.append((int(pid), (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                     None, None, None, POLICY_MODEL_TYPE, 0, snapshot, lt, None,
                     INSUFFICIENT, None, None, None))

    return rows, priced, flagged, tiers, n_fallback


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--db", default=DB_NAME)
    ap.add_argument("--buffer-quantile", type=float, default=DEFAULT_BUFFER_QUANTILE,
                    help="default operating point for SKUs with no scoreable history")
    ap.add_argument("--cluster-k", type=int, default=DEFAULT_CLUSTER_K)
    ap.add_argument("--shrink", action="store_true",
                    help="re-enable the cluster-pooled rate fallback (off by default: "
                         "dominated, see docs/PRESCRIPTIVE_CONTRACT.md)")
    ap.add_argument("--flat-quantile", action="store_true",
                    help="control: one population-wide operating point, no tiering")
    ap.add_argument("--tier-target", type=float, default=DEFAULT_TIER_TARGET)
    ap.add_argument("--tier-min-efficiency", type=float, default=DEFAULT_TIER_MIN_EFFICIENCY)
    ap.add_argument("--zero-fill", action="store_true",
                    help="control: full-window denominator rather than observed days")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Result_Forecast'").fetchone():
        print("Result_Forecast does not exist. Run scripts/step4_forecast_model.py "
              "first - it creates the table this step publishes into.")
        return 1
    added = ensure_forecast_policy_columns(con)
    if added:
        print(f"Migrated Result_Forecast: added {', '.join(added)}")

    series, products, idx = load_series(con)
    observed = None if args.zero_fill else build_observed_mask(con, idx)
    prod_ix, eligible, prices = eligible_population(products, series)

    print("=" * 74)
    print("STEP 4b - PUBLISH THE POLICY RATE AND INTERVAL")
    print("=" * 74)
    print(f"Calendar     : {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} days)")
    print(f"Eligible F+S : {len(eligible)} SKUs")
    if observed is not None:
        print(f"Evidence     : {int(observed.sum())} of {len(idx)} days observed "
              f"(rates divide by observed days)")
    else:
        print("Evidence     : --zero-fill (pre-correction denominator)")

    snapshot = dt.date.today().isoformat()
    rows, priced, flagged, tiers, n_fallback = build_rows(
        eligible, prod_ix, prices, observed, args, idx[-1], snapshot)

    # Replace only this step's own rows. step4's point forecast is a different
    # model_type and is left exactly where it is - the whole point is that the
    # two sit side by side rather than one overwriting the other.
    n_before = con.execute("SELECT COUNT(*) FROM Result_Forecast").fetchone()[0]
    n_point = con.execute("SELECT COUNT(*) FROM Result_Forecast WHERE model_type IS NOT ?",
                          (POLICY_MODEL_TYPE,)).fetchone()[0]
    con.execute("DELETE FROM Result_Forecast WHERE model_type = ?", (POLICY_MODEL_TYPE,))
    con.executemany(
        f"INSERT INTO Result_Forecast ({INSERT_COLUMNS}) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()

    n_after = con.execute("SELECT COUNT(*) FROM Result_Forecast").fetchone()[0]
    kept = con.execute("SELECT COUNT(*) FROM Result_Forecast WHERE model_type IS NOT ?",
                       (POLICY_MODEL_TYPE,)).fetchone()[0]

    print(f"\nPublished    : {len(rows):,} policy_rate rows "
          f"({len(priced)} priced x their lead time, {len(flagged)} flagged x 1)")
    print(f"Kept beside  : {kept:,} point-forecast rows, untouched "
          f"({'unchanged' if kept == n_point else 'CHANGED - investigate'})")
    print(f"Result_Forecast: {n_before:,} -> {n_after:,} rows")

    tier_counts = {}
    for t in tiers.values():
        tier_counts[t.get("tier")] = tier_counts.get(t.get("tier"), 0) + 1
    print(f"\nPredictive coverage: {len(priced)} SKUs carry a rate, "
          f"{len(flagged)} flagged insufficient_data, "
          f"{len(priced) + len(flagged)} of {len(eligible)} eligible accounted for")
    if tier_counts:
        print("Service tiers      : "
              + ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items()) if k))
    if n_fallback:
        print(f"Normal-buffer fallback: {n_fallback} SKU(s) had too little history to "
              f"fold at their lead-time horizon; step5 supplies z*sigma for those")

    # ---- gates ----
    print("\n=== gates ===")
    ok = True

    def expect(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"[{'PASS' if good else 'FAIL'}] {label:<52} {got}")

    expect("every eligible SKU is accounted for", len(priced) + len(flagged), len(eligible))
    expect("point-forecast rows untouched", kept, n_point)
    expect("flagged rows carrying a rate", con.execute(
        "SELECT COUNT(*) FROM Result_Forecast WHERE model_type = ? AND rate_source = ? "
        "AND yhat IS NOT NULL", (POLICY_MODEL_TYPE, INSUFFICIENT)).fetchone()[0], 0)
    expect("priced rows missing a rate", con.execute(
        "SELECT COUNT(*) FROM Result_Forecast WHERE model_type = ? AND rate_source IS NOT ? "
        "AND yhat IS NULL", (POLICY_MODEL_TYPE, INSUFFICIENT)).fetchone()[0], 0)
    expect("rows whose band is below their point", con.execute(
        "SELECT COUNT(*) FROM Result_Forecast WHERE model_type = ? AND yhat IS NOT NULL "
        "AND (yhat_upper < yhat OR yhat_lower > yhat)", (POLICY_MODEL_TYPE,)).fetchone()[0], 0)
    expect("priced SKUs whose row count is not their lead time", con.execute(
        "SELECT COUNT(*) FROM (SELECT product_id, COUNT(*) n, MAX(horizon_days) h "
        "FROM Result_Forecast WHERE model_type = ? AND rate_source IS NOT ? "
        "GROUP BY product_id) WHERE n <> h",
        (POLICY_MODEL_TYPE, INSUFFICIENT)).fetchone()[0], 0)

    # The band is not decoration: summed across the lead time it IS the
    # reorder point, so a reader can rebuild the prescription from this table.
    bad_band = con.execute(
        "SELECT COUNT(*) FROM (SELECT product_id, "
        "  SUM(yhat_upper) - (MIN(yhat) * MIN(horizon_days) + MIN(COALESCE(buffer_units,0))) d "
        "  FROM Result_Forecast WHERE model_type = ? AND rate_source IS NOT ? "
        "  GROUP BY product_id) WHERE ABS(d) > 1e-6",
        (POLICY_MODEL_TYPE, INSUFFICIENT)).fetchone()[0]
    expect("SKUs where SUM(upper band) is not the reorder point", bad_band, 0)

    con.close()
    print("\n" + ("All gates PASS." if ok else "GATES FAILED."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
