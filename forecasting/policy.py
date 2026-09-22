"""
forecasting/policy.py
------------------------------------------------------------------
The forecast -> prescriptive contract, as four pure functions.

What this module is for
-----------------------
The prescriptive layer does not consume a 30-day point forecast. It
consumes a demand RATE and an UNCERTAINTY, and the thing that has to be
reliable is the POLICY those two imply - whether the reorder point it
produces meets demand at acceptable holding - not the point accuracy of
any forecast. `docs/DEGENERATE_FORECAST.md` is why: on this catalogue the
error-minimising forecast is zero, and the method that wins MASE
(`rolling_median_30`) prices 0 of 266 SKUs. Optimising a point forecast
harder walks further into that trap, not out of it.

So this module supplies:

    resolve_rates       demand rate per SKU, with a stated SOURCE and an
                        explicit "no rate" state - never a silent zero
    cluster_rates       the pooled fallback rate, from behavioural
                        clusters, for SKUs whose own rate is too thin
    policy_fold_errors  the policy's OWN backtest errors, so its buffer
                        is sized from what it actually got wrong
    empirical_buffer    the buffer, as a quantile of those errors

Nothing here touches a database, a CSV, or a model. It is all arrays in,
arrays out, so it is testable without ustore.db - the same split
forecasting/evaluate.py and forecasting/category.py already use.

Three decisions, recorded here so they are not silently reopened
----------------------------------------------------------------

**1. Three rate states, not two.** `observed` / `cluster_pooled` /
`insufficient_data`. The third is the point. `step5_prescriptive.py` used
to `continue` past any SKU whose trailing window summed to zero, which
dropped 58 of 266 eligible SKUs out of the output entirely - they did not
appear as "unknown", they simply were not there, and the screen showed
nothing where a recommendation should have been. A SKU with no learnable
rate now gets a ROW with `insufficient_data` and no number. The reliable
output for a first-season design or a genuinely dead line IS the flag.

**2. The fallback serves THIN rates, not MISSING ones.** This is a
correction to the original framing, made from measurement rather than
assumed. Under the ratified trailing-365-day basis:

  - 58 of 266 eligible SKUs have no rate at all, and every one of them
    last sold between 2024-05-07 and 2025-07-21 - all silent for over a
    year. Borrowing a cluster rate for those would INVENT demand for
    items that have demonstrated a year of zero.
  - 112 SKUs first sold within the trailing year, and all 112 already
    have a positive rate. The "new SKU whose window cannot be filled"
    case, which the fallback was originally motivated by, is empty here.
  - 36 SKUs are priced off fewer than 10 units across the whole trailing
    year (~0.027/day; under an 18-day lead time, half a unit). Their rate
    exists but carries almost no evidence.

That last group is what pooling is for. Shrinking a thin rate toward its
behavioural cluster is variance reduction on a real estimate; replacing a
year of silence with a cluster average is fabrication. So the fallback
applies to the thin, and the silent get the flag.

**3. The buffer is generated at LEAD-TIME horizon.**
`tools/service_frontier.py` computes its empirical quantile from
`rolling_mean_30`'s 30-day-aggregate errors. That is the right unit for
the benchmark's periodic-review simulation and the wrong one here: this
policy is forecast-free (the rate is trailing observed) and its reorder
point is continuous-review, `rate * L + buffer`, so the quantity at risk
is demand over ONE LEAD TIME. Taking a 30-day quantile and scaling it by
sqrt(L/30) would reintroduce exactly the distributional assumption the
move to an empirical quantile exists to remove - and on a right-skewed
intermittent series that scaling is wrong in the direction that
under-sizes. Running the backtest at horizon = the SKU's own lead time
puts the quantile directly in the unit the reorder point needs, with no
scaling step at all.

The knee's LOCATION (q ~= 0.80) is imported from the benchmark frontier
as a default, not a finding of this module.
`scripts/validate_policy_holdout.py` re-measures it on this policy's own
curve so the default is checked rather than asserted.
------------------------------------------------------------------
"""
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from .evaluate import DEFAULT_MIN_FOLDS, DEFAULT_MIN_TRAIN, make_folds

