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

# A sale's price, when it is not the sheet price on Dim_Product: the price
# typed with the sale (Sales Tally's optional Price field), else the listed
# price in force on the sale's date (Item_Price_Change, Edit price). Older
# sales therefore keep the price they sold at. Needs Fact_Sales f and Dim_Date d.
PRICE_IN_FORCE = """(SELECT pc.price_php FROM Item_Price_Change pc
    WHERE pc.product_id = f.product_id AND pc.effective_from <= d.calendar_date
    ORDER BY pc.effective_from DESC, pc.change_id DESC LIMIT 1)"""
OWN_PRICE = f"COALESCE(f.entry_price_php, {PRICE_IN_FORCE})"

# Selected AS entry_units and entry_php beside SUM(quantity_sold), these are
# what pesos() needs. Every peso figure goes through pesos(), so a report
# cannot count a sale's price differently from another.
ENTRY_UNITS = f"SUM(CASE WHEN {OWN_PRICE} IS NOT NULL THEN f.quantity_sold ELSE 0 END)"
ENTRY_PHP = f"SUM(COALESCE(f.quantity_sold * {OWN_PRICE}, 0))"
ENTRY_PRICES = f"COUNT(DISTINCT {OWN_PRICE})"


def pesos(units, price, entry_units=0, entry_php=0.0):
    """What `units` sold for: `entry_units` of them at their own prices
    (`entry_php` in all), the rest at the listed `price`. None when none of
    them has a price: an unpriced item with no priced sale counts in units
    only, as it always has."""
    if price is None:
        return entry_php if entry_units else None
    return (units - entry_units) * price + entry_php

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
    held_at           TEXT,
    entered_by        TEXT,               -- who imported it; carried into Fact_Sales
    order_type        TEXT                -- tally rows: walk_in / bulk / pre_order
)"""

# App_User: the accounts that can sign in to the API (backend/auth.py).
# Passwords are werkzeug hashes. Operational, like Product_Status: no
# pipeline step reads or clears it.
APP_USER_DDL = """CREATE TABLE IF NOT EXISTS App_User (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    created_at     TEXT
)"""
# Restock_Log: each restock order and the day it arrived, recorded in the
# Tally Interface, so lead times are measured per supplier instead of taken
# from the verbal 14 / 18 / 28-day estimates (step5a_set_lead_times.py uses a
# supplier's median once it has enough deliveries). Operational: no pipeline
# step clears it.
RESTOCK_LOG_DDL = """CREATE TABLE IF NOT EXISTS Restock_Log (
    restock_id    INTEGER PRIMARY KEY,
    supplier_name TEXT    NOT NULL,   -- normalised, as on Dim_Product
    ordered_on    TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    delivered_on  TEXT,               -- NULL until it arrives
    note          TEXT,
    entered_by    TEXT,
    date_logged   TEXT
)"""
# Upcoming_Order: bulk / organisation orders and pre-orders the store knows
# are coming. The forecasts add each on its expected date, on top of everyday
# (walk-in) demand (scripts/order_types.py). Operational.
UPCOMING_ORDER_DDL = """CREATE TABLE IF NOT EXISTS Upcoming_Order (
    order_id      INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL,
    expected_date TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    quantity      INTEGER NOT NULL,
    order_type    TEXT    NOT NULL,   -- 'bulk' | 'pre_order'
    note          TEXT,
    entered_by    TEXT,
    date_logged   TEXT
)"""
# Sales_Goal: the store's sales goal in pesos for an academic year, a term or
# a quarter, entered on the Summary Report screen and checked against there
# (backend/summary_report.py). Keyed by the period, not by dates, so a goal
# stays with its term if the school calendar moves the term's dates.
# Operational: no pipeline step reads or clears it.
SALES_GOAL_DDL = """CREATE TABLE IF NOT EXISTS Sales_Goal (
    period_kind  TEXT NOT NULL,   -- 'year' | 'term' | 'quarter'
    period_key   TEXT NOT NULL,   -- '2026' (A.Y. 2026-2027) | 'AY2627-T1' | '2026-Q3'
    goal_php     REAL NOT NULL,
    set_by       TEXT,
    set_at       TEXT,
    PRIMARY KEY (period_kind, period_key)
)"""
# Item_Price_Change: a listed price edited in the Tally Interface, from a date
# on. Sales before that date keep their price; the rows are the price history,
# so they are only ever added to. Dim_Product.unit_price_php stays the price
# on the store's sheets, which step1 recalculates. Operational: no pipeline
# step clears it, and step1 moves it with an item that is merged into another.
ITEM_PRICE_CHANGE_DDL = """CREATE TABLE IF NOT EXISTS Item_Price_Change (
    change_id       INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL,
    effective_from  TEXT    NOT NULL,   -- 'YYYY-MM-DD', the first day at this price
    price_php       REAL    NOT NULL,
    previous_php    REAL,               -- the price it replaced, NULL if unpriced
    note            TEXT,
    changed_by      TEXT,
    changed_at      TEXT
)"""
ITEM_PRICE_CHANGE_INDEX = """CREATE INDEX IF NOT EXISTS ix_price_change_item
    ON Item_Price_Change (product_id, effective_from)"""
_TABLES = (PRODUCT_STATUS_DDL, PENDING_IMPORT_DDL, APP_USER_DDL, RESTOCK_LOG_DDL, UPCOMING_ORDER_DDL,
           SALES_GOAL_DDL, ITEM_PRICE_CHANGE_DDL, ITEM_PRICE_CHANGE_INDEX)

# Columns added after databases already existed. CREATE TABLE IF NOT EXISTS
# cannot add a column to a table that is already there, so each is added here
# when missing. Fact_Sales.entered_by is the signed-in user for rows the Tally
# Interface writes; pipeline-loaded rows (tally_date_flag = 1) leave it NULL.
_COLUMNS = (
    ("Fact_Sales", "entered_by", "TEXT"),
    ("Pending_Import_Row", "entered_by", "TEXT"),
    # walk_in / bulk / pre_order (scripts/order_types.py); NULL = walk-in
    ("Fact_Sales", "order_type", "TEXT"),
    ("Pending_Import_Row", "order_type", "TEXT"),
    # the Tally_Correction a corrected workbook row came from
    ("Fact_Sales", "correction_id", "INTEGER"),
    # the price one sale went at, typed in Sales Tally (see pesos() below);
    # NULL = the item's listed price
    ("Fact_Sales", "entry_price_php", "REAL"),
)


def _add_missing_columns(con):
    for table, column, decl in _COLUMNS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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
                for stmt in _TABLES:
                    con.execute(stmt)
                # Committed before the indexes: a write that needs entered_by
                # must not lose it to an index that could not be built.
                _add_missing_columns(con)
                con.commit()
                for stmt in _INDEXES:
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
