# Fast-moving benchmark: raw tally sheets vs cleaned star schema

Twenty-nine forecasting methods, scored on the Fast-moving segment only, run twice — once on the raw
monthly workbooks and once on the cleaned star schema — and broken down by product category.

This is a **measurement, not a model selection**, on the same terms as `model_benchmark.py` and
`docs/SPARSE_DEMAND_EXPERIMENTS.md`. No winner is declared; selection is deferred decision **B3**,
downstream of **B2**.

**Reproduce:**

```
python scripts/benchmark_fast_raw_vs_clean.py      # ~70 min: 29 methods, 2 stages
python scripts/paired_cleaning_effect.py           # the controlled same-item comparison
python scripts/polish_benchmark_workbook.py        # colour scales + charts
python scripts/export_fastmoving_benchmark_csv.py  # the flat two-CSV view
```

Outputs:

| File | What it is |
|---|---|
| `data/fastmoving_benchmark_summary.csv` | One row per method per stage, **same columns as `data/model_benchmark_summary.csv`** with `stage` prepended |
| `data/fastmoving_benchmark_results.csv` | One row per SKU per method per fold, **same columns as `data/model_benchmark_results.csv`** with `stage` prepended and `fsn_class` / `in_fast_set` appended |
| `data/USTore_FastMoving_Model_Benchmark.xlsx` | 17 sheets: the category breakdown, the RM window sweep, the paired cleaning analysis, per-SKU detail |
| `data/fastmoving_benchmark_folds.csv` | The raw fold output the export script reads |

The two CSVs are the same shape as the existing benchmark's, so anything that already reads
`model_benchmark_summary.csv` reads this too. The summary is reproducible from the results file:

```python
results[results.in_fast_set].groupby(["stage", "method"])
```

---

## 1. What is being compared

| | RAW | CLEAN |
|---|---|---|
| Source | `data/USTore_sales_long_with_zeros.csv` | `Fact_Sales` in `ustore.db` |
| Keyed on | the item name **exactly as spelled in the workbook** | `product_id` |
| Controlled vocabulary | not applied | applied (step 1) |
| Proportional allocation | not applied | applied |
| Supplier mapping | not applied | applied |

Both stages are reindexed onto the same complete daily calendar (2024-05-02 … 2026-07-31, 821 days),
so the fold layout is identical and no method or stage can be advantaged by a different split.

**Segment.** Fast-moving only. Slow and Non-moving SKUs are excluded, which is what section 3.3.2
prescribes: only F and HVL items go to a forecasting model at all.

**Validation.** Walk-forward, expanding window, horizon 30 days, 12 rolling origins per SKU,
minimum 60 days of training history. Every method sees identical folds; every training slice ends
strictly before its origin. The scoring unit is a **30-day aggregate per fold**, not 30 daily points,
because the 30-day total is the quantity the reorder decision consumes.

### The caveat that bounds every raw-vs-clean number

The two stages **do not contain the same SKUs**, and they cannot: cleaning is what decides what an
SKU *is*. So the headline raw-vs-clean comparison is a **population** comparison — how forecastable
is the Fast segment that each stage produces — not a per-item one. The per-item question is answered
separately, in section 4, on the items cleaning actually changed.

---

## 2. The two Fast sets on the clean side

`Summary_CLEAN` uses the **pipeline** Fast set — `Dim_Product.fsn_class` as written by
`step3_fsn_classification.py`, which weights imputed rows at 0.5 and drops censored zero-sale days
from the ADUS denominator. That is what the repo actually forecasts.

`Summary_CLEAN_ruleF` uses the **recomputed** rule: ADUS ≥ the 80th percentile of the moving
population, unweighted, no censoring exclusion. That is the only rule the raw stage can support,
because neither flag exists before step 2 creates it.

Both are slices of **one** benchmark run over the union of the two sets, so they share folds and
predictions exactly. `Raw_vs_Clean` compares RAW against the rule-matched set, so the only thing
differing between its two columns is the data.

That the two definitions disagree at all is itself worth recording — see `Fast_Set_Overlap`.

---

## 3. Methods scored

| Family | Methods |
|---|---|
| Naive references | `naive`, `seasonal_naive_7` |
| Rolling mean (the RM sweep) | `RM3_3day`, `RM6_6day`, `rolling_mean_14`, `rolling_mean_30`, `rolling_mean_60`, `RM3_3month_90d`, `RM6_6month_180d` |
| Other central tendency | `rolling_median_30`, `rolling_q75_30`, `ewma_a0.1`, `ewma_a0.3` |
| Intermittent demand | `croston`, `sba`, `tsb`, `weekly_hurdle_12w` |
| Exponential smoothing | `ets` (Holt-Winters, damped trend + weekly season) |
| Machine learning, per SKU | `random_forest`, `extra_trees`, `gradient_boosting`, `ridge`, `xgboost`, `lightgbm` |
| Machine learning, pooled | `xgboost_pooled`, `lightgbm_pooled`, `random_forest_pooled` |
| Prophet | `prophet_plain`, `prophet_cal` |

**RM3 / RM6 are scored under both readings.** The request did not say whether they meant 3/6 *days*
or 3/6 *months*, and on a daily series with a 30-day horizon those are different models. Both are in
the table: `RM3_3day` / `RM6_6day` and `RM3_3month_90d` / `RM6_6month_180d`. The `RM_Windows` sheet
puts the whole window sweep (3, 6, 14, 30, 60, 90, 180 days) side by side so the shape of the
window-length curve is visible rather than inferred from two points.

### How the ML methods are set up

Each learner is a **direct 30-day-total regression**, not a recursive daily model: features
observable at the origin → the next 30 days' total, spread evenly across the horizon. The harness
scores the sum, so the spread does not affect the score; what it avoids is compounding 30 steps of
prediction error against a target nobody measures. Twenty-three features per origin — lags,
rolling means and standard deviations at 7/14/30/60/90 days, non-zero fractions, days since last
sale, a trend contrast, weekday. Full definitions in `forecasting/ml_models.py`.

Causality is enforced in one place: a training row at index *t* uses features from `y[:t]` and a
target of `sum(y[t:t+30])`, both strictly inside the fold's training slice.

