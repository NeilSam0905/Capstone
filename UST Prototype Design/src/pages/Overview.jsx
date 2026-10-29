import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getProducts, getMonthlyUnits, getStockPosition, getReorderAlerts, getAdvisories,
  getDecisionRules, getMeta, getOverviewPerformance,
} from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import { Loading, PendingValue } from '../components/Pending';
import { LineChart, HBars, StackBar } from '../components/charts';
import Modal from '../components/Modal';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import { AdvisoriesPanel, AdvisoriesJump } from '../components/Advisories';
import { ADVISORIES_ANCHOR, TALLY_TAB_KEY } from '../config';
import { num, shortMonth, longMonth, usDate, peso, pesoK, FSN_TONE, FSN_LABEL, RULE, RULE_ORDER } from '../lib/format';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';

/* The Overview reads top to bottom from the store as a whole down to the
 * items to order (the adviser's review, Oct 2026):
 *
 *   Sale Overview                    sales value (pesos), latest tallied month
 *   Top 10 Earners                   the items that drive sales, not all 500
 *   Reorder                          the reorder decision, in units
 *
 * Pesos tell the story because a unit total across a category adds lanyards
 * to jackets; units stay the measure for ordering, which is what suppliers
 * deliver. Each section ends in something to do, so leading with performance
 * does not turn the page into a report. After them: the advisories and the
 * sales detail behind all of it. */

const TONE_COLOR = {
  ok: 'var(--ok)', warn: 'var(--warn)', gold: 'var(--gold)', crit: 'var(--crit)', info: 'var(--info)',
};

/** What each decision rule asks the store to do, in a line. */
const RULE_HINT = {
  keep_stocked: 'Fast-moving: reorder at the reorder point',
  promote: 'Slow-moving: bundle, reposition, market at events',
  review_discontinuation: 'Slow two semesters running: keep or drop?',
  clear_out: 'Never sold: return, mark down or discontinue',
  discontinued: 'Already marked discontinued',
};

const ORDER_FIRST_ROWS = 8;
const HERO_LIST_ROWS = 5;

