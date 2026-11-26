"""
tests/test_stock_running_balance.py
------------------------------------------------------------------
Stock on hand between counts (backend/catalog.py load_current_stock):

  - a staff count is run down by every unit tallied out since its count
    day - sales, and the damaged / promo / transfer removals - never
    below 0;
  - a monthly count stands for the 1st of its month (the count sheets
    record stock at the start of the billing month), unless it was typed
    later in that same month, when it stands for the day it was typed;
  - a newer count resets the balance;
  - a count typed after that day's app-entered sales stands as typed: the
    sales already in the tally when it was saved are not taken off again
    (QA-040);
  - a workbook figure is not run down;
  - days_of_supply is on hand / the reorder demand rate, as the Reorder
    list's days of cover (QA-041).
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import catalog  # noqa: E402


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setattr(catalog, "load_csv_stock", lambda: {})
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
                                 quantity_sold INTEGER, transaction_type TEXT,
                                 tally_date_flag INTEGER DEFAULT 0);
        CREATE TABLE Inventory_Count (count_id INTEGER PRIMARY KEY, product_id INTEGER,
                                      count_month TEXT, quantity INTEGER, note TEXT,
                                      counted_by TEXT, date_logged TEXT,
                                      seen_through_sale_id INTEGER,
                                      UNIQUE (product_id, count_month));
    """)
    yield c
    c.close()


def sell(c, pid, day, qty, kind="sale", workbook=False):
    row = c.execute("SELECT date_id FROM Dim_Date WHERE calendar_date = ?", (day,)).fetchone()
    did = row[0] if row else c.execute("INSERT INTO Dim_Date (calendar_date) VALUES (?)", (day,)).lastrowid
    c.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, transaction_type, tally_date_flag) "
              "VALUES (?, ?, ?, ?, ?)", (pid, did, qty, kind, 1 if workbook else 0))


def count(c, pid, month, qty, logged, mark=True):
    """Save a count the way app.py does: with the newest sale_id at that
    moment (mark=False: a count saved before the marker existed)."""
    seen = c.execute("SELECT COALESCE(MAX(sale_id), 0) FROM Fact_Sales").fetchone()[0] if mark else None
    c.execute("INSERT OR REPLACE INTO Inventory_Count (product_id, count_month, quantity, date_logged, "
              "seen_through_sale_id) VALUES (?, ?, ?, ?, ?)", (pid, month, qty, logged, seen))


def stock(c, pid=1):
    return catalog.load_current_stock(c, {pid: "ID Lace"})[pid]


def test_the_count_day_is_the_first_unless_typed_later_that_month():
    assert catalog.count_day("2026-10", "2026-11-03T09:00:00") == "2026-10-01"   # imported report
    assert catalog.count_day("2026-11", "2026-11-20T15:30:00") == "2026-11-20"   # mid-month recount
    assert catalog.count_day("2026-11", None) == "2026-11-01"


def test_sales_since_the_count_come_off_the_shelf(con):
    count(con, 1, "2026-10", 50, "2026-11-03T09:00:00")       # stands for Oct 1
    sell(con, 1, "2026-09-30", 7)                              # before the count: ignored
    sell(con, 1, "2026-10-01", 5)
    sell(con, 1, "2026-11-12", 35)
    s = stock(con)
    assert (s["counted_qty"], s["out_since"], s["qty"]) == (50, 40, 10)


def test_damaged_promo_and_transfer_also_leave_the_shelf(con):
    count(con, 1, "2026-10", 50, "2026-11-03T09:00:00")
    for kind in ("sale", "damaged", "promo", "transfer"):
        sell(con, 1, "2026-10-05", 2, kind)
    assert stock(con)["qty"] == 42


def test_it_never_goes_below_zero(con):
    count(con, 1, "2026-10", 5, "2026-11-03T09:00:00")
    sell(con, 1, "2026-10-05", 9)
    assert stock(con)["qty"] == 0


def test_a_recount_resets_the_balance(con):
    count(con, 1, "2026-10", 50, "2026-11-03T09:00:00")
    sell(con, 1, "2026-10-05", 40)
    count(con, 1, "2026-11", 110, "2026-11-20T15:30:00")      # a delivery arrived; staff recount
    sell(con, 1, "2026-11-19", 3)                              # before the recount day: ignored
    sell(con, 1, "2026-11-21", 4)
    s = stock(con)
    assert (s["counted_qty"], s["out_since"], s["qty"]) == (110, 4, 106)


def test_a_workbook_figure_is_not_run_down(con, monkeypatch):
    monkeypatch.setattr(catalog, "load_csv_stock", lambda: {"ID Lace": {"month": "2026-04", "qty": 80.0}})
    sell(con, 1, "2026-06-01", 30)
    s = stock(con)
    assert (s["source"], s["qty"]) == ("workbook", 80.0)
    assert "out_since" not in s


# ---- QA-040: a count typed after that day's sales -------------------------

def test_a_count_typed_after_that_days_sales_stands_as_typed(con):
    sell(con, 1, "2026-11-25", 25)                             # sold this morning, typed into the app
    count(con, 1, "2026-11", 15, "2026-11-25T15:00:00")        # then the shelf is counted: 15
    s = stock(con)
    assert (s["counted_qty"], s["out_since"], s["qty"]) == (15, 0, 15)
    sell(con, 1, "2026-11-25", 2)                              # sold after the count, same day
    sell(con, 1, "2026-11-26", 3)
    assert stock(con)["qty"] == 10


def test_a_correction_after_sales_shows_the_corrected_figure(con):
    count(con, 1, "2026-11", 15, "2026-11-25T09:00:00")
    sell(con, 1, "2026-11-25", 4)
    assert stock(con)["qty"] == 11
    count(con, 1, "2026-11", 10, "2026-11-25T15:00:00")        # Current Inventory correction, same month
    assert stock(con)["qty"] == 10


def test_a_count_for_the_1st_still_takes_off_that_days_sales(con):
    sell(con, 1, "2026-10-01", 5)
    count(con, 1, "2026-10", 50, "2026-11-03T09:00:00")        # stands for Oct 1, typed later
    assert stock(con)["qty"] == 45


def test_workbook_rows_on_the_count_day_still_come_off(con):
    sell(con, 1, "2026-11-25", 6, workbook=True)               # no time of day, renumbered every run
    count(con, 1, "2026-11", 15, "2026-11-25T15:00:00")
    assert stock(con)["qty"] == 9


def test_a_count_saved_before_the_marker_keeps_the_old_rule(con):
    sell(con, 1, "2026-11-25", 4)
    count(con, 1, "2026-11", 15, "2026-11-25T15:00:00", mark=False)
    assert stock(con)["qty"] == 11


# ---- QA-041: days of supply = on hand / the reorder demand rate ------------

def test_days_of_supply_is_on_hand_over_the_reorder_rate(con):
    con.executescript("""
        CREATE TABLE Result_Prescriptive (product_id INTEGER, ordering_cost_scenario TEXT,
                                          avg_daily_demand REAL, reorder_point REAL);
        INSERT INTO Result_Prescriptive VALUES (1, 'low_admin_cost', 0.5, 4.0), (1, 'high_goods_value', 0.5, 4.0),
                                               (2, 'low_admin_cost', 0.3, NULL);
    """)
    rate = catalog.load_demand_rate(con)
    assert rate == {1: 0.5}                                    # no reorder point, no rate
    assert catalog.days_of_supply(21, rate.get(1)) == 42.0
    assert catalog.days_of_supply(21, rate.get(2)) is None
    assert catalog.days_of_supply(None, 0.5) is None
