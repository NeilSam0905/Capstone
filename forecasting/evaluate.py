"""
forecasting/evaluate.py
------------------------------------------------------------------
Walk-forward evaluation on a 30-day aggregate target. Blocks 4.2 + 4.4.

Two design decisions, both already made, recorded here so they are not
silently reopened by whoever reads this next.

**1. Re-target the evaluation, not the training resolution.**
Fitting still happens on tally-date observations - the daily series. Only
the SCORING is aggregated to 30 days. The alternative, re-targeting the
training unit, would make the natural observation a month; with ~26
months of data no SKU could reach the >=60 tier, and the sufficiency
tiers the whole design rests on would collapse.

**2. Walk-forward means walk-forward.**
Section 3.3.4 and Figure 3 both promise walk-forward validation and then
describe a single 80/20 chronological holdout. The manuscript cannot be
edited, so the code delivers what the manuscript promises: rolling
origins, expanding window, **minimum 3 folds**. An SKU that cannot
support 3 folds is *reported as insufficient*, never quietly scored on
one and listed beside SKUs that got three.

The leakage guarantee
---------------------
Each fold's training slice ends strictly before its origin, and the test
window is [origin, origin + horizon). `fit_predict` is handed nothing but
that training slice, so a model physically cannot see its own test
window. `Fold.assert_no_leakage()` re-checks this per fold, and
tests/test_evaluate.py checks it again across randomised configurations -
a leaky harness produces beautiful numbers, which is the failure mode
worth spending tests on.

Model interface
---------------
Any callable of the form

    fit_predict(train_values: np.ndarray, horizon: int) -> np.ndarray

returning `horizon` daily predictions. The harness sums them into the
30-day aggregate. Nothing else about the model is assumed, which is what
lets naive, rolling median, Croston, SBA and ETS all be scored on
identical folds.
------------------------------------------------------------------
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .metrics import mae, naive_scale, rmse

__all__ = [
    "Fold", "SkuEvaluation", "make_folds", "aggregate_blocks",
    "walk_forward_evaluate", "evaluate_methods", "DEFAULT_HORIZON", "DEFAULT_MIN_FOLDS",
]

DEFAULT_HORIZON = 30
DEFAULT_MIN_FOLDS = 3
DEFAULT_MIN_TRAIN = 60      # days of history before the first origin


@dataclass(frozen=True)
class Fold:
    """One rolling origin. Train is [0, train_end), test is
    [train_end, train_end + horizon) - both as positional indices into
    the SKU's daily series."""
    fold_index: int
    train_end: int          # exclusive; this IS the origin
    horizon: int

    @property
    def origin(self) -> int:
        return self.train_end

    @property
    def test_start(self) -> int:
        return self.train_end

    @property
    def test_end(self) -> int:      # exclusive
        return self.train_end + self.horizon

    @property
    def n_train(self) -> int:
        return self.train_end

    def train_slice(self, values: np.ndarray) -> np.ndarray:
        return values[:self.train_end]

    def test_slice(self, values: np.ndarray) -> np.ndarray:
        return values[self.test_start:self.test_end]

    def assert_no_leakage(self, n_total: int) -> None:
        """The invariant this whole module exists to protect."""
        if self.train_end <= 0:
            raise ValueError(f"fold {self.fold_index}: empty training window")
        if self.test_end > n_total:
            raise ValueError(
                f"fold {self.fold_index}: test window runs past the series "
                f"({self.test_end} > {n_total})")
        if self.test_start < self.train_end:
            raise ValueError(
                f"fold {self.fold_index}: test window starts at {self.test_start}, "
                f"before the training window ends at {self.train_end} - LEAKAGE")


@dataclass
class SkuEvaluation:
    """Per-SKU outcome. `sufficient` is False when the series could not
    support `min_folds` folds; `folds` is then empty and `reason` says why."""
    sku: object
    sufficient: bool
    n_folds: int
    reason: str = ""
    rows: List[dict] = field(default_factory=list)


def aggregate_blocks(values: Sequence[float], size: int) -> np.ndarray:
    """Sum a series into non-overlapping blocks of `size`, taking blocks
    from the END backwards so the most recent data is never the part
    that gets dropped by a ragged remainder."""
    v = np.asarray(values, dtype=float).ravel()
    n_blocks = v.size // size
    if n_blocks == 0:
        return np.empty(0, dtype=float)
    trimmed = v[v.size - n_blocks * size:]
    return trimmed.reshape(n_blocks, size).sum(axis=1)


