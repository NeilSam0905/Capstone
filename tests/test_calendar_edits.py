"""
tests/test_calendar_edits.py
------------------------------------------------------------------
Editing closures and events, upcoming and previous (Tally Interface →
Edit dates), backend/app.py edit_closure, edit_event and delete_event, on a
throwaway database:

  - moving a closure reopens the old day and closes the new one, in both
    Dim_Date and Closure_Log (so a pipeline rebuild replays it), and the
    closed-dates list shows the newest reason;
  - moving an event moves its flag; the old day keeps a flag only while
    another logged event or the published calendar still has one there;
  - past dates can be corrected too, but never onto a day the calendar does
    not hold (the flag would be silently lost);
  - GET /api/events names the date `calendar_date`, as the interface reads it.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402
import calendar_extent  # noqa: E402
import db as dbmod  # noqa: E402

TODAY = date.today()


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "ustore.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT UNIQUE,
            semester_id TEXT, semester_week INTEGER, is_enrollment_period INTEGER DEFAULT 0,
            is_exam_week INTEGER DEFAULT 0, is_event_day INTEGER DEFAULT 0, is_sem_break INTEGER DEFAULT 0,
            is_tally_date INTEGER DEFAULT 0, is_tally_date_positive INTEGER DEFAULT 0,
            is_store_closed INTEGER DEFAULT 0);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY AUTOINCREMENT, closure_date TEXT,
            is_closed INTEGER, reason TEXT, created_by TEXT, date_logged TEXT);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT,
            event_name TEXT, event_description TEXT, created_by TEXT, date_logged TEXT);
    """)
    for n in range(-10, 60):
        d = TODAY + timedelta(days=n)
        c.execute("INSERT INTO Dim_Date (date_id, calendar_date) VALUES (?, ?)",
                  (calendar_extent.date_id_for(d), d.isoformat()))
    c.commit()
    c.close()

    monkeypatch.setattr(dbmod, "DB_PATH", path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    monkeypatch.setattr(backend, "_published_event_days", lambda: set())
    client = backend.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "admin"     # these are edits; the staff path is test_admin_confirm.py
    client.db = path
    return client


def flags(api, iso):
    c = sqlite3.connect(api.db)
    return c.execute("SELECT is_store_closed, is_event_day FROM Dim_Date WHERE calendar_date = ?",
                     (iso,)).fetchone()


def close(api, iso, reason=None):
    return api.put(f"/api/calendar/{iso}/closure", json={"closed": True, "reason": reason})


# ------------------------------------------------------------- closures

def test_moving_a_closure_reopens_the_old_day_and_closes_the_new_one(api):
    close(api, day(10), "Holiday")
    r = api.put(f"/api/calendar/closures/{day(10)}", json={"calendar_date": day(12), "reason": "Moved holiday"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert flags(api, day(10))[0] == 0 and flags(api, day(12))[0] == 1
    log = sqlite3.connect(api.db).execute(
        "SELECT closure_date, is_closed FROM Closure_Log ORDER BY closure_id").fetchall()
    assert log[-2:] == [(day(10), 0), (day(12), 1)]       # what a pipeline rebuild replays
    shown = {c["calendar_date"]: c["reason"] for c in api.get("/api/calendar/closed").get_json()}
    assert day(10) not in shown and shown[day(12)] == "Moved holiday"


def test_changing_only_the_reason_shows_the_newest_one(api):
    close(api, day(5), "Typhoon")
    api.put(f"/api/calendar/closures/{day(5)}", json={"calendar_date": day(5), "reason": "Typhoon signal no. 3"})
    shown = {c["calendar_date"]: c["reason"] for c in api.get("/api/calendar/closed").get_json()}
    assert shown[day(5)] == "Typhoon signal no. 3"


def test_a_past_closure_can_be_corrected(api):
    close(api, day(-3), "Typhoon")
    r = api.put(f"/api/calendar/closures/{day(-3)}", json={"calendar_date": day(-4), "reason": "Typhoon"})
    assert r.status_code == 200
    assert flags(api, day(-3))[0] == 0 and flags(api, day(-4))[0] == 1


def test_an_unflagged_day_or_one_outside_the_calendar_is_refused(api):
    assert api.put(f"/api/calendar/closures/{day(8)}", json={"calendar_date": day(9)}).status_code == 404
    close(api, day(8))
    # The throwaway calendar starts ten days ago: a day before it has no row
    # to hold the closure, and would be silently lost.
    r = api.put(f"/api/calendar/closures/{day(8)}", json={"calendar_date": day(-40)})
    assert r.status_code == 400 and "calendar starts" in r.get_json()["errors"]["calendar_date"]
    assert flags(api, day(8))[0] == 1


# --------------------------------------------------------------- events

def add_event(api, iso, name="Paskuhan"):
    return api.post("/api/events", json={"calendar_date": iso, "event_name": name}).get_json()["event"]


def test_moving_an_event_moves_its_flag(api):
    ev = add_event(api, day(20))
    r = api.put(f"/api/events/{ev['event_id']}",
                json={"calendar_date": day(22), "event_name": "Paskuhan (moved)", "event_description": "x"})
    assert r.status_code == 200
    assert flags(api, day(20))[1] == 0 and flags(api, day(22))[1] == 1
    listed = api.get("/api/events").get_json()
    assert listed[0]["calendar_date"] == day(22) and listed[0]["event_name"] == "Paskuhan (moved)"


def test_the_old_day_keeps_its_flag_while_something_else_is_on_it(api, monkeypatch):
    ev = add_event(api, day(20))
    add_event(api, day(20), "Fair")                       # a second event the same day
    api.put(f"/api/events/{ev['event_id']}", json={"calendar_date": day(21), "event_name": "Paskuhan"})
    assert flags(api, day(20))[1] == 1

    monkeypatch.setattr(backend, "_published_event_days", lambda: {day(30)})
    ev2 = add_event(api, day(30))
    api.put(f"/api/events/{ev2['event_id']}", json={"calendar_date": day(31), "event_name": "Paskuhan"})
    assert flags(api, day(30))[1] == 1                    # the published calendar still has one


def past_event(api):
    c = sqlite3.connect(api.db)
    c.execute("INSERT INTO Event_Log (event_date, event_name) VALUES (?, 'Old')", (day(-2),))
    c.execute("UPDATE Dim_Date SET is_event_day = 1 WHERE calendar_date = ?", (day(-2),))
    c.commit()
    return c.execute("SELECT MAX(event_id) FROM Event_Log").fetchone()[0]


def test_a_past_event_can_be_corrected(api):
    pid = past_event(api)
    r = api.put(f"/api/events/{pid}", json={"calendar_date": day(-5), "event_name": "Old (fixed)"})
    assert r.status_code == 200
    assert flags(api, day(-2))[1] == 0 and flags(api, day(-5))[1] == 1


def test_a_bad_date_or_one_outside_the_calendar_is_refused(api):
    ev = add_event(api, day(9))
    for bad in ("2026-13-45", day(-40)):
        assert api.put(f"/api/events/{ev['event_id']}",
                       json={"calendar_date": bad, "event_name": "x"}).status_code == 400
    assert flags(api, day(9))[1] == 1                     # nothing moved
    assert api.put("/api/events/99999", json={"calendar_date": day(3), "event_name": "x"}).status_code == 404


# ------------------------------------------------------------- deleting

def test_deleting_an_event_removes_it_and_its_flag(api):
    ev = add_event(api, day(15))
    r = api.delete(f"/api/events/{ev['event_id']}")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert api.get("/api/events").get_json() == []
    assert flags(api, day(15))[1] == 0


def test_deleting_one_of_two_events_on_a_day_keeps_the_flag(api):
    ev = add_event(api, day(15))
    add_event(api, day(15), "Fair")
    api.delete(f"/api/events/{ev['event_id']}")
    assert flags(api, day(15))[1] == 1


def test_a_past_event_logged_by_mistake_can_be_deleted(api):
    pid = past_event(api)
    assert api.delete(f"/api/events/{pid}").status_code == 200
    assert flags(api, day(-2))[1] == 0
    assert api.delete("/api/events/99999").status_code == 404


def test_deleting_a_closure_opens_the_day(api):
    close(api, day(6), "Holiday")
    api.put(f"/api/calendar/{day(6)}/closure", json={"closed": False, "reason": "Closure removed"})
    assert flags(api, day(6))[0] == 0
    assert day(6) not in {c["calendar_date"] for c in api.get("/api/calendar/closed").get_json()}
