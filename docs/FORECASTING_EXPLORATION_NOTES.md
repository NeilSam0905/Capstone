# Forecasting accuracy exploration — findings, corrections, open work

Where the forecasting work stands after merging `marco` into `neil` and wiring
a category forecast into the dashboard. Written to be picked up by anyone on
the team without re-deriving it from chat history. **Part 2 corrects things
earlier versions of this file (and `docs/REBUILD_PIPELINE.md` §6) claimed.**

## 1. Where things stand

- **The dashboard's category forecast is now a trailing 6-month average of the
  category's combined sales** (`scripts/step4c_category_forecast.py`), not the
  sum of item forecasts and not Prophet. It covers every item in the category.
- On the project's walk-forward harness (30-day horizon, up to 12 folds) it
  beats "repeat the last 30 days" in **12 of 12 categories**; pooled WMAPE
  **45.2%** against 55.8% for repeating last month.
- It is still not accurate in absolute terms. The typical miss (MAE as a share
  of a typical month) runs from 29% for Outerwear to 85% for Apparel Accessories,
  and roughly 30–55% for the eight biggest categories. MASE (in the scored unit)
  is 1.14 macro and below 1 in only 4 of 12 categories, and the manuscript's ≤20%
  MAPE target stays out of reach (Divergence #6). The screen shows the typical
  miss, in units and as a share of a month, next to the total.
- The old sum-of-items behaviour is kept as an automatic fallback for any
  database where step4c has not run.
- The chart is no longer a flat line. The 30-day total is still the 6-month
  average's; a calendar-aware Prophet model only decides how that total is spread
  over the days (section 2.6). The gain is real but modest, and comes almost
  entirely from months containing a semester break.
- **Both forecasts now lower their 30-day total when the school calendar shows
  quieter days ahead** (section 2.10): category macro MASE 1.14 to 1.09, item
  1.71 to 1.67. Small, and it under-forecasts more in break months.
- **The dashboard's item forecast is no longer Prophet.** It is a 50/50 blend of
  the item's category share and TSB, with the 30-day total spread over the days by
  the category's Prophet pattern (`scripts/step4_forecast_model.py`, section 2.7).
  Mean MASE 1.71 against 2.52 for the Prophet it replaced, better on 46 of 57
  items; on the 45 items that still sell, 2.16 against 2.61. Still weak in absolute
  terms (MASE below 1 for 23 of 57 items), and it has no seasonal term.

What changed in the repo for this:

- `scripts/step4c_category_forecast.py` (new): writes `Result_Category_Forecast`
  and `Result_Category_Forecast_Metrics`; in the pipeline after step4 (optional).
- `backend/app.py`: `/api/forecast/category/<c>` serves the category model
  (`data.source`), new `/api/forecast/categories`; fallback kept.
- `Forecast.jsx`, `dataService.js`, `format.js`: category picker now includes
  categories with no Fast item (Home & Novelty, Apparel Accessories were
  unreachable before); card states what the figure covers, its typical miss, and
  that the item list does not add up to it.
- `scripts/step4_forecast_model.py`: **bug fix**, see 2.5; **new default model**,
  see 2.7. `forecasting/topdown.py` (category-share model and the blend) and
  `forecasting/shape.py` (the day-by-day shape, shared with step4c) are new;
  `forecasting/prophet_model.py` gained an optional `growth` argument.
- `forecasting/calendar_adjust.py` and `scripts/test_calendar_adjustment.py`
  (new): the calendar adjustment in 2.10 and the test behind it
  (`data/calendar_adjustment_*.csv`); step4 and step4c use it by default.
- `scripts/test_item_forecast_methods.py` (new): the 47-method item comparison
  behind 2.7. Outputs `data/item_forecast_*.csv`; about 6 minutes.
- `scripts/compare_category_forecast_methods.py` (new): reproduces the method
  comparison behind the choice. Outputs `data/category_forecast_method_*.csv`.
- `scripts/test_category_forecast_shape.py` (new): the day-by-day shape test
  behind the non-flat chart. Outputs `data/category_forecast_shape_*.csv`.

## 2. Corrections to earlier claims

### 2.1 "Marco's Prophet: 8 of 12 categories beat naive, MASE 0.99" does not hold

`scripts/forecast_category_prophet.py` is flattered three ways
(`compare_category_forecast_methods.py`, 12 categories x 140 folds, same
actuals for every method, each forecast using only pre-origin data):

- **Hindsight trading days.** It sums the forecast over the trading days that
  actually occurred in the test window. Production cannot know next month's
  closures.
- **Sale-days-only training.** It drops trading days on which the category sold
  nothing, so it learns "units per selling day" and forecasts **+34% high**.
- **Wrong-unit MASE denominator.** Built from 30 *rows* of the trading-day
  series (~43 calendar days) while errors are on 30-calendar-day windows: 1.41x
  too large. A plain "repeat last month" forecast scores 0.90 on it, which is
  how you can tell it is not a naive benchmark. Rescaled, the same Prophet
  output is **MASE 1.50, with 1 of 12 categories below 1**.

| method | beats "repeat last 30d" | pooled WMAPE | bias |
|---|---:|---:|---:|
| trailing 180-day average (shipped) | 12 of 12 | 45.7% | -6.9% |
| 50/50 blend, Prophet + 180-day average | 11 of 12 | 47.0% | +8.2% |
| trailing 90-day average | 9 of 12 | 50.7% | -5.5% |
| repeat last 30 days | — | 55.7% | -5.1% |
| Prophet, zero-filled, hindsight days | 8 of 12 | 52.1% | +12.6% |
| Prophet, zero-filled, expected trading days (production-realistic) | 5 of 12 | 61.1% | +23.3% |
| Prophet as committed | 4 of 12 | 62.4% | +33.9% |

This table uses the rebuild-CSV data path (`data/rebuild_*.csv`), which Prophet
needs; the same 180-day average on the DB series the pipeline actually has is
45.2% (section 1). The project's earlier 27-method category benchmark
(`data/category_benchmark_summary.csv`) independently ranked the 180-day
average first and Prophet mid-table.

### 2.2 "Aggregating before forecasting is the lever" was overstated

For a trailing average, summing items then averaging is **identical** to
averaging then summing. What actually helps here is (a) covering every item
instead of only Fast ones and (b) a longer window, which a smooth category
series can afford and sparse item series cannot. Aggregating first does matter
for non-linear models (Prophet, ML) — that is where it was first seen — but
those did not win once scored fairly. `data/category_vs_sku_level.csv`
(49% category vs 85% per item) shows only that error on a *total* is
relatively smaller, which any aggregation does.

### 2.3 Fast/Slow/Non-moving splitting adds nothing

Earlier: "splitting rescues Home & Novelty (MASE 1.358 -> 0.421)". The gain was
the **zero-fill fix**, applied to the split arm only. Zero-filling the *whole*
category with no split gives 0.432 there. All on the marco-scale MASE with
hindsight days, so read them as relative:

| category | as committed | zero-fill only | split (Fast/Slow/Non-moving) |
|---|---:|---:|---:|
| Shirts & Tops | 1.357 | 1.101 | 1.062 |
| Lanyards & IDs | 1.553 | 1.444 | 1.452 |
| Home & Novelty | 1.358 | 0.432 | 0.421 |
| Uncategorised | 1.229 | 0.852 | 1.170 |

### 2.4 Clustering vs category grouping is unresolved, not a win

The k=4 whole-catalogue comparison (1.023 vs 1.102) ran in the same harness
with the same confound: the cluster arm was zero-filled and the category
baseline was not. It is not evidence that behaviour clusters beat semantic
categories. `scripts/cluster_split_prophet.py` and
`scripts/category_split_prophet.py` remain as experiments only.

### 2.5 The item forecasts on the dashboard were ~5x too low (fixed)

`Fact_Sales` is zero-filled to the end of the calendar range (2026-07-31) but
the tallies stop at 2026-07-08, so its last **23 days are padding**. Step4's
30-day trailing mean therefore averaged 7 real days with 23 zeros: the Fast
items' forecasts summed to **647 units against 3,291 actually sold in the last
30 real days** (0.20x). After the fix they sum to 3,291 (1.00x). The same
padding turned up in the category benchmark: its 49.3% WMAPE includes 23 fake
zero days, and is 45.2% with them trimmed. Both forecast steps now end their
history at the last day any SKU sold, and the forecast window starts the day
after (07-09 to 08-07).

- Fixed 2026-09-30: `step5_prescriptive.py::load_series` spanned the padded
  panel. Its window is 365 days so the effect was ~6% on the denominator, not
  5x — but trimming re-anchors the window as well, and the knock-on was much
  larger than 6%: six more SKUs clear the rate threshold, the empirical buffer
  grows 52% because the last folds had been scoring against fabricated zero
  actuals, and the acceptance verdict flips. The rule now lives in one place,
  `step5_prescriptive.history_index()`, shared with
  `validate_policy_holdout.py`. Full before/after in `docs/OPEN_ISSUES.md`
  under "the padding fix". Root cause is still upstream in the step0/step2
  zero-fill to month end, which is untouched.
- Still unfixed, and now inconsistent: `model_benchmark.py::load_daily_series`
  spans the padded panel, so `tools/service_frontier.py` (which imports it)
  and every benchmark table measure a 821-day span while the deployed policy
  measures 798. Changing it would move every published benchmark figure,
  including the 0.9490 ceiling, so it is a decision rather than a patch.
- Fixed (only matters for `--model prophet` now): item-level Prophet's calendar
  regressors were zero in the forecast window (`forecasting/prophet_model.py::
  load_calendar` reindexes Dim_Date onto the shared index, so future dates came
  back empty and were filled with 0). The validation folds saw the real flags, so
  the validated model and the produced forecast were not the same model.
  `step4_forecast_model.py::_make_prophet` now extends the index and calendar 30
  days; validation is unchanged (mean MASE 2.515) and the production total moved
  from 3,688 to 3,793 units.

### 2.6 A flat line is a limitation of the average, and the fix is only partly available

A trailing average has no idea about dates, so it draws a flat line. Shipping a
date-aware model (Prophet) for the whole forecast would fix the picture but cost
accuracy (section 2.1; its 30-day total error is 48.7-52.3% against 45.2%, and
its day-by-day error is 3.5-6.6% WORSE than a flat line). So the
forecast is a hybrid: the validated 6-month total, spread over the days by a
date-aware shape (`step4c_category_forecast.py --shape`).
`scripts/test_category_forecast_shape.py` scores shapes day by day on the same
144 category-folds (daily error relative to a flat line; below 1 is better):

| shape | all months | months with a semester break | other months |
|---|---:|---:|---:|
| Prophet: weekly + calendar (shipped) | 0.972 | 0.921 | 0.995 |
| Calendar regression (weekday + events + closures) | 0.960 | 0.872 | 0.999 |
| Weekday pattern + known closures | 0.977 | 0.966 | 0.981 |
| Holt-Winters weekly | 1.012 | 0.986 | 1.023 |

- The top three shapes win in about 60-65% of category-months, by 2-4% on daily error. Daily
  sales are mostly noise, so this is small, and it is a rhythm (quiet Sundays,
  closures, breaks), not a forecast of individual spikes. One-off bulk orders are
  not predictable from dates, and windows with enrollment or event days show no
  better skill than the rest (only 34 enrollment days exist in the history).
- Almost all of the gain is in the 4 test windows containing 5+ semester-break
  days. The current forecast window (2026-07-09 to 08-07) contains 7, and the same
  season last year (2025-07-14 to 08-12) contained 9, so the dip the chart shows
  there is the best-supported part of the shape, though four windows is thin.
- In ordinary months the Prophet shape is a wash against flat (0.995); the plain
  weekday pattern is slightly better there (0.981) and needs no Prophet. The
  three leading shapes are within noise of each other overall, so Prophet was
  kept as the default because it was asked for and is never worse than flat;
  `--shape weekday` is the dependency-free option and the automatic fallback.

**Changed 2026-09-30: `weekday` is now the default shape, in both `step4c` and
`step4`.** Nothing measured here moved — this is a dependency decision, recorded
rather than made silently. The shape only redistributes a 30-day total and the
harness scores the total, so on the primary metric the two are identical; they
differ only in the daily curve, where the table above puts Prophet at 0.972
overall against weekday's 0.977, and weekday **ahead** in ordinary months (0.981
against 0.995). Prophet keeps the edge in break-heavy months (0.921 against
0.966), which is the case for keeping it selectable, not for making a `cmdstan`
toolchain a precondition for running the pipeline at all. `step4`'s default is
`topdown_tsb+calendar+weekday_shape` and `step4c`'s is `--shape weekday`;
`topdown_tsb+calendar+prophet_shape` and `--shape prophet` are unchanged and
still there, and `requirements/requirements-prophet.txt` is what installs them.

