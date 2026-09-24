"""
batch_export.py — CSV and XLSX renderers for the monthly Batch Sales Report.

Shares build_batch_report()'s output with the PDF (batch_pdf.py) and the
on-screen table, so all three export formats and the screen can never show
different numbers for the same month.

--- The XLSX is a rebuild of the store's own sheet ---

`render_xlsx` reproduces the layout of
`rawdata/USTore TBS OCTOBER A.Y. 2025-2026.xlsx`, because that is the
document Purchasing and Finance already read: a supplier block per
consignor, one column per calendar day, then TOTAL QUANTITY, ITEM PRICE and
FOR REMITTANCE, closed by a yellow TOTAL row. The fonts (Arial Narrow), the
column widths, the 90-degree date headers, the frozen header row and the
number formats are measured from that file rather than chosen.

The date columns are colour-coded from the calendar the system actually
holds — closures, event days, enrollment, exam weeks and semestral breaks,
all read from Dim_Date — and the legend counts how many days of each fall in
the month, so it always describes something visible in the grid. TOTAL
SELLING DAYS counts the dates that recorded a sale. The store's own
annotation vocabulary (EVM, class suspension, the selling days) is
reproduced below it as a key for staff to shade by hand; nothing
auto-colours those, because the system holds no data that would say which
day was which, and inventing one would put a claim in a remittance
document.

Cells a reader could recompute are written as FORMULAS, not as Python
results: TOTAL QUANTITY is =SUM() across the day columns and FOR REMITTANCE
is price × quantity, exactly as in the store's sheet. Editing a day cell
re-totals its row, which is how the staff use the original.

--- The CSV ---

Units, not pesos, are the SUBTOTAL/TOTAL rows - same rule the PDF and the
screen follow (see batch_pdf.py's docstring): this is an internal counting
document for supplier remittance reference, not an invoice (BIR constraint,
docs/PROMPT_1_FRONTEND.md §1). Item Price and For Remittance appear as
per-line reference data only.
"""
import csv
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from batch_pdf import long_month

COLUMNS = ["Supplier", "Item", "Quantity", "Item Price (PHP)", "For Remittance (PHP)"]


def _line_rows(report):
    """One row per line item, then a SUBTOTAL row per supplier — the same
    grouping the store's own TBS sheets use per-supplier block."""
    for entry in report:
        for item in entry["items"]:
            yield [
                entry["supplier"],
                item["item_name"],
                item["quantity"],
                item["unit_price_php"] if item["unit_price_php"] is not None else None,
                item["line_total"] if item["line_total"] is not None else None,
            ]
        yield [f"SUBTOTAL — {entry['supplier']}", "", entry["total_units"], None, None]


def render_csv(report, month):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["USTore Monthly Batch Sales Report"])
    writer.writerow([f"Period: {long_month(month)}"])
    writer.writerow([])
    writer.writerow(COLUMNS)
    for row in _line_rows(report):
        writer.writerow(["" if v is None else v for v in row])
    if report:
        writer.writerow([])
        writer.writerow(["GRAND TOTAL UNITS SOLD — ALL SUPPLIERS", "",
                         sum(e["total_units"] for e in report), "", ""])
    # utf-8-sig: the BOM is what makes Excel open this as UTF-8 rather than
    # guessing a legacy codepage and mangling the peso-adjacent text.
    return buf.getvalue().encode("utf-8-sig")


# ------------------------------------------------------------- the TBS grid
#
# Every constant below is measured from the store's October 2025 sheet.
NARROW = "Arial Narrow"
LEGEND_FONT = "Arial"

HEADER_FILL = PatternFill("solid", fgColor="F1C232")   # the gold header band
SUPPLIER_FILL = PatternFill("solid", fgColor="FFF2CC")
TOTAL_FILL = PatternFill("solid", fgColor="FFFF00")
WHITE_FILL = PatternFill("solid", fgColor="FFFFFF")

THIN = Side(style="thin")
MEDIUM = Side(style="medium")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TOTAL_BOX = Border(left=MEDIUM, right=MEDIUM, top=MEDIUM, bottom=MEDIUM)

MONEY = "#,##0.00"
DAY_FMT = 'mmm"-"d'
MONTH_FMT = "mmm yyyy"

