"""
Open work item #1 — canonicalize items + normalize suppliers on the
canonical sales fact table.

Reads  : USTore_sales_long_allocated.csv   (canonical, post-allocation)
         vocab_mapping_FINAL_v2.csv        (raw item -> canonical item)
Writes : USTore_sales_long_allocated_normalized.csv
         supplier_normalization_map.csv    (audit trail of the supplier map)

Three things happen, none of them destructive — every original value is
preserved in a *_raw column so the transform stays fully auditable:

  1. `Item`     -> `canonical_item_name`  via vocab_mapping_FINAL_v2.csv
  2. `Supplier` -> `supplier_name`         via the explicit map below
  3. `Supplier` -> `payment_status`        (CONSIGNMENT / PAID / UNSPECIFIED)

Why an explicit map instead of regex on the parentheticals: three raw
strings carry a parenthetical that is NOT payment status —
  - "ARTS AND LETTERS (SHIRT HAPPENS)"  -> paren names the manufacturer
  - "STITCH CORP. (BLEEVES)"            -> paren names a product line
  - "(Paid)"                            -> parsing artifact, supplier prefix lost
A blanket "strip trailing (...) and read PAID/CONSIGNMENT" rule would
mangle all three. The 40 strings are enumerated by hand instead.
"""
import sys

import pandas as pd

FACT_CSV = "USTore_sales_long_allocated.csv"
MAPPING_CSV = "vocab_mapping_FINAL_v2.csv"
OUT_CSV = "USTore_sales_long_allocated_normalized.csv"
SUPPLIER_MAP_CSV = "supplier_normalization_map.csv"

# Payment-status vocabulary. UNSPECIFIED = the source row carried no
# payment marker (a bare supplier name, or a non-payment parenthetical).
CONSIGNMENT = "CONSIGNMENT"
PAID = "PAID"
UNSPECIFIED = "UNSPECIFIED"

