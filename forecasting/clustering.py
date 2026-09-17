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
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

__all__ = ["sku_features", "cluster_skus", "choose_k", "match_clusters_to_reference"]

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


def match_clusters_to_reference(centers, reference_centers):
    """old_label -> new_label so each row of `centers` (k x len(FEATURE_COLS),
    ORIGINAL feature units) is matched one-to-one to whichever row of
    `reference_centers` (same shape) it is closest to, minimizing total
    distance (Hungarian assignment - scipy.optimize.linear_sum_assignment).

    Why this exists: K-means's own cluster numbering is ARBITRARY per fit -
    refitting on slightly different data (a different fold's window, a
    handful of changed values) can permute which integer label lands on
    which real cluster, even when the clusters themselves are nearly
    identical. Observed directly: re-running the walk-forward cluster
    grouping with a small data change (see docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's
    gap-fill sensitivity check) put 262 of 266 SKUs under 3-4 DIFFERENT
    cluster numbers across their 12 folds, versus 0 in the unmodified run -
    not because the underlying grouping changed that much, but because
    K-means relabelled it. Matching every fit's clusters back to one fixed
    reference (typically the full-history clustering) keeps "cluster0"
    meaning the same real group across folds/runs, which per-cluster
    reporting depends on. This does not change which SKUs get pooled with
    which inside any single fold - only what the resulting label is called
    - so it cannot leak anything into scoring.

    Distances are scaled by the reference's own per-feature spread first,
    so a large-magnitude feature (e.g. log_total) cannot dominate purely
    from its units. Falls back to the identity mapping if the two center
    arrays don't have the same shape (e.g. a different k)."""
    centers = np.asarray(centers, dtype=float)
    reference_centers = np.asarray(reference_centers, dtype=float)
    if centers.shape != reference_centers.shape:
        return {i: i for i in range(centers.shape[0])}
    scale = reference_centers.std(axis=0)
    scale[scale == 0] = 1.0
    d = np.linalg.norm(
        (centers[:, None, :] - reference_centers[None, :, :]) / scale, axis=2)
    row_ind, col_ind = linear_sum_assignment(d)
    return {int(r): int(c) for r, c in zip(row_ind, col_ind)}


def cluster_skus(df, k, seed=0, reference_centers=None):
    """sku -> f"cluster{n}" for n in [0, k). Also returns the fitted
    KMeans, the scaler, and the cluster centers in ORIGINAL feature units
    (k x len(FEATURE_COLS): scaler.inverse_transform(km.cluster_centers_),
    computed once here rather than re-derived by every caller).

    Pass `reference_centers` (this same original-units center array from a
    prior/reference fit - typically the full-history clustering) to hold
    cluster IDENTITY stable across independent fits: see
    match_clusters_to_reference for why raw K-means labels cannot be
    trusted to mean the same thing twice."""
    scaler = StandardScaler()
    Xs = scaler.fit_transform(df[FEATURE_COLS])
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(Xs)
    raw_centers = scaler.inverse_transform(km.cluster_centers_)

    label_map = {i: i for i in range(k)}
    if reference_centers is not None:
        label_map = match_clusters_to_reference(raw_centers, reference_centers)

    labels = {row.sku: f"cluster{label_map[lab]}"
             for row, lab in zip(df.itertuples(), km.labels_)}

    # Reordered so centers[i] is cluster i's center IN THE RETURNED LABELS -
    # a caller chaining stability fold-to-fold (see build_group_fn) can pass
    # this straight back in as the next call's reference_centers without
    # having to track label_map itself.
    centers = np.empty_like(raw_centers)
    for old_label, new_label in label_map.items():
        centers[new_label] = raw_centers[old_label]

    return labels, km, scaler, centers
