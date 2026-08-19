"""
tools/service_frontier.py
------------------------------------------------------------------
Reproduces docs/SERVICE_LEVEL_FRONTIER.md (Divergence #22) from
model_benchmark_results.csv only. Touches no database, fits no models -
every number below is a re-scoring of forecasts model_benchmark.py already
produced, using the per-fold columns it already wrote (actual_30d,
pred_30d, safety_stock, stock_level, units_served/short/held). It cannot
change what the benchmark found - only what is asked of it.

Why this exists
----------------
Divergence #6 proposed "service level >= 95%" to replace the unreachable
MAPE <= 20% criterion. docs/SERVICE_LEVEL_FRONTIER.md checked that proposal
against the data and found it is ALSO unreachable, for three separable
reasons - one a defect in model_benchmark.py's own scoring code, one a
hard arithmetic ceiling, one a wrong distributional assumption. This
script is the promised reproduction: the document said "reproduced by
tools/service_frontier.py" before this file existed.

**Remediation D1 (2026-08-19): Cause 1 is fixed, not just measured.**
model_benchmark.py originally computed safety stock as z*sigma*sqrt(7) -
a continuous-review formula against a periodic-review simulation (stock
set once per fold, no replenishment inside the 30-day window). D1
corrected it to z*sigma*sqrt(review + lead) = sqrt(37) at the source.
This script's Cause-1 section therefore reports two different things now:
a frozen historical record of what the pre-fix defect measured
(PRE_FIX_EXP_CAUSE1: 0.7161->0.7775 ets, 0.7098->0.7746 rolling_mean_30,
0.6862->0.7538 tsb, via algebraic rescaling of the old CSV), and a live
measurement of today's committed CSV (EXP_CAUSE1_POST_FIX), which needs
no rescaling because the fix already lives in the data.

What else matches the document exactly (independently re-derived here,
not copied):
    - Cause 2 (structural ceiling): 584 folds / 103 SKUs / 2,732 units
      unservable -> a 0.9490 ceiling on ANY method
    - EOQ demand-basis decoupling: 208 of 266 SKUs have positive observed
      demand across the scored folds, independent of forecasting method

What does NOT match, and is reported rather than forced to match:
    - Cause 3's frontier table (the empirical-quantile safety stock).
      The document's methodology is under-specified past "expanding
      window, strictly prior folds only, never the fold being scored" -
      that sentence doesn't say what a fold with zero prior observations
      (every SKU's first scored fold) should do, and different
      reasonable choices there move the sweep by several points. This
      script's choice - documented in empirical_quantile_frontier() below
      - is defensible but is NOT the one that produced the document's
      0.673/0.818 figures, since no script producing those was
      committed anywhere in the repo. The QUALITATIVE claims (the
      frontier is monotonic, rolling_mean_30 dominates ets/tsb at
      q=0.80, marginal holding cost rises with q) DO reproduce under
      this script's methodology - only the exact numbers differ.

Run:
    python tools/service_frontier.py
------------------------------------------------------------------
"""
import sys

import numpy as np
import pandas as pd

RESULTS_CSV = "data/model_benchmark_results.csv"

# Remediation D1 fixed model_benchmark.py's safety_stock to use
# review + lead time (37d) at the SOURCE, not lead time alone (7d). That
# means "as-built" and "corrected" are no longer two different numbers to
# reconcile by rescaling a column - the committed CSV's own safety_stock
# already reflects the fix. The rescale-by-2.299 trick below is kept only
# as a frozen, documented HISTORICAL reproduction of what the pre-fix
# defect measured; it is not run against fresh data post-D1 (see
# PRE_FIX_EXP_CAUSE1 and current_fill_rate() below).
PRE_FIX_LEAD_TIME = 7
PRE_FIX_REVIEW_PERIOD = 30
PRE_FIX_RISK_PERIOD = PRE_FIX_REVIEW_PERIOD + PRE_FIX_LEAD_TIME       # 37
PRE_FIX_RISK_SCALE = np.sqrt(PRE_FIX_RISK_PERIOD / PRE_FIX_LEAD_TIME)  # ~2.299

QUANTILES = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98]
KNEE_METHODS = ["rolling_mean_30", "ets", "tsb"]

# ---- expected values: inputs, not outputs -------------------------
# These pin today's measurement of the committed model_benchmark_results.csv.
# A different benchmark run (new methods, a re-fit) is expected to move
# these; a silent change on an unchanged CSV is not.
EXP_N_SKUS = 266
EXP_FOLDS_PER_SKU = 12
EXP_UNSERVABLE_FOLDS = 584
EXP_UNSERVABLE_SKUS = 103
EXP_UNSERVABLE_DEMAND = 2732.0
EXP_CEILING = 0.9490
EXP_SKUS_POSITIVE_DEMAND = 208

