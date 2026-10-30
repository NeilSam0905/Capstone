import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getProducts, getMonthlyUnits, getStockPosition, getReorderAlerts, getAdvisories,
  getDecisionRules, getMeta, getOverviewPerformance,
} from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import { Loading } from '../components/Pending';
import { LineChart, HBars, StackBar } from '../components/charts';
import Modal from '../components/Modal';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
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

  // Every item with a reorder point, sorted by what its last inventory count
  // says: the stock check under the reorder banner. Each group opens as a list
  // (StockGroupModal), highest sales first, so every count leads to the items
  // behind it and to why they matter.
  const revenueOf = useMemo(() => new Map(products.map(p => [p.product_id, p.revenue ?? 0])), [products]);
  const salesInView = useMemo(() => products.reduce((s, p) => s + (p.revenue ?? 0), 0), [products]);
  const stockGroups = useMemo(() => {
    const bySales = (a, b) => (revenueOf.get(b.product_id) ?? 0) - (revenueOf.get(a.product_id) ?? 0);
    const g = { reorder: due, enough: [], unjudged: [], uncounted: [] };
    alertItems.forEach(a => {
      if (a.current_stock == null) g.uncounted.push(a);
      else if (a.needs_reorder === false) g.enough.push(a);
      else if (a.needs_reorder == null) g.unjudged.push(a);
    });
    ['enough', 'unjudged', 'uncounted'].forEach(k => g[k].sort(bySales));
    return g;
  }, [alertItems, due, revenueOf]);
  const [openGroup, setOpenGroup] = useState(null);

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
      <PerformanceStep perf={perf} dueByCategory={dueByCategory} setPage={setPage}
                       alertById={alertById} latestCount={latestCount} recount={recount} />

      <TopEarnersStep perf={perf} showAll={showAllEarners} setShowAll={setShowAllEarners}
                      alertById={alertById} showSupplier={showSupplier} />

      <ReorderHero
        alerts={alerts}
        due={due}
        nearRop={nearRop}
        groups={stockGroups}
        latestCount={latestCount}
        salesThrough={salesThrough}
        categoryOf={categoryOf}
        earnersDue={top ? dueAmongEarners : null}
        setPage={setPage}
        onShowGroup={setOpenGroup}
      />

      {/* The two things the stock check asks the store to do, side by side:
          order the items at their reorder point, and count the ones that
          cannot be checked yet. */}
      <div className="grid-2">
        <OrderFirst due={due} showSupplier={showSupplier} setPage={setPage}
                    latestCount={latestCount} recount={recount} />
        <CountFirst items={stockGroups.uncounted} revenueOf={revenueOf} salesInView={salesInView}
                    earnerIds={earnerIds} onShowAll={() => setOpenGroup('uncounted')} recount={recount} />
      </div>

      <DecisionGroups counts={ruleCounts} setPage={setPage} wide />

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

      <StockGroupModal
        group={openGroup}
        onClose={() => setOpenGroup(null)}
        groups={stockGroups}
        revenueOf={revenueOf}
        salesInView={salesInView}
        categoryOf={categoryOf}
        earnerIds={earnerIds}
        latestCount={latestCount}
        setPage={setPage}
        recount={recount}
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

/** The decision, in one look: how many items to reorder, how many units and
 *  from how many suppliers, with short tags instead of sentences, and the
 *  categories and suppliers it means. Under it, the stock check: the one bar
 *  every count on this banner is a slice of. */
