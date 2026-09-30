# Chapter 4 — source context for drafting

Everything needed to write Chapter 4 (Results and Discussion) of the USTore Demand Forecasting and
Prescriptive Inventory Management capstone. Every number below was measured from the project's own
committed artifacts — the `ustore.db` database, the CSVs under `data/`, and the experiment logs
under `docs/`. Figures are stated here so they do not have to be re-derived.

**This is a source document, not the chapter.** Expand it into chapter prose. Section 4.1 and the
opening of 4.2 already exist in polished form in `docs/CHAPTER_4_DRAFT.md` and should be reused
verbatim where they overlap.

---

## 0. The project in one paragraph

USTore is the University of Santo Tomas merchandise store. It sells apparel and souvenirs on
consignment from external suppliers and records sales on paper tally sheets. This project builds an
ETL pipeline that turns those workbooks into a star-schema SQLite database, classifies every product
Fast/Slow/Non-moving, forecasts demand for the fast movers, and computes reorder point, safety stock
and EOQ — delivered through a React and Flask application plus a specified Power BI dashboard.

---

## 1. Chapter structure to produce

| Section | Contents |
| --- | --- |
| 4.1 Presentation of Results | The dataset, data quality, classification, forecast benchmark, service level, production forecast, prescriptive outputs, experiments |
| 4.2 Data Analysis | Why the acceptance criterion fails, what replaces it, why accuracy does not improve, pooling and the leakage audit, provisional costs, validity |
| 4.3 Dashboard | The delivered application and the specified Power BI layer |
| 4.4 Summary of Findings | Objectives vs outcomes, principal findings, limitations, open decisions |

Tables are numbered from 5 because Tables 1–4 are in Chapters 1–3.

---

## 2. Voice and conventions

- Measured and plain. State what was measured, then what it means. No hype.
- A failing result is reported, never relaxed. Where a criterion is not met, say so and explain why.
- Anything provisional is labelled provisional in the sentence it appears in.
- British spelling throughout (normalisation, minimise, analyse).
- Table captions in the form: *Table 8. Fifteen forecasting methods on identical walk-forward folds.*
- Script and file names in monospace (`step4_forecast_model.py`, `Fact_Sales`).
- Where a figure differs from one previously circulated, flag the difference rather than silently
  reconciling it.

---

## 3. Section 4.1 — Presentation of Results

### 3.1 The integrated dataset

Sales span 2 May 2024 to 31 July 2026. Inventory counts span 1 November 2024 to 1 April 2026.

**Table 5. Loaded dataset volumes.**

| Loaded object | Count |
| --- | --- |
| Sales records | 84,399 |
| – with a positive quantity | 15,858 |
| – with a zero quantity | 68,541 |
| Total units sold | 89,232 |
| Catalog items | 519 |
| – with at least one sales record | 286 |
| – with more than zero total units | 266 |
| Calendar dates in scope | 1,461 |
| – on which a tally was recorded | 608 |
| – on which a sale was recorded | 416 |
| Consignment suppliers after normalisation | 19 |
| Sales coverage | 2 May 2024 to 31 July 2026 |
| Inventory coverage | 1 November 2024 to 1 April 2026 |

Supporting points:

- Item names were consolidated into 519 canonical items through a controlled vocabulary mapping;
  supplier names were normalised from 42 raw strings to 19 suppliers.
- The 233 items with no sales record appear only in inventory sheets.
- **Correction to flag.** "Dates on which a sale was recorded" is **416, not the 411 previously
  circulated.** Both flags derive from the same zero-inclusive source file, filtered differently:
  `is_tally_date` = 608 (any tally row that day), `is_tally_date_positive` = 416 (any positive
  quantity). The old 411 came from a source file predating the July 2026 month. Recorded in
  `docs/REMEDIATION_WAVE1_STATUS.md` §3.

### 3.2 Data quality

- **No source record failed transformation.** The exception table is empty; §3.1.3 committed to
  reporting the proportion of flagged records, and that proportion is zero.
- Price-grouped tally entries — a single quantity covering every item sold at one price point —
  were split by proportional allocation weighted on beginning stock: **5,815 grouped entries became
  15,117 item-level records**, which is 17.9% of `Fact_Sales`, all carrying `imputation_flag = 1`.

**Table 6. Proportional allocation by weighting basis.**

| Weighting basis | Records | Share |
| --- | --- | --- |
| Inventory count from the same month | 7,368 | 48.7% |
| Nearest available count from another month | 5,998 | 39.7% |
| Equal split, no count available | 1,751 | 11.6% |
| Total allocated records | 15,117 | 100% |

- **Inventory coverage is the second limitation.** A zero in `Fact_Sales` means one of three things —
  the store had nothing to sell, stock was on hand and nobody bought, or there is no inventory record
  and the two cannot be told apart — and `is_censored` is the column that separates them. Only
  **14,160 of 84,399 rows (16.8%)** carry any stock signal; **75 of the 286 selling products** appear
  in the inventory workbook; **62 products** have a derivable `days_of_supply`. The remaining 83.2%
  of rows are NULL.

### 3.3 Product classification (FSN)

Method: ADUS (Average Daily Units Sold), allocated rows weighted 0.5, censored days dropped from the
denominator, each SKU's window anchored at its own entry date. Fast = ADUS at or above the 80th
percentile of moving SKUs. Non-moving = no sales record at all. Re-run at the 75th and 85th
percentiles as the sensitivity check §3.3.1 requires.

**Table 7. FSN classification of the 519 catalog items.**

| Class | Definition | Items |
| --- | --- | --- |
| Fast (F) | ADUS at or above the 80th percentile of moving SKUs | 58 |
| – of which High-Velocity-Limited (HVL) | Fast, but fewer than 30 active tally dates | 6 |
| Slow (S) | Has a sales record, ADUS below the cut | 228 |
| Non-moving (N) | No sales record in the observation window | 233 |
| Total | | 519 |

HVL is a reporting modifier on Fast, not a fourth class — the column stays CHECK-constrained to
F/S/N, matching §3.2.

### 3.4 Forecast method comparison

Harness: 266 moving SKUs, horizon 30 days, minimum training window 60 days, up to 12 folds per SKU —
**3,192 (SKU, fold) pairs per method**. All series reindexed onto one shared 821-day calendar so
fold origins are identical across SKUs and methods. **The scoring unit is a 30-day aggregate**, which
is what the prescriptive layer and the monthly billing cycle consume — not a daily point.

**Table 8. Fifteen forecasting methods on identical walk-forward folds (266 SKUs, 3,192 folds).**

