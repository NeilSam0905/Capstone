# Forecast testing summary and review notes (as of 2026-09-30, branch `neil`)

Everything the project has tested on forecasting, collected in one place for review after the
merge: what the app runs now, how it is tested, every experiment by theme with its result and where
it is documented, what changed in the latest session, and what still needs a group decision.

Numbers are quoted from the docs and result files named in each row. Where a later test corrected
an earlier one, the correction is given.

**TL;DR**

- **The app runs two simple models.** Per category: 6-month average, lowered for semester breaks and
  exams (off by **42.1%** of units sold). Per item (58 Fast items): 50/50 blend of category share and
  TSB, same calendar rule (off by **60.1%**). Prophet only spreads each 30-day total over the days.
- **Over 100 methods and variants were tested** at item, category and whole-segment level: trailing
  averages, smoothing, Croston/SBA/TSB, hurdle models, ETS, ARIMA/SARIMA, Prophet (many settings),
  six ML learners (per item, pooled, clustered), transformations and blends. Simple trailing averages
  and blends win every fair comparison.
- **The ceiling is in the data, not the model or the amount of data.** Sales come in bursts (bulk
  orders, school-calendar rushes, renamed or pulled products). More history plateaus after ~6 months;
  four separate synthetic-history tests did not beat the shipped models; even a forecast that knew
  each item's exact average would miss by 77%.
