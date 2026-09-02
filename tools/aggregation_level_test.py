"""
tools/aggregation_level_test.py
------------------------------------------------------------------
Reproduces the "does forecasting at a coarser level fix MAPE" experiment
in docs/SPARSE_DEMAND_EXPERIMENTS.md. Scores the SAME model
(rolling_mean_30, forecasting.baselines) with the SAME walk-forward harness
(forecasting.evaluate) at three different levels of aggregation:

    LEVEL 1  per-SKU              (266 series - what model_benchmark.py scores)
    LEVEL 2  per-category         (APPAREL / NON-APPAREL / MAIN STORAGE)
    LEVEL 3  whole store          (every moving SKU summed into one series)

Touches no table, writes nothing back - read-only against ustore.db.

Category coverage caveat
-------------------------
218 of 519 products have `category IS NULL` in Dim_Product and are excluded
from LEVEL 2 (not imputed - category is a controlled-vocabulary field).
LEVEL 2 therefore covers 301 of 519 products, not the full catalogue.
LEVEL 3 has no such gap: it sums every moving SKU regardless of category.

Run: python tools/aggregation_level_test.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import rolling_mean_fit_predict
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.metrics import mae, mape, rmse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ustore.db")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60


def build_series(sub, full_index):
    return (sub.groupby("calendar_date")["quantity_sold"].sum()
               .reindex(full_index, fill_value=0.0).astype(float).to_numpy())


def score_level(name, series_by_key, model_fn, method_name):
    rows = []
    for key, values in series_by_key.items():
        folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
        if not folds:
            continue
        ev = walk_forward_evaluate(key, values, model_fn, method_name, folds=folds)
        rows.extend(ev.rows)
    if not rows:
        print(f"{name}: no scoreable series")
        return

    df = pd.DataFrame(rows)
    per_mae, per_rmse, per_mape = [], [], []
    n_undef = n_total = 0
    for _, g in df.groupby("sku"):
        per_mae.append(mae(g["actual_30d"], g["pred_30d"]))
        per_rmse.append(rmse(g["actual_30d"], g["pred_30d"]))
        m = mape(g["actual_30d"], g["pred_30d"])
        n_undef += m.n_undefined
        n_total += m.n_total
        if not np.isnan(m.value):
            per_mape.append(m.value)

    print(f"\n=== {name} ({df['sku'].nunique()} series, {len(df)} folds) ===")
    print(f"  MAE  : {np.mean(per_mae):.3f}")
    print(f"  RMSE : {np.mean(per_rmse):.3f}")
    if per_mape:
        cov = 100 * (n_total - n_undef) / n_total
        print(f"  MAPE : {np.mean(per_mape):.1f}%  (computable on "
              f"{n_total - n_undef}/{n_total} folds = {cov:.0f}%)")
    else:
        print(f"  MAPE : undefined for every fold")


def main():
    con = sqlite3.connect(DB_PATH)
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])
    products = pd.read_sql_query("SELECT product_id, category FROM Dim_Product", con)
    con.close()

    moving = fact.groupby("product_id")["quantity_sold"].sum().pipe(lambda s: s[s > 0]).index
    fact = fact[fact["product_id"].isin(moving)]
    full_index = pd.date_range(fact["calendar_date"].min(), fact["calendar_date"].max(), freq="D")

    sku_series = {pid: build_series(g, full_index) for pid, g in fact.groupby("product_id")}

    fact_cat = fact.merge(products, on="product_id", how="left")
    cat_series = {cat: build_series(g, full_index)
                 for cat, g in fact_cat.dropna(subset=["category"]).groupby("category")}

    store_series = {"WHOLE_STORE": build_series(fact, full_index)}

    model = rolling_mean_fit_predict(30)
    score_level("LEVEL 1: per-SKU", sku_series, model, "rolling_mean_30")
    score_level("LEVEL 2: per-category (301/519 products with a category tagged)",
               cat_series, model, "rolling_mean_30")
    score_level("LEVEL 3: whole store", store_series, model, "rolling_mean_30")


if __name__ == "__main__":
    main()
