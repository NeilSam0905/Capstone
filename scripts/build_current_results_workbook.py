"""
scripts/build_current_results_workbook.py
------------------------------------------------------------------
Builds data/USTore_Current_Model_Results.xlsx: a short workbook showing only
the models the app uses right now and how accurate they are, for the
adviser. (The long list of every method tried is in
USTore_Forecast_Testing_Summary.xlsx.)

  Read Me            what the models are, how they were tested, what each column means
  Summary            one row per level (category, item), plus what each column means
  Categories         MASE, MAPE, error %, bias etc. for each of the 12 categories
  Items              the same for each of the 58 forecast items
  Category by Month  forecast vs actual for every category and past 30-day window
  Item by Month      forecast vs actual for every item and past 30-day window
  CICS vs Current    the store's own item list ("FOR CICS STUDENTS.xlsx") as the
                     grouping, against the current 12 categories
  CICS Per Item      that comparison per Fast and Slow item
  CICS Mapping       which CICS section each of the 519 products was placed in

The three CICS tabs are read from data/cics_*.csv - run
scripts/compare_cics_categorization.py first.

Scores are recomputed with the production code (the same walk-forward test
step4 / step4c use, via scripts/test_calendar_adjustment.py) and checked
against the MAE / MASE stored in ustore.db; the script stops if they differ.
Run step4 and step4c first.

Run:  python scripts/build_current_results_workbook.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import test_calendar_adjustment as tca

DB_PATH = os.path.join(ROOT, "ustore.db")
OUT = os.path.join(ROOT, "data", "USTore_Current_Model_Results.xlsx")
DISCONTINUED_DAYS = 365   # same rule as backend/app.py

CAT_MODEL = "6-month average per category, calendar-adjusted"
ITEM_MODEL = "Category share + recent-sales blend, calendar-adjusted"
CAT_MODEL_ID = "RM6_6month_180d+calendar+prophet_shape"
ITEM_MODEL_ID = "topdown_tsb+calendar+prophet_shape"

FONT = "Calibri"
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(name=FONT, size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT, size=14, bold=True, color="1F4E78")
NOTE_FONT = Font(name=FONT, size=10, italic=True, color="595959")
BODY_FONT = Font(name=FONT, size=11)
BOLD_FONT = Font(name=FONT, size=11, bold=True)
GOOD_FILL = PatternFill("solid", fgColor="E2EFDA")
BAD_FILL = PatternFill("solid", fgColor="FCE4D6")
GREY_FILL = PatternFill("solid", fgColor="EDEDED")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


# ---------------------------------------------------------------- scoring
def score(df):
    """Per-key accuracy of the shipped ("capped") version over the past windows."""
    out = []
    for key, g in df.groupby("key"):
        err = g.pred - g.actual
        sc = g.scale[np.isfinite(g.scale) & (g.scale > 0)]
        nz = g.actual > 0
        out.append(dict(
            key=key, windows=len(g),
            avg_actual=g.actual.mean(), avg_forecast=g.pred.mean(),
            mae=err.abs().mean(), rmse=np.sqrt((err ** 2).mean()),
            mase=err.abs().mean() / sc.mean() if len(sc) else np.nan,
            mape=100 * (err[nz].abs() / g.actual[nz]).mean() if nz.any() else np.nan,
            wmape=100 * err.abs().sum() / g.actual.sum() if g.actual.sum() else np.nan,
            bias=100 * err.sum() / g.actual.sum() if g.actual.sum() else np.nan))
    return pd.DataFrame(out).set_index("key")


def summary_row(level, model, s, df):
    err = df.pred - df.actual
    return [level, model, len(s),
            s.mase.mean(), s.mase.median(), f"{int((s.mase < 1).sum())} of {s.mase.notna().sum()}",
            s.mape.mean(), s.mape.median(),
            100 * err.abs().sum() / df.actual.sum(), 100 * err.sum() / df.actual.sum(),
            f"{int(s.beats_naive.sum())} of {s.beats_naive.notna().sum()}"]


def check_against_db(s, stored, label):
    j = s[["mae", "mase"]].join(stored[["mae", "mase"]], rsuffix="_db", how="outer")
    def differs(a, b):   # both blank (e.g. MASE of an item with no sales in any window) counts as equal
        return ((a - b).abs() > 1e-6) | (a.isna() != b.isna())
    bad = j[differs(j.mae, j.mae_db) | differs(j.mase, j.mase_db)]
    if len(bad):
        print(f"{label}: recomputed scores differ from ustore.db for {len(bad)} rows:")
        print(bad)
        raise SystemExit(1)
    print(f"{label}: {len(j)} rows match ustore.db (MAE and MASE)")


# ---------------------------------------------------------------- styling
def title(ws, text, notes):
    ws.cell(row=1, column=1, value=text).font = TITLE_FONT
    for i, n in enumerate(notes):
        ws.cell(row=2 + i, column=1, value=n).font = NOTE_FONT
    return 3 + len(notes)


def table(ws, r0, headers, rows, formats, widths, fills=None):
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=r0, column=j, value=h)
        c.font, c.fill, c.border = HEAD_FONT, HEAD_FILL, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[r0].height = 45
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            if isinstance(v, float) and not np.isfinite(v):
                v = None
            c = ws.cell(row=r0 + 1 + i, column=j + 1, value=v)
            c.font, c.border = BODY_FONT, BORDER
            if j in formats:
                c.number_format = formats[j]
            if fills and (i, j) in fills:
                c.fill = fills[(i, j)]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=r0 + 1, column=2).coordinate
    ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(headers))}{r0 + len(rows)}"
    return r0 + len(rows)


def mase_fills(rows, col, fills=None):
    fills = fills if fills is not None else {}
    for i, row in enumerate(rows):
        v = row[col]
        if v is not None and np.isfinite(v):
            fills[(i, col)] = GOOD_FILL if v < 1 else BAD_FILL if v > 2 else None
    return {k: v for k, v in fills.items() if v is not None}


# ---------------------------------------------------------------- sheets
def sheet_readme(wb, summ, snapshot, fc_start, fc_end, first_win, last_win):
    ws = wb.active
    ws.title = "Read Me"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    ws.cell(row=1, column=1, value="USTore Forecasting: Current Models and Their Accuracy").font = TITLE_FONT
    ws.cell(row=2, column=1, value=f"Data up to {snapshot}. Current forecast covers {fc_start} to {fc_end}.").font = NOTE_FONT
    cat, item = summ[0], summ[1]
    lines = [
        ("MODELS IN USE", None),
        ("Category level", f"{CAT_MODEL}. Each category's next 30 days = its average daily sales over the last 6 months, "
                           "lowered when the school calendar shows semester break or exam days ahead (never raised). "
                           "The 30-day total is then spread over the days using Prophet's daily pattern."),
        ("Item level", f"{ITEM_MODEL}. Half of it is the item's recent share of its category's sales times the category's "
                       "6-month level; the other half is TSB, a method built for items that do not sell every day. "
                       "Same calendar lowering and daily spread as the category level."),
        ("Items covered", "The 58 fast-moving items. The other 461 items (slow and non-moving) sell too rarely to forecast "
                          "and are not scored."),
        ("", None),
        ("HOW IT WAS TESTED", None),
        ("Walk-forward test", f"Pretend it is an earlier date, forecast the next 30 days using only data before it, then compare "
                              f"with what really sold. Repeated for 12 past 30-day windows ({first_win} to {last_win}). "
                              "Scores are on 30-day totals, not single days."),
        ("", None),
        ("WHAT THE COLUMNS MEAN", None),
        ("MASE", "Error compared with a simple guess (\"next 30 days = last 30 days\"). Below 1 = better than the simple guess. "
                 "Green below 1, orange above 2."),
        ("MAPE", "Average % error per window. Windows with zero sales are skipped. Blows up for items that sell only a "
                 "few units (missing 5 units on 1 sold = 500%), so it looks worse than the model really is."),
        ("Error % of units sold (WMAPE)", "Total units missed divided by total units sold. Big sellers count more, so it "
                                           "reflects stock and money better than MAPE."),
        ("Over/under forecast %", "Positive = forecast too high, negative = too low."),
        ("Beats simple guess", "Lower average error than \"next 30 days = last 30 days\"."),
        ("Average / median", "Average = the per-category or per-item scores averaged, every item counting the same. "
                             "Median = the middle score, the typical category or item. Full list on the Summary tab."),
        ("", None),
        ("HEADLINE RESULTS", None),
        ("Category level", f"Average MASE {cat[3]:.2f} (below 1 for {cat[5]} categories). Error {cat[8]:.1f}% of units sold, "
                           f"forecast {cat[9]:+.1f}% vs actual. Beats the simple guess for {cat[10]} categories."),
        ("Item level", f"Average MASE {item[3]:.2f} (median {item[4]:.2f}; below 1 for {item[5]} items). Error {item[8]:.1f}% "
                       f"of units sold, forecast {item[9]:+.1f}% vs actual. Beats the simple guess for {item[10]} items."),
        ("Why items are harder", "Most items sell a few units on scattered days, so any 30-day total is mostly luck. "
                                 "Categories add many items together, which smooths this out."),
        ("", None),
        ("OTHER TABS", "Summary, Categories, Items: the scores. Category by Month, Item by Month: forecast vs actual in "
                       "every past window. The full list of methods tried is in USTore_Forecast_Testing_Summary.xlsx."),
        ("CICS tabs", "CICS vs Current, CICS Per Item, CICS Mapping: the same models re-run with the store's own item list "
                      "(FOR CICS STUDENTS.xlsx) as the categories, to see whether it forecasts better. Includes the slow "
                      "items, which the app does not forecast, scored the same way for this comparison only."),
    ]
    r = 4
    for a, b in lines:
        ca = ws.cell(row=r, column=1, value=a)
        if b is None:
            ca.font = Font(name=FONT, size=11, bold=True, color="1F4E78")
        else:
            ca.font = BOLD_FONT
            cb = ws.cell(row=r, column=2, value=b)
            cb.font = BODY_FONT
            cb.alignment = Alignment(wrap_text=True, vertical="top")
            ca.alignment = Alignment(vertical="top", wrap_text=True)
            ws.row_dimensions[r].height = 15 * max(1, -(-len(b) // 105))
        r += 1


SUMMARY_TERMS = [
    ("How many", "Number of categories or items scored."),
    ("MASE", "The model's error divided by the error of a simple guess (\"next 30 days = last 30 days\"). "
             "Below 1 = the model beat the simple guess; 0.8 means 20% less error. Lower is better."),
    ("Average MASE", "Each category's (or item's) MASE, averaged. Every category or item counts the same, "
                     "so one badly forecast item can pull it up."),
    ("Median MASE", "The middle MASE when all are sorted from best to worst: half are better, half are worse. "
                    "Not pulled up by a few extreme items, so it shows the typical item."),
    ("MASE below 1", "How many categories or items beat the simple guess on MASE."),
    ("MAPE", "Mean Absolute Percentage Error: the forecast's miss as a % of what actually sold, per 30-day window. "
             "Windows with no sales are skipped."),
    ("Average MAPE", "Each category's (or item's) MAPE, averaged. Very high for items that sell only a few units "
                     "(forecasting 6 when 1 sold is a 500% miss), so it looks worse than the model really is."),
    ("Median MAPE", "The middle MAPE when all are sorted: the % error of a typical category or item."),
    ("Error % of units sold (WMAPE)", "All units missed divided by all units sold, adding every category or item "
                                      "together first (\"pooled\"). Big sellers count more, so it reflects stock and "
                                      "money better than MAPE."),
    ("Over/under forecast", "All units forecast minus all units sold, as a % of units sold. Negative = the model "
                            "forecast too low overall; positive = too high."),
    ("Beats simple guess", "How many had a lower average error (in units) than \"next 30 days = last 30 days\"."),
]


def sheet_summary(wb, summ):
    ws = wb.create_sheet("Summary")
    r = title(ws, "Summary: Current Models", [
        "Scores are from 12 past 30-day windows. What each column means is explained below the table."])
    headers = ["Level", "Model in use", "How many", "Average MASE", "Median MASE", "MASE below 1",
               "Average MAPE, %", "Median MAPE, %", "Error % of units sold (WMAPE)",
               "Over/under forecast, %", "Beats simple guess"]
    r = table(ws, r, headers, summ, {3: "0.00", 4: "0.00", 6: "0.0", 7: "0.0", 8: "0.0", 9: "+0.0;-0.0"},
              [28, 50, 9, 11, 11, 11, 11, 11, 14, 13, 12])
    ws.freeze_panes = None
    ws.auto_filter.ref = None

    r += 2
    ws.cell(row=r, column=1, value="WHAT THE COLUMNS MEAN").font = Font(name=FONT, size=11, bold=True, color="1F4E78")
    for term, text in SUMMARY_TERMS:
        r += 1
        ca = ws.cell(row=r, column=1, value=term)
        ca.font, ca.alignment = BOLD_FONT, Alignment(vertical="top", wrap_text=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=11)
        cb = ws.cell(row=r, column=2, value=text)
        cb.font, cb.alignment = BODY_FONT, Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[r].height = 15 * max(1, -(-len(text) // 130))


def sheet_categories(wb, cs):
    ws = wb.create_sheet("Categories")
    r = title(ws, f"Per Category: {CAT_MODEL}", [
        "One row per category, 12 past 30-day windows each. Green MASE = better than the simple guess; orange = more than twice its error.",
        "Next 30 days = the forecast the app currently shows."])
    cs = cs.sort_values("avg_actual", ascending=False)
    headers = ["Category", "Avg units sold per 30 days", "Avg forecast per 30 days", "MASE",
               "Error % of units sold (WMAPE)", "Over/under forecast, %", "Beats simple guess",
               "Next 30 days forecast (units)"]
    rows = [[k, x.avg_actual, x.avg_forecast, x.mase, x.wmape, x.bias,
             "Yes" if x.beats_naive == 1 else "No", x.next_30] for k, x in cs.iterrows()]
    table(ws, r, headers, rows, {1: "0.0", 2: "0.0", 3: "0.00", 4: "0.0", 5: "+0.0;-0.0", 7: "0"},
          [22, 12, 12, 9, 13, 12, 10, 13], mase_fills(rows, 3))


def sheet_items(wb, its):
    ws = wb.create_sheet("Items")
    r = title(ws, f"Per Item: {ITEM_MODEL}", [
        "One row per forecast item, biggest sellers first, 12 past 30-day windows each. Green MASE = better than the simple guess; orange = more than twice its error.",
        f"Grey rows: no sale in the last {DISCONTINUED_DAYS} days (probably no longer stocked). They are forecast near zero and score well, so they are kept in the averages.",
        "One item sold nothing in any test window, so it has no MASE (the simple guess was never wrong); that is why MASE covers 57 items.",
        "Column meanings are on the Summary tab."])
    its = its.sort_values("avg_actual", ascending=False)
    headers = ["Item", "Category", "Avg units sold per 30 days", "Avg forecast per 30 days", "MASE",
               "Error % of units sold (WMAPE)", "Over/under forecast, %", "Beats simple guess", "Last sale",
               "Next 30 days forecast (units)"]
    rows, fills = [], {}
    for i, (k, x) in enumerate(its.iterrows()):
        rows.append([x.item_name, x.category, x.avg_actual, x.avg_forecast, x.mase, x.wmape, x.bias,
                     "Yes" if x.beats_naive == 1 else "No", x.last_sale, x.next_30])
        if x.discontinued:
            for j in range(len(headers)):
                fills[(i, j)] = GREY_FILL
    fills = mase_fills(rows, 4, fills)
    table(ws, r, headers, rows, {2: "0.0", 3: "0.0", 4: "0.00", 5: "0.0", 6: "+0.0;-0.0", 9: "0"},
          [40, 20, 11, 11, 8, 12, 11, 10, 11, 12], fills)


def sheet_by_month(wb, name, heading, df, lead_cols, lead_widths, what):
    ws = wb.create_sheet(name)
    r = title(ws, heading, [
        "Each row: one past 30-day window. Forecast made using only data before the window started.",
        "Error = forecast minus actual (positive = forecast too high). % error is blank when nothing sold. Use the filter arrows to pick one " + what + " or window."])
    headers = lead_cols + ["Window start", "Window end", "Actual units sold", "Forecast units", "Error (units)", "% error"]
    rows = []
    for x in df.itertuples():
        start = pd.Timestamp(x.window_start)
        e = x.pred - x.actual
        rows.append(list(x.lead) + [start.date().isoformat(), (start + pd.Timedelta(days=29)).date().isoformat(),
                                    x.actual, x.pred, e, 100 * e / x.actual if x.actual > 0 else None])
    n = len(lead_cols)
    table(ws, r, headers, rows, {n + 2: "0", n + 3: "0.0", n + 4: "+0.0;-0.0", n + 5: "+0;-0"},
          lead_widths + [12, 12, 11, 11, 11, 10])


def load_cics():
    paths = {k: os.path.join(ROOT, "data", f"cics_{k}.csv") for k in
             ("vs_current_summary", "vs_current_category", "vs_current_items", "category_mapping")}
    missing = [p for p in paths.values() if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"missing {missing} - run scripts/compare_cics_categorization.py first")
    return {k: pd.read_csv(p) for k, p in paths.items()}


def better_fills(rows, col, fills):
    for i, row in enumerate(rows):
        fills[(i, col)] = {"CICS": GOOD_FILL, "Current": BAD_FILL}.get(row[col])
    return {k: v for k, v in fills.items() if v is not None}


def sheet_cics_summary(wb, cics):
    ws = wb.create_sheet("CICS vs Current")
    r = title(ws, "Store Item List (CICS) vs Current 12 Categories: Same Models, Same 12 Windows", [
        "CICS = the store's own sections from FOR CICS STUDENTS.xlsx (17 with sales; Extras/Packaging has no item in our data).",
        "Category level: each grouping's own categories forecast with the 6-month average + calendar. Smaller groups are harder to forecast,",
        "so the fair head-to-head is the ITEM level: the same items, the same actual sales, only the category each item borrows from changes.",
        "Slow items are not forecast by the app; they are scored here the same way as the fast items, for this comparison only.",
        "Difference = CICS minus current (negative = CICS better). 95% range from resampling items: if it spans 0, the two are a tie."])
    s = cics["vs_current_summary"]
    headers = ["Level", "Group of", "How many (current / CICS)", "Current: average MASE", "CICS: average MASE",
               "Current: median MASE", "CICS: median MASE", "Current: error % of units (WMAPE)", "CICS: error % of units (WMAPE)",
               "MASE below 1 (current / CICS)", "Items better with CICS", "Items better with current",
               "Difference in average MASE", "95% range: low", "95% range: high", "Verdict"]
    rows, fills = [], {}
    for cohort, g in s.groupby("cohort", sort=False):
        c, n = g[g.scheme == "current"].iloc[0], g[g.scheme == "cics"].iloc[0]
        if c.level == "category":
            verdict = "Current better" if n.mean_mase > c.mean_mase and n.wmape_pct > c.wmape_pct else "Mixed"
            rows.append(["Category", "categories", f"{int(c.n)} / {int(n.n)}", c.mean_mase, n.mean_mase,
                         c.median_mase, n.median_mase, c.wmape_pct, n.wmape_pct,
                         f"{int(c.mase_below_1)} / {int(n.mase_below_1)}", None, None, n.mean_mase - c.mean_mase,
                         None, None, verdict])
        else:
            tie = c.gap_ci_lo < 0 < c.gap_ci_hi
            verdict = "Tie" if tie else ("CICS better" if c.gap_cics_minus_current < 0 else "Current better")
            rows.append(["Item", cohort, f"{int(c.n)}", c.mean_mase, n.mean_mase, c.median_mase, n.median_mase,
                         c.wmape_pct, n.wmape_pct, f"{int(c.mase_below_1)} / {int(n.mase_below_1)}",
                         int(c.cics_better), int(c.cics_worse), c.gap_cics_minus_current, c.gap_ci_lo, c.gap_ci_hi,
                         verdict])
    r = table(ws, r, headers, rows, {3: "0.000", 4: "0.000", 5: "0.000", 6: "0.000", 7: "0.0", 8: "0.0",
                                     12: "+0.000;-0.000", 13: "+0.000;-0.000", 14: "+0.000;-0.000"},
              [10, 24, 11, 10, 10, 10, 10, 12, 12, 11, 10, 10, 11, 10, 10, 14])
    ws.freeze_panes = None
    ws.auto_filter.ref = None

    # Per-category tables, one per grouping, side by side in reading order.
    cat = cics["vs_current_category"]
    r += 2
    ws.cell(row=r, column=1, value="Per category, each grouping (biggest first). Green MASE below 1, orange above 2.").font = BOLD_FONT
    r += 1
    headers = ["Grouping", "Category", "Avg units sold per 30 days", "MASE", "Error % of units sold (WMAPE)",
               "MAE (units)", "Simple guess MAE (units)", "Beats simple guess"]
    rows = []
    for scheme, label in (("current", "Current"), ("cics", "CICS")):
        g = cat[cat.scheme == scheme].sort_values("mean_actual_30d", ascending=False)
        rows += [[label, x.group, x.mean_actual_30d, x.mase, x.wmape_pct, x.mae, x.naive_mae,
                  "Yes" if x.beats_naive == 1 else "No"] for x in g.itertuples()]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=j, value=h)
        c.font, c.fill, c.border = HEAD_FONT, HEAD_FILL, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[r].height = 45
    fills = mase_fills(rows, 3)
    fmt = {2: "0.0", 3: "0.00", 4: "0.0", 5: "0.0", 6: "0.0"}
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            c = ws.cell(row=r + 1 + i, column=j + 1, value=None if isinstance(v, float) and not np.isfinite(v) else v)
            c.font, c.border = BODY_FONT, BORDER
            if j in fmt:
                c.number_format = fmt[j]
            if (i, j) in fills:
                c.fill = fills[(i, j)]
    ws.column_dimensions["B"].width = 38


def sheet_cics_items(wb, cics):
    ws = wb.create_sheet("CICS Per Item")
    r = title(ws, "Per Item: Current Category vs CICS Section (Fast and Slow Items)", [
        "Same item model the app uses (category share + recent-sales blend, calendar-adjusted); only the category it borrows from changes.",
        "Better with: which grouping gave the lower MASE (green = CICS, orange = current). 'Same' when the category's members did not change,",
        "e.g. Drinkware and Mugs & Tumblers hold exactly the same items. Slow items are not forecast by the app; scored here for comparison only.",
        "Items with no MASE sold nothing in their scoring history (or had too little of it) and are left out of the averages."])
    it = cics["vs_current_items"].copy()
    it["avg"] = it.actual_sum / it.n_folds.where(it.n_folds > 0)
    it = it.sort_values(["fsn_class", "avg"], ascending=[True, False])
    headers = ["Item", "Speed", "Current category", "CICS section", "Category members changed",
               "Test windows", "Avg units sold per 30 days", "Current: MASE", "CICS: MASE", "Difference (CICS - current)",
               "Current: MAE (units)", "CICS: MAE (units)", "Current: over/under, %", "CICS: over/under, %", "Better with"]
    rows = []
    for x in it.itertuples():
        d = x.cics_mase - x.current_mase
        better = ("" if not np.isfinite(d) else "Same" if abs(d) < 1e-9 else "CICS" if d < 0 else "Current")
        bias = lambda b: 100 * b / x.actual_sum if x.actual_sum > 0 else np.nan
        rows.append([x.item_name, "Fast" if x.fsn_class == "F" else "Slow", x.current_category, x.cics_category,
                     "Yes" if x.category_changed else "No", int(x.n_folds), x.avg, x.current_mase, x.cics_mase, d,
                     x.current_mae, x.cics_mae, bias(x.current_bias), bias(x.cics_bias), better])
    fills = better_fills(rows, 14, mase_fills(rows, 8, mase_fills(rows, 7)))
    table(ws, r, headers, rows, {6: "0.0", 7: "0.00", 8: "0.00", 9: "+0.00;-0.00", 10: "0.0", 11: "0.0",
                                 12: "+0.0;-0.0", 13: "+0.0;-0.0"},
          [40, 7, 20, 30, 10, 8, 10, 9, 9, 11, 10, 10, 10, 10, 10], fills)


def sheet_cics_mapping(wb, cics):
    ws = wb.create_sheet("CICS Mapping")
    r = title(ws, "Where Each Product Goes in the CICS Item List", [
        "On CICS list: the item appears in FOR CICS STUDENTS.xlsx, often under a different spelling (e.g. 'C. Keychain' = UST College Keychain).",
        "By name: not on the list; section chosen from the name (price and sale dates used where the name alone was unclear - see Note).",
        "Uncategorized: no clue in the name. Both such products have never sold. Use the filter arrows to review one section."])
    m = cics["category_mapping"].sort_values(["cics", "units_sold"], ascending=[True, False])
    basis = {"list": "On CICS list", "name": "By name", "none": "Uncategorized"}
    speed = {"F": "Fast", "S": "Slow", "N": "Non-moving"}
    rows = [[x.item_name, speed.get(x.fsn_class, x.fsn_class), x.units_sold, x.current, x.cics,
             basis[x.basis], x.note if isinstance(x.note, str) else None] for x in m.itertuples()]
    fills = {(i, 5): BAD_FILL for i, row in enumerate(rows) if row[5] != "On CICS list"}
    table(ws, r, ["Item", "Speed", "Units sold (all history)", "Current category", "CICS section", "How placed", "Note"],
          rows, {2: "0"}, [44, 11, 11, 20, 36, 14, 60], fills)


# ---------------------------------------------------------------- main
def main():
    cics = load_cics()
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    cat = pd.DataFrame(tca.category_rows(con))
    item = pd.DataFrame(tca.item_rows(con))
    cat, item = cat[cat.version == "capped"], item[item.version == "capped"]

    models = {r[0] for r in con.execute("SELECT DISTINCT model_type FROM Result_Forecast")}
    cmodels = {r[0] for r in con.execute("SELECT DISTINCT model_type FROM Result_Category_Forecast")}
    if models != {ITEM_MODEL_ID} or cmodels != {CAT_MODEL_ID}:
        print("ustore.db holds a different model than this workbook describes:", models, cmodels)
        return 1

    cstored = pd.read_sql_query("""SELECT forecast_category AS key, mae, mase, naive_mae, beats_naive_mae
        FROM Result_Category_Forecast_Metrics WHERE period_scope = 'overall'""", con).set_index("key")
    istored = pd.read_sql_query("""SELECT product_id AS key, mae, mase, naive_mae, beats_naive_mae
        FROM Result_Forecast_Metrics WHERE period_scope = 'overall'""", con).set_index("key")
    cnext = pd.read_sql_query("""SELECT forecast_category AS key, SUM(yhat) AS next_30, MIN(forecast_date) AS s,
        MAX(forecast_date) AS e, MAX(snapshot_date) AS snap FROM Result_Category_Forecast GROUP BY 1""", con).set_index("key")
    inext = pd.read_sql_query("SELECT product_id AS key, SUM(yhat) AS next_30 FROM Result_Forecast GROUP BY 1",
                              con).set_index("key")
    prod = pd.read_sql_query("SELECT product_id AS key, item_name, forecast_category FROM Dim_Product", con).set_index("key")
    last = pd.read_sql_query("""SELECT f.product_id AS key, MAX(d.calendar_date) AS last_sale FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.quantity_sold > 0 GROUP BY 1""", con).set_index("key")
    con.close()

    cs, its = score(cat), score(item)
    check_against_db(cs, cstored, "Categories")
    check_against_db(its, istored, "Items")

    cs = cs.join(cstored[["naive_mae", "beats_naive_mae"]].rename(columns={"beats_naive_mae": "beats_naive"}))
    cs = cs.join(cnext.next_30)
    its = its.join(istored[["naive_mae", "beats_naive_mae"]].rename(columns={"beats_naive_mae": "beats_naive"}))
    its = its.join(inext.next_30).join(prod).join(last)
    its["category"] = its.forecast_category.fillna("Uncategorised")
    snapshot = cnext.snap.max()
    its["discontinued"] = (pd.Timestamp(snapshot) - pd.to_datetime(its.last_sale)).dt.days > DISCONTINUED_DAYS

    summ = [summary_row("Category", CAT_MODEL, cs, cat), summary_row("Item", ITEM_MODEL, its, item)]

    cat = cat.sort_values(["key", "fold"]).assign(lead=lambda d: [(k,) for k in d.key])
    item = item.assign(avg=item.key.map(its.avg_actual)).sort_values(["avg", "key", "fold"], ascending=[False, True, True])
    item["lead"] = [(its.item_name[k], its.category[k]) for k in item.key]

    wb = Workbook()
    wins = sorted(cat.window_start.unique())
    sheet_readme(wb, summ, snapshot, cnext.s.min(), cnext.e.max(),
                 pd.Timestamp(wins[0]).date(), (pd.Timestamp(wins[-1]) + pd.Timedelta(days=29)).date())
    sheet_summary(wb, summ)
    sheet_categories(wb, cs)
    sheet_items(wb, its)
    sheet_by_month(wb, "Category by Month", "Category Forecast vs Actual, Each Past 30-Day Window",
                   cat, ["Category"], [22], "category")
    sheet_by_month(wb, "Item by Month", "Item Forecast vs Actual, Each Past 30-Day Window",
                   item, ["Item", "Category"], [40, 20], "item")
    sheet_cics_summary(wb, cics)
    sheet_cics_items(wb, cics)
    sheet_cics_mapping(wb, cics)
    wb.save(OUT)

    for s in summ:
        print("  %-8s average MASE %.3f (median %.3f, below 1: %s)  average MAPE %.1f%%  WMAPE %.1f%%  bias %+.1f%%  beats naive %s"
              % (s[0], s[3], s[4], s[5], s[6], s[8], s[9], s[10]))
    print("Wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
