"""
Read-only diagnostic: for each of the 269 inventory-only ("no sales")
canonical items, find the closest-matching item among the 273 items that
DO have sales, using token (shared-word) overlap. Writes candidate_pairs.csv
for human review. Does not modify the mapping, the CSVs, or the database.
"""
import re

import pandas as pd

SALES_MAPPED_CSV = "USTore_sales_long_May_Aug2024-May2026_mapped.csv"
INVENTORY_MAPPED_CSV = "USTore_inventory_excel_long_mapped.csv"
OUT_CSV = "candidate_pairs.csv"

# generic/connective words that would create false-positive overlaps
STOPWORDS = {
    "for", "the", "a", "an", "of", "w", "with", "and", "in", "on", "to",
    "no", "sig", "set",
}


def tokenize(name):
    words = re.findall(r"[a-z0-9]+", str(name).lower())
    filtered = [w for w in words if w not in STOPWORDS]
    return set(filtered) if filtered else set(words)


def main():
    sales = pd.read_csv(SALES_MAPPED_CSV)
    inv = pd.read_csv(INVENTORY_MAPPED_CSV)

    sales_items = set(sales["canonical_item_name"].unique())
    inv_items = set(inv["canonical_item_name"].unique())
    no_sales = sorted(inv_items - sales_items)
    has_sales = sorted(sales_items)

    print(f"no-sales items: {len(no_sales)}")
    print(f"has-sales items: {len(has_sales)}")

    # category + latest qty per no-sales item, from inventory log
    no_sales_inv = inv[inv["canonical_item_name"].isin(no_sales)].copy()
    no_sales_inv["parsed_date"] = pd.to_datetime(
        no_sales_inv["Date"], format="%Y-%m-%d", errors="raise"
    )
    no_sales_inv["Quantity"] = pd.to_numeric(no_sales_inv["Quantity"], errors="coerce")

    def mode_or_none(series):
        s = series.dropna()
        return s.mode().iloc[0] if not s.empty else None

    category_map = no_sales_inv.groupby("canonical_item_name")["Category"].apply(mode_or_none)

    latest_rows = (
        no_sales_inv.sort_values("parsed_date")
        .groupby("canonical_item_name")
        .tail(1)
        .set_index("canonical_item_name")["Quantity"]
    )

    # category counts (answer to Q1)
    print("\nCategory breakdown of the 269 no-sales items:")
    print(category_map.value_counts(dropna=False))

    # sales row counts per has-sales item (for tie-breaking / context)
    sales_row_counts = sales["canonical_item_name"].value_counts()

    # precompute tokens for has-sales items
    sales_tokens = {item: tokenize(item) for item in has_sales}

    records = []
    for item in no_sales:
        tokens_a = tokenize(item)
        best_item = None
        best_score = 0.0
        best_shared = set()
        for cand, tokens_b in sales_tokens.items():
            if not tokens_a or not tokens_b:
                continue
            shared = tokens_a & tokens_b
            if not shared:
                continue
            union = tokens_a | tokens_b
            score = len(shared) / len(union)
            if score > best_score or (
                score == best_score
                and best_item is not None
                and sales_row_counts.get(cand, 0) > sales_row_counts.get(best_item, 0)
            ):
                best_score = score
                best_item = cand
                best_shared = shared

        records.append(
            {
                "no_sales_item": item,
                "category": category_map.get(item),
                "latest_qty": latest_rows.get(item),
                "suggested_sales_item": best_item,
                "sales_row_count": sales_row_counts.get(best_item, None) if best_item else None,
                "shared_words": " ".join(sorted(best_shared)) if best_item else "",
                "match_score": round(best_score, 3) if best_item else 0.0,
            }
        )

    out = pd.DataFrame(records)
    out = out.sort_values("latest_qty", ascending=False, na_position="last")
    out.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV} ({len(out)} rows)")

    no_match = out[out["suggested_sales_item"].isna()]
    print(f"no-sales items with NO token overlap at all: {len(no_match)}")


if __name__ == "__main__":
    main()