| Method | MAE | RMSE | MASE (mean) | MASE (global) | % beating naive | SKUs priced |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| weekly_hurdle_12w | 14.20 | 20.32 | 4.79 | 1.62 | 51.1 | 140 |
| rolling_median_30 | 14.87 | 23.35 | 4.83 | 1.70 | 46.6 | 0 |
| rolling_mean_30 (production) | 13.47 | 20.97 | 5.27 | 1.54 | 51.1 | 79 |
| tsb | 14.23 | 21.79 | 5.33 | 1.63 | 52.6 | 266 |
| ewma_a0.1 | 14.68 | 23.43 | 5.45 | 1.68 | 51.1 | 266 |
| xgboost | 19.45 | 33.24 | 5.67 | 2.23 | 50.0 | 261 |
| rolling_q75_30 | 15.16 | 23.87 | 5.69 | 1.73 | 45.5 | 0 |
| seasonal_naive | 16.59 | 27.31 | 6.30 | 1.90 | 47.4 | 0 |
| random_forest | 23.89 | 41.55 | 6.91 | 2.73 | 45.1 | 254 |
| logistic_hurdle | 15.01 | 22.08 | 7.33 | 1.72 | 47.7 | 141 |
| lightgbm | 30.99 | 40.86 | 8.34 | 3.55 | 38.7 | 260 |
| naive | 34.24 | 70.19 | 8.73 | 3.92 | — | 0 |
| ets | 18.17 | 32.65 | 9.28 | 2.08 | 48.9 | 255 |
| sba | 28.21 | 33.97 | 12.08 | 2.80 | 49.6 | 266 |
| croston | 29.28 | 35.08 | 12.50 | 2.90 | 49.2 | 266 |

Sorted by mean MASE. Best MAE is rolling_mean_30 (13.47); best mean MASE is weekly_hurdle_12w (4.79);
best global MASE is rolling_mean_30 (1.54); best coverage is 266 of 266.

**The "SKUs priced" column is the one to read first.** It counts SKUs for which a method produces a
positive 30-day forecast — the precondition for annualising demand, and therefore for any reorder
point or EOQ. **Four of the fifteen price nothing**: rolling_median_30, rolling_q75_30,
seasonal_naive and naive all return zero on a series whose recent values are mostly zero. The best
of the ten committed statistical baselines on MASE is among them.

### 3.5 Service level and the frontier

Fill rate simulates the stocking decision each forecast implies: stock set once per fold at forecast
plus safety stock, thirty days of demand arrive, nothing replenishes inside the window. Safety stock
is z·σ·√37 (review 30 days + lead time 7), with σ from the training slice only.

**Table 9. Service outcome of the ten committed statistical methods (3,192 folds, 53,573 units of
scored demand).**

| Method | Fill rate | Units short | Units held | SKUs priced |
| --- | ---: | ---: | ---: | ---: |
| ets | 0.7768 | 11,959 | 84,728 | 255 |
| ewma_a0.1 | 0.7755 | 12,026 | 71,097 | 266 |
| rolling_mean_30 (production) | 0.7746 | 12,078 | 68,266 | 79 |
| tsb | 0.7538 | 13,192 | 69,437 | 266 |
| seasonal_naive | 0.7371 | 14,086 | 73,114 | 0 |
| rolling_q75_30 | 0.7256 | 14,702 | 69,501 | 0 |
| croston | 0.7126 | 15,396 | 116,191 | 266 |
| sba | 0.7037 | 15,873 | 111,678 | 266 |
| naive | 0.6793 | 17,183 | 120,912 | 0 |
| rolling_median_30 | 0.5062 | 26,455 | 40,274 | 0 |

The two hurdle methods, scored on the same folds under a fold-scoped service class, reach 0.7274
(weekly_hurdle_12w) and 0.7372 (logistic_hurdle) — footnote them rather than merging them into this
table, because the z-selection rule differs (see §8).

**The best method reaches 77.7% against a 95% target.**

Re-scoring the same 3,192 stored (predicted, actual) pairs with an **empirical quantile of each SKU's
own prior-fold forecast errors** — expanding window, strictly pre-origin, never the fold being
scored — produces a frontier instead of a threshold.

**Table 10. Service / holding frontier for rolling_mean_30.**

| Buffer quantile q | Fill rate | Units short | Units held | Units held per extra unit served |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 0.645 | 19,018.5 | 30,115.0 | — |
| 0.60 | 0.664 | 17,984.0 | 34,031.2 | 3.8 |
| 0.70 | 0.699 | 16,151.4 | 40,511.2 | 3.5 |
| 0.75 | 0.719 | 15,065.5 | 44,773.8 | 3.9 |
| 0.80 | 0.742 | 13,844.4 | 49,852.6 | 4.2 |
| 0.85 | 0.758 | 12,955.3 | 56,972.6 | 8.0 |
| 0.90 | 0.779 | 11,856.5 | 66,360.2 | 8.5 |
| 0.95 | 0.794 | 11,040.2 | 79,714.6 | 16.4 |
| 0.98 | 0.800 | 10,707.4 | 87,887.6 | 24.6 |

The last column is the result: below q ≈ 0.80 a unit of service costs about four units of holding;
above it the price roughly doubles, then doubles again. **The knee at q ≈ 0.80 is a property of the
demand, not a threshold chosen in advance.**

**Table 11. Method comparison at the knee (q = 0.80).**

| Method | Fill rate | Units short | Units held |
| --- | ---: | ---: | ---: |
| rolling_mean_30 | 0.742 | 13,844.4 | 49,852.6 |
| ets | 0.738 | 14,032.1 | 64,336.4 |
| tsb | 0.722 | 14,913.9 | 52,202.2 |

rolling_mean_30 **dominates** — higher service and less stock than both.

### 3.6 The production forecast

`step4_forecast_model.py` forecasts every Fast SKU with rolling_mean_30 — literally the same callable
the benchmark scores, so the benchmark and frontier findings are findings about the production model,
not about a near relative. `Result_Forecast` holds 1,740 rows (58 SKUs × 30 days);
`Result_Forecast_Metrics` holds 174 rows (58 SKUs × three period scopes). **58 of 58 Fast SKUs
scored, 12 folds each, none falling back to a heuristic.**

**Table 12. Validation of the production forecast against the acceptance criteria (58 Fast SKUs).**

| Criterion or measure | Result | Status |
| --- | --- | --- |
| §3.3.4: MAPE ≤ 20% | 1 of 58 SKUs | Not met |
| – MAPE computable for | 44 of 58 (undefined where the 30-day actual is 0) | |
| – Best / median / worst MAPE | 16.9% / 120.6% / 3,254.1% | |
| Divergence #6's replacement: MASE < 1.0 | 23 of 58 nominally, 10 of 58 honestly | Not met |
| – Mean MASE (naive on the same folds) | 2.147 (5.294) | |
| – Median MASE | 1.637 | |
| Divergence #6's replacement: service ≥ 95% | Ceiling of 0.9490 before any modelling choice | Unreachable |
| Beats naive on MAE | 35 of 58 (60%) | Yes |
| – Mean MAE (naive on the same folds) | 40.02 (104.19) | |
| SKUs with a positive 30-day forecast | 26 of 58 | See 4.2 |