__all__ = [
    "OBSERVED", "CLUSTER_POOLED", "INSUFFICIENT", "RATE_SOURCES",
    "DEFAULT_WINDOW", "DEFAULT_MIN_SALE_DAYS", "DEFAULT_BUFFER_QUANTILE",
    "DEFAULT_CLUSTER_K", "MAX_FOLDS",
    "trailing_window", "observed_days", "resolve_rates", "cluster_rates",
    "policy_fold_errors", "empirical_buffer", "trailing_rate_fn",
    "SERVABLE", "PARTIAL", "NOT_STOCKABLE", "SERVICE_TIERS", "TIER_QUANTILES",
    "DEFAULT_TIER_TARGET", "DEFAULT_TIER_MIN_EFFICIENCY", "DEFAULT_TIER_MAX_Q",
    "tier_curve", "achievable_fill", "assign_service_tier",
]

OBSERVED = "observed"
CLUSTER_POOLED = "cluster_pooled"
INSUFFICIENT = "insufficient_data"
RATE_SOURCES = (OBSERVED, CLUSTER_POOLED, INSUFFICIENT)

DEFAULT_WINDOW = 365            # days; the ratified trailing basis (remediation S1)
DEFAULT_MIN_SALE_DAYS = 10      # == step5_prescriptive.MIN_SALE_DAYS_FOR_SIGMA
DEFAULT_BUFFER_QUANTILE = 0.80  # the frontier knee, as a DEFAULT - see module docstring
DEFAULT_CLUSTER_K = 4           # matches data/model_benchmark_category_breakdown_cluster4.csv
MAX_FOLDS = 12                  # == model_benchmark.py / step4_forecast_model.py


def trailing_window(values: Sequence[float], window: int = DEFAULT_WINDOW,
                    upto: Optional[int] = None) -> np.ndarray:
    """The last `window` days of `values`, ending strictly before `upto`.

    `upto` is the leakage guard, and it is positional into the shared
    calendar index every series is reindexed onto - so `upto=None` means
    "through the end of history" (deployment) and `upto=origin` means
    "as the policy would have seen it at that origin" (backtest). A
    caller that forgets it gets deployment semantics, which is why every
    backtest path in this module passes it explicitly.
    """
    v = np.asarray(values, dtype=float).ravel()
    end = v.size if upto is None else int(upto)
    if end <= 0:
        return np.empty(0, dtype=float)
    return v[max(0, end - window):end]


def observed_days(observed: Optional[Sequence[bool]], window: int,
                  upto: Optional[int], n_total: int) -> int:
    """How many days in this trailing window are actually EVIDENCE.

    A day counts as observed when the store was recorded selling (a
    `Fact_Sales` row exists for it) or was recorded shut (`is_sem_break`
    or `is_store_closed`). Anything else is a day nobody wrote anything
    down about, and the pipeline used to call those zero demand.

    Measured on this catalogue: 139 of 821 calendar days - 16.9% - are
    zero-filled with no evidence the store was shut, and the store
    demonstrably trades seven days a week (79 Saturdays and 76 Sundays
    are tallied). Dividing units by 365 when only 357 of those days were
    observed understates the rate. It is a measurement error, not a
    modelling choice, and it biases every rate in the same direction.

    Falls back to `window` when no mask is supplied (the pre-correction
    behaviour, reachable via --zero-fill) or when a window somehow
    contains no observed day at all - a zero denominator would be worse
    than a slightly low rate.
    """
    if observed is None:
        return window
    end = n_total if upto is None else int(upto)
    if end <= 0:
        return window
    mask = np.asarray(observed, dtype=bool).ravel()[max(0, end - window):end]
    n = int(mask.sum())
    return n if n > 0 else window


