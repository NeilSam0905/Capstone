# USTore Demand Forecasting — ETL & Analytics Pipeline

This branch (`neil`) contains the working data pipeline behind the USTore
Demand Forecasting & Prescriptive Inventory Management capstone: it turns
the raw monthly tally-sheet workbooks into a clean star-schema SQLite
database (`ustore.db`), classifies every product as Fast/Slow/Non-moving,
and produces Prophet-based 30-day forecasts for the Fast-moving SKUs.

Run the scripts below **in order** — each one reads the previous step's
output and writes into `ustore.db`. All of them are safe to re-run: they
clear their own tables/files before writing, so re-running never
duplicates data.

## Pipeline

| Step | Script | What it does |
|---|---|---|
| 0 | `step0_convert_sales_with_zeros.py` | Converts the raw TBS tally-sheet workbooks (`drive-download-.../*.xlsx`) into `USTore_sales_long_with_zeros.csv`. Per month, decides whether the sheet is a genuine daily tally (has a date column for ~most calendar days) or a sparse periodic stock-count, and only zero-fills blank/missing sale cells for the dense months. Currently: Aug–Sep 2024 stay sparse; every month Oct 2024–Jul 2026 is zero-filled. |
| 1 | `step1_apply_mapping.py` | Applies `vocab_mapping_FINAL_v5.csv` (the controlled vocabulary — 597 raw names → 540 canonical products) to both the sales and inventory CSVs, reports any unmapped raw name (should be 0), and (re)builds `Dim_Product` (519 rows — some canonical names in the mapping file have no live data behind them, which is expected). |
| — | `proportional_allocation.py` | Splits price-grouped tally rows (a single row covering several SKUs sharing a price point) into per-SKU rows, weighted by each SKU's beginning-of-month stock. Outputs `USTore_sales_long_allocated.csv` (+ `allocation_audit.csv` documenting every split). Run with `--sales USTore_sales_long_with_zeros.csv` so zero-quantity rows are preserved through the split (a grouped row with 0 units on a given day now correctly emits 0 for every constituent, instead of being dropped). |
| 2 | `step2_load_fact_sales.py` | Loads the allocated CSV into `Fact_Sales` (84,175 rows: 68,321 zero-quantity + 15,854 positive), routing unresolvable rows to `Exception_Log` instead of dropping them. Derives `cumulative_monthly_units` and `daily_depletion_rate`. |
| 3 | `step3_fsn_classification.py` | Computes ADUS (Average Daily Units Sold) per SKU, weighting imputed/allocated rows at 0.5, and classifies Fast/Slow/Non-moving at the 80th-percentile ADUS cutoff (currently F=58, S=230, N=231). Flags High-Velocity-Limited (HVL) items with thin history. Writes `fsn_class`/`is_hvl` back to `Dim_Product`. |
| 4 | `step4_prophet_forecast.py` | Fits Prophet per Fast SKU with a data-sufficiency tier (49 standard/60+ obs, 2 simplified/30-59 obs, 7 rolling-average/<30 obs — no real model), validates against a naive baseline, and writes 30-day forecasts + metrics to `Result_Forecast` / `Result_Forecast_Metrics`. Requires `cmdstan` (see below). |

Supporting/one-off scripts still in the repo: `create_schema.py` (builds
the empty `ustore.db`), `populate_dim_date.py` (loads the academic
calendar into `Dim_Date`), `build_vocab_mapping.py` / `diag_token_match.py`
(fuzzy-matching tools used to build and refine the vocabulary mapping —
not part of the regular run order, only needed if you're revisiting the
vocabulary itself).

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
  "same as yesterday" baseline** for these SKUs (10/51 beat naive, 0/51
  hit MAPE≤20%). This is a real finding, not a bug — most of these
  items are erratic/low-volume, and once the data honestly includes
  zero-sale days, persistence becomes a very strong baseline. Worth
  stating plainly in the write-up rather than tuned away.

## Files intentionally not committed

Regenerable intermediate outputs (`*_mapped.csv` from Step 1) and
superseded vocabulary-mapping versions are left out to keep the repo
readable — re-running the pipeline reproduces them. `ustore.db` itself
is gitignored (binary, machine-specific); run `create_schema.py` →
`populate_dim_date.py` → the pipeline above to rebuild it from scratch.
