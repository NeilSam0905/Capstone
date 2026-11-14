/**
 * Which forecast model is recommended for one item or category, from the
 * API's `model_metrics` (every model's past 30-day checks). Shared by the
 * Demand Forecast page and the prefetcher (services/prefetch.js), so the
 * prefetcher warms exactly the forecast the page will open on.
 */

/** Every model's check, lowest scaled miss (MASE) first; models with no
 *  figures (not made yet, or too little history to check) at the end, in the
 *  offered order. */
export function rankModels(rows) {
  const scored = rows.filter(r => r.mase != null).sort((a, b) => a.mase - b.mase);
  return [...scored, ...rows.filter(r => r.mase == null)];
}

/** The model recommended for one item or category: the lowest MASE on its own
 *  past checks. Null when no model has been checked for it yet. Its
 *  `recommended` flag says whether it is also the store-wide model the rest of
 *  the dashboard uses. */
export function bestModelOf(metrics) {
  const top = rankModels(metrics ?? [])[0];
  return top?.mase != null && top.made ? top : null;
}
