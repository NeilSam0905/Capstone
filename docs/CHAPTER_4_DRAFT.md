# Chapter 4: Results and Discussion

> **Working draft.** Every figure in this chapter is measured from the committed artifacts named
> beside it — `ustore.db`, the CSVs under `data/`, or the experiment logs under `docs/` — and not
> transcribed from an earlier version of this document. Where a number here differs from one
> circulated previously, the difference is flagged rather than silently reconciled. Provisional
> figures are labelled provisional in place.

> **Amended 30 September – 1 October 2026, after the working branches were merged and the July
> tallies arrived.** The forecasting line, the policy line and the frontend were developed on
> separate branches and brought into one tree. Running every gate against the merged result found
> five defects that were invisible while the halves were apart, one of which — a 23-day run of
> zero-fill sitting in the denominator of every demand rate — had been diagnosed in
> `docs/FORECASTING_EXPLORATION_NOTES.md` §2.5 and fixed only in the two forecast steps, because
> the prescriptive stage that also needed it was on another branch. The July 2026 tally sheets were
> then completed, which added 5,950 units and turned those 23 days into real trading days.
> **Sections 4.1 to 4.3 are as transcribed before both changes**; §4.4's *A correction, and the
> data that superseded it* carries the current figures and is authoritative where they disagree.

> **Reconciled 23 September 2026 against the policy layer.** This chapter was first consolidated on
> 16 September 2026, before `docs/PRESCRIPTIVE_CONTRACT.md`, `docs/ACCEPTANCE_STANDARD.md`,
> `docs/POLICY_HOLDOUT.md` and `docs/INVENTORY_SIMULATION.md` existed. Three of its principal
> findings described a design those documents replaced and have been rewritten; the measurements
> behind them are kept and re-scoped rather than deleted, because a benchmark result that no longer
> decides anything is still a correct measurement of what it measured. The line-by-line audit the
> edit was made from — every finding, table and source row marked intact, narrowed, superseded or
> missing — is `docs/CHAPTER_4_RECONCILIATION.md`, which also records the places where two committed
> documents report a quantity differently and which reading this chapter uses.

## 4.1  Presentation of Results

### Integrated dataset

The extraction, transformation, and loading process produced a single integrated dataset from the
USTore's historical tally workbooks and monthly inventory sheets. Sales records span 2 May 2024 to
31 July 2026 and inventory counts span 1 November 2024 to 1 April 2026.

***Table 5. Loaded dataset volumes.***

| Loaded object | Count |
| --- | --- |
| Sales records | 84,399 |
| &nbsp;&nbsp;&nbsp;&nbsp;with a positive quantity | 15,858 |
| &nbsp;&nbsp;&nbsp;&nbsp;with a zero quantity | 68,541 |
| Total units sold | 89,232 |
| Catalog items | 519 |
| &nbsp;&nbsp;&nbsp;&nbsp;with at least one sales record | 286 |
| &nbsp;&nbsp;&nbsp;&nbsp;with more than zero total units | 266 |
| Calendar dates in scope | 1,461 |
| &nbsp;&nbsp;&nbsp;&nbsp;on which a tally was recorded | 608 |
| &nbsp;&nbsp;&nbsp;&nbsp;on which a sale was recorded | 416 |
| Consignment suppliers after normalisation | 19 |
| Sales coverage | 2 May 2024 to 31 July 2026 |
| Inventory coverage | 1 November 2024 to 1 April 2026 |

Item names were consolidated into 519 canonical items through a controlled vocabulary mapping, and
supplier names were normalised from 42 raw strings in the source workbooks to 19 suppliers. Of the
519 catalog items, 286 carry at least one sales record and 266 recorded more than zero units within
the observation window. The remaining items appear only in inventory sheets.

> **Correction against an earlier draft.** The count of dates on which a sale was recorded is
> **416**, not the 411 previously circulated. Both flags are now derived in
> `scripts/populate_dim_date.py` from the same zero-inclusive source file, filtered differently:
> `is_tally_date` = 608 (any tally row on that date) and `is_tally_date_positive` = 416 (any row
> with a positive quantity). The earlier 411 was computed on a source file that predated the July
> 2026 month. The discrepancy is recorded in `docs/REMEDIATION_WAVE1_STATUS.md` §3 and the higher,
> single-provenance figure is kept.

### Data quality

No source record failed transformation. The exception table is empty: every record in the source
workbooks resolved to a canonical item and a valid date. Section 3.1.3 committed to reporting the
proportion of flagged records relative to the total dataset, and that proportion is zero.

The historical tally sheets record a substantial share of sales as price-grouped entries, in which a
single quantity covers every item sold at a given price rather than naming the item. Proportional
allocation distributed 5,815 such entries across 15,117 item-level records, weighted by beginning
stock. The weighting basis available differed by record.

***Table 6. Proportional allocation by weighting basis.***

| Weighting basis | Records | Share |
| --- | --- | --- |
| Inventory count from the same month | 7,368 | 48.7% |
| Nearest available count from another month | 5,998 | 39.7% |
| Equal split, no count available | 1,751 | 11.6% |
| Total allocated records | 15,117 | 100% |

Allocated records are 17.9% of `Fact_Sales` and carry `imputation_flag = 1`, which is the column
every downstream calculation uses to down-weight them.

A second coverage limitation runs alongside the allocation one. A zero in `Fact_Sales` has three
possible meanings — the store had nothing to sell, stock was on hand and nobody bought, or there is
no inventory record and the two cannot be told apart — and the column that separates them is
`is_censored`. Only 14,160 of 84,399 rows (16.8%) carry any stock signal at all; 75 of the 286
selling products appear in the inventory workbook, and 62 products have a derivable
`days_of_supply`. The remaining 83.2% of rows are `NULL`: not a defect in the pipeline, but the
inventory workbook's own coverage.

### Product classification

Fast/Slow/Non-moving classification was computed from Average Daily Units Sold (ADUS), with
allocated rows weighted at 0.5, censored days excluded from the denominator, and each SKU's
observation window anchored at its own `entry_date`. The Fast cut is the 80th percentile of the
ADUS distribution over moving SKUs; Non-moving is defined structurally as a product with no sales
record at all.

***Table 7. FSN classification of the 519 catalog items.***

| Class | Definition | Items |
| --- | --- | --- |
| Fast (F) | ADUS at or above the 80th percentile of moving SKUs | 58 |
| &nbsp;&nbsp;&nbsp;&nbsp;of which High-Velocity-Limited (HVL) | Fast, but fewer than 30 active tally dates | 6 |
| Slow (S) | Has a sales record, ADUS below the cut | 228 |
| Non-moving (N) | No sales record in the observation window | 233 |
| **Total** | | **519** |

HVL is a reporting modifier on the Fast class, not a fourth class — `fsn_class` remains
CHECK-constrained to F/S/N, matching §3.2. The same classification is re-run at the 75th and 85th
percentile cutoffs as the sensitivity check §3.3.1 mandates; the sensitivity table is exposed live
through the application at `/api/fsn/sensitivity` and on the FSN Classification screen.

### Forecast method comparison

Fifteen forecasting methods were scored on identical walk-forward folds: 266 moving SKUs, horizon
30 days, minimum training window 60 days, maximum 12 folds per SKU — **3,192 (SKU, fold) pairs per
method**. Every series is reindexed onto one shared 821-day calendar, so fold origins are identical
across SKUs and across methods. The scoring unit is a **30-day aggregate**, which is the quantity the
prescriptive layer and the monthly billing cycle actually consume, not a daily point.

***Table 8. Fifteen forecasting methods on identical walk-forward folds (266 SKUs, 3,192 folds).***

| Method | MAE | RMSE | MASE (mean) | MASE (global) | % beating naive | SKUs priced |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `weekly_hurdle_12w` | 14.20 | **20.32** | **4.79** | 1.62 | 51.1 | 140 |
| `rolling_median_30` | 14.87 | 23.35 | 4.83 | 1.70 | 46.6 | 0 |
| `rolling_mean_30` *(production)* | **13.47** | 20.97 | 5.27 | **1.54** | 51.1 | 79 |
| `tsb` | 14.23 | 21.79 | 5.33 | 1.63 | **52.6** | **266** |
| `ewma_a0.1` | 14.68 | 23.43 | 5.45 | 1.68 | 51.1 | **266** |
| `xgboost` | 19.45 | 33.24 | 5.67 | 2.23 | 50.0 | 261 |
| `rolling_q75_30` | 15.16 | 23.87 | 5.69 | 1.73 | 45.5 | 0 |
| `seasonal_naive` | 16.59 | 27.31 | 6.30 | 1.90 | 47.4 | 0 |
| `random_forest` | 23.89 | 41.55 | 6.91 | 2.73 | 45.1 | 254 |
| `logistic_hurdle` | 15.01 | 22.08 | 7.33 | 1.72 | 47.7 | 141 |
| `lightgbm` | 30.99 | 40.86 | 8.34 | 3.55 | 38.7 | 260 |
| `naive` | 34.24 | 70.19 | 8.73 | 3.92 | — | 0 |
| `ets` | 18.17 | 32.65 | 9.28 | 2.08 | 48.9 | 255 |
| `sba` | 28.21 | 33.97 | 12.08 | 2.80 | 49.6 | **266** |
| `croston` | 29.28 | 35.08 | 12.50 | 2.90 | 49.2 | **266** |

*Sources: `data/model_benchmark_summary.csv` (ten statistical methods),
`data/model_benchmark_category_summary_category_speed.csv` (the two hurdle methods, per-SKU
variants, same folds), `data/model_benchmark_ml_summary.csv` (the three tree models),
`data/robust_metric_comparison.csv` (global MASE). Sorted by mean MASE.*

**"SKUs priced" is the column to read first.** It counts the SKUs for which a method produces a
positive 30-day forecast — the precondition for annualising a demand and therefore for computing a
reorder point or an EOQ at all. Four of the fifteen methods price nothing: `rolling_median_30`,
`rolling_q75_30`, `seasonal_naive` and `naive` all return zero on a series whose recent values are
mostly zero. **The best of the ten committed statistical baselines on mean MASE is among them** —
`rolling_median_30`, which also has the lowest median MASE of any method in the table and prices 0
of 266 SKUs.

### Service level and holding cost

`fill_rate_at_target` simulates the stocking decision each forecast implies: stock is set once per
fold at forecast plus safety stock, thirty days of demand arrive against it, and nothing replenishes
inside the window. Safety stock is `z·σ·√37` — review period 30 days plus lead time 7 — with σ taken
from the fold's training slice only.

***Table 9. Service outcome of the ten committed statistical methods (3,192 folds, 53,573 units of
scored demand).***

| Method | Fill rate | Units short | Units held | SKUs priced |
| --- | ---: | ---: | ---: | ---: |
| `ets` | **0.7768** | 11,959 | 84,728 | 255 |
| `ewma_a0.1` | 0.7755 | 12,026 | 71,097 | **266** |
| `rolling_mean_30` *(production)* | 0.7746 | 12,078 | 68,266 | 79 |
| `tsb` | 0.7538 | 13,192 | 69,437 | **266** |
| `seasonal_naive` | 0.7371 | 14,086 | 73,114 | 0 |
| `rolling_q75_30` | 0.7256 | 14,702 | 69,501 | 0 |
| `croston` | 0.7126 | 15,396 | 116,191 | **266** |
| `sba` | 0.7037 | 15,873 | 111,678 | **266** |
| `naive` | 0.6793 | 17,183 | 120,912 | 0 |
| `rolling_median_30` | 0.5062 | 26,455 | 40,274 | 0 |

*Source: `data/model_benchmark_summary.csv`. The two hurdle methods, scored on the same folds under
a fold-scoped service class, reach 0.7274 (`weekly_hurdle_12w`) and 0.7372 (`logistic_hurdle`) —
see the note on z-selection in §4.2.*

**The best method reaches 77.7% against the 95% target that was then in force.** That gap is
analysed in §4.2; it is not closed by any method in this study, and part of it is arithmetically
unreachable on this path. That target has since been retired along with the criterion it belonged
to — `service ≥ 95%` is no longer encoded anywhere as a pass/fail — so the figure below is read as a
measurement of the benchmark, not as a shortfall against a live bar.

The first replacement for a fixed target was a frontier. Re-scoring the same 3,192 stored
`(pred_30d, actual_30d)` pairs with an **empirical quantile of each SKU's own prior-fold forecast
errors** — expanding window, strictly pre-origin, never the fold being scored — sweeps service
against holding.

***Table 10. Service / holding frontier for `rolling_mean_30`.***

| Buffer quantile *q* | Fill rate | Units short | Units held | Units held per *extra* unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.645 | 19,018.5 | 30,115.0 | — |
| 0.60 | 0.664 | 17,984.0 | 34,031.2 | 3.8 |
| 0.70 | 0.699 | 16,151.4 | 40,511.2 | 3.5 |
| 0.75 | 0.719 | 15,065.5 | 44,773.8 | 3.9 |
| **0.80** | **0.742** | **13,844.4** | **49,852.6** | **4.2** |
| 0.85 | 0.758 | 12,955.3 | 56,972.6 | 8.0 |
| 0.90 | 0.779 | 11,856.5 | 66,360.2 | 8.5 |
| 0.95 | 0.794 | 11,040.2 | 79,714.6 | 16.4 |
| 0.98 | 0.800 | 10,707.4 | 87,887.6 | 24.6 |

*Source: `tools/service_frontier.py`, pinned by `tests/test_service_frontier.py`.*

The last column is the result. Below *q* ≈ 0.80 a unit of service costs about four units of
holding; above it the price roughly doubles, then doubles again. **On this curve the knee at
*q* ≈ 0.80 is measured, not chosen in advance** — and the emphasis on *this curve* is load-bearing.
The policy that was subsequently built does not score on these folds, and its own frontier has no
interior knee at all (§4.2). At that operating point the ranking of the three leading methods
changes character:

***Table 11. Method comparison at the knee (*q* = 0.80).***

| Method | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: |
| **`rolling_mean_30`** | **0.742** | **13,844.4** | **49,852.6** |
| `ets` | 0.738 | 14,032.1 | 64,336.4 |
| `tsb` | 0.722 | 14,913.9 | 52,202.2 |

`rolling_mean_30` **dominates** at the knee: higher service *and* less stock than both alternatives.
That was the result the model-selection decision was expected to turn on. It no longer is — the
prescriptive layer built since consumes a demand rate rather than a point forecast, so no ranking of
forecasting methods at any operating point selects anything downstream (§4.2). The measurement
stands; its consequence does not.

### The production forecast

`scripts/step4_forecast_model.py` forecasts every Fast SKU with `rolling_mean_30` — literally the
same callable the benchmark scores — so the benchmark and frontier findings above are findings about
the production model rather than about a near relative. `Result_Forecast` holds 1,740 rows (58 SKUs
× 30 days); `Result_Forecast_Metrics` holds 174 rows (58 SKUs × three period scopes). Validation
runs on the same harness at the same settings: horizon 30, 3–12 folds, minimum training 60 days.
**58 of 58 Fast SKUs scored, 12 folds each, none falling back to a heuristic.**

***Table 12. Validation of the production forecast against the acceptance criteria then in force (58
Fast SKUs).***

| Criterion or measure | Result | Status |
| --- | --- | --- |
| §3.3.4: MAPE ≤ 20% | 1 of 58 SKUs | **Not met** |
| &nbsp;&nbsp;&nbsp;&nbsp;MAPE computable for | 44 of 58 (undefined where the 30-day actual is 0) | |
| &nbsp;&nbsp;&nbsp;&nbsp;Best / median / worst MAPE | 16.9% / 120.6% / 3,254.1% | |
| Divergence #6's replacement: MASE < 1.0 | 23 of 58 nominally, **10 of 58 honestly** | **Not met** |
| &nbsp;&nbsp;&nbsp;&nbsp;Mean MASE (naive on the same folds) | 2.147 (5.294) | |
| &nbsp;&nbsp;&nbsp;&nbsp;Median MASE | 1.637 | |
| Divergence #6's replacement: service ≥ 95% | Ceiling of 0.9490 before any modelling choice | **Unreachable on this path** |
| Beats naive on MAE | **35 of 58 (60%)** | Yes |
| &nbsp;&nbsp;&nbsp;&nbsp;Mean MAE (naive on the same folds) | **40.02 (104.19)** | |
| SKUs with a positive 30-day forecast | 26 of 58 | **See §4.2** |

The nominal MASE count is reported alongside the honest one because thirteen of the twenty-three
SKUs with MASE < 1.0 have `MAE = 0` exactly: they sold nothing across the scored 360-day window, and
both the model and the naive baseline predicted that perfectly. Counting them as successes would be
scoring a forecast of nothing as a forecast. **The defensible count is 10 of 58.**

