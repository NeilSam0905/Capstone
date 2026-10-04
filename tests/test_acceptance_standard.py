"""
tests/test_acceptance_standard.py
------------------------------------------------------------------
Proves the acceptance standard is a standard.

`MAPE <= 20%` could fail, and did. Any criterion proposed to replace it
inherits that obligation: if it cannot fail, it is not a criterion, it is
a certificate. So every condition in tools/acceptance_standard.py is fed a
fixture built to break it here, and required to break.

This is the same rule tests/test_gates_can_fail.py enforces for the
pipeline gates, applied to the acceptance criterion itself - which is the
one place it matters most, because that criterion is what the project's
central claim rests on.

One of the conditions FAILS against the live data today. It used to be
forward demand coverage (0.8830 against an a priori 0.90); since bf08ca7
corrected the rate denominators that one passes at 0.9080 and condition 3
fails instead, because flat q=0.85 dominates the tiered policy. Which one
is red is not the point and is not repaired here. A test suite that
quietly asserted the live system passes would be the same mistake as
choosing a threshold the system clears.
------------------------------------------------------------------
"""
import sqlite3

import pandas as pd
import pytest

import acceptance_standard as acc   # conftest.py puts tools/ on sys.path


def _checker():
    return acc.Checker()


# ---- condition 1: actionability --------------------------------------

