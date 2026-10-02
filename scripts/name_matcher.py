"""
name_matcher.py - suggest which existing item a new tally-sheet name is.

The store renames rows on its sheets (about 50 on the July 2026 sheet alone:
"Cotton Brush Jacket" became "UST JACKET COTTON BRUSH W/ PRINT"). A name the
vocabulary does not know used to stop the pipeline; step1 now loads it as a
provisional item and lists it for review in the Tally Interface, and this
module supplies the suggestion shown next to it.

It only ever SUGGESTS. Nothing here writes the vocabulary: a person confirms
each name in the review list, and that confirmation is what is written.

A candidate must come from the same supplier (a name with no supplier, as
in an import file, is compared with every item but never gets more than a
weak suggestion). It is scored on:

  - shared words, weighted so a rare word ("taslan") counts for more than a
    common one ("shirt"), measured both ways: how much of the old name is in
    the new one (renames mostly add words) and how much of the new name is in
    the old one;
  - the sheets' ITEM PRICE, when both are known, each in its latest month;
  - whether the old name stopped where the new one started: a rename replaces
    a row, so the old name is absent from the new name's months and was on
    the sheet just before them.

Pairs are settled one-to-one, best score first, so two new names can never
both be pointed at one old row: without that, three new jacket rows all
pointed at the old row called "Jacket". A suggestion is "strong" when it is
clearly ahead of every remaining alternative, the old name was replaced, and
neither the sheet price nor a price in the name ("Keychain @160" against
"Keychain @180") differs. Everything else is "weak" (shown, not pre-selected)
or has no suggestion at all.

Measured on the 53 names first seen on the July 2026 sheet, with the 50
vocabulary rows Pass 6 added for them taken out: 40 strong suggestions, 39
of them the item the group chose and the other ("SM Tiger Plushie Big V2"
-> "SM Tiger Plushie V2") a pair already on the open merge-candidate list;
12 weak, 9 of them the right item; 1 with no suggestion, correctly (a new
4XL-5XL size). Replayed over every month since August 2024, most strong
suggestions that disagree with the vocabulary are pairs on that same list
(data/vocab_merge_candidates_2026-10.csv) - which is why a person confirms
every one.
"""
import math
import re
from collections import Counter

# Words that say nothing about which item a row is. "w/ print" is how the
# July 2026 sheet marks every printed item; "ust" prefixes most new names.
NOISE = {"ust", "print", "with", "the", "of", "and", "new", "design", "designs", "assorted", "all"}

PRICE_RE = re.compile(r"@\s*(\d+(?:\.\d+)?)")

MIN_SCORE = 0.45      # below this a candidate is not worth showing at all
STRONG_SCORE = 0.6    # a strong suggestion needs at least this ...
STRONG_MARGIN = 0.15  # ... and this much lead over the runner-up
RECENT_MONTHS = 2     # "was on the sheet just before": within this many months


def name_key(name):
    """How two spellings of one name compare when looked up in the
    vocabulary: capitals and runs of spaces ignored. No two names in the
    vocabulary that differ only this way map to different items (checked
    2026-10-02), so it never changes which item a known name is - it saves
    someone retyping a sheet name exactly, capitals and all."""
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def price_in_name(name):
    m = PRICE_RE.search(name or "")
    return float(m.group(1)) if m else None


def _stem(tok):
    if len(tok) > 4 and tok.endswith("ves"):
        return tok[:-3] + "f"          # scarves -> scarf
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]                # shirts -> shirt, shorts -> short
    return tok


def tokens(name):
    """Words of a name, lower-cased and singular, without prices or noise."""
    s = PRICE_RE.sub(" ", (name or "").lower())
    s = re.sub(r"\bw/\s*", " with ", s)
    words = [_stem(t) for t in re.split(r"[^a-z0-9]+", s) if t]
    return [t for t in words if t not in NOISE]


def _joined(words):
    """Adjacent pairs run together, so "polo shirt" can meet "poloshirt"."""
    return {a + b for a, b in zip(words, words[1:])}


def _found(tok, words, joined):
    if tok in words or tok in joined:
        return True
    # one is an abbreviation of the other: "corp" / "corporate"
    return any((len(tok) >= 4 and w.startswith(tok)) or (len(w) >= 4 and tok.startswith(w))
               for w in words)


def _coverage(src, dst, weight):
    """Weighted share of src's words found in dst."""
    if not src:
        return 0.0
    dst_set, joined = set(dst), _joined(dst)
    total = sum(weight(t) for t in src)
    hit = sum(weight(t) for t in src if _found(t, dst_set, joined))
    return hit / total if total else 0.0


