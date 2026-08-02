"""
Proportional allocation of price-grouped USTore sales rows
==========================================================
Implements the manuscript's ETL step: historical tally rows that bundle several
SKUs sharing a price point are split into one row per real SKU, distributing the
grouped quantity in proportion to each SKU's beginning-of-month stock.

  allocated(s" ) = round( Q * stock(s" , M) / Sum stock(s#, M) )   [largest-remainder]

MAP BEFORE ALLOCATE (Code Work Plan v2, Block 2.2)
--------------------------------------------------
This script used to read the RAW sales/inventory CSVs and match group
labels and constituents on raw names. That worked only by luck: the 18
group labels and 45 variants happened to be spelled identically to their
canonical names. Any raw spelling that differs - "Back Pack" vs
"Back pack", "QUIANA SHIRT (B&Y SUBLI)" vs "QUIANA SUBLI SHIRT (B&Y)" -
silently missed its group and passed through as an unsplit bundled row.
Allocation weights each SKU by its inventory stock, which is a
name-matching operation, so the canonical name is the correct join key.

It now reads step1_apply_mapping.py's mapped outputs and joins on
canonical_item_name. step1 must run first; it aborts on any unmapped
name, so this script can trust its input.

Inputs
  - mapped sales CSV     : Date, canonical_item_name, Total Quantity, Supplier
                           (ISO YYYY-MM-DD; from step1_apply_mapping.py)
  - mapped inventory CSV : Date, canonical_item_name, ..., Quantity
                           (monthly stock; from step1_apply_mapping.py)
  - allocation_groups CSV: generic_sales_name  <- inventory_variant (group
                           members). Still stores RAW names; both columns
                           are canonicalised here through the vocabulary
                           mapping, so a future edit can use either.

Weight (stock) fallback ladder, per constituent:
  1. exact_month        - constituent's total stock in the sale's month M
  2. nearest_month      - nearest available month's stock (ties -> most recent prior)
  3. no_inventory       - constituent never appears in inventory -> weight 0
If every constituent resolves to 0 -> equal split across constituents.

Output (Fact_Sales-style long CSV):
  Date, canonical_item_name, Total Quantity, Supplier, imputation_flag, weight
  - non-grouped rows pass through unchanged (imputation_flag=0, weight=1.0)
  - grouped rows are REPLACED by their allocated constituent rows
    (imputation_flag=1, weight=0.5); constituents allocated 0 units are dropped
Plus an audit CSV documenting every split.
"""
import csv, datetime, argparse
from collections import defaultdict

def parse_date(s, where=""):
    """ISO 8601 only, and it raises. The old multi-format ladder tried
    DD/MM before YYYY-MM-DD and returned None on failure, so a date in
    the wrong format was either read as the wrong day or silently
    dropped into the 'no_date' allocation basis. Every CSV this script
    reads is ISO now (see verify_data.py), so anything else is a bug."""
    s=(s or "").strip()
    try: return datetime.datetime.strptime(s,"%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"non-ISO date {s!r}{' in '+where if where else ''} "
                         f"- expected YYYY-MM-DD; run verify_data.py") from None

ITEM_COL="canonical_item_name"

def open_mapped(path):
    """DictReader over a step1 output, refusing a raw (unmapped) CSV."""
    rdr=csv.DictReader(open(path,encoding="utf-8"))
    if ITEM_COL not in (rdr.fieldnames or []):
        raise SystemExit(f"{path}: no {ITEM_COL!r} column - this looks like a raw CSV. "
                         f"Run step1_apply_mapping.py first (map before allocate).")
    return rdr

def load_mapping(path):
    """Only used to canonicalise allocation_groups.csv. The sales and
    inventory data arrive already mapped from step1."""
    return {r["raw_name"].strip(): r["canonical_item_name"].strip()
            for r in csv.DictReader(open(path,encoding="utf-8"))}

def canon(mapping, name, where):
    n=(name or "").strip()
    if n in mapping: return mapping[n]
    raise SystemExit(f"{where}: {n!r} is not in the vocabulary mapping - "
                     f"add it there before allocating.")

def num(v):
    if v is None or str(v).strip()=="" : return 0.0
    try: return float(str(v).replace(",","").strip())
    except ValueError: return 0.0

def month_key(d): return d.year*12 + (d.month-1)

def largest_remainder(Q, weights):
    """Split integer Q across nonneg weights (sum>0); result sums exactly to Q."""
    tot=sum(weights)
    raw=[Q*w/tot for w in weights]
    floor=[int(x) for x in raw]
    left=Q-sum(floor)
    order=sorted(range(len(weights)), key=lambda i: raw[i]-floor[i], reverse=True)
    for i in range(left): floor[order[i]] += 1
    return floor

def build_stock(inv_path):
    """stock[canonical][monthkey] = total beginning-month quantity, summed over
    sizes/locations AND over every raw spelling that maps to the same canonical
    item - which is the point of doing this after the mapping."""
    stock=defaultdict(lambda: defaultdict(float))
    for r in open_mapped(inv_path):
        d=parse_date(r["Date"], inv_path); it=r[ITEM_COL].strip()
        if not it: continue
        stock[it][month_key(d)] += num(r["Quantity"])
    return stock

def stock_weight(stock, item, mk):
    """Return (weight, basis) with nearest-month fallback."""
    if item not in stock: return 0.0, "no_inventory"
    months=stock[item]
    if mk in months: return months[mk], "exact_month"
    # nearest: smallest |distance|; tie -> most recent prior (larger mk among equal dist)
    best=min(months.keys(), key=lambda m:(abs(m-mk), -(m<mk), -m))
    dd=best-mk
    tag=f"nearest_month({dd:+d}mo)"
    return months[best], tag

