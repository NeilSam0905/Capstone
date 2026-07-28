"""
prophet_diagnostic.py — Open work item #6: diagnose the Prophet underperformance
per PROJECT_CONTEXT.md §9, before any decision to change models.

It fits, per SKU in the >=60-observation tier (§7.2), an 80/20 chronological split
and compares four forecasters on the held-out tail:

  naive_last   : previous period = forecast  (flat = last training value)   [the §7.4 baseline]
  naive_mean   : flat = mean of training values                            [secondary reference]
  prophet_base : vanilla Prophet (trend + yearly seasonality only)
  prophet_reg  : Prophet + the §7.2 Dim_Date regressors

Regressors: is_enrollment_period, is_exam_week, is_event_day, is_sem_break,
is_store_closed, semester_week. The series is irregular — Prophet is given the
tally dates as observed points, with NO zero interpolation (§7.2).

Metrics per SKU on the test tail, and split into a "semester" bucket vs a
"break" bucket (is_sem_break OR is_store_closed) so MAPE is judged where it is
stable and MAE where it is not (§7.4). Writes prophet_diagnostic_results.csv and
prints the answers to §9's four questions.
"""
import logging
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
for noisy in ("prophet", "cmdstanpy", "cmdstanpy.utils"):
    lg = logging.getLogger(noisy)
    lg.setLevel(logging.CRITICAL)
    lg.handlers = []
    lg.propagate = False
    lg.disabled = True

from prophet import Prophet  # noqa: E402

REGRESSORS = ["is_enrollment_period", "is_exam_week", "is_event_day",
              "is_sem_break", "is_store_closed", "semester_week"]
# semester_week (continuous) extrapolates badly across semester resets and made
# prophet_reg worse than vanilla — the winning config drops it.
REGRESSORS_NOWEEK = [r for r in REGRESSORS if r != "semester_week"]
MIN_OBS = 60
TEST_FRAC = 0.20
RESULTS_CSV = "prophet_diagnostic_results.csv"


def load_series():
    con = sqlite3.connect("ustore.db")
    df = pd.read_sql_query(
        """SELECT f.product_id, p.item_name, d.calendar_date AS ds, f.quantity_sold AS y,
                  d.is_enrollment_period, d.is_exam_week, d.is_event_day,
                  d.is_sem_break, d.is_store_closed, d.semester_week
           FROM Fact_Sales f
           JOIN Dim_Product p ON f.product_id = p.product_id
           JOIN Dim_Date d    ON f.date_id = d.date_id
           WHERE f.tally_date_flag = 1
           ORDER BY f.product_id, d.calendar_date""",
        con,
    )
    con.close()
    df["ds"] = pd.to_datetime(df["ds"])
    df["semester_week"] = df["semester_week"].fillna(0)
    return df


def metrics(y_true, y_pred):
    y_true = np.asarray(y_true, float)
    y_pred = np.asarray(y_pred, float)
    if len(y_true) == 0:
        return np.nan, np.nan, np.nan
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    nz = y_true != 0
    mape = float(np.mean(np.abs(err[nz]) / y_true[nz]) * 100) if nz.any() else np.nan
    return mae, rmse, mape


def fit_prophet(train, test, use_reg, tuned=False, growth="linear", log=False):
    """log=True fits on log1p(y) and back-transforms — variance stabilization for
    bursty count data. growth='flat' disables trend extrapolation."""
    if tuned:
        # §7.4 escalation: damp the trend + seasonality priors so Prophet stops
        # overfitting the sparse bursty series (fewer yearly Fourier terms too).
        m = Prophet(weekly_seasonality=False, daily_seasonality=False,
                    yearly_seasonality=3, changepoint_prior_scale=0.01,
                    seasonality_prior_scale=0.1, growth=growth)
    else:
        m = Prophet(weekly_seasonality=False, daily_seasonality=False,
                    yearly_seasonality=True, growth=growth)
    regs = use_reg if isinstance(use_reg, list) else (REGRESSORS if use_reg else [])
    for r in regs:
        m.add_regressor(r)
    tr = train[["ds", "y"] + regs].copy()
    if log:
        tr["y"] = np.log1p(tr["y"])
    m.fit(tr)
    pred = m.predict(test[["ds"] + regs])["yhat"].values
    if log:
        pred = np.expm1(pred)
    return np.clip(pred, 0, None)