### 2.7 The item forecast was Prophet, and a simple blend beats it (changed)

`scripts/test_item_forecast_methods.py` scores 47 methods on the 58 Fast items over
the same walk-forward folds (`data/item_forecast_method_test.csv`). Prophet as it
was shipped reproduces its stored metrics exactly (per-item MAE difference
0.000000), so these are the numbers the screen was showing. MASE is the mean over
the 57 items with a usable scale.

| method | mean MASE | 45 items still selling | pooled WMAPE | bias |
|---|---:|---:|---:|---:|
| Prophet (previous default) | 2.52 | 2.61 | 88.4% | +25% |
| repeat last 30 days | 1.95 | 2.47 | 71.6% | -5% |
| 6-month average | 2.25 | 2.80 | 78.1% | -7% |
| TSB, alpha = beta = 0.05 | 1.79 | 2.27 | 66.9% | -7% |
| category 6-month average x item's 30-day share | 1.75 | 2.22 | 65.5% | -8% |
| **50/50 of the last two (the new default)** | **1.71** | **2.16** | **64.1%** | -7% |

The blend is half the category's long-run level split by the item's recent share
of it (long memory for the smooth category, short for the item), half TSB (a
smoothed recent-sales rate that decays on items that stop selling).

- **Size of the gain.** Against the old Prophet the mean MASE gap is 0.81 (95%
  interval over resampled items 0.51 to 1.21), better on 46 of 57 items. On the 45
  items still selling it is 0.45 (0.28 to 0.61), better on 34. It has a lower
  error than the old Prophet in all 12 past 30-day windows
  (`data/item_forecast_by_window.csv`).
