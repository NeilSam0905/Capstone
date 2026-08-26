"""
create_schema.py
------------------------------------------------------------------
Creates the SQLite database and all five tables for the USTore
Demand Forecasting & Prescriptive Inventory Management Dashboard.

HOW TO RUN:
    1. Save this file in your project folder.
    2. Open a terminal in that folder.
    3. Run:   python create_schema.py       (or: py create_schema.py on Windows)

It builds an empty database file called "ustore.db" in the same folder.
Safe to re-run: it uses CREATE TABLE IF NOT EXISTS, so it will not
overwrite tables you already have.
------------------------------------------------------------------
SQLite type notes (why the columns look different from the manuscript):
    INTEGER  -> whole numbers; booleans are stored as 0 (false) / 1 (true)
    REAL     -> decimal numbers (what the manuscript calls "float")
    TEXT     -> words; dates are stored as ISO-8601 strings 'YYYY-MM-DD'
SQLite has no separate BOOLEAN or DATE type, so we use the above. This is
standard practice and matches the ISO-8601 dates your ETL already plans to use.
------------------------------------------------------------------
"""

import sqlite3

DB_NAME = "ustore.db"   # the entire database is just this one file

# Connect to the file (this line CREATES it if it doesn't exist yet)
connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()

# Tell SQLite to actually enforce the foreign-key links between tables
cursor.execute("PRAGMA foreign_keys = ON;")


# ----- DIMENSION TABLE: Dim_Product --------------------------------
# One row per unique USTore item (SKU).
cursor.execute("""
CREATE TABLE IF NOT EXISTS Dim_Product (
    product_id      INTEGER PRIMARY KEY,
    item_name       TEXT    NOT NULL,
    category        TEXT,
    unit_price_php  REAL,
    -- Remediation S12: unit_price_php had exactly one source (inventory
    -- sheets) and inherited that source's coverage gap wholesale. This
    -- records which of the two sources (or neither) supplied the value,
    -- so a name-suffix fallback price is never silently indistinguishable
    -- from a confirmed inventory price.
    price_source    TEXT    CHECK (price_source IN ('inventory', 'name_suffix')
                                   OR price_source IS NULL),
    -- supplier_name is the NORMALISED name from supplier_mapping.csv (42 raw
    -- strings -> 19 suppliers). The "(CONSIGNMENT)"/"(PAID)" suffix the raw
    -- strings carried is split out into payment_status, which §3.2's supplier
    -- grouping needs as its own field rather than buried in the name.
    supplier_name   TEXT,
    payment_status  TEXT    CHECK (payment_status IN ('CONSIGNMENT', 'PAID', 'UNKNOWN')
                                   OR payment_status IS NULL),
    lead_time_days  INTEGER,
    fsn_class       TEXT    CHECK (fsn_class IN ('F', 'S', 'N') OR fsn_class IS NULL),
    -- HVL (High-Velocity-Limited) is a modifier on F, not a fourth class:
    -- step3 writes it here, and the CHECK above would reject 'HVL' as an
    -- fsn_class value anyway. Without this column a from-scratch rebuild
    -- runs fine until step3_fsn_classification.py, then dies on UPDATE.
    is_hvl          INTEGER DEFAULT 0,
    entry_date      TEXT,                 -- 'YYYY-MM-DD', first month the item appears
    is_active       INTEGER DEFAULT 1     -- 1 = active, 0 = discontinued
);
""")


# ----- DIMENSION TABLE: Dim_Date -----------------------------------
# One row per calendar date in the data scope. The boolean flags feed
# Prophet's academic-calendar regressors.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Dim_Date (
    date_id              INTEGER PRIMARY KEY,
    calendar_date        TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    semester_id          TEXT,
    semester_week        INTEGER,
    is_enrollment_period INTEGER DEFAULT 0,
    is_exam_week         INTEGER DEFAULT 0,
    is_event_day         INTEGER DEFAULT 0,
    is_sem_break         INTEGER DEFAULT 0,
    is_tally_date        INTEGER DEFAULT 0,   -- 608 dates: zero-inclusive, "a tally happened"
    -- Remediation S3: 411 dates, "a sale was actually recorded" - a narrower
    -- question than is_tally_date. Neither stands in for the other; state
    -- which one a denominator means. See populate_dim_date.py's docstring.
    is_tally_date_positive INTEGER DEFAULT 0,
    is_store_closed      INTEGER DEFAULT 0   -- Figure 5 labels this is_suspension_day; same idea
);
""")


# ----- FACT TABLE: Fact_Sales --------------------------------------
# The center of the star. One row per recorded item-level sales event.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Fact_Sales (
    sale_id                  INTEGER PRIMARY KEY,
    product_id               INTEGER NOT NULL,
    date_id                  INTEGER NOT NULL,
    quantity_sold            INTEGER,
    cumulative_monthly_units INTEGER,          -- derived during ETL
    daily_depletion_rate     REAL,             -- derived during ETL; NULL where
                                               -- no interval exists to divide by
    days_of_supply           REAL,             -- derived during ETL; NULL unless
                                               -- the item has an inventory count
    -- 1 = zero sale with the stock model saying the item was out; 0 = stock
    -- believed on hand; NULL = no inventory record, so it cannot be told
    -- apart from a genuine zero-demand day. See step2_load_fact_sales.py.
    is_censored              INTEGER,
    imputation_flag          INTEGER DEFAULT 0, -- 1 = split from a price-grouped record
    tally_date_flag          INTEGER DEFAULT 0, -- 1 = historical tally, 0 = live daily record
    transaction_type         TEXT    DEFAULT 'sale',
    FOREIGN KEY (product_id) REFERENCES Dim_Product (product_id),
    FOREIGN KEY (date_id)    REFERENCES Dim_Date (date_id)
);
""")


