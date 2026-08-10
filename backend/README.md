# USTore backend

Flask + SQLite JSON API. Phase 3 of the frontend build (`PROMPT_3_BACKEND.md`): replaces
`UST Prototype Design`'s mock data layer (`src/services/dataService.js`) with real reads/writes
against the repo-root `ustore.db`.

## Run it

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Serves on `http://127.0.0.1:5000`. No seed step here — it reads the `ustore.db` the ETL pipeline
already built (see the repo root README for the rebuild command:
`create_schema.py` → `populate_dim_date.py` → `step0`...`step3` → `step5a` → `step5_prescriptive.py`).
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
  tool only (BIR compliance — see `PROMPT_1_FRONTEND.md` §1). If an endpoint starts to look like a
  point of sale, that's a bug.
- **No auth.** `Event_Log.created_by` is hardcoded `'local'`, matching the frontend's prior mock
  behaviour.
- **No PDF export.** The batch sales report returns JSON; server-rendered PDF export is a
  follow-up, not implemented here.

## Endpoints

See `UST Prototype Design/BACKEND_TODO.md` for the full contract this implements. Summary:

| | |
|---|---|
| Reads | `/api/meta`, `/api/products`, `/api/products/:id/history`, `/api/sales/monthly`, `/api/reports/batch`, `/api/fsn/sensitivity`, `/api/stock`, `/api/reorder`, `/api/calendar`, `/api/calendar/:date`, `/api/calendar/closed`, `/api/tally/recent`, `/api/tally?date=`, `/api/events`, `/api/forecast/:productId`, `/api/suppliers`, `/api/categories`, `/api/months` |
| Writes | `POST /api/tally`, `PUT /api/calendar/:date/closure`, `POST /api/events` |

`/api/reorder` now returns real (provisional) ROP / Safety Stock / EOQ from `Result_Prescriptive`,
grouped per SKU with both ordering-cost scenarios (`low_admin_cost`, `high_goods_value`) nested —
`Dim_Parameters` and `Result_Prescriptive` were populated this session (see
`STATUS_AND_NEXT_STEPS.md` at the repo root). `/api/forecast/:productId` still returns the pending
shape: Prophet (`step4_prophet_forecast.py`) needs `cmdstan` and has not been re-run, so
`Result_Forecast` does not exist.

## Files

- `app.py` — Flask app, all routes.
- `db.py` — connection helper; points at `../ustore.db` and `../USTore_inventory_excel_long_mapped.csv`.
- `catalog.py` — per-product measured stats (ADUS, current stock, days of supply, FSN sensitivity),
  ported from `UST Prototype Design/scripts/generate_fixtures.py`'s logic and re-run live per
  request instead of dumped to a JSON fixture once.
- `validation.py` — server-side mirror of `dataService.js`'s `validateEntry()`.
