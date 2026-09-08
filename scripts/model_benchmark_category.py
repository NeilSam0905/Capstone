"""
scripts/model_benchmark_category.py
------------------------------------------------------------------
Adviser's suggestion: categorize items into apparel / non-apparel and
check whether that improves the tree-based ML baselines
(xgboost, lightgbm, random_forest - forecasting/ml_models.py).

model_benchmark_ml.py fits one model PER SKU: for a SKU with a short
history, that model has very little to learn from. This script instead
POOLS training rows across every SKU in a GROUP and fits ONE shared
model per group per fold (forecasting/ml_models.pooled_fit_predict), then
forecasts each SKU recursively from that shared model. `--by` picks how
SKUs are grouped for pooling:

    category        apparel / non-apparel (forecasting/category.classify:
                     Dim_Product.category where set - read-only, never
                     modified here - a keyword match on item_name where
                     it is not)
    speed            fast / slow mover, from Dim_Product.fsn_class - the
                     store's own tag (also read-only), not derived
    category_speed   the cross of both, e.g. "apparel-fast" - the first
                     run (category alone) showed the damage was
                     concentrated in near-flat/slow-moving SKUs, so this
                     tests whether separating fast from slow within each
                     category recovers it

Scored on the SAME walk-forward folds as model_benchmark_ml.py (all
series share one calendar index, so fold origins are identical for
every SKU - see model_benchmark.load_daily_series). `naive` is re-run
here too, only so pct_skus_beating_naive is computed on THIS run's
folds.

EXPERIMENTAL - see requirements/requirements-ml-experimental.txt.

Run (from the repo root):
    python scripts/model_benchmark_category.py [--by category|speed|category_speed]
                                                [--max-folds N] [--limit N]

Writes (filenames carry a suffix for --by speed / --by category_speed, so
different groupings never clobber each other):
    data/model_benchmark_category_results[_SUFFIX].csv     (per SKU, per method, per fold)
    data/model_benchmark_category_summary[_SUFFIX].csv     (ranked summary, pooled methods only)
    data/model_benchmark_category_breakdown[_SUFFIX].csv   (pooled methods, split by group)
    data/model_benchmark_category_comparison[_SUFFIX].csv  (pooled vs. the committed per-SKU models)
------------------------------------------------------------------
"""
import argparse
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_benchmark as mb
from forecasting.baselines import naive_fit_predict
from forecasting.category import classify, classify_product_type, speed_label
from forecasting.evaluate import aggregate_blocks, make_folds, summarise
from forecasting.metrics import naive_scale
from forecasting.hurdle import (
    logistic_hurdle_fit_predict, pooled_logistic_hurdle_fit_predict,
    weekly_hurdle_fit_predict,
)
from forecasting.ml_models import (
    lightgbm_pooled_ctor, pooled_fit_predict, random_forest_pooled_ctor,
    xgboost_pooled_ctor,
)
from tools.synthetic_augment_test import SEED as SYNTHETIC_SEED
from tools.synthetic_augment_test import bootstrap_prehistory

BASE = "data/model_benchmark_category"
COMMITTED_ML_SUMMARY_CSV = "data/model_benchmark_ml_summary.csv"

def _tree_pooled(ctor):
    """Wrap a tree ctor as the uniform pooled interface every pooled
    method here shares: f(train_by_sku, horizon) -> {sku: predictions}."""
    def _f(train_by_sku, horizon):
        return pooled_fit_predict(ctor, train_by_sku, horizon)
    return _f


# method name (this run, suffix stripped) -> f(train_by_sku, horizon) -> {sku: preds}
POOLED_METHODS = {
    "xgboost": _tree_pooled(xgboost_pooled_ctor(n_estimators=50)),
    "lightgbm": _tree_pooled(lightgbm_pooled_ctor(n_estimators=50)),
    "random_forest": _tree_pooled(random_forest_pooled_ctor(n_estimators=50)),
    # docs/SPARSE_DEMAND_EXPERIMENTS.md section 2 asked for exactly this
    "logistic_hurdle": pooled_logistic_hurdle_fit_predict(),
}

# scored per SKU on the SAME folds, so each pooled method can be read
# against its own per-SKU counterpart rather than only against the
# committed tree CSV. naive additionally seeds pct_skus_beating_naive.
PER_SKU_METHODS = {
    "naive": naive_fit_predict(),
    "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
    "logistic_hurdle": logistic_hurdle_fit_predict(),
}

METHOD_SUFFIX = "_pooled_cat"
PER_SKU_SUFFIX = "_per_sku"

