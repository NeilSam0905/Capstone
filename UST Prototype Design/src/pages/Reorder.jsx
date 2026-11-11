import { useEffect, useState } from 'react';
import { getStockPosition, getReorderAlerts, getReorderMethod, getReorderSeason } from '../services/dataService';
import OrderAheadModal from '../components/OrderAhead';
import ItemHistory from '../components/ItemHistory';
import { ORDER_AHEAD_ANCHOR } from '../config';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
import SearchSelect from '../components/SearchSelect';
import Pending, { Loading, PendingValue } from '../components/Pending';
import ReorderWhatIf from '../components/ReorderWhatIf';
import ReorderSettings from '../components/ReorderSettings';
import Modal from '../components/Modal';
import { num, usDate, windowDates, FSN_TONE, FSN_LABEL } from '../lib/format';

/** Fast, Slow, then Non-Moving: the order the recommendations table opens in. */
const CLASS_ORDER = { F: 0, S: 1, N: 2 };

const ALL_SUPPLIERS = 'All Suppliers';

/** The Status filter of the recommendations table. "Approaching" is the
 *  backend's own flag (stock above the reorder point by at most 20%). */
const STATUS = {
  All:         { label: 'All Statuses',    test: () => true },
  reorder:     { label: 'Reorder now',     test: i => !!i.needs_reorder },
  approaching: { label: 'Approaching ROP', test: i => !!i.approaching_rop },
  ok:          { label: 'Stock is fine',   test: i => i.current_stock != null && !i.needs_reorder && !i.approaching_rop },
  uncounted:   { label: 'No stock count',  test: i => i.current_stock == null },
};

/**
 * Reorder Alerts, kept to one list (consultation, Nov 2026: "too many boxes").
 *
 *   three tiles      what is due now, what is close, and the next busy period;
 *                    each one opens what it counts
 *   What to Order    THE list. It opens on the items to reorder now, most
 *                    urgent first, with the total to order under it - what the
 *                    old Priority card showed - and its Status filter widens it
 *                    to every item (the old Recommendations table). Its row of
 *                    buttons opens everything else as a dialog:
 *                      Plan Ahead            ordering ahead of busy periods
 *                      Stock Position        every counted item's stock
 *                      How It's Worked Out   the formulas, inputs and sources
 *                      Edit Settings         the store's own figures for an item
 */
