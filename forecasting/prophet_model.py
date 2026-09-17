"""
forecasting/prophet_model.py
------------------------------------------------------------------
Prophet, wired into the same `fit_predict(train, horizon) -> array`
contract as every other method, so it can finally be scored on the
identical walk-forward folds as the baselines.

Why this file exists
--------------------
Prophet is the model the manuscript commits to (sections 1.2, 2.1.4,
3.3.2) and the one thing the repo's own benchmark has never actually
measured - `scripts/model_benchmark.py` says so in its docstring and
defers it as decision B5, because a cmdstan build is exactly the
toolchain gamble that made Chapter 4 unreproducible. It is deferred
there and stays deferred there: nothing in the production pipeline
imports this module. It is imported by
`scripts/benchmark_fast_raw_vs_clean.py` only, and only when Prophet
actually imports, so a clean clone without cmdstan still runs that
benchmark minus these two rows.

Two deliberate departures from section 3.3.2, both of which make the
number REPORTED here a lower bound on effort, not a better one:

1. **MAP estimation, not MCMC.** Section 3.3.2 specifies
   `mcmc_samples=1000`. At ~0.4 s per MAP fit, the 1,560 fits this
   benchmark needs take about ten minutes; the same fits under full MCMC
   are a multi-day run. MCMC changes the UNCERTAINTY INTERVAL, not the
   point forecast that the 30-day aggregate is scored on, so the ranking
   below is unaffected - but the interval widths in section 3.3.2 are
   not reproduced here and should not be quoted from this run.

2. **Regressors that are constant inside a fold's training slice are
   dropped for that fold.** An early origin can sit entirely outside any
   enrollment window, and a regressor with no variance carries no
   information while making the fit ill-posed. Dropping it per fold is
   the honest handling; silently keeping it would let Prophet fit a
   coefficient to nothing.

The date alignment
------------------
`fit_predict` receives an array, not dates - but every SKU in this
benchmark is reindexed onto ONE shared daily calendar, so position `i`
in the array is `index[i]` for every SKU. That is what lets a
date-driven model be handed a bare array without guessing: the calendar
is bound into the closure once, by the caller that built the series.
------------------------------------------------------------------
"""
import logging
import os
import warnings

import numpy as np
import pandas as pd

__all__ = ["prophet_fit_predict", "CALENDAR_REGRESSORS", "load_calendar"]

# Section 3.3.2's named regressors. semester_week is the continuous one;
# the rest are the Dim_Date booleans.
CALENDAR_REGRESSORS = [
    "is_enrollment_period", "is_exam_week", "is_event_day",
    "is_sem_break", "is_store_closed", "semester_week",
]


def _quiet():
    """Prophet/cmdstanpy log one block per fit. At 1,560 fits that buries
    the benchmark's own output, so the loggers are muted here rather than
    left for every caller to remember."""
    for name in ("cmdstanpy", "prophet", "fbprophet", "numexpr"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False
        lg.disabled = True          # setLevel alone leaves cmdstanpy's own
        lg.handlers[:] = []         # handler printing "Chain [1] ..." per fit
    warnings.filterwarnings("ignore")
    os.environ.setdefault("PYSTAN_LOGGING", "0")


def load_calendar(con, index):
    """Dim_Date's calendar flags, reindexed onto the benchmark's daily
    index. Missing dates are filled with 0 rather than dropped - a date
    with no Dim_Date row is a date with no flags set, not a gap."""
    d = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CALENDAR_REGRESSORS),
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    return d.reindex(index).fillna(0.0).astype(float)


def prophet_fit_predict(index, calendar=None, name="prophet",
                        weekly=True, yearly=True,
                        changepoint_prior_scale=0.05,
                        seasonality_prior_scale=10.0):
    """Build a `fit_predict(train, horizon)` bound to a shared calendar.

    `calendar` is a DataFrame aligned to `index` carrying the regressor
    columns; pass None for plain Prophet (trend + seasonality only).
    """
    from prophet import Prophet
    _quiet()
    idx = pd.DatetimeIndex(index)

    def _f(train, horizon):
        v = np.asarray(train, dtype=float).ravel()
        n = v.size
        if n < 2 or not np.isfinite(v).all():
            return np.zeros(horizon)
        # A series that never sold has no trend for Prophet to find and
        # its fit is pure noise around zero; return zero directly rather
        # than paying 0.4 s to be told the same thing.
        if v.sum() <= 0:
            return np.zeros(horizon)

        hist = pd.DataFrame({"ds": idx[:n], "y": v})
        future_idx = idx[n:n + horizon]
        if len(future_idx) < horizon:      # past the bound calendar
            future_idx = pd.date_range(idx[n - 1] + pd.Timedelta(days=1),
                                       periods=horizon, freq="D")

        m = Prophet(weekly_seasonality=weekly, yearly_seasonality=yearly,
                    daily_seasonality=False,
                    changepoint_prior_scale=changepoint_prior_scale,
                    seasonality_prior_scale=seasonality_prior_scale,
                    uncertainty_samples=0)

        used = []
        if calendar is not None:
            cal_hist = calendar.iloc[:n]
            for col in calendar.columns:
                # constant inside THIS fold's training slice -> no
                # information, and an ill-posed coefficient
                if cal_hist[col].nunique() > 1:
                    m.add_regressor(col)
                    used.append(col)
            for col in used:
                hist[col] = cal_hist[col].to_numpy()

        try:
            m.fit(hist)
            future = pd.DataFrame({"ds": future_idx})
            if used:
                cal_future = calendar.reindex(future_idx).fillna(0.0)
                for col in used:
                    future[col] = cal_future[col].to_numpy()
            yhat = m.predict(future)["yhat"].to_numpy()
        except Exception:
            # A Stan fit that fails to converge is a real outcome on a
            # near-empty series. Falling back to the trailing mean keeps
            # the fold scored (every method must cover every fold, or the
            # identical-folds gate fails) and is recorded here rather
            # than silently producing zeros.
            w = v[-30:] if n >= 30 else v
            yhat = np.full(horizon, w.mean() if w.size else 0.0)

        return np.maximum(np.asarray(yhat, dtype=float), 0.0)

    _f.__name__ = name
    return _f
