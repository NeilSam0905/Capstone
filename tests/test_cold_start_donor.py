"""
tests/test_cold_start_donor.py
------------------------------------------------------------------
Pins the mechanism of tools/cold_start_donor_test.py
(docs/COLD_START_ANALOG.md).

Synthetic arrays throughout - no ustore.db - so these pin the REASONING
rather than today's numbers, the same way tests/test_inventory_simulation.py
and tests/test_policy.py do.

What is worth a test here, in order of how badly a regression would hurt:

  1. **Cold start must mean "never sold before the origin", not "empty
     trailing window".** These are different sets and the difference is the
     whole finding: one is a structural boundary nothing fitted on sales
     history can cross, the other is a dormant SKU with a history to reason
     from. docs/ACCEPTANCE_STANDARD.md section 5 conflates them for one
     paragraph and then corrects itself; the code must not.
  2. **The matched-stock control must actually reject a rule that only buys
     stock.** That control is the single load-bearing piece of the analysis -
     it is what rejects `product_type`. A control that cannot reject is
     decoration, the same objection tests/test_gates_can_fail.py makes of a
     gate that cannot fail. So there is a test here with a synthetic "label"
     whose ONLY effect is to commit more, and the frontier must mark it down.
  3. **No donor rate may see past the origin.** Same leak class the project
     has already caught in `fsn_class`; a leaked donor rate would not look
     wrong, it would look good.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from cold_start_donor_test import cold_start_skus, donor_rates, score_donor, summarise


class Products:
    """Minimal stand-in for the Dim_Product frame score_donor indexes into."""

    def __init__(self, lead_times):
        self._lt = lead_times

    @property
    def loc(self):
        return self

    def __getitem__(self, key):
        pid, col = key
        assert col == "lead_time_days"
        return self._lt[pid]


def fitted_of(rates):
    return {pid: {"rate": r, "rate_source": "observed" if r else "insufficient_data"}
            for pid, r in rates.items()}


# ---- 1. what cold start is, and what it is not --------------------------

def test_a_sku_that_never_sold_before_the_origin_is_cold_start():
    series = {"new": np.concatenate([np.zeros(100), np.full(10, 3.0)])}
    assert cold_start_skus(series, 100) == ["new"]


def test_a_dormant_sku_with_old_sales_is_not_cold_start():
    """Sold on day 1, silent for the next 99. Its trailing 365-day window may
    be empty, but it has a history - it is NOT the structural boundary."""
    v = np.zeros(110)
    v[0] = 5.0
    assert cold_start_skus({"dormant": v}, 100) == []


def test_cold_start_is_evaluated_strictly_before_the_origin():
    """A sale ON the origin day belongs to the scored window, not to history."""
    v = np.zeros(110)
    v[100] = 4.0
    assert cold_start_skus({"edge": v}, 100) == ["edge"]
    assert cold_start_skus({"edge": v}, 101) == []


# ---- 2. the donor statistic ---------------------------------------------

def test_the_donor_rate_is_the_median_of_the_pool_not_the_mean():
    """Right-skewed pools are why: one high-volume member must not drag every
    borrower upward. Same choice, same reason, as policy.cluster_rates."""
    fitted = fitted_of({"a": 1.0, "b": 2.0, "c": 99.0})
    groups = {"a": "g", "b": "g", "c": "g"}
    assert donor_rates(fitted.keys(), fitted, groups, None, 0)["g"] == 2.0


def test_an_unpriced_member_contributes_nothing_to_its_pool():
    """A pool describes what its LIVE members sell, not what its dead ones
    do not - shrinking a borrower toward zero is the degeneracy the whole
    contract exists to keep out of the prescription."""
    fitted = fitted_of({"a": 2.0, "b": 4.0, "dead": 0.0})
    groups = {"a": "g", "b": "g", "dead": "g"}
    assert donor_rates(fitted.keys(), fitted, groups, None, 0)["g"] == 3.0


def test_donor_rates_cannot_see_past_the_origin():
    """Two pools identical before the origin and wildly different after it
    must produce the same donor rate. `fitted` is built with upto=split
    upstream; this pins that the statistic adds no second path to the future."""
    fitted = fitted_of({"a": 1.0, "b": 3.0})
    groups = {"a": "g", "b": "g"}
    quiet = {"a": np.zeros(200), "b": np.zeros(200)}
    spike = {"a": np.concatenate([np.zeros(100), np.full(100, 50.0)]),
             "b": np.concatenate([np.zeros(100), np.full(100, 90.0)])}
    assert (donor_rates(quiet, fitted, groups, None, 100)
            == donor_rates(spike, fitted, groups, None, 100))


# ---- 3. scoring mechanics ------------------------------------------------

def test_commitment_is_rate_times_lead_time_and_the_rest_is_held():
    """rate 2/day over a 10-day lead time commits 20; 6 units arrive, so 6
    are served and 14 sit."""
    series = {"s": np.concatenate([np.zeros(100), np.full(10, 0.6)])}
    df = score_donor(series, Products({"s": 10}), ["s"], 100, 10, lambda pid: 2.0)
    row = summarise(df, "r")
    assert row["units_served"] == pytest.approx(6.0)
    assert row["units_held"] == pytest.approx(14.0)


def test_committing_nothing_serves_nothing_and_holds_nothing():
    """The `none` arm is the current system: the flag, not a number."""
    series = {"s": np.concatenate([np.zeros(100), np.full(10, 1.0)])}
    df = score_donor(series, Products({"s": 10}), ["s"], 100, 10, lambda pid: None)
    row = summarise(df, "none")
    assert row["fill"] == 0.0
    assert row["units_held"] == 0.0


def test_a_cold_sku_that_sells_nothing_still_costs_held_stock():
    """The reason the scored set is ALL cold-start SKUs. Excluding the ones
    that turn out not to sell conditions on the outcome and flatters every
    donor rule by hiding stock it really commits."""
    series = {"dud": np.zeros(110)}
    df = score_donor(series, Products({"dud": 10}), ["dud"], 100, 10, lambda pid: 2.0)
    assert summarise(df, "r")["units_held"] == pytest.approx(20.0)


def test_only_whole_lead_time_blocks_are_scored():
    """25 days of window at a 10-day lead time is 2 blocks, not 2.5 - the
    same tiling validate_policy_holdout.score uses, so the fill rates here
    are read on the same axis as every other fill rate in the project."""
    series = {"s": np.concatenate([np.zeros(100), np.full(25, 1.0)])}
    df = score_donor(series, Products({"s": 10}), ["s"], 100, 25, lambda pid: 1.0)
    assert len(df) == 2


# ---- 4. the matched-stock control must be able to reject -----------------

def _frontier_fill_at(series, products, skus, split, horizon, base_rate, held):
    """The uncategorised control's fill at a given stock level, interpolated -
    the comparison the tool makes, reproduced here on synthetic data."""
    pts = []
    for scale in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
        row = summarise(score_donor(series, products, skus, split, horizon,
                                    lambda pid, s=scale: base_rate * s), "c")
        pts.append((row["units_held"], row["fill"]))
    pts.sort()
    h = np.array([p[0] for p in pts], dtype=float)
    f = np.array([p[1] for p in pts], dtype=float)
    return float(np.interp(held, h, f))


def test_the_control_rejects_a_label_whose_only_effect_is_more_stock():
    """A 'label' that simply commits 1.5x the global rate, uniformly, adds no
    information. It buys fill, so against a single unscaled comparison it
    looks like a win; against the matched-stock frontier its delta must be
    ~0 - which is what rejects product_type on the real data."""
    series = {f"s{i}": np.concatenate([np.zeros(100), np.full(20, 0.2 * (i + 1))])
              for i in range(6)}
    products = Products({f"s{i}": 10 for i in range(6)})
    skus, split, horizon, base = list(series), 100, 20, 1.0

    labelled = summarise(score_donor(series, products, skus, split, horizon,
                                     lambda pid: base * 1.5), "label")
    naked = summarise(score_donor(series, products, skus, split, horizon,
                                  lambda pid: base), "global")

    assert labelled["fill"] > naked["fill"]          # it DOES buy fill...
    at_same_stock = _frontier_fill_at(series, products, skus, split, horizon,
                                      base, labelled["units_held"])
    assert labelled["fill"] == pytest.approx(at_same_stock, abs=1e-9)   # ...and nothing more


def test_the_control_credits_a_label_that_really_allocates_better():
    """The mirror: a control that cannot say yes is as useless as one that
    cannot say no. Here the label gives each SKU its OWN rate rather than a
    shared one, which is real information, and it must land above the curve."""
    rates = {f"s{i}": 0.2 * (i + 1) for i in range(6)}
    series = {pid: np.concatenate([np.zeros(100), np.full(20, r)])
              for pid, r in rates.items()}
    products = Products({pid: 10 for pid in rates})
    skus, split, horizon, base = list(series), 100, 20, float(np.median(list(rates.values())))

    informed = summarise(score_donor(series, products, skus, split, horizon,
                                     lambda pid: rates[pid]), "informed")
    at_same_stock = _frontier_fill_at(series, products, skus, split, horizon,
                                      base, informed["units_held"])
    assert informed["fill"] > at_same_stock
