"""
scripts/build_synthetic_results_workbook.py
------------------------------------------------------------------
Builds data/USTore_with_Synthetic_Current_Model_Results.xlsx: the same layout
as USTore_Current_Model_Results.xlsx, but every score is shown twice - trained
on real sales only (what the app does) and trained on real sales PLUS the
synthetic sales made for the months the store has no tally data for
(2023-01..2024-04 and 2024-06..2024-07).

  Read Me               what was filled in, how, how it was tested, the answer
  Summary               one row per level x training data
  Categories            per category: real vs with synthetic
  Items                 per forecast item: real vs with synthetic
  Category by Month     forecast vs actual, every past 30-day window, both versions
  Item by Month         the same per item
  All Models Tested     every model scored with and without synthetic data (10 draws)
  Synthetic Months      the synthetic data month by month, next to real months
  Synthetic Days        one row per synthetic date: which real day it was copied from
  Synthetic Sales Data  the synthetic sales themselves, one row per date x product

Scores are read from the files tools/synthetic_missing_months_test.py writes
(data/synthetic_missing_months_*.csv) - run that first. The "with synthetic"
columns use generator seed 0, which is exactly the data in
data/synthetic_missing_months_2023_2024.csv; the spread over 10 seeds is on
All Models Tested. The real-only scores are checked against ustore.db, and the
real-only "next 30 days" forecasts against Result_Category_Forecast /
Result_Forecast; the script stops if either differs.

Run:  python scripts/build_synthetic_results_workbook.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_current_results_workbook as bcr
import synthetic_missing_months_test as smm
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict
from forecasting.topdown import topdown_tsb_fit_predict

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "USTore_with_Synthetic_Current_Model_Results.xlsx")
SEED = 0
REAL_LBL, SYN_LBL = "Real sales only", "Real + synthetic"
SAME_TOL = 0.0005          # MASE difference below this is reported as "Same"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SECTION_FONT = Font(name=bcr.FONT, size=11, bold=True, color="1F4E78")


# ------------------------------------------------------------------ inputs
def load_inputs():
    paths = {k: os.path.join(DATA, f"synthetic_missing_months_{k}.csv")
             for k in ("folds", "test", "days", "2023_2024")}
    missing = [p for p in paths.values() if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"missing {missing} - run tools/synthetic_missing_months_test.py first")
    folds = pd.read_csv(paths["folds"], dtype={"key": str})
    return (folds, pd.read_csv(paths["test"]), pd.read_csv(paths["days"]),
            pd.read_csv(paths["2023_2024"]))


def shipped_and_next(folds):
    """Re-score the two shipped models at full precision (the folds CSV keeps 4
    decimals, too coarse for the exact check against ustore.db) on the real and
    the seed-0 synthetic series, plus their next-30-day totals from the end of
    the data. Returns {level: (real rows, synthetic rows)} and {label: (category
    next-30, item next-30)}. Synthetic rows carry the REAL arm's naive scale, so
    MASE compares like with like."""
    smm.init()
    rows, nxt = {"category": {}, "item": {}}, {}
    for arm_name, seed, label in ((smm.REAL, -1, REAL_LBL), ("synthetic", SEED, SYN_LBL)):
        p, cats, types = smm.arm(arm_name, seed)
        c_rows, c_next = [], {}
        for k in cats.columns:
            if cats[k].sum() > 0:
                v = cats[k].to_numpy(float)
                fn = calendar_capped_fit_predict(rolling_mean_fit_predict(180), types)
                c_rows += smm.score("category", k, v, p.index, {smm.SHIP_CAT: fn}, arm_name, seed)
                c_next[k] = float(fn(v, smm.H).sum())
        i_rows, i_next = [], {}
        for pid in smm.G["fast"]:
            cv = cats[smm.G["cat_of"][pid]].to_numpy(float)
            fn = calendar_capped_fit_predict(topdown_tsb_fit_predict(cv), types, cat_values=cv)
            v = p[pid].to_numpy(float)
            i_rows += smm.score("item", pid, v, p.index, {smm.SHIP_ITEM: fn}, arm_name, seed)
            i_next[pid] = float(fn(v, smm.H).sum())
        rows["category"][label] = pd.DataFrame(c_rows)
        rows["item"][label] = pd.DataFrame(i_rows)
        nxt[label] = (pd.Series(c_next), pd.Series(i_next))
    smm.G["con"].close()

    out = {}
    for level, method in (("category", smm.SHIP_CAT), ("item", smm.SHIP_ITEM)):
        real, syn = rows[level][REAL_LBL], rows[level][SYN_LBL]
        sc = real.set_index(["key", "fold"]).scale
        syn["scale"] = sc.reindex(pd.MultiIndex.from_frame(syn[["key", "fold"]])).to_numpy()
        # must agree with what the experiment wrote
        f = folds[(folds.level == level) & (folds.method == method)
                  & ((folds.arm == smm.REAL) | (folds.seed == SEED))].copy()
        if level == "item":
            f["key"] = f.key.astype(int)
        f["lbl"] = np.where(f.arm == smm.REAL, REAL_LBL, SYN_LBL)
        both = pd.concat([real.assign(lbl=REAL_LBL), syn.assign(lbl=SYN_LBL)])
        j = both.merge(f[["key", "fold", "lbl", "pred", "actual"]], on=["key", "fold", "lbl"],
                       suffixes=("", "_csv"))
        gap = max((j.pred - j.pred_csv).abs().max(), (j.actual - j.actual_csv).abs().max())
        assert len(j) == len(both) == len(f) and gap < 1e-3, f"{level}: differs from the experiment's CSV"
        print(f"{level}: {len(both)} fold rows match data/synthetic_missing_months_folds.csv (max gap {gap:.1e})")
        out[level] = (real, syn)
    return out, nxt


def check_next(ours, stored, label):
    gap = (ours - stored.reindex(ours.index)).abs()
    if gap.isna().any() or gap.max() > 0.01:
        print(f"{label}: real-only next-30 forecast differs from ustore.db:")
        print(gap[gap.isna() | (gap > 0.01)])
        raise SystemExit(1)
    print(f"{label}: {len(gap)} next-30 forecasts match ustore.db (max gap {gap.max():.1e})")


def per_key(df, naive_mae):
    s = bcr.score(df)
    nm = naive_mae.reindex(s.index)
    s["beats_naive"] = np.where(nm.notna(), (s.mae < nm).astype(float), np.nan)
    return s


def better_with(d):
    if d is None or not np.isfinite(d):
        return None
    return "Synthetic" if d < -SAME_TOL else "Real only" if d > SAME_TOL else "Same"


def better_fills(rows, col, fills):
    for i, row in enumerate(rows):
        f = {"Synthetic": bcr.GOOD_FILL, "Real only": bcr.BAD_FILL}.get(row[col])
        if f is not None:
            fills[(i, col)] = f
    return fills


def text_block(ws, r, heading, items, last_col, per_line=120):
    ws.cell(row=r, column=1, value=heading).font = SECTION_FONT
    for head, text in items:
        r += 1
        ca = ws.cell(row=r, column=1, value=head)
        ca.font, ca.alignment = bcr.BOLD_FONT, Alignment(vertical="top", wrap_text=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=last_col)
        cb = ws.cell(row=r, column=2, value=text)
        cb.font, cb.alignment = bcr.BODY_FONT, Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[r].height = 15 * max(1, -(-len(text) // per_line))
    return r


# ------------------------------------------------------------------ sheets
def summary_row(level, training, model, s, df):
    err = df.pred - df.actual
    return [level, training, model, len(s), s.mase.mean(), s.mase.median(),
            f"{int((s.mase < 1).sum())} of {s.mase.notna().sum()}", s.mape.mean(), s.mape.median(),
            100 * err.abs().sum() / df.actual.sum(), 100 * err.sum() / df.actual.sum(),
            f"{int(s.beats_naive.sum())} of {s.beats_naive.notna().sum()}"]


def diff_row(level, a, b):
    return [level, "Difference (synthetic - real)", None, None, b[4] - a[4], b[5] - a[5], None,
            b[7] - a[7], b[8] - a[8], b[9] - a[9], b[10] - a[10], None]


def sheet_readme(wb, summ, snapshot, first_win, last_win, days, syn_long, tests):
    ws = wb.active
    ws.title = "Read Me"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    ws.cell(row=1, column=1, value="USTore Forecasting: Current Models With and Without Synthetic Data").font = bcr.TITLE_FONT
    ws.cell(row=2, column=1, value=f"Data up to {snapshot}. Synthetic sales added for the months with no tally data. "
                                   "Nothing in the app uses the synthetic data.").font = bcr.NOTE_FONT
    cr, cs, cd = summ[0], summ[1], summ[2]
    ir, is_, idf = summ[3], summ[4], summ[5]
    ship = tests[tests.method.isin([smm.SHIP_CAT, smm.SHIP_ITEM])].set_index("level")
    lvl = days.match_level.value_counts()
    lines = [
        ("THE QUESTION", None),
        ("Question", "The store has no tally sheets for Jan 2023 - Apr 2024 and Jun - Jul 2024. If those months are filled "
                     "with realistic synthetic sales, do the forecasting models the app uses get more accurate?"),
        ("Answer", f"No. The category model's average MASE goes from {cr[4]:.4f} to {cs[4]:.4f} and the item model's from "
                   f"{ir[4]:.4f} to {is_[4]:.4f} - differences far too small to matter, and inside the range that "
                   "random chance produces. Recommendation: keep training on real sales only."),
        ("", None),
        ("THE SYNTHETIC DATA", None),
        ("Months filled", f"2023-01 to 2024-04 (16 months, before the first tally sheet) and 2024-06 to 2024-07 (2 months "
                          f"that the app currently reads as zero sales). {len(days)} days, {int(syn_long.quantity_sold.sum()):,} "
                          f"units, {len(syn_long):,} item-day rows. Aug - Dec 2024 have real sheets and were left alone."),
        ("How it was made", "Each missing day copies the sales of ONE real store-day, for every item at once, so category "
                            "totals stay realistic. The real day is picked at random from days with the same month of the "
                            "year, weekday and school-calendar period (enrollment, semester break, exam week or normal); "
                            "if fewer than 3 real days match, it matches on less. Days the calendar marks as store closures "
                            "get zero sales."),
        ("How closely it matched", f"Same month + weekday + school period: {lvl.get('L1 month+weekday+day type', 0)} days. "
                                   f"Weekday + school period: {lvl.get('L2 weekday+day type', 0)}. Weekday only: "
                                   f"{lvl.get('L3 weekday', 0)}. Store closed: {lvl.get('closed (zero)', 0)}."),
        ("Where it came from", f"Only real tallied days BEFORE {first_win}, when the first test window starts. So the synthetic "
                               "data holds no information from any period the forecasts are graded on. Aug and Sep 2024 "
                               "were not used as sources (periodic stock counts, not daily tallies)."),
        ("What it assumes", "No growth or decline: 2023 is assumed to sell like May 2024 - Jul 2025. Items launched later can "
                            "receive synthetic 2023 sales. It is made-up data and must be labelled as such wherever it is used."),
        ("", None),
        ("MODELS TESTED", None),
        ("Category level", f"{bcr.CAT_MODEL} - the model the app uses."),
        ("Item level", f"{bcr.ITEM_MODEL} - the model the app uses, for the 58 fast-moving items."),
        ("Other models", "Models that can read further back than the app's (12-month average, same-window-last-year, Prophet "
                         "with a yearly pattern, Ridge) - see All Models Tested."),
        ("", None),
        ("HOW IT WAS TESTED", None),
        ("Walk-forward test", f"Same test as the Current Model Results workbook: forecast each of 12 past 30-day windows "
                              f"({first_win} to {last_win}) using only data before it, then compare with what really sold. "
                              "Both versions are graded on exactly the same windows and the same REAL sales; synthetic days "
                              "are only ever training data."),
        ("Fair MASE", "MASE is divided by the simple guess's error on the REAL data in both versions, so a change in MASE "
                      "is a change in the forecast, not in the yardstick."),
        ("Random draws", "The synthetic data was drawn 10 times with different random picks. These tabs show draw 0 (the "
                         "data in the Synthetic Sales Data tab); All Models Tested shows the range over all 10."),
        ("Checks (all passed)", "The real-only scores match ustore.db exactly. Every version used identical test windows and "
                                "actual sales. Two models that cannot see the synthetic data gave identical forecasts in "
                                "every version, proving no synthetic value reached a test window."),
        ("", None),
        ("HEADLINE RESULTS", None),
        ("Category level", f"Real only: average MASE {cr[4]:.3f}, error {cr[9]:.1f}% of units sold. With synthetic: "
                           f"{cs[4]:.3f}, {cs[9]:.1f}%. Over 10 draws: {ship.loc['category', 'syn_mase_min']:.3f} to "
                           f"{ship.loc['category', 'syn_mase_max']:.3f}."),
        ("Item level", f"Real only: average MASE {ir[4]:.3f}, error {ir[9]:.1f}% of units sold. With synthetic: "
                       f"{is_[4]:.3f}, {is_[9]:.1f}%. Over 10 draws: {ship.loc['item', 'syn_mase_min']:.3f} to "
                       f"{ship.loc['item', 'syn_mase_max']:.3f}."),
        ("Why nothing changes", "The app's models only look back 6 to 12 months, and the first test window starts 14 months "
                                "after the real data begins. The 2023 synthetic data is too old for them to see; the "
                                "Jun - Jul 2024 fill only touches the calendar adjustment of the earliest windows."),
        ("Models that can see it", "Move by at most about 3% (some better, some worse) and none comes close to the app's "
                                   "models. Resampling the store's own days cannot add a pattern the real days lack."),
        ("", None),
        ("WHAT THE COLUMNS MEAN", "Same as the Current Model Results workbook - see the Summary tab. 'Difference' = with "
                                  "synthetic minus real only (negative = synthetic helped). 'Better with': green = "
                                  "synthetic, orange = real only, 'Same' = MASE within 0.0005."),
    ]
    r = 4
    for a, b in lines:
        ca = ws.cell(row=r, column=1, value=a)
        if b is None:
            ca.font = SECTION_FONT
        else:
            ca.font = bcr.BOLD_FONT
            cb = ws.cell(row=r, column=2, value=b)
            cb.font = bcr.BODY_FONT
            cb.alignment = Alignment(wrap_text=True, vertical="top")
            ca.alignment = Alignment(vertical="top", wrap_text=True)
            ws.row_dimensions[r].height = 15 * max(1, -(-len(b) // 105))
        r += 1


def sheet_summary(wb, summ):
    ws = wb.create_sheet("Summary")
    r = bcr.title(ws, "Summary: Current Models, Real Sales Only vs Real + Synthetic", [
        "Scores are from 12 past 30-day windows, graded on real sales only. Synthetic = draw 0. "
        "What each column means is explained below the table."])
    headers = ["Level", "Training data", "Model in use", "How many", "Average MASE", "Median MASE", "MASE below 1",
               "Average MAPE, %", "Median MAPE, %", "Error % of units sold (WMAPE)", "Over/under forecast, %",
               "Beats simple guess"]
    fills = {}
    for i, row in enumerate(summ):
        if row[1].startswith("Difference"):
            for j in range(len(headers)):
                fills[(i, j)] = bcr.GREY_FILL
    r = bcr.table(ws, r, headers, summ,
                  {4: "0.000", 5: "0.000", 7: "0.0", 8: "0.0", 9: "0.0", 10: "+0.0;-0.0"},
                  [10, 26, 44, 9, 11, 11, 11, 11, 11, 14, 13, 12], fills)
    for i, row in enumerate(summ):
        if row[1].startswith("Difference"):
            for j, f in ((4, "+0.000;-0.000;0.000"), (5, "+0.000;-0.000;0.000"), (7, "+0.0;-0.0;0.0"),
                         (8, "+0.0;-0.0;0.0"), (9, "+0.00;-0.00;0.00"), (10, "+0.00;-0.00;0.00")):
                ws.cell(row=r - len(summ) + 1 + i, column=j + 1).number_format = f
    ws.freeze_panes = None
    ws.auto_filter.ref = None
    terms = [("Training data", "Real sales only = what the app does. Real + synthetic = the same real sales plus the "
                               "synthetic months in front of / inside them. Difference = synthetic minus real "
                               "(negative MASE / error = synthetic helped).")] + bcr.SUMMARY_TERMS
    text_block(ws, r + 2, "WHAT THE COLUMNS MEAN", terms, 12, per_line=130)


def sheet_level(wb, name, heading, notes, s_real, s_syn, nxt_real, nxt_syn, lead, lead_heads, lead_widths,
                grey=None):
    ws = wb.create_sheet(name)
    r = bcr.title(ws, heading, notes)
    headers = lead_heads + ["Avg units sold per 30 days", "Real only: MASE", "With synthetic: MASE",
                            "Difference in MASE", "Real only: error % of units (WMAPE)",
                            "With synthetic: error % of units (WMAPE)", "Real only: over/under, %",
                            "With synthetic: over/under, %", "Real only: beats simple guess",
                            "With synthetic: beats simple guess", "Real only: next 30 days (units)",
                            "With synthetic: next 30 days (units)", "Better with"]
    n = len(lead_heads)
    order = s_real.sort_values("avg_actual", ascending=False).index
    rows, fills = [], {}
    for i, k in enumerate(order):
        a, b = s_real.loc[k], s_syn.loc[k]
        d = b.mase - a.mase if np.isfinite(a.mase) and np.isfinite(b.mase) else None
        yn = lambda v: None if not np.isfinite(v) else ("Yes" if v == 1 else "No")
        rows.append(list(lead(k)) + [a.avg_actual, a.mase, b.mase, d, a.wmape, b.wmape, a.bias, b.bias,
                                     yn(a.beats_naive), yn(b.beats_naive), nxt_real.get(k), nxt_syn.get(k),
                                     better_with(d)])
        if grey is not None and grey.get(k, False):
            for j in range(len(headers)):
                fills[(i, j)] = bcr.GREY_FILL
    fills = bcr.mase_fills(rows, n + 1, fills)
    fills = bcr.mase_fills(rows, n + 2, fills)
    fills = better_fills(rows, n + 12, fills)
    bcr.table(ws, r, headers, rows,
              {n: "0.0", n + 1: "0.000", n + 2: "0.000", n + 3: "+0.000;-0.000;0.000", n + 4: "0.0",
               n + 5: "0.0", n + 6: "+0.0;-0.0", n + 7: "+0.0;-0.0", n + 10: "0", n + 11: "0"},
              lead_widths + [11, 10, 10, 10, 12, 12, 11, 11, 10, 10, 12, 12, 11], fills)


def sheet_by_month(wb, name, heading, real, syn, lead, lead_heads, lead_widths, what, order):
    ws = wb.create_sheet(name)
    r = bcr.title(ws, heading, [
        "Each row: one past 30-day window. Both forecasts made using only data before the window started; "
        "the actual sales are real in both.",
        "Error = forecast minus actual (positive = forecast too high). % error is blank when nothing sold. "
        "Use the filter arrows to pick one " + what + " or window."])
    j = real.merge(syn[["key", "fold", "pred"]], on=["key", "fold"], suffixes=("_real", "_syn"))
    j["rank"] = j.key.map({k: i for i, k in enumerate(order)})
    j = j.sort_values(["rank", "fold"])
    headers = lead_heads + ["Window start", "Window end", "Actual units sold", "Real only: forecast",
                            "With synthetic: forecast", "Real only: error (units)", "With synthetic: error (units)",
                            "Real only: % error", "With synthetic: % error", "Change in forecast (units)"]
    rows = []
    for x in j.itertuples():
        start = pd.Timestamp(x.window_start)
        er, es = x.pred_real - x.actual, x.pred_syn - x.actual
        pct = (lambda e: 100 * e / x.actual if x.actual > 0 else None)
        rows.append(list(lead(x.key)) + [start.date().isoformat(), (start + pd.Timedelta(days=29)).date().isoformat(),
                                         x.actual, x.pred_real, x.pred_syn, er, es, pct(er), pct(es),
                                         x.pred_syn - x.pred_real])
    n = len(lead_heads)
    bcr.table(ws, r, headers, rows,
              {n + 2: "0", n + 3: "0.0", n + 4: "0.0", n + 5: "+0.0;-0.0", n + 6: "+0.0;-0.0",
               n + 7: "+0;-0", n + 8: "+0;-0", n + 9: "+0.00;-0.00;0.00"},
              lead_widths + [12, 12, 11, 11, 11, 11, 11, 10, 10, 11])


def verdict(x):
    """Clearly better / worse only when the draw-0 95% range excludes zero AND the
    10-draw average agrees AND at least 8 of the draws (or none of them) beat real
    only. Anything else is within chance."""
    if x.method in (smm.CONTROL_CAT, smm.CONTROL_ITEM):
        return "Control (cannot see it)"
    change = x.syn_mean_mase - x.real_mean_mase
    share = x.seeds_improved / x.seeds
    if x.gap_ci_hi < 0 and change < -SAME_TOL and share >= 0.8:
        return "Better with synthetic"
    if x.gap_ci_lo > 0 and change > SAME_TOL and share <= 0.2:
        return "Worse with synthetic"
    return "No clear change"


def sheet_all_models(wb, tests):
    ws = wb.create_sheet("All Models Tested")
    r = bcr.title(ws, "Every Model Tested, With and Without Synthetic Data", [
        "Same 12 past windows and real actual sales for every model. 'With synthetic' = the average over 10 random draws "
        "of the synthetic data (Prophet: 1 draw, it is slow), with the best and worst draw beside it.",
        "95% range: the difference in average MASE (draw 0 minus real only) when the categories / items are resampled. "
        "If it spans 0, the change is within chance.",
        "Controls read only the last 6 months, so they cannot see the synthetic data; identical scores prove none of it "
        "leaked into a test window.",
        "Lower MASE and error % are better. Green = clearly better with synthetic, orange = clearly worse."])
    ship = {smm.SHIP_CAT, smm.SHIP_ITEM}
    t = tests.copy()
    t["in_app"] = t.method.isin(ship)
    t["lvl_rank"] = t.level.map({"category": 0, "item": 1})
    t = t.sort_values(["lvl_rank", "in_app", "real_mean_mase"], ascending=[True, False, True])
    headers = ["Level", "Model", "Used by the app", "How many", "Real only: average MASE",
               "With synthetic: average MASE", "Best draw", "Worst draw", "Change in MASE, %",
               "Real only: error % of units (WMAPE)", "With synthetic: error % of units (WMAPE)",
               "Draws better than real (of 10)", "95% range: low", "95% range: high", "Verdict"]
    rows, fills = [], {}
    for i, x in enumerate(t.itertuples()):
        v = verdict(x)
        rows.append([x.level.capitalize(), x.method, "Yes" if x.in_app else "No", int(x.n), x.real_mean_mase,
                     x.syn_mean_mase, x.syn_mase_min, x.syn_mase_max, x.mase_change_pct, x.real_wmape_pct,
                     x.syn_wmape_pct, f"{int(x.seeds_improved)} of {int(x.seeds)}", x.gap_ci_lo, x.gap_ci_hi, v])
        f = {"Better with synthetic": bcr.GOOD_FILL, "Worse with synthetic": bcr.BAD_FILL}.get(v)
        if f is not None:
            fills[(i, 14)] = f
        if x.in_app:
            fills[(i, 1)] = bcr.GREY_FILL
    r = bcr.table(ws, r, headers, rows,
                  {4: "0.000", 5: "0.000", 6: "0.000", 7: "0.000", 8: "+0.0;-0.0;0.0", 9: "0.0", 10: "0.0",
                   12: "+0.000;-0.000;0.000", 13: "+0.000;-0.000;0.000"},
                  [10, 46, 9, 8, 11, 11, 10, 10, 10, 12, 12, 11, 10, 10, 20], fills)
    for i in range(len(rows)):
        if rows[i][2] == "Yes":
            ws.cell(row=r - len(rows) + 1 + i, column=2).font = bcr.BOLD_FONT
    ws.freeze_panes = None
    ws.auto_filter.ref = None

    p = tests[tests.method == smm.PROPHET].set_index("level")
    best = {lv: g.loc[g.syn_mean_mase.idxmin()] for lv, g in t.groupby("level")}
    moved = t[t.apply(verdict, axis=1) == "Better with synthetic"]
    notes = [
        ("The app's models", "Do not change in any meaningful way: both 95% ranges include zero, and the 10 draws "
                             "barely differ from each other."),
        ("Models that look further back", "Same-window-last-year gains the most (its earliest windows were reading the "
                                          "zero-filled Jul 2024 gap), Ridge and category-level Prophet gain a little on MASE. "
                                          "None comes close to the app's models."),
        ("Clearly better", ", ".join(f"{x.method} ({x.level})" for x in moved.itertuples()) + " - and it is still far "
                           "behind the app's model." if len(moved) else "None."),
        ("Leaning worse", "Item-level Prophet and the calendar adjustment measured over all history score a little worse "
                          "on average (within chance): older, resampled days dilute the recent pattern that describes "
                          "the next month better."),
        ("Prophet fallbacks", f"On some item windows Prophet failed to fit and quietly used the 30-day average instead: "
                              f"{int(p.loc['item', 'fallback_folds_real'])} of {int(p.loc['item', 'folds_per_arm'])} windows "
                              f"real only, {int(p.loc['item', 'fallback_folds_syn'])} with synthetic. None at category level."),
        ("Best with synthetic", f"Category: {best['category'].method} ({best['category'].syn_mean_mase:.3f}). Item: "
                                f"{best['item'].method} ({best['item'].syn_mean_mase:.3f}). The app's models stay best "
                                "with or without synthetic data."),
    ]
    text_block(ws, r + 2, "WHAT THIS SHOWS", notes, 15, per_line=150)


def sheet_months(wb, days, real_month):
    ws = wb.create_sheet("Synthetic Months")
    r = bcr.title(ws, "The Synthetic Data, Month by Month", [
        "Units = all items together. Real same month = the real store total for that calendar month in 2025, for scale.",
        "Nov and Dec 2025 were only partly tallied, and Jul 2025 was a quiet month; see Tally days.",
        "Match: how closely each synthetic day's source matched it (month + weekday + school period, weekday + school "
        "period, weekday only)."])
    d = days.copy()
    d["month"] = d.calendar_date.str[:7]
    g = d.groupby("month")
    rows = []
    for m, x in g:
        same = f"2025-{m[5:]}"
        rm = real_month.get(same, (None, None))
        open_days = int((x.closed == 0).sum())
        rows.append([m, len(x), int(x.closed.sum()), float(x.units.sum()),
                     float(x.units.sum()) / open_days if open_days else None,
                     int((x.match_level.str.startswith("L1")).sum()), int((x.match_level.str.startswith("L2")).sum()),
                     int((x.match_level.str.startswith("L3")).sum()), same, rm[0], rm[1]])
    rows.append(["Total", len(d), int(d.closed.sum()), float(d.units.sum()), None,
                 int(d.match_level.str.startswith("L1").sum()), int(d.match_level.str.startswith("L2").sum()),
                 int(d.match_level.str.startswith("L3").sum()), None, None, None])
    headers = ["Synthetic month", "Days", "Store closed days", "Synthetic units", "Units per open day",
               "Match: month + weekday + period", "Match: weekday + period", "Match: weekday only",
               "Real same month", "Real units that month", "Real tally days that month"]
    fills = {(len(rows) - 1, j): bcr.GREY_FILL for j in range(len(headers))}
    bcr.table(ws, r, headers, rows, {3: "#,##0", 4: "0.0", 9: "#,##0"},
              [14, 8, 10, 11, 11, 13, 12, 11, 11, 12, 11], fills)
    ws.auto_filter.ref = None


def sheet_days(wb, days):
    ws = wb.create_sheet("Synthetic Days")
    r = bcr.title(ws, "Every Synthetic Day and the Real Day It Was Copied From", [
        "Source day = the real store-day whose sales (every item) were copied onto this date. Blank when the store was closed.",
        "School period from the published calendar: enrol = enrollment, break = semester break, exam = exam week."])
    rows = [[x.calendar_date, WEEKDAYS[int(x.weekday)], x.day_type, "Yes" if x.closed else "No",
             None if pd.isna(x.source_date) else x.source_date, x.match_level, x.units]
            for x in days.itertuples()]
    bcr.table(ws, r, ["Date", "Weekday", "School period", "Store closed", "Source day (real)", "Match", "Units"],
              rows, {6: "0"}, [12, 9, 11, 9, 13, 26, 8])


def sheet_sales(wb, syn):
    ws = wb.create_sheet("Synthetic Sales Data")
    r = bcr.title(ws, "Synthetic Sales Data (made up - not real sales)", [
        "One row per synthetic date and item with sales above zero; an item missing on a date sold zero that day. "
        "Same data as data/synthetic_missing_months_2023_2024.csv."])
    cols = ["calendar_date", "product_id", "item_name", "forecast_category", "quantity_sold", "source_date",
            "day_type", "match_level"]
    rows = syn[cols].values.tolist()
    bcr.table(ws, r, ["Date", "Product ID", "Item", "Category", "Units", "Source day (real)", "School period",
                      "Match"], rows, {4: "0"}, [12, 9, 40, 20, 8, 13, 11, 26])


# ---------------------------------------------------------------- main
def main():
    folds, tests, days, syn_long = load_inputs()
    scored, nxt = shipped_and_next(folds)
    (cat_r, cat_s), (item_r, item_s) = scored["category"], scored["item"]

    con = sqlite3.connect("file:%s?mode=ro" % bcr.DB_PATH, uri=True)
    cstored = pd.read_sql_query("""SELECT forecast_category AS key, mae, mase, naive_mae
        FROM Result_Category_Forecast_Metrics WHERE period_scope = 'overall'""", con).set_index("key")
    istored = pd.read_sql_query("""SELECT product_id AS key, mae, mase, naive_mae
        FROM Result_Forecast_Metrics WHERE period_scope = 'overall'""", con).set_index("key")
    cnext = pd.read_sql_query("""SELECT forecast_category AS key, SUM(yhat) AS next_30,
        MAX(snapshot_date) AS snap FROM Result_Category_Forecast GROUP BY 1""", con).set_index("key")
    inext = pd.read_sql_query("SELECT product_id AS key, SUM(yhat) AS next_30 FROM Result_Forecast GROUP BY 1",
                              con).set_index("key")
    prod = pd.read_sql_query("SELECT product_id AS key, item_name, forecast_category FROM Dim_Product",
                             con).set_index("key")
    last = pd.read_sql_query("""SELECT f.product_id AS key, MAX(d.calendar_date) AS last_sale FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.quantity_sold > 0 GROUP BY 1""", con).set_index("key")
    real_month = pd.read_sql_query("""SELECT substr(d.calendar_date, 1, 7) AS m, SUM(f.quantity_sold) AS units
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id GROUP BY 1""", con).set_index("m").units
    tally = pd.read_sql_query("""SELECT substr(calendar_date, 1, 7) AS m, SUM(is_tally_date) AS t
        FROM Dim_Date GROUP BY 1""", con).set_index("m").t
    con.close()

    cs_r, cs_s = per_key(cat_r, cstored.naive_mae), per_key(cat_s, cstored.naive_mae)
    is_r, is_s = per_key(item_r, istored.naive_mae), per_key(item_s, istored.naive_mae)
    bcr.check_against_db(cs_r, cstored, "Categories (real only)")
    bcr.check_against_db(is_r, istored, "Items (real only)")

    check_next(nxt[REAL_LBL][0], cnext.next_30, "Categories")
    check_next(nxt[REAL_LBL][1], inext.next_30, "Items")

    summ = [summary_row("Category", REAL_LBL, bcr.CAT_MODEL, cs_r, cat_r),
            summary_row("Category", SYN_LBL, bcr.CAT_MODEL, cs_s, cat_s)]
    summ.append(diff_row("Category", summ[0], summ[1]))
    summ += [summary_row("Item", REAL_LBL, bcr.ITEM_MODEL, is_r, item_r),
             summary_row("Item", SYN_LBL, bcr.ITEM_MODEL, is_s, item_s)]
    summ.append(diff_row("Item", summ[3], summ[4]))

    snapshot = cnext.snap.max()
    wins = sorted(cat_r.window_start.unique())
    first_win = pd.Timestamp(wins[0]).date()
    last_win = (pd.Timestamp(wins[-1]) + pd.Timedelta(days=29)).date()
    item_cat = prod.forecast_category.fillna("Uncategorised")
    discontinued = ((pd.Timestamp(snapshot) - pd.to_datetime(last.last_sale)).dt.days
                    > bcr.DISCONTINUED_DAYS).to_dict()
    real_m = {m: (float(real_month.get(m, 0)), int(tally.get(m, 0))) for m in tally.index}

    wb = Workbook()
    sheet_readme(wb, summ, snapshot, first_win, last_win, days, syn_long, tests)
    sheet_summary(wb, summ)
    sheet_level(wb, "Categories", f"Per Category: {bcr.CAT_MODEL}", [
        "One row per category, 12 past 30-day windows each. Green MASE = better than the simple guess; orange = more "
        "than twice its error.",
        "Difference = with synthetic minus real only (negative = synthetic helped). Better with: green = synthetic, "
        "orange = real only.",
        "Next 30 days = the forecast from the end of the data; the real-only one is what the app currently shows."],
        cs_r, cs_s, nxt[REAL_LBL][0], nxt[SYN_LBL][0], lambda k: (k,), ["Category"], [22])
    sheet_level(wb, "Items", f"Per Item: {bcr.ITEM_MODEL}", [
        "One row per forecast item, biggest sellers first, 12 past 30-day windows each. Green MASE = better than the "
        "simple guess; orange = more than twice its error.",
        f"Grey rows: no sale in the last {bcr.DISCONTINUED_DAYS} days (probably no longer stocked).",
        "One item sold nothing in any test window, so it has no MASE; that is why MASE covers 57 items.",
        "Difference = with synthetic minus real only (negative = synthetic helped). Column meanings are on the Summary tab."],
        is_r, is_s, nxt[REAL_LBL][1], nxt[SYN_LBL][1],
        lambda k: (prod.item_name[k], item_cat[k], last.last_sale.get(k)),
        ["Item", "Category", "Last sale"], [40, 20, 11], grey=discontinued)
    cat_order = list(cs_r.sort_values("avg_actual", ascending=False).index)
    item_order = list(is_r.sort_values("avg_actual", ascending=False).index)
    sheet_by_month(wb, "Category by Month", "Category Forecast vs Actual, Real Only and With Synthetic",
                   cat_r, cat_s, lambda k: (k,), ["Category"], [22], "category", cat_order)
    sheet_by_month(wb, "Item by Month", "Item Forecast vs Actual, Real Only and With Synthetic",
                   item_r, item_s, lambda k: (prod.item_name[k], item_cat[k]), ["Item", "Category"], [40, 20],
                   "item", item_order)
    sheet_all_models(wb, tests)
    sheet_months(wb, days, real_m)
    sheet_days(wb, days)
    sheet_sales(wb, syn_long)
    wb.save(OUT)

    for s in summ:
        if s[1].startswith("Difference"):
            continue
        print("  %-8s %-17s average MASE %.3f (median %.3f, below 1: %s)  WMAPE %.1f%%  bias %+.1f%%  beats naive %s"
              % (s[0], s[1], s[4], s[5], s[6], s[9], s[10], s[11]))
    print("Wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
