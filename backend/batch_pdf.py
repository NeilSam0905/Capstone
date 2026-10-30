"""
batch_pdf.py — renders the Batch Sales Report as a PDF.

This is the document the **UST Purchasing Office** and **Finance Department**
work from: Purchasing issues the Purchase Orders and Finance cuts the cheques,
both against the volume the store recorded as sold that month. Producing it by
hand from the tally sheets is part of the administrative burden the project
exists to remove, so "export a PDF" is a deliverable, not a convenience.

**It is the .xlsx, printed.** The PDF draws the workbook from batch_export.py
as Excel prints it: the same TBS grid (supplier blocks, one column per day,
TOTAL QUANTITY / ITEM PRICE / FOR REMITTANCE), the same fonts, fills, borders
and number formats, and the same page setup - A4 landscape at the sheet's
print scale, the date row repeated on every page, page breaks at the same
rows, and the legend on a page of its own. The layout (column widths, row
heights, scale, page breaks, legend rows) is computed once in batch_export
and read by both, so the two cannot drift apart. Values the sheet holds as
formulas (row totals, remittance, block totals) are worked out here the same
way the formulas would.

Converting the .xlsx with Excel or LibreOffice would give the same picture,
but neither can be assumed on the store's computer; this needs nothing but
Python.

**Why fpdf2.** weasyprint needs GTK/Pango/Cairo installed separately, and
reportlab ships a C extension. fpdf2 is pure Python from a plain wheel, with
nothing underneath it - the right dependency for a machine in a store that
must not need a toolchain to print a report.

**Fonts.** The sheet is set in Arial Narrow (the store's own font), with the
legend in Arial. Both ship with Windows and are embedded from its Fonts
folder. Where they are missing, the PDF falls back to the built-in Helvetica,
which is Latin-1 only: `_txt()` then degrades anything outside it rather than
raising, so an unusual character prints slightly wrong instead of failing the
report.

**Units, not pesos, are the totals.** The document counts units sold. Unit
prices and FOR REMITTANCE appear as per-line reference data, exactly as on
the store's sheet - the reason this stays inside the BIR constraint: it is an
internal counting document, not an invoice.
"""
import os
from datetime import date, timedelta
from pathlib import Path

from fpdf import FPDF

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def long_month(iso_month):
    """'2026-04' -> 'April 2026'."""
    try:
        y, m = iso_month.split("-")
        return f"{MONTHS[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return iso_month


def short_month(iso_month):
    """'2026-07' -> 'Jul 2026', the sheet's "mmm yyyy" header cell."""
    try:
        y, m = iso_month.split("-")
        return f"{MONTHS[int(m) - 1][:3]} {y}"
    except (ValueError, IndexError):
        return iso_month


# ----------------------------------------------------------------- periods
#
# A report covers a PERIOD: a month as 'YYYY-MM' (the store's own TBS sheet),
# a year as 'YYYY', or a span of days as a (start, end) pair of ISO dates -
# one day is (d, d), a week the seven days from the day picked. Every label the documents
# print is derived here, so the screen's period, the PDF's header and the
# sheet's tab can never name different things.

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _day(iso):
    return date(*(int(part) for part in iso.split("-")))


def is_year(period):
    """A year, 'YYYY' - the one period whose grid has a column per month."""
    return isinstance(period, str) and len(period) == 4


def period_span(period):
    """(first, last) ISO dates the period covers, inclusive."""
    if is_year(period):
        return f"{period}-01-01", f"{period}-12-31"
    if isinstance(period, str):
        first = _day(f"{period}-01")
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        return first.isoformat(), (nxt - timedelta(days=1)).isoformat()
    return tuple(period)


def _md(d, short=False):
    """'July 6' / 'Jul 6'."""
    name = MONTHS[d.month - 1]
    return f"{name[:3] if short else name} {d.day}"


def _range_label(start, end, short=False):
    """'July 6 – 12, 2026', 'June 29 – July 5, 2026',
    'December 28, 2026 – January 3, 2027'."""
    if start == end:
        return f"{_md(start, short)}, {start.year}"
    if start.year != end.year:
        return f"{_md(start, short)}, {start.year} – {_md(end, short)}, {end.year}"
    tail = str(end.day) if start.month == end.month else _md(end, short)
    return f"{_md(start, short)} – {tail}, {end.year}"


def period_label(period):
    """How the documents name the period: '2026', 'July 2026',
    'Friday, July 3, 2026' or 'July 6 – 12, 2026'."""
    if is_year(period):
        return period
    if isinstance(period, str):
        return long_month(period)
    start, end = (_day(d) for d in period)
    if start == end:
        return f"{WEEKDAYS[start.weekday()]}, {_range_label(start, end)}"
    return _range_label(start, end)


