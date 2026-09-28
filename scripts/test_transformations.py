"""
scripts/test_transformations.py
------------------------------------------------------------------
Do variance-stabilising transformations (log1p, square root, Yeo-Johnson) make
the forecasts more accurate?

Each candidate is fitted on the transformed daily series and its forecast is
transformed back, either naively (the inverse of the prediction) or with Duan
smearing (the average inverse of the prediction plus each in-sample residual,
the standard correction for the fact that the inverse of an average is not the
average of the inverse). Scored on the project's harness (30-day horizon, up to
12 folds, MASE scaled as step4/step4c store it), against the shipped models.

  CATEGORY (12)  references: 6-month average (unadjusted) | shipped (+ calendar)
                 180-day average on the transformed scale, naive / smeared
                 Prophet (weekly + 5 calendar flags) on raw / log1p / sqrt / Yeo-Johnson
                 Ridge (weekday + calendar flags) on the same four
                 50/50 unadjusted average + each smeared transformed Prophet
  ITEM (58)      references: blend (unadjusted) | shipped (+ calendar)
                 30- and 180-day averages on log1p / Yeo-Johnson, naive
                 Prophet (the old shipped item config) on the four, naive / smeared
                 50/50 unadjusted blend + each smeared transformed Prophet

Yeo-Johnson lambda is fitted (MLE) on each fold's own training slice, clipped to
[-1, 3]; back-transformed days are clipped to [0, 10 x the training max] so one
extreme lambda cannot produce infinity. Where that cap binds, the method's
numbers are still reported: blowing up is the finding.

Result (data/transformation_test.csv, transformation_checks.csv): nothing beats
the shipped models. A smeared back-transform of an average returns the plain
average exactly; a naive one under-forecasts by 60-80%. Square-root Prophet ties
the shipped category model, and is the best way to run Prophet on this data.
Yeo-Johnson fails: most category-days are zero, the fitted lambdas are all
negative, and their inverse either collapses or explodes.

Needs Prophet and scikit-learn; about 8 minutes on 4 cores.
Run:  python scripts/test_transformations.py [--jobs 4]
------------------------------------------------------------------
"""
import argparse
import logging
import os
import sqlite3
import sys
import time
import warnings
from multiprocessing import Pool

import numpy as np
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.baselines import rolling_mean_fit_predict
from forecasting.calendar_adjust import calendar_capped_fit_predict, load_day_types
from forecasting.evaluate import aggregate_blocks, make_folds
from forecasting.metrics import naive_scale
from forecasting.topdown import topdown_tsb_fit_predict

H = 30
CAL5 = ["is_enrollment_period", "is_exam_week", "is_event_day", "is_sem_break", "is_store_closed"]
CAL6 = CAL5 + ["semester_week"]
TFS = ("raw", "log1p", "sqrt", "yj")
N_BOOT, SEED = 4000, 5
G = {}


# ------------------------------------------------------------------ transforms
def yj_inv(z, lam):
    """Inverse Yeo-Johnson for any real z (the prediction can be negative on
    the transformed scale even though sales cannot)."""
    z = np.asarray(z, float)
    out = np.empty_like(z)
    pos = z >= 0
    with np.errstate(all="ignore"):
        if abs(lam) > 1e-8:
            out[pos] = np.power(np.maximum(z[pos] * lam + 1, 0), 1 / lam) - 1
        else:
            out[pos] = np.expm1(z[pos])
        if abs(lam - 2) > 1e-8:
            out[~pos] = 1 - np.power(np.maximum(1 - (2 - lam) * z[~pos], 0), 1 / (2 - lam))
        else:
            out[~pos] = -np.expm1(-z[~pos])
    return out


