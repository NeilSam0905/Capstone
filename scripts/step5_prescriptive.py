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

    trailing (DEFAULT, ratified) - D is the SKU's own observed
        trailing-365-day total, no forecast method involved. Reaches 208
        of 266 SKUs. This is the default by decision, not by inertia: it
        prices 2.6x as many SKUs as the alternative and does not make the
        prescriptive layer hostage to a forecasting-method choice (B3)
        that is still open.
    forecast - D from the PIPELINE'S OWN 30-day forecast, annualised
        x365/30, read out of Result_Forecast.

What changed in `forecast` mode (and why it matters)
-----------------------------------------------------
It used to RE-COMPUTE a forecast in-process by calling
`--demand-method`'s fit_predict over Fact_Sales. That meant this script
never read Result_Forecast at all, in either mode - so step4's output had
no consumer anywhere in the pipeline, and `--demand-method` could silently
disagree with the model step4 had actually published. Two sources of truth
for "the forecast", one of them invisible on the dashboard.

`forecast` now reads Result_Forecast directly: D = SUM(yhat) over the
SKU's 30 horizon rows, annualised. Consequences, stated plainly:

  - step4 must have been run. The script exits with a clear message if
    Result_Forecast is empty, rather than silently falling back.
  - Coverage is bounded by what step4 forecasts, which is the Fast class
    ONLY - 58 SKUs. Every S-class SKU is dropped in this mode. That is a
    real and large coverage loss (208 -> ~58) and is the strongest
    argument for keeping `trailing` as the default.
  - `--demand-method` is now IGNORED in forecast mode and warns when
    passed: the method is whatever step4 published, recorded per row as
    "result_forecast:<model_type>".

Every row records which basis/method actually fed D (demand_method
column: "trailing_365d" or "result_forecast:<model_type>") and is flagged
provisional either way.

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
from forecasting.policy import (
    CLUSTER_POOLED, DEFAULT_BUFFER_QUANTILE, DEFAULT_CLUSTER_K,
    DEFAULT_TIER_MAX_Q, DEFAULT_TIER_MIN_EFFICIENCY, DEFAULT_TIER_TARGET,
    INSUFFICIENT, NOT_STOCKABLE, OBSERVED, PARTIAL, SERVABLE, SERVICE_TIERS,
    assign_service_tier, empirical_buffer, policy_fold_errors, resolve_rates,
    trailing_rate_fn,
)
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

# The same bar, reused as the demand-rate evidence bar in
# forecasting.policy.resolve_rates: a SKU with too few sale-days to give a
# trustworthy sigma has too few to give a trustworthy RATE either, and
# splitting the two thresholds would mean defending two numbers instead of
# one. Passed explicitly rather than relying on policy.py's own default, so
# a change here moves both together.
MIN_SALE_DAYS_FOR_RATE = MIN_SALE_DAYS_FOR_SIGMA

# Buffer provenance, recorded per row alongside sigma_source.
# Non-degeneracy floor (work direction section 3, acceptance criterion 2).
# The gate that catches rolling_median_30: a method can win every error
# metric and still price nothing, and before this there was no check that
# would notice. Measured today: 208 of 266 eligible F+S SKUs receive a
# positive demand rate = 78.2%. The floor sits just under that so a real
# regression fails rather than being absorbed. It is a FLOOR on usable
# output, not a MAPE-style accuracy threshold - the two failed for opposite
# reasons and must not be confused.
MIN_PRICED_SHARE = 0.78

BUFFER_EMPIRICAL = "empirical_quantile"
BUFFER_NORMAL_FALLBACK = "normal_z_sigma_fallback"
# not_stockable: expected lead-time demand covered, no safety stock bought.
BUFFER_NOT_STOCKED = "no_buffer_not_stockable"

PROVISIONAL = "PROVISIONAL - pending Block 5 (USTore site visit)"

# VESTIGIAL: none of these callables are invoked any more. `forecast` mode
# reads Result_Forecast instead of recomputing, and `trailing` mode never used
# a method at all - so this dict now only supplies argparse's `choices` for the
# deprecated --demand-method flag, which is kept so existing commands and docs
# do not break. Delete it and the flag together when nothing references it.
DEMAND_METHODS = {
    "rolling_median_30": rolling_median_fit_predict(30),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "seasonal_naive": seasonal_naive_fit_predict(7),
    "croston": croston_fit_predict(0.1),
    "sba": sba_fit_predict(0.1),
}


