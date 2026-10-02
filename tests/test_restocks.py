"""
tests/test_restocks.py
------------------------------------------------------------------
Measured lead times (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 5): staff record
restocks (ordered / delivered) in the Tally Interface, and step5a uses a
supplier's median delivery time once it has MIN_DELIVERIES of them, in place
of the verbal 14 / 18 / 28-day estimate. Fewer, or none: nothing changes.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import db as dbmod  # noqa: E402  (backend/db.py)
import step5a_set_lead_times as step5a  # noqa: E402


def _con(deliveries):
    con = sqlite3.connect(":memory:")
    con.execute(dbmod.RESTOCK_LOG_DDL)
    con.executemany("INSERT INTO Restock_Log (supplier_name, ordered_on, delivered_on) VALUES (?, ?, ?)",
                    deliveries)
    return con


def test_a_supplier_needs_enough_deliveries_before_its_median_is_used():
    con = _con([
        ("JYL ATHLETICA", "2026-08-01", "2026-08-21"),     # 20 days
        ("JYL ATHLETICA", "2026-09-01", "2026-09-25"),     # 24
        ("JYL ATHLETICA", "2026-09-10", "2026-09-30"),     # 20
        ("JUC", "2026-09-01", "2026-09-08"),                # only two deliveries
        ("JUC", "2026-09-10", "2026-09-17"),
        ("JUC", "2026-09-20", None),                        # still on the way: not counted
        ("BLAZE", "2026-09-20", "2026-09-10"),              # before the order: not counted
    ])
    assert step5a.measured_lead_times(con) == {"JYL ATHLETICA": (20, 3)}
    measured = step5a.measured_lead_times(con)
    assert step5a.lead_time("Corp Jacket", None, "JYL ATHLETICA", measured) == (20, "measured (3 deliveries)")
    assert step5a.lead_time("Corp Jacket", None, "JUC", measured) == (28, "jacket")
    assert step5a.lead_time("UST Shirt", None, None, measured) == (14, "simple_dtf_puff_shirt")


def test_no_restock_table_means_the_estimates():
    con = sqlite3.connect(":memory:")
    assert step5a.measured_lead_times(con) == {}


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app as backend
    db_path = tmp_path / "ustore.db"
    con = sqlite3.connect(db_path)
    con.executescript(f"""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, supplier_name TEXT,
                                  lead_time_days INTEGER);
        INSERT INTO Dim_Product VALUES (1, 'Corp Jacket', 'JYL ATHLETICA', 28),
                                       (2, 'Corp Jacket V3', 'JYL ATHLETICA', 28),
                                       (3, 'Polo', 'JYL ATHLETICA', 14);
        {dbmod.RESTOCK_LOG_DDL};
    """)
    con.close()
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    client = backend.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "staff"
    return client


def test_record_deliver_summarise_and_remove(api):
    bad = api.post("/api/restocks", json={"supplier_name": "", "ordered_on": "2026-09-10",
                                          "delivered_on": "2026-09-01"}).get_json()
    assert set(bad["errors"]) == {"supplier_name", "delivered_on"}
    future = api.post("/api/restocks", json={"supplier_name": "JUC", "ordered_on": "2999-01-01"})
    assert future.status_code == 400

    for o, d in [("2026-08-01", "2026-08-21"), ("2026-09-01", "2026-09-25")]:
        assert api.post("/api/restocks", json={"supplier_name": "JYL ATHLETICA",
                                               "ordered_on": o, "delivered_on": d}).status_code == 200
    rid = api.post("/api/restocks", json={"supplier_name": "JYL ATHLETICA",
                                          "ordered_on": "2026-09-10"}).get_json()["restock_id"]
    (s,) = api.get("/api/restocks").get_json()["suppliers"]
    assert (s["deliveries"], s["open"], s["in_use"], s["lead_time_now"]) == (2, 1, False, 28)

    assert api.put(f"/api/restocks/{rid}", json={"delivered_on": "2026-09-05"}).status_code == 400  # before order
    assert api.put(f"/api/restocks/{rid}", json={"delivered_on": "2026-09-30"}).status_code == 200
    body = api.get("/api/restocks").get_json()
    (s,) = body["suppliers"]
    assert (s["deliveries"], s["open"], s["median_days"], s["in_use"]) == (3, 0, 20, True)
    assert body["restocks"][0]["days"] == 20

    assert api.delete(f"/api/restocks/{rid}").status_code == 200
    assert api.get("/api/restocks").get_json()["suppliers"][0]["deliveries"] == 2
    assert api.delete(f"/api/restocks/{rid}").status_code == 404
