"""
tests/test_determinism.py
------------------------------------------------------------------
Reproducibility of the benchmark methods.

A9 exists so a panel member can regenerate Chapter 4's central result from
a clean clone. That guarantee is worth only as much as the determinism
underneath it, so this pins what is actually true - which is not "all of
it".

The accurate claim, established empirically on 2026-08-05 with two
consecutive full 266-SKU runs plus a cross-environment comparison:

    All eight methods are bit-reproducible under the documented
    environment. ETS moves only if you substitute a different BLAS.

  * **Within one environment**: byte-identical. Both
    `model_benchmark_results.csv` and `model_benchmark_summary.csv` hash
    the same across consecutive runs. No unseeded shuffle, no
    dict-ordering dependency, no tie broken by iteration order.

  * **Under `requirements.txt`**: reproduces the committed artifact
    exactly, ETS included. PyPI's numpy/scipy wheels bundle OpenBLAS, and
    the committed artifact is an OpenBLAS run.

  * **If the BLAS is swapped** - most commonly by installing scipy from
    conda, which links MKL - seven of the eight methods stay identical to
    15 decimal places and **ETS does not**: MASE 9.290576 (MKL) vs
    9.275184 (OpenBLAS), and 251 vs 254 SKUs priced.

ETS is the only method that fits parameters numerically; the other seven
are closed-form, which is exactly why they are unaffected. A random seed
would not fix this - it is not randomness.

Note which of those two numbers matters. The MASE delta is 0.17%, real
noise. The SKUs-priced difference is a DISCRETE OUTCOME FLIP: three SKUs
sit close enough to the pricing threshold that whether ETS forecasts them
positive depends on the BLAS. Bounded - ETS ranks 6th of 8 under both,
neighbours at 8.73 and 12.08, so no ranking can move - but ETS's pricing
decision is numerically marginal for a handful of SKUs, which is a
stronger statement than "the third decimal moves".

No documented conclusion depends on it: every result in
DEGENERATE_FORECAST.md concerns bit-identical methods. Tightening this is
a Batch 3 item.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.baselines import (
    ets_fit_predict, naive_fit_predict, rolling_mean_fit_predict,
    rolling_median_fit_predict, seasonal_naive_fit_predict,
)
from forecasting.intermittent import (
    croston_fit_predict, sba_fit_predict, tsb_fit_predict,
)

HORIZON = 30

# The seven closed-form methods. No optimiser, so no BLAS exposure.
CLOSED_FORM = {
    "naive": naive_fit_predict(),
    "seasonal_naive": seasonal_naive_fit_predict(7),
    "rolling_mean_30": rolling_mean_fit_predict(30),
    "rolling_median_30": rolling_median_fit_predict(30),
    "croston": croston_fit_predict(0.1),
    "sba": sba_fit_predict(0.1),
    "tsb": tsb_fit_predict(0.1, 0.1),
}


def intermittent_series(n=400, seed=11, zero_frac=0.8):
    rng = np.random.default_rng(seed)
    y = rng.integers(1, 40, size=n).astype(float)
    y[rng.random(n) < zero_frac] = 0.0
    return y


# ---- repeated calls must agree bit-for-bit ---------------------------

@pytest.mark.parametrize("name", sorted(CLOSED_FORM))
@pytest.mark.parametrize("seed", [1, 7, 23])
def test_closed_form_methods_are_bit_reproducible(name, seed):
    """Catches hidden state, an unseeded RNG, or a dependency on call
    order inside a method."""
    y = intermittent_series(seed=seed)
    fn = CLOSED_FORM[name]
    a, b = fn(y, HORIZON), fn(y, HORIZON)
    assert np.array_equal(a, b), f"{name} returned different output for identical input"
    assert a.dtype == b.dtype


@pytest.mark.parametrize("name", sorted(CLOSED_FORM))
def test_closed_form_methods_do_not_mutate_their_input(name):
    """A method that edits the series it was handed would make every
    later method in the loop non-reproducible."""
    y = intermittent_series()
    before = y.copy()
    CLOSED_FORM[name](y, HORIZON)
    assert np.array_equal(y, before), f"{name} mutated its input series"


def test_ets_is_reproducible_within_one_process():
    """ETS is the method that varies across BLAS backends. Within a single
    environment it must still be exactly repeatable, or the artifact would
    not be stable even on the machine that produced it."""
    y = intermittent_series(seed=5, zero_frac=0.4)
    fn = ets_fit_predict(7, optimise=True)
    a, b = fn(y, HORIZON), fn(y, HORIZON)
    assert np.array_equal(a, b), "ETS is not repeatable within one process"


def test_method_results_do_not_depend_on_evaluation_order():
    """Running the methods in a different order must not change any of
    them - the failure mode if one left state behind."""
    y = intermittent_series(seed=3)
    forward = {n: CLOSED_FORM[n](y, HORIZON) for n in sorted(CLOSED_FORM)}
    reverse = {n: CLOSED_FORM[n](y, HORIZON) for n in sorted(CLOSED_FORM, reverse=True)}
    for n in CLOSED_FORM:
        assert np.array_equal(forward[n], reverse[n]), f"{n} depends on evaluation order"


# ---- the documented cross-environment caveat -------------------------

def test_only_ets_uses_a_numerical_optimiser():
    """The reason the caveat is confined to one row. If another method
    ever gains an optimiser, this fails and the caveat needs widening."""
    import inspect

    import forecasting.baselines as baselines
    import forecasting.intermittent as intermittent

    optimiser_users = []
    for mod in (baselines, intermittent):
        src = inspect.getsource(mod)
        for fn_name in ("holt_winters_forecast", "naive_fit_predict",
                        "seasonal_naive_fit_predict", "rolling_mean_fit_predict",
                        "rolling_median_fit_predict", "croston", "sba", "tsb"):
            if not hasattr(mod, fn_name):
                continue
            fn_src = inspect.getsource(getattr(mod, fn_name))
            if "minimize(" in fn_src:
                optimiser_users.append(fn_name)

    assert optimiser_users == ["holt_winters_forecast"], (
        f"methods calling an optimiser: {optimiser_users}. The "
        f"cross-BLAS reproducibility caveat in this module's docstring "
        f"covers ETS only and needs updating.")