- **Not one lucky period.** Ranking all 47 methods on the older six folds and the
  newer six gives a rank correlation of 0.95, and the blend is first in both.
  A grid over its two settings (top-down share window x TSB smoothing) is flat,
  1.68 to 1.90 across 18 combinations (`data/item_forecast_sensitivity.csv`).
  Forty-seven methods were compared, so the top group (top-down, TSB, their blends)
  is within noise of each other: the blend beats TSB alone by 0.09 (0.02 to 0.16)
  but not clearly the top-down part alone (0.05, -0.02 to 0.12).
- **13 of the 58 "Fast" items sold nothing in the last 360 days** (last sales
  between 2024-05 and 2025-05). `fsn_class` is computed from full history
  (`forecasting/category.py` explains why that is a leak in backtests), so they are
  still Fast, and Prophet kept forecasting them. A decaying method does not. That is
  a real fault of Prophet here, but it flatters the headline: use the 45-item column.
- **The category-level lesson does not carry down.** The plain 6-month average,
  the winner for categories, is 2.25 here and is not reliably better than Prophet
  (interval -0.73 to +0.10; worse on the 45 items still selling, 2.80 against
  2.61). Item series are too sparse for a long window on their own; the category
  supplies the long memory.
- **No seasonality, so it misses the peaks.** It under-forecasts busy windows and
  over-forecasts quiet ones: 40% low in 2025-07-14..08-12, the same season as the
  current forecast window, and 43% low in the following month; +92% in the
  December holidays and +112% in February to March. Items that sell only around
  enrollment will be forecast near zero until they sell again. "Same 30 days last
  year" and seasonal indices were tested and did not help (they under-forecast by
  36-50%: the first year of data is a much smaller base), and only 3 windows
  contain any enrollment day, so an enrollment effect cannot be estimated.
