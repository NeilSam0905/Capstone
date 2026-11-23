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
  - a workbook figure is not run down.
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
        CREATE TABLE Fact_Sales (product_id INTEGER, date_id INTEGER, quantity_sold INTEGER,
                                 transaction_type TEXT);
        CREATE TABLE Inventory_Count (count_id INTEGER PRIMARY KEY, product_id INTEGER,
                                      count_month TEXT, quantity INTEGER, note TEXT,
                                      counted_by TEXT, date_logged TEXT,
                                      UNIQUE (product_id, count_month));
    """)
    yield c
    c.close()


def sell(c, pid, day, qty, kind="sale"):
    row = c.execute("SELECT date_id FROM Dim_Date WHERE calendar_date = ?", (day,)).fetchone()
    did = row[0] if row else c.execute("INSERT INTO Dim_Date (calendar_date) VALUES (?)", (day,)).lastrowid
    c.execute("INSERT INTO Fact_Sales VALUES (?, ?, ?, ?)", (pid, did, qty, kind))


def count(c, pid, month, qty, logged):
    c.execute("INSERT OR REPLACE INTO Inventory_Count (product_id, count_month, quantity, date_logged) "
              "VALUES (?, ?, ?, ?)", (pid, month, qty, logged))


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
