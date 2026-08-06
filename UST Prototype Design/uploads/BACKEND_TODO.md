# BACKEND_TODO — what Phase 3 has to build

Phase 1 wired this frontend to a mock data layer. Every screen reads through
`src/services/dataService.js`; **no screen touches data directly**. Phase 3
replaces the body of each function in that one file with an HTTP call. If you
find yourself editing a page component to get the backend in, something has
gone wrong — the seam is in the service.

Three things are throwaway and must be replaced, not extended:

1. `src/services/fixtures/*.json` — generated snapshots, not a database.
2. `src/services/tallyStore.js` — localStorage. Per-browser, unauthenticated,
   trivially editable. **Nothing written through it is a record of anything.**
3. The `LATENCY_MS` shim in `dataService.js` — a fake delay so loading states
   are exercised. Delete it when real requests provide the latency.

---

## 1. Endpoints to implement

Shapes below are what `dataService` already consumes; keeping them means the
frontend needs no changes beyond swapping the function bodies. Field names
match the star schema in `create_schema.py`.

### Reads

| Service function | Suggested endpoint | Returns |
|---|---|---|
| `getMeta()` | `GET /api/meta` | row counts, `sales_span`, and the `available` / `pending_reason` blocks (see §3) |
| `getProducts(filters)` | `GET /api/products?supplier=&category=&dateRange=` | `Dim_Product` joined to measured stats — `product_id, item_name, category, unit_price_php, supplier_name, payment_status, fsn_class, is_hvl, entry_date, is_active, total_units, adus, avg_monthly, cv, active_tally_dates, censored_days, current_stock, stock_as_of, days_of_supply, revenue` |
| `getSellableProducts()` | `GET /api/products?has_history=1` | same shape, products with a `Fact_Sales` row, sorted by name |
| `getProductHistory(id)` | `GET /api/products/:id/history` | `[{ month: 'YYYY-MM', units, tally_days }]` |
| `getMonthlyUnits(filters)` | `GET /api/sales/monthly?…` | `[{ month, units, revenue, priced_units }]` |
| `getBatchReport(month)` | `GET /api/reports/batch?month=YYYY-MM` | `[{ supplier, items: [{ item_name, quantity, unit_price_php, line_total }], subtotal, unpriced_units }]` |
| `getFsnSensitivity()` | `GET /api/fsn/sensitivity` | `{ p75: {F,S,N,cutoff}, p80: {…}, p85: {…} }` |
| `getStockPosition(filters)` | `GET /api/stock` | `{ items: [...products with current_stock], covered, total }` |
| `getCalendar()` | `GET /api/calendar` | `Dim_Date` rows |
| `getDateFlags(date)` | `GET /api/calendar/:date` | one `Dim_Date` row incl. `is_store_closed`, `is_event_day` |
| `getRecentEntries(limit)` | `GET /api/tally/recent?limit=` | `Fact_Sales` rows joined to product name + supplier |
| `getEntriesByDate(date)` | `GET /api/tally?date=YYYY-MM-DD` | same shape, one date |
| `getEventLog()` | `GET /api/events` | `Event_Log` rows |
| `getClosedDates()` | `GET /api/calendar/closed` | `['YYYY-MM-DD', …]` |
| `getForecast()` / `getForecastMetrics()` | `GET /api/forecast/:productId` | `Result_Forecast` / `Result_Forecast_Metrics`, or the pending shape in §3 |
| `getReorderAlerts()` | `GET /api/reorder` | ROP/EOQ/safety stock per SKU, or the pending shape |

### Writes — the three the tally screen performs

| Service function | Suggested endpoint | Effect |
|---|---|---|
| `addEntry(entry)` | `POST /api/tally` | insert a `Fact_Sales` row: `product_id`, `date_id` (resolved from the ISO date), `quantity_sold`, `transaction_type`, `imputation_flag = 0`, `tally_date_flag = 0` (this is a live entry, not a historical tally) |
| `setStoreClosed(date, closed)` | `PUT /api/calendar/:date/closure` | set `Dim_Date.is_store_closed` |
| `addEvent({…})` | `POST /api/events` | insert `Event_Log` **and** set `Dim_Date.is_event_day = 1` for that date |

All three currently return `{ ok: true, … }` or `{ ok: false, errors: {field: message} }`.
Keep that contract — the forms render `errors` inline by field name.

## 2. Validation to mirror server-side

`validateEntry()` in `dataService.js` is a **convenience, not a guarantee**.
Re-implement every rule on the server and reject on failure:

- `product_id` required, must exist in `Dim_Product`
- `quantity_sold` required, a **positive integer** (reject 0, negatives,
  decimals, non-numerics)
- `calendar_date` required, ISO `YYYY-MM-DD`, must exist in `Dim_Date`, not in
  the future
- `transaction_type` required, one of `SALE | DAMAGED | PROMO | TRANSFER`

Two schema notes:

- `Fact_Sales.transaction_type` is currently a free `TEXT` column defaulting to
  `'sale'`, and historical rows use lowercase. Either add a
  `CHECK (transaction_type IN (...))` and migrate the existing rows, or
  normalise case on read. The frontend uppercases for display.
- The tally screen's four types come from `PROMPT_1_FRONTEND.md §4`. The
  original design had `Return` instead of `PROMO`. If the store actually needs
  to record returns, that is a fifth value and a decision for the team — it was
  not silently kept.

## 3. Do not fabricate what the pipeline has not produced

`meta.json` carries an `available` block and a `pending_reason` block; the UI
renders a "pending" card whenever a capability is false. **Keep serving this
shape.** Today:

```json
"available": { "sales": true, "fsn": true, "batch_report": true,
               "forecast": false, "reorder": false }
```

- `forecast: false` — `step4_prophet_forecast.py` needs `cmdstan` and has not
  been re-run; `Result_Forecast` does not exist in the database.
- `reorder: false` — `Dim_Parameters` is empty. No lead times, ordering cost or
  holding cost have been collected (Block 5, the USTore site visit).

When those run, flip the flags and the screens light up on their own. The
previous version of this frontend synthesised a Prophet line with `Math.sin()`
and hardcoded MAPE per FSN class (12.5 / 16.8 / 22.4%), which contradicted the
project's own benchmark finding that no SKU reaches MAPE ≤ 20%. Don't reintroduce that.

## 4. Everything else

- **Auth.** There is none. The tally screen writes with no identity attached;
  `Event_Log.created_by` is hardcoded `'local'`.
- **Concurrency.** Two staff tallying at once currently cannot conflict because
  nothing is shared. With a real backend, decide whether a duplicate
  (product, date) entry is a correction or a second observation.
- **The fixture generator** (`scripts/generate_fixtures.py`) can be retired once
  the API exists, or kept for offline demos.
- **PDF export** on the Batch Sales Report is disabled in the UI and marked
  `TODO: backend` — it should be server-rendered.
- **BIR constraint carries over.** The API must not gain a checkout, a payment
  field, a customer total or a receipt endpoint. Unit prices are reference data
  for internal supplier remittance only.