function ReorderHero({ alerts, due, nearRop, groups, latestCount, salesThrough, categoryOf, earnersDue, setPage, onShowGroup }) {
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

  const check = (
    <StockCheck groups={groups} latestCount={latestCount} salesThrough={salesThrough} onShowGroup={onShowGroup} />
  );

  if (due.length === 0) {
    return (
      <section className="card hero is-clear">
        <div className="hero__main">
          <div className="hero__title">No item needs reordering now</div>
          {nearRop > 0 && (
            <div className="hero__chips">
              <span className="tag tag--warn">{num(nearRop)} running low</span>
            </div>
          )}
        </div>
        {check}
      </section>
    );
  }

  const units = due.reduce((s, a) => s + (a.suggested_order_qty || 0), 0);
  const unitsMin = due.reduce((s, a) => s + orderMin(a), 0);
  const outOfStock = due.filter(a => a.current_stock === 0).length;

  return (
    <section className="card hero">
      <div className="hero__main">
        <div className="hero__title">
          Reorder <b>{num(due.length)} {due.length === 1 ? 'item' : 'items'}</b>
        </div>
        <div className="hero__sub">
          {range(unitsMin, units)} units from {num(suppliers.length)} {suppliers.length === 1 ? 'supplier' : 'suppliers'}
        </div>
        {/* Every due item stays in the list: filtering it to the top earners
            would hide real reorders. The tags only say what is among them. */}
        <div className="hero__chips">
          {outOfStock > 0 && <span className="tag tag--crit">{num(outOfStock)} out of stock</span>}
          {earnersDue > 0 && (
            <span className="tag tag--gold" title="Among the items that make 80% of sales">
              {num(earnersDue)} top {earnersDue === 1 ? 'earner' : 'earners'}
            </span>
          )}
          {nearRop > 0 && <span className="tag tag--warn">{num(nearRop)} more running low</span>}
        </div>
        <div className="hero__actions">
          <button type="button" className="btn btn--ink" onClick={() => setPage?.('reorder')}>
            Open Reorder Alerts <Icon name="arrow" size={13} />
          </button>
        </div>
      </div>

      <HeroList title="Categories" groups={categories} noun={['category', 'categories']} />
      <HeroList title="Suppliers" groups={suppliers} noun={['supplier', 'suppliers']} />

      {check}
    </section>
  );
}

/** What the last inventory count says about each item with a reorder point.
 *  Grey and striped for "not counted": an unknown, not a level. */
const STOCK_GROUPS = [
  { key: 'reorder',   label: 'Reorder now',          color: 'var(--crit)' },
  { key: 'enough',    label: 'Enough stock',         color: 'var(--ok)' },
  { key: 'unjudged',  label: 'Too new to judge',     color: 'var(--warn)' },
  { key: 'uncounted', label: 'Not counted',
    color: 'repeating-linear-gradient(135deg, var(--line) 0 5px, var(--line-soft) 5px 10px)' },
];

/** What "counted" means, for a reader new to the system: the (i) beside the
 *  stock check and Count These First. */
function CountInfo() {
  return (
    <InfoTip label="What is an inventory count?" width={380}>
      <div className="infotip__h">What &ldquo;counted&rdquo; means</div>
      <p>
        An <b>inventory count</b> is when staff count how many units of an item are actually on the shelf
        and record it in the Tally Interface (Stock tab &rarr; Monthly Inventory Count).
      </p>
      <p>
        The system compares that number with the item&rsquo;s <b>reorder point</b>, the stock level at which
        it should be ordered again:
      </p>
      <ul className="infotip__bullets">
        <li><b>Reorder now</b>: the count is at or below the reorder point.</li>
        <li><b>Enough stock</b>: the count is above it.</li>
        <li><b>Too new to judge</b>: counted, but the item has too little sales history for a reorder point yet.</li>
        <li><b>Not counted</b>: no count recorded, so the system cannot tell how many are on hand or whether to reorder.</li>
      </ul>
    </InfoTip>
  );
}

/** The stock check: one bar for every item with a reorder point, split by
 *  what its last count says. It is what makes the banner's numbers relate -
 *  the items to reorder are a slice of the counted ones, and the uncounted
 *  slice is everything the reorder list cannot see. Each slice, and its
 *  legend entry, opens the items in it. */
