import { useState, useMemo } from 'react';
import {
  getProducts, getForecast, getProductForecast, getProductHistory,
  getCategoryForecast, getCategoryForecasts, getOverviewPerformance,
  getForecastModels,
} from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart, MARK_KINDS, ScrollForecastChart } from '../components/charts';
import { num, shortMonth, longMonth, monthName, usDate, isCalendarAdjusted, roundNice, pesoK, FSN_TONE, FSN_LABEL } from '../lib/format';
import SearchSelect from '../components/SearchSelect';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
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
  // Which model's forecast is shown, per scope; null is the recommended one,
  // the forecast the rest of the dashboard uses (ModelSelect).
  const [shown, setShown] = useState({ item: null, category: null });
  const showModel = scope => model => setShown(s => ({ ...s, [scope]: model }));

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
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                {/* The app's own dropdown, like the Item picker beside it.
                    Re-picking the category already shown keeps the item:
                    unlike a native <select>, this reports every pick. */}
                <SearchSelect
                  id="fc-category"
                  value={activeCategory}
                  onChange={c => { if (c !== activeCategory) { setCategory(c); setSelectedId(ALL_ITEMS); } }}
                  minWidth={210}
                  searchable={categories.length > 10}
                  placeholder="Search categories…"
                  options={categories.map(c => ({ value: c, label: c }))}
                />
                <CategoryContents category={activeCategory} products={products} supplier={filters.supplier} />
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
                         forecastMeta={forecastMeta} setPage={setPage}
                         model={shown.item} onModel={showModel('item')} />
        : <CategoryForecastPanel category={activeCategory} onPickItem={setSelectedId} setPage={setPage}
                                 model={shown.category} onModel={showModel('category')} />}

      <DailySales filters={filters} />
    </div>
  );
}

/** '2026-07-31' -> 'Jul 31', short enough for a daily axis. */
const dayLabel = iso => `${shortMonth(iso.slice(0, 7)).split(' ')[0]} ${+iso.slice(8, 10)}`;

/**
 * The store's sales in pesos, day by day over the last 90 tallied days - the
 * daily view the second consultation asked for. It lives here, with the
 * forecasts, rather than on the Overview: it is for exploring how sales move,
 * not a decision, and most of its ₱0 days are days with no sales recorded
 * (Sundays, and days the old tally sheets skipped) rather than days nothing
 * sold. The note under the chart says so.
 */
function DailySales({ filters }) {
  const { data: perf } = useData(() => getOverviewPerformance(filters), [filters.supplier]);
  if (!perf?.available) return null;
  const daily = perf.daily.map(d => ({ label: dayLabel(d.date), value: d.php }));
  const recorded = perf.daily.filter(d => d.php > 0).length;

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Daily Sales, Last 90 Days</span>
        <span className="hint">
          Pesos per day, to {usDate(perf.history_end)}
          {filters.supplier !== ALL_SUPPLIERS ? ` · ${filters.supplier} only` : ' · whole store'}
        </span>
      </div>
      <LineChart data={daily} tickFmt={pesoK} unit="" />
      <div className="hint" style={{ marginTop: 8 }}>
        Sales were recorded on {num(recorded)} of these {num(daily.length)} days. A day at ₱0 usually means
        no sales were recorded that day (Sundays, and days the tally sheets skipped), not that nothing sold.
      </div>
    </div>
  );
}

/**
 * What a category holds, behind the (i) beside the Category select: how many
 * items in each FSN group, and the items themselves, best sellers first.
 *
 * The category forecast is built from the combined sales of every item in it,
 * so this is the answer to "what does this number include". With a supplier
 * picked in the topbar only that supplier's items are listed, while the
 * forecast still covers every supplier - the panel says so.
 */