GROUPERS = {
    "category": lambda cat, speed: cat,
    "speed": lambda cat, speed: speed,
    "category_speed": lambda cat, speed: f"{cat}-{speed}",
    # "product_type" is handled separately in load_group_map - it needs
    # item_name/db_category directly (forecasting.category.classify_product_type),
    # not the (category, speed) pair the other groupers combine.
    "product_type": None,
}


def load_group_map(con, skus, by):
    """product_id -> group label for every sku in `skus`, per the chosen
    --by grouping. category comes from forecasting.category.classify;
    speed comes from Dim_Product.fsn_class via forecasting.category.speed_label;
    product_type is the TEMPORARY/exploratory finer split (clothes,
    drinkware, bags, ... - forecasting.category.classify_product_type),
    keyword-only since there is no DB column for it. category/fsn_class
    are read-only controlled vocabulary - this only reads them."""
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, category, fsn_class FROM Dim_Product", con)
    prod = prod[prod["product_id"].isin(skus)]

    if by == "product_type":
        return {
            row.product_id: classify_product_type(row.item_name, row.category)
            for row in prod.itertuples()
        }

    grouper = GROUPERS[by]
    return {
        row.product_id: grouper(classify(row.item_name, row.category),
                                speed_label(row.fsn_class))
        for row in prod.itertuples()
    }


def skus_by_group(group_map):
    groups = defaultdict(list)
    for sku, g in group_map.items():
        groups[g].append(sku)
    return dict(groups)


def _real_denom(values, real_offset, train_end, horizon):
    """MASE's denominator, computed from the REAL portion of training only
    (values[real_offset:train_end]), never from any synthetic pre-history
    at values[:real_offset]. See the --synthetic-years note in main() and
    tools/synthetic_augment_all_methods.py's module docstring: scaling
    MASE by a denominator that includes bootstrapped synthetic days
    inflates it with synthetic block-to-block noise and shrinks MASE with
    NO actual forecast improvement - caught empirically because ets and
    rolling_q75_30 produce IDENTICAL predictions with or without synthetic
    data (they only look at a fixed trailing window) yet showed a ~3x MASE
    drop under the naive (buggy) denominator. real_offset=0 (no synthetic
    run) makes this identical to using the whole training slice."""
    blocks = aggregate_blocks(values[real_offset:train_end], horizon)
    return naive_scale(blocks) if blocks.size > 1 else float("nan")


def score_pooled(series, folds, groups, real_offset=0, methods=None):
    """One row per (sku, pooled-method, fold), same schema
    evaluate_methods() produces. Fits ONE model per (method, group,
    fold) - not one per SKU - on rows pooled across that group's SKUs.
    Training uses the FULL series (real, or real+synthetic pre-history);
    the MASE denominator always uses the REAL portion only - see
    _real_denom."""
    rows = []
    for method_name, pooled_fn in POOLED_METHODS.items():
        if methods and method_name not in methods:
            continue
        out_name = method_name + METHOD_SUFFIX
        for group_name, skus in groups.items():
            if not skus:
                continue
            for f in folds:
                train_by_sku = {sku: f.train_slice(series[sku]) for sku in skus}
                preds_by_sku = pooled_fn(train_by_sku, f.horizon)

                for sku in skus:
                    actual_daily = f.test_slice(series[sku])
                    pred = preds_by_sku[sku]

                    actual_agg = float(actual_daily.sum())
                    pred_agg = float(pred.sum())
                    denom = _real_denom(series[sku], real_offset, f.train_end, f.horizon)

                    rows.append({
                        "sku": sku, "method": out_name, "fold": f.fold_index,
                        "origin": f.origin, "n_train": f.n_train,
                        "horizon": f.horizon, "actual_30d": actual_agg,
                        "pred_30d": pred_agg,
                        "abs_error": abs(actual_agg - pred_agg),
                        "naive_scale": denom,
                    })
    return rows


