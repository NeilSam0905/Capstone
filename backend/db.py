"""
db.py — the only place this backend opens a connection.

Points at the repo-root ustore.db: the same file create_schema.py builds and
the ETL pipeline populates. This backend does not seed or reshape it; it
reads and, for the tally/closure/event endpoints, writes to it, per the
build plan's "one SQLite database is the single source of truth."
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "ustore.db"
INVENTORY_CSV = Path(__file__).resolve().parent.parent / "USTore_inventory_excel_long_mapped.csv"


def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"{DB_PATH} not found - rebuild it first (see the repo README)."
        )
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # create_schema.py defines no indexes; every endpoint here joins
    # Fact_Sales to Dim_Product/Dim_Date on every request (not a one-off
    # script run), so these matter for response time. Doesn't touch
    # create_schema.py itself - purely a read-path optimisation on the
    # existing schema, idempotent, no data/expected-value change.
    con.execute("CREATE INDEX IF NOT EXISTS idx_fact_sales_product ON Fact_Sales(product_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fact_sales_date ON Fact_Sales(date_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_dim_date_calendar_date ON Dim_Date(calendar_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_result_prescriptive_product ON Result_Prescriptive(product_id)")
    return con


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


def one(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None
