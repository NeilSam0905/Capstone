"""
db.py — the only place this backend opens a connection.

Points at the repo-root ustore.db: the same file create_schema.py builds and
the ETL pipeline populates. This backend does not seed or reshape it; it
reads and, for the tally/closure/event endpoints, writes to it, per the
build plan's "one SQLite database is the single source of truth."

Concurrency note (this matters because of the "Run Full Pipeline" button):
while a pipeline run is in flight, ten separate `python scripts/*.py`
processes take long write transactions against this same file — step2 alone
rewrites 84k Fact_Sales rows. Two things here keep the API answering
during that window instead of 500-ing:

1. **WAL journal mode**, set once at startup. In the default rollback
   journal a writer blocks every reader for the whole transaction; under
   WAL, readers see the last committed snapshot and never wait on the
   writer. WAL is a persistent property of the database file, so the
   pipeline scripts (plain `sqlite3.connect`) get it too.
2. **The indexes are created once at startup, not per request.** They used
   to be created on every `get_db()`, which made *every* API call - including
   pure reads and the pipeline-status poll's neighbours - open a write
   transaction. During a pipeline run those all queued behind the ETL
   writer and died on "database is locked".

`busy_timeout` is raised from SQLite's 5s default for the same reason: a
write that lands mid-checkpoint should wait, not fail.
"""
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "ustore.db"
INVENTORY_CSV = Path(__file__).resolve().parent.parent / "data" / "USTore_inventory_excel_long_mapped.csv"

BUSY_TIMEOUT_MS = 30_000

# The demand side of Fact_Sales, for any query aliasing it `f`. Historical rows
# are 'sale'; the Tally Interface also records damaged, promo and transfer
# removals, which leave the shelf but are not demand. Case-insensitive so rows
# written in upper case before writes were normalised still count.
SALE_ONLY = "LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'"

# create_schema.py defines no indexes; every endpoint here joins Fact_Sales to
# Dim_Product/Dim_Date on every request (not a one-off script run), so these
# matter for response time. Doesn't touch create_schema.py itself - purely a
# read-path optimisation on the existing schema, idempotent, no data or
# expected-value change.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_fact_sales_product ON Fact_Sales(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_fact_sales_date ON Fact_Sales(date_id)",
    "CREATE INDEX IF NOT EXISTS idx_dim_date_calendar_date ON Dim_Date(calendar_date)",
    "CREATE INDEX IF NOT EXISTS idx_result_prescriptive_product ON Result_Prescriptive(product_id)",
)

# Operational tables this backend writes that an older ustore.db may not have
# yet (create_schema.py defines the same ones; IF NOT EXISTS makes the two
# agree whichever runs first).
#
# Product_Status: items the store has marked discontinued from the Tally
# Interface. Keyed by item_name, not product_id, because step1 rebuilds
# Dim_Product from scratch on every pipeline run and a product_id is not
# guaranteed to survive that; the name is.
PRODUCT_STATUS_DDL = """CREATE TABLE IF NOT EXISTS Product_Status (
    item_name     TEXT PRIMARY KEY,
    discontinued  INTEGER NOT NULL DEFAULT 0,
    changed_at    TEXT
)"""

# Pending_Import_Row: rows of an imported tally or stock-count file whose item
# name the vocabulary does not know yet. Held rather than rejected, because a
# tally import appends - re-importing the file once the name is settled would
# count every row that DID load a second time. Applied, and deleted, when the
# name is confirmed under "Names to review" (backend/names.py).
PENDING_IMPORT_DDL = """CREATE TABLE IF NOT EXISTS Pending_Import_Row (
    pending_id        INTEGER PRIMARY KEY,
    kind              TEXT    NOT NULL,   -- 'tally' | 'inventory'
    raw_name          TEXT    NOT NULL,
    calendar_date     TEXT,               -- tally rows: 'YYYY-MM-DD'
    count_month       TEXT,               -- inventory rows: 'YYYY-MM'
    quantity          INTEGER NOT NULL,
    transaction_type  TEXT,               -- tally rows, lower case
    note              TEXT,               -- inventory rows
    source_file       TEXT,
    held_at           TEXT
)"""
_TABLES = (PRODUCT_STATUS_DDL, PENDING_IMPORT_DDL)

_init_lock = threading.Lock()
_initialised = False


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return con


def ensure_initialised(force=False):
    """WAL + indexes, once per process. Best-effort: if the pipeline happens
    to hold the write lock right now, the API still works (just without the
    indexes until the next attempt), which is strictly better than refusing
    to serve. `force=True` re-arms it - the pipeline calls that after a run,
    because step1/step2 drop and reload the tables these indexes sit on."""
    global _initialised
    with _init_lock:
        if _initialised and not force:
            return
        try:
            con = _connect()
            try:
                con.execute("PRAGMA journal_mode = WAL")
                for stmt in (*_TABLES, *_INDEXES):
                    con.execute(stmt)
                con.commit()
            finally:
                con.close()
            _initialised = True
        except sqlite3.Error:
            _initialised = False


def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"{DB_PATH} not found - rebuild it first (see the repo README)."
        )
    ensure_initialised()
    return _connect()


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


def one(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None
