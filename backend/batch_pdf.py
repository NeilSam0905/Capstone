"""
batch_pdf.py — renders the monthly Batch Sales Report as a PDF.

This is the document the **UST Purchasing Office** and **Finance Department**
work from: Purchasing issues the Purchase Orders and Finance cuts the cheques,
both against the volume the store recorded as sold that month. Producing it by
hand from the tally sheets is part of the administrative burden the project
exists to remove, so "export a PDF" is a deliverable, not a convenience.

**Why fpdf2.** The README recorded this feature as deferred because
"weasyprint/reportlab have native dependencies that are fragile on Windows",
which is true of both: weasyprint needs GTK/Pango/Cairo installed separately,
and reportlab ships a C extension. fpdf2 is pure Python from a plain wheel,
with nothing underneath it - the right dependency for a machine in a store
that must not need a toolchain to print a report.

**Latin-1, and why that is fine here.** fpdf2's built-in Helvetica is a
Latin-1 font; embedding a Unicode TTF would mean shipping a font file and
picking one whose licence permits redistribution. Checked against the live
catalogue: all 539 item and supplier names are Latin-1 already, so nothing is
lost. `_txt()` still degrades anything outside it rather than raising, so a
future item name with a curly apostrophe prints slightly wrong instead of
500-ing the endpoint. The peso sign (U+20B1) is NOT Latin-1, so money is
written "PHP 1,234.00" - which is also the clearer form in a document that
crosses departments.

**Units, not pesos, are the totals.** Subtotals and the grand total are unit
counts. Unit prices appear as per-item reference data for remittance only -
the same rule the screen follows, and the reason this stays inside the BIR
constraint: it is an internal counting document, not an invoice.
"""
from datetime import date

from fpdf import FPDF

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# The page is LANDSCAPE because the document is now the store's TBS grid -
# one column per calendar day - and 31 day columns do not fit across a
# portrait A4. 297mm wide at 8mm margins leaves 281mm of printable width.
PAGE_W = 281.0
COL_ITEM, COL_QTY, COL_PRICE, COL_TOTAL = 62.0, 18.0, 24.0, 28.0
# Whatever the fixed columns leave is shared by the day columns.
DAY_SPAN = PAGE_W - (COL_ITEM + COL_QTY + COL_PRICE + COL_TOTAL)

SUPPLIER_BG = (255, 242, 204)   # the TBS sheet's cream supplier bar
TOTAL_BG = (255, 255, 0)        # its yellow TOTAL row
GRID = (150, 146, 136)
HEADER_BG = (241, 194, 50)      # the TBS sheet's gold header band

INK = (22, 20, 15)          # near-black, the UI's --ink
GOLD = (212, 160, 23)       # the UI's --accent
GREY = (110, 104, 92)
LINE = (215, 211, 200)


def long_month(iso_month):
    """'2026-04' -> 'April 2026'."""
    try:
        y, m = iso_month.split("-")
        return f"{MONTHS[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return iso_month


def _txt(value):
    """Latin-1-safe text. Anything the built-in font cannot encode is dropped
    rather than raising - a malformed character must not fail the report."""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _php(value):
    return f"PHP {value:,.2f}"


def _fit(pdf, text, max_mm):
    """Shorten `text` with an ellipsis until it fits `max_mm` at the current
    font. fpdf2 cells do not clip - text wider than its cell simply prints
    over whatever is beside it, which on the subtotal bar means a long
    supplier name running underneath the unit count. The longest real name
    (ASSOCIATION FOR THE EDUCATIONAL ASSISTANCE OF POOR SEMINARIANS, INC.)
    measures 163mm against a 120mm cell, so this is a live case, not a
    hypothetical one."""
    text = _txt(text)
    if pdf.get_string_width(text) <= max_mm:
        return text
    while len(text) > 1 and pdf.get_string_width(text + "...") > max_mm:
        text = text[:-1]
    return text.rstrip() + "..."


class _Report(FPDF):
    """Page furniture. `header`/`footer` are fpdf2 hooks, called on every page,
    which is what keeps the running head and page numbers on continuation
    pages when a supplier's items spill over."""

    def __init__(self, month):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.month = month
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(8, 8, 8)
        self.set_title(f"USTore Batch Sales Report - {long_month(month)}")
        self.set_creator("USTore Inventory Analytics")

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*INK)
        self.cell(0, 7, _txt("USTore Monthly Batch Sales Report"), new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*GREY)
        self.cell(0, 4.5, _txt("For UST Purchasing Office and Finance Department "
                               "- internal supplier remittance reference"),
                  new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4.5, _txt(f"Period: {long_month(self.month)}    "
                               f"Generated: {date.today().strftime('%m/%d/%Y')}    "
                               f"Source: ustore.db"),
                  new_x="LMARGIN", new_y="NEXT")

        self.set_draw_color(*GOLD)
        self.set_line_width(0.6)
        y = self.get_y() + 2
        self.line(8, y, 289, y)
        self.set_y(y + 4)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*LINE)
        self.set_line_width(0.2)
        self.line(8, self.get_y(), 289, self.get_y())
        self.set_y(-11)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GREY)
        self.cell(140, 5, _txt("Unit counts only - not an invoice or official receipt."))
        self.cell(141, 5, _txt(f"Page {self.page_no()} of {{nb}}"), align="R")


