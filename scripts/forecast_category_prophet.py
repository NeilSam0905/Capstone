"""
scripts/forecast_category_prophet.py
------------------------------------------------------------------
Category-level Prophet forecasting on the colour-aware series.

Pipeline position: consumes `data/rebuild_sales_long.csv` and
`data/rebuild_day_status.csv` from `scripts/rebuild_extract_tbs.py`, joins
`Dim_Date` and `Dim_Product` from `ustore.db`, fits one Prophet model per
forecast_category, and reports MAPE / MAE / MASE / RMSE per category.

The one design decision that matters
------------------------------------
A day the store was SHUT is not a zero-demand observation. The extractor
resolved each date's operating status from the workbook's own colour
legend, and this script drops days where `demand_observable = 0` from the
training series instead of feeding Prophet a fabricated zero.

That is not a tuning choice, it is a correctness one. Measured on the
existing pipeline (docs/SYNTHETIC_AUGMENTATION_CATEGORY.md): 405 of 821
days are store-wide zeros and 84.1% of all category-level zero cells are
common-mode - the categories go quiet together because the STORE went
quiet, not because demand did. Training on those teaches the model that
demand collapses on dates where in fact no demand could be expressed.

Because the days are dropped rather than zero-filled, the series Prophet
sees is irregular. Prophet handles that natively - it fits on (ds, y)
pairs and does not require a contiguous index - which is the specific
property that makes this correction cheap here.

Forecasts are produced on TRADING days only and then aggregated to the
30-day horizon total, so a horizon containing more closures does not
inflate the prediction.

Evaluation
----------
Walk-forward, horizon 30, up to 12 folds, min_train 60 - deliberately the
same harness parameters as `scripts/benchmark_category_level.py` so these
numbers can be read against the 27-method table already in
`data/category_benchmark_summary.csv`.

  MAPE  per fold |actual-pred|/actual, averaged; folds with actual == 0
        are excluded and counted (the metric is undefined there).
  MAE   mean absolute error on the 30-day total.
  RMSE  root mean squared error on the 30-day total.
  MASE  MAE / naive_scale, where the denominator is the in-sample MAE of
        a seasonal-naive forecast over 30-day blocks of the TRAINING
        slice - the same definition `forecasting/metrics.py` uses, so the
        value is comparable to every other MASE in this project.
        MASE < 1 means the model beats that naive benchmark.

Assumptions
-----------
  * `ustore.db` sits at the repo root and holds Dim_Date and Dim_Product.
  * Products are mapped to categories via `Dim_Product.forecast_category`,
    joined on the canonical `item_name`. Unmapped items fall into
    "Uncategorised" and are reported rather than dropped.
  * Prophet is installed. If it is not, the script says so and exits
    non-zero rather than silently substituting another model.

Run:  python scripts/forecast_category_prophet.py [--horizon 30]
                                                  [--folds 12] [--no-db-write]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "ustore.db")

SALES_CSV = os.path.join(DATA_DIR, "rebuild_sales_long.csv")
DAYS_CSV = os.path.join(DATA_DIR, "rebuild_day_status.csv")
OUT_METRICS = os.path.join(DATA_DIR, "category_prophet_metrics.csv")
OUT_FORECAST = os.path.join(DATA_DIR, "category_prophet_forecast.csv")

# Dim_Date columns offered to Prophet as extra regressors. semester_week is
# deliberately excluded: Divergence #8 records that its continuous form
# resets each term and extrapolates badly across the boundary.
CALENDAR_REGRESSORS = ["is_enrollment_period", "is_exam_week",
                       "is_event_day", "is_sem_break"]


def _quiet():
    """Prophet/cmdstanpy emit a block per fit; at ~150 fits that buries
    everything else this script prints."""
    for name in ("cmdstanpy", "prophet", "fbprophet", "numexpr"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False
        lg.disabled = True
        lg.handlers[:] = []
    warnings.filterwarnings("ignore")


# --------------------------------------------------------------- loading
def load_inputs(con, fsn_only=None):
    """Sales + day status + calendar + category map, joined.

    `fsn_only` restricts to SKUs in those FSN classes (e.g. {"F"}). The
    category series is then built from Fast-moving items alone, which is the
    population step4 actually forecasts and the one section 3.3.2 scopes the
    accuracy criterion to - so it is the like-for-like comparison, not the
    whole catalogue's.

    Returns (daily, calendar) where `daily` is one row per
    (category, trading date) carrying units, and `calendar` is the
    Dim_Date frame indexed by date.
    """
    if not (os.path.exists(SALES_CSV) and os.path.exists(DAYS_CSV)):
        raise SystemExit(
            "missing %s / %s - run scripts/rebuild_extract_tbs.py first"
            % (os.path.basename(SALES_CSV), os.path.basename(DAYS_CSV)))

    sales = pd.read_csv(SALES_CSV, parse_dates=["calendar_date"])
    days = pd.read_csv(DAYS_CSV, parse_dates=["calendar_date"])

    prod = pd.read_sql_query(
        "SELECT item_name, forecast_category, fsn_class FROM Dim_Product", con)
    n_all_skus = len(prod)
    if fsn_only:
        prod = prod[prod["fsn_class"].isin(fsn_only)]
    cat = (prod.dropna(subset=["item_name"])
               .assign(key=lambda d: d.item_name.astype(str).str.strip().str.upper())
               .drop_duplicates("key")
               .set_index("key")["forecast_category"].to_dict())

    sales["key"] = sales["item_name"].astype(str).str.strip().str.upper()
    if fsn_only:
        # Drop rows for SKUs outside the requested FSN classes outright -
        # mapping them to "Uncategorised" would silently pool every Slow and
        # Non-moving item into a thirteenth category and misreport it as one.
        before_fsn = len(sales)
        sales = sales[sales["key"].isin(cat)]
        n_unmapped = before_fsn - len(sales)
    else:
        n_unmapped = int((~sales["key"].isin(cat)).sum())
    sales["forecast_category"] = sales["key"].map(cat).fillna("Uncategorised")

    # THE CORRECTION: keep only days on which demand could be expressed.
    observable = set(days.loc[days["demand_observable"] == 1, "calendar_date"])
    before = len(sales)
    sales = sales[sales["calendar_date"].isin(observable)]

    daily = (sales.groupby(["forecast_category", "calendar_date"], as_index=False)
                  ["quantity_sold"].sum()
                  .rename(columns={"quantity_sold": "y", "calendar_date": "ds"}))

    cal = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CALENDAR_REGRESSORS),
        con, parse_dates=["calendar_date"]).set_index("calendar_date")

    return daily, cal, days, {"unmapped_rows": n_unmapped,
                              "rows_dropped_not_observable": before - len(sales),
                              "rows_kept": len(sales),
                              "n_skus_in_scope": len(cat),
                              "n_skus_total": n_all_skus}


# --------------------------------------------------------------- metrics
def naive_scale_blocks(train_y, horizon):
    """MASE denominator: in-sample MAE of a naive forecast on 30-day blocks.

    Blocks, not daily values, because the scored unit is the 30-day total -
    a denominator in daily units would not be in the same scale as the
    errors and the resulting MASE would be meaningless.
    """
    y = np.asarray(train_y, dtype=float)
    n_blocks = y.size // horizon
    if n_blocks < 2:
        return np.nan
    blocks = y[-n_blocks * horizon:].reshape(n_blocks, horizon).sum(axis=1)
    return float(np.mean(np.abs(np.diff(blocks))))


def score(actuals, preds, scales):
    """MAPE / MAE / RMSE / MASE over a category's folds."""
    a = np.asarray(actuals, float)
    p = np.asarray(preds, float)
    e = np.abs(a - p)
    defined = a != 0
    mae = float(e.mean())
    denom = np.nanmean(scales) if len(scales) else np.nan
    return {
        "mae": mae,
        "rmse": float(np.sqrt((e ** 2).mean())),
        "mape_pct": float(np.mean(e[defined] / a[defined]) * 100) if defined.any() else np.nan,
        "mase": mae / denom if denom and np.isfinite(denom) and denom > 0 else np.nan,
        "n_folds": int(a.size),
        "n_mape_undefined": int((~defined).sum()),
    }


