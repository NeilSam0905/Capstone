"""
tests/test_priced_share_warning.py
------------------------------------------------------------------
step5's priced-share floor (MIN_PRICED_SHARE) as a reported check rather than
a run-stopping error, when it is the only gate that fails:

  - the gate itself is unchanged: run_gates() still returns 1;
  - step5 exits EXIT_PRICED_SHARE_SHORT only when every other gate passed and
    at least one SKU was priced. A table that prices nothing at all (the
    collapse the floor was added to catch) and any other failed gate keep
    exit code 1;
  - the pipeline records that exit as "warning" for the step and the run, the
    staleness check counts a warning run as completed, and any other non-zero
    exit from step5 is still an error.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline  # noqa: E402  (backend/pipeline.py)
import step5_prescriptive as s5  # noqa: E402

RESULT_DDL = """
    CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, fsn_class TEXT);
    CREATE TABLE Dim_Parameters (parameter_id INTEGER PRIMARY KEY, parameter_name TEXT,
                                 value REAL, unit TEXT, last_updated TEXT);
    CREATE TABLE Result_Prescriptive (
        result_id INTEGER PRIMARY KEY, product_id INTEGER, fsn_class TEXT,
        lead_time_days INTEGER, cost_ratio REAL, avg_daily_demand REAL,
        annual_demand REAL, sigma_demand REAL, sigma_source TEXT,
        z_value REAL, safety_stock REAL, reorder_point REAL, eoq REAL,
        cost_at_eoq REAL, cost_at_half_eoq REAL, cost_at_double_eoq REAL,
        demand_method TEXT, is_provisional INTEGER, generated_at TEXT,
        ordering_cost_scenario TEXT, ordering_cost_php REAL,
        rate_source TEXT, service_tier TEXT, buffer_quantile REAL,
        buffer_source TEXT, safety_stock_normal_legacy REAL);
