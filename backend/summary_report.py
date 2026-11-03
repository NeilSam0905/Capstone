"""
summary_report.py - the Summary Report: an academic year, a term or a quarter
in a page or two, for the people the store reports to.

The Batch Sales Report (batch_pdf.py) is the store's tally sheet line by line,
for Purchasing and Finance to pay suppliers from. Staff asked for something to
hand upward instead (client consultation, Nov 2026): how the store did over a
school year or a quarter, whether it reached its sales goal, and whether the
new items sold. So this is short on purpose - a few figures and small tables.

**Periods.** An academic year runs August 1 to July 31, as the terms in
Dim_Date do (First Term from August 1, Special Term to July 31). A term is a
Dim_Date semester_id, with the dates the school calendar gives it; a quarter
is a calendar quarter. Only periods with tallied sales are offered.

**Pesos** are units x each item's listed price, as on the Overview. An item
with no price adds units but no pesos, and the report says how many units
that leaves out.

**Like for like.** A period still running is measured to the last tallied day
and set against the same days a year earlier, never against a whole year.
Where the records do not reach back a year there is no comparison, rather
than one against a part of a period.

**The goal** is the store's own figure, in pesos, entered per period on the
screen (Sales_Goal, backend/db.py). Nothing here makes one up.

**Gaps in the records.** Some months were barely tallied (August and
September 2024 have 5 and 3 days on record; June and July 2024 none). A month
a year earlier is compared only where at least COMPARABLE_SHARE as many days
were recorded as now; otherwise the report says its records are incomplete
instead of showing a rise of several hundred percent.

**A new item** is one that first appears on the tally sheet during the period
(its first Fact_Sales row, sold or not). The items already listed on the day
the records begin are not new, only the first ones on record.
"""
import re
from datetime import date, datetime, timedelta

from fpdf import FPDF

import batch_pdf
import catalog
import db as dbmod

KINDS = ("year", "term", "quarter")
TERM_NAMES = {"T1": "First Term", "T2": "Second Term", "ST": "Special Term"}
TERM_RE = re.compile(r"^AY(\d{2})(\d{2})-(T1|T2|ST)$")
QUARTER_NAMES = {1: "1st Quarter", 2: "2nd Quarter", 3: "3rd Quarter", 4: "4th Quarter"}
TOP_ITEMS = 10
# A year-earlier figure is compared only where at least this share as many
# days were recorded then as now (see "Gaps in the records" above), and a
# projection only from a year whose every month has MIN_MONTH_DAYS recorded.
COMPARABLE_SHARE = 0.8
MIN_MONTH_DAYS = 10
# The PDF lists every new item that has not sold - those are the ones to act
# on - but only the best of the ones that have; the screen shows them all.
PDF_NEW_SOLD_ROWS = 10


def _d(iso):
    return date.fromisoformat(iso)


def _year_back(iso):
    """The same day a year earlier; February 29 becomes the 28th."""
    d = _d(iso)
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:
        return d.replace(year=d.year - 1, day=28).isoformat()


def _month_end(month):
    y, m = int(month[:4]), int(month[5:7])
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).isoformat()


def _next_month(month):
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def _ay(first_year):
    return f"{first_year}–{first_year + 1}"


def span_label(start, end):
    """'August 1, 2026 – July 31, 2027'."""
    return batch_pdf._range_label(_d(start), _d(end))


def records_span(c):
    """(first, last) day with tallied rows, as /api/meta's sales_span."""
    r = dbmod.one(c, f"""
        SELECT MIN(d.calendar_date) AS a, MAX(d.calendar_date) AS b
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE {dbmod.SALE_ONLY}
    """)
    return r["a"], r["b"]


# ----------------------------------------------------------------- periods

