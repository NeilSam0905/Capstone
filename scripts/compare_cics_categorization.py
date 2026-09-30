"""
scripts/compare_cics_categorization.py
------------------------------------------------------------------
Would the store's own item list ("FOR CICS STUDENTS.xlsx", 18 sections under
APPAREL / NON-APPAREL) forecast better or worse than the 12 keyword categories
step1b_categorize_products.py assigns today?

Read-only: nothing in ustore.db is changed. Both groupings are scored with the
models the dashboard actually ships, on the same walk-forward folds:

  category level  step4c's RM6_6month_180d+calendar (6-month average lowered by
                  the school calendar) against "repeat last 30 days", per group.
  item level      step4's topdown_tsb+calendar on the Fast items (what the app
                  forecasts) and the Slow items (scored the same way, for this
                  comparison only) - the item forecast borrows its CATEGORY's
                  level and calendar ratios, so the grouping changes every item
                  forecast too. This is the one
                  comparison where the two schemes are scored on identical
                  targets (the same items, the same actuals), so it is the fair
                  head-to-head; category-level numbers are per group and a
                  scheme with smaller groups is harder to forecast by nature.

Mapping the 519 catalogue names onto the CICS list
--------------------------------------------------
The tally names rarely match the list's wording ("C. Keychain" is "UST College
Keychain", "Sci Totebag" is the COS Totebag). Each product is placed by
PRODUCT_OVERRIDES (checked by hand, price and sale dates used where the name is
ambiguous) or else by the first matching rule in RULES. `basis` records how:
  list   the item is on the CICS list (possibly under a different spelling)
  name   not on the list; section inferred from the name
  none   neither - left in "Uncategorized"
Written to data/cics_category_mapping.csv for review.

Outputs (data/):
  cics_category_mapping.csv      product -> current category, CICS section, basis
  cics_vs_current_category.csv   one row per group per scheme (category level)
  cics_vs_current_items.csv      one row per Fast / Slow item: both schemes' MASE
  cics_vs_current_summary.csv    headline numbers per scheme

Run:  python scripts/compare_cics_categorization.py
------------------------------------------------------------------
"""
import os
import re
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.topdown import topdown_tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
UNCAT = "Uncategorized"
CURRENT_RESIDUE = "Uncategorised"          # step1b's spelling

# The CICS sections, in list order (Extras/Packaging has no catalogue item).
CICS_SECTIONS = [
    "Shirts", "Limited-time offers", "Bottomwear", "Polo Shirts",
    "Jackets, Hoodies, Sweatshirt", "Costumes", "Jersey",
    "Bags", "Mugs & Tumblers", "Lanyards & ID Accessories", "Writing & Stationery",
    "Premium Pens", "Gift & Souvenir Items", "Accessories, Tech (Textiles & Wearables)",
    "Toys and Plushies", "Bundles & Gift Sets", "Vouchers",
    "Extras, Packaging (Biodegradable Bags)",
]
SHIRTS, LTO, BOTTOM, POLO, JACKETS, COSTUME, JERSEY = CICS_SECTIONS[:7]
BAGS, MUGS, LANYARDS, STATIONERY, PREMIUM, GIFT, ACCESS, TOYS, BUNDLES, VOUCHERS = CICS_SECTIONS[7:17]

