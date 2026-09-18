"""
Step 1b of ETL: derive a forecast-level product category, then aggregate
Fact_Sales to one daily series per category.

Why this exists
---------------
Forecasting per SKU on this catalogue is close to its arithmetic ceiling:
`docs/FAST_MOVING_BENCHMARK.md` shows 37 methods across 10 families all
landing within 10% of each other, and the best MAE (40.02) sits 2% above
the floor for any flat forecast. Aggregating to category is the one lever
measured to move the number materially - pooled WMAPE 74.8% -> 57.7% on
the same folds (section 6.14's aggregation ladder).

Why a NEW column instead of overwriting Dim_Product.category
------------------------------------------------------------
`category` is already load-bearing and already owned:

  step1_apply_mapping.py  WRITES it from the inventory workbook, so
                          anything written here is wiped on the next run
  step5a_set_lead_times.py  READS it to pick a garment lead-time tier
  step5_prescriptive.py     READS it for the same classification

Overwriting it would silently change lead times and then be reverted by
the next ETL pass. So this script adds `forecast_category` and leaves
`category` untouched. The two answer different questions: `category` is
"what the inventory sheet called this", `forecast_category` is "which
series does this SKU's demand belong to".

The keyword rules
-----------------
`Dim_Product.category` is NULL for 218 of 519 products and for 48 of the
58 Fast SKUs, and its populated values are three coarse buckets
(APPAREL / NON-APPAREL / MAIN STORAGE). Neither is usable as a
forecasting grain, so the bucket is derived from the canonical item name.

NO SIZE-TOKEN RULE. An earlier draft matched garment sizes (XL, L, ...)
to route items into Apparel. It is deliberately absent: a bare "L" or "S"
matches Lanyard, Logo, Clock, Sticker and Socks, and even an anchored
\\b(xl|2xl)\\b only fires on the handful of names that spell a size range
out - it buys almost nothing and risks mislabelling by substring. Garment
items are caught by their noun (shirt, polo, hoodie) instead, which is
both safer and more complete.

Order matters: the first matching rule wins, so the specific bucket is
listed before the general one that would also match. 'UST OAT MUG' must
reach Drinkware before any apparel rule, and 'Tiger Claw Keychain' must
reach Keychains before Plush.

Run (from the repo root, after step1_apply_mapping.py):
    python scripts/step1b_categorize_products.py
"""
import argparse
import os
import re
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ustore.db")
OUT_SERIES = "data/category_daily_series.csv"
OUT_SERIES_FAST = "data/category_daily_series_fast.csv"
OUT_AUDIT = "data/category_assignment_audit.csv"

RESIDUE = "Uncategorised"

