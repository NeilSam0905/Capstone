"""
scripts/test_calendar_adjustment.py
------------------------------------------------------------------
Does lowering the 30-day total when the school calendar shows quieter days
ahead (forecasting/calendar_adjust.py) improve the shipped forecasts?

Scores three versions of each shipped model on the project's walk-forward
harness (30-day horizon, up to 12 folds, MASE scaled as step4/step4c store it),
using the PRODUCTION code, so these are the numbers the pipeline reproduces:

  base      the model without the adjustment
              category: 6-month average (RM6_6month_180d)
              item:     50/50 category share + TSB (forecasting.topdown)
  uncapped  base x calendar factor, allowed to go up or down
  capped    base x min(1, calendar factor)        <- shipped

Day types come from the PUBLISHED calendar only (enrollment, semester break,
exam week). Store closures are left out on purpose: some were recorded after
the fact (typhoons), which a real forecast could not know. Including them
changed the result by less than 0.01 MASE when this was first explored.

Honesty notes
-------------
- The "capped" rule was chosen AFTER seeing the uncapped version go badly wrong
  in the months right after a break (Feb and Apr 2026). The robustness columns
  below are still worth reading, but the gain is likely a little optimistic.
- It under-forecasts more on average; the per-window file shows where.

Outputs (data/):
  calendar_adjustment_test.csv       level x version x fold-half: MASE, WMAPE, bias,
                                     plus the bootstrap gap of capped vs base
  calendar_adjustment_by_window.csv  level x 30-day window: error of each version

Run:  python scripts/test_calendar_adjustment.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.topdown import topdown_tsb_fit_predict
import step4c_category_forecast as s4c

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
H, MIN_F, MAX_F, MIN_T = 30, 3, 12, 60
RESIDUE = "Uncategorised"
VERSIONS = ("base", "uncapped", "capped")
N_BOOT, SEED = 4000, 11


def versions(base_fn, types, cat_values=None):
    return {"base": base_fn,
            "uncapped": calendar_capped_fit_predict(base_fn, types, cat_values, cap=False),
            "capped": calendar_capped_fit_predict(base_fn, types, cat_values, cap=True)}


def score_series(key, v, fns, idx):
    folds = make_folds(v.size, H, MIN_F, MAX_F, MIN_T)
    rows = []
    for name, fn in fns.items():
        for r in walk_forward_evaluate(key, v, fn, name, folds=folds).rows:
            rows.append(dict(key=key, version=name, fold=r["fold"], actual=r["actual_30d"],
                             pred=r["pred_30d"], scale=r["naive_scale"],
                             window_start=idx[r["origin"]].date()))
    return rows


def category_rows(con):
    wide, _ = s4c.load_category_series(con)
    series, _, _ = s4c.trim_padding(wide)
    types = load_day_types(con, series.index, H)
    rows = []
    for cat in series.columns:
        v = series[cat].to_numpy(float)
        rows += score_series(cat, v, versions(rolling_mean_fit_predict(180), types), series.index)
    return rows


def item_rows(con):
    prod = pd.read_sql_query("SELECT product_id, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query("""SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id""", con, parse_dates=["calendar_date"])
    last = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last, freq="D")
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    cat_of = prod.set_index("product_id")["forecast_category"].fillna(RESIDUE)
    cats = {c: daily[[p for p in daily.columns if cat_of.get(p) == c]].sum(axis=1).to_numpy()
            for c in cat_of.unique()}
    types = load_day_types(con, idx, H)
    rows = []
    for pid in prod[prod.fsn_class == "F"].product_id:
        cv = cats[cat_of[pid]]
        rows += score_series(pid, daily[pid].to_numpy(float),
                             versions(topdown_tsb_fit_predict(cv), types, cat_values=cv), idx)
    return rows


def per_key_mase(df, version):
    out = {}
    for key, g in df[df.version == version].groupby("key"):
        sc = g.scale[np.isfinite(g.scale) & (g.scale > 0)]
        if len(sc):
            out[key] = np.abs(g.actual - g.pred).mean() / sc.mean()
    return pd.Series(out)


def summarise(df, level):
    nf = df.fold.max() + 1
    rows = []
    for half, sub in (("all folds", df), ("older folds", df[df.fold < nf // 2]),
                      ("newer folds", df[df.fold >= nf // 2])):
        for ver in VERSIONS:
            g = sub[sub.version == ver]
            m = per_key_mase(sub, ver)
            rows.append(dict(level=level, folds=half, version=ver, mean_MASE=m.mean(),
                             n_below_1=int((m < 1).sum()), n=len(m),
                             pooled_WMAPE_pct=100 * (g.actual - g.pred).abs().sum() / g.actual.sum(),
                             bias_pct=100 * (g.pred.sum() - g.actual.sum()) / g.actual.sum()))
    res = pd.DataFrame(rows)
    d = (per_key_mase(df, "capped") - per_key_mase(df, "base")).dropna().to_numpy()
    b = np.random.default_rng(SEED).integers(0, d.size, size=(N_BOOT, d.size))
    means = d[b].mean(axis=1)
    res.loc[(res.folds == "all folds") & (res.version == "capped"), "gap_vs_base"] = d.mean()
    res.loc[(res.folds == "all folds") & (res.version == "capped"), "gap_ci_lo"] = np.percentile(means, 2.5)
    res.loc[(res.folds == "all folds") & (res.version == "capped"), "gap_ci_hi"] = np.percentile(means, 97.5)
    res.loc[(res.folds == "all folds") & (res.version == "capped"), "n_better"] = int((d < 0).sum())
    return res


def by_window(df, level):
    out = []
    for (fold, start), g in df.groupby(["fold", "window_start"]):
        r = dict(level=level, fold=fold, window_start=start)
        for ver in VERSIONS:
            gv = g[g.version == ver]
            r[ver + "_error_pct"] = 100 * (gv.actual - gv.pred).abs().sum() / gv.actual.sum()
            r[ver + "_bias_pct"] = 100 * (gv.pred.sum() - gv.actual.sum()) / gv.actual.sum()
        out.append(r)
    return pd.DataFrame(out)


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    cat = pd.DataFrame(category_rows(con))
    item = pd.DataFrame(item_rows(con))
    con.close()
    res = pd.concat([summarise(cat, "category"), summarise(item, "item")], ignore_index=True)
    win = pd.concat([by_window(cat, "category"), by_window(item, "item")], ignore_index=True)
    res.to_csv(os.path.join(DATA, "calendar_adjustment_test.csv"), index=False,
               lineterminator="\n", float_format="%.4f")
    win.to_csv(os.path.join(DATA, "calendar_adjustment_by_window.csv"), index=False,
               lineterminator="\n", float_format="%.2f")
    pd.set_option("display.width", 220)
    print(res.round(3).to_string(index=False))
    print("\n" + win.round(1).to_string(index=False))
    print("\nWrote data/calendar_adjustment_test.csv and data/calendar_adjustment_by_window.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