Against persistence the model is materially better once both are scored on the same folds and the
same horizon: mean MAE 40.02 against 104.19, mean MASE 2.147 against 5.294 — roughly 2.6×. Under the
earlier 80/20 daily holdout the naive baseline appeared to *beat* the model, because the model was
frozen at the split and predicted up to ~70 days ahead while the baseline re-read yesterday's actual
at every test point. That comparison was not a fair one and has been replaced.

### Prescriptive outputs

`scripts/step5_prescriptive.py` computes safety stock, reorder point and EOQ per SKU under real —
but still provisional — USTore inputs rather than an abstract sensitivity grid. All 17 assumption
rows are stored in `Dim_Parameters`, each suffixed `[PROVISIONAL — pending Block 5 (USTore site
visit)]`.

***Table 13. Prescriptive inputs (all provisional).***

| Input | Value | Basis |
| --- | --- | --- |
| Holding cost *H* | ₱1.4563 / unit / year | 0.25 × ₱210,000 inventory-value midpoint ÷ 36,051 units on hand |
| Ordering cost *S* — `low_admin_cost` | ₱1,250 / order | Midpoint of a plausible ₱500–2,000 administrative range |
| Ordering cost *S* — `high_goods_value` | ₱200,000 / order | USTore's own monthly figure taken literally |
| Lead time | 14 d (122 products), 18 d (371), 28 d (26) | Name-keyword garment classifier, `step5a_set_lead_times.py` |
| Service *z* — **retired as the buffer** | 1.65 (class F), 1.04 (class S) | §3.3.3; class N excluded from pricing entirely. Still written to `z_value`, but as a comparison rather than as the buffer — see *The policy layer* below |
| Review period | 30 days | The monthly billing cycle §3.3.2 names. This is the benchmark's periodic-review assumption; the deployed reorder point is continuous review at the SKU's own lead time |
| Demand basis | Trailing 365-day observed sum | Now the committed contract rather than a default awaiting a model choice (`docs/PRESCRIPTIVE_CONTRACT.md` §1) |

***Table 14. Prescriptive results.***

| Measure | Value |
| --- | --- |
| `Result_Prescriptive` rows | **474 = 208 priced SKUs × 2 ordering-cost scenarios + 58 flagged SKUs** |
| &nbsp;&nbsp;&nbsp;&nbsp;Fast SKUs priced | 44 |
| &nbsp;&nbsp;&nbsp;&nbsp;Slow SKUs priced | 164 |
| &nbsp;&nbsp;&nbsp;&nbsp;SKUs flagged `insufficient_data`, carrying a state and no number | **58** |
| σ from observed per-SKU history *(retired `z·σ` buffer)* | 334 of the 416 priced rows |
| σ from class-median CV fallback, thin history *(retired `z·σ` buffer)* | 82 of the 416 priced rows |
| EOQ exceeding a full year of demand — `low_admin_cost` | 204 of 208 SKUs, median EOQ ÷ annual demand **4.34×** |
| EOQ exceeding a full year of demand — `high_goods_value` | 208 of 208 SKUs, median EOQ ÷ annual demand **54.94×** |
| EOQ ratio between the two scenarios | Exactly 12.65× = √(200,000 ÷ 1,250), for every SKU |

The largest single SKU illustrates the scale. `Lanyard @180` has an annual demand of 5,094 units, a
reorder point of 425 and a safety stock of 174 — all of which are computed from measured inputs — and
an EOQ of 2,957 units under `low_admin_cost` or 37,406 under `high_goods_value`. `5M Rotating
Keychain` has an annual demand of 1,389 and an EOQ of 1,544 or 19,533. Presenting either EOQ figure
to staff as an order quantity would be recommending between four and fifty-five years of stock.

### Experiments on the sparse-demand problem

A further programme of experiments, logged across `docs/FORECAST_METHOD_COMPARISON.md`,
`docs/SPARSE_DEMAND_EXPERIMENTS.md` and `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md`, asked whether
anything — a different model shape, a different level of aggregation, more history, richer features,
or training SKUs together instead of separately — brings forecast error down to a level usable as a
pass/fail bar. Most did not. They are recorded because a negative result measured on real data is
evidence, and because several of them fail for reasons that identify the actual constraint.

***Table 15. Experiments that did not improve accuracy.***

| Experiment | Design | Result |
| --- | --- | --- |
| EWMA (α = 0.1) | Geometric decay in place of a boxcar window | Sits between the two methods already in use on every metric; displaces neither |
| Rolling 75th percentile | Bias the *point forecast* upward to cover rather than predict | Prices **0 of 266** SKUs; fill rate falls to 0.726 |
| Fitted per-SKU hurdle classifier | Logistic regression on weekday, recency, trailing rates | Worse than the single empirical rate on every error metric (MASE 7.33 vs 4.79) |
| Forecast at a coarser level | Same model, per-category and whole-store series | MAPE 203% → 86%, but **median MASE barely moves** (1.95 → 1.91) |
| Five extra years of history | Weekday-stratified bootstrap, prepended, never substituted | No change at all for window-based methods; ~1% for TSB |
| Academic-calendar features | Enrollment / exam / event / break flags added to the hurdle model | **Median change in per-SKU MAE was exactly 0.0**; only 114 of 266 SKUs improved |
| Pooling by product type | Eight keyword buckets (clothes, drinkware, bags, …) | Worse than category alone; drinkware — the most internally similar group — was the single worst performer |
| Pooling by category + speed | Apparel/non-apparel × fast/slow | After the leakage fix, the **worst** grouping tested (xgboost MAE 19.45 → 24.18) |

Three results went the other way: one new model shape, one pooling fix, and one grouping basis.

***Table 16. Grouping bases for pooled models, after the leakage fix.***

| Method | Grouping | MAE | RMSE | MASE (mean) | SKUs priced |
| --- | --- | ---: | ---: | ---: | ---: |
| `xgboost` | per-SKU (baseline) | 19.45 | 33.24 | 5.67 | 261 |
| `xgboost` | category | 15.42 | 23.02 | 12.71 | 266 |
| `xgboost` | category + speed | 24.18 | 32.32 | 13.08 | 266 |
| `xgboost` | product type | 16.43 | 24.84 | 13.80 | 266 |
| `xgboost` | **cluster (K = 4)** | **14.78** | **21.72** | **5.52** | 266 |
| `random_forest` | per-SKU (baseline) | 23.89 | 41.55 | 6.91 | 254 |
| `random_forest` | category + speed | 34.54 | 48.58 | 17.04 | 266 |
| `random_forest` | product type | 19.69 | 30.02 | 17.74 | 266 |
| `random_forest` | **cluster (K = 4)** | **17.36** | **26.36** | **6.52** | 266 |
| `logistic_hurdle` | per-SKU (baseline) | 15.01 | 22.08 | 7.33 | 141 |
| `logistic_hurdle` | category + speed | 14.74 | 22.00 | 6.23 | 141 |
| `logistic_hurdle` | **cluster (K = 4)** | **14.27** | **21.29** | **5.54** | 141 |

*Sources: `data/model_benchmark_category_comparison*.csv`. Every row is a post-correction run — see
§4.2 on the leakage audit.*

K-means on five behavioural features per SKU — demand density, mean non-zero sale size, coefficient
of variation of non-zero sales, log total demand, log unit price — with K = 4 chosen by elbow
(inertia 147.9 → 94.9 → 81.9) and checked against silhouette (0.327 at K = 4 against a peak of 0.333
at K = 3). No feature reads `category` or `fsn_class`, so the result is directly comparable against
every manual grouping. What the clustering found, unprompted, was a demand-shape partition that cuts
across the product taxonomy: clusters 0 and 3 both mix apparel and non-apparel freely.

***Table 17. Cluster composition and per-cluster accuracy (`xgboost`, pooled within cluster).***

| Cluster | Character | SKUs | MAE | MASE |
| --- | --- | ---: | ---: | ---: |
| cluster0 | High-density, high-value steady sellers | 31 | 51.65 | **1.82** |
| cluster1 | Very sparse — the hard tail | 117 | 1.89 | 6.80 |
| cluster2 | Extreme bulk-order outliers | 2 | 7.55 | 12.81 |
| cluster3 | Moderate mixed sellers | 116 | 18.06 | 5.13 |

MAE is not comparable across these rows — cluster1's 1.89 is low because those SKUs barely sell, not
because they are forecast well — which is why MASE is the column to read. Cluster0 at MASE 1.82 is
genuinely competitive with per-SKU modelling. The hard tail stays hard, as it does everywhere in this
study, but unlike a manual grouping it does not drag the dense cluster down with it.

The second positive result is that **pooling rescues the fitted hurdle classifier.** Per SKU it was
the weakest of the hurdle variants (MASE 7.33), because most SKUs have too few sale events for a
ten-parameter model to learn weekday and recency effects without fitting noise. Pooling the
sale-probability classifier across a group while keeping each SKU's own size estimate — probability
features are scale-free, size is pure scale — improves it on every metric, and improves it further
under clustering than under category+speed (7.33 → 6.23 → 5.54).

The third is the **weekly hurdle model** (`weekly_hurdle_12w`): the fraction of the trailing twelve
weeks with any sale, multiplied by the mean size of the non-zero weekly totals, spread evenly across
the horizon. Deliberately a plain empirical rate rather than a smoothing recursion. It is the lowest
mean MASE of the fifteen methods in Table 8 (4.79) and prices 140 SKUs against the production model's
79 — the first method in this study to combine the best error metric with a non-degenerate output.
It does not dominate: `rolling_mean_30` still wins MAE and fill rate on the SKUs it prices.

### The policy layer: what the prescriptive stage actually consumes

Everything above this point treats the forecast as the object to be got right. The layer that was
built after it does not read a forecast at all, and the reason is the degeneracy argument in §4.2
carried to its conclusion: if the method that wins the error metric is the method that prices
nothing, then a point forecast is the wrong thing for a stocking decision to depend on. The
prescriptive layer therefore consumes a **demand rate** and an **uncertainty**, and what has to be
reliable is the **policy** those two imply — stated as a contract in `docs/PRESCRIPTIVE_CONTRACT.md`,
implemented in `forecasting/policy.py`, pinned by `tests/test_policy.py`.

Three things are consumed, and each has a named source. The **rate** is units per day from a
trailing observed window, with a flag where there is none and a pooled fallback that is available and
off by default. The **uncertainty** is units over one lead time, taken as an empirical quantile of
the policy's own prior-fold errors. The **operating point** *q* is set per service tier from each
SKU's own curve, and the dial exposed to the store is a stock budget rather than a quantile.

**The demand rate has three states, and one of them is not a number.**

***Table 18. `Result_Prescriptive.rate_source`.***

| State | Rule | SKUs |
| --- | --- | ---: |
| `observed` | positive trailing-365-day window → the SKU's own rate | 208 |
| `cluster_pooled` | thin rate shrunk toward its behavioural cluster — **off by default** | 0 |
| `insufficient_data` | trailing window empty → **flagged, no rate, no reorder point** | 58 |
| **Total eligible (F + S)** | | **266** |

The third state is the substantive change, and it is a change in what the system is willing to say.
The previous code passed over any SKU whose trailing window summed to zero, so those 58 were not
reported as unknown — they were **absent from the output entirely**, and the screen showed nothing
where a recommendation belonged. They now carry a row stating the state, with `reorder_point` and
`eoq` NULL on purpose, because a zero there renders as *"you have enough stock"*, which is a
recommendation the pipeline has no evidence for. `backend/app.py` previously coerced that NULL to
`0.0` and rendered `needs_reorder = False`; it now returns the explicit state and a manual-review
note.

Three measurements bound what the flag costs and what it does not. Every one of the 58 flagged SKUs
**last sold between 7 May 2024 and 21 July 2025** — all silent for over a year, so borrowing a rate
for them would invent demand for items that have demonstrated a year of zero. **112 SKUs first sold
inside the trailing year, and all 112 already carry a positive rate**, so the "new SKU whose window
cannot be filled" case that motivated a pooled fallback is empty in this data. What is real is a
third group: **36 SKUs are priced off fewer than 10 units across the whole trailing year** — about
0.027 units/day, half a unit over an 18-day lead time — whose rate exists but carries almost no
evidence. Pooling serves *thin* rates, not missing ones.

The pooled fallback itself was measured and **defaulted off** as dominated: on a reserved window it
bought **+12.7 units served for +155.1 units held** — 12.2 held per extra unit served, against 5.0
from simply raising the buffer quantile — and it priced **no additional SKU**, so it was never a
coverage fix. The code and the flag are kept so the decision stays reversible, and the clusters it
draws on are the same K = 4 behavioural clusters Table 17 reports, from `forecasting/clustering.py`
unchanged.

**The operating point is per service tier, because a single one does two wrong things at once.**
Sorting the scored population into trailing-volume quintiles and sweeping the buffer quantile shows
why.

***Table 19. Fill by trailing-volume quintile at two buffer quantiles.***

| Quintile | Fill @ *q* = 0.80 | Fill @ *q* = 0.95 | Share of demand |
| --- | ---: | ---: | ---: |
| Q5 (highest) | 0.813 | **0.951** | 50% |
| Q4 | 0.706 | 0.827 | 14% |
| Q3 | 0.389 | 0.681 | 21% |
| Q2 | 0.280 | 0.525 | 10% |
| Q1 (lowest) | 0.117 | 0.288 | 6% |

Service converts into stock about **seven times** more efficiently at the top of that table than at
the bottom, and the bottom does not reach 0.38 at *any* quantile. A flat quantile therefore
under-serves the half of demand that could reach 0.95 while charging holding to chase SKUs no
reorder point can serve.

***Table 20. `Result_Prescriptive.service_tier`, assigned from each SKU's own pre-origin folds.***

| Tier | Rule | Operating point | SKUs |
| --- | --- | --- | ---: |
| `servable` | reaches the target fill at some *q* ≤ 0.95 | the **smallest** *q* that does | 24 |
| `partial` | cannot reach it | stops at its own marginal knee | 166 |
| `not_stockable` | no *q* returns the efficiency floor | **none** — covers expected demand, buys no buffer | 18 |

Those counts are the **priced catalogue**, 208 SKUs. `docs/POLICY_HOLDOUT.md` reports a different
split — 24 / 177 / 14 over 215 SKUs — because it tiers the population *scored at the development
origin*, which is a different set. Neither supersedes the other and the denominators are stated
wherever either appears.

**Eight distinct operating points are in use across the catalogue (*q* = 0.50 … 0.95) where there
was previously one.** The tiering is on **efficiency, not fill**, and the two disagree sharply:
writing off every SKU that cannot reach 50% fill discards 54 SKUs carrying **28.5%** of demand,
which is far too much to call made-to-order, while writing off every SKU that cannot return 0.10
units served per unit held discards a similar 52 SKUs carrying **1.8%**. The difference is the SKUs
that are hard to serve but *cheap* to serve — low fill on almost no stock — which a fill-based rule
cannot see.

**The buffer is empirical, and measured at the lead-time horizon.** The retired buffer was
`z·σ·√L` with z = 1.65 (F) / 1.04 (S), which assumes a symmetric normal around the mean; the demand
it buffers is right-skewed intermittent. The replacement is the *q*-quantile of the SKU's own
prior-fold policy errors, expanding window, strictly prior folds, floored at zero — taken at the
SKU's own lead time (14 / 18 / 28 days) rather than scaled down from a 30-day quantile by `√(L/30)`,
because that scaling step would reintroduce exactly the distributional assumption the empirical
quantile exists to remove. Deployed, it is **7.46 units mean against 13.84 for `z·σ`**: smaller, and
on the holdout below it serves more demand. Every row records `buffer_source` and carries the
retired figure in `safety_stock_normal_legacy`, so the swing between the two readings stays visible
— the same treatment this pipeline gives the two readings of the ordering cost.

`scripts/step5_prescriptive.py` fails the run if a flagged row carries a reorder point or an EOQ, if
a priced row is missing one, if a `not_stockable` row holds safety stock, if any row states an
unknown `rate_source`, `service_tier` or `buffer_source`, or if the row count does not equal
`priced × scenarios + flagged`. A non-degeneracy gate requires a priced share of at least 0.78
(today 208 / 266 = 0.7820) — it is the gate that catches `rolling_median_30`, best error metric, 0
SKUs priced. `tests/test_gates_can_fail.py` feeds each gate a fixture built to break it and requires
the break, because every legacy gate is an existence-negation that passes trivially against a table
in which nothing is priced.

### Validation of the policy on rolling origins

