"""
tools/audit_price_suffix_skus.py
------------------------------------------------------------------
MEASUREMENT ONLY. This script changes nothing.

71 of the 519 rows in Dim_Product carry a price inside the item name
("Lanyard @180", "Arch @320"). 12 of those also have a de-priced twin
row ("Lanyard", "Arch") sitting alongside them, across 8 base families.
Whether any of those pairs are the same physical product - and should
therefore be merged - is a question for USTore staff, not for this
script. It is logged as **B6** in the deferred-decision register.

So this file measures and reports. It does not touch Dim_Product,
vocab_mapping_FINAL_v5.csv or allocation_groups.csv, all of which are on
the run's do-not-touch list. The mechanical guard is that
tools/assert_invariants.py must still exit 0 afterwards: if anything here
had merged an SKU, the FSN split would move off 58/228/233 and that gate
would fail.

Why it matters for Chapter 4: `Lanyard @180` alone is 7,201 units, the
largest single SKU in the dataset and Fast-classified, while a bare
`Lanyard` row exists with zero units and an N class. Any reader comparing
"lanyard sales" across the two will get a different answer depending on
which row they pick.

Run:
    python tools/audit_price_suffix_skus.py

Outputs docs/price_suffix_audit.csv and docs/PRICE_SUFFIX_AUDIT.md.
Exit 0 = the audit's expected values hold.
------------------------------------------------------------------
"""
import csv
import os
import re
import sqlite3
import sys
from collections import defaultdict

DB_NAME = "ustore.db"
ALLOC_GROUPS = "allocation_groups.csv"
VOCAB = "vocab_mapping_FINAL_v5.csv"
DOCS = "docs"
OUT_CSV = os.path.join(DOCS, "price_suffix_audit.csv")
OUT_MD = os.path.join(DOCS, "PRICE_SUFFIX_AUDIT.md")

PRICE_SUFFIX_RE = re.compile(r"\s*@.*$")

# ---- expected values: inputs, not outputs -------------------------
EXP_N_WITH_AT = 71
EXP_N_WITH_TWIN = 12
EXP_N_TWIN_FAMILIES = 8
EXP_TOP_SKU = ("Lanyard @180", "F", 7201)
EXP_N_LANYARD_WITH_ROWS = 10
EXP_LANYARD_SUFFIXED = 3
EXP_LANYARD_FSN = {"F": 4, "S": 6}
EXP_TWIN_FAMILIES = [
    "Arch", "Eco Bag", "ID Case", "Keychain",
    "Lanyard", "Long Sticker", "New Tiger Plushie Big", "New Tiger Plushie Small",
]


def base_name(name):
    """'Lanyard @180' -> 'Lanyard'. Everything from the '@' onward is the
    price tag the tally sheets fold into the label."""
    return PRICE_SUFFIX_RE.sub("", name).strip()