export default function Overview({ filters, setPage, setView, user }) {
  const [productsOpen, setProductsOpen] = useState(false);
  const advisoriesRef = useRef(null);
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `overview:products:${filters.supplier}|${filters.category}|${filters.dateRange}|${filters.rangeFrom}|${filters.rangeTo}` });
  const { data: monthly } = useData(() => getMonthlyUnits(filters), [filters], []);
  // The On-hand column in the products modal.
  const { data: stock } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: advisories } = useData(getAdvisories, []);
  const { data: rules } = useData(() => getDecisionRules(), []);
  const { data: perf } = useData(() => getOverviewPerformance(filters), [filters.supplier, filters.category]);
  const [showAllEarners, setShowAllEarners] = useState(false);
  // The last day any sale was tallied: what every figure on this page rests
  // on, said beside the stock-count month so neither looks current when it
  // is not.
  const { data: meta } = useData(getMeta, []);
  const salesThrough = meta?.sales_span?.[1] ?? null;

  // A zero on hand is often an old count, not an empty shelf. This opens the
  // Tally Interface straight on its Stock tab, where the count is entered.
  const recount = setView ? () => {
    try { sessionStorage.setItem(TALLY_TAB_KEY, 'stock'); } catch { /* opens on its last tab */ }
    setView('tally');
  } : null;

  // Everything below follows the topbar's supplier and category: an item
  // counts only if the filtered product list holds it.
  const inView = useMemo(() => new Set(products.map(p => p.product_id)), [products]);

  // Every item that gets a reorder point (Fast and Slow), as /api/reorder has
  // it, so this page and Reorder Alerts count the same items.
  const alertItems = useMemo(
    () => (alerts?.available ? alerts.data.items : []).filter(a => inView.has(a.product_id)),
    [alerts, inView]);

  // At or below the reorder point, most urgent first: least cover left, then
  // the fastest seller - the order Reorder Alerts lists them in.
  const due = useMemo(() => alertItems
    .filter(a => a.needs_reorder)
    .sort((a, b) =>
      (a.days_cover_remaining ?? 0) - (b.days_cover_remaining ?? 0)
      || (b.avg_daily_demand ?? 0) - (a.avg_daily_demand ?? 0)),
  [alertItems]);
  const nearRop = alertItems.filter(a => a.approaching_rop).length;
  const counted = alertItems.filter(a => a.current_stock != null);
  const uncounted = alertItems.length - counted.length;
  const latestCount = counted.reduce((m, a) => (a.stock_as_of && a.stock_as_of > m ? a.stock_as_of : m), '');

  const alertById = useMemo(() => new Map(alertItems.map(a => [a.product_id, a])), [alertItems]);
  // /api/reorder carries no category; the product list does.
  const categoryOf = useMemo(() => new Map(products.map(p => [p.product_id, p.category])), [products]);
  // Items due for reorder per category, for the Sale Overview's change table.
  const dueByCategory = useMemo(() => {
    const counts = new Map();
    due.forEach(a => {
      const name = categoryOf.get(a.product_id) ?? 'Uncategorised';
      counts.set(name, (counts.get(name) ?? 0) + 1);
    });
    return counts;
  }, [due, categoryOf]);

  // The Top 10 Earners section's items: the items that make most of the sales value, by
  // which the reorder card says how many reorders fall on them.
  const top = perf?.available ? perf.top : null;
  const earners = useMemo(() => top?.items ?? [], [top]);
  const earnerIds = useMemo(
    () => new Set(earners.slice(0, top?.n_for_share ?? 0).map(t => t.product_id)), [earners, top]);
  const dueAmongEarners = due.filter(a => earnerIds.has(a.product_id)).length;
  const top10Uncounted = earners.slice(0, 10)
    .filter(t => alertById.get(t.product_id)?.current_stock == null).length;

  const ruleCounts = useMemo(() => {
    const counts = Object.fromEntries(RULE_ORDER.map(k => [k, 0]));
    (rules?.items ?? []).forEach(i => {
      if (inView.has(i.product_id) && counts[i.action] !== undefined) counts[i.action]++;
    });
    return counts;
  }, [rules, inView]);

  const showSupplier = filters.supplier === ALL_SUPPLIERS;
  const top10 = useMemo(() => [...products]
    .sort((a, b) => b.total_units - a.total_units)
    .slice(0, 10)
    .map(p => ({
      name: p.item_name,
      value: p.total_units,
      // Whether the best sellers are covered is the question this card
      // answers, so each bar says where its item stands. The supplier is
      // only worth showing while the chart spans every supplier.
      sub: [showSupplier ? p.supplier_name : null, stockStatus(p, alertById.get(p.product_id))]
        .filter(Boolean).join(' · '),
    })), [products, showSupplier, alertById]);

  const trend = useMemo(
    () => monthly.map(m => ({ label: shortMonth(m.month), value: m.units })),
    [monthly]
  );

  if (loading) return <Loading label="Loading catalogue…" />;

  return (
    <div className="stack">
      <SectionLabel title="Sale Overview" hint="Sales value, latest tallied month" />
      <PerformanceStep perf={perf} dueByCategory={dueByCategory} setPage={setPage} />

      <TopEarnersStep perf={perf} showAll={showAllEarners} setShowAll={setShowAllEarners}
                      alertById={alertById} showSupplier={showSupplier} />

      <ReorderHero
        alerts={alerts}
        due={due}
        nearRop={nearRop}
        counted={counted.length}
        uncounted={uncounted}
        latestCount={latestCount}
        salesThrough={salesThrough}
        categoryOf={categoryOf}
        setPage={setPage}
        earners={top ? { due: dueAmongEarners, n: top.n_for_share, share: top.share } : null}
      />

      <div className="grid-3">
        <KPICard
          label="Top 10 Earners Without a Count"
          value={top ? `${num(top10Uncounted)} of ${num(Math.min(10, earners.length))}` : <PendingValue />}
          tone={top10Uncounted > 0 ? 'crit' : 'ok'}
          sub={top10Uncounted > 0
            ? 'The items that bring in the most cannot be checked against a reorder point until they are counted. Count these first.'
            : 'Every top-10 earner has a stock count.'}
          icon="tag"
          onClick={top10Uncounted > 0 ? recount ?? undefined : undefined}
          linkLabel="Count them in the Tally Interface"
        />
        <KPICard
          label="Items Without an Inventory Count"
          value={alerts?.available ? num(uncounted) : <PendingValue />}
          tone={uncounted > 0 ? 'crit' : 'ok'}
          sub={alerts?.available
            ? `of ${num(alertItems.length)} items with a reorder point. Count them in the Tally Interface (Stock tab → Monthly Inventory Count) so they can be checked.`
            : alerts?.reason ?? 'Reorder data not yet available'}
          icon="box"
        />
        <NextEventTile
          advisories={advisories}
          onOpen={() => advisoriesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        />
      </div>

      <div className="grid-2-1">
        <OrderFirst due={due} showSupplier={showSupplier} setPage={setPage}
                    latestCount={latestCount} recount={recount} />
        <DecisionGroups counts={ruleCounts} setPage={setPage} />
      </div>

      {/* Advisories — calendar-contextual, driven by real data. The id is
          what the topbar's Advisories button scrolls to on this page
          (App.jsx); on every other page that button opens them in a dialog. */}
      <div id={ADVISORIES_ANCHOR} ref={advisoriesRef} style={{ scrollMarginTop: 12 }}>
        <div className="card-h"><span className="section-h">Upcoming Event Advisories</span></div>
        <AdvisoriesPanel advisories={advisories} setPage={setPage} />
      </div>

      <div className="card-h" style={{ marginBottom: 0 }}>
        <span className="section-h">Sales Detail</span>
        <span className="hint">What sold, and when</span>
      </div>
      <div className="grid-2">
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Monthly Units Sold</span>
            <span className="hint">Stock up ahead of the peaks</span>
          </div>
          <LineChart data={trend} />
        </div>
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Best Sellers</span>
            <button type="button" className="linkish" onClick={() => setProductsOpen(true)}>
              Browse all products <Icon name="arrow" size={12} />
            </button>
          </div>
          <HBars data={top10} />
        </div>
      </div>

      {/* Overview only: a floating pill that scrolls down to the advisories. */}
      <AdvisoriesJump target={advisoriesRef} user={user} />

      <ProductsModal
        open={productsOpen}
        onClose={() => setProductsOpen(false)}
        products={products}
        stock={stock}
      />
    </div>
  );
}