# product_id -> (section, basis, note). Ambiguous names settled by hand.
PRODUCT_OVERRIDES = {
    52:  (ACCESS, "name", "Baller @150 - silicone 'baller' wristband"),
    241: (ACCESS, "name", "New Baller - see Baller"),
    233: (GIFT, "name", "Main Building @430 - souvenir, sold alongside Arch"),
    335: (SHIRTS, "name", "Subli. Shirt @550 - T-shirt price, not polo (@800)"),
    336: (SHIRTS, "name", "Subli. Shirt 2 colors @500 - T-shirt price"),
    167: (SHIRTS, "name", "GT 375 @250 - Growling Tigers shirt"),
    1:   (POLO, "name", "fragment of a sublimation polo description"),
    519: (POLO, "name", "fragment of a two-color polo description"),
    35:  (UNCAT, "none", "Alessandrini - no clue in name; never sold"),
    185: (UNCAT, "none", "Hernandez - no clue in name; never sold"),
    484: (SHIRTS, "list", "Varsity Lifestyle Co. UST (Y,B)"),
    343: (BUNDLES, "name", "Tiger BUNDLE @900"),
    211: (BUNDLES, "list", "Thomasian kit"),
    243: (BUNDLES, "list", "Thomasian kit"),
    346: (ACCESS, "list", "Tiger Claw (sold per pair)"),
    345: (ACCESS, "list", "Tiger Claw (sold per pair)"),
    350: (ACCESS, "list", "Paw Keychain"),
    206: (BOTTOM, "list", "UST Jersey Short"),
    93:  (BOTTOM, "name", "Black Shorts"),
    418: (LTO, "list", "USTWBT Championship T-shirt 2025"),
    121: (LTO, "list", "USTWBT Championship T-shirt 2025"),
    304: (LTO, "list", "COS Ballpen"),
    305: (LTO, "list", "COS Notebook"),
    306: (LTO, "list", "COS Totebag"),
    272: (GIFT, "list", "UST paper weight"),
    273: (GIFT, "list", "UST paper weight"),
    344: (GIFT, "name", "Tiger Brooch - pin-like souvenir"),
    183: (ACCESS, "list", "Tiger headband"),
    184: (ACCESS, "list", "Tiger headband"),
    68:  (TOYS, "list", "UST big tiger plushie"),
    69:  (TOYS, "list", "UST big tiger plushie"),
    318: (TOYS, "list", "UST small tiger plushie"),
    319: (TOYS, "list", "UST small tiger plushie"),
    294: (LANYARDS, "name", "Retractable @25 - ID reel"),
    160: (ACCESS, "list", "Embro Cap - baseball cap"),
    307: (ACCESS, "name", "Shabby Hat - a hat, but not the listed Bucket Hat"),
    41:  (SHIRTS, "name", "college shirt (Architecture)"),
    45:  (SHIRTS, "name", "Athletic shirt"),
    46:  (SHIRTS, "name", "Athletic shirt"),
    62:  (SHIRTS, "name", "Basketball shirt"),
    63:  (SHIRTS, "name", "Basketball shirt"),
    457: (SHIRTS, "name", "Volleyball athletic shirt"),
    354: (TOYS, "list", "UST tiger plushie with box"),
}

# (section, basis, regex on the lower-cased name); first match wins.
RULES = [
    (VOUCHERS, "list", r"\bvoucher"),
    (COSTUME,  "list", r"\bcost?ume\b|\bcustome\b"),
    (BUNDLES,  "list", r"\bkit\b"),
    (TOYS,     "list", r"\bcar\b|plush|stuff\s*toy"),
    (JERSEY,   "list", r"\bjersey\b"),
    (JACKETS,  "list", r"hoodie|jacket|windbreaker|breaker|sweatshirt|bomber|machete|taslan"),
    (POLO,     "list", r"\bpolo|poloshirt|subli\w*\s+(yellow|2 color)"),
    (SHIRTS,   "name", r"(architecture|cics|cthm|engineering|fine arts|pharmacy|tourism|commerce"
                       r"|college of science|ipea|absc|arts & letters)"),
    (SHIRTS,   "list", r"shirt|embro|dri-?\s*fit|dry fit|oversize|\bos\b|golden tigresses"
                       r"|happy tiger|tiger pattern|quiana|quina|go uste|\bvl ust"),
    (PREMIUM,  "list", r"insignia|blanca|picasso|w/\s*box|with box"),
    (GIFT,     "list", r"sticker|key\s*chain|clicker|\bpins?\b|\barch\b|clock|utensil|clapper"),
    (STATIONERY, "list", r"\bpens?\b|ballpen|pencil|notebook|\bnb\b|notelet|bookmark|ust-pen"),
    (BAGS,     "list", r"\bbags?\b|\btote|back\s*pack"),
    (MUGS,     "list", r"\bmug|tumbler|aquaflask|\bcup\b"),
    (LANYARDS, "list", r"lanyard|\blace\b|i\.?d\s*case"),
    (ACCESS,   "list", r"scarf|scarves|headband|\bcaps?\b|\bhat\b|trucker|umbrella|\bumb\b|\bumb\."
                       r"|canopy|power\s*bank|powerbank|blanket"),
    (ACCESS,   "name", r"\bsash\b|\bsocks?\b|charger"),
]


