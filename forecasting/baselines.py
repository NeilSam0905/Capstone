"""
forecasting/baselines.py
------------------------------------------------------------------
The benchmark's non-intermittent methods, all as
`fit_predict(train, horizon) -> array` so evaluate.py can score them on
identical folds.

  naive              last observed value, held flat
  seasonal_naive     the last full seasonal cycle, tiled
  rolling_mean_30    mean of the trailing window
  rolling_median_30  median of the trailing window
  ets                Holt-Winters, additive damped trend + additive season

Why Holt-Winters is implemented here instead of imported
--------------------------------------------------------
statsmodels is installed in this environment, but it is NOT in the
project's declared dependency set (pandas, numpy, openpyxl, scipy). The
whole point of Block 6.2 / task A9 is that Chapter 4's central result
becomes reproducible from a clean clone without chasing a toolchain, and
quietly adding a dependency to achieve that would defeat it. The
recursions are short, so they live here and use scipy.optimize - which is
declared - to fit the smoothing parameters.

Everything is clipped at zero. These are unit sales; a method that
forecasts -3 lanyards is wrong in a way no metric should have to absorb.
------------------------------------------------------------------
"""
import numpy as np
from scipy.optimize import minimize

__all__ = [
    "naive_fit_predict", "seasonal_naive_fit_predict",
    "rolling_mean_fit_predict", "rolling_median_fit_predict",
    "ets_fit_predict", "holt_winters_forecast", "DEFAULT_SEASON",
]

DEFAULT_SEASON = 7          # weekly: the store's trading rhythm
DEFAULT_WINDOW = 30


def _clip(a):
    return np.maximum(np.asarray(a, dtype=float), 0.0)


def naive_fit_predict():
    """Persistence: tomorrow looks like the last day observed."""
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        return _clip(np.full(horizon, t[-1] if t.size else 0.0))
    _f.__name__ = "naive"
    return _f


def seasonal_naive_fit_predict(season: int = DEFAULT_SEASON):
    """Repeat the last complete seasonal cycle across the horizon."""
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        if t.size < season:
            return _clip(np.full(horizon, t.mean() if t.size else 0.0))
        cycle = t[-season:]
        reps = int(np.ceil(horizon / season))
        return _clip(np.tile(cycle, reps)[:horizon])
    _f.__name__ = f"seasonal_naive({season})"
    return _f


def rolling_mean_fit_predict(window: int = DEFAULT_WINDOW):
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        w = t[-window:] if t.size >= window else t
        return _clip(np.full(horizon, w.mean() if w.size else 0.0))
    _f.__name__ = f"rolling_mean({window})"
    return _f


def rolling_median_fit_predict(window: int = DEFAULT_WINDOW):
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        w = t[-window:] if t.size >= window else t
        return _clip(np.full(horizon, np.median(w) if w.size else 0.0))
    _f.__name__ = f"rolling_median({window})"
    return _f


# ---- Holt-Winters ----------------------------------------------------

def _hw_recursion(y, alpha, beta, gamma, phi, season):
    """Additive trend (damped) + additive seasonality.

    Returns (level, trend, seasonal_tail, sse). Runs the standard
    recursions once; the optimiser calls this repeatedly.
    """
    n = y.size
    # initialise: level from the first cycle, trend from the change
    # between the first two cycles, seasonals as deviations from level
    if n >= 2 * season:
        l = y[:season].mean()
        b = (y[season:2 * season].mean() - l) / season
    else:
        l = y.mean()
        b = 0.0
    s = (y[:season] - l) if n >= season else np.zeros(season)
    s = np.asarray(s, dtype=float).copy()

    sse = 0.0
    for t in range(n):
        si = t % season
        l_prev, b_prev = l, b

        fitted = l_prev + phi * b_prev + s[si]
        e = y[t] - fitted
        sse += e * e

        l = alpha * (y[t] - s[si]) + (1 - alpha) * (l_prev + phi * b_prev)
        b = beta * (l - l_prev) + (1 - beta) * phi * b_prev
        # Seasonal update uses the PREVIOUS level and trend, not the newly
        # updated level. This is Hyndman's standard additive formulation and
        # the one statsmodels implements. Using `y[t] - l` instead is a
        # different (and non-standard) model: the error is invisible at small
        # gamma and reached 8.8% divergence at gamma = 0.3, which is inside
        # the box the optimiser searches. Caught by
        # tests/test_holtwinters_reference.py.
        s[si] = gamma * (y[t] - l_prev - phi * b_prev) + (1 - gamma) * s[si]

    return l, b, s, sse


def holt_winters_forecast(train, horizon, season: int = DEFAULT_SEASON,
                          optimise: bool = True, maxiter: int = 40):
    """Fit Holt-Winters on `train` and forecast `horizon` steps."""
    y = np.asarray(train, dtype=float).ravel()
    if y.size == 0:
        return np.zeros(horizon)
    if y.size < 2 * season:
        # not enough history for a seasonal fit; fall back to the mean
        return _clip(np.full(horizon, y.mean()))

    def sse_of(params):
        a, b, g, p = params
        try:
            *_, sse = _hw_recursion(y, a, b, g, p, season)
        except (FloatingPointError, ValueError):
            return np.inf
        return sse if np.isfinite(sse) else np.inf

    x0 = np.array([0.3, 0.05, 0.1, 0.95])
    if optimise:
        res = minimize(sse_of, x0, method="L-BFGS-B",
                       bounds=[(1e-4, 0.999)] * 3 + [(0.8, 0.999)],
                       options={"maxiter": maxiter})
        params = res.x if res.success or np.isfinite(res.fun) else x0
    else:
        params = x0

    a, b_, g, p = params
    level, trend, s, _ = _hw_recursion(y, a, b_, g, p, season)

    # damped-trend cumulation: phi + phi^2 + ... + phi^h
    steps = np.arange(1, horizon + 1)
    damp = np.cumsum(p ** steps)
    season_idx = (y.size + np.arange(horizon)) % season
    return _clip(level + damp * trend + s[season_idx])


def ets_fit_predict(season: int = DEFAULT_SEASON, optimise: bool = True,
                    maxiter: int = 40):
    def _f(train, horizon):
        return holt_winters_forecast(train, horizon, season, optimise, maxiter)
    _f.__name__ = f"ets(season={season})"
    return _f
