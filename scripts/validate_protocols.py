"""
scripts/validate_protocols.py
------------------------------------------------------------------
Section 3.3.4 names two validation protocols: walk-forward AND an 80/20
train-test split. step4 / step4c score only walk-forward. This script scores
the models the app runs NOW under both, plus a middle protocol that isolates
what changes between them.

    A  walk-forward, last 12 months, refit every 30 days (what step4 / step4c
       store in Result_*_Metrics)
    B  walk-forward restricted to the held-out 20%: only folds whose origin is
       at or after the 80% cut. Still refit every 30 days, so B minus A is the
       effect of WHICH months are tested, not of refitting.
    C  strict 80/20: fit ONCE on the first 80% of the series, call the model
       once for the whole held-out span, score it in consecutive 30-day blocks
       from the cut. Nothing is refit, so C minus B is the effect of freezing
       the model. Same protocol as scripts/holdout_8020_benchmark.py.

Each protocol is run twice: trained on real sales only, and trained on real
sales plus the 2023 synthetic history (Fact_Sales_Synthetic, see
forecasting/synthetic_history.py). The 80% cut is taken over the REAL days and
placed at the same date in both, and walk-forward folds are laid out from the
end, so both versions are scored on identical windows of real sales: any
difference is the training history, nothing else. The 30-day forecasts the app
would show (fit on everything) are compared too.

Models, series and harness settings are imported from step4_forecast_model.py
and step4c_category_forecast.py, not re-implemented: the item model is step4's
DEFAULT_MODEL callable bound to each item's category series, the category model
is step4c's DEFAULT_MODEL, and the synthetic history is added by their own
extend_with_synthetic. A for the version the pipeline used (with synthetic when
the table is loaded) is checked against the MAPE step4 / step4c stored in the
database, so a mismatch in the set-up fails loudly.

Metrics, all on 30-day totals (the unit the app serves):
    how far off    pooled WMAPE = sum |actual - forecast| / sum actual
    too high/low   bias         = (sum forecast - sum actual) / sum actual
    MAPE <= 20%    per item / category, mean of |error| / actual over its
                   30-day windows with non-zero actual (step4's definition)
MASE is not reported: its scale is taken over all training blocks, so it moves
with the training history even when no forecast does.

Run (from the repo root, after step4 and step4c):
    python scripts/validate_protocols.py [--write]
--write saves data/validation_protocols_summary.csv and
data/validation_protocols_windows.csv.
------------------------------------------------------------------
"""
import argparse
import importlib.util
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forecasting import synthetic_history as sh
from forecasting.calendar_adjust import load_day_types
from forecasting.evaluate import make_folds


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


s4 = _load("step4_forecast_model")
s4c = _load("step4c_category_forecast")

DB_PATH = os.path.join(ROOT, "ustore.db")
OUT_SUMMARY = os.path.join(ROOT, "data", "validation_protocols_summary.csv")
OUT_WINDOWS = os.path.join(ROOT, "data", "validation_protocols_windows.csv")

TRAIN_FRACTION = 0.80
HORIZON = s4.HORIZON
MAPE_TARGET = 20.0
ARMS = {False: "real only", True: "real + 2023 synthetic"}


def item_inputs(con, synthetic):
    """({product_id: (values, fit)}, index, n_pre, n_real) for step4's Fast items."""
    products, fact, dim_date = s4.load_common(con)
    real_idx, _ = s4.build_calendar(fact, dim_date)
    cats = s4.build_category_series(fact, products, real_idx)
    idx, n_pre, _, _, cats, syn_items = s4.extend_with_synthetic(
        con, real_idx, dim_date, cats, use=synthetic)
    cat_of = products.set_index("product_id")["forecast_category"].fillna(s4.RESIDUE)
    make_model, _ = s4.MODELS[s4.DEFAULT_MODEL]
    ctx = {"index": idx, "con": con, "day_types": load_day_types(con, idx, HORIZON)}
    by_product = dict(list(fact.groupby("product_id")))
    out = {}
    for pid in products.loc[products["fsn_class"] == "F", "product_id"].astype(int):
        g = by_product.get(pid)
        v = sh.prepend(s4.build_series(g, real_idx) if g is not None else np.zeros(len(real_idx)),
                       syn_items.get(pid), idx, n_pre)
        cv = cats.get(cat_of.get(pid, s4.RESIDUE), np.zeros(len(idx)))
        out[pid] = (v, make_model({**ctx, "category_values": cv}))
    return out, idx, n_pre, len(real_idx)