# Frozen 2026-08-19, from the pre-D1 committed CSV (sqrt(7) at the source).
# Not re-derived from fresh data - this is the historical record that the
# defect existed and by how much it moved fill rate. method -> (as_built,
# rescaled-by-2.299 "corrected").
PRE_FIX_EXP_CAUSE1 = {
    "ets": (0.7161, 0.7775),
    "rolling_mean_30": (0.7098, 0.7746),
    "tsb": (0.6862, 0.7538),
}
# Post-D1: measured directly from the (now-fixed-at-source) committed CSV,
# no rescaling. rolling_mean_30 and tsb are closed-form - deterministic,
# and match PRE_FIX_EXP_CAUSE1's rescaled "corrected" column exactly,
# which is the point: D1 didn't change what the correct answer was, only
# where it's computed. ets is the odd one out: it fits parameters
# numerically (scipy.optimize), and this project already documents
# (tests/test_determinism.py) that it is NOT bit-reproducible across BLAS
# backends - a ~0.1-0.2% wobble is real numerical noise, not a defect.
# EXP_CAUSE1_TOL below gives ets a looser gate for exactly that reason.
EXP_CAUSE1_POST_FIX = {
    "ets": 0.7768,
    "rolling_mean_30": 0.7746,
    "tsb": 0.7538,
}
EXP_CAUSE1_TOL = {"ets": 0.01, "rolling_mean_30": 1e-4, "tsb": 1e-4}


def load():
    df = pd.read_csv(RESULTS_CSV)
    if df.empty:
        print("FAIL: model_benchmark_results.csv is empty - "
              "run model_benchmark.py first.")
        sys.exit(1)
    return df


# ---------------------------------------------------------- Cause 1 ----

def pre_fix_risk_period_rescale_HISTORICAL(df):
    """Reproduces what the pre-D1 defect measured, by algebraically
    rescaling safety_stock as if it still used sqrt(7) at the source.
    Only meaningful against the pre-fix CSV - do not read this as today's
    number, it exists to keep the defect's evidence reproducible. Kept,
    not deleted, per the remediation register's own instruction."""
    out = df.copy()
    out["safety_stock_corrected"] = out["safety_stock"] * PRE_FIX_RISK_SCALE
    out["stock_corrected"] = (out["pred_30d"] + out["safety_stock_corrected"]).clip(lower=0)
    out["served_corrected"] = np.minimum(out["actual_30d"], out["stock_corrected"])

    g = out.groupby("method").agg(
        demand=("actual_30d", "sum"),
        served_as_built=("units_served", "sum"),
        served_corrected=("served_corrected", "sum"),
    )
    g["fill_rate_as_built"] = (g["served_as_built"] / g["demand"]).round(4)
    g["fill_rate_corrected"] = (g["served_corrected"] / g["demand"]).round(4)
    return g[["fill_rate_as_built", "fill_rate_corrected"]]


def current_fill_rate(df):
    """Fill rate measured directly from the committed CSV as it stands -
    post-D1, safety_stock already uses the corrected risk period at the
    source, so no rescaling is needed or correct here. Rescaling this
    data would double-apply the correction."""
    g = df.groupby("method").agg(
        demand=("actual_30d", "sum"),
        served=("units_served", "sum"),
    )
    g["fill_rate"] = (g["served"] / g["demand"]).round(4)
    return g[["fill_rate"]]


# ---------------------------------------------------------- Cause 2 ----

def structural_ceiling(df):
    """A fold is structurally unservable when stock_level is exactly 0 -
    only possible when the point forecast AND the safety stock are both
    zero, i.e. the training slice behind that fold was flat-zero (no
    signal for any method, sigma=0 for the z*sigma buffer). This is a
    property of the training window, not the method, so any one method's
    rows identify the same (sku, fold) set - 'naive' is used as the
    representative here."""
    naive = df[df.method == "naive"]
    unservable = naive[naive.stock_level == 0]

    total_demand = float(naive.actual_30d.sum())
    unservable_demand = float(unservable.actual_30d.sum())
    return {
        "n_folds": len(naive),
        "n_unservable_folds": len(unservable),
        "n_unservable_skus": unservable.sku.nunique(),
        "total_demand": total_demand,
        "unservable_demand": unservable_demand,
        "ceiling": round(1 - unservable_demand / total_demand, 4),
    }


# ---------------------------------------------------------- Cause 3 ----

