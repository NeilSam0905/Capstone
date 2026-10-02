"""
Phase 3 of ETL: demand forecasting for Fast SKUs, scored on 30-day aggregates.

Every Fast SKU is forecast with the SAME model, selected by `--model`. The
default is `topdown_tsb`: a 50/50 blend of two forecasts of the item's next 30
days, spread over the days by its category's calendar-aware Prophet pattern.
Whichever model is chosen, it is literally the callable from `forecasting/`
that the benchmarks score under that name, so a finding about it is a finding
about THIS model, not a near relative.

--- The default: 50/50 category share + TSB ---

    top-down   the item's CATEGORY's trailing 6-month average x the item's share
               of the category over the last 30 days. Long window for the smooth
               category, short one for the item's own recent standing.
    TSB        the item's demand probability x demand size, both smoothed
               (alpha = beta = 0.05). The probability is updated EVERY day, zeros
               included, so an item that stops selling decays towards zero.
    forecast   0.5 x top-down + 0.5 x TSB, a flat rate per day.

Chosen by `scripts/test_item_forecast_methods.py` (data/item_forecast_method_test.csv,
data/item_forecast_robustness.csv): 47 methods on the 58 Fast items, identical
walk-forward folds (the harness below), each forecast using only data before its
origin. Shipped Prophet, which this replaces, reproduces its own stored metrics
exactly in that script, so the numbers are comparable to what the screen showed:

    method                              mean MASE   pooled WMAPE   bias
    Prophet (previous default)             2.52        88.4%       +25%
    repeat last 30 days                    1.95        71.6%        -5%
    6-month average                        2.25        78.1%        -7%
    TSB alpha=beta=0.05                    1.79        66.9%        -7%
    top-down alone                         1.75        65.5%        -8%
    50/50 top-down + TSB (default)         1.71        64.1%        -7%

    (57 items with a usable MASE scale; below 1 in 23 items, was 6.)

  - It beats the old Prophet on 46 of 57 items; the paired bootstrap over items
    puts the mean MASE gain at 0.81 (95% CI 0.51 to 1.21).
  - Not luck of one period: the ranking of all 47 methods on the older six folds
    and the newer six correlates at 0.95, and the blend is first in both.
  - 13 of the 58 "Fast" items sold NOTHING in the last 360 days (their last sale
    is between 2024-05 and 2025-05: fsn_class is computed from full history, see
    forecasting/category.py). Prophet keeps forecasting them; a decaying method
    does not. That is a real defect of Prophet here, but it inflates the headline.
    On the 45 items still selling: mean MASE 2.61 -> 2.16, better on 34 of 45,
    CI 0.28 to 0.61. Read that one as the honest size of the improvement.
  - It is still not accurate in absolute terms: MASE is below 1 for 11 of those 45
    items, and the manuscript's <=20% MAPE target stays out of reach.

The top group (top-down, TSB, their blends) is within noise of each other; the
blend beats TSB alone by 0.09 MASE (CI 0.02 to 0.16), and a grid over the top-down
share window and TSB parameters is flat (1.68 to 1.90), so it is not a lucky
setting. Two more reasons to prefer it to plain TSB: the Fast items' forecasts
then follow their category's long-run level, and 47 methods were compared, so the
simpler principle (short memory for the item, long memory for the category) is the
safer bet than the single best number.

What did NOT help, all in data/item_forecast_method_test.csv: the 6-month average
on its own (item series are too sparse for a long window; not reliably better than
Prophet, CI -0.73 to +0.10), 3- and 12-month averages, spike capping, medians and
trimmed means, "same 30 days last year" (under-forecasts by 50%), category momentum
and seasonal indices, Croston and SBA (over-forecast by 39-46%), Prophet with a flat
trend (worse), and blending Prophet in: every pairing with Prophet scored worse than
the same blend without it. Dropping Prophet's yearly term helps it (2.52 -> 2.22 on
all items) but not on the 45 items still selling (2.61 -> 2.60), and it still trails
every method above.

--- The school-calendar adjustment (default) ---

`topdown_tsb+calendar+prophet_shape` is the blend above, multiplied by
forecasting/calendar_adjust.py's factor: when the next 30 days hold more
semester-break / exam days than the window the level was learned over, the
total goes down to match (ratios measured on the item's CATEGORY, since an item
is too sparse). It never goes up; store closures are not used. Measured by
scripts/test_calendar_adjustment.py on the same folds:

    mean MASE 1.71 -> 1.67 | pooled WMAPE 64.1% -> 60.3% | bias -7% -> -15%
    better on 41 of 57 items (bootstrap 95% CI of the gap -0.066 to -0.007),
    in both the older and the newer six folds

The extra under-forecast falls mostly in break months. The "lower only" rule was
chosen after seeing the uncapped version fail after breaks, so treat the gain as
slightly optimistic. `--model topdown_tsb+prophet_shape` gives the unadjusted blend.

--- The day-by-day shape ---

The blend is a flat rate, and a flat line tells you nothing about the days. Each
item's 30-day total is therefore spread over the days by its CATEGORY's Prophet
pattern (weekly seasonality + the Dim_Date flags; forecasting/shape.py, the same
code step4c uses). The total is unchanged - the weights sum to 1 - so the 30-day
accuracy above is unaffected. Scored day by day against a flat line (below 1 is
better; test_item_forecast_methods.py, data/item_forecast_shape_test.csv):

    category's Prophet pattern     0.976   (0.941 in months with 5+ break days)
    category's weekday pattern     0.982
    the item's own Prophet         0.986
    the item's own weekday         0.998

An item's daily series is mostly zeros, so the category, which has enough sales to
show a weekly rhythm, supplies the pattern. The gain is small (2-3% of daily
error) and concentrated in semester-break months; it is a rhythm (quiet Sundays,
closures, breaks), not a forecast of individual spikes or bulk orders.
`--model topdown_tsb+weekday_shape` (no Prophet needed) and `--model topdown_tsb`
(flat) are the alternatives, and Prophet being unavailable falls back to weekday
automatically; `Result_Forecast.model_type` says which was actually used
(e.g. "topdown_tsb+prophet_shape").

--- Prophet, and why it is no longer the default ---

Prophet was the default "by request": the forecast screen was to be served by
Prophet. It is still selectable (`--model prophet`), and it still supplies the
day-by-day pattern above, but as the source of the 30-day total it lost. ~50% of
days are zeros and the span is 23 months, so its trend and yearly terms are fitted
on almost no evidence and then extrapolated; it over-forecasts by 25% and keeps
forecasting items that stopped selling. docs/FAST_MOVING_BENCHMARK.md recorded the
same ordering earlier (37 methods, nothing beat a trailing average, Prophet beat
the trailing mean on 12 of 58 SKUs). `--model prophet` now looks its calendar flags
up for the forecast window too; before, they were zero-filled there, so the
validated model and the produced forecast were not the same thing.

Other selectable models, all flat rates:

    rolling_mean_30  mean of the trailing 30 observations, clipped at 0
    tsb              TSB with alpha = beta = 0.1
    ewma_a0.1        exponentially weighted average, alpha = 0.1

--- What this file used to be ---

It fit Prophet per SKU (logistic growth, five Dim_Date regressors, 25
changepoints for a "standard" tier, a fixed linear trend for a "simplified"
tier, MCMC(1000) production fits) and took 1-2 hours behind a cmdstan build. Then
it briefly used a full-history (expanding) mean, then the benchmark's Prophet
through forecasting/prophet_model.py, and now the blend above.

--- The rows ---

    yhat       = the item's 30-day total x that day's weight (an even split when
                 no shape is applied)
    yhat_lower = max(yhat - SD, 0)
    yhat_upper = yhat + SD

SD is the sample standard deviation (ddof=1) of the item's FULL daily series, not
of a 30-day window: a SKU whose last 30 days happen to be flat would otherwise get
a zero-width interval, which claims a certainty the model does not have. The band
is display-only - it is not read by step5 or by anything else.

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

--- 2023 synthetic history (training only) ---

When scripts/load_synthetic_2023.py has filled Fact_Sales_Synthetic, every
series is extended back to 2023-02-01 with it (forecasting/synthetic_history.py;
--no-synthetic turns it off). The daily split is modelled from real 2023 batch
totals, so it is TRAINING data only: folds are laid out from the end, so every
scored window is still real sales, the forecast dates are unchanged, and the
interval band (SD) is taken over real days. 2023-09..2024-04 has no data at all;
it is zero-filled for the array models and passed as missing to the Prophet day
shape. MASE's scale is taken over all training blocks, so it moves with the
extra history; compare WMAPE / bias instead (scripts/validate_protocols.py).
Result_Forecast.model_type is unchanged (scripts/verify_rebuild_state.py checks
it against DEFAULT_MODEL); the run prints which history it used.

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

is_sem_break assigns those scopes. The Dim_Date flags also reach the forecast
through the day-by-day shape (and through Prophet, if selected); they do not move
the 30-day total of the default model, which is what these metrics score.

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
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.shape import day_shape, load_calendar as load_shape_calendar
from forecasting.topdown import topdown_tsb_fit_predict
from forecasting import synthetic_history as sh
from order_types import add_known_orders, known_orders, walk_in_only  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ustore.db")

# Harness settings, identical to model_benchmark.py so the two are comparable.
HORIZON = 30
MIN_FOLDS = 3
MAX_FOLDS = 12
MIN_TRAIN = 60

WINDOW = 30                    # the rolling mean's trailing window
TSB_ALPHA = 0.1                # size smoothing      - benchmark's values
TSB_BETA = 0.1                 # probability smoothing

RESIDUE = "Uncategorised"      # same bucket step1b_categorize_products.py uses

# Every entry is the SAME callable the benchmarks score under that name.
# Keyed by the string written to Result_Forecast.model_type, so the label and
# the model can never drift apart - which is why a model that spreads its total
# over the days with a shape is its OWN entry ("topdown_tsb+prophet_shape"), not
# a flag: the stored label says exactly what produced the rows.
#
# Each factory takes a `ctx` dict so a model that needs context can have it:
# Prophet is bound to the shared daily index and to Dim_Date's regressor columns,
# and the category-share model to the item's category series
# (ctx["category_values"], rebuilt per item because it differs per item). Every
# entry takes the same argument rather than special-casing one.
_TOPDOWN_DESC = ("50/50 blend: category 6-month average x item's 30-day share + "
                 "TSB alpha=beta=0.05")


def _topdown(ctx):
    return topdown_tsb_fit_predict(ctx["category_values"])


def _topdown_calendar(ctx):
    """The blend, lowered when the school calendar shows quieter days ahead
    (forecasting/calendar_adjust.py; ratios from the item's CATEGORY)."""
    return calendar_capped_fit_predict(_topdown(ctx), ctx["day_types"],
                                       cat_values=ctx["category_values"])


MODELS = {
    "topdown_tsb+calendar+prophet_shape": (
        _topdown_calendar, f"{_TOPDOWN_DESC}, lowered when the school calendar shows quieter "
                           f"days ahead; the {HORIZON}-day total is spread over the days by the "
                           f"item's category's Prophet pattern"),
    "topdown_tsb+prophet_shape": (
        _topdown, f"{_TOPDOWN_DESC}; the {HORIZON}-day total is spread over the days by "
                  f"the item's category's Prophet pattern"),
    "topdown_tsb+weekday_shape": (
        _topdown, f"{_TOPDOWN_DESC}; the {HORIZON}-day total is spread over the days by "
                  f"the item's category's weekday pattern"),
    "topdown_tsb": (_topdown, f"{_TOPDOWN_DESC}, flat rate over {HORIZON} days"),
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
# The default, and why: see the docstring. Measured on the 58 Fast SKUs over
# identical folds by scripts/test_item_forecast_methods.py: mean MASE 1.71 against
# 2.52 for the Prophet it replaces (2.16 against 2.61 on the 45 items still
# selling). `prophet` stays in MODELS and is still selectable with --model prophet:
# it is the comparison the manuscript's sections 2.1.4 and 3.3.2 need, and a
# measured negative result is worth more than an untested claim.
# The calendar adjustment on top (scripts/test_calendar_adjustment.py): mean MASE
# 1.71 -> 1.67, pooled WMAPE 64.1% -> 60.3%, better on 41 of 57 items.
DEFAULT_MODEL = "topdown_tsb+calendar+prophet_shape"

# Models whose factory needs the item's category series (see MODELS).
NEEDS_CATEGORY = {"topdown_tsb", "topdown_tsb+prophet_shape", "topdown_tsb+weekday_shape",
                  "topdown_tsb+calendar+prophet_shape"}

# A level model that gets its category's day-by-day shape on top (forecasting/shape.py).
# The 30-day total is the level model's, unchanged: the shape only redistributes it.
SHAPE_KIND = {"topdown_tsb+prophet_shape": "prophet", "topdown_tsb+weekday_shape": "weekday",
              "topdown_tsb+calendar+prophet_shape": "prophet"}

# Models whose STORED forecast varies by day: Prophet draws its own curve, the
# shaped ones borrow their category's. scripts/verify_rebuild_state.py reads this
# to decide whether a flat forecast per SKU would be a fault.
CURVE_MODELS = {"prophet"} | set(SHAPE_KIND)


def _make_prophet(ctx):
    """Prophet bound to the benchmark's shared daily index and calendar.

    Returns the standard `fit_predict(train, horizon)` callable, so it
    drops into the same harness as the trailing averages and is scored by
    `walk_forward_evaluate` on identical folds.

    The calendar columns come from Dim_Date, which `populate_dim_date.py` fills
    from `calendar_ranges.csv`; they are the six in
    `forecasting.prophet_model.CALENDAR_REGRESSORS`, semester_week included.

    The index and calendar are extended HORIZON days past the last observation.
    Without that, the flags for the production forecast window fall off the end
    of the index and are zero-filled, so the validated model (folds see the real
    flags) and the produced forecast were not the same model.
    """
    from forecasting.prophet_model import load_calendar, prophet_fit_predict
    if ctx.get("con") is None:
        return prophet_fit_predict(ctx["index"], None, "prophet")
    idx = ctx["index"]
    ext = idx.append(pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=HORIZON, freq="D"))
    return prophet_fit_predict(ext, load_calendar(ctx["con"], ext), "prophet")


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
    cols = {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    cat_col = "forecast_category" if "forecast_category" in cols else "NULL AS forecast_category"
    products = pd.read_sql(
        f"SELECT product_id, item_name, fsn_class, is_hvl, {cat_col} FROM Dim_Product", con
    )
    # sales only: damaged / promo / transfer removals are not demand. Walk-in
    # sales only: a recorded bulk or pre-order is not everyday demand
    # (scripts/order_types.py).
    fact = pd.read_sql(
        f"""SELECT f.product_id, d.calendar_date, f.quantity_sold
           FROM Fact_Sales f JOIN Dim_Date d ON f.date_id = d.date_id
           WHERE LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'
             AND {walk_in_only(con)}""",
        con,
    )
    fact["calendar_date"] = pd.to_datetime(fact["calendar_date"])
    dim_date = pd.read_sql(f"SELECT calendar_date, {SCOPE_COLUMN} FROM Dim_Date", con)
    dim_date["calendar_date"] = pd.to_datetime(dim_date["calendar_date"])
    return products, fact, dim_date


def build_calendar(fact, dim_date):
    """The full daily index every SKU's series is reindexed onto, plus the
    is_sem_break flag aligned to it.

    The index ends at the last date ANY SKU sold, not at the last row of
    Fact_Sales. The panel is zero-filled out to the end of the calendar range
    (2026-07-31) while the tallies stop at 2026-07-08, so its last 23 days are
    padding. Left in, a 30-day trailing mean averaged 7 real days with 23
    padded zeros: measured on the Fast SKUs, the 30-day forecasts summed to
    647 units against 3,291 actually sold in the last 30 real days (0.20x),
    and the last validation folds scored fake zero "actuals". Forecast dates
    start the day after this end, which is also what
    step4c_category_forecast.py does, so a category and its items describe
    the same 30 days. (step5_prescriptive.py::load_series still spans the
    whole panel; its 365-day window makes that a ~6% effect, not 5x.)"""
    last_sale = fact.loc[fact["quantity_sold"] > 0, "calendar_date"].max()
    if pd.isna(last_sale):
        last_sale = fact["calendar_date"].max()
    idx = pd.date_range(fact["calendar_date"].min(), last_sale, freq="D")
    breaks = (dim_date.set_index("calendar_date")[SCOPE_COLUMN]
              .reindex(idx).fillna(0).to_numpy(dtype=float))
    return idx, breaks


def build_series(fact_one, idx):
    """One SKU's dense daily series over the full calendar, zero-filled."""
    return (fact_one.groupby("calendar_date")["quantity_sold"].sum()
            .reindex(idx, fill_value=0.0).astype(float).to_numpy())


def build_category_series(fact, products, idx):
    """{forecast_category: dense daily array over idx}: each category's combined
    sales, every item in it (Fast or not), zero-filled on the same index as the
    item series so position i is the same date for both. Same construction and
    residue bucket as step4c_category_forecast.py."""
    cat = products.set_index("product_id")["forecast_category"].fillna(RESIDUE)
    f = fact.assign(category=fact["product_id"].map(cat).fillna(RESIDUE))
    wide = (f.groupby(["category", "calendar_date"])["quantity_sold"].sum()
            .unstack(0).reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    return {c: wide[c].to_numpy() for c in wide.columns}


def extend_with_synthetic(con, idx, dim_date, cat_series, use=True):
    """Prepend the 2023 synthetic TRAINING history (Fact_Sales_Synthetic, see
    forecasting/synthetic_history.py) to the shared index and the category series.

    Returns (ext_idx, n_pre, gap, breaks, cat_series, item_daily): the index
    extended back to the first synthetic day, the number of days prepended, the
    mask of prepended days with no data at all (2023-09..2024-04), is_sem_break
    on ext_idx, the category series on ext_idx, and {product_id: daily synthetic
    Series} for items to prepend with forecasting.synthetic_history.prepend.
    With use=False or no synthetic table, everything comes back on `idx`."""
    syn = sh.load(con) if use else None
    ext, n_pre, gap = sh.extend_index(idx, syn)
    breaks = (dim_date.set_index("calendar_date")[SCOPE_COLUMN]
              .reindex(ext).fillna(0).to_numpy(dtype=float))
    by_cat = sh.by_category(syn)
    cats = {c: sh.prepend(v, by_cat.get(c), ext, n_pre) for c, v in cat_series.items()}
    return ext, n_pre, gap, breaks, cats, sh.by_product(syn)


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
                         "Use rolling_mean_30 to reproduce the published "
                         "benchmark numbers.")
    ap.add_argument("--no-synthetic", action="store_true",
                    help="train on real sales only (default: also the 2023 synthetic "
                         "history in Fact_Sales_Synthetic, when loaded)")
    args = ap.parse_args()
    model_type = args.model
    make_model, model_desc = MODELS[model_type]

    con = sqlite3.connect(DB_PATH)
    create_result_tables(con)

    products, fact, dim_date = load_common(con)
    real_idx, _ = build_calendar(fact, dim_date)

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

    # A shaped model gets its CATEGORY's day-by-day pattern on top of the level
    # model's total (which it leaves unchanged); prophet draws its own curve.
    shape_kind = SHAPE_KIND.get(model_type)
    shaped = shape_kind is not None
    needs_category = model_type in NEEDS_CATEGORY
    cat_series = build_category_series(fact, products, real_idx) if (needs_category or shaped) else {}
    # 2023 synthetic history: training only. Every scored window and the
    # forecast dates stay on real history (they are laid out from the end).
    idx, n_pre, gap, breaks, cat_series, syn_items = extend_with_synthetic(
        con, real_idx, dim_date, cat_series, use=not args.no_synthetic)
    cat_of = products.set_index("product_id")["forecast_category"].fillna(RESIDUE)
    if needs_category and cat_of.eq(RESIDUE).all():
        raise SystemExit("Dim_Product.forecast_category is empty - run "
                         "scripts/step1b_categorize_products.py first, or use --model tsb")
    forecast_dates = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    shape_cal = load_shape_calendar(con) if shaped else None
    shape_cache = {}                    # category -> (weights, shape actually used)

    base_ctx = {"index": idx, "con": con,
                "day_types": load_day_types(con, idx, HORIZON)}
    model = None if needs_category else make_model(base_ctx)
    naive = naive_fit_predict()
    # The last date of real history (see build_calendar), not the panel's last row.
    snapshot_date = idx[-1].strftime("%Y-%m-%d")
    last_date = idx[-1]

    forecast_rows, metric_rows, unscored = [], [], []
    model_totals = {}               # product_id -> the model's own 30-day total
    by_product = dict(list(fact.groupby("product_id")))

    print(f"Model: {model_type} ({model_desc})")
    print("Day-by-day shape: " + (
        f"{shape_kind}, from each item's category (the 30-day total is unchanged; see the docstring)"
        if shaped else ("the model's own curve" if model_type in CURVE_MODELS else "flat")))
    print(f"Harness: {VALIDATION_METHOD} | horizon {HORIZON} | folds {MIN_FOLDS}-{MAX_FOLDS} "
          f"| min_train {MIN_TRAIN}  (identical to model_benchmark.py)")
    print("Training history: " + (
        f"real {real_idx[0].date()} .. {real_idx[-1].date()} + 2023 synthetic from {idx[0].date()} "
        f"({n_pre} days prepended, {int(gap.sum())} of them with no data; "
        f"{sum(1 for p in syn_items if p in set(fast['product_id']))} Fast items have synthetic sales)\n"
        if n_pre else f"real only, {real_idx[0].date()} .. {real_idx[-1].date()}\n"))

    for _, row in fast.iterrows():
        pid, name, tier = int(row["product_id"]), row["item_name"], row["tier"]
        g = by_product.get(pid)
        values = sh.prepend(build_series(g, real_idx) if g is not None else np.zeros(len(real_idx)),
                            syn_items.get(pid), idx, n_pre)

        # ONE fold layout per SKU, handed to both methods, so neither can be
        # advantaged by a different split.
        folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        cat_name = cat_of.get(pid, RESIDUE)
        cat_vals = cat_series.get(cat_name, np.zeros(len(idx)))
        # Bound per item: the category-share model reads this item's category.
        fit = make_model({**base_ctx, "category_values": cat_vals}) if needs_category else model
        ev_m = walk_forward_evaluate(pid, values, fit, model_type, folds=folds)
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
        out = np.asarray(fit(values, HORIZON), dtype=float).ravel()
        # A flat model is stored as its single level, or as its 30-day total
        # spread by the category's shape; a curve model keeps all 30 values.
        # `reported` is only what this line prints.
        row_model, level, used = model_type, out, "flat"
        if model_type != "prophet":
            level = float(out[0])
            if shaped:
                if cat_name not in shape_cache:
                    # The no-data gap goes in as NaN: Prophet skips missing days.
                    shape_cache[cat_name] = day_shape(shape_kind, np.where(gap, np.nan, cat_vals),
                                                      idx, shape_cal, forecast_dates)
                weights, used = shape_cache[cat_name]
                # day_shape says which shape it ACTUALLY used (prophet -> weekday
                # -> flat on failure), and the row is labelled with that, so a
                # fallback is visible in Result_Forecast.model_type rather than
                # hidden behind the configured name.
                base = model_type.rsplit("+", 1)[0]
                row_model = base if used == "flat" else f"{base}+{used}_shape"
                if used != "flat":
                    level = float(out.sum()) * weights
        reported = float(np.mean(out))
        real = values[n_pre:]          # the band describes real sales only
        spread = float(np.std(real, ddof=1)) if real.size > 1 else 0.0
        append_forecast(pid, level, spread, 0 if scored else 1,
                        last_date, snapshot_date, row_model, forecast_rows)
        model_totals[pid] = float(out.sum())

        shape = "mean" if model_type == "prophet" else ("shaped" if used != "flat" else "flat")
        print(f"[{tier:10}] product_id={pid:4} sale_days={int(row['n_obs']):4} "
              f"folds={ev_m.n_folds:3} yhat({shape})={reported:8.3f}  {name}")

    forecast_df = pd.DataFrame(forecast_rows)
    metrics_df = pd.DataFrame(metric_rows)

    # A shape only redistributes: every item's 30 days must still add up to its
    # model's total (which is what was validated). Checked BEFORE anything is written.
    written = forecast_df.groupby("product_id")["yhat"].sum()
    drift = max(abs(written[pid] - t) for pid, t in model_totals.items())
    assert drift < 1e-3, f"the day-by-day shape changed an item's 30-day total (max drift {drift:.6f})"
    assert (forecast_df["yhat"] >= 0).all(), "negative forecast written"

    # Orders the store already knows are coming (Upcoming_Order) go on top of
    # the everyday forecast on their date - after the check above, which is
    # about the model's own total (scripts/order_types.py).
    forecast_df, known_units = add_known_orders(forecast_df, known_orders(con), "product_id")
    if known_units:
        print(f"Known upcoming orders added to the item forecasts: {known_units:.0f} units")

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
