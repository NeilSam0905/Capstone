# USTore Demand Forecasting & Prescriptive Inventory Dashboard — Project Context

> Context primer for Claude Code. Read this before touching the pipeline.
> Capstone project, University of Santo Tomas (UST), College of Information and Computing Sciences.
> IS 26312 (Capstone 1 — documentation, current) → IS 26316 (Capstone 2 — implementation).

---

## 1. What this project is

A **Business Analytics** capstone (explicitly *not* a software engineering deliverable). The primary
output is a decision-support dashboard driven by statistical forecasting — not a transactional app.

**The client:** USTore, the official merchandise outlet of UST (España campus, Manila). Sells
institutional apparel, memorabilia, university-branded goods to students, alumni, faculty, staff,
visitors.

**Business model:** Consignment. The university does not buy stock upfront. Suppliers deliver
merchandise; the university pays each supplier monthly based strictly on volume confirmed sold in that
billing period. Payment requires coordination between the **UST Purchasing Office** (generates Purchase
Orders) and the **Finance Department** (issues checks).

### The hard constraint (drives every design decision)

University policy, aligned with **BIR** (Bureau of Internal Revenue) regulations on third-party
financial systems, **prohibits USTore from deploying an automated POS system**. Staff therefore record
sales by hand on paper tally sheets, grouped per consignment supplier.

Consequences that the project exists to fix:
- During peak periods (enrollment, foundation days, varsity events) transaction volume exceeds manual
  tallying capacity → **missed/unrecorded sales**.
- The inventory baseline is compiled from accumulated tallies, so missed tallies **distort stock levels**.
- Month-end reconciliation rolls untallied sales into the next month's settlement → the real-time
  inventory record is **never fully accurate**.
- Management therefore restocks on experience-based guesswork → recurrent stockouts.

**Compliance rule for anything we build:** the Digital Tallying Interface is an *internal inventory
counting tool only*. It must NOT process monetary transactions, compute customer-facing totals, or
produce BIR receipts.

---

## 2. Four-layer architecture

| Layer | Purpose | Tech |
|---|---|---|
| 1. Data Capture | Replace paper tally sheets | Flask or React + SQLite / Google Sheets |
| 2. Data Processing | ETL → Star Schema | Python 3.x + pandas |
| 3. Analytics | FSN classification → Prophet forecast → ROP/SS/EOQ | Python, Prophet (Meta) |
| 4. Visualization | Decision support + PDF batch reports | Microsoft Power BI (free desktop tier) |

Two functional areas integrate across these: **FA1 Sales & Inventory Operations** and
**FA2 Procurement & Supplier Management**. Methodology framework is **CRISP-DM** (6 phases, iterative).

---

## 3. Current status

Capstone 1 documentation is largely complete (Chapters 1–3 + bibliography). Data engineering is well
advanced — the historical ETL has been built and run. **Prophet has been prototyped and underperformed;
diagnosis is the live open question** (see §9).

---

## 4. Data assets

All paths relative to the working output directory. **Canonical files are bolded.**

### 4.1 **`USTore_sales_long_allocated_normalized.csv`** — primary sales fact table

