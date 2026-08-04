"""
tools/assert_invariants.py
------------------------------------------------------------------
The full data contract, as a regression gate.

This is the mechanical guard for the whole `tyrone` branch. Every value
below was re-derived from a from-scratch rebuild on 2026-08-04 and is an
INPUT to this script, not an output of it.

    Never edit an expected value to make this pass.

If SUM(quantity_sold) comes back as something other than 89,232, that is
the single most important result the run can produce, and it must be
reported rather than corrected. Same for every other row here.

Run it after any task that could move a number:

    python tools/assert_invariants.py

Exit 0 = the contract holds. Exit 1 = it does not, and the diff says where.

Note on Dim_Parameters
----------------------
`Dim_Parameters` is 0 rows in the baseline pipeline. Task A10 seeds it with
a PROVISIONAL sensitivity grid, which is a deliberate state change, not a
regression. Pass --phase a10 once that has run; the default (`baseline`)
asserts the untouched post-rebuild state.
------------------------------------------------------------------
"""
import argparse
import sqlite3
import sys

DB_NAME = "ustore.db"


class Contract:
    """Collects checks, prints one line each, and reports a diff on failure."""

    def __init__(self):
        self.results = []

    def expect(self, label, actual, expected):
        ok = actual == expected
        self.results.append((label, actual, expected, ok))
        return ok

    def report(self):
        width = max(len(r[0]) for r in self.results)
        failed = [r for r in self.results if not r[3]]

        for label, actual, expected, ok in self.results:
            status = "PASS" if ok else "FAIL"
            line = f"[{status}] {label:<{width}}  {actual!r}"
            if not ok:
                line += f"   != expected {expected!r}"
            print(line)

        print("-" * (width + 30))
        print(f"{len(self.results) - len(failed)}/{len(self.results)} checks passed.")

        if failed:
            print("\nCONTRACT VIOLATED - do not 'fix' this by editing the expected values.")
            print("Record it under 'Gate failures' in CHANGES_tyrone.md and report it.\n")
            for label, actual, expected, _ in failed:
                print(f"  {label}\n      actual   = {actual!r}\n      expected = {expected!r}")
            return 1
        return 0


def table_exists(cur, name):
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["baseline", "a10"], default="baseline",
                    help="baseline = post-rebuild; a10 = after the prescriptive grid is seeded")
    ap.add_argument("--db", default=DB_NAME)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    c = Contract()

    # ---- Dim_Date -------------------------------------------------
    c.expect("Dim_Date rows", scalar(cur, "SELECT COUNT(*) FROM Dim_Date"), 1461)
    c.expect("semester_week non-null",
             scalar(cur, "SELECT COUNT(semester_week) FROM Dim_Date"), 1453)
    c.expect("MAX(semester_week)",
             scalar(cur, "SELECT MAX(semester_week) FROM Dim_Date"), 23)
    c.expect("is_tally_date = 1",
             scalar(cur, "SELECT COUNT(*) FROM Dim_Date WHERE is_tally_date = 1"), 608)

    # ---- Fact_Sales -----------------------------------------------
    c.expect("Fact_Sales rows", scalar(cur, "SELECT COUNT(*) FROM Fact_Sales"), 84399)
    c.expect("SUM(quantity_sold)",
             scalar(cur, "SELECT SUM(quantity_sold) FROM Fact_Sales"), 89232)
    c.expect("zero-quantity rows",
             scalar(cur, "SELECT COUNT(*) FROM Fact_Sales WHERE quantity_sold = 0"), 68541)

    # ---- Dim_Product ----------------------------------------------
    c.expect("Dim_Product rows", scalar(cur, "SELECT COUNT(*) FROM Dim_Product"), 519)
    for cls, expected in (("F", 58), ("S", 228), ("N", 233)):
        c.expect(f"fsn_class = {cls}",
                 scalar(cur, f"SELECT COUNT(*) FROM Dim_Product WHERE fsn_class = '{cls}'"),
                 expected)
    c.expect("is_hvl = 1",
             scalar(cur, "SELECT COUNT(*) FROM Dim_Product WHERE is_hvl = 1"), 6)

    # ---- Referential integrity ------------------------------------
    c.expect("orphan joins to Dim_Date", scalar(cur, """
        SELECT COUNT(*) FROM Fact_Sales f
        LEFT JOIN Dim_Date d ON f.date_id = d.date_id
        WHERE d.date_id IS NULL"""), 0)
    c.expect("orphan joins to Dim_Product", scalar(cur, """
        SELECT COUNT(*) FROM Fact_Sales f
        LEFT JOIN Dim_Product p ON f.product_id = p.product_id
        WHERE p.product_id IS NULL"""), 0)

    # ---- Product coverage -----------------------------------------
    c.expect("products with >=1 Fact_Sales row",
             scalar(cur, "SELECT COUNT(DISTINCT product_id) FROM Fact_Sales"), 286)
    c.expect("products with >0 total units", scalar(cur, """
        SELECT COUNT(*) FROM (
            SELECT product_id FROM Fact_Sales
            GROUP BY product_id HAVING SUM(quantity_sold) > 0)"""), 266)

    # ---- Sales date span ------------------------------------------
    span = cur.execute("""
        SELECT MIN(d.calendar_date), MAX(d.calendar_date)
        FROM Fact_Sales f JOIN Dim_Date d ON f.date_id = d.date_id""").fetchone()
    c.expect("sales date span", list(span), ["2024-05-02", "2026-07-31"])

    # ---- Tables that Phase 5 has not populated --------------------
    # Exception_Log is created by step2_load_fact_sales.py, not create_schema.py.
    expected_params = 0 if args.phase == "baseline" else None
    c.expect("Event_Log rows", scalar(cur, "SELECT COUNT(*) FROM Event_Log"), 0)
    c.expect("Exception_Log exists", table_exists(cur, "Exception_Log"), True)
    if table_exists(cur, "Exception_Log"):
        c.expect("Exception_Log rows",
                 scalar(cur, "SELECT COUNT(*) FROM Exception_Log"), 0)

    n_params = scalar(cur, "SELECT COUNT(*) FROM Dim_Parameters")
    if args.phase == "baseline":
        c.expect("Dim_Parameters rows", n_params, expected_params)
    else:
        # A10 seeds the grid definition; every row must still be provisional.
        c.expect("Dim_Parameters rows > 0", n_params > 0, True)
        c.expect("Dim_Parameters all PROVISIONAL", scalar(cur, """
            SELECT COUNT(*) FROM Dim_Parameters
            WHERE unit IS NULL OR unit NOT LIKE '%PROVISIONAL%'"""), 0)

    con.close()
    sys.exit(c.report())


if __name__ == "__main__":
    main()
