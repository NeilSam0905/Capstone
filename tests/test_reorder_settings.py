"""
tests/test_reorder_settings.py
------------------------------------------------------------------
Reorder settings the store saves on the Reorder Alerts page
(backend/reorder_settings.py, /api/reorder/<id>/settings):

  - only the inputs are set; the reorder point and EOQ are worked out from
    them with step5's formulas, and a blank keeps the calculated figure;
  - a new lead time with no safety stock of its own scales the buffer by
    the square root, as the what-if does; a typed safety stock is used as is;
  - a typed cost replaces both EOQ cost scenarios;
  - bad input is refused per field, an empty save is refused, and an item
    with no reorder point cannot be set;
  - saved settings reach /api/reorder (and so the what-if and the forecast
    guidance), the pipeline's figures stay under `calculated`, saving and
    resetting need the admin, and Result_Prescriptive is never written.
------------------------------------------------------------------
"""
import copy
import math
import os
import shutil
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import app as backend  # noqa: E402  (backend/app.py)
import db as dbmod  # noqa: E402
import reorder_settings as rs  # noqa: E402


def item(**over):
    """An entry shaped like app._reorder_items' output for a priced item."""
    d, lt, ss = 4.0, 18, 10.0
    base = {
        "product_id": 7, "item_name": "Lanyard", "avg_daily_demand": d, "annual_demand": 365 * d,
        "lead_time_days": lt, "safety_stock": ss, "reorder_point": d * lt + ss, "current_stock": 50,
        "scenarios": {
            "low_admin_cost": {"ordering_cost_php": 1250.0, "holding_cost_php_per_unit_year": 1.5,
                               "eoq": math.sqrt(2 * 1460 * 1250 / 1.5)},
            "high_goods_value": {"ordering_cost_php": 2500.0, "holding_cost_php_per_unit_year": 3.0,
                                 "eoq": math.sqrt(2 * 1460 * 2500 / 3.0)},
        },
    }
    base.update(over)
    return base


def setting(**values):
    return {**{k: None for k in rs.FIELDS}, **values}


# ---- the rules -----------------------------------------------------------

def test_a_typed_safety_stock_moves_the_reorder_point_by_the_formula():
    it = rs.apply(item(), setting(safety_stock=30))
    assert it["safety_stock"] == 30
    assert it["reorder_point"] == pytest.approx(4.0 * 18 + 30)
    assert it["calculated"]["reorder_point"] == pytest.approx(82)
    assert it["calculated"]["safety_stock"] == 10.0
    assert it["adjusted"]["safety_stock"] == 30


def test_a_new_lead_time_alone_scales_the_buffer_by_its_square_root():
    it = rs.apply(item(), setting(lead_time_days=36))
    assert it["safety_stock"] == pytest.approx(10.0 * math.sqrt(2))
    assert it["reorder_point"] == pytest.approx(4.0 * 36 + 10.0 * math.sqrt(2))


def test_a_new_lead_time_with_a_typed_buffer_uses_the_buffer_as_typed():
    it = rs.apply(item(), setting(lead_time_days=36, safety_stock=12))
    assert it["reorder_point"] == pytest.approx(4.0 * 36 + 12)


def test_typed_costs_replace_both_scenarios_and_move_only_the_eoq():
    it = rs.apply(item(), setting(ordering_cost_php=500, holding_cost_php=2.0))
    q = math.sqrt(2 * 1460 * 500 / 2.0)
    for scen in it["scenarios"].values():
        assert scen["eoq"] == pytest.approx(q)
        assert scen["cost_at_eoq"] == pytest.approx(1460 / q * 500 + q / 2 * 2.0)
    assert it["reorder_point"] == pytest.approx(82)


def test_blank_settings_keep_every_calculated_figure():
    it = rs.apply(item(), setting())
    assert (it["lead_time_days"], it["safety_stock"], it["reorder_point"]) == (18, 10.0, 82.0)
    assert it["scenarios"]["high_goods_value"]["eoq"] == pytest.approx(math.sqrt(2 * 1460 * 2500 / 3.0))


def test_the_preview_orders_up_to_one_review_period_past_the_reorder_point():
    f = rs.figures(rs.apply(item(), setting(safety_stock=30)), 30)
    assert f["needs_reorder"] is True                           # 50 <= 102
    assert f["suggested_order_qty"] == round(102 + 30 * 4.0 - 50)
    assert rs.figures(item(current_stock=None), 30)["suggested_order_qty"] is None


@pytest.mark.parametrize("body, field", [
    ({"lead_time_days": "abc"}, "lead_time_days"),
    ({"lead_time_days": 0}, "lead_time_days"),
    ({"lead_time_days": 2.5}, "lead_time_days"),
    ({"safety_stock": -1}, "safety_stock"),
    ({"ordering_cost_php": 0}, "ordering_cost_php"),
    ({"holding_cost_php": "nan"}, "holding_cost_php"),
    ({"note": "x" * 301}, "note"),
])
def test_bad_input_is_refused_per_field(body, field):
    assert field in rs.parse(body)[1]