`scripts/validate_policy_holdout.py` scores the policy at four successive non-overlapping origins.
Every quantity the policy commits to — demand rate, behavioural clusters, service tier, buffer
quantile — is fitted strictly before the origin it is scored against, and a gate re-derives those
quantities with the scored window blanked to prove it, because a docstring promising a pre-origin
cut is not evidence.

***Table 21. The policy at four rolling origins.***

| Origin | Window end | SKUs | Demand | Fill rate | Units held | Served / held | Flat *q*=0.80 fill | Observed share |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-05-03 *(development set)* | 2026-07-31 | 215 | 11,538.0 | **0.6851** | 14,750.7 | 0.536 | 0.6188 | 0.9781 |
| 2026-02-02 | 2026-05-02 | 191 | 7,249.0 | **0.9149** | 28,925.9 | 0.229 | 0.8023 | 0.9726 |
| 2025-11-04 | 2026-02-01 | 164 | 12,821.0 | **0.7277** | 18,279.7 | 0.510 | 0.6485 | 0.9644 |
| 2025-08-06 | 2025-11-03 | 150 | 15,064.0 | **0.5987** | 6,795.7 | 1.327 | 0.5573 | 0.8055 |

*Sources: `docs/POLICY_HOLDOUT.md` (all columns but the last), `docs/ACCEPTANCE_STANDARD.md`
condition 4 (observed share).*

**Median fill 0.7064, range 0.5987–0.9149.** The spread is the finding. Achievable service varies
more with what demand does in a given quarter than with anything the policy chooses, so any
single-number claim about this system's service level is overstated by construction — including the
0.742 the benchmark frontier reports at its knee.

**The most recent window is a development set, not a clean holdout**, and is labelled as one in
every table that carries it. It was used to diagnose where the shortfall sat, to test rate-window
and lead-time sensitivity, and to choose efficiency over fill as the tiering variable. Every fitted
quantity is still selected strictly pre-origin, so the *procedure* is clean — but the design was
informed by looking, and that is researcher degrees of freedom a rigorous panel is right to
discount.

***Table 22. What the tiering buys, at the development origin.***

| Policy | Fill rate | Units short | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| **Tiered (per-tier *q*)** | **0.6851** | 3,633.2 | **14,750.7** | **0.536** |
| flat *q* = 0.80 (pre-tiering) | 0.6188 | 4,398.6 | 15,122.2 | 0.472 |
| flat *q* = 0.95 | 0.7980 | 2,330.7 | 32,056.9 | 0.287 |
| normal `z·σ` (retired) | 0.6022 | 4,589.9 | 21,029.2 | 0.330 |

Same SKUs, same blocks, same realised demand — only the stock differs. **Across the four origins the
tiering beat the flat quantile on fill at 4 of 4 and on efficiency at 2 of 4.** Those are different
claims and the weaker one is stated rather than dropped: the tiering reliably buys more service, but
at some origins it does so by spending more stock rather than less. The dominance on the development
set is real and is not universal — and §4.2 records that under simulation it does not reproduce, and
adjudicates what replaces it.

Sweeping a single population-wide quantile on the policy's own data gives the curve that is
directly comparable with Table 10 — and it is where the two paths diverge most sharply.

***Table 23. What a population-wide *q* would have bought, on the policy's own curve.***

| *q* | Fill rate | Units short | Units held | Held per extra unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.4741 | 6,067.6 | 9,113.2 | — |
| 0.60 | 0.5158 | 5,586.8 | 9,992.0 | 1.8 |
| 0.70 | 0.5629 | 5,042.7 | 12,102.4 | 3.9 |
| 0.75 | 0.5884 | 4,748.9 | 13,435.2 | 4.5 |
| 0.80 | 0.6188 | 4,398.6 | 15,122.2 | 4.8 |
| 0.85 | 0.6719 | 3,785.9 | 18,890.3 | 6.2 |
| 0.90 | 0.7257 | 3,164.5 | 24,255.5 | 8.6 |
| 0.95 | 0.7980 | 2,330.7 | 32,056.9 | 9.4 |
| 0.98 | 0.8264 | 2,003.0 | 37,407.1 | 16.3 |

**There is no interior knee here.** Marginal holding rises steadily rather than bending — 4.8 at
*q* = 0.80 to 9.4 at *q* = 0.95, a 1.96× rise where `tests/test_service_frontier.py`'s own knee test
requires more than 2×. `docs/PRESCRIPTIVE_CONTRACT.md` reports the same comparison from a separate
run at 5.0 → 9.4, a 1.88× rise; both readings are below the bar, which is the claim. The knee in
Table 10 is real on the benchmark's curve and **inherited rather than confirmed** on this one, which
is precisely why the operating point is now resolved per tier from each SKU's own curve instead of
being set population-wide.

**And the dial USTore actually turns is not a quantile.** Asking the store to pick a buffer quantile
asks them to interpret a number that means nothing outside this repository. The holdout reports a
**total stock budget**, in units they already count, allocated across SKUs greedily by marginal
units-served-per-unit-held so that every unit goes where it converts best. One knob, denominated in
stock, with the service it buys beside it.

***Table 24. The stock budget dial. 1.0× = what the tiered policy commits pre-origin (26,221 units).***

| Budget | Fill rate | Units short | Units held | Served / held | PHP/year *(provisional)* |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5× | 0.4741 | 6,067.6 | 9,113.2 | 0.600 | 12.31 |
| 0.75× | 0.5436 | 5,266.1 | 10,754.6 | 0.583 | 14.53 |
| 1× | 0.6130 | 4,465.6 | 14,647.0 | 0.483 | 19.79 |
| 1.25× | 0.6239 | 4,339.2 | 19,900.6 | 0.362 | 26.88 |
| 1.5× | 0.6564 | 3,964.7 | 25,639.1 | 0.295 | 34.64 |
| 2× | 0.6581 | 3,944.3 | 29,355.5 | 0.259 | 39.66 |
| 3× | 0.6581 | 3,944.3 | 29,355.5 | 0.259 | 39.66 |

Returns flatten hard above about 1.5×: past that, extra stock buys almost nothing, and the 2× and 3×
rows are identical to four decimals. That is the single most useful thing the table says, and it is
the reason a budget rather than a quantile is the right knob to hand over. The peso column is an
average-inventory approximation at the single blended holding rate of Table 13 and is
**provisional**; it is present so the trade can be discussed in money at all, not to be quoted as a
cost.

### Scored against real opening stock

Every service figure above is **reorder-point coverage** — each window is tiled into lead-time
blocks and the question asked is whether `rate·L + buffer` covers the demand in each. That is the
right proxy when no starting stock exists. It is not the quantity the store experiences, which is:
*given what was on the shelf that morning, how much demand walked away?*
`tools/inventory_simulation.py` measures the second one on the subset where it can be measured
without inventing anything.

**Read the coverage before the numbers.** `Inventory_Count` — the table — is empty, and this does
not change that: it is fed by the Digital Tallying Interface and the store has not tallied through
the app. The **historical workbook** `data/USTore_inventory_excel_long_mapped.csv` is a different
thing — 23 monthly hand counts from November 2024 to April 2026, already vocabulary-mapped, all 301
of its items joining cleanly to `Dim_Product.item_name`, and already read by `backend/catalog.py`
and by `step5_prescriptive.py`, whose holding cost `H` is derived from its March 2026 snapshot. The
loader reconciles exactly against figures committed independently elsewhere: **36,051 units for
2026-03**, which is `UNITS_ON_HAND_ESTIMATE` to the unit.

**73 of the 266 scored SKUs (27%)** appear in that workbook, carrying roughly **18%** of scored
demand. After restricting to SKUs the policy actually priced, and to counts fresh enough to be
evidence — a count more than one month old is dropped rather than carried forward, because carrying
it forward would quietly re-invent the starting condition the tool exists to avoid inventing — each
origin simulates 28 to 45 SKUs. **Nothing in this subsection generalises to the 266.**

***Table 25. Simulated coverage at three origins.***

| Origin | Oldest count used | Worst staleness | SKUs | Opening units | Demand |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-02-02 | 2026-02 | 0 mo | 45 | 7,047 | 1,299 |
| 2025-11-04 | 2025-10 | 1 mo | 36 | 6,924 | 1,711 |
| 2025-08-06 | 2025-08 | 0 mo | 28 | 5,370 | 1,555 |

The 2026-05-03 origin drops out: its nearest usable count is two months stale, because the April
2026 sheet is partial — 187 of 1,416 quantities filled, 369 units against March's 36,051 — and
`step5_prescriptive.py` already excludes that month for the same reason. The drop from counted to
simulated is almost entirely SKUs flagged `insufficient_data`: they carry no reorder point, so there
is no policy to simulate for them, and they are the same SKUs the acceptance standard counts as
uncovered demand.

Per SKU, day by day: receipts land, demand arrives, `served = min(on_hand, demand)`, and **unmet
demand is lost, not backordered** — a student who cannot buy a lanyard today does not queue for next
week. Review is continuous on inventory *position*; when position falls to the reorder point an
order of EOQ is placed and arrives `L` days later. Four arms, identical SKUs, identical demand,
identical opening stock — only the reorder rule differs.

***Table 26. Four replenishment rules on real opening stock, pooled over three origins.***

| Arm | Fill | Units short | Stockout days | Mean on hand | Orders |
| --- | ---: | ---: | ---: | ---: | ---: |
| **tiered** (committed policy) | **0.9345** | 299 | 30 | 9,486 | 31 |
| flat *q* = 0.80 | 0.9301 | 319 | 40 | 9,201 | 30 |
| naive (no model) | 0.9281 | 328 | 39 | 8,935 | 30 |
| **none** (no replenishment) | **0.6745** | 1,486 | 248 | 5,826 | 0 |

**Replenishing at all is worth +26 points of fill. Which rule you replenish by is worth 0.6.** The
tiered policy beats naive stocking at 3 of 3 origins, by 0.0064 pooled, while holding 6% more stock.
That is not a dominance; it is a marginal gain bought with inventory. What that costs two claims
already in this chapter, and why the shelf is the moderator, is §4.2.

> **Two different 26-point figures, and they must not be conflated.** The +26 above is *tiered
> against no replenishment at all* (0.9345 − 0.6745), measured here. A second figure of about the
> same size appears in §4.2 and in acceptance condition 2 — *the policy against naive stocking under
> reorder-point coverage* (0.6851 − 0.4264). The coincidence in magnitude is accidental. The first
> says replenishment is worth having; the second is the one the simulation cuts to 0.6.

The two ordering-cost scenarios of Table 13 separate cleanly here for the first time.

***Table 27. The ordering-cost scenarios differ in holding, not in service.***

| Scenario | *S* (PHP/order) | Fill | Mean on hand | Units ordered |
| --- | ---: | ---: | ---: | ---: |
| `low_admin_cost` | 1,250 | 0.9345 | 9,486 | 18,169 |
| `high_goods_value` | 200,000 | 0.9345 | 53,426 | 229,823 |

**Fill is identical to four decimals.** Service is set by the reorder point; the order quantity only
sets how much stock sits idle. `high_goods_value` orders 229,823 units against 4,565 units of
realised demand — a 50× overshoot that makes the EOQ, rather than the reorder point, the dominant
term in holding. The ambiguity in USTore's stated figure therefore puts *holding* at risk and not
*service*, which is the first measurement of what that unresolved input would actually do.

### The acceptance standard and its verdict

`MAPE ≤ 20%` is replaced not by a lower threshold but by a different kind of object: a
four-condition standard whose thresholds are set **a priori**, from what an inventory system must
*do* to be useful, and every one of which **can fail**. `tests/test_acceptance_standard.py` feeds
each condition a fixture built to break it and requires the break, because a criterion that cannot
fail is not a criterion but a certificate. The standard is stated in `docs/ACCEPTANCE_STANDARD.md`
and run by `tools/acceptance_standard.py`, which exits non-zero when the system is not accepted.

***Table 28. The four conditions and the current verdict.***

| # | Condition | Threshold (a priori) | Measured | Status |
| --- | --- | --- | --- | --- |
| 1 | **Actionability** — every eligible SKU carries a state | 100% | 266 / 266 | ✅ |
| | Flagged rows emitting a reorder point | 0 | 0 | ✅ |
| | Priced rows missing a reorder point | 0 | 0 | ✅ |
| 1b | **Forward demand coverage** | ≥ 0.90 | **0.8830** | **❌** |
| 2 | **Beats the no-model alternative** — at *every* origin, not on average | 4 of 4 | 4 of 4 | ✅ |
| 3 | **Not dominated** — no simpler policy gives ≥ service at ≤ stock | none | dominated by none | ✅ |
| 4 | **Evidence integrity** — headline windows ≥ 90% observed, the rest disclosed | 3 of 4 + disclosure | 3 headline, 1 disclosed | ✅ |

> **Superseded 2026-10-01.** Condition 1b now measures **0.8937** against the same a priori 0.90
> and the verdict is still **NOT ACCEPTED — 13 of 14**, on these same four origins. The figure moved
> because July 2026's tallies were completed, not because any threshold was touched. It is directly
> comparable with the 0.8830 below. See §4.4's *A correction, and the data that superseded it*,
> which also explains why an intermediate measurement briefly read 0.9080 on four shifted windows.

**Current verdict: NOT ACCEPTED — 13 of 14 checks pass.** That failure is reported, not repaired,
and it is the proof that the thresholds were not written to be cleared: they would read the same if
the system were failing all four, and one of them is failing.

Forward coverage asks *of the demand that actually arrived, how much came from SKUs the system
priced in advance?* A first draft of the check measured the **trailing** window and scored a perfect
1.0000. That looked like a pass and was arithmetic: a SKU is flagged `insufficient_data` precisely
*because* its trailing window is empty, so flagged SKUs contribute zero to trailing demand by
construction and the ratio can never be anything but 1. It could not fail, so it was not a
condition — the same class of defect as `MAPE ≤ 20%`, caught in the project's own replacement for it.

***Table 29. Forward demand coverage, the failing condition.***

| Origin | Forward coverage | Flagged demand |
| --- | ---: | --- |
| 2026-05-03 | 0.9750 | 296 of 11,834 |
| 2026-02-02 | 0.8964 | 848 of 8,184 |
| 2025-11-04 | **0.7798** | 3,760 of 17,073 |
| 2025-08-06 | 0.9172 | 1,365 of 16,482 |
| **pooled** | **0.8830** | 6,269 of 53,573 |

**It fails because dormant SKUs revive, and it cannot be tuned away.** Lengthening the rate window
is the obvious response and does nothing: coverage moves by **0.0002 across a doubling** of the
window (0.8830 at 365 days, 0.8832 at 456, 547 and 730), while fill falls. The revived SKUs are not
dormant for eighteen months — they have **no sales anywhere in the record before the origin**.
Decomposing the shortfall across all four origins:

Decomposed across all four origins, **6,257 units — 11.68% of all demand — come from SKUs that had
never sold a unit before the decision point**, against 12 units (0.02%) from SKUs with history that
had merely gone quiet.

**99.8% of the shortfall is products with no sales history at all**, and nothing fitted on sales
history can forecast those. The ceiling on forward coverage for any such method is **0.8832**; this
system achieves **0.8830**, capturing 99.98% of what was learnable. The threshold as written
therefore requires the logically impossible, and the planned correction — measuring coverage against
the achievable ceiling rather than against an absolute, keeping the 0.90 bar and its failure on the
record as the audit trail — **is not implemented**. The standard as committed reports NOT ACCEPTED,
and this chapter reports that rather than the correction it anticipates.

One exclusion is deliberate and belongs in the record. **Efficiency is not a condition.**
Units-served-per-unit-held is reported throughout this project and kept out of the standard on
purpose, because it is degenerate in exactly the way MAPE is: a policy that stocks one unit and
sells it scores perfect efficiency and serves nobody. Measured, the naive baseline beats this policy
on efficiency at 4 of 4 origins while serving far less demand. Service and cost are judged
**together**, as a dominance, or not at all — designing the replacement criterion with the
original's failure mode in view is the point of it.

## 4.2  Data Analysis

The dataset is narrower than the 2023 to 2026 scope stated in Sections 1.4.1 and 3.1.1 and in Table
2. This reflects the source records as supplied rather than a loss during processing. The earliest
material is six undated batch aggregates, of which one of thirty-four item labels matches a current
catalog item. These cannot be attributed to specific items or dates without fabricating both, and
are excluded rather than estimated. The usable series is therefore roughly twenty-six months rather
than the three years anticipated, which constrains what any seasonal model can be expected to learn.

The record is sparser than the raw sales count suggests. Of the 84,399 sales records, 68,541 carry a
quantity of zero, leaving 15,858 records of actual sales. The zero records are not observations in
the source workbooks; they are produced by the zero-filling step during loading. The same
distinction appears in the calendar: a tally was recorded on 608 dates, but a sale was recorded on
only 416. Any velocity measure has to state which of those two denominators it uses, because the
difference between them changes the count of observed selling days by roughly a third. This is the
central characteristic of the dataset and it conditions every result that follows.