# `\w*shirts?\b` rather than `\bshirts?\b`: the catalogue spells several
# garments as one word - "Poloshirt @800", "UST Sweatshirt Sporty" - and a
# leading \b refuses to match inside them. The suffix form catches
# T-Shirt, Poloshirt and Sweatshirt alike. "embro" (embroidered) is a
# garment marker throughout these tally sheets, never a non-apparel one.
CATEGORY_RULES = [
    ("Outerwear",          r"(\b(hoodies?|jackets?|windbreakers?|sweaters?|pullovers?|bombers?)\b"
                           r"|\bsweat\s*shirts?\b|\bsweatshirts?\b)"),
    ("Shirts & Tops",      r"(\w*t-?shirts?\b|\w*shirts?\b|\bpolos?\b|\bpolo\s*shirts?\b"
                           r"|\bjerseys?\b|\btees?\b|\bsubli\w*|\boversized\b"
                           r"|\bcrew\s*neck\b|\buniforms?\b|\bdri-?\s*fit\b|\bembro\w*)"),
    ("Bags",               r"(\btote\s*bags?\b|\btotebags?\b|\btotes?\b|\beco\s*bags?\b"
                           r"|\bbags?\b|\bpouch\w*|\bsling\b|\bbackpacks?\b)"),
    ("Drinkware",          r"(\b(mugs?|tumblers?|bottles?|sippers?|jugs?)\b|\w*flasks?\b)"),
    ("Lanyards & IDs",     r"(\blanyards?\b|\blaces?\b|\bid\s*cases?\b|\bid\s*holders?\b"
                           r"|\bribbons?\b)"),
    ("Keychains & Charms", r"(\bkey\s*chains?\b|\bkeychains?\b|\bcharms?\b|\bclickers?\b"
                           r"|\bfidgets?\b|\bpins?\b)"),
    ("Stationery",         r"(\bball\s*pens?\b|\bpens?\b|\bpencils?\b|\bnotebooks?\b|\bnb\b"
                           r"|\bnotelets?\b|\bstickers?\b|\bplanners?\b|\bbookmarks?\b"
                           r"|\bfolders?\b|\bpapers?\b|\berasers?\b|\brulers?\b)"),
    ("Plush & Souvenirs",  r"(\bplushi?e?s?\b|\bplush\b|\bstuff(ed)?\s*toys?\b|\btoys?\b"
                           r"|\bclappers?\b|\barch\b|\bfigurines?\b|\btokens?\b"
                           r"|\bw/?\s*box\b|\btiger\s*w\b)"),
    ("Apparel Accessories", r"(\bscarf\b|\bscarves\b|\bsash\b|\bhead\s*bands?\b"
                            r"|\bwrist\s*bands?\b|\bbracelets?\b|\bgloves?\b)"),
    ("Umbrellas & Gear",   r"(\bumbrellas?\b|\bumb\b|\bcanopy\b|\bcaps?\b|\bhats?\b"
                           r"|\bsocks?\b|\btowels?\b|\bfans?\b|\bshoes?\b)"),
    ("Home & Novelty",     r"(\bclocks?\b|\butensils?\b|\bmouse\s*pads?\b|\blamps?\b"
                           r"|\bframes?\b|\bmagnets?\b|\bcoasters?\b|\bpower\s*banks?\b)"),
]


def categorise(name) -> str:
    """First matching rule wins; anything unmatched falls to RESIDUE.

    Falling through rather than guessing is deliberate: a residue bucket
    you can count and inspect is safer than a greedy catch-all regex that
    silently swallows exactly the items worth looking at by hand. Step 2
    of the plan audits whatever lands here.
    """
    n = str(name).lower()
    for label, pattern in CATEGORY_RULES:
        if re.search(pattern, n):
            return label
    return RESIDUE


