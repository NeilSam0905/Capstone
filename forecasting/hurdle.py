"""
forecasting/hurdle.py
------------------------------------------------------------------
Weekly hurdle model: "will this SKU sell at all this week, and if so,
how much" - as a `fit_predict(train, horizon) -> array` so it scores on
the same walk-forward folds as everything in baselines.py / intermittent.py.

Why this is a different experiment from `tsb`, not a restatement of it
-----------------------------------------------------------------------
TSB (forecasting/intermittent.py) already decomposes demand into a
probability and a size, but it does so PER DAY, smoothed with a single
exponential constant. On an 81%-zero daily series, "will it sell today"
is itself close to degenerate (see docs/DEGENERATE_FORECAST.md, #21) -
the positive class is rare even for genuinely active SKUs.

This model asks the same probability x size question at WEEKLY
resolution instead: aggregate the trailing history into non-overlapping
weeks first, then ask "what fraction of recent weeks had any demand"
and "when a week has demand, how much is it". A SKU that sells a little
every week looks very different at weekly resolution than at daily
resolution, even though the underlying series is the same.

The estimator is a plain empirical rate, not a smoothing recursion -
deliberately, so the "will it sell this week" number is legible as
exactly what it says: the fraction of the trailing window's weeks with
a sale.

The second model in this file, `logistic_hurdle_fit_predict`, is the
step up from that: an actual FITTED classifier (regularised logistic
regression, no external ML dependency - just numpy + the already-declared
scipy.optimize, same as `holt_winters_forecast` in baselines.py) that
predicts daily sale probability from weekday, recent sale frequency and
days-since-last-sale, instead of one flat empirical rate. It answers
"will it sell TODAY", conditioned on features, where `weekly_hurdle`
answers "how often does it sell in a week" as a single number.

Why weekday is available as a feature without ever seeing a date
------------------------------------------------------------------
`fit_predict(train, horizon)` receives only the value array, not dates.
But every SKU's series is built the same way (model_benchmark.py's
`load_daily_series`, step4's `build_series`): reindexed onto ONE shared
calendar starting at the same first date. So position 0 is the same
calendar day for every SKU, in every fold (`train = values[:train_end]`
always starts at position 0), and `position % 7` recovers true weekday
up to a fixed rotation. Which bucket ends up labelled "Monday" is
irrelevant to a model that just learns one weight per bucket.
------------------------------------------------------------------
"""
import numpy as np
from scipy.optimize import minimize

from .evaluate import aggregate_blocks

__all__ = [
    "weekly_hurdle_fit_predict", "DEFAULT_WEEK", "DEFAULT_WINDOW_WEEKS",
    "logistic_hurdle_fit_predict", "pooled_logistic_hurdle_fit_predict",
    "calendar_logistic_hurdle_fit_predict",
]

DEFAULT_WEEK = 7
DEFAULT_WINDOW_WEEKS = 12          # ~3 months of trailing weeks


def _clip(a):
    return np.maximum(np.asarray(a, dtype=float), 0.0)


def weekly_hurdle_fit_predict(window_weeks: int = DEFAULT_WINDOW_WEEKS,
                              week: int = DEFAULT_WEEK):
    """Two-part weekly forecast, spread evenly over the horizon's days.

        p_hat    = fraction of the trailing `window_weeks` complete weeks
                   with a nonzero total - "how often does this SKU sell
                   at all in a week"
        size_hat = mean of the NONZERO weekly totals in that same window -
                   "how much, in a week it does sell"
        forecast = p_hat * size_hat, divided evenly across each day of
                   the horizon so it can be scored by the same harness
                   that sums daily predictions into a 30-day aggregate

    Falls back to whatever weeks are available when the training slice
    is shorter than `window_weeks` full weeks; returns 0 when there is
    not even one complete week of history yet.
    """
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        weeks = aggregate_blocks(t, week)
        if weeks.size == 0:
            return _clip(np.zeros(horizon))

        w = weeks[-window_weeks:] if weeks.size >= window_weeks else weeks
        p_hat = float(np.mean(w > 0))
        nonzero = w[w > 0]
        size_hat = float(nonzero.mean()) if nonzero.size else 0.0

        daily_rate = (p_hat * size_hat) / week
        return _clip(np.full(horizon, daily_rate))

    _f.__name__ = f"weekly_hurdle(w={window_weeks})"
    return _f


