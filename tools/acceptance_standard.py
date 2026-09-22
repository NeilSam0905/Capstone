"""
tools/acceptance_standard.py
------------------------------------------------------------------
The acceptance criterion that replaces `MAPE <= 20%`, as a runnable gate.

Section 3.3.4 set `MAPE <= 20%` and the system failed it. The adviser has
granted freedom to choose a replacement, and that freedom is the risk
rather than the relief: the obvious attack on any project that fails one
criterion and proposes another is *"you invented a criterion you pass."*

Two things answer that attack, and this file is built around both.

**1. The thresholds are set a priori.** Each one comes from what an
inventory system must do to be useful, not from what this system happens
to score. They were written down before the measurement was taken, and
they would read the same if the system were failing them. Nothing here is
tuned to the observed value - where a measured figure is quoted it is in
the OUTPUT, never in the threshold.

**2. Every condition can fail.** `tests/test_acceptance_standard.py`
feeds each one a fixture built to break it and requires the failure. A
criterion that cannot fail is not a criterion - the same rule
`tests/test_gates_can_fail.py` already enforces for the pipeline gates.

Why this is STRICTER than the criterion it replaces
----------------------------------------------------
Worth stating plainly, because "we changed the criterion" invites the
assumption that the bar was lowered:

  - MAPE was ONE number on ONE split. This is four conditions on four
    held-out windows.
  - MAPE's optimum was a forecast of ZERO (docs/DEGENERATE_FORECAST.md).
    Conditions 1 and 3 make that state FAIL: a system that forecasts
    nothing prices nothing, and is dominated by anything that stocks.
  - MAPE never compared against a baseline. Condition 2 does, against a
    policy the store could actually run with no system at all.
  - MAPE never asked whether its own inputs were observed. Condition 4
    does, and 16.9% of this calendar turns out to be assumption.

A note on what is NOT a criterion here
---------------------------------------
Units served per unit held is reported everywhere in this project and is
deliberately NOT a condition. It is degenerate in exactly the way MAPE
is: a policy that stocks one unit and sells it scores perfect efficiency
and serves nobody. Measured - the naive baseline beats this policy on
efficiency at every origin while serving far less demand. Service and
cost are judged TOGETHER (condition 3, as a dominance) or not at all.

Run (from the repo root):
    python scripts/step5_prescriptive.py        # produces Result_Prescriptive
    python scripts/validate_policy_holdout.py   # produces the holdout CSVs
    python tools/acceptance_standard.py
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd

DB_NAME = "ustore.db"
ORIGINS_CSV = "data/policy_holdout_origins.csv"
COMPARISON_CSV = "data/policy_holdout_comparison.csv"

# ---- the thresholds, set a priori ----------------------------------
# Each is justified by what the system must DO, not by what it scores.
# Changing one of these is changing the criterion, and should be argued in
# the write-up rather than done to make a run pass.

# 1. A stocking system is useless if it cannot price the items that carry the
#    demand. The catalogue has a long tail of dormant SKUs, so a share-of-SKUs
#    bar would be satisfied by pricing the tail and missing the trade; the bar
#    is therefore on DEMAND. 90% leaves room for a genuinely dead tail while
#    still requiring that essentially all real trade is covered.
#
#    Measured FORWARD, on demand that actually materialised in each scored
#    window - NOT on the trailing window. A first draft of this check used the
#    trailing window and scored a perfect 1.0000, which looked like a pass and
#    was a tautology: a SKU is flagged `insufficient_data` precisely BECAUSE
#    its trailing window is empty, so flagged SKUs contribute zero to trailing
#    demand by construction and the ratio can never be anything but 1. A
#    condition that cannot fail is not a condition. The forward form can fail,
#    and does.
MIN_DEMAND_COVERAGE = 0.90
# ...and every eligible SKU must carry an explicit state. Not 99%: a silent
# omission is the specific failure this whole contract exists to prevent, so
# the only defensible bar is all of them.
REQUIRE_FULL_STATE_COVERAGE = 1.0

# 2. A system that cannot beat what the store could do with no system is not
#    worth deploying. "Every origin", not "on average": a model that wins on
#    average and loses in some quarters is not one a store can rely on.
BEAT_NAIVE_AT_EVERY_ORIGIN = True

# 3. If a simpler policy delivers at least as much service for no more stock,
#    the complexity is unjustified. Judged as a Pareto comparison because
#    service and cost must move together - see the module docstring.
ALLOW_DOMINATION = False

# 4. No headline figure may rest on a window that is mostly assumption. 90%
#    observed means at most one day in ten is inferred rather than recorded.
MIN_OBSERVED_SHARE = 0.90


class Checker:
    def __init__(self):
        self.failures = []
        self.n = 0

    def expect(self, condition, label, actual, ok, detail=""):
        self.n += 1
        print("  [%s] %-58s %s" % ("PASS" if ok else "FAIL", label, actual))
        if detail:
            print("         %s" % detail)
        if not ok:
            self.failures.append(f"{condition}: {label}")
        return ok


def condition_1_actionability(con, c):
    """A usable recommendation where the demand is; an explicit state
    everywhere else; never a silent zero."""
    print("\nCONDITION 1 - ACTIONABILITY")
    print("  A stocking system must price the items that carry the trade, and")
    print("  must say so explicitly where it cannot.")

    eligible = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT p.product_id FROM Dim_Product p
            JOIN Fact_Sales f ON f.product_id = p.product_id
            WHERE p.fsn_class IN ('F','S')
            GROUP BY p.product_id HAVING SUM(f.quantity_sold) > 0)""").fetchone()[0]
    stated = con.execute(
        "SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive").fetchone()[0]

    # population assert first - every check below is vacuous on an empty table
    if not c.expect("1", "eligible population is non-empty", eligible, eligible > 0):
        return

    c.expect("1", "every eligible SKU carries a state",
             f"{stated}/{eligible}",
             stated == eligible >= 1 and
             stated / eligible >= REQUIRE_FULL_STATE_COVERAGE)
    c.expect("1", "flagged rows emitting a reorder point (silent zeros)",
             con.execute("SELECT COUNT(*) FROM Result_Prescriptive WHERE "
                         "rate_source='insufficient_data' AND reorder_point IS NOT NULL"
                         ).fetchone()[0] , con.execute(
                 "SELECT COUNT(*) FROM Result_Prescriptive WHERE "
                 "rate_source='insufficient_data' AND reorder_point IS NOT NULL"
             ).fetchone()[0] == 0)
    c.expect("1", "priced rows missing a reorder point",
             con.execute("SELECT COUNT(*) FROM Result_Prescriptive WHERE "
                         "rate_source!='insufficient_data' AND reorder_point IS NULL"
                         ).fetchone()[0], con.execute(
                 "SELECT COUNT(*) FROM Result_Prescriptive WHERE "
                 "rate_source!='insufficient_data' AND reorder_point IS NULL"
             ).fetchone()[0] == 0)


