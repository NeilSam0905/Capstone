"""
forecasting/category.py
------------------------------------------------------------------
Apparel vs non-apparel classification, built for the pooled-by-category
ML experiment in scripts/model_benchmark_category.py (adviser's
suggestion: pool SKUs into one shared tree model per category instead of
fitting one model per SKU, and check whether that improves on the
per-SKU models in data/model_benchmark_ml_summary.csv).

Two-tier classification:

  1. Dim_Product.category, where set, is authoritative: APPAREL maps to
     "apparel"; NON-APPAREL and MAIN STORAGE map to "non-apparel". That
     column is controlled vocabulary (see CLAUDE.md) - this module only
     READS it, nothing here writes back to the DB.
  2. Where category is NULL - 190 of the 266 moving, scored SKUs -
     fall back to a keyword match on item_name. This is an ANALYSIS-ONLY
     label to test the pooling hypothesis, not a claim about the true
     catalogue taxonomy.
------------------------------------------------------------------
"""
import re
from typing import Optional

import numpy as np

__all__ = ["APPAREL", "NON_APPAREL", "classify", "speed_label",
           "fold_scoped_speed_labels", "fold_scoped_fsn_labels",
           "PRODUCT_TYPES", "classify_product_type"]

APPAREL = "apparel"
NON_APPAREL = "non-apparel"

_DB_CATEGORY_MAP = {
    "APPAREL": APPAREL,
    "NON-APPAREL": NON_APPAREL,
    "MAIN STORAGE": NON_APPAREL,
}

# Matched as a plain substring - distinctive enough not to false-positive,
# and some appear glued to another word in this catalogue, e.g.
# "Poloshirt", "UST Embro OS", so a \b...\b word match would miss them.
_APPAREL_SUBSTRINGS = [
    "shirt", "jersey", "jacket", "hoodie", "sweatshirt", "poloshirt",
    "uniform", "blouse", "costume", "embro", "oversized", "varsity",
    "dri-fit", "drifit", "taslan", "subli", "headband", "blazer",
]

# Short/generic words - require a word boundary so they only match as a
# whole word, not as a substring of something unrelated.
_APPAREL_WORDS = [
    "cap", "caps", "hat", "hats", "vest", "coat", "sash", "scarf",
    "scarfs", "scarves", "socks", "dress", "skirt", "pants", "short",
    "shorts", "robe", "gown", "polo",
]

_APPAREL_WORD_RE = re.compile(
    r"\b(" + "|".join(_APPAREL_WORDS) + r")\b", re.IGNORECASE)


def _apparel_by_keyword(item_name: Optional[str]) -> bool:
    name = (item_name or "").lower()
    if any(s in name for s in _APPAREL_SUBSTRINGS):
        return True
    return bool(_APPAREL_WORD_RE.search(name))


def classify(item_name: str, db_category: Optional[str]) -> str:
    """apparel / non-apparel for one SKU.

    db_category is Dim_Product.category (may be None/NaN); item_name is
    Dim_Product.item_name. db_category wins whenever it is set.
    """
    if db_category in _DB_CATEGORY_MAP:
        return _DB_CATEGORY_MAP[db_category]
    return APPAREL if _apparel_by_keyword(item_name) else NON_APPAREL


# CORRECTION (see docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's correction
# section): Dim_Product.fsn_class is NOT "the store's own tag" and is NOT
# controlled vocabulary - it is computed by scripts/step3_fsn_classification.py
# from an unfiltered SELECT over the whole Fact_Sales table (every calendar
# day, no date bound) and an 80th-percentile cross-sectional cutoff, then
# written back into Dim_Product. Using it (via speed_label below) to choose
# a walk-forward fold's pooling group is a leak: an early fold's group
# assignment is partly determined by sales that happen after that fold's
# own origin. Measured directly - recomputing the label from only the data
# available at the first fold's origin flips 54 of 266 SKUs (20%) relative
# to this full-history value. speed_label/_SPEED_MAP below are kept for
# code that genuinely wants the deployed, current classification (e.g.
# step5_prescriptive.py's real ROP/safety-stock run, which is not a
# backtest and has no "future" to leak from). For anything scored on
# walk-forward folds, use fold_scoped_speed_labels instead - see its
# docstring.
_SPEED_MAP = {"F": "fast", "S": "slow", "N": "nonmoving"}


