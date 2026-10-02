"""
names.py - item names the vocabulary does not know yet: the Tally Interface's
"Names to review" list, its Rename Item action, and the import rows held
until a name is settled. The routes are in app.py.

Unknown names arrive two ways:

  - on the tally sheets. step1_apply_mapping.py loads each one as a
    provisional item (the run no longer stops) and lists it in Name_Review
    with name_matcher's suggestion of which existing item it probably is;
  - in a file imported through the interface. Its rows are held in
    Pending_Import_Row instead of being rejected.

Settling a name appends ONE row to the controlled vocabulary
(data/vocab_mapping_FINAL_v5.csv) and changes nothing else in it - the same
user-directed exception "Add item" already has, extended to these
confirmations on 2026-10-02 (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 6.1):

    same item as X   raw name -> X        X keeps its name and its history
    new item         raw name -> itself

Nothing is written without a person choosing it; a "strong" suggestion is
pre-filled in the interface, never applied on its own. Held import rows are
applied as soon as their name is settled. A sheet name takes effect at the
next pipeline run, which reads the vocabulary again; until then the list
shows it as waiting for a run.

Names are compared with name_matcher.name_key (capitals and spacing
ignored), the same rule step1 uses when it reads the vocabulary.
"""
import csv
from datetime import date, datetime

import db as dbmod
import name_matcher            # scripts/, on sys.path via pipeline.py

MAX_NAME_LENGTH = 200