def category_inputs(con, synthetic):
    """({category: (values, fit)}, index, n_pre, n_real) for step4c."""
    wide, _ = s4c.load_category_series(con)
    series, _, _ = s4c.trim_padding(wide)
    n_real = len(series)
    series, n_pre, _ = s4c.extend_with_synthetic(con, series, use=synthetic)
    model = s4c.MODELS[s4c.DEFAULT_MODEL]({"day_types": load_day_types(con, series.index, HORIZON)})
    return ({c: (series[c].to_numpy(float), model) for c in series.columns},
            series.index, n_pre, n_real)


def score_windows(key, values, fit, cut):
    """[(protocol, window, actual, pred)] for one series under A, B and C."""
    n = values.size
    out = []

    # A: step4 / step4c's own fold layout.
    for f in make_folds(n, HORIZON, s4.MIN_FOLDS, s4.MAX_FOLDS, s4.MIN_TRAIN):
        pred = float(np.sum(fit(values[:f.train_end], HORIZON)))
        out.append(("A", f.fold_index, float(values[f.test_start:f.test_end].sum()), pred))

    # B: every walk-forward origin at or after the cut (same origins as A's
    # most recent ones, since make_folds lays them out from the end).
    folds = [f for f in make_folds(n, HORIZON, 1, None, s4.MIN_TRAIN) if f.origin >= cut]
    for i, f in enumerate(folds):
        pred = float(np.sum(fit(values[:f.train_end], HORIZON)))
        out.append(("B", i, float(values[f.test_start:f.test_end].sum()), pred))

    # C: one fit at the cut, one call for the whole held-out span.
    n_blocks = (n - cut) // HORIZON
    pred = np.asarray(fit(values[:cut], n_blocks * HORIZON), dtype=float).ravel()
    for b in range(n_blocks):
        s = slice(b * HORIZON, (b + 1) * HORIZON)
        actual = float(values[cut + b * HORIZON: cut + (b + 1) * HORIZON].sum())
        out.append(("C", b, actual, float(pred[s].sum())))

    return [dict(key=key, protocol=p, window=w, actual=a, pred=q) for p, w, a, q in out]


def run(level, inputs, synthetic):
    """(windows, next-30-day totals, cut date) for one level and training history."""
    series, idx, n_pre, n_real = inputs
    cut = n_pre + int(np.floor(TRAIN_FRACTION * n_real))   # same real date in both arms
    rows, nxt = [], {}
    for key, (v, fit) in series.items():
        rows += score_windows(key, v, fit, cut)
        nxt[key] = float(np.sum(fit(v, HORIZON)))
    w = pd.DataFrame(rows).assign(level=level, training=ARMS[synthetic])
    return w, nxt, idx[cut]


def per_key_mape(g):
    nz = g["actual"] != 0
    if not nz.any():
        return np.nan
    return float(np.mean(np.abs((g.loc[nz, "actual"] - g.loc[nz, "pred"]) / g.loc[nz, "actual"])) * 100)


def summarise(w):
    rows = []
    for (lvl, tr, p), g in w.groupby(["level", "training", "protocol"], sort=False):
        a, q = g["actual"].sum(), g["pred"].sum()
        mapes = g.groupby("key").apply(per_key_mape, include_groups=False)
        rows.append(dict(
            level=lvl, training=tr, protocol=p,
            n_series=g["key"].nunique(), windows_per_series=int(g.groupby("key").size().max()),
            wmape_pct=100 * (g["actual"] - g["pred"]).abs().sum() / a if a else np.nan,
            bias_pct=100 * (q - a) / a if a else np.nan,
            n_mape_le_20=int((mapes <= MAPE_TARGET).sum()), n_mape_defined=int(mapes.notna().sum()),
        ))
    return pd.DataFrame(rows)


