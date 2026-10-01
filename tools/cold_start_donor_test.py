"""
tools/cold_start_donor_test.py
------------------------------------------------------------------
Can a category label price the SKUs that have never sold?

`docs/ACCEPTANCE_STANDARD.md` condition 1b - forward demand coverage -
is the one failing check, at 0.8830 against an a priori 0.90. It fails
for one reason, and the reason is structural: demand arrives from SKUs
that had sold NOTHING anywhere in the record before the decision point,
so nothing fitted on sales history can price them. The reliable output
for those is the FLAG, not a number.

The only escape route that does not invent data is an ANALOG (donor)
model: borrow a rate from SIMILAR EXISTING items, since the item's own
history is empty by definition. That makes categorisation the thing
being tested - and this is the one place in the project where the
catalogue taxonomy has never been tried, because every previous test
(docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md) used it as a POOLING basis
for a model, not as a DONOR for an item with no history at all.

What is measured
----------------
At each rolling origin, on the COLD-START SUBSET ONLY:

    cold start   sum(series[:origin]) == 0   - never sold, ever, before
                 the origin, and sells in the window that follows

Six donor rules, identical SKUs, identical demand, identical scoring -
only where the borrowed rate comes from differs:

    none            commit nothing               (the current system)
    global          median rate over ALL priced SKUs   (no categorisation)
    category        median within apparel / non-apparel
    product_type    median within the 8 keyword buckets
    global_x{S}     CONTROL: `global`, scaled              <- see below
    price_band      median within the SKU's unit-price quantile band

THE MATCHED-STOCK CONTROL IS THE POINT, not decoration. A donor rule can
always buy fill by committing more stock, and the finer the grouping the
more likely its median lands high. The control holds the grouping
constant and moves ONLY the scale, so "did the label help?" is answered
against "would the SAME uncategorised rule, holding this much stock, have
done as well?".

A single scaled point cannot answer that for a rule that holds MORE stock
than the point does - it would read as a win when it is only a purchase.
So the control is swept into a FRONTIER: `global` is re-scored at a range
of scales, giving fill as a function of stock with no categorisation in
it anywhere, and each labelled rule is then compared against the
control's fill AT ITS OWN STOCK LEVEL, interpolated. The comparison is
`rule_fill - control_fill_at_the_same_units_held`. Positive means the
label bought something scaling could not.

Measured, `product_type` lands BELOW that frontier: the finer taxonomy
is not extracting a better rate, it is just committing more. The label's
real contribution is the `global` -> `category` gap, which is measured
here and is a fraction of a point.

WHAT THIS TOOL DOES NOT DO
--------------------------
It does not deploy anything, and nothing here changes a threshold, a
default or a committed figure. `forecasting/policy.py` is untouched and
`Result_Prescriptive` is not rewritten. It opens the database read-only.

The reason is condition 1b itself, and it is the most important thing
this tool prints. 1b counts a SKU as COVERED if it is priced AT ALL,
regardless of whether that price serves any demand. A donor model prices
every cold-start SKU, so it moves coverage from 0.8830 to ~1.00 and flips
the verdict to ACCEPTED - while serving only the fill rate in the table
below. That is the SAME tautology this project already caught and removed
once, in the trailing-coverage draft of this very condition
(docs/ACCEPTANCE_STANDARD.md condition 1). Adopting a donor model without
first re-specifying 1b would trade a reported failure for an unreported
one. The `--gaming` block prints that projection so it cannot be
overlooked by anyone reading the fill column and liking it.

Design choices, stated because they are choices
-----------------------------------------------
* **Population and origins are not re-derived.** `validate_policy_holdout.py`
  supplies both, via `load()` and the same `split = len(idx) - holdout*(k+1)`
  stepping, so this tool scores the same SKUs over the same windows as
  `docs/POLICY_HOLDOUT.md` and the coverage figures line up by construction.
* **Labels are not re-derived either.** `forecasting/category.py::classify`
  and `::classify_product_type` are the committed implementations, imported
  unchanged. This tool READS `Dim_Product.category` and `item_name` through
  them and writes nothing back - the controlled vocabulary is not touched.
* **The donor statistic is the MEDIAN**, not the mean, of the pool members
  that have a rate - the same choice and the same reason as
  `forecasting/policy.py::cluster_rates`: these distributions are
  right-skewed and one high-volume member would otherwise drag every
  borrower upward.
* **Every cold-start SKU is scored, including the ones that never sell.** A
  deployed donor model prices all of them, and the stock it commits to a SKU
  that turns out to sell nothing is a real cost. Restricting the scored set to
  the SKUs that happened to have demand conditions on the outcome and flatters
  every donor rule - measured, it roughly triples efficiency without moving
  fill at all, because the excluded SKUs contribute held and no demand.
  `--scored-set with-demand` reproduces that variant as a control so the size
  of the selection effect is visible rather than argued about.
* **No buffer.** The commitment is `donor_rate * L`, with no safety stock.
  A cold-start SKU has no prior-fold errors to take an empirical quantile
  of, so any buffer would itself have to be borrowed - a second borrowed
  quantity confounding the first. The question here is whether the LABEL
  improves the borrowed RATE, so everything else is held fixed.
  `--buffer-quantile` adds a donor-pool buffer as a sensitivity.
* **Price bands are quantiles of `unit_price_php`**, count set by
  `--price-bands` (default 4). There is no committed band definition in
  this repository, so this one is an analysis choice and its sensitivity
  to the band count is reported rather than hidden.

Leakage
-------
Every donor rate is fitted strictly pre-origin (`upto=split`), and a gate
re-derives the rates with the scored window blanked and requires an
identical answer. The label inputs - `Dim_Product.category`,
`item_name`, `unit_price_php` - are static catalogue attributes, not
derived from `Fact_Sales`, so unlike `fsn_class` they carry no temporal
leak; `FORECAST_EXPERIMENT_AUDIT.md` is about exactly that distinction
and the gate below makes the claim checkable rather than asserted.

Run (from the repo root):
    python tools/cold_start_donor_test.py
    python tools/cold_start_donor_test.py --price-bands 3 5 8   # sensitivity
    python tools/cold_start_donor_test.py --buffer-quantile 0.80
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from forecasting.category import classify, classify_product_type, storage_category_sql
from forecasting.policy import (
    DEFAULT_BUFFER_QUANTILE, DEFAULT_CLUSTER_K, empirical_buffer,
    policy_fold_errors, trailing_rate_fn, trailing_window,
)
from step5_prescriptive import DAYS_PER_YEAR, MIN_SALE_DAYS_FOR_RATE
import validate_policy_holdout as vph

OUT_CSV = "data/cold_start_donor.csv"
DEFAULT_CONTROL_SCALE = 1.2
# The control frontier: `global` re-scored across this range, so a labelled rule
# holding any amount of stock in it can be compared against an UNCATEGORISED rule
# holding the same. Wide enough to bracket every rule scored here.
CONTROL_SCALES = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
DEFAULT_PRICE_BANDS = 4

GLOBAL = "global"
CATEGORY = "category"
PRODUCT_TYPE = "product_type"
PRICE_BAND = "price_band"
CONTROL_PREFIX = "global_x"

# The grouping each rule pools its donors within. `global` is the degenerate
# one-group case and is kept in the same shape on purpose: "no categorisation"
# has to be scored by the identical code path as every categorisation, or the
# comparison is measuring the code and not the label.
GROUPINGS = (GLOBAL, CATEGORY, PRODUCT_TYPE, PRICE_BAND)


# ------------------------------------------------------------- labelling ---

def label_maps(eligible, products, n_bands):
    """sku -> group label, for each grouping. Static catalogue attributes only."""
    cat, ptype = {}, {}
    for pid in eligible:
        name = products.loc[pid, "item_name"]
        db_cat = products.loc[pid, "category"] if "category" in products.columns else None
        if db_cat is not None and pd.isna(db_cat):
            db_cat = None
        cat[pid] = classify(name, db_cat)
        ptype[pid] = classify_product_type(name, db_cat)

    prices = {pid: products.loc[pid, "unit_price_php"] for pid in eligible}
    known = np.array([float(p) for p in prices.values() if not pd.isna(p)], dtype=float)
    if known.size:
        edges = np.quantile(known, np.linspace(0, 1, n_bands + 1)[1:-1])
    else:
        edges = np.array([])
    band = {}
    for pid, p in prices.items():
        band[pid] = "unpriced" if pd.isna(p) else f"band{int(np.searchsorted(edges, float(p)))}"

    return {GLOBAL: {pid: "all" for pid in eligible},
            CATEGORY: cat, PRODUCT_TYPE: ptype, PRICE_BAND: band}


# ------------------------------------------------------------ donor rates ---

def donor_rates(eligible, fitted, groups, rate_fn, split):
    """group label -> median trailing rate over the pool members that have one.

    Pool membership is "priced at this origin" - exactly `vph.priced`, the
    same predicate the holdout uses to decide whether a SKU has a policy to
    score at all. A SKU with no rate contributes nothing to the median: the
    pool describes what its LIVE members sell, not what its dead ones do not.
    That is `cluster_rates`' rule, reused rather than reinvented.
    """
    pools = {}
    for pid in eligible:
        if not vph.priced(fitted, pid):
            continue
        pools.setdefault(groups[pid], []).append(float(fitted[pid]["rate"]))
    return {g: float(np.median(v)) for g, v in pools.items() if v}


def cold_start_skus(eligible, split):
    """SKUs with NO sale anywhere in the record strictly before `split`.

    Not "empty trailing window" - that is a different and much larger set,
    and conflating the two is the error `docs/ACCEPTANCE_STANDARD.md` §5
    corrects mid-paragraph. A SKU dormant for fourteen months has a history
    to reason from; one that has never sold does not.
    """
    return [pid for pid in eligible if float(np.asarray(eligible[pid])[:split].sum()) == 0.0]


# --------------------------------------------------------------- scoring ---

def score_donor(eligible, products, skus, split, horizon, rate_for, buffer_for=None):
    """Block-tile the scored window and score `rate*L (+buffer)` against demand.

    Identical in shape to `validate_policy_holdout.score` - same lead-time
    blocks, same served/short/held accounting - so a fill rate here is read
    on the same axis as every other fill rate this project reports.
    """
    rows = []
    for pid in skus:
        lt = int(products.loc[pid, "lead_time_days"])
        rate = rate_for(pid)
        if rate is None:
            com = 0.0
        else:
            com = rate * lt + (0.0 if buffer_for is None else buffer_for(pid))
        hold = np.asarray(eligible[pid], dtype=float)[split:split + horizon]
        for b in range(hold.size // lt):
            actual = float(hold[b * lt:(b + 1) * lt].sum())
            rows.append({"sku": pid, "block": b, "committed": com, "actual": actual,
                         "served": min(actual, com),
                         "short": max(0.0, actual - com),
                         "held": max(0.0, com - actual)})
    return pd.DataFrame(rows)


def summarise(df, rule):
    demand = float(df["actual"].sum()) if len(df) else 0.0
    served = float(df["served"].sum()) if len(df) else 0.0
    held = float(df["held"].sum()) if len(df) else 0.0
    return {"rule": rule,
            "n_skus": int(df["sku"].nunique()) if len(df) else 0,
            "n_blocks": int(len(df)),
            "demand": round(demand, 1),
            "fill": round(served / demand, 4) if demand > 0 else np.nan,
            "units_served": round(served, 1),
            "units_held": round(held, 1),
            "served_per_held": round(served / held, 3) if held > 0 else np.nan}


# ----------------------------------------------------------------- gates ---

def assert_no_leakage(eligible, prices, products, split, args, observed):
    """Re-derive the donor rates with the scored window blanked; require
    an identical answer. A docstring promising `upto=split` is not evidence."""
    blanked = {}
    for pid, v in eligible.items():
        w = np.asarray(v, dtype=float).copy()
        w[split:] = 0.0
        blanked[pid] = w
    groups = label_maps(eligible, products, args.price_bands[0])
    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed,
                               min_sale_days=MIN_SALE_DAYS_FOR_RATE)
    a = donor_rates(eligible, vph.fit(eligible, prices, split,
                                      DEFAULT_CLUSTER_K, False, observed=observed),
                    groups[CATEGORY], rate_fn, split)
    b = donor_rates(blanked, vph.fit(blanked, prices, split,
                                     DEFAULT_CLUSTER_K, False, observed=observed),
                    groups[CATEGORY], rate_fn, split)
    bad = [g for g in set(a) | set(b) if abs(a.get(g, float("nan")) - b.get(g, float("nan"))) > 1e-12]
    if bad:
        raise SystemExit(f"LEAKAGE: donor rates change when the scored window is blanked: {bad}")
    return len(a)


def assert_cold_start(eligible, skus, split):
    """Every scored SKU really has no pre-origin sale. Cheap, and it is the
    definition the whole measurement rests on."""
    for pid in skus:
        if float(np.asarray(eligible[pid])[:split].sum()) != 0.0:
            raise SystemExit(f"NOT COLD START: {pid} has pre-origin sales at split={split}")


# ------------------------------------------------------------------ main ---

def run_origin(eligible, products, prices, idx, args, k, observed):
    split = len(idx) - args.holdout_days * (k + 1)
    if split < 200:
        return None
    fitted = vph.fit(eligible, prices, split, DEFAULT_CLUSTER_K,
                     False, observed=observed)
    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed,
                               min_sale_days=MIN_SALE_DAYS_FOR_RATE)

    cold = cold_start_skus(eligible, split)
    assert_cold_start(eligible, cold, split)
    n_cold_all = len(cold)
    if args.scored_set == "with-demand":
        cold = [pid for pid in cold
                if np.asarray(eligible[pid])[split:split + args.holdout_days].sum() > 0]

    # Demand decomposition over the FULL window, on the same denominator
    # acceptance condition 1b uses: every eligible SKU, not only the scored ones.
    win = lambda pid: float(np.asarray(eligible[pid])[split:split + args.holdout_days].sum())
    window_demand = sum(win(pid) for pid in eligible)
    flagged = [pid for pid in eligible if not vph.priced(fitted, pid)]
    cold_demand = sum(win(pid) for pid in cold)
    quiet_demand = sum(win(pid) for pid in flagged if pid not in set(cold))

    out = {"origin": str(idx[split].date()),
           "window_end": str(idx[min(split + args.holdout_days, len(idx)) - 1].date()),
           "split": split,
           "n_cold_start": n_cold_all,
           "n_cold_start_scored": len(cold),
           "n_cold_start_with_demand": sum(1 for pid in cold if win(pid) > 0),
           "window_demand": round(window_demand, 1),
           "cold_start_demand": round(cold_demand, 1),
           "gone_quiet_demand": round(quiet_demand, 1),
           "forward_coverage": round(1 - (cold_demand + quiet_demand) / window_demand, 4)
           if window_demand else np.nan}

    groups_by_band = {n: label_maps(eligible, products, n) for n in args.price_bands}
    rules = []

    rules.append(("none", lambda pid: None, None))
    for n_bands in args.price_bands:
        groups = groups_by_band[n_bands]
        for g in GROUPINGS:
            if g == PRICE_BAND and len(args.price_bands) > 1:
                name = f"{PRICE_BAND}_{n_bands}"
            elif g == PRICE_BAND:
                name = PRICE_BAND
            elif n_bands != args.price_bands[0]:
                continue           # non-price groupings do not depend on band count
            else:
                name = g
            med = donor_rates(eligible, fitted, groups[g], rate_fn, split)
            gm = groups[g]
            rules.append((name, (lambda m, lab: lambda pid: m.get(lab.get(pid)))(med, gm), None))
            if g == GLOBAL and n_bands == args.price_bands[0]:
                scales = sorted(set(args.control_scales) | {args.control_scale})
                for sc in scales:
                    rules.append((f"{CONTROL_PREFIX}{sc:g}",
                                  (lambda m, lab, sc: lambda pid: None if m.get(lab.get(pid)) is None
                                   else m[lab[pid]] * sc)(med, gm, sc), None))

    buffer_for = None
    if args.buffer_quantile_donor is not None:
        # Sensitivity only: borrow the donor pool's MEDIAN empirical buffer too.
        pooled = [empirical_buffer(policy_fold_errors(eligible[pid],
                                                      int(products.loc[pid, "lead_time_days"]),
                                                      rate_fn, upto=split),
                                   args.buffer_quantile_donor)
                  for pid in eligible if vph.priced(fitted, pid)]
        pooled = [b for b in pooled if b is not None and np.isfinite(b)]
        med_buf = float(np.median(pooled)) if pooled else 0.0
        buffer_for = lambda pid: med_buf
        out["donor_buffer_units"] = round(med_buf, 3)

    per_rule = []
    for name, rate_for, _ in rules:
        df = score_donor(eligible, products, cold, split, args.holdout_days,
                         rate_for, buffer_for)
        row = summarise(df, name)
        row["origin"] = out["origin"]
        per_rule.append(row)
    return out, per_rule


def main():
    ap = argparse.ArgumentParser(description="Donor (analog) rates for cold-start SKUs.")
    ap.add_argument("--db", default=vph.DB_NAME)
    ap.add_argument("--holdout-days", type=int, default=vph.HOLDOUT_DAYS)
    ap.add_argument("--origins", type=int, default=vph.DEFAULT_ORIGINS)
    ap.add_argument("--price-bands", type=int, nargs="+", default=[DEFAULT_PRICE_BANDS],
                    help="quantile bands for the price-band donor rule; give several "
                         "to report the sensitivity (default %(default)s)")
    ap.add_argument("--control-scale", type=float, default=DEFAULT_CONTROL_SCALE,
                    help="the named matched-stock control point, kept for continuity with "
                         "docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md (default %(default)s)")
    ap.add_argument("--control-scales", type=float, nargs="+", default=CONTROL_SCALES,
                    help="scales swept to build the matched-stock control FRONTIER, against "
                         "which every labelled rule is compared at its own stock level")
    ap.add_argument("--buffer-quantile", type=float, default=DEFAULT_BUFFER_QUANTILE,
                    help="quantile passed to the policy fit (not to the donor)")
    ap.add_argument("--buffer-quantile-donor", type=float, default=None,
                    help="sensitivity: also give each donor the pool's median empirical "
                         "buffer at this quantile. Off by default - see the module docstring.")
    ap.add_argument("--scored-set", choices=["all", "with-demand"], default="all",
                    help="which cold-start SKUs to score. `all` (default) includes those "
                         "that sell nothing in the window - a donor model would price them "
                         "and carry their stock. `with-demand` is a CONTROL showing the "
                         "selection effect of excluding them.")
    ap.add_argument("--zero-fill", action="store_true",
                    help="control: full-window denominator rather than observed days")
    ap.add_argument("--out-csv", default=OUT_CSV)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    eligible, products, prices, idx, observed_all = vph.load(con)
    products = products.join(
        pd.read_sql_query(
            f"SELECT product_id, {storage_category_sql(con)} FROM Dim_Product", con)
          .set_index("product_id"), how="left")
    con.close()
    observed = None if args.zero_fill else observed_all

    print("=" * 78)
    print("COLD-START DONOR TEST")
    print("=" * 78)
    print(f"Calendar     : {idx[0].date()} .. {idx[-1].date()}  ({len(idx)} days)")
    print(f"Eligible F+S : {len(eligible)} SKUs   (population from validate_policy_holdout.load)")
    print(f"Origins      : {args.origins} x {args.holdout_days}d, stepping back non-overlapping")
    print(f"Price bands  : {args.price_bands}   control scale: x{args.control_scale:g}")
    print(f"Donor buffer : {'none (rate*L only)' if args.buffer_quantile_donor is None else args.buffer_quantile_donor}")
    print(f"Scored set   : {args.scored_set}"
          f"{'' if args.scored_set == 'all' else '   <- CONTROL: excludes cold SKUs that sold nothing'}")

    n_groups = assert_no_leakage(eligible, prices, products,
                                 len(idx) - args.holdout_days, args, observed)
    print(f"[GATE] donor rates unchanged when the scored window is blanked "
          f"({n_groups} category pools) - PASS")

    origins, rules = [], []
    for k in range(args.origins):
        got = run_origin(eligible, products, prices, idx, args, k, observed)
        if got is None:
            break
        o, per_rule = got
        origins.append(o)
        rules.extend(per_rule)
        assert_cold_start(eligible, cold_start_skus(eligible, o["split"]), o["split"])

    if not origins:
        print("no usable origin")
        return 1

    od = pd.DataFrame(origins)
    rd = pd.DataFrame(rules)

    print("\n--- cold start, per origin -------------------------------------------------")
    print(od[["origin", "window_end", "n_cold_start", "n_cold_start_scored",
              "window_demand", "cold_start_demand", "gone_quiet_demand",
              "forward_coverage"]].to_string(index=False))

    cold_u = od["cold_start_demand"].sum()
    quiet_u = od["gone_quiet_demand"].sum()
    all_u = od["window_demand"].sum()
    shortfall = cold_u + quiet_u
    n_distinct = len(set().union(*[
        set(cold_start_skus(eligible, int(s))) for s in od["split"]]))

    print(f"\npooled: {all_u:,.0f} units of forward demand across {len(od)} origins")
    print(f"  cold start (never sold before the origin) : {cold_u:>9,.0f}  "
          f"{cold_u / all_u:6.2%} of demand   {cold_u / shortfall:6.1%} of the shortfall")
    print(f"  history, gone quiet                       : {quiet_u:>9,.0f}  "
          f"{quiet_u / all_u:6.2%} of demand   {quiet_u / shortfall:6.1%} of the shortfall")
    print(f"  distinct cold-start SKUs                  : {n_distinct}")
    print(f"  pooled forward coverage                   : {1 - shortfall / all_u:.4f}")

    # Pool the rules across origins on the raw unit totals, not by averaging
    # per-origin fill rates - an origin with 40 units of cold-start demand must
    # not weigh the same as one with 3,000.
    pooled = (rd.groupby("rule", sort=False)
                .agg(n_blocks=("n_blocks", "sum"),
                     demand=("demand", "sum"),
                     units_served=("units_served", "sum"),
                     units_held=("units_held", "sum"))
                .reset_index())
    pooled["fill"] = (pooled["units_served"] / pooled["demand"]).round(4)
    pooled["served_per_held"] = (pooled["units_served"] /
                                 pooled["units_held"].replace(0, np.nan)).round(3)
    pooled = pooled.sort_values("fill").reset_index(drop=True)

    is_ctrl = pooled["rule"].str.startswith(CONTROL_PREFIX)
    print("\n--- donor rules, pooled over the origins, cold-start subset only -----------")
    print(pooled.loc[~is_ctrl, ["rule", "fill", "units_served", "units_held",
                                "served_per_held"]].to_string(index=False))

    # ---- the matched-stock control, as a frontier, evaluated not asserted ----
    ctrl = (pooled[pooled["rule"].str.startswith(CONTROL_PREFIX)]
            .assign(scale=lambda d: d["rule"].str.slice(len(CONTROL_PREFIX)).astype(float))
            .sort_values("units_held"))
    # `global` IS the control at scale 1.0 - comparing it against itself is
    # meaningless, so it is excluded here and reported in the table above.
    labelled = pooled[~pooled["rule"].str.startswith(CONTROL_PREFIX)
                      & ~pooled["rule"].isin(["none", GLOBAL])].sort_values("units_held")

    print("\n--- matched-stock control frontier (`global`, rescaled, no label anywhere) --")
    print(ctrl[["rule", "fill", "units_served", "units_held",
                "served_per_held"]].to_string(index=False))

    def control_fill_at(held):
        """The uncategorised control's fill at this stock level, interpolated."""
        h, f = ctrl["units_held"].to_numpy(float), ctrl["fill"].to_numpy(float)
        if held < h[0] or held > h[-1]:
            return None
        return float(np.interp(held, h, f))

    print("\n--- does the LABEL beat scaling, at the same stock? -------------------------")
    print(f"{'rule':<16}{'fill':>8}{'held':>10}{'control @ same held':>21}{'delta':>9}   verdict")
    print(f"{'-'*16}{'-'*8}{'-'*10}{'-'*21}{'-'*9}   {'-'*7}")
    verdicts = []
    for _, r in labelled.iterrows():
        cf = control_fill_at(r["units_held"])
        if cf is None:
            print(f"{r['rule']:<16}{r['fill']:>8.4f}{r['units_held']:>10,.0f}"
                  f"{'outside sweep':>21}{'':>9}   widen --control-scales")
            continue
        d = r["fill"] - cf
        v = ("BELOW the control - the label costs fill" if d < -1e-4 else
             "at the control - the label buys nothing" if d <= 1e-4 else
             "above the control - the label buys something")
        verdicts.append((r["rule"], d))
        print(f"{r['rule']:<16}{r['fill']:>8.4f}{r['units_held']:>10,.0f}"
              f"{cf:>21.4f}{d:>+9.4f}   {v}")

    # The price-band rule depends on a band count with no principled basis. If
    # several were swept, say plainly whether the answer survives the choice -
    # a result that flips sign with a free parameter is not a result.
    band_deltas = [(r, d) for r, d in verdicts if r.startswith(PRICE_BAND)]
    if len(band_deltas) > 1:
        lo, hi = min(d for _, d in band_deltas), max(d for _, d in band_deltas)
        stable = (lo > 0) or (hi < 0)
        print(f"\nprice-band sensitivity to the band count ({len(band_deltas)} settings): "
              f"delta ranges {lo:+.4f} .. {hi:+.4f}")
        print("  the sign is STABLE - the band count does not decide the answer" if stable else
              "  the sign FLIPS with the band count - price band is NOT established, it is a"
              "\n  free analysis parameter deciding the verdict. Reported, not resolved.")

    g, c = pooled.loc[pooled["rule"] == GLOBAL].iloc[0], pooled.loc[pooled["rule"] == CATEGORY].iloc[0]
    print(f"\nthe category label is worth {(c['fill'] - g['fill']) * 100:+.2f}pp of fill "
          f"({g['fill']:.4f} -> {c['fill']:.4f}), at {c['units_held'] - g['units_held']:+,.0f} units held")

    # ---- condition 1b, and why the fill column must not be read alone ----
    best = labelled.sort_values("fill").iloc[-1]
    gamed = 1 - quiet_u / all_u
    print("\n--- what deploying this would do to acceptance condition 1b ----------------")
    print("1b counts a SKU as COVERED if it is priced AT ALL - not if the price serves demand.")
    print(f"  coverage now                      : {1 - shortfall / all_u:.4f}   (FAILS the a priori 0.90)")
    print(f"  coverage with a donor model       : {gamed:.4f}   (PASSES - every cold SKU priced)")
    print(f"  share of that demand actually met : {best['fill']:.4f}   (rule `{best['rule']}`)")
    print(f"  => the verdict would flip to ACCEPTED on demand that is still "
          f"{1 - best['fill']:.0%} unserved.")
    print("  This is the trailing-coverage tautology again. The threshold is NOT changed here,")
    print("  and no donor rule is deployed; 1b would have to be re-specified first.")

    os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
    rd.to_csv(args.out_csv, index=False)
    od.to_csv(args.out_csv.replace(".csv", "_origins.csv"), index=False)
    print(f"\nwrote {args.out_csv} and {args.out_csv.replace('.csv', '_origins.csv')}")
    print("Nothing was written to the database; no threshold or default was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