def period_short(period):
    """The grid's top-left header cell: '2026', 'Jul 2026', 'Jul 3, 2026',
    'Jul 6 – 12, 2026'."""
    if is_year(period):
        return period
    if isinstance(period, str):
        return short_month(period)
    return _range_label(*(_day(d) for d in period), short=True)


def period_slug(period):
    """For filenames: '2026', '2026-07', '2026-07-03',
    '2026-07-06_to_2026-07-12'."""
    if isinstance(period, str):
        return period
    start, end = period
    return start if start == end else f"{start}_to_{end}"


# ------------------------------------------------------------ measuring
#
# The grid is laid out from the width its text actually takes in the sheet's
# font, so the .xlsx and the PDF agree on which item names need a second line
# and which day columns need widening for a three-digit count.

_measurer = None


def text_width(value, size=12, style="", family="narrow"):
    """Width of `value` in points, in the sheet's own font when Windows has
    it (Helvetica, which is wider, otherwise - so the layout errs roomy)."""
    global _measurer
    if _measurer is None:
        _measurer = _Sheet("2000-01")
    _measurer.use(family, style, size)
    return _measurer.get_string_width(_measurer.t(value))


def wrap_lines(value, room, size=12, style="", family="narrow"):
    """`value` broken into lines no wider than `room` points, at spaces where
    it can be and inside a word only when the word alone is too wide - the
    way Excel's wrap_text breaks a cell."""
    lines = []
    for word in str(value).split():
        if lines and text_width(f"{lines[-1]} {word}", size, style, family) <= room:
            lines[-1] += f" {word}"
            continue
        while text_width(word, size, style, family) > room and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and text_width(word[:cut], size, style, family) > room:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        lines.append(word)
    return lines or [""]


def _txt(value):
    """Latin-1-safe text, for the Helvetica fallback only."""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _rgb(hex_colour):
    """"FFD966" -> (255, 217, 102). The colours are shared with the .xlsx,
    which stores hex because that is what openpyxl takes."""
    return tuple(int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))


def _general(n):
    """A number as Excel's General format shows it: 1836, 2.5."""
    if n is None:
        return ""
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def _money(n):
    """#,##0.00"""
    return f"{n:,.2f}"


# ------------------------------------------------------------------ fonts

_FONT_FILES = {
    ("narrow", ""): "ARIALN.TTF", ("narrow", "B"): "ARIALNB.TTF",
    ("narrow", "I"): "ARIALNI.TTF",
    ("arial", ""): "arial.ttf", ("arial", "B"): "arialbd.ttf", ("arial", "I"): "ariali.ttf",
}


def _font_dir():
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


class _Sheet(FPDF):
    """A4 landscape in points, with the sheet's page header and footer."""

    def __init__(self, period):
        super().__init__(orientation="L", unit="pt", format="A4")
        from batch_export import MARGIN_IN, page_header

        self.period = period
        self.margin = {k: v * 72 for k, v in MARGIN_IN.items()}
        self.head_left, self.head_right = page_header(period)
        self.set_auto_page_break(False)
        self.set_margins(self.margin["left"], self.margin["top"], self.margin["right"])
        self.set_title(f"USTore Batch Sales Report - {period_label(period)}")
        self.set_creator("USTore Inventory Analytics")
        self.unicode = self._load_fonts()

    def _load_fonts(self):
        folder = _font_dir()
        try:
            for (family, style), name in _FONT_FILES.items():
                path = folder / name
                if not path.exists():
                    path = folder / name.lower()
                self.add_font(family, style, str(path))
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def use(self, family, style="", size=10):
        """`family` is narrow | arial; Helvetica stands in for both when the
        Windows fonts are not there."""
        if self.unicode:
            self.set_font(family, style, size)
        else:
            self.set_font("Helvetica", style, size)

    def t(self, value):
        return str(value) if self.unicode else _txt(value)

    def header(self):
        self.set_text_color(0, 0, 0)
        y = self.margin["header"] + 9
        self.use("arial", "B", 9)
        self.text(self.margin["left"], y, self.t(self.head_left))
        self.use("arial", "", 8)
        w = self.get_string_width(self.t(self.head_right))
        self.text(self.w - self.margin["right"] - w, y, self.t(self.head_right))

    def footer(self):
        from batch_export import FOOTER_NOTE

        self.set_text_color(0, 0, 0)
        y = self.h - self.margin["footer"]
        self.use("arial", "", 8)
        self.text(self.margin["left"], y, self.t(FOOTER_NOTE))
        # a cell, not text(): fpdf2 fills in the page count {nb} only in cells
        self.set_xy(self.w - self.margin["right"] - 120, y - 7)
        self.cell(120, 9, self.t(f"Page {self.page_no()} of {{nb}}"), align="R")


