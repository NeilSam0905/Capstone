"""
advisory_outlook.py - the predictive part of a calendar advisory.

An advisory used to say only WHAT is coming ("Exam Week Upcoming") and list
the Fast items (the adviser, Nov 2026: "where is the predictive part?"). This
adds, for each enrollment, exam or event advisory:

  expected      units the store should sell per selling day in the window:
                a regular day's level NOW x the lift measured for that kind
                of day (forecasting/day_kinds.py, the same lifts the Reorder
                what-if uses)

                    level     average of the recorded regular days in the
                              last BASELINE_DAYS days of sales (every recorded
                              regular day when there are fewer than
                              MIN_BASELINE_DAYS of them)
                    lift      units per recorded day of that kind / per
                              recorded regular day, over all history

                Per SELLING day, not per calendar day: a day with no sales on
                the tally sheet is a closure or a day nothing was written down
                (day_kinds), so a total over every calendar day would guess at
                which days the store opens.

  last_year     the same window a year earlier - the past run of the same
                calendar flag (or the same logged event) that started nearest
                to one year before - as daily sales with CONTEXT_DAYS either
                side, for the advisory's chart, and its units per selling day.

Read-only, from Dim_Date and Fact_Sales; nothing here is stored.
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)        # forecasting/; appended so backend's own modules stay first
from forecasting import day_kinds  # noqa: E402

BASELINE_DAYS = 90
MIN_BASELINE_DAYS = 10
CONTEXT_DAYS = 7
# How far from exactly one year back last year's window may start. School
# calendars move a week or two between years; a run further off than this is
# a different period, not last year's version of this one.
LAST_YEAR_SEARCH_DAYS = 60


def baseline(days):
    """A regular day's sales now: {"per_day", "days", "from", "to"}, or None
    when no regular day was ever recorded."""
    rec = days[days["recorded"] & (days["kind"] == "regular")]
    if rec.empty:
        return None
    last = days.index[days["recorded"]].max()
    recent = rec[rec.index > last - timedelta(days=BASELINE_DAYS)]
    use = recent if len(recent) >= MIN_BASELINE_DAYS else rec
    return {"per_day": float(use["units"].mean()), "days": int(len(use)),
            "from": use.index.min().date().isoformat(), "to": use.index.max().date().isoformat()}


def expected(days, lifts, start, end, kind=None):
    """What the store should sell per selling day from `start` to `end`.

    Each date takes its own kind from Dim_Date (or `kind` for every date, for
    a logged event the calendar does not flag), so a window that mixes kinds
    averages their lifts. A kind with no measured lift counts as a regular day.
    None when there is no regular-day level to scale."""
    base = baseline(days)
    if base is None:
        return None
    dates = [date.fromisoformat(start) + timedelta(days=i)
             for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
    kinds = [kind or _kind_on(days, d) for d in dates]
    lift_of = {k: (lifts.get(k) or {}).get("lift") or 1.0 for k in set(kinds)}
    lift = sum(lift_of[k] for k in kinds) / len(kinds)
    main = max(set(kinds), key=kinds.count)
    return {
        "kind": main,
        "kind_label": day_kinds.LABELS.get(main, main),
        "lift": lift,
        "past_days": int((lifts.get(main) or {}).get("days") or 0),
        "baseline_per_day": base["per_day"],
        "baseline_days": base["days"],
        "baseline_from": base["from"],
        "baseline_to": base["to"],
        "per_day": base["per_day"] * lift,
    }


def _kind_on(days, d):
    """A date's kind; a date past Dim_Date is a regular day."""
    try:
        return days.at[pd.Timestamp(d), "kind"]
    except KeyError:
        return "regular"


def pick_last_year(runs, start, last_sale):
    """The run in `runs` ([{"start", "end"}], ISO) that starts nearest to one
    year before `start`, ended by `last_sale` and within LAST_YEAR_SEARCH_DAYS
    of that date; else the same dates a year back, moved to the same weekday
    (364 days), when those are over too. None when neither has happened."""
    target = date.fromisoformat(start) - timedelta(days=365)
    done = [r for r in runs if r["end"] <= last_sale
            and abs((date.fromisoformat(r["start"]) - target).days) <= LAST_YEAR_SEARCH_DAYS]
    if done:
        return min(done, key=lambda r: abs((date.fromisoformat(r["start"]) - target).days))
    return None


def same_dates_last_year(start, end, last_sale):
    """The window 364 days back (same weekdays), if it is over."""
    s = (date.fromisoformat(start) - timedelta(days=364)).isoformat()
    e = (date.fromisoformat(end) - timedelta(days=364)).isoformat()
    return {"start": s, "end": e} if e <= last_sale else None


def last_year(days, run):
    """Daily sales over last year's window `run` and CONTEXT_DAYS either side,
    with the window's units per selling day. No average of the days around it:
    one bulk order (2,623 units on 7 Dec 2025, the week before that December's
    exams) swamps it, and the chart shows those days anyway."""
    s, e = date.fromisoformat(run["start"]), date.fromisoformat(run["end"])
    lo, hi = s - timedelta(days=CONTEXT_DAYS), e + timedelta(days=CONTEXT_DAYS)
    part = days.loc[str(lo):str(hi)]
    series = []
    for ts, row in part.iterrows():
        d = ts.date()
        series.append({
            "date": d.isoformat(),
            "units": float(row["units"]) if row["recorded"] else None,
            "closed": bool(row["closed"]),
            "in_window": s <= d <= e,
            "kind": row["kind"],
        })
    inside = [p for p in series if p["in_window"] and p["units"] is not None]
    return {
        "start": run["start"], "end": run["end"],
        "window_days": (e - s).days + 1,
        "recorded_days": len(inside),
        "per_day": sum(p["units"] for p in inside) / len(inside) if inside else None,
        "series": series,
    }


def outlook(days, lifts, start, end, past_runs, kind=None):
    """The advisory's `outlook`: `expected` plus `last_year` (None when there
    is no past window to show). `past_runs` are the earlier runs of the same
    calendar flag or logged event."""
    exp = expected(days, lifts, start, end, kind)
    if exp is None:
        return None
    last_sale = days.index[days["recorded"]].max().date().isoformat()
    run = pick_last_year(past_runs, start, last_sale) or same_dates_last_year(start, end, last_sale)
    return {**exp, "last_year": last_year(days, run) if run else None}