def speed_label(fsn_class: Optional[str]) -> str:
    """'fast' / 'slow' / 'nonmoving' for Dim_Product.fsn_class, or
    'unknown' for anything else (missing, or a value outside F/S/N).

    This is the CURRENT, full-history classification - correct for a real
    (non-backtest) use like step5_prescriptive.py, LEAKAGE if used to
    choose a walk-forward fold's pooling group or safety-stock class. See
    fold_scoped_speed_labels for the leak-free version."""
    return _SPEED_MAP.get(fsn_class, "unknown")


def fold_scoped_speed_labels(series, train_end: int, real_offset: int = 0,
                             threshold: float = 80.0):
    """sku -> 'fast'/'slow', computed from ONLY series[sku][real_offset:train_end]
    for every sku - the leak-free replacement for speed_label(fsn_class) inside
    a walk-forward fold. Approximates step3_fsn_classification.py's method
    (ADUS = units sold per day-with-a-sale, ranked cross-sectionally, split
    at the 80th percentile) scoped to one fold's training window instead of
    the whole series.

    Deliberately NOT a byte-for-byte port of step3: that script additionally
    weights by imputation_flag and excludes is_censored days, using raw
    Fact_Sales rows this function never sees (it works from the daily-
    aggregated arrays scripts/model_benchmark.py already builds). For
    choosing which SKUs get POOLED together, that is a second-order
    difference - the two things that matter for a pooling decision (which
    SKUs sell often, which barely sell at all) are exactly what ADUS-per-
    sale-day and an 80th-percentile split capture. This function is not a
    replacement for step3's committed fsn_class column, which is the
    deployed classification USTore's prescriptive layer actually uses.

    A SKU with zero sales in the training window gets ADUS 0, which sorts
    to "slow" - there is no "non-moving" bucket here, matching how
    forecasting/category.py's callers only ever pool F/S SKUs anyway.
    """
    adus = {}
    for sku, values in series.items():
        train = np.asarray(values, dtype=float)[real_offset:train_end]
        sale_days = int(np.count_nonzero(train > 0))
        adus[sku] = float(train.sum()) / sale_days if sale_days > 0 else 0.0

    cutoff = np.quantile(np.fromiter(adus.values(), dtype=float), threshold / 100.0)
    return {sku: ("fast" if a >= cutoff else "slow") for sku, a in adus.items()}


def fold_scoped_fsn_labels(series, train_end: int, real_offset: int = 0,
                           threshold: float = 80.0):
    """sku -> 'fast'/'slow'/'nonmoving', computed from ONLY
    series[sku][real_offset:train_end] for every sku.

    Unlike fold_scoped_speed_labels above, this keeps a real non-moving
    bucket rather than folding zero-sale SKUs into "slow" - matching how
    scripts/step3_fsn_classification.py actually classifies the committed
    fsn_class column: the 80th-percentile ADUS split is computed over the
    MOVING population only (SKUs with at least one sale in the window),
    and every SKU with zero sales in the window is 'nonmoving' outright,
    never ranked against the movers. Still not a byte-for-byte port (same
    caveats as fold_scoped_speed_labels: no imputation_flag weighting, no
    is_censored exclusion - this works from the daily-aggregated arrays
    already in hand), but the bucket boundaries follow the real
    methodology instead of approximating it away.

    Exists for scripts/category_split_prophet.py's category-splitting
    test: an item with zero sales in a fold's training window is exactly
    the kind of SKU worth pooling with other quiet SKUs into its own
    aggregate rather than diluting - or being diluted by - the fast
    movers, and merging it into "slow" (as fold_scoped_speed_labels does,
    deliberately, for its own per-SKU pooling use case) would erase that
    distinction here.

    If every SKU in the window is non-moving (a degenerate window), there
    is nothing to rank and everyone stays 'nonmoving'.
    """
    movers = {}
    for sku, values in series.items():
        train = np.asarray(values, dtype=float)[real_offset:train_end]
        sale_days = int(np.count_nonzero(train > 0))
        if sale_days > 0:
            movers[sku] = float(train.sum()) / sale_days

    if not movers:
        return {sku: "nonmoving" for sku in series}

    cutoff = np.quantile(np.fromiter(movers.values(), dtype=float), threshold / 100.0)
    return {
        sku: ("nonmoving" if sku not in movers else
              "fast" if movers[sku] >= cutoff else "slow")
        for sku in series
    }


