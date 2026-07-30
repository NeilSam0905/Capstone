"""
verify_data.py
------------------------------------------------------------------
Run this before every commit that touches a CSV in this repo. It exists
because these CSVs are edited by script only, never opened in Excel -
Excel silently reformats/truncates dates and reorders columns, and
nothing downstream would notice until a script crashed or (worse)
produced quietly wrong output.

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
SALES_WITH_ZEROS_CSV = "USTore_sales_long_with_zeros.csv"

# Established and explained in this session: the zero-fill rebuild
# picked up a July 2026 sheet the old combined file never had, plus a
# more authoritative re-derivation of May 2024 - both real, both
# checked for double-counting. 89,232 is now the correct invariant,
# not the older 88,481.
EXPECTED_SALES_TOTAL = 89232

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def verify_calendar_ranges(path=CALENDAR_RANGES_CSV):
    df = pd.read_csv(path, dtype=str)

    bad_start = df[~df["start_date"].str.match(ISO_DATE_RE)]
    check(len(bad_start) == 0,
          f"{path}: {len(bad_start)} row(s) with non-ISO start_date, e.g. {bad_start['start_date'].tolist()[:3]}")

    bad_end = df[~df["end_date"].str.match(ISO_DATE_RE)]
    check(len(bad_end) == 0,
          f"{path}: {len(bad_end)} row(s) with non-ISO end_date, e.g. {bad_end['end_date'].tolist()[:3]}")

    if not bad_start.any().any() and not bad_end.any().any():
        starts = pd.to_datetime(df["start_date"], format="%Y-%m-%d")
        ends = pd.to_datetime(df["end_date"], format="%Y-%m-%d")
        inverted = df[ends < starts]
        check(len(inverted) == 0,
              f"{path}: {len(inverted)} row(s) with end_date < start_date "
              f"(a date was likely read as MM/DD, not DD/MM) - rows: {inverted.index.tolist()[:5]}")

    check(len(df) == 135, f"{path}: expected 135 rows, found {len(df)}")


def verify_sales_totals(path=SALES_WITH_ZEROS_CSV):
    df = pd.read_csv(path)
    total = int(df["Total Quantity"].sum())
    check(total == EXPECTED_SALES_TOTAL,
          f"{path}: Total Quantity sums to {total}, expected {EXPECTED_SALES_TOTAL} "
          f"(unit conservation broken - a row was lost, duplicated, or a value was corrupted)")

    bad_dates = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce").isna().sum()
    check(bad_dates == 0, f"{path}: {bad_dates} row(s) with unparseable Date values")

    negative = (df["Total Quantity"] < 0).sum()
    check(negative == 0, f"{path}: {negative} row(s) with a negative Total Quantity")


def main():
    verify_calendar_ranges()
    verify_sales_totals()

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