# ---- logistic hurdle: a fitted classifier instead of an empirical rate --

def _weekday_onehot(n, offset=0):
    """n x 7 one-hot weekday bucket, position `offset` at the series start."""
    idx = (offset + np.arange(n)) % 7
    onehot = np.zeros((n, 7))
    onehot[np.arange(n), idx] = 1.0
    return onehot


def _causal_rate(y, window):
    """Fraction of nonzero days in the trailing `window`, EXCLUDING day t
    itself - feature[t] may only see y[:t]. Rows with no history yet (t=0)
    get 0.0 (no evidence of recent sales), not NaN, so the design matrix
    stays finite without a separate missing-data case."""
    n = y.size
    nz = (y > 0).astype(float)
    csum = np.concatenate([[0.0], np.cumsum(nz)])   # csum[i] = sum(nz[:i])
    idx = np.arange(n)
    lo = np.clip(idx - window, 0, None)
    span = np.maximum(idx - lo, 1)                  # avoid /0 at t=0
    rate = (csum[idx] - csum[lo]) / span
    rate[idx - lo == 0] = 0.0                        # t == 0: no prior days
    return rate


def _causal_days_since_sale(y, cap):
    """Days since the last nonzero day STRICTLY BEFORE t, capped at `cap`.
    t=0 gets `cap` (no prior history - treated as "a long time")."""
    n = y.size
    idx = np.arange(n)
    last_incl = np.maximum.accumulate(np.where(y > 0, idx, -1))
    last_excl = np.concatenate([[-1], last_incl[:-1]])
    gap = np.where(last_excl >= 0, idx - last_excl, cap)
    return np.minimum(gap, cap).astype(float)


def _design_matrix(y, rate_windows, recency_cap, offset=0):
    n = y.size
    cols = [_weekday_onehot(n, offset)]
    for w in rate_windows:
        cols.append(_causal_rate(y, w).reshape(-1, 1))
    cols.append((_causal_days_since_sale(y, recency_cap) / recency_cap).reshape(-1, 1))
    return np.hstack(cols)


def _fit_logistic(X, y, l2, maxiter=150):
    """Regularised logistic regression via L-BFGS-B on the analytic
    gradient - same optimiser `holt_winters_forecast` already uses, no
    sklearn. L2 (not maximum likelihood alone) is what keeps this well
    posed on an SKU whose training label is nearly constant (almost
    always sold, or almost never) - without it, separable data drives
    the weights to +/-infinity instead of converging."""
    n, p = X.shape

    def nll_and_grad(w):
        z = np.clip(X @ w, -30, 30)
        pred = 1.0 / (1.0 + np.exp(-z))
        eps = 1e-9
        nll = (-np.sum(y * np.log(pred + eps) + (1 - y) * np.log(1 - pred + eps))
               + 0.5 * l2 * np.sum(w * w))
        grad = X.T @ (pred - y) + l2 * w
        return nll, grad

    res = minimize(nll_and_grad, np.zeros(p), jac=True, method="L-BFGS-B",
                   options={"maxiter": maxiter})
    return res.x


