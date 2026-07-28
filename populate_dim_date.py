"""
populate_dim_date.py  — Open work item #3
------------------------------------------------------------------
Builds Dim_Date: one row per calendar day 2023-01-01 .. 2026-12-31, then
  1. expands calendar_ranges.csv ranges to per-day boolean flags,
  2. assigns semester_id + semester_week to every day from data-derived
     term windows (not a hardcoded list),
  3. sets is_tally_date = 1 for every date that appears in the sales fact.

Writes the table into ustore.db (creating the table if the schema isn't there
yet) and also exports dim_date.csv for Power BI / inspection.

HOW TO RUN:
    python create_schema.py        # once, to build ustore.db (optional — see below)
    python populate_dim_date.py

Safe to re-run: it clears Dim_Date first, then rebuilds from scratch.

Term windows / semester_week
----------------------------
Each semester_id's term start is the EARLIEST date carrying that tag in
calendar_ranges.csv. Terms are ordered chronologically and each term runs from
its start up to the day before the next term begins (the last term runs to
END_DATE), giving contiguous, non-overlapping windows that tile the whole span.
semester_week = whole weeks since the term start + 1. Days before the first
term (2023-01-01..08) get no semester_id/week — outside the academic calendar
and outside the sales window. If the manuscript needs a specific "classes
begin" anchor instead of the earliest calendared date, override TERM_START_OVERRIDE.
"""

import csv
import sqlite3
from datetime import date, timedelta

import pandas as pd

DB_NAME = "ustore.db"
CALENDAR_CSV = "calendar_ranges.csv"
SALES_CSV = "USTore_sales_long_allocated_normalized.csv"   # source of tally dates
CSV_EXPORT = "dim_date.csv"

START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 12, 31)

FLAG_COLUMNS = [
    "is_enrollment_period",
    "is_exam_week",
    "is_event_day",
    "is_sem_break",
    "is_tally_date",
    "is_store_closed",
]

# Per-term start overrides, if a term should anchor semester_week on a date
# other than its earliest calendared date. Empty = derive everything from data.
TERM_START_OVERRIDE = {}  # e.g. {"AY2526-T1": date(2025, 8, 7)}

CREATE_DIM_DATE = """
CREATE TABLE IF NOT EXISTS Dim_Date (
    date_id              INTEGER PRIMARY KEY,
    calendar_date        TEXT    NOT NULL,
    semester_id          TEXT,
    semester_week        INTEGER,
    is_enrollment_period INTEGER DEFAULT 0,
    is_exam_week         INTEGER DEFAULT 0,
    is_event_day         INTEGER DEFAULT 0,
    is_sem_break         INTEGER DEFAULT 0,
    is_tally_date        INTEGER DEFAULT 0,
    is_store_closed      INTEGER DEFAULT 0
);
"""


def daterange(start, end):
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


def derive_term_windows(cal):
    """(term_id, start, end) contiguous windows from the earliest tagged date
    of each semester_id; last term extends to END_DATE."""
    starts = (
        cal.dropna(subset=["semester_id"])
        .groupby("semester_id")["start_date"]
        .min()
        .apply(lambda s: date.fromisoformat(s))
        .to_dict()
    )
    for tid, ov in TERM_START_OVERRIDE.items():
        starts[tid] = ov
    ordered = sorted(starts.items(), key=lambda kv: kv[1])
    windows = []
    for i, (tid, start) in enumerate(ordered):
        end = ordered[i + 1][1] - timedelta(days=1) if i + 1 < len(ordered) else END_DATE
        windows.append((tid, start, end))
    return windows


def main():
    cal = pd.read_csv(CALENDAR_CSV, dtype=str)
    term_windows = derive_term_windows(cal)

    # ----- Step 1: one row per day, flags 0, semester fields empty -----
    rows = {}
    for i, d in enumerate(daterange(START_DATE, END_DATE), start=1):
        rows[d] = {
            "date_id": i,
            "calendar_date": d.isoformat(),
            "semester_id": None,
            "semester_week": None,
            **dict.fromkeys(FLAG_COLUMNS, 0),
        }

    # ----- Step 2: semester_id + semester_week for every day in a term window -----
    for term_id, start, end in term_windows:
        for d in daterange(start, end):
            if d in rows:
                rows[d]["semester_id"] = term_id
                rows[d]["semester_week"] = (d - start).days // 7 + 1

    # ----- Step 3: expand calendar_ranges flags to per-day booleans -----
    for _, r in cal.iterrows():
        flag = str(r["flag"]).strip()
        if flag not in FLAG_COLUMNS:
            raise ValueError(f"Unknown flag '{flag}' in {CALENDAR_CSV}")
        s = date.fromisoformat(str(r["start_date"]).strip())
        e = date.fromisoformat(str(r["end_date"]).strip())
        for d in daterange(s, e):
            if d in rows:
                rows[d][flag] = 1

    # ----- Step 4: is_tally_date from the sales fact -----
    sales = pd.read_csv(SALES_CSV, usecols=["Date"], dtype=str)
    tally_dates = {date.fromisoformat(x) for x in sales["Date"].unique()}
    for d in tally_dates:
        if d in rows:
            rows[d]["is_tally_date"] = 1
    out_of_scope = sum(1 for d in tally_dates if d not in rows)

    # ----- Step 5: write to SQLite -----
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute(CREATE_DIM_DATE)
    cur.execute("DELETE FROM Dim_Date;")
    cur.executemany(
        """INSERT INTO Dim_Date (
               date_id, calendar_date, semester_id, semester_week,
               is_enrollment_period, is_exam_week, is_event_day,
               is_sem_break, is_tally_date, is_store_closed
           ) VALUES (
               :date_id, :calendar_date, :semester_id, :semester_week,
               :is_enrollment_period, :is_exam_week, :is_event_day,
               :is_sem_break, :is_tally_date, :is_store_closed
           )""",
        list(rows.values()),
    )
    conn.commit()

    # ----- Step 6: CSV export -----
    fields = ["date_id", "calendar_date", "semester_id", "semester_week"] + FLAG_COLUMNS
    with open(CSV_EXPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows.values())

    # ----- Sanity report -----
    total = cur.execute("SELECT COUNT(*) FROM Dim_Date;").fetchone()[0]
    print("=== Dim_Date built ===")
    print(f"rows (days) inserted            : {total}")
    print(f"term windows derived            : {len(term_windows)}")
    print("flag day-counts:")
    for flag in FLAG_COLUMNS:
        n = cur.execute(f"SELECT COUNT(*) FROM Dim_Date WHERE {flag}=1;").fetchone()[0]
        print(f"   {flag:<22} {n}")
    with_sem = cur.execute("SELECT COUNT(*) FROM Dim_Date WHERE semester_id IS NOT NULL;").fetchone()[0]
    print(f"days with semester_id assigned  : {with_sem}  (no-semester: {total - with_sem})")
    print(f"distinct tally dates flagged    : {len(tally_dates) - out_of_scope}"
          f"{' (WARNING: %d tally dates outside scope!)' % out_of_scope if out_of_scope else ''}")
    conn.close()
    print(f"\nwrote table Dim_Date in {DB_NAME} and exported {CSV_EXPORT}")


if __name__ == "__main__":
    main()
