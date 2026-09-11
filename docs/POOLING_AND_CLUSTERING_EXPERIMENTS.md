# Pooling, categorization, and clustering — a full experiment log

Follow-up to `docs/SPARSE_DEMAND_EXPERIMENTS.md`, prompted by the adviser's suggestion to
categorize items (apparel / non-apparel) to see if it improves the ML baselines
(`forecasting/ml_models.py`). That single suggestion opened into eleven separate
experiments, because the first result was ambiguous enough to need explaining, and
explaining it kept surfacing the next question. Recorded here in the order they actually
happened, including a measurement bug that was found and corrected mid-session — a
negative result that turned out to be an artifact is worth exactly as much of a paper
trail as a real one.

**None of this selects a model.** Like `model_benchmark.py` and `SPARSE_DEMAND_EXPERIMENTS.md`,
this is a measurement. Model selection is deferred decision **B3**.

---

## CORRECTION (post-publication audit)

An independent audit (`FORECAST_EXPERIMENT_AUDIT.md`) found two real defects in the code
this document reports on, both since fixed in `scripts/model_benchmark_category.py` and
`forecasting/category.py`:

1. **`fsn_class` leak (experiments #2, #4, #7, #8, and #11's `speed`/`category_speed`/
   `cluster` groupings).** `Dim_Product.fsn_class` is computed by
   `scripts/step3_fsn_classification.py` from the WHOLE `Fact_Sales` table, no date bound -
   using it to choose a walk-forward fold's pooling group leaked each fold's own future
   into that fold's group assignment. Measured directly: recomputing the label from only
   the data available at fold 0's origin flips 54 of 266 SKUs (20%). `forecasting/clustering.py`
   had the identical defect independently - its features (density, size, volatility, volume)
   were built from each SKU's WHOLE series, not fold-scoped either.
2. **Safety-stock service class leak, on every `--by` path.** The fill-rate/units-held
   figures throughout this document used the same static, full-history `fsn_class` for the
   Z-score lookup regardless of grouping choice - so fill rate was contaminated even for
   the `category` and `product_type` runs, which are otherwise leak-free.

**Fixed:** `forecasting.category.fold_scoped_speed_labels` and
`scripts/model_benchmark_category.py::build_group_fn`/`build_speed_fn` now recompute both
the pooling label and the service class PER FOLD from only that fold's pre-origin data.
`tests/test_category_leakage.py` pins the mechanism. `category` and `product_type` were
never affected on the grouping side (both are static product attributes), but their
fill-rate numbers were affected by defect 2 and have also been re-run.

**Also corrected:** the "What this does not say" section below claimed
`weekly_hurdle_12w` "remains the strongest single result on fill rate and is still the
project's committed champion." That is false - it is 6th of 8 in the committed benchmark
(0.7285 vs. `ets`'s 0.7768) - and has been struck.

Re-run numbers for the affected experiments are in the sections below, clearly marked.
Experiments #1, #3, #5, #6, #9, #10 (category/product_type grouping, the synthetic-history
mechanism test, the MASE-aggregation audit, the sparsity diagnostics, and the trading-day
finding) did not use `speed`/`category_speed`/`cluster` and are unaffected by defect 1;
their fill-rate figures, where reported, were affected by defect 2 only where a `speed`-
derived class was in play, i.e. nowhere in those sections - none reports fill rate.

---

## Summary