def _rgb(hex_colour):
    """"FFD966" -> (255, 217, 102). The colour key is shared with the .xlsx,
    which stores hex because that is what openpyxl takes."""
    return tuple(int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))


def _day_width(days):
    """Day columns share whatever the fixed columns leave, so a 28-day month
    prints wider columns than a 31-day one instead of leaving a gap."""
    return DAY_SPAN / len(days) if days else 0.0


def _column_header(pdf, days, marks):
    """The TBS header row: ITEM, one cell per calendar day, then the three
    totals columns. Day cells are shaded from the same CALENDAR_KEY the .xlsx
    uses, so the two documents colour the month identically. Reprinted at the
    top of every page, because a day grid with no dates on it is unreadable."""
    from batch_export import CALENDAR_KEY, ink_on

    fills = {mark: colour for mark, colour, _ in CALENDAR_KEY}
    day_w = _day_width(days)

    pdf.set_draw_color(*GRID)
    pdf.set_line_width(0.15)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(*HEADER_BG)
    pdf.cell(COL_ITEM, 7, _txt("  ITEM"), border=1, fill=True)

    pdf.set_font("Helvetica", "B", 5.5)
    for iso in days:
        colour = fills.get(marks.get(iso))
        pdf.set_fill_color(*(_rgb(colour) if colour else HEADER_BG))
        pdf.set_text_color(*(_rgb(ink_on(colour)) if colour else INK))
        pdf.cell(day_w, 7, _txt(str(int(iso[-2:]))), border=1, align="C", fill=True)
    pdf.set_text_color(*INK)

    pdf.set_fill_color(*HEADER_BG)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.cell(COL_QTY, 7, _txt("TOTAL QTY  "), border=1, align="R", fill=True)
    pdf.cell(COL_PRICE, 7, _txt("ITEM PRICE  "), border=1, align="R", fill=True)
    pdf.cell(COL_TOTAL, 7, _txt("FOR REMITTANCE  "), border=1, align="R", fill=True,
             new_x="LMARGIN", new_y="NEXT")


def _day_colours(days, marks):
    """iso date -> (fill rgb, text rgb) for the marked days, so the colour
    runs down the whole column rather than sitting in the header alone."""
    from batch_export import CALENDAR_KEY, ink_on

    fills = {mark: colour for mark, colour, _ in CALENDAR_KEY}
    out = {}
    for iso in days:
        colour = fills.get(marks.get(iso))
        if colour:
            out[iso] = (_rgb(colour), _rgb(ink_on(colour)))
    return out


