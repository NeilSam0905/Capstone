"""
tests/test_calendar_extent.py
------------------------------------------------------------------
Dim_Date extends itself (scripts/calendar_extent.py), so a date staff
enter after the published school calendar is not refused:

  - it runs to the end of next year, and an appended day gets the same
    date_id populate_dim_date.py gives it (days since 2023-01-01, plus one);
  - appended days carry no academic flag and no semester, but do carry
    the staff's own closures (latest entry wins) and events;
  - the pipeline rebuild never ends before a day already in Dim_Date, and
    the last term stops where calendar_ranges.csv stops.
------------------------------------------------------------------
"""
import sqlite3
from datetime import date

import calendar_extent as ce        # conftest.py puts scripts/ on sys.path
import populate_dim_date as pdd


def dim_date(last_day, closures=(), events=()):
    """A Dim_Date running from 2023-01-01 to `last_day`, plus the two logs."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT UNIQUE,
            semester_id TEXT, semester_week INTEGER, is_enrollment_period INTEGER,
            is_exam_week INTEGER, is_event_day INTEGER, is_sem_break INTEGER,
            is_tally_date INTEGER, is_tally_date_positive INTEGER, is_store_closed INTEGER);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY AUTOINCREMENT, closure_date TEXT,
            is_closed INTEGER, reason TEXT, created_by TEXT, date_logged TEXT);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT,
            event_name TEXT, event_description TEXT, created_by TEXT, date_logged TEXT);
    """)
    for d in pdd.daterange(ce.START_DATE, last_day):
        c.execute("INSERT INTO Dim_Date VALUES (?, ?, 'T', 1, 0, 0, 0, 0, 0, 0, 0)",
                  (ce.date_id_for(d), d.isoformat()))
    for day, closed in closures:
        c.execute("INSERT INTO Closure_Log (closure_date, is_closed) VALUES (?, ?)", (day, closed))
    for day in events:
        c.execute("INSERT INTO Event_Log (event_date, event_name) VALUES (?, 'x')", (day,))
    return c


def row(c, day):
    return c.execute("SELECT date_id, semester_id, is_enrollment_period, is_event_day, "
                     "is_store_closed FROM Dim_Date WHERE calendar_date = ?", (day,)).fetchone()


def test_horizon_is_the_end_of_next_year():
    assert ce.horizon_end(date(2026, 10, 14)) == date(2027, 12, 31)
    assert ce.horizon_end(date(2027, 1, 1)) == date(2028, 12, 31)


def test_date_id_is_populate_dim_dates_sequential_id():
    assert ce.date_id_for(date(2023, 1, 1)) == 1
    assert ce.date_id_for(date(2026, 12, 31)) == 1461      # the last row today


def test_appends_the_missing_days_with_matching_ids():
    c = dim_date(date(2026, 12, 31))
    added = ce.extend_dim_date(c, through=date(2027, 1, 31))
    assert added == 31
    assert row(c, "2027-01-01") == (1462, None, 0, 0, 0)    # no semester, no academic flag
    assert row(c, "2026-12-31")[1] == "T"                   # existing rows untouched
    assert ce.extend_dim_date(c, through=date(2027, 1, 31)) == 0


def test_appended_days_carry_the_staffs_closures_and_events():
    c = dim_date(date(2026, 12, 31),
                 closures=[("2027-01-02", 1), ("2027-01-03", 1), ("2027-01-03", 0)],
                 events=["2027-01-05"])
    ce.extend_dim_date(c, through=date(2027, 1, 10))
    assert row(c, "2027-01-02")[4] == 1
    assert row(c, "2027-01-03")[4] == 0                     # reopened later: latest entry wins
    assert row(c, "2027-01-05")[3] == 1


def test_an_empty_calendar_is_left_to_the_pipeline():
    c = dim_date(date(2022, 12, 31))                        # no rows at all
    assert ce.extend_dim_date(c, through=date(2027, 1, 31)) == 0


def test_rebuild_never_ends_before_a_day_already_in_dim_date():
    c = dim_date(date(2029, 3, 1))
    assert pdd.calendar_end(c, today=date(2026, 10, 14)) == date(2029, 3, 1)
    short = dim_date(date(2026, 12, 31))
    assert pdd.calendar_end(short, today=date(2026, 10, 14)) == date(2027, 12, 31)


def test_last_term_stops_where_the_school_calendar_stops(tmp_path):
    csv = tmp_path / "ranges.csv"
    csv.write_text("start_date,end_date,flag,semester_id,event_name\n"
                   "2026-06-06,2026-06-20,is_enrollment_period,AY2526-ST,a\n"
                   "2026-08-01,2026-08-10,is_enrollment_period,AY2627-T1,b\n"
                   "2026-12-21,2026-12-31,is_sem_break,AY2627-T1,c\n")
    end = pdd.academic_end(str(csv))
    assert end == date(2026, 12, 31)
    windows = pdd.derive_term_windows(str(csv), end)
    assert windows[-1] == ("AY2627-T1", date(2026, 8, 1), date(2026, 12, 31))


def test_log_readers_are_still_reachable_from_populate_dim_date():
    assert pdd.load_closure_log is ce.load_closure_log
    assert pdd.load_event_dates is ce.load_event_dates
