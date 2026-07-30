# USTore Demand Forecasting — ETL & Analytics Pipeline

This branch (`neil`) contains the working data pipeline behind the USTore
Demand Forecasting & Prescriptive Inventory Management capstone: it turns
the raw monthly tally-sheet workbooks into a clean star-schema SQLite
database (`ustore.db`), classifies every product as Fast/Slow/Non-moving,
and produces Prophet-based 30-day forecasts for the Fast-moving SKUs.

Run the scripts below **in order** — each one reads the previous step's
output and writes into `ustore.db`. All of them are safe to re-run: they
clear their own tables/files before writing, so re-running never
duplicates data. Run `python verify_data.py` before committing any change
that touches a CSV (see Block 1 below for what it checks).

## Pipeline

| Step | Script | What it does |
|---|---|---|
| — | `create_schema.py` | Builds the empty `ustore.db` (all 5 tables). Run once, or whenever rebuilding from scratch. |
| — | `populate_dim_date.py` | Fills `Dim_Date` (1,461 rows, 2023-01-01–2026-12-31) from `calendar_ranges.csv`: calendar flags, `semester_id`/`semester_week` derived from 12 term windows, and `is_tally_date` from the sales data. |
| 0 | `step0_convert_sales_with_zeros.py` | Converts the raw TBS tally-sheet workbooks (`drive-download-.../*.xlsx`) into `USTore_sales_long_with_zeros.csv`. Per month, decides whether the sheet is a genuine daily tally (has a date column for ~most calendar days) or a sparse periodic stock-count, and only zero-fills blank/missing sale cells for the dense months. Currently: Aug–Sep 2024 stay sparse; every month Oct 2024–Jul 2026 is zero-filled. |
| 1 | `step1_apply_mapping.py` | Applies `vocab_mapping_FINAL_v5.csv` (the controlled vocabulary — 597 raw names → 540 canonical products) to both the sales and inventory CSVs, reports any unmapped raw name (should be 0), and (re)builds `Dim_Product` (519 rows — some canonical names in the mapping file have no live data behind them, which is expected). |
| — | `proportional_allocation.py` | Splits price-grouped tally rows (a single row covering several SKUs sharing a price point) into per-SKU rows, weighted by each SKU's beginning-of-month stock. Outputs `USTore_sales_long_allocated.csv` (+ `allocation_audit.csv` documenting every split). Run with `--sales USTore_sales_long_with_zeros.csv` so zero-quantity rows are preserved through the split (a grouped row with 0 units on a given day now correctly emits 0 for every constituent, instead of being dropped). |
| 2 | `step2_load_fact_sales.py` | Loads the allocated CSV into `Fact_Sales` (84,175 rows: 68,321 zero-quantity + 15,854 positive; sums to **89,232** units — see Block 0 below for why that's not the older 88,481), routing unresolvable rows to `Exception_Log` instead of dropping them. Derives `cumulative_monthly_units` and `daily_depletion_rate`. |
| 3 | `step3_fsn_classification.py` | Computes ADUS (Average Daily Units Sold) per SKU, weighting imputed/allocated rows at 0.5, and classifies Fast/Slow/Non-moving at the 80th-percentile ADUS cutoff (currently F=58, S=230, N=231). Flags High-Velocity-Limited (HVL) items with thin history. Writes `fsn_class`/`is_hvl` back to `Dim_Product`. |
| 4 | `step4_prophet_forecast.py` | Fits Prophet per Fast SKU with a data-sufficiency tier, keyed on **distinct sale-days (quantity_sold > 0), not raw row count** — currently 38 standard (60+ sale-days), 10 simplified (30-59), 10 rolling-average (<30, no real model). Validates against a naive baseline, writes 30-day forecasts + metrics to `Result_Forecast` / `Result_Forecast_Metrics`. Requires `cmdstan` (see below). |

Supporting/one-off scripts still in the repo: `build_vocab_mapping.py` /
`diag_token_match.py` (fuzzy-matching tools used to build and refine the
vocabulary mapping — not part of the regular run order, only needed if
you're revisiting the vocabulary itself).

## Key design decisions worth knowing before you touch this

- **Zero-fill is per-month, not global.** The original converter dropped
  every blank/zero cell, which made genuinely daily data (Oct 2024
  onward) look like sparse episodic tallies. `step0` fixes this, but
  only for months where the source sheet actually has a date column for
  most of the month — verified against the real workbooks, not assumed.
- **Fact_Sales rows now include true zero-sale days.** Any code reading
  `Fact_Sales` should NOT assume every row is a sale — `quantity_sold`
  can legitimately be 0.
- **ADUS denominator = distinct dates with any Fact_Sales row**, not a
  fixed calendar window. This is quietly "calendar days" for the dense
  months and "sale-days only" for the two sparse ones — tested and
  confirmed this doesn't change any SKU's F/S/N class vs. a uniform
  calendar-day denominator, so the simpler current implementation was
  kept.
- **Prophet needs `cmdstan`** (the compiled Stan backend), which is not
  a plain `pip install`. On Windows: `pip install prophet`, then
  `python -m cmdstanpy.install_cxx_toolchain` (installs RTools/g++) and
  `python -m cmdstanpy.install_cmdstan` with `RTools40/usr/bin` and
  `RTools40/mingw64/bin` on `PATH` and `MAKE=make` set. This is a
  one-time ~20-30 minute build.
- **Even with clean daily data, Prophet mostly doesn't beat a naive
  "same as yesterday" baseline** for these SKUs (9/48 beat naive, 0/48
  hit MAPE≤20%). This is a real finding, not a bug — most of these
  items are erratic/low-volume, and once the data honestly includes
  zero-sale days, persistence becomes a very strong baseline. Worth
  stating plainly in the write-up rather than tuned away.
- **`is_tally_date` = 608, not 411.** 411 is distinct dates with a
  recorded *sale*; 608 is distinct dates the store actually *tallied*
  (from the zero-inclusive sales file), which is what "tally date"
  should mean once zero-fill exists. Most months (Oct 2024 onward) were
  tallied on nearly every calendar day whether or not anything sold.
- **`semester_week`'s week-1 origin is enrollment, not the first class
  day.** A term's week count starts at the earliest `calendar_ranges.csv`
  row for that `semester_id`, which in practice is the enrollment-period
  range. Chosen deliberately (enrollment is when the sales surge
  happens), but it's a choice, not a fact — must match whatever
  Chapter 4 states.
- **`semester_week`'s regressor values changed substantially** after the
  Block 1 fix below (512 → 1,453 non-null rows) — a lot of historical
  training data that Prophet fit on previously had `semester_week`
  silently defaulted to 0. **Phase 3 (`step4_prophet_forecast.py`) has
  not been re-run since this fix** - the forecasts/metrics currently in
  `Result_Forecast(_Metrics)` predate it.

## Files intentionally not committed

Regenerable intermediate outputs (`*_mapped.csv` from Step 1) and
superseded vocabulary-mapping versions are left out to keep the repo
readable — re-running the pipeline reproduces them. `ustore.db` itself
is gitignored (binary, machine-specific); run `create_schema.py` →
`populate_dim_date.py` → the pipeline above to rebuild it from scratch.

## Status against `CODE_WORK_PLAN_v2.md`

That file is a team audit/remediation plan; this section tracks what's
actually been done against it so nobody re-does or skips work.

**🔴 Not done — highest priority, do this first:** Block 0.1. The plan
states this repo was public with real client data (supplier names, unit
prices, sales volumes). Making it private is a GitHub account-level
setting, not something done from inside this branch — needs a human to
verify and act.

**Done:**
- **Block 0.3** (push local work), **0.5** (ran the 4 verification
  queries — results below)
- **Block 1, all four items** (1.1 dates → ISO, 1.2 `verify_data.py`
  guard, 1.3 `populate_dim_date.py` runs again, 1.4 `TERM_STARTS`/
  `is_tally_date`/`semester_id` fixed) — see the Pipeline table and
  design-decisions above for specifics
- **Block 2.1** 🔴 (zero-fill inflating Prophet's tier counts) — fixed,
  see the Step 4 row above
- **Block 2.3** (`is_hvl` as a boolean modifier, not a 4th `fsn_class`
  value) — already correct
- **Block 2.5** (partial - `is_store_closed` dropped as an unusable
  Prophet regressor, constant zero for most SKUs)
- **Block 6.1** (this README)

**Block 0.5 verification results** (`ustore.db`, current state):

| Check | Plan's target | Actual |
|---|---:|---:|
| `semester_week` non-null | ~1400 | 1,453 |
| `MAX(semester_week)` | 18-26 | 23 |
| `is_tally_date = 1` | 411 | 608 (see design decisions above) |
| `SUM(quantity_sold)` | 88,481 | 89,232 (explained — 2 legitimately different months, checked for double-counting, not corruption) |

**Not done — everything else in the plan:**
- Block 1.1's broader scope (reformatting the sales/inventory CSVs
  themselves to ISO) was deliberately NOT done — those files aren't
  actually blocking anything (the whole pipeline already parses
  DD/MM/YYYY explicitly and correctly), and reformatting them would mean
  touching validated pipeline code for no functional benefit. Only
  `calendar_ranges.csv` (the genuine blocker for `populate_dim_date.py`)
  was converted.
- Block 2.2 (map-before-allocate reordering), 2.4 (stockout/censoring
  flags), 2.6 (`days_of_supply` + gap-handling rules), 2.7 (supplier
  name consolidation, 40 strings → ~15 real suppliers)
- Block 3 (inventory coverage — only 42 canonical items appear in both
  sales and inventory; needs a group decision + USTore staff input)
- Block 4 (the forecasting strategy overhaul: adviser conversation on
  the MAPE≤20% criterion, retargeting to 30-day aggregate demand,
  `prophet_flatlog`, genuine multi-fold walk-forward validation,
  Croston/SBA for intermittent-demand SKUs, the 8-method benchmark
  writeup) — **none of this is in the current Phase 3 script.** It's
  still vanilla-ish Prophet with an 80/20 holdout.
- Block 5 (USTore site visit — lead time, ordering/holding cost,
  supplier confirmations, borderline-FSN review)
- Block 6.2 (`FORECASTING_OPTIONS.md`, `prophet_diagnostic.py`,
  `model_benchmark.py`, `PROJECT_LOG.md`, etc. - these don't exist
  anywhere in this branch; looks like a parallel workstream from another
  teammate that hasn't been merged in), 6.3 (`.mailmap`), 6.4 (reconcile
  `USTore_Build_Plan.pdf`)