# ------------------------------------------------------------ cell drawing

THIN_PT, MEDIUM_PT = 0.5, 1.25


class _Grid:
    """Draws cells the way Excel prints them: fill, then borders, then text,
    at the sheet's print scale."""

    def __init__(self, pdf, scale, widths, x0):
        from batch_export import col_points

        self.pdf, self.s = pdf, scale
        self.w = [col_points(w) * scale for w in widths]
        self.x = [x0]
        for w in self.w:
            self.x.append(self.x[-1] + w)

    def fill(self, col, y, h, hex_colour):
        self.pdf.set_fill_color(*_rgb(hex_colour))
        self.pdf.rect(self.x[col], y, self.w[col], h, style="F")

    def box(self, col, y, h, weight=THIN_PT, sides="LRTB"):
        pdf, x, w = self.pdf, self.x[col], self.w[col]
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(weight)
        if "L" in sides:
            pdf.line(x, y, x, y + h)
        if "R" in sides:
            pdf.line(x + w, y, x + w, y + h)
        if "T" in sides:
            pdf.line(x, y, x + w, y)
        if "B" in sides:
            pdf.line(x, y + h, x + w, y + h)

    def reach(self, col, value, family="narrow", style="", size=12, span_to=None):
        """The last column that left-aligned text in `col` runs into. Excel
        leaves out the borders between the cells it runs over, so the text
        reads as one line rather than being cut by every column."""
        if value in (None, ""):
            return col
        pdf = self.pdf
        pdf.use(family, style, size * self.s)
        end = self.x[col] + 1.5 * self.s + pdf.get_string_width(pdf.t(value))
        limit = (span_to or col + 1) - 1
        last = col
        while last < limit and self.x[last + 1] < end:
            last += 1
        return last

    def row_box(self, cols, y, h, weight=THIN_PT, sides="LRTB", run=(0, 0)):
        """box() for each of `cols`, minus the vertical lines inside `run`
        (first, last), the stretch overflowing text covers."""
        first, last = run
        for col in cols:
            own = sides
            if first <= col < last:
                own = own.replace("R", "")
            if first < col <= last:
                own = own.replace("L", "")
            if own:
                self.box(col, y, h, weight, own)

    def text(self, col, y, h, value, family="narrow", style="", size=12,
             align="L", colour="000000", span_to=None):
        """One line of text in a cell, bottom-aligned as Excel does by default.
        Left-aligned text runs on over empty cells to its right up to
        `span_to` (exclusive), as Excel's overflow does, and is clipped there."""
        if value in (None, ""):
            return
        pdf = self.pdf
        pdf.use(family, style, size * self.s)
        pdf.set_text_color(*_rgb(colour))
        value = pdf.t(value)
        pad = 1.5 * self.s
        width = pdf.get_string_width(value)
        base = y + h - 0.25 * size * self.s - 1.5 * self.s
        if align == "C":
            pdf.text(self.x[col] + (self.w[col] - width) / 2, base, value)
        elif align == "R":
            pdf.text(self.x[col] + self.w[col] - pad - width, base, value)
        else:
            # Excel cuts left-aligned text at the edge of the last cell it may
            # run over. Measured and shortened rather than clipped: fpdf2's
            # clip region resets the font in the page without telling it,
            # which scrambles the text drawn after it.
            right = self.x[span_to] if span_to else self.x[col + 1]
            room = right - self.x[col] - pad
            while value and pdf.get_string_width(value) > room:
                value = value[:-1]
            pdf.text(self.x[col] + pad, base, value)

    def centred(self, col, y, h, lines, line_h, first_h, size=12, align="L", colour="000000",
                style=""):
        """Lines of text centred top to bottom in the cell, as a wrapped cell
        with vertical="center" prints: the first line `first_h` points tall
        (the sheet's row height), each further one `line_h`."""
        pdf, s = self.pdf, self.s
        pdf.use("narrow", style, size * s)
        pdf.set_text_color(*_rgb(colour))
        pad = 1.5 * s
        block = (first_h + line_h * (len(lines) - 1)) * s
        base = y + (h - block) / 2 + (first_h - 0.25 * size - 1.5) * s
        for k, line in enumerate(lines):
            line = pdf.t(line)
            width = pdf.get_string_width(line)
            if align == "C":
                x = self.x[col] + (self.w[col] - width) / 2
            elif align == "R":
                x = self.x[col] + self.w[col] - pad - width
            else:
                x = self.x[col] + pad
            pdf.text(x, base + line_h * s * k, line)

    def middle(self, col, y, h, lines, size, colour="000000", family="narrow", style="B"):
        """Centred both ways, several lines - the header labels."""
        pdf = self.pdf
        pdf.use(family, style, size * self.s)
        pdf.set_text_color(*_rgb(colour))
        lh = size * self.s * 1.15
        top = y + (h - lh * len(lines)) / 2
        for k, line in enumerate(lines):
            line = pdf.t(line)
            x = self.x[col] + (self.w[col] - pdf.get_string_width(line)) / 2
            pdf.text(x, top + lh * (k + 1) - 0.25 * size * self.s, line)

    def wrap(self, col, words, size, family="narrow", style="B"):
        """Words on as few lines as fit the column, as wrap_text does."""
        pdf = self.pdf
        pdf.use(family, style, size * self.s)
        room = self.w[col] - 4.5 * self.s
        lines = []
        for word in words.split():
            if lines and pdf.get_string_width(pdf.t(f"{lines[-1]} {word}")) <= room:
                lines[-1] += f" {word}"
            else:
                lines.append(word)
        return lines

    def rotated(self, col, y, h, value, size, colour):
        """The 90-degree date headers, centred in the cell."""
        pdf = self.pdf
        pdf.use("narrow", "B", size * self.s)
        pdf.set_text_color(*_rgb(colour))
        value = pdf.t(value)
        cx, cy = self.x[col] + self.w[col] / 2, y + h / 2
        width = pdf.get_string_width(value)
        with pdf.rotation(90, cx, cy):
            pdf.text(cx - width / 2, cy + 0.35 * size * self.s, value)


