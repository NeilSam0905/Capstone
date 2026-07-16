"""
Build a draft controlled-vocabulary mapping for item names found in
USTore_sales_long_*.csv and USTore_inventory_excel_long.csv.

This is a DRAFT ONLY. Nothing is auto-merged with confidence; every
suggestion must be confirmed by USTore staff before use. See
vocab_mapping_draft.csv for the output and the printed summary for
counts.
"""

import re
from collections import defaultdict

import pandas as pd
from rapidfuzz import fuzz, process

SALES_FILE = "USTore_sales_long_May_Aug2024-May2026.csv"
INVENTORY_FILE = "USTore_inventory_excel_long.csv"
OUTPUT_FILE = "vocab_mapping_draft.csv"

# Words that mark a real product-level difference (size / color / pattern).
# If two similar names differ in these, they must NOT be auto-merged.
COLOR_WORDS = {
    "black", "white", "blue", "red", "yellow", "green", "gray", "grey",
    "maroon", "pink", "orange", "purple", "navy", "gold", "golden",
    "silver", "brown", "beige", "tiger", "camo",
}
SIZE_WORDS = {
    "xs", "s", "m", "l", "xl", "xxl", "xxxl", "small", "medium", "large",
    "kids", "adult", "junior",
}
# Supplier/SKU codes like (MCX-A501), (MTA-T120) — different codes mean
# a different SKU even if the surrounding text is nearly identical.
SKU_CODE_RE = re.compile(r"\(([A-Za-z]{2,5}-?[A-Za-z0-9]{2,6})\)")

EXACT_SCORE_THRESHOLD = 100          # identical after normalizing
MEDIUM_SCORE_THRESHOLD = 90          # likely typo / abbreviation
CLUSTER_SCORE_THRESHOLD = 80         # below this, don't even group


def normalize(name: str) -> str:
    """Lowercase + collapse whitespace so pure spelling/case/space
    differences collapse to the same key. Does not strip words, so
    real content differences (color, size, sku code) are preserved."""
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*/\s*", "/", s)
    return s


def word_set(name: str, vocab: set) -> frozenset:
    tokens = re.findall(r"[a-z]+", name.lower())
    return frozenset(t for t in tokens if t in vocab)


def sku_code(name: str):
    m = SKU_CODE_RE.search(name)
    return m.group(1).upper() if m else None


NUMBER_RE = re.compile(r"\d+\.?\d*")


