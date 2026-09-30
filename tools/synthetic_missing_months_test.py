"""
tools/synthetic_missing_months_test.py
------------------------------------------------------------------
Fill the months the store has NO tally data for with synthetic sales, write
them to a CSV, and measure whether the shipped forecasts (and the models that
could plausibly use a longer history) get better when trained on them.

The missing months
------------------
Dim_Date runs from 2023-01-01, but Fact_Sales starts on 2024-05-02 and has no
rows at all in June or July 2024:

    2023-01 .. 2024-04   16 months before the first tally sheet
    2024-06 .. 2024-07    2 months between the May 2024 sheet and the Aug 2024 one

The two months in the gap are zero-filled in production (the daily panel is
reindexed with fill_value=0), so every model currently reads them as "sold
nothing". The 16 earlier months simply do not exist for any model. Aug-Dec 2024
are partially tallied but DO have real sheets, so they are left as they are.

How the synthetic days are made (calendar-matched whole-day resampling)
-----------------------------------------------------------------------
Every missing date is given the sales of one REAL store-day, copied across all
products at once (so category totals and the zero pattern shared by all
categories stay realistic). The source day is drawn at random from real tallied
days that match the missing date on, in order of preference:

    L1  month of year + weekday + school-calendar day type   (>= MIN_BUCKET days)
    L2  weekday + day type
    L3  weekday
    L4  any source day
Day types come from Dim_Date exactly as forecasting/calendar_adjust.py reads
them (enrollment > semester break > exam week > normal); Dim_Date's calendar for
2023-24 is the published one from data/calendar_ranges.csv, not a guess. A date
Dim_Date marks is_store_closed gets zero sales.

Source days are restricted to
  - days with a tally sheet (is_tally_date = 1): an un-tallied zero-filled day
    is not a real observation,
  - not Aug or Sep 2024: step0 classifies those two sheets as sparse periodic
    stock counts, not daily tallies,
  - strictly BEFORE the first test window of the walk-forward harness. The
    synthetic data therefore carries no information from any day a forecast is
    scored on, in ANY fold.

What this does NOT do: invent a trend. 2023 is assumed to sell at the level of
May 2024 - Jul 2025. Items launched later get synthetic 2023 sales if they sold
on the source day. Both are fabrications and are labelled as such.

Why the result is largely decided in advance (stated before running)
--------------------------------------------------------------------
The shipped models read short windows: the category model a 180-day average,
the item model a 180-day category level x a 30-day share, blended with TSB at
alpha 0.05 (weights decay to ~1e-10 after 438 days), and the calendar
adjustment measures its day-type ratios over the last 365 days. The first test
window opens 438 days after 2024-05-02, so:
  - the 2023 - Apr 2024 synthetic data is invisible to every shipped piece,
  - the Jun-Jul 2024 fill reaches only the calendar ratios of the earliest
    folds (365 days back from a July 2025 origin lands in July 2024).
The models that CAN read the extra history are scored alongside for that reason:
a 12-month average, same-window-last-year seasonal models, the calendar ratios
measured over ALL history, Prophet with a yearly term, and ridge (the learner
that won the earlier bootstrap experiment, docs/SYNTHETIC_AUGMENTATION_CATEGORY.md).

Honesty controls (the script stops if any fails)
------------------------------------------------
  1. The real arm reproduces the MASE stored in ustore.db for both shipped
     models (Result_Category_Forecast_Metrics, Result_Forecast_Metrics).
  2. Every arm is scored on the SAME real test windows with the SAME actuals;
     synthetic values never enter a test window.
  3. Immune controls (the category 6-month average, the item top-down share)
     read <= 180 days, so their predictions must be bit-identical in every arm.
  4. MASE uses the REAL arm's naive scale for every arm. The harness computes
     the scale from the training slice, which the synthetic data changes; left
     alone it would move MASE for a model whose predictions did not change.
  5. Ten generator seeds for every method except Prophet (one seed: cost); the
     spread is reported so one lucky draw cannot pass as a result.
  6. Prophet's silent fallback to a 30-day mean is COUNTED per arm.

Outputs (data/)
---------------
  synthetic_missing_months_2023_2024.csv   THE SYNTHETIC DATA (seed 0): one row per
                                           synthetic date x product with sales > 0
  synthetic_missing_months_days.csv        one row per synthetic date: source day,
                                           match level, day type, units
  synthetic_missing_months_test.csv        level x method x arm: MASE, WMAPE, bias,
                                           items better / worse, seed spread
  synthetic_missing_months_folds.csv       raw rows: level x key x method x arm x fold

Run:  python tools/synthetic_missing_months_test.py [--jobs 8] [--seeds 10] [--no-prophet]
------------------------------------------------------------------
"""
import argparse
import os
import sqlite3
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import (LEVEL_WINDOW, calendar_capped_fit_predict,
                                         load_day_types, type_ratios)
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.topdown import topdown_share_fit_predict, topdown_tsb_fit_predict

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
H, MIN_F, MAX_F, MIN_T = 30, 3, 12, 60
RESIDUE = "Uncategorised"
MISSING = [("2023-01-01", "2024-04-30"), ("2024-06-01", "2024-07-31")]
SPARSE_MONTHS = {"2024-08", "2024-09"}      # step0: periodic stock counts, not daily tallies
MIN_BUCKET = 3
N_BOOT, BOOT_SEED = 4000, 0

