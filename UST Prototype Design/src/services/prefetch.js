/**
 * Warm the API cache for every dashboard page in the background, so opening
 * a page for the first time paints its data at once instead of a spinner.
 *
 * It requests exactly what each page asks for with its topbar filters at
 * their defaults (App.jsx's UNFILTERED) - the same paths, so the pages'
 * own requests land on these cache entries, and useData's cache peek shows
 * them on the first frame.
 *
 * Runs one request at a time, starting a moment after the app opens, so it
 * never competes with the page actually on screen. Every write clears the
 * cache (dataService's clearApiCache), so it re-warms shortly after one.
 *
 * Demand Forecast is warmed whole: every category's forecast and every
 * forecast item's (only the items Run Forecast made one for - a few dozen),
 * each on the model the page opens it on, so picking any of them is instant.
 *
 * Not covered: a filter the user changes (each value is its own request) and
 * a model picked by hand from the forecast's model menu. Those load on demand.
 */
import {
  getMeta, getMonths, getCategories, getSuppliers, getProducts, getMonthlyUnits,
  getStockPosition, getReorderAlerts, getAdvisories, getDecisionRules, getForecast,
  getCategoryForecasts, getCategoryForecast, getProductForecast, getBatchReport, onCacheCleared,
} from './dataService';
import { bestModelOf } from '../lib/bestModel';

// Must match App.jsx's UNFILTERED - the filters every page starts on.
const DEFAULT_FILTERS = {
  dateRange: 'All Time',
  supplier: 'All Suppliers',
  category: 'All Categories',
  rangeFrom: null,
  rangeTo: null,
};

const START_DELAY_MS = 400;     // let the screen on show fetch first
const REWARM_DELAY_MS = 1500;   // after a write, once the burst of saves settles

let generation = 0;             // bumps on every (re)start; stale runs stop
let timer = null;

async function run(gen) {
  const live = () => gen === generation;
  const step = async fn => {
    if (!live()) return undefined;
    try { return await fn(); } catch { return undefined; }   // a failed warm-up is not an error
  };

  // Shared by several pages, and the topbar.
  await step(getMeta);
  const months = await step(getMonths);
  await step(getCategories);
  await step(() => getSuppliers({}));
  await step(() => getSuppliers({ forecastable: true }));

  // Overview, FSN Classification, Reorder Alerts, Demand Forecast.
  await step(() => getProducts(DEFAULT_FILTERS));
  await step(() => getMonthlyUnits(DEFAULT_FILTERS));
  await step(() => getStockPosition(DEFAULT_FILTERS));
  await step(getReorderAlerts);
  await step(getAdvisories);
  await step(() => getDecisionRules());
  const forecastList = await step(getForecast);

  // Batch Sales Report opens on the latest month.
  const latest = Array.isArray(months) ? months[months.length - 1] : null;
  if (latest) {
    await step(() => getSuppliers({ period: { month: latest } }));
    await step(() => getBatchReport({ month: latest }, undefined));
  }

  // Demand Forecast: every category's chart, then every forecast item's, so
  // switching category or item is instant too. Last, because it is the
  // largest batch.
  const cats = await step(getCategoryForecasts);
  for (const r of cats?.available ? cats.data.categories : []) {
    await warmForecast(step, getCategoryForecast, r.category);
  }
  for (const p of forecastList?.available ? forecastList.data.products : []) {
    await warmForecast(step, getProductForecast, p.product_id);
  }
}

/** One item's or category's forecast as the Demand Forecast page opens it
 *  (Forecast.jsx's useRecommendedForecast): the plain request, whose
 *  model_metrics say which model is recommended for it, then that model's
 *  forecast too when it is not the store-wide one the plain request is. */
async function warmForecast(step, load, id) {
  const base = await step(() => load(id));
  const best = bestModelOf(base?.data?.model_metrics);
  if (best && !best.recommended) await step(() => load(id, best.id));
}

function schedule(delay) {
  generation += 1;
  const gen = generation;
  clearTimeout(timer);
  timer = setTimeout(() => { run(gen); }, delay);
}

let started = false;
let unsubscribe = null;

/** Start warming (idempotent), and re-warm after every write. */
export function startPrefetch() {
  if (started) return;
  started = true;
  schedule(START_DELAY_MS);
  unsubscribe = onCacheCleared(() => schedule(REWARM_DELAY_MS));
}

/** Stop warming, on sign-out. Every request would answer 401 then, and each
 *  one clears the cache - which would schedule the next re-warm, forever. */
export function stopPrefetch() {
  if (!started) return;
  started = false;
  generation += 1;              // a run already in flight stops at its next step
  clearTimeout(timer);
  unsubscribe?.();
  unsubscribe = null;
}
