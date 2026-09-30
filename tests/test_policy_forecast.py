"""
tests/test_policy_forecast.py
------------------------------------------------------------------
Pins the wiring of the predictive stage: scripts/step4b_policy_forecast.py
publishes the policy rate and interval into Result_Forecast, and
scripts/step5_prescriptive.py reads them instead of recomputing.

Synthetic tables throughout - no ustore.db - so these pin the CONTRACT
rather than today's 208/58 split.

What is worth a test here, in order of how badly a regression would hurt:

  1. **The read-back must be exact.** The whole claim of this change is that
     Result_Prescriptive comes out identical whether step5 reads the
     published rows or recomputes them. Recovering the buffer by subtracting
     one stored float from another gets it back to within an ulp, not
     exactly, and an ulp in the buffer is an ulp in every reorder point. So
     the two consumed quantities are stored whole and read with no
     arithmetic, and that is pinned here.
  2. **step4 must not delete step4b's rows.** Two writers now share one
     table. An unqualified DELETE in the wrong one silently empties the
     predictive stage; step5 would then exit rather than recompute, which is
     the right failure, but the regression should be caught here first.
  3. **A flagged SKU must appear, carrying no number.** The contract's whole
     point is that "no rate" is reported rather than being absent or being a
     zero. A zero yhat would read as "nothing will sell" - the degeneracy
     this project removed from the prescription, re-entering through the
     predictive table.
------------------------------------------------------------------
"""
import sqlite3

import pytest

from step5_prescriptive import (
    FORECAST_POLICY_COLUMNS, POLICY_MODEL_TYPE, ensure_forecast_policy_columns,
    load_forecast_totals, load_policy_forecast,
)

POINT_MODEL = "rolling_mean_30"


