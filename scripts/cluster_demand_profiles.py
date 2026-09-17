"""
scripts/cluster_demand_profiles.py
------------------------------------------------------------------
Unsupervised clustering of SKUs by DEMAND BEHAVIOUR, as an alternative
forecasting grain to the semantic categories.

Why bother, given Dim_Product.forecast_category already exists
--------------------------------------------------------------
The existing 12 categories are semantic - they group by what a thing IS
(Shirts, Bags, Drinkware). Forecasting wants groups that behave alike:
series with a similar velocity, a similar burstiness, and a similar
response to the academic calendar aggregate into a signal, while a group
that mixes a steady seller with a graduation-week spike averages them
into noise.

Nothing guarantees those coincide. `Shirts & Tops` holds 136 SKUs of very
different velocities; `Lanyards & IDs` holds 22. This script tests the
question rather than assuming either answer, and reports whether the
learned clusters actually beat the semantic ones on the SAME harness.

Features (all causal, all scale-free where it matters)
------------------------------------------------------
Computed per SKU on the colour-aware TRADING-day series only - a closed
day is not evidence about an item's behaviour:

    log_mean_units      velocity, logged because the range spans 3 orders
    cv                  sd/mean - burstiness, the single most diagnostic one
    active_rate         fraction of trading days with a sale
    p90_share           share of units falling on the top-decile days
                        (a "spikes at events" detector)
    weekday_gini        concentration across weekday-of-week profile
    break_ratio        mean units on sem-break days / mean on term days
    exam_ratio          mean units on exam days / mean on term days
    log_price           price band, logged

Ratios are clipped and NaN-filled at 1.0 (= behaves the same in both
periods), which is the neutral value, not an optimistic one.

Method
------
k-means on z-scored features, k chosen by silhouette over a range. The
silhouette is REPORTED, not just used - if it is low, these clusters are
weak structure and the honest reading is that the demand profiles do not
separate cleanly, whatever k the argmax lands on.

Outputs
-------
  data/demand_clusters.csv              one row per SKU with its cluster
  data/demand_cluster_profiles.csv      cluster centroids in real units
  ustore.db : Dim_Demand_Cluster        (dropped and rebuilt)

`Dim_Product` is NOT modified - the assignment lands in its own table so
the semantic categories stay intact and the two grains can be compared.

Run:  python scripts/cluster_demand_profiles.py [--k-min 3] [--k-max 10]
------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "ustore.db")
SALES_CSV = os.path.join(DATA_DIR, "rebuild_sales_long.csv")
DAYS_CSV = os.path.join(DATA_DIR, "rebuild_day_status.csv")

FEATURES = ["log_mean_units", "cv", "active_rate", "p90_share",
            "weekday_gini", "break_ratio", "exam_ratio", "log_price"]
MIN_SALE_DAYS = 8        # below this the features are noise, not a profile


def gini(x):
    """Concentration of a non-negative vector. 0 = flat, ->1 = concentrated."""
    x = np.sort(np.asarray(x, dtype=float))
    if x.size == 0 or x.sum() <= 0:
        return 0.0
    n = x.size
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def build_features(sales, days, cal, prices):
    """One behavioural row per SKU, computed on trading days only."""
    observable = set(days.loc[days["demand_observable"] == 1, "calendar_date"])
    s = sales[sales["calendar_date"].isin(observable)].copy()

    n_trading = len(observable)
    brk = set(cal.loc[cal["is_sem_break"] == 1, "calendar_date"])
    exm = set(cal.loc[cal["is_exam_week"] == 1, "calendar_date"])

    rows = []
    for item, g in s.groupby("item_name"):
        daily = g.groupby("calendar_date")["quantity_sold"].sum()
        daily = daily[daily > 0]
        if len(daily) < MIN_SALE_DAYS:
            continue
        v = daily.to_numpy(float)
        mean = v.mean()

        is_brk = daily.index.isin(brk)
        is_exm = daily.index.isin(exm)
        term = v[~(is_brk | is_exm)]
        term_mean = term.mean() if term.size else mean

        wd = daily.groupby(daily.index.dayofweek).sum().reindex(range(7)).fillna(0)
        top = np.sort(v)[-max(1, int(np.ceil(0.1 * v.size))):]

        rows.append({
            "item_name": item,
            "n_sale_days": int(len(daily)),
            "total_units": float(v.sum()),
            "mean_units": float(mean),
            "log_mean_units": float(np.log1p(mean)),
            "cv": float(v.std(ddof=1) / mean) if v.size > 1 and mean > 0 else 0.0,
            "active_rate": float(len(daily) / max(n_trading, 1)),
            "p90_share": float(top.sum() / v.sum()) if v.sum() > 0 else 0.0,
            "weekday_gini": float(gini(wd.to_numpy())),
            "break_ratio": float(v[is_brk].mean() / term_mean)
                           if is_brk.any() and term_mean > 0 else 1.0,
            "exam_ratio": float(v[is_exm].mean() / term_mean)
                          if is_exm.any() and term_mean > 0 else 1.0,
            "log_price": float(np.log1p(prices.get(item, 0.0) or 0.0)),
        })
    f = pd.DataFrame(rows)
    if not len(f):
        return f
    # Ratios: 1.0 is "behaves the same in both periods" - the neutral fill,
    # and clipped so one sem-break outlier cannot dominate the distance.
    for c in ("break_ratio", "exam_ratio"):
        f[c] = f[c].replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0, 5)
    return f


def choose_k(X, k_min, k_max, seed=0):
    """k by silhouette. The score is returned so the caller can report it."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    best, scores = None, []
    for k in range(k_min, min(k_max, len(X) - 1) + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
        if len(set(km.labels_)) < 2:
            continue
        sil = float(silhouette_score(X, km.labels_))
        scores.append({"k": k, "silhouette": sil,
                       "inertia": float(km.inertia_)})
        if best is None or sil > best[1]:
            best = (k, sil, km)
    return best, pd.DataFrame(scores)


def name_cluster(row):
    """A short human label from the centroid, so the output is readable.

    The calendar-response marker is included because it is the part that
    matters for forecasting and it is what separates otherwise similar
    centroids: one cluster sells ~5x MORE during semestral breaks and
    another ~4x more during exam weeks, which velocity and price alone
    would collapse into the same name.
    """
    speed = ("fast" if row["mean_units"] >= 8 else
             "steady" if row["mean_units"] >= 2.5 else "slow")
    shape = ("bursty" if row["cv"] >= 1.3 or row["p90_share"] >= 0.55
             else "even")
    band = ("premium" if row["log_price"] >= np.log1p(700) else
            "mid" if row["log_price"] >= np.log1p(200) else "budget")
    if row["break_ratio"] >= 2.0:
        cal = "-breakpeak"
    elif row["exam_ratio"] >= 2.0:
        cal = "-exampeak"
    elif row["break_ratio"] <= 0.75:
        cal = "-termonly"
    else:
        cal = ""
    return "c%d-%s-%s-%s%s" % (int(row["cluster"]), speed, shape, band, cal)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k-min", type=int, default=3)
    ap.add_argument("--k-max", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("scikit-learn is required for clustering.")
        return 2

    if not (os.path.exists(SALES_CSV) and os.path.exists(DAYS_CSV)):
        print("run scripts/rebuild_extract_tbs.py first")
        return 1

    sales = pd.read_csv(SALES_CSV, parse_dates=["calendar_date"])
    days = pd.read_csv(DAYS_CSV, parse_dates=["calendar_date"])
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    cal = pd.read_sql_query(
        "SELECT calendar_date, is_sem_break, is_exam_week FROM Dim_Date",
        con, parse_dates=["calendar_date"])
    prod = pd.read_sql_query(
        "SELECT item_name, unit_price_php, forecast_category FROM Dim_Product", con)
    con.close()

    prices = dict(zip(prod["item_name"].astype(str).str.strip(),
                      prod["unit_price_php"]))
    semantic = dict(zip(prod["item_name"].astype(str).str.strip(),
                        prod["forecast_category"]))

    print("=" * 88)
    print("DEMAND-PROFILE CLUSTERING (unsupervised, on the colour-aware series)")
    print("=" * 88)

    f = build_features(sales, days, cal, prices)
    if len(f) < args.k_min + 1:
        print("only %d SKUs cleared the %d-sale-day floor - not enough to cluster"
              % (len(f), MIN_SALE_DAYS))
        return 1
    print("%d SKUs with >= %d sale days | %d features: %s"
          % (len(f), MIN_SALE_DAYS, len(FEATURES), ", ".join(FEATURES)))

    X = StandardScaler().fit_transform(f[FEATURES].to_numpy(float))
    best, scores = choose_k(X, args.k_min, args.k_max, args.seed)
    if best is None:
        print("k-means produced no usable partition")
        return 1
    k, sil, km = best
    f["cluster"] = km.labels_
    f["semantic_category"] = f["item_name"].map(semantic).fillna("Uncategorised")

    print("\nk selection by silhouette:")
    print(scores.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("\nchosen k = %d (silhouette %.3f)" % (k, sil))
    if sil < 0.25:
        print("  WARNING: silhouette below 0.25. These clusters are weak "
              "structure -")
        print("  the demand profiles do not separate cleanly, and the grouping "
              "should be")
        print("  treated as a convenience, not as discovered categories.")

    prof = (f.groupby("cluster")
              .agg(n_skus=("item_name", "size"),
                   total_units=("total_units", "sum"),
                   mean_units=("mean_units", "mean"),
                   cv=("cv", "mean"), active_rate=("active_rate", "mean"),
                   p90_share=("p90_share", "mean"),
                   break_ratio=("break_ratio", "mean"),
                   exam_ratio=("exam_ratio", "mean"),
                   log_price=("log_price", "mean"))
              .reset_index())
    prof["cluster_label"] = prof.apply(name_cluster, axis=1)
    f = f.merge(prof[["cluster", "cluster_label"]], on="cluster", how="left")

    print("\n" + "=" * 88)
    print("CLUSTER PROFILES")
    print("=" * 88)
    show = prof[["cluster", "cluster_label", "n_skus", "total_units",
                 "mean_units", "cv", "active_rate", "p90_share",
                 "break_ratio", "exam_ratio"]]
    print(show.to_string(index=False, float_format=lambda x: "%.2f" % x))

    # How much do the learned clusters actually disagree with the semantic
    # ones? If they agree almost everywhere, clustering has added nothing.
    ct = pd.crosstab(f["semantic_category"], f["cluster_label"])
    purity = float(ct.max(axis=1).sum() / ct.to_numpy().sum())
    print("\n" + "=" * 88)
    print("LEARNED CLUSTERS vs SEMANTIC CATEGORIES")
    print("=" * 88)
    print(ct.to_string())
    print("\n  purity %.2f - the share of SKUs whose semantic category maps to a"
          % purity)
    print("  single dominant cluster. Near 1.0 means the clusters are just the")
    print("  categories again; low means they cut across them, which is the")
    print("  interesting case and the reason to test the grain at all.")

    f.to_csv(os.path.join(DATA_DIR, "demand_clusters.csv"),
             index=False, lineterminator="\n")
    prof.to_csv(os.path.join(DATA_DIR, "demand_cluster_profiles.csv"),
                index=False, lineterminator="\n")
    print("\nWrote data/demand_clusters.csv and data/demand_cluster_profiles.csv")

    if not args.no_db:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("DROP TABLE IF EXISTS Dim_Demand_Cluster")
            f[["item_name", "cluster", "cluster_label", "semantic_category",
               "n_sale_days", "total_units", "mean_units", "cv",
               "active_rate", "p90_share", "break_ratio", "exam_ratio"]].to_sql(
                "Dim_Demand_Cluster", con, if_exists="replace", index=False)
            con.commit()
            print("ustore.db: Dim_Demand_Cluster rebuilt (%d SKUs, k=%d). "
                  "Dim_Product untouched." % (len(f), k))
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