/** Where a best seller stands: due for reorder, never counted, or how many
 *  are on hand. Non-moving items have no reorder point at all. */
function stockStatus(product, alert) {
  if (!alert) return product.fsn_class === 'N' ? 'Non-moving' : null;
  if (alert.needs_reorder) return 'Reorder now';
  if (alert.current_stock == null) return 'Not counted';
  return `${num(alert.current_stock)} on hand`;
}

/* ----------------------------------------------------------------- hero */

/** The decision, in one look: how many items to reorder, how many units, which
 *  categories need restocking and which suppliers that means calling. The
 *  footnote says how much of the catalogue the answer rests on, because an
 *  item never counted can never show up here however low it runs. */
function ReorderHero({ alerts, due, nearRop, counted, uncounted, latestCount, salesThrough, categoryOf, setPage, earners }) {
  const categories = useMemo(() => groupDue(due, a => categoryOf.get(a.product_id)), [due, categoryOf]);
  const suppliers = useMemo(() => groupDue(due, a => a.supplier_name), [due]);

  if (!alerts) return <div className="card hero"><Loading label="Checking stock against reorder points…" /></div>;
  if (!alerts.available) {
    return (
      <section className="card hero">
        <div className="hero__main">
          <div className="hero__title">Reorder data is not available yet</div>
          <div className="hero__sub">{alerts.reason ?? 'Run Analytics in the Tally Interface to calculate reorder points.'}</div>
        </div>
      </section>
    );
  }

  const units = due.reduce((s, a) => s + (a.suggested_order_qty || 0), 0);
  const outOfStock = due.filter(a => a.current_stock === 0).length;
  const footnote = (
    <div className="hero__foot">
      {uncounted > 0
        ? <>Only {num(counted)} of {num(counted + uncounted)} items have an inventory count</>
        : <>All {num(counted)} items have an inventory count</>}
      {latestCount && <> (last counted {longMonth(latestCount.slice(0, 7))})</>}.
      {uncounted > 0 && <> Count the other {num(uncounted)} to include them.</>}
      {salesThrough && <> Based on sales tallied through {usDate(salesThrough)}.</>}
    </div>
  );

  if (due.length === 0) {
    return (
      <section className="card hero is-clear">
        <div className="hero__main">
          <div className="hero__title">No item needs reordering now</div>
          <div className="hero__sub">
            {nearRop > 0
              ? <>{num(nearRop)} {nearRop === 1 ? 'item is' : 'items are'} running low; add {nearRop === 1 ? 'it' : 'them'} to the next order.</>
              : 'Every counted item still has enough stock.'}
          </div>
        </div>
        {footnote}
      </section>
    );
  }

  return (
    <section className="card hero">
      <div className="hero__main">
        <div className="hero__title">
          Reorder <b>{num(due.length)} {due.length === 1 ? 'item' : 'items'}</b>
        </div>
        <div className="hero__sub">
          Order about <b>{num(units)} units</b> across <b>{num(suppliers.length)} {suppliers.length === 1 ? 'supplier' : 'suppliers'}</b>.
          {outOfStock > 0 && <> <b className="is-crit">{num(outOfStock)}</b> of {outOfStock === 1 ? 'this item has' : 'these items have'} no stock left.</>}
          {/* Every due item stays in the list: filtering it to the top
              earners would hide real reorders. This only says where the
              sales value is among them. */}
          {earners && (
            <> {num(earners.due)} of them {earners.due === 1 ? 'is' : 'are'} among the {num(earners.n)} items
              that make {Math.round(earners.share * 100)}% of sales; the
              other {num(due.length - earners.due)} are lower-value.</>
          )}
        </div>
        <div className="hero__actions">
          <button type="button" className="btn btn--ink" onClick={() => setPage?.('reorder')}>
            Open Reorder Alerts <Icon name="arrow" size={13} />
          </button>
          <span className="hint">
            {nearRop > 0
              ? `${num(nearRop)} more ${nearRop === 1 ? 'item is' : 'items are'} running low; add ${nearRop === 1 ? 'it' : 'them'} to the next order.`
              : 'No other counted item is running low.'}
          </span>
        </div>
      </div>

      <HeroList title="Categories" groups={categories} noun={['category', 'categories']} />
      <HeroList title="Suppliers" groups={suppliers} noun={['supplier', 'suppliers']} />

      {footnote}
    </section>
  );
}

