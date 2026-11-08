"""
tests/test_whatif.py
------------------------------------------------------------------
The Reorder Alerts what-if (backend/whatif.py, GET /api/reorder/<id>/whatif),
the day kinds and lifts it shares with the daily accuracy check
(forecasting/day_kinds.py), and that check's own arithmetic
(scripts/daily_accuracy_check.py):

  - with nothing changed the what-if shows the figures the Reorder table
    shows, and each change moves them by the formulas step5 uses;
  - a busy period adds daily demand x (lift - 1) for the days inside the lead
    time, a bulk order its units; the sales at stake are the units short at
    the item's price;
  - bad input is refused with a sentence per field, and an item with no
    reorder point cannot be tested;
  - days are classified in precedence order, and a day the store was closed
    or sold nothing is not a recorded day;
  - "trading days known" moves a window's forecast onto the days the store
    traded without changing the window's total.
------------------------------------------------------------------
"""
import math
import os
import shutil
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import app as backend  # noqa: E402  (backend/app.py)
import db as dbmod  # noqa: E402
import whatif  # noqa: E402
import daily_accuracy_check as dac  # noqa: E402
from forecasting import day_kinds  # noqa: E402

LIFTS = {"enrollment": {"days": 13, "units_per_day": 325.0, "lift": 1.5},
         "term_start": {"days": 18, "units_per_day": 357.0, "lift": 2.0},
         "regular": {"days": 338, "units_per_day": 212.0, "lift": 1.0}}


def item(**over):
    """An entry shaped like app._reorder_items' output for a priced item."""
    d, lt, ss = 4.0, 18, 10.0
    base = {
        "product_id": 7, "item_name": "Lanyard", "supplier_name": "S", "fsn_class": "F",
        "service_tier": "servable", "avg_daily_demand": d, "annual_demand": 365 * d,
        "lead_time_days": lt, "safety_stock": ss, "reorder_point": d * lt + ss,
        "current_stock": 50,
        "scenarios": {"low_admin_cost": {"ordering_cost_php": 1250.0,
                                         "holding_cost_php_per_unit_year": 1.4563}},
    }
    base.update(over)
    return base


def run(it=None, price=100.0, **params):
    params.setdefault("scenario", "none")
    return whatif.compute(it or item(), price, LIFTS, 4, params, 30)


# ---- the formulas --------------------------------------------------------

def test_nothing_changed_shows_the_current_figures():
    r = run()
    cur = r["current"]
    assert r["what_if"] == cur
    assert cur["reorder_point"] == pytest.approx(4.0 * 18 + 10.0)
    assert cur["needs_reorder"] is True                         # 50 <= 82
    assert cur["order_up_to_level"] == pytest.approx(82 + 30 * 4.0)
    assert cur["suggested_order_qty"] == round(202 - 50)
    assert cur["eoq"] == pytest.approx(math.sqrt(2 * 1460 * 1250 / 1.4563))
    assert cur["units_short"] == pytest.approx(72 - 50)
    assert cur["php_at_stake"] == pytest.approx(22 * 100.0)


def test_enough_stock_means_no_order_and_nothing_at_stake():
    r = run(item(current_stock=500))
    assert r["current"]["needs_reorder"] is False
    assert r["current"]["suggested_order_qty"] == 0
    assert r["current"]["units_short"] == 0


def test_a_longer_lead_time_scales_the_buffer_by_its_square_root():
    r = run(lead_time_days=36)
    w = r["what_if"]
    assert w["safety_stock"] == pytest.approx(10.0 * math.sqrt(2))
    assert w["reorder_point"] == pytest.approx(4.0 * 36 + 10.0 * math.sqrt(2))
    assert any("square root" in n for n in r["notes"])


def test_a_zero_buffer_stays_zero():
    r = run(item(safety_stock=0.0, reorder_point=72.0), lead_time_days=36)
    assert r["what_if"]["safety_stock"] == 0
    assert not any("square root" in n for n in r["notes"])


def test_enrollment_adds_the_measured_lift_for_its_days():
    r = run(scenario="enrollment")                             # default 4 days, lift 1.5
    assert r["scenario"]["extra_units"] == pytest.approx(4.0 * 0.5 * 4)
    assert r["what_if"]["reorder_point"] == pytest.approx(82 + 8.0)
    assert r["scenario"]["measured_days"] == 13


def test_only_the_busy_days_inside_the_lead_time_count():
    r = run(scenario="term_start", lead_time_days=10)           # 14 days, lift 2.0, LT 10
    assert r["scenario"]["days_counted"] == 10
    assert r["scenario"]["extra_units"] == pytest.approx(4.0 * 1.0 * 10)