def load_allocation_membership():
    """Names that appear in allocation_groups.csv, as either a group label
    or a constituent. That file stores RAW spellings, so each is mapped
    through the vocabulary to its canonical form before comparison -
    otherwise a raw/canonical spelling difference reads as 'not grouped'."""
    mapping = {}
    with open(VOCAB, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapping[row["raw_name"].strip()] = row["canonical_item_name"].strip()

    members = set()
    with open(ALLOC_GROUPS, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for col in ("generic_sales_name", "inventory_variant"):
                raw = (row.get(col) or "").strip()
                if not raw:
                    continue
                members.add(raw)
                if raw in mapping:
                    members.add(mapping[raw])
    return members


def main():
    os.makedirs(DOCS, exist_ok=True)
    failures = []

    def expect(label, actual, expected):
        ok = actual == expected
        print("[%s] %-34s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                   "" if ok else "   != expected %r" % (expected,)))
        if not ok:
            failures.append((label, actual, expected))

    con = sqlite3.connect(DB_NAME)
    rows = con.execute("""
        SELECT p.product_id, p.item_name, p.fsn_class, COALESCE(p.is_hvl, 0),
               COALESCE(SUM(f.quantity_sold), 0),
               COUNT(f.sale_id),
               COUNT(DISTINCT CASE WHEN f.quantity_sold > 0 THEN f.date_id END)
        FROM Dim_Product p
        LEFT JOIN Fact_Sales f ON f.product_id = p.product_id
        GROUP BY p.product_id
        ORDER BY p.item_name
    """).fetchall()
    con.close()

    all_names = {r[1] for r in rows}
    alloc = load_allocation_membership()

    suffixed = []
    for _pid, name, fsn, hvl, units, fact_rows, sale_days in rows:
        if "@" not in name:
            continue
        base = base_name(name)
        suffixed.append({
            "item_name": name,
            "base_name": base,
            "has_depriced_twin": int(base in all_names),
            "in_allocation_groups": int(name in alloc or base in alloc),
            "units": int(units),
            "fact_rows": int(fact_rows),
            "sale_days": int(sale_days),
            "fsn_class": fsn if fsn is not None else "",
            "is_hvl": int(hvl),
        })

    # ---- write the audit ------------------------------------------
    cols = ["item_name", "base_name", "has_depriced_twin", "in_allocation_groups",
            "units", "fact_rows", "sale_days", "fsn_class", "is_hvl"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in sorted(suffixed, key=lambda x: (-x["units"], x["item_name"])):
            w.writerow(r)

    # ---- the numbers ----------------------------------------------
    twins = [r for r in suffixed if r["has_depriced_twin"]]
    families = sorted({r["base_name"] for r in twins})
    top = max(suffixed, key=lambda r: r["units"])

    lanyards = [r for r in rows if "LANYARD" in r[1].upper()]
    lan_rows = [r for r in lanyards if r[5] > 0]
    lan_fsn = defaultdict(int)
    for r in lan_rows:
        lan_fsn[r[2]] += 1

    expect("products with '@'", len(suffixed), EXP_N_WITH_AT)
    expect("with a de-priced twin", len(twins), EXP_N_WITH_TWIN)
    expect("twin base families", len(families), EXP_N_TWIN_FAMILIES)
    expect("twin family names", families, EXP_TWIN_FAMILIES)
    expect("top suffixed SKU",
           (top["item_name"], top["fsn_class"], top["units"]), EXP_TOP_SKU)
    expect("lanyard SKUs with >=1 row", len(lan_rows), EXP_N_LANYARD_WITH_ROWS)
    expect("  of which price-suffixed",
           sum(1 for r in lan_rows if "@" in r[1]), EXP_LANYARD_SUFFIXED)
    expect("  lanyard FSN split", dict(sorted(lan_fsn.items())),
           dict(sorted(EXP_LANYARD_FSN.items())))

    write_markdown(rows, suffixed, twins, families, top, lanyards, lan_rows, lan_fsn)
    print("\nWrote %s (%d rows)" % (OUT_CSV, len(suffixed)))
    print("Wrote %s" % OUT_MD)

    if failures:
        print("\nFAILED: %d check(s). Record under 'Gate failures' in "
              "CHANGES_tyrone.md; do not edit the expected values." % len(failures))
        return 1
    print("\nAudit complete. Nothing was modified.")
    return 0


def write_markdown(rows, suffixed, twins, families, top, lanyards, lan_rows, lan_fsn):
    by_family = defaultdict(list)
    for r in twins:
        by_family[r["base_name"]].append(r)
    # product_id, item_name, fsn_class, is_hvl, units, fact_rows, sale_days
    by_name = {r[1]: r for r in rows}

    lines = []
    a = lines.append
    a("# Price-suffix SKU audit")
    a("")
    a("Generated by `tools/audit_price_suffix_skus.py`. **Measurement only - nothing")
    a("in the vocabulary, the allocation groups or `Dim_Product` was modified.** The")
    a("merge ruling is deferred decision **B6** and belongs to USTore staff.")
    a("")
    a("## The shape of it")
    a("")
    a("| | |")
    a("|---|---:|")
    a("| Rows in `Dim_Product` | 519 |")
    a("| Carrying a price suffix (`@`) | **%d** |" % len(suffixed))
    a("| Of those, with a de-priced twin row | **%d** |" % len(twins))
    a("| Base families containing such a twin | **%d** |" % len(families))
    a("| Largest suffixed SKU | `%s` (%s, %s units) |"
      % (top["item_name"], top["fsn_class"], "{:,}".format(top["units"])))
    a("")
    a("Per-SKU detail, sorted by units, is in `price_suffix_audit.csv`.")
    a("")
    a("## The %d twin families" % len(families))
    a("")
    a("Each of these has both a priced row and a bare row. Whether the two are the")
    a("same physical product is exactly what nobody in the repo can answer.")
    a("")
    a("| Base family | Priced rows | Units (priced) | Bare-row class |")
    a("|---|---|---:|---|")
    for fam in families:
        members = by_family[fam]
        labels = ", ".join("`%s`" % m["item_name"] for m in sorted(
            members, key=lambda x: -x["units"]))
        units = sum(m["units"] for m in members)
        bare = by_name.get(fam)
        bare_cls = "%s, %s units" % (bare[2] or "-", "{:,}".format(bare[4])) if bare else "-"
        a("| %s | %s | %s | %s |" % (fam, labels, "{:,}".format(units), bare_cls))
    a("")

    # The families split cleanly in two, and the two halves are different
    # problems. This is the most useful thing the audit produces for B6.
    dead = [f for f in families if (by_name.get(f) or (None,) * 5)[4] == 0]
    live = [f for f in families if f not in dead]
    a("**These are two different problems, not one.**")
    a("")
    a("- %d families have a bare row with **0 units and class N** (%s). Nothing was"
      % (len(dead), ", ".join("`%s`" % f for f in dead)))
    a("  ever recorded against the bare label, so it is almost certainly a vocabulary")
    a("  artifact rather than a real product. Merging these would move no units.")
    a("- %d families have a bare row carrying **real sales** (%s). Here the bare and"
      % (len(live), ", ".join("`%s`" % f for f in live)))
    a("  priced labels were both used, in the same dataset, and merging them *would*")
    a("  move units between SKUs and change the FSN split. These are the ones that")
    a("  genuinely need a staff ruling.")
    a("")
    a("## Why this is not cosmetic")
    a("")
    a("`%s` is **%s units** - the largest single SKU in the dataset - and is"
      % (top["item_name"], "{:,}".format(top["units"])))
    a("Fast-classified. A bare `Lanyard` row also exists, with 0 units and class N.")
    a("Anyone asking \"how many lanyards did we sell\" gets a different answer")
    a("depending on which row they read, and the FSN classification depends on the")
    a("same choice.")
    a("")
    a("Lanyards are the worst case: **%d** distinct lanyard SKUs carry at least one" % len(lan_rows))
    a("`Fact_Sales` row (%d price-suffixed, %d named variants), split %s."
      % (sum(1 for r in lan_rows if "@" in r[1]),
         len(lan_rows) - sum(1 for r in lan_rows if "@" in r[1]),
         " / ".join("%d %s" % (v, k) for k, v in sorted(lan_fsn.items()))))
    a("A further %d lanyard rows exist with no sales at all." % (len(lanyards) - len(lan_rows)))
    a("")
    a("| Lanyard SKU | Class | Units | Fact rows | Sale days |")
    a("|---|---|---:|---:|---:|")
    for r in sorted(lanyards, key=lambda x: -x[4]):
        a("| `%s` | %s | %s | %d | %d |" % (r[1], r[2] or "-", "{:,}".format(r[4]), r[5], r[6]))
    a("")
    a("## What would settle it")
    a("")
    a("A USTore staff member confirming, per family, whether the priced and bare")
    a("labels denote one product or two. Until then the rows stay separate, because")
    a("merging them is irreversible in the vocabulary and would silently move the")
    a("FSN split, the tier counts and every forecast built on them.")
    a("")

    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