def score_per_sku(series, folds, real_offset=0, methods=None):
    """PER_SKU_METHODS, re-run on THIS run's folds so each pooled method
    can be read against its own per-SKU counterpart on identical windows.
    `naive` keeps its bare name because beats_naive() looks it up by that
    name; everything else gets PER_SKU_SUFFIX so it cannot collide with a
    pooled method of the same name."""
    rows = []
    for method_name, fn in PER_SKU_METHODS.items():
        if methods and method_name not in methods and method_name != "naive":
            continue
        out_name = method_name if method_name == "naive" else method_name + PER_SKU_SUFFIX
        for f in folds:
            for sku, values in series.items():
                train = f.train_slice(values)
                actual_daily = f.test_slice(values)
                pred = np.asarray(fn(train, f.horizon), dtype=float).ravel()

                actual_agg = float(actual_daily.sum())
                pred_agg = float(pred.sum())
                denom = _real_denom(values, real_offset, f.train_end, f.horizon)

                rows.append({
                    "sku": sku, "method": out_name, "fold": f.fold_index,
                    "origin": f.origin, "n_train": f.n_train,
                    "horizon": f.horizon, "actual_30d": actual_agg,
                    "pred_30d": pred_agg, "abs_error": abs(actual_agg - pred_agg),
                    "naive_scale": denom,
                })
    return rows


def skus_priced_pooled(series, groups, horizon, methods=None):
    """How many SKUs each method would actually price, fit on the FULL
    history exactly as skus_priced() does for the per-SKU methods - see
    model_benchmark.skus_priced for why this column matters."""
    out = {}
    for method_name, pooled_fn in POOLED_METHODS.items():
        if methods and method_name not in methods:
            continue
        n = 0
        for skus in groups.values():
            if not skus:
                continue
            train_by_sku = {sku: series[sku] for sku in skus}
            preds = pooled_fn(train_by_sku, horizon)
            n += sum(1 for p in preds.values() if float(np.sum(p)) > 0)
        out[method_name + METHOD_SUFFIX] = n

    for method_name, fn in PER_SKU_METHODS.items():
        if methods and method_name not in methods and method_name != "naive":
            continue
        name = method_name if method_name == "naive" else method_name + PER_SKU_SUFFIX
        out[name] = sum(
            1 for values in series.values()
            if float(np.sum(fn(np.asarray(values, dtype=float), horizon))) > 0)
    return out


def service_metrics_real_offset(results, series, fsn_class, real_offset):
    """mb.service_metrics, but safety-stock sigma is computed from the REAL
    portion of training only (series[sku][real_offset:origin]) - same
    reasoning as _real_denom: synthetic pre-history is bootstrapped i.i.d.
    per weekday and does not carry this SKU's real day-to-day
    autocorrelation, so including it in the volatility estimate would size
    the safety stock off of manufactured noise. Identical to
    mb.service_metrics when real_offset=0."""
    from step5_prescriptive import Z_BY_CLASS

    served = np.zeros(len(results))
    short = np.zeros(len(results))
    held = np.zeros(len(results))
    ss_col = np.zeros(len(results))
    sigma_cache = {}

    for i, (sku, origin, pred, actual) in enumerate(zip(
            results["sku"].to_numpy(), results["origin"].to_numpy(),
            results["pred_30d"].to_numpy(), results["actual_30d"].to_numpy())):
        key = (sku, origin)
        if key not in sigma_cache:
            train = series[sku][real_offset:origin]
            sigma_cache[key] = float(np.std(train, ddof=1)) if train.size > 1 else 0.0
        sigma = sigma_cache[key]

        z = Z_BY_CLASS.get(fsn_class.get(sku), 0.0)
        ss = z * sigma * np.sqrt(mb.SERVICE_RISK_PERIOD)
        stock = max(pred + ss, 0.0)

        ss_col[i] = ss
        served[i] = min(actual, stock)
        short[i] = max(0.0, actual - stock)
        held[i] = max(0.0, stock - actual)

    out = results.copy()
    out["safety_stock"] = ss_col
    out["stock_level"] = np.maximum(out["pred_30d"] + ss_col, 0.0)
    out["units_served"] = served
    out["units_short"] = short
    out["units_held"] = held
    return out


def build_summary(results, series, groups, horizon, fsn_class, real_offset=0,
                  methods=None):
    results = service_metrics_real_offset(results, series, fsn_class, real_offset)

    summary = summarise(results)
    beats = mb.beats_naive(results)
    summary["pct_skus_beating_naive"] = (
        summary["method"].map(beats).astype(float).round(1))

    priced = skus_priced_pooled(series, groups, horizon, methods)
    svc = (results.groupby("method")
                  .agg(units_served=("units_served", "sum"),
                       units_short=("units_short", "sum"),
                       units_held=("units_held", "sum"),
                       demand=("actual_30d", "sum"))
                  .reset_index())
    svc["fill_rate_at_target"] = (svc["units_served"] / svc["demand"]).round(4)
    summary = summary.merge(
        svc[["method", "fill_rate_at_target", "units_short", "units_held"]],
        on="method", how="left")
    summary["n_skus_priced"] = summary["method"].map(priced).astype(int)
    return results, summary