# -------------------------------------------------------------- modelling
TIER_FULL, TIER_REDUCED, TIER_NONE = 120, 60, 0


def sufficiency_tier(n_obs):
    """Manuscript section 3.3.2's data-sufficiency tiers, applied at the
    category grain.

    This is not defensive padding - it was added after measuring the
    failure. On the first run Home & Novelty (116 trading days with sales,
    mean 7.3 units/day, one 150-unit spike) produced a 30-day forecast of
    6,999.6 units against an actual of 13. With ~23 months of span there
    are barely two annual cycles, so the yearly Fourier terms and the
    linear trend are fitted on almost no evidence and extrapolate without
    restraint.

      full     >=120 obs : weekly + yearly seasonality, linear growth
      reduced   60-119   : weekly only, FLAT growth, few changepoints
      none      < 60     : no Prophet - a trailing mean, flagged as such
    """
    if n_obs >= TIER_FULL:
        return "full"
    if n_obs >= TIER_REDUCED:
        return "reduced"
    return "none"


def fit_predict(train, future_ds, cal, regressors, tier):
    """Fit Prophet on an irregular trading-day series, predict future_ds.

    `train` is a (ds, y) frame of TRADING days only. Regressors constant
    inside this particular training slice are dropped - a constant column
    carries no information and gives Prophet an ill-posed coefficient.
    """
    from prophet import Prophet

    if tier == "full":
        m = Prophet(weekly_seasonality=True, yearly_seasonality=True,
                    daily_seasonality=False, uncertainty_samples=0)
    else:
        # Flat growth is the load-bearing part: it removes the trend term
        # that produced the 6,999-unit forecast, leaving level + weekly
        # shape, which is all a series this thin can support.
        m = Prophet(weekly_seasonality=True, yearly_seasonality=False,
                    daily_seasonality=False, uncertainty_samples=0,
                    growth="flat", n_changepoints=0)

    hist = train.copy()
    used = []
    if cal is not None and regressors:
        joined = cal.reindex(hist["ds"]).fillna(0.0).reset_index(drop=True)
        for col in regressors:
            if col in joined and joined[col].nunique() > 1:
                hist[col] = joined[col].to_numpy()
                m.add_regressor(col)
                used.append(col)

    m.fit(hist[["ds", "y"] + used])

    fut = pd.DataFrame({"ds": future_ds})
    if used:
        fcal = cal.reindex(fut["ds"]).fillna(0.0).reset_index(drop=True)
        for col in used:
            fut[col] = fcal[col].to_numpy()
    yhat = m.predict(fut)["yhat"].to_numpy()
    return np.maximum(yhat, 0.0), used


