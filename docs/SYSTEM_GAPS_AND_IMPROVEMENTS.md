# System gaps and improvements (as of 2026-10-02, branch `neil`)

A review of the whole system — pipeline, models, database, backend, frontend, tests and repository —
listing what is missing, what could give wrong results, and what can be made better or faster.

Every number below was measured on 2026-10-02 against the current `ustore.db` (sales to 2026-07-31,
after the full-July update), the code on `neil` (commit `25826b0`), and GitHub, unless it says it comes
from an older document.

Three kinds of fix appear below:

- **Code**: a change the team can make in the repo.
- **Group decision**: something the project rules reserve for the group (the controlled vocabulary,
  the calendar ranges, the expected values in verification scripts are never edited without one).
- **USTore**: needs information or a change in practice from the store.

---

## In short: can it scale?

**Not yet, and it needs to.** The system works for its current scope (one store, one laptop, a capstone
demonstration), but it cannot keep running for years, through sheet changes and with several staff,
without a developer. The limits are operational, not computational: the data is small and the models
are fast. None of the fixes needs a redesign.

| Dimension | Today | What stops it scaling | Fix (section) |
|---|---|---|---|
| Data volume | About 84,000 sales rows; full run 114 s, of which the forecasts take 13 s | Not a blocker. Every run re-reads every workbook, so run time grows with each year added | Incremental runs (8) |
| Time | Calendar ends 2026-12-31; the list of workbooks is fixed in code | Stops accepting entries on 1 January 2027; next year's workbook is never read | Extend the calendar every year (2.1); read every workbook in `rawdata/` (6.2) |
| Change | Any renamed or new item name stops the pipeline | Every sheet redesign needs a developer | Rename button, import matcher, provisional items (6.1) |
| People | No login; development servers on localhost; one database file with no scheduled backup | Not safe or stable for several staff over a network; one lost file loses everything since the last vault commit | Login and restricted CORS (1.2); production server and backups (1.3) |
| Data quality | Stock signal on 16.8% of rows; bulk orders mixed with walk-in sales; costs provisional | Forecasts and reorder points cannot get better than they are now | USTore practices (3.2, 3.4, 5) |
| More stores or heavy use | SQLite in WAL mode | Fine for one store and a few users; many simultaneous writers or several stores would need PostgreSQL (Chapter 3, §3.1.4 already allows the move) | Later |

To make it scale, in order: extend the calendar (2.1, 2.2); read new workbooks automatically (6.2);
handle renamed items without a developer (6.1); fix `transaction_type` (3.1); add a login, a production
server and backups (1.2, 1.3); then the USTore practices (3.2, 3.4, 5).

---

## Priority list

| # | Gap | Why it matters | Type | Effort |
|---|---|---|---|---|
| 1 | GitHub repository is **public** and its history holds 120 unencrypted client data files | Supplier names, prices and sales volumes are readable by anyone | Owner action, then group decision | S / M |
| 2 | Calendar (`Dim_Date`, `calendar_ranges.csv`) **ends 2026-12-31** | From 1 Jan 2027 tally entries are rejected and the calendar check goes blind | Group decision | S |
| 3 | **First-term enrollment** (July–August) is missing from the calendar in every year | The biggest demand event of the year is invisible to the forecast | Group decision | S |
| 4 | `transaction_type` **case mismatch**; non-sales counted as demand | App-entered sales vanish from category forecasts; damaged/promo/transfer items inflate FSN, forecasts and reorder points | Code | S (**fixed 2026-10-02**, see 3.1) |
| 5 | **No login** on the API; CORS open to every site | Anyone who can reach the server can add products, tallies, closures or start a pipeline run | Code | M |
| 6 | Renamed item names **stop the pipeline** | Every sheet redesign needs a developer (50 names in July) | Code + group decision | M (**done 2026-10-02**, see 6.1) |
| 7 | **New tally workbooks need a code edit** | Next academic year's workbook is never read unless `step0` is changed | Code | S (**done 2026-10-02**, see 6.2) |
| 8 | Fast list **keeps dead items** | 28 of 58 Fast items sold nothing in 90 days, 13 in a year | Group decision | S |
| 9 | Bulk / organisation orders not logged separately | Largest error cause tested: item error 64.5% → 38–51% if known | USTore + code | M |
| 10 | Pinned checks fail and docs are stale after the July update | The team cannot tell a real failure from a known one | Group decision | S |
| 11 | Runs on **development servers**; no scheduled **database backup** | Not stable for daily use; a lost or corrupted `ustore.db` loses all entries since the last vault commit | Code | S |

