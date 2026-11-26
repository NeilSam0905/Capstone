"""
tests/test_qa_fixes.py
------------------------------------------------------------------
Regression tests for the faults the November 2026 quality checks found
(docs/QUALITY_CHECK_PLAN.md): quantity validation (QA-016, QA-019), date
and threshold checks (QA-013, QA-015), the admin lockout window (QA-036),
an abandoned pipeline run (QA-037), an unreadable tally workbook (QA-034)
and changing a password (QA-026).
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import openpyxl
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth  # noqa: E402
import decision_rules  # noqa: E402
import pipeline  # noqa: E402
import set_password  # noqa: E402
import step0_convert_sales_with_zeros as step0  # noqa: E402
import validation  # noqa: E402


# ---- QA-016 / QA-019: quantities ------------------------------------------

@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "abc", "1.5", "0", "-1", 10**12])
def test_a_bad_quantity_is_refused_not_crashed(raw):
    n, problem = validation.whole_number(raw, "Quantity", 1, validation.MAX_ENTRY_UNITS)
    assert n is None and problem


def test_quantities_up_to_the_cap_are_accepted():
    cap = validation.MAX_ENTRY_UNITS
    assert validation.whole_number("3", "Quantity", 1, cap) == (3, None)
    assert validation.whole_number(cap, "Quantity", 1, cap) == (cap, None)
    assert validation.whole_number(cap + 1, "Quantity", 1, cap)[0] is None
    # a stock count may be zero
    assert validation.whole_number(0, "Units on hand", 0, validation.MAX_COUNT_UNITS) == (0, None)


# ---- QA-013 / QA-015: dates and the threshold ------------------------------

def test_a_month_must_be_a_real_month():
    assert validation.is_iso_month("2026-11")
    assert not validation.is_iso_month("2026-13")
    assert not validation.is_iso_month("2026-00")
    assert not validation.is_iso_month("26-11")


@pytest.mark.parametrize("raw", ["abc", "-5", "inf", "nan"])
def test_an_unusable_threshold_is_flagged_when_strict(raw):
    assert decision_rules.parse_threshold(raw, strict=True) is None
    assert decision_rules.parse_threshold(raw) == decision_rules.DEFAULT_REVIEW_THRESHOLD


# ---- QA-036: the lockout window --------------------------------------------

def test_an_old_stray_failure_does_not_defeat_the_lockout(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(auth, "_failures", {})
    auth._record_failure("k")                       # one old mistake
    clock[0] += auth.LOCKOUT_SECONDS + 2
    for _ in range(auth.MAX_FAILURES):
        assert auth.locked_for("k") == 0
        auth._record_failure("k")
        clock[0] += 1
    assert auth.locked_for("k") > 0
    clock[0] += auth.LOCKOUT_SECONDS
    assert auth.locked_for("k") == 0


# ---- QA-037: a run the backend never finished ------------------------------

def _runs_db(status):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE Pipeline_Run (run_id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
            status TEXT, steps_skipped INTEGER, max_sale_id INTEGER, max_event_id INTEGER,
            max_closure_id INTEGER);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, tally_date_flag INTEGER);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY);
    """)
    con.execute("INSERT INTO Pipeline_Run (status) VALUES (?)", (status,))
    return con


def test_an_abandoned_running_row_reads_as_a_failed_run(monkeypatch):
    monkeypatch.setitem(pipeline._state, "status", "idle")
    monkeypatch.setitem(pipeline._state, "run_id", None)
    s = pipeline.get_staleness(_runs_db("running"))
    assert (s["running"], s["interrupted"], s["stale"]) == (False, "error", True)


def test_the_run_this_process_is_executing_is_not_interrupted(monkeypatch):
    monkeypatch.setitem(pipeline._state, "status", "running")
    monkeypatch.setitem(pipeline._state, "run_id", 1)
    s = pipeline.get_staleness(_runs_db("running"))
    assert (s["running"], s["interrupted"]) == (True, None)


# ---- QA-034: one unreadable workbook ---------------------------------------

def test_an_unreadable_workbook_is_named_and_the_others_still_read(tmp_path):
    good = tmp_path / "good.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "NOVEMBER 2026 - TBS"
    wb.active.append(["ITEM", __import__("datetime").date(2026, 11, 2)])
    wb.save(good)
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"PK\x03\x04 not a workbook")
    unreadable = []
    plan, _ = step0.plan_sheets([str(bad), str(good)], unreadable)
    assert [os.path.basename(p) for p, _s in plan] == ["good.xlsx"]
    assert [n for n, _r in unreadable] == ["bad.xlsx"]
    line = f"  SKIPPED unreadable workbook: bad.xlsx ({unreadable[0][1]})"
    assert pipeline._UNREADABLE.findall(line) == ["bad.xlsx"]
    assert "'bad.xlsx'" in pipeline._summarise_warning("step0", line)


# ---- QA-026: changing a password -------------------------------------------

def test_set_password_changes_the_login(tmp_path):
    con = sqlite3.connect(tmp_path / "u.db")
    con.row_factory = sqlite3.Row
    assert set_password.set_password(con, "Staff", "a-new-long-password")
    assert auth.check_login(con, "staff", "a-new-long-password", "t1")
    assert auth.check_login(con, "staff", "staff123", "t2") is None
    assert not set_password.set_password(con, "nobody", "a-new-long-password")