def run():
    df = load_series()
    counts = df.groupby("product_id").size()
    keep = counts[counts >= MIN_OBS].index
    print(f"SKUs in >=60 tier: {len(keep)} (of {df['product_id'].nunique()} sold)")

    rows = []
    for pid in keep:
        s = df[df["product_id"] == pid].sort_values("ds").reset_index(drop=True)
        n = len(s)
        cut = int(round(n * (1 - TEST_FRAC)))
        train, test = s.iloc[:cut], s.iloc[cut:]
        if len(test) < 3 or train["y"].nunique() < 2:
            continue
        yt = test["y"].values

        preds = {
            "naive_last": np.full(len(test), train["y"].iloc[-1], float),
            "naive_mean": np.full(len(test), train["y"].mean(), float),
        }
        try:
            preds["prophet_base"] = fit_prophet(train, test, use_reg=False)
            preds["prophet_reg"] = fit_prophet(train, test, use_reg=True)
            preds["prophet_tuned"] = fit_prophet(train, test, use_reg=True, tuned=True)
            preds["prophet_flatlog"] = fit_prophet(
                train, test, use_reg=REGRESSORS_NOWEEK, tuned=True,
                growth="flat", log=True)
        except Exception as e:  # noqa: BLE001
            print(f"  [pid {pid}] fit failed: {e}")
            continue

        is_break = ((test["is_sem_break"] == 1) | (test["is_store_closed"] == 1)).values
        rec = {"product_id": pid, "item_name": s["item_name"].iloc[0],
               "n_obs": n, "n_test": len(test), "n_test_break": int(is_break.sum())}
        for name, yp in preds.items():
            mae, rmse, mape = metrics(yt, yp)
            rec[f"{name}_MAE"] = round(mae, 3)
            rec[f"{name}_RMSE"] = round(rmse, 3)
            rec[f"{name}_MAPE"] = round(mape, 1) if not np.isnan(mape) else np.nan
            # semester-bucket MAPE (where MAPE is meaningful), break-bucket MAE
            sm_mae, _, sm_mape = metrics(yt[~is_break], yp[~is_break])
            bk_mae, _, _ = metrics(yt[is_break], yp[is_break])
            rec[f"{name}_MAPE_sem"] = round(sm_mape, 1) if not np.isnan(sm_mape) else np.nan
            rec[f"{name}_MAE_break"] = round(bk_mae, 3) if not np.isnan(bk_mae) else np.nan
        rows.append(rec)

    res = pd.DataFrame(rows)
    res.to_csv(RESULTS_CSV, index=False)
    summarize(res)
    return res


MODELS = ["naive_last", "naive_mean", "prophet_base", "prophet_reg",
          "prophet_tuned", "prophet_flatlog"]


def summarize(res):
    n = len(res)
    tot_test = int(res["n_test"].sum())
    tot_break = int(res["n_test_break"].sum())
    naive_best = res[["naive_last_MAE", "naive_mean_MAE"]].min(axis=1)

    print(f"\n=== PROPHET PERFORMANCE REPORT ===")
    print(f"Tier: >=60 observations (standard-fit tier, §7.2).  SKUs evaluated: {n}.")
    print(f"Validation: chronological 80/20 hold-out per SKU.  "
          f"Test points: {tot_test} ({tot_break} in break periods, {tot_test-tot_break} in semester).")
    print(f"Metrics averaged across SKUs (median | mean). MAPE% on non-zero actuals.\n")

    # ---- Table 1: central tendency per model ----
    hdr = f"| {'model':13} | {'MAE med':>7} | {'MAE mean':>8} | {'RMSE med':>8} | {'MAPE% med':>9} | {'MAPE% mean':>10} | {'MAPE%_sem med':>13} |"
    sep = "|" + "|".join("-" * (w + 2) for w in (13, 7, 8, 8, 9, 10, 13)) + "|"
    print("Table 1 — accuracy by model (per-SKU errors, aggregated):")
    print(hdr); print(sep)
    for m in MODELS:
        print(f"| {m:13} | {res[m+'_MAE'].median():7.2f} | {res[m+'_MAE'].mean():8.2f} | "
              f"{res[m+'_RMSE'].median():8.2f} | {res[m+'_MAPE'].median():9.1f} | "
              f"{res[m+'_MAPE'].mean():10.1f} | {res[m+'_MAPE_sem'].median():13.1f} |")

    # ---- Table 2: skill vs the naive baseline + acceptance ----
    print("\nTable 2 — skill vs naive baseline & acceptance (§7.4):")
    print(f"| {'model':13} | {'% beats naive (MAE)':>19} | {'% beats vanilla (MAE)':>21} | {'% MAPE_sem <=20%':>16} |")
    print("|" + "|".join("-" * (w + 2) for w in (13, 19, 21, 16)) + "|")
    for m in MODELS:
        bn = (res[m+"_MAE"] < naive_best).mean() * 100 if m.startswith("prophet") else float("nan")
        bv = (res[m+"_MAE"] < res["prophet_base_MAE"]).mean() * 100 if m != "prophet_base" else float("nan")
        hit = (res[m+"_MAPE_sem"] <= 20).mean() * 100
        bn_s = f"{bn:19.0f}" if not np.isnan(bn) else f"{'—':>19}"
        bv_s = f"{bv:21.0f}" if not np.isnan(bv) else f"{'—':>21}"
        print(f"| {m:13} | {bn_s} | {bv_s} | {hit:16.0f} |")

    # ---- Table 3: MAPE distribution for the two Prophet variants of record ----
    print("\nTable 3 — MAPE% distribution (all test points), selected models:")
    print(f"| {'model':13} | {'min':>6} | {'p25':>6} | {'median':>7} | {'p75':>6} | {'max':>7} |")
    print("|" + "|".join("-" * (w + 2) for w in (13, 6, 6, 7, 6, 7)) + "|")
    for m in ["naive_last", "prophet_base", "prophet_reg", "prophet_tuned", "prophet_flatlog"]:
        s = res[m+"_MAPE"]
        print(f"| {m:13} | {s.min():6.1f} | {s.quantile(.25):6.1f} | {s.median():7.1f} | "
              f"{s.quantile(.75):6.1f} | {s.max():7.1f} |")

    print(f"\nBest single model by median MAE: "
          f"{min(MODELS, key=lambda m: res[m+'_MAE'].median())}. "
          f"Per-SKU detail in {RESULTS_CSV}.")


if __name__ == "__main__":
    run()