def number_set(name: str) -> frozenset:
    """Standalone numbers in the name: catches version numbers (V.1/V.2),
    counts (1 line/2 lines), size ranges (3XL-5XL), and price tags (@1500)
    -- all real product-level differences a plain word/color/size check
    would miss."""
    return frozenset(NUMBER_RE.findall(name))


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def main():
    sales = pd.read_csv(SALES_FILE)
    inventory = pd.read_csv(INVENTORY_FILE)

    sales["Item"] = sales["Item"].astype(str).str.strip()
    inventory["Item"] = inventory["Item"].astype(str).str.strip()

    sales_counts = sales.groupby("Item").size().to_dict()
    inv_counts = inventory.groupby("Item").size().to_dict()

    all_names = sorted(set(sales_counts) | set(inv_counts))
    print(f"Distinct raw item names (combined, deduped): {len(all_names)}")

    # ---- Step 1: per-name source + row_count table ----
    rows = {}
    for name in all_names:
        sources = []
        row_count = 0
        if name in sales_counts:
            sources.append("sales")
            row_count += sales_counts[name]
        if name in inv_counts:
            sources.append("inventory")
            row_count += inv_counts[name]
        rows[name] = {
            "source": "+".join(sources),
            "row_count": row_count,
        }

    # ---- Step 2: group by normalized spelling, then fuzzy-cluster those
    # distinct normalized keys ----
    # Two raw names that normalize to the exact same text (case/whitespace
    # only difference) are an unambiguous duplicate pair, no matter what
    # else ends up nearby in the fuzzy grouping below. We decide that
    # first, independent of the wider cluster, so a same-SKU case-only
    # duplicate never gets downgraded just because a *different* SKU
    # happened to fuzzy-match into the same neighborhood.
    norm_map = {name: normalize(name) for name in all_names}
    norm_groups = defaultdict(list)
    for name in all_names:
        norm_groups[norm_map[name]].append(name)
    distinct_keys = sorted(norm_groups.keys())

    uf = UnionFind(distinct_keys)
    score_matrix = process.cdist(
        distinct_keys, distinct_keys, scorer=fuzz.token_sort_ratio,
    )
    n = len(distinct_keys)
    for i in range(n):
        for j in range(i + 1, n):
            if score_matrix[i][j] >= CLUSTER_SCORE_THRESHOLD:
                uf.union(distinct_keys[i], distinct_keys[j])

    key_clusters = defaultdict(list)
    for key in distinct_keys:
        key_clusters[uf.find(key)].append(key)

    # ---- Step 3: decide suggestion / confidence / needs_review ----
    # Unit of decision is a "spelling group" (one normalized key + all its
    # raw variants). Whether that group sits alone or with siblings in a
    # wider fuzzy cluster only affects cluster_id (for review adjacency),
    # not the confidence of the group's own internal merge.
    cluster_id_counter = 0
    output_rows = []

    for keys_in_cluster in key_clusters.values():
        cluster_id_counter += 1
        cluster_id = f"C{cluster_id_counter:04d}"

        multi_text_cluster = len(keys_in_cluster) > 1

        # Attribute (color/size/SKU) differences between the DISTINCT
        # texts in this cluster -> those texts must not be merged with
        # each other, even though they're grouped for review.
        attribute_diff = False
        if multi_text_cluster:
            reps = [norm_groups[k][0] for k in keys_in_cluster]
            for i in range(len(reps)):
                for j in range(i + 1, len(reps)):
                    a, b = reps[i], reps[j]
                    if word_set(a, COLOR_WORDS) != word_set(b, COLOR_WORDS):
                        attribute_diff = True
                    if word_set(a, SIZE_WORDS) != word_set(b, SIZE_WORDS):
                        attribute_diff = True
                    ca, cb = sku_code(a), sku_code(b)
                    if ca and cb and ca != cb:
                        attribute_diff = True
                    if number_set(a) != number_set(b):
                        attribute_diff = True
                    if attribute_diff:
                        break
                if attribute_diff:
                    break

        overall_canonical = None
        cluster_min_score = None
        if multi_text_cluster and not attribute_diff:
            overall_canonical = max(
                (name for k in keys_in_cluster for name in norm_groups[k]),
                key=lambda m: rows[m]["row_count"],
            )
            idxs = [distinct_keys.index(k) for k in keys_in_cluster]
            cluster_min_score = min(
                score_matrix[idxs[i]][idxs[j]]
                for i in range(len(idxs)) for j in range(i + 1, len(idxs))
            )

        for key in keys_in_cluster:
            variants = norm_groups[key]
            internal_rep = max(variants, key=lambda m: rows[m]["row_count"])
            internal_rep_clean = re.sub(r"\s+", " ", internal_rep).strip()

            # Merging the variants WITHIN one normalized key is always safe:
            # by construction they are the same text apart from case/
            # whitespace, so this decision is independent of whatever else
            # is nearby in the wider fuzzy cluster.
            if not multi_text_cluster:
                # Nothing else nearby at all -> nothing to flag.
                confidence, needs_review = "high", "no"
                suggested = variants[0] if len(variants) == 1 else internal_rep_clean
            elif attribute_diff:
                # Sits among other texts that differ by color/size/SKU ->
                # never merge across that boundary. The within-key merge
                # itself is still safe/high-confidence; we just want a
                # human to glance at the family and confirm the boundary.
                confidence, needs_review, suggested = "high", "yes", internal_rep_clean
            else:
                # Multiple distinct spellings, no attribute difference ->
                # probably the same product (typo/abbreviation); genuinely
                # uncertain, so confidence reflects the fuzzy-match score.
                confidence = "medium" if cluster_min_score >= MEDIUM_SCORE_THRESHOLD else "low"
                needs_review = "yes"
                suggested = re.sub(r"\s+", " ", overall_canonical).strip()

            for name in variants:
                output_rows.append({
                    "raw_name": name,
                    "suggested_canonical_name": suggested,
                    "cluster_id": cluster_id,
                    "source": rows[name]["source"],
                    "row_count": rows[name]["row_count"],
                    "confidence": confidence,
                    "needs_review": needs_review,
                })

    out_df = pd.DataFrame(output_rows)
    out_df = out_df.sort_values(["cluster_id", "raw_name"]).reset_index(drop=True)
    out_df.to_csv(OUTPUT_FILE, index=False)

    total_names = len(out_df)
    total_clusters = out_df["cluster_id"].nunique()
    multi_member_clusters = out_df.groupby("cluster_id").size()
    multi_member_clusters = (multi_member_clusters > 1).sum()
    needs_review_count = (out_df["needs_review"] == "yes").sum()

    print(f"Total clusters formed: {total_clusters} "
          f"({multi_member_clusters} contain more than one raw name)")
    print(f"Rows flagged needs_review=yes: {needs_review_count} / {total_names}")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