def ensure_column(con):
    """Add forecast_category if absent. Idempotent - safe to re-run."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    if "forecast_category" not in cols:
        con.execute("ALTER TABLE Dim_Product ADD COLUMN forecast_category TEXT")
        con.commit()
        return True
    return False


def assign_categories(con):
    products = pd.read_sql_query(
        "SELECT product_id, item_name, category AS legacy_category, fsn_class "
        "FROM Dim_Product", con)
    products["forecast_category"] = products["item_name"].map(categorise)

    # Write BOTH columns. `category` is what backend/catalog.py reads and
    # therefore what the dashboard filters on; `forecast_category` is what
    # every model here groups by. They were separate values - storage
    # groupings (APPAREL / NON-APPAREL / MAIN STORAGE) against semantic ones -
    # which meant filtering the dashboard by a category no forecast had ever
    # been grouped by. scripts/migrate_category_to_forecast.py aligned them
    # once and preserved the old values in `storage_category`; writing both
    # here is what stops a later pipeline run silently undoing that.
    cols = {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    if "storage_category" not in cols:
        con.execute("ALTER TABLE Dim_Product ADD COLUMN storage_category TEXT")
        con.execute("UPDATE Dim_Product SET storage_category = category")

    con.executemany(
        "UPDATE Dim_Product SET forecast_category = ?, category = ? "
        "WHERE product_id = ?",
        [(fc, fc, pid) for fc, pid in
         zip(products["forecast_category"], products["product_id"])])
    con.commit()
    return products


def category_daily_series(con, products, fsn=None):
    """One daily series per forecast_category, over the complete calendar.

    Reindexing onto the FULL date range is what makes a 30-day window a
    CALENDAR window - without it a category with no rows on some date
    would silently shorten its own history. Same convention as
    step4_forecast_model.py::build_series and model_benchmark.py.

    transaction_type is filtered to 'sale' so returns, damages and
    internal transfers cannot inflate demand (Table 4's Data Integrity
    risk).

    `fsn` restricts the series to one FSN class. That is not a cosmetic
    filter: step4b allocates a category total back to its members, and if
    the total was built from ALL SKUs while only the Fast ones receive an
    allocation, the Fast SKUs absorb the Slow and Non-moving members'
    demand as well. The forecast input has to be built from the same
    population the allocation targets, so a Fast-only run forecasts a
    Fast-only series. The two files are written side by side rather than
    one being derived from the other, because a category's Fast subtotal
    is not recoverable from its all-SKU total.
    """
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold, p.fsn_class
        FROM Fact_Sales f
        JOIN Dim_Date d    ON d.date_id = f.date_id
        JOIN Dim_Product p ON p.product_id = f.product_id
        WHERE f.transaction_type = 'sale'
    """, con, parse_dates=["calendar_date"])

    fact = fact.merge(products[["product_id", "forecast_category"]],
                      on="product_id", how="left")
    fact["forecast_category"] = fact["forecast_category"].fillna(RESIDUE)

    # The calendar is taken from the FULL fact table before any FSN
    # filter, so both files share one index. A Fast-only span would
    # otherwise start and end on different days than the all-SKU span and
    # the two could not be compared fold-for-fold.
    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")

    subset = fact if fsn is None else fact[fact["fsn_class"] == fsn]

    wide = (subset.groupby(["forecast_category", "calendar_date"])["quantity_sold"]
                  .sum().unstack(0)
                  .reindex(idx, fill_value=0.0)
                  .fillna(0.0).astype(float))
    wide.index.name = "calendar_date"

    # Conservation gate. Aggregation must not create or destroy units; if
    # it does, a join dropped rows and every downstream number is wrong.
    assert abs(wide.to_numpy().sum() - subset["quantity_sold"].sum()) < 1e-6, \
        "unit total changed during aggregation - a join dropped rows"

    # Drop categories with no demand in this population. Keeping an
    # all-zero column would hand step4b a category to forecast that has
    # no members to allocate to.
    return wide.loc[:, wide.sum() > 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    added = ensure_column(con)
    products = assign_categories(con)
    series = category_daily_series(con, products)
    series_fast = category_daily_series(con, products, fsn="F")
    con.close()

    print(f"Dim_Product.forecast_category: "
          f"{'added' if added else 'already present'}, "
          f"{len(products)} products assigned\n")

    print("=== forecast_category x legacy Dim_Product.category ===")
    print(pd.crosstab(products["forecast_category"],
                      products["legacy_category"].fillna("(NULL)")).to_string())

    fast = products[products["fsn_class"] == "F"]
    print(f"\n=== Fast segment ({len(fast)} SKUs) ===")
    print(fast["forecast_category"].value_counts().to_string())

    res = products[products["forecast_category"] == RESIDUE]
    print(f"\n=== {RESIDUE}: {len(res)} of {len(products)} products "
          f"({100 * len(res) / len(products):.1f}%) ===")
    print(f"  of which Fast: {int((res['fsn_class'] == 'F').sum())}")

    print(f"\n=== category daily series: {series.shape[1]} categories x "
          f"{len(series)} days ===")
    tot = series.sum().sort_values(ascending=False)
    print(pd.DataFrame({"units": tot,
                        "pct_of_total": (100 * tot / tot.sum()).round(1)}).to_string())

    print(f"\n=== Fast-only series: {series_fast.shape[1]} categories x "
          f"{len(series_fast)} days ===")
    dropped = sorted(set(series.columns) - set(series_fast.columns))
    if dropped:
        print(f"  no Fast members, excluded from Fast-only runs: {dropped}")
    tot_f = series_fast.sum().sort_values(ascending=False)
    print(pd.DataFrame({"units": tot_f,
                        "pct_of_all_sku": (100 * tot_f / tot).round(1)}).to_string())

    series.to_csv(OUT_SERIES, lineterminator="\n")
    series_fast.to_csv(OUT_SERIES_FAST, lineterminator="\n")
    products.to_csv(OUT_AUDIT, index=False, lineterminator="\n")
    print(f"\nWrote {OUT_SERIES}, {OUT_SERIES_FAST} and {OUT_AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