def make_tf(name, y):
    """(transformed y, inverse function), fitted on y - the training slice only."""
    y = np.asarray(y, float)
    if name == "raw":
        return y, lambda z: np.asarray(z, float)
    if name == "log1p":
        return np.log1p(y), lambda z: np.expm1(np.asarray(z, float))
    if name == "sqrt":
        return np.sqrt(y), lambda z: np.maximum(np.asarray(z, float), 0) ** 2
    if name == "yj":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                lam = float(stats.yeojohnson_normmax(y))
            except Exception:
                lam = 1.0
        lam = float(np.clip(lam, -1, 3)) if np.isfinite(lam) else 1.0
        return stats.yeojohnson(y, lmbda=lam), lambda z, lam=lam: yj_inv(z, lam)
    raise ValueError(name)


def safe(x, ymax):
    x = np.nan_to_num(np.asarray(x, float), nan=0.0, posinf=np.inf)
    return np.clip(x, 0, 10 * max(ymax, 1.0))


def smear(pred_t, resid, inv, ymax):
    """Duan smearing: the expected value of inv(pred + e) over in-sample residuals."""
    return safe(np.mean(inv(pred_t[:, None] + resid[None, :]), axis=1), ymax)


# ------------------------------------------------------------------ data
def load():
    import step4c_category_forecast as s4c
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "ustore.db"), uri=True)
    wide, _ = s4c.load_category_series(con)
    cats, _, _ = s4c.trim_padding(wide)
    prod = pd.read_sql_query("SELECT product_id, fsn_class, forecast_category FROM Dim_Product", con)
    fact = pd.read_sql_query("""SELECT f.product_id, d.calendar_date, f.quantity_sold FROM Fact_Sales f
        JOIN Dim_Date d ON d.date_id = f.date_id""", con, parse_dates=["calendar_date"])
    idx = cats.index
    daily = (fact.groupby(["product_id", "calendar_date"]).quantity_sold.sum().unstack(0)
             .reindex(idx, fill_value=0.0).fillna(0.0).astype(float))
    cal = (pd.read_sql_query("SELECT calendar_date, %s FROM Dim_Date" % ", ".join(CAL6), con,
                             parse_dates=["calendar_date"]).set_index("calendar_date")
           .reindex(idx).fillna(0.0).astype(float))
    types = load_day_types(con, idx, H)
    con.close()
    prod["forecast_category"] = prod["forecast_category"].fillna("Uncategorised")
    return dict(cats=cats, prod=prod, daily=daily, idx=idx, cal=cal, types=types)