/** Due items grouped by `keyOf`, the biggest order first. */
function groupDue(due, keyOf) {
  const groups = new Map();
  due.forEach(a => {
    const name = keyOf(a) ?? 'Uncategorised';
    const g = groups.get(name) ?? { name, items: 0, units: 0 };
    g.items += 1;
    g.units += a.suggested_order_qty || 0;
    groups.set(name, g);
  });
  return [...groups.values()].sort((a, b) => b.units - a.units || b.items - a.items);
}

/** One of the hero's side lists: the top few groups with their units and
 *  items to order, each with a bar against the largest. */
function HeroList({ title, groups, noun }) {
  const max = Math.max(1, ...groups.map(g => g.units));
  const more = groups.length - HERO_LIST_ROWS;
  return (
    <div className="hero__side">
      <div className="hero__side-h">{title}</div>
      <ul className="hero__list">
        {groups.slice(0, HERO_LIST_ROWS).map(g => (
          <li key={g.name} className="hero__row">
            <span className="hero__row-name" title={g.name}>{g.name}</span>
            <span className="hero__row-qty">
              {num(g.units)} units &middot; {num(g.items)} {g.items === 1 ? 'item' : 'items'}
            </span>
            <span className="hero__row-bar"><span style={{ width: `${(g.units / max) * 100}%` }} /></span>
          </li>
        ))}
      </ul>
      {more > 0 && (
        <div className="hint" style={{ marginTop: 8 }}>+ {num(more)} more {more === 1 ? noun[0] : noun[1]}</div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- steps */

/* The peso sections read /api/overview/performance. Every date there is anchored on
 * the last day a sale was tallied, not on today, so "latest month" is the
 * latest TALLIED month and is labelled that way. The old "Expected Demand ·
 * 30 Days" tile is gone: it added units of unlike items together ("480 units
 * of what?"); units appear only per
 * item, in the reorder card. */

const change = (a, b) => (b ? (a - b) / b : null);
const signedPct = p => `${p >= 0 ? '+' : '−'}${Math.abs(Math.round(p * 100))}%`;
const pct1 = v => `${v.toFixed(1)}%`;
/** Pesos, in thousands once they are big enough for it to read well. */
const money = n => (Math.abs(n) < 10000 ? peso(n) : pesoK(n));
const signedMoney = n => `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}`;

/** A label over the part of the page it names. */
function SectionLabel({ title, hint }) {
  return (
    <div className="step-h">
      <span className="section-h">{title}</span>
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/** "Next step: ...", the line every step ends on. */
function NextStep({ children }) {
  return <div className="hint step-next"><b>Next step:</b> {children}</div>;
}

/** Sale Overview: the latest tallied month's sales value against the month
 *  before, and where it moved - category by category, with how many items in
 *  each are due for reorder - so every figure points at something to do.
 *
 *  Two things that used to sit here are gone. The daily sales line rose and
 *  fell without saying what to order, and most of its ₱0 days were days with no
 *  tally rather than days with no sales; it moved to Demand Forecast, for
 *  exploring. The same month a year earlier went too: the older months were
 *  tallied on far fewer days (12 in July 2025 against 20 in July 2026), so that
 *  comparison measured record-keeping as much as sales. */
function PerformanceStep({ perf, dueByCategory, setPage }) {
  if (!perf) return <div className="card card__pad"><Loading label="Adding up sales…" /></div>;
  if (!perf.available) return <div className="card card__pad"><div className="empty">{perf.reason}</div></div>;

  const cur = perf.latest_month, prev = perf.previous_month;
  const vsPrev = prev && change(cur.php, prev.php);
  const unpriced = perf.priced_unit_share == null ? 0 : 1 - perf.priced_unit_share;

  const cats = (perf.by_category ?? [])
    .filter(c => c.php > 0 || c.previous_php > 0)
    .map(c => ({
      ...c,
      diff: c.php - c.previous_php,
      pct: change(c.php, c.previous_php),
      due: dueByCategory.get(c.category) ?? 0,
    }));
  // The category that moved furthest the way the store moved: the reason the
  // month-on-month tile gives for its number.
  const mover = vsPrev == null || !cats.length ? null
    : [...cats].sort((a, b) => (vsPrev < 0 ? a.diff - b.diff : b.diff - a.diff))[0];
  const grew = cats.filter(c => c.diff > 0).sort((a, b) => b.diff - a.diff).slice(0, 2);
  const fell = cats.filter(c => c.diff < 0).sort((a, b) => a.diff - b.diff)[0];

  const steps = [];
  if (grew.length) {
    steps.push(`restock the categories that grew most first: ${grew.map(c => c.category).join(' and ')}.`);
  }
  if (fell) {
    steps.push(`Check why ${fell.category} fell ${signedMoney(fell.diff)}`
      + (fell.due ? `: ${num(fell.due)} of its items are at their reorder point, so it may have run out.` : '.'));
  }
  if (!steps.length) steps.push('keep the Top 10 Earners below in stock.');

  return (
    <>
      <div className="grid-2">
        <KPICard
          label={`Sales, ${longMonth(cur.month)}`}
          value={pesoK(cur.php)}
          sub={`${num(cur.units)} units · latest tallied month`
            + (perf.latest_month_complete ? '' : `, through ${usDate(perf.history_end)}`)}
          icon="trend"
        />
        <KPICard
          label={prev ? `vs ${longMonth(prev.month)}` : 'vs the month before'}
          value={vsPrev == null ? '—' : signedPct(vsPrev)}
          tone={vsPrev == null ? undefined : vsPrev >= 0 ? 'ok' : 'crit'}
          sub={prev
            ? `${pesoK(prev.php)} the month before`
              + (mover && mover.diff !== 0
                ? ` · biggest ${mover.diff < 0 ? 'drop' : 'rise'}: ${mover.category} (${signedMoney(mover.diff)})`
                : '')
            : 'No sales tallied the month before'}
          icon="chart"
        />
      </div>

      {cats.length > 0 && (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">What Changed This Month</span>
            <span className="hint">
              By category{prev ? `, ${longMonth(cur.month)} against ${longMonth(prev.month)}` : ''}
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="num">{longMonth(cur.month)}</th>
                  {prev && <th className="num">{longMonth(prev.month)}</th>}
                  {prev && <th className="num">Change</th>}
                  <th className="num">Items to reorder</th>
                </tr>
              </thead>
              <tbody>
                {cats.map(c => (
                  <tr key={c.category}>
                    <td className="strong">{c.category}</td>
                    <td className="num strong">{money(c.php)}</td>
                    {prev && <td className="num">{money(c.previous_php)}</td>}
                    {prev && (
                      <td className={`num change-cell${c.diff > 0 ? ' is-up' : c.diff < 0 ? ' is-down' : ''}`}>
                        {c.pct == null
                          ? (c.php > 0 ? 'new' : '—')
                          : <>{c.diff >= 0 ? '▲' : '▼'} {Math.abs(Math.round(c.pct * 100))}%</>}
                        <span className="change-cell__php">{signedMoney(c.diff)}</span>
                      </td>
                    )}
                    <td className="num">
                      {c.due > 0
                        ? <button type="button" className="linkish" onClick={() => setPage?.('reorder')}>{num(c.due)}</button>
                        : <span className="nodata">&mdash;</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <NextStep>
            {steps.join(' ')}
            {unpriced > 0.001 && <> (Peso figures leave out the {pct1(unpriced * 100)} of units with no price.)</>}
          </NextStep>
        </div>
      )}
    </>
  );
}

/** Top 10 Earners: the items that make most of the sales value, top 10 first, with
 *  the rest of the 80% one click away. Items that barely sold are named as
 *  left out rather than silently dropped. */
function TopEarnersStep({ perf, showAll, setShowAll, alertById, showSupplier }) {
  if (!perf) return <div className="card card__pad"><Loading label="Ranking items by sales…" /></div>;
  if (!perf.available) return <div className="card card__pad"><div className="empty">{perf.reason}</div></div>;

  const t = perf.top, s = perf.small_sellers;
  const sharePct = Math.round(t.share * 100);
  const list = showAll ? t.items.slice(0, t.n_for_share) : t.items.slice(0, 10);
  const rows = list.map(i => ({
    name: i.item_name,
    value: i.php,
    sub: [`${pct1(i.share * 100)} of sales`, showSupplier ? i.supplier_name : null,
          stockStatus(i, alertById.get(i.product_id))].filter(Boolean).join(' · '),
  }));

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">
          {showAll ? `The ${num(t.n_for_share)} Items That Make ${sharePct}% of Sales` : 'Top 10 Earners'}
        </span>
        {t.n_for_share > 10 && (
          <button type="button" className="linkish" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'Show top 10' : `Show all ${num(t.n_for_share)} (${sharePct}% of sales)`}
          </button>
        )}
      </div>
      <div className="hint" style={{ marginBottom: 12 }}>
        {usDate(t.window_start)} – {usDate(t.window_end)}: {pesoK(t.total_php)} in sales. The top 10 made
        {' '}{pct1(t.top10_share * 100)}; {num(t.n_for_share)} of the {num(t.n_selling)} items that sold
        made {sharePct}%.
      </div>
      {rows.length ? <HBars data={rows} valueFmt={pesoK} /> : <div className="empty">No priced sales in this window.</div>}
      <NextStep>
        keep these items in stock first; the reorder list below shows which need ordering.
        {s.count > 0 && (
          <> {num(s.count)} {s.count === 1 ? 'item' : 'items'} sold {s.max_units} or fewer units in these 12
            months ({pct1(s.share * 100)} of sales) and {s.count === 1 ? 'is' : 'are'} left out here; they
            stay under Browse all products and Classification.</>
        )}
      </NextStep>
    </div>
  );
}

/* ---------------------------------------------------------------- tiles */

/** The next dated advisory (enrollment, exams, a break, an event) that has
 *  not ended yet: what the store should plan the next order around. */
function NextEventTile({ advisories, onOpen }) {
  const today = new Date().toISOString().slice(0, 10);
  const next = (advisories?.advisories ?? [])
    .filter(a => a.date_range && a.date_range[1] >= today)
    .sort((a, b) => a.date_range[0].localeCompare(b.date_range[0]))[0];

  if (!next) {
    return (
      <KPICard
        label="Next on the Calendar"
        value={advisories ? 'Nothing flagged' : <PendingValue />}
        sub="No enrollment, exam week, break or event in the advisories"
        icon="cal"
      />
    );
  }

  // "Exams scheduled 12/14/2026 to 12/20/2026. Past exam weeks sold about the
  // same per day as regular days; restock as usual." - the dates are on the
  // tile already, so only the advice after the first sentence is kept.
  const advice = next.description.split(/(?<=\.)\s+/).slice(1).join(' ') || next.description;
  const [from, to] = next.date_range;
  return (
    <KPICard
      label="Next on the Calendar"
      value={next.title.replace(/\s+Upcoming$/i, '')}
      sub={`${usDate(from)}${to !== from ? ` – ${usDate(to)}` : ''} · ${advice}`}
      icon="cal"
      onClick={onOpen}
      linkLabel="See advisories"
    />
  );
}

/* -------------------------------------------------------------- actions */

/** The first few items to order, most urgent first, with the quantity. The
 *  full list, with how each figure was worked out, is Reorder Alerts. */
function OrderFirst({ due, showSupplier, setPage, latestCount, recount }) {
  const rows = due.slice(0, ORDER_FIRST_ROWS);
  const countMonth = latestCount ? longMonth(latestCount.slice(0, 7)) : null;
  // step5 marks every reorder point provisional while lead times are
  // estimated from the item's name rather than measured deliveries.
  const provisional = due.some(a => a.is_provisional);
  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Order These First</span>
        {due.length > 0 && (
          <button type="button" className="linkish" onClick={() => setPage?.('reorder')}>
            See all {num(due.length)} <Icon name="arrow" size={12} />
          </button>
        )}
      </div>
      {rows.length === 0
        ? <div className="empty">Nothing is at its reorder point.</div>
        : (
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Item</th>
                  {showSupplier && <th className="overview-order__supplier">Supplier</th>}
                  <th className="num" title={countMonth ? `Stock as last counted, ${countMonth}` : undefined}>
                    On hand{countMonth && <span className="th-sub">as of {shortMonthYear(latestCount)}</span>}
                  </th>
                  <th className="num">Order{provisional && <span className="th-sub">provisional</span>}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(a => (
                  <tr key={a.product_id} className="is-clickable" onClick={() => setPage?.('reorder')}>
                    <td className="strong overview-order__item" title={a.item_name}>{a.item_name}</td>
                    {showSupplier && <td className="overview-order__supplier">{a.supplier_name}</td>}
                    <td className="num">{num(a.current_stock)}</td>
                    <td className="num strong">{num(a.suggested_order_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      <div className="hint" style={{ marginTop: 10 }}>
        Least stock cover first. Each order covers the supplier&rsquo;s lead time plus 30 days of sales.
        {provisional && <> Quantities are provisional: lead times are estimates
          {countMonth && <> and stock is as counted in {countMonth}</>}.</>}
        {recount && rows.length > 0 && (
          <> Is an item&rsquo;s stock wrong?{' '}
            <button type="button" className="linkish" onClick={recount}>Recount it</button>.</>
        )}
      </div>
    </div>
  );
}

/** '2026-04' or '2026-04-30' -> 'Apr 2026', short enough for a column heading. */
function shortMonthYear(iso) {
  return `${shortMonth(iso.slice(0, 7)).split(' ')[0]} ${iso.slice(0, 4)}`;
}

/** The FSN classes as the actions they call for (backend/decision_rules.py),
 *  so the card says what to do rather than only how items are split. */
function DecisionGroups({ counts, setPage }) {
  const shown = RULE_ORDER.filter(k => k !== 'discontinued' || counts[k] > 0);
  const total = shown.reduce((s, k) => s + counts[k], 0);
  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">What to Do With Each Group</span>
        <button type="button" className="linkish" onClick={() => setPage?.('classification')}>
          Open Classification <Icon name="arrow" size={12} />
        </button>
      </div>
      <div className="decisions">
        {shown.map(k => (
          <button key={k} type="button" className="decision" onClick={() => setPage?.('classification')}>
            <span className={`tag tag--${RULE[k].tone}`}>{RULE[k].label}</span>
            <span className="decision__hint">{RULE_HINT[k]}</span>
            <span className="decision__count">{num(counts[k])}</span>
          </button>
        ))}
      </div>
      <StackBar parts={shown.map(k => ({ value: counts[k], color: TONE_COLOR[RULE[k].tone] }))} />
      <div className="hint" style={{ textAlign: 'right', marginTop: 6 }}>{num(total)} products</div>
      {/* Promote + Review together are the Slow class, which is how Power BI
          and Classification count it. A few never-sold rows are older names of
          items that still sell under a new one (Names to review). */}
      <div className="hint" style={{ marginTop: 8 }}>
        {RULE.promote.label} and {RULE.review_discontinuation.label} together are the Slow-moving
        items. {RULE.clear_out.label} includes a few older names of items that still sell; check
        before returning stock.
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- products */

/** Browse the whole catalogue: search, filter, sort.
 *
 *  A modal rather than a page because there is no Products page to navigate
 *  to - the FSN card already owns Classification, and this answers a different
 *  question ("what do we sell, and how much of it") without leaving Overview.
 *
 *  Sorting and paging come free from DataTable; this only adds the search and
 *  the three filters. The subtitle always states the visible count against the
 *  total, so a filtered catalogue is never mistaken for the whole one.
 */
function ProductsModal({ open, onClose, products, stock }) {
  const [q, setQ] = useState('');
  const [fsn, setFsn] = useState('All');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);

  // Reset on open: this answers a fresh question each time, and reopening onto
  // a stale search reads as missing data.
  useEffect(() => {
    if (open) {
      setQ('');
      setFsn('All');
      setCategory(ALL_CATEGORIES);
      setSupplier(ALL_SUPPLIERS);
    }
  }, [open]);

  const stockById = useMemo(
    () => new Map((stock?.items ?? []).map(p => [p.product_id, p.current_stock])),
    [stock]
  );

  const categories = useMemo(
    () => [...new Set(products.map(p => p.category))].sort(), [products]);
  const suppliers = useMemo(
    () => [...new Set(products.map(p => p.supplier_name))].sort(), [products]);

  const needle = q.trim().toLowerCase();
  const shown = useMemo(() => products.filter(p =>
    (!needle
      || p.item_name.toLowerCase().includes(needle)
      || p.supplier_name.toLowerCase().includes(needle)
      || p.category.toLowerCase().includes(needle))
    && (fsn === 'All' || p.fsn_class === fsn)
    && (category === ALL_CATEGORIES || p.category === category)
    && (supplier === ALL_SUPPLIERS || p.supplier_name === supplier)
  ), [products, needle, fsn, category, supplier]);

  // Widths sum to exactly 100. They previously summed to 104, which
  // table-layout:fixed renormalises - every column came out narrower than it
  // asked for, and the Class pill was the first casualty.
  const columns = [
    { key: 'item_name', label: 'Item', strong: true, truncate: true, width: '26%' },
    { key: 'category', label: 'Category', truncate: true, width: '13%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '16%' },
    {
      key: 'fsn_class', label: 'Class', width: '14%',
      render: v => v
        ? <span className={`tag tag--${FSN_TONE[v] || 'info'}`}>{FSN_LABEL[v] || v}</span>
        : <span className="nodata">&mdash;</span>,
    },
    { key: 'total_units', label: 'Units', num: true, strong: true, width: '10%', render: num },
    { key: 'adus', label: 'ADUS', num: true, width: '9%', render: v => (v ?? 0).toFixed(3) },
    {
      key: 'current_stock', label: 'On hand', num: true, width: '12%',
      render: v => v == null
        ? <span className="nodata" title="Never counted">&ndash;</span>
        : num(v),
    },
  ];

  const rows = shown.map(p => ({
    ...p,
    current_stock: stockById.get(p.product_id) ?? null,
    rowKey: `pr${p.product_id}`,
  }));

  const filtered = shown.length !== products.length;
  const units = shown.reduce((sum, p) => sum + p.total_units, 0);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Products"
      subtitle={filtered
        ? `${num(shown.length)} of ${num(products.length)} products - ${num(units)} units sold`
        : `${num(products.length)} products - ${num(units)} units sold`}
      width={1000}
    >
      <div className="modal-filters">
        <input
          type="search"
          value={q}
          placeholder="Search Item, Supplier Or Category..."
          onChange={e => setQ(e.target.value)}
          aria-label="Search products"
        />
        <select value={fsn} onChange={e => setFsn(e.target.value)} aria-label="Filter by FSN class">
          <option value="All">All Classes</option>
          <option value="F">{FSN_LABEL.F}</option>
          <option value="S">{FSN_LABEL.S}</option>
          <option value="N">{FSN_LABEL.N}</option>
        </select>
        <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Filter by category">
          <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={supplier} onChange={e => setSupplier(e.target.value)} aria-label="Filter by supplier">
          <option value={ALL_SUPPLIERS}>{ALL_SUPPLIERS}</option>
          {suppliers.map(sup => <option key={sup} value={sup}>{sup}</option>)}
        </select>
      </div>

      <div className="hint" style={{ margin: '2px 0 10px' }}>
        Click a column heading to sort. &ndash; in <b>On hand</b> means never counted.
        {filtered && <> Showing <b>{num(shown.length)}</b> of {num(products.length)}.</>}
      </div>

      <div className="modal-grow">
        {shown.length === 0
          ? <div className="empty">No product matches those filters.</div>
          : <DataTable columns={columns} data={rows} pageSize={12} minWidth={900} />}
      </div>
    </Modal>
  );
}