The **pooled** variants fit one model per fold across every Fast SKU at once, rather than one model
per SKU. A per-SKU learner sees roughly 400 rows of a mostly-zero series; a pooled learner sees every
Fast item and can borrow demand shape across them.

### Prophet, finally scored

`model_benchmark.py` deliberately excludes Prophet (deferred decision **B5**) because a cmdstan build
is the toolchain gamble that made Chapter 4 unreproducible. That exclusion stands for the production
pipeline — nothing in `scripts/step*.py` imports `forecasting/prophet_model.py`. But the manuscript
commits to Prophet in sections 1.2, 2.1.4 and 3.3.2, and until now the repo had never measured it.
It is measured here, in two variants:

- `prophet_plain` — trend + weekly + yearly seasonality
- `prophet_cal` — plus section 3.3.2's academic-calendar regressors (`is_enrollment_period`,
  `is_exam_week`, `is_event_day`, `is_sem_break`, `is_store_closed`, `semester_week`) read from
  `Dim_Date`

Two departures from section 3.3.2, both of which make this a *lower* bound on effort, not a better one:

1. **MAP estimation, not `mcmc_samples=1000`.** MCMC changes the uncertainty interval, not the point
   forecast the 30-day aggregate is scored on, so the ranking is unaffected — but the interval
   widths section 3.3.2 specifies are not reproduced here and must not be quoted from this run.
2. **A regressor that is constant inside a fold's training slice is dropped for that fold.** An early
   origin can sit entirely outside any enrollment window; a zero-variance regressor carries no
   information while making the fit ill-posed.

---

## 4. Metrics, and why there are this many

| Column | Meaning |
|---|---|
| `mae`, `rmse` | Per-SKU error, averaged across SKUs. Units. |
| `mase` | MAE ÷ the mean absolute difference between consecutive 30-day **training** blocks. 1.0 = as good as predicting last month's total for this month. |
| `mase_median`, `mae_median` | The same, median instead of mean. MASE divides by a per-SKU denominator that can be tiny; a handful of such SKUs can move the mean by an order of magnitude without anything changing operationally. Read the two together. |
| `wmape_pct` | Σ\|error\| ÷ Σactual, per SKU, averaged. The percentage metric that survives intermittency. |
| `wmape_pooled_pct` | The same ratio computed over all SKUs at once — every unit counts once, rather than every SKU counting once. |
| `mape_pct` | Section 3.3.4's own metric, reported so the manuscript's ≤ 20% gate stays visible, with `mape_n_undefined` beside it. Undefined wherever a fold's actual is zero, which is common here. |
| `mae_pct_of_demand`, `rmse_pct_of_demand` | MAE and RMSE as a percent of the mean 30-day demand being forecast — the scale-free reading of the same two numbers. |
| `pct_better_than_naive_*` | Percent reduction against the `naive` row, per metric. |
| `pct_skus_beating_naive` | Share of SKUs where this method's MAE is strictly below naive's on identical folds. A win rate, not an average of averages. |
| `bias` | Mean (predicted − actual). Negative = under-forecasting, which on an inventory system means stockouts. |

**MASE here is not comparable to a MASE quoted elsewhere** unless the denominator matches. Every
method scores above 1 because the denominator is demanding on a series this intermittent.

---

## 5. Sheet map

| Sheet | What it holds |
|---|---|
| `README` | Definitions, provenance, caveats — inside the workbook, so it travels with the numbers |
| `Summary_CLEAN` | Headline table: every method on the pipeline Fast set |
| `Summary_RAW` | The same methods on the raw Fast set |
| `Raw_vs_Clean` | Per method, side by side, with percent change |
| `Summary_CLEAN_ruleF` | The rule-matched clean set (what `Raw_vs_Clean` compares against) |
| `RM_Windows` | The rolling-mean window sweep, both stages |
| `By_Category` | Every category × every method |
| `Category_Best` | Best method per category, by MASE and by MAE |
| `RM_by_Category` | The RM windows, per category |
| `Fast_Items` | Every Fast SKU: category, supplier, price, ADUS, sale-day count, best method |
| `Per_SKU_CLEAN` | Per SKU × per method metrics |
| `FSN_Raw_vs_Clean` | Classification counts and cutoffs on both stages |
| `Fast_Set_Overlap` | How much the Fast rosters agree |
| `Paired_Cleaning_Effect` | Section 4's controlled comparison, per method |
| `Paired_Per_Item` | The same, per item |
| `Series_Changed_By_ETL` | Every exact-name pair, with units moved by cleaning |
| `Segment_Profile` | What the Fast segment looks like on each side of the ETL |

---

## 6. Results

Run of 2026-09-04. 76 clean-side SKUs and 54 raw-side SKUs, 12 folds each, 45,240 scored
predictions. Full tables in the workbook; the six findings below are what the numbers say.

### 6.1 Nothing beats a trailing average — and that includes every learner tried

CLEAN stage, Fast-moving, ordered by MASE (lower is better):

| # | method | MAE | RMSE | MASE | median MASE | WMAPE % | % SKUs beating naive |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | `tsb` | 42.37 | 63.60 | **3.450** | 1.54 | 83.1 | 63.8 |
| 2 | `rolling_mean_30` | **40.02** | 61.08 | 3.468 | 1.64 | 83.7 | 60.3 |
| 3 | `ewma_a0.1` | 43.13 | 66.69 | 3.483 | 1.61 | 87.7 | 60.3 |
| 4 | `rolling_mean_14` | 45.58 | 71.52 | 3.494 | **1.54** | 91.8 | 60.3 |
| 5 | `rolling_mean_60` | 42.87 | 60.39 | 3.575 | 1.82 | 90.4 | 58.6 |
| 6 | `weekly_hurdle_12w` | 42.96 | **59.78** | 3.587 | 1.85 | 93.2 | 55.2 |
| 9 | `xgboost_pooled` | 44.48 | 62.63 | 3.912 | — | 96.4 | 56.9 |
| 10 | `lightgbm_pooled` | 44.27 | 63.10 | 3.982 | — | 97.8 | 55.2 |
| 14 | `random_forest` | 49.56 | 69.55 | 4.128 | — | 110.1 | 51.7 |
| 18 | `xgboost` | 52.23 | 75.90 | 4.265 | — | 116.5 | 50.0 |
| 20 | `lightgbm` | 51.64 | 74.00 | 4.297 | — | 110.4 | 51.7 |
| 23 | `prophet_cal` | 53.38 | 76.79 | 5.423 | — | 112.0 | 53.4 |
| 25 | `prophet_plain` | 52.16 | 75.71 | 5.662 | — | 109.5 | 53.4 |
| 26 | `ridge` | 84.40 | 179.92 | 6.985 | — | 196.2 | 48.3 |
| 27 | `naive` | 104.19 | 199.29 | 8.520 | — | 186.1 | — |

