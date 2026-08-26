"""
app.py — USTore backend (Flask + SQLite), Phase 3 of docs/PROMPT_3_BACKEND.md.

Reads and writes ../ustore.db directly (see db.py for why: it's the same
database the ETL pipeline already builds and populates - this app is not a
second seed path). Endpoint shapes match what
`UST Prototype Design/src/services/dataService.js` already consumes, per
`UST Prototype Design/BACKEND_TODO.md` - so swapping the frontend from
mocks to this API is a same-shape change, not a rewrite.

BIR compliance (see docs/PROMPT_1_FRONTEND.md / docs/PROMPT_3_BACKEND.md §1): this is
an internal inventory counting service only. No checkout, payment, customer
total, or receipt endpoint exists anywhere in this file, and none should be
added.

Run:
    pip install -r requirements.txt
    python app.py
Serves on http://127.0.0.1:5000. The frontend dev server proxies /api to
this port (see vite.config.js) so no CORS is needed in normal dev use;
flask-cors is enabled anyway as a fallback for direct access.
"""
from datetime import date, datetime

from flask import Flask, Response, g, jsonify, request
from flask_cors import CORS

import batch_pdf
import catalog
import db as dbmod
import pipeline
import validation

app = Flask(__name__)
CORS(app)


def con():
    if "con" not in g:
        g.con = dbmod.get_db()
    return g.con


@app.teardown_appcontext
def _close_con(_exc):
    c = g.pop("con", None)
    if c is not None:
        c.close()


def _bool_flag(v):
    return request.args.get(v) in ("1", "true", "True")


def _us_date(iso):
    """'2026-10-09' -> '10/09/2026', for dates baked into prose the frontend
    renders as-is. Structured date fields stay ISO - the frontend formats
    those itself (lib/format.js usDate) and anything feeding an API call or an
    <input type="date"> must remain ISO. This is only for the advisory
    `description` strings, which are sentences, not data."""
    try:
        y, m, d = str(iso).split("-")
        return f"{m}/{d}/{y}" if len(y) == 4 else str(iso)
    except (ValueError, AttributeError):
        return str(iso)


# --------------------------------------------------------------- metadata

@app.get("/api/meta")
def get_meta():
    c = con()
    products = dbmod.rows(c, "SELECT product_id, unit_price_php FROM Dim_Product")
    stats, _ = catalog.compute_stats(c)
    dim_date_span = dbmod.one(c, "SELECT MIN(calendar_date) a, MAX(calendar_date) b FROM Dim_Date")
    sales_span = dbmod.one(c, """
        SELECT MIN(d.calendar_date) a, MAX(d.calendar_date) b
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """)
    fact_sales_rows = c.execute("SELECT COUNT(*) FROM Fact_Sales").fetchone()[0]
    total_units = c.execute("SELECT SUM(quantity_sold) FROM Fact_Sales").fetchone()[0]
    n_params = c.execute("SELECT COUNT(*) FROM Dim_Parameters").fetchone()[0]
    n_reorder = c.execute("SELECT COUNT(*) FROM Result_Prescriptive").fetchone()[0]
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_forecast = "Result_Forecast" in tables and c.execute(
        "SELECT COUNT(*) FROM Result_Forecast").fetchone()[0] > 0

    priced = [p for p in products if p["unit_price_php"] is not None]
    unpriced_units = sum(
        stats[p["product_id"]]["total_units"]
        for p in products
        if p["unit_price_php"] is None and p["product_id"] in stats
    )

    return jsonify({
        "generated_at": date.today().isoformat(),
        "source": "ustore.db (live)",
        "sales_span": [sales_span["a"], sales_span["b"]],
        "calendar_span": [dim_date_span["a"], dim_date_span["b"]],
        "fact_sales_rows": fact_sales_rows,
        "total_units": total_units,
        "products": len(products),
        "products_with_sales": len(stats),
        "products_with_price": len(priced),
        "unpriced_units": unpriced_units,
        "products_with_stock": sum(1 for s in stats.values() if s["current_stock"] is not None),
        "recent_window_days": 60,
        "available": {
            "sales": True,
            "fsn": True,
            "batch_report": True,
            "forecast": bool(has_forecast),
            "reorder": n_params > 0 and n_reorder > 0,
        },
        # Only populated when the thing is actually pending. This used to be a
        # fixed string blaming a missing cmdstan build, rendered even on
        # databases that had forecasts in them. Both halves were wrong: recent
        # prophet releases ship their own cmdstan, so a missing forecast means
        # step4 has not been run (it is the long, opt-out step in the Tally
        # Interface's pipeline runner), not that it cannot be.
        "pending_reason": {
            "forecast": None if has_forecast else FORECAST_PENDING_REASON,
            "reorder": "Dim_Parameters is empty - no lead times, ordering or holding costs "
                       "have been collected yet (Block 5, the USTore site visit)."
                       if not (n_params > 0 and n_reorder > 0) else None,
        },
    })


