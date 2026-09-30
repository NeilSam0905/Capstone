"""
scripts/test_category_forecast_shape.py
------------------------------------------------------------------
Does a DATE-AWARE model give a better DAY-BY-DAY shape than the flat line a
trailing average draws?

A trailing average has no idea about dates, so its 30-day forecast is a flat
line. The 30-day TOTAL from the 6-month average is the validated part
(scripts/step4c_category_forecast.py). This tests whether letting a
date-aware model decide how that total is spread across the 30 days is
better than spreading it evenly, scored day by day.

Hybrid forecast = (6-month-average 30-day total) x (shape from a date-aware
model, normalised to sum to 1). The 30-day total is therefore IDENTICAL for
every hybrid; only the daily distribution differs. Shapes tested:

  Weekday pattern + known closures        mean by weekday over the last 365 days
                                          (non-closed days), zero on published
                                          closures
  Calendar regression                     Ridge on weekday + enrollment / exam /
                                          event / semester-break flags + closures
  Prophet: weekly + calendar (no yearly)  Dim_Date flags as regressors
  Prophet: weekly + yearly + calendar
  Holt-Winters weekly

and, for contrast, Prophet / Holt-Winters shipped AS-IS (their own total AND
shape), which is what "just use Prophet" would do.

Same series and folds as step4c: DB category series trimmed to the last real
sale day, 30-day horizon, up to 12 folds per category. Published-calendar flags
(Dim_Date) are known in advance, so using them for the test window is not
look-ahead.

Reading the result: `daily_error_vs_flat` < 1 means the shape beats a flat line
on day-by-day error. `total_30d_WMAPE` is the error on the 30-day total.

Outputs: data/category_forecast_shape_test.csv (one row per method) and
data/category_forecast_shape_folds.csv (one row per category-fold-method).
Needs Prophet and scikit-learn; ~2 minutes.

Run:  python scripts/test_category_forecast_shape.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from forecasting.baselines import ets_fit_predict
from forecasting.evaluate import make_folds
from forecasting.history import trim_to_history
from forecasting.prophet_model import prophet_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
CAL_COLS = ["is_enrollment_period", "is_exam_week", "is_event_day",
            "is_sem_break", "is_store_closed"]
HORIZON = 30


def load():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    fact = pd.read_sql_query("""
        SELECT d.calendar_date, f.quantity_sold, p.forecast_category
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'""", con, parse_dates=["calendar_date"])
    caldf = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CAL_COLS), con,
        parse_dates=["calendar_date"]).set_index("calendar_date")
    con.close()
    fact["forecast_category"] = fact["forecast_category"].fillna("Uncategorised")
    idx = pd.date_range(fact.calendar_date.min(), fact.calendar_date.max(), freq="D")
    wide = (fact.groupby(["forecast_category", "calendar_date"]).quantity_sold.sum()
            .unstack(0).reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    wide = wide.loc[:, wide.sum() > 0]
    series = trim_to_history(wide)[0]
    cal = caldf.reindex(series.index).fillna(0.0).astype(float)
    return series, cal


def main():
    series, cal = load()
    sidx = series.index
    closed = cal["is_store_closed"].to_numpy()
    flags = cal[["is_enrollment_period", "is_exam_week", "is_event_day",
                 "is_sem_break"]].to_numpy()
    dow = sidx.dayofweek.to_numpy()
    flat = np.full(HORIZON, 1.0 / HORIZON)

    def dow_shape(v, f):
        n, lo = f.train_end, max(0, f.train_end - 365)
        y, c, d = v[lo:n], closed[lo:n], dow[lo:n]
        prof = np.array([y[(d == k) & (c == 0)].mean()
                         if ((d == k) & (c == 0)).any() else y.mean() for k in range(7)])
        win = slice(f.test_start, f.test_end)
        return prof[dow[win]] * (1 - closed[win])

    def ridge_shape(v, f):
        n, lo = f.train_end, max(0, f.train_end - 365)
        def X(sl):
            return np.column_stack([np.eye(7)[dow[sl]], flags[sl], closed[sl]])
        m = Ridge(alpha=1.0).fit(X(slice(lo, n)), v[lo:n])
        return np.maximum(m.predict(X(slice(f.test_start, f.test_end))), 0.0)

    def model_shape(fn):
        return lambda v, f: np.asarray(fn(v[:f.train_end], HORIZON), float)

    shapes = {
        "Weekday pattern + known closures": dow_shape,
        "Calendar regression (weekday+events+closures)": ridge_shape,
        "Prophet: weekly + calendar (no yearly)": model_shape(
            prophet_fit_predict(sidx, cal, "p_w", weekly=True, yearly=False)),
        "Prophet: weekly + yearly + calendar": model_shape(
            prophet_fit_predict(sidx, cal, "p_wy", weekly=True, yearly=True)),
        "Holt-Winters weekly": model_shape(ets_fit_predict()),
    }

    rec, t0 = [], time.time()
    for cat in series.columns:
        v = series[cat].to_numpy(float)
        for f in make_folds(v.size, HORIZON, 3, 12, 60):
            act = v[f.test_start:f.test_end]
            total6 = HORIZON * v[:f.train_end][-180:].mean()
            has_event = flags[f.test_start:f.test_end][:, [0, 2]].sum() > 0
            break_days = int(flags[f.test_start:f.test_end][:, 3].sum())
            base = dict(cat=cat, fold=f.fold_index, has_event=has_event,
                        break_days=break_days, act_total=act.sum())
            rec.append({**base, "method": "Flat line (6-month avg)",
                        "daily_mae": np.abs(act - total6 * flat).mean(),
                        "total_err": abs(act.sum() - total6)})
            for name, fn in shapes.items():
                p = np.maximum(fn(v, f), 0.0)
                s = p / p.sum() if p.sum() > 0 else flat
                hyb = total6 * s
                rec.append({**base, "method": "6-month total x shape: " + name,
                            "daily_mae": np.abs(act - hyb).mean(),
                            "total_err": abs(act.sum() - hyb.sum())})
                if name.startswith(("Prophet", "Holt")):
                    rec.append({**base, "method": "AS-IS (own total + shape): " + name,
                                "daily_mae": np.abs(act - p).mean(),
                                "total_err": abs(act.sum() - p.sum())})
        print("  %-22s done (%4.0fs)" % (cat, time.time() - t0), flush=True)

    df = pd.DataFrame(rec)
    fl = df[df.method == "Flat line (6-month avg)"].set_index(["cat", "fold"])
    out = []
    for m, g in df.groupby("method", sort=False):
        g = g.set_index(["cat", "fold"])
        base = fl.loc[g.index]
        ev = g.has_event.to_numpy()
        brk = (g.break_days >= 5).to_numpy()
        out.append(dict(
            method=m,
            daily_error_vs_flat=g.daily_mae.sum() / base.daily_mae.sum(),
            folds_beating_flat_pct=100 * (g.daily_mae < base.daily_mae).mean(),
            daily_error_vs_flat_event_windows=(g.daily_mae[ev].sum() / base.daily_mae[ev].sum())
            if ev.any() else np.nan,
            # Months with 5+ semester-break days (4 of the 12 test windows): the
            # calendar's clearest effect, and the dip a forecast window with a
            # break in it will show.
            daily_error_vs_flat_break_months=(g.daily_mae[brk].sum() / base.daily_mae[brk].sum())
            if brk.any() else np.nan,
            daily_error_vs_flat_other_months=(g.daily_mae[~brk].sum() / base.daily_mae[~brk].sum())
            if (~brk).any() else np.nan,
            total_30d_wmape_pct=100 * g.total_err.sum() / g.act_total.sum(),
            n_category_folds=len(g)))
    res = pd.DataFrame(out)
    df.to_csv(os.path.join(DATA, "category_forecast_shape_folds.csv"), index=False, lineterminator="\n")
    res.to_csv(os.path.join(DATA, "category_forecast_shape_test.csv"), index=False, lineterminator="\n")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    print("\n" + res.round(3).to_string(index=False))
    print("\nWrote data/category_forecast_shape_test.csv and data/category_forecast_shape_folds.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