# raw Supplier string -> (supplier_name, payment_status, note)
# Covers all 40 distinct strings observed in the fact table.
SUPPLIER_MAP = {
    # ---- the parsing artifact (see module docstring / §9) --------------
    # All 180 "(Paid)" rows are July-2025 records of five STITCH CORP.
    # BLEEVES-line items (Ballpen, Bamboo Pen, Corp Jacket V2, Kraft/
    # Bamboo Notebook). Those same items appear only under STITCH CORP.
    # variants elsewhere, so the dropped prefix was STITCH CORP.
    "(Paid)": ("STITCH CORP.", PAID, "recovered artifact: STITCH CORP. BLEEVES items, Jul 2025"),

    # ---- BLAZE ----------------------------------------------------------
    "BLAZE": ("BLAZE", UNSPECIFIED, ""),
    "BLAZE (CONSIGNMENT)": ("BLAZE", CONSIGNMENT, ""),

    # ---- JUC ------------------------------------------------------------
    "JUC": ("JUC", UNSPECIFIED, ""),
    "JUC (CONSIGNMENT)": ("JUC", CONSIGNMENT, ""),

    # ---- JYL ATHLETICA (JYL = abbreviation) -----------------------------
    "JYL": ("JYL ATHLETICA", UNSPECIFIED, "abbreviation of JYL ATHLETICA"),
    "JYL ATHLETICA": ("JYL ATHLETICA", UNSPECIFIED, ""),
    "JYL ATHLETICA (CONSIGNMENT)": ("JYL ATHLETICA", CONSIGNMENT, ""),

    # ---- NAPOLIZ ENTERPRISES (NAPOLIZ = short form) ---------------------
    "NAPOLIZ": ("NAPOLIZ ENTERPRISES", UNSPECIFIED, "short form of NAPOLIZ ENTERPRISES"),
    "NAPOLIZ ENTERPRISES": ("NAPOLIZ ENTERPRISES", UNSPECIFIED, ""),
    "NAPOLIZ ENTERPRISES (CONSIGNMENT)": ("NAPOLIZ ENTERPRISES", CONSIGNMENT, ""),

    # ---- VARSITY LIFESTYLE (LIFE STYLE = spacing typo) ------------------
    "VARSITY LIFE STYLE": ("VARSITY LIFESTYLE", UNSPECIFIED, "spacing variant"),
    "VARSITY LIFESTYLE": ("VARSITY LIFESTYLE", UNSPECIFIED, ""),
    "VARSITY LIFESTYLE (CONSIGNMENT)": ("VARSITY LIFESTYLE", CONSIGNMENT, ""),

    # ---- THREADMARKED ---------------------------------------------------
    "THREADMARKED": ("THREADMARKED", UNSPECIFIED, ""),
    "THREADMARKED (CONSIGNMENT)": ("THREADMARKED", CONSIGNMENT, ""),
    "THREADMARKED (PAID)": ("THREADMARKED", PAID, ""),

    # ---- STITCH CORP. (BLEEVES = product line, not payment status) ------
    "STITCH CORP. (BLEEVES)": ("STITCH CORP.", UNSPECIFIED, "BLEEVES = product line; no payment marker"),
    "STITCH CORP. (BLEEVES CONSIGNMENT)": ("STITCH CORP.", CONSIGNMENT, "BLEEVES product line"),
    "STITCH CORP. (BLEEVES PAID)": ("STITCH CORP.", PAID, "BLEEVES product line"),
    "STITCH CORP. (CONSIGNMENT)": ("STITCH CORP.", CONSIGNMENT, ""),
    "STITCH CORP. (PAID)": ("STITCH CORP.", PAID, ""),

    # ---- MADEBYRUZ CONSUMER GOODS TRADING -------------------------------
    "MADEBYRUZ CONSUMER GOODS TRADING": ("MADEBYRUZ CONSUMER GOODS TRADING", UNSPECIFIED, ""),
    "MADEBYRUZ CONSUMER GOODS TRADING (CONSIGNMENT)": ("MADEBYRUZ CONSUMER GOODS TRADING", CONSIGNMENT, ""),

    # ---- NEW TRENDS -----------------------------------------------------
    "NEW TRENDS": ("NEW TRENDS", UNSPECIFIED, ""),
    "NEW TRENDS (PAID)": ("NEW TRENDS", PAID, ""),

    # ---- TET AND DARS ---------------------------------------------------
    "TET AND DARS": ("TET AND DARS", UNSPECIFIED, ""),
    "TET AND DARS (PAID)": ("TET AND DARS", PAID, ""),

    # ---- TOP GUN PHILS. -------------------------------------------------
    "TOP GUN PHILS.": ("TOP GUN PHILS.", UNSPECIFIED, ""),
    "TOP GUN PHILS. (PAID)": ("TOP GUN PHILS.", PAID, ""),

    # ---- USTORE (the store itself, acting as consignor) -----------------
    "USTORE": ("USTORE", UNSPECIFIED, ""),
    "USTORE (PAID)": ("USTORE", PAID, ""),

    # ---- SHIRT HAPPENS (manufacturer; "ARTS AND LETTERS" = college) -----
    "SHIRT HAPPENS": ("SHIRT HAPPENS", UNSPECIFIED, ""),
    "SHIRT HAPPENS (CONSIGNMENT)": ("SHIRT HAPPENS", CONSIGNMENT, ""),
    "ARTS AND LETTERS (SHIRT HAPPENS)": ("SHIRT HAPPENS", UNSPECIFIED, "manufacturer SHIRT HAPPENS; merch for the Arts & Letters college"),

    # ---- CENTRAL SEMINARY (merge flagged as an assumption) --------------
    "CENTRAL SEMINARY": ("CENTRAL SEMINARY", UNSPECIFIED, ""),
    "ASSOCIATION FOR THE EDUCATIONAL ASSISTANCE OF POOR SEMINARIANS, INC.":
        ("CENTRAL SEMINARY", UNSPECIFIED, "ASSUMED same entity as CENTRAL SEMINARY (per project context) — verify with staff"),

    # ---- single-string suppliers (internal UST units) -------------------
    "COLLEGE OF SCIENCE AT 100": ("COLLEGE OF SCIENCE AT 100", UNSPECIFIED, ""),
    "IPEA": ("IPEA", UNSPECIFIED, ""),
    "TIGER FLASK": ("TIGER FLASK", UNSPECIFIED, ""),
}