def trailing_rate_fn(window: int = DEFAULT_WINDOW,
                     observed: Optional[Sequence[bool]] = None):
    """`train -> units/day`, the deployed policy's own predictor.

    Deliberately the SAME shape as forecasting/baselines.py's fit_predict
    factories so it can be fed to the walk-forward harness, but it returns
    a RATE rather than `horizon` daily predictions - the prescriptive
    layer's input is a rate, and converting to a horizon total is the
    caller's job (`policy_fold_errors` does it).

    `observed` is the full-series evidence mask. `train` is always a
    PREFIX of the series - every caller passes `values[:origin]` - so
    `len(train)` locates the window inside the mask without widening the
    callable's signature, which is what lets the harness keep treating
    this as an ordinary one-argument model.
    """
    def _fn(train: np.ndarray) -> float:
        w = trailing_window(train, window)
        if not w.size:
            return 0.0
        denom = observed_days(observed, window, len(train),
                              len(train) if observed is None else len(observed))
        return float(w.sum()) / float(denom)
    return _fn


def cluster_rates(series: Dict[object, Sequence[float]],
                  prices: Dict[object, Optional[float]],
                  k: int = DEFAULT_CLUSTER_K,
                  *, upto: Optional[int] = None,
                  window: int = DEFAULT_WINDOW,
                  observed: Optional[Sequence[bool]] = None,
                  seed: int = 0):
    """(sku -> cluster label, sku -> leave-one-out pooled units/day).

    The pooled rate is the MEDIAN trailing rate over the cluster's members
    that have one, not the mean: these distributions are right-skewed and
    a single high-volume member would otherwise drag every thin SKU in its
    cluster upward. Members with a zero trailing window contribute nothing
    to the median - a cluster's rate describes what its LIVE members sell,
    not what its dead ones do not.

    `upto` slices every series before featurising, the same guard
    scripts/model_benchmark_category.py:build_group_fn applies to its
    fold-scoped cluster labels. Without it a validation run would fit its
    clusters on data including the window it is about to be scored on,
    which is the leak docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's
    correction section is about.

    Imports sklearn indirectly (forecasting/clustering.py). That is a
    declared runtime dependency as of this module - see
    requirements/requirements.txt.

    **Leave-one-out.** A SKU's pooled rate is the median of the OTHER
    members of its cluster, never of a set including itself. Borrowing
    strength means borrowing from somebody else: a SKU pooled with its own
    value in the set is partly shrunk toward itself, and an outlier that
    K-means isolates into a singleton cluster would otherwise be "pooled"
    toward its own rate and labelled `cluster_pooled` while nothing was
    actually pooled. That mislabels a no-op as a correction, and it is the
    kind of thing that quietly makes a component look harmless when it is
    really absent. A singleton gets no pooled rate at all, and
    resolve_rates keeps its own evidence instead.

    Returns (sku -> label, sku -> leave-one-out pooled rate). The second
    map is keyed by SKU, not by label, precisely because of this.
    """
    from .clustering import cluster_skus, sku_features

    end = None if upto is None else int(upto)
    sliced = {sku: np.asarray(v, dtype=float).ravel()[:end] for sku, v in series.items()}

    feat = sku_features(sliced, prices)
    labels, _km, _scaler = cluster_skus(feat, k=k, seed=seed)

    n_obs = observed_days(observed, window, end, len(next(iter(series.values()), [])))
    by_label: Dict[str, Dict[object, float]] = {}
    for sku, values in sliced.items():
        w = trailing_window(values, window)
        rate = float(w.sum()) / float(n_obs) if w.size else 0.0
        if rate > 0:
            by_label.setdefault(labels[sku], {})[sku] = rate

    pooled: Dict[object, float] = {}
    for sku in sliced:
        members = by_label.get(labels[sku], {})
        others = [r for other, r in members.items() if other != sku]
        if others:
            pooled[sku] = float(np.median(others))
    return labels, pooled


