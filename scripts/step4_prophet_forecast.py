"""
Phase 3 of ETL: Prophet forecasting (Option A scope).

Tiers are keyed on SALE-DAYS - distinct calendar dates with
quantity_sold > 0 - not on raw Fact_Sales row count. See "Fix 4" below
for why that distinction matters.
  - Fast SKUs, 60+ sale-days  -> standard Prophet, 80/20 chronological
    train/test split, default changepoints, MCMC(1000) for the final
    production fit.
  - Fast SKUs, 30-59 sale-days -> simplified Prophet (n_changepoints=0
    => a single fixed linear trend, no piecewise changepoints), validated
    with walk-forward one-step-ahead CV over a PROPORTIONAL tail window
    (20% of that SKU's observations, minimum 5 - see "Fix 3" below),
    MCMC(1000) for the final production fit.
  - Fast+HVL SKUs, <30 sale-days (plus any simplified-tier SKU that can't
    support a proper holdout even proportionally - see "Fix 3") -> NOT
    modelled. A flat 30-day rolling average of all available history,
    flagged as a heuristic ("Insufficient Data for Forecasting"), no
    MAPE/MAE validation (there is nothing to fit).
  - Everything else (Slow, Non-moving) is out of scope for this phase.
  - The model itself still fits on the FULL dense series (real
    quantity_sold, including the zero-fill days) regardless of tier -
    only the tier ASSIGNMENT is sale-days based.

Regressors (from Dim_Date): is_enrollment_period, is_exam_week,
is_event_day, is_sem_break, semester_week. is_event_day is kept as its
own regressor on purpose (opposite demand signal from a break/closure,
must not be merged with the others).

--- Three fixes applied after reviewing the first run ---

Fix 1 - dropped is_store_closed as a regressor. It was constant zero for
31 of 34 modeled SKUs (a closed day essentially never gets a sales tally
to begin with), so it had no variance to learn from and nothing to
predict over the forecast horizon - pure noise in the design matrix.

Fix 2 - growth='logistic' instead of 'linear', with floor=0 and a
per-SKU cap = 1.5 x that SKU's historical max daily quantity (computed
from whatever data is available at fit time - the training subset for
validation fits, the full series for the final production fit, so no
future information leaks into the cap). Unit sales can't be negative,
and unbounded linear trend was a likely contributor to the large misses
in the first run; logistic growth also removes negative yhat_lower
values by construction.

Fix 3 - simplified-tier validation no longer uses a fixed 20-row tail
on series as short as ~35-58 rows (a ~50/50 split is not a valid
holdout). Two changes: (a) the tail window is now proportional -
max(round(0.2 * n_obs), 5) - and (b) each held-out point is now
predicted using ONLY strictly-prior data (walk-forward: train on
series.iloc[:i]), not series.drop(index=i) as before, which had let
FUTURE rows leak into predicting an earlier held-out point - a separate,
more serious problem than window size alone.

Fix 4 - tier assignment now counts SALE-DAYS (distinct dates with
quantity_sold > 0), not raw Fact_Sales row count. After the zero-fill
rebuild grew Fact_Sales to include real zero-quantity rows for every
calendar day in a dense-tallied month, counting rows meant almost every
SKU cleared 60 "observations" regardless of how many days it actually
sold anything - the zero-padding was doing the counting, not real sales
history. This silently pushed thin SKUs into the standard tier with a
full Prophet fit instead of the rolling-average fallback they actually
warranted. The demand series fed to Prophet is unchanged either way -
it's still the full dense series including the zero days, which is
correct - only which BUCKET a SKU is assigned to changed.

Fitting cost note: MCMC(1000) is used only for each SKU's single final
production fit (used for the 30-day forecast + uncertainty interval).
Validation refits (the 80/20 holdout fit, and the walk-forward folds per
simplified-tier SKU) use Prophet's default MAP optimisation instead -
running MCMC on every validation fold would take hours and buys no
extra validation accuracy, since MAP point estimates are what the error
metrics need.

Validation metrics:
  - MAE / RMSE / MAPE on held-out points, split into 'standard_period'
    (is_sem_break=0) and 'semestral_break' (is_sem_break=1) subsets, plus
    an 'overall' pool. MAPE is undefined/unstable when actual sales are
    at or near zero, which happens disproportionately in break periods,
    so MAE is the metric used for break-period comparisons and for the
    naive-vs-Prophet "did it help" comparison; MAPE<=20% is only assessed
    on the 'overall' and 'standard_period' scopes.
  - Naive baseline = previous observed period's actual value (persistence
    model), computed on the same held-out points.

Writes to (does not touch Fact_Sales):
  - Result_Forecast          (per SKU, per forecast date, 30-day horizon)
  - Result_Forecast_Metrics  (per SKU, per validation-period scope)

Safe to re-run: both result tables are cleared before inserting.
"""
import sqlite3
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ustore.db")
HORIZON_DAYS = 30
MCMC_SAMPLES = 1000
MAPE_PASS_THRESHOLD = 20.0
SIMPLIFIED_TEST_FRACTION = 0.2
SIMPLIFIED_TEST_MIN = 5
MIN_SIMPLIFIED_TRAIN = 20  # below this, move the SKU to rolling_average instead
CAP_MULTIPLIER = 1.5

