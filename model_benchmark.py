"""
model_benchmark.py — benchmark alternative forecasters against the Prophet
results, on the same 89 SKUs (>=60 tier), same chronological 80/20 split.

Models
------
naive_last    : last training value carried forward            (the §7.4 baseline)
rolling_med   : median of last 5 training observations         (robust to bursts)
ewma          : exponentially weighted mean (alpha=0.3)
croston       : Croston's method for intermittent demand
sba           : Syntetos-Boylan Approximation (bias-corrected Croston)
global_xgb    : ONE gradient-boosted model trained across ALL SKUs, using
                lag/rolling features + the Dim_Date calendar flags. This is the
                M5-competition-winning family and the main candidate: with a
                median of 25 obs/SKU, per-series models starve, while a global
                model borrows strength across the whole catalogue.

Leakage control: for the test block, lag/rolling features are FROZEN at the end
of training (you do not know future actuals when forecasting a billing month);
only the calendar features vary per test date. This mirrors production use and
keeps the comparison honest against naive/Prophet.

Metric: MASE (mean absolute scaled error) is primary — MASE < 1.0 means the
model beats the in-sample naive forecast, the standard acceptance metric for
intermittent demand, and the criterion recommended in place of MAPE <= 20%.
"""
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
from xgboost import XGBRegressor  # noqa: E402

CAL = ["is_enrollment_period", "is_exam_week", "is_event_day",
       "is_sem_break", "is_store_closed", "semester_week"]
MIN_OBS = 60
TEST_FRAC = 0.20
OUT_CSV = "model_benchmark_results.csv"


def load():
    con = sqlite3.connect("ustore.db")
    df = pd.read_sql_query(
        f"""SELECT f.product_id, p.item_name, d.calendar_date AS ds,
                   f.quantity_sold AS y, {', '.join('d.'+c for c in CAL)}
            FROM Fact_Sales f
            JOIN Dim_Product p ON f.product_id=p.product_id
            JOIN Dim_Date d ON f.date_id=d.date_id
            ORDER BY f.product_id, d.calendar_date""", con)
    con.close()
    df["ds"] = pd.to_datetime(df["ds"])
    df["semester_week"] = df["semester_week"].fillna(0)
    return df


# ---------- intermittent-demand methods ----------
def croston(y, alpha=0.1, sba=False):
    """Croston: smooth demand SIZE and INTERVAL separately. SBA applies the
    (1 - alpha/2) bias correction."""
    y = np.asarray(y, float)
    nz = np.flatnonzero(y > 0)
    if len(nz) == 0:
        return 0.0
    z = y[nz[0]]                      # demand size
    x = nz[0] + 1                     # interval
    last = nz[0]
    for i in nz[1:]:
        z += alpha * (y[i] - z)
        x += alpha * ((i - last) - x)
        last = i
    f = z / x if x > 0 else 0.0
    return f * (1 - alpha / 2) if sba else f


def ewma(y, alpha=0.3):
    y = np.asarray(y, float)
    s = y[0]
    for v in y[1:]:
        s = alpha * v + (1 - alpha) * s
    return s


# ---------- feature builder for the global model ----------
def make_features(g):
    """Past-only lag/rolling features for one SKU (shifted, so row t sees < t)."""
    g = g.copy()
    s = g["y"].shift(1)
    g["lag1"] = s
    g["lag2"] = g["y"].shift(2)
    g["lag3"] = g["y"].shift(3)
    g["roll3"] = s.rolling(3, min_periods=1).mean()
    g["roll5"] = s.rolling(5, min_periods=1).mean()
    g["roll10"] = s.rolling(10, min_periods=1).mean()
    g["rollmed5"] = s.rolling(5, min_periods=1).median()
    g["expmean"] = s.expanding(min_periods=1).mean()
    g["gap_days"] = g["ds"].diff().dt.days.fillna(0)
    return g


FEATS = ["lag1", "lag2", "lag3", "roll3", "roll5", "roll10",
         "rollmed5", "expmean", "gap_days"] + CAL


def mase(yt, yp, train_y):
    """Scale by the in-sample naive-1 MAE (Hyndman)."""
    yt, yp = np.asarray(yt, float), np.asarray(yp, float)
    d = np.mean(np.abs(np.diff(np.asarray(train_y, float))))
    if d == 0 or np.isnan(d):
        return np.nan
    return np.mean(np.abs(yp - yt)) / d