def periods(c):
    """Every period with tallied sales in it, newest first within each kind:
    [{kind, key, label, start, end, span}]. Keys are what the API takes:
    year '2026' (A.Y. 2026-2027), term 'AY2627-T1', quarter '2026-Q3'."""
    first, last = records_span(c)
    if not first:
        return []
    out = []

    def ay_of(iso):
        d = _d(iso)
        return d.year if d.month >= 8 else d.year - 1

    for y in range(ay_of(last), ay_of(first) - 1, -1):
        out.append({"kind": "year", "key": str(y), "label": f"Academic Year {_ay(y)}",
                    "start": f"{y}-08-01", "end": f"{y + 1}-07-31"})

    for t in dbmod.rows(c, """
        SELECT semester_id AS id, MIN(calendar_date) AS a, MAX(calendar_date) AS b
        FROM Dim_Date WHERE semester_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """):
        m = TERM_RE.match(t["id"])
        if not m or t["b"] < first or t["a"] > last:
            continue
        out.append({"kind": "term", "key": t["id"],
                    "label": f"{TERM_NAMES[m[3]]}, A.Y. {_ay(2000 + int(m[1]))}",
                    "start": t["a"], "end": t["b"]})

    def quarter_of(iso):
        d = _d(iso)
        return d.year, (d.month - 1) // 3 + 1

    (y, q), floor = quarter_of(last), quarter_of(first)
    while (y, q) >= floor:
        m0 = 3 * q - 2
        out.append({"kind": "quarter", "key": f"{y}-Q{q}", "label": f"{QUARTER_NAMES[q]} {y}",
                    "start": f"{y}-{m0:02d}-01", "end": _month_end(f"{y}-{m0 + 2:02d}")})
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)

    for p in out:
        p["span"] = span_label(p["start"], p["end"])
    return out


def find(c, kind, key):
    return next((p for p in periods(c) if p["kind"] == kind and p["key"] == key), None)


# -------------------------------------------------------------------- goal

def get_goal(c, kind, key):
    c.execute(dbmod.SALES_GOAL_DDL)   # in case startup could not create it
    return dbmod.one(c, "SELECT goal_php, set_by, set_at FROM Sales_Goal "
                        "WHERE period_kind = ? AND period_key = ?", (kind, key))


# ----------------------------------------------------------------- figures

def _units(c, a, b):
    """{product_id: {'YYYY-MM': units}} for every item on the tally sheet
    between a and b - an item listed but not sold carries 0."""
    out = {}
    for r in dbmod.rows(c, f"""
        SELECT f.product_id, substr(d.calendar_date, 1, 7) AS month, SUM(f.quantity_sold) AS units
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE d.calendar_date BETWEEN ? AND ? AND {dbmod.SALE_ONLY}
        GROUP BY 1, 2
    """, (a, b)):
        out.setdefault(r["product_id"], {})[r["month"]] = r["units"]
    return out


def _selling_days(c, a, b):
    return dbmod.one(c, f"""
        SELECT COUNT(DISTINCT f.date_id) AS n
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE d.calendar_date BETWEEN ? AND ? AND {dbmod.SALE_ONLY} AND f.quantity_sold > 0
    """, (a, b))["n"]


def _recorded_days(c, a, b):
    """{'YYYY-MM': days with anything on the tally sheet} between a and b."""
    return {r["month"]: r["n"] for r in dbmod.rows(c, f"""
        SELECT substr(d.calendar_date, 1, 7) AS month, COUNT(DISTINCT d.calendar_date) AS n
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE d.calendar_date BETWEEN ? AND ? AND {dbmod.SALE_ONLY}
        GROUP BY 1
    """, (a, b))}


def _comparable(then_days, now_days):
    return then_days > 0 and then_days >= COMPARABLE_SHARE * now_days


def _totals(units, price):
    t = {"php": 0.0, "units": 0, "items_sold": 0, "unpriced_items": 0, "unpriced_units": 0}
    for pid, months in units.items():
        u = sum(months.values())
        if u <= 0:
            continue
        t["items_sold"] += 1
        t["units"] += u
        p = price(pid)
        if p is None:
            t["unpriced_items"] += 1
            t["unpriced_units"] += u
        else:
            t["php"] += u * p
    t["php"] = round(t["php"], 2)
    return t


def _by_month(units, price):
    out = {}
    for pid, months in units.items():
        p = price(pid)
        for m, u in months.items():
            e = out.setdefault(m, {"units": 0, "php": 0.0})
            e["units"] += u
            if p is not None:
                e["php"] += u * p
    return out


def _by_category(units, price, category):
    out = {}
    for pid, months in units.items():
        u = sum(months.values())
        if u <= 0:
            continue
        e = out.setdefault(category(pid), {"units": 0, "php": 0.0})
        e["units"] += u
        p = price(pid)
        if p is not None:
            e["php"] += u * p
    return out


