"""
Phase 3 of ETL: demand forecasting for Fast SKUs, scored on 30-day aggregates.

Every Fast SKU is forecast with the SAME model, selected by `--model`.
The default is `rolling_mean_30` - the trailing 30-day mean. Whichever is
chosen, it is literally the callable from `forecasting/` that
`model_benchmark.py` and `scripts/benchmark_fast_raw_vs_clean.py` score
under the same name, so their findings are findings about THIS model, not
about a near relative.

--- Why rolling_mean_30 ---

`docs/FAST_MOVING_BENCHMARK.md` scored 37 methods across 10 families on
the 58 Fast SKUs over identical walk-forward folds - trailing averages,
quantiles, exponential smoothing, intermittent-demand (Croston/SBA/TSB),
hurdle, Holt-Winters, six ML learners, pooled ML, Prophet, calendar-lag
and top-down hierarchical. Nothing beat a trailing average (section 6.1),
and the top eight sat within 10% of each other on MASE. `rolling_mean_30`
has the best MAE of the lot (40.02) and is the simplest thing in the
comparison, which is the tie-break when the error metric will not
separate the candidates.

The one thing it is NOT best at is coverage: it forecasts exactly zero
whenever its 30-day window contains no sale, which on this catalogue is
32 of 58 Fast items, so those SKUs carry no EOQ. `tsb` prices all 58 for
a 0.5% worse MASE. That trade-off is deferred decision **B3** and is not
settled here - run `--model tsb` to see the other side of it.
`Result_Forecast.model_type` records which model wrote each row, and
`step5_prescriptive.py` reads that column rather than assuming, so the
provenance label follows the choice automatically.

Neither model has trend or seasonality: the forecast is a flat rate per
SKU. Nothing about the calendar enters the prediction.

--- What this file used to be ---

It fit Prophet per SKU (logistic growth, five Dim_Date regressors, 25
changepoints for a "standard" tier, a fixed linear trend for a
"simplified" tier, MCMC(1000) production fits) and took 1-2 hours behind
a cmdstan build. Then it briefly used a full-history (expanding) mean.
Both are gone. Consequences worth stating plainly:

  - The forecast is a CONSTANT per SKU. No trend, no weekly seasonality,
    no calendar effects: enrollment periods, exam weeks, event days and
    semestral breaks no longer move the prediction, because a mean has no
    design matrix to put them in.
  - Runtime is seconds, and no toolchain is required.
  - Nothing in the repository imports prophet any more.

--- The model ---

Both available models emit a flat rate held across the horizon, so the
row shape is identical and only `level` differs:

    tsb              level = p_hat * z_hat, where z_hat is the smoothed
                     demand SIZE (updated on days with a sale) and p_hat
                     the smoothed demand PROBABILITY (updated EVERY day,
                     including zeros - that is what makes it decay on a
                     dying SKU where Croston holds flat). alpha=beta=0.1,
                     the values the benchmark scored.
    rolling_mean_30  level = mean of the trailing 30 observations (fewer
                     if the training slice is shorter), clipped at 0

    yhat       = level, on each of the 30 horizon days
    yhat_lower = max(level - SD, 0)
    yhat_upper = level + SD

SD is the sample standard deviation (ddof=1) of the FULL series, not of
the 30-day window. The window is the right basis for the level (it tracks
recent demand) but a poor one for dispersion: a SKU whose last 30 days
happen to be flat would get a zero-width interval, which claims a
certainty the model does not have. The band is display-only - it is not
read by step5 or by anything else.

--- Validation: the same harness as the benchmark ---

Scoring is `forecasting.evaluate.walk_forward_evaluate` at the SAME
settings `model_benchmark.py` uses:

    horizon 30 | min_folds 3 | max_folds 12 | min_train 60

which means the scoring unit is ONE 30-DAY AGGREGATE PER FOLD, not 30
daily points. That matters, and it is the reason this replaced the old
80/20 daily holdout:

  - `Result_Forecast` serves a 30-day forecast, and `step5_prescriptive.py`
    (in --demand-basis=forecast) consumes its 30-day total. Scoring daily
    one-step-ahead accuracy measured a quantity nothing consumes.
  - The old 80/20 holdout was also not a fair comparison against the naive
    baseline: the model was frozen at the split and predicted up to ~70
    days ahead while `naive` re-read yesterday's ACTUAL at every test
    point. Persistence was being handed one-step-ahead information the
    model never got. Under rolling origins both methods see exactly the
    same training slice per fold.
  - Section 3.3.4 and Figure 3 both promise walk-forward validation. The
    80/20 holdout was not that. This is.

Leakage is enforced per fold by `Fold.assert_no_leakage()`: each training
slice ends strictly before its origin, and the test window is
[origin, origin + horizon).

The daily series is reindexed over the FULL calendar span, zero-filled -
the same convention as `step5_prescriptive.py::load_series` and
`model_benchmark.py::load_daily_series`. A tallied day with no row for a
SKU is a day that SKU sold zero, which is real information. (The previous
version built each SKU's series from only its own Fact_Sales dates, which
gave series as short as 6 rows and left 5 SKUs unscoreable. Under the
shared convention every Fast SKU reaches the full 12 folds.)

--- Tiers ---

The sale-day tiers (>=60 standard, 30-59 simplified, <30 minimal) are now
DESCRIPTIVE LABELS ONLY. They do not select a model and no longer select a
validation method either - every SKU gets the same model and the same
harness. They are still computed and still written to
Result_Forecast_Metrics because `tools/tier_counts.py` reconciles
Divergence Register #17 against them and the README quotes the 38/10/10
split. "Sale-days" remain distinct calendar dates with quantity_sold > 0,
never raw Fact_Sales row count.

Metrics, per SKU, per period scope:
  - MAE / RMSE / MAPE / MASE on the 30-day aggregates.
  - MASE is available now that scoring is aggregate: its denominator is
    the naive scale over 30-day BLOCKS of the training slice, in the same
    unit as the errors. It was not computable under the old daily holdout.
  - Naive baseline = `forecasting.baselines.naive_fit_predict()`, scored on
    IDENTICAL folds (the fold layout is computed once per SKU and handed to
    both methods), so neither can be advantaged by a different split.
  - Scopes: a fold is 'semestral_break' when MORE THAN HALF its 30 test
    days are is_sem_break=1, else 'standard_period'; 'overall' pools all
    folds. MAPE is undefined/unstable when actuals are at or near zero,
    which happens disproportionately in breaks, so MAPE<=20% is assessed
    only on 'overall' and 'standard_period'.

is_sem_break is the only Dim_Date column read, and only to assign those
scopes. It does not enter the forecast.

Writes to (does not touch Fact_Sales):
  - Result_Forecast          (per SKU, per forecast date, 30-day horizon)
  - Result_Forecast_Metrics  (per SKU, per validation-period scope)

Safe to re-run: both result tables are cleared and refilled in one
transaction, so an interrupted run leaves the previous forecasts in place.

Filename note: this was step4_prophet_forecast.py until the model changed.
Older docs and log entries refer to it under that name.
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

# forecasting/ lives at the repo root, one level above scripts/ - Python only
# auto-adds the directory of the script being RUN to sys.path. Mirrors what
# step5_prescriptive.py and conftest.py do.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import (
    ewma_fit_predict, naive_fit_predict, rolling_mean_fit_predict,
)
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.intermittent import tsb_fit_predict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ustore.db")

# Harness settings, identical to model_benchmark.py so the two are comparable.
HORIZON = 30
MIN_FOLDS = 3
MAX_FOLDS = 12
MIN_TRAIN = 60

WINDOW = 30                    # the rolling mean's trailing window
TSB_ALPHA = 0.1                # size smoothing      - benchmark's values
TSB_BETA = 0.1                 # probability smoothing

# Every entry is the SAME callable the benchmarks score under that name.
# Keyed by the string written to Result_Forecast.model_type, so the label
# and the model can never drift apart.
#
# Each factory takes a `ctx` dict so a model that needs calendar context
# can have it. Prophet is the only one that does - it is bound to the
# shared daily index and to Dim_Date's regressor columns - but every entry
# takes the same argument rather than special-casing one, so adding
# another calendar-aware model does not require changing the call site.
MODELS = {
    "tsb": (lambda ctx: tsb_fit_predict(TSB_ALPHA, TSB_BETA),
            f"TSB p_hat*z_hat, alpha={TSB_ALPHA} beta={TSB_BETA}, "
            f"flat over {HORIZON} days"),
    "rolling_mean_30": (lambda ctx: rolling_mean_fit_predict(WINDOW),
                        f"trailing {WINDOW}-day mean, flat over {HORIZON} days"),
    "ewma_a0.1": (lambda ctx: ewma_fit_predict(0.1),
                  f"EWMA alpha=0.1, flat over {HORIZON} days"),
    "prophet": (lambda ctx: _make_prophet(ctx),
                f"Prophet, weekly+yearly seasonality with Dim_Date calendar "
                f"regressors, VARYING over {HORIZON} days"),
}
# Measured, not assumed. On the 58 Fast SKUs over identical folds
# (data/fastmoving_benchmark_results.csv):
#
#   median per-SKU MAE   rolling_mean_30 26.83 | tsb 28.06
#                        prophet_plain   33.17 | prophet_cal 33.83 | naive 51.50
#   head-to-head         prophet beats rolling_mean_30 on 12 of 58 SKUs (21%)
#
# Prophet sits nearer the naive baseline than the trailing mean. The reason is
# structural rather than a tuning gap: ~50% of days are zeros and the span is
# 23 months, so the trend and yearly-seasonality terms are fitted on almost no
# evidence and then extrapolated - Home & Novelty forecast 6,999 units against
# an actual of 13 before the sufficiency tiers were added. A trailing mean has
# no trend term to run away with, which is exactly why it wins here.
#
# `prophet` stays in MODELS and is still selectable with --model prophet: it is
# the comparison the manuscript's sections 2.1.4 and 3.3.2 need, and a measured
# negative result is worth more than an untested claim.
DEFAULT_MODEL = "rolling_mean_30"

# Prophet is the only model here whose forecast is not flat. Recorded as a
# constant so append_forecast and the summary print agree about which
# models produce a curve, instead of each testing the name separately.
CURVE_MODELS = {"prophet"}


def _make_prophet(ctx):
    """Prophet bound to the benchmark's shared daily index and calendar.

    Returns the standard `fit_predict(train, horizon)` callable, so it
    drops into the same harness as the trailing averages and is scored by
    `walk_forward_evaluate` on identical folds.

    The calendar columns come from Dim_Date, which
    `populate_dim_date.py` fills from `calendar_ranges.csv`.
    `semester_week` is deliberately NOT among them - Divergence #8 records
    that its continuous form resets each term and extrapolates badly
    across the boundary.
    """
    from forecasting.prophet_model import load_calendar, prophet_fit_predict
    cal = load_calendar(ctx["con"], ctx["index"]) if ctx.get("con") is not None else None
    return prophet_fit_predict(ctx["index"], cal, "prophet")

VALIDATION_METHOD = "walk_forward_30d_aggregate"
MAPE_PASS_THRESHOLD = 20.0

# Descriptive sale-day tiers. Not used to select anything - see the docstring.
STANDARD_MIN_SALE_DAYS = 60
SIMPLIFIED_MIN_SALE_DAYS = 30

SCOPE_COLUMN = "is_sem_break"
# A fold counts as 'semestral_break' when at least a third of its 30 test days
# are break days. NOT a majority rule: at 30-day aggregation NO window on this
# calendar is majority-break - the fullest is 14/30 (0.47), because a semestral
# break is shorter than half a 30-day tile and never aligns with one. A >50%
# rule is therefore structurally unreachable and silently produced zero break
# rows. At 1/3 exactly one fold per SKU qualifies (2025-12-04..2026-01-02,
# 14/30 break days), so every break-scope metric below rests on a SINGLE 30-day
# window and should be read as indicative, not as a comparison of equals with
# the 12-fold standard-period figure. The daily harness this replaced could
# separate the two cleanly; that resolution is the price of scoring the 30-day
# aggregate the pipeline actually serves.
BREAK_FOLD_THRESHOLD = 1.0 / 3.0


def create_result_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Forecast (
            forecast_id   INTEGER PRIMARY KEY,
            product_id    INTEGER NOT NULL,
            forecast_date TEXT NOT NULL,
            yhat          REAL,
            yhat_lower    REAL,
            yhat_upper    REAL,
            model_type    TEXT,
            is_heuristic  INTEGER DEFAULT 0,
            snapshot_date TEXT,
            FOREIGN KEY (product_id) REFERENCES Dim_Product (product_id)
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Forecast_Metrics (
            metric_id            INTEGER PRIMARY KEY,
            product_id            INTEGER NOT NULL,
            item_name              TEXT,
            tier                    TEXT,
            validation_method       TEXT,
            period_scope            TEXT,
            n_obs                   INTEGER,
            mae                     REAL,
            rmse                    REAL,
            mape                    REAL,
            naive_mae               REAL,
            naive_rmse              REAL,
            naive_mape              REAL,
            beats_naive_mae         INTEGER,
            meets_mape_threshold    INTEGER,
            snapshot_date           TEXT,
            FOREIGN KEY (product_id) REFERENCES Dim_Product (product_id)
        );
    """)
    # MASE only became computable when scoring moved to 30-day aggregates, so
    # it is added to an existing table rather than assumed present.
    cols = {r[1] for r in con.execute("PRAGMA table_info(Result_Forecast_Metrics)")}
    for name in ("mase", "naive_mase"):
        if name not in cols:
            con.execute(f"ALTER TABLE Result_Forecast_Metrics ADD COLUMN {name} REAL")


