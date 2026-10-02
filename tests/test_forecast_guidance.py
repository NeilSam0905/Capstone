"""
tests/test_forecast_guidance.py
------------------------------------------------------------------
The plain-language reading under the Demand Forecast chart, and the
calendar windows the Upcoming Event Advisories are built from
(backend/app.py _date_runs, _item_stock_advice, _category_stock_advice):

  - flagged dates group into one window per unbroken stretch, so two exam
    periods months apart stay two advisories, not one "10/09 to 12/20";
  - each stock position maps to the one action it calls for, with dates
    projected from today, never from a forecast window already gone;
  - a category's action names the items at their reorder point, using the
    same Reorder Alerts numbers.
------------------------------------------------------------------
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402  (backend/app.py)

TODAY = date(2026, 10, 2)


def test_exam_periods_months_apart_are_separate_windows():
    midterms = [f"2026-10-{d:02d}" for d in range(9, 15)]
    finals = [f"2026-12-{d:02d}" for d in range(14, 21)]
    assert backend._date_runs(finals + midterms) == [
        {"start": "2026-10-09", "end": "2026-10-14", "days": 6},
        {"start": "2026-12-14", "end": "2026-12-20", "days": 7},
    ]


def test_a_weekend_inside_a_window_does_not_split_it():
    assert backend._date_runs(["2026-10-09", "2026-10-12"]) == [
        {"start": "2026-10-09", "end": "2026-10-12", "days": 2}]


def reorder_row(needs, rop, qty=0):
    return {7: {"product_id": 7, "item_name": "Lanyard", "supplier_name": "VL", "current_stock": None,
                "reorder_point": rop, "needs_reorder": needs, "suggested_order_qty": qty}}


def advise(stock, reorder, rate=2.0, total=60.0, high=90.0):
    stats = {7: {"current_stock": stock, "stock_as_of": "2026-09"}} if stock is not None else {}
    return backend._item_stock_advice(7, stats, reorder, rate, total, high, TODAY)


def test_at_the_reorder_point_says_reorder_now_with_the_reorder_alerts_quantity():
    out, action = advise(10, reorder_row(True, 20, qty=70))
    assert action == "reorder_now"
    assert out["suggested_order_qty"] == 70


def test_above_the_reorder_point_gives_an_order_by_date_from_today():
    out, action = advise(40, reorder_row(False, 20))
    assert action == "order_by"
    assert out["order_by"] == "2026-10-12"         # (40 - 20) / 2 a day = 10 days from today
    assert out["runs_out_on"] == "2026-10-22"


def test_no_reorder_point_but_short_of_the_month():
    out, action = advise(30, {})
    assert action == "short"
    assert out["to_cover_30d"] == 30 and out["to_cover_30d_high"] == 60


def test_covers_expected_but_not_a_busy_month():
    assert advise(70, {})[1] == "watch"


def test_covered_even_at_the_high_end():
    assert advise(200, reorder_row(False, 20))[1] == "covered"


def test_no_count_and_no_demand():
    assert advise(None, reorder_row(False, 20))[1] == "count_stock"
    assert advise(50, {}, rate=0.0, total=0.2, high=3.0)[1] == "no_demand"


def test_category_names_the_items_at_their_reorder_point():
    reorder = {
        1: {"product_id": 1, "item_name": "A", "supplier_name": "S", "current_stock": 0,
            "reorder_point": 5, "needs_reorder": True, "suggested_order_qty": 12},
        2: {"product_id": 2, "item_name": "B", "supplier_name": "S", "current_stock": 3,
            "reorder_point": 9, "needs_reorder": True, "suggested_order_qty": 30},
        3: {"product_id": 3, "item_name": "C", "supplier_name": "S", "current_stock": 50,
            "reorder_point": 9, "needs_reorder": False, "suggested_order_qty": 0},
    }
    stats = {1: {"current_stock": 0}, 2: {"current_stock": 3}, 3: {"current_stock": 50}}
    out, action = backend._category_stock_advice([1, 2, 3, 4], stats, reorder, total=100)
    assert action == "reorder_items"
    assert [r["item_name"] for r in out["reorder_now"]] == ["B", "A"]      # biggest order first
    assert (out["reorder_now_total"], out["reorder_units_total"]) == (2, 42)
    assert (out["items_counted"], out["items_total"]) == (3, 4)

    assert backend._category_stock_advice([3], stats, reorder, total=100)[1] == "covered"
    assert backend._category_stock_advice([4], stats, reorder, total=100)[1] == "count_stock"
