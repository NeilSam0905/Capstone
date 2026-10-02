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
 * BIR compliance (see docs/PROMPT_1_FRONTEND.md §1): this is an internal
 * inventory counting service. It exposes unit counts, and unit prices as
 * reference data for supplier-remittance reporting. It must never gain a
 * cart, a checkout, a customer total, change due, or a receipt.
 */

const API_BASE = '/api';

/* ------------------------------------------------------------- GET cache

   Every screen refetches on mount, so moving Overview -> Reorder -> Overview
   used to re-run the same queries three times. Two things fix that here:

   - **Cache**, keyed on the full path (so `?supplier=X` is a different entry
     from `?supplier=Y`). A repeat GET inside the TTL resolves from memory and
     never touches the network.
   - **In-flight de-duplication**, keyed the same way. Two components mounting
     together and asking for the same path share ONE request rather than
     racing. Overview alone used to fire six.

   Correctness rule: any mutation clears everything. A single tally entry
   changes products, monthly units, stock, reorder and advisories at once, so
   per-endpoint invalidation would be a list to keep in sync and get wrong.
   Clearing is cheap - the next read just refetches.

   `cacheVersion` lets useData tell whether a snapshot it kept is still from
   the current generation, without importing anything back from the hook. */
const CACHE_TTL_MS = 60_000;
const cache = new Map();      // path -> { at, data }
const inflight = new Map();   // path -> Promise

let cacheVersion = 0;
export const getCacheVersion = () => cacheVersion;

const clearListeners = new Set();

/** Drop everything. Called after every write, and available to callers that
 *  know they have invalidated server state some other way. */
export function clearApiCache() {
  cache.clear();
  inflight.clear();
  cacheVersion += 1;
  clearListeners.forEach(fn => fn());
}

/** Run `fn` after every cache clear (i.e. after every write). The prefetcher
 *  uses it to re-warm the pages the write just made stale. Returns an
 *  unsubscribe function. */
export function onCacheCleared(fn) {
  clearListeners.add(fn);
  return () => clearListeners.delete(fn);
}

/* ---------------------------------------------------------------- peek

   What a loader WOULD resolve to, read synchronously from the cache - so a
   page can paint its data on the first frame instead of flashing a spinner
   while an already-cached promise resolves.

   `peekCached(() => getProducts(filters))` runs the loader with `get`
   switched to a cache lookup: it sends no request and returns the cached
   value, or undefined when there is none. Anything the loader returns other
   than a plain get() (a Promise.resolve(null) guard, say) is a miss.

   Age is ignored on purpose: a peek only picks what to show first, and the
   caller still runs the real loader, which refetches anything past the TTL.
   Entries never outlive a write - clearApiCache empties the map. */
const PEEK = Symbol('peek');
let peeking = false;

export function peekCached(loader) {
  peeking = true;
  try {
    const out = loader();
    return out && out[PEEK] ? out.data : undefined;
  } catch {
    return undefined;
  } finally {
    peeking = false;
  }
}

/* ---------------------------------------------------------------- auth

   Every route but /auth/* answers 401 { auth_required: true } without a
   signed-in session (backend/auth.py). That is not an error a screen can do
   anything about, so it is turned into one event here: App.jsx listens and
   puts the login page back. It also throws, so a 401 body is never cached
   as if it were the data a screen asked for. */
const authListeners = new Set();

/** Run `fn` when the server says the session has ended. Returns an
 *  unsubscribe function. */
export function onAuthExpired(fn) {
  authListeners.add(fn);
  return () => authListeners.delete(fn);
}

function sessionEnded(status, body) {
  if (status !== 401 || !body?.auth_required) return false;
  clearApiCache();
  authListeners.forEach(fn => fn());
  return true;
}

async function doFetch(method, path, body) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    // Plain language on screen. The fetch failure rides along as `cause`, so a
    // developer is still one console expand away from the real reason.
    throw new Error('Cannot reach the server. Make sure it is running, then refresh this page.', { cause: err });
  }
  // Validation failures come back as 400 with a { ok:false, errors } body,
  // which callers already handle - only a response with no JSON body at
  // all (network error, backend down) or an ended session should throw.
  let out;
  try {
    out = await res.json();
  } catch (err) {
    // This used to surface as "GET /meta returned no JSON (status 502)". A
    // method, a path and a status code mean nothing to whoever is running the
    // tally, so they move off the message and onto the error itself - still the
    // first thing a developer sees when they expand it in the console.
    const failed = new Error('The server did not respond properly. Wait a moment, then refresh this page.', { cause: err });
    failed.request = `${method} ${path}`;
    failed.status = res.status;
    throw failed;
  }
  if (sessionEnded(res.status, out)) throw new Error(out.error);
  return out;
}

