"""
Step 1 of ETL: apply the canonical-name mapping to the sales and inventory
CSVs, report any unmapped item names, and populate Dim_Product.

Does NOT do proportional allocation and does NOT touch Fact_Sales.
"""
import sqlite3
import sys

import pandas as pd

MAPPING_CSV = "vocab_mapping_FINAL_v5.csv"
SUPPLIER_MAPPING_CSV = "supplier_mapping.csv"
SALES_CSV = "USTore_sales_long_with_zeros.csv"
INVENTORY_CSV = "USTore_inventory_excel_long.csv"
DB_PATH = "ustore.db"

# Named after its actual input (SALES_CSV), not the older pre-zero-fill
# file - proportional_allocation.py reads this, so the name has to say
# which sales file it came from.
SALES_MAPPED_CSV = "USTore_sales_long_with_zeros_mapped.csv"
INVENTORY_MAPPED_CSV = "USTore_inventory_excel_long_mapped.csv"


def load_mapping():
    vm = pd.read_csv(MAPPING_CSV)
    vm["raw_name"] = vm["raw_name"].astype(str).str.strip()
    vm["canonical_item_name"] = vm["canonical_item_name"].astype(str).str.strip()
    return dict(zip(vm["raw_name"], vm["canonical_item_name"]))


def load_supplier_mapping():
    """42 raw Supplier strings -> 19 suppliers + a payment_status, per
    supplier_mapping.csv. Two of the raw strings resolve to no supplier at
    all (the bare "(Paid)" parser artefact and an item name typed into the
    Supplier column); those carry an empty supplier_name and are counted
    as unattributed rather than invented."""
    sm = pd.read_csv(SUPPLIER_MAPPING_CSV, dtype=str).fillna("")
    for col in ("raw_supplier", "supplier_name", "payment_status"):
        sm[col] = sm[col].str.strip()
    return (dict(zip(sm["raw_supplier"], sm["supplier_name"])),
            dict(zip(sm["raw_supplier"], sm["payment_status"])))


def apply_supplier_mapping(sales, name_map, status_map):
    raw = sales["Supplier"].astype(str).str.strip()
    unknown = sorted(set(raw) - set(name_map))
    if unknown:
        print("[supplier] UNMAPPED supplier strings:")
        for u in unknown:
            print(f"   - {u!r}")
        sys.exit(f"ABORTING: {len(unknown)} supplier string(s) missing from "
                 f"{SUPPLIER_MAPPING_CSV}. Add them there first.")
    sales = sales.copy()
    sales["supplier_name"] = raw.map(name_map).replace("", pd.NA)
    sales["payment_status"] = raw.map(status_map).replace("", pd.NA)
    n_named = int(sales["supplier_name"].notna().sum())
    print(f"[supplier] {raw.nunique()} raw strings -> "
          f"{sales['supplier_name'].nunique()} suppliers; "
          f"{len(sales) - n_named} row(s) with no attributable supplier")
    return sales


def apply_mapping(df, mapping, label):
    stripped_items = df["Item"].astype(str).str.strip()
    canonical = stripped_items.map(mapping)
    unmatched_mask = canonical.isna()
    unmatched_names = sorted(stripped_items[unmatched_mask].unique().tolist())
    df = df.copy()
    df["canonical_item_name"] = canonical
    print(f"[{label}] rows processed: {len(df)}")
    print(f"[{label}] unmatched distinct item names: {len(unmatched_names)}")
    if unmatched_names:
        print(f"[{label}] UNMATCHED NAMES:")
        for name in unmatched_names:
            print(f"   - {name!r}")
    return df, unmatched_names