class ReviewError(Exception):
    """A request the interface should show as a message, with its HTTP status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------ CSV writes

def append_csv_row(path, row):
    """Append one dict to an existing CSV, in that file's own column order.

    Reads the header first and writes strictly against it, so a column added
    to the file later cannot silently shift values into the wrong field.
    A failure here is raised, never swallowed - a half-registered item is
    worse than a rejected one.
    """
    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=header).writerow(
            {k: row.get(k, "") for k in header})
    return header


def register_new_item(vocab_path, inventory_path, name, category, price, note=None):
    """Make a new item survive `step1_apply_mapping.py`, which rebuilds
    Dim_Product from scratch on every pipeline run.

    TWO files are needed, and the second is the one that is easy to miss:

      1. `vocab_mapping_FINAL_v5.csv` - the controlled vocabulary. Maps a raw
         name to its canonical form; a new item maps to itself.
      2. `USTore_inventory_excel_long.csv` - the inventory source. This is
         what actually decides the roster for an item with no sales on the
         tally sheets: step1 builds `all_items` from the canonical names
         present in the SALES and INVENTORY csvs, NOT from the vocabulary.
         A vocabulary entry alone would leave the item out of Dim_Product
         entirely at the next rebuild.

    Quantity is left blank rather than zeroed: the store has not counted this
    item yet, and writing 0 would assert a stock figure nobody measured. It
    reads as 0 until the first real count is recorded, which then supersedes
    it (catalog.load_counted_stock takes precedence over the workbook).
    """
    today_iso = date.today().isoformat()
    append_csv_row(vocab_path, {
        "raw_name": name,
        "canonical_item_name": name,
        "merged": "no",
        "row_count": 0,
        "source": "tally_interface",
        "revisit_with_store": note or "",
    })
    append_csv_row(inventory_path, {
        "Category": category,
        "Date": today_iso,
        "Item": name,
        "Price": price if price is not None else "",
        "Quantity": "",
        "Notes": f"Added via the tally interface {today_iso}; not yet counted",
    })


# ---------------------------------------------------------------- reads

def read_vocabulary(path):
    """({name_key(raw name): canonical name}, {canonical names})."""
    known, canonicals = {}, set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = (row.get("raw_name") or "").strip()
            canon = (row.get("canonical_item_name") or "").strip()
            if raw and canon:
                known.setdefault(name_matcher.name_key(raw), canon)
                canonicals.add(canon)
    return known, canonicals


def _has_table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def name_index(c, vocab_path):
    """name_key -> product_id for every name an import may use: each item's
    own name, and every sheet name the vocabulary maps onto an item. An
    import written with the sheet's names used to be rejected row by row,
    although the vocabulary knew every one of them."""
    by_item = {r["item_name"]: r["product_id"]
               for r in dbmod.rows(c, "SELECT product_id, item_name FROM Dim_Product")}
    index = {}
    try:
        known, _ = read_vocabulary(vocab_path)
    except OSError:
        known = {}
    for key, canon in known.items():
        if canon in by_item:
            index[key] = by_item[canon]
    for name, pid in by_item.items():          # an item's own name wins
        index[name_matcher.name_key(name)] = pid
    return index


def _sheet_names(c):
    if not _has_table(c, "Name_Review"):
        return []
    return dbmod.rows(c, "SELECT * FROM Name_Review ORDER BY raw_name")


def _held(c):
    """name_key -> {"raw_name", "rows", "units", "tally", "inventory"}."""
    if not _has_table(c, "Pending_Import_Row"):
        return {}
    out = {}
    for r in dbmod.rows(c, "SELECT raw_name, kind, quantity FROM Pending_Import_Row ORDER BY pending_id"):
        h = out.setdefault(name_matcher.name_key(r["raw_name"]),
                           {"raw_name": r["raw_name"], "rows": 0, "units": 0, "tally": 0, "inventory": 0})
        h["rows"] += 1
        h[r["kind"]] += 1
        if r["kind"] == "tally":
            h["units"] += r["quantity"]
    return out


def review_list(c, vocab_path):
    """What the "Names to review" card shows.

    `names`: every name still not in the vocabulary, sheet and import alike,
    strong suggestions first. `awaiting_run`: sheet names settled since the
    last pipeline run, which takes them up."""
    known, canonicals = read_vocabulary(vocab_path)
    items = dbmod.rows(c, "SELECT product_id, item_name, supplier_name FROM Dim_Product")
    ids = {r["item_name"]: r["product_id"] for r in items}
    held = _held(c)

    names, awaiting, seen = [], [], set()
    for r in _sheet_names(c):
        key = name_matcher.name_key(r["raw_name"])
        seen.add(key)
        if key in known:
            awaiting.append({"raw_name": r["raw_name"], "item_name": known[key]})
            continue
        h = held.get(key, {})
        names.append({
            "raw_name": r["raw_name"],
            "source": "sheet+import" if h else "sheet",
            "first_date": r["first_date"], "last_date": r["last_date"],
            "sheet_rows": r.get("sheet_rows"), "units": r["units"],
            "supplier_name": r["supplier_name"], "sheet_price": r["sheet_price"],
            "held_rows": h.get("rows", 0), "held_units": h.get("units", 0),
            "provisional_product_id": ids.get(r["raw_name"]),
            "suggestion": _suggestion(r["suggested_item"], r["match_strength"], r["match_reason"],
                                      ids, canonicals),
        })

    imported = [h for key, h in held.items() if key not in seen and key not in known]
    if imported:
        # No supplier in an import file, so these are compared with every
        # catalogue item and can only ever be weak suggestions.
        pool = [{"name": r["item_name"], "supplier": r["supplier_name"], "price": None, "months": ()}
                for r in items if r["item_name"] in canonicals]
        sug = name_matcher.suggest(
            [{"name": h["raw_name"], "supplier": None, "price": None, "months": ()} for h in imported], pool)
        for h in imported:
            s = sug[h["raw_name"]]
            names.append({
                "raw_name": h["raw_name"], "source": "import",
                "first_date": None, "last_date": None, "sheet_rows": 0, "units": None,
                "supplier_name": None, "sheet_price": None,
                "held_rows": h["rows"], "held_units": h["units"],
                "provisional_product_id": None,
                "suggestion": _suggestion(s["item"], s["strength"], s["why"], ids, canonicals),
            })

    rank = {"strong": 0, "weak": 1}
    names.sort(key=lambda n: (rank.get((n["suggestion"] or {}).get("strength"), 2), n["raw_name"].lower()))
    return {
        "names": names,
        "awaiting_run": awaiting,
        "strong": sum(1 for n in names if (n["suggestion"] or {}).get("strength") == "strong"),
    }


def _suggestion(item, strength, reason, ids, canonicals):
    # Only an item the vocabulary already has can be suggested: pointing a
    # name at another unsettled name would chain two guesses together.
    if not item or item not in ids or item not in canonicals:
        return None
    return {"item_name": item, "product_id": ids[item], "strength": strength, "reason": reason}


def awaiting_run_count(c, vocab_path):
    """Sheet names settled since the last pipeline run, for the staleness banner."""
    rows = _sheet_names(c)
    if not rows:
        return 0
    try:
        known, _ = read_vocabulary(vocab_path)
    except OSError:
        return 0
    return sum(1 for r in rows if name_matcher.name_key(r["raw_name"]) in known)


# --------------------------------------------------------------- writes

def hold_row(c, kind, raw_name, quantity, source_file, *, calendar_date=None,
             count_month=None, transaction_type=None, note=None, entered_by=None,
             order_type=None):
    c.execute(dbmod.PENDING_IMPORT_DDL)      # in case startup could not create it
    c.execute("""
        INSERT INTO Pending_Import_Row
            (kind, raw_name, calendar_date, count_month, quantity, transaction_type,
             note, source_file, held_at, entered_by, order_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (kind, raw_name, calendar_date, count_month, quantity, transaction_type,
          note, source_file, datetime.now().isoformat(timespec="seconds"), entered_by, order_type))


