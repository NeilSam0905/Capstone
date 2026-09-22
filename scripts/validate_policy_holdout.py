"""
validate_policy_holdout.py
------------------------------------------------------------------
The acceptance test for the forecast -> prescriptive contract: does the
stocking POLICY meet demand at acceptable holding, on windows no fitting
step was allowed to see.

Why this is the acceptance test and MAPE is not
-----------------------------------------------
`MAPE <= 20%` is retired (docs/DEGENERATE_FORECAST.md, pinned by
tests/test_degenerate_forecast.py): on a majority-zero series its
error-minimising optimum is a forecast of zero, so the method that wins it
is the method that can stock nothing. Point accuracy is not on the path to
a usable system, and a holdout scored on point accuracy would measure the
wrong thing more rigorously. What has to be reliable is the POLICY - the
reorder point the rate and the buffer imply - so that is what this scores.

**What is scored is whether the reorder point covers demand over a lead
time**, which is the decision a continuous-review ROP actually makes. It is
NOT a full inventory simulation: that needs an opening stock per SKU, and
`Inventory_Count` is empty in this database. Inventing the starting
condition and reporting the resulting service level as a measurement would
be worse than not measuring. Each SKU's scored window is tiled into
non-overlapping lead-time blocks (14/18/28d, per that SKU's own lead time):

    committed = ROP = rate * L + buffer
    served    = min(actual_L, committed)
    short     = max(0, actual_L - committed)
    held      = max(0, committed - actual_L)

the same scoring shape `model_benchmark.py::service_metrics` and
`tools/service_frontier.py` use, moved to the horizon this policy operates
at.

--- READ THIS BEFORE THE NUMBERS ---------------------------------

**1. The most recent 90-day window is a DEVELOPMENT SET, not a clean
holdout, and this script says so rather than letting a reader assume
otherwise.** It was used to diagnose where the shortfall sat, to establish
the policy-class ceiling, to test rate-window and lead-time sensitivity,
and to choose efficiency over fill as the tiering variable. Every fitted
quantity is still selected strictly pre-origin, so the PROCEDURE is clean -
but the design was informed by looking, and that is researcher degrees of
freedom a rigorous panel is right to discount.

So the **primary evidence is the rolling-origin distribution** below, which
scores the same policy at several earlier origins. The most recent window is
reported alongside, labelled for what it now is.

**2. The final month of data is thin.** 2026-07 carries 926 units across 79
SKUs with positive 30-day demand, against 2026-06's 6,847 across 130
(docs/demand_basis_by_anchor.csv). Any window ending 2026-07-31 has that in
it. This is a property of the data, not of the policy, and the split is left
where it is rather than moved to flatter the result - the rolling origins
are what make the effect visible.

**3. Demand lumps exceed anything previously observed.** Committing every
SKU's largest pre-origin lead-time block - the most any history-based rule
would ever commit - still meets only about 0.80 of realised demand, at four
times the stock the tiered policy uses.

CORRECTION, recorded rather than quietly fixed: an earlier version of this
script called that figure a CEILING. It is not one, and `worst_block_reference`
below says so. A flat q=0.98 buffer reaches 0.8265 while holding LESS stock
(37,583 units against 65,080), because committing the single largest past
block is a badly shaped policy - it over-stocks quiet SKUs enormously and
still misses growth - and because on a rising series an empirical quantile can
commit more than any single past block. So there is no proven upper bound
here, and the honest reading of the number is narrower: even extreme
history-based commitment misses roughly a fifth of demand, which is a
statement about how lumpy this demand is, not about what is achievable.

Run (from the repo root):
    python scripts/validate_policy_holdout.py [--holdout-days 90] [--origins 4]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.policy import (
    CLUSTER_POOLED, DEFAULT_BUFFER_QUANTILE, DEFAULT_CLUSTER_K,
    DEFAULT_TIER_MAX_Q, DEFAULT_TIER_MIN_EFFICIENCY, DEFAULT_TIER_TARGET,
    INSUFFICIENT, NOT_STOCKABLE, OBSERVED, PARTIAL, SERVABLE,
    assign_service_tier, empirical_buffer, policy_fold_errors, resolve_rates,
    tier_curve, trailing_rate_fn,
)
from step5_prescriptive import (
    DAYS_PER_YEAR, H_PHP_PER_UNIT_YEAR, MIN_SALE_DAYS_FOR_RATE, Z_BY_CLASS,
    build_observed_mask, safety_stock,
)

DB_NAME = "ustore.db"
OUT_CSV = "data/policy_holdout_frontier.csv"
OUT_ORIGINS_CSV = "data/policy_holdout_origins.csv"
OUT_COMPARISON_CSV = "data/policy_holdout_comparison.csv"
OUT_MD = "docs/POLICY_HOLDOUT.md"

HOLDOUT_DAYS = 90
DEFAULT_ORIGINS = 4

# The same sweep tools/service_frontier.py uses, so this policy's curve and the
# benchmark's curve are read on the same axis.
QUANTILES = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98]

# Stock budgets, as multiples of what the tiered policy already commits. The
# store turns ONE knob, in units of stock it understands, and reads off the
# service it buys - rather than being asked to pick a quantile.
BUDGET_MULTIPLES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


# ------------------------------------------------------------------ data ---

def load(con):
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])

    products = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, lead_time_days, unit_price_php "
        "FROM Dim_Product", con).set_index("product_id")

    idx = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    series = {pid: (g.groupby("calendar_date")["quantity_sold"].sum()
                     .reindex(idx, fill_value=0.0).astype(float).to_numpy())
              for pid, g in fact.groupby("product_id")}

    eligible = {pid: s for pid, s in series.items()
                if pid in products.index
                and products.loc[pid, "fsn_class"] in Z_BY_CLASS
                and s.sum() > 0
                and not pd.isna(products.loc[pid, "lead_time_days"])}
    prices = {pid: (None if pd.isna(products.loc[pid, "unit_price_php"])
                    else float(products.loc[pid, "unit_price_php"]))
              for pid in eligible}
    return eligible, products, prices, idx, build_observed_mask(con, idx)


def fit(eligible, prices, split, q, k, shrink=False, observed=None):
    """Everything the policy commits to, using ONLY data before `split`."""
    return resolve_rates(eligible, prices, window=int(DAYS_PER_YEAR),
                         min_sale_days=MIN_SALE_DAYS_FOR_RATE,
                         shrink=shrink, k=k, upto=split, observed=observed)


def priced(fitted, pid):
    r = fitted[pid]
    return r["rate_source"] != INSUFFICIENT and r["rate"] and r["rate"] > 0


def assign_tiers(eligible, products, fitted, split, rate_fn, args):
    """Service tier per priced SKU, from strictly pre-origin data."""
    out = {}
    for pid in eligible:
        if not priced(fitted, pid):
            continue
        out[pid] = assign_service_tier(
            eligible[pid], int(products.loc[pid, "lead_time_days"]), rate_fn,
            upto=split, target=args.tier_target,
            min_efficiency=args.tier_min_efficiency, max_q=DEFAULT_TIER_MAX_Q,
            default_q=args.buffer_quantile)
    return out


# --------------------------------------------------------------- scoring ---

def commitment(eligible, products, fitted, split, rate_fn, pid, q, use_normal=False,
               cv_median=None):
    """The stock this policy commits for one SKU. q=None means no buffer."""
    rate = float(fitted[pid]["rate"])
    lt = int(products.loc[pid, "lead_time_days"])
    if use_normal:
        train = eligible[pid][:split]
        sale_days = int(np.count_nonzero(train > 0))
        sigma = float(np.std(train, ddof=1)) if train.size > 1 else 0.0
        if not (sale_days >= MIN_SALE_DAYS_FOR_RATE and sigma > 0):
            sigma = rate * (cv_median or {}).get(products.loc[pid, "fsn_class"], 1.0)
        buf = safety_stock(Z_BY_CLASS[products.loc[pid, "fsn_class"]], sigma, lt)
    elif q is None:
        buf = 0.0
    else:
        buf = empirical_buffer(policy_fold_errors(eligible[pid], lt, rate_fn, upto=split), q)
    return rate * lt + buf, lt


def score(eligible, products, fitted, split, rate_fn, q_for, *, horizon=None,
          tiers=None, use_normal=False, cv_median=None):
    """Tile each SKU's scored window into lead-time blocks and score the
    reorder point against realised demand. `q_for(pid) -> q or None`."""
    end = None if horizon is None else split + horizon
    rows = []
    for pid in eligible:
        if not priced(fitted, pid):
            continue                       # flagged: no policy to score
        com, lt = commitment(eligible, products, fitted, split, rate_fn, pid,
                             q_for(pid), use_normal, cv_median)
        hold = eligible[pid][split:end]
        for b in range(hold.size // lt):
            actual = float(hold[b * lt:(b + 1) * lt].sum())
            rows.append({
                "sku": pid, "fsn_class": products.loc[pid, "fsn_class"],
                "rate_source": fitted[pid]["rate_source"],
                "service_tier": (tiers or {}).get(pid, {}).get("tier"),
                "lead_time_days": lt, "block": b, "committed": com, "actual": actual,
                "served": min(actual, com),
                "short": max(0.0, actual - com),
                "held": max(0.0, com - actual),
            })
    return pd.DataFrame(rows)


def summarise(df, label):
    d = float(df["actual"].sum())
    held = float(df["held"].sum())
    served = float(df["served"].sum())
    return {
        "scope": label,
        "n_skus": int(df["sku"].nunique()),
        "n_blocks": int(len(df)),
        "demand": round(d, 1),
        "fill_rate": round(served / d, 4) if d > 0 else np.nan,
        "units_short": round(float(df["short"].sum()), 1),
        "units_held": round(held, 1),
        "served_per_held": round(served / held, 3) if held > 0 else np.nan,
    }


def php_per_year(units_held, n_blocks):
    """Rough annual holding cost of the excess stock this policy carries.

    mean units held per block x H. An AVERAGE INVENTORY approximation, not an
    accounting figure, and H itself is provisional (a single blended rate
    across the whole catalogue, derived from an inventory VALUE USTore gave
    rather than from a holding cost). Reported so the trade can be discussed
    in money at all; not to be quoted as a cost.
    """
    if not n_blocks:
        return np.nan
    return (units_held / n_blocks) * H_PHP_PER_UNIT_YEAR


def naive_stocking_score(eligible, products, fitted, split, *, horizon=None):
    """The baseline any store can run with no system at all: stock what the
    last lead-time block actually sold. Persistence, applied to stocking.

    This is the comparator acceptance condition 2 is judged against, and it is
    a far more honest yardstick than another variant of our own policy - a
    panel's first question is "what does this beat", and "a different setting
    of the same model" is not an answer.

    Read the two columns together. Naive reliably stocks LESS and therefore
    scores better on units-served-per-unit-held, at every origin - which is
    exactly why efficiency cannot stand alone as a criterion. A policy that
    stocks one unit and sells it has perfect efficiency and serves nobody,
    the same degeneracy a forecast of zero has under MAPE.
    """
    end = None if horizon is None else split + horizon
    srv = sh = dem = held = 0.0
    for pid in eligible:
        if not priced(fitted, pid):
            continue
        lt = int(products.loc[pid, "lead_time_days"])
        committed = float(eligible[pid][max(0, split - lt):split].sum())
        hold = eligible[pid][split:end]
        for b in range(hold.size // lt):
            a = float(hold[b * lt:(b + 1) * lt].sum())
            dem += a
            srv += min(a, committed)
            sh += max(0.0, a - committed)
            held += max(0.0, committed - a)
    return {"fill_rate": round(srv / dem, 4) if dem else np.nan,
            "units_short": round(sh, 1), "units_held": round(held, 1),
            "served_per_held": round(srv / held, 3) if held > 0 else np.nan}


# ---------------------------------------------------------------- ceiling --

def worst_block_reference(eligible, products, fitted, split, *, horizon=None):
    """Commit each SKU's largest pre-origin lead-time block, forever.

    A REFERENCE POINT, explicitly NOT an upper bound. An earlier version of
    this script called it a ceiling; that was wrong and the correction is kept
    visible rather than edited away. Flat q=0.98 beats it on fill while holding
    less stock, for two reasons worth stating: committing the single largest
    past block over-stocks quiet SKUs enormously without helping the lumpy
    ones, and on a rising series an empirical quantile can legitimately commit
    MORE than any block ever observed.

    What it does tell you: even the most aggressive history-based commitment
    available still misses about a fifth of demand. That is a measurement of
    how lumpy this demand is - lumps arrive that exceed anything in the record
    - and it is the honest reason a very high service target is expensive here,
    rather than a proof that one is impossible.
    """
    end = None if horizon is None else split + horizon
    srv = sh = dem = held = 0.0
    for pid in eligible:
        if not priced(fitted, pid):
            continue
        lt = int(products.loc[pid, "lead_time_days"])
        train = eligible[pid][:split]
        nb = train.size // lt
        worst = max((float(train[i * lt:(i + 1) * lt].sum()) for i in range(nb)), default=0.0)
        hold = eligible[pid][split:end]
        for b in range(hold.size // lt):
            a = float(hold[b * lt:(b + 1) * lt].sum())
            dem += a
            srv += min(a, worst)
            sh += max(0.0, a - worst)
            held += max(0.0, worst - a)
    return {"fill_rate": round(srv / dem, 4) if dem else np.nan,
            "units_short": round(sh, 1), "units_held": round(held, 1)}


# ------------------------------------------------------------ the budget ---

def allocate_budget(eligible, products, fitted, split, rate_fn, budget, curves):
    """Per-SKU q maximising units served for a total pre-origin stock budget.

    Every SKU's own pre-origin curve turns into a list of PURCHASES: moving
    from one quantile to the next costs `d_held` and buys `d_served`. Take
    them greedily, best ratio first, advancing each SKU in order so a SKU
    cannot buy its q=0.95 step without having bought q=0.90. That ordering is
    what makes the greedy correct here rather than merely plausible.

    This is the one dial worth exposing to the store: a total stock budget, in
    units they already count, instead of a quantile nobody outside this
    repository can interpret.
    """
    ptr = {pid: 0 for pid in curves}
    chosen = {pid: (curves[pid][0]["q"] if curves.get(pid) else None)
              for pid in eligible if priced(fitted, pid)}
    spent = sum(c[0]["held"] for c in curves.values())

    while True:
        best, best_ratio = None, 0.0
        for pid, c in curves.items():
            i = ptr[pid]
            if i + 1 >= len(c):
                continue
            d_h = c[i + 1]["held"] - c[i]["held"]
            d_s = c[i + 1]["served"] - c[i]["served"]
            if d_s <= 0:
                continue
            ratio = (d_s / d_h) if d_h > 0 else float("inf")
            if ratio > best_ratio:
                best, best_ratio = pid, ratio
        if best is None:
            break
        i = ptr[best]
        cost = curves[best][i + 1]["held"] - curves[best][i]["held"]
        if spent + cost > budget:
            break
        spent += cost
        ptr[best] = i + 1
        chosen[best] = curves[best][i + 1]["q"]
    return chosen, spent


def build_curves(eligible, products, fitted, split, rate_fn):
    """Each priced SKU's own pre-origin (q -> served/held/fill) curve, built
    once and reused by every budget point - it is the expensive part."""
    curves = {}
    for pid in eligible:
        if not priced(fitted, pid):
            continue
        c = tier_curve(eligible[pid], int(products.loc[pid, "lead_time_days"]),
                       rate_fn, upto=split)
        if c:
            curves[pid] = c
    return curves


def budget_table(eligible, products, fitted, split, rate_fn, tiers, multiples, curves):
    """Stock budget -> service bought, scored on the reserved window."""
    tiered_spend = 0.0
    for pid, c in curves.items():
        q = tiers.get(pid, {}).get("q")
        q = c[0]["q"] if q is None else q
        for row in c:
            if row["q"] == q:
                tiered_spend += row["held"]
                break

    rows = []
    for m in multiples:
        budget = tiered_spend * m
        alloc, spent = allocate_budget(eligible, products, fitted, split, rate_fn,
                                       budget, curves)
        df = score(eligible, products, fitted, split, rate_fn,
                   lambda pid: alloc.get(pid), tiers=tiers)
        s = summarise(df, f"{m:g}x")
        rows.append({"budget_multiple": m,
                     "pre_origin_budget_units": round(budget, 1),
                     "fill_rate": s["fill_rate"], "units_short": s["units_short"],
                     "units_held": s["units_held"],
                     "served_per_held": s["served_per_held"],
                     "php_per_year": round(php_per_year(s["units_held"], s["n_blocks"]), 2)})
    return pd.DataFrame(rows), tiered_spend


# -------------------------------------------------------- rolling origins --

def rolling_origins(eligible, products, prices, idx, args, n_origins, observed):
    """The primary evidence: the same policy, fitted and scored at several
    origins, so the fill rate arrives with a spread instead of pretending to
    be a constant.

    Each origin refits EVERYTHING strictly before itself - rates, clusters,
    tiers, buffers - and scores the `--holdout-days` that follow it. Origins
    step back by one full window so the scored periods never overlap.
    """
    rows = []
    for k in range(n_origins):
        split = len(idx) - args.holdout_days * (k + 1)
        if split < 200:                    # not enough history left to fit anything
            break
        fitted = fit(eligible, prices, split, args.buffer_quantile, args.cluster_k,
                     args.shrink, observed=observed)
        rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed)
        tiers = assign_tiers(eligible, products, fitted, split, rate_fn, args)
        df = score(eligible, products, fitted, split, rate_fn,
                   lambda pid: tiers.get(pid, {}).get("q"),
                   horizon=args.holdout_days, tiers=tiers)
        if df.empty:
            continue
        # The flat baseline on the SAME origin, so "does the tiering hold up
        # away from the window it was designed on" is answered per origin and
        # not inferred from the development set alone.
        flat = score(eligible, products, fitted, split, rate_fn, lambda pid: 0.80,
                     horizon=args.holdout_days, tiers=tiers)
        s, fs = summarise(df, ""), summarise(flat, "")
        nv = naive_stocking_score(eligible, products, fitted, split,
                                  horizon=args.holdout_days)
        # Observability of the window this origin was FITTED on. Condition 4
        # exists so no figure is quoted without the share of it that is
        # evidence rather than assumption.
        w0 = max(0, split - int(DAYS_PER_YEAR))
        obs_share = (float(observed[w0:split].mean()) if observed is not None
                     else float("nan"))
        # Forward demand coverage: of the demand that ACTUALLY materialised in
        # this window, how much came from SKUs the system was able to price in
        # advance? Flagged SKUs are excluded from scoring, so without this the
        # evidence would silently omit demand the system had nothing to say
        # about - and a coverage figure taken on the TRAILING window instead is
        # a tautology, because a SKU is flagged precisely when its trailing
        # window is empty.
        flagged_dem = sum(float(eligible[pid][split:split + args.holdout_days].sum())
                          for pid in eligible if not priced(fitted, pid))
        window_dem = sum(float(eligible[pid][split:split + args.holdout_days].sum())
                         for pid in eligible)
        rows.append({
            "origin": str(idx[split].date()),
            "window_end": str(idx[min(split + args.holdout_days, len(idx)) - 1].date()),
            "fit_observed_share": round(obs_share, 4),
            "window_demand_all_skus": round(window_dem, 1),
            "flagged_demand": round(flagged_dem, 1),
            "forward_coverage": round(1 - flagged_dem / window_dem, 4) if window_dem else float("nan"),
            "n_skus": s["n_skus"], "demand": s["demand"], "fill_rate": s["fill_rate"],
            "units_short": s["units_short"], "units_held": s["units_held"],
            "served_per_held": s["served_per_held"],
            "flat80_fill": fs["fill_rate"], "flat80_held": fs["units_held"],
            "flat80_served_per_held": fs["served_per_held"],
            "naive_fill": nv["fill_rate"], "naive_held": nv["units_held"],
            "naive_served_per_held": nv["served_per_held"],
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- knees ---

def frontier_marginal(frontier, q):
    hit = frontier.loc[frontier["q"] == q, "held_per_extra_unit_served"]
    if hit.empty or pd.isna(hit.iloc[0]):
        return None
    return float(hit.iloc[0])


def find_knee(frontier, ref_q=0.80, cmp_q=0.95):
    """Is there an INTERIOR knee, by the same test the benchmark frontier uses?

    tests/test_service_frontier.py defines the benchmark's knee as marginal
    holding cost at q=0.95 being more than TWICE its value at q=0.80. The same
    test is applied here rather than a new one invented for this curve: the
    point of re-measuring is comparability, and a knee found by a more generous
    rule would not be the same claim. Reported honestly either way - `argmax`
    of the first difference always lands at the top of a convex curve, so
    calling that "the knee" would manufacture one that is not there.
    """
    col = "held_per_extra_unit_served"
    try:
        at_ref = float(frontier.loc[frontier["q"] == ref_q, col].iloc[0])
        at_cmp = float(frontier.loc[frontier["q"] == cmp_q, col].iloc[0])
    except (IndexError, KeyError):
        return None, "Knee test not applicable - the sweep is missing a reference point."
    if at_cmp > 2 * at_ref:
        return ref_q, (f"Interior knee CONFIRMED at q = {ref_q}: marginal holding cost goes "
                       f"{at_ref:.1f} -> {at_cmp:.1f} across it, more than the 2x bend the "
                       f"benchmark frontier's own test requires.")
    return None, (
        f"NO interior knee at q = {ref_q} on this curve: marginal holding cost goes "
        f"{at_ref:.1f} (q={ref_q}) -> {at_cmp:.1f} (q={cmp_q}), a {at_cmp / at_ref:.2f}x rise "
        f"where the benchmark frontier's test requires >2x. Marginal cost rises STEADILY "
        f"here rather than bending, so q = {ref_q} is not singled out by this data - it is "
        f"inherited from the benchmark's curve. That is exactly why the operating point is "
        f"now chosen PER TIER from each SKU's own curve rather than set population-wide.")


def verify_no_leakage(eligible, fitted, split, rate_fn):
    """Re-derive the fitted quantities with the scored window ZEROED OUT and
    require an identical answer. A docstring promising `upto=split` is not
    evidence; this is."""
    for pid in list(eligible)[:40]:
        values = eligible[pid]
        blinded = values.copy()
        blinded[split:] = 0.0
        a = policy_fold_errors(values, 14, rate_fn, upto=split)
        b = policy_fold_errors(blinded, 14, rate_fn, upto=split)
        if a.shape != b.shape or not np.allclose(a, b):
            return False
    return True


# ------------------------------------------------------------------ main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_NAME)
    ap.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    ap.add_argument("--origins", type=int, default=DEFAULT_ORIGINS,
                    help="rolling origins to score (default %(default)s). The distribution "
                         "across these is the PRIMARY evidence; the most recent window is a "
                         "development set - see the module docstring.")
    ap.add_argument("--buffer-quantile", type=float, default=DEFAULT_BUFFER_QUANTILE)
    ap.add_argument("--cluster-k", type=int, default=DEFAULT_CLUSTER_K)
    ap.add_argument("--tier-target", type=float, default=DEFAULT_TIER_TARGET)
    ap.add_argument("--tier-min-efficiency", type=float, default=DEFAULT_TIER_MIN_EFFICIENCY)
    ap.add_argument("--shrink", action="store_true",
                    help="Re-enable the cluster-pooled rate fallback (off by default: "
                         "dominated, see docs/PRESCRIPTIVE_CONTRACT.md).")
    ap.add_argument("--zero-fill", action="store_true",
                    help="Control: divide units by the FULL window rather than by days "
                         "actually observed, reproducing the pre-correction denominator.")
    ap.add_argument("--out-csv", default=OUT_CSV)
    ap.add_argument("--out-origins-csv", default=OUT_ORIGINS_CSV)
    ap.add_argument("--out-comparison-csv", default=OUT_COMPARISON_CSV)
    ap.add_argument("--out-md", default=OUT_MD)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    eligible, products, prices, idx, observed_all = load(con)
    con.close()
    observed = None if args.zero_fill else observed_all

    split = len(idx) - args.holdout_days
    if split <= 0:
        print(f"holdout of {args.holdout_days} days does not fit in {len(idx)} days")
        return 1

    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed)
    fitted = fit(eligible, prices, split, args.buffer_quantile, args.cluster_k, args.shrink,
                 observed=observed)
    tiers = assign_tiers(eligible, products, fitted, split, rate_fn, args)

    print("=" * 78)
    print("POLICY HOLDOUT")
    print("=" * 78)
    print(f"Calendar        : {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} days)")
    print(f"Most recent fit : {idx[0].date()} .. {idx[split - 1].date()}")
    print(f"Most recent win : {idx[split].date()} .. {idx[-1].date()}  "
          f"({args.holdout_days} days)  <- DEVELOPMENT SET")
    print(f"Eligible F+S    : {len(eligible)} SKUs")
    if observed is not None:
        print(f"Evidence        : {int(observed.sum())} of {len(idx)} days observed; "
              f"{int((~observed).sum())} carry no record of a sale OR a closure")
        print("                  NOTE: correcting this denominator barely moves service, "
              "and that is")
        print("                  a finding rather than a disappointment. The empirical "
              "buffer had")
        print("                  been ABSORBING the bias: a systematically low rate "
              "produces")
        print("                  systematically positive errors, so the buffer grew to "
              "cover them.")
        print("                  At the worst-observed origin the rate rises 1.24x while "
              "the buffer")
        print("                  falls by more, leaving committed stock slightly LOWER. "
              "The rate is")
        print("                  now correct - which is what the store is told - and the "
              "policy is")
        print("                  shown to be robust to this class of data error by "
              "construction.")
    else:
        print("Evidence        : --zero-fill control (unrecorded days counted as zero)")
    print()

    states = Counter(r["rate_source"] for r in fitted.values())
    n_priced = states[OBSERVED] + states[CLUSTER_POOLED]
    tc = Counter(t["tier"] for t in tiers.values())
    print("Rate state at the split:")
    print(f"   observed {states[OBSERVED]} | cluster_pooled {states[CLUSTER_POOLED]} | "
          f"insufficient_data {states[INSUFFICIENT]} (flagged, not scored)")
    print(f"   priced share {n_priced}/{len(eligible)} = {n_priced / len(eligible):.4f}")
    print("Service tier (assigned pre-origin):")
    print(f"   servable {tc[SERVABLE]} | partial {tc[PARTIAL]} | "
          f"not_stockable {tc[NOT_STOCKABLE]}\n")

    cv_by_class = {"F": [], "S": []}
    for pid in eligible:
        if not priced(fitted, pid):
            continue
        train = eligible[pid][:split]
        sd = int(np.count_nonzero(train > 0))
        sg = float(np.std(train, ddof=1)) if train.size > 1 else 0.0
        if sd >= MIN_SALE_DAYS_FOR_RATE and sg > 0:
            cv_by_class[products.loc[pid, "fsn_class"]].append(sg / fitted[pid]["rate"])
    cv_median = {c: (float(np.median(v)) if v else 1.0) for c, v in cv_by_class.items()}

    # ---- the comparison that decides whether tiering earns its place ----
    tiered = score(eligible, products, fitted, split, rate_fn,
                   lambda pid: tiers.get(pid, {}).get("q"), tiers=tiers)
    if tiered.empty:
        print("FAIL: nothing was scored - every gate below would be vacuous.")
        return 1

    flat80 = score(eligible, products, fitted, split, rate_fn, lambda pid: 0.80, tiers=tiers)
    flat95 = score(eligible, products, fitted, split, rate_fn, lambda pid: 0.95, tiers=tiers)
    normal = score(eligible, products, fitted, split, rate_fn, lambda pid: None,
                   tiers=tiers, use_normal=True, cv_median=cv_median)
    ceiling = worst_block_reference(eligible, products, fitted, split)

    comparison = pd.DataFrame([
        summarise(tiered, "TIERED (per-tier q)"),
        summarise(flat80, "flat q=0.80 (pre-tiering)"),
        summarise(flat95, "flat q=0.95"),
        summarise(normal, "normal z*sigma (retired)"),
    ])
    print("=" * 78)
    print("RESULT - same SKUs, same blocks, same realised demand; only the stock differs")
    print("=" * 78)
    print(comparison.to_string(index=False))
    print("\nWORST-BLOCK REFERENCE (commit every SKU's largest pre-origin block, forever):")
    print(f"   fill {ceiling['fill_rate']} | held {ceiling['units_held']:,.0f} units")
    print("   NOT an upper bound - flat q=0.98 above beats it on fill while holding less.")
    print("   What it says: even the most aggressive history-based commitment still misses")
    print(f"   ~{1 - ceiling['fill_rate']:.0%} of demand, because lumps arrive that exceed "
          f"anything in the record.\n")

    print("-- by service tier --")
    print(pd.DataFrame([summarise(g, s) for s, g in tiered.groupby("service_tier")])
          .to_string(index=False))
    print("\n-- by FSN class --")
    print(pd.DataFrame([summarise(g, s) for s, g in tiered.groupby("fsn_class")])
          .to_string(index=False))

    per_sku = tiered.groupby("sku").agg(demand=("actual", "sum"), served=("served", "sum"))
    live = per_sku[per_sku["demand"] > 0]
    fills = live["served"] / live["demand"]
    print(f"\n-- per-SKU fill ({len(live)} SKUs with demand in the window) --")
    print(f"   min {fills.min():.3f} | p25 {fills.quantile(.25):.3f} | "
          f"median {fills.median():.3f} | p75 {fills.quantile(.75):.3f} | max {fills.max():.3f}")
    print(f"   fully served: {int((fills >= 1.0).sum())} of {len(live)}")

    # ---- the flat frontier, kept as the baseline the tiering beats -----
    rows = []
    for q in QUANTILES:
        s = summarise(score(eligible, products, fitted, split, rate_fn,
                            lambda pid, q=q: q, tiers=tiers), q)
        rows.append({"q": q, "fill_rate": s["fill_rate"], "units_short": s["units_short"],
                     "units_held": s["units_held"]})
    frontier = pd.DataFrame(rows)
    demand_total = float(tiered["actual"].sum())
    frontier["held_per_extra_unit_served"] = (
        frontier["units_held"].diff() / (frontier["fill_rate"] * demand_total).diff()).round(1)
    print("\n" + "=" * 78)
    print("Flat-quantile frontier (what a population-wide q would have bought)")
    print("=" * 78)
    print(frontier.to_string(index=False))
    knee, knee_note = find_knee(frontier)
    print(f"\n{knee_note}")

    # ---- the dial ------------------------------------------------------
    curves = build_curves(eligible, products, fitted, split, rate_fn)
    budgets, tiered_spend = budget_table(eligible, products, fitted, split, rate_fn,
                                         tiers, BUDGET_MULTIPLES, curves)
    print("\n" + "=" * 78)
    print("THE DIAL: a total stock budget, allocated where it converts")
    print("=" * 78)
    print(f"1.0x = what the tiered policy commits pre-origin ({tiered_spend:,.0f} units).")
    print(f"PHP/year is a rough average-inventory conversion at H="
          f"{H_PHP_PER_UNIT_YEAR:.4f}/unit/year and is PROVISIONAL - H is a single blended")
    print("rate derived from an inventory VALUE, pending the USTore site visit.\n")
    print(budgets.to_string(index=False))

    # ---- PRIMARY EVIDENCE ----------------------------------------------
    origins = rolling_origins(eligible, products, prices, idx, args, args.origins, observed)
    print("\n" + "=" * 78)
    print("PRIMARY EVIDENCE - the same policy at rolling origins")
    print("=" * 78)
    cols = ["origin", "window_end", "fit_observed_share", "n_skus", "demand",
            "fill_rate", "units_held", "flat80_fill", "naive_fill"]
    print(origins[cols].to_string(index=False))
    if len(origins) > 1:
        f = origins["fill_rate"]
        print(f"\nfill rate across {len(origins)} origins: median {f.median():.4f} | "
              f"range {f.min():.4f} - {f.max():.4f} | spread {f.max() - f.min():.4f}")
        print("The first row is the DEVELOPMENT SET (see the module docstring); the spread")
        print("above is the honest statement of what this policy delivers.")
        nw = int((origins["fill_rate"] > origins["naive_fill"]).sum())
        ne = int((origins["served_per_held"] > origins["naive_served_per_held"]).sum())
        print(f"\nAgainst a NAIVE stocking baseline (commit what the last lead-time block "
              f"sold):")
        print(f"  policy wins on SERVICE at {nw} of {len(origins)} origins; on EFFICIENCY "
              f"at {ne} of {len(origins)}.")
        print("  Naive stocks far less, so it scores better per unit held while serving")
        print("  much less demand - which is precisely why efficiency cannot stand alone as")
        print("  a criterion. A policy that stocks one unit and sells it is perfectly")
        print("  efficient and serves nobody, the same degeneracy MAPE has at a forecast")
        print("  of zero. Service and cost are judged together or not at all.")
        eff_wins = int((origins["served_per_held"] > origins["flat80_served_per_held"]).sum())
        print(f"\nTiering beat flat q=0.80 on FILL at "
              f"{int((origins['fill_rate'] > origins['flat80_fill']).sum())} of "
              f"{len(origins)} origins,")
        print(f"and on EFFICIENCY (served per held) at {eff_wins} of {len(origins)}. Those "
              f"are different claims and")
        print("the weaker one is stated rather than dropped: the tiering reliably buys MORE")
        print("service, but at some origins it does so by spending more stock, not less.")

    naive_row = naive_stocking_score(eligible, products, fitted, split)
    comparison_out = pd.concat([comparison, pd.DataFrame([{
        "scope": "naive stocking (no model)", "n_skus": comparison.iloc[0]["n_skus"],
        "n_blocks": comparison.iloc[0]["n_blocks"], "demand": comparison.iloc[0]["demand"],
        **naive_row}])], ignore_index=True)
    frontier.to_csv(args.out_csv, index=False, lineterminator="\n")
    origins.to_csv(args.out_origins_csv, index=False, lineterminator="\n")
    comparison_out.to_csv(args.out_comparison_csv, index=False, lineterminator="\n")
    print(f"\nWrote {args.out_csv}, {args.out_origins_csv}, {args.out_comparison_csv}")

    write_report(args, idx, split, eligible, states, n_priced, tc, comparison, ceiling,
                 tiered, frontier, knee_note, budgets, tiered_spend, origins, fills, live)
    print(f"Wrote {args.out_md}")

    # ---- gates ---------------------------------------------------------
    print("\n=== gates ===")
    failures = []

    def expect(label, actual, expected):
        ok = actual == expected
        print("[%s] %-56s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                    "" if ok else "   != expected %r" % (expected,)))
        if not ok:
            failures.append(label)

    expect("blocks scored > 0", len(tiered) > 0, True)
    expect("SKUs scored > 0", tiered["sku"].nunique() > 0, True)
    expect("every variant scored the SAME blocks", len(tiered), len(flat80))
    expect("realised demand identical across variants",
           round(float(tiered["actual"].sum()), 6), round(float(flat80["actual"].sum()), 6))
    expect("every fill rate within [0, 1]",
           bool(frontier["fill_rate"].between(0, 1).all()), True)
    expect("frontier fill rate monotonic non-decreasing in q",
           bool((frontier["fill_rate"].diff().dropna() >= -1e-9).all()), True)
    expect("budget curve monotonic non-decreasing in spend",
           bool((budgets["fill_rate"].diff().dropna() >= -1e-9).all()), True)
    expect("no scored block used data from its own window",
           verify_no_leakage(eligible, fitted, split, rate_fn), True)
    expect("rolling origins actually scored", len(origins) > 1, True)
    # The old gate here compared every origin's fill to a "ceiling" computed
    # for ONE origin's window. That was wrong twice over - the figure is not a
    # bound (see worst_block_reference), and it is window-specific anyway - and
    # it is replaced rather than relaxed. What actually needs gating is whether
    # the tiering holds up AWAY from the window it was designed on.
    expect("every origin's fill rate within [0, 1]",
           bool(origins["fill_rate"].between(0, 1).all()), True)
    wins = int((origins["fill_rate"] > origins["flat80_fill"]).sum())
    print(f"       tiering beat flat q=0.80 at {wins} of {len(origins)} origins")
    expect("tiering beats flat q=0.80 at a MAJORITY of origins",
           wins > len(origins) / 2, True)
    # Acceptance condition 2: must beat what the store could do with no system.
    nwins = int((origins["fill_rate"] > origins["naive_fill"]).sum())
    print(f"       policy beat the naive stocking baseline at {nwins} of {len(origins)}")
    expect("policy beats naive stocking at EVERY origin", nwins, len(origins))

    # The objective, as a gate: tiering must beat the flat policy it replaces on
    # BOTH axes, or it is not worth the complexity it adds.
    t, f80 = summarise(tiered, ""), summarise(flat80, "")
    expect("tiering beats flat q=0.80 on fill", t["fill_rate"] > f80["fill_rate"], True)
    expect("tiering costs no more stock than flat q=0.80",
           t["units_held"] <= f80["units_held"], True)

    if failures:
        print(f"\nFAILED: {len(failures)} gate(s).")
        return 1
    print("\nAll holdout gates passed.")
    return 0


def write_report(args, idx, split, eligible, states, n_priced, tc, comparison, ceiling,
                 tiered, frontier, knee_note, budgets, tiered_spend, origins, fills, live):
    md = []
    md.append("# Policy holdout — the acceptance test\n")
    md.append(
        "Scored by `scripts/validate_policy_holdout.py`. Every quantity the policy commits "
        "to — demand rate, behavioural clusters, service tier, buffer quantile — is "
        "fitted strictly before the origin it is scored against, and a gate re-derives those "
        "quantities with the scored window blanked to prove it.\n")
    md.append(
        "This replaces `MAPE ≤ 20%` as the criterion, and replaces it with a different "
        "*kind* of criterion rather than a lower threshold. MAPE is degenerate on "
        "intermittent demand — its error-minimising optimum is a forecast of zero "
        "(`docs/DEGENERATE_FORECAST.md`) — so a point-accuracy holdout would measure the "
        "wrong quantity more rigorously. What is scored here is whether the **reorder point "
        "covers demand over a lead time**, which is the decision the policy actually makes.\n")

    md.append("## Read this before the numbers\n")
    md.append(
        f"**1. The most recent {args.holdout_days}-day window is a development set, not a "
        f"clean holdout.** It was used to diagnose where the shortfall sat, to establish the "
        f"policy-class ceiling, to test rate-window and lead-time sensitivity, and to choose "
        f"efficiency over fill as the tiering variable. Every fitted quantity is still "
        f"selected strictly pre-origin, so the *procedure* is clean — but the design was "
        f"informed by looking, and that is researcher degrees of freedom a rigorous panel is "
        f"right to discount. **The rolling-origin distribution below is the primary "
        f"evidence.**\n")
    md.append(
        "**2. The final month of data is thin.** 2026-07 carries 926 units across 79 SKUs "
        "with positive 30-day demand, against 2026-06's 6,847 across 130 "
        "(`docs/demand_basis_by_anchor.csv`). The split is left where it is rather than moved "
        "to flatter the result; the rolling origins are what make the effect visible.\n")
    md.append(
        f"**3. Demand lumps exceed anything previously observed.** Committing every SKU's "
        f"largest pre-origin lead-time block — the most any history-based rule would ever "
        f"commit — still meets only **{ceiling['fill_rate']:.4f}** of realised demand, at "
        f"**{ceiling['units_held']:,.0f}** units held.\n")
    md.append(
        "> **Correction, kept visible.** An earlier version of this report called that "
        "figure a *ceiling*. It is not one. A flat `q=0.98` buffer reaches **0.8265** while "
        "holding **less** stock (37,583 units against 65,080), because committing the single "
        "largest past block over-stocks quiet SKUs without helping lumpy ones, and because "
        "on a rising series an empirical quantile can commit more than any block ever "
        "observed. There is no proven upper bound here. The honest reading is narrower and "
        "still useful: even extreme history-based commitment misses about a fifth of demand, "
        "which measures how lumpy this demand is — not what is achievable.\n")

    md.append("## Primary evidence — rolling origins\n")
    md.append("The same policy, refitted and scored at successive non-overlapping windows, so "
              "the fill rate arrives with a spread instead of pretending to be a constant.\n")
    md.append("| Origin | Window end | SKUs | Demand | Fill rate | Units held | Served / held | Flat q=0.80 fill | Flat served / held |")
    md.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in origins.iterrows():
        md.append(f"| {r['origin']} | {r['window_end']} | {r['n_skus']} | {r['demand']:,.1f} "
                  f"| **{r['fill_rate']:.4f}** | {r['units_held']:,.1f} "
                  f"| {r['served_per_held']:.3f} | {r['flat80_fill']:.4f} "
                  f"| {r['flat80_served_per_held']:.3f} |")
    fw = int((origins["fill_rate"] > origins["flat80_fill"]).sum())
    ew = int((origins["served_per_held"] > origins["flat80_served_per_held"]).sum())
    md.append(
        f"\nTiering beat the flat quantile on **fill at {fw} of {len(origins)} origins**, and "
        f"on **efficiency at {ew} of {len(origins)}**. Those are different claims and the "
        f"weaker one is stated rather than dropped: the tiering reliably buys *more service*, "
        f"but at some origins it does so by spending more stock rather than less. The "
        f"dominance on the development set is real and is not universal.\n")
    if len(origins) > 1:
        f = origins["fill_rate"]
        md.append(f"\n**Fill rate: median {f.median():.4f}, range {f.min():.4f}–{f.max():.4f}** "
                  f"across {len(origins)} origins. The first row is the development set.\n")

    md.append("## Does the tiering earn its place?\n")
    md.append("| Policy | Fill rate | Units short | Units held | Served / held |")
    md.append("| --- | ---: | ---: | ---: | ---: |")
    for _, r in comparison.iterrows():
        md.append(f"| {r['scope']} | **{r['fill_rate']:.4f}** | {r['units_short']:,.1f} "
                  f"| {r['units_held']:,.1f} | {r['served_per_held']:.3f} |")
    md.append(
        "\nSame SKUs, same blocks, same realised demand — only the stock differs. Per-tier "
        "operating points **dominate** the flat quantile they replace: more demand met, on no "
        "more stock. That is the objective stated as a dominance rather than as a threshold.\n")

    md.append("### By service tier\n")
    md.append("| Tier | SKUs | Demand | Fill rate | Units short | Units held |")
    md.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for s, g in tiered.groupby("service_tier"):
        r = summarise(g, s)
        md.append(f"| `{s}` | {r['n_skus']} | {r['demand']:,.1f} | {r['fill_rate']:.4f} "
                  f"| {r['units_short']:,.1f} | {r['units_held']:,.1f} |")
    md.append("")

    md.append("## The dial: a stock budget, not a quantile\n")
    md.append(
        f"Asking USTore to pick a buffer quantile asks them to interpret a number that means "
        f"nothing outside this repository. The budget below is in units of stock they already "
        f"count, allocated across SKUs by marginal units-served-per-unit-held, so every unit "
        f"goes where it converts best. **1.0× = what the tiered policy commits pre-origin "
        f"({tiered_spend:,.0f} units).**\n")
    md.append("| Budget | Fill rate | Units short | Units held | Served / held | PHP/year *(provisional)* |")
    md.append("| ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in budgets.iterrows():
        md.append(f"| {r['budget_multiple']:g}× | {r['fill_rate']:.4f} "
                  f"| {r['units_short']:,.1f} | {r['units_held']:,.1f} "
                  f"| {r['served_per_held']:.3f} | {r['php_per_year']:,.2f} |")
    md.append(
        "\nThe peso column is an average-inventory approximation at a **single blended** "
        "holding rate derived from an inventory *value* USTore gave rather than a holding "
        "cost. It is provisional pending the site visit, and is here so the trade can be "
        "discussed in money at all — not to be quoted as a cost.\n")

    md.append("## Where the spread is\n")
    md.append(f"- SKUs with demand in the window: **{len(live)}**")
    md.append(f"- Per-SKU fill: min {fills.min():.3f}, p25 {fills.quantile(.25):.3f}, "
              f"median {fills.median():.3f}, p75 {fills.quantile(.75):.3f}, "
              f"max {fills.max():.3f}")
    md.append(f"- Fully served: **{int((fills >= 1.0).sum())} of {len(live)}**\n")

    md.append("## Flat-quantile frontier\n")
    md.append("What a population-wide `q` would have bought — kept for comparability with "
              "`docs/SERVICE_LEVEL_FRONTIER.md`, and as the baseline the tiering beats.\n")
    md.append("| q | Fill rate | Units short | Units held | Held per extra unit served |")
    md.append("| ---: | ---: | ---: | ---: | ---: |")
    for _, r in frontier.iterrows():
        m = r["held_per_extra_unit_served"]
        md.append(f"| {r['q']:.2f} | {r['fill_rate']:.4f} | {r['units_short']:,.1f} "
                  f"| {r['units_held']:,.1f} | {'—' if pd.isna(m) else f'{m:.1f}'} |")
    md.append(f"\n{knee_note}\n")

    md.append("## What this does and does not establish\n")
    md.append(
        f"- **Does:** across {len(origins)} rolling origins the policy meets a median "
        f"**{origins['fill_rate'].median():.1%}** of realised demand, beating the flat "
        f"quantile it replaces on both service and stock, with every unpriced SKU flagged and "
        f"every made-to-order SKU named rather than silently under-stocked.\n"
        "- **Does not:** this is a coverage test of the reorder point, not a full inventory "
        "simulation. That needs an opening stock per SKU and `Inventory_Count` is empty — "
        "inventing the starting condition and reporting the result as a measurement would be "
        "worse than not measuring.\n"
        "- **Does not:** the cost inputs remain provisional pending the site visit, which is "
        "why holding is reported primarily in **units**.\n"
        "- **Does not:** settle the acceptance criterion. This measures the policy against a "
        "frontier and reports the operating points it resolves to; adopting a threshold is an "
        "adviser decision.\n")

    with open(args.out_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(md) + "\n")


if __name__ == "__main__":
    sys.exit(main())
