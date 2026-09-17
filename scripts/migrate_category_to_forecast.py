"""
scripts/migrate_category_to_forecast.py
------------------------------------------------------------------
Point `Dim_Product.category` at the semantic forecast categories.

The problem this fixes
----------------------
The project carries TWO category systems and the dashboard was reading the
wrong one:

  Dim_Product.category           APPAREL / NON-APPAREL / MAIN STORAGE / NULL
                                 - a storage and handling grouping, inherited
                                   from the source workbooks
  Dim_Product.forecast_category  Outerwear / Shirts & Tops / Drinkware /
                                 Umbrellas & Gear / Home & Novelty / ... (12)
                                 - written by step1b_categorize_products.py
                                   and the grain every model in this repo
                                   actually groups by

`backend/catalog.py` selects `category`, and `forecast_category` is not
exposed by the backend at all. So filtering the dashboard by "APPAREL"
selected a set no forecast, FSN run or benchmark had ever grouped by.

What this does
--------------
  1. Adds `storage_category` and copies the CURRENT `category` into it, so
     the APPAREL / NON-APPAREL / MAIN STORAGE grouping is preserved rather
     than destroyed. Nothing reads it yet; it is kept because it came from
     the source data and this migration is not entitled to delete it.
  2. Sets `category = forecast_category` for every product that has one.
  3. Leaves products with no forecast_category as 'Uncategorised', which is
     what `catalog.py` already substitutes for NULL.

Re-running is safe: `storage_category` is only populated from `category` on
the first run, when it is still holding the original values.

Durability: `step1b_categorize_products.py` writes `forecast_category` only,
so a later pipeline run would leave `category` stale. That script is patched
by this one's companion change to write both columns, so the two cannot
drift apart again.

Run:  python scripts/migrate_category_to_forecast.py [--db ustore.db] [--dry-run]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "ustore.db")


def columns(con, table):
    return {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    try:
        cols = columns(con, "Dim_Product")
        if "forecast_category" not in cols:
            print("Dim_Product has no forecast_category - "
                  "run scripts/step1b_categorize_products.py first")
            return 1

        print("=" * 78)
        print("MIGRATE Dim_Product.category -> forecast_category")
        print("=" * 78)

        before = con.execute(
            "SELECT COALESCE(category,'(null)'), COUNT(*) FROM Dim_Product "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        print("category BEFORE:")
        for name, n in before:
            print("   %-16s %4d" % (name, n))

        target = con.execute(
            "SELECT COALESCE(forecast_category,'(null)'), COUNT(*) FROM Dim_Product "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        print("\nforecast_category (the target):")
        for name, n in target:
            print("   %-22s %4d" % (name, n))

        if args.dry_run:
            print("\n--dry-run: nothing written")
            return 0

        # 1. preserve the original grouping
        if "storage_category" not in cols:
            con.execute("ALTER TABLE Dim_Product ADD COLUMN storage_category TEXT")
            con.execute("UPDATE Dim_Product SET storage_category = category")
            print("\n[+] added storage_category and preserved the original values")
        else:
            print("\n[=] storage_category already exists - original values left as they are")

        # 2. repoint category
        n = con.execute(
            "UPDATE Dim_Product SET category = forecast_category "
            "WHERE forecast_category IS NOT NULL AND forecast_category <> ''"
        ).rowcount
        con.execute(
            "UPDATE Dim_Product SET category = 'Uncategorised' "
            "WHERE category IS NULL OR category = ''")
        con.commit()

        after = con.execute(
            "SELECT category, COUNT(*) FROM Dim_Product GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        print("[+] repointed %d products\n" % n)
        print("category AFTER:")
        for name, cnt in after:
            print("   %-22s %4d" % (name, cnt))

        # Which of these will actually carry a 30-day forecast: only Fast SKUs
        # are forecast, so a category with none will show the pending state.
        print("\nForecast coverage per category (Fast SKUs drive Result_Forecast):")
        rows = con.execute("""
            -- COUNT(DISTINCT ...), not COUNT(*): Result_Forecast holds 30
            -- rows per product, so a plain count multiplies every product
            -- by its horizon.
            SELECT p.category,
                   COUNT(DISTINCT p.product_id) AS products,
                   COUNT(DISTINCT CASE WHEN p.fsn_class='F'
                                       THEN p.product_id END) AS fast,
                   COUNT(DISTINCT f.product_id) AS with_forecast
            FROM Dim_Product p
            LEFT JOIN Result_Forecast f ON f.product_id = p.product_id
            GROUP BY p.category ORDER BY with_forecast DESC, products DESC
        """).fetchall()
        for name, products, fast, wf in rows:
            flag = "" if wf else "   <- no forecast, will show the pending state"
            print("   %-22s products %3d  fast %2d  forecast %2d%s"
                  % (name, products, fast or 0, wf, flag))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
