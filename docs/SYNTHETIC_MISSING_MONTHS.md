# Does filling the missing 2023–2024 months with synthetic data improve the forecasts?

**No, not for the models the app uses.** Filling the 18 months with no tally data with calendar-matched
synthetic sales changes the shipped category model's mean MASE from **1.074 to 1.076** and the shipped item
model's from **1.668 to 1.666**. Both changes are well inside noise: the 95% bootstrap interval of the gap
straddles zero at both levels. Some models that read longer history do move, by up to 3%, but none of them
gets close to the shipped models.

**Reproduce:**

```
python tools/synthetic_missing_months_test.py                 # 10 seeds + Prophet, ~4 min on 4 workers
python tools/synthetic_missing_months_test.py --no-prophet    # ~30 s
```

Use `--jobs 4` or fewer on a 32 GB machine.

**Workbook for the adviser:** `python scripts/build_synthetic_results_workbook.py` writes
`data/USTore_with_Synthetic_Current_Model_Results.xlsx`. It has the same layout as
`USTore_Current_Model_Results.xlsx`, with every score shown for real data only and for real + synthetic
data, plus tabs for all models tested and for the synthetic data itself. Run the experiment first. At 12 workers, the Windows paging file ran out.

This is a **measurement, not a model selection**. Nothing in the production pipeline reads the synthetic
file, and `ustore.db` is opened read-only. Any number below whose training data is partly synthetic is
labelled as such.

---

## 1. The missing months

`Dim_Date` starts on 2023-01-01, but `Fact_Sales` starts on 2024-05-02, and June and July 2024 have no rows:

| span | months | what production does with it today |
|---|---:|---|
| 2023-01 .. 2024-04 | 16 | nothing: the series starts on 2024-05-02 |
| 2024-06 .. 2024-07 | 2 | zero-filled, so models read it as "sold nothing" |

Aug–Dec 2024 are only partly tallied, but they do have real sheets, so they were left alone. Aug and Sep 2024
are the sparse periodic stock counts described in `step0_convert_sales_with_zeros.py`.

## 2. The synthetic data: `data/synthetic_missing_months_2023_2024.csv`

The file covers 547 days, 64,287 units and 11,994 product-day rows, with sales > 0 only. A day with no row
for a product means 0 for that product. The per-day log, `data/synthetic_missing_months_days.csv`, gives the
source day and match level for each of the 547 dates.

**Generator (calendar-matched whole-day resampling).** Each missing date copies one real store-day across
**all products at once**. This keeps category totals and the store-wide zero pattern realistic. The source
day is drawn at random from real days that match the missing date. The script tries the most specific match
first and falls back to the next only when fewer than 3 real days match:

| level | match on | dates |
|---|---|---:|
| L1 | month of year + weekday + school-calendar day type | 236 |
| L2 | weekday + day type | 228 |
| L3 | weekday | 69 |
| — | `is_store_closed = 1` in Dim_Date → zero sales | 14 |

Day types (enrollment > break > exam > normal) come from `Dim_Date` in the same way that
`forecasting/calendar_adjust.py` reads them. The 2023–24 calendar is the published one from
`calendar_ranges.csv`, so it is not tiled or guessed.

**Source days are restricted to:**
- tallied days (`is_tally_date = 1`), so zero-filled days are excluded;
- days outside Aug and Sep 2024;
- days **before 2025-07-14, when the first test window opens**.

Because of the last restriction, the synthetic data cannot carry information from any day that a forecast
is scored on, in any fold.

**Sanity check.** The synthetic per-weekday levels track the real ones:

| weekday (units/day) | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---:|---:|---:|---:|---:|---:|---:|
| real days | 89 | 119 | 116 | 186 | 160 | 79 | 101 |
| synthetic | 74 | 131 | 111 | 194 | 159 | 85 | 71 |

Synthetic months total 2.2k–5.8k units, which is inside the range of real full months, 1.2k–6.8k.

**What it fabricates, stated plainly:**
- It assumes no trend, so 2023 sells at the May 2024 – Jul 2025 level.
- Items launched later still receive synthetic 2023 sales whenever they sold on the source day.

## 3. Controls: all passed

```
[PASS] real arm reproduces stored category MASE (max gap 0.0e+00, 12 series)
[PASS] real arm reproduces stored item MASE (max gap 0.0e+00, 57 series)
[PASS] identical real test windows and actuals (74,016/74,016 rows)
[PASS] immune control '6-mo avg, no calendar': max prediction drift 0.00e+00
[PASS] immune control 'Top-down share, no TSB': max prediction drift 0.00e+00
```

MASE uses the **real arm's** naive scale in every arm. The harness computes the scale from the training
slice, so without this fix synthetic data would change MASE even for a model whose predictions stayed the
same. Every method except Prophet was run with 10 generator seeds. Prophet used one seed because of its
run time.

## 4. Result

The table covers 30-day aggregate folds (up to 12 per series, 2025-07-14 → 2026-07-08): 12 categories and
the 57 Fast items with a MASE scale. "+synthetic" is the mean over 10 seeds, with the min–max across seeds
in brackets.

