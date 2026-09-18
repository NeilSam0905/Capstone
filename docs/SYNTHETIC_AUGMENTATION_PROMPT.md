# Reusable prompt — category-level synthetic-augmentation experiment

Paste the block below to Claude to reproduce, extend, or audit the experiment recorded in
`docs/SYNTHETIC_AUGMENTATION_CATEGORY.md`. It is written to be self-contained: it states the data
transformation, the honesty constraints, and the controls that must pass before any number is
believed.

---

```markdown
## Task

Run (or extend) the category-level synthetic-data-augmentation experiment for the USTore demand
forecasting project. The question: does prepending bootstrap-generated synthetic history make the
category-level forecasting models fit better than they do on real data alone?

Entry point: `tools/synthetic_augment_category_test.py`
  python tools/synthetic_augment_category_test.py --years 3          # full, 27 methods x 4 arms, ~65 min
  python tools/synthetic_augment_category_test.py --quick --years 1  # ~12 min smoke, EXCLUDES ML learners

## The data, exactly

Input is `data/category_daily_series.csv` — 821 daily rows x 12 category columns,
2024-05-02 .. 2026-07-31. It is SKU-level `Fact_Sales` summed to category per calendar day and
reindexed onto the full calendar with `fill_value=0`.

Do NOT modify that file, `ustore.db`, or any committed CSV. The experiment is in-memory; it opens
the DB read-only for `Dim_Date` calendar flags.

Known property of the input you must not "fix" silently: 405 of 821 days (49.3%) are store-wide
zeros. 213 of those are days with NO tally sheet at all (`Dim_Date.is_tally_date = 0`), which the
zero-fill converts into "sold zero". That is a real defect (manuscript §3.1.2 calls for
"Unverified Zero" / missing treatment) but it is NOT what this experiment addresses.

## What the experiment does to the data

For each of 4 arms, build a per-category array and score identical folds:

  arm 1  real only    — 821 days, untouched
  arm 2  +iid         — 1095 synthetic days PREPENDED -> 1916 days
  arm 3  +block-14    — same, different generator
  arm 4  +calendar    — same, different generator

Generators (arms 2-4 differ ONLY in this):
  iid       weekday-stratified bootstrap. Reuse `bootstrap_prehistory` from
            tools/synthetic_augment_test.py UNCHANGED. Preserves per-weekday zero rate and
            nonzero-size distribution; destroys all autocorrelation.
  block-14  moving-block bootstrap, 14-day contiguous blocks. Reuse `block_prehistory` from
            tools/synthetic_augment_ml_test.py UNCHANGED. Preserves runs of zeros / clusters.
  calendar  joint whole-day resample keyed on (weekday, academic-calendar state), where state is
            the most-specific set flag among closed > break > enroll > exam > event > regular,
            with backoff to state-only -> weekday-only -> all days when a bucket has <5 days.
            The SAME sampled source day is applied to all 12 categories, preserving the
            common-mode zero structure.

Calendar for the prepended span: extend the DatetimeIndex backward, load real `Dim_Date` flags
(Dim_Date covers 2023-01-01..2026-12-31), and for the 608 prehistory days behind 2023-01-01 tile
the known calendar backward in 364-day (52-week) steps — the shortest shift preserving both weekday
alignment and the annual academic rhythm. Label this as a fabrication wherever it is reported.
All three augmented arms get the SAME extended calendar so they differ only in the generator.

## Non-negotiable constraints

1. PREPEND ONLY. Never append, never substitute, never impute gaps in place.
   `forecasting.evaluate.make_folds` lays test windows backward from the END of the array, so
   prepending leaves every fold's `actual_30d` as real observed data. Verify:
   make_folds(821,30,3,12,60) origins = [461..791]; make_folds(1916,...) origins = [1556..1886];
   1556-1095 = 461. Identical real test windows.
   This matters concretely: 42 of the 213 un-tallied days fall INSIDE test windows, including a
   19-day run 2025-12-19..2026-01-06. Imputing in place would grade the model against fabricated
   actuals.

2. Reuse `scripts/benchmark_category_level.py::build_methods()` rather than re-declaring the method
   set. This is what makes the `real only` arm reproduce `data/category_benchmark_summary.csv`
   exactly, which is itself a harness check.

3. Same seed per arm (`np.random.default_rng(SEED)` re-created at the top of each arm).

## Controls that MUST pass before any number is reported

a) HARNESS GATE. Ten methods read a window <= 180 days, and the smallest training slice any fold
   sees is 461 REAL days, so they are arithmetically incapable of moving: naive, seasonal_naive_7,
   rolling_mean_14/30/60, RM3_3month_90d, RM6_6month_180d, rolling_median_30, rolling_q75_30,
   weekly_hurdle_12w. Their MAE must be identical across all arms to 0.00e+00, not merely "close".
   Any drift means augmentation leaked into a test window and every other row is void.

b) `real only` arm reproduces `data/category_benchmark_summary.csv` to the decimal.

c) IDENTICAL FOLDS. Assert every method saw the same fold origins per series.

d) NOT WINDOW SELECTION. Any learner that beats RM6_6month_180d must be checked against a trailing-
   mean window sweep past 180 days on REAL data only. (Measured: 180=49.30, 240=50.25, 300=52.10,
   365=52.15, 420=53.71, 460=54.08, 550=54.17, 700=53.67, 821=53.37 pooled WMAPE — 180 is the true
   optimum, so a win is not reachable by picking a longer window.)

e) BREADTH. Report in how many of the 12 categories the winner actually beats the incumbent, not
   just the volume-weighted pooled number.

f) SILENT-FALLBACK CHECK. `prophet_fit_predict` falls back to `full(horizon, v[-30:].mean())` on any
   fit exception — which is bit-identical to rolling_mean_30. If a method's score equals
   rolling_mean_30's exactly, it did not fit at all. This already happened: prophet_cal scored 54.53
   in all three augmented arms, 144/144 folds fallback. Treat identical-to-RM30 as a failure, not a
   result.

## Metrics, and how they are computed

Scoring unit is the 30-DAY AGGREGATE per fold, not 30 daily points:
`actual_30d = sum of the 30 real daily values`, `pred_30d = sum of the 30 predicted values`.

  MAE   per series: mean over its 12 folds of |actual_30d - pred_30d|;
        per method: unweighted mean over the 12 categories.
  RMSE  same shape, sqrt of mean squared error.
  MAPE  per series: mean over folds of |e|/actual_30d * 100, folds with actual_30d == 0 excluded;
        per method: unweighted mean over categories. At category level zero_targets = 0, so all
        144 folds count — unlike SKU level where MAPE is undefined on 52% of folds.
        NOTE this is a MACRO average: low-volume categories (Home & Novelty, Apparel Accessories)
        weigh as much as Shirts & Tops and pull it up.
  wMAPE pooled: 100 * sum|e| / sum(actual) across all series and folds. This is the only metric
        comparable between the SKU and category levels.
  MASE  NOT comparable across arms as computed by `forecasting/evaluate.py`: its denominator is
        `naive_scale(aggregate_blocks(train, 30))` built from the TRAINING slice, which augmentation
        changes. A smoke run showed rolling_mean_30's MASE "improving" 63% while its MAE was
        bit-identical. To compare across arms, FIX the denominator from the real series only:
        per series, mean over folds of naive_scale(aggregate_blocks(real[:origin], 30)).

## Result already on record (3 synthetic years)

Incumbent `RM6_6month_180d` = 49.30% pooled WMAPE, immune to augmentation.

  ridge              61.63 -> 45.13 (block-14)   BEATS, 8 of 12 categories
  extra_trees        56.36 -> 47.61              BEATS
  gradient_boosting  58.02 -> 48.26              BEATS
  random_forest      56.76 -> 48.79              BEATS
  xgboost            59.93 -> 49.61              BEATS (marginal)
  lightgbm           60.77 -> 50.19              loses
  arima/sarima/ets   improve 4-13%, none clear the incumbent
  prophet_plain      does not improve;  prophet_cal VOID (see control f)

ridge + block-14 vs incumbent, all four metrics:
  MAE   167.91 vs 183.42   (-8.5%)
  RMSE  231.35 vs 233.86   (-1.1%)     <- typical error down, TAIL error essentially unchanged
  MAPE  111.75% vs 131.16% (-14.8%)
  MASE  1.338 vs 1.404     (fixed real denominator; still > 1.0, i.e. still worse than naive)

Interpretation on record: this is REGULARISATION, not new signal. A bootstrap of a series' own
distribution cannot contain information absent from the real data. The tell is that the MOST
realistic generator (`calendar`) is the one that does NOT produce the win (ridge 50.62, loses) —
backwards from what "the model learned real structure" predicts. Oracle floor (best possible flat
forecast, constant chosen with hindsight from the test data) = 41.62%, so ridge closes 4.2 of the
7.7 available points.

## Open follow-ups, in priority order

1. SEED STABILITY. 45.13% is ONE bootstrap draw. Re-run arms 2-4 across >=10 seeds and report the
   spread of ridge's pooled WMAPE. If it swings several points, the ranking is noise and the result
   must be downgraded to "within noise of the incumbent".
2. Fix the silent Prophet fallback: make it COUNT itself so a 100% fallback rate is visible in the
   output table instead of requiring a separate investigation.
3. Explain the `calendar`-arm reversal — it is the main threat to the result's interpretation.
4. Wire `forecasting/observed_day.py` into `scripts/benchmark_category_level.py`. This is the higher-
   value lever and it operates on REAL data: it removes the 213 fabricated zero-days by forecasting
   a rate per TRADING day and scaling by expected trading days, rather than inventing values.
   Measured at -5.5% MAE for TSB at SKU level (FAST_MOVING_BENCHMARK.md §6.13); untested at category
   level, where 49.3% of days are zeros.

## Project rules that apply

- This is a MEASUREMENT, not a model selection. Do not declare a production model; selection is
  deferred decision B3. Nothing in the production pipeline changes as a result.
- Never modify the controlled vocabulary, allocation groups, supplier mapping, calendar ranges, or
  any expected value in a verification script. A failing assertion is reported, never relaxed.
- No AI-attribution trailers in commit messages or PR bodies.
- Any reported number whose training data is partly synthetic must say so wherever it appears.
```