def condition_1b_forward_coverage(origins, c):
    """Of the demand that actually arrived, how much could the system price
    in advance? Measured forward, on windows the fitting never saw."""
    print("\n  Forward demand coverage - of demand that actually materialised,")
    print("  how much came from SKUs the system priced IN ADVANCE:")
    if not c.expect("1", "forward coverage is reported",
                    "forward_coverage" in origins.columns,
                    "forward_coverage" in origins.columns):
        return
    for _, r in origins.iterrows():
        ok = "OK" if r["forward_coverage"] >= MIN_DEMAND_COVERAGE else "BELOW BAR"
        print(f"         {r['origin']}  coverage {r['forward_coverage']:.4f}  "
              f"({r['flagged_demand']:,.0f} of {r['window_demand_all_skus']:,.0f} units "
              f"came from flagged SKUs)   {ok}")
    pooled = 1 - (origins["flagged_demand"].sum()
                  / origins["window_demand_all_skus"].sum())
    c.expect("1", f"pooled forward coverage >= {MIN_DEMAND_COVERAGE:.0%}",
             f"{pooled:.4f}", pooled >= MIN_DEMAND_COVERAGE,
             detail=("Dormant SKUs revive. The threshold was set a priori and is NOT "
                     "moved to fit this - see the module docstring."))


def condition_2_beats_no_model(origins, c):
    """Must outperform what the store could do with no system at all."""
    print("\nCONDITION 2 - BEATS THE NO-MODEL ALTERNATIVE")
    print("  The comparator is a naive stocking policy: commit what the last")
    print("  lead-time block actually sold. Not another setting of our own model.")

    if not c.expect("2", "rolling origins were scored", len(origins), len(origins) > 1):
        return
    if not c.expect("2", "naive baseline present in the evidence",
                    "naive_fill" in origins.columns, "naive_fill" in origins.columns):
        return

    wins = int((origins["fill_rate"] > origins["naive_fill"]).sum())
    for _, r in origins.iterrows():
        print(f"         {r['origin']}  policy {r['fill_rate']:.4f} vs naive "
              f"{r['naive_fill']:.4f}   {'OK' if r['fill_rate'] > r['naive_fill'] else 'LOSS'}")
    c.expect("2", "policy beats naive stocking at EVERY origin",
             f"{wins}/{len(origins)}",
             (wins == len(origins)) if BEAT_NAIVE_AT_EVERY_ORIGIN else wins > 0)