"""


def prescriptive_db(tmp_path, n_skus, n_priced, safety_stock=2.0):
    """`n_priced` of `n_skus` SKUs priced with rows that satisfy every other
    gate (both ordering-cost scenarios, the class's Z, EOQ's 1.25 cost curve,
    a service tier and a buffer source), the rest flagged insufficient_data."""
    con = sqlite3.connect(tmp_path / "p.db")
    con.executescript(RESULT_DDL)
    rows = []
    for pid in range(1, n_skus + 1):
        if pid > n_priced:
            rows.append((pid, "S", None, None, None, None, None, None, None,
                         "not_priced", 0.0, s5.INSUFFICIENT, None, None, None))
            continue
        for scenario, cost in s5.ORDERING_COST_SCENARIOS.items():
            rows.append((pid, "F", s5.Z_BY_CLASS["F"], safety_stock, 20.0, 50.0,
                         100.0, 125.0, 125.0, scenario, cost, s5.OBSERVED,
                         s5.SERVABLE, 0.8, s5.BUFFER_EMPIRICAL))
    con.executemany(
        "INSERT INTO Result_Prescriptive (product_id, fsn_class, z_value, safety_stock, "
        "reorder_point, eoq, cost_at_eoq, cost_at_half_eoq, cost_at_double_eoq, "
        "ordering_cost_scenario, ordering_cost_php, rate_source, service_tier, "
        "buffer_quantile, buffer_source, lead_time_days, is_provisional) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 18, 1)", rows)
    con.commit()
    return con


def exit_code(con):
    gates = {}
    rc = s5.run_gates(con, gates)
    return rc, s5.gate_exit_code(rc, gates)


# ---- step5's exit code -------------------------------------------------

def test_a_short_priced_share_alone_is_reported_not_an_error(tmp_path):
    con = prescriptive_db(tmp_path, n_skus=20, n_priced=15)      # 75% < 78%
    rc, code = exit_code(con)
    assert rc == 1                       # the gate still fails, unchanged
    assert code == s5.EXIT_PRICED_SHARE_SHORT != 0


def test_a_priced_share_over_the_floor_passes(tmp_path):
    con = prescriptive_db(tmp_path, n_skus=20, n_priced=16)      # 80%
    assert exit_code(con) == (0, 0)


def test_nothing_priced_is_still_an_error(tmp_path):
    con = prescriptive_db(tmp_path, n_skus=20, n_priced=0)
    assert exit_code(con) == (1, 1)


def test_another_failed_gate_is_still_an_error(tmp_path):
    # ROP below safety stock fails its own gate as well as the short share
    con = prescriptive_db(tmp_path, n_skus=20, n_priced=15, safety_stock=30.0)
    assert exit_code(con) == (1, 1)


def test_an_empty_table_is_still_an_error(tmp_path):
    con = sqlite3.connect(tmp_path / "e.db")
    con.executescript(RESULT_DDL)
    assert exit_code(con) == (1, 1)


def test_the_floor_is_unchanged():
    assert s5.MIN_PRICED_SHARE == 0.78


def test_the_pipeline_and_step5_agree_on_the_exit_code():
    assert pipeline.WARNING_EXIT == {"step5": s5.EXIT_PRICED_SHARE_SHORT}


# ---- the pipeline ------------------------------------------------------

STEP5_STDOUT = ("=== gates ===\n"
                "       priced share = 223/292 = 0.7637 (floor 0.78)\n"
                "[FAIL] priced share >= 0.78 (non-degeneracy)      False   != expected True\n"
                "FAILED: 1 gate(s).\n")


@pytest.fixture
def run_all(monkeypatch):
    """pipeline._run_all with every step's process replaced: step5 exits with
    the code given, every other step exits 0. Nothing is run, decrypted or
    written."""
    monkeypatch.setattr(pipeline, "_state", {"status": "running", "run_id": None,
                                             "steps": pipeline._fresh_steps(), "vault": None})
    monkeypatch.setattr(pipeline.vault, "ensure_unlocked", lambda: None)
    monkeypatch.setattr(pipeline.vault, "MANIFEST", Path("no-such-manifest.json"))
    monkeypatch.setattr(pipeline.dbmod, "ensure_initialised", lambda **k: None)
    monkeypatch.setattr(pipeline, "_finish_run_record", lambda *a: None)

    def run(step5_code):
        def fake_step(script, entry, timeout_s):
            if script.endswith("step5_prescriptive.py"):
                return step5_code, STEP5_STDOUT, "", False
            return 0, "", "", False
        monkeypatch.setattr(pipeline, "_run_step", fake_step)
        pipeline._run_all()
        return pipeline._state, next(s for s in pipeline._state["steps"] if s["id"] == "step5")
    return run


def test_a_short_priced_share_finishes_the_run_with_a_warning(run_all):
    state, step5 = run_all(s5.EXIT_PRICED_SHARE_SHORT)
    assert state["status"] == "warning"
    assert step5["status"] == "warning"
    assert "223 of 292 items (76.4%)" in step5["error"]
    assert "at least 78%" in step5["error"]
    assert "other 69" in step5["error"]


def test_any_other_step5_failure_is_still_an_error(run_all):
    state, step5 = run_all(1)
    assert state["status"] == "error"
    assert step5["status"] == "error"


def test_a_clean_step5_is_done(run_all):
    state, step5 = run_all(0)
    assert state["status"] == "done"
    assert step5["status"] == "done"


def test_a_warning_run_counts_as_completed_for_the_staleness_check():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE Pipeline_Run (run_id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
            status TEXT, trigger_source TEXT, steps_ok INTEGER, steps_skipped INTEGER,
            steps_failed INTEGER, max_sale_id INTEGER, max_event_id INTEGER, max_closure_id INTEGER);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, tally_date_flag INTEGER);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY);
        INSERT INTO Fact_Sales VALUES (1, 0);
        INSERT INTO Pipeline_Run (started_at, finished_at, status, max_sale_id, max_event_id, max_closure_id)
            VALUES ('2026-11-05T09:56:32', '2026-11-05T09:58:06', 'warning', 1, 0, 0);
    """)
    st = pipeline.get_staleness(con)
    assert st["interrupted"] is None
    assert st["stale"] is False
    assert st["last_run"]["status"] == "warning"
