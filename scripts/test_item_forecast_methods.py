"""
scripts/test_item_forecast_methods.py
------------------------------------------------------------------
Which item-level forecaster should the Demand Forecast screen use? Scores 47
candidate methods on the 58 Fast items, on the SAME walk-forward folds, against
the SAME actuals, each forecast using only what was known at its origin - the
comparison scripts/step4_forecast_model.py's docstring rests on.

Why this exists
---------------
The item forecast on the dashboard was Prophet (mean MASE 2.52, over-forecasting
by 25%). The question was whether anything simple does better, and whether blends
("50/50" and so on) help. Every method is scored by the project's own harness
(forecasting/evaluate.py: 30-day horizon, up to 12 folds, min_train 60), on the
series step4 forecasts (zero-filled daily, cut at the last day any SKU sold), and
MASE is computed exactly as step4 stores it: an item's MAE over its folds divided
by the mean of its folds' naive scales, folds with a zero scale left out. The
script checks itself against the metrics stored in the database (below).

The candidates (declared before scoring; all 47 reported, not only the winners)
-------------------------------------------------------------------------------
  Prophet            as shipped (weekly + yearly + 6 calendar regressors); without
                     the yearly term; flat trend without / with the yearly term
  Trailing averages  last 30 / 60 / 90 / 180 / 365 days; exponentially weighted
                     (alpha 0.1 / 0.05 / 0.02); 6-month average with spikes capped
                     at the 95th / 99th percentile; median and trimmed mean of the
                     last six 30-day totals
  Intermittent       TSB (0.1, 0.05, 0.02), Croston, SBA
  Category-informed  top-down: the category's 6-month average x the item's share of
                     the category over the last 30 / 90 / 365 days; the item's
                     6- / 12-month average x category momentum; the item's 6-month
                     average x an item or category seasonal index (same window last
                     year); the same 30 days last year
  Blends             Prophet + trailing / TSB (50/50, 25/75, 75/25, thirds);
                     top-down + TSB / last month; last month + 6-month;
                     6-month + 12-month; 6-month + TSB; 3 + 6 + 12 month; and more

Cohorts, and why there are two
------------------------------
  all items      the 57 Fast items with a usable MASE scale (step4 stores 58 rows,
                 one has no scale). Comparable to what the screen showed.
  still selling  the 45 of them that sold anything in the 12 test windows (the last
                 360 days). The other 13 last sold between 2024-05 and 2025-05 -
                 fsn_class is computed from full history, so they are still
                 "Fast" - and a method that keeps forecasting them is penalised on
                 every fold. That is a real defect of that method, but it flatters
                 whichever method decays, so the honest size of an improvement is
                 the second cohort.

Robustness (what keeps a best-of-47 from being luck)
----------------------------------------------------
  time split      every method re-scored on the older 6 folds and on the newer 6;
                  the rank correlation between the two tells you whether the ranking
                  is a property of the methods or of one period
  bootstrap       items resampled with replacement (4,000 draws, seed 0): the mean
                  MASE gap between two methods with a 95% interval
  sensitivity     the leading blend re-scored over its two settings (top-down share
                  window x TSB smoothing): a flat grid means it is not a lucky pick
  by window       error and bias per 30-day window, against calendar flags

Shape
-----
The leading blend is a flat rate. Its 30-day total is spread over the days by a
shape (forecasting/shape.py) and scored day by day against a flat line: the item's
own Prophet / weekday pattern against its CATEGORY's. The total is identical for
every shape.

Outputs (data/)
---------------
  item_forecast_method_test.csv     one row per method, both cohorts, time split,
                                    bootstrap against Prophet
  item_forecast_method_folds.csv    one row per item-fold-method (the raw predictions)
  item_forecast_robustness.csv      bootstrap between the leading methods
  item_forecast_sensitivity.csv     top-down setting x TSB setting
  item_forecast_by_window.csv       per 30-day window: error and bias, with calendar flags
  item_forecast_shape_test.csv      the daily-shape comparison
  item_forecast_shape_folds.csv     and its raw per item-fold rows
Needs Prophet (`--no-prophet` skips every Prophet row, and the shape test); about
8 minutes on 4 cores.

Run:  python scripts/test_item_forecast_methods.py [--jobs 4] [--no-prophet] [--skip-shape]
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
from forecasting.baselines import ewma_fit_predict, rolling_mean_fit_predict
from forecasting.evaluate import make_folds, walk_forward_evaluate
from forecasting.intermittent import (croston_fit_predict, sba_fit_predict,
                                      tsb_fit_predict)
from forecasting.prophet_model import CALENDAR_REGRESSORS, load_calendar as load_prophet_calendar
from forecasting.shape import load_calendar as load_shape_calendar
from forecasting.shape import prophet_shape, weekday_shape
from forecasting.topdown import (blend_fit_predict, topdown_share_fit_predict,
                                 topdown_tsb_fit_predict)

DB_PATH = os.path.join(ROOT, "ustore.db")
DATA = os.path.join(ROOT, "data")
HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN = 30, 3, 12, 60
RESIDUE = "Uncategorised"
N_BOOT, BOOT_SEED = 4000, 0

SHIPPED = "Prophet (as shipped)"
BAR = "Repeat last month (30d avg)"
LEADER = "50/50: top-down + TSB"
TD30 = "Top-down: category 6-mo avg x item share of last 30d"
TSB05 = "TSB 0.05/0.05"

CAL5 = [c for c in CALENDAR_REGRESSORS if c != "semester_week"]

G = {}          # per-process data, filled by init()


# ---------------------------------------------------------------- data
def load():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    cols = {r[1] for r in con.execute("PRAGMA table_info(Dim_Product)")}
    if "forecast_category" not in cols:
        raise SystemExit("Dim_Product.forecast_category is missing - run "
                         "scripts/step1b_categorize_products.py first")
    prod = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query(
        """SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
           JOIN Dim_Date d ON d.date_id = f.date_id""", con, parse_dates=["calendar_date"])
    last_sale = fact.loc[fact.quantity_sold > 0, "calendar_date"].max()
    idx = pd.date_range(fact.calendar_date.min(), last_sale, freq="D")
    cal_p = load_prophet_calendar(con, idx)             # the six Prophet regressors
    cal_s = load_shape_calendar(con)                    # the five shape flags, all dates
    try:
        stored = pd.read_sql_query(
            """SELECT product_id, mae, mase FROM Result_Forecast_Metrics
               WHERE period_scope = 'overall'""", con)
        stored_model = [r[0] for r in con.execute("SELECT DISTINCT model_type FROM Result_Forecast")]
    except (sqlite3.OperationalError, pd.errors.DatabaseError):     # step4 has not run yet
        stored, stored_model = pd.DataFrame(columns=["product_id", "mae", "mase"]), []
    con.close()
    prod["forecast_category"] = prod["forecast_category"].fillna(RESIDUE)
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    cat_of = prod.set_index("product_id")["forecast_category"]
    fast = [p for p in prod[prod.fsn_class == "F"].product_id if p in daily.columns]
    cats = {c: daily[[p for p in daily.columns if cat_of.get(p) == c]].sum(axis=1).to_numpy()
            for c in sorted({cat_of[p] for p in fast})}
    return dict(prod=prod, daily=daily, idx=idx, cal_p=cal_p, cal_s=cal_s, cat_of=cat_of,
                fast=fast, cats=cats, stored=stored, stored_model=stored_model)


def init():
    global G
    G = load()


# ------------------------------------------------------- the methods
def _clip(a):
    return np.maximum(np.asarray(a, dtype=float), 0.0)


def _winsor(window, q):
    def _f(train, h):
        t = np.asarray(train, float)
        w = t[-window:] if t.size >= window else t
        cap = np.quantile(w, q)
        return _clip(np.full(h, np.minimum(w, cap).mean() if w.size else 0.0))
    return _f


def _block_stat(kind, n_blocks=6):
    def _f(train, h):
        t = np.asarray(train, float)
        n = min(n_blocks, t.size // 30)
        if n < 3:
            return _clip(np.full(h, t.mean() if t.size else 0.0))
        b = t[-n * 30:].reshape(n, 30).sum(axis=1)
        v = np.median(b) if kind == "median" else np.sort(b)[1:-1].mean()
        return _clip(np.full(h, v / 30.0))
    return _f


def _momentum(cat, item_window, long_window):
    """item's trailing mean x (category's last 90 days / category's `long_window`),
    the ratio clipped to [0.5, 2]."""
    def _f(train, h):
        t = np.asarray(train, float)
        c = cat[:t.size]
        base = c[-long_window:].mean()
        r = float(np.clip(c[-90:].mean() / base, 0.5, 2.0)) if base > 0 else 0.0
        return _clip(np.full(h, t[-item_window:].mean() * r))
    return _f


def _seasonal(cat, kind):
    """The window one year before the forecast window, relative to the trailing
    year's average 30-day block. 'naive' repeats last year's window as is;
    'item' / 'category' scale the item's 6-month average by that ratio (clipped to
    [0.5, 2]) taken from the item's / the category's own series. Needs 395 days;
    on a shorter slice it falls back to the plain 6-month average."""
    def _f(train, h):
        t = np.asarray(train, float)
        n = t.size
        if n < 365 + 30:
            return _clip(np.full(h, t[-180:].mean()))
        x = t if kind in ("naive", "item") else cat[:n]
        ly = x[n - 365:n - 335].sum()
        if kind == "naive":
            return _clip(np.full(h, ly / 30.0))
        blk = x[n - 365:n].sum() / (365 / 30.0)
        s = float(np.clip(ly / blk, 0.5, 2.0)) if blk > 0 else 1.0
        return _clip(np.full(h, t[-180:].mean() * s))
    return _f


def build_fast_methods(cat):
    """Every non-Prophet base method, for an item whose category series is `cat`."""
    m = {
        BAR: rolling_mean_fit_predict(30),
        "2-month avg": rolling_mean_fit_predict(60),
        "3-month avg": rolling_mean_fit_predict(90),
        "6-month avg": rolling_mean_fit_predict(180),
        "12-month avg": rolling_mean_fit_predict(365),
        "Weighted avg alpha 0.1": ewma_fit_predict(0.1),
        "Weighted avg alpha 0.05": ewma_fit_predict(0.05),
        "Weighted avg alpha 0.02": ewma_fit_predict(0.02),
        "TSB 0.1/0.1": tsb_fit_predict(0.1, 0.1),
        TSB05: tsb_fit_predict(0.05, 0.05),
        "TSB 0.02/0.02": tsb_fit_predict(0.02, 0.02),
        "Croston": croston_fit_predict(),
        "SBA": sba_fit_predict(),
        "6-month avg, spikes capped 95th": _winsor(180, 0.95),
        "6-month avg, spikes capped 99th": _winsor(180, 0.99),
        "Median of last 6 months": _block_stat("median"),
        "Trimmed mean of last 6 months": _block_stat("trimmed"),
        TD30: topdown_share_fit_predict(cat, 180, 30),
        "Top-down: category 6-mo avg x item share of last 90d": topdown_share_fit_predict(cat, 180, 90),
        "Top-down: category 6-mo avg x item share of last 365d": topdown_share_fit_predict(cat, 180, 365),
        "6-mo avg x category momentum (3-mo / 6-mo)": _momentum(cat, 180, 180),
        "12-mo avg x category momentum (3-mo / 12-mo)": _momentum(cat, 365, 365),
        "Same 30 days last year (seasonal naive)": _seasonal(cat, "naive"),
        "6-mo avg x item seasonal index (same window last year)": _seasonal(cat, "item"),
        "6-mo avg x category seasonal index (same window last year)": _seasonal(cat, "category"),
    }
    return m


PROPHET_VARIANTS = {          # name -> (yearly, growth, calendar columns)
    SHIPPED: (True, "linear", list(CALENDAR_REGRESSORS)),
    "Prophet, no yearly": (False, "linear", list(CALENDAR_REGRESSORS)),
    "Prophet, flat trend, no yearly": (False, "flat", CAL5),
    "Prophet, flat trend + yearly": (True, "flat", CAL5),
}

# name -> [(base method, weight)], fixed and declared up front
BLENDS = {
    "50/50: Prophet + repeat last month": [(SHIPPED, .5), (BAR, .5)],
    "50/50: Prophet + 6-month avg": [(SHIPPED, .5), ("6-month avg", .5)],
    "50/50: Prophet + TSB": [(SHIPPED, .5), (TSB05, .5)],
    "25/75: Prophet + 6-month avg": [(SHIPPED, .25), ("6-month avg", .75)],
    "75/25: Prophet + 6-month avg": [(SHIPPED, .75), ("6-month avg", .25)],
    "1/3 each: Prophet + 6-month avg + TSB": [(SHIPPED, 1 / 3), ("6-month avg", 1 / 3), (TSB05, 1 / 3)],
    LEADER: [(TD30, .5), (TSB05, .5)],
    "50/50: top-down + repeat last month": [(TD30, .5), (BAR, .5)],
    "1/3 each: top-down + TSB + repeat last month": [(TD30, 1 / 3), (TSB05, 1 / 3), (BAR, 1 / 3)],
    "50/50: Prophet (no yearly) + TSB": [("Prophet, no yearly", .5), (TSB05, .5)],
    "50/50: Prophet (no yearly) + top-down": [("Prophet, no yearly", .5), (TD30, .5)],
    "50/50: repeat last month + 6-month avg": [(BAR, .5), ("6-month avg", .5)],
    "50/50: 6-month avg + 12-month avg": [("6-month avg", .5), ("12-month avg", .5)],
    "50/50: 6-month avg + TSB": [("6-month avg", .5), (TSB05, .5)],
    "50/50: repeat last month + TSB": [(BAR, .5), (TSB05, .5)],
    "1/3 each: 3 + 6 + 12-month avg": [("3-month avg", 1 / 3), ("6-month avg", 1 / 3), ("12-month avg", 1 / 3)],
    "25/75: repeat last month + 6-month avg": [(BAR, .25), ("6-month avg", .75)],
    "75/25: repeat last month + 6-month avg": [(BAR, .75), ("6-month avg", .25)],
}


def family(name):
    if name.startswith(("50/50", "25/75", "75/25", "1/3 each")):
        return "Blend"
    if name.startswith("Prophet"):
        return "Prophet"
    if name.startswith(("Top-down", "6-mo avg x", "12-mo avg x", "Same 30 days")):
        return "Category / season"
    if name.startswith(("TSB", "Croston", "SBA")):
        return "Intermittent"
    return "Trailing average"


# ---------------------------------------------------------- scoring
def rows_for(pid, methods):
    v = G["daily"][pid].to_numpy(float)
    folds = make_folds(v.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)
    out = []
    for name, fn in methods.items():
        for r in walk_forward_evaluate(pid, v, fn, name, folds=folds).rows:
            out.append((pid, r["fold"], name, r["actual_30d"], r["pred_30d"], r["naive_scale"]))
    return out


def fast_job(pid):
    return rows_for(pid, build_fast_methods(G["cats"][G["cat_of"][pid]]))


def prophet_job(pid):
    from forecasting.prophet_model import prophet_fit_predict
    cal = G["cal_p"]
    methods = {name: prophet_fit_predict(G["idx"], cal[cols], name, weekly=True, yearly=yearly,
                                         growth=growth)
               for name, (yearly, growth, cols) in PROPHET_VARIANTS.items()}
    return rows_for(pid, methods)


def add_blends(base):
    P = base.pivot_table(index=["pid", "fold"], columns="method", values="pred", aggfunc="first")
    act = base.drop_duplicates(["pid", "fold"]).set_index(["pid", "fold"])[["actual", "scale"]]
    out = {}
    for name, parts in BLENDS.items():
        if all(m in P.columns for m, _ in parts):
            out[name] = sum(w * P[m] for m, w in parts)
    B = pd.DataFrame(out).stack().rename("pred").reset_index().rename(columns={"level_2": "method"})
    return B.merge(act.reset_index(), on=["pid", "fold"])[["pid", "fold", "method", "actual", "pred", "scale"]]


def item_scale(df):
    """step4's rule: the mean of an item's folds' naive scales, zero / NaN folds left out."""
    return df.groupby("pid").scale.apply(lambda s: s[np.isfinite(s) & (s > 0)].mean())


