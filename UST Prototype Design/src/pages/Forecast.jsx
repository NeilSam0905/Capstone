import { useState, useMemo } from 'react';
import {
  getProducts, getForecast, getProductForecast, getProductHistory,
  getCategoryForecast, getCategoryForecasts,
} from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart, ScrollForecastChart } from '../components/charts';
import { num, shortMonth, usDate, modelLabel, isCalendarAdjusted, FSN_TONE, FSN_LABEL } from '../lib/format';
import { ALL_SUPPLIERS } from '../services/dataService';

const ALL_ITEMS = '__all__';

/**
 * Demand Forecast.
 *
 * CATEGORY FIRST, item second. The 30-day chart shows the whole category's
 * forecast until an item is picked, then it shows that item alone.
 *
 * The category figure comes from its own model (step4c_category_forecast.py,
 * via /api/forecast/category in app.py): the whole category's sales, every
 * item in it, forecast as one series. The items listed under it are the Fast
 * items' individual forecasts and are a BREAKDOWN, not the parts of the total
 * — they are forecast separately and cover only Fast items, so the two do not
 * add up, and the card says so and shows both numbers. Where step4c has not run,
 * the backend falls back to summing the item forecasts (`data.source ===
 * 'sum_of_items'`) and the card words itself for that instead.
 *
 * When the forecast tables exist this shows the forecast with its confidence
 * band and accuracy check. When they don't, it shows the pending state and the
 * real observed history — no fabricated numbers either way.
 */
export default function Forecast({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `forecast:products:${filters.supplier}|${filters.category}` });
  const { data: forecastMeta } = useData(getForecast, []);
  const { data: categoryList, loading: categoryListLoading } = useData(
    getCategoryForecasts, [], null, { key: 'forecast:categories' });
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

  // Categories that actually have something to show, biggest first, so the
  // list never offers one that opens straight onto an empty state.
  //
  // With category forecasts available, that is every category that has one AND
  // has sales among the products currently shown (so a supplier filter still
  // narrows the list). This is what brings back categories with no Fast item -
  // Home & Novelty, Apparel Accessories - which the per-item forecasts cannot
  // reach. Without them (step4c not run) it falls back to what it always did:
  // the categories of the items that have their own forecast.
  const categories = useMemo(() => {
    const modelled = categoryList?.available ? categoryList.data.categories : null;
    const source = modelled ? products.filter(p => p.total_units > 0) : withHistory;
    const totals = new Map();
    for (const p of source) {
      const c = p.category || 'Uncategorised';
      totals.set(c, (totals.get(c) || 0) + (p.total_units || 0));
    }
    const keep = modelled ? new Set(modelled.map(r => r.category)) : null;
    return [...totals.entries()]
      .filter(([c]) => !keep || keep.has(c))
      .sort((a, b) => b[1] - a[1])
      .map(([c]) => c);
  }, [categoryList, products, withHistory]);

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

  if (loading || categoryListLoading) return <Loading label="Loading products…" />;
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

      {/* 30-day forecast and the monthly history leading up to it — category
          total, or the selected item */}
      {product
        ? <ForecastPanel productId={product.product_id} itemName={product.item_name}
                         forecastMeta={forecastMeta} />
        : <CategoryForecastPanel category={activeCategory} onPickItem={setSelectedId} />}
    </div>
  );
}

/**
 * The category view: one chart of the category's history and forecast, plus
 * the items behind it.
 *
 * Two shapes come back from the API (`data.source`). 'category_model' is the
 * category's own forecast, covering every item in it, with its accuracy check.
 * 'sum_of_items' is the fallback when step4c has not run: the Fast items'
 * forecasts added up, which covers only those items — `n_forecast` vs
 * `n_products` is stated explicitly there because a reader who assumes it
 * covers the whole category would over-order.
 */
