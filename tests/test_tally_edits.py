"""
tests/test_tally_edits.py
------------------------------------------------------------------
Editing and removing tally entries (Tally Interface → Sales Tally → Edit
entries), backend/app.py edit_entry and delete_entry, on a throwaway
database:

  - an entry typed in can have its date, quantity and type changed, and can
    be removed;
  - a row loaded from the tally workbook (tally_date_flag = 1) can be too,
    and the change is kept in Tally_Correction, because step2 reloads every
    workbook row on each pipeline run and re-applies it from there;
  - the same validation as a new entry applies (no future date, quantity
    above zero);
  - staff need the admin's password, like every other edit.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402
import calendar_extent  # noqa: E402
import db as dbmod  # noqa: E402
import tally_corrections  # noqa: E402

TODAY = date.today()


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "ustore.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT UNIQUE,
            semester_id TEXT, semester_week INTEGER, is_enrollment_period INTEGER DEFAULT 0,
            is_exam_week INTEGER DEFAULT 0, is_event_day INTEGER DEFAULT 0, is_sem_break INTEGER DEFAULT 0,
            is_tally_date INTEGER DEFAULT 0, is_tally_date_positive INTEGER DEFAULT 0,
            is_store_closed INTEGER DEFAULT 0);
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, category TEXT,
            supplier_name TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,
            date_id INTEGER NOT NULL, quantity_sold INTEGER, cumulative_monthly_units INTEGER,
            daily_depletion_rate REAL, days_of_supply REAL, is_censored INTEGER,
            imputation_flag INTEGER DEFAULT 0, tally_date_flag INTEGER DEFAULT 0,
            transaction_type TEXT DEFAULT 'sale', entered_by TEXT, order_type TEXT);
        INSERT INTO Dim_Product VALUES (1, 'Thick Lanyard', 'Lanyards & IDs', 'USTORE');
    """)
    for n in range(-10, 10):
        d = TODAY + timedelta(days=n)
        c.execute("INSERT INTO Dim_Date (date_id, calendar_date) VALUES (?, ?)",
                  (calendar_extent.date_id_for(d), d.isoformat()))
    # sale 1 typed in through the interface, sale 2 loaded from the workbook
    for sale_id, flag in ((1, 0), (2, 1)):
        c.execute("INSERT INTO Fact_Sales (sale_id, product_id, date_id, quantity_sold, tally_date_flag, "
                  "transaction_type) VALUES (?, 1, ?, 3, ?, 'sale')",
                  (sale_id, calendar_extent.date_id_for(TODAY - timedelta(days=2)), flag))
    c.commit()
    c.close()

    monkeypatch.setattr(dbmod, "DB_PATH", path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "admin"
    client.db = path
    return client


def row(api, sale_id):
    return sqlite3.connect(api.db).execute(
        "SELECT d.calendar_date, f.quantity_sold, f.transaction_type, f.order_type "
        "FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.sale_id = ?",
        (sale_id,)).fetchone()


def edit(api, sale_id, **body):
    payload = {"calendar_date": day(-2), "quantity_sold": 3, "transaction_type": "SALE"} | body
    return api.put(f"/api/tally/{sale_id}", json=payload)


def test_an_entry_can_be_moved_recounted_and_retyped(api):
    r = edit(api, 1, calendar_date=day(-1), quantity_sold=5, order_type="bulk")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert row(api, 1) == (day(-1), 5, "sale", "bulk")


def test_a_removal_is_typed_without_an_order_type(api):
    assert edit(api, 1, transaction_type="DAMAGED", order_type="bulk").status_code == 200
    assert row(api, 1)[2:] == ("damaged", None)


def test_an_entry_can_be_removed(api):
    r = api.delete("/api/tally/1")
    assert r.status_code == 200 and row(api, 1) is None


def corrections(api):
    return sqlite3.connect(api.db).execute(
        "SELECT item_name, original_date, original_quantity, calendar_date, quantity, is_deleted "
        "FROM Tally_Correction ORDER BY correction_id").fetchall()


def test_an_entry_made_here_leaves_no_correction(api):
    edit(api, 1, quantity_sold=4)
    api.delete("/api/tally/1")
    assert corrections(api) == []


def test_a_workbook_row_is_changed_and_the_change_kept_for_the_next_reload(api):
    assert edit(api, 2, quantity_sold=9).status_code == 200
    assert row(api, 2)[1] == 9
    assert corrections(api) == [("Thick Lanyard", day(-2), 3, day(-2), 9, 0)]


def test_a_second_edit_updates_the_same_correction(api):
    edit(api, 2, quantity_sold=9)
    edit(api, 2, quantity_sold=8, calendar_date=day(-1))
    # still described by what the workbook says: 3 units on day -2
    assert corrections(api) == [("Thick Lanyard", day(-2), 3, day(-1), 8, 0)]


def test_removing_a_workbook_row_is_kept_too(api):
    assert api.delete("/api/tally/2").status_code == 200
    assert row(api, 2) is None
    assert corrections(api) == [("Thick Lanyard", day(-2), 3, None, None, 1)]


# ------------------------------------------- step2 re-applies them on reload

def loaded(qty=3, iso="2026-07-01", imputed=0):
    return {"product_id": 1, "date_id": 1, "iso_date": iso, "quantity_sold": qty, "imputation_flag": imputed}


def correction(cid, **kw):
    base = {"correction_id": cid, "item_name": "Thick Lanyard", "original_date": "2026-07-01",
            "original_quantity": 3, "imputation_flag": 0, "calendar_date": "2026-07-02", "quantity": 9,
            "transaction_type": "sale", "order_type": "bulk", "is_deleted": 0}
    return base | kw


def test_step2_applies_a_change_and_a_removal():
    rows = [loaded(), loaded(), loaded(qty=5)]
    applied, unmatched = tally_corrections.apply(
        rows, [correction(1), correction(2, is_deleted=1)],
        {"Thick Lanyard": 1}, {"2026-07-02": 2})
    assert (applied, unmatched) == (2, 0)
    # the first matching row changed, the second identical one removed, the 5 untouched
    assert [(r["iso_date"], r["date_id"], r["quantity_sold"], r.get("order_type")) for r in rows] == [
        ("2026-07-02", 2, 9, "bulk"), ("2026-07-01", 1, 5, None)]
    assert rows[0]["correction_id"] == 1


def test_step2_skips_a_correction_the_workbook_no_longer_matches():
    rows = [loaded(qty=4)]                     # the workbook itself was fixed since
    assert tally_corrections.apply(rows, [correction(1)], {"Thick Lanyard": 1}, {"2026-07-02": 2}) == (0, 1)
    assert rows == [loaded(qty=4)]


def test_a_future_date_or_a_zero_quantity_is_refused(api):
    assert edit(api, 1, calendar_date=day(3)).status_code == 400
    assert edit(api, 1, quantity_sold=0).status_code == 400
    assert row(api, 1) == (day(-2), 3, "sale", None)


def test_a_missing_entry_is_404(api):
    assert edit(api, 99).status_code == 404
    assert api.delete("/api/tally/99").status_code == 404


def test_staff_need_the_admin_password(api):
    with api.session_transaction() as s:
        s["user"] = "staff"
    r = edit(api, 1, quantity_sold=7)
    assert r.status_code == 403 and r.get_json()["admin_required"]
    assert api.delete("/api/tally/1").status_code == 403
    assert row(api, 1)[1] == 3


def test_entries_say_where_they_came_from(api):
    flags = {e["sale_id"]: e["tally_date_flag"] for e in api.get(f"/api/tally?date={day(-2)}").get_json()}
    assert flags == {1: 0, 2: 1}