def _month_index(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def replaced(old_months, new_months):
    """True when the old name is absent from every month the new name is on,
    and was on the sheet within RECENT_MONTHS before the new name's first."""
    if not old_months or not new_months:
        return False
    if set(old_months) & set(new_months):
        return False
    first_new = min(_month_index(m) for m in new_months)
    before = [_month_index(m) for m in old_months if _month_index(m) < first_new]
    return bool(before) and first_new - max(before) <= RECENT_MONTHS


def score(new, old, weight):
    """0..1. Words decide most of it; a matching price and a clean hand-over
    (old name gone where the new one starts) add to it."""
    nt, ot = tokens(new["name"]), tokens(old["name"])
    words = 0.7 * _coverage(ot, nt, weight) + 0.3 * _coverage(nt, ot, weight)
    s = 0.75 * words
    np_, op = new.get("price"), old.get("price")
    if np_ is not None and op is not None and abs(np_ - op) < 0.005:
        s += 0.15
    if replaced(old.get("months") or (), new.get("months") or ()):
        s += 0.10
    return s


def suggest(new_items, existing_items):
    """For each new name, the existing item it most likely is.

    new_items / existing_items: dicts with
        name      str
        supplier  normalised supplier name, or None
        price     float or None
        months    iterable of 'YYYY-MM' the name has rows in

    Returns {new name: {"item", "strength", "score", "why"}}, where strength
    is "strong", "weak" or None (item None too)."""
    docs = Counter()
    for it in [*new_items, *existing_items]:
        docs.update(set(tokens(it["name"])))
    n_docs = max(len(new_items) + len(existing_items), 1)

    def weight(tok):
        return math.log((n_docs + 1) / (docs.get(tok, 0) + 1)) + 1.0

    by_supplier = {}
    for old in existing_items:
        if old.get("supplier"):
            by_supplier.setdefault(old["supplier"], []).append(old)

    scores = {}            # (new name, old name) -> score
    olds = {}
    for new in new_items:
        # A name with no supplier (a row typed into an import file) is
        # compared with every item, and can only ever be a weak suggestion.
        pool = by_supplier.get(new["supplier"], []) if new.get("supplier") else existing_items
        for old in pool:
            if old["name"] != new["name"]:
                scores[(new["name"], old["name"])] = score(new, old, weight)
                olds[old["name"]] = old

    # One-to-one: the best-scoring pair is settled first, then the best of what
    # is left, so two new names can never both be pointed at one old row.
    paired, taken = {}, set()
    for (n, o), s in sorted(scores.items(), key=lambda kv: -kv[1]):
        if s < MIN_SCORE:
            break
        if n not in paired and o not in taken:
            paired[n] = o
            taken.add(o)

    out = {}
    for new in new_items:
        n = new["name"]
        mine = sorted(((s, o) for (nn, o), s in scores.items() if nn == n), reverse=True)
        if not mine or mine[0][0] < MIN_SCORE:
            out[n] = {"item": None, "strength": None,
                      "score": round(mine[0][0], 3) if mine else None,
                      "why": "no similar item from the same supplier"
                             if new.get("supplier") else "no similar item"}
            continue

        o = paired.get(n)
        if o is None:
            s, o = mine[0]
            rival = next(nn for (nn, oo) in scores if oo == o and paired.get(nn) == o)
            out[n] = {"item": o, "strength": "weak", "score": round(s, 3),
                      "why": f"'{rival}' matches '{o}' better"}
            continue

        s = scores[(n, o)]
        old = olds[o]
        # Closest competitor on either side that is still free: another old
        # row this name could be, or another new name that could be this row.
        others = [ss for ss, oo in mine if oo != o and oo not in taken]
        rivals = [ss for (nn, oo), ss in scores.items() if oo == o and nn != n and nn not in paired]
        runner_up = max(others + rivals, default=0.0)
        handover = replaced(old.get("months") or (), new.get("months") or ())
        price_conflict = (new.get("price") is not None and old.get("price") is not None
                          and abs(new["price"] - old["price"]) >= 0.005)
        # Price-suffixed rows ("Keychain @160", "Keychain @180") are separate
        # products throughout the vocabulary; a different suffix is a new SKU,
        # or at least a person's call.
        np_, op = price_in_name(n), price_in_name(o)
        suffix_conflict = np_ is not None and op is not None and np_ != op
        strong = (bool(new.get("supplier")) and s >= STRONG_SCORE and s - runner_up >= STRONG_MARGIN
                  and handover and not price_conflict and not suffix_conflict)

        why = ["same supplier"] if new.get("supplier") else ["supplier unknown"]
        if price_conflict:
            why.append(f"price differs ({old['price']:g} -> {new['price']:g})")
        elif new.get("price") is not None and old.get("price") is not None:
            why.append("same price")
        if suffix_conflict:
            why.append(f"different price in the name (@{op:g} -> @{np_:g})")
        why.append("old name stopped when this one started" if handover
                   else "old name still on the sheet"
                   if set(old.get("months") or ()) & set(new.get("months") or ())
                   else "no clean hand-over")
        if s - runner_up < STRONG_MARGIN:
            why.append("another item is almost as close")
        out[n] = {"item": o, "strength": "strong" if strong else "weak",
                  "score": round(s, 3), "why": "; ".join(why)}
    return out
