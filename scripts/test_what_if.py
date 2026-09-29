"""
scripts/test_what_if.py
------------------------------------------------------------------
What-if scenarios: how low could the error go if the store removed a cause of
error that no forecasting model can remove on its own?

  Closures known       store closures are scheduled in advance, so the
                       forecast drops the days the store will be shut.
  Bulk orders known    large one-day purchases (a day selling more than 20x /
                       10x the item's usual selling day - bulk or organisation
                       orders, pre-orders) are logged ahead of time and added to
                       the forecast exactly; the model forecasts only the
                       regular walk-in demand.

These are NOT model improvements and are not what the app does. They split the
current error into "what the model gets wrong" and "what the store's data could
not have told it", which is what a recommendation to the store rests on.

Honest scoring
--------------
  - Same models as the app (step4 / step4c), same 12 past windows, same
    folds. Baseline reproduces ustore.db exactly (checked).
  - Error is always divided by ALL units actually sold, bulk included: in the
    bulk scenario the store's forecast is "regular forecast + the known orders",
    so the orders add no error but still count in the total. Removing them from
    the denominator would flatter the result.
  - MASE uses the same denominator as the baseline (the real series'), so the
    scenarios are comparable.
  - The bulk threshold is shown at two levels (20x strict, 10x broader) rather
    than one chosen after the fact, with the share of units each one treats as
    known. Some flagged days are enrollment rushes rather than orders, so the
    bulk figure is an upper bound on what logging orders alone would achieve.

Outputs (data/):
  what_if_test.csv     one row per cohort x scenario
  what_if_series.csv   one row per category / item x scenario

Run:  python scripts/test_what_if.py
------------------------------------------------------------------
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.evaluate import make_folds, walk_forward_evaluate
import test_history_length as thl   # same data loading and shipped models

DATA = os.path.join(ROOT, "data")
H = thl.HORIZON
LEVEL_WINDOW = 180      # the 6-month average's window: closures there already pulled the level down


def closures(idx):
    con = sqlite3.connect("file:%s?mode=ro" % thl.DB_PATH, uri=True)
    c = pd.read_sql_query("SELECT calendar_date, is_store_closed FROM Dim_Date", con,
                          parse_dates=["calendar_date"]).set_index("calendar_date").is_store_closed
    con.close()
    full = pd.date_range(idx[0], idx[-1] + pd.Timedelta(days=H), freq="D")
    return c.reindex(full).fillna(0).to_numpy(float)


def with_closures(fn, closed):
    """Scale a forecast by (share of the next 30 days the store is open) /
    (share open over the 180 days its level was learned on)."""
    def _f(train, horizon):
        n = len(train)
        base = np.asarray(fn(train, horizon), dtype=float)
        open_next = 1 - closed[n:n + horizon].mean()
        open_past = 1 - closed[max(0, n - LEVEL_WINDOW):n].mean()
        return base * (open_next / open_past if open_past > 0 else 1.0)
    return _f


def regular_demand(daily, k):
    """Each item's sales with its bulk days cut back to a usual day: any day
    above k x the item's median selling day is set to that median. Returns
    (regular, share of units treated as known orders)."""
    q = daily.to_numpy(float).copy()
    for j in range(q.shape[1]):
        col = q[:, j]
        nz = col[col > 0]
        if nz.size:
            med = np.median(nz)
            spike = col > k * med
            col[spike] = med
    reg = pd.DataFrame(q, index=daily.index, columns=daily.columns)
    return reg, 1 - reg.to_numpy().sum() / daily.to_numpy().sum()


def run(key, real, series, fn):
    """Score `fn` on `series` (the scenario's version), against the REAL series'
    totals and MASE denominator."""
    folds = make_folds(real.size, H, thl.MIN_FOLDS, thl.MAX_FOLDS, thl.MIN_TRAIN)
    ev = walk_forward_evaluate(key, series, fn, "s", folds=folds)
    ev_real = walk_forward_evaluate(key, real, lambda t, h: np.zeros(h), "r", folds=folds)
    if not ev.sufficient:
        return None
    a = np.array([r["actual_30d"] for r in ev.rows]); p = np.array([r["pred_30d"] for r in ev.rows])
    tot = np.array([r["actual_30d"] for r in ev_real.rows])
    s = np.array([r["naive_scale"] for r in ev_real.rows]); good = np.isfinite(s) & (s > 0)
    err = np.abs(a - p)           # known orders are added exactly, so they add no error
    known = tot - a
    return dict(mae=float(err.mean()), mase=float(err.mean() / s[good].mean()) if good.any() else np.nan,
                abs_err=float(err.sum()), actual=float(tot.sum()), bias=float(((p + known) - tot).sum()))


def main():
    prod, daily, cats, types, stored_cat, stored_item = thl.load()
    cat_of = prod.set_index("product_id").category
    fsn = prod.set_index("product_id").fsn_class
    name = prod.set_index("product_id").item_name
    items = [p for p in daily.columns if fsn.get(p) in ("F", "S")]
    closed = closures(daily.index)
    print(f"closed days in the history: {int(closed[:len(daily)].sum())}")

    def cat_series(d):
        g = d.T.groupby(d.columns.map(cat_of)).sum().T
        return g.reindex(columns=cats.columns, fill_value=0.0)

    scen = [("Current (as the app works now)", daily, False),
            ("Closures known in advance", daily, True)]
    shares = {}
    for k in (20, 10):
        reg, share = regular_demand(daily, k)
        shares[k] = share
        scen.append((f"Bulk orders known (>{k}x a usual day)", reg, False))
    reg10, _ = regular_demand(daily, 10)
    scen.append(("Bulk orders (>10x) + closures known", reg10, True))
    print("units treated as known orders: " + ", ".join(f">{k}x {100 * s:.1f}%" for k, s in shares.items()))

    rows = []
    for label, d, use_closed in scen:
        cs = cat_series(d)
        for c in cats.columns:
            fn = thl.cat_model(types, None)
            fn = with_closures(fn, closed) if use_closed else fn
            r = run(c, cats[c].to_numpy(float), cs[c].to_numpy(float), fn)
            if r:
                rows.append(dict(scenario=label, cohort="Categories", key=c, name=c, **r))
        for pid in items:
            cv = cs[cat_of[pid]].to_numpy(float)
            fn = thl.item_model(types, cv)
            fn = with_closures(fn, closed) if use_closed else fn
            r = run(pid, daily[pid].to_numpy(float), d[pid].to_numpy(float), fn)
            if r:
                rows.append(dict(scenario=label, cohort="Fast items" if fsn[pid] == "F" else "Slow items",
                                 key=pid, name=name[pid], **r))
        print(f"  scored: {label}")
    per = pd.DataFrame(rows)

    base = per[per.scenario == scen[0][0]]
    gc = (base[base.cohort == "Categories"].set_index("key").mase - stored_cat).abs().max()
    bi = base[base.cohort != "Categories"].set_index("key").mase
    bi = bi[bi.index.isin(stored_item.index)]
    gi = (bi - stored_item.reindex(bi.index)).abs().max()
    print(f"self-check vs ustore.db: category max MASE gap {gc:.2e}, item {gi:.2e}")
    assert gc < 1e-9 and gi < 1e-9, "baseline does not reproduce the stored metrics"
    per.to_csv(os.path.join(DATA, "what_if_series.csv"), index=False, lineterminator="\n")

    out = []
    for cohort, g in per.groupby("cohort", sort=False):
        ok = g.groupby("key").mase.apply(lambda s: np.isfinite(s).all() and len(s) == len(scen))
        g = g[g.key.isin(ok[ok].index)]
        for label, *_ in scen:
            a = g[g.scenario == label]
            share = next((v for k, v in shares.items() if f">{k}x" in label), 0.0)
            out.append(dict(cohort=cohort, scenario=label, n=len(a), mean_mase=a.mase.mean(),
                            median_mase=a.mase.median(), wmape_pct=100 * a.abs_err.sum() / a.actual.sum(),
                            bias_pct=100 * a.bias.sum() / a.actual.sum(),
                            units_known_in_advance_pct=100 * share))
    out = pd.DataFrame(out)
    out.to_csv(os.path.join(DATA, "what_if_test.csv"), index=False, lineterminator="\n")
    print(out.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
