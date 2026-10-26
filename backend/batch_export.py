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

--- It prints as the PDF ---

The sheet carries its own print setup (A4 landscape at a fixed scale, the
date row repeated on each page, page breaks that keep a supplier's block
together, the legend as a second print area), and batch_pdf.py draws the PDF
from the same layout functions below - so the downloaded PDF and the Print
Preview are this sheet as Excel prints it.

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
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.pagebreak import Break

from batch_pdf import long_month

COLUMNS = ["Supplier", "Item", "Quantity", "Item Price (PHP)", "For Remittance (PHP)"]

# --- formula injection -----------------------------------------------------
#
# Item and supplier names are typed by staff (Add Item, Rename, the
# vocabulary), and these files are opened in Excel by Purchasing and Finance.
# A name starting with one of these characters would be run as a formula
# there - a remittance document is the last place for that.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """A CSV cell that Excel will show as text: a leading apostrophe on any
    string that would otherwise start a formula (OWASP's CSV-injection
    advice). Numbers pass untouched, so negative quantities stay numbers."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _text_cell(ws, row, col, value):
    """Write a name into the sheet as text, never as a formula. openpyxl makes
    any string starting with "=" a formula; forcing the cell type back to
    string keeps the name exactly as typed, with no apostrophe showing. Only
    for names: the sheet's own =SUM cells are written with ws.cell()."""
    cell = ws.cell(row, col, value)
    if isinstance(value, str) and cell.data_type == "f":
        cell.data_type = "s"
    return cell


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
        writer.writerow(["" if v is None else _csv_safe(v) for v in row])
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


# ------------------------------------------------------------ print layout
#
# The PDF (batch_pdf.py) is drawn from the same layout as the sheet, so the
# downloaded PDF, the Print Preview and the .xlsx printed from Excel are one
# document: same columns, row heights, scale and page breaks. Everything that
# decides where things fall on paper lives here, and both renderers read it.
#
# The sheet prints at a FIXED scale rather than "fit to page": Excel ignores
# manual page breaks under fit-to, and the breaks are what keep a supplier's
# block on one page in both files.

PAPER_W_IN, PAPER_H_IN = 11.69, 8.27            # A4, landscape: the sheet's paper and the PDF's
# The layout is sized to fit Letter too (11 in wide, against A4's 11.69; A4 is
# the shorter of the two), so the sheet prints whole on either: Excel prints
# on whatever paper the printer has loaded, and short bond is common.
FIT_W_IN, FIT_H_IN = 11.0, 8.27
MARGIN_IN = {"left": 0.25, "right": 0.25, "top": 0.75, "bottom": 0.6,
             "header": 0.3, "footer": 0.3}

# Column widths in Excel's character units, measured from the store's sheet
# (the legend's two are ours).
WIDTH = {"item": 23.5, "day": 3.75, "total": 10.38, "price": 15.25, "remit": 13.75,
         "swatch": 25, "label": 36}

# Row heights in points. Set explicitly on the sheet so Excel does not size
# rows from its own font metrics, which the PDF could not know.
ROW_H = {"head": 48.0, "supplier": 18.75, "item": 15.75, "total": 17.25,
         "gap": 15.75, "grand": 18.75, "legend": 15.75, "empty": 15.75}

# A page is filled to this share of its height, and the grid to this share of
# its width. Excel sizes printed columns from the printer's own font metrics,
# a few percent wider than on screen and different from printer to printer;
# without the spare width the last column moves to a page of its own.
_PAGE_FILL = 0.97
_WIDTH_FILL = 0.94


def col_points(width):
    """Printed width of a column at 100%, in points. Excel stores widths in
    characters of the workbook's default font (openpyxl's Calibri 11, seven
    pixels a digit) and draws trunc(7 * width + 0.5) pixels, 96 to the inch."""
    return int(width * 7 + 0.5) * 0.75


def grid_widths(n_days):
    """Character widths of the grid's columns, item to FOR REMITTANCE."""
    return [WIDTH["item"], *[WIDTH["day"]] * n_days,
            WIDTH["total"], WIDTH["price"], WIDTH["remit"]]


def print_scale(n_days):
    """The whole-percent scale that fits the grid across the page, never
    above 100%: what Excel prints at, and what the PDF draws at."""
    avail = (FIT_W_IN - MARGIN_IN["left"] - MARGIN_IN["right"]) * 72
    grid = sum(col_points(w) for w in grid_widths(n_days))
    return min(100, int(avail * _WIDTH_FILL / grid * 100)) / 100