REGRESSORS = [
    "is_enrollment_period",
    "is_exam_week",
    "is_event_day",
    "is_sem_break",
    "semester_week",
]


def simplified_test_size(n):
    return max(round(n * SIMPLIFIED_TEST_FRACTION), SIMPLIFIED_TEST_MIN)


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


def load_common(con):
    products = pd.read_sql(
        "SELECT product_id, item_name, fsn_class, is_hvl FROM Dim_Product", con
    )
    fact = pd.read_sql(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold, f.imputation_flag
           FROM Fact_Sales f JOIN Dim_Date d ON f.date_id = d.date_id""",
        con,
    )
    dim_date = pd.read_sql(
        f"SELECT calendar_date, {', '.join(REGRESSORS)} FROM Dim_Date", con
    )
    dim_date["semester_week"] = dim_date["semester_week"].fillna(0)
    dim_date["calendar_date"] = pd.to_datetime(dim_date["calendar_date"])
    return products, fact, dim_date


def build_series(fact, dim_date, product_id):
    s = fact[fact["product_id"] == product_id][["calendar_date", "quantity_sold"]].copy()
    s = s.groupby("calendar_date", as_index=False)["quantity_sold"].sum()
    s["calendar_date"] = pd.to_datetime(s["calendar_date"])
    s = s.merge(dim_date, on="calendar_date", how="left")
    s[REGRESSORS] = s[REGRESSORS].fillna(0)
    s = s.rename(columns={"calendar_date": "ds", "quantity_sold": "y"})
    return s.sort_values("ds").reset_index(drop=True)


def naive_predict(series, idx):
    """Persistence baseline: predicted[i] = actual value of the previous row
    in the full chronological series (works across train/test boundaries)."""
    preds = []
    for i in idx:
        preds.append(series["y"].iloc[i - 1] if i > 0 else series["y"].iloc[0])
    return np.array(preds)


def compute_metrics(actual, pred):
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mae = float(np.mean(np.abs(actual - pred)))
    rmse = float(np.sqrt(np.mean((actual - pred) ** 2)))
    mask = actual != 0
    mape = float(np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100) if mask.sum() else None
    return mae, rmse, mape


def cap_for(y_values):
    """cap = 1.5 x historical max daily quantity seen so far (whatever data
    the caller passes in - train-only for a validation fit, full series for
    the production fit - so the cap never leaks future information)."""
    hist_max = float(np.asarray(y_values).max()) if len(y_values) else 1.0
    return max(hist_max * CAP_MULTIPLIER, 1.0)


def with_cap(df, cap):
    d = df.copy()
    d["cap"] = cap
    d["floor"] = 0.0
    return d


def fit_prophet(train_df, n_changepoints, cap, mcmc_samples=0):
    from prophet import Prophet
    # changepoint_range=0.95 (Prophet default: 0.8): these are short (60-350
    # point), sparse, episodic tally series with real recent demand shifts.
    # The 0.8 default confines trend flexibility to the first 80% of
    # training history, so it systematically misses a shift happening near
    # the end of training and over-predicts the holdout. 0.95 lets the
    # trend bend closer to the edge of the data. Applied uniformly to every
    # SKU (not tuned per-SKU), and irrelevant when n_changepoints=0
    # (simplified tier's fixed linear trend has no changepoints to place).
    #
    # growth='logistic' with floor=0: unit sales can't be negative, and a
    # bounded S-curve is a better fit for these thin series than an
    # unbounded line (see module docstring, Fix 2).
    m = Prophet(
        growth="logistic",
        n_changepoints=n_changepoints,
        changepoint_range=0.95,
        mcmc_samples=mcmc_samples,
        interval_width=0.80,
    )
    for r in REGRESSORS:
        m.add_regressor(r)
    fit_df = with_cap(train_df[["ds", "y"] + REGRESSORS], cap)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(fit_df)
    return m


def future_regressor_frame(last_date, dim_date, horizon_days, cap):
    future_dates = pd.date_range(last_date + timedelta(days=1), periods=horizon_days, freq="D")
    fut = pd.DataFrame({"ds": future_dates}).merge(
        dim_date.rename(columns={"calendar_date": "ds"}), on="ds", how="left"
    )
    fut[REGRESSORS] = fut[REGRESSORS].fillna(0)
    return with_cap(fut, cap)


def metrics_by_scope(series, test_idx, model_preds, naive_preds):
    """Build overall / standard_period / semestral_break metric rows."""
    test = series.iloc[test_idx].reset_index(drop=True)
    rows = []
    scopes = {
        "overall": np.ones(len(test), dtype=bool),
        "standard_period": (test["is_sem_break"] == 0).to_numpy(),
        "semestral_break": (test["is_sem_break"] == 1).to_numpy(),
    }
    for scope, mask in scopes.items():
        n = int(mask.sum())
        if n == 0:
            continue
        mae, rmse, mape = compute_metrics(test["y"][mask], model_preds[mask])
        n_mae, n_rmse, n_mape = compute_metrics(test["y"][mask], naive_preds[mask])
        rows.append(
            dict(
                period_scope=scope,
                n_obs=n,
                mae=mae, rmse=rmse, mape=mape,
                naive_mae=n_mae, naive_rmse=n_rmse, naive_mape=n_mape,
                beats_naive_mae=int(mae < n_mae),
                meets_mape_threshold=(int(mape <= MAPE_PASS_THRESHOLD) if mape is not None else None)
                if scope != "semestral_break" else None,
            )
        )
    return rows


def run_standard_tier(product_id, item_name, series, dim_date, snapshot_date, forecast_rows, metric_rows):
    n = len(series)
    split = int(n * 0.8)
    train, test_idx = series.iloc[:split], list(range(split, n))

    train_cap = cap_for(train["y"])
    model = fit_prophet(train, n_changepoints=25, cap=train_cap, mcmc_samples=0)
    test_future = with_cap(series.iloc[test_idx][["ds"] + REGRESSORS].reset_index(drop=True), train_cap)
    fc = model.predict(test_future)
    model_preds = np.maximum(fc["yhat"].to_numpy(), 0.0)  # unit sales can't be negative
    naive_preds = naive_predict(series, test_idx)

    for row in metrics_by_scope(series, test_idx, model_preds, naive_preds):
        metric_rows.append(dict(
            product_id=product_id, item_name=item_name, tier="standard",
            validation_method="80_20_holdout", snapshot_date=snapshot_date, **row,
        ))

    # production fit on full history, MCMC for uncertainty intervals
    full_cap = cap_for(series["y"])
    prod_model = fit_prophet(series, n_changepoints=25, cap=full_cap, mcmc_samples=MCMC_SAMPLES)
    future = future_regressor_frame(series["ds"].max(), dim_date, HORIZON_DAYS, full_cap)
    _append_forecast(product_id, prod_model, future, "prophet_standard", 0, snapshot_date, forecast_rows)


def run_simplified_tier(product_id, item_name, series, dim_date, snapshot_date, forecast_rows, metric_rows):
    n = len(series)
    test_size = simplified_test_size(n)
    tail_start = n - test_size
    tail_idx = list(range(tail_start, n))

    # walk-forward: predict each held-out point using ONLY strictly-prior
    # rows (series.iloc[:i]), never rows that come after it in time.
    model_preds = np.empty(len(tail_idx))
    for pos, i in enumerate(tail_idx):
        train = series.iloc[:i].reset_index(drop=True)
        fold_cap = cap_for(train["y"])
        m = fit_prophet(train, n_changepoints=0, cap=fold_cap, mcmc_samples=0)
        pred_row = with_cap(series.iloc[[i]][["ds"] + REGRESSORS], fold_cap)
        model_preds[pos] = max(m.predict(pred_row)["yhat"].iloc[0], 0.0)  # unit sales can't be negative

    naive_preds = naive_predict(series, tail_idx)
    for row in metrics_by_scope(series, tail_idx, model_preds, naive_preds):
        metric_rows.append(dict(
            product_id=product_id, item_name=item_name, tier="simplified",
            validation_method="walk_forward_proportional_tail", snapshot_date=snapshot_date, **row,
        ))

    full_cap = cap_for(series["y"])
    prod_model = fit_prophet(series, n_changepoints=0, cap=full_cap, mcmc_samples=MCMC_SAMPLES)
    future = future_regressor_frame(series["ds"].max(), dim_date, HORIZON_DAYS, full_cap)
    _append_forecast(product_id, prod_model, future, "prophet_simplified", 0, snapshot_date, forecast_rows)


def run_rolling_average(product_id, item_name, series, snapshot_date, forecast_rows, metric_rows):
    avg = series["y"].mean() if len(series) else 0.0
    std = series["y"].std() if len(series) > 1 else 0.0
    last_date = series["ds"].max() if len(series) else pd.Timestamp.today().normalize()
    future_dates = pd.date_range(last_date + timedelta(days=1), periods=HORIZON_DAYS, freq="D")
    for d in future_dates:
        forecast_rows.append(dict(
            product_id=product_id, forecast_date=d.strftime("%Y-%m-%d"),
            yhat=round(avg, 3), yhat_lower=round(max(avg - std, 0), 3), yhat_upper=round(avg + std, 3),
            model_type="rolling_average", is_heuristic=1, snapshot_date=snapshot_date,
        ))
    metric_rows.append(dict(
        product_id=product_id, item_name=item_name, tier="rolling_average",
        validation_method="none", period_scope="overall", n_obs=len(series),
        mae=None, rmse=None, mape=None, naive_mae=None, naive_rmse=None, naive_mape=None,
        beats_naive_mae=None, meets_mape_threshold=None, snapshot_date=snapshot_date,
    ))


def _append_forecast(product_id, model, future, model_type, is_heuristic, snapshot_date, forecast_rows):
    fc = model.predict(future)
    for _, r in fc.iterrows():
        forecast_rows.append(dict(
            product_id=product_id, forecast_date=r["ds"].strftime("%Y-%m-%d"),
            # Logistic growth bounds the trend curve but NOT the final yhat,
            # since additive seasonality/regressor effects sit on top of it
            # and can still push it negative - clip at 0 explicitly, since
            # unit sales can never be negative.
            yhat=round(max(float(r["yhat"]), 0.0), 3),
            yhat_lower=round(max(float(r["yhat_lower"]), 0.0), 3),
            yhat_upper=round(max(float(r["yhat_upper"]), 0.0), 3),
            model_type=model_type, is_heuristic=is_heuristic, snapshot_date=snapshot_date,
        ))


def main():
    con = sqlite3.connect(DB_PATH)
    create_result_tables(con)
    con.execute("DELETE FROM Result_Forecast")
    con.execute("DELETE FROM Result_Forecast_Metrics")
    con.commit()

    products, fact, dim_date = load_common(con)
    # Tier sufficiency = distinct SALE-DAYS (quantity_sold > 0), not raw row
    # count - see "Fix 4" in the module docstring. Fact_Sales now carries a
    # real zero-quantity row for every calendar day in a densely-tallied
    # month, so counting all rows would count zero-padding as observations.
    obs_counts = (
        fact[fact["quantity_sold"] > 0]
        .groupby("product_id")["calendar_date"]
        .nunique()
        .rename("n_obs")
    )
    products = products.merge(obs_counts, on="product_id", how="left").fillna({"n_obs": 0})
    products["n_obs"] = products["n_obs"].astype(int)

    fast = products[products["fsn_class"] == "F"].copy()
    fast["tier"] = np.select(
        [fast["n_obs"] >= 60, fast["n_obs"] >= 30], ["standard", "simplified"], default="rolling_average"
    )

    # Fix 3, part b: a simplified-tier SKU only keeps its tier if a
    # proportional holdout still leaves enough training rows; otherwise it
    # drops to the rolling-average heuristic instead of being force-fit.
    is_simplified = fast["tier"] == "simplified"
    test_sizes = fast.loc[is_simplified, "n_obs"].apply(simplified_test_size)
    train_sizes = fast.loc[is_simplified, "n_obs"] - test_sizes
    demoted = fast.loc[is_simplified].loc[train_sizes < MIN_SIMPLIFIED_TRAIN]
    if len(demoted):
        print(f"Demoting {len(demoted)} simplified-tier SKU(s) to rolling_average "
              f"(proportional holdout would leave < {MIN_SIMPLIFIED_TRAIN} sale-days):")
        print(demoted[["item_name", "n_obs"]].to_string(index=False))
        fast.loc[demoted.index, "tier"] = "rolling_average"
    else:
        print(f"All {is_simplified.sum()} simplified-tier SKUs keep >= {MIN_SIMPLIFIED_TRAIN} "
              f"sale-days under the proportional holdout - none demoted.")

    snapshot_date = fact["calendar_date"].max()

    forecast_rows, metric_rows = [], []

    for _, row in fast.iterrows():
        pid, name, tier, sale_days = int(row["product_id"]), row["item_name"], row["tier"], int(row["n_obs"])
        series = build_series(fact, dim_date, pid)
        print(f"[{tier:12}] product_id={pid:4} sale_days={sale_days:4} series_len={len(series):4}  {name}")
        if tier == "standard":
            run_standard_tier(pid, name, series, dim_date, snapshot_date, forecast_rows, metric_rows)
        elif tier == "simplified":
            run_simplified_tier(pid, name, series, dim_date, snapshot_date, forecast_rows, metric_rows)
        else:
            run_rolling_average(pid, name, series, snapshot_date, forecast_rows, metric_rows)

    forecast_df = pd.DataFrame(forecast_rows)
    metrics_df = pd.DataFrame(metric_rows)

    con.executemany(
        """INSERT INTO Result_Forecast
           (product_id, forecast_date, yhat, yhat_lower, yhat_upper, model_type, is_heuristic, snapshot_date)
           VALUES (:product_id, :forecast_date, :yhat, :yhat_lower, :yhat_upper, :model_type, :is_heuristic, :snapshot_date)""",
        forecast_df.to_dict("records"),
    )
    con.executemany(
        """INSERT INTO Result_Forecast_Metrics
           (product_id, item_name, tier, validation_method, period_scope, n_obs, mae, rmse, mape,
            naive_mae, naive_rmse, naive_mape, beats_naive_mae, meets_mape_threshold, snapshot_date)
           VALUES (:product_id, :item_name, :tier, :validation_method, :period_scope, :n_obs, :mae, :rmse, :mape,
                   :naive_mae, :naive_rmse, :naive_mape, :beats_naive_mae, :meets_mape_threshold, :snapshot_date)""",
        metrics_df.to_dict("records"),
    )
    con.commit()

    # ================= REPORT =================
    print("\n=== TIER BREAKDOWN ===")
    print(fast["tier"].value_counts().to_string())

    overall = metrics_df[metrics_df["period_scope"] == "overall"]
    modelled = overall[overall["tier"] != "rolling_average"]

    print(f"\nSKUs with overall MAPE <= {MAPE_PASS_THRESHOLD}%: "
          f"{int(modelled['meets_mape_threshold'].sum())} / {len(modelled)}")
    print(f"SKUs that beat the naive baseline (overall MAE): "
          f"{int(modelled['beats_naive_mae'].sum())} / {len(modelled)}")

    print("\n=== Per-SKU metrics (overall scope) ===")
    print(
        modelled[["item_name", "tier", "n_obs", "mae", "rmse", "mape", "naive_mae", "beats_naive_mae", "meets_mape_threshold"]]
        .sort_values(["tier", "mape"])
        .to_string(index=False)
    )

    print("\n=== Standard-period vs semestral-break (MAE primary for breaks) ===")
    for scope in ["standard_period", "semestral_break"]:
        sub = metrics_df[(metrics_df["period_scope"] == scope) & (metrics_df["tier"] != "rolling_average")]
        if len(sub):
            print(f"\n-- {scope} ({len(sub)} SKUs with test points in this scope) --")
            print(sub[["item_name", "tier", "n_obs", "mae", "rmse", "mape", "naive_mae"]].to_string(index=False))

    print(f"\nRolling-average (heuristic, 'Insufficient Data for Forecasting') SKUs: "
          f"{(fast['tier']=='rolling_average').sum()}")

    con.close()


if __name__ == "__main__":
    main()
