"""
tools/demand_basis_by_anchor.py
------------------------------------------------------------------
The 30-day demand basis, evaluated at EVERY month anchor in the series.

Why this exists
---------------
`step5_prescriptive.py` annualises a 30-day forecast, so its answer
depends on where those 30 days sit. In the current build they sit on
2026-07, which is inside the AY2526 summer term - and only 79 of 266 F+S
SKUs show any demand in that window, against 208 over a trailing year.

Batch 1 measured that one anchor. This measures all of them, for the same
reason A10 computes a grid instead of picking a lead time: report the
function, and let the decision be a lookup.

**This script does not choose an anchor, and it does not re-run A10
against a different one.** Which anchor Chapter 4 standardises on is
deferred decision B14. What is deliberately supplied here is the evidence
that the choice matters and by how much.

Output: docs/demand_basis_by_anchor.csv
    anchor_month, term_tag, n_skus_positive_30d, n_skus_positive_365d,
    median_add, ...

Run:
    python tools/demand_basis_by_anchor.py
------------------------------------------------------------------
"""
import csv
import os
import sqlite3
import sys
from collections import Counter

import numpy as np
import pandas as pd

DB_NAME = "ustore.db"
DOCS = "docs"
OUT_CSV = os.path.join(DOCS, "demand_basis_by_anchor.csv")

# F and S only, matching step5_prescriptive.py - N is excluded from the
# prescriptive math entirely, so it has no demand basis to speak of.
PRICED_CLASSES = ("F", "S")
SHORT_WINDOW = 30
LONG_WINDOW = 365

# ---- expected values: inputs, not outputs -------------------------
# These pin the Batch 1 measurement at the anchor the build currently uses.
EXP_ANCHOR = "2026-07"
EXP_POSITIVE_30D = 79
EXP_POSITIVE_365D = 208
EXP_ELIGIBLE = 266


def load(con):
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])

    products = pd.read_sql_query(
        "SELECT product_id, fsn_class FROM Dim_Product", con)
    keep = set(products.loc[products["fsn_class"].isin(PRICED_CLASSES), "product_id"])

    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")

    series = {}
    for pid, g in fact.groupby("product_id"):
        if pid not in keep:
            continue
        s = (g.groupby("calendar_date")["quantity_sold"].sum()
              .reindex(idx, fill_value=0.0).astype(float))
        if s.sum() > 0:                      # eligible = F/S that ever moved
            series[pid] = s.to_numpy()

    # academic term per calendar day, for tagging anchors
    terms = pd.read_sql_query(
        "SELECT calendar_date, semester_id FROM Dim_Date", con,
        parse_dates=["calendar_date"]).set_index("calendar_date")["semester_id"]
    return series, idx, terms


def term_tag(month_days, terms):
    """The academic term an anchor month sits in: the modal semester_id
    across that month's days. Modal rather than last-day, so a month that
    straddles a term boundary is labelled by where most of it lies."""
    labels = [terms.get(d) for d in month_days]
    labels = [x for x in labels if x is not None and not pd.isna(x)]
    if not labels:
        return "UNKNOWN"
    common = Counter(labels).most_common()
    tag = common[0][0]
    return f"{tag}*" if len(common) > 1 else tag       # * = straddles a boundary