- **Still weak in absolute terms.** MASE is below 1 for 23 of 57 items (11 of the
  45 still selling); the manuscript's <=20% MAPE target is out of reach.

What did not help: 2-, 3- and 12-month averages, spike capping, medians and trimmed
means, category momentum, seasonal indices, Croston and SBA (over-forecast by
39-46%), Prophet with a flat trend, and every blend that included Prophet (each
scored worse than the same blend without it). Dropping Prophet's yearly term helps
it on all items (2.52 to 2.22) but not on the 45 still selling (2.61 to 2.60).

Day-by-day shape. The blend is a flat rate, so each item's total is spread by its
category's calendar-aware Prophet pattern, scored day by day against a flat line
(below 1 is better; `data/item_forecast_shape_test.csv`): the category's Prophet
pattern 0.976 (0.941 in months with 5+ semester-break days), the category's
weekday pattern 0.982, the item's own Prophet 0.986, the item's own weekday
0.998. An item's own days are mostly zeros, so its category supplies the rhythm.

Prophet is still selectable (`--model prophet`) and still supplies the daily
pattern; it no longer supplies the total.

### 2.8 Every fold above scores a forecast as if it were actionable the instant
it is made. It is not: a reorder placed today does not restock the shelf for
`Dim_Product.lead_time_days` (14, 18 or 28 days here). `forecasting/evaluate.py`
gained a `gap` parameter (default 0, so every result above is unaffected) that
inserts that many days between training and the scored window, so a fold is
tested on the same real 30-day outcome using only the information that would
genuinely have been available `lead_time_days` before it -
`scripts/test_lead_time_gap.py` (`data/lead_time_gap_test.csv`) scores the two
shipped models this way, gap = each item's own lead time / each category's
sales-weighted mean lead time (18 days for 10 of 12 categories; 14.3 for
Shirts & Tops, 23.9 for Outerwear, from the mix of items each contains).

