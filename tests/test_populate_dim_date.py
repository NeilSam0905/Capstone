"""
tests/test_populate_dim_date.py
------------------------------------------------------------------
Remediation D3: populate_dim_date.py rebuilds Dim_Date from scratch on
every run (DELETE + re-INSERT), which previously erased every staff-set
closure - and, though never previously exercised (Event_Log has always
been empty in the real data), would have erased every logged event too,
despite create_schema.py's own comment claiming otherwise.

load_closure_log() and load_event_dates() are the read-back fix, tested
here directly against a minimal in-memory fixture rather than the full
CSV + Dim_Product/Dim_Date setup main() requires.
------------------------------------------------------------------
"""
import sqlite3
from datetime import date

import populate_dim_date as pdd  # conftest.py puts scripts/ on sys.path


def _log_db():
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE Closure_Log (
            closure_id INTEGER PRIMARY KEY, closure_date TEXT NOT NULL,
            is_closed INTEGER NOT NULL, reason TEXT, created_by TEXT, date_logged TEXT
        );
    """)
    con.execute("""
        CREATE TABLE Event_Log (
            event_id INTEGER PRIMARY KEY, event_date TEXT NOT NULL,
            event_name TEXT NOT NULL, event_description TEXT,
            created_by TEXT, date_logged TEXT
        );
    """)
    return con


def test_closure_log_latest_entry_per_date_wins():
    con = _log_db()
    # closed, then later reopened - the reopen must win
    con.execute("INSERT INTO Closure_Log (closure_date, is_closed, date_logged) "
                "VALUES ('2026-08-15', 1, '2026-08-01T09:00:00')")
    con.execute("INSERT INTO Closure_Log (closure_date, is_closed, date_logged) "
                "VALUES ('2026-08-15', 0, '2026-08-10T09:00:00')")
    # a second date, closed and never reopened
    con.execute("INSERT INTO Closure_Log (closure_date, is_closed, date_logged) "
                "VALUES ('2026-09-01', 1, '2026-08-20T09:00:00')")
    con.commit()

    result = pdd.load_closure_log(con)

    assert result == {
        date(2026, 8, 15): 0,
        date(2026, 9, 1): 1,
    }


def test_closure_log_empty_table_gives_no_closures():
    """Population check before the 'latest wins' claim above means
    anything - an empty table trivially satisfies most assertions."""
    con = _log_db()
    assert pdd.load_closure_log(con) == {}


def test_event_dates_are_read_back():
    con = _log_db()
    con.execute("INSERT INTO Event_Log (event_date, event_name, date_logged) "
                "VALUES ('2026-07-04', 'Foundation Day', '2026-07-01T09:00:00')")
    con.execute("INSERT INTO Event_Log (event_date, event_name, date_logged) "
                "VALUES ('2026-08-20', 'Sports Fest', '2026-08-15T09:00:00')")
    con.commit()

    result = pdd.load_event_dates(con)

    assert result == {date(2026, 7, 4), date(2026, 8, 20)}


def test_missing_closure_log_table_raises_not_silently_skips():
    """A database built before this migration must fail loudly (run
    create_schema.py first), not silently report zero closures - that
    would look identical to 'no closures logged yet'."""
    con = sqlite3.connect(":memory:")  # no tables at all
    try:
        pdd.load_closure_log(con)
        assert False, "expected RuntimeError for a missing Closure_Log table"
    except RuntimeError:
        pass
