"""
The store's own inventory report workbook, imported through the Monthly
Inventory Count card.

The report is one workbook for every month: a MAIN STORAGE snapshot plus an
APPAREL and a NON-APPAREL sheet per month, with the sheets for the rest of the
year laid out in advance and left blank. Importing it loads the LATEST month
that has counts in it - not the month the card happens to be set to, and not
a blank template month - totalled per item across its sizes, classifications
and inside/outside locations, the same way catalog.load_csv_stock totals the
historical workbook.

Sheets are parsed by scripts/Inventory Excel Converter.py, the converter the
historical inventory CSV is built with, so a sheet reads the same here as it
does there.
"""
import importlib
import io
import re
from datetime import datetime

from openpyxl import load_workbook

import name_matcher                          # scripts/, on sys.path via pipeline

converter = importlib.import_module("Inventory Excel Converter")

# "OCTOBER 2026 INVENTORY  FOR APP", "OCTOBER INVENTORY FOR NON-APPAR".
# Excel cuts sheet names at 31 characters and some months carry no year, so
# the year is optional here and read from the sheet's own title when absent.
MONTH_SHEET_RE = re.compile(
    r"^\s*(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)"
    r"\.?\s*(\d{4})?\b.*INVENTORY", re.I)


def _iso_month(text):
    """'October 2026' -> '2026-10', or None."""
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m")
        except ValueError:
            pass
    return None


def _rows(wb, name):
    return [list(r) for r in wb[name].iter_rows(values_only=True)]


def _monthly_sheets(wb):
    """'YYYY-MM' -> [sheet names]. MAIN STORAGE is a one-off snapshot, not a
    month of the report, and is left out."""
    out = {}
    for name in wb.sheetnames:
        m = MONTH_SHEET_RE.match(name)
        if not m:
            continue
        month = _iso_month(f"{m.group(1)} {m.group(2)}") if m.group(2) else None
        if month is None:
            rows = _rows(wb, name)
            month = _iso_month(converter.find_report_month(rows, converter.find_report_date(rows)))
        if month:
            out.setdefault(month, []).append(name)
    return out


def read_latest_month(raw, up_to_month):
    """Read the report's latest month with counts, no later than `up_to_month`.

    Returns None when `raw` is not the inventory report (no monthly
    "<MONTH> ... INVENTORY" sheets, or not a readable workbook), so the caller
    can fall back to reading it as a plain Item / Units On Hand sheet.
    Otherwise {"month": 'YYYY-MM' or None, "sheets": [...], "rows": [...]},
    where rows are the converter's rows for that month's sheets. A month
    counts as having data when any item on it has a non-zero quantity: a
    template month copied forward with zeros is still a blank month."""
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:                                  # openpyxl raises a zoo
        return None
    try:
        by_month = _monthly_sheets(wb)
        if not by_month:
            return None
        for month in sorted(by_month, reverse=True):
            if month > up_to_month:
                continue
            rows = []
            for name in by_month[month]:
                converter.convert_sheet(_rows(wb, name), name, rows)
            if any(r["Quantity"] not in ("", 0) for r in rows):
                return {"month": month, "sheets": by_month[month], "rows": rows}
        return {"month": None, "sheets": [], "rows": []}
    finally:
        wb.close()


def item_totals(rows):
    """[(item name, units on hand or None)], one per item in sheet order.

    An item's sizes / classifications / locations are added together. Names
    that differ only in case or spacing are one item (name_matcher.name_key),
    so a held name is held once with its whole total. None means the sheet
    gives no figure for any of the item's rows ("N/A" or blank)."""
    totals = {}
    for r in rows:
        item = r["Item"].strip()
        if not item:
            continue
        entry = totals.setdefault(name_matcher.name_key(item), [item, None])
        if r["Quantity"] != "":
            entry[1] = (entry[1] or 0) + r["Quantity"]
    return [tuple(v) for v in totals.values()]
