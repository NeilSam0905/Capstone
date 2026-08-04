"""
tools/tier_counts.py
------------------------------------------------------------------
Divergence Register #17, closed.

Three numbers for "the tier counts" are in circulation, and the register
marks the item "fix, don't explain". It turns out there was never an
arithmetic conflict to fix: the three numbers describe three different
POPULATIONS, and each is correct about its own.

    87 / 56 / 162 = 305    pre-canonicalisation, pre-zero-fill,
                           all moving SKUs
    89 in the >=60 tier    ~ the current >=60 figure, all moving SKUs
    (PROJECT_LOG)
    38 / 10 / 10 = 58      Fast SKUs only - what step4 actually routes
    (README)

The first two count every SKU that ever moved. The third counts only
fsn_class = 'F', which is the population `step4_prophet_forecast.py`
actually iterates over. 58 is the size of the F class, so the README
figure is not a subset-of-a-subset - it is the whole of what step4 sees.

This script prints both populations side by side on the one definition
that Block 2.1 established as correct: a "sale-day" is a distinct
calendar date with quantity_sold > 0. Counting raw Fact_Sales rows
instead would count zero-fill padding as observations and collapse every
SKU into the top tier (305 / 0 / 0).

Tier thresholds, matching step4_prophet_forecast.py exactly:
    >= 60 sale-days   standard
    30-59 sale-days   simplified
    <  30 sale-days   rolling_average

Run:
    python tools/tier_counts.py

Exit 0 = both populations match their expected split.
------------------------------------------------------------------
"""
import sqlite3
import sys

DB_NAME = "ustore.db"

# ---- expected values: inputs, not outputs -------------------------
EXP_ALL_MOVING = (92, 51, 123)     # sums to 266 = products with >0 total units
EXP_FAST_ONLY = (38, 10, 10)       # sums to  58 = the F class
EXP_ALL_MOVING_TOTAL = 266
EXP_FAST_TOTAL = 58

# The historical figures this reconciles, for the record.
HISTORICAL = [
    ("87 / 56 / 162 = 305", "pre-canonicalisation, pre-zero-fill, all moving SKUs"),
    ("89 in the >=60 tier", "PROJECT_LOG; ~ the current >=60 figure, all moving SKUs"),
    ("38 / 10 / 10 = 58", "README; Fast SKUs only - what step4 routes"),
]


def tier_of(n_sale_days):
    if n_sale_days >= 60:
        return "standard"
    if n_sale_days >= 30:
        return "simplified"
    return "rolling_average"


def sale_days_by_product(con):
    """Distinct calendar dates with a non-zero sale, per product. This is
    Block 2.1's corrected definition and matches step4's obs_counts."""
    return dict(con.execute("""
        SELECT f.product_id, COUNT(DISTINCT d.calendar_date)
        FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE f.quantity_sold > 0
        GROUP BY f.product_id
    """).fetchall())


def split(product_ids, sale_days):
    counts = {"standard": 0, "simplified": 0, "rolling_average": 0}
    for pid in product_ids:
        counts[tier_of(sale_days.get(pid, 0))] += 1
    return (counts["standard"], counts["simplified"], counts["rolling_average"])


def main():
    con = sqlite3.connect(DB_NAME)
    sale_days = sale_days_by_product(con)

    moving = [r[0] for r in con.execute("""
        SELECT product_id FROM Fact_Sales
        GROUP BY product_id HAVING SUM(quantity_sold) > 0
    """).fetchall()]

    fast = [r[0] for r in con.execute(
        "SELECT product_id FROM Dim_Product WHERE fsn_class = 'F'").fetchall()]
    con.close()

    all_moving = split(moving, sale_days)
    fast_only = split(fast, sale_days)

    print("Divergence Register #17 - three numbers, three populations\n")
    print("Previously in circulation:")
    for figure, what in HISTORICAL:
        print("   %-22s %s" % (figure, what))

    print("\nBoth populations, on distinct non-zero sale-days:\n")
    print("   %-26s %10s %12s %16s %8s" %
          ("population", ">=60", "30-59", "<30", "total"))
    print("   " + "-" * 74)
    for label, sp in (("all moving SKUs (>0 units)", all_moving),
                      ("Fast SKUs only (step4)", fast_only)):
        print("   %-26s %10d %12d %16d %8d" % (label, sp[0], sp[1], sp[2], sum(sp)))

    failures = []

    def expect(label, actual, expected):
        ok = actual == expected
        print("\n[%s] %-30s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                     "" if ok else "   != expected %r" % (expected,)))
        if not ok:
            failures.append((label, actual, expected))

    expect("all moving SKUs", all_moving, EXP_ALL_MOVING)
    expect("Fast SKUs only", fast_only, EXP_FAST_ONLY)
    expect("all-moving total", sum(all_moving), EXP_ALL_MOVING_TOTAL)
    expect("Fast total (= the F class)", sum(fast_only), EXP_FAST_TOTAL)

    print("\nReading: the two rows are not in conflict and never were. The Fast-only")
    print("row is the one that describes what step4_prophet_forecast.py routes; the")
    print("all-moving row is the one the 87/56/162 and 89 figures were counting.")

    if failures:
        print("\nFAILED: %d check(s). Record under 'Gate failures' in "
              "CHANGES_tyrone.md; do not edit the expected values." % len(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
