"""
scripts/test_calendar_adjustment_starts.py
------------------------------------------------------------------
Is the calendar check's effect real, or an accident of where the test
windows happen to fall? (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 4.3)

scripts/test_calendar_adjustment.py scores the shipped forecasts on 12
walk-forward windows that end at the last day of data. Where those windows
start decides which of them straddle a break or an enrollment rush, and the
result moves with it: with the data ending on 31 July the calendar check
looks harmful, at other alignments it helps.

This re-runs the same test 30 times, cutting 0, 1, ... 29 days off the end
of the data first, so the 12 windows start on 30 different days. Same
production code, same two versions as that script:

  base      the model without the calendar check
  capped    base x min(1, calendar factor)        <- shipped

For each alignment it reports pooled WMAPE (all units missed / all units
sold) per level, and whether the check helped. The average over the 30 is
the figure to quote; a single alignment is not.

Output: data/calendar_adjustment_starts.csv
        (level, days_cut, data_end, first_window, base_wmape, capped_wmape, helped)
Read by scripts/build_current_results_workbook.py (Calendar Check tab).

Run:  python scripts/test_calendar_adjustment_starts.py [--starts 30]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.topdown import topdown_tsb_fit_predict
import step4c_category_forecast as s4c
import test_calendar_adjustment as tca

OUT = os.path.join(ROOT, "data", "calendar_adjustment_starts.csv")


def two_versions(base_fn, types, cat_values=None):
    return {"base": base_fn,
            "capped": calendar_capped_fit_predict(base_fn, types, cat_values, cap=True)}


def pooled(rows, version):
    g = rows[rows.version == version]
    return 100 * (g.actual - g.pred).abs().sum() / g.actual.sum()


def category_scores(con, series, cut):
    s = series.iloc[:len(series) - cut]
    types = load_day_types(con, s.index, tca.H)
    rows = []
    for cat in s.columns:
        rows += tca.score_series(cat, s[cat].to_numpy(float),
                                 two_versions(rolling_mean_fit_predict(180), types), s.index)
    return pd.DataFrame(rows)


def item_scores(con, daily, cat_of, fast, cut):
    d = daily.iloc[:len(daily) - cut]
    cats = {c: d[[p for p in d.columns if cat_of.get(p) == c]].sum(axis=1).to_numpy()
            for c in cat_of.unique()}
    types = load_day_types(con, d.index, tca.H)
    rows = []
    for pid in fast:
        cv = cats[cat_of[pid]]
        rows += tca.score_series(pid, d[pid].to_numpy(float),
                                 two_versions(topdown_tsb_fit_predict(cv), types, cat_values=cv), d.index)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--starts", type=int, default=30)
    args = ap.parse_args()

    con = sqlite3.connect("file:%s?mode=ro" % tca.DB_PATH, uri=True)
    wide, _ = s4c.load_category_series(con)
    series, _, _ = s4c.trim_padding(wide)

    # The item series exactly as tca.item_rows builds it.
    prod = pd.read_sql_query("SELECT product_id, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query("""SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id""", con, parse_dates=["calendar_date"])
    last = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last, freq="D")
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    cat_of = prod.set_index("product_id")["forecast_category"].fillna(tca.RESIDUE)
    fast = list(prod[prod.fsn_class == "F"].product_id)

    out = []
    for cut in range(args.starts):
        for level, rows, end in (("category", category_scores(con, series, cut), series.index[-1 - cut]),
                                 ("item", item_scores(con, daily, cat_of, fast, cut), daily.index[-1 - cut])):
            base, capped = pooled(rows, "base"), pooled(rows, "capped")
            out.append(dict(level=level, days_cut=cut, data_end=end.date(),
                            first_window=min(rows.window_start), base_wmape=base, capped_wmape=capped,
                            helped=capped < base))
        print(f"  cut {cut:2d}: data to {out[-1]['data_end']}  "
              f"category {out[-2]['base_wmape']:.1f} -> {out[-2]['capped_wmape']:.1f}  "
              f"item {out[-1]['base_wmape']:.1f} -> {out[-1]['capped_wmape']:.1f}", flush=True)
    con.close()

    res = pd.DataFrame(out)
    res.to_csv(OUT, index=False, lineterminator="\n", float_format="%.4f")

    print(f"\n=== Average over {args.starts} start dates (pooled WMAPE, %) ===")
    for level, g in res.groupby("level"):
        print(f"  {level:8s} without check {g.base_wmape.mean():.1f}  with check {g.capped_wmape.mean():.1f}  "
              f"helped at {int(g.helped.sum())} of {len(g)}")
    print("Wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
