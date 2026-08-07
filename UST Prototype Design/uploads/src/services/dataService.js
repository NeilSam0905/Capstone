/**
 * dataService — the ONLY module in this app that touches data.
 *
 * Every screen calls these functions; no screen imports a fixture, a JSON
 * file or the tally store directly. In Phase 3 this file is the single
 * thing that changes: each function body becomes a `fetch()` against the
 * real API and the screens stay exactly as they are.
 *
 * That is why everything here is async even though it currently resolves
 * from an in-memory import — the screens already handle loading states,
 * so swapping in a network call changes nothing above this line.
 *
 * Field names mirror the star schema in create_schema.py (product_id,
 * item_name, unit_price_php, calendar_date, quantity_sold), so the
 * fixtures and the future API responses have the same shape.
 *
 * BIR compliance (see PROMPT_1_FRONTEND.md §1): this is an internal
 * inventory counting service. It exposes unit counts, and unit prices as
 * reference data for supplier-remittance reporting. It must never gain a
 * cart, a checkout, a customer total, change due, or a receipt.
 */
import DIM_PRODUCT from './fixtures/dim_product.json';
import DIM_DATE from './fixtures/dim_date.json';
import FACT_MONTHLY from './fixtures/fact_sales_monthly.json';
import FACT_RECENT from './fixtures/fact_sales_recent.json';
import PRODUCT_STATS from './fixtures/product_stats.json';
import FSN_SENSITIVITY from './fixtures/fsn_sensitivity.json';
import EVENT_LOG_FIXTURE from './fixtures/event_log.json';
import META from './fixtures/meta.json';

import * as tallyStore from './tallyStore';

/** Simulated latency, so loading states are exercised in development. */
const LATENCY_MS = 60;
const resolve = value => new Promise(r => setTimeout(() => r(value), LATENCY_MS));

export const ALL_SUPPLIERS = 'All Suppliers';
export const ALL_CATEGORIES = 'All Categories';
export const UNATTRIBUTED = 'Unattributed';

/** TRANSACTION_TYPE values. SALE is a sale; the rest are non-sale removals.
 *  Stored on Fact_Sales.transaction_type. */
export const TRANSACTION_TYPES = ['SALE', 'DAMAGED', 'PROMO', 'TRANSFER'];

const statsById = new Map(PRODUCT_STATS.map(s => [s.product_id, s]));
const productById = new Map(DIM_PRODUCT.map(p => [p.product_id, p]));
const monthsSeen = [...new Set(FACT_MONTHLY.map(m => m.month))].sort();

/** Dim_Product joined to its measured stats. Read-only reference data. */
const CATALOG = DIM_PRODUCT.map(p => {
  const s = statsById.get(p.product_id) || {};
  return {
    ...p,
    supplier_name: p.supplier_name || UNATTRIBUTED,
    category: p.category || 'Uncategorised',
    total_units: s.total_units ?? 0,
    adus: s.adus ?? 0,
    avg_monthly: s.avg_monthly ?? 0,
    cv: s.cv ?? 0,
    active_tally_dates: s.active_tally_dates ?? 0,
    censored_days: s.censored_days ?? 0,
    current_stock: s.current_stock ?? null,
    stock_as_of: s.stock_as_of ?? null,
    days_of_supply: s.days_of_supply ?? null,
    first_sale: s.first_sale ?? null,
    last_sale: s.last_sale ?? null,
    // revenue is reference data for supplier remittance, not a sale total
    revenue: p.unit_price_php != null ? (s.total_units ?? 0) * p.unit_price_php : null,
  };
});

function inRange(month, dateRange) {
  if (dateRange === 'All Time' || !monthsSeen.length) return true;
  const months = { 'Last 3 Months': 3, 'Last 6 Months': 6, 'Last 12 Months': 12 }[dateRange] ?? 12;
  return month >= monthsSeen[Math.max(0, monthsSeen.length - months)];
}

function matches(product, { supplier, category } = {}) {
  return (!supplier || supplier === ALL_SUPPLIERS || product.supplier_name === supplier)
    && (!category || category === ALL_CATEGORIES || product.category === category);
}

// ---------------------------------------------------------------- metadata

export const getMeta = () => resolve(META);

