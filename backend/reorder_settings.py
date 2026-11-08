"""
reorder_settings.py - an item's reorder settings, saved by the store.

Where the what-if (whatif.py) shows what a change WOULD do and saves nothing,
these are kept: the store sets an item's lead time, safety stock, ordering
cost or holding cost, and the Reorder table, the suggested orders and the
forecast guidance all use them from then on.

Only the inputs are set. The reorder point and EOQ are never typed in: they
are worked out from the inputs with the formulas step5_prescriptive.py uses,
so the figures on screen always add up -

  reorder point    = daily demand x lead time + safety stock
  EOQ              = sqrt(2 x yearly demand x ordering cost / holding cost)

and a setting left blank keeps the calculated figure. One carry-over from the
what-if: a new lead time with no safety stock of its own scales the
calculated buffer by sqrt(new / calculated lead time), the textbook rule, as
the buffer cannot be re-measured here. A typed safety stock is used as is.

Stored apart from the pipeline's results, in Reorder_Setting (backend/db.py),
and applied when the figures are read (app._reorder_items). Result_Prescriptive
is never written, so step5's verification gates still check exactly what the
pipeline produced. Keyed by item name, as Product_Status is, because step1
rebuilds Dim_Product on every run and a product_id need not survive it; no
pipeline step clears the table, so a setting stays until it is reset.

A cost applies to both EOQ cost scenarios (low admin cost, high goods value):
a typed cost is the store's own figure, which replaces both estimates.
"""
import math

# field: (lowest, highest, label, whole number)
FIELDS = {
    "lead_time_days": (1, 180, "Lead time", True),
    "safety_stock": (0, 1_000_000, "Safety stock", False),
    "ordering_cost_php": (1, 10_000_000, "Ordering cost", False),
    "holding_cost_php": (0.01, 100_000, "Holding cost", False),
}
NOTE_MAX = 300


def parse(payload):
    """(values, errors) from a request body. A field missing, null or blank is
    None: keep the calculated figure. Errors are {field: sentence}."""
    values, errors = {}, {}
    for key, (lo, hi, label, whole) in FIELDS.items():
        raw = payload.get(key)
        if raw is None or str(raw).strip() == "":
            values[key] = None
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            errors[key] = f"{label} must be a number."
            continue
        if not math.isfinite(v) or v < lo or v > hi:
            errors[key] = f"{label} must be between {lo:,} and {hi:,}."
            continue
        if whole:
            if not v.is_integer():
                errors[key] = f"{label} must be a whole number of days."
                continue
            v = int(v)
        values[key] = v
    note = str(payload.get("note") or "").strip()
    if len(note) > NOTE_MAX:
        errors["note"] = f"Keep the note under {NOTE_MAX} characters."
    values["note"] = note or None
    return values, errors


def any_set(values):
    return any(values.get(k) is not None for k in FIELDS)


def load(con):
    """{item_name: setting row} - empty when nothing is saved or the table
    does not exist yet (an older ustore.db)."""
    try:
        rows = con.execute("SELECT * FROM Reorder_Setting").fetchall()
    except Exception:          # sqlite3.OperationalError: no such table
        return {}
    cols = [d[0] for d in con.execute("SELECT * FROM Reorder_Setting LIMIT 0").description]
    return {r[0]: dict(zip(cols, r)) for r in rows}


def eoq(annual, ordering, holding):
    """step5_prescriptive.eoq: 0.0 unless every input is positive."""
    if annual and ordering and holding and annual > 0 and ordering > 0 and holding > 0:
        return math.sqrt(2.0 * annual * ordering / holding)
    return 0.0


def _total_cost(annual, ordering, holding, q):
    """step5_prescriptive.total_cost: TC(Q) = (D/Q)*S + (Q/2)*H."""
    if not q or q <= 0 or not annual or not ordering or not holding:
        return None
    return (annual / q) * ordering + (q / 2.0) * holding


def calculated(item):
    """The pipeline's own figures for an item, before any setting."""
    low = item["scenarios"].get("low_admin_cost") or {}
    return {
        "lead_time_days": item["lead_time_days"],
        "safety_stock": item["safety_stock"],
        "reorder_point": item["reorder_point"],
        "ordering_cost_php": low.get("ordering_cost_php"),
        "holding_cost_php": low.get("holding_cost_php_per_unit_year"),
        "eoq": low.get("eoq"),
    }


def apply(item, setting):
    """Put a saved setting into a priced item from app._reorder_items, before
    its stock decisions are worked out. Keeps the pipeline's figures under
    item["calculated"] and says what was set under item["adjusted"]."""
    calc = calculated(item)
    item["calculated"] = calc
    d = item["avg_daily_demand"] or 0.0
    lt0, ss0 = calc["lead_time_days"], calc["safety_stock"] or 0.0

    lt = setting.get("lead_time_days") or lt0
    if setting.get("safety_stock") is not None:
        ss = float(setting["safety_stock"])
    else:
        ss = ss0 * math.sqrt(lt / lt0) if lt0 else ss0
    item["lead_time_days"] = lt
    item["safety_stock"] = ss
    item["reorder_point"] = d * lt + ss

    annual = item["annual_demand"]
    for scen in item["scenarios"].values():
        s = setting.get("ordering_cost_php") or scen["ordering_cost_php"]
        h = setting.get("holding_cost_php") or scen["holding_cost_php_per_unit_year"]
        q = eoq(annual, s, h)
        scen.update({
            "ordering_cost_php": s,
            "holding_cost_php_per_unit_year": h,
            "eoq": q,
            "cost_at_eoq": _total_cost(annual, s, h, q),
            "cost_at_half_eoq": _total_cost(annual, s, h, q / 2),
            "cost_at_double_eoq": _total_cost(annual, s, h, q * 2),
        })

    item["adjusted"] = {
        **{k: setting.get(k) for k in FIELDS},
        "note": setting.get("note"),
        "changed_by": setting.get("changed_by"),
        "changed_at": setting.get("changed_at"),
    }
    return item


def figures(item, review_days):
    """What the settings form shows for an item: the inputs, the reorder
    point and EOQ they give, and the order they lead to today. The order uses
    app._reorder_items' rule (order-up-to = reorder point + one review period
    of demand), so a preview matches the table once saved."""
    d = item["avg_daily_demand"] or 0.0
    low = item["scenarios"].get("low_admin_cost") or {}
    rop = item["reorder_point"]
    stock = item.get("current_stock")
    needs = None if stock is None else stock <= rop
    target = rop + review_days * d
    annual = item["annual_demand"]
    q = low.get("eoq")
    return {
        "lead_time_days": item["lead_time_days"],
        "safety_stock": item["safety_stock"],
        "lead_time_demand": d * item["lead_time_days"],
        "reorder_point": rop,
        "ordering_cost_php": low.get("ordering_cost_php"),
        "holding_cost_php": low.get("holding_cost_php_per_unit_year"),
        "eoq": q,
        "eoq_exceeds_annual_demand": None if not q or not annual else q > annual,
        "stock_on_hand": stock,
        "needs_reorder": needs,
        "suggested_order_qty": None if stock is None else (max(0, int(round(target - stock))) if needs else 0),
    }