# ----- CONFIG TABLE: Dim_Parameters (sits outside the star) --------
# Editable inputs for the ROP / Safety Stock / EOQ math
# (e.g. lead time, ordering cost, holding cost).
cursor.execute("""
CREATE TABLE IF NOT EXISTS Dim_Parameters (
    parameter_id   INTEGER PRIMARY KEY,
    parameter_name TEXT    NOT NULL,
    value          REAL,
    unit           TEXT,
    last_updated   TEXT
);
""")


# ----- OPERATIONAL TABLE: Event_Log (sits outside the star) --------
# Staff-logged events not in the official UST calendar. The ETL reads
# this and flips is_event_day = 1 on the matching Dim_Date rows.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Event_Log (
    event_id          INTEGER PRIMARY KEY,
    event_date        TEXT    NOT NULL,
    event_name        TEXT    NOT NULL,
    event_description TEXT,
    created_by        TEXT,
    date_logged       TEXT
);
""")


# ----- OPERATIONAL TABLE: Closure_Log (sits outside the star) ------
# Remediation D3. Durable record of the Digital Tallying Interface's
# closure toggle, so a populate_dim_date.py rebuild can restore
# is_store_closed the same way it already restores is_event_day from
# Event_Log - previously the toggle wrote Dim_Date directly and a
# rebuild silently erased it (§3.1.1 describes the toggle updating
# Dim_Date directly; this table is a deliberate divergence, recorded in
# DIVERGENCE_REGISTER.md, applying the manuscript's own Event_Log
# pattern to closures for the same durability reason).
#
# is_closed is here because closures are toggled BOTH ways ("mark
# closed" / "mark open"), unlike events, which are never un-flagged - an
# append-only log needs a value to distinguish the two, or a reopen
# can't be told apart from a closure that was never logged. Latest row
# per closure_date (by closure_id) wins on read-back.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Closure_Log (
    closure_id    INTEGER PRIMARY KEY,
    closure_date  TEXT    NOT NULL,
    is_closed     INTEGER NOT NULL,   -- 1 = closed, 0 = reopened
    reason        TEXT,
    created_by    TEXT,
    date_logged   TEXT
);
""")


