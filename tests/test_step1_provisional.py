"""
tests/test_step1_provisional.py
------------------------------------------------------------------
step1_apply_mapping.py, for a sheet that renamed or added items:

  - an unknown name becomes a provisional item instead of stopping the run;
  - a name typed with different capitals still matches its vocabulary row;
  - product_id stays with the item name across rebuilds. It used to be
    renumbered 1..N in name order, so one new name moved every later id
    away from the Tally Interface rows saved against it;
  - when the vocabulary maps a name onto another item (a provisional name
    settled as an existing item), the interface's rows move with it.
------------------------------------------------------------------
"""
import sqlite3

import pandas as pd

import step1_apply_mapping as step1  # conftest.py puts scripts/ on sys.path


def test_unknown_names_become_provisional_items():
    sales = pd.DataFrame({"Item": [" Tote ", "UST NEW TOTE"], "Total Quantity": [1, 2]})
    mapped, unmatched = step1.apply_mapping(sales, {"Tote": "Tote (Viva)"}, "sales")
    assert unmatched == ["UST NEW TOTE"]
    filled = step1.load_as_provisional(mapped)
    assert filled["canonical_item_name"].tolist() == ["Tote (Viva)", "UST NEW TOTE"]


def test_capitals_and_spacing_still_match_the_vocabulary():
    sales = pd.DataFrame({"Item": ["ust tiger  headband"], "Total Quantity": [1]})
    mapped, unmatched = step1.apply_mapping(
        sales, {"UST TIGER HEADBAND": "Tiger Headband (2 Designs)"}, "sales")
    assert unmatched == []
    assert mapped["canonical_item_name"].tolist() == ["Tiger Headband (2 Designs)"]


def test_ids_are_kept_by_name_and_new_items_get_new_ids():
    dim = pd.DataFrame({"item_name": ["Apron", "Bag", "Cap", "Zipper"]})
    out = step1.assign_product_ids(dim, {"Bag": 1, "Cap": 2, "Zipper": 3, "Gone": 7})
    assert dict(zip(out["item_name"], out["product_id"])) == {"Bag": 1, "Cap": 2, "Zipper": 3, "Apron": 8}


def test_first_build_numbers_in_name_order():
    dim = pd.DataFrame({"item_name": ["Apron", "Bag", "Cap"]})
    assert step1.assign_product_ids(dim, {})["product_id"].tolist() == [1, 2, 3]


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
                                 quantity_sold INTEGER, tally_date_flag INTEGER, transaction_type TEXT);
        CREATE TABLE Inventory_Count (count_id INTEGER PRIMARY KEY, product_id INTEGER, count_month TEXT,
                                      quantity INTEGER, note TEXT, counted_by TEXT, date_logged TEXT,
                                      UNIQUE (product_id, count_month));
        -- 10 = 'UST TIGER HEADBAND' (provisional), 3 = 'Tiger Headband', 11 = 'Typo Item'
        INSERT INTO Fact_Sales (product_id, date_id, quantity_sold, tally_date_flag) VALUES
            (10, 1, 5, 1),          -- historical: step2 reloads it, left alone here
            (10, 2, 2, 0),          -- typed in the Tally Interface
            (11, 2, 1, 0);
        INSERT INTO Inventory_Count (product_id, count_month, quantity, date_logged) VALUES
            (10, '2026-08', 7, '2026-08-31T09:00:00'),
            (3,  '2026-08', 4, '2026-08-30T09:00:00'),   -- same month, logged earlier
            (10, '2026-09', 6, '2026-09-30T09:00:00');
    """)
    return con


def test_settled_names_take_their_interface_rows_along():
    con = _db()
    old_ids = {"UST TIGER HEADBAND": 10, "Tiger Headband": 3, "Typo Item": 11}
    new_ids = {"Tiger Headband": 3}
    moved, orphans = step1.carry_over_operational_rows(
        con, old_ids, new_ids, {"UST TIGER HEADBAND": "Tiger Headband"})
    assert moved == {("UST TIGER HEADBAND", "Tiger Headband"): (1, 2)}
    assert orphans == {"Typo Item": 1}
    assert con.execute("SELECT product_id, tally_date_flag FROM Fact_Sales ORDER BY sale_id").fetchall() == [
        (10, 1), (3, 0), (11, 0)]
    # the later-logged August count wins; September comes across
    assert con.execute("SELECT product_id, count_month, quantity FROM Inventory_Count "
                       "ORDER BY count_month").fetchall() == [(3, "2026-08", 7), (3, "2026-09", 6)]


def test_name_review_lists_each_provisional_name_with_its_evidence():
    sales = pd.DataFrame({
        "Date": ["2026-06-01", "2026-06-02", "2026-07-01", "2026-07-02"],
        "Item": ["Tiger Headband (2 Designs)", "Tiger Headband (2 Designs)",
                 "UST TIGER HEADBAND (2 DESIGNS)", "UST TIGER HEADBAND (2 DESIGNS)"],
        "Total Quantity": [1, 2, 3, 4],
        "canonical_item_name": ["Tiger Headband (2 Designs)"] * 2 + ["UST TIGER HEADBAND (2 DESIGNS)"] * 2,
        "supplier_name": ["MADEBYRUZ"] * 4,
    })
    inventory = pd.DataFrame({"Item": []})
    prices = {("Tiger Headband (2 Designs)", "2026-06"): [150.0],
              ("UST TIGER HEADBAND (2 DESIGNS)", "2026-07"): [150.0]}
    review = step1.build_name_review(["UST TIGER HEADBAND (2 DESIGNS)"], sales, inventory, prices)
    r = review.iloc[0]
    assert (r.raw_name, r.seen_in, r.first_date, r.last_date, r.sheet_rows, r.units) == (
        "UST TIGER HEADBAND (2 DESIGNS)", "sales", "2026-07-01", "2026-07-02", 2, 7.0)
    assert (r.suggested_item, r.match_strength, r.sheet_price) == ("Tiger Headband (2 Designs)", "strong", 150.0)
