# Claude Code Prompt 3/3 — USTore Backend (Flask + SQLite)

> **Sequence:** this is **Phase 3 of 3**. Do `PROMPT_1_FRONTEND.md` and `PROMPT_2_POWERBI.md` first.
> **Depends on:** the frontend's single mock data layer + the `BACKEND_TODO.md` that Phase 1 produced.
> Edit the **bracketed paths** before pasting.

---

You are working on **USTore**, a Business Analytics capstone. The frontend runs on a mock data layer
(Phase 1) and embeds Power BI (Phase 2). This pass builds the **Python Flask + SQLite backend** and
**replaces the frontend's mock data layer with a real API** — without changing the UI.

## 0. Before writing any code — orient yourself

1. Read `PROJECT_CONTEXT.md` (esp. **§6 Star Schema**) and **`BACKEND_TODO.md`** (the exact contract
   Phase 1 left for you). `PROJECT_CONTEXT.md` is the source of truth.
2. Read the frontend's **single data-access module** at `[PATH TO THE FRONTEND DATA MODULE, e.g. src/api.js]`
   and every `// TODO: backend` marker. Your API must satisfy exactly what that module needs — same
   shapes, same field names.
3. Look at the seed data: `USTore_sales_long_allocated.csv`, `calendar_ranges_2023_2026.csv`,
   `USTore_inventory_excel_long.csv`.
4. **Report your plan — endpoints, schema, seed strategy, and how you'll swap the mock layer — before
   building.**

## 1. The one constraint you must never violate (BIR compliance)

The tallying tool is an **INTERNAL INVENTORY COUNTING TOOL ONLY.** No payment processing, no checkout, no
customer-facing totals, no receipts. It records **unit counts**. Unit prices are reference data for
internal supplier-remittance only. No cart/checkout/receipt endpoints. If a feature feels like a POS,
stop.

## 2. Stack

- **Python Flask + SQLite.** Expose a clean **JSON API**. SQLAlchemy or plain `sqlite3` — keep it
  simple and dependency-light. Provide `requirements.txt` and a `README` with run + seed instructions.
- Low-infrastructure, runnable locally with one command. Handle CORS if the frontend runs on a separate
  dev-server port.

## 3. Data model — SQLite Star Schema (full field lists in `PROJECT_CONTEXT.md` §6)

- **`Fact_Sales`** — `sale_id` PK, `product_id` FK, `date_id` FK, `quantity_sold`,
  `cumulative_monthly_units`, `daily_depletion_rate`, `imputation_flag`, `tally_date_flag`,
  `transaction_type`.
- **`Dim_Product`** — `product_id` PK, `item_name`, `category`, `unit_price_php`, `supplier_name`,
  `lead_time_days`, `fsn_class`, `entry_date`, `is_active`.
- **`Dim_Date`** — `date_id` PK, `calendar_date` (ISO 8601), `semester_id`, `semester_week`,
  `is_enrollment_period`, `is_exam_week`, `is_event_day`, `is_sem_break`, `is_tally_date`,
  `is_store_closed`.
- **`Dim_Parameters`** — `parameter_id`, `parameter_name`, `value`, `unit`, `last_updated`.
- **`Event_Log`** — `event_id` PK, `event_date`, `event_name`, `event_description`, `created_by`,
  `date_logged` (operational feed into `Dim_Date.is_event_day`; does not join to Fact_Sales).

**Do NOT create `Dim_Inventory`** — stock on hand is derived
(`beginning_monthly_stock − cumulative_monthly_units`); load beginning stock as a reference lookup.

**Seed script (idempotent, prints row counts):**
- `Dim_Date` from `calendar_ranges_2023_2026.csv` — expand each `start_date…end_date` range into one row
  per calendar date and set the matching flag(s); a date may carry multiple flags.
- `Fact_Sales` + `Dim_Product` from `USTore_sales_long_allocated.csv` (carry `imputation_flag`; set
  `tally_date_flag = TRUE` for these historical rows; default `transaction_type = 'SALE'`).
- Beginning-stock lookup from `USTore_inventory_excel_long.csv`.

## 4. Endpoints (must match what the frontend mock layer exposes — reconcile with `BACKEND_TODO.md`)

At minimum:
- **Tallying:** create a sales entry (item, quantity, date, supplier, `transaction_type`) → `Fact_Sales`;
  list recent entries; entries by date. **Server-side validation** mirroring the frontend: reject null
  item/quantity/date/transaction_type; quantity positive integer; return clear error payloads.
- **Closure toggle:** set/clear `is_store_closed` on a `Dim_Date` row.
- **Event flagging:** insert `Event_Log` + set `is_event_day` on the matching `Dim_Date` row(s).
- **Stock status:** per product, on-hand = beginning monthly stock − cumulative units sold that month,
  with color-coded threshold state — computed from seeded data.
- **Calendar context:** upcoming `Dim_Date` flags for a window.
- **FSN / forecast reads:** return `fsn_class` (currently NULL → clean "not classified" response) and a
  forecast stub ("pending"). **Do not fabricate analytics values.**

## 5. Swap the frontend from mock → real API

- Change **only the single data-access module** to call these endpoints; **do not touch UI components**
  or restyle anything. If the swap needs shape changes, fix them in that module (or the API) so screens
  stay untouched.
- Replace the temporary client-side/localStorage persistence with real API calls; remove the
  `// TODO: backend` markers as each is satisfied.
- Verify each screen still works against the live backend exactly as it did against mocks.

## 6. Guardrails

- **Do not break the frontend or the Power BI embed.** Leave the ETL/analytics scripts
  (`ustore_tbs_to_csv.py`, `proportional_allocation.py`, etc.) untouched.
- **Never fabricate analytics data** to populate a view — "pending"/empty states are correct where
  FSN/forecast haven't run.
- Keep the batch/remittance report internal; never a customer receipt.
- Work in small, verifiable steps: (1) schema + seed, verify row counts; (2) tallying create/read +
  validation, verify a POST persists and reads back; (3) stock-status from seeded data; (4) closure +
  event; (5) swap the frontend data module and re-verify every screen. Ask before any large refactor.

## 7. Definition of done

- `pip install -r requirements.txt` then one command runs the API; one documented command seeds the DB
  from the three CSVs, printing row counts.
- **Tallying:** add a sale (with transaction_type) → persisted + listed; bad input → validation errors;
  closure toggle and event flag reflected in `Dim_Date`/`Event_Log`.
- **Stock status** renders real color-coded on-hand numbers; FSN/forecast return clean pending states.
- The frontend runs unchanged in appearance but now reads/writes the real backend; Power BI embed still
  works; no `// TODO: backend` markers remain.
- No checkout, payment, customer total, or receipt exists anywhere.
- You've reported the schema, endpoints, and what changed in the frontend data module.

Start by reading `PROJECT_CONTEXT.md` §6, `BACKEND_TODO.md`, and the frontend data module, then give me
your plan before building.
