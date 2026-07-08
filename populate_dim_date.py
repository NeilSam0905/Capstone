"""
populate_dim_date.py
------------------------------------------------------------------
Fills the Dim_Date table in ustore.db with one row per calendar day
from 2023-01-01 to 2026-12-31, then applies the academic-calendar
flags/semester_id from calendar_ranges.csv and computes semester_week.

HOW TO RUN:
    python populate_dim_date.py

Safe to re-run: it clears Dim_Date first, then rebuilds it from
scratch, so running it twice will not create duplicate rows.
------------------------------------------------------------------
"""

import csv
import sqlite3
from datetime import date, timedelta

DB_NAME = "ustore.db"
CSV_NAME = "calendar_ranges.csv"

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

# Term windows used ONLY to compute semester_week (not semester_id;
# semester_id comes straight from calendar_ranges.csv in Step 2 below).
# Each term runs from its start date up to (but not including) the
# next term's start date. AY2526-ST has no known successor term in
# our data, so its window is extended through END_DATE. Revisit this
# if you later get a real end date / next term start.
TERM_STARTS = [
    ("AY2526-T1", date(2025, 8, 7)),
    ("AY2526-T2", date(2026, 1, 16)),
    ("AY2526-ST", date(2026, 6, 10)),
]


def build_term_windows(term_starts, overall_end):
    windows = []
    for i, (term_id, start) in enumerate(term_starts):
        if i + 1 < len(term_starts):
            end = term_starts[i + 1][1] - timedelta(days=1)
        else:
            end = overall_end
        windows.append((term_id, start, end))
    return windows


def daterange(start, end):
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


def main():
    term_windows = build_term_windows(TERM_STARTS, END_DATE)

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

    # ----- Step 2: apply flags + semester_id from calendar_ranges.csv -----
    with open(CSV_NAME, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            start = date.fromisoformat(row["start_date"].strip())
            end = date.fromisoformat(row["end_date"].strip())
            flag = row["flag"].strip()
            semester_id = row["semester_id"].strip()
            if flag not in FLAG_COLUMNS:
                raise ValueError(f"Unknown flag '{flag}' in calendar_ranges.csv")
            for d in daterange(start, end):
                if d not in rows:
                    continue  # outside the 2023-01-01..2026-12-31 window
                rows[d][flag] = 1
                rows[d]["semester_id"] = semester_id

    # ----- Step 3: is_tally_date stays 0 for every row (set later from sales data) -----
    # (already 0 by default from Step 1 — nothing to do here)

    # ----- Step 4: compute semester_week from the term windows -----
    for term_id, start, end in term_windows:
        for d in daterange(start, end):
            if d in rows:
                rows[d]["semester_week"] = (d - start).days // 7 + 1

    # ----- Step 5: insert into SQLite -----
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
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

    conn.close()


if __name__ == "__main__":
    main()
