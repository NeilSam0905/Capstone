"""
season_ahead.py - what to order ahead of the next busy period.

The adviser (Nov 2026) asked the reorder side to "predict what should be
ordered ahead of a date or seasonality": the reorder point says when stock is
low NOW, at the usual selling rate, and so cannot see a rush coming. This
looks at the store's two real rushes - enrollment (January, June) and the
first two weeks of the first term (August), the kinds of day that sell well
above a regular one (forecasting/day_kinds.py) - and works out, item by item,
what to order and by when so stock is on the shelf when the rush starts.

The windows. Each upcoming run of enrollment or term-start days in the school
calendar (Dim_Date). The published calendar (data/calendar_ranges.csv) ends
before a year is out, so a rush past its last day is ESTIMATED: last year's
run of the same kind moved a year forward, and said to be an estimate. The
store's enrollment has opened on 10-12 January and 6-13 June every year since
2023, and the first term on 1 August.

Per item, a day-by-day projection of its stock from today to the end of the
window, with the Reorder table's own figures (Result_Prescriptive, after the
store's saved settings): its selling rate (ADUS), lead time, safety stock,
reorder point and order-up-to level.

    start             on hand at the last inventory count - the stock the
                      Reorder table judges by
    each day          ADUS sold, or ADUS x lift on a day in the window (lift:
                      units per recorded day of that kind / per recorded
                      regular day over all history - the What-if's lifts)
    regular reorders  as the Reorder table orders: when stock is at or below
                      the reorder point and nothing is on order, order up to
                      the order-up-to level; it arrives a lead time later
    lowest in window  the lowest stock the projection reaches during the rush

So the reorder point is projected on the calendar: `next_reorder` is the day
the item reaches its reorder point (and what it would order) before the rush
ends. The reorder point is set for a regular day's sales, so a rush can still
take the stock under its safety stock; then

    rush order        safety stock - lowest in window: the extra to have on
                      the shelf when the rush starts
    order by          first day of the window - lead time, so it arrives
                      before the rush (today, if that day has passed)

An item with no inventory count cannot be projected; it gets what the window
itself needs (ADUS x lift x days + safety stock) for reference.

Events. Every campus event in the next year is listed with its own track
record, because they behave very differently: Paskuhan sold about 4x a
regular day both years it was tallied, the Feast of St. Thomas Aquinas sold
less than a regular day. Each upcoming event is matched with the same event
in earlier years - within EVENT_MATCH_DAYS of the same date and sharing a word
of its name, as the calendar words it differently from year to year - and
each year's effect is the units per recorded day over the event and the day
before (Paskuhan's busiest day is the eve), against a recorded regular day.

    rush      tallied at least MIN_RUSH_YEARS years, every one >= RUSH_LIFT x
    quieter   every tallied year below 1x
    mixed     anything else tallied
    unknown   never tallied

Only a rush event gets an order plan, over the event and the day before, with
the average of its years as the lift - and not one inside an enrollment or
start-of-term window, which already plans for those days. Read-only.
"""
import math
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)        # forecasting/; appended so backend's own modules stay first
from forecasting import day_kinds  # noqa: E402

# The kinds of day worth ordering ahead for: the two rushes. Exam weeks sell
# about as a regular day (lift ~1.04) and events are too few to size one.
BUSY = ("enrollment", "term_start")
LABELS = {"enrollment": "Enrollment", "term_start": "Start of the first term"}
HORIZON_DAYS = 365
GAP_DAYS = 3        # flagged days this far apart are one window (app.WINDOW_GAP_DAYS)

# Events (module docstring).
EVENT_MATCH_DAYS = 10
RUSH_LIFT = 1.5
MIN_RUSH_YEARS = 2
EVE_DAYS = 1        # days before the event counted with it
# Words too common in event names to say two are the same event.
_NAME_STOP = {"of", "the", "in", "and", "a", "an", "to", "for", "with", "on", "st", "ust", "day", "honor",
              "date", "uncertain", "verify", "begin", "begins", "activities", "virtual", "person"}


