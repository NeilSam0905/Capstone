"""
tests/test_inventory_simulation.py
------------------------------------------------------------------
Pins the mechanism of tools/inventory_simulation.py
(docs/INVENTORY_SIMULATION.md).

Synthetic arrays and a synthetic workbook throughout - no ustore.db - so
these pin the REASONING rather than today's counts, the same way
tests/test_policy.py and tests/test_category_leakage.py do. The measured
figures move when the data does; the properties below must not.

What is worth a test here, in order of how badly a regression would hurt:

  1. **Nothing may open from a count taken after the origin.** This is the
     same leak class the project has already caught twice - once in
     `fsn_class` (docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's correction
     section) and once in the trailing-coverage tautology
     (docs/ACCEPTANCE_STANDARD.md condition 1b). An opening stock read from
     the future would not produce an obviously wrong number; it would
     produce a flattering one.
  2. **A stale count is dropped, not carried forward.** The entire claim of
     this tool is that it does NOT invent the starting condition. Carrying
     a count forward indefinitely re-invents it quietly.
  3. **Unmet demand is lost, not backordered.** A retail stockout is a lost
     sale. Backordering would let a late receipt retro-serve demand that
     already walked out, inflating every fill rate in the output.
------------------------------------------------------------------
"""
import numpy as np
import pandas as pd
import pytest

from inventory_simulation import (
    PARTIAL_COUNT_MONTHS, _months_between, load_workbook, opening_stock_at,
    paired_bootstrap, simulate, verdict,
)


def workbook(entries):
    """(item, month) -> units, in the shape load_workbook returns."""
    idx = pd.MultiIndex.from_tuples(
        [(i, m) for i, m, _ in entries], names=["canonical_item_name", "month"])
    return pd.Series([u for _, _, u in entries], index=idx, name="Quantity")


# ---------------------------------------------------------- leakage ------

def test_opening_stock_never_uses_a_count_after_the_origin():
    """The gate. A count taken in March is not evidence available in January."""
    wb = workbook([("Tote", "2026-01", 100.0), ("Tote", "2026-03", 999.0)])
    out = opening_stock_at(wb, "2026-01", {7: "Tote"}, max_staleness=1)
    assert out[7]["units"] == 100.0, "used a count from after the origin"
    assert out[7]["as_of"] == "2026-01"


def test_a_count_in_the_origins_own_month_is_available():
    """The other half of the gate: same-month counts are legitimate. The
    origin is a day inside that month and the count is taken at its start."""
    wb = workbook([("Tote", "2026-02", 42.0)])
    out = opening_stock_at(wb, "2026-02", {7: "Tote"}, max_staleness=0)
    assert out[7]["units"] == 42.0
    assert out[7]["staleness_months"] == 0


# ---------------------------------------------------------- staleness ----

def test_a_stale_count_is_excluded_rather_than_carried_forward():
    wb = workbook([("Tote", "2025-11", 100.0)])
    assert opening_stock_at(wb, "2025-12", {7: "Tote"}, max_staleness=1), \
        "a one-month-old count is within the cap and should be used"
    assert opening_stock_at(wb, "2026-02", {7: "Tote"}, max_staleness=1) == {}, \
        "a three-month-old count was carried forward instead of dropped"


def test_months_between_spans_a_year_boundary():
    assert _months_between("2025-11", "2026-02") == 3
    assert _months_between("2026-02", "2026-02") == 0


# ---------------------------------------------------------- simulation ---

def test_ample_stock_never_stocks_out_and_never_orders():
    r = simulate(np.ones(90), opening=1000.0, rop=10.0, order_qty=50.0,
                 lead_time=14)
    assert r["short"] == 0.0
    assert r["stockout_days"] == 0
    assert r["orders_placed"] == 0, "ordered while far above the reorder point"
    assert r["closing_on_hand"] == pytest.approx(910.0)