| level | method | real only | +synthetic (seed range) | change | pooled WMAPE real → syn |
|---|---|---:|---:|---:|---:|
| category | **Shipped: 6-mo avg + calendar** | **1.074** | 1.076 (1.074–1.077) | +0.2% | 42.1 → 42.0 |
| category | 6-mo avg, ratios from all history | 1.085 | 1.100 (1.086–1.110) | +1.4% | 42.2 → 43.0 |
| category | 12-mo avg + calendar | 1.128 | 1.125 (1.122–1.129) | −0.2% | 46.3 → 46.1 |
| category | 6-mo avg × seasonal index | 1.135 | 1.137 (1.115–1.157) | +0.1% | 47.9 → 47.5 |
| category | Prophet weekly + yearly + calendar | 1.300 | 1.286 (1 seed) | −1.1% | 51.6 → **53.7** |
| category | Ridge (won the earlier bootstrap test) | 1.326 | 1.306 (1.236–1.388) | −1.5% | 54.3 → 54.2 |
| category | Same 30 days last year | 1.440 | 1.399 (1.367–1.428) | −2.8% | 60.7 → 58.7 |
| item | **Shipped: top-down + TSB + calendar** | **1.668** | 1.666 (1.664–1.667) | −0.1% | 60.1 → 60.0 |
| item | Shipped, ratios from all history | 1.668 | 1.673 (1.668–1.677) | +0.3% | 59.9 → 61.2 |
| item | 6-mo avg × category seasonal index | 2.203 | 2.203 (2.193–2.209) | 0.0% | 73.7 → 73.4 |
| item | 6-mo avg × item seasonal index | 2.304 | 2.306 (2.301–2.310) | +0.1% | 76.3 → 76.1 |
| item | 12-mo avg + calendar | 2.495 | 2.496 (2.495–2.498) | +0.1% | 84.6 → 84.5 |
| item | Prophet weekly + yearly + calendar | 2.515 | 2.567 (1 seed) | **+2.1%** | 87.8 → **93.2** |
| item | Same 30 days last year | 3.014 | 3.030 (3.010–3.060) | +0.5% | 105.6 → 105.0 |

Bootstrap of the seed-0 MASE gap (synthetic − real), with series resampled:

| model | gap 95% CI |
|---|---|
| shipped category model | −0.001 to +0.002 |
| shipped item model | −0.003 to +0.001 |

On seed 0, the shipped category model gets better in 5 categories and worse in 7. The shipped item model
gets better on 18 items and worse on 16.

Prophet fell back silently to the 30-day mean on 27 of 696 item folds in the real arm and 16 in the synthetic
arm. It never fell back on the category folds.

## 5. Why the shipped models do not move

This was predictable before the run, and the run confirms it:
- The category model reads a 180-day average.
- The item model reads a 180-day category level × a 30-day share, blended with TSB at α = 0.05. TSB weights
  shrink to ~1e-10 after the 438 days before the first test window.
- The calendar adjustment measures its day-type ratios over the last 365 days.

So the 2023 – Apr 2024 synthetic data is **invisible** to every shipped component. The Jun–Jul 2024 fill
reaches only the calendar ratios of the earliest folds, which is why the predictions drift slightly (and
not at all for the immune controls).

This matches `scripts/test_history_length.py` on real data: category accuracy stops improving at about 9–12
months of history, and item accuracy at about 6 months.

## 6. What the models that can read more history show

- **Seasonal models** gain the most. "Same 30 days last year" improves 2.8% at category level because its
  earliest folds were reading the zero-filled July 2024 gap. It is still 30% worse than the shipped model.
- **Ridge and Prophet** improve slightly on MASE at category level. Ridge's seed range (1.236–1.388) is wider
  than its gain. Prophet's WMAPE gets **worse** (51.6 → 53.7), and item-level Prophet is worse on both
  metrics.
- **Measuring the calendar ratios over all history gets worse with synthetic data** at both levels. The
  synthetic history dilutes the recent day-type ratios with older resampled days, and the recent ratios
  describe the forecast window better.

None of these gains is real signal. A resample of the store's own days cannot contain a pattern that the
real days lack. This agrees with `docs/SYNTHETIC_AUGMENTATION_CATEGORY.md` §6 and
`docs/SPARSE_DEMAND_EXPERIMENTS.md` §4.

## 7. What this means

- **Keep the shipped models trained on real data only.** Synthetic months give no measurable gain, and
  they would put fabricated data into a result that has to be defended in Chapter 4.
- **Do not claim that the missing 2023–24 months are what holds accuracy back.** The shipped models cannot
  see that far back, and the models that can see it do not beat the shipped ones even with it filled in.
- **The Jun–Jul 2024 zero-fill is still a data-quality question for USTore** (see `FORECAST_EXPERIMENT_AUDIT.md`
  recommendation 3). This experiment shows that filling it changes nothing that ships. It does not show that
  zero is the right value.
