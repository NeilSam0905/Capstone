import { useState, useMemo } from 'react';
import {
  getProducts, getForecast, getProductForecast, getProductHistory,
  getCategoryForecast,
} from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart, ForecastChart } from '../components/charts';
import { num, shortMonth, usDate, modelLabel, FSN_TONE, FSN_LABEL } from '../lib/format';
import { ALL_SUPPLIERS } from '../services/dataService';

const ALL_ITEMS = '__all__';

/**
 * Demand Forecast.
 *
 * CATEGORY FIRST, item second. The 30-day chart shows the whole category's
 * forecast until an item is picked, then it shows that item alone.
 *
 * The category figure is the SUM of its items' forecasts (see
 * /api/forecast/category in app.py), not a separately fitted category model.
 * That is what lets a user drill from the category into an item without the
 * two numbers disagreeing on screen.
 *
 * When Result_Forecast exists (step4_forecast_model.py has run) this shows
 * the forecast with its confidence band and accuracy check. When it doesn't,
 * it shows the pending state and the real observed history — no fabricated
 * numbers either way.
 */
export default function Forecast({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `forecast:products:${filters.supplier}|${filters.category}` });
  const { data: forecastMeta } = useData(getForecast, []);
  const [category, setCategory] = useState(null);
  const [selectedId, setSelectedId] = useState(ALL_ITEMS);

  // Only SKUs step4_forecast_model.py actually produced a forecast for
  // (the Fast tier) belong in the item dropdown - every other SKU would just
  // open onto the "no forecast" pending state, which is pointless to pick
  // from a list of hundreds. Falls back to every SKU with sales history if
  // the pipeline hasn't been run at all yet, so the pending state still has
  // something to show.
  const forecastableIds = useMemo(
    () => forecastMeta?.data?.products ? new Set(forecastMeta.data.products.map(p => p.product_id)) : null,
    [forecastMeta]
  );
  const withHistory = useMemo(() => {
    const withSales = products.filter(p => p.total_units > 0);
    const filtered = forecastableIds ? withSales.filter(p => forecastableIds.has(p.product_id)) : withSales;
    return filtered.sort((a, b) => b.total_units - a.total_units);
  }, [products, forecastableIds]);

  // Categories that actually have something to show, biggest first. Derived
  // from the forecastable set rather than from /api/categories so the list
  // never offers a category that opens straight onto an empty state.
  const categories = useMemo(() => {
    const totals = new Map();
    for (const p of withHistory) {
      const c = p.category || 'Uncategorised';
      totals.set(c, (totals.get(c) || 0) + (p.total_units || 0));
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  }, [withHistory]);

  const activeCategory = category ?? categories[0] ?? null;

  const itemsInCategory = useMemo(
    () => withHistory.filter(p => (p.category || 'Uncategorised') === activeCategory),
    [withHistory, activeCategory]
  );

  // Changing category must not leave a stale item selected from the previous
  // one. Resetting happens in the change handler below (the actual user
  // action); this lookup is the guard for every other way the list can shift
  // under a selection - a supplier filter change, a pipeline re-run. An id
  // that is no longer in this category simply resolves to null, which renders
  // the category view rather than an item that is not in it.
  const product = selectedId === ALL_ITEMS
    ? null
    : itemsInCategory.find(p => p.product_id === selectedId) ?? null;

  // Which supplier the item on screen belongs to. Only worth stating while the
  // list spans every supplier — with one selected the topbar already says it.
  const showSupplier = filters.supplier === ALL_SUPPLIERS;

  if (loading) return <Loading label="Loading products…" />;
  if (!activeCategory) {
    return (
      <div className="empty">
        {forecastableIds
          ? "No forecasted products match this filter."
          : "No products match this filter."}
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="card card__pad">
        <div className="card-h" style={{ marginBottom: 0 }}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="fc-category">Category</label>
              <div className="filter">
                <select
                  id="fc-category"
                  value={activeCategory}
                  onChange={e => { setCategory(e.target.value); setSelectedId(ALL_ITEMS); }}
                  style={{ minWidth: 210 }}
                >
                  {categories.map(c => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <span className="filter__chev">▾</span>
              </div>
            </div>
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="fc-item">Item</label>
              <div className="filter">
                <select
                  id="fc-item"
                  value={selectedId}
                  onChange={e => setSelectedId(
                    e.target.value === ALL_ITEMS ? ALL_ITEMS : Number(e.target.value))}
                  style={{ minWidth: 300 }}
                >
                  {/* Names the scope, not the category: the category select
                      beside it already says which one, and repeating it made
                      the option text grow with the longest category name. */}
                  <option value={ALL_ITEMS}>All Items in Category</option>
                  {itemsInCategory.map(p => (
                    <option key={p.product_id} value={p.product_id}>
                      {p.item_name}{showSupplier ? ` — ${p.supplier_name}` : ''} ({num(p.total_units)} units)
                    </option>
                  ))}
                </select>
                <span className="filter__chev">▾</span>
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {product ? (
              <>
                <span className={`tag tag--${FSN_TONE[product.fsn_class]}`}>{FSN_LABEL[product.fsn_class]}</span>
                {product.is_hvl === 1 && <span className="tag tag--hvl">HVL</span>}
                {showSupplier && (
                  <span className="hint" title={product.supplier_name}>
                    Supplier: <b style={{ color: 'var(--text-2)' }}>{product.supplier_name}</b>
                  </span>
                )}
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/* 30-day forecast — category total, or the selected item */}
      {product
        ? <ForecastPanel productId={product.product_id} forecastMeta={forecastMeta} />
        : <CategoryForecastPanel category={activeCategory} forecastMeta={forecastMeta}
                                 onPickItem={setSelectedId} />}

      {/* Observed monthly history — per item only. There is no category-level
          history endpoint, and summing one client-side from a filtered product
          list would quietly exclude the non-Fast items the category contains,
          producing a total that does not match anything. */}
      {product ? (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Observed Monthly Units — {product.item_name}</span>
            <span className="hint">actual tallied history · no fitted line, no projection</span>
          </div>
          <HistoryChart productId={product.product_id} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * The category view: one summed 30-day line plus the items behind it.
 *
 * `n_forecast` vs `n_products` is stated explicitly because only Fast-moving
 * items are forecast — the total is for those items, not for everything the
 * category contains, and a reader who assumes otherwise would over-order.
 */
function CategoryForecastPanel({ category, forecastMeta, onPickItem }) {
  const { data: forecast, loading } = useData(
    () => getCategoryForecast(category), [category], null,
    { key: `forecast:category:${category}` }
  );

  if (loading) return <Loading label="Loading category forecast…" />;

  if (!forecastMeta?.available || !forecast?.available) {
    return (
      <Pending
        title={`No forecast for ${category}`}
        reason={forecast?.reason ?? forecastMeta?.reason}
      />
    );
  }

  const fd = forecast.data;

  return (
    <>
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">30-Day Demand Forecast — {category}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="tag tag--gold" title={fd.model_type}>{modelLabel(fd.model_type)}</span>
            <span className="hint">Generated {usDate(fd.snapshot_date)}</span>
          </div>
        </div>

        <ForecastChart data={fd.forecast} />
        <div className="legend" style={{ justifyContent: 'center', marginTop: 10 }}>
          <span><i style={{ background: 'var(--accent)' }} />Forecast (ŷ)</span>
          <span><i style={{ background: 'var(--accent)', opacity: 0.15 }} />Confidence band</span>
        </div>
      </div>

      <CategoryTotalCard category={category} fd={fd} onPickItem={onPickItem} />
    </>
  );
}

/**
 * The category total and the items behind it — its own card, because it
 * answers a different question from the chart above it ("how much do we
 * expect" rather than "what shape is the month") and was previously a plain
 * heading and an unstyled table tacked onto the end of the chart card.
 *
 * Three things carry the caveat that this is a PARTIAL total, because a
 * reader who misses it over-orders: the count, a coverage meter, and the
 * sentence. The meter is the one that works at a glance - a sliver of fill
 * says "most of this category is not in this number" before any of it is
 * read.
 */
function CategoryTotalCard({ category, fd, onPickItem }) {
  const partial = fd.n_forecast < fd.n_products;
  const covered = fd.n_products ? fd.n_forecast / fd.n_products : 0;

  // Bars are scaled to the largest contributor, not to the total: at 30 items
  // every bar would be a stub against the sum, and the column is here to rank
  // the items against each other.
  const peak = Math.max(...fd.contributors.map(r => r.yhat_30d || 0), 0) || 1;

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Expected Demand — Next 30 Days</span>
        <span className="hint">{category}</span>
      </div>

      <div className="ftotal">
        <div className="ftotal__figure">
          <div className="ftotal__val">{num(Math.round(fd.total_30d))}</div>
          <div className="ftotal__unit">units forecast</div>
        </div>

        <div className="ftotal__coverage">
          <div className="ftotal__coverage-head">
            <span className="ftotal__coverage-lbl">Items covered</span>
            <b>{num(fd.n_forecast)} <span>of</span> {num(fd.n_products)}</b>
          </div>
          <div className="meter" role="img"
               aria-label={`${fd.n_forecast} of ${fd.n_products} items in ${category} are forecast`}>
            <div className="meter__fill" style={{ width: `${Math.max(covered * 100, 1.5)}%` }} />
          </div>
          <p className="ftotal__note">
            {partial ? (
              <>Only Fast-moving items are forecast, so this total covers the{' '}
                <b>{fd.n_forecast} item{fd.n_forecast === 1 ? '' : 's'}</b> listed below —
                not all {num(fd.n_products)} in {category}.</>
            ) : (
              <>Every item in {category} is forecast, so this total covers the whole category.</>
            )}
          </p>
        </div>
      </div>

      <div className="tbl__scroll">
        <table className="tbl" style={{ minWidth: 560 }}>
          <thead>
            <tr>
              <th>Item</th>
              <th>Supplier</th>
              <th className="num" colSpan={2}>30-day forecast</th>
            </tr>
          </thead>
          <tbody>
            {fd.contributors.map(r => {
              const units = Math.round(r.yhat_30d || 0);
              return (
                <tr key={r.product_id}
                    onClick={() => onPickItem(r.product_id)}
                    className="is-clickable"
                    title="Show this item on its own">
                  <td className="strong">
                    <span className="cell-trunc" style={{ '--trunc': '320px' }}>{r.item_name}</span>
                  </td>
                  <td>{r.supplier_name}</td>
                  {/* The bar ranks the row against the biggest contributor.
                      One hue for every row: these are the same kind of thing,
                      and colour by rank would say they are not. It sits to the
                      LEFT of the value so the fill grows towards the number it
                      belongs to, and the right-aligned heading stays over the
                      column it names. */}
                  <td style={{ width: 130 }}>
                    <span className="minibar">
                      <span className="minibar__fill"
                            style={{ width: `${(r.yhat_30d || 0) / peak * 100}%` }} />
                    </span>
                  </td>
                  <td className="num" style={{ width: 80 }}>
                    {units === 0 ? <span className="nodata">0</span> : num(units)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ForecastPanel({ productId, forecastMeta }) {
  const { data: forecast, loading } = useData(
    () => getProductForecast(productId), [productId], null,
    { key: `forecast:${productId}` }
  );

  if (loading) return <Loading label="Loading forecast…" />;

  // No forecast available at all (pipeline not run)
  if (!forecastMeta?.available || !forecast?.available) {
    return (
      <>
        {/* The explanation comes from the API's `reason` (see app.py's
            FORECAST_PENDING_REASON) rather than a string hardcoded here, so
            the wording is changed in one place. */}
        <Pending
          title="No forecast has been generated for this SKU"
          reason={forecast?.reason ?? forecastMeta?.reason}
        />
      </>
    );
  }

  // Forecast data is available — render it
  const fd = forecast.data;

  return (
    <>
      {/* Forecast chart */}
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">30-Day Demand Forecast — {fd.item_name}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="tag tag--gold" title={fd.model_type}>{modelLabel(fd.model_type)}</span>
            <ReliabilityTag metrics={fd.metrics} isHeuristic={fd.is_heuristic} />
            <span className="hint">Generated {usDate(fd.snapshot_date)}</span>
          </div>
        </div>
        <ForecastChart data={fd.forecast} />
        <div className="legend" style={{ justifyContent: 'center', marginTop: 10 }}>
          <span><i style={{ background: 'var(--accent)' }} />Forecast (ŷ)</span>
          <span><i style={{ background: 'var(--accent)', opacity: 0.15 }} />Confidence band</span>
        </div>

        {fd.is_heuristic && (
          <div className="notice notice--warn" style={{ marginTop: 12 }}>
            Not enough sales history yet to double-check this forecast — treat it as a rough estimate.
          </div>
        )}
      </div>
    </>
  );
}

/**
 * Whether the forecast held up when it was checked against real sales, as one
 * tag beside the model's own.
 *
 * This was a card of its own titled "How Reliable Is This Forecast?". A whole
 * card to say one word pushed the observed-history chart below the fold, so
 * the verdict now sits next to the model that produced it and the sentence
 * behind it is the tag's tooltip.
 *
 * MAPE is deliberately never shown: on this dataset it is undefined whenever a
 * period had zero actual sales (the common case) and reads as 100%+ even for a
 * working forecast, so surfacing it to a non-technical reader does more harm
 * than good (see docs/DEGENERATE_FORECAST.md, docs/SPARSE_DEMAND_EXPERIMENTS.md).
 */
function ReliabilityTag({ metrics, isHeuristic }) {
  if (isHeuristic) {
    return (
      <span className="tag tag--warn"
            title="This item hasn't sold long enough to check this forecast against real results.">
        Not enough history
      </span>
    );
  }

  const overall = metrics?.find(m => m.period_scope === 'overall') ?? metrics?.[0];
  if (!overall || overall.mae == null) {
    return (
      <span className="tag tag--info" title="No accuracy check has been recorded for this item yet.">
        Not checked
      </span>
    );
  }

  const reliable = !!overall.beats_naive_mae;
  const typicalOff = Math.round(overall.mae);
  const checked = `Checked against ${overall.n_obs} past 30-day period${overall.n_obs === 1 ? '' : 's'} of real sales `
    + `— actual sales were typically about ${typicalOff} unit${typicalOff === 1 ? '' : 's'} away from this forecast.`;

  return reliable
    ? <span className="tag tag--ok" title={`More accurate than just repeating last month's number. ${checked}`}>
        Reliable
      </span>
    : <span className="tag tag--warn"
            title={`No more accurate than repeating last month's number — use with caution. ${checked}`}>
        Rough estimate
      </span>;
}

function HistoryChart({ productId }) {
  const { data, loading } = useData(() => getProductHistory(productId), [productId], [],
    { key: `history:${productId}` });
  if (loading) return <Loading />;
  if (!data || data.length === 0) return <div className="empty">No monthly history for this product.</div>;
  return <LineChart data={data.map(d => ({ label: shortMonth(d.month), value: d.units }))} height={240} />;
}