def build(c, period):
    """The report for one period (an entry of periods()), as the screen and
    the PDF both show it."""
    first, last = records_span(c)
    start, end = max(period["start"], first), min(period["end"], last)
    in_progress = period["end"] > last
    cat = {p["product_id"]: p for p in catalog.compute_catalog(c)}

    def price(pid):
        return (cat.get(pid) or {}).get("unit_price_php")

    def category(pid):
        return (cat.get(pid) or {}).get("category") or "Uncategorised"

    cur = _units(c, start, end)
    days = _recorded_days(c, start, end)
    totals = {**_totals(cur, price), "selling_days": _selling_days(c, start, end),
              "recorded_days": sum(days.values()), "listed": len(cur)}

    # The same days a year earlier, if the records reach back that far.
    ly_start, ly_end = _year_back(start), _year_back(end)
    # Its totals are compared only if every month tallied now was tallied
    # about as fully then: one sparse month would inflate the whole change.
    prev = _units(c, ly_start, ly_end) if ly_start >= first else None
    last_year, ly_days = None, {}
    if prev is not None:
        ly_days = _recorded_days(c, ly_start, ly_end)
        last_year = {**_totals(prev, price), "from": ly_start, "to": ly_end,
                     "recorded_days": sum(ly_days.values()),
                     "complete": all(_comparable(ly_days.get(f"{int(m[:4]) - 1:04d}{m[4:]}", 0), n)
                                     for m, n in days.items())}

    # A period still running: where it ends if the rest of it sells as the
    # rest of the same period did last year. Only with last year's whole
    # period on record, every month of it tallied.
    projection = None
    full_a, full_b = _year_back(period["start"]), _year_back(period["end"])
    if (in_progress and last_year and last_year["complete"] and last_year["php"] > 0
            and full_a >= first and full_b <= last):
        full_days = _recorded_days(c, full_a, full_b)
        month, tallied = full_a[:7], True
        while month <= full_b[:7]:
            tallied = tallied and full_days.get(month, 0) >= MIN_MONTH_DAYS
            month = _next_month(month)
        full_php = _totals(_units(c, full_a, full_b), price)["php"] if tallied else 0
        if full_php > 0:
            projection = {"php": round(totals["php"] * full_php / last_year["php"], 2),
                          "last_year_full_php": full_php,
                          "last_year_share_by_now": last_year["php"] / full_php}

    goal = None
    row = get_goal(c, period["kind"], period["key"])
    if row:
        g = row["goal_php"]
        if not in_progress:
            status = "met" if totals["php"] >= g else "not_met"
        elif projection:
            status = "on_track" if projection["php"] >= g else "behind"
        else:
            status = "in_progress"
        goal = {"php": g, "set_by": row["set_by"], "set_at": row["set_at"],
                "reached_share": totals["php"] / g if g else None, "status": status}

    # Each month against the same month a year earlier: last_year_php is
    # None where there is no comparison - no records that far back, or too
    # few days tallied then (last_year_incomplete).
    cur_m = _by_month(cur, price)
    prev_m = _by_month(prev, price) if prev is not None else {}
    months, m = [], start[:7]
    while m <= end[:7]:
        e = cur_m.get(m, {"units": 0, "php": 0.0})
        lm = f"{int(m[:4]) - 1:04d}{m[4:]}"
        ly = prev_m.get(lm, {"units": 0, "php": 0.0})
        ok = prev is not None and _comparable(ly_days.get(lm, 0), days.get(m, 0))
        months.append({"month": m, "from": max(start, f"{m}-01"), "to": min(end, _month_end(m)),
                       "units": e["units"], "php": round(e["php"], 2), "recorded_days": days.get(m, 0),
                       "last_year_php": round(ly["php"], 2) if ok else None,
                       "last_year_recorded_days": ly_days.get(lm, 0) if prev is not None else None,
                       "last_year_incomplete": prev is not None and not ok})
        m = _next_month(m)

    cur_c = _by_category(cur, price, category)
    compare = bool(last_year and last_year["complete"])
    prev_c = _by_category(prev, price, category) if compare else {}
    categories = sorted((
        {"category": name, "units": e["units"], "php": round(e["php"], 2),
         "share": e["php"] / totals["php"] if totals["php"] else 0.0,
         "last_year_php": round(prev_c.get(name, {"php": 0.0})["php"], 2) if compare else None}
        for name, e in cur_c.items()), key=lambda r: (-r["php"], -r["units"]))

    def item(pid, units):
        p = cat.get(pid) or {}
        php = units * p["unit_price_php"] if p.get("unit_price_php") is not None else None
        return {"product_id": pid, "item_name": p.get("item_name"), "category": category(pid),
                "supplier_name": p.get("supplier_name"), "units": units,
                "php": round(php, 2) if php is not None else None}

    sold = [(pid, sum(ms.values())) for pid, ms in cur.items() if sum(ms.values()) > 0]
    ranked = sorted(sold, key=lambda t: (-(t[1] * (price(t[0]) or 0)), -t[1]))
    top_items = [{**item(pid, u), "share": (u * (price(pid) or 0)) / totals["php"] if totals["php"] else 0.0}
                 for pid, u in ranked[:TOP_ITEMS]]

    first_listed = {r["product_id"]: r["first"] for r in dbmod.rows(c, f"""
        SELECT f.product_id, MIN(d.calendar_date) AS first
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE {dbmod.SALE_ONLY} GROUP BY 1
    """)}
    first_sale = {r["product_id"]: r["first"] for r in dbmod.rows(c, f"""
        SELECT f.product_id, MIN(d.calendar_date) AS first
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE d.calendar_date BETWEEN ? AND ? AND {dbmod.SALE_ONLY} AND f.quantity_sold > 0
        GROUP BY 1
    """, (start, end))}
    new = [{**item(pid, sum(cur.get(pid, {}).values())), "first_listed": f,
            "first_sale": first_sale.get(pid)}
           for pid, f in first_listed.items() if start <= f <= end and f != first]
    new_sold = sorted((n for n in new if n["units"] > 0), key=lambda n: (-n["units"], n["item_name"]))
    new_unsold = sorted((n for n in new if n["units"] <= 0), key=lambda n: (n["first_listed"], n["item_name"]))

    return {
        "period": {**period, "from": start, "to": end, "in_progress": in_progress,
                   "records_from": first if period["start"] < first else None,
                   "days": (_d(end) - _d(start)).days + 1,
                   "days_total": (_d(period["end"]) - _d(period["start"])).days + 1},
        "sales_through": last,
        "generated_at": date.today().isoformat(),
        "totals": totals,
        "last_year": last_year,
        "goal": goal,
        "projection": projection,
        "months": months,
        "categories": categories,
        "top_items": top_items,
        "new_items": {"count": len(new), "sold": new_sold, "unsold": new_unsold,
                      "sold_units": sum(n["units"] for n in new_sold),
                      "sold_php": round(sum(n["php"] or 0 for n in new_sold), 2)},
    }


