"""
tests/test_season_ahead.py
------------------------------------------------------------------
Ordering ahead of a busy period (backend/season_ahead.py):

  - upcoming windows come from the school calendar, and past its last day
    from last year's runs a year on, marked estimated;
  - the projection sells the usual rate, the lift on window days, reorders
    up to the order-up-to level at the reorder point with the lead time;
  - an extra rush order is what keeps stock at its safety stock through the
    rush, ordered a lead time before it; none when regular reorders cover it;
  - /api/reorder/season and the busy months on an item's movement.
------------------------------------------------------------------
"""
import os
import pathlib
import shutil
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import app as backend  # noqa: E402
import db as dbmod  # noqa: E402
import season_ahead as sa  # noqa: E402

REAL_DB = os.path.join(ROOT, "ustore.db")
LIFTS = {"enrollment": {"days": 13, "lift": 1.5}, "term_start": {"days": 18, "lift": 2.0}}
RUNS = {"enrollment": [("2026-01-12", "2026-01-16"), ("2026-06-06", "2026-06-09")],
        "term_start": [("2026-08-01", "2026-08-14")]}


def test_windows_past_the_calendar_are_last_years_a_year_on():
    ws = sa.upcoming_windows(RUNS, "2026-11-11", "2026-12-31", LIFTS)
    assert [(w["kind"], w["start"], w["end"], w["estimated"]) for w in ws] == [
        ("enrollment", "2027-01-12", "2027-01-16", True),
        ("enrollment", "2027-06-06", "2027-06-09", True),
        ("term_start", "2027-08-01", "2027-08-14", True),
    ]
    assert ws[0]["from_start"] == "2026-01-12" and ws[0]["days"] == 5 and ws[0]["lift"] == 1.5


def test_a_published_window_is_not_estimated_and_not_doubled():
    runs = {**RUNS, "enrollment": RUNS["enrollment"] + [("2027-01-11", "2027-01-15")]}
    ws = sa.upcoming_windows(runs, "2026-11-11", "2027-03-31", LIFTS)
    jan = [w for w in ws if w["start"].startswith("2027-01")]
    assert [(w["start"], w["estimated"]) for w in jan] == [("2027-01-11", False)]
    # A window under way is still listed; one beyond the horizon is not.
    assert sa.upcoming_windows(RUNS, "2026-01-14", "2026-12-31", LIFTS, horizon=10)[0]["start"] == "2026-01-12"


def test_the_projection_reorders_at_the_reorder_point():
    # 10 a day, lead time 5, reorder point 50, up to 150. From 100: reaches 50
    # on day 5 (after its sales), orders 100, which lands on day 10.
    t, s, e = date(2026, 1, 1), date(2026, 1, 20), date(2026, 1, 21)
    reorders, low, at_start = sa.project(100, 10, 5, 50, 150, t, s, e, lift=1.0)
    assert reorders[0] == (date(2026, 1, 5), 100, date(2026, 1, 10))
    assert at_start is not None and low >= 0


def test_extra_only_when_the_rush_breaks_the_safety_stock():
    item = {"product_id": 1, "item_name": "Lanyard", "avg_daily_demand": 10.0, "lead_time_days": 5,
            "safety_stock": 20.0, "reorder_point": 70.0, "order_up_to_level": 370.0,
            "current_stock": 300, "stock_as_of": "2026-10", "policy_state": "priced"}
    calm = {"start": "2026-11-20", "end": "2026-11-22", "lift": 1.0}
    rush = {"start": "2026-11-20", "end": "2026-11-29", "lift": 3.0}
    p = sa.plan_item(item, calm, "2026-11-11")
    assert p["status"] in ("covered", "reorder") and p["rush_order"] == 0
    p = sa.plan_item(item, rush, "2026-11-11")
    assert p["status"] == "rush_by" and p["rush_order"] == pytest.approx(item["safety_stock"] - p["lowest_in_window"], abs=1)
    assert p["order_by"] == "2026-11-15"                    # start - lead time
    # Too late to land before it: order now.
    assert sa.plan_item(item, rush, "2026-11-17")["status"] == "rush_now"
    # No count, nothing to project; no rate, no plan.
    assert sa.plan_item({**item, "current_stock": None}, rush, "2026-11-11")["status"] == "uncounted"
    assert sa.plan_item({**item, "avg_daily_demand": 0}, rush, "2026-11-11") is None


def test_event_tags():
    assert sa.classify([3.9, 4.2]) == "rush"
    assert sa.classify([2.7]) == "mixed"                  # one year is not enough to call a rush
    assert sa.classify([0.3, 0.66]) == "quieter"
    assert sa.classify([0.47, 0.73, 1.86]) == "mixed"
    assert sa.classify([None, None]) == "unknown" and sa.classify([]) == "unknown"


