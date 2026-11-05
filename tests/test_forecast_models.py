"""
tests/test_forecast_models.py
------------------------------------------------------------------
Comparing forecast models on the Demand Forecast page
(scripts/forecast_options.py, scripts/step4d_forecast_comparison.py,
backend/forecast_models.py, ?model= on the forecast endpoints), and the
Overview's Top Sellers period (/api/overview/performance?top_range=):

  - every model offered is one step4 / step4c accept as --model, and the
    recommended ones are the scripts' own defaults;
  - Run Forecast makes every other model into the comparison tables, and
    Run Analytics leaves that step out;
  - ?model= reads another model's forecast from the comparison tables and
    leaves the recommended one - what every other page reads - as it is;
  - Top Sellers with no period is the 12-month ranking it always was, and a
    period narrows the window.
------------------------------------------------------------------
"""
import os
import shutil
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import app as backend  # noqa: E402  (backend/app.py)
import db as dbmod  # noqa: E402
import forecast_models  # noqa: E402
import forecast_options as fo  # noqa: E402
import pipeline  # noqa: E402
import step4_forecast_model as s4  # noqa: E402
import step4c_category_forecast as s4c  # noqa: E402
import step4d_forecast_comparison as s4d  # noqa: E402


def test_every_option_is_a_model_the_scripts_accept():
    assert {o["id"] for o in fo.OPTIONS["item"]} <= set(s4.MODELS)
    assert {o["id"] for o in fo.OPTIONS["category"]} <= set(s4c.MODELS)
    assert fo.DEFAULTS == {"item": s4.DEFAULT_MODEL, "category": s4c.DEFAULT_MODEL}
    assert "prophet" in fo.alternatives("item") and "prophet" in fo.alternatives("category")
    assert "tsb" in fo.alternatives("item") and "tsb" in fo.alternatives("category")


def test_stored_names_read_as_their_label():
    assert forecast_models.label("category", "RM6_6month_180d+calendar+weekday_shape") \
        == "6-Month Average + School Calendar"
    assert forecast_models.label("item", "prophet") == "Prophet"
    assert forecast_models.label("item", "something_else") == "something_else"


def test_run_forecast_makes_every_other_model_and_run_analytics_does_not(tmp_path, monkeypatch):
    assert {"step4", "step4d"} <= pipeline.SKIPPABLE
    ids = [s[0] for s in pipeline.STEPS]
    assert ids.index("step4c") < ids.index("step4d") < ids.index("step5")

    sqlite3.connect(tmp_path / "ustore.db").close()
    monkeypatch.setattr(s4d, "DB_PATH", str(tmp_path / "ustore.db"))
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "Wrote 30 rows", ""

    monkeypatch.setattr(s4d.subprocess, "run", lambda cmd, **kw: ran.append(cmd[2:]) or Done())
    assert s4d.main() == 0
    expected = ([[s4d.SCRIPTS["category"], "--model", m, "--compare"] for m in fo.alternatives("category")]
                + [[s4d.SCRIPTS["item"], "--model", m, "--compare"] for m in fo.alternatives("item")])
    assert ran == expected


