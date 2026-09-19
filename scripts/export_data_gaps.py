"""
scripts/export_data_gaps.py
------------------------------------------------------------------
One workbook listing every product with a missing attribute, ranked by
how much it matters.

Why ranked rather than just listed
----------------------------------
519 products have gaps; 256 of them are missing a supplier. A flat list
of 256 rows is not actionable - it does not say which ones to chase. So
every row carries the two facts that decide priority:

  units_sold   an item that has never sold is a catalogue entry, not an
               operational problem. An item selling thousands of units
               with no supplier on file is a payment that cannot be
               reconciled.
  fsn_class    Fast-moving items feed the forecast and the reorder
               advisories; a gap there propagates into a decision.

Rows are sorted by units sold, so the top of every sheet is the part
worth someone's afternoon.

Sheets
------
  Summary          counts per gap type, and how much volume each covers
  No Category      category = 'Uncategorised'
  No Supplier      supplier_name null or blank
  No Price         unit_price_php null
  All Gaps         one row per affected product, with a flag per gap
                   and a gap_count, so multi-gap items surface first

Run:  python scripts/export_data_gaps.py [--out data/USTore_Data_Gaps.xlsx]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "ustore.db")
DEFAULT_OUT = os.path.join(ROOT, "data", "USTore_Data_Gaps.xlsx")

COLS = ["product_id", "item_name", "category", "supplier_name",
        "unit_price_php", "fsn_class", "is_hvl", "units_sold",
        "sale_days", "first_sale", "last_sale"]


def blank(series):
    """Null, empty, or a placeholder string - all mean 'not recorded'."""
    s = series.astype("string")
    return series.isna() | s.str.strip().isin(["", "None", "nan", "NaN"])


def load(con):
    p = pd.read_sql_query("""
        SELECT product_id, item_name, category, supplier_name, unit_price_php,
               fsn_class, is_hvl, payment_status, entry_date, lead_time_days,
               storage_category
        FROM Dim_Product
    """, con)

    # Observed sales, so a gap can be weighed rather than just counted.
    sales = pd.read_sql_query("""
        SELECT f.product_id,
               SUM(f.quantity_sold)                              AS units_sold,
               COUNT(DISTINCT CASE WHEN f.quantity_sold > 0
                                   THEN f.date_id END)           AS sale_days,
               MIN(CASE WHEN f.quantity_sold > 0 THEN d.calendar_date END) AS first_sale,
               MAX(CASE WHEN f.quantity_sold > 0 THEN d.calendar_date END) AS last_sale
        FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
        GROUP BY f.product_id
    """, con)

    p = p.merge(sales, on="product_id", how="left")
    for c in ("units_sold", "sale_days"):
        p[c] = p[c].fillna(0)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    p = load(con)
    con.close()

    p["no_category"] = (p["category"].fillna("") == "Uncategorised") | blank(p["category"])
    p["no_supplier"] = blank(p["supplier_name"])
    p["no_price"] = p["unit_price_php"].isna()
    p["no_payment_status"] = blank(p["payment_status"])
    p["no_entry_date"] = blank(p["entry_date"])

    flags = ["no_category", "no_supplier", "no_price",
             "no_payment_status", "no_entry_date"]
    p["gap_count"] = p[flags].sum(axis=1)

    def sheet(mask):
        return (p.loc[mask, COLS]
                 .sort_values(["units_sold", "item_name"], ascending=[False, True])
                 .reset_index(drop=True))

    no_cat, no_sup, no_price = (sheet(p.no_category), sheet(p.no_supplier),
                                sheet(p.no_price))
    all_gaps = (p.loc[p.gap_count > 0, COLS + flags + ["gap_count"]]
                  .sort_values(["gap_count", "units_sold"], ascending=[False, False])
                  .reset_index(drop=True))

    total_units = p["units_sold"].sum()
    summary = pd.DataFrame([
        {"gap": "No category (Uncategorised)", "products": int(p.no_category.sum()),
         "pct_of_catalogue": 100 * p.no_category.mean(),
         "units_affected": p.loc[p.no_category, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & p.no_category).sum())},
        {"gap": "No supplier", "products": int(p.no_supplier.sum()),
         "pct_of_catalogue": 100 * p.no_supplier.mean(),
         "units_affected": p.loc[p.no_supplier, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & p.no_supplier).sum())},
        {"gap": "No unit price", "products": int(p.no_price.sum()),
         "pct_of_catalogue": 100 * p.no_price.mean(),
         "units_affected": p.loc[p.no_price, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & p.no_price).sum())},
        {"gap": "No payment status", "products": int(p.no_payment_status.sum()),
         "pct_of_catalogue": 100 * p.no_payment_status.mean(),
         "units_affected": p.loc[p.no_payment_status, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & p.no_payment_status).sum())},
        {"gap": "No entry date", "products": int(p.no_entry_date.sum()),
         "pct_of_catalogue": 100 * p.no_entry_date.mean(),
         "units_affected": p.loc[p.no_entry_date, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & p.no_entry_date).sum())},
        {"gap": "ANY gap", "products": int((p.gap_count > 0).sum()),
         "pct_of_catalogue": 100 * (p.gap_count > 0).mean(),
         "units_affected": p.loc[p.gap_count > 0, "units_sold"].sum(),
         "fast_moving_affected": int(((p.fsn_class == "F") & (p.gap_count > 0)).sum())},
        {"gap": "-- catalogue total --", "products": len(p),
         "pct_of_catalogue": 100.0, "units_affected": total_units,
         "fast_moving_affected": int((p.fsn_class == "F").sum())},
    ])
    summary["pct_of_units"] = 100 * summary["units_affected"] / max(total_units, 1)

    notes = pd.DataFrame({"note": [
        "Generated by scripts/export_data_gaps.py from ustore.db.",
        "",
        "units_sold / sale_days come from Fact_Sales - they say whether a gap",
        "matters. An item that never sold is a catalogue entry; an item selling",
        "thousands of units with no supplier is a payment that cannot be",
        "reconciled against a batch report.",
        "",
        "Sheets are sorted by units sold, so the actionable rows are at the top.",
        "'All Gaps' is sorted by gap_count first - items missing several",
        "attributes are usually one bad source row, not five separate problems.",
        "",
        "no_supplier, no_payment_status and no_entry_date overlap almost exactly:",
        "they come from products that entered via a source carrying none of the",
        "three, so fixing the supplier normally fixes all three at once.",
        "",
        "'No category' means category = 'Uncategorised' - step1b_categorize_products.py",
        "found no keyword match for the item name. It is a naming gap, not a",
        "missing product: the fix is usually a rule in that script's CATEGORY_RULES",
        "or a cleaner item name, not a database edit.",
    ]})

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with pd.ExcelWriter(args.out, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        no_cat.to_excel(xl, sheet_name="No Category", index=False)
        no_sup.to_excel(xl, sheet_name="No Supplier", index=False)
        no_price.to_excel(xl, sheet_name="No Price", index=False)
        all_gaps.to_excel(xl, sheet_name="All Gaps", index=False)
        notes.to_excel(xl, sheet_name="Notes", index=False)

        for name, df in (("Summary", summary), ("No Category", no_cat),
                         ("No Supplier", no_sup), ("No Price", no_price),
                         ("All Gaps", all_gaps), ("Notes", notes)):
            ws = xl.sheets[name]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, start=1):
                # An all-null column gives .max() of NaN, not 0 - guard it,
                # otherwise a sheet with one empty column kills the export.
                longest = 0
                if len(df):
                    m = df[col].astype(str).str.len().max()
                    longest = int(m) if pd.notna(m) else 0
                width = max(len(str(col)), longest)
                ws.column_dimensions[
                    ws.cell(1, i).column_letter].width = min(max(width + 2, 10), 52)

    print("=" * 74)
    print("DATA GAPS")
    print("=" * 74)
    print(summary[["gap", "products", "pct_of_catalogue", "units_affected",
                   "pct_of_units", "fast_moving_affected"]]
          .to_string(index=False, float_format=lambda x: "%.1f" % x))
    print("\nWrote %s" % args.out)
    print("Sheets: Summary, No Category (%d), No Supplier (%d), No Price (%d), "
          "All Gaps (%d), Notes" % (len(no_cat), len(no_sup), len(no_price),
                                    len(all_gaps)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