A bigger gap forecasts further ahead from less data, so worse accuracy is the
expected, honest cost of this check, not a sign anything is wrong - the
question is how much worse, and whether "beats naive" and "MASE < 1" survive it:

| | mean MASE | ratio | MASE < 1 | beats naive | pooled WMAPE |
|---|---:|---:|---:|---:|---:|
| item (gap=0, current) | 1.71 | - | 23/57 | 37/58 | 64.1% |
| item (gap=lead time) | 2.14 | 1.25x | 21/57 | 32/58 | 70.4% |
| category (gap=0, current) | 1.14 | - | 4/12 | 12/12 | 45.2% |
| category (gap=lead time) | 1.03 | 0.90x | 6/12 | 10/12 | 45.9% |

- **Item level gets meaningfully worse**, as expected - it loses 14-28 days of
  a series that is already short and sparse. The 45-items-still-selling caveat
  from 2.7 applies here too.
- **Category level does not clearly get worse, and is not a clean win either.**
  The 6-month average barely moves when its window is anchored 14-24 days
  earlier - a slow average is close to itself a few weeks either side - so the
  average MASE and count-below-1 both improve slightly. But the "repeat last
  30 days" benchmark it is compared against is just as sensitive to the gap as
  any other 30-day window, and moved MORE in two categories (Lanyards & IDs,
  Shirts & Tops), flipping them from beating it to not. Read the aggregate
  improvement as "no clear cost", not "the model got better."
- This is a validation-only check. It does not change what step4/step4c write
  to `Result_Forecast` / `Result_Category_Forecast` - those still forecast
  starting the day after the last real sale. Whether the SERVED forecast
  window should itself start `lead_time_days` later (so the number on screen
  answers "demand once a reorder placed today would arrive") is a separate,
  larger product decision, not made here.
