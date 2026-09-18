"""
scripts/rebuild_extract_tbs.py
------------------------------------------------------------------
Colour-aware re-extraction of the USTore raw workbooks.

What is different from the existing step0/step1 pipeline
--------------------------------------------------------
1. DAY STATUS IS READ FROM CELL COLOUR. Every TBS sheet encodes each
   date's operating status in its header fill, with a per-sheet legend
   giving the meaning (`forecasting/day_status.py`). That is the only
   record in the project of which days the store was actually shut.

2. A CLOSED DAY IS NOT A ZERO. The existing builders reindex onto the
   full calendar with `fill_value=0`, which asserts "sold nothing" for
   213 days that have no tally sheet and for every day the store never
   opened. Here each date carries an explicit status and a
   `demand_observable` flag; the forecasting layer decides what to do
   with a non-observable day rather than being handed a fabricated zero.

3. THE 2023 BATCH FILE IS NOT RESAMPLED. `2023 total sales by batch`
   holds per-batch totals with no dates. The previous pipeline spread
   those across daily frequencies, inventing a within-batch shape that
   the source does not contain. Here they load into a separate
   `Fact_Batch_Sales` table as aggregates, and they never enter the
   daily series.

Outputs (all additive - no existing table or CSV is modified)
------------------------------------------------------------
  data/rebuild_sales_long.csv        one row per item x date x sheet
  data/rebuild_day_status.csv        one row per date: status, traded, units
  data/day_status_vocabulary.csv     every legend label + inferred status
  data/rebuild_batch_sales_2023.csv  the 2023 aggregates, undated
  data/rebuild_extract_report.csv    per-sheet parse + cross-check report
  ustore.db : Dim_Day_Status, Fact_Batch_Sales   (created, then replaced)

Assumptions, stated because they are assumptions
------------------------------------------------
  * A TBS sheet's date header row is the row in the first 8 with the most
    datetime cells; item names are in the first column; a supplier
    section header is a row whose only value is in column 1.
  * `TOTAL QUANTITY` / `ITEM PRICE` / `FOR REMITTANCE` columns are
    identified by their header text and excluded from the date columns.
  * Sheets whose names contain VOUCHER / OPEX / CASH REQ / O.R. /
    RE-CHECKING are not item-sales sheets and are skipped.
  * Item names are mapped through `data/vocab_mapping_FINAL_v5.csv` and
    suppliers through `data/supplier_mapping.csv`, both read-only.

Run:  python scripts/rebuild_extract_tbs.py [--db ustore.db] [--no-db]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import re
import sqlite3
import sys

import openpyxl
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting.day_status import (                       # noqa: E402
    DayStatus, STATUS_ORDER, classify_label, header_dates_with_status,
    legend_from_sheet, load_vocabulary_override, verify_selling_days,
    _date_header_row, _column_body, _fill,
)

RAW_DIR = os.path.join(ROOT, "rawdata")
DATA_DIR = os.path.join(ROOT, "data")

SKIP_SHEET = ("VOUCHER", "OPEX", "CASH REQ", "O.R.", "RE-CHECKING")
BATCH_FILE_HINT = "TOTAL SALES BY BATCH"
DSR_FILE_HINT = "DSR"
NON_DATE_HEADERS = ("TOTAL QUANTITY", "ITEM PRICE", "FOR REMITTANCE",
                    "TOTAL", "AMOUNT", "REMITTANCE", "PRICE")

# A status where a sale was physically possible. Everything else is a day
# on which zero units is a fact about the STORE, not about demand.
DEMAND_OBSERVABLE = {DayStatus.NORMAL, DayStatus.SELLING_SPECIAL,
                     DayStatus.SUSPENSION}


# --------------------------------------------------------------- mappings
def load_mappings():
    """Read-only lookups. Per CLAUDE.md these files are never written."""
    vocab_path = os.path.join(DATA_DIR, "vocab_mapping_FINAL_v5.csv")
    supp_path = os.path.join(DATA_DIR, "supplier_mapping.csv")
    vocab, supp = {}, {}
    if os.path.exists(vocab_path):
        v = pd.read_csv(vocab_path)
        vocab = dict(zip(v["raw_name"].astype(str).str.strip().str.upper(),
                         v["canonical_item_name"].astype(str).str.strip()))
    if os.path.exists(supp_path):
        s = pd.read_csv(supp_path)
        supp = dict(zip(s["raw_supplier"].astype(str).str.strip().str.upper(),
                        s["supplier_name"].astype(str).str.strip()))
    return vocab, supp


def canon_item(name, vocab):
    key = str(name).strip().upper()
    return vocab.get(key, str(name).strip())


def canon_supplier(name, supp):
    key = str(name).strip().upper()
    return supp.get(key, str(name).strip())


# ----------------------------------------------------------- sheet layout
def resolve_date_columns(ws, row):
    """Genuine date columns, excluding label and summary columns.

    A dated header alone is not enough: column A's header is the sheet
    title (a date), and `TOTAL QUANTITY` sits in the same row. Both are
    excluded - the first by its string-heavy body, the second by header
    text on neighbouring cells.
    """
    max_c = min(ws.max_column or 1, 80)
    summary_cols = {c for c in range(1, max_c + 1)
                    if isinstance(ws.cell(row, c).value, str)
                    and any(h in ws.cell(row, c).value.upper()
                            for h in NON_DATE_HEADERS)}
    first_summary = min(summary_cols) if summary_cols else max_c + 1

    cols = {}
    for c in range(1, max_c + 1):
        v = ws.cell(row, c).value
        if not isinstance(v, dt.datetime) or c >= first_summary:
            continue
        nums, strs, _ = _column_body(ws, row, c)
        if strs > nums and strs > 3:
            continue                      # item-label column
        cols[c] = v.date()
    return cols


def is_section_header(ws, r, first_col, date_cols):
    """A supplier band: text in column 1, nothing in any date column."""
    v = ws.cell(r, first_col).value
    if not isinstance(v, str) or not v.strip():
        return False
    return not any(ws.cell(r, c).value is not None for c in date_cols)


def parse_tbs_sheet(ws, sheet, source, vocab, supp, override):
    """One TBS sheet -> (sales rows, day rows, report dict)."""
    row, n = _date_header_row(ws)
    if not row or n < 2:
        return [], [], None
    date_cols = resolve_date_columns(ws, row)
    if len(date_cols) < 2:
        return [], [], None

    days = header_dates_with_status(ws, override, value_columns=set(date_cols))
    status_by_date = {d.date: d for d in days}

    sales, supplier = [], None
    for r in range(row + 1, (ws.max_row or 1) + 1):
        if is_section_header(ws, r, 1, list(date_cols)):
            supplier = canon_supplier(ws.cell(r, 1).value, supp)
            continue
        raw_item = ws.cell(r, 1).value
        if not isinstance(raw_item, str) or not raw_item.strip():
            continue
        item = canon_item(raw_item, vocab)
        for c, date in date_cols.items():
            q = ws.cell(r, c).value
            if isinstance(q, bool) or not isinstance(q, (int, float)):
                continue
            rec = status_by_date.get(date)
            sales.append({
                "calendar_date": date, "item_name": item,
                "raw_item_name": str(raw_item).strip(),
                "supplier_name": supplier, "quantity_sold": float(q),
                "day_status": rec.status if rec else DayStatus.UNKNOWN,
                "source_file": source, "source_sheet": sheet,
            })

    check = verify_selling_days(ws, days)
    day_rows = [{
        "calendar_date": d.date, "day_status": d.status,
        "legend_label": d.label, "fill_colour": d.colour,
        "traded": int(d.traded), "units_that_day": d.units,
        "demand_observable": int(d.status in DEMAND_OBSERVABLE),
        "is_store_closed": int(d.status == DayStatus.CLOSED),
        "source_file": source, "source_sheet": sheet,
    } for d in days]

    report = {
        "source_file": source, "source_sheet": sheet,
        "n_dates": len(days), "n_sales_rows": len(sales),
        "legend_entries": len(legend_from_sheet(ws)),
        "stated_selling_days": check[0] if check else None,
        "parsed_selling_days": check[1] if check else
            sum(1 for d in days if d.traded and d.status == DayStatus.NORMAL),
        "selling_days_match": (None if not check else int(check[2])),
        **{"n_" + s.lower(): sum(1 for d in days if d.status == s)
           for s in STATUS_ORDER},
    }
    return sales, day_rows, report


# --------------------------------------------------------- 2023 batch file
def parse_batch_2023(path, vocab):
    """The 2023 batch workbook: aggregates, ingested AS aggregates.

    Requirement #2 of the rebuild spec. These sheets carry `Item /
    Total Quantity / Amount / Item Price` with no dates at all. The
    previous pipeline resampled them to a daily frequency, which
    fabricates a within-batch shape the source never recorded. They are
    kept here as one row per item per batch, with the batch name as the
    only temporal key, and they never enter the daily series.
    """
    TOTALS = re.compile(r"^\s*(TOTAL|UST\s+SALES|GRAND\s+TOTAL|SUB\s*-?\s*TOTAL)\b",
                        re.I)
    rows = []
    wb = openpyxl.load_workbook(path, data_only=True)
    for sn in wb.sheetnames:
        ws = wb[sn]
        max_c = min(ws.max_column or 1, 30)

        # Two layouts exist in this one workbook and both must be read:
        #   A  February : an explicit "Item | Total Quantity | Amount | Item Price"
        #                 block sitting to the right of a size-breakdown table
        #   B  the rest : "DATE | Total Quantity | Item Price | FOR REMITTANCE",
        #                 where the "DATE" column actually holds item names and
        #                 supplier bands (NAPOLIZ ENTERPRISES, JYL ATHLETICA)
        #                 separate the sections
        header_row, cmap = None, {}
        for r in range(1, min(12, (ws.max_row or 1)) + 1):
            hdr = {}
            for c in range(1, max_c + 1):
                v = ws.cell(r, c).value
                if not isinstance(v, str):
                    continue
                t = v.strip().upper()
                if t == "ITEM":
                    hdr["item"] = c
                elif "TOTAL QUANTITY" in t:
                    hdr["qty"] = c
                elif t == "AMOUNT":
                    hdr["amount"] = c
                elif "ITEM PRICE" in t:
                    hdr["price"] = c
                elif "REMITTANCE" in t:
                    hdr["amount"] = hdr.get("amount", c)
                elif t in ("DATE", "DATE "):
                    hdr.setdefault("item", c)     # layout B mislabels the item column
            if "qty" in hdr:
                # Layout B labels the item column inconsistently - "DATE" in
                # March/April/May, the month name ("JUNE") in june/july-aug.
                # Whatever it is called, it is the leftmost column, so fall
                # back to that rather than adding a name to chase.
                hdr.setdefault("item", 1)
                header_row, cmap = r, hdr
                break
        if header_row is None:
            continue

        supplier = None
        for r in range(header_row + 1, (ws.max_row or 1) + 1):
            item = ws.cell(r, cmap["item"]).value
            qty = ws.cell(r, cmap["qty"]).value
            if not isinstance(item, str) or not item.strip():
                continue
            text = item.strip()
            if TOTALS.match(text):
                continue                      # a summary line, not an item
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                # text in the item column with no quantity = a supplier band
                if not any(isinstance(ws.cell(r, c).value, (int, float))
                           for c in range(cmap["item"] + 1, max_c + 1)):
                    supplier = canon_supplier(text, {})
                continue
            rows.append({
                "batch_label": sn.strip(),
                "supplier_name": supplier,
                "item_name": canon_item(text, vocab),
                "raw_item_name": text,
                "total_quantity": float(qty),
                "amount_php": (ws.cell(r, cmap["amount"]).value
                               if "amount" in cmap else None),
                "item_price_php": (ws.cell(r, cmap["price"]).value
                                   if "price" in cmap else None),
                "source_file": os.path.basename(path),
                "grain": "batch_aggregate",   # explicitly NOT daily
            })
    wb.close()
    return rows


# -------------------------------------------------------------- vocabulary
def write_vocabulary(labels, path):
    """Every legend label found, with its inferred status, for human audit.

    Written only when absent, so a curated file is never overwritten by a
    later run - the point of the file is that a person can correct it.
    """
    if os.path.exists(path):
        return False
    rows = [{"label": lab, "status": classify_label(lab),
             "n_sheets": cnt, "reviewed": "no", "note": ""}
            for lab, cnt in sorted(labels.items(), key=lambda x: -x[1])]
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")
    return True


# ----------------------------------------------- calendar as a supplement
def supplement_from_calendar(day_df, db_path):
    """Fill dates the workbooks never covered, using Dim_Date.

    PRECEDENCE IS FIXED AND ONE-WAY: colour wins. A date that appears in a
    workbook keeps the status its fill colour resolved to, whatever the
    published calendar says about it. Only dates with NO colour evidence -
    days that fall inside the span but were never given a column - are
    filled from `Dim_Date`, whose flags come from `calendar_ranges.csv`.

    Why the asymmetry: the two sources disagree on 62 of 601 dates, and 48
    of those are Sundays the workbooks mark "NO OPERATION" while the
    academic calendar has no opinion about them. The calendar describes the
    university's term structure; only the workbook describes the shop.

    `status_source` carries the provenance so a downstream reader can drop
    the inferred rows if it wants colour-only evidence.
    """
    import numpy as np

    day_df = day_df.copy()
    day_df["status_source"] = "colour"
    if not len(day_df):
        return day_df

    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        dim = pd.read_sql_query(
            "SELECT calendar_date, is_store_closed, is_sem_break, is_event_day "
            "FROM Dim_Date", con, parse_dates=["calendar_date"])
        con.close()
    except Exception:
        return day_df                      # no DB yet: colour-only is valid

    seen = pd.to_datetime(day_df["calendar_date"])
    lo, hi = seen.min(), seen.max()
    dim = dim[(dim["calendar_date"] >= lo) & (dim["calendar_date"] <= hi)]
    missing = dim[~dim["calendar_date"].isin(set(seen))]
    if not len(missing):
        return day_df

    rows = []
    for _, r in missing.iterrows():
        closed = bool(r["is_store_closed"]) or bool(r["is_sem_break"])
        rows.append({
            "calendar_date": r["calendar_date"].date(),
            "day_status": DayStatus.CLOSED if closed else DayStatus.UNKNOWN,
            "legend_label": None,
            "fill_colour": None,
            "traded": 0,
            "units_that_day": 0.0,
            # An UNKNOWN day has no colour and no sales column: nobody
            # recorded anything, so a zero there is not evidence of demand.
            "demand_observable": 0,
            "is_store_closed": int(closed),
            "source_file": "calendar_ranges.csv (via Dim_Date)",
            "source_sheet": None,
            "status_source": "calendar",
        })
    out = pd.concat([day_df, pd.DataFrame(rows)], ignore_index=True)
    return out.sort_values("calendar_date").reset_index(drop=True)


# --------------------------------------------------------------- database
DDL = """
CREATE TABLE IF NOT EXISTS Dim_Day_Status (
    day_status_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    calendar_date     TEXT    NOT NULL,
    day_status        TEXT    NOT NULL,
    legend_label      TEXT,
    fill_colour       TEXT,
    traded            INTEGER NOT NULL DEFAULT 0,
    units_that_day    REAL,
    demand_observable INTEGER NOT NULL DEFAULT 1,
    is_store_closed   INTEGER NOT NULL DEFAULT 0,
    status_source     TEXT    NOT NULL DEFAULT 'colour',
    source_file       TEXT,
    source_sheet      TEXT,
    UNIQUE(calendar_date)
);
CREATE INDEX IF NOT EXISTS ix_day_status_date ON Dim_Day_Status(calendar_date);

CREATE TABLE IF NOT EXISTS Fact_Batch_Sales (
    batch_sale_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_label     TEXT    NOT NULL,
    supplier_name   TEXT,
    item_name       TEXT    NOT NULL,
    raw_item_name   TEXT,
    total_quantity  REAL    NOT NULL,
    amount_php      REAL,
    item_price_php  REAL,
    source_file     TEXT,
    grain           TEXT    NOT NULL DEFAULT 'batch_aggregate'
);
"""


def write_db(db_path, day_df, batch_df):
    con = sqlite3.connect(db_path)
    try:
        # DROP then CREATE, not CREATE IF NOT EXISTS + DELETE. These two
        # tables are owned entirely by this script and are rebuilt from the
        # workbooks on every run, so the DDL here must be the authority on
        # their shape - otherwise adding a column (as `status_source` was)
        # fails against a table left over from an earlier version.
        # No table this script does not own is touched.
        con.execute("DROP TABLE IF EXISTS Dim_Day_Status")
        con.execute("DROP TABLE IF EXISTS Fact_Batch_Sales")
        con.executescript(DDL)
        cols = ["calendar_date", "day_status", "legend_label", "fill_colour",
                "traded", "units_that_day", "demand_observable",
                "is_store_closed", "status_source", "source_file", "source_sheet"]
        d = day_df.copy()
        d["calendar_date"] = d["calendar_date"].astype(str)
        con.executemany(
            "INSERT INTO Dim_Day_Status (%s) VALUES (%s)"
            % (",".join(cols), ",".join("?" * len(cols))),
            d[cols].where(pd.notna(d[cols]), None).itertuples(index=False, name=None))
        bcols = ["batch_label", "supplier_name", "item_name", "raw_item_name",
                 "total_quantity", "amount_php", "item_price_php",
                 "source_file", "grain"]
        if len(batch_df):
            b = batch_df.reindex(columns=bcols)
            con.executemany(
                "INSERT INTO Fact_Batch_Sales (%s) VALUES (%s)"
                % (",".join(bcols), ",".join("?" * len(bcols))),
                b.where(pd.notna(b), None).itertuples(index=False, name=None))
        con.commit()
    finally:
        con.close()


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "ustore.db"))
    ap.add_argument("--no-db", action="store_true",
                    help="write CSVs only, leave the database untouched")
    args = ap.parse_args()

    vocab, supp = load_mappings()
    override = load_vocabulary_override()
    print("=" * 88)
    print("COLOUR-AWARE RE-EXTRACTION OF THE USTore RAW WORKBOOKS")
    print("=" * 88)
    print("vocab entries %d | supplier entries %d | curated status overrides %d"
          % (len(vocab), len(supp), len(override)))

    all_sales, all_days, reports, batch, labels = [], [], [], [], {}
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.xlsx"))):
        base = os.path.basename(path)
        if BATCH_FILE_HINT in base.upper():
            rows = parse_batch_2023(path, vocab)
            batch.extend(rows)
            print("  [batch  ] %-46s %4d aggregate rows (NOT resampled to daily)"
                  % (base[:46], len(rows)))
            continue
        if DSR_FILE_HINT in base.upper():
            print("  [skip   ] %-46s daily sales report - superseded by TBS"
                  % base[:46])
            continue

        wb = openpyxl.load_workbook(path, data_only=True)
        n_s = n_d = 0
        for sn in wb.sheetnames:
            if any(s in sn.upper() for s in SKIP_SHEET):
                continue
            ws = wb[sn]
            for lab in legend_from_sheet(ws).values():
                labels[lab] = labels.get(lab, 0) + 1
            sales, days, rep = parse_tbs_sheet(ws, sn, base, vocab, supp, override)
            if rep is None:
                continue
            all_sales.extend(sales)
            all_days.extend(days)
            reports.append(rep)
            n_s += len(sales)
            n_d += len(days)
        wb.close()
        print("  [tbs    ] %-46s %5d sales rows, %3d dated days"
              % (base[:46], n_s, n_d))

    sales_df = pd.DataFrame(all_sales)
    day_df = pd.DataFrame(all_days)
    batch_df = pd.DataFrame(batch)
    rep_df = pd.DataFrame(reports)

    # One row per calendar date. A date appearing in two sheets keeps the
    # more informative record: a day someone marked with a reason beats a
    # blank one, and a day that traded beats one that did not.
    if len(day_df):
        day_df["_rank"] = (day_df["traded"] * 2
                           + (day_df["day_status"] != DayStatus.NORMAL).astype(int))
        day_df = (day_df.sort_values(["calendar_date", "_rank"])
                        .drop_duplicates("calendar_date", keep="last")
                        .drop(columns="_rank")
                        .sort_values("calendar_date").reset_index(drop=True))

    # ---- calendar_ranges as SUPPLEMENT, never as override ---------------
    # The colour legend is the store's own record of what it did and wins
    # every contest. calendar_ranges.csv is the published academic calendar:
    # provisional, and blind to the store's own rhythm - it does not know the
    # store shuts on Sundays, which is 48 of the 56 dates where the two
    # disagree. So it is used only where colour evidence does not exist,
    # and `status_source` records which answer each row came from.
    day_df = supplement_from_calendar(day_df, args.db)

    os.makedirs(DATA_DIR, exist_ok=True)
    sales_df.to_csv(os.path.join(DATA_DIR, "rebuild_sales_long.csv"),
                    index=False, lineterminator="\n")
    day_df.to_csv(os.path.join(DATA_DIR, "rebuild_day_status.csv"),
                  index=False, lineterminator="\n")
    batch_df.to_csv(os.path.join(DATA_DIR, "rebuild_batch_sales_2023.csv"),
                    index=False, lineterminator="\n")
    rep_df.to_csv(os.path.join(DATA_DIR, "rebuild_extract_report.csv"),
                  index=False, lineterminator="\n")
    fresh = write_vocabulary(labels, os.path.join(DATA_DIR, "day_status_vocabulary.csv"))

    print("\n" + "=" * 88)
    print("DAY STATUS - what the colours say")
    print("=" * 88)
    counts = day_df["day_status"].value_counts()
    for s in STATUS_ORDER:
        if s in counts:
            obs = day_df.loc[day_df.day_status == s, "demand_observable"].iloc[0]
            print("  %-16s %4d days   demand observable: %s"
                  % (s, counts[s], "yes" if obs else "NO"))
    n_closed = int((day_df["is_store_closed"] == 1).sum())
    n_unobs = int((day_df["demand_observable"] == 0).sum())
    print("\n  %d dated days total | %d store-closed | %d where a zero is NOT demand"
          % (len(day_df), n_closed, n_unobs))
    print("  -> those %d days are the ones the existing pipeline records as 'sold zero'"
          % n_unobs)

    print("\n" + "=" * 88)
    print("CROSS-CHECK against each sheet's own 'TOTAL SELLING DAYS'")
    print("=" * 88)
    chk = rep_df[rep_df["stated_selling_days"].notna()].copy()
    if len(chk):
        chk["delta"] = chk["parsed_selling_days"] - chk["stated_selling_days"]
        exact = int((chk["delta"] == 0).sum())
        within = int((chk["delta"].abs() <= 1).sum())
        print("  exact match %d/%d (%.0f%%) | within 1 day %d/%d (%.0f%%)"
              % (exact, len(chk), 100 * exact / len(chk),
                 within, len(chk), 100 * within / len(chk)))
        bad = chk[chk["delta"].abs() > 1]
        for _, r in bad.iterrows():
            print("    [review] %-28s stated %2d parsed %2d (%+d)"
                  % (r.source_sheet[:28], r.stated_selling_days,
                     r.parsed_selling_days, r.delta))
    print("\n  This is an INDEPENDENT check - the sheet author counted by hand.")
    print("  Residuals are reported, never reconciled into the data.")

    print("\n" + "=" * 88)
    print("OUTPUTS")
    print("=" * 88)
    print("  data/rebuild_sales_long.csv        %7d rows, %s .. %s"
          % (len(sales_df), sales_df["calendar_date"].min(),
             sales_df["calendar_date"].max()) if len(sales_df) else "")
    print("  data/rebuild_day_status.csv        %7d dated days" % len(day_df))
    print("  data/rebuild_batch_sales_2023.csv  %7d aggregate rows (grain=batch, NOT daily)"
          % len(batch_df))
    print("  data/rebuild_extract_report.csv    %7d sheets" % len(rep_df))
    print("  data/day_status_vocabulary.csv     %s (%d labels)"
          % ("written - review and edit to override" if fresh
             else "already exists, left untouched", len(labels)))

    if not args.no_db:
        write_db(args.db, day_df, batch_df)
        print("\n  ustore.db: Dim_Day_Status (%d) and Fact_Batch_Sales (%d) replaced"
              % (len(day_df), len(batch_df)))
        print("  No existing table was altered.")
    else:
        print("\n  --no-db: database untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
