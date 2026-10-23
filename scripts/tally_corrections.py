"""
tally_corrections.py - changes staff make to sales loaded from the tally
workbook, kept so they survive the next pipeline run.

step2 deletes every workbook row (Fact_Sales.tally_date_flag = 1) and loads
them again from the allocated CSV on every run, so a change made straight to
one of those rows would be lost. Tally Interface → Sales Tally → Edit entries
therefore records the change here as well as making it, and step2 applies it
again after each reload (apply).

A correction names the row by what the workbook says about it: item name
(not product_id - step1 rebuilds Dim_Product), date, quantity and whether it
is a price-group allocation. The first matching row not already claimed by an
earlier correction takes it. If the workbook itself is later fixed so no row
matches any more, the correction is simply not applied, and step2 reports it.

Fact_Sales.correction_id links a corrected row back to its correction, so a
second edit of the same row updates that correction instead of describing an
original the workbook never had.
"""

DDL = """CREATE TABLE IF NOT EXISTS Tally_Correction (
    correction_id      INTEGER PRIMARY KEY,
    item_name          TEXT    NOT NULL,   -- the row as the workbook has it
    original_date      TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    original_quantity  INTEGER NOT NULL,
    imputation_flag    INTEGER NOT NULL DEFAULT 0,
    calendar_date      TEXT,               -- what it should be
    quantity           INTEGER,
    transaction_type   TEXT,               -- lower case, as Fact_Sales stores it
    order_type         TEXT,               -- walk_in / bulk / pre_order; NULL for a removal type
    is_deleted         INTEGER NOT NULL DEFAULT 0,
    entered_by         TEXT,
    date_logged        TEXT
)"""


def ensure(con):
    """The table, and Fact_Sales.correction_id on a database built before it."""
    con.execute(DDL)
    cols = {r[1] for r in con.execute("PRAGMA table_info(Fact_Sales)")}
    if cols and "correction_id" not in cols:
        con.execute("ALTER TABLE Fact_Sales ADD COLUMN correction_id INTEGER")


def load(con):
    """Every correction, oldest first, as dicts."""
    cur = con.execute("SELECT * FROM Tally_Correction ORDER BY correction_id")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def apply(rows, corrections, product_id_by_name, date_id_by_iso):
    """Apply `corrections` to step2's loaded rows, in place.

    `rows` are step2's good_rows (product_id, date_id, iso_date,
    quantity_sold, imputation_flag, ...). A corrected row gains
    transaction_type, order_type and correction_id; a deleted one is removed.
    Returns (applied, unmatched) counts."""
    index = {}
    for i, r in enumerate(rows):
        key = (r["product_id"], r["iso_date"], r["quantity_sold"], r["imputation_flag"])
        index.setdefault(key, []).append(i)

    drop, applied, unmatched = set(), 0, 0
    for c in corrections:
        key = (product_id_by_name.get(c["item_name"]), c["original_date"],
               c["original_quantity"], c["imputation_flag"])
        free = [i for i in index.get(key, []) if i not in drop and "correction_id" not in rows[i]]
        new_date_id = date_id_by_iso.get(c["calendar_date"])
        if not free or (not c["is_deleted"] and new_date_id is None):
            unmatched += 1
            continue
        i = free[0]
        applied += 1
        if c["is_deleted"]:
            drop.add(i)
            continue
        rows[i].update(
            iso_date=c["calendar_date"], date_id=new_date_id, quantity_sold=c["quantity"],
            transaction_type=c["transaction_type"] or "sale", order_type=c["order_type"],
            correction_id=c["correction_id"],
        )
    rows[:] = [r for i, r in enumerate(rows) if i not in drop]
    return applied, unmatched
