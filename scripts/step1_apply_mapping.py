"""
Step 1 of ETL: apply the canonical-name mapping to the sales and inventory
CSVs, report any unmapped item names, and populate Dim_Product.

Does NOT do proportional allocation and does NOT touch Fact_Sales' sales
history (step2 reloads that); it only re-points rows typed into the Tally
Interface when the item they belong to was merged (see
carry_over_operational_rows).

A name the vocabulary does not know no longer stops the run. The store
renames rows on its sheets (about 50 on the July 2026 sheet), and waiting
for a developer to extend the vocabulary each time is what made the system
unusable without one. Each unknown name is loaded as a provisional item -
its own product, named as the sheet names it - and written to Name_Review
with name_matcher's suggestion of which existing item it probably is. Staff
settle it in the Tally Interface's "Names to review" list; that confirmation
appends to the vocabulary, and the next run reads the name accordingly.
Nothing here writes the vocabulary.

product_id is kept per item name across runs. Dim_Product is rebuilt from
scratch every run, and it used to be renumbered 1..N in name order, so one
new name shifted the id of every item after it - while Tally Interface
entries and stock counts (Fact_Sales rows with tally_date_flag = 0,
Inventory_Count) keep the id they were saved with. They would have moved
silently onto other products.
"""
import re
import sqlite3
import sys
from datetime import datetime

import csv
import os

import pandas as pd

import name_matcher
from step0_convert_sales_with_zeros import MAY_2024_DSR_PRICES_CSV, TBS_PRICES_CSV

# Remediation S12. Distinct from tools/audit_price_suffix_skus.py's
# PRICE_SUFFIX_RE (r"\s*@.*$"), which strips the suffix to recover the
# base name and throws the number away - this one needs the number
# itself, as a fallback price. "Lanyard @180" -> 180.
PRICE_SUFFIX_RE = re.compile(r"@(\d+)")


def price_from_suffix(item_name):
    m = PRICE_SUFFIX_RE.search(item_name)
    return float(m.group(1)) if m else None