- Tried and not adopted: re-tuning the item blend's settings specifically
  against this gapped test. A longer category window (365 vs 180 days) scores
  a little lower (2.07 vs 2.14 mean MASE, a real gap by bootstrap) but nearly
  doubles the under-forecast bias (-14% vs -7%) and was already close to best
  even without the gap - not a gap-specific fix, and not worth the bias for a
  small, borderline gain. Category-level settings were already about as good
  as this data allows; nothing tested beat the shipped 180-day average.

### 2.9 13 "Fast" items look dormant, and there is no real "still stocked" flag
to check them against - `Dim_Product.is_active` is 1 for all 519 products, and
`payment_status` is "UNKNOWN" for the items this would matter for. Filtering
them out of the reported accuracy was considered and rejected: they are
currently the EASIEST items to forecast (mean MASE 0.01 - both the model and
"repeat last month" correctly predict close to zero for an item that sold
close to zero), so removing them makes the reported average WORSE (1.71 to
2.16), not better - see 2.7's 45-items-still-selling figure, which already is
that number. What was built instead is a screen-only flag, so a reader is not
left wondering why an item reads 0: `backend/app.py`'s `DISCONTINUED_DAYS`
(365) flags an item with no recorded sale in over a year as `likely_discontinued`,
shown as a notice on its own forecast page and a small tag next to its name in
a category's item list. It does not touch `fsn_class` or any reported metric -
an item flagged this way is still scored and still counted as Fast everywhere
else. It is a heuristic guess from a sales gap, not a confirmed status (a
genuinely seasonal item could trip it too), which the wording says explicitly.

### 2.10 Lowering the total when the calendar shows quieter days ahead (changed)

Every forecast so far learns one per-day rate and repeats it, so a 30-day window
full of semester-break days gets the same total as an ordinary month: the
6-month average over-forecast the categories by 75% in 2025-12-11..2026-01-09
(21 break days). The school calendar that says a break is coming is published
in advance, so `forecasting/calendar_adjust.py` uses it: it measures how much
each kind of day (enrollment, semester break, exam week, normal) sells relative
to the category's average, and scales the total by how the next 30 days compare
with the days the level was learned on. Only DOWN: the version allowed to go up
assumed a rebound after each break and was badly wrong in February and April
2026. Store closures are not used (some were recorded after the fact); leaving
them out changed nothing measurable. Items use their category's day-type
ratios, since their own days are mostly zeros.

`scripts/test_calendar_adjustment.py` (`data/calendar_adjustment_*.csv`):

| | macro MASE | MASE < 1 | pooled WMAPE | bias |
|---|---:|---:|---:|---:|
| category, before | 1.14 | 4/12 | 45.2% | -7% |
| category, lowered (shipped) | **1.09** | 6/12 | **41.8%** | -15% |
| item, before | 1.71 | 23/57 | 64.1% | -7% |
| item, lowered (shipped) | **1.67** | 24/57 | **60.3%** | -15% |

- Better for 10 of 12 categories (bootstrap 95% CI of the MASE gap -0.108 to
  -0.002) and 41 of 57 items (-0.066 to -0.007), and in both the older and the
  newer six folds at both levels. By far the biggest gain is December (category
  error 75% to 25%).
- **The cost is more under-forecasting** (-7% to -15% overall), mostly in
  break months, when the store is quiet; in the newer folds bias is about 0.
- **It was chosen after looking.** The "only down" rule came from seeing the
  uncapped version fail, so the gain is likely a little optimistic. The next
  real semester break is the first true out-of-sample test.
- **Watch the current window.** 2026-07-09..08-07 has 7 break and 5 exam days,
  so every category is lowered 4-15%. In the same season last year the two
  biggest categories (Shirts & Tops, Lanyards & IDs) were already under-forecast
  by 50-68% before any adjustment, likely the start of the school year, which
  the calendar does not flag. The adjustment was a tie in that window overall
  (it helped the over-forecast small categories), but those two may read low.
