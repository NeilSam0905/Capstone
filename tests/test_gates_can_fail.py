"""
tests/test_gates_can_fail.py
------------------------------------------------------------------
Proves the gates are gates.

The Batch 1 run shipped a bug worth generalising: `step5_prescriptive.py`
printed four `[PASS]` lines against zero priced SKUs, because "no N-class
rows" and "EOQ is the cost minimum" are trivially true of an empty table.
`all()` over an empty iterable is `True`; a loop over an empty list runs
zero assertions and reports success.

That shape was not local to A10. Several checks in test_evaluate.py had
it too: `for f in folds: assert ...` verifies nothing if `folds` is empty,
and one test was guarded by a literal `if folds:` which made the whole
assertion optional.

So the rule is: **every existence-negation assert gets a population assert
in front of it.** This file is the enforcement. For each gate touched, it
feeds a deliberately-emptied fixture and asserts the gate RAISES. A gate
that passes on empty input is not a gate, and a test that cannot fail is
not a test.
------------------------------------------------------------------
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from forecasting.evaluate import (
    DEFAULT_MIN_FOLDS, evaluate_methods, make_folds, summarise,
    walk_forward_evaluate,
)

import tests.test_evaluate as te


def mean_model(train, horizon):
    return np.full(horizon, train.mean() if train.size else 0.0)


# ---- the helper that enforces the rule in test_evaluate.py -----------

def test_rows_of_raises_on_an_insufficient_evaluation():
    """`rows_of` is what every `for row in ...` loop in test_evaluate.py
    now goes through. If it ever returned an empty list quietly, all of
    those loops would go back to passing vacuously."""
    ev = walk_forward_evaluate("thin", np.ones(50), mean_model, "mean")
    assert ev.sufficient is False
    with pytest.raises(AssertionError):
        te.rows_of(ev)


def test_rows_of_accepts_a_real_evaluation():
    ev = walk_forward_evaluate("ok", np.arange(400.0), mean_model, "mean")
    rows = te.rows_of(ev)
    assert len(rows) == ev.n_folds >= DEFAULT_MIN_FOLDS


# ---- the leakage gate ------------------------------------------------

def test_leakage_loop_would_be_vacuous_without_its_population_assert():
    """A series too short to fold produces []. The old form of the leakage
    test iterated over exactly this and reported success."""
    folds = make_folds(50, horizon=30, min_folds=1, min_train=30)
    assert folds == []

    # the loop body never executes - this is the vacuous pass, demonstrated
    executed = sum(1 for _ in folds)
    assert executed == 0

    # ...which is why the real test now asserts the population first
    with pytest.raises(AssertionError):
        assert len(folds) > 0, "no folds to check - this test would pass vacuously"


def test_fold_leakage_check_actually_raises_on_a_leaky_fold():
    """The gate must fire on a genuinely bad fold, not merely on empties."""
    from forecasting.evaluate import Fold
    with pytest.raises(ValueError, match="past the series"):
        Fold(0, train_end=90, horizon=30).assert_no_leakage(n_total=100)
    with pytest.raises(ValueError, match="empty training window"):
        Fold(0, train_end=0, horizon=30).assert_no_leakage(n_total=100)


# ---- the minimum-folds gate ------------------------------------------

def test_min_folds_gate_rejects_a_series_that_can_only_support_one_fold():
    assert make_folds(100, horizon=30, min_folds=3, min_train=60) == []
    ev = walk_forward_evaluate("thin", np.ones(100), mean_model, "mean")
    assert ev.sufficient is False
    assert ev.n_folds == 0
    assert ev.rows == []


def test_an_all_thin_population_produces_no_results_not_a_silent_pass():
    """Every SKU too short: the results frame is empty, and any
    'all SKUs have >= 3 folds' check over it is vacuously true."""
    data = {f"thin_{i}": np.ones(80) for i in range(5)}
    results, insufficient = evaluate_methods(data, {"mean": mean_model})

    assert results.empty
    assert len(insufficient) == 5

    # the vacuous form passes...
    assert all(g["fold"].nunique() >= 3 for _, g in results.groupby("sku"))
    # ...so the population assert is what makes it a gate
    with pytest.raises(AssertionError):
        assert not results.empty


# ---- the identical-folds gate ---------------------------------------

def test_identical_folds_check_cannot_see_a_missing_method():
    """`nunique() == 1` per SKU stays true when a method contributes NO
    rows, so the count of methods per SKU has to be asserted separately.
    This is the hole model_benchmark.py's gate now closes."""
    data = {"a": np.arange(400.0)}
    results, _ = evaluate_methods(data, {"mean": mean_model, "zero": lambda t, h: np.zeros(h)})
    assert results["method"].nunique() == 2

    # drop one method entirely, as a silent failure would
    crippled = results[results["method"] == "mean"]

    layouts = crippled.groupby(["sku", "method"])["origin"].apply(lambda s: tuple(sorted(s)))
    per_sku = layouts.groupby("sku").nunique()
    assert not (per_sku != 1).any()          # the old gate still says "fine"

    # the population assert is what catches it
    methods_per_sku = crippled.groupby("sku")["method"].nunique()
    assert (methods_per_sku != 2).any()