# --------------------------------------------------------------------- PDF
#
# A4 portrait in fpdf2, as the batch report is (see batch_pdf.py for why
# fpdf2). Arial from the Windows Fonts folder, which has the peso sign; on a
# machine without it, Helvetica and "PHP" stand in.

INK, MUTED, LINE = (22, 20, 15), (107, 104, 96), (221, 217, 206)
GOLD, GOLD_WASH = batch_pdf._rgb("F1C232"), batch_pdf._rgb("FFF2CC")
OK, CRIT = (46, 125, 85), (193, 69, 47)
MARGIN, BOTTOM = 15, 16
ROW_H = 6.2


class _Doc(FPDF):
    def __init__(self, title, running_head):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(MARGIN, MARGIN, MARGIN)
        # Off: every block checks its own room first, so a table's header
        # follows it onto the next page instead of being left behind.
        self.set_auto_page_break(False)
        self.set_title(title)
        self.set_creator("USTore Inventory Analytics")
        self.running_head = running_head
        self.unicode = self._load_fonts()
        self.alias_nb_pages()
        self.content_w = self.w - 2 * MARGIN

    def _load_fonts(self):
        folder = batch_pdf._font_dir()
        try:
            for style, name in (("", "arial.ttf"), ("B", "arialbd.ttf"), ("I", "ariali.ttf")):
                self.add_font("arial", style, str(folder / name))
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def use(self, style="", size=9, colour=INK):
        self.set_font("arial" if self.unicode else "Helvetica", style, size)
        self.set_text_color(*colour)

    def t(self, value):
        return str(value) if self.unicode else batch_pdf._txt(value)

    def peso(self, n):
        if n is None:
            return "—" if self.unicode else "-"
        return f"{'₱' if self.unicode else 'PHP '}{n:,.0f}"

    def header(self):
        if self.page_no() == 1:
            return
        self.use("", 7.5, MUTED)
        self.set_xy(MARGIN, 8)
        self.cell(self.content_w, 4, self.t(self.running_head))
        self.set_y(MARGIN)

    def footer(self):
        self.use("", 7.5, MUTED)
        self.set_xy(MARGIN, self.h - 10)
        self.cell(self.content_w / 2, 4, self.t("USTore Summary Report · internal report, "
                                                 "not an invoice or official receipt"))
        self.cell(self.content_w / 2, 4, self.t(f"Page {self.page_no()} of {{nb}}"), align="R")

    # -------------------------------------------------------------- blocks

    def room(self, h):
        """Start a new page unless `h` mm still fit on this one."""
        if self.get_y() + h > self.h - BOTTOM:
            self.add_page()

    def fit(self, text, w):
        text = self.t(text)
        if self.get_string_width(text) <= w:
            return text
        dots = "…" if self.unicode else "..."
        while text and self.get_string_width(text + dots) > w:
            text = text[:-1]
        return text.rstrip() + dots

    def heading(self, text, need=30):
        self.room(need)
        self.ln(4)
        self.use("B", 11.5)
        self.cell(self.content_w, 6, self.t(text), new_x="LMARGIN", new_y="NEXT")
        y = self.get_y() + 0.8
        self.set_draw_color(*GOLD)
        self.set_line_width(0.6)
        self.line(MARGIN, y, MARGIN + self.content_w, y)
        self.set_y(y + 2.5)

    def para(self, text, size=9, colour=INK, style="", h=4.6):
        self.use(style, size, colour)
        self.set_x(MARGIN)
        self.multi_cell(self.content_w, h, self.t(text), new_x="LMARGIN", new_y="NEXT")

    def tiles(self, tiles):
        """[(label, value, note, note_colour)], side by side."""
        gap, h = 3.5, 21
        w = (self.content_w - gap * (len(tiles) - 1)) / len(tiles)
        self.room(h + 2)
        y = self.get_y()
        for i, (label, value, note, colour) in enumerate(tiles):
            x = MARGIN + i * (w + gap)
            self.set_fill_color(250, 248, 242)
            self.set_draw_color(*LINE)
            self.set_line_width(0.25)
            self.rect(x, y, w, h, style="DF")
            self.set_fill_color(*GOLD)
            self.rect(x, y, 1.1, h, style="F")
            self.use("", 7.5, MUTED)
            self.set_xy(x + 3.5, y + 2.5)
            self.cell(w - 5, 4, self.fit(label, w - 5))
            self.use("B", 14)
            self.set_xy(x + 3.5, y + 7.5)
            self.cell(w - 5, 6, self.fit(value, w - 5))
            self.use("", 7.5, colour or MUTED)
            self.set_xy(x + 3.5, y + 14.5)
            self.cell(w - 5, 4, self.fit(note, w - 5))
        self.set_y(y + h + 1)

    def bar(self, share):
        h = 4.2
        self.room(h + 2)
        y = self.get_y() + 1
        self.set_fill_color(236, 233, 224)
        self.rect(MARGIN, y, self.content_w, h, style="F")
        self.set_fill_color(*(OK if share >= 1 else GOLD))
        self.rect(MARGIN, y, self.content_w * max(0.0, min(share, 1.0)), h, style="F")
        self.set_y(y + h + 2)

    def table(self, cols, rows, total=None):
        """cols: [(label, width share, align)]; rows: lists of str or
        (str, colour). Widths are shares of the page width."""
        widths = [self.content_w * c[1] for c in cols]

        def head():
            self.set_fill_color(*GOLD_WASH)
            self.use("B", 8)
            self.set_x(MARGIN)
            for (label, _, align), w in zip(cols, widths):
                self.cell(w, ROW_H, self.fit(label, w - 2), align=align, fill=True)
            self.ln(ROW_H)

        def line(cells, bold=False):
            self.set_x(MARGIN)
            for cell, (_, _, align), w in zip(cells, cols, widths):
                text, colour = cell if isinstance(cell, tuple) else (cell, INK)
                self.use("B" if bold else "", 8.5, colour)
                self.cell(w, ROW_H, self.fit(text, w - 2), align=align)
            self.ln(ROW_H)
            self.set_draw_color(*LINE)
            self.set_line_width(0.2)
            self.line(MARGIN, self.get_y(), MARGIN + self.content_w, self.get_y())

        self.room(ROW_H * min(len(rows) + 1, 4))
        head()
        for r in rows:
            if self.get_y() + ROW_H > self.h - BOTTOM:
                self.add_page()
                head()
            line(r)
        if total:
            self.room(ROW_H)
            line(total, bold=True)
        self.ln(1.5)