| # | Experiment | Result |
|---|---|---|
| 1 | Pool SKUs into one shared model per category (apparel / non-apparel) | **Negative.** Helped MAE/coverage, hurt MASE — damage concentrated in near-flat/slow SKUs |
| 2 | Also split by fast/slow mover | **CORRECTED to negative.** The original "partial positive" used a leaked grouping label (see correction notice) - fixed, it is the *worst* grouping tested, not the best |
| 3 | Split into finer product types (clothes, drinkware, bags, ...) | **Negative.** No better than category alone — drinkware, the most "similar" group, was the single worst performer |
| 4 | Add 5 years of bootstrapped synthetic history | **Positive, but overstated at first** — a measurement bug inflated the apparent gain; corrected, the gain is real but smaller |
| 5 | Same synthetic test on the 5 statistical methods not previously covered | **Mixed, mechanistic.** Helps *fitted* methods (Croston, SBA, logistic hurdle); zero effect on fixed-window methods (ETS, rolling quantile) — confirms the mechanism, not just the direction |
| 6 | Audit how MASE is aggregated across SKUs | **Found a real problem.** Mean MASE is dominated by 10 near-dead SKUs (up to 73% of the total); median/weighted/global tell a different, more defensible story |
| 7 | Pooled logistic hurdle (the fix `SPARSE_DEMAND_EXPERIMENTS.md` §2 named but never built) | **Positive.** Improved on every metric |
| 8 | Add the academic calendar (semester/exam/break flags) as model features | **Negative / null.** Median change in per-SKU MAE was exactly 0.0 |
| 9 | Three diagnostics: density buckets, aggregation, controlled simulation | **Converged on one finding.** Sparsity is not the bottleneck — demand whose *rate* shifts (semester cycles) is |
| 10 | Data audit: which days are real trading days | **Found a real problem.** 21% of modelled days aren't trading days (2 missing months + closed Sundays), counted as zero demand |
| 11 | Data-driven clustering (K-means on demand behavior) instead of manual categories | **Best result of the whole investigation - CONFIRMED after the correction.** Had the same leak as #2, but re-scoring it fold-by-fold barely changed its numbers (unlike #2, which collapsed). Beats every manual grouping, and beats the per-SKU baseline on weighted/global MASE |

---

## 0. Why pooling needed building first

`forecasting/ml_models.py`'s tree models (XGBoost, LightGBM, Random Forest) each fit **one
model per SKU**, using only that SKU's own lagged history. A category label does nothing
to a method that only ever looks at one SKU in isolation — categorizing only becomes
testable once SKUs can be trained *together*. `forecasting/ml_models.pooled_fit_predict`
and `scripts/model_benchmark_category.py` were built for exactly this: pool training rows
across every SKU in a group, fit ONE shared model per group per fold, forecast each SKU
recursively from that shared model. Everything below reuses this same machinery with a
different definition of "group."

Scored on the same walk-forward folds as `model_benchmark.py` throughout (all series share
one calendar index, so fold origins are identical for every SKU regardless of grouping).

---

## 1. Category alone — negative

`forecasting/category.py`: `Dim_Product.category` where set (read-only, never modified),
a keyword match on `item_name` where it is not (190 of 266 moving SKUs had no label).
85 apparel / 181 non-apparel.

| method | MAE (per-SKU &rarr; pooled) | MASE (per-SKU &rarr; pooled) |
|---|---|---|
| xgboost | 19.45 &rarr; 15.42 | 5.67 &rarr; 12.71 |
| random_forest | 23.89 &rarr; 24.76 | 6.91 &rarr; 18.30 |
| lightgbm | 30.99 &rarr; 32.38 | 8.34 &rarr; 119.37 |

MAE/coverage improved (266/266 SKUs priced vs. 254-261); MASE got much worse. Root cause,
confirmed by direct comparison: **90 of 266 SKUs have a near-zero MASE denominator**
(flat/intermittent training history). A pooled model, trained on a category's typical
(more active) demand shape, over-predicts on SKUs that are essentially dead — and MASE is
unbounded when its denominator is tiny. On the other 176 "normal" SKUs, pooled and
per-SKU MASE were close (3.39 vs. 3.16 for xgboost).

---

## 2. Category + fast/slow — CORRECTED: the apparent gain was mostly a leak

**Original claim (retracted): partial positive, "roughly halved the damage."** That used
`Dim_Product.fsn_class` directly - see the correction notice at the top of this document.
Re-run with `forecasting.category.fold_scoped_speed_labels` (fold-scoped, leak-free):

| metric (xgboost) | per-item (committed) | category+speed, LEAKED | category+speed, FIXED |
|---|---|---|---|
| MAE | 19.45 | 17.80 | 24.18 |
| MASE (weighted) | 5.21 | 4.73 | **5.16** |
| MASE (global) | 2.23 | 2.04 | **2.70** |

| metric (random_forest) | per-item (committed) | category+speed, LEAKED | category+speed, FIXED |
|---|---|---|---|
| MAE | 23.89 | 22.50 | 34.54 |
| MASE (weighted) | 6.15 | 5.79 | **6.96** |
| MASE (global) | 2.73 | 2.57 | **3.86** |

