import { useEffect, useMemo, useRef, useState } from 'react';
import {
  getProducts, getStockPosition, getReorderAlerts, getAdvisories, getMeta, getOverviewPerformance,
  getSuppliers, getCategories, getMonths,
} from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import { Loading } from '../components/Pending';
import { HBars } from '../components/charts';
import Modal from '../components/Modal';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
import SearchSelect from '../components/SearchSelect';
import { AdvisoriesPanel, AdvisoriesJump } from '../components/Advisories';
import { ADVISORIES_ANCHOR, TALLY_TAB_KEY } from '../config';
import { num, shortMonth, longMonth, usDate, peso, pesoK, FSN_TONE, FSN_LABEL } from '../lib/format';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';

/* The Overview reads top to bottom from the store as a whole down to the
 * items to order (the adviser's review, Oct 2026):
 *
 *   Sale Overview          three tiles: the change, where it came from, what to check
 *   Reorder                the reorder decision, in units, with the stock check
 *   Order These First      the most urgent orders  |  Top Sellers  the items that sell most
 *   Advisories             upcoming events
 *
 * Pesos tell the story because a unit total across a category adds lanyards
 * to jackets; units stay the measure for ordering, which is what suppliers
 * deliver.
 *
 * Kept short on purpose (consultation, Oct 2026: "too much is displayed"):
 * each card shows at most five rows and every full list opens on a click. The
 * category table opens from its tile, the uncounted items from the stock
 * check, and two cards left for the pages that already hold them - the
 * decision groups (FSN Classification) and the monthly units chart (Demand
 * Forecast). Top 10 Earners and Best Sellers ranked the same items two ways,
 * so they are one card with a pesos / units switch. */

const SHORT_LIST_ROWS = 5;
const HERO_LIST_ROWS = 5;

