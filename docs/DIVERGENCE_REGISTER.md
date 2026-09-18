# Divergence Register — for Chapter 4

Every place the system departs from Chapters 1–3. **Nothing here is a manuscript edit.** This is
what the limitations and results discussion get written from.

Promoted out of `CODE_WORK_PLAN_v2.md` into its own tracked file on 2026-08-04, on branch `tyrone`.
Rows 1–17 are carried over from there; **1, 16 and 17 are corrected** and **18–20 are new**, all
with the evidence named. Corrections are marked ⚠ and the superseded text is kept, struck through,
so nothing silently changes meaning between versions.

**2026-08-19 update (`REMEDIATION_MASTER_v2.md`):** row **6** rewritten (the service-level
replacement it named is itself unreachable — see #22); row **16** corrected a second time (marked
⚠⚠ — the closure flag is sound, the 15-date count was measuring something else); rows **22–23** are
new.

**2026-09-09 update (category-grain refactor):** rows **24–25** are new. Both come out of one
question — *is the per-SKU grain itself the constraint?* — and the answer is recorded below under
"#24 — the grain, measured both ways". Nothing in the production pipeline changed as a result; that
is the finding, not an omission.

| # | Chapter 3 says | System does | Where to explain |
|---|---|---|---|
| 1 ⚠ | Data scope 2023–2026 (§1.4.1, §3.1.1, Table 2) | Sales 2024-05-02 → **2026-07-31** (~~2026-06-30~~); inventory 2024-11-01 → 2026-04-01; 2023 = 6 undated batch aggregates, 1 of 34 labels matching a current SKU | Ch4 data description |
| 2 | No artificial daily interpolation of zero-sale values (§3.3.2); historical zeros treated as missing (§3.1.2) | Zero-filled to ~84,000 rows | Ch4 — **argue this**, it's plausibly why the regressors now work (Block 2.5) |
| 3 | Sufficiency tiers by observation count (§3.3.2, §3.3.4) | Counted on distinct non-zero sale dates — consequence of #2 | Ch4 method note |
| 4 | `is_suspension_day` (Figure 5) | `is_store_closed` — matches all prose and the DDL | Ch4 footnote |
| 5 | `fsn_class ∈ {F,S,N}` (§3.2, Figure 5) | Added `is_hvl` boolean; HVL is a modifier on F per §3.3.1 | Ch4 schema note |
| 6 ⚠ | MAPE ≤20% primary acceptance criterion (8 locations) | Unreachable — perfect-forecast floor ~89% daily / ~60% monthly. ~~Report service level ≥95% + MASE <1.0~~ **That replacement is ALSO unreachable — see #22 — by 0.10pp before any modelling choice is made. Report a service/holding-cost frontier with a recommended operating point at the knee (q≈0.80–0.85), not a second threshold.** | Ch4 — **the headline result** |
| 7 | Forecast units on tally dates | 30-day aggregate demand per SKU (what ROP/EOQ and the billing cycle need) | Ch4 method |
| 8 | `semester_week` as a Prophet regressor (§1.2, §3.3.2) | Dropped, or cyclically re-encoded — continuous form extrapolates badly across resets | Ch4 model spec |
| 9 | Prophet outperforms ARIMA on seasonal series (§2.1.4) | Rolling median beat all 8 methods (MAE 2.24, MASE 0.61); XGBoost 7th | Ch4 benchmark |
| 10 | Prophet applied blanket to F/HVL (§3.3.2); §2.1.4 lists no intermittent-demand methods | Option C two-track: flatlog for F/HVL, Croston/SBA for S | Ch4 model selection |
| 11 | Inventory omitted because stock is derived (§3.2); ROP validated against beginning counts (Obj 4) | 13.7% coverage — derivable for 42 of 273 selling items | Ch4 data quality + limitations |
| 12 | Walk-forward validation (§3.3.4, Figure 3) | Implement genuine rolling origins (Block 4.4) — or explain the holdout | Ch4 validation protocol |
| 13 | — | `semester_week` week-1 origin: enrollment vs first class day (Block 1.4) | Ch4 method note |
| 14 | Holding cost includes opportunity cost of capital tied up in stock (§3.1.1) | University doesn't own consignment stock — reframe `H` or present EOQ as order-batching | Ch4 EOQ discussion |
| 15 | — | Nearest-month allocation weights, some ±20 months stale; 312 of 2,852 splits used an equal split with no stock backing | Ch4 imputation limitations |
| 16 ⚠⚠ | Closure flags feed the depletion denominator | ~~15 tally dates fall on flagged closures (two: 2025-06-12, 2025-11-30) — pending Block 5~~ **Corrected (remediation S3): `is_store_closed` is broadly sound, not backwards. 13 of the 15 flagged-closed tally dates sold NOTHING (0 units) — exactly what a genuine closure looks like. The 15-date count is mostly an artifact of `is_tally_date`'s zero-inclusive definition (a closed day gets `is_tally_date=1` merely because zero-fill wrote a row for it), not evidence against the flag. The real residue is just 2 dates that genuinely traded on a flagged closure — 2025-06-12, 2025-11-30 — 143 units, 0.16% of volume. Do not split the flag; take those 2 dates to USTore.** | Ch4 data quality |
| 17 ⚠ | Benchmark tier counts | ~~`PROJECT_LOG` says 89 SKUs in the ≥60 tier; tier counts elsewhere are 87/56/162 = 305. Reconcile before publishing either~~ **Not a conflict: three different populations. Closed — see below.** | Ch4 method note |
| **18** | — | `SUM(quantity_sold)` 88,481 → **89,232**: May 2024 re-sourced DSR→TBS (−296), July 2026 added (+1,047) | Ch4 data description |
| **19** | — | 71 of 519 products carry a price suffix; 12 have a de-priced twin. `Lanyard @180` is the largest SKU at 7,201 units | Ch4 data quality |
| **20** | — | CSV line-ending split made the repo non-reproducible across platforms; `USTore_Build_Plan.pdf` was corrupted on every Windows checkout | Ch4 reproducibility note |
| **21** | §3.3.4's error-threshold acceptance criterion | Not merely unreachable on this data — **degenerate**. The optimum of the criterion is a forecast of zero: the MASE-minimising method prices 0 of 266 SKUs | Ch4 results **and** limitations |
| **22** | §1.2's promised "EOQ-based optimization model ... subject to a cycle service level constraint" (never delivered as a constraint, only as a fixed target) | #21's replacement (service level ≥95%) checked against the data and found ALSO unreachable, for three separable reasons: (a) a defect in the benchmark's own risk-period formula, now fixed — remediation D1; (b) a hard arithmetic ceiling of 0.9490 — 584 folds / 103 SKUs have flat-zero training slices, 5.1% of demand structurally unservable before any model runs; (c) normal quantiles under-size the buffer on an 81%-zero series. The service/holding-cost frontier this produces (`tools/service_frontier.py`) **is** the constrained optimisation §1.2 already promised | Ch4 — **delivers §1.2's own promise** |
| **23** | Store Closure/Suspension toggle updates `Dim_Date.is_store_closed` directly (§3.1.1) | Writes to a new `Closure_Log` table (mirrors `Event_Log`'s own pattern) and updates `Dim_Date` immediately; `populate_dim_date.py` reads `Closure_Log` back after a rebuild. Remediation D3: a bare `Dim_Date` write did not survive `populate_dim_date.py`'s DELETE+re-INSERT, silently erasing every staff-set closure on the next rebuild. Applies the manuscript's own `Event_Log` durability pattern to closures for the identical reason — found while fixing this, `Event_Log`'s own flag had the same unexercised gap (never noticed because `Event_Log` has stayed empty) and is fixed the same way | Ch4 — interface durability |
| **24** | Objective 3 and §3.3.2 forecast **per SKU**; the grain is never questioned | A **category** grain was built and measured (`step1b_categorize_products.py`, `benchmark_category_level.py`). At category level pooled WMAPE is 49.3% against 74.8% per SKU. That gain is **arithmetic, not modelling**: RM6 is linear, so the category forecast is bit-identical to summing the per-SKU forecasts the existing pipeline already produces (0.00pp gap, verified). And it does **not** transfer downward either — disaggregating back to SKU by trailing-90d share scores 77.6% against per-SKU's 74.8% (`compare_topdown_vs_persku.py`, identical folds). **Production stays per-SKU, unchanged.** Ch4 should report the category result as evidence about the *data* — signal exists in aggregate, effectively none per item — and never as a model that ships | Ch4 — **the aggregation result**, and limitations |
| **25** | §2.1.4 positions ARIMA as a principal comparator, and §1.2/§3.3.2 select Prophet | Neither was ever scorable per SKU — §2.1.4's own precondition (a near-continuous series, 50–100 clean observations) is not met by a ~90% zero series. Once aggregated they become viable and were scored: SARIMA(1,1,1)(1,0,1,7) placed **2nd of 27** (WMAPE 53.2%), ARIMA(2,1,2) 4th (54.9%), Prophet 5th (56.8%) — all beaten by a 180-day trailing mean (49.3%). The manuscript's preferred model classes were not wrong so much as **untestable at the grain the manuscript chose** | Ch4 benchmark — completes #9 |

---

## The corrections, with evidence

### ⚠ #1 — the sales series ends 2026-07-31, not 2026-06-30

The zero-fill rebuild picked up a July 2026 sheet the old combined CSV predated. Asserted in
`tools/assert_invariants.py` ("sales date span") and reproduced in `tools/provenance_may2024.py`.

### ⚠ #16 — 15 closed-day tallies, not 2

The original entry named two dates. The rebuild finds **15** tally dates falling on a flagged
closure:

```
2025-01-09  2025-02-25  2025-04-09  2025-06-12  2025-06-24
2025-08-08  2025-08-21  2025-08-25  2025-11-30  2026-01-09
2026-02-17  2026-02-25  2026-04-09  2026-06-12  2026-06-24
```

Verify with:

```sql
SELECT calendar_date FROM Dim_Date
WHERE is_tally_date = 1 AND is_store_closed = 1
ORDER BY calendar_date;
```

**The month-day pairs repeat annually** — `01-09`, `02-25`, `04-09`, `06-12`, `06-24` each appear in
both 2025 and 2026. Those are Philippine public holidays (EDSA anniversary, Araw ng Kagitingan,
Independence Day). The store appears to trade on them anyway.

**Hypothesis, recorded and NOT acted on:** `is_store_closed` may encode "holiday", not "closed". If
so it is the wrong denominator for depletion-rate calculations, and the flag is being read backwards
wherever it gates a computation. This is deferred decision **B8** and needs a USTore staff answer.
Nothing in this branch changes the flag or anything downstream of it.

### ⚠ #17 — three populations, never a conflict

Closed by `tools/tier_counts.py`, which asserts both populations on Block 2.1's corrected
definition (distinct calendar dates with `quantity_sold > 0`, matching `step4`'s `obs_counts`):

| Population | ≥60 | 30–59 | <30 | Total |
|---|---:|---:|---:|---:|
| All moving SKUs (>0 units) | 92 | 51 | 123 | **266** |
| Fast SKUs only — what `step4` routes | 38 | 10 | 10 | **58** |

The three circulating figures each described a different population and each was right about its
own: `87/56/162 = 305` was pre-canonicalisation and pre-zero-fill on all moving SKUs; the
`PROJECT_LOG`'s `89` is ≈ the current 92 in the ≥60 tier, also all moving SKUs; the README's
`38/10/10 = 58` counts Fast SKUs only. 58 is not a subset of a subset — it is the entire population
`step4_forecast_model.py` iterates over, which is why it looked incompatible with the other two.

The register marked this "fix, don't explain". There was no arithmetic to fix; the fix was to say
which population each number counts, and that is now asserted rather than asserted-in-prose.

---

## The new rows

### #18 — 88,481 → 89,232

Fully reproduced by `tools/provenance_may2024.py`; narrative in `DATA_PROVENANCE.md`. The delta
decomposes into exactly two months and no others:

| Month | Old file | Zero-filled | Δ |
|---|---:|---:|---:|
| 2024-05 | 4,318 | 4,022 | **−296** |
| 2026-07 | 0 | 1,047 | **+1,047** |
| **Net** | | | **+751** |

2026-07 is a new month. **2024-05 is a source-sheet change, not new data** — both files cover the
same 23 tally dates. The decisive evidence: the May 2024 DSR sheets' three price channels
(retail 3,719 + discounted 551 + special 48) sum to **4,318**, exactly the old CSV's May total. The
old series *was* the DSR sheets. The TBS sheet, covering the same dates, totals 4,022.

The manuscript's 88,481 was computed on a **mixed-provenance** series — May 2024 from daily DSR
sheets, every other month from TBS tally sheets. 89,232 is the single-provenance figure.

`verify_data.py` now fails if any third month moves.

The 296-unit gap itself is **not resolved** — it is deferred decision **B7**.

### #19 — price-suffixed SKUs

Measured by `tools/audit_price_suffix_skus.py`; detail in `PRICE_SUFFIX_AUDIT.md`. 71 of 519
`Dim_Product` rows fold a price into the item name; 12 have a de-priced twin, across 8 base
families.

The audit's useful finding is that those 8 families are **two different problems**:

- **4 families** (`Arch`, `Keychain`, `Lanyard`, `Long Sticker`) have a bare row with **0 units and
  class N** — a vocabulary artifact. Merging moves nothing.
- **4 families** (`Eco Bag`, `ID Case`, `New Tiger Plushie Big`, `New Tiger Plushie Small`) have bare
  rows carrying **real sales**. Merging these *would* move units between SKUs and change the FSN
  split. Only these need a staff ruling.

`Lanyard @180` is the largest single SKU in the dataset at 7,201 units and Fast-classified, while a
bare `Lanyard` row exists at 0 units and class N. Merge ruling is **B6**.

### #20 — reproducibility

The repo had two CSV writers with different line-ending conventions and no `.gitattributes`:
`step1_apply_mapping.py` writes LF via pandas, `proportional_allocation.py` wrote CRLF via
`csv.writer`'s RFC 4180 default. The committed files are LF only because they were committed from
Windows with `core.autocrlf=true`. Anywhere else, re-running the pipeline marked
`USTore_sales_long_allocated.csv` and `allocation_audit.csv` fully modified — **199,036 lines of
diff, content bit-for-bit identical**.

Fixed by `.gitattributes` plus `lineterminator="\n"` on both `csv.writer` calls.

**The same gap had already corrupted a file.** Without `.gitattributes`, git classified
`USTore_Build_Plan.pdf` as text, so `autocrlf` inflated it 13,058 → 13,220 bytes on every Windows
checkout. That shifted every absolute byte offset in its xref table: `startxref 12442` no longer
landed on `xref`. The file is now marked binary and restored byte-exact. The five `.xlsx` workbooks
were auto-detected as binary and were never affected.

Chapter 4 should note this as a reproducibility limitation that was found and closed, not as an
open one.

### #21 — the acceptance criterion is degenerate, not just strict

This is the strongest result the project has, and it changes the shape of the argument in Chapter 4.

Full detail and the identity chain are in `DEGENERATE_FORECAST.md`; pinned by
`tests/test_degenerate_forecast.py`. In short:

1. MAE is minimised by the conditional median of the predictive distribution.
2. MASE is MAE divided by a scale that does not depend on the forecast, so it has the **same**
   minimiser.
3. 68,541 of 84,399 `Fact_Sales` rows are zero, so on this series the median **is** zero.

Therefore any selection rule that minimises MASE converges, by construction, on the forecast
"nothing will sell". Measured instance: the rolling 30-day median leads the eight-method benchmark
on MASE **and prices 0 of 266 SKUs**, because an annualised demand of zero yields no EOQ.

The distinction that matters for the write-up: divergence #6 says the ≤20% MAPE bar cannot be
cleared, which reads as asking for the bar to be lowered. #21 says something different and stronger —
**an error-minimising acceptance criterion is structurally invalid for intermittent demand**, and we
can demonstrate it on our own data rather than citing it. That is a contribution, not a concession.

Stated as a demonstration on this dataset. The recommendation is **not** drawn here; that is B2 and
B3.

---

## Also worth recording

These came out of the `tyrone` run and are not manuscript divergences, but they change how results
should be read.

**The accuracy/actionability split.** The rolling 30-day median leads the seven-method benchmark on
MASE and is *unusable* as the demand input for EOQ: on an intermittent daily series the trailing
median is zero, so it prices 0 of 266 SKUs. The method that minimises forecast error predicts
"nothing will sell" — nearly right day-to-day, useless for deciding how much to order. Any model
selection made on MASE alone (**B3**) has to confront this.

**Croston's blind spot.** Croston and SBA place last in the benchmark for a structural reason, not a
tuning failure: they update only on periods when demand *arrives*, so trailing zeros are invisible
and an SKU that stops selling keeps forecasting its old rate forever. Pinned by
`test_croston_cannot_see_trailing_zeros`. The known remedy is an obsolescence-aware variant (TSB),
which is not implemented.

**The demand basis lands in a break.** An annualised *30-day* forecast anchors on 2026-07, inside the
AY2526 summer term. 79 of 266 SKUs get a positive D at 30 days, against 141 at 90, 163 at 180 and
208 at 365. Whether to widen the window is a Block 5 decision.

---

## #24 — the grain, measured both ways

`docs/FAST_MOVING_BENCHMARK.md` closes with 37 methods across 10 families landing within 10% of each
other, and the best MAE (40.02) sitting 2.0% above the arithmetic floor for *any* flat forecast. That
is not a modelling failure with a modelling fix; it says the per-SKU series carries almost no
learnable signal. So the remaining lever is the grain itself.

**Step 1 — build the grain.** `scripts/step1b_categorize_products.py` derives
`Dim_Product.forecast_category` from the item name (11 keyword rules, first match wins). It adds a
**new column** rather than overwriting `category`, which `step1_apply_mapping.py` writes and
`step5a_set_lead_times.py` / `step5_prescriptive.py` read — an overwrite would silently retier lead
times and be reverted on the next ETL pass. 12 categories; 65 of 519 products (12.5%, 2.2% of units)
fall to `Uncategorised`, of which **1** is Fast.

**Step 2 — score it.** `scripts/benchmark_category_level.py`, 27 methods, same harness as the SKU
benchmark (H=30, folds 3–12, min_train 60), identical-folds gate passed:

| level | best method | MAE | RMSE | MAPE % | pooled WMAPE % |
|---|---|---:|---:|---:|---:|
| category | RM6_6month_180d | 183.42 | 233.86 | 131.2 | **49.30** |
| category | sarima_weekly | 197.98 | 244.68 | 124.9 | 53.22 |
| category | prophet_plain | 211.29 | 274.38 | 142.1 | 56.79 |
| SKU | rolling_mean_30 | 40.02 | 89.48 | undefined on 52% of folds | **74.82** |

MAE is **not** comparable across levels (a category sells more, so its absolute error is larger by
construction); WMAPE is, and it moves 74.8% → 49.3%. `zero_targets` goes 361 → **0**, so MAPE is
defined on every fold for the first time in this project.

**Step 3 — check it transfers.** It does not. `scripts/compare_topdown_vs_persku.py` scores both arms
at SKU level on identical folds (58 Fast SKUs × 12 origins = 696 pairs), allocating the category
total by trailing-90d share computed strictly before each origin:

| arm | method | MAE | RMSE | WMAPE % | bias % | zero forecasts |
|---|---|---:|---:|---:|---:|---:|
| per-SKU | rolling_mean_30 | **40.02** | 89.48 | **74.8** | +3.1 | 363 |
| top-down | RM6_180 | 41.52 | **87.04** | 77.6 | **−0.8** | 318 |
| per-SKU | tsb | 42.37 | 99.72 | 79.2 | +1.8 | 223 |
| top-down | rolling_mean_30 | 42.43 | 90.27 | 79.3 | +3.1 | 318 |

Top-down is 3.7% worse on WMAPE. **Production stays per-SKU.**

**Three qualifications, because the headline understates the picture.**

1. *The gap is not significant.* Paired over the 696 rows, top-down wins 161, loses 217, ties 318
   (the ties are rows where both arms forecast into a zero actual). Wilcoxon signed-rank **p =
   0.054** — this is a tie that fell the per-SKU way, not a defeat.
2. *Top-down is better where it costs most.* On the worst decile of per-SKU errors, top-down averages
   **178.1 units against 231.1** — 23% better on exactly the misses that cause stockouts. It also
   carries near-zero pooled bias (−0.8% vs +3.1%) and lower RMSE. A criterion weighted to tail risk
   rather than mean error would select the other arm.
3. *The effect is category-dependent, and partly a window effect.* Top-down gains in `Bags` (+6.3pp)
   and `Lanyards & IDs` (+5.1pp) — large, multi-SKU, concentrated-share categories — and loses badly
   in `Outerwear` (−18.6pp) and `Uncategorised` (−9.6pp), both of which contain a **single** Fast SKU,
   where "top-down" is just the same series under a 180-day window instead of 30. Those two rows
   measure the window, not the grain.

**Step 4 — check the gain is a gain at all. It is not.** A rolling mean is linear, so
`mean(Σ series) = Σ mean(series)`: the RM6 category forecast is *arithmetically identical* to
forecasting each Fast SKU with RM6 and adding the results. Scored on identical folds against
identical actuals, direct and bottom-up agree to the bit — RM6_180 53.33% both ways,
rolling_mean_30 57.68% both ways, ewma_a0.1 64.13% both ways. The 74.8% → 49.3% improvement is
therefore **not a better model**; it is the same model scored on a sum rather than on its parts, and
`step4_forecast_model.py` already produces every number needed to report it. The identity breaks
only for non-linear methods (`croston` is 29pp better fitted directly, `tsb` 2pp worse), and neither
beats RM6. **Chapter 4 must not present the category WMAPE as a modelling result** — it is a change
of denominator, and the honest claim is about the data: this catalogue carries forecastable signal in
aggregate and effectively none per item.

**A defect this exposed, worth its own note.** The first cut forecast the *all-SKU* category total and
allocated it only to Fast members — crediting every Fast SKU with its category's Slow and Non-moving
demand (in `Outerwear`, 89% of the units). The conservation assertion in `step4b_category_forecast.py`
caught the mirror-image half of it: two categories had a forecast and no Fast member to receive it,
so 65 units/30d vanished. Fixed by forecasting a Fast-only series
(`data/category_daily_series_fast.csv`) whenever the allocation targets Fast SKUs — the forecast
population must match the allocation population.

**Schema note.** Category output lives in `Result_Forecast_Category` and
`Result_Forecast_Category_SKU`, **not** in `Result_Forecast`. Tagging two grains in one table with a
`forecast_level` column was tried and reverted: six readers aggregate that table with a bare
`SUM(yhat)` — `step5_prescriptive.py::load_forecast_totals` and four queries in `backend/app.py` —
so a second grain silently inflates every reorder point (647 → 2,804 units on the first run).
Separate tables make that unrepresentable, and also stop `step4_forecast_model.py`'s unconditional
`DELETE FROM Result_Forecast` from wiping step4b's output on every run.
