"""
Generate the frontend's mock fixtures from the real pipeline output.

    python scripts/generate_fixtures.py

Reads ustore.db (and the mapped inventory CSV) at the repo root and writes
JSON into src/services/fixtures/. Field names deliberately mirror the star
schema in create_schema.py - product_id, item_name, unit_price_php,
calendar_date, quantity_sold - so that when Phase 3 replaces the fixtures
with a real API, only dataService.js changes and no screen does.

Nothing here invents a number. Anything the pipeline has not produced is
absent from the fixtures and flagged in meta.json's `available` block, so
the UI can render a "pending" state driven by data rather than by a
hardcoded string. In particular there are NO forecasts and NO ROP/EOQ:
step4_prophet_forecast.py needs cmdstan and has not been re-run, and
Dim_Parameters is empty.
"""
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                      # scripts -> uploads -> UST Prototype Design -> Capstone
DB = REPO / "ustore.db"
INVENTORY = REPO / "USTore_inventory_excel_long_mapped.csv"
OUT = HERE.parent / "src" / "services" / "fixtures"

RECENT_DAYS = 60          # window for the tally screen's recent-entries list
THRESHOLDS = (75, 80, 85)  # must match step3_fsn_classification.py


def write(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  {name:26} {path.stat().st_size / 1024:8.1f} KB")


def rows(con, sql, params=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, params)]


