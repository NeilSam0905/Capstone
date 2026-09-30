"""
scripts/benchmark_fast_raw_vs_clean.py
------------------------------------------------------------------
Fast-moving-only model benchmark, run TWICE - once on the raw tally
sheets and once on the cleaned star schema - and broken down by product
category.

What this answers
-----------------
1. Does the ETL cleaning in manuscript section 3.1.3 (controlled
   vocabulary + proportional allocation) actually make demand more
   forecastable, or does it only make it tidier? Same methods, same
   walk-forward harness, same horizon, two datasets.
2. Which method wins on the Fast-moving segment specifically - the only
   segment section 3.3.2 sends to a forecasting model at all.
3. Does that answer change by product category (apparel vs drinkware vs
   lanyards ...)?
4. Do the tree-based learners (XGBoost / LightGBM / random forest) that
   this project has never tested beat the trailing-average baselines it
   currently ships?

Definition of the two stages - this is the whole experiment, so it is
stated precisely rather than left to the reader:

  RAW    data/USTore_sales_long_with_zeros.csv, keyed on the item name
         EXACTLY AS SPELLED in the monthly workbook. No controlled
         vocabulary (so 'UST College ID Lace' and 'GENERIC Lace' stay
         separate series), no proportional allocation (so a price-grouped
         row stays one lumped series), no supplier mapping. This is
         step 0 output: the workbooks parsed, and nothing else done.
  CLEAN  Fact_Sales in ustore.db, keyed on product_id. Vocabulary
         applied (step 1), price-grouped rows split per SKU by
         beginning-of-month stock (proportional_allocation.py),
         derived fields and censoring flags computed (step 2).

Both stages are reindexed onto the SAME complete daily calendar, so the
fold layout is identical and no method or stage can be advantaged by a
different split.

Honest caveat, stated up front because it bounds every raw-vs-clean
number below: the two stages do NOT contain the same SKUs. Cleaning is
what decides what an SKU IS, so a like-for-like row-by-row comparison is
not available - it would presuppose the answer. The comparison here is
therefore at the POPULATION level (how forecastable is the Fast segment
each stage produces), plus a MATCHED subset for the canonical items that
survive cleaning untouched: exactly one raw name, zero allocated rows.
The matched subset is the controlled comparison; the population one is
the operational one.

This is a MEASUREMENT, not a model selection. Consistent with
model_benchmark.py and docs/SPARSE_DEMAND_EXPERIMENTS.md, no winner is
declared - selection is deferred decision B3.

Run (from the repo root):
    python scripts/benchmark_fast_raw_vs_clean.py [--quick] [--no-ml]
------------------------------------------------------------------
"""
import argparse
import os
import re
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import (
    ets_fit_predict, ewma_fit_predict, naive_fit_predict,
    rolling_mean_fit_predict, rolling_median_fit_predict,
    rolling_quantile_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.evaluate import (
    aggregate_blocks, evaluate_methods, make_folds,
)
from forecasting.hurdle import weekly_hurdle_fit_predict
from forecasting.intermittent import croston_fit_predict, sba_fit_predict, tsb_fit_predict
from forecasting.metrics import naive_scale
try:
    from forecasting.prophet_model import load_calendar
except ImportError:      # prophet not installed - the two prophet rows drop out
    load_calendar = None
from forecasting.ml_models_fastmoving import (
    MIN_HISTORY, available_ml_methods, build_training_set, make_features,
)

DB_PATH = "ustore.db"
RAW_CSV = "data/USTore_sales_long_with_zeros.csv"
OUT_XLSX = "data/USTore_FastMoving_Model_Benchmark.xlsx"
OUT_FOLDS = "data/fastmoving_benchmark_folds.csv"

HORIZON = 30
MIN_FOLDS = 3
MIN_TRAIN = 60
MAX_FOLDS = 12
ALPHA = 0.1
BETA = 0.1
FSN_PERCENTILE = 80          # section 3.3.1's primary cutoff


# =================================================================
# 1. Product category taxonomy
# =================================================================
# Dim_Product.category is populated for only 10 of the 58 Fast SKUs -
# the other 48 are NULL, because the column is sourced from the
# inventory workbook and most fast sellers never appear there. A
# category breakdown off that column would describe 10 items and call
# it a catalogue. So the grouping below is derived from the canonical
# item name instead, which every SKU has.
#
# Order matters: the first pattern that matches wins, so the more
# specific bucket is listed before the one that would also match. E.g.
# 'UST OAT MUG' must reach Drinkware before 'UST OAT T-Shirt' logic,
# and 'Tiger Claw Keychain' must reach Keychains before Plush.
CATEGORY_RULES = [
    ("Outerwear",          r"\b(hoodies?|jackets?|windbreakers?|sweaters?|pullovers?|bombers?)\b"),
    ("Shirts & Tops",      r"(\bt-?shirts?\b|\bshirts?\b|\bpolos?\b|\bjerseys?\b|\btees?\b"
                           r"|\bsubli\w*|\boversized\b|\bos\b|\bcrew\s*neck\b|\bunif\w*)"),
    ("Bags",               r"(\btote\s*bags?\b|\btotebags?\b|\btotes?\b|\beco\s*bags?\b"
                           r"|\bbags?\b|\bpouch\w*|\bsling\b|\bbackpacks?\b)"),
    ("Drinkware",          r"\b(mugs?|tumblers?|flasks?|bottles?|sippers?|jugs?)\b"),
    ("Lanyards & IDs",     r"(\blanyards?\b|\blaces?\b|\bid\s*cases?\b|\bid\s*holders?\b|\bribbons?\b)"),
    ("Keychains & Charms", r"(\bkey\s*chains?\b|\bkeychains?\b|\bcharms?\b|\bclickers?\b"
                           r"|\bfidgets?\b|\bpins?\b)"),
    ("Stationery",         r"(\bball\s*pens?\b|\bpens?\b|\bpencils?\b|\bnotebooks?\b|\bnb\b"
                           r"|\bnotelets?\b|\bstickers?\b|\bplanners?\b|\bbookmarks?\b"
                           r"|\bfolders?\b|\bpapers?\b|\berasers?\b|\brulers?\b)"),
    ("Plush & Souvenirs",  r"(\bplushi?e?s?\b|\bplush\b|\bstuff(ed)?\s*toys?\b|\btoys?\b"
                           r"|\bclappers?\b|\barch\b|\bfigurines?\b|\btokens?\b"
                           r"|\bw/?\s*box\b|\btiger\s*w\b)"),
    ("Umbrellas & Gear",   r"(\bumbrellas?\b|\bumb\b|\bcaps?\b|\bhats?\b|\bsocks?\b"
                           r"|\btowels?\b|\bfans?\b|\bshoes?\b)"),
    ("Home & Novelty",     r"(\bclocks?\b|\butensils?\b|\bmouse\s*pads?\b|\blamps?\b"
                           r"|\bframes?\b|\bmagnets?\b|\bcoasters?\b)"),
]


def categorise(name):
    """Bucket one item name. Falls through to 'Other' rather than
    guessing - an over-eager regex that swallows the residue would hide
    exactly the items worth looking at by hand."""
    n = str(name).lower()
    for label, pattern in CATEGORY_RULES:
        if re.search(pattern, n):
            return label
    return "Other"


# =================================================================
# 2. Loading the two stages
# =================================================================

def _to_daily(df, key_col, date_col, qty_col, index):
    """One float array per key, over the shared complete daily index."""
    out = {}
    for key, g in df.groupby(key_col, sort=True):
        s = (g.groupby(date_col)[qty_col].sum()
              .reindex(index, fill_value=0.0).astype(float))
        out[key] = s.to_numpy()
    return out


def load_clean(con, index=None):
    """CLEAN stage: Fact_Sales, canonical + allocated."""
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])
    if index is None:
        index = pd.date_range(fact["calendar_date"].min(),
                              fact["calendar_date"].max(), freq="D")
    series = _to_daily(fact, "product_id", "calendar_date", "quantity_sold", index)
    meta = pd.read_sql_query("""
        SELECT product_id, item_name, category AS db_category, supplier_name,
               unit_price_php, fsn_class AS fsn_pipeline, is_hvl
        FROM Dim_Product
    """, con)
    return series, meta, index


