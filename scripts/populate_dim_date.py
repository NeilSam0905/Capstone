"""
populate_dim_date.py
------------------------------------------------------------------
Fills the Dim_Date table in ustore.db with one row per calendar day
from 2023-01-01 to 2026-12-31, then applies the academic-calendar
flags from calendar_ranges.csv, derives semester_id/semester_week
from term windows, and populates is_tally_date from the sales data.

HOW TO RUN:
    python populate_dim_date.py

Safe to re-run: it clears Dim_Date first, then rebuilds it from
scratch, so running it twice will not create duplicate rows.
------------------------------------------------------------------
Fixes applied here (Code Work Plan v2, Block 1):

1.1/1.3 - calendar_ranges.csv dates were DD/MM/YYYY; this script called
  date.fromisoformat() on them and crashed. calendar_ranges.csv has been
  converted to ISO 8601 in place (start_date/end_date now YYYY-MM-DD);
  0 of 135 ranges were inverted (end < start) after parsing with
  dayfirst=True, confirming the day/month reading was correct.
  Block 1.1 was later completed across the whole repo: the sales and
  inventory CSVs are ISO too, so load_tally_dates() below parses
  "%Y-%m-%d" with errors="raise" rather than DD/MM/YYYY. verify_data.py
  guards this for every CSV.

1.4 - TERM_STARTS used to hardcode 3 terms. calendar_ranges.csv actually
  spans 12 (AY2223-T2 .. AY2627-T1). Every term's start is now derived
  as min(start_date) over its own rows, sorted chronologically, so a
  term's window runs up to (but not including) the next term's start -
  fixing both the 42%-of-rows-null semester_week problem AND the
  AY2526-ST -> 2026-12-31 overrun (it now correctly ends the day before
  AY2627-T1 starts, since that term is now included).

  Week 1 origin: a term's semester_week starts counting from its
  EARLIEST calendar_ranges row for that semester_id, which in practice
  is the enrollment-period range, not the first class day. Chosen
  deliberately - enrollment is when the sales surge happens, so it's
  the more useful anchor for a demand regressor - but it's a choice,
  not a fact, and must match whatever Chapter 4 states. (Divergence
  Register item.)

  semester_id is no longer set inside the flag-application loop (where
  overlapping ranges meant "last CSV row wins" - not reproducible).
  It's now assigned once per day, together with semester_week, purely
  from the derived term windows.

  is_tally_date: populated from the distinct dates in
  USTore_sales_long_with_zeros.csv (608 dates), NOT the 411 distinct
  dates in the original positive-sales-only CSV. Those 411 only capture
  days with a recorded SALE; the zero-fill work established that most
  months (Oct 2024 onward) were tallied on essentially every calendar
  day regardless of whether anything sold, so "tally date" and "sale
  date" are different questions once zero-fill exists. Using the
  zero-inclusive file's date set is the more accurate definition of
  "a physical tally happened this day." (Divergence Register item -
  this also further supports the zero-fill decision itself.)
------------------------------------------------------------------
"""

import csv
import sqlite3
from datetime import date, timedelta

import pandas as pd

DB_NAME = "ustore.db"
CSV_NAME = "data/calendar_ranges.csv"
SALES_WITH_ZEROS_CSV = "data/USTore_sales_long_with_zeros.csv"

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


def derive_term_windows(csv_name, overall_end):
    """One window per distinct semester_id in calendar_ranges.csv, each
    starting at min(start_date) of its own rows and running up to (but
    not including) the next term's start - chronologically ordered, not
    hardcoded, so a new term added to the CSV is picked up automatically."""
    df = pd.read_csv(csv_name)
    df["start_date"] = pd.to_datetime(df["start_date"], format="%Y-%m-%d")
    starts = df.groupby("semester_id")["start_date"].min().sort_values()

    term_ids = list(starts.index)
    term_starts = [pd.Timestamp(d).date() for d in starts.values]

    windows = []
    for i, (term_id, start) in enumerate(zip(term_ids, term_starts)):
        if i + 1 < len(term_ids):
            end = term_starts[i + 1] - timedelta(days=1)
        else:
            end = overall_end
        windows.append((term_id, start, end))
    return windows


def daterange(start, end):
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


def load_tally_dates(sales_csv):
    df = pd.read_csv(sales_csv)
    parsed = pd.to_datetime(df["Date"], format="%Y-%m-%d", errors="raise")
    return set(parsed.dt.date)


