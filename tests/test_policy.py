"""
tests/test_policy.py
------------------------------------------------------------------
Pins the forecast -> prescriptive contract (forecasting/policy.py,
docs/PRESCRIPTIVE_CONTRACT.md).

These use small synthetic series, not ustore.db, so they run without a
live database and pin the MECHANISM rather than today's specific counts -
the same approach tests/test_category_leakage.py takes, and for the same
reason: the numbers will move when the data does, and a test that only
knows today's numbers stops protecting the reasoning the moment they do.

The three properties worth spending tests on, in order of how badly a
regression would hurt:

  1. **A SKU with no learnable rate never gets a number.** This is the
     whole point of the third state. A regression here does not produce a
     wrong forecast, it produces a confident one - a zero reorder point
     renders as "you have enough stock", which is a recommendation the
     pipeline has no evidence for.
  2. **Nothing fitted may see past its origin.** The cluster fit and the
     buffer quantile are both fitted quantities, and both are new surfaces
     for exactly the leak docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's
     correction section already caught once in this project.
  3. **The empirical buffer is better CALIBRATED than z*sigma**, which is
     the measured reason for the swap. Not "bigger" - on the real data it
     is frequently smaller and still serves more demand. Bigger would be
     easy and useless; calibrated is the claim.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.policy import (
    CLUSTER_POOLED, INSUFFICIENT, OBSERVED, cluster_rates, empirical_buffer,
    policy_fold_errors, resolve_rates, trailing_rate_fn, trailing_window,
)

WINDOW = 365
MIN_SALE_DAYS = 10


def _pool(n=24, n_days=900, seed=0):
    """A comparison pool of live SKUs with spread-out, stable rates.

    Clustering needs a real population to be meaningful - a handful of SKUs
    makes every cluster trivial and the pooled rate self-referential, which
    would let a broken implementation pass. Prices are positive and varied
    because sku_features takes log(price).
    """
    rng = np.random.default_rng(seed)
    series, prices = {}, {}
    for i in range(n):
        v = np.zeros(n_days)
        # every third day, a steady size - plenty of sale-days, so these are
        # all comfortably OBSERVED and none of them is the thing under test
        v[::3] = 1.0 + (i % 6)
        series[f"live{i}"] = v
        prices[f"live{i}"] = 50.0 + 25.0 * (i % 8)
    return series, prices


# ---- 1. the flag: a SKU with no learnable rate gets no number --------

def test_a_year_silent_sku_is_flagged_and_never_given_a_rate():
    """Sales long ago, nothing inside the trailing window. The rate is not
    zero, it is UNKNOWN, and the two must not render the same."""
    series, prices = _pool()
    n_days = 900
    dead = np.zeros(n_days)
    dead[10:40:2] = 8.0                    # a real selling history...
    series["dead"] = dead                  # ...entirely before the window
    prices["dead"] = 120.0

    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS)

    assert out["dead"]["rate_source"] == INSUFFICIENT
    assert out["dead"]["rate"] is None, (
        "a flagged SKU must carry None, not 0.0 - a zero reorder point reads "
        "as 'you have enough stock', which is a recommendation with no evidence")


def test_the_flag_survives_no_shrink():
    """`--no-shrink` disables the POOLING, not the FLAG. Turning off a
    variance-reduction step must not silently re-enable the silent zero."""
    series, prices = _pool()
    series["dead"] = np.zeros(900)
    prices["dead"] = 120.0

    for shrink in (True, False):
        out = resolve_rates(series, prices, window=WINDOW,
                            min_sale_days=MIN_SALE_DAYS, shrink=shrink)
        assert out["dead"]["rate_source"] == INSUFFICIENT
        assert out["dead"]["rate"] is None


def test_every_sku_is_accounted_for_in_exactly_one_state():
    """The partition property. The contract's claim is that no SKU is
    dropped, and that is only true if the three states cover the population
    exactly once - the old code's failure was a SKU falling through all of
    them and out of the output entirely."""
    series, prices = _pool()
    series["dead"] = np.zeros(900)
    prices["dead"] = 120.0
    thin = np.zeros(900)
    thin[-40] = 3.0
    series["thin"] = thin
    prices["thin"] = 90.0

    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS)

    assert set(out) == set(series), "a SKU went missing from the resolution"
    assert all(r["rate_source"] in (OBSERVED, CLUSTER_POOLED, INSUFFICIENT)
               for r in out.values())
    # ...and the flag is exactly the no-rate set, in both directions
    for sku, r in out.items():
        has_rate = r["rate"] is not None and r["rate"] > 0
        assert has_rate == (r["rate_source"] != INSUFFICIENT), (
            f"{sku}: rate presence and rate_source disagree")


# ---- 2. the pooled fallback ------------------------------------------

def test_a_thin_sku_is_shrunk_strictly_between_its_own_rate_and_its_cluster():
    """Shrinkage, not replacement. A thin rate carries SOME evidence and
    throwing it away entirely would be as wrong as trusting it fully.

    The thin SKU is given near-identical PEERS on purpose. Pooling is
    leave-one-out, so a SKU K-means isolates into a singleton has nobody to
    borrow from and correctly keeps its own rate - without peers here this
    test would be asserting against a no-op.
    """
    series, prices = _pool()
    thin = np.zeros(900)
    thin[-30] = 2.0                        # one sale-day in the window
    series["thin"] = thin
    prices["thin"] = 90.0
    # Peers shaped like `thin` - same sparse density, so K-means groups them
    # together - but selling far more when they do sell, so the leave-one-out
    # median sits well away from `thin`'s own rate and the interval the
    # shrinkage moves within is non-trivial.
    for i, qty in enumerate((10.0, 12.0, 14.0, 16.0)):
        peer = np.zeros(900)
        peer[-30 - i] = qty
        series[f"peer{i}"] = peer
        prices[f"peer{i}"] = 92.0 + i

    # k=2 so the sparse group and the dense group separate cleanly; the
    # property under test is the shrinkage arithmetic, not K-means' choices.
    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS, k=2)
    r = out["thin"]

    assert r["rate_source"] == CLUSTER_POOLED
    own, pooled = r["own_rate"], r["cluster_rate"]
    assert pooled is not None and pooled > 0
    lo, hi = sorted((own, pooled))
    assert lo <= r["rate"] <= hi, "the shrunk rate left the interval it shrinks within"
    assert r["rate"] != own, "nothing was actually shrunk"
    assert 0.0 < r["shrink_weight"] < 1.0


def test_a_singleton_cluster_borrows_nothing_and_is_not_mislabelled():
    """Leave-one-out, as a property. A SKU alone in its cluster has nobody
    to borrow strength from. It must keep its own rate AND must not be
    labelled `cluster_pooled` - labelling a no-op as a correction is how a
    component comes to look harmless when it is really absent."""
    series, prices = _pool()
    # a behavioural extreme: K-means will isolate it
    odd = np.zeros(900)
    odd[-5] = 5000.0
    series["odd"] = odd
    prices["odd"] = 100000.0

    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS)
    r = out["odd"]
    if r["cluster_rate"] is None:
        assert r["rate_source"] == OBSERVED
        assert r["rate"] == pytest.approx(r["own_rate"])
    else:
        # it landed with company; then it must genuinely move
        assert r["rate_source"] == CLUSTER_POOLED


def test_shrink_false_reproduces_the_raw_trailing_rate_exactly():
    """The isolated-variable guarantee. Without this the pooling could not
    be measured against its own absence, which is how the holdout
    adjudicates it."""
    series, prices = _pool()
    thin = np.zeros(900)
    thin[-30] = 2.0
    series["thin"] = thin
    prices["thin"] = 90.0

    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS,
                        shrink=False)
    for sku, r in out.items():
        if r["rate"] is None:
            continue
        assert r["rate_source"] == OBSERVED
        assert r["rate"] == pytest.approx(r["own_rate"], abs=0, rel=0)


def test_shrinkage_never_pulls_toward_a_cluster_with_no_rate():
    """The degeneracy guard inside the fallback. If a cluster has nothing to
    lend, the SKU keeps its own evidence - shrinking toward zero is exactly
    the collapse this contract exists to prevent, and it must not sneak back
    in through the pooling step."""
    n_days = 900
    series, prices = {}, {}
    for i in range(12):                    # a population of near-dead SKUs...
        v = np.zeros(n_days)
        v[5] = 1.0                         # ...all selling only before the window
        series[f"quiet{i}"] = v
        prices[f"quiet{i}"] = 40.0 + i
    thin = np.zeros(n_days)
    thin[-20] = 4.0
    series["thin"] = thin
    prices["thin"] = 99.0

    out = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS)
    r = out["thin"]
    assert r["rate"] == pytest.approx(r["own_rate"])
    assert r["rate"] > 0


# ---- 3. leakage: nothing fitted may see past its origin --------------

def test_cluster_fit_ignores_everything_after_upto():
    """The cluster labels and pooled rates are FITTED quantities, so they
    are a leakage surface. A wildly different future appended after `upto`
    must not change them at all."""
    series, prices = _pool(n_days=600)
    split = 400

    quiet = {sku: v.copy() for sku, v in series.items()}
    loud = {sku: v.copy() for sku, v in series.items()}
    for v in loud.values():
        v[split:] = 500.0                  # an enormous, uniform future

    lab_q, pooled_q = cluster_rates(quiet, prices, k=3, upto=split, window=WINDOW)
    lab_l, pooled_l = cluster_rates(loud, prices, k=3, upto=split, window=WINDOW)

    assert lab_q == lab_l, "cluster labels moved when only post-origin data changed"
    assert pooled_q == pytest.approx(pooled_l), "pooled rates saw past the origin"


def test_resolve_rates_ignores_everything_after_upto():
    series, prices = _pool(n_days=600)
    split = 400
    loud = {sku: v.copy() for sku, v in series.items()}
    for v in loud.values():
        v[split:] = 500.0

    a = resolve_rates(series, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS,
                      upto=split)
    b = resolve_rates(loud, prices, window=WINDOW, min_sale_days=MIN_SALE_DAYS,
                      upto=split)
    for sku in series:
        assert a[sku]["rate_source"] == b[sku]["rate_source"]
        assert a[sku]["rate"] == pytest.approx(b[sku]["rate"]) \
            if a[sku]["rate"] is not None else b[sku]["rate"] is None


def test_policy_fold_errors_ignores_everything_after_upto():
    """The buffer's input is a fitted quantity too."""
    rng = np.random.default_rng(3)
    v = np.zeros(600)
    v[::5] = rng.integers(1, 9, size=v[::5].size)
    split = 400

    blinded = v.copy()
    blinded[split:] = 0.0

    fn = trailing_rate_fn(WINDOW)
    a = policy_fold_errors(v, 14, fn, upto=split)
    b = policy_fold_errors(blinded, 14, fn, upto=split)

    assert a.size > 0, "no folds - this test would pass vacuously"
    assert np.allclose(a, b)