def self_check(w, con, used_synthetic):
    """A, for the history the pipeline used, must reproduce the stored MAPE."""
    stored = {
        "items": pd.read_sql("SELECT product_id AS key, mape FROM Result_Forecast_Metrics "
                             "WHERE period_scope = 'overall'", con),
        "categories": pd.read_sql("SELECT forecast_category AS key, mape FROM "
                                  "Result_Category_Forecast_Metrics WHERE period_scope = 'overall'", con),
    }
    for lvl, st in stored.items():
        st = st.set_index("key")["mape"]
        g = w[(w["level"] == lvl) & (w["protocol"] == "A") & (w["training"] == ARMS[used_synthetic])]
        mine = g.groupby("key").apply(per_key_mape, include_groups=False)
        both = pd.concat([mine.rename("mine"), st.rename("stored")], axis=1).dropna()
        diff = float((both["mine"] - both["stored"]).abs().max()) if len(both) else np.nan
        ok = len(both) == st.notna().sum() and diff < 1e-6
        print(f"  self-check {lvl} ({ARMS[used_synthetic]}): A reproduces stored MAPE for "
              f"{len(both)}/{st.notna().sum()} (max diff {diff:.2e}) -> {'OK' if ok else 'MISMATCH'}")
        if not ok:
            raise SystemExit(f"protocol A does not reproduce the stored {lvl} metrics - "
                             "re-run step4 / step4c, or the set-up here has drifted")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("---")[0].strip())
    ap.add_argument("--write", action="store_true", help="also write the CSVs under data/")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    loaded = sh.load(con) is not None
    if not loaded:
        print("NOTE: Fact_Sales_Synthetic is not loaded - run scripts/load_synthetic_2023.py; "
              "only the real-only version is scored.")
    arms = [False, True] if loaded else [False]

    windows, nxt_rows = [], []
    for level, build in (("items", item_inputs), ("categories", category_inputs)):
        nxt = {}
        for syn in arms:
            inputs = build(con, syn)
            w, nxt[syn], cut_date = run(level, inputs, syn)
            windows.append(w)
            print(f"{level:10s} {ARMS[syn]:22s} training from {inputs[1][0].date()}; "
                  f"80% cut: test from {cut_date.date()}")
        if loaded:
            for k in nxt[False]:
                nxt_rows.append(dict(level=level, key=k, real=nxt[False][k], synthetic=nxt[True][k]))
    w = pd.concat(windows, ignore_index=True)
    self_check(w, con, used_synthetic=loaded)

    summary = summarise(w)
    pd.set_option("display.width", 200)
    print()
    print(summary.round(1).to_string(index=False))

    if loaded:
        # Same windows in both arms, so the predictions can be compared one to one.
        piv = w.pivot_table(index=["level", "protocol", "key", "window"], columns="training",
                            values="pred").dropna()
        d = (piv[ARMS[True]] - piv[ARMS[False]]).abs().groupby(level=["level", "protocol"])
        tot = piv[ARMS[False]].groupby(level=["level", "protocol"]).sum()
        print("\nHow much the 2023 history changed the predictions on the scored windows:")
        print(pd.DataFrame({"windows_changed": d.apply(lambda s: int((s > 1e-9).sum())),
                            "windows": d.size(),
                            "total_abs_change_pct": 100 * d.sum() / tot}).round(3).to_string())
        nx = pd.DataFrame(nxt_rows)
        print("\nNext 30 days (what the app shows), real only vs + 2023 synthetic:")
        for lvl, g in nx.groupby("level"):
            ch = (g["synthetic"] - g["real"]).abs()
            print(f"  {lvl:10s} total {g['real'].sum():8.1f} -> {g['synthetic'].sum():8.1f} units | "
                  f"{int((ch > 1e-6).sum())} of {len(g)} changed, largest change {ch.max():.3f} units")

    if args.write:
        os.makedirs(os.path.dirname(OUT_SUMMARY), exist_ok=True)
        summary.to_csv(OUT_SUMMARY, index=False)
        w.to_csv(OUT_WINDOWS, index=False)
        print(f"\nWrote {os.path.relpath(OUT_SUMMARY, ROOT)} and {os.path.relpath(OUT_WINDOWS, ROOT)}")


if __name__ == "__main__":
    main()