The allocation results in Table 6 should not be read as three tiers of equal internal confidence.
Only the first rests on a stock observation contemporaneous with the sale. Within the second,
staleness varies widely: the nearest available count is within one month in some cases and as much as
twenty months away in others, and an allocation weighted against a stock position twenty months
removed carries substantially weaker evidence than the category name suggests.

Section 1.4.2 anticipated that proportional imputation would introduce item-level estimation
uncertainty and specified a 0.5 weighting coefficient in response. Table 6 is the empirical basis for
judging that choice. A single coefficient applied uniformly treats the 7,368 records backed by a
same-month count identically to the 1,751 with no count at all, when the evidence behind them differs
materially.

### The acceptance criterion is degenerate, not merely strict

Section 3.3.4 sets MAPE ≤ 20% as the primary acceptance criterion. Divergence #6 established that
the bar is unreachable on this data — the perfect-forecast floor is approximately 89% daily and 60%
monthly, so no model, including a perfect one, clears it. On its own that reads as a request to lower
the bar. The stronger finding is that lowering it does not help, because **an acceptance criterion
defined purely on forecast error is structurally invalid for intermittent demand: its optimum is a
forecast of zero.**

The argument is three steps, each individually unremarkable, and each asserted directly in
`tests/test_degenerate_forecast.py` rather than only observed in the output:

1. For absolute-error loss, the constant that minimises E|y − c| is the **median** of y, not the
   mean. Standard result, verified numerically over ten random series.
2. MASE is MAE divided by a scale computed from the training data, which does not depend on the
   forecast. Dividing an objective by a positive constant cannot move its argmin, so ranking
   candidates by MASE and ranking them by MAE give the **same answer**. This is the step that
   matters, because "use MASE instead of MAPE" is the standard remedy for MAPE's undefined-at-zero
   problem — and it does fix that problem, while leaving this one untouched.
3. `Fact_Sales` is 68,541 zero rows of 84,399 — **81.2%**. Once more than half the observations are
   zero, the median is zero, and so is the error-minimising constant forecast.

Therefore any selection rule that minimises MASE converges, by construction, on "nothing will sell."

Table 8 is that prediction measured. The ranking inverts exactly where the argument says it will:
`rolling_median_30` is second on mean MASE and last on fill rate, at 0.5062, and prices **0 of 266**
SKUs. Three further methods price nothing for the same reason. The cleanest statement of the
trade-off, and the one sentence to carry forward, is this: **TSB scores MASE 5.3262 against the
rolling median's 4.8341 — 10.2% worse on the error metric — and prices the entire catalogue rather
than none of it. Ten percent more forecast error buys the difference between 0 usable SKUs and 266.**
That figure is pinned by a test so it cannot drift in prose.

One method escapes the trap, and it escapes it in the way the argument predicts. `weekly_hurdle_12w`
takes the top mean MASE in Table 8 while pricing 140 SKUs, because it does not forecast a constant at
all: it forecasts a *rate* times a *size*, so its output is positive whenever the item sold in any of
the trailing twelve weeks, even though most individual days in that window are zero. That is a result
about the trap rather than a refutation of it — the degeneracy binds on rules that select a constant
by minimising error, and the escape route is to change the shape of the forecast, not the threshold.

This argument is not confined to this chapter. It is reproduced as the opening of
`docs/PRESCRIPTIVE_CONTRACT.md` and of `docs/ACCEPTANCE_STANDARD.md`, and it is the reason the
prescriptive layer consumes a demand rate rather than a point forecast and the reason the
replacement criterion is a four-condition dominance test rather than a second threshold. It is a
contribution rather than a limitation, and it is demonstrated on the project's own data
with a reproducible script rather than asserted from the literature. What it does *not* say is that
MASE is a bad metric — it is a good one, and it fixes MAPE's undefined-at-zero problem. It says that
MASE cannot be used *alone* as a selection rule on intermittent demand.

### The obvious replacement is also unreachable, on the path it was measured on

Divergence #6 names service level ≥ 95% as the replacement criterion. Checked against the data before
being proposed to the adviser, that replacement fails too — for three separable reasons, one of which
was a defect in the project's own scoring code rather than a property of the demand.

Each of the three is a measurement of the **benchmark path**: periodic review, a point forecast, a
30-day aggregate, folds laid backward from the end of each series. The policy that replaced that
path is scored differently, and the scope matters for what the third of them turns out to have
established.

**Cause 1 — a formula error, since fixed.** The benchmark sized safety stock as `z·σ·√L` with
L = 7 days, the continuous-review reorder-point formula, while the simulated policy is periodic
review: stock is set once, thirty days of demand arrive, and nothing replenishes inside the window.
The interval the buffer must survive is review + lead time = **37 days**, not 7. Correcting
`√7 → √37` scaled every safety stock by 2.2991 and moved `rolling_mean_30`'s fill rate from 0.7098
to 0.7746. Real, worth having fixed, and nowhere near enough. `step5_prescriptive.py` never shared
this defect: its `ROP = ADUS·L + SS` paired with `SS = z·σ·√L` is a correct continuous-review reorder
point, exactly as §3.3.3 specifies. What that raises is a separate question about policy — USTore
reorders on the monthly billing cycle, which is periodic review with R = 30 — and that is a modelling
assumption to justify, not a bug to fix.

**Cause 2 — a hard arithmetic ceiling at 94.90%.** Across the scored folds, **584 folds spanning 103
SKUs have a flat-zero training slice**. σ is zero, every method's point forecast on an all-zero
history is zero, so stock is exactly zero and every unit arriving in the test window is short. That
is **2,732 units — 5.1% of all scored demand — which no stocking policy, no safety-stock formula and
no forecasting method can serve, because the decision is made before any of them are consulted.**

```
total scored demand           53,573
structurally unservable        2,732   (5.1%)
=> ceiling on any fill rate     0.9490
fill on the servable remainder  0.7480
```

**95% is out of reach by 0.10 percentage points before a single modelling choice is made.** It is a
cold-start property of a catalogue where SKUs enter mid-series, and neither tightening nor loosening
the target touches it.

That ceiling is a property of **these folds**, and it does not transfer. On the policy path the
2026-02-02 origin reaches **0.9149** (Table 21), so high service is expensive and window-
dependent rather than arithmetically barred. The reason `service ≥ 95%` is no longer encoded
anywhere as a pass/fail is accordingly not that it cannot be hit — it is that asserting it would
produce a gate that mostly fails for reasons nothing in the pipeline controls.

**Cause 3 — normal quantiles under-size the buffer.** `z·σ` prices the buffer off a normal
distribution. The 30-day aggregate of an 81.2%-zero series is right-skewed, and a normal quantile
understates its upper tail. Replacing the formula with the empirical quantile of the SKU's own prior
forecast errors reaches 0.794 at q = 0.95 on `rolling_mean_30` — better than 0.710, still short of
0.95, because Cause 2 caps it at 0.949 and the remainder is per-SKU volatility that no fixed-quantile
buffer removes.

**Of the three, this is the one that survived into the deployed system.** The empirical quantile is
now the buffer rather than a diagnostic: measured on the policy's own prior-fold errors at the SKU's
own lead-time horizon rather than on 30-day forecast errors, and deployed at 7.46 units mean against
13.84 for the `z·σ` it replaced. Causes 1 and 2 are properties of a scoring harness the prescriptive
layer no longer runs through; Cause 3 was a statement about the demand, and it transferred.

### What replaces the threshold

The conclusion is not to lower the bar a second time. It is that a fixed service threshold is the
wrong *kind* of object for this demand regime. This is also what §1.2 already promised and never
delivered: *"an EOQ-based optimization model to minimize the total inventory cost … subject to a
cycle service level constraint."* The constrained optimisation was written into Chapter 1; what was
missing was the curve it is constrained along.

The frontier in Table 10 was the first answer, and it is not the one that was deployed. Three things
were expected to follow from it. Measurement since has left one of the three standing and replaced
the other two, and the replacement is the stronger result.

**First — the operating point is evidence, not preference. This survives, and the evidence changed
shape.** The knee in Table 10 is real: on the benchmark's curve marginal holding roughly doubles
above *q* ≈ 0.80. On the **policy's own** curve it does not (Table 23). Marginal
holding rises steadily there — 4.8 → 9.4 from *q* = 0.80 to 0.95, a 1.96× rise where the knee test
requires more than 2×, and 1.88× on the separate run `docs/PRESCRIPTIVE_CONTRACT.md` reports. So
*q* = 0.80 is not singled out by the policy's data; it was **inherited** from the benchmark's curve.
Had that gone unmeasured, a population-wide operating point would have been carried forward on the
strength of a knee that is not there. What replaced it keeps the principle and drops the inheritance:
the operating point is resolved **per service tier from each SKU's own curve**, eight distinct
quantiles in use where there was one, and the dial the store turns is a **stock budget in units they
already count** rather than a quantile that means nothing outside this repository.

**Second — the production model dominates at the knee. This is now moot.** Table 11's measurement
stands: at *q* = 0.80, `rolling_mean_30` beats `ets` and `tsb` on both axes. What it was for was
settling a model-selection decision, and there is no longer a decision of that shape to settle. The
prescriptive layer consumes a demand rate and an empirical uncertainty, never a point forecast;
`backend/pipeline.py` says so in the code — *"step5a/step5, neither of which read `Result_Forecast`."*
A ranking of forecasting methods at an operating point selects nothing downstream. The finding that
replaces it is measured on real opening stock and is less flattering: **replenishing at all is worth
+26 points of fill, and which rule you replenish by is worth 0.6.**

**Third — 0.742 is the honest service level. This is superseded by a distribution.** A single number
implied a precision the data does not support. Scored at four rolling origins the policy meets a
**median 0.7064 of realised demand, ranging 0.5987 to 0.9149** — a spread wide enough that the
quarter matters more than the policy. Reporting the spread is the honest statement; reporting 0.742,
or any other single figure, is not, and that applies to the frontier's own number as much as to the
95% it replaced.

### The cost of the trailing window

Table 12's last row is the finding to carry forward, and it is a regression against the earlier
full-history mean. The trailing 30-day window (2 July – 31 July 2026) is empty for 32 of the 58 Fast
SKUs, so the window mean is zero and the forecast is a flat zero line. This is the degenerate-forecast
argument appearing in production rather than in a benchmark: on a majority-zero series the
error-minimising forecast is zero, and a forecast of zero cannot stock anything. It is the same
mechanism that limits `rolling_mean_30` to 79 of 266 SKUs in the benchmark.

The consequence runs into the prescriptive layer, and it is why the demand basis there is observed
history rather than a forecast:

| Demand basis | SKUs priced |
| --- | ---: |
| Trailing 365-day observed *(default)* | **208** |
| `Result_Forecast` (`rolling_mean_30`, 30-day, annualised) | 26 |

EOQ is batching economics — it answers how large an order should be given fixed ordering and holding
costs, and it is insensitive to short-run forecast error. Making the prescriptive layer hostage to a
forecasting-method decision buys nothing and costs 182 SKUs. That choice, provisional when this
table was first drawn, is now the committed contract: the trailing observed window is what the
prescriptive layer consumes, and `Result_Forecast` is not read at all. The anchor sensitivity
makes the same point from another direction: a 30-day demand window anchored on July 2026 prices 79
of 266 SKUs, the same window anchored on June 2026 prices 130, and the 365-day window prices 208. The
30-day figure is not a property of the catalogue; it is a property of which month the window lands
on, and July 2026 falls inside the AY2526 summer term.

### The predictive stage was computed and consumed by nothing

The table above states a cost — 182 SKUs — and reads as a decision about the prescriptive layer. Read
from the other end it is a statement about the **predictive** one, and it is the more uncomfortable
of the two readings. The manuscript's own progression is descriptive → predictive → prescriptive.
Measured stage by stage, that chain is broken at both joints.

***Table 30. The three stages and what each one reaches.***

| Stage | Coverage | Consumed downstream? |
| --- | --- | --- |
| **Descriptive** — `Fact_Sales`, FSN, density, observability | 266 SKUs, 821 days | yes |
| **Predictive** — `Result_Forecast` (`rolling_mean_30` with intervals) | **58 SKUs, of which 32 forecast a flat zero → 26 usable (9.8% of 266)** | **no — orphaned** |
| **Prescriptive** — reorder point, EOQ, service tiers | 266 SKUs scored, 208 priced (78.2%) | — computes its own rate |

`backend/pipeline.py` states the break in a comment rather than hiding it: *"step5a/step5, neither of
which read `Result_Forecast`."* `Result_Forecast_Metrics` holds 174 rows — 58 SKUs at three period
scopes — of which **2 meet the MAPE threshold**, which is the same result Table 12 reports at a
single scope and a different denominator.

This was not a missing stage. It was a **buried** one. `resolve_rates` produces a demand rate for
208 SKUs with an explicit `insufficient_data` state for the rest, and the empirical buffer produces
an uncertainty for each of them — which is what a forecast *is*, expressed in the units the decision
needs. It was simply computed inside the prescriptive script and thrown away.

**It is now published and consumed.** `scripts/step4b_policy_forecast.py` writes the policy's rate
and its lead-time interval into `Result_Forecast` as `model_type='policy_rate'` — one row per day
across each SKU's own lead time, carrying the rate in `yhat` and a band whose upper edge sums across
the lead time to the reorder point — and `step5_prescriptive.py` reads them instead of recomputing.
`rolling_mean_30`'s 1,740 rows are untouched and still what the Demand Forecast screen draws; the
two model types answer different questions and live side by side.

***Table 31. The same three stages, after the predictive one was wired in.***

| Stage | Coverage before | Coverage now |
| --- | --- | --- |
| Descriptive | 266 SKUs, 821 days | unchanged |
| **Predictive** | 58 SKUs, **26 usable (9.8%)**, consumed by nothing | **208 priced + 58 flagged = 266 of 266**, consumed by step5 |
| Prescriptive | 266 scored, 208 priced, computing its own rate | 266 scored, 208 priced, **reading the rows above** |

The flagged 58 matter as much as the priced 208: they appear in the predictive table carrying
`rate_source = 'insufficient_data'` and a NULL `yhat`, rather than being absent from it. A zero
there would read as *"nothing will sell"* — the degeneracy §4.2 opens with, re-entering through the
predictive table instead of the prescriptive one.

**The wiring changes no number, and that is checked rather than claimed.** `--recompute-policy`
keeps the pre-wiring path runnable as a control, and `Result_Prescriptive` comes out **identical in
474 of 474 rows, byte-for-byte across all 27 columns**, whether step5 reads the published rows or
computes them itself. The progression the manuscript describes is now literal; what it describes has
not changed.

### Why accuracy does not improve

Table 15 is a set of negative results with a common cause, and the three diagnostics that isolate it
are worth stating together because no one of them settles the question.

**Accuracy tracks demand density at the dense end.** Bucketing the SKUs by the share of days with any
sale:

***Table 32. Accuracy by demand density (`weekly_hurdle_12w`, median MASE).***

| Density bucket | SKUs | Median MASE | Mean MASE |
| --- | ---: | ---: | ---: |
| Almost never (< 2%) | 82 | 2.45 | 6.02 |
| Rare (2–5%) | 61 | 2.25 | 5.87 |
| Occasional (5–10%) | 50 | 3.08 | 5.00 |
| Regular (10–25%) | 55 | 1.48 | 2.47 |
| Frequent (> 25%) | 14 | **1.03** | **1.24** |

*Source: `data/density_vs_accuracy.csv`. Median MASE is reported first for the reason given at the end
of this section.*

The relationship is not monotonic across the three sparse buckets, which hover between 2.2 and 3.1,
but the two densest buckets are clearly and substantially better — and **only 14 of 266 SKUs (5%)
reach the density at which the model performs well.** The mean column is shown alongside to make the
aggregation problem visible: it is between two and three times the median in every sparse bucket and
converges on it only at the dense end.

**Making the data denser by aggregation does not help.** Summing the same real data into coarser
series raises demand density from 7% to 51%. MAPE improves dramatically, from 203% to 86%, because
there are no longer zero-actual folds to make it undefined. **Median MASE moves from 1.95 to 1.91.**
The MAPE improvement is a metric artifact of aggregation, not a gain in forecastability.

**Sparsity by itself is not the problem.** In controlled synthetic worlds where sparsity is the only
variable — independent days, sale sizes drawn from the real catalogue's pooled distribution, trained
and tested inside the same world — MASE sits between 0.8 and 1.0 at **every** density tested,
including 1%. These methods handle sparse-but-stable demand perfectly well.

