"""
tests/test_calendar_adjust.py
------------------------------------------------------------------
forecasting/calendar_adjust.py: the forecast total may only go DOWN, and only
when the calendar says quieter days are ahead; it must never read past the
origin.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.calendar_adjust import (
    calendar_capped_fit_predict, calendar_multiplier, type_ratios,
)
from forecasting.evaluate import make_folds, walk_forward_evaluate


def flat_model(train, horizon):
    return np.full(horizon, float(np.mean(train[-180:])))


def series_with_quiet_breaks(n=500, normal=10.0, brk=2.0, every=60, length=15):
    """Daily sales of `normal`, dropping to `brk` on break days; returns
    (values, types) with `types` extended 30 days past the series."""
    types = np.array(["normal"] * (n + 30), dtype=object)
    for start in range(0, n + 30, every):
        types[start:start + length] = "break"
    values = np.where(types[:n] == "break", brk, normal).astype(float)
    return values, types


def test_ratios_see_that_break_days_sell_less():
    v, t = series_with_quiet_breaks()
    r = type_ratios(v, t[:v.size])
    assert r["break"] < 1.0 < r["normal"]


def test_multiplier_is_one_when_the_future_looks_like_the_past():
    v = np.full(400, 5.0)
    t = np.array(["normal"] * 430, dtype=object)
    assert calendar_multiplier(v, t, 400, 30) == pytest.approx(1.0)


def test_multiplier_lowers_a_window_full_of_break_days():
    v, t = series_with_quiet_breaks()
    t = t.copy()
    t[v.size:v.size + 30] = "break"           # the whole horizon is a break
    assert calendar_multiplier(v, t, v.size, 30) < 0.7


def test_multiplier_never_raises_the_forecast():
    """After a break-heavy training window, an all-normal horizon would push
    the uncapped factor above 1; the default caps it at exactly 1."""
    v, t = series_with_quiet_breaks(every=20, length=15)   # training mostly breaks
    t = t.copy()
    t[v.size:v.size + 30] = "normal"
    assert calendar_multiplier(v, t, v.size, 30, cap=False) > 1.0
    assert calendar_multiplier(v, t, v.size, 30) == pytest.approx(1.0)


def test_nothing_sold_means_no_adjustment():
    v = np.zeros(300)
    t = np.array(["break"] * 330, dtype=object)
    assert calendar_multiplier(v, t, 300, 30) == 1.0


def test_types_must_cover_the_forecast_window():
    with pytest.raises(ValueError):
        calendar_multiplier(np.ones(100), np.array(["normal"] * 110, dtype=object), 100, 30)


def test_wrapped_model_only_scales_the_base_forecast():
    v, t = series_with_quiet_breaks()
    f = calendar_capped_fit_predict(flat_model, t)
    base = flat_model(v, 30)
    out = f(v, 30)
    assert out.shape == base.shape
    assert np.all(out <= base + 1e-12)
    assert np.allclose(out / base, out[0] / base[0])      # one factor for the whole window


def test_wrapped_model_cannot_see_its_test_window():
    """Change every value AFTER a fold's origin: the fold's prediction must not move."""
    v, t = series_with_quiet_breaks()
    f = calendar_capped_fit_predict(flat_model, t)
    folds = make_folds(v.size, 30, 3, 12, 60)
    a = walk_forward_evaluate("s", v, f, "m", folds=folds).rows
    for fold, row in zip(folds, a):
        v2 = v.copy()
        v2[fold.train_end:] = 999.0
        b = walk_forward_evaluate("s", v2, f, "m", folds=[fold]).rows[0]
        assert b["pred_30d"] == pytest.approx(row["pred_30d"])


def test_item_ratios_come_from_the_category_not_the_item():
    """With cat_values given, the item's own (sparse) series must not decide
    the factor: two items with different sales in the same category get the
    same multiplier."""
    cat, t = series_with_quiet_breaks()
    t = t.copy()
    t[cat.size:cat.size + 30] = "break"
    item_a = np.zeros(cat.size); item_a[::7] = 3.0
    item_b = np.zeros(cat.size); item_b[::3] = 1.0
    fa = calendar_capped_fit_predict(flat_model, t, cat_values=cat)
    fb = calendar_capped_fit_predict(flat_model, t, cat_values=cat)
    ra = fa(item_a, 30)[0] / flat_model(item_a, 30)[0]
    rb = fb(item_b, 30)[0] / flat_model(item_b, 30)[0]
    assert ra == pytest.approx(rb)
