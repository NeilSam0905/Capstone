"""
scripts/compare_category_forecast_methods.py
------------------------------------------------------------------
Which category-level forecaster should the dashboard use? Scores every
candidate on the SAME folds, against the SAME actuals, using only what was
known at each forecast origin - the comparison that
scripts/step4c_category_forecast.py's docstring rests on.

Why this exists
---------------
scripts/forecast_category_prophet.py reports MASE 0.99 with 8 of 12 categories
below 1. That harness scores each fold over the trading days actually observed
in the test window, which production cannot know, trains on sale-days only,
and scales MASE by blocks of 30 trading-day rows rather than 30 calendar days.
This script removes those advantages one at a time and adds baselines that
need no foresight, so the Prophet result can be read against them.

Candidates (all forecast the 30-CALENDAR-day total of one category)
-------------------------------------------------------------------
  naive_last30        repeat the last 30 calendar days           (the bar to beat)
  mean_90 / 180 / 365 trailing calendar-day average, scaled to 30 days
  prophet_marco       forecast_category_prophet.py as committed  (its own folds)
  prophet_zf_hind     Prophet on ZERO-FILLED trading days, summed over the trading
                      days that actually occurred (hindsight - isolates zero-fill)
  prophet_zf_expected the same fit, but each day weighted by the probability the
                      store trades (weekday rate from the trailing 365 days before
                      the origin; zero on published closures) - what production can do
  blend_50            0.5 x prophet_zf_expected + 0.5 x mean_180

Metrics per method
------------------
  wmape / bias        pooled over every category-fold
  beats_naive         categories whose MAE is below naive_last30's
  mase_marco_scale    macro MASE with forecast_category_prophet.py's denominator
  mase_calendar_scale macro MASE with a denominator in the scored unit: in-sample
                      naive MAE on non-overlapping 30-CALENDAR-day block totals

The last two differ because the first denominator is built from 30 ROWS of the
trading-day series (about 43 calendar days) while errors are on 30-calendar-day
windows; it comes out ~1.4x too large, which is why a naive forecast itself
scores under 1 on it.

Inputs: ustore.db (Dim_Product.forecast_category, Dim_Date), data/rebuild_sales_long.csv,
data/rebuild_day_status.csv, data/category_prophet_forecast.csv (marco's folds).
Outputs: data/category_forecast_method_comparison.csv (one row per method) and
data/category_forecast_method_folds.csv (one row per category-fold).
Needs Prophet; ~1 minute.

Run:  python scripts/compare_category_forecast_methods.py
------------------------------------------------------------------
"""
import importlib.util
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "ustore.db")
H = 30

spec = importlib.util.spec_from_file_location(
    "fcp", os.path.join(ROOT, "scripts", "forecast_category_prophet.py"))
fcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fcp)

METHODS = ["naive_last30", "mean_90", "mean_180", "mean_365", "prophet_marco",
           "prophet_zf_hind", "prophet_zf_expected", "blend_50"]


def dow_rate(cut, obs, pub):
    """P(store trades | weekday, not a published closure) from the 365 days up
    to and including the origin - pre-origin data only."""
    lo = cut - pd.Timedelta(days=365)
    o = obs[(obs.index > lo) & (obs.index <= cut)]
    o = o[pub.reindex(o.index).fillna(0).to_numpy() == 0]
    r = o.groupby(o.index.dayofweek).mean()
    return {d: float(r.get(d, o.mean())) for d in range(7)}


