"""
scripts/verify_rebuild_state.py
------------------------------------------------------------------
Does ustore.db actually contain everything the rebuild was supposed to
put there?

Every check is a statement that can fail. This is not a summary printer -
a report that says "725 rows" tells you nothing unless something is
checking that 725 is the number it should be, and that the rows say what
they are supposed to say. Each check prints PASS or FAIL and the script
exits non-zero if any FAIL, so it can be wired into CI or run after a
pipeline as a gate.

Covers, in the order they were built:
  1. Dim_Day_Status          colour-derived operating status per date
  2. Fact_Batch_Sales        2023 aggregates, NOT resampled to daily
  3. Dim_Product.category    repointed to the semantic categories
  4. Dim_Product.storage_category   the ORIGINAL values, preserved
  5. Dim_Demand_Cluster      unsupervised demand-profile clusters
  6. Result_Forecast         Prophet, as a per-day curve not a flat level
  7. Result_Forecast_Metrics accuracy per SKU
  8. Result_Category_Prophet_Metrics  the category-grain Prophet run
  9. Result_Prescriptive     ROP / EOQ still computed downstream

Run:  python scripts/verify_rebuild_state.py [--db ustore.db]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "ustore.db")

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", label, detail))


def tables(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def cols(con, table):
    return {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}


def scalar(con, sql, args=()):
    row = con.execute(sql, args).fetchone()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("no database at %s" % args.db)
        return 1

    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    t = tables(con)
    print("=" * 88)
    print("VERIFYING %s" % os.path.abspath(args.db))
    print("=" * 88)

    # ---- 1. Dim_Day_Status ------------------------------------------
    print("\n1. Dim_Day_Status - operating status read from workbook cell colour")
    if "Dim_Day_Status" not in t:
        check("table exists", False, "run scripts/rebuild_extract_tbs.py")
    else:
        n = scalar(con, "SELECT COUNT(*) FROM Dim_Day_Status")
        check("table exists and is populated", n > 0, "%d dated days" % n)
        check("has status_source (colour vs calendar provenance)",
              "status_source" in cols(con, "Dim_Day_Status"))
        closed = scalar(con, "SELECT COUNT(*) FROM Dim_Day_Status WHERE is_store_closed=1")
        check("closed days recorded", closed > 0, "%d days" % closed)
        unobs = scalar(con,
                       "SELECT COUNT(*) FROM Dim_Day_Status WHERE demand_observable=0")
        check("days where a zero is NOT demand", unobs > 0, "%d days" % unobs)
        # the colour evidence must actually dominate
        src = dict(con.execute(
            "SELECT status_source, COUNT(*) FROM Dim_Day_Status GROUP BY 1"))
        check("colour is the primary source",
              src.get("colour", 0) > src.get("calendar", 0),
              ", ".join("%s=%d" % kv for kv in sorted(src.items())))
        # a CLOSED day must never be marked observable
        bad = scalar(con, "SELECT COUNT(*) FROM Dim_Day_Status "
                          "WHERE day_status='CLOSED' AND demand_observable=1")
        check("no CLOSED day is marked demand-observable", bad == 0,
              "violations: %d" % bad)

    # ---- 2. Fact_Batch_Sales ----------------------------------------
    print("\n2. Fact_Batch_Sales - 2023 batch totals, ingested AS aggregates")
    if "Fact_Batch_Sales" not in t:
        check("table exists", False, "run scripts/rebuild_extract_tbs.py")
    else:
        n = scalar(con, "SELECT COUNT(*) FROM Fact_Batch_Sales")
        b = scalar(con, "SELECT COUNT(DISTINCT batch_label) FROM Fact_Batch_Sales")
        u = scalar(con, "SELECT SUM(total_quantity) FROM Fact_Batch_Sales")
        check("populated", n > 0, "%d rows, %d batches, %.0f units" % (n, b, u or 0))
        c = cols(con, "Fact_Batch_Sales")
        # The whole point of the requirement: these must not have been
        # resampled to a daily frequency, so there must be no date column.
        check("carries NO date column (not resampled to daily)",
              not any("date" in x.lower() for x in c),
              "columns: %s" % ", ".join(sorted(c)))
        check("grain is labelled batch_aggregate",
              scalar(con, "SELECT COUNT(*) FROM Fact_Batch_Sales "
                          "WHERE grain <> 'batch_aggregate'") == 0)

    # ---- 3/4. Dim_Product category columns --------------------------
    print("\n3. Dim_Product.category - repointed to the semantic categories")
    pc = cols(con, "Dim_Product")
    check("forecast_category column present", "forecast_category" in pc)
    check("storage_category column present (originals preserved)",
          "storage_category" in pc)
    n_cats = scalar(con, "SELECT COUNT(DISTINCT category) FROM Dim_Product")
    check("category holds the semantic set", n_cats >= 10,
          "%d distinct values" % n_cats)
    mismatch = scalar(con,
                      "SELECT COUNT(*) FROM Dim_Product "
                      "WHERE forecast_category IS NOT NULL "
                      "AND forecast_category <> '' AND category <> forecast_category")
    check("category == forecast_category everywhere", mismatch == 0,
          "mismatched rows: %d" % mismatch)
    for want in ("Outerwear", "Shirts & Tops", "Drinkware",
                 "Umbrellas & Gear", "Home & Novelty"):
        n = scalar(con, "SELECT COUNT(*) FROM Dim_Product WHERE category=?", (want,))
        check("  category present: %s" % want, n > 0, "%d products" % n)

    print("\n4. Dim_Product.storage_category - the ORIGINAL grouping, not destroyed")
    if "storage_category" in pc:
        kept = dict(con.execute(
            "SELECT COALESCE(storage_category,'(null)'), COUNT(*) "
            "FROM Dim_Product GROUP BY 1 ORDER BY 2 DESC"))
        check("original values still readable",
              any(k in kept for k in ("APPAREL", "NON-APPAREL", "MAIN STORAGE")),
              ", ".join("%s=%d" % kv for kv in kept.items()))

    # ---- 5. Dim_Demand_Cluster --------------------------------------
    print("\n5. Dim_Demand_Cluster - unsupervised demand-profile clustering")
    if "Dim_Demand_Cluster" not in t:
        check("table exists", False, "run scripts/cluster_demand_profiles.py")
    else:
        n = scalar(con, "SELECT COUNT(*) FROM Dim_Demand_Cluster")
        k = scalar(con, "SELECT COUNT(DISTINCT cluster) FROM Dim_Demand_Cluster")
        check("populated", n > 0 and k >= 2, "%d SKUs across k=%d clusters" % (n, k))
        check("clusters cut across the semantic categories",
              scalar(con, "SELECT COUNT(DISTINCT semantic_category) "
                          "FROM Dim_Demand_Cluster") > 1)

    # ---- 6. Result_Forecast -----------------------------------------
    print("\n6. Result_Forecast - written by whichever model step4 is set to")
    if "Result_Forecast" not in t:
        check("table exists", False)
    else:
        n = scalar(con, "SELECT COUNT(*) FROM Result_Forecast")
        skus = scalar(con, "SELECT COUNT(DISTINCT product_id) FROM Result_Forecast")
        check("populated", n > 0, "%d rows across %d SKUs" % (n, skus))

        # Asserted against step4's OWN default rather than a name written
        # here, so switching the model does not turn this check into a
        # false alarm - and a table left behind by a different model than
        # the one currently configured still fails, which is the case worth
        # catching.
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        try:
            import step4_forecast_model as s4
            expected, curve_models = s4.DEFAULT_MODEL, s4.CURVE_MODELS
        except Exception:
            expected, curve_models = None, set()

        models = dict(con.execute(
            "SELECT model_type, COUNT(*) FROM Result_Forecast GROUP BY 1"))
        check("model_type matches step4's configured default",
              expected is None or set(models) == {expected},
              "table=%s, step4 default=%s"
              % (", ".join(sorted(models)), expected))

        # A curve model must produce a per-day curve; a flat model must not.
        # If step4 ever wrote a Prophet forecast as its own mean, every SKU
        # would collapse to one distinct yhat and the calendar shape - the
        # only reason to run Prophet at all - would be silently discarded.
        varying = scalar(con, """
            SELECT COUNT(*) FROM (
                SELECT product_id FROM Result_Forecast
                GROUP BY product_id HAVING COUNT(DISTINCT yhat) > 1)
        """)
        if expected in curve_models:
            check("forecast VARIES by day (curve model, not flattened)",
                  varying > 0, "%d of %d SKUs vary" % (varying, skus))
        else:
            check("forecast is flat per SKU (as a level model should be)",
                  varying == 0,
                  "%d of %d SKUs vary (expected 0)" % (varying, skus))
        check("no negative forecast",
              scalar(con, "SELECT COUNT(*) FROM Result_Forecast WHERE yhat < 0") == 0)

    # ---- 7/8. metrics -----------------------------------------------
    print("\n7. Result_Forecast_Metrics - per-SKU accuracy")
    if "Result_Forecast_Metrics" in t:
        n = scalar(con, "SELECT COUNT(*) FROM Result_Forecast_Metrics "
                        "WHERE mae IS NOT NULL")
        check("scored SKUs present", n > 0, "%d scored rows" % n)
    else:
        check("table exists", False)

    print("\n8. Result_Category_Prophet_Metrics - the category-grain run")
    if "Result_Category_Prophet_Metrics" not in t:
        check("table exists", False, "run scripts/forecast_category_prophet.py")
    else:
        n = scalar(con, "SELECT COUNT(*) FROM Result_Category_Prophet_Metrics")
        c = cols(con, "Result_Category_Prophet_Metrics")
        check("populated", n > 0, "%d categories" % n)
        check("carries all four metrics",
              {"mape_pct", "mae", "mase", "rmse"} <= c,
              "has: %s" % ", ".join(sorted(c & {"mape_pct", "mae", "mase", "rmse"})))

    # ---- 9. downstream still works ----------------------------------
    print("\n9. Result_Prescriptive - ROP / EOQ still derived downstream")
    if "Result_Prescriptive" in t:
        n = scalar(con, "SELECT COUNT(*) FROM Result_Prescriptive")
        check("populated after the Prophet switch", n > 0, "%d SKUs" % n)
    else:
        check("table exists", False)

    con.close()
    failed = RESULTS.count(False)
    print("\n" + "=" * 88)
    print("%d checks, %d passed, %d FAILED" % (len(RESULTS), RESULTS.count(True), failed))
    print("=" * 88)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
