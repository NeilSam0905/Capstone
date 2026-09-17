# Colour-aware extraction rebuild + category-level Prophet

A re-do of the raw-workbook extraction that reads the **store's operating
status out of the cell colours**, plus a Prophet model fitted per forecast
category on the corrected series.

```
python scripts/rebuild_extract_tbs.py        # extract (add --no-db for a dry run)
python scripts/forecast_category_prophet.py  # forecast + metrics
```

Everything is **additive**. No existing table, CSV, mapping file or script was
modified. Per `CLAUDE.md`, `vocab_mapping_FINAL_v5.csv`, `supplier_mapping.csv`,
`allocation_groups.csv` and `calendar_ranges.csv` are read-only inputs here.

---

## 1. The finding that drove the design

Every TBS sheet encodes each date's operating status in the **fill colour of
its header cell**, with a legend block on the right giving the meaning. That
legend is the only record in this project of which days the store was actually
shut — `Closure_Log` is empty and `Dim_Date.is_store_closed` was populated from
the published academic calendar alone.

**Colours are not consistent across sheets.** Measured over the whole corpus:

| colour | means, depending on the sheet |
|---|---|
| `FF00FF00` green | "USTET SELLING DAY" / "ELECTION DAY" / "HOLIDAY" / "UST VOLLEYBALL GAME SELLING" |
| `FFFF00FF` magenta | "CLASS SUSPENSION" / "PREPARATION FOR USTET" / "CHINESE NEW YEAR" / "UST ALAB: RETREAT 2026" |
| `FFFF0000` red | "NO OPERATION" — the only corpus-stable one (15 sheets) |

So a global colour table would be wrong about half the time. **The legend is
parsed per sheet** and a colour is resolved only within the sheet it came from.
Classification is then done on the *label text*, not the colour.

44 distinct labels were found and written to `data/day_status_vocabulary.csv`
with an inferred status, `reviewed=no`, and a `note` column. **That file is a
human-editable override**: if it exists, its `status` column wins over the
keyword rules, and a later run will not overwrite it. Two calls are worth your
review — `ELECTION DAY` (classified CLOSED as a national non-working holiday,
but coloured like a selling day) and `AFTERNOON SELLING FRAY MHEL'S BDAY`.

---

## 2. Day statuses, and why `demand_observable` is the point

| status | days | demand observable? | meaning |
|---|---:|:--:|---|
| `NORMAL` | 459 | yes | ordinary trading day |
| `SELLING_SPECIAL` | 9 | yes | USTET / volleyball / MathEd selling |
| `SUSPENSION` | 33 | yes | class suspension, virtual mode, transport strike — campus empty, store staffed |
| `OPS_NONSELLING` | 33 | **NO** | inventory, maintenance, cleaning, bench-marking, assemblies |
| `CLOSED` | 67 | **NO** | no operation, holidays |

**100 of 601 dated days are days on which a zero is a fact about the store, not
about demand.** The existing builders reindex onto the full calendar with
`fill_value=0` and hand all of them to the model as "sold zero". This pipeline
flags them instead and the forecaster drops them.

That matters because of what was already measured in
`docs/SYNTHETIC_AUGMENTATION_CATEGORY.md`: 405 of 821 days are store-wide zeros
and **84.1% of all category-level zero cells are common-mode** — the categories
go quiet together because the *store* went quiet.

`SUSPENSION` is deliberately kept observable: the store was open, and low
demand on an empty campus is real demand information.

---

## 3. The cross-check

Most sheets state their own answer in a "TOTAL SELLING DAYS: N DAYS" cell. The
parser's count of `traded AND NORMAL` days is compared against it:

```
exact match 5/15 (33%) | within 1 day 12/15 (80%)
```

December 2025 — the sheet in the screenshot — matches exactly at 11.

This is an **independent** check: the author counted by hand. Three sheets are
off by more than a day and are printed for review rather than reconciled:

| sheet | stated | parsed | |
|---|---:|---:|---|
| MAY 2025 - TBS | 22 | 24 | +2 |
| JUNE 2026 - TBS | 18 | 22 | +4 |
| JULY 2026 - TBS | 18 | 5 | **−13** — the sheet holds data only to 2026-07-08 |

The residual ±1 cases are plausibly the author's own judgement (half-days, a
special event that also traded normally). **Nothing was fitted to close the
gap.**

---

## 4. The 2023 batch file is not resampled

`2023 total sales by batch (3).xlsx` holds per-batch totals with **no dates at
all**. The previous pipeline spread these across daily frequencies, inventing a
within-batch shape the source never recorded.

They now load into `Fact_Batch_Sales` as aggregates — **101 rows, 6,629 units,
6 batches** — and the table has *no date column*, so they cannot leak into a
daily series. The file contains three different layouts (February's
`Item | Total Quantity | Amount | Item Price` block; March–May's
`DATE | Total Quantity | …`; June/July-Aug's identical layout with the item
column headed `JUNE`), all three of which are handled.

---

