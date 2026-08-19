"""
tests/test_service_frontier.py
------------------------------------------------------------------
Remediation R3. Divergence #21 has test_degenerate_forecast.py pinning
its figures so Chapter 4 prose can't drift from the data; #22
(docs/SERVICE_LEVEL_FRONTIER.md, reproduced by tools/service_frontier.py)
had nothing. This pins the three figures the module's own "Reproducing
it" section names as worth asserting before Chapter 4 quotes them: the
0.9490 structural ceiling, the knee's location in the marginal-cost
column, and rolling_mean_30's dominance over ets/tsb at q=0.80.

Reads the real committed data/model_benchmark_results.csv - like
tools/service_frontier.py itself, this fits no models and touches no
database, so a missing/regenerated CSV is the only way these can move.
------------------------------------------------------------------
"""
import os

import pytest

import service_frontier as sf  # conftest.py puts tools/ on sys.path

RESULTS_CSV = "data/model_benchmark_results.csv"


def _skip_if_no_data():
    if not os.path.exists(RESULTS_CSV):
        pytest.skip(f"{RESULTS_CSV} not built - run scripts/model_benchmark.py first")


@pytest.fixture(scope="module")
def df():
    _skip_if_no_data()
    return sf.load()


def test_population_is_nonempty_before_anything_else(df):
    """Every assertion below is trivially satisfied by an empty frame -
    this is the population check that has to come first, per the
    project's own established rule (CHANGES_tyrone.md #2.3/#2.4)."""
    assert len(df) > 0
    assert df.sku.nunique() == sf.EXP_N_SKUS


def test_structural_ceiling_is_0949(df):
    """Cause 2: 584 folds / 103 SKUs have flat-zero training slices -
    2,732 units (5.1% of scored demand) are unservable by ANY method
    before a single modelling choice is made."""
    c2 = sf.structural_ceiling(df)
    assert c2["n_unservable_folds"] == sf.EXP_UNSERVABLE_FOLDS
    assert c2["n_unservable_skus"] == sf.EXP_UNSERVABLE_SKUS
    assert c2["ceiling"] == pytest.approx(sf.EXP_CEILING, abs=1e-4)


def test_the_knee_is_a_real_marginal_cost_jump(df):
    """Cause 3: the frontier isn't just monotonic, it has a genuine knee -
    marginal holding cost at q=0.95 is materially higher than at q=0.80,
    not a rounding-level difference. This is the finding the frontier's
    'report a recommended operating point' argument rests on."""
    frontier = sf.empirical_quantile_frontier(df)
    at_80 = frontier.loc[frontier["q"] == 0.80, "held_per_extra_unit_served"].iloc[0]
    at_95 = frontier.loc[frontier["q"] == 0.95, "held_per_extra_unit_served"].iloc[0]
    assert at_95 > at_80 * 2, (
        f"expected a real knee (>=2x jump in marginal cost), got {at_80} -> {at_95}")


def test_rolling_mean_30_dominates_at_the_knee(df):
    """The claim that actually resolves B3/B15: at q=0.80, rolling_mean_30
    has both a higher fill rate AND less stock held than ets or tsb - a
    dominance, not a trade-off to adjudicate."""
    knee = sf.knee_comparison(df, q=0.80).set_index("method")
    winner = knee.loc["rolling_mean_30"]
    for rival in ("ets", "tsb"):
        row = knee.loc[rival]
        assert winner["fill_rate"] > row["fill_rate"], (
            f"rolling_mean_30 should beat {rival} on fill rate at the knee")
        assert winner["units_held"] < row["units_held"], (
            f"rolling_mean_30 should hold less stock than {rival} at the knee")


def test_eoq_demand_basis_is_method_independent(df):
    """208 of 266 SKUs have positive observed demand across the scored
    folds - a property of the demand data, not of which forecasting
    method is asked. This is what remediation S1 decouples EOQ onto."""
    n_positive, n_total = sf.eoq_demand_basis(df)
    assert n_total == sf.EXP_N_SKUS
    assert n_positive == sf.EXP_SKUS_POSITIVE_DEMAND