export default function Overview({ filters, setPage, setView, user }) {
  const [productsOpen, setProductsOpen] = useState(false);
  const advisoriesRef = useRef(null);
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `overview:products:${filters.supplier}|${filters.category}|${filters.dateRange}|${filters.rangeFrom}|${filters.rangeTo}` });
  // The On-hand column in the products modal.
  const { data: stock } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: advisories } = useData(getAdvisories, []);
  const { data: perf } = useData(() => getOverviewPerformance(filters), [filters.supplier, filters.category]);
  // The last day any sale was tallied: what every figure on this page rests
  // on, said beside the stock-count month so neither looks current when it
  // is not.
  const { data: meta } = useData(getMeta, []);
  const salesThrough = meta?.sales_span?.[1] ?? null;

  // A zero on hand is often an old count, not an empty shelf. This opens the
  // Tally Interface straight on its Inventory tab, where the count is entered.
  const recount = setView ? () => {
    try { sessionStorage.setItem(TALLY_TAB_KEY, 'stock'); } catch { /* opens on its last tab */ }
    setView('tally');
  } : null;

  // The Overview has no topbar filters (App.jsx PAGE_FILTERS), so this is
  // every product; Top Sellers narrows itself with its own Filter button.
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

  const showSupplier = filters.supplier === ALL_SUPPLIERS;

  if (loading) return <Loading label="Loading catalogue…" />;

  return (
    <div className="stack">
      <PerformanceStep filters={filters} dueByCategory={dueByCategory} setPage={setPage}
                       alertById={alertById} latestCount={latestCount} recount={recount} />

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

      {/* What to order first, beside what sells most - which of those to keep
          in stock. The uncounted items open from the stock check above. */}
      <div className="grid-2">
        <OrderFirst due={due} showSupplier={showSupplier} setPage={setPage}
                    latestCount={latestCount} recount={recount} />
        <TopSellers alertById={alertById} categoryOf={categoryOf} onBrowse={() => setProductsOpen(true)} />
      </div>

      {/* Advisories — calendar-contextual, driven by real data. The id is
          what the topbar's Advisories button scrolls to on this page
          (App.jsx); on every other page that button opens them in a dialog. */}
      <div id={ADVISORIES_ANCHOR} ref={advisoriesRef} style={{ scrollMarginTop: 12 }}>
        <div className="card-h"><span className="section-h">Upcoming Event Advisories</span></div>
        <AdvisoriesPanel advisories={advisories} setPage={setPage} />
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
        and record it in the Tally Interface (Inventory tab &rarr; Monthly Inventory Count).
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

/** A label over the part of the page it names, with any control for it. */
function SectionLabel({ title, hint, children }) {
  return (
    <div className="step-h">
      <span className="section-h">{title}</span>
      {hint && <span className="hint">{hint}</span>}
      {children}
    </div>
  );
}

/** '20 selling days': how many days a month has sales on, which differs a lot
 *  between months, so a total is not read as like for like. */
const sellingDays = m => (m?.days ? `${num(m.days)} selling ${m.days === 1 ? 'day' : 'days'}` : null);
/** ' (a, b)' from the parts that are set, or nothing. */
const paren = parts => {
  const p = parts.filter(Boolean);
  return p.length ? ` (${p.join(', ')})` : '';
};

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
 *  comparison measured record-keeping as much as sales.
 *
 *  The month it compares with is picked beside the label (the adviser, Nov
 *  2026: compare a chosen month with the current one), the month before by
 *  default; all three tiles and the category table follow it. Because months
 *  were tallied on such different numbers of days, each total says how many
 *  days it has sales on. */
function PerformanceStep({ filters, dueByCategory, setPage, alertById, latestCount, recount }) {
  const [stoppedOpen, setStoppedOpen] = useState(false);
  const [catsOpen, setCatsOpen] = useState(false);
  // null = the month before. Unset rather than that month itself, so the
  // default asks for the same URL as the rest of the page (one cached answer).
  const [compare, setCompare] = useState(null);
  const { data: perf } = useData(
    () => getOverviewPerformance(filters, undefined, compare ?? undefined),
    [filters.supplier, filters.category, compare]);

  const label = picker => <SectionLabel title="Sale Overview">{picker}</SectionLabel>;
  if (!perf) return <>{label()}<div className="card card__pad"><Loading label="Adding up sales…" /></div></>;
  if (!perf.available) return <>{label()}<div className="card card__pad"><div className="empty">{perf.reason}</div></div></>;

  const cur = perf.latest_month, prev = perf.previous_month;
  const prevMonth = compare ?? perf.compare_month ?? prev?.month;
  const picker = (
    <span className="step-h__compare">
      <span className="hint">
        {longMonth(cur.month)}{perf.latest_month_complete ? '' : ' so far'}, the latest tallied month, compared with
      </span>
      <SearchSelect
        id="ov-compare"
        value={compare ?? perf.default_compare_month}
        options={(perf.compare_months ?? []).map(m => ({
          value: m,
          label: m === perf.default_compare_month ? `${longMonth(m)} (month before)` : longMonth(m),
        }))}
        onChange={m => setCompare(m === perf.default_compare_month ? null : m)}
        minWidth={220}
        placeholder="Search months…"
        emptyLabel="Select a Month"
      />
      {compare && (
        <button type="button" className="linkish" onClick={() => setCompare(null)}>Back to the month before</button>
      )}
    </span>
  );
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
      + (fell.due
        ? `: ${num(fell.due)} of its items ${fell.due === 1 ? 'is' : 'are'} at the reorder point, so it may have run out.`
        : '.'));
  }
  if (!steps.length) steps.push('keep the Top Sellers in stock.');

  // Month names alone ("July to October") unless the two months are in
  // different years, where "October to October" would say nothing.
  const withYear = prevMonth && prevMonth.slice(0, 4) !== cur.month.slice(0, 4);
  const curName = withYear ? longMonth(cur.month) : monthName(cur.month);
  const prevName = !prevMonth ? 'the month before' : withYear ? longMonth(prevMonth) : monthName(prevMonth);
  const through = perf.latest_month_complete ? null : `through ${usDate(perf.history_end)}`;
  const moved = mover && mover.diff !== 0 ? mover : null;
  const stopped = perf.stopped_selling?.items ?? [];
  const stoppedPhp = stopped.reduce((s, i) => s + i.previous_php, 0);
  const stoppedNames = stopped.slice(0, 2).map(i => i.item_name).join(', ')
    + (stopped.length > 2 ? ` and ${num(stopped.length - 2)} more` : '');

  return (
    <>
      {label(picker)}
      <div className="grid-3">
        <KPICard
          label={prev ? `Sales, ${prevName} to ${curName}` : `Sales, ${longMonth(cur.month)}`}
          value={vsPrev == null ? pesoK(cur.php) : signedPct(vsPrev)}
          tone={vsPrev == null ? undefined : vsPrev >= 0 ? 'ok' : 'crit'}
          sub={prev
            ? `${pesoK(prev.php)} in ${prevName}${paren([sellingDays(prev)])}, `
              + `${pesoK(cur.php)} in ${curName}${paren([sellingDays(cur), through])}`
            : `No sales tallied in ${prevName}${paren([through])}`}
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
            : prev ? `Every category sold the same as in ${prevName}` : `No sales tallied in ${prevName}`}
          icon="chart"
          onClick={cats.length ? () => setCatsOpen(true) : undefined}
          linkLabel="See all categories"
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

      {catsOpen && (
        <Modal
          open
          onClose={() => setCatsOpen(false)}
          title="What Changed This Month"
          subtitle={`By category${prev ? `, ${longMonth(prev.month)} to ${longMonth(cur.month)}` : ''}`}
          width={860}
        >
          <div className="modal-grow" style={{ overflowX: 'auto' }}>
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
                        ? (
                          <button type="button" className="linkish"
                                  onClick={() => { setCatsOpen(false); setPage?.('reorder'); }}>
                            {num(c.due)}
                          </button>
                        )
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
        </Modal>
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

/** The Top Sellers card's own filter (the Overview has no topbar filters).
 *  The presets are the backend's TOP_EARNER_RANGES; besides them, one chosen
 *  month or a range of whole months (the adviser, Nov 2026: "not just
 *  presets"), held in `from` / `to` as 'YYYY-MM'. */
const TOP_RANGES = ['This Month', 'Last 3 Months', 'Last 6 Months', 'Last 12 Months', 'All Time'];
const ONE_MONTH = 'A Specific Month';
const MONTH_RANGE = 'Custom Range';
const TOP_DEFAULT = {
  supplier: ALL_SUPPLIERS, category: ALL_CATEGORIES, range: 'Last 12 Months', from: null, to: null,
};
// Months a freshly opened custom range starts with, ending at the latest one
// (as the topbar's Custom Date Range does).
const RANGE_DEFAULT_MONTHS = 6;

/** What getOverviewPerformance takes for the filter's period: nothing for the
 *  default (the URL the rest of the page asks for, so one cached answer), a
 *  preset's name, or the months. */
function topPeriod(tf) {
  if (tf.range === ONE_MONTH) return { from: tf.from, to: tf.from };
  if (tf.range === MONTH_RANGE) return { from: tf.from, to: tf.to };
  return tf.range === TOP_DEFAULT.range ? undefined : tf.range;
}

/** 'Last 6 Months', 'July 2026' or 'May 2026 – October 2026'. */
function periodLabel(tf) {
  if (tf.range === ONE_MONTH) return longMonth(tf.from);
  if (tf.range === MONTH_RANGE) {
    return tf.from === tf.to ? longMonth(tf.from) : `${longMonth(tf.from)} – ${longMonth(tf.to)}`;
  }
  return tf.range;
}

/** Top Sellers: the items that sold most, top five, ranked by pesos (what
 *  earns most) or units (what moves fastest). It was two cards, Top 10
 *  Earners and Best Sellers, ranking the same items two ways. Every item that
 *  sold opens on "See all"; the window and the shares are behind the (i).
 *
 *  Its Filter button narrows it by supplier, category and period - the
 *  filters the Overview's topbar used to carry. Unfiltered it asks for exactly
 *  what the page already loaded (same URL, so the cached answer), which is
 *  the last 12 months store-wide. */
function TopSellers({ alertById, categoryOf, onBrowse }) {
  const [by, setBy] = useState('php');
  const [allOpen, setAllOpen] = useState(false);
  const [tf, setTf] = useState(TOP_DEFAULT);
  const [filterOpen, setFilterOpen] = useState(false);
  const { data: perf } = useData(
    () => getOverviewPerformance(tf, topPeriod(tf)),
    [tf.supplier, tf.category, tf.range, tf.from, tf.to]);
  const period = periodLabel(tf);
  const active = [tf.range !== TOP_DEFAULT.range && period,
    tf.supplier !== ALL_SUPPLIERS && tf.supplier,
    tf.category !== ALL_CATEGORIES && tf.category].filter(Boolean);
  const showSupplier = tf.supplier === ALL_SUPPLIERS;

  const filterButton = (
    <button type="button" className="btn btn--ghost btn--sm" onClick={() => setFilterOpen(true)}>
      <Icon name="filter" size={13} /> Filter
      {active.length > 0 && <span className="tag tag--info" style={{ marginLeft: 2 }}>{active.length}</span>}
    </button>
  );
  const filterModal = filterOpen && (
    <TopSellersFilter value={tf} onApply={v => { setTf(v); setFilterOpen(false); }}
                      onClose={() => setFilterOpen(false)} />
  );

  if (!perf) return <div className="card card__pad"><Loading label="Ranking items by sales…" /></div>;
  if (!perf.available) {
    return (
      <div className="card card__pad">
        <div className="card-h"><span className="section-h">Top Sellers</span>{filterButton}</div>
        <div className="empty">{perf.reason}</div>
        {filterModal}
      </div>
    );
  }

  const t = perf.top;
  const ranked = by === 'php' ? t.items : [...t.items].sort((a, b) => b.units - a.units);
  const rows = ranked.slice(0, SHORT_LIST_ROWS).map(i => ({
    name: i.item_name,
    value: by === 'php' ? i.php : i.units,
    sub: [showSupplier ? i.supplier_name : null, stockStatus(i, alertById.get(i.product_id))]
      .filter(Boolean).join(' · '),
  }));

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="card-h__title">
          <span className="section-h">Top Sellers</span>
          <TopSellersInfo t={t} small={perf.small_sellers} />
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
          {t.items.length > SHORT_LIST_ROWS && (
            <button type="button" className="linkish" onClick={() => setAllOpen(true)}>
              See all {num(t.items.length)} <Icon name="arrow" size={12} />
            </button>
          )}
          {filterButton}
        </span>
      </div>
      {filterModal}
      <div className="topsellers__bar">
        <div className="period-tabs period-tabs--sm" role="tablist" aria-label="Rank by">
          {[['php', 'Sales (₱)'], ['units', 'Units']].map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={by === k}
                    className={'period-tab' + (by === k ? ' is-active' : '')} onClick={() => setBy(k)}>
              {label}
            </button>
          ))}
        </div>
        <span className="hint">
          {[period, ...active.filter(a => a !== period)].join(' · ')} &middot; keep these in stock
        </span>
      </div>
      {rows.length
        ? <HBars data={rows} valueFmt={by === 'php' ? pesoK : num} />
        : <div className="empty">No priced sales for this filter.</div>}
      {allOpen && (
        <TopSellersModal
          t={t}
          by={by}
          onClose={() => setAllOpen(false)}
          alertById={alertById}
          categoryOf={categoryOf}
          onBrowse={onBrowse}
        />
      )}
    </div>
  );
}

