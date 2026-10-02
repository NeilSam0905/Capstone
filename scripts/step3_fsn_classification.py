"""
Phase 2 of ETL: FSN (Fast/Slow/Non-moving) classification.

Reads Fact_Sales + Dim_Product from ustore.db, computes ADUS per SKU,
classifies F/S/N at an 80th-percentile cutoff (with 75th/85th run as a
sensitivity check), and writes fsn_class back to Dim_Product.

ADUS definition (per spec):
  ADUS = weighted units sold / number of distinct tally dates the item
         sold on (NOT calendar days - these are episodic tally records).
  weighted units = SUM(quantity_sold * w), w = 0.5 if imputation_flag=1
                   else 1.0, so imputed/allocated rows count for less.

  Days flagged is_censored = 1 by step2 (a zero sale on a day the stock
  model says the item was already out) are dropped from the denominator:
  a day the store had nothing to sell is not evidence that the item
  moves slowly. This is Block 2.4's flag being used rather than merely
  recorded - it is the one place the flag changes a published number, so
  set EXCLUDE_CENSORED_DAYS = False to see the classification without it.
  Only 16.8% of rows have any stock record at all, so this can only help
  the items inventory actually covers; the rest are unaffected.
  A SKU's observation window is anchored at its own entry_date (which,
  by construction, is already the earliest date it has a Fact_Sales
  row) rather than at the dataset's overall start, so a newer SKU is
  never diluted by days before it existed.

Classification (primary, 80th percentile):
  - Non-moving (N): no recorded sales - no Fact_Sales rows at all, OR
    rows that are all zeros (on the tally sheet, never sold). This is the
    manuscript's definition. It used to be "no rows at all" only, which
    classed the 19 on-the-sheet-but-never-sold items Slow
    (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 4.2).
  - Fast (F): ADUS in the top 20% (>= 80th percentile) of the ADUS
    distribution.
  - Slow (S): everything else.

  The percentile cutoff is computed over every SKU with a Fact_Sales row,
  the zero-unit ones included, exactly as before the N change. Those
  items sit at ADUS 0, at the bottom of the distribution, so moving them
  to N re-labels them and nothing else: the cutoff, and so every other
  item's F/S class, is the same as it would have been.

Recency: an item that sold NOTHING in the last STALE_DAYS (90) days is
never Fast, whatever its ADUS - it is classed Slow. ADUS is over full
history, and its denominator only counts days the item is on the sheet,
so an item that drops off the sheets keeps its old score forever: "UST
T-Shirt (College Shirt)" sold 44 units in May 2024 (on the sheet 23
days, ADUS 1.91), was never tallied again, and stayed Fast. Before this
rule 28 of the 58 Fast items had sold nothing in the last 90 days, 13
nothing in a year. 90 days is the window measured on 30 Sep (docs/
SESSION_SUMMARY_2026-09-30.md 4.2: about 15 dead items a month dropped,
no loss of coverage of next month's sales); tools/fsn_recency_check.py
re-measures it. The window ends at the last date ANY item sold (not the
panel's zero-padded end), the same reference step4 / step4c use. The
percentile cutoff is still computed over every moving SKU, so the rule
only demotes; it never changes the cutoff another item is measured
against.

HVL (High-Velocity Limited) is a reporting flag only, not a 4th
fsn_class value (the column is CHECK-constrained to F/S/N): a Fast item
with fewer than 30 active tally dates is flagged HVL so it isn't read
as having the same confidence as an established Fast SKU.

Sensitivity: the same classification is re-run at 75th/85th percentile
cutoffs. Any moving SKU whose F/S label changes across the three
cutoffs is reported as borderline.
"""
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd

DB_PATH = "ustore.db"
THRESHOLDS = [75, 80, 85]
PRIMARY_THRESHOLD = 80
HVL_MIN_DATES = 30
EXCLUDE_CENSORED_DAYS = True
STALE_DAYS = 90


def load_fact(con):
    """Sales rows only. Damaged, promo and transfer removals recorded through the
    Tally Interface leave the shelf but are not demand, so they must not raise an
    item's ADUS. Case-insensitive: the interface used to store 'SALE'."""
    return pd.read_sql(
        """SELECT f.product_id, f.date_id, d.calendar_date, f.quantity_sold,
                  f.imputation_flag, f.is_censored
           FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
           WHERE LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'""",
        con, parse_dates=["calendar_date"],
    )