The rest of this file explains each one, then lists smaller gaps and optimisations.

---

## 1. Security and data exposure

### 1.1 The repository is public, and its history contains client data

**Found:** an anonymous request to `https://api.github.com/repos/NeilSam0905/Capstone` returns
`"visibility": "public"`. The current branch tracks no plain data file (only `vault/*.enc`), but
**120 unencrypted data files** were committed before the vault existed and are still in the history:
`data/USTore_sales_long_*.csv`, `data/USTore_inventory_excel_long*.csv`, `data/allocation_groups.csv`,
`Model_Comparison.xlsx`, and more (`git log --all --diff-filter=A -- 'data/*.csv' '*.xlsx'`).

**Why it matters:** this is decision B1 in `docs/STATUS_AND_NEXT_STEPS.md`, still open. Supplier names,
prices and sales volumes are client data.

**Fix:**
1. Owner makes the repository private (GitHub → Settings → General → Danger Zone). Takes a minute.
2. Group decides whether to purge the history (`git filter-repo`, then a force push and every member
   re-cloning). Making it private first removes most of the risk; a purge cannot recall copies already
   cloned or forked.

### 1.2 The API has no login, and CORS allows every origin

**Found:** `backend/app.py` has 39 routes and no authentication of any kind. 11 of them write:
add a product (which also appends to the controlled vocabulary), tally entries and imports, inventory
counts and imports, closures, events, product status, and **pipeline run / stop**. `CORS(app)` allows
requests from any website. `app.run(debug=False, port=5000)` binds to localhost only, which is what
keeps this safe today.

**Why it matters:** fine on one laptop. The moment the backend is reachable by store staff over a
network, anyone on that network can change data or start a pipeline. There is also no record of *who*
entered a tally row (Event_Log has `created_by`; Fact_Sales does not).

**Fix (before any shared deployment):** a login (even one shared staff PIN to start), CORS limited to the
dashboard's own address, and an `entered_by` column on app-entered Fact_Sales rows.

### 1.3 Development servers and no scheduled backup