def grid_rows(report):
    """The rows under the header, in sheet order: (kind, entry, item).

    kind is supplier | item | total | gap | grand | empty. The first is sheet
    row 2."""
    rows = []
    if not report:
        return [("empty", None, None)]
    for entry in report:
        rows.append(("supplier", entry, None))
        rows.extend(("item", entry, item) for item in entry["items"])
        rows.append(("total", entry, None))
        rows.append(("gap", entry, None))
    rows.append(("grand", None, None))
    return rows


def page_starts(rows, scale):
    """Sheet rows (1-based) that begin a new page, the header row repeating
    on each. A supplier block that fits on a page is never split: it moves
    to the next page whole. Only a block taller than a page breaks inside."""
    avail = ((FIT_H_IN - MARGIN_IN["top"] - MARGIN_IN["bottom"]) * 72 * _PAGE_FILL
             / scale - ROW_H["head"])

    # blocks of row indexes: a supplier with its items and TOTAL, then its gap
    blocks, current = [], []
    for i, (kind, _, _) in enumerate(rows):
        current.append(i)
        if kind in ("total", "grand", "empty"):
            blocks.append(current)
            current = []
        elif kind == "gap":
            blocks[-1].append(i)     # the gap closes the block before it
            current = []
    if current:
        blocks.append(current)

    starts, used = [], 0.0
    for block in blocks:
        body = [i for i in block if rows[i][0] != "gap"]
        need = sum(ROW_H[rows[i][0]] for i in body)
        if used and used + need > avail and need <= avail:
            starts.append(block[0] + 2)
            used = 0.0
        for i in block:
            h = ROW_H[rows[i][0]]
            if used and used + h > avail:
                starts.append(i + 2)
                used = 0.0
            used += h
    return starts


def legend_rows(marks, selling_days, unpriced):
    """The legend block, one entry per sheet row from row 1:
    (style, colour, text, note). Shared with the PDF's legend page."""
    counts = {}
    for mark in marks.values():
        counts[mark] = counts.get(mark, 0) + 1
    rows = [("head", None, "LEGEND", None)]
    # Applied automatically. The day count tells a reader which of these
    # actually occur this month, so an unused colour is never mistaken for a
    # missing one.
    for mark, colour, label in CALENDAR_KEY:
        n = counts.get(mark, 0)
        rows.append(("key", colour, label, f"{n} day{'' if n == 1 else 's'}" if n else "none"))
    rows.append(("blank", None, None, None))
    rows.append(("title", None, "STORE ANNOTATIONS", "shade by hand as on the store's sheet"))
    rows.extend(("key", colour, label, None) for colour, label in STORE_KEY)
    rows.append(("blank", None, None, None))
    rows.append(("bold", None, "TOTAL SELLING DAYS", None))
    rows.append(("plain", None, f"{selling_days} DAYS", None))
    if unpriced:
        rows.append(("blank", None, None, None))
        rows.append(("bold", None, "NO PRICE ON RECORD", None))
        rows.append(("plain", None, f"{unpriced} line item{'' if unpriced == 1 else 's'}: "
                                    "price and remittance left blank", None))
    return rows


def item_units(item, days, by_date):
    """TOTAL QUANTITY as the sheet's =SUM over the day cells computes it."""
    if not days:
        return item["quantity"]
    return sum(by_date.get(iso) or 0 for iso in days)


def page_header(month):
    return (f"USTore Monthly Batch Sales Report — {long_month(month)}",
            f"Generated {date.today().strftime('%m/%d/%Y')}")


FOOTER_NOTE = "Unit counts only - not an invoice or official receipt."


