"""
load_star_tables.py — load Dim_Product and Fact_Sales into ustore.db.

Prereq: create_schema.py (tables) and populate_dim_date.py (Dim_Date) already run.

Dim_Product : the full product catalog = union of canonical items seen in sales
              and in inventory (517 rows). supplier_name comes from the NORMALIZED
              sales file; category / unit_price_php from inventory; entry_date is
              the item's earliest appearance (sales date if it ever sold, else
              earliest inventory date). lead_time_days / fsn_class left NULL — they
              are later work items (management estimate; FSN classification).
Fact_Sales  : the derived per-product-per-date fact (USTore_fact_sales_derived.csv),
              re-keyed to product_id (via item_name) and date_id (via Dim_Date).
              Only the 297 sold products appear here; the 220 inventory-only
              products sit in Dim_Product with no fact rows (correct for N-class).
"""
import sqlite3

import pandas as pd

DB = "ustore.db"
VOCAB = "vocab_mapping_FINAL_v2.csv"
SALES_NORM = "USTore_sales_long_allocated_normalized.csv"
INVENTORY = "USTore_inventory_excel_long.csv"
FACT_DERIVED = "USTore_fact_sales_derived.csv"


def mode_or_none(s):
    s = s.dropna()
    return s.mode().iloc[0] if not s.empty else None


def build_dim_product(sales, inv):
    items = sorted(set(sales["canonical_item_name"]) | set(inv["canonical_item_name"]))

    supplier = sales.groupby("canonical_item_name")["supplier_name"].apply(mode_or_none)
    category = inv.groupby("canonical_item_name")["Category"].apply(mode_or_none)
    price = inv.groupby("canonical_item_name")["Price"].apply(mode_or_none)

    sales_entry = sales.groupby("canonical_item_name")["Date"].min()
    inv_entry = inv.groupby("canonical_item_name")["Date"].min()

    rows = []
    for pid, item in enumerate(items, start=1):
        entry = sales_entry.get(item)
        if pd.isna(entry) if entry is not None else True:
            entry = inv_entry.get(item)
        rows.append({
            "product_id": pid,
            "item_name": item,
            "category": category.get(item),
            "unit_price_php": price.get(item),
            "supplier_name": supplier.get(item),
            "lead_time_days": None,
            "fsn_class": None,
            "entry_date": entry if isinstance(entry, str) else (None if entry is None else str(entry)),
            "is_active": 1,
        })
    return pd.DataFrame(rows)


def main():
    vm = pd.read_csv(VOCAB)
    mp = dict(zip(vm["raw_name"].astype(str).str.strip(),
                  vm["canonical_item_name"].astype(str).str.strip()))

    sales = pd.read_csv(SALES_NORM, dtype={"Date": str})
    inv = pd.read_csv(INVENTORY, dtype={"Date": str})
    inv["canonical_item_name"] = inv["Item"].astype(str).str.strip().map(mp)
    inv["Price"] = pd.to_numeric(inv["Price"], errors="coerce")
    unmapped = inv["canonical_item_name"].isna().sum()
    if unmapped:
        raise SystemExit(f"{unmapped} inventory rows have no canonical mapping — fix the vocab file first.")

    dim_product = build_dim_product(sales, inv)
    pid_of = dict(zip(dim_product["item_name"], dim_product["product_id"]))

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON;")

    # date_id lookup from Dim_Date
    date_id = dict(con.execute("SELECT calendar_date, date_id FROM Dim_Date").fetchall())

    # ---- Fact_Sales from the derived fact ----
    fact = pd.read_csv(FACT_DERIVED, dtype={"Date": str})
    fact["product_id"] = fact["canonical_item_name"].map(pid_of)
    fact["date_id"] = fact["Date"].map(date_id)
    assert fact["product_id"].notna().all(), "unmapped product in fact"
    assert fact["date_id"].notna().all(), "fact date outside Dim_Date scope"

    fact_rows = [
        (
            i,                                             # sale_id
            int(r.product_id),
            int(r.date_id),
            int(r.quantity_sold),
            int(r.cumulative_monthly_units),
            None if pd.isna(r.daily_depletion_rate) else float(r.daily_depletion_rate),
            int(r.imputation_flag),
            int(r.tally_date_flag),
            "sale",
        )
        for i, r in enumerate(fact.itertuples(index=False), start=1)
    ]

    # ---- load (clear first; children before re-inserting parents) ----
    cur = con.cursor()
    cur.execute("DELETE FROM Fact_Sales;")
    cur.execute("DELETE FROM Dim_Product;")
    dim_product.to_sql("Dim_Product", con, if_exists="append", index=False)
    cur.executemany(
        """INSERT INTO Fact_Sales (
               sale_id, product_id, date_id, quantity_sold, cumulative_monthly_units,
               daily_depletion_rate, imputation_flag, tally_date_flag, transaction_type
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        fact_rows,
    )
    con.commit()

    # ---- verification ----
    np = cur.execute("SELECT COUNT(*) FROM Dim_Product").fetchone()[0]
    nf = cur.execute("SELECT COUNT(*) FROM Fact_Sales").fetchone()[0]
    units = cur.execute("SELECT SUM(quantity_sold) FROM Fact_Sales").fetchone()[0]
    orphans_p = cur.execute(
        "SELECT COUNT(*) FROM Fact_Sales f LEFT JOIN Dim_Product p ON f.product_id=p.product_id WHERE p.product_id IS NULL"
    ).fetchone()[0]
    orphans_d = cur.execute(
        "SELECT COUNT(*) FROM Fact_Sales f LEFT JOIN Dim_Date d ON f.date_id=d.date_id WHERE d.date_id IS NULL"
    ).fetchone()[0]
    sold = cur.execute("SELECT COUNT(DISTINCT product_id) FROM Fact_Sales").fetchone()[0]
    null_rate = cur.execute("SELECT COUNT(*) FROM Fact_Sales WHERE daily_depletion_rate IS NULL").fetchone()[0]

    print("=== star tables loaded ===")
    print(f"Dim_Product rows           : {np}")
    print(f"Fact_Sales rows            : {nf}")
    print(f"  distinct products sold   : {sold}  (inventory-only: {np - sold})")
    print(f"  units_sold total         : {units}")
    print(f"  depletion NULL (long gap): {null_rate}")
    print(f"FK orphans (product/date)  : {orphans_p} / {orphans_d}")
    with_sup = cur.execute("SELECT COUNT(*) FROM Dim_Product WHERE supplier_name IS NOT NULL").fetchone()[0]
    with_cat = cur.execute("SELECT COUNT(*) FROM Dim_Product WHERE category IS NOT NULL").fetchone()[0]
    print(f"Dim_Product w/ supplier    : {with_sup}   w/ category+price: {with_cat}")
    con.close()


if __name__ == "__main__":
    main()
