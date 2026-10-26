import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getProducts, getMonthlyUnits, getStockPosition, getReorderAlerts, getAdvisories,
  getDecisionRules, getCategoryForecasts, getMeta,
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
import { num, shortMonth, longMonth, usDate, roundNice, FSN_TONE, FSN_LABEL, RULE, RULE_ORDER } from '../lib/format';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';

/* The Overview is read top to bottom, general to detailed:
 *
 *   1. the decision the dashboard exists to support - what to reorder now -
 *      readable in one look, with the suppliers it means calling;
 *   2. three glance tiles for what shapes the next decisions: expected
 *      demand, the items that cannot be checked yet, and the next calendar
 *      event;
 *   3. the actions themselves: the items to order first, and what to do with
 *      each FSN group;
 *   4. the advisories;
 *   5. the sales detail behind all of it.
 *
 * Counts that support no decision on their own (total products, units by
 * category) are left out; the product list stays one click away under
 * Best Sellers. */

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
  const { data: forecasts } = useData(getCategoryForecasts, []);
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
      />

      <div className="grid-3">
        <ExpectedDemandTile forecasts={forecasts} filters={filters} setPage={setPage} />
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
function ReorderHero({ alerts, due, nearRop, counted, uncounted, latestCount, salesThrough, categoryOf, setPage }) {
  const categories = useMemo(() => groupDue(due, a => categoryOf.get(a.product_id)), [due, categoryOf]);
  const suppliers = useMemo(() => groupDue(due, a => a.supplier_name), [due]);

  if (!alerts) return <div className="card hero"><Loading label="Checking stock against reorder points…" /></div>;
  if (!alerts.available) {
    return (
      <section className="card hero">
        <div className="hero__main">
          <div className="hero__title">Reorder data is not available yet</div>
          <div className="hero__sub">{alerts.reason ?? 'Run the pipeline to calculate reorder points.'}</div>
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

/* ---------------------------------------------------------------- tiles */

/** Expected demand over the forecast's 30 days. Forecasts are per category,
 *  so a category filter narrows it and a supplier filter cannot. The window
 *  starts the day after the last tally, which can already be in the past -
 *  said so rather than passed off as the next 30 days from today. */
function ExpectedDemandTile({ forecasts, filters, setPage }) {
  const cats = forecasts?.available ? forecasts.data.categories : [];
  const scoped = filters.category === ALL_CATEGORIES ? cats : cats.filter(c => c.category === filters.category);
  const total = scoped.reduce((s, c) => s + c.total_30d, 0);
  const snapshot = cats[0]?.snapshot_date;

  let sub;
  let passed = false;
  if (!forecasts) sub = 'Loading forecast…';
  else if (!forecasts.available || !snapshot) sub = forecasts.reason ?? 'Forecast not yet available';
  else if (!scoped.length) sub = `No forecast for ${filters.category}`;
  else {
    const start = addDays(snapshot, 1);
    const end = addDays(snapshot, 30);
    passed = end < new Date().toISOString().slice(0, 10);
    sub = `${usDate(start)} – ${usDate(end)} · about ${num(total / 30)} a day`
      + (filters.supplier !== ALL_SUPPLIERS ? ' · all suppliers' : '')
      + (passed ? ' · this window has passed; add newer tally sheets' : '');
  }

  // A window that has already passed supports no decision, so its total is
  // shown greyed and smaller (.kpi__val.is-stale) rather than at headline size.
  return (
    <KPICard
      label="Expected Demand · 30 Days"
      value={forecasts?.available && scoped.length ? `${num(roundNice(total))} units` : <PendingValue />}
      tone={passed ? 'stale' : undefined}
      sub={sub}
      icon="trend"
      onClick={() => setPage?.('forecast')}
      linkLabel="Open Demand Forecast"
    />
  );
}

function addDays(iso, days) {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

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