# ---- summarise -------------------------------------------------------

def test_summarise_of_an_empty_frame_is_empty_not_a_fabricated_ranking():
    out = summarise(pd.DataFrame())
    assert out.empty
    assert len(out) == 0


# ---- the prescriptive gates -----------------------------------------

@pytest.fixture
def empty_prescriptive_db(tmp_path):
    """A schema-correct database with an empty Result_Prescriptive - the
    exact state that produced four false PASS lines in Batch 1."""
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, fsn_class TEXT);
        CREATE TABLE Dim_Parameters (parameter_id INTEGER PRIMARY KEY,
                                     parameter_name TEXT, value REAL, unit TEXT,
                                     last_updated TEXT);
        CREATE TABLE Result_Prescriptive (
            result_id INTEGER PRIMARY KEY, product_id INTEGER, fsn_class TEXT,
            lead_time_days INTEGER, cost_ratio REAL, avg_daily_demand REAL,
            annual_demand REAL, sigma_demand REAL, sigma_source TEXT,
            z_value REAL, safety_stock REAL, reorder_point REAL, eoq REAL,
            cost_at_eoq REAL, cost_at_half_eoq REAL, cost_at_double_eoq REAL,
            demand_method TEXT, is_provisional INTEGER, generated_at TEXT);
    """)
    con.commit()
    return con


def test_the_batch1_bug_reproduces_on_an_empty_table(empty_prescriptive_db):
    """All four original gates pass against zero rows. This is the bug,
    pinned, so nobody re-introduces the un-guarded form."""
    con = empty_prescriptive_db
    for sql in (
        "SELECT COUNT(*) FROM Result_Prescriptive WHERE fsn_class NOT IN ('F','S')",
        "SELECT COUNT(*) FROM Result_Prescriptive WHERE cost_at_eoq >= cost_at_half_eoq",
        "SELECT COUNT(*) FROM Result_Prescriptive WHERE cost_at_eoq >= cost_at_double_eoq",
        "SELECT COUNT(*) FROM Result_Prescriptive WHERE is_provisional != 1",
    ):
        assert con.execute(sql).fetchone()[0] == 0      # "PASS", against nothing


def test_the_non_emptiness_gate_catches_it(empty_prescriptive_db):
    """The guard that now runs first, and short-circuits the rest."""
    con = empty_prescriptive_db
    n_rows = con.execute("SELECT COUNT(*) FROM Result_Prescriptive").fetchone()[0]
    assert n_rows == 0
    with pytest.raises(AssertionError):
        assert n_rows > 0, "Result_Prescriptive is empty"


def test_step5_gate_function_returns_failure_on_an_empty_table(empty_prescriptive_db):
    """End-to-end: run the real gate function against the empty database
    and require a non-zero return code."""
    import step5_prescriptive
    assert step5_prescriptive.run_gates(empty_prescriptive_db) == 1


def test_step5_gates_pass_on_the_real_database():
    """The complement: the same function must return 0 on a populated
    database, or the test above proves nothing about the gate's aim."""
    import os

    import step5_prescriptive
    if not os.path.exists("ustore.db"):
        pytest.skip("ustore.db not built")
    con = sqlite3.connect("ustore.db")
    n = con.execute("SELECT COUNT(*) FROM Result_Prescriptive").fetchone()[0]
    if n == 0:
        pytest.skip("step5_prescriptive.py has not been run")
    assert step5_prescriptive.run_gates(con) == 0
    con.close()


# ---- the invariant contract -----------------------------------------

def test_assert_invariants_fails_on_an_empty_database(tmp_path):
    """The contract gate must not pass against a database with the right
    tables and no data."""
    import subprocess
    import sys

    db = tmp_path / "hollow.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT,
                               semester_week INTEGER, is_tally_date INTEGER);
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT,
                                  fsn_class TEXT, is_hvl INTEGER);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER,
                                 date_id INTEGER, quantity_sold INTEGER);
        CREATE TABLE Dim_Parameters (parameter_id INTEGER PRIMARY KEY, unit TEXT);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY);
        CREATE TABLE Exception_Log (id INTEGER PRIMARY KEY);
    """)
    con.commit()
    con.close()

    r = subprocess.run(
        [sys.executable, "tools/assert_invariants.py", "--db", str(db)],
        capture_output=True, text=True)
    assert r.returncode == 1, "the contract gate passed against an empty database"
    assert "FAIL" in r.stdout