- `--model RM6_6month_180d` (step4c) and `--model topdown_tsb+prophet_shape`
  (step4) give the unadjusted forecasts.

## 3. Earlier work on this branch (unchanged)

- **Merged `origin/marco` into `neil`**, 5 conflicts. `forecasting/ml_models.py`
  kept as two incompatible modules (`ml_models.py` for this branch's per-SKU /
  pooled experiments, `ml_models_fastmoving.py` for marco's 30-day-total
  regressors); marco's category-first Forecast UI and `ComboBox` taken.
- **Marco's ML models** (XGBoost, LightGBM, RF, Ridge) all did worse than
  `rolling_mean_30` / `tsb` on the CLEAN fast-moving benchmark.
- **Marco's demand clustering** (k=5): silhouette 0.239 (below the script's own
  0.25 weak-structure line), purity 0.525.
- **Cleaning is not the bottleneck:** CLEAN vs RAW cuts MASE ~30–45%, but the best
  CLEAN method still sits at MASE 3.45 at item level.
- **MAPE is unreliable here** because small categories have small actuals, not
  zero-division; MASE (with a denominator in the scored unit) or WMAPE are the
  metrics to quote.
- **Cluster-based pooling (k=4, `forecasting/clustering.py`)** beats
  category+speed pooling for every pooled ML method (30–49% lower MASE), though
  pooled models still never beat the best single per-SKU baseline.

## 4. Reproduce

```
python scripts/step4c_category_forecast.py --no-db-write   # report only
python scripts/step4c_category_forecast.py                 # write the tables
python scripts/compare_category_forecast_methods.py        # the comparison, ~1 min
python scripts/test_category_forecast_shape.py             # day-by-day shape test, ~2 min
python scripts/test_item_forecast_methods.py               # the 47-method item comparison, ~6 min
python scripts/step4_forecast_model.py                     # item forecasts (default: blend + daily pattern), ~10 s
python scripts/test_lead_time_gap.py                        # validate with a lead-time gap (section 2.8), seconds
python scripts/test_calendar_adjustment.py                  # the calendar adjustment (section 2.10), ~1 min
```

## 5. Open work

- **Does the adviser need Prophet on the category or item screen?** The evidence
  says it costs accuracy as the source of the total (sections 2.1 and 2.7); it now
  supplies only the daily pattern. If it is required as the source of the total,
  the least bad versions are 50/50 blends: at category level Prophet + 6-month
  average (47.0% WMAPE, 11 of 12), at item level Prophet (no yearly) + TSB (mean
  MASE 1.98 against 1.71 for the default, better than Prophet's 2.52).
- **The item forecast has no seasonal term** (section 2.7). It will read low in
  busy windows. With three years of data a same-period-last-year adjustment
  becomes testable.
- **13 "Fast" items have not sold in a year.** `fsn_class` comes from full history
  (step3), so they stay Fast and appear on the item screen at zero. Someone with
  store knowledge should say which are discontinued. Not changed here.
- **The item "Reliable" tag compares against a different bar than its tooltip
  says.** `Result_Forecast_Metrics.naive_*` is the last DAY held flat for 30 days,
  the tooltip says "repeating last month's number". The category metrics use the
  last-30-days average (a harder bar). Not changed.
- **The 180-day mean cannot see enrollment peaks or breaks.** With two years of
  data a same-period-last-year adjustment is not testable yet.
- **Fix the padding at its source** (step0/step2), and in `step5_prescriptive.py`.
- **Item-level Prophet's future calendar regressors** (see 2.5).
- **65 of 519 products (12.5%) are still "Uncategorised"**; extending the keyword
  rules in `step1b_categorize_products.py` would shrink the grab-bag category.
- **~10 day-status classifications** in `data/day_status_vocabulary.csv` are
  unreviewed (e.g. "ELECTION DAY") and need someone with real store history.
- **Manuscript:** the honest ladder (item -> category, trailing average vs
  Prophet, and the harness corrections in 2.1) is not written up yet.
