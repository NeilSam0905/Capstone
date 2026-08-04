"""
step5_prescriptive.py
------------------------------------------------------------------
ROP / Safety Stock / EOQ as a SENSITIVITY SURFACE, not a point estimate.

Why a surface
-------------
`lead_time_days` is NULL for all 519 products, and no ordering or holding
cost has been confirmed by anyone at USTore. Seeding those with invented
numbers would produce a table that looks authoritative and is not. So
this script does not pick values: it evaluates the formulas across a grid
and stores every cell.

    lead time   in {3, 7, 14, 21, 30} days
    cost ratio  S/H in {0.5, 1, 2, 5, 10}

25 cells per SKU. When someone finally asks the store what their lead
time is, the answer is a lookup in this table rather than a recompute -
and in the meantime the spread across the grid shows how much the answer
actually depends on the number nobody has (deferred decision B9).

The formulas, exactly as documented
-----------------------------------
    ROP          = (Average Daily Demand x Lead Time) + Safety Stock
    Safety Stock = Z x sigma_demand x sqrt(Lead Time)
    EOQ          = sqrt((2 x D x S) / H)

Z by FSN class: F = 1.65 (95% service), S = 1.04 (85%). **N is excluded
entirely** - a non-moving item has no meaningful reorder point, and
computing one would imply it should be restocked.

Costs are normalised by holding cost
------------------------------------
EOQ depends only on the RATIO S/H, so the grid is over that ratio and
total cost is reported in units of H:

    TC(Q) / H = (D / Q) x (S/H) + Q / 2

which is minimised at Q = EOQ. This keeps the whole table free of any
invented peso figure while still letting the EOQ-is-the-minimum property
be checked - it is asserted at 0.5x, 1x and 2x EOQ.

Demand input, and a result worth reading
---------------------------------------
D is the annualised 30-day forecast. The default is the rolling 30-day
MEAN, and the reason is a finding in its own right:

    The rolling 30-day MEDIAN leads the A9 ranking on MASE, and it is
    unusable here. On an intermittent daily series most days are zero, so
    the trailing median IS zero for nearly every SKU. Running this script
    with --demand-method rolling_median_30 prices 0 of 266 SKUs, because
    every annual demand comes out at zero and there is no EOQ to compute.

That is the accuracy/actionability split in one line: the method that
minimises forecast error predicts "nothing will sell", which is nearly
right day-to-day and useless for deciding how much to order. Anyone
choosing a model on MASE alone (deferred decision B3) needs to see this
before choosing.

The mean is used instead because it is the cheapest estimator that
returns a positive rate. **That is an input choice, not a model
selection.** Every row records which method fed it, and every row is
flagged provisional.

Run:
    python step5_prescriptive.py [--demand-method rolling_mean_30]
------------------------------------------------------------------
"""
import argparse
import datetime as dt
import sqlite3
import sys

import numpy as np
import pandas as pd

from forecasting.baselines import (
    rolling_mean_fit_predict, rolling_median_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.intermittent import croston_fit_predict, sba_fit_predict

DB_NAME = "ustore.db"
HORIZON = 30
DAYS_PER_YEAR = 365.0

LEAD_TIMES = [3, 7, 14, 21, 30]
COST_RATIOS = [0.5, 1.0, 2.0, 5.0, 10.0]

# Z by FSN class. N is not here because N is excluded, not zero-weighted.
Z_BY_CLASS = {"F": 1.65, "S": 1.04}
SERVICE_BY_CLASS = {"F": "95%", "S": "85%"}

# A sigma estimated from too few non-zero days is noise. Below this many
# selling days the class-median coefficient of variation is used instead.
MIN_SALE_DAYS_FOR_SIGMA = 10

PROVISIONAL = "PROVISIONAL - pending Block 5"

DEMAND_METHODS = {
    "rolling_median_30": rolling_median_fit_predict(30),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "seasonal_naive": seasonal_naive_fit_predict(7),
    "croston": croston_fit_predict(0.1),
    "sba": sba_fit_predict(0.1),
}


def eoq(D, ratio):
    """sqrt(2 D S / H) with the cost ratio standing in for S/H."""
    return float(np.sqrt(2.0 * D * ratio)) if D > 0 and ratio > 0 else 0.0


def total_cost_normalised(D, ratio, Q):
    """TC(Q)/H = (D/Q)(S/H) + Q/2. Minimised at Q = EOQ."""
    if Q <= 0:
        return float("inf")
    return (D / Q) * ratio + Q / 2.0


def safety_stock(z, sigma, lead_time):
    return float(z * sigma * np.sqrt(lead_time))


def reorder_point(add, lead_time, ss):
    return float(add * lead_time + ss)


def load_series(con):
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])

    products = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class FROM Dim_Product", con)

    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")

    series = {}
    for pid, g in fact.groupby("product_id"):
        series[pid] = (g.groupby("calendar_date")["quantity_sold"].sum()
                        .reindex(idx, fill_value=0.0).astype(float).to_numpy())
    return series, products, idx