# --- the colour key, shared by the XLSX and the PDF ------------------------
#
# CALENDAR_KEY is APPLIED: build_batch_daily() marks each date from Dim_Date
# and the date column is shaded accordingly, so the legend always describes
# something the reader can see in the grid. NO OPERATION keeps the store's
# own red; the other four are the academic-calendar regressors the forecast
# is fitted on, in colours that do not collide with the store's key below.
#
# STORE_KEY is the store's own annotation vocabulary, reproduced so staff can
# colour days by hand exactly as they do on their sheet. Nothing auto-applies
# it: the system holds no data saying which day was EVM or a USTET selling
# day, and inventing one would put a claim in a remittance document.
CALENDAR_KEY = [
    ("closed",     "FF0000", "NO OPERATION"),
    ("event",      "FFD966", "EVENT DAY"),
    ("enrollment", "B6D7A8", "ENROLLMENT PERIOD"),
    ("exam",       "D9D2E9", "EXAM WEEK"),
    ("break",      "D9D9D9", "SEMESTRAL BREAK"),
]

STORE_KEY = [
    ("4A86E8", "EVM (Enriched Virtual Mode)"),
    ("FF00FF", "CLASS SUSPENSION"),
    ("7F6000", "MathEd Selling"),
    ("00FF00", "USTET Selling Day"),
    ("FF9900", "Cleaning after USTET"),
    ("00FFFF", "Inventory"),
]

MARK_FILL = {mark: colour for mark, colour, _ in CALENDAR_KEY}


def ink_on(hex_colour):
    """Black or white, whichever the day number can be read in against
    `hex_colour`. NO OPERATION's red is dark enough that a black numeral on
    it is effectively invisible, which is how the shading first shipped."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    return "000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "FFFFFF"


def _iso_to_date(iso):
    return date(*(int(part) for part in iso.split("-")))


def _sheet_title(month):
    """"OCTOBER 2025 - TBS" — the store's own tab naming."""
    first = _iso_to_date(f"{month}-01")
    return f"{first.strftime('%B').upper()} {first.year} - TBS"