def cics_section(pid, name):
    if pid in PRODUCT_OVERRIDES:
        return PRODUCT_OVERRIDES[pid]
    n = str(name).lower()
    for section, basis, pat in RULES:
        if re.search(pat, n):
            return section, basis, ""
    return UNCAT, "none", ""


# ------------------------------------------------------------------ data
def load():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
           JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.transaction_type = 'sale'""",
        con, parse_dates=["calendar_date"])
    stored = pd.read_sql_query(
        "SELECT forecast_category, mase FROM Result_Category_Forecast_Metrics", con)
    # Same index as step4 / step4c: first day .. last day any item sold.
    last_sale = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last_sale, freq="D")
    day_types = load_day_types(con, idx, HORIZON)
    con.close()

    prod["current"] = prod["forecast_category"].fillna(CURRENT_RESIDUE)
    placed = [cics_section(p, n) for p, n in zip(prod.product_id, prod.item_name)]
    prod["cics"] = [s for s, _, _ in placed]
    prod["basis"] = [b for _, b, _ in placed]
    prod["note"] = [n for _, _, n in placed]

    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    units = daily.sum()
    prod["units_sold"] = prod.product_id.map(units).fillna(0.0)
    return prod, daily, idx, day_types, stored


def group_series(daily, prod, col):
    cat_of = prod.set_index("product_id")[col]
    g = daily.T.groupby(daily.columns.map(cat_of)).sum().T
    assert abs(g.to_numpy().sum() - daily.to_numpy().sum()) < 1e-6, "units lost grouping"
    return g.loc[:, g.sum() > 0]


def fold_rows(key, values, fn, name):
    folds = make_folds(values.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    ev = walk_forward_evaluate(key, values, fn, name, folds=folds)
    return ev.rows if ev.sufficient else []


def mase_of(rows):
    a = np.array([r["actual_30d"] for r in rows]); p = np.array([r["pred_30d"] for r in rows])
    s = np.array([r["naive_scale"] for r in rows]); good = np.isfinite(s) & (s > 0)
    mae = float(np.mean(np.abs(a - p)))
    return mae, (mae / s[good].mean() if good.any() else np.nan)


# --------------------------------------------------------- category level
def score_categories(wide, day_types, scheme):
    model = calendar_capped_fit_predict(rolling_mean_fit_predict(180), day_types)
    naive = rolling_mean_fit_predict(30)
    out = []
    for cat in wide.columns:
        v = wide[cat].to_numpy(float)
        m_rows, n_rows = fold_rows(cat, v, model, "m"), fold_rows(cat, v, naive, "n")
        if not m_rows:
            out.append(dict(scheme=scheme, group=cat, validated=False)); continue
        mae, mase = mase_of(m_rows)
        n_mae, _ = mase_of(n_rows)
        act = sum(r["actual_30d"] for r in m_rows)
        out.append(dict(scheme=scheme, group=cat, validated=True, n_folds=len(m_rows),
                        mean_actual_30d=act / len(m_rows), mae=mae, naive_mae=n_mae, mase=mase,
                        beats_naive=int(mae < n_mae),
                        abs_err_sum=sum(abs(r["actual_30d"] - r["pred_30d"]) for r in m_rows),
                        actual_sum=act,
                        wmape_pct=100 * sum(abs(r["actual_30d"] - r["pred_30d"]) for r in m_rows) / act
                        if act else np.nan))
    return pd.DataFrame(out)


# ------------------------------------------------------------- item level
def score_items(daily, prod, wides, day_types):
    """step4's default total: topdown_tsb, calendar-lowered with the category's ratios.

    Fast AND Slow items. step4 only forecasts the Fast ones; the Slow ones are
    scored here the same way so the grouping's effect on them is visible too."""
    fsn = prod.set_index("product_id")["fsn_class"]
    items = [p for p in prod.loc[prod.fsn_class.isin(["F", "S"]), "product_id"] if p in daily.columns]
    rows = []
    for pid in items:
        v = daily[pid].to_numpy(float)
        rec = dict(product_id=pid, fsn_class=fsn[pid])
        for scheme, (col, wide) in wides.items():
            cat = prod.set_index("product_id").at[pid, col]
            cv = wide[cat].to_numpy(float)
            fn = calendar_capped_fit_predict(topdown_tsb_fit_predict(cv), day_types, cat_values=cv)
            r = fold_rows(pid, v, fn, scheme)
            mae, mase = mase_of(r)
            rec.update({f"{scheme}_category": cat, f"{scheme}_mae": mae, f"{scheme}_mase": mase,
                        f"{scheme}_abs_err": sum(abs(x["actual_30d"] - x["pred_30d"]) for x in r),
                        f"{scheme}_bias": sum(x["pred_30d"] - x["actual_30d"] for x in r)})
            rec["n_folds"] = len(r)
            rec["actual_sum"] = sum(x["actual_30d"] for x in r)
            rec["still_selling"] = rec["actual_sum"] > 0
        rows.append(rec)
    df = pd.DataFrame(rows).merge(prod[["product_id", "item_name"]], on="product_id")
    return df


