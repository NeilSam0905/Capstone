"""
scripts/test_prediction_targets.py
------------------------------------------------------------------
Two questions other than "how many units", each scored against the simple
rule a store would use without a model (docs/SESSION_SUMMARY_2026-09-30.md
3.9, docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md 4.4):

  #1  Will the item sell in the next 30 days?        (yes / no)
        model   gradient-boosted classifier on the features below
        rule    it sold in the last 30 days; ranked by how recently it sold
        scored  AUC (ranking), accuracy (yes/no at 0.5), and "wrong idle
                flags": items called idle that did sell, % of all rows

  #2  How many weeks until the item's next sale?      (time to event)
        model   discrete-time survival: the same kind of classifier
                predicts the chance of a first sale in each week ahead, and
                the expected wait is read off that (capped at 26 weeks)
        rule    the longer since the last sale, the longer the wait
        scored  C-index (Harrell's; censored items count where the order is
                known), 0.5 = no better than chance

These scripts replace the laptop run whose scripts were never committed, so
the numbers will not match that run exactly. Rebuilt from its description,
not copied.

Test design - walk-forward, no peeking:
  For each of the last 12 month starts, the model is trained only on what
  was known before it (#1: rows whose 30 days had already closed; #2: waits
  measured up to that day, still-waiting ones censored there), then predicts
  every item that had sold at least once before that month.

  Features are the item's own sales before the origin (days since last /
  first sale, units and selling days in the last 30 / 90 / 365, the average
  gap between selling days) plus the published school calendar for the 30
  days ahead (enrollment, exam and semester-break days), which is known in
  advance.

  Each result is reported twice: all items, and items that sold in the 90
  days before the origin. Long-dead items are easy (they stay dead) and
  flatter every score - the 30 Sep review measured the rule's AUC dropping
  0.89 -> 0.75 on active items - so the active-only row is the honest one.

Output: data/prediction_targets_results.csv  (target, items, method, metric, value, n)
Read-only on ustore.db. Run:  python scripts/test_prediction_targets.py
------------------------------------------------------------------
"""
import os
import sqlite3

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "ustore.db")
OUT = os.path.join(ROOT, "data", "prediction_targets_results.csv")

H1 = 30                 # #1's horizon, days
MAX_WEEKS = 26          # #2's horizon; a longer wait is censored at 26 weeks
TEST_MONTHS = 12
ACTIVE_DAYS = 90        # "recently active" = sold in the 90 days before the origin
SEED = 7

FEATURES = ["days_since_last", "days_since_first", "units_30", "units_90", "units_365",
            "days_sold_30", "days_sold_90", "days_sold_365", "mean_gap",
            "cal_enrollment", "cal_exam", "cal_break"]


