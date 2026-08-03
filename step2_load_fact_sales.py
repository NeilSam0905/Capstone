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

Also reads USTore_inventory_excel_long_mapped.csv, the only stock signal
in the project, for days_of_supply and the censoring flag.

Derived fields (no formula is specified upstream, so these are ETL-level
assumptions, documented here rather than silently picked):

  - cumulative_monthly_units: running sum of quantity_sold for a product,
    reset at the start of each calendar month, ordered by date.

  - daily_depletion_rate: the product's total units on that date, divided
    by the days elapsed since its previous observation. Block 2.6 asked
    for four rules to be settled; measured on the current data, they are:

      1. First observation per product (286 rows): NULL. There is no
         elapsed interval to divide by, and the old code divided by 1,
         which invents a rate that reads as "sold this much in one day".
      2. Same-date rows (640 pairs, from a product receiving both a
         direct and an allocated row on one day): the numerator is the
         product's DAILY TOTAL and every row of that date carries the
         same rate. Previously the second row divided by a gap of 0
         clamped to 1, giving two incompatible rates for one day.
      3. Long gaps: the denominator is capped at LONG_GAP_CAP_DAYS = 30.
         Post zero-fill only 3.5% of consecutive observations are more
         than a day apart (it was 55% on tally-date data), but the tail
         is long - max gap 156 days, 217 gaps over 30. Dividing by 156
         reports a real sale as a depletion rate near zero. Capping
         overstates the rate on those rows, which is the safe direction
         for a field that feeds reorder timing.
      4. Month boundaries: the interval is NOT reset at the 1st. 2,011
         observations sit across a month boundary with a gap over a day;
         depletion is a physical rate, not an accounting period. Only
         cumulative_monthly_units resets.

    Sundays are deliberately NOT excluded from the denominator: 76 of the
    608 tally dates are Sundays and 15 fall on dates flagged
    is_store_closed, so the store demonstrably trades on both. Whether
    those closure flags are wrong is a question for the store visit
    (Block 5), not something to assume here.

  - days_of_supply: estimated units on hand divided by the product's mean
    daily units over the trailing 28 observed days. NULL where the
    product has no inventory count for that month - which is most of
    them; see the coverage figure the script prints.

  - is_censored (Block 2.4): 1 = a zero-sale row where the stock model
    says the item was already out, so the zero is a supply gap rather
    than absent demand; 0 = stock believed on hand; NULL = no inventory
    record, so a zero-filled row and a censored row cannot be told
    apart. The stock model is: units on hand at the start of a day =
    that month's inventory count minus the product's sales earlier in
    the month. A positive sale while the model says zero proves an
    unrecorded restock, so that row and the rest of that month become
    NULL rather than being forced to fit.
