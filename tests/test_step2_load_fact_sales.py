"""
tests/test_step2_load_fact_sales.py
------------------------------------------------------------------
Remediation D2: re-running step2_load_fact_sales.py must not erase rows
the Digital Tallying Interface wrote (backend/app.py's POST /api/tally
inserts with tally_date_flag=0). The original `DELETE FROM Fact_Sales`
had no WHERE clause and cleared everything, historical AND live, on
every rebuild - undocumented anywhere, and directly against Objective
1's premise that the interface accumulates data over time.

step2_load_fact_sales.clear_historical_fact_sales() is the fix, pulled
out as its own function specifically so it's testable against a minimal
in-memory fixture rather than needing the full CSV + Dim_Product/Dim_Date
setup main() requires.
------------------------------------------------------------------
"""
import sqlite3

import step2_load_fact_sales as step2  # conftest.py puts scripts/ on sys.path


def _fact_sales_db():
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE Fact_Sales (
            sale_id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            date_id INTEGER NOT NULL,
            quantity_sold INTEGER,
            imputation_flag INTEGER DEFAULT 0,
            tally_date_flag INTEGER DEFAULT 0,
            transaction_type TEXT DEFAULT 'sale'
        );
    """)
    return con


def test_interface_written_row_survives_a_step2_reload():
    con = _fact_sales_db()
    # historical, as step2 itself writes (tally_date_flag=1)
    con.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag) "
                "VALUES (1, 100, 5, 1)")
    # interface-written, as backend/app.py's POST /api/tally writes (tally_date_flag=0)
    con.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag) "
                "VALUES (2, 200, 3, 0)")
    con.commit()

    step2.clear_historical_fact_sales(con)
    con.commit()

    remaining = con.execute(
        "SELECT product_id, quantity_sold, tally_date_flag FROM Fact_Sales"
    ).fetchall()
    assert remaining == [(2, 3, 0)], (
        f"expected only the interface-written row to survive, got {remaining}")


def test_multiple_historical_rows_are_all_cleared():
    """Population check before the negative claim above means anything -
    if the delete silently matched zero rows, the first test would pass
    vacuously too."""
    con = _fact_sales_db()
    for pid in range(5):
        con.execute("INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag) "
                    "VALUES (?, ?, 1, 1)", (pid, pid))
    con.commit()

    before = con.execute("SELECT COUNT(*) FROM Fact_Sales WHERE tally_date_flag = 1").fetchone()[0]
    assert before == 5, "fixture setup failed - test would be vacuous"

    step2.clear_historical_fact_sales(con)
    con.commit()

    after = con.execute("SELECT COUNT(*) FROM Fact_Sales").fetchone()[0]
    assert after == 0
