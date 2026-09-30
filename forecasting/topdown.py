"""
forecasting/topdown.py
------------------------------------------------------------------
Item forecasts that borrow strength from the item's CATEGORY, plus the
equal-weight blend the item-level forecast uses.

  topdown_share_fit_predict   the category's long-run level (trailing mean over
                              `level_window` days) x the item's share of the
                              category over the last `share_window` days.
                              Long window for the smooth category, short one
                              for the item's own recent standing.
  blend_fit_predict           weighted average of several fit_predict callables.
  topdown_tsb_fit_predict     the 50/50 blend of the top-down forecast and TSB
                              (alpha = beta = 0.05) - the item-level default in
                              scripts/step4_forecast_model.py.

Why these, and not Prophet: scripts/test_item_forecast_methods.py scores 47
methods on the 58 Fast items over the project's walk-forward folds
(data/item_forecast_method_test.csv). Prophet as previously shipped scored a mean
MASE of 2.52; this blend scores 1.71 and beats Prophet on 46 of 57 items.

Date alignment
--------------
`fit_predict` receives only the item's training slice, but every series in the
pipeline sits on ONE shared daily index, so position `i` is the same date for
the item and for its category. The category array is bound in once, and the
callable reads `cat_values[:len(train)]` - the same trick
forecasting/prophet_model.py uses for its calendar, and it cannot see past the
slice the harness hands the item, so no fold can leak.

Each callable returns a flat per-day rate, like every other trailing method.
Everything is clipped at zero.
------------------------------------------------------------------
"""
import numpy as np

from .intermittent import tsb_fit_predict

__all__ = ["topdown_share_fit_predict", "blend_fit_predict", "topdown_tsb_fit_predict",
           "LEVEL_WINDOW", "SHARE_WINDOW", "TSB_ALPHA", "TSB_BETA"]

LEVEL_WINDOW = 180       # category's trailing mean: 6 months
SHARE_WINDOW = 30        # item's share of the category: last month
TSB_ALPHA = 0.05
TSB_BETA = 0.05


def topdown_share_fit_predict(cat_values, level_window: int = LEVEL_WINDOW,
                              share_window: int = SHARE_WINDOW):
    """rate = mean(category, last `level_window` days)
              x sum(item, last `share_window`) / sum(category, last `share_window`).

    Zero when the category sold nothing in the share window (no share to take)."""
    cat = np.asarray(cat_values, dtype=float).ravel()

    def _f(train, horizon):
        t = np.asarray(train, dtype=float).ravel()
        n = t.size
        c = cat[:n]
        if n == 0 or c.size < n:
            raise ValueError("category series is shorter than the item's training slice")
        denom = c[-share_window:].sum()
        share = t[-share_window:].sum() / denom if denom > 0 else 0.0
        return np.maximum(np.full(horizon, c[-level_window:].mean() * share), 0.0)
    _f.__name__ = f"topdown(level={level_window},share={share_window})"
    return _f


def blend_fit_predict(fns, weights=None):
    """Weighted average of fit_predict callables (equal weights by default)."""
    w = np.full(len(fns), 1.0 / len(fns)) if weights is None else np.asarray(weights, dtype=float)
    if len(w) != len(fns) or abs(w.sum() - 1.0) > 1e-9:
        raise ValueError("weights must match the callables and sum to 1")

    def _f(train, horizon):
        out = sum(wi * np.asarray(fn(train, horizon), dtype=float) for wi, fn in zip(w, fns))
        return np.maximum(out, 0.0)
    _f.__name__ = "blend(" + ",".join(getattr(fn, "__name__", "fn") for fn in fns) + ")"
    return _f


def topdown_tsb_fit_predict(cat_values, alpha: float = TSB_ALPHA, beta: float = TSB_BETA):
    """50/50: category-share forecast + TSB. The item-level default."""
    return blend_fit_predict([topdown_share_fit_predict(cat_values),
                              tsb_fit_predict(alpha, beta)])
