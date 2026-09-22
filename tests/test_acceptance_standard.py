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

One of the conditions FAILS against the live data today (forward demand
coverage, 0.8830 against an a priori 0.90). That is deliberate and it is
not repaired here. A test suite that quietly asserted the live system
passes would be the same mistake as choosing a threshold the system
clears.
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


def test_condition_3_fails_when_a_simpler_policy_dominates():
    """More service for less stock. If that exists, the complexity is
    unjustified and the condition must say so."""
    c = _checker()
    acc.condition_3_not_dominated(_comparison(0.75, 12000.0), c)
    assert c.failures, "a dominating alternative existed and the condition passed"


def test_condition_3_tolerates_a_genuine_trade_off():
    """More service at MORE stock is a trade, not a domination - the naive
    baseline sits on the other side of exactly this line."""
    c = _checker()
    acc.condition_3_not_dominated(_comparison(0.80, 30000.0), c)
    assert c.failures == []


def test_condition_3_is_not_vacuous_with_nothing_to_compare_against():
    c = _checker()
    acc.condition_3_not_dominated(pd.DataFrame([
        {"scope": "TIERED", "fill_rate": 0.68, "units_held": 14000.0}]), c)
    assert c.failures, "a comparison against nothing reported a pass"


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
