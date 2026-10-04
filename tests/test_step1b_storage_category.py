"""
tests/test_step1b_storage_category.py
------------------------------------------------------------------
`storage_category` is the ORIGINAL inventory-sheet grouping (APPAREL /
NON-APPAREL / MAIN STORAGE) that step1b preserves before it repoints
`Dim_Product.category` at the semantic forecast categories.
`forecasting.category.storage_category_sql()` reads it through a
COALESCE, and five call sites depend on that - step5a's lead-time tiers,
tools/cold_start_donor_test.py's `category` donor rule,
scripts/model_benchmark_category.py and tools/aggregation_density_test.py.

The backfill used to live inside `if "storage_category" not in cols`, so
it fired exactly once. step1_apply_mapping.py opens with
"DELETE FROM Dim_Product" and re-appends build_dim_product()'s ten
columns, which do not include this one - so every pipeline run after the
first left the column present and empty, the COALESCE fell through to
`category`, and the five readers got the semantic label where they meant
the storage tag. Result_Prescriptive was unaffected (the two lead-time
tiers `category` separates are both 18 days) but the donor rule moved
from fill 0.1412 to 0.1384, inverting docs/COLD_START_ANALOG.md, and
nothing in the suite noticed: tests/test_cold_start_donor.py is
synthetic by design.

These three pin the three states the backfill has to get right, against
an in-memory fixture rather than ustore.db.
------------------------------------------------------------------
"""
import sqlite3

import step1b_categorize_products as step1b  # conftest.py puts scripts/ on sys.path

# (product_id, item_name, storage grouping as step1 writes it, semantic label)
CATALOGUE = [
    (1, "Black Hoodie Embro", "APPAREL",      "Outerwear"),
    (2, "Yellow Id Lanyard",  "NON-APPAREL",  "Lanyards & IDs"),
    (3, "UST OAT MUG (B)",    "MAIN STORAGE", "Drinkware"),
    (4, "Sci Notebook",       None,           "Stationery"),   # never had a tag
]


def _dim_product_db():
    """Dim_Product as step1_apply_mapping.py leaves it: the storage grouping
    in `category`, and neither of step1b's own columns present yet."""
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE Dim_Product (
            product_id INTEGER PRIMARY KEY,
            item_name  TEXT NOT NULL,
            category   TEXT,
            fsn_class  TEXT
        );
    """)
    con.executemany(
        "INSERT INTO Dim_Product (product_id, item_name, category, fsn_class) "
        "VALUES (?, ?, ?, 'S')",
        [(pid, name, storage) for pid, name, storage, _semantic in CATALOGUE])
    con.commit()
    return con


def _read(con):
    return {row[0]: (row[1], row[2]) for row in con.execute(
        "SELECT product_id, category, storage_category FROM Dim_Product")}


def _rerun_step1(con):
    """What step1_apply_mapping.py:360-361 does to this table: DELETE, then
    re-append a frame carrying neither storage_category nor
    forecast_category, so both come back NULL with `category` reset to the
    inventory sheet's grouping."""
    con.execute("DELETE FROM Dim_Product")
    con.executemany(
        "INSERT INTO Dim_Product (product_id, item_name, category, fsn_class) "
        "VALUES (?, ?, ?, 'S')",
        [(pid, name, storage) for pid, name, storage, _semantic in CATALOGUE])
    con.commit()


def test_first_run_preserves_the_storage_grouping():
    con = _dim_product_db()
    step1b.ensure_column(con)
    step1b.assign_categories(con)

    got = _read(con)
    for pid, _name, storage, semantic in CATALOGUE:
        assert got[pid] == (semantic, storage), (
            f"product {pid}: expected category={semantic!r} "
            f"storage_category={storage!r}, got {got[pid]}")


def test_storage_grouping_survives_a_second_pipeline_run():
    """The regression. Before the fix the column existed after run 1, so the
    guarded backfill never fired again and every value stayed NULL."""
    con = _dim_product_db()
    step1b.ensure_column(con)
    step1b.assign_categories(con)

    _rerun_step1(con)
    assert all(v[1] is None for v in _read(con).values()), (
        "fixture check: step1 should have left storage_category NULL")

    step1b.ensure_column(con)
    step1b.assign_categories(con)

    got = _read(con)
    for pid, _name, storage, semantic in CATALOGUE:
        assert got[pid] == (semantic, storage), (
            f"product {pid} after a second run: expected category={semantic!r} "
            f"storage_category={storage!r}, got {got[pid]}")


def test_a_semantic_label_is_never_copied_into_the_storage_slot():
    """step1b run again on its own - or a row added through the Tally
    Interface, which backend/app.py defaults to "Uncategorised" - leaves
    `category` already holding a semantic label. Backfilling from it would
    write a forecast category into the storage slot, which is worse than
    leaving the gap visible."""
    con = _dim_product_db()
    step1b.ensure_column(con)
    step1b.assign_categories(con)
    con.execute("UPDATE Dim_Product SET storage_category = NULL")
    con.commit()

    step1b.assign_categories(con)

    semantic_labels = {label for label, _pattern in step1b.CATEGORY_RULES}
    semantic_labels.add(step1b.RESIDUE)
    for pid, (category, storage) in _read(con).items():
        assert storage is None, (
            f"product {pid}: storage_category should stay NULL with nothing "
            f"authentic to copy, got {storage!r}")
        assert category in semantic_labels, (
            f"product {pid}: category should still be semantic, got {category!r}")