def test_trailing_window_ends_strictly_before_upto():
    v = np.arange(100.0)
    w = trailing_window(v, window=10, upto=50)
    assert w.tolist() == list(range(40, 50)), (
        "the window must end at upto-1; including index `upto` is a one-day leak")


# ---- 4. the buffer is expanding-window, and calibrated ---------------

def test_the_buffer_at_a_fold_cannot_see_later_folds():
    """Expanding window, strictly prior folds - the same rule
    tools/service_frontier.py's frontier uses, so the two remain
    comparable."""
    errors = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    tampered = errors.copy()
    tampered[3:] = 1000.0

    for i in range(1, len(errors)):
        assert empirical_buffer(errors[:i], 0.8) == empirical_buffer(tampered[:i], 0.8) \
            or i > 3


def test_the_first_fold_gets_a_zero_buffer_not_an_error():
    """No prior errors means no quantile to take. Zero, cleanly - the
    documented choice in tools/service_frontier.py, restated rather than
    re-decided."""
    assert empirical_buffer(np.array([]), 0.8) == 0.0


def test_the_buffer_is_never_negative():
    """A policy that over-predicts at this q gets no buffer, not negative
    stock."""
    assert empirical_buffer(np.array([-5.0, -3.0, -1.0]), 0.8) == 0.0


