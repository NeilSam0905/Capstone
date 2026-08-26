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

# Column widths (mm) across the 190mm printable width of A4 at 10mm margins.
COL_ITEM, COL_QTY, COL_PRICE, COL_TOTAL = 96, 24, 34, 36

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
        super().__init__(orientation="P", unit="mm", format="A4")
        self.month = month
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(10, 10, 10)
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
        self.line(10, y, 200, y)
        self.set_y(y + 4)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*LINE)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_y(-11)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GREY)
        self.cell(95, 5, _txt("Unit counts only - not an invoice or official receipt."))
        self.cell(95, 5, _txt(f"Page {self.page_no()} of {{nb}}"), align="R")


def _supplier_block(pdf, entry):
    """One supplier: dark name bar, item table, gold subtotal bar — the same
    three-part shape the Batch Sales Report screen uses, so the printed and
    on-screen versions are recognisably one document."""
    # Keep the bar with at least a couple of rows rather than stranding it at
    # the foot of a page.
    if pdf.get_y() > 250:
        pdf.add_page()

    pdf.set_fill_color(*INK)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 7, "  " + _fit(pdf, entry["supplier"], 186),
             new_x="LMARGIN", new_y="NEXT", fill=True)

    pdf.set_fill_color(244, 243, 239)
    pdf.set_text_color(*GREY)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(COL_ITEM, 6, _txt("  ITEM"), fill=True)
    pdf.cell(COL_QTY, 6, _txt("QUANTITY  "), align="R", fill=True)
    pdf.cell(COL_PRICE, 6, _txt("UNIT PRICE  "), align="R", fill=True)
    pdf.cell(COL_TOTAL, 6, _txt("LINE TOTAL  "), align="R", fill=True,
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.1)
    for item in entry["items"]:
        if pdf.get_y() > 268:
            pdf.add_page()
        pdf.set_text_color(*INK)
        # Truncate rather than wrap: one line per item keeps the row grid
        # aligned with the quantity column, which is what gets checked.
        name = _fit(pdf, item["item_name"], COL_ITEM - 6)
        pdf.cell(COL_ITEM, 5.6, "  " + name, border="B")
        pdf.cell(COL_QTY, 5.6, f"{item['quantity']:,}  ", align="R", border="B")
        if item["unit_price_php"] is None:
            pdf.set_text_color(*GREY)
            pdf.cell(COL_PRICE, 5.6, _txt("no price  "), align="R", border="B")
            pdf.cell(COL_TOTAL, 5.6, _txt("-  "), align="R", border="B",
                     new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(COL_PRICE, 5.6, _txt(_php(item["unit_price_php"]) + "  "), align="R", border="B")
            pdf.cell(COL_TOTAL, 5.6, _txt(_php(item["line_total"]) + "  "), align="R", border="B",
                     new_x="LMARGIN", new_y="NEXT")

    if pdf.get_y() > 268:
        pdf.add_page()
    pdf.set_fill_color(*GOLD)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 8.5)
    n = len(entry["items"])
    note = f"   ({n} line item{'' if n == 1 else 's'})"
    # Trim the NAME, not the count - the count is the part that gets checked
    # against the tally sheets.
    name = _fit(pdf, f"SUBTOTAL - {entry['supplier']}",
                COL_ITEM + COL_QTY - 6 - pdf.get_string_width(note))
    pdf.cell(COL_ITEM + COL_QTY, 7, "  " + name + note, fill=True)
    pdf.cell(COL_PRICE + COL_TOTAL, 7, _txt(f"{entry['total_units']:,} units  "),
             align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)


def render(report, month):
    """report -> PDF bytes. `report` is build_batch_report()'s output."""
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

    for entry in report:
        _supplier_block(pdf, entry)

    if pdf.get_y() > 255:
        pdf.add_page()
    pdf.set_fill_color(*INK)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(COL_ITEM + COL_QTY, 9, _txt("  GRAND TOTAL UNITS SOLD - ALL SUPPLIERS"), fill=True)
    pdf.set_text_color(*GOLD)
    pdf.cell(COL_PRICE + COL_TOTAL, 9, _txt(f"{grand_units:,} units  "),
             align="R", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)
    pdf.set_text_color(*GREY)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 4.5, _txt(f"Suppliers: {len(report)}    "
                          f"Line items: {sum(len(e['items']) for e in report)}    "
                          f"Prepared for: UST Purchasing Office / Finance Department"),
             new_x="LMARGIN", new_y="NEXT")

    # Signature block: this sheet is the basis of a payment, so it needs
    # somewhere for the two offices to sign off, the way the paper one does.
    pdf.ln(10)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 8.5)
    for label in ("Prepared by (USTore)", "Verified by (Purchasing Office)", "Approved by (Finance)"):
        pdf.cell(63, 5, _txt("_" * 28))
    pdf.ln(5)
    for label in ("Prepared by (USTore)", "Verified by (Purchasing Office)", "Approved by (Finance)"):
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*GREY)
        pdf.cell(63, 4, _txt(label))
    pdf.ln(4)

    return bytes(pdf.output())
