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

__all__ = ["APPAREL", "NON_APPAREL", "classify", "speed_label",
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


# Dim_Product.fsn_class is the store's own fast/slow-mover tag (F/S, and N
# for non-moving where it appears elsewhere in the catalogue) - controlled
# vocabulary, read-only here, same as `category`. Not derived or guessed:
# unlike apparel/non-apparel there is no fallback because every moving SKU
# already carries an fsn_class value.
_SPEED_MAP = {"F": "fast", "S": "slow", "N": "nonmoving"}


def speed_label(fsn_class: Optional[str]) -> str:
    """'fast' / 'slow' / 'nonmoving' for Dim_Product.fsn_class, or
    'unknown' for anything else (missing, or a value outside F/S/N)."""
    return _SPEED_MAP.get(fsn_class, "unknown")


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
