"""
forecasting/clustering.py
------------------------------------------------------------------
Data-driven grouping for the pooled-model experiment
(scripts/model_benchmark_category.py), as an alternative to the manual
apparel/non-apparel/fast-slow/product-type rules in
forecasting/category.py. Adviser's suggestion: cluster the catalogue
instead of hand-labelling it, then pool models within each cluster -
same pooling machinery, a different way of deciding who gets pooled
with whom.

Five features per SKU, from real sales only - nothing here reads
Dim_Product.category or fsn_class, so this is independent of (and
comparable against) the category_speed grouping already tested:

    density    share of days with any sale - the intermittency axis
    mean_nz    average sale size on days that DID sell - size tier
    cv_nz      std/mean of nonzero sales - volatility, independent of size
    log_total  log1p(total units sold) - overall importance/volume
    log_price  log(unit price) - value tier, from Dim_Product.unit_price_php

K-means on z-scored features. K is a free parameter (--k on the
benchmark script); this module exposes an elbow/silhouette helper so a
K can be chosen with evidence rather than picked to match a preferred
group count.
"""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

__all__ = ["sku_features", "cluster_skus", "choose_k"]

FEATURE_COLS = ["density", "mean_nz", "cv_nz", "log_total", "log_price"]


def sku_features(series, prices):
    """One row per SKU. `series`: dict sku -> daily array (moving SKUs,
    as returned by model_benchmark.load_daily_series). `prices`: dict
    sku -> unit_price_php (may be missing/None per SKU - median-imputed,
    flagged in a `price_imputed` column so it's auditable)."""
    rows = []
    for sku, v in series.items():
        v = np.asarray(v, dtype=float)
        nz = v[v > 0]
        density = float(np.mean(v > 0))
        mean_nz = float(nz.mean()) if nz.size else 0.0
        cv_nz = float(nz.std() / mean_nz) if nz.size > 1 and mean_nz > 0 else 0.0
        total = float(v.sum())
        rows.append({"sku": sku, "density": density, "mean_nz": mean_nz,
                     "cv_nz": cv_nz, "log_total": np.log1p(total),
                     "price": prices.get(sku)})
    df = pd.DataFrame(rows)
    df["price_imputed"] = df["price"].isna()
    df["price"] = df["price"].fillna(df["price"].median())
    df["log_price"] = np.log(df["price"])
    return df


def choose_k(df, k_range=range(2, 9), seed=0):
    """Inertia (elbow) and silhouette score per K, to justify a K instead
    of asserting one. Returns a DataFrame - inspect it, don't just take
    argmax(silhouette): the elbow and the silhouette peak often disagree
    on data this lumpy, and that disagreement is itself worth reporting."""
    from sklearn.metrics import silhouette_score

    X = StandardScaler().fit_transform(df[FEATURE_COLS])
    rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
        sil = silhouette_score(X, km.labels_) if k > 1 else float("nan")
        rows.append({"k": k, "inertia": km.inertia_, "silhouette": sil})
    return pd.DataFrame(rows)


def cluster_skus(df, k, seed=0):
    """sku -> f"cluster{n}" for n in [0, k). Also returns the fitted
    KMeans and the scaler, so a caller can inspect cluster centers in
    original feature units (scaler.inverse_transform(km.cluster_centers_))."""
    X = StandardScaler()
    Xs = X.fit_transform(df[FEATURE_COLS])
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(Xs)
    labels = {row.sku: f"cluster{lab}" for row, lab in zip(df.itertuples(), km.labels_)}
    return labels, km, X
