"""
forecasting/metrics.py
------------------------------------------------------------------
Forecast accuracy and service-level metrics.

Two decisions in here are not stylistic, and both come from the
manuscript:

1. **MAPE's undefined cases are counted, never dropped.** USTore's demand
   is intermittent - 68,541 of 84,399 Fact_Sales rows are zero-quantity -
   so `y_true == 0` is the common case, not an edge case. Silently
   dropping those days is what lets a MAPE be reported as if it described
   the whole series when it describes only the days the store happened to
   sell something. `mape()` therefore returns a `MapeResult` carrying the
   value AND how many points it could not be computed on, and refuses to
   collapse to a bare float. If you want the number, ask for
   `.value`; you will have `.n_undefined` sitting next to it.

2. **Reporting splits into `standard_period` and `sem_break` pools.**
   Section 3.3.4 makes MAE the primary metric during semester breaks,
   because break-period demand is near-zero and percentage error is
   meaningless there. `evaluate_by_period()` returns both pools with the
   primary metric named per pool, so a summary table cannot accidentally
   average a break MAPE into a term-time one.

MASE is scale-free and is the metric that survives both problems, which
is why it is the one to lead with for intermittent SKUs.
------------------------------------------------------------------
"""
from typing import NamedTuple, Optional

import numpy as np

__all__ = [
    "mae", "rmse", "mape", "mape_undefined_count", "mase", "naive_scale",
    "fill_rate", "cycle_service_level", "MapeResult", "evaluate", "evaluate_by_period",
]


def _as_arrays(y_true, y_pred):
    a = np.asarray(y_true, dtype=float).ravel()
    b = np.asarray(y_pred, dtype=float).ravel()
    if a.shape != b.shape:
        raise ValueError(f"length mismatch: y_true={a.shape}, y_pred={b.shape}")
    if a.size == 0:
        raise ValueError("empty series - nothing to score")
    return a, b


def mae(y_true, y_pred) -> float:
    a, b = _as_arrays(y_true, y_pred)
    return float(np.mean(np.abs(a - b)))


def rmse(y_true, y_pred) -> float:
    a, b = _as_arrays(y_true, y_pred)
    return float(np.sqrt(np.mean((a - b) ** 2)))


class MapeResult(NamedTuple):
    """MAPE plus the bookkeeping that makes it honest.

    `value` is NaN when every actual is zero - which is a real outcome for
    a sem-break window, not an error to swallow.
    """
    value: float
    n_undefined: int
    n_used: int
    n_total: int

    @property
    def coverage(self) -> float:
        """Fraction of points the value was actually computed on."""
        return self.n_used / self.n_total if self.n_total else 0.0

    def __str__(self) -> str:
        if np.isnan(self.value):
            return f"MAPE undefined (all {self.n_total} actuals are zero)"
        return (f"MAPE {self.value:.1f}% on {self.n_used}/{self.n_total} points "
                f"({self.n_undefined} undefined at zero demand)")


def mape_undefined_count(y_true) -> int:
    """How many points MAPE cannot be computed on: those where the actual
    is zero, making the percentage denominator zero."""
    a = np.asarray(y_true, dtype=float).ravel()
    return int(np.count_nonzero(a == 0))


def mape(y_true, y_pred) -> MapeResult:
    """Mean absolute percentage error over the points where it is defined.

    Returns a MapeResult, not a float, so the undefined count travels with
    the number. See the module docstring for why.
    """
    a, b = _as_arrays(y_true, y_pred)
    defined = a != 0
    n_undefined = int(np.count_nonzero(~defined))
    n_used = int(np.count_nonzero(defined))

    if n_used == 0:
        return MapeResult(float("nan"), n_undefined, 0, a.size)

    pct = np.abs((a[defined] - b[defined]) / a[defined]) * 100.0
    return MapeResult(float(np.mean(pct)), n_undefined, n_used, a.size)


def naive_scale(y_train, seasonality: int = 1) -> float:
    """MASE's denominator: the in-sample MAE of a seasonal naive forecast
    on the TRAINING data.

    Computing it on training data is what makes MASE comparable across
    SKUs and across folds - a denominator taken from the test window would
    move with the thing being measured.
    """
    y = np.asarray(y_train, dtype=float).ravel()
    if y.size <= seasonality:
        raise ValueError(
            f"need more than {seasonality} training points to scale MASE, got {y.size}")
    return float(np.mean(np.abs(y[seasonality:] - y[:-seasonality])))


