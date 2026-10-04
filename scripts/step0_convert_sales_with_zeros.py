"""
Step 0 (new): re-convert the USTore TBS tally-sheet workbooks into long
format, this time PRESERVING true zero-sale days instead of dropping them.

Why this exists
----------------
The original converter ("Converter Aug 2024 - May 2026.py") drops any cell
that is blank OR an explicit 0 - it can't tell the two apart, so it treats
every gap in an item's series as "not observed". That's correct for a
sheet that only has a handful of tally-date columns (a periodic physical
stock count), but wrong for a sheet that has a date column for nearly
every calendar day in the month - there, a blank cell means "the store
was tallied that day and this item sold zero", a real data point that
was being silently discarded everywhere downstream (Fact_Sales, ADUS,
Prophet's series).

Per-sheet check: (# of real date columns) / (days spanned by those
columns). Verified against every monthly sheet in the source workbooks:
  - Aug 2024: 5 dates / 22-day span  (0.23) -> sparse, episodic
  - Sep 2024: 4 dates / 19-day span  (0.21) -> sparse, episodic
  - Oct 2024 onward, every month through Jul 2026: consistently >=0.85,
    most at or above 1.0 -> dense, essentially a full daily matrix
The May 2024 DSR&TBS file's "TBS" sheet is also dense (23/30 = 0.77),
consistent with its underlying daily "DAILY SALES REPORT" sheets (May 2,
May 3, ...) which record explicit 0s per item per day.

So: dense sheets get every blank/zero cell filled in as an explicit
Total Quantity = 0 row. Sparse sheets (only Aug/Sep 2024) keep the
original drop-blank-cells behaviour, since a gap there really does mean
"not counted that day", not "counted as zero".

Output: USTore_sales_long_with_zeros.csv (same 4 columns as the
original: Date, Item, Total Quantity, Supplier), reading directly from
the original source workbooks in rawdata/ (renamed from
drive-download-20260724T120738Z-1-001/ for convenience - same files).

Which workbooks: every .xlsx in rawdata/ that has a "<MONTH> ... - TBS"
sheet (find_workbooks / plan_sheets), so next academic year's workbook is
read as soon as it is dropped in - or uploaded through the Tally Interface
- with no edit here. This used to be a fixed list of four files. Skipped:
Excel's "~$" lock files, and the "import_*" copies the Tally Interface
archives after loading a Date/Item/Quantity table straight into Fact_Sales
(reading those here as well would count the same sales twice). When the
same month is in more than one workbook - an updated copy of a workbook
saved under a new name - only the most recently modified workbook's sheet
for that month is read, and the run says which copy it skipped.

Also writes the two price columns step1 needs (TBS_PRICES_CSV,
MAY_2024_DSR_PRICES_CSV) while the workbooks are open, so step1 opens none.
And it only converts when something it reads changed: the workbooks' names
and contents and this script are fingerprinted (STATE_PATH); if they match
the last conversion and the outputs are there, the run keeps them (~35 s
saved on a routine run). `--force` converts regardless.

Date is written as ISO 8601 (YYYY-MM-DD). The source workbooks use a
mix of DD/MM/YYYY, MM/DD/YYYY and real Excel date cells in their column
headers; parse_date_header() below resolves those, and everything this
script writes is ISO from that point on. Do not open the output in
Excel - it will silently rewrite the column back to a locale format.
"""
import calendar
import csv
import datetime
import hashlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict

import openpyxl

SRC_DIR = "rawdata"
# May 2024 is the one workbook whose tally sheet is not named "<MONTH> - TBS":
# it is a plain "TBS" sheet beside 23 daily sales-report sheets.
MAY_2024_WORKBOOK = "2024 5 MAY DSR & TBS.xlsx"
MAY_2024_NON_DSR_SHEETS = {"TS", "TBS", "INVENTORY"}
# Tally Interface archives of an uploaded Date/Item/Quantity table (see
# backend/app.py _archive_upload). Already loaded into Fact_Sales directly.
APP_IMPORT_PREFIX = "import_"
OUT_PATH = "data/USTore_sales_long_with_zeros.csv"
# The price columns step1 prices items from, written while the workbooks are
# open (see convert). With these, step1 reads no workbook at all.
TBS_PRICES_CSV = "data/tbs_item_prices.csv"
MAY_2024_DSR_PRICES_CSV = "data/may2024_dsr_prices.csv"
# What the last conversion on this machine read (see unchanged_since_last_run).
# Gitignored and not vaulted, like .vault_state.json: it describes this
# machine's rawdata/, which no other machine has.
STATE_PATH = ".step0_state.json"
DENSE_THRESHOLD = 0.7