function StockCheck({ groups, latestCount, salesThrough, onShowGroup }) {
  const shown = STOCK_GROUPS.filter(s => groups[s.key].length > 0);
  const total = shown.reduce((n, s) => n + groups[s.key].length, 0);
  if (!total) return null;
  return (
    <div className="stockcheck">
      <div className="stockcheck__h">
        <span className="stockcheck__title">
          <b>Stock check</b><CountInfo /> &middot; {num(total)} items with a reorder point
        </span>
        <span className="hint">
          {latestCount && <>Last counted {longMonth(latestCount.slice(0, 7))}</>}
          {latestCount && salesThrough && ' · '}
          {salesThrough && <>sales through {usDate(salesThrough)}</>}
        </span>
      </div>
      <div className="stockcheck__bar">
        {shown.map(s => (
          <button key={s.key} type="button" className="stockcheck__seg"
                  style={{ flexGrow: groups[s.key].length, background: s.color }}
                  title={`${num(groups[s.key].length)} ${s.label.toLowerCase()}`}
                  aria-label={`Show the ${num(groups[s.key].length)} items: ${s.label}`}
                  onClick={() => onShowGroup(s.key)} />
        ))}
      </div>
      <div className="stockcheck__legend">
        {shown.map(s => (
          <button key={s.key} type="button" className="stockcheck__item" onClick={() => onShowGroup(s.key)}>
            <i style={{ background: s.color }} />
            <b>{num(groups[s.key].length)}</b> {s.label}
          </button>
        ))}
        <span className="hint">Click any part to see the items</span>
      </div>
    </div>
  );
}