The only reading that reconciles all three is that what defeats these methods on the real catalogue
is not the density of zeros but that **the underlying rate shifts over time** — semester cycles,
product lifecycles, one-off events. Aggregation and synthetic history both leave that instability
intact, which is exactly why neither moved the metric. That also explains the two results that look
anomalous in isolation: five extra years of bootstrapped history changed nothing for window-based
methods because history behind a fixed window is structurally invisible to them, and the academic
calendar features changed nothing because a per-SKU classifier does not see enough sale events inside
any single exam week or semestral break to learn from a handful of extra binary columns.

### Pooling, and the leakage audit

Pooling SKUs into a shared model is the natural response to thin per-SKU history, and the question it
raises is not *whether* to pool but *on what basis*. Product taxonomy fails: pooling by category is
worse than per-SKU modelling on MASE, and the finer product-type split is worse again — drinkware,
22 tumblers and mugs and about as internally similar a group as the catalogue contains, was the
single worst-performing group of all. Behavioural clustering succeeds: K-means on five demand-shape
features beats every manual grouping and beats the per-SKU baseline on every method tested
(Table 16).

**This section rests on a correction, and the correction is part of the result.** An independent audit
of the experiment log checked its claims against the code rather than against its own narrative and
found two defects of the same class:

1. **The pooling label leaked the test period.** `Dim_Product.fsn_class` is computed by
   `step3_fsn_classification.py` from the *whole* `Fact_Sales` table with no date bound. Using it to
   choose a walk-forward fold's pooling group meant an early fold's group assignment was partly
   determined by sales occurring after that fold's own origin. The leak is worse than a per-SKU one,
   because the 80th-percentile cut is *relative* — a SKU's label depends on other SKUs' full-history
   totals too, making the leak cross-sectional as well as temporal. The clustering module had the
   identical defect independently: its features were built from each SKU's whole series.
2. **The safety-stock service class leaked on every path.** The z-score used to size the buffer was
   selected by the same static full-history label regardless of grouping choice, so the fill-rate
   column was affected even in runs that were otherwise leak-free.

Neither is target leakage — the label is one bit, it never enters the model as a feature, and the
expected effect is optimistic bias of unmeasured size rather than a fabricated result. But
"unmeasured" was the operative word, and the re-run is what settles it. Both were fixed by
recomputing the pooling label and the service class **per fold, from pre-origin data only**
(`forecasting.category.fold_scoped_speed_labels`), with `tests/test_category_leakage.py` pinning the
mechanism rather than today's numbers.

**The re-run reversed one headline finding and confirmed another.** Recomputing the label at fold 0's
origin flips 54 of 266 SKUs (20%). `category_speed` pooling, which had appeared to be a partial
improvement, became the *worst* grouping tested — xgboost MAE 19.45 → 24.18, global MASE 2.23 → 2.70.
Clustering barely moved: xgboost MAE 14.48 → 14.78, global MASE 1.66 → 1.69.

Why one survived and the other did not is the more durable finding. `fsn_class` is a hard
cross-sectional percentile cut, and 20% of SKUs sit close enough to that boundary to change sides
depending on which window computes it, so leaking the future materially changes who is pooled with
whom. The clustering features describe how a SKU behaves on average, and for most SKUs that does not
change much between the first and second half of an 821-day history — a steady seller is a steady
seller in both windows. **A behavioural feature that is stable over time is a more robust basis for
pooling than a hard percentile cutoff, independent of which performs better on any single run.**

Two consequences for how the tables in this chapter should be read. First, the audit's second finding
is **not yet closed on the statistical-benchmark path**: `scripts/model_benchmark.py` still selects
its safety-stock z from the static `Dim_Product.fsn_class`, so Table 9's fill-rate column carries
that bias, while the fold-scoped z used in the pooled runs does not. This is why the two hurdle
methods' fill rates are footnoted rather than merged into Table 9. Table 10 and Table 11 are
unaffected — the frontier replaces the `z·σ` buffer entirely with an empirical quantile of prior-fold
errors and never consults a class label at all, which is precisely why it is the table the model
selection should rest on.

Second, the mean MASE reported throughout this chapter is a poor summary on its own. An audit of how
MASE aggregates across SKUs found that **1,290 of 3,192 (SKU, fold) pairs have a MASE denominator
below 1.0**, and that the ten worst SKUs alone produce between 23% and 73% of the total mean MASE
depending on the run. For xgboost pooled by category+speed, mean MASE is 9.16 while the median is
3.06, the demand-weighted mean is 4.73 and the global ratio is 2.04. Reading mean MASE alone
materially overstates how much pooling "hurt." Every table in this chapter should be read with the
median and global figures alongside the mean, not instead of it — they answer different questions
(equal weight per SKU, against weight by how much the SKU matters to the business).

### The prescriptive layer rests on provisional costs

Table 14's EOQ column is the clearest statement of what is still missing. Under `low_admin_cost`,
EOQ exceeds a full year of demand for 204 of 208 SKUs, at a median of 4.34× annual demand; under
`high_goods_value` it does so for all 208, at a median of 54.94×. The two scenarios differ by
**exactly** 12.65× for every SKU, because that is √(200,000 ÷ 1,250) and the ratio is the only thing
that changed.

That swing is the finding, not a defect in the arithmetic — `step5_prescriptive.py`'s EOQ passes
every gate it sets itself. It is a symptom of an input that does not yet exist: USTore's stated
₱200,000–500,000 per month is ambiguous between the administrative cost of *placing* an order (what
EOQ requires) and the peso *value* of goods ordered that month (a completely different quantity that
makes EOQ meaningless if substituted). Rather than silently choosing a reading, every SKU is priced
under both, and the divergence between them is reported.

Two consequences are recorded rather than resolved. **The application does not present EOQ as an
order quantity.** Its Reorder screen leads with an order-up-to level — reorder point plus 30 days of
demand at the observed rate, minus what is on hand — which uses only measured inputs (on-hand, ADUS,
lead time) and neither cost estimate. EOQ remains visible in the recommendations table under both
interpretations, greyed with a tooltip wherever it exceeds annual demand. **And the holding cost is
a single blended rate** of ₱1.4563 per unit per year across the whole catalogue: it cannot
distinguish a ₱30 keychain from a ₱1,500 jacket, because USTore supplied one total inventory value
rather than a per-item breakdown. A further question sits underneath it — under consignment the
university may not own the stock at all, in which case the opportunity-cost framing §3.1.1 assumes
does not apply and EOQ is better presented as order batching than as cost optimisation.

### The proxy overstates the comparator, and shelf depth is the moderator

Two claims this project had carried forward did not survive being re-measured against real opening
stock, and recording what that cost is more useful than the claims were.

**The reorder-point proxy overstates the margin over naive stocking by roughly fortyfold.** Under
coverage the tiered policy beats naive **0.6851 against 0.4264** — 26 points. Under simulation, on
the same policy and the same comparator, it beats naive **0.9345 against 0.9281** — 0.6 points, while
holding 6% more stock. The mechanism is not mysterious: coverage scores each lead-time block as
though the shelf were empty at its start, so the buffer must absorb all of that block's variance. A
real shelf carries stock across blocks, and that carried stock absorbs most of the same variance for
free. The buffer is then doing far less work than the proxy credits it with.

This retracts nothing in the coverage test, which measures what it says it measures and says plainly
that it is not an inventory simulation. What it does mean is that **acceptance condition 2 — beats
the no-model alternative — rests on a proxy that exaggerates the margin.** The condition still
passes, 3 of 3 origins under simulation as well as 4 of 4 under coverage, and the threshold and
comparator are unchanged. But a 0.6-point margin is thin enough to sit inside the noise of the design
choices behind it — the staleness cap, the ordering-cost scenario, which 28 to 45 SKUs the workbook
happens to cover — and **the 26-point figure should not be quoted as the system's advantage over
doing nothing.**

**The second casualty is the tiering's dominance over the flat quantile.** Under coverage the
per-tier operating points deliver more demand met on less stock — a dominance, not a trade. Under
simulation that does not reproduce: the two trade places by depth, and per origin the differences
pull in opposite directions. The gaps are **14 to 64 units on 4,565 units of pooled demand**, in a
catalogue where single SKUs routinely move hundreds.

It is *not* that the tiering collapses into the flat rule: only 12–17% of SKUs are assigned exactly
*q* = 0.80, the rest spreading across 0.50–0.95 with mass at both ends. It makes materially different
per-SKU bets that wash out in aggregate. But the tiering is **fitted on 145–179 SKUs and observable
on 28–45**, so the first reading was measured-and-*unresolved* rather than measured-and-refuted, with
sample size as the obvious suspect.

**It is the right suspect, and removing it settles the question against the dominance claim.** A
synthetic shelf gives every priced SKU the same depth relative to its own policy — `C × rate × L`
units of opening stock — so the whole priced catalogue becomes observable at 242 SKUs instead of 52.
Demand, rates, reorder points and lead times are all the real ones; **only the opening condition is
invented**, every row is labelled as such, and the measured shelf remains the anchor. The difference
is read with a bootstrap **paired on the arm and clustered on the SKU**, because both arms run on
identical SKUs and a SKU seen at four origins is one draw rather than four.

***Table 33. Tiered − flat *q* = 0.80, paired bootstrap, 10,000 resamples.***

| Shelf | SKUs | Δ fill | 95% CI | Δ stock held | Verdict |
| --- | ---: | ---: | --- | ---: | --- |
| **measured — the anchor** | 52 | +0.0044 | [−0.0000, +0.0111] | +3.1% | **not separable** |
| synthetic, *C* = 2 | 242 | −0.0018 | [−0.0108, +0.0063] | −5.2% | not separable |
| synthetic, *C* = 4 | 242 | **+0.0196** | [+0.0053, +0.0353] | +15.6% | **trade** |
| synthetic, *C* = 8 | 242 | **+0.0260** | [+0.0145, +0.0411] | +14.3% | **trade** |
| synthetic, *C* = 24 ≈ the measured depth | 242 | **+0.0096** | [+0.0028, +0.0206] | **+0.6%** | **trade** |

**Separable at 5 of 10 depths tested, and a dominance at none of them.** Once enough SKUs are
observable the tiering wins real service — +1.0 to +2.8 points of fill, P(>0) = 1.00 — and at every
depth where it wins it holds **more** stock. The finding is therefore neither the dominance the
contract carried nor the null the measured shelf suggested: **the eight operating points buy service,
and they buy it with inventory.** At the depth bracketing USTore's actual shelf that price is 0.6%
more stock for +0.96 points of fill — a favourable trade, and one the store should be offered
knowingly rather than told is free.

One property of the thin end needs stating, because a column of exact zeros invites the wrong
reading. At *C* ≤ 1, **100% of SKUs open below *both* reorder points**, so both rules fire on day
zero and the lead time — not the reorder point — decides what is served. The arms are identical there
*by construction*. That is a mechanism, not a measurement of indifference, and the sweep reports the
share of SKUs in that state alongside every row.

**Why the margin is small is itself measurable, and it is not the method.** The simulated shelf opens
at **4.2× the window's demand** (3.5× / 4.0× / 5.4× by origin). With that much cover, replenishment
timing barely binds, so nothing feeding replenishment — the demand rate included — gets the chance to
matter. Two checks rule out the obvious alternatives. It is **not an easy-item artifact**: the covered
subset is *slower*-moving than the catalogue, 11% Fast-class against 26%, median 78 units per SKU
against 125. And it is **not a metric artifact**: the `none` arm places zero orders and still meets
67% of demand, so the shelf is doing the work directly.

So the question is regime, not magnitude. Multiplying the measured opening stock and re-running
answers it. **Only the 1.0× row is a measurement; every other row is a counterfactual shelf** and carries
`stock_basis = counterfactual` in `data/inventory_stock_depth.csv`. The reorder points are deliberately not
rescaled, because the policy is fitted on demand history and does not know what is on the shelf,
which is the asymmetry being probed.

***Table 34. Fill by shelf depth. Every row but the measured one is a counterfactual.***

| Shelf ÷ demand | tiered | flat *q*=0.80 | naive | none | **tiered − naive** | tiered − none |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.53× | 0.8519 | 0.8792 | 0.8312 | 0.2607 | 0.0208 | 0.5913 |
| 0.79× | 0.8738 | 0.8635 | 0.8294 | 0.3257 | **0.0444** | 0.5482 |
| 1.06× | 0.8845 | 0.8819 | 0.8412 | 0.3832 | **0.0433** | 0.5013 |
| 1.59× | 0.8874 | 0.9067 | 0.8840 | 0.4715 | 0.0035 | 0.4160 |
| 2.12× | 0.9199 | 0.9292 | 0.9143 | 0.5287 | 0.0056 | 0.3912 |
| 3.18× | 0.9267 | 0.9404 | 0.9074 | 0.6136 | 0.0193 | 0.3131 |
| **4.24× — measured** | **0.9345** | 0.9301 | 0.9281 | 0.6745 | **0.0064** | 0.2600 |
| 6.36× | 0.9567 | 0.9428 | 0.9558 | 0.7426 | 0.0010 | 0.2141 |

**The rate, buffer and tier apparatus is worth 0.6 points at USTore's current shelf and 4.4 points
near one lead-time's cover — about seven times more.** It beats naive at every depth tested, so it is
never harmful; what changes is how much it is worth. The operational reading matters more than the
arithmetic: this project exists to *reduce* inventory, and as it succeeds, cover falls toward the
0.8–1.1× band where the gap is widest. **The forecast becomes more valuable as the project's own
recommendations are implemented, not less.** The 0.6-point result describes USTore's present
stocking, not the method's ceiling.

One property of that table needs stating because an earlier draft of the sweep treated it as a
defect. **Fill is not monotone in shelf depth, and that is not a bug.** Continuous review fires on
inventory *position*, so a deeper opening shelf delays the first trigger and slides every later order
with it; inside a finite window one fewer order lands, and crossing that boundary the shelf gains Δ
units while forgoing a whole order of *Q*. When *Q* > Δ, total availability — and fill — falls as
opening stock rises. Reproduced on a single SKU: **opening 80 serves 344 where opening 70 serves
346**, gaining 10 units of shelf and forgoing an order of 40. A monotonicity assertion was replaced
with a test pinning the mechanism.

### The rate window, reopened — and what closing it cost

The trailing window is 365 days on a growing catalogue, and a shorter one was tested early and
closed. The record of that closure is worth reproducing in full, because the closure was wrong for a
reason that has nothing to do with the measurement.

**The original measurement was correct and reproduces exactly** on its own terms — fixed population,
flat *q* = 0.80, development origin: a flat 120-day window bought **+2.2pp fill and −14.2% holding**
(0.6188 → 0.6415), and cost **63 SKUs** their rate entirely, because they fall under the minimum
sale-days threshold in a shorter window and would be flagged `insufficient_data`. Nothing about that
was wrong. It was ranked *second-order with a real coverage cost* and set aside.

**The ranking was wrong, and the ranking is the finding.** "Second-order" was a judgement made beside
an apparatus the evidence then said beat naive stocking by 26 points. That comparison has since been
measured at **0.6 points**. Against the corrected baseline a 2.2-point lever is roughly *four times*
the thing it was called second-order against — and it is the only lever tested that addresses this
chapter's own diagnosed bottleneck, that the underlying rate shifts, head on rather than around it.

**The coverage cost is removable, and removing it required one layer rather than a new model.**
`resolve_rates(..., short_window=N)` prefers the short window only where a SKU has the sale-days to
support it and otherwise falls through to the committed rule on 365 days. No new `rate_source` value
is introduced — that vocabulary is CHECK-constrained and reaches `Result_Prescriptive`,
`backend/app.py` and the database invariants — so the existing `window_days` field carries which
window was used. `trailing_rate_fn` takes the same cascade and must be given it whenever
`resolve_rates` is, or the empirical buffer is calibrated against a rate the policy does not deploy.
Across four rolling origins at short windows of 90, 120, 180 and 270 days, **forward coverage stays
at 0.8830 and the minimum priced count at 150 — identical to committed at every setting.** The 63
SKUs keep their rate; acceptance condition 1b is untouched.

***Table 35. The cascade on the full population at the development origin.***

| Window | Operating point | Fill | Units held |
| --- | --- | ---: | ---: |
| 365 | flat *q* = 0.80 | 0.6188 | 15,122 |
| **120 cascade** | **flat *q* = 0.80** | **0.6412** | **13,305** |
| 365 | tiered (committed) | **0.6851** | 14,751 |
| 120 cascade | tiered | 0.6603 | 12,758 |