def _change(now, then):
    """('+12%', colour) - or 'new' where there was nothing to compare with."""
    if then is None:
        return ("—", MUTED)
    if not then:
        return ("new", OK) if now else ("—", MUTED)
    pct = (now - then) / then * 100
    if round(pct) == 0:
        return ("0%", MUTED)
    sign = "+" if pct > 0 else "−"
    return (f"{sign}{abs(pct):.0f}%", OK if pct > 0 else CRIT)


def _long_date(iso):
    d = _d(iso)
    return f"{batch_pdf.MONTHS[d.month - 1]} {d.day}, {d.year}"


def _short_date(iso):
    d = _d(iso)
    return f"{batch_pdf.MONTHS[d.month - 1][:3]} {d.day}, {d.year}"


def _month_label(row):
    """'October 2026', or 'October 2026 (1–18)' for part of a month."""
    name = batch_pdf.long_month(row["month"])
    a, b = _d(row["from"]), _d(row["to"])
    if a.day == 1 and row["to"] == _month_end(row["month"]):
        return name
    return f"{name} ({a.day}–{b.day})" if a != b else f"{name} ({a.day})"


def peso_text(n):
    return "—" if n is None else f"₱{n:,.0f}"


def goal_lines(report, peso):
    """The goal section as (text, tone) lines - tone ok | crit | None - for
    the PDF and, through the API, the screen. `peso` formats an amount."""
    goal, proj, tot = report["goal"], report["projection"], report["totals"]
    through = _long_date(report["sales_through"])
    if not goal:
        lines = [("No sales goal was set for this period.", None)]
        if proj:
            lines.append((f"If the rest of the period sells as it did last year, it ends near "
                          f"{peso(proj['php'])}.", None))
        return lines
    g, got = goal["php"], tot["php"]
    lines = [(f"Goal {peso(g)}. Reached {peso(got)}, {goal['reached_share'] * 100:.0f}% of the goal"
              + (f" (sales to {through})." if report["period"]["in_progress"] else "."), None)]
    st = goal["status"]
    if st == "met":
        lines.append((f"Goal met, {peso(got - g)} above it.", "ok"))
    elif st == "not_met":
        lines.append((f"Goal not met, {peso(g - got)} short.", "crit"))
    elif st == "on_track":
        lines.append((f"On track. If the rest of the period sells as it did last year, it ends near "
                      f"{peso(proj['php'])}, {proj['php'] / g * 100:.0f}% of the goal.", "ok"))
    elif st == "behind":
        lines.append((f"Behind. If the rest of the period sells as it did last year, it ends near "
                      f"{peso(proj['php'])}, {proj['php'] / g * 100:.0f}% of the goal "
                      f"({peso(g - proj['php'])} short).", "crit"))
    else:
        left = (_d(report["period"]["end"]) - _d(report["sales_through"])).days
        lines.append((f"{peso(max(0.0, g - got))} still to go, with {left} days left in the period.", None))
    return lines


