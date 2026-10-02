"""
tests/test_auth_api.py
------------------------------------------------------------------
The API's login, origin checks and attribution (backend/auth.py,
docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 1.2), against a throwaway database:

  - every /api route but /api/auth/* refuses a request with no session;
  - the default staff account is created on first use and signs in;
    a wrong password does not, and repeated ones lock the address out;
  - a write from an address that is not the dashboard is refused, and
    CORS answers only the dashboard's addresses;
  - a tally entry records who entered it (Fact_Sales.entered_by), on a
    database whose Fact_Sales predates that column.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402  (backend/app.py)
import auth  # noqa: E402
import db as dbmod  # noqa: E402

STAFF = {"username": "staff", "password": "staff123"}
DASHBOARD = "http://localhost:5173"


@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = tmp_path / "ustore.db"
    con = sqlite3.connect(db_path)
    # Fact_Sales as it was before entered_by: the backend must add it.
    con.executescript("""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT NOT NULL,
            supplier_name TEXT, is_active INTEGER);
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
            quantity_sold INTEGER, imputation_flag INTEGER DEFAULT 0, tally_date_flag INTEGER DEFAULT 0,
            transaction_type TEXT DEFAULT 'sale', is_censored INTEGER);
        INSERT INTO Dim_Product VALUES (1, 'Lanyard', 'VARSITY LIFESTYLE', 1);
        INSERT INTO Dim_Date VALUES (1, '2026-08-03');
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    monkeypatch.setattr(auth, "_failures", {})
    client = backend.app.test_client()
    client.db = db_path
    return client


def login(api, **creds):
    return api.post("/api/auth/login", json={**STAFF, **creds})


def tally(api, **headers):
    return api.post("/api/tally", headers=headers, json={
        "product_id": 1, "quantity_sold": 2, "calendar_date": "2026-08-03", "transaction_type": "SALE"})


def test_routes_refuse_a_request_with_no_session(api):
    for method, path in (("get", "/api/meta"), ("get", "/api/tally/recent"),
                         ("post", "/api/pipeline/run"), ("post", "/api/tally")):
        r = getattr(api, method)(path, json={} if method == "post" else None)
        assert r.status_code == 401, path
        assert r.get_json()["auth_required"] is True
    assert api.get("/api/auth/session").get_json() == {"authenticated": False, "user": None}


def test_staff_signs_in_and_out(api):
    r = login(api)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "user": "staff"}
    assert api.get("/api/auth/session").get_json() == {"authenticated": True, "user": "staff"}

    con = sqlite3.connect(api.db)
    (stored,) = con.execute("SELECT password_hash FROM App_User WHERE username = 'staff'").fetchone()
    con.close()
    assert stored != "staff123"                      # hashed, never plain

    api.post("/api/auth/logout")
    assert api.get("/api/auth/session").get_json()["authenticated"] is False
    assert api.get("/api/tally/recent").status_code == 401


def test_wrong_password_is_refused_then_locked_out(api):
    for _ in range(auth.MAX_FAILURES):
        r = login(api, password="nope")
        assert r.status_code == 401
        assert "auth_required" not in r.get_json()    # a failed login is not an ended session
    assert login(api).status_code == 429              # even the right password waits
    assert api.get("/api/auth/session").get_json()["authenticated"] is False


def test_tally_entry_records_who_entered_it(api):
    login(api)
    r = tally(api, Origin=DASHBOARD)
    assert r.status_code == 200
    assert r.get_json()["entry"]["entered_by"] == "staff"
    con = sqlite3.connect(api.db)
    assert con.execute("SELECT entered_by FROM Fact_Sales").fetchall() == [("staff",)]
    con.close()
    assert api.get("/api/tally/recent").get_json()[0]["entered_by"] == "staff"


def test_writes_from_another_site_are_refused(api):
    login(api)
    r = tally(api, Origin="http://evil.example")
    assert r.status_code == 403
    con = sqlite3.connect(api.db)
    assert con.execute("SELECT COUNT(*) FROM Fact_Sales").fetchone() == (0,)
    con.close()
    # Reads are not origin-checked (CORS keeps another site from seeing them).
    assert api.get("/api/tally/recent", headers={"Origin": "http://evil.example"}).status_code == 200


def test_cors_answers_only_the_dashboard(api):
    ok = api.options("/api/tally", headers={"Origin": DASHBOARD, "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("Access-Control-Allow-Origin") == DASHBOARD
    assert ok.headers.get("Access-Control-Allow-Credentials") == "true"
    bad = api.options("/api/tally", headers={"Origin": "http://evil.example",
                                             "Access-Control-Request-Method": "POST"})
    assert "Access-Control-Allow-Origin" not in bad.headers
