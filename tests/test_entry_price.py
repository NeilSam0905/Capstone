"""
tests/test_entry_price.py
------------------------------------------------------------------
Sales Tally's optional price: a sale can carry its own price, for that entry
only, and every peso figure counts it the same way (backend/db.py pesos()).

  - pesos() splits units between their own prices and the listed one, and an
    unpriced item with no priced sale stays out of the peso figures;
  - a price is checked, saved on a sale and ignored on a non-sale;
  - on a copy of the real database, one sale at its own price moves the
    Overview, the product list, the monthly sales, the Batch Sales Report and
    the Summary Report by exactly the same amount;
  - the item's listed price does not change.
------------------------------------------------------------------
"""
import os
import shutil
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import app as backend  # noqa: E402  (backend/app.py)
import db as dbmod  # noqa: E402
import summary_report  # noqa: E402
import validation  # noqa: E402

REAL_DB = os.path.join(ROOT, "ustore.db")


def test_pesos_counts_own_prices_then_the_listed_one():
    assert dbmod.pesos(10, 150) == 1500
    assert dbmod.pesos(10, 150, 4, 400.0) == 1300          # 6 at 150 + 4 at 100
    assert dbmod.pesos(10, 150, 10, 1200.0) == 1200
    assert dbmod.pesos(10, None) is None                   # counts in units only
    assert dbmod.pesos(10, None, 4, 400.0) == 400          # the priced sale counts


def test_a_price_is_checked():
    assert validation.entry_price("") == (None, None)
    assert validation.entry_price(None) == (None, None)
    assert validation.entry_price("1,250.50") == (1250.5, None)
    assert validation.entry_price(0) == (0.0, None)        # given away is a price
    assert validation.entry_price("abc")[1] == "Price must be a number."
    assert validation.entry_price(-5)[1] == "Price cannot be negative."
    assert validation.entry_price("nan")[1] == "Price cannot be negative."


