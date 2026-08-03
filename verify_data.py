"""
verify_data.py
------------------------------------------------------------------
Run this before every commit that touches a CSV in this repo. It exists
because these CSVs are edited by script only, never opened in Excel -
Excel silently reformats/truncates dates and reorders columns, and
nothing downstream would notice until a script crashed or (worse)
produced quietly wrong output.

Every date column in every CSV here is ISO 8601 (YYYY-MM-DD). That is
the single thing this file guards hardest, because the failure mode is
silent: 05/11/2025 is a valid date under both DD/MM and MM/DD, so a
locale reformat moves data six months without erroring anywhere. The
inventory CSV was found in exactly that state - written as ISO by
`Inventory Excel Converter.py`, committed as DD/MM/YYYY - which is what
this check is for.

Exit code 0 = all checks passed. Non-zero = something is wrong; the
printed messages say what and where.

HOW TO RUN:
    python verify_data.py
------------------------------------------------------------------
"""
import re
import sys

import pandas as pd

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CALENDAR_RANGES_CSV = "calendar_ranges.csv"
SALES_RAW_CSV = "USTore_sales_long_May_Aug2024-May2026.csv"
SALES_WITH_ZEROS_CSV = "USTore_sales_long_with_zeros.csv"
SALES_ALLOCATED_CSV = "USTore_sales_long_allocated.csv"
INVENTORY_CSV = "USTore_inventory_excel_long.csv"
ALLOCATION_AUDIT_CSV = "allocation_audit.csv"
SUPPLIER_MAPPING_CSV = "supplier_mapping.csv"

VALID_PAYMENT_STATUS = {"CONSIGNMENT", "PAID", "UNKNOWN"}

# Every (file, date column) pair in the repo. Add to this list, don't
# write a new one-off check.
DATE_COLUMNS = [
    (CALENDAR_RANGES_CSV, "start_date"),
    (CALENDAR_RANGES_CSV, "end_date"),
    (SALES_RAW_CSV, "Date"),
    (SALES_WITH_ZEROS_CSV, "Date"),
    (SALES_ALLOCATED_CSV, "Date"),
    (INVENTORY_CSV, "Date"),
    (ALLOCATION_AUDIT_CSV, "Date"),
]

# Established and explained in this session: the zero-fill rebuild
# picked up a July 2026 sheet the old combined file never had, plus a
# more authoritative re-derivation of May 2024 - both real, both
# checked for double-counting. 89,232 is now the correct invariant,
# not the older 88,481.
EXPECTED_SALES_TOTAL = 89232
# The pre-zero-fill converter output, kept for provenance. Its own
# total is the older figure and must stay there - it is a different
# set of months, not a different count of the same months.
EXPECTED_SALES_RAW_TOTAL = 88481

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def verify_iso_dates(path, col):
    """Non-ISO text, and dates that are ISO-shaped but impossible
    (2025-13-05 - the signature of a DD/MM value pasted into an ISO
    column). errors="raise" is deliberate; "coerce" would turn exactly
    the rows we're hunting for into blanks."""
    series = pd.read_csv(path, dtype=str)[col].fillna("")

    bad_shape = series[~series.str.match(ISO_DATE_RE)]
    check(len(bad_shape) == 0,
          f"{path}: {len(bad_shape)} row(s) where {col} is not YYYY-MM-DD, "
          f"e.g. {bad_shape.tolist()[:3]} (opened in Excel?)")
    if len(bad_shape):
        return

    try:
        pd.to_datetime(series, format="%Y-%m-%d", errors="raise")
    except ValueError as exc:
        check(False, f"{path}: {col} has an ISO-shaped but invalid date - {exc}")


def verify_calendar_ranges(path=CALENDAR_RANGES_CSV):
    df = pd.read_csv(path, dtype=str)

    if df["start_date"].str.match(ISO_DATE_RE).all() and df["end_date"].str.match(ISO_DATE_RE).all():
        starts = pd.to_datetime(df["start_date"], format="%Y-%m-%d")
        ends = pd.to_datetime(df["end_date"], format="%Y-%m-%d")
        inverted = df[ends < starts]
        check(len(inverted) == 0,
              f"{path}: {len(inverted)} row(s) with end_date < start_date "
              f"(a date was likely read as MM/DD, not DD/MM) - rows: {inverted.index.tolist()[:5]}")

    check(len(df) == 135, f"{path}: expected 135 rows, found {len(df)}")


def verify_sales_totals(path, expected_total, qty_col="Total Quantity"):
    df = pd.read_csv(path)
    total = int(df[qty_col].sum())
    check(total == expected_total,
          f"{path}: {qty_col} sums to {total}, expected {expected_total} "
          f"(unit conservation broken - a row was lost, duplicated, or a value was corrupted)")

    negative = (df[qty_col] < 0).sum()
    check(negative == 0, f"{path}: {negative} row(s) with a negative {qty_col}")


def verify_supplier_mapping(mapping_path=SUPPLIER_MAPPING_CSV, sales_path=SALES_WITH_ZEROS_CSV):
    """Every raw Supplier string has to be accounted for. Without this,
    a new supplier (or a new typo of an existing one) silently becomes a
    20th supplier in Dim_Product - step1 aborts on it, but only after
    someone has already committed the CSV."""
    sm = pd.read_csv(mapping_path, dtype=str).fillna("")
    sales = pd.read_csv(sales_path, dtype=str)

    raw = set(sales["Supplier"].str.strip())
    mapped = set(sm["raw_supplier"].str.strip())
    missing = sorted(raw - mapped)
    check(not missing,
          f"{mapping_path}: {len(missing)} Supplier string(s) in {sales_path} are unmapped: {missing[:3]}")
    stale = sorted(mapped - raw)
    check(not stale,
          f"{mapping_path}: {len(stale)} mapped supplier(s) no longer appear in {sales_path}: {stale[:3]}")

    bad_status = sorted(set(sm["payment_status"].str.strip()) - VALID_PAYMENT_STATUS)
    check(not bad_status,
          f"{mapping_path}: payment_status values outside {sorted(VALID_PAYMENT_STATUS)}: {bad_status}")

    named = sm[sm["supplier_name"].str.strip() != ""]
    check(named["supplier_name"].str.contains(r"\(", regex=True).sum() == 0,
          f"{mapping_path}: a supplier_name still carries a parenthetical suffix - "
          f"that belongs in payment_status")


def main():
    for path, col in DATE_COLUMNS:
        verify_iso_dates(path, col)

    verify_calendar_ranges()
    verify_supplier_mapping()

    verify_sales_totals(SALES_RAW_CSV, EXPECTED_SALES_RAW_TOTAL)
    verify_sales_totals(SALES_WITH_ZEROS_CSV, EXPECTED_SALES_TOTAL)
    # allocation splits grouped rows but must never create or destroy units
    verify_sales_totals(SALES_ALLOCATED_CSV, EXPECTED_SALES_TOTAL)

    if failures:
        print(f"FAILED: {len(failures)} check(s) did not pass:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All data verification checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