@app.get("/api/suppliers")
def get_suppliers():
    cat = catalog.compute_catalog(con())
    return jsonify([catalog.ALL_SUPPLIERS, *sorted({p["supplier_name"] for p in cat})])


@app.get("/api/categories")
def get_categories():
    cat = catalog.compute_catalog(con())
    return jsonify([catalog.ALL_CATEGORIES, *sorted({p["category"] for p in cat})])


@app.get("/api/months")
def get_months():
    return jsonify(catalog.months_seen(con()))


# ---------------------------------------------------------------- catalog

@app.get("/api/products")
def get_products():
    c = con()
    cat = catalog.compute_catalog(c)
    supplier = request.args.get("supplier") or None
    category = request.args.get("category") or None
    cat = [p for p in cat if catalog.matches(p, supplier, category)]
    if _bool_flag("has_history"):
        cat = [p for p in cat if p["is_active"] and p["total_units"] >= 0 and p["has_history"]]
        cat.sort(key=lambda p: p["item_name"])
    return jsonify(cat)


@app.get("/api/products/<int:product_id>/history")
def get_product_history(product_id):
    c = con()
    rows = dbmod.rows(c, """
        SELECT substr(d.calendar_date, 1, 7) AS month,
               SUM(f.quantity_sold) AS units, COUNT(DISTINCT f.date_id) AS tally_days
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.product_id = ?
        GROUP BY 1 ORDER BY 1
    """, (product_id,))
    return jsonify(rows)


# ------------------------------------------------------------------ sales

@app.get("/api/sales/monthly")
def get_monthly_units():
    c = con()
    cat = catalog.compute_catalog(c)
    supplier = request.args.get("supplier") or None
    category = request.args.get("category") or None
    date_range = request.args.get("dateRange") or None
    keep = {p["product_id"] for p in cat if catalog.matches(p, supplier, category)}
    price_by_id = {p["product_id"]: p["unit_price_php"] for p in cat}
    months = catalog.months_seen(c)

    monthly = dbmod.rows(c, """
        SELECT f.product_id, substr(d.calendar_date, 1, 7) AS month, SUM(f.quantity_sold) AS units
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        GROUP BY 1, 2
    """)
    by_month = {}
    for row in monthly:
        if row["product_id"] not in keep or not catalog.in_range(row["month"], date_range, months):
            continue
        acc = by_month.setdefault(row["month"], {"month": row["month"], "units": 0, "revenue": 0.0, "priced_units": 0})
        acc["units"] += row["units"]
        price = price_by_id.get(row["product_id"])
        if price is not None:
            acc["revenue"] += row["units"] * price
            acc["priced_units"] += row["units"]
    return jsonify(sorted(by_month.values(), key=lambda a: a["month"]))


def build_batch_report(c, month):
    """The month's sales grouped by supplier — the shared body of both
    /api/reports/batch (JSON, for the screen) and /api/reports/batch.pdf
    (the document Purchasing and Finance actually receive). Neither may
    compute this differently from the other."""
    cat = catalog.compute_catalog(c)
    product_by_id = {p["product_id"]: p for p in cat}

    monthly = dbmod.rows(c, """
        SELECT f.product_id, substr(d.calendar_date, 1, 7) AS month, SUM(f.quantity_sold) AS units
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE substr(d.calendar_date, 1, 7) = ?
        GROUP BY 1, 2
    """, (month,))

    by_supplier = {}
    for row in monthly:
        if row["units"] <= 0:
            continue
        p = product_by_id.get(row["product_id"])
        if not p:
            continue
        supplier = p["supplier_name"] or catalog.UNATTRIBUTED
        entry = by_supplier.setdefault(supplier, {
            "supplier": supplier, "items": [], "total_units": 0, "subtotal": 0.0, "unpriced_units": 0,
        })
        line_total = row["units"] * p["unit_price_php"] if p["unit_price_php"] is not None else None
        entry["items"].append({
            "item_name": p["item_name"],
            "quantity": row["units"],
            "unit_price_php": p["unit_price_php"],
            "line_total": line_total,
        })
        entry["total_units"] += row["units"]
        if line_total is not None:
            entry["subtotal"] += line_total
        else:
            entry["unpriced_units"] += row["units"]

    out = []
    for entry in by_supplier.values():
        entry["items"].sort(key=lambda i: i["quantity"], reverse=True)
        out.append(entry)
    out.sort(key=lambda e: e["subtotal"], reverse=True)
    return out


