"""
forecasting/history.py
------------------------------------------------------------------
Where the real history ends.

`Fact_Sales` is zero-filled past the last tally. step0 fills blank cells out
to month end for the dense months, so the panel runs to 2026-07-31 while the
tallies stop at 2026-07-08 - and the 23 days between are step0's fill, not 23
days on which the store sold nothing. Checked against the source rather than
inferred: the "JULY 2026 - TBS" sheet carries a date column for every day of
the month and does not hold one literal zero anywhere in it. Every cell is
blank or positive, so a blank means "not written up yet".

Left in, those days are counted as evidence - `Fact_Sales` has 176 rows for
each of them - and they drag every trailing statistic computed over the panel.
Measured: step4's 30-day mean averaged 7 real days with 23 padded zeros and
under-forecast 5x (`docs/FORECASTING_EXPLORATION_NOTES.md` 2.5); step5's
365-day window put 23 non-evidence days in the denominator of every rate.

Why this module exists
----------------------
The rule - end at the last date ANY SKU sold - was implemented six times
across `scripts/`, in two shapes and with two different behaviours when
nothing has sold at all. That is the drift hazard
`forecasting.policy.eligible_population`'s docstring warns about, and it had
already bitten once: `scripts/model_benchmark.py` never got the rule, so
`tools/service_frontier.py` measures an 821-day span while the deployed policy
measures 798, and its `EXP_SKUS_POSITIVE_DEMAND = 208` still passes while the
pipeline reports 214.

Two shapes, because the callers hold the data two ways, not because the rule
differs:

- `history_index(fact)` - a LONG frame (product_id, calendar_date,
  quantity_sold). Used by `step4_forecast_model.build_calendar` and
  `step5_prescriptive.load_series`, which reindex per-SKU series onto a shared
  daily index.
- `trim_to_history(wide)` - a WIDE frame, dates down and categories across.
  Used by `step4c_category_forecast` and the category experiments, which sum
  across columns to find the last day anything moved.

Both fall back to the last date present when nothing is positive. The wide
form previously did not: `total[total > 0].index.max()` is NaT on an all-zero
frame and `wide.loc[:NaT]` raises, so a caller with no sales at all crashed
rather than degrading. Nothing in the repo hits that today; it is the kind of
difference that makes two copies of a rule stop being one rule.
------------------------------------------------------------------
"""
import pandas as pd

__all__ = ["history_end", "history_index", "trim_to_history"]

DATE_COL = "calendar_date"
QTY_COL = "quantity_sold"


def history_end(fact, date_col=DATE_COL, qty_col=QTY_COL):
    """The last date on which anything actually sold.

    Falls back to the last date present when nothing is positive - a series of
    all zeros has no real history to find, and returning NaT would make every
    caller's slice raise instead of degrading to "use what there is".
    """
    last_sale = fact.loc[fact[qty_col] > 0, date_col].max()
    return fact[date_col].max() if pd.isna(last_sale) else last_sale


def history_index(fact, date_col=DATE_COL, qty_col=QTY_COL):
    """Daily index from the first row to `history_end`, for a LONG frame.

    The index every per-SKU series is reindexed onto, so each position is the
    same calendar day for every SKU - which is what lets a calendar-aware
    correction be applied to a bare array later.
    """
    return pd.date_range(fact[date_col].min(),
                         history_end(fact, date_col, qty_col), freq="D")


def trim_to_history(wide):
    """(trimmed, history_end, n_trimmed) for a WIDE date-indexed frame.

    `wide` has dates down and one column per series; a row counts as real
    history when anything in it is positive.
    """
    total = wide.sum(axis=1)
    positive = total[total > 0]
    end = wide.index.max() if positive.empty else positive.index.max()
    trimmed = wide.loc[:end]
    return trimmed, end, len(wide) - len(trimmed)