def build_dim_product(sales_mapped, inventory_mapped):
    # entry_date = earliest sales date per canonical item
    sales_dates = sales_mapped.copy()
    # Every CSV in this repo stores dates as ISO 8601 (YYYY-MM-DD). errors="raise"
    # is deliberate: a coerced date becomes NaT and silently vanishes from the
    # entry_date min(), which nothing downstream would notice.
    sales_dates["parsed_date"] = pd.to_datetime(
        sales_dates["Date"], format="%Y-%m-%d", errors="raise"
    )
    entry_dates = (
        sales_dates.groupby("canonical_item_name")["parsed_date"]
        .min()
        .dt.strftime("%Y-%m-%d")
    )

    # category + unit_price_php: from inventory, most frequent non-null value per item
    def mode_or_none(series):
        s = series.dropna()
        if s.empty:
            return None
        return s.mode().iloc[0]

    inv_cat = inventory_mapped.groupby("canonical_item_name")["Category"].apply(mode_or_none)
    inv_price = inventory_mapped.groupby("canonical_item_name")["Price"].apply(mode_or_none)

    # supplier_name / payment_status: from sales, most frequent non-null value
    # per item, off the NORMALISED columns. Payment status is really a property
    # of a consignment agreement, not of a product, and a few items appear
    # under both terms - the modal value is a summary, and the count of items
    # where it isn't unanimous is reported below so it can't pass unnoticed.
    sales_supplier = sales_mapped.groupby("canonical_item_name")["supplier_name"].apply(mode_or_none)
    sales_status = sales_mapped.groupby("canonical_item_name")["payment_status"].apply(mode_or_none)

    mixed = (
        sales_mapped.dropna(subset=["payment_status"])
        .groupby("canonical_item_name")["payment_status"].nunique()
    )
    n_mixed = int((mixed > 1).sum())
    print(f"[supplier] items sold under more than one payment_status: {n_mixed} "
          f"(Dim_Product keeps the modal value)")

    all_items = sorted(
        set(sales_mapped["canonical_item_name"]) | set(inventory_mapped["canonical_item_name"])
    )

    rows = []
    for item in all_items:
        rows.append(
            {
                "item_name": item,
                "category": inv_cat.get(item),
                "unit_price_php": inv_price.get(item),
                "supplier_name": sales_supplier.get(item),
                "payment_status": sales_status.get(item),
                "lead_time_days": None,
                "fsn_class": None,
                "entry_date": entry_dates.get(item),
                "is_active": 1,
            }
        )
    return pd.DataFrame(rows)


def main():
    mapping = load_mapping()

    sales = pd.read_csv(SALES_CSV)
    inventory = pd.read_csv(INVENTORY_CSV)

    sales_mapped, sales_unmatched = apply_mapping(sales, mapping, "sales")
    inventory_mapped, inventory_unmatched = apply_mapping(inventory, mapping, "inventory")

    name_map, status_map = load_supplier_mapping()
    sales_mapped = apply_supplier_mapping(sales_mapped, name_map, status_map)

    sales_mapped.to_csv(SALES_MAPPED_CSV, index=False)
    inventory_mapped.to_csv(INVENTORY_MAPPED_CSV, index=False)

    total_unmatched = len(set(sales_unmatched) | set(inventory_unmatched))
    if total_unmatched:
        print(f"\nABORTING Dim_Product load: {total_unmatched} unmatched name(s) found. Fix the mapping file first.")
        sys.exit(1)

    dim_product = build_dim_product(sales_mapped, inventory_mapped)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM Dim_Product")
    dim_product.to_sql("Dim_Product", con, if_exists="append", index=False)
    con.commit()
    row_count = cur.execute("SELECT COUNT(*) FROM Dim_Product").fetchone()[0]
    con.close()

    print("\n=== SUMMARY ===")
    print(f"sales rows processed: {len(sales_mapped)}")
    print(f"inventory rows processed: {len(inventory_mapped)}")
    print(f"unmatched names: {total_unmatched}")
    print(f"Dim_Product rows: {row_count}")
    print(f"\nMapped files written: {SALES_MAPPED_CSV}, {INVENTORY_MAPPED_CSV}")


if __name__ == "__main__":
    main()
