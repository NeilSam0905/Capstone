"""
scripts/load_synthetic_2023.py
------------------------------------------------------------------
Loads data/USTore_sales_2023_daily_synthetic.csv (2023-02..2023-08, written by
scripts/disaggregate_batch_sales_2023.py) into its OWN table,
Fact_Sales_Synthetic, so the forecast steps can train on it
(forecasting/synthetic_history.py).

It is never written to Fact_Sales: only the 2023 batch totals are real, the
daily split is modelled, so it must not reach Fast/Slow classification, reorder
math, the dashboard's history or any scored test window. Every row carries
is_synthetic = 1 and its source batch.

Matching 2023 sheet names to products, without touching the controlled
vocabulary (data/vocab_mapping_FINAL_v5.csv is read, never written):

  mapping        the name IS a raw_name in the vocabulary once case, repeated
                 spaces and spaces around '@' are ignored ("LANYARD @180" is
                 "Lanyard @180"); product and category from Dim_Product
  spelling       the 2023 sheet spells a catalogue item differently, same item
                 and price ("VB JERSEY W/O SIG. @1000" = "VB Jersey without
                 Sig @1000"); listed in SPELLING
  category_only  no single product, but the category is clear (a generic
                 "TOTE BAG", a price the catalogue does not carry); counts
                 toward the category forecast only; listed in CATEGORY_ONLY
  unmapped       neither; stored, never used for training

Runs after step1b (it needs Dim_Product.forecast_category) and before step4.
Re-running replaces the table.

Run:  python scripts/load_synthetic_2023.py
------------------------------------------------------------------
"""
import os
import re
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.synthetic_history import TABLE

DB_PATH = os.path.join(ROOT, "ustore.db")
SYN_CSV = os.path.join(ROOT, "data", "USTore_sales_2023_daily_synthetic.csv")
MAPPING_CSV = os.path.join(ROOT, "data", "vocab_mapping_FINAL_v5.csv")

SPELLING = {
    "VB JERSEY W/O SIG. @1000": "VB Jersey without Sig @1000",
    "VB JERSEY W/O SIG. @2000": "VB Jersey without Sig @2000",
    "VB JERSEY W SIG. @1800": "VB Jersey with Sig. @1800",
    "VB JERSEY W SIG. @2800": "VB Jersey with Sig. @2800",
}
CATEGORY_ONLY = {
    "Tote Bag White (200)": "Bags",
    "TOTE BAG": "Bags",
    "Tote Bag Black (230)": "Bags",
    "tote 230": "Bags",
    "Sling Bag 330": "Bags",
    "Laptop Bag 1450": "Bags",
    "Back pack 1750": "Bags",
    "Golf Umbrella": "Umbrellas & Gear",
    "GT 350": "Shirts & Tops",          # GT 375 is a shirt; supplier JYL ATHLETICA (sportswear)
    "Hoodies": "Outerwear",
    "jacket@1650": "Outerwear",
    "UST JUC Y/B": "Outerwear",         # supplier JUC: every JUC product in the catalogue is a jacket
    "LANYARD @175": "Lanyards & IDs",
}


def norm(name):
    s = re.sub(r"\s*@\s*", "@", str(name).strip().lower())
    return re.sub(r"\s+", " ", s)


