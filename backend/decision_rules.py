"""
The FSN decision rules: what the store should do about each class
(manuscript Section 3.3.1).

  - Fast-moving      -> keep in stock: forecast, reorder point, EOQ.
  - Slow-moving      -> promote: bundled pricing, placement adjustment,
                        targeted marketing during high-traffic events.
  - Slow-moving that stays below a configurable velocity threshold for two
    consecutive semesters -> "Review for Discontinuation".
  - Non-moving       -> clear out: excluded from replenishment; evaluate
                        returning it to the supplier, marking it down for
                        clearance, or discontinuing it.

An item the store has already marked discontinued (Product_Status) reads as
"discontinued" whatever its class: the decision these rules ask for has been
made.

Velocity is the FSN model's own metric, ADUS, measured per semester exactly
as step3_fsn_classification.py measures it over the whole history: weighted
units (imputed rows at 0.5) over active selling days (days on the sheet,
less censored stockout days).

"Semester" means a regular term, T1 or T2. The special term (ST) is skipped:
it is a short, quiet summer window, and counting it would make a summer lull
look like a second slow semester. The two semesters judged are the last two
regular terms that ended on or before the last day with recorded sales, so
a term still in progress is never judged on its first weeks.
"""
import math
import re

from catalog import load_discontinued
from db import SALE_ONLY, rows

# Units per selling day. Below this in both semesters, a Slow item is
# escalated to "Review for Discontinuation". 0.1 is about three units a
# month. The manuscript leaves the value configurable, so /api/fsn/decision-rules
# takes ?threshold= and the FSN Classification page offers a choice.
DEFAULT_REVIEW_THRESHOLD = 0.1
REVIEW_SEMESTERS = 2
MAX_THRESHOLD = 1000.0

KEEP_STOCKED = "keep_stocked"
PROMOTE = "promote"
REVIEW = "review_discontinuation"
CLEAR_OUT = "clear_out"
DISCONTINUED = "discontinued"

_REGULAR_TERM = re.compile(r"-T[12]$")


def parse_threshold(raw):
    """A request's ?threshold=, or the default when missing or not a usable
    non-negative number."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_THRESHOLD
    if not math.isfinite(value) or value < 0 or value > MAX_THRESHOLD:
        return DEFAULT_REVIEW_THRESHOLD
    return value


def review_semesters(con, n=REVIEW_SEMESTERS):
    """The last `n` complete regular semesters, oldest first:
    [{semester_id, start, end}]. Fewer than `n` when the data is shorter."""
    last = rows(con, f"""
        SELECT MAX(d.calendar_date) AS last_day
          FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
         WHERE {SALE_ONLY}
    """)[0]["last_day"]
    if not last:
        return []
    terms = rows(con, """
        SELECT semester_id, MIN(calendar_date) AS start, MAX(calendar_date) AS end
          FROM Dim_Date
         WHERE semester_id IS NOT NULL
         GROUP BY semester_id
         ORDER BY start
    """)
    done = [t for t in terms if _REGULAR_TERM.search(t["semester_id"]) and t["end"] <= last]
    return done[-n:] if n else []


def semester_velocity(con, semesters):
    """product_id -> {semester_id: {units, active_days, adus}} for every SKU
    with a row in that semester. A SKU absent from a semester is absent here."""
    out = {}
    for s in semesters:
        for r in rows(con, f"""
            SELECT f.product_id,
                   SUM(f.quantity_sold) AS units,
                   COUNT(DISTINCT f.date_id) AS observed_days,
                   COUNT(DISTINCT CASE WHEN f.is_censored = 1 THEN f.date_id END) AS censored_days,
                   SUM(CASE WHEN f.imputation_flag = 1 THEN f.quantity_sold * 0.5
                            ELSE f.quantity_sold END) AS weighted_units
              FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
             WHERE {SALE_ONLY} AND d.calendar_date BETWEEN ? AND ?
             GROUP BY f.product_id
        """, (s["start"], s["end"])):
            active = r["observed_days"] - r["censored_days"]
            out.setdefault(r["product_id"], {})[s["semester_id"]] = {
                "units": r["units"] or 0,
                "active_days": active,
                "adus": round((r["weighted_units"] or 0) / active, 4) if active > 0 else 0.0,
            }
    return out


def first_seen(con):
    """product_id -> the first date the SKU appears on a sheet at all."""
    return {r["product_id"]: r["first_day"] for r in rows(con, f"""
        SELECT f.product_id, MIN(d.calendar_date) AS first_day
          FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
         WHERE {SALE_ONLY}
         GROUP BY f.product_id
    """)}


def semester_reading(semester, velocity, first_day):
    """One semester for one SKU, as the rule reads it.

    On the sheet: its measured ADUS. Not on the sheet at all, although the
    item was already on an earlier sheet: velocity 0 - the store carried it
    and it did not move (or stopped being listed). Not on the sheet and not
    yet seen anywhere: not measurable, so it cannot count towards the rule."""
    v = velocity.get(semester["semester_id"])
    if v and v["active_days"] > 0:
        return {"semester_id": semester["semester_id"], "adus": v["adus"],
                "units": v["units"], "active_days": v["active_days"], "on_sheet": True,
                "measurable": True}
    existed = first_day is not None and first_day < semester["start"]
    return {"semester_id": semester["semester_id"], "adus": 0.0,
            "units": v["units"] if v else 0, "active_days": 0, "on_sheet": False,
            "measurable": existed}


def action_for(fsn_class, readings, threshold, discontinued=False, required=REVIEW_SEMESTERS):
    """The decision rule for one SKU. `readings` are its semester_reading()s."""
    if discontinued:
        return DISCONTINUED
    if fsn_class == "F":
        return KEEP_STOCKED
    if fsn_class == "N":
        return CLEAR_OUT
    if fsn_class != "S":
        return None
    slow_throughout = (
        len(readings) >= required
        and all(r["measurable"] and r["adus"] < threshold for r in readings)
    )
    return REVIEW if slow_throughout else PROMOTE


def evaluate(con, threshold=DEFAULT_REVIEW_THRESHOLD):
    """Every SKU's decision rule, plus what the rule was judged on."""
    semesters = review_semesters(con)
    velocity = semester_velocity(con, semesters)
    seen = first_seen(con)
    discontinued = load_discontinued(con)

    items = []
    summary = {KEEP_STOCKED: 0, PROMOTE: 0, REVIEW: 0, CLEAR_OUT: 0, DISCONTINUED: 0}
    for p in rows(con, "SELECT product_id, item_name, fsn_class FROM Dim_Product"):
        pid = p["product_id"]
        readings = [semester_reading(s, velocity.get(pid, {}), seen.get(pid)) for s in semesters]
        action = action_for(p["fsn_class"], readings, threshold,
                            discontinued=p["item_name"] in discontinued)
        if action:
            summary[action] += 1
        items.append({
            "product_id": pid,
            "fsn_class": p["fsn_class"],
            "action": action,
            "semesters": readings,
        })

    return {
        "threshold": threshold,
        "default_threshold": DEFAULT_REVIEW_THRESHOLD,
        "threshold_unit": "units per selling day",
        "semesters": semesters,
        "semesters_required": REVIEW_SEMESTERS,
        "items": items,
        "summary": summary,
    }
