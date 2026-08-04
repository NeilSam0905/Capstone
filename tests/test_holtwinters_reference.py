"""
tests/test_holtwinters_reference.py
------------------------------------------------------------------
Validates the hand-rolled Holt-Winters against statsmodels.

`forecasting/baselines.py` implements Holt-Winters rather than importing
it, because statsmodels is not in the project's declared dependency set
and task A9 exists to make Chapter 4 reproducible from a clean clone.
That decision stands - but it makes the implementation a **benchmark
competitor**, and a subtle error in it would quietly distort the ranking
that deferred decision B3 depends on.

So: reference-checked where statsmodels is present, zero runtime
dependency where it isn't. The whole module skips cleanly if the import
fails, and the benchmark runs either way.

What is compared, and why not exact equality
--------------------------------------------
The tight comparison fixes all four smoothing constants and hands
statsmodels the *same* initial level, trend and seasonal vector, so the
only thing under test is the recursion itself. Agreement is then within
~1% rather than exact, because statsmodels applies the initial state and
computes its first fitted value slightly differently. That residual is
structural, not a bug - the alternative seasonal conventions were checked
and are far worse (SSE 894 for the convention used here, against 2,136
and 5,066 for a rolled or reversed seasonal vector).

A 1.5% tolerance is only meaningful if a real bug would exceed it, so
`test_the_comparison_would_catch_a_real_bug` corrupts the recursion in
three plausible ways and requires each to blow past the tolerance.
------------------------------------------------------------------
"""
import numpy as np
import pytest

from forecasting.baselines import DEFAULT_SEASON, _hw_recursion, holt_winters_forecast

statsmodels = pytest.importorskip(
    "statsmodels", reason="statsmodels is an optional dev-only reference dependency")
from statsmodels.tsa.holtwinters import ExponentialSmoothing  # noqa: E402

SEASON = DEFAULT_SEASON
ALPHA, BETA, GAMMA, PHI = 0.3, 0.05, 0.1, 0.95
REL_TOL = 0.015          # see the module docstring


