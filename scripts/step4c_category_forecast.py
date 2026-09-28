"""
Step 4c of ETL: forecast each CATEGORY's total demand directly - add the
sales up first, then predict the sum - and validate it against the
naive "repeat last month" forecast on the project's own walk-forward harness.

This is what the Demand Forecast screen shows for a category. Before this
step existed, the category figure was the SUM of its Fast items' individual
forecasts (see backend/app.py::get_forecast_category), which covers only the
Fast items and inherits every one of their errors.

--- Why forecast the category directly ---

Two reasons, and neither is "adding first is inherently better":

  1. Coverage. Item forecasts exist only for Fast items (58 of 519), and two
     categories (Home & Novelty, Apparel Accessories) have none, so a sum of
     item forecasts cannot describe the category. This covers every item.
  2. The right window differs by level. Item demand is sparse and lumpy, and
     at item level a 30-day average wins (docs/FAST_MOVING_BENCHMARK.md);
     a category's total is smooth enough that a 6-month average wins.

For a trailing average, summing items and then averaging is IDENTICAL to
averaging the summed series, so "aggregate first" changes nothing on its own
- the gain is the longer window a smooth series can afford. It does matter
for non-linear models (Prophet, ML), which is where it was first observed,
but those did not win once tested fairly (below). Note too that
data/category_vs_sku_level.csv (WMAPE 49% at category vs 85% per item) shows
only that error on a TOTAL is relatively smaller, which any aggregation does;
it is not evidence that forecasting the total beats adding up item forecasts.

--- Why a 6-month average and not Prophet ---

This was tested, not assumed. Same 12 walk-forward folds, same actuals,
every forecast using only what was known at its origin, all 12 categories
(data/category_forecast_method_comparison.csv):

    method                                     beats "repeat last 30d"   pooled WMAPE
    6-month (180-day) average                          12 of 12              45.7%
    50/50 blend, Prophet + 6-month average             11 of 12              47.0%
    repeat last 30 days                                 -                    55.7%
    Prophet, zero-filled, expected trading days         5 of 12              61.1%
    Prophet as scripts/forecast_category_prophet.py     4 of 12              62.4%

scripts/forecast_category_prophet.py reports MASE 0.99 and 8-of-12 categories
below 1. Three things in that harness flatter it, and none of them survive
production:

  1. It scores over the trading days actually observed in each test window
     (hindsight) - production cannot know next month's closures.
  2. It trains on sale-days only, dropping zero-sale trading days, so it
     learns "units per selling day" and over-forecasts (+34% bias).
  3. Its MASE denominator is built from 30 ROWS of the trading-day series,
     about 43 calendar days, while the error is on 30-calendar-day windows.
     That inflates the denominator 1.41x. Rescaled to matching units the same
     Prophet output scores MASE 1.50 with 1 of 12 categories under 1.

The project's earlier 27-method category benchmark
(data/category_benchmark_summary.csv) independently ranked this same 6-month
average first. `--model` keeps the other trailing windows selectable; Prophet
is deliberately not offered here because it lost on every fair comparison.

--- The school-calendar adjustment (default) ---

`RM6_6month_180d+calendar` is the same 6-month average, multiplied by
forecasting/calendar_adjust.py's factor: when the next 30 days hold more
semester-break / exam days (which sell less) than the 180 days the average was
learned on, the total goes down to match. It never goes up. Store closures are
not used, since some are recorded after the fact. Measured by
scripts/test_calendar_adjustment.py on these same folds:

    method                        macro MASE   pooled WMAPE   bias
    6-month average (previous)       1.14         45.2%       -7%
    + calendar, lower only           1.09         41.8%      -15%

Better in 10 of 12 categories (bootstrap 95% CI of the MASE gap -0.108 to
-0.002), in both the older and the newer six folds, and by far most in the
break-heavy December window (error 75% -> 25%). It under-forecasts more on
average; the extra falls in break months, when the store is quiet anyway. The
"lower only" rule was chosen after seeing the uncapped version fail after
breaks, so treat the gain as slightly optimistic. `--model RM6_6month_180d`
gives the unadjusted average.

--- What "history" means here ---

Fact_Sales is a zero-filled panel that runs to the end of the calendar range
(2026-07-31) while the tallies stop at 2026-07-08, so its last 23 days are
padding, not sales. Left in, they drag every trailing average down ~13% and
show up as fake zero "actuals" in the last validation folds. The history used
for both fitting and validation is therefore cut at the last date on which
ANY item sold (history_end), and the forecast window starts the day after it.
step4_forecast_model.py uses the same rule, so a category figure and its
items describe the same 30 calendar days.

--- What gets written ---

  Result_Category_Forecast          one row per category per forecast day
  Result_Category_Forecast_Metrics  walk-forward accuracy per category

Both are new. Result_Forecast (per item) is not touched, and nothing that
reads it (step5_prescriptive.py, the dashboard's item views) changes. The
older step4b_category_forecast.py, which allocates a category total back to
items, is left as it was and writes different tables.

Metrics mirror Result_Forecast_Metrics' columns so the same "Reliable / Rough
estimate" tag can read either. One difference, stated because it changes what
the tag means: there `naive` is the last observed DAY held flat; here it is the
last 30 days' average - "repeat last month", which is what the tag's tooltip
actually says - a harder bar. The band on each day is +/- one RMSE of the
30-day walk-forward errors, split across the 30 days in proportion to each
day's share of the forecast (evenly when the forecast is flat) - i.e. the
model's typical 30-day miss, not day-to-day noise.

--- The day-by-day shape ---

A trailing average has no idea about dates, so on its own it draws a flat line.
The 30-day TOTAL is the validated part; `--shape` decides how that total is
spread across the days:

  prophet   (default) Prophet with weekly seasonality and the Dim_Date flags
            (enrollment, exam week, events, semester break, store closed) as
            regressors, trained on the category's own daily series. Only its
            SHAPE is used - its forecast is rescaled so the 30 days still add
            up to the 6-month average's total.
  weekday   the category's average by weekday over the last year, zero on
            published closures. Pure pandas; the automatic fallback if Prophet
            is missing or a fit fails (the row's model_type says which was used).
  flat      the old behaviour.

Tested (scripts/test_category_forecast_shape.py, data/category_forecast_shape_
test.csv; 12 categories x 12 folds, same folds as the totals): the Prophet shape
has 2.8% lower day-by-day error than a flat line and beats it in 65% of
category-months; the weekday shape 2.3% lower / 65%. The total is identical for
every shape by construction. Shipping Prophet AS-IS (its own total AND shape)
is worse than flat on both counts: daily error +3.5%, 30-day total WMAPE 48.7%
against 45.2%.

What the shape is and is not: it is the weekly rhythm (quiet Sundays, no sales
on published closures) plus whatever the school calendar explains. It is a small
improvement, not a forecast of individual spikes: one-off bulk orders are not
predictable from dates, the windows containing enrollment or event days show no
better skill than the rest (ratio 0.97-0.99), and Dim_Date holds only 34
enrollment days in the whole history.

Where the gain comes from: months containing 5+ semester-break days (4 of the
12 test windows) - the calendar's one clear effect. There the Prophet shape has
7.9% lower day-by-day error than flat (0.921; the weekday pattern 0.966). In
the other months it is a wash: Prophet 0.995, weekday 0.981. So a forecast
window with a break in it shows a real dip (the 2026-07-09..08-07 window has 7
break days, and the same season last year, 2025-07-14..08-12, had 9), while an
ordinary month is close to a gentle weekly wave. With only four break windows in
the history, treat the size of the dip as indicative.

Run (from the repo root, after step1b_categorize_products.py):
    python scripts/step4c_category_forecast.py [--model RM6_6month_180d]
                                               [--shape prophet|weekday|flat] [--no-db-write]
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.baselines import rolling_mean_fit_predict
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.shape import day_shape, load_calendar

DB_PATH = os.path.join(ROOT, "ustore.db")

# Harness settings identical to step4_forecast_model.py / model_benchmark.py.
HORIZON = 30
MIN_FOLDS = 3
MAX_FOLDS = 12
MIN_TRAIN = 60

RESIDUE = "Uncategorised"          # same bucket step1b_categorize_products.py uses
VALIDATION_METHOD = "walk_forward_30d_aggregate"

# Every entry is the SAME callable the benchmarks score under that name.
# Every entry is the SAME callable the benchmarks score under that name. Each
# factory takes a ctx dict; the calendar-adjusted one needs ctx["day_types"]
# (forecasting/calendar_adjust.py), aligned to the category series' index.
MODELS = {
    "RM6_6month_180d+calendar": lambda ctx: calendar_capped_fit_predict(
        rolling_mean_fit_predict(180), ctx["day_types"]),
    "RM6_6month_180d": lambda ctx: rolling_mean_fit_predict(180),
    "RM3_3month_90d": lambda ctx: rolling_mean_fit_predict(90),
    "rolling_mean_30": lambda ctx: rolling_mean_fit_predict(30),
}
# The 6-month average, lowered when the school calendar shows quieter days
# ahead (see the docstring and scripts/test_calendar_adjustment.py).
DEFAULT_MODEL = "RM6_6month_180d+calendar"

# What a forecast has to beat: repeat the last 30 days.
NAIVE = rolling_mean_fit_predict(30)

# Day-by-day shape (see the docstring): forecasting/shape.py, shared with
# step4_forecast_model.py, which gives each item its category's shape.
SHAPES = ("prophet", "weekday", "flat")
DEFAULT_SHAPE = "prophet"


def create_result_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Category_Forecast (
            forecast_id       INTEGER PRIMARY KEY,
            forecast_category TEXT NOT NULL,
            forecast_date     TEXT NOT NULL,
            yhat              REAL,
            yhat_lower        REAL,
            yhat_upper        REAL,
            model_type        TEXT,
            is_heuristic      INTEGER DEFAULT 0,
            snapshot_date     TEXT,
            history_end       TEXT
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Category_Forecast_Metrics (
            metric_id         INTEGER PRIMARY KEY,
            forecast_category TEXT NOT NULL,
            validation_method TEXT,
            period_scope      TEXT,
            n_obs             INTEGER,
            mae               REAL,
            rmse              REAL,
            mape              REAL,
            mase              REAL,
            naive_mae         REAL,
            naive_mase        REAL,
            beats_naive_mae   INTEGER,
            mean_actual_30d   REAL,
            snapshot_date     TEXT
        )""")


