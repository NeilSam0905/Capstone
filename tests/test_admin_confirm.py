"""
tests/test_admin_confirm.py
------------------------------------------------------------------
The admin's confirmation (backend/app.py _needs_admin, backend/auth.py),
on a throwaway database:

  - an "admin" account exists, and is added to a user table built before it;
  - changing or removing a saved record as anyone else is refused with
    `admin_required` - and nothing changes - until the admin's password
    comes with it (X-Admin-Password); a wrong one is refused too;
  - the admin signed in edits directly;
  - adding something new (an event, a first stock count, a closure) is not
    gated; changing a stock count to a different figure, or reopening a
    closed day, is;
  - wrong admin passwords lock the address out, as wrong logins do.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402
import auth  # noqa: E402
import calendar_extent  # noqa: E402
import db as dbmod  # noqa: E402

TODAY = date.today()
MONTH = TODAY.strftime("%Y-%m")
ADMIN = {auth.ADMIN_PASSWORD_HEADER: "admin123"}


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "ustore.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, supplier_name TEXT);
        INSERT INTO Dim_Product VALUES (1, 'Lanyard', 'VL');
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT UNIQUE,
            semester_id TEXT, semester_week INTEGER, is_enrollment_period INTEGER DEFAULT 0,
            is_exam_week INTEGER DEFAULT 0, is_event_day INTEGER DEFAULT 0, is_sem_break INTEGER DEFAULT 0,
            is_tally_date INTEGER DEFAULT 0, is_tally_date_positive INTEGER DEFAULT 0,
            is_store_closed INTEGER DEFAULT 0);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY AUTOINCREMENT, closure_date TEXT,
            is_closed INTEGER, reason TEXT, created_by TEXT, date_logged TEXT);
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT,
            event_name TEXT, event_description TEXT, created_by TEXT, date_logged TEXT);
        CREATE TABLE Inventory_Count (count_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,
            count_month TEXT NOT NULL, quantity INTEGER NOT NULL, note TEXT, counted_by TEXT,
            date_logged TEXT, UNIQUE (product_id, count_month));
    """)
    for n in range(-10, 40):
        d = TODAY + timedelta(days=n)
        c.execute("INSERT INTO Dim_Date (date_id, calendar_date) VALUES (?, ?)",
                  (calendar_extent.date_id_for(d), d.isoformat()))
    c.commit()
    c.close()

    monkeypatch.setattr(dbmod, "DB_PATH", path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    monkeypatch.setattr(auth, "_failures", {})
    monkeypatch.setattr(backend, "_published_event_days", lambda: set())
    client = backend.app.test_client()
    client.db = path
    return client


def sign_in(api, user):
    with api.session_transaction() as s:
        s["user"] = user


def event_count(api):
    return sqlite3.connect(api.db).execute("SELECT COUNT(*) FROM Event_Log").fetchone()[0]


def add_event(api):
    return api.post("/api/events", json={"calendar_date": day(5), "event_name": "Fair"}).get_json()["event"]


# ---------------------------------------------------------------- account

def test_admin_account_signs_in(api):
    r = api.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200 and r.get_json()["user"] == "admin"


def test_admin_is_added_to_a_user_table_built_before_it(api):
    c = sqlite3.connect(api.db)
    c.row_factory = sqlite3.Row
    c.execute(dbmod.APP_USER_DDL)
    c.execute("INSERT INTO App_User VALUES ('staff', 'x', NULL)")      # an older table: staff only
    c.commit()
    auth._ensure_users(c)
    names = {r[0] for r in c.execute("SELECT username FROM App_User")}
    assert names == {"staff", "admin"}


# ------------------------------------------------------------ the gate

def test_a_staff_edit_waits_for_the_admin_password(api):
    sign_in(api, "staff")
    ev = add_event(api)                                   # adding: not gated
    r = api.delete(f"/api/events/{ev['event_id']}")
    body = r.get_json()
    assert r.status_code == 403 and body["admin_required"] and body["action"] == "Removing an event"
    assert event_count(api) == 1                          # nothing removed

    r = api.delete(f"/api/events/{ev['event_id']}", headers={auth.ADMIN_PASSWORD_HEADER: "nope"})
    assert r.status_code == 403 and r.get_json()["error"] == "That is not the admin password."
    assert event_count(api) == 1

    r = api.delete(f"/api/events/{ev['event_id']}", headers=ADMIN)
    assert r.status_code == 200 and event_count(api) == 0


def test_the_admin_edits_directly(api):
    sign_in(api, "admin")
    ev = add_event(api)
    assert api.delete(f"/api/events/{ev['event_id']}").status_code == 200


def test_a_first_count_is_free_but_changing_it_is_an_edit(api):
    sign_in(api, "staff")
    count = {"product_id": 1, "count_month": MONTH, "quantity": 10}
    assert api.post("/api/inventory", json=count).status_code == 200            # first count
    assert api.post("/api/inventory", json=count).status_code == 200            # same figure again
    r = api.post("/api/inventory", json={**count, "quantity": 12})
    assert r.status_code == 403 and r.get_json()["admin_required"]
    assert api.post("/api/inventory", json={**count, "quantity": 12}, headers=ADMIN).status_code == 200


def test_a_correction_from_current_inventory_is_always_an_edit(api):
    sign_in(api, "staff")
    count = {"product_id": 1, "count_month": MONTH, "quantity": 7, "correction": True}
    r = api.post("/api/inventory", json=count)                                  # first count of the month
    assert r.status_code == 403 and r.get_json()["admin_required"]
    assert api.post("/api/inventory", json=count, headers=ADMIN).status_code == 200


def test_closing_a_day_is_free_but_reopening_it_is_an_edit(api):
    sign_in(api, "staff")
    assert api.put(f"/api/calendar/{day(3)}/closure", json={"closed": True}).status_code == 200
    r = api.put(f"/api/calendar/{day(3)}/closure", json={"closed": False})
    assert r.status_code == 403 and r.get_json()["action"] == "Reopening a closed day"
    r = api.put(f"/api/calendar/{day(3)}/closure", json={"closed": False}, headers=ADMIN)
    assert r.status_code == 200


def test_every_edit_endpoint_is_gated(api):
    sign_in(api, "staff")
    for method, path, body in (
            ("post", "/api/products/1/rename", {"new_name": "Lanyard 2"}),
            ("put", "/api/products/1/status", {"discontinued": True}),
            ("delete", "/api/inventory/1", None),
            ("delete", "/api/restocks/1", None),
            ("delete", "/api/orders/upcoming/1", None),
            ("put", f"/api/calendar/closures/{day(3)}", {"calendar_date": day(4)}),
            ("put", "/api/events/1", {"calendar_date": day(4), "event_name": "x"}),
            ("delete", "/api/events/1", None)):
        r = getattr(api, method)(path, json=body)
        assert r.status_code == 403 and r.get_json().get("admin_required"), (method, path)


def test_wrong_admin_passwords_lock_the_address_out(api):
    sign_in(api, "staff")
    ev = add_event(api)
    wrong = {auth.ADMIN_PASSWORD_HEADER: "guess"}
    for _ in range(auth.MAX_FAILURES):
        assert api.delete(f"/api/events/{ev['event_id']}", headers=wrong).status_code == 403
    r = api.delete(f"/api/events/{ev['event_id']}", headers=ADMIN)       # even the right one, now
    assert r.status_code == 429 and event_count(api) == 1