async function request(method, path, body) {
  if (method !== 'GET') {
    const out = await doFetch(method, path, body);
    clearApiCache();
    return out;
  }

  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.data;

  const pending = inflight.get(path);
  if (pending) return pending;

  const p = doFetch('GET', path)
    .then(data => {
      cache.set(path, { at: Date.now(), data });
      inflight.delete(path);
      return data;
    })
    .catch(err => {
      inflight.delete(path);
      throw err;
    });
  inflight.set(path, p);
  return p;
}

const get = path => {
  if (peeking) {
    const hit = cache.get(path);
    return hit ? { [PEEK]: true, data: hit.data } : undefined;
  }
  return request('GET', path);
};
const post = (path, body) => request('POST', path, body);
const put = (path, body) => request('PUT', path, body);
const del = path => request('DELETE', path);

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
/** The date filter's "pick your own months" option (backend catalog.CUSTOM_RANGE). */
export const CUSTOM_RANGE = 'Custom Range';
export const UNATTRIBUTED = 'Unattributed';

/** TRANSACTION_TYPE values. SALE is a sale; the rest are non-sale removals.
 *  Stored on Fact_Sales.transaction_type. */
export const TRANSACTION_TYPES = ['SALE', 'DAMAGED', 'PROMO', 'TRANSFER'];

// -------------------------------------------------------------------- auth

/** Who is signed in -> { authenticated, user }. Never cached: it is the one
 *  question whose answer changes without a write going through here. */
export const getSession = () => doFetch('GET', '/auth/session');

/** -> { ok:true, user } or { ok:false, error } (wrong password, locked out). */
export const login = (username, password) => post('/auth/login', { username, password });

export const logout = () => post('/auth/logout');

// ---------------------------------------------------------------- metadata

export const getMeta = () => get('/meta');

/** `forecastable: true` narrows the list to suppliers that own at least one
 *  SKU the model actually forecast — the Demand Forecast screen's dropdown.
 *  It falls back to the full list when the pipeline has produced no forecasts
 *  at all, matching that screen's own fallback. */
/** `month` ('YYYY-MM') narrows the list to suppliers with a sale that month —
 *  the batch report's filter, whose report is a single month. */
export const getSuppliers = ({ forecastable = false, month } = {}) =>
  get(`/suppliers${qs({ forecastable: forecastable ? 1 : '', month })}`);

export const getCategories = () => get('/categories');

export const getMonths = () => get('/months');

// ---------------------------------------------------------------- catalog

/** The catalogue, cut by the topbar's three filters.
 *
 *  `dateRange` windows the *sales* figures each row carries (total_units,
 *  adus, avg_monthly, cv, revenue) — it does not window current_stock,
 *  days_of_supply or fsn_class, which describe the present rather than a
 *  slice of history. Passing it matters: every product-derived panel reads
 *  this endpoint, so while it was omitted the date filter moved the trend
 *  chart alone and left every other figure at its all-time value. */
export const getProducts = (filters = {}) =>
  get(`/products${qs({
    supplier: filters.supplier,
    category: filters.category,
    ...rangeParams(filters),
  })}`);

/** The date filter as query params: the preset label, plus the From/To months
 *  when it is "Custom Range" (whole months, inclusive). */
function rangeParams(filters) {
  const custom = filters.dateRange === CUSTOM_RANGE;
  return {
    dateRange: filters.dateRange,
    start: custom ? filters.rangeFrom : undefined,
    end: custom ? filters.rangeTo : undefined,
  };
}

/** Products that have at least one Fact_Sales row — the tally screen's
 *  item picker, so a user cannot tally against a name with no history. */
export const getSellableProducts = () => get('/products?has_history=1');

// ---------------------------------------------------------------- sales

export const getMonthlyUnits = (filters = {}) =>
  get(`/sales/monthly${qs({ supplier: filters.supplier, category: filters.category, ...rangeParams(filters) })}`);

/** One product's observed monthly units — measured history, no fit. */
export const getProductHistory = productId => get(`/products/${productId}/history`);

/** Per-supplier unit counts and remittance line totals for one month.
 *  §3.2's batch sales report: internal reporting, not a customer document. */
export const getBatchReport = (month, supplier) =>
  get(`/reports/batch${qs({ month, supplier })}`);