def test_the_empirical_buffer_is_better_calibrated_than_z_sigma():
    """The measured reason for the swap, as a property rather than a number.

    The claim is NOT that the empirical buffer is bigger - on the real data
    it is frequently smaller and still serves more demand. The claim is that
    it is CALIBRATED: a q-quantile buffer covers about q of the error
    distribution. z*sigma does not, on a right-skewed series, because it
    assumes a symmetry the data does not have.
    """
    rng = np.random.default_rng(11)
    # right-skewed, the shape intermittent demand errors actually take
    errors = rng.lognormal(mean=0.0, sigma=1.2, size=4000) - 1.0

    q = 0.80
    emp = empirical_buffer(errors, q)
    z80 = 0.8416                            # the normal quantile at the SAME q
    normal = z80 * float(np.std(errors, ddof=1))

    cover_emp = float(np.mean(errors <= emp))
    cover_normal = float(np.mean(errors <= normal))

    assert abs(cover_emp - q) < abs(cover_normal - q), (
        f"empirical coverage {cover_emp:.3f} vs normal {cover_normal:.3f} "
        f"against a {q} target - the empirical buffer is supposed to be the "
        f"better-calibrated one, which is the whole argument for the swap")
    assert abs(cover_emp - q) < 0.02


# ---- 5. the harness contract ----------------------------------------