def resolve_rates(series: Dict[object, Sequence[float]],
                  prices: Dict[object, Optional[float]],
                  *, window: int = DEFAULT_WINDOW,
                  min_sale_days: int = DEFAULT_MIN_SALE_DAYS,
                  shrink: bool = True,
                  k: int = DEFAULT_CLUSTER_K,
                  upto: Optional[int] = None,
                  observed: Optional[Sequence[bool]] = None,
                  seed: int = 0) -> Dict[object, dict]:
    """sku -> {rate, rate_source, sale_days, own_rate, cluster, cluster_rate, shrink_weight}.

    The rule, in full:

      trailing sum == 0                  -> INSUFFICIENT, rate None
      trailing sum > 0, sale_days >= min -> OBSERVED,      rate = own
      trailing sum > 0, sale_days <  min -> CLUSTER_POOLED,
                                            rate = w*own + (1-w)*cluster,
                                            w = sale_days / min_sale_days

    The shrinkage is linear in the evidence the SKU actually has, and it
    is continuous at the boundary: a SKU with exactly `min_sale_days`
    sale days has w = 1 and is left alone, so OBSERVED and CLUSTER_POOLED
    do not disagree about the SKU sitting between them. `shrink=False`
    disables the pooling entirely and reproduces the pre-contract
    behaviour for every SKU that had a rate at all, which is what makes
    the shrinkage an isolated, measurable variable rather than a change
    tangled up with the flag.

    A cluster whose own pooled rate is absent or zero does NOT get used:
    the SKU keeps its own thin rate and stays OBSERVED-by-fallback rather
    than being shrunk toward nothing. Shrinking toward zero is the
    degeneracy this whole contract exists to keep out of the
    prescription, and it must not sneak back in through the pooling step.
    """
    out: Dict[object, dict] = {}

    need_clusters = shrink and any(
        0 < int(np.count_nonzero(trailing_window(v, window, upto) > 0)) < min_sale_days
        for v in series.values())

    labels: Dict[object, str] = {}
    pooled: Dict[str, float] = {}
    if need_clusters:
        labels, pooled = cluster_rates(series, prices, k=k, upto=upto,
                                       window=window, observed=observed, seed=seed)

    for sku, values in series.items():
        w = trailing_window(values, window, upto)
        total = float(w.sum())
        sale_days = int(np.count_nonzero(w > 0))
        n_obs = observed_days(observed, window, upto, len(values))
        own = total / float(n_obs) if n_obs > 0 else 0.0

        rec = {"rate": None, "rate_source": INSUFFICIENT, "sale_days": sale_days,
               "own_rate": own, "cluster": labels.get(sku),
               "cluster_rate": None, "shrink_weight": None,
               "observed_days": n_obs, "window_days": window}

        if total <= 0:
            out[sku] = rec                      # the flag: no rate, not a zero
            continue

        if not shrink or sale_days >= min_sale_days:
            rec.update(rate=own, rate_source=OBSERVED)
            out[sku] = rec
            continue

        cluster_rate = pooled.get(sku)
        if not cluster_rate or cluster_rate <= 0:
            # Nothing to borrow - an empty cluster, a singleton, or one whose
            # other members have no rate either. Keep the SKU's own evidence
            # rather than shrinking it toward nothing, and do NOT label it
            # cluster_pooled: nothing was pooled.
            rec.update(rate=own, rate_source=OBSERVED)
            out[sku] = rec
            continue

        weight = min(1.0, sale_days / float(min_sale_days))
        rec.update(rate=weight * own + (1.0 - weight) * cluster_rate,
                   rate_source=CLUSTER_POOLED,
                   cluster_rate=cluster_rate, shrink_weight=weight)
        out[sku] = rec

    return out