def main():
    if not os.path.exists(SYN_CSV):
        raise SystemExit(f"{os.path.relpath(SYN_CSV, ROOT)} not found - run "
                         "scripts/disaggregate_batch_sales_2023.py first")
    syn = pd.read_csv(SYN_CSV)
    if not (syn["Is_Synthetic"] == 1).all():
        raise SystemExit("every row of the synthetic file must carry Is_Synthetic = 1")
    syn["Item"] = syn["Item"].str.strip()
    syn = syn.rename(columns={"Total Quantity": "qty"})

    con = sqlite3.connect(DB_PATH)
    products = pd.read_sql("SELECT product_id, item_name, forecast_category FROM Dim_Product", con)
    by_name = products.set_index("item_name")
    if products["forecast_category"].isna().all():
        raise SystemExit("Dim_Product.forecast_category is empty - run "
                         "scripts/step1b_categorize_products.py first")
    categories = set(products["forecast_category"].dropna())

    # Normalised vocabulary lookup; a key that normalises to two different
    # canonical names is ambiguous and not used.
    vm = pd.read_csv(MAPPING_CSV)
    vm["key"] = vm["raw_name"].map(norm)
    canon = vm.groupby("key")["canonical_item_name"].agg(lambda s: set(s.str.strip()))
    vocab = {k: next(iter(v)) for k, v in canon.items() if len(v) == 1}

    match = {}
    for item in syn["Item"].unique():
        target = vocab.get(norm(item)) or SPELLING.get(item)
        if target is not None and target in by_name.index:
            match[item] = ("mapping" if item not in SPELLING else "spelling",
                           int(by_name.at[target, "product_id"]),
                           by_name.at[target, "forecast_category"])
        elif item in CATEGORY_ONLY:
            match[item] = ("category_only", None, CATEGORY_ONLY[item])
        else:
            match[item] = ("unmapped", None, None)
    bad = {c for _, _, c in match.values() if c is not None} - categories
    if bad:
        raise SystemExit(f"category not in Dim_Product.forecast_category: {sorted(bad)}")

    dates = pd.read_sql("SELECT date_id, calendar_date FROM Dim_Date", con)
    syn = syn.merge(dates, left_on="Date", right_on="calendar_date", how="left")
    if syn["date_id"].isna().any():
        raise SystemExit("synthetic dates missing from Dim_Date: "
                         f"{sorted(syn.loc[syn['date_id'].isna(), 'Date'].unique())[:5]}")
    syn["match_type"] = syn["Item"].map(lambda i: match[i][0])
    syn["product_id"] = syn["Item"].map(lambda i: match[i][1])
    syn["forecast_category"] = syn["Item"].map(lambda i: match[i][2])

    con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    con.execute(f"""
        CREATE TABLE {TABLE} (
            synthetic_id      INTEGER PRIMARY KEY,
            date_id           INTEGER NOT NULL REFERENCES Dim_Date(date_id),
            calendar_date     TEXT    NOT NULL,
            raw_item_name     TEXT    NOT NULL,
            product_id        INTEGER REFERENCES Dim_Product(product_id),
            forecast_category TEXT,
            match_type        TEXT    NOT NULL
                CHECK (match_type IN ('mapping','spelling','category_only','unmapped')),
            quantity_sold     REAL    NOT NULL,
            supplier          TEXT,
            source_batch      TEXT,
            is_synthetic      INTEGER NOT NULL DEFAULT 1 CHECK (is_synthetic = 1)
        )""")
    rows = [(int(r.date_id), r.Date, r.Item,
             None if pd.isna(r.product_id) else int(r.product_id),
             r.forecast_category, r.match_type, float(r.qty), r.Supplier, r.Source_Batch)
            for r in syn.itertuples()]
    con.executemany(
        f"""INSERT INTO {TABLE} (date_id, calendar_date, raw_item_name, product_id,
               forecast_category, match_type, quantity_sold, supplier, source_batch)
            VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()

    # Loading must neither create nor drop units.
    stored = con.execute(f"SELECT SUM(quantity_sold), COUNT(*) FROM {TABLE}").fetchone()
    assert abs(stored[0] - syn["qty"].sum()) < 1e-6 and stored[1] == len(syn), \
        "unit or row total changed while loading"

    print(f"{TABLE}: {stored[1]} rows, {stored[0]:.0f} units, "
          f"{syn['Date'].min()} .. {syn['Date'].max()} (synthetic daily split of real 2023 batch totals)")
    summary = (syn.groupby("match_type")
                  .agg(names=("Item", "nunique"), units=("qty", "sum"))
                  .reindex(["mapping", "spelling", "category_only", "unmapped"]).dropna())
    print(summary.astype(int).to_string())
    unm = syn[syn["match_type"] == "unmapped"].groupby("Item")["qty"].sum()
    if len(unm):
        print("not used for training (no product or category): "
              + ", ".join(f"{k} ({v:.0f})" for k, v in unm.items()))
    con.close()


if __name__ == "__main__":
    main()