def test_policy_fold_errors_reports_insufficiency_rather_than_scoring_what_it_can():
    """Same contract as walk_forward_evaluate: a series too short returns
    nothing rather than a one-fold answer dressed up as a distribution."""
    short = np.ones(70)
    out = policy_fold_errors(short, 30, trailing_rate_fn(WINDOW))
    assert out.size == 0


def test_errors_are_actual_minus_predicted():
    """Sign convention, pinned in both directions.

    A positive error is demand the policy did NOT plan for, so the upper
    quantile of errors is the stock that would have covered it. Flip the
    sign and the buffer silently becomes a discount - the failure would
    under-stock every SKU and still look like a working system.

    Asserted on the extremes of the error series rather than on a
    particular fold index: most folds of an intermittent series sit
    entirely inside a quiet stretch where actual and predicted are both
    zero and an error of 0.0 is correct, so `errors[0]` says nothing.
    """
    fn = trailing_rate_fn(WINDOW)

    ramps_up = np.zeros(400)
    ramps_up[200:] = 1.0                    # sells later, not earlier
    up = policy_fold_errors(ramps_up, 30, fn, min_train=60)
    assert up.size > 0, "no folds - this test would pass vacuously"
    assert up.max() > 0, (
        "a SKU selling MORE than its trailing rate predicted must produce a "
        "POSITIVE error")

    dies_off = np.zeros(400)
    dies_off[:200] = 1.0                    # sold earlier, stops
    down = policy_fold_errors(dies_off, 30, fn, min_train=60)
    assert down.size > 0, "no folds - this test would pass vacuously"
    assert down.min() < 0, (
        "a SKU selling LESS than its trailing rate predicted must produce a "
        "NEGATIVE error")


