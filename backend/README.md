# USTore backend

Flask + SQLite JSON API. Phase 3 of the frontend build (`docs/PROMPT_3_BACKEND.md`): replaces
`UST Prototype Design`'s mock data layer (`src/services/dataService.js`) with real reads/writes
against the repo-root `ustore.db`.

## Run it

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Serves on `http://127.0.0.1:5000`. No seed step here — it reads the `ustore.db` the ETL pipeline
already built (see the repo root README for the rebuild command, run from the repo root:
`scripts/create_schema.py` → `scripts/populate_dim_date.py` → `scripts/step0`...`scripts/step3` →
`scripts/step5a` → `scripts/step5_prescriptive.py`).
If `ustore.db` doesn't exist yet, rebuild it first; the backend refuses to start against a missing
database rather than silently serving nothing.

The frontend's Vite dev server proxies `/api/*` to this port (see
`UST Prototype Design/vite.config.js`), so run both side by side:

```bash
# terminal 1
cd backend && python app.py
# terminal 2
cd "UST Prototype Design" && npm run dev
```

## What it does not do

- **No seeding.** `ustore.db` is the single source of truth; this app only reads it and, for the
  three tally/closure/event endpoints, writes new rows into it. It does not reload or reshape
  history from the raw CSVs.
- **No checkout, payment, customer total, or receipt.** This is an internal inventory counting
  tool only (BIR compliance — see `docs/PROMPT_1_FRONTEND.md` §1). If an endpoint starts to look like a
  point of sale, that's a bug.
- **No open routes.** Every `/api` route except `/api/auth/*` needs a signed-in session (`auth.py`).
  Accounts are in `App_User` (hashed passwords); the default `staff` / `staff123` account is created
  on the first login attempt. CORS and writes are limited to the dashboard's address (Vite's 5173 /
  4173); set `USTORE_ALLOWED_ORIGINS` (comma-separated) when it is served from elsewhere, e.g. over
  the store's network. The signed-in user is recorded in `Fact_Sales.entered_by` (app-entered rows
  only; pipeline rows leave it NULL), `Event_Log.created_by`, `Closure_Log.created_by` and
  `Inventory_Count.counted_by`.
- **PDF export is implemented.** `GET /api/reports/batch.pdf?month=YYYY-MM` renders the batch
  sales report with `fpdf2` (`batch_pdf.py`) — pure Python, no system libraries, which is why it
  is fpdf2 and not weasyprint or reportlab. `&inline=1` serves it for viewing instead of
  downloading. It shares `build_batch_report()` with the JSON endpoint, so the two cannot drift.

## Endpoints

See `UST Prototype Design/BACKEND_TODO.md` for the full contract this implements. Summary:

| | |
|---|---|
| Reads | `/api/meta`, `/api/products`, `/api/products/:id/history`, `/api/sales/monthly`, `/api/reports/batch`, `/api/fsn/sensitivity`, `/api/stock`, `/api/reorder`, `/api/calendar`, `/api/calendar/:date`, `/api/calendar/closed`, `/api/tally/recent`, `/api/tally?date=`, `/api/events`, `/api/forecast/:productId`, `/api/forecast/category/:category`, `/api/forecast/categories`, `/api/suppliers`, `/api/categories`, `/api/months` |
| Writes | `POST /api/tally`, `PUT /api/calendar/:date/closure`, `POST /api/events` |
| Auth (`auth.py`, open) | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/session` |
| Names and workbooks (`names.py`) | `GET /api/names/review`, `POST /api/names/review`, `POST /api/products/:id/rename`, `POST /api/tally/workbook` |
| Orders and restocks | `GET/POST /api/orders/upcoming`, `DELETE /api/orders/upcoming/:id`, `GET/POST /api/restocks`, `PUT/DELETE /api/restocks/:id` |

**Names and workbooks** keep the system running through sheet changes without a developer.
`GET /api/names/review` lists item names the vocabulary does not know yet: from the tally sheets
(`Name_Review`, written by step1, which loads each as a provisional item instead of stopping) and from
imported files (`Pending_Import_Row`: `/api/tally/import` and `/api/inventory/import` hold rows with an
unknown name rather than rejecting them, and recognise every sheet name the vocabulary already maps).
`POST /api/names/review` settles one (`same` + `product_id`, `new`, or `discard` for import-only names)
by appending one row to `data/vocab_mapping_FINAL_v5.csv` and applying any held rows.
`POST /api/products/:id/rename` adds the sheet's new name for an existing item the same way: the item
keeps its history and the name it is shown under. `POST /api/tally/workbook` saves the store's tally
workbook into `rawdata/` for step0 (a same-named one is moved to `rawdata/replaced/`, never overwritten).
Settled names show in `/api/pipeline/staleness` as `pending.name_decisions` until the next run.

**Orders and restocks.** A sale carries `order_type` (`walk_in`, `bulk`, `pre_order`;
`POST /api/tally` field and an import's optional "Order Type" column); the forecasts train on
walk-in sales only (`scripts/order_types.py`). `/api/orders/upcoming` holds bulk orders and
pre-orders the store knows are coming, which steps 4 and 4c add on their expected date.
`/api/restocks` records when each restock was ordered and delivered; from 3 deliveries a supplier's
median replaces its estimated lead time (`step5a_set_lead_times.py`).

**Daily use:** `python serve.py` serves this API and the built dashboard together with waitress and
backs up `ustore.db` daily (`backup.py`); see the root README, "Daily use at the store".

`/api/reorder` now returns real (provisional) ROP / Safety Stock / EOQ from `Result_Prescriptive`,
grouped per SKU with both ordering-cost scenarios (`low_admin_cost`, `high_goods_value`) nested —
`Dim_Parameters` and `Result_Prescriptive` were populated this session (see
`docs/STATUS_AND_NEXT_STEPS.md`). `/api/forecast/:productId` returns real data:
`step4_forecast_model.py` (a 50/50 blend of the item's category share and TSB, its 30-day total
spread over the days by the category's Prophet pattern; runs in about 10 seconds) has been run and
`Result_Forecast` holds 1,740 rows across 58 Fast SKUs. `model_type` says what wrote them
(`topdown_tsb+calendar+prophet_shape`).

`/api/forecast/category/:category` is served from `Result_Category_Forecast`
(`step4c_category_forecast.py`): the category's sales added up and forecast as one series, so it
covers every item in the category, with its own walk-forward accuracy in `data.metrics`. The Fast
items listed beside it (`contributors`) come from `Result_Forecast` and are a breakdown, not the
parts of the total, so the two do not add up (`contributors_total_30d` carries the items' sum).
`data.source` says which shape came back: `category_model`, or `sum_of_items` - the previous
behaviour, kept as the fallback for a database where step4c has not run.
`/api/forecast/categories` lists the categories that have a category forecast (it includes ones with
no Fast item, which the per-item forecasts cannot reach).

## Files

- `app.py` — Flask app, all routes.
- `db.py` — connection helper; points at `../ustore.db` and `../data/USTore_inventory_excel_long_mapped.csv`.
- `catalog.py` — per-product measured stats (ADUS, current stock, days of supply, FSN sensitivity),
  ported from `UST Prototype Design/scripts/generate_fixtures.py`'s logic and re-run live per
  request instead of dumped to a JSON fixture once.
- `validation.py` — server-side mirror of `dataService.js`'s `validateEntry()`.
