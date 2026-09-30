"""
scripts/test_lead_time_gap.py
------------------------------------------------------------------
Does validating with a lead-time gap change the accuracy the app reports?

Every fold so far has scored a forecast against the days immediately after
training ends, as if it could be acted on the instant it is made. It cannot:
placing a reorder does not restock the shelf for `Dim_Product.lead_time_days`
(14-28 days here). What a forecast is actually used for is the days STARTING
AFTER that lead time - the days a reorder placed today would still need to be
covering by the time it arrives. `forecasting/evaluate.py`'s new `gap`
parameter (see its module docstring, point 3) tests exactly that: the
training slice ends `gap` days earlier than it would at gap=0, so the fold is
scored on the same real outcome window using only the information that would
actually have been available `gap` days before it.

This is a validation-only question. It does NOT change what step4/step4c
forecast or write to the database - it changes how honestly their accuracy
claims describe the situation the forecast is actually used in.

What is scored
---------------
The two models currently shipped, unchanged, on the SAME series and origins
as step4 / step4c, at gap=0 (current) and gap=lead_time_days (proposed):

  item level      the 58 Fast items' topdown_tsb blend (forecasting.topdown.
                  topdown_tsb_fit_predict), gap = each item's own
                  Dim_Product.lead_time_days (14, 18 or 28)
  category level  the 6-month average (RM6_6month_180d), gap = each
                  category's SALES-WEIGHTED mean lead time across its items
                  (only Shirts & Tops at 14.6d and Outerwear at 21.9d differ
                  meaningfully from 18d; see the printed table)

Reading the result
-------------------
A larger gap forecasts further ahead using less training data, so error is
expected to be a little WORSE, not better - that is the honest cost of taking
lead time into account, not a bug. `mase_ratio_gap_over_0` > 1 means exactly
that. The question this answers is how much worse, and whether the "beats
naive"/"MASE < 1" claims still hold once the comparison is fair to itself (the
naive benchmark is re-scored with the same gap, on the same folds).

Outputs: data/lead_time_gap_test.csv (one row per item/category x gap).
Run:  python scripts/test_lead_time_gap.py
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
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.topdown import topdown_tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
RESIDUE = "Uncategorised"
NAIVE_ITEM = rolling_mean_fit_predict(30)          # what step4 itself scores against
NAIVE_CATEGORY = rolling_mean_fit_predict(30)      # step4c's "repeat last 30 days"


def load():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, forecast_category, lead_time_days FROM Dim_Product", con)
    fact = pd.read_sql_query(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
           JOIN Dim_Date d ON d.date_id = f.date_id""", con, parse_dates=["calendar_date"])
    con.close()
    last_sale = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last_sale, freq="D")
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    prod["forecast_category"] = prod["forecast_category"].fillna(RESIDUE)
    return prod, daily, idx


def score(v, model, naive, gap):
    """(model MAE, model MASE, naive MAE, n folds) over one series, at one gap.
    MASE denominator: in-sample naive error on 30-day blocks of each fold's
    OWN training slice - same rule step4/step4c use - so a fold that lost
    training days to the gap is scaled by what THAT fold actually knew."""
    folds = make_folds(v.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN, gap=gap)
    if not folds:
        return None
    ev_m = walk_forward_evaluate("s", v, model, "model", folds=folds)
    ev_n = walk_forward_evaluate("s", v, naive, "naive", folds=folds)
    act = np.array([r["actual_30d"] for r in ev_m.rows])
    pm = np.array([r["pred_30d"] for r in ev_m.rows])
    pn = np.array([r["pred_30d"] for r in ev_n.rows])
    sc = np.array([r["naive_scale"] for r in ev_m.rows])
    good = np.isfinite(sc) & (sc > 0)
    mae = float(np.mean(np.abs(act - pm)))
    mase = float(mae / np.mean(sc[good])) if good.any() else None
    naive_mae = float(np.mean(np.abs(act - pn)))
    return dict(mae=mae, mase=mase, naive_mae=naive_mae, beats_naive=mae < naive_mae,
                n_folds=len(folds), mean_actual=float(act.mean()))