def test_write_compare_keeps_the_main_tables(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    s4c.create_result_tables(con)
    con.execute("INSERT INTO Result_Category_Forecast (forecast_category, forecast_date, yhat, model_type) "
                "VALUES ('Bags', '2026-11-01', 5, 'RM6_6month_180d+calendar+weekday_shape')")
    rows = [{"forecast_category": "Bags", "forecast_date": "2026-11-01", "yhat": 9.0, "model_type": "tsb"}]
    fo.write_compare(con, "category", "tsb", rows, [])
    fo.write_compare(con, "category", "tsb", rows, [])                 # replaced, not added to
    assert con.execute("SELECT model, yhat FROM Result_Category_Forecast_Compare").fetchall() == [("tsb", 9.0)]
    assert con.execute("SELECT yhat FROM Result_Category_Forecast").fetchall() == [(5.0,)]


REAL_DB = os.path.join(ROOT, "ustore.db")


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    """The forecast and overview endpoints need a real catalogue and real
    sales; a copy, so nothing the test writes reaches ustore.db."""
    if not os.path.exists(REAL_DB):
        pytest.skip("ustore.db not built")
    db_path = tmp_path / "ustore.db"
    shutil.copy(REAL_DB, db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    client.db = db_path
    return client


def test_another_models_forecast_is_shown_beside_the_recommended(real_api):
    con = sqlite3.connect(real_api.db)
    # From no comparison forecasts, whatever Run Forecast has left in ustore.db.
    for table in (*fo.COMPARE_TABLES["item"], *fo.COMPARE_TABLES["category"]):
        con.execute(f"DROP TABLE IF EXISTS {table}")
    con.commit()
    cat = con.execute("SELECT forecast_category FROM Result_Category_Forecast LIMIT 1").fetchone()
    if cat is None:
        pytest.skip("no category forecasts in this database")
    cat = cat[0]
    main_total = con.execute("SELECT SUM(yhat) FROM Result_Category_Forecast WHERE forecast_category = ?",
                             (cat,)).fetchone()[0]

    # Not made yet: said so, not filled in from anything else.
    r = real_api.get(f"/api/forecast/category/{cat}?model=tsb").get_json()
    assert r["available"] is False and "Run Forecast" in r["reason"]
    assert real_api.get(f"/api/forecast/category/{cat}?model=nonsense").status_code == 400
    models = real_api.get("/api/forecast/models").get_json()
    tsb = next(o for o in models["category"]["options"] if o["id"] == "tsb")
    assert tsb["made"] is None
    assert next(o for o in models["category"]["options"] if o.get("recommended"))["made"] is not None

    # Made: the comparison tables answer for it, the recommended one is untouched.
    rows = [dict(zip(("forecast_category", "forecast_date", "yhat", "yhat_lower", "yhat_upper",
                      "model_type", "is_heuristic", "snapshot_date", "history_end"), r))
            for r in con.execute("SELECT forecast_category, forecast_date, yhat * 2, yhat_lower, yhat_upper, "
                                 "'tsb+weekday_shape', is_heuristic, snapshot_date, history_end "
                                 "FROM Result_Category_Forecast WHERE forecast_category = ?", (cat,))]
    metric = {"forecast_category": cat, "validation_method": "walk_forward_30d_aggregate",
              "period_scope": "overall", "n_obs": 12, "mae": 1.0, "rmse": 2.0, "mape": 30.0, "mase": 0.01,
              "naive_mae": 3.0, "naive_mase": 1.0, "beats_naive_mae": 1, "mean_actual_30d": 10.0}
    fo.write_compare(con, "category", "tsb", rows, [metric])
    con.commit()
    con.close()

    # Every model's check, for the (i)'s comparison: the recommended one from
    # the main tables, TSB from the comparison tables, the rest not made.
    compared = {r["id"]: r for r in
                real_api.get(f"/api/forecast/category/{cat}").get_json()["data"]["model_metrics"]}
    assert set(compared) == {o["id"] for o in fo.OPTIONS["category"]}
    assert compared["tsb"]["made"] and compared["tsb"]["mase"] == 0.01
    assert compared[fo.DEFAULTS["category"]]["made"]
    assert not compared["prophet"]["made"] and compared["prophet"]["mase"] is None

    shown = real_api.get(f"/api/forecast/category/{cat}?model=tsb").get_json()["data"]
    assert shown["total_30d"] == pytest.approx(main_total * 2, abs=1e-3)
    assert shown["model_label"] == "TSB (Recent Sales Trend)"
    default = real_api.get(f"/api/forecast/category/{cat}").get_json()["data"]
    assert default["total_30d"] == pytest.approx(main_total, abs=1e-3)
    models = real_api.get("/api/forecast/models").get_json()
    assert next(o for o in models["category"]["options"] if o["id"] == "tsb")["made"] is not None


def test_calendar_marks_name_what_falls_in_the_forecast(tmp_path, monkeypatch):
    (tmp_path / "calendar_ranges.csv").write_text("""start_date,end_date,flag,semester_id,event_name
2026-10-29,2026-11-03,is_sem_break,AY2627-T1,Undas Break
2026-11-30,2026-11-30,is_store_closed,AY2627-T1,Bonifacio Day
2026-10-20,2026-10-20,is_event_day,AY2627-T1,Foundation Day (date uncertain - verify)
""", encoding="utf-8")
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path)
    con = sqlite3.connect(tmp_path / "x.db")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY, event_date TEXT, event_name TEXT);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY, closure_date TEXT, is_closed INTEGER, reason TEXT);
        INSERT INTO Event_Log (event_date, event_name) VALUES ('2026-11-05', 'Book Fair'), ('2027-01-05', 'Later');
        INSERT INTO Closure_Log (closure_date, is_closed, reason) VALUES ('2026-11-06', 1, NULL), ('2026-11-07', 0, 'x');
    """)
    marks = backend._calendar_marks(con, "2026-10-19", "2026-11-01")
    assert marks == [{"start": "2026-10-20", "end": "2026-10-20", "name": "Foundation Day", "kind": "event"},
                     {"start": "2026-10-29", "end": "2026-11-01", "name": "Undas Break", "kind": "break"}]
    later = backend._calendar_marks(con, "2026-11-04", "2026-11-17")
    assert [(m["name"], m["kind"]) for m in later] == [("Book Fair", "event"), ("Store closed", "closed")]


def test_top_sellers_period(real_api):
    default = real_api.get("/api/overview/performance").get_json()
    if not default.get("available"):
        pytest.skip("no sales tallied")
    top = default["top"]
    assert top["range"] == "Last 12 Months"
    twelve = real_api.get("/api/overview/performance?top_range=Last%2012%20Months").get_json()["top"]
    assert twelve["items"] == top["items"]

    month = real_api.get("/api/overview/performance?top_range=This%20Month").get_json()["top"]
    assert month["window_start"] == default["history_end"][:7] + "-01"
    assert month["total_php"] <= top["total_php"]
    everything = real_api.get("/api/overview/performance?top_range=All%20Time").get_json()["top"]
    assert everything["total_php"] >= top["total_php"]
    assert real_api.get("/api/overview/performance?top_range=Forever").status_code == 400