def policy_fold_errors(values: Sequence[float], horizon: int,
                       rate_fn: Callable[[np.ndarray], float],
                       *, min_folds: int = DEFAULT_MIN_FOLDS,
                       max_folds: Optional[int] = MAX_FOLDS,
                       min_train: int = DEFAULT_MIN_TRAIN,
                       upto: Optional[int] = None) -> np.ndarray:
    """The policy's own errors, `actual - predicted`, one per fold.

    Folds come from forecasting.evaluate.make_folds at the project's
    standard settings, so this inherits the leakage guarantee rather than
    restating it: each fold's training slice ends strictly before its
    origin and `Fold.assert_no_leakage` re-checks it. The prediction at
    each origin is `rate_fn(train) * horizon` - what the DEPLOYED policy
    would have committed to at that moment, not what a model fitted with
    hindsight would say.

    `horizon` is the SKU's lead time, not 30. See the module docstring:
    the reorder point is continuous-review, so the quantity at risk is
    demand over one lead time and the errors must be measured in that
    unit if their quantile is to be used as the buffer without a scaling
    assumption.

    Returns an empty array when the series cannot support `min_folds`
    folds - the caller decides what that means, exactly as
    walk_forward_evaluate reports insufficiency instead of scoring what
    it can.
    """
    v = np.asarray(values, dtype=float).ravel()
    if upto is not None:
        v = v[:int(upto)]

    folds = make_folds(v.size, horizon, min_folds, max_folds, min_train)
    if not folds:
        return np.empty(0, dtype=float)

    errors = np.empty(len(folds), dtype=float)
    for i, f in enumerate(folds):
        f.assert_no_leakage(v.size)
        pred = float(rate_fn(f.train_slice(v))) * horizon
        errors[i] = float(f.test_slice(v).sum()) - pred
    return errors


def empirical_buffer(errors: Sequence[float], q: float = DEFAULT_BUFFER_QUANTILE,
                     *, upto_fold: Optional[int] = None) -> float:
    """The q-quantile of under-prediction, floored at zero.

    Identical in rule to tools/service_frontier.py's frontier estimator,
    deliberately: the two must be the same buffer or the operating point
    measured on the benchmark's curve says nothing about this one. Errors
    are `actual - predicted`, so a positive error is demand the policy
    did not plan for and the quantile of those is the stock needed to
    have covered them. A negative quantile (the policy over-predicts at
    this q) floors at zero - a buffer is never negative stock.

    `upto_fold` makes it expanding-window for a backtest: the buffer for
    fold i uses only errors from folds strictly before i. Fold 0 has no
    prior errors and gets a zero buffer - the bare rate-implied stock.
    That is service_frontier.py's documented choice, restated here rather
    than re-decided, so the two remain comparable. In DEPLOYMENT
    `upto_fold` is left None: every fold is in the past by then and all
    of them are legitimately available.
    """
    e = np.asarray(errors, dtype=float).ravel()
    if upto_fold is not None:
        e = e[:int(upto_fold)]
    if e.size == 0:
        return 0.0
    return float(max(np.quantile(e, q), 0.0))


# ==================================================================== tiers ==
# Service tiers: how much service this SKU's demand will ACCEPT, and whether
# buying it is worth the stock - both measured before any is bought.
#
# The reason this exists, in one measurement. Sorting the scored population
# into trailing-volume quintiles and sweeping the buffer quantile gives:
#
#     quintile   fill @ q=0.80   fill @ q=0.95   share of demand
#     Q5 (high)      0.813           0.951             50%
#     Q4             0.706           0.827             14%
#     Q3             0.389           0.681             21%
#     Q2             0.280           0.525             10%
#     Q1 (low)       0.117           0.288              6%
#
# Service converts into stock about SEVEN TIMES more efficiently at the top of
# that table than at the bottom, and the bottom does not reach 0.38 at ANY
# quantile. A single population-wide q therefore does two wrong things at
# once: it under-serves the half of demand that could reach 0.95, and it
# charges holding to chase SKUs no reorder point can serve.
#
# **Tiering is on EFFICIENCY, not on fill.** This was measured, not assumed,
# and the two disagree sharply. Writing off every SKU that cannot reach 50%
# fill discards 54 SKUs carrying **28.5%** of holdout demand - far too much to
# call made-to-order. Writing off every SKU that cannot return 0.10 units
# served per unit held discards a similar 52 SKUs carrying **1.8%**. The
# difference is the SKUs that are hard to serve but CHEAP to serve: low fill
# on almost no stock. Fill alone cannot see them, and the stated objective -
# maximise demand met per unit of stock - is about efficiency anyway.
#
# A SKU below the efficiency floor at every quantile is not a tuning problem.
# It is a made-to-order item, and saying so is worth more than spending stock
# to pretend otherwise.

