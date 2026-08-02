"""
Step 2 of ETL: load the allocated sales history into Fact_Sales, routing
anything unresolvable to Exception_Log instead of dropping it.

Reads:
  - USTore_sales_long_allocated.csv (Date, canonical_item_name, Total
    Quantity, Supplier, imputation_flag, weight) - names already
    canonical and allocation already applied
  - ustore.db (Dim_Product, Dim_Date already populated; Fact_Sales empty)

No vocabulary mapping here any more (Block 2.2): mapping now happens once,
in step1_apply_mapping.py, and everything after it joins on canonical
names. A row whose name isn't in Dim_Product is an Exception_Log row, not
something to re-map on the fly - two places applying the same mapping is
how the map-vs-allocate ambiguity started.

Safe to re-run: Fact_Sales and Exception_Log are cleared before loading.

Derived fields (no formula is specified upstream, so these are ETL-level
assumptions, documented here rather than silently picked):
  - cumulative_monthly_units: running sum of quantity_sold for a product,
    reset at the start of each calendar month, ordered by date.
  - daily_depletion_rate: quantity_sold / days since that product's
    previous tally record (or 1 for a product's first record). This
    turns an episodic tally quantity into a comparable daily rate,
    since tally_date_flag=1 rows are irregular-interval observations,
    not literal daily sales.
"""
import sqlite3
import sys
from collections import defaultdict

import pandas as pd

SALES_CSV = "USTore_sales_long_allocated.csv"
ITEM_COL = "canonical_item_name"
DB_PATH = "ustore.db"

REASON_INVALID_QTY = "invalid_or_missing_quantity"
REASON_ITEM_IS_SUPPLIER = "item_name_is_supplier_name"
REASON_ITEM_NO_MATCH = "item_no_match_in_dim_product"
REASON_DATE_NO_MATCH = "date_no_match_in_dim_date"