## 5. Schema additions

```sql
Dim_Day_Status (calendar_date UNIQUE, day_status, legend_label, fill_colour,
                traded, units_that_day, demand_observable, is_store_closed,
                source_file, source_sheet)          -- 601 rows

Fact_Batch_Sales (batch_label, supplier_name, item_name, raw_item_name,
                  total_quantity, amount_php, item_price_php,
                  source_file, grain='batch_aggregate')   -- 101 rows, no date

Result_Category_Prophet_Metrics (forecast_category, tier, n_folds,
                                 mape_pct, mae, mase, rmse, …)  -- 12 rows
```

`Dim_Day_Status.is_store_closed` is the evidence-based version of the flag
`Dim_Date` already carries. It is written to a **separate table** rather than
over `Dim_Date` so the published-calendar version and the observed version can
be compared before anything is overwritten.

---

## 6. Forecasting results

One Prophet model per `forecast_category`, `Dim_Date` regressors
(`is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`),
walk-forward, horizon 30, 12 folds, min_train 60. `semester_week` is excluded —
Divergence #8 records that its continuous form extrapolates badly across term
resets.

| category | tier | MAPE % | MAE | MASE | RMSE |
|---|---|---:|---:|---:|---:|
| Outerwear | full | **36.49** | 84.84 | 0.88 | 104.32 |
| Uncategorised | full | 55.51 | 59.80 | 1.23 | 70.14 |
| Shirts & Tops | full | 60.82 | 653.09 | 1.36 | 787.72 |
| Drinkware | full | 60.83 | 96.68 | 0.87 | 116.62 |
| Keychains & Charms | full | 69.66 | 227.82 | 0.86 | 290.14 |
| Stationery | full | 74.16 | 419.66 | 0.89 | 584.15 |
| Lanyards & IDs | full | 78.19 | 547.47 | 1.55 | 708.06 |
| Bags | full | 92.95 | 239.60 | 0.80 | 339.33 |
| Umbrellas & Gear | full | 107.93 | 100.86 | **0.68** | 128.09 |
| Plush & Souvenirs | full | 316.28 | 282.93 | 0.76 | 436.96 |
| Apparel Accessories | full | 493.25 | 47.64 | **0.67** | 54.69 |
| Home & Novelty | reduced | 792.51 | 99.48 | 1.36 | 122.81 |

**Pooled:** MAPE 186.55% (macro) · MAE 238.32 · MASE **0.99** · RMSE 311.92 ·
WMAPE 62.36%

**8 of 12 categories have MASE < 1**, i.e. they beat the naive benchmark. That
is the first time anything in this project has done so — Divergences #6 and #22
record MASE < 1.0 as previously unreachable.

### The sufficiency tier, and the failure that forced it

The first run produced a **6,999.6-unit forecast for Home & Novelty against an
actual of 13** (MAPE 7,101%, MASE 11.84). Cause: 116 trading days with sales,
mean 7.3 units, one 150-unit spike, and barely two annual cycles of span — the
yearly Fourier terms and linear trend were fitted on almost no evidence and
extrapolated without restraint.

Manuscript §3.3.2's own data-sufficiency tiers were applied at category grain:

| obs | tier | model |
|---|---|---|
| ≥120 | full | weekly + yearly seasonality, linear growth |
| 60–119 | reduced | weekly only, **flat growth**, no changepoints |
| <60 | none | trailing mean, flagged as a heuristic |

Flat growth is the load-bearing part. Effect on Home & Novelty:
MAPE 7,101% → 792%, MAE 867 → 99, MASE 11.84 → 1.36. Macro MASE 1.89 → 0.99.

A `plausibility_cap` (3× the largest historical 30-day total) is also applied
and **counted** — it bound on 0 of 142 folds in this run, which is the result
you want from a guard.

---

## 7. What these numbers do and do not say

**Do not rank WMAPE 62.36% against the 49.30% in
`data/category_benchmark_summary.csv`.** That table scores a zero-filled
calendar series over 2024-05-02..2026-07-31; this one scores trading days only
over 2024-08-06..2026-07-08. Different targets, different denominators —
the two answer different questions. The script prints this warning itself.

The same caution applies to MASE: the denominator here is built from 30-day
blocks of the *trading-day* series, so it is internally consistent but not
interchangeable with MASE elsewhere in the project.

**MAPE remains far above §3.3.4's ≤20% criterion** — 36% at best, 187% macro.
The small-volume categories (Home & Novelty, Apparel Accessories, Plush &
Souvenirs) dominate the macro average because their denominators are tiny; the
volume-weighted figure is less alarming and more honest about where the money
is. Divergence #6 already records the ≤20% target as unreachable on this data,
and nothing here changes that.

**Known gaps:** the May 2024 DSR workbook is skipped (superseded by TBS), so the
series starts 2024-08-06 rather than 2024-05-02; July 2026 holds data only to
the 8th; and the `day_status_vocabulary.csv` calls have not yet been reviewed by
anyone who knows the store.
