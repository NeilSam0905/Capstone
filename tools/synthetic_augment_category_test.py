"""
tools/synthetic_augment_category_test.py
------------------------------------------------------------------
Does synthetic training history help the CATEGORY-level models?

Two earlier experiments asked a version of this question and both were
answered at SKU level:

  SPARSE_DEMAND_EXPERIMENTS.md #4  prepended 5 synthetic years, scored 7
      WINDOW methods, found nothing. It could not have found anything:
      six of the seven only ever read a fixed trailing window, so
      history behind that window is invisible BY CONSTRUCTION.

  tools/synthetic_augment_ml_test.py  asked it again of the learners,
      where sample size plausibly binds. Answer: the learners improved
      9-15% on MAE and still lost to rolling_mean_30. Regularisation,
      not new information.

Neither touched the category level, and the category level is where the
question is live for this project - scripts/benchmark_category_level.py
newly makes ARIMA, SARIMA and Prophet viable, and Prophet is the
manuscript's headline model (section 3.3.2). Those are full-history
models. If data volume binds anywhere in this repo, it binds there.

What can and cannot move, decided before the run
------------------------------------------------
make_folds(821, 30, 3, 12, 60) puts every fold's origin at 461 days or
later, so the smallest training slice any method ever sees is 461 REAL
days. Every window method in the category benchmark reads at most 180
days (RM6_6month_180d, the current winner). Those windows sit entirely
inside real data in every arm, so ten of the twenty-seven methods -
including the incumbent - are arithmetically incapable of moving. They
are kept in the run precisely for that reason: if any of their numbers
shifts by more than float noise, the harness is broken and every other
row is void.

That also sets the bar. The question is not "does augmentation change
anything" - it is whether it lifts some full-history model past
RM6_6month_180d's 49.30% pooled WMAPE, which augmentation cannot touch.

Three generators, increasingly realistic
----------------------------------------
  iid       weekday-stratified bootstrap from tools/synthetic_augment_test.py,
            reused unchanged. Keeps the per-weekday zero rate and nonzero
            size distribution; destroys all autocorrelation.
  block-14  moving-block bootstrap from tools/synthetic_augment_ml_test.py,
            reused unchanged. Runs of zeros and clusters of sales survive.
  calendar  new here. Resamples whole store-days keyed on
            (weekday, academic-calendar state), so the synthetic
            prehistory carries the semester rhythm the other two flatten
            - the specific defect SPARSE_DEMAND_EXPERIMENTS.md #4 flagged
            and did not fix. It also resamples the same source day across
            all 12 categories at once, preserving the common-mode zero
            structure (49.3% of days are store-wide zeros; the categories
            go quiet together, not independently). It differs from
            block-14 in two ways rather than one, both toward realism -
            deliberately, because this arm is the best case for
            augmentation, not a controlled contrast against block-14.

All three augmented arms are handed the SAME extended calendar, so
prophet_cal has populated regressors in every arm and the arms differ
only in how demand was generated.

The honesty guarantee, unchanged
--------------------------------
Synthetic days are PREPENDED, never appended and never substituted.
make_folds lays test windows out backward from the END of the array, so
every fold's actual_30d is the same real observed data in every arm.
Only the training slice grows. The scorer never sees a synthetic value.

Read-only: touches no table, opens ustore.db read-only for the calendar,
and writes one CSV under data/.

Run: python tools/synthetic_augment_category_test.py [--years N] [--quick]
------------------------------------------------------------------
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
import time
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

_spec = importlib.util.spec_from_file_location(
    "_cat", os.path.join(ROOT, "scripts", "benchmark_category_level.py"))
cat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cat)

from forecasting.evaluate import evaluate_methods
from forecasting.prophet_model import CALENDAR_REGRESSORS, load_calendar
from synthetic_augment_test import bootstrap_prehistory      # reuse, do not reimplement
from synthetic_augment_ml_test import block_prehistory       # reuse, do not reimplement

SEED = 20260909
OUT = os.path.join(ROOT, "data", "synthetic_augment_category.csv")

# Windows <= 180 days sit inside the smallest (461-day) real training
# slice, so these cannot move. Named here so the invariant check below
# is a declared expectation rather than a discovered one.
IMMUNE = {
    "naive", "seasonal_naive_7", "rolling_mean_14", "rolling_mean_30",
    "rolling_mean_60", "RM3_3month_90d", "RM6_6month_180d",
    "rolling_median_30", "rolling_q75_30", "weekly_hurdle_12w",
}


# ---------------------------------------------------------------- calendar
def extend_index(index, n_days):
    """n_days of real calendar dates immediately before index[0], + index."""
    idx = pd.DatetimeIndex(index)
    pre = pd.date_range(end=idx[0] - pd.Timedelta(days=1), periods=n_days, freq="D")
    return pre.append(idx)


def extended_calendar(con, ext_index):
    """Dim_Date flags over the extended index.

    Dim_Date covers 2023-01-01..2026-12-31. Prehistory that runs behind
    2023-01-01 has no published calendar, so it is filled by tiling the
    known calendar backward in 364-day (52-week) steps - the shortest
    shift that preserves BOTH weekday alignment and the annual academic
    rhythm. That is a fabrication and is labelled as one; it is the
    minimum needed to stop prophet_cal being handed a prehistory of
    all-zero regressors, which would itself be a fabrication (asserting
    the university had no semesters before 2023).
    """
    real = load_calendar(con, ext_index)                 # NaN -> 0 outside Dim_Date
    known = pd.read_sql_query(
        "SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CALENDAR_REGRESSORS),
        con, parse_dates=["calendar_date"]).set_index("calendar_date")
    first_known = known.index.min()
    gap = ext_index < first_known
    n_tiled = int(gap.sum())
    if n_tiled:
        for d in ext_index[gap]:
            src = d
            while src < first_known:
                src += pd.Timedelta(days=364)
            if src in known.index:
                real.loc[d, :] = known.loc[src].to_numpy(float)
    return real, n_tiled


def calendar_state(cal):
    """One compact ordered label per day, most-specific flag wins."""
    s = np.full(len(cal), "regular", dtype=object)
    for col, lab in (("is_event_day", "event"), ("is_exam_week", "exam"),
                     ("is_enrollment_period", "enroll"), ("is_sem_break", "break"),
                     ("is_store_closed", "closed")):
        if col in cal.columns:
            s[cal[col].to_numpy(float) > 0] = lab
    return s


def calendar_prehistory(series, real_cal, syn_cal, rng, min_bucket=5):
    """Joint whole-day resample keyed on (weekday, calendar state).

    Returns {key: prehistory array}. The SAME sampled source-day index is
    applied to every category, so the common-mode zero pattern and the
    cross-category correlation both survive into the prehistory.

    A (weekday, state) bucket thinner than min_bucket real days backs off
    to state-only, then weekday-only, then the whole series - so the
    sampler is always well defined and never draws from a 1-day bucket.
    """
    real_key = list(zip(pd.DatetimeIndex(real_cal.index).weekday, calendar_state(real_cal)))
    syn_key = list(zip(pd.DatetimeIndex(syn_cal.index).weekday, calendar_state(syn_cal)))

    pos = {}
    for i, k in enumerate(real_key):
        pos.setdefault(k, []).append(i)
    by_state, by_wd = {}, {}
    for i, (wd, st) in enumerate(real_key):
        by_state.setdefault(st, []).append(i)
        by_wd.setdefault(wd, []).append(i)
    allpos = np.arange(len(real_key))

    picks = np.empty(len(syn_key), dtype=int)
    for j, (wd, st) in enumerate(syn_key):
        cand = pos.get((wd, st), [])
        if len(cand) < min_bucket:
            cand = by_state.get(st, [])
        if len(cand) < min_bucket:
            cand = by_wd.get(wd, [])
        if len(cand) < min_bucket:
            cand = allpos
        picks[j] = rng.choice(np.asarray(cand))
    return {k: np.asarray(v, dtype=float)[picks] for k, v in series.items()}


# ------------------------------------------------------------------- arms
def build_arm(series, index, years, mode, con, rng):
    """Return (data, ext_index, ext_calendar, n_tiled) for one arm."""
    if mode is None:
        cal = load_calendar(con, index)
        return series, pd.DatetimeIndex(index), cal, 0

    n_days = int(round(365 * years))
    ext_index = extend_index(index, n_days)
    ext_cal, n_tiled = extended_calendar(con, ext_index)
    syn_cal = ext_cal.iloc[:n_days]
    real_cal = ext_cal.iloc[n_days:]

    if mode == "calendar":
        pre = calendar_prehistory(series, real_cal, syn_cal, rng)
    else:
        start_wd = int(pd.Timestamp(index[0]).weekday())
        pre = {k: (bootstrap_prehistory(v, start_wd, n_days, rng) if mode == "iid"
                   else block_prehistory(v, n_days, rng))
               for k, v in series.items()}

    data = {k: np.concatenate([pre[k], v]) for k, v in series.items()}
    return data, ext_index, ext_cal, n_tiled


def build_methods(ext_index, ext_cal, quick):
    """The category benchmark's own method set, plus Prophet bound to
    this arm's index/calendar. Reusing cat.build_methods is what makes
    the 'real only' arm reproduce data/category_benchmark_summary.csv."""
    m, missing = cat.build_methods(with_ml=not quick, with_stats=True)
    if quick:
        for k in ("arima_212", "sarima_weekly"):
            m.pop(k, None)
    try:
        from forecasting.prophet_model import prophet_fit_predict
        m["prophet_plain"] = prophet_fit_predict(ext_index, None, "prophet_plain")
        m["prophet_cal"] = prophet_fit_predict(ext_index, ext_cal, "prophet_cal")
    except ImportError as exc:
        missing["prophet"] = str(exc)
    return m, missing


def oracle_floor(series, horizon, min_folds, max_folds, min_train):
    """Pooled WMAPE of the best possible FLAT forecast per series.

    The constant is the median of that series' own realised 30-day
    totals - chosen with hindsight from the test data itself, which no
    real method may do. Every method in the category benchmark emits a
    level, so this is a hard lower bound on all of them, and it is
    unaffected by augmentation (it reads only the test windows).

    Reported because it is what makes the result interpretable: an
    augmented model that closes 3% of a gap is a different story
    depending on whether the remaining gap is 40 points or 8.
    """
    from forecasting.evaluate import make_folds
    per, act_all, pred_all = [], [], []
    for k, v in series.items():
        folds = make_folds(len(v), horizon, min_folds, max_folds, min_train)
        if not folds:
            continue
        a = np.array([np.asarray(v, float)[f.origin:f.origin + horizon].sum()
                      for f in folds])
        c = np.median(a)
        per.append({"series": k, "mean_30d": a.mean(), "sd_30d": a.std(),
                    "cv": a.std() / max(a.mean(), 1e-9),
                    "oracle_const_wmape_pct": 100 * np.abs(a - c).sum() / max(a.sum(), 1e-9)})
        act_all.append(a)
        pred_all.append(np.full(a.size, c))
    A, P = np.concatenate(act_all), np.concatenate(pred_all)
    return pd.DataFrame(per), 100 * np.abs(A - P).sum() / A.sum()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--quick", action="store_true",
                    help="drop ML + the slow ARIMAs for a smoke run")
    args = ap.parse_args()
    warnings.simplefilter("ignore")

    series_df = pd.read_csv(os.path.join(ROOT, cat.SERIES_CSV),
                            index_col=0, parse_dates=True)
    series = {c: series_df[c].to_numpy(float) for c in series_df.columns}
    index = series_df.index
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "ustore.db"), uri=True)

    print("=" * 96)
    print("Does synthetic training history help the CATEGORY-level models?")
    print("=" * 96)
    print("%d categories | real history %d days (%s .. %s)"
          % (len(series), len(index), index[0].date(), index[-1].date()))
    print("Prepending %s synthetic years (%d days) under three generators."
          % (args.years, int(365 * args.years)))
    print("Test windows are identical real data in every arm - "
          "the scorer never sees a synthetic value.\n")

    rows = []
    for label, mode in (("real only", None), ("+synthetic (iid)", "iid"),
                        ("+synthetic (block-14)", "block"),
                        ("+synthetic (calendar)", "calendar")):
        rng = np.random.default_rng(SEED)          # same seed per arm
        data, ext_index, ext_cal, n_tiled = build_arm(
            series, index, args.years, mode, con, rng)
        methods, missing = build_methods(ext_index, ext_cal, args.quick)
        n_days = len(next(iter(data.values())))

        t0 = time.time()
        res, insufficient = evaluate_methods(
            data, methods, cat.HORIZON, cat.MIN_FOLDS, cat.MAX_FOLDS, cat.MIN_TRAIN)
        layouts = res.groupby(["sku", "method"])["origin"].apply(lambda s: tuple(sorted(s)))
        assert (layouts.groupby("sku").nunique() == 1).all(), \
            "[%s] methods were NOT scored on identical folds" % label

        summary, _ = cat.summarise(res, label)
        summary["arm"] = label
        summary["series_days"] = n_days
        summary["synthetic_days"] = n_days - len(index)
        summary["calendar_days_tiled"] = n_tiled
        rows.append(summary)
        print("  [%-22s] %5d days/series, %2d methods, %6d predictions, %5.0fs%s"
              % (label, n_days, len(methods), len(res), time.time() - t0,
                 (", %d calendar days tiled" % n_tiled) if n_tiled else ""))
        if insufficient:
            print("      insufficient history: %s" % list(insufficient))
    con.close()

    out = pd.concat(rows, ignore_index=True)
    out.to_csv(OUT, index=False, lineterminator="\n")

    order = ["real only", "+synthetic (iid)", "+synthetic (block-14)",
             "+synthetic (calendar)"]
    wide = out.pivot_table(index="method", columns="arm",
                           values=["mae", "rmse", "wmape_pooled_pct"])

    # ---- gate: the immune methods must not have moved ----------------
    print("\n" + "=" * 96)
    print("HARNESS GATE - methods whose window sits inside the real data CANNOT move")
    print("=" * 96)
    worst, worst_m = 0.0, None
    for m in wide.index:
        if m not in IMMUNE:
            continue
        base = wide[("mae", "real only")][m]
        for a in order[1:]:
            if (("mae", a) in wide.columns) and np.isfinite(wide[("mae", a)][m]):
                d = abs(wide[("mae", a)][m] - base)
                if d > worst:
                    worst, worst_m = d, "%s / %s" % (m, a)
    tol = 1e-9
    verdict = "PASS" if worst <= tol else "FAIL"
    print("[%s] %d immune methods x %d augmented arms | largest MAE drift %.2e%s"
          % (verdict, len(IMMUNE & set(wide.index)), len(order) - 1, worst,
             ("  (%s)" % worst_m) if worst_m else ""))
    if verdict == "FAIL":
        print("       Augmentation leaked into a fixed-window method. "
              "Every number below is void.")

    def table(metric):
        t = pd.DataFrame(index=wide.index)
        for a in order:
            if (metric, a) in wide.columns:
                t[a] = wide[(metric, a)]
        for a in order[1:]:
            if (metric, a) in wide.columns:
                t["chg%% %s" % a.replace("+synthetic ", "")] = (
                    100.0 * (wide[(metric, a)] - wide[(metric, "real only")])
                    / wide[(metric, "real only")])
        t["immune"] = ["yes" if m in IMMUNE else "" for m in t.index]
        return t.sort_values("real only")

    pd.set_option("display.width", 220)
    for metric in ("wmape_pooled_pct", "mae", "rmse"):
        print("\n" + "=" * 96)
        print("%s by arm  (lower is better; negative chg%% = augmentation helped)"
              % metric.upper())
        print("=" * 96)
        print(table(metric).to_string(float_format=lambda x: "%8.2f" % x))

    # ---- the bar ------------------------------------------------------
    print("\n" + "=" * 96)
    print("THE BAR - can any augmented full-history model beat the incumbent?")
    print("=" * 96)
    inc = wide[("wmape_pooled_pct", "real only")].get("RM6_6month_180d", np.nan)
    print("RM6_6month_180d, real only, pooled WMAPE = %.2f%%  "
          "(immune to augmentation - the same number in every arm)" % inc)
    best_any = np.inf
    for a in order:
        col = ("wmape_pooled_pct", a)
        if col not in wide.columns:
            continue
        s = wide[col].drop(labels=[m for m in wide.index if m in IMMUNE],
                           errors="ignore")
        v, m = s.min(), s.idxmin()
        best_any = min(best_any, v)
        print("  best non-immune model, %-22s : %-20s %6.2f%%  (%s incumbent)"
              % (a, m, v, "BEATS" if v < inc else "loses to"))

    # ---- how much headroom existed in the first place -----------------
    per, floor = oracle_floor(series, cat.HORIZON, cat.MIN_FOLDS,
                              cat.MAX_FOLDS, cat.MIN_TRAIN)
    print("\n" + "=" * 96)
    print("THE FLOOR - best possible FLAT forecast, constant chosen with hindsight")
    print("=" * 96)
    print(per.round(2).to_string(index=False))
    print("\n  oracle-constant pooled WMAPE (unachievable lower bound) : %6.2f%%" % floor)
    print("  RM6_6month_180d, real only                              : %6.2f%%" % inc)
    print("  best augmented full-history model, any arm              : %6.2f%%" % best_any)
    print("\n  Total headroom for ANY level forecast: %.1f pp, and capturing it"
          % (inc - floor))
    print("  would require knowing each category's future mean in advance.")
    print("\nWrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
