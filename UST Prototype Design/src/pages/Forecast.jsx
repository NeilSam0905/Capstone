import { useState, useMemo } from 'react';
import {
  getProducts, getForecast, getProductForecast, getProductHistory,
  getCategoryForecast, getCategoryForecasts,
} from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart, ScrollForecastChart } from '../components/charts';
import { num, shortMonth, longMonth, monthName, usDate, isCalendarAdjusted, FSN_TONE, FSN_LABEL } from '../lib/format';
import SearchSelect from '../components/SearchSelect';
import Icon from '../components/Icon';
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
export default function Forecast({ filters, setPage }) {
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

  const itemOptions = useMemo(() => [
    { value: ALL_ITEMS, label: 'All Items in Category' },
    ...itemsInCategory.map(p => ({
      value: p.product_id,
      label: `${p.item_name}${showSupplier ? ` — ${p.supplier_name}` : ''} (${num(p.total_units)} units)`,
    })),
  ], [itemsInCategory, showSupplier]);

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
              {/* Searchable: a category can hold dozens of items. "All Items"
                  names the scope, not the category: the category select beside
                  it already says which one. */}
              <SearchSelect
                id="fc-item"
                value={selectedId}
                onChange={setSelectedId}
                minWidth={300}
                placeholder="Search items…"
                options={itemOptions}
              />
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
                         forecastMeta={forecastMeta} setPage={setPage} />
        : <CategoryForecastPanel category={activeCategory} onPickItem={setSelectedId} setPage={setPage} />}
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
function CategoryForecastPanel({ category, onPickItem, setPage }) {
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
      <ForecastCard key={category} title={category} fd={fd} onPickItem={onPickItem} setPage={setPage}
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
              Forecast from all of this category&rsquo;s sales taken together,
              so it includes slow and non-moving items too.
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
        <div className="notice notice--info" style={{ marginTop: 14, marginBottom: 14 }}>
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

function ForecastPanel({ productId, itemName, forecastMeta, setPage }) {
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
      <ForecastCard key={productId} title={fd.item_name} fd={fd} setPage={setPage}
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
function ForecastCard({ title, fd, scope, tags, children, onPickItem, setPage }) {
  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Demand Forecast — {title}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {tags}
          <span className="hint" title={fd.history_end ? `Sales through ${usDate(fd.history_end)}` : undefined}>
            {fd.history_end
              ? `Based on ${monthName(fd.history_end)} Sales`
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

      <ForecastGuidance g={fd.guidance} title={title} onPickItem={onPickItem} setPage={setPage} />
    </div>
  );
}

/* ------------------------------------------------------------ guidance */

/** '2026-04' (a count month) or '2026-04-30' -> how a person says it. */
const asOf = v => (/^\d{4}-\d{2}$/.test(v ?? '') ? longMonth(v) : usDate(v));

const perDay = n => (n < 10 ? n.toFixed(1) : num(n));

const span = r => (r.start === r.end ? usDate(r.start) : `${usDate(r.start)} – ${usDate(r.end)}`);

const plural = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

/** The action box's tone, by what it asks of the reader. */
const ACTION_TONE = {
  reorder_now: 'crit', reorder_items: 'crit', short: 'crit',
  order_by: 'warn', watch: 'warn', count_stock: 'info',
  covered: 'ok', no_demand: 'ok',
};

/**
 * The chart, read out for whoever orders stock: how much to expect, against
 * what has been selling, when the busy days are, what the calendar holds —
 * and then the one thing to do about it.
 *
 * Every number comes from the API's `guidance` block (backend/app.py
 * _forecast_guidance), which derives it from the forecast, Result_Prescriptive
 * and the stock counts. Nothing is calculated here but the wording, so this
 * page and Reorder Alerts cannot suggest different quantities for an item.
 */
function ForecastGuidance({ g, title, onPickItem, setPage }) {
  if (!g) return null;
  const e = g.expected;
  const change = g.change_pct;

  return (
    <div className="fguide">
      <div className="fguide__h">
        <span className="section-h">What this means</span>
        <span className="hint">{g.scope === 'item' ? 'for this item' : `for ${title}`}</span>
      </div>

      {g.window.passed && (
        <div className="notice notice--warn" style={{ marginBottom: 12 }}>
          This forecast is for {usDate(g.window.start)} – {usDate(g.window.end)}, which has already
          passed: the latest tally on record is the day before it starts. Add the newest tally sheets
          and run the pipeline for an up-to-date forecast. Until then, the advice below applies the
          forecast&rsquo;s daily rate to today&rsquo;s stock.
        </div>
      )}

      <ul className="fguide__list">
        <li>
          <span className="fguide__icon"><Icon name="trend" size={15} /></span>
          <span>
            Expect about <b>{num(e.total)} units</b> over {g.window.days} days — most likely
            between {num(e.low)} and {num(e.high)}. That is about {perDay(e.per_day)} a day
            {g.recent && change != null && (
              Math.abs(change) < 10
                ? <>, about the same as {longMonth(g.recent.month)} ({perDay(g.recent.per_day)} a day).</>
                : <>, <b>{Math.abs(change)}% {change > 0 ? 'more' : 'less'}</b> than{' '}
                    {longMonth(g.recent.month)} ({perDay(g.recent.per_day)} a day).</>
            )}
            {!(g.recent && change != null) && '.'}
          </span>
        </li>

        {g.busiest_week && (
          <li>
            <span className="fguide__icon"><Icon name="zap" size={15} /></span>
            {/* A week in a window that has passed is history, not something to
                stock up for. */}
            <span>
              {g.window.passed ? 'In this forecast the busiest week was' : 'The busiest week is'}{' '}
              <b>{span(g.busiest_week)}</b>, with about {num(g.busiest_week.units)} units
              ({Math.round(g.busiest_week.share * 100)}% of the period).
              {!g.window.passed && ' Have stock on the shelf before it starts.'}
            </span>
          </li>
        )}

        <li>
          <span className="fguide__icon"><Icon name="cal" size={15} /></span>
          <CalendarNote cal={g.calendar} />
        </li>
      </ul>

      <div className={`notice notice--${ACTION_TONE[g.action] ?? 'info'} fguide__action`}>
        <div className="fguide__action-h">What to do</div>
        {g.scope === 'item'
          ? <ItemAction g={g} />
          : <CategoryAction g={g} title={title} onPickItem={onPickItem} />}
        {setPage && ['reorder_now', 'reorder_items', 'short', 'order_by'].includes(g.action) && (
          <div className="btn-row" style={{ marginTop: 10 }}>
            <button className="btn btn--ghost btn--sm" onClick={() => setPage('reorder')}>
              Open Reorder Alerts <Icon name="arrow" size={12} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** The next 30 days of the school calendar, as what each part means for sales. */
function CalendarNote({ cal }) {
  const parts = [
    ...cal.enrollment.map(r => <>Enrollment <b>{span(r)}</b> — the busiest sales window; stock Fast-moving items before it starts.</>),
    ...cal.exams.map(r => <>Exams <b>{span(r)}</b> — fewer shoppers; non-urgent restocking can wait until after.</>),
    ...cal.sem_break.map(r => <>Semester break <b>{span(r)}</b> — campus is quiet, expect low sales.</>),
    ...cal.events.map(ev => <><b>{ev.name}</b> on {usDate(ev.date)} — check stock of the items it draws on.</>),
  ];
  if (cal.closed_days > 0) parts.push(<>The store is closed on {plural(cal.closed_days, 'day')}.</>);

  const when = `${usDate(cal.start)} – ${usDate(cal.end)}`;
  if (parts.length === 0) {
    return <span>No enrollment, exams, semester break or logged events between {when}.</span>;
  }
  return (
    <span>
      Between {when}:
      <ul className="fguide__sub">
        {parts.map((p, i) => <li key={i}>{p}</li>)}
      </ul>
    </span>
  );
}

function ItemAction({ g }) {
  const s = g.stock;
  const e = g.expected;
  const counted = s.stock_as_of ? <> (last counted {asOf(s.stock_as_of)})</> : null;
  const onHand = <><b>{num(s.current_stock ?? 0)}</b> on hand{counted}</>;

  switch (g.action) {
    case 'reorder_now':
      return (
        <p>
          <b>Reorder now.</b> {onHand} is at or below the reorder point of{' '}
          {num(Math.round(s.reorder_point))}
          {s.current_stock > 0 && s.days_cover != null && <>, enough for only about {perDay(s.days_cover)} days</>}.
          Order about <b>{num(s.suggested_order_qty)} units</b> — enough to cover the supplier&rsquo;s
          lead time plus the next 30 days.
        </p>
      );
    case 'order_by':
      return (
        <p>
          <b>Place the next order by {usDate(s.order_by)}.</b> {onHand} lasts about{' '}
          {perDay(s.days_cover)} days at the forecast rate (until around {usDate(s.runs_out_on)}).
          Ordering when it reaches {num(Math.round(s.reorder_point))} leaves enough to sell while the
          order arrives.
        </p>
      );
    case 'short':
      return (
        <p>
          <b>Stock won&rsquo;t last the period.</b> {onHand} runs out around{' '}
          {usDate(s.runs_out_on)} at the forecast rate. Order about <b>{num(s.to_cover_30d)} units</b>{' '}
          to cover the next 30 days ({num(s.to_cover_30d_high)} if it turns out busy).
        </p>
      );
    case 'watch':
      return (
        <p>
          <b>Enough for now — keep an eye on it.</b> {onHand} covers the expected{' '}
          {num(e.total)} units, but a busy month could need up to {num(e.high)}: about{' '}
          {num(s.to_cover_30d_high)} more.
        </p>
      );
    case 'covered':
      return (
        <p>
          <b>No order needed.</b> {onHand} covers the next 30 days even at the high end
          ({num(e.high)} units).
        </p>
      );
    case 'count_stock':
      return (
        <p>
          <b>Count this item first.</b> It has no stock count, so there is no way to tell whether it
          needs ordering. To cover the next 30 days the shelf needs about <b>{num(e.total)} units</b>{' '}
          ({num(e.high)} for a busy month) — record a count in the Tally Interface to get a
          recommendation.
        </p>
      );
    default:
      return <p><b>No order needed.</b> This item is forecast to sell almost nothing in the next 30 days.</p>;
  }
}

function CategoryAction({ g, title, onPickItem }) {
  const s = g.stock;
  const uncounted = s.items_total - s.items_counted;
  const coverage = (
    <span className="hint fguide__coverage">
      Stock is counted for {num(s.items_counted)} of {plural(s.items_total, 'item')} in {title}
      {uncounted > 0 && '; the rest cannot be checked until they are counted'}.
    </span>
  );

  if (g.action === 'reorder_items') {
    const more = s.reorder_now_total - s.reorder_now.length;
    return (
      <>
        <p>
          <b>Reorder {plural(s.reorder_now_total, 'item')} in {title}</b> — about{' '}
          <b>{num(s.reorder_units_total)} units</b> in total. {s.reorder_now_total === 1 ? 'It is' : 'They are'} at
          or below the reorder point:
        </p>
        <div className="tbl__scroll" style={{ marginTop: 8 }}>
          <table className="tbl fguide__tbl">
            <thead>
              <tr><th>Item</th><th>Supplier</th><th className="num">On hand</th><th className="num">Order</th></tr>
            </thead>
            <tbody>
              {s.reorder_now.map(r => (
                <tr key={r.product_id} className={onPickItem ? 'is-clickable' : undefined}
                    onClick={onPickItem ? () => onPickItem(r.product_id) : undefined}
                    title={onPickItem ? 'Show this item on its own' : undefined}>
                  <td className="strong"><span className="cell-trunc" style={{ '--trunc': '260px' }}>{r.item_name}</span></td>
                  <td>{r.supplier_name ?? '—'}</td>
                  <td className="num">{num(r.current_stock)}</td>
                  <td className="num"><b>{num(r.suggested_order_qty)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {more > 0 && <div className="hint" style={{ marginTop: 6 }}>…and {plural(more, 'more item')} in Reorder Alerts.</div>}
        {coverage}
      </>
    );
  }
  if (g.action === 'count_stock') {
    return (
      <p>
        <b>Count the stock first.</b> No item in {title} has a stock count, so this forecast cannot be
        checked against what is on the shelf. Start with the Fast-moving items.
      </p>
    );
  }
  if (g.action === 'covered') {
    return (
      <>
        <p><b>No order needed right now.</b> None of the counted items in {title} is at its reorder point.</p>
        {coverage}
      </>
    );
  }
  return <p><b>No order needed.</b> {title} is forecast to sell almost nothing in the next 30 days.</p>;
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
