# Status — 2026-08-05, branch `tyrone`

Where the pipeline stands after seeding Phase 4 with real (provisional) USTore estimates, what is
still open, and what Phase 6 (Power BI) needs from here. Written as a handoff snapshot, not a
manuscript section — see `CHANGES_tyrone.md` and `docs/BUILD_PLAN_RECONCILIATION.md` for the fuller
history this builds on.

---

## 1. Phase status

| Phase | Scope | Status |
|---|---|---|
| 0–1 | Setup, schema, ETL | Done |
| 2 | FSN classification | Done — 58 F / 228 S / 233 N, 6 HVL |
| 3 | Prophet forecasting | **Blocked on cmdstan** (B5). Superseded in practice by the 8-method benchmark; prescriptive math runs on `rolling_mean_30`, not Prophet |
| 4 | ROP / Safety Stock / EOQ | **Real per-SKU estimates now seeded** (this session) — see §2. Still provisional, still pending the USTore site visit (Block 5) |
| 5 | Flask tallying interface | **Not started.** `Event_Log` is 0 rows |
| 6 | Power BI dashboard | **Not started — the next task.** See §4 |
| 7 | Chapter 4 write-up | In progress alongside the above |

---

## 2. What changed this session (Phase 4)

`step5_prescriptive.py` no longer runs an abstract 5×5 lead-time × cost-ratio grid. It now uses:

- **Lead time** — real per-product value from `Dim_Product.lead_time_days`, set by
  `step5a_set_lead_times.py` (new file) from a name-keyword classifier: jacket/windbreaker → 28d,
  embroidered → 18d, shirt/jersey/polo/tee → 14d, everything else → 18d default. Tier counts:
  simple_dtf_puff_shirt 122, embroidered_shirt 23, jacket 26, default 348 (144 confirmed
  non-apparel + 204 uncategorized).
- **Holding cost** — derived, not guessed: `H = 0.25 × ₱210,000 (inventory-value midpoint) ÷
  36,051 (units on hand, latest complete inventory snapshot) = ₱1.4563/unit/year`. One blended
  rate across the whole catalogue — it cannot distinguish a keychain from a jacket, because
  USTore gave one total value, not a per-item breakdown.
- **Ordering cost** — flagged as ambiguous rather than resolved. Every SKU is priced under both
  `low_admin_cost` (₱1,250/order) and `high_goods_value` (₱200,000/order, USTore's literal
  monthly figure). EOQ swings **exactly 12.65× (`√(200,000/1,250)`)** between the two, for every
  SKU — the swing itself is the finding to bring back to USTore.
- All 18 assumption rows are in `Dim_Parameters`, each suffixed
  `[PROVISIONAL - pending Block 5 (USTore site visit)]`.
- `Result_Prescriptive`: 158 rows = 79 priced SKUs (26 F + 53 S) × 2 ordering-cost scenarios.
  0 of the 26 Fast SKUs needed the coefficient-of-variation sigma fallback; 5 Slow SKUs did
  (`SM Tiger Plushie Big V2`, `Sci Ballpen`, `Sci Notebook`, `UST Logo Embro Polo Shirt (2XL-3XL)`,
  `UST Toddler Hoodie`).