/** URL of the server-rendered PDF for one month ('YYYY-MM').
 *
 *  Returns a URL rather than fetching, because the browser has to do the
 *  navigating: `inline` opens it in the PDF viewer (Print Preview), the
 *  default sends Content-Disposition: attachment (Export as PDF). Building it
 *  here keeps API_BASE in this module, per the rule that no screen constructs
 *  an API path of its own. */
export const batchReportPdfUrl = (month, { inline = false, supplier } = {}) =>
  `${API_BASE}/reports/batch.pdf${qs({ month, supplier, inline: inline ? 1 : '' })}`;

/** Same report, as a raw-data-shaped .csv/.xlsx download - same shared body
 *  as the PDF (backend/batch_export.py), so the numbers can never diverge. */
export const batchReportCsvUrl = (month, supplier) =>
  `${API_BASE}/reports/batch.csv${qs({ month, supplier })}`;
export const batchReportXlsxUrl = (month, supplier) =>
  `${API_BASE}/reports/batch.xlsx${qs({ month, supplier })}`;

// ---------------------------------------------------------------- FSN

export const getFsnSensitivity = () => get('/fsn/sensitivity');

// ---------------------------- analytics the pipeline has produced (or not) --
// Do not "fill these in" client-side: forecasts come from
// step4_forecast_model.py and ROP/EOQ from Dim_Parameters +
// step5_prescriptive.py. The backend returns an explicit pending shape
// ({ available: false, reason, data: null }) rather than a number when the
// pipeline hasn't produced one.

export const getForecast = () => get('/forecast');
export const getForecastMetrics = () => get('/forecast');
export const getProductForecast = productId => get(`/forecast/${productId}`);

// Category-level forecast, from step4c_category_forecast.py: the category's
// sales are added up first and THAT total is forecast, so it covers every item
// in the category. The items listed under it (`contributors`) are a breakdown
// from the per-item forecasts and do not add up to it. When step4c has not run
// the backend falls back to summing the item forecasts and says so in
// `data.source` ('category_model' | 'sum_of_items').
// encodeURIComponent because the names carry spaces and '&'
// ("Shirts & Tops", "Umbrellas & Gear").
export const getCategoryForecast = category =>
  get(`/forecast/category/${encodeURIComponent(category)}`);
// Which categories have a category-level forecast, biggest first. Includes
// categories with no Fast item, which the per-item forecasts cannot reach.
export const getCategoryForecasts = () => get('/forecast/categories');
export const getReorderAlerts = () => get('/reorder');
export const getAdvisories = () => get('/advisories');

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

/** Mark a date closed or open. `reason` is free text and optional — it is
 *  stored on Closure_Log.reason, which has always existed in the schema but
 *  had no way in from the interface until now. Reopening a date carries a
 *  reason too, since "why did this reopen" is as worth recording as why it
 *  shut. */
export async function setStoreClosed(isoDate, closed, reason = '') {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) {
    return { ok: false, errors: { calendar_date: 'Date must be YYYY-MM-DD.' } };
  }
  return put(`/calendar/${isoDate}/closure`, { closed: !!closed, reason: (reason || '').trim() });
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

// -------------------------------------------------------------- inventory

/** Staff-entered stock counts for one month ('YYYY-MM').
 *  -> { month, counts:[...], total_units, workbook_month }. */
export const getInventoryCounts = month => get(`/inventory?month=${encodeURIComponent(month)}`);

/** Record (or correct) one product's units on hand for a month. Upsert:
 *  re-submitting the same product+month replaces the figure rather than adding
 *  to it, and the response's `replaced` says what the previous value was.
 *  Zero is a legitimate count — it means the store is out of that item. */
export const saveInventoryCount = ({ product_id, count_month, quantity, note }) =>
  post('/inventory', { product_id, count_month, quantity, note });

/** Remove a count recorded by mistake. */
export const deleteInventoryCount = countId => del(`/inventory/${countId}`);

// ------------------------------------------------------- import / export

/** Upload a .csv/.xlsx of stock counts or sales tallies.
 *
 *  Multipart, so these bypass request() — that helper sets a JSON
 *  Content-Type, and a multipart body must be left alone for the browser to
 *  attach its own boundary. The server archives the file into `rawdata/`
 *  exactly as uploaded and returns
 *  { ok, imported, updated, rejected:[{row,item,reason}], rows_read, saved_to }.
 *
 *  Rejected rows are reported rather than dropped, so the caller is expected
 *  to show them. */