def main():
    os.makedirs(DOCS, exist_ok=True)
    con = sqlite3.connect(DB_NAME)
    series, idx, terms = load(con)
    con.close()

    if not series:
        print("FAIL: no eligible SKUs - every check below would be vacuous.")
        return 1

    months = pd.PeriodIndex(idx, freq="M").unique()
    matrix = np.vstack([series[p] for p in sorted(series)])   # SKUs x days
    day_pos = {d: i for i, d in enumerate(idx)}

    rows = []
    for m in months:
        month_days = [d for d in idx if d.to_period("M") == m]
        end = day_pos[month_days[-1]] + 1          # anchor = end of that month

        def window(n):
            start = max(0, end - n)
            return matrix[:, start:end]

        short, long = window(SHORT_WINDOW), window(LONG_WINDOW)
        pos_short = short.sum(axis=1) > 0
        pos_long = long.sum(axis=1) > 0

        add = short.sum(axis=1) / SHORT_WINDOW     # average daily demand
        median_add = float(np.median(add[pos_short])) if pos_short.any() else 0.0

        rows.append({
            "anchor_month": str(m),
            "term_tag": term_tag(month_days, terms),
            "n_skus_eligible": matrix.shape[0],
            "n_skus_positive_30d": int(pos_short.sum()),
            "n_skus_positive_365d": int(pos_long.sum()),
            "pct_positive_30d": round(100.0 * pos_short.mean(), 1),
            "median_add": round(median_add, 4),
            "total_units_30d": float(short.sum()),
            "days_available": int(end),
        })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    by_anchor = {r["anchor_month"]: r for r in rows}

    # ---- report ----------------------------------------------------
    print(f"{len(rows)} month anchors, {matrix.shape[0]} eligible F+S SKUs\n")
    print("%-9s %-12s %8s %9s %10s"
          % ("anchor", "term", ">0 @30d", ">0 @365d", "median ADD"))
    print("-" * 62)
    for r in rows:
        mark = "  <- current" if r["anchor_month"] == EXP_ANCHOR else ""
        print("%-9s %-12s %8d %9d %10.4f%s"
              % (r["anchor_month"], r["term_tag"], r["n_skus_positive_30d"],
                 r["n_skus_positive_365d"], r["median_add"], mark))

    best = max(rows, key=lambda r: r["n_skus_positive_30d"])
    worst = min(rows[11:], key=lambda r: r["n_skus_positive_30d"])  # skip warm-up
    print(f"\nSpread across anchors: {worst['n_skus_positive_30d']} "
          f"({worst['anchor_month']}) to {best['n_skus_positive_30d']} "
          f"({best['anchor_month']}) SKUs with any demand in the 30-day window.")

    # The comparison that makes B14 concrete rather than abstract.
    recent = rows[-12:]
    current = by_anchor[EXP_ANCHOR]
    lower = [r for r in recent if r["n_skus_positive_30d"] < current["n_skus_positive_30d"]]
    prev = rows[-2]
    print(f"\nThe anchor currently in use is {EXP_ANCHOR} "
          f"({current['n_skus_positive_30d']} SKUs). Of the last 12 anchors, "
          f"{len(lower)} are lower.")
    print(f"The immediately preceding month, {prev['anchor_month']}, gives "
          f"{prev['n_skus_positive_30d']} - "
          f"{prev['n_skus_positive_30d'] / max(current['n_skus_positive_30d'], 1):.1f}x as many "
          f"priced SKUs, from a window one month earlier.")
    print("Both sit in the same summer term, so 'it is a break month' does not")
    print("by itself account for the difference.")

    # ---- gates -----------------------------------------------------
    failures = []

    def expect(label, actual, expected):
        ok = actual == expected
        print("[%s] %-38s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                   "" if ok else "   != expected %r" % (expected,)))
        if not ok:
            failures.append(label)

    print("\n=== gates ===")
    expect("anchors == distinct months in series", len(rows), len(months))
    expect("eligible F+S SKUs", matrix.shape[0], EXP_ELIGIBLE)
    expect(f"{EXP_ANCHOR} positive at 30d",
           by_anchor[EXP_ANCHOR]["n_skus_positive_30d"], EXP_POSITIVE_30D)
    expect(f"{EXP_ANCHOR} positive at 365d",
           by_anchor[EXP_ANCHOR]["n_skus_positive_365d"], EXP_POSITIVE_365D)
    expect("anchors missing a term tag",
           sum(1 for r in rows if r["term_tag"] in ("", "UNKNOWN")), 0)
    expect("anchors with 365d < 30d count",
           sum(1 for r in rows if r["n_skus_positive_365d"] < r["n_skus_positive_30d"]), 0)

    print(f"\nWrote {OUT_CSV}")
    print("\nNo anchor is chosen here. That is B14.")

    if failures:
        print(f"\nFAILED: {len(failures)} gate(s). Record under 'Gate failures' "
              f"in docs/CHANGES_tyrone.md.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
