# Does synthetic training data help the category-level models?

**Yes — for the ML learners, and by enough to change the leaderboard.** Ridge trained on the real
series plus three years of block-bootstrap synthetic prehistory scores **45.13%** pooled WMAPE
against the incumbent 180-day trailing mean's **49.30%**, winning in 8 of 12 categories. Four other
learners also clear the incumbent. The classical models (ARIMA, SARIMA, ETS) improve 4–9% but do not
clear it, and Prophet does not improve at all.

**This result comes with a caution that is not decoration** — the *most realistic* of the three
generators is the one that does **not** produce the win. See §6. Read the result as "the learners
were variance-limited and augmentation regularises them," not as "we found more signal."

**Reproduce:**

```
python tools/synthetic_augment_category_test.py --years 3          # full, 27 methods x 4 arms, ~65 min
python tools/synthetic_augment_category_test.py --quick --years 1  # ~12 min smoke, NO ML learners
```

Output: `data/synthetic_augment_category.csv` (one row per method per arm).

This is a **measurement, not a model selection**, on the same terms as `model_benchmark.py`,
`docs/SPARSE_DEMAND_EXPERIMENTS.md` and `docs/FAST_MOVING_BENCHMARK.md`. Nothing in the production
pipeline changed as a result. Selection remains deferred decision **B3**.

---

## 1. Why this was asked again

Two earlier experiments asked a version of "would more data help", and both were answered at SKU
level:

| where | what it tested | result |
|---|---|---|
| `SPARSE_DEMAND_EXPERIMENTS.md` #4 | 5 synthetic years, **7 window methods** | nothing moved |
| `tools/synthetic_augment_ml_test.py` | 3 synthetic years, **the ML learners** | learners improved 9–15% MAE, still lost to `rolling_mean_30` |