Two points to make here:

- **The honest MASE count is 10, not 23.** Thirteen of the twenty-three SKUs with MASE < 1.0 have
  MAE = 0 exactly: they sold nothing across the scored 360-day window, and both the model and the
  naive baseline predicted that perfectly. Counting them would be scoring a forecast of nothing as a
  forecast.
- **The model is materially better than persistence** once both are scored on the same folds and the
  same horizon: MAE 40.02 vs 104.19, MASE 2.147 vs 5.294, roughly 2.6×. Under the earlier 80/20 daily
  holdout the naive baseline appeared to *beat* the model — the model was frozen at the split and
  predicted up to ~70 days ahead while the baseline re-read yesterday's actual at every test point.
  That comparison was not fair and has been replaced.

### 3.7 Prescriptive outputs

All 17 assumption rows live in `Dim_Parameters`, each suffixed "PROVISIONAL — pending Block 5
(USTore site visit)".

**Table 13. Prescriptive inputs (all provisional).**

| Input | Value | Basis |
| --- | --- | --- |
| Holding cost H | ₱1.4563 per unit per year | 0.25 × ₱210,000 inventory-value midpoint ÷ 36,051 units on hand |
| Ordering cost S — low_admin_cost | ₱1,250 per order | Midpoint of a plausible ₱500–2,000 administrative range |
| Ordering cost S — high_goods_value | ₱200,000 per order | USTore's own monthly figure taken literally |
| Lead time | 14 d (122 products), 18 d (371), 28 d (26) | Name-keyword garment classifier |
| Service z | 1.65 (class F), 1.04 (class S) | §3.3.3; class N excluded from pricing |
| Review period | 30 days | The monthly billing cycle §3.3.2 names |
| Demand basis | Trailing 365-day observed sum | Default; independent of the open model-selection decision |

**Table 14. Prescriptive results.**

| Measure | Value |
| --- | --- |
| Result_Prescriptive rows | 416 = 208 priced SKUs × 2 ordering-cost scenarios |
| – Fast SKUs priced | 44 |
| – Slow SKUs priced | 164 |
| σ from observed per-SKU history | 334 of 416 rows |
| σ from class-median CV fallback (thin history) | 82 of 416 rows |
| EOQ exceeding a year of demand — low_admin_cost | 204 of 208 SKUs, median EOQ ÷ annual demand 4.34× |
| EOQ exceeding a year of demand — high_goods_value | 208 of 208 SKUs, median EOQ ÷ annual demand 54.94× |
| EOQ ratio between the two scenarios | Exactly 12.65× = √(200,000 ÷ 1,250), for every SKU |

Worked examples: `Lanyard @180` — annual demand 5,094, reorder point 425, safety stock 174, EOQ
2,957 (low) or 37,406 (high). `5M Rotating Keychain` — annual demand 1,389, EOQ 1,544 or 19,533.
Presenting either EOQ as an order quantity would recommend between four and fifty-five years of
stock.

### 3.8 The experiment programme

Logged across `docs/FORECAST_METHOD_COMPARISON.md`, `docs/SPARSE_DEMAND_EXPERIMENTS.md` and
`docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md`. Most were negative, and several fail for reasons that
identify the actual constraint.

**Table 15. Experiments that did not improve accuracy.**

| Experiment | Design | Result |
| --- | --- | --- |
| EWMA (α = 0.1) | Geometric decay in place of a boxcar window | Sits between the two methods already in use on every metric; displaces neither |
| Rolling 75th percentile | Bias the point forecast upward to cover rather than predict | Prices 0 of 266 SKUs; fill rate falls to 0.726 |
| Fitted per-SKU hurdle classifier | Logistic regression on weekday, recency, trailing rates | Worse than the single empirical rate on every error metric (MASE 7.33 vs 4.79) |
| Forecast at a coarser level | Same model, per-category and whole-store series | MAPE 203% → 86%, but median MASE barely moves (1.95 → 1.91) |
| Five extra years of history | Weekday-stratified bootstrap, prepended, never substituted | No change at all for window-based methods; ~1% for TSB |
| Academic-calendar features | Enrollment / exam / event / break flags added to the hurdle model | Median change in per-SKU MAE was exactly 0.0; only 114 of 266 SKUs improved |
| Pooling by product type | Eight keyword buckets (clothes, drinkware, bags, and so on) | Worse than category alone; drinkware — the most internally similar group — was the single worst performer |
| Pooling by category + speed | Apparel/non-apparel × fast/slow | After the leakage fix, the worst grouping tested (xgboost MAE 19.45 → 24.18) |

Three results went the other way.

**Table 16. Grouping bases for pooled models, after the leakage fix.**

| Method | Grouping | MAE | RMSE | MASE (mean) | SKUs priced |
| --- | --- | ---: | ---: | ---: | ---: |
| xgboost | per-SKU (baseline) | 19.45 | 33.24 | 5.67 | 261 |
| xgboost | category | 15.42 | 23.02 | 12.71 | 266 |
| xgboost | category + speed | 24.18 | 32.32 | 13.08 | 266 |
| xgboost | product type | 16.43 | 24.84 | 13.80 | 266 |
| xgboost | cluster (K = 4) | 14.78 | 21.72 | 5.52 | 266 |
| random_forest | per-SKU (baseline) | 23.89 | 41.55 | 6.91 | 254 |
| random_forest | category + speed | 34.54 | 48.58 | 17.04 | 266 |
| random_forest | product type | 19.69 | 30.02 | 17.74 | 266 |
| random_forest | cluster (K = 4) | 17.36 | 26.36 | 6.52 | 266 |
| logistic_hurdle | per-SKU (baseline) | 15.01 | 22.08 | 7.33 | 141 |
| logistic_hurdle | category + speed | 14.74 | 22.00 | 6.23 | 141 |
| logistic_hurdle | cluster (K = 4) | 14.27 | 21.29 | 5.54 | 141 |

**Clustering wins every comparison.** K-means on five behavioural features per SKU — demand density,
mean non-zero sale size, coefficient of variation of non-zero sales, log total demand, log unit price
— with K = 4 chosen by elbow (inertia 147.9 → 94.9 → 81.9) and checked against silhouette (0.327 at
K = 4 against a peak of 0.333 at K = 3). No feature reads the category or FSN column, so the result
is directly comparable against every manual grouping. Clusters 0 and 3 both mix apparel and
non-apparel freely — the partition cuts across the product taxonomy.