def load_current_stock():
    """Latest inventory count per canonical item. This is the only stock
    signal the project has, and it covers a minority of products - see
    Block 3 in the repo README."""
    import csv
    latest = {}
    with open(INVENTORY, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            item = r["canonical_item_name"].strip()
            month = r["Date"][:7]
            try:
                qty = float(r["Quantity"] or 0)
            except ValueError:
                continue
            cur = latest.get(item)
            if cur is None or month > cur["month"]:
                latest[item] = {"month": month, "qty": 0.0}
                cur = latest[item]
            if month == latest[item]["month"]:
                latest[item]["qty"] += qty
    return latest


def fsn_sensitivity(products, stats):
    """Reproduces step3_fsn_classification.py's sensitivity table: the F/S
    split at three ADUS percentile cutoffs over the moving population.
    Same rule, same data - not a re-invention."""
    moving = sorted(s["adus"] for s in stats.values() if s["active_tally_dates"] > 0)
    n_non_moving = sum(1 for p in products if p["product_id"] not in stats
                       or stats[p["product_id"]]["active_tally_dates"] == 0)

    def quantile(sorted_vals, q):
        if not sorted_vals:
            return 0.0
        pos = (len(sorted_vals) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)

    out = {}
    for t in THRESHOLDS:
        cutoff = quantile(moving, t / 100)
        fast = sum(1 for v in moving if v >= cutoff)
        out[f"p{t}"] = {
            "F": fast,
            "S": len(moving) - fast,
            "N": n_non_moving,
            "cutoff": round(cutoff, 4),
        }
    return out


def main():
    if not DB.exists():
        raise SystemExit(f"{DB} not found - rebuild it first (see the repo README).")
    con = sqlite3.connect(DB)

    # ---------- Dim_Product ----------
    products = rows(con, """
        SELECT product_id, item_name, category, unit_price_php, supplier_name,
               payment_status, lead_time_days, fsn_class, is_hvl, entry_date, is_active
        FROM Dim_Product ORDER BY item_name
    """)

    # ---------- Dim_Date ----------
    dim_date = rows(con, """
        SELECT date_id, calendar_date, semester_id, semester_week,
               is_enrollment_period, is_exam_week, is_event_day, is_sem_break,
               is_tally_date, is_store_closed
        FROM Dim_Date ORDER BY calendar_date
    """)

    # ---------- per-product stats, all measured ----------
    agg = rows(con, """
        SELECT f.product_id,
               SUM(f.quantity_sold)                                   AS total_units,
               COUNT(DISTINCT f.date_id)                              AS observed_days,
               COUNT(DISTINCT CASE WHEN f.is_censored = 1 THEN f.date_id END) AS censored_days,
               SUM(CASE WHEN f.imputation_flag = 1 THEN f.quantity_sold * 0.5
                        ELSE f.quantity_sold END)                     AS weighted_units,
               MIN(d.calendar_date)                                   AS first_sale,
               MAX(d.calendar_date)                                   AS last_sale
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        GROUP BY f.product_id
    """)

    monthly = rows(con, """
        SELECT f.product_id, substr(d.calendar_date, 1, 7) AS month,
               SUM(f.quantity_sold) AS units, COUNT(DISTINCT f.date_id) AS tally_days
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    series = defaultdict(list)
    for m in monthly:
        series[m["product_id"]].append(m)

    # latest days_of_supply actually derived by step2 (NULL for most SKUs)
    dos = dict(con.execute("""
        SELECT f.product_id, f.days_of_supply FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.days_of_supply IS NOT NULL
          AND d.calendar_date = (SELECT MAX(d2.calendar_date) FROM Fact_Sales f2
                                 JOIN Dim_Date d2 ON d2.date_id = f2.date_id
                                 WHERE f2.product_id = f.product_id AND f2.days_of_supply IS NOT NULL)
    """).fetchall())

    stock = load_current_stock()
    by_name = {p["item_name"]: p for p in products}

    stats = {}
    for a in agg:
        pid = a["product_id"]
        # ADUS uses the same denominator step3 does: observed days minus
        # censored ones (a day with nothing to sell is not slow movement)
        denom = a["observed_days"] - a["censored_days"]
        units_by_month = [m["units"] for m in series[pid]]
        sigma = statistics.pstdev(units_by_month) if len(units_by_month) > 1 else 0.0
        mean_month = sum(units_by_month) / len(units_by_month) if units_by_month else 0.0
        name = next((p["item_name"] for p in products if p["product_id"] == pid), None)
        st = stock.get(name)
        stats[pid] = {
            "product_id": pid,
            "total_units": a["total_units"],
            "observed_days": a["observed_days"],
            "censored_days": a["censored_days"],
            "active_tally_dates": denom,
            "adus": round(a["weighted_units"] / denom, 4) if denom else 0.0,
            "avg_monthly": round(mean_month, 1),
            "sigma_monthly": round(sigma, 2),
            "cv": round(sigma / mean_month * 100, 1) if mean_month else 0.0,
            "first_sale": a["first_sale"],
            "last_sale": a["last_sale"],
            "current_stock": int(st["qty"]) if st else None,
            "stock_as_of": st["month"] if st else None,
            "days_of_supply": round(dos[pid], 1) if pid in dos else None,
        }

    # ---------- recent real entries, for the tally screen ----------
    last_date = con.execute("""
        SELECT MAX(d.calendar_date) FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """).fetchone()[0]
    recent = rows(con, """
        SELECT f.sale_id, f.product_id, f.date_id, d.calendar_date, f.quantity_sold,
               f.imputation_flag, f.transaction_type, f.is_censored
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.quantity_sold > 0 AND d.calendar_date >= date(?, ?)
        ORDER BY d.calendar_date DESC, f.sale_id DESC
    """, (last_date, f"-{RECENT_DAYS} days"))

    # ---------- what the pipeline has NOT produced ----------
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_forecast = "Result_Forecast" in tables and con.execute(
        "SELECT COUNT(*) FROM Result_Forecast").fetchone()[0] > 0 if "Result_Forecast" in tables else False
    n_params = con.execute("SELECT COUNT(*) FROM Dim_Parameters").fetchone()[0]

    priced = [p for p in products if p["unit_price_php"] is not None]
    unpriced_units = sum(stats[p["product_id"]]["total_units"]
                         for p in products
                         if p["unit_price_php"] is None and p["product_id"] in stats)

    meta = {
        "generated_at": date.today().isoformat(),
        "source": "ustore.db (see the repo README for the rebuild command)",
        # the span of actual sales, not of the calendar dimension
        "sales_span": list(con.execute("""
            SELECT MIN(d.calendar_date), MAX(d.calendar_date)
            FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        """).fetchone()),
        "calendar_span": [dim_date[0]["calendar_date"], dim_date[-1]["calendar_date"]],
        "fact_sales_rows": con.execute("SELECT COUNT(*) FROM Fact_Sales").fetchone()[0],
        "total_units": con.execute("SELECT SUM(quantity_sold) FROM Fact_Sales").fetchone()[0],
        "products": len(products),
        "products_with_sales": len(stats),
        "products_with_price": len(priced),
        "unpriced_units": unpriced_units,
        "products_with_stock": sum(1 for s in stats.values() if s["current_stock"] is not None),
        "recent_window_days": RECENT_DAYS,
        # The UI reads these to decide between rendering a number and
        # rendering a "pending" state. Phase 3 should serve the same shape.
        "available": {
            "sales": True,
            "fsn": True,
            "batch_report": True,
            "forecast": bool(has_forecast),
            "reorder": n_params > 0,
        },
        "pending_reason": {
            "forecast": "step4_prophet_forecast.py has not been re-run (needs cmdstan); "
                        "Result_Forecast does not exist in the database.",
            "reorder": "Dim_Parameters is empty - no lead times, ordering or holding costs "
                       "have been collected yet (Block 5, the USTore site visit).",
        },
    }

    print("Writing fixtures:")
    write("dim_product.json", products)
    write("dim_date.json", dim_date)
    write("fact_sales_monthly.json", monthly)
    write("fact_sales_recent.json", recent)
    write("product_stats.json", list(stats.values()))
    write("fsn_sensitivity.json", fsn_sensitivity(products, stats))
    write("event_log.json", rows(con, "SELECT * FROM Event_Log"))
    write("meta.json", meta)

    print(f"\n{len(products)} products, {len(stats)} with sales, "
          f"{meta['total_units']} units, {len(recent)} recent entries")
    print(f"forecast available: {meta['available']['forecast']} | "
          f"reorder available: {meta['available']['reorder']}")
    con.close()


if __name__ == "__main__":
    main()
