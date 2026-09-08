"""
tools/aggregation_density_test.py
------------------------------------------------------------------
Option C: forecast at coarser and coarser levels, where zero-demand days
get rarer, and measure accuracy against DEMAND DENSITY on the same scale
tools/density_vs_accuracy.py uses for individual SKUs. Together the two
put real items and real aggregates on one axis: "how dense does a series
have to be before these methods work?"

Extends tools/aggregation_level_test.py (which scored one model at three
levels and reported MAPE) by
  - adding the category+speed grouping this project now uses,
  - scoring four methods rather than one,
  - reporting MASE/RMSSE, not just MAE/RMSE/MAPE, and
  - reporting each level's demand density, which is the explanatory
    variable the whole question turns on.

Uses only real Fact_Sales data - nothing synthetic. Read-only.

Run: python tools/aggregation_density_test.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import model_benchmark as mb
from forecasting.baselines import naive_fit_predict, rolling_mean_fit_predict
from forecasting.category import classify, speed_label
from forecasting.evaluate import (
    aggregate_blocks, make_folds, walk_forward_evaluate,
)
from forecasting.hurdle import weekly_hurdle_fit_predict
from forecasting.intermittent import tsb_fit_predict
from forecasting.metrics import mape

OUT_CSV = "data/aggregation_density.csv"

METHODS = {
    "naive": naive_fit_predict(),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "tsb": tsb_fit_predict(0.1, 0.1),
    "weekly_hurdle_12w": weekly_hurdle_fit_predict(12),
}


def score_level(series_by_key, folds_of):
    """Per (method, series) rows, then per-method robust aggregates."""
    out = []
    for name, fn in METHODS.items():
        per_series = []
        for key, values in series_by_key.items():
            folds = folds_of(values.size)
            if not folds:
                continue
            ev = walk_forward_evaluate(key, values, fn, name, folds=folds)
            if not ev.rows:
                continue
            g = pd.DataFrame(ev.rows)
            err = (g["actual_30d"] - g["pred_30d"]).to_numpy(dtype=float)

            scales_abs, scales_sq = [], []
            for f in folds:
                blocks = aggregate_blocks(values[:f.train_end], f.horizon)
                if blocks.size > 1:
                    d = np.diff(blocks)
                    scales_abs.append(np.mean(np.abs(d)))
                    scales_sq.append(np.mean(d ** 2))
            a_s = float(np.mean(scales_abs)) if scales_abs else np.nan
            s_s = float(np.mean(scales_sq)) if scales_sq else np.nan

            m = mape(g["actual_30d"], g["pred_30d"])
            per_series.append({
                "mae": float(np.mean(np.abs(err))),
                "mse": float(np.mean(err ** 2)),
                "abs_scale": a_s, "sq_scale": s_s,
                "mape": m.value,
            })

        ps = pd.DataFrame(per_series)
        ok = ps["abs_scale"].notna() & (ps["abs_scale"] > 0)
        mase = (ps.loc[ok, "mae"] / ps.loc[ok, "abs_scale"])
        ok_sq = ps["sq_scale"].notna() & (ps["sq_scale"] > 0)
        rmsse = np.sqrt(ps.loc[ok_sq, "mse"] / ps.loc[ok_sq, "sq_scale"])

        out.append({
            "method": name,
            "n_series": len(ps),
            "mae": ps["mae"].mean(),
            "mase_median": mase.median() if len(mase) else np.nan,
            "mase_global": (ps.loc[ok, "mae"].sum() / ps.loc[ok, "abs_scale"].sum()
                            if ok.any() else np.nan),
            "rmsse_median": rmsse.median() if len(rmsse) else np.nan,
            "mape": ps["mape"].mean(skipna=True),
        })
    return out


def main():
    con = sqlite3.connect(mb.DB_NAME)
    series, _, index = mb.load_daily_series(con)
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, category, fsn_class FROM Dim_Product", con)
    con.close()

    prod = prod[prod["product_id"].isin(series)]
    cat = {r.product_id: classify(r.item_name, r.category) for r in prod.itertuples()}
    catspeed = {r.product_id: f"{classify(r.item_name, r.category)}-"
                              f"{speed_label(r.fsn_class)}" for r in prod.itertuples()}

    def summed(mapping):
        acc = {}
        for sku, values in series.items():
            k = mapping.get(sku)
            if k is None:
                continue
            acc[k] = acc.get(k, 0) + np.asarray(values, dtype=float)
        return acc

    levels = {
        "1. per SKU": series,
        "2. per category+speed (4)": summed(catspeed),
        "3. per category (2)": summed(cat),
        "4. whole store (1)": {"ALL": np.sum(list(series.values()), axis=0)},
    }

    def folds_of(n):
        return make_folds(n, mb.HORIZON, mb.MIN_FOLDS, mb.MAX_FOLDS, mb.MIN_TRAIN)

    rows = []
    for level, sbk in levels.items():
        density = 100 * float(np.mean([np.mean(np.asarray(v) > 0) for v in sbk.values()]))
        print(f"{level}: {len(sbk)} series, demand density "
              f"{density:.1f}% of days have a sale")
        for r in score_level(sbk, folds_of):
            r.update(level=level, density_pct=density)
            rows.append(r)

    out = pd.DataFrame(rows)[["level", "density_pct", "method", "n_series",
                              "mae", "mase_median", "mase_global",
                              "rmsse_median", "mape"]]
    out.to_csv(OUT_CSV, index=False, lineterminator="\n")

    print("\n" + "=" * 104)
    print("OPTION C - ACCURACY AS SERIES GET DENSER (real data, aggregated)")
    print("=" * 104)
    print(out.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\nWrote {OUT_CSV}")


if __name__ == "__main__":
    main()