# ---- 6. service tiers ------------------------------------------------
# The tiering exists because a single population-wide operating point does
# two wrong things at once: it under-serves the demand that could be served
# cheaply, and it charges holding to chase demand no reorder point can
# cover. These pin the mechanism, not today's tier counts.

from forecasting.policy import (  # noqa: E402  (grouped with its own section)
    NOT_STOCKABLE, PARTIAL, SERVABLE, assign_service_tier, tier_curve,
)


def _dense(n_days=900, every=3, qty=5.0):
    """A SKU that sells often and predictably - servable by construction."""
    v = np.zeros(n_days)
    v[::every] = qty
    return v


def _lumpy(n_days=900, drip=40, lump=300.0):
    """A SKU that drips steadily - so a rate exists and stock gets committed
    every block - and then takes one lump far bigger than anything the buffer
    could carry. Served stays tiny while held accumulates, which is exactly the
    low-efficiency shape the floor is for.

    Note the drip is load-bearing: a SKU that sells ONLY in rare lumps has no
    demand inside the scored folds at all, so `tier_curve` returns [] and the
    SKU takes the no-evidence path instead. That is a different behaviour and
    `test_a_series_too_short_to_fold_is_not_written_off` covers it.
    """
    v = np.zeros(n_days)
    v[::drip] = 1.0
    v[n_days - 20] = lump
    return v


def test_a_dense_predictable_sku_is_servable_at_a_low_quantile():
    """The tiering's upside: a SKU whose demand is steady reaches the target
    without buying much buffer, and must be charged the SMALLEST quantile that
    gets there. Taking the largest would buy stock, not availability."""
    t = assign_service_tier(_dense(), 14, trailing_rate_fn(WINDOW))
    assert t["tier"] == SERVABLE
    assert t["q"] <= 0.80, (
        f"a steady SKU should reach the target cheaply, got q={t['q']} - the rule is the "
        f"SMALLEST qualifying quantile, not the safest-looking one")


def test_a_lumpy_sku_is_written_off_rather_than_expensively_stocked():
    """The tiering's whole point, and the floor shown doing the work.

    Asserted at an EXPLICIT floor on either side of this SKU's measured
    efficiency rather than against the shipped default. Whether the default
    happens to catch a particular synthetic is not the property worth pinning -
    that it is the efficiency floor, and not some other threshold, deciding the
    outcome is. The default's own calibration is a data question, measured on
    the real catalogue and recorded in docs/PRESCRIPTIVE_CONTRACT.md.
    """
    fn = trailing_rate_fn(WINDOW)
    v = _lumpy()
    curve = tier_curve(v, 14, fn)
    assert curve, "no curve - this test would be vacuous"
    best_eff = max(r["efficiency"] for r in curve)
    assert np.isfinite(best_eff), "an infinitely efficient SKU cannot exercise the floor"

    above = assign_service_tier(v, 14, fn, min_efficiency=best_eff * 2)
    assert above["tier"] == NOT_STOCKABLE
    assert above["q"] is None, "a not_stockable SKU must not carry an operating point"

    below = assign_service_tier(v, 14, fn, min_efficiency=best_eff / 2)
    assert below["tier"] != NOT_STOCKABLE
    assert below["q"] is not None


def test_not_stockable_is_decided_on_EFFICIENCY_not_on_fill():
    """Measured on the real catalogue and pinned here as a property: tiering
    on fill discards SKUs that are hard to serve but CHEAP to serve. A SKU
    with modest fill and almost no holding must survive the floor.

    Built so the two rules disagree: this SKU's achievable fill is low, but
    it achieves it on so little stock that its served-per-held is high.
    """
    fn = trailing_rate_fn(WINDOW)
    v = _lumpy(drip=25, lump=150.0)
    curve = tier_curve(v, 14, fn)
    assert curve, "no curve - this test would be vacuous"

    best_fill = max(r["fill"] for r in curve)
    best_eff = max(r["efficiency"] for r in curve)

    # a floor set ABOVE this SKU's efficiency writes it off...
    strict = assign_service_tier(v, 14, fn, min_efficiency=best_eff * 2)
    assert strict["tier"] == NOT_STOCKABLE
    # ...and one set BELOW keeps it, even though its fill never moved
    lenient = assign_service_tier(v, 14, fn, min_efficiency=best_eff / 2)
    assert lenient["tier"] in (PARTIAL, SERVABLE)
    assert lenient["q"] is not None
    assert best_fill == pytest.approx(max(r["fill"] for r in tier_curve(v, 14, fn)))