# ---------------------------------------------------------------- data
def load(con):
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, SUM(f.quantity_sold) AS units
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
        WHERE LOWER(COALESCE(f.transaction_type, 'sale')) = 'sale'
        GROUP BY 1, 2
    """, con, parse_dates=["calendar_date"])
    last = fact.loc[fact.units > 0, "calendar_date"].max()
    first = fact.loc[fact.units > 0, "calendar_date"].min()
    idx = pd.date_range(first, last, freq="D")
    daily = (fact.pivot_table(index="calendar_date", columns="product_id", values="units", aggfunc="sum")
             .reindex(idx).fillna(0.0).clip(lower=0.0))
    daily = daily.loc[:, daily.sum() > 0]
    cal = pd.read_sql_query("""SELECT calendar_date, is_enrollment_period, is_exam_week, is_sem_break
        FROM Dim_Date""", con, parse_dates=["calendar_date"]).set_index("calendar_date")
    return daily, cal


def features_at(daily, cal, t):
    """One row per item that had sold before day index `t`, from days [0, t) only."""
    past = daily.iloc[:t]
    sold = past.to_numpy() > 0
    n = sold.shape[0]
    has = sold.any(axis=0)
    last_i = np.where(has, n - 1 - np.argmax(sold[::-1], axis=0), -1)
    first_i = np.where(has, np.argmax(sold, axis=0), -1)
    days_sold = sold.sum(axis=0)
    span = np.maximum(last_i - first_i, 0)

    def window(w, values):
        return values[max(0, n - w):].sum(axis=0)

    units = past.to_numpy()
    origin = daily.index[t] if t < len(daily) else daily.index[-1] + pd.Timedelta(days=t - len(daily) + 1)
    ahead = cal.reindex(pd.date_range(origin, periods=H1, freq="D")).fillna(0)
    f = pd.DataFrame({
        "product_id": daily.columns,
        "days_since_last": n - last_i,
        "days_since_first": n - first_i,
        "units_30": window(30, units), "units_90": window(90, units), "units_365": window(365, units),
        "days_sold_30": window(30, sold), "days_sold_90": window(90, sold), "days_sold_365": window(365, sold),
        "mean_gap": np.where(days_sold > 1, span / np.maximum(days_sold - 1, 1), np.nan),
        "cal_enrollment": ahead.is_enrollment_period.sum(), "cal_exam": ahead.is_exam_week.sum(),
        "cal_break": ahead.is_sem_break.sum(),
    })
    f["origin"] = origin
    return f[has].reset_index(drop=True)


def wait_weeks(daily, t, end):
    """Weeks from day `t` to each item's next sale, looking only at days [t, end).
    Returns (weeks, observed): unobserved = no sale before `end` or within MAX_WEEKS."""
    fut = daily.iloc[t:end].to_numpy() > 0
    has = fut.any(axis=0)
    first = np.argmax(fut, axis=0)
    weeks = np.where(has, first // 7 + 1, (end - t) // 7)
    observed = has & (weeks <= MAX_WEEKS)
    weeks = np.minimum(weeks, MAX_WEEKS)
    return pd.Series(weeks, index=daily.columns), pd.Series(observed, index=daily.columns)


# ---------------------------------------------------------------- scoring
def c_index(time, observed, risk_of_sooner):
    """Harrell's C: of the pairs whose order is known (the earlier one was an
    observed sale), the share where the predicted order agrees. Ties in the
    prediction count half."""
    t, e, r = (np.asarray(x, dtype=float) for x in (time, observed, risk_of_sooner))
    ok = conc = 0.0
    for i in np.flatnonzero(e):
        later = t > t[i]
        ok += later.sum()
        conc += (r[i] > r[later]).sum() + 0.5 * (r[i] == r[later]).sum()
    return conc / ok if ok else np.nan


def clf():
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          l2_regularization=1.0, random_state=SEED)


def person_weeks(rows, weeks, observed):
    """Expand each (item, origin) into one row per week it was still waiting:
    target 1 in the week the sale came, 0 before it."""
    k = weeks.to_numpy().astype(int)
    rep = rows.loc[rows.index.repeat(k)].copy()
    rep["week"] = np.concatenate([np.arange(1, n + 1) for n in k])
    last = np.repeat(k, k) == rep["week"].to_numpy()
    rep["event"] = (last & np.repeat(observed.to_numpy(), k)).astype(int)
    return rep


def expected_wait(model, rows):
    """Expected weeks to next sale from predicted weekly chances (capped at MAX_WEEKS)."""
    surv = np.ones(len(rows))
    total = np.zeros(len(rows))
    for w in range(1, MAX_WEEKS + 1):
        h = model.predict_proba(rows.assign(week=w)[FEATURES + ["week"]])[:, 1]
        total += surv                      # still waiting at the start of week w
        surv = surv * (1 - h)
    return total


# ---------------------------------------------------------------- main
def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    daily, cal = load(con)
    con.close()
    dates = daily.index
    T = len(dates)

    month_starts = [i for i, d in enumerate(dates) if d.day == 1 and i >= 180]
    test_starts = [i for i in month_starts if i + H1 <= T][-TEST_MONTHS:]
    print(f"Data {dates[0].date()} to {dates[-1].date()}, {daily.shape[1]} items with sales; "
          f"testing {len(test_starts)} months, {dates[test_starts[0]].date()} to {dates[test_starts[-1]].date()}")

    rows1, rows2 = [], []
    for t in test_starts:
        # ---- #1: will it sell in the next 30 days ----
        train = []
        for s in month_starts:
            if s + H1 > t:                 # its 30 days had not closed by the test origin
                break
            f = features_at(daily, cal, s)
            f["y"] = (daily.iloc[s:s + H1].sum() > 0).reindex(f.product_id).to_numpy().astype(int)
            train.append(f)
        train = pd.concat(train, ignore_index=True)
        test = features_at(daily, cal, t)
        test["y"] = (daily.iloc[t:t + H1].sum() > 0).reindex(test.product_id).to_numpy().astype(int)
        m = clf().fit(train[FEATURES], train.y)
        test["p_model"] = m.predict_proba(test[FEATURES])[:, 1]
        test["p_rule"] = -test.days_since_last                       # sold more recently = likelier
        test["rule_yes"] = (test.days_since_last <= 30).astype(int)
        rows1.append(test)

        # ---- #2: weeks until the next sale ----
        train2 = []
        for s in month_starts:
            if s >= t:
                break
            f = features_at(daily, cal, s)
            w, e = wait_weeks(daily, s, t)                           # only what was known by t
            f["weeks"], f["observed"] = w.reindex(f.product_id).to_numpy(), e.reindex(f.product_id).to_numpy()
            train2.append(f[f.weeks > 0])
        train2 = pd.concat(train2, ignore_index=True)
        pw = person_weeks(train2, train2.weeks, train2.observed)
        m2 = clf().fit(pw[FEATURES + ["week"]], pw.event)
        test2 = features_at(daily, cal, t)
        w, e = wait_weeks(daily, t, T)
        test2["weeks"], test2["observed"] = w.reindex(test2.product_id).to_numpy(), e.reindex(test2.product_id).to_numpy()
        test2 = test2[test2.weeks > 0].copy()
        test2["wait_model"] = expected_wait(m2, test2)
        rows2.append(test2)
        print(f"  {dates[t].date()}: #1 {len(test)} items, #2 {len(test2)} items", flush=True)

    r1, r2 = pd.concat(rows1, ignore_index=True), pd.concat(rows2, ignore_index=True)
    out = []
    for label, a1, a2 in (("all items", r1, r2),
                          (f"sold in last {ACTIVE_DAYS} days", r1[r1.days_since_last <= ACTIVE_DAYS],
                           r2[r2.days_since_last <= ACTIVE_DAYS])):
        model_yes = (a1.p_model >= 0.5).astype(int)
        for method, score, yes in (("model", a1.p_model, model_yes), ("rule", a1.p_rule, a1.rule_yes)):
            out += [
                dict(target="#1 sells in 30 days", items=label, method=method, metric="AUC",
                     value=roc_auc_score(a1.y, score), n=len(a1)),
                dict(target="#1 sells in 30 days", items=label, method=method, metric="accuracy %",
                     value=100 * (yes == a1.y).mean(), n=len(a1)),
                dict(target="#1 sells in 30 days", items=label, method=method, metric="wrong idle flags %",
                     value=100 * ((yes == 0) & (a1.y == 1)).mean(), n=len(a1)),
            ]
        out += [
            dict(target="#2 weeks to next sale", items=label, method="model", metric="C-index",
                 value=c_index(a2.weeks, a2.observed, -a2.wait_model), n=len(a2)),
            dict(target="#2 weeks to next sale", items=label, method="rule", metric="C-index",
                 value=c_index(a2.weeks, a2.observed, -a2.days_since_last), n=len(a2)),
        ]
    res = pd.DataFrame(out)
    res.to_csv(OUT, index=False, lineterminator="\n", float_format="%.4f")

    pd.set_option("display.width", 160)
    print("\n" + res.pivot_table(index=["target", "items", "metric"], columns="method", values="value")
          .round(3).to_string())
    print("\nrows scored:", res.groupby(["target", "items"]).n.first().to_dict())
    print("Wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