def load_raw(index=None):
    """RAW stage: the step-0 long CSV, keyed on the workbook's own spelling."""
    raw = pd.read_csv(RAW_CSV, parse_dates=["Date"])
    raw = raw.rename(columns={"Total Quantity": "qty"})
    if index is None:
        index = pd.date_range(raw["Date"].min(), raw["Date"].max(), freq="D")
    series = _to_daily(raw, "Item", "Date", "qty", index)
    meta = (raw.groupby("Item")
               .agg(supplier_name=("Supplier", lambda s: s.mode().iat[0]),
                    raw_rows=("qty", "size"))
               .reset_index().rename(columns={"Item": "item_name"}))
    return series, meta, index


# =================================================================
# 3. FSN classification, recomputed identically on both stages
# =================================================================

def classify_fsn(series, percentile=FSN_PERCENTILE):
    """Section 3.3.1's rule, applied to whatever series it is handed.

    ADUS = total units / number of distinct days the item actually sold
    on. N = never sold. F = ADUS at or above the `percentile`-th
    percentile of the MOVING population. S = the rest.

    Deliberately NOT reusing step3's imputation weighting or censoring
    exclusion: neither flag exists on the raw side, and applying a rule
    to one stage that cannot be applied to the other would put the
    difference in the rule instead of in the data. `fsn_pipeline` from
    Dim_Product is carried alongside so the production label is still
    visible for the clean stage.
    """
    rows = []
    for key, v in series.items():
        sold = v > 0
        rows.append({
            "key": key,
            "units": float(v.sum()),
            "active_sale_days": int(sold.sum()),
            "nonzero_frac": float(sold.mean()),
            "mean_daily": float(v.mean()),
            "max_daily": float(v.max()),
        })
    df = pd.DataFrame(rows)
    df["ADUS"] = np.where(df["active_sale_days"] > 0,
                          df["units"] / df["active_sale_days"], 0.0)

    moving = df["active_sale_days"] > 0
    cutoff = df.loc[moving, "ADUS"].quantile(percentile / 100.0) if moving.any() else np.inf
    df["fsn"] = np.where(~moving, "N", np.where(df["ADUS"] >= cutoff, "F", "S"))
    return df, float(cutoff)


# =================================================================
# 4. Methods
# =================================================================