@app.get("/api/reports/batch")
def get_batch_report():
    month = request.args.get("month")
    if not month:
        return jsonify({"ok": False, "errors": {"month": "month=YYYY-MM is required."}}), 400
    return jsonify(build_batch_report(con(), month))


@app.get("/api/reports/batch.pdf")
def get_batch_report_pdf():
    """The batch sales report as a real PDF file.

    Rendered server-side with **fpdf2**, chosen because it is pure Python with
    no system libraries behind it. This is what previously blocked the feature:
    weasyprint needs GTK/Pango/Cairo installed separately on Windows, and
    reportlab pulls a C extension - neither is something a store's machine
    should have to acquire to print a monthly report. fpdf2 installs from a
    plain wheel and was already present in this environment.

    `?inline=1` serves it for viewing in the browser (the Print Preview
    button); the default is a download (Export as PDF).
    """
    month = request.args.get("month")
    if not month or not validation.ISO_MONTH_RE.match(month):
        return jsonify({"ok": False, "errors": {"month": "month=YYYY-MM is required."}}), 400

    report = build_batch_report(con(), month)
    pdf_bytes = batch_pdf.render(report, month)

    disposition = "inline" if _bool_flag("inline") else "attachment"
    filename = f"USTore_Batch_Sales_Report_{month}.pdf"
    return Response(pdf_bytes, mimetype="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Content-Length": str(len(pdf_bytes)),
    })


# -------------------------------------------------------------------- FSN

@app.get("/api/fsn/sensitivity")
def get_fsn_sensitivity():
    return jsonify(catalog.fsn_sensitivity(con()))


# --------------------------------------------------------------- reorder

# One tally/billing cycle. Matches the 30-day horizon the forecast targets
# (§3.3.2) and the monthly supplier-remittance cycle the store already runs on,
# so "enough to last until the next count" means the same thing in both places.
REVIEW_PERIOD_DAYS = 30

ORDER_QTY_NOTE = (
    "Suggested quantity brings stock up to the reorder point plus "
    f"{REVIEW_PERIOD_DAYS} days of demand at the observed rate. It is deliberately "
    "not EOQ: under the provisional ordering/holding costs (Block 5/B9, still "
    "estimates), EOQ works out larger than a whole year of demand for nearly "
    "every SKU, so ordering it would mean buying years of stock. EOQ stays in "
    "the table below as the theoretical figure, flagged where it exceeds annual "
    "demand."
)


