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
    period narrows the window;
  - the Sale Overview compares with the month before unless ?compare= names
    another tallied month.
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


def test_checks_are_each_fold_on_its_calendar_days(tmp_path):
    """Actual vs Predicted: one row per fold with the days it covered, what
    sold, what the model and the naive forecast said; replaced per model."""
    import numpy as np
    import pandas as pd
    from forecasting.evaluate import make_folds, walk_forward_evaluate

    idx = pd.date_range("2025-01-01", periods=420, freq="D")     # 60 to train + 12 x 30
    values = np.arange(420, dtype=float) % 7
    folds = make_folds(values.size, 30, 3, 12, 60)
    flat = lambda train, h: np.full(h, train[-30:].mean())   # noqa: E731
    ev_m = walk_forward_evaluate("Bags", values, flat, "flat", folds=folds)
    ev_n = walk_forward_evaluate("Bags", values, s4c.NAIVE, "naive", folds=folds)
    rows = fo.check_rows("Bags", folds, idx, ev_m.rows, ev_n.rows, "2026-02-04")

    assert len(rows) == 12 and rows[-1]["end_date"] == idx[-1].strftime("%Y-%m-%d")
    for r, f in zip(rows, folds):
        assert r["start_date"] == idx[f.test_start].strftime("%Y-%m-%d")
        assert (pd.Timestamp(r["end_date"]) - pd.Timestamp(r["start_date"])).days == 29
        assert r["actual_30d"] == pytest.approx(values[f.test_start:f.test_end].sum())

    con = sqlite3.connect(tmp_path / "x.db")
    fo.write_checks(con, "category", "tsb", rows)
    fo.write_checks(con, "category", "tsb", rows)                      # replaced, not added to
    fo.write_checks(con, "category", "rolling_mean_30", rows[:2])
    assert con.execute(f"SELECT model, COUNT(*) FROM {fo.CHECK_TABLE} GROUP BY 1 ORDER BY 1").fetchall() \
        == [("rolling_mean_30", 2), ("tsb", 12)]


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


def test_top_sellers_months(real_api):
    """?top_from=&top_to= rank whole months, inclusive; one month is from = to."""
    default = real_api.get("/api/overview/performance").get_json()
    if not default.get("available") or len(default["compare_months"]) < 3:
        pytest.skip("too few tallied months")
    latest = default["latest_month"]["month"]
    prev = default["compare_months"][0]
    older = default["compare_months"][2]

    one = real_api.get(f"/api/overview/performance?top_from={prev}&top_to={prev}").get_json()
    assert one["top"]["range"] == backend.TOP_CUSTOM_RANGE
    assert one["top"]["window_start"] == f"{prev}-01"
    assert one["top"]["window_end"] == backend._month_end(prev).isoformat()
    assert one["top"]["total_php"] == pytest.approx(default["previous_month"]["php"], abs=1)

    # The latest month ends at the last tallied day, and matches "This Month".
    now = real_api.get(f"/api/overview/performance?top_from={latest}&top_to={latest}").get_json()["top"]
    this_month = real_api.get("/api/overview/performance?top_range=This%20Month").get_json()["top"]
    assert now["window_end"] == default["history_end"]
    assert now["items"] == this_month["items"]

    span = real_api.get(f"/api/overview/performance?top_from={older}&top_to={prev}").get_json()["top"]
    assert span["total_php"] >= one["top"]["total_php"]
    for bad in (f"top_from={prev}&top_to={older}", f"top_from={prev}", "top_from=July&top_to=July"):
        assert real_api.get(f"/api/overview/performance?{bad}").status_code == 400


def test_forecast_carries_its_checks_and_the_days_tallied_since(real_api):
    """Without the checks table the forecast says so (None); with it, the
    shown model's checks come back oldest first. Days sold since the forecast
    are only days the store tallied anything."""
    cats = real_api.get("/api/forecast/categories").get_json()
    names = [r["category"] for r in (cats.get("data") or {}).get("categories", [])]
    if not names:
        pytest.skip("no category forecast")
    cat = names[0]
    con = sqlite3.connect(real_api.db)
    con.execute(f"DROP TABLE IF EXISTS {fo.CHECK_TABLE}")
    con.commit()
    fd = real_api.get(f"/api/forecast/category/{cat}").get_json()["data"]
    assert fd["checks"] is None

    rows = [{"series_key": cat, "fold": i, "start_date": f"2026-0{i + 1}-01", "end_date": f"2026-0{i + 1}-30",
             "actual_30d": 10.0 * i, "pred_30d": 12.0, "naive_30d": 9.0, "snapshot_date": "x"} for i in (1, 0)]
    fo.write_checks(con, "category", fo.DEFAULTS["category"], rows)
    fo.write_checks(con, "category", "tsb", rows[:1])
    con.commit()
    fd = real_api.get(f"/api/forecast/category/{cat}").get_json()["data"]
    assert [r["start_date"] for r in fd["checks"]] == ["2026-01-01", "2026-02-01"]
    other = real_api.get(f"/api/forecast/category/{cat}?model=tsb").get_json()["data"]
    if other:                                                          # tsb made for comparison
        assert len(other["checks"]) == 1

    # Tallied days only: shift the forecast back over real history and every
    # day returned must be one the store sold something on.
    con.execute("UPDATE Result_Category_Forecast SET forecast_date = date(forecast_date, '-45 days')")
    con.commit()
    fd = real_api.get(f"/api/forecast/category/{cat}").get_json()["data"]
    tallied = {r[0] for r in con.execute("""
        SELECT DISTINCT d.calendar_date FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.quantity_sold > 0 AND LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'""")}
    assert fd["sold_since"], "the shifted window overlaps tallied days"
    assert all(r["date"] in tallied for r in fd["sold_since"])