async function upload(path, file, fields = {}) {
  const body = new FormData();
  body.append('file', file);
  for (const [k, v] of Object.entries(fields)) if (v != null) body.append(k, v);
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: 'POST', body, credentials: 'same-origin' });
  } catch {
    throw new Error(
      'Cannot reach the backend. Make sure the Flask server is running on :5000 '
      + '(cd backend && python app.py).'
    );
  }
  clearApiCache();   // an import changes Fact_Sales / Inventory_Count
  let out;
  try {
    out = await res.json();
  } catch {
    return { ok: false, error: `Import failed (status ${res.status}).` };
  }
  sessionEnded(res.status, out);
  return out;
}

export const importInventoryCounts = (file, month) =>
  upload('/inventory/import', file, { month });

/** Either import also returns `held` / `held_names`: rows whose item name the
 *  vocabulary does not know. They are not rejected; they wait under Names to
 *  review and are loaded when the name is settled there. */
export const importTallyEntries = file => upload('/tally/import', file);

/** The store's own tally workbook (the "<MONTH> - TBS" sheets), saved into
 *  rawdata/ for the next pipeline run to read.
 *  -> { ok, saved_as, months:['YYYY-MM'], replaced_file, takes_over:[{month, from:[...]}] }. */
export const addTallyWorkbook = file => upload('/tally/workbook', file);

// ------------------------------------------------------------ names to review

/** Item names the vocabulary does not know yet — from the tally sheets (the
 *  pipeline loaded them as provisional items) and from imported files (rows
 *  held) — with the matcher's suggestion where it has one.
 *  -> { names:[{ raw_name, source, units, supplier_name, held_rows, suggestion }],
 *       awaiting_run:[{ raw_name, item_name }], strong }. */
export const getNamesToReview = () => get('/names/review');

/** Settle one name. `action`: 'same' (with product_id), 'new' (an import-only
 *  name also takes category / supplier_name), or 'discard' (import-only).
 *  Appends one row to the controlled vocabulary. */
export const settleName = ({ raw_name, action, product_id, category, supplier_name }) =>
  post('/names/review', { raw_name, action, product_id, category, supplier_name });

/** The tally sheet now calls an item something else. The new name is added
 *  to the vocabulary as another name for the item: history and the name
 *  shown here are kept. */
export const renameProduct = (productId, newName) =>
  post(`/products/${productId}/rename`, { new_name: newName });

// ---------------------------------------------------------------- catalog

/** Create one item so a count can be recorded against something the catalogue
 *  does not have yet. Permanent: the backend also writes it to the vocabulary
 *  and the inventory source, which is what step1 rebuilds Dim_Product from. */
export const addProduct = ({ item_name, category, supplier_name }) =>
  post('/products', { item_name, category, supplier_name });

/** Mark an item discontinued (true) or back in stock (false). A discontinued
 *  item drops out of the tally item pickers; its history is kept. */
export const setProductDiscontinued = (productId, discontinued) =>
  put(`/products/${productId}/status`, { discontinued });

// --------------------------------------------------------------- pipeline

/** Kicks off create_schema.py -> step5_prescriptive.py as a background job.
 *  Returns { ok:false, error } (HTTP 409) if a run is already in progress.
 *
 *  `includeForecast: false` leaves out step4_forecast_model.py, which forecasts
 *  every Fast SKU (a blend of category share and TSB, plus a few Prophet fits
 *  for the daily pattern). It used to be a full-MCMC Prophet run costing 1-2
 *  hours, which is where the opt-out comes from; it now finishes in about 10
 *  seconds. Nothing else in the pipeline reads its output, so the rest of the
 *  run (rebuilt database, FSN classes, reorder points) is unaffected either
 *  way. */
export const runPipeline = ({ includeForecast = true } = {}) =>
  post('/pipeline/run', { include_forecast: includeForecast });

/** Terminates the step currently running and halts the rest of the run.
 *  Returns { ok:false, error } (HTTP 409) if nothing is in progress. */
export const stopPipeline = () => post('/pipeline/stop');

/** Poll this while a run is in flight — { status, steps:[{id,label,status,...}] }. */
export const getPipelineStatus = () => get('/pipeline/status');

/** Whether the analytics on screen are older than what has been tallied.
 *  Tally entries / events / closures are written to ustore.db the moment they
 *  are saved, but fsn_class, Result_Forecast and Result_Prescriptive are only
 *  recomputed by a pipeline run — so the Reorder and Classification screens can
 *  be showing numbers that predate everything typed in since. Shape:
 *  { stale, never_run, running, last_run:{finished_at,...}, pending:{tally_entries,events,closures}, total_pending }. */
export const getPipelineStaleness = () => get('/pipeline/staleness');
