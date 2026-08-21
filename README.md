# USTore Demand Forecasting — ETL & Analytics Pipeline

This branch (`neil`) contains the working data pipeline behind the USTore
Demand Forecasting & Prescriptive Inventory Management capstone: it turns
the raw monthly tally-sheet workbooks into a clean star-schema SQLite
database (`ustore.db`), classifies every product as Fast/Slow/Non-moving,
and produces Prophet-based 30-day forecasts for the Fast-moving SKUs.

The pipeline below (Phases 1–4) is the whole analytics side. A React
frontend and Flask backend also exist now — see **"Frontend + backend"**
near the end of this file for what they are and how to run them, and
`docs/STATUS_AND_NEXT_STEPS.md` for the full status/blocker register, including
Power BI (Phase 6), which hasn't been built yet.

## Layout

```
scripts/       the pipeline below, plus the two one-off converters/diagnostics
data/          every CSV: raw inputs, hand-maintained mappings, generated intermediates
requirements/  requirements.txt / requirements-dev.txt / requirements-prophet.txt
tools/, tests/, forecasting/, docs/   unchanged
ustore.db      stays at the repo root (gitignored) - every script below is run
               FROM THE REPO ROOT (e.g. `python scripts/create_schema.py`), never from
               inside scripts/, so its bare "ustore.db" path keeps resolving correctly
```

Run the scripts below **in order, from the repo root** — each one reads the previous step's
output and writes into `ustore.db`. All of them are safe to re-run: they
clear their own tables/files before writing, so re-running never
duplicates data. Run `python scripts/verify_data.py` before committing any change
that touches a CSV (see Block 1 below for what it checks).

## Pipeline