def test_max_q_caps_spending_but_not_measurement():
    """`best_fill` must stay honest about what the SKU could reach even when
    the cap forbids paying for it. Confusing the two would silently relabel an
    expensive SKU as unservable."""
    fn = trailing_rate_fn(WINDOW)
    v = _dense(every=9, qty=3.0)
    capped = assign_service_tier(v, 14, fn, max_q=0.50)
    uncapped = assign_service_tier(v, 14, fn, max_q=0.99)

    assert capped["best_fill"] == pytest.approx(uncapped["best_fill"]), (
        "the cap changed what the SKU is reported as ABLE to reach, not just what "
        "it is allowed to spend")
    if capped["q"] is not None:
        assert capped["q"] <= 0.50


def test_tier_assignment_sees_nothing_after_its_origin():
    """Tiers are a FITTED quantity, so they are a leakage surface - the same
    one docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's correction section caught
    once already. A wildly different future must not move the tier."""
    fn = trailing_rate_fn(WINDOW)
    base = _dense(n_days=600)
    split = 400

    quiet, loud = base.copy(), base.copy()
    loud[split:] = 900.0

    a = assign_service_tier(quiet, 14, fn, upto=split)
    b = assign_service_tier(loud, 14, fn, upto=split)
    assert a["tier"] == b["tier"]
    assert a["q"] == b["q"]
    assert a["best_fill"] == pytest.approx(b["best_fill"])


def test_tier_curve_buffer_at_a_fold_cannot_see_that_fold():
    """The curve's own expanding-window guarantee, checked end to end: append
    an enormous late fold and every EARLIER fold's contribution must be
    unchanged, which shows up as the shorter series being a prefix of the
    longer one's behaviour rather than being rescored."""
    fn = trailing_rate_fn(WINDOW)
    v = _dense(n_days=600)
    short = tier_curve(v[:500], 14, fn)
    same = tier_curve(v, 14, fn, upto=500)
    assert short, "no curve - this test would be vacuous"
    for a, b in zip(short, same):
        assert a["served"] == pytest.approx(b["served"])
        assert a["held"] == pytest.approx(b["held"])


def test_a_series_too_short_to_fold_is_not_written_off():
    """Absence of evidence is not evidence of unservability. A SKU with no
    scoreable folds gets the middle tier and the default operating point, not
    NOT_STOCKABLE - writing it off would be the silent-zero failure wearing a
    different hat."""
    t = assign_service_tier(np.ones(70), 30, trailing_rate_fn(WINDOW), default_q=0.8)
    assert t["tier"] == PARTIAL
    assert t["q"] == 0.8


def test_every_tier_decision_is_one_of_the_three_states():
    """The partition property, over a deliberately varied population."""
    fn = trailing_rate_fn(WINDOW)
    for v in (_dense(), _lumpy(), _dense(every=20, qty=1.0), np.zeros(900),
              _lumpy(drip=60, lump=500.0), np.ones(900)):
        t = assign_service_tier(v, 18, fn)
        assert t["tier"] in (SERVABLE, PARTIAL, NOT_STOCKABLE)
        assert (t["q"] is None) == (t["tier"] == NOT_STOCKABLE), (
            "an operating point must be present exactly when stock will be committed")
