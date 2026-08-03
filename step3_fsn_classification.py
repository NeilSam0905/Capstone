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
  - Non-moving (N): zero Fact_Sales rows at all.
  - Fast (F): ADUS in the top 20% (>= 80th percentile) of the ADUS
    distribution computed over SKUs that have at least one sale.
  - Slow (S): everything else.

HVL (High-Velocity Limited) is a reporting flag only, not a 4th
fsn_class value (the column is CHECK-constrained to F/S/N): a Fast item
with fewer than 30 active tally dates is flagged HVL so it isn't read
as having the same confidence as an established Fast SKU.

Sensitivity: the same classification is re-run at 75th/85th percentile
cutoffs. Any moving SKU whose F/S label changes across the three
cutoffs is reported as borderline.
"""
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = "ustore.db"
THRESHOLDS = [75, 80, 85]
PRIMARY_THRESHOLD = 80
HVL_MIN_DATES = 30
EXCLUDE_CENSORED_DAYS = True


def main():
    con = sqlite3.connect(DB_PATH)

    products = pd.read_sql("SELECT product_id, item_name, entry_date FROM Dim_Product", con)

    fact = pd.read_sql(
        "SELECT product_id, date_id, quantity_sold, imputation_flag, is_censored FROM Fact_Sales",
        con,
    )
    if EXCLUDE_CENSORED_DAYS:
        censored = fact["is_censored"] == 1
        print(f"Dropping {int(censored.sum())} censored zero-sale rows "
              f"({fact.loc[censored, 'product_id'].nunique()} SKUs) from the ADUS denominator")
        fact = fact[~censored]
    fact["weight"] = np.where(fact["imputation_flag"] == 1, 0.5, 1.0)
    fact["weighted_units"] = fact["quantity_sold"] * fact["weight"]

    agg = fact.groupby("product_id").agg(
        weighted_units=("weighted_units", "sum"),
        active_tally_dates=("date_id", "nunique"),
    ).reset_index()

    df = products.merge(agg, on="product_id", how="left")
    df["weighted_units"] = df["weighted_units"].fillna(0.0)
    df["active_tally_dates"] = df["active_tally_dates"].fillna(0).astype(int)
    df["ADUS"] = np.where(
        df["active_tally_dates"] > 0, df["weighted_units"] / df["active_tally_dates"], 0.0
    )

    moving = df[df["active_tally_dates"] > 0].copy()
    non_moving = df[df["active_tally_dates"] == 0].copy()

    # ---- classify at each threshold, over the moving population only ----
    cutoffs = {t: moving["ADUS"].quantile(t / 100.0) for t in THRESHOLDS}
    for t in THRESHOLDS:
        moving[f"class_{t}"] = np.where(moving["ADUS"] >= cutoffs[t], "F", "S")

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

    # ---- write fsn_class + is_hvl back to Dim_Product ----
    con.executemany(
        "UPDATE Dim_Product SET fsn_class = ?, is_hvl = ? WHERE product_id = ?",
        list(zip(df["fsn_class"], df["is_hvl"], df["product_id"])),
    )
    con.commit()

    # ================= REPORT =================
    print("=== Percentile cutoffs (ADUS, computed over moving SKUs only) ===")
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

    print(f"\nNon-moving (N) items: {len(non_moving)}")

    con.close()


if __name__ == "__main__":
    main()