def apply_held(c, key, product_id):
    """Load every held import row for one name onto `product_id`, and stop
    holding them. Returns {"tally": n, "inventory": n}. Does not commit."""
    applied = {"tally": 0, "inventory": 0}
    if not _has_table(c, "Pending_Import_Row"):
        return applied
    dates = {r["calendar_date"]: r["date_id"]
             for r in dbmod.rows(c, "SELECT date_id, calendar_date FROM Dim_Date")}
    now = datetime.now().isoformat(timespec="seconds")
    for r in dbmod.rows(c, "SELECT * FROM Pending_Import_Row ORDER BY pending_id"):
        if name_matcher.name_key(r["raw_name"]) != key:
            continue
        if r["kind"] == "tally":
            if r["calendar_date"] not in dates:
                continue            # cannot happen for a row checked when held; left held if it does
            # entered_by is whoever imported the file, not whoever settled
            # the name: they are the one who put these numbers in.
            c.execute("""
                INSERT INTO Fact_Sales
                    (product_id, date_id, quantity_sold, imputation_flag, tally_date_flag,
                     transaction_type, entered_by, order_type)
                VALUES (?, ?, ?, 0, 0, ?, ?, ?)
            """, (product_id, dates[r["calendar_date"]], r["quantity"], r["transaction_type"] or "sale",
                  r.get("entered_by"), r.get("order_type")))
        else:
            c.execute("""
                INSERT INTO Inventory_Count (product_id, count_month, quantity, note, counted_by, date_logged)
                VALUES (?, ?, ?, ?, 'import', ?)
                ON CONFLICT (product_id, count_month) DO UPDATE SET
                    quantity = excluded.quantity, note = excluded.note, date_logged = excluded.date_logged
            """, (product_id, r["count_month"], r["quantity"], r["note"], now))
        c.execute("DELETE FROM Pending_Import_Row WHERE pending_id = ?", (r["pending_id"],))
        applied[r["kind"]] += 1
    return applied


def _find_sheet_name(c, key):
    for r in _sheet_names(c):
        if name_matcher.name_key(r["raw_name"]) == key:
            return r
    return None


def settle(c, vocab_path, inventory_path, raw_name, action, product_id=None,
           category=None, supplier_name=None):
    """Settle one name from the review list. `action`:

        same      it is the existing item `product_id`
        new       it is a new item (import-only names also need `category`,
                  optionally `supplier_name`, as in Add New Item)
        discard   drop held import rows - only for a name that is on no sheet

    Raises ReviewError for anything the person has to change. Commits."""
    raw_name = str(raw_name or "").strip()
    key = name_matcher.name_key(raw_name)
    known, canonicals = read_vocabulary(vocab_path)
    if key in known:
        raise ReviewError(409, f"'{raw_name}' is already in the vocabulary, as '{known[key]}'.")
    sheet = _find_sheet_name(c, key)
    held = _held(c).get(key)
    if sheet is None and held is None:
        raise ReviewError(404, f"'{raw_name}' is not waiting for review.")
    shown = sheet["raw_name"] if sheet else held["raw_name"]
    today = date.today().isoformat()

    if action == "discard":
        if sheet is not None:
            raise ReviewError(400, "This name is on the tally sheets, so the next run would bring it "
                                   "back. Choose the item it is, or confirm it as a new item.")
        n = 0
        for r in dbmod.rows(c, "SELECT pending_id, raw_name FROM Pending_Import_Row"):
            if name_matcher.name_key(r["raw_name"]) == key:      # every spelling of it
                c.execute("DELETE FROM Pending_Import_Row WHERE pending_id = ?", (r["pending_id"],))
                n += 1
        c.commit()
        return {"raw_name": shown, "action": "discard", "discarded": n}

    if action == "same":
        target = dbmod.one(c, "SELECT product_id, item_name FROM Dim_Product WHERE product_id = ?",
                           (_as_id(product_id),))
        if target is None:
            raise ReviewError(400, "Choose the item this name is.")
        if target["item_name"] not in canonicals:
            raise ReviewError(400, f"'{target['item_name']}' is itself waiting for review - settle it first.")
        if name_matcher.name_key(target["item_name"]) == key:
            raise ReviewError(400, "Choose a different item, or confirm this name as a new item.")
        suggested = sheet and sheet["suggested_item"] == target["item_name"]
        append_csv_row(vocab_path, {
            "raw_name": shown, "canonical_item_name": target["item_name"], "merged": "yes",
            "row_count": (sheet or {}).get("sheet_rows") or (held or {}).get("rows", 0),
            "source": "tally_interface",
            "revisit_with_store": f"Confirmed in the Tally Interface {today}: same item as "
                                  f"'{target['item_name']}'"
                                  + (f" (the {sheet['match_strength']} suggestion)" if suggested else ""),
        })
        applied = apply_held(c, key, target["product_id"])
        c.commit()
        return {"raw_name": shown, "action": "same", "item_name": target["item_name"],
                "applied": applied, "next_run": sheet is not None}

    if action == "new":
        note = f"Confirmed in the Tally Interface {today}: new item"
        if sheet is not None:
            # On the sheets, so step1 already loaded it (as a provisional
            # item); the vocabulary row is all it needs to stay.
            append_csv_row(vocab_path, {
                "raw_name": shown, "canonical_item_name": shown, "merged": "no",
                "row_count": sheet.get("sheet_rows") or 0, "source": "tally_interface",
                "revisit_with_store": note,
            })
            pid = (dbmod.one(c, "SELECT product_id FROM Dim_Product WHERE item_name = ?", (shown,))
                   or {}).get("product_id")
        else:
            category = str(category or "").strip() or "Uncategorised"
            supplier_name = str(supplier_name or "").strip() or None
            if len(shown) > MAX_NAME_LENGTH:
                raise ReviewError(400, "Item name is too long.")
            if c.execute("SELECT 1 FROM Dim_Product WHERE LOWER(TRIM(item_name)) = ?",
                         (shown.lower(),)).fetchone():
                raise ReviewError(400, "An item with that name already exists.")
            register_new_item(vocab_path, inventory_path, shown, category, None, note)
            pid = c.execute("""
                INSERT INTO Dim_Product (item_name, category, supplier_name, entry_date, is_active, is_hvl)
                VALUES (?, ?, ?, ?, 1, 0)
            """, (shown, category, supplier_name, today)).lastrowid
        applied = apply_held(c, key, pid) if pid is not None else {"tally": 0, "inventory": 0}
        c.commit()
        return {"raw_name": shown, "action": "new", "item_name": shown,
                "applied": applied, "next_run": sheet is not None}

    raise ReviewError(400, "Choose same item, new item or discard.")