def create_exception_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS Exception_Log (
            exception_id      INTEGER PRIMARY KEY,
            raw_date          TEXT,
            raw_item          TEXT,
            raw_quantity      TEXT,
            raw_supplier      TEXT,
            raw_imputation_flag TEXT,
            raw_weight        TEXT,
            reason            TEXT
        );
    """)


def main():
    df = pd.read_csv(SALES_CSV, dtype=str)
    if ITEM_COL not in df.columns:
        sys.exit(f"{SALES_CSV}: no {ITEM_COL!r} column - this is a pre-Block-2.2 "
                 f"allocated file built on raw names. Re-run step1_apply_mapping.py "
                 f"then proportional_allocation.py.")
    df = df.rename(columns={"Total Quantity": "Quantity", ITEM_COL: "Item"})
    df["row_order"] = range(len(df))

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    create_exception_table(con)

    con.execute("DELETE FROM Fact_Sales")
    con.execute("DELETE FROM Exception_Log")

    product_id_by_name = dict(
        con.execute("SELECT item_name, product_id FROM Dim_Product").fetchall()
    )
    date_id_by_iso = dict(
        con.execute("SELECT calendar_date, date_id FROM Dim_Date").fetchall()
    )

    # Only treat frequently-recurring Supplier values as "known suppliers".
    # A real supplier appears on hundreds/thousands of rows; a one-off data
    # entry mistake (e.g. an item name accidentally typed into the Supplier
    # column for a handful of rows) would not - and without this floor, a
    # coincidental match would wrongly quarantine that item's real rows
    # elsewhere. Empirically suppliers cluster at >=120 rows with one
    # genuine low-volume supplier at 56 and the one known bad entry at 48,
    # so 100 cleanly separates real suppliers from noise.
    MIN_SUPPLIER_ROWS = 100
    supplier_counts = df["Supplier"].dropna().str.strip().value_counts()
    known_suppliers = set()
    for s, cnt in supplier_counts.items():
        if cnt < MIN_SUPPLIER_ROWS:
            continue
        known_suppliers.add(s.lower())
        # also index the name with any trailing "(...)" qualifier stripped,
        # e.g. "BLAZE (CONSIGNMENT)" -> "blaze"
        base = s.split("(")[0].strip().lower()
        if base:
            known_suppliers.add(base)

    exceptions = []
    good_rows = []  # dicts with product_id, date_id, iso_date, quantity_sold, imputation_flag, row_order

    for row in df.itertuples(index=False):
        raw_date, raw_item, raw_qty, raw_supplier, raw_imp, raw_weight, row_order = (
            row.Date, row.Item, row.Quantity, row.Supplier, row.imputation_flag, row.weight, row.row_order
        )

        item = (raw_item or "").strip()
        supplier = (raw_supplier or "").strip()

        def send_to_exception(reason):
            exceptions.append((raw_date, raw_item, raw_qty, raw_supplier, raw_imp, raw_weight, reason))

        # 1. quantity must be a non-negative integer (0 is a legitimate
        # "tallied, sold nothing" observation now that zero-fill preserves
        # true zero-sale days - only negative/missing/non-numeric is invalid)
        qty = None
        try:
            qty = int(float(raw_qty))
        except (TypeError, ValueError):
            qty = None
        if qty is None or qty < 0:
            send_to_exception(REASON_INVALID_QTY)
            continue

        # 2. item column actually holds a supplier name, not a product
        if item.lower() in known_suppliers:
            send_to_exception(REASON_ITEM_IS_SUPPLIER)
            continue

        # 3. item is already canonical - it must exist in Dim_Product as-is
        product_id = product_id_by_name.get(item)
        if product_id is None:
            send_to_exception(REASON_ITEM_NO_MATCH)
            continue

        # 4. date must parse and exist in Dim_Date
        # ISO (YYYY-MM-DD) only. The old DD/MM/YYYY fallback is gone: with
        # both formats accepted, "05/11/2025" parses under either reading
        # and lands on a different day depending on which branch caught it.
        # A non-ISO date is now a row-level exception, not a silent guess.
        parsed = pd.to_datetime(raw_date, format="%Y-%m-%d", errors="coerce")
        iso_date = parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else None
        date_id = date_id_by_iso.get(iso_date) if iso_date else None
        if date_id is None:
            send_to_exception(REASON_DATE_NO_MATCH)
            continue

        good_rows.append(
            {
                "product_id": product_id,
                "date_id": date_id,
                "iso_date": iso_date,
                "quantity_sold": qty,
                "imputation_flag": int(float(raw_imp)) if raw_imp not in (None, "") else 0,
                "row_order": row_order,
            }
        )

    # ---- derive cumulative_monthly_units and daily_depletion_rate ----
    by_product = defaultdict(list)
    for r in good_rows:
        by_product[r["product_id"]].append(r)

    for product_id, rows in by_product.items():
        rows.sort(key=lambda r: (r["iso_date"], r["row_order"]))
        month_running = defaultdict(int)
        prev_date = None
        for r in rows:
            ym = r["iso_date"][:7]
            month_running[ym] += r["quantity_sold"]
            r["cumulative_monthly_units"] = month_running[ym]

            cur_date = pd.Timestamp(r["iso_date"])
            gap_days = (cur_date - prev_date).days if prev_date is not None else 1
            gap_days = max(gap_days, 1)
            r["daily_depletion_rate"] = round(r["quantity_sold"] / gap_days, 4)
            prev_date = cur_date

    fact_rows = [
        (
            r["product_id"],
            r["date_id"],
            r["quantity_sold"],
            r["cumulative_monthly_units"],
            r["daily_depletion_rate"],
            r["imputation_flag"],
            1,  # tally_date_flag: these are historical tally observations
            "sale",
        )
        for rows in by_product.values()
        for r in rows
    ]

    con.executemany(
        """INSERT INTO Fact_Sales
           (product_id, date_id, quantity_sold, cumulative_monthly_units,
            daily_depletion_rate, imputation_flag, tally_date_flag, transaction_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        fact_rows,
    )
    con.executemany(
        """INSERT INTO Exception_Log
           (raw_date, raw_item, raw_quantity, raw_supplier, raw_imputation_flag, raw_weight, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        exceptions,
    )
    con.commit()

    # ---- report ----
    total_input = len(df)
    loaded = len(fact_rows)
    n_exceptions = len(exceptions)
    imputed_loaded = sum(1 for rows in by_product.values() for r in rows if r["imputation_flag"] == 1)

    reason_counts = defaultdict(int)
    for e in exceptions:
        reason_counts[e[-1]] += 1

    print("=== LOAD SUMMARY ===")
    print(f"Total input rows            : {total_input}")
    print(f"Rows loaded into Fact_Sales  : {loaded}")
    print(f"Rows sent to Exception_Log   : {n_exceptions}")
    print("  Breakdown by reason:")
    for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {reason:32} {cnt}")
    print(f"Fact_Sales rows with imputation_flag=1 : {imputed_loaded}")
    flagged_or_isolated = imputed_loaded + n_exceptions
    pct = 100 * flagged_or_isolated / total_input if total_input else 0
    print(f"Flagged or isolated: {flagged_or_isolated} / {total_input} = {pct:.2f}%")

    con.close()


if __name__ == "__main__":
    main()