@app.get("/api/reorder")
def get_reorder():
    c = con()
    n_params = c.execute("SELECT COUNT(*) FROM Dim_Parameters").fetchone()[0]
    reorder_rows = dbmod.rows(c, """
        SELECT rp.*, p.item_name, p.supplier_name
        FROM Result_Prescriptive rp
        JOIN Dim_Product p ON p.product_id = rp.product_id
        ORDER BY p.item_name, rp.ordering_cost_scenario
    """)
    if n_params == 0 or not reorder_rows:
        return jsonify({
            "available": False,
            "reason": "Dim_Parameters is empty - no lead times, ordering or holding costs "
                      "have been collected yet (Block 5, the USTore site visit).",
            "data": None,
        })

    by_product = {}
    for r in reorder_rows:
        pid = r["product_id"]
        item = by_product.setdefault(pid, {
            "product_id": pid,
            "item_name": r["item_name"],
            "supplier_name": r["supplier_name"],
            "fsn_class": r["fsn_class"],
            "lead_time_days": r["lead_time_days"],
            "lead_time_category": r["lead_time_category"],
            "avg_daily_demand": r["avg_daily_demand"],
            "annual_demand": r["annual_demand"],
            "sigma_demand": r["sigma_demand"],
            "sigma_source": r["sigma_source"],
            "z_value": r["z_value"],
            "safety_stock": r["safety_stock"],
            "reorder_point": r["reorder_point"],
            "demand_method": r["demand_method"],
            "is_provisional": bool(r["is_provisional"]),
            "scenarios": {},
        })
        item["scenarios"][r["ordering_cost_scenario"]] = {
            "ordering_cost_php": r["ordering_cost_php"],
            "holding_cost_php_per_unit_year": r["holding_cost_php_per_unit_year"],
            "eoq": r["eoq"],
            "cost_at_eoq": r["cost_at_eoq"],
            "cost_at_half_eoq": r["cost_at_half_eoq"],
            "cost_at_double_eoq": r["cost_at_double_eoq"],
        }

    # ---- what to actually do about it -------------------------------
    # Joining stock here rather than leaving the frontend to do it: the
    # order quantity below has to be derived from on-hand, and a screen must
    # not invent an analytic the backend has not computed.
    stats, _ = catalog.compute_stats(c)

    for item in by_product.values():
        st = stats.get(item["product_id"], {})
        stock = st.get("current_stock")
        add = item["avg_daily_demand"] or 0.0
        rop = item["reorder_point"] or 0.0

        item["current_stock"] = stock
        item["stock_as_of"] = st.get("stock_as_of")
        item["stock_source"] = st.get("stock_source")
        item["days_cover_remaining"] = round(stock / add, 1) if (stock is not None and add > 0) else None
        item["needs_reorder"] = stock is not None and stock <= rop
        item["approaching_rop"] = stock is not None and rop < stock <= rop * 1.2

        # Order-up-to quantity, NOT EOQ. See ORDER_QTY_NOTE: EOQ under the
        # current provisional cost inputs comes out larger than a year of
        # demand for essentially every SKU, so it cannot be handed to staff
        # as "order this many". This is the textbook (s, S) alternative -
        # bring stock up to the reorder point plus one review period of
        # demand - and it uses only the three inputs that are actually
        # measured here (on-hand, ADUS, lead time), not the two cost
        # estimates that are still guesses.
        target = rop + REVIEW_PERIOD_DAYS * add
        item["order_up_to_level"] = round(target, 1)
        item["suggested_order_qty"] = (
            max(0, int(round(target - stock))) if item["needs_reorder"] else 0
        )

        for scen in item["scenarios"].values():
            scen["exceeds_annual_demand"] = bool(
                item["annual_demand"] and scen["eoq"] > item["annual_demand"]
            )

    items = sorted(by_product.values(), key=lambda i: i["item_name"])
    due = [i for i in items if i["needs_reorder"]]
    eoq_over = sum(
        1 for i in items if i["scenarios"].get("low_admin_cost", {}).get("exceeds_annual_demand")
    )

    return jsonify({
        "available": True,
        "reason": None,
        "data": {
            "items": items,
            "summary": {
                "priced_skus": len(items),
                "with_stock_count": sum(1 for i in items if i["current_stock"] is not None),
                "no_stock_count": sum(1 for i in items if i["current_stock"] is None),
                "reorder_now": len(due),
                "approaching_rop": sum(1 for i in items if i["approaching_rop"]),
                "suggested_units_total": sum(i["suggested_order_qty"] for i in due),
                "suppliers_affected": len({i["supplier_name"] for i in due if i["supplier_name"]}),
                "review_period_days": REVIEW_PERIOD_DAYS,
                "order_qty_note": ORDER_QTY_NOTE,
                "eoq_exceeding_annual_demand": eoq_over,
            },
        },
    })


# ---------------------------------------------------------------- stock

@app.get("/api/stock")
def get_stock():
    c = con()
    cat = catalog.compute_catalog(c)
    supplier = request.args.get("supplier") or None
    category = request.args.get("category") or None
    items = [p for p in cat if catalog.matches(p, supplier, category) and p["current_stock"] is not None]
    items.sort(key=lambda p: (p["days_of_supply"] is None, p["days_of_supply"] or 0))
    return jsonify({"items": items, "covered": len(items), "total": len(cat)})


# ------------------------------------------------------------ inventory

@app.get("/api/inventory")
def get_inventory_counts():
    """Staff-entered stock counts for one month (?month=YYYY-MM), newest
    first, joined to the product name so the table needs no second call.

    Also returns `workbook_month`: the last month the historical inventory
    workbook covers. The interface shows it so whoever is counting can see
    where the existing data stops and what they are extending."""
    month = request.args.get("month") or ""
    if not validation.ISO_MONTH_RE.match(month):
        return jsonify({"ok": False, "errors": {"count_month": "Month must be YYYY-MM."}}), 400

    counts = dbmod.rows(con(), """
        SELECT ic.count_id, ic.product_id, p.item_name, p.supplier_name, p.category,
               ic.count_month, ic.quantity, ic.note, ic.date_logged
          FROM Inventory_Count ic
          JOIN Dim_Product p ON p.product_id = ic.product_id
         WHERE ic.count_month = ?
         ORDER BY ic.count_id DESC
    """, (month,))
    for r in counts:
        r["supplier_name"] = r["supplier_name"] or catalog.UNATTRIBUTED

    workbook = catalog.load_csv_stock()
    return jsonify({
        "month": month,
        "counts": counts,
        "total_units": sum(r["quantity"] for r in counts),
        "workbook_month": max((v["month"] for v in workbook.values()), default=None),
    })