# Columns the contract added. Applied as an in-place migration rather than
# requiring a full create_schema.py rebuild, mirroring what
# step4_forecast_model.py does for Result_Forecast_Metrics.mase - a pipeline
# that can only pick up a new column by dropping the database is a pipeline
# nobody runs.
CONTRACT_COLUMNS = {
    "rate_source": "TEXT",
    "service_tier": "TEXT",
    "buffer_quantile": "REAL",
    "buffer_source": "TEXT",
    "safety_stock_normal_legacy": "REAL",
}


def ensure_contract_columns(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(Result_Prescriptive)")}
    added = []
    for name, decl in CONTRACT_COLUMNS.items():
        if name not in have:
            con.execute(f"ALTER TABLE Result_Prescriptive ADD COLUMN {name} {decl}")
            added.append(name)
    if added:
        print(f"Schema: added {len(added)} contract column(s) to Result_Prescriptive: "
              f"{', '.join(added)}")
    return added


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


def build_observed_mask(con, idx):
    """Which calendar days are EVIDENCE, aligned to `idx`.

    A day is observed when the store was recorded selling (a Fact_Sales row
    exists for it) or recorded shut (is_sem_break or is_store_closed).
    Anything else is a day nobody wrote anything down about.

    The pipeline used to call every such day zero demand. Measured: 139 of
    821 calendar days - 16.9% - are zero-filled with no evidence the store
    was shut, and the store demonstrably trades seven days a week (79
    Saturdays and 76 Sundays are tallied, so weekends are not closures).
    123 of those 139 fall in 2024, which is why the correction barely moves
    recent rates (~+2%) and moves 2024-anchored ones a great deal (~+20%).

    This does NOT touch Fact_Sales. Only the denominator of a rate changes,
    so every committed database invariant holds.
    """
    ev = pd.read_sql_query("""
        SELECT d.calendar_date,
               CASE WHEN EXISTS (SELECT 1 FROM Fact_Sales f WHERE f.date_id = d.date_id)
                     OR d.is_sem_break = 1
                     OR d.is_store_closed = 1
                    THEN 1 ELSE 0 END AS observed
        FROM Dim_Date d
    """, con, parse_dates=["calendar_date"]).set_index("calendar_date")["observed"]
    return ev.reindex(idx).fillna(0).astype(bool).to_numpy()


def load_series(con):
    fact = pd.read_sql_query("""
        SELECT f.product_id, d.calendar_date, f.quantity_sold
        FROM Fact_Sales f JOIN Dim_Date d ON d.date_id = f.date_id
    """, con, parse_dates=["calendar_date"])

    products = pd.read_sql_query(
        "SELECT product_id, item_name, fsn_class, lead_time_days, unit_price_php "
        "FROM Dim_Product", con)

    idx = pd.date_range(fact["calendar_date"].min(),
                        fact["calendar_date"].max(), freq="D")

    series = {}
    for pid, g in fact.groupby("product_id"):
        series[pid] = (g.groupby("calendar_date")["quantity_sold"].sum()
                        .reindex(idx, fill_value=0.0).astype(float).to_numpy())
    return series, products, idx


def load_forecast_totals(con):
    """{product_id: 30-day forecast total} straight out of Result_Forecast,
    plus the model_type that produced it.

    This is step4's published output - the same numbers the Demand Forecast
    screen draws - not a forecast recomputed here. Returns ({}, None) when
    the table is absent or empty so the caller can fail loudly.
    """
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Result_Forecast'").fetchone():
        return {}, None
    rows = con.execute(
        "SELECT product_id, SUM(yhat) FROM Result_Forecast GROUP BY product_id").fetchall()
    models = [r[0] for r in con.execute(
        "SELECT DISTINCT model_type FROM Result_Forecast").fetchall()]
    # One model per run in practice; if step4 ever mixes them, name them all
    # rather than picking one and misreporting the provenance.
    model_type = "+".join(sorted(m for m in models if m)) or "unknown"
    return {int(pid): float(total or 0.0) for pid, total in rows}, model_type


def seed_dim_parameters(con, demand_method, buffer_quantile=None,
                        cluster_k=None, shrink=None, tier_target=None,
                        tier_min_efficiency=None):
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

    # The operating point, recorded as a DIAL rather than an asserted service
    # level. The z rows below are kept as the retired comparison, not as the
    # buffer - see docs/PRESCRIPTIVE_CONTRACT.md.
    if buffer_quantile is not None:
        rows.append(("service.buffer_quantile", float(buffer_quantile),
                     f"quantile of each SKU's own prior-fold policy errors, taken at its "
                     f"lead-time horizon, used AS the safety stock. Default 0.80 is the "
                     f"knee measured in docs/SERVICE_LEVEL_FRONTIER.md; it is an operating "
                     f"point chosen with the holding cost visible, NOT a service-level "
                     f"guarantee [{PROVISIONAL}]"))
    if cluster_k is not None:
        rows.append(("input.cluster_k", float(cluster_k),
                     f"K for the behavioural clustering that supplies the pooled fallback "
                     f"rate for thin SKUs [{PROVISIONAL}]"))
    if tier_target is not None:
        rows.append(("service.tier_target_fill", float(tier_target),
                     f"fill rate on a SKU's own pre-origin folds at or above which it is "
                     f"`servable` and takes the smallest quantile reaching it "
                     f"[{PROVISIONAL}]"))
    if tier_min_efficiency is not None:
        rows.append(("service.tier_min_efficiency", float(tier_min_efficiency),
                     f"units served per unit held below which stock is not committed; "
                     f"decides `not_stockable` AND where a `partial` SKU stops buying "
                     f"buffer [{PROVISIONAL}]"))
    if shrink is not None:
        rows.append(("input.rate_shrinkage_enabled", 1.0 if shrink else 0.0,
                     f"whether a thin SKU's rate is shrunk toward its behavioural "
                     f"cluster's median rate [{PROVISIONAL}]"))
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
    ap.add_argument("--buffer-quantile", type=float, default=DEFAULT_BUFFER_QUANTILE,
                    help="Operating point on the service/holding frontier, as a DIAL "
                         "rather than an asserted service level. The buffer is the "
                         "q-quantile of this SKU's own prior-fold policy errors, measured "
                         "at its lead-time horizon. Default %(default)s is the knee "
                         "measured in docs/SERVICE_LEVEL_FRONTIER.md - imported as a "
                         "default, not a finding of this script; "
                         "scripts/validate_policy_holdout.py re-measures it on this "
                         "policy's own curve. Raising it buys service and costs holding, "
                         "and the point of exposing it is that USTore makes that trade "
                         "deliberately with the holding cost visible.")
    ap.add_argument("--cluster-k", type=int, default=DEFAULT_CLUSTER_K,
                    help="K for the behavioural clustering that supplies the pooled "
                         "fallback rate for thin SKUs (default %(default)s, matching "
                         "data/model_benchmark_category_breakdown_cluster4.csv).")
    # DEFAULT FLIPPED to off, on evidence. Scored on the reserved window, the
    # shrinkage bought +12.7 units served for +155.1 units held - 12.2 held per
    # extra unit served, against 5.0 from simply raising the buffer quantile -
    # and it prices no additional SKU (coverage is identical either way). It is
    # dominated: the same service is available more cheaply from the dial. The
    # code and the flag are kept so the decision stays reversible and auditable;
    # `--shrink` re-enables it. See docs/POLICY_HOLDOUT.md.
    ap.add_argument("--shrink", dest="shrink", action="store_true", default=False,
                    help="Re-enable the cluster-pooled fallback for thin SKUs. OFF by "
                         "default because it is dominated on the reserved window (12.2 "
                         "units held per extra unit served, against 5.0 from the buffer "
                         "quantile) and prices no additional SKU.")
    ap.add_argument("--no-shrink", dest="shrink", action="store_false",
                    help="Explicit form of the default. Kept so existing commands and "
                         "docs continue to work unchanged.")
    ap.add_argument("--tier-target", type=float, default=DEFAULT_TIER_TARGET,
                    help="Fill rate on a SKU's own pre-origin folds at or above which it "
                         "is `servable` and gets the SMALLEST quantile that reaches it "
                         "(default %(default)s).")
    ap.add_argument("--tier-min-efficiency", type=float, default=DEFAULT_TIER_MIN_EFFICIENCY,
                    help="Units served per unit held below which stock is not worth "
                         "committing (default %(default)s). Decides both `not_stockable` "
                         "and where a `partial` SKU stops buying more buffer.")
    ap.add_argument("--zero-fill", action="store_true",
                    help="Restore the pre-correction denominator: divide units by the FULL "
                         "window rather than by the days actually observed. Asserting zero "
                         "demand on a day nobody recorded is a measurement error, so this "
                         "is a control for measuring the correction as an isolated "
                         "variable - not a supported mode.")
    ap.add_argument("--flat-quantile", action="store_true",
                    help="Disable per-tier operating points and apply --buffer-quantile to "
                         "every SKU, reproducing the pre-tiering policy. The control for "
                         "measuring what the tiering is worth.")
    args = ap.parse_args()

    if not 0.0 < args.buffer_quantile < 1.0:
        print(f"--buffer-quantile must be strictly between 0 and 1, got "
              f"{args.buffer_quantile}")
        return 1

    con = sqlite3.connect(args.db)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='Result_Prescriptive'").fetchone():
        print("Result_Prescriptive does not exist - run create_schema.py first.")
        return 1
    ensure_contract_columns(con)

    series, products, idx = load_series(con)
    observed = None if args.zero_fill else build_observed_mask(con, idx)
    if observed is not None:
        n_un = int((~observed).sum())
        print(f"Evidence: {int(observed.sum())} of {len(idx)} calendar days observed "
              f"({n_un} day(s) carry no record of a sale OR a closure, "
              f"{100 * n_un / len(idx):.1f}%)")
        print(f"          rates divide by observed days, not by the full window "
              f"(--zero-fill restores the old denominator)")
    else:
        print("Evidence: --zero-fill - every unrecorded day counted as zero demand "
              "(pre-correction control)")

    forecast_totals = {}
    if args.demand_basis == "forecast":
        forecast_totals, model_type = load_forecast_totals(con)
        if not forecast_totals:
            print("--demand-basis=forecast reads Result_Forecast, which is empty or "
                  "missing.\nRun scripts/step4_forecast_model.py first, or use "
                  "--demand-basis=trailing (the default).")
            return 1
        demand_label = f"result_forecast:{model_type}"
        if args.demand_method != ap.get_default("demand_method"):
            print(f"NOTE: --demand-method={args.demand_method} is ignored in forecast "
                  f"mode. D now comes from Result_Forecast, published by step4 as "
                  f"'{model_type}'. See the module docstring.")
        print(f"Demand basis: forecast, read from Result_Forecast "
              f"({len(forecast_totals)} SKUs carry one, model_type={model_type})")
    else:
        demand_label = "trailing_365d"
        print(f"Demand basis: trailing (trailing_365d)")

    print(f"Holding cost H = {H_PHP_PER_UNIT_YEAR:.4f} PHP/unit/year "
          f"(0.25 x {INVENTORY_VALUE_MID:,.0f} / {UNITS_ON_HAND_ESTIMATE:,.0f})")
    print(f"Ordering cost scenarios: "
          f"low_admin_cost = PHP {ORDERING_COST_SCENARIOS['low_admin_cost']:,.0f}/order, "
          f"high_goods_value = PHP {ORDERING_COST_SCENARIOS['high_goods_value']:,.0f}/order "
          f"({ORDERING_COST_SCENARIOS['high_goods_value']/ORDERING_COST_SCENARIOS['low_admin_cost']:.0f}x apart)")

    # ---- eligible population, stated before anything is computed ----
    # Eligibility is F/S with any sales history at all. Every eligible SKU
    # gets a ROW below, whatever happens to its rate - that is the change
    # this contract makes. The previous version `continue`d past a SKU whose
    # trailing window summed to zero, which dropped 58 of 266 out of the
    # output entirely: not as "unknown", simply absent, so the screen showed
    # nothing where a recommendation belonged.
    prod_ix = products.set_index("product_id")
    eligible = {}
    for _, row in products.iterrows():
        pid, cls = row["product_id"], row["fsn_class"]
        if cls not in Z_BY_CLASS:          # N excluded entirely
            continue
        s = series.get(pid)
        if s is None or s.sum() <= 0:
            continue
        eligible[pid] = s

    prices = {pid: (None if pd.isna(prod_ix.loc[pid, "unit_price_php"])
                    else float(prod_ix.loc[pid, "unit_price_php"]))
              for pid in eligible}

    # ---- demand rate: trailing observed, cluster-pooled fallback ----
    if args.demand_basis == "forecast":
        # Legacy mode, kept so the pre-contract behaviour is reproducible.
        # The contract does NOT apply here: D comes from Result_Forecast, a
        # deployment artifact with no per-fold history, so there is no
        # policy-error distribution to take a quantile of. Sizing this mode's
        # buffer from the TRAILING policy's errors would mean buffering one
        # predictor with another predictor's mistakes, which is the exact
        # mismatch the empirical buffer exists to remove. This mode therefore
        # keeps z*sigma, and says so.
        rates = {}
        for pid in eligible:
            forecast_30d = forecast_totals.get(int(pid), 0.0)
            rates[pid] = {"rate": (forecast_30d / HORIZON) if forecast_30d > 0 else None,
                          "rate_source": OBSERVED if forecast_30d > 0 else INSUFFICIENT,
                          "sale_days": None, "own_rate": None, "cluster": None,
                          "cluster_rate": None, "shrink_weight": None}
        print("NOTE: --demand-basis=forecast keeps the z*sigma normal buffer. The "
              "empirical\n      buffer is available only on the ratified trailing "
              "basis - see the loop\n      comment in main() for why.")
    else:
        rates = resolve_rates(eligible, prices,
                              window=int(DAYS_PER_YEAR),
                              min_sale_days=MIN_SALE_DAYS_FOR_RATE,
                              shrink=args.shrink,
                              k=args.cluster_k,
                              observed=observed)

    # ---- per-SKU variability (feeds the LEGACY comparison column) ----
    stats = {}
    flagged = {}
    cv_by_class = {"F": [], "S": []}
    missing_lead_time = []

    for pid, s in eligible.items():
        cls = prod_ix.loc[pid, "fsn_class"]
        rec = rates[pid]

        if pd.isna(prod_ix.loc[pid, "lead_time_days"]):
            missing_lead_time.append(prod_ix.loc[pid, "item_name"])
            continue
        lt = int(prod_ix.loc[pid, "lead_time_days"])

        if rec["rate_source"] == INSUFFICIENT or not rec["rate"] or rec["rate"] <= 0:
            # No learnable rate. A flag, never a zero reorder point - a zero
            # here would read on the screen as "you have enough", which is a
            # recommendation this script has no evidence for.
            flagged[pid] = {"cls": cls, "lead_time_days": lt,
                            "rate_source": INSUFFICIENT}
            continue

        add = float(rec["rate"])                       # average daily demand
        annual = add * DAYS_PER_YEAR                   # D

        sale_days = int((s > 0).sum())
        sigma_obs = float(np.std(s, ddof=1)) if s.size > 1 else 0.0

        stats[pid] = {"cls": cls, "add": add, "annual": annual,
                      "sigma_obs": sigma_obs, "sale_days": sale_days,
                      "lead_time_days": lt, "rate_source": rec["rate_source"],
                      "shrink_weight": rec["shrink_weight"],
                      "cluster_rate": rec["cluster_rate"]}
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

    rate_fn = trailing_rate_fn(int(DAYS_PER_YEAR), observed=observed)
    n_normal_fallback = 0

    # ---- service tiers: how much service each SKU's demand will ACCEPT ----
    # Assigned from each SKU's OWN history, before any stock is committed. A
    # single population-wide quantile does two wrong things at once here: it
    # under-serves the half of demand that can reach 0.95, and it charges
    # holding to chase SKUs no reorder point can serve. See
    # forecasting/policy.py's tier section for the measurement behind that.
    tiers = {}
    if args.demand_basis != "forecast" and not args.flat_quantile:
        for pid, st in stats.items():
            tiers[pid] = assign_service_tier(
                eligible[pid], st["lead_time_days"], rate_fn,
                target=args.tier_target,
                min_efficiency=args.tier_min_efficiency,
                max_q=DEFAULT_TIER_MAX_Q,
                default_q=args.buffer_quantile)

    for pid, st in stats.items():
        cls, add, annual, lt = st["cls"], st["add"], st["annual"], st["lead_time_days"]
        z = Z_BY_CLASS[cls]

        if st["sale_days"] >= MIN_SALE_DAYS_FOR_SIGMA and st["sigma_obs"] > 0:
            sigma, src = st["sigma_obs"], "observed"
        else:
            sigma, src = add * cv_median[cls], "cv_fallback"
            n_fallback += 1

        # The LEGACY buffer, kept as a comparison column rather than as the
        # decision. z*sigma assumes a symmetric normal around the mean; the
        # demand it is buffering is right-skewed intermittent, so it
        # under-sizes exactly where under-sizing costs a stockout. Reported
        # alongside so the swing between the two readings is visible, the
        # same way this script prices both readings of the ordering cost
        # instead of silently picking one.
        ss_normal = safety_stock(z, sigma, lt)

        tier = tiers.get(pid, {}).get("tier", PARTIAL)
        # The operating point is now per TIER, not per population. A servable
        # SKU takes the smallest quantile that reaches the target - service
        # beyond it is holding cost with nothing bought. A not_stockable SKU
        # takes NO buffer: it covers expected lead-time demand and nothing
        # more, because on its own history every extra unit of stock returns
        # less than the efficiency floor.
        tier_q = tiers.get(pid, {}).get("q", args.buffer_quantile)

        if args.demand_basis == "forecast":
            ss, buffer_src, buffer_q = ss_normal, BUFFER_NORMAL_FALLBACK, None
        elif tier == NOT_STOCKABLE:
            ss, buffer_src, buffer_q = 0.0, BUFFER_NOT_STOCKED, None
        else:
            # The buffer this policy actually uses: the q-quantile of its OWN
            # prior-fold errors, measured at THIS SKU's lead-time horizon, so
            # the quantile is already in the unit the reorder point needs and
            # no sqrt(L) scaling assumption is reintroduced.
            q = tier_q if tier_q is not None else args.buffer_quantile
            errors = policy_fold_errors(eligible[pid], lt, rate_fn)
            if errors.size:
                ss, buffer_src = empirical_buffer(errors, q), BUFFER_EMPIRICAL
            else:
                # Too little history to fold at this horizon. Fall back to the
                # normal buffer and RECORD that it happened - a buffer whose
                # provenance is not recorded cannot be audited later.
                ss, buffer_src = ss_normal, BUFFER_NORMAL_FALLBACK
                n_normal_fallback += 1
            buffer_q = q

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
                st["rate_source"], buffer_q, buffer_src, ss_normal, tier,
            ))

    # ---- the flagged rows: present, and explicitly without a number ----
    # Written ONCE, not once per ordering-cost scenario: there is no EOQ to
    # price under two readings of the ordering cost when there is no demand
    # rate to price it from. The scenario label says so rather than borrowing
    # one of the two real scenario names.
    for pid, fl in flagged.items():
        out.append((
            int(pid), fl["cls"], fl["lead_time_days"], lt_categories.get(pid),
            "not_priced", 0.0, H_PHP_PER_UNIT_YEAR,
            None, None, None, None, INSUFFICIENT, None,
            None, None, None,
            None, None, None,
            demand_label, 1, now,
            INSUFFICIENT, None, None, None, None,
        ))

    con.executemany("""
        INSERT INTO Result_Prescriptive
          (product_id, fsn_class, lead_time_days, lead_time_category,
           ordering_cost_scenario, ordering_cost_php, holding_cost_php_per_unit_year,
           cost_ratio, avg_daily_demand, annual_demand, sigma_demand, sigma_source, z_value,
           safety_stock, reorder_point, eoq, cost_at_eoq, cost_at_half_eoq, cost_at_double_eoq,
           demand_method, is_provisional, generated_at,
           rate_source, buffer_quantile, buffer_source, safety_stock_normal_legacy,
           service_tier)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, out)

    n_params = seed_dim_parameters(
        con, demand_label,
        buffer_quantile=(None if args.demand_basis == "forecast" else args.buffer_quantile),
        cluster_k=args.cluster_k, shrink=args.shrink,
        tier_target=(args.tier_target if tiers else None),
        tier_min_efficiency=(args.tier_min_efficiency if tiers else None))
    con.commit()

    # ---- coverage diagnostic (unchanged from before) ----------------
    print("\nDemand-basis coverage (how many F+S SKUs get a positive D):")
    for window in (30, 90, 180, 365):
        n = sum(1 for p in eligible
                if float(np.mean(series[p][-window:])) > 0)
        tag = "  <- the spec's 30-day basis" if window == 30 else ""
        print(f"   trailing {window:3d} days : {n:3d} / {len(eligible)} SKUs{tag}")

    # ---- the contract, as a population table ------------------------
    # Printed as a partition of the ELIGIBLE population, not as a count of
    # what happened to get priced: the whole point of the contract is that
    # every eligible SKU is accounted for somewhere, so the three states have
    # to add back up to the population in front of the reader.
    n_obs = sum(1 for st in stats.values() if st["rate_source"] == OBSERVED)
    n_pooled = sum(1 for st in stats.values() if st["rate_source"] == CLUSTER_POOLED)
    n_flagged = len(flagged)
    total = n_obs + n_pooled + n_flagged

    print(f"\nDemand rate by source (forecast -> prescriptive contract):")
    print(f"   observed          : {n_obs:3d}  own trailing-{int(DAYS_PER_YEAR)}d rate, "
          f">= {MIN_SALE_DAYS_FOR_RATE} sale-days of evidence")
    print(f"   cluster_pooled    : {n_pooled:3d}  thin rate shrunk toward its behavioural "
          f"cluster")
    print(f"   insufficient_data : {n_flagged:3d}  FLAGGED - no learnable rate, no reorder "
          f"point emitted")
    print(f"   {'-' * 58}")
    print(f"   eligible F+S      : {total:3d}  (every one accounted for; none dropped)")
    if total != len(eligible):
        print(f"   WARNING: {len(eligible)} eligible SKUs but {total} accounted for - "
              f"{len(eligible) - total} went missing")

    if tiers:
        from collections import Counter
        tc = Counter(t["tier"] for t in tiers.values())
        print(f"\nService tier (how much service each SKU's demand will ACCEPT, "
              f"measured pre-commitment):")
        print(f"   servable       : {tc[SERVABLE]:3d}  reaches {args.tier_target:.0%} fill on "
              f"its own folds; takes the SMALLEST q that does")
        print(f"   partial        : {tc[PARTIAL]:3d}  cannot reach it; stops where marginal "
              f"return falls below {args.tier_min_efficiency}")
        print(f"   not_stockable  : {tc[NOT_STOCKABLE]:3d}  no quantile returns "
              f"{args.tier_min_efficiency} served per held - expected demand covered, "
              f"NO buffer bought")
        print(f"   {'-' * 58}")
        print(f"   priced SKUs    : {sum(tc.values()):3d}")
        qs = sorted({t["q"] for t in tiers.values() if t["q"] is not None})
        print(f"   operating points in use: {', '.join(f'{q:g}' for q in qs)}")
    elif args.flat_quantile:
        print(f"\nService tiers DISABLED (--flat-quantile): every SKU at "
              f"q={args.buffer_quantile}.")

    if args.demand_basis != "forecast":
        q = args.buffer_quantile
        emp = [r for r in out if r[24] == BUFFER_EMPIRICAL]
        print(f"\nBuffer: empirical quantile q={q} of each SKU's own prior-fold policy "
              f"errors,")
        print(f"        measured at its lead-time horizon (14/18/28d), not scaled from a "
              f"30-day one.")
        print(f"   empirical buffer  : {len(set(r[0] for r in emp)):3d} SKUs")
        if n_normal_fallback:
            print(f"   normal z*sigma    : {n_normal_fallback:3d} SKUs - too little history "
                  f"to fold at their lead time")
        priced_rows = [r for r in out if r[13] is not None]
        if priced_rows:
            ss_emp = float(np.mean([r[13] for r in priced_rows]))
            ss_nrm = float(np.mean([r[25] for r in priced_rows if r[25] is not None]))
            print(f"   mean safety stock : {ss_emp:8.3f} empirical  vs  {ss_nrm:8.3f} "
                  f"z*sigma (legacy)")

    print(f"\nSKUs priced        : {len(stats)} (F+S with a demand rate; N excluded)")
    print(f"sigma via fallback : {n_fallback} of {len(stats)}  "
          f"(feeds the LEGACY comparison column only)")
    if n_fallback:
        fallback_names = [
            prod_ix.loc[pid, "item_name"]
            for pid, st in stats.items()
            if not (st["sale_days"] >= MIN_SALE_DAYS_FOR_SIGMA and st["sigma_obs"] > 0)
        ]
        print(f"   ({fallback_names})")
    print(f"Result_Prescriptive: {len(out):,} rows "
          f"({len(stats)} priced SKUs x {len(ORDERING_COST_SCENARIOS)} ordering-cost "
          f"scenarios + {n_flagged} flagged)")
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
    if n_rows == 0:
        print("\nFAILED: nothing was priced, so the remaining gates would be vacuous.")
        return 1

    # Flagged SKUs are written ONCE (no EOQ to price under two readings of the
    # ordering cost), so the row count is no longer a flat multiple of the SKU
    # count. Exactly as strict as before - it still pins every row to a SKU and
    # a known row class - it just knows about the third class now.
    n_flagged = con.execute(
        "SELECT COUNT(*) FROM Result_Prescriptive WHERE rate_source = ?",
        (INSUFFICIENT,)).fetchone()[0]
    n_priced_skus = con.execute(
        "SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive "
        "WHERE rate_source IS NOT ?", (INSUFFICIENT,)).fetchone()[0]
    expect("rows == priced SKUs x scenarios + flagged SKUs",
           n_rows, n_priced_skus * len(ORDERING_COST_SCENARIOS) + n_flagged)

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

    # ---- the contract's own gates ------------------------------------
    # Population first, per the project's rule: every assertion below is
    # trivially satisfied if no SKU reached the table at all.
    expect("eligible SKUs accounted for (> 0)", n_skus > 0, True)

    # (1) Non-degeneracy. The gate there was no version of before.
    share = n_priced_skus / n_skus if n_skus else 0.0
    print(f"       priced share = {n_priced_skus}/{n_skus} = {share:.4f} "
          f"(floor {MIN_PRICED_SHARE})")
    expect(f"priced share >= {MIN_PRICED_SHARE} (non-degeneracy)",
           share >= MIN_PRICED_SHARE, True)

    # (2) A flagged SKU must not carry a number. This is the gate that keeps
    # the degeneracy out of the prescription: a zero reorder point reads on
    # the screen as "you have enough stock".
    expect("flagged rows carrying a reorder point",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source = ? AND reorder_point IS NOT NULL",
                       (INSUFFICIENT,)).fetchone()[0], 0)
    expect("flagged rows carrying an EOQ",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source = ? AND eoq IS NOT NULL",
                       (INSUFFICIENT,)).fetchone()[0], 0)

    # (3) ...and the converse, so the flag cannot be used to hide a failure
    # to compute: a SKU WITH a rate must have a reorder point.
    expect("priced rows missing a reorder point",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source IS NOT ? AND reorder_point IS NULL",
                       (INSUFFICIENT,)).fetchone()[0], 0)

    # (4) Service tiers partition the priced population exactly once, and a
    # not_stockable SKU has bought no safety stock. The second is the tiering's
    # whole point: if it can still hold buffer, the tier is decorative.
    n_tiered = con.execute(
        "SELECT COUNT(DISTINCT product_id) FROM Result_Prescriptive "
        "WHERE service_tier IS NOT NULL").fetchone()[0]
    expect("rows with an unknown service_tier",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE service_tier IS NOT NULL AND service_tier NOT IN (?,?,?)",
                       SERVICE_TIERS).fetchone()[0], 0)
    expect("priced SKUs carrying a service tier", n_tiered, n_priced_skus)
    expect("flagged rows carrying a service tier",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source = ? AND service_tier IS NOT NULL",
                       (INSUFFICIENT,)).fetchone()[0], 0)
    expect("not_stockable rows holding safety stock",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE service_tier = ? AND safety_stock > 1e-9",
                       (NOT_STOCKABLE,)).fetchone()[0], 0)
    expect("servable/partial rows with no operating point",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE service_tier IN (?,?) AND buffer_quantile IS NULL",
                       (SERVABLE, PARTIAL)).fetchone()[0], 0)

    # (5) Every row states where its rate and its buffer came from.
    expect("rows with an unknown rate_source",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source IS NULL OR rate_source NOT IN (?,?,?)",
                       (OBSERVED, CLUSTER_POOLED, INSUFFICIENT)).fetchone()[0], 0)
    expect("priced rows with an unknown buffer_source",
           con.execute("SELECT COUNT(*) FROM Result_Prescriptive "
                       "WHERE rate_source IS NOT ? AND (buffer_source IS NULL "
                       "OR buffer_source NOT IN (?,?,?))",
                       (INSUFFICIENT, BUFFER_EMPIRICAL, BUFFER_NORMAL_FALLBACK,
                        BUFFER_NOT_STOCKED)).fetchone()[0], 0)

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