The top of the table is the same four methods this repo already knew about. **No gradient-boosted
model, forest, or Prophet variant reaches the top eight.** The best learner
(`xgboost_pooled`, MASE 3.912) is 13% worse than `tsb` and 12.8% worse than the
`rolling_mean_30` that `step4_forecast_model.py` already ships.

Ranking on **median** MASE instead of the mean does not rescue them: the top five are
`rolling_mean_14` and `tsb` (1.54), `ewma_a0.1` (1.61), `seasonal_naive_7` (1.62),
`rolling_mean_30` (1.64). The ranking is stable under both averaging schemes, which is worth
stating because the mean MASE is visibly inflated by a handful of small-denominator SKUs —
`croston` and `sba` post mean MASEs of 32.6 and 31.2 for exactly that reason.

**Pooling helps, per-SKU fitting hurts.** Every learner improves when fitted once across all Fast
SKUs rather than once per SKU: XGBoost 4.265 → 3.912, LightGBM 4.297 → 3.982, random forest
4.128 → 4.115. That is the expected direction — a per-SKU learner sees ~400 rows of a series that
is mostly zeros — but it is worth recording that pooling closes roughly a third of the gap to the
baselines and still does not close it.

### 6.2 Prophet, measured at last, loses to a 30-day average by 56%

`prophet_cal` (MASE 5.423) and `prophet_plain` (5.662) rank 23rd and 25th of 29. Against
`rolling_mean_30` at 3.468 that is **56–63% worse on MASE** and 33% worse on MAE.

The academic-calendar regressors do help — `prophet_cal` beats `prophet_plain` by 4.2% on MASE —
so section 3.3.2's regressor design is directionally right. It is the model that does not fit this
data: Prophet's additive trend-plus-seasonality decomposition assumes a continuous signal to
decompose, and on a series where the Fast segment's median item sells on 7% of days, there is
mostly no signal between the spikes.

Caveat, stated so this is not over-read: this is MAP estimation, not section 3.3.2's
`mcmc_samples=1000`. MCMC changes the uncertainty interval, not the point forecast being scored,
so it would not reorder this table — but the section 3.3.2 interval widths are not reproduced here.

### 6.3 The MAPE ≤ 20% gate is not close to survivable

Best MAPE of any of the 29 methods: **201.5%** (`rolling_median_30`). Best pooled WMAPE: **74.8%**
(`rolling_mean_30`). Section 3.3.4's acceptance criterion is ≤ 20%.

