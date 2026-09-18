"""
forecasting/observed_day.py
------------------------------------------------------------------
Corrects the single largest defect in the model inputs: 26% of every
series handed to every forecasting method is fabricated.

The defect
----------
`Fact_Sales` has rows only for days the store was actually tallied - 608
of the 821 calendar days in the model span. Every series builder in the
repo (`step4_forecast_model.py::build_series`,
`model_benchmark.py::load_daily_series`,
`step5_prescriptive.py::load_series`) reindexes onto the FULL calendar
with `fill_value=0`, which converts all 213 un-tallied days into "this
SKU sold zero that day".

That is not a rounding issue. It includes a 66-day unbroken run
(2024-06-01 .. 2024-08-05) and a 25-day run (2024-08-23 .. 2024-09-16)
where no tally sheet exists at all - step0's `FILES` list simply jumps
from the May 2024 workbook to the Aug 2024 one. Two months of "the store
sold nothing" is asserted by the pipeline and supported by no evidence.

Manuscript section 3.1.2 prescribes exactly the opposite treatment: days
with no entry are to be flagged "Unverified Zero" and "treat[ed] as
missing data rather than as true observations, preventing encoding
lapses from suppressing the demand forecast." `Dim_Date.is_tally_date`
already carries the flag - it agrees with "has a Fact_Sales row" on 100%
of days - and no series builder reads it.

The consequence is a systematic downward bias. A trailing 30-day mean
divides by 30 when up to 8 of those days are fiction, so it
under-forecasts by roughly the fabrication rate.

The correction
--------------
Forecast a rate per TRADING day rather than per calendar day, then scale
back up by how many trading days the horizon is expected to contain:

    level_per_trading_day = base_model(observed days only)
    expected_trading_days = horizon x (recent observed density)
    forecast_total        = level x expected_trading_days

spread evenly across the horizon, because the harness scores the sum.

What this does NOT fix
----------------------
The TEST windows are contaminated too: a 30-day window containing
un-tallied days has an `actual_30d` that understates true demand, and
nothing here can recover units that were never written down. Both the
corrected and uncorrected models are scored against the same imperfect
target, so the A/B comparison is fair - but the residual error floor it
implies is a property of the data, not of either model.

Genuine zero-sale days are preserved. The 23 days at the end of the span
(2026-07-09 .. 07-31) carry 176 Fact_Sales rows each with quantity 0:
they are observed, and they stay in.
------------------------------------------------------------------
"""
import numpy as np

__all__ = ["observed_rate_fit_predict", "observed_mask_from_index",
           "DEFAULT_DENSITY_WINDOW"]

DEFAULT_DENSITY_WINDOW = 90     # days used to estimate recent trading density


def observed_mask_from_index(con, index):
    """Boolean mask over `index`: True where the store was tallied.

    Read from `Dim_Date.is_tally_date`, the flag the schema already
    carries for this. A date missing from Dim_Date is treated as NOT
    observed - the conservative direction, since the alternative is to
    invent a trading day.
    """
    import pandas as pd
    d = pd.read_sql_query(
        "SELECT calendar_date, is_tally_date FROM Dim_Date",
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    return (d.reindex(index)["is_tally_date"].fillna(0).astype(int)
             .to_numpy().astype(bool))


def observed_rate_fit_predict(base, observed, density_window=DEFAULT_DENSITY_WINDOW,
                              min_observed=20, name=None):
    """Wrap any `fit_predict` so it sees trading days only.

    `observed` is a boolean mask aligned to the SAME shared daily index
    every series in the benchmark uses - position i is the same calendar
    day for every SKU, which is what lets a calendar-aware correction be
    applied to a bare array (the trick `prophet_model.py` uses too).

    `density_window` bounds how far back trading density is estimated
    from. Using the whole history instead would drag the 2024 gap into a
    2026 forecast, which is the same mistake in a different place.

    Falls back to the uncorrected base model when the training slice has
    fewer than `min_observed` trading days - below that the density
    estimate is noise and the correction would amplify it.
    """
    def _f(train, horizon):
        v = np.asarray(train, dtype=float).ravel()
        n = v.size
        mask = observed[:n] if observed.size >= n else np.ones(n, dtype=bool)

        if mask.sum() < min_observed:
            return np.asarray(base(v, horizon), dtype=float)

        # 1. the rate, computed on trading days only
        traded = v[mask]
        rate = float(np.mean(base(traded, horizon)))

        # 2. how many trading days the horizon should contain
        tail = mask[-density_window:] if n >= density_window else mask
        density = float(tail.mean()) if tail.size else 1.0
        expected_days = max(horizon * density, 1.0)

        total = max(rate * expected_days, 0.0)
        return np.full(horizon, total / horizon, dtype=float)

    _f.__name__ = name or f"observed_{getattr(base, '__name__', 'model')}"
    return _f