def seed_dim_parameters(con, demand_method):
    """Store the GRID DEFINITION, not a chosen value. Every row is
    flagged provisional; tools/assert_invariants.py --phase a10 checks
    that none of them has lost the flag."""
    con.execute("DELETE FROM Dim_Parameters")
    now = dt.date.today().isoformat()

    rows = []
    for lt in LEAD_TIMES:
        rows.append((f"grid.lead_time_days.{lt}", float(lt),
                     f"days [{PROVISIONAL}]"))
    for r in COST_RATIOS:
        rows.append((f"grid.cost_ratio_S_over_H.{r}", float(r),
                     f"ratio S/H, dimensionless [{PROVISIONAL}]"))
    for cls, z in Z_BY_CLASS.items():
        rows.append((f"service.z_value.{cls}", z,
                     f"z for {SERVICE_BY_CLASS[cls]} service, class {cls} [{PROVISIONAL}]"))
    rows.append(("grid.horizon_days", float(HORIZON),
                 f"days, forecast horizon [{PROVISIONAL}]"))
    rows.append(("grid.days_per_year", DAYS_PER_YEAR,
                 f"days, annualisation factor [{PROVISIONAL}]"))
    rows.append(("grid.eoq_sensitivity_multipliers", 0.0,
                 f"evaluated at 0.5x / 1x / 2x EOQ [{PROVISIONAL}]"))
    rows.append(("input.demand_method", 0.0,
                 f"{demand_method}; an input, not a model selection (B3) [{PROVISIONAL}]"))

    con.executemany(
        "INSERT INTO Dim_Parameters (parameter_name, value, unit, last_updated) "
        "VALUES (?, ?, ?, ?)",
        [(n, v, u, now) for n, v, u in rows])
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demand-method", default="rolling_mean_30",
                    choices=sorted(DEMAND_METHODS),
                    help="rolling_median_30 leads on MASE but forecasts zero "
                         "for intermittent SKUs and prices nothing - see the "
                         "module docstring")
    ap.add_argument("--db", default=DB_NAME)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Result_Prescriptive'").fetchone():
        print("Result_Prescriptive does not exist - run create_schema.py first.")
        return 1

    series, products, idx = load_series(con)
    fit_predict = DEMAND_METHODS[args.demand_method]

    # ---- per-SKU demand and variability ---------------------------
    stats = {}
    cv_by_class = {"F": [], "S": []}

    for _, row in products.iterrows():
        pid, cls = row["product_id"], row["fsn_class"]
        if cls not in Z_BY_CLASS:          # N excluded entirely
            continue
        s = series.get(pid)
        if s is None or s.sum() <= 0:
            continue

        forecast_30d = float(np.sum(fit_predict(s, HORIZON)))
        if forecast_30d <= 0:
            continue

        add = forecast_30d / HORIZON                       # average daily demand
        annual = forecast_30d * (DAYS_PER_YEAR / HORIZON)  # D
        sale_days = int((s > 0).sum())
        sigma_obs = float(np.std(s, ddof=1)) if s.size > 1 else 0.0

        stats[pid] = {"cls": cls, "add": add, "annual": annual,
                      "sigma_obs": sigma_obs, "sale_days": sale_days}
        if sale_days >= MIN_SALE_DAYS_FOR_SIGMA and add > 0 and sigma_obs > 0:
            cv_by_class[cls].append(sigma_obs / add)

    cv_median = {c: (float(np.median(v)) if v else 1.0) for c, v in cv_by_class.items()}
    print("Class-median coefficient of variation (sigma fallback): "
          + ", ".join(f"{c}={cv_median[c]:.3f}" for c in sorted(cv_median)))

    # ---- the grid --------------------------------------------------
    con.execute("DELETE FROM Result_Prescriptive")
    now = dt.datetime.now().isoformat(timespec="seconds")
    out = []
    n_fallback = 0

    for pid, st in stats.items():
        cls, add, annual = st["cls"], st["add"], st["annual"]
        z = Z_BY_CLASS[cls]

        if st["sale_days"] >= MIN_SALE_DAYS_FOR_SIGMA and st["sigma_obs"] > 0:
            sigma, src = st["sigma_obs"], "observed"
        else:
            # too few selling days to estimate sigma directly
            sigma, src = add * cv_median[cls], "cv_fallback"
            n_fallback += 1

        for lt in LEAD_TIMES:
            ss = safety_stock(z, sigma, lt)
            rop = reorder_point(add, lt, ss)
            for ratio in COST_RATIOS:
                q = eoq(annual, ratio)
                out.append((
                    int(pid), cls, int(lt), float(ratio), add, annual, sigma, src, z,
                    ss, rop, q,
                    total_cost_normalised(annual, ratio, q),
                    total_cost_normalised(annual, ratio, 0.5 * q),
                    total_cost_normalised(annual, ratio, 2.0 * q),
                    args.demand_method, 1, now,
                ))

    con.executemany("""
        INSERT INTO Result_Prescriptive
          (product_id, fsn_class, lead_time_days, cost_ratio, avg_daily_demand,
           annual_demand, sigma_demand, sigma_source, z_value, safety_stock,
           reorder_point, eoq, cost_at_eoq, cost_at_half_eoq, cost_at_double_eoq,
           demand_method, is_provisional, generated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, out)

    n_params = seed_dim_parameters(con, args.demand_method)
    con.commit()

    # ---- coverage diagnostic ---------------------------------------
    # The spec's demand basis is an annualised 30-DAY forecast, so D is
    # anchored on the trailing 30 days - which here is 2026-07, inside the
    # AY2526 summer term. Most SKUs sell nothing then, so most get D = 0
    # and drop out. That is a property of the definition, not a bug, but it
    # decides how much of the catalogue gets a reorder point at all, so it
    # is measured rather than left implicit.
    eligible = [p for p in products["product_id"]
                if products.set_index("product_id").loc[p, "fsn_class"] in Z_BY_CLASS
                and series.get(p) is not None and series[p].sum() > 0]
    print("\nDemand-basis coverage (how many F+S SKUs get a positive D):")
    for window in (30, 90, 180, 365):
        n = sum(1 for p in eligible
                if float(np.mean(series[p][-window:])) > 0)
        tag = "  <- the spec's 30-day basis" if window == 30 else ""
        print(f"   trailing {window:3d} days : {n:3d} / {len(eligible)} SKUs{tag}")
    print("   The trailing 30 days fall in the 2026 summer term; a break window")
    print("   is why the 30-day basis prices so few. Widening it is a decision")
    print("   for Block 5, not a change this run makes.")

    print(f"\nSKUs priced        : {len(stats)} (F+S with demand > 0; N excluded)")
    print(f"sigma via fallback : {n_fallback}")
    print(f"Result_Prescriptive: {len(out):,} rows "
          f"({len(LEAD_TIMES)} lead times x {len(COST_RATIOS)} cost ratios)")
    print(f"Dim_Parameters     : {n_params} grid-definition rows, all provisional")

    rc = run_gates(con)
    con.close()
    return rc


def run_gates(con):
    failures = []

    def expect(label, actual, expected):
        ok = actual == expected
        print("[%s] %-42s %r%s" % ("PASS" if ok else "FAIL", label, actual,
                                   "" if ok else "   != expected %r" % (expected,)))
        if not ok:
            failures.append(label)

    print("\n=== gates ===")

    # 0. The table must not be empty. Without this, every gate below
    #    passes trivially on zero rows - which is exactly what happened
    #    on the first run, when the rolling-median demand input priced
    #    nothing and four "PASS" lines were printed against an empty table.
    n_rows = con.execute("SELECT COUNT(*) FROM Result_Prescriptive").fetchone()[0]
    n_skus = con.execute(
        "SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive").fetchone()[0]
    expect("Result_Prescriptive is non-empty", n_rows > 0, True)
    expect("rows == SKUs x lead times x cost ratios",
           n_rows, n_skus * len(LEAD_TIMES) * len(COST_RATIOS))
    if n_rows == 0:
        print("\nFAILED: nothing was priced, so the remaining gates would be vacuous.")
        return 1

    # 1. no N-class rows
    expect("N-class rows in Result_Prescriptive",
           con.execute("""SELECT COUNT(*) FROM Result_Prescriptive r
                          JOIN Dim_Product p ON p.product_id = r.product_id
                          WHERE p.fsn_class = 'N'""").fetchone()[0], 0)
    expect("rows with fsn_class not in (F,S)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE fsn_class NOT IN ('F','S')").fetchone()[0], 0)

    # 2. EOQ is the cost minimum
    expect("rows where cost(EOQ) >= cost(0.5x EOQ)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE cost_at_eoq >= cost_at_half_eoq").fetchone()[0], 0)
    expect("rows where cost(EOQ) >= cost(2x EOQ)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE cost_at_eoq >= cost_at_double_eoq").fetchone()[0], 0)

    # the textbook result: both off-optimum points cost exactly 1.25x
    ratio = con.execute("""SELECT AVG(cost_at_half_eoq / cost_at_eoq),
                                  AVG(cost_at_double_eoq / cost_at_eoq)
                           FROM Result_Prescriptive WHERE cost_at_eoq > 0""").fetchone()
    if ratio[0] is not None:
        print(f"       mean cost at 0.5x EOQ = {ratio[0]:.4f}x optimum, "
              f"at 2x EOQ = {ratio[1]:.4f}x  (theory: 1.25 both)")
        expect("cost curve matches EOQ theory at 0.5x",
               round(float(ratio[0]), 4), 1.25)
        expect("cost curve matches EOQ theory at 2x",
               round(float(ratio[1]), 4), 1.25)

    # 3. every Dim_Parameters row flagged provisional
    expect("Dim_Parameters rows NOT flagged provisional",
           con.execute("SELECT COUNT(*) FROM Dim_Parameters "
                       "WHERE unit IS NULL OR unit NOT LIKE '%PROVISIONAL%'").fetchone()[0], 0)
    expect("Result_Prescriptive rows not provisional",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE is_provisional != 1").fetchone()[0], 0)

    # 4. Z is correct per class
    for cls, z in Z_BY_CLASS.items():
        expect(f"rows with wrong Z for class {cls}",
               con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                           "WHERE fsn_class = ? AND z_value != ?", (cls, z)).fetchone()[0], 0)

    # 5. ROP >= safety stock (it is SS plus a non-negative demand term)
    expect("rows where ROP < safety stock",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE reorder_point < safety_stock - 1e-9").fetchone()[0], 0)

    if failures:
        print(f"\nFAILED: {len(failures)} gate(s). Record under 'Gate failures' "
              f"in CHANGES_tyrone.md.")
        return 1
    print("\nAll prescriptive gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
