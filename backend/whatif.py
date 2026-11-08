"""
whatif.py - the Reorder Alerts what-if: change an assumption for one item and
see what it does to its reorder point, suggested order, EOQ and the sales at
stake. Nothing is saved, and the real recommendations are left as they are.

Same formulas step5_prescriptive.py and app._reorder_items use (every stored
row matches them exactly), so with nothing changed the what-if shows the
figures the Reorder table shows:

  reorder point    = daily demand x lead time + safety stock
  order-up-to      = reorder point + one review period (30 days) of demand
  suggested order  = order-up-to - stock on hand, when stock <= reorder point
  EOQ              = sqrt(2 x yearly demand x ordering cost / holding cost)

What can be changed, and the one approximation:

  lead time         the safety stock is scaled by sqrt(new / current lead
                    time). The stored buffer is measured from the item's own
                    past shortfalls at its current lead time and cannot be
                    re-measured here; growing with the square root of the lead
                    time is the textbook rule, and a zero buffer stays zero.
  stock on hand     counts are often old, so a fresh count can be typed in.
  ordering cost,    EOQ only. The suggested order uses neither cost, which is
  holding cost      why it is what the dashboard suggests (ORDER_QTY_NOTE).
  what is coming    extra demand the store knows is coming before a delivery
                    ordered today could arrive; it joins the reorder point the
                    way the lead-time demand does:
      enrollment    daily demand x (lift - 1) x days
      term_start    the same for the first two weeks of the first term
      bulk          the units typed in
    The two lifts are measured live from the store's own sales
    (forecasting/day_kinds.py): average units per recorded day of that kind
    against a regular day, store-wide, applied to this item's daily demand.
    Only the days that fall inside the lead time are counted.

Sales at stake: the units the store would run short of before a delivery
ordered today arrives (lead-time demand + what is coming - stock on hand),
at the item's current listed price.
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)        # forecasting/; appended so backend's own modules stay first
from forecasting import day_kinds  # noqa: E402

TERM_START_DAYS = 7 * day_kinds.TERM_START_WEEKS

SCENARIOS = {
    "none": "Nothing extra",
    "enrollment": "Enrollment days (January or June)",
    "term_start": "First two weeks of the first term (August)",
    "bulk": "A bulk or organization order",
}

LIMITS = {
    "lead_time_days": (1, 180, "Lead time"),
    "stock_on_hand": (0, 1_000_000, "Stock on hand"),
    "scenario_days": (1, 60, "Days"),
    "bulk_units": (1, 100_000, "Units"),
    "ordering_cost_php": (1, 10_000_000, "Ordering cost"),
    "holding_cost_php": (0.01, 100_000, "Holding cost"),
}
WHOLE = {"lead_time_days", "stock_on_hand", "scenario_days", "bulk_units"}


def parse(args):
    """(params, errors) from the query string. A value left out keeps the
    item's current figure; errors are {field: sentence}."""
    params, errors = {}, {}
    for key, (lo, hi, label) in LIMITS.items():
        raw = args.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            errors[key] = f"{label} must be a number."
            continue
        if not math.isfinite(v) or v < lo or v > hi:
            errors[key] = f"{label} must be between {lo:,} and {hi:,}."
            continue
        if key in WHOLE:
            if not v.is_integer():
                errors[key] = f"{label} must be a whole number."
                continue
            v = int(v)
        params[key] = v
    scenario = (args.get("scenario") or "none").strip()
    if scenario not in SCENARIOS:
        errors["scenario"] = "Pick one of the options."
    elif scenario == "bulk" and "bulk_units" not in params and "bulk_units" not in errors:
        errors["bulk_units"] = "Type how many units the order is for."
    params["scenario"] = scenario
    return params, errors


def presets(lifts, enrollment_days):
    """The two measured busy periods, for the screen's menu."""
    out = []
    for key, default_days in (("enrollment", enrollment_days), ("term_start", TERM_START_DAYS)):
        lift = lifts.get(key) or {}
        out.append({"key": key, "label": SCENARIOS[key], "lift": lift.get("lift"),
                    "measured_days": lift.get("days", 0), "default_days": default_days})
    return out


