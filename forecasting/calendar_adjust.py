"""
forecasting/calendar_adjust.py
------------------------------------------------------------------
Lower a 30-day forecast TOTAL when the school calendar says quieter days are
coming. Never raise it.

A trailing average (or the item blend) learns one per-day rate from its
training window. That window mixes ordinary days with semester-break, exam and
enrollment days, but the next 30 days can hold a very different mix: the
window 2025-12-11..2026-01-09 had 21 semester-break days, and the 6-month
average over-forecast the categories there by 75%. The calendar that says so is
published in advance (Dim_Date, filled from data/calendar_ranges.csv), so a
forecast made today can use it without looking ahead.

    day type        enrollment > semester break > exam week > normal, from Dim_Date
    ratio(type)     mean daily sales on days of that type / mean daily sales,
                    over the last RATIO_WINDOW days of the CATEGORY's own history,
                    shrunk toward 1 by SHRINK_K pseudo-days so a type seen on
                    only a few days cannot swing the forecast
    multiplier      mean ratio over the next 30 days
                    / mean ratio over the base model's LEVEL_WINDOW training days
    forecast        base forecast x min(1, multiplier)

Why only down: the uncapped version raised forecasts after a break (the level
had been learned over quiet days, so it assumed a rebound) and was badly wrong
in February and April 2026, when sales stayed low for reasons the calendar does
not carry. Knowing a break is coming is reliable; assuming a rebound is not.
That rule was chosen after seeing those two windows, so its measured gain may
be a little optimistic - see scripts/test_calendar_adjustment.py.

Store closures (is_store_closed) are deliberately NOT used: some are recorded
after the fact (typhoons), which a real forecast could not know. Leaving them
out changed the result by less than 0.01 MASE.

Ratios come from the CATEGORY series even for an item: an item's own days are
mostly zeros and cannot support a ratio per day type.
------------------------------------------------------------------
"""
import numpy as np
import pandas as pd

__all__ = ["DAY_TYPES", "load_day_types", "type_ratios", "calendar_multiplier",
           "calendar_capped_fit_predict", "RATIO_WINDOW", "LEVEL_WINDOW", "SHRINK_K"]

DAY_TYPES = ("enrol", "break", "exam", "normal")
RATIO_WINDOW = 365      # days of category history the ratios are measured on
LEVEL_WINDOW = 180      # days the base model's level is treated as averaged over
SHRINK_K = 10           # pseudo-days pulling each ratio toward 1


def load_day_types(con, index, horizon):
    """Day type for every date in `index` plus `horizon` days past its end,
    from Dim_Date. A date with no Dim_Date row is 'normal'."""
    cal = pd.read_sql_query(
        "SELECT calendar_date, is_enrollment_period, is_sem_break, is_exam_week FROM Dim_Date",
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    idx = pd.DatetimeIndex(index)
    ext = idx.append(pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=horizon, freq="D"))
    c = cal.reindex(ext).fillna(0.0)
    return np.select([c["is_enrollment_period"] > 0, c["is_sem_break"] > 0, c["is_exam_week"] > 0],
                     ["enrol", "break", "exam"], "normal")


def type_ratios(cat_train, types_train, window=RATIO_WINDOW, k=SHRINK_K):
    """{day type: shrunk ratio of its mean daily sales to the overall mean},
    over the last `window` days. Empty when the window sold nothing."""
    y = np.asarray(cat_train, dtype=float)[-window:]
    t = np.asarray(types_train)[-window:]
    m = y.mean() if y.size else 0.0
    if m <= 0:
        return {}
    out = {}
    for kind in DAY_TYPES:
        sel = t == kind
        n = int(sel.sum())
        raw = y[sel].mean() / m if n else 1.0
        out[kind] = (n * raw + k) / (n + k)
    return out


def calendar_multiplier(cat_train, types, n, horizon, level_window=LEVEL_WINDOW, cap=True):
    """Factor for a forecast made after `n` days of history, for days
    [n, n + horizon). `types` must cover at least n + horizon days. 1.0 when
    there is nothing to go on. Capped at 1 unless cap=False (kept only so the
    test script can report the uncapped version)."""
    types = np.asarray(types)
    if types.size < n + horizon:
        raise ValueError("day types do not cover the forecast window")
    r = type_ratios(np.asarray(cat_train, dtype=float)[:n], types[:n])
    if not r:
        return 1.0
    denom = np.mean([r[x] for x in types[max(0, n - level_window):n]])
    if denom <= 0:
        return 1.0
    mult = float(np.mean([r[x] for x in types[n:n + horizon]]) / denom)
    return min(1.0, mult) if cap else mult


def calendar_capped_fit_predict(base_fn, types, cat_values=None,
                                level_window=LEVEL_WINDOW, cap=True):
    """Wrap a `fit_predict(train, horizon)` so its forecast is scaled by
    calendar_multiplier. `types` is aligned to the shared daily index (and runs
    at least `horizon` days past it); `cat_values` is the category series on the
    same index, or None to measure the ratios on the training slice itself (a
    category forecasting its own total). Only data before the origin is read, so
    the walk-forward harness stays leak-free."""
    cat = None if cat_values is None else np.asarray(cat_values, dtype=float)

    def _f(train, horizon):
        t = np.asarray(train, dtype=float).ravel()
        n = t.size
        base = np.asarray(base_fn(t, horizon), dtype=float)
        src = t if cat is None else cat[:n]
        return np.maximum(base * calendar_multiplier(src, types, n, horizon, level_window, cap), 0.0)
    _f.__name__ = "calendar_capped(" + getattr(base_fn, "__name__", "base") + ")"
    return _f
