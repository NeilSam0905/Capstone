"""
step5_prescriptive.py
------------------------------------------------------------------
ROP / Safety Stock / EOQ using REAL (still provisional) USTore
estimates, not the abstract lead-time x cost-ratio sensitivity grid
this script used to produce.

Where the numbers come from
----------------------------
1. LEAD TIME - step5a_set_lead_times.py sets Dim_Product.lead_time_days
   per product by garment category (14d simple/DTF/puff shirt, 18d
   embroidered shirt, 28d jacket, 18d default/non-apparel). Run that
   script first; this one exits if any priced SKU has a NULL lead time.

2. HOLDING COST (H) - USTore gave an inventory VALUE, not a holding
   cost: approximately PHP 120,000-300,000 of stock on hand. Converted
   here as:

       annual holding cost (PHP) = 25% x inventory value
       H (PHP / unit / year)     = annual holding cost / units on hand

   Using the midpoint of the range (PHP 210,000) and an estimated
   36,051 units on hand (the latest COMPLETE monthly inventory
   snapshot - 2026-03; 2026-04 was excluded because it is a partial
   count, only 187 of the usual ~1,400 rows):

       annual holding cost = 0.25 x 210,000       = PHP 52,500
       H                   = 52,500 / 36,051       = PHP 1.4563 / unit / year

   This is a single BLENDED rate averaged across the whole catalogue -
   it does not distinguish a PHP 30 keychain from a PHP 1,500 jacket,
   because USTore gave one inventory-value figure, not a per-item
   breakdown. That is a real limitation, not an oversight; flagged in
   Dim_Parameters and here.

3. ORDERING COST (S) - USTore's figure (PHP 200,000-500,000/month) is
   ambiguous: it may be the admin/setup cost of placing an order (what
   EOQ actually wants), or it may just be the PESO VALUE of goods
   ordered that month, which is a completely different quantity and
   would make EOQ meaningless if used directly. Rather than silently
   pick one reading, every SKU is priced under BOTH:

       low_admin_cost  : PHP 1,250 / order  (midpoint of a plausible
                          500-2,000 admin-cost range - staff time,
                          paperwork, a phone call to the supplier)
       high_goods_value: PHP 200,000 / order (USTore's own low-end
                          figure, taken literally as if it WERE a
                          per-order cost, to show how far EOQ swings
                          under the more likely-wrong reading)

   The gap between the two rows per SKU IS the finding: if EOQ moves
   by an order of magnitude between them (it does), that is the
   argument for going back to USTore and asking specifically "what
   does it cost you, in staff time and paperwork, to place one
   order?" rather than accepting the monthly figure as-is.

4. 0.5x / 1x / 2x EOQ sensitivity - kept exactly as before, now
   computed in real PHP/year instead of normalised-by-H units, since
   real S and H both exist now: TC(Q) = (D/Q) x S + (Q/2) x H.

Formulas (unchanged)
---------------------
    ROP          = (Average Daily Demand x Lead Time) + Safety Stock
    Safety Stock = Z x sigma_demand x sqrt(Lead Time)
    EOQ          = sqrt((2 x D x S) / H)

Z by FSN class: F = 1.65 (95% service), S = 1.04 (85%). N excluded.

Demand input (remediation S1)
-------------------------------
D used to come from a specific forecasting method's 30-day point
forecast, annualised x365/30 - which meant a method that forecasts zero
(e.g. rolling_median_30 on this intermittent catalogue) priced nothing,
and even the working default (rolling_mean_30) only reached 79 of 266
eligible F+S SKUs. EOQ is batching economics, insensitive to short-run
forecast error - the coupling to a specific forecast was never load-
bearing. `--demand-basis` controls this:

    trailing (default) - D is the SKU's own observed trailing-365-day
        total, no forecast method involved. Reaches 208 of 266 SKUs.
    forecast - the original behaviour: D from --demand-method's 30-day
        point forecast, annualised. Reaches 79 (rolling_mean_30) or
        fewer. Kept for exact reproducibility of prior runs.

Every row records which basis/method actually fed D (demand_method
column: "trailing_365d" or the forecast method name) and is flagged
provisional either way. The choice of default is implemented but not
unilaterally decided - see REMEDIATION_MASTER_v2.md S1 for the team
ratification this is pending.

Run (from the repo root):
    python scripts/step5a_set_lead_times.py     # first, if not already run
    python scripts/step5_prescriptive.py [--demand-basis trailing|forecast] [--demand-method rolling_mean_30]
------------------------------------------------------------------
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

# forecasting/ lives at the repo root, one level above this file (scripts/) -
# Python only auto-adds the directory of the script being RUN to sys.path,
# not the caller's cwd, so `python scripts/step5_prescriptive.py` cannot see
# it without this. Mirrors what conftest.py does for the test suite.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forecasting.baselines import (
    rolling_mean_fit_predict, rolling_median_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.intermittent import croston_fit_predict, sba_fit_predict
from step5a_set_lead_times import classify as classify_lead_time_tier

DB_NAME = "ustore.db"
HORIZON = 30
DAYS_PER_YEAR = 365.0

# ---- Holding cost (H): inventory value -> PHP/unit/year -------------
INVENTORY_VALUE_LOW = 120_000.0
INVENTORY_VALUE_MID = 210_000.0
INVENTORY_VALUE_HIGH = 300_000.0
HOLDING_COST_ANNUAL_RATE = 0.25
UNITS_ON_HAND_ESTIMATE = 36_051.0
UNITS_ON_HAND_SOURCE = (
    "latest COMPLETE monthly inventory snapshot (2026-03, 1,412 rows); "
    "2026-04 excluded as a partial count (187 rows)"
)
H_PHP_PER_UNIT_YEAR = (HOLDING_COST_ANNUAL_RATE * INVENTORY_VALUE_MID) / UNITS_ON_HAND_ESTIMATE

# ---- Ordering cost (S): two competing interpretations ----------------
ORDERING_COST_SCENARIOS = {
    "low_admin_cost": 1_250.0,     # midpoint of a plausible PHP 500-2,000/order admin cost
    "high_goods_value": 200_000.0,  # USTore's own low-end monthly figure, taken literally
}

Z_BY_CLASS = {"F": 1.65, "S": 1.04}
SERVICE_BY_CLASS = {"F": "95%", "S": "85%"}

MIN_SALE_DAYS_FOR_SIGMA = 10

PROVISIONAL = "PROVISIONAL - pending Block 5 (USTore site visit)"

DEMAND_METHODS = {
    "rolling_median_30": rolling_median_fit_predict(30),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "seasonal_naive": seasonal_naive_fit_predict(7),
    "croston": croston_fit_predict(0.1),
    "sba": sba_fit_predict(0.1),
}


def eoq(D, S, H):
    return float(np.sqrt(2.0 * D * S / H)) if D > 0 and S > 0 and H > 0 else 0.0


def total_cost(D, S, H, Q):
    """TC(Q) = (D/Q)*S + (Q/2)*H, real PHP/year. Minimised at Q = EOQ."""
    if Q <= 0:
        return float("inf")
    return (D / Q) * S + (Q / 2.0) * H


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
        "SELECT product_id, item_name, fsn_class, lead_time_days FROM Dim_Product", con)

    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")

    series = {}
    for pid, g in fact.groupby("product_id"):
        series[pid] = (g.groupby("calendar_date")["quantity_sold"].sum()
                        .reindex(idx, fill_value=0.0).astype(float).to_numpy())
    return series, products, idx


def seed_dim_parameters(con, demand_method):
    con.execute("DELETE FROM Dim_Parameters")
    now = dt.date.today().isoformat()

    rows = [
        ("assumption.inventory_value_php_low", INVENTORY_VALUE_LOW,
         f"PHP, USTore-stated range low end [{PROVISIONAL}]"),
        ("assumption.inventory_value_php_mid", INVENTORY_VALUE_MID,
         f"PHP, range midpoint - USED for holding cost below [{PROVISIONAL}]"),
        ("assumption.inventory_value_php_high", INVENTORY_VALUE_HIGH,
         f"PHP, USTore-stated range high end [{PROVISIONAL}]"),
        ("assumption.holding_cost_annual_rate", HOLDING_COST_ANNUAL_RATE,
         f"fraction of inventory value assumed as annual holding cost [{PROVISIONAL}]"),
        ("assumption.units_on_hand_estimate", UNITS_ON_HAND_ESTIMATE,
         f"units, {UNITS_ON_HAND_SOURCE} [{PROVISIONAL}]"),
        ("derived.holding_cost_php_per_unit_year", round(H_PHP_PER_UNIT_YEAR, 4),
         f"PHP/unit/year = 0.25 x {INVENTORY_VALUE_MID:,.0f} / {UNITS_ON_HAND_ESTIMATE:,.0f}; "
         f"a single BLENDED rate across the whole catalogue, not per-item [{PROVISIONAL}]"),
        ("assumption.ordering_cost_low_admin_php", ORDERING_COST_SCENARIOS["low_admin_cost"],
         f"PHP/order, midpoint of a plausible 500-2,000 admin-cost range [{PROVISIONAL}]"),
        ("assumption.ordering_cost_high_goods_value_php", ORDERING_COST_SCENARIOS["high_goods_value"],
         f"PHP/order, USTore's own 200k-500k/month figure taken literally - "
         f"LIKELY MONTHLY GOODS VALUE, NOT A PER-ORDER ADMIN COST [{PROVISIONAL}]"),
        ("assumption.lead_time_days.simple_dtf_puff_shirt", 14.0,
         f"days [{PROVISIONAL}]"),
        ("assumption.lead_time_days.embroidered_shirt", 18.0,
         f"days [{PROVISIONAL}]"),
        ("assumption.lead_time_days.jacket", 28.0,
         f"days [{PROVISIONAL}]"),
        ("assumption.lead_time_days.default", 18.0,
         f"days, non-apparel and anything uncategorized [{PROVISIONAL}]"),
        ("grid.horizon_days", float(HORIZON), f"days, forecast horizon [{PROVISIONAL}]"),
        ("grid.days_per_year", DAYS_PER_YEAR, f"days, annualisation factor [{PROVISIONAL}]"),
        ("input.demand_method", 0.0,
         f"{demand_method}; an input, not a model selection (B3) [{PROVISIONAL}]"),
    ]
    for cls, z in Z_BY_CLASS.items():
        rows.append((f"service.z_value.{cls}", z,
                     f"z for {SERVICE_BY_CLASS[cls]} service, class {cls} [{PROVISIONAL}]"))

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
                         "module docstring. Only used when --demand-basis=forecast.")
    ap.add_argument("--demand-basis", default="trailing", choices=["forecast", "trailing"],
                    help="Remediation S1. 'forecast' (the old default): D comes from "
                         "--demand-method's 30-day point forecast annualised x365/30 - "
                         "a method that forecasts zero prices nothing, which is why only "
                         "79 of 266 F+S SKUs got priced. 'trailing' (new default): D is "
                         "the SKU's own observed trailing-365-day total, sourced directly, "
                         "no forecast method involved - prices 208 of 266. EOQ is batching "
                         "economics and is insensitive to short-run forecast error, so "
                         "coupling it to a specific forecast was never load-bearing; see "
                         "REMEDIATION_MASTER_v2.md S1. Ratify the default with the team - "
                         "'forecast' reproduces the old behaviour exactly.")
    ap.add_argument("--db", default=DB_NAME)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Result_Prescriptive'").fetchone():
        print("Result_Prescriptive does not exist - run create_schema.py first.")
        return 1

    series, products, idx = load_series(con)
    fit_predict = DEMAND_METHODS[args.demand_method]
    demand_label = args.demand_method if args.demand_basis == "forecast" else "trailing_365d"
    print(f"Demand basis: {args.demand_basis} ({demand_label})")

    print(f"Holding cost H = {H_PHP_PER_UNIT_YEAR:.4f} PHP/unit/year "
          f"(0.25 x {INVENTORY_VALUE_MID:,.0f} / {UNITS_ON_HAND_ESTIMATE:,.0f})")
    print(f"Ordering cost scenarios: "
          f"low_admin_cost = PHP {ORDERING_COST_SCENARIOS['low_admin_cost']:,.0f}/order, "
          f"high_goods_value = PHP {ORDERING_COST_SCENARIOS['high_goods_value']:,.0f}/order "
          f"({ORDERING_COST_SCENARIOS['high_goods_value']/ORDERING_COST_SCENARIOS['low_admin_cost']:.0f}x apart)")

    # ---- per-SKU demand and variability ---------------------------
    stats = {}
    cv_by_class = {"F": [], "S": []}
    missing_lead_time = []

    for _, row in products.iterrows():
        pid, cls = row["product_id"], row["fsn_class"]
        if cls not in Z_BY_CLASS:          # N excluded entirely
            continue
        s = series.get(pid)
        if s is None or s.sum() <= 0:
            continue

        if args.demand_basis == "forecast":
            forecast_30d = float(np.sum(fit_predict(s, HORIZON)))
            if forecast_30d <= 0:
                continue
            add = forecast_30d / HORIZON                       # average daily demand
            annual = forecast_30d * (DAYS_PER_YEAR / HORIZON)  # D
        else:  # trailing: the SKU's own observed history, no forecast method involved
            trailing = s[-int(DAYS_PER_YEAR):]
            annual = float(trailing.sum())                     # D
            if annual <= 0:
                continue
            add = annual / DAYS_PER_YEAR                        # average daily demand

        if pd.isna(row["lead_time_days"]):
            missing_lead_time.append(row["item_name"])
            continue

        sale_days = int((s > 0).sum())
        sigma_obs = float(np.std(s, ddof=1)) if s.size > 1 else 0.0

        stats[pid] = {"cls": cls, "add": add, "annual": annual,
                      "sigma_obs": sigma_obs, "sale_days": sale_days,
                      "lead_time_days": int(row["lead_time_days"])}
        if sale_days >= MIN_SALE_DAYS_FOR_SIGMA and add > 0 and sigma_obs > 0:
            cv_by_class[cls].append(sigma_obs / add)

    if missing_lead_time:
        print(f"\nABORTING: {len(missing_lead_time)} priced SKU(s) have no lead_time_days "
              f"- run step5a_set_lead_times.py first: {missing_lead_time[:5]}")
        return 1

    cv_median = {c: (float(np.median(v)) if v else 1.0) for c, v in cv_by_class.items()}
    print("\nClass-median coefficient of variation (sigma fallback): "
          + ", ".join(f"{c}={cv_median[c]:.3f}" for c in sorted(cv_median)))

    # ---- price every SKU under both ordering-cost scenarios --------
    con.execute("DELETE FROM Result_Prescriptive")
    now = dt.datetime.now().isoformat(timespec="seconds")
    out = []
    n_fallback = 0
    # Re-derive the actual tier label the same way step5a computed it (not
    # guessed back from lead_time_days, which can't tell "embroidered_shirt"
    # apart from "default" - both are 18 days).
    product_meta = pd.read_sql_query(
        "SELECT product_id, item_name, category FROM Dim_Product", con
    ).set_index("product_id")
    lt_categories = product_meta.apply(
        lambda r: classify_lead_time_tier(r["item_name"], r["category"])[1], axis=1
    )

    for pid, st in stats.items():
        cls, add, annual, lt = st["cls"], st["add"], st["annual"], st["lead_time_days"]
        z = Z_BY_CLASS[cls]

        if st["sale_days"] >= MIN_SALE_DAYS_FOR_SIGMA and st["sigma_obs"] > 0:
            sigma, src = st["sigma_obs"], "observed"
        else:
            sigma, src = add * cv_median[cls], "cv_fallback"
            n_fallback += 1

        ss = safety_stock(z, sigma, lt)
        rop = reorder_point(add, lt, ss)

        for scenario, S in ORDERING_COST_SCENARIOS.items():
            q = eoq(annual, S, H_PHP_PER_UNIT_YEAR)
            out.append((
                int(pid), cls, lt, lt_categories.get(pid), scenario, S, H_PHP_PER_UNIT_YEAR,
                S / H_PHP_PER_UNIT_YEAR, add, annual, sigma, src, z,
                ss, rop, q,
                total_cost(annual, S, H_PHP_PER_UNIT_YEAR, q),
                total_cost(annual, S, H_PHP_PER_UNIT_YEAR, 0.5 * q),
                total_cost(annual, S, H_PHP_PER_UNIT_YEAR, 2.0 * q),
                demand_label, 1, now,
            ))

    con.executemany("""
        INSERT INTO Result_Prescriptive
          (product_id, fsn_class, lead_time_days, lead_time_category,
           ordering_cost_scenario, ordering_cost_php, holding_cost_php_per_unit_year,
           cost_ratio, avg_daily_demand, annual_demand, sigma_demand, sigma_source, z_value,
           safety_stock, reorder_point, eoq, cost_at_eoq, cost_at_half_eoq, cost_at_double_eoq,
           demand_method, is_provisional, generated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, out)

    n_params = seed_dim_parameters(con, demand_label)
    con.commit()

    # ---- coverage diagnostic (unchanged from before) ----------------
    eligible = [p for p in products["product_id"]
                if products.set_index("product_id").loc[p, "fsn_class"] in Z_BY_CLASS
                and series.get(p) is not None and series[p].sum() > 0]
    print("\nDemand-basis coverage (how many F+S SKUs get a positive D):")
    for window in (30, 90, 180, 365):
        n = sum(1 for p in eligible
                if float(np.mean(series[p][-window:])) > 0)
        tag = "  <- the spec's 30-day basis" if window == 30 else ""
        print(f"   trailing {window:3d} days : {n:3d} / {len(eligible)} SKUs{tag}")

    print(f"\nSKUs priced        : {len(stats)} (F+S with demand > 0; N excluded)")
    print(f"sigma via fallback : {n_fallback} of {len(stats)}")
    if n_fallback:
        fallback_names = [
            products.set_index("product_id").loc[pid, "item_name"]
            for pid, st in stats.items()
            if not (st["sale_days"] >= MIN_SALE_DAYS_FOR_SIGMA and st["sigma_obs"] > 0)
        ]
        print(f"   ({fallback_names})")
    print(f"Result_Prescriptive: {len(out):,} rows "
          f"({len(stats)} SKUs x {len(ORDERING_COST_SCENARIOS)} ordering-cost scenarios)")
    print(f"Dim_Parameters     : {n_params} assumption rows, all provisional")

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

    n_rows = con.execute("SELECT COUNT(*) FROM Result_Prescriptive").fetchone()[0]
    n_skus = con.execute(
        "SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive").fetchone()[0]
    expect("Result_Prescriptive is non-empty", n_rows > 0, True)
    expect("rows == SKUs x ordering-cost scenarios",
           n_rows, n_skus * len(ORDERING_COST_SCENARIOS))
    if n_rows == 0:
        print("\nFAILED: nothing was priced, so the remaining gates would be vacuous.")
        return 1

    expect("N-class rows in Result_Prescriptive",
           con.execute("""SELECT COUNT(*) FROM Result_Prescriptive r
                          JOIN Dim_Product p ON p.product_id = r.product_id
                          WHERE p.fsn_class = 'N'""").fetchone()[0], 0)
    expect("rows with fsn_class not in (F,S)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE fsn_class NOT IN ('F','S')").fetchone()[0], 0)

    expect("rows where cost(EOQ) >= cost(0.5x EOQ)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE cost_at_eoq >= cost_at_half_eoq").fetchone()[0], 0)
    expect("rows where cost(EOQ) >= cost(2x EOQ)",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE cost_at_eoq >= cost_at_double_eoq").fetchone()[0], 0)

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

    expect("Dim_Parameters rows NOT flagged provisional",
           con.execute("SELECT COUNT(*) FROM Dim_Parameters "
                       "WHERE unit IS NULL OR unit NOT LIKE '%PROVISIONAL%'").fetchone()[0], 0)
    expect("Result_Prescriptive rows not provisional",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE is_provisional != 1").fetchone()[0], 0)

    for cls, z in Z_BY_CLASS.items():
        expect(f"rows with wrong Z for class {cls}",
               con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                           "WHERE fsn_class = ? AND z_value != ?", (cls, z)).fetchone()[0], 0)

    expect("rows where ROP < safety stock",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE reorder_point < safety_stock - 1e-9").fetchone()[0], 0)

    expect("rows with wrong ordering cost for their scenario",
           con.execute("""SELECT COUNT(*) FROM Result_Prescriptive
                          WHERE (ordering_cost_scenario='low_admin_cost' AND ordering_cost_php!=1250.0)
                             OR (ordering_cost_scenario='high_goods_value' AND ordering_cost_php!=200000.0)
                       """).fetchone()[0], 0)

    if failures:
        print(f"\nFAILED: {len(failures)} gate(s). Record under 'Gate failures' "
              f"in docs/CHANGES_tyrone.md.")
        return 1
    print("\nAll prescriptive gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