@pytest.fixture
def db():
    """A schema-correct database with one priced and one flagged SKU."""
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT);
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, fsn_class TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER,
                                 date_id INTEGER, quantity_sold INTEGER);
        CREATE TABLE Result_Prescriptive (result_id INTEGER PRIMARY KEY,
            product_id INTEGER, rate_source TEXT, reorder_point REAL, eoq REAL);
        INSERT INTO Dim_Date VALUES (1, '2026-07-31');
        INSERT INTO Dim_Product VALUES (1, 'F'), (2, 'S');
        INSERT INTO Fact_Sales VALUES (1, 1, 1, 10), (2, 2, 1, 5);
        INSERT INTO Result_Prescriptive VALUES
            (1, 1, 'observed', 20.0, 100.0),
            (2, 2, 'insufficient_data', NULL, NULL);
    """)
    con.commit()
    return con


def test_condition_1_passes_on_a_well_formed_database(db):
    """The complement first: without this, every failure test below could be
    passing for the wrong reason."""
    c = _checker()
    acc.condition_1_actionability(db, c)
    assert c.failures == [], f"expected a clean pass, got {c.failures}"


def test_condition_1_fails_when_a_flagged_sku_emits_a_reorder_point(db):
    """The silent zero, which is the specific failure the whole contract
    exists to prevent: a SKU with no learnable rate rendering as though it
    had one."""
    db.execute("UPDATE Result_Prescriptive SET reorder_point = 0.0 "
               "WHERE rate_source = 'insufficient_data'")
    c = _checker()
    acc.condition_1_actionability(db, c)
    assert c.failures, "a flagged SKU emitted a reorder point and the condition passed"


def test_condition_1_fails_when_an_eligible_sku_has_no_state(db):
    """A SKU dropped from the output entirely - the pre-contract behaviour."""
    db.execute("DELETE FROM Result_Prescriptive WHERE product_id = 2")
    c = _checker()
    acc.condition_1_actionability(db, c)
    assert c.failures, "an eligible SKU vanished from the output and the condition passed"


def test_condition_1_is_not_vacuous_on_an_empty_population(db):
    """Population assert first: with no eligible SKUs every check below is
    trivially satisfied, so the condition must stop rather than report a pass."""
    db.execute("DELETE FROM Fact_Sales")
    c = _checker()
    acc.condition_1_actionability(db, c)
    assert c.failures, "an empty population reported a pass"


# ---- condition 1b: forward coverage ----------------------------------

def _origins(coverage=(0.99, 0.98), observed=(0.99, 0.98), policy=(0.7, 0.8),
             naive=(0.4, 0.5)):
    n = len(coverage)
    return pd.DataFrame({
        "origin": [f"2026-0{i + 1}-01" for i in range(n)],
        "fit_observed_share": list(observed),
        "fill_rate": list(policy),
        "naive_fill": list(naive),
        "forward_coverage": list(coverage),
        "window_demand_all_skus": [1000.0] * n,
        "flagged_demand": [1000.0 * (1 - cv) for cv in coverage],
    })


def test_condition_1b_fails_when_flagged_skus_carry_the_demand():
    """The condition that fails on live data. A system whose flagged tail
    turns out to carry the trade has not covered the catalogue, however
    tidily it reports the rest."""
    c = _checker()
    acc.condition_1b_forward_coverage(_origins(coverage=(0.60, 0.55)), c)
    assert c.failures, "flagged SKUs carried 40% of demand and the condition passed"


def test_condition_1b_passes_when_coverage_is_genuinely_high():
    c = _checker()
    acc.condition_1b_forward_coverage(_origins(coverage=(0.99, 0.98)), c)
    assert c.failures == []


def test_condition_1b_cannot_be_satisfied_by_the_trailing_tautology():
    """Guards the bug this condition was rewritten to remove.

    A first draft measured coverage on the TRAILING window and scored a
    perfect 1.0000 - which looked like a pass and was arithmetic: a SKU is
    flagged precisely because its trailing window is empty, so flagged SKUs
    contribute zero to trailing demand by construction. The forward form
    must be able to report something other than 1.0, or the same trap has
    been rebuilt.
    """
    df = _origins(coverage=(0.75, 0.75))
    assert (df["forward_coverage"] < 1.0).all()
    c = _checker()
    acc.condition_1b_forward_coverage(df, c)
    assert c.failures


# ---- condition 2: beats the no-model alternative ---------------------

def test_condition_2_fails_when_naive_wins_at_any_origin():
    """'Every origin', not 'on average'. A policy that wins on average and
    loses in some quarters is not one a store can rely on."""
    c = _checker()
    acc.condition_2_beats_no_model(
        _origins(policy=(0.70, 0.40), naive=(0.40, 0.55)), c)
    assert c.failures, "naive won an origin and the condition passed"


def test_condition_2_fails_when_there_is_no_baseline_to_beat():
    """Evidence without a comparator cannot support the claim."""
    df = _origins().drop(columns=["naive_fill"])
    c = _checker()
    acc.condition_2_beats_no_model(df, c)
    assert c.failures


def test_condition_2_passes_when_the_policy_wins_everywhere():
    c = _checker()
    acc.condition_2_beats_no_model(_origins(policy=(0.70, 0.80), naive=(0.40, 0.50)), c)
    assert c.failures == []


# ---- condition 3: not dominated --------------------------------------

def _comparison(rival_fill, rival_held):
    return pd.DataFrame([
        {"scope": "TIERED", "fill_rate": 0.68, "units_held": 14000.0},
        {"scope": "rival", "fill_rate": rival_fill, "units_held": rival_held},
    ])


def _frontier(*points):
    """A flat-quantile sweep, as validate_policy_holdout.py writes it."""
    return pd.DataFrame([{"q": q, "fill_rate": f, "units_held": h}
                         for q, f, h in points])


def test_condition_3_fails_when_a_simpler_policy_dominates():
    """More service for less stock. If that exists, the complexity is
    unjustified and the condition must say so."""
    c = _checker()
    acc.condition_3_not_dominated(_comparison(0.75, 12000.0), None, None, c)
    assert c.failures, "a dominating alternative existed and the condition passed"


def test_condition_3_tolerates_a_genuine_trade_off():
    """More service at MORE stock is a trade, not a domination - the naive
    baseline sits on the other side of exactly this line."""
    c = _checker()
    acc.condition_3_not_dominated(_comparison(0.80, 30000.0), None, None, c)
    assert c.failures == []


def test_condition_3_is_not_vacuous_with_nothing_to_compare_against():
    c = _checker()
    acc.condition_3_not_dominated(pd.DataFrame([
        {"scope": "TIERED", "fill_rate": 0.68, "units_held": 14000.0}]), None, None, c)
    assert c.failures, "a comparison against nothing reported a pass"


def test_condition_3_sees_a_dominator_the_comparison_table_never_names():
    """The regression this pair of arguments exists for.

    Every named arm is a genuine trade-off, so the old condition - which read
    the comparison table alone - passed. A quantile nobody put in that table
    dominates on both axes. This is not hypothetical: flat q=0.85 did exactly
    this to the live policy once bf08ca7 corrected the buffers, and condition 3
    reported "alternatives that dominate this policy: 0" while it happened.
    """
    c = _checker()
    acc.condition_3_not_dominated(
        _comparison(0.80, 30000.0),                  # a trade-off, as before
        _frontier((0.80, 0.60, 11000.0),             # less fill, less stock
                  (0.85, 0.70, 13000.0),             # MORE fill, LESS stock
                  (0.95, 0.90, 40000.0)),            # more fill, far more stock
        None, c)
    assert c.failures, "a frontier point dominated and the condition passed"


def test_condition_3_does_not_invent_a_dominator_from_the_frontier():
    """The other direction - a frontier where every point is an honest trade
    must still pass, or the widened condition is just noisier."""
    c = _checker()
    acc.condition_3_not_dominated(
        _comparison(0.80, 30000.0),
        _frontier((0.70, 0.55, 9000.0), (0.80, 0.62, 12000.0),
                  (0.90, 0.80, 25000.0)),
        None, c)
    assert c.failures == []


def test_condition_3_does_not_double_count_an_arm_the_table_already_names():
    """The comparison table's `flat q=0.80 (pre-tiering)` and the frontier's
    q=0.80 row are the same measurement. Listing it twice would not change the
    verdict, but it would misreport how many rivals were considered."""
    c = _checker()
    comparison = pd.DataFrame([
        {"scope": "TIERED", "fill_rate": 0.68, "units_held": 14000.0},
        {"scope": "flat q=0.80 (pre-tiering)", "fill_rate": 0.62, "units_held": 12000.0},
    ])
    acc.condition_3_not_dominated(comparison, _frontier((0.80, 0.62, 12000.0)), None, c)
    assert c.failures == []


def _dominance_by_origin(*dominator_counts):
    """The rolling-origins table as validate_policy_holdout.py writes it, cut
    down to what condition 3 reads: one row per origin carrying how many
    frontier quantiles dominated the tiering there.

    Named apart from `_origins` above, which builds the coverage/fill columns
    conditions 1b, 2 and 4 read - same CSV, different slice of it.
    """
    return pd.DataFrame([
        {"origin": f"origin-{i}", "n_frontier_dominators": n,
         "dominated_by_q": "0.85" if n else ""}
        for i, n in enumerate(dominator_counts)])


def test_condition_3_gates_on_the_origins_not_the_development_set():
    """The fix for the basis, not just the comparison set.

    Everything in the comparison table and the flat frontier is scored at one
    split - the most recent window, which the holdout's own docstring calls a
    development set. Here that window shows a dominator and the four rolling
    origins mostly do not: the condition must follow the origins, or a 1.5%
    margin on the window the policy was designed on decides the verdict. This
    is the live 2026-09-30 shape - dominated at 1 of 4, efficient at 3.
    """
    c = _checker()
    acc.condition_3_not_dominated(
        _comparison(0.80, 30000.0),
        _frontier((0.85, 0.70, 13000.0)),        # dominates on the dev set
        _dominance_by_origin(1, 0, 0, 0),                    # but only at 1 of 4 origins
        c)
    assert c.failures == [], "a single-origin dominance decided the verdict"


def test_condition_3_fails_when_most_origins_are_dominated():
    """And the gate still bites where it should: a dominance that shows up
    across the clean windows is evidence about the policy, not the window."""
    c = _checker()
    acc.condition_3_not_dominated(
        _comparison(0.80, 30000.0),
        _frontier((0.85, 0.70, 13000.0)),
        _dominance_by_origin(1, 1, 1, 0),                    # 3 of 4
        c)
    assert c.failures, "a majority of origins were dominated and it passed"


def test_condition_3_falls_back_to_the_development_set_without_origins():
    """An origins table from before the per-origin frontier existed must not
    silently pass - the weaker basis is used and announced."""
    c = _checker()
    acc.condition_3_not_dominated(
        _comparison(0.80, 30000.0),
        _frontier((0.85, 0.70, 13000.0)),
        pd.DataFrame([{"origin": "old", "fill_rate": 0.6}]),   # no dominator column
        c)
    assert c.failures, "the fallback basis reported a pass with a dominator present"


# ---- condition 4: evidence integrity ---------------------------------

def test_condition_4_fails_when_observability_is_not_reported():
    """A figure quoted without the share of it that is evidence."""
    df = _origins().drop(columns=["fit_observed_share"])
    c = _checker()
    acc.condition_4_evidence_integrity(df, c)
    assert c.failures


def test_condition_4_fails_when_every_window_is_mostly_assumption():
    """At least one headline window must rest on evidence. If they all sit
    below the bar there is nothing to headline."""
    c = _checker()
    acc.condition_4_evidence_integrity(_origins(observed=(0.55, 0.60)), c)
    assert c.failures, "every window was mostly assumption and the condition passed"


def test_condition_4_passes_while_disclosing_a_weak_window():
    """One window below the bar is reported and labelled, not dropped -
    excluding an unfavourable window would be selection, which is a worse
    failure than the low observability itself."""
    c = _checker()
    acc.condition_4_evidence_integrity(_origins(observed=(0.98, 0.80)), c)
    assert c.failures == []


# ---- the standard as a whole -----------------------------------------

def test_the_thresholds_are_constants_not_computed_from_the_data():
    """The a priori property, enforced structurally.

    If a threshold were ever derived from a measurement it would stop being
    a criterion and become a description. These are module-level constants
    with no data in scope; this test pins that they are plain numbers and
    that nobody has quietly replaced one with a call.
    """
    for name in ("MIN_DEMAND_COVERAGE", "MIN_OBSERVED_SHARE"):
        v = getattr(acc, name)
        assert isinstance(v, float) and 0.0 < v < 1.0, f"{name} is not a plain fraction"
    assert acc.BEAT_NAIVE_AT_EVERY_ORIGIN is True
    assert acc.ALLOW_DOMINATION is False