- Schema: `create_schema.py`'s `Result_Prescriptive` DDL gained `lead_time_category`,
  `ordering_cost_scenario`, `ordering_cost_php`, `holding_cost_php_per_unit_year` and dropped the
  grid-only columns. `ustore.db` was rebuilt from scratch to pick up the new DDL (`CREATE TABLE IF
  NOT EXISTS` doesn't alter an existing table).
- **Not yet committed.** `step5a_set_lead_times.py` is untracked; `step5_prescriptive.py` and
  `create_schema.py` are modified, all on `tyrone`, uncommitted.

This **partially resolves B9** below (lead time is no longer NULL; holding/ordering cost are real
numbers instead of a grid) but does not close it — the three numbers are still provisional
estimates, not confirmed USTore figures.

---

## 3. Changes / blocks / problems still open

Carried forward from `CHANGES_tyrone.md` §8, with status updated where this session moved something.
**B1–B15 were deliberately not decided by any Claude session — every one needs a human call.**

| # | Decision | Status | Blocks |
|---|---|---|---|
| B1 | Make the repo private | Open — still public with supplier names, prices, sales volumes | Nothing technical; a disclosure risk before the defence |
| B2 ⚠ | Acceptance criterion: MAPE ≤20% is structurally unreachable on intermittent demand (the optimum forecast is zero) | Open — needs adviser sign-off on the reframing (service-level/fill-rate headline, MASE underneath) | B3 |
| B3 ⚠ | Which forecasting model to select | Open — cannot run on MASE alone (the MASE winner prices 0/266 SKUs). Needs B2 first, then a target service level | Ch4 model-selection story; **the Demand Forecast dashboard view** |
| B4 | `semester_week`: drop or cyclically re-encode | Open | The Prophet regressor spec (moot while B5 is blocked) |
| B5 | Prophet / `prophet_flatlog` | **Blocked on a cmdstan/RTools toolchain build.** Not re-run since Block 1/2.2 — stored Prophet forecasts, if any, are stale | Whether Prophet appears in Ch4 at all |
| B6 | Price-suffix SKU merge ruling (71 suffixed, 12 twins, 4 families with real sales) | Open — needs USTore confirmation per family | FSN split for those 4 families |
| B7 | May 2024 DSR (4,318) vs TBS (4,022) — which figure is the true total | Open | Closes a data-provenance divergence |
| B8 | `is_store_closed`: does it mean "closed" or "holiday"? (15 tally dates on flagged closures, month-day pairs repeat annually and match PH public holidays) | Open | The depletion-rate denominator |
| **B9** | Lead time / ordering cost / holding cost | **Partially resolved this session** — see §2. Still provisional, pending USTore confirmation | Whether the site visit is a full estimation session or a 3-number lookup against `Dim_Parameters` |
| B10 | Inventory coverage position (76–82 of ~300+ items have any stock record, ~14–17% of units) | Open — needs a group decision on scope/definition | **The Stock Status dashboard view** (§1.5 of the build plan makes it the entry point of the five-view narrative) |
| B11 | Holding cost under consignment (university may not own the stock) | Open — current `H` treats it as owned inventory | Whether EOQ is framed as cost-optimal or as an order-batching heuristic |
| B12 | The four attribution commits on `neil` | Open, recommendation is to leave them | Nothing blocking |
| B13 | Post-defence revision window (two Figure 3s, missing Table 1, etc.) | Open — status unknown | Four manuscript fixes, ~1 hour once confirmed |
| B14 | Which 30-day demand anchor to standardise on (2026-07 gives 79/266 SKUs priced; 2026-06 gives 130) | Open | A methodological note pre-empting a panel question |
| B15 | Croston vs TSB for the S/N tiers (TSB: MASE 5.33 vs Croston 12.50, prices all 266) | Open, follows B3 | Which intermittent-demand method Ch4 presents |

**Also still true:**
- Phase 5 (Flask tallying interface) has not been started at all — `Event_Log` is 0 rows, there is
  no live write path into `Fact_Sales`, and `is_store_closed` has no interface to toggle it from.
- `ustore_neil_backup.db` (the pre-rebuild database from the `neil` branch) is still sitting in the
  working directory, untracked — fine to keep for now, worth deleting once nobody needs to diff
  against it.

---

## 4. Next task: Phase 6 — Power BI dashboard

Per `USTore_Build_Plan.pdf` (Phase 6) and the build plan's own principle: **Power BI does not run
Prophet, EOQ, or any other model — everything is computed in Python and written to `ustore.db`
result tables first.** The dashboard is a read-only presentation layer over:
`Dim_Product`, `Dim_Date`, `Fact_Sales`, `Dim_Parameters`, `Result_Prescriptive`, and (once Phase 3
is unblocked) a `Result_Forecast` / `Result_Forecast_Metrics` pair.

### 4.1 The five views, in narrative order

| # | View | Primary source table(s) | Ready now? |
|---|---|---|---|
| 1 | **Stock Status** (entry point) | `Fact_Sales` (`days_of_supply`, `is_censored`), inventory coverage | **Blocked on B10** — coverage is only ~14–17% of units; the view needs a stated scope before it can be built honestly |
| 2 | **FSN Classification** | `Dim_Product.fsn_class`, `is_hvl` | Ready — 58 F / 228 S / 233 N / 6 HVL, stable since Phase 2 |
| 3 | **Demand Forecast** | `Result_Forecast` (Prophet) or the benchmark's `rolling_mean_30` | **Blocked on B3/B5** — no forecast table is currently authoritative; the benchmark exists (`model_benchmark_results.csv`) but nothing has been selected as *the* number to display |
| 4 | **Restocking Advisory** | `Result_Prescriptive` (ROP / Safety Stock / EOQ) | **Buildable now**, with caveats surfaced on-screen: label everything provisional, show both ordering-cost scenarios (don't collapse to one), note which SKUs used the sigma fallback |
| 5 | **Automated Batch Sales Report** (PDF export) | `Fact_Sales` + `Dim_Product.supplier_name` / `payment_status` | Ready — supplier normalisation and payment_status split are already in `Dim_Product` |

Plus: **calendar-contextual interpretation cards** (build plan, Phase 6) — short annotations tied
to `Dim_Date` flags (`is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`) so a
demand spike/drop on the dashboard reads as "exam week" or "enrollment period," not as an
unexplained anomaly.

### 4.2 What can start immediately (views 2, 4, 5)

1. **Connection**: Power BI → SQLite. Power BI has no native SQLite connector; the usual path is
   an ODBC driver (e.g. the Devart or the community SQLite ODBC driver) or exporting the needed
   result tables to CSV/Parquet as a scheduled Python step and pointing Power BI at those files
   instead. Given `ustore.db` is gitignored and rebuilt by script, an explicit "export result
   tables" step (`Dim_Product`, `Fact_Sales`, `Dim_Parameters`, `Result_Prescriptive`) is probably
   more portable for a defence machine than relying on an ODBC driver being installed there.
2. **FSN Classification view**: pie/bar of F/S/N counts, HVL flag, borderline-item list from the
   Phase 2 sensitivity table (75/80/85 percentile cutoffs) if that was retained.
3. **Restocking Advisory view**: per-SKU ROP / Safety Stock / EOQ table from `Result_Prescriptive`,
   filterable by `ordering_cost_scenario`. The 12.65× EOQ swing between scenarios should be a
   visible toggle or side-by-side, not hidden behind a default. Every card/table needs the
   `is_provisional` flag surfaced as a visible label, not just a database column.
4. **Batch Sales Report view**: supplier-grouped sales totals with PDF export (Power BI's built-in
   export-to-PDF covers this). Needs `payment_status` (CONSIGNMENT/PAID/UNKNOWN) as a filter per
   the build plan's supplier-grouping requirement.

### 4.3 What's blocked and on whom

- **Stock Status (view 1)** needs the whole group's B10 call: reconcile coverage with USTore staff,
  or explicitly scope the view to the ~14-17% covered subset and put the coverage % on the
  dashboard itself rather than silently omitting uncovered SKUs.
- **Demand Forecast (view 3)** needs B3 (which model/metric wins) decided, which itself needs B2
  (adviser sign-off on the reframed acceptance criterion) decided first. Until then this view has
  no authoritative number to show — `rolling_mean_30` (already feeding `Result_Prescriptive`'s `D`)
  is the interim candidate, but that's a `step5_prescriptive.py` internal choice, not yet a Ch4
  decision.
- No `Result_Forecast` / `Result_Forecast_Metrics` tables currently exist in the schema — they're
  in the original `create_schema.py` design intent but were never populated (Prophet blocked, and
  the benchmark writes to loose CSVs, not the database). Wiring the benchmark's winning method into
  a proper result table is a prerequisite for view 3 regardless of which method B3 picks.

### 4.4 Suggested order

Build 2, 4, 5 now (no open decisions block them) while B2/B3/B10 get resolved in parallel with
USTore/adviser. Add 1 and 3 once those land. This matches the build plan's own sequencing note that
Phase 6 is a group task done last, after every per-SKU number it displays already exists in the
database.
