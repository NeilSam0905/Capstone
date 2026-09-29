"""
scripts/test_history_length.py
------------------------------------------------------------------
Does more history make the forecasts better? Measured on REAL data only: the
models the app uses are re-scored on the same past 30-day windows, but each
forecast is only allowed to see the last 3 / 6 / 9 / 12 / 18 months before it
(or everything, as in production).

Why not synthetic history
-------------------------
Already tried, twice (docs/SPARSE_DEMAND_EXPERIMENTS.md section 4,
docs/FAST_MOVING_BENCHMARK.md section 6.12): years of history resampled from an
item's own sales cannot hold a pattern the real sales do not, and the models
in use only read the last 180-365 days, so they did not move. Cutting REAL
history back is the version of "does more data help" that needs no invented
data: it traces accuracy against history length over the range the store has.

Fairness
--------
  - Same windows, same actual sales, same MASE denominator (the harness
    computes it from the full training slice whatever the model is shown), so
    only what the model sees changes between columns.
  - Positions stay aligned: trimming a forecast to its last N days trims the
    school-calendar day types and the category series by the same amount, so
    the calendar adjustment and the top-down share read the right dates.
  - "All" reproduces what the app stores in ustore.db (checked below).
  - A window of N months is only really N months where that much history
    existed; the earliest windows have about 14 months in total. The table
    reports the history each arm actually used on average.

Models (as shipped)
-------------------
  category  6-month average, calendar-adjusted  (step4c)
  item      category share + TSB blend, calendar-adjusted (step4), Fast and
            Slow items (the app forecasts the Fast ones; Slow scored for context)

Outputs (data/):
  history_length_test.csv     one row per level x history length
  history_length_series.csv   one row per category / item x history length

Run:  python scripts/test_history_length.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.topdown import topdown_tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
RESIDUE = "Uncategorised"

ARMS = [("3 months", 91), ("6 months", 182), ("9 months", 274), ("12 months", 365),
        ("18 months", 548), ("All history", None)]
ALL = "All history"


def load():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
           JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.transaction_type = 'sale'""",
        con, parse_dates=["calendar_date"])
    stored_cat = pd.read_sql_query(
        "SELECT forecast_category AS key, mase FROM Result_Category_Forecast_Metrics "
        "WHERE period_scope = 'overall'", con).set_index("key").mase
    stored_item = pd.read_sql_query(
        "SELECT product_id AS key, mase FROM Result_Forecast_Metrics "
        "WHERE period_scope = 'overall'", con).set_index("key").mase
    last_sale = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last_sale, freq="D")
    types = np.asarray(load_day_types(con, idx, HORIZON))
    con.close()
    prod["category"] = prod.forecast_category.fillna(RESIDUE)
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    cat_of = prod.set_index("product_id").category
    cats = daily.T.groupby(daily.columns.map(cat_of)).sum().T
    cats = cats.loc[:, cats.sum() > 0]
    return prod, daily, cats, types, stored_cat, stored_item


def limited(build, types, cat, max_days):
    """fit_predict that sees only the last `max_days` of its training slice.
    `build(types, cat)` makes the model; both are trimmed from the same start
    as the training slice, so position i still means the same date."""
    def _f(train, horizon):
        n = len(train)
        s = 0 if max_days is None else max(0, n - max_days)
        fn = build(types[s:], None if cat is None else cat[s:])
        return fn(np.asarray(train, dtype=float)[s:], horizon)
    return _f


def cat_model(types, _cat):
    return calendar_capped_fit_predict(rolling_mean_fit_predict(180), types)


def item_model(types, cat):
    return calendar_capped_fit_predict(topdown_tsb_fit_predict(cat), types, cat_values=cat)


