# USTore Demand Forecasting — ETL & Analytics Pipeline

This branch (`neil`) contains the working data pipeline behind the USTore
Demand Forecasting & Prescriptive Inventory Management capstone: it turns
the raw monthly tally-sheet workbooks into a clean star-schema SQLite
database (`ustore.db`), classifies every product as Fast/Slow/Non-moving,
and produces rolling-mean 30-day forecasts for the Fast-moving SKUs.

The pipeline below (Phases 1–4) is the whole analytics side. A React frontend
and Flask backend also exist — see **"Changes added since the ETL pipeline"**
below for what they are and how to run them.

**Everything else is in `docs/` — see the [Documentation](#documentation) map.**
Start with `docs/OPEN_ISSUES.md` (what's broken) and
`docs/STATUS_AND_NEXT_STEPS.md` (decisions awaiting a human call).

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
that touches a CSV (see `docs/WORK_PLAN_STATUS_HISTORY.md` Block 1 for what it checks).

## Pipeline

| Step | Script | What it does |
|---|---|---|
| — | `scripts/create_schema.py` | Builds the empty `ustore.db`. `CREATE TABLE IF NOT EXISTS` throughout, so it is safe on an existing database. (The manuscript's 5 star-schema tables, plus `Exception_Log`, `Event_Log`, `Closure_Log`, the three `Result_*` tables, and `Pipeline_Run` — see "Running the pipeline from the frontend" below for what that last one is for.) |
| — | `scripts/populate_dim_date.py` | Fills `Dim_Date` (1,461 rows, 2023-01-01–2026-12-31) from `data/calendar_ranges.csv`: calendar flags, `semester_id`/`semester_week` derived from 12 term windows, and `is_tally_date` from the sales data. |
| 0 | `scripts/step0_convert_sales_with_zeros.py` | Converts the raw TBS tally-sheet workbooks (`rawdata/*.xlsx`) into `data/USTore_sales_long_with_zeros.csv`. Per month, decides whether the sheet is a genuine daily tally (has a date column for ~most calendar days) or a sparse periodic stock-count, and only zero-fills blank/missing sale cells for the dense months. Currently: Aug–Sep 2024 stay sparse; every month Oct 2024–Jul 2026 is zero-filled. |
| 1 | `scripts/step1_apply_mapping.py` | Applies `data/vocab_mapping_FINAL_v5.csv` (the controlled vocabulary — 597 raw names → 540 canonical products) to both the sales and inventory CSVs, reports any unmapped raw name (should be 0), and (re)builds `Dim_Product` (519 rows — some canonical names in the mapping file have no live data behind them, which is expected). Writes `data/USTore_sales_long_with_zeros_mapped.csv` + `data/USTore_inventory_excel_long_mapped.csv`, which are what the allocation step reads. **This is the only place the vocabulary mapping is applied.** Also applies `data/supplier_mapping.csv` (42 raw supplier strings → 19 suppliers + a `payment_status`) — see Block 2.7 in `docs/WORK_PLAN_STATUS_HISTORY.md`. |
| — | `scripts/proportional_allocation.py` | Splits price-grouped tally rows (a single row covering several SKUs sharing a price point) into per-SKU rows, weighted by each SKU's beginning-of-month stock. Reads step 1's *mapped* CSVs and joins on `canonical_item_name` (see Block 2.2 in `docs/WORK_PLAN_STATUS_HISTORY.md`); defaults now point at those files, so plain `python scripts/proportional_allocation.py` is correct. Outputs `data/USTore_sales_long_allocated.csv` (+ `data/allocation_audit.csv` documenting every split). Zero-quantity rows survive the split — a grouped row with 0 units on a given day emits 0 for every constituent instead of being dropped. |
| 2 | `scripts/step2_load_fact_sales.py` | Loads the allocated CSV into `Fact_Sales` (84,399 rows: 68,541 zero-quantity + 15,858 positive; sums to **89,232** units — see Block 0 in `docs/WORK_PLAN_STATUS_HISTORY.md` for why that's not the older 88,481), routing unresolvable rows to `Exception_Log` instead of dropping them. Item names arrive canonical, so it joins straight to `Dim_Product` and applies no mapping of its own. Derives `cumulative_monthly_units`, `daily_depletion_rate`, `days_of_supply` and `is_censored` — the four rules behind those are settled in the script's docstring and summarised under Blocks 2.4/2.6 in `docs/WORK_PLAN_STATUS_HISTORY.md`. |
| 3 | `scripts/step3_fsn_classification.py` | Computes ADUS (Average Daily Units Sold) per SKU, weighting imputed/allocated rows at 0.5, and classifies Fast/Slow/Non-moving at the 80th-percentile ADUS cutoff (currently F=58, S=228, N=233; it was S=230/N=231 before Block 2.2 in `docs/WORK_PLAN_STATUS_HISTORY.md`). Days flagged `is_censored` are dropped from the ADUS denominator — `EXCLUDE_CENSORED_DAYS = False` reverts that, see Block 2.4 in `docs/WORK_PLAN_STATUS_HISTORY.md`. Flags High-Velocity-Limited (HVL) items with thin history. Writes `fsn_class`/`is_hvl` back to `Dim_Product`. |
| 4 | `scripts/step4_forecast_model.py` | Forecasts **every** Fast SKU with `rolling_mean_30` — literally `forecasting/baselines.py::rolling_mean_fit_predict(30)`, the same callable `model_benchmark.py` scores, so the benchmark's and `SERVICE_LEVEL_FRONTIER.md`'s findings apply to the production model directly. Flat over 30 days, ±1 SD band. Validated on `forecasting/evaluate.py`'s walk-forward harness at the benchmark's exact settings (**horizon 30, 3–12 folds, min_train 60**), so the scoring unit is a **30-day aggregate** — the quantity the pipeline serves — not a daily point. **58 of 58 SKUs scored, 12 folds each**; naive is scored on identical folds. **Takes seconds.** Sale-day tiers (38/10/10) are now descriptive labels only — they select neither model nor harness. Results: 35/58 beat naive (MAE 40.0 vs 104.2), 1/58 meets MAPE ≤20%, and **32/58 forecast zero** because their trailing window is empty — see `docs/ROLLING_MEAN_FORECAST.md` §4, that one is an open decision. Previously fit Prophet per SKU with full-MCMC production fits at 1–2 hours; renamed from `step4_prophet_forecast.py`. |
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
- **There is no Prophet, and no `cmdstan`.** Step 4 forecasts with a rolling
  mean and nothing in the repo imports `prophet`, so blocker B5 is closed and
  the toolchain instructions that used to live here no longer apply. What that
  swap gave up — all trend, seasonality and calendar-regressor signal — is set
  out in `docs/ROLLING_MEAN_FORECAST.md`.
- **Step 4 replaces its results in one transaction.** It used to clear
  `Result_Forecast`/`Result_Forecast_Metrics` at the top of `main()` and
  refill them 1-2 hours later, so anything that interrupted the run in
  between - the frontend's Stop button, a timeout, a crash - left the
  tables empty and the Demand Forecast screen stuck on "pending" until
  someone sat through another full run. The two `DELETE`s now sit with the
  `INSERT`s at the end, so an interrupted run rolls back to the previous
  forecasts instead of to nothing.
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
  silently defaulted to 0. Phase 3 (`step4_forecast_model.py`) **has since
  been re-run**, so `Result_Forecast(_Metrics)` post-dates the fix. Note
  that `semester_week` is no longer a model input either way: the rolling
  mean has no regressors, and Dim_Date is now read only to scope the
  validation metrics.

## Files intentionally not committed

- `ustore.db` — binary and machine-specific. Rebuild it by running the pipeline
  above from `scripts/create_schema.py`.
- `rawdata/*.xlsx` — the real client tally-sheet workbooks. Sensitive (supplier
  names, prices, sales volumes). Only step 0 reads them; every later step reads
  `data/*.csv`, which **is** committed, so the pipeline runs without them.
- Superseded vocabulary-mapping versions, to keep the repo readable.

`data/*_mapped.csv` **are** committed: since Block 2.2 allocation reads them, so
they are pipeline inputs rather than diagnostics. `vocab_mapping_FINAL_v5.csv`
and `supplier_mapping.csv` are hand-maintained inputs and are committed too.

## Documentation

The README covers the pipeline and how to run it. Everything else lives in
`docs/`:

| I want to know… | Read |
|---|---|
| **What's broken / still to do** | `docs/OPEN_ISSUES.md` |
| **Open team decisions** (B1–B15) | `docs/STATUS_AND_NEXT_STEPS.md` |
| The forecasting model, and whether it meets the criteria | `docs/ROLLING_MEAN_FORECAST.md` |
| Why an error-based acceptance criterion fails here | `docs/DEGENERATE_FORECAST.md` |
| Why service level is a frontier, not a threshold | `docs/SERVICE_LEVEL_FRONTIER.md` |
| Method comparison (10 methods, identical folds) | `docs/FORECAST_METHOD_COMPARISON.md` |
| Where every number came from | `docs/DATA_PROVENANCE.md` |
| Divergences from the manuscript | `docs/DIVERGENCE_REGISTER.md` |
| The remediation plan and its status | `docs/REMEDIATION_MASTER_v2.md`, `docs/REMEDIATION_WAVE1_STATUS.md` |
| Power BI build spec | `docs/POWERBI_DASHBOARD_PLAN.md` |
| Backend / frontend contracts | `backend/README.md`, `UST Prototype Design/README.md` |
| Historical status against the work plan | `docs/WORK_PLAN_STATUS_HISTORY.md` |

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

### Running the pipeline from the frontend

The Tally Interface's **Full Pipeline Run** card drives the whole table at
the top of this file as a background job (`backend/pipeline.py`,
`POST /api/pipeline/run`), so the pipeline does not have to be run from a
terminal after a day of tallying. Two buttons, because the two runs have
wildly different costs:

| Button | Steps | Time |
|---|---|---|
| **Run Pipeline (no forecast)** | everything except step 4 | **~40 s** |
| **Run Full Pipeline + Forecast** | everything | **~50 s** (step 4 adds ~10 s) |

Step 4 is the only step that can be opted out of (`pipeline.SKIPPABLE`),
and it is safe to skip because nothing downstream reads its output —
`step5_prescriptive.py` derives demand from observed history
(`--demand-basis trailing`, the default), not from `Result_Forecast`. A
no-forecast run still rebuilds the database, the FSN classes and the
reorder points; it just leaves whatever forecasts are already there alone.

What the run does *not* destroy, and why that matters if you are reading
these scripts and expecting a from-scratch rebuild to be a wipe:

- **Tally entries** typed into the interface are `tally_date_flag = 0`;
  `step2_load_fact_sales.py` only deletes `tally_date_flag = 1`, so they
  survive and are picked up by step 3's ADUS on the next run.
- **Events and closures** are re-applied onto the freshly rebuilt
  `Dim_Date` by `populate_dim_date.py`, which reads `Event_Log` and
  `Closure_Log` back before its `INSERT`.
- **`Pipeline_Run`** (added to `create_schema.py`) is never cleared by any
  step. One row per run, plus the `Fact_Sales`/`Event_Log`/`Closure_Log`
  id high-water marks at the moment the run finished.
- **`Inventory_Count`** (also added to `create_schema.py`) likewise — see
  below.

**The staleness warning.** Those high-water marks are what
`GET /api/pipeline/staleness` compares against the database now, and what
the banner at the top of the Tally Interface renders. Everything the
interface writes is in `ustore.db` immediately, but `fsn_class`,
`Result_Forecast` and `Result_Prescriptive` are only recomputed by a run —
so without this, the Reorder Alerts screen will show reorder points that
predate a fortnight of tallying with nothing on screen saying so. It
warns in three situations and is silent otherwise:

- tally entries / events / closures recorded since the last completed run,
- no completed run recorded for this database at all (with such records
  present),
- the most recent run was stopped or failed part-way, which leaves the
  tables half-rebuilt (step 3 may have rewritten `fsn_class` with step 5
  never reaching `Result_Prescriptive`).

### Monthly inventory counts

The Tally Interface has a **Monthly Inventory Count** card: pick a month,
pick an item, enter units on hand. It writes `Inventory_Count` (one row per
product per month, `UNIQUE (product_id, count_month)` — a recount *replaces*
the earlier figure rather than adding to it, and the card says so when it
overwrites one). Zero is a valid and deliberately-allowed count: it is the
store recording that an item is out.

This is the digital counterpart of the historical inventory workbook, and
it exists to chip away at what is still the project's biggest data gap
(Block 3/B10): only ~17% of `Fact_Sales` rows have any stock signal behind
them, and `USTore_inventory_excel_long_mapped.csv` stops at **2026-04**
while sales run to 2026-07.

**It does not reintroduce `Dim_Inventory`.** §3.2 omits that deliberately —
per-*day* stock stays derived (beginning stock minus cumulative units),
which is the thing that would have made it a rapidly-changing dimension.
This table holds what the workbook holds: a periodic count at month
granularity.

`catalog.py` merges the two sources per product and **the more recent month
wins, with a tie going to the staff count** — a count taken this month is
better evidence than a workbook row from the same month, and the workbook
does not extend past its last export. So counts take over naturally as the
store starts entering them, with no switch to flip. Verified: entering a
count for a product the workbook never covered raised `/api/stock`'s
coverage from 82 to 83 products, and `stock_source` on every catalog row
says which source the number came from (`workbook` / `counted`).

> **Known gap.** These counts feed the *live API* (`current_stock`,
> `/api/stock`, the Reorder screen's on-hand column) but **not the ETL**.
> `Fact_Sales.is_censored` and `days_of_supply` are still derived by
> `step2_load_fact_sales.py` from the inventory CSV alone, so a
> newly-counted product shows `current_stock` but still `days_of_supply:
> null`. Wiring `Inventory_Count` into step 2 would change those columns'
> documented row counts and the censoring evidence behind Block 2.4, so it
> is left as a deliberate team decision rather than done silently.

### The Reorder screen's order quantity is not EOQ

`/api/reorder` returns a `suggested_order_qty` per SKU and a `summary` block,
and the Reorder Alerts screen leads with a **Reorder Today** card built from
them. That quantity is an **order-up-to level** — reorder point plus
`REVIEW_PERIOD_DAYS` (30) of demand at the observed rate, minus what is on
hand — and deliberately *not* EOQ.

The reason is a measured property of the current numbers, not a preference:

| Ordering-cost scenario | SKUs where EOQ > a full year of demand | median EOQ ÷ annual demand |
|---|---:|---:|
| `low_admin_cost` | 204 of 208 | 4.3× |
| `high_goods_value` | 208 of 208 | 54.8× |

e.g. `5M Rotating Keychain`: annual demand 1,389, EOQ 1,544 (low) / 19,533
(high). Putting either figure in front of staff as "order this many" would be
telling them to buy between four and fifty-five years of stock. The cause is
the still-provisional ordering/holding costs (Block 5/B9) — the S/H ratio is
not yet a real number — so this is a symptom of missing inputs, not a bug in
`step5_prescriptive.py`, whose EOQ arithmetic passes all its own gates.

**EOQ is not hidden.** It stays in the Reorder Recommendations table under
both interpretations, greyed with a tooltip wherever it exceeds annual demand,
and `scenarios.*.exceeds_annual_demand` carries the flag in the API. Once the
site visit produces real costs, `suggested_order_qty` is the thing to revisit.

The order-up-to quantity uses only inputs that are actually measured — on-hand,
ADUS, lead time — and none of the two cost estimates. It is computed in
`app.py`, not in the screen: per `Pending.jsx`'s rule, a screen must never
invent an analytic the backend has not produced.

The card sits under the KPI row and is laid out like the Batch Sales Report,
reusing its markup (`.report-supplier` header bar, `.report-items` table,
`.report-subtotal` gold total bar) as one card, with the title and supplier
count inside the dark bar. One table — Item / Supplier / On Hand / Reorder
Point / Order — sorted most-urgent-first (fewest days of cover left, then the
fastest seller). `.report-items` caps the body at ten rows and scrolls with the
header pinned, the same as on the report. Also on that screen, the ROP/EOQ
formula card is collapsed by default (`<details>`).

`summary.order_qty_note` and `summary.no_stock_count` are still on the API but
are no longer printed under the table — the reasoning above is the record for
them.

**Behaviour worth knowing.** Steps run with `python -u` and their stdout is
streamed line by line, so the card shows a live tail rather than nothing
until the step exits. Each step has a 15-minute wall-clock timeout so a hang is
reported instead of leaving the UI on "Running…" forever. Stop kills the process
*tree*, not just the Python child — written when step 4 spawned a Stan binary
per SKU, and kept because `terminate()` orphaning grandchildren is a general
gap.

`db.py` also opens the database in **WAL** mode and creates its indexes
once at startup rather than on every request. Both are there for this
button: under the default rollback journal, ten `python scripts/*.py`
processes taking long write transactions would block every API read, and
the per-request `CREATE INDEX IF NOT EXISTS` meant even pure reads opened
a write transaction and died on "database is locked" mid-run.

See `backend/README.md` and `UST Prototype Design/README.md` for details,
and `UST Prototype Design/BACKEND_TODO.md` for the endpoint-by-endpoint
contract the backend implements.


## Status and open issues

- **Open technical issues** — `docs/OPEN_ISSUES.md`
- **Open team decisions** (B1–B15, all needing a human call) —
  `docs/STATUS_AND_NEXT_STEPS.md`

The single highest-priority item is **B1: this repo is public and contains real
client data** (supplier names, unit prices, sales volumes). Making it private is
a GitHub account-level setting and needs a human to act.