**Table 17. Cluster composition and per-cluster accuracy (xgboost, pooled within cluster).**

| Cluster | Character | SKUs | MAE | MASE |
| --- | --- | ---: | ---: | ---: |
| cluster0 | High-density, high-value steady sellers | 31 | 51.65 | 1.82 |
| cluster1 | Very sparse — the hard tail | 117 | 1.89 | 6.80 |
| cluster2 | Extreme bulk-order outliers | 2 | 7.55 | 12.81 |
| cluster3 | Moderate mixed sellers | 116 | 18.06 | 5.13 |

MAE is not comparable across these rows — cluster1's 1.89 is low because those SKUs barely sell, not
because they are forecast well — so MASE is the column to read. Cluster0 at 1.82 is genuinely
competitive with per-SKU modelling.

The other two positives:

- **Pooling rescues the fitted hurdle classifier.** Per SKU it was the weakest hurdle variant
  (MASE 7.33), because most SKUs have too few sale events for a ten-parameter model to learn weekday
  and recency effects without fitting noise. Pooling the sale-probability classifier across a group
  while keeping each SKU's own size estimate improves it on every metric, and improves it further
  under clustering than under category+speed (7.33 → 6.23 → 5.54).
- **The weekly hurdle model** (weekly_hurdle_12w): the fraction of the trailing twelve weeks with any
  sale, multiplied by the mean size of the non-zero weekly totals, spread evenly across the horizon.
  Deliberately a plain empirical rate rather than a smoothing recursion. Lowest mean MASE of the
  fifteen methods (4.79), and prices 140 SKUs against the production model's 79 — the first method in
  this study to combine the best error metric with a non-degenerate output. It does not dominate:
  rolling_mean_30 still wins MAE and fill rate on the SKUs it prices.

---

## 4. Section 4.2 — the arguments to make

The existing draft's first four paragraphs (narrow observation window, sparsity and the two
denominators, the three unequal allocation tiers, the single 0.5 coefficient) stay as they are. What
follows are the arguments to add.

### 4.1 The acceptance criterion is degenerate, not merely strict

§3.3.4 sets MAPE ≤ 20%. Divergence #6 already established the bar is unreachable — the
perfect-forecast floor is ≈89% daily and ≈60% monthly, so no model including a perfect one clears it.
On its own that reads as a request to lower the bar. **The stronger finding is that lowering it does
not help, because an acceptance criterion defined purely on forecast error is structurally invalid
for intermittent demand: its optimum is a forecast of zero.**

Three steps, each asserted in `tests/test_degenerate_forecast.py` rather than merely observed:

1. For absolute-error loss, the constant minimising E|y − c| is the **median** of y, not the mean.
2. MASE is MAE divided by a training-data scale that does not depend on the forecast. Dividing an
   objective by a positive constant cannot move its argmin, so ranking by MASE and ranking by MAE
   give the **same answer**. This is the load-bearing step, because "use MASE instead of MAPE" is the
   standard remedy for MAPE's undefined-at-zero problem — and it fixes that problem while leaving
   this one untouched.
3. `Fact_Sales` is 68,541 zero rows of 84,399 — **81.2%**. Past 50% zeros the median is zero, and so
   is the error-minimising constant forecast.

Therefore any selection rule minimising MASE converges by construction on "nothing will sell."

Table 8 is that prediction measured. rolling_median_30 is second on mean MASE, has the lowest median
MASE of any method, is last on fill rate at 0.5062, and prices 0 of 266 SKUs. **The sentence to carry
forward: TSB scores MASE 5.3262 against the rolling median's 4.8341 — 10.2% worse on the error metric
— and prices the entire catalogue rather than none of it. Ten percent more forecast error buys the
difference between 0 usable SKUs and 266.** Pinned by a test so it cannot drift in prose.

One method escapes, in the way the argument predicts: weekly_hurdle_12w takes the top mean MASE while
pricing 140 SKUs, because it does not forecast a constant at all — it forecasts a rate times a size,
so its output is positive whenever the item sold in any of the trailing twelve weeks. That is a
result about the trap, not a refutation of it: the degeneracy binds on rules that select a constant
by minimising error, and the escape route is to change the shape of the forecast, not the threshold.

This is a contribution rather than a limitation, demonstrated on the project's own data with a
reproducible script rather than asserted from the literature. It does **not** say MASE is a bad
metric — only that MASE cannot be used *alone* as a selection rule on intermittent demand.

### 4.2 The obvious replacement is also unreachable

Divergence #6 names service level ≥ 95% as the replacement. Checked against the data before being
proposed to the adviser, it fails too, for three separable reasons.

**Cause 1 — a formula error, since fixed.** The benchmark sized safety stock as z·σ·√L with L = 7,
the continuous-review reorder-point formula, while the simulated policy is periodic review: stock set
once, thirty days of demand, no replenishment inside the window. The interval the buffer must survive
is review + lead time = **37 days**. Correcting √7 → √37 scaled every safety stock by 2.2991 and
moved rolling_mean_30's fill rate from 0.7098 to 0.7746. Real, worth fixing, nowhere near enough.
`step5_prescriptive.py` never shared this defect — its ROP = ADUS·L + SS paired with SS = z·σ·√L is a
correct continuous-review reorder point, exactly as §3.3.3 specifies. What that raises is a separate
question about policy (USTore reorders on the monthly billing cycle, which is periodic review with
R = 30) — a modelling assumption to justify, not a bug.

**Cause 2 — a hard arithmetic ceiling at 94.90%.** **584 folds spanning 103 SKUs have a flat-zero
training slice.** σ is zero, every method's forecast on an all-zero history is zero, so stock is
exactly zero and every arriving unit is short. That is **2,732 units — 5.1% of all scored demand —
that no stocking policy, safety-stock formula or forecasting method can serve, because the decision
is made before any of them are consulted.** Total scored demand 53,573; structurally unservable
2,732; ceiling on any fill rate 0.9490; fill on the servable remainder 0.7480. **95% is out of reach
by 0.10 percentage points before a single modelling choice is made** — a cold-start property of a
catalogue where SKUs enter mid-series.

**Cause 3 — normal quantiles under-size the buffer.** z·σ prices the buffer off a normal
distribution; the 30-day aggregate of an 81.2%-zero series is right-skewed, and a normal quantile
understates its upper tail. The empirical-quantile replacement reaches 0.794 at q = 0.95 — better
than 0.710, still short of 0.95, because Cause 2 caps it at 0.949 and the rest is per-SKU volatility
no fixed-quantile buffer removes.