def boot_gap(a, b, draws=4000, seed=0):
    rng = np.random.default_rng(seed)
    d = np.asarray(a) - np.asarray(b)
    g = np.array([d[rng.integers(0, d.size, d.size)].mean() for _ in range(draws)])
    return d.mean(), np.percentile(g, 2.5), np.percentile(g, 97.5)


def main():
    prod, daily, idx, day_types, stored = load()
    prod[["product_id", "item_name", "fsn_class", "units_sold", "current", "cics", "basis", "note"]] \
        .sort_values(["cics", "item_name"]).to_csv(
            os.path.join(DATA, "cics_category_mapping.csv"), index=False, lineterminator="\n")

    print("=== CICS mapping of the 519 catalogue products ===")
    tab = prod.groupby("cics").agg(products=("product_id", "size"), units=("units_sold", "sum"),
                                   fast=("fsn_class", lambda s: int((s == "F").sum())),
                                   on_list=("basis", lambda s: int((s == "list").sum())),
                                   by_name=("basis", lambda s: int((s == "name").sum())))
    print(tab.reindex([c for c in CICS_SECTIONS + [UNCAT] if c in tab.index]).to_string())
    print(f"\nbasis: {prod.basis.value_counts().to_dict()}")
    print("\n=== where current categories go (units sold) ===")
    ct = prod.pivot_table(index="current", columns="cics", values="units_sold", aggfunc="sum",
                          fill_value=0)
    for cur, row in ct.iterrows():
        parts = ", ".join(f"{c} {int(u)}" for c, u in row[row > 0].sort_values(ascending=False).items())
        print(f"  {cur:22s} -> {parts}")

    wides = {"current": ("current", group_series(daily, prod, "current")),
             "cics": ("cics", group_series(daily, prod, "cics"))}

    # --- category level
    cat = pd.concat([score_categories(w, day_types, s) for s, (_, w) in wides.items()])
    # Self-check: the current scheme must reproduce step4c's stored metrics.
    chk = cat[cat.scheme == "current"].merge(stored, left_on="group", right_on="forecast_category")
    gap = (chk.mase_x - chk.mase_y).abs().max() if len(chk) else np.nan
    print(f"\nself-check vs Result_Category_Forecast_Metrics: {len(chk)} categories, "
          f"max MASE gap {gap:.2e}")
    cat.to_csv(os.path.join(DATA, "cics_vs_current_category.csv"), index=False, lineterminator="\n")

    print("\n=== CATEGORY LEVEL (6-month avg + calendar vs repeat-last-30d) ===")
    for s in ("current", "cics"):
        c = cat[cat.scheme == s].sort_values("actual_sum", ascending=False)
        print(f"\n[{s}]")
        print(c[["group", "validated", "mean_actual_30d", "mase", "wmape_pct", "beats_naive"]]
              .to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    # --- item level
    items = score_items(daily, prod, wides, day_types)
    # A renamed group with the same members (Drinkware -> Mugs & Tumblers) leaves
    # the item's forecast unchanged; flag only items whose category SERIES moved.
    cw, nw = wides["current"][1], wides["cics"][1]
    items["category_changed"] = [not np.allclose(cw[a].to_numpy(), nw[b].to_numpy())
                                 for a, b in zip(items.current_category, items.cics_category)]
    items.to_csv(os.path.join(DATA, "cics_vs_current_items.csv"), index=False, lineterminator="\n")

    # One row per (level, cohort, scheme). An item counts only if both schemes give
    # it a MASE (an item that never sold in its scoring history has no scale).
    ok = items[np.isfinite(items.current_mase) & np.isfinite(items.cics_mase)]
    cohorts = [("Fast items", ok[ok.fsn_class == "F"]),
               ("Fast items, still selling", ok[(ok.fsn_class == "F") & ok.still_selling]),
               ("Slow items", ok[ok.fsn_class == "S"]),
               ("Slow items, still selling", ok[(ok.fsn_class == "S") & ok.still_selling]),
               ("Fast + Slow items", ok)]
    summary = []
    for s in ("current", "cics"):
        c = cat[(cat.scheme == s) & cat.validated]
        summary.append(dict(level="category", cohort="Categories", scheme=s, n=len(c),
                            mean_mase=c.mase.mean(), median_mase=c.mase.median(),
                            mase_below_1=int((c.mase < 1).sum()),
                            beats_naive=int(c.beats_naive.sum()),
                            wmape_pct=100 * c.abs_err_sum.sum() / c.actual_sum.sum()))
    for label, sub in cohorts:
        m, lo, hi = boot_gap(sub.cics_mase, sub.current_mase)
        for s in ("current", "cics"):
            summary.append(dict(level="item", cohort=label, scheme=s, n=len(sub),
                                mean_mase=sub[f"{s}_mase"].mean(), median_mase=sub[f"{s}_mase"].median(),
                                mase_below_1=int((sub[f"{s}_mase"] < 1).sum()),
                                wmape_pct=100 * sub[f"{s}_abs_err"].sum() / sub.actual_sum.sum(),
                                bias_pct=100 * sub[f"{s}_bias"].sum() / sub.actual_sum.sum(),
                                cics_better=int((sub.cics_mase < sub.current_mase - 1e-12).sum()),
                                cics_worse=int((sub.cics_mase > sub.current_mase + 1e-12).sum()),
                                gap_cics_minus_current=m, gap_ci_lo=lo, gap_ci_hi=hi))
    summary = pd.DataFrame(summary)
    summary.to_csv(os.path.join(DATA, "cics_vs_current_summary.csv"), index=False, lineterminator="\n")
    print("\n=== SUMMARY ===")
    print(summary.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    changed = ok[ok.category_changed]
    print(f"\nItems whose category membership changed: {len(changed)} of {len(ok)} "
          f"(Fast {int((changed.fsn_class == 'F').sum())}, Slow {int((changed.fsn_class == 'S').sum())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