MONTHS = {
    "JAN", "JANUARY", "FEB", "FEBRUARY", "MAR", "MARCH", "APR", "APRIL",
    "MAY", "JUN", "JUNE", "JUL", "JULY", "AUG", "AUGUST", "SEP", "SEPT",
    "SEPTEMBER", "OCT", "OCTOBER", "NOV", "NOVEMBER", "DEC", "DECEMBER",
}
META_HEADERS = {"TOTAL QUANTITY", "ITEM PRICE", "FOR REMITTANCE"}


def is_tbs_month_sheet(name: str) -> bool:
    n = name.strip().upper()
    if not n.endswith("- TBS"):
        return False
    return n.split()[0] in MONTHS


def is_total_row(label) -> bool:
    return isinstance(label, str) and label.strip().upper() == "TOTAL"


def parse_date_header(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        d = value.date() if isinstance(value, datetime.datetime) else value
        return "date", d
    if isinstance(value, str):
        s = value.strip().upper()
        if s == "NO DATE":
            return "nodate", None
        if s in META_HEADERS or s == "":
            return None, None
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y"):
            try:
                return "date", datetime.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None, None


def round_half_up(x) -> int:
    return int(math.floor(x + 0.5))


def to_quantity(value):
    """Coerce a cell to a quantity, or None if truly blank/non-numeric.
    NOTE: unlike the original converter, this DOES distinguish 0 from
    None - a real 0 is returned as 0.0, not collapsed into None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def fmt_qty(q):
    return int(q) if float(q).is_integer() else q


def impute_nodate_dates(date_cols, warnings, sheet_title):
    imputed = {}
    if not any(k == "nodate" for _, k, _ in date_cols):
        return imputed
    real = [(c, d) for c, k, d in date_cols if k == "date"]
    if not real:
        warnings.append(f"[{sheet_title}] NO DATE column but no real dates to anchor - left as 'NO DATE'")
        return imputed
    year, month = real[0][1].year, real[0][1].month
    last_day = calendar.monthrange(year, month)[1]
    median_day = round_half_up(statistics.median(sorted(d.day for _, d in real)))
    for i, (c, kind, _) in enumerate(date_cols):
        if kind != "nodate":
            continue
        left = next((date_cols[j][2].day for j in range(i - 1, -1, -1)
                     if date_cols[j][1] == "date"), None)
        right = next((date_cols[j][2].day for j in range(i + 1, len(date_cols))
                      if date_cols[j][1] == "date"), None)
        day = round_half_up((left + right) / 2) if (left is not None and right is not None) else median_day
        day = min(max(day, 1), last_day)
        imputed[c] = datetime.date(year, month, day)
    return imputed


def sheet_density(date_cols):
    """Density = (# real date columns) / (days in that calendar month).
    Uses the FULL month as the denominator, not just the span between the
    earliest and latest found date - a sheet whose only usable dates
    happen to cluster together (e.g. Sep 2024's 17/18/19) would otherwise
    look artificially "dense" just because those few dates are close
    together, even though the month as a whole was barely tallied."""
    real_dates = [d for _, k, d in date_cols if k == "date"]
    if not real_dates:
        return 0.0, 0
    year, month = real_dates[0].year, real_dates[0].month
    span_days = calendar.monthrange(year, month)[1]
    return (len(real_dates) / span_days if span_days else 0.0), span_days


class _Cell:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class SheetGrid:
    """A worksheet read once, in openpyxl's read-only mode, into a list of rows,
    answering the same ws.cell(r, c).value / ws.max_row / ws.max_column calls
    the converters below make. Read-only mode parses a workbook in about half
    the time but has no random cell access - this gives it back.

    A cell outside what the sheet stores reads as None, exactly as an empty
    cell does in a normally loaded sheet."""

    def __init__(self, ws):
        self.title = ws.title
        self.rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        self.max_row = len(self.rows)
        self.max_column = max((len(r) for r in self.rows), default=0)

    def cell(self, row, column):
        if row <= self.max_row:
            values = self.rows[row - 1]
            if column <= len(values):
                return _Cell(values[column - 1])
        return _Cell(None)

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        return iter(self.rows[min_row - 1:max_row])


def tbs_sheet_names(wb, path):
    """The tally sheets in one workbook, in workbook order."""
    if os.path.basename(path) == MAY_2024_WORKBOOK:
        return ["TBS"]
    return [sn for sn in wb.sheetnames if is_tbs_month_sheet(sn)]


def find_workbooks(src_dir=SRC_DIR):
    """Every .xlsx in src_dir that could hold tally sheets (see the module
    docstring for what is skipped). Raises FileNotFoundError when there is
    none, so the pipeline records this step as skipped and keeps the CSV it
    already has rather than overwriting it with an empty one."""
    names = sorted(os.listdir(src_dir)) if os.path.isdir(src_dir) else []
    files = [os.path.join(src_dir, n) for n in names
             if n.lower().endswith(".xlsx") and not n.startswith("~$")
             and not n.startswith(APP_IMPORT_PREFIX)]
    if not files:
        raise FileNotFoundError(f"No tally workbooks (.xlsx) found in {src_dir}/")
    return files


def sheet_month(ws):
    """'YYYY-MM' of the first real date in a tally sheet's header row, or None."""
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    for value in header[1:]:
        kind, d = parse_date_header(value)
        if kind == "date":
            return d.strftime("%Y-%m")
    return None


def plan_sheets(files):
    """Which sheet of which workbook to convert: [(path, [sheet names])],
    oldest workbook first, plus a note for every month found in more than one
    workbook. Opens each workbook read-only and reads only the header rows.

    A month in several workbooks is taken from the most recently modified
    one: the store keeps extending its current workbook, so a second copy of
    a month is an update of the first, and adding the two would count every
    sale in it twice."""
    found = []                         # (path, sheet, month)
    for path in files:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            found += [(path, sn, sheet_month(wb[sn])) for sn in tbs_sheet_names(wb, path)]
        finally:
            wb.close()

    holders = defaultdict(set)         # month -> workbooks that have it
    for path, _sn, month in found:
        if month:
            holders[month].add(path)
    owner, notes = {}, []
    for month, paths in sorted(holders.items()):
        owner[month] = max(paths, key=lambda p: (os.path.getmtime(p), p))
        if len(paths) > 1:
            others = sorted(os.path.basename(p) for p in paths - {owner[month]})
            notes.append(f"{month} is in {len(paths)} workbooks - read from "
                         f"'{os.path.basename(owner[month])}' (modified most recently), "
                         f"skipped in {', '.join(repr(o) for o in others)}")

    sheets = defaultdict(list)
    for path, sn, month in found:
        if month is None or owner[month] == path:
            sheets[path].append(sn)
    first = {p: min((m for pp, _s, m in found if pp == p and m), default="9999") for p in sheets}
    plan = [(p, sheets[p]) for p in sorted(sheets, key=lambda p: (first[p], p))]
    if not plan:
        raise FileNotFoundError(f"No tally sheets ('<MONTH> ... - TBS') in any workbook in "
                                f"{os.path.dirname(files[0]) or '.'}/")
    return plan, notes


def convert_sheet(ws, sheet_label, warnings, density_log):
    date_cols = []
    relevant_cols = []  # date/meta columns only - excludes stray trailing
                         # annotation columns like "LEGEND" comments
    for c in range(2, ws.max_column + 1):
        header = ws.cell(1, c).value
        kind, d = parse_date_header(header)
        if kind:
            date_cols.append([c, kind, d])
            relevant_cols.append(c)
        elif isinstance(header, str) and header.strip().upper() in META_HEADERS:
            relevant_cols.append(c)
    if not date_cols:
        warnings.append(f"[{sheet_label}] no date columns found - skipped")
        return

    density, span_days = sheet_density(date_cols)
    is_dense = density >= DENSE_THRESHOLD
    n_real = sum(1 for _, k, _ in date_cols if k == "date")
    density_log.append((sheet_label, n_real, span_days, density, is_dense))

    imputed = impute_nodate_dates(date_cols, warnings, sheet_label)

    supplier = None
    prev_was_header_row = False
    for r in range(2, ws.max_row + 1):
        label = ws.cell(r, 1).value
        if label is None or str(label).strip() == "":
            continue
        label = str(label).strip()

        if is_total_row(label):
            prev_was_header_row = False
            continue

        # A real item row always has SOME data somewhere in its row (a
        # price, a quantity, a remittance figure...). A supplier/
        # consignment label row has NONE - not even a price. This is more
        # robust than only trusting an explicit "TOTAL" row to signal the
        # next label is a new supplier: some sheets are missing that TOTAL
        # row (the label after it then gets a blank cell instead), and a
        # few suppliers split their qualifier onto its own line (e.g.
        # "STITCH CORP. (BLEEVES)" then a separate "(Consignment)" line) -
        # both cases would otherwise get misread as zero-selling "items".
        has_any_data = any(
            ws.cell(r, c).value not in (None, "")
            for c in relevant_cols
        )
        if not has_any_data:
            supplier = f"{supplier} {label}" if prev_was_header_row and supplier else label
            prev_was_header_row = True
            continue
        prev_was_header_row = False

        if supplier is None:
            warnings.append(f"[{sheet_label}] item '{label}' before any supplier - skipped")
            continue

        for c, kind, d in date_cols:
            qty = to_quantity(ws.cell(r, c).value)
            if kind == "date":
                obs = d
            elif c in imputed:
                obs = imputed[c]
            else:
                if qty is not None and qty != 0:
                    yield (1, 0), "NO DATE", label, qty, supplier
                continue

            if qty is None:
                if is_dense:
                    qty = 0.0
                else:
                    continue  # sparse month: a gap really is "not observed"

            yield (0, obs.toordinal()), obs.strftime("%Y-%m-%d"), label, qty, supplier


def sheet_item_prices(ws):
    """[(row label, price)] from a tally sheet's ITEM PRICE column, in row
    order: every labelled row but TOTAL with a number in that column."""
    price_col = None
    for c in range(2, ws.max_column + 1):
        header = ws.cell(1, c).value
        if isinstance(header, str) and header.strip().upper() == "ITEM PRICE":
            price_col = c
            break
    if price_col is None:
        return []
    out = []
    for r in range(2, ws.max_row + 1):
        label = ws.cell(r, 1).value
        if label is None:
            continue
        label = str(label).strip()
        if not label or label.upper() == "TOTAL":
            continue
        price = ws.cell(r, price_col).value
        if isinstance(price, (int, float)):
            out.append((label, float(price)))
    return out


def may2024_dsr_prices(wb):
    """[(item name, retail price)] from the May 2024 workbook's 23 daily sales
    report sheets ("May 2", "May 3", ...), in sheet and row order.

    Each daily sheet repeats a header row (ITEMS | RETAIL PRICE | PCS SOLD |
    SALES | DISCOUNTED PRICE | ...) before every supplier's block - the very
    first one has the supplier name on its own row above; every later one has
    the supplier name fused into the header row's first cell instead. A TOTAL
    row (blank item cell, "TOTAL" elsewhere) closes each block. Detecting a
    block boundary by column 2 == "RETAIL PRICE" (rather than by column 1)
    handles both forms without caring which one it is."""
    out = []
    for sheet_name in [s for s in wb.sheetnames if s not in MAY_2024_NON_DSR_SHEETS]:
        ws = SheetGrid(wb[sheet_name])
        in_block = False
        for r in range(1, ws.max_row + 1):
            c1, c2 = ws.cell(r, 1).value, ws.cell(r, 2).value
            if isinstance(c2, str) and c2.strip().upper() == "RETAIL PRICE":
                in_block = True
                continue
            if not in_block:
                continue
            label = str(c1).strip() if c1 is not None else ""
            if not label:
                continue  # TOTAL row, or a stray blank
            if isinstance(c2, (int, float)):
                out.append((label, float(c2)))
    return out


def write_rows(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def convert(plan, out_path, prices=None):
    """`plan` is plan_sheets()'s [(workbook path, [sheet names])].

    If `prices` is a dict it also receives the price columns step1 prices
    items from, read here because the workbooks are open anyway (step1 used to
    open every workbook a second time for them, ~40 s of every run):
      "tbs"          [(row label, 'YYYY-MM' or None, price)], plan order
      "may2024_dsr"  [(item name, retail price)]"""
    agg = defaultdict(float)
    meta = {}
    warnings = []
    density_log = []
    per_sheet = []

    for fn, sheet_names in plan:
        wb = openpyxl.load_workbook(fn, read_only=True, data_only=True)
        short = os.path.basename(fn)
        try:
            if prices is not None and short == MAY_2024_WORKBOOK:
                prices.setdefault("may2024_dsr", []).extend(may2024_dsr_prices(wb))

            for sn in sheet_names:
                ws = SheetGrid(wb[sn])
                label = f"{short} :: {sn}"
                count = 0
                for sort_key, date_str, item, qty, supplier in convert_sheet(ws, label, warnings, density_log):
                    key = (date_str, item, supplier)
                    agg[key] += qty
                    meta[key] = sort_key
                    count += 1
                per_sheet.append((short, sn, count))
                if prices is not None:
                    prices.setdefault("tbs", []).extend(
                        (item, sheet_month(ws), price) for item, price in sheet_item_prices(ws))
        finally:
            wb.close()

    rows = sorted(
        ((meta[k], k) for k in agg),
        key=lambda x: (x[0], x[1][2].upper(), x[1][1].upper()),
    )

    # lineterminator="\n" because csv.writer defaults to CRLF, which made this
    # the one committed input a rebuild could not reproduce byte-for-byte - the
    # vault's copy is LF and every rebuild here rewrote it 75,121 bytes larger,
    # one per line, with identical content. 64 of the repo's 93 to_csv calls
    # already pin it; this is the writer that does not go through pandas.
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["Date", "Item", "Total Quantity", "Supplier"])
        for _, (date_str, item, supplier) in rows:
            w.writerow([date_str, item, fmt_qty(agg[(date_str, item, supplier)]), supplier])

    return per_sheet, warnings, density_log, len(agg)


def input_fingerprint(files):
    """One hash over everything the outputs depend on: each workbook's name and
    contents, and this script itself (a code change must reconvert too)."""
    h = hashlib.sha256()
    with open(__file__, "rb") as f:
        h.update(f.read())
    for path in sorted(files):
        h.update(os.path.basename(path).encode() + b"\0")
        with open(path, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def unchanged_since_last_run(fingerprint):
    """True when the last conversion on this machine read exactly these inputs
    and its outputs are still there. Converting all the workbooks is ~35 s of a
    pipeline run that, on most days, only has new Tally Interface entries -
    which live in the database, not in any workbook."""
    if not all(os.path.exists(p) for p in (OUT_PATH, TBS_PRICES_CSV, MAY_2024_DSR_PRICES_CSV)):
        return False
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f).get("fingerprint") == fingerprint
    except (OSError, ValueError):
        return False


def main():
    files = find_workbooks()
    fingerprint = input_fingerprint(files)
    if "--force" not in sys.argv and unchanged_since_last_run(fingerprint):
        print(f"The tally workbooks in {SRC_DIR}/ have not changed since the last conversion - "
              f"kept {OUT_PATH} and the price files. (--force converts them again.)")
        return

    plan, notes = plan_sheets(files)
    print(f"=== Tally workbooks in {SRC_DIR}/ ===")
    for fn, sheets in plan:
        print(f"  {os.path.basename(fn):45} {len(sheets):3} tally sheet(s)")
    for note in notes:
        print("  NOTE:", note)

    prices = {}
    per_sheet, warnings, density_log, n = convert(plan, OUT_PATH, prices)
    write_rows(TBS_PRICES_CSV, ["Item", "Month", "Price"],
               [(item, month or "", price) for item, month, price in prices.get("tbs", [])])
    write_rows(MAY_2024_DSR_PRICES_CSV, ["Item", "Retail Price"], prices.get("may2024_dsr", []))

    print("\n=== Per-sheet density (real tally dates / calendar-day span) ===")
    for label, n_real, span, density, is_dense in density_log:
        tag = "DENSE (zero-filled)" if is_dense else "sparse (gaps kept as unobserved)"
        print(f"  {label:65} {n_real:3} dates / {span:3} days = {density:.2f}  -> {tag}")

    print("\n=== Per-sheet row counts ===")
    for fname, sheet, c in per_sheet:
        print(f"  {fname:38} | {sheet:22} | {c:6} rows")

    if warnings:
        print("\nNotes / imputations:")
        for wmsg in warnings:
            print("  -", wmsg)

    print(f"\nWrote {n} rows -> {OUT_PATH}")
    print(f"Wrote {len(prices.get('tbs', []))} sheet prices -> {TBS_PRICES_CSV}, "
          f"{len(prices.get('may2024_dsr', []))} May 2024 retail prices -> {MAY_2024_DSR_PRICES_CSV}")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fingerprint, "converted_at":
                   datetime.datetime.now().isoformat(timespec="seconds"),
                   "workbooks": [os.path.basename(p) for p in files]}, f, indent=1)


if __name__ == "__main__":
    main()