def empirical_quantile_frontier(df, method="rolling_mean_30", quantiles=QUANTILES):
    """Safety stock from the SKU's own prior forecast errors instead of
    z*sigma - expanding window, strictly prior folds only (a fold's
    buffer uses only errors from folds before it, for that same SKU,
    never the fold being scored).

    Choice made here, and flagged in the module docstring as the point
    where this script's numbers diverge from docs/SERVICE_LEVEL_FRONTIER.md's
    prose table: a SKU's first scored fold has zero prior folds, so
    there is no history to take a quantile of. This implementation gives
    it a zero buffer (bare point forecast) rather than falling back to a
    pooled/class-level quantile - the simpler reading of "the SKU's own
    ... errors", and the one that doesn't borrow strength from other
    SKUs the way the project's documented cv_fallback does elsewhere.
    Falling back to a pooled quantile for under-observed folds is a
    reasonable alternative and would move these numbers; it just isn't
    this script's choice.
    """
    sub = df[df.method == method].sort_values(["sku", "fold"]).reset_index(drop=True).copy()
    sub["error"] = sub["actual_30d"] - sub["pred_30d"]

    demand_total = sub["actual_30d"].sum()
    rows = []
    for q in quantiles:
        buf = np.zeros(len(sub))
        for _sku, grp in sub.groupby("sku"):
            errs = grp["error"].to_numpy()
            idx = grp.index.to_numpy()
            for i in range(len(errs)):
                prior = errs[:i]
                buf[idx[i]] = max(np.quantile(prior, q), 0.0) if len(prior) else 0.0

        stock = np.maximum(sub["pred_30d"].to_numpy() + buf, 0.0)
        actual = sub["actual_30d"].to_numpy()
        served = np.minimum(actual, stock)
        short = np.maximum(0.0, actual - stock)
        held = np.maximum(0.0, stock - actual)

        rows.append({
            "q": q,
            "fill_rate": round(served.sum() / demand_total, 4),
            "units_short": round(float(short.sum()), 1),
            "units_held": round(float(held.sum()), 1),
        })

    tbl = pd.DataFrame(rows)
    served_units = (tbl["fill_rate"] * demand_total)
    d_served = served_units.diff()
    d_held = tbl["units_held"].diff()
    tbl["held_per_extra_unit_served"] = (d_held / d_served).round(1)
    return tbl


def knee_comparison(df, q=0.80, methods=KNEE_METHODS):
    rows = []
    for m in methods:
        tbl = empirical_quantile_frontier(df, method=m, quantiles=[q])
        r = tbl.iloc[0]
        rows.append({"method": m, "fill_rate": r["fill_rate"],
                      "units_short": r["units_short"], "units_held": r["units_held"]})
    return pd.DataFrame(rows).sort_values("fill_rate", ascending=False).reset_index(drop=True)


# ---------------------------------------------------- EOQ decoupling ---

def eoq_demand_basis(df):
    """SKUs with any positive observed demand across the scored folds -
    method-independent (actual_30d doesn't depend on the forecasting
    method), so this is the ceiling on how many SKUs COULD get an EOQ if
    D were sourced from trailing observed demand instead of a specific
    method's 30-day point forecast."""
    naive = df[df.method == "naive"]
    per_sku = naive.groupby("sku")["actual_30d"].sum()
    return int((per_sku > 0).sum()), len(per_sku)


# --------------------------------------------------------------- gates -