def _runs(dates, gap=GAP_DAYS):
    """Sorted ISO dates -> [(start, end)] unbroken windows."""
    out = []
    for d in sorted(set(dates)):
        if out and (date.fromisoformat(d) - date.fromisoformat(out[-1][1])).days <= gap:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _next_year(iso):
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:            # 29 February
        return (d.replace(day=28, year=d.year + 1)).isoformat()


def busy_runs(con):
    """{kind: [(start, end)]} every run of each busy kind in Dim_Date."""
    rows = con.execute("""
        SELECT calendar_date, is_enrollment_period, semester_id, semester_week
        FROM Dim_Date ORDER BY calendar_date""").fetchall()
    dates = {k: [] for k in BUSY}
    for d, enrol, sid, week in rows:
        if enrol:
            dates["enrollment"].append(d)
        elif sid and str(sid).endswith("-T1") and week is not None \
                and 1 <= int(week) <= day_kinds.TERM_START_WEEKS:
            dates["term_start"].append(d)
    return {k: _runs(v) for k, v in dates.items()}


def calendar_end(con):
    """The last day the published school calendar covers (as /api/meta)."""
    return con.execute("SELECT MAX(calendar_date) FROM Dim_Date WHERE semester_id IS NOT NULL").fetchone()[0]


def upcoming_windows(runs, today, published_end, lifts, horizon=HORIZON_DAYS):
    """The busy windows starting from `today` (or under way) to `horizon`
    days ahead, soonest first: the calendar's own, then each kind's runs of
    the last year moved a year forward where they fall past `published_end`.
    [{kind, label, start, end, days, estimated, from_start, lift, past_days}]"""
    t = date.fromisoformat(today)
    last = (t + timedelta(days=horizon)).isoformat()
    out = []
    for kind, rs in runs.items():
        for s, e in rs:
            if e >= today and s <= last:
                out.append((kind, s, e, False, None))
        # Last year's runs, a year on, where the calendar does not reach yet.
        year_ago = (t - timedelta(days=365)).isoformat()
        for s, e in rs:
            if not (year_ago <= s < today):
                continue
            ns, ne = _next_year(s), _next_year(e)
            if ns > published_end and ns <= last and ne >= today:
                out.append((kind, ns, ne, True, s))
    out.sort(key=lambda w: w[1])
    return [{
        "kind": kind, "label": LABELS[kind], "start": s, "end": e,
        "days": (date.fromisoformat(e) - date.fromisoformat(s)).days + 1,
        "estimated": est, "from_start": src,
        "lift": (lifts.get(kind) or {}).get("lift") or 1.0,
        "past_days": int((lifts.get(kind) or {}).get("days") or 0),
    } for kind, s, e, est, src in out]


def project(stock, d, lt, rop, up_to, today, start, end, lift):
    """The day-by-day projection (module docstring) from `today` to `end`:
    (reorders [(order date, qty, arrival date)], lowest stock in the window,
    stock on the window's first day). Stock may go below 0: that is demand
    the shelf could not meet, and what a rush order has to cover."""
    level, arriving, reorders = float(stock), None, []
    low, at_start = math.inf, None
    day = today
    while day <= end:
        if arriving and arriving[0] == day:           # a delivery lands first
            level += arriving[1]
            arriving = None
        in_window = start <= day
        if in_window and at_start is None:
            at_start = level
        level -= d * (lift if in_window else 1.0)
        if in_window:
            low = min(low, level)
        if arriving is None and level <= rop:          # end-of-day review
            qty = max(0, math.ceil(up_to - level))
            if qty:
                arriving = (day + timedelta(days=lt), qty)
                reorders.append((day, qty, arriving[0]))
        day += timedelta(days=1)
    return reorders, low, at_start


