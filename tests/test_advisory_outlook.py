"""
tests/test_advisory_outlook.py
------------------------------------------------------------------
The predictive part of a calendar advisory (backend/advisory_outlook.py):

  - expected sales per selling day = a regular day's recent level x the lift
    of each date's kind of day, averaged over the window;
  - the level is the recent regular days, or every regular day when the
    recent ones are too few;
  - last year's window is the past run of the same flag nearest one year
    back, else the same weekdays a year back, and only once it is over;
  - its chart series marks the window, and days with nothing tallied as None;
  - /api/advisories carries it on the calendar advisories only.
------------------------------------------------------------------
"""
import os
import pathlib
import shutil
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import advisory_outlook as ao  # noqa: E402
import app as backend  # noqa: E402
import db as dbmod  # noqa: E402

REAL_DB = os.path.join(ROOT, "ustore.db")


def _days(spec, start="2025-01-01", periods=420):
    """A load_days-shaped frame: every day regular, 100 units, recorded,
    except the (date, kind, units) overrides in `spec` (units None = nothing
    tallied)."""
    idx = pd.date_range(start, periods=periods, freq="D")
    df = pd.DataFrame({"kind": "regular", "closed": False, "units": 100.0, "recorded": True}, index=idx)
    for d, kind, units in spec:
        df.loc[pd.Timestamp(d), "kind"] = kind
        df.loc[pd.Timestamp(d), "units"] = units or 0.0
        df.loc[pd.Timestamp(d), "recorded"] = units is not None
    return df


LIFTS = {"exam": {"days": 69, "lift": 1.5}, "event": {"days": 4, "lift": 2.0},
         "regular": {"days": 300, "lift": 1.0}}


def test_expected_is_the_recent_level_times_the_lift():
    days = _days([("2026-02-20", "exam", 150), ("2026-02-21", "exam", 150)])
    out = ao.expected(days, LIFTS, "2026-02-20", "2026-02-21")
    assert out["kind"] == "exam" and out["lift"] == pytest.approx(1.5)
    assert out["baseline_per_day"] == pytest.approx(100)
    assert out["per_day"] == pytest.approx(150)
    assert out["past_days"] == 69


def test_a_mixed_window_averages_its_lifts_and_an_event_can_be_forced():
    days = _days([("2026-02-20", "exam", 150)])
    out = ao.expected(days, LIFTS, "2026-02-20", "2026-02-21")   # one exam day, one regular
    assert out["lift"] == pytest.approx(1.25)
    forced = ao.expected(days, LIFTS, "2026-02-21", "2026-02-21", kind="event")
    assert forced["kind"] == "event" and forced["per_day"] == pytest.approx(200)
    beyond = ao.expected(days, LIFTS, "2030-01-01", "2030-01-02")   # past Dim_Date: regular
    assert beyond["lift"] == pytest.approx(1.0)


def test_the_level_falls_back_to_every_regular_day():
    # Only the last 5 days of the recent 90 are recorded regular days at 300;
    # too few, so the level is all recorded regular days.
    start = pd.Timestamp("2025-01-01")
    spec = [((start + pd.Timedelta(days=i)).date().isoformat(), "regular", None) for i in range(330, 415)]
    spec += [((start + pd.Timedelta(days=i)).date().isoformat(), "regular", 300) for i in range(415, 420)]
    base = ao.baseline(_days(spec))
    assert base["days"] == 330 + 5
    assert base["per_day"] == pytest.approx((330 * 100 + 5 * 300) / 335)


def test_last_years_window_is_the_nearest_run_once_it_is_over():
    runs = [{"start": "2024-12-09", "end": "2024-12-15"}, {"start": "2025-12-12", "end": "2025-12-18"},
            {"start": "2025-10-09", "end": "2025-10-14"}]
    assert ao.pick_last_year(runs, "2026-12-14", "2026-10-18") == runs[1]
    assert ao.pick_last_year(runs, "2026-12-14", "2025-12-17") is None   # not over yet
    assert ao.pick_last_year(runs[:1], "2026-12-14", "2026-10-18") is None   # two years back
    same = ao.same_dates_last_year("2026-12-14", "2026-12-20", "2026-10-18")
    assert same == {"start": "2025-12-15", "end": "2025-12-21"}   # 364 days: same weekdays


def test_the_chart_series_marks_the_window_and_untallied_days():
    days = _days([("2025-12-12", "exam", 200), ("2025-12-13", "exam", None), ("2025-12-14", "exam", 400)])
    ly = ao.last_year(days, {"start": "2025-12-12", "end": "2025-12-14"})
    assert len(ly["series"]) == 3 + 2 * ao.CONTEXT_DAYS
    inside = [p for p in ly["series"] if p["in_window"]]
    assert [p["units"] for p in inside] == [200, None, 400]
    assert ly["recorded_days"] == 2 and ly["window_days"] == 3
    assert ly["per_day"] == pytest.approx(300)


@pytest.fixture
def real_api(tmp_path, monkeypatch):
    if not os.path.exists(REAL_DB):
        pytest.skip("ustore.db not built")
    db_path = pathlib.Path(tmp_path) / "ustore.db"
    shutil.copy(REAL_DB, db_path)
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(dbmod, "_initialised", False)
    client = backend.app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    return client


def test_calendar_advisories_carry_an_outlook(real_api):
    data = real_api.get("/api/advisories").get_json()
    calendar = [a for a in data["advisories"] if a["type"] in ("enrollment", "exam_week", "event")]
    if not calendar:
        pytest.skip("no upcoming calendar advisory")
    for a in data["advisories"]:
        if a["type"] not in ("enrollment", "exam_week", "event"):
            assert a.get("outlook") is None
    for a in calendar:
        o = a["outlook"]
        assert o is not None and o["per_day"] > 0
        assert o["per_day"] == pytest.approx(o["baseline_per_day"] * o["lift"])
        if o["last_year"]:
            assert o["last_year"]["end"] < a["date_range"][0]
            assert any(p["in_window"] for p in o["last_year"]["series"])