def main():
    df = load()
    counts = df.groupby("product_id").size()
    keep = set(counts[counts >= MIN_OBS].index)
    df = df[df["product_id"].isin(keep)].copy()
    # build features per SKU then reassemble (groupby.apply in pandas 3.0 would
    # absorb product_id into the index)
    df = pd.concat([make_features(g.sort_values("ds"))
                    for _, g in df.groupby("product_id")], ignore_index=True)

    # ---- split, and build the global training set ----
    splits, tr_parts = {}, []
    for pid, g in df.groupby("product_id"):
        g = g.sort_values("ds").reset_index(drop=True)
        cut = int(round(len(g) * (1 - TEST_FRAC)))
        tr, te = g.iloc[:cut], g.iloc[cut:]
        if len(te) < 3 or tr["y"].nunique() < 2:
            continue
        splits[pid] = (tr, te)
        tr_parts.append(tr)

    train_all = pd.concat(tr_parts).dropna(subset=["lag3"])
    model = XGBRegressor(n_estimators=400, max_depth=5, learning_rate=0.05,
                         subsample=0.8, colsample_bytree=0.8,
                         objective="reg:squarederror", n_jobs=4,
                         random_state=0)
    model.fit(train_all[FEATS], train_all["y"])
    print(f"global XGB trained on {len(train_all)} rows from {len(splits)} SKUs")

    rows = []
    for pid, (tr, te) in splits.items():
        ytr, yt = tr["y"].values, te["y"].values

        # frozen state at end of training (no future actuals leak in)
        frozen = {f: tr[f].iloc[-1] if f in tr else 0 for f in FEATS}
        frozen["lag1"], frozen["lag2"], frozen["lag3"] = ytr[-1], ytr[-2], ytr[-3]
        frozen["roll3"] = ytr[-3:].mean()
        frozen["roll5"] = ytr[-5:].mean()
        frozen["roll10"] = ytr[-10:].mean()
        frozen["rollmed5"] = np.median(ytr[-5:])
        frozen["expmean"] = ytr.mean()
        X = pd.DataFrame([frozen] * len(te))
        for c in CAL:                       # calendar varies per test date
            X[c] = te[c].values
        X["gap_days"] = te["ds"].diff().dt.days.fillna(0).values

        preds = {
            "naive_last": np.full(len(te), ytr[-1], float),
            "rolling_med": np.full(len(te), np.median(ytr[-5:]), float),
            "ewma": np.full(len(te), ewma(ytr), float),
            "croston": np.full(len(te), croston(ytr), float),
            "sba": np.full(len(te), croston(ytr, sba=True), float),
            "global_xgb": np.clip(model.predict(X[FEATS]), 0, None),
        }
        rec = {"product_id": pid, "item_name": te["item_name"].iloc[0], "n_obs": len(tr) + len(te)}
        for k, p in preds.items():
            rec[f"{k}_MAE"] = np.mean(np.abs(p - yt))
            rec[f"{k}_RMSE"] = np.sqrt(np.mean((p - yt) ** 2))
            rec[f"{k}_MASE"] = mase(yt, p, ytr)
        rows.append(rec)

    r = pd.DataFrame(rows)
    r.to_csv(OUT_CSV, index=False)

    models = ["naive_last", "rolling_med", "ewma", "croston", "sba", "global_xgb"]
    print(f"\n=== BENCHMARK: {len(r)} SKUs, 80/20 split ===")
    print(f"{'model':13} {'MAE med':>8} {'MAE mean':>9} {'RMSE med':>9} "
          f"{'MASE med':>9} {'%MASE<1':>8} {'%beats naive':>13}")
    for m in models:
        bn = "" if m == "naive_last" else "{:.0f}".format(
            (r[m + "_MAE"] < r["naive_last_MAE"]).mean() * 100)
        print(f"{m:13} {r[m+'_MAE'].median():8.2f} {r[m+'_MAE'].mean():9.2f} "
              f"{r[m+'_RMSE'].median():9.2f} {r[m+'_MASE'].median():9.2f} "
              f"{(r[m+'_MASE']<1).mean()*100:7.0f}% {bn:>13}")
    print(f"\nwrote {OUT_CSV}")


if __name__ == "__main__":
    main()