### 4.3 What the frontier delivers

Not a second lowered bar — a different *kind* of object. Three points:

1. **The operating point is evidence, not preference.** The marginal cost of service roughly doubles
   above q ≈ 0.80, so the knee is measured rather than chosen.
2. **At the knee, rolling_mean_30 dominates** ets and tsb on both axes simultaneously (Table 11),
   which removes the accuracy-versus-usability trade the model-selection decision was deadlocked on.
3. **0.742 is not a good service level in absolute terms** — it is the honest one at a defensible
   holding cost, and stating it with the curve behind it is stronger than asserting a 95% never met.

This also delivers §1.2's own promise, written into Chapter 1 and never operationalised: *"an
EOQ-based optimization model to minimize the total inventory cost … subject to a cycle service level
constraint."* The constraint was stated; the curve is the missing half.

### 4.4 The cost of the trailing window

The trailing 30-day window (2–31 July 2026) is empty for **32 of the 58 Fast SKUs**, so the window
mean is zero and the forecast is a flat zero line. This is the degenerate-forecast argument appearing
in production rather than in a benchmark, and the same mechanism that limits rolling_mean_30 to 79 of
266 SKUs in the benchmark.

The consequence runs into the prescriptive layer, and is why the demand basis there is observed
history:

| Demand basis | SKUs priced |
| --- | ---: |
| Trailing 365-day observed (default) | 208 |
| Result_Forecast (rolling_mean_30, 30-day, annualised) | 26 |

EOQ is batching economics — it answers how large an order should be given fixed ordering and holding
costs, and is insensitive to short-run forecast error. Making the prescriptive layer hostage to an
open model-selection decision buys nothing and costs 182 SKUs.

The anchor sensitivity makes the same point from another direction: a 30-day demand window anchored
on July 2026 prices 79 of 266 SKUs; anchored on June 2026 it prices 130; the 365-day window prices
208. The 30-day figure is a property of which month the window lands on, not of the catalogue —
July 2026 falls inside the AY2526 summer term.

### 4.5 Why accuracy does not improve

Three diagnostics converge; no one of them settles it alone.

**Accuracy tracks demand density at the dense end.**

**Table 18. Accuracy by demand density (weekly_hurdle_12w).**

| Density bucket | SKUs | Median MASE | Mean MASE |
| --- | ---: | ---: | ---: |
| Almost never (< 2%) | 82 | 2.45 | 6.02 |
| Rare (2–5%) | 61 | 2.25 | 5.87 |
| Occasional (5–10%) | 50 | 3.08 | 5.00 |
| Regular (10–25%) | 55 | 1.48 | 2.47 |
| Frequent (> 25%) | 14 | 1.03 | 1.24 |

Not monotonic across the three sparse buckets, which hover between 2.2 and 3.1, but the two densest
buckets are clearly better — and **only 14 of 266 SKUs (5%) reach the density at which the model
performs well.** The mean column is shown alongside to make the aggregation problem visible: two to
three times the median in every sparse bucket, converging only at the dense end.

**Making the data denser by aggregation does not help.** Summing the same real data into coarser
series raises density from 7% to 51%. MAPE improves dramatically, 203% → 86%, because there are no
longer zero-actual folds to make it undefined. **Median MASE moves 1.95 → 1.91.** The MAPE
improvement is a metric artifact of aggregation, not a gain in forecastability.

**Sparsity by itself is not the problem.** In controlled synthetic worlds where sparsity is the only
variable — independent days, sale sizes drawn from the real catalogue's pooled distribution, trained
and tested inside the same world — MASE sits between 0.8 and 1.0 at **every** density tested,
including 1%. These methods handle sparse-but-stable demand perfectly well.

**The only reading that reconciles all three: what defeats these methods is not the density of zeros
but that the underlying rate shifts over time** — semester cycles, product lifecycles, one-off
events. Aggregation and synthetic history both leave that instability intact, which is exactly why
neither moved the metric. It also explains the two results that look anomalous in isolation: five
extra years of bootstrapped history changed nothing for window-based methods because history behind a
fixed window is structurally invisible to them, and the calendar features changed nothing because a
per-SKU classifier does not see enough sale events inside any single exam week or break to learn from
a handful of extra binary columns.

### 4.6 Pooling, and the leakage audit

The question is not *whether* to pool but *on what basis*. Product taxonomy fails; behavioural
clustering succeeds (Table 16).

**This section rests on a correction, and the correction is part of the result.** An independent
audit checked the experiment log's claims against the code rather than against its own narrative and
found two defects of the same class:

1. **The pooling label leaked the test period.** The FSN class is computed from the *whole*
   `Fact_Sales` table with no date bound, so using it to choose a walk-forward fold's pooling group
   meant an early fold's group assignment was partly determined by sales occurring after that fold's
   own origin. Worse than a per-SKU leak, because the 80th-percentile cut is *relative* — a SKU's
   label depends on other SKUs' full-history totals, making the leak cross-sectional as well as
   temporal. The clustering module had the identical defect independently: its features were built
   from each SKU's whole series.
2. **The safety-stock service class leaked on every path.** The z-score sizing the buffer was
   selected by the same static full-history label regardless of grouping, so fill rate was
   contaminated even in runs that were otherwise leak-free.

Neither is target leakage — the label is one bit, never enters the model as a feature, and the
expected effect is optimistic bias of unmeasured size rather than a fabricated result. But
"unmeasured" was the operative word. Both were fixed by recomputing the pooling label and the service
class **per fold, from pre-origin data only**, with `tests/test_category_leakage.py` pinning the
mechanism rather than today's numbers.

**The re-run reversed one headline finding and confirmed another.** Recomputing the label at fold 0's
origin flips **54 of 266 SKUs (20%)**. category_speed pooling, which had appeared to be a partial
improvement, became the *worst* grouping tested (xgboost MAE 19.45 → 24.18, global MASE 2.23 → 2.70).
Clustering barely moved (MAE 14.48 → 14.78, global MASE 1.66 → 1.69).

**Why one survived and the other did not is the durable finding.** The FSN class is a hard
cross-sectional percentile cut, and 20% of SKUs sit close enough to that boundary to change sides
depending on which window computes it, so leaking the future materially changes who is pooled with
whom. The clustering features describe how a SKU behaves on average, and for most SKUs that does not
change much between the first and second half of an 821-day history — a steady seller is a steady
seller in both windows. **A behavioural feature that is stable over time is a more robust basis for
pooling than a hard percentile cutoff, independent of which performs better on any single run.**

Two consequences for how the tables read:

