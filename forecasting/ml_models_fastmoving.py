"""
forecasting/ml_models_fastmoving.py
------------------------------------------------------------------
Tree-based and linear learners wired into the SAME
`fit_predict(train, horizon) -> array` contract every other method in
`forecasting/` uses, so `evaluate.py` can score them on identical
walk-forward folds. Added for the Fast-moving benchmark
(`scripts/benchmark_fast_raw_vs_clean.py`).

Why DIRECT 30-day-total regression, not recursive daily
-------------------------------------------------------
The harness scores a 30-day AGGREGATE (evaluate.py sums the horizon).
A recursive daily model would have to feed its own predictions back in
for 30 steps, compounding error against a target nobody scores. So each
learner is trained to predict the next 30 days' TOTAL directly from
features observable at the origin, and that total is then spread evenly
across the horizon - the sum, which is the scored quantity, is exactly
the model's output either way.

Causality
---------
A training example at index t uses features built from y[:t] ONLY and a
target of sum(y[t:t+30]). Both live strictly inside the fold's training
slice, because the harness never hands `fit_predict` anything past the
origin. `build_training_set` is the single place this is enforced.

Dependencies
------------
scikit-learn, xgboost and lightgbm are NOT in
requirements/requirements.txt (the declared set is pandas, numpy,
openpyxl, scipy - see the note at the top of baselines.py about why that
matters). Every import here is therefore LAZY and guarded: a missing
library disables that one method and leaves the rest of the benchmark
runnable, rather than failing the import of the whole module.
`available_ml_methods()` reports what actually loaded.
------------------------------------------------------------------
"""
import numpy as np

__all__ = [
    "make_features", "build_training_set", "sklearn_direct_fit_predict",
    "random_forest_fit_predict", "extra_trees_fit_predict",
    "gradient_boosting_fit_predict", "ridge_fit_predict",
    "xgboost_fit_predict", "lightgbm_fit_predict",
    "FEATURE_NAMES", "MIN_HISTORY", "available_ml_methods",
]

MIN_HISTORY = 90        # days of history a training row needs behind it
DEFAULT_HORIZON = 30

FEATURE_NAMES = [
    "lag_1", "lag_7", "lag_14", "lag_30",
    "mean_7", "mean_14", "mean_30", "mean_60", "mean_90",
    "std_30", "std_90",
    "sum_30", "sum_60", "sum_90",
    "nonzero_frac_30", "nonzero_frac_90",
    "days_since_sale", "max_30", "max_90",
    "trend_30v30", "ratio_30v90",
    "dow", "week_of_month",
]