def write_comparison(summary, comparison_csv, variant_label):
    """Each pooled method against its own per-SKU counterpart, one pair per
    method, so 'did pooling help' has a direct answer.

    The counterpart is this run's own per-SKU row (PER_SKU_METHODS, same
    folds, same DB) where one exists - that is the like-for-like
    comparison. For the tree methods, which have no per-SKU counterpart
    scored here, it falls back to the committed
    data/model_benchmark_ml_summary.csv row."""
    cols = ["method", "variant", "mae", "rmse", "mase",
            "pct_skus_beating_naive", "fill_rate_at_target", "n_skus_priced"]

    pooled = summary[summary["method"].str.endswith(METHOD_SUFFIX)].copy()
    pooled["method"] = pooled["method"].str.replace(METHOD_SUFFIX, "", regex=False)
    pooled["variant"] = variant_label

    in_run = summary[summary["method"].str.endswith(PER_SKU_SUFFIX)].copy()
    in_run["method"] = in_run["method"].str.replace(PER_SKU_SUFFIX, "", regex=False)
    in_run["variant"] = "per_sku (this run)"

    need = set(pooled["method"]) - set(in_run["method"])
    committed = pd.read_csv(COMMITTED_ML_SUMMARY_CSV)
    committed = committed[committed["method"].isin(need)].copy()
    committed["variant"] = "per_sku (committed)"

    combined = pd.concat([in_run[cols], committed[cols], pooled[cols]],
                         ignore_index=True)
    combined = combined.sort_values(["method", "variant"]).reset_index(drop=True)

    # per method: did the pooled variant lower MASE against its counterpart
    base = (combined[combined["variant"] != variant_label]
            .set_index("method")["mase"])
    pooled_mase = combined[combined["variant"] == variant_label].set_index("method")["mase"]
    improved = (pooled_mase < base.reindex(pooled_mase.index)).rename("mase_improved")
    combined = combined.merge(improved.reset_index(), on="method", how="left")

    combined.to_csv(comparison_csv, index=False, lineterminator="\n")
    print(f"\nWrote {comparison_csv}\n")
    print("=" * 88)
    print(f"PER-SKU vs. {variant_label.upper()} - same folds, same DB")
    print("=" * 88)
    print(combined[["method", "variant", "mae", "rmse", "mase",
                    "pct_skus_beating_naive", "fill_rate_at_target",
                    "n_skus_priced", "mase_improved"]]
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("=" * 88)
    print(f"mase_improved: True where {variant_label} lowered MASE against "
          "that method's per-SKU counterpart. NOTE mase here is the MEAN "
          "per-SKU MASE, which tools/robust_metric_comparison.py shows is "
          "dominated by a near-zero-denominator tail - read it next to "
          "that script's median/weighted/global columns, not alone.")


def write_breakdown(results, group_map, breakdown_csv):
    """Pooled methods only, split by the group each SKU actually belongs
    to - is performance uniform across groups within the SAME pooled
    model, or concentrated in one of them."""
    pooled = results[results["method"].str.endswith(METHOD_SUFFIX)].copy()
    pooled["group"] = pooled["sku"].map(group_map)

    rows = []
    for (method, group), g in pooled.groupby(["method", "group"]):
        s = summarise(g.assign(method=method))
        if s.empty:
            continue
        row = s.iloc[0].to_dict()
        row["group"] = group
        rows.append(row)

    out = pd.DataFrame(rows)[["method", "group", "mae", "rmse", "mase",
                              "mase_n_skus", "n_skus", "n_folds"]]
    out.to_csv(breakdown_csv, index=False, lineterminator="\n")
    print(f"Wrote {breakdown_csv}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", choices=list(GROUPERS), default="category",
                    help="how to group SKUs for pooling (default: %(default)s)")
    ap.add_argument("--max-folds", type=int, default=mb.MAX_FOLDS)
    ap.add_argument("--limit", type=int, default=None,
                    help="benchmark only the first N SKUs (for a smoke run)")
    ap.add_argument("--synthetic-years", type=int, default=0,
                    help="prepend N years of bootstrapped synthetic pre-history "
                         "to every SKU before pooling (see "
                         "tools/synthetic_augment_test.py and "
                         "docs/SPARSE_DEMAND_EXPERIMENTS.md section 4). "
                         "0 (default) = off, real history only")
    ap.add_argument("--methods", nargs="+", default=None,
                    choices=sorted(set(POOLED_METHODS) | set(PER_SKU_METHODS)),
                    help="only score these methods (default: all). naive is "
                         "always scored - pct_skus_beating_naive needs it")
    ap.add_argument("--tag", default="",
                    help="extra suffix for this run's output filenames, so a "
                         "subset run does not overwrite a full one")
    args = ap.parse_args()

    methods = set(args.methods) if args.methods else None

    suffix = "" if args.by == "category" else f"_{args.by}"
    if args.synthetic_years:
        suffix += f"_syn{args.synthetic_years}y"
    if args.tag:
        suffix += f"_{args.tag}"
    results_csv = f"{BASE}_results{suffix}.csv"
    summary_csv = f"{BASE}_summary{suffix}.csv"
    breakdown_csv = f"{BASE}_breakdown{suffix}.csv"
    comparison_csv = f"{BASE}_comparison{suffix}.csv"
    variant_label = f"pooled_by_{args.by}"
    if args.synthetic_years:
        variant_label += f"_syn{args.synthetic_years}y"

    con = mb.sqlite3.connect(mb.DB_NAME)
    series, names, index = mb.load_daily_series(con, args.limit)
    group_map = load_group_map(con, series.keys(), args.by)
    fsn_class = dict(con.execute(
        "SELECT product_id, fsn_class FROM Dim_Product").fetchall())
    con.close()

    groups = skus_by_group(group_map)
    print(f"Loaded {len(series)} moving SKUs over {len(index)} calendar days "
          f"({index[0].date()} .. {index[-1].date()})")
    print(f"Grouped by --by {args.by}: " +
          ", ".join(f"{len(skus)} {name}" for name, skus in sorted(groups.items())))

    n_total = len(index)
    real_offset = 0
    if args.synthetic_years:
        # Same bootstrap as tools/synthetic_augment_test.py, same seed - a
        # weekday-stratified resample of each SKU's OWN real distribution,
        # PREPENDED (never appended/substituted). make_folds lays out
        # origins backward from the END of the array, so the real end of
        # each series - and therefore every fold's actual_30d target -
        # is unaffected; only the training slice each fold sees grows.
        syn_days = 365 * args.synthetic_years
        rng = np.random.default_rng(SYNTHETIC_SEED)
        start_weekday = int(index[0].dayofweek)
        series = {
            sku: np.concatenate(
                [bootstrap_prehistory(values, start_weekday, syn_days, rng), values])
            for sku, values in series.items()
        }
        n_total = syn_days + len(index)
        real_offset = syn_days
        print(f"+ {syn_days} synthetic pre-history days/SKU "
              f"(seed={SYNTHETIC_SEED}), prepended - real end of series unchanged")
        print("  MASE and safety-stock volatility are scaled off the REAL "
              "portion of training only (real_offset), never the synthetic "
              "days - see _real_denom / service_metrics_real_offset")

    folds = make_folds(n_total, horizon=mb.HORIZON, min_folds=mb.MIN_FOLDS,
                       max_folds=args.max_folds, min_train=mb.MIN_TRAIN)
    if not folds:
        print("No fold layout supports min_folds at this horizon/min_train. "
              "Nothing scored.")
        return 1
    print(f"{len(folds)} folds, horizon {mb.HORIZON}d "
          f"(shared across every SKU - see model_benchmark.load_daily_series)\n")

    t0 = time.time()
    rows = score_pooled(series, folds, groups, real_offset, methods)
    rows += score_per_sku(series, folds, real_offset, methods)
    elapsed = time.time() - t0

    results = pd.DataFrame(rows, columns=[
        "sku", "method", "fold", "origin", "n_train", "horizon",
        "actual_30d", "pred_30d", "abs_error", "naive_scale"])

    print(f"Scored {results['sku'].nunique()} SKUs x "
          f"{results['method'].nunique()} methods "
          f"in {elapsed:.1f}s ({len(results):,} rows)")

    results, summary = build_summary(results, series, groups, mb.HORIZON,
                                     fsn_class, real_offset, methods)
    results["item_name"] = results["sku"].map(names)
    results["group"] = results["sku"].map(group_map)
    results.to_csv(results_csv, index=False, lineterminator="\n")
    print(f"Wrote {results_csv}")

    scored = summary[summary["method"] != "naive"].reset_index(drop=True)
    scored.to_csv(summary_csv, index=False, lineterminator="\n")
    print(f"Wrote {summary_csv}")

    write_breakdown(results, group_map, breakdown_csv)
    write_comparison(scored, comparison_csv, variant_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