**The cascade dominates the flat baseline** — +2.2pp of fill on 12% less stock, full population,
coverage preserved. **It does not stack with the tiering.** Applied on top it loses 2.5pp under
coverage, and under simulation the interference appears with the signs reversed: the best fill there
is flat *q* = 0.80 *with* the cascade (0.9395 at 10,729 mean on hand), and adding the tiering makes it
worse on both axes (0.9371 at 10,828). The reading is that **the short window and the service tiering
are substitutes**: both put stock where demand can use it, the tiering across SKUs and the window
across time. Together, the tiering reads the more responsive rate's fold errors, concludes less
buffer is needed, and over-trims.

What is settled is that the coverage cost which closed this lever is removed. What is **not** settled
is whether cascade-plus-flat or tiered-plus-365 is the better policy: the two measures disagree in
sign, and the simulation's differences — 0.3 to 0.9pp, 14 to 43 units on 4,565 — sit below what 28 to
45 observable SKUs can resolve, which is the same limit that blocks adjudicating the tiering itself.
**The deployed default is unchanged: the 365-day window with tiering.** `short_window` is measured,
available and defaulted off — the same treatment the cluster-pooled fallback received, and for the
same reason.

### Validity of the comparison

Four safeguards make the tables above comparable, and two limitations bound what they can support.

`forecasting/evaluate.py` enforces a no-leakage assertion when folds are constructed, again per fold
during evaluation, and again across randomised configurations in `tests/test_evaluate.py`: each
`fit_predict` call is handed nothing but its own training slice, so a model physically cannot see its
own test window. The fold layout is computed **once per SKU and handed to every method**, including
the naive baseline, so no method is scored on a different window than its competitor. σ for the
service simulation is computed from the training slice only. And in the synthetic-history
experiments the generated days are **prepended, never appended or substituted**, so every fold's test
target is the same real observed data whether or not the synthetic years exist — the design cannot
manufacture an improvement by feeding the scorer fabricated actuals, because the scorer never sees
them. That guarantee is real and correctly scoped; both leaks described above sat *outside* it, in
code that chose a label before the fold loop began.

The first limitation is that **no untouched holdout exists.** Folds are laid backward from the end of
each series and nothing is reserved beyond them, so roughly thirty method-and-variant combinations —
plus the frontier's operating point — have been compared on the same 3,192 folds they are reported
on. That is a selection-on-test problem, it grows with every experiment added, and the correct
response before the model choice is finalised is to reserve the most recent ~90 days, select on folds
strictly before it, and score only the finalists on it, once.

**That limitation now holds of the benchmark path only.** The policy layer is scored the way the
sentence above prescribes: four rolling non-overlapping origins, every committed quantity — rate,
clusters, tier, buffer quantile — fitted strictly before the origin it is scored against, and a gate
that re-derives all of them with the scored window blanked and requires an identical answer. What
remains, and is labelled in every table that carries it, is that **the most recent of those four
windows is a development set**: it was used to diagnose where the shortfall sat, to test rate-window
and lead-time sensitivity, and to choose efficiency over fill as the tiering variable. The procedure
is clean because the fitting is pre-origin; the *design* was informed by looking, which is researcher
degrees of freedom a panel is right to discount. The remaining three origins are the primary
evidence, and they are reported with their spread rather than averaged into one number.