def load_category_series(con):
    """(wide, panel_end): one zero-filled DAILY column per forecast_category
    over the panel's full calendar range. Same construction as
    step1b_categorize_products.py::category_daily_series, so a 30-day window
    is a CALENDAR window."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    if "forecast_category" not in cols:
        raise SystemExit("Dim_Product.forecast_category is missing - run "
                         "scripts/step1b_categorize_products.py first")

    fact = pd.read_sql_query("""
        SELECT d.calendar_date, f.quantity_sold, p.forecast_category
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'
    """, con, parse_dates=["calendar_date"])
    if fact.empty:
        raise SystemExit("Fact_Sales has no sales - run step2 first")
    if fact["forecast_category"].isna().all():
        raise SystemExit("No product has a forecast_category - run "
                         "scripts/step1b_categorize_products.py first")

    fact["forecast_category"] = fact["forecast_category"].fillna(RESIDUE)
    idx = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    wide = (fact.groupby(["forecast_category", "calendar_date"])["quantity_sold"]
                .sum().unstack(0)
                .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))

    # Aggregation must neither create nor destroy units.
    assert abs(wide.to_numpy().sum() - fact["quantity_sold"].sum()) < 1e-6, \
        "unit total changed during aggregation - a join dropped rows"
    return wide.loc[:, wide.sum() > 0], idx[-1]


def trim_padding(wide):
    """Cut the series at the last date ANY category sold. Returns
    (trimmed, history_end, n_padding_days)."""
    total = wide.sum(axis=1)
    history_end = total[total > 0].index.max()
    return wide.loc[:history_end], history_end, len(wide) - len(wide.loc[:history_end])


def error_metrics(actual, pred, scales):
    """MAE / RMSE / MAPE / MASE over one category's 30-day folds. Same
    definitions as step4_forecast_model.py::error_metrics; MASE's denominator
    is the in-sample error of a naive forecast on 30-CALENDAR-day blocks of
    the training data (forecasting/evaluate.py), so it is in the unit the
    error is measured in."""
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    err = actual - pred
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    nz = actual != 0
    mape = float(np.mean(np.abs(err[nz] / actual[nz])) * 100) if nz.any() else None
    scales = np.asarray(scales, dtype=float)
    good = np.isfinite(scales) & (scales > 0)
    mase = float(mae / np.mean(scales[good])) if good.any() else None
    return mae, rmse, mape, mase


def validate(category, values, model, model_type):
    """Walk-forward metrics for one category, or None if its history cannot
    support MIN_FOLDS folds (the forecast is then flagged heuristic)."""
    folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    ev_m = walk_forward_evaluate(category, values, model, model_type, folds=folds)
    if not ev_m.sufficient:
        return None, ev_m.reason
    ev_n = walk_forward_evaluate(category, values, NAIVE, "repeat_last_30d", folds=folds)

    actual = np.array([r["actual_30d"] for r in ev_m.rows])
    m_pred = np.array([r["pred_30d"] for r in ev_m.rows])
    n_pred = np.array([r["pred_30d"] for r in ev_n.rows])
    scales = np.array([r["naive_scale"] for r in ev_m.rows])

    mae, rmse, mape, mase = error_metrics(actual, m_pred, scales)
    n_mae, _, _, n_mase = error_metrics(actual, n_pred, scales)
    return dict(
        validation_method=VALIDATION_METHOD, period_scope="overall",
        n_obs=len(folds),                 # folds scored, not days
        mae=mae, rmse=rmse, mape=mape, mase=mase,
        naive_mae=n_mae, naive_mase=n_mase,
        beats_naive_mae=int(mae < n_mae),
        mean_actual_30d=float(actual.mean()),
    ), ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("---")[0].strip())
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS),
                    help="trailing-average window (default: %(default)s)")
    ap.add_argument("--shape", default=DEFAULT_SHAPE, choices=SHAPES,
                    help="how the 30-day total is spread across the days "
                         "(default: %(default)s; see the docstring)")
    ap.add_argument("--no-db-write", action="store_true",
                    help="run and report, leave the database untouched")
    args = ap.parse_args()
    model_type = args.model

    con = sqlite3.connect(DB_PATH)
    wide, panel_end = load_category_series(con)
    series, history_end, n_padding = trim_padding(wide)
    cal = load_calendar(con)
    model = MODELS[model_type]({"day_types": load_day_types(con, series.index, HORIZON)})

    snapshot_date = history_end.strftime("%Y-%m-%d")
    dates = pd.date_range(history_end + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    n_missing_cal = int((~dates.isin(cal.index)).sum())

    print(f"Model: {model_type} | horizon {HORIZON} | folds {MIN_FOLDS}-{MAX_FOLDS} "
          f"| min_train {MIN_TRAIN}  (identical to step4 / model_benchmark.py)")
    print(f"Series: {series.shape[1]} categories x {len(series)} days "
          f"({series.index[0].date()} .. {history_end.date()})")
    print(f"Panel runs to {panel_end.date()}; last day any item sold is "
          f"{history_end.date()} -> {n_padding} trailing padding days excluded "
          f"from fitting and validation.")
    print(f"Forecast window: {dates[0].date()} .. {dates[-1].date()}")
    print(f"Day-by-day shape: {args.shape}"
          + ("" if args.shape == "flat" else " (the 30-day total is unchanged; see the docstring)"))
    if n_missing_cal:
        print(f"NOTE: {n_missing_cal} forecast day(s) are past the end of Dim_Date - their "
              f"calendar flags are treated as 0.")
    print()

    forecast_rows, metric_rows, report, shape_used = [], [], [], {}
    for cat in series.columns:
        v = series[cat].to_numpy(float)
        metrics, reason = validate(cat, v, model, model_type)
        level = float(np.asarray(model(v, HORIZON), dtype=float).ravel()[0])
        total = level * HORIZON
        weights, used = day_shape(args.shape, v, series.index, cal, dates)
        shape_used[cat] = (used, float(weights.max() * HORIZON), float(weights.min() * HORIZON))
        row_model = model_type if used == "flat" else f"{model_type}+{used}_shape"

        # Each day carries its share of the 30-day total, and its share of the
        # typical 30-day miss (RMSE): an even split reproduces the old flat band.
        for d, w in zip(dates, weights):
            yhat = total * w
            spread = None if not metrics else metrics["rmse"] * w
            forecast_rows.append(dict(
                forecast_category=cat, forecast_date=d.strftime("%Y-%m-%d"),
                yhat=round(yhat, 6),
                yhat_lower=None if spread is None else round(max(yhat - spread, 0.0), 6),
                yhat_upper=None if spread is None else round(yhat + spread, 6),
                model_type=row_model, is_heuristic=0 if metrics else 1,
                snapshot_date=snapshot_date,
                history_end=history_end.strftime("%Y-%m-%d"),
            ))
        if metrics:
            metric_rows.append(dict(forecast_category=cat, snapshot_date=snapshot_date, **metrics))
        report.append(dict(category=cat, per_day=level, total_30d=total,
                           mae=metrics["mae"] if metrics else np.nan,
                           naive_mae=metrics["naive_mae"] if metrics else np.nan,
                           mase=metrics["mase"] if metrics else np.nan,
                           beats=metrics["beats_naive_mae"] if metrics else None,
                           reason=reason))

    forecast_df = pd.DataFrame(forecast_rows)
    metrics_df = pd.DataFrame(metric_rows)
    rep = pd.DataFrame(report).sort_values("total_30d", ascending=False)

    # The shape only redistributes: each category's 30 days must still add up to
    # the validated 6-month-average total. Checked BEFORE anything is written.
    written = forecast_df.groupby("forecast_category")["yhat"].sum()
    drift = (written - rep.set_index("category")["total_30d"]).abs().max()
    assert drift < 1e-3, f"the day-by-day shape changed a category's 30-day total (max drift {drift:.6f})"

    if not args.no_db_write:
        create_result_tables(con)
        # Clear + refill in one transaction, matching step4: on failure SQLite
        # rolls back to the previous run rather than to nothing.
        con.execute("DELETE FROM Result_Category_Forecast")
        con.execute("DELETE FROM Result_Category_Forecast_Metrics")
        con.executemany(
            """INSERT INTO Result_Category_Forecast
               (forecast_category, forecast_date, yhat, yhat_lower, yhat_upper,
                model_type, is_heuristic, snapshot_date, history_end)
               VALUES (:forecast_category,:forecast_date,:yhat,:yhat_lower,:yhat_upper,
                       :model_type,:is_heuristic,:snapshot_date,:history_end)""",
            forecast_df.to_dict("records"))
        con.executemany(
            """INSERT INTO Result_Category_Forecast_Metrics
               (forecast_category, validation_method, period_scope, n_obs, mae, rmse,
                mape, mase, naive_mae, naive_mase, beats_naive_mae, mean_actual_30d,
                snapshot_date)
               VALUES (:forecast_category,:validation_method,:period_scope,:n_obs,:mae,
                       :rmse,:mape,:mase,:naive_mae,:naive_mase,:beats_naive_mae,
                       :mean_actual_30d,:snapshot_date)""",
            metrics_df.to_dict("records"))
        con.commit()

    # Informational: how far the sum of the Fast items' own forecasts sits from
    # the category figure. They are different things (see docstring), so this is
    # printed, never asserted.
    item_sum = {}
    has_items = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='Result_Forecast'").fetchone()
    if has_items:
        item_sum = dict(con.execute("""
            SELECT p.forecast_category, SUM(f.yhat)
            FROM Result_Forecast f JOIN Dim_Product p ON p.product_id = f.product_id
            GROUP BY 1""").fetchall())
    con.close()

    print(f"{'category':22s} {'30d fcst':>9s} {'MAE':>8s} {'repeat-30d':>10s} "
          f"{'MASE':>6s}  beats   {'Fast items sum':>14s}")
    for r in rep.itertuples():
        tag = "--" if r.beats is None else ("yes" if r.beats else "NO")
        fi = item_sum.get(r.category)
        print(f"{r.category:22s} {r.total_30d:9.0f} {r.mae:8.1f} {r.naive_mae:10.1f} "
              f"{r.mase:6.2f}  {tag:5s}   {'' if fi is None else f'{fi:14.0f}'}")

    if args.shape != "flat":
        print("\nDay-by-day shape (busiest day / quietest day, as a multiple of the average day):")
        for cat, (used, hi, lo) in sorted(shape_used.items()):
            print(f"  {cat:22s} {used:8s} busiest {hi:4.1f}x  quietest {lo:4.1f}x")

    scored = rep[rep["beats"].notna()]
    print(f"\nBeat 'repeat last 30 days': {int(scored['beats'].sum())} of {len(scored)} categories "
          f"| MASE < 1 in {int((scored['mase'] < 1).sum())} of {len(scored)}")
    if len(scored) < len(rep):
        print(f"Not validated (too little history, flagged is_heuristic): "
              f"{', '.join(rep[rep['beats'].isna()]['category'])}")

    assert (forecast_df["yhat"] >= 0).all(), "negative forecast written"
    assert (forecast_df.groupby("forecast_category").size() == HORIZON).all(), \
        "a category is missing forecast days"
    print("\n[PASS] no negative forecasts; every category has exactly "
          f"{HORIZON} forecast days; each 30-day total unchanged by the shape")
    print("[dry run - database not modified]" if args.no_db_write else
          f"Wrote {len(forecast_df)} rows to Result_Category_Forecast and "
          f"{len(metrics_df)} to Result_Category_Forecast_Metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