def plan_item(item, window, today):
    """One item's plan for one window (see the module docstring), or None
    for an item with no selling rate or reorder point."""
    d = item.get("avg_daily_demand") or 0.0
    rop = item.get("reorder_point")
    if d <= 0 or rop is None:
        return None
    lt = int(item.get("lead_time_days") or 0)
    ss = float(item.get("safety_stock") or 0.0)
    up_to = item.get("order_up_to_level") or rop
    t = date.fromisoformat(today)
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    window_days = (end - max(start, t)).days + 1     # a window under way counts from today
    order_by = start - timedelta(days=lt)

    stock = item.get("current_stock")
    out = {
        "product_id": item["product_id"], "item_name": item["item_name"],
        "supplier_name": item.get("supplier_name"), "fsn_class": item.get("fsn_class"),
        "avg_daily_demand": d, "lead_time_days": lt, "safety_stock": ss,
        "reorder_point": rop, "current_stock": stock, "stock_as_of": item.get("stock_as_of"),
        "window_need": round(d * window["lift"] * window_days + ss, 1),
        "rush_extra": round(d * (window["lift"] - 1) * window_days, 1),
        "order_by": max(order_by, t).isoformat(),
        "arrives_late": t + timedelta(days=lt) > start,
        "next_reorder": None, "lowest_in_window": None, "stock_at_start": None, "rush_order": None,
    }
    if stock is None:
        out["status"] = "uncounted"
        return out
    reorders, low, at_start = project(stock, d, lt, rop, up_to, t, start, end, window["lift"])
    if reorders:
        day, qty, arrives = reorders[0]
        out["next_reorder"] = {"date": day.isoformat(), "qty": qty, "arrives": arrives.isoformat()}
    rush = max(0, math.ceil(ss - low))
    out.update(lowest_in_window=round(low, 1), stock_at_start=round(at_start, 1), rush_order=rush)
    out["status"] = ("rush_now" if rush and order_by <= t
                     else "rush_by" if rush
                     else "reorder" if reorders
                     else "covered")
    return out


STATUS_ORDER = {"rush_now": 0, "rush_by": 1, "reorder": 2, "uncounted": 3, "covered": 4}


# ------------------------------------------------------------------ events

def _words(name):
    """The significant words of an event name, lower case."""
    out = set()
    for w in "".join(ch if ch.isalnum() else " " for ch in (name or "").lower()).split():
        if len(w) > 2 and w not in _NAME_STOP:
            out.add(w[:-1] if w.endswith("s") else w)       # "awards" and "award" match
    return out


def _yearly_gap(a, b):
    """Days between two dates' places in the year (2025-12-19 vs 2026-12-18 -> 1)."""
    da, db = date.fromisoformat(a), date.fromisoformat(b)
    shifted = da.replace(year=db.year) if not (da.month == 2 and da.day == 29) else da.replace(day=28, year=db.year)
    gap = abs((shifted - db).days)
    return min(gap, 365 - gap)


def _effect(days, base, start, end):
    """Units per recorded day over the event and EVE_DAYS before it, as a
    multiple of a recorded regular day; (value or None, recorded days)."""
    lo = date.fromisoformat(start) - timedelta(days=EVE_DAYS)
    part = days.loc[str(lo):end]
    rec = part[part["recorded"]]
    if rec.empty or not base:
        return None, 0
    return float(rec["units"].mean()) / base, int(len(rec))


def classify(values):
    """'rush' / 'quieter' / 'mixed' / 'unknown' from the tallied years' effects."""
    vals = [v for v in values if v is not None]
    if not vals:
        return "unknown"
    if len(vals) >= MIN_RUSH_YEARS and min(vals) >= RUSH_LIFT:
        return "rush"
    if max(vals) < 1:
        return "quieter"
    return "mixed"


