"""
forecasting/shape.py
------------------------------------------------------------------
How a 30-day TOTAL is spread across the 30 days.

A trailing average or TSB has no idea about dates, so on its own it draws a
flat line. The 30-day total is the part that is validated (it is what
`forecasting/evaluate.py` scores); this module decides only the DAY-BY-DAY
split, using the published school calendar (Dim_Date) and the weekly rhythm.
The weights always sum to 1, so multiplying them by a total never changes
that total.

Shared by scripts/step4c_category_forecast.py (category forecasts) and
scripts/step4_forecast_model.py (item forecasts, which borrow their CATEGORY's
shape because a single item's daily series is too sparse to carry one; see
scripts/test_item_forecast_methods.py).

  prophet   Prophet with weekly seasonality and the Dim_Date flags (enrollment,
            exam week, event day, semester break, store closed) as regressors.
            Only its SHAPE is used.
  weekday   average by weekday over the last year, zero on published closures.
            Pure pandas; the fallback if Prophet is missing or a fit fails.
  flat      an even split.

`day_shape` falls back prophet -> weekday -> flat and says which it used, so a
stored row never claims a shape it did not get.
------------------------------------------------------------------
"""
import numpy as np
import pandas as pd

__all__ = ["CAL_COLS", "load_calendar", "weekday_shape", "prophet_shape", "day_shape"]

# semester_week is left out on purpose: Divergence #8 records that its
# continuous form extrapolates badly across a term reset.
CAL_COLS = ["is_enrollment_period", "is_exam_week", "is_event_day",
            "is_sem_break", "is_store_closed"]


def load_calendar(con):
    """Dim_Date's published-calendar flags, indexed by date. Known in advance
    for every future date (Dim_Date runs to the end of 2026), so using them for
    the forecast window is not look-ahead."""
    cal = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CAL_COLS), con,
        parse_dates=["calendar_date"]).set_index("calendar_date")
    return cal.astype(float)


def weekday_shape(v, hist_idx, cal, dates):
    """Average by weekday over the last 365 days (published-closure days left
    out of the average), zero on published closures in the forecast window."""
    lo = max(0, len(v) - 365)
    y, d = v[lo:], hist_idx[lo:].dayofweek.to_numpy()
    c = cal["is_store_closed"].reindex(hist_idx[lo:]).fillna(0.0).to_numpy()
    prof = np.array([y[(d == k) & (c == 0)].mean() if ((d == k) & (c == 0)).any()
                     else y.mean() for k in range(7)])
    fclosed = cal["is_store_closed"].reindex(dates).fillna(0.0).to_numpy()
    return prof[dates.dayofweek.to_numpy()] * (1 - fclosed)


def prophet_shape(v, hist_idx, cal, dates):
    """Prophet's daily forecast for `dates`: weekly seasonality plus the
    calendar flags as regressors (a flag constant inside the training history
    carries no information and is dropped). Same configuration the shape tests
    scored as 'Prophet: weekly + calendar (no yearly)'. Unlike
    forecasting/prophet_model.py, the FUTURE flags are looked up in Dim_Date
    rather than zero-filled."""
    import logging
    import warnings
    for name in ("cmdstanpy", "prophet"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.disabled = True
    warnings.filterwarnings("ignore")
    from prophet import Prophet

    hist = pd.DataFrame({"ds": hist_idx, "y": v})
    hcal = cal.reindex(hist_idx).fillna(0.0)
    fcal = cal.reindex(dates).fillna(0.0)
    m = Prophet(weekly_seasonality=True, yearly_seasonality=False,
                daily_seasonality=False, uncertainty_samples=0)
    used = [c for c in CAL_COLS if hcal[c].nunique() > 1]
    for c in used:
        m.add_regressor(c)
        hist[c] = hcal[c].to_numpy()
    m.fit(hist)
    fut = pd.DataFrame({"ds": dates})
    for c in used:
        fut[c] = fcal[c].to_numpy()
    return m.predict(fut)["yhat"].to_numpy()


def day_shape(kind, v, hist_idx, cal, dates):
    """(weights summing to 1, shape actually used). Falls back prophet ->
    weekday -> flat, and says so through the returned label rather than
    quietly, so a row never claims a shape it did not get."""
    flat = np.full(len(dates), 1.0 / len(dates))
    p = None
    used = "flat"
    if kind == "prophet":
        try:
            p, used = prophet_shape(v, hist_idx, cal, dates), "prophet"
        except Exception as exc:                        # missing package or a failed Stan fit
            print("  NOTE: Prophet shape unavailable (%s) - using the weekday pattern"
                  % str(exc)[:70])
            kind = "weekday"
    if kind == "weekday":
        p, used = weekday_shape(v, hist_idx, cal, dates), "weekday"
    if p is None:
        return flat, "flat"
    p = np.maximum(np.asarray(p, dtype=float), 0.0)
    if not np.isfinite(p).all() or p.sum() <= 0:
        return flat, "flat"
    return p / p.sum(), used