/** The Top Sellers filter: period, supplier and category, applied together
 *  on Apply so the card is not re-ranked at every pick. Mounted only while
 *  open, so a cancelled edit leaves the filter in force untouched. */
function TopSellersFilter({ value, onApply, onClose }) {
  const [draft, setDraft] = useState(value);
  const { data: suppliers } = useData(() => getSuppliers(), [], []);
  const { data: categories } = useData(getCategories, [], []);
  const { data: months } = useData(getMonths, [], []);   // months with sales, oldest first
  const set = (k, v) => setDraft(d => ({ ...d, [k]: v }));
  const latest = months[months.length - 1] ?? null;

  // A month or a range starts from what was picked before, else the latest
  // month (a range: the latest few); a preset clears them.
  function setPeriod(range) {
    setDraft(d => {
      if (range === ONE_MONTH) return { ...d, range, from: d.from ?? latest, to: null };
      if (range === MONTH_RANGE) {
        return { ...d, range, from: d.from ?? months[Math.max(0, months.length - RANGE_DEFAULT_MONTHS)] ?? null,
          to: d.to ?? latest };
      }
      return { ...d, range, from: null, to: null };
    });
  }

  const monthOpts = list => list.map(m => ({ value: m, label: longMonth(m) }));
  // The app's dropdown, as in the topbar; a list held over that no longer
  // carries the value still shows it rather than something else.
  const field = (id, label, val, onChange, options, searchable = options.length > 10) => (
    <div className="field">
      <label htmlFor={`ts-${id}`}>{label}</label>
      <SearchSelect id={`ts-${id}`} variant="field" minWidth={0} searchable={searchable}
                    value={val ?? ''} onChange={onChange} placeholder={`Search ${label.toLowerCase()}…`}
                    emptyLabel="Select a Month" options={options} />
    </div>
  );
  const names = (list, v) => (list.includes(v) ? list : [v, ...list]).map(o => ({ value: o, label: o }));
  const incomplete = (draft.range === ONE_MONTH && !draft.from)
    || (draft.range === MONTH_RANGE && (!draft.from || !draft.to));
  const inRange = draft.range === MONTH_RANGE
    ? months.filter(m => m >= draft.from && m <= draft.to).length : 0;

  return (
    <Modal open onClose={onClose} width={480} title="Filter Top Sellers" overflowVisible
           subtitle="Narrow the ranking to one period, supplier or category.">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {field('range', 'Period', draft.range, setPeriod, names([...TOP_RANGES, ONE_MONTH, MONTH_RANGE], draft.range),
          false)}
        {draft.range === ONE_MONTH && field('month', 'Month', draft.from, v => set('from', v),
          monthOpts([...months].reverse()), true)}
        {draft.range === MONTH_RANGE && (
          <div>
            <div className="form-grid">
              {field('from', 'From', draft.from, v => set('from', v),
                monthOpts(months.filter(m => !draft.to || m <= draft.to)))}
              {field('to', 'To', draft.to, v => set('to', v),
                monthOpts(months.filter(m => !draft.from || m >= draft.from)))}
            </div>
            <div className="hint" style={{ marginTop: 6 }}>
              Whole months, first to last inclusive: {inRange} month{inRange === 1 ? '' : 's'} with sales.
            </div>
          </div>
        )}
        {field('supplier', 'Supplier', draft.supplier, v => set('supplier', v), names(suppliers, draft.supplier))}
        {field('category', 'Category', draft.category, v => set('category', v), names(categories, draft.category))}
      </div>
      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn btn--ink btn--sm" disabled={incomplete} onClick={() => onApply(draft)}>Apply</button>
        <button className="btn btn--ghost btn--sm" onClick={() => onApply(TOP_DEFAULT)}>Reset</button>
        <button className="btn btn--ghost btn--sm" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** The window, the shares and the two rankings, for the (i) by Top Sellers. */
function TopSellersInfo({ t, small }) {
  const sharePct = Math.round(t.share * 100);
  return (
    <InfoTip label="About Top Sellers" width={380}>
      <div className="infotip__h">Top Sellers</div>
      <p>
        {usDate(t.window_start)} &ndash; {usDate(t.window_end)}: {pesoK(t.total_php)} in sales. The top 10
        made {pct1(t.top10_share * 100)}; {num(t.n_for_share)} of the {num(t.n_selling)} items that sold
        made {sharePct}%.
      </p>
      <ul className="infotip__bullets">
        <li><b>Sales (₱)</b>: what each item earned. The items to keep in stock first.</li>
        <li><b>Units</b>: how many sold. A cheap item can sell many and earn little.</li>
      </ul>
      {small.count > 0 && (
        <p>
          {num(small.count)} {small.count === 1 ? 'item' : 'items'} sold {small.max_units} or fewer units
          ({pct1(small.share * 100)} of sales); they are under Browse all products and FSN Classification.
        </p>
      )}
    </InfoTip>
  );
}

/** Every item that sold in the window, sortable by units or sales, opened
 *  sorted the way the card was ranking. */
function TopSellersModal({ t, by, onClose, alertById, categoryOf, onBrowse }) {
  const data = (by === 'php' ? t.items : [...t.items].sort((a, b) => b.units - a.units)).map(i => ({
    ...i,
    category: i.category ?? categoryOf.get(i.product_id) ?? '—',
    now: stockStatus(i, alertById.get(i.product_id)) ?? '—',
  }));
  const columns = [
    { key: 'item_name', label: 'Item', strong: true, truncate: true, width: '31%' },
    { key: 'category', label: 'Category', truncate: true, width: '18%' },
    { key: 'units', label: 'Units', num: true, width: '11%', render: num },
    { key: 'php', label: 'Sales', num: true, strong: true, width: '13%', render: v => money(v) },
    { key: 'share', label: 'Share', num: true, width: '11%', render: v => pct1(v * 100) },
    { key: 'now', label: 'Now', width: '16%' },
  ];
  return (
    <Modal
      open
      onClose={onClose}
      title={`Top Sellers: ${num(t.items.length)} items`}
      subtitle={`Every item that sold, ${usDate(t.window_start)} – ${usDate(t.window_end)}. `
        + 'Click Units or Sales to rank by it.'}
      width={960}
    >
      {onBrowse && (
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => { onClose(); onBrowse(); }}>
            Browse all products <Icon name="arrow" size={12} />
          </button>
        </div>
      )}
      <div className="modal-grow">
        <DataTable columns={columns} data={data} pageSize={10} minWidth={760} />
      </div>
    </Modal>
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

/* -------------------------------------------------------------- actions */

/** The first few items to order, most urgent first, with the quantity. The
 *  full list, with how each figure was worked out, is Reorder Alerts. */
function OrderFirst({ due, showSupplier, setPage, latestCount, recount }) {
  const rows = due.slice(0, SHORT_LIST_ROWS);
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
        Most urgent first.
        {recount && rows.length > 0 && (
          <> Stock wrong?{' '}
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
      <p>
        Quantities are provisional: lead times are still estimates, and stock is as of the last inventory count.
      </p>
    </InfoTip>
  );
}

/** '2026-04' or '2026-04-30' -> 'Apr 2026', short enough for a column heading. */
function shortMonthYear(iso) {
  return `${shortMonth(iso.slice(0, 7)).split(' ')[0]} ${iso.slice(0, 4)}`;
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