def load_groups(ag_path, mapping):
    """Group label and constituents, both canonicalised. Returns
    (groups, collapsed) where collapsed records variants that two raw
    spellings mapped onto the same canonical constituent - a legitimate
    merge, but one that changes the split, so it gets reported."""
    groups=defaultdict(list); collapsed=[]
    for r in csv.DictReader(open(ag_path,encoding="utf-8")):
        lab=canon(mapping, r["generic_sales_name"], ag_path)
        var=canon(mapping, r["inventory_variant"], ag_path)
        if var==lab:
            raise SystemExit(f"{ag_path}: {r['inventory_variant']!r} canonicalises to "
                             f"{var!r}, the same as its own group label - a group cannot "
                             f"contain itself. Fix the mapping or the group file.")
        if var in groups[lab]:
            collapsed.append((lab, r["inventory_variant"].strip(), var))
            continue
        groups[lab].append(var)
    return groups, collapsed

def run(sales_path, inv_path, ag_path, map_path, out_path, audit_path):
    groups,collapsed=load_groups(ag_path, load_mapping(map_path))
    stock=build_stock(inv_path)
    out=[]; audit=[]
    n_direct=n_grouped=n_alloc=0
    units_in=units_out=0.0
    basis_tally=defaultdict(int)
    grouped_units=0.0; groups_seen=defaultdict(float)

    for r in open_mapped(sales_path):
        d=parse_date(r["Date"], sales_path); item=r[ITEM_COL].strip()
        supplier=r.get("Supplier","").strip()
        Q=int(round(num(r["Total Quantity"])))
        iso=d.strftime("%Y-%m-%d")
        units_in+=Q

        if item not in groups:
            out.append([iso,item,Q,supplier,0,"1.0"]); n_direct+=1; units_out+=Q
            continue

        # ---- price-grouped row: allocate ----
        n_grouped+=1; grouped_units+=Q; groups_seen[item]+=Q
        mk=month_key(d)
        members=groups[item]
        wb=[stock_weight(stock,c,mk) for c in members]
        weights=[w for w,_ in wb]
        if sum(weights)>0:
            alloc=largest_remainder(Q, weights)
            grp_basis="stock_weighted"
        else:
            eq=[1.0]*len(members)
            alloc=largest_remainder(Q, eq)
            grp_basis="equal_split_no_stock"
            wb=[(0.0,"equal_split") for _ in members]
        for c,(w,b),a in zip(members, wb, alloc):
            basis_tally[b]+=1
            audit.append([iso,item,Q,c,round(w,2),b,a,supplier])
            if a>=0:
                out.append([iso,c,a,supplier,1,"0.5"]); n_alloc+=1; units_out+=a
        # reconcile per row
        assert sum(alloc)==Q, f"alloc mismatch {item} {iso}: {sum(alloc)} != {Q}"

    # sort chronologically: date, supplier, item (matches earlier sales_long ordering)
    out.sort(key=lambda x:(x[0], (x[3] or "").upper(), x[1].upper()))
    with open(out_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["Date",ITEM_COL,"Total Quantity","Supplier","imputation_flag","weight"])
        w.writerows(out)
    with open(audit_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["Date","GroupLabel","GroupQty","Constituent","StockWeight","WeightBasis","Allocated","Supplier"])
        audit.sort(key=lambda x:(x[1].upper(), x[0]))
        w.writerows(audit)

    # ---- report ----
    print(f"Group labels                 : {len(groups)}  "
          f"({sum(len(v) for v in groups.values())} canonical constituents)")
    if collapsed:
        print(f"Constituents merged by canonicalisation: {len(collapsed)}")
        for lab,raw,c in collapsed:
            print(f"   {lab[:35]:35} {raw[:35]:35} -> {c}")
    print(f"Direct (unchanged) rows      : {n_direct}")
    print(f"Price-grouped rows found     : {n_grouped}  ({int(grouped_units)} units)")
    print(f"Allocated constituent rows   : {n_alloc}")
    print(f"TOTAL output rows            : {len(out)}")
    print(f"Units in = {int(units_in)} | Units out = {int(units_out)}  (conserved: {int(units_in)==int(units_out)})")
    print("\nWeight-basis breakdown (per constituent-allocation):")
    for b,c in sorted(basis_tally.items(), key=lambda x:-x[1]): print(f"   {b:22} {c}")
    print("\nPer-group units allocated:")
    for g,u in sorted(groups_seen.items(), key=lambda x:-x[1]): print(f"   {g[:45]:45} {int(u)}")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    # Defaults are step1_apply_mapping.py's outputs and this repo's real
    # filenames - the old defaults pointed at the raw CSVs and at
    # /mnt/user-data/outputs/, a path from the environment this was first
    # written in, so a plain `python proportional_allocation.py` either
    # allocated on raw names or crashed on a missing directory.
    ap.add_argument("--sales", default="USTore_sales_long_with_zeros_mapped.csv")
    ap.add_argument("--inventory", default="USTore_inventory_excel_long_mapped.csv")
    ap.add_argument("--groups", default="allocation_groups.csv")
    ap.add_argument("--mapping", default="vocab_mapping_FINAL_v5.csv")
    ap.add_argument("-o","--output", default="USTore_sales_long_allocated.csv")
    ap.add_argument("--audit", default="allocation_audit.csv")
    a=ap.parse_args()
    run(a.sales, a.inventory, a.groups, a.mapping, a.output, a.audit)
