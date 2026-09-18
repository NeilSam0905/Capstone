"""
Step 4b of ETL: forecast at CATEGORY level, then disaggregate back to SKU
so the prescriptive layer can still act.

Why the disaggregation step is not optional
-------------------------------------------
`step5_prescriptive.py` computes ROP, Safety Stock and EOQ PER SKU. A
category forecast cannot order a specific item - the store buys "UST OAT
MUG (B)", not "3,631 units of Drinkware". So a category model is only
usable if its total is allocated back to members.

The allocation is a trailing demand share over `SHARE_WINDOW` days:

    w_i   = units_i(origin-W .. origin) / sum_j units_j(origin-W .. origin)
    yhat_i = category_forecast x w_i

with an equal split as the fallback when a category sold nothing in the
window (otherwise the weights are 0/0).

Measured caveat, stated because it bounds what this buys
--------------------------------------------------------
docs/FAST_MOVING_BENCHMARK.md section 6.11 tested exactly this shape:
`topdown_tsb_share90` scored MASE 3.678 against per-SKU `tsb`'s 3.450 -
top-down was WORSE at SKU level. Aggregation makes the CATEGORY series
easier to forecast, and the allocation step hands the intermittency
straight back, because the share weights are themselves estimated from
the same sparse per-SKU history. Expect the category gain to show up in
category-level reporting and to be roughly a wash on per-SKU ordering.

Writes both levels into Result_Forecast, distinguished by
`forecast_level` ('category' rows carry the category total for reporting;
'sku' rows carry the disaggregated per-item figure that step5 consumes).

Run (from the repo root, after step1b_categorize_products.py):
    python scripts/step4b_category_forecast.py [--model rolling_mean_30]
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.baselines import ewma_fit_predict, rolling_mean_fit_predict
from forecasting.intermittent import tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
SERIES_CSV = "data/category_daily_series.csv"
SERIES_CSV_FAST = "data/category_daily_series_fast.csv"
HORIZON = 30
SHARE_WINDOW = 90

# RM6_6month_180d is the default because it WON the category benchmark
# (pooled WMAPE 49.3%). Note this inverts the SKU-level result, where the
# 30-day window won and 180 days was 4th-worst: an aggregated series is
# far smoother, so more averaging helps rather than hurts. Same callable,
# opposite conclusion, because the input changed.
MODELS = {
    "RM6_6month_180d": lambda: rolling_mean_fit_predict(180),
    "rolling_mean_30": lambda: rolling_mean_fit_predict(30),
    "rolling_mean_60": lambda: rolling_mean_fit_predict(60),
    "tsb": lambda: tsb_fit_predict(0.1, 0.1),
    "ewma_a0.1": lambda: ewma_fit_predict(0.1),
}


def ensure_schema(con):
    """Create step4b's own two tables. Result_Forecast is NOT touched.

    Both grains were briefly written into Result_Forecast, tagged by a
    `forecast_level` column. That was wrong, and the NOT NULL constraint
    on product_id was the first symptom rather than the problem: six
    readers aggregate that table with no level filter -
    step5_prescriptive.py::load_forecast_totals and four queries in
    backend/app.py all do bare SUM(yhat) - so a second grain in the same
    table silently inflates every reorder point and every dashboard
    total. Tagging rows only works if every reader is level-aware, and
    that is a property no schema can enforce.

    Separate tables make the mistake unrepresentable. It also stops
    step4_forecast_model.py's unconditional `DELETE FROM Result_Forecast`
    from wiping step4b's output, which it otherwise would on every run.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Forecast_Category (
            forecast_id       INTEGER PRIMARY KEY,
            forecast_category TEXT NOT NULL,
            forecast_date     TEXT NOT NULL,
            yhat              REAL,
            model_type        TEXT,
            snapshot_date     TEXT
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS Result_Forecast_Category_SKU (
            forecast_id       INTEGER PRIMARY KEY,
            product_id        INTEGER NOT NULL,
            forecast_category TEXT NOT NULL,
            forecast_date     TEXT NOT NULL,
            yhat              REAL,
            share             REAL,
            model_type        TEXT,
            snapshot_date     TEXT
        )""")

    # Undo the earlier tagged-column attempt so a DB that ran it matches
    # a DB built fresh from create_schema.py, which defines neither.
    cols = {r[1] for r in con.execute("PRAGMA table_info(Result_Forecast)")}
    removed = []
    if "forecast_level" in cols:
        con.execute("DELETE FROM Result_Forecast WHERE forecast_level <> 'sku'")
        con.execute("ALTER TABLE Result_Forecast DROP COLUMN forecast_level")
        removed.append("forecast_level")
    if "forecast_category" in cols:
        con.execute("ALTER TABLE Result_Forecast DROP COLUMN forecast_category")
        removed.append("forecast_category")
    con.commit()
    return removed


def sku_shares(con, index, cut):
    """Trailing-window demand share per SKU within its category.

    `cut` is the positional origin: only data STRICTLY BEFORE it is used,
    so the weights cannot see the period being forecast.
    """
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold,
               p.forecast_category, p.fsn_class
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'
    """, con, parse_dates=["calendar_date"])

    lo = index[max(cut - SHARE_WINDOW, 0)]
    hi = index[cut - 1]
    win = fact[(fact["calendar_date"] >= lo) & (fact["calendar_date"] <= hi)]

    units = (win.groupby(["forecast_category", "product_id"])["quantity_sold"]
                .sum().rename("units").reset_index())
    # SKUs with no sales in the window still need a row, or a category
    # forecast would be silently allocated to fewer items than it covers.
    allp = fact[["product_id", "forecast_category", "fsn_class"]].drop_duplicates()
    units = allp.merge(units, on=["forecast_category", "product_id"], how="left")
    units["units"] = units["units"].fillna(0.0)

    tot = units.groupby("forecast_category")["units"].transform("sum")
    n = units.groupby("forecast_category")["product_id"].transform("size")
    units["share"] = np.where(tot > 0, units["units"] / tot, 1.0 / n)
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="RM6_6month_180d", choices=sorted(MODELS))
    ap.add_argument("--fast-only", action="store_true",
                    help="allocate only to Fast SKUs (what step5 forecasts today)")
    args = ap.parse_args()

    # The series must be built from the same population the allocation
    # targets. Forecasting the all-SKU category total and then handing it
    # only to Fast members would inflate every Fast SKU by its category's
    # Slow and Non-moving demand - in Outerwear that is 89% of the units.
    src = SERIES_CSV_FAST if args.fast_only else SERIES_CSV
    series_df = pd.read_csv(src, index_col=0, parse_dates=True)
    index = series_df.index
    model = MODELS[args.model]()

    con = sqlite3.connect(DB_PATH)
    removed = ensure_schema(con)
    shares = sku_shares(con, index, len(index))

    if args.fast_only:
        shares = shares[shares["fsn_class"] == "F"].copy()
        tot = shares.groupby("forecast_category")["units"].transform("sum")
        n = shares.groupby("forecast_category")["product_id"].transform("size")
        shares["share"] = np.where(tot > 0, shares["units"] / tot, 1.0 / n)

    snapshot = index[-1].strftime("%Y-%m-%d")
    dates = pd.date_range(index[-1] + pd.Timedelta(days=1), periods=HORIZON, freq="D")

    cat_rows, sku_rows = [], []
    for cat in series_df.columns:
        total = float(np.sum(model(series_df[cat].to_numpy(float), HORIZON)))
        daily = total / HORIZON
        for d in dates:
            cat_rows.append(dict(forecast_category=cat,
                                 forecast_date=d.strftime("%Y-%m-%d"),
                                 yhat=round(daily, 6), model_type=args.model,
                                 snapshot_date=snapshot))
        members = shares[shares["forecast_category"] == cat]
        for r in members.itertuples():
            per_day = daily * r.share
            for d in dates:
                sku_rows.append(dict(product_id=int(r.product_id),
                                     forecast_category=cat,
                                     forecast_date=d.strftime("%Y-%m-%d"),
                                     yhat=round(per_day, 6),
                                     share=round(float(r.share), 6),
                                     model_type=f"{args.model}_topdown",
                                     snapshot_date=snapshot))

    cats_df = pd.DataFrame(cat_rows)
    skus_df = pd.DataFrame(sku_rows)

    # Clear + refill in ONE transaction, matching step4's convention: on
    # failure SQLite rolls back to the previous run rather than to nothing.
    con.execute("DELETE FROM Result_Forecast_Category")
    con.executemany(
        """INSERT INTO Result_Forecast_Category
           (forecast_category, forecast_date, yhat, model_type, snapshot_date)
           VALUES (:forecast_category,:forecast_date,:yhat,:model_type,
                   :snapshot_date)""",
        cats_df.to_dict("records"))
    con.execute("DELETE FROM Result_Forecast_Category_SKU")
    con.executemany(
        """INSERT INTO Result_Forecast_Category_SKU
           (product_id, forecast_category, forecast_date, yhat, share,
            model_type, snapshot_date)
           VALUES (:product_id,:forecast_category,:forecast_date,:yhat,:share,
                   :model_type,:snapshot_date)""",
        skus_df.to_dict("records"))
    con.commit()
    con.close()

    if removed:
        print(f"Result_Forecast: dropped stray columns {removed} "
              f"(step4b no longer writes to that table)")
    cats, skus = cats_df, skus_df
    print(f"Model: {args.model} | snapshot {snapshot} | horizon {HORIZON}d")
    print(f"  series source: {src}")
    print(f"  category rows: {len(cats):,} ({cats['forecast_category'].nunique()} categories)")
    print(f"  sku rows     : {len(skus):,} ({skus['product_id'].nunique()} SKUs"
          f"{', Fast only' if args.fast_only else ''})")
    tot_cat = cats.groupby("forecast_category")["yhat"].sum()
    tot_sku = skus.groupby("forecast_category")["yhat"].sum()
    chk = pd.DataFrame({"category_30d": tot_cat, "sku_sum_30d": tot_sku}).fillna(0)
    chk["diff"] = (chk["category_30d"] - chk["sku_sum_30d"]).abs()
    print("\n=== allocation conservation (category total vs sum of its SKUs) ===")
    print(chk.round(3).to_string())
    assert chk["diff"].max() < 1e-3, "allocation did not conserve the category total"
    print("\n[PASS] allocation conserves every category total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