def add_prophet_methods(methods, index, calendar):
    """Two Prophet rows, added only if Prophet actually imports.

    `prophet_plain` is trend + weekly + yearly seasonality. `prophet_cal`
    adds section 3.3.2's academic-calendar regressors, which is the
    version the manuscript actually specifies - having both is the only
    way to see whether the calendar regressors earn their place or just
    add parameters."""
    try:
        from forecasting.prophet_model import prophet_fit_predict
    except ImportError as exc:
        return {"prophet": str(exc)}
    methods["prophet_plain"] = prophet_fit_predict(index, None, "prophet_plain")
    methods["prophet_cal"] = prophet_fit_predict(index, calendar, "prophet_cal")
    return {}


def build_methods(quick=False, with_ml=True):
    """Every method scored. RM3/RM6 appear under BOTH readings of the
    abbreviation - 3/6 DAYS and 3/6 MONTHS - because on a daily series
    with a 30-day horizon those are different models and the request did
    not say which. Both are reported; neither is assumed."""
    m = {
        # --- naive references
        "naive": naive_fit_predict(),
        "seasonal_naive_7": seasonal_naive_fit_predict(7),
        # --- the RM family, the requested windows first
        "RM3_3day": rolling_mean_fit_predict(3),
        "RM6_6day": rolling_mean_fit_predict(6),
        "RM3_3month_90d": rolling_mean_fit_predict(90),
        "RM6_6month_180d": rolling_mean_fit_predict(180),
        "rolling_mean_30": rolling_mean_fit_predict(30),
        "rolling_mean_14": rolling_mean_fit_predict(14),
        "rolling_mean_60": rolling_mean_fit_predict(60),
        # --- other central-tendency baselines
        "rolling_median_30": rolling_median_fit_predict(30),
        "rolling_q75_30": rolling_quantile_fit_predict(30, 0.75),
        "ewma_a0.1": ewma_fit_predict(0.1),
        "ewma_a0.3": ewma_fit_predict(0.3),
        # --- intermittent-demand family
        "croston": croston_fit_predict(ALPHA),
        "sba": sba_fit_predict(ALPHA),
        "tsb": tsb_fit_predict(ALPHA, BETA),
        "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
        # --- exponential smoothing with trend + season
        "ets": ets_fit_predict(7, optimise=not quick),
    }
    missing = {}
    if with_ml:
        ml, missing = available_ml_methods()
        m.update(ml)
    return m, missing


# =================================================================
# 5. Pooled ("global") learners - one model across all Fast SKUs
# =================================================================
# The per-SKU learners above see ~400 rows of one mostly-zero series.
# A pooled model sees every Fast SKU at once and can borrow the shape of
# demand from items that are still selling. It cannot use the standard
# fit_predict contract (that contract only ever sees one series), so it
# runs here instead - on the SAME fold layout, so its rows drop straight
# into the same results frame.

def run_pooled_ml(series, folds, factories, horizon=HORIZON,
                  min_history=MIN_HISTORY):
    rows = []
    for name, make_model in factories.items():
        for f in folds:
            Xs, ys = [], []
            for key, v in series.items():
                X, y = build_training_set(v[:f.train_end], horizon, min_history)
                if X.shape[0]:
                    Xs.append(X)
                    ys.append(y)
            if not Xs:
                continue
            model = make_model()
            model.fit(np.vstack(Xs), np.concatenate(ys))

            keys = list(series)
            Xp = np.array([make_features(series[k][:f.train_end], f.train_end)
                           for k in keys])
            preds = np.maximum(model.predict(Xp), 0.0)

            for key, pred_total in zip(keys, preds):
                v = series[key]
                actual = float(v[f.test_start:f.test_end].sum())
                blocks = aggregate_blocks(v[:f.train_end], horizon)
                denom = naive_scale(blocks) if blocks.size > 1 else float("nan")
                rows.append({
                    "sku": key, "method": name, "fold": f.fold_index,
                    "origin": f.origin, "n_train": f.n_train, "horizon": horizon,
                    "actual_30d": actual, "pred_30d": float(pred_total),
                    "abs_error": abs(actual - float(pred_total)),
                    "naive_scale": denom,
                })
    return pd.DataFrame(rows)


def pooled_factories():
    """Same libraries as the per-SKU learners, same guarded imports."""
    out = {}
    try:
        import xgboost as xgb
        out["xgboost_pooled"] = lambda: xgb.XGBRegressor(
            n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8,
            colsample_bytree=0.8, reg_lambda=1.0, min_child_weight=10,
            random_state=0, n_jobs=-1, tree_method="hist", verbosity=0)
    except ImportError:
        pass
    try:
        import lightgbm as lgb
        out["lightgbm_pooled"] = lambda: lgb.LGBMRegressor(
            n_estimators=400, num_leaves=31, learning_rate=0.05,
            min_child_samples=30, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0, random_state=0,
            n_jobs=-1, verbose=-1)
    except ImportError:
        pass
    try:
        from sklearn.ensemble import RandomForestRegressor
        out["random_forest_pooled"] = lambda: RandomForestRegressor(
            n_estimators=300, min_samples_leaf=5, max_features="sqrt",
            random_state=0, n_jobs=-1)
    except ImportError:
        pass
    return out


# =================================================================
# 6. Scoring
# =================================================================

