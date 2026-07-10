"""
USTore Inventory Report converter
==================================
Converts the USTore's Main Storage inventory report (a human-formatted sheet
export with merged header blocks and forward-filled blanks) into a tidy
long-format CSV with the columns:

    Report Date, No, Item, Size, Price, Quantity, Notes

The source sheet mixes two layouts in one file:
  - Block A (no SIZE column): NO, PRICE, ITEM, QUANTITY, [blank], PICTURES,
    TAKEN ITEMS, [extra note/date]
  - Block B (with SIZE column): NO, PRICE, ITEM, SIZE, QUANTITY, PICTURES,
    TAKEN ITEMS, [extra note/date]

A new "header" row (starting with "NO.") switches which layout is active for
the rows that follow. Within a layout, a size breakdown for one item spans
several rows: only the first row of the group carries NO / PRICE / ITEM, the
rest are blank and only carry SIZE + QUANTITY. Those blanks are forward-filled
from the group's first row.

QUANTITY cells like "213 pcs.", "9,980 pcs." or bare "20" are parsed to plain
integers. PRICE cells like "1,750" are parsed to plain integers. The
free-text "TAKEN ITEMS" note (and any stray extra column) is kept verbatim in
a Notes column rather than parsed into structured events - the dates/quantities
inside are written in too many inconsistent formats to reliably split apart.

Usage:
    python "Inventory Converter.py"                 # uses the bundled report
    python "Inventory Converter.py" file.csv -o out.csv
"""

import re
import csv
import argparse

DEFAULT_INPUT = "Copy of USTORE INVENTORY REPORT - MAIN STORAGE.csv"
DEFAULT_OUTPUT = "USTore_inventory_long.csv"

DATE_RE = re.compile(r"DATE:\s*(\d{1,2}/\d{1,2}/\d{4})")
NUM_RE = re.compile(r"[\d,]+")


def clean(s):
    return (s or "").strip()


def find_report_date(rows):
    for row in rows:
        for cell in row:
            m = DATE_RE.search(cell or "")
            if m:
                return m.group(1)
    return ""


def is_header_row(row):
    return clean(row[0]).upper().rstrip(".") == "NO"


def has_size_column(row):
    return any(clean(c).upper() == "SIZE" for c in row)


def parse_int(text):
    """Pull the first number out of a cell like '213 pcs.' or '9,980 pcs.' or '20'."""
    m = NUM_RE.search(text or "")
    if not m:
        return None
    return int(m.group(0).replace(",", ""))


def convert(in_path, out_path):
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    report_date = find_report_date(rows)

    out_rows = []
    layout_has_size = False
    group_no, group_item, group_price = "", "", None
    skipped = []

    for row in rows:
        row = [clean(c) for c in row] + [""] * max(0, 8 - len(row))

        if is_header_row(row):
            layout_has_size = has_size_column(row)
            group_no, group_item, group_price = "", "", None
            continue

        if layout_has_size:
            no, price, item, size, qty, pictures, taken, extra = row[:8]
        else:
            no, price, item, qty, blank, pictures, taken, extra = row[:8]
            size = ""

        if not item and not qty and not size:
            continue  # fully blank spacer row

        if item:
            # first row of a new item group
            group_no, group_item = no, item
            group_price = parse_int(price)
        elif not (size or qty):
            continue  # nothing to carry forward, nothing to record
        # else: continuation row -> reuse group_no / group_item / group_price

        quantity = parse_int(qty)
        if quantity is None:
            skipped.append(row)
            continue

        notes = "; ".join(n for n in (taken, extra) if n)

        out_rows.append([
            report_date, group_no, group_item, size,
            group_price if group_price is not None else "",
            quantity, notes,
        ])

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Report Date", "No", "Item", "Size", "Price", "Quantity", "Notes"])
        w.writerows(out_rows)

    return report_date, len(out_rows), skipped


def main():
    ap = argparse.ArgumentParser(description="Convert the USTore inventory report to a tidy long-format CSV.")
    ap.add_argument("file", nargs="?", default=DEFAULT_INPUT, help="Input inventory CSV")
    ap.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="Output CSV path")
    args = ap.parse_args()

    report_date, n, skipped = convert(args.file, args.output)

    print(f"Report date: {report_date or '(not found)'}")
    print(f"Wrote {n} rows -> {args.output}")
    if skipped:
        print(f"\n{len(skipped)} row(s) skipped (no parseable quantity):")
        for row in skipped:
            print("  -", row)


if __name__ == "__main__":
    main()