/** Due items grouped by `keyOf`, the biggest order first. */
function groupDue(due, keyOf) {
  const groups = new Map();
  due.forEach(a => {
    const name = keyOf(a) ?? 'Uncategorised';
    const g = groups.get(name) ?? { name, items: 0, units: 0, unitsMin: 0 };
    g.items += 1;
    g.units += a.suggested_order_qty || 0;
    g.unitsMin += orderMin(a);
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
              {range(g.unitsMin, g.units)} units &middot; {num(g.items)} {g.items === 1 ? 'item' : 'items'}
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
// The word joiner keeps the sign on the same line as its figure.
const signedMoney = n => `${n >= 0 ? '+' : '−'}⁠${money(Math.abs(n))}`;
/** '2026-07' -> 'July'. */
const monthName = m => longMonth(m).split(' ')[0];

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
 *  The three tiles answer one question each, in the order a reader asks them:
 *  did sales go up or down, where did that come from (the category and the
 *  items behind it), and what to check - the items that sold last month and
 *  not once this month, which have often simply run out. A sales total on its
 *  own ("₱2.38M, so what?") answers none of them, so it is only the detail
 *  under the change.
 *
 *  Two things that used to sit here are gone. The daily sales line rose and
 *  fell without saying what to order, and most of its ₱0 days were days with no
 *  tally rather than days with no sales; it moved to Demand Forecast, for
 *  exploring. The same month a year earlier went too: the older months were
 *  tallied on far fewer days (12 in July 2025 against 20 in July 2026), so that
 *  comparison measured record-keeping as much as sales. */
function PerformanceStep({ perf, dueByCategory, setPage, alertById, latestCount, recount }) {
  const [stoppedOpen, setStoppedOpen] = useState(false);
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

  const curName = monthName(cur.month);
  const prevName = prev ? monthName(prev.month) : 'the month before';
  const soFar = perf.latest_month_complete ? '' : ` (through ${usDate(perf.history_end)})`;
  const moved = mover && mover.diff !== 0 ? mover : null;
  const stopped = perf.stopped_selling?.items ?? [];
  const stoppedPhp = stopped.reduce((s, i) => s + i.previous_php, 0);
  const stoppedNames = stopped.slice(0, 2).map(i => i.item_name).join(', ')
    + (stopped.length > 2 ? ` and ${num(stopped.length - 2)} more` : '');

  return (
    <>
      <div className="grid-3">
        <KPICard
          label={prev ? `Sales, ${prevName} to ${curName}` : `Sales, ${longMonth(cur.month)}`}
          value={vsPrev == null ? pesoK(cur.php) : signedPct(vsPrev)}
          tone={vsPrev == null ? undefined : vsPrev >= 0 ? 'ok' : 'crit'}
          sub={prev
            ? `${pesoK(prev.php)} in ${prevName}, ${pesoK(cur.php)} in ${curName}${soFar}`
            : `No sales tallied the month before${soFar}`}
          icon="trend"
        />
        <KPICard
          label={moved ? `Category that ${moved.diff < 0 ? 'fell' : 'grew'} most` : 'Nothing changed'}
          value={moved ? moved.category : '—'}
          sub={moved
            ? `${signedMoney(moved.diff)}`
              + (moved.drivers?.length
                ? `, mostly ${moved.drivers.map(d => `${d.item_name} (${signedMoney(d.diff)})`).join(' and ')}`
                : '')
            : prev ? `Every category sold the same as in ${prevName}` : 'No sales tallied the month before'}
          icon="chart"
        />
        <KPICard
          label={`Sold in ${prevName}, none in ${curName}`}
          value={stopped.length ? `${num(stopped.length)} ${stopped.length === 1 ? 'item' : 'items'}` : 'None'}
          tone={stopped.length ? 'crit' : undefined}
          sub={stopped.length
            ? `${money(stoppedPhp)} of sales in ${prevName}. Did they run out? ${stoppedNames}`
            : `Every item that sold ${perf.stopped_selling?.min_units ?? 5} or more in ${prevName} sold again.`}
          icon="alert"
          onClick={stopped.length ? () => setStoppedOpen(true) : undefined}
          linkLabel="See the items"
        />
      </div>

      <StoppedModal
        open={stoppedOpen}
        onClose={() => setStoppedOpen(false)}
        items={stopped}
        minUnits={perf.stopped_selling?.min_units}
        prevName={prevName}
        curName={curName}
        alertById={alertById}
        latestCount={latestCount}
        setPage={setPage}
        recount={recount}
      />

      {cats.length > 0 && (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">What Changed This Month</span>
            <span className="hint">
              By category{prev ? `, ${longMonth(prev.month)} to ${longMonth(cur.month)}` : ''}
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Category</th>
                  {prev && <th className="num">{longMonth(prev.month)}</th>}
                  <th className="num">{longMonth(cur.month)}</th>
                  {prev && <th className="num">Change</th>}
                  <th className="num">Items to reorder</th>
                </tr>
              </thead>
              <tbody>
                {cats.map(c => (
                  <tr key={c.category}>
                    <td className="strong">{c.category}</td>
                    {prev && <td className="num">{money(c.previous_php)}</td>}
                    <td className="num strong">{money(c.php)}</td>
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

/** The items that sold last month and not once this month, biggest seller
 *  first, with where each stands now: one that suddenly stops selling has
 *  often run out, so this is the list to check the shelf against. */
function StoppedModal({ open, onClose, items, minUnits, prevName, curName, alertById, latestCount, setPage, recount }) {
  if (!open) return null;
  const total = items.reduce((s, i) => s + i.previous_php, 0);
  const countMonth = latestCount ? shortMonthYear(latestCount) : null;

  const nowOf = a => {
    if (!a) return { text: 'No reorder point', rank: 3 };
    if (a.needs_reorder) return { text: `Reorder ${range(orderMin(a), a.suggested_order_qty)}`, rank: 0, crit: true };
    if (a.current_stock == null) return { text: 'Not counted', rank: 1 };
    return { text: `${num(a.current_stock)} on hand${countMonth ? ` (${countMonth})` : ''}`, rank: 2 };
  };
  const columns = [
    { key: 'item_name', label: 'Item', strong: true, truncate: true, width: '32%' },
    { key: 'category', label: 'Category', truncate: true, width: '18%' },
    { key: 'previous_units', label: `Sold in ${prevName}`, num: true, width: '14%', render: num },
    { key: 'previous_php', label: `Sales in ${prevName}`, num: true, width: '14%', render: v => money(v) },
    { key: 'rank', label: 'Now', width: '22%',
      render: (_, r) => (r.now.crit ? <span className="tag tag--crit">{r.now.text}</span> : r.now.text) },
  ];
  const rows = items.map(i => {
    const now = nowOf(alertById.get(i.product_id));
    return { ...i, now, rank: now.rank, rowKey: `st${i.product_id}` };
  });

  return (
    <Modal
      open
      onClose={onClose}
      title={`Sold in ${prevName}, none in ${curName}: ${num(items.length)} ${items.length === 1 ? 'item' : 'items'}`}
      subtitle={`Each sold ${minUnits ?? 5} or more in ${prevName}, ${money(total)} in all, then none in ${curName}. `
        + 'An item that suddenly stops selling has often run out: check the shelf, then reorder or count it.'}
      width={960}
    >
      <div className="btn-row" style={{ marginBottom: 12 }}>
        <button className="btn btn--ink btn--sm" onClick={() => { onClose(); setPage?.('reorder'); }}>
          Open Reorder Alerts <Icon name="arrow" size={12} />
        </button>
        {recount && (
          <button className="btn btn--ghost btn--sm" onClick={() => { onClose(); recount(); }}>
            Count them in the Tally Interface <Icon name="arrow" size={12} />
          </button>
        )}
      </div>
      <div className="modal-grow">
        <DataTable columns={columns} data={rows} pageSize={10} minWidth={760} />
      </div>
    </Modal>
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

/* ------------------------------------------------------------ stock check */

/** What each stock-check group is, in a line, for the list it opens. */
const GROUP_TEXT = {
  reorder:   'At or below their reorder point at the last count.',
  enough:    'Above their reorder point at the last count.',
  unjudged:  'Counted, but with too little sales history for a reorder point yet.',
  uncounted: 'No inventory count, so the system cannot tell whether they are running low.',
};

/** The items in one stock-check group, highest sales first: which items a
 *  count is made of, what they sold (why they matter), and the one thing to
 *  do about them. */
function StockGroupModal({ group, onClose, groups, revenueOf, salesInView, categoryOf, earnerIds, latestCount, setPage, recount }) {
  if (!group) return null;
  const meta = STOCK_GROUPS.find(s => s.key === group);
  const items = groups[group] ?? [];
  const sales = items.reduce((s, a) => s + (revenueOf.get(a.product_id) ?? 0), 0);
  const share = salesInView ? sales / salesInView : 0;
  const countMonth = latestCount ? shortMonthYear(latestCount) : null;

  const last = group === 'reorder'
    ? { key: 'suggested_order_qty', label: <>Order <OrderInfo /></>, num: true, strong: true, width: '13%',
        render: (v, a) => range(orderMin(a), v) }
    : group === 'uncounted'
      ? { key: 'top', label: '', width: '12%', render: v => (v ? <span className="tag tag--gold">Top earner</span> : null) }
      : { key: 'current_stock', label: countMonth ? `On hand (${countMonth})` : 'On hand', num: true, width: '12%', render: num };
  const columns = [
    { key: 'item_name', label: 'Item', strong: true, truncate: true, width: '34%' },
    { key: 'category', label: 'Category', truncate: true, width: '18%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '22%' },
    { key: 'sales', label: 'Sales', num: true, width: '14%', render: v => money(v) },
    last,
  ];
  const rows = items.map(a => ({
    ...a,
    category: categoryOf.get(a.product_id) ?? '—',
    sales: revenueOf.get(a.product_id) ?? 0,
    top: earnerIds.has(a.product_id),
    rowKey: `sg${a.product_id}`,
  }));

  return (
    <Modal
      open
      onClose={onClose}
      title={`${meta.label}: ${num(items.length)} ${items.length === 1 ? 'item' : 'items'}`}
      subtitle={`${GROUP_TEXT[group]} They made ${money(sales)}, ${pct1(share * 100)} of sales.`}
      width={960}
    >
      {group === 'reorder' && (
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <button className="btn btn--ink btn--sm" onClick={() => { onClose(); setPage?.('reorder'); }}>
            Open Reorder Alerts <Icon name="arrow" size={12} />
          </button>
        </div>
      )}
      {group === 'uncounted' && recount && (
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <button className="btn btn--ink btn--sm" onClick={() => { onClose(); recount(); }}>
            Count them in the Tally Interface <Icon name="arrow" size={12} />
          </button>
        </div>
      )}
      <div className="modal-grow">
        <DataTable columns={columns} data={rows} pageSize={10} minWidth={760} />
      </div>
    </Modal>
  );
}

/** The items the stock check cannot see, highest sales first: which to count,
 *  and why - what they sell. The whole list opens from "See all". */
function CountFirst({ items, revenueOf, salesInView, earnerIds, onShowAll, recount }) {
  const rows = items.slice(0, ORDER_FIRST_ROWS);
  const sales = items.reduce((s, a) => s + (revenueOf.get(a.product_id) ?? 0), 0);
  const share = salesInView ? sales / salesInView : 0;
  const earners = items.filter(a => earnerIds.has(a.product_id)).length;
  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="card-h__title">
          <span className="section-h">Count These First</span>
          <CountInfo />
        </span>
        {items.length > 0 && (
          <button type="button" className="linkish" onClick={onShowAll}>
            See all {num(items.length)} <Icon name="arrow" size={12} />
          </button>
        )}
      </div>
      {rows.length === 0
        ? <div className="empty">Every item with a reorder point has an inventory count.</div>
        : (
          <>
            <div className="countfirst__why">
              Not counted, so they can&rsquo;t be flagged for reorder. They made <b>{money(sales)}</b>,{' '}
              {pct1(share * 100)} of sales{earners > 0 && <>, including <b>{num(earners)} top earners</b></>}.
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th className="num">Sales</th>
                    <th aria-label="Top earner" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map(a => (
                    <tr key={a.product_id}>
                      <td className="strong overview-order__item" title={a.item_name}>{a.item_name}</td>
                      <td className="num">{money(revenueOf.get(a.product_id) ?? 0)}</td>
                      <td>{earnerIds.has(a.product_id) && <span className="tag tag--gold">Top earner</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {recount && (
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button className="btn btn--ink btn--sm" onClick={recount}>
                  Count them in the Tally Interface <Icon name="arrow" size={12} />
                </button>
              </div>
            )}
          </>
        )}
    </div>
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
                  <th className="num">
                    Order <OrderInfo />{provisional && <span className="th-sub">provisional</span>}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(a => (
                  <tr key={a.product_id} className="is-clickable" onClick={() => setPage?.('reorder')}>
                    <td className="strong overview-order__item" title={a.item_name}>{a.item_name}</td>
                    {showSupplier && <td className="overview-order__supplier">{a.supplier_name}</td>}
                    <td className="num">{num(a.current_stock)}</td>
                    <td className="num strong">{range(orderMin(a), a.suggested_order_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      <div className="hint" style={{ marginTop: 10 }}>
        Least stock cover first.
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

/** The low end of an item's order: the backend's quantity without the safety
 *  stock. Falls back to the full quantity, a range of one. */
const orderMin = a => a.suggested_order_min ?? a.suggested_order_qty ?? 0;

/** 'lo–hi', or one number when the two meet. */
const range = (lo, hi) => (lo === hi ? num(hi) : `${num(lo)}–${num(hi)}`);

/** Why the order is a range and which end to pick: the (i) beside "Order". */
function OrderInfo() {
  return (
    <InfoTip label="Why is the order a range?" width={380}>
      <div className="infotip__h">Why a range</div>
      <p>The order is an estimate, so it is shown from low to high:</p>
      <ul className="infotip__bullets">
        <li><b>Low</b>: enough for the expected sales until the delivery arrives, plus 30 days, less what is on hand.</li>
        <li><b>High</b>: the same, plus the item&rsquo;s <b>safety stock</b>, extra kept in case the delivery is late or sales jump.</li>
      </ul>
      <p>
        Order nearer the high end for a top earner or a supplier that is often late, nearer the low end when
        shelf space is tight. One number means the item has little or no safety stock, so both ends come out the same.
      </p>
    </InfoTip>
  );
}

/** '2026-04' or '2026-04-30' -> 'Apr 2026', short enough for a column heading. */
function shortMonthYear(iso) {
  return `${shortMonth(iso.slice(0, 7)).split(' ')[0]} ${iso.slice(0, 4)}`;
}

/** The FSN classes as the actions they call for (backend/decision_rules.py),
 *  so the card says what to do rather than only how items are split. `wide`
 *  lays the groups out in a row, for a card that spans the page. */
function DecisionGroups({ counts, setPage, wide = false }) {
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
      <div className={'decisions' + (wide ? ' decisions--row' : '')}>
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