# ---- finer product-type buckets (TEMPORARY / exploratory) -----------
# apparel/non-apparel still leaves "non-apparel" as a grab-bag of mugs,
# bags, keychains, plushies... the category_speed breakdown showed that
# heterogeneity, not speed, is what pooling struggles with there. This
# splits it further by what the item actually IS, purely from item_name
# keywords - there is no Dim_Product column for this, so unlike
# `category`/`fsn_class` there is nothing authoritative to defer to here.
# It exists only to test the pooling hypothesis at finer granularity and
# is not a proposed addition to the catalogue's real taxonomy.
CLOTHES = "clothes"
DRINKWARE = "drinkware"
BAGS = "bags"
STATIONERY = "stationery"
ACCESSORIES = "accessories"
UMBRELLA = "umbrella"
TOYS_PLUSH = "toys_plush"
MISC = "misc"

PRODUCT_TYPES = (CLOTHES, DRINKWARE, BAGS, STATIONERY, ACCESSORIES,
                 UMBRELLA, TOYS_PLUSH, MISC)

# Checked in this order - first match wins - so a name that could fit two
# buckets lands in the more specific/earlier one (e.g. "Sci Totebag" hits
# BAGS before it would ever reach a generic catch-all).
_PRODUCT_TYPE_RULES = [
    (DRINKWARE, ["tumbler", "mug", "flask", "aquaflask"]),
    (BAGS, ["back ?pack", "laptop bag", "tote", "sling bag", "eco bag",
            "paper bag", "\\bbag\\b"]),
    (TOYS_PLUSH, ["plushie", "plush", "stuff toy", "stuffed toy",
                  "mystery car", "\\bcar\\b", "machete", "tiger costume"]),
    (UMBRELLA, ["umbrella", "\\bumb\\b", "canopy", "\\bcane\\b"]),
    (STATIONERY, ["notebook", "notelet", "bookmark", "pencil", "\\bpen\\b",
                  "ballpen", "paper weight", "paperweight"]),
    (ACCESSORIES, ["keychain", "lanyard", "sticker", "\\bpin\\b", "pins",
                   "id case", "id lace", "clicker", "clapper", "\\bclock\\b",
                   "charger", "powerbank"]),
]

_PRODUCT_TYPE_RULES = [
    (label, re.compile("|".join(pats), re.IGNORECASE))
    for label, pats in _PRODUCT_TYPE_RULES
]


def classify_product_type(item_name: str, db_category: Optional[str]) -> str:
    """One of PRODUCT_TYPES for one SKU - TEMPORARY/exploratory, keyword
    only (see module note above). CLOTHES is decided by DB category
    APPAREL or the apparel keyword match directly - NOT via classify(),
    because classify() lets a MAIN STORAGE tag force non-apparel before
    ever checking keywords, and two MAIN STORAGE items here ('Bomber
    Jacket', 'Yellow Baseball Shirt') are unambiguously clothing by name."""
    if db_category == "APPAREL" or _apparel_by_keyword(item_name):
        return CLOTHES

    name = (item_name or "")
    for label, pattern in _PRODUCT_TYPE_RULES:
        if pattern.search(name):
            return label
    return MISC
