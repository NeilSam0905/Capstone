"""
forecasting/calendar_models.py
------------------------------------------------------------------
Calendar-lag models: the one family `benchmark_fast_raw_vs_clean.py`
left untested, and the one the manuscript's own thesis points at.

Why these were missing
----------------------
The 29-method benchmark contains exactly one seasonal method,
`seasonal_naive_7`, and its season is a WEEK. Every other method is some
form of trailing average, which by construction cannot know that August
is enrollment month and July is dead. Yet sections 1.2 and 3.3.2 rest
entirely on the claim that USTore demand is driven by the academic
calendar - and the data agrees: August 2025 sold 5,210 units on the Fast
segment against a 12-window median of 2,976.

That is information no method in the benchmark uses. These do.

  seasonal_naive_365   the same 30-day window one calendar year ago,
                       used as-is. The crudest possible use of the
                       annual cycle, and therefore the right control:
                       any more elaborate model has to beat THIS before
                       its elaboration has earned anything.
  seasonal_index       last year's window, RESCALED by how the store is
                       trading now: level_ratio = mean(recent window) /
                       mean(same window a year ago). The standard retail
                       decomposition - a seasonal shape times a current
                       level - so a growing or shrinking catalogue does
                       not inherit last year's magnitude wholesale.
  semester_week_mean   mean historical demand on days sharing the same
                       `semester_week` value. Uses the academic calendar
                       directly rather than the Gregorian one, which
                       matters because term dates move by a week or two
                       between years and a 365-day lag silently
                       misaligns them.

A caveat that bounds all three on THIS dataset: the series starts
2024-05-02, so a 365-day lag has a full year behind it only from
2025-05-02 onward, and the earliest folds reach back into the sparse,
non-zero-filled Aug-Sep 2024 records. These models are structurally
short of history here in a way the trailing averages are not. If they
win anyway, that is a strong result; if they lose, the data volume is a
live alternative explanation and must be reported as one.

Same `fit_predict(train, horizon) -> array` contract as everything else,
so evaluate.py scores them on identical folds.
------------------------------------------------------------------
"""
import numpy as np

__all__ = [
    "seasonal_naive_365_fit_predict", "seasonal_index_fit_predict",
    "semester_week_mean_fit_predict", "blend_fit_predict", "YEAR",
]

YEAR = 365


def _clip(a):
    return np.maximum(np.asarray(a, dtype=float), 0.0)


def _fallback(train, horizon, window=30):
    """Trailing mean, used whenever the annual lag is not yet reachable.
    Named rather than inlined because how often it fires IS the result on
    a two-year dataset - the caller reports the count."""
    t = np.asarray(train, dtype=float)
    w = t[-window:] if t.size >= window else t
    return _clip(np.full(horizon, w.mean() if w.size else 0.0))


def seasonal_naive_365_fit_predict(year=YEAR):
    """Last year's same window, verbatim."""
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        if t.size < year:
            return _fallback(t, horizon)
        # the window starting exactly one year before the origin
        past = t[t.size - year: t.size - year + horizon]
        if past.size < horizon:                     # ragged tail
            past = np.concatenate([past, np.full(horizon - past.size, past.mean())])
        return _clip(past)
    _f.__name__ = f"seasonal_naive_{year}"
    return _f


def seasonal_index_fit_predict(year=YEAR, level_window=90, cap=4.0):
    """Last year's shape, this year's level.

    forecast = last_year_window x (recent level / last year's level at
    the same point in its cycle)

    The ratio is capped both ways: on a series this intermittent the
    denominator can be a handful of units, and an uncapped ratio would
    turn one quiet month last year into a 50x multiplier this year.
    """
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        if t.size < year + level_window:
            return _fallback(t, horizon)

        past = t[t.size - year: t.size - year + horizon]
        if past.size < horizon:
            past = np.concatenate([past, np.full(horizon - past.size, past.mean())])

        recent_level = t[-level_window:].mean()
        past_level = t[t.size - year - level_window: t.size - year].mean()
        if past_level <= 0:
            return _fallback(t, horizon)

        ratio = float(np.clip(recent_level / past_level, 1.0 / cap, cap))
        return _clip(past * ratio)
    _f.__name__ = f"seasonal_index_{year}"
    return _f


def semester_week_mean_fit_predict(semester_week, min_obs=3):
    """Mean historical demand on days sharing the horizon day's
    `semester_week`.

    `semester_week` is an integer array aligned to the SAME daily index
    every series in the benchmark uses, so position i in the array is
    position i in the calendar for every SKU - the same trick
    prophet_model.py uses to hand a date-driven model a bare array.

    Falls back to the trailing mean for any horizon day whose
    semester_week has fewer than `min_obs` historical observations,
    rather than fitting a mean to one or two days.
    """
    sw = np.asarray(semester_week)

    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        n = t.size
        if n < 60 or n + horizon > sw.size:
            return _fallback(t, horizon)

        hist_weeks = sw[:n]
        out = np.empty(horizon, dtype=float)
        default = float(t[-30:].mean()) if n >= 30 else float(t.mean())
        for i in range(horizon):
            target = sw[n + i]
            mask = hist_weeks == target
            out[i] = t[mask].mean() if mask.sum() >= min_obs else default
        return _clip(out)
    _f.__name__ = "semester_week_mean"
    return _f


def blend_fit_predict(a, b, weight=0.5, name="blend"):
    """Convex combination of two fit_predict callables.

    Included because the interesting question about a calendar model on
    two years of data is rarely "is it better" but "does averaging it
    with the incumbent beat either alone" - a seasonal signal can carry
    real information and still be too noisy to use undiluted.
    """
    w = float(np.clip(weight, 0.0, 1.0))

    def _f(train, horizon):
        return _clip(w * np.asarray(a(train, horizon), dtype=float)
                     + (1.0 - w) * np.asarray(b(train, horizon), dtype=float))
    _f.__name__ = name
    return _f