def test_blank_fields_are_kept_as_calculated():
    values, errors = rs.parse({"lead_time_days": "", "safety_stock": None, "note": "  "})
    assert errors == {} and not rs.any_set(values) and values["note"] is None


# ---- the endpoints, on a copy of the real database -----------------------

REAL_DB = os.path.join(ROOT, "ustore.db")


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    if not os.path.exists(REAL_DB):
        pytest.skip("ustore.db not built")
    db_path = tmp_path / "ustore.db"
    shutil.copy(REAL_DB, db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    return client, db_path


def _priced(client):
    data = client.get("/api/reorder").get_json()
    if not data["available"]:
        pytest.skip("step5 has not run")
    items = data["data"]["items"]
    return items, next(i for i in items if i["policy_state"] == "priced" and i["current_stock"] is not None)


def _stored(db_path, product_id):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT * FROM Result_Prescriptive WHERE product_id = ? ORDER BY 1",
                           (product_id,)).fetchall()
    finally:
        con.close()


def test_save_reaches_the_reorder_table_and_reset_restores_it(real_api):
    client, db_path = real_api
    _, it = _priced(client)
    pid, before = it["product_id"], _stored(db_path, it["product_id"])

    preview = client.get(f"/api/reorder/{pid}/settings?safety_stock=0").get_json()
    assert preview["ok"] and preview["saved"] is None
    assert preview["calculated"]["reorder_point"] == pytest.approx(it["reorder_point"])
    assert preview["preview"]["reorder_point"] == pytest.approx(
        it["avg_daily_demand"] * it["lead_time_days"])

    r = client.put(f"/api/reorder/{pid}/settings", json={"safety_stock": 0, "note": "test"})
    assert r.status_code == 200, r.get_json()
    after = next(i for i in client.get("/api/reorder").get_json()["data"]["items"] if i["product_id"] == pid)
    assert after["safety_stock"] == 0
    assert after["reorder_point"] == pytest.approx(it["avg_daily_demand"] * it["lead_time_days"])
    assert after["needs_reorder"] == (after["current_stock"] <= after["reorder_point"])
    assert after["calculated"]["reorder_point"] == pytest.approx(it["reorder_point"])
    assert after["adjusted"]["note"] == "test"
    wi = client.get(f"/api/reorder/{pid}/whatif").get_json()
    assert wi["current"]["reorder_point"] == pytest.approx(after["reorder_point"])
    assert _stored(db_path, pid) == before          # the pipeline's results are untouched

    assert client.delete(f"/api/reorder/{pid}/settings").status_code == 200
    back = next(i for i in client.get("/api/reorder").get_json()["data"]["items"] if i["product_id"] == pid)
    assert back["reorder_point"] == pytest.approx(it["reorder_point"]) and back["adjusted"] is None
    assert client.delete(f"/api/reorder/{pid}/settings").status_code == 404


def test_refusals(real_api):
    client, _ = real_api
    items, it = _priced(client)
    pid = it["product_id"]
    assert client.put(f"/api/reorder/{pid}/settings", json={}).status_code == 400
    bad = client.put(f"/api/reorder/{pid}/settings", json={"lead_time_days": 0})
    assert bad.status_code == 400 and "lead_time_days" in bad.get_json()["errors"]
    flagged = next((i for i in items if i["policy_state"] == "insufficient_data"), None)
    if flagged:
        assert client.put(f"/api/reorder/{flagged['product_id']}/settings",
                          json={"safety_stock": 1}).status_code == 409
    assert client.get("/api/reorder/999999999/settings").status_code == 404


def test_the_method_explanation_reads_the_pipelines_own_figures(real_api):
    client, db_path = real_api
    _priced(client)
    m = client.get("/api/reorder/method").get_json()
    assert m["available"]
    con = sqlite3.connect(db_path)
    try:
        h = con.execute("SELECT value FROM Dim_Parameters "
                        "WHERE parameter_name = 'derived.holding_cost_php_per_unit_year'").fetchone()[0]
        n = con.execute("SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive "
                        "WHERE lead_time_days IS NOT NULL").fetchone()[0]
    finally:
        con.close()
    assert m["holding_cost"] == h
    assert sum(lt["items"] for lt in m["lead_times"]) >= n      # an item counts once per lead time
    assert m["review_period_days"] == backend.REVIEW_PERIOD_DAYS
    assert m["adjusted"] == 0


def test_saving_needs_the_admin(real_api, monkeypatch):
    client, _ = real_api
    _, it = _priced(client)
    monkeypatch.setattr(backend.auth, "is_admin", lambda user: False)
    r = client.put(f"/api/reorder/{it['product_id']}/settings", json={"safety_stock": 1})
    assert r.status_code == 403 and r.get_json()["admin_required"]