- **The audit's second finding is not yet closed on the statistical-benchmark path.**
  `scripts/model_benchmark.py` still selects its safety-stock z from the static FSN class, so Table
  9's fill-rate column carries that bias while the fold-scoped z used in the pooled runs does not.
  Tables 10 and 11 are unaffected — the frontier replaces the z·σ buffer entirely with an empirical
  quantile of prior-fold errors and never consults a class label, which is precisely why it is the
  table model selection should rest on.
- **Mean MASE is a poor summary on its own.** **1,290 of 3,192 (SKU, fold) pairs have a MASE
  denominator below 1.0**, and the ten worst SKUs alone produce between 23% and 73% of the total mean
  MASE depending on the run. For xgboost pooled by category+speed: mean 9.16, median 3.06,
  demand-weighted 4.73, global 2.04. Read median and global alongside the mean, not instead of it —
  they answer different questions (equal weight per SKU, versus weight by how much the SKU matters
  to the business).

### 4.7 The prescriptive layer rests on provisional costs

Table 14's EOQ column is the clearest statement of what is missing. The two scenarios differ by
**exactly 12.65×** for every SKU, because that is √(200,000 ÷ 1,250) and the ratio is the only thing
that changed.

**That swing is the finding, not a defect in the arithmetic** — the EOQ passes every gate it sets
itself. It is a symptom of an input that does not yet exist: USTore's stated ₱200,000–500,000 per
month is ambiguous between the administrative cost of *placing* an order (what EOQ requires) and the
peso *value* of goods ordered that month (a different quantity that makes EOQ meaningless if
substituted). Rather than silently choosing a reading, every SKU is priced under both.

Two consequences recorded rather than resolved:

- **The application does not present EOQ as an order quantity.** Its Reorder screen leads with an
  order-up-to level — reorder point plus 30 days of demand at the observed rate, minus what is on
  hand — using only measured inputs (on-hand, ADUS, lead time) and neither cost estimate. EOQ stays
  visible under both interpretations, greyed wherever it exceeds annual demand.
- **The holding cost is a single blended rate** of ₱1.4563 per unit per year across the whole
  catalogue: it cannot distinguish a ₱30 keychain from a ₱1,500 jacket, because USTore supplied one
  total inventory value rather than a per-item breakdown. Underneath it sits a further question —
  under consignment the university may not own the stock, in which case the opportunity-cost framing
  §3.1.1 assumes does not apply and EOQ is better presented as order batching than cost optimisation.

### 4.8 Validity of the comparison

**Four safeguards.** The evaluation harness enforces a no-leakage assertion when folds are
constructed, again per fold during evaluation, and again across randomised configurations in its own
test suite: each fit call is handed nothing but its own training slice, so a model physically cannot
see its own test window. The fold layout is computed **once per SKU and handed to every method**,
including the naive baseline. σ for the service simulation comes from the training slice only. And in
the synthetic-history experiments the generated days are **prepended, never appended or
substituted**, so every fold's test target is the same real observed data whether or not the
synthetic years exist — the design cannot manufacture an improvement by feeding the scorer fabricated
actuals, because the scorer never sees them. Both leaks above sat *outside* that guarantee, in code
that chose a label before the fold loop began.

**Two limits.**

- **No untouched holdout exists.** Folds are laid backward from the end of each series with nothing
  reserved beyond them, so the 33 method-and-variant combinations in the robust-metric comparison
  alone, plus the frontier's operating point, have been compared on the same folds they are reported
  on. That is a selection-on-test problem that grows with every experiment. The correct response
  before the model choice is finalised: reserve the most recent ~90 days, select on folds strictly
  before it, score only the finalists on it, once.
- **A trading-day contamination upstream of every density figure.** 405 of 821 days show zero
  store-wide sales. June–July 2024 is 61 consecutive days with no rows at all — a data gap, not
  necessarily zero demand — and 88% of Sundays in the window are zero while only 4 Sundays in the
  entire 1,461-day calendar are flagged closed (the flag covers special closures, not the weekly
  schedule). Together **169 of 821 days (21%)** are plausibly not trading days yet are modelled as
  customers wanting nothing. Excluding them raises mean per-SKU density from 7.3% to 8.8%. Not
  re-run through the benchmark, and it should not be until the June–July 2024 gap is confirmed with
  the client: a data gap and a true zero need opposite treatment.

**Two smaller data-quality findings.** Fifteen tally dates fall on flagged store closures, and the
month-day pairs repeat annually (01-09, 02-25, 04-09, 06-12, 06-24 in both 2025 and 2026) matching
Philippine public holidays. Thirteen of the fifteen sold nothing, which is what a genuine closure
looks like; the real residue is two dates that genuinely traded on a flagged closure — 143 units,
0.16% of volume. The flag is broadly sound. Separately, 71 of 519 products fold a price into the item
name, 12 have a de-priced twin across eight base families, and four of those families have a bare row
carrying real sales, so merging them would move units between SKUs and change the FSN split. Neither
is changed by this analysis — the controlled vocabulary is not modified — and both are recorded for
a staff ruling.

---

## 5. Section 4.3 — Dashboard

### 5.1 The delivered application

A two-tier presentation layer over the same database the pipeline builds: a React and Vite front end
with **seven screens**, served by a Flask and SQLite JSON API of **thirty-three endpoints**. Nothing
in the application invents a number. Every screen reads and writes through a single data-access
module, so no screen knows the API's URL shape, and per-product statistics (ADUS, stock position, FSN
sensitivity) are recomputed from the database on each request rather than served from a cached
fixture.

**Table 19. Application screens and their state.**

| Screen | What it shows | State |
| --- | --- | --- |
| Dashboard Overview | Units over time, category mix, top products, FSN split, reorder-now count | Real |
| FSN Classification | ADUS, HVL flag, and the 75th/80th/85th percentile sensitivity table | Real |
| Demand Forecast | 30-day forecast with band, plus observed history and per-SKU accuracy | Real for the 58 Fast SKUs; pending card otherwise |
| Reorder Alerts | Stock position, ROP, safety stock and EOQ under both ordering-cost scenarios | Real, every figure labelled provisional |
| Batch Sales Report | Per-supplier quantities and remittance line totals, with PDF / CSV / XLSX export | Real |
| Digital Tallying Interface | Entry with validation, closure toggle, event flagging, monthly inventory counts, pipeline run | Real; every write is a database row |
| Analytics (Power BI) | Embedded published report | Placeholder until a report URL is configured |