def rename(c, vocab_path, product_id, new_name):
    """Rename Item: the tally sheet now calls an existing item something else.

    The new name is added to the vocabulary as another name for the item, so
    the item keeps its history and the name the dashboard shows - the
    convention the July 2026 renames followed. Showing the sheet's new name
    instead would rewrite the item's existing vocabulary rows, which is the
    group's decision (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 3.3). Commits."""
    product = dbmod.one(c, "SELECT product_id, item_name FROM Dim_Product WHERE product_id = ?",
                        (_as_id(product_id),))
    if product is None:
        raise ReviewError(404, "No such item.")
    new_name = str(new_name or "").strip()
    if not new_name:
        raise ReviewError(400, "Enter the name the tally sheet now uses.")
    if len(new_name) > MAX_NAME_LENGTH:
        raise ReviewError(400, "That name is too long.")
    item = product["item_name"]
    known, canonicals = read_vocabulary(vocab_path)
    if item not in canonicals:
        raise ReviewError(400, f"'{item}' is itself waiting under Names to review - settle it there.")
    key = name_matcher.name_key(new_name)
    if key in known:
        if known[key] == item:
            raise ReviewError(409, f"'{new_name}' is already a name for '{item}'.")
        raise ReviewError(400, f"'{new_name}' is already the name of '{known[key]}'.")
    for other in dbmod.rows(c, "SELECT item_name FROM Dim_Product WHERE product_id != ?", (product["product_id"],)):
        if name_matcher.name_key(other["item_name"]) == key and other["item_name"] in canonicals:
            raise ReviewError(400, f"An item called '{other['item_name']}' already exists. Making two "
                                   f"items one is the group's decision (data/vocab_merge_candidates_2026-10.csv).")
    sheet = _find_sheet_name(c, key)
    append_csv_row(vocab_path, {
        "raw_name": sheet["raw_name"] if sheet else new_name, "canonical_item_name": item, "merged": "yes",
        "row_count": (sheet or {}).get("sheet_rows") or 0, "source": "tally_interface",
        "revisit_with_store": f"Renamed in the Tally Interface {date.today().isoformat()}: "
                              f"the tally sheet now calls '{item}' '{new_name}'",
    })
    applied = apply_held(c, key, product["product_id"])
    c.commit()
    return {"item_name": item, "new_name": new_name, "applied": applied,
            "was_waiting": sheet is not None}


def _as_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
