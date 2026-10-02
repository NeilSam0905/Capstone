"""
tests/test_name_matcher.py
------------------------------------------------------------------
scripts/name_matcher.py suggests which existing item a new tally-sheet name
is. Its suggestions are shown for a person to confirm, so the property that
matters most is that a "strong" one is only given when it is unambiguous:

  - one-to-one: three new jacket rows must not all point at "Jacket";
  - same supplier, a clean hand-over (old name gone where the new starts),
    no price change in the sheet price or in the name itself.
------------------------------------------------------------------
"""
import name_matcher as nm  # conftest.py puts scripts/ on sys.path


def item(name, supplier="JYL ATHLETICA", price=None, months=("2026-06",)):
    return {"name": name, "supplier": supplier, "price": price, "months": set(months)}


def new(name, supplier="JYL ATHLETICA", price=None, months=("2026-07",)):
    return item(name, supplier, price, months)


def test_tokens_drop_prices_noise_and_plurals():
    assert nm.tokens("UST ASSORTED SHIRTS UPSIZE (5XL) W/ PRINT") == ["shirt", "upsize", "5xl"]
    assert nm.tokens("Shirts all @450 UPSIZE (5XL)") == ["shirt", "upsize", "5xl"]
    assert nm.tokens("UST VL SCARF W/ PRINT") == ["vl", "scarf"]
    assert nm.tokens("Scarves") == ["scarf"]


def test_name_key_ignores_capitals_and_spacing_only():
    assert nm.name_key("  UST Tiger  Headband ") == nm.name_key("ust tiger headband")
    assert nm.name_key("Corp Jacket v.3") != nm.name_key("Corp Jacket V3")


def test_replaced_needs_the_old_name_gone_and_recent():
    assert nm.replaced({"2026-05", "2026-06"}, {"2026-07"})
    assert not nm.replaced({"2026-06", "2026-07"}, {"2026-07"})      # both on the July sheet
    assert not nm.replaced({"2025-01"}, {"2026-07"})                 # stopped long before
    assert not nm.replaced(set(), {"2026-07"})


def test_a_clean_rename_is_strong():
    out = nm.suggest([new("UST TIGER HEADBAND (2 DESIGNS)", "MADEBYRUZ", 150)],
                     [item("Tiger Headband (2 Designs)", "MADEBYRUZ", 150),
                      item("Tiger Claw", "MADEBYRUZ", 800)])
    assert out["UST TIGER HEADBAND (2 DESIGNS)"]["item"] == "Tiger Headband (2 Designs)"
    assert out["UST TIGER HEADBAND (2 DESIGNS)"]["strength"] == "strong"


def test_three_jackets_cannot_all_point_at_one_row():
    news = [new("UST JACKET V1 W/ PRINT"), new("UST JACKET V2 W/ PRINT"),
            new("UST CORPORATE JACKET V1 W/ PRINT")]
    olds = [item("Jacket"), item("Corp Jacket"), item("Cotton Brush Jacket")]
    out = nm.suggest(news, olds)
    strong_targets = [r["item"] for r in out.values() if r["strength"] == "strong"]
    assert len(strong_targets) == len(set(strong_targets))
    assert sum(1 for r in out.values() if r["item"] == "Jacket" and r["strength"] == "strong") <= 1
    assert out["UST CORPORATE JACKET V1 W/ PRINT"]["item"] == "Corp Jacket"


def test_other_supplier_is_never_suggested():
    out = nm.suggest([new("UST VL LANYARD W/ PRINT", "VARSITY LIFESTYLE")],
                     [item("Lanyard", "JUC")])
    assert out["UST VL LANYARD W/ PRINT"]["item"] is None


def test_a_different_price_in_the_name_is_not_strong():
    out = nm.suggest([new("Keychain @180", "USTORE", 50)], [item("Keychain @160", "USTORE", 50)])
    assert out["Keychain @180"]["item"] == "Keychain @160"
    assert out["Keychain @180"]["strength"] == "weak"


def test_a_different_sheet_price_is_not_strong():
    out = nm.suggest([new("UST TIGER CLAW GLOVES", "MADEBYRUZ", 1200)],
                     [item("Tiger Claw Gloves", "MADEBYRUZ", 800)])
    assert out["UST TIGER CLAW GLOVES"]["strength"] == "weak"


def test_old_name_still_selling_is_not_strong():
    out = nm.suggest([new("UST OAT MUG (Y,W,B) W/ PRINT", "JUC")],
                     [item("UST OAT MUG (Y,W,B)", "JUC", months=("2026-06", "2026-07"))])
    assert out["UST OAT MUG (Y,W,B) W/ PRINT"]["strength"] == "weak"


def test_no_supplier_is_compared_with_everything_but_only_weak():
    out = nm.suggest([new("Tiger Headband 2 Designs", None, months=())],
                     [item("Tiger Headband (2 Designs)", "MADEBYRUZ"), item("Lanyard", "JUC")])
    assert out["Tiger Headband 2 Designs"]["item"] == "Tiger Headband (2 Designs)"
    assert out["Tiger Headband 2 Designs"]["strength"] == "weak"