"""
import sqlite3
import sys
from collections import defaultdict

import pandas as pd

SALES_CSV = "USTore_sales_long_allocated.csv"
INVENTORY_CSV = "USTore_inventory_excel_long_mapped.csv"
ITEM_COL = "canonical_item_name"
DB_PATH = "ustore.db"

LONG_GAP_CAP_DAYS = 30   # see rule 3 in the module docstring
DOS_WINDOW_DAYS = 28     # trailing window for the days_of_supply rate

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


def load_stock(product_id_by_name):
    """stock[product_id][YYYY-MM] = that month's inventory count, summed over
    sizes and locations. The count date within a month varies (mostly the 1st,
    sometimes the 2nd/8th/9th/13th); it is treated as a beginning-of-month
    figure, the same convention proportional_allocation.py already uses."""
    inv = pd.read_csv(INVENTORY_CSV, dtype=str)
    inv["qty"] = pd.to_numeric(inv["Quantity"], errors="coerce").fillna(0.0)
    inv["ym"] = inv["Date"].str[:7]
    stock = defaultdict(dict)
    grouped = inv.groupby([ITEM_COL, "ym"])["qty"].sum()
    for (item, ym), qty in grouped.items():
        product_id = product_id_by_name.get(item)
        if product_id is not None:
            stock[product_id][ym] = float(qty)
    return stock


def stock_derived(product_id, dates, day_total, sold_before, stock, stats):
    """days_of_supply and is_censored for one product, per date.

    Both are NULL for any month with no inventory count - the honest
    answer, since 66% of Fact_Sales rows are for items that never appear
    in the inventory workbook at all (Block 3)."""
    months = stock.get(product_id, {})
    dos, censored = {}, {}
    broken_months = set()   # months where an unrecorded restock invalidated the model

    for i, d in enumerate(dates):
        ym = d[:7]
        if ym not in months or ym in broken_months:
            dos[d] = None
            censored[d] = None
            stats["stock_unknown"] += 1
            continue

        on_hand = months[ym] - sold_before[d]
        if on_hand > 0:
            censored[d] = 0
            stats["not_censored"] += 1
        elif day_total[d] == 0:
            censored[d] = 1
            stats["censored"] += 1
        else:
            # sold something the model says we didn't have: a restock nobody
            # recorded. Stop guessing for the rest of this month.
            broken_months.add(ym)
            dos[d] = None
            censored[d] = None
            stats["stock_model_broken"] += 1
            continue

        # trailing-window mean daily units, over observed days only
        window = [day_total[dates[j]] for j in range(max(0, i - DOS_WINDOW_DAYS + 1), i + 1)]
        rate = sum(window) / len(window)
        dos[d] = round(max(on_hand, 0.0) / rate, 2) if rate > 0 else None
        if dos[d] is None:
            stats["dos_null_zero_rate"] += 1

    return dos, censored


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

    # ---- derive the four ETL fields (rules in the module docstring) ----
    by_product = defaultdict(list)
    for r in good_rows:
        by_product[r["product_id"]].append(r)

    stock = load_stock(product_id_by_name)
    stats = defaultdict(int)

    for product_id, rows in by_product.items():
        rows.sort(key=lambda r: (r["iso_date"], r["row_order"]))

        # daily totals drive the rate, the stock model and the DOS window
        day_total = defaultdict(int)
        for r in rows:
            day_total[r["iso_date"]] += r["quantity_sold"]
        dates = sorted(day_total)

        month_running = defaultdict(int)
        # units sold earlier in the month, per date, for the stock model
        sold_before = {}
        running = defaultdict(int)
        for d in dates:
            sold_before[d] = running[d[:7]]
            running[d[:7]] += day_total[d]

        rate_by_date = {}
        prev = None
        for d in dates:
            if prev is None:
                rate_by_date[d] = None          # rule 1
                stats["rate_null_first_obs"] += 1
            else:
                gap = (pd.Timestamp(d) - pd.Timestamp(prev)).days
                if gap > LONG_GAP_CAP_DAYS:     # rule 3
                    stats["rate_gap_capped"] += 1
                    gap = LONG_GAP_CAP_DAYS
                rate_by_date[d] = round(day_total[d] / gap, 4)   # rules 2 and 4
            prev = d

        dos_by_date, censored_by_date = stock_derived(
            product_id, dates, day_total, sold_before, stock, stats
        )

        for r in rows:
            d = r["iso_date"]
            month_running[d[:7]] += r["quantity_sold"]
            r["cumulative_monthly_units"] = month_running[d[:7]]
            r["daily_depletion_rate"] = rate_by_date[d]
            r["days_of_supply"] = dos_by_date[d]
            r["is_censored"] = censored_by_date[d]

    fact_rows = [
        (
            r["product_id"],
            r["date_id"],
            r["quantity_sold"],
            r["cumulative_monthly_units"],
            r["daily_depletion_rate"],
            r["days_of_supply"],
            r["is_censored"],
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
            daily_depletion_rate, days_of_supply, is_censored,
            imputation_flag, tally_date_flag, transaction_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    # row-level counts, so these are comparable with the load summary above;
    # `stats` counts product-days, which is the natural unit for the rules
    # themselves but not for "how much of the table is affected".
    all_rows = [r for rows in by_product.values() for r in rows]
    n_rate_null = sum(1 for r in all_rows if r["daily_depletion_rate"] is None)
    n_cens_1 = sum(1 for r in all_rows if r["is_censored"] == 1)
    n_cens_0 = sum(1 for r in all_rows if r["is_censored"] == 0)
    n_cens_null = sum(1 for r in all_rows if r["is_censored"] is None)
    n_dos = sum(1 for r in all_rows if r["days_of_supply"] is not None)

    print("\n=== DERIVED FIELDS (Blocks 2.4 / 2.6) === (rows, of "
          f"{loaded}; product-days in brackets)")
    print(f"daily_depletion_rate NULL, product's first observation  : {n_rate_null}"
          f"  [{stats['rate_null_first_obs']} products]")
    print(f"daily_depletion_rate with the gap capped at {LONG_GAP_CAP_DAYS} days   : "
          f"[{stats['rate_gap_capped']} product-days]")
    print(f"is_censored = 1, zero sale and the stock model says out : {n_cens_1}"
          f"  [{stats['censored']}]")
    print(f"is_censored = 0, stock believed on hand                 : {n_cens_0}"
          f"  [{stats['not_censored']}]")
    print(f"is_censored NULL, no inventory count that month         : {n_cens_null}"
          f"  [{stats['stock_unknown'] + stats['stock_model_broken']}, of which "
          f"{stats['stock_model_broken']} from an unrecorded restock]")
    cov = 100 * (n_cens_1 + n_cens_0) / loaded if loaded else 0
    print(f"Stock coverage: {n_cens_1 + n_cens_0} / {loaded} rows = {cov:.1f}% "
          f"(the rest is Block 3's inventory-coverage gap)")
    print(f"days_of_supply populated                               : {n_dos}"
          f"  ({stats['dos_null_zero_rate']} product-days left NULL: trailing rate 0)")

    con.close()


if __name__ == "__main__":
    main()