def render_pdf(report, prepared_by=None):
    """build()'s report -> PDF bytes."""
    p, tot, ly = report["period"], report["totals"], report["last_year"]
    doc = _Doc(f"USTore Summary Report - {p['label']}", f"USTore Summary Report — {p['label']}")
    peso = doc.peso
    doc.add_page()

    # ---- title
    doc.use("B", 18)
    doc.cell(doc.content_w, 9, doc.t("USTore Summary Report"), new_x="LMARGIN", new_y="NEXT")
    doc.use("B", 13, (150, 110, 10))
    doc.cell(doc.content_w, 7, doc.t(p["label"]), new_x="LMARGIN", new_y="NEXT")
    doc.para(p["span"], size=9.5, colour=MUTED)
    if p["records_from"]:
        doc.para(f"Records begin {_long_date(p['records_from'])}, so this covers "
                 f"{span_label(p['from'], p['to'])} only.", size=9, colour=MUTED)
    elif p["in_progress"]:
        doc.para(f"In progress: sales recorded to {_long_date(report['sales_through'])} "
                 f"({p['days']} of {p['days_total']} days).", size=9, colour=MUTED)
    who = f" by {prepared_by[:1].upper() + prepared_by[1:]}" if prepared_by else ""
    doc.para(f"Prepared {_long_date(report['generated_at'])}{who}.", size=9, colour=MUTED)

    # ---- at a glance
    compare = bool(ly and ly["complete"])

    def vs(now, then, money=False):
        if not ly:
            return ("No records a year earlier", MUTED)
        if not compare:
            return ("Last year's records incomplete", MUTED)
        text, colour = _change(now, then)
        was = peso(then) if money else f"{then:,}"
        return (f"{text} vs last year ({was})", colour)

    doc.heading("At a glance", need=36)
    doc.tiles([
        ("Sales", peso(tot["php"]), *vs(tot["php"], ly and ly["php"], money=True)),
        ("Units sold", f"{tot['units']:,}", *vs(tot["units"], ly and ly["units"])),
        ("Items that sold", f"{tot['items_sold']:,}", f"of {tot['listed']:,} on the tally sheet", MUTED),
        ("Days with sales", f"{tot['selling_days']:,}", f"of {tot['recorded_days']:,} days recorded", MUTED),
    ])
    if compare:
        doc.para(f"Last year means the same days a year earlier: {span_label(ly['from'], ly['to'])}.",
                 size=8, colour=MUTED, h=4)
    elif ly:
        doc.para(f"No change is shown against last year: part of {span_label(ly['from'], ly['to'])} "
                 "was barely tallied (see Month by month).", size=8, colour=MUTED, h=4)

    # ---- goal
    doc.heading("Sales goal", need=26)
    if report["goal"]:
        doc.bar(report["goal"]["reached_share"] or 0.0)
    for text, tone in goal_lines(report, peso):
        doc.para(text, size=9.5, colour={"ok": OK, "crit": CRIT}.get(tone, INK),
                 style="B" if tone else "")
    if report["projection"] and p["in_progress"]:
        share = report["projection"]["last_year_share_by_now"]
        doc.para(f"By {_long_date(ly['to'])}, last year's period had made {share * 100:.0f}% of its "
                 f"sales ({peso(report['projection']['last_year_full_php'])} in all).",
                 size=8, colour=MUTED, h=4)

    # ---- months
    def month_row(m):
        if not m["recorded_days"]:
            return [_month_label(m), ("no records", MUTED), "", "", ""]
        if m["last_year_incomplete"]:
            then = [("no records" if not m["last_year_recorded_days"] else "incomplete", MUTED), ""]
        else:
            then = [peso(m["last_year_php"]) if m["last_year_php"] is not None else "—",
                    _change(m["php"], m["last_year_php"])]
        return [_month_label(m), f"{m['units']:,}", peso(m["php"]), *then]

    doc.heading("Month by month")
    doc.table(
        [("Month", .34, "L"), ("Units", .14, "R"), ("Sales", .19, "R"),
         ("Last year", .19, "R"), ("Change", .14, "R")],
        [month_row(m) for m in report["months"]],
        total=["Total", f"{tot['units']:,}", peso(tot["php"]), peso(ly["php"]) if compare else "—",
               _change(tot["php"], ly["php"] if compare else None)])
    if any(m["last_year_incomplete"] and m["last_year_recorded_days"]
           for m in report["months"] if m["recorded_days"]):
        doc.para(f"Incomplete: fewer than {COMPARABLE_SHARE:.0%} as many days were tallied in that month "
                 "a year earlier, so it is not compared.", size=8, colour=MUTED, h=4)

    # ---- categories (the year-earlier columns only when there is a comparison)
    doc.heading("Sales by category")
    if compare:
        doc.table(
            [("Category", .32, "L"), ("Units", .12, "R"), ("Sales", .17, "R"), ("Share", .10, "R"),
             ("Last year", .17, "R"), ("Change", .12, "R")],
            [[r["category"], f"{r['units']:,}", peso(r["php"]), f"{r['share'] * 100:.0f}%",
              peso(r["last_year_php"]), _change(r["php"], r["last_year_php"])]
             for r in report["categories"]])
    else:
        doc.table(
            [("Category", .46, "L"), ("Units", .18, "R"), ("Sales", .22, "R"), ("Share", .14, "R")],
            [[r["category"], f"{r['units']:,}", peso(r["php"]), f"{r['share'] * 100:.0f}%"]
             for r in report["categories"]])

    # ---- top items
    doc.heading(f"Top {len(report['top_items'])} items by sales")
    doc.table(
        [("#", .05, "L"), ("Item", .43, "L"), ("Category", .20, "L"), ("Units", .09, "R"),
         ("Sales", .14, "R"), ("Share", .09, "R")],
        [[str(i), r["item_name"], r["category"], f"{r['units']:,}", peso(r["php"]),
          f"{r['share'] * 100:.0f}%" if r["php"] is not None else "—"]
         for i, r in enumerate(report["top_items"], 1)])

    # ---- new items
    new = report["new_items"]
    doc.heading("New items", need=22)
    if not new["count"]:
        doc.para("No new item appeared on the tally sheet during this period.")
    else:
        unsold = len(new["unsold"])
        not_yet = "have not sold yet" if p["in_progress"] else "did not sell during the period"
        doc.para(f"{new['count']} item{'s' if new['count'] != 1 else ''} first appeared on the tally "
                 f"sheet during this period. {len(new['sold'])} sold ({new['sold_units']:,} units, "
                 f"{peso(new['sold_php'])}); {unsold} {not_yet}.")
        doc.ln(1)
        if new["unsold"]:
            doc.room(ROW_H * 3 + 6)
            doc.para("Not sold" + (" yet" if p["in_progress"] else ""), size=9, style="B")
            to = _d(p["to"])
            doc.table(
                [("Item", .37, "L"), ("Category", .17, "L"), ("Supplier", .2, "L"),
                 ("First listed", .14, "R"), ("Days listed", .12, "R")],
                [[r["item_name"], r["category"], r["supplier_name"] or "", _short_date(r["first_listed"]),
                  str((to - _d(r["first_listed"])).days + 1)] for r in new["unsold"]])
        if new["sold"]:
            shown = new["sold"][:PDF_NEW_SOLD_ROWS]
            doc.room(ROW_H * 3 + 6)
            doc.para("Sold" + (f" (top {len(shown)} by units)" if len(new["sold"]) > len(shown) else ""),
                     size=9, style="B")
            doc.table(
                [("Item", .42, "L"), ("First listed", .14, "R"), ("First sale", .14, "R"),
                 ("Units", .12, "R"), ("Sales", .18, "R")],
                [[r["item_name"], _short_date(r["first_listed"]), _short_date(r["first_sale"]),
                  f"{r['units']:,}", peso(r["php"])] for r in shown])
            if len(new["sold"]) > len(shown):
                doc.para(f"and {len(new['sold']) - len(shown)} more, listed on the Summary Report screen.",
                         size=8, colour=MUTED, h=4)

    # ---- notes
    doc.heading("How these figures are made", need=24)
    notes = ["Sales in pesos are units sold times each item's listed price."]
    if tot["unpriced_items"]:
        notes.append(f"{tot['unpriced_items']} item{'s' if tot['unpriced_items'] != 1 else ''} that sold "
                     f"({tot['unpriced_units']:,} units) {'have' if tot['unpriced_items'] != 1 else 'has'} "
                     f"no price on record, so {'they count' if tot['unpriced_items'] != 1 else 'it counts'} "
                     f"in units but not in pesos.")
    notes.append("Figures come from the store's tally sheets, recorded through "
                 f"{_long_date(report['sales_through'])}.")
    if report["goal"]:
        g = report["goal"]
        when = f" on {_long_date(g['set_at'][:10])}" if g.get("set_at") else ""
        notes.append(f"The sales goal was entered in the dashboard{when}.")
    for n in notes:
        doc.para(f"•  {n}", size=8, colour=MUTED, h=4)

    return bytes(doc.output())


def filename(period):
    slug = re.sub(r"[^\w]+", "_", period["label"]).strip("_")
    return f"USTore_Summary_Report_{slug}.pdf"


def goal_record(c, kind, key, goal_php, user):
    """Write (or with goal_php None, remove) a period's goal."""
    c.execute(dbmod.SALES_GOAL_DDL)
    if goal_php is None:
        c.execute("DELETE FROM Sales_Goal WHERE period_kind = ? AND period_key = ?", (kind, key))
    else:
        c.execute("""
            INSERT INTO Sales_Goal (period_kind, period_key, goal_php, set_by, set_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (period_kind, period_key) DO UPDATE SET
                goal_php = excluded.goal_php, set_by = excluded.set_by, set_at = excluded.set_at
        """, (kind, key, goal_php, user, datetime.now().isoformat(timespec="seconds")))
    c.commit()