def cohort_table(df, items=None, suffix=""):
    """One row per method: MASE (mean / median over items), items below 1, pooled WMAPE, bias."""
    d = df if items is None else df[df.pid.isin(items)]
    d = d.assign(err=(d.actual - d.pred).abs())
    scale = item_scale(d)
    valid = scale[scale > 0].dropna().index
    bar = d[d.method == BAR].groupby("pid").err.mean() if (d.method == BAR).any() else None
    rows = []
    for m, g in d.groupby("method", sort=False):
        mae = g.groupby("pid").err.mean()
        mase = (mae / scale).loc[valid]
        rows.append({"method": m,
                     "mean_MASE" + suffix: mase.mean(), "median_MASE" + suffix: mase.median(),
                     "items_below_1" + suffix: int((mase < 1).sum()),
                     "pooled_WMAPE_pct" + suffix: 100 * g.err.sum() / g.actual.sum(),
                     "bias_pct" + suffix: 100 * (g.pred.sum() - g.actual.sum()) / g.actual.sum(),
                     "items_beating_repeat_last_month" + suffix:
                         int((mae.loc[valid] < bar.loc[valid]).sum()) if (bar is not None and m != BAR) else 0,
                     "n_items" + suffix: len(mase)})
    return pd.DataFrame(rows).set_index("method")