Neither covered the category level, and the category level is where the question is live.
`scripts/benchmark_category_level.py` newly makes ARIMA, SARIMA and Prophet scorable (Divergence
#25), and Prophet is the manuscript's headline model (§3.3.2). Those are **full-history** models.

Experiment #4's null was partly an artefact of its own design: six of its seven methods only ever
read a fixed trailing window, so history behind that window is invisible **by construction**. It
could not have found an effect. This run fixes that by scoring the models that actually read their
whole history — and, unlike the SKU-level ML run, finds one that clears the incumbent.

---

## 2. What can and cannot move, decided before the run

`make_folds(821, 30, 3, 12, 60)` places every fold origin at day 461 or later, so the **smallest
training slice any method ever sees is 461 real days**. Every window method in the category benchmark
reads at most 180 days:

| | window | can augmentation reach it? |
|---|---:|---|
| `RM6_6month_180d` (**incumbent**) | 180 d | **no** — 180 < 461 |
| `RM3_3month_90d`, `rolling_mean_60/30/14` | ≤ 90 d | no |
| `rolling_median_30`, `rolling_q75_30`, `seasonal_naive_7`, `naive` | ≤ 30 d | no |
| `weekly_hurdle_12w` | 84 d | no |
| `ets`, `ewma`, `tsb`, `croston`, `sba`, ARIMA, SARIMA, Prophet, **ML** | whole history | **yes** |

Ten of the twenty-seven methods are **arithmetically incapable of moving**, and are kept in the run
as the harness control. If any of their numbers shifts by more than float noise, augmentation has
leaked into the test window and every other row is void.

That framing sets **the bar**: whether augmentation lifts some full-history model past
`RM6_6month_180d`'s **49.30%**, a number augmentation cannot touch.

---

## 3. Design, and why it stays honest

Synthetic days are **prepended, never appended and never substituted**. `make_folds` lays test
windows out backward from the *end* of the array, so every fold's `actual_30d` is the same real
observed data in every arm. Only the training slice grows. **The scorer never sees a synthetic
value.**

This matters more than it might look. Of the 213 un-tallied days in the span, **42 fall inside test
windows** — including a 19-day run (2025-12-19 → 2026-01-06). Imputing gaps *in place* would have
graded the models against fabricated actuals. Prepending is what avoids that.

Three generators, increasingly realistic:

| arm | generator | preserves | destroys |
|---|---|---|---|
| `iid` | weekday-stratified bootstrap (reused unchanged from `tools/synthetic_augment_test.py`) | per-weekday zero rate, nonzero size distribution | all autocorrelation |
| `block-14` | moving-block bootstrap, 14-day blocks (reused from `tools/synthetic_augment_ml_test.py`) | runs of zeros, clusters of sales | structure beyond 14 days |
| `calendar` | **new here.** Whole store-days resampled keyed on (weekday, academic-calendar state) | the semester rhythm, **and** the common-mode zero pattern — the same source day applied to all 12 categories at once | structure beyond one day |

The `calendar` arm exists because experiment #4 explicitly flagged that its synthetic data "does not
reproduce the semester-break calendar pattern" and did not fix it. It is the **best case** for
augmentation on realism grounds, not a controlled one-variable contrast against `block-14`: it
differs in two ways, both toward realism, deliberately.

All three augmented arms are handed the **same** extended calendar, so the arms differ only in how
demand was generated. `Dim_Date` covers 2023-01-01 onward; the 608 prehistory days behind that are
filled by tiling the known calendar back in 364-day (52-week) steps — the shortest shift preserving
both weekday alignment and the annual academic rhythm. **That is a fabrication and is labelled as
one.**

### Harness gate

```
[PASS] 10 immune methods x 3 augmented arms | largest MAE drift 0.00e+00
```

Not "small" — **exactly zero**, on all 30 method-arm cells. And the `real only` arm reproduces
`data/category_benchmark_summary.csv` to the decimal, because it calls that benchmark's own
`build_methods()` rather than re-declaring the method set.

---

## 4. Result — pooled WMAPE, 12 categories × 12 folds, 3 synthetic years

Immune methods omitted except the incumbent. Full table in `data/synthetic_augment_category.csv`.

| method | real only | +iid | +block-14 | +calendar | best chg | clears 49.30? |
|---|---:|---:|---:|---:|---:|:--:|
| **`RM6_6month_180d`** (incumbent, immune) | **49.30** | 49.30 | 49.30 | 49.30 | — | — |
| `ridge` | 61.63 | 47.94 | **45.13** | 50.62 | **−26.8%** | **yes** |
| `extra_trees` | 56.36 | 49.76 | **47.61** | 51.78 | −15.5% | **yes** |
| `gradient_boosting` | 58.02 | 49.73 | **48.26** | 53.00 | −16.8% | **yes** |
| `random_forest` | 56.76 | 49.89 | **48.79** | 54.63 | −14.0% | **yes** |
| `xgboost` | 59.93 | 49.97 | **49.61** | 55.30 | −17.2% | no (−0.31 short) |
| `lightgbm` | 60.77 | 50.58 | **50.19** | 54.64 | −17.4% | no |
| `arima_212` | 54.88 | **50.12** | 52.94 | 50.72 | −8.7% | no |
| `arima_111` | 54.94 | **50.36** | 52.94 | 50.68 | −8.3% | no |
| `sarima_weekly` | 53.22 | 50.36 | 51.18 | **50.21** | −5.7% | no |
| `ets` | 58.36 | **50.90** | 54.32 | 52.23 | −12.8% | no |
| `prophet_plain` | 56.79 | 58.27 | 57.58 | **55.45** | −2.4% | no |
| ~~`prophet_cal`~~ | ~~59.41~~ | ~~54.53~~ | ~~54.53~~ | ~~54.53~~ | — | **VOID — see §5** |
| `tsb` / `croston` / `sba` / `ewma_a0.1` | — | — | — | — | 0.00 | no (α-decayed out of reach) |

```
RM6_6month_180d, real only, pooled WMAPE = 49.30%   (immune — same number in every arm)
  best non-immune, real only              : sarima_weekly  53.22%   loses
  best non-immune, +synthetic (iid)       : ridge          47.94%   BEATS
  best non-immune, +synthetic (block-14)  : ridge          45.13%   BEATS
  best non-immune, +synthetic (calendar)  : sarima_weekly  50.21%   loses
```

**The exponentially-decayed methods do not move at all.** `tsb`, `croston`, `sba` and `ewma_a0.1` are
bit-identical across arms. At α = 0.1 the effective memory is ~20–40 days; three synthetic years sit
so far behind that the weights underflow. Same mechanism as experiment #4's null, reproduced here.

---

## 5. One row in that table is void

`prophet_cal` reports 54.53 in all three augmented arms — identical to three decimal places across
three different generators, and identical to `rolling_mean_30`'s 54.53. That is the signature of
`prophet_fit_predict`'s exception fallback, which returns `full(horizon, v[-30:].mean())`.

Checked directly: **144 of 144 folds** produce predictions bit-identical to `rolling_mean_30`.
Prophet never fit on the augmented series at all. Its apparent "−8.22% improvement" is the trailing
mean wearing a Prophet label.

The fallback is deliberate and documented in `prophet_model.py` — every method must cover every fold
or the identical-folds gate fails — but it is **silent**, and silence is how a total fit failure
reached a results table looking like an improvement. Two follow-ups: find why Prophet fails on a
1,916-day series with tiled regressors, and make the fallback *count itself* so a fallback rate of
100% is visible in the output rather than requiring a separate investigation to detect.

---

## 6. Why the win is believed, and where it is fragile

Four controls, all passed:

**1. The harness gate.** 0.00e+00 drift on 10 immune methods × 3 arms. Nothing leaked.

**2. The `real only` arm reproduces the committed benchmark** to the decimal.

**3. It is not window selection in disguise.** The obvious deflationary reading — "ridge just learned
a longer trailing average, and 180 days was never the true optimum" — was tested by sweeping trailing
windows past 180 on **real data only**:

| window | 180 | 240 | 300 | 365 | 420 | 460 | 550 | 700 | 821 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pooled WMAPE % | **49.30** | 50.25 | 52.10 | 52.15 | 53.71 | 54.08 | 54.17 | 53.67 | 53.37 |

180 days is the genuine optimum; every longer window is worse. Ridge's 45.13% is not reachable by
picking a better window.

**4. The win is broad, not one category carrying a volume-weighted metric.** Ridge + block-14 beats
`RM6_180` in **8 of 12** categories:

| category | RM6_180 | ridge+block | | category | RM6_180 | ridge+block |
|---|---:|---:|---|---|---:|---:|
| Lanyards & IDs | 56.5 | **44.6** | | Bags | 56.2 | 60.3 ✗ |
| Umbrellas & Gear | 66.6 | **52.8** | | Home & Novelty | 88.4 | 92.0 ✗ |
| Shirts & Tops | 45.6 | **40.3** | | Outerwear | 35.5 | 40.1 ✗ |
| Keychains & Charms | 67.8 | **62.4** | | Uncategorised | 43.3 | 45.6 ✗ |
| Plush & Souvenirs | 62.3 | **59.1** | | | | |
| Apparel Accessories | 94.5 | **90.1** | | | | |
| Stationery | 34.5 | **32.9** | | | | |
| Drinkware | 33.4 | **33.0** | | | | |

### Where it is fragile — read this before using it

**The most realistic generator does not produce the win.** Ridge scores 45.13 under `block-14`,
47.94 under `iid` — and **50.62 under `calendar`, which loses to the incumbent.** The same reversal
holds for every learner. The two generators that *destroy* structure produce the wins; the one that
preserves the semester rhythm and the common-mode zero pattern does not.

That ordering is backwards from what a "the model learned real structure" story predicts, and it
points at the mechanism: the bootstrapped prehistory is **smoother and more stationary than reality**,
and training on it shrinks an over-parameterised learner toward a stable level estimate. That is
regularisation, and it is a real and useful effect — it is how ridge on ~400 noisy rows becomes ridge
on ~1,500 — but it is **not** new information about demand. A bootstrap of a series' own distribution
cannot invent a relationship absent from the real data.

Consistent with that reading, the gain stops well short of the ceiling. **Oracle floor**, same folds:

| | pooled WMAPE |
|---|---:|
| Best possible flat forecast, constant chosen **with hindsight from the test data** | **41.62%** |
| `ridge` + synthetic (block-14) | 45.13% |
| `RM6_6month_180d`, real only | 49.30% |

Ridge closes 4.2 of the 7.7 points that separated the incumbent from an unachievable oracle. That is
a real bite out of a small budget, not a breakthrough. The 30-day totals carry CV 0.36–1.14 per
category; at CV near 1 the standard deviation of demand exceeds its mean, and no level forecast
scores well against that.

---

## 7. Why the category grain still felt no better than per-SKU

Divergence #24 establishes that the *incumbent's* category forecast is **bit-identical to summing the
per-SKU forecasts** (RM6 is linear; verified 0.00pp gap). The WMAPE improvement from 74.8% to 49.3%
is a property of the metric's denominator, not of a better model. That is why the category run felt
like the per-SKU run.

The measured reason aggregation buys so little:

| | |
|---|---:|
| Days in span | 821 |
| Days where the **whole store** records zero | **405 (49.3%)** |
| — of those, `is_tally_date = 0` (no sheet exists) | 213 |
| — of those, Sunday | 103 |
| — of those, `is_store_closed = 1` | 22 |
| Category-level zero cells (12 × 821) | 5,781 |
| — attributable to **store-wide silence** | **4,860 (84.1%)** |

Aggregation removes *idiosyncratic* intermittency. It cannot remove sparsity that is **common-mode**,
and 84.1% of these zeros are: the categories go quiet **together**. On the 416 days the store did
record, the store total still carries CV 1.15.

Note this does **not** apply to the ridge result. Ridge is non-linear in the series (it fits features
of it), so its category forecast is *not* the sum of per-SKU forecasts, and #24's arithmetic-identity
argument does not cover it.

---

## 8. What this rules in and out

**Ruled out.** "Not enough history" is not the bottleneck for the window and exponential-smoothing
methods, nor for Prophet. Any Chapter 4 claim that accuracy improves as the Digital Tallying
Interface accumulates data should be scoped carefully — this experiment shows volume helps *only*
the over-parameterised learners, and helps them by regularising, not by teaching.

**Ruled in, with caveats.** A regularised learner at category grain is the first thing in this
project's history to beat a trailing average on the level-comparable metric. Before it could be
proposed for anything it needs: the `calendar`-arm reversal explained (§6), a seed-stability check
across several bootstrap draws, and the `prophet_cal` fallback bug fixed (§5). It also inherits an
uncomfortable property for a capstone — **its training data is partly fabricated** — which must be
stated wherever the number appears.

**Not tested, and the better lever.** 213 of 821 days are un-tallied days that every series builder
converts to "sold zero" via `reindex(fill_value=0)`. That is a *data-quality* defect, not a volume
one. `forecasting/observed_day.py` already implements the correction — forecast a rate per **trading**
day, scale by expected trading days — and `FAST_MOVING_BENCHMARK.md` §6.13 measures it at −5.5% MAE
for TSB at SKU level. **It is not wired into `benchmark_category_level.py`.** Unlike this experiment,
it removes a fabrication instead of adding one.

Neither result disturbs the headline. The oracle floor (41.62%) and §6.14's independent SKU-level
floor both say the manuscript's MAPE ≤ 20% criterion (§3.3.4) is unreachable on this data — which is
Divergence #6, already Chapter 4's designated headline.