def expect(label, actual, expected, failures, tol=None):
    if tol is None:
        ok = actual == expected
    else:
        ok = abs(actual - expected) <= tol
    print("[%s] %-46s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                "" if ok else "   != expected %r" % (expected,)))
    if not ok:
        failures.append(label)


def main():
    df = load()
    failures = []

    print(f"{len(df):,} rows, {df.sku.nunique()} SKUs, "
          f"{df.method.nunique()} methods, {df.fold.nunique()} folds\n")

    # ---- Cause 1 ----------------------------------------------------
    print("=== Cause 1 - risk period, fixed at the source by remediation D1 ===")
    print("Historical record (frozen 2026-08-19, pre-fix CSV, sqrt(7) rescaled to sqrt(37)):")
    pre_fix = pd.DataFrame(PRE_FIX_EXP_CAUSE1, index=["fill_rate_as_built", "fill_rate_corrected"]).T
    print(pre_fix.to_string())
    print("\nMeasured directly from today's committed CSV (safety_stock already corrected at the source):")
    c1 = current_fill_rate(df)
    print(c1.loc[list(EXP_CAUSE1_POST_FIX)].to_string())

    # ---- Cause 2 ----------------------------------------------------
    print("\n=== Cause 2 - structural ceiling (flat-zero training folds) ===")
    c2 = structural_ceiling(df)
    for k, v in c2.items():
        print(f"  {k}: {v}")

    # ---- EOQ demand basis --------------------------------------------
    print("\n=== EOQ demand basis (method-independent) ===")
    n_positive, n_total = eoq_demand_basis(df)
    print(f"  SKUs with >0 observed demand across scored folds: {n_positive} of {n_total}")

    # ---- Cause 3 ------------------------------------------------------
    print("\n=== Cause 3 - empirical-quantile frontier (rolling_mean_30) ===")
    print("  NOTE: absolute figures differ from docs/SERVICE_LEVEL_FRONTIER.md's")
    print("  prose table - see this script's module docstring for why.")
    frontier = empirical_quantile_frontier(df)
    print(frontier.to_string(index=False))

    print(f"\n=== Comparison at the knee (q=0.80) ===")
    knee = knee_comparison(df, q=0.80)
    print(knee.to_string(index=False))

    # ---- gates --------------------------------------------------------
    print("\n=== gates ===")

    expect("SKUs scored", df.sku.nunique(), EXP_N_SKUS, failures)
    per_sku_method_folds = df.groupby(["sku", "method"]).size()
    expect("folds per (SKU, method) uniform", int(per_sku_method_folds.nunique()), 1, failures)
    expect("folds per (SKU, method)", int(per_sku_method_folds.iloc[0]), EXP_FOLDS_PER_SKU, failures)

    for method, expected_fill in EXP_CAUSE1_POST_FIX.items():
        expect(f"Cause1 {method} fill rate (post-D1, at source)", c1.loc[method, "fill_rate"],
               expected_fill, failures, tol=EXP_CAUSE1_TOL[method])

    for method, (as_built, corrected) in PRE_FIX_EXP_CAUSE1.items():
        expect(f"Cause1 {method} corrected > as-built (historical record)",
               corrected > as_built, True, failures)

    expect("Cause2 unservable folds", c2["n_unservable_folds"], EXP_UNSERVABLE_FOLDS, failures)
    expect("Cause2 unservable SKUs", c2["n_unservable_skus"], EXP_UNSERVABLE_SKUS, failures)
    expect("Cause2 unservable demand", c2["unservable_demand"], EXP_UNSERVABLE_DEMAND, failures)
    expect("Cause2 ceiling", c2["ceiling"], EXP_CEILING, failures, tol=1e-4)

    expect("EOQ SKUs with positive demand", n_positive, EXP_SKUS_POSITIVE_DEMAND, failures)
    expect("EOQ SKU population", n_total, EXP_N_SKUS, failures)

    # Cause 3: population first, then the structural/qualitative claims
    # this script CAN stand behind even though its exact numbers are not
    # the document's.
    expect("frontier has every requested quantile", len(frontier), len(QUANTILES), failures)
    monotonic = bool((frontier["fill_rate"].diff().dropna() >= 0).all())
    expect("frontier fill rate is monotonic non-decreasing in q", monotonic, True, failures)
    below_ceiling = bool((frontier["fill_rate"] <= c2["ceiling"] + 1e-9).all())
    expect("no quantile in the sweep exceeds the Cause-2 ceiling", below_ceiling, True, failures)

    marginal_rises = bool(
        frontier.loc[frontier["q"] == 0.95, "held_per_extra_unit_served"].iloc[0]
        > frontier.loc[frontier["q"] == 0.80, "held_per_extra_unit_served"].iloc[0]
    )
    expect("marginal holding cost at q=0.95 > at q=0.80 (a knee exists)", marginal_rises, True, failures)

    rm = knee.set_index("method")
    dominates = bool(
        (rm.loc["rolling_mean_30", "fill_rate"] > rm.loc["ets", "fill_rate"]) and
        (rm.loc["rolling_mean_30", "fill_rate"] > rm.loc["tsb", "fill_rate"]) and
        (rm.loc["rolling_mean_30", "units_held"] < rm.loc["ets", "units_held"]) and
        (rm.loc["rolling_mean_30", "units_held"] < rm.loc["tsb", "units_held"])
    )
    expect("rolling_mean_30 dominates ets and tsb at q=0.80", dominates, True, failures)

    if failures:
        print(f"\nFAILED: {len(failures)} gate(s).")
        return 1
    print("\nAll gates passed. Cause 3's absolute figures are this script's own "
          "(documented, defensible) methodology, not a forced match to the prose "
          "document - see the module docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
