"""
forecasting/ml_models.py
------------------------------------------------------------------
Tree-based ML baselines (gradient boosting / random forest), scored on
the SAME walk-forward harness as forecasting/baselines.py - same
`fit_predict(train, horizon) -> array` interface, same folds.

EXPERIMENTAL. Not part of the committed benchmark
(data/model_benchmark_summary.csv / scripts/model_benchmark.py). Requires
xgboost, lightgbm and scikit-learn, none of which are in
requirements/requirements.txt - see
requirements/requirements-ml-experimental.txt. Kept out of the standard
install for the same reason Prophet is (requirements-prophet.txt):
Chapter 4's central result has to reproduce from a clean clone of the
declared dependency set alone.

Each model is fit PER SKU PER FOLD on that SKU's own lagged daily
history - the same autoregressive setup naive/rolling-mean/ETS use, just
with a tree learner instead of a closed-form rule. It forecasts
`horizon` days RECURSIVELY: predict day 1, append the prediction to the
lag buffer, predict day 2 from a buffer that now includes that
prediction, and so on. Errors can compound over a 30-day horizon this
way - that is a property of the method, not a bug in the harness.
------------------------------------------------------------------
"""
import numpy as np

__all__ = [
    "xgboost_fit_predict", "lightgbm_fit_predict", "random_forest_fit_predict",
    "xgboost_pooled_ctor", "lightgbm_pooled_ctor", "random_forest_pooled_ctor",
    "pooled_fit_predict",
]

LAGS = (1, 2, 3, 7, 14, 21, 28)
ROLL_WINDOWS = (7, 14, 30)
MIN_TRAINING_ROWS = 10   # below this a tree fit is noise, not a model


def _clip(a):
    return np.maximum(np.asarray(a, dtype=float), 0.0)


def _feature_row(buffer, day_of_week):
    """buffer: 1D array/list of the series observed so far (real history
    plus, during recursive forecasting, predictions already made)."""
    n = len(buffer)
    row = [buffer[-lag] if n >= lag else 0.0 for lag in LAGS]
    for w in ROLL_WINDOWS:
        tail = buffer[-w:] if n >= 1 else buffer
        row.append(float(np.mean(tail)) if len(tail) else 0.0)
    row.append(float(np.std(buffer[-7:])) if n >= 2 else 0.0)
    row.append(float(day_of_week))
    return row


def _build_training_set(train):
    max_lag = max(LAGS)
    n = train.size
    if n <= max_lag:
        return None, None
    X = [_feature_row(train[:t], t % 7) for t in range(max_lag, n)]
    y = train[max_lag:n]
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def _tree_recursive_fit_predict(model_ctor):
    def _f(train, horizon):
        t = np.asarray(train, dtype=float).ravel()
        X, y = _build_training_set(t)
        if X is None or X.shape[0] < MIN_TRAINING_ROWS:
            # Too little history for a tree fit to mean anything - fall
            # back to the trailing-30-day mean, the same floor
            # rolling_mean_30 uses.
            w = t[-30:] if t.size else t
            return _clip(np.full(horizon, w.mean() if w.size else 0.0))

        model = model_ctor()
        model.fit(X, y)

        buffer = list(t)
        preds = []
        for h in range(horizon):
            row = _feature_row(buffer, (t.size + h) % 7)
            p = max(float(model.predict([row])[0]), 0.0)
            preds.append(p)
            buffer.append(p)
        return np.asarray(preds, dtype=float)
    return _f


def xgboost_fit_predict(n_estimators=100, max_depth=3, learning_rate=0.1):
    def _ctor():
        from xgboost import XGBRegressor
        return XGBRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, objective="reg:squarederror",
            verbosity=0, n_jobs=1)
    f = _tree_recursive_fit_predict(_ctor)
    f.__name__ = f"xgboost(n={n_estimators},depth={max_depth})"
    return f


def lightgbm_fit_predict(n_estimators=100, max_depth=3, learning_rate=0.1):
    def _ctor():
        from lightgbm import LGBMRegressor
        return LGBMRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, min_child_samples=5,
            verbosity=-1, n_jobs=1)
    f = _tree_recursive_fit_predict(_ctor)
    f.__name__ = f"lightgbm(n={n_estimators},depth={max_depth})"
    return f


def random_forest_fit_predict(n_estimators=100, max_depth=5):
    def _ctor():
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            n_jobs=1, random_state=0)
    f = _tree_recursive_fit_predict(_ctor)
    f.__name__ = f"random_forest(n={n_estimators},depth={max_depth})"
    return f


# ---- pooled-by-category variant ------------------------------------
# Everything above fits one model PER SKU. The functions below instead
# fit ONE model on training rows pooled across every SKU handed in, then
# forecast each SKU recursively from that shared model - the "categorize
# the items, train one model per category" experiment
# (scripts/model_benchmark_category.py). Same features, same recursive
# horizon walk, same MIN_TRAINING_ROWS floor as the per-SKU path above;
# the only difference is that the training rows come from many SKUs
# instead of one.

def xgboost_pooled_ctor(n_estimators=50, max_depth=3, learning_rate=0.1):
    def _ctor():
        from xgboost import XGBRegressor
        return XGBRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, objective="reg:squarederror",
            verbosity=0, n_jobs=1)
    return _ctor


def lightgbm_pooled_ctor(n_estimators=50, max_depth=3, learning_rate=0.1):
    def _ctor():
        from lightgbm import LGBMRegressor
        return LGBMRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, min_child_samples=5,
            verbosity=-1, n_jobs=1)
    return _ctor


def random_forest_pooled_ctor(n_estimators=50, max_depth=5):
    def _ctor():
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            n_jobs=1, random_state=0)
    return _ctor


def _pooled_training_set(train_by_sku):
    """Stack (X, y) built the same way _build_training_set does, across
    every SKU in train_by_sku. Returns (None, None) if no SKU contributed
    a single usable row."""
    xs, ys = [], []
    for values in train_by_sku.values():
        X, y = _build_training_set(np.asarray(values, dtype=float).ravel())
        if X is not None:
            xs.append(X)
            ys.append(y)
    if not xs:
        return None, None
    return np.vstack(xs), np.concatenate(ys)


def pooled_fit_predict(model_ctor, train_by_sku, horizon):
    """Fit ONE model on rows pooled across every SKU in train_by_sku, then
    recursively forecast `horizon` days for EACH sku independently from
    that shared model.

    train_by_sku: dict sku -> 1D training array (that SKU's history up to
    the current fold origin). Returns dict sku -> prediction array.

    Falls back to the trailing-30-day mean, per SKU, when the POOL as a
    whole has fewer than MIN_TRAINING_ROWS usable rows - the same floor
    _tree_recursive_fit_predict applies per SKU.
    """
    X, y = _pooled_training_set(train_by_sku)

    if X is None or X.shape[0] < MIN_TRAINING_ROWS:
        fallback = {}
        for sku, values in train_by_sku.items():
            t = np.asarray(values, dtype=float).ravel()
            w = t[-30:] if t.size else t
            fallback[sku] = _clip(np.full(horizon, w.mean() if w.size else 0.0))
        return fallback

    model = model_ctor()
    model.fit(X, y)

    preds_by_sku = {}
    for sku, values in train_by_sku.items():
        t = np.asarray(values, dtype=float).ravel()
        buffer = list(t)
        preds = []
        for h in range(horizon):
            row = _feature_row(buffer, (t.size + h) % 7)
            p = max(float(model.predict([row])[0]), 0.0)
            preds.append(p)
            buffer.append(p)
        preds_by_sku[sku] = np.asarray(preds, dtype=float)
    return preds_by_sku