# ------------------------------------------------------------------ render

def render(report, period, daily=None):
    """report -> PDF bytes, drawn as the .xlsx prints. `period` is a month
    ('YYYY-MM'), a year ('YYYY') or a (start, end) span of days.

    `report` is build_batch_report()'s output and `daily` is
    build_batch_daily()'s. Without `daily` the grid carries no day columns,
    as the sheet does."""
    from batch_export import (
        CALENDAR_KEY, HEADER_FILL, ITEM_LINE_PT, ROW_H, SUPPLIER_FILL, TOTAL_FILL, WIDTH,
        column_label, fitted_widths, grid_rows, ink_on, item_lines, item_units, legend_rows,
        page_starts, print_scale, row_height, col_points,
    )

    daily = daily or {"dates": [], "marks": {}, "quantities": {}, "selling_days": 0}
    days = list(daily["dates"])
    marks = daily.get("marks") or {}
    quantities = daily["quantities"]
    fills = {mark: colour for mark, colour, _ in CALENDAR_KEY}
    gold = HEADER_FILL.fgColor.rgb[-6:]
    cream = SUPPLIER_FILL.fgColor.rgb[-6:]
    yellow = TOTAL_FILL.fgColor.rgb[-6:]

    widths = fitted_widths(report, daily)
    scale = print_scale(len(days), widths)
    rows = grid_rows(report)
    starts = set(page_starts(rows, scale))

    pdf = _Sheet(period)
    pdf.alias_nb_pages()

    avail = pdf.w - pdf.margin["left"] - pdf.margin["right"]
    grid_w = sum(col_points(w) for w in widths) * scale
    g = _Grid(pdf, scale, widths, pdf.margin["left"] + max(0.0, (avail - grid_w) / 2))
    n = len(widths)
    first_day, total_col, price_col, remit_col = 1, n - 3, n - 2, n - 1

    def header_row(y):
        h = ROW_H["head"] * scale
        for col in range(n):
            g.fill(col, y, h, gold)
        for k, iso in enumerate(days):
            colour = fills.get(marks.get(iso))
            if colour:
                g.fill(first_day + k, y, h, colour)
        for col in range(n):
            g.box(col, y, h)
        g.middle(0, y, h, g.wrap(0, period_short(period), 12), 12)
        for k, iso in enumerate(days):
            colour = fills.get(marks.get(iso))
            g.rotated(first_day + k, y, h, column_label(iso), 12,
                      ink_on(colour) if colour else "000000")
        for col, label in ((total_col, "TOTAL QUANTITY"), (price_col, "ITEM PRICE"),
                           (remit_col, "FOR REMITTANCE")):
            g.middle(col, y, h, g.wrap(col, label, 12), 12)
        return y + h

    def new_page():
        pdf.add_page()
        return header_row(pdf.margin["top"])

    y = new_page()
    block_rows, block_remit, totals = [], [], []
    for i, (kind, entry, item) in enumerate(rows):
        if i + 2 in starts:
            y = new_page()
        h = row_height(kind, item) * scale

        if kind == "empty":
            g.text(0, y, h, f"No sales were recorded for {period_label(period)}.", style="I",
                   span_to=n)

        elif kind == "supplier":
            g.fill(0, y, h, cream)
            run = (0, g.reach(0, entry["supplier"], style="B", size=14, span_to=n))
            g.row_box(range(n), y, h, MEDIUM_PT, "LR", run)
            g.row_box(range(n), y, h, THIN_PT, "B")
            g.text(0, y, h, entry["supplier"], style="B", size=14, span_to=n)
            block_rows, block_remit = [], []

        elif kind == "item":
            by_date = quantities.get(item.get("product_id"), {})
            units = item_units(item, days, by_date)
            price = item["unit_price_php"]
            remit = price * units if price is not None else None
            block_rows.append(units)
            block_remit.append(remit or 0)
            for col in range(n):
                g.fill(col, y, h, "FFFFFF")
            for k, iso in enumerate(days):
                colour = fills.get(marks.get(iso))
                if colour:
                    g.fill(first_day + k, y, h, colour)
            # The name wraps inside its own column, as the sheet's wrap_text
            # cell does, and every figure sits level with its middle.
            g.row_box(range(n), y, h)

            def put(col, lines, align="C", colour="000000"):
                g.centred(col, y, h, lines, ITEM_LINE_PT, ROW_H["item"], align=align,
                          colour=colour)

            put(0, item_lines(item["item_name"]), align="L")
            for k, iso in enumerate(days):
                v = by_date.get(iso)
                if v:
                    colour = fills.get(marks.get(iso))
                    put(first_day + k, [_general(v)], colour=ink_on(colour) if colour else "000000")
            put(total_col, [_general(units)])
            if price is not None:
                put(price_col, [_money(price)])
                put(remit_col, [_money(remit)])

        elif kind in ("total", "grand"):
            if kind == "total":
                units, remit = sum(block_rows), sum(block_remit)
                totals.append((units, remit))
                colour, label = yellow, "TOTAL"
            else:
                units, remit = sum(t[0] for t in totals), sum(t[1] for t in totals)
                colour, label = gold, "GRAND TOTAL \u2014 ALL SUPPLIERS"
            for col in range(n):
                g.fill(col, y, h, colour)
            run = (0, 0) if kind == "total" else (
                0, g.reach(0, label, style="B", size=13, span_to=total_col))
            g.row_box(range(n), y, h, MEDIUM_PT, run=run)
            # TOTAL right-aligned in column A; the grand-total label left, running
            # on over the empty day cells - both as the sheet aligns them
            g.text(0, y, h, label, style="B", size=13,
                   align="R" if kind == "total" else "L", span_to=None if kind == "total" else total_col)
            g.text(total_col, y, h, _general(units), style="B", size=13, align="C")
            g.text(remit_col, y, h, _money(remit), style="B", size=13, align="C")

        y += h

    # ---- the legend, on a page of its own, as the sheet's second print area
    unpriced = sum(1 for e in report for it in e["items"] if it["unit_price_php"] is None)
    legend = legend_rows(marks, daily["selling_days"], unpriced)
    pdf.add_page()
    lw = [WIDTH["swatch"], WIDTH["label"]]
    lg_w = sum(col_points(w) for w in lw) * scale
    lg = _Grid(pdf, scale, lw, pdf.margin["left"] + max(0.0, (avail - lg_w) / 2))
    heights = [ROW_H["head"]] + [row_height(k, it) for k, _, it in rows]
    y = pdf.margin["top"]
    for i, (style, colour, text, note) in enumerate(legend):
        h = (heights[i] if i < len(heights) else ROW_H["legend"]) * scale
        if style == "head":
            for col in (0, 1):
                lg.fill(col, y, h, gold)
                lg.box(col, y, h)
            lg.middle(0, y, h, [text], 12, family="arial")
        elif style == "key":
            lg.fill(0, y, h, colour)
            lg.box(0, y, h)
            lg.box(1, y, h)
            lg.text(1, y, h, text if not note else f"{text}  ({note})", family="arial", size=11)
        elif style == "title":
            lg.text(0, y, h, text, family="arial", style="B", size=11)
            lg.text(1, y, h, note, family="arial", style="I", size=11)
        elif style in ("bold", "plain"):
            lg.text(0, y, h, text, family="arial", style="B" if style == "bold" else "",
                    size=11, span_to=2)
        y += h

    return bytes(pdf.output())