def per_sku_metrics(results):
    """One row per (method, sku): the metrics the manuscript names, plus
    WMAPE.

    WMAPE = sum|error| / sum(actual) is included because it is the
    percentage metric that survives intermittency. Section 3.3.4's MAPE
    divides by each period's actual, which is zero on most of this
    catalogue's fold windows; WMAPE divides by the total instead, so it
    is defined whenever the SKU sold anything at all in the scored
    period. `mape` is reported next to it, with its undefined count, so
    the manuscript's own gate stays visible rather than being quietly
    replaced.
    """
    rows = []
    for (method, sku), g in results.groupby(["method", "sku"], sort=False):
        a = g["actual_30d"].to_numpy(dtype=float)
        p = g["pred_30d"].to_numpy(dtype=float)
        err = np.abs(a - p)
        denom = np.nanmean(g["naive_scale"].to_numpy(dtype=float))
        m = float(err.mean())
        defined = a != 0
        rows.append({
            "method": method, "sku": sku, "n_folds": len(g),
            "mae": m,
            "rmse": float(np.sqrt(np.mean((a - p) ** 2))),
            "mase": m / denom if denom and np.isfinite(denom) and denom > 0 else np.nan,
            "wmape_pct": 100.0 * err.sum() / a.sum() if a.sum() > 0 else np.nan,
            "mape_pct": float(np.mean(100.0 * err[defined] / a[defined])) if defined.any() else np.nan,
            "mape_n_undefined": int((~defined).sum()),
            "bias": float(np.mean(p - a)),
            "mean_actual_30d": float(a.mean()),
            "total_actual": float(a.sum()),
            "total_pred": float(p.sum()),
            "abs_err_sum": float(err.sum()),
        })
    return pd.DataFrame(rows)


def summarise_methods(ps, label=""):
    """Method-level table. Two averaging schemes, both reported, because
    they answer different questions and this project has already been
    bitten by conflating them (see model_benchmark.py's reading note 2).

      *_mean_of_skus  each SKU counts once - the small ones are not
                      drowned out by the one item that sells hundreds.
      *_pooled        every unit counts once - what the store's total
                      stockout exposure actually looks like.
    """
    out = (ps.groupby("method")
             .agg(mae=("mae", "mean"),
                  rmse=("rmse", "mean"),
                  mase=("mase", "mean"),
                  mase_median=("mase", "median"),
                  mae_median=("mae", "median"),
                  wmape_pct=("wmape_pct", "mean"),
                  mape_pct=("mape_pct", "mean"),
                  bias=("bias", "mean"),
                  mase_n_skus=("mase", "count"),
                  n_skus=("sku", "nunique"),
                  n_folds=("n_folds", "sum"))
             .reset_index())

    pooled = (ps.groupby("method")
                .apply(lambda g: pd.Series({
                    "wmape_pooled_pct": 100.0 * g["abs_err_sum"].sum() / g["total_actual"].sum()
                    if g["total_actual"].sum() > 0 else np.nan,
                    "mean_demand_30d": g["mean_actual_30d"].mean(),
                }), include_groups=False)
                .reset_index())
    out = out.merge(pooled, on="method", how="left")

    # MAE and RMSE as a PERCENT of the average 30-day demand being
    # forecast - the scale-free reading of the same two numbers, so a
    # 13-unit MAE can be read against what 13 units is worth here.
    out["mae_pct_of_demand"] = 100.0 * out["mae"] / out["mean_demand_30d"]
    out["rmse_pct_of_demand"] = 100.0 * out["rmse"] / out["mean_demand_30d"]
    out["mase_pct"] = 100.0 * out["mase"]

    # improvement against the naive reference, in percent
    for col in ("mae", "rmse", "mase"):
        base = out.loc[out["method"] == "naive", col]
        b = float(base.iloc[0]) if len(base) and np.isfinite(base.iloc[0]) else np.nan
        out[f"pct_better_than_naive_{col}"] = 100.0 * (b - out[col]) / b if b else np.nan

    if label:
        out.insert(0, "stage", label)
    return out.sort_values(["mase", "mae"], na_position="last",
                           kind="stable").reset_index(drop=True)


def pct_skus_beating_naive(ps):
    """Share of SKUs where the method's MAE is strictly below naive's on
    the identical folds. A per-SKU win rate, not an average of averages -
    one huge SKU cannot carry a method that loses everywhere else."""
    wide = ps.pivot_table(index="sku", columns="method", values="mae")
    if "naive" not in wide:
        return {}
    base = wide["naive"]
    return {m: round(100.0 * float((wide[m] < base).mean()), 1)
            for m in wide.columns if m != "naive"}


# =================================================================
# 7. Driver
# =================================================================

