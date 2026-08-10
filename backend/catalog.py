"""
catalog.py — per-product measured stats (ADUS, stock, days of supply, etc.),
computed live from ustore.db on every request instead of being dumped to a
JSON fixture once. The math is ported unchanged from
`UST Prototype Design/scripts/generate_fixtures.py` (the reference
implementation Phase 1 wrote for exactly this computation) - same queries,
same denominators, just re-expressed as live reads so a new tally shows up
immediately instead of needing a fixture regeneration.

Nothing here invents a number: anything the pipeline has not produced
(current_stock, days_of_supply) stays None/null rather than being guessed.
"""
import csv
import statistics
from collections import defaultdict

from db import INVENTORY_CSV, rows

ALL_SUPPLIERS = "All Suppliers"
ALL_CATEGORIES = "All Categories"
UNATTRIBUTED = "Unattributed"


def load_current_stock():
    """Latest inventory count per canonical item name. The only stock
    signal this project has; covers a minority of products (Block 3)."""
    if not INVENTORY_CSV.exists():
        return {}
    latest = {}
    with open(INVENTORY_CSV, encoding="utf-8") as f:
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


def compute_stats(con):
    """product_id -> measured stats dict. Mirrors generate_fixtures.py."""
    products = rows(con, "SELECT product_id, item_name FROM Dim_Product")
    name_by_id = {p["product_id"]: p["item_name"] for p in products}

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

    # generate_fixtures.py picks this via a correlated MAX(...) subquery,
    # fine for a one-off script but O(n^2) without an index when re-run on
    # every request; a single pass over the candidate rows is equivalent
    # and doesn't need one.
    dos_candidates = rows(con, """
        SELECT f.product_id, d.calendar_date, f.days_of_supply
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.days_of_supply IS NOT NULL
    """)
    dos, dos_date = {}, {}
    for r in dos_candidates:
        pid = r["product_id"]
        if pid not in dos_date or r["calendar_date"] > dos_date[pid]:
            dos_date[pid] = r["calendar_date"]
            dos[pid] = r["days_of_supply"]

    stock = load_current_stock()

    stats = {}
    for a in agg:
        pid = a["product_id"]
        denom = a["observed_days"] - a["censored_days"]
        units_by_month = [m["units"] for m in series[pid]]
        sigma = statistics.pstdev(units_by_month) if len(units_by_month) > 1 else 0.0
        mean_month = sum(units_by_month) / len(units_by_month) if units_by_month else 0.0
        st = stock.get(name_by_id.get(pid))
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
    return stats, series


def compute_catalog(con):
    """Dim_Product joined to its measured stats — mirrors dataService.js's
    CATALOG constant."""
    products = rows(con, """
        SELECT product_id, item_name, category, unit_price_php, supplier_name,
               payment_status, lead_time_days, fsn_class, is_hvl, entry_date, is_active
        FROM Dim_Product ORDER BY item_name
    """)
    stats, _series = compute_stats(con)

    catalog = []
    for p in products:
        s = stats.get(p["product_id"], {})
        total_units = s.get("total_units") or 0
        row = {
            **p,
            "supplier_name": p["supplier_name"] or UNATTRIBUTED,
            "category": p["category"] or "Uncategorised",
            "total_units": total_units,
            "adus": s.get("adus", 0.0),
            "avg_monthly": s.get("avg_monthly", 0.0),
            "cv": s.get("cv", 0.0),
            "active_tally_dates": s.get("active_tally_dates", 0),
            "censored_days": s.get("censored_days", 0),
            "current_stock": s.get("current_stock"),
            "stock_as_of": s.get("stock_as_of"),
            "days_of_supply": s.get("days_of_supply"),
            "first_sale": s.get("first_sale"),
            "last_sale": s.get("last_sale"),
            "revenue": (total_units * p["unit_price_php"]) if p["unit_price_php"] is not None else None,
            "has_history": p["product_id"] in stats,
        }
        catalog.append(row)
    return catalog


def matches(product, supplier=None, category=None):
    return (not supplier or supplier == ALL_SUPPLIERS or product["supplier_name"] == supplier) \
        and (not category or category == ALL_CATEGORIES or product["category"] == category)


def months_seen(con):
    return sorted({r["month"] for r in rows(con, """
        SELECT DISTINCT substr(d.calendar_date, 1, 7) AS month
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """)})


def in_range(month, date_range, months):
    if date_range == "All Time" or not months:
        return True
    n = {"Last 3 Months": 3, "Last 6 Months": 6, "Last 12 Months": 12}.get(date_range, 12)
    cutoff_idx = max(0, len(months) - n)
    return month >= months[cutoff_idx]


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def fsn_sensitivity(con, thresholds=(75, 80, 85)):
    """Reproduces step3_fsn_classification.py's sensitivity table, same as
    generate_fixtures.py: the F/S split at three ADUS percentile cutoffs."""
    product_ids = [r["product_id"] for r in rows(con, "SELECT product_id FROM Dim_Product")]
    stats, _ = compute_stats(con)
    moving = sorted(s["adus"] for s in stats.values() if s["active_tally_dates"] > 0)
    n_non_moving = sum(1 for pid in product_ids if pid not in stats or stats[pid]["active_tally_dates"] == 0)

    out = {}
    for t in thresholds:
        cutoff = quantile(moving, t / 100)
        fast = sum(1 for v in moving if v >= cutoff)
        out[f"p{t}"] = {
            "F": fast,
            "S": len(moving) - fast,
            "N": n_non_moving,
            "cutoff": round(cutoff, 4),
        }
    return out