def rolling_mean_predict(train, future_ds, window):
    """Trailing mean over the last `window` TRADING days.

    Window counts trading days, not calendar days, because that is the
    series this harness scores: on a ~50%-closed calendar a 30-calendar-day
    window would cover roughly 15 real selling days, so counting calendar
    days here would quietly halve the window relative to what the same
    number means in the zero-filled pipeline.

    Emits one value per trading day in the horizon, so the 30-day total is
    level x (trading days in the window) - the same construction Prophet's
    output goes through, which is what makes the two comparable.
    """
    y = np.asarray(train["y"], dtype=float)
    w = y[-window:] if y.size >= window else y
    level = float(w.mean()) if w.size else 0.0
    return np.full(len(future_ds), max(level, 0.0))


def plausibility_cap(train_y, horizon, factor=3.0):
    """Largest 30-day total the history makes credible, times `factor`.

    A guard, not a model: any forecast above this is a divergent fit
    rather than a prediction. Every time it binds is COUNTED and reported
    - a cap that fires often is telling you the model is wrong, and
    hiding that behind a clipped number would be the actual error.
    """
    y = np.asarray(train_y, dtype=float)
    if y.size < horizon:
        return float(max(y.sum(), 1.0) * factor)
    blocks = pd.Series(y).rolling(horizon).sum().max()
    return float(max(blocks, 1.0) * factor)