def run_stage(series_all, fast_keys, methods, pooled, label, quick=False):
    """Score every method on one stage's Fast-moving set."""
    series = {k: series_all[k] for k in fast_keys}
    n = len(next(iter(series.values())))
    folds = make_folds(n, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)

    print(f"\n[{label}] {len(series)} Fast SKUs x {len(methods)} methods "
          f"x {len(folds)} folds ({n} calendar days)")
    t0 = time.time()
    results, insufficient = evaluate_methods(
        series, methods, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    print(f"[{label}] per-SKU methods done in {time.time() - t0:.0f}s "
          f"({len(results):,} scored predictions)")

    if pooled and not quick:
        t0 = time.time()
        pooled_rows = run_pooled_ml(series, folds, pooled)
        print(f"[{label}] pooled learners done in {time.time() - t0:.0f}s "
              f"({len(pooled_rows):,} rows)")
        results = pd.concat([results, pooled_rows], ignore_index=True)

    results["stage"] = label

    # identical-folds gate, same property model_benchmark.py enforces
    layouts = results.groupby(["sku", "method"])["origin"].apply(lambda s: tuple(sorted(s)))
    assert (layouts.groupby("sku").nunique() == 1).all(), \
        f"[{label}] methods were NOT scored on identical folds"

    return results, insufficient, len(folds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip ETS optimisation and the pooled learners")
    ap.add_argument("--no-ml", action="store_true",
                    help="baselines only - no sklearn/xgboost/lightgbm")
    ap.add_argument("--no-prophet", action="store_true",
                    help="skip the two Prophet rows (they are ~10 min of the run)")
    ap.add_argument("--out", default=OUT_XLSX)
    args = ap.parse_args()

    print("=" * 78)
    print("USTore Fast-moving benchmark - RAW tally sheets vs CLEANED star schema")
    print("=" * 78)

    con = sqlite3.connect(DB_PATH)
    clean_series, clean_meta, index = load_clean(con)
    con.close()
    raw_series, raw_meta, _ = load_raw(index)

    print(f"Calendar: {index[0].date()} .. {index[-1].date()} ({len(index)} days)")
    print(f"RAW   : {len(raw_series)} distinct item names as spelled in the workbooks")
    print(f"CLEAN : {len(clean_series)} canonical product_ids in Fact_Sales")

    # ---- FSN, recomputed with the same rule on both stages -----------
    raw_fsn, raw_cut = classify_fsn(raw_series)
    clean_fsn, clean_cut = classify_fsn(clean_series)
    print(f"\nFSN cutoff (ADUS, {FSN_PERCENTILE}th pct of movers): "
          f"RAW {raw_cut:.3f} | CLEAN {clean_cut:.3f}")
    for lab, d in (("RAW", raw_fsn), ("CLEAN", clean_fsn)):
        c = d["fsn"].value_counts()
        print(f"  {lab:5} F={c.get('F', 0):4}  S={c.get('S', 0):4}  N={c.get('N', 0):4}")

    raw_fast = raw_fsn.loc[raw_fsn["fsn"] == "F", "key"].tolist()
    clean_fast_rule = clean_fsn.loc[clean_fsn["fsn"] == "F", "key"].tolist()

    # The production Fast set is step3's, not this script's: it weights
    # imputed rows at 0.5 and drops censored zero-sale days, neither of
    # which exists on the raw side. Both sets are carried:
    #   clean_fast_pipeline  what the repo actually forecasts (58 SKUs)
    #   clean_fast_rule      the same rule applied to raw, for a
    #                        rule-matched raw-vs-clean comparison
    # The benchmark runs over the UNION once, and the two summaries are
    # slices of it - so the two views are guaranteed to come from the
    # same folds and the same predictions, not from two separate runs.
    clean_fast_pipeline = clean_meta.loc[
        clean_meta["fsn_pipeline"] == "F", "product_id"].tolist()
    clean_fast_pipeline = [p for p in clean_fast_pipeline if p in clean_series]
    clean_union = sorted(set(clean_fast_pipeline) | set(clean_fast_rule))
    print(f"\nCLEAN Fast sets: pipeline (Dim_Product.fsn_class) = "
          f"{len(clean_fast_pipeline)}, recomputed rule = {len(clean_fast_rule)}, "
          f"overlap = {len(set(clean_fast_pipeline) & set(clean_fast_rule))}, "
          f"union scored = {len(clean_union)}")

    methods, missing_ml = build_methods(args.quick, with_ml=not args.no_ml)
    if not args.no_prophet and load_calendar is not None:
        con = sqlite3.connect(DB_PATH)
        calendar = load_calendar(con, index)
        con.close()
        missing_ml.update(add_prophet_methods(methods, index, calendar))
    if missing_ml:
        print(f"\nUnavailable learners (skipped): {list(missing_ml)}")
    print(f"\nMethods ({len(methods)}): {', '.join(methods)}")
    pooled = {} if (args.quick or args.no_ml) else pooled_factories()
    if pooled:
        print(f"Pooled learners ({len(pooled)}): {', '.join(pooled)}")

    clean_res, clean_insuf, n_folds = run_stage(
        clean_series, clean_union, methods, pooled, "CLEAN", args.quick)
    raw_res, raw_insuf, _ = run_stage(
        raw_series, raw_fast, methods, pooled, "RAW", args.quick)

    all_res = pd.concat([clean_res, raw_res], ignore_index=True)
    all_res.to_csv(OUT_FOLDS, index=False, lineterminator="\n")
    print(f"\nPer-fold detail -> {OUT_FOLDS} ({len(all_res):,} rows)")

    return build_workbook(args.out, clean_res, raw_res, clean_meta, raw_meta,
                          clean_fsn, raw_fsn, clean_fast_pipeline,
                          clean_fast_rule, raw_fast,
                          clean_cut, raw_cut, index, methods, pooled,
                          n_folds, clean_insuf, raw_insuf)


def build_workbook(path, clean_res, raw_res, clean_meta, raw_meta,
                   clean_fsn, raw_fsn, clean_fast_pipeline, clean_fast_rule,
                   raw_fast, clean_cut, raw_cut, index, methods, pooled,
                   n_folds, clean_insuf, raw_insuf):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    clean_ps_all = per_sku_metrics(clean_res)
    raw_ps = per_sku_metrics(raw_res)

    pipe = set(clean_fast_pipeline)
    rule = set(clean_fast_rule)
    clean_ps = clean_ps_all[clean_ps_all["sku"].isin(pipe)].copy()          # headline
    clean_ps_rule = clean_ps_all[clean_ps_all["sku"].isin(rule)].copy()     # rule-matched

    clean_sum = summarise_methods(clean_ps, "CLEAN (pipeline Fast set)")
    clean_sum_rule = summarise_methods(clean_ps_rule, "CLEAN (rule-matched Fast set)")
    raw_sum = summarise_methods(raw_ps, "RAW")
    clean_sum["pct_skus_beating_naive"] = clean_sum["method"].map(pct_skus_beating_naive(clean_ps))
    clean_sum_rule["pct_skus_beating_naive"] = clean_sum_rule["method"].map(
        pct_skus_beating_naive(clean_ps_rule))
    raw_sum["pct_skus_beating_naive"] = raw_sum["method"].map(pct_skus_beating_naive(raw_ps))

    # ---- raw vs clean, per method -----------------------------------
    # Compared against the RULE-MATCHED clean set, so the only thing that
    # differs between the two columns is the DATA, not the definition of
    # what counts as Fast. The pipeline set is carried alongside as the
    # operational reference.
    keep = ["method", "mae", "rmse", "mase", "wmape_pct"]
    cmp_df = (clean_sum_rule[keep]
              .merge(raw_sum[keep], on="method", suffixes=("_clean", "_raw")))
    for m in ("mae", "rmse", "mase", "wmape_pct"):
        # negative = cleaning REDUCED the error, which is the improvement
        cmp_df[f"pct_change_{m}"] = 100.0 * (cmp_df[f"{m}_clean"] - cmp_df[f"{m}_raw"]) / cmp_df[f"{m}_raw"]
    cmp_df = cmp_df.merge(
        clean_sum[["method", "mae", "rmse", "mase"]].rename(columns={
            "mae": "mae_clean_pipelineF", "rmse": "rmse_clean_pipelineF",
            "mase": "mase_clean_pipelineF"}), on="method", how="left")
    cmp_df = cmp_df.sort_values("mase_clean").reset_index(drop=True)

    # ---- category breakdown (CLEAN stage) ---------------------------
    cm = clean_meta.copy()
    cm["category"] = cm["item_name"].map(categorise)
    cat_of = dict(zip(cm["product_id"], cm["category"]))
    name_of = dict(zip(cm["product_id"], cm["item_name"]))

    cps = clean_ps.copy()
    cps["category"] = cps["sku"].map(cat_of)
    cps["item_name"] = cps["sku"].map(name_of)

    by_cat = (cps.groupby(["category", "method"])
                 .agg(n_skus=("sku", "nunique"), mae=("mae", "mean"),
                      rmse=("rmse", "mean"), mase=("mase", "mean"),
                      wmape_pct=("wmape_pct", "mean"),
                      mean_demand_30d=("mean_actual_30d", "mean"),
                      abs_err_sum=("abs_err_sum", "sum"),
                      total_actual=("total_actual", "sum"))
                 .reset_index())
    by_cat["wmape_pooled_pct"] = 100.0 * by_cat["abs_err_sum"] / by_cat["total_actual"]
    by_cat["mae_pct_of_demand"] = 100.0 * by_cat["mae"] / by_cat["mean_demand_30d"]
    by_cat["rmse_pct_of_demand"] = 100.0 * by_cat["rmse"] / by_cat["mean_demand_30d"]
    by_cat["mase_pct"] = 100.0 * by_cat["mase"]
    by_cat = by_cat.sort_values(["category", "mase"]).reset_index(drop=True)

    best = (by_cat.sort_values(["category", "mase", "mae"])
                  .groupby("category", as_index=False).first()
                  .rename(columns={"method": "best_method_by_mase"}))
    best_mae = (by_cat.sort_values(["category", "mae"])
                      .groupby("category", as_index=False).first()[["category", "method", "mae"]]
                      .rename(columns={"method": "best_method_by_mae", "mae": "best_mae"}))
    cat_best = best[["category", "n_skus", "best_method_by_mase", "mase",
                     "mae", "wmape_pooled_pct", "mean_demand_30d"]].merge(
        best_mae, on="category", how="left")

    # ---- RM window sweep --------------------------------------------
    rm_names = ["RM3_3day", "RM6_6day", "rolling_mean_14", "rolling_mean_30",
                "rolling_mean_60", "RM3_3month_90d", "RM6_6month_180d"]
    rm_win = {"RM3_3day": 3, "RM6_6day": 6, "rolling_mean_14": 14,
              "rolling_mean_30": 30, "rolling_mean_60": 60,
              "RM3_3month_90d": 90, "RM6_6month_180d": 180}
    rm = pd.concat([
        clean_sum[clean_sum["method"].isin(rm_names)],
        raw_sum[raw_sum["method"].isin(rm_names)],
    ], ignore_index=True)
    rm["window_days"] = rm["method"].map(rm_win)
    rm = rm.sort_values(["stage", "window_days"]).reset_index(drop=True)

    rm_cat = by_cat[by_cat["method"].isin(["RM3_3day", "RM6_6day",
                                           "RM3_3month_90d", "RM6_6month_180d",
                                           "rolling_mean_30"])].copy()
    rm_cat["window_days"] = rm_cat["method"].map(rm_win)
    rm_cat = rm_cat.sort_values(["category", "window_days"]).reset_index(drop=True)

    # ---- Fast item inventory ----------------------------------------
    # Every SKU scored on the clean side, labelled by which Fast
    # definition put it there - so the 22 items the two definitions
    # disagree about are visible by name, not just as a count.
    cfsn = clean_fsn.rename(columns={"key": "product_id",
                                     "fsn": "fsn_recomputed"})
    fast_items = cm.merge(cfsn, on="product_id", how="inner")
    fast_items = (fast_items[fast_items["product_id"].isin(pipe | rule)]
                  .sort_values("ADUS", ascending=False))
    fast_items["in_pipeline_fast"] = fast_items["product_id"].isin(pipe)
    fast_items["in_recomputed_fast"] = fast_items["product_id"].isin(rule)
    fast_items["fast_set"] = np.select(
        [fast_items["in_pipeline_fast"] & fast_items["in_recomputed_fast"],
         fast_items["in_pipeline_fast"]],
        ["both", "pipeline only"], default="recomputed only")

    per_sku_best = (clean_ps_all.sort_values(["sku", "mase", "mae"])
                       .groupby("sku", as_index=False).first()
                       [["sku", "method", "mase", "mae"]]
                       .rename(columns={"sku": "product_id",
                                        "method": "best_method",
                                        "mase": "best_mase", "mae": "best_mae"}))
    fast_items = fast_items.merge(per_sku_best, on="product_id", how="left")
    fast_items = fast_items[["product_id", "item_name", "category", "db_category",
                             "supplier_name", "unit_price_php", "fsn_pipeline",
                             "fsn_recomputed", "fast_set", "in_pipeline_fast",
                             "in_recomputed_fast", "is_hvl", "units",
                             "active_sale_days", "nonzero_frac",
                             "ADUS", "mean_daily", "max_daily",
                             "best_method", "best_mase", "best_mae"]]

    # ---- FSN stability, raw vs clean --------------------------------
    rf = raw_fsn.rename(columns={"key": "item_name"})
    stability = pd.DataFrame([
        {"metric": "Distinct series (SKUs)", "RAW": len(raw_fsn), "CLEAN": len(clean_fsn)},
        {"metric": "Fast (F)", "RAW": int((raw_fsn['fsn'] == 'F').sum()),
         "CLEAN": int((clean_fsn['fsn'] == 'F').sum())},
        {"metric": "Slow (S)", "RAW": int((raw_fsn['fsn'] == 'S').sum()),
         "CLEAN": int((clean_fsn['fsn'] == 'S').sum())},
        {"metric": "Non-moving (N)", "RAW": int((raw_fsn['fsn'] == 'N').sum()),
         "CLEAN": int((clean_fsn['fsn'] == 'N').sum())},
        {"metric": f"ADUS cutoff at {FSN_PERCENTILE}th pct", "RAW": round(raw_cut, 4),
         "CLEAN": round(clean_cut, 4)},
        {"metric": "Total units in scope", "RAW": float(raw_fsn['units'].sum()),
         "CLEAN": float(clean_fsn['units'].sum())},
        {"metric": "Units held by Fast items",
         "RAW": float(raw_fsn.loc[raw_fsn['fsn'] == 'F', 'units'].sum()),
         "CLEAN": float(clean_fsn.loc[clean_fsn['fsn'] == 'F', 'units'].sum())},
        {"metric": "SKUs with <3 folds (excluded)", "RAW": len(raw_insuf),
         "CLEAN": len(clean_insuf)},
    ])

    # name-level Fast membership overlap: exact string match only, since
    # anything fuzzier would be the vocabulary mapping under another name
    raw_fast_names = set(rf.loc[rf["fsn"] == "F", "item_name"])
    clean_fast_names = set(fast_items.loc[fast_items["in_recomputed_fast"], "item_name"])
    pipe_fast_names = set(fast_items.loc[fast_items["in_pipeline_fast"], "item_name"])
    overlap = pd.DataFrame([
        {"set": "Fast in BOTH stages, same rule (exact name match)",
         "n": len(raw_fast_names & clean_fast_names)},
        {"set": "Fast in RAW only", "n": len(raw_fast_names - clean_fast_names)},
        {"set": "Fast in CLEAN only (same rule)", "n": len(clean_fast_names - raw_fast_names)},
        {"set": "CLEAN pipeline-Fast that the recomputed rule also calls Fast",
         "n": len(pipe_fast_names & clean_fast_names)},
        {"set": "CLEAN pipeline-Fast the recomputed rule does NOT call Fast",
         "n": len(pipe_fast_names - clean_fast_names)},
        {"set": "Recomputed-Fast the pipeline does NOT call Fast",
         "n": len(clean_fast_names - pipe_fast_names)},
    ])

    # ---- README ------------------------------------------------------
    readme = pd.DataFrame([
        ("WHAT THIS IS", "Fast-moving-only forecasting benchmark for the USTore capstone, "
                         "run on the RAW tally sheets and again on the CLEANED star schema."),
        ("GENERATED", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")),
        ("SOURCE - RAW", f"{RAW_CSV} - item names EXACTLY as spelled in the monthly workbooks. "
                         "No controlled vocabulary, no proportional allocation, no supplier mapping."),
        ("SOURCE - CLEAN", f"{DB_PATH} Fact_Sales - canonical names (step1), price-grouped rows "
                           "split per SKU by beginning-of-month stock, derived fields (step2)."),
        ("CALENDAR", f"{index[0].date()} to {index[-1].date()} ({len(index)} days). Both stages are "
                     "reindexed onto this same daily index, so fold layouts are identical."),
        ("SEGMENT", "Fast-moving only. Slow and Non-moving SKUs are excluded entirely, which is "
                    "what section 3.3.2 prescribes - only F and HVL items go to a forecasting model."),
        ("TWO FAST SETS", f"Summary_CLEAN uses the PIPELINE Fast set ({len(clean_fast_pipeline)} SKUs, "
                          "Dim_Product.fsn_class from step3 - imputed rows weighted 0.5, censored "
                          f"days dropped). Summary_CLEAN_ruleF uses the RECOMPUTED rule "
                          f"({len(clean_fast_rule)} SKUs: ADUS >= the {FSN_PERCENTILE}th percentile "
                          "of movers, unweighted), which is the only rule the raw stage can support. "
                          "Both are slices of ONE benchmark run over the union, so they share folds "
                          "and predictions exactly. Raw_vs_Clean compares RAW against the "
                          "rule-matched set so the only difference is the DATA."),
        ("VALIDATION", f"Walk-forward, expanding window, horizon {HORIZON}d, {n_folds} rolling origins "
                       f"per SKU, min train {MIN_TRAIN}d. Every method sees identical folds; "
                       "training slices end strictly before each origin (no leakage)."),
        ("SCORING UNIT", "A 30-day AGGREGATE per fold, not 30 daily points - that is the quantity "
                         "the reorder decision consumes."),
        ("MASE DENOMINATOR", "Mean absolute difference between consecutive 30-day TRAINING blocks. "
                             "MASE 1.0 = as good as predicting last month's total for this month. "
                             "Not comparable to a MASE quoted against a different denominator."),
        ("WMAPE vs MAPE", "WMAPE = sum|error| / sum(actual), the percentage metric that survives "
                          "intermittent demand. MAPE is reported too, with its undefined count, "
                          "because section 3.3.4's <=20% gate is written in MAPE."),
        ("mae_pct_of_demand", "MAE as a percent of the mean 30-day demand being forecast - the "
                              "scale-free reading of the same number."),
        ("pct_change_* (Raw_vs_Clean)", "100 * (clean - raw) / raw. NEGATIVE = cleaning reduced "
                                        "the error."),
        ("RM3 / RM6", "The request did not say whether RM3/RM6 meant 3/6 DAYS or 3/6 MONTHS. "
                      "Both are scored: RM3_3day, RM6_6day, RM3_3month_90d, RM6_6month_180d."),
        ("CATEGORY", "Derived from the canonical item name by keyword (see CATEGORY_RULES in the "
                     "script). Dim_Product.category is NULL for 48 of the 58 Fast SKUs, so it is "
                     "carried as db_category for reference but is not the grouping used."),
        ("POOLED LEARNERS", "*_pooled models are fitted ONCE per fold across every Fast SKU at once "
                            "instead of once per SKU, on the same folds."),
        ("CAVEAT - NOT LIKE FOR LIKE", "The RAW and CLEAN stages do not contain the same SKUs: "
                                       "cleaning is what decides what an SKU is. Raw-vs-clean numbers "
                                       "are a POPULATION comparison (how forecastable is the Fast "
                                       "segment each stage produces), not a per-item one."),
        ("STATUS", "This is a MEASUREMENT, not a model selection. No winner is declared - model "
                   "selection is deferred decision B3, downstream of B2."),
    ], columns=["field", "value"])

    sheets = {
        "README": readme,
        "Summary_CLEAN": clean_sum,
        "Summary_RAW": raw_sum,
        "Raw_vs_Clean": cmp_df,
        "Summary_CLEAN_ruleF": clean_sum_rule,
        "RM_Windows": rm,
        "By_Category": by_cat,
        "Category_Best": cat_best,
        "RM_by_Category": rm_cat,
        "Fast_Items": fast_items,
        "Per_SKU_CLEAN": cps.sort_values(["sku", "mase"]),
        "FSN_Raw_vs_Clean": stability,
        "Fast_Set_Overlap": overlap,
    }

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False,
                        float_format=None)

    # ---- formatting --------------------------------------------------
    import openpyxl
    wb = openpyxl.load_workbook(path)
    head_fill = PatternFill("solid", fgColor="1F3864")
    head_font = Font(color="FFFFFF", bold=True)
    for name in wb.sheetnames:
        ws = wb[name]
        for c in ws[1]:
            c.fill = head_fill
            c.font = head_font
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
        ws.freeze_panes = "A2"
        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            width = max((len(str(c.value)) for c in col[:200] if c.value is not None),
                        default=10)
            ws.column_dimensions[letter].width = min(max(width + 2, 10),
                                                     60 if name == "README" else 26)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, float):
                    c.number_format = "0.000" if abs(c.value) < 1000 else "#,##0.0"
        if name == "README":
            ws.column_dimensions["A"].width = 30
            ws.column_dimensions["B"].width = 110
            for row in ws.iter_rows(min_row=2):
                row[1].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)

    print(f"\nWrote {path}")
    print(f"  sheets: {', '.join(sheets)}")
    print("\n" + "=" * 78)
    print("CLEAN stage - Fast-moving SKUs, ordered by MASE")
    print("=" * 78)
    cols = ["method", "mae", "rmse", "mase", "wmape_pct", "mae_pct_of_demand",
            "pct_skus_beating_naive"]
    print(clean_sum[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n" + "=" * 78)
    print("RAW stage - Fast-moving SKUs, ordered by MASE")
    print("=" * 78)
    print(raw_sum[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n" + "=" * 78)
    print("Best method per category (CLEAN stage)")
    print("=" * 78)
    print(cat_best.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