def test_sale_overview_compares_with_a_chosen_month(real_api):
    """?compare= puts an earlier tallied month in place of the month before;
    no compare is the month before, as it always was."""
    default = real_api.get("/api/overview/performance").get_json()
    if not default.get("available") or len(default["compare_months"]) < 2:
        pytest.skip("too few tallied months")
    latest = default["latest_month"]["month"]
    months = default["compare_months"]
    assert months == sorted(months, reverse=True) and all(m < latest for m in months)
    assert default["compare_month"] == default["default_compare_month"] == backend._month_before(latest)
    assert default["latest_month"]["days"] >= 1

    older = months[-1]
    chosen = real_api.get(f"/api/overview/performance?compare={older}").get_json()
    assert chosen["compare_month"] == older and chosen["previous_month"]["month"] == older
    assert chosen["latest_month"] == default["latest_month"]
    assert chosen["top"] == default["top"]   # Top Sellers does not follow it
    assert sum(c["previous_php"] for c in chosen["by_category"]) == pytest.approx(chosen["previous_month"]["php"],
                                                                                  abs=1)

    same = real_api.get(f"/api/overview/performance?compare={default['default_compare_month']}").get_json()
    assert same["previous_month"] == default["previous_month"]
    for bad in (latest, "2099-01", "July"):
        assert real_api.get(f"/api/overview/performance?compare={bad}").status_code == 400


def test_sale_overview_compares_two_chosen_months(real_api):
    """?month= moves the later month too (the topbar's month comparator); no
    month is the latest tallied one, as it always was."""
    default = real_api.get("/api/overview/performance").get_json()
    if not default.get("available") or len(default["compare_months"]) < 3:
        pytest.skip("too few tallied months")
    latest = default["latest_month"]["month"]
    assert default["latest_tallied_month"] == latest
    assert default["months"][0] == latest and default["months"][1:] == default["compare_months"]

    later, earlier = default["compare_months"][0], default["compare_months"][-1]
    pair = real_api.get(f"/api/overview/performance?month={later}&compare={earlier}").get_json()
    assert pair["latest_month"]["month"] == later and pair["previous_month"]["month"] == earlier
    assert pair["latest_tallied_month"] == latest and pair["latest_month_complete"] is True
    assert pair["compare_months"] == [m for m in default["months"] if m < later]
    assert pair["default_compare_month"] == backend._month_before(later)
    assert pair["top"] == default["top"]   # Top Sellers does not follow it
    assert sum(c["php"] for c in pair["by_category"]) == pytest.approx(pair["latest_month"]["php"], abs=1)

    alone = real_api.get(f"/api/overview/performance?month={later}").get_json()
    assert alone["compare_month"] == backend._month_before(later)
    same = real_api.get(f"/api/overview/performance?month={latest}").get_json()
    assert same["latest_month"] == default["latest_month"] and same["previous_month"] == default["previous_month"]

    for bad in ("2099-01", "July"):
        assert real_api.get(f"/api/overview/performance?month={bad}").status_code == 400
    # The compared month has to come before the chosen one.
    assert real_api.get(f"/api/overview/performance?month={earlier}&compare={later}").status_code == 400


def test_product_movement_lists_every_tallied_month(real_api):
    """/movement (the advisories' item detail) gives an item's units in every
    month the store tallied sales - 0 where it sold none, so a non-moving item
    is a full row of zeros, not an empty list - and its inventory counts."""
    months = real_api.get("/api/overview/performance").get_json().get("months")
    if not months:
        pytest.skip("no tallied months")
    advs = real_api.get("/api/advisories").get_json()["advisories"]
    items = [i for a in advs for i in a["items"]]
    if not items:
        pytest.skip("no advisory items")
    for item in items[:3] + [i for a in advs if a["type"] == "non_moving" for i in a["items"][:1]]:
        m = real_api.get(f"/api/products/{item['product_id']}/movement").get_json()
        assert [r["month"] for r in m["months"]] == sorted(months)
        assert m["total_units"] == sum(r["units"] for r in m["months"])
        assert m["months_sold"] == sum(1 for r in m["months"] if r["units"] > 0)
        assert (m["last_sale"] is None) == (m["total_units"] == 0)
        assert [s["month"] for s in m["stock"]] == sorted({s["month"] for s in m["stock"]})
        if item.get("fsn_class") == "N":
            assert m["total_units"] == 0
    assert real_api.get("/api/products/999999999/movement").status_code == 404