def _days_frame(spec):
    import pandas as pd
    idx = pd.date_range("2024-01-01", "2026-11-10", freq="D")
    df = pd.DataFrame({"kind": "regular", "closed": False, "units": 100.0, "recorded": True}, index=idx)
    for d, units in spec.items():
        df.loc[pd.Timestamp(d), "kind"] = "event"         # so a regular day stays 100
        df.loc[pd.Timestamp(d), "units"] = units or 0.0
        df.loc[pd.Timestamp(d), "recorded"] = units is not None
    return df


def test_events_are_matched_with_their_earlier_years():
    days = _days_frame({"2024-12-19": 1250, "2024-12-20": 410, "2025-12-18": 890, "2025-12-19": None,
                        "2025-01-27": 30, "2025-01-28": 30, "2026-01-27": 60, "2026-01-28": 70})
    events = [
        {"name": "Paskuhan Festivities", "start": "2024-12-20", "end": "2024-12-20"},
        {"name": "Paskuhan Festivities", "start": "2025-12-19", "end": "2025-12-19"},
        {"name": "Paskuhan Festivities", "start": "2026-12-18", "end": "2026-12-18"},
        {"name": "Feast of St. Thomas Aquinas", "start": "2025-01-28", "end": "2025-01-28"},
        {"name": "Feast of St. Thomas Aquinas", "start": "2026-01-28", "end": "2026-01-28"},
        # Same date, nothing in common with Paskuhan: not its history.
        {"name": "Christmas Bazaar", "start": "2025-12-18", "end": "2025-12-18"},
    ]
    ev = {e["name"]: e for e in sa.upcoming_events(events, days, "2026-11-11", "2026-12-31")}
    pas = ev["Paskuhan Festivities"]
    assert not pas["estimated"] and pas["tag"] == "rush"
    assert [y["start"] for y in pas["years"]] == ["2024-12-20", "2025-12-19"]
    assert pas["years"][0]["value"] == pytest.approx((1250 + 410) / 2 / 100)   # the eve counts
    assert pas["years"][1]["value"] == pytest.approx(8.9)                      # eve only, the day untallied
    feast = ev["Feast of St. Thomas Aquinas"]
    assert feast["estimated"] and feast["start"] == "2027-01-28" and feast["tag"] == "quieter"


def test_only_rush_events_outside_a_busy_window_are_planned():
    events = [{"name": "Paskuhan", "tag": "rush", "start": "2026-12-18", "end": "2026-12-18", "estimated": False,
               "lift": 4.0, "past_days": 3},
              {"name": "Orientation", "tag": "rush", "start": "2027-08-01", "end": "2027-08-06", "estimated": True,
               "lift": 2.3, "past_days": 9},
              {"name": "Feast", "tag": "quieter", "start": "2027-01-28", "end": "2027-01-28", "estimated": True,
               "lift": 0.5, "past_days": 2}]
    busy = [{"label": "Start of the first term", "start": "2027-08-01", "end": "2027-08-14"}]
    ws = sa.event_windows(events, busy)
    assert [(w["label"], w["start"], w["end"], w["lift"]) for w in ws] == [("Paskuhan", "2026-12-17", "2026-12-18", 4.0)]
    assert events[1]["part_of"] == "Start of the first term"


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


def test_season_endpoint_and_busy_months(real_api):
    data = real_api.get("/api/reorder/season").get_json()
    if not data.get("available"):
        pytest.skip("step5 has not run")
    reorder = {i["product_id"]: i for i in real_api.get("/api/reorder").get_json()["data"]["items"]}
    for e in data["events"]:
        assert e["tag"] in ("rush", "mixed", "quieter", "unknown")
        planned = [w for w in data["windows"] if w["kind"] == "event" and w["label"] == e["name"]]
        assert bool(planned) == (e["tag"] == "rush" and not e.get("part_of"))
    for w in data["windows"]:
        assert w["start"] >= data["today"] or w["end"] >= data["today"]
        assert w["estimated"] == (w["start"] > data["calendar_end"])
        assert w["n_rush"] == sum(1 for p in w["items"] if p["status"] in ("rush_now", "rush_by"))
        for p in w["items"]:
            assert p["reorder_point"] == reorder[p["product_id"]]["reorder_point"]
            if p["status"] == "uncounted":
                assert p["current_stock"] is None
    some = next(iter(reorder))
    months = real_api.get(f"/api/products/{some}/movement").get_json()["months"]
    busy = {m["month"]: m["busy"] for m in months if m["busy"]}
    assert all(k in ("enrollment", "term_start") for k in busy.values())
    if any(m["month"] == "2026-08" for m in months):
        assert busy.get("2026-08") == "term_start"
