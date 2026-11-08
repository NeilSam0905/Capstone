"""
forecasting/day_kinds.py
------------------------------------------------------------------
Which kind of school day each date is, and how much the store sells on each
kind compared with a regular day.

Shared by scripts/daily_accuracy_check.py (the day-by-day forecast error,
split by kind of day) and backend/whatif.py (the Reorder Alerts what-if
presets), so the two use the same groups and the same measured lifts.

Kinds, in order of precedence (a day takes the first that applies), all read
from Dim_Date:

  enrollment   is_enrollment_period: the January and June enrollment days
  term_start   the first two weeks of the first term (semester_id ending in
               -T1, semester_week 1 or 2): August, when the school year opens
  exam         is_exam_week
  sem_break    is_sem_break
  event        is_event_day: Paskuhan in December and the other campus events
               in the calendar
  regular      every other day

A RECORDED day is one the store was open (is_store_closed = 0) and sold
something: 465 of the days to 18 October 2026. The store trades every day,
so a day on which no item at all sold is either a closure marked on the tally
sheet's colour legend or a day nothing was written down - not a day of zero
demand. It is left out rather than averaged in as a zero; averaging those
days in dilutes every kind of day and hides most of the differences between
them.

Sales are every Fact_Sales row of transaction_type 'sale', bulk and
organization orders included: what the store actually had to supply on such
a day, which is what a what-if about a coming busy period needs.
------------------------------------------------------------------
"""
import pandas as pd

__all__ = ["KINDS", "LABELS", "TERM_START_WEEKS", "day_kind", "load_days", "lifts",
           "enrollment_days_typical"]

KINDS = ("enrollment", "term_start", "exam", "sem_break", "event", "regular")
LABELS = {
    "enrollment": "Enrollment days (January, June)",
    "term_start": "First two weeks of the first term (August)",
    "exam": "Exam weeks",
    "sem_break": "Semestral breaks",
    "event": "Event days (Paskuhan and other campus events)",
    "regular": "Regular days",
}
TERM_START_WEEKS = 2

_CALENDAR_COLS = ("is_enrollment_period", "semester_id", "semester_week", "is_exam_week",
                  "is_sem_break", "is_event_day", "is_store_closed")


def day_kind(row):
    """The kind of one Dim_Date row (a mapping with the _CALENDAR_COLS)."""
    if row["is_enrollment_period"]:
        return "enrollment"
    sid, week = row["semester_id"], row["semester_week"]
    if sid and str(sid).endswith("-T1") and week is not None and pd.notna(week) \
            and 1 <= int(week) <= TERM_START_WEEKS:
        return "term_start"
    if row["is_exam_week"]:
        return "exam"
    if row["is_sem_break"]:
        return "sem_break"
    if row["is_event_day"]:
        return "event"
    return "regular"


def load_days(con):
    """Every Dim_Date day as a DataFrame indexed by date: its `kind`, whether
    the store was closed, the store's units sold that day (0 where nothing was
    recorded) and `recorded` (open and sold something)."""
    cal = pd.read_sql_query(
        "SELECT date_id, calendar_date, %s FROM Dim_Date" % ", ".join(_CALENDAR_COLS),
        con, parse_dates=["calendar_date"])
    units = pd.read_sql_query("""
        SELECT date_id, SUM(quantity_sold) AS units
        FROM Fact_Sales
        WHERE LOWER(COALESCE(transaction_type, 'sale')) = 'sale'
        GROUP BY date_id""", con)
    df = cal.merge(units, on="date_id", how="left")
    df["units"] = df["units"].fillna(0.0).astype(float)
    df["kind"] = [day_kind(r) for r in df[list(_CALENDAR_COLS)].to_dict("records")]
    df["closed"] = df["is_store_closed"].fillna(0).astype(int) == 1
    df["recorded"] = (~df["closed"]) & (df["units"] > 0)
    return df.set_index("calendar_date")[["kind", "closed", "units", "recorded"]].sort_index()


def lifts(con, days=None):
    """{kind: {"days", "units_per_day", "lift"}} over every recorded day: the
    store's average units per recorded day of that kind, and that average as a
    multiple of a regular day's. `lift` is None when there are no regular days
    to compare with, or no recorded day of the kind."""
    days = load_days(con) if days is None else days
    rec = days[days["recorded"]]
    regular = rec.loc[rec["kind"] == "regular", "units"]
    base = float(regular.mean()) if len(regular) else None
    out = {}
    for kind in KINDS:
        u = rec.loc[rec["kind"] == kind, "units"]
        per_day = float(u.mean()) if len(u) else None
        out[kind] = {
            "days": int(len(u)),
            "units_per_day": per_day,
            "lift": (per_day / base) if (per_day is not None and base) else None,
        }
    return out


def enrollment_days_typical(con):
    """How many days a run of enrollment days usually lasts in Dim_Date (the
    median run length, at least 1): the default length of the what-if's
    enrollment preset."""
    flags = pd.read_sql_query(
        "SELECT calendar_date, is_enrollment_period FROM Dim_Date ORDER BY calendar_date",
        con, parse_dates=["calendar_date"])
    runs, n, last = [], 0, None
    for d, flagged in zip(flags["calendar_date"], flags["is_enrollment_period"]):
        if not flagged:
            if n:
                runs.append(n)
            n = 0
            continue
        if n and (d - last).days == 1:
            n += 1
        else:
            if n:
                runs.append(n)
            n = 1
        last = d
    if n:
        runs.append(n)
    if not runs:
        return 1
    runs.sort()
    return max(1, runs[len(runs) // 2])