def classify(products, fact, stale_days=STALE_DAYS, verbose=False):
    """F/S/N for every product from `fact` (load_fact's shape). No I/O, so
    tools/fsn_recency_check.py can rebuild the Fast list as of any past date
    by passing a truncated `fact`. Returns a namespace of the frames main()
    reports on."""
    # ---- recency: days since each SKU's last sale, up to the last day anything sold ----
    sold = fact[fact["quantity_sold"] > 0]
    last_any_sale = sold["calendar_date"].max()
    last_sale = sold.groupby("product_id")["calendar_date"].max()
    days_since_sale = (last_any_sale - products["product_id"].map(last_sale)).dt.days
    # No sale ever counts as stale too (such an SKU has ADUS 0 and is Slow or N anyway).
    stale = (days_since_sale.isna() | (days_since_sale >= stale_days)).to_numpy()
    if EXCLUDE_CENSORED_DAYS:
        censored = fact["is_censored"] == 1
        if verbose:
            print(f"Dropping {int(censored.sum())} censored zero-sale rows "
                  f"({fact.loc[censored, 'product_id'].nunique()} SKUs) from the ADUS denominator")
        fact = fact[~censored]
    fact = fact.assign(weight=np.where(fact["imputation_flag"] == 1, 0.5, 1.0))
    fact["weighted_units"] = fact["quantity_sold"] * fact["weight"]

    agg = fact.groupby("product_id").agg(
        weighted_units=("weighted_units", "sum"),
        units=("quantity_sold", "sum"),
        active_tally_dates=("date_id", "nunique"),
    ).reset_index()

    df = products.assign(stale=stale).merge(agg, on="product_id", how="left")
    df["weighted_units"] = df["weighted_units"].fillna(0.0)
    df["units"] = df["units"].fillna(0)
    df["active_tally_dates"] = df["active_tally_dates"].fillna(0).astype(int)
    df["ADUS"] = np.where(
        df["active_tally_dates"] > 0, df["weighted_units"] / df["active_tally_dates"], 0.0
    )

    # Every SKU with a row sets the cutoff (see the docstring); only the ones
    # that sold anything can be F or S.
    on_sheet = df[df["active_tally_dates"] > 0]
    cutoffs = {t: on_sheet["ADUS"].quantile(t / 100.0) for t in THRESHOLDS}
    moving = df[df["units"] > 0].copy()
    non_moving = df[df["units"] <= 0].copy()
    never_sold = non_moving[non_moving["active_tally_dates"] > 0]

    # ---- classify at each threshold ----
    for t in THRESHOLDS:
        moving[f"class_{t}"] = np.where((moving["ADUS"] >= cutoffs[t]) & ~moving["stale"], "F", "S")
    demoted = moving[(moving["ADUS"] >= cutoffs[PRIMARY_THRESHOLD]) & moving["stale"]]

    df["fsn_class"] = "N"
    df.loc[moving.index, "fsn_class"] = moving[f"class_{PRIMARY_THRESHOLD}"]

    # ---- HVL flag (primary threshold), persisted to Dim_Product.is_hvl ----
    moving["HVL"] = (moving[f"class_{PRIMARY_THRESHOLD}"] == "F") & (
        moving["active_tally_dates"] < HVL_MIN_DATES
    )
    df["is_hvl"] = 0
    df.loc[moving.index, "is_hvl"] = moving["HVL"].astype(int)

    # ---- borderline: F/S label changes across the 3 thresholds ----
    label_cols = [f"class_{t}" for t in THRESHOLDS]
    moving["borderline"] = moving[label_cols].nunique(axis=1) > 1

    return SimpleNamespace(df=df, moving=moving, non_moving=non_moving, never_sold=never_sold,
                           cutoffs=cutoffs, demoted=demoted,
                           last_any_sale=last_any_sale, last_sale=last_sale)


def main():
    con = sqlite3.connect(DB_PATH)

    products = pd.read_sql("SELECT product_id, item_name, entry_date FROM Dim_Product", con)
    r = classify(products, load_fact(con), verbose=True)
    df, moving, non_moving, never_sold = r.df, r.moving, r.non_moving, r.never_sold
    cutoffs, demoted, last_any_sale, last_sale = r.cutoffs, r.demoted, r.last_any_sale, r.last_sale

    # ---- write fsn_class + is_hvl back to Dim_Product ----
    con.executemany(
        "UPDATE Dim_Product SET fsn_class = ?, is_hvl = ? WHERE product_id = ?",
        list(zip(df["fsn_class"], df["is_hvl"], df["product_id"])),
    )
    con.commit()

    # ================= REPORT =================
    print("=== Percentile cutoffs (ADUS, computed over every SKU with a Fact_Sales row) ===")
    for t in THRESHOLDS:
        print(f"  {t}th percentile: ADUS >= {cutoffs[t]:.4f}")

    print("\n=== Category counts (primary: 80th percentile) ===")
    print(df["fsn_class"].value_counts().reindex(["F", "S", "N"]).fillna(0).astype(int).to_string())

    print("\n=== Sensitivity table (category counts per threshold) ===")
    sens_rows = []
    for t in THRESHOLDS:
        f_count = (moving[f"class_{t}"] == "F").sum()
        s_count = (moving[f"class_{t}"] == "S").sum()
        sens_rows.append({"threshold": f"{t}th pct", "F": f_count, "S": s_count, "N": len(non_moving)})
    sens_df = pd.DataFrame(sens_rows)
    print(sens_df.to_string(index=False))

    print(f"\n=== Recency: {len(demoted)} items above the Fast cutoff but with no sale in the "
          f"{STALE_DAYS} days to {last_any_sale.date()} -> classed Slow ===")
    if len(demoted):
        print(
            demoted.assign(ADUS=lambda d: d["ADUS"].round(3),
                           last_sale=lambda d: d["product_id"].map(last_sale).dt.date)
            [["item_name", "ADUS", "last_sale"]]
            .sort_values("last_sale")
            .to_string(index=False)
        )

    hvl = moving[moving["HVL"]].sort_values("ADUS", ascending=False)
    print(f"\n=== HVL (High-Velocity Limited) items: {len(hvl)} ===")
    if len(hvl):
        print(
            hvl[["item_name", "ADUS", "active_tally_dates"]]
            .assign(ADUS=lambda d: d["ADUS"].round(3))
            .to_string(index=False)
        )

    borderline = moving[moving["borderline"]].sort_values("ADUS", ascending=False)
    print(f"\n=== Borderline items (class changes across 75/80/85): {len(borderline)} ===")
    if len(borderline):
        print(
            borderline[["item_name", "ADUS", "active_tally_dates", "class_75", "class_80", "class_85"]]
            .assign(ADUS=lambda d: d["ADUS"].round(3))
            .to_string(index=False)
        )

    print(f"\nNon-moving (N) items: {len(non_moving)} "
          f"({len(never_sold)} on the sheets but never sold, "
          f"{len(non_moving) - len(never_sold)} with no rows at all)")

    con.close()


if __name__ == "__main__":
    main()