export const getSuppliers = () => resolve(
  [ALL_SUPPLIERS, ...[...new Set(CATALOG.map(p => p.supplier_name))].sort()]
);

export const getCategories = () => resolve(
  [ALL_CATEGORIES, ...[...new Set(CATALOG.map(p => p.category))].sort()]
);

export const getMonths = () => resolve(monthsSeen);

// ---------------------------------------------------------------- catalog

export const getProducts = (filters = {}) =>
  resolve(CATALOG.filter(p => matches(p, filters)));

/** Products that have at least one Fact_Sales row — the tally screen's
 *  item picker, so a user cannot tally against a name with no history. */
export const getSellableProducts = () =>
  resolve(CATALOG.filter(p => p.is_active && p.total_units >= 0 && statsById.has(p.product_id))
    .sort((a, b) => a.item_name.localeCompare(b.item_name)));

// ---------------------------------------------------------------- sales

export async function getMonthlyUnits(filters = {}) {
  const keep = new Set(CATALOG.filter(p => matches(p, filters)).map(p => p.product_id));
  const byMonth = new Map();
  for (const row of FACT_MONTHLY) {
    if (!keep.has(row.product_id) || !inRange(row.month, filters.dateRange)) continue;
    const product = productById.get(row.product_id);
    const acc = byMonth.get(row.month) || { month: row.month, units: 0, revenue: 0, priced_units: 0 };
    acc.units += row.units;
    if (product?.unit_price_php != null) {
      acc.revenue += row.units * product.unit_price_php;
      acc.priced_units += row.units;
    }
    byMonth.set(row.month, acc);
  }
  return resolve([...byMonth.values()].sort((a, b) => a.month.localeCompare(b.month)));
}

/** One product's observed monthly units — measured history, no fit. */
export const getProductHistory = productId => resolve(
  FACT_MONTHLY.filter(m => m.product_id === Number(productId))
    .map(m => ({ month: m.month, units: m.units, tally_days: m.tally_days }))
);

/** Per-supplier unit counts and remittance line totals for one month.
 *  §3.2's batch sales report: internal reporting, not a customer document. */
export async function getBatchReport(month) {
  const bySupplier = new Map();
  for (const row of FACT_MONTHLY) {
    if (row.month !== month || row.units <= 0) continue;
    const p = productById.get(row.product_id);
    if (!p) continue;
    const supplier = p.supplier_name || UNATTRIBUTED;
    const entry = bySupplier.get(supplier)
      || { supplier, items: [], total_units: 0, subtotal: 0, unpriced_units: 0 };
    const lineTotal = p.unit_price_php != null ? row.units * p.unit_price_php : null;
    entry.items.push({
      item_name: p.item_name,
      quantity: row.units,
      unit_price_php: p.unit_price_php,
      line_total: lineTotal,
    });
    // total_units is what the report subtotals on — every unit counts,
    // priced or not. subtotal (pesos) stays in the payload as reference
    // data for supplier remittance, but no screen totals money any more.
    entry.total_units += row.units;
    if (lineTotal != null) entry.subtotal += lineTotal;
    else entry.unpriced_units += row.units;
    bySupplier.set(supplier, entry);
  }
  return resolve([...bySupplier.values()]
    .map(e => ({ ...e, items: e.items.sort((a, b) => b.quantity - a.quantity) }))
    .sort((a, b) => b.subtotal - a.subtotal));
}

// ---------------------------------------------------------------- FSN

export const getFsnSensitivity = () => resolve(FSN_SENSITIVITY);

// ---------------------------- analytics the pipeline has not produced ----
// These resolve to an explicit pending shape rather than a number. Do not
// "fill them in" client-side: forecasts come from step4_prophet_forecast.py
// and ROP/EOQ from Dim_Parameters + step5_prescriptive.py.

const pending = key => resolve({
  available: false,
  reason: META.pending_reason[key],
  data: null,
});

export const getForecast = () => pending('forecast');
export const getForecastMetrics = () => pending('forecast');
export const getReorderAlerts = () => pending('reorder');

/** Stock position IS available for the items inventory covers — real
 *  counts and step2's days_of_supply. It carries no ROP, so it cannot say
 *  "reorder now"; it reports what is on hand and how long it lasts. */