def score(key, values, build, types, cat, arm, days):
    folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    ev = walk_forward_evaluate(key, values, limited(build, types, cat, days), arm, folds=folds)
    if not ev.sufficient:
        return None
    a = np.array([r["actual_30d"] for r in ev.rows]); p = np.array([r["pred_30d"] for r in ev.rows])
    s = np.array([r["naive_scale"] for r in ev.rows]); good = np.isfinite(s) & (s > 0)
    used = [f.train_end if days is None else min(days, f.train_end) for f in folds]
    mae = float(np.mean(np.abs(a - p)))
    return dict(mae=mae, mase=mae / s[good].mean() if good.any() else np.nan,
                abs_err=float(np.abs(a - p).sum()), bias=float((p - a).sum()),
                actual=float(a.sum()), months_used=float(np.mean(used)) / 30.44, n_folds=len(folds))


def main():
    prod, daily, cats, types, stored_cat, stored_item = load()
    cat_of = prod.set_index("product_id").category
    fsn = prod.set_index("product_id").fsn_class
    name = prod.set_index("product_id").item_name
    items = [p for p in daily.columns if fsn.get(p) in ("F", "S")]
    print(f"history: {len(daily)} days ({daily.index[0].date()} .. {daily.index[-1].date()}); "
          f"{cats.shape[1]} categories, {len(items)} Fast/Slow items")

    rows = []
    for arm, days in ARMS:
        for c in cats.columns:
            r = score(c, cats[c].to_numpy(float), cat_model, types, None, arm, days)
            if r:
                rows.append(dict(level="category", cohort="Categories", key=c, name=c, arm=arm, **r))
        for pid in items:
            cv = cats[cat_of[pid]].to_numpy(float)
            r = score(pid, daily[pid].to_numpy(float), item_model, types, cv, arm, days)
            if r:
                rows.append(dict(level="item", cohort="Fast items" if fsn[pid] == "F" else "Slow items",
                                 key=pid, name=name[pid], arm=arm, **r))
        print(f"  scored {arm}")
    per = pd.DataFrame(rows)

    # "All history" must be exactly what the app stores.
    full = per[per.arm == ALL]
    gc = (full[full.level == "category"].set_index("key").mase - stored_cat).abs().max()
    fi = full[(full.level == "item") & full.key.isin(stored_item.index)].set_index("key").mase
    gi = (fi - stored_item.reindex(fi.index)).abs().max()
    print(f"self-check vs ustore.db: category max MASE gap {gc:.2e}, item max MASE gap {gi:.2e}")
    assert gc < 1e-9 and gi < 1e-9, "the 'All history' arm does not reproduce the stored metrics"

    per.to_csv(os.path.join(DATA, "history_length_series.csv"), index=False, lineterminator="\n")

    summ = []
    order = [a for a, _ in ARMS]
    for cohort, g in per.groupby("cohort", sort=False):
        # Only series scored in every arm, so each column averages the same set.
        keys = g.groupby("key").arm.nunique()
        g = g[g.key.isin(keys[keys == len(ARMS)].index)]
        g = g[np.isfinite(g.mase)] if cohort != "Categories" else g
        keys = g.groupby("key").arm.nunique()
        g = g[g.key.isin(keys[keys == len(ARMS)].index)]
        base = g[g.arm == ALL].set_index("key")
        for arm in order:
            a = g[g.arm == arm]
            vs = a.set_index("key").mase - base.mase
            summ.append(dict(cohort=cohort, arm=arm, n=len(a), months_used=a.months_used.mean(),
                             mean_mase=a.mase.mean(), median_mase=a.mase.median(),
                             wmape_pct=100 * a.abs_err.sum() / a.actual.sum(),
                             bias_pct=100 * a.bias.sum() / a.actual.sum(),
                             better_than_all=int((vs < -1e-9).sum()), worse_than_all=int((vs > 1e-9).sum())))
    summ = pd.DataFrame(summ)
    summ.to_csv(os.path.join(DATA, "history_length_test.csv"), index=False, lineterminator="\n")
    print(summ.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("Wrote data/history_length_test.csv and data/history_length_series.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