def _supplier_block(pdf, entry, days, marks, quantities):
    """One supplier: cream name bar, a row per item across the day columns,
    then the yellow TOTAL row - the three-part shape of a TBS supplier block.

    Row height is fixed so the day grid stays square down the page, and a long
    item name is truncated rather than wrapped, for the same reason the
    portrait version did it: one line per item keeps every quantity on the row
    its name is on, which is what gets checked against the tally sheets."""
    day_w = _day_width(days)
    row_h = 4.8
    banded = _day_colours(days, marks)

    if pdf.get_y() > 165:
        pdf.add_page()
        _column_header(pdf, days, marks)

    pdf.set_fill_color(*SUPPLIER_BG)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(PAGE_W, 6, "  " + _fit(pdf, entry["supplier"], PAGE_W - 6),
             border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

    for item in entry["items"]:
        if pdf.get_y() > 178:
            pdf.add_page()
            _column_header(pdf, days, marks)
        by_date = quantities.get(item.get("product_id"), {})

        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.cell(COL_ITEM, row_h, "  " + _fit(pdf, item["item_name"], COL_ITEM - 4), border=1)

        pdf.set_font("Helvetica", "", 5.5)
        for iso in days:
            units = by_date.get(iso)
            band = banded.get(iso)
            if band:
                pdf.set_fill_color(*band[0])
                pdf.set_text_color(*band[1])
            else:
                pdf.set_text_color(*INK)
            pdf.cell(day_w, row_h, _txt(str(units)) if units else "",
                     border=1, align="C", fill=bool(band))
        pdf.set_text_color(*INK)

        pdf.set_font("Helvetica", "B", 6.5)
        pdf.cell(COL_QTY, row_h, _txt(f"{item['quantity']:,}  "), border=1, align="R")
        pdf.set_font("Helvetica", "", 6.5)
        if item["unit_price_php"] is None:
            pdf.set_text_color(*GREY)
            pdf.cell(COL_PRICE, row_h, _txt("no price  "), border=1, align="R")
            pdf.cell(COL_TOTAL, row_h, _txt("-  "), border=1, align="R",
                     new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(COL_PRICE, row_h, _txt(_php(item["unit_price_php"]) + "  "),
                     border=1, align="R")
            pdf.cell(COL_TOTAL, row_h, _txt(_php(item["line_total"]) + "  "),
                     border=1, align="R", new_x="LMARGIN", new_y="NEXT")

    if pdf.get_y() > 178:
        pdf.add_page()
        _column_header(pdf, days, marks)
    pdf.set_fill_color(*TOTAL_BG)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(COL_ITEM + day_w * len(days), 6, _txt("TOTAL  "), border=1, align="R", fill=True)
    pdf.cell(COL_QTY, 6, _txt(f"{entry['total_units']:,}  "), border=1, align="R", fill=True)
    pdf.cell(COL_PRICE, 6, "", border=1, fill=True)
    pdf.cell(COL_TOTAL, 6,
             _txt(_php(entry["subtotal"]) + "  ") if entry["subtotal"] else "",
             border=1, align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)


def _legend(pdf, marks, selling_days):
    """The same two-part key the .xlsx carries: the calendar colours actually
    applied to the grid above, then the store's own annotation vocabulary for
    staff to shade by hand."""
    from batch_export import CALENDAR_KEY, STORE_KEY

    counts = {}
    for mark in marks.values():
        counts[mark] = counts.get(mark, 0) + 1

    if pdf.get_y() > 150:
        pdf.add_page()
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 6, _txt("LEGEND"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_draw_color(*GRID)
    pdf.set_font("Helvetica", "", 7)
    for mark, colour, label in CALENDAR_KEY:
        n = counts.get(mark, 0)
        pdf.set_fill_color(*_rgb(colour))
        pdf.cell(8, 4.2, "", border=1, fill=True)
        pdf.set_text_color(*INK)
        pdf.cell(62, 4.2, _txt("  " + label))
        pdf.set_text_color(*GREY)
        pdf.cell(30, 4.2, _txt(f"{n} day{'' if n == 1 else 's'}" if n else "none"),
                 new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(0, 5, _txt("STORE ANNOTATIONS  (shade by hand as on the store's sheet)"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    for colour, label in STORE_KEY:
        pdf.set_fill_color(*_rgb(colour))
        pdf.cell(8, 4.2, "", border=1, fill=True)
        pdf.set_text_color(*INK)
        pdf.cell(90, 4.2, _txt("  " + label), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(0, 5, _txt(f"TOTAL SELLING DAYS: {selling_days} DAYS"),
             new_x="LMARGIN", new_y="NEXT")


def render(report, month, daily=None):
    """report -> PDF bytes.

    `report` is build_batch_report()'s output and `daily` is
    build_batch_daily()'s. Without `daily` the grid carries no day columns
    and the document falls back to item totals only, rather than failing."""
    daily = daily or {"dates": [], "marks": {}, "quantities": {}, "selling_days": 0}
    days = list(daily["dates"])
    marks = daily.get("marks") or {}
    quantities = daily["quantities"]

    pdf = _Report(month)
    pdf.alias_nb_pages()
    pdf.add_page()

    if not report:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*GREY)
        pdf.cell(0, 8, _txt(f"No sales were recorded for {long_month(month)}."),
                 new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    grand_units = sum(e["total_units"] for e in report)
    unpriced_lines = sum(1 for e in report for i in e["items"] if i["unit_price_php"] is None)

    if unpriced_lines:
        pdf.set_fill_color(251, 241, 220)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(
            0, 5,
            _txt(f"  {unpriced_lines} line item{'' if unpriced_lines == 1 else 's'} in this period have no "
                 "unit price on record, shown as \"no price\". Quantities and subtotals are unaffected "
                 "- those are unit counts."),
            fill=True, new_x="LMARGIN", new_y="NEXT",
        )
        pdf.ln(3)

    _column_header(pdf, days, marks)
    for entry in report:
        _supplier_block(pdf, entry, days, marks, quantities)

    if pdf.get_y() > 175:
        pdf.add_page()
    pdf.set_fill_color(*INK)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(PAGE_W - COL_QTY - COL_PRICE - COL_TOTAL, 8,
             _txt("  GRAND TOTAL UNITS SOLD - ALL SUPPLIERS"), fill=True)
    pdf.set_text_color(*GOLD)
    pdf.cell(COL_QTY + COL_PRICE + COL_TOTAL, 8, _txt(f"{grand_units:,} units  "),
             align="R", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    _legend(pdf, marks, daily["selling_days"])
    pdf.ln(4)
    pdf.set_text_color(*GREY)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 4.5, _txt(f"Suppliers: {len(report)}    "
                          f"Line items: {sum(len(e['items']) for e in report)}    "
                          f"Prepared for: UST Purchasing Office / Finance Department"),
             new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
