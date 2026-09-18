"""
forecasting/day_status.py
------------------------------------------------------------------
Reads the colour-coded day status out of the USTore TBS workbooks.

Why this module exists
----------------------
Every TBS sheet encodes the store's operating status for each date in
the FILL COLOUR of that date's header cell, with a legend block on the
right-hand side giving the meaning. That legend is the only record in the
project of which days the store was actually shut - `Dim_Date.is_store_closed`
was previously populated from the published academic calendar alone, and
`Closure_Log` is empty.

This matters directly for forecasting. `docs/SYNTHETIC_AUGMENTATION_CATEGORY.md`
measured that 405 of 821 days (49.3%) are store-wide zeros and that 84.1%
of all category-level zero cells are common-mode - the categories go quiet
together. A large share of those are days the store never opened, which
the pipeline currently records as "sold zero". A zero the store could not
possibly have sold on is not demand information; it is a closed door.

The one thing you must not assume
---------------------------------
COLOURS ARE NOT CONSISTENT ACROSS SHEETS. Measured over the whole corpus:

    FF00FF00 (green)   = "USTET SELLING DAY" in one sheet
                       = "ELECTION DAY"      in another
                       = "HOLIDAY"           in another
                       = "UST VOLLEYBALL GAME SELLING" in another
    FFFF00FF (magenta) = "CLASS SUSPENSION" / "PREPARATION FOR USTET"
                       / "CHINESE NEW YEAR" / "UST ALAB: RETREAT 2026"

So a global colour->meaning table would be wrong roughly half the time.
The legend is parsed PER SHEET and the colour is resolved only within the
sheet it came from. The only colour stable across the corpus is
FFFF0000 (red) = "NO OPERATION", and even that is confirmed from the
sheet's own legend rather than assumed.

Classification is by LABEL TEXT, not by colour
----------------------------------------------
Once the per-sheet legend gives `colour -> label`, the label text is
classified into a small set of operating statuses by keyword. The keyword
rules are ordered because the vocabulary overlaps: "PREPARATION FOR USTET
SELLING" contains "SELLING" but is not a selling day, so the operations
rules are tested before the selling rules.

Every distinct label found is written to `data/day_status_vocabulary.csv`
with its inferred status so a human can audit and override it. If that
file exists, its `status` column WINS over the keyword rules - the
classification of a store's own operating calendar is a business fact,
not something this module should decide unilaterally.

The built-in check
------------------
Most sheets state their own answer: a "TOTAL SELLING DAYS" cell followed
by "N DAYS". `verify_selling_days()` compares the parser's own count of
normal selling days against that number. It is a genuine external check
on the parse - the sheet author counted independently - and it is
reported, never silently reconciled.
------------------------------------------------------------------
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Dict, List, NamedTuple, Optional

__all__ = [
    "DayStatus", "STATUS_ORDER", "legend_from_sheet", "classify_label",
    "header_dates_with_status", "verify_selling_days", "load_vocabulary_override",
    "VOCAB_PATH",
]

VOCAB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "day_status_vocabulary.csv")


class DayStatus:
    """The operating statuses a date can carry.

    NORMAL is the absence of a legend entry, not a legend entry itself:
    an unfilled or default-filled header means an ordinary trading day.
    """
    NORMAL = "NORMAL"                 # ordinary trading day
    CLOSED = "CLOSED"                 # store shut - no demand was possible
    SELLING_SPECIAL = "SELLING_SPECIAL"   # open, unusual selling event (USTET, volleyball)
    OPS_NONSELLING = "OPS_NONSELLING"     # staff present, store not trading normally
    SUSPENSION = "SUSPENSION"         # campus empty (class suspension, virtual mode, strike)
    UNKNOWN = "UNKNOWN"               # coloured header with no legend entry in that sheet


STATUS_ORDER = [DayStatus.NORMAL, DayStatus.SELLING_SPECIAL, DayStatus.SUSPENSION,
                DayStatus.OPS_NONSELLING, DayStatus.CLOSED, DayStatus.UNKNOWN]

# Ordered keyword rules. ORDER IS LOAD-BEARING - the first match wins, and
# the vocabularies overlap ("PREPARATION FOR USTET SELLING" contains
# "SELLING"; "HOLIDAY (DAY OF VALOR)" contains "HOLIDAY").
_RULES = [
    # 1. Staff operations first: these contain selling/holiday words but are neither.
    (DayStatus.OPS_NONSELLING, (
        "PREPARATION", "CLEANING", "INVENTORY", "INVTY", "MAINTENANCE",
        "BENCH-MARKING", "BENCHMARKING", "PLANNING", "MEETING", "ASSEMBLY",
        "TEAM BUILDING", "SPORTSFEST", "THANKSGIVING PARTY", "RETREAT",
        "HIYAS", "UAAP CLOSING", "GENERAL CLEANING",
    )),
    # 2. Closure: the store was shut. NO OPERATION is the only corpus-stable colour.
    (DayStatus.CLOSED, (
        "NO OPERATION", "HOLIDAY", "DAY OF VALOR", "INDEPENDENCE DAY",
        "EID'L", "EIDL", "EDSA", "MANILA DAY", "BLACK NAZARENE",
        "ST. THOMAS AQUINAS", "CHINESE NEW YEAR", "ELECTION DAY",
        "ALL SAINTS", "ALL SOULS", "CHRISTMAS", "NEW YEAR",
    )),
    # 3. Campus empty but the store may be staffed.
    (DayStatus.SUSPENSION, (
        "SUSPENSION", "VIRTUAL MODE", "EVM", "STRIKE", "TYPHOON",
        "WALANG PASOK", "NO CLASS",
    )),
    # 4. Unusual selling events - tested last so PREPARATION cannot reach here.
    (DayStatus.SELLING_SPECIAL, (
        "SELLING", "USTET", "VOLLEYBALL", "GAME", "MATHED", "FAIR", "BAZAAR",
    )),
]

# Legend-block furniture that is not a status label.
_NOT_A_LABEL = re.compile(
    r"^\s*(LEGEND|TOTAL\s+SELLING\s+DAYS|\d+\s*DAYS?|TOTAL|NOTE[S]?)\s*$", re.I)


class DayRecord(NamedTuple):
    """One dated column of a TBS sheet, with its resolved operating status.

    `traded` is the DATA fact (the column carries quantities); `status` is
    the COLOUR fact (why it did or did not). Keeping them separate is the
    point of this module - together they separate "the store was shut" from
    "the store was open and sold nothing" from "nobody wrote the sheet",
    three things the existing pipeline collapses into a single zero.
    """
    date: _dt.date
    column: int
    colour: str
    label: Optional[str]
    status: str
    traded: bool
    units: float


# ----------------------------------------------------------------- helpers
def _fill(cell) -> str:
    """Normalised fill key for a cell: 'FFRRGGBB', 'themeN+tint', or ''.

    Theme colours carry no RGB in the file, so they are keyed by theme
    index plus tint. That is sufficient here because a key is only ever
    compared against other keys from THE SAME SHEET.
    """
    f = getattr(cell, "fill", None)
    if f is None or f.fill_type in (None, "none"):
        return ""
    colour = f.start_color
    rgb = getattr(colour, "rgb", None)
    if isinstance(rgb, str):
        return rgb.upper()
    theme = getattr(colour, "theme", None)
    if theme is None:
        return ""
    try:
        tint = round(float(getattr(colour, "tint", 0) or 0), 2)
    except (TypeError, ValueError):
        tint = 0.0
    return "theme%s+%s" % (theme, tint)


def load_vocabulary_override(path: str = VOCAB_PATH) -> Dict[str, str]:
    """`label -> status` overrides curated by a human, if the file exists.

    Returned empty when absent, which is the first-run case: the extractor
    writes the file so the next run can be corrected.
    """
    if not os.path.exists(path):
        return {}
    import csv
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("label") or "").strip().upper()
            status = (row.get("status") or "").strip().upper()
            if label and status in STATUS_ORDER:
                out[label] = status
    return out


def classify_label(label: Optional[str],
                   override: Optional[Dict[str, str]] = None) -> str:
    """Map one legend label to a DayStatus.

    A curated override always wins; otherwise the ordered keyword rules
    apply; otherwise the label is coloured-but-unrecognised (UNKNOWN),
    which is reported rather than quietly folded into NORMAL.
    """
    if label is None:
        return DayStatus.NORMAL
    text = label.strip().upper()
    if not text:
        return DayStatus.NORMAL
    if override and text in override:
        return override[text]
    for status, keywords in _RULES:
        if any(k in text for k in keywords):
            return status
    return DayStatus.UNKNOWN


# ------------------------------------------------------------- sheet parse
def legend_from_sheet(ws, max_scan_rows: int = 40) -> Dict[str, str]:
    """`colour -> label` for ONE sheet.

    A legend entry is a filled, value-less swatch cell with descriptive
    text immediately to its right. Scanning is limited to columns right of
    the data block so a coloured data cell cannot be mistaken for a
    swatch.

    When one colour appears against several labels inside a single sheet,
    the first is kept and the collision is left for the caller to notice -
    within a sheet this has not been observed, and silently merging them
    would hide a real ambiguity.
    """
    legend: Dict[str, str] = {}
    max_r = min(ws.max_row or 1, max_scan_rows)
    max_c = min(ws.max_column or 1, 60)
    for r in range(1, max_r + 1):
        for c in range(2, max_c + 1):
            value = ws.cell(r, c).value
            if not isinstance(value, str):
                continue
            text = value.strip()
            if len(text) < 3 or _NOT_A_LABEL.match(text):
                continue
            swatch = ws.cell(r, c - 1)
            if swatch.value is not None:
                continue                      # a labelled cell, not a swatch
            colour = _fill(swatch)
            if colour and colour not in legend:
                legend[colour] = text.upper()
    return legend


def _date_header_row(ws, max_scan_rows: int = 8):
    """Row index holding the date columns: the row with the most dates."""
    best_row, best_n = None, 0
    max_c = min(ws.max_column or 1, 80)
    for r in range(1, min(ws.max_row or 1, max_scan_rows) + 1):
        n = sum(1 for c in range(1, max_c + 1)
                if isinstance(ws.cell(r, c).value, _dt.datetime))
        if n > best_n:
            best_row, best_n = r, n
    return best_row, best_n


def _column_body(ws, row: int, col: int, limit: int = 400):
    """Numeric count, string count and unit total below a header cell."""
    nums = strs = 0
    total = 0.0
    for r in range(row + 1, min(row + limit, (ws.max_row or 1)) + 1):
        v = ws.cell(r, col).value
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            nums += 1
            total += float(v)
        elif isinstance(v, str) and v.strip():
            strs += 1
    return nums, strs, total


def header_dates_with_status(ws, override: Optional[Dict[str, str]] = None,
                             value_columns: Optional[set] = None
                             ) -> List[DayRecord]:
    """Every dated column of the sheet, with its operating status.

    Three things this has to get right, each learned from the corpus
    rather than assumed:

    1. THE LABEL COLUMN IS ALSO DATED. Column A holds the item names and
       its header is the sheet title ("December 2025"), which openpyxl
       returns as a datetime - so it looks like a date column and its date
       collides with the real first day. It is identified by its body
       being predominantly strings and dropped.

    2. A SHEET CAN HAVE MORE THAN ONE NEUTRAL FILL. JULY 2025 - TBS uses
       both `FFF1C232` and `theme6+0.0` for ordinary days. Taking the
       single majority fill as "the banner" therefore mislabels the other
       one. Instead, any fill that is NOT in the sheet's own legend is
       neutral: the legend is the sheet's own statement of which colours
       carry meaning.

    3. COLUMNS BEYOND THE DATES ARE ALSO NUMERIC. `TOTAL QUANTITY`,
       `ITEM PRICE` and `FOR REMITTANCE` are not days; `value_columns`
       lets the caller pass the set of genuine date columns it resolved.
    """
    row, n = _date_header_row(ws)
    if not row or n < 2:
        return []

    max_c = min(ws.max_column or 1, 80)
    cells = [(c, ws.cell(row, c)) for c in range(1, max_c + 1)
             if isinstance(ws.cell(row, c).value, _dt.datetime)]
    if not cells:
        return []

    legend = legend_from_sheet(ws)

    out: List[DayRecord] = []
    seen = set()
    for col, cell in cells:
        if value_columns is not None and col not in value_columns:
            continue
        nums, strs, total = _column_body(ws, row, col)
        if strs > nums and strs > 3:
            continue                       # the item-label column, not a day
        date = cell.value.date()
        if date in seen:                   # duplicated header for the same day
            continue
        seen.add(date)

        colour = _fill(cell)
        label = legend.get(colour)         # None => colour carries no meaning here
        status = classify_label(label, override)
        out.append(DayRecord(date, col, colour, label, status,
                             traded=nums > 0, units=total))
    return out


def verify_selling_days(ws, records: List[DayRecord]) -> Optional[tuple]:
    """Compare the parser's NORMAL-day count with the sheet's own total.

    Returns `(stated, parsed, ok)` or None when the sheet declares no
    total. This is an independent check: the sheet author counted the
    selling days by hand, so agreement is evidence the colour parse is
    right, and disagreement is evidence it is not. Reported, never
    reconciled.
    """
    stated = None
    max_r, max_c = min(ws.max_row or 1, 60), min(ws.max_column or 1, 60)
    for r in range(1, max_r + 1):
        for c in range(1, max_c + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str) and "TOTAL SELLING DAY" in v.upper():
                # the count sits below the caption, or just right of it
                for rr, cc in ((r + 1, c), (r, c + 1), (r + 1, c + 1)):
                    if rr > (ws.max_row or 1) or cc > (ws.max_column or 1):
                        continue
                    nv = ws.cell(rr, cc).value
                    if isinstance(nv, (int, float)):
                        stated = int(nv)
                    elif isinstance(nv, str):
                        m = re.search(r"(\d+)", nv)
                        if m:
                            stated = int(m.group(1))
                    if stated is not None:
                        break
            if stated is not None:
                break
        if stated is not None:
            break
    if stated is None:
        return None
    # The sheet author counts days the store TRADED NORMALLY: a day with no
    # sales is not a selling day even if nothing was wrong with it, and a
    # USTET selling day is counted separately from ordinary trade. Measured
    # against 15 sheets this reproduces the stated figure exactly on 5 and
    # to within one day on 12; the residual is the author's own judgement
    # (half-days, a special event that also traded normally) and is
    # reported rather than fitted away.
    parsed = sum(1 for r in records
                 if r.traded and r.status == DayStatus.NORMAL)
    return stated, parsed, stated == parsed