def load_item_mapping():
    vm = pd.read_csv(MAPPING_CSV)
    vm["raw_name"] = vm["raw_name"].astype(str).str.strip()
    vm["canonical_item_name"] = vm["canonical_item_name"].astype(str).str.strip()
    return dict(zip(vm["raw_name"], vm["canonical_item_name"]))


def main():
    item_map = load_item_mapping()
    fact = pd.read_csv(FACT_CSV, dtype={"Total Quantity": "int64"})

    units_before = int(fact["Total Quantity"].sum())
    rows_before = len(fact)

    # ---- 1. canonicalize item names -----------------------------------
    item_stripped = fact["Item"].astype(str).str.strip()
    fact["canonical_item_name"] = item_stripped.map(item_map)
    unmapped_items = sorted(item_stripped[fact["canonical_item_name"].isna()].unique())

    # ---- 2 & 3. normalize supplier + split payment status -------------
    sup_stripped = fact["Supplier"].astype(str).str.strip()
    unmapped_suppliers = sorted(s for s in sup_stripped.unique() if s not in SUPPLIER_MAP)

    if unmapped_items or unmapped_suppliers:
        if unmapped_items:
            print(f"ABORT: {len(unmapped_items)} item name(s) not in vocab map:")
            for x in unmapped_items:
                print("   -", repr(x))
        if unmapped_suppliers:
            print(f"ABORT: {len(unmapped_suppliers)} supplier string(s) not in SUPPLIER_MAP:")
            for x in unmapped_suppliers:
                print("   -", repr(x))
        sys.exit(1)

    fact["supplier_name"] = sup_stripped.map(lambda s: SUPPLIER_MAP[s][0])
    fact["payment_status"] = sup_stripped.map(lambda s: SUPPLIER_MAP[s][1])

    # ---- write outputs -------------------------------------------------
    out = fact[[
        "Date", "Item", "canonical_item_name", "Total Quantity",
        "Supplier", "supplier_name", "payment_status",
        "imputation_flag", "weight",
    ]]
    out.to_csv(OUT_CSV, index=False)

    audit = pd.DataFrame(
        [(raw, name, status, note) for raw, (name, status, note) in sorted(SUPPLIER_MAP.items())],
        columns=["raw_supplier", "supplier_name", "payment_status", "note"],
    )
    audit.to_csv(SUPPLIER_MAP_CSV, index=False)

    # ---- verification / summary ---------------------------------------
    units_after = int(out["Total Quantity"].sum())
    assert len(out) == rows_before, "row count changed!"
    assert units_after == units_before, "unit total changed!"

    print("=== SUMMARY ===")
    print(f"rows: {rows_before} (unchanged)")
    print(f"units: {units_after} (conserved, was {units_before})")
    print(f"raw item names {item_stripped.nunique()} -> {out['canonical_item_name'].nunique()} canonical")
    print(f"raw supplier strings {sup_stripped.nunique()} -> {out['supplier_name'].nunique()} suppliers")
    print("payment_status breakdown:")
    for k, v in out["payment_status"].value_counts().items():
        print(f"   {k:12s} {v}")
    print(f"\nwrote {OUT_CSV}")
    print(f"wrote {SUPPLIER_MAP_CSV} ({len(audit)} rows)")


if __name__ == "__main__":
    main()