def render_xlsx(report, month, daily=None):
    """The month as the store's TBS grid, set up to print.

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
    ws.row_dimensions[1].height = ROW_H["head"]
    ws.freeze_panes = "B2"

    # Column index -> fill, for the vertical bands below.
    day_colour = {}
    for offset, iso in enumerate(days):
        colour = MARK_FILL.get(marks.get(iso))
        if colour:
            day_colour[first_day_col + offset] = colour

    # ---- one block per supplier -------------------------------------------
    rows = grid_rows(report)
    first_item_row = None
    total_rows = []
    for i, (kind, entry, item) in enumerate(rows):
        row = i + 2
        ws.row_dimensions[row].height = ROW_H[kind]

        if kind == "empty":
            ws.cell(row, 1, f"No sales were recorded for {long_month(month)}.").font = Font(
                name=NARROW, size=12, italic=True)

        elif kind == "supplier":
            head = _text_cell(ws, row, 1, entry["supplier"])
            head.font = Font(name=NARROW, size=14, bold=True)
            head.fill = SUPPLIER_FILL
            for col in range(1, remit_col + 1):
                ws.cell(row, col).border = Border(left=MEDIUM, right=MEDIUM, bottom=THIN)
            first_item_row = row + 1

        elif kind == "item":
            by_date = quantities.get(item.get("product_id"), {})
            _text_cell(ws, row, 1, item["item_name"])
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

        elif kind in ("total", "grand"):
            label = "TOTAL" if kind == "total" else "GRAND TOTAL — ALL SUPPLIERS"
            total = ws.cell(row, 1, label)
            # TOTAL sits right in column A; the longer grand-total label is
            # left-aligned so it runs on over the empty day cells instead of
            # being cut off at the sheet's left edge.
            total.alignment = Alignment(horizontal="right" if kind == "total" else "left")
            for col in (total_col, remit_col):
                letter = get_column_letter(col)
                if kind == "total" and row - 1 >= first_item_row:
                    ws.cell(row, col, f"=SUM({letter}{first_item_row}:{letter}{row - 1})")
                elif kind == "grand" and total_rows:
                    ws.cell(row, col, "=" + "+".join(f"{letter}{r}" for r in total_rows))
            for col in range(1, remit_col + 1):
                cell = ws.cell(row, col)
                cell.font = Font(name=NARROW, size=13, bold=True)
                cell.fill = TOTAL_FILL if kind == "total" else HEADER_FILL
                cell.border = TOTAL_BOX
                if col in (total_col, remit_col):
                    cell.alignment = Alignment(horizontal="center")
                if col == remit_col:
                    cell.number_format = MONEY
            if kind == "total":
                total_rows.append(row)

    last_grid_row = len(rows) + 1

    # ---- legend block, to the right of the grid ---------------------------
    swatch_col = remit_col + 3
    label_col = swatch_col + 1
    unpriced = sum(1 for e in report for it in e["items"] if it["unit_price_php"] is None)
    legend = legend_rows(marks, daily["selling_days"], unpriced)
    for i, (style, colour, text, note) in enumerate(legend):
        row = i + 1
        if row > last_grid_row:
            ws.row_dimensions[row].height = ROW_H["legend"]
        swatch, label = ws.cell(row, swatch_col), ws.cell(row, label_col)
        if style == "head":
            swatch.value = text
            swatch.font = Font(name=LEGEND_FONT, size=12, bold=True)
            swatch.alignment = Alignment(horizontal="center", vertical="center")
            for cell in (swatch, label):
                cell.border = BOX
                cell.fill = HEADER_FILL
        elif style == "key":
            swatch.fill = PatternFill("solid", fgColor=colour)
            swatch.border = BOX
            label.value = text if not note else f"{text}  ({note})"
            label.font = Font(name=LEGEND_FONT)
            label.alignment = Alignment(horizontal="left")
            label.border = BOX
        elif style == "title":
            swatch.value = text
            swatch.font = Font(name=LEGEND_FONT, size=11, bold=True)
            label.value = note
            label.font = Font(name=LEGEND_FONT, italic=True)
        elif style in ("bold", "plain"):
            swatch.value = text
            swatch.font = Font(name=LEGEND_FONT, bold=style == "bold")

    # ---- column widths, measured from the store's sheet --------------------
    ws.column_dimensions["A"].width = WIDTH["item"]
    for col in range(first_day_col, last_day_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = WIDTH["day"]
    ws.column_dimensions[get_column_letter(total_col)].width = WIDTH["total"]
    ws.column_dimensions[get_column_letter(price_col)].width = WIDTH["price"]
    ws.column_dimensions[get_column_letter(remit_col)].width = WIDTH["remit"]
    ws.column_dimensions[get_column_letter(swatch_col)].width = WIDTH["swatch"]
    ws.column_dimensions[get_column_letter(label_col)].width = WIDTH["label"]

    # ---- print setup: what the PDF reproduces -----------------------------
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.scale = int(round(print_scale(len(days)) * 100))
    ws.page_margins = PageMargins(**MARGIN_IN)
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = "1:1"
    # The grid, then the legend on a page of its own.
    ws.print_area = [f"A1:{get_column_letter(remit_col)}{last_grid_row}",
                     f"{get_column_letter(swatch_col)}1:{get_column_letter(label_col)}{len(legend)}"]
    for start in page_starts(rows, print_scale(len(days))):
        ws.row_breaks.append(Break(id=start - 1))     # a break above `start`
    left, right = page_header(month)
    ws.oddHeader.left.text, ws.oddHeader.left.font, ws.oddHeader.left.size = left, "Arial,Bold", 9
    ws.oddHeader.right.text, ws.oddHeader.right.font, ws.oddHeader.right.size = right, "Arial", 8
    ws.oddFooter.left.text, ws.oddFooter.left.font, ws.oddFooter.left.size = FOOTER_NOTE, "Arial", 8
    ws.oddFooter.right.text, ws.oddFooter.right.font, ws.oddFooter.right.size = (
        "Page &P of &N", "Arial", 8)
    ws.HeaderFooter.scaleWithDoc = False

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
