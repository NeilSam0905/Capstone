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
    is_tally_date        INTEGER DEFAULT 0,
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
