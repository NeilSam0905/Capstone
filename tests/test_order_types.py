"""
tests/test_order_types.py
------------------------------------------------------------------
Bulk / organisation orders and pre-orders (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md,
3.4) are recorded on the sale (Fact_Sales.order_type) and left out of the
FORECASTS' training only. Classification, reorder demand and the dashboard
still count every sale: the stock left the shelf either way.

One product: 5 + 3 units walk-in (one row NULL, one 'walk_in'), a 40-unit
bulk order and a 6-unit pre-order. The forecasts must see 8; the rest 54.
Also: step2 reads the store's answers from bulk_day_candidates.csv, and the
Tally Interface records the type on single entries and imports.
------------------------------------------------------------------
"""
import io
import os
import sqlite3
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import catalog  # noqa: E402  (backend/)
import order_types  # noqa: E402  (scripts/)
import step1b_categorize_products as step1b  # noqa: E402
import step2_load_fact_sales as step2  # noqa: E402
import step3_fsn_classification as step3  # noqa: E402
import step4_forecast_model as step4  # noqa: E402
import step4c_category_forecast as step4c  # noqa: E402
import step5_prescriptive as step5  # noqa: E402

WALK_IN, ALL = 8, 54


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT, is_sem_break INTEGER DEFAULT 0);
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, fsn_class TEXT,
                                  is_hvl INTEGER DEFAULT 0, lead_time_days INTEGER, forecast_category TEXT,
                                  unit_price_php REAL);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
                                 quantity_sold INTEGER, imputation_flag INTEGER DEFAULT 0,
                                 tally_date_flag INTEGER DEFAULT 0, transaction_type TEXT DEFAULT 'sale',
                                 is_censored INTEGER, days_of_supply REAL, order_type TEXT);
        INSERT INTO Dim_Date VALUES (1, '2026-08-01', 0), (2, '2026-08-02', 0), (3, '2026-08-03', 0);
        INSERT INTO Dim_Product VALUES (1, 'Test Tote', 'F', 0, 18, 'Bags', 150);
        INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag, order_type) VALUES
            (1, 1, 5, 1, NULL),
            (1, 2, 3, 0, 'walk_in'),
            (1, 3, 40, 0, 'bulk'),
            (1, 3, 6, 0, 'pre_order');
    """)
    return con


def test_spellings():
    assert order_types.normalize("") == "walk_in"
    assert order_types.normalize("Walk-in") == "walk_in"
    assert order_types.normalize("Organisation") == "bulk"
    assert order_types.normalize("BULK ORDER") == "bulk"
    assert order_types.normalize("pre-order") == "pre_order"
    assert order_types.normalize("yes", default=None) == "bulk"
    assert order_types.normalize("", default=None) is None
    assert order_types.normalize("maybe") is None


def test_item_forecast_trains_on_walk_in_sales():
    _products, fact, _dim_date = step4.load_common(_db())
    assert fact["quantity_sold"].sum() == WALK_IN


def test_category_forecast_trains_on_walk_in_sales():
    wide, _end = step4c.load_category_series(_db())
    assert wide.to_numpy().sum() == WALK_IN
    products = pd.DataFrame({"product_id": [1], "forecast_category": ["Bags"]})
    assert step1b.category_daily_series(_db(), products).to_numpy().sum() == WALK_IN


def test_classification_reorder_and_dashboard_still_count_every_sale():
    assert step3.load_fact(_db())["quantity_sold"].sum() == ALL
    series, _products, _idx = step5.load_series(_db())
    assert series[1].sum() == ALL
    con = _db()
    con.row_factory = sqlite3.Row
    stats, _series = catalog.compute_stats(con)
    assert stats[1]["total_units"] == ALL


def test_a_database_without_the_column_keeps_every_sale():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, quantity_sold INTEGER)")
    assert order_types.walk_in_only(con) == "1 = 1"
    order_types.ensure_column(con)
    assert order_types.walk_in_only(con).startswith("COALESCE(")


def test_step2_reads_the_stores_answers(tmp_path):
    path = tmp_path / "bulk_day_candidates.csv"
    path.write_text(
        "calendar_date,item_name,units,bulk_order_confirmed\n"
        "2024-12-19,New Clappers,700,yes\n"
        "2025-01-23,Tote Bag Canvas White,671,pre-order\n"
        "2025-06-10,New Clappers,90,no\n"
        "2025-07-01,New Clappers,80,\n"
        "2025-08-01,New Clappers,85,dunno\n"
        "2025-09-01,Not An Item,85,bulk\n", encoding="utf-8")
    confirmed, unreadable = step2.load_confirmed_orders(
        {"New Clappers": 7, "Tote Bag Canvas White": 9}, str(path))
    assert confirmed == {(7, "2024-12-19"): "bulk", (9, "2025-01-23"): "pre_order"}
    assert unreadable == [("2025-08-01", "New Clappers", "dunno")]
    assert step2.load_confirmed_orders({}, str(tmp_path / "missing.csv")) == ({}, [])


# ------------------------------------------------------------------ the API

@pytest.fixture
def api(tmp_path, monkeypatch):
    import app as backend
    import db as dbmod
    db_path = tmp_path / "ustore.db"
    con = sqlite3.connect(db_path)
    con.executescript(f"""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, supplier_name TEXT);
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
            quantity_sold INTEGER, imputation_flag INTEGER, tally_date_flag INTEGER,
            transaction_type TEXT, entered_by TEXT, order_type TEXT, is_censored INTEGER);
        {dbmod.PENDING_IMPORT_DDL};
        INSERT INTO Dim_Product VALUES (1, 'Lanyard', 'VARSITY LIFESTYLE');
        INSERT INTO Dim_Date VALUES (1, '2026-08-03');
    """)
    con.close()
    vocab = tmp_path / "vocab.csv"
    vocab.write_text("raw_name,canonical_item_name,merged,row_count,source,revisit_with_store\n"
                     "Lanyard,Lanyard,no,1,sales,\n", encoding="utf-8")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(backend, "VOCAB_CSV", vocab)
    monkeypatch.setattr(backend, "RAWDATA_DIR", tmp_path / "rawdata")
    client = backend.app.test_client()
    with client.session_transaction() as s:              # signed in (backend/auth.py)
        s["user"] = "staff"
    client.db = db_path
    return client


def rows(api):
    con = sqlite3.connect(api.db)
    try:
        return con.execute("SELECT transaction_type, quantity_sold, order_type FROM Fact_Sales "
                           "ORDER BY sale_id").fetchall()
    finally:
        con.close()


def entry(**kw):
    return {"product_id": 1, "quantity_sold": 2, "calendar_date": "2026-08-03",
            "transaction_type": "SALE", **kw}


def test_single_entries_carry_their_order_type(api):
    assert api.post("/api/tally", json=entry()).status_code == 200
    r = api.post("/api/tally", json=entry(order_type="bulk"))
    assert r.status_code == 200 and r.get_json()["entry"]["order_type"] == "bulk"
    assert api.post("/api/tally", json=entry(transaction_type="DAMAGED", order_type="bulk")).status_code == 200
    bad = api.post("/api/tally", json=entry(order_type="whatever"))
    assert bad.status_code == 400 and "order_type" in bad.get_json()["errors"]
    assert rows(api) == [("sale", 2, "walk_in"), ("sale", 2, "bulk"), ("damaged", 2, None)]
    listed = api.get("/api/tally?date=2026-08-03").get_json()
    assert {r["order_type"] for r in listed} == {"walk_in", "bulk", None}


def test_imports_read_an_order_type_column(api):
    csv_text = ("Date,Item,Quantity,Order Type\n"
                "2026-08-03,Lanyard,3,\n"
                "2026-08-03,Lanyard,50,Organisation\n"
                "2026-08-03,Lanyard,4,someday\n"
                "2026-08-03,Brand New Pin,7,pre-order\n")
    r = api.post("/api/tally/import", data={"file": (io.BytesIO(csv_text.encode()), "aug.csv")},
                 content_type="multipart/form-data").get_json()
    assert (r["imported"], r["held"], r["rejected_total"]) == (2, 1, 1)
    assert rows(api) == [("sale", 3, "walk_in"), ("sale", 50, "bulk")]
    con = sqlite3.connect(api.db)
    assert con.execute("SELECT raw_name, order_type FROM Pending_Import_Row").fetchall() == [
        ("Brand New Pin", "pre_order")]
    con.close()


# ------------------------------------------------------------ known orders

def test_known_orders_go_on_top_of_the_forecast_on_their_date():
    forecast = pd.DataFrame({
        "product_id": [1, 1, 2], "forecast_date": ["2026-10-05", "2026-10-06", "2026-10-05"],
        "yhat": [2.0, 3.0, 1.0], "yhat_lower": [0.0, 1.0, 0.0], "yhat_upper": [5.0, 6.0, 2.0]})
    orders = pd.DataFrame({"product_id": [1, 1, 9], "forecast_category": ["Bags", "Bags", None],
                           "forecast_date": ["2026-10-06", "2026-10-06", "2026-10-06"],
                           "quantity": [40, 10, 99]})
    out, added = order_types.add_known_orders(forecast, orders, "product_id")
    assert added == 50                                   # item 9 has no forecast row
    assert out["yhat"].tolist() == [2.0, 53.0, 1.0]
    assert out["yhat_lower"].tolist() == [0.0, 51.0, 0.0] and out["yhat_upper"].tolist() == [5.0, 56.0, 2.0]
    assert list(out.columns) == list(forecast.columns)
    same, none = order_types.add_known_orders(forecast, orders.iloc[0:0], "product_id")
    assert none == 0 and same.equals(forecast)


def test_known_orders_table_is_read_with_the_items_category():
    con = sqlite3.connect(":memory:")
    assert order_types.known_orders(con).empty
    import db as dbmod
    con.executescript(f"""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, forecast_category TEXT);
        INSERT INTO Dim_Product VALUES (1, 'Bags');
        {dbmod.UPCOMING_ORDER_DDL};
        INSERT INTO Upcoming_Order (product_id, expected_date, quantity, order_type) VALUES (1, '2026-10-06', 40, 'bulk');
    """)
    assert order_types.known_orders(con).to_dict("records") == [
        {"product_id": 1, "forecast_category": "Bags", "forecast_date": "2026-10-06", "quantity": 40}]


def test_upcoming_orders_api(api, monkeypatch):
    import db as dbmod
    con = sqlite3.connect(api.db)
    con.execute(dbmod.UPCOMING_ORDER_DDL)
    con.close()
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=5)).isoformat()
    bad = api.post("/api/orders/upcoming", json={"product_id": 99, "expected_date": "2020-01-01",
                                                 "quantity": 0, "order_type": "walk-in"}).get_json()
    assert set(bad["errors"]) == {"product_id", "expected_date", "quantity", "order_type"}
    r = api.post("/api/orders/upcoming", json={"product_id": 1, "expected_date": soon, "quantity": 40,
                                               "order_type": "Organisation", "note": "Student council"})
    assert r.status_code == 200
    (o,) = api.get("/api/orders/upcoming").get_json()
    assert (o["item_name"], o["quantity"], o["order_type"]) == ("Lanyard", 40, "bulk")
    assert api.delete(f"/api/orders/upcoming/{o['order_id']}").status_code == 200
    assert api.get("/api/orders/upcoming").get_json() == []