def score_folds(daily, cal, obs, pub, marco_folds):
    first = daily["ds"].min()
    trading = sorted(pd.Timestamp(d) for d in obs[obs == 1].index if d <= daily["ds"].max())
    tidx = pd.DatetimeIndex(trading)
    trading_set = set(trading)
    rows = []
    for cat, folds in marco_folds.groupby("forecast_category"):
        g = daily[daily["forecast_category"] == cat]
        series = pd.Series(0.0, index=tidx)          # zero-filled trading days
        series.loc[g["ds"].values] = g["y"].values
        for r in folds.itertuples():
            cut = r.origin
            win = pd.date_range(cut + pd.Timedelta(days=1), periods=H, freq="D")

            def trailing(n):
                lo = max(cut - pd.Timedelta(days=n - 1), first)
                span = (cut - lo).days + 1
                s = g[(g["ds"] >= lo) & (g["ds"] <= cut)]["y"].sum()
                return float(s) * H / span

            # MASE denominator in the scored unit: 30-calendar-day block totals
            cal_idx = pd.date_range(first, cut, freq="D")
            cs = (g[g["ds"] <= cut].set_index("ds")["y"]
                  .reindex(cal_idx, fill_value=0.0).to_numpy())
            nb = cs.size // H
            scale_cal = float(np.mean(np.abs(np.diff(cs[-nb * H:].reshape(nb, H).sum(1)))))

            train = series[series.index <= cut]
            tdf = pd.DataFrame({"ds": train.index, "y": train.values})
            rate = dow_rate(cut, obs, pub)
            w = np.array([0.0 if pub.get(d, 0) == 1 else rate[d.dayofweek] for d in win])
            hind = np.array([d in trading_set for d in win])
            tier = fcp.sufficiency_tier(int((train.values > 0).sum()))
            cap = fcp.plausibility_cap(train.values, H)
            if tier == "none":
                lvl = float(train.tail(30).mean())
                p_hind, p_exp = lvl * hind.sum(), lvl * w.sum()
            else:
                try:
                    yhat, _ = fcp.fit_predict(tdf, list(win), cal, fcp.CALENDAR_REGRESSORS, tier)
                    p_hind = min(float(yhat[hind].sum()), cap)
                    p_exp = min(float((yhat * w).sum()), cap)
                except Exception:
                    lvl = float(train.tail(30).mean())
                    p_hind, p_exp = lvl * hind.sum(), lvl * w.sum()

            m180 = trailing(180)
            rows.append(dict(
                forecast_category=cat, origin=cut, actual=r.actual_30d,
                naive_last30=trailing(30), mean_90=trailing(90), mean_180=m180,
                mean_365=trailing(365), prophet_marco=r.pred_30d,
                prophet_zf_hind=p_hind, prophet_zf_expected=p_exp,
                blend_50=0.5 * p_exp + 0.5 * m180,
                scale_marco=r.naive_scale, scale_cal=scale_cal))
        print("  %-22s done" % cat, flush=True)
    return pd.DataFrame(rows)


def summarise(df):
    per_mae = {m: df.groupby("forecast_category").apply(
        lambda g, m=m: (g["actual"] - g[m]).abs().mean(), include_groups=False)
        for m in METHODS}
    sc_marco = df.groupby("forecast_category")["scale_marco"].mean()
    sc_cal = df.groupby("forecast_category")["scale_cal"].mean()
    out = []
    for m in METHODS:
        err = (df["actual"] - df[m]).abs().sum()
        out.append(dict(
            method=m,
            wmape_pct=100 * err / df["actual"].sum(),
            bias_pct=100 * (df[m].sum() - df["actual"].sum()) / df["actual"].sum(),
            beats_naive_last30=int((per_mae[m] < per_mae["naive_last30"]).sum()),
            n_categories=int(per_mae[m].size),
            mase_marco_scale=float((per_mae[m] / sc_marco).mean()),
            mase_marco_scale_lt1=int((per_mae[m] / sc_marco < 1).sum()),
            mase_calendar_scale=float((per_mae[m] / sc_cal).mean()),
            mase_calendar_scale_lt1=int((per_mae[m] / sc_cal < 1).sum()),
            n_folds=len(df)))
    return pd.DataFrame(out)


def main():
    fcp._quiet()
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    daily, cal, days, _ = fcp.load_inputs(con)
    pub = (pd.read_sql_query("SELECT calendar_date, is_store_closed FROM Dim_Date", con,
                             parse_dates=["calendar_date"])
             .set_index("calendar_date")["is_store_closed"])
    con.close()
    days["calendar_date"] = pd.to_datetime(days["calendar_date"])
    obs = days.set_index("calendar_date")["demand_observable"]
    marco = pd.read_csv(os.path.join(DATA, "category_prophet_forecast.csv"),
                        parse_dates=["origin"])

    print("Scoring %d category-folds..." % len(marco))
    df = score_folds(daily, cal, obs, pub, marco)
    res = summarise(df)

    df.to_csv(os.path.join(DATA, "category_forecast_method_folds.csv"),
              index=False, lineterminator="\n")
    res.to_csv(os.path.join(DATA, "category_forecast_method_comparison.csv"),
               index=False, lineterminator="\n")

    pd.set_option("display.width", 200)
    print("\n" + res.round(2).to_string(index=False))
    print("\nWrote data/category_forecast_method_comparison.csv and "
          "data/category_forecast_method_folds.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