function CategoryForecastPanel({ category, onPickItem }) {
  const { data: forecast, loading } = useData(
    () => getCategoryForecast(category), [category], null,
    { key: `forecast:category:${category}` }
  );

  if (loading) return <Loading label="Loading category forecast…" />;

  if (!forecast?.available) {
    return <Pending title={`No forecast for ${category}`} reason={forecast?.reason} />;
  }

  const fd = forecast.data;
  const wholeCategory = fd.source === 'category_model';

  return (
    <>
      <ForecastCard key={category} title={category} fd={fd}
                    tags={wholeCategory && <ReliabilityTag metrics={fd.metrics} isHeuristic={fd.is_heuristic} />}
                    scope={!wholeCategory && fd.n_forecast < fd.n_products
                      ? `the ${fd.n_forecast} forecast item${fd.n_forecast === 1 ? '' : 's'} only`
                      : null} />

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
 * Two shapes (`fd.source`). For the category model the figure covers the
 * whole category, so the caveats that matter are different: how far off it
 * has typically been (in units AND as a share of a typical month, because a
 * bare number makes a 60% miss look small), and that the items listed under
 * it do not add up to it.
 *
 * For the sum-of-items fallback the total is PARTIAL, and three things carry
 * that, because a reader who misses it over-orders: the count, a coverage
 * meter, and the sentence. The meter is the one that works at a glance - a
 * sliver of fill says "most of this category is not in this number" before
 * any of it is read.
 */
function CategoryTotalCard({ category, fd, onPickItem }) {
  const wholeCategory = fd.source === 'category_model';
  const partial = fd.n_forecast < fd.n_products;
  const covered = fd.n_products ? fd.n_forecast / fd.n_products : 0;

  // How far off the category forecast has typically been, from its own
  // walk-forward check. Stated in units because that is what an order is in.
  const overall = fd.metrics?.find(m => m.period_scope === 'overall') ?? fd.metrics?.[0];
  const typicalOff = overall?.mae != null ? Math.round(overall.mae) : null;
  const typicalPct = overall?.mae != null && overall.mean_actual_30d > 0
    ? Math.round(100 * overall.mae / overall.mean_actual_30d) : null;

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

        {wholeCategory ? (
          <div className="ftotal__coverage">
            <div className="ftotal__coverage-head">
              <span className="ftotal__coverage-lbl">Covers</span>
              <b>all {num(fd.n_products)} <span>items in</span> {category}</b>
            </div>
            <p className="ftotal__note">
              Forecast from all of this category&rsquo;s sales taken together
              ({modelLabel(fd.model_type)}), so it includes slow and
              non-moving items too.
              {typicalOff != null && (
                <> Checked against {overall.n_obs} past 30-day period{overall.n_obs === 1 ? '' : 's'},
                  it was typically off by about <b>{num(typicalOff)} units</b>
                  {typicalPct != null && <> (roughly {typicalPct}% of a typical month)</>}.</>
              )}
            </p>
          </div>
        ) : (
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
        )}
      </div>

      {/* The items are a breakdown, not the parts of the total: say so, with
          both numbers, rather than leave a reader to add the column up and
          conclude one of them is wrong.
          Three cases, not two: contributors can be empty either because the
          category has no Fast item (n_fast === 0) or because it does but the
          per-item forecast step hasn't run (n_fast > 0) - step4c runs on its
          own and does not wait for step4, so that second case is a real,
          reachable state, not a hypothetical. Saying "none is Fast-moving"
          in that case would be false. */}
      {wholeCategory && (
        <div className="notice notice--info" style={{ marginTop: 14 }}>
          {fd.contributors.length > 0 ? (
            <>The {num(fd.n_forecast)} Fast-moving item{fd.n_forecast === 1 ? '' : 's'} below
              {' '}{fd.n_forecast === 1 ? 'is' : 'are'} each forecast on {fd.n_forecast === 1 ? 'its' : 'their'} own,
              so {fd.n_forecast === 1 ? 'it adds' : 'they add'} up to{' '}
              <b>{num(Math.round(fd.contributors_total_30d))} units</b>, not the{' '}
              {num(Math.round(fd.total_30d))} above. Use the category figure to plan the
              category as a whole; use the items to see which ones drive it.</>
          ) : fd.n_fast > 0 ? (
            <>{category}&rsquo;s Fast-moving items don&rsquo;t have their own forecast yet —
              run the pipeline's item forecast step to see them listed here. The figure
              above is still the forecast to use for this category.</>
          ) : (
            <>None of the items in {category} is Fast-moving, so none has a forecast of its
              own. The figure above is the forecast to use for this category.</>
          )}
        </div>
      )}

      {fd.contributors.length > 0 && (
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
                    {/* No real "still stocked" flag exists in the data (see
                        backend/app.py's DISCONTINUED_DAYS) — this marks an item
                        with no sale in over a year so it isn't mistaken for a
                        normal Fast mover just because it is sitting at 0. */}
                    {r.likely_discontinued && (
                      <span className="tag tag--warn" style={{ marginLeft: 8 }}
                            title={`No sale since ${r.last_sale_date ?? 'sales records began'} — may no longer be stocked`}>
                        Not currently stocked?
                      </span>
                    )}
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
      )}
    </div>
  );
}

function ForecastPanel({ productId, itemName, forecastMeta }) {
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
        {/* Without a forecast there is no month to predict, but the real
            history is still worth showing on its own. */}
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Observed Monthly Units — {itemName}</span>
            <span className="hint">actual tallied history · no fitted line, no projection</span>
          </div>
          <HistoryChart productId={productId} />
        </div>
      </>
    );
  }

  // Forecast data is available — render it
  const fd = forecast.data;
  const total30 = fd.forecast.reduce((a, r) => a + (r.yhat || 0), 0);
  const shaped = /_shape$/.test(fd.model_type ?? '');

  return (
    <>
      <ForecastCard key={productId} title={fd.item_name} fd={fd}
                    tags={<ReliabilityTag metrics={fd.metrics} isHeuristic={fd.is_heuristic} />}>
        {/* Same reasoning as the category note: a shaped line draws ups and downs
            that are not predicted spikes. An item's own days are too sparse to
            carry a pattern, so it borrows its category's. */}
        {shaped && (
          <p className="hint" style={{ textAlign: 'center', margin: '8px 0 0' }}>
            The 30-day total blends this item&rsquo;s recent sales with its category&rsquo;s 6-month
            level{isCalendarAdjusted(fd.model_type) && ', lowered when the school calendar shows quieter days ahead'}.
            The day-to-day pattern is the category&rsquo;s (weekdays, store closures, the
            school calendar) — one-off bulk orders can&rsquo;t be predicted from dates.
          </p>
        )}

        {/* An item that has stopped selling is forecast near zero, and 0 on its
            own reads like a bug. `likely_discontinued` (no real sale in over a
            year — backend/app.py's DISCONTINUED_DAYS) gets the stronger, more
            specific notice; a merely-quiet item still gets the softer one. */}
        {fd.likely_discontinued ? (
          <div className="notice notice--warn" style={{ marginTop: 12 }}>
            This item hasn&rsquo;t sold since{' '}
            {fd.last_sale_date ? usDate(fd.last_sale_date) : 'sales records began'} and may no
            longer be stocked. There is no "discontinued" flag in the data yet — this is a guess
            from a year of no sales — so check with the store before ordering off this forecast.
          </div>
        ) : total30 < 0.5 && (
          <div className="notice notice--info" style={{ marginTop: 12 }}>
            This item hasn&rsquo;t sold recently, so the forecast is close to zero. It will
            rise again if sales pick up.
          </div>
        )}

        {fd.is_heuristic && (
          <div className="notice notice--warn" style={{ marginTop: 12 }}>
            Not enough sales history yet to double-check this forecast — treat it as a rough estimate.
          </div>
        )}
      </ForecastCard>
    </>
  );
}

