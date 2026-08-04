"""
forecasting/intermittent.py
------------------------------------------------------------------
Croston's method and the Syntetos-Boylan Approximation. Block 4.5.

Why these belong in this project
--------------------------------
68,541 of 84,399 Fact_Sales rows are zero-quantity. For the S (slow)
tier that is not noise around a level - it is the shape of the demand.
Exponential smoothing on such a series chases the zeros and produces a
forecast that is wrong on both the days demand arrives and the days it
does not.

Croston's insight is to stop forecasting "demand per day" directly and
instead forecast two things separately:

    demand SIZE      how much is bought, when something is bought
    demand INTERVAL  how many periods pass between purchases

and recombine them as `size / interval`. Both are smoothed only on the
periods when demand actually arrives, so the zeros stop dragging the
level down.

This module deliberately exposes that decomposition (`size_estimate`,
`interval_estimate`, and the raw `sizes`/`intervals` arrays) rather than
returning a bare number, because the decomposition is the argument
Chapter 4 needs to make about why these methods suit the S tier.

SBA
---
Croston's estimator is biased: E[size]/E[interval] is not an unbiased
estimator of E[size/interval]. Syntetos and Boylan's correction multiplies
by (1 - alpha/2). It is a strict shrinkage of Croston toward zero, which
is why SBA usually wins on intermittent series where Croston over-forecasts.

Register note
-------------
Neither method appears in section 2.1.4 of the manuscript, so adding them
is already logged as Divergence Register #10.

Pure NumPy. No Prophet, no cmdstan, no network.
------------------------------------------------------------------
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

__all__ = [
    "CrostonResult", "croston", "sba", "decompose",
    "croston_fit_predict", "sba_fit_predict", "DEFAULT_ALPHA",
]

DEFAULT_ALPHA = 0.1


@dataclass
class CrostonResult:
    """A point forecast plus the decomposition that produced it."""
    point_forecast: float
    size_estimate: float          # z-hat: smoothed demand size
    interval_estimate: float      # p-hat: smoothed inter-demand interval
    alpha: float
    method: str
    n_periods: int
    n_nonzero: int
    sizes: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))
    intervals: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    @property
    def bias_factor(self) -> float:
        """1.0 for Croston, (1 - alpha/2) for SBA."""
        return 1.0 if self.method == "croston" else 1.0 - self.alpha / 2.0

    @property
    def average_interval(self) -> float:
        return float(self.intervals.mean()) if self.intervals.size else float("nan")

    @property
    def average_size(self) -> float:
        return float(self.sizes.mean()) if self.sizes.size else float("nan")

    def recombine(self) -> float:
        """The point forecast, rebuilt from its parts. Exists so the
        identity `size / interval * bias == point_forecast` can be
        asserted rather than assumed."""
        if self.interval_estimate == 0:
            return float("nan")
        return self.size_estimate / self.interval_estimate * self.bias_factor

    def __str__(self) -> str:
        return (f"{self.method}: {self.point_forecast:.4f}/period "
                f"= size {self.size_estimate:.3f} / interval "
                f"{self.interval_estimate:.3f} x {self.bias_factor:.3f} "
                f"({self.n_nonzero}/{self.n_periods} periods with demand)")


def decompose(y):
    """Split a series into demand sizes and inter-demand intervals.

    The first interval is measured from the start of the series, so a
    demand at index 0 has interval 1 - it arrived in the first period.

        [0, 0, 5, 0, 3]  ->  sizes [5, 3], intervals [3, 2]

    Returns (sizes, intervals) as float arrays of equal length.
    """
    v = np.asarray(y, dtype=float).ravel()
    if np.any(v < 0):
        raise ValueError("demand cannot be negative")

    idx = np.flatnonzero(v > 0)
    if idx.size == 0:
        return np.empty(0), np.empty(0)

    sizes = v[idx]
    # interval[0] counts periods from series start; the rest are gaps
    intervals = np.diff(np.concatenate([[-1], idx])).astype(float)
    return sizes, intervals


def _smooth(values, alpha, initial):
    """Simple exponential smoothing, returning the final level."""
    level = float(initial)
    for x in values:
        level += alpha * (float(x) - level)
    return level


def _croston_core(y, alpha, method):
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")

    v = np.asarray(y, dtype=float).ravel()
    if v.size == 0:
        raise ValueError("empty series - nothing to forecast")

    sizes, intervals = decompose(v)

    if sizes.size == 0:
        # Never sold. The honest forecast is zero, and the decomposition
        # is undefined rather than zero - there is no interval to speak of.
        return CrostonResult(0.0, 0.0, float("nan"), alpha, method,
                             v.size, 0, sizes, intervals)

    # initialise on the first observed demand, then smooth over the rest
    z_hat = _smooth(sizes[1:], alpha, sizes[0])
    p_hat = _smooth(intervals[1:], alpha, intervals[0])

    bias = 1.0 if method == "croston" else 1.0 - alpha / 2.0
    point = (z_hat / p_hat) * bias if p_hat > 0 else float("nan")

    return CrostonResult(float(point), float(z_hat), float(p_hat), alpha, method,
                         v.size, int(sizes.size), sizes, intervals)


def croston(y, alpha: float = DEFAULT_ALPHA) -> CrostonResult:
    """Croston's method. Returns a CrostonResult; `.point_forecast` is
    the expected demand per period."""
    return _croston_core(y, alpha, "croston")


def sba(y, alpha: float = DEFAULT_ALPHA) -> CrostonResult:
    """Syntetos-Boylan Approximation: Croston scaled by (1 - alpha/2) to
    correct Croston's upward bias on intermittent series."""
    return _croston_core(y, alpha, "sba")


# ---- harness-compatible wrappers -------------------------------------
# forecasting/evaluate.py calls fit_predict(train_values, horizon) -> array.
# Croston produces a flat rate, so the horizon is filled with it.

def croston_fit_predict(alpha: float = DEFAULT_ALPHA):
    def _f(train, horizon):
        rate = croston(train, alpha).point_forecast
        return np.full(horizon, 0.0 if not np.isfinite(rate) else rate)
    _f.__name__ = f"croston(alpha={alpha})"
    return _f


def sba_fit_predict(alpha: float = DEFAULT_ALPHA):
    def _f(train, horizon):
        rate = sba(train, alpha).point_forecast
        return np.full(horizon, 0.0 if not np.isfinite(rate) else rate)
    _f.__name__ = f"sba(alpha={alpha})"
    return _f