def test_an_empty_shelf_reorders_and_the_receipt_lands_after_exactly_lead_time():
    """One replenishment cycle, isolated: the window ends before the second
    order could arrive, so every starved day is a day before the FIRST
    receipt. A longer window starves again at the tail - correctly, because
    the reorder placed near the end lands after the window closes - which
    would blur the property this test exists to pin."""
    L = 14
    r = simulate(np.ones(L + 6), opening=0.0, rop=5.0, order_qty=40.0, lead_time=L)
    assert r["orders_placed"] == 1
    assert r["stockout_days"] == L, (
        f"expected exactly {L} starved days before the first receipt, "
        f"got {r['stockout_days']}")
    assert r["served"] == pytest.approx(6.0)


def test_no_second_order_is_placed_while_one_is_already_in_flight():
    """Inventory POSITION, not on-hand, drives the review - otherwise an
    empty shelf reorders every day of the lead time and the simulation
    floods itself with stock the policy never asked for."""
    r = simulate(np.ones(10), opening=0.0, rop=5.0, order_qty=40.0, lead_time=14)
    assert r["orders_placed"] == 1, "reordered against on-hand instead of position"


def test_unmet_demand_is_lost_not_backordered():
    """Day 0 wants 10 against 5 on hand. The 5 that walked away must not be
    served by the receipt that arrives later."""
    demand = np.zeros(30)
    demand[0] = 10.0
    r = simulate(demand, opening=5.0, rop=100.0, order_qty=50.0, lead_time=3)
    assert r["served"] == pytest.approx(5.0)
    assert r["short"] == pytest.approx(5.0)
    assert r["demand"] == pytest.approx(10.0)
    assert r["served"] + r["short"] == pytest.approx(r["demand"])


def test_the_none_arm_places_no_orders_and_runs_the_shelf_down():
    r = simulate(np.full(90, 2.0), opening=100.0, rop=50.0, order_qty=0.0,
                 lead_time=14)
    assert r["orders_placed"] == 0
    assert r["units_ordered"] == 0.0
    assert r["closing_on_hand"] == 0.0
    assert r["served"] == pytest.approx(100.0), "served more than the shelf held"


def test_mean_on_hand_is_the_window_average_not_the_closing_level():
    """Reported holding must be the average over the window - a closing-level
    figure would understate holding for any SKU that draws down."""
    r = simulate(np.ones(10), opening=10.0, rop=0.0, order_qty=0.0, lead_time=1)
    # on_hand after each day: 9,8,...,0  -> mean 4.5
    assert r["mean_on_hand"] == pytest.approx(4.5)
    assert r["closing_on_hand"] == 0.0


# ---------------------------------------------------------- workbook ----

def test_the_partial_count_month_is_excluded(tmp_path):
    """2026-04 holds 187 of 1,416 quantities. Including it would read as a
    catastrophic stock collapse rather than an unfinished sheet -
    step5_prescriptive.UNITS_ON_HAND_SOURCE excludes the same month."""
    partial = sorted(PARTIAL_COUNT_MONTHS)[0]
    csv = tmp_path / "wb.csv"
    csv.write_text(
        "Date,Quantity,canonical_item_name\n"
        "2026-03-01,500,Tote\n"
        f"{partial}-01,3,Tote\n", encoding="utf-8")
    wb = load_workbook(csv)
    months = set(wb.index.get_level_values("month"))
    assert partial not in months
    assert wb[("Tote", "2026-03")] == 500.0


def test_quantities_sum_across_size_and_location_rows(tmp_path):
    """One item is split across rows by Size/Location, so a row is not a
    stock level - the (item, month) group is. Same convention as
    backend/catalog.py::load_csv_stock."""
    csv = tmp_path / "wb.csv"
    csv.write_text(
        "Date,Quantity,canonical_item_name\n"
        "2026-03-01,10,Shirt\n"
        "2026-03-01,12,Shirt\n"
        "2026-03-01,14,Shirt\n", encoding="utf-8")
    assert load_workbook(csv)[("Shirt", "2026-03")] == 36.0


# ------------------------------------------------- shelf-depth sweep ----

