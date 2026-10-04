"""
tools/inventory_simulation.py
------------------------------------------------------------------
Does the reorder point actually prevent stockouts, on SKUs whose real
opening stock is known?

Every service figure this project reports today is REORDER-POINT
COVERAGE: `scripts/validate_policy_holdout.py` tiles each scored window
into lead-time blocks and asks whether `ROP = rate*L + buffer` covers the
demand in each block. That is the right proxy when no starting stock
exists, and it is what `docs/POLICY_HOLDOUT.md` measures. It is not the
same quantity the store experiences, which is: given what was on the
shelf on the day, how much demand walked away?

This tool measures the second one, on the subset where it can be measured
without inventing anything.

Where the opening stock comes from
----------------------------------
`Inventory_Count` (the table) is empty - it is fed by the Digital Tallying
Interface and the store has not tallied through the app yet. But the
historical workbook `data/USTore_inventory_excel_long_mapped.csv` holds 23
monthly counts (2024-11 .. 2026-04) of real hand-counted stock, already
vocabulary-mapped, and `backend/catalog.py::load_csv_stock` already reads
it for the dashboard's current-stock column. `scripts/step5_prescriptive.py`
already depends on it too: UNITS_ON_HAND_ESTIMATE (36,051) is that
workbook's 2026-03 snapshot, and is what the holding cost H is derived
from.

So for the SKUs the workbook covers, the opening condition does NOT have
to be invented. It is measured. That is the whole reason this tool can
exist, and the reason it covers a minority of the catalogue.

COVERAGE, STATED UP FRONT because it bounds every number below: 76 of the
266 scored SKUs (29%) appear in the workbook at all, carrying ~18% of
scored demand. This does not generalise to the 266 and no figure here
should be quoted as if it did.

What is scored
--------------
Per SKU, day by day across the holdout window:

    receipts land          on_hand += arrivals due today
    demand arrives         served = min(on_hand, demand)
    unmet demand is LOST   not backordered - a student who cannot buy a
                           lanyard today does not queue for next week
    review (continuous)    position = on_hand + on_order
                           position <= ROP  ->  order EOQ, arrives in L days

Four arms on identical SKUs, identical demand, identical opening stock -
only the reorder rule differs:

    tiered        the committed policy: per-tier buffer quantile
    flat q=0.80   the pre-tiering baseline it replaced
    naive         stock what the last lead-time block sold (no model)
    none          NO replenishment at all: run the opening stock down

`none` is not padding. With 27k-31k units on hand entering a 90-day
window, a policy can look excellent while contributing nothing, because
the shelf was already full. If `none` fills nearly as well as `tiered`,
the honest headline is that opening stock dominates the window - and that
has to be visible in the output, not buried.

Design decisions
----------------
- **Holding is reported in UNITS, never pesos.** Cost inputs stay
  provisional pending the site visit (docs/WORKLOG_POLICY_AND_ACCEPTANCE.md
  section 9), exactly as every other document here reports them.
- **Both ordering-cost scenarios are run.** `low_admin_cost` (S=1,250) and
  `high_goods_value` (S=200,000) are 160x apart, so their EOQs differ by
  ~12.6x. Picking one silently would hide that the order QUANTITY, not the
  reorder point, drives holding in this simulation.
- **Opening stock is month-granular.** A count is dated to a month; an
  origin is a day. The count is taken as the position at the origin, which
  slightly overstates stock when the origin falls late in its month. Stated
  rather than interpolated - interpolation would be inventing the thing
  this tool exists to avoid inventing.
- **2026-04 is excluded as a partial count** (187 of 1,416 quantities
  filled, 369 units against 2026-03's 36,051). Not a new finding: this is
  already `step5_prescriptive.UNITS_ON_HAND_SOURCE`'s documented reason for
  using 2026-03. It costs the 2026-05-03 origin, which is the development
  set anyway - the three that remain are the primary evidence.
- **Leakage.** Opening stock AT the origin is known at decision time and is
  legitimate. A count dated after the origin is not, and
  `opening_stock_at` filters on the origin's own month. Pinned by
  tests/test_inventory_simulation.py.
- **`--stock-scale` is where this tool stops measuring and starts
  supposing, and it says so in the output.** Everything above exists so
  that the opening condition is a COUNT rather than a guess. Multiplying
  that count asks a different question - "what would this policy have done
  on a leaner shelf?" - and the answer is a counterfactual, not a
  measurement. Only `1.0` is labelled `measured` in the `stock_basis`
  column; every other multiplier is labelled `counterfactual` and must be
  read as one. The reorder points are deliberately NOT rescaled with it:
  the policy is fitted on demand history and does not know what is on the
  shelf, and that asymmetry is exactly what the sweep probes.

- **`--synthetic-cover C` goes further still, and is the adjudication
  mode.** `docs/POLICY_HOLDOUT.md` reports the per-tier operating points
  DOMINATING flat q=0.80 under reorder-point coverage. Under this
  simulation that did not reproduce - but the tiering is fitted on 145-179
  SKUs and the workbook makes only 28-45 observable, so the honest reading
  was measured-and-unresolved rather than measured-and-refuted. Sample size
  was the suspect.

  `--synthetic-cover C` removes that suspect by replacing the opening
  condition entirely: every PRICED SKU opens at `C * rate * L`, the same
  depth relative to its own policy, so the whole priced catalogue becomes
  observable. Demand, rates, reorder points, lead times and EOQ are all the
  real ones; ONLY the opening stock is invented, and every row is labelled
  `synthetic` in `stock_basis`. It establishes nothing about USTore's
  shelf - the measured run stays the anchor and is printed first - and it
  answers exactly one question the measured shelf cannot: with enough SKUs
  observable, are these two rules separable at all?

  The precedent is `tools/sparsity_sensitivity_sim.py`, which answers "how
  dense would demand have to be?" in a controlled world for the same
  reason: train and test live inside one synthetic world, nothing real is
  scored against fabricated data, and the claim is about the METHODS rather
  than about the store.

  The difference is read with a PAIRED bootstrap CLUSTERED ON THE SKU.
  Paired because both arms run on identical SKUs, demand and opening stock -
  only the reorder rule differs - so the two outcomes must travel together
  under resampling. Clustered because a SKU appears at several origins and
  those rows are not independent draws; resampling rows would treat one SKU
  seen four times as four SKUs and narrow the interval it has no right to
  narrow. And it can return "cannot tell": a comparison that can only ever
  confirm is the failure mode tests/test_gates_can_fail.py exists to catch.

Read-only: opens ustore.db with mode=ro and writes nothing to it. In
particular it does NOT write `Inventory_Count` - that table is the
Tallying Interface's, and backfilling the workbook into it would relabel
historical rows as staff counts and break the "more recent month wins, tie
goes to the staff count" precedence in `catalog.load_current_stock`.

Run: python tools/inventory_simulation.py
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
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from forecasting.policy import (
    DEFAULT_BUFFER_QUANTILE, DEFAULT_TIER_MIN_EFFICIENCY, DEFAULT_TIER_TARGET,
    trailing_rate_fn,
)
from step5_prescriptive import (
    DAYS_PER_YEAR, H_PHP_PER_UNIT_YEAR, MIN_SALE_DAYS_FOR_RATE,
    ORDERING_COST_SCENARIOS, eoq,
)
import validate_policy_holdout as vph

OUT_CSV = "data/inventory_simulation.csv"
OUT_SYNTHETIC_CSV = "data/inventory_synthetic_cover.csv"

# fill_rate needs 4 decimals; unit counts read better with thousands commas.
# One float_format cannot do both, so the columns are formatted explicitly.
_FMT = {"fill_rate": lambda x: f"{x:.4f}"}


def _fmt(df):
    cols = {c: (lambda x: f"{x:,.1f}") for c in df.select_dtypes("number").columns}
    cols.update({k: v for k, v in _FMT.items() if k in df.columns})
    return df.to_string(index=False, formatters=cols)

# The workbook month that is a partial count. step5_prescriptive.py excludes
# it for the same reason; the constant is named here so the exclusion is
# greppable from both sides rather than being a bare string literal.
PARTIAL_COUNT_MONTHS = frozenset({"2026-04"})

ARMS = ("tiered", "flat80", "naive", "none")

# The two arms the tiering adjudication is about. Everything else in this file
# treats all four symmetrically; the bootstrap below is specifically the
# dominance claim docs/POLICY_HOLDOUT.md makes and this tool could not resolve.
ADJUDICATED = ("tiered", "flat80")

BOOTSTRAP_SEED = 20260923
DEFAULT_BOOTSTRAP = 10_000
DEFAULT_COVERS = [0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 16.0, 24.0]


# ------------------------------------------------------------- opening ---

def load_workbook(path=None):
    """The historical inventory workbook, as (item, month) -> units.

    Quantities are summed within an (item, month): the workbook splits one
    item across rows by Size and Location (an apparel item carries 10-16
    rows a month), so a row is not a stock level - the group is. This is
    the same convention `backend/catalog.py::load_csv_stock` uses.
    """
    if path is None:
        from db import INVENTORY_CSV
        path = INVENTORY_CSV
    df = pd.read_csv(path, usecols=["Date", "Quantity", "canonical_item_name"])
    df = df.dropna(subset=["canonical_item_name", "Quantity"])
    df["month"] = df["Date"].astype(str).str.slice(0, 7)
    df = df[~df["month"].isin(PARTIAL_COUNT_MONTHS)]
    return df.groupby(["canonical_item_name", "month"])["Quantity"].sum()


def _months_between(earlier, later):
    """Whole months from 'YYYY-MM' to 'YYYY-MM'."""
    ey, em = int(earlier[:4]), int(earlier[5:7])
    ly, lm = int(later[:4]), int(later[5:7])
    return (ly * 12 + lm) - (ey * 12 + em)


def opening_stock_at(workbook, origin_month, name_by_id, max_staleness=1):
    """product_id -> units on hand at `origin_month`, from the latest
    workbook count ON OR BEFORE it and no more than `max_staleness`
    months old.

    Two filters, for two different reasons.

    The `<= origin_month` filter is the LEAKAGE gate: a count taken after
    the decision point is not available at the decision point. Pinned by
    test_opening_stock_never_uses_a_count_after_the_origin.

    The staleness cap is an EVIDENCE gate. Carrying a count forward
    indefinitely would silently turn "measured opening stock" back into
    "invented opening stock" - a figure two months stale on a SKU selling
    daily describes a shelf that no longer exists, and the whole reason
    this tool can exist is that it does not invent the starting condition.
    One month is the tightest cap the workbook's monthly cadence allows.
    """
    at_or_before = workbook[workbook.index.get_level_values("month") <= origin_month]
    if at_or_before.empty:
        return {}
    latest_month = at_or_before.groupby(level="canonical_item_name").apply(
        lambda s: s.index.get_level_values("month").max())
    out = {}
    for pid, name in name_by_id.items():
        if name not in latest_month.index:
            continue
        m = latest_month[name]
        stale = _months_between(m, origin_month)
        if stale > max_staleness:
            continue
        out[pid] = {"units": float(at_or_before[(name, m)]), "as_of": m,
                    "staleness_months": stale}
    return out


# ---------------------------------------------------------- simulation ---

def simulate(demand, opening, rop, order_qty, lead_time):
    """One SKU, one arm, day by day. Returns the measured outcome.

    Continuous review with lost sales. `order_qty <= 0` disables
    replenishment entirely, which is how the `none` arm runs the opening
    stock down.
    """
    on_hand = float(opening)
    pipeline = {}
    served = short = 0.0
    stockout_days = 0
    orders = 0
    ordered_units = 0.0
    trace = np.empty(len(demand), dtype=float)

    for t, d in enumerate(demand):
        on_hand += pipeline.pop(t, 0.0)
        d = float(d)
        s = min(on_hand, d)
        on_hand -= s
        served += s
        if d > s:
            short += d - s
            stockout_days += 1
        trace[t] = on_hand
        if order_qty > 0:
            position = on_hand + sum(pipeline.values())
            if position <= rop:
                due = t + lead_time
                pipeline[due] = pipeline.get(due, 0.0) + order_qty
                orders += 1
                ordered_units += order_qty

    return {
        "demand": float(np.sum(demand)),
        "served": served,
        "short": short,
        "stockout_days": stockout_days,
        "mean_on_hand": float(trace.mean()) if trace.size else 0.0,
        "peak_on_hand": float(trace.max()) if trace.size else 0.0,
        "closing_on_hand": on_hand,
        "orders_placed": orders,
        "units_ordered": ordered_units,
    }


def reorder_points(eligible, products, fitted, split, rate_fn, tiers, args):
    """(rop, lead_time) per priced SKU, for each arm that has one."""
    out = {}
    for pid in eligible:
        if not vph.priced(fitted, pid):
            continue
        lt = int(products.loc[pid, "lead_time_days"])
        tier_q = tiers.get(pid, {}).get("q")
        tiered, _ = vph.commitment(eligible, products, fitted, split, rate_fn,
                                   pid, tier_q)
        flat80, _ = vph.commitment(eligible, products, fitted, split, rate_fn,
                                   pid, 0.80)
        naive = float(eligible[pid][max(0, split - lt):split].sum())
        out[pid] = {"tiered": tiered, "flat80": flat80, "naive": naive,
                    "none": 0.0, "lead_time": lt}
    return out


def run_origin(eligible, products, prices, idx, observed, split, workbook, args,
               scenario, S, stock_scale=1.0, synthetic_cover=None):
    """Every arm at one origin. Returns (pooled rows, per-SKU rows).

    Two mutually exclusive opening conditions:

    `synthetic_cover=None` - the MEASURED shelf. Opening stock is the
    workbook's real count, on the SKUs it covers. `stock_scale` multiplies it;
    1.0 is the count itself and anything else is a counterfactual shelf.

    `synthetic_cover=C` - a SYNTHETIC shelf. Every PRICED SKU opens at
    `C * rate * L`, so the population is the whole priced catalogue rather than
    the 28-45 the workbook happens to cover, and every SKU opens at the same
    depth relative to its own policy. Nothing about the demand, the rates or
    the reorder points is synthetic - only the opening condition is, and it is
    labelled `synthetic` in `stock_basis` on every row.
    """
    fitted = vph.fit(eligible, prices, split, args.cluster_k,
                     args.shrink, observed=observed,
                     short_window=getattr(args, "short_window", None))
    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed,
                               short_window=getattr(args, "short_window", None),
                               min_sale_days=MIN_SALE_DAYS_FOR_RATE)
    tiers = vph.assign_tiers(eligible, products, fitted, split, rate_fn, args)
    rops = reorder_points(eligible, products, fitted, split, rate_fn, tiers, args)

    if synthetic_cover is None:
        origin_month = str(idx[split].date())[:7]
        name_by_id = {pid: products.loc[pid, "item_name"] for pid in eligible}
        opening = opening_stock_at(workbook, origin_month, name_by_id,
                                   max_staleness=args.max_staleness_months)
        covered = sorted(set(rops) & set(opening))
        shelf_of = {pid: opening[pid]["units"] * stock_scale for pid in covered}
        basis = "measured" if stock_scale == 1.0 else "counterfactual"
        oldest = min((opening[p]["as_of"] for p in covered), default=None)
        stale = max((opening[p]["staleness_months"] for p in covered), default=None)
    else:
        # C lead-times of expected demand, per SKU, from the SKU's own rate.
        covered = sorted(rops)
        shelf_of = {pid: synthetic_cover * float(fitted[pid]["rate"]) * rops[pid]["lead_time"]
                    for pid in covered}
        basis, oldest, stale = "synthetic", None, None

    detail = {}
    for arm in ARMS:
        for pid in covered:
            lt = rops[pid]["lead_time"]
            annual = float(fitted[pid]["rate"]) * DAYS_PER_YEAR
            qty = 0.0 if arm == "none" else eoq(annual, S, H_PHP_PER_UNIT_YEAR)
            detail[(pid, arm)] = simulate(
                eligible[pid][split:split + args.holdout_days],
                shelf_of[pid], rops[pid][arm], qty, lt)

    common = {"origin": str(idx[split].date()), "scenario": scenario,
              "stock_scale": stock_scale, "synthetic_cover": synthetic_cover,
              "stock_basis": basis}

    rows = []
    for arm in ARMS:
        agg = {k: sum(detail[(pid, arm)][k] for pid in covered)
               for k in ("demand", "served", "short", "stockout_days",
                         "mean_on_hand", "orders_placed", "units_ordered")} \
            if covered else {k: 0.0 for k in ("demand", "served", "short",
                                              "stockout_days", "mean_on_hand",
                                              "orders_placed", "units_ordered")}
        d = agg["demand"]
        rows.append({
            **common,
            # The OLDEST count in use and the worst staleness describe the
            # same SKU, so the two columns are read together. Reporting the
            # newest count beside the worst staleness would describe two
            # different SKUs and flatter the window.
            "oldest_count": oldest,
            "max_staleness_months": stale,
            "arm": arm,
            "n_skus": len(covered),
            "opening_units": round(sum(shelf_of[p] for p in covered), 1),
            "demand": round(d, 1),
            "served": round(agg["served"], 1),
            "fill_rate": round(agg["served"] / d, 4) if d > 0 else np.nan,
            "units_short": round(agg["short"], 1),
            "stockout_days": int(agg["stockout_days"]),
            "mean_on_hand": round(agg["mean_on_hand"], 1),
            "orders_placed": int(agg["orders_placed"]),
            "units_ordered": round(agg["units_ordered"], 1),
        })

    # Per-SKU detail for the two adjudicated arms, so the difference between
    # them can be resampled at the level it is actually uncertain at: the SKU.
    per_sku = [{**common, "sku": pid,
                "demand": detail[(pid, ADJUDICATED[0])]["demand"],
                # When the shelf opens below BOTH reorder points, both rules
                # fire on day zero and the lead time - not the reorder point -
                # decides what gets served. The two arms are then identical by
                # construction, and a difference of exactly zero is a
                # mechanism rather than a measurement.
                "opening_below_both_rops": bool(
                    shelf_of[pid] <= min(rops[pid]["tiered"], rops[pid]["flat80"])),
                **{f"{k}_{arm}": detail[(pid, arm)][k]
                   for arm in ADJUDICATED for k in ("served", "mean_on_hand")}}
               for pid in covered]
    return rows, per_sku


# ---------------------------------------------------------- adjudication ---

def paired_bootstrap(per_sku, n_boot=DEFAULT_BOOTSTRAP, seed=BOOTSTRAP_SEED):
    """Resample SKUs with replacement; report tiered - flat80 with a CI.

    PAIRED, and clustered on the SKU. Both arms run on identical SKUs,
    identical demand and identical opening stock - only the reorder rule
    differs - so a SKU's two outcomes must move together under resampling or
    the comparison stops being the one that was measured. Clustered because a
    SKU appears at several origins and those rows are not independent draws:
    resampling rows rather than SKUs would treat one SKU seen four times as
    four SKUs and narrow the interval it has no right to narrow.

    The unit is the SKU because that is the axis the sample size objection is
    about - docs/INVENTORY_SIMULATION.md could not separate these two arms on
    28-45 SKUs, and the question is whether more SKUs separate them.
    """
    if per_sku.empty:
        return None
    by_sku = (per_sku.groupby("sku", sort=True)
              [["demand", "served_tiered", "served_flat80",
                "mean_on_hand_tiered", "mean_on_hand_flat80"]].sum())
    dem = by_sku["demand"].to_numpy(float)
    st = by_sku["served_tiered"].to_numpy(float)
    sf = by_sku["served_flat80"].to_numpy(float)
    ht = by_sku["mean_on_hand_tiered"].to_numpy(float)
    hf = by_sku["mean_on_hand_flat80"].to_numpy(float)
    n = len(dem)

    def stats(ix):
        d = dem[ix].sum()
        fill = (st[ix].sum() - sf[ix].sum()) / d if d > 0 else np.nan
        # Holding as a RATIO, so it reads on the same axis as the committed
        # "holds 6% more stock" and does not depend on how many SKUs are in
        # the resample.
        hb = hf[ix].sum()
        return fill, (ht[ix].sum() / hb - 1.0) if hb > 0 else np.nan

    point = stats(np.arange(n))
    rng = np.random.default_rng(seed)
    draws = np.array([stats(rng.integers(0, n, n)) for _ in range(n_boot)])
    fill_d, hold_d = draws[:, 0], draws[:, 1]

    return {
        "n_skus": n,
        "n_skus_with_demand": int((dem > 0).sum()),
        "n_boot": n_boot,
        "d_fill": point[0],
        "d_fill_lo": float(np.nanpercentile(fill_d, 2.5)),
        "d_fill_hi": float(np.nanpercentile(fill_d, 97.5)),
        "p_fill_gt0": float(np.nanmean(fill_d > 0)),
        "d_hold": point[1],           # share more (+) or less (-) stock held
        "d_hold_lo": float(np.nanpercentile(hold_d, 2.5)),
        "d_hold_hi": float(np.nanpercentile(hold_d, 97.5)),
        # The dominance claim is BOTH at once: more served on no more stock.
        "p_dominates": float(np.nanmean((fill_d > 0) & (hold_d <= 0))),
    }


def verdict(b):
    """One line, and it must be able to say 'cannot tell'."""
    if b is None:
        return "no SKUs to adjudicate"
    sep = (b["d_fill_lo"] > 0) or (b["d_fill_hi"] < 0)
    if not sep:
        return "NOT SEPARABLE - the 95% interval for the fill difference spans zero"
    if b["d_fill_lo"] > 0 and b["d_hold_hi"] <= 0:
        return "TIERED DOMINATES - more fill, no more stock, both separable"
    if b["d_fill_lo"] > 0:
        return "TIERED WINS ON FILL but buys it with stock - a trade, not a dominance"
    return "FLAT q=0.80 WINS ON FILL - the tiering is not earning its complexity here"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--db", default=os.path.join(_ROOT, "ustore.db"))
    ap.add_argument("--holdout-days", type=int, default=vph.HOLDOUT_DAYS)
    ap.add_argument("--origins", type=int, default=vph.DEFAULT_ORIGINS)
    ap.add_argument("--buffer-quantile", type=float, default=DEFAULT_BUFFER_QUANTILE)
    ap.add_argument("--cluster-k", type=int, default=4)
    ap.add_argument("--tier-target", type=float, default=DEFAULT_TIER_TARGET)
    ap.add_argument("--tier-min-efficiency", type=float,
                    default=DEFAULT_TIER_MIN_EFFICIENCY)
    ap.add_argument("--shrink", action="store_true")
    ap.add_argument("--max-staleness-months", type=int, default=1,
                    help="How old an opening count may be, in months. Beyond "
                         "this the origin is dropped rather than simulated "
                         "from a stale shelf (default: 1).")
    ap.add_argument("--stock-scale", type=float, nargs="+", default=[1.0],
                    metavar="MULT",
                    help="Multiplier(s) on the measured opening stock. 1.0 (the "
                         "default) is the count itself and is the only value "
                         "reported as `measured`; anything else is a "
                         "COUNTERFACTUAL shelf, labelled as such in the output. "
                         "Reorder points are not rescaled with it.")
    ap.add_argument("--short-window", type=int, default=None,
                    help="Prefer this trailing window where a SKU has enough "
                         "sale-days in it, falling back to 365d otherwise. "
                         "Omitted (the default) is the committed behaviour.")
    ap.add_argument("--synthetic-cover", type=float, nargs="*", default=None,
                    metavar="C",
                    help="ADJUDICATION MODE. Replace the workbook's opening stock with "
                         "a SYNTHETIC shelf of C x rate x L units for EVERY priced SKU, "
                         "so the tiered-vs-flat80 comparison is observable on the whole "
                         "priced catalogue instead of the 28-45 the workbook covers. "
                         "Give several values to sweep, or none for the default sweep. "
                         "Every row is labelled `synthetic` in `stock_basis`; the "
                         "measured run is still printed first and is still the anchor.")
    ap.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP,
                    help="Resamples for the paired SKU bootstrap on tiered - flat80 "
                         "(default %(default)s; 0 disables).")
    ap.add_argument("--out-csv", default=os.path.join(_ROOT, OUT_CSV))
    ap.add_argument("--out-synthetic-csv",
                    default=os.path.join(_ROOT, OUT_SYNTHETIC_CSV))
    args = ap.parse_args()
    if args.synthetic_cover is not None and not args.synthetic_cover:
        args.synthetic_cover = list(DEFAULT_COVERS)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    eligible, products, prices, idx, observed = vph.load(con)
    workbook = load_workbook()

    print("=" * 100)
    print("INVENTORY SIMULATION - real opening stock, lost sales, continuous review")
    print("=" * 100)
    print(f"Eligible F+S SKUs        : {len(eligible)}")
    names = {products.loc[pid, "item_name"] for pid in eligible}
    wb_items = set(workbook.index.get_level_values("canonical_item_name"))
    print(f"Covered by the workbook  : {len(names & wb_items)} "
          f"({len(names & wb_items) / len(eligible):.0%}) "
          f"- every figure below is bounded by this")
    print(f"Partial counts excluded  : {', '.join(sorted(PARTIAL_COUNT_MONTHS))} "
          f"(step5_prescriptive.UNITS_ON_HAND_SOURCE excludes the same month)")
    print(f"Holding reported in      : UNITS (cost inputs provisional)")

    scales = sorted(set(args.stock_scale))
    if scales != [1.0]:
        print(f"Shelf depths simulated   : {', '.join(f'{s:g}x' for s in scales)} "
              f"- only 1x is a measured count; the rest are COUNTERFACTUAL")

    rows, measured_detail = [], []
    for scale in scales:
        for scenario, S in sorted(ORDERING_COST_SCENARIOS.items()):
            for k in range(args.origins):
                split = len(idx) - args.holdout_days * (k + 1)
                if split < 200:
                    break
                r, d = run_origin(eligible, products, prices, idx, observed,
                                  split, workbook, args, scenario, S,
                                  stock_scale=scale)
                rows.extend(r)
                if scale == 1.0 and scenario == "low_admin_cost":
                    measured_detail.extend(d)

    out = pd.DataFrame(rows)
    dropped = sorted(set(out.loc[out["n_skus"] == 0, "origin"]))
    out = out[out["n_skus"] > 0]
    if dropped:
        print(f"Origins dropped          : {', '.join(dropped)} - no opening count "
              f"within {args.max_staleness_months} month(s) of the origin")
    out.to_csv(args.out_csv, index=False, lineterminator="\n")

    for scale in scales:
        for scenario in sorted(out["scenario"].unique()):
            sub = out[(out["scenario"] == scenario) & (out["stock_scale"] == scale)]
            if sub.empty:
                continue
            basis = sub["stock_basis"].iloc[0]
            print(f"\n--- ordering cost: {scenario} "
                  f"(S = PHP {ORDERING_COST_SCENARIOS[scenario]:,.0f}/order)"
                  f" | shelf {scale:g}x [{basis}] ---")
            print(_fmt(sub.drop(columns=["scenario", "stock_scale", "stock_basis"])))

    print("\n" + "=" * 100)
    print("POOLED ACROSS ORIGINS")
    print("=" * 100)
    pooled = (out.groupby(["stock_scale", "scenario", "arm"], as_index=False)
                 .agg(n_origins=("origin", "nunique"),
                      demand=("demand", "sum"), served=("served", "sum"),
                      units_short=("units_short", "sum"),
                      stockout_days=("stockout_days", "sum"),
                      mean_on_hand=("mean_on_hand", "mean"),
                      orders_placed=("orders_placed", "sum"),
                      units_ordered=("units_ordered", "sum")))
    pooled["fill_rate"] = (pooled["served"] / pooled["demand"]).round(4)
    pooled["arm"] = pd.Categorical(pooled["arm"], ARMS, ordered=True)
    pooled = pooled.sort_values(["stock_scale", "scenario", "arm"])
    print(_fmt(pooled))
    print("\n  mean_on_hand is averaged across origins, not summed: each origin's\n"
          "  figure is already a portfolio mean over its own 90-day window.")

    if len(scales) > 1:
        print("\n" + "=" * 100)
        print("WHERE DOES THE REORDER RULE START TO MATTER?")
        print("=" * 100)
        print("  Fill by arm as the shelf thins. The column that answers the question is\n"
              "  `tiered-naive`: how much the fitted rate/buffer/tier apparatus buys over\n"
              "  summing the last lead-time block. Only the 1x row is a measured shelf.\n")
        sep = (out[out["scenario"] == "low_admin_cost"]
               .groupby(["stock_scale", "arm"], as_index=False)
               .agg(demand=("demand", "sum"), served=("served", "sum"),
                    opening=("opening_units", "sum")))
        sep["fill"] = sep["served"] / sep["demand"]
        wide = sep.pivot(index="stock_scale", columns="arm", values="fill")
        first = sep.drop_duplicates("stock_scale").set_index("stock_scale")
        cover = first["opening"] / first["demand"]
        wide.insert(0, "shelf_x_demand", cover)
        wide["tiered-naive"] = wide["tiered"] - wide["naive"]
        wide["tiered-none"] = wide["tiered"] - wide["none"]
        wide = wide[["shelf_x_demand"] + list(ARMS) + ["tiered-naive", "tiered-none"]]
        print(wide.to_string(float_format=lambda x: f"{x:.4f}"))
        print("\n  Fill is NOT guaranteed monotone in shelf depth, and the dips below are\n"
              "  not defects. Continuous review fires on inventory POSITION, so a deeper\n"
              "  opening shelf delays the first trigger and every order after it; within a\n"
              "  finite window that can cost a whole order of Q while gaining less than Q\n"
              "  in opening stock. Pinned by test_more_opening_stock_can_serve_less.")

    # ---------------------------------------------------------------------
    # The adjudication. docs/POLICY_HOLDOUT.md reports the per-tier operating
    # points DOMINATING flat q=0.80 under reorder-point coverage; this tool
    # found that it does not reproduce under simulation, and could not say
    # whether that was a refutation or a sample size - the workbook covers
    # 28-45 SKUs where the tiering is fitted on 145-179.
    # ---------------------------------------------------------------------
    if args.bootstrap:
        print("\n" + "=" * 100)
        print("DOES THE SERVICE TIERING EARN ITS COMPLEXITY?")
        print("=" * 100)
        print(f"  tiered - flat q=0.80, paired and clustered on the SKU, "
              f"{args.bootstrap:,} resamples (seed {BOOTSTRAP_SEED}).")
        print("  Both arms run on identical SKUs, demand and opening stock; only the")
        print("  reorder rule differs, so the difference is paired by construction.")
        print("  Scored on low_admin_cost: fill is identical across the two ordering-cost")
        print("  scenarios to four decimals, so the EOQ choice cannot move this comparison.")

        anchor = paired_bootstrap(pd.DataFrame(measured_detail), args.bootstrap)
        print("\n  --- MEASURED SHELF (the anchor; the workbook's own counts) ---")
        if anchor is None:
            print("    no covered SKUs")
        else:
            print(f"    SKUs                {anchor['n_skus']} "
                  f"({anchor['n_skus_with_demand']} with demand in the window)")
            print(f"    d fill              {anchor['d_fill']:+.5f}   "
                  f"95% CI [{anchor['d_fill_lo']:+.5f}, {anchor['d_fill_hi']:+.5f}]   "
                  f"P(>0) = {anchor['p_fill_gt0']:.2f}")
            print(f"    d stock held        {anchor['d_hold']:+.2%}   "
                  f"95% CI [{anchor['d_hold_lo']:+.2%}, {anchor['d_hold_hi']:+.2%}]")
            print(f"    P(dominates)        {anchor['p_dominates']:.2f}")
            print(f"    verdict             {verdict(anchor)}")

    if args.synthetic_cover:
        covers = sorted(set(args.synthetic_cover))
        print("\n  --- SYNTHETIC COVER SWEEP ---")
        print("  Every row below is SYNTHETIC and labelled `synthetic` in stock_basis.")
        print("  Opening stock is C x rate x L for EVERY priced SKU - the same depth")
        print("  relative to each SKU's own policy - so the comparison is observable on")
        print("  the whole priced catalogue instead of the minority the workbook counts.")
        print("  Demand, rates, reorder points and lead times are all the real ones;")
        print("  ONLY the opening condition is invented. It establishes nothing about")
        print("  USTore's shelf - the measured row above remains the anchor - and it")
        print("  exists to answer one question the measured shelf cannot: with enough")
        print("  SKUs observable, are these two rules separable at all?\n")

        syn_rows, syn_boot = [], []
        for C in covers:
            detail, below, total = [], 0, 0
            for k in range(args.origins):
                split = len(idx) - args.holdout_days * (k + 1)
                if split < 200:
                    break
                r, d = run_origin(eligible, products, prices, idx, observed, split,
                                  workbook, args, "low_admin_cost",
                                  ORDERING_COST_SCENARIOS["low_admin_cost"],
                                  synthetic_cover=C)
                syn_rows.extend(r)
                detail.extend(d)
                below += sum(1 for x in d if x["opening_below_both_rops"])
                total += len(d)
            below_both = below / total if total else np.nan
            b = paired_bootstrap(pd.DataFrame(detail), args.bootstrap)
            if b is None:
                continue
            dd = pd.DataFrame(detail)
            sub = pd.DataFrame(syn_rows)
            sub = sub[(sub["synthetic_cover"] == C) & (sub["arm"] == "tiered")]
            b.update({"cover": C, "below_both": below_both,
                      "shelf_x_demand": (sub["opening_units"].sum() /
                                         sub["demand"].sum()) if sub["demand"].sum() else np.nan})
            syn_boot.append(b)

        hdr = (f"{'C':>6}{'shelf/dem':>11}{'SKUs':>6}{'d_fill':>10}"
               f"{'95% CI':>22}{'P(>0)':>7}{'d_hold':>9}{'<both':>8}   verdict")
        print("  " + hdr)
        print("  " + "-" * (len(hdr) + 30))
        for b in syn_boot:
            ci = f"[{b['d_fill_lo']:+.5f},{b['d_fill_hi']:+.5f}]"
            print(f"  {b['cover']:>6g}{b['shelf_x_demand']:>11.2f}{b['n_skus']:>6}"
                  f"{b['d_fill']:>+10.5f}{ci:>22}{b['p_fill_gt0']:>7.2f}"
                  f"{b['d_hold']:>+9.1%}{b['below_both']:>8.0%}   {verdict(b)}")

        if syn_rows:
            syn = pd.DataFrame(syn_rows)
            syn.to_csv(args.out_synthetic_csv, index=False, lineterminator="\n")
            print(f"\n  Wrote {args.out_synthetic_csv} "
                  f"({len(syn):,} rows, every one stock_basis=synthetic)")

        seps = [b for b in syn_boot if (b["d_fill_lo"] > 0) or (b["d_fill_hi"] < 0)]
        print(f"\n  Separable at {len(seps)} of {len(syn_boot)} cover levels tested.")
        if not seps:
            print("  On this evidence the two rules are NOT distinguishable at any depth")
            print("  tested, even with the whole priced catalogue observable. That is an")
            print("  answer, not a missing result: the sample size was not what was")
            print("  hiding the effect.")

    print(f"\nWrote {args.out_csv}")
    print("\nRead the `none` arm before the others: it is the opening stock with no\n"
          "replenishment at all. Where it fills nearly as well as `tiered`, the window\n"
          "was carried by stock already on the shelf, not by the policy.")


if __name__ == "__main__":
    main()