def init():
    global G
    G = load()
    for name in ("cmdstanpy", "prophet"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.disabled = True
    warnings.filterwarnings("ignore")


# ------------------------------------------------------------------ models
def prophet_tf(v, n, tf, cols, yearly):
    from prophet import Prophet
    y = v[:n]
    if y.sum() <= 0:
        z = np.zeros(H)
        return z, z
    idx, cal = G["idx"], G["cal"]
    yt, inv = make_tf(tf, y)
    hist = pd.DataFrame({"ds": idx[:n], "y": yt})
    m = Prophet(weekly_seasonality=True, yearly_seasonality=yearly, daily_seasonality=False,
                uncertainty_samples=0)
    used = [c for c in cols if cal[c].iloc[:n].nunique() > 1]
    for c in used:
        m.add_regressor(c)
        hist[c] = cal[c].iloc[:n].to_numpy()
    fut = pd.DataFrame({"ds": idx[n:n + H]})
    for c in used:
        fut[c] = cal[c].iloc[n:n + H].to_numpy()
    try:
        m.fit(hist)
        pt = m.predict(fut)["yhat"].to_numpy()
        tail = hist.iloc[-365:]
        resid = tail["y"].to_numpy() - m.predict(tail.drop(columns="y"))["yhat"].to_numpy()
    except Exception:
        z = np.full(H, y[-30:].mean())
        return z, z
    return safe(inv(pt), y.max()), smear(pt, resid, inv, y.max())


def ridge_tf(v, n, tf):
    from sklearn.linear_model import Ridge
    idx, cal = G["idx"], G["cal"]
    lo = max(0, n - 365)
    y = v[lo:n]
    if y.sum() <= 0:
        z = np.zeros(H)
        return z, z
    X = np.column_stack([np.eye(7)[idx.dayofweek.to_numpy()], cal[CAL5].to_numpy()])
    yt, inv = make_tf(tf, y)
    mdl = Ridge(alpha=1.0).fit(X[lo:n], yt)
    pt = mdl.predict(X[n:n + H])
    resid = yt - mdl.predict(X[lo:n])
    return safe(inv(pt), y.max()), smear(pt, resid, inv, y.max())


def mean_tf(v, n, tf, window):
    y = v[:n][-window:]
    if y.sum() <= 0:
        z = np.zeros(H)
        return z, z
    yt, inv = make_tf(tf, y)
    naive = safe(np.full(H, inv(np.array([yt.mean()]))[0]), y.max())
    smeared = safe(np.full(H, np.mean(inv(yt))), y.max())
    return naive, smeared


def category_job(cat):
    v = G["cats"][cat].to_numpy(float)
    base = rolling_mean_fit_predict(180)
    shipped = calendar_capped_fit_predict(base, G["types"])
    rows = []
    for f in make_folds(v.size, H, 3, 12, 60):
        n = f.train_end
        blocks = aggregate_blocks(v[:n], H)
        rec = {"key": cat, "fold": f.fold_index, "actual": v[n:n + H].sum(),
               "scale": naive_scale(blocks) if blocks.size > 1 else np.nan,
               "REF 6-month average (unadjusted)": base(v[:n], H).sum(),
               "REF shipped (6-month + calendar)": shipped(v[:n], H).sum()}
        for tf in ("log1p", "sqrt", "yj"):
            nv, sm = mean_tf(v, n, tf, 180)
            rec["6-month avg, %s, naive back-transform" % tf] = nv.sum()
            rec["6-month avg, %s, smeared back-transform" % tf] = sm.sum()
        for tf in TFS:
            nv, sm = prophet_tf(v, n, tf, CAL5, yearly=False)
            rec["Prophet, %s, naive" % tf] = nv.sum()
            rec["Prophet, %s, smeared" % tf] = sm.sum()
            nv, sm = ridge_tf(v, n, tf)
            rec["Ridge calendar, %s, naive" % tf] = nv.sum()
            rec["Ridge calendar, %s, smeared" % tf] = sm.sum()
        rows.append(rec)
    return rows


def item_job(pid):
    prod, daily = G["prod"], G["daily"]
    cat_of = prod.set_index("product_id")["forecast_category"]
    cv = daily[[p for p in daily.columns if cat_of.get(p) == cat_of[pid]]].sum(axis=1).to_numpy()
    v = daily[pid].to_numpy(float)
    base = topdown_tsb_fit_predict(cv)
    shipped = calendar_capped_fit_predict(base, G["types"], cat_values=cv)
    rows = []
    for f in make_folds(v.size, H, 3, 12, 60):
        n = f.train_end
        blocks = aggregate_blocks(v[:n], H)
        rec = {"key": pid, "fold": f.fold_index, "actual": v[n:n + H].sum(),
               "scale": naive_scale(blocks) if blocks.size > 1 else np.nan,
               "REF blend (unadjusted)": base(v[:n], H).sum(),
               "REF shipped (blend + calendar)": shipped(v[:n], H).sum()}
        for w in (30, 180):
            for tf in ("log1p", "yj"):
                rec["%d-day avg, %s, naive back-transform" % (w, tf)] = mean_tf(v, n, tf, w)[0].sum()
        for tf in TFS:
            nv, sm = prophet_tf(v, n, tf, CAL6, yearly=True)
            rec["Prophet, %s, naive" % tf] = nv.sum()
            rec["Prophet, %s, smeared" % tf] = sm.sum()
        rows.append(rec)
    return rows


# ------------------------------------------------------------------ scoring
def per_key(sub, m):
    out = {}
    for k, g in sub.groupby("key"):
        sc = g.scale[np.isfinite(g.scale) & (g.scale > 0)]
        if len(sc):
            out[k] = np.abs(g.actual - g[m]).mean() / sc.mean()
    return pd.Series(out)


def boot(a, b, seed):
    d = (a - b).dropna().to_numpy()
    means = d[np.random.default_rng(seed).integers(0, d.size, size=(N_BOOT, d.size))].mean(axis=1)
    return dict(gap=d.mean(), ci_lo=np.percentile(means, 2.5), ci_hi=np.percentile(means, 97.5),
                n_better=int((d < 0).sum()), n=int(d.size))


def score(df, level):
    ref = "REF 6-month average (unadjusted)" if level == "category" else "REF blend (unadjusted)"
    ship = "REF shipped (6-month + calendar)" if level == "category" else "REF shipped (blend + calendar)"
    lab = "6-month avg" if level == "category" else "blend"
    for tf in TFS:
        df["50/50 %s + Prophet %s smeared" % (lab, tf)] = 0.5 * df[ref] + 0.5 * df["Prophet, %s, smeared" % tf]
    methods = [c for c in df.columns if c not in ("key", "fold", "actual", "scale")]
    nf = df.fold.max() + 1
    ref_k, ship_k = per_key(df, ref), per_key(df, ship)
    rows = []
    for m in methods:
        mk = per_key(df, m)
        rows.append(dict(level=level, method=m, is_reference=m.startswith("REF"),
                         macro_MASE=mk.mean(), below_1=int((mk < 1).sum()), n=len(mk),
                         pooled_WMAPE_pct=100 * (df.actual - df[m]).abs().sum() / df.actual.sum(),
                         bias_pct=100 * (df[m].sum() - df.actual.sum()) / df.actual.sum(),
                         MASE_older=per_key(df[df.fold < nf // 2], m).mean(),
                         MASE_newer=per_key(df[df.fold >= nf // 2], m).mean(),
                         **{"vs_unadjusted_" + k: v for k, v in boot(mk, ref_k, SEED).items()},
                         **{"vs_shipped_" + k: v for k, v in boot(mk, ship_k, SEED + 1).items()}))
    return pd.DataFrame(rows).sort_values("macro_MASE").reset_index(drop=True)


def checks():
    lams = []
    for c in G["cats"].columns:
        v = G["cats"][c].to_numpy(float)
        for f in make_folds(v.size, H, 3, 12, 60):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                lams.append(stats.yeojohnson_normmax(v[:f.train_end]))
    lams = np.array(lams)
    return dict(yj_lambda_min=lams.min(), yj_lambda_median=np.median(lams), yj_lambda_max=lams.max(),
                n_category_slices=len(lams),
                category_zero_day_pct=100 * (G["cats"].to_numpy() == 0).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    args = ap.parse_args()
    t0 = time.time()
    init()
    out = []
    items = [p for p in G["prod"][G["prod"].fsn_class == "F"].product_id if p in G["daily"].columns]
    for level, job, keys in (("category", category_job, list(G["cats"].columns)),
                             ("item", item_job, items)):
        rows = []
        with Pool(args.jobs, initializer=init) as pool:
            for r in pool.imap_unordered(job, keys):
                rows += r
        df = pd.DataFrame(rows).sort_values(["key", "fold"]).reset_index(drop=True)
        df.to_csv(os.path.join(DATA, "transformation_folds_%s.csv" % level), index=False,
                  lineterminator="\n", float_format="%.4f")
        out.append(score(df, level))
        print("  %s done (%.0fs)" % (level, time.time() - t0), flush=True)
    res = pd.concat(out, ignore_index=True)
    res.to_csv(os.path.join(DATA, "transformation_test.csv"), index=False, lineterminator="\n",
               float_format="%.4f")
    pd.DataFrame([checks()]).to_csv(os.path.join(DATA, "transformation_checks.csv"), index=False,
                                    lineterminator="\n", float_format="%.4f")
    pd.set_option("display.width", 230)
    pd.set_option("display.max_colwidth", 50)
    print(res[["level", "method", "macro_MASE", "below_1", "pooled_WMAPE_pct", "bias_pct",
               "vs_shipped_gap", "vs_shipped_ci_lo", "vs_shipped_ci_hi"]].round(3).to_string(index=False))
    print("\nWrote data/transformation_test.csv, transformation_checks.csv, transformation_folds_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
