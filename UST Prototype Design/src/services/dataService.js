/**
 * dataService — the ONLY module in this app that touches data.
 *
 * Every screen calls these functions; no screen imports a fixture, a JSON
 * file or the tally store directly. Phase 3: this file now calls the real
 * Flask + SQLite API (see ../../../backend) instead of resolving from the
 * in-memory fixtures - every function keeps the exact signature and return
 * shape it had in Phase 1/2, so no screen changes for this swap.
 *
 * Field names mirror the star schema in create_schema.py (product_id,
 * item_name, unit_price_php, calendar_date, quantity_sold), so the API
 * responses have the same shape the fixtures used to.
 *
 * BIR compliance (see PROMPT_1_FRONTEND.md §1): this is an internal
 * inventory counting service. It exposes unit counts, and unit prices as
 * reference data for supplier-remittance reporting. It must never gain a
 * cart, a checkout, a customer total, change due, or a receipt.
 */

const API_BASE = '/api';

async function request(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  // Validation failures come back as 400 with a { ok:false, errors } body,
  // which callers already handle - only a response with no JSON body at
  // all (network error, backend down) should throw.
  try {
    return await res.json();
  } catch {
    throw new Error(`${method} ${path} returned no JSON (status ${res.status})`);
  }
}

const get = path => request('GET', path);
const post = (path, body) => request('POST', path, body);
const put = (path, body) => request('PUT', path, body);

function qs(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') usp.set(k, v);
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

export const ALL_SUPPLIERS = 'All Suppliers';
export const ALL_CATEGORIES = 'All Categories';
export const UNATTRIBUTED = 'Unattributed';

/** TRANSACTION_TYPE values. SALE is a sale; the rest are non-sale removals.
 *  Stored on Fact_Sales.transaction_type. */
export const TRANSACTION_TYPES = ['SALE', 'DAMAGED', 'PROMO', 'TRANSFER'];

// ---------------------------------------------------------------- metadata

export const getMeta = () => get('/meta');

export const getSuppliers = () => get('/suppliers');

export const getCategories = () => get('/categories');

export const getMonths = () => get('/months');

// ---------------------------------------------------------------- catalog

export const getProducts = (filters = {}) =>
  get(`/products${qs({ supplier: filters.supplier, category: filters.category })}`);

/** Products that have at least one Fact_Sales row — the tally screen's
 *  item picker, so a user cannot tally against a name with no history. */
export const getSellableProducts = () => get('/products?has_history=1');

// ---------------------------------------------------------------- sales

export const getMonthlyUnits = (filters = {}) =>
  get(`/sales/monthly${qs({ supplier: filters.supplier, category: filters.category, dateRange: filters.dateRange })}`);

/** One product's observed monthly units — measured history, no fit. */
export const getProductHistory = productId => get(`/products/${productId}/history`);

/** Per-supplier unit counts and remittance line totals for one month.
 *  §3.2's batch sales report: internal reporting, not a customer document. */
export const getBatchReport = month => get(`/reports/batch${qs({ month })}`);

// ---------------------------------------------------------------- FSN

export const getFsnSensitivity = () => get('/fsn/sensitivity');

// ---------------------------- analytics the pipeline has produced (or not) --
// Do not "fill these in" client-side: forecasts come from
// step4_prophet_forecast.py and ROP/EOQ from Dim_Parameters +
// step5_prescriptive.py. The backend returns an explicit pending shape
// ({ available: false, reason, data: null }) rather than a number when the
// pipeline hasn't produced one.

export const getForecast = () => get('/forecast');
export const getForecastMetrics = () => get('/forecast');
export const getReorderAlerts = () => get('/reorder');

/** Stock position IS available for the items inventory covers — real
 *  counts and step2's days_of_supply. It carries no ROP, so it cannot say
 *  "reorder now"; it reports what is on hand and how long it lasts. */
export const getStockPosition = (filters = {}) =>
  get(`/stock${qs({ supplier: filters.supplier, category: filters.category })}`);

// ---------------------------------------------------------------- calendar

export const getCalendar = () => get('/calendar');

export const getDateFlags = isoDate => get(`/calendar/${isoDate}`);

// --------------------------------------------------------------------tally

export const getRecentEntries = (limit = 50) => get(`/tally/recent${qs({ limit })}`);

export const getEntriesByDate = isoDate => get(`/tally${qs({ date: isoDate })}`);

/** Client-side pre-check so obviously-bad input never leaves the browser.
 *  This is a convenience, not the guarantee - the server re-validates
 *  every one of these rules, plus rules the client can't check on its own
 *  (product/date existence). See backend/validation.py. Returns a
 *  field->message map; an empty object means valid. */
export function validateEntry({ product_id, quantity_sold, calendar_date, transaction_type }) {
  const errors = {};
  if (!product_id) errors.product_id = 'Select an item.';
  if (quantity_sold === '' || quantity_sold == null) {
    errors.quantity_sold = 'Enter a quantity.';
  } else {
    const n = Number(quantity_sold);
    if (!Number.isFinite(n)) errors.quantity_sold = 'Quantity must be a number.';
    else if (!Number.isInteger(n)) errors.quantity_sold = 'Quantity must be a whole number.';
    else if (n <= 0) errors.quantity_sold = 'Quantity must be greater than zero.';
  }
  if (!calendar_date) errors.calendar_date = 'Pick a date.';
  else if (!/^\d{4}-\d{2}-\d{2}$/.test(calendar_date)) errors.calendar_date = 'Date must be YYYY-MM-DD.';
  else if (calendar_date > new Date().toISOString().slice(0, 10)) errors.calendar_date = 'Date cannot be in the future.';
  if (!transaction_type) errors.transaction_type = 'Select a transaction type.';
  else if (!TRANSACTION_TYPES.includes(transaction_type)) errors.transaction_type = 'Unknown transaction type.';
  return errors;
}

export async function addEntry(entry) {
  const errors = validateEntry(entry);
  if (Object.keys(errors).length) return { ok: false, errors };
  return post('/tally', {
    product_id: Number(entry.product_id),
    quantity_sold: Number(entry.quantity_sold),
    calendar_date: entry.calendar_date,
    transaction_type: entry.transaction_type,
  });
}

export async function setStoreClosed(isoDate, closed) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) {
    return { ok: false, errors: { calendar_date: 'Date must be YYYY-MM-DD.' } };
  }
  return put(`/calendar/${isoDate}/closure`, { closed: !!closed });
}

export async function addEvent({ calendar_date, event_name, event_description }) {
  const errors = {};
  if (!calendar_date) errors.calendar_date = 'Pick a date.';
  if (!event_name?.trim()) errors.event_name = 'Give the event a label.';
  if (Object.keys(errors).length) return { ok: false, errors };
  return post('/events', {
    calendar_date,
    event_name: event_name.trim(),
    event_description: (event_description || '').trim(),
  });
}

export const getEventLog = () => get('/events');

export const getClosedDates = () => get('/calendar/closed');

/** No-op now that persistence is server-side; kept so anything still
 *  importing it (nothing in this app does) doesn't break. */
export const resetLocalState = () => {};