def mase_matrix(df, items=None):
    """method x item MASE."""
    d = df if items is None else df[df.pid.isin(items)]
    d = d.assign(err=(d.actual - d.pred).abs())
    scale = item_scale(d)
    valid = scale[scale > 0].dropna().index
    return (d.groupby(["method", "pid"]).err.mean().unstack() / scale).loc[:, valid]


def boot(mm, a, b, draws):
    """Mean MASE difference a - b over items, with a 95% interval over resampled items."""
    d = mm.loc[a].to_numpy() - mm.loc[b].to_numpy()
    means = d[draws].mean(axis=1)
    return dict(mean_diff=d.mean(), ci_lo=np.percentile(means, 2.5), ci_hi=np.percentile(means, 97.5),
                p_better=(means < 0).mean(), items_better=int((d < 0).sum()), n_items=len(d))


# ------------------------------------------------------------ shape
def cat_shape_job(a):
    c, fold_i, n = a
    idx = G["idx"]
    v, dates = G["cats"][c][:n], idx[n:n + HORIZON]
    try:
        p = prophet_shape(v, idx[:n], G["cal_s"], dates)
    except Exception:
        p = None
    return c, fold_i, _norm(p) if p is not None else None, _norm(weekday_shape(v, idx[:n], G["cal_s"], dates))