def condition_3_not_dominated(comparison, c):
    """No simpler policy delivers at least as much service for no more stock."""
    print("\nCONDITION 3 - NOT DOMINATED")
    print("  Service and cost judged together, as a Pareto comparison. Efficiency")
    print("  alone is degenerate: stocking almost nothing maximises it (the naive")
    print("  row below does exactly that) while serving far less demand.")

    if not c.expect("3", "comparison table is populated", len(comparison),
                    len(comparison) > 1):
        return
    ours = comparison.iloc[0]
    rivals = comparison.iloc[1:]
    dominators = rivals[(rivals["fill_rate"] >= ours["fill_rate"])
                        & (rivals["units_held"] <= ours["units_held"])]
    for _, r in rivals.iterrows():
        rel = ("DOMINATES US" if r["fill_rate"] >= ours["fill_rate"]
               and r["units_held"] <= ours["units_held"]
               else "we dominate" if r["fill_rate"] <= ours["fill_rate"]
               and r["units_held"] >= ours["units_held"] else "trade-off")
        print(f"         {r['scope']:<28} fill {r['fill_rate']:.4f}  "
              f"held {r['units_held']:>9,.1f}   {rel}")
    c.expect("3", "alternatives that dominate this policy", len(dominators),
             (len(dominators) == 0) if not ALLOW_DOMINATION else True)


def condition_4_evidence_integrity(origins, c):
    """No headline figure rests on a window that is mostly assumption."""
    print("\nCONDITION 4 - EVIDENCE INTEGRITY")
    print("  16.9% of this calendar carries no record of a sale OR a closure.")
    print("  Every figure must state the share of its window that is evidence.")

    if not c.expect("4", "observability is reported at all",
                    "fit_observed_share" in origins.columns,
                    "fit_observed_share" in origins.columns):
        return
    c.expect("4", "every origin reports its observability",
             int(origins["fit_observed_share"].notna().sum()),
             bool(origins["fit_observed_share"].notna().all()))

    below = origins[origins["fit_observed_share"] < MIN_OBSERVED_SHARE]
    for _, r in origins.iterrows():
        mark = "headline" if r["fit_observed_share"] >= MIN_OBSERVED_SHARE else "DISCLOSED"
        print(f"         {r['origin']}  observed {r['fit_observed_share']:.4f}   {mark}")
    c.expect("4", f"at least one headline origin >= {MIN_OBSERVED_SHARE:.0%} observed",
             len(origins) - len(below), (len(origins) - len(below)) >= 1)
    if len(below):
        print(f"         {len(below)} origin(s) below the bar are reported and labelled,")
        print(f"         not dropped - excluding an unfavourable window would be selection.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_NAME)
    ap.add_argument("--origins-csv", default=ORIGINS_CSV)
    ap.add_argument("--comparison-csv", default=COMPARISON_CSV)
    args = ap.parse_args()

    print("=" * 78)
    print("USTore acceptance standard - the criterion replacing MAPE <= 20%")
    print("=" * 78)
    print("Thresholds are set a priori, from what the system must DO. They were")
    print("written before the measurement and would read the same if it failed.")

    missing = [p for p in (args.db, args.origins_csv, args.comparison_csv)
               if not os.path.exists(p)]
    if missing:
        print(f"\nCannot judge - missing evidence: {', '.join(missing)}")
        print("Run scripts/step5_prescriptive.py and scripts/validate_policy_holdout.py first.")
        return 1

    con = sqlite3.connect(args.db)
    origins = pd.read_csv(args.origins_csv)
    comparison = pd.read_csv(args.comparison_csv)

    c = Checker()
    condition_1_actionability(con, c)
    condition_1b_forward_coverage(origins, c)
    condition_2_beats_no_model(origins, c)
    condition_3_not_dominated(comparison, c)
    condition_4_evidence_integrity(origins, c)
    con.close()

    print("\n" + "=" * 78)
    if c.failures:
        print(f"NOT ACCEPTED - {len(c.failures)} of {c.n} checks failed:")
        for f in c.failures:
            print(f"   {f}")
        print("=" * 78)
        return 1
    print(f"ACCEPTED - all {c.n} checks passed across 4 conditions.")
    print("Each condition is judged on data the fitting never saw, and each can fail")
    print("(tests/test_acceptance_standard.py proves it).")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