**The tallying interface** is the digital counterpart of the paper tally sheet and the one place the
system accumulates new data rather than reading history. Every write is validated server-side
independently of the client check. Its **Monthly Inventory Count** card exists specifically to chip
away at the coverage gap: one row per product per month, a recount replacing rather than adding to
the earlier figure, and zero accepted as a valid count because it records that an item is out. Counts
feed the live API — a product the workbook never covered raised stock coverage from 82 to 83 products
in testing, and every catalog row reports whether its figure came from the workbook or a staff count
— but they do **not** yet feed the ETL, so a newly counted product shows a current stock and still a
null days-of-supply. Wiring them in would change the censoring evidence behind the classification and
is left as a team decision rather than done silently.

**The interface also drives the pipeline.** A Full Pipeline Run card runs the whole chain as a
background job — about 40 seconds without the forecast step, 50 with it — streaming each step's
output line by line, with a per-step wall-clock timeout and a Stop control that kills the process
tree rather than orphaning children. A staleness banner compares the last completed run's high-water
marks against the database and warns in three situations: records written since the last run; no
completed run at all with such records present; or a most-recent run that stopped or failed part-way,
leaving the result tables half-rebuilt. The reason it matters is specific — everything the interface
writes lands in the database immediately, but the FSN classes, forecasts and prescriptive results are
only recomputed by a run, so without the banner the Reorder screen would show reorder points
predating a fortnight of tallying with nothing on screen saying so.

### 5.2 The Power BI layer

Phase 6 specifies Power BI as a read-only presentation layer, on the principle that no model,
forecast or EOQ is ever computed inside the report — everything is computed in Python and written to
result tables first. The embed route is built and configured through a single environment key; the
report file itself has not been authored.

**Table 20. The five specified views and their readiness.**

| # | View | Primary source | Status |
| --- | --- | --- | --- |
| 1 | Stock Status | Fact_Sales (days_of_supply, is_censored) | Blocked — only 62 of 519 products have any stock reading; the coverage figure must be on the page, not behind it |
| 2 | FSN Classification | Dim_Product (fsn_class, is_hvl) | Buildable now |
| 3 | Demand Forecast | Result_Forecast, Result_Forecast_Metrics | Unblocked — 1,740 and 174 rows exist; the forecast line is flat by construction and should be shown as such |
| 4 | Restocking Advisory | Result_Prescriptive, Dim_Parameters | Buildable now, with both ordering-cost scenarios side by side, never collapsed to one |
| 5 | Batch Sales Report | Fact_Sales × Dim_Product | Buildable now |
| — | Calendar interpretation cards | Dim_Date flags: 34 enrollment, 173 exam-week, 54 event, 185 break, 43 closed days | Buildable now |

### 5.3 What the dashboard deliberately does not show

Three omissions that are design decisions, and belong in the record as such:

- **No fabricated forecast.** A screen shows a pending card whenever the pipeline has not produced
  that output, rather than a plausible-looking chart. The same rule applies to the Power BI build: a
  placeholder chart styled to look like a forecast would be less honest than an empty page with a
  one-line caption, because a number shown on a dashboard ends up quoted in a chapter.
- **No peso grand total on the batch report.** Totals are unit counts. Twenty-two of the 519 products
  carry no unit price, so a peso total would silently undercount — and, the binding reason, this is
  an internal counting document under the BIR constraint, not an invoice. Unit price appears as
  supplier-remittance reference data only. The PDF renders through a pure-Python library with no
  system dependencies, in Latin-1 by deliberate choice, so money prints as "PHP 1,234.00" rather than
  using the peso sign.
- **No store-wide stock percentage.** Any "% in stock" computed over the covered 14–17% of units
  would represent the other 86% as either fine or absent, and neither is true — it is simply
  unmeasured. The covered subset is shown, labelled as the covered subset.

---

## 6. Section 4.4 — Summary of Findings

### 6.1 Objectives vs outcomes

**The objective descriptors below are paraphrases** reconstructed from the build plan and remediation
register, which reference the objectives by number and subject but do not reproduce their text.
Replace each with §1.3's exact wording; the outcomes are measured and do not change.

**Table 21. Objectives and outcomes.**

| Objective | Outcome |
| --- | --- |
| Consolidate the tally workbooks into a single integrated dataset | Met. 84,399 sales records, 89,232 units, 519 canonical items, 19 suppliers, zero transformation exceptions |
| Classify products Fast / Slow / Non-moving with a sensitivity check | Met. 58 F (6 HVL) / 228 S / 233 N at the 80th percentile, re-run at the 75th and 85th |
| Forecast demand for the fast-moving items | Met in substance, not against the stated criterion. 58 of 58 Fast SKUs scored on 12 walk-forward folds; 2.6× better than persistence; MAPE ≤ 20% cleared by 1 of 58, and shown to be the wrong criterion. Prophet does not appear — superseded by a benchmarked rolling mean, which is a reported result rather than a gap |
| Compute reorder point, safety stock and EOQ | Met, provisionally. 208 SKUs priced under two ordering-cost scenarios from measured lead time and derived holding cost; every figure flagged pending the site visit |
| Deliver a dashboard and an automated batch sales report | Partially met. Seven working screens over the live database, with PDF / CSV / XLSX export; the Power BI report file is specified but unbuilt, and one of its five views is blocked on inventory coverage |

### 6.2 Principal findings

1. **An acceptance criterion defined purely on forecast error is structurally invalid for
   intermittent demand.** Its optimum is a forecast of zero. Demonstrated by identity on this
   project's own data and measured in Table 8: the best of the ten committed statistical baselines on
   MASE prices 0 of 266 SKUs, and the one method that tops the metric without being degenerate does
   so by changing the shape of the forecast, not the threshold. Ten percent more forecast error buys
   the difference between 0 usable SKUs and 266.
2. **The obvious replacement — a fixed 95% service level — is unreachable by 0.10 percentage points
   before any modelling choice is made.** 584 folds spanning 103 SKUs have flat-zero training slices,
   making 2,732 units (5.1% of scored demand) structurally unservable. The ceiling on any method is
   0.9490.
3. **What replaces both is a frontier with a measured operating point.** The marginal cost of service
   roughly doubles above q ≈ 0.80, locating a knee that is a property of the demand rather than a
   number chosen in advance. This delivers §1.2's own promise of an EOQ model subject to a cycle
   service-level constraint, stated in Chapter 1 and never operationalised.
4. **At that operating point the production model dominates.** rolling_mean_30 achieves higher
   service *and* less stock than both ets and tsb at q = 0.80, removing the accuracy-versus-usability
   trade-off the model-selection decision was deadlocked on.
5. **The binding constraint is rate instability, not sparsity.** Three independent diagnostics —
   accuracy by density, aggregation of real data, controlled simulation — converge on it. None of
   coarser aggregation, five extra years of history, or academic-calendar features moved the metric,
   and each failed for a reason the mechanism predicts.