def _norm(p, h=HORIZON):
    p = np.maximum(np.asarray(p, float), 0.0)
    return p / p.sum() if np.isfinite(p).all() and p.sum() > 0 else np.full(h, 1.0 / h)


def item_shape_init(cs, tot):
    global G
    G = load()
    G["catshape"], G["tot"] = cs, tot


def item_shape_job(pid):
    idx, cal = G["idx"], G["cal_s"]
    v = G["daily"][pid].to_numpy(float)
    c = G["cat_of"][pid]
    flat = np.full(HORIZON, 1.0 / HORIZON)
    rows = []
    for f in make_folds(v.size, HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN):
        n = f.train_end
        dates = idx[n:n + HORIZON]
        act = v[f.test_start:f.test_end]
        T = G["tot"].get((pid, f.fold_index), np.nan)
        if not np.isfinite(T):
            continue
        cp, cw = G["catshape"].get((c, f.fold_index), (None, None))
        sh = {"Flat line": flat,
              "Category's Prophet pattern": cp if cp is not None else flat,
              "Category's weekday pattern": cw if cw is not None else flat,
              "Item's own weekday pattern": _norm(weekday_shape(v[:n], idx[:n], cal, dates))}
        try:
            sh["Item's own Prophet pattern"] = (_norm(prophet_shape(v[:n], idx[:n], cal, dates))
                                                if v[:n].sum() > 0 else flat)
        except Exception:
            sh["Item's own Prophet pattern"] = flat
        brk = float(cal["is_sem_break"].reindex(dates).fillna(0).sum())
        for name, w in sh.items():
            rows.append((pid, f.fold_index, name, np.abs(act - T * w).mean(), act.sum(), brk))
    return rows