def test_a_bulk_order_adds_its_units_and_the_pesos_at_stake_follow():
    r = run(scenario="bulk", bulk_units=60)
    w = r["what_if"]
    assert w["reorder_point"] == pytest.approx(82 + 60)
    assert w["units_short"] == pytest.approx(72 + 60 - 50)
    assert w["php_at_stake"] == pytest.approx(82 * 100.0)


def test_no_count_leaves_the_stock_decisions_unknown_until_one_is_typed():
    r = run(item(current_stock=None))
    assert r["current"]["needs_reorder"] is None
    assert r["current"]["suggested_order_qty"] is None
    assert any("no inventory count" in n for n in r["notes"])
    typed = run(item(current_stock=None), stock_on_hand=10)
    assert typed["what_if"]["needs_reorder"] is True
    assert typed["current"]["needs_reorder"] is None


def test_costs_move_only_the_eoq():
    r = run(ordering_cost_php=200_000.0, holding_cost_php=2.0)
    w, cur = r["what_if"], r["current"]
    assert w["eoq"] == pytest.approx(math.sqrt(2 * 1460 * 200_000 / 2.0))
    assert w["eoq_exceeds_annual_demand"] is True
    assert w["suggested_order_qty"] == cur["suggested_order_qty"]
    assert w["reorder_point"] == cur["reorder_point"]


def test_an_unpriced_item_has_no_pesos_at_stake():
    assert run(price=None)["current"]["php_at_stake"] is None


def test_presets_carry_the_measured_lifts():
    p = {x["key"]: x for x in whatif.presets(LIFTS, 4)}
    assert p["enrollment"]["lift"] == 1.5 and p["enrollment"]["default_days"] == 4
    assert p["term_start"]["default_days"] == 14


# ---- the input ---------------------------------------------------------

@pytest.mark.parametrize("args, field", [
    ({"lead_time_days": "abc"}, "lead_time_days"),
    ({"lead_time_days": "0"}, "lead_time_days"),
    ({"lead_time_days": "12.5"}, "lead_time_days"),
    ({"stock_on_hand": "-1"}, "stock_on_hand"),
    ({"holding_cost_php": "inf"}, "holding_cost_php"),
    ({"scenario": "bulk"}, "bulk_units"),
    ({"scenario": "holiday"}, "scenario"),
])
def test_bad_input_is_refused_per_field(args, field):
    _, errors = whatif.parse(args)
    assert field in errors


def test_blank_fields_keep_the_current_figures():
    params, errors = whatif.parse({"lead_time_days": "", "stock_on_hand": " ", "scenario": ""})
    assert errors == {}
    assert params == {"scenario": "none"}


# ---- day kinds ---------------------------------------------------------