**The leaked version beat per-item on both models and both robust metrics. The fixed
version loses to per-item on every one of them.** The entire apparent improvement from
splitting by speed was the leak - once each fold's Fast/Slow label can no longer see that
fold's own future, `category_speed` pooling is a net loss, not a partial win. This is the
single most important correction in this document.

---

## 3. Finer product types — negative

`forecasting.category.classify_product_type`: TEMPORARY/exploratory keyword classifier,
8 buckets (clothes 91, accessories 58, bags 31, drinkware 22, misc 21, stationery 21,
toys_plush 13, umbrella 9). No DB column backs this - keyword-only, analysis-only.

| method | category+speed MASE | product_type MASE |
|---|---|---|
| xgboost | 9.16 | 13.80 (worse) |
| random_forest | 10.80 | 17.74 (worse) |
| lightgbm | 90.60 | 160.45 (worse) |

**Drinkware - 22 tumblers/mugs, about as "similar" a product group as exists in this
catalogue - was the single worst-performing group of all** (lightgbm MASE 1366 on that
group alone). Product *type* similarity does not predict poolability; product *speed*
does. This directly motivated experiment #11.

---

## 4. Synthetic history — positive, with a correction mid-experiment

**NOT YET RE-VERIFIED against the `fsn_class` leak fix (see correction notice).** This
section's numbers use `category+speed` grouping, the same leak §2 was corrected for, and
have not been re-run with `fold_scoped_speed_labels`. Given §2's fixed result reversed
sign entirely, treat every MASE figure below as unconfirmed until this is re-run - the
MAE/RMSE-vs-MASE mechanism finding (that synthetic data helps raw error but not
necessarily relative error) is still architecturally sound, but the specific numbers are
not.

5 years of bootstrapped pre-history per SKU (`tools/synthetic_augment_test.py`'s existing,
previously-vetted method: weekday-stratified resample of that SKU's own real distribution,
prepended, never appended/substituted - the scorer never sees a synthetic day).

**First pass (bug):** MASE denominator was computed from the augmented training slice,
which includes synthetic days. Caught because `ets` and `rolling_q75_30` - both fixed
trailing-window methods - produced **byte-identical predictions** with or without
synthetic data, yet showed MASE improving ~3x. Root cause: synthetic block-to-block noise
inflated the denominator with no actual forecast change.

**Fix:** MASE always scaled against the REAL portion of training only
(`scripts/model_benchmark_category.py::_real_denom`,
`service_metrics_real_offset` for the matching safety-stock sigma).

**Corrected result** (category+speed+synthetic, full 266 SKUs):

| method | category+speed MASE | + synthetic, corrected MASE |
|---|---|---|
| xgboost | 9.16 | 11.65 (worse) |
| random_forest | 10.80 | 14.72 (worse) |
| lightgbm | 90.60 | 37.43 (better, still far behind) |

MAE/RMSE improved genuinely for all three (e.g. xgboost 17.80&rarr;16.09), but on MASE
only lightgbm improved after correction - xgboost and random_forest got worse. The
initial (buggy) run had claimed xgboost/random_forest beat the committed per-SKU models;
that claim was retracted once the fix was in.

---

## 5. Synthetic history on the other 5 methods — mixed, and mechanistic

`tools/synthetic_augment_test.py` originally covered 7 methods; extended
(`tools/synthetic_augment_all_methods.py`) to the remaining 5, with the same real-only-
denominator correction as #4 built in from the start.

| method | MAE real &rarr; +synthetic | MASE real &rarr; +synthetic (corrected) | verdict |
|---|---|---|---|
| croston | 29.3 &rarr; 21.9 | 12.50 &rarr; 6.62 | genuine improvement |
| sba | 28.2 &rarr; 21.2 | 12.08 &rarr; 6.49 | genuine improvement |
| ets | 23.6 &rarr; 23.6 | 8.32 &rarr; 8.32 | **zero effect** |
| rolling_q75_30 | 15.2 &rarr; 15.2 | 5.69 &rarr; 5.69 | **zero effect** |
| logistic_hurdle | 15.0 &rarr; 13.1 | 7.33 &rarr; 5.39 | genuine improvement |