def mase(y_true, y_pred, naive_denominator: Optional[float] = None,
         y_train=None, seasonality: int = 1) -> float:
    """Mean absolute scaled error.

    MASE = MAE(forecast) / MAE(seasonal-naive on training)

    Supply either `naive_denominator` directly, or `y_train` to have it
    computed. MASE == 1.0 means "exactly as good as the naive baseline";
    below 1 beats it, above 1 loses to it.
    """
    if naive_denominator is None:
        if y_train is None:
            raise ValueError("give either naive_denominator or y_train")
        naive_denominator = naive_scale(y_train, seasonality)

    if naive_denominator == 0:
        # A flat training series: the naive forecast was perfect in-sample,
        # so there is no scale to divide by. NaN, not a divide-by-zero or a
        # silent zero, because "MASE is undefined here" is the true answer.
        return float("nan")

    return mae(y_true, y_pred) / float(naive_denominator)


def fill_rate(demand, supplied) -> float:
    """Fraction of demanded units actually served (a unit-weighted
    service measure). Returns 1.0 when nothing was demanded - a period
    with no demand was not a period of failed service."""
    d = np.asarray(demand, dtype=float).ravel()
    s = np.asarray(supplied, dtype=float).ravel()
    if d.shape != s.shape:
        raise ValueError(f"length mismatch: demand={d.shape}, supplied={s.shape}")

    total = float(np.sum(d))
    if total <= 0:
        return 1.0

    served = float(np.sum(np.minimum(d, np.maximum(s, 0.0))))
    return float(np.clip(served / total, 0.0, 1.0))


def cycle_service_level(demand, supplied) -> float:
    """Fraction of periods with no stockout (a period-weighted measure).

    Distinct from fill rate: one badly-missed day drags fill rate down by
    its unit count, but costs cycle service level exactly one period.
    """
    d = np.asarray(demand, dtype=float).ravel()
    s = np.asarray(supplied, dtype=float).ravel()
    if d.shape != s.shape:
        raise ValueError(f"length mismatch: demand={d.shape}, supplied={s.shape}")
    if d.size == 0:
        return 1.0
    return float(np.mean(s >= d))


def evaluate(y_true, y_pred, naive_denominator=None, y_train=None,
             seasonality: int = 1) -> dict:
    """All accuracy metrics for one series, as a plain dict."""
    m = mape(y_true, y_pred)
    out = {
        "n": int(np.asarray(y_true).size),
        "mae": mae(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "mape": m.value,
        "mape_n_undefined": m.n_undefined,
        "mape_n_used": m.n_used,
        "mape_coverage": m.coverage,
    }
    if naive_denominator is not None or y_train is not None:
        out["mase"] = mase(y_true, y_pred, naive_denominator, y_train, seasonality)
    return out


def evaluate_by_period(y_true, y_pred, is_sem_break, naive_denominator=None,
                       y_train=None, seasonality: int = 1) -> dict:
    """Split scoring into the two pools section 3.3.4 treats differently.

    `is_sem_break` is a boolean mask, one entry per observation. Returns

        {"standard_period": {...}, "sem_break": {...}, "overall": {...}}

    with a `primary_metric` naming the metric that pool should be judged
    on - MAPE during term time, MAE during breaks, where near-zero demand
    makes percentage error meaningless. A pool with no observations is
    reported as None rather than as a zero.
    """
    a, b = _as_arrays(y_true, y_pred)
    mask = np.asarray(is_sem_break, dtype=bool).ravel()
    if mask.shape != a.shape:
        raise ValueError(f"length mismatch: is_sem_break={mask.shape}, y_true={a.shape}")

    def pool(sel, primary):
        if not np.any(sel):
            return None
        d = evaluate(a[sel], b[sel], naive_denominator, y_train, seasonality)
        d["primary_metric"] = primary
        return d

    return {
        # MAE is primary during breaks per section 3.3.4; MAPE is only
        # meaningful where demand is reliably non-zero.
        "standard_period": pool(~mask, "mape"),
        "sem_break": pool(mask, "mae"),
        "overall": evaluate(a, b, naive_denominator, y_train, seasonality),
    }
