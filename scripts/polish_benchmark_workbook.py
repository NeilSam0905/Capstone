"""
scripts/polish_benchmark_workbook.py
------------------------------------------------------------------
Presentation pass over the workbook that
`benchmark_fast_raw_vs_clean.py` (+ `paired_cleaning_effect.py`) write.

Kept separate from the scripts that produce the numbers, deliberately:
nothing in here recomputes, re-sorts or filters anything. It only adds
colour scales, number formats and two charts, so a mistake in this file
can make the workbook ugly but cannot make it wrong. Re-runnable - every
rule it adds replaces the ones from the previous run.

Colour convention, applied to every error column:
    green = low error = good, red = high error.
For the percent-change columns the scale is inverted, since there a
NEGATIVE number (error fell after cleaning) is the good outcome.

Run (from the repo root):
    python scripts/polish_benchmark_workbook.py
------------------------------------------------------------------
"""
import argparse
import os
import sys

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

WORKBOOK = "data/USTore_FastMoving_Model_Benchmark.xlsx"

# lower is better
ERROR_COLS = {
    "mae", "rmse", "mase", "mase_median", "mae_median", "wmape_pct",
    "mape_pct", "wmape_pooled_pct", "mae_pct_of_demand", "rmse_pct_of_demand",
    "mase_pct", "mae_clean", "mae_raw", "rmse_clean", "rmse_raw",
    "mase_clean", "mase_raw", "wmape_pct_clean", "wmape_pct_raw",
    "wmape_pct", "mae_pct_of_demand",
    "best_mase", "best_mae", "mae_clean_pipelineF", "rmse_clean_pipelineF",
    "mase_clean_pipelineF",
}
# higher is better
GOOD_COLS = {
    "pct_skus_beating_naive", "pct_better_than_naive_mae",
    "pct_better_than_naive_rmse", "pct_better_than_naive_mase",
    "pct_items_improved", "items_improved",
    "pct_items_improved_wmape", "items_improved_wmape",
}
# negative is better (error fell)
DELTA_COLS = {
    "pct_change_mae", "pct_change_rmse", "pct_change_mase",
    "pct_change_wmape_pct", "delta_mae", "delta_rmse", "delta_mase",
    "delta_wmape_pct", "median_pct_change_mae", "bias",
    "pct_change_wmape_pct",
}

GREEN, YELLOW, RED = "63BE7B", "FFEB84", "F8696B"


def scale(low, mid, high):
    return ColorScaleRule(start_type="min", start_color=low,
                          mid_type="percentile", mid_value=50, mid_color=mid,
                          end_type="max", end_color=high)


def polish(path):
    wb = openpyxl.load_workbook(path)
    head_fill = PatternFill("solid", fgColor="1F3864")
    head_font = Font(color="FFFFFF", bold=True)

    for name in wb.sheetnames:
        ws = wb[name]
        if ws.max_row < 2 or name == "README":
            continue

        headers = {str(c.value): c.column for c in ws[1] if c.value is not None}
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions

        for header, col in headers.items():
            letter = get_column_letter(col)
            rng = f"{letter}2:{letter}{ws.max_row}"
            if header in ERROR_COLS:
                ws.conditional_formatting.add(rng, scale(GREEN, YELLOW, RED))
            elif header in GOOD_COLS:
                ws.conditional_formatting.add(rng, scale(RED, YELLOW, GREEN))
            elif header in DELTA_COLS:
                ws.conditional_formatting.add(rng, scale(GREEN, YELLOW, RED))

            if header.endswith("_pct") or header.startswith("pct_"):
                for cell in ws[letter][1:]:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.0"

    # ---- one chart per summary sheet --------------------------------
    for sheet, metric, title in (
        ("Summary_CLEAN", "mase", "MASE by method - CLEAN, Fast-moving SKUs"),
        ("Summary_RAW", "mase", "MASE by method - RAW, Fast-moving SKUs"),
    ):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        headers = {str(c.value): c.column for c in ws[1] if c.value is not None}
        if metric not in headers or "method" not in headers:
            continue
        # a re-run must not stack a second chart on the first
        ws._charts = []

        ch = BarChart()
        ch.type, ch.style = "bar", 10
        ch.title = title
        ch.x_axis.title, ch.y_axis.title = "method", metric.upper()
        ch.height, ch.width = 12, 20
        data = Reference(ws, min_col=headers[metric], min_row=1, max_row=ws.max_row)
        cats = Reference(ws, min_col=headers["method"], min_row=2, max_row=ws.max_row)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.legend = None
        ws.add_chart(ch, f"{get_column_letter(ws.max_column + 2)}2")

    wb.save(path)
    print(f"Polished {path} ({len(wb.sheetnames)} sheets)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", default=WORKBOOK)
    args = ap.parse_args()
    if not os.path.exists(args.workbook):
        print(f"{args.workbook} not found - run benchmark_fast_raw_vs_clean.py first")
        return 1
    polish(args.workbook)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