SERVABLE = "servable"
PARTIAL = "partial"
NOT_STOCKABLE = "not_stockable"
SERVICE_TIERS = (SERVABLE, PARTIAL, NOT_STOCKABLE)

# Quantile sweep for tier assignment. Same grid tools/service_frontier.py and
# scripts/validate_policy_holdout.py use, so a tier's chosen q is a point on
# the same curve the report prints - not a separate scale to reconcile.
TIER_QUANTILES = (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98, 0.99)

DEFAULT_TIER_TARGET = 0.90      # a SKU reaching this is worth serving properly
# Units served per unit held, below which stock is not worth committing.
# Measured: the p25 of the population's own pre-origin efficiency is 0.094, so
# this floor sits just above the least-efficient quarter and costs 1.8% of
# demand. It does double duty below - it decides both "not worth stocking at
# all" and "not worth stocking FURTHER" - so there is one number to defend
# rather than two.
DEFAULT_TIER_MIN_EFFICIENCY = 0.10
# Cap on what will be SPENT, not on what is measured: the frontier's marginal
# holding cost runs away above here (16.5 units held per extra unit served at
# q=0.98, against 5.0 at q=0.80).
DEFAULT_TIER_MAX_Q = 0.95


def tier_curve(values: Sequence[float], horizon: int,
               rate_fn: Callable[[np.ndarray], float],
               *, upto: Optional[int] = None,
               quantiles: Sequence[float] = TIER_QUANTILES,
               min_folds: int = DEFAULT_MIN_FOLDS,
               max_folds: Optional[int] = MAX_FOLDS,
               min_train: int = DEFAULT_MIN_TRAIN) -> List[dict]:
    """[{q, served, held, demand, fill, efficiency}] on this SKU's OWN folds.

    Expanding window throughout: fold i is served by a buffer taken from folds
    strictly before it, the same rule `empirical_buffer` applies in
    deployment. So this is an honest pre-origin estimate of what this SKU's
    policy covers, and what it costs to cover it - the only thing tier
    assignment is allowed to see.

    Returns [] when the series cannot support folds, or when the folds it does
    support carry no demand: a fill rate over zero demand is 0/0, and
    returning 0.0 there would tier a SKU as unservable on the strength of
    having had nothing to serve.
    """
    v = np.asarray(values, dtype=float).ravel()
    if upto is not None:
        v = v[:int(upto)]

    folds = make_folds(v.size, horizon, min_folds, max_folds, min_train)
    if not folds:
        return []

    out = []
    for q in quantiles:
        served = held = demand = 0.0
        errors: List[float] = []
        for f in folds:
            f.assert_no_leakage(v.size)
            pred = float(rate_fn(f.train_slice(v))) * horizon
            buf = empirical_buffer(errors, q) if errors else 0.0
            committed = max(pred + buf, 0.0)

            actual = float(f.test_slice(v).sum())
            demand += actual
            served += min(actual, committed)
            held += max(0.0, committed - actual)
            errors.append(actual - pred)   # AFTER scoring: fold i never sees itself

        if demand <= 0:
            return []
        out.append({"q": q, "served": served, "held": held, "demand": demand,
                    "fill": served / demand,
                    "efficiency": (served / held) if held > 0 else float("inf")})
    return out