def load_common(con):
    products = pd.read_sql(
        "SELECT product_id, item_name, fsn_class, is_hvl FROM Dim_Product", con
    )
    fact = pd.read_sql(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold
           FROM Fact_Sales f JOIN Dim_Date d ON f.date_id = d.date_id""",
        con,
    )
    fact["calendar_date"] = pd.to_datetime(fact["calendar_date"])
    dim_date = pd.read_sql(f"SELECT calendar_date, {SCOPE_COLUMN} FROM Dim_Date", con)
    dim_date["calendar_date"] = pd.to_datetime(dim_date["calendar_date"])
    return products, fact, dim_date


def build_calendar(fact, dim_date):
    """The full daily index every SKU's series is reindexed onto, plus the
    is_sem_break flag aligned to it. Same span convention as
    step5_prescriptive.py::load_series."""
    idx = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")
    breaks = (dim_date.set_index("calendar_date")[SCOPE_COLUMN]
              .reindex(idx).fillna(0).to_numpy(dtype=float))
    return idx, breaks


def build_series(fact_one, idx):
    """One SKU's dense daily series over the full calendar, zero-filled."""
    return (fact_one.groupby("calendar_date")["quantity_sold"].sum()
            .reindex(idx, fill_value=0.0).astype(float).to_numpy())


def fold_scope(fold, breaks):
    """'semestral_break' if more than half the fold's test days are break
    days, else 'standard_period'. A 30-day window straddles the boundary far
    more often than a single day does, so it needs a rule rather than a
    lookup."""
    window = breaks[fold.test_start:fold.test_end]
    if window.size == 0:
        return "standard_period"
    return "semestral_break" if window.mean() > BREAK_FOLD_THRESHOLD else "standard_period"


def error_metrics(actual, pred, scales):
    """MAE / RMSE / MAPE / MASE over a set of 30-day aggregate folds."""
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


def metrics_rows(model_rows, naive_rows, breaks, folds):
    """One metrics row per period scope, from folds already scored."""
    scopes = np.array([fold_scope(f, breaks) for f in folds])
    actual = np.array([r["actual_30d"] for r in model_rows], dtype=float)
    m_pred = np.array([r["pred_30d"] for r in model_rows], dtype=float)
    n_pred = np.array([r["pred_30d"] for r in naive_rows], dtype=float)
    scales = np.array([r["naive_scale"] for r in model_rows], dtype=float)

    out = []
    for scope in ("overall", "standard_period", "semestral_break"):
        mask = np.ones(len(folds), dtype=bool) if scope == "overall" else (scopes == scope)
        n = int(mask.sum())
        if n == 0:
            continue
        mae, rmse, mape, mase = error_metrics(actual[mask], m_pred[mask], scales[mask])
        n_mae, n_rmse, n_mape, n_mase = error_metrics(actual[mask], n_pred[mask], scales[mask])
        out.append(dict(
            period_scope=scope,
            n_obs=n,                       # folds scored in this scope, not days
            mae=mae, rmse=rmse, mape=mape, mase=mase,
            naive_mae=n_mae, naive_rmse=n_rmse, naive_mape=n_mape, naive_mase=n_mase,
            beats_naive_mae=int(mae < n_mae),
            # MAPE is not assessed on break scopes: actuals sit at or near
            # zero there and the ratio blows up.
            meets_mape_threshold=(
                None if scope == "semestral_break"
                else (int(mape <= MAPE_PASS_THRESHOLD) if mape is not None else None)
            ),
        ))
    return out


def append_forecast(product_id, level, spread, is_heuristic, last_date, snapshot_date,
                    model_type, rows):
    """30 flat rows: the model's level, banded by +/- 1 SD, clipped at 0.

    `model_type` is passed in rather than read from a module constant so
    that the label written to Result_Forecast is the one `--model`
    actually selected. step5_prescriptive.py reads that column to build
    its provenance string, so a stale constant here would mislabel every
    downstream number."""
    # 6 dp, not 3. A trailing mean's level is order 1-10, so rounding to 3
    # was lossless in practice. TSB's level is a RATE (p_hat * z_hat) and
    # can legitimately be 1e-4 or smaller, so round(level, 3) silently
    # turned 30 of 58 positive forecasts into hard zeros - which reads
    # downstream as "this SKU cannot be ordered" rather than "this SKU is
    # forecast to sell very little". Rounding must not manufacture a
    # category change. It is still rounding, not truncation to a
    # threshold: a level genuinely too small to order stays too small,
    # it just stops being reported as an exact zero.
    # `level` may be a scalar (every flat model) or a HORIZON-length array
    # (Prophet). Broadcasting here rather than at the call site means the
    # 30 rows written are the model's ACTUAL day-by-day output: writing a
    # Prophet forecast as its own mean would discard exactly the calendar
    # shape the model was chosen for, and would be Prophet in name only.
    curve = np.broadcast_to(np.asarray(level, dtype=float).ravel(),
                            (HORIZON,)) if np.ndim(level) == 0 else \
        np.asarray(level, dtype=float).ravel()
    if curve.size != HORIZON:
        raise ValueError(
            f"model returned {curve.size} values for a {HORIZON}-day horizon")

    dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    for d, y in zip(dates, curve):
        rows.append(dict(
            product_id=product_id, forecast_date=d.strftime("%Y-%m-%d"),
            yhat=round(float(y), 6),
            yhat_lower=round(max(float(y) - spread, 0.0), 6),
            yhat_upper=round(float(y) + spread, 6),
            model_type=model_type, is_heuristic=is_heuristic, snapshot_date=snapshot_date,
        ))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("---")[0].strip())
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS),
                    help="forecasting model (default: %(default)s). "
                         "Use rolling_mean_30 to reproduce the previously "
                         "published numbers.")
    args = ap.parse_args()
    model_type = args.model
    make_model, model_desc = MODELS[model_type]

    con = sqlite3.connect(DB_PATH)
    create_result_tables(con)

    products, fact, dim_date = load_common(con)
    idx, breaks = build_calendar(fact, dim_date)

    # Tier sufficiency = distinct SALE-DAYS (quantity_sold > 0), not raw row
    # count: Fact_Sales carries a real zero-quantity row for every calendar day
    # in a densely-tallied month. Descriptive only - see the docstring.
    obs_counts = (fact[fact["quantity_sold"] > 0]
                  .groupby("product_id")["calendar_date"].nunique().rename("n_obs"))
    products = products.merge(obs_counts, on="product_id", how="left").fillna({"n_obs": 0})
    products["n_obs"] = products["n_obs"].astype(int)

    fast = products[products["fsn_class"] == "F"].copy()
    fast["tier"] = np.select(
        [fast["n_obs"] >= STANDARD_MIN_SALE_DAYS, fast["n_obs"] >= SIMPLIFIED_MIN_SALE_DAYS],
        ["standard", "simplified"], default="minimal",
    )

    model = make_model({"index": idx, "con": con})
    naive = naive_fit_predict()
    snapshot_date = fact["calendar_date"].max().strftime("%Y-%m-%d")
    last_date = idx[-1]

    forecast_rows, metric_rows, unscored = [], [], []
    by_product = dict(list(fact.groupby("product_id")))

    print(f"Model: {model_type} ({model_desc})")
    print(f"Harness: {VALIDATION_METHOD} | horizon {HORIZON} | folds {MIN_FOLDS}-{MAX_FOLDS} "
          f"| min_train {MIN_TRAIN}  (identical to model_benchmark.py)\n")

    for _, row in fast.iterrows():
        pid, name, tier = int(row["product_id"]), row["item_name"], row["tier"]
        g = by_product.get(pid)
        values = build_series(g, idx) if g is not None else np.zeros(len(idx))

        # ONE fold layout per SKU, handed to both methods, so neither can be
        # advantaged by a different split.
        folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        ev_m = walk_forward_evaluate(pid, values, model, model_type, folds=folds)
        scored = ev_m.sufficient

        if scored:
            ev_n = walk_forward_evaluate(pid, values, naive, "naive", folds=folds)
            for r in metrics_rows(ev_m.rows, ev_n.rows, breaks, folds):
                metric_rows.append(dict(
                    product_id=pid, item_name=name, tier=tier,
                    validation_method=VALIDATION_METHOD, snapshot_date=snapshot_date, **r,
                ))
        else:
            unscored.append((name, ev_m.reason))
            metric_rows.append(dict(
                product_id=pid, item_name=name, tier=tier,
                validation_method="none", period_scope="overall", n_obs=0,
                mae=None, rmse=None, mape=None, mase=None,
                naive_mae=None, naive_rmse=None, naive_mape=None, naive_mase=None,
                beats_naive_mae=None, meets_mape_threshold=None, snapshot_date=snapshot_date,
            ))

        # Production fit: the same callable, on the whole series.
        out = np.asarray(model(values, HORIZON), dtype=float).ravel()
        # Flat models are stored as their single level; a curve model keeps
        # all 30 values. `reported` is only what this line prints.
        level = out if model_type in CURVE_MODELS else float(out[0])
        reported = float(np.mean(out))
        spread = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        append_forecast(pid, level, spread, 0 if scored else 1,
                        last_date, snapshot_date, model_type, forecast_rows)

        shape = "mean" if model_type in CURVE_MODELS else "flat"
        print(f"[{tier:10}] product_id={pid:4} sale_days={int(row['n_obs']):4} "
              f"folds={ev_m.n_folds:3} yhat({shape})={reported:8.3f}  {name}")

    forecast_df = pd.DataFrame(forecast_rows)
    metrics_df = pd.DataFrame(metric_rows)

    # Clear + refill in one transaction: on failure SQLite rolls back to the
    # previous run's forecasts rather than to nothing.
    con.execute("DELETE FROM Result_Forecast")
    con.execute("DELETE FROM Result_Forecast_Metrics")
    con.executemany(
        """INSERT INTO Result_Forecast
           (product_id, forecast_date, yhat, yhat_lower, yhat_upper, model_type, is_heuristic, snapshot_date)
           VALUES (:product_id, :forecast_date, :yhat, :yhat_lower, :yhat_upper, :model_type, :is_heuristic, :snapshot_date)""",
        forecast_df.to_dict("records"),
    )
    con.executemany(
        """INSERT INTO Result_Forecast_Metrics
           (product_id, item_name, tier, validation_method, period_scope, n_obs, mae, rmse, mape, mase,
            naive_mae, naive_rmse, naive_mape, naive_mase, beats_naive_mae, meets_mape_threshold, snapshot_date)
           VALUES (:product_id, :item_name, :tier, :validation_method, :period_scope, :n_obs, :mae, :rmse, :mape, :mase,
                   :naive_mae, :naive_rmse, :naive_mape, :naive_mase, :beats_naive_mae, :meets_mape_threshold, :snapshot_date)""",
        metrics_df.to_dict("records"),
    )
    con.commit()

    # ================= REPORT =================
    print("\n=== TIER BREAKDOWN (descriptive only - every SKU got the same model and harness) ===")
    print(fast["tier"].value_counts().to_string())

    overall = metrics_df[metrics_df["period_scope"] == "overall"]
    scored_df = overall[overall["validation_method"] != "none"]

    print(f"\nSKUs scored: {len(scored_df)} / {len(fast)}")
    if unscored:
        print(f"NOT scored ({len(unscored)} - could not support {MIN_FOLDS} folds, is_heuristic=1):")
        for name, reason in unscored:
            print(f"   {name}: {reason}")

    print(f"\nSKUs with overall MAPE <= {MAPE_PASS_THRESHOLD}%: "
          f"{int(scored_df['meets_mape_threshold'].fillna(0).sum())} / {len(scored_df)}")
    print(f"SKUs that beat the naive baseline (overall MAE): "
          f"{int(scored_df['beats_naive_mae'].fillna(0).sum())} / {len(scored_df)}")
    if len(scored_df):
        print(f"Mean MAE  (30-day aggregate): model {scored_df['mae'].mean():.3f}  "
              f"vs naive {scored_df['naive_mae'].mean():.3f}")
        print(f"Mean MASE                   : model {scored_df['mase'].mean():.3f}  "
              f"vs naive {scored_df['naive_mase'].mean():.3f}")

    print("\n=== Per-SKU metrics (overall scope) ===")
    print(scored_df[["item_name", "tier", "n_obs", "mae", "rmse", "mape", "mase",
                     "naive_mae", "beats_naive_mae", "meets_mape_threshold"]]
          .sort_values(["tier", "mase"]).to_string(index=False))

    print("\n=== Standard-period vs semestral-break (MAE primary for breaks) ===")
    for scope in ("standard_period", "semestral_break"):
        sub = metrics_df[(metrics_df["period_scope"] == scope)
                         & (metrics_df["validation_method"] != "none")]
        if len(sub):
            print(f"\n-- {scope} ({len(sub)} SKUs with folds in this scope) --")
            print(sub[["item_name", "tier", "n_obs", "mae", "rmse", "mape", "mase", "naive_mae"]]
                  .to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