def logistic_hurdle_fit_predict(rate_windows=(7, 30), recency_cap=60,
                                l2=1.0, size_window=90, min_train_days=21):
    """Fitted-classifier hurdle: P(sale on day t | weekday, recent
    frequency, days since last sale) x E[size | sale], per day.

    Unlike `weekly_hurdle`'s single empirical rate, this produces a
    forecast that varies across the horizon by weekday - the only method
    in the benchmark besides `ets` whose forecast is not flat.

    Forward projection freezes the recency/frequency features at their
    end-of-training values and only advances the weekday bucket on
    schedule. Letting those features respond to the model's OWN
    predicted probabilities across the horizon would compound a guess
    about which future days sell into the features that predict the
    next one - freezing avoids that feedback loop, at the cost of not
    letting the classifier's own optimism/pessimism about early horizon
    days inform later ones.
    """
    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        n = t.size

        window = t[-size_window:] if t.size >= size_window else t
        nz_window = window[window > 0]
        size_hat = float(nz_window.mean()) if nz_window.size else 0.0

        if n < min_train_days:
            # Too little history to fit a multi-feature model responsibly;
            # fall back to the plain empirical daily rate x size.
            rate = float(np.mean(t > 0)) if n else 0.0
            return _clip(np.full(horizon, rate * size_hat))

        X = _design_matrix(t, rate_windows, recency_cap, offset=0)
        y = (t > 0).astype(float)
        w = _fit_logistic(X, y, l2=l2)

        last_row = X[-1]
        Xf = np.tile(last_row, (horizon, 1))
        wd = np.zeros((horizon, 7))
        wd[np.arange(horizon), (n + np.arange(horizon)) % 7] = 1.0
        Xf[:, :7] = wd

        z = np.clip(Xf @ w, -30, 30)
        p_sale = 1.0 / (1.0 + np.exp(-z))

        return _clip(p_sale * size_hat)

    _f.__name__ = f"logistic_hurdle(rw={rate_windows})"
    return _f


# ---- pooled logistic hurdle -----------------------------------------
# docs/SPARSE_DEMAND_EXPERIMENTS.md section 2 diagnosed logistic_hurdle's
# loss to the empirical weekly_hurdle as thin per-SKU history - "too
# little signal for a 10-parameter model to learn weekday/recency effects
# from without fitting noise instead" - and named the fix it did not
# attempt: "pooling features across similar SKUs (or at least within an
# FSN tier) ... so a slow-moving SKU can borrow statistical strength from
# others like it". This is that model.
#
# WHICH half gets pooled, and why only that half:
#
#   probability  P(sale on day t | weekday, trailing 7/30d sale rate,
#                days since last sale) - POOLED. Every feature is already
#                scale-free (a one-hot, two rates in [0,1], a capped
#                recency in [0,1]), so rows from a plushie and a tumbler
#                are directly comparable and one shared classifier can be
#                fit across all of them.
#   size         E[units | it sells] - kept PER SKU. That one is pure
#                scale: a P2,000 tumbler and a P30 sticker sell in
#                different quantities, and averaging them would be
#                meaningless.
#
# The pooled classifier does NOT collapse every SKU onto one probability:
# the trailing-rate and recency features are computed from each SKU's own
# history, so what is shared is the MAPPING from "how active has this item
# been lately" to "how likely is it to sell tomorrow" - learned once from
# every item in the group instead of separately from each item's handful
# of sale events. That mapping is exactly what a thin per-SKU fit cannot
# estimate, and exactly what generalises across items in a group.
#
# Interface note: this returns a callable taking train_by_sku (dict
# sku -> that SKU's training slice) rather than one SKU's array, matching
# forecasting.ml_models.pooled_fit_predict. It is deliberately NOT a
# `fit_predict(train, horizon)`, because pooling is precisely the thing
# that interface cannot express - see the section-2 note above.

def pooled_logistic_hurdle_fit_predict(rate_windows=(7, 30), recency_cap=60,
                                       l2=1.0, size_window=90,
                                       min_train_days=21, min_pooled_rows=200):
    """One shared sale-probability classifier per GROUP, each SKU's own
    size estimate. Returns f(train_by_sku, horizon) -> {sku: predictions}."""
    def _f(train_by_sku, horizon):
        rows_X, rows_y = [], []
        state = {}       # sku -> (last design row or None, n_train, size_hat)

        for sku, values in train_by_sku.items():
            t = np.asarray(values, dtype=float).ravel()
            n = t.size

            window = t[-size_window:] if n >= size_window else t
            nz = window[window > 0]
            size_hat = float(nz.mean()) if nz.size else 0.0

            if n < min_train_days:
                state[sku] = (None, n, size_hat)
                continue

            X = _design_matrix(t, rate_windows, recency_cap, offset=0)
            rows_X.append(X)
            rows_y.append((t > 0).astype(float))
            state[sku] = (X[-1], n, size_hat)

        pooled_rows = sum(x.shape[0] for x in rows_X)
        weights = None
        if rows_X and pooled_rows >= min_pooled_rows:
            weights = _fit_logistic(np.vstack(rows_X), np.concatenate(rows_y), l2=l2)

        out = {}
        for sku, (last_row, n, size_hat) in state.items():
            if weights is None or last_row is None:
                # Same fallback the per-SKU model uses: flat empirical
                # daily rate x size.
                t = np.asarray(train_by_sku[sku], dtype=float).ravel()
                rate = float(np.mean(t > 0)) if t.size else 0.0
                out[sku] = _clip(np.full(horizon, rate * size_hat))
                continue

            Xf = np.tile(last_row, (horizon, 1))
            wd = np.zeros((horizon, 7))
            wd[np.arange(horizon), (n + np.arange(horizon)) % 7] = 1.0
            Xf[:, :7] = wd

            z = np.clip(Xf @ weights, -30, 30)
            p_sale = 1.0 / (1.0 + np.exp(-z))
            out[sku] = _clip(p_sale * size_hat)
        return out

    _f.__name__ = f"pooled_logistic_hurdle(rw={rate_windows})"
    return _f