@pytest.fixture
def days_db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT, semester_id TEXT,
            semester_week INTEGER, is_enrollment_period INTEGER, is_exam_week INTEGER,
            is_event_day INTEGER, is_sem_break INTEGER, is_store_closed INTEGER);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, date_id INTEGER, product_id INTEGER,
            quantity_sold INTEGER, transaction_type TEXT);
    """)
    days = [  # date, term, week, enroll, exam, event, break, closed, units
        ("2026-06-06", "AY2526-ST", 1, 1, 1, 0, 0, 0, 30),   # enrollment beats exam
        ("2026-06-07", "AY2526-ST", 1, 1, 0, 0, 0, 0, 30),
        ("2026-06-08", "AY2526-ST", 1, 1, 0, 0, 0, 0, 0),    # nothing sold: not recorded
        ("2026-08-01", "AY2627-T1", 1, 0, 0, 1, 0, 0, 40),   # term start beats event
        ("2026-08-20", "AY2627-T1", 3, 0, 0, 0, 0, 0, 10),   # week 3: regular
        ("2026-01-09", "AY2526-T2", 1, 0, 0, 0, 0, 0, 10),   # 2nd term start: regular
        ("2026-12-18", "AY2627-T1", 20, 0, 0, 1, 0, 0, 25),  # Paskuhan: event
        ("2026-12-25", "AY2627-T1", 21, 0, 0, 0, 1, 1, 5),   # closed: not recorded
        ("2026-06-20", "AY2526-ST", 3, 1, 0, 0, 0, 0, 30),   # a second run of one day
    ]
    for i, (d, sid, wk, en, ex, ev, br, cl, u) in enumerate(days, 1):
        con.execute("INSERT INTO Dim_Date VALUES (?,?,?,?,?,?,?,?,?)", (i, d, sid, wk, en, ex, ev, br, cl))
        if u:
            con.execute("INSERT INTO Fact_Sales (date_id, product_id, quantity_sold, transaction_type) "
                        "VALUES (?, 1, ?, 'sale')", (i, u))
    return con


def test_days_take_the_first_kind_that_applies(days_db):
    k = day_kinds.load_days(days_db)["kind"]
    assert k["2026-06-06"] == "enrollment"
    assert k["2026-08-01"] == "term_start"
    assert k["2026-08-20"] == "regular"
    assert k["2026-01-09"] == "regular"
    assert k["2026-12-18"] == "event"


def test_closed_and_empty_days_are_not_recorded(days_db):
    d = day_kinds.load_days(days_db)
    assert not d.loc["2026-06-08", "recorded"]
    assert not d.loc["2026-12-25", "recorded"]
    assert d.loc["2026-06-07", "recorded"]


def test_lifts_compare_recorded_days_with_regular_ones(days_db):
    lifts = day_kinds.lifts(days_db)
    assert lifts["regular"]["units_per_day"] == 10
    assert lifts["enrollment"]["days"] == 3                   # the empty day is left out
    assert lifts["enrollment"]["lift"] == pytest.approx(3.0)
    assert lifts["exam"]["lift"] is None                      # no recorded exam day


def test_the_typical_enrollment_run_is_the_median_length(days_db):
    assert day_kinds.enrollment_days_typical(days_db) in (1, 3)


# ---- the daily accuracy check --------------------------------------------

def test_trading_days_known_keeps_each_windows_total():
    df = pd.DataFrame({
        "category": ["A"] * 4 + ["B"] * 2, "fold": [0] * 6,
        "forecast": [1.0, 1.0, 2.0, 0.0, 0.0, 3.0],
        "recorded": [True, False, True, True, True, False],
    })
    known = dac.trading_days_known(df)
    assert known[:4].tolist() == pytest.approx([4 / 3, 0.0, 8 / 3, 0.0])
    assert known[:4].sum() == pytest.approx(4.0)
    # B's one traded day has no forecast: nothing to scale, so it stays as
    # drawn instead of dividing by zero
    assert known[4:].tolist() == [0.0, 0.0]


def test_wape_and_bias():
    assert dac.wape([8, 12], [10, 10]) == pytest.approx(20.0)
    assert dac.bias([8, 8], [10, 10]) == pytest.approx(-20.0)
    assert dac.mape([5, 30], [10, 20]) == pytest.approx(50.0)
    assert math.isnan(dac.wape([1], [0]))


# ---- the endpoint, on a copy of the real database -------------------------

REAL_DB = os.path.join(ROOT, "ustore.db")


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    if not os.path.exists(REAL_DB):
        pytest.skip("ustore.db not built")
    db_path = tmp_path / "ustore.db"
    shutil.copy(REAL_DB, db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    return client


def test_the_endpoint_matches_the_reorder_table(real_api):
    data = real_api.get("/api/reorder").get_json()
    if not data["available"]:
        pytest.skip("step5 has not run")
    items = data["data"]["items"]
    counted = [i for i in items if i["policy_state"] == "priced" and i["current_stock"] is not None]
    assert counted, "no priced item has a stock count"
    for i in counted[:5]:
        r = real_api.get(f"/api/reorder/{i['product_id']}/whatif").get_json()
        assert r["ok"]
        assert r["current"]["reorder_point"] == pytest.approx(i["reorder_point"])
        assert r["current"]["needs_reorder"] == i["needs_reorder"]
        assert r["current"]["suggested_order_qty"] == i["suggested_order_qty"]
        assert r["what_if"] == r["current"]


def test_the_endpoint_refuses_bad_input_and_untestable_items(real_api):
    data = real_api.get("/api/reorder").get_json()
    if not data["available"]:
        pytest.skip("step5 has not run")
    items = data["data"]["items"]
    priced = next(i for i in items if i["policy_state"] == "priced")
    bad = real_api.get(f"/api/reorder/{priced['product_id']}/whatif?lead_time_days=abc")
    assert bad.status_code == 400 and "lead_time_days" in bad.get_json()["errors"]
    flagged = next((i for i in items if i["policy_state"] == "insufficient_data"), None)
    if flagged:
        assert real_api.get(f"/api/reorder/{flagged['product_id']}/whatif").status_code == 409
    assert real_api.get("/api/reorder/999999999/whatif").status_code == 404