REAL = "real only"
SHIP_CAT = "Shipped: 6-mo avg + calendar"
SHIP_ITEM = "Shipped: top-down + TSB + calendar"
CONTROL_CAT = "6-mo avg, no calendar (immune control)"
CONTROL_ITEM = "Top-down share, no TSB (immune control)"
PROPHET = "Prophet weekly + yearly + calendar"

G = {}


# ------------------------------------------------------------------ data
def day_table(con, dates):
    """weekday, month, day type, closed and tally flags for `dates`."""
    cal = pd.read_sql_query(
        "SELECT calendar_date, is_tally_date, is_store_closed FROM Dim_Date",
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    idx = pd.DatetimeIndex(dates)
    c = cal.reindex(idx).fillna(0)
    return pd.DataFrame({"weekday": idx.dayofweek, "month": idx.month,
                         "ym": idx.strftime("%Y-%m"),
                         "day_type": load_day_types(con, idx, 0)[:len(idx)],
                         "tally": c.is_tally_date.astype(int).to_numpy(),
                         "closed": c.is_store_closed.astype(int).to_numpy()}, index=idx)


def load_real(con):
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, forecast_category FROM Dim_Product", con)
    prod["forecast_category"] = prod.forecast_category.fillna(RESIDUE)
    fact = pd.read_sql_query(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
           JOIN Dim_Date d ON d.date_id = f.date_id WHERE f.transaction_type = 'sale'""",
        con, parse_dates=["calendar_date"])
    # Same construction as step4 / step4c: zero-filled daily panel from the first
    # Fact_Sales date to the last date anything sold.
    last = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last, freq="D")
    panel = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    return prod, panel


def first_test_day(n_days, idx):
    folds = make_folds(n_days, H, MIN_F, MAX_F, MIN_T)
    return idx[min(f.origin for f in folds)]


def missing_dates():
    return pd.DatetimeIndex(np.concatenate(
        [pd.date_range(a, b, freq="D").to_numpy() for a, b in MISSING]))


def synthesise(panel, days_real, days_miss, cutoff, seed):
    """(synthetic panel on the missing dates, per-date log). See the docstring."""
    src = days_real[(days_real.index < cutoff) & (days_real.tally == 1) & (days_real.closed == 0)
                    & ~days_real.ym.isin(SPARSE_MONTHS)]
    ladders = [("L1 month+weekday+day type", ["month", "weekday", "day_type"]),
               ("L2 weekday+day type", ["weekday", "day_type"]),
               ("L3 weekday", ["weekday"])]
    rng = np.random.default_rng(seed)
    picks, log = [], []
    for d, r in days_miss.iterrows():
        if r.closed:
            picks.append(None)
            log.append((d, None, "closed (zero)"))
            continue
        pool, level = src.index, "L4 any day"
        for name, keys in ladders:
            m = np.logical_and.reduce([src[k].to_numpy() == r[k] for k in keys])
            if m.sum() >= MIN_BUCKET:
                pool, level = src.index[m], name
                break
        s = pool[rng.integers(len(pool))]
        picks.append(s)
        log.append((d, s, level))
    vals = np.vstack([np.zeros(panel.shape[1]) if s is None else panel.loc[s].to_numpy()
                      for s in picks])
    syn = pd.DataFrame(vals, index=days_miss.index, columns=panel.columns)
    lg = pd.DataFrame(log, columns=["date", "source_date", "match_level"]).set_index("date")
    return syn, lg


def augmented(panel, syn):
    """Real panel extended back to the first missing date; synthetic values on the
    missing dates, real values everywhere else (real untallied days stay 0)."""
    idx = pd.date_range(min(syn.index.min(), panel.index.min()), panel.index.max(), freq="D")
    out = panel.reindex(idx, fill_value=0.0)
    assert (panel.reindex(syn.index).fillna(0.0).to_numpy() == 0).all(), \
        "a 'missing' date has real sales - the gap list is wrong"
    out.loc[syn.index] = syn.to_numpy()
    return out


def cat_series(panel, cat_of):
    return panel.T.groupby(panel.columns.map(cat_of)).sum().T


# ---------------------------------------------------------------- methods
def calendar_all_history(base_fn, types, cat_values=None):
    """forecasting/calendar_adjust.py's capped adjustment with the day-type ratios
    measured over ALL history before the origin instead of the last 365 days."""
    cat = None if cat_values is None else np.asarray(cat_values, dtype=float)

    def _f(train, horizon):
        t = np.asarray(train, dtype=float)
        n = t.size
        base = np.asarray(base_fn(t, horizon), dtype=float)
        src = t if cat is None else cat[:n]
        r = type_ratios(src, types[:n], window=n)
        mult = 1.0
        if r:
            denom = np.mean([r[x] for x in types[max(0, n - LEVEL_WINDOW):n]])
            if denom > 0:
                mult = min(1.0, float(np.mean([r[x] for x in types[n:n + horizon]]) / denom))
        return np.maximum(base * mult, 0.0)
    return _f


def seasonal(kind, cat=None):
    """Same definitions as scripts/test_item_forecast_methods.py::_seasonal:
    'naive' repeats the same 30 days last year; 'own' / 'category' scale the
    6-month average by last year's same window / last year's average 30 days
    (clipped to [0.5, 2]) from the series itself / its category. Falls back to
    the 6-month average when there is less than 395 days of history."""
    def _f(train, h):
        t = np.asarray(train, float)
        n = t.size
        if n < 365 + 30:
            return np.maximum(np.full(h, t[-180:].mean()), 0.0)
        x = t if kind in ("naive", "own") else cat[:n]
        ly = x[n - 365:n - 335].sum()
        if kind == "naive":
            return np.maximum(np.full(h, ly / 30.0), 0.0)
        blk = x[n - 365:n].sum() / (365 / 30.0)
        s = float(np.clip(ly / blk, 0.5, 2.0)) if blk > 0 else 1.0
        return np.maximum(np.full(h, t[-180:].mean() * s), 0.0)
    return _f


def cat_methods(types):
    from forecasting.ml_models_fastmoving import ridge_fit_predict
    rm6 = rolling_mean_fit_predict(180)
    return {
        SHIP_CAT: calendar_capped_fit_predict(rm6, types),
        CONTROL_CAT: rm6,
        "6-mo avg + calendar, ratios from all history": calendar_all_history(rm6, types),
        "12-mo avg + calendar": calendar_capped_fit_predict(rolling_mean_fit_predict(365), types),
        "6-mo avg x seasonal index (same window last year)": seasonal("own"),
        "Same 30 days last year": seasonal("naive"),
        "Ridge (prior bootstrap winner)": ridge_fit_predict(),
    }


def item_methods(types, cv):
    return {
        SHIP_ITEM: calendar_capped_fit_predict(topdown_tsb_fit_predict(cv), types, cat_values=cv),
        CONTROL_ITEM: topdown_share_fit_predict(cv, 180, 30),
        "Top-down + TSB, no calendar": topdown_tsb_fit_predict(cv),
        "Shipped, calendar ratios from all history":
            calendar_all_history(topdown_tsb_fit_predict(cv), types, cat_values=cv),
        "12-mo avg + calendar":
            calendar_capped_fit_predict(rolling_mean_fit_predict(365), types, cat_values=cv),
        "6-mo avg x item seasonal index": seasonal("own"),
        "6-mo avg x category seasonal index": seasonal("category", cv),
        "Same 30 days last year": seasonal("naive"),
    }


def prophet_method(con, idx):
    from forecasting.prophet_model import CALENDAR_REGRESSORS, load_calendar, prophet_fit_predict
    cal = load_calendar(con, idx)
    return prophet_fit_predict(idx, cal[list(CALENDAR_REGRESSORS)], PROPHET, weekly=True, yearly=True)


# ----------------------------------------------------------------- workers
def init():
    global G
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    prod, panel = load_real(con)
    cat_of = prod.set_index("product_id").forecast_category
    miss = missing_dates()
    G = dict(con=con, prod=prod, panel=panel, cat_of=cat_of,
             fast=[p for p in prod[prod.fsn_class == "F"].product_id if p in panel.columns],
             days_real=day_table(con, panel.index), days_miss=day_table(con, miss),
             cutoff=first_test_day(len(panel), panel.index), arms={})


def arm(name, seed):
    """(daily panel, category panel, day types) for an arm, cached per worker."""
    key = (name, seed)
    if key not in G["arms"]:
        p = G["panel"]
        if name != REAL:
            syn, _ = synthesise(p, G["days_real"], G["days_miss"], G["cutoff"], seed)
            p = augmented(p, syn)
        # one arm cached at a time: tasks arrive grouped by arm, and it bounds memory
        G["arms"] = {key: (p, cat_series(p, G["cat_of"]),
                           np.asarray(load_day_types(G["con"], p.index, H)))}
    return G["arms"][key]


def score(level, key, v, idx, methods, arm_name, seed):
    folds = make_folds(v.size, H, MIN_F, MAX_F, MIN_T)
    out = []
    for name, fn in methods.items():
        for r in walk_forward_evaluate(key, v, fn, name, folds=folds).rows:
            out.append(dict(level=level, key=key, method=name, arm=arm_name, seed=seed,
                            fold=r["fold"], window_start=idx[r["origin"]].date(),
                            actual=r["actual_30d"], pred=r["pred_30d"], scale=r["naive_scale"],
                            train_days=r["origin"]))
    return out


def job(args):
    arm_name, seed, what = args
    p, cats, types = arm(arm_name, seed)
    idx = p.index
    rows = []
    if what == "cheap":
        for c in cats.columns:
            if cats[c].sum() > 0:
                rows += score("category", c, cats[c].to_numpy(float), idx, cat_methods(types),
                              arm_name, seed)
        for pid in G["fast"]:
            cv = cats[G["cat_of"][pid]].to_numpy(float)
            rows += score("item", pid, p[pid].to_numpy(float), idx, item_methods(types, cv),
                          arm_name, seed)
    else:                       # ("prophet", level, key)
        _, level, key = what
        v = (cats[key] if level == "category" else p[key]).to_numpy(float)
        rows += score(level, key, v, idx, {PROPHET: prophet_method(G["con"], idx)}, arm_name, seed)
        # the silent fallback is the 30-day mean; flag folds that equal it exactly
        folds = {f.fold_index: f for f in make_folds(v.size, H, MIN_F, MAX_F, MIN_T)}
        for r in rows:
            o = folds[r["fold"]].origin
            fb = v[o - 30:o].mean() * H
            r["prophet_fallback"] = bool(v[:o].sum() > 0 and abs(r["pred"] - fb) < 1e-9)
    return rows


# ------------------------------------------------------------- reporting
def per_key(df):
    """step4's MASE rule on the REAL arm's scale: MAE over folds / mean of the
    folds' scales, zero / NaN scales left out."""
    g = df.groupby("key")
    mae = g.apply(lambda x: np.abs(x.actual - x.pred).mean())
    sc = g.real_scale.apply(lambda s: s[np.isfinite(s) & (s > 0)].mean())
    return pd.DataFrame({"mae": mae, "mase": mae / sc,
                         "abs_err": g.apply(lambda x: np.abs(x.actual - x.pred).sum()),
                         "bias": g.apply(lambda x: (x.pred - x.actual).sum()),
                         "actual": g.actual.sum()})


def summarise(df):
    rows = []
    for (level, method), g in df.groupby(["level", "method"], sort=False):
        base = per_key(g[g.arm == REAL])
        keys = base.index[np.isfinite(base.mase)]
        rec = dict(level=level, method=method, n=len(keys),
                   real_mean_mase=base.mase[keys].mean(),
                   real_wmape_pct=100 * base.abs_err[keys].sum() / base.actual[keys].sum(),
                   real_bias_pct=100 * base.bias[keys].sum() / base.actual[keys].sum())
        syn = g[g.arm != REAL]
        per_seed = []
        for seed, gs in syn.groupby("seed"):
            k = per_key(gs).loc[keys]
            per_seed.append(dict(seed=seed, mase=k.mase.mean(),
                                 wmape=100 * k.abs_err.sum() / k.actual.sum(),
                                 bias=100 * k.bias.sum() / k.actual.sum(),
                                 better=int((k.mase < base.mase[keys] - 1e-9).sum()),
                                 worse=int((k.mase > base.mase[keys] + 1e-9).sum()),
                                 max_pred_drift=float(np.abs(
                                     gs.sort_values(["key", "fold"]).pred.to_numpy()
                                     - g[g.arm == REAL].sort_values(["key", "fold"]).pred.to_numpy()).max())))
        ps = pd.DataFrame(per_seed)
        rec.update(seeds=len(ps),
                   syn_mean_mase=ps.mase.mean(), syn_mase_min=ps.mase.min(), syn_mase_max=ps.mase.max(),
                   syn_wmape_pct=ps.wmape.mean(), syn_wmape_min=ps.wmape.min(), syn_wmape_max=ps.wmape.max(),
                   syn_bias_pct=ps.bias.mean(),
                   mase_change_pct=100 * (ps.mase.mean() / rec["real_mean_mase"] - 1),
                   seeds_improved=int((ps.mase < rec["real_mean_mase"] - 1e-9).sum()),
                   keys_better_seed0=int(ps.better.iloc[0]), keys_worse_seed0=int(ps.worse.iloc[0]),
                   max_pred_drift=ps.max_pred_drift.max())
        # bootstrap over series, seed 0: MASE(synthetic) - MASE(real)
        k0 = per_key(syn[syn.seed == ps.seed.iloc[0]]).loc[keys]
        d = (k0.mase - base.mase[keys]).to_numpy()
        b = np.random.default_rng(BOOT_SEED).integers(0, d.size, size=(N_BOOT, d.size))
        m = d[b].mean(axis=1)
        rec.update(gap_seed0=d.mean(), gap_ci_lo=np.percentile(m, 2.5), gap_ci_hi=np.percentile(m, 97.5))
        if method == PROPHET:
            rec["fallback_folds_real"] = int(g[g.arm == REAL].prophet_fallback.sum())
            rec["fallback_folds_syn"] = int(syn.prophet_fallback.sum())
            rec["folds_per_arm"] = int((g.arm == REAL).sum())
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--no-prophet", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    init()
    con, prod, panel = G["con"], G["prod"], G["panel"]
    cutoff = G["cutoff"]
    print(f"real panel {panel.index[0].date()} .. {panel.index[-1].date()} ({len(panel)} days); "
          f"first test window opens {cutoff.date()}")

    # ---- the synthetic data file (seed 0) ----------------------------------
    syn, log = synthesise(panel, G["days_real"], G["days_miss"], cutoff, 0)
    dm = G["days_miss"]
    assert log.source_date.dropna().max() < cutoff, "a source day lies inside a test window"
    name = prod.set_index("product_id").item_name
    long = syn.stack().rename("quantity_sold").reset_index()
    long.columns = ["calendar_date", "product_id", "quantity_sold"]
    long = long[long.quantity_sold > 0].copy()
    long["item_name"] = long.product_id.map(name)
    long["forecast_category"] = long.product_id.map(G["cat_of"])
    long = long.join(log, on="calendar_date").join(dm[["day_type"]], on="calendar_date")
    long["is_synthetic"] = 1
    long["generator"] = "calendar_matched_whole_day_seed0"
    long["calendar_date"] = long.calendar_date.dt.strftime("%Y-%m-%d")
    long["source_date"] = pd.to_datetime(long.source_date).dt.strftime("%Y-%m-%d")
    long = long[["calendar_date", "product_id", "item_name", "forecast_category", "quantity_sold",
                 "source_date", "day_type", "match_level", "is_synthetic", "generator"]]
    long.sort_values(["calendar_date", "product_id"]).to_csv(
        os.path.join(DATA, "synthetic_missing_months_2023_2024.csv"), index=False, lineterminator="\n")
    days = dm[["weekday", "day_type", "closed"]].join(log)
    days["units"] = syn.sum(axis=1)
    days.index.name = "calendar_date"
    days = days.reset_index()
    days["calendar_date"] = days.calendar_date.dt.strftime("%Y-%m-%d")
    days["source_date"] = pd.to_datetime(days.source_date).dt.strftime("%Y-%m-%d")
    days.to_csv(os.path.join(DATA, "synthetic_missing_months_days.csv"), index=False, lineterminator="\n")
    real_rate = panel[(G["days_real"].tally == 1).to_numpy()].sum(axis=1)
    print(f"synthetic: {len(syn)} days, {int(syn.to_numpy().sum()):,} units, {len(long):,} product-day rows; "
          f"mean {syn.sum(axis=1).mean():.1f} units/day vs {real_rate.mean():.1f} on real tallied days")
    print("match levels:", log.match_level.value_counts().to_dict())

    # ---- score ---------------------------------------------------------------
    tasks = [(REAL, -1, "cheap")] + [("synthetic", s, "cheap") for s in range(args.seeds)]
    if not args.no_prophet:
        _, cats, _ = arm(REAL, -1)
        keys = [("category", c) for c in cats.columns if cats[c].sum() > 0] + \
               [("item", p) for p in G["fast"]]
        tasks += [(a, s, ("prophet", lv, k)) for a, s in ((REAL, -1), ("synthetic", 0)) for lv, k in keys]
    with Pool(args.jobs, initializer=init) as pool:
        rows = [r for part in pool.imap_unordered(job, tasks) for r in part]
    df = pd.DataFrame(rows)
    print(f"scored {len(df):,} fold rows in {time.time() - t0:.0f}s")

    # ---- controls ------------------------------------------------------------
    real = df[df.arm == REAL]
    # 1. real arm reproduces what step4 / step4c stored
    stored_c = pd.read_sql_query("SELECT forecast_category AS key, mase FROM Result_Category_Forecast_Metrics "
                                 "WHERE period_scope = 'overall'", con).set_index("key").mase
    stored_i = pd.read_sql_query("SELECT product_id AS key, mase FROM Result_Forecast_Metrics "
                                 "WHERE period_scope = 'overall'", con).set_index("key").mase
    for level, method, stored in (("category", SHIP_CAT, stored_c), ("item", SHIP_ITEM, stored_i)):
        g = real[(real.level == level) & (real.method == method)].copy()
        g["real_scale"] = g.scale
        k = per_key(g).mase.dropna()
        gap = (k - stored.reindex(k.index)).abs().max()
        print(f"[{'PASS' if gap < 1e-9 else 'FAIL'}] real arm reproduces stored {level} MASE "
              f"(max gap {gap:.1e}, {k.size} series)")
        assert gap < 1e-9, f"real arm does not reproduce the stored {level} metrics"
    # 2. same real test windows and actuals in every arm
    ref = real.set_index(["level", "key", "method", "fold"])[["window_start", "actual", "scale"]]
    j = df.join(ref, on=["level", "key", "method", "fold"], rsuffix="_real")
    same = (j.window_start == j.window_start_real) & (j.actual == j.actual_real)
    print(f"[{'PASS' if same.all() else 'FAIL'}] identical real test windows and actuals "
          f"({same.sum():,}/{len(j):,} rows)")
    assert same.all(), "an arm was scored on different test windows"
    df["real_scale"] = j.scale_real.to_numpy()
    # 3. immune controls bit-identical
    for level, method in (("category", CONTROL_CAT), ("item", CONTROL_ITEM)):
        g = df[(df.level == level) & (df.method == method)]
        r = g[g.arm == REAL].set_index(["key", "fold"]).pred
        dd = max(float((gs.set_index(["key", "fold"]).pred - r).abs().max())
                 for _, gs in g[g.arm != REAL].groupby("seed"))
        print(f"[{'PASS' if dd == 0 else 'FAIL'}] immune control '{method}': max prediction drift {dd:.2e}")
        assert dd == 0, "an immune control moved - synthetic data reached a test window"
    con.close()

    # ---- summary -------------------------------------------------------------
    res = summarise(df)
    res.to_csv(os.path.join(DATA, "synthetic_missing_months_test.csv"), index=False,
               lineterminator="\n", float_format="%.4f")
    df.drop(columns=["real_scale"]).to_csv(os.path.join(DATA, "synthetic_missing_months_folds.csv"),
                                           index=False, lineterminator="\n", float_format="%.4f")
    pd.set_option("display.width", 250)
    cols = ["level", "method", "n", "real_mean_mase", "syn_mean_mase", "syn_mase_min", "syn_mase_max",
            "mase_change_pct", "real_wmape_pct", "syn_wmape_pct", "seeds_improved", "keys_better_seed0",
            "keys_worse_seed0", "gap_ci_lo", "gap_ci_hi", "max_pred_drift"]
    print(res[cols].round(3).to_string(index=False))
    if "fallback_folds_syn" in res:
        print(res.loc[res.method == PROPHET, ["level", "fallback_folds_real", "fallback_folds_syn",
                                              "folds_per_arm"]].to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s. Wrote data/synthetic_missing_months_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