def make_features(y, t):
    """Feature vector for an origin at index `t`, built from y[:t] only.

    `t` is where the forecast starts, so y[t] itself is unseen - this is
    the causality boundary and nothing below is allowed to cross it.
    """
    h = np.asarray(y, dtype=float)[:t]
    n = h.size
    if n == 0:
        return np.zeros(len(FEATURE_NAMES), dtype=float)

    def tail(k):
        return h[-k:] if n >= k else h

    def lag(k):
        return float(h[-k]) if n >= k else 0.0

    t7, t14, t30, t60, t90 = (tail(k) for k in (7, 14, 30, 60, 90))

    nz = np.nonzero(h > 0)[0]
    days_since = float(n - 1 - nz[-1]) if nz.size else float(min(n, 90))

    m30, m90 = float(t30.mean()), float(t90.mean())
    prev30 = h[-60:-30] if n >= 60 else h[:max(n - 30, 1)]

    return np.array([
        lag(1), lag(7), lag(14), lag(30),
        float(t7.mean()), float(t14.mean()), m30, float(t60.mean()), m90,
        float(t30.std()), float(t90.std()),
        float(t30.sum()), float(t60.sum()), float(t90.sum()),
        float((t30 > 0).mean()), float((t90 > 0).mean()),
        min(days_since, 90.0), float(t30.max()), float(t90.max()),
        m30 - float(prev30.mean()) if prev30.size else 0.0,
        (m30 / m90) if m90 > 0 else 0.0,
        float(t % 7),                    # every SKU shares one calendar index
        float((t // 7) % 4),
    ], dtype=float)


def build_training_set(train, horizon=DEFAULT_HORIZON, min_history=MIN_HISTORY):
    """(X, y) for the direct 30-day-total regression, from `train` alone.

    Rows are origins t in [min_history, len(train) - horizon]; the target
    for each is the next `horizon` days' total, which is still inside
    `train`. Returns empty arrays when the slice is too short - callers
    fall back rather than fit on nothing.
    """
    v = np.asarray(train, dtype=float).ravel()
    last = v.size - horizon
    if last < min_history:
        return np.empty((0, len(FEATURE_NAMES))), np.empty(0)

    X = np.array([make_features(v, t) for t in range(min_history, last + 1)])
    y = np.array([v[t:t + horizon].sum() for t in range(min_history, last + 1)])
    return X, y


def fallback_forecast(train, horizon, window=30):
    """What every learner does when the training slice is too thin to fit:
    the trailing-mean forecast. Chosen because rolling_mean_30 is what the
    production pipeline already uses (step4_forecast_model.py), so a
    fallback row is scored against the incumbent, not against zero."""
    t = np.asarray(train, dtype=float)
    w = t[-window:] if t.size >= window else t
    return np.maximum(np.full(horizon, w.mean() if w.size else 0.0), 0.0)


def sklearn_direct_fit_predict(make_model, min_history=MIN_HISTORY,
                               min_rows=40, name="ml"):
    """Wrap any fit/predict estimator as a `fit_predict(train, horizon)`.

    `make_model` is a zero-arg factory so every fold gets a FRESH
    estimator - reusing one instance across folds would carry state from
    a later origin back into an earlier one, which is leakage by another
    name.
    """
    def _f(train, horizon):
        v = np.asarray(train, dtype=float).ravel()
        X, y = build_training_set(v, horizon, min_history)
        if X.shape[0] < min_rows:
            return fallback_forecast(v, horizon)

        model = make_model()
        model.fit(X, y)
        total = float(model.predict(make_features(v, v.size).reshape(1, -1))[0])
        total = max(total, 0.0)
        return np.full(horizon, total / horizon, dtype=float)

    _f.__name__ = name
    return _f


# ---- concrete learners ------------------------------------------------
# Hyperparameters are deliberately conservative (shallow trees, high leaf
# minimums, strong shrinkage). A per-SKU model sees ~400 rows of a series
# that is mostly zeros; an unconstrained booster memorises it, and the
# walk-forward score then measures memorisation rather than skill.

def random_forest_fit_predict(n_estimators=200, min_samples_leaf=3, seed=0):
    from sklearn.ensemble import RandomForestRegressor
    return sklearn_direct_fit_predict(
        lambda: RandomForestRegressor(
            n_estimators=n_estimators, min_samples_leaf=min_samples_leaf,
            max_features="sqrt", random_state=seed, n_jobs=-1),
        name="random_forest")


def extra_trees_fit_predict(n_estimators=200, min_samples_leaf=3, seed=0):
    from sklearn.ensemble import ExtraTreesRegressor
    return sklearn_direct_fit_predict(
        lambda: ExtraTreesRegressor(
            n_estimators=n_estimators, min_samples_leaf=min_samples_leaf,
            max_features="sqrt", random_state=seed, n_jobs=-1),
        name="extra_trees")


def gradient_boosting_fit_predict(n_estimators=200, max_depth=3,
                                  learning_rate=0.05, seed=0):
    from sklearn.ensemble import GradientBoostingRegressor
    return sklearn_direct_fit_predict(
        lambda: GradientBoostingRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8, random_state=seed),
        name="gradient_boosting")


def ridge_fit_predict(alpha=10.0):
    """The linear control. If a booster cannot beat a regularised linear
    fit on the same features, the gain was never in the model class."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return sklearn_direct_fit_predict(
        lambda: make_pipeline(StandardScaler(), Ridge(alpha=alpha)),
        name="ridge")


def xgboost_fit_predict(n_estimators=300, max_depth=3, learning_rate=0.05, seed=0):
    import xgboost as xgb
    return sklearn_direct_fit_predict(
        lambda: xgb.XGBRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, min_child_weight=5, random_state=seed,
            n_jobs=-1, tree_method="hist", verbosity=0),
        name="xgboost")


def lightgbm_fit_predict(n_estimators=300, num_leaves=15, learning_rate=0.05, seed=0):
    import lightgbm as lgb
    return sklearn_direct_fit_predict(
        lambda: lgb.LGBMRegressor(
            n_estimators=n_estimators, num_leaves=num_leaves,
            learning_rate=learning_rate, min_child_samples=20,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_lambda=1.0, random_state=seed, n_jobs=-1, verbose=-1),
        name="lightgbm")


def available_ml_methods():
    """{name: fit_predict} for every learner whose library actually
    imported. A missing optional dependency drops one row from the
    comparison table; it does not fail the run."""
    out, missing = {}, {}
    for name, factory in [
        ("random_forest", random_forest_fit_predict),
        ("extra_trees", extra_trees_fit_predict),
        ("gradient_boosting", gradient_boosting_fit_predict),
        ("ridge", ridge_fit_predict),
        ("xgboost", xgboost_fit_predict),
        ("lightgbm", lightgbm_fit_predict),
    ]:
        try:
            out[name] = factory()
        except ImportError as exc:
            missing[name] = str(exc)
    return out, missing