def make_folds(n_total: int, horizon: int = DEFAULT_HORIZON,
               min_folds: int = DEFAULT_MIN_FOLDS,
               max_folds: Optional[int] = None,
               min_train: int = DEFAULT_MIN_TRAIN) -> List[Fold]:
    """Rolling origins with an expanding training window.

    Origins are laid out from the END of the series backwards in steps of
    `horizon`, so the test windows tile the most recent history without
    overlapping. Returns [] if fewer than `min_folds` origins fit - the
    caller is expected to record that as insufficient rather than score
    what it can.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if min_folds <= 0:
        raise ValueError("min_folds must be positive")

    origins = []
    origin = n_total - horizon          # last origin: test window ends at n_total
    while origin >= min_train:
        origins.append(origin)
        origin -= horizon
    origins.reverse()

    if max_folds is not None and len(origins) > max_folds:
        origins = origins[-max_folds:]   # keep the most recent

    if len(origins) < min_folds:
        return []

    folds = [Fold(i, o, horizon) for i, o in enumerate(origins)]
    for f in folds:
        f.assert_no_leakage(n_total)
    return folds


def walk_forward_evaluate(sku, values: Sequence[float],
                          fit_predict: Callable[[np.ndarray, int], np.ndarray],
                          method_name: str = "model",
                          horizon: int = DEFAULT_HORIZON,
                          min_folds: int = DEFAULT_MIN_FOLDS,
                          max_folds: Optional[int] = None,
                          min_train: int = DEFAULT_MIN_TRAIN,
                          folds: Optional[List[Fold]] = None) -> SkuEvaluation:
    """Score one method on one SKU across rolling origins.

    Pass `folds` to reuse a fold layout computed once - that is how every
    method is guaranteed to be scored on identical windows.
    """
    v = np.asarray(values, dtype=float).ravel()

    if folds is None:
        folds = make_folds(v.size, horizon, min_folds, max_folds, min_train)

    if not folds:
        return SkuEvaluation(
            sku, False, 0,
            reason=(f"{v.size} days of history supports fewer than {min_folds} "
                    f"folds at horizon {horizon} (min_train={min_train})"))

    rows = []
    for f in folds:
        f.assert_no_leakage(v.size)
        train = f.train_slice(v)
        actual_daily = f.test_slice(v)

        pred = np.asarray(fit_predict(train, f.horizon), dtype=float).ravel()
        if pred.size != f.horizon:
            raise ValueError(
                f"{method_name} returned {pred.size} predictions for horizon "
                f"{f.horizon} on SKU {sku!r}, fold {f.fold_index}")

        # THE scoring unit: one 30-day aggregate per fold, not 30 daily points
        actual_agg = float(actual_daily.sum())
        pred_agg = float(pred.sum())

        # MASE's scale must be in the same unit as the errors, so the
        # naive denominator is built from 30-day blocks of the TRAINING
        # data, not from daily values.
        blocks = aggregate_blocks(train, f.horizon)
        denom = naive_scale(blocks) if blocks.size > 1 else float("nan")

        rows.append({
            "sku": sku,
            "method": method_name,
            "fold": f.fold_index,
            "origin": f.origin,
            "n_train": f.n_train,
            "horizon": f.horizon,
            "actual_30d": actual_agg,
            "pred_30d": pred_agg,
            "abs_error": abs(actual_agg - pred_agg),
            "naive_scale": denom,
        })

    return SkuEvaluation(sku, True, len(folds), rows=rows)


def evaluate_methods(series_by_sku: Dict[object, Sequence[float]],
                     methods: Dict[str, Callable[[np.ndarray, int], np.ndarray]],
                     horizon: int = DEFAULT_HORIZON,
                     min_folds: int = DEFAULT_MIN_FOLDS,
                     max_folds: Optional[int] = None,
                     min_train: int = DEFAULT_MIN_TRAIN):
    """Score every method on every SKU, on identical folds.

    The fold layout is computed ONCE per SKU and handed to every method,
    so no method can be advantaged by a different split. SKUs that cannot
    support `min_folds` folds are excluded from the results frame and
    listed separately - they are never scored on fewer folds and reported
    alongside SKUs that got the full set.

    Returns (results_df, insufficient) where insufficient maps
    sku -> reason.
    """
    all_rows: List[dict] = []
    insufficient: Dict[object, str] = {}

    for sku, values in series_by_sku.items():
        v = np.asarray(values, dtype=float).ravel()
        folds = make_folds(v.size, horizon, min_folds, max_folds, min_train)

        if not folds:
            insufficient[sku] = (
                f"{v.size} days of history supports fewer than {min_folds} "
                f"folds at horizon {horizon}")
            continue

        for name, fn in methods.items():
            ev = walk_forward_evaluate(sku, v, fn, name, horizon, min_folds,
                                       max_folds, min_train, folds=folds)
            all_rows.extend(ev.rows)

    cols = ["sku", "method", "fold", "origin", "n_train", "horizon",
            "actual_30d", "pred_30d", "abs_error", "naive_scale"]
    df = pd.DataFrame(all_rows, columns=cols)
    return df, insufficient


def summarise(results: pd.DataFrame) -> pd.DataFrame:
    """Per-method summary across all SKUs and folds.

    MASE is computed per SKU first (each SKU scaled by its own naive
    denominator) and then averaged, so a single high-volume SKU cannot
    dominate a scale-free metric.
    """
    if results.empty:
        return pd.DataFrame()

    per_sku = []
    for (method, sku), g in results.groupby(["method", "sku"], sort=False):
        denom = np.nanmean(g["naive_scale"].to_numpy(dtype=float))
        m = mae(g["actual_30d"], g["pred_30d"])
        per_sku.append({
            "method": method,
            "sku": sku,
            "mae": m,
            "rmse": rmse(g["actual_30d"], g["pred_30d"]),
            "mase": m / denom if denom and np.isfinite(denom) and denom > 0 else np.nan,
            "n_folds": len(g),
        })

    ps = pd.DataFrame(per_sku)
    out = (ps.groupby("method")
             .agg(mae=("mae", "mean"),
                  rmse=("rmse", "mean"),
                  mase=("mase", "mean"),
                  mase_n_skus=("mase", "count"),   # non-NaN MASE contributions
                  n_skus=("sku", "nunique"),
                  n_folds=("n_folds", "sum"))
             .reset_index())

    # Rank on MASE, falling back to MAE. Sorting on MASE alone looks fine
    # until every SKU in the comparison has a flat training series: MASE is
    # then NaN for every method, pandas leaves the rows in input order, and
    # the "ranking" silently becomes dict insertion order. Ranking must
    # degrade to a real metric, not to whichever method was defined first.
    return (out.sort_values(["mase", "mae"], na_position="last", kind="stable")
               .reset_index(drop=True))
