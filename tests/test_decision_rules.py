"""
tests/test_decision_rules.py
------------------------------------------------------------------
The FSN decision rules (backend/decision_rules.py, manuscript Sec. 3.3.1),
on a small hand-built database:

  - Fast -> keep in stock, Slow -> promote, Non-moving -> clear out, and a
    store-discontinued item reads as discontinued whatever its class;
  - a Slow item is escalated to "Review for Discontinuation" only when it is
    under the threshold in BOTH of the last two regular semesters;
  - the special term (ST) and a semester still in progress are not judged;
  - an item absent from a semester's sheets counts as 0 only if it was
    carried before that semester began;
  - a bad ?threshold= falls back to the default.

Plus the advisory window helper in backend/app.py.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import decision_rules as dr  # noqa: E402

SEMESTERS = [  # (semester_id, first day, last day)
    ("AY2425-T2", "2025-01-09", "2025-06-05"),
    ("AY2425-ST", "2025-06-06", "2025-07-31"),
    ("AY2526-T1", "2025-08-01", "2026-01-08"),
    ("AY2526-T2", "2026-01-09", "2026-06-05"),
    ("AY2526-ST", "2026-06-06", "2026-07-31"),
    ("AY2627-T1", "2026-08-01", "2026-12-31"),
]


def database(products, sales, last_day="2026-07-31"):
    """products: [(id, class)]; sales: [(id, date, units)]. One Dim_Date row
    per semester boundary and per sale date is all the queries need."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT, semester_id TEXT);
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT, fsn_class TEXT);
        CREATE TABLE Fact_Sales (product_id INTEGER, date_id INTEGER, quantity_sold INTEGER,
                                 is_censored INTEGER DEFAULT 0, imputation_flag INTEGER DEFAULT 0,
                                 transaction_type TEXT);
        CREATE TABLE Product_Status (item_name TEXT, discontinued INTEGER);
    """)
    days = {}

    def date_id(day):
        if day not in days:
            sem = next((s for s, lo, hi in SEMESTERS if lo <= day <= hi), None)
            days[day] = len(days) + 1
            c.execute("INSERT INTO Dim_Date VALUES (?, ?, ?)", (days[day], day, sem))
        return days[day]

    for _, lo, hi in SEMESTERS:
        date_id(lo)
        date_id(hi)
    for pid, cls in products:
        c.execute("INSERT INTO Dim_Product VALUES (?, ?, ?)", (pid, f"item {pid}", cls))
    for pid, day, units in sales:
        c.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold) VALUES (?, ?, ?)",
                  (pid, date_id(day), units))
    # The last day anything was tallied, on a product no rule reads.
    c.execute("INSERT INTO Dim_Product VALUES (999, 'marker', 'F')")
    c.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold) VALUES (999, ?, 1)",
              (date_id(last_day),))
    return c


def actions(c, **kw):
    return {i["product_id"]: i["action"] for i in dr.evaluate(c, **kw)["items"]}


def ten_days(month, units):
    """Ten sheet days in `month` ('YYYY-MM'), `units` sold on the first."""
    return [(f"{month}-{d:02d}", units if d == 10 else 0) for d in range(10, 20)]


def slow(pid, t1_units, t2_units, carried_from="2025-02"):
    """A Slow item on the sheet before AY2526, then in each of its terms."""
    rows = [(pid, day, u) for day, u in ten_days(carried_from, 5)]
    if t1_units is not None:
        rows += [(pid, day, u) for day, u in ten_days("2025-09", t1_units)]
    if t2_units is not None:
        rows += [(pid, day, u) for day, u in ten_days("2026-02", t2_units)]
    return rows


def test_each_class_gets_its_rule():
    c = database([(1, "F"), (2, "S"), (3, "N")],
                 slow(1, 50, 50) + slow(2, 5, 5))
    got = actions(c)
    assert got[1] == dr.KEEP_STOCKED
    assert got[2] == dr.PROMOTE          # 5 units over 10 days = 0.5 a day, above 0.1
    assert got[3] == dr.CLEAR_OUT


def test_judged_on_the_last_two_complete_regular_semesters():
    c = database([(1, "S")], [])
    ids = [s["semester_id"] for s in dr.review_semesters(c)]
    # Not AY2526-ST (special term), not AY2627-T1 (ends after the last tally).
    assert ids == ["AY2526-T1", "AY2526-T2"]


def test_slow_in_both_semesters_is_reviewed():
    c = database([(1, "S")], slow(1, 0, 0))
    assert actions(c)[1] == dr.REVIEW


def test_slow_in_only_one_semester_is_still_promoted():
    c = database([(1, "S"), (2, "S")], slow(1, 0, 5) + slow(2, 5, 0))
    got = actions(c)
    assert got[1] == dr.PROMOTE and got[2] == dr.PROMOTE


def test_the_threshold_moves_the_line():
    c = database([(1, "S")], slow(1, 2, 2))      # 0.2 a day in each semester
    assert actions(c)[1] == dr.PROMOTE
    assert actions(c, threshold=0.25)[1] == dr.REVIEW


def test_carried_but_off_the_sheet_counts_as_zero():
    c = database([(1, "S")], slow(1, None, None))  # on the sheet in 2025-02 only
    assert actions(c)[1] == dr.REVIEW


def test_new_item_is_not_judged_on_a_semester_before_it_existed():
    # First seen in AY2526-T2: the T1 reading is not measurable, so no review.
    c = database([(1, "S")], [(1, day, u) for day, u in ten_days("2026-02", 0)])
    item = dr.evaluate(c)["items"][0]
    assert item["action"] == dr.PROMOTE
    assert [r["measurable"] for r in item["semesters"]] == [False, True]


def test_store_discontinued_wins_over_the_class():
    c = database([(1, "S"), (2, "N")], slow(1, 0, 0))
    c.execute("INSERT INTO Product_Status VALUES ('item 1', 1)")
    c.execute("INSERT INTO Product_Status VALUES ('item 2', 1)")
    got = actions(c)
    assert got[1] == dr.DISCONTINUED and got[2] == dr.DISCONTINUED


def test_summary_counts_every_rule():
    c = database([(1, "F"), (2, "S"), (3, "S"), (4, "N")],
                 slow(1, 50, 50) + slow(2, 5, 5) + slow(3, 0, 0))
    summary = dr.evaluate(c)["summary"]
    assert summary[dr.KEEP_STOCKED] == 2       # item 1 and the last-day marker
    assert summary[dr.PROMOTE] == 1
    assert summary[dr.REVIEW] == 1
    assert summary[dr.CLEAR_OUT] == 1


def test_bad_thresholds_fall_back_to_the_default():
    for raw in (None, "", "abc", "-1", "nan", "inf", "5000"):
        assert dr.parse_threshold(raw) == dr.DEFAULT_REVIEW_THRESHOLD
    assert dr.parse_threshold("0.25") == 0.25
    assert dr.parse_threshold("0") == 0.0


def test_promotion_window_is_the_earliest_of_enrollment_and_events():
    import app as backend
    enrollment = [{"start": "2026-11-20", "end": "2026-11-28", "days": 9}]
    events = [{"event_name": "Paskuhan", "event_date": "2026-11-15"}]
    assert backend._next_high_traffic(enrollment, events) == ("Paskuhan", "2026-11-15", "2026-11-15")
    assert backend._next_high_traffic(enrollment, []) == ("enrollment", "2026-11-20", "2026-11-28")
    assert backend._next_high_traffic([], []) is None