/**
 * The one forecast chart: a compact grey monthly overview of past demand running
 * into the next 30 days' forecast in full daily detail, on a timeline that
 * opens on the forecast and scrolls left through the history (see
 * ScrollForecastChart).
 *
 * `fd.history` comes from the same response as the forecast and covers the
 * same items (for the category model, every item in the category; for the
 * sum-of-items fallback, only the Fast items forecast), so the history and the
 * forecast are totals of the same thing. `scope` names that when it is
 * narrower than the whole category.
 *
 * Callers key this on the item/category, so switching either reopens the
 * chart on the forecast instead of wherever the last one was scrolled to.
 */
function ForecastCard({ title, fd, scope, tags, children }) {
  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Demand Forecast — {title}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span className="tag tag--gold" title={fd.model_type}>{modelLabel(fd.model_type)}</span>
          {tags}
          <span className="hint">
            {fd.history_end
              ? `Based on sales through ${usDate(fd.history_end)}`
              : `Generated ${usDate(fd.snapshot_date)}`}
          </span>
        </div>
      </div>
      {scope && <div className="hint" style={{ marginTop: -6, marginBottom: 6 }}>Totals cover {scope}.</div>}

      <ScrollForecastChart history={fd.history} forecast={fd.forecast} />
      <div className="legend" style={{ justifyContent: 'center', marginTop: 10 }}>
        <span><i style={{ background: 'var(--text-2)', opacity: 0.7 }} />Past months · average sold per day</span>
        <span><i style={{ background: 'var(--accent)' }} />Forecast per day</span>
        <span><i style={{ background: 'var(--accent)', opacity: 0.25 }} />Likely range</span>
      </div>

      {children}
    </div>
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