def upcoming_events(events, days, today, published_end, horizon=HORIZON_DAYS):
    """Every campus event in the next `horizon` days with its track record.
    `events` are [{name, start, end}] from the published calendar and the
    Event_Log, past and future. One past the published calendar is last
    year's, a year on, and `estimated` (as the windows are).
    [{name, start, end, estimated, tag, lift, past_days, years: [{start, value}]}]"""
    t = date.fromisoformat(today)
    last = (t + timedelta(days=horizon)).isoformat()
    year_ago = (t - timedelta(days=365)).isoformat()
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: e["start"]):
        k = (e["start"], frozenset(_words(e["name"])) or e["name"].lower())
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    coming = [(e, False) for e in uniq if e["end"] >= today and e["start"] <= last]
    coming += [({**e, "start": _next_year(e["start"]), "end": _next_year(e["end"])}, True)
               for e in uniq if year_ago <= e["start"] < today
               and published_end < _next_year(e["start"]) <= last]

    rec = days[days["recorded"]]
    regular = rec.loc[rec["kind"] == "regular", "units"]
    base = float(regular.mean()) if len(regular) else None
    out = []
    for e, estimated in sorted(coming, key=lambda c: c[0]["start"]):
        words = _words(e["name"])
        years = {}
        for p in uniq:
            if p["start"] >= today or p["start"] >= e["start"] or not (words & _words(p["name"])):
                continue
            if _yearly_gap(p["start"], e["start"]) > EVENT_MATCH_DAYS:
                continue
            yr = p["start"][:4]
            if yr not in years:                    # the first matching entry of that year
                value, n = _effect(days, base, p["start"], p["end"])
                years[yr] = {"start": p["start"], "name": p["name"], "value": value, "recorded_days": n}
        hist = [years[y] for y in sorted(years)]
        vals = [h["value"] for h in hist if h["value"] is not None]
        out.append({
            "name": e["name"], "start": e["start"], "end": e["end"], "estimated": estimated,
            "tag": classify(vals), "years": hist,
            "lift": sum(vals) / len(vals) if vals else None,
            "past_days": sum(h["recorded_days"] for h in hist),
        })
    return out


def event_windows(events, busy_windows):
    """The rush events as order-ahead windows over the event and its eve,
    leaving out one inside an enrollment or start-of-term window (that window
    plans for its days); each such event says which window it is part of."""
    out = []
    for ev in events:
        if ev["tag"] != "rush":
            continue
        start = (date.fromisoformat(ev["start"]) - timedelta(days=EVE_DAYS)).isoformat()
        host = next((w for w in busy_windows if w["start"] <= ev["start"] <= w["end"]), None)
        if host:
            ev["part_of"] = host["label"]
            continue
        out.append({
            "kind": "event", "label": ev["name"], "start": start, "end": ev["end"],
            "days": (date.fromisoformat(ev["end"]) - date.fromisoformat(start)).days + 1,
            "estimated": ev["estimated"], "from_start": None,
            "lift": ev["lift"], "past_days": ev["past_days"],
        })
    return out


def season_plan(con, items, today, published_end=None, lifts=None, events=None):
    """{windows: [... with items, n_rush, rush_units, ...], events: [...]}
    for every upcoming busy window and campus event. `items` are the Reorder
    table's rows (app._reorder_items); only priced ones with a selling rate
    are planned. `events` are the calendar's and the Event_Log's
    [{name, start, end}]."""
    days = day_kinds.load_days(con)
    lifts = lifts or day_kinds.lifts(con, days)
    published_end = published_end or calendar_end(con)
    windows = upcoming_windows(busy_runs(con), today, published_end, lifts)
    coming_events = upcoming_events(events or [], days, today, published_end)
    windows = sorted(windows + event_windows(coming_events, windows), key=lambda w: w["start"])
    priced = [i for i in items if i.get("policy_state") == "priced"]
    for w in windows:
        plans = [p for p in (plan_item(i, w, today) for i in priced) if p]

        def key(p):
            when = (p["order_by"] if p["status"].startswith("rush")
                    else (p["next_reorder"] or {}).get("date", "9999"))
            return STATUS_ORDER[p["status"]], when, -(p["rush_order"] or 0), p["item_name"]
        plans.sort(key=key)
        rush = [p for p in plans if p["status"] in ("rush_now", "rush_by")]
        w.update(
            items=plans,
            n_rush=len(rush),
            n_rush_now=sum(1 for p in rush if p["status"] == "rush_now"),
            rush_units=sum(p["rush_order"] for p in rush),
            earliest_order_by=min((p["order_by"] for p in rush), default=None),
            n_reorder=sum(1 for p in plans if p["next_reorder"]),
            n_uncounted=sum(1 for p in plans if p["status"] == "uncounted"),
            n_counted=sum(1 for p in plans if p["status"] != "uncounted"),
        )
    return {"today": today, "calendar_end": published_end, "windows": windows, "events": coming_events,
            "rules": {"rush_lift": RUSH_LIFT, "min_rush_years": MIN_RUSH_YEARS, "eve_days": EVE_DAYS}}
