"""
tests/test_fsn_rules.py
------------------------------------------------------------------
step3's classification rules (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 4.1, 4.2),
on a small hand-built history:

  - an item on the sheets that never sold is Non-moving, like one with no
    rows at all (the manuscript's "no recorded sales");
  - moving it to N leaves the percentile cutoff, and so every other item's
    class, exactly where it was;
  - a high-ADUS item with no sale in the last 90 days is Slow, not Fast;
    one that sold 89 days before the last sale in the data stays Fast.

Plus the scoring helpers of scripts/test_prediction_targets.py.
------------------------------------------------------------------
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import step3_fsn_classification as step3  # noqa: E402
import test_prediction_targets as tpt  # noqa: E402

END = pd.Timestamp("2026-07-31")


def history(spec):
    """{product_id: [(days_before_END, units), ...]} -> load_fact's frame."""
    rows = [dict(product_id=pid, date_id=int((END - pd.Timedelta(days=d)).strftime("%Y%m%d")),
                 calendar_date=END - pd.Timedelta(days=d), quantity_sold=u,
                 imputation_flag=0, is_censored=0)
            for pid, sales in spec.items() for d, u in sales]
    return pd.DataFrame(rows)


def products(ids):
    return pd.DataFrame({"product_id": ids, "item_name": [f"item {i}" for i in ids], "entry_date": None})


def classes(spec, ids, **kw):
    df = step3.classify(products(ids), history(spec), **kw).df
    return dict(zip(df.product_id, df.fsn_class))


# Ten moving items with ADUS 1..10 (one sale a day, recent), so the 80th
# percentile picks out the top ones.
MOVING = {i: [(0, i), (1, i)] for i in range(1, 11)}


def test_on_the_sheet_but_never_sold_is_non_moving():
    spec = {**MOVING, 11: [(0, 0), (5, 0), (9, 0)]}        # 11: three zero rows
    got = classes(spec, list(range(1, 13)))                 # 12: no rows at all
    assert got[11] == "N" and got[12] == "N"


def test_zero_unit_items_do_not_move_the_cutoff():
    with_zero = step3.classify(products(list(range(1, 12))),
                               history({**MOVING, 11: [(0, 0)]})).cutoffs
    # The cutoff is computed over every item with a row, the zero one included,
    # exactly as before item 11 became N.
    expected = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0], dtype=float).quantile(0.8)
    assert with_zero[step3.PRIMARY_THRESHOLD] == expected


def test_no_sale_in_90_days_is_never_fast():
    spec = {**MOVING,
            20: [(95, 500), (96, 500)],     # huge ADUS, last sale 95 days before the end
            21: [(89, 500), (90, 500)]}     # same, but sold 89 days before the end
    got = classes(spec, list(range(1, 11)) + [20, 21])
    assert got[20] == "S"
    assert got[21] == "F"
    assert classes(spec, list(range(1, 11)) + [20, 21], stale_days=180)[20] == "F"


def test_c_index_orders_pairs_and_skips_unknown_ones():
    # Sale after 1, 2, 3 weeks; a perfect prediction scores 1, a reversed one 0.
    t, e = [1, 2, 3], [1, 1, 1]
    assert tpt.c_index(t, e, risk_of_sooner=[3, 2, 1]) == 1.0
    assert tpt.c_index(t, e, risk_of_sooner=[1, 2, 3]) == 0.0
    # An item still waiting at week 1 (censored) cannot be ordered against a later one.
    assert tpt.c_index([1, 2], [0, 1], risk_of_sooner=[1, 2]) != tpt.c_index([1, 2], [1, 1], [1, 2])


def test_wait_weeks_censors_at_the_cut_off():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    daily = pd.DataFrame({"a": 0.0, "b": 0.0}, index=idx)
    daily.loc[idx[10], "a"] = 3                               # a sells on day 10
    weeks, observed = tpt.wait_weeks(daily, 0, 40)
    assert (weeks["a"], observed["a"]) == (2, True)           # day 10 is in week 2
    assert observed["b"] is np.False_ and weeks["b"] == 5     # still waiting after 40 days
