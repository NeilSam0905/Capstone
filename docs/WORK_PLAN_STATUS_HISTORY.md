# Status against `docs/CODE_WORK_PLAN_v2.md` — historical record

**This is history, not current status.** It was the README's longest section
(262 lines, ~40% of the file) and is kept here so the README can be read in one
sitting. It predates a large merge and is stale in places — Block 6.2's files
(`model_benchmark.py`, the test suite, `.mailmap`) **do** exist now, and Block 4's
forecasting overhaul has since landed (`docs/ROLLING_MEAN_FORECAST.md`).

**For what is actually true today, read instead:**

| Question | File |
|---|---|
| Open team decisions (B1–B15) | `docs/STATUS_AND_NEXT_STEPS.md` |
| Open technical issues | `docs/OPEN_ISSUES.md` |
| The corrected record of the merge | `docs/CHANGES_tyrone.md` |
| The forecasting model and criteria | `docs/ROLLING_MEAN_FORECAST.md` |

Left unedited below rather than rewritten, so it stays an honest record of what
was believed at the time.

---


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
  `Exception_Log` row, not a guess). `step4_forecast_model.py` reads
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