def make_series(n=120, seed=3, slope=0.3, amp=8.0, noise=2.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return (50 + slope * t + amp * np.sin(2 * np.pi * t / SEASON)
            + rng.normal(0, noise, n))


def our_init(y):
    """The initialisation baselines.py uses, extracted so statsmodels can
    be given exactly the same starting state."""
    l0 = y[:SEASON].mean()
    b0 = (y[SEASON:2 * SEASON].mean() - l0) / SEASON
    s0 = (y[:SEASON] - l0).astype(float)
    return l0, b0, s0


def reference_fit(y, alpha=ALPHA, beta=BETA, gamma=GAMMA, phi=PHI):
    l0, b0, s0 = our_init(y)
    model = ExponentialSmoothing(
        y, trend="add", damped_trend=True, seasonal="add",
        seasonal_periods=SEASON, initialization_method="known",
        initial_level=l0, initial_trend=b0, initial_seasonal=s0)
    return model.fit(smoothing_level=alpha, smoothing_trend=beta,
                     smoothing_seasonal=gamma, damping_trend=phi,
                     optimized=False)


# ---- the tight comparison: same params, same init --------------------

@pytest.mark.parametrize("seed", [3, 11, 42])
def test_forecasts_match_statsmodels_within_tolerance(seed):
    y = make_series(seed=seed)
    ours = holt_winters_forecast(y, 14, SEASON, optimise=False)
    theirs = np.asarray(reference_fit(y).forecast(14))

    assert ours.shape == theirs.shape
    rel = np.max(np.abs(ours - theirs) / np.abs(theirs))
    assert rel < REL_TOL, f"forecasts diverge by {rel:.4%}, tolerance {REL_TOL:.2%}"


@pytest.mark.parametrize("seed", [3, 11, 42])
def test_final_level_and_trend_match_statsmodels(seed):
    """The states, not just the forecast - a compensating pair of errors
    could still produce a plausible forecast."""
    y = make_series(seed=seed)
    level, trend, _s, _sse = _hw_recursion(y, ALPHA, BETA, GAMMA, PHI, SEASON)
    ref = reference_fit(y)

    assert level == pytest.approx(float(ref.level[-1]), rel=REL_TOL)
    assert trend == pytest.approx(float(ref.trend[-1]), abs=0.05)


@pytest.mark.parametrize("alpha,beta,gamma,phi", [
    (0.1, 0.01, 0.05, 0.90),
    (0.3, 0.05, 0.10, 0.95),
    (0.5, 0.10, 0.20, 0.98),
    (0.8, 0.20, 0.30, 0.99),
])
def test_agreement_holds_across_the_parameter_space(alpha, beta, gamma, phi):
    """One matching parameter set could be luck. The optimiser explores
    this whole box, so agreement has to hold across it."""
    y = make_series(seed=7)
    ours = holt_winters_forecast(y, 10, SEASON, optimise=False)

    # holt_winters_forecast uses its own default x0; drive the recursion
    # directly so the parameters under test are the ones being compared
    level, trend, s, _ = _hw_recursion(y, alpha, beta, gamma, phi, SEASON)
    steps = np.arange(1, 11)
    damp = np.cumsum(phi ** steps)
    idx = (y.size + np.arange(10)) % SEASON
    ours = np.maximum(level + damp * trend + s[idx], 0.0)

    theirs = np.asarray(reference_fit(y, alpha, beta, gamma, phi).forecast(10))
    rel = np.max(np.abs(ours - theirs) / np.abs(theirs))
    assert rel < REL_TOL, (
        f"diverge by {rel:.4%} at alpha={alpha} beta={beta} gamma={gamma} phi={phi}")


def test_seasonal_convention_matches_the_reference():
    """The seasonal vector is applied in the same order as statsmodels.
    Rolling or reversing it is materially worse, which is what makes the
    agreement above evidence rather than coincidence."""
    y = make_series(seed=3)
    l0, b0, s0 = our_init(y)

    def sse_with(seasonal):
        m = ExponentialSmoothing(
            y, trend="add", damped_trend=True, seasonal="add",
            seasonal_periods=SEASON, initialization_method="known",
            initial_level=l0, initial_trend=b0, initial_seasonal=seasonal)
        return m.fit(smoothing_level=ALPHA, smoothing_trend=BETA,
                     smoothing_seasonal=GAMMA, damping_trend=PHI,
                     optimized=False).sse

    ours = sse_with(s0)
    for wrong in (s0[::-1], np.roll(s0, 1), np.roll(s0, -1)):
        assert ours < sse_with(wrong), "a shuffled seasonal fitted as well or better"


# ---- does the comparison have teeth? --------------------------------

def _buggy_recursion(y, alpha, beta, gamma, phi, season, bug):
    """The corrected recursion from baselines.py with one plausible defect
    injected. `seasonal_uses_new_level` is the defect this file actually
    found and is kept as a permanent regression guard."""
    n = y.size
    l = y[:season].mean()
    b = (y[season:2 * season].mean() - l) / season
    s = (y[:season] - l).astype(float).copy()

    for t in range(n):
        si = t % season
        seas_i = (t + 1) % season if bug == "seasonal_offset" else si
        l_prev, b_prev = l, b

        damp = 1.0 if bug == "no_damping" else phi
        sign = +1.0 if bug == "seasonal_sign" else -1.0

        l = alpha * (y[t] + sign * s[seas_i]) + (1 - alpha) * (l_prev + damp * b_prev)
        b = beta * (l - l_prev) + (1 - beta) * damp * b_prev

        if bug == "seasonal_uses_new_level":
            s[seas_i] = gamma * (y[t] - l) + (1 - gamma) * s[seas_i]
        else:
            s[seas_i] = gamma * (y[t] - l_prev - damp * b_prev) + (1 - gamma) * s[seas_i]
    return l, b, s


# Each defect is tested at the parameters that expose it. That is not
# cherry-picking - it is the point. `seasonal_uses_new_level` is the defect
# this file actually found, and it diverges only 0.89% at gamma = 0.1 while
# reaching 8.8% at gamma = 0.3. A single-point comparison would have missed
# it entirely; the parameter SWEEP is what caught it, because the optimiser
# searches that whole box.
BUG_PARAMS = {
    "no_damping": (ALPHA, BETA, GAMMA, PHI),
    "seasonal_offset": (ALPHA, BETA, GAMMA, PHI),
    "seasonal_sign": (ALPHA, BETA, GAMMA, PHI),
    "seasonal_uses_new_level": (0.8, 0.2, 0.3, 0.99),
}


@pytest.mark.parametrize("bug", sorted(BUG_PARAMS))
def test_the_comparison_would_catch_a_real_bug(bug):
    """A 1.5% tolerance is only a test if a genuine error exceeds it."""
    y = make_series(seed=3)
    a, b_, g, p = BUG_PARAMS[bug]
    level, trend, s = _buggy_recursion(y, a, b_, g, p, SEASON, bug)

    steps = np.arange(1, 15)
    damp = np.cumsum(p ** steps)
    idx = (y.size + np.arange(14)) % SEASON
    broken = np.maximum(level + damp * trend + s[idx], 0.0)

    theirs = np.asarray(reference_fit(y, a, b_, g, p).forecast(14))
    rel = np.max(np.abs(broken - theirs) / np.abs(theirs))
    assert rel > REL_TOL, (
        f"the '{bug}' defect diverged by only {rel:.4%}, inside the "
        f"{REL_TOL:.2%} tolerance - the reference test is too loose")


def test_the_found_defect_is_invisible_at_low_gamma():
    """Why this bug survived Batch 1: at the default smoothing constants it
    is well inside tolerance. It only bites where the optimiser roams."""
    y = make_series(seed=3)
    quiet = _buggy_recursion(y, ALPHA, BETA, 0.1, PHI, SEASON, "seasonal_uses_new_level")
    loud = _buggy_recursion(y, 0.8, 0.2, 0.3, 0.99, SEASON, "seasonal_uses_new_level")

    def divergence(state, params):
        level, trend, s = state
        a, b_, g, p = params
        steps = np.arange(1, 15)
        fc = np.maximum(level + np.cumsum(p ** steps) * trend
                        + s[(y.size + np.arange(14)) % SEASON], 0.0)
        ref = np.asarray(reference_fit(y, a, b_, g, p).forecast(14))
        return np.max(np.abs(fc - ref) / np.abs(ref))

    assert divergence(quiet, (ALPHA, BETA, 0.1, PHI)) < REL_TOL
    assert divergence(loud, (0.8, 0.2, 0.3, 0.99)) > REL_TOL


# ---- end-to-end, with both sides optimising --------------------------

def test_optimised_fits_agree_on_the_broad_answer():
    """Looser: both sides fit their own parameters. They will not land on
    identical values, but they must not disagree about the level of the
    series - that would mean one of them is fitting something else."""
    y = make_series(n=200, seed=5)
    ours = holt_winters_forecast(y, 30, SEASON, optimise=True)

    ref = ExponentialSmoothing(
        y, trend="add", damped_trend=True, seasonal="add",
        seasonal_periods=SEASON, initialization_method="estimated").fit()
    theirs = np.asarray(ref.forecast(30))

    assert np.mean(ours) == pytest.approx(np.mean(theirs), rel=0.10)
    assert np.corrcoef(ours, theirs)[0, 1] > 0.75


def test_our_forecast_is_never_negative():
    """Unit sales. statsmodels will happily return negatives on a
    declining series; ours clips, and that difference is deliberate."""
    y = np.maximum(np.linspace(60, 0, 120) + np.random.default_rng(1).normal(0, 3, 120), 0)
    assert np.all(holt_winters_forecast(y, 30, SEASON, optimise=True) >= 0.0)