def _read_price_rows(path):
    """Rows of a price CSV step0 wrote, in file order. Plain csv rather than
    pandas: an item name such as "NA" must stay a name, not become NaN.

    These files replace reading the workbooks here, which took ~40 s of every
    run; step0 writes them from the same workbooks while it converts them."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} is missing - run scripts/step0_convert_sales_with_zeros.py once with the tally "
            f"workbooks in rawdata/ (it writes this file), or restore it from the vault")
    with open(path, newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        next(rows)
        return list(rows)


# May 2024's workbook has a "TBS" summary sheet (what step0 reads for
# quantities) AND 23 daily "DAILY SALES REPORT" sheets ("May 2", "May 3", ...).
# Those carry a RETAIL PRICE column - a third price fallback, used only for
# canonical items inventory and the name-suffix both missed. step0 reads them
# (may2024_dsr_prices) into MAY_2024_DSR_PRICES_CSV.


def load_may2024_dsr_prices(mapping):
    """Read-only against data/vocab_mapping_FINAL_v5.csv: a raw name here that
    isn't already in the approved mapping is skipped and counted, never
    guessed at - this is a supplementary price source, not a mapping change.
    """
    raw_prices = {}  # raw item name -> [retail prices seen across the month]
    for label, price in _read_price_rows(MAY_2024_DSR_PRICES_CSV):
        raw_prices.setdefault(label, []).append(float(price))

    unmapped = set()
    canonical_prices = {}  # canonical_item_name -> [retail prices seen]
    for raw_name, prices in raw_prices.items():
        canonical = mapping.get(raw_name)
        if canonical is None:
            unmapped.add(raw_name)
            continue
        canonical_prices.setdefault(canonical, []).extend(prices)

    def mode(values):
        return max(set(values), key=values.count)

    result = {canonical: mode(prices) for canonical, prices in canonical_prices.items()}
    n_conflict = sum(1 for prices in canonical_prices.values() if len(set(prices)) > 1)

    print(f"[may2024_dsr] {len(raw_prices)} raw item name(s) with a retail price found "
          f"in the May 2024 daily sheets")
    print(f"[may2024_dsr] {len(unmapped)} raw name(s) not in the vocab mapping - skipped, "
          f"not aborting (supplementary source, not the core mapping)")
    print(f"[may2024_dsr] {len(result)} canonical item(s) priced from this source; "
          f"{n_conflict} had more than one distinct retail price across the month "
          f"(modal value kept)")
    return result


def load_tbs_item_prices(mapping):
    """Every TBS-pattern sheet, in every workbook (not just May 2024's), carries
    an ITEM PRICE column that step0 has never read - it only reads the per-date
    quantity cells. A price sometimes genuinely drifts between months (e.g.
    "UST College ID Lace" ~140 in late 2024, ~100 from 2025 onward) rather than
    being noise, so the modal value across every month an item appears in is
    kept - the same aggregation rule build_dim_product() already uses for
    inv_price. Used only for canonical items inventory, the name-suffix and
    may2024_dsr_price all missed.

    step0 reads this column from the same sheets it converts (sheet_item_prices,
    in TBS_PRICES_CSV), so a month that is in two workbooks is not counted twice
    here either. Also returns the raw prices per (sheet name, 'YYYY-MM'):
    name_matcher compares a new name's price with an old name's price in its
    LATEST month, from this same column - not with unit_price_php, which mostly
    comes from the inventory workbook and is a different figure for the same item.
    """
    raw_prices = {}
    by_month = {}
    for label, month, price in _read_price_rows(TBS_PRICES_CSV):
        raw_prices.setdefault(label, []).append(float(price))
        by_month.setdefault((label, month or None), []).append(float(price))

    unmapped = set()
    canonical_prices = {}
    for raw_name, prices in raw_prices.items():
        canonical = mapping.get(raw_name)
        if canonical is None:
            unmapped.add(raw_name)
            continue
        canonical_prices.setdefault(canonical, []).extend(prices)

    def mode(values):
        return max(set(values), key=values.count)

    result = {c: mode(p) for c, p in canonical_prices.items()}
    n_conflict = sum(1 for p in canonical_prices.values() if len(set(p)) > 1)

    print(f"[tbs_item_price] {len(raw_prices)} raw item name(s) with a price "
          f"found across every workbook's TBS sheets")
    print(f"[tbs_item_price] {len(unmapped)} raw name(s) not in the vocab mapping - skipped")
    print(f"[tbs_item_price] {len(result)} canonical item(s) priced from this source; "
          f"{n_conflict} had more than one distinct price across the months it "
          f"appeared in (modal value kept - most are real price drift, not noise)")
    return result, by_month


MAPPING_CSV = "data/vocab_mapping_FINAL_v5.csv"
SUPPLIER_MAPPING_CSV = "data/supplier_mapping.csv"
SALES_CSV = "data/USTore_sales_long_with_zeros.csv"
INVENTORY_CSV = "data/USTore_inventory_excel_long.csv"
DB_PATH = "ustore.db"

# Named after its actual input (SALES_CSV), not the older pre-zero-fill
# file - proportional_allocation.py reads this, so the name has to say
# which sales file it came from.
SALES_MAPPED_CSV = "data/USTore_sales_long_with_zeros_mapped.csv"
INVENTORY_MAPPED_CSV = "data/USTore_inventory_excel_long_mapped.csv"


def load_mapping():
    vm = pd.read_csv(MAPPING_CSV)
    vm["raw_name"] = vm["raw_name"].astype(str).str.strip()
    vm["canonical_item_name"] = vm["canonical_item_name"].astype(str).str.strip()
    return dict(zip(vm["raw_name"], vm["canonical_item_name"]))


def load_supplier_mapping():
    """42 raw Supplier strings -> 19 suppliers + a payment_status, per
    supplier_mapping.csv. Two of the raw strings resolve to no supplier at
    all (the bare "(Paid)" parser artefact and an item name typed into the
    Supplier column); those carry an empty supplier_name and are counted
    as unattributed rather than invented."""
    sm = pd.read_csv(SUPPLIER_MAPPING_CSV, dtype=str).fillna("")
    for col in ("raw_supplier", "supplier_name", "payment_status"):
        sm[col] = sm[col].str.strip()
    return (dict(zip(sm["raw_supplier"], sm["supplier_name"])),
            dict(zip(sm["raw_supplier"], sm["payment_status"])))


def apply_supplier_mapping(sales, name_map, status_map):
    raw = sales["Supplier"].astype(str).str.strip()
    unknown = sorted(set(raw) - set(name_map))
    if unknown:
        print("[supplier] UNMAPPED supplier strings:")
        for u in unknown:
            print(f"   - {u!r}")
        sys.exit(f"ABORTING: {len(unknown)} supplier string(s) missing from "
                 f"{SUPPLIER_MAPPING_CSV}. Add them there first.")
    sales = sales.copy()
    sales["supplier_name"] = raw.map(name_map).replace("", pd.NA)
    sales["payment_status"] = raw.map(status_map).replace("", pd.NA)
    n_named = int(sales["supplier_name"].notna().sum())
    print(f"[supplier] {raw.nunique()} raw strings -> "
          f"{sales['supplier_name'].nunique()} suppliers; "
          f"{len(sales) - n_named} row(s) with no attributable supplier")
    return sales


def apply_mapping(df, mapping, label):
    stripped_items = df["Item"].astype(str).str.strip()
    canonical = stripped_items.map(mapping)
    # A name typed with different capitals or spacing from its vocabulary row
    # (most likely one added from the Tally Interface's Rename) is still that
    # name - see name_matcher.name_key for why this can never pick a
    # different item. Exact matches are untouched.
    if canonical.isna().any():
        folded = {name_matcher.name_key(raw): canon for raw, canon in mapping.items()}
        missing = canonical.isna()
        canonical[missing] = stripped_items[missing].map(lambda s: folded.get(name_matcher.name_key(s)))
    unmatched_mask = canonical.isna()
    unmatched_names = sorted(stripped_items[unmatched_mask].unique().tolist())
    df = df.copy()
    df["canonical_item_name"] = canonical
    print(f"[{label}] rows processed: {len(df)}")
    print(f"[{label}] unmatched distinct item names: {len(unmatched_names)}")
    if unmatched_names:
        print(f"[{label}] UNMATCHED NAMES:")
        for name in unmatched_names:
            print(f"   - {name!r}")
    return df, unmatched_names


def load_as_provisional(df):
    """Rows whose name the vocabulary does not know keep that name as their
    item: a provisional product, until someone confirms in the Tally
    Interface which item it is."""
    df = df.copy()
    df["canonical_item_name"] = df["canonical_item_name"].fillna(df["Item"].astype(str).str.strip())
    return df


# Rebuilt every run, like Dim_Product: the names THIS run did not find in the
# vocabulary. The Tally Interface lists the ones still not in the vocabulary
# for review; a name confirmed since the run stays here until the next run
# picks the confirmation up.
NAME_REVIEW_DDL = """CREATE TABLE Name_Review (
    raw_name        TEXT PRIMARY KEY,
    seen_in         TEXT,      -- 'sales', 'inventory' or 'sales+inventory'
    first_date      TEXT,
    last_date       TEXT,
    sheet_rows      INTEGER,   -- rows (item-days) under this name
    units           REAL,      -- units sold under this name
    supplier_name   TEXT,      -- normalised, from supplier_mapping.csv
    sheet_price     REAL,      -- the sheets' ITEM PRICE in its latest month
    suggested_item  TEXT,      -- name_matcher's suggestion, or NULL
    match_strength  TEXT,      -- 'strong' | 'weak' | NULL
    match_score     REAL,
    match_reason    TEXT,
    found_at        TEXT
)"""

_YM = re.compile(r"^\d{4}-\d{2}$")


def _latest_sheet_price(labels, months, prices_by_month):
    """Modal ITEM PRICE over `labels` in the latest month that has one."""
    for ym in sorted(months, reverse=True):
        vals = [p for lab in labels for p in prices_by_month.get((lab, ym), [])]
        if vals:
            return max(set(vals), key=vals.count)
    return None


def build_name_review(provisional, sales_mapped, inventory_mapped, prices_by_month):
    """One row per provisional name, with name_matcher's suggestion."""
    if not provisional:
        return pd.DataFrame()
    sales = sales_mapped.assign(
        raw=sales_mapped["Item"].astype(str).str.strip(),
        ym=sales_mapped["Date"].astype(str).str[:7],
        qty=pd.to_numeric(sales_mapped["Total Quantity"], errors="coerce").fillna(0.0))
    sales = sales[sales["ym"].str.match(_YM)]
    inv_names = set(inventory_mapped["Item"].astype(str).str.strip())

    def describe(name, rows, labels):
        sup = rows["supplier_name"].dropna()
        months = set(rows["ym"])
        return {"name": name, "supplier": sup.mode().iloc[0] if len(sup) else None,
                "price": _latest_sheet_price(labels, months, prices_by_month), "months": months}

    existing = [describe(c, g, set(g["raw"]))
                for c, g in sales[~sales["raw"].isin(provisional)].groupby("canonical_item_name")]
    new = {n: describe(n, g, {n}) for n, g in sales[sales["raw"].isin(provisional)].groupby("raw")}
    for n in provisional:          # an inventory-only name has no sales rows
        new.setdefault(n, {"name": n, "supplier": None, "price": None, "months": set()})
    suggestions = name_matcher.suggest(list(new.values()), existing)

    found_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for n in sorted(provisional):
        g = sales[sales["raw"] == n]
        in_sales, in_inv = len(g) > 0, n in inv_names
        s = suggestions[n]
        rows.append({
            "raw_name": n,
            "seen_in": "sales+inventory" if in_sales and in_inv else "sales" if in_sales else "inventory",
            "first_date": g["Date"].min() if in_sales else None,
            "last_date": g["Date"].max() if in_sales else None,
            "sheet_rows": len(g),
            "units": float(g["qty"].sum()) if in_sales else None,
            "supplier_name": new[n]["supplier"],
            "sheet_price": new[n]["price"],
            "suggested_item": s["item"],
            "match_strength": s["strength"],
            "match_score": s["score"],
            "match_reason": s["why"],
            "found_at": found_at,
        })
    return pd.DataFrame(rows)


def assign_product_ids(dim_product, old_ids):
    """Give every item the product_id it had last run; a new item gets the
    next unused id. On an empty Dim_Product this is 1..N in name order, the
    numbering the table has always had."""
    next_id = max(old_ids.values(), default=0) + 1
    ids = []
    for name in dim_product["item_name"]:
        if name in old_ids:
            ids.append(old_ids[name])
        else:
            ids.append(next_id)
            next_id += 1
    out = dim_product.copy()
    out.insert(0, "product_id", ids)
    return out


def carry_over_operational_rows(con, old_ids, new_ids, mapping):
    """Move Tally Interface rows off items that no longer exist under that
    name because the vocabulary now maps the name onto another item - a
    provisional name confirmed as an existing item, or two items merged.

    Only rows the interface wrote are moved: Fact_Sales with tally_date_flag
    = 0 (step2 reloads the historical rows anyway) and Inventory_Count. Two
    counts for the same month end up on one item; the later-logged one is
    kept, since a count is a statement of what was on the shelf, and the
    same shelf cannot be counted twice into a total.

    Returns (moved, orphans): {(old name, new name): (sales rows, counts)}
    and {old name: rows} for app rows left pointing at an id that is gone."""
    has_counts = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Inventory_Count'").fetchone()
    folded = {name_matcher.name_key(raw): canon for raw, canon in mapping.items()}
    moved, orphans = {}, {}
    for name, old_id in old_ids.items():
        if name in new_ids:
            continue
        target = mapping.get(name) or folded.get(name_matcher.name_key(name))
        if target is None or target not in new_ids:
            n = con.execute("SELECT COUNT(*) FROM Fact_Sales WHERE product_id = ? AND tally_date_flag = 0",
                            (old_id,)).fetchone()[0]
            if has_counts:
                n += con.execute("SELECT COUNT(*) FROM Inventory_Count WHERE product_id = ?",
                                 (old_id,)).fetchone()[0]
            if n:
                orphans[name] = n
            continue
        new_id = new_ids[target]
        n_sales = con.execute("UPDATE Fact_Sales SET product_id = ? WHERE product_id = ? AND tally_date_flag = 0",
                              (new_id, old_id)).rowcount
        n_counts = 0
        if has_counts:
            for count_id, month, logged in con.execute(
                    "SELECT count_id, count_month, date_logged FROM Inventory_Count WHERE product_id = ?",
                    (old_id,)).fetchall():
                clash = con.execute("SELECT count_id, date_logged FROM Inventory_Count "
                                    "WHERE product_id = ? AND count_month = ?", (new_id, month)).fetchone()
                if clash is not None:
                    if (logged or "") <= (clash[1] or ""):
                        con.execute("DELETE FROM Inventory_Count WHERE count_id = ?", (count_id,))
                        continue
                    con.execute("DELETE FROM Inventory_Count WHERE count_id = ?", (clash[0],))
                con.execute("UPDATE Inventory_Count SET product_id = ? WHERE count_id = ?", (new_id, count_id))
                n_counts += 1
        if n_sales or n_counts:
            moved[(name, target)] = (n_sales, n_counts)
    return moved, orphans


def build_dim_product(sales_mapped, inventory_mapped, may2024_dsr_price, tbs_item_price):
    # entry_date = earliest sales date per canonical item
    sales_dates = sales_mapped.copy()
    # Every CSV in this repo stores dates as ISO 8601 (YYYY-MM-DD). errors="raise"
    # is deliberate: a coerced date becomes NaT and silently vanishes from the
    # entry_date min(), which nothing downstream would notice.
    sales_dates["parsed_date"] = pd.to_datetime(
        sales_dates["Date"], format="%Y-%m-%d", errors="raise"
    )
    entry_dates = (
        sales_dates.groupby("canonical_item_name")["parsed_date"]
        .min()
        .dt.strftime("%Y-%m-%d")
    )

    # category + unit_price_php: from inventory, most frequent non-null value per item
    def mode_or_none(series):
        s = series.dropna()
        if s.empty:
            return None
        return s.mode().iloc[0]

    inv_cat = inventory_mapped.groupby("canonical_item_name")["Category"].apply(mode_or_none)
    inv_price = inventory_mapped.groupby("canonical_item_name")["Price"].apply(mode_or_none)

    # supplier_name / payment_status: from sales, most frequent non-null value
    # per item, off the NORMALISED columns. Payment status is really a property
    # of a consignment agreement, not of a product, and a few items appear
    # under both terms - the modal value is a summary, and the count of items
    # where it isn't unanimous is reported below so it can't pass unnoticed.
    sales_supplier = sales_mapped.groupby("canonical_item_name")["supplier_name"].apply(mode_or_none)
    sales_status = sales_mapped.groupby("canonical_item_name")["payment_status"].apply(mode_or_none)

    mixed = (
        sales_mapped.dropna(subset=["payment_status"])
        .groupby("canonical_item_name")["payment_status"].nunique()
    )
    n_mixed = int((mixed > 1).sum())
    print(f"[supplier] items sold under more than one payment_status: {n_mixed} "
          f"(Dim_Product keeps the modal value)")

    all_items = sorted(
        set(sales_mapped["canonical_item_name"]) | set(inventory_mapped["canonical_item_name"])
    )

    # Remediation S12. unit_price_php previously had exactly one source -
    # the inventory sheets - and inherited that source's coverage gap
    # wholesale (239 of 519 products, 82.3% of units, unpriced; 48 of 58
    # Fast SKUs). 64 of the 71 price-suffixed products ("Lanyard @180")
    # carry the price in the name itself, the May 2024 workbook's daily
    # sheets carry a RETAIL PRICE column, and every workbook's TBS sheets
    # (every month, not just May) carry their own ITEM PRICE column - none
    # of which any script had read before now. price_source records which
    # of the four supplied the value, so they're never silently conflated -
    # a handful of items where they disagree (name-coined price vs. current
    # inventory price, most likely price drift over time) stay visible
    # rather than being overwritten one way or the other.
    price_source_counts = {
        "inventory": 0, "name_suffix": 0, "may2024_dsr": 0, "tbs_item_price": 0, None: 0,
    }
    rows = []
    for item in all_items:
        price = inv_price.get(item)
        # inv_price is a groupby().apply(mode_or_none) Series: once ANY group
        # returns a real float, pandas coerces the whole Series to float64,
        # silently turning mode_or_none's Python None (no inventory price at
        # all) into np.nan instead. `price is not None` is True for NaN, so
        # this used to mislabel a no-price item as price_source="inventory" -
        # the exact conflation this column exists to prevent. pd.notna()
        # catches both cases.
        if pd.notna(price):
            source = "inventory"
        else:
            price = price_from_suffix(item)
            if price is not None:
                source = "name_suffix"
            else:
                price = may2024_dsr_price.get(item)
                if price is not None:
                    source = "may2024_dsr"
                else:
                    price = tbs_item_price.get(item)
                    source = "tbs_item_price" if price is not None else None
        price_source_counts[source] += 1

        rows.append(
            {
                "item_name": item,
                "category": inv_cat.get(item),
                "unit_price_php": price,
                "price_source": source,
                "supplier_name": sales_supplier.get(item),
                "payment_status": sales_status.get(item),
                "lead_time_days": None,
                "fsn_class": None,
                "entry_date": entry_dates.get(item),
                "is_active": 1,
            }
        )
    print(f"[price] source: inventory={price_source_counts['inventory']}, "
          f"name_suffix={price_source_counts['name_suffix']}, "
          f"may2024_dsr={price_source_counts['may2024_dsr']}, "
          f"tbs_item_price={price_source_counts['tbs_item_price']}, "
          f"unpriced={price_source_counts[None]}")
    return pd.DataFrame(rows)


def main():
    mapping = load_mapping()

    sales = pd.read_csv(SALES_CSV)
    inventory = pd.read_csv(INVENTORY_CSV)

    sales_mapped, sales_unmatched = apply_mapping(sales, mapping, "sales")
    inventory_mapped, inventory_unmatched = apply_mapping(inventory, mapping, "inventory")

    provisional = sorted(set(sales_unmatched) | set(inventory_unmatched))
    if provisional:
        print(f"\n{len(provisional)} name(s) not in the vocabulary: loaded as provisional items and "
              f"listed under 'Names to review' in the Tally Interface. The run continues.")
        sales_mapped = load_as_provisional(sales_mapped)
        inventory_mapped = load_as_provisional(inventory_mapped)
    # A provisional item is priced from its own name and sheet rows like any other.
    priced_as = {**mapping, **{n: n for n in provisional}}
    may2024_dsr_price = load_may2024_dsr_prices(priced_as)
    tbs_item_price, prices_by_month = load_tbs_item_prices(priced_as)

    name_map, status_map = load_supplier_mapping()
    sales_mapped = apply_supplier_mapping(sales_mapped, name_map, status_map)

    sales_mapped.to_csv(SALES_MAPPED_CSV, index=False)
    inventory_mapped.to_csv(INVENTORY_MAPPED_CSV, index=False)

    dim_product = build_dim_product(sales_mapped, inventory_mapped, may2024_dsr_price, tbs_item_price)
    review = build_name_review(provisional, sales_mapped, inventory_mapped, prices_by_month)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    old_ids = dict(cur.execute("SELECT item_name, product_id FROM Dim_Product").fetchall())
    dim_product = assign_product_ids(dim_product, old_ids)
    new_ids = dict(zip(dim_product["item_name"], dim_product["product_id"]))
    moved, orphans = carry_over_operational_rows(con, old_ids, new_ids, mapping)
    cur.execute("DELETE FROM Dim_Product")
    dim_product.to_sql("Dim_Product", con, if_exists="append", index=False)
    # Dropped, not emptied: it holds nothing but this run's findings.
    cur.execute("DROP TABLE IF EXISTS Name_Review")
    cur.execute(NAME_REVIEW_DDL)
    if len(review):
        review.to_sql("Name_Review", con, if_exists="append", index=False)
    con.commit()
    row_count = cur.execute("SELECT COUNT(*) FROM Dim_Product").fetchone()[0]
    con.close()

    n_kept = sum(1 for n in new_ids if n in old_ids)
    print("\n=== SUMMARY ===")
    print(f"sales rows processed: {len(sales_mapped)}")
    print(f"inventory rows processed: {len(inventory_mapped)}")
    print(f"provisional items (names not in the vocabulary yet): {len(provisional)}")
    if len(review):
        strength = review["match_strength"].fillna("none").value_counts()
        print(f"  suggestions: {strength.get('strong', 0)} strong, {strength.get('weak', 0)} weak, "
              f"{strength.get('none', 0)} none")
    print(f"Dim_Product rows: {row_count} ({n_kept} kept their product_id, {row_count - n_kept} new)")
    if len(review):
        print("\nNames to review:")
        for r in review.itertuples():
            hint = f"-> '{r.suggested_item}' ({r.match_strength})" if r.suggested_item else "-> no suggestion"
            print(f"  - {r.raw_name!r} {hint}")
    for (old, new), (n_sales, n_counts) in sorted(moved.items()):
        print(f"  moved to '{new}' from '{old}': {n_sales} tally entr(ies), {n_counts} stock count(s)")
    for old, n in sorted(orphans.items()):
        print(f"  WARNING: {n} Tally Interface row(s) still point at '{old}', which is no longer an item "
              f"and is not mapped to one")
    print(f"\nMapped files written: {SALES_MAPPED_CSV}, {INVENTORY_MAPPED_CSV}")


if __name__ == "__main__":
    main()