def achievable_fill(values: Sequence[float], horizon: int,
                    rate_fn: Callable[[np.ndarray], float], q: float,
                    **kw) -> Optional[float]:
    """Fill rate this SKU's own policy would have achieved at buffer `q`, on
    its own pre-origin folds. Thin wrapper over `tier_curve` so the two can
    never drift apart; None when there is nothing scoreable."""
    for row in tier_curve(values, horizon, rate_fn, quantiles=(q,), **kw):
        return row["fill"]
    return None


def assign_service_tier(values: Sequence[float], horizon: int,
                        rate_fn: Callable[[np.ndarray], float],
                        *, upto: Optional[int] = None,
                        target: float = DEFAULT_TIER_TARGET,
                        min_efficiency: float = DEFAULT_TIER_MIN_EFFICIENCY,
                        max_q: float = DEFAULT_TIER_MAX_Q,
                        quantiles: Sequence[float] = TIER_QUANTILES,
                        default_q: float = DEFAULT_BUFFER_QUANTILE) -> dict:
    """{tier, q, best_fill, fill_at_q, efficiency_at_q} for one SKU, pre-origin.

        some q <= max_q reaches `target` fill  -> SERVABLE, at the SMALLEST such q
        best efficiency >= `min_efficiency`    -> PARTIAL, at its own marginal knee
        best efficiency <  `min_efficiency`    -> NOT_STOCKABLE (flag, stock minimally)
        no scoreable folds                     -> PARTIAL at `default_q`, conservatively

    Three details that matter:

    **The smallest qualifying q, not the largest.** Service beyond the target
    is holding cost with nothing bought - if q=0.75 already reaches the target
    on this SKU's own history, q=0.95 buys stock, not availability. On the real
    catalogue 16 of 21 servable SKUs settle at q=0.50 for exactly this reason.

    **PARTIAL stops at its MARGINAL knee, not at its best fill.** Maximising
    fill regardless of cost is the behaviour the tiering exists to prevent. The
    walk up the curve stops at the last step whose *marginal* return still
    clears `min_efficiency`, so the same floor decides "not worth stocking" and
    "not worth stocking further" - one number to defend, not two.

    **`max_q` caps spending, not measurement.** The sweep still runs above it
    so `best_fill` is honest about what the SKU could reach; confusing the two
    would silently relabel an expensive SKU as unservable.
    """
    curve = tier_curve(values, horizon, rate_fn, upto=upto, quantiles=quantiles)

    if not curve:
        # No scoreable folds - a new or very short series. That is absence of
        # evidence, not evidence of unservability, so it gets the default
        # operating point and the middle tier rather than being written off.
        return {"tier": PARTIAL, "q": default_q, "best_fill": None,
                "fill_at_q": None, "efficiency_at_q": None}

    by_q = {row["q"]: row for row in curve}
    best_fill = max(row["fill"] for row in curve)

    for row in curve:                      # ascending q: first hit is the smallest
        if row["q"] <= max_q and row["fill"] >= target:
            return {"tier": SERVABLE, "q": row["q"], "best_fill": best_fill,
                    "fill_at_q": row["fill"], "efficiency_at_q": row["efficiency"]}

    if max(row["efficiency"] for row in curve) < min_efficiency:
        return {"tier": NOT_STOCKABLE, "q": None, "best_fill": best_fill,
                "fill_at_q": None, "efficiency_at_q": None}

    # Walk up while each additional step still returns at least the floor rate.
    chosen = curve[0]["q"]
    for prev, nxt in zip(curve, curve[1:]):
        if nxt["q"] > max_q:
            break
        d_served, d_held = nxt["served"] - prev["served"], nxt["held"] - prev["held"]
        marginal = (d_served / d_held) if d_held > 0 else float("inf")
        if marginal < min_efficiency:
            break
        chosen = nxt["q"]

    row = by_q[chosen]
    return {"tier": PARTIAL, "q": chosen, "best_fill": best_fill,
            "fill_at_q": row["fill"], "efficiency_at_q": row["efficiency"]}