| Step | Script | What it does |
|---|---|---|
| — | `scripts/create_schema.py` | Builds the empty `ustore.db` (all 5 tables). Run once, or whenever rebuilding from scratch. |
| — | `scripts/populate_dim_date.py` | Fills `Dim_Date` (1,461 rows, 2023-01-01–2026-12-31) from `data/calendar_ranges.csv`: calendar flags, `semester_id`/`semester_week` derived from 12 term windows, and `is_tally_date` from the sales data. |
| 0 | `scripts/step0_convert_sales_with_zeros.py` | Converts the raw TBS tally-sheet workbooks (`rawdata/*.xlsx`) into `data/USTore_sales_long_with_zeros.csv`. Per month, decides whether the sheet is a genuine daily tally (has a date column for ~most calendar days) or a sparse periodic stock-count, and only zero-fills blank/missing sale cells for the dense months. Currently: Aug–Sep 2024 stay sparse; every month Oct 2024–Jul 2026 is zero-filled. |
| 1 | `scripts/step1_apply_mapping.py` | Applies `data/vocab_mapping_FINAL_v5.csv` (the controlled vocabulary — 597 raw names → 540 canonical products) to both the sales and inventory CSVs, reports any unmapped raw name (should be 0), and (re)builds `Dim_Product` (519 rows — some canonical names in the mapping file have no live data behind them, which is expected). Writes `data/USTore_sales_long_with_zeros_mapped.csv` + `data/USTore_inventory_excel_long_mapped.csv`, which are what the allocation step reads. **This is the only place the vocabulary mapping is applied.** Also applies `data/supplier_mapping.csv` (42 raw supplier strings → 19 suppliers + a `payment_status`) — see Block 2.7 below. |
| — | `scripts/proportional_allocation.py` | Splits price-grouped tally rows (a single row covering several SKUs sharing a price point) into per-SKU rows, weighted by each SKU's beginning-of-month stock. Reads step 1's *mapped* CSVs and joins on `canonical_item_name` (see Block 2.2 below); defaults now point at those files, so plain `python scripts/proportional_allocation.py` is correct. Outputs `data/USTore_sales_long_allocated.csv` (+ `data/allocation_audit.csv` documenting every split). Zero-quantity rows survive the split — a grouped row with 0 units on a given day emits 0 for every constituent instead of being dropped. |
| 2 | `scripts/step2_load_fact_sales.py` | Loads the allocated CSV into `Fact_Sales` (84,399 rows: 68,541 zero-quantity + 15,858 positive; sums to **89,232** units — see Block 0 below for why that's not the older 88,481), routing unresolvable rows to `Exception_Log` instead of dropping them. Item names arrive canonical, so it joins straight to `Dim_Product` and applies no mapping of its own. Derives `cumulative_monthly_units`, `daily_depletion_rate`, `days_of_supply` and `is_censored` — the four rules behind those are settled in the script's docstring and summarised under Blocks 2.4/2.6 below. |
| 3 | `scripts/step3_fsn_classification.py` | Computes ADUS (Average Daily Units Sold) per SKU, weighting imputed/allocated rows at 0.5, and classifies Fast/Slow/Non-moving at the 80th-percentile ADUS cutoff (currently F=58, S=228, N=233; it was S=230/N=231 before Block 2.2 below). Days flagged `is_censored` are dropped from the ADUS denominator — `EXCLUDE_CENSORED_DAYS = False` reverts that, see Block 2.4 below. Flags High-Velocity-Limited (HVL) items with thin history. Writes `fsn_class`/`is_hvl` back to `Dim_Product`. |
| 4 | `scripts/step4_prophet_forecast.py` | Fits Prophet per Fast SKU with a data-sufficiency tier, keyed on **distinct sale-days (quantity_sold > 0), not raw row count** — currently 38 standard (60+ sale-days), 10 simplified (30-59), 10 rolling-average (<30, no real model). Validates against a naive baseline, writes 30-day forecasts + metrics to `Result_Forecast` / `Result_Forecast_Metrics`. Requires `cmdstan` (see below). |
| 5a | `scripts/step5a_set_lead_times.py` | Sets `Dim_Product.lead_time_days` per product from a name-keyword classifier (jacket/windbreaker → 28d, embroidered → 18d, shirt/jersey/polo/tee → 14d, else → 18d default). Provisional, pending Block 5 (USTore site visit). |
| 5 | `scripts/step5_prescriptive.py` | ROP / Safety Stock / EOQ per Fast+Slow SKU, using `step5a`'s real lead time and a holding cost derived from USTore's stated inventory value (arithmetic + every assumption written to `Dim_Parameters`, all flagged provisional). Ordering cost is genuinely ambiguous, so every SKU is priced under **two** scenarios (`low_admin_cost` / `high_goods_value`) rather than one guess — see `docs/STATUS_AND_NEXT_STEPS.md` for the numbers. Writes `Result_Prescriptive`. |

Supporting/one-off scripts still in the repo: `scripts/build_vocab_mapping.py` /
`scripts/diag_token_match.py` (fuzzy-matching tools used to build and refine the
vocabulary mapping — not part of the regular run order, only needed if
you're revisiting the vocabulary itself).

## Key design decisions worth knowing before you touch this

- **Every date in every CSV is ISO 8601 (`YYYY-MM-DD`), and every script
  parses it with an explicit `format="%Y-%m-%d"` and `errors="raise"`.**
  No script tries DD/MM and then falls back to something else: with two
  formats accepted, `05/11/2025` is valid under both readings and lands
  six months apart depending on which branch caught it. `verify_data.py`
  now checks every date column in every CSV, including for ISO-shaped
  impossibilities like `2025-13-05`. **Never open these CSVs in Excel** —
  that is exactly how `USTore_inventory_excel_long.csv` ended up
  committed as DD/MM/YYYY when its own converter writes ISO.
- **Map first, then allocate — and the mapping is applied exactly once.**
  `step1_apply_mapping.py` is the only script that reads
  `vocab_mapping_FINAL_v5.csv` for data; allocation and `Fact_Sales`
  loading both join on `canonical_item_name`. Allocation matches a
  bundled sales row to its price group and weights each constituent by
  inventory stock — both name lookups — so they have to happen on the
  canonical name, not on whatever the tally sheet happened to spell that
  month. Feeding either script a raw CSV now exits 1 with a message
  naming the step to run, rather than quietly half-matching.
- **A zero in `Fact_Sales` has three possible meanings, and the column
  that tells them apart is `is_censored`.** 1 = the store had nothing to
  sell that day, 0 = stock was on hand and nobody bought, NULL = there
  is no inventory record, so it can't be told. NULL is the majority
  (83%) and that is a data limitation, not a defect to paper over: only
  75 of the 286 selling products appear in the inventory workbook at
  all. Any model averaging over zero-sale days should either exclude
  `is_censored = 1` or say why it doesn't.
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

Superseded vocabulary-mapping versions are left out to keep the repo
readable. `ustore.db` is gitignored (binary, machine-specific); run
`scripts/create_schema.py` → `scripts/populate_dim_date.py` → the pipeline above to
rebuild it from scratch.

This section used to say the `*_mapped.csv` files from Step 1 were left
out. That changed with Block 2.2: allocation reads them, so they are
pipeline inputs rather than diagnostics, and both
`USTore_sales_long_with_zeros_mapped.csv` and
`USTore_inventory_excel_long_mapped.csv` are now tracked. Step 1 has to
run before allocation either way. (Step 1's sales output was also
renamed — it was `USTore_sales_long_May_Aug2024-May2026_mapped.csv`,
named after a file it isn't built from.) `vocab_mapping_FINAL_v5.csv`
and `supplier_mapping.csv` are hand-maintained inputs rather than
intermediates, and are committed.

## Status against `docs/CODE_WORK_PLAN_v2.md`

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
  design-decisions above for specifics. **1.1 is now complete in its
  full scope**, not just `calendar_ranges.csv`: every date column in
  every CSV is ISO, both converters emit ISO, and every reader parses
  ISO strictly. Details in the section below.
- **Block 2.1** 🔴 (zero-fill inflating Prophet's tier counts) — fixed,
  see the Step 4 row above
- **Block 2.2** (map-vs-allocate order) — resolved as map → allocate,
  details below
- **Block 2.4** (stockout/censoring flags), **2.6** (`days_of_supply` +
  the four gap-handling rules), **2.7** (supplier consolidation) —
  details below
- **Block 2.3** (`is_hvl` as a boolean modifier, not a 4th `fsn_class`
  value) — the *shape* was already correct, but `create_schema.py` was
  missing the `is_hvl` column entirely; only databases that had it added
  by hand worked. A from-scratch rebuild ran fine until
  `step3_fsn_classification.py` and then died on
  `sqlite3.OperationalError: no such column: is_hvl`. The column is now
  in the committed DDL (`is_hvl INTEGER DEFAULT 0`), so the rebuild path
  the section below describes actually works. Found while re-running the
  whole pipeline to verify the ISO change.
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

**Block 1.1 in full — what changed and how it was verified**

Previously only `calendar_ranges.csv` was ISO; the rest of the repo was
`DD/MM/YYYY` with each reader carrying its own format string. That was
working, but it left the repo one Excel save away from a silent
six-month data shift, with no check that would catch it.

- **Converted in place** (round-trip verified: the ISO file converted
  back to DD/MM/YYYY is byte-identical to the committed original, so
  nothing but the date format changed) —
  `USTore_sales_long_May_Aug2024-May2026.csv` (15,151 rows, still
  88,481 units) and `USTore_inventory_excel_long.csv` (19,049 rows).
  These two can't be regenerated: the first is the pre-zero-fill
  converter output kept for provenance, and the second's source
  workbook isn't in the repo.
- **Regenerated** by re-running the fixed converter —
  `USTore_sales_long_with_zeros.csv` (75,120 rows, 89,232 units;
  differs from the committed version in the date column only).
  `USTore_sales_long_allocated.csv` and `allocation_audit.csv` were
  already ISO and came back byte-identical.
- **Writers now emit ISO:** `step0_convert_sales_with_zeros.py`,
  `Converter Aug 2024 - May 2026.py`. (`Inventory Excel Converter.py`
  always did — its output had been reformatted after the fact.)
- **Readers now parse ISO strictly:** `step1_apply_mapping.py`,
  `populate_dim_date.py`, `diag_token_match.py`,
  `proportional_allocation.py` (its silent multi-format ladder that
  returned `None` on failure now raises), `step2_load_fact_sales.py`
  (the DD/MM fallback is gone; a non-ISO date is now an
  `Exception_Log` row, not a guess). `step4_prophet_forecast.py` reads
  only the database and was untouched.
- **`verify_data.py` extended** from 2 files to every date column in
  all 6 CSVs, plus unit totals for all three sales files. Confirmed it
  fails (exit 1) on a deliberately broken copy in three ways: the
  inventory dates reformatted back to DD/MM/YYYY, one `2025-13-05`, and
  one quantity edited by +7.
- **Whole pipeline re-run from an empty database** (`create_schema` →
  `populate_dim_date` → `step1` → `proportional_allocation` → `step2` →
  `step3`). Every documented number reproduced exactly: Dim_Date 1,461
  rows / 1,453 `semester_week` / `MAX` 23 / 608 tally dates; Fact_Sales
  84,175 rows / 89,232 units / 68,321 zero-quantity / 0 exceptions;
  Dim_Product 519 rows, F=58 S=230 N=231, 7 HVL (Block 2.2 below has
  since moved 3 of those items). Step 4 was not re-run
  (needs `cmdstan`, and it was already pending a re-run — see above).

**Block 2.2 in full — map before allocate**

The plan's recommendation was adopted: allocation now runs on canonical
names. `step1_apply_mapping.py` writes mapped copies of the sales and
inventory CSVs, `proportional_allocation.py` reads those and joins on
`canonical_item_name`, and `step2_load_fact_sales.py` no longer carries
its own copy of the vocabulary mapping.

- **`allocation_groups.csv` was already canonical, by luck.** All 18
  group labels and all 45 constituents map to themselves, so the file
  needed no edit — but the script canonicalises both columns through
  `vocab_mapping_FINAL_v5.csv` at load time anyway, so a future edit can
  use either spelling. It also refuses a group that would contain its
  own label, and reports any constituents that canonicalisation merges
  (currently none: 45 raw variants → 45 canonical).
- **What actually changed.** Three raw spellings were dodging the group
  match: `Back Pack` → `Back pack` and `QUIANA SHIRT (B&Y SUBLI)` →
  `QUIANA SUBLI SHIRT (B&Y)` on the sales side, and
  `Varsity UST  Keychain` (double space) → `Varsity UST Keychain` on the
  inventory side. Price-grouped rows went from 5,614 → **5,838** (3,906
  → 3,950 units allocated), stock weights picked up 4 more inventory
  rows, and `Fact_Sales` went from 84,175 → **84,399 rows**. Audit rows
  14,669 → 15,117.
- **Units are unchanged: 89,232 in, 89,232 out**, which is the check
  that matters — allocation redistributes units between SKUs, it never
  creates or destroys them. The row-count shift is the legitimate kind
  the plan anticipated.
- **Three SKUs changed FSN class**, and two of them are the point of
  the exercise: `Back pack` (S → **N**) and `QUIANA SUBLI SHIRT (B&Y)`
  (F + HVL → **N**) are price-group *labels*, not sellable SKUs. Under
  the old order their variant spellings were loaded as real sales for
  the label itself, so a bundle looked like a moving product — one of
  them Fast. Both now have zero `Fact_Sales` rows, all their units
  having gone to real constituents. `Corp Jacket V2` (S → **F**) is a
  constituent that gained the redistributed units. → Divergence
  Register: the F/S/N counts in any earlier draft were computed with
  two bundle labels counted as products.
- **Both orderings now fail loudly instead of silently.** Feeding
  `proportional_allocation.py` a raw sales CSV, or `step2` a
  pre-2.2 allocated file, exits 1 with a message naming the step to run
  — verified. The allocated CSV's item column was renamed `Item` →
  `canonical_item_name` precisely so a stale file can't half-load.
- **Not re-run:** step 4. It reads only the database, but its inputs
  have changed (`Fact_Sales` rows and the Fast list), so its stored
  forecasts are now stale for a second reason.

**Blocks 2.4 / 2.6 in full — the derived fields and the censoring flag**

`Fact_Sales` gained `days_of_supply` and `is_censored`; `create_schema.py`
carries both. The four rules the plan wanted signed off are settled in
`step2_load_fact_sales.py`'s docstring, and the evidence changed since
the plan was written — the plan's "55% of consecutive per-SKU
observations are >1 day apart, max gap 67 days" was measured before the
zero-fill. It is now **3.5%, max gap 156 days**.

1. **First observation per product → `daily_depletion_rate` NULL**
   (290 rows). Dividing by 1 invented a rate for a day with no
   preceding interval.
2. **Same-date rows → one rate per product-day.** 640 date pairs have a
   product receiving both a direct and an allocated row; the old code
   gave the second row a gap of 0 clamped to 1, so one day carried two
   incompatible rates. The numerator is now the product's daily total.
3. **Long gaps capped at 30 days** (217 product-days). Dividing a real
   sale by a 156-day gap reports it as a depletion rate near zero.
   Capping overstates the rate on those rows, which is the safe
   direction for a field that feeds reorder timing.
4. **Month boundaries do not reset the interval** (2,011 observations
   sit across one). Depletion is a physical rate; only
   `cumulative_monthly_units` resets.
   **Sundays are deliberately not excluded** — 76 of the 608 tally
   dates are Sundays and 15 fall on dates flagged `is_store_closed`, so
   the store demonstrably trades on both. Whether those closure flags
   are wrong is a Block 5 question, not something to assume here.

`days_of_supply` = estimated units on hand ÷ the product's mean daily
units over the trailing 28 observed days. Populated on 8,216 rows;
median 229 days, p90 3,300, max 83,167 — the long tail is real (a large
stock against a near-zero recent sales rate), not a bug, and it is left
uncapped rather than clipped to a tidier number. Only 24% of populated
rows are under 60 days.

`is_censored` (Block 2.4) uses the only stock signal in the project:
the month's inventory count minus the product's sales earlier in that
month. **1,787 rows (37 SKUs) are censored** — zero sales on a day the
item was already out. 12,373 rows are confirmed not censored, and
**70,239 are NULL**, because the item has no inventory record for that
month; stock coverage is 16.8% of rows. 51 rows are NULL for a
different reason: a positive sale on a day the model said stock was
zero proves an unrecorded restock, so that row and the rest of that
month are marked unknown rather than forced to fit. The
"ALL STOCKS ARE TAKEN" notes in the inventory workbook would be a
second signal but appear on only 3 rows — not usable, worth raising at
the store visit.

**The flag is used, not just recorded.** `step3_fsn_classification.py`
drops censored days from the ADUS denominator, since a day with nothing
to sell is not evidence that an item moves slowly. The counts are
unchanged (58/228/233 — the cutoff is a percentile, so this reshuffles
the boundary rather than shifting it) but **four items swap**:
`White Tote Bag` (289 of its 339 days censored) and `Tiger w/ Box` rise
S → F; `Corp Jacket V2` and `Y,B VL UST`, with no censored days, drop
F → S. That is the plan's stated concern — a stocked-out item being
misread as slow — actually biting on real SKUs. Set
`EXCLUDE_CENSORED_DAYS = False` to see it the other way. → Divergence
Register.

**Block 2.7 in full — supplier normalisation**

`supplier_mapping.csv` (new, reviewable, same pattern as the vocabulary
file) maps all **42 raw supplier strings → 19 suppliers**, splitting the
`(CONSIGNMENT)`/`(PAID)` suffix into a new `Dim_Product.payment_status`
column. `step1_apply_mapping.py` applies it and aborts on any unmapped
string; `verify_data.py` fails if a string in the sales CSV is missing
from the map, if a `supplier_name` still carries a parenthetical, or if
a `payment_status` is outside CONSIGNMENT/PAID/UNKNOWN — verified by
deleting a row from a copy.

- The plan estimated ~15 real suppliers; the true figure is **19**.
  Three judgment calls account for most of the difference and are
  flagged in the file's `note` column for the store visit:
  `STITCH CORP. (BLEEVES)` ×4 collapsed into `STITCH CORP.` (BLEEVES
  read as a product line, per the plan), `ARTS AND LETTERS (SHIRT
  HAPPENS)` → `SHIRT HAPPENS`, and `COLLEGE OF SCIENCE AT 100` / `IPEA`
  / `CENTRAL SEMINARY` kept as separate names although they are UST
  units commissioning merchandise rather than trade suppliers.
- **The `(Paid)` orphan is resolved**: it maps to no supplier and
  `payment_status = PAID`. Same for `Subli. Shirt 2 colors`, an item
  name typed into the Supplier column. Together 622 rows have no
  attributable supplier — but **no product lost its supplier**, since
  every item appearing under those strings also appears under a real
  one. The 256 `Dim_Product` rows with a NULL supplier are the
  inventory-only items that have no sales rows at all.
- **`payment_status` is not really a product attribute**: 103 items are
  sold under more than one status. `Dim_Product` keeps the modal value
  and step 1 prints that count, so the simplification is visible. If
  §3.2's supplier grouping needs per-transaction accuracy, the field
  belongs on `Fact_Sales` instead — a call for the team.

**Not done — everything else in the plan:**
- Block 3 (inventory coverage — needs a group decision + USTore staff
  input). Measured again while building the censoring flag: **76**
  canonical items now appear in both sales and inventory, not the plan's
  42 — canonicalisation and the Block 2.2 reorder both improved it. It
  is still only 16.8% of `Fact_Sales` rows, and the inventory workbook
  only spans 2024-11 → 2026-04 against sales running 2024-05 → 2026-07,
  so everything outside that window has no stock signal at all.
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

> **This "Status against `docs/CODE_WORK_PLAN_v2.md`" section predates a large
> merge and is now stale in places** — e.g. Block 6.2's files (
> `model_benchmark.py`, tests, `.mailmap`, etc.) **do** exist in this
> branch now, and Block 4's forecasting overhaul is partly done (an
> 8-method benchmark exists, though Prophet itself is still unrun). Left
> as-is rather than rewritten, since `docs/CHANGES_tyrone.md` is the corrected,
> authoritative record of that merge. Treat this section as history, not
> current status — see `docs/STATUS_AND_NEXT_STEPS.md` for what's actually true
> today.

---

## Changes added since the ETL pipeline above (frontend + backend)

The pipeline (Phases 1–4) is unchanged by this. What's new sits on top of
the same `ustore.db`:

- **`UST Prototype Design/`** — a React + Vite frontend (five analytics
  screens plus a Digital Tallying Interface), built in two earlier passes
  (`docs/PROMPT_1_FRONTEND.md`, mock data only; a Power BI embed placeholder).
  Every screen reads through one module, `src/services/dataService.js`.
- **`backend/`** — a Flask + SQLite API (`docs/PROMPT_3_BACKEND.md`, Phase 3)
  that replaces the frontend's mock fixtures with real reads/writes
  against the same `ustore.db` the pipeline above builds. It does **not**
  reseed from CSVs — it's a read/write layer on the pipeline's output, not
  a second data path. `dataService.js` was swapped to call it; no other
  frontend file changed except `Reorder.jsx` (see below).
- **`step5a_set_lead_times.py` / `step5_prescriptive.py`** (Pipeline table
  above) — real per-SKU lead time, holding cost, and dual-scenario
  ordering cost replaced the old abstract 5×5 sensitivity grid.
  `Dim_Parameters` and `Result_Prescriptive` are populated now, so the
  Reorder Alerts screen shows real (provisional) numbers instead of a
  permanent "pending" state.

**Run both together:**

```bash
cd backend && pip install -r requirements.txt && python app.py       # :5000
cd "UST Prototype Design" && npm install && npm run dev              # :5173, proxies /api to :5000
```

See `backend/README.md` and `UST Prototype Design/README.md` for details,
and `UST Prototype Design/BACKEND_TODO.md` for the endpoint-by-endpoint
contract the backend implements.

## Problems that still need fixing

- ~~`Overview.jsx`'s "Items Below / Near ROP" KPI is stale~~ **Fixed.** It
  now computes the same reorder-now count `Reorder.jsx` does (stock ≤
  reorder point, both real), with a KPI/banner/advisory copy pass across
  the whole screen so nothing there still implies reorder data doesn't
  exist.
- **No PDF export on the Batch Sales Report.** Still not implemented —
  `weasyprint`/`reportlab` have native dependencies that are fragile on
  Windows, so this was deliberately deferred rather than attempted
  mid-session. The buttons are disabled with an accurate
  "not yet implemented" message (previously said "Phase 3 backend",
  which was stale — Phase 3 is the backend, and it's here).
- ~~`ustore.db` at the repo root predates `Result_Prescriptive` /
  `Closure_Log` / the Wave 1 schema changes~~ **Fixed.** It had never
  actually been rebuilt since the original ETL work — `backend/db.py`'s
  unconditional `CREATE INDEX ... ON Result_Prescriptive` meant every API
  call 500'd. Rebuilt from scratch via the full current pipeline
  (`create_schema` → `populate_dim_date` → `step1` →
  `proportional_allocation` → `step2` → `step3` → `step5a` → `step5`,
  `step4`/Prophet still skipped, see below); every README-documented
  invariant reproduced exactly (84,399 `Fact_Sales` rows, 89,232 units,
  F=58/S=228/N=233, 416 `Result_Prescriptive` rows, all step5 gates
  passing). If `ustore.db` is ever regenerated from an older checkout, run
  the full pipeline again rather than assuming an existing file is current
  — a stale-but-present file fails differently (and less visibly) than a
  missing one.
- **No auth on the backend.** `Event_Log.created_by` is hardcoded
  `'local'`. Fine for a single-machine capstone demo, not for anything
  beyond that.
- **Prophet is still blocked** (Block 5/B5 — needs a `cmdstan` build) —
  `Result_Forecast` doesn't exist, so the Demand Forecast screen correctly
  shows "pending," not a number.
- **Power BI itself (Phase 6) hasn't been built.** The frontend has an
  embed placeholder (`PowerBIDashboard.jsx`) wired to `VITE_POWERBI_EMBED_URL`,
  but no `.pbix` has been authored or published yet. Two of its five views
  (Stock Status, Demand Forecast) are additionally blocked on data
  decisions — see `docs/STATUS_AND_NEXT_STEPS.md` §4 for the per-view
  breakdown and what's blocking each one, and
  `docs/POWERBI_DASHBOARD_PLAN.md` for the concrete chart-by-chart build
  spec (fields, filters, colour, which 3 of the 5 views are buildable
  today).
- **Inventory coverage is still low** (~14–17% of products have any stock
  count), which limits both the Stock Status view and the Reorder
  screen's "on hand" column to a minority of SKUs — Block 3/B10, unresolved.
- **Everything Phase 4 produces is explicitly provisional.** Lead time,
  holding cost, and both ordering-cost interpretations are estimates
  pending the actual USTore site visit (Block 5/B9) — not yet confirmed
  numbers. Don't treat `Result_Prescriptive` as final.

Full status/blocker register (B1–B15, all still-open team decisions):
`docs/STATUS_AND_NEXT_STEPS.md`.