# ----- RESULT TABLE: Result_Prescriptive ---------------------------
# ROP / Safety Stock / EOQ using REAL (but still provisional) USTore
# estimates, not the abstract lead-time x cost-ratio grid this table
# used to hold.
#
# lead_time_days is now a real per-product value from Dim_Product
# (step5a_set_lead_times.py, category-based: 14/18/28 days). Holding
# cost is a single blended PHP/unit/year figure derived from USTore's
# stated inventory value (see Dim_Parameters for the arithmetic).
# Ordering cost is genuinely ambiguous (USTore's figure may be monthly
# goods value, not a per-order admin cost), so it is NOT collapsed to
# one number - every SKU is priced under BOTH a low (admin-cost) and a
# high (goods-value) interpretation, one row each, so the spread is
# visible rather than hidden behind a single choice. See deferred
# decision B9.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Result_Prescriptive (
    result_id                 INTEGER PRIMARY KEY,
    product_id                INTEGER NOT NULL,
    fsn_class                 TEXT    CHECK (fsn_class IN ('F', 'S')),  -- N is excluded
    lead_time_days            INTEGER NOT NULL,
    lead_time_category        TEXT,     -- which garment tier set this SKU's lead time
    ordering_cost_scenario    TEXT    NOT NULL,  -- 'low_admin_cost' | 'high_goods_value'
    ordering_cost_php         REAL    NOT NULL,  -- S, PHP per order
    holding_cost_php_per_unit_year REAL NOT NULL, -- H, same across scenarios
    cost_ratio                REAL,     -- S/H, kept for continuity with the EOQ theory checks
    avg_daily_demand          REAL,
    annual_demand             REAL,     -- D, annualised 30-day forecast
    sigma_demand               REAL,
    sigma_source              TEXT,     -- 'observed' or 'cv_fallback'
    z_value                   REAL,     -- 1.65 for F (95%), 1.04 for S (85%)
    safety_stock               REAL,
    reorder_point              REAL,
    eoq                        REAL,
    cost_at_eoq                REAL,    -- real PHP/year: (D/Q)*S + (Q/2)*H
    cost_at_half_eoq            REAL,
    cost_at_double_eoq          REAL,
    demand_method              TEXT,    -- which forecast fed D
    is_provisional              INTEGER DEFAULT 1,
    generated_at                TEXT,
    FOREIGN KEY (product_id) REFERENCES Dim_Product (product_id)
);
""")


# ----- OPERATIONAL TABLE: Inventory_Count --------------------------
# Staff-entered monthly stock counts, written from the Digital Tallying
# Interface's "Monthly Inventory Count" card. One row per product per
# month; re-submitting the same pair overwrites it (see the UNIQUE
# constraint below), because a recount corrects a count, it does not add
# to it.
#
# This does NOT reintroduce Dim_Inventory. §3.2 omits that deliberately -
# stock on a given DAY stays derived (beginning stock minus cumulative
# units), which is what would have made it a rapidly-changing dimension.
# What this table holds is the same thing the inventory workbook holds:
# a periodic count, at month granularity, of the kind the store already
# takes by hand. It is the digital counterpart of
# data/USTore_inventory_excel_long_mapped.csv, whose coverage is the
# project's biggest data gap (Block 3/B10: only ~17% of Fact_Sales rows
# have any stock signal, and the workbook stops at 2026-04).
#
# Operational, outside the star, like Event_Log and Closure_Log: no ETL
# step reads it and no pipeline step clears it.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Inventory_Count (
    count_id      INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL,
    count_month   TEXT    NOT NULL,   -- 'YYYY-MM'
    quantity      INTEGER NOT NULL,   -- units on hand at the count
    note          TEXT,
    counted_by    TEXT,
    date_logged   TEXT,
    UNIQUE (product_id, count_month),
    FOREIGN KEY (product_id) REFERENCES Dim_Product (product_id)
);
""")

# ----- OPERATIONAL TABLE: Pipeline_Run -----------------------------
# One row per end-to-end pipeline run (create_schema.py ->
# step5_prescriptive.py), written by backend/pipeline.py when the Tally
# Interface's "Run Full Pipeline" button is used.
#
# Its job is the staleness warning on that screen. Everything the
# Digital Tallying Interface writes - a tally entry, a flagged event, a
# store closure - lands in the database immediately, but the ANALYTICS
# derived from it (fsn_class, Result_Forecast, Result_Prescriptive) are
# only recomputed when the pipeline runs. Without a record of when that
# last happened, the dashboard has no way to tell the difference between
# "these reorder points are current" and "these reorder points predate
# the last three weeks of tallying".
#
# The high-water marks are how "since the last run" is measured without
# adding a created_at column to Fact_Sales: every table below has a
# monotonic INTEGER PRIMARY KEY, so anything with a larger id than the
# mark arrived after the run finished. Fact_Sales rows loaded BY the
# pipeline are tally_date_flag=1 and manual ones are 0, so the count
# that matters is "tally_date_flag = 0 AND sale_id > mark".
#
# Not part of the star schema and deliberately outside it - like
# Event_Log and Closure_Log, it is an operational log, and no ETL step
# reads it. It is also the one table the pipeline never clears.
cursor.execute("""
CREATE TABLE IF NOT EXISTS Pipeline_Run (
    run_id            INTEGER PRIMARY KEY,
    started_at        TEXT    NOT NULL,   -- ISO 8601 local time
    finished_at       TEXT,
    status            TEXT    NOT NULL,   -- running | done | error | cancelled
    trigger_source    TEXT,               -- 'frontend' (the Tally Interface button)
    steps_ok          INTEGER,            -- steps that completed
    steps_skipped     INTEGER,            -- optional steps that failed (step0 / step4)
    steps_failed      INTEGER,
    -- high-water marks at the moment the run finished
    max_sale_id       INTEGER,
    max_event_id      INTEGER,
    max_closure_id    INTEGER
);
""")

# Save all changes to the file
connection.commit()

# Confirm what got created
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = [row[0] for row in cursor.fetchall()]
print("Database file created:", DB_NAME)
print("Tables now inside it:")
for t in tables:
    print("   -", t)

connection.close()