export async function getStockPosition(filters = {}) {
  const items = CATALOG
    .filter(p => matches(p, filters) && p.current_stock != null)
    .sort((a, b) => (a.days_of_supply ?? Infinity) - (b.days_of_supply ?? Infinity));
  return resolve({
    items,
    covered: items.length,
    total: CATALOG.length,
  });
}

// ---------------------------------------------------------------- calendar

export const getCalendar = () => resolve(DIM_DATE);

export async function getDateFlags(isoDate) {
  const base = DIM_DATE.find(d => d.calendar_date === isoDate) || null;
  const overrides = tallyStore.getDateOverrides()[isoDate];
  return resolve(base ? { ...base, ...(overrides || {}) } : (overrides ? { calendar_date: isoDate, ...overrides } : null));
}

// ------------------------------------------------- tally (client-side only)
// TODO: backend — everything below writes to localStorage via tallyStore.
// It is throwaway state for this pass, NOT the system of record. Phase 3
// replaces each of these with a POST/PUT and re-validates server-side.

/** Recent entries = real Fact_Sales rows from the fixture, with anything
 *  entered in this browser session merged on top and marked `is_local`. */
export async function getRecentEntries(limit = 50) {
  const local = tallyStore.getEntries().map(e => ({ ...e, is_local: true }));
  const historical = FACT_RECENT.map(r => ({
    ...r,
    item_name: productById.get(r.product_id)?.item_name ?? '(unknown item)',
    supplier_name: productById.get(r.product_id)?.supplier_name ?? UNATTRIBUTED,
    transaction_type: (r.transaction_type || 'SALE').toUpperCase(),
    is_local: false,
  }));
  const all = [...local, ...historical].sort(
    (a, b) => b.calendar_date.localeCompare(a.calendar_date) || (b.sale_id ?? 0) - (a.sale_id ?? 0)
  );
  return resolve(all.slice(0, limit));
}

export async function getEntriesByDate(isoDate) {
  const all = await getRecentEntries(Infinity);
  return all.filter(e => e.calendar_date === isoDate);
}

/** Validation mirrors what Phase 3 must re-check server-side — the client
 *  copy is a convenience, never the guarantee. Returns a field->message
 *  map; an empty object means valid. */
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
  const product = productById.get(Number(entry.product_id));
  const saved = tallyStore.addEntry({
    product_id: Number(entry.product_id),
    item_name: product?.item_name ?? '(unknown item)',
    supplier_name: product?.supplier_name ?? UNATTRIBUTED,
    quantity_sold: Number(entry.quantity_sold),
    calendar_date: entry.calendar_date,
    transaction_type: entry.transaction_type,
  });
  return resolve({ ok: true, entry: saved });
}

export async function setStoreClosed(isoDate, closed) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) {
    return { ok: false, errors: { calendar_date: 'Date must be YYYY-MM-DD.' } };
  }
  tallyStore.setDateOverride(isoDate, { is_store_closed: closed ? 1 : 0 });
  return resolve({ ok: true });
}

export async function addEvent({ calendar_date, event_name, event_description }) {
  const errors = {};
  if (!calendar_date) errors.calendar_date = 'Pick a date.';
  if (!event_name?.trim()) errors.event_name = 'Give the event a label.';
  if (Object.keys(errors).length) return { ok: false, errors };
  const saved = tallyStore.addEvent({
    calendar_date,
    event_name: event_name.trim(),
    event_description: (event_description || '').trim(),
  });
  tallyStore.setDateOverride(calendar_date, { is_event_day: 1 });
  return resolve({ ok: true, event: saved });
}

export async function getEventLog() {
  return resolve([
    ...tallyStore.getEvents().map(e => ({ ...e, is_local: true })),
    ...EVENT_LOG_FIXTURE.map(e => ({ ...e, is_local: false })),
  ]);
}

export const getClosedDates = () => resolve(
  Object.entries(tallyStore.getDateOverrides())
    .filter(([, v]) => v.is_store_closed === 1)
    .map(([calendar_date]) => calendar_date)
    .sort()
);

export const resetLocalState = () => { tallyStore.reset(); };