@pytest.fixture
def api(tmp_path, monkeypatch):
    if not os.path.exists(REAL_DB):
        pytest.skip("ustore.db not built")
    db_path = tmp_path / "ustore.db"
    shutil.copy(REAL_DB, db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    client.db = db_path
    return client


def figures(api, product_id, day):
    """Every peso figure a sale on `day` should move."""
    month = day[:7]
    perf = api.get("/api/overview/performance").get_json()
    products = {p["product_id"]: p for p in api.get("/api/products").get_json()}
    sales = {m["month"]: m for m in api.get("/api/sales/monthly?dateRange=All Time").get_json()}
    con = dbmod.get_db()
    try:
        batch = sum(e["subtotal"] for e in backend.build_batch_report(con, month))
        period = next(p for p in summary_report.periods(con)
                      if p["kind"] == "year" and p["start"] <= day <= p["end"])
        summary = summary_report.build(con, period)["totals"]["php"]
    finally:
        con.close()
    return {"overview": perf["latest_month"]["php"], "product": products[product_id]["revenue"],
            "monthly": sales[month]["revenue"], "batch": batch, "summary": summary}


def test_a_sale_at_its_own_price_moves_every_report_the_same(api):
    con = sqlite3.connect(api.db)
    day = con.execute("""SELECT MAX(d.calendar_date) FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
                         WHERE f.quantity_sold > 0""").fetchone()[0]
    pid, listed = con.execute("SELECT product_id, unit_price_php FROM Dim_Product "
                              "WHERE unit_price_php > 10 ORDER BY product_id LIMIT 1").fetchone()
    con.close()

    before = figures(api, pid, day)
    r = api.post("/api/tally", json={"product_id": pid, "quantity_sold": 4, "calendar_date": day,
                                     "transaction_type": "SALE", "entry_price_php": 2.5})
    assert r.status_code == 200 and r.get_json()["entry"]["entry_price_php"] == 2.5
    after = figures(api, pid, day)
    for name in before:
        assert after[name] - before[name] == pytest.approx(10.0, abs=0.02), name

    # Blank is the listed price, as every sale was before.
    r = api.post("/api/tally", json={"product_id": pid, "quantity_sold": 1, "calendar_date": day,
                                     "transaction_type": "SALE"})
    assert r.get_json()["entry"]["entry_price_php"] is None
    final = figures(api, pid, day)
    for name in after:
        assert final[name] - after[name] == pytest.approx(listed, abs=0.02), name

    # The listed price is untouched, and Recent Entries shows the entry's own.
    con = sqlite3.connect(api.db)
    assert con.execute("SELECT unit_price_php FROM Dim_Product WHERE product_id = ?", (pid,)).fetchone()[0] == listed
    con.close()
    recent = api.get("/api/tally/recent?limit=5").get_json()
    assert {e["entry_price_php"] for e in recent if e["product_id"] == pid} >= {2.5, None}


def test_a_price_is_only_for_a_sale(api):
    con = sqlite3.connect(api.db)
    day = con.execute("SELECT MAX(calendar_date) FROM Dim_Date WHERE calendar_date <= date('now')").fetchone()[0]
    pid = con.execute("SELECT product_id FROM Dim_Product LIMIT 1").fetchone()[0]
    con.close()
    entry = {"product_id": pid, "quantity_sold": 1, "calendar_date": day}

    r = api.post("/api/tally", json={**entry, "transaction_type": "SALE", "entry_price_php": "abc"})
    assert r.status_code == 400 and r.get_json()["errors"]["entry_price_php"] == "Price must be a number."
    r = api.post("/api/tally", json={**entry, "transaction_type": "DAMAGED", "entry_price_php": 5})
    assert r.status_code == 200 and r.get_json()["entry"]["entry_price_php"] is None


def test_exports_use_the_exact_total(api):
    con = sqlite3.connect(api.db)
    day = con.execute("""SELECT MAX(d.calendar_date) FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
                         WHERE f.quantity_sold > 0""").fetchone()[0]
    pid = con.execute("SELECT product_id FROM Dim_Product WHERE unit_price_php > 10 ORDER BY product_id LIMIT 1").fetchone()[0]
    con.close()
    api.post("/api/tally", json={"product_id": pid, "quantity_sold": 3, "calendar_date": day,
                                 "transaction_type": "SALE", "entry_price_php": 7})
    month = day[:7]
    con = dbmod.get_db()
    try:
        items = [i for e in backend.build_batch_report(con, month) for i in e["items"]]
    finally:
        con.close()
    line = next(i for i in items if i["product_id"] == pid)
    assert line["sale_prices"] is True
    assert api.get(f"/api/reports/batch.pdf?month={month}").status_code == 200
    assert api.get(f"/api/reports/batch.xlsx?month={month}").status_code == 200
    assert api.get(f"/api/reports/batch.csv?month={month}").status_code == 200


def sold_on_or_after(api, pid, day):
    con = sqlite3.connect(api.db)
    try:
        return con.execute("""SELECT COALESCE(SUM(f.quantity_sold), 0) FROM Fact_Sales f
                              JOIN Dim_Date d ON d.date_id = f.date_id
                              WHERE f.product_id = ? AND d.calendar_date >= ?
                                AND LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'""", (pid, day)).fetchone()[0]
    finally:
        con.close()


def test_a_price_change_leaves_earlier_sales_alone(api):
    con = sqlite3.connect(api.db)
    pid, listed = con.execute("""
        SELECT p.product_id, p.unit_price_php FROM Dim_Product p
        JOIN Fact_Sales f ON f.product_id = p.product_id
        WHERE p.unit_price_php > 10 AND f.quantity_sold > 0
        GROUP BY 1 ORDER BY SUM(f.quantity_sold) DESC LIMIT 1""").fetchone()
    day = con.execute("""SELECT MAX(d.calendar_date) FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
                         WHERE f.quantity_sold > 0""").fetchone()[0]
    con.close()
    new = listed + 25
    after_day = sold_on_or_after(api, pid, day)

    before = figures(api, pid, day)
    r = api.post(f"/api/products/{pid}/price", json={"price_php": new, "effective_from": day, "note": "New cost"})
    assert r.status_code == 200 and r.get_json()["previous_php"] == listed
    moved = figures(api, pid, day)
    # Only the units from that day on change price; everything earlier stays.
    for name in before:
        assert moved[name] - before[name] == pytest.approx(after_day * 25, abs=0.02), name

    # The sheet price stays on Dim_Product, and a sale's own price still wins.
    api.post("/api/tally", json={"product_id": pid, "quantity_sold": 2, "calendar_date": day,
                                 "transaction_type": "SALE", "entry_price_php": 1})
    own = figures(api, pid, day)
    assert own["summary"] - moved["summary"] == pytest.approx(2, abs=0.02)
    con = sqlite3.connect(api.db)
    assert con.execute("SELECT unit_price_php FROM Dim_Product WHERE product_id = ?", (pid,)).fetchone()[0] == listed
    con.close()

    body = api.get(f"/api/products/{pid}/price").get_json()
    assert (body["sheet_price"], body["current_price"]) == (listed, new)
    (c,) = body["changes"]
    assert (c["effective_from"], c["price_php"], c["previous_php"], c["note"], c["changed_by"]) \
        == (day, new, listed, "New cost", "admin")
    shown = next(p for p in api.get("/api/products").get_json() if p["product_id"] == pid)
    assert (shown["current_price_php"], shown["unit_price_php"]) == (new, listed)


def test_a_price_starting_later_changes_nothing_yet(api):
    con = sqlite3.connect(api.db)
    pid, listed = con.execute("SELECT product_id, unit_price_php FROM Dim_Product "
                              "WHERE unit_price_php > 10 ORDER BY product_id LIMIT 1").fetchone()
    day = con.execute("""SELECT MAX(d.calendar_date) FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
                         WHERE f.quantity_sold > 0""").fetchone()[0]
    con.close()
    before = figures(api, pid, day)
    assert api.post(f"/api/products/{pid}/price", json={"price_php": listed + 1, "effective_from": "2999-01-01"}).status_code == 200
    assert figures(api, pid, day) == before
    assert api.get(f"/api/products/{pid}/price").get_json()["current_price"] == listed


def test_a_price_change_is_checked_and_the_admins(api):
    con = sqlite3.connect(api.db)
    pid, listed = con.execute("SELECT product_id, unit_price_php FROM Dim_Product "
                              "WHERE unit_price_php > 10 ORDER BY product_id LIMIT 1").fetchone()
    free = con.execute("SELECT product_id FROM Dim_Product WHERE unit_price_php IS NULL LIMIT 1").fetchone()
    con.close()
    assert api.post(f"/api/products/{pid}/price", json={}).get_json()["errors"]["price_php"] == "Enter the new price."
    assert "price_php" in api.post(f"/api/products/{pid}/price", json={"price_php": -1}).get_json()["errors"]
    assert "effective_from" in api.post(f"/api/products/{pid}/price",
                                        json={"price_php": 5, "effective_from": "nope"}).get_json()["errors"]
    assert api.post(f"/api/products/{pid}/price", json={"price_php": listed}).status_code == 400
    assert api.post("/api/products/999999/price", json={"price_php": 5}).status_code == 404

    with api.session_transaction() as s:
        s["user"] = "staff"
    r = api.post(f"/api/products/{pid}/price", json={"price_php": listed + 1})
    assert r.status_code == 403 and r.get_json()["admin_required"] is True
    assert api.get(f"/api/products/{pid}/price").get_json()["changes"] == []
    if free:                                 # a first price is a new record, not an edit
        assert api.post(f"/api/products/{free[0]}/price", json={"price_php": 50}).status_code == 200


def test_the_price_history_moves_with_a_merged_item(tmp_path):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import step1_apply_mapping as step1
    con = sqlite3.connect(tmp_path / "x.db")
    con.executescript("""
        CREATE TABLE Fact_Sales (product_id INTEGER, tally_date_flag INTEGER);
    """)
    con.execute(dbmod.ITEM_PRICE_CHANGE_DDL)
    con.execute("INSERT INTO Item_Price_Change (product_id, effective_from, price_php) VALUES (7, '2026-11-01', 90)")
    step1.carry_over_operational_rows(con, {"Old Name": 7}, {"New Name": 3}, {"Old Name": "New Name"})
    assert con.execute("SELECT product_id FROM Item_Price_Change").fetchall() == [(3,)]
