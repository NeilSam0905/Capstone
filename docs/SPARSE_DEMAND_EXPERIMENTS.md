# Four follow-up experiments on the sparse-demand problem

Follow-ups to `docs/DEGENERATE_FORECAST.md` (#21) and `docs/FORECAST_METHOD_COMPARISON.md`, prompted by
one question: given that `Fact_Sales` is 81.2% zero-quantity rows, is there anything — a different
model shape, more granularity, more history — that gets forecast error down to a level (MAPE well
under 40%, MASE under 1.0) usable as a pass/fail bar? Four things were tried. One is a genuine
improvement over what's in production. The other three are negative results, recorded here for the
same reason `FORECAST_METHOD_COMPARISON.md` records its negative results: on real data, a "we tried X
and it didn't help" is evidence, not a wasted afternoon.

**None of this selects a model.** Like `model_benchmark.py`, this is a measurement. Model selection is
deferred decision **B3**, downstream of **B2** (whether the MAPE ≤ 20% framing in §3.3.4 survives at
all — see Divergence #6).

---

## Summary

| # | Experiment | Result |
|---|---|---|
| 1 | Weekly hurdle model (empirical rate) | **Positive.** Best MASE/RMSE of 11 methods tested, prices 140/266 SKUs (vs. production's 79) |
| 2 | Weekly hurdle model, upgraded to a fitted classifier | **Negative.** Worse than the empirical version on every error metric — overfits thin per-SKU history |
| 3 | Forecasting at a coarser level (category / whole store) | **Partial.** MAPE drops a lot (203% → 86%) but never gets near 40%, even summed to one number a day |
| 4 | Simulating 5 more years of history | **Negative.** Changes almost nothing — confirms the ceiling is about demand volatility, not data volume |

---

## 1. The weekly hurdle model — a genuine improvement

**The idea.** Split the forecast into two questions instead of one: *does this item sell at all this
week* (a rate), and *when it does, how much* (a size). Multiply them. This is the "two-part" or
"hurdle" framing standard in intermittent-demand forecasting — `tsb` (`forecasting/intermittent.py`)
already does a version of this per DAY, smoothed with one exponential constant. This model asks the
same question at WEEKLY resolution instead, on the reasoning that "will it sell this week" is a much
less degenerate question than "will it sell today" on an 81%-zero series.

**The model** (`forecasting/hurdle.py::weekly_hurdle_fit_predict`):

```
p_hat    = fraction of the trailing 12 weeks with a nonzero total
size_hat = mean of the NONZERO weekly totals in that same window
forecast = p_hat * size_hat, spread evenly across each day of the horizon
```

Deliberately a plain empirical rate, not a smoothing recursion — legible as exactly what it says.

**Result**, scored on the same 266-SKU / 3,192-fold walk-forward harness as every other method in
`model_benchmark.py`:

| method | MAE | RMSE | MASE | SKUs priced | fill rate |
|---|---:|---:|---:|---:|---:|
| **weekly_hurdle_12w** | 14.20 | **20.32 (best)** | **4.79 (best)** | 140 | 0.7285 |
| rolling_mean_30 (production) | **13.47 (best)** | 20.97 | 5.27 | 79 | **0.7746** |
| tsb (fallback) | 14.23 | 21.79 | 5.33 | **266 (all)** | 0.7538 |
| rolling_median_30 (old MASE "winner") | 14.87 | 23.35 | 4.83 | **0** | 0.5062 |

The old MASE leader (`rolling_median_30`) prices zero SKUs — the degenerate-forecast trap #21
describes. `weekly_hurdle_12w` beats it on error *and* is usable on 140 items, nearly double
`rolling_mean_30`'s 79. It doesn't dominate outright — `rolling_mean_30` still wins on MAE and fill
rate — but it's the first method in this project's history to combine "most accurate" with "not
degenerate," which is a real trade-off improvement over anything in `FORECAST_METHOD_COMPARISON.md`.

**Status:** wired into `model_benchmark.py`'s `build_methods()` as `weekly_hurdle_12w`, alongside the
original 10 methods.

---

## 2. The fitted-classifier version — tried, and it lost

**The idea.** The empirical hurdle rate is a single number per SKU. A genuinely "smarter" version
would fit a real classifier — logistic regression predicting daily sale probability from weekday,
recent sale frequency, and days-since-last-sale — instead of one flat rate.

**The model** (`forecasting/hurdle.py::logistic_hurdle_fit_predict`): regularised logistic regression
(L-BFGS-B on the analytic gradient, same optimiser `ets` already uses — no sklearn, no new
dependency), fit per SKU per fold on:

- 7 weekday one-hot buckets (recovered from array position, since every SKU shares one calendar —
  see the module docstring for why this needs no date column)
- trailing 7-day and 30-day sale-frequency rate (causal — never sees the day it predicts)
- days since the last sale, capped at 60

L2 regularisation is not optional here — it's what keeps the fit well-posed on an SKU whose label is
nearly constant (almost always sold, or almost never); without it, separable data drives the weights
to infinity instead of converging.

**Result:**

| method | MAE | RMSE | MASE | SKUs priced | fill rate |
|---|---:|---:|---:|---:|---:|
| weekly_hurdle_12w (empirical) | **14.20** | **20.32** | **4.79** | 140 | 0.7285 |
| **logistic_hurdle (fitted)** | 15.01 | 22.08 | 7.33 | 141 | 0.7389 |

Worse on every error metric, for essentially the same coverage. **This is the predicted failure mode,
not a surprise**: most SKUs in this catalogue have very few sale-events across two years of history —
too little signal for a 10-parameter model to learn weekday/recency effects from without fitting
noise instead. The empirical single-rate version degrades gracefully on thin data; the classifier
does not.

**What would change this:** pooling features across similar SKUs (or at least within an FSN tier)
rather than fitting one classifier per item, so a slow-moving SKU can borrow statistical strength from
others like it. Not attempted here — it requires restructuring the `fit_predict(train, horizon)`
interface every method in this project shares, since it currently receives one SKU's series in
isolation with no way to see its neighbours.

**Status:** kept in `build_methods()` as `logistic_hurdle` (a documented negative result, same
treatment `ewma`/`rolling_q75` got in `FORECAST_METHOD_COMPARISON.md`), not recommended.

---

## 3. Forecasting at a coarser level — helps a lot, not enough

**The question.** MAPE is undefined whenever actual demand is 0, which is most SKU-days here. Does
forecasting at a level where zero is rare — a whole product category, or the whole store's daily
total — make it a usable metric?

**Method:** the same model (`rolling_mean_30`), same walk-forward harness, run on three different
levels of aggregation of the same underlying `Fact_Sales` data. Reproducible via
`tools/aggregation_level_test.py`.

| Level | Series | MAE | RMSE | MAPE | MAPE computable on |
|---|---|---:|---:|---:|---:|
| Per SKU | 266 | 13.47 | 20.97 | 203.4% | 41% of folds |
| Per category *(APPAREL / NON-APPAREL / MAIN STORAGE)* | 3 | 141.19 | 186.05 | 115.2% | 100% of folds |
| Whole store | 1 | 2,035.50 | 2,621.14 | **85.8%** | 100% of folds |

*Category coverage caveat: 218 of 519 products have `category IS NULL` in `Dim_Product` and are
excluded from the category level (not imputed — a controlled-vocabulary field). That level covers 301
of 519 products, not the full catalogue.*

**Reading it:** aggregating genuinely helps. Going from per-item to whole-store, MAPE becomes
computable on every single fold (no more "zero actual" undefined cases) and drops from 203% to 86%.
That's a real, large improvement — and it's *because* higher levels of aggregation are structurally
less zero-inflated, not because the model got better.

**But it doesn't cross the line.** Even the most aggregated view possible — one number, the whole
store, per day — still misses by 86% on average. That is close to the ~89% daily / ~60% monthly
perfect-forecast floor Divergence #6 already established: it says the remaining error isn't
zero-inflation any more, it's genuine day-to-day volatility in how much the store sells at all
(enrollment weeks, exam weeks, ordinary randomness), which a flat 30-day rolling average cannot track
at any level of aggregation.

**What this rules out:** "just forecast something bigger" is not a route to a sub-40% MAPE on this
data. It's a partial fix for the zero-inflation half of the problem, and a demonstration that the
other half — raw volatility — persists at every scale tried.

---

## 4. Simulating 5 more years of history — no meaningful effect

**The question.** Is the accuracy ceiling a data-*volume* problem — would the models simply do better
with more years of history to learn from?

**Design, and why the test stays honest.** Reproducible via `tools/synthetic_augment_test.py`. For
each SKU, ~1,825 synthetic days (5 years) were generated by a **weekday-stratified bootstrap of that
same SKU's own real distribution** — same zero-rate per weekday, same nonzero-size distribution per
weekday, resampled with replacement — and **prepended** before the real 821-day series, never
appended and never substituted for real days.

That "prepend, never substitute" choice is what keeps the experiment honest rather than
self-fulfilling: `forecasting.evaluate.make_folds` lays out test windows by stepping backward from the
END of the array, so every fold's test target (`actual_30d`) is the exact same real, observed data
whether or not the synthetic years exist. Only the *training* slice available to each fold grows. It
is structurally impossible for this design to manufacture an improvement by feeding the scorer
fabricated "actuals" — the scorer never sees the synthetic days at all. The synthetic data does **not**
reproduce the semester-break calendar pattern; that's a real simplification of the simulation, noted
rather than hidden.

**This is a sandboxed, in-memory experiment.** It never touches `ustore.db`, `Fact_Sales`, or any
committed CSV — nothing about it is part of the pipeline or the production database.

**Result** (7 of the project's methods; the two optimiser-heavy ones, `ets` and `logistic_hurdle`,
were left out to keep the run fast — nothing about the mechanism below is specific to which method is
tested):

| method | MAE (real only) | MAE (+5 synthetic years) | change |
|---|---:|---:|---|
| naive | 34.244 | 34.244 | none |
| seasonal_naive | 16.587 | 16.587 | none |
| rolling_mean_30 | 13.473 | 13.473 | none |
| rolling_median_30 | 14.874 | 14.874 | none |
| ewma_a0.1 | 14.682 | 14.682 | none |
| tsb | 14.233 | 14.072 | ~1% better |
| weekly_hurdle_12w | 14.204 | 14.204 | none |

Same story at the whole-store level: every method identical with or without the synthetic years,
including `weekly_hurdle_12w` (MAE 2,178.79 either way).

**Why almost nothing moved.** Every window-based method (`rolling_mean`, `rolling_median`,
`seasonal_naive`, `weekly_hurdle`) only ever looks at a fixed trailing window — 30 days, 7 days, 12
weeks — by design. History sitting behind that window is structurally invisible to them, so adding 5
more years behind it changes nothing, by construction. `ewma` and `tsb` do use their whole history,
but decay it exponentially (α=β=0.1): by ~100–200 days back, older observations barely register any
more, so even they only picked up a small benefit — and only on the handful of SKUs whose *real*
history was short enough that a longer synthetic warm-up measurably stabilised their very first
estimates.

**What this rules out.** "Not enough data" was not the bottleneck. If it had been, feeding the models
years more history — even statistically well-behaved history — should have moved the error down
noticeably. It didn't, which is itself informative: it corroborates #3's reading that the remaining
error is intrinsic day-to-day volatility in this store's demand, not a symptom of a short observation
window that more years would fix.

---

## 5. Is there a model built specifically for sparse demand? Yes — already tried

Croston, SBA, and TSB (`forecasting/intermittent.py`) are the standard textbook answer to exactly this
problem, and all three are already in every benchmark run in this project. They didn't top the
leaderboard, for a documented, structural reason rather than mistuning: Croston and SBA update their
size/interval estimate only on periods when demand actually arrives, so an SKU that sold four times
and then stopped keeps forecasting its old rate forever (pinned by
`test_croston_cannot_see_trailing_zeros`). TSB is the fix — it updates demand probability every
period, including the zeros, so its forecast decays on a dying SKU
(`test_tsb_decays_on_a_dead_sku_where_croston_holds_flat`) — and TSB is the one of the three that
prices the full catalogue and sits competitively on error. Croston/SBA remain in the benchmark as the
un-fixed baseline TSB is the answer to.

Two sparse-demand-specific approaches were considered and **not** attempted, noted here as open
options rather than tried-and-rejected:

- **ADIDA** (Aggregate-Disaggregate Intermittent Demand Approach) — the formal version of what
  §1's weekly hurdle model approximates: aggregate to a coarser bucket, forecast there, disaggregate
  back down.
- **Willemain's bootstrap method** — resample chunks of real historical demand to build a distribution
  of plausible outcomes rather than a single point forecast. Genuinely different in kind from
  everything tried so far (all of which produce one number); not attempted here.

A non-sparse-specific method was also already tried in an earlier pass of this project and is worth
naming for contrast: XGBoost placed 7th of 8 methods (Divergence #9), so "throw a more powerful
general-purpose model at it" has been tried too, separately from this document, and also did not win.

---

## What this does not say

- **Not** that `weekly_hurdle_12w` should replace `rolling_mean_30` in production. It's a stronger
  candidate than anything found before it, but model selection is B3, and Table 2's fill-rate/holding
  trade-off (§1 above) still favours `rolling_mean_30` on the SKUs it does price.
- **Not** that fitted/ML approaches to this problem are hopeless in general — only that a per-SKU
  fitted classifier, on this catalogue's amount of history, is. Pooling across SKUs remains untested.
- **Not** that more data could never help. It's specific to *this* mechanism: window-based methods
  structurally can't use it, and exponentially-smoothed methods forget it within ~100-200 days
  regardless of how much exists further back. A method designed to actually use multi-year history
  (e.g. a real seasonal model with a multi-year period) was not tested here.
- **Not** a claim about intermittent retail demand in general — every number in this document is
  specific to `ustore.db`'s real 266-SKU, 821-day catalogue.

---

## Reproducing it

```bash
python scripts/model_benchmark.py               # includes weekly_hurdle_12w and logistic_hurdle
python tools/aggregation_level_test.py           # section 3
python tools/synthetic_augment_test.py           # section 4 (--years N to change the horizon)
```

`tools/synthetic_augment_test.py` uses a fixed random seed (20260902) — its numbers reproduce exactly,
not just directionally.

## Related

- `docs/DEGENERATE_FORECAST.md` — #21, the mechanism §1's baseline (`rolling_median_30`'s zero
  forecasts) and §3's floor both trace back to
- `docs/FORECAST_METHOD_COMPARISON.md` — the two earlier "tried and rejected" methods (`ewma_a0.1`,
  `rolling_q75_30`), same negative-result convention this document follows
- `docs/SERVICE_LEVEL_FRONTIER.md` — #22, the frontier `weekly_hurdle_12w`'s fill rate (0.7285) should
  be read against
- `forecasting/hurdle.py` — both models from §1 and §2
- `forecasting/evaluate.py` — the shared harness and its leakage guarantee, load-bearing for why §4's
  design is honest