def item_rows(prod, daily, idx):
    cat_of = prod.set_index("product_id")["forecast_category"]
    fast = prod[prod.fsn_class == "F"].set_index("product_id")
    cats = {c: daily[[p for p in daily.columns if cat_of.get(p) == c]].sum(axis=1).to_numpy()
            for c in sorted({cat_of[p] for p in fast.index})}
    rows = []
    for pid, row in fast.iterrows():
        v = daily[pid].to_numpy(float)
        model = topdown_tsb_fit_predict(cats[row["forecast_category"]])
        for gap, label in ((0, "current (gap=0)"), (int(row["lead_time_days"]), "with lead-time gap")):
            r = score(v, model, NAIVE_ITEM, gap)
            if r:
                rows.append(dict(level="item", name=row["item_name"], scope=row["forecast_category"],
                                 gap=gap, gap_label=label, **r))
        print("  %-40s lead_time=%2dd" % (row["item_name"][:40], row["lead_time_days"]), flush=True)
    return rows


def category_rows(prod, daily, idx):
    cat_of = prod.set_index("product_id")["forecast_category"]
    fact_cat = daily.T.groupby(cat_of.reindex(daily.columns)).sum().T
    fact_cat = fact_cat.loc[:, fact_cat.sum() > 0]
    total = fact_cat.sum(axis=1)
    fact_cat = fact_cat.loc[:total[total > 0].index.max()]
    # Sales-weighted mean lead time per category (Fast + Slow + Non-moving alike,
    # matching step4c's "every item in the category" coverage), rounded to the
    # nearest day for a fold count that is an integer number of days.
    sold = daily.sum(axis=0)
    w = prod.set_index("product_id").loc[sold.index[sold.index.isin(prod.product_id)]]
    lt = (sold.reindex(w.index) * w["lead_time_days"]).groupby(w["forecast_category"]).sum() \
        / sold.reindex(w.index).groupby(w["forecast_category"]).sum()
    model = rolling_mean_fit_predict(180)
    rows = []
    for cat in fact_cat.columns:
        v = fact_cat[cat].to_numpy(float)
        gap = int(round(lt.get(cat, 18.0)))
        for g, label in ((0, "current (gap=0)"), (gap, "with lead-time gap")):
            r = score(v, model, NAIVE_CATEGORY, g)
            if r:
                rows.append(dict(level="category", name=cat, scope=cat, gap=g, gap_label=label, **r))
        print("  %-22s weighted lead_time=%.1fd" % (cat, lt.get(cat, float("nan"))), flush=True)
    return rows


def summarise(df, level):
    g = df[df.level == level]
    out = []
    for label, sub in g.groupby("gap_label", sort=False):
        out.append(dict(
            level=level, gap_label=label,
            n=len(sub), mean_MASE=sub.mase.mean(), median_MASE=sub.mase.median(),
            n_below_1=int((sub.mase < 1).sum()),
            n_beats_naive=int(sub.beats_naive.sum()),
            pooled_WMAPE_pct=100 * (sub.mae * sub.n_folds).sum() / (sub.mean_actual * sub.n_folds).sum(),
        ))
    res = pd.DataFrame(out).set_index("gap_label")
    res["mase_ratio_gap_over_0"] = res["mean_MASE"] / res.loc["current (gap=0)", "mean_MASE"]
    return res.reset_index()


def main():
    prod, daily, idx = load()
    print("Item level: 58 Fast items, gap = each item's own lead_time_days")
    irows = item_rows(prod, daily, idx)
    print("\nCategory level: gap = each category's sales-weighted mean lead_time_days")
    crows = category_rows(prod, daily, idx)

    df = pd.DataFrame(irows + crows)
    df.to_csv(os.path.join(DATA, "lead_time_gap_test.csv"), index=False, lineterminator="\n",
              float_format="%.4f")

    pd.set_option("display.width", 200)
    for level in ("item", "category"):
        print("\n=== %s level ===" % level)
        print(summarise(df, level).round(3).to_string(index=False))
    print("\nWrote data/lead_time_gap_test.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
