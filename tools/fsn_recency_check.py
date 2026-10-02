"""
tools/fsn_recency_check.py
------------------------------------------------------------------
Re-measures step3's recency rule (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 4.1):
an item with no sale in the last N days is never Fast.

For each of the last 12 months, the Fast list is rebuilt from the sales
BEFORE that month (step3's own classify(), so the rule tested is the rule
shipped), and checked against the 30 days that follow:

    fast        items on the Fast list
    dead        Fast items that then sold nothing in those 30 days
    coverage    share of all units sold in those 30 days that were sold by
                an item on the Fast list

A good rule drops dead items without lowering coverage: a dead item adds
nothing to coverage, so removing it costs nothing, and an item the rule
drops that DID go on to sell shows up as lost coverage.

Read-only. Usage:  python tools/fsn_recency_check.py [--db ustore.db] [--months 12]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import step3_fsn_classification as step3  # noqa: E402

RULES = {"no rule": 10 ** 6, "180 days": 180, "90 days": 90}
HORIZON_DAYS = 30


def measure(products, fact, months):
    sold = fact[fact["quantity_sold"] > 0]
    last_month = sold["calendar_date"].max().to_period("M")
    origins = [(last_month - i).to_timestamp() for i in range(months - 1, -1, -1)]

    rows = []
    for origin in origins:
        history = fact[fact["calendar_date"] < origin]
        ahead = fact[(fact["calendar_date"] >= origin)
                     & (fact["calendar_date"] < origin + pd.Timedelta(days=HORIZON_DAYS))]
        units_ahead = ahead.groupby("product_id")["quantity_sold"].sum()
        total_ahead = units_ahead.sum()
        if history.empty or total_ahead <= 0:
            continue
        for name, days in RULES.items():
            df = step3.classify(products, history, stale_days=days).df
            fast = set(df.loc[df["fsn_class"] == "F", "product_id"])
            ahead_fast = units_ahead.reindex(list(fast)).fillna(0)
            rows.append({
                "month": origin.strftime("%Y-%m"), "rule": name, "fast": len(fast),
                "dead": int((ahead_fast <= 0).sum()),
                "coverage": ahead_fast.sum() / total_ahead,
            })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="ustore.db")
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    products = pd.read_sql("SELECT product_id, item_name, entry_date FROM Dim_Product", con)
    fact = step3.load_fact(con)
    con.close()

    res = measure(products, fact, args.months)
    wide = res.pivot(index="month", columns="rule", values=["fast", "dead", "coverage"])
    pd.set_option("display.width", 160)
    print(f"=== Fast list rebuilt before each month, scored on the next {HORIZON_DAYS} days ===")
    print(wide.round(3).to_string())

    summary = res.groupby("rule", sort=False).agg(
        fast=("fast", "mean"), dead=("dead", "mean"), coverage=("coverage", "mean"))
    base = summary.loc["no rule"]
    summary["dead dropped"] = base["dead"] - summary["dead"]
    summary["coverage change (pts)"] = 100 * (summary["coverage"] - base["coverage"])
    print(f"\n=== Average over {res['month'].nunique()} months ===")
    print(summary.assign(coverage=lambda d: (100 * d["coverage"]).round(1))
          .round(2).to_string())


if __name__ == "__main__":
    main()
