"""
scripts/daily_accuracy_check.py
------------------------------------------------------------------
How far off the forecasts are DAY BY DAY, next to the 30-day figure every
other check reports.

Every model on the Demand Forecast page is scored on its 30-day total
(forecasting/evaluate.py): one number per 30-day window. This scores the same
forecasts one day at a time. It runs step4c's six category models on step4c's
own 12 walk-forward windows, ending at the last tallied day. Each window's
30-day total is spread over the days by the weekday pattern, exactly as the
dashboard spreads it; Prophet keeps its own day-by-day curve. Every day is
then compared with what actually sold that day.

Levels: the store (all categories added up) and the 13 categories. Not single
items: about 80% of item-days sell nothing, so a daily error per item says
nothing.

Scored days: the days the store was open and recorded a sale
(forecasting/day_kinds.py). A day with no sale anywhere is a closure or a day
nothing was written down, so it is left out of the daily score. The 30-day
figure is the dashboard's own: every day of the window, and the model's
30-day total as the walk-forward check scores it.

Two daily versions, because the store does not trade every day. In the last
year it traded on about 204 of 360 days; the rest were closures, class
suspensions and non-selling days marked on the tally sheets' legend, which the
forecast cannot know in advance (Dim_Date publishes only a few closures). So:

  as drawn             the daily line exactly as the Demand Forecast page
                       draws it: each calendar day's expected share of the
                       30-day total, trading or not. Scored on trading days
                       only, it reads too low - the window's sales all land
                       on the days the store opened.
  trading days known   the same 30-day total spread over the days the store
                       actually traded in that window, in the same day-to-day
                       proportions. Hindsight on WHICH days the store opened,
                       none on how much it sold: this is the forecast's own
                       day-by-day accuracy.

Measures, pooled over the scored days (or windows):
  WAPE   total miss / total sold
  bias   (total forecast - total sold) / total sold; negative = too low
  MAPE   average of each day's (or window's) miss / sold, store level only:
         every scored day sold something, so it is defined there. A few
         trading days with only a handful of units tallied (3 of 199 under
         20 units) dominate it, so the median % miss - the typical day - is
         printed beside it

The store's daily error ("trading days known") is also split by kind of
school day (enrollment, the first two weeks of the first term, exam weeks,
breaks, event days, regular days), which shows where the day-by-day forecast
misses most.

Read-only on ustore.db. Writes data/daily_accuracy_by_model.csv and
data/daily_accuracy_by_day_kind.csv (data/ is git-ignored; scripts/vault.py
keeps the encrypted copy). Prophet takes a few minutes; --skip-prophet leaves
it out.

Run:  python scripts/daily_accuracy_check.py [--skip-prophet]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from forecasting import day_kinds  # noqa: E402
from forecasting.calendar_adjust import load_day_types  # noqa: E402
from forecasting.evaluate import make_folds  # noqa: E402
from forecasting.shape import day_shape, load_calendar  # noqa: E402
import forecast_options  # noqa: E402
import step4c_category_forecast as s4c  # noqa: E402

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
LABELS = {o["id"]: o["label"] for o in forecast_options.OPTIONS[forecast_options.CATEGORY]}
SELECTED = forecast_options.DEFAULTS[forecast_options.CATEGORY]


def daily_forecasts(series, gap, model_fn, curve, cal, shape=s4c.DEFAULT_SHAPE):
    """One row per category, window and day: the model's forecast for the day,
    what sold, and the model's 30-day total as the walk-forward check scores
    it (`total_30d`, the sum of its raw output). Every category column shares
    one index, so every category gets the same windows - which is what lets
    the categories be added up into a store forecast day by day."""
    H = s4c.HORIZON
    folds = make_folds(len(series), H, s4c.MIN_FOLDS, s4c.MAX_FOLDS, s4c.MIN_TRAIN)
    idx = series.index
    rows = []
    for cat in series.columns:
        v = series[cat].to_numpy(float)
        for f in folds:
            train = v[:f.train_end]
            out = np.asarray(model_fn(train, H), dtype=float).ravel()
            dates = idx[f.test_start:f.test_end]
            if curve:
                daily = np.clip(out[:H], 0.0, None)
            else:
                weights, _ = day_shape(shape, np.where(gap[:f.train_end], np.nan, train),
                                       idx[:f.train_end], cal, dates)
                daily = float(out.sum()) * weights
            actual = v[f.test_start:f.test_end]
            for d, fc, a in zip(dates, daily, actual):
                rows.append((cat, f.fold_index, d, float(fc), float(a), float(out.sum())))
    return pd.DataFrame(rows, columns=["category", "fold", "date", "forecast", "actual", "total_30d"])


def wape(f, a):
    a_sum = float(np.sum(a))
    return 100.0 * float(np.sum(np.abs(np.asarray(f) - np.asarray(a)))) / a_sum if a_sum else np.nan


def bias(f, a):
    a_sum = float(np.sum(a))
    return 100.0 * (float(np.sum(f)) - a_sum) / a_sum if a_sum else np.nan


def mape(f, a, how=np.mean):
    f, a = np.asarray(f, float), np.asarray(a, float)
    nz = a > 0
    return 100.0 * float(how(np.abs(f[nz] - a[nz]) / a[nz])) if nz.any() else np.nan


def trading_days_known(df):
    """`forecast_known`: each category window's forecast moved onto the days
    the store traded in it, keeping the days' proportions and the window's
    total (see the docstring). Zero on the other days, which are not scored.
    A window whose traded days carry no forecast at all has nothing to scale,
    so those days keep the forecast as drawn (zero)."""
    w = df.groupby(["category", "fold"])["forecast"]
    total = w.transform("sum")
    on_traded = df["forecast"].where(df["recorded"], 0.0).groupby([df["category"], df["fold"]]).transform("sum")
    scale = (total / on_traded).where(on_traded > 0)
    known = (df["forecast"] * scale).where(df["recorded"], 0.0)
    return known.fillna(df["forecast"])


def score(df, days):
    """The by-model row and the by-kind rows for one model's daily forecasts."""
    kind = days["kind"].reindex(df["date"]).to_numpy()
    recorded = days["recorded"].reindex(df["date"]).fillna(False).to_numpy(bool)
    df = df.assign(kind=kind, recorded=recorded)
    df["forecast_known"] = trading_days_known(df)

    # 30-day windows, as the dashboard scores them: every day, raw totals.
    win_cat = df.groupby(["category", "fold"]).agg(F=("total_30d", "first"), A=("actual", "sum"))
    win_store = win_cat.groupby("fold").sum()

    rec = df[df["recorded"]]
    store_day = rec.groupby("date").agg(drawn=("forecast", "sum"), known=("forecast_known", "sum"),
                                        actual=("actual", "sum"), kind=("kind", "first"))
    a = store_day["actual"]
    row = {
        "store_30d_wape_pct": wape(win_store["F"], win_store["A"]),
        "store_30d_mape_pct": mape(win_store["F"], win_store["A"]),
        "store_30d_median_ape_pct": mape(win_store["F"], win_store["A"], np.median),
        "store_daily_wape_drawn_pct": wape(store_day["drawn"], a),
        "store_daily_bias_drawn_pct": bias(store_day["drawn"], a),
        "store_daily_wape_known_pct": wape(store_day["known"], a),
        "store_daily_mape_known_pct": mape(store_day["known"], a),
        "store_daily_median_ape_known_pct": mape(store_day["known"], a, np.median),
        "store_daily_bias_known_pct": bias(store_day["known"], a),
        "category_30d_wape_pct": wape(win_cat["F"], win_cat["A"]),
        "category_daily_wape_drawn_pct": wape(rec["forecast"], rec["actual"]),
        "category_daily_wape_known_pct": wape(rec["forecast_known"], rec["actual"]),
        "scored_days": int(len(store_day)),
        "windows": int(len(win_store)),
        "categories": int(df["category"].nunique()),
    }
    by_kind = []
    for k in day_kinds.KINDS:
        g = store_day[store_day["kind"] == k]
        by_kind.append({
            "kind": k, "label": day_kinds.LABELS[k], "days": int(len(g)),
            "sold_per_day": float(g["actual"].mean()) if len(g) else np.nan,
            "forecast_per_day": float(g["known"].mean()) if len(g) else np.nan,
            "daily_wape_pct": wape(g["known"], g["actual"]) if len(g) else np.nan,
            "daily_bias_pct": bias(g["known"], g["actual"]) if len(g) else np.nan,
            "daily_bias_drawn_pct": bias(g["drawn"], g["actual"]) if len(g) else np.nan,
        })
    return row, by_kind, store_day


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("---")[0].strip())
    ap.add_argument("--skip-prophet", action="store_true",
                    help="leave Prophet out (it takes a few minutes)")
    args = ap.parse_args()

    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    wide, _ = s4c.load_category_series(con)
    series, history_end, _ = s4c.trim_padding(wide)
    series, n_pre, gap = s4c.extend_with_synthetic(con, series)
    cal = load_calendar(con)
    days = day_kinds.load_days(con)
    ctx = {"day_types": load_day_types(con, series.index, s4c.HORIZON),
           "index": series.index, "con": con}

    models = [o["id"] for o in forecast_options.OPTIONS[forecast_options.CATEGORY]
              if not (args.skip_prophet and o["id"] == "prophet")]
    print(f"Daily accuracy check: {series.shape[1]} categories, sales to {history_end.date()}, "
          f"{len(models)} models, the same 12 walk-forward windows as the Demand Forecast page")
    print(f"Scored days: the {int(days['recorded'].sum())} days the store was open and recorded "
          f"a sale (closures and days with nothing written down left out)\n")

    by_model, by_kind = [], []
    for m in models:
        t0 = time.time()
        fn = s4c.MODELS[m](ctx)
        df = daily_forecasts(series, gap, fn, m in s4c.CURVE_MODELS, cal)
        row, kinds, _ = score(df, days)
        by_model.append({"model": m, "label": LABELS.get(m, m), "selected": m == SELECTED, **row})
        by_kind.extend({"model": m, "label_model": LABELS.get(m, m), **k} for k in kinds)
        print(f"  {LABELS.get(m, m):36s} done ({time.time() - t0:5.1f}s)", flush=True)
    con.close()

    res = pd.DataFrame(by_model)
    kinds = pd.DataFrame(by_kind)
    os.makedirs(DATA, exist_ok=True)
    res.to_csv(os.path.join(DATA, "daily_accuracy_by_model.csv"), index=False, lineterminator="\n")
    kinds.to_csv(os.path.join(DATA, "daily_accuracy_by_day_kind.csv"), index=False, lineterminator="\n")

    print("\nStore total: the 30-day window against single days (WAPE; lower is better)")
    print(f"{'model':46s} {'30-day':>15s} {'daily, as drawn':>16s} {'daily, trading days known':>35s}")
    print(f"{'':46s} {'WAPE':>7s} {'median':>7s} {'WAPE':>7s} {'bias':>8s} {'WAPE':>8s} {'median':>7s} "
          f"{'MAPE':>8s} {'bias':>8s}")
    for r in res.itertuples():
        name = r.label + (" (selected)" if r.selected else "")
        print(f"{name:46s} {r.store_30d_wape_pct:6.1f}% {r.store_30d_median_ape_pct:6.1f}% "
              f"{r.store_daily_wape_drawn_pct:6.1f}% {r.store_daily_bias_drawn_pct:+7.1f}% "
              f"{r.store_daily_wape_known_pct:7.1f}% {r.store_daily_median_ape_known_pct:6.1f}% "
              f"{r.store_daily_mape_known_pct:7.1f}% {r.store_daily_bias_known_pct:+7.1f}%")
    print("('median' = the typical window's or day's % miss; MAPE = the average, which a few days "
          "with only a handful of units tallied blow up)")
    sel = res[res["selected"]].iloc[0]
    print(f"\nCategories pooled ({int(sel.categories)}), WAPE: 30-day / daily as drawn / daily trading days known")
    for r in res.itertuples():
        name = r.label + (" (selected)" if r.selected else "")
        print(f"  {name:44s} {r.category_30d_wape_pct:6.1f}% {r.category_daily_wape_drawn_pct:7.1f}% "
              f"{r.category_daily_wape_known_pct:7.1f}%")
    print(f"\n{int(sel.scored_days)} trading days scored in {int(sel.windows)} windows of 30 days.")

    print(f"\nStore total by kind of day, trading days known, {LABELS[SELECTED]} (selected)")
    print(f"{'kind of day':48s} {'days':>5s} {'sold/day':>9s} {'fcst/day':>9s} {'WAPE':>7s} {'bias':>8s}")
    for r in kinds[kinds["model"] == SELECTED].itertuples():
        if not r.days:
            print(f"{r.label:48s} {0:5d}")
            continue
        print(f"{r.label:48s} {r.days:5d} {r.sold_per_day:9.0f} {r.forecast_per_day:9.0f} "
              f"{r.daily_wape_pct:6.1f}% {r.daily_bias_pct:+7.1f}%")
    print("\nWrote data/daily_accuracy_by_model.csv and data/daily_accuracy_by_day_kind.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