def walk_forward(cat_df, trading_days, cal, horizon, max_folds, min_train,
                 model="prophet"):
    """Rolling-origin evaluation on the trading-day series.

    Origins step back from the end in `horizon`-sized blocks of CALENDAR
    time, so each test window is a real 30-day month. Within a window only
    trading days are predicted and summed, which is what makes the total
    comparable to the actual: both count the same days.
    """
    s = cat_df.sort_values("ds").reset_index(drop=True)
    if len(s) < min_train + 5:
        return None

    last = s["ds"].max()
    origins = []
    cut = last - pd.Timedelta(days=horizon)
    while len(origins) < max_folds:
        train_mask = s["ds"] <= cut
        if int(train_mask.sum()) < min_train:
            break
        origins.append(cut)
        cut = cut - pd.Timedelta(days=horizon)
    origins = sorted(origins)
    if len(origins) < 3:
        return None

    rows = []
    for cut in origins:
        train = s[s["ds"] <= cut]
        win_lo, win_hi = cut + pd.Timedelta(days=1), cut + pd.Timedelta(days=horizon)
        future_ds = [d for d in trading_days if win_lo <= d <= win_hi]
        if not future_ds:
            continue
        actual = float(s[(s["ds"] >= win_lo) & (s["ds"] <= win_hi)]["y"].sum())

        # A trailing mean has no trend or seasonality term, so it cannot
        # diverge - the sufficiency tiers and the plausibility cap exist to
        # contain Prophet's extrapolation and would only add noise here.
        # Same folds, same targets, same metrics; only the model differs.
        if model != "prophet":
            win = int(model.split("_")[-1])
            pred = float(np.sum(rolling_mean_predict(train, future_ds, win)))
            rows.append({"origin": cut, "actual_30d": actual,
                         "n_trading_days": len(future_ds),
                         "n_train_obs": len(train), "tier": "n/a", "cap": None,
                         "naive_scale": naive_scale_blocks(train["y"], horizon),
                         "pred_30d": pred, "fallback": 0, "capped": 0,
                         "regressors": "", "error": ""})
            continue

        tier = sufficiency_tier(len(train))
        cap = plausibility_cap(train["y"], horizon)
        base = {"origin": cut, "actual_30d": actual,
                "n_trading_days": len(future_ds), "n_train_obs": len(train),
                "tier": tier, "cap": cap,
                "naive_scale": naive_scale_blocks(train["y"], horizon)}

        if tier == "none":
            # Below the tier floor Prophet is not fitted at all, exactly as
            # section 3.3.2 prescribes; the advisory is a heuristic and the
            # row says so.
            rate = float(train["y"].tail(30).mean()) if len(train) else 0.0
            rows.append({**base, "pred_30d": rate * len(future_ds),
                         "fallback": 1, "capped": 0, "regressors": "",
                         "error": "below sufficiency tier - trailing mean"})
            continue
        try:
            yhat, used = fit_predict(train, future_ds, cal,
                                     CALENDAR_REGRESSORS, tier)
            raw = float(np.sum(yhat))
        except Exception as exc:                       # a Stan fit can fail
            rate = float(train["y"].tail(30).mean()) if len(train) else 0.0
            rows.append({**base, "pred_30d": rate * len(future_ds),
                         "fallback": 1, "capped": 0, "regressors": "",
                         "error": str(exc)[:80]})
            continue
        pred = min(raw, cap)
        rows.append({**base, "pred_30d": pred, "fallback": 0,
                     "capped": int(pred < raw), "regressors": ",".join(used),
                     "error": "", "pred_raw_30d": raw})
    return pd.DataFrame(rows) if rows else None


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--folds", type=int, default=12)
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--model", default="prophet",
                    choices=["prophet", "rolling_mean_30", "rolling_mean_90",
                             "rolling_mean_180"],
                    help="window sizes count TRADING days")
    ap.add_argument("--fsn", default=None,
                    help="restrict to FSN classes, e.g. F or F,S "
                         "(default: every SKU)")
    ap.add_argument("--no-db-write", action="store_true")
    args = ap.parse_args()
    _quiet()

    try:
        if args.model == "prophet":
            import prophet  # noqa: F401
    except ImportError:
        print("prophet is not installed - install it rather than silently "
              "substituting another model.")
        return 2

    fsn_only = ({x.strip().upper() for x in args.fsn.split(",") if x.strip()}
                if args.fsn else None)
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    daily, cal, days, info = load_inputs(con, fsn_only)
    con.close()

    trading = sorted(pd.to_datetime(
        days.loc[days["demand_observable"] == 1, "calendar_date"]).unique())
    trading = [pd.Timestamp(d) for d in trading]

    print("=" * 92)
    print("CATEGORY-LEVEL PROPHET on the colour-aware series")
    print("=" * 92)
    if fsn_only:
        print("SCOPE: FSN class %s only - %d of %d SKUs"
              % ("/".join(sorted(fsn_only)), info["n_skus_in_scope"],
                 info["n_skus_total"]))
    print("sales rows kept %d | dropped as not-demand-observable %d | out of scope %d"
          % (info["rows_kept"], info["rows_dropped_not_observable"],
             info["unmapped_rows"]))
    print("trading days %d | span %s .. %s | categories %d"
          % (len(trading), daily["ds"].min().date(), daily["ds"].max().date(),
             daily["forecast_category"].nunique()))
    print("regressors offered: %s" % ", ".join(CALENDAR_REGRESSORS))
    print("harness: horizon %d, up to %d folds, min_train %d "
          "(same as benchmark_category_level.py)\n"
          % (args.horizon, args.folds, args.min_train))

    results, folds_all = [], []
    for category, g in daily.groupby("forecast_category"):
        fold_df = walk_forward(g, trading, cal, args.horizon,
                               args.folds, args.min_train, args.model)
        if fold_df is None:
            print("  [skip] %-24s insufficient history (%d trading days)"
                  % (category, len(g)))
            continue
        fold_df.insert(0, "forecast_category", category)
        folds_all.append(fold_df)
        m = score(fold_df["actual_30d"], fold_df["pred_30d"],
                  fold_df["naive_scale"])
        m["forecast_category"] = category
        m["n_fallback"] = int(fold_df["fallback"].sum())
        m["n_capped"] = int(fold_df.get("capped", pd.Series(dtype=int)).sum())
        m["tier"] = fold_df["tier"].iloc[-1]
        m["mean_actual_30d"] = float(fold_df["actual_30d"].mean())
        results.append(m)
        print("  [ok]   %-24s folds %2d  MAPE %7.1f%%  MAE %7.1f  "
              "MASE %5.2f  RMSE %7.1f"
              % (category, m["n_folds"], m["mape_pct"], m["mae"],
                 m["mase"], m["rmse"]))

    if not results:
        print("\nNo category had enough history to evaluate.")
        return 1

    res = pd.DataFrame(results)[
        ["forecast_category", "tier", "n_folds", "mape_pct", "mae", "mase",
         "rmse", "mean_actual_30d", "n_mape_undefined", "n_fallback", "n_capped"]
    ].sort_values("mape_pct").reset_index(drop=True)
    folds_df = pd.concat(folds_all, ignore_index=True)

    # A scoped run writes its OWN files. Without this a `--fsn F` run
    # silently overwrote the all-SKU metrics with the Fast-only ones, which
    # is the worst kind of bug: the file still exists, still parses, and no
    # longer means what its name says.
    suffix = ("_" + "".join(sorted(fsn_only)).lower()) if fsn_only else ""
    if args.model != "prophet":
        suffix += "_" + args.model
    out_metrics = OUT_METRICS.replace(".csv", suffix + ".csv")
    out_forecast = OUT_FORECAST.replace(".csv", suffix + ".csv")
    res.to_csv(out_metrics, index=False, lineterminator="\n")
    folds_df.to_csv(out_forecast, index=False, lineterminator="\n")

    print("\n" + "=" * 92)
    print("SUMMARY REPORT - per category")
    print("=" * 92)
    print(res.to_string(index=False, float_format=lambda x: "%.2f" % x))

    # Pooled figures. WMAPE is volume-weighted and is the number comparable
    # to the existing category benchmark; the plain MAPE mean is a macro
    # average in which a 33-unit category counts as much as a 1,092-unit one.
    err = (folds_df["actual_30d"] - folds_df["pred_30d"]).abs()
    wmape = 100 * err.sum() / folds_df["actual_30d"].sum()
    print("\n" + "=" * 92)
    print("POOLED")
    print("=" * 92)
    print("  MAPE  (macro mean over categories) : %8.2f %%" % res["mape_pct"].mean())
    print("  MAE   (macro mean over categories) : %8.2f units" % res["mae"].mean())
    print("  MASE  (macro mean over categories) : %8.2f      %s"
          % (res["mase"].mean(),
             "(<1 beats the naive benchmark)" if res["mase"].mean() < 1
             else "(>1 = worse than naive)"))
    print("  RMSE  (macro mean over categories) : %8.2f units" % res["rmse"].mean())
    print("  WMAPE (volume-weighted, pooled)    : %8.2f %%" % wmape)
    print("")
    print("  WMAPE is NOT directly comparable to data/category_benchmark_summary.csv:")
    print("  that table scores a zero-filled calendar series over 2024-05-02..2026-07-31,")
    print("  this one scores trading days only over %s..%s. The targets differ, so the"
          % (daily["ds"].min().date(), daily["ds"].max().date()))
    print("  two numbers answer different questions and must not be ranked against"
          " each other.")
    n_cap = int(folds_df.get("capped", pd.Series(dtype=int)).sum())
    if n_cap:
        print("")
        print("  NOTE: the plausibility cap bound on %d of %d folds - those are"
              % (n_cap, len(folds_df)))
        print("        divergent fits that were clipped, not clean predictions.")
    n_fb = int(folds_df["fallback"].sum())
    if n_fb:
        print("\n  NOTE: %d of %d folds fell back to a trailing mean because the "
              "Prophet fit failed." % (n_fb, len(folds_df)))
        print("        Those folds are NOT Prophet results and are counted here.")

    print("\nWrote %s" % out_metrics)
    print("Wrote %s" % out_forecast)

    if not args.no_db_write:
        con = sqlite3.connect(DB_PATH)
        try:
            table = ("Result_Category_Prophet_Metrics" + suffix.upper()
                     if suffix else "Result_Category_Prophet_Metrics")
            res.assign(snapshot_date=pd.Timestamp.today().date().isoformat(),
                       model_type="prophet_colour_aware",
                       fsn_scope=("/".join(sorted(fsn_only)) if fsn_only else "all")
                       ).to_sql(table, con, if_exists="replace", index=False)
            con.commit()
            print("ustore.db: %s replaced (no existing table altered)" % table)
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
