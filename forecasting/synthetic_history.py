"""
forecasting/synthetic_history.py
------------------------------------------------------------------
Extra TRAINING history from Fact_Sales_Synthetic (2023-02..2023-08, written by
scripts/load_synthetic_2023.py from data/USTore_sales_2023_daily_synthetic.csv).

Only the 2023 batch totals are real; the split across days is modelled. So the
rows are kept out of Fact_Sales and enter exactly one place: the series the
forecast models are fitted on (step4 / step4c). Fast/Slow classification,
reorder math, the dashboard's history and every scored test window stay on
real sales only.

How it joins the real series
----------------------------
The real series start 2024-05-02. The synthetic one ends 2023-08-31, so the
daily index is extended back to the first synthetic day and the eight months in
between (2023-09..2024-04) have NO data at all. Array models need a number on
every day, so the gap is zero-filled there - the same convention as an
untallied day elsewhere in the pipeline - and `gap` marks it so callers can
pass NaN instead where a model can skip missing days (Prophet, for the
day-by-day shape).

What this can and cannot change, stated because it decides the result: the
shipped models look back at most 365 days from any forecast origin (6-month
averages, a 30-day share, the 365-day calendar ratios), and the oldest
walk-forward origin is in 2025. Only TSB's starting state (which then decays
for 700+ days), the Prophet day shape and MASE's scale (mean change between
consecutive 30-day blocks of ALL training data, gap zeros included) can see
2023. scripts/validate_protocols.py measures the effect with and without.
------------------------------------------------------------------
"""
import numpy as np
import pandas as pd

TABLE = "Fact_Sales_Synthetic"

__all__ = ["TABLE", "load", "extend_index", "prepend", "by_product", "by_category"]


def load(con):
    """Daily synthetic quantities usable for training, or None when the table
    is missing or empty. Rows that matched neither a product nor a category
    ('unmapped') are left out."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                   (TABLE,)).fetchone() is None:
        return None
    syn = pd.read_sql(
        f"""SELECT calendar_date, product_id, forecast_category, quantity_sold
            FROM {TABLE} WHERE match_type <> 'unmapped'""",
        con, parse_dates=["calendar_date"])
    return syn if len(syn) else None


def extend_index(idx, syn):
    """(ext_idx, n_pre, gap): `idx` extended back to the first synthetic day,
    how many days were prepended, and a mask over ext_idx that is True on
    prepended days the synthetic data does not cover."""
    if syn is None or syn["calendar_date"].min() >= idx[0]:
        return idx, 0, np.zeros(len(idx), dtype=bool)
    ext = pd.date_range(syn["calendar_date"].min(), idx[-1], freq="D")
    n_pre = len(ext) - len(idx)
    gap = np.zeros(len(ext), dtype=bool)
    gap[:n_pre] = ext[:n_pre] > syn["calendar_date"].max()
    return ext, n_pre, gap


def prepend(values, daily, ext_idx, n_pre):
    """A series on the real index -> the same series on ext_idx, with the
    synthetic daily quantities (a Series indexed by date, or None) in front and
    zeros on every other prepended day."""
    v = np.asarray(values, dtype=float)
    if n_pre == 0:
        return v
    head = (np.zeros(n_pre) if daily is None else
            daily.reindex(ext_idx[:n_pre], fill_value=0.0).to_numpy(dtype=float))
    return np.concatenate([head, v])


def by_product(syn):
    """{product_id: daily Series} for rows matched to a product."""
    if syn is None:
        return {}
    s = syn.dropna(subset=["product_id"])
    return {int(p): g.groupby("calendar_date")["quantity_sold"].sum()
            for p, g in s.groupby("product_id")}


def by_category(syn):
    """{forecast_category: daily Series}: product-matched and category-only rows."""
    if syn is None:
        return {}
    s = syn.dropna(subset=["forecast_category"])
    return {c: g.groupby("calendar_date")["quantity_sold"].sum()
            for c, g in s.groupby("forecast_category")}