export default function Reorder({ filters }) {
  const { data: stock, loading } = useData(() => getStockPosition(filters), [filters], null,
    { key: `reorder:stock:${filters.supplier}|${filters.category}` });
  // Bumped after reorder settings are saved or reset, to fetch the table again.
  const [reload, setReload] = useState(0);
  const { data: alerts } = useData(getReorderAlerts, [reload]);
  const { data: method } = useData(getReorderMethod, [reload]);
  const { data: season } = useData(getReorderSeason, [reload]);
  // The settings dialog: { id } while open (id null = pick an item there),
  // else null; and what its save changed.
  const [editing, setEditing] = useState(null);
  const [savedMsg, setSavedMsg] = useState(null);
  // The item open in the what-if dialog, or null.
  const [whatIf, setWhatIf] = useState(null);
  // The item whose sales history is open, or null.
  const [history, setHistory] = useState(null);
  // The dialog open from the list's buttons: 'ahead' | 'stock' | 'method' |
  // null. Arriving from the Overview's "Plan ahead" opens on Plan Ahead; the
  // request is cleared once read, so coming back later does not reopen it.
  const [panel, setPanel] = useState(() => {
    try { return sessionStorage.getItem(ORDER_AHEAD_ANCHOR) === '1' ? 'ahead' : null; } catch { return null; }
  });
  useEffect(() => {
    try { sessionStorage.removeItem(ORDER_AHEAD_ANCHOR); } catch { /* nothing to clear */ }
  }, []);

  const items = stock?.items ?? [];
  const withDos = items.filter(p => p.days_of_supply != null);
  const medianDos = withDos.length
    ? [...withDos].sort((a, b) => a.days_of_supply - b.days_of_supply)[Math.floor(withDos.length / 2)].days_of_supply
    : null;

  const alertItems = alerts?.available ? alerts.data.items : [];
  const summary = alerts?.available ? alerts.data.summary : null;
  const next = season?.available ? season.windows?.[0] : null;

  // Search and filters. The list opens on the items to reorder now.
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('reorder');
  const [fsn, setFsn] = useState('All');
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);
  const suppliers = [...new Set(alertItems.map(i => i.supplier_name).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  const needle = q.trim().toLowerCase();
  const urgentFirst = status === 'reorder';
  // Due items most urgent first - least days of cover left, then the fastest
  // seller - and numbered; any other view opens Fast, Slow, then the rest.
  const ordered = urgentFirst
    ? [...alertItems].sort((a, b) =>
      (a.days_cover_remaining ?? 0) - (b.days_cover_remaining ?? 0)
      || (b.avg_daily_demand ?? 0) - (a.avg_daily_demand ?? 0))
    : [...alertItems].sort((a, b) => (CLASS_ORDER[a.fsn_class] ?? 9) - (CLASS_ORDER[b.fsn_class] ?? 9));
  const inStatus = ordered.filter(STATUS[status].test);
  const shown = inStatus
    .map((i, n) => ({ ...i, rank: n + 1 }))
    .filter(i =>
      (!needle || i.item_name?.toLowerCase().includes(needle)
        || i.supplier_name?.toLowerCase().includes(needle))
      && (fsn === 'All' || i.fsn_class === fsn)
      && (supplier === ALL_SUPPLIERS || i.supplier_name === supplier));
  const narrowed = needle !== '' || fsn !== 'All' || supplier !== ALL_SUPPLIERS;
  const clearFilters = () => { setQ(''); setStatus('reorder'); setFsn('All'); setSupplier(ALL_SUPPLIERS); };
  const showStatus = s => {
    setStatus(s);
    requestAnimationFrame(() => document.getElementById('ro-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  const columns = [
    { key: 'item_name',     label: 'Product Name', strong: true, truncate: true, width: '26%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
    { key: 'current_stock', label: 'Current Stock', num: true, width: '12%', render: v => num(v) },
    { key: 'stock_as_of',   label: 'Counted', width: '10%', render: v => <span className="muted">{usDate(v)}</span> },
    {
      key: 'days_of_supply', label: 'Days of Supply', num: true, width: '12%',
      render: v => v == null
        ? <span className="muted">—</span>
        : <span style={v < 30 ? { color: 'var(--warn)', fontWeight: 700 } : undefined}>{num(v)}</span>,
    },
    { key: 'adus', label: 'ADUS', num: true, width: '8%', render: v => v.toFixed(3) },
    {
      key: 'censored_days', label: 'Stockout', num: true, width: '8%',
      render: v => v > 0 ? <span style={{ color: 'var(--warn)', fontWeight: 700 }}>{v}</span> : <span className="muted">—</span>,
    },
  ];

  const alertColumns = [
    ...(urgentFirst ? [{
      key: 'rank', label: '#', width: '4%',
      render: v => <span className={'rank' + (v <= 3 ? ' is-top' : '')}>{v}</span>,
    }] : []),
    {
      key: 'item_name', label: 'Product Name', strong: true, width: urgentFirst ? '20%' : '23%',
      render: (v, row) => (
        <span className="stock-item">
          <button type="button" className="item-link cell-trunc" title={`${v}: show its sales history`}
                  onClick={() => setHistory(row)}>{v}</button>
          {row.adjusted && (
            <span className="tag tag--gold"
                  title={'Uses saved settings'
                    + (row.adjusted.changed_by ? ` set by ${row.adjusted.changed_by}` : '')
                    + (row.adjusted.note ? `: ${row.adjusted.note}` : '')}>
              Adjusted
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'supplier_name', label: 'Supplier', width: urgentFirst ? '14%' : '16%',
      render: v => <span className="cell-trunc" title={v || 'Unattributed'}>{v || <span className="muted">Unattributed</span>}</span>,
    },
    {
      key: 'fsn_class', label: 'Class', width: '8%',
      render: v => v
        ? <span className={`tag tag--${FSN_TONE[v] || 'info'}`}>{FSN_LABEL[v] || v}</span>
        : <span className="muted">—</span>,
    },
    { key: 'lead_time_days', label: 'Lead Time', num: true, width: '6%', render: v => `${v}d` },
    {
      key: 'current_stock', label: 'On Hand', num: true, width: '7%',
      render: v => v == null ? <span className="muted">—</span> : num(v),
    },
    { key: 'safety_stock', label: 'Safety Stock', num: true, width: '8%', render: v => num(Math.round(v)) },
    {
      key: 'reorder_point', label: 'ROP', num: true, width: '6%',
      render: (v, row) => (
        <span style={row.current_stock != null && row.current_stock <= v ? { color: 'var(--warn)', fontWeight: 700 } : undefined}>
          {num(Math.round(v))}
        </span>
      ),
    },
    {
      key: 'suggested_order_qty', label: 'Order Qty', num: true, width: '8%',
      render: (v, row) => row.needs_reorder
        ? <span style={{ color: 'var(--warn)', fontWeight: 800 }}>{num(v)}</span>
        : <span className="muted">—</span>,
    },
    {
      // One EOQ column: the low admin-cost figure, the high goods-value one on
      // hover. Both are explained under How It's Worked Out.
      key: 'eoq', label: 'EOQ', num: true, width: '7%', sortable: false,
      render: (_v, row) => (
        <span title={row.scenarios.high_goods_value
          ? `High goods-value cost: ${num(Math.round(row.scenarios.high_goods_value.eoq))}` : undefined}>
          <Eoq scenario={row.scenarios.low_admin_cost} />
        </span>
      ),
    },
    {
      // An item with no reorder point (no sale in a year) has nothing to test.
      key: 'what_if', label: '', width: urgentFirst ? '12%' : '11%', sortable: false,
      render: (_v, row) => row.policy_state === 'priced'
        ? <button type="button" className="tag tag--info whatif-btn" onClick={() => setWhatIf(row)}
                  title="Try a busy period, a bulk order, a new lead time or new costs for this item. Not saved.">
            What if
          </button>
        : <span className="muted">—</span>,
    },
  ];

  if (loading) return <Loading label="Loading stock position…" />;

  const due = alertItems.filter(i => i.needs_reorder);
  const dueSuppliers = new Set(due.map(i => i.supplier_name || '—')).size;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard
          label="Reorder Now"
          value={summary ? num(summary.reorder_now) : <PendingValue />}
          sub={summary
            ? `${num(summary.suggested_units_total)} units from ${num(dueSuppliers)} supplier${dueSuppliers === 1 ? '' : 's'}`
            : 'At or below the reorder point'}
          icon="alert"
          tone={summary?.reorder_now ? 'crit' : undefined}
          onClick={summary?.reorder_now ? () => showStatus('reorder') : undefined}
          linkLabel="Show them"
        />
        <KPICard
          label="Approaching ROP"
          value={summary ? num(summary.approaching_rop) : <PendingValue />}
          sub="Close to it: add them to the next order from the same supplier"
          icon="bell"
          onClick={summary?.approaching_rop ? () => showStatus('approaching') : undefined}
          linkLabel="Show them"
        />
        {next ? (
          <KPICard
            label="Next Busy Period"
            value={next.kind === 'event' ? next.label.replace(/ Festivities$/, '') : next.label}
            sub={`${windowDates(next)}${next.estimated ? ' (estimated)' : ''} · `
              + (next.n_rush
                ? `${num(next.n_rush)} item${next.n_rush === 1 ? '' : 's'} need extra, order by ${shortDate(next.earliest_order_by)}`
                : 'the regular reorders cover it')}
            icon="cal"
            accent
            onClick={() => setPanel('ahead')}
            linkLabel="Plan ahead"
          />
        ) : (
          <KPICard
            label="Median Days of Supply"
            value={medianDos != null ? `${Math.round(medianDos)}d` : <PendingValue />}
            sub={`How long the typical item's stock lasts · ${withDos.length} of ${items.length} counted items`}
            icon="clock"
            accent
            onClick={() => setPanel('stock')}
            linkLabel="Stock position"
          />
        )}
      </div>

      {!alerts?.available && (
        <Pending title="Reorder alerts are not computed yet" reason={alerts?.reason}>
          <div className="pending__body">
            ROP = (forecasted daily demand × lead time) + Safety Stock, and Safety Stock = Z × σ<sub>demand</sub> × √lead
            time. Both need a per-supplier lead time and a forecast; neither is in the database. Stock Position shows
            what the data does support today: measured stock on hand and how long it lasts at the observed rate.
          </div>
          <button type="button" className="btn btn--ghost btn--sm" style={{ marginTop: 10 }}
                  onClick={() => setPanel('stock')}>
            <Icon name="box" size={13} /> Stock Position
          </button>
        </Pending>
      )}

      {alerts?.available && (
        <div className="card card__pad" id="ro-list" style={{ scrollMarginTop: 12 }}>
          <div className="card-h ro-head">
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
              What to Order
              <span className="count-pill">
                {shown.length !== alertItems.length
                  ? <><b>{num(shown.length)}</b> of {num(alertItems.length)}</>
                  : <><b>{num(alertItems.length)}</b> items</>}
              </span>
              <WhatIfGuide />
            </span>
            <span className="ro-toolbar">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPanel('ahead')}
                      title="What to order before enrollment, the start of term and Paskuhan">
                <Icon name="cal" size={13} /> Plan Ahead
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPanel('stock')}
                      title="Every counted item's stock and how long it lasts">
                <Icon name="box" size={13} /> Stock Position
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPanel('method')}
                      title="The formulas behind every figure here, their inputs and sources">
                <Icon name="info" size={13} /> How It&rsquo;s Worked Out
              </button>
              <EditSettingsButton onClick={() => setEditing({ id: null })}
                                  adjusted={alertItems.filter(i => i.adjusted).length} />
            </span>
          </div>

          <div className="filter-row">
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="ro-status">Show</label>
              <SearchSelect id="ro-status" value={status} onChange={setStatus} minWidth={190}
                            searchable={false}
                            options={Object.entries(STATUS).map(([value, s]) => ({
                              value, label: `${s.label} (${num(alertItems.filter(s.test).length)})`,
                            }))} />
            </div>
            <div className="filter-field filter-field--grow">
              <label className="filter-field__label" htmlFor="ro-search">Search</label>
              <input id="ro-search" type="search" value={q} placeholder="Item or supplier…"
                     onChange={e => setQ(e.target.value)} />
            </div>
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="ro-class">Class</label>
              <SearchSelect id="ro-class" value={fsn} onChange={setFsn} minWidth={150} searchable={false}
                            options={[
                              { value: 'All', label: 'All Classes' },
                              ...['F', 'S', 'N'].map(k => ({ value: k, label: FSN_LABEL[k] })),
                            ]} />
            </div>
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="ro-supplier">Supplier</label>
              <SearchSelect id="ro-supplier" value={supplier} onChange={setSupplier} minWidth={220}
                            searchable={suppliers.length > 10} placeholder="Search suppliers…"
                            options={[ALL_SUPPLIERS, ...suppliers].map(s => ({ value: s, label: s }))} />
            </div>
            {(narrowed || status !== 'reorder') && (
              <button type="button" className="btn btn--ghost btn--sm filter-row__clear" onClick={clearFilters}>
                Clear
              </button>
            )}
          </div>

          {shown.length === 0
            ? (
              <div className="empty">
                {status === 'reorder' && !narrowed
                  ? <>Nothing needs reordering today: every counted item is above its Reorder Point.{' '}
                    <button type="button" className="linkish" onClick={() => setStatus('All')}>Show every item</button></>
                  : 'No item matches those filters.'}
              </div>
            )
            : <DataTable columns={alertColumns} data={shown} minWidth={1100} />}

          {urgentFirst && due.length > 0 && (
            <div className="order-total">
              <span className="order-total__label">
                Total to Order
                <span className="order-total__note"> · {num(due.length)} item{due.length === 1 ? '' : 's'} across{' '}
                  {num(dueSuppliers)} supplier{dueSuppliers === 1 ? '' : 's'} · most urgent first</span>
              </span>
              <span className="order-total__val">{num(summary.suggested_units_total)} units</span>
            </div>
          )}
        </div>
      )}

      <OrderAheadModal open={panel === 'ahead'} onClose={() => setPanel(null)} onHistory={setHistory} />

      {panel === 'stock' && (
        <Modal open onClose={() => setPanel(null)} width={1060} title="Stock Position"
               subtitle={`${num(items.length)} items with an inventory count`
                 + (medianDos != null ? ` · the typical item's stock lasts ${Math.round(medianDos)} days at its recent rate` : '')}>
          <div className="modal-grow">
            {items.length === 0
              ? <div className="empty">No inventory counts match this filter.</div>
              : <DataTable columns={columns} data={items} minWidth={900} />}
          </div>
        </Modal>
      )}

      {panel === 'method' && method?.available && (
        <Modal open onClose={() => setPanel(null)} width={900} title="How It's Worked Out"
               subtitle="The four formulas behind every figure on this page. Each (i) shows its inputs, their values and where they come from.">
          <div className="modal-grow">
            <FormulaList m={method} />
            <div className="btn-row" style={{ marginTop: 14 }}>
              <EditSettingsButton onClick={() => { setPanel(null); setEditing({ id: null }); }}
                                  adjusted={alertItems.filter(i => i.adjusted).length} />
            </div>
          </div>
        </Modal>
      )}

      <ItemHistory item={history} onClose={() => setHistory(null)} />
      <ReorderWhatIf item={whatIf} onClose={() => setWhatIf(null)} />
      <ReorderSettings open={!!editing} initialId={editing?.id ?? null}
                       items={alertItems.filter(i => i.policy_state === 'priced')}
                       onClose={() => setEditing(null)}
                       onSaved={text => { setEditing(null); setSavedMsg(text); setReload(k => k + 1); }} />
      {savedMsg && (
        <Modal open width={460} title="Saved" subtitle="The change is recorded." onClose={() => setSavedMsg(null)}>
          <p style={{ margin: '0 0 16px' }}>{savedMsg}</p>
          <button type="button" className="btn btn--ink btn--sm" onClick={() => setSavedMsg(null)}>OK</button>
        </Modal>
      )}
    </div>
  );
}

/** 'Dec 25' */
const shortDate = iso => new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

/* ---------------------------------------------------- reorder settings */

/** Plain names for step5a's lead-time groups. */
const LEAD_GROUP = {
  simple_dtf_puff_shirt: 'printed shirts, jerseys, polos, tees',
  embroidered_shirt: 'embroidered shirts',
  jacket: 'jackets, windbreakers',
  'default (confirmed non-apparel)': 'non-apparel',
  'default (uncategorized)': 'everything else',
};
const leadGroup = c => LEAD_GROUP[c] ?? (String(c).startsWith('measured') ? 'measured from deliveries' : c);

const peso = v => `₱${Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 })}`;

/** How It's Worked Out: the four formulas behind the page, each with an (i)
 *  that opens its inputs, their values and where they come from, read live
 *  from the pipeline (GET /api/reorder/method) so the numbers follow every
 *  run. Shown in a dialog from the What to Order card (it was a folded card
 *  of its own), with Edit Reordering Settings beside it, next to the
 *  assumptions those settings replace. */
function FormulaList({ m }) {
  // Lead times, one line per number of days: "18 days: embroidered shirts,
  // non-apparel, everything else (222 items)".
  const byDays = {};
  for (const lt of m.lead_times) {
    (byDays[lt.days] ??= { groups: [], items: 0 });
    byDays[lt.days].groups.push(leadGroup(lt.category));
    byDays[lt.days].items += lt.items;
  }
  const b = m.buffers;
  const q = Math.round((m.buffer_quantile ?? 0.8) * 100);

  return (
      <div className="formula-list">
        <Formula name="Reorder Point" text="average daily demand × lead time + Safety Stock">
          <MethodBlock title="Reorder Point (ROP)" formula="ROP = average daily demand × lead time + Safety Stock"
          note="The stock level that should last until a new delivery arrives, with a buffer for surprises."
          rule="Stock at or below the Reorder Point means order now.">
          <MethodRow input="Average daily demand" value="Each item's own sales per day over the last 365 days">
            <b>The store&rsquo;s tally sheets.</b> Days with nothing recorded are left out.
          </MethodRow>
          <MethodRow input="Lead time" value={
            <div className="lead-list">
              {Object.entries(byDays).map(([d, g]) => (
                <div className="lead-list__row" key={d}>
                  <span className="lead-list__days">{d} days</span>
                  <span className="lead-list__what">{g.groups.join(', ')}</span>
                  <span className="lead-list__n">{num(g.items)} items</span>
                </div>
              ))}
            </div>
          }>
            <b>The USTore&rsquo;s own estimates by item type.</b> Once a supplier has {m.min_deliveries} logged
            deliveries, its median delivery time replaces the estimate.
          </MethodRow>
          <MethodRow input="Safety Stock" value="The cushion for a slow delivery or a busy week">
            Worked out per item: see the Safety Stock (i).
          </MethodRow>
        </MethodBlock>
        </Formula>
        <Formula name="Safety Stock" text={`the forecast miss over the lead time that ${q}% of past misses stayed under`}>
          <MethodBlock title="Safety Stock" formula={`Safety Stock = the forecast miss over the lead time that ${q}% of past misses stayed under`}
          note="Measured from each item's own history rather than assumed from a bell curve."
          rule="A bigger buffer means fewer stockouts, but more money sitting in stock.">
          <MethodRow input="Measured buffer" value={`${num(b.measured)} items`}>
            <b>Each item&rsquo;s own past forecast misses</b> over its lead time: the buffer covers {q}% of them.
          </MethodRow>
          {b.fallback > 0 && (
            <MethodRow input="Z × σ × √lead time" value={`${num(b.fallback)} items · Z ${m.z.F} Fast, ${m.z.S} Slow`}>
              Used only when an item has too little history to measure its misses.
            </MethodRow>
          )}
          <MethodRow input="Not worth stocking" value={`${num(b.not_stockable)} items · no buffer`}>
            Sells too rarely for extra stock to pay off. The Reorder Point covers normal demand only.
          </MethodRow>
          <MethodRow input="No Reorder Point" value={`${num(b.none)} items`}>
            Too few sale days to learn a selling rate. Shown as unknown, never as zero.
          </MethodRow>
        </MethodBlock>
        </Formula>
        <Formula name="Order Quantity" text={`Reorder Point + ${m.review_period_days} days of demand − stock on hand`}>
          <MethodBlock title="Order Quantity staff actually see"
          formula={`Order = Reorder Point + ${m.review_period_days} days of demand − stock on hand`}
          note="Brings stock back up to the Reorder Point plus one more month of sales."
          rule="Nothing is ordered while stock is above the Reorder Point.">
          <MethodRow input={`${m.review_period_days} days of demand`} value="Average daily demand × 30">
            One review period, so an order lasts until the next one is placed.
          </MethodRow>
          <MethodRow input="Stock on hand" value="The latest count">
            <b>The Tally Interface</b> inventory counts, or the inventory workbook when it is newer.
          </MethodRow>
        </MethodBlock>
        </Formula>
        <Formula name="EOQ" text="√(2 × yearly demand × ordering cost ÷ holding cost)">
          <MethodBlock title="EOQ" formula="EOQ = √(2 × D × S ÷ H)"
          note="With these costs, EOQ comes out larger than a whole year of demand for almost every item."
          rule="EOQ is for reference only. Buy the Order Quantity.">
          <MethodRow input="D, yearly demand" value="365 × average daily demand">
            <b>The store&rsquo;s tally sheets</b>, as above.
          </MethodRow>
          <MethodRow input="S, ordering cost (low)" value={`${peso(m.ordering_cost_low)} per order`}>
            <b>Assumption:</b> the midpoint of a plausible ₱500–2,000 admin cost per order (staff time,
            paperwork, a call to the supplier).
          </MethodRow>
          <MethodRow input="S, ordering cost (high)" value={`${peso(m.ordering_cost_high)} per order`}>
            <b>The USTore&rsquo;s own figure</b> of ₱200,000–500,000 per month, taken at the low end as if it were
            the cost of one order.
          </MethodRow>
          <MethodRow input="H, holding cost" value={`${peso(m.holding_cost)} per unit per year`}>
            <b>Worked out:</b> {Math.round(m.holding_rate * 100)}% yearly holding rate (assumption)
            × {peso(m.inventory_value_mid)} inventory value (midpoint of the store&rsquo;s{' '}
            {peso(m.inventory_value_low)}–{peso(m.inventory_value_high)}) ÷ {num(m.units_on_hand)} units on hand
            (inventory workbook, March 2026).
          </MethodRow>
        </MethodBlock>
        </Formula>
      </div>
  );
}

/** One formula, and an (i) that opens its inputs, values and sources. */
function Formula({ name, text, children }) {
  return (
    <div className="formula">
      <span className="formula__name">{name}</span>
      <span className="formula__eq">=</span>
      <span className="formula__text">{text}</span>
      <span className="formula__tip">
        <InfoTip label={`How the ${name.toLowerCase()} is worked out`} width={560}>
          <div className="method-stack">{children}</div>
        </InfoTip>
      </span>
    </div>
  );
}

/** One formula's tip: a heading, the formula, what it means in a sentence,
 *  then each input as a short stacked block - its name, its value, and
 *  where it comes from - read top to bottom rather than across a table, and
 *  the one thing to remember as a bold line. */
function MethodBlock({ title, formula, note, rule, children }) {
  return (
    <>
      <div className="infotip__h">{title}</div>
      <p className="tip-formula">{formula}</p>
      {note && <p>{note}</p>}
      <div className="infotip__h" style={{ marginTop: 12 }}>The inputs</div>
      <div className="input-list">{children}</div>
      {rule && <p className="reliab__rule"><Icon name="alert" size={13} /> {rule}</p>}
    </>
  );
}

function MethodRow({ input, value, children }) {
  return (
    <div className="input-row">
      <div className="input-row__name">{input}</div>
      <div className="input-row__value">{value}</div>
      {children && <div className="input-row__src"><span className="input-row__label">Source</span>{children}</div>}
    </div>
  );
}

/** Opens Edit reordering settings (ReorderSettings). Black, as the app's
 *  actions are, so it is not read as an explanation like the formulas' (i).
 *  `adjusted` counts the items already using saved settings. */
function EditSettingsButton({ onClick, adjusted = 0 }) {
  return (
    <button type="button" className="btn btn--ink btn--sm settings-btn" onClick={onClick}
            title="Set an item's lead time, Safety Stock or costs. Saved, unlike What if.">
      <Icon name="pencil" size={13} /> Edit Settings
      {adjusted > 0 && <span className="settings-btn__n" title={`${adjusted} adjusted`}>{num(adjusted)}</span>}
    </button>
  );
}

/* ------------------------------------------------------------- what-if */

/** The Recommendations card's guide to the What if button: a gold pill that
 *  opens a short, plain explanation, in place of a one-line hint that listed
 *  four things to try without saying what any of them would show. */
function WhatIfGuide() {
  const rows = [
    ['gold', 'Busy period', 'Enrollment or the start of a term, when sales run above a regular day. See how much more stock to keep.'],
    ['info', 'Bulk order', 'An office or organization buys many at once. See whether it leaves the shelf short.'],
    ['warn', 'Lead time', 'The supplier takes more (or fewer) days to deliver. See how the Reorder Point moves.'],
    ['ok', 'Costs', 'A different cost per order or cost of keeping stock. This changes the EOQ only.'],
  ];
  return (
    <InfoTip text="What if?" label="How to use What if" width={440}>
      <div className="infotip__h">Test a change before you order</div>
      <p>
        Every item in this table has a <b>What if</b> button. It opens that item so you can change one
        assumption and compare the figures <b>now</b> with the figures <b>if</b> it happens.
      </p>
      <div className="explain" style={{ marginTop: 10 }}>
        {rows.map(([tone, name, text]) => (
          <div className="explain__row" key={name}>
            <span className={`tag tag--${tone}`}>{name}</span>
            <p>{text}</p>
          </div>
        ))}
      </div>
    </InfoTip>
  );
}

/** EOQ cell. Struck through in muted type when it exceeds a year of demand,
 *  which under the current provisional costs is nearly every SKU - the point
 *  being that the figure is present and honest, not that it is orderable. */
function Eoq({ scenario }) {
  if (!scenario) return <span className="muted">—</span>;
  return scenario.exceeds_annual_demand
    ? <span className="muted" title="Exceeds a full year of demand — not a usable Order Quantity yet">
        {num(scenario.eoq)}
      </span>
    : <span>{num(scenario.eoq)}</span>;
}