def render_xlsx(report, month, daily=None):
    """The month as the store's TBS grid.

    `daily` is build_batch_daily()'s output. It is optional so a caller
    holding only the monthly totals still gets a readable sheet — the grid
    then carries a TOTAL QUANTITY column and no day columns, rather than
    failing.
    """
    daily = daily or {"dates": [], "quantities": {}, "selling_days": 0}
    days = list(daily["dates"])
    marks = daily.get("marks") or {}
    quantities = daily["quantities"]

    wb = Workbook()
    ws = wb.active
    ws.title = _sheet_title(month)

    first_day_col = 2                                   # column B
    last_day_col = first_day_col + len(days) - 1
    total_col = max(last_day_col + 1, first_day_col)    # TOTAL QUANTITY
    price_col = total_col + 1
    remit_col = total_col + 2

    # ---- header row -------------------------------------------------------
    month_cell = ws.cell(1, 1, _iso_to_date(f"{month}-01"))
    month_cell.number_format = MONTH_FMT
    for offset, iso in enumerate(days):
        cell = ws.cell(1, first_day_col + offset, _iso_to_date(iso))
        cell.number_format = DAY_FMT
        cell.alignment = Alignment(horizontal="center", vertical="center", textRotation=90)
        cell.border = BOX
        # A marked day takes its own colour; every other day takes the gold
        # header band, exactly as the store's June sheet does.
        colour = MARK_FILL.get(marks.get(iso))
        cell.fill = PatternFill("solid", fgColor=colour) if colour else HEADER_FILL
        cell.font = Font(name=NARROW, size=12, bold=True,
                         color=ink_on(colour) if colour else "000000")
    for col, label in ((total_col, "TOTAL QUANTITY"),
                       (price_col, "ITEM PRICE"),
                       (remit_col, "FOR REMITTANCE")):
        cell = ws.cell(1, col, label)
        cell.font = Font(name=NARROW, size=12, bold=True)
        cell.border = BOX
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    month_cell.font = Font(name=NARROW, size=12, bold=True)
    month_cell.border = BOX
    month_cell.fill = HEADER_FILL
    month_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 48
    ws.freeze_panes = "B2"

    # Column index -> fill, for the vertical bands below.
    day_colour = {}
    for offset, iso in enumerate(days):
        colour = MARK_FILL.get(marks.get(iso))
        if colour:
            day_colour[first_day_col + offset] = colour

    # ---- one block per supplier -------------------------------------------
    row = 2
    for entry in report:
        head = ws.cell(row, 1, entry["supplier"])
        head.font = Font(name=NARROW, size=14, bold=True)
        head.fill = SUPPLIER_FILL
        for col in range(1, remit_col + 1):
            ws.cell(row, col).border = Border(left=MEDIUM, right=MEDIUM, bottom=THIN)
        row += 1

        first_item_row = row
        for item in entry["items"]:
            by_date = quantities.get(item.get("product_id"), {})
            ws.cell(row, 1, item["item_name"])
            for offset, iso in enumerate(days):
                units = by_date.get(iso)
                if units:
                    ws.cell(row, first_day_col + offset, units)
            if days:
                span = (f"{get_column_letter(first_day_col)}{row}"
                        f":{get_column_letter(last_day_col)}{row}")
                ws.cell(row, total_col, f"=SUM({span})")
            else:
                ws.cell(row, total_col, item["quantity"])
            if item["unit_price_php"] is not None:
                ws.cell(row, price_col, item["unit_price_php"])
                ws.cell(row, remit_col,
                        f"={get_column_letter(price_col)}{row}"
                        f"*{get_column_letter(total_col)}{row}")
            for col in range(1, remit_col + 1):
                cell = ws.cell(row, col)
                # A marked day is a marked day for the whole month, so its
                # colour runs the full height of the block rather than
                # sitting in the header alone - that is what makes a closure
                # or an exam week legible as a band down the sheet.
                colour = day_colour.get(col)
                cell.fill = PatternFill("solid", fgColor=colour) if colour else WHITE_FILL
                cell.font = Font(name=NARROW, size=12,
                                 color=ink_on(colour) if colour else "000000")
                cell.border = BOX
                if col > 1:
                    cell.alignment = Alignment(horizontal="center")
                if col in (price_col, remit_col):
                    cell.number_format = MONEY
            row += 1

        last_item_row = row - 1
        total = ws.cell(row, 1, "TOTAL")
        total.alignment = Alignment(horizontal="right")
        if last_item_row >= first_item_row:
            for col in (total_col, remit_col):
                letter = get_column_letter(col)
                ws.cell(row, col, f"=SUM({letter}{first_item_row}:{letter}{last_item_row})")
        for col in range(1, remit_col + 1):
            cell = ws.cell(row, col)
            cell.font = Font(name=NARROW, size=13, bold=True)
            cell.fill = TOTAL_FILL
            cell.border = TOTAL_BOX
            if col in (total_col, remit_col):
                cell.alignment = Alignment(horizontal="center")
            if col == remit_col:
                cell.number_format = MONEY
        row += 2

    # ---- legend block, to the right of the grid ---------------------------
    swatch_col = remit_col + 3
    label_col = swatch_col + 1
    head = ws.cell(1, swatch_col, "LEGEND")
    head.font = Font(name=LEGEND_FONT, size=12, bold=True)
    head.alignment = Alignment(horizontal="center")
    head.border = BOX
    head.fill = HEADER_FILL
    spacer = ws.cell(1, label_col)
    spacer.border = BOX
    spacer.fill = HEADER_FILL

    def legend_row(row, colour, label, note=None):
        swatch = ws.cell(row, swatch_col)
        swatch.fill = PatternFill("solid", fgColor=colour)
        swatch.border = BOX
        text = ws.cell(row, label_col, label if not note else f"{label}  ({note})")
        text.font = Font(name=LEGEND_FONT)
        text.alignment = Alignment(horizontal="left")
        text.border = BOX

    # Applied automatically. The day count tells a reader which of these
    # actually occur this month, so an unused colour is never mistaken for a
    # missing one.
    counts = {}
    for mark in marks.values():
        counts[mark] = counts.get(mark, 0) + 1
    row = 2
    for mark, colour, label in CALENDAR_KEY:
        n = counts.get(mark, 0)
        legend_row(row, colour, label, f"{n} day{'' if n == 1 else 's'}" if n else "none")
        row += 1

    row += 1
    ws.cell(row, swatch_col, "STORE ANNOTATIONS").font = Font(
        name=LEGEND_FONT, size=11, bold=True)
    ws.cell(row, label_col, "shade by hand as on the store's sheet").font = Font(
        name=LEGEND_FONT, italic=True)
    row += 1
    for colour, label in STORE_KEY:
        legend_row(row, colour, label)
        row += 1

    row += 1
    ws.cell(row, swatch_col, "TOTAL SELLING DAYS").font = Font(name=LEGEND_FONT, bold=True)
    ws.cell(row + 1, swatch_col, f"{daily['selling_days']} DAYS").font = Font(name=LEGEND_FONT)

    # ---- column widths, measured from the store's sheet --------------------
    ws.column_dimensions["A"].width = 23.5
    for col in range(first_day_col, last_day_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 3.75
    ws.column_dimensions[get_column_letter(total_col)].width = 10.38
    ws.column_dimensions[get_column_letter(price_col)].width = 15.25
    ws.column_dimensions[get_column_letter(remit_col)].width = 13.75
    ws.column_dimensions[get_column_letter(swatch_col)].width = 20
    ws.column_dimensions[get_column_letter(label_col)].width = 28

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