def _figures(d, annual, lead_time, safety_stock, stock, extra, ordering_cost, holding_cost,
             price, review_days):
    lead_demand = d * lead_time
    rop = lead_demand + safety_stock + extra
    target = rop + review_days * d
    if stock is None:
        needs = order = short = None
    else:
        needs = stock <= rop
        order = max(0, int(round(target - stock))) if needs else 0
        short = max(0.0, lead_demand + extra - stock)
    eoq = (math.sqrt(2 * annual * ordering_cost / holding_cost)
           if annual and ordering_cost and holding_cost else None)
    return {
        "lead_time_days": lead_time,
        "stock_on_hand": stock,
        "lead_time_demand": lead_demand,
        "extra_units": extra,
        "safety_stock": safety_stock,
        "reorder_point": rop,
        "needs_reorder": needs,
        "order_up_to_level": target,
        "suggested_order_qty": order,
        "units_short": short,
        "php_at_stake": None if (short is None or price is None) else short * price,
        "ordering_cost_php": ordering_cost,
        "holding_cost_php": holding_cost,
        "eoq": eoq,
        "eoq_exceeds_annual_demand": None if eoq is None or not annual else eoq > annual,
    }


def compute(item, price, lifts, enrollment_days, params, review_days):
    """The current figures and the what-if figures for one priced item (an
    entry of app._reorder_items), as one JSON-ready dict."""
    d = item["avg_daily_demand"] or 0.0
    annual = item["annual_demand"]
    lt0 = item["lead_time_days"]
    ss0 = item["safety_stock"] or 0.0
    stock0 = item["current_stock"]
    low = item["scenarios"].get("low_admin_cost") or {}
    s0, h0 = low.get("ordering_cost_php"), low.get("holding_cost_php_per_unit_year")
    notes = []

    current = _figures(d, annual, lt0, ss0, stock0, 0.0, s0, h0, price, review_days)

    lt = params.get("lead_time_days", lt0)
    ss = ss0 * math.sqrt(lt / lt0) if lt0 else ss0
    if lt != lt0 and ss0 > 0:
        notes.append("Safety stock is scaled with the square root of the lead time. That is an "
                     "estimate: the real buffer is measured from this item's own past shortfalls "
                     "at its current lead time.")
    stock = params.get("stock_on_hand", stock0)
    if stock is None:
        notes.append("This item has no inventory count. Type the units on hand to see whether it "
                     "needs reordering and what is at stake.")

    key = params.get("scenario", "none")
    scenario = {"key": key, "label": SCENARIOS[key], "lift": None, "measured_days": None,
                "days": None, "days_counted": None, "extra_units": 0.0}
    extra = 0.0
    if key in ("enrollment", "term_start"):
        lift = (lifts.get(key) or {}).get("lift")
        days = params.get("scenario_days", enrollment_days if key == "enrollment" else TERM_START_DAYS)
        counted = min(days, lt)
        scenario.update(lift=lift, measured_days=(lifts.get(key) or {}).get("days"),
                        days=days, days_counted=counted)
        if lift is None:
            notes.append("There are not enough recorded days of this kind to measure it.")
        else:
            extra = d * max(0.0, lift - 1.0) * counted
            notes.append(
                f"On the {scenario['measured_days']} recorded days of this kind, the store sold "
                f"{lift:.2f} times as much as on a regular day. The same lift is applied to this item's "
                f"daily demand for {counted} day{'s' if counted != 1 else ''}"
                + (f", the {lt} days of the lead time" if counted < days else "") + ".")
    elif key == "bulk":
        extra = float(params["bulk_units"])
        notes.append("The order's units are added to the demand before a delivery could arrive.")
    scenario["extra_units"] = extra

    s = params.get("ordering_cost_php", s0)
    h = params.get("holding_cost_php", h0)
    if s != s0 or h != h0:
        notes.append("EOQ uses the costs typed here. The suggested order uses neither cost.")

    what_if = _figures(d, annual, lt, ss, stock, extra, s, h, price, review_days)
    return {
        "ok": True,
        "product_id": item["product_id"],
        "item_name": item["item_name"],
        "supplier_name": item["supplier_name"],
        "fsn_class": item["fsn_class"],
        "service_tier": item.get("service_tier"),
        "avg_daily_demand": d,
        "annual_demand": annual,
        "price_php": price,
        "review_period_days": review_days,
        "scenario": scenario,
        "current": current,
        "what_if": what_if,
        "presets": presets(lifts, enrollment_days),
        "notes": notes,
    }