6. **Pooling helps, but only on a behavioural basis.** Product taxonomy fails; K-means on demand-shape
   features beats every manual grouping and beats per-SKU modelling on every method tested. The
   deeper result is that a temporally stable feature is a more robust pooling basis than a hard
   percentile cutoff — established by which of the two survived a leakage correction that reversed
   the other.
7. **Two partition leaks were found by audit, closed, and the affected results re-run.** One reversed
   a published finding outright. The mechanism is now pinned by tests rather than by a comment, which
   is the relevant lesson: the original audit was skipped because three docstrings asserted the label
   was not derived, when the project's own ETL derives it.
8. **The prescriptive layer is arithmetically sound and economically unresolved.** EOQ swings exactly
   12.65× between two readings of a single USTore figure, and exceeds a year of demand under both.
   The system therefore recommends an order-up-to level built only from measured inputs, and shows
   EOQ under both interpretations rather than choosing one.

### 6.3 Limitations

| Limitation | Detail |
| --- | --- |
| Observation window | Roughly twenty-six usable months, not the three years stated in §1.4.1 — a property of the records as supplied, not a processing loss. Any seasonal claim rests on at most two observations of each academic-year phase |
| Imputation | 17.9% of sales rows are allocated from price-grouped entries; 11.6% of those have no stock backing at all and a further 39.7% are weighted against a count up to twenty months stale. The single 0.5 coefficient does not distinguish these tiers |
| Inventory coverage | 16.8% of rows carry any stock signal; 62 of 519 products have a derivable days-of-supply. Limits the censoring evidence, the Stock Status view, and the validation of reorder points against beginning counts that Objective 4 anticipates |
| Trading-day calendar | 21% of modelled days are plausibly not trading days, including a 61-day gap in June–July 2024, currently counted as zero demand. Every density figure sits on that calendar |
| No untouched holdout | The 33 method-and-variant combinations in the robust-metric comparison alone, plus the frontier's operating point, were compared on the same folds they are reported on |
| One residual leak on one path | The statistical benchmark's fill-rate column still selects its safety-stock z from a full-history class label. The frontier does not, which is why model selection rests on the frontier |
| Provisional cost inputs | Lead time, holding cost and both ordering-cost readings are estimates pending the USTore site visit. The holding cost is a single blended rate across the catalogue |
| Break-scope metrics | At 30-day aggregation no window on this calendar is majority-break, so the standard-period versus semestral-break split yields exactly one break-exposed fold per SKU. Indicative, not a peer of the twelve-fold overall result |

### 6.4 Decisions this chapter does not make

Each closure belongs to a person rather than to a script:

- **The acceptance criterion** needs adviser sign-off on reporting a frontier with a recommended
  operating point in place of a second threshold.
- **Model selection** follows that decision, and the frontier is the currency it should be settled in.
- **Lead time, holding cost and ordering cost** need the USTore site visit.
- **A client or staff ruling** is needed on the June–July 2024 gap, the two dates that traded on a
  flagged closure, the May 2024 source-sheet discrepancy of 296 units, and the four price-suffix
  families carrying real sales.
- The controlled vocabulary, allocation groups, supplier mapping and calendar ranges are not modified
  by this analysis in any case.

---

## 7. Numbers that must not drift

Pinned by verification scripts and tests. If any of these move, something upstream changed.

| Figure | Value |
| --- | --- |
| Fact_Sales rows | 84,399 (68,541 zero + 15,858 positive) |
| Total units sold | 89,232 |
| Zero-row share | 81.2% |
| Products | 519 — F 58 / S 228 / N 233, 6 HVL |
| Moving SKUs benchmarked | 266 |
| Folds per method | 3,192 (266 × 12) |
| Total scored demand | 53,573 units |
| Structurally unservable | 2,732 units, 584 folds, 103 SKUs |
| Fill-rate ceiling | 0.9490 |
| Frontier knee | q ≈ 0.80, fill 0.742 |
| EOQ demand-basis count | 208 of 266 |
| Result_Forecast | 1,740 rows (58 × 30) |
| Result_Forecast_Metrics | 174 rows (58 × 3) |
| Result_Prescriptive | 416 rows (208 × 2) |
| Cost-of-usefulness gap | 10.2% MASE for 0 → 266 SKUs priced |

---

## 8. Things to flag, not fix

1. **416, not 411** sale-recorded dates (§3.1). Flag the correction rather than silently using either.
2. **The residual z-leak** on the statistical-benchmark fill-rate column (§4.6). Fixing it means
   re-running the benchmark, which will move committed numbers. Until then, route model selection
   through the frontier, which is unaffected.
3. **Objective wording** in Table 21 is paraphrased — replace with §1.3's own text.
4. **Everything in Tables 13 and 14** is provisional pending the site visit, and must be labelled so
   in the sentence it appears in, not only in a footnote.

---

## 9. Where every figure came from

| Section | Artifact |
| --- | --- |
| Tables 5–7 | ustore.db; data/allocation_audit.csv; scripts/verify_data.py, tools/assert_invariants.py |
| Table 8 | data/model_benchmark_summary.csv, data/model_benchmark_ml_summary.csv, data/model_benchmark_category_summary_category_speed.csv, data/robust_metric_comparison.csv |
| Table 9 | data/model_benchmark_summary.csv |
| Tables 10–11 | tools/service_frontier.py, pinned by tests/test_service_frontier.py |
| Table 12 | Result_Forecast, Result_Forecast_Metrics; docs/ROLLING_MEAN_FORECAST.md |
| Tables 13–14 | Dim_Parameters, Result_Prescriptive; scripts/step5_prescriptive.py, scripts/step5a_set_lead_times.py |
| Tables 15–18 | docs/SPARSE_DEMAND_EXPERIMENTS.md, docs/FORECAST_METHOD_COMPARISON.md, docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md, data/model_benchmark_category_*.csv, data/density_vs_accuracy.csv, data/aggregation_density.csv, data/sparsity_sensitivity_sim.csv |
| §4.1 degeneracy | docs/DEGENERATE_FORECAST.md, pinned by tests/test_degenerate_forecast.py |
| §4.2–4.3 service level | docs/SERVICE_LEVEL_FRONTIER.md (Divergence #22) |
| §4.6 leakage audit | FORECAST_EXPERIMENT_AUDIT.md; fix in forecasting/category.py, pinned by tests/test_category_leakage.py |
| §4.8 provenance | docs/DATA_PROVENANCE.md, docs/DIVERGENCE_REGISTER.md, docs/REMEDIATION_WAVE1_STATUS.md |
| Tables 19–20 | backend/app.py, UST Prototype Design/src/, docs/POWERBI_DASHBOARD_PLAN.md |