@app.post("/api/inventory")
def add_inventory_count():
    """Record (or correct) one product's stock count for one month.

    Upsert, not append: `Inventory_Count` is UNIQUE on (product_id,
    count_month), and a recount replaces the earlier figure rather than
    adding to it. The response says which of the two happened so the
    interface can tell the user it overwrote something."""
    payload = request.get_json(silent=True) or {}
    c = con()
    errors = validation.validate_inventory_count(c, payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    product_id = int(payload["product_id"])
    count_month = payload["count_month"]
    quantity = int(float(payload["quantity"]))
    note = (payload.get("note") or "").strip() or None

    previous = dbmod.one(c, """
        SELECT quantity FROM Inventory_Count WHERE product_id = ? AND count_month = ?
    """, (product_id, count_month))

    c.execute("""
        INSERT INTO Inventory_Count (product_id, count_month, quantity, note, counted_by, date_logged)
        VALUES (?, ?, ?, ?, 'local', ?)
        ON CONFLICT (product_id, count_month) DO UPDATE SET
            quantity = excluded.quantity,
            note = excluded.note,
            date_logged = excluded.date_logged
    """, (product_id, count_month, quantity, note, datetime.now().isoformat(timespec="seconds")))
    c.commit()

    product = dbmod.one(c, "SELECT item_name FROM Dim_Product WHERE product_id = ?", (product_id,))
    return jsonify({
        "ok": True,
        "replaced": previous["quantity"] if previous else None,
        "count": {
            "product_id": product_id,
            "item_name": product["item_name"],
            "count_month": count_month,
            "quantity": quantity,
            "note": note,
        },
    })


@app.delete("/api/inventory/<int:count_id>")
def delete_inventory_count(count_id):
    c = con()
    if not dbmod.one(c, "SELECT 1 FROM Inventory_Count WHERE count_id = ?", (count_id,)):
        return jsonify({"ok": False, "error": "No such inventory count."}), 404
    c.execute("DELETE FROM Inventory_Count WHERE count_id = ?", (count_id,))
    c.commit()
    return jsonify({"ok": True})


# -------------------------------------------------------------- calendar

@app.get("/api/calendar")
def get_calendar():
    return jsonify(dbmod.rows(con(), "SELECT * FROM Dim_Date ORDER BY calendar_date"))


@app.get("/api/calendar/closed")
def get_closed_dates():
    rows = dbmod.rows(con(), """
        SELECT calendar_date FROM Dim_Date WHERE is_store_closed = 1 ORDER BY calendar_date
    """)
    return jsonify([r["calendar_date"] for r in rows])


@app.get("/api/calendar/<iso_date>")
def get_date_flags(iso_date):
    row = dbmod.one(con(), "SELECT * FROM Dim_Date WHERE calendar_date = ?", (iso_date,))
    return jsonify(row)


@app.put("/api/calendar/<iso_date>/closure")
def set_closure(iso_date):
    if not validation.ISO_DATE_RE.match(iso_date):
        return jsonify({"ok": False, "errors": {"calendar_date": "Date must be YYYY-MM-DD."}}), 400
    payload = request.get_json(silent=True) or {}
    closed = 1 if payload.get("closed") else 0
    reason = (payload.get("reason") or "").strip() or None

    c = con()
    # Remediation D3. Log the toggle in Closure_Log (durable across a
    # populate_dim_date.py rebuild, which reads it back - see that
    # script) AND update Dim_Date directly (so the change is visible
    # immediately, same dual-write shape as add_event() below).
    c.execute("""
        INSERT INTO Closure_Log (closure_date, is_closed, reason, created_by, date_logged)
        VALUES (?, ?, ?, 'local', ?)
    """, (iso_date, closed, reason, datetime.now().isoformat(timespec="seconds")))
    c.execute("UPDATE Dim_Date SET is_store_closed = ? WHERE calendar_date = ?", (closed, iso_date))
    c.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- tally

@app.get("/api/tally/recent")
def get_recent_entries():
    limit = request.args.get("limit", default=50, type=int)
    rows = dbmod.rows(con(), """
        SELECT f.sale_id, f.product_id, d.calendar_date, f.quantity_sold,
               UPPER(f.transaction_type) AS transaction_type, f.imputation_flag, f.is_censored,
               p.item_name, COALESCE(p.supplier_name, ?) AS supplier_name
        FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.quantity_sold > 0
        ORDER BY d.calendar_date DESC, f.sale_id DESC
        LIMIT ?
    """, (catalog.UNATTRIBUTED, limit))
    for r in rows:
        r["is_local"] = False
    return jsonify(rows)


@app.get("/api/tally")
def get_entries_by_date():
    iso_date = request.args.get("date")
    if not iso_date:
        return jsonify({"ok": False, "errors": {"calendar_date": "date=YYYY-MM-DD is required."}}), 400
    rows = dbmod.rows(con(), """
        SELECT f.sale_id, f.product_id, d.calendar_date, f.quantity_sold,
               UPPER(f.transaction_type) AS transaction_type, f.imputation_flag, f.is_censored,
               p.item_name, COALESCE(p.supplier_name, ?) AS supplier_name
        FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.quantity_sold > 0 AND d.calendar_date = ?
        ORDER BY f.sale_id DESC
    """, (catalog.UNATTRIBUTED, iso_date))
    for r in rows:
        r["is_local"] = False
    return jsonify(rows)


@app.post("/api/tally")
def add_entry():
    payload = request.get_json(silent=True) or {}
    c = con()
    errors = validation.validate_entry(c, payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    product_id = int(payload["product_id"])
    quantity_sold = int(float(payload["quantity_sold"]))
    calendar_date = payload["calendar_date"]
    transaction_type = str(payload["transaction_type"]).upper()

    date_row = dbmod.one(c, "SELECT date_id FROM Dim_Date WHERE calendar_date = ?", (calendar_date,))
    cur = c.execute("""
        INSERT INTO Fact_Sales
            (product_id, date_id, quantity_sold, imputation_flag, tally_date_flag, transaction_type)
        VALUES (?, ?, ?, 0, 0, ?)
    """, (product_id, date_row["date_id"], quantity_sold, transaction_type))
    c.commit()

    product = dbmod.one(c, "SELECT item_name, supplier_name FROM Dim_Product WHERE product_id = ?", (product_id,))
    entry = {
        "sale_id": cur.lastrowid,
        "product_id": product_id,
        "item_name": product["item_name"],
        "supplier_name": product["supplier_name"] or catalog.UNATTRIBUTED,
        "quantity_sold": quantity_sold,
        "calendar_date": calendar_date,
        "transaction_type": transaction_type,
        "is_local": False,
    }
    return jsonify({"ok": True, "entry": entry})


# ---------------------------------------------------------------- events

@app.get("/api/events")
def get_events():
    rows = dbmod.rows(con(), "SELECT * FROM Event_Log ORDER BY event_date DESC, event_id DESC")
    for r in rows:
        r["is_local"] = False
    return jsonify(rows)


@app.post("/api/events")
def add_event():
    payload = request.get_json(silent=True) or {}
    calendar_date = payload.get("calendar_date")
    event_name = (payload.get("event_name") or "").strip()
    event_description = (payload.get("event_description") or "").strip()

    errors = {}
    if not calendar_date:
        errors["calendar_date"] = "Pick a date."
    if not event_name:
        errors["event_name"] = "Give the event a label."
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    c = con()
    cur = c.execute("""
        INSERT INTO Event_Log (event_date, event_name, event_description, created_by, date_logged)
        VALUES (?, ?, ?, 'local', ?)
    """, (calendar_date, event_name, event_description, datetime.now().isoformat(timespec="seconds")))
    c.execute("UPDATE Dim_Date SET is_event_day = 1 WHERE calendar_date = ?", (calendar_date,))
    c.commit()

    event = {
        "event_id": cur.lastrowid,
        "event_date": calendar_date,
        "calendar_date": calendar_date,
        "event_name": event_name,
        "event_description": event_description,
        "created_by": "local",
        "is_local": False,
    }
    return jsonify({"ok": True, "event": event})


# -------------------------------------------------------------- forecast

def _has_forecast_table(c):
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return "Result_Forecast" in tables and c.execute(
        "SELECT COUNT(*) FROM Result_Forecast").fetchone()[0] > 0


# Wording is aimed at the person using the dashboard, not at whoever wrote the
# ETL: from the frontend there is exactly one action that fixes this, and it is
# the Tally Interface's pipeline runner.
FORECAST_PENDING_REASON = (
    "The pipeline has not been run yet, please run it from the tally interface and this page will automatically display the results."
)

_FORECAST_PENDING = {
    "available": False,
    "reason": FORECAST_PENDING_REASON,
    "data": None,
}


@app.get("/api/forecast")
def get_forecast_general():
    c = con()
    if not _has_forecast_table(c):
        return jsonify(_FORECAST_PENDING)
    # Return a summary: list of product_ids that have forecasts
    products_with_forecast = dbmod.rows(c, """
        SELECT DISTINCT rf.product_id, p.item_name, p.fsn_class, p.is_hvl,
               rf.model_type, rf.is_heuristic, rf.snapshot_date
        FROM Result_Forecast rf
        JOIN Dim_Product p ON p.product_id = rf.product_id
        GROUP BY rf.product_id
    """)
    return jsonify({
        "available": True,
        "reason": None,
        "data": {
            "product_count": len(products_with_forecast),
            "products": products_with_forecast,
        },
    })


@app.get("/api/forecast/<int:product_id>")
def get_forecast(product_id):
    c = con()
    if not _has_forecast_table(c):
        return jsonify(_FORECAST_PENDING)

    forecast_rows = dbmod.rows(c, """
        SELECT forecast_date, yhat, yhat_lower, yhat_upper,
               model_type, is_heuristic, snapshot_date
        FROM Result_Forecast
        WHERE product_id = ?
        ORDER BY forecast_date
    """, (product_id,))

    if not forecast_rows:
        return jsonify({
            "available": False,
            "reason": f"No forecast exists for product {product_id}.",
            "data": None,
        })

    # Metrics from Result_Forecast_Metrics
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    metrics = []
    if "Result_Forecast_Metrics" in tables:
        metrics = dbmod.rows(c, """
            SELECT tier, validation_method, period_scope, n_obs,
                   mae, rmse, mape, naive_mae, naive_rmse, naive_mape,
                   beats_naive_mae, meets_mape_threshold, snapshot_date
            FROM Result_Forecast_Metrics
            WHERE product_id = ?
        """, (product_id,))

    product = dbmod.one(c,
        "SELECT item_name, fsn_class, is_hvl FROM Dim_Product WHERE product_id = ?",
        (product_id,))

    return jsonify({
        "available": True,
        "reason": None,
        "data": {
            "product_id": product_id,
            "item_name": product["item_name"] if product else None,
            "fsn_class": product["fsn_class"] if product else None,
            "is_hvl": product["is_hvl"] if product else 0,
            "model_type": forecast_rows[0]["model_type"],
            "is_heuristic": bool(forecast_rows[0]["is_heuristic"]),
            "snapshot_date": forecast_rows[0]["snapshot_date"],
            "forecast": forecast_rows,
            "metrics": metrics,
        },
    })


# ------------------------------------------------------------ advisories

@app.get("/api/advisories")
def get_advisories():
    """Calendar-contextual advisories: combine demand forecast (if any) with
    upcoming calendar events to produce actionable stocking recommendations.
    Returns an honest pending shape when the forecast hasn't been generated."""
    c = con()
    has_forecast = _has_forecast_table(c)

    # Upcoming events from Event_Log
    upcoming_events = dbmod.rows(c, """
        SELECT e.event_date, e.event_name, e.event_description,
               d.is_enrollment_period, d.is_exam_week, d.is_sem_break,
               d.semester_id, d.semester_week
        FROM Event_Log e
        LEFT JOIN Dim_Date d ON d.calendar_date = e.event_date
        WHERE e.event_date >= date('now')
        ORDER BY e.event_date
        LIMIT 10
    """)

    # Upcoming calendar periods (enrollment, exams) from Dim_Date
    upcoming_periods = dbmod.rows(c, """
        SELECT calendar_date, semester_id, semester_week,
               is_enrollment_period, is_exam_week, is_event_day, is_sem_break
        FROM Dim_Date
        WHERE calendar_date >= date('now')
          AND (is_enrollment_period = 1 OR is_exam_week = 1 OR is_event_day = 1)
        ORDER BY calendar_date
        LIMIT 30
    """)

    advisories = []

    # Build advisories from calendar signals
    period_types = {}
    for row in upcoming_periods:
        if row["is_enrollment_period"]:
            period_types.setdefault("enrollment", []).append(row["calendar_date"])
        if row["is_exam_week"]:
            period_types.setdefault("exam_week", []).append(row["calendar_date"])

    if "enrollment" in period_types:
        dates = period_types["enrollment"]
        advisories.append({
            "type": "enrollment",
            "severity": "high",
            "title": "Enrollment Period Approaching",
            "description": f"Enrollment runs {_us_date(dates[0])} to {_us_date(dates[-1])}. "
                           "Historically the highest-volume sales window — "
                           "ensure Fast-moving items (uniforms, IDs, school supplies) are stocked.",
            "date_range": [dates[0], dates[-1]],
            "has_forecast": has_forecast,
        })

    if "exam_week" in period_types:
        dates = period_types["exam_week"]
        advisories.append({
            "type": "exam_week",
            "severity": "medium",
            "title": "Exam Week Upcoming",
            "description": f"Exams scheduled {_us_date(dates[0])} to {_us_date(dates[-1])}. "
                           "Expect reduced foot traffic; delay non-urgent restocking.",
            "date_range": [dates[0], dates[-1]],
            "has_forecast": has_forecast,
        })

    for event in upcoming_events:
        advisories.append({
            "type": "event",
            "severity": "medium",
            "title": event["event_name"],
            "description": event["event_description"] or
                           f"Event on {_us_date(event['event_date'])}. Check stock for high-demand items.",
            "date_range": [event["event_date"], event["event_date"]],
            "has_forecast": has_forecast,
        })

    # If forecast exists, add top fast-movers that may need restocking
    if has_forecast:
        # Get fast-moving items with low projected supply
        fast_items = dbmod.rows(c, """
            SELECT p.item_name, p.product_id,
                   SUM(rf.yhat) AS total_forecast_30d
            FROM Result_Forecast rf
            JOIN Dim_Product p ON p.product_id = rf.product_id
            WHERE p.fsn_class = 'F'
            GROUP BY rf.product_id
            ORDER BY total_forecast_30d DESC
            LIMIT 5
        """)
        if fast_items:
            names = ", ".join(i["item_name"] for i in fast_items[:3])
            advisories.insert(0, {
                "type": "forecast_alert",
                "severity": "high",
                "title": "Top Forecasted Demand — Next 30 Days",
                "description": f"Highest projected demand: {names}. "
                               "Review stock levels against the forecast on the Demand Forecast page.",
                "date_range": None,
                "has_forecast": True,
                "top_items": fast_items,
            })

    if not advisories:
        advisories.append({
            "type": "info",
            "severity": "low",
            "title": "No upcoming calendar signals",
            "description": "No enrollment, exam weeks, or flagged events are coming up. "
                           "Advisories appear here when calendar signals exist."
                           + ("" if has_forecast
                              else " The demand forecast has not been generated yet — "
                                   "run the pipeline from the Tally Interface to enable "
                                   "forecast-based advisories."),
            "date_range": None,
            "has_forecast": has_forecast,
        })

    return jsonify({
        "available": True,
        "has_forecast": has_forecast,
        "advisories": advisories,
    })


# --------------------------------------------------------------- pipeline

@app.post("/api/pipeline/run")
def run_pipeline():
    """Body (optional): {"skip": ["step4"]}, or the friendlier
    {"include_forecast": false} the Tally Interface sends.

    Only step4 can be opted out of (pipeline.SKIPPABLE) - it is the only step
    that costs hours rather than seconds and the only one whose output no
    later step reads. Everything else always runs, so a "quick" run still
    produces a fully rebuilt database, FSN classes and reorder points."""
    payload = request.get_json(silent=True) or {}
    skip = set(payload.get("skip") or [])
    if payload.get("include_forecast") is False:
        skip.add("step4")

    started = pipeline.start_pipeline(skip)
    if not started:
        return jsonify({"ok": False, "error": "The pipeline is already running."}), 409
    return jsonify({"ok": True, "skipped": sorted(skip & pipeline.SKIPPABLE)})


@app.post("/api/pipeline/stop")
def stop_pipeline():
    stopped = pipeline.stop_pipeline()
    if not stopped:
        return jsonify({"ok": False, "error": "No pipeline run is in progress."}), 409
    return jsonify({"ok": True})


@app.get("/api/pipeline/status")
def pipeline_status():
    return jsonify(pipeline.get_status())


@app.get("/api/pipeline/staleness")
def pipeline_staleness():
    """Whether the analytics on screen are older than what has been tallied.

    Everything the Digital Tallying Interface writes lands in the database
    immediately, but fsn_class / Result_Forecast / Result_Prescriptive are
    only recomputed when the pipeline runs - so a screen can be showing
    reorder points that predate a week of tally entries with nothing
    indicating it. See pipeline.get_staleness()."""
    return jsonify(pipeline.get_staleness(con()))


if __name__ == "__main__":
    app.run(debug=False, port=5000)