The second is a **calendar contamination that is upstream of every density figure in this chapter.**
405 of 821 days show zero store-wide sales. Of those, June–July 2024 is 61 consecutive days with no
`Fact_Sales` rows at all — a data gap, not necessarily zero demand — and 88% of Sundays in that
window are zero, while only 4 Sundays in the entire 1,461-day calendar are flagged closed
(`is_store_closed` covers special closures, not the store's regular weekly schedule). Together, 169
of 821 days (21%) are plausibly not trading days yet are currently
modelled as customers wanting nothing. Excluding them raises mean per-SKU demand density from 7.3% to
8.8%. This has not been re-run through the full benchmark, and it should not be until the June–July
2024 gap is confirmed with the client: a data gap and a true zero need opposite treatment, and that
is not a determination to make from the code.

**The policy path has since stopped counting the worst of those days as demand.** A narrower and
separately measured subset — **139 of 821 days (16.9%)** carrying no record of a sale *or* a closure,
where the store demonstrably trades seven days a week (79 Saturdays and 76 Sundays are tallied) — is
now excluded from the denominator: demand rates divide by **days actually observed** rather than by
the full window, and `--zero-fill` restores the old denominator as a control, verified exact at 365
days against 355. `Fact_Sales` is untouched, so every committed invariant holds and **every density
figure in §4.1 still sits on the full calendar.** The two counts are not the same quantity: 169 of
821 is *plausibly not trading*, on the strength of the 2024 gap and the Sunday pattern; 139 of 821 is
*carrying no evidence either way*. Neither supersedes the other, and the June–July 2024 ruling is
still the client's.

The correction is worth recording for what it did **not** do. At the worst-observed origin the rate
rises 1.24× as expected — and the buffer *falls* by more than the rate raises the commitment (−491
units against +365), leaving committed stock slightly lower. The empirical buffer had been
**absorbing the bias**: a systematically low rate produces systematically positive errors, so the
quantile of those errors had grown to cover them. Correcting the rate removes both the bias and its
compensating buffer. Two things follow — the rate USTore is *told* is now correct, which matters
independently of service, and the policy is shown to be robust to this class of data error by
construction, which is the same reason its lead-time sensitivity is small. 123 of the 139 unevidenced
days sit in 2024, so recent windows are 96–98% observed and only the 2025-08-06 origin, at 80.6%,
falls below the standard's bar.

Two smaller data-quality findings belong with these. Fifteen tally dates fall on dates flagged as
store closures, and the month-day pairs repeat annually — 01-09, 02-25, 04-09, 06-12, 06-24 in both
2025 and 2026 — matching Philippine public holidays. Thirteen of the fifteen sold nothing, which is
what a genuine closure looks like; the real residue is two dates that genuinely traded on a flagged
closure, 143 units, 0.16% of volume. The flag is broadly sound and is not being read backwards. And
71 of 519 products fold a price into the item name, of which 12 have a de-priced twin across eight
base families; four of those families have a bare row carrying real sales, so merging them would move
units between SKUs and change the FSN split. Neither is changed here — the controlled vocabulary is
not modified by this analysis — and both are recorded for a staff ruling.

### The cold-start boundary, and a measured attempt on it

The failing acceptance condition has one cause and it is structural: 6,257 units, **11.68% of
realised demand**, arrive from **103 distinct SKUs that had never sold a unit before the decision
point**. Nothing fitted on sales history can forecast those, and the reliable output for them is the
flag rather than a number.

The obvious escape is an **analog model** — borrow a rate from similar existing items rather than
from the item's own history, since the item's own history is empty by definition. That makes
categorisation the thing under test, and it is the one place in this study where the product
taxonomy has never been tried as anything but a pooling basis. `tools/cold_start_donor_test.py`
scores it on the same four origins and the same population as the policy holdout, fitted strictly
pre-origin, on the cold-start subset only.

***Table 36. Donor rules on the cold-start subset. Fitted pre-origin; not deployed.***

| Donor rule | Fill | Units served | Units held | Served / held |
| --- | ---: | ---: | ---: | ---: |
| commit nothing (the current system) | 0.0000 | 0.0 | 0.0 | — |
| borrow from everything (no categorisation) | 0.1392 | 843.8 | 3,935.1 | 0.214 |
| borrow from the same category (apparel / non-apparel) | 0.1412 | 855.8 | 3,974.1 | 0.215 |
| borrow from the same product type (8 buckets) | 0.1552 | 940.5 | 4,852.9 | 0.194 |
| borrow from the same price band (4 quantiles) | **0.1816** | 1,100.5 | 5,763.2 | 0.191 |

Read alone that column says the finer the taxonomy the better. Read alone it is wrong, because a
donor rule can always buy fill by committing more stock. **The control is what settles it:** the
uncategorised donor is rescaled across a range and re-scored, giving fill as a function of stock
with no label in it anywhere, and each labelled rule is compared against that curve *at its own
stock level*.

***Table 37. Each donor rule against an uncategorised control holding the same stock.***

| Rule | Fill | Units held | Control at that stock | Δ | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| category | 0.1412 | 3,974 | 0.1402 | **+0.0010** | above the control |
| product type | 0.1552 | 4,853 | 0.1631 | **−0.0079** | **below** — the label costs fill |
| price band (4) | 0.1816 | 5,763 | 0.1844 | **−0.0028** | **below** — the label costs fill |

*Source: `docs/COLD_START_ANALOG.md`; `tools/cold_start_donor_test.py`, pinned by
`tests/test_cold_start_donor.py`. Measured, **not deployed**.*

**The product taxonomy is not extracting a better rate; it is committing more stock.** Scaling the
uncategorised donor to hold the same 4,853 units reaches 0.1631 where the eight buckets reach
0.1552 — the finer taxonomy is worse than no taxonomy at all once stock is held fixed. That is the
same verdict the pooling experiments reached by a different route: the taxonomy failed as a pooling
basis (Table 16, and drinkware as the worst group of all), and it fails again as a donor basis.
**The apparel/non-apparel label is worth +0.10 percentage points** over scaling alone.

**Price band is not established either, and the reason is a free parameter.** Its verdict flips
sign with the number of quantile bands — Δ from −0.0058 at eight bands to +0.0135 at five — and
there is no principled basis in this repository for choosing one. A result that changes sign with
an analysis choice is a degree of freedom, not a finding, and it is reported rather than resolved.
Product type is below the control at every setting tested, which is why that rejection stands and
this one is withheld.

**One trap belongs in the record before anyone builds this, and it is not about donors.** Acceptance
condition 1b counts a SKU as *covered* if it is priced **at all**, regardless of whether that price
serves any demand. A donor model prices every cold-start SKU, so it moves forward coverage from
**0.8830 to 0.9998** and flips the verdict to ACCEPTED **while leaving 82% of that demand unserved**.
That is the same tautology this project already caught and removed once, in the trailing-coverage
draft of the very same condition. Adopting a donor model without first re-specifying 1b would trade
a reported failure for an unreported one — and the re-specification 1b actually needs is the other
one, measuring coverage against the achievable ceiling, which raises the floor without making the
condition unfailable.

### Four numbers that did not mean what they appeared to

Four times in this project a decision was made against a number that did not mean what it looked
like it meant, and each time the correction came from measurement rather than from review. Set side
by side they are one finding rather than four incidents, and it is the methodological finding this
chapter is best placed to make.

***Table 38. Numbers that misled, and what they actually measured.***

| The number | What it appeared to mean | What it actually meant | How it was caught |
| --- | --- | --- | --- |
| `MAPE ≤ 20%` | forecast quality | its optimum is a forecast of zero | identity, proved on this project's data |
| trailing coverage = **1.0000** | the system prices the demand that arrives | an identity that cannot fail | asking whether the check *could* fail |
| **26 points** over naive stocking | the apparatus is worth a great deal | a proxy overstating the margin ~40× | re-measuring against real opening stock |
| "the rate window is second-order" | a lever not worth pulling | ranked against the number above it | re-ranking after that correction |

The first two are already argued above. The third and fourth are the new ones, and the fourth is the
most instructive because nothing about its underlying measurement was ever wrong: a 120-day window
really does buy +2.2pp of fill for −14.2% of holding. What was wrong was the **comparison it was
judged beside**, which was the project's own headline result, and it closed a real lever for four
days.

What the four have in common is that no amount of code review would have surfaced any of them. Each
was caught by asking what a number would have to look like in order to be false, and then measuring
that. That is the discipline the gates in `tests/test_gates_can_fail.py` and
`tests/test_acceptance_standard.py` encode — every condition is fed a fixture built to break it and
required to break — and it is the practice this project would carry into any further work before it
would carry any particular model.

## 4.3  Dashboard

### The delivered system

The analytical outputs above are delivered through a two-tier presentation layer over the same
`ustore.db` the pipeline builds: a React and Vite front end with seven screens, served by a Flask and
SQLite JSON API of thirty-three endpoints. Nothing in the application invents a number. Every screen
reads and writes through a single data-access module, so no screen knows the API's URL shape, and
`backend/catalog.py` recomputes ADUS, stock position and FSN sensitivity from `Fact_Sales`,
`Dim_Product` and `Dim_Date` on each request rather than serving a cached fixture.

***Table 39. Application screens and their state.***

| Screen | What it shows | State |
| --- | --- | --- |
| Dashboard Overview | Units over time, category mix, top products, FSN split, reorder-now count | Real |
| FSN Classification | ADUS, HVL flag, and the 75th/80th/85th percentile sensitivity table | Real |
| Demand Forecast | 30-day forecast with band, plus observed history and per-SKU accuracy | Real for the 58 Fast SKUs; pending card otherwise. Read by no downstream stage — see §4.2 |
| Reorder Alerts | Stock position, ROP, safety stock and EOQ under both ordering-cost scenarios, plus an explicit state and manual-review note for the 58 SKUs the policy will not price | Real, every figure labelled **provisional** |
| Batch Sales Report | Per-supplier quantities and remittance line totals, with PDF / CSV / XLSX export | Real |
| Digital Tallying Interface | Entry with validation, closure toggle, event flagging, monthly inventory counts, pipeline run | Real; every write is a row in `ustore.db` |
| Analytics (Power BI) | Embedded published report | Placeholder until a report URL is configured |

One change to the Reorder screen belongs in the record because it is a change in what the system is
willing to claim rather than in what it computes. `backend/app.py` previously coerced a missing
reorder point to `0.0` and rendered `needs_reorder = False`, so a SKU the pipeline had no evidence
about was displayed as a SKU with enough stock. It now returns the explicit `insufficient_data` state
and a manual-review note, and the screen renders it as such. The 58 rows this affects are the same
58 that fail the acceptance standard's coverage condition; the screen and the standard now disagree
with each other about nothing.

The tallying interface is the digital counterpart of the paper tally sheet and the one place the
system accumulates new data rather than reading history. It writes real `Fact_Sales`, `Event_Log`,
`Closure_Log` and `Inventory_Count` rows, validated server-side independently of the client check.
Its **Monthly Inventory Count** card exists specifically to chip away at the coverage gap identified
in §4.1: one row per product per month, a recount replacing rather than adding to the earlier figure,
and zero accepted as a valid count because it records that an item is out. Counts feed the live API —
a product the workbook never covered raised stock coverage from 82 to 83 products in testing, and
every catalog row reports whether its figure came from the workbook or a staff count. They do **not**
yet feed the ETL, so a newly counted product shows a current stock but still a null days-of-supply;
wiring them in would change the censoring evidence behind the classification and is left as a team
decision rather than done silently.

The interface also drives the pipeline. A **Full Pipeline Run** card runs the whole chain as a
background job — approximately 40 seconds without the forecast step, 50 seconds with it — streaming
each step's output line by line, with a per-step wall-clock timeout and a Stop control that kills the
process tree rather than orphaning children. A staleness banner compares the last completed run's
high-water marks against the database and warns in three situations: tally entries, events or
closures recorded since the last run; no completed run recorded at all with such records present; or
a most-recent run that stopped or failed part-way, which leaves the result tables half-rebuilt. The
reason this matters is specific: everything the interface writes lands in `ustore.db` immediately,
but the FSN classes, the forecasts and the prescriptive results are only recomputed by a run, so
without the banner the Reorder screen would show reorder points predating a fortnight of tallying
with nothing on screen saying so.

### The Power BI layer

The manuscript's Phase 6 specifies a Power BI dashboard as a read-only presentation layer, on the
principle that no model, forecast or EOQ is ever computed inside the report — everything is computed
in Python and written to result tables first. The embed route is built and configured through a
single environment key; the report file itself has not been authored.

***Table 40. The five specified views and their readiness.***

| # | View | Primary source | Status |
| --- | --- | --- | --- |
| 1 | Stock Status | `Fact_Sales` (`days_of_supply`, `is_censored`) | **Blocked** — only 62 of 519 products have any stock reading; the coverage figure must be on the page, not behind it. The historical workbook covers more than the derivable days-of-supply suggests — 73 of the 266 scored SKUs, ~18% of demand — which is enough to simulate against but not enough to report store-wide |
| 2 | FSN Classification | `Dim_Product` (`fsn_class`, `is_hvl`) | Buildable now |
| 3 | Demand Forecast | `Result_Forecast`, `Result_Forecast_Metrics` | **Unblocked but orphaned** — 1,740 and 174 rows exist, of which 26 of 58 SKUs carry a positive forecast; the line is flat by construction for the rest and should be shown as such, with the page stating that no downstream stage reads this table |
| 4 | Restocking Advisory | `Result_Prescriptive`, `Dim_Parameters` | Buildable now, with both ordering-cost scenarios side by side, never collapsed to one, and the 58 flagged SKUs shown as flagged rather than filtered out |
| 5 | Batch Sales Report | `Fact_Sales` × `Dim_Product` | Buildable now |
| — | Calendar interpretation cards | `Dim_Date` flags: 34 enrollment, 173 exam-week, 54 event, 185 break, 43 closed days | Buildable now |

### What the dashboard deliberately does not show

Three omissions are design decisions and belong in the record as such.

**No fabricated forecast.** A screen shows a pending card whenever the pipeline has not produced that
output, rather than a plausible-looking chart. The same rule applies to the Power BI build: a
placeholder chart styled to look like a forecast would be less honest than an empty page with a
one-line caption, because a number shown on a dashboard ends up quoted in a chapter.

**No peso grand total on the batch report.** Totals are unit counts. Twenty-two of the 519 products
carry no unit price, so a peso total would silently undercount, and — the binding reason — this is an
internal counting document under the BIR constraint, not an invoice. Unit price appears as supplier-remittance
reference data only. The PDF renders through a pure-Python library with no system dependencies and in
Latin-1 by deliberate choice: every catalogue name is already Latin-1, and embedding a Unicode
font would mean shipping a licensed one, so money prints as `PHP 1,234.00` rather than using the peso
sign.

**No store-wide stock percentage.** Any "% in stock" computed over the covered 14–17% of units would
represent the other 86% as either fine or absent, and neither is true — it is simply unmeasured. The
covered subset is shown, labelled as the covered subset.

## 4.4  Summary of Findings

### Against the objectives

> **Editorial note.** The objective descriptors below are paraphrases reconstructed from the build
> plan and the remediation register, which reference the objectives by number and subject but do not
> reproduce their text. Replace each with §1.3's exact wording before this section is submitted; the
> outcomes are measured and do not change.

***Table 41. Objectives and outcomes.***

| Objective | Outcome |
| --- | --- |
| Consolidate the tally workbooks into a single integrated dataset | **Met.** 84,399 sales records, 89,232 units, 519 canonical items, 19 suppliers, zero transformation exceptions |
| Classify products as Fast / Slow / Non-moving with a sensitivity check | **Met.** 58 F (6 HVL) / 228 S / 233 N at the 80th percentile, re-run at the 75th and 85th |
| Forecast demand for the fast-moving items | **Met in substance, not against the stated criterion, and the criterion has since been replaced.** 58 of 58 Fast SKUs scored on 12 walk-forward folds; 2.6× better than persistence; MAPE ≤ 20% cleared by 1 of 58 and shown below to be structurally invalid on this demand. `MAPE ≤ 20%` is retired in favour of a four-condition acceptance standard. Prophet does not appear: it was superseded by a benchmarked rolling mean, which is a reported result rather than a gap. The qualification that matters is downstream — the demand object the prescriptive stage consumes is a **rate**, not this forecast, and `Result_Forecast` is read by nothing |
| Compute reorder point, safety stock and EOQ | **Met, provisionally.** *(Figures superseded — 210 priced / 58 flagged / 478 rows of 268 eligible, verdict NOT ACCEPTED 13 of 14 at coverage 0.8937; see §4.4's correction section for the full before/after.)* 208 SKUs priced under two ordering-cost scenarios from measured lead time and derived holding cost, with a further **58 flagged** rather than silently omitted; every figure flagged pending the site visit. Against the four-condition standard the verdict is **NOT ACCEPTED — 13 of 14 checks pass**, the failure being forward demand coverage at 0.8830 against an a priori 0.90. On the 27% of SKUs with real opening stock the policy meets 93.5% of demand against 67.5% for not replenishing |
| Deliver a dashboard and an automated batch sales report | **Partially met.** Seven working screens over the live database, with PDF / CSV / XLSX export; the Power BI report file itself is specified but unbuilt, and one of its five views is blocked on inventory coverage |

### Principal findings

1. **An acceptance criterion defined purely on forecast error is structurally invalid for
   intermittent demand.** Its optimum is a forecast of zero. Demonstrated by identity on this
   project's own data and measured in Table 8: the best of the ten committed statistical baselines
   on MASE prices 0 of 266 SKUs, and the one method that tops the metric without being degenerate
   does so by changing the shape of the forecast, not the threshold. Ten percent more forecast error
   buys the difference between 0 usable SKUs and 266. It is no longer only a critique: it is the
   opening argument of both committed documents that replaced the criterion — the forecast →
   prescriptive contract and the acceptance standard — and the reason the prescriptive layer
   consumes a rate rather than a forecast.
2. **The layer that replaced it does not consume a forecast at all.** The prescriptive stage takes a
   demand **rate** and an **empirical uncertainty**, and what has to be reliable is the policy those
   imply. The rate has three states and one of them is not a number: 208 SKUs `observed`, 58
   `insufficient_data` — flagged, with no reorder point and no EOQ, where the previous code emitted
   no row at all. The buffer is the quantile of each SKU's own prior-fold policy errors at its own
   lead-time horizon, deployed at 7.46 units mean against 13.84 for the `z·σ` it replaced: smaller,
   and serving more. *(This replaces an earlier finding that a fixed 95% service level is unreachable
   by 0.10 percentage points. That measurement stands — 584 folds across 103 SKUs are structurally
   unservable, capping the benchmark path at 0.9490 — but it is a property of those folds, and one
   policy origin reaches 0.9149.)*
3. **What replaces a threshold is four falsifiable conditions, and the system fails one of them.**
   Thresholds set a priori from what a stocking system must *do*; every condition fed a fixture built
   to break it and required to break. **Verdict: NOT ACCEPTED — 13 of 14 checks pass.** Forward
   demand coverage is 0.8830 against 0.90, because 11.68% of realised demand arrives from SKUs that
   had never sold a unit before the decision point. Lengthening the rate window moves that by 0.0002
   across a doubling, so it is a structural boundary rather than a tuning gap — and the standard
   surfacing it is what a criterion is for. *(This replaces an earlier finding that the frontier's
   knee at q ≈ 0.80 is measured rather than chosen. The knee is real on the benchmark's curve and
   **absent on the policy's own**, where marginal holding rises steadily at 1.88–1.96× against the
   >2× the knee test requires. The operating point is now resolved per tier from each SKU's own
   curve, and the dial the store turns is a stock budget in units they already count.)*
4. **Replenishing at all is worth +26 points of fill; which rule you replenish by is worth 0.6.**
   Measured against real opening stock on the 27% of SKUs the historical workbook covers: the tiered
   policy meets 0.9345 of demand, naive stocking 0.9281 and no replenishment at all 0.6745.
   Separately, and coincidentally close in size, the policy's margin over *naive* under
   reorder-point coverage reads 0.6851 against 0.4264 — also about 26 points — which the simulation
   cuts to 0.6. **The proxy therefore overstates the margin over the no-model baseline roughly
   fortyfold**; acceptance condition 2 rests on that proxy, still passes, and its margin should not
   be quoted at 26. The 0.6 is not a property of the method but of
   how deeply USTore stocks: at one lead-time's cover the gap is **4.4 points**, about seven times
   larger. Since the project exists to *reduce* inventory, **the apparatus becomes more valuable as
   its own recommendations are implemented.** The same simulation adjudicates the service tiering
   against the flat quantile it replaced: on a synthetic shelf that makes all 242 priced SKUs
   observable rather than 52, the two separate from one lead-time's cover upward, the tiering
   winning **+1.0 to +2.8 points of fill and holding more stock at every depth where it wins** —
   **a trade, not the dominance the contract carried.** *(This replaces an earlier finding that the
   production model dominates at the operating point. Table 11's measurement stands; it settles a
   model-selection question the prescriptive layer no longer asks.)*
5. **The binding constraint is rate instability, not sparsity.** Three independent diagnostics —
   accuracy by density, aggregation of real data, and controlled simulation — converge on it. None of
   coarser aggregation, five extra years of history, or academic-calendar features moved the metric,
   and each failed for a reason the mechanism predicts. It is also the diagnosis that reopened the
   rate window: a shorter window is the only lever tested that addresses a shifting rate head on
   rather than around it (Table 35).
6. **Pooling helps, but only on a behavioural basis.** Product taxonomy fails; K-means on demand-shape
   features beats every manual grouping and beats per-SKU modelling on every method tested. The
   deeper result is that a temporally stable feature is a more robust pooling basis than a hard
   percentile cutoff — established by which of the two survived a leakage correction that reversed the
   other. Those same K = 4 clusters are now deployed: the policy's rate shrinkage draws on them
   unchanged. The shrinkage itself was measured and defaulted off as dominated — +12.7 units served
   for +155.1 units held, and no additional SKU priced — which is a result about the fallback, not
   about the clustering.
7. **Two partition leaks were found by audit, closed, and the affected results re-run.** One reversed
   a published finding outright. The mechanism is now pinned by tests rather than by a comment, which
   is the relevant lesson: the original audit was skipped because three docstrings asserted the label
   was not derived, when the project's own ETL derives it. That lesson is now standing practice
   rather than a single correction — the policy holdout re-derives every fitted quantity against a
   blanked window rather than trusting the code that claims to have cut it, and every acceptance
   condition and every prescriptive gate is fed a fixture built to break it.
8. **The prescriptive layer is arithmetically sound and economically unresolved — and the ambiguity
   costs holding, not service.** EOQ swings exactly 12.65× between two readings of a single USTore
   figure, and exceeds a year of demand under both. Simulated against real opening stock the two
   readings return **identical fill to four decimals (0.9345)** while mean on hand goes 9,486 →
   53,426 and units ordered go 18,169 → 229,823 against 4,565 units of realised demand — a 50×
   overshoot. Service is set by the reorder point; the order quantity only sets how much stock sits
   idle. The system therefore recommends an order-up-to level built only from measured inputs, and
   shows EOQ under both interpretations rather than choosing one.
9. **The predictive stage was computed and consumed by nothing, and is now wired in.** The
   manuscript's descriptive → predictive → prescriptive chain was broken at both joints: 266 SKUs
   described, **26 usable forecasts (9.8%)** of which 32 of 58 were a flat zero, and 266 SKUs scored
   prescriptively from a rate the prescriptive stage computed itself and discarded. The stage was
   buried rather than missing — the policy's rate and empirical interval are a forecast in the units
   the decision needs. Materialising them into `Result_Forecast` as `model_type='policy_rate'`,
   alongside the point forecast rather than replacing it, raises predictive coverage from **26 SKUs
   to 208 priced plus 58 flagged — 266 of 266** — and `step5_prescriptive.py` now reads them.
   `Result_Prescriptive` is identical in 474 of 474 rows byte-for-byte either way, so the
   progression became literal without any figure in this chapter moving.
10. **Four times, a decision was made against a number that did not mean what it appeared to mean,
    and each was caught by measurement rather than review.** `MAPE ≤ 20%`, whose optimum is a
    forecast of zero; a trailing-coverage check scoring 1.0000 because it could not fail; a
    26-point margin over naive that a real shelf reduces to 0.6; and a rate-window lever called
    second-order because it was ranked beside that margin. Each was found by asking what the number
    would have to look like in order to be false — the same discipline the falsifiability tests
    encode — and that, rather than any particular model, is what this project would carry forward.

### The claim this chapter makes

Stated once, in the form it should be defended in:

> The forecast's error is bounded by how demand arrives, not by model choice. Over a hundred
> methods and variants were scored on identical walk-forward folds — trailing averages, smoothing,
> Croston/SBA/TSB, hurdle models, ETS, ARIMA/SARIMA, Prophet across many settings, six machine
> learners per-item, pooled and clustered, transformations and blends — and simple trailing
> averages and blends win every fair comparison. More history plateaus after six to nine months;
> four separate synthetic-history experiments failed to beat the shipped models; a forecast that
> knew each item's exact yearly average would still miss by 76.9%. Error tracks burstiness
> (Spearman +0.69) and not history length (−0.10, not significant).
>
> We therefore did not pursue a lower accuracy threshold. We replaced the accuracy target with a
> **decision standard**: price the items that carry the trade and say so explicitly where you
> cannot; beat the no-model alternative at every origin rather than on average; do not be dominated
> on service and cost together; and disclose the share of every window that is evidence rather than
> assumption. Measured on four rolling holdout origins, the system prices 210 of 268 eligible SKUs,
> flags the remaining 58 rather than emitting silent zeros, and beats naive stocking at 4 of 4
> origins.
>
> **One of this project's own conditions returns a verdict against it.** Forward demand coverage
> reaches 0.8937 against an a priori 0.90, so the standard reports NOT ACCEPTED. The threshold has
> not been moved, and the shortfall is decomposed rather than explained away: 99.8% of it is demand
> from SKUs with no sales history at all before the decision point, which nothing fitted on sales
> history can forecast.

An acceptance standard that has never returned a verdict against the system it measures is
indistinguishable from a certificate. This one does, on the figure the project would most like to
clear, and the threshold it fails was written before the measurement existed.

### A correction, and the data that superseded it

Sections 4.1 to 4.3 are transcribed from the artifacts named beside them. Two things happened after
that transcription, in this order, and the second changed the meaning of the first. **Where this
section and a table above disagree, this section is current.**

**First, a measurement error was corrected.** `step5_prescriptive.py::load_series` reindexed every
series onto the full `Fact_Sales` span. step0 zero-fills blank cells to month end for the dense
months, so the panel ran to 2026-07-31 while the tallies stopped at 2026-07-08 — and those 23 days
nonetheless carried 176 `Fact_Sales` rows each, which made the observability mask count them as
evidence. 23 days sat in the denominator of every trailing rate contributing nothing to the
numerator. The defect was diagnosed in `docs/FORECASTING_EXPLORATION_NOTES.md` §2.5 and fixed in
the two forecast steps at the time; that note recorded that the prescriptive stage was **not**
fixed. The two halves were on different branches, and merging them is what allowed the diagnosis to
be finished.

**Then the data caught up.** The tally sheets for July 2026 were completed and the panel rebuilt:
75,120 → 75,151 rows, **89,232 → 95,182 units**, and the last date anything actually sold moved
from 2026-07-08 to **2026-07-31**. The 23 days are no longer zero-fill; they are real trading days.
The correction's code is unchanged and still correct — it ends the series at the last date anything
sold — but it now has nothing to trim, and the history span is the full 821 days again.

That sequence matters for reading the rest of this chapter, because the intermediate state produced
three figures that were artifacts of a trimmed span rather than properties of the system, and all
three have since reverted.

***Table 42. Where the figures stand, through both changes.***

| Quantity | As transcribed in §4.1 | After the correction | **Current** |
| --- | ---: | ---: | ---: |
| History span, days | 821 | 798 | **821** |
| Days counted as evidence | 682 of 821 | 659 of 798 | **682 of 821** |
| `Fact_Sales` rows / units | 84,399 / 89,232 | 84,399 / 89,232 | **84,430 / 95,182** |
| Eligible SKUs | 266 | 266 | **268** |
| SKUs priced / flagged | 208 / 58 | 214 / 52 | **210 / 58** |
| `Result_Prescriptive` rows | 474 | 480 | **478** |
| Priced share | 0.7820 | 0.8045 | **0.7836** |
| Mean safety stock, empirical | 7.46 | 13.442 | **14.782** |
| Pooled forward coverage | 0.8830 ❌ | 0.9080 ✅ | **0.8937 ❌** |
| Verdict | NOT ACCEPTED 13/14 | ACCEPTED 14/14 | **NOT ACCEPTED 13/14** |

**The verdict reverting is the sound reading, and the intermediate pass was the unsound one.** The
rolling origins are anchored off the end of history, so the trimmed span had moved all four of them
and 0.9080 was not comparable with the 0.8830 originally reported. With July tallied the span is
full and the origins are the original four — 2026-05-03, 2026-02-02, 2025-11-04, 2025-08-06 — so
**0.8937 is directly comparable with 0.8830**, measured on the same windows. The improvement is
attributable to the new data, not to a shifted goalpost. Table 29's ceiling argument, that 0.8832
is the limit for any method fitted on sales history, was computed on these same origins and so does
transfer; 0.8937 exceeding it is the new July sales changing which SKUs had history, not a method
beating a proven bound.

**And the service tiering is vindicated, having briefly appeared not to be.** With the trimmed span,
scoring the flat-quantile frontier at every origin — not only at the development set, which is the
distinction §4.2 insists on elsewhere — put the tiering on the efficient frontier at 3 of 4 origins,
and a 30-configuration sweep of `tier_target` × `min_efficiency` found **none** that reached 4 of 4.
On the current data the same measurements reverse:

***Table 43. The tiered policy against the flat frontier, per origin, current data.***

| Origin | Tiered policy | Cheapest flat q reaching that fill | Margin |
| --- | --- | --- | ---: |
| 2026-05-03 | 0.6403 @ 12,158.3 | q = 0.85 → 16,096.1 | **+3,938** |
| 2026-02-02 | 0.9149 @ 28,925.9 | q = 0.95 → 37,484.1 | **+8,558** |
| 2025-11-04 | 0.7277 @ 18,279.7 | q = 0.90 → 19,061.8 | +782 |
| 2025-08-06 | 0.5987 @ 6,795.7 | q = 0.85 → 7,794.0 | +998 |

No flat quantile dominates the tiering at **any** origin, and `tools/tier_operating_point.py` now
finds **13 of 30** configurations clearing that 4-of-4 bar with the deployed operating point among
them — so no change to `tier_target = 0.90, min_efficiency = 0.10` is indicated. The sweep's bar is
deliberately stricter than the gate's majority, because a search permitted to miss one window will
find the configuration that misses the awkward one.

The lesson is worth more than the figure. A measurement artefact produced a false negative about
this project's own contribution, and it survived two gates before a third caught it: the holdout's
original pair of checks compared the tiering against `flat q=0.80` specifically and could not see a
dominator at q=0.85, and `tools/acceptance_standard.py`'s condition 3 read four hand-picked rows
from `data/policy_holdout_comparison.csv` while the dominating point sat unread in
`data/policy_holdout_frontier.csv` beside it. Both now judge against the whole frontier at every
rolling origin, which is why the reversal was detectable at all. **Widening what a gate compares
against is not weakening it**, and the a priori constants — `ALLOW_DOMINATION = False` and the four
thresholds — were not touched in either direction.

**One claim restated in magnitude, one not restated at all.** The empirical buffer still dominates
the retired `z·σ`: 0.5874 fill on 12,631.7 units against 0.5844 on 18,433.7, **31.5% less stock**
where `docs/PRESCRIPTIVE_CONTRACT.md` §4 says 27%. `PRESCRIPTIVE_CONTRACT.md`'s 120-day cascade
comparison has not been re-measured since either change and its figures are superseded; it is not
restated here.

**What the new data cost the forecast, which is the honest direction.** Scored against a fully
tallied July the item model's mean MASE moves from 1.668 to **1.970**, and the category model beats
"repeat the last 30 days" in **7 of 12** categories rather than 12 of 12. Nothing about the models
changed. July 2026 sold 6,997 units, and the previous panel represented most of that month as
fabricated zeros, which the models predicted accurately and meaninglessly. The worse figure is the
better measurement.

**Nine pinned assertions the new data outgrew, reported and not adjusted.**
`tools/assert_invariants.py` fails seven — `Fact_Sales` rows 84,430 against 84,399, units 95,182
against 89,232, zero-quantity rows 67,708 against 68,541, `Dim_Product` 520 against 519,
`fsn_class = S` 229 against 228, products with a row 287 against 286, products with units 268
against 266 — and `tests/test_degenerate_forecast.py` and `tests/test_policy.py` fail one each on
the same figures. None is a defect; each is a contract figure the data has passed. They fail on the
branch the data arrived from as well, which did not update either file. Changing them revises
figures this chapter and the README quote throughout, so it is a decision for the team rather than
a consequence of a merge.

### Limitations

- **Observation window.** Roughly twenty-six usable months, not the three years stated in §1.4.1 —
  a property of the records as supplied, not a processing loss. Any seasonal claim rests on at most
  two observations of each academic-year phase.
- **Imputation.** 17.9% of sales rows are allocated from price-grouped entries, 11.6% of them with no
  stock backing at all and a further 39.7% weighted against a count up to twenty months stale. The
  single 0.5 coefficient does not distinguish these tiers.
- **Cold start.** 11.68% of realised demand arrives from 103 SKUs with no sales history at all before
  the decision point. Nothing fitted on sales history can price them; the system flags them for human
  review, which is the correct operational behaviour and is also why it fails one of its own four
  acceptance conditions. A donor model was measured and not adopted: the category label is worth
  +0.17pp of fill, and adopting one without re-specifying the coverage condition would convert a
  reported failure into an unreported one.
- **Inventory coverage.** 16.8% of rows carry any stock signal; 62 of 519 products have a derivable
  days-of-supply. This limits the censoring evidence and the Stock Status view. **Partly closed for
  the validation** Objective 4 anticipates: the historical workbook supplies real opening stock for
  73 of the 266 scored SKUs (27%, ~18% of demand), and `tools/inventory_simulation.py` scores the
  policy against it. That simulation runs on **28 to 45 SKUs per origin** and generalises to nothing
  wider — and at that sample size it **cannot separate the service tiering from a flat quantile**,
  with pooled gaps of 14 to 64 units on 4,565 where single SKUs routinely move hundreds. The
  `Inventory_Count` table itself remains empty; the store has not yet tallied through the app.
- **Trading-day calendar.** 21% of modelled days are plausibly not trading days, including a
  61-day gap in June–July 2024, and are currently counted as zero demand. **Every density figure in
  §4.1 still sits on that calendar**, because `Fact_Sales` is untouched. A narrower and separately
  measured subset — 139 of 821 days carrying no record of a sale *or* a closure — is now excluded
  from the demand-rate denominator on the policy path only. The June–July 2024 ruling is still
  outstanding, and a data gap and a true zero need opposite treatment.
- **No untouched holdout on the benchmark path.** The 33 method-and-variant combinations in
  `data/robust_metric_comparison.csv` alone, plus the benchmark frontier's operating point, have been
  compared on the same folds they are reported on. **The policy path is scored differently** — four
  rolling non-overlapping origins, every fitted quantity selected strictly pre-origin, with a gate
  re-deriving them against a blanked window — but the most recent of those four origins is a
  **development set**, used to diagnose the shortfall and to choose the tiering variable, and is
  labelled as such wherever it appears.
- **One residual leak on one path.** The statistical benchmark's fill-rate column still selects its
  safety-stock z from a full-history class label. Neither the benchmark frontier nor the deployed
  policy does — the frontier replaces the `z·σ` buffer with an empirical quantile of prior-fold
  errors, and the policy consults a class label for nothing at all.
- **The tiering is adjudicated on a synthetic shelf, not a measured one.** Its separation from the
  flat quantile is established at 242 observable SKUs, and the opening stock that makes those SKUs
  observable is invented — `C × rate × L`, labelled synthetic on every row. On USTore's measured
  shelf the two rules remain not separable at 52 SKUs. The direction of the finding is
  conservative — it *removes* a dominance claim rather than adding one — but the separation itself
  rests on a counterfactual opening condition and is reported as such.
- **The failing acceptance threshold is itself mis-specified, and the correction is not implemented.**
  Forward coverage ≥ 0.90 requires the logically impossible: the ceiling for any history-fitted
  method is 0.8832 and the system reaches 0.8830, capturing 99.98% of what was learnable. Measuring
  coverage against that achievable ceiling rather than against an absolute is the planned correction.
  It is **not built**, and the standard as committed therefore reports NOT ACCEPTED.
- **Provisional cost inputs.** Lead time, holding cost and both ordering-cost readings are estimates
  pending the USTore site visit. The holding cost is a single blended rate across the catalogue.
- **Break-scope metrics rest on a single window.** At 30-day aggregation no window on this calendar
  is majority-break, so the standard-period versus semestral-break split yields exactly one
  break-exposed fold per SKU. Those figures are indicative, not a peer of the twelve-fold overall
  result.

### Decisions this chapter does not make

The evidence above narrows several open questions without closing them, and each closure belongs to a
person rather than to a script.

**Whether a system that fails one of four conditions should be adopted is the adviser's call.** The
standard reports; it does not decide. The same sign-off covers whether the four-condition standard is
accepted in place of `MAPE ≤ 20%` at all, and whether the failing condition should be re-specified
against the achievable ceiling — which would be a defensible correction and would also convert a
reported failure into a pass, so it is exactly the kind of change that should not be made by the
party it flatters.

**Whether the service tiering or the short-window cascade is the better policy is still not
resolved, though it is now resolvable.** *(The tiering half is answered and finds FOR the tiering:
no flat quantile dominates it at any of the four origins, and 13 of 30 operating points clear that
bar with the deployed one among them. An intermediate measurement on a trimmed span briefly found
the opposite — see §4.4. The cascade half stands as written and has not been re-measured.)* Reorder-point coverage and the inventory simulation
disagree in sign, and the simulation's differences sat below what 28 to 45 observable SKUs could
resolve. The synthetic-cover method built to adjudicate the tiering against the flat quantile
applies unchanged to this pair and has not been run on it. What the tiering adjudication does
settle is the shape of the answer: these differences are purchases rather than free gains, so the
question to put to the cascade is not "which wins" but "what does each cost". The deployed default
is unchanged in the meantime, and both alternatives are measured, flagged and defaulted off rather
than deleted.

**Whether to move the service tiering's operating point is a trade, not a fix.** The deployed
point is on the efficient frontier at all four origins, so nothing is wrong with it; but 12 other
configurations also clear that bar, and they are not all the same purchase.
`tier_target = 0.90, min_efficiency = 0.30` reaches 0.6421 units served per unit held against the
deployed 0.5362 — 20% better efficiency on 22% less stock — for about 4 percentage points of fill.
Which side of that the store wants is not a question the evidence answers, and changing the default
moves every reorder point, so it is left open rather than taken.

**Whether the predictive stage should be wired into the chain** — materialising the policy's rate and
empirical interval into `Result_Forecast` so the descriptive → predictive → prescriptive progression
is literal — is a design decision with a clear benefit (26 usable SKUs to 208) and a clear risk (the
table would then be read as a forecast again), and it is left as a team decision rather than taken
silently.

The lead time, holding cost
and ordering cost need the USTore site visit; the June–July 2024 gap, the two dates that traded on a
flagged closure, the May 2024 source-sheet discrepancy of 296 units, and the four price-suffix
families carrying real sales all need a client or staff ruling. The controlled vocabulary, the
allocation groups, the supplier mapping and the calendar ranges are not modified by this analysis in
any case.

---

## Sources for every figure in this chapter

Every figure in this chapter is transcribed from the artifact named beside it, and where two
committed documents report the same quantity differently the divergence and the reading taken are
recorded in `docs/CHAPTER_4_RECONCILIATION.md` §6.

**Two exceptions, both marked.** Tables 42 and 43 were recomputed on 30 September 2026 from a rerun
of the pipeline and the holdout, because they exist to record a correction applied after everything
above them was transcribed. They are the only figures in this chapter produced that way, and the
commands that produced them are named in their source rows below so the recomputation is repeatable
rather than asserted.

| Section | Artifact |
| --- | --- |
| Tables 5–7 | `ustore.db`; `data/allocation_audit.csv`; `scripts/verify_data.py`, `tools/assert_invariants.py` |
| Table 8 | `data/model_benchmark_summary.csv`, `data/model_benchmark_ml_summary.csv`, `data/model_benchmark_category_summary_category_speed.csv`, `data/robust_metric_comparison.csv` |
| Table 9 | `data/model_benchmark_summary.csv` |
| Tables 10–11 | `tools/service_frontier.py`, pinned by `tests/test_service_frontier.py` |
| Table 12 | `Result_Forecast`, `Result_Forecast_Metrics`; `docs/ROLLING_MEAN_FORECAST.md` |
| Tables 13–14 | `Dim_Parameters`, `Result_Prescriptive`; `scripts/step5_prescriptive.py`, `scripts/step5a_set_lead_times.py` |
| Tables 15–17 | `docs/SPARSE_DEMAND_EXPERIMENTS.md`, `docs/FORECAST_METHOD_COMPARISON.md`, `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md`, `data/model_benchmark_category_*.csv` |
| Tables 18–20 — the contract, the rate states, the tiers | `docs/PRESCRIPTIVE_CONTRACT.md` §1, §1b, §2; `forecasting/policy.py`, pinned by `tests/test_policy.py` |
| Tables 21–24 — rolling origins, the tiering, the policy's frontier, the dial | `docs/POLICY_HOLDOUT.md`; `scripts/validate_policy_holdout.py` |
| Tables 25–27 and X-tiering — the inventory simulation and the tiering adjudication | `docs/INVENTORY_SIMULATION.md`; `tools/inventory_simulation.py`, pinned by `tests/test_inventory_simulation.py`; `data/inventory_simulation.csv`; source workbook `data/USTore_inventory_excel_long_mapped.csv` |
| Tables 28–29 — the acceptance standard and its verdict | `docs/ACCEPTANCE_STANDARD.md`; `tools/acceptance_standard.py`, proved falsifiable by `tests/test_acceptance_standard.py` |
| Tables 30–31 — the three stages, before and after wiring | `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md` §7b; `docs/PRESCRIPTIVE_CONTRACT.md` §5; `scripts/step4b_policy_forecast.py`, `scripts/step5_prescriptive.py`, pinned by `tests/test_policy_forecast.py`; `backend/pipeline.py` |
| Table 32 | `data/density_vs_accuracy.csv`, `data/aggregation_density.csv`, `data/sparsity_sensitivity_sim.csv` |
| Table 34 — the shelf-depth sweep | `docs/INVENTORY_SIMULATION.md`; `data/inventory_stock_depth.csv`, every row but 4.24× labelled `stock_basis = counterfactual` |
| Table 35 — the rate-window cascade | `docs/PRESCRIPTIVE_CONTRACT.md` §1a; `forecasting/policy.py::resolve_rates` and `trailing_rate_fn` |
| Tables 36–37 — the cold-start donor rules | `docs/COLD_START_ANALOG.md`; `tools/cold_start_donor_test.py`, pinned by `tests/test_cold_start_donor.py`; `data/cold_start_donor.csv`, `data/cold_start_donor_origins.csv`. Measured, **not deployed** |
| Table 38 — the four misread numbers | Synthesised from the four sources above: `docs/DEGENERATE_FORECAST.md`, `docs/ACCEPTANCE_STANDARD.md` §1, `docs/INVENTORY_SIMULATION.md`, `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md` §5 |
| Tables 39–40 | `backend/app.py`, `UST Prototype Design/src/`, `docs/POWERBI_DASHBOARD_PLAN.md` |
| Table 41 | Every table above, plus `docs/BUILD_PLAN_RECONCILIATION.md` for the objective descriptors |
| Table 42 — the figures through both changes | The fix is `forecasting/history.py` (the shared rule) called from `step5_prescriptive.py::load_series`; the diagnosis is `docs/FORECASTING_EXPLORATION_NOTES.md` §2.5. The three columns are read from `scripts/step4b_policy_forecast.py`, `scripts/step5_prescriptive.py` and `tools/acceptance_standard.py` output at each of the three states. The July 2026 panel arrived in `vault/data/USTore_sales_long_with_zeros.csv.enc`; the pre-completion source check was the `JULY 2026 - TBS` sheet of `rawdata/USTore TBS OCTOBER A.Y. 2025-2026.xlsx`, which held a date column for every day and no literal zero anywhere. Recorded in `docs/OPEN_ISSUES.md` issue 12. **Recomputed for this section, unlike every table above it** |
| Table 43 — the tiering against the flat frontier | `data/policy_holdout_frontier_by_origin.csv` and `data/policy_holdout_origins.csv`, both written by `scripts/validate_policy_holdout.py`; the 30-configuration sweep is `tools/tier_operating_point.py` and `data/tier_operating_point.csv`. The superseded 3-of-4 reading is in that file's history and in `docs/SERVICE_LEVEL_FRONTIER.md`, kept rather than deleted because a measurement that no longer decides anything is still a correct measurement of what it measured. **Recomputed for this section** |
| §4.1 gates and their falsifiability | `scripts/step5_prescriptive.py` (`run_gates`); `tests/test_gates_can_fail.py` |
| §4.2 degeneracy | `docs/DEGENERATE_FORECAST.md`, pinned by `tests/test_degenerate_forecast.py` |
| §4.2 service level | `docs/SERVICE_LEVEL_FRONTIER.md` (Divergence #22) |
| §4.2 leakage audit | `FORECAST_EXPERIMENT_AUDIT.md`; fix in `forecasting/category.py`, pinned by `tests/test_category_leakage.py` |
| §4.2 provenance and divergences | `docs/DATA_PROVENANCE.md`, `docs/DIVERGENCE_REGISTER.md`, `docs/REMEDIATION_WAVE1_STATUS.md` |
| The narrative record behind the policy layer | `docs/WORKLOG_POLICY_AND_ACCEPTANCE.md`, `docs/WORKLOG_INVENTORY_AND_RATE_WINDOW.md`, `docs/WORKLOG_RECONCILIATION_AND_WIRING.md`; the reconciliation of this chapter against them is `docs/CHAPTER_4_RECONCILIATION.md` |