This is not a tuning gap; it is an order of magnitude, and it reproduces from a different direction
what `docs/DEGENERATE_FORECAST.md` (#21) and `docs/SPARSE_DEMAND_EXPERIMENTS.md` already found.
Evidence for deferred decision **B2**, on the Fast segment specifically — the segment where the
gate has the best possible chance, since these are the items with the most data behind them.

### 6.4 Cleaning improves forecastability — but not the way the headline suggests

At the population level, every stage-to-stage comparison favours CLEAN. `rolling_mean_30` goes from
MASE 6.507 (raw) to 5.17 (clean, rule-matched), **−20.6%**; 20 of 29 methods improve, median
improvement **−12.7%**. The best MASE available anywhere goes from 4.418 (raw) to 3.450 (clean),
**−21.9%**.

But the mechanism is not "cleaning smooths the series." Of 238 exact-name pairs, **220 are
byte-identical after cleaning** — the ETL provably did not touch them. Only 18 series changed, 8 of
them Fast. So most of the population-level gap is the **roster**, not the series: cleaning changes
*which items you forecast*, and that is what section 3.1.3 is for.

On the raw side, three physical products appear **twice** in the Fast roster under different
spellings — `Lanyard @180` / `B. Lanyard @180`, `Tote Bag Canvas White` / `… @160`,
`Umb (One For UST)` / `Umb ONE FOR UST`. 54 raw Fast names collapse to 51 canonical items. A manager
working from raw sheets would forecast and reorder the same item twice. Consolidation also
concentrates demand into the segment: the Fast segment carries 56.9% of all units before cleaning
and **61.2%** after, on the same ~19% of SKUs, and the median Fast item's history goes from 46.5
active sale days to **57.5** (+24%).

### 6.5 On the items cleaning *did* change, MAE and WMAPE disagree — and only one is right

The 18 changed items carry 16,069 units before cleaning and 18,471 after (**+14.9%**): proportional
allocation moves units *into* them from the price-grouped rows they were bundled in.

| method | WMAPE raw | WMAPE clean | Δ WMAPE | MAE raw | MAE clean | Δ MAE |
|---|---:|---:|---:|---:|---:|---:|
| `xgboost` | 353.5 | 139.0 | **−60.7%** | 42.99 | 48.08 | +11.9% |
| `lightgbm` | 341.2 | 141.4 | **−58.5%** | 42.56 | 48.11 | +13.1% |
| `RM6_6month_180d` | 216.6 | 116.1 | **−46.4%** | 37.14 | 40.97 | +10.3% |
| `weekly_hurdle_12w` | 147.7 | 96.7 | **−34.5%** | 32.18 | 40.01 | +24.3% |
| `rolling_mean_30` | 118.2 | 95.5 | **−19.2%** | 29.44 | 39.20 | +33.1% |

**MAE rises on every method and WMAPE falls on 12 of 13.** MAE has to rise: the quantity being
forecast got 15% bigger. WMAPE divides that out, and says the same item became substantially easier
to forecast. Reading this table on MAE would produce the exact opposite — and wrong — conclusion,
which is why `Paired_Cleaning_Effect` is sorted on WMAPE and carries the explanation inline.

### 6.6 RM3 and RM6 both lose to the 30-day window, under either reading

The window sweep on the CLEAN Fast segment:

| window | 3d | 6d | 14d | **30d** | 60d | 90d (RM3-months) | 180d (RM6-months) |
|---|---:|---:|---:|---:|---:|---:|---:|
| MASE | 5.206 | 4.093 | 3.494 | **3.468** | 3.575 | 3.603 | 3.812 |
| MAE | 62.84 | 51.04 | 45.58 | **40.02** | 42.87 | 43.06 | 45.32 |
| WMAPE % | 125.8 | 103.7 | 91.8 | **83.7** | 90.4 | 94.1 | 105.8 |

The curve is U-shaped with its minimum at 30 days, and `rolling_mean_30` wins on all three metrics.
Under the **days** reading, RM3 and RM6 are decisively worse (MASE +50% and +18%). Under the
**months** reading they are close but still behind (+3.9% and +9.9%). The curve is shallow between
14 and 90 days, so the exact window is not a sensitive choice in that band — but there is no reading
of RM3 or RM6 under which it beats what the pipeline already uses.

### 6.7 Category is where the learners earn their place

Best method per category, CLEAN stage:

| category | n | best by MASE | MASE | best by MAE | MAE |
|---|---:|---|---:|---|---:|
| Stationery | 12 | `rolling_mean_30` | **1.349** | `rolling_mean_30` | 37.90 |
| Lanyards & IDs | 8 | **`xgboost_pooled`** | **1.825** | `RM6_6month_180d` | 62.85 |
| Drinkware | 4 | `tsb` | 2.217 | `tsb` | 17.26 |
| Bags | 6 | **`random_forest`** | 2.558 | **`lightgbm_pooled`** | 41.41 |
| Plush & Souvenirs | 7 | `ewma_a0.1` | 3.371 | `RM6_6day` | 10.43 |
| Umbrellas & Gear | 2 | `RM6_6month_180d` | 3.773 | `rolling_mean_14` | 29.42 |
| Outerwear | 1 | `rolling_mean_30` | 4.097 | `rolling_mean_30` | 24.33 |
| Keychains & Charms | 6 | `rolling_mean_14` | 4.745 | **`lightgbm_pooled`** | 40.13 |
| Shirts & Tops | 11 | `rolling_mean_30` | 5.087 | `rolling_mean_30` | 44.96 |

(`Other` is omitted — a single SKU, won by `gradient_boosting`.)

A learner takes the top spot on at least one metric in **3 of these 9** categories — Bags (both
metrics), Lanyards & IDs (MASE), Keychains & Charms (MAE) — in a catalogue-wide table that said
they all lose. Forecastability also varies almost fourfold across categories, from Stationery at
MASE 1.349 to Shirts & Tops at 5.087, which is a stronger argument for segment-specific handling
than anything in the aggregate table. The category counts are small (1–12 SKUs), so these are leads
to check in Capstone 2, not conclusions.

### 6.8 `n_skus_priced` — and a calendar artefact that must not be read as a result

`n_skus_priced` counts the SKUs a method gives a **positive** 30-day forecast for. It is the column
that decides whether the prescriptive layer can act at all: a zero forecast yields no annual demand,
no EOQ, and nothing to order, whatever the method's MAE says.

Two facts fall out of it, and they are different in kind.

**The real one.** `rolling_mean_30` — the production model — prices **26 of 58** Fast SKUs. Thirty-two
Fast items get a zero forecast and cannot be ordered from it. That reproduces
`docs/ROLLING_MEAN_FORECAST.md` §4's open decision on the Fast segment exactly. Meanwhile `tsb`,
`ewma_a0.1`/`a0.3`, `croston`, `sba`, `ets`, `lightgbm_pooled` and `random_forest_pooled` all price
**58 of 58**, and the per-SKU learners price 53–54. That is a genuine trade-off: `rolling_mean_30`
has the better MAE and can act on 45% of the segment; `tsb` is a whisker behind on MASE and can act
on all of it.

**The artefact.** The daily index runs to 2026-07-31, but the last recorded sale anywhere in the
catalogue is **2026-07-08** — 23 trailing all-zero calendar days. Any method whose window is ≤ 23
days therefore sees nothing but zeros when forecasting from the end of the calendar, and prices
0 SKUs for that reason alone:

| method | priced from end of calendar | priced from last sale day |
|---|---:|---:|
| `naive` | 0 | 18 |
| `RM3_3day` | 0 | 22 |
| `rolling_q75_30` | 0 | 25 |
| `seasonal_naive_7` | 0 | 26 |
| `RM6_6day` | 0 | 26 |
| `rolling_mean_14` | 0 | 26 |
| `rolling_median_30` | 0 | 20 |
| `rolling_mean_30` | 26 | 27 |
| `tsb` | 58 | 58 |

(RAW stage shows the same pattern: `naive` 0 → 12, `RM3_3day` 0 → 14, `rolling_mean_14` 0 → 19.)

Those zeros are a property of when the tally sheets stop, not of the methods. The summary CSV
therefore carries **both** columns — `n_skus_priced` (same anchor as `model_benchmark.py`, so the two
files mean the same thing) and `n_skus_priced_at_last_sale`. A zero that recovers at the second
anchor is an artefact; one that does not is structural, which is the distinction
`docs/DEGENERATE_FORECAST.md` (#21) is actually about.

Note that `rolling_mean_30` moves only 26 → 27, so **its** zeros are not the artefact — they are
per-SKU empty windows, the real thing.

### 6.9 The catalogue is not 80/20, and the forecasts reproduce the split it does have

`python scripts/pareto_concentration_check.py`

Section 2.1.3 invokes the Pareto Principle as the basis for FSN classification and section 3.3.1
sets the Fast cutoff at the 80th ADUS percentile on that reasoning. Measured, the catalogue is
**73/20, not 80/20**: the top 20% of sellers (53 of 266 SKUs) carry 72.6% of units, and reaching 80%
of units takes the top **26%**. The FSN Fast segment — 58 SKUs, 21.8% of sellers — carries 67.0%.
Close enough that the Pareto framing is sound; far enough that "80/20" should not be quoted as a
finding about this data.

Whether the *forecasts* reproduce that concentration is a different question from forecast error,
and the answer is much more positive. Across the scored windows the top 20% of Fast SKUs carry 60.8%
of demand; several methods land within a percentage point:

| method | top-20% share of forecast | error vs actual | Spearman ρ | total forecast vs actual | SKUs forecast zero |
|---|---:|---:|---:|---:|---:|
| actual | 60.8% | — | — | 100% | — |
| `rolling_mean_14` | 60.2% | −0.5 pp | 0.972 | 103.6% | 14 |
| `tsb` | 61.3% | +0.5 pp | 0.979 | **101.8%** | **0** |
| `rolling_mean_30` | 61.5% | +0.7 pp | **0.996** | 103.1% | 13 |
| `ewma_a0.1` | 61.6% | +0.8 pp | 0.974 | 108.4% | 0 |
| `weekly_hurdle_12w` | 63.0% | +2.2 pp | 0.996 | 100.5% | 10 |
| `croston` / `sba` | 59.4% | −1.4 pp | **0.394** | 156–165% | 0 |
| `naive` | 71.4% | +10.6 pp | 0.925 | 191.9% | 15 |
| `rolling_median_30` | 78.9% | +18.2 pp | 0.815 | **30.8%** | 22 |

`tsb` is the standout: right concentration, near-perfect ordering, aggregate volume within 2%, and
it forecasts a non-zero quantity for every SKU. `croston`/`sba` land the *share* by accident — their
ρ of 0.39 means they get which SKUs are big substantially wrong, and they over-forecast total volume
by 56–65%. `rolling_median_30` looks concentrated only because it zeroes out 22 of 58 SKUs.

**This is a weaker claim than forecasting well, and the two must not be conflated.** A method can put
the right share of demand in the right SKUs across a year while still being wrong about which month
the units land in — which is what MASE measures, and where every method scores above 3. The
practical reading: these forecasts are usable for **annual sizing, EOQ and supplier allocation**, and
not yet for **monthly reorder timing**.

### 6.10 The 80/20 train-test split ranks the methods differently — and it is one draw

`python scripts/holdout_8020_benchmark.py`

Section 3.3.4 says both "walk-forward validation" and "an 80/20 train-test split", which are two
protocols. Everything above is walk-forward: 12 rolling origins, refit at each. The holdout fits
**once** on the first 656 days and forecasts the remaining 150 as five consecutive 30-day blocks
with no refitting — strictly harder, and the usual meaning of the phrase.

It reorders the table. Under walk-forward the ranking is `tsb` → `rolling_mean_30` → `ewma_a0.1` →
`rolling_mean_14`. Under the 80/20 holdout it is `rolling_mean_14` (MASE 1.544) → `ewma_a0.3` →
`seasonal_naive_7` → `RM6_6day`, with `rolling_mean_30` falling to **13th** and `naive` beating it
on MAE (53.4 vs 66.6).

That is not evidence that short windows are better. It is an artefact of where the single cut lands:

| trailing window at the 2026-02-16 cut | implied 30-day forecast |
|---|---:|
| last 14 days | 2,089 units |
| **last 30 days** | **4,842 units** |
| last 60 days | 3,145 units |
| *actual mean over the 150 held-out days* | *2,394 units* |

The cut falls immediately after a demand spike, so the 30-day window carries it forward for all five
blocks and over-forecasts by ~2×, while the 14-day window happens to sit almost exactly on the
outcome. Walk-forward averages over twelve such origins; the 80/20 split takes one and inherits its
luck. **A single chronological holdout is one sample, and on 821 days of intermittent demand that is
not enough to rank models.** The walk-forward numbers are the ones to quote.

### 6.11 Calendar-lag and top-down models: both tried, both lost

Two families the 29-method benchmark left out were added afterwards, on identical folds, because
each had a specific reason to be expected to work. Neither did.

**Calendar-lag** (`forecasting/calendar_models.py`). The benchmark's only seasonal method was
`seasonal_naive_7`, whose season is a *week* — so nothing in it could know that August is enrollment
month and July is dead, even though that is the manuscript's entire thesis and the data agrees
(August 2025: 5,210 units on the Fast segment, against a 12-window median of 2,976).

| method | MAE | MASE | median MASE |
|---|---:|---:|---:|
| `tsb` (incumbent) | 42.37 | **3.450** | 1.541 |
| `rolling_mean_30` | **40.02** | 3.468 | 1.637 |
| `blend_tsb+seasonal_naive_365` | 43.61 | 3.496 | **1.521** |
| `seasonal_index_365` | 48.32 | 3.633 | 2.093 |
| `seasonal_naive_365` | 58.20 | 4.714 | 2.164 |
| `semester_week_mean` | 52.68 | 5.258 | 2.026 |

A raw annual lag is 37% worse than the incumbent. Rescaling it by the current level
(`seasonal_index_365`) recovers most of that but still loses. Matching on `semester_week` — the
academic calendar rather than the Gregorian one — is worst of all. The 50/50 blend of TSB with the
annual lag is the only one that is competitive, and it wins on *median* MASE (1.521) while losing on
the mean; that is a marginal result on 58 SKUs, not a finding.

**A live alternative explanation, which has to be stated:** the series starts 2024-05-02, so a
365-day lag has a full year behind it only from 2025-05-02, and the earliest folds reach into the
sparse, non-zero-filled Aug–Sep 2024 records. These models are structurally short of history here in
a way the trailing averages are not. This is a negative result *on two years of data*, not a general
one about seasonal models.

**Top-down hierarchical.** Forecast each category's total — much less intermittent than any single
SKU — then allocate back to SKUs by trailing demand share. The standard answer to intermittency, and
untested here.

| method | MAE | MASE |
|---|---:|---:|
| `topdown_tsb_share90` | 45.95 | 3.678 |
| `topdown_rm30_share90` | 42.43 | 3.821 |
| `topdown_rm60_share180` | 47.79 | 4.050 |

All lose. Aggregation does make the *category* series easier to forecast (as
`docs/SPARSE_DEMAND_EXPERIMENTS.md` #3 already found), but the allocation step hands the
intermittency straight back: the share weights are themselves estimated from the same sparse per-SKU
history.

**Running total: 37 methods, 10 families** — trailing averages, quantiles, exponential smoothing,
intermittent-demand (Croston/SBA/TSB), hurdle, Holt-Winters, six ML learners, pooled ML, Prophet,
calendar-lag, top-down. The top eight sit within **10%** of each other on MASE. Meanwhile a single
non-model decision — where the production fit is anchored — is worth **11×** (section 6.8).

**The ceiling is in the data, not the model class.** Further model search is the lowest-yield
remaining option and should stop.

### 6.12 Synthetic training data: it genuinely helps the learners, and still does not win

`python tools/synthetic_augment_ml_test.py --years 3`

`docs/SPARSE_DEMAND_EXPERIMENTS.md` #4 asked whether more history would help and found essentially
nothing — but it tested seven **window-based** methods, six of which cannot see past a fixed trailing
window by construction. That experiment could not have found an effect. The learners in
`forecasting/ml_models.py` are different: a per-SKU XGBoost fits tree structure from ~400 rows of a
series that is ~90% zeros, which is the one thing in this project that might be variance-limited
rather than signal-limited.

Three synthetic years were **prepended** (never appended, never substituted) to each of the 58 Fast
SKUs, under two generators: the weekday-stratified iid bootstrap from the original experiment, and a
moving-block bootstrap at block 14 that preserves short-range autocorrelation. Because
`make_folds` lays out test windows backward from the end of the array, every fold's `actual_30d` is
the same real data in all three arms — only the training slice grows. The scorer never sees a
synthetic value.

**MAE, all 58 SKUs, 3,480 predictions per arm, identical folds:**

| method | real only | +synthetic (iid) | +synthetic (block-14) | best change |
|---|---:|---:|---:|---:|
| `rolling_mean_30` **[control]** | 40.022 | 40.022 | 40.022 | **0.000** |
| `tsb` [reference] | 42.373 | 42.008 | 42.147 | −0.9% |
| `lightgbm` | 51.638 | 46.689 | **45.956** | **−11.0%** |
| `xgboost` | 52.234 | 47.424 | **46.408** | **−11.2%** |
| `ridge` | 84.400 | 46.071 | **42.644** | **−49.5%** |

**Augmentation works.** Every learner improves, the block bootstrap beats the iid one every time
(preserving autocorrelation matters — the features in `ml_models.py` are largely autocorrelation
features), and `ridge` improves by half, which says a linear fit on ~400 rows of this series was
wildly unstable rather than merely wrong.

**And it still loses.** The best augmented learner — `ridge` at 42.644 — remains **6.6% worse than
`rolling_mean_30` on unaugmented data**. Nothing crosses the baseline. A 6-SKU smoke run had
suggested `lightgbm` and `xgboost` would overtake it; that did not survive the full 58.

So the gap between the learners and the trailing averages was **partly** a sample-size artefact and
is now mostly closed — but it does not reverse. The conclusion in section 6.11 stands, with one
refinement: the ceiling is signal, not sample size, and synthetic resampling of a series' own
distribution cannot manufacture signal it does not contain.

**A methodological trap worth recording, because it nearly went in as a result.** MASE must NOT be
compared across augmentation arms. Its denominator is `naive_scale` over 30-day blocks of the
*training* slice, and three synthetic years of a stationary bootstrap have far smoother
block-to-block variation than the real series, inflating the denominator. Under MASE the control's
"improvement" reads as **−64.8%** while its MAE is bit-identical — the tell that the metric, not the
model, moved. `tools/synthetic_augment_ml_test.py` reports MASE but labels it not-comparable, gates
the run on the control's MAE being unchanged to 0.00e+00, and excludes MASE from the conclusion.

### 6.13 26% of every model's input is fabricated — and fixing it does not rescue MAPE

**The defect.** `Fact_Sales` carries rows only for days the store was tallied: 608 of the 821
calendar days in the model span. Every series builder in the repo — `step4_forecast_model.py::
build_series`, `model_benchmark.py::load_daily_series`, `step5_prescriptive.py::load_series` —
reindexes onto the full calendar with `fill_value=0`, converting all **213 un-tallied days into
"this SKU sold zero"**.

That includes a 66-day unbroken run (2024-06-01 → 2024-08-05) and a 25-day run
(2024-08-23 → 2024-09-16) where no tally sheet exists at all: step0's `FILES` list jumps straight
from the May 2024 workbook to the Aug 2024 one. Two months of "the store sold nothing" is asserted
by the pipeline and supported by no evidence.

| span | days | observed | fabricated |
|---|---:|---:|---:|
| 2024-05-02 → 2026-07-31 (current) | 821 | 74.1% | **213** |
| from 2024-10-01 | 669 | 86.2% | 92 |
| from 2025-01-07 | 571 | 91.4% | 49 |

Section 3.1.2 of the manuscript prescribes the opposite treatment — days with no entry flagged
"Unverified Zero" and "treated as missing data rather than as true observations, preventing encoding
lapses from suppressing the demand forecast." `Dim_Date.is_tally_date` already carries the flag, and
agrees with "has a Fact_Sales row" on **100%** of days. No series builder reads it.

**The correction** (`forecasting/observed_day.py`): forecast a rate per *trading* day, then scale by
the trading days the horizon is expected to contain. Genuine zero-sale days are preserved — the 23
days at the end of the span carry 176 rows each at quantity 0, so they are observed and stay in.

**Measured on identical folds against identical targets:**

| model | MAE | WMAPE % | MAPE % |
|---|---:|---:|---:|
| `tsb` | 42.37 | 79.2 | 365.9 |
| **`tsb` + observed-day fix** | **40.03 (−5.5%)** | **74.8** | **349.0** |
| `rolling_mean_30` | **40.02** | 74.8 | 386.8 |
| `rolling_mean_30` + observed-day fix | 40.74 (+1.8%) | 76.2 | 372.9 |

The fix helps models that read their **whole** history (TSB −5.5%) and slightly hurts a 30-day
trailing window, whose recent window rarely contains fabricated days anyway. It is a no-op on the
current production forecast, whose trailing window (2026-07-02 → 07-31) is 100% observed.

**It is still worth fixing** — a quarter of the input to every downstream calculation is invented,
and that is true whether or not this particular metric moves.

### 6.14 Why MAPE ≤ 30% is unreachable, and what number is

Three independent lines of evidence, none of which is about the model.

**1. The oracle floor.** Give a flat forecast *perfect knowledge of each SKU's own future mean* over
the scored folds — the best any constant forecast could possibly do, and every method in this
benchmark is constant:

| | |
|---|---:|
| Mean per-SKU MAPE of the oracle | **250.6%** |
| Median per-SKU MAPE of the oracle | 69.9% |
| SKUs whose oracle MAPE ≤ 30% | **0 / 58** |
| SKUs whose oracle MAPE ≤ 20% | **0 / 58** |
| Median coefficient of variation across folds | **1.15** |
| Folds where actual = 0 (MAPE undefined) | 361 / 696 (52%) |

At CV > 1 the standard deviation of 30-day demand exceeds its mean. No flat forecast can have low
MAPE against that; it is arithmetic, not a modelling failure. And MAPE is undefined on over half the
folds.

**2. The aggregation ladder.** Same model, same data, walk-forward, varying only granularity and
horizon:

| level | H=30 | H=90 | H=180 |
|---|---:|---:|---:|
| per SKU (58 series) | 74.8 | 79.7 | 105.7 |
| per category (10 series) | 57.7 | 63.0 | 90.3 |
| whole Fast segment (1 series) | **45.6** | 50.7 | 73.1 |

*(WMAPE %; MAPE is higher at every cell.)* Aggregation helps a lot — 74.8% → 45.6% — and still stops
short of 30%.

**3. Restricting to densely-observed test windows makes it worse, not better** (per SKU, H=30:
WMAPE 74.8% → 83.8%). The fabricated-zero windows were low-actual windows that the model was also
predicting low, so dropping them removes easy folds. This avenue is closed, and would have been
cherry-picking regardless.

**The best number available, and it is honest:**

> **WMAPE 44.4% / MAPE 50.9%** — whole Fast segment, 90-day horizon, `rolling_mean_30` with the
> observed-day fix.

That is the floor of this data with a flat model, and it is 1.5× the 30% ask and 2.5× the
manuscript's 20% gate. Reaching 20% would require either demand an order of magnitude less volatile
than USTore's, or a forecast unit far coarser than a monthly SKU-level reorder decision — which is
no longer the quantity the prescriptive layer consumes.

---

### 6.15 Alternative models, retested at category level — where ARIMA and Prophet finally become scorable

6.7 found that category is where aggregation helps, and section 7 closed by calling a per-category
model assignment "the natural follow-up". This is that follow-up, run properly:
`scripts/step1b_categorize_products.py` builds a real category grain (11 keyword rules on the item
name, 12 categories, 12.5% residue), and `scripts/benchmark_category_level.py` scores **27 methods**
on the 12 category series using the same harness as everything above — H=30, folds 3–12, min_train
60, identical-folds gate passed (12 series × 12 folds = 144 scored windows per method).

**Read WMAPE, not MAE.** A category sells far more units than a SKU, so its absolute error is larger
by construction; MAE across levels would rank the better forecast worse. WMAPE (Σ|error| / Σactual)
divides that out.

| # | method | MAE | RMSE | MAPE % | MASE | **WMAPE %** |
|---:|---|---:|---:|---:|---:|---:|
| 1 | **RM6_6month_180d** | 183.42 | 233.86 | 131.2 | 1.48 | **49.30** |
| 2 | sarima_weekly (1,1,1)(1,0,1,7) | 197.98 | 244.68 | 124.9 | 1.65 | 53.22 |
| 3 | rolling_mean_30 | 202.86 | 266.56 | 176.7 | 1.66 | 54.53 |
| 4 | arima_212 | 204.16 | 254.40 | 143.1 | 1.70 | 54.88 |
| 5 | weekly_hurdle_12w | 204.30 | 246.22 | 141.7 | 1.58 | 54.92 |
| 6 | arima_111 | 204.40 | 254.25 | 140.8 | 1.70 | 54.94 |
| 7 | RM3_3month_90d | 205.41 | 246.80 | 137.4 | 1.58 | 55.21 |
| 8 | sba | 206.81 | 264.54 | 142.5 | 1.62 | 55.59 |
| 9 | extra_trees | 209.69 | 259.89 | 124.5 | 1.62 | 56.36 |
| 10 | random_forest | 211.15 | 263.06 | 125.0 | 1.64 | 56.76 |
| 11 | prophet_plain | 211.29 | 274.38 | 142.1 | 1.73 | 56.79 |
| 12 | rolling_mean_60 | 213.96 | 259.58 | 143.4 | 1.63 | 57.51 |
| 13 | croston | 215.05 | 275.45 | 150.8 | 1.68 | 57.80 |
| 14 | gradient_boosting | 215.85 | 269.50 | 130.4 | 1.71 | 58.02 |
| 15 | ets | 217.11 | 275.10 | 144.9 | 1.89 | 58.36 |
| 16 | prophet_cal | 221.03 | 282.37 | 142.5 | 1.78 | 59.41 |
| 17 | xgboost | 222.98 | 275.67 | 132.0 | 1.76 | 59.93 |
| 18 | lightgbm | 226.07 | 276.71 | 130.4 | 1.74 | 60.77 |
| 19 | ridge | 229.27 | 298.68 | 127.3 | 1.83 | 61.63 |
| 20 | ewma_a0.1 | 235.18 | 329.43 | 153.6 | 1.95 | 63.21 |
| 21 | tsb | 238.99 | 322.18 | 158.2 | 1.92 | 64.24 |
| 22 | rolling_q75_30 | 240.69 | 328.14 | 190.4 | 1.92 | 64.70 |
| 23 | rolling_mean_14 | 250.44 | 355.37 | 155.8 | 2.11 | 67.32 |
| 24 | rolling_median_30 | 253.38 | 336.63 | 111.2 | 2.14 | 68.11 |
| 25 | seasonal_naive_7 | 271.04 | 359.24 | 147.2 | 2.25 | 72.85 |
| 26 | ewma_a0.3 | 341.53 | 525.84 | 176.4 | 2.80 | 91.80 |
| 27 | naive | 682.73 | 1162.22 | 337.2 | 5.42 | 183.51 |

**Five findings.**

**1. ARIMA and Prophet were never beaten per SKU — they were unscorable.** Section 2.1.4 names ARIMA
as a principal comparator and states its own precondition: a near-continuous series with 50–100 clean
observations. A ~90% zero per-SKU series does not supply that, which is why ARIMA appears nowhere in
the 37-method SKU table. Aggregation supplies it. Scored at last, SARIMA takes **2nd of 27** and both
ARIMA orders land in the top 6. Prophet, which lost to a 30-day average by 56% per SKU (6.2), closes
to **15%** here. The manuscript's preferred model classes were not wrong so much as untested at the
grain the manuscript chose.

**2. A trailing average still wins.** RM6 (180-day) beats SARIMA by 7.4% and every learner by 12–19%.
This is the same verdict as 6.1 with none of the same inputs — different grain, different series
length, different sparsity — which is what makes it worth trusting.

**3. The best *window* inverts with the grain.** Per SKU, 30 days won and 180 was 4th-worst. At
category level 180 wins and 14 is 23rd. Same callable, opposite conclusion: an aggregated series is
smooth enough that more averaging removes noise instead of signal. Any "best window" quoted without
its grain is meaningless.

**4. MAPE is finally *defined* — and still fails.** `zero_targets` drops 361 → **0**, so MAPE exists
on every fold for the first time in this project. The best of 27 is 111.2% (rolling_median_30, which
is 24th on WMAPE). The ≤20% gate survives neither the change of grain nor the change of metric.

**5. Intermittent-demand methods lose their reason to exist.** TSB falls to 21st and Croston to 13th.
Both are corrections for intermittency; aggregation already removed it, so what is left is their cost
— a smoothed rate that lags a real level shift.

**6. The winning category forecast is arithmetically identical to summing the per-SKU forecasts.**
This is the finding that reframes all five above. A rolling mean is a *linear* operator, and
`mean(Σ series) = Σ mean(series)` — so forecasting the category series with RM6 returns exactly the
number you get by forecasting each Fast SKU with RM6 and adding the results. Measured on identical
folds and identical actuals, the two arms are bit-identical:

| method | direct category WMAPE % | bottom-up (Σ per-SKU) WMAPE % | gap |
|---|---:|---:|---:|
| RM6_180 | 53.33 | 53.33 | **0.00 pp** |
| rolling_mean_30 | 57.68 | 57.68 | **0.00 pp** |
| ewma_a0.1 | 64.13 | 64.13 | **0.00 pp** |
| tsb | 66.40 | 64.42 | +1.97 pp |
| croston | 59.88 | 89.11 | **−29.23 pp** |

(Fast-only series, 10 categories × 12 folds — hence 53.33% rather than the 49.30% in the table
above, which is the all-SKU 12-category series.)

So the category *model* contributes nothing over the pipeline that already exists. The 74.8% → 49.3%
improvement is **not** a better model beating a worse one; it is the same model scored on a sum
instead of on its parts. Variance does not add linearly, so any aggregate is easier to hit than its
components — that is arithmetic, available for free, and `step4_forecast_model.py`'s existing output
already contains it. Anyone quoting the category WMAPE as evidence of a modelling gain is quoting a
change of denominator.

The exception is instructive: the identity holds only for linear forecasters. `croston` is 29pp
better fitted directly to the category series than summed from SKUs — its non-linear
inter-arrival logic genuinely benefits from a dense series. It still loses to RM6 by 6.5pp, so it
does not change the selection, but it is the one case where "fit the category directly" is a real
operation rather than a restatement.

**The result does not transfer to SKU level.** `scripts/compare_topdown_vs_persku.py` allocates the
category forecast back by trailing-90d share and scores it against the per-SKU arm on identical folds:
77.6% WMAPE against 74.8%. Top-down loses by 3.7%, though the paired test is inconclusive (Wilcoxon
p = 0.054) and top-down is 23% better on the worst decile of errors. Full treatment in
`docs/DIVERGENCE_REGISTER.md` #24. **Production stays per-SKU**; the category grain is reported as
evidence about the data, not shipped as a model.

## 7. What this does and does not settle

**Settles nothing about model selection.** That is still **B3**. What it removes is the assumption
that a more sophisticated model class was the missing piece: six learner families, two Prophet
configurations, and a pooled-fitting variant of each were tried on identical folds, and the
trailing average is still in front.

**Adds evidence to B2.** The MAPE ≤ 20% gate in section 3.3.4 is off by an order of magnitude on
the Fast segment, which is the friendliest possible test for it.

**Adds evidence for the ETL.** Section 3.1.3's cleaning is worth its cost, but the reason to keep it
is roster integrity — not forecasting a duplicate item twice — rather than smoother series.

**~~Open, and worth doing next:~~ Done — see 6.15.** The category result (6.7) was the most actionable
finding here and the least supported by sample size. It has since been built out properly: a real
category grain (`step1b_categorize_products.py`), 27 methods scored on it
(`benchmark_category_level.py`), and a like-for-like SKU-level test of whether it transfers
(`compare_topdown_vs_persku.py`). Summary: category-level WMAPE is 49.3% against 74.8% per SKU — the
largest accuracy gain in this project — and it **does not survive disaggregation**, so production
stays per-SKU. The finding is about the data, not about a model.

**Open, and worth doing next:** the top-down arm is better on the worst decile of errors (178 units
against 231) and carries near-zero bias, which is the behaviour that matters for stockouts even though
it loses on mean error. Selecting the arm per category — top-down for large multi-SKU categories with
concentrated share (`Bags`, `Lanyards & IDs`, both +5–6pp), per-SKU elsewhere — is the natural next
test, and needs more Digital Tallying Interface data before it can be validated rather than fitted.