Post-ETL, post-allocation, **post item-canonicalization + supplier-normalization** (work item #1).
This is now the file to build on. Adds `canonical_item_name`, `supplier_name`, and `payment_status`
alongside the preserved raw `Item` / `Supplier` columns. Its predecessor `USTore_sales_long_allocated.csv`
(raw `Item`/`Supplier` only) is retained as the pre-normalization intermediate.

| Column | Notes |
|---|---|
| `Date` | ISO 8601 `YYYY-MM-DD` |
| `Item` | Raw item name, **not yet canonicalized** |
| `Total Quantity` | Units sold that date |
| `Supplier` | Raw supplier string, **not yet normalized** |
| `imputation_flag` | `1` = row produced by proportional allocation, else `0` |
| `weight` | `0.5` for imputed rows, `1.0` for direct |

- **15,683 rows** — 14,014 direct + 1,669 allocated
- **88,481 units**, conserved exactly through allocation (audited)
- **Coverage:** 2024-05-02 → 2026-06-30, **411 distinct real tally dates**
- **305 distinct SKUs**

> Note: `USTore_sales_allocated.csv` and `USTore_sales_long.csv` are earlier intermediates. Ignore them.

### 4.2 **`calendar_ranges_2023_2026.csv`** — academic calendar → Dim_Date source

| Column | Notes |
|---|---|
| `start_date`, `end_date` | ISO 8601, inclusive range |
| `flag` | One of the five Dim_Date booleans |
| `semester_id` | e.g. `AY2526-T1`, `AY2425-ST` |
| `event_name` | Human-readable, for verification |

- **135 rows**, span 2023-01-09 → 2026-12-31, contiguous with no gaps
- Flags: `is_store_closed` 43, `is_event_day` 35, `is_exam_week` 28, `is_sem_break` 21,
  `is_enrollment_period` 8
- 12 semester tags: `AY2223-T2/ST` … `AY2627-T1`
- Derived by hand from the official UST Collegiate Calendar images (AY2022-23 … AY2026-27)

**Derivation conventions** (apply these if extending):
- National/religious holidays → `is_store_closed`
- Ceremonies, orientations, masses, festivities, "classes begin" → `is_event_day`
- "Exams begin" dates expanded to spans: prelims 6d, finals 7d, special-term finals 5d
- Registration → start of term → `is_enrollment_period`
- Breaks (Undas, Easter, academic, inter-term, Christmas) → `is_sem_break`
- Overlaps between flags are permitted and expected (e.g. Paskuhan inside a final-exam window)

### 4.3 **`USTore_inventory_excel_long.csv`** — monthly stock snapshots

Columns: `Category, Date, No, Item, Size, Location, Classification, Price, Quantity, OPEX, Notes`

- **19,049 rows**, coverage **Nov 2024 – Apr 2026**
- `Date` is ISO. **8,983 rows** carry the sheet's exact stated date; **10,066 rows** had only a month
  title and were set to the **first of that month**. A date ending `-01` is therefore usually a
  month-level value, not a literal snapshot on the 1st.
- Used as the weighting source for proportional allocation, and as the beginning-stock lookup for
  deriving estimated stock on hand.

### 4.4 Supporting mapping files (user-curated)

- `vocab_mapping_FINAL_v2.csv` — 564 entries, `raw_name` → `canonical_item_name`
- `allocation_groups.csv` — 45 rows / 18 price-groups, `generic_sales_name` → `inventory_variant`.
  Note `CONFIRM_belongs` is **blank on all rows**; every listed variant was treated as confirmed.
- `allocation_audit.csv` — 2,852 rows, traces every split (group, constituent, stock weight, basis,
  allocated units)

### 4.5 2023 batch data — **deliberately excluded from the daily series**

`2023_total_sales_by_batch__3_.xlsx`: 6 batch periods (Feb, Mar, Apr, May, Jun, Jul–Aug), 34 labels,
96 item-level totals. **No dates within a batch.** Only **1 of 34 labels** matches a 2024–26 SKU — the
data is aggregated in *both* time and item ("Shirts: 237" spans all shirt variants).

**Decision: use at batch level only.** See §8 for rationale — this is settled, do not relitigate.

---

## 5. Pipeline scripts

| Script | Does |
|---|---|
| `ustore_tbs_to_csv.py` | Wide→long melt of monthly TBS workbooks. Auto-detects date columns from header row 1; skips non-sales tabs (OPEX, VOUCHER, RE-CHECKING…); resolves `NO DATE` columns to the midpoint of surrounding tally days. CLI: `-o out.csv file.xlsx` |
| `Inventory_Excel_Converter.py` | Parses the 37-sheet inventory workbook. Auto-detects 4 header layouts (PLAIN / SIZE / CLASS / LOC) which can co-exist in one sheet; forward-fills item across size/classification breakdown rows. |
| `proportional_allocation.py` | Splits price-grouped rows by beginning-of-month stock share, largest-remainder rounding, tags `imputation_flag`/`weight`, emits audit CSV. |

All require `openpyxl`.

---

## 6. Target Star Schema

**`Fact_Sales`** (central) — `sale_id` PK, `product_id` FK, `date_id` FK, `quantity_sold`,
`cumulative_monthly_units`, `daily_depletion_rate`, `imputation_flag`, `tally_date_flag`,
`transaction_type`

**`Dim_Product`** — `product_id` PK, `item_name`, `category`, `unit_price_php`, `supplier_name`,
`lead_time_days`, `fsn_class` (F/S/N), `entry_date`, `is_active`

**`Dim_Date`** — `date_id` PK, `calendar_date` (ISO 8601), `semester_id`, `semester_week`,
`is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`, `is_tally_date`,
`is_store_closed`

**Outside the star:**
- `Dim_Parameters` — configurable EOQ/ROP inputs (`parameter_name`, `value`, `unit`, `last_updated`)
- `Event_Log` — staff-flagged unscheduled events; ETL reads it each cycle and updates `is_event_day`
  in Dim_Date. Does not join to Fact_Sales.

**`Dim_Inventory` is deliberately omitted** — stock level is derived
(`beginning_monthly_stock − cumulative_monthly_units`), not an independent entity. Storing it would
create a rapidly-changing dimension, violating Kimball. Beginning stock is a reference lookup joined at
query time.

**Storage:** SQLite primary; may migrate to CSV-per-dimension or PostgreSQL if Power BI connectivity
or concurrency demands it.

### Two supplier attributes live in Dim_Product, not a Dim_Supplier
Only `supplier_name` and `lead_time_days` are available. A two-attribute dimension doesn't justify the
join cost (Kimball). Also simplifies Power BI's supplier-grouped batch report.

---

## 7. Analytics specification

### 7.1 FSN classification
- **Metric:** Average Daily Units Sold (ADUS) = total confirmed units ÷ active selling days.
  - Historical tally records → denominator is **distinct tally dates**, not calendar days
  - Digital Tallying Interface records → denominator is **calendar days** since deployment
- **Primary cutoff:** 80th percentile (Pareto). Sensitivity analysis at **75th and 85th**; SKUs that
  shift category are flagged borderline for manual review.
- **Imputed records carry weight 0.5** in classification.
- Observation window starts at each SKU's `entry_date`, not a uniform date.
- **High-Velocity Limited (HVL):** sells >80% of initial consignment stock within first 14 days →
  bypasses the minimum-tally-date requirement, classified F on ADUS immediately.
- Items with <30 active tally dates → provisionally Slow *unless* Stock Depletion Rate is high.

**Category decision rules:** F → Prophet + ROP/SS/EOQ. HVL → Strategic Re-run Advisory. S → no Prophet;
promotional advisory; two consecutive low semesters → "Review for Discontinuation". N → excluded from
replenishment; return/markdown/discontinue advisory.

### 7.2 Prophet forecasting
Applied **only to F and HVL** SKUs.

- **Regressors:** `is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`,
  `is_store_closed`, plus `semester_week` (continuous). `is_store_closed` is kept separate from
  `is_event_day` — closures = zero expected demand, events = unusual nonzero demand; conflating them
  injects contradictory training signal.
- **Horizon:** 30 days (matches monthly billing cycle). Regenerated at each billing month start.
- **Fitting:** MCMC sampling, 1,000 samples → posterior uncertainty intervals, shown as confidence
  bands in Power BI.
- Historical tally data is treated as an **irregular time series** — Prophet receives tally dates as
  observed points. **No artificial daily interpolation of zero-sale values.**

**Data sufficiency tiers:**

| Observations | Treatment |
|---|---|
| ≥60 | Standard Prophet + 80/20 split |
| 30–59 | Simplified (reduced changepoints, fixed linear trend) + LOO-CV on most recent 20 |
| <30 | **No Prophet.** 30-day rolling average, flagged "Insufficient Data for Forecasting" |

**Current tier distribution across 305 SKUs: 87 at ≥60, 56 at 30–59, 162 at <30.**

### 7.3 Prescriptive formulas

```
ROP        = (Average Daily Demand × Lead Time in Days) + Safety Stock
SafetyStock= Z × σ_demand × √(Lead Time)
EOQ        = √((2 × D × S) / H)
```
- Z varies by FSN class: **F = 1.65** (95% cycle service level), **S = 1.04** (85%), **N excluded**.
  Adjustable per-SKU for high-profile items (e.g. graduation merchandise).
- `D` = annualized 30-day Prophet forecast; `S` = ordering cost/cycle; `H` = holding cost/unit/year.
  Both cost params are **management estimates**, stored in `Dim_Parameters`.
- EOQ sensitivity check: compute total cost at EOQ, 0.5×EOQ, 2×EOQ; confirm EOQ is the minimum.
- Lead time is a **generalized verbal estimate**, uniform default across SKUs, refinable per supplier.

### 7.4 Validation protocol
- Walk-forward validation, **80/20** chronological split; **70/30** alternative if too small for a
  three-way split; hyperparameter tuning uses a validation subset held out *within* training so the
  test set stays unseen.
- **Primary acceptance: MAPE ≤ 20%** for standard semester periods.
- **MAE is primary during semestral breaks** — MAPE is mathematically unstable near zero.
- RMSE as diagnostic, reported per SKU.
- **Naive baseline** (previous period = forecast) established first; Prophet must beat it.
- Failing SKUs → tune `changepoint_prior_scale`, `seasonality_prior_scale`; persistent failure →
  30-day rolling average + dashboard flag.
- **Split-period evaluation:** report performance separately for sparse historical-tally periods vs
  continuous Digital Tallying Interface periods.

---

## 8. Decisions already made (do not relitigate)

1. **Prophet over ARIMA and Holt-Winters.** Justified in §2.1.4 of the manuscript on multiple-seasonality
   handling and native user-specified holiday effects. Swapping models means amending Ch.2, Objective 3,
   §3.3.2, Table 3, and two figures.
2. **No daily smearing of 2023 batch data.** It would fabricate ~61 synthetic points per real datum;
   under a chronological split all of it lands in *training* (66% synthetic for top SKUs, 100% for the
   median SKU); it would falsely promote 162 sub-30 SKUs past the sufficiency gate; and uniform smears
   (CV 0.00) vs real bursty series (CV 1.24–1.56) would dilute the holiday-regressor coefficients that
   justify Prophet at all. Use 2023 for **FSN velocity, YoY seasonal context, and `entry_date` evidence**.
3. **`NO DATE` column (Aug 2024, 30 rows) imputed to 2024-08-13** — midpoint of surrounding tally days
   9 and 16. These are imputed, not observed.
4. **Inventory month-only sheets dated to first-of-month** rather than left blank.
5. **Proportional allocation weights by beginning-of-month stock**, largest-remainder rounding so
   allocated units sum exactly to the group total.

---

## 9. Known issues & gotchas

### Blocking / high priority

> **Supplier + item normalization RESOLVED** (work item #1) — see §4.1 / §10. The two bullets below
> are retained as the historical description of the problem that `normalize_suppliers_and_items.py`
> fixed. Remaining caveat: the `CENTRAL SEMINARY` merge is a flagged assumption pending staff confirmation.

- **Supplier names are badly unnormalized — 40 distinct strings for ~15 real suppliers.** Variants
  include `NAPOLIZ` / `NAPOLIZ ENTERPRISES` / `NAPOLIZ ENTERPRISES (CONSIGNMENT)`; `JYL` / `JYL ATHLETICA`;
  `VARSITY LIFE STYLE` / `VARSITY LIFESTYLE`; `STITCH CORP.` with five suffix variants; `THREADMARKED`
  ×3. `CENTRAL SEMINARY` is probably the same entity as
  `ASSOCIATION FOR THE EDUCATIONAL ASSISTANCE OF POOR SEMINARIANS, INC.`
  There is also a bare **`(Paid)`** supplier value — almost certainly a parsing artifact worth tracing.
  The `(CONSIGNMENT)` / `(PAID)` suffixes encode payment status, not supplier identity — likely belongs
  in its own field.
- **Item names are raw**, not canonicalized. `vocab_mapping_FINAL_v2.csv` exists but is not yet applied
  to the fact table.
- **Prophet underperformed — DIAGNOSED** (work item #6, see §10.6). All four hypotheses checked: the
  regressors were included (they *hurt*, not help), it genuinely loses to the naive baseline (tuned
  Prophet wins only 8% of the ≥60 tier), the high MAPE is intrinsic burstiness (not break-period
  instability), and it fails even on the best-case ≥60 SKUs. Root cause is structural (smooth
  trend+seasonality vs sparse bursty consignment series), not a fixable config oversight — though tuning
  halves the error and vanilla Prophet must never be used. See §10.6 for the recommended path.

### Data quality

- **Allocation leaned heavily on fallbacks.** Of 2,852 constituent allocations: 1,274 used exact-month
  stock, **1,266 used nearest-month** (some ±20 months), 312 had no stock at all (equal split).
  Inventory covers Nov 2024–Apr 2026 but grouped sales run May 2024–Jun 2026; six months
  (2024-05, -08, -09, -10, 2026-05, -06) have no stock to weight by. Consider capping nearest-month
  distance (e.g. beyond ±3 months → equal split).
- **Two tally dates fall on `is_store_closed` days** — 2025-06-12 (Independence Day) and 2025-11-30
  (Bonifacio Day). Either the store opened anyway or the tallies are misdated. **Unresolved**, and it
  matters because closure flags feed the depletion-rate denominator.
- **14 of 411 tally dates are Sundays** (3.4%) — the store is not strictly closed Sundays, so they
  can't be blanket-excluded from selling-day counts.
- **4 items** where the sheet's own `TOTAL QUANTITY` ≠ sum of its per-date cells (source errors,
  faithfully reproduced). E.g. Jul 2025 "New GT Shirts": cells sum to 8, stated total 5.
- **May 27 2024 "Keychain":** 1 pc at ₱160 but ₱0 sales recorded.
- **Manuscript naming inconsistency:** prose (§3.2, §3.3.2) says `is_store_closed`; Figure 5 labels the
  same field `is_suspension_day`. Reconcile before building Dim_Date.
- **`RE-CHECKING FEB 2025` sheet was skipped** in favour of `FEB 2025 - TBS`. If the re-checked version
  is the corrected copy, this needs revisiting.

### Structural characteristics to design around

- **55% of consecutive per-SKU observations are >1 day apart**; max gap **67 days**. Episodic recording
  is the norm, not the exception.
- Real series are **bursty** — CV 1.24–1.56, e.g. UST College ID Lace mean 9.2 / max 140.
- Median SKU has only **25 observations**.

---

## 10. Open work items

1. ~~Apply `vocab_mapping_FINAL_v2.csv` to canonicalize `Item`; build supplier normalization map and
   split payment-status suffixes into their own field.~~ **DONE** — `normalize_suppliers_and_items.py`
   writes `USTore_sales_long_allocated_normalized.csv` (rows + units conserved: 15,683 / 88,481).
   305 raw items → 297 canonical (all mapped, 0 unmatched). 40 supplier strings → **17 suppliers** via
   an explicit curated map (`supplier_normalization_map.csv`), not regex — three parentheticals are
   *not* payment status (`(BLEEVES)` = product line, `(SHIRT HAPPENS)` = manufacturer, bare `(Paid)` =
   artifact). New `payment_status` field: CONSIGNMENT 3,411 / PAID 3,271 / UNSPECIFIED 9,001.
   The `(Paid)` artifact (item #5) is resolved here → **STITCH CORP.**, PAID (all 180 rows are Jul-2025
   STITCH CORP. BLEEVES-line items). `CENTRAL SEMINARY` ← `ASSOCIATION FOR THE EDUCATIONAL ASSISTANCE…`
   merge is applied but flagged in the audit map as an assumption to confirm with staff.
2. ~~Derive `daily_depletion_rate` and `cumulative_monthly_units`.~~ **DONE** — `derive_depletion.py`
   writes `USTore_fact_sales_derived.csv` at **(canonical_item_name, Date)** grain (aggregating the 25
   duplicate product-date pairs from name merges / multi-supplier items; supplier belongs to Dim_Product,
   not this grain). 15,654 rows, units conserved (88,481), monthly reset audited. All four undecided
   rules were implemented **with the suggested defaults**, each a flip-able constant in the script:
   - **#1 first-ever observation** → denominator = selling days from **month start** to the obs date
     (`FIRST_OBS_DENOM="month_start"`; `"entry_date"` alt available). 297 first-obs rows.
   - **#2 Sundays** → non-selling **unless the Sunday is a tally date**. Folded into the selling-day
     predicate: `selling_day = is_tally_date OR (NOT is_store_closed AND NOT Sunday)`. A recorded tally
     is treated as proof the store sold that day — this also cleanly absorbs the two closed-day tallies
     (see item #5).
   - **#3 long gaps** → calendar gap > 30 days ⇒ `daily_depletion_rate = NULL` + `long_gap_flag=1`
     (no smearing). 290 rows flagged (max gap 517 days).
   - **#4 month boundaries** → intervals **span months**; only `cumulative_monthly_units` resets.

   All current data is historical tally (`tally_date_flag=1`); the DTI `denominator=1` branch is dormant
   until the Digital Tallying Interface ships. Output columns add `cumulative_monthly_units`,
   `daily_depletion_rate`, `selling_days_in_interval`, `prev_obs_date`, `first_obs_flag`, `long_gap_flag`.
   ```
   cumulative_monthly_units = SUM(quantity_sold) OVER (
       PARTITION BY product_id, YEAR(calendar_date), MONTH(calendar_date)
       ORDER BY calendar_date ROWS UNBOUNDED PRECEDING)

   daily_depletion_rate = quantity_sold ÷ selling_days_since_previous_observation
       -- selling_days = is_tally_date OR (NOT is_store_closed AND NOT Sunday)
       -- tally_date_flag = FALSE (DTI data) → denominator is 1  [dormant: no DTI data yet]
   ```
3. ~~Build `Dim_Date` from `calendar_ranges.csv` (expand ranges to one row per date;
   add `semester_week`, `is_tally_date`).~~ **DONE** — `populate_dim_date.py` builds the table into
   `ustore.db` (via `create_schema.py`) and exports `dim_date.csv`. **1,461 rows** (2023-01-01 →
   2026-12-31). Ranges expanded to per-day flags: `is_store_closed` 43, `is_event_day` 54,
   `is_exam_week` 173, `is_sem_break` 185, `is_enrollment_period` 34 (day-counts, vs the CSV's *range*
   counts). `is_tally_date`=1 on all **411** tally dates. `semester_id`+`semester_week` assigned to every
   day (1,453; the 8 pre-calendar days 2023-01-01..08 left null) from **12 data-derived term windows**:
   term start = earliest calendared date per `semester_id`, each term running to the day before the next
   begins (contiguous, no gaps/overlaps; verified at the AY2425-T1→T2 boundary). Replaces the old
   hardcoded 3-term `TERM_STARTS`; a `TERM_START_OVERRIDE` dict is available if the manuscript needs a
   specific "classes begin" anchor instead of the earliest calendared date. Flag overlaps preserved
   (e.g. the two closed-day tallies carry both `is_store_closed`+`is_tally_date`).
   **Star tables loaded** (`load_star_tables.py`): `Dim_Product` (517 = 297 sold + 220 inventory-only;
   supplier from normalized sales, category/price from inventory, `entry_date` = earliest appearance,
   `lead_time_days`/`fsn_class` still NULL) and `Fact_Sales` (15,654 rows, 88,481 units, 0 FK orphans,
   290 long-gap depletion NULLs). `ustore.db` star is now queryable end-to-end.
4. Map 2023 batch categories → SKU groups for batch-level FSN. **BLOCKED** — `2023_total_sales_by_batch__3_.xlsx`
   is not in the repo/working dir (no xlsx present). Needs the source file before it can proceed.
5. Resolve the two closed-day tally dates. ~~and the `(Paid)` supplier artifact~~ — the `(Paid)`
   artifact is **resolved** (STITCH CORP., PAID; see item #1). The two closed-day tallies
   (2025-06-12, 2025-11-30) are now handled *computationally* in item #2 (a recorded tally overrides the
   closure → counted as a selling day), but the underlying provenance question — store opened anyway vs.
   misdated tally — is still **unconfirmed** and needs a staff check.
6. ~~Diagnose Prophet per §9.~~ **DONE** — `prophet_diagnostic.py` (results in
   `prophet_diagnostic_results.csv`). Fit the **89 SKUs in the ≥60 tier** (best case), 80/20 chronological
   split, four forecasters. **Median test MAE: naive_last 2.54 · naive_mean 2.94 · prophet_base 6.93 ·
   prophet_reg 10.34 · prophet_tuned 3.35.** Answers to §9's four questions:
   - **(a) regressors don't rescue it** — `prophet_reg` (with all six §7.2 regressors) is *worse* than
     vanilla (beats it on only 45% of SKUs; median MAE 10.34 vs 6.93). The continuous `semester_week`
     regressor is the prime suspect — it extrapolates badly across semester resets in the test tail.
   - **(b) it genuinely loses to naive**, not merely misses 20% MAPE — vanilla beats naive on 4%,
     `prophet_reg` 2%, and even the **tuned** Prophet (damped `changepoint_prior_scale=0.01`,
     `seasonality_prior_scale=0.1`, `yearly_seasonality=3`) beats naive on only **8% (7/89)**.
   - **(c) high MAPE is intrinsic burstiness, not break-period instability** — semester-only MAPE
     (221.8%) ≈ all-test MAPE (222.5%). So MAE is the right primary metric — and Prophet loses on MAE too.
   - **(d)** restricted to the ≥60 tier and it still fails; the 7 tuned-winners are indistinguishable
     from losers by volume/CV (wins ≈ noise, no viable Prophet sub-segment).

   **Root cause:** structural mismatch — Prophet's smooth trend + Fourier-seasonality decomposition does
   not fit sparse, irregular, bursty (CV 1.2–1.6) consignment series. **Tuning halves the error vs
   defaults**, so if Prophet stays it must never run vanilla. Actionable now: (1) re-encode or drop
   `semester_week` (linear-continuous form actively hurts); (2) treat the §7.4 rolling-average/naive
   fallback as the *primary* forecaster (persistent Prophet failure is the norm, 92%, not the exception);
   (3) before any §8-decision-#1 change, re-run as **walk-forward 30-day windows** (matches the production
   horizon; fairer to Prophet than one long 80/20 tail) — caveat noted, but regressors-hurt + 8%-win is
   unlikely to fully reverse.
7. Optionally add `is_imputed_date` / `derivation_method` flags so imputations stay traceable in Ch.4
   data-quality reporting.
