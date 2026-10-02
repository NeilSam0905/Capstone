"""
tests/test_names_api.py
------------------------------------------------------------------
The Tally Interface's side of "using the system without a developer"
(backend/names.py, routes in backend/app.py), against a throwaway database
and throwaway copies of the vocabulary and inventory CSVs - never the real
files:

  - Names to review lists what step1 could not name, with its suggestion;
    settling a name appends exactly one vocabulary row and nothing else;
  - an import recognises tally-sheet names the vocabulary already maps, and
    HOLDS rows with unknown names instead of rejecting them, applying them
    when the name is settled;
  - Rename Item adds the sheet's new name to an existing item;
  - Add tally workbook accepts only a workbook with tally sheets, and never
    overwrites one in place.
------------------------------------------------------------------
"""
import csv
import datetime
import io
import os
import sqlite3
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import app as backend  # noqa: E402  (backend/app.py)
import db as dbmod  # noqa: E402
from step1_apply_mapping import NAME_REVIEW_DDL  # noqa: E402

VOCAB_HEADER = ["raw_name", "canonical_item_name", "merged", "row_count", "source", "revisit_with_store"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = tmp_path / "ustore.db"
    con = sqlite3.connect(db_path)
    con.executescript(f"""
        CREATE TABLE Dim_Product (product_id INTEGER PRIMARY KEY, item_name TEXT NOT NULL, category TEXT,
            unit_price_php REAL, supplier_name TEXT, payment_status TEXT, entry_date TEXT,
            is_active INTEGER, is_hvl INTEGER DEFAULT 0, fsn_class TEXT);
        CREATE TABLE Dim_Date (date_id INTEGER PRIMARY KEY, calendar_date TEXT);
        CREATE TABLE Fact_Sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, date_id INTEGER,
            quantity_sold INTEGER, imputation_flag INTEGER DEFAULT 0, tally_date_flag INTEGER DEFAULT 0,
            transaction_type TEXT DEFAULT 'sale');
        CREATE TABLE Inventory_Count (count_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL,
            count_month TEXT NOT NULL, quantity INTEGER NOT NULL, note TEXT, counted_by TEXT,
            date_logged TEXT, UNIQUE (product_id, count_month));
        CREATE TABLE Event_Log (event_id INTEGER PRIMARY KEY);
        CREATE TABLE Closure_Log (closure_id INTEGER PRIMARY KEY);
        CREATE TABLE Pipeline_Run (run_id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
            status TEXT, trigger_source TEXT, steps_ok INTEGER, steps_skipped INTEGER, steps_failed INTEGER,
            max_sale_id INTEGER, max_event_id INTEGER, max_closure_id INTEGER);
        {NAME_REVIEW_DDL};
        {dbmod.PENDING_IMPORT_DDL};
        INSERT INTO Dim_Product (product_id, item_name, supplier_name, is_active) VALUES
            (1, 'Lanyard', 'VARSITY LIFESTYLE', 1),
            (2, 'Tiger Headband (2 Designs)', 'MADEBYRUZ', 1),
            (3, 'UST TIGER HEADBAND (2 DESIGNS)', 'MADEBYRUZ', 1);   -- provisional, from step1
        INSERT INTO Dim_Date VALUES (1, '2026-08-03'), (2, '2026-08-04');
        INSERT INTO Pipeline_Run (started_at, finished_at, status, max_sale_id, max_event_id, max_closure_id)
            VALUES ('2026-09-30T10:00:00', '2026-09-30T10:02:00', 'done', 0, 0, 0);
        INSERT INTO Name_Review (raw_name, seen_in, first_date, last_date, sheet_rows, units, supplier_name,
            sheet_price, suggested_item, match_strength, match_score, match_reason, found_at)
            VALUES ('UST TIGER HEADBAND (2 DESIGNS)', 'sales', '2026-07-01', '2026-07-31', 31, 40,
                    'MADEBYRUZ', 150, 'Tiger Headband (2 Designs)', 'strong', 0.9, 'same supplier', 'now');
    """)
    con.commit()
    con.close()

    vocab = tmp_path / "vocab.csv"
    with open(vocab, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(VOCAB_HEADER)
        w.writerow(["Lanyard", "Lanyard", "no", 10, "sales", ""])
        w.writerow(["UST VL LANYARD W/ PRINT", "Lanyard", "yes", 31, "sales", "Pass6"])
        w.writerow(["Tiger Headband (2 Designs)", "Tiger Headband (2 Designs)", "no", 10, "sales", ""])
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("Category,Date,Item,Price,Quantity,Notes\n", encoding="utf-8")

    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(backend, "VOCAB_CSV", vocab)
    monkeypatch.setattr(backend, "INVENTORY_SOURCE_CSV", inventory)
    monkeypatch.setattr(backend, "RAWDATA_DIR", tmp_path / "rawdata")
    client = backend.app.test_client()
    client.vocab, client.inventory, client.db, client.tmp = vocab, inventory, db_path, tmp_path
    return client


def vocab_rows(api):
    with open(api.vocab, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def query(api, sql):
    con = sqlite3.connect(api.db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def upload(api, path, text, name):
    return api.post(path, data={"file": (io.BytesIO(text.encode()), name)}, content_type="multipart/form-data")


def test_review_lists_the_sheet_name_with_its_suggestion(api):
    body = api.get("/api/names/review").get_json()
    assert body["strong"] == 1 and body["awaiting_run"] == []
    (n,) = body["names"]
    assert n["raw_name"] == "UST TIGER HEADBAND (2 DESIGNS)" and n["source"] == "sheet"
    assert n["provisional_product_id"] == 3
    assert n["suggestion"] == {"item_name": "Tiger Headband (2 Designs)", "product_id": 2,
                               "strength": "strong", "reason": "same supplier"}


def test_settling_appends_exactly_one_vocabulary_row(api):
    before = vocab_rows(api)
    r = api.post("/api/names/review", json={"raw_name": "UST TIGER HEADBAND (2 DESIGNS)",
                                            "action": "same", "product_id": 2})
    assert r.status_code == 200 and r.get_json()["next_run"] is True
    after = vocab_rows(api)
    assert after[:-1] == before
    assert after[-1]["raw_name"] == "UST TIGER HEADBAND (2 DESIGNS)"
    assert after[-1]["canonical_item_name"] == "Tiger Headband (2 Designs)"
    assert after[-1]["source"] == "tally_interface" and after[-1]["merged"] == "yes"

    body = api.get("/api/names/review").get_json()
    assert body["names"] == []
    assert body["awaiting_run"] == [{"raw_name": "UST TIGER HEADBAND (2 DESIGNS)",
                                     "item_name": "Tiger Headband (2 Designs)"}]
    stale = api.get("/api/pipeline/staleness").get_json()
    assert stale["pending"]["name_decisions"] == 1 and stale["stale"] is True

    again = api.post("/api/names/review", json={"raw_name": "UST TIGER HEADBAND (2 DESIGNS)",
                                                "action": "new"})
    assert again.status_code == 409 and len(vocab_rows(api)) == len(after)


def test_a_sheet_name_cannot_be_discarded_or_pointed_at_itself(api):
    before = vocab_rows(api)
    assert api.post("/api/names/review", json={"raw_name": "UST TIGER HEADBAND (2 DESIGNS)",
                                               "action": "discard"}).status_code == 400
    assert api.post("/api/names/review", json={"raw_name": "UST TIGER HEADBAND (2 DESIGNS)",
                                               "action": "same", "product_id": 3}).status_code == 400
    assert vocab_rows(api) == before


def test_import_uses_sheet_names_and_holds_unknown_ones(api):
    r = upload(api, "/api/tally/import",
               "Date,Item,Quantity\n2026-08-03,ust vl lanyard w/ print,3\n2026-08-03,Brand New Pin,2\n"
               "2026-08-04,Brand New Pin,1\n2026-08-03,Lanyard,0\n", "aug.csv")
    body = r.get_json()
    assert (body["imported"], body["held"], body["rejected_total"]) == (1, 2, 1)
    assert body["held_names"] == [{"name": "Brand New Pin", "rows": 2}]
    assert query(api, "SELECT product_id, quantity_sold FROM Fact_Sales") == [(1, 3)]

    names = {n["raw_name"]: n for n in api.get("/api/names/review").get_json()["names"]}
    assert names["Brand New Pin"]["source"] == "import" and names["Brand New Pin"]["held_units"] == 3

    r = api.post("/api/names/review", json={"raw_name": "Brand New Pin", "action": "new", "category": "Pins"})
    assert r.status_code == 200 and r.get_json()["applied"] == {"tally": 2, "inventory": 0}
    assert vocab_rows(api)[-1]["canonical_item_name"] == "Brand New Pin"
    assert "Brand New Pin" in api.inventory.read_text()          # so step1 keeps it in the roster
    pid = query(api, "SELECT product_id FROM Dim_Product WHERE item_name = 'Brand New Pin'")[0][0]
    assert query(api, f"SELECT SUM(quantity_sold) FROM Fact_Sales WHERE product_id = {pid}") == [(3,)]
    assert query(api, "SELECT COUNT(*) FROM Pending_Import_Row") == [(0,)]


def test_held_rows_follow_the_item_they_are_settled_as(api):
    upload(api, "/api/tally/import", "Date,Item,Quantity\n2026-08-03,Lanyrd,4\n", "a.csv")
    upload(api, "/api/inventory/import", "Item,Units On Hand,Month\nLanyrd,9,2026-08\n", "b.csv")
    upload(api, "/api/tally/import", "Date,Item,Quantity\n2026-08-03,scribble,1\n", "c.csv")

    r = api.post("/api/names/review", json={"raw_name": "Lanyrd", "action": "same", "product_id": 1})
    assert r.get_json()["applied"] == {"tally": 1, "inventory": 1}
    assert query(api, "SELECT product_id, quantity_sold, tally_date_flag FROM Fact_Sales") == [(1, 4, 0)]
    assert query(api, "SELECT product_id, quantity FROM Inventory_Count") == [(1, 9)]

    before = vocab_rows(api)
    r = api.post("/api/names/review", json={"raw_name": "scribble", "action": "discard"})
    assert r.get_json()["discarded"] == 1
    assert vocab_rows(api) == before
    assert query(api, "SELECT COUNT(*) FROM Pending_Import_Row") == [(0,)]


def test_rename_adds_the_sheet_name_to_the_item(api):
    r = api.post("/api/products/2/rename", json={"new_name": "UST TIGER HEADBAND V2"})
    assert r.status_code == 200
    row = vocab_rows(api)[-1]
    assert (row["raw_name"], row["canonical_item_name"]) == ("UST TIGER HEADBAND V2", "Tiger Headband (2 Designs)")
    n = len(vocab_rows(api))

    assert api.post("/api/products/2/rename", json={"new_name": "ust tiger headband v2"}).status_code == 409
    assert api.post("/api/products/1/rename", json={"new_name": "UST TIGER HEADBAND V2"}).status_code == 400
    assert api.post("/api/products/2/rename", json={"new_name": "lanyard"}).status_code == 400
    assert api.post("/api/products/3/rename", json={"new_name": "Something"}).status_code == 400  # provisional
    assert api.post("/api/products/2/rename", json={"new_name": " "}).status_code == 400
    assert len(vocab_rows(api)) == n


def test_rename_to_a_waiting_sheet_name_settles_it(api):
    r = api.post("/api/products/2/rename", json={"new_name": "ust tiger headband (2 designs)"})
    assert r.status_code == 200 and r.get_json()["was_waiting"] is True
    assert vocab_rows(api)[-1]["raw_name"] == "UST TIGER HEADBAND (2 DESIGNS)"   # the sheet's spelling
    assert api.get("/api/names/review").get_json()["names"] == []


def test_add_item_still_registers_in_both_source_files(api):
    r = api.post("/api/products", json={"item_name": "Tumbler 2027", "category": "Drinkware"})
    assert r.status_code == 200
    assert vocab_rows(api)[-1]["raw_name"] == "Tumbler 2027"
    assert "Tumbler 2027" in api.inventory.read_text()


def workbook_bytes(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, day in sheets:
        ws = wb.create_sheet(title)
        ws.append(["ITEMS", day, "TOTAL QUANTITY"])
        ws.append(["TEST SUPPLIER"])
        ws.append(["Test Tote", 2, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def add_workbook(api, data, name):
    return api.post("/api/tally/workbook", data={"file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data")


def test_add_tally_workbook(api):
    rawdata = api.tmp / "rawdata"
    assert add_workbook(api, b"x", "notes.csv").status_code == 400
    assert add_workbook(api, workbook_bytes([("OPEX", datetime.datetime(2026, 8, 3))]),
                        "OPEX.xlsx").status_code == 400
    assert not rawdata.exists() or not any(rawdata.iterdir())

    aug = workbook_bytes([("AUGUST 2026 - TBS", datetime.datetime(2026, 8, 3))])
    r = add_workbook(api, aug, "USTore TBS A.Y. 2026-2027.xlsx").get_json()
    assert r["ok"] and r["saved_as"] == "USTore TBS A.Y. 2026-2027.xlsx" and r["months"] == ["2026-08"]
    assert r["replaced_file"] is None and r["takes_over"] == []

    r = add_workbook(api, aug, "USTore TBS A.Y. 2026-2027.xlsx").get_json()
    assert r["replaced_file"] and (api.tmp / r["replaced_file"]).exists()      # moved aside, not overwritten
    assert len(list((rawdata / "replaced").iterdir())) == 1

    r = add_workbook(api, aug, "import_copy.xlsx").get_json()
    assert r["saved_as"] == "tally_import_copy.xlsx"                            # step0 skips import_*
    assert r["takes_over"] == [{"month": "2026-08", "from": ["USTore TBS A.Y. 2026-2027.xlsx"]}]
    assert sorted(p.name for p in rawdata.glob("*.xlsx")) == ["USTore TBS A.Y. 2026-2027.xlsx",
                                                              "tally_import_copy.xlsx"]
    assert Path(rawdata / "tally_import_copy.xlsx").read_bytes() == aug
