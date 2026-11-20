# USTore System Quality Check Plan

A full bug and quality pass over the whole system, from the raw tally sheets to the screens staff
use: every pipeline step, every API endpoint, every screen and form, and the Power BI report.

- **Prepared:** 2026-11-20, against `main` at `c947278`.
- **How to use it:** work through the phases in order. Tick a box when the check passes. When it
  fails, log it in the [bug log](#10-bug-log) and write the bug ID next to the box instead of a tick.
- **Done when:** the [exit criteria](#11-exit-criteria) hold.

| Phase | What | Where it runs |
|---|---|---|
| [0](#0-ground-rules-and-setup) | Ground rules and setup | — |
| [1](#1-baseline-what-already-fails-today) | Baseline: what already fails today | — |
| [A](#3-phase-a--automated-gates) | Automated gates (tests, lint, build, data checks) | terminal |
| [B](#4-phase-b--data-pipeline) | Data pipeline, step by step | terminal + Tally Interface |
| [C](#5-phase-c--backend-api) | Backend API, endpoint by endpoint | curl / browser DevTools |
| [D](#6-phase-d--frontend-screen-by-screen) | Frontend, screen by screen | browser |
| [E](#7-phase-e--end-to-end-traces) | End-to-end traces: screen number = API number = database number | browser + SQL |
| [F](#8-phase-f--non-functional-quality) | Non-functional: UI quality, speed, security, operations, code | mixed |
| [G](#9-phase-g--power-bi-report) | Power BI report | Power BI Desktop |

---

## 0. Ground rules and setup

### Rules

1. **Test on a copy, never on the store's database.** Many checks below write data: tally entries,
   counts, closures, settled names (which append a row to `data/vocab_mapping_FINAL_v5.csv`), and
   pipeline runs (which rewrite `ustore.db` and `data/*.csv`, and re-encrypt `vault/`). The backend's
   database path is fixed to the repo root (`backend/db.py:33`), so the clean way to isolate is to
   copy the whole project folder and test in the copy.
2. **Don't change protected inputs to make something pass.** Per `CLAUDE.md`, the controlled
   vocabulary, allocation groups, supplier mapping, calendar ranges, and every expected value in a
   verification script stay as they are. A failing assertion gets logged. It never gets relaxed.
3. **Known failures are not new bugs.** [Section 1](#1-baseline-what-already-fails-today) lists what
   already fails. Log something only if it is new, or worse than the baseline.
4. **The BIR scope line.** This is an internal counting tool. Any screen, export, or endpoint that
   computes a customer total, change due, or receipt, or takes payment, is a bug no matter what else
   it gets right.
5. **Nothing from the QA copy goes back into the repo.** Don't commit or push from it. No `vault/`,
   `data/`, `ustore.db`, or `dist/` changes made during testing get copied back.

### Setup

```powershell
# From the folder that contains Capstone\
Copy-Item -Recurse "Capstone" "Capstone-QA"
```

Then, inside `Capstone-QA`:

```powershell
# Terminal 1 - backend (port 5000)
cd backend; python app.py

# Terminal 2 - frontend (port 5173, proxies /api to 5000)
cd "UST Prototype Design"; npm run dev
```

- Don't run the real project's backend at the same time. Both use port 5000.
- Default logins (if not yet changed): `staff` / `staff123`, `admin` / `admin123`.
- Tools you'll want: browser DevTools (Console and Network tabs), the `sqlite3` CLI (3.50.4 is
  installed) or DB Browser for SQLite, and `curl`.
- Note the starting point before Phase B: `python backend/backup.py` takes a copy of the QA
  database, so you can return to it with `--restore backups/<file>`.

---

## 1. Baseline: what already fails today

Measured 2026-11-20 on the main working copy (read-only checks only).

| Check | Command | Result today |
|---|---|---|
| Python tests | `python -m pytest tests -q` | **764 passed, 3 failed** (about 2.5 min) |
| Frontend lint | `cd "UST Prototype Design"; npm run lint` | **9 errors**: 8 × `react-hooks/set-state-in-effect`, 1 × `react-refresh/only-export-components` |
| Frontend build | `npm run build` | Builds. One warning: the main JS chunk is 512.8 kB (limit 500 kB) |
| CSV checks | `python scripts/verify_data.py` | **4 failures** |
| Database contract | `python tools/assert_invariants.py` | **7 of 21 pass** |
| Rebuild state | `python scripts/verify_rebuild_state.py` | **26 of 28 pass** |
| Commit data guard | `git config core.hooksPath` | **Unset** in this clone, so the pre-commit check that blocks plain store data is off (QA-008) |

What those failures are, sorted by what to do about them:

**Expected: the pinned values predate the new months.** Sales now run to **2026-10-18**. The pinned
figures were set on data that ended in July 2026 (`docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md` §7.1).
These all move together and need the group to approve new values. Log them once, as QA-007:

- `verify_data.py`: total units 99,240 vs pinned 89,232; per-month deltas now include 2026-08, 09, 10.
- `assert_invariants.py`: row counts, units, zero rows, product counts, F/S/N split, date span,
  `Dim_Parameters` 22 vs 0.
- `tests/test_degenerate_forecast.py`: two tests pin 84,399 / 68,541 rows and 266 eligible products.

**Known and documented. Don't re-log.**

- `verify_rebuild_state.py` `model_type` check: `docs/OPEN_ISSUES.md` #9.

**Needs triage. These are not in any doc yet** (pre-logged in the [bug log](#10-bug-log)):

| ID | Finding | Why it matters |
|---|---|---|
| QA-001 | Step 5's non-degeneracy gate fails on the real database: priced share **217 / 286 = 0.7587**, floor **0.78** (`tests/test_gates_can_fail.py::test_step5_gates_pass_on_the_real_database`). | This is one of the acceptance gates Chapter 4 relies on. It passed before the Aug–Oct data. |
| QA-002 | Supplier string `BAGS ALL YOU WANT` appears in the sales CSV but has no row in `data/supplier_mapping.csv`. | Its sales are unattributed in supplier views and the batch report. The mapping file is protected, so adding it is a group decision. |
| QA-003 | `is_hvl = 1` for **0** products (pinned 6). | Either the new data really removed every HVL item, or step 3's HVL flagging broke. Check step 3 against the data. |
| QA-004 | `Dim_Demand_Cluster` table is missing (`verify_rebuild_state.py` says to run `scripts/cluster_demand_profiles.py`). | Only that script and the verifier mention it. Confirm nothing in the app reads it before deciding whether it matters. |

---

## 2. Severity and how to log

| Severity | Meaning | Example |
|---|---|---|
| **S1 Critical** | A wrong number that staff or Chapter 4 would act on; lost or corrupted data; a security hole; a crash on a main path | ROP on screen ≠ `Result_Prescriptive`; a tally save that silently drops |
| **S2 Major** | A feature broken, with a workaround; a wrong number in a secondary place | CSV export total ≠ PDF total |
| **S3 Minor** | Cosmetic, confusing wording, layout breaks at one width | A long item name overflows its card |
| **S4 Note** | Code quality, lint, a speed improvement | A lint rule violation |

Every bug gets: steps to reproduce, expected result, actual result, and evidence (a screenshot, the
API response, or the SQL output). See the [bug log](#10-bug-log) template.

---

## 3. Phase A — Automated gates

Run these first, and again after every fix.

- [ ] `python -m pytest tests -q`: only the 3 baseline failures.
- [ ] `cd "UST Prototype Design"; npm run lint`: only the 9 baseline errors.
- [ ] `npm run build`: builds. Only the chunk-size warning.
- [ ] `python scripts/verify_data.py`: only the 4 baseline failures.
- [ ] `python tools/assert_invariants.py`: only the 14 baseline failures.
- [ ] `python scripts/verify_rebuild_state.py`: only the 2 baseline failures.
- [ ] `python tools/acceptance_standard.py`: record the result of each of its four conditions.
- [ ] `python scripts/vault.py status`: the vault is in sync with the plain files.
- [ ] `git config core.hooksPath` prints `.githooks` (the pre-commit data guard is on). Unset today (QA-008).
- [ ] Optional: `pip install pyflakes; python -m pyflakes backend forecasting scripts tools` for unused
  imports and undefined names.

**Coverage gaps to keep in mind during Phases C and D:**

- **There are no frontend tests.** Phase D is the only check on the screens.
- **17 of 75 API routes are not called by any test** (a text match on route paths, so the functions
  behind some of them may still be unit-tested):
  `GET /api/suppliers`, `/api/categories`, `/api/months`, `/api/products/:id/history`,
  `/api/reports/batch`, `/api/reports/summary/periods`, `/api/reports/summary`,
  `/api/reports/summary.pdf`, `PUT /api/reports/summary/goal`, `GET /api/fsn/sensitivity`,
  `/api/fsn/decision-rules`, `/api/stock`, `/api/calendar`, `/api/events/labels`, `/api/forecast`,
  `POST /api/pipeline/stop`, `GET /api/pipeline/status`. Give these extra attention in Phase C.

---

## 4. Phase B — Data pipeline

Run in the QA copy. The step order and timings come from `backend/pipeline.py` `STEPS`.

### B.1 Each step

| ✓ | Step | Script | Check |
|---|---|---|---|
| [ ] | create_schema | `scripts/create_schema.py` | Safe on an existing database. Every table exists, including `Pipeline_Run`, `Inventory_Count`, `Exception_Log`. (Known: it cannot repair an existing schema, `OPEN_ISSUES.md` #6.) |
| [ ] | populate_dim_date | `scripts/populate_dim_date.py` | `Dim_Date` starts 2023-01-01 and covers every sale date. `Event_Log` and `Closure_Log` entries are re-applied after the rebuild. `is_tally_date` is set. |
| [ ] | step0 | `scripts/step0_convert_sales_with_zeros.py` | Reads every workbook in `rawdata/`. Skips `import_*` archives and `~$` lock files. A month in two workbooks is read once, from the newest. Sparse months stay sparse, dense months are zero-filled. A re-run with no changed workbook finishes in seconds. |
| [ ] | daystatus | `scripts/rebuild_extract_tbs.py` | `Dim_Day_Status` is populated. No CLOSED day is marked demand-observable. |
| [ ] | step1 | `scripts/step1_apply_mapping.py` | An unknown raw name becomes a provisional item plus a `Name_Review` row, and the run keeps going. `product_id` stays the same per item across runs. The supplier mapping is applied. Without `rawdata/`, it fails with a clear message. |
| [ ] | allocation | `scripts/proportional_allocation.py` | Units in = units out (unit conservation). Zero-quantity rows survive. Given a raw (unmapped) CSV, it exits 1 and names the step to run. |
| [ ] | step2 | `scripts/step2_load_fact_sales.py` | `SUM(quantity_sold)` matches the allocated CSV. Unresolvable rows go to `Exception_Log`, not dropped. Entries typed in the app (`tally_date_flag = 0`) survive the reload. `is_censored` holds 1, 0, and NULL. |
| [ ] | step3 | `scripts/step3_fsn_classification.py` | No Fast item without a sale in the last 90 days. An item with no sales is Non-moving. HVL flagging works (see QA-003). |
| [ ] | step1b | `scripts/step1b_categorize_products.py` | After step 1 wiped them, categories are restored: no `APPAREL` / `NON-APPAREL` in `category`, and `category = forecast_category` everywhere. |
| [ ] | synthetic2023 | `scripts/load_synthetic_2023.py` | Writes to its own table. Nothing goes into `Fact_Sales`. |
| [ ] | step4 | `scripts/step4_forecast_model.py` | About 10 s. Every Fast item has a forecast. No negative `yhat`. Kill it mid-run: the previous forecasts are still there (it replaces them in one transaction). |
| [ ] | step4c | `scripts/step4c_category_forecast.py` | Every category has a forecast. If it is skipped, the forecast screen falls back to summing item forecasts. |
| [ ] | step4d | `scripts/step4d_forecast_comparison.py` | Writes one forecast per selectable model, for the model picker. |
| [ ] | step5a | `scripts/step5a_set_lead_times.py` | Every product has a `lead_time_days`. A supplier with 3+ recorded deliveries uses their median. |
| [ ] | step4b | `scripts/step4b_policy_forecast.py` | Writes `policy_rate` rows. Every eligible item is either priced or flagged `insufficient_data`. |
| [ ] | step5 | `scripts/step5_prescriptive.py` | Two scenarios per priced item. Exits with a message when step 4b's rows are missing. `--recompute-policy` gives an identical `Result_Prescriptive`. Gates: see QA-001. |

### B.2 Repeatability

- [ ] Run the full pipeline twice in a row with no data change. Every result table is identical
  across the two runs (compare `SELECT COUNT(*), SUM(...)` per table, or dump and diff).
- [ ] Run it with one new tally entry added in between. Only what that entry should move, moves.

### B.3 Running it from the app (Tally Interface → Update Analytics)

- [ ] **Run Pipeline (Without Forecast)** skips step 4 only, and finishes in about 45 s.
- [ ] **Run Full Pipeline + Forecast** runs everything, in about 55–75 s.
- [ ] The log tail updates live, line by line, not all at the end.
- [ ] **Stop** ends the whole process tree (check Task Manager for leftover `python.exe`). Afterwards,
  the staleness banner says the last run was stopped.
- [ ] Clicking Run again while a run is going is refused, with a message.
- [ ] A failed step shows which step failed and why (`StepProblem`). Later steps that need it don't run.
- [ ] After a run, other screens show the new numbers without a manual page reload.
- [ ] Each run adds one `Pipeline_Run` row with the right status.

### B.4 Failure injection (QA copy only)

- [ ] Rename `rawdata/` away: step 0 is skipped as optional. Step 1 fails with a clear message, and
  the run stops there.
- [ ] Hold a write lock on `ustore.db` (open a write transaction in DB Browser): the run reports
  "Close anything else using ustore.db", rather than hanging.
- [ ] Add a corrupt `.xlsx` to `rawdata/`: step 0 names the file. It does not crash without saying which.
- [ ] Add a workbook with a renamed item: it becomes a provisional item, plus a Names to review entry.
- [ ] Save a data CSV through Excel (dates become DD/MM): `verify_data.py` catches it.

---

## 5. Phase C — Backend API

### C.0 Checks that apply to every endpoint

Run these against every row in C.1, not only where they're mentioned.

- [ ] **No session:** returns 401 JSON. (Only `/api/auth/*` is open.)
- [ ] **Write from another origin:** refused. Also try a write with **no** `Origin` header (plain
  curl), and record what happens.
- [ ] **Changing or removing an existing record as `staff`:** returns `admin_required`. With a wrong
  `X-Admin-Password`: refused, and the attempt counts toward lockout. With the right one: succeeds.
  Signed in as `admin`: no prompt.
- [ ] **Bad input** (missing field, wrong type, negative number, bad date, unknown id): a 4xx with
  `{ok: false, errors}`. Never a 500, never an HTML traceback.
- [ ] **Quotes, `;`, `--`, `%`, and non-ASCII** in query strings and path parameters are treated as
  data.
- [ ] **Field names** match the star schema and what `dataService.js` reads.
- [ ] **Who did it is recorded:** `Fact_Sales.entered_by`, `Event_Log.created_by`,
  `Closure_Log.created_by`, `Inventory_Count.counted_by`.
- [ ] **After the whole phase:** the backend console has no Python tracebacks.

Starting point for curl (run from a bash shell; `jar` holds the session cookie):

```bash
O="Origin: http://localhost:5173"; J="Content-Type: application/json"; B=http://127.0.0.1:5000/api
curl -s -c jar -H "$O" -H "$J" -d '{"username":"staff","password":"staff123"}' $B/auth/login
curl -s -b jar $B/meta
curl -s -b jar -X DELETE -H "$O" $B/tally/1                    # expect admin_required
curl -s -b jar -X DELETE -H "$O" -H "X-Admin-Password: wrong" $B/tally/1
curl -s -b jar -X POST -H "Origin: http://evil.example" -H "$J" -d '{}' $B/events   # expect refused
```

### C.1 Endpoints

| ✓ | Endpoint | What to check |
|---|---|---|
| | **Auth** | |
| [ ] | `POST /auth/login` | Right and wrong passwords. An unknown username gets the same message, and about the same response time. 5 failures from one address → a 60 s lockout. On an empty `App_User`, the first attempt creates `staff` and `admin`. |
| [ ] | `POST /auth/logout` | The cookie is cleared. The next call returns 401. |
| [ ] | `GET /auth/session` | `authenticated` is true or false correctly. The session ends after 12 h. |
| | **Meta and lookups** | |
| [ ] | `GET /meta` | `available.*` flags turn false when the matching `Result_*` table is empty. `academic_calendar_end` is correct. |
| [ ] | `GET /suppliers` | `forecastable` and `period` parameters. QA-002's supplier shows up somewhere sensible (or as Unattributed). |
| [ ] | `GET /categories`, `GET /months` | Complete, with no blanks or duplicates. Months run up to the latest tallied month. |
| | **Products and names** | |
| [ ] | `GET /products` | Every filter, `has_history=1`, and a filter combination that matches nothing. |
| [ ] | `POST /products` | Adds an item. Rejects a blank name, a duplicate name, and an unknown category. |
| [ ] | `GET/POST /products/:id/price` | A new price with `effective_from`. Changing a listed price needs the admin. Rejects negative or zero prices. |
| [ ] | `POST /products/:id/rename` | Needs the admin. The item keeps its history. One vocabulary row is appended. |
| [ ] | `PUT /products/:id/status` | Needs the admin. A discontinued item drops out where it should, and keeps its history. |
| [ ] | `GET /products/:id/history`, `/movement` | Match `Fact_Sales` for that item. A nonexistent id returns 404. |
| [ ] | `GET/POST /names/review` | Lists names from `Name_Review` and from held import rows. `same` + `product_id`, `new`, and `discard` (import-only names) each work. Held rows get applied. |
| | **Sales and overview** | |
| [ ] | `GET /sales/monthly` | Filters work. Months with no sales show as 0, not missing. |
| [ ] | `GET /overview/performance` | `top`, `compare`, and `month` parameters. An invalid month returns 4xx. Comparing a month with itself is handled. |
| | **Reports** | |
| [ ] | `GET /reports/batch` | Day, week, and month periods, with and without a supplier. An empty period. An invalid month. |
| [ ] | `GET /reports/batch.pdf` / `.csv` / `.xlsx` | Each opens. `&inline=1` views instead of downloading. **Totals are identical across JSON, PDF, CSV, and XLSX.** No customer total or receipt wording. |
| [ ] | `GET /reports/summary/periods`, `/summary`, `/summary.pdf` | `kind=year|term|quarter` with a valid and an invalid `key`. The PDF matches the JSON. |
| [ ] | `PUT /reports/summary/goal` | Setting a goal works. Changing or removing one needs the admin. Rejects negative and non-numeric goals. |
| | **FSN** | |
| [ ] | `GET /fsn/sensitivity` | The 75 / 80 / 85 rows. The 80 row matches the current F/S/N counts. |
| [ ] | `GET /fsn/decision-rules?threshold=` | A valid threshold, a missing one, and nonsense. |
| | **Reorder** | |
| [ ] | `GET /reorder` | Both scenarios per item. The `summary` block. `exceeds_annual_demand` flags. `suggested_order_qty` = ROP + 30 days of demand − on hand, never negative. |
| [ ] | `GET /reorder/season` | Busy periods and the dates they reach ROP look right against the calendar. |
| [ ] | `GET /reorder/:id/whatif` | Each parameter on its own: `lead_time_days`, `stock_on_hand`, `scenario` (none / enrollment / term_start / bulk, with `scenario_days` or `bulk_units`), `ordering_cost_php`, `holding_cost_php`. Invalid values. **Writes nothing** (check the tables before and after). |
| [ ] | `GET /reorder/method` | Matches what step 5 does. |
| [ ] | `GET/PUT/DELETE /reorder/:id/settings` | Saving and resetting need the admin. A saved setting changes that item in `/reorder`. |
| | **Stock and inventory** | |
| [ ] | `GET /stock` | Coverage count. `stock_source` is `workbook` or `counted`. In the same month, a staff count beats the workbook. |
| [ ] | `GET /inventory?month=` | A valid month, a bad month, and a month with no counts. |
| [ ] | `POST /inventory` | 0 is allowed. Negative is rejected. A recount **replaces** the earlier figure. Correcting units on hand or changing a count needs the admin. |
| [ ] | `DELETE /inventory/:id` | Needs the admin. |
| [ ] | `POST /inventory/import` | A good file. Wrong columns. Unknown names are held for review. Importing the same file twice. |
| | **Restocks and upcoming orders** | |
| [ ] | `GET/POST /restocks`, `PUT/DELETE /restocks/:id` | Rejects delivered-before-ordered. Changing a delivery date or removing a restock needs the admin. |
| [ ] | `GET/POST /orders/upcoming`, `DELETE /orders/upcoming/:id` | Rejects quantity ≤ 0. Records the order type. Removing one needs the admin. |
| | **Calendar, closures, and events** | |
| [ ] | `GET /calendar`, `/calendar/closed`, `/calendar/:date` | An invalid date. A date outside `Dim_Date`. Closed days merge the calendar and the workbook colours. |
| [ ] | `PUT /calendar/:date/closure` | Closing works. Reopening needs the admin. A reason is saved. |
| [ ] | `PUT /calendar/closures/:date` | Editing a closure needs the admin. Moving it to a date that is already closed. |
| [ ] | `GET /events`, `/events/labels` | Labels for autocomplete come back without duplicates. |
| [ ] | `POST /events`, `PUT/DELETE /events/:id` | Rejects a blank name. Changing or removing needs the admin. |
| | **Tally** | |
| [ ] | `GET /tally/recent?limit=` | The default, a huge limit, 0, and a negative. |
| [ ] | `GET /tally?date=` | A valid date, an invalid one, and a date with no entries. |
| [ ] | `POST /tally` | Every rule in `backend/validation.py`: quantity 0, negative, decimal, huge; future dates; dates before 2023; a **date past the last `Dim_Date` row** (`calendar_extent` should extend it); a closed day; every `transaction_type` and `order_type`; entry price. |
| [ ] | `PUT/DELETE /tally/:id` | Needs the admin. Rejects a pipeline-loaded row, if that's the rule. A nonexistent id returns 404. |
| [ ] | `POST /tally/import` | CSV and XLSX. Wrong headers. Unknown names held. The optional Order Type column. An empty file. |
| [ ] | `POST /tally/workbook` | Only `.xlsx` is accepted. A same-named file moves to `rawdata/replaced/`, never overwritten. A filename containing `../` stays inside `rawdata/`. An upload over 256 MB returns the JSON 413 message. |
| | **Forecast** | |
| [ ] | `GET /forecast`, `/forecast/models` | The list of models matches what step 4d wrote. |
| [ ] | `GET /forecast/:id?model=` | The default model, each selectable model, an unknown model, and a non-Fast item. |
| [ ] | `GET /forecast/categories`, `/forecast/category/<name>` | Category names with `&`, spaces, and `/` (e.g. `Umbrellas & Gear`), URL-encoded. `data.source` is `category_model`, or `sum_of_items` when step 4c's table is empty. `contributors_total_30d` is present. |
| | **Advisories** | |
| [ ] | `GET /advisories` | Every advisory points to a real item and page. Counts per group match the modal. |
| | **Pipeline** | |
| [ ] | `POST /pipeline/run` | `includeForecast` true and false. A second call while running is refused. |
| [ ] | `POST /pipeline/stop`, `GET /pipeline/status` | Stopping when nothing is running. Status during, after, and after a backend restart. |
| [ ] | `GET /pipeline/staleness` | Each of the three warning cases, and silence otherwise. `pending.name_decisions` after settling a name. |

### C.2 Load and concurrency

- [ ] Save tally entries and counts **while a pipeline run is going**. No "database is locked" error
  reaches the user (the database uses WAL mode).
- [ ] Two browsers edit the same tally entry. The second save does not silently overwrite the first
  without anyone knowing.
- [ ] Stop the backend mid-run, then restart it. Status and staleness tell the truth afterwards.
- [ ] Rename `ustore.db` away and start the backend. It refuses to start, with a message.

---

## 6. Phase D — Frontend, screen by screen

### D.0 Every screen, every time

- [ ] **Loading:** something is shown while it loads, not a blank area.
- [ ] **Data:** every figure has a label and a unit (₱, units, days).
- [ ] **Empty:** a filter that matches nothing says so.
- [ ] **Pending:** when the pipeline hasn't produced an output, the `Pending` card shows. Test by
  emptying the `Result_*` table in the QA copy.
- [ ] **Backend down:** stop `app.py`. The "Connection problem" banner appears, not an endless
  spinner. It recovers when the backend comes back.
- [ ] **Console:** no errors, no React `key` warnings, no failed requests in the Network tab.
- [ ] **After a write:** other screens show the change without a manual reload.

### D.1 Login and the app shell

- [ ] Wrong password: a clear message. 5 wrong: the lockout message. After 60 s, it works again.
- [ ] Backend down at login: the submit says why, rather than spinning.
- [ ] The show/hide password toggle works.
- [ ] A page reload keeps you signed in. **Sign out** returns to login. The browser Back button after
  signing out shows no data.
- [ ] An expired session (clear the cookie, or restart the backend with a new secret) returns to
  login with a "session ended" notice. A request still in flight during Sign out does not show that
  notice.
- [ ] The sidebar opens every page. The active item is highlighted. The title matches the page.
- [ ] **Topbar filters**: each page shows only its own filters (Overview: sale months.
  Classification: date range, supplier, category. Forecast: supplier. Report: supplier. Reorder and
  Power BI: none). A supplier picked on Classification doesn't silently filter a page that has no
  supplier filter.
- [ ] Custom Range: a From month after the To month is handled. A page without Custom Range that
  inherits one shows "All Time".
- [ ] After login, moving between pages is quick (prefetch). The Network tab shows no repeated
  request storms.

### D.2 Dashboard Overview

- [ ] KPI tiles show labelled figures. Each matches its API field.
- [ ] **Sale Overview:** by default, it compares the month before with the latest tallied month.
  Changing either month updates it. Choosing the same month twice is handled.
- [ ] **Top Sellers:** the Filter button narrows by period, supplier, and category. Reset works. An
  empty result says so.
- [ ] "Among the items that make 80% of sales" agrees with the Pareto split.
- [ ] The Categories, Suppliers, and Products tables sort. Shares add up to about 100%.
- [ ] The stock statuses (Reorder now / Enough stock / Not counted / Too new to judge) add up to
  the total.
- [ ] "Show its sales history" opens the right item.
- [ ] Charts: tooltips on hover, labelled axes, zero months drawn as zero, no `NaN`.

### D.3 FSN Classification

- [ ] The F / S / N counts match the database (Phase E).
- [ ] Date range, supplier, category, and Custom Range filters, alone and combined.
- [ ] Every column sorts. Search works. Pagination works.
- [ ] The HVL badge ("Sells quickly, but on few recorded days"). Today no item has it (QA-003), so
  this can only be checked once that's resolved.
- [ ] The 75 / 80 / 85 sensitivity table. The FSN band modal. The decision rules.

### D.4 Demand Forecast

- [ ] The category list includes categories with no Fast item.
- [ ] The category chart shows history plus forecast. The forecast follows a weekday pattern, not a
  flat line. The source label is correct (category model vs sum of items).
- [ ] The contributing items are listed, with a note that they don't add up to the category total.
- [ ] "Show this item on its own" opens the item's forecast. A non-Fast item shows "No forecast has
  been generated for this SKU".
- [ ] The model picker switches model. The best-model marker is on the right one. The WAPE % error
  and "miss against a simple guess" tooltips read correctly.
- [ ] An item with no accuracy check shows "No accuracy check has been recorded".
- [ ] An item whose forecast is all zero (`OPEN_ISSUES.md` #1) is explained, not shown as a broken
  chart.
- [ ] Hovering a day shows its name. Event and closure days are marked.
- [ ] The supplier filter changes the item list.

### D.5 Reorder Alerts

- [ ] **Reorder Today card:** sorted most urgent first. Ten rows, then it scrolls with the header
  pinned. The supplier count is in the dark bar. The gold bar totals the Order column.
- [ ] Order quantity is order-up-to, not EOQ. Work out 3 items by hand: ROP + 30 × daily demand −
  on hand.
- [ ] **Recommendations table:** class and status filters. Every status appears where it should
  (Stockout, Reorder now, Approaching ROP, Stock is fine, No stock count). Both scenarios show. EOQ
  is greyed, with a tooltip, where it exceeds a year of demand.
- [ ] Every figure that rests on `Dim_Parameters` is marked **provisional**.
- [ ] "How It's Worked Out" is collapsed by default. Its formulas match step 5.
- [ ] **What-if:** each input changes the result. Reset works. Nothing is written to the database.
- [ ] **Reorder settings:** saving as `staff` asks for the admin password. Reset works. Saved values
  persist and change that item's row.
- [ ] **Plan Ahead — Busy Periods:** "Reaches ROP", "Extra for Rush", "Lowest in Rush", and "What To
  Do" make sense for 3 sampled items.

### D.6 Batch Sales Report and Summary Report

- [ ] The period picker (day / week / month) and the supplier filter.
- [ ] Per-supplier sections, subtotals, and the grand total. Items with no price are marked.
- [ ] **PDF** (download and open in a tab), **CSV**, and **Excel**: open each one. The totals equal
  the screen.
- [ ] A period with no sales (e.g. a closed day) says so.
- [ ] Nothing that looks like a customer total or receipt.
- [ ] The browser print preview is readable.
- [ ] **Summary Report:** year, term, and quarter. The period list. Setting a goal. Changing or
  removing a goal asks for the admin password. The PDF matches the screen.

### D.7 Analytics (Power BI)

- [ ] With no URL set: the placeholder names `.env.local`.
- [ ] With a pasted `<iframe>` tag as the URL: the "paste only the URL" message.
- [ ] With a valid URL: the report renders.

### D.8 Tally Interface

General:

- [ ] The open tab is remembered across a reload. The tab badges show names waiting and
  staleness.
- [ ] Every admin-confirm dialog: a wrong password shows a message. Cancel leaves the record
  unchanged. Signed in as `admin`, there's no prompt.

**Sales Tally tab**

- [ ] **Entry form:** pick a category, then an item. Quantity, date, transaction type (SALE,
  DAMAGED, PROMO, TRANSFER), order type (walk-in, bulk, pre-order), entry price.
- [ ] **Validation:** quantity 0, negative, decimal, huge; a future date; a date before 2023; a
  closed day; no item chosen. **The screen's message matches the server's message** for the same
  input (send the same payload with curl to bypass the screen).
- [ ] Saving shows the saved dialog. The entry appears in recent entries.
- [ ] **Import CSV / Excel:** a good file, wrong columns, unknown names (held for review), the same
  file twice, a non-spreadsheet file.
- [ ] **Upcoming orders:** add, list, remove (admin).
- [ ] **Entries by date:** pick a date. Edit an entry (admin). Delete an entry (admin).
- [ ] **Names to review:** shows the evidence. Settle as same, new, and discard. Rename an item.
- [ ] **Add item**, **item price** (changing a price asks for the admin), and **discontinue**.

**Inventory tab**

- [ ] **Monthly count:** month, item, quantity. 0 is allowed. A recount replaces the earlier figure,
  and says so. Correcting on-hand and deleting a count need the admin. Import counts from a file.
- [ ] **Current stock:** search, category and supplier filters, the "stock only" toggle, editing.
- [ ] **Restock deliveries:** add one. Mark it delivered. Delivered-before-ordered is rejected.
  Delete (admin).

**Calendar tab**

- [ ] Close a day, with a reason. Reopen it (admin). Edit a closure (admin). Check the rules for past
  vs future days.
- [ ] Add an event. Label autocomplete. Edit and delete (admin).
- [ ] The edit-calendar modal.
- [ ] The calendar-end banner shows, and dismissing it is remembered.

**Update Analytics tab**

- [ ] Covered by [B.3](#b3-running-it-from-the-app-tally-interface--update-analytics), plus: **Add
  tally workbook** accepts `.xlsx` only and shows the result.
- [ ] The staleness banner: each of its three warning cases, and silence when up to date.

### D.9 Advisories

- [ ] Grouped as Act now / Plan for / For info. The counts match the button.
- [ ] Each jump link goes to the right page and the right spot on it.

---

## 7. Phase E — End-to-end traces

A number on a screen has to equal the API's number, which has to equal the database's number. For
each trace, write all three in the bug log's evidence column if they differ.

**Read the route in `backend/app.py` before writing the SQL.** The queries below are starting points.
Add whatever filters the route applies (`transaction_type`, `order_type`, `is_active`, date window),
or you'll log false bugs.

| ✓ | Screen figure | API | Database |
|---|---|---|---|
| [ ] | Units sold in a month (Overview) | `/overview/performance` | `SELECT SUM(f.quantity_sold) FROM Fact_Sales f JOIN Dim_Date d USING(date_id) WHERE substr(d.calendar_date,1,7)='2026-10';` |
| [ ] | F / S / N counts (Classification) | `/products` | `SELECT fsn_class, COUNT(*) FROM Dim_Product GROUP BY 1;` |
| [ ] | One item's ROP, safety stock, EOQ (Reorder) | `/reorder` | `SELECT ordering_cost_scenario, reorder_point, safety_stock, eoq FROM Result_Prescriptive WHERE product_id=?;` |
| [ ] | One item's 30-day forecast (Forecast) | `/forecast/:id` | `SELECT model_type, SUM(yhat) FROM Result_Forecast WHERE product_id=? GROUP BY 1;` |
| [ ] | One category's 30-day forecast | `/forecast/category/:name` | `SELECT SUM(yhat) FROM Result_Category_Forecast WHERE forecast_category=?;` |
| [ ] | One item's on-hand (Reorder) | `/stock` | `SELECT * FROM Inventory_Count WHERE product_id=? ORDER BY count_month DESC LIMIT 1;` plus the workbook CSV |
| [ ] | Batch report month total | `/reports/batch` | The sum of quantity × price per supplier for that month, and the same in the PDF, CSV, XLSX |
| [ ] | Staleness banner | `/pipeline/staleness` | `SELECT max_sale_id FROM Pipeline_Run ORDER BY run_id DESC LIMIT 1;` vs `SELECT MAX(sale_id) FROM Fact_Sales;` |
| [ ] | Power BI totals | — | Units, F/S/N counts, and one ROP equal the app's |

### E.2 Write-through scenarios

Do each one start to finish in the QA copy.

- [ ] **A sale:** enter it → it shows in recent entries and by date → the staleness banner appears →
  run the pipeline → the item's ADUS (and maybe its class) updates → Reorder updates. Check
  `entered_by`, `tally_date_flag = 0`, `order_type`, and `transaction_type` in `Fact_Sales`.
- [ ] **A non-sale** (DAMAGED or PROMO): it is recorded, but not counted as demand in ADUS or the
  forecast.
- [ ] **A bulk order:** recorded as bulk. The forecast trains on walk-in sales only.
- [ ] **A closure:** it shows on the calendar → after a run, the forecast treats that day as closed.
- [ ] **An inventory count:** Reorder's on-hand changes, and `stock_source` becomes `counted`.
- [ ] **Renaming an item:** its history and forecast stay attached to it.
- [ ] **Settling a name:** one vocabulary row is appended → the next run reads the name correctly
  → held import rows are applied.
- [ ] **An upcoming order:** steps 4 and 4c add it on its expected date.
- [ ] **Three restocks for one supplier:** after a run, step 5a uses their median lead time.

---

## 8. Phase F — Non-functional quality

### F.1 UI quality

- [ ] Widths 1280, 1366, 1920, and a narrow window. Zoom 125% and 150%. Nothing overlaps or gets cut off.
- [ ] The longest item name in the catalogue, in every table, card, modal, and PDF.
- [ ] Keyboard: a sensible Tab order, Esc closes modals, Enter submits forms, focus is visible.
- [ ] Contrast: the gold accent and grey text are readable on their backgrounds.
- [ ] Every abbreviation (ADUS, ROP, EOQ, FSN, HVL, WAPE) has an info tip the first time it appears on
  a page.
- [ ] Consistent formatting: ₱ with thousand separators, the same date format everywhere, units
  never shown as decimals unless they're a rate.
- [ ] Consistent wording for the same thing across screens (e.g. "Reorder now" vs "Order now").

### F.2 Speed

- [ ] Time each page's first load, and a revisit. Note anything over 2 s.
- [ ] Time the heaviest endpoints in DevTools: `/products`, `/reorder`, `/advisories`,
  `/overview/performance`, `/stock` (`catalog.py` recomputes per request).
- [ ] The JS bundle is 512.8 kB. Note it (QA-006). Splitting by page is the usual fix.
- [ ] Pipeline timings against the README's (about 45 s / 55–75 s).

### F.3 Security

- [ ] The default passwords (`staff123`, `admin123`) are changed before the app goes on the store's
  network.
- [ ] `backend/.secret_key` and `backend/.env` are gitignored and not in git history.
- [ ] The session cookie is `HttpOnly` and `SameSite=Lax` (DevTools → Application → Cookies).
- [ ] The login lockout and the admin-password lockout each work, separately.
- [ ] Uploads: a non-spreadsheet renamed to `.xlsx`, a huge file, a filename with `../`.
- [ ] `python serve.py --host 0.0.0.0` from another computer: the dashboard works when that address
  is in `USTORE_ALLOWED_ORIGINS`, and writes are refused when it isn't.
- [ ] Still open from `README.md` and the docs: the repo is public with client data (B1), and a
  Power BI "Publish to web" link is public. Confirm their current status. Don't re-log them as new.

### F.4 Reliability and operations

- [ ] `python serve.py` serves the built dashboard and the API at `http://localhost:8080`.
- [ ] Its daily backup appears in `backups/`. Only the last 14 are kept.
- [ ] `python backend/backup.py --list` and `--restore backups/<file>`. A restore first saves a
  `before-restore` copy.
- [ ] `python scripts/vault.py lock` / `unlock` / `status`. When both sides changed, it stops and asks
  instead of overwriting.
- [ ] The pre-commit hook refuses a commit that stages a plain data file.

### F.5 Code quality

- [ ] The 9 lint errors (QA-005). Most are `setState` inside a `useEffect` that resets a form when a
  modal opens.
- [ ] The client-side check (`validateEntry` in `dataService.js`) and the server-side one
  (`backend/validation.py`) give the same verdict on the same inputs. Use the D.8 validation cases.
- [ ] No screen calls `fetch` directly. Everything goes through `dataService.js`:
  `grep -rnE "(^|[^A-Za-z])fetch\(" "UST Prototype Design/src" --include=*.jsx` returns nothing
  (it returned nothing on 2026-11-20).
- [ ] Note for maintainability (not a bug): `backend/app.py` is 4,442 lines and
  `TallyInterface.jsx` is 3,109.

---

## 9. Phase G — Power BI report

The report lives in `UST Prototype Design/USTORE_DB.Report` and its model in
`USTORE_DB.SemanticModel`.

- [ ] Open `USTORE_DB.pbip`. The data source in `expressions.tmdl` points at the QA copy's
  `ustore.db`. Refresh works with no errors.
- [ ] The relationships in `relationships.tmdl` are all active and point the right way.
- [ ] Each of the 3 pages renders with no broken visuals.
- [ ] Totals match the app (the Power BI row in [Phase E](#7-phase-e--end-to-end-traces)).
- [ ] Slicers filter every visual on their page.
- [ ] The model reads only the 7 tables it lists. Note any screen figure the report shows that comes
  from a table it doesn't load (e.g. category forecasts).
- [ ] `.pbi/unappliedChanges.json` is untracked in git. Decide whether those changes should be
  applied or discarded.

---

## 10. Bug log

Copy this table into an issue tracker, or keep it here. One row per bug.

| ID | Sev | Area | Where | Steps to reproduce | Expected | Actual | Evidence | Status | Owner |
|---|---|---|---|---|---|---|---|---|---|
| QA-001 | S1 | Pipeline | step 5 gate | `python -m pytest tests/test_gates_can_fail.py` | priced share ≥ 0.78 | 217/286 = 0.7587 | pytest output | Triage | |
| QA-002 | S2 | Data | `data/supplier_mapping.csv` | `python scripts/verify_data.py` | every supplier string mapped | `BAGS ALL YOU WANT` unmapped | verify_data output | Needs group decision | |
| QA-003 | S2 | Pipeline | step 3 HVL | `SELECT COUNT(*) FROM Dim_Product WHERE is_hvl=1;` | some HVL items (pinned 6) | 0 | SQL output | Triage | |
| QA-004 | S3 | Pipeline | `Dim_Demand_Cluster` | `python scripts/verify_rebuild_state.py` | table exists | missing | verifier output | Triage | |
| QA-005 | S4 | Frontend | ESLint | `npm run lint` | 0 errors | 9 errors | lint output | Open | |
| QA-006 | S4 | Frontend | bundle size | `npm run build` | under 500 kB | 512.8 kB | build output | Open | |
| QA-007 | S2 | Tests | pinned values | Phase A | pins match the current data | sales now run to 2026-10-18 | §1 | Needs group decision | |
| QA-008 | S3 | Repo | pre-commit hook | `git config core.hooksPath` | `.githooks` | unset | command output | Open (fix: `git config core.hooksPath .githooks` in each clone) | |

**Statuses:** Triage → Open → Fixed → Verified. Or: Needs group decision / Won't fix (with the reason).

For any bug that touches a protected file (vocabulary, allocation groups, supplier mapping, calendar
ranges) or a pinned expected value, the status is **Needs group decision**. Nobody edits it as part
of the fix.

---

## 11. Exit criteria

The pass is done when all of these hold:

1. Every box is ticked, or has a bug ID next to it.
2. Every Phase A gate either passes or fails only on items already in the bug log.
3. There are no open S1 bugs.
4. Every S2 bug is fixed, or has an owner and a written decision.
5. Every Phase E trace matches across screen, API, and database.
6. Every fix has been re-checked in the QA copy, and Phase A has been re-run after the last fix.

## 12. Suggested order

| Day | Work |
|---|---|
| 1 | Setup, Phase A, triage QA-001 to QA-004, Phase B |
| 2 | Phase C |
| 3–4 | Phase D (split the screens between people: Tally Interface alone is a day) |
| 5 | Phases E, F, G |
| 6 | Re-check fixes, re-run Phase A, close out the log |
