"""
step5a_set_lead_times.py
------------------------------------------------------------------
Sets Dim_Product.lead_time_days per product, by garment category,
using the estimates USTore gave verbally:

    simple / puff / DTF-printed t-shirts : 14 days
    embroidered shirts                   : 18 days
    jackets                              : 28 days
    non-apparel / anything uncategorized : 18 days  (default)

These are PROVISIONAL estimates pending confirmation at the USTore
site visit (deferred decision B9) - this script only assigns them so
step5_prescriptive.py has a real per-SKU lead time instead of a grid.

Classification is keyword-matching on item_name, since Dim_Product's
own `category` column is too coarse (APPAREL / NON-APPAREL / MAIN
STORAGE / NULL) AND only ~17% populated - it comes from the inventory
file, which most SKUs (including many Fast-selling ones, e.g. "UST
T-Shirt Sporty") don't appear in at all (Block 3's coverage gap). This
is itself a provisional heuristic, not an authoritative product
attribute - the printed breakdown below is meant to be read and
corrected, not trusted blindly.

Priority (checked in this order, first match wins):
  1. category == "NON-APPAREL" (a CONFIRMED tag, not a missing one)
     -> default (18d). This is the one case where category overrides
     the name - it's real signal when present, unlike a NULL category
     which just means "not in the inventory file".
  2. "jacket" / "windbreaker" in the name -> jackets (28d). A jacket
     that also happens to be embroidered is still a jacket first - the
     base garment's construction time dominates, not the decoration.
  3. "embro"                   -> embroidered shirts (18d). Catches
     both "Embro <X>" tops and "<X> Embroidered/Embroidery Shirt".
  4. "shirt" / "jersey" / "polo" / "tee" -> simple/DTF/puff shirts (14d)
  5. everything else            -> default (18d), including hoodies,
     sweatshirts, shorts, and the handful of item names that don't
     spell out a garment type at all (e.g. "Alessandrini",
     "Athletic V.1") - deliberately NOT guessed at further.

Everything that isn't a recognised shirt or jacket - confirmed
non-apparel, mugs/bags/stationery, and genuinely uncategorized items -
gets the same 18-day default per USTore's own framing ("non-apparel
and anything uncategorized").

Safe to re-run: recomputes and overwrites lead_time_days for every
product each time.
"""
import re
import sqlite3

import pandas as pd

DB_PATH = "ustore.db"

LT_SIMPLE_SHIRT = 14
LT_EMBROIDERED = 18
LT_JACKET = 28
LT_DEFAULT = 18

JACKET_RE = re.compile(r"jacket|windbreaker", re.I)
EMBRO_RE = re.compile(r"embro", re.I)
SHIRT_RE = re.compile(r"shirt|jersey|polo|\btee\b", re.I)


def classify(item_name, category):
    # category is only ~17% populated (it comes from the inventory file,
    # which most SKUs - including many Fast-selling ones - don't appear
    # in; see Block 3's coverage gap). Gating the garment-keyword check
    # behind category == "APPAREL" would silently default obvious shirts
    # like "UST T-Shirt Sporty" to the non-apparel bucket just because
    # they have no inventory record. So: trust a CONFIRMED "NON-APPAREL"
    # tag (it's a real negative signal), but otherwise classify by name
    # regardless of whether category happens to be populated at all.
    if category == "NON-APPAREL":
        return LT_DEFAULT, "default (confirmed non-apparel)"
    if JACKET_RE.search(item_name):
        return LT_JACKET, "jacket"
    if EMBRO_RE.search(item_name):
        return LT_EMBROIDERED, "embroidered_shirt"
    if SHIRT_RE.search(item_name):
        return LT_SIMPLE_SHIRT, "simple_dtf_puff_shirt"
    return LT_DEFAULT, "default (uncategorized)"


def main():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT product_id, item_name, category FROM Dim_Product", con)

    results = df.apply(
        lambda r: classify(r["item_name"], r["category"]), axis=1, result_type="expand"
    )
    df["lead_time_days"], df["lead_time_tier"] = results[0], results[1]

    con.executemany(
        "UPDATE Dim_Product SET lead_time_days = ? WHERE product_id = ?",
        list(zip(df["lead_time_days"].tolist(), df["product_id"].tolist())),
    )
    con.commit()

    print("=== Lead time assigned, by tier (PROVISIONAL - pending Block 5 confirmation) ===")
    print(df.groupby(["lead_time_tier", "lead_time_days"]).size().to_string())

    print("\n=== Sample of each tier (up to 5 items) ===")
    for tier in sorted(df["lead_time_tier"].unique()):
        sample = df[df["lead_time_tier"] == tier]["item_name"].head(5).tolist()
        print(f"  {tier}: {sample}")

    null_check = con.execute(
        "SELECT COUNT(*) FROM Dim_Product WHERE lead_time_days IS NULL"
    ).fetchone()[0]
    print(f"\nDim_Product rows with lead_time_days still NULL (should be 0): {null_check}")

    con.close()


if __name__ == "__main__":
    main()
