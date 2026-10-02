"""
scripts/disaggregate_batch_sales_2023.py
------------------------------------------------------------------
Spread the 2023 per-batch sales totals across days, producing a daily
long-format CSV in the same shape as the TBS conversions
(Date, Item, Total Quantity, Supplier), so 2023 can be tried as extra
training history.

READ THIS FIRST: the daily values are SYNTHETIC
-----------------------------------------------
`2023 total sales by batch (3).xlsx` holds one total per item per batch and
no dates (docs/REBUILD_PIPELINE.md section 4). Only the batch totals are real.
The split within each batch is modelled, not observed. Every row carries
Is_Synthetic = 1 and its Source_Batch so it can always be filtered back out,
and nothing here enters the shipped pipeline unless a script reads this file
on purpose. docs/SYNTHETIC_MISSING_MONTHS.md found that resampled history gave
the shipped models no measurable gain; measure before relying on this file.

How a batch total becomes daily sales
-------------------------------------
  window   each batch is a calendar month; "july-aug" is 2023-07-01..08-31
  days     every day in the window except is_store_closed days in
           data/calendar_ranges.csv (no row at all, like a closed TBS day)
  weight   weekday ratio x day-type ratio, both measured on the store's real
           daily totals (data/USTore_sales_long_with_zeros.csv):
             weekday ratio   mean units on that weekday / overall mean
             day-type ratio  forecasting.calendar_adjust.type_ratios over the
                             whole real history (enrol > break > exam > normal,
                             same precedence and shrinkage as the forecaster)
  units    each item's batch total is drawn multinomially over its days with
           those weights (seeded), so daily counts are whole units, look
           intermittent like the real data, and sum EXACTLY to the batch total
  zeros    open days with no draw are written as 0, like the dense TBS months

Rows dropped or filled, stated because they are judgement calls
---------------------------------------------------------------
  * "ADDITIONAL 50 NOT REMITTED MARCH APRIL" is a PHP 50 remittance top-up on
    earlier jersey sales, not units sold - dropped.
  * The February sheet names no supplier. Its five items are exactly
    NAPOLIZ ENTERPRISES' block in every later batch, so it is filled with that.
  * Item names are the raw sheet labels, as in the other long CSVs, so
    scripts/step1_apply_mapping.py-style mapping applies unchanged.

Usage:
    python scripts/disaggregate_batch_sales_2023.py [--seed 0] [-o out.csv]
------------------------------------------------------------------
"""
import argparse
import calendar
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from forecasting.calendar_adjust import type_ratios          # noqa: E402
from rebuild_extract_tbs import load_mappings, parse_batch_2023  # noqa: E402

BATCH_FILE = os.path.join(ROOT, "rawdata", "2023 total sales by batch (3).xlsx")
REAL_SALES = os.path.join(ROOT, "data", "USTore_sales_long_with_zeros.csv")
CALENDAR = os.path.join(ROOT, "data", "calendar_ranges.csv")
OUT_PATH = os.path.join(ROOT, "data", "USTore_sales_2023_daily_synthetic.csv")

YEAR = 2023
# batch sheet label (case/space-insensitive) -> (first month, last month)
BATCH_MONTHS = {
    "TOTAL SALES FEBRUARY BATCH": (2, 2),
    "TOTAL SALES MARCH BATCH": (3, 3),
    "TOTAL SALES APRIL BATCH": (4, 4),
    "TOTAL SALES MAY BATCH": (5, 5),
    "JUNE BATCH": (6, 6),
    "JULY-AUG": (7, 8),
}
NOT_UNITS = {"ADDITIONAL 50 NOT REMITTED MARCH APRIL"}
FEB_SUPPLIER = "NAPOLIZ ENTERPRISES"


def batch_key(label):
    return " ".join(label.upper().split())


def batch_window(label):
    m0, m1 = BATCH_MONTHS[batch_key(label)]
    start = dt.date(YEAR, m0, 1)
    end = dt.date(YEAR, m1, calendar.monthrange(YEAR, m1)[1])
    return pd.date_range(start, end, freq="D")


def calendar_flags():
    """One row per date with the calendar_ranges flags as 0/1 columns."""
    cr = pd.read_csv(CALENDAR, parse_dates=["start_date", "end_date"])
    rows = [(d, r.flag) for r in cr.itertuples()
            for d in pd.date_range(r.start_date, r.end_date, freq="D")]
    f = pd.DataFrame(rows, columns=["date", "flag"]).assign(v=1)
    return f.pivot_table(index="date", columns="flag", values="v", aggfunc="max").fillna(0)