def main():
    term_windows = derive_term_windows(CSV_NAME, END_DATE)
    tally_dates = load_tally_dates(SALES_WITH_ZEROS_CSV)

    # ----- Step 1: one row per day, flags 0, semester fields empty -----
    rows = {}
    date_id = 1
    for d in daterange(START_DATE, END_DATE):
        rows[d] = {
            "date_id": date_id,
            "calendar_date": d.isoformat(),
            "semester_id": None,
            "semester_week": None,
            "is_enrollment_period": 0,
            "is_exam_week": 0,
            "is_event_day": 0,
            "is_sem_break": 0,
            "is_tally_date": 0,
            "is_store_closed": 0,
        }
        date_id += 1

    # ----- Step 2: apply calendar-flag booleans from calendar_ranges.csv -----
    # (semester_id is NOT set here - see Step 4)
    with open(CSV_NAME, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            start = date.fromisoformat(row["start_date"].strip())
            end = date.fromisoformat(row["end_date"].strip())
            flag = row["flag"].strip()
            if flag not in FLAG_COLUMNS:
                raise ValueError(f"Unknown flag '{flag}' in calendar_ranges.csv")
            for d in daterange(start, end):
                if d not in rows:
                    continue  # outside the 2023-01-01..2026-12-31 window
                rows[d][flag] = 1

    # ----- Step 3: is_tally_date from the zero-inclusive sales data -----
    for d in tally_dates:
        if d in rows:
            rows[d]["is_tally_date"] = 1

    # ----- Step 4: semester_id + semester_week from the derived term windows -----
    for term_id, start, end in term_windows:
        for d in daterange(start, end):
            if d in rows:
                rows[d]["semester_id"] = term_id
                rows[d]["semester_week"] = (d - start).days // 7 + 1

    # ----- Step 5: insert into SQLite -----
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Fact_Sales now has rows referencing Dim_Date.date_id (it didn't when
    # this script was first written). date_id assignment here is fully
    # deterministic - sequential from the same START_DATE - so the
    # delete+reinsert reproduces byte-identical date_id<->calendar_date
    # pairs; foreign keys are disabled only for this one operation, not
    # left off for the rest of the session.
    cur.execute("PRAGMA foreign_keys = OFF;")
    cur.execute("DELETE FROM Dim_Date;")
    cur.executemany(
        """
        INSERT INTO Dim_Date (
            date_id, calendar_date, semester_id, semester_week,
            is_enrollment_period, is_exam_week, is_event_day,
            is_sem_break, is_tally_date, is_store_closed
        ) VALUES (
            :date_id, :calendar_date, :semester_id, :semester_week,
            :is_enrollment_period, :is_exam_week, :is_event_day,
            :is_sem_break, :is_tally_date, :is_store_closed
        )
        """,
        list(rows.values()),
    )
    conn.commit()
    cur.execute("PRAGMA foreign_keys = ON;")

    # ----- Sanity-check output -----
    total = cur.execute("SELECT COUNT(*) FROM Dim_Date;").fetchone()[0]
    print(f"Total rows inserted: {total}")
    print()
    print("Days with each flag set to 1:")
    for flag in FLAG_COLUMNS:
        count = cur.execute(
            f"SELECT COUNT(*) FROM Dim_Date WHERE {flag} = 1;"
        ).fetchone()[0]
        print(f"   {flag:<22} {count}")

    weeks_set = cur.execute(
        "SELECT COUNT(*) FROM Dim_Date WHERE semester_week IS NOT NULL;"
    ).fetchone()[0]
    print()
    print(f"Rows with semester_week computed: {weeks_set}")
    print(f"Term windows derived: {len(term_windows)}")
    for term_id, start, end in term_windows:
        print(f"   {term_id:12} {start} .. {end}")

    max_week = cur.execute("SELECT MAX(semester_week) FROM Dim_Date;").fetchone()[0]
    orphan = cur.execute(
        "SELECT COUNT(*) FROM Dim_Date WHERE semester_id IS NOT NULL AND semester_week IS NULL;"
    ).fetchone()[0]
    print()
    print(f"MAX(semester_week): {max_week}")
    print(f"semester_id set but semester_week NULL (should be 0): {orphan}")

    conn.close()


if __name__ == "__main__":
    main()