**Found:** the README starts the system with `python app.py` (Flask's built-in development server) and
`npm run dev` (Vite's development server). Everything staff enter lives in one file, `ustore.db`; the only
copy elsewhere is the encrypted vault, which is updated only when someone runs `python scripts/vault.py
lock` (or a full pipeline run does) and then commits.

**Why it matters:** development servers are not built for daily use by several people, and a deleted or
corrupted `ustore.db` loses every tally, count, closure and event entered since the last vault commit.

**Fix (code):** serve the API with a production server (`waitress` works on Windows), serve the built
frontend (`npm run build`), and take a nightly copy of `ustore.db` with SQLite's backup API (safe while
the app is running) plus a scheduled `vault.py lock`.

---

## 2. Calendar

### 2.1 The calendar ends on 31 December 2026

**Found:** `Dim_Date` runs 2023-01-01 → 2026-12-31 (`populate_dim_date.py`), and the last row of
`data/calendar_ranges.csv` is the AY 2026–27 Christmas break, marked *"truncated at 2026-12-31;
continues into January 2027"*. The only AY 2026–27 term present is the first term.

**Why it matters:**
- From 1 January 2027 the Tally Interface rejects every entry: `/api/tally` and `/api/tally/import`
  refuse dates not in `Dim_Date` ("Date not in the calendar").
- From early December 2026 the 30-day forecast window runs past the calendar, so the calendar check
  cannot see the January enrollment or the rest of the break.

**Fix (group decision; `calendar_ranges.csv` is a protected file):** add the AY 2026–27 second term and
special term from the published UST calendar, and extend `Dim_Date` (`populate_dim_date.py`'s end date)
to at least 2027-12-31. Needed before December.

### 2.2 First-term enrollment is not in the calendar in any year

**Found:** `calendar_ranges.csv` has 8 enrollment ranges, all of them January (second-term) or June
(special-term) registration. **None** is for the July–August enrollment before the first term, in any
year from 2023 to 2026. In `Dim_Date`, July and August 2026 have zero enrollment days.

**Why it matters:** the start-of-term surge is the largest single cause of forecast error (35% of the item
error, `docs/SESSION_SUMMARY_2026-09-30.md` §4.1), and July 2026 is where the calendar check made the
forecast worse (Lanyard @180: forecast 245, sold 560). The forecast cannot learn a rush the calendar
never names. The 2023 batch file shows the same July–August jump (Lanyard @180: 3.0× June in 2023,
3.6× in 2025), so it recurs every year.

**Fix (group decision):** check the published calendars for first-term enrollment dates and add them for
each year. Then re-run `scripts/test_calendar_adjustment.py` and test a per-category "raise for
enrollment" rule on the 30-start-date test (see 4.3). Do not ship a raise rule untested: every earlier
attempt to let forecasts go up lost.

---

## 3. Data

### 3.1 `transaction_type`: app sales lost, non-sales counted as demand

**Found:**
- The historical rows are stored as lowercase `sale` (84,430 of 84,430).
- The Tally Interface stores **uppercase** `SALE`, `DAMAGED`, `PROMO`, `TRANSFER`
  (`backend/validation.py`, `app.py` `add_entry` / `import_tally`).
- `step1b_categorize_products.py` and `step4c_category_forecast.py` keep only `= 'sale'` (case-sensitive
  in SQLite), so **app-entered sales are left out of the category forecasts**.
- `step3_fsn_classification.py`, `step4_forecast_model.py` and `step5_prescriptive.py` do **not** filter
  by type, so **damaged, promo and transfer entries count as demand** in FSN, the item forecast and the
  reorder points. This contradicts the manuscript's own risk mitigation (Table 4, Data Integrity).

**Why it matters:** no app entries exist yet, so today's numbers are unaffected. It breaks on the first
day staff use the interface.

**Fix (code):** store one spelling (lowercase, matching history) on write, or compare with
`LOWER(transaction_type) = 'sale'` everywhere; add the filter to steps 3, 4 and 5 and to any app query
that computes demand; add a test that inserts a `DAMAGED` row and a `SALE` row and checks both forecasts.

**Status: fixed 2026-10-02.**
- The Tally Interface now stores types in lower case (`backend/app.py`, single entry and import); the
  screens still show them in upper case.
- Every demand reader counts sales only, case-insensitively: steps 1b, 3, 4, 4b, 4c and 5, the dashboard's
  catalog stats (`backend/catalog.py`) and the API's sales, history, batch-report and last-sale queries
  (shared condition `db.SALE_ONLY`). Stock is not derived from Fact_Sales, so nothing needed the removals.
- `tests/test_transaction_type.py` (5 tests) puts 5 units sold as `sale`, 3 as `SALE`, and 7 + 2 + 4
  removed as damaged / promo / transfer through every reader; each must see 8. On the old code all 5 fail.
- On the current data nothing changes (every historical row is `sale`); the API totals stay 95,182 units.

### 3.2 Stock on hand is unknown for most items

**Found:** only 16.8% of Fact_Sales rows have any stock signal (`is_censored` not blank). Inventory counts
cover November 2024 – April 2026. `Inventory_Count` (the app's monthly count table) has 0 rows, and its
counts feed the API but **not** the ETL (`README.md`, "Known gap").

**Why it matters:** for 83% of rows a zero-sale day cannot be told apart from an out-of-stock day; the
Reorder screen's "on hand" is blank for most items; ROP cannot be checked against real counts
(Objective 4).

**Fix:** USTore enters a monthly count per item through the Tally Interface; wire `Inventory_Count` into
`step2_load_fact_sales.py` (a group decision, because it changes `is_censored` counts and the pinned
checks).

### 3.3 Renamed and split products break item history

**Found:** pairs such as Tote (Viva) → Tote Viva Santo Tomas, Tiger Claw → Tiger Paw Keychain, ID Case →
ID Case v2 are separate products, so each half has a short history (`SESSION_SUMMARY_2026-09-30.md`
§4.3, about 19% of item error). The July rename was handled (Pass 6 of the vocabulary); the older pairs
were not.

**Fix (group decision):** decide which pairs are the same product and add them to the vocabulary.

**Status: candidate list ready 2026-10-02.** `data/vocab_merge_candidates_2026-10.csv` lists 65 pairs with
the evidence for each (supplier, last sale of the old name, first sale of the new one, units on each side,
units the old name sold after the switch, price, word overlap) and an empty `same_product_decision`
column:

| Type | Pairs | Example |
|---|---:|---|
| Duplicate spelling | 2 | Corp Jacket v.3 / Corp Jacket V3 (same supplier, both selling; v.3 has no price) |
| Rename | 31 | Tote (Viva) → Tote Viva Santo Tomas; Tiger Claw Keychain → Tiger Paw Keychain |
| Price change | 5 | Pen @30 → Pen @40; Keychain @160 → Keychain @180 |
| Check | 25 | Two single-colour Quiana shirts → one two-toned row (a merge) |
| Keep separate? | 2 | Corp Jacket v.2 / V2 and Polo Shirt @800 / Poloshirt @800: different suppliers, selling at the same time |

Found by an automatic search (one name stops, a similar name from the same supplier starts within about
six weeks), so every row needs a person's yes or no. One convention to agree first: the July renames kept
the **old** product name; a merge can also keep the name staff use **now**.

### 3.4 Bulk orders are mixed with walk-in sales

**Found (current data, `data/what_if_test.csv`):** if large one-day purchases were known in advance,
fast-item error would fall from 64.5% to 50.8% (purchases over 20× a usual day) or 38.4% (over 10×),
and category error from 46.5% to 36.1% or 28.8%. No model change tested comes close.

**Fix:** USTore records organisation / bulk orders and pre-orders separately. Code: an "order type"
(walk-in / bulk / pre-order) on tally entries; the forecast models walk-in sales and adds known orders.

**Status: history ready for USTore 2026-10-02.** `data/bulk_day_candidates.csv` lists every past item-day
that sold more than 10× the item's usual selling day (the What-If test's rule): 354 item-days holding 24.8%
of all units sold, 132 of them over 20× (13.7%). The biggest months are January 2026 and December 2025.
Each row has an empty `bulk_order_confirmed` column for the store to mark which were organisation or
pre-orders, and which were ordinary rushes such as enrollment. The order-type field itself is not built
yet (schema and Tally Interface change).

### 3.5 Incomplete product details

| Gap | Count |
|---|---:|
| Products with sales but no supplier | 35 |
| Products with no forecast category ("Uncategorised") | 2 |
| Products with no price | 22 (1 with sales) |

These show as blanks on the dashboard and drop out of supplier totals. Fix via `supplier_mapping.csv` /
vocabulary review (group decision) or a USTore check.

**Status (2026-10-02):**
- **Supplier: fixed.** All 35 were Slow items that only ever sell inside a price-grouped row, so step 1
  (which reads suppliers before the split) never saw them by name; their 4,300 units are exactly the
  split-out units. `proportional_allocation.py` now carries step 1's cleaned `supplier_name` and
  `payment_status` onto each split row, and step 2 fills only missing suppliers from them
  (`fill_missing_suppliers`, most common value, as step 1 does; test added). Pipeline re-run: 38 products
  filled, 0 selling products without a supplier, no existing supplier changed, and sales, FSN, forecasts
  and reorder points are identical. Those 4,300 units no longer fall under "Unattributed" in the batch
  sales report. "New Keychain" sold under two suppliers (USTORE, Varsity Lifestyle) and keeps the more
  common one.
- **Category:** the 2 "Uncategorised" products are "Alessandrini" and "Hernandez", which look like
  people's names, never sold, and are Non-moving. Candidates for removal at the next vocabulary review.
- **Price:** the one unpriced product with sales is "Corp Jacket v.3", a duplicate spelling of the
  priced "Corp Jacket V3" (see 3.3); merging them fixes it.

### 3.6 Other data notes

- **July 2026 sheet:** 23 units that four Threadmarked items sold on 1–8 July disappeared when the rows
  moved from consignment to paid. Ask USTore whether that was intended.
- **Event and closure logs are empty** (`Event_Log` 0 rows, `Closure_Log` 0 rows), so event flags carry
  nothing yet.
- **2023 batch file** (`data/rebuild_batch_sales_2023.csv`): `docs/DIVERGENCE_REGISTER.md` #1 says
  "1 of 34 labels matching a current SKU"; it is **15 of 34**, covering 76% of the 2023 units (ignoring
  capitals). May's "ADDITIONAL 50 NOT REMITTED MARCH APRIL" (37) is a payment correction counted as sales.
  **Fixed 2026-10-02:** the figure is corrected (struck through, not deleted) in `DIVERGENCE_REGISTER.md`,
  `BUILD_PLAN_RECONCILIATION.md` and `REMEDIATION_MASTER_v2.md`; `rebuild_extract_tbs.py` now skips
  "not remitted" lines, and the regenerated file differs from the old one by that row only (100 rows,
  6,592 units; `REBUILD_PIPELINE.md` updated).

### 3.7 Questions for USTore

Data the store can supply that no code change can:

1. **Threadmarked, 1–8 July 2026:** were the 23 units removed on purpose when the items moved from
   consignment to paid, or should they be restored on the sheet?
2. **Enrollment dates:** first-term (July–August) enrollment dates for 2024, 2025 and 2026, and the
   AY 2026–27 second-term calendar (see 2.1, 2.2).
3. **Renamed products:** confirm the pairs in `data/vocab_merge_candidates_2026-10.csv`.
4. **Bulk orders:** mark the confirmed organisation / pre-order days in `data/bulk_day_candidates.csv`,
   and from now on record such orders separately.
5. **Monthly stock counts** for every item, entered through the Tally Interface's inventory card.
6. **Costs:** ordering cost per restock and holding cost, and **delivery dates** for each restock (see 5).
7. **Events and closures:** log them in the Tally Interface as they happen.

---

## 4. Forecasting and classification

### 4.1 The Fast list keeps items that stopped selling

**Found:** 28 of the 58 Fast items sold nothing in the last 90 days, 13 nothing in a year (e.g. New
Clappers). FSN averages over all history, so an item that sold heavily once stays Fast.

**Why it matters:** these items get forecasts and reorder alerts they do not need, and they flatter the
average MASE (forecast ≈ 0, sold 0).

**Fix (group decision):** the 30 Sep analysis found that skipping items with no sale in 90 days removes
about 15 dead items a month with no loss of coverage, and that rules based on recent sales cover 77–83% of
next month's sales against 69% now (`SESSION_SUMMARY_2026-09-30.md` §4.2, measured before the July
update; re-check). Changing the rule changes the pinned F = 58 check.

**Status (done, in code):** `step3` now never classes an item Fast if it sold nothing in the last
`STALE_DAYS = 90` days (it was 180). Re-checked on the July data with `tools/fsn_recency_check.py` (Fast
list rebuilt before each of the last 12 months): the 90-day rule drops 16.4 dead items a month on average
(180 days: 12.4) and next month's coverage is unchanged (63.0% with or without the rule). The 77–83% figure
above was for fixed-size recent-sales lists, a different rule, and was not re-tested. On the current data
Fast goes 58 → 30, and the 6 HVL items are all among those demoted (HVL 6 → 0). Takes effect at the next
pipeline run. The pinned F = 58 and `is_hvl` = 6 in `tools/assert_invariants.py` now fail and were **not**
edited (CLAUDE.md); the group approves new values.

### 4.2 Non-moving definition differs from the manuscript

**Found:** `step3` marks an item Non-moving only if it has **no Fact_Sales rows at all**. The 19 items that
appear on the sheets but never sold (rows of zeros) are classed **Slow**. The manuscript defines
Non-moving as no recorded sales.

**Fix (code + group decision):** classify zero-unit items as N; this changes S = 229 → 210 and N = 233 →
252 (pinned values).

**Status (done, in code):** `step3` classes an item N when it has no recorded sales, rows or not. The
percentile cutoff is still computed over every item with a row (zero-unit ones included), so this only
re-labels those 19 items and moves no other item's class. Together with 4.1 the current data gives
F 30 / S 238 / N 252 (S would be 210 from this change alone). Pinned S = 228 and N = 233 fail and were not
edited.

### 4.3 The calendar check: real but small, and sensitive to timing

**Found:** re-scoring with the 12 test windows starting on each of 30 different days, the calendar check
lowered error on average (categories 44.3% → 42.7%, items 63.5% → 61.1%) and helped at 19 of 30 (categories)
and 23 of 30 (items) start dates. With the data ending on 31 July it happens to hurt (46.5% vs 43.3%),
which is why the results workbook now looks worse than before.

**Fix:**
- Add the 30-start-date test to `scripts/` and report its average in the results workbook instead of one
  alignment (the script exists in a scratch folder; it reuses `test_calendar_adjustment.py`).
- After 2.2, test a per-category enrollment raise.

**Status:** the test is now `scripts/test_calendar_adjustment_starts.py` (writes
`data/calendar_adjustment_starts.csv`) and reproduces the figures above exactly. The results workbook has a
**Calendar Check** tab with all 30 alignments, and its Read Me quotes the 30-start average next to the
single-alignment caveat. The per-category enrollment raise is **still blocked on 2.2**: it needs first-term
enrollment dates in `calendar_ranges.csv`, a protected file.

### 4.4 Other model notes

- **Prophet** still loses on the current data (categories 55.3% vs 46.5%, items 88.6% vs 64.5%, same
  windows); keep it for the day-by-day shape only.
- **High-Velocity Limited** is a label only (6 items); the Stock Depletion Rate rule and the re-run,
  promotion and discontinuation advisories were not built (now listed as Recommendations in the errata).
- **Prediction targets** (#1 "will it sell in 30 days", #2 "weeks to next sale") looked useful, but their
  scripts were never committed (`SESSION_SUMMARY_2026-09-30.md` §3.9); none is in `scripts/` or `tools/`.

  **Status:** rebuilt from that description as `scripts/test_prediction_targets.py` (walk-forward over the
  last 12 months, no peeking; writes `data/prediction_targets_results.csv`). Not the laptop's code, and
  the laptop's gain does **not** reproduce. All items: #1 AUC 0.942 model vs 0.942 rule, #2 C-index 0.899 vs
  0.894. Items sold in the last 90 days (the honest subset): #1 AUC 0.835 vs 0.829, #2 C-index 0.784 vs
  0.767; on yes/no calls the simple rule is better (accuracy 82.6% vs 80.8%, wrong idle flags 5.8% vs
  9.3%). Not worth a "sales outlook" chip yet.
- **Prophet** and **HVL**: no change. Prophet already only shapes the days. The Stock Depletion Rate needs
  initial consignment stock, which exists for about a sixth of rows (`REMEDIATION_MASTER_v2.md`), and the
  advisories were moved to Recommendations in the errata, so they stay unbuilt.

---

## 5. Reorder (prescriptive) outputs

- **EOQ is not usable yet:** with the provisional costs, EOQ exceeds a full year of demand for 206 of 210
  items (low-cost scenario) and 210 of 210 (high-cost scenario). The app correctly shows an order-up-to
  quantity instead. Real ordering and holding costs from USTore are needed (Block 5 / B9).
- **Lead times are verbal** (14 / 18 / 28 days by garment type). Recording the order and delivery date of
  each restock would let them be measured per supplier.
- **ROP uses 365-day actual sales**, not the forecast. That is deliberate (it prices 210 items against 123
  on a 30-day basis) but means the forecast does not drive the reorder point; Chapter 3 now says so.

---

## 6. Using the system without a developer

### 6.1 New or renamed item names stop the pipeline

**Found:** `step1_apply_mapping.py` aborts on any name missing from the vocabulary; the July sheet renamed
about 50. The app's import rejects unknown names; there is no "rename item" action.

**Fix:**
- A **Rename item** button that keeps the product's history.
- An **import matcher**: in a test on the July names, supplier + price + shared words matched 46 of 50
  automatically, 42 correctly. The 4 mistakes (three jackets all matched to "Jacket") show it must accept
  only strong one-to-one matches and send the rest to a short review list in the app.
- Let the run continue with unconfirmed names as provisional new items instead of stopping.

Automatic vocabulary writes need a group decision (the vocabulary is a protected file; "Add item" is the
one agreed exception).

**Status: done 2026-10-02.** Decided that day to extend "Add item"'s exception to names a person settles
in the Tally Interface: each answer appends one row to `vocab_mapping_FINAL_v5.csv` (source
`tally_interface`, with a note saying what was confirmed and when); no existing row is ever changed, and
nothing is written without someone choosing it. **The group should confirm this extension.**
- **Provisional items** (`step1_apply_mapping.py`): an unknown name no longer stops the run. It is loaded
  as its own product under the sheet's name and written to a new `Name_Review` table with its evidence
  (dates, units, supplier, sheet price) and a suggestion. Names that differ from a vocabulary row only in
  capitals or spacing now match it (no two such names map to different items).
- **Matcher** (`scripts/name_matcher.py`): same supplier; shared words weighted by rarity; the sheet's
  ITEM PRICE in each name's latest month (not `unit_price_php`, which mostly comes from the inventory
  workbook and differs from it); and whether the old name stopped where the new one started. Pairs are
  settled one-to-one, best score first, so three jackets can no longer all point at "Jacket". "Strong"
  also needs a clear lead over the next candidate and no price change (sheet price or `@price` in the
  name). On the 53 names first seen on the July 2026 sheet, with the 50 Pass 6 rows taken out: **40
  strong, 39 of them the group's Pass 6 choice**; the other is "SM Tiger Plushie Big V2" → "SM Tiger
  Plushie V2", a pair already on the merge-candidate list. 12 weak (9 the right item); 1 with no
  suggestion, correctly (the new 4XL-5XL polo size). The earlier test's 46 automatic / 42 correct traded
  precision for coverage; here a person still confirms every one.
- **Names to review** (Tally Interface, top of the page, hidden when empty): each name with its evidence
  and suggestion, and "Yes, same item" / "A different item…" / "It is a new item". A strong suggestion
  gets the primary button; "Review N strong suggestions" saves them all after showing the full list. A
  settled sheet name takes effect at the next run, and the staleness banner counts it until then.
- **Rename Item** (Monthly Inventory Count card): the sheet now calls an item something else. The new name
  is added as another name for the item, so it keeps its history and the name shown on the dashboard
  (the convention the July renames followed; showing the new name instead would rewrite existing
  vocabulary rows, the open question in 3.3).
- **Imports**: `/api/tally/import` and `/api/inventory/import` now recognise every sheet name the
  vocabulary maps, and **hold** a valid row with an unknown name (new table `Pending_Import_Row`) instead
  of rejecting it. A tally import appends, so re-importing the file after fixing a name would have
  doubled every other row. Held rows are applied when the name is settled; an import-only name can also
  be discarded.
- **`product_id` is now kept per item name** across runs; a new item gets the next free id. Step1 used to
  renumber `Dim_Product` 1..N in name order, so one new name shifted every later item's id, and the
  interface's tally entries and stock counts (which keep the id they were saved with) would have landed on
  other products. When a name is settled as an existing item, its interface rows move with it at the next
  run; rows left pointing at an item that no longer exists are reported, not deleted.
- **Checked:** on the current data the full pipeline produces every table and CSV identical to before
  (ids included; the only change is the new, empty `Name_Review`). End to end, on a copy with the 50 Pass 6
  rows removed: run 1 finished with 50 provisional items and every existing item kept its id; after
  settling them through the backend, run 2 gave per-item historical units identical to the real database
  (the one difference: the new 4XL-5XL item takes the sheet's name, because "new item" keeps the name as
  written), and a tally entry typed against a provisional item moved to "Tiger Headband (2 Designs)".
  Tests: `tests/test_name_matcher.py`, `tests/test_step1_provisional.py`, `tests/test_names_api.py`.
- **Still stops the run:** a supplier string missing from `supplier_mapping.csv` (a protected file). A new
  supplier on next year's sheet needs a group member to add it.

### 6.2 New tally workbooks need a code change

**Found:** `step0_convert_sales_with_zeros.py` reads a fixed list of four workbooks (`FILES`). The next
academic year's workbook will not be read until that list is edited. The app's import accepts only a long
table (Date / Item / Quantity), not the store's own monthly sheet layout.

**Fix (code):** let step 0 read every TBS workbook in `rawdata/` (the check `is_tbs_month_sheet` already
recognises the sheets), or accept the store's workbook through the app.

**Status: done 2026-10-02, both.**
- `step0_convert_sales_with_zeros.py` reads every `.xlsx` in `rawdata/` that has tally sheets
  (`find_workbooks`, `plan_sheets`); the fixed `FILES` list is gone. It skips the interface's `import_*`
  archives (already in `Fact_Sales`) and Excel `~$` lock files. A month found in two workbooks is read once,
  from the most recently modified, and the run says which copy it skipped. Without that, an updated copy of
  a workbook saved under a new name would have counted every sale in its months twice. No workbook at all
  raises `FileNotFoundError`, so the pipeline skips step 0 and keeps its CSV instead of writing an empty
  one. Step 1's ITEM PRICE reader uses the same plan. On the current `rawdata/` it picks the same four
  workbooks in the same order (the 2023 batch file has no tally sheets) and the output CSV is
  byte-identical.
- **Add Tally Workbook** (Full Pipeline Run card, `POST /api/tally/workbook`): accepts only a workbook with
  tally sheets, saves it into `rawdata/` under the store's own file name, moves an existing file of that
  name to `rawdata/replaced/` rather than overwriting it, and reports the months it covers and any it
  takes over from another workbook.
- Tests: `tests/test_step0_workbooks.py`, plus the workbook cases in `tests/test_names_api.py`.

### 6.3 Not built: zero-sale verification

The mid-day "Data Pending" alert and the "Unverified Zero" flag (manuscript §3.1.2) were not built; a day
with no entries is read as a day with no sales. The staleness banner is the only check. Moved to
Recommendations in the errata.

**Status (2026-10-02): left as a recommendation**, as the errata has it. Not built.

### 6.4 Leftover Power BI tab

`UST Prototype Design/src/App.jsx` line 25 still has `// TODO: Phase 2 — the Power BI report gets
embedded into this route`, and the tab shows a "Power BI report not configured" card. The group chose the
web dashboard: remove or hide the tab. `origin/gambe` holds one unmerged commit, "PowerBI placeholders added".

**Status (2026-10-02): finding corrected, tab kept.** The finding above is wrong. The tab embeds a
published Power BI report: the secure-embed link (`app.powerbi.com/reportEmbed`) is in
`UST Prototype Design/.env`, which is gitignored, so it exists only on the machine that has that file. The
"not configured" card appears only where `.env` is missing, as on a fresh clone. The tab was removed
on 2026-10-02 on the strength of this finding and restored the same day, unchanged. What is still true:
the `TODO: Phase 2` comment in `App.jsx` is out of date. Also, a teammate needs that `.env` file (or the
link in `.env.local`) to see the report. Whether the group keeps both the web dashboard and the Power BI
report is its decision.

---

## 7. Engineering, tests and documentation

### 7.1 Pinned checks fail after the July update

| Check | Failing | Old value → now |
|---|---|---|
| `scripts/verify_data.py` | 3 | total units 89,232 → 95,182; July delta +1,047 → +6,997 |
| `tools/assert_invariants.py` | 8 of 21 | rows 84,399 → 84,430; units 89,232 → 95,182; zero rows 68,541 → 67,708; products 519 → 520; S 228 → 229; products with rows 286 → 287, with units 266 → 268; and `Dim_Parameters` rows 0 → 17 (pinned before step 5 wrote its parameters; corrected from "7 of 22" on 2026-10-02, same result on the database from before the section 6 work) |
| `pytest` | 2 of 392 | `tests/test_degenerate_forecast.py` pins 84,399 / 68,541 and 266 |

These are known consequences of adding July, not bugs. Until the group approves new expected values, every
real failure hides among them.

### 7.2 Stale documentation

- `README.md`: 84,399 rows, 89,232 units, 519 products, S = 228, "tallies stop at 2026-07-08", "Run
  Full Pipeline ~50 s" (measured 114 s), "step1 ~10 s" (42 s).
- `docs/SESSION_SUMMARY_2026-09-30.md`: item 60.1% / category 42.1% (now 64.5% / 46.5%).
- `docs/DIVERGENCE_REGISTER.md` #1: "1 of 34" (15 of 34).
- `USTore_Forecast_Testing_Summary.xlsx` and `USTore_with_Synthetic_Current_Model_Results.xlsx` were built
  on data ending 8 July.

### 7.3 Branches

- `main` is **76 commits behind** `neil`; GitHub's default branch shows an old version of the project.
- `origin/tyrone` has 2 commits not in `neil`: the acceptance standard / stocking policy (`d5275e6`) and
  the fsn_class leak fix (`d618462`). `d5275e6` also deletes the commit and scope policies from
  `CLAUDE.md`; decide before merging.
- `origin/gambe` has 1 commit not in `neil` (Power BI placeholders).

### 7.4 Large files

`backend/app.py` is 2,044 lines and `TallyInterface.jsx` 1,369. Splitting the API into Flask blueprints
(products, tally, inventory, forecast, pipeline) and the Tally Interface into one component per card would
make reviews and fixes easier. Low priority.

---

## 8. Speed

A full run took **114 s** on 2026-09-30:

| Step | Time |
|---|---:|
| step1 (vocabulary + prices) | 41.9 s |
| step0 (read tally workbooks) | 31.4 s |
| step2 (load Fact_Sales) | 16.2 s |
| step4 + step4c (forecasts) | 13.1 s |
| everything else | ~11 s |

- **step1 re-opens every tally workbook** just to read the ITEM PRICE column, after step0 already opened
  the same files. Writing prices out during step0 would remove most of step1's 42 s.
- **openpyxl read-only mode** loads and scans the largest workbook in 3.3 s against 6.6 s for a normal
  load. Step 0 uses random cell access (`ws.cell(r, c)`), so switching needs a row-by-row rewrite of
  `convert_sheet`.
- **Every run rebuilds everything**, even when only today's tallies changed. An incremental mode (skip
  step 0 and step 1 when no workbook or mapping file changed) would bring a routine run down to about 40 s.

---

## Suggested order

1. Owner: make the repository private (1.1).
2. Group: extend the calendar past 2026 and add first-term enrollment (2.1, 2.2) — before December.
3. Code: fix `transaction_type` and add the test (3.1) — before staff use the Tally Interface.
4. Group: approve the post-July expected values; update README and docs (7.1, 7.2).
5. Code: read new workbooks automatically; rename button and import review list (6.1, 6.2) — **done
   2026-10-02**; the group still needs to confirm the vocabulary-write extension in 6.1.
6. Group: Fast-list rule and Non-moving definition (4.1, 4.2); renamed product pairs (3.3).
7. USTore: monthly counts, bulk-order logging, real costs, delivery dates (3.2, 3.4, 5).
8. Code: login, production server and backups before any shared deployment (1.2, 1.3); speed-ups (8);
   merge branches (7.3).