def test_more_opening_stock_can_serve_less():
    """NOT a bug, and the reason no monotonicity assertion belongs here.

    Continuous review fires on inventory POSITION, so a deeper opening shelf
    delays the first trigger and slides every later order with it. Inside a
    finite window that can cost a whole order of Q while the shelf gained
    less than Q - so total availability, and fill, can fall as opening stock
    rises. A real property of (s,Q) with lost sales on a finite horizon.

    An earlier draft of the stock-depth sweep asserted monotonicity and would
    have reported this as a defect.
    """
    kw = dict(rop=50.0, order_qty=40.0, lead_time=14)
    demand = np.full(90, 4.0)
    deeper = simulate(demand, opening=80.0, **kw)
    leaner = simulate(demand, opening=70.0, **kw)
    assert deeper["served"] < leaner["served"], (
        "the boundary case no longer reproduces - if the simulation changed, "
        "re-derive it rather than deleting this test")
    assert deeper["orders_placed"] == leaner["orders_placed"] - 1, \
        "the mechanism is the forgone order; it is not what happened here"


def test_stock_scale_of_one_is_the_measured_shelf():
    """1.0x must pass the counted stock through untouched - the whole claim of
    the tool is that the opening condition is measured, not supposed."""
    demand = np.full(30, 1.0)
    assert (simulate(demand, 100.0 * 1.0, 10.0, 20.0, 7)["served"]
            == simulate(demand, 100.0, 10.0, 20.0, 7)["served"])


def test_halving_the_shelf_cannot_help_the_no_replenishment_arm():
    """`none` places no orders, so its served units are bounded by the shelf.
    If scaling ever fails to reach the simulation, this is where it shows."""
    demand = np.full(90, 3.0)
    full = simulate(demand, 200.0, 0.0, 0.0, 14)
    half = simulate(demand, 100.0, 0.0, 0.0, 14)
    assert half["served"] < full["served"]
    assert half["served"] == pytest.approx(100.0)


# ---------------------------------------------------------------------
# The tiering adjudication (--synthetic-cover and the paired bootstrap).
#
# docs/POLICY_HOLDOUT.md claims the per-tier operating points DOMINATE flat
# q=0.80. The measured shelf here could not separate them on 28-45 SKUs, and
# the question left open was whether that was a refutation or a sample size.
# What follows pins the machinery that answers it - in particular that the
# bootstrap is able to return "cannot tell", because a test that can only
# confirm is the same failure mode tests/test_gates_can_fail.py exists to
# prevent.
# ---------------------------------------------------------------------


def detail(rows):
    """(sku, demand, served_tiered, served_flat80, hold_t, hold_f) -> frame."""
    return pd.DataFrame(
        [{"sku": s, "demand": d, "served_tiered": st, "served_flat80": sf,
          "mean_on_hand_tiered": ht, "mean_on_hand_flat80": hf}
         for s, d, st, sf, ht, hf in rows])


def test_the_point_estimate_is_the_pooled_fill_difference():
    b = paired_bootstrap(detail([(1, 100, 60, 50, 10, 10),
                                 (2, 100, 30, 40, 10, 10)]), n_boot=200)
    assert b["d_fill"] == pytest.approx((90 - 90) / 200)


def test_the_bootstrap_can_say_cannot_tell():
    """Two SKUs pulling in opposite directions by the same amount. A method
    that always returns a verdict would call this a result."""
    b = paired_bootstrap(detail([(1, 100, 60, 40, 10, 10),
                                 (2, 100, 40, 60, 10, 10)]), n_boot=2000)
    assert b["d_fill_lo"] < 0 < b["d_fill_hi"]
    assert verdict(b).startswith("NOT SEPARABLE")


def test_a_consistent_gap_across_many_skus_is_separable():
    """The mirror: a bootstrap that can only ever say "cannot tell" is as
    useless as one that can only confirm."""
    b = paired_bootstrap(detail([(i, 100, 60, 50, 10, 10) for i in range(40)]),
                         n_boot=2000)
    assert b["d_fill_lo"] > 0
    assert b["p_fill_gt0"] == 1.0