def run_shape(jobs, fast, leader_total, live):
    idx = G["idx"]
    cat_jobs = [(c, f.fold_index, f.train_end) for c in G["cats"]
                for f in make_folds(len(idx), HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN)]
    with Pool(jobs, initializer=init) as pool:
        res = pool.map(cat_shape_job, cat_jobs)
    cs = {(c, fi): (cp, cw) for c, fi, cp, cw in res}
    with Pool(jobs, initializer=item_shape_init, initargs=(cs, leader_total)) as pool:
        out = [r for rows in pool.imap_unordered(item_shape_job, fast) for r in rows]
    df = pd.DataFrame(out, columns=["pid", "fold", "shape", "daily_mae", "act_total", "break_days"])
    res = []
    for label, sub in (("all Fast items", df), ("items still selling", df[df.pid.isin(live)])):
        fl = sub[sub["shape"] == "Flat line"].set_index(["pid", "fold"])
        for s, g in sub.groupby("shape", sort=False):
            g = g.set_index(["pid", "fold"])
            b = fl.loc[g.index]
            brk = (g.break_days >= 5).to_numpy()
            res.append(dict(cohort=label, shape=s,
                            daily_error_vs_flat=g.daily_mae.sum() / b.daily_mae.sum(),
                            item_folds_beating_flat_pct=100 * (g.daily_mae < b.daily_mae - 1e-12).mean(),
                            break_months=g.daily_mae[brk].sum() / b.daily_mae[brk].sum(),
                            other_months=g.daily_mae[~brk].sum() / b.daily_mae[~brk].sum(),
                            n_item_folds=len(g)))
    return df, pd.DataFrame(res)