def day_types(dates, flags):
    c = flags.reindex(dates).fillna(0)
    get = lambda k: c[k] if k in c else 0
    return np.select([get("is_enrollment_period") > 0, get("is_sem_break") > 0,
                      get("is_exam_week") > 0], ["enrol", "break", "exam"], "normal")


def day_weights(flags):
    """Weekday and day-type ratios from the real daily store totals."""
    real = pd.read_csv(REAL_SALES, parse_dates=["Date"])
    daily = real.groupby("Date")["Total Quantity"].sum().sort_index()
    overall = daily.mean()
    wk = (daily.groupby(daily.index.dayofweek).mean() / overall).to_dict()
    dtype = type_ratios(daily.values, day_types(daily.index, flags), window=len(daily))
    return wk, dtype


def load_batches():
    vocab, _ = load_mappings()
    b = pd.DataFrame(parse_batch_2023(BATCH_FILE, vocab))
    b = b[~b["raw_item_name"].str.strip().str.upper().isin(NOT_UNITS)].copy()
    b["supplier_name"] = b["supplier_name"].fillna(FEB_SUPPLIER)
    b["total_quantity"] = b["total_quantity"].round().astype(int)
    return b


def disaggregate(seed):
    flags = calendar_flags()
    wk, dtype = day_weights(flags)
    batches = load_batches()
    closed = flags.get("is_store_closed", pd.Series(dtype=float))
    rng = np.random.default_rng(seed)

    out = []
    for label, grp in batches.groupby("batch_label", sort=False):
        days = batch_window(label)
        days = days[closed.reindex(days).fillna(0).values == 0]
        types = day_types(days, flags)
        w = np.array([wk[d.dayofweek] * dtype.get(t, 1.0) for d, t in zip(days, types)])
        p = w / w.sum()
        for r in grp.itertuples():
            qty = rng.multinomial(r.total_quantity, p) if r.total_quantity > 0 \
                else np.zeros(len(days), dtype=int)
            out.append(pd.DataFrame({
                "Date": days.strftime("%Y-%m-%d"),
                "Item": r.raw_item_name.strip(),
                "Total Quantity": qty,
                "Supplier": r.supplier_name,
                "Source_Batch": label.strip(),
                "Is_Synthetic": 1,
            }))
    df = pd.concat(out, ignore_index=True)
    # several raw labels can repeat inside a batch; sum like the TBS converter does
    keys = ["Date", "Item", "Supplier", "Source_Batch", "Is_Synthetic"]
    df = df.groupby(keys, as_index=False, sort=False)["Total Quantity"].sum()
    df = df[["Date", "Item", "Total Quantity", "Supplier", "Source_Batch", "Is_Synthetic"]]
    df = df.sort_values(["Date", "Supplier", "Item"], key=lambda s: s.str.upper()
                        if s.dtype == object else s).reset_index(drop=True)
    return df, batches, wk, dtype


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-o", "--output", default=OUT_PATH)
    args = ap.parse_args()

    df, batches, wk, dtype = disaggregate(args.seed)

    # the one guarantee this file makes: every batch total is preserved exactly
    got = df.groupby(["Source_Batch", "Item", "Supplier"])["Total Quantity"].sum()
    want = (batches.assign(Item=batches["raw_item_name"].str.strip(),
                           Source_Batch=batches["batch_label"].str.strip())
            .groupby(["Source_Batch", "Item", "supplier_name"])["total_quantity"].sum())
    want.index.names = got.index.names
    assert got.sort_index().equals(want.sort_index().astype(got.dtype)), "batch totals not preserved"

    df.to_csv(args.output, index=False)
    names = "Mon Tue Wed Thu Fri Sat Sun".split()
    print("weekday ratio :", ", ".join(f"{names[k]} {v:.2f}" for k, v in sorted(wk.items())))
    print("day-type ratio:", ", ".join(f"{k} {v:.2f}" for k, v in dtype.items()))
    print(df.groupby("Source_Batch", sort=False).agg(
        days=("Date", "nunique"), items=("Item", "nunique"),
        units=("Total Quantity", "sum")).to_string())
    print(f"\n{len(df)} rows, {int(df['Total Quantity'].sum())} units, "
          f"{df['Date'].min()} .. {df['Date'].max()} -> {os.path.relpath(args.output, ROOT)}")


if __name__ == "__main__":
    main()