def test_the_resample_is_paired_not_independent():
    """Both arms run on identical SKUs, demand and opening stock. If the two
    arms were resampled independently the pairing would break and a SKU's
    tiered result could be matched against another SKU's flat80. Here every
    SKU has served_tiered == served_flat80, so the difference is exactly zero
    in every resample - which only holds if the pair travels together."""
    b = paired_bootstrap(detail([(i, 100, 50 + i, 50 + i, 10, 10) for i in range(30)]),
                         n_boot=500)
    assert b["d_fill"] == 0.0
    assert b["d_fill_lo"] == b["d_fill_hi"] == 0.0


def test_the_resample_clusters_on_the_sku_not_the_row():
    """A SKU appears at several origins and those rows are not independent
    draws. Resampling rows would treat one SKU seen four times as four SKUs
    and narrow the interval it has no right to narrow."""
    one_sku_four_origins = detail([(1, 100, 60, 50, 10, 10)] * 4)
    b = paired_bootstrap(one_sku_four_origins, n_boot=200)
    assert b["n_skus"] == 1                      # not 4


def test_holding_is_reported_as_a_ratio_so_it_reads_beside_the_committed_figure():
    """docs/INVENTORY_SIMULATION.md reports the tiering holding "6% more
    stock". A difference in units would not be comparable to that, and would
    also scale with how many SKUs land in a resample."""
    b = paired_bootstrap(detail([(1, 100, 50, 50, 106, 100)]), n_boot=100)
    assert b["d_hold"] == pytest.approx(0.06)


def test_dominance_requires_both_more_fill_and_no_more_stock():
    """The claim under adjudication is a dominance, not a win on one axis.
    More fill bought with more stock is a trade and must be named one."""
    trade = paired_bootstrap(detail([(i, 100, 60, 50, 20, 10) for i in range(40)]),
                             n_boot=1000)
    assert trade["d_fill"] > 0 and trade["d_hold"] > 0
    assert trade["p_dominates"] == 0.0
    assert "trade, not a dominance" in verdict(trade)

    dominates = paired_bootstrap(detail([(i, 100, 60, 50, 9, 10) for i in range(40)]),
                                 n_boot=1000)
    assert "DOMINATES" in verdict(dominates)


def test_a_flat_quantile_win_is_named_as_one():
    b = paired_bootstrap(detail([(i, 100, 40, 55, 10, 10) for i in range(40)]),
                         n_boot=1000)
    assert "FLAT q=0.80 WINS" in verdict(b)


def test_the_bootstrap_is_deterministic_under_its_seed():
    d = detail([(i, 100, 50 + (i % 7), 50, 10, 10) for i in range(25)])
    assert paired_bootstrap(d, n_boot=300) == paired_bootstrap(d, n_boot=300)


def test_an_opening_shelf_below_both_reorder_points_makes_the_arms_identical():
    """Why the thin end of the --synthetic-cover sweep reads exactly zero. If
    the shelf opens below BOTH reorder points, both rules fire on day zero and
    the lead time - not the reorder point - decides what gets served. A
    difference of exactly zero there is a mechanism, not a measurement, and
    the sweep labels the share of SKUs in that state so the rows can be read
    correctly."""
    demand = np.full(60, 2.0)
    opening, big_q, lt = 5.0, 10_000.0, 14
    a = simulate(demand, opening, rop=50.0, order_qty=big_q, lead_time=lt)
    b = simulate(demand, opening, rop=80.0, order_qty=big_q, lead_time=lt)
    assert a["served"] == b["served"]
    assert a["mean_on_hand"] == b["mean_on_hand"]


def test_the_arms_separate_once_the_shelf_opens_above_one_reorder_point():
    """The complement - otherwise the test above would pass on a tool that
    ignored the reorder point entirely."""
    demand = np.full(60, 2.0)
    a = simulate(demand, 60.0, rop=50.0, order_qty=40.0, lead_time=14)
    b = simulate(demand, 60.0, rop=80.0, order_qty=40.0, lead_time=14)
    assert a["orders_placed"] != b["orders_placed"]