function CategoryContents({ category, products, supplier }) {
  const items = products
    .filter(p => (p.category || 'Uncategorised') === category)
    .sort((a, b) => (b.total_units || 0) - (a.total_units || 0));
  const counts = { F: 0, S: 0, N: 0 };
  items.forEach(p => { if (counts[p.fsn_class] !== undefined) counts[p.fsn_class]++; });

  return (
    <InfoTip label={`What is included in ${category}`} width={430}>
      <div className="infotip__h">What&rsquo;s in {category}</div>
      <p>
        <b>{num(items.length)} {items.length === 1 ? 'item' : 'items'}</b>: {num(counts.F)} fast-moving,{' '}
        {num(counts.S)} slow-moving and {num(counts.N)} non-moving. The category forecast is built from the
        combined sales of all of them, so slow-moving items count too; non-moving items have no sales, so
        they add nothing.
      </p>
      {supplier !== ALL_SUPPLIERS && (
        <p className="hint">Only {supplier}&rsquo;s items are listed; the forecast covers every supplier.</p>
      )}
      <div className="infotip__list-head">
        <span>Item</span><span>Class</span><span>Units</span>
      </div>
      <ul className="infotip__list">
        {items.map(p => (
          <li key={p.product_id}>
            <span className="infotip__list-name" title={p.item_name}>{p.item_name}</span>
            <span className={`tag tag--${FSN_TONE[p.fsn_class] || 'info'}`}>{FSN_LABEL[p.fsn_class] || '—'}</span>
            <span className="infotip__list-num">{num(p.total_units || 0)}</span>
          </li>
        ))}
      </ul>
    </InfoTip>
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
function CategoryForecastPanel({ category, onPickItem, setPage, model, onModel }) {
  const { data: forecast, loading } = useData(
    () => getCategoryForecast(category, model), [category, model], null,
    { key: `forecast:category:${category}:${model ?? ''}` }
  );

  if (loading) return <Loading label="Loading category forecast…" />;

  if (!forecast?.available) {
    return (
      <>
        <Pending title={`No forecast for ${category}`} reason={forecast?.reason} />
        {model && <BackToRecommended onModel={onModel} />}
      </>
    );
  }

  const fd = forecast.data;
  const wholeCategory = fd.source === 'category_model';

  return (
    <>
      <ForecastCard key={category} title={category} fd={fd} setPage={setPage}
                    modelScope={wholeCategory ? 'category' : 'item'}
                    model={wholeCategory ? model : null} onModel={wholeCategory ? onModel : null}
                    tags={wholeCategory && <Reliability fd={fd} model={model} />}
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

  // The likely range, as a range rather than one number: the guidance block's
  // low/high (backend _forecast_guidance), which for the category model is the
  // 30-day total plus or minus its walk-forward RMSE - step4c spreads that
  // RMSE across the days in proportion to each day's share, so the summed
  // daily bounds are exactly total +/- RMSE. Shown for the category model
  // only: the sum-of-items fallback adds up item bands built from one day's
  // spread, which overstates a 30-day range many times over.
  const expected = fd.guidance?.expected;
  const ranged = wholeCategory && expected && expected.high > expected.low;
  const halfWidth = ranged ? Math.round((expected.high - expected.low) / 2) : null;

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
        {/* Rounded to how precisely a 30-day forecast is really known
            (roundNice): 1,898 reads as 1,900, the best estimate included. */}
        {ranged ? (
          <div className="ftotal__figure ftotal__figure--range">
            <div className="ftotal__val">
              {num(roundNice(expected.low))}
              <span className="ftotal__dash">–</span>
              {num(roundNice(expected.high))}
            </div>
            <div className="ftotal__unit">
              units likely · best estimate <b>{num(roundNice(fd.total_30d))}</b>
            </div>
          </div>
        ) : (
          <div className="ftotal__figure">
            <div className="ftotal__val">{num(roundNice(fd.total_30d))}</div>
            <div className="ftotal__unit">units forecast</div>
          </div>
        )}

        {wholeCategory ? (
          <div className="ftotal__coverage">
            <div className="ftotal__coverage-head">
              <span className="ftotal__coverage-lbl">Covers</span>
              <b>all {num(fd.n_products)} <span>items in</span> {category}</b>
            </div>
            {ranged && (
              <p className="ftotal__note">
                Best estimate ± <b>{num(roundNice(halfWidth))} units</b>, its usual miss in past months.
              </p>
            )}
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
              {partial
                ? <>Only the {fd.n_forecast} Fast-moving item{fd.n_forecast === 1 ? '' : 's'} below are included.</>
                : <>Every item in {category} is included.</>}
            </p>
          </div>
        )}
      </div>

      {fd.contributors.length > 0 && (
      <div className="tbl__scroll">
        <table className="tbl" style={{ minWidth: 820 }}>
          <thead>
            <tr>
              <th>Item</th>
              <th>Category</th>
              <th>Supplier</th>
              <th className="num"
                  title="Best estimate plus or minus the item's usual miss (RMSE) over its past 30-day checks">
                Likely range
              </th>
              <th className="num" colSpan={2}>30-day forecast</th>
            </tr>
          </thead>
          <tbody>
            {fd.contributors.map(r => {
              const units = roundNice(r.yhat_30d || 0);
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
                  {/* Every row here is from the category picked above; named
                      on each row so a copied-out table still says which. */}
                  <td>{r.category ?? category}</td>
                  <td>{r.supplier_name}</td>
                  {/* The item's own likely range - the best estimate plus or
                      minus its walk-forward RMSE (backend _range_30d), the
                      same rule as the category figure above and the same
                      range the item's own page quotes. A dash when the item
                      has no checks to size it from. */}
                  <td className="num frange">
                    {r.range_30d
                      ? <span title={`${num(units)} ± ${num(roundNice(r.range_30d.rmse))} units, the item's usual miss in past months`}>
                          {num(roundNice(r.range_30d.low))}<span className="frange__dash">–</span>{num(roundNice(r.range_30d.high))}
                        </span>
                      : <span className="nodata">—</span>}
                  </td>
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

function ForecastPanel({ productId, itemName, forecastMeta, setPage, model, onModel }) {
  const { data: forecast, loading } = useData(
    () => getProductForecast(productId, model), [productId, model], null,
    { key: `forecast:${productId}:${model ?? ''}` }
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
        {model && <BackToRecommended onModel={onModel} />}
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
  const shaped = /_shape$/.test(fd.model_type ?? '');

  return (
    <>
      <ForecastCard key={productId} title={fd.item_name} fd={fd} setPage={setPage}
                    modelScope="item" model={model} onModel={onModel} tags={<Reliability fd={fd} model={model} />}>
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
        ) : null}

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
function ForecastCard({ title, fd, scope, modelScope, model, onModel, tags, children, setPage }) {
  return (
    <div className="card card__pad">
      {/* Left: the title, and under it the model that made the figures (a
          dropdown to choose another). Right: which month's sales they rest on,
          and under it whether the forecast held up. */}
      <div className="card-h" style={{ alignItems: 'flex-start' }}>
        <div className="fcard__head">
          <span className="section-h">Demand Forecast — {title}</span>
          {onModel && (
            <div className="fcard__model">
              <ModelSelect scope={modelScope} value={model} onChange={onModel} />
            </div>
          )}
        </div>
        <div className="fcard__side">
          <span className="hint" title={fd.history_end ? `Sales through ${usDate(fd.history_end)}` : undefined}>
            {fd.history_end
              ? `Based on ${monthName(fd.history_end)} Sales`
              : `Generated ${usDate(fd.snapshot_date)}`}
          </span>
          {tags}
        </div>
      </div>
      {scope && <div className="hint" style={{ marginTop: -6, marginBottom: 6 }}>Totals cover {scope}.</div>}

      <ScrollForecastChart history={fd.history} forecast={fd.forecast} marks={fd.calendar_marks} />
      <div className="legend" style={{ justifyContent: 'center', marginTop: 10 }}>
        <span><i style={{ background: 'var(--text-2)', opacity: 0.7 }} />Past months · average sold per day</span>
        <span><i style={{ background: 'var(--accent)' }} />Forecast per day</span>
        <span><i style={{ background: 'var(--accent)', opacity: 0.25 }} />Likely range</span>
        {/* Only the kinds in this window; hover a day for its name. */}
        {[...new Set((fd.calendar_marks ?? []).map(m => m.kind))].map(kind => (
          <span key={kind} title="Hover the day on the chart for its name">
            <i className="legend__mark" style={{ background: MARK_KINDS[kind]?.color }} />
            {MARK_KINDS[kind]?.label}
          </span>
        ))}
      </div>

      {children}

      <ForecastGuidance g={fd.guidance} title={title} setPage={setPage} />
    </div>
  );
}

/* ------------------------------------------------------------ guidance */

const span = r => (r.start === r.end ? usDate(r.start) : `${usDate(r.start)} – ${usDate(r.end)}`);

/** The one calendar fact worth a sentence in the next 30 days, busiest kind
 *  first: enrollment is a rush, exams and the break are quiet, an event is
 *  worth a look. None of them -> nothing to say. */
function calendarSentence(cal) {
  if (!cal) return null;
  if (cal.enrollment?.length) return <> Enrollment runs <b>{span(cal.enrollment[0])}</b>, so expect a rush.</>;
  if (cal.exams?.length) return <> Exams run <b>{span(cal.exams[0])}</b>, so expect fewer shoppers.</>;
  if (cal.sem_break?.length) return <> The semester break runs <b>{span(cal.sem_break[0])}</b>, so expect a quiet stretch.</>;
  if (cal.events?.length) return <> <b>{cal.events[0].name}</b> is on {usDate(cal.events[0].date)}.</>;
  return null;
}

/**
 * The chart, read out in a few plain sentences: how much to expect over the
 * next 30 days and how that compares with last month, the busy week and the
 * one calendar fact that matters, then what to do - check the expected
 * demand, and restock and reorder from Reorder Alerts.
 *
 * The sentences sit behind the (i) beside "What this means", so the page
 * shows the chart and the action and a reader opens the reading only when
 * they want it. The passed-window warning stays in view: it changes how
 * every figure on the page should be read.
 *
 * Every number comes from the API's `guidance` block (backend/app.py
 * _forecast_guidance); nothing is calculated here but the wording. Figures
 * are rounded as the Expected Demand card rounds them (roundNice).
 */
function ForecastGuidance({ g, title, setPage }) {
  if (!g) return null;
  const e = g.expected;
  const change = g.change_pct;
  const item = g.scope === 'item';
  const compared = g.recent && change != null
    ? (Math.abs(change) < 10
      ? <>, about the same as {longMonth(g.recent.month)}</>
      : <>, <b>{Math.abs(change)}% {change > 0 ? 'more' : 'less'}</b> than {longMonth(g.recent.month)}</>)
    : null;

  return (
    <div className="fguide">
      {g.window.passed && (
        <div className="notice notice--warn" style={{ marginBottom: 12 }}>
          These 30 days have already passed. Add the newest tally sheets and choose Run Forecast in the
          Tally Interface for a current forecast.
        </div>
      )}

      <div className="fguide__bar">
        <span className="fguide__label">
          <span className="section-h">What this means</span>
          <InfoTip label="What this forecast means" width={400}>
            <p>
              {item ? 'This item' : title} should sell about <b>{num(roundNice(e.total))} units</b> over
              the next {g.window.days} days (likely {num(roundNice(e.low))}–{num(roundNice(e.high))})
              {compared}.
              {g.busiest_week && !g.window.passed && <> The busiest week is <b>{span(g.busiest_week)}</b>.</>}
              {calendarSentence(g.calendar)}
            </p>
            <p>
              <b>What to do:</b> check the expected demand for the items to restock, and reorder them.
            </p>
          </InfoTip>
        </span>
        {setPage && (
          <button className="btn btn--ink btn--sm" onClick={() => setPage('reorder')}>
            Open Reorder Alerts <Icon name="arrow" size={12} />
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Whether the forecast held up when it was checked against real sales: a tag
 * with the verdict, and behind the (i) beside it the reason in a sentence and
 * how every model did on the same check, worded for a store reader.
 *
 * The verdict is the walk-forward check's: did the model miss by less than
 * simply repeating the last 30 days (beats_naive_mae)? The table under it is
 * every offered model's check for this item or category (the API's
 * model_metrics, from the main and comparison metrics tables), ranked by
 * MASE, with the model on screen marked - so the reader sees whether another
 * model would have done better here.
 *
 * MAPE is shown, with a caution: on this dataset a 30-day period that sold
 * very little turns a small miss into a huge percentage, so it can read 100%+
 * for a forecast that is doing well (docs/DEGENERATE_FORECAST.md). That is
 * why the ranking is by MASE.
 */
function Reliability({ fd, model }) {
  if (fd.is_heuristic) {
    return (
      <span className="tag tag--warn"
            title="This hasn't sold long enough to check the forecast against real results.">
        Not enough history
      </span>
    );
  }
  const m = fd.metrics?.find(r => r.period_scope === 'overall') ?? fd.metrics?.[0];
  if (!m || m.mae == null) {
    return (
      <span className="tag tag--info" title="No accuracy check has been recorded for this forecast yet.">
        Not checked
      </span>
    );
  }
  const reliable = !!m.beats_naive_mae;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      <span className={`tag tag--${reliable ? 'ok' : 'warn'}`}>{reliable ? 'Reliable' : 'Rough estimate'}</span>
      <InfoTip label="How reliable is this forecast?" width={540}>
        <ReliabilityDetail m={m} reliable={reliable} model={model} fd={fd} />
      </InfoTip>
    </span>
  );
}

const units = v => `${num(Math.round(v))} unit${Math.round(v) === 1 ? '' : 's'}`;
const pctFmt = v => `${num(Math.round(v))}%`;

/** Every model's check for this item or category (the API's model_metrics),
 *  lowest scaled miss first; models with no figures (not made yet, or too
 *  little history to check) at the end, in the offered order. Ranked by MASE
 *  rather than MAPE because the percentage swells on a period that sold very
 *  little. */
function rankModels(rows) {
  const scored = rows.filter(r => r.mase != null).sort((a, b) => a.mase - b.mase);
  return [...scored, ...rows.filter(r => r.mase == null)];
}

function ReliabilityDetail({ m, reliable, model, fd }) {
  const periods = `${num(m.n_obs)} past 30-day period${m.n_obs === 1 ? '' : 's'}`;
  const ofMonth = m.mean_actual_30d > 0 ? Math.round(m.mae / m.mean_actual_30d * 100) : null;
  const rows = rankModels(fd.model_metrics ?? []);
  const shownId = model ?? rows.find(r => r.recommended)?.id;
  const bestId = rows[0]?.mase != null ? rows[0].id : null;
  const mapeSwells = rows.some(r => (r.mape ?? 0) > 100);
  const missing = rows.filter(r => !r.made).length;
  return (
    <>
      <div className="infotip__h">What &ldquo;{reliable ? 'Reliable' : 'Rough estimate'}&rdquo; means</div>
      <p>
        To test a forecast, the system goes back in time: it forecasts past months as if it did not know
        their sales yet, then compares each guess with what really sold.
      </p>
      <p>
        {reliable
          ? <>This forecast <b>did better</b> than simply assuming the next 30 days will sell like the last 30,
            so it is a useful guide.</>
          : <>This forecast <b>did no better</b> than simply assuming the next 30 days will sell like the
            last 30. Treat its number as a ballpark, not an exact amount, and check it against what you
            know, such as upcoming events or bulk orders.</>}
      </p>
      {!reliable && (
        <p>
          This is common at the USTore: sales are uneven, with many quiet days and then a big order all at
          once, which no forecast can see coming unless the order is known in advance.
        </p>
      )}
      <p>
        Checked against {periods} of real sales, it missed by about <b>{units(m.mae)}</b> on average
        {m.naive_mae != null && <> (repeating the last 30 days missed by {units(m.naive_mae)})</>}.
        {ofMonth != null && <> That is about {ofMonth}% of a typical month ({units(m.mean_actual_30d)}).</>}
      </p>

      {rows.length > 0 && (
        <>
          <div className="infotip__h" style={{ marginTop: 12 }}>How the other models did on the same test</div>
          <table className="reliab">
            <colgroup><col /><col className="reliab__fig" /><col className="reliab__fig" /></colgroup>
            <thead>
              <tr>
                <th>Model</th>
                <th className="num" title="The miss as a share of what actually sold">
                  Percent error<br /><span className="reliab__code">MAPE</span>
                </th>
                <th className="num" title="The miss measured against a simple day-to-day guess">
                  Scaled miss<br /><span className="reliab__code">MASE</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className={[r.id === shownId && 'is-shown', !r.made && 'is-missing']
                  .filter(Boolean).join(' ') || undefined}>
                  <td>
                    <div className="reliab__name">{r.label}</div>
                    {(r.id === shownId || r.id === bestId || r.recommended) && (
                      <div className="reliab__tags">
                        {r.id === shownId && <span className="tag tag--info">Shown</span>}
                        {r.id === bestId && <span className="tag tag--ok">Lowest error</span>}
                        {r.recommended && <span className="tag">Recommended</span>}
                      </div>
                    )}
                  </td>
                  <td className="num">{r.mape != null ? pctFmt(r.mape) : '—'}</td>
                  <td className="num">{r.mase != null ? r.mase.toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="reliab__rule"><Icon name="alert" size={13} /> Higher error means less accurate.</p>
          {missing > 0 && (
            <p className="hint">
              {missing === 1 ? 'The model' : `The ${missing} models`} with &ldquo;&mdash;&rdquo; {missing === 1 ? 'has' : 'have'} no
              figures yet: you need to Run the Forecast.
            </p>
          )}
        </>
      )}
      <p className="hint">
        <b>Percent error</b> is how far off the guess was, as a share of what actually sold. <b>Scaled
        miss</b> is the same miss measured against a simple day-to-day guess, so items that sell a lot and
        items that sell a little can be compared fairly. Lower is better for both; models are ranked by
        scaled miss{mapeSwells ? ', because percent error swells when a period sold very little' : ''}.
      </p>
    </>
  );
}

/* ------------------------------------------------------------ model choice */

/**
 * Which model's forecast this card shows, as a dropdown under its title:
 * categories have their own models, items theirs (scripts/forecast_options.py).
 *
 * A comparison, nothing more: Run Forecast makes every model's forecast, and
 * picking one here only changes what this page shows - nothing is saved, and
 * every other page keeps the recommended model's figures, which the line
 * beside the dropdown says while another is shown. A model Run Forecast has
 * not made yet is listed, greyed out.
 */
/** Under a comparison model that has nothing for this item or category, the
 *  way back - the card holding the dropdown is not shown then. */
function BackToRecommended({ onModel }) {
  return (
    <div className="btn-row">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => onModel(null)}>
        Show the recommended model&rsquo;s forecast
      </button>
    </div>
  );
}

function ModelSelect({ scope, value, onChange }) {
  const { data } = useData(getForecastModels, [], null);
  const s = data?.[scope];
  if (!s) return null;
  const current = value ?? s.default;
  const recommended = s.options.find(o => o.id === s.default);
  // A model Run Forecast has not made is listed but cannot be picked; hovering
  // it says why. The recommended one is the dashboard's own forecast, never
  // held back here (the page says so itself when there is no forecast at all).
  const options = s.options.map(o => {
    const ready = Boolean(o.made) || Boolean(o.recommended);
    return {
      value: o.id,
      label: `${o.label}${o.recommended ? ' (Recommended)' : ''}`,
      disabled: !ready,
      title: ready ? o.desc : 'You need to Run the Forecast',
    };
  });
  return (
    <>
      <SearchSelect value={current} options={options} searchable={false} minWidth={280}
                    onChange={id => onChange(id === s.default ? null : id)} />
      <ModelsInfo options={s.options} />
      {current !== s.default && (
        <span className="hint" title="Nothing else on the dashboard changes">
          For comparison · the dashboard uses {recommended?.label}
        </span>
      )}
    </>
  );
}

/** How each model makes its forecast, in words a store reader can follow:
 *  the (i) beside the model menu. Each line is the option's `desc`
 *  (scripts/forecast_options.py), the same text the menu shows on hover. */
function ModelsInfo({ options }) {
  return (
    <InfoTip label="How do the models work?" width={460}>
      <div className="infotip__h">How each model works</div>
      <p>
        Every model looks at past sales and guesses the next 30 days, each in its own way. Pick one in
        the menu to compare its forecast; the rest of the dashboard always uses the Recommended one.
      </p>
      <ul className="infotip__bullets">
        {options.map(o => (
          <li key={o.id}><b>{o.label}{o.recommended ? ' (Recommended)' : ''}</b>: {o.desc}</li>
        ))}
      </ul>
    </InfoTip>
  );
}

function HistoryChart({ productId }) {
  const { data, loading } = useData(() => getProductHistory(productId), [productId], [],
    { key: `history:${productId}` });
  if (loading) return <Loading />;
  if (!data || data.length === 0) return <div className="empty">No monthly history for this product.</div>;
  return <LineChart data={data.map(d => ({ label: shortMonth(d.month), value: d.units }))} height={240} />;
}