Confirms the mechanism, not just the direction: methods that only look at a fixed recent
window (ETS, rolling quantile) are structurally blind to history further back, so more of
it changes nothing - their predictions are provably identical. Methods that *fit*
something (Croston/SBA's smoothed rate, logistic hurdle's regression) genuinely benefit
from more training rows. This matches `SPARSE_DEMAND_EXPERIMENTS.md` section 4's original
negative result for window-based methods and extends it correctly to the fitted ones,
which that section had not tested.

---

## 6. The MASE aggregation problem

Diagnostic: 1,290 of 3,192 (SKU, fold) pairs across today's runs have a MASE denominator
below 1.0. `tools/robust_metric_comparison.py` recomputes every run under five
aggregations: mean (what `summarise()` reports today), median, 10%-trimmed,
demand-weighted, and global (one ratio: total error / total scale), plus RMSSE
(M5's squared-error analogue).

**The 10 worst SKUs alone produced 23-73% of the total mean MASE**, depending on the run.
Example (xgboost, category+speed):

| aggregation | value |
|---|---|
| mean (what's been reported all along) | 9.16 |
| median | 3.06 |
| demand-weighted | 4.73 |
| global | 2.04 |

Reading mean MASE alone materially overstates how much pooling "hurt." Every table after
this point in the project should be read with median/weighted/global alongside the mean,
not instead of it - they answer different questions (mean: equal weight per SKU; weighted/
global: weighted by how much the SKU actually matters to the business).

---

## 7. Pooled logistic hurdle — positive

`SPARSE_DEMAND_EXPERIMENTS.md` section 2 diagnosed `logistic_hurdle`'s underperformance as
"too little signal for a 10-parameter model to learn from" on thin per-SKU history, and
named the fix - pooling across similar SKUs - as something "not attempted here." Built:
`forecasting.hurdle.pooled_logistic_hurdle_fit_predict`. Pools the sale-PROBABILITY
classifier across a category+speed group (every feature is already scale-free); keeps each
SKU's own size estimate (pure scale, should not be pooled).

**CORRECTED (see notice at top): re-run with `fold_scoped_speed_labels` closing the
group-assignment leak.** Unlike #2, this finding survives - smaller margin, same
direction:

| variant | MAE | MASE (median) | MASE (weighted) | MASE (global) |
|---|---|---|---|---|
| logistic_hurdle, per-SKU (baseline) | 15.01 | 2.21 | 4.53 | 1.72 |
| logistic_hurdle, pooled, LEAKED | 14.48 | 2.00 | 4.35 | 1.66 |
| **logistic_hurdle, pooled, FIXED** | **14.75** | **2.08** | **4.43** | **1.69** |

Still improves on every metric after the fix, just by less than the leaked run reported.
Combined-with-synthetic (#4/#5) numbers below are from the pre-fix run and have not been
re-verified - treat the "do not stack" finding as unconfirmed pending a re-run, same
caveat as #4:

Combined with synthetic history (#4/#5), the two do not stack
(pooled+synthetic MASE 1.952 vs. pooled-alone 2.001 vs. per-SKU+synthetic 1.778) - both
address the same underlying problem (too few training rows), so fixing it once mostly
exhausts the benefit.

---

## 8. Calendar-aware hurdle — negative / null

Motivated by #9 below: if the bottleneck is demand whose rate shifts with the academic
calendar, telling the model about the calendar should help. `Dim_Date` already carries
`is_enrollment_period`/`is_exam_week`/`is_event_day`/`is_sem_break`, populated with real
values, unused anywhere in `forecasting/`. Added as extra logistic-regression columns
(`forecasting.hurdle.calendar_logistic_hurdle_fit_predict`), looked up by array position
using the same trick `_weekday_onehot` already relies on (every SKU's series starts on the
same calendar day).

| method | MAE | MASE (mean) | MAPE |
|---|---|---|---|
| logistic_hurdle | 15.01 | 7.33 | 226.3% |
| calendar_logistic_hurdle | 15.00 | 7.02 | 221.0% |

Only 114/266 (43%) of SKUs improved at all; **median change in per-SKU MAE was exactly
0.0**. Reading: the calendar signal exists and is real, but a per-SKU classifier still
doesn't have enough sale events during any given exam-week/sem-break window to learn much
from a handful of extra binary columns - the same thin-data problem #7 solved for the
baseline features, re-appearing for the new ones. Pooling the calendar-aware version was
not attempted; it is the natural next step if this is revisited.

---

## 9. Three diagnostics: sparsity is not the bottleneck

Three experiments on REAL data only (no synthetic anything), converging on one answer.

**9a. Accuracy by demand density** (`tools/density_vs_accuracy.py`) - 266 real SKUs bucketed
by share of days with any sale:

| density bucket | # SKUs | weekly_hurdle_12w MASE |
|---|---|---|
| almost never (&lt;2%) | 82 | 2.45 |
| occasional (5-10%) | 50 | 3.08 |
| frequent (&gt;25%) | 14 | 1.03 |

Accuracy improves sharply with density - but only 14 of 266 SKUs (5%) reach that density.

**9b. Aggregation** (`tools/aggregation_density_test.py`) - same real data, summed into
coarser series so zero-days become rare:

| level | demand density | MAPE | MASE (median) |
|---|---|---|---|
| per SKU | 7% | 203% | 1.95 |
| per category (2) | 51% | 86% | 1.80 |
| whole store (1) | 51% | 86% | 1.91 |

MAPE improves dramatically (fewer zero-actual folds to break it); **MASE barely moves at
all**. The MAPE improvement is a metric artifact of aggregation, not a real gain in
forecastability - directly extends `SPARSE_DEMAND_EXPERIMENTS.md` section 3's finding with
a metric that isn't fooled by zero-inflation.

**9c. Controlled simulation** (`tools/sparsity_sensitivity_sim.py`) - synthetic worlds
where sparsity is the ONLY variable (independent days, sale sizes drawn from the real
catalogue's pooled distribution), trained AND tested inside the same world so the result
is honest:

| density | rolling_mean | tsb | weekly_hurdle |
|---|---|---|---|
| 1% | 1.05 | 1.03 | 1.03 |
| 10% | 0.87 | 0.79 | 0.74 |
| 50% | 1.01 | 0.97 | 0.90 |

**MASE sits around 0.8-1.0 at every density tested, including 1%.** These methods handle
sparse-but-STABLE demand fine.

**Conclusion:** (9a) says density alone predicts real-world accuracy; (9b) says making
real data denser through aggregation does not actually help; (9c) says sparsity in a
controlled, stable-rate setting does not hurt. The only way to reconcile all three: what
defeats these methods on the real catalogue is not the density of zeros, it is that the
underlying RATE shifts over time (semester cycles, product lifecycles, one-off events) -
aggregation and synthetic history both leave that instability intact.

---

## 10. Data audit: 21% of modelled days are not trading days

While investigating #9's whole-store aggregate: **405 of 821 days show zero store-wide
sales.** Breakdown:

- **June-July 2024: 61 consecutive days with NO Fact_Sales rows at all** - a data gap,
  not necessarily zero demand.
- **Sundays: 88% zero** (only 4 of 209 Sundays are flagged `is_store_closed` in `Dim_Date`
  - that flag covers special closures, not the store's regular weekly schedule).
- Together: 169 of 821 days (21%) are plausibly not real trading days, currently modelled
  as "customers wanted nothing."

Excluding them raises mean per-SKU demand density from 7.3% to 8.8% (+21% relative). Not
re-run through the full benchmark - flagged here as a data-quality fix worth making before
the next forecasting pass, and as a concrete question for the client (confirm the June-July
2024 gap).

---

## 11. Data-driven clustering — the strongest result in the investigation

Adviser's follow-up suggestion: cluster the catalogue instead of hand-labelling it.
`forecasting/clustering.py`: K-means on five REAL behavioral features per SKU - demand
density, mean nonzero sale size, coefficient of variation of nonzero sales, log(total
demand), log(unit price). No reference to `category` or `fsn_class` at all, so the result
is independent of (and directly comparable against) every manual grouping above.

K=4 chosen via elbow (`forecasting.clustering.choose_k`): inertia drop slows sharply after
K=4 (147.9 &rarr; 94.9 &rarr; 81.9), silhouette 0.327 vs. a peak of 0.333 at K=3.

**What the data found, unprompted:** cluster0 (31 SKUs) - high-density, high-value steady
sellers. cluster1 (117 SKUs) - very sparse, the hard tail. cluster2 (2 SKUs) - extreme
bulk-order outliers, too small to pool meaningfully. cluster3 (116 SKUs) - moderate mixed
sellers. **Clusters 0 and 3 both mix apparel and non-apparel freely** - confirming #3's
finding that product type does not predict poolability.

**CORRECTED (see the notice at the top of this document): `forecasting/clustering.py`'s
features were originally built from each SKU's WHOLE series, the identical leak class as
`fsn_class` above. Re-run with per-fold-sliced features.** The result is the reason this
section is still titled "the strongest result in the investigation" rather than retracted
like #2 - unlike `category_speed`, clustering's numbers barely moved:

| method | grouping | MAE | MASE (weighted) | MASE (global) |
|---|---|---|---|---|
| xgboost | per-SKU (committed) | 19.45 | 5.21 | 2.23 |
| xgboost | category+speed, FIXED (§2) | 24.18 | 5.16 | 2.70 |
| xgboost | product_type (§3, unaffected - static labels) | 16.43 | 4.99 | 1.88 |
| xgboost | cluster (K=4), LEAKED | 14.48 | 4.01 | 1.66 |
| xgboost | **cluster (K=4), FIXED** | **14.78** | **4.04** | **1.69** |
| random_forest | per-SKU (committed) | 23.89 | 6.15 | 2.73 |
| random_forest | category+speed, FIXED (§2) | 34.54 | 6.96 | 3.86 |
| random_forest | product_type (§3, unaffected) | 19.69 | 6.07 | 2.25 |
| random_forest | cluster (K=4), LEAKED | 16.89 | 4.71 | 1.93 |
| random_forest | **cluster (K=4), FIXED** | **17.36** | **4.69** | **1.98** |

**Cluster (K=4) still wins every comparison in this table after the fix** - the leaked and
fixed cluster rows are within noise of each other (xgboost global MASE 1.66 &rarr; 1.69;
random_forest 1.93 &rarr; 1.98), while `category_speed`'s FIXED row is now the *worst*
grouping tested, having been the *best* under the leak. Per-cluster breakdown is equally
stable: cluster0 still scores MASE 1.4-2.3 (vs. 1.4-2.3 before the fix), genuinely
competitive with per-SKU modeling. The hard tail (cluster1) stays hard, as it does
everywhere in this document - but unlike manual grouping, it does not drag cluster0 down
with it.

**Why clustering survived the fix and category_speed did not.** `fsn_class` is a hard
80th-percentile cross-sectional cutoff - measured in the correction notice, 20% of SKUs sit
close enough to that boundary to flip sides depending on which window computes it, so
leaking the future materially changes WHO gets pooled with whom. The clustering features
(demand density, size, volatility) describe how a SKU behaves on average, and for most
SKUs that does not change much between the first and second half of an 821-day history - a
steady seller is a steady seller in both windows, a near-dead SKU is near-dead in both. The
grouping barely moves when the window does, so there was little leak to close in the first
place. That is itself a finding: **a behavioural feature that is stable over time is a more
robust basis for pooling than a hard percentile cutoff, independent of which one performs
better on any single (potentially leaked) run.**

**Caveats:** cluster2 (2 SKUs) is too small to pool meaningfully and should be merged into
its nearest neighbour in any production use. K=4 is defensible, not uniquely correct -
K=3 has a marginally higher silhouette score. Not yet tried combined with synthetic
history (#4) or the calendar features (#8); both are natural next steps given how
independently each has performed. #4, #7, and #8 all used the (now-corrected)
`category_speed`/`speed` grouping or the pre-fix safety-stock service class and have not
yet been re-run with the fix - their MASE/MAE conclusions are believed directionally
similar to #2's post-fix result (i.e. weaker than reported) but this is not yet measured
and should not be cited until it is.

---

## What this does not say

- **Not** that any of these pooled/clustered variants should replace the current
  production model. **Corrected** (see the correction notice above): `weekly_hurdle_12w`
  is NOT the strongest result on fill rate - it is 6th of 8 methods in the committed
  benchmark (0.7285), behind `ets` (0.7768), `ewma_a0.1` (0.7755), `rolling_mean_30`
  (0.7746, current production), and `tsb` (0.7538). Its real strengths are MASE (lowest of
  the eight) and coverage (140 SKUs priced vs. production's 79). There is no "committed
  champion" - model selection is deferred decision B3, and B3 is open, gated on B2. What
  this document CAN say: clustering is the strongest result on error metrics among the ML
  baselines specifically, among the variants tested here.
- **Not** that MASE should be reported as a bare mean going forward - #6 is a methodology
  finding that applies to every table in this document and to `model_benchmark.py`'s
  existing output.
- **Not** that the calendar doesn't matter - #8 says a per-SKU model can't extract much
  from it on this little data per SKU; a pooled calendar-aware model (combining #7's fix
  with #8's features) was not attempted and is an open question, not a closed one.
- **Not** that clustering has been validated for production use - K, the feature set, and
  the tiny-cluster handling are all first-pass choices that would need to be pinned down
  (ideally against a second time period) before this became more than an experiment.
- **Not** a claim about intermittent retail demand in general - every number here is
  specific to `ustore.db`'s real 266-SKU, 821-day catalogue, exactly as
  `SPARSE_DEMAND_EXPERIMENTS.md` says of its own numbers.

---

## Reproducing it

```bash
# 1-3: category / speed / product-type pooling
python scripts/model_benchmark_category.py --by category
python scripts/model_benchmark_category.py --by category_speed
python scripts/model_benchmark_category.py --by product_type

# 4: synthetic history on the pooled tree models (corrected denominator)
python scripts/model_benchmark_category.py --by category_speed --synthetic-years 5

# 5: synthetic history on croston/sba/ets/rolling_q75/logistic_hurdle
python tools/synthetic_augment_all_methods.py --years 5

# 6: robust MASE aggregations across every run above
python tools/robust_metric_comparison.py

# 7-8: pooled and calendar-aware logistic hurdle
python scripts/model_benchmark_category.py --by category_speed --methods logistic_hurdle weekly_hurdle_12w
python tools/calendar_hurdle_test.py

# 9: sparsity diagnostics
python tools/density_vs_accuracy.py
python tools/aggregation_density_test.py
python tools/sparsity_sensitivity_sim.py

# 11: data-driven clustering
python scripts/model_benchmark_category.py --by cluster --k 4 --methods xgboost random_forest logistic_hurdle weekly_hurdle_12w
```

`tools/synthetic_augment_test.py` and `tools/sparsity_sensitivity_sim.py` use fixed random
seeds - their numbers reproduce exactly, not just directionally.

## Related

- `docs/FORECAST_VALIDATION.md` - an independent validation of THIS document against the
  objective rather than against its own metrics. Confirms every number here reproduces exactly
  from the committed fold CSVs, and finds that #11's clustering result - genuinely the best
  demand-weighted MASE in the repository - is strictly dominated by `rolling_mean_30` on fill
  rate per unit held, and that its 266/266 coverage recovers ~0.5% of the demand the incumbent
  misses. Also the origin of the pooled calendar-aware hurdle this document's #7/#8/#9 point at
- `docs/SPARSE_DEMAND_EXPERIMENTS.md` - the four experiments this document follows up on,
  in particular section 2 (the pooling fix built in #7) and section 4 (the synthetic-data
  mechanism extended in #5)
- `docs/DEGENERATE_FORECAST.md` - #21, the "wins on error, prices nothing" trap that
  `weekly_hurdle_12w` (mentioned throughout as the standing champion) was built to avoid
- `docs/SERVICE_LEVEL_FRONTIER.md` - #22, the fill-rate frontier the "what this does not
  say" section measures the champion against
- `forecasting/category.py`, `forecasting/clustering.py`, `forecasting/hurdle.py` - the
  grouping and pooled-model code this document exercises
- `forecasting/evaluate.py` - the shared harness and its leakage guarantee, load-bearing
  for why #4's synthetic-history design (and its bug fix) is honest
