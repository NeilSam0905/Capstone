"""
tests/test_transaction_type.py
------------------------------------------------------------------
Fact_Sales holds sales and, from the Tally Interface, non-sale removals
(damaged, promo, transfer). Historical rows store 'sale'; the interface
used to store 'SALE'. Every demand reader must count both spellings of a
sale and nothing else:

  - the category series (step1b) used to match only lower-case 'sale', so
    interface sales vanished from the category forecasts;
  - FSN (step3), the item forecast (step4) and the reorder points (step5)
    did not filter at all, so damaged / promo / transfer counted as demand;
  - the dashboard's catalog stats (backend/catalog.py) likewise.

One fixture, one product: 5 + 3 units sold, 7 + 2 + 4 units removed
without a sale. Every reader must see 8.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import catalog  # noqa: E402  (backend/)
import step1b_categorize_products as step1b  # noqa: E402
import step3_fsn_classification as step3  # noqa: E402
import step4_forecast_model as step4  # noqa: E402
import step5_prescriptive as step5  # noqa: E402

SOLD = 8   # 5 ('sale', historical) + 3 ('SALE', interface before the fix)


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
                                 is_censored INTEGER, days_of_supply REAL);
        INSERT INTO Dim_Date VALUES (1, '2026-08-01', 0), (2, '2026-08-02', 0), (3, '2026-08-03', 0);
        INSERT INTO Dim_Product VALUES (1, 'Test Tote', 'F', 0, 18, 'Bags', 150);
        INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag, transaction_type) VALUES
            (1, 1, 5, 1, 'sale'),
            (1, 2, 3, 0, 'SALE'),
            (1, 3, 7, 0, 'damaged'),
            (1, 3, 2, 0, 'PROMO'),
            (1, 3, 4, 0, 'transfer');
    """)
    return con


def test_fsn_counts_sales_only():
    assert step3.load_fact(_db())["quantity_sold"].sum() == SOLD


def test_category_series_counts_both_spellings_of_a_sale():
    con = _db()
    products = pd.DataFrame({"product_id": [1], "forecast_category": ["Bags"]})
    assert step1b.category_daily_series(con, products).to_numpy().sum() == SOLD


def test_item_forecast_input_counts_sales_only():
    _products, fact, _dim_date = step4.load_common(_db())
    assert fact["quantity_sold"].sum() == SOLD


def test_reorder_demand_counts_sales_only():
    series, _products, _idx = step5.load_series(_db())
    assert series[1].sum() == SOLD


def test_dashboard_stats_count_sales_only():
    con = _db()
    con.row_factory = sqlite3.Row
    stats, _series = catalog.compute_stats(con)
    assert stats[1]["total_units"] == SOLD
    assert stats[1]["observed_days"] == 2      # the removal-only day is not a sales day