- **MAPE <= 20% is unreachable and the wrong objective** (its optimum on 81%-zero data is "forecast
  nothing"). Replacement criteria were built and measured: the service-level frontier (in `neil`) and
  an acceptance standard with a stocking-policy holdout (**only on `origin/tyrone`, not merged**).
- Latest session: results workbook corrected and simplified, FSN classification analysed, renamed
  products found, the laptop's alternative prediction targets reviewed. No pipeline or model change.

---

## 1. What the app runs now

| Level | Model | How it works | Error (WMAPE) | Chosen by |
|---|---|---|---:|---|
| Category (12) | `RM6_6month_180d+calendar` (`scripts/step4c_category_forecast.py`) | Category's average daily sales over 180 days x 30, lowered (never raised) when more break/exam days are coming | 42.1% | Method comparison + parameter tuning (3.2) |
| Item (58 Fast) | `topdown_tsb+calendar+prophet_shape` (`scripts/step4_forecast_model.py`) | Half: category 180-day level x item's share of the last 30 days. Half: TSB (alpha = beta = 0.05). Same calendar rule | 60.1% | 47-method item test (3.1) |
| Day-by-day | Category's Prophet pattern (`forecasting/shape.py`) | Spreads each 30-day total over the days; total unchanged | 2-3% lower daily error than flat | Daily shape tests (3.3) |
| Reorder (step 5) | Trailing 365-day actual demand, not the forecast | ROP / safety stock / EOQ for Fast + Slow items | n/a | Remediation S1 (3.9) |

How the item forecast got here: Prophet per SKU (Jul) -> `rolling_mean_30` (1 Sep,
`docs/ROLLING_MEAN_FORECAST.md`) -> category share + TSB blend with calendar adjustment (28 Sep).

---

## 2. How everything is tested

- **Walk-forward** (`forecasting/evaluate.py`): up to 12 rolling origins, 30 days apart; each forecast
  uses only data before its origin; horizon 30 days; min 60 days of training. Every method in a
  comparison is scored on identical folds.
- **The scored unit is the 30-day total**, because that is what the reorder decision uses. It replaced
  an 80/20 daily holdout, which was not walk-forward and handed the naive baseline one-step-ahead data.
- **Metrics**
  - **WMAPE** (how far off, % of units sold): units missed / units sold. The main number now.
  - **MASE**: error / the series' usual change between consecutive 30-day blocks in its training data.
    Not the same test as "beats repeating the last 30 days".
  - **MAPE**: the manuscript's metric; undefined on zero-sale windows (about half the item windows),
    explodes on small ones.
  - **Fill rate / units held / SKUs priced**: whether a forecast is usable for stocking.
- Leakage guards and tests: `tests/test_evaluate.py`, `tests/test_category_leakage.py`,
  `tests/test_determinism.py`, `tests/test_gates_can_fail.py`; `tools/assert_invariants.py` pins row
  counts and the F/S/N split.

---

## 3. Test catalogue

Verdict key: **Won** = became or confirmed the shipped choice; **Lost** = worse than the shipped or
simple baseline; **Mixed / null**; **Finding** = a measurement, not a model.

### 3.1 Item-level model comparisons

| Test | Result | Verdict | Where |
|---|---|---|---|
| 8-method benchmark, 266 SKUs (Aug) | Best MASE is `rolling_median_30`, which prices 0 of 266 SKUs; TSB is 10.2% worse on MASE and prices all 266 | Finding (#21) | `docs/DEGENERATE_FORECAST.md` |
| EWMA and rolling 75th percentile added | Neither beats `rolling_mean_30` / TSB | Lost | `docs/FORECAST_METHOD_COMPARISON.md` |
| 29 methods on the Fast segment, raw vs cleaned data | TSB (MASE 3.45) and `rolling_mean_30` (3.47) top; best learner `xgboost_pooled` 3.91; Prophet 56-63% worse | Trailing averages won | `docs/FAST_MOVING_BENCHMARK.md` 6.1-6.2 |
| Rolling-mean window sweep (3 days to 6 months) | U-shaped, best at 30 days per SKU | Finding | same, 6.6 |
| Calendar-lag models ("same period last year", semester-week match) | 37% worse; semester-week worst; only one year of history to learn from | Lost | same, 6.11 |
| Top-down with a 90-day share (Sep) | Loses to per-SKU | Lost (see next row) | same, 6.11 |
| **47 methods on the 58 Fast items** (Prophet, averages, TSB, category share, blends) | 50/50 category share (30-day share) + TSB: mean MASE 1.71 vs 2.52 for the Prophet it replaced; better on 46 of 57 items; first in both older and newer 6 folds; settings grid flat (1.68-1.90) | **Won -> shipped** | `scripts/test_item_forecast_methods.py`, Testing Summary workbook |
| Blends that include Prophet | Every blend with Prophet scored worse than the same blend without it | Lost | same |
| Transformations (log, sqrt, Yeo-Johnson) | No gain; Yeo-Johnson breaks; sqrt Prophet ties the category model (1.08 vs 1.09) but not better | Null | `scripts/test_transformations.py` |
| 80/20 single holdout vs walk-forward | Reorders the ranking because one cut lands after a spike; one draw is not enough | Finding | `docs/FAST_MOVING_BENCHMARK.md` 6.10 |

### 3.2 Category-level model comparisons

| Test | Result | Verdict | Where |
|---|---|---|---|
| 27 methods on 12 category series | 6-month average best (WMAPE 49.3%); SARIMA 2nd (53.2%); ARIMA top 6; Prophet 15% behind | Won | `docs/FAST_MOVING_BENCHMARK.md` 6.15 |
| Category forecast vs sum of item forecasts | Identical for linear methods (mean of sums = sum of means); the category "gain" is aggregation, not a better model | Finding | same |
| Top-down (category split back to items by 90-day share) | 77.6% vs 74.8% per-SKU (p = 0.054) | Lost | same; `docs/DIVERGENCE_REGISTER.md` #24 |
| 6-month vs 3/12-month averages vs 3 Prophet versions vs Prophet blend | 6-month best (macro MASE 1.14) | Won -> shipped | `scripts/compare_category_forecast_methods.py` |
| Re-scoring the earlier "Prophet: 8 of 12 categories beat naive, MASE 0.99" | Did not hold: scored only on open days (hindsight), trained on sale-days only, MASE denominator in the wrong unit (~1.4x too small). Correct scale: 1 of 12 below 1 | Correction | `docs/FORECASTING_EXPLORATION_NOTES.md` 2.1 |
| TSB / SBA / Croston per category | Beat the average in only 2 of 12 categories | Lost | Testing Summary workbook |
| Tuning TSB (16 settings) and Prophet (6 settings) | Best TSB 1.33, best Prophet 1.22, vs 1.14 | Lost | `scripts/tune_category_forecast_params.py` |
| Best model per category | Winners scattered across 8 unrelated families: noise from 27 candidates x 12 months | Not used | Testing Summary workbook |

### 3.3 School calendar and day-by-day shape

| Test | Result | Verdict | Where |
|---|---|---|---|
| Prophet with calendar regressors vs plain | Calendar helps Prophet 4%, still loses to trailing averages | Mixed | `docs/FAST_MOVING_BENCHMARK.md` 6.2 |
| Calendar flags as ML / hurdle features | Median change in per-SKU MAE exactly 0 | Null | `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` #8 |
| **Calendar adjustment of the 30-day total** (lower only) | Item WMAPE 64.1% -> 60.3%, category 45.2% -> 41.8%; better in both older and newer folds. The "lower only" rule was chosen after seeing two months, so slightly optimistic; the up-or-down version failed after breaks | **Won -> shipped** | `scripts/test_calendar_adjustment.py` |
| Day-by-day shape (weekday, calendar regression, Prophet, Holt-Winters) | Category Prophet pattern 2-3% lower daily error than flat, mostly in break months; Prophet's own total is worse than flat | Won -> shipped (shape only) | `scripts/test_category_forecast_shape.py`, item version in `test_item_forecast_methods.py` |
| Lead-time gap (score as if actionable only after 14-28 days) | Item MASE 1.71 -> 2.14; category roughly unchanged | Disclosure | `scripts/test_lead_time_gap.py` |

### 3.4 Sparse and intermittent demand

| Test | Result | Verdict | Where |
|---|---|---|---|
| Weekly hurdle model (chance of selling this week x size) | Best MASE/RMSE of 11 methods at the time; prices 140 of 266 SKUs vs 79 | Positive | `docs/SPARSE_DEMAND_EXPERIMENTS.md` #1 |
| Hurdle with a fitted classifier | Worse on every metric (overfits thin history) | Lost | same, #2 |
| Pooled logistic hurdle | Better on every metric | Positive | `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` #7 |
| Croston / SBA | Over-forecast by 39-46%; mean MASE blown up by small denominators | Lost | 47-method test, benchmarks |
| Forecasting at a coarser level (category, whole store) | MAPE 203% -> 86%, never near 40% | Partial | `docs/SPARSE_DEMAND_EXPERIMENTS.md` #3 |
| Degenerate forecast | On 81%-zero data the MAE/MASE-optimal forecast is zero | Finding (#21) | `docs/DEGENERATE_FORECAST.md`, `tests/test_degenerate_forecast.py` |

### 3.5 More data, synthetic data, data gaps

| Test | Result | Verdict | Where |
|---|---|---|---|
| 5 synthetic years added (window methods) | Almost nothing moves (window methods cannot see it by design) | Null | `docs/SPARSE_DEMAND_EXPERIMENTS.md` #4 |
| 3 synthetic years for the ML learners (per SKU) | Learners improve ~11% (ridge by half), best still 6.6% worse than `rolling_mean_30` | Lost | `docs/FAST_MOVING_BENCHMARK.md` 6.12 |
| Synthetic history with pooled models | Real but smaller gain after a measurement bug was caught | Mixed | `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` #4-5 |
| 3 synthetic years at category level | Ridge 45.1% vs 6-month average 49.3%, but the **most realistic** generator loses (50.6%): regularisation, not new signal | Mixed, fragile | `docs/SYNTHETIC_AUGMENTATION_CATEGORY.md` |
| Filling the missing 2023-2024 months with synthetic sales | Shipped models unchanged (category MASE 1.074 -> 1.076, item 1.668 -> 1.666, within noise) | Null | `docs/SYNTHETIC_MISSING_MONTHS.md` |
| **More real history** (limit the model to the last 3-18 months) | Fast items 64.6% (3 months) -> 60.4% (6) -> 60.1% (all 20); flat after 6-9 months | Finding | `scripts/test_history_length.py` |
| Un-tallied days read as zero sales | 213 of 821 days are fabricated zeros (incl. 66 days in Jun-Aug 2024); "observed-day" fix helps TSB 5.5%, no change to the 30-day window | Finding | `docs/FAST_MOVING_BENCHMARK.md` 6.13, pooling log #10 |
| Padding days at the end of the series | Series ran to 31 Jul while tallies stop 8 Jul; item forecasts were ~5x too low (647 vs 3,291 units). Fixed | Fix | `docs/FORECASTING_EXPLORATION_NOTES.md` 2.5 |

### 3.6 Pooling, grouping, clustering, categories

| Test | Result | Verdict | Where |
|---|---|---|---|
| Pool items by category / finer product type | Hurt MASE; drinkware worst | Lost | `docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md` #1, #3 |
| Pool by category + fast/slow | Apparent gain was a leak (fsn_class uses full history); fixed, it is the worst grouping | Corrected to Lost | same #2; `FORECAST_EXPERIMENT_AUDIT.md` |
| K-means clustering on demand behaviour | Best error metrics of the pooling log after the fix, but on the stocking objective 6.35 points less fill rate than `rolling_mean_30` while holding more stock | Dominated | same #11; `docs/FORECAST_VALIDATION.md` (origin/tyrone) |
| Behaviour clusters vs named categories (same technique) | Unresolved, not a win | Null | `docs/FORECASTING_EXPLORATION_NOTES.md` 2.4 |
| Splitting by Fast/Slow/Non-moving | Adds nothing | Null | same, 2.3 |
| Store's own item list (CICS sections) as categories | Item level: tie (MASE 1.678 vs 1.668, 95% range crosses 0); category level: current better | Tie | `scripts/compare_cics_categorization.py`, results workbook |
| Grouping search (chosen on older 6 months, scored on newer 6) | Current categories best on unseen months (2.907); search pick looked better on chosen months, lost on unseen; 1-2 groups worse | Current kept | `scripts/search_groupings.py` |

### 3.7 Error ceilings and diagnostics

| Test | Result | Where |
|---|---|---|
| Oracle floor (flat forecast knowing each SKU's future mean) | Mean per-SKU MAPE 250.6%; 0 of 58 SKUs <= 30%; 52% of folds have zero sales; CV 1.15 | `docs/FAST_MOVING_BENCHMARK.md` 6.14 |
| Aggregation ladder (same model) | WMAPE 74.8% per SKU -> 57.7% per category -> 45.6% whole segment | same |
| Sparsity diagnostics (density buckets, simulation) | Sparsity is not the bottleneck; demand whose rate shifts (semester cycles) is | pooling log #9 |
| MASE aggregation | Mean MASE dominated by ~10 near-dead SKUs (up to 73%); report median / weighted too | pooling log #6 |
| Pareto check | Catalogue is ~73/20, not 80/20; forecasts reproduce which SKUs are big (TSB Spearman 0.98) | `docs/FAST_MOVING_BENCHMARK.md` 6.9 |
| What-if: bulk orders / closures known in advance | Item error 60.1% -> 47.0% (strict) or 36.2% (broader) with bulk orders known; closures -0.2 points | `scripts/test_what_if.py` |
| Cleaning effect (raw vs clean) | Population MASE -20.6%, but 220 of 238 series unchanged: the gain is mostly the roster (duplicates merged), not smoother series | `docs/FAST_MOVING_BENCHMARK.md` 6.4-6.5 |

### 3.8 Acceptance criteria and the stocking policy

| Test | Result | Verdict | Where |
|---|---|---|---|
| Manuscript MAPE <= 20% | 1 of 58 items, and that item (Tote (Viva)) passes only because it was renamed and MAPE skips its zero months (see 4.3) | Not met, degenerate | `docs/ROLLING_MEAN_FORECAST.md`, results workbook |
| Replacement "service >= 95% + MASE < 1" | Best fill 71.6%; hard ceiling 94.9% before any model; normal-quantile safety stock undersizes lumpy demand | Not met | `docs/SERVICE_LEVEL_FRONTIER.md` (#22) |
| Service-level frontier instead of a threshold | `rolling_mean_30` dominates at q ~ 0.80 (fill 0.742, 49,853 units held vs ETS 64,336) | Finding | same |
| Acceptance standard (4 conditions: actionability, beats no-model policy, not dominated, evidence integrity) | 13 of 14 checks pass; forward demand coverage 0.883 vs a priori 0.90 -> NOT ACCEPTED, reported not tuned | **origin/tyrone only** | `docs/ACCEPTANCE_STANDARD.md` (tyrone) |
| Stocking-policy holdout (4 rolling origins) | Fill rate median 0.706 (0.599-0.915); tiered buffers beat a flat quantile on fill at 4 of 4 origins | **origin/tyrone only** | `docs/POLICY_HOLDOUT.md` (tyrone) |
| EOQ demand basis | Decoupled from the forecast: trailing 365-day demand prices 208 SKUs vs 79 | Shipped | `scripts/step5_prescriptive.py` |

### 3.9 Alternative prediction targets (laptop run, reviewed this session)

Instead of exact units, six other questions, each model vs a simple rule
(`prediction_targets_results.xlsx`, repo root):

| # | Question | Result | Verdict |
|---|---|---|---|
| 1 | Will the item sell in the next 30 days? | AUC 0.95 vs 0.88; wrong "idle" flags 5% vs 9%; yes/no accuracy about the same (88.9% vs 88.6%) | Useful |
| 2 | Weeks until the next sale | C-index 0.93 vs 0.90 | Useful |
| 3 | Next month's top sellers | Last month's ranking is as good | Use the simple rule |
| 4 | Item going 60 days silent | Ranks better (0.85 vs 0.78), flags no more accurately | Marginal |
| 5 | New product's first 90 days | 28% of guesses within 2x | Ballpark only |
| 6 | Semester-start spike | Worse than "spiked last semester" | Not usable |

Review notes: #1's baselines reproduce on this DB exactly (n = 1,922); #1 and #2 scores are
flattered by long-dead items (14% of rows; on items sold in the last 90 days the simple rule's AUC
drops 0.89 -> 0.75), so re-run on active items for the honest figure. The three scripts it names are
not in the repo, and its Overview's Model/Baseline/Gain cells are formulas with no cached values.

---

## 4. Latest session (2026-09-30)

### 4.1 Why the item error is 60%: breakdown

Shares of the pooled item error (overlapping, do not add to 100%):

| Cause | Share | Evidence |
|---|---:|---|
| Start-of-term surges, forecast too low | 35% | Jul-Aug 2025 and Jan 2026 sold 45-50% more than forecast |
| Renamed / re-listed products | ~19% | Heuristic name matching, approximate |
| Bulk-order days | 17% | One day >= half the month (63 of 343 item-months with sales) |
| Just after a surge, forecast too high | 16% | Feb-Mar 2026, 63-110% too high |
| Item missing from the sheet (sold out / pulled) | 6-12% | On the sheet 3% of days in those months vs 85% when selling |
| New item, no history | 4% | First month of sales |

Also: a forecast that knew each item's exact yearly average misses by 76.9%; with steady random
buying the same forecast would miss by 8.4% (sales swing 7.8x more than random buying). Even with
each month's segment total known exactly, item error would be 51.5%. In the raw tally sheets (no
cleaning), 79% of item-days are zero, top sellers make 32% of their sales on 5 days, and sales per
day jump from 40 (Jul 2025) to 217 (Aug 2025). Error tracks burstiness (Spearman +0.69), not
history length (-0.10, not significant).

### 4.2 FSN classification

- ADUS = weighted units / **dates the item has any Fact_Sales row** (zero-sale rows included,
  censored days excluded); Fast = top 20% (cutoff 1.692/day; UST Embro OS 1.6923 is Fast, Yellow
  Notebook 1.6908 is Slow).
- Stopped items never leave Fast: 28 of 58 unsold for 90 days, 13 for a year (e.g. New Clappers,
  936 units on 6 sheet days in Dec 2024). They add ~0% to error but flatter MASE and clutter screens.
- Rules tested (Fast list rebuilt before each of 12 months): **no rule lowers the error** (58.9-60.0%).
  Skipping items with no sale in 90 days removes ~15 dead items a month with zero coverage loss.
  Recent-sales rules cover 77-83% of next month's sales at 58 items vs 69% now. The manuscript's
  literal wording (units / days with a sale) is worse on both (54.8% coverage, 62.6% error).
- `README.md` lines 92-97 claim a calendar-day denominator changes no class; it changes 36 items now.
  The code/manuscript wording difference is not in `docs/DIVERGENCE_REGISTER.md`.
- Pareto: top 20% of items hold 72.6% of units; the Fast set holds 67.0%; only 37 of the 58 Fast
  items are among the 58 best sellers by units.

### 4.3 Renamed products and the MAPE "pass"

- Old name stops, near-identical name starts days later (group to decide if same product; the
  vocabulary was not touched): Santo Tomas shirt -> "/Script" (29 Mar -> 5 Apr 2026), Tote (Viva) ->
  Tote Viva Santo Tomas (28 Oct -> 5 Nov 2025), Ballpen 2 -> 3 Designs, Eco Bag -> Black Eco Bag,
  Tiger Claw -> Tiger Paw Keychain, ID Case -> ID Case v2, UST OAT MUG variants. Many switch between
  28 Oct and 5 Nov 2025 (likely a sheet redesign).
- The one item under 20% MAPE is Tote (Viva): MAPE skips its 8 zero months, including Nov 2025
  (forecast 59, sold 0). MAPE 9.3%, WMAPE 33.5%. Tiger Plushie Small's 13.5% in the older
  `baseline_rolling_mean_30_metrics.csv` was a single semester-break window, never counted.

### 4.4 Results workbook (`data/USTore_Current_Model_Results.xlsx`)

Script `scripts/build_current_results_workbook.py` edited; numbers unchanged and still checked
against `ustore.db`.

| Was | Now |
|---|---|
| Item MASE headline included 13 items unsold for a year (score ~0) | "Items still selling (45)" row: MASE 2.11 (not 1.67), below 1 for 13 of 45 |
| MASE defined as "vs repeating the last 30 days" | Correct definition |
| No note that methods were chosen on the same 12 months | "Fair warning": without the calendar rule items 63.9%, categories 45.5% |
| MAPE <= 20% target not mentioned | 0 of 12 categories, 1 of 58 items |
| "58 best-selling items" | "Items classed as fast-moving" |
| What-If "biggest cause… no model could predict" | "Largest cause tested", with the enrollment-rush caveat |
| History "cannot change", "a year is enough" | "Barely", "6 to 12 months" |

Plus: plain-language Read Me and glossary, plain column names ("How far off, % of units sold"
first), and a new **How It Works** tab with a Lanyard @180 worked example that is recomputed at
build time and checked against the model (252 forecast vs 313 sold).

### 4.5 Also checked

Lead times come from USTore (verbal, per garment type 14/18/28/18 days); only the item-to-type
keyword matching is ours. `neil` was fast-forwarded to `origin/neil` (`b6cf799`), which already
contained marco's latest frontend. Frontend idea (not built): show prediction targets #1/#2 as a
"sales outlook" chip, separate from the reliability tag.

---

## 5. What is and is not in `neil`

| Item | Status |
|---|---|
| Tyrone's acceptance standard, stocking policy, service-class leak fix (`d5275e6`, `d618462`: `forecasting/policy.py`, `scripts/validate_policy_holdout.py`, large `step5_prescriptive.py` change, 5 docs) | **Only on `origin/tyrone`**; will overlap with `step5_prescriptive.py` and `create_schema.py` when merged. `d5275e6` also deletes the commit and scope policies from `CLAUDE.md`; decide whether to keep them before merging |
| Laptop scripts for the prediction targets | Not committed anywhere |
| `scripts/build_current_results_workbook.py` changes | Uncommitted in the working tree |
| Rebuilt results workbook | Not locked into `vault/` (`lock` would also add `prediction_targets_results.xlsx`) |
| One-off checks from the latest session (4.1-4.3 breakdowns, FSN rule simulations, rename matching) | Scratch scripts, not in the repo; can go under `tools/` if wanted |
| Testing Summary workbook (`data/USTore_Forecast_Testing_Summary.xlsx`) | Built 27 Sep, before the uncategorised items were placed (29 Sep), so it still lists "Uncategorised" |

---

## 6. Needs a group decision / to do

- [ ] Merge plan for `origin/tyrone` (acceptance standard + policy) into `neil`, and which criterion Chapter 4 reports (B2).
- [ ] Commit `scripts/build_current_results_workbook.py`, then `vault.py lock` and commit `vault/`.
- [ ] Commit the laptop's prediction-target scripts; re-run #1/#2 on recently active items.
- [ ] FSN: add a "skip items with no sale in 90 days" filter to step 4, keep as is, or change the rule (a rule change breaks the F = 58 check in `tools/assert_invariants.py`, which was not edited).
- [ ] Record the ADUS denominator difference in `docs/DIVERGENCE_REGISTER.md`; fix `README.md` lines 92-97.
- [ ] Decide which renamed pairs are the same product (vocabulary change).
- [ ] Ask USTore to log bulk / organisation orders separately and keep monthly stock counts; that would cut the error more than any model tested.
- [ ] Decide whether to build the sales-outlook chip once the prediction scripts are in.