def db(with_policy_columns=True):
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE Result_Forecast (
            forecast_id   INTEGER PRIMARY KEY,
            product_id    INTEGER NOT NULL,
            forecast_date TEXT NOT NULL,
            yhat          REAL,
            yhat_lower    REAL,
            yhat_upper    REAL,
            model_type    TEXT,
            is_heuristic  INTEGER DEFAULT 0,
            snapshot_date TEXT
        )""")
    if with_policy_columns:
        ensure_forecast_policy_columns(con)
    return con


def add_point(con, pid, n=3, yhat=2.0):
    con.executemany(
        "INSERT INTO Result_Forecast (product_id, forecast_date, yhat, yhat_lower, "
        "yhat_upper, model_type) VALUES (?,?,?,?,?,?)",
        [(pid, f"2026-08-{d + 1:02d}", yhat, yhat, yhat, POINT_MODEL) for d in range(n)])


def add_policy(con, pid, rate, buffer_units, lt, tier="partial", q=0.80,
               src="empirical_quantile"):
    band = 0.0 if not buffer_units else buffer_units / lt
    con.executemany(
        "INSERT INTO Result_Forecast (product_id, forecast_date, yhat, yhat_lower, "
        "yhat_upper, model_type, horizon_days, buffer_units, rate_source, "
        "service_tier, buffer_quantile, buffer_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(pid, f"2026-09-{d + 1:02d}", rate, rate, rate + band, POLICY_MODEL_TYPE,
          lt, buffer_units, "observed", tier, q, src) for d in range(lt)])


def add_flagged(con, pid, lt=18):
    con.execute(
        "INSERT INTO Result_Forecast (product_id, forecast_date, yhat, yhat_lower, "
        "yhat_upper, model_type, horizon_days, buffer_units, rate_source, "
        "service_tier, buffer_quantile, buffer_source) "
        "VALUES (?,?,NULL,NULL,NULL,?,?,NULL,'insufficient_data',NULL,NULL,NULL)",
        (pid, "2026-09-01", POLICY_MODEL_TYPE, lt))


# ---- 1. the read-back is exact ------------------------------------------

@pytest.mark.parametrize("rate,buffer_units,lt", [
    (0.1, 7.46, 18),
    (0.027397260273972604, 13.840000000000001, 14),
    (3.1415926535897931, 0.30000000000000004, 28),
    (1e-6, 1234.5678901234567, 14),
])
def test_rate_and_buffer_round_trip_bit_exactly(rate, buffer_units, lt):
    """No arithmetic on the read path. A derived buffer - (upper - yhat) * L -
    comes back within an ulp, and an ulp in the buffer is an ulp in every
    reorder point built from it."""
    con = db()
    add_policy(con, 1, rate, buffer_units, lt)
    rec = load_policy_forecast(con)[1]
    assert rec["rate"] == rate
    assert rec["buffer_units"] == buffer_units
    assert rec["horizon_days"] == lt


def test_the_derived_route_is_the_one_that_loses_precision():
    """Shows WHY the columns exist rather than asserting it. This is the
    subtraction the band would force on a consumer."""
    rate, buffer_units, lt = 0.027397260273972604, 13.840000000000001, 14
    band = buffer_units / lt
    assert (rate + band - rate) * lt != buffer_units       # the lossy route
    con = db()
    add_policy(con, 1, rate, buffer_units, lt)
    assert load_policy_forecast(con)[1]["buffer_units"] == buffer_units   # the stored one


# ---- 2. the band is not decoration --------------------------------------

def test_the_upper_band_sums_to_the_reorder_point_over_the_lead_time():
    """SUM(yhat_upper) across a SKU's L rows == rate*L + buffer == ROP, so the
    prescription is rebuildable from the predictive table by a reader who
    never opens Result_Prescriptive."""
    rate, buffer_units, lt = 0.5, 9.0, 18
    con = db()
    add_policy(con, 1, rate, buffer_units, lt)
    total = con.execute("SELECT SUM(yhat_upper) FROM Result_Forecast").fetchone()[0]
    assert total == pytest.approx(rate * lt + buffer_units, abs=1e-9)


def test_a_priced_sku_gets_one_row_per_lead_time_day():
    con = db()
    add_policy(con, 1, 0.5, 9.0, 28)
    n, h = con.execute("SELECT COUNT(*), MAX(horizon_days) FROM Result_Forecast").fetchone()
    assert n == h == 28


# ---- 3. a flagged SKU is present and carries no number -------------------

def test_a_flagged_sku_is_reported_not_absent_and_not_zero():
    con = db()
    add_flagged(con, 7)
    rec = load_policy_forecast(con)[7]
    assert rec["rate_source"] == "insufficient_data"
    assert rec["rate"] is None            # not 0.0 - a zero reads as "enough stock"
    assert rec["buffer_units"] is None
    assert rec["service_tier"] is None


def test_predictive_coverage_counts_priced_and_flagged_separately():
    con = db()
    for pid in (1, 2, 3):
        add_policy(con, pid, 0.5, 9.0, 14)
    for pid in (8, 9):
        add_flagged(con, pid)
    policy = load_policy_forecast(con)
    assert len(policy) == 5
    priced = [p for p in policy.values() if p["rate_source"] != "insufficient_data"]
    assert len(priced) == 3


# ---- 4. two writers, one table -------------------------------------------

def test_step4s_scoped_delete_keeps_the_policy_rows():
    """The exact statement scripts/step4_forecast_model.py runs."""
    con = db()
    add_point(con, 1)
    add_policy(con, 1, 0.5, 9.0, 14)
    con.execute("DELETE FROM Result_Forecast WHERE model_type IS NOT 'policy_rate'")
    kinds = {r[0] for r in con.execute("SELECT DISTINCT model_type FROM Result_Forecast")}
    assert kinds == {POLICY_MODEL_TYPE}


def test_the_scoped_delete_still_clears_rows_with_a_null_model_type():
    """`IS NOT` rather than `<>` - a NULL model_type is legacy point-forecast
    data and must still be cleared, which `<>` would silently leave behind."""
    con = db()
    con.execute("INSERT INTO Result_Forecast (product_id, forecast_date, yhat, model_type) "
                "VALUES (1,'2026-01-01',1.0,NULL)")
    add_policy(con, 1, 0.5, 9.0, 14)
    con.execute("DELETE FROM Result_Forecast WHERE model_type IS NOT 'policy_rate'")
    assert con.execute("SELECT COUNT(*) FROM Result_Forecast").fetchone()[0] == 14


def test_the_legacy_forecast_basis_does_not_sum_the_policy_rate_into_demand():
    """--demand-basis=forecast annualises a 30-day point total. A units/day
    rate added into that total would be two different quantities summed and
    called demand."""
    con = db()
    add_point(con, 1, n=3, yhat=2.0)
    add_policy(con, 1, 99.0, 9.0, 14)
    totals, model_type = load_forecast_totals(con)
    assert totals == {1: 6.0}
    assert model_type == POINT_MODEL


def test_the_two_model_types_coexist():
    con = db()
    add_point(con, 1)
    add_policy(con, 1, 0.5, 9.0, 14)
    assert set(load_policy_forecast(con)) == {1}
    assert set(load_forecast_totals(con)[0]) == {1}


# ---- 5. a stale schema must fail loudly, not silently recompute ----------

def test_load_returns_nothing_when_the_policy_columns_are_absent():
    """step5 treats an empty result as "step4b has not run" and exits. It must
    not be reachable by a half-migrated table looking like an empty one after
    a partial ALTER."""
    con = db(with_policy_columns=False)
    add_point(con, 1)
    assert load_policy_forecast(con) == {}


def test_migration_is_idempotent_and_reports_only_what_it_added():
    con = db(with_policy_columns=False)
    first = ensure_forecast_policy_columns(con)
    assert set(first) == set(FORECAST_POLICY_COLUMNS)
    assert ensure_forecast_policy_columns(con) == []
