"""
Proportional allocation of price-grouped USTore sales rows
==========================================================
Implements the manuscript's ETL step: historical tally rows that bundle several
SKUs sharing a price point are split into one row per real SKU, distributing the
grouped quantity in proportion to each SKU's beginning-of-month stock.

  allocated(s" ) = round( Q * stock(s" , M) / Sum stock(s#, M) )   [largest-remainder]

Inputs
  - sales CSV            : Date, Item, Total Quantity, Supplier   (DD/MM/YYYY)
  - inventory CSV        : Category, Date, Item, ..., Quantity    (monthly stock)
  - allocation_groups CSV: generic_sales_name  <- inventory_variant (group members)

Weight (stock) fallback ladder, per constituent:
  1. exact_month        - constituent's total stock in the sale's month M
  2. nearest_month      - nearest available month's stock (ties -> most recent prior)
  3. no_inventory       - constituent never appears in inventory -> weight 0
If every constituent resolves to 0 -> equal split across constituents.

Output (Fact_Sales-style long CSV):
  Date, Item, Total Quantity, Supplier, imputation_flag, weight
  - non-grouped rows pass through unchanged (imputation_flag=0, weight=1.0)
  - grouped rows are REPLACED by their allocated constituent rows
    (imputation_flag=1, weight=0.5); constituents allocated 0 units are dropped
Plus an audit CSV documenting every split.
"""
import csv, datetime, argparse
from collections import defaultdict

def parse_date(s):
    s=(s or "").strip()
    for f in ("%d/%m/%Y","%Y-%m-%d","%m/%d/%Y"):
        try: return datetime.datetime.strptime(s,f).date()
        except ValueError: pass
    return None

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
    """stock[item][monthkey] = total beginning-month quantity (summed over sizes/locations)."""
    stock=defaultdict(lambda: defaultdict(float))
    for r in csv.DictReader(open(inv_path,encoding="utf-8")):
        d=parse_date(r["Date"]); it=r["Item"].strip()
        if d is None or not it: continue
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

def load_groups(ag_path):
    groups=defaultdict(list)
    for r in csv.DictReader(open(ag_path,encoding="utf-8")):
        groups[r["generic_sales_name"].strip()].append(r["inventory_variant"].strip())
    return groups

def run(sales_path, inv_path, ag_path, out_path, audit_path):
    groups=load_groups(ag_path)
    stock=build_stock(inv_path)
    out=[]; audit=[]
    n_direct=n_grouped=n_alloc=0
    units_in=units_out=0.0
    basis_tally=defaultdict(int)
    grouped_units=0.0; groups_seen=defaultdict(float)

    for r in csv.DictReader(open(sales_path,encoding="utf-8")):
        d=parse_date(r["Date"]); item=r["Item"].strip()
        supplier=r.get("Supplier","").strip()
        Q=int(round(num(r["Total Quantity"])))
        iso=d.strftime("%Y-%m-%d") if d else (r["Date"] or "").strip()
        units_in+=Q

        if item not in groups:
            out.append([iso,item,Q,supplier,0,"1.0"]); n_direct+=1; units_out+=Q
            continue

        # ---- price-grouped row: allocate ----
        n_grouped+=1; grouped_units+=Q; groups_seen[item]+=Q
        mk=month_key(d) if d else None
        members=groups[item]
        wb=[stock_weight(stock,c,mk) if mk is not None else (0.0,"no_date") for c in members]
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
            if a>0:
                out.append([iso,c,a,supplier,1,"0.5"]); n_alloc+=1; units_out+=a
        # reconcile per row
        assert sum(alloc)==Q, f"alloc mismatch {item} {iso}: {sum(alloc)} != {Q}"

    # sort chronologically: date, supplier, item (matches earlier sales_long ordering)
    out.sort(key=lambda x:(x[0], (x[3] or "").upper(), x[1].upper()))
    with open(out_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["Date","Item","Total Quantity","Supplier","imputation_flag","weight"])
        w.writerows(out)
    with open(audit_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["Date","GroupLabel","GroupQty","Constituent","StockWeight","WeightBasis","Allocated","Supplier"])
        audit.sort(key=lambda x:(x[1].upper(), x[0]))
        w.writerows(audit)

    # ---- report ----
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
    ap.add_argument("--sales", default="USTore_sales_long_May_Aug2024-May2026.csv")
    ap.add_argument("--inventory", default="USTore_inventory_excel_long.csv")
    ap.add_argument("--groups", default="allocation_groups.csv")
    ap.add_argument("-o","--output", default="/mnt/user-data/outputs/USTore_sales_long_allocated.csv")
    ap.add_argument("--audit", default="/mnt/user-data/outputs/allocation_audit.csv")
    a=ap.parse_args()
    run(a.sales, a.inventory, a.groups, a.output, a.audit)