# ---- calendar-aware logistic hurdle -----------------------------------
# The full-catalogue diagnostics (2026-09-08 session) found that neither
# sparsity nor product category explains the accuracy ceiling - a
# synthetic-world simulation showed these methods handle sparse-but-STABLE
# demand fine, and aggregating real SKUs into bigger, denser series didn't
# move MASE at all. What's left is that the demand RATE itself shifts -
# semester cycles, exam weeks, breaks - and none of the project's models
# are told when in the academic year a given training/forecast day falls.
# Dim_Date already carries that as real, populated columns
# (is_enrollment_period, is_exam_week, is_event_day, is_sem_break) that
# nothing in forecasting/ had ever read.
#
# This adds those four flags as extra logistic-regression columns,
# alongside logistic_hurdle's existing weekday/recency/rate features.
#
# How a per-SKU array sees the calendar without ever holding a date
# --------------------------------------------------------------------
# `fit_predict(train, horizon)` receives only the value array - the same
# constraint _weekday_onehot works under (recovering weekday from array
# POSITION because every SKU's series starts on the same calendar day, per
# model_benchmark.load_daily_series). calendar_features here is that same
# trick generalised: a (n_total_days, n_flags) array built ONCE from
# Dim_Date for the exact date range every series shares, then looked up by
# position exactly like weekday is - calendar_features[:n] for the
# training rows, calendar_features[n:n+horizon] for the forecast horizon.

def calendar_logistic_hurdle_fit_predict(calendar_features, rate_windows=(7, 30),
                                         recency_cap=60, l2=1.0,
                                         size_window=90, min_train_days=21):
    """logistic_hurdle_fit_predict + calendar_features as extra columns.

    calendar_features: 2D array, one row per day of the SAME shared
    calendar every SKU's series is built on, one column per calendar flag
    (e.g. is_enrollment_period/is_exam_week/is_event_day/is_sem_break).
    Looked up by array position - see the module note above for why that
    is safe without ever seeing an actual date.
    """
    calendar_features = np.asarray(calendar_features, dtype=float)

    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        n = t.size

        window = t[-size_window:] if t.size >= size_window else t
        nz_window = window[window > 0]
        size_hat = float(nz_window.mean()) if nz_window.size else 0.0

        if n < min_train_days:
            rate = float(np.mean(t > 0)) if n else 0.0
            return _clip(np.full(horizon, rate * size_hat))

        base = _design_matrix(t, rate_windows, recency_cap, offset=0)
        X = np.hstack([base, calendar_features[:n]])
        y = (t > 0).astype(float)
        w = _fit_logistic(X, y, l2=l2)

        last_row = np.hstack([base[-1], calendar_features[n - 1]])
        Xf = np.tile(last_row, (horizon, 1))
        wd = np.zeros((horizon, 7))
        wd[np.arange(horizon), (n + np.arange(horizon)) % 7] = 1.0
        Xf[:, :7] = wd
        n_base = base.shape[1]
        Xf[:, n_base:] = calendar_features[n:n + horizon]

        z = np.clip(Xf @ w, -30, 30)
        p_sale = 1.0 / (1.0 + np.exp(-z))
        return _clip(p_sale * size_hat)

    _f.__name__ = f"calendar_logistic_hurdle(rw={rate_windows})"
    return _f