# --------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("Why this exists")[0].strip())
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--no-prophet", action="store_true", help="skip every Prophet row and the shape test")
    ap.add_argument("--skip-shape", action="store_true", help="skip the day-by-day shape test (~2 min)")
    args = ap.parse_args()
    t0 = time.time()
    init()
    fast, idx = G["fast"], G["idx"]
    print("%d Fast items, %d days (%s .. %s), %d categories" %
          (len(fast), len(idx), idx[0].date(), idx[-1].date(), len(G["cats"])), flush=True)

    rows = [r for pid in fast for r in fast_job(pid)]
    print("  trailing / intermittent / category methods scored (%.0fs)" % (time.time() - t0), flush=True)
    if not args.no_prophet:
        with Pool(args.jobs, initializer=init) as pool:
            for i, r in enumerate(pool.imap_unordered(prophet_job, fast), 1):
                rows.extend(r)
                if i % 15 == 0:
                    print("  Prophet variants: %d/%d items (%.0fs)" % (i, len(fast), time.time() - t0), flush=True)
    base = pd.DataFrame(rows, columns=["pid", "fold", "method", "actual", "pred", "scale"])
    full = pd.concat([base, add_blends(base)], ignore_index=True)
    methods = list(dict.fromkeys(full.method))
    print("%d methods x %d item-folds" % (len(methods), len(full) // len(methods)), flush=True)

    # The leading blend is assembled here from its two parts; production calls
    # forecasting.topdown.topdown_tsb_fit_predict. They must be the same numbers.
    prod_rows = pd.DataFrame(
        [r for pid in fast for r in rows_for(pid, {"prod": topdown_tsb_fit_predict(G["cats"][G["cat_of"][pid]])})],
        columns=["pid", "fold", "method", "actual", "pred", "scale"]).set_index(["pid", "fold"]).pred
    mine_l = full[full.method == LEADER].set_index(["pid", "fold"]).pred
    gap = (mine_l - prod_rows.reindex(mine_l.index)).abs().max()
    assert gap < 1e-9, "the blend scored here differs from topdown_tsb_fit_predict (max gap %.3g)" % gap
    print("Leading blend == forecasting.topdown.topdown_tsb_fit_predict on every item-fold (max gap %.1e)" % gap)

    # ----- cohorts
    act = full.drop_duplicates(["pid", "fold"]).groupby("pid").actual.sum()
    live = list(act[act > 0].index)
    dead = list(act[act == 0].index)
    print("Items with no sale in any test window: %d (still selling: %d)" % (len(dead), len(live)))
    nfold = full.fold.max() + 1
    tab = cohort_table(full)
    tab = tab.join(cohort_table(full, live, "_still_selling")[
        ["mean_MASE_still_selling", "median_MASE_still_selling", "items_below_1_still_selling",
         "pooled_WMAPE_pct_still_selling", "bias_pct_still_selling", "n_items_still_selling"]])
    old = cohort_table(full[full.fold < nfold // 2])["mean_MASE"].rename("mean_MASE_older_folds")
    new = cohort_table(full[full.fold >= nfold // 2])["mean_MASE"].rename("mean_MASE_newer_folds")
    tab = tab.join(old).join(new)
    tab["rank_all"] = tab.mean_MASE.rank(method="min").astype(int)
    tab["rank_older_folds"] = tab.mean_MASE_older_folds.rank(method="min").astype(int)
    tab["rank_newer_folds"] = tab.mean_MASE_newer_folds.rank(method="min").astype(int)

    # ----- bootstrap against the shipped Prophet, both cohorts
    rng = np.random.default_rng(BOOT_SEED)
    mm_all, mm_live = mase_matrix(full), mase_matrix(full, live)
    d_all = rng.integers(0, mm_all.shape[1], size=(N_BOOT, mm_all.shape[1]))
    d_live = rng.integers(0, mm_live.shape[1], size=(N_BOOT, mm_live.shape[1]))
    have_prophet = SHIPPED in mm_all.index
    if have_prophet:
        for m in methods:
            for tag, mm, dr in (("", mm_all, d_all), ("_still_selling", mm_live, d_live)):
                b = boot(mm, m, SHIPPED, dr)
                tab.loc[m, "vs_prophet_mean_MASE_diff" + tag] = b["mean_diff"]
                tab.loc[m, "vs_prophet_ci_lo" + tag] = b["ci_lo"]
                tab.loc[m, "vs_prophet_ci_hi" + tag] = b["ci_hi"]
                tab.loc[m, "items_better_than_prophet" + tag] = b["items_better"]
    tab.insert(0, "family", [family(m) for m in tab.index])
    tab = tab.sort_values("mean_MASE")
    tab.reset_index().to_csv(os.path.join(DATA, "item_forecast_method_test.csv"), index=False,
                             lineterminator="\n", float_format="%.4f")
    full.round(4).to_csv(os.path.join(DATA, "item_forecast_method_folds.csv"), index=False, lineterminator="\n")

    # ----- pairwise bootstrap among the leaders
    rob = []
    pairs = [(LEADER, TD30), (LEADER, TSB05), (LEADER, "TSB 0.1/0.1"), (LEADER, "50/50: repeat last month + TSB"),
             (LEADER, BAR), (LEADER, SHIPPED), (TD30, SHIPPED), (TSB05, SHIPPED), (BAR, SHIPPED),
             ("6-month avg", SHIPPED), ("Prophet, no yearly", SHIPPED)]
    for a, b in pairs:
        if a not in mm_all.index or b not in mm_all.index:
            continue
        for label, mm, dr in (("all Fast items", mm_all, d_all), ("items still selling", mm_live, d_live)):
            rob.append(dict(method=a, versus=b, cohort=label, **boot(mm, a, b, dr)))
    pd.DataFrame(rob).to_csv(os.path.join(DATA, "item_forecast_robustness.csv"), index=False,
                             lineterminator="\n", float_format="%.4f")

    # ----- sensitivity of the leading blend
    sens = []
    tsbs = {"TSB 0.02/0.02": tsb_fit_predict(.02, .02), TSB05: tsb_fit_predict(.05, .05),
            "TSB 0.1/0.1": tsb_fit_predict(.1, .1)}
    settings = [(180, 14), (180, 30), (180, 60), (180, 90), (90, 30), (365, 30)]
    for pid in fast:
        cat = G["cats"][G["cat_of"][pid]]
        ms = {}
        for lw, sw in settings:
            td = topdown_share_fit_predict(cat, lw, sw)
            ms["top-down (level %dd, share %dd)" % (lw, sw)] = td
            for tn, tf in tsbs.items():
                ms["50/50: top-down (level %dd, share %dd) + %s" % (lw, sw, tn)] = blend_fit_predict([td, tf])
        sens.extend(rows_for(pid, ms))
    sd = pd.DataFrame(sens, columns=["pid", "fold", "method", "actual", "pred", "scale"])
    st = cohort_table(sd).join(cohort_table(sd, live, "_still_selling")[
        ["mean_MASE_still_selling", "pooled_WMAPE_pct_still_selling"]])
    st.reset_index().to_csv(os.path.join(DATA, "item_forecast_sensitivity.csv"), index=False,
                            lineterminator="\n", float_format="%.4f")

    # ----- by window
    if have_prophet:
        cal = G["cal_s"]
        w_rows = []
        for f in make_folds(len(idx), HORIZON, MIN_FOLDS, MAX_FOLDS, MIN_TRAIN):
            win = cal.reindex(idx[f.test_start:f.test_end]).fillna(0)
            g = full[full.fold == f.fold_index]
            r = dict(fold=f.fold_index, start=idx[f.test_start].date(), end=idx[f.test_end - 1].date(),
                     enrollment_days=int(win.is_enrollment_period.sum()), break_days=int(win.is_sem_break.sum()),
                     exam_days=int(win.is_exam_week.sum()), event_days=int(win.is_event_day.sum()),
                     actual_units=g[g.method == LEADER].actual.sum())
            for m, lab in ((SHIPPED, "prophet"), (LEADER, "blend"), (BAR, "repeat_last_month")):
                gm = g[g.method == m]
                r[lab + "_WMAPE_pct"] = 100 * (gm.actual - gm.pred).abs().sum() / gm.actual.sum()
                r[lab + "_bias_pct"] = 100 * (gm.pred.sum() - gm.actual.sum()) / gm.actual.sum()
            w_rows.append(r)
        pd.DataFrame(w_rows).to_csv(os.path.join(DATA, "item_forecast_by_window.csv"), index=False,
                                    lineterminator="\n", float_format="%.2f")

    # ----- console report
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 58)
    show = ["family", "mean_MASE", "median_MASE", "items_below_1", "pooled_WMAPE_pct", "bias_pct",
            "mean_MASE_still_selling", "rank_older_folds", "rank_newer_folds"]
    print("\n" + tab[show].round(3).to_string())
    rho = tab.rank_older_folds.corr(tab.rank_newer_folds, method="spearman")
    print("\nRank correlation, older folds vs newer folds: %.2f" % rho)
    if have_prophet:
        print("Leading blend vs shipped Prophet, mean MASE gap %.3f (95%% CI %.3f to %.3f), better on %d of %d items"
              % (tab.loc[LEADER, "vs_prophet_mean_MASE_diff"], tab.loc[LEADER, "vs_prophet_ci_lo"],
                 tab.loc[LEADER, "vs_prophet_ci_hi"], tab.loc[LEADER, "items_better_than_prophet"],
                 mm_all.shape[1]))
        print("  on the %d items still selling: gap %.3f (CI %.3f to %.3f), better on %d"
              % (mm_live.shape[1], tab.loc[LEADER, "vs_prophet_mean_MASE_diff_still_selling"],
                 tab.loc[LEADER, "vs_prophet_ci_lo_still_selling"], tab.loc[LEADER, "vs_prophet_ci_hi_still_selling"],
                 tab.loc[LEADER, "items_better_than_prophet_still_selling"]))

    # ----- check against what step4 stored in the database
    stored = G["stored"].set_index("product_id")
    sm = G["stored_model"]
    ref = None
    # The calendar-adjusted default is not one of the 47 methods scored here;
    # scripts/test_calendar_adjustment.py covers it and matches it instead.
    if sm and sm[0].startswith("topdown_tsb") and "+calendar" not in sm[0]:
        ref = LEADER
    elif sm and sm[0] == "prophet":
        ref = SHIPPED
    if ref and ref in full.method.values:
        mine = full[full.method == ref].assign(err=lambda d: (d.actual - d.pred).abs()).groupby("pid").err.mean()
        diff = (mine - stored["mae"].reindex(mine.index)).abs().max()
        print("\nCheck against Result_Forecast_Metrics (model %s): max |MAE difference| over %d items = %.6f -> %s"
              % (sm[0], len(mine), diff, "MATCH" if diff < 1e-6 else "DIFFERENT"))
    else:
        print("\n(stored model is %s; no reference method to check against)" % (sm,))

    # ----- shape
    if have_prophet and not args.skip_shape:
        print("\nDay-by-day shape test ...", flush=True)
        lt = full[full.method == LEADER].set_index(["pid", "fold"]).pred
        sdf, sres = run_shape(args.jobs, fast, lt.to_dict(), live)
        sdf.to_csv(os.path.join(DATA, "item_forecast_shape_folds.csv"), index=False, lineterminator="\n",
                   float_format="%.5f")
        sres.to_csv(os.path.join(DATA, "item_forecast_shape_test.csv"), index=False, lineterminator="\n",
                    float_format="%.4f")
        print(sres.round(3).to_string(index=False))
    print("\nDone in %.0fs. Wrote data/item_forecast_*.csv" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
