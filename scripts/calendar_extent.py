"""
calendar_extent.py
------------------------------------------------------------------
How far Dim_Date runs, and how to add the days it is missing.

Dim_Date used to stop at 2026-12-31, the last day in calendar_ranges.csv,
and every tally, closure or event dated after it was refused. It now runs
to the end of NEXT year, counted from today, and moves forward on its own:
populate_dim_date.py builds it that far on every pipeline run, and the
backend appends the missing days on start-up and before it saves anything
dated (extend_dim_date below). So a date a staff member enters is in the
calendar whether or not anyone has edited a file.

What the extra days do NOT have is the school calendar. Enrollment, exam
weeks, semestral breaks and semester_id come only from calendar_ranges.csv
(a protected file - see CLAUDE.md), so a day past its last range carries no
academic flag. Store closures and events are the staff's own entries
(Closure_Log, Event_Log) and are applied to every day, extended or not.
Every forecast already reads a day without flags as an ordinary day
(forecasting/calendar_adjust.py, forecasting/shape.py), so the extension
changes no forecast.

date_id stays what populate_dim_date.py has always made it - sequential
from START_DATE, i.e. days since 2023-01-01 plus one - so an appended day
gets the same id the next full rebuild gives it, and Fact_Sales rows that
point at it survive that rebuild.
------------------------------------------------------------------
"""
from datetime import date, timedelta

START_DATE = date(2023, 1, 1)


def horizon_end(today=None):
    """The last day Dim_Date should hold: 31 December of next year."""
    today = today or date.today()
    return date(today.year + 1, 12, 31)


def date_id_for(d):
    """Dim_Date.date_id for a day - populate_dim_date.py's sequential id."""
    return (d - START_DATE).days + 1


def load_closure_log(con):
    """Remediation D3. Latest Closure_Log row per closure_date wins (an
    append-only log of toggle actions, not a snapshot), so a later
    reopen correctly overrides an earlier closure. Returns {date: 0/1}.
    Table may not exist yet on a database built before this migration -
    that is a real error (create_schema.py first), not something to
    silently skip."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Closure_Log'").fetchone():
        raise RuntimeError("Closure_Log does not exist - run create_schema.py first "
                           "(remediation D3 added this table).")
    rows = con.execute("""
        SELECT cl.closure_date, cl.is_closed
        FROM Closure_Log cl
        WHERE cl.closure_id = (
            SELECT MAX(cl2.closure_id) FROM Closure_Log cl2
            WHERE cl2.closure_date = cl.closure_date
        )
    """).fetchall()
    return {date.fromisoformat(d): int(closed) for d, closed in rows}


def load_event_dates(con):
    """Remediation D3 (extended). Event_Log rows are a permanent flag,
    same semantics backend/app.py's add_event() already applies at write
    time (is_event_day never gets un-set) - this just makes that
    survive a Dim_Date rebuild, which nothing previously did despite
    create_schema.py's own comment claiming it."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Event_Log'").fetchone():
        raise RuntimeError("Event_Log does not exist - run create_schema.py first.")
    rows = con.execute("SELECT DISTINCT event_date FROM Event_Log").fetchall()
    return {date.fromisoformat(d) for (d,) in rows}


def _has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def extend_dim_date(con, through=None):
    """Append the days Dim_Date is missing after its last one, up to
    `through` (default horizon_end()). Returns how many were added.

    An appended day has no academic flag and no semester (see the module
    docstring); its closure and event flags come from Closure_Log and
    Event_Log, so a closure logged for a day before that day existed still
    lands on it. An empty Dim_Date is left alone: building it from nothing
    is populate_dim_date.py's job. The caller commits."""
    through = through or horizon_end()
    last = con.execute("SELECT MAX(calendar_date) FROM Dim_Date").fetchone()[0]
    if last is None:
        return 0
    day = date.fromisoformat(last) + timedelta(days=1)
    if day > through:
        return 0

    closures = load_closure_log(con) if _has_table(con, "Closure_Log") else {}
    events = load_event_dates(con) if _has_table(con, "Event_Log") else set()
    rows = []
    while day <= through:
        rows.append((date_id_for(day), day.isoformat(),
                     1 if day in events else 0, closures.get(day, 0)))
        day += timedelta(days=1)
    con.executemany("""
        INSERT INTO Dim_Date (
            date_id, calendar_date, semester_id, semester_week,
            is_enrollment_period, is_exam_week, is_event_day,
            is_sem_break, is_tally_date, is_tally_date_positive, is_store_closed
        ) VALUES (?, ?, NULL, NULL, 0, 0, ?, 0, 0, 0, ?)
    """, rows)
    return len(rows)
