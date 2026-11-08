import { useState } from 'react';
import { getStockPosition, getReorderAlerts, getReorderMethod } from '../services/dataService';
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
import { num, usDate, FSN_TONE, FSN_LABEL } from '../lib/format';

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

export default function Reorder({ filters }) {
  const { data: stock, loading } = useData(() => getStockPosition(filters), [filters], null,
    { key: `reorder:stock:${filters.supplier}|${filters.category}` });
  // Bumped after reorder settings are saved or reset, to fetch the table again.
  const [reload, setReload] = useState(0);
  const { data: alerts } = useData(getReorderAlerts, [reload]);
  const { data: method } = useData(getReorderMethod, [reload]);
  // The settings dialog: { id } while open (id null = pick an item there),
  // else null; and what its save changed.
  const [editing, setEditing] = useState(null);
  const [savedMsg, setSavedMsg] = useState(null);
  // The item open in the what-if dialog, or null.
  const [whatIf, setWhatIf] = useState(null);

  const items = stock?.items ?? [];
  const withDos = items.filter(p => p.days_of_supply != null);
  const medianDos = withDos.length
    ? [...withDos].sort((a, b) => a.days_of_supply - b.days_of_supply)[Math.floor(withDos.length / 2)].days_of_supply
    : null;

  // current_stock, needs_reorder and suggested_order_qty now come from
  // /api/reorder itself (it joins catalog stats server-side), so this screen
  // no longer re-derives them from /api/stock.
  const alertItems = alerts?.available ? alerts.data.items : [];
  // The recommendations table opens on the Fast items - the ones a stockout
  // costs most - then Slow, then the rest; within a class the API's name
  // order stands (Array.sort is stable). A column heading still re-sorts.
  const byClass = [...alertItems].sort(
    (a, b) => (CLASS_ORDER[a.fsn_class] ?? 9) - (CLASS_ORDER[b.fsn_class] ?? 9));
  const summary = alerts?.available ? alerts.data.summary : null;

  // Search and filters for the recommendations table. They narrow what the
  // table shows only: the priority list, the cards and the totals above
  // still count every item.
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('All');
  const [fsn, setFsn] = useState('All');
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);
  const suppliers = [...new Set(alertItems.map(i => i.supplier_name).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  const needle = q.trim().toLowerCase();
  const shown = byClass.filter(i =>
    (!needle || i.item_name?.toLowerCase().includes(needle)
      || i.supplier_name?.toLowerCase().includes(needle))
    && STATUS[status].test(i)
    && (fsn === 'All' || i.fsn_class === fsn)
    && (supplier === ALL_SUPPLIERS || i.supplier_name === supplier));
  const filtering = needle !== '' || status !== 'All' || fsn !== 'All' || supplier !== ALL_SUPPLIERS;
  const clearFilters = () => { setQ(''); setStatus('All'); setFsn('All'); setSupplier(ALL_SUPPLIERS); };

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
    {
      key: 'item_name', label: 'Product Name', strong: true, width: '21%',
      render: (v, row) => (
        <span className="stock-item">
          <span className="cell-trunc" title={v}>{v}</span>
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
      key: 'fsn_class', label: 'Class', width: '9%',
      render: v => v
        ? <span className={`tag tag--${FSN_TONE[v] || 'info'}`}>{FSN_LABEL[v] || v}</span>
        : <span className="muted">—</span>,
    },
    { key: 'lead_time_days', label: 'Lead Time', num: true, width: '7%', render: v => `${v}d` },
    {
      key: 'current_stock', label: 'On Hand', num: true, width: '8%',
      render: v => v == null ? <span className="muted">—</span> : num(v),
    },
    { key: 'safety_stock', label: 'Buffer Stock', num: true, width: '9%', render: v => num(Math.round(v)) },
    {
      key: 'reorder_point', label: 'ROP', num: true, width: '7%',
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
      key: 'eoq_low', label: 'EOQ · low admin', num: true, width: '10%',
      render: (_v, row) => <Eoq scenario={row.scenarios.low_admin_cost} />,
    },
    {
      key: 'eoq_high', label: 'EOQ · high goods-value', num: true, width: '11%',
      render: (_v, row) => <Eoq scenario={row.scenarios.high_goods_value} />,
    },
    {
      // An item with no reorder point (no sale in a year) has nothing to test.
      key: 'what_if', label: '', width: '10%', sortable: false,
      render: (_v, row) => row.policy_state === 'priced'
        ? <button type="button" className="tag tag--info whatif-btn" onClick={() => setWhatIf(row)}
                  title="Try a busy period, a bulk order, a new lead time or new costs for this item. Not saved.">
            What if
          </button>
        : <span className="muted">—</span>,
    },
  ];

  if (loading) return <Loading label="Loading stock position…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard
          label="Reorder Now"
          value={summary ? num(summary.reorder_now) : <PendingValue />}
          sub="Stock has reached the order-now level — order these today"
          icon="alert"
        />
        <KPICard
          label="Approaching ROP"
          value={summary ? num(summary.approaching_rop) : <PendingValue />}
          sub="Not due yet, but close — add to the next order from the same supplier"
          icon="bell"
        />
        <KPICard
          label="Median Days of Supply"
          value={medianDos != null ? `${Math.round(medianDos)}d` : <PendingValue />}
          sub={`How long the typical item's stock lasts at its recent selling rate · `
            + `${withDos.length} of ${items.length} counted items`}
          icon="clock"
          accent
        />
      </div>

      {summary && <ReorderAdvice items={alertItems} summary={summary} />}

      {!alerts?.available && (
        <Pending title="Reorder alerts are not computed yet" reason={alerts?.reason}>
          <div className="pending__body">
            ROP = (forecasted daily demand × lead time) + Safety Stock, and Safety Stock = Z × σ<sub>demand</sub> × √lead
            time. Both need a per-supplier lead time and a forecast; neither is in the database. The table below is what
            the data does support today: measured stock on hand and how long it lasts at the observed rate.
          </div>
        </Pending>
      )}

      {alerts?.available && method?.available && (
        <ReorderMethodCard m={method} adjusted={alertItems.filter(i => i.adjusted).length}
                           onEdit={() => setEditing({ id: null })} />
      )}

      {alerts?.available && (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
              Reorder Recommendations
              <span className="count-pill">
                {shown.length !== byClass.length
                  ? <><b>{num(shown.length)}</b> of {num(byClass.length)}</>
                  : <><b>{num(byClass.length)}</b> items</>}
              </span>
            </span>
            <WhatIfGuide />
          </div>

          <div className="filter-row">
            <div className="filter-field filter-field--grow">
              <label className="filter-field__label" htmlFor="ro-search">Search</label>
              <input id="ro-search" type="search" value={q} placeholder="Item or supplier…"
                     onChange={e => setQ(e.target.value)} />
            </div>
            <div className="filter-field">
              <label className="filter-field__label" htmlFor="ro-status">Status</label>
              <SearchSelect id="ro-status" value={status} onChange={setStatus} minWidth={170}
                            searchable={false}
                            options={Object.entries(STATUS).map(([value, s]) => ({ value, label: s.label }))} />
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
            {filtering && (
              <button type="button" className="btn btn--ghost btn--sm filter-row__clear" onClick={clearFilters}>
                Clear
              </button>
            )}
          </div>

          {shown.length === 0
            ? <div className="empty">No item matches those filters.</div>
            : <DataTable columns={alertColumns} data={shown} minWidth={1100} />}
        </div>
      )}
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

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Stock Position</span>
          <span className="hint">
            {items.length} items with an inventory count
          </span>
        </div>
        {items.length === 0
          ? <div className="empty">No inventory counts match this filter.</div>
          : <DataTable columns={columns} data={items} minWidth={900} />}
      </div>
    </div>
  );
}

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

const FORMULAS_OPEN_KEY = 'reorder.formulasOpen';

/** "Reorder Formulas & Calculation": the four formulas behind the page, each
 *  with an (i) that opens its inputs, their values and where they come from,
 *  read live from the pipeline (GET /api/reorder/method) so the numbers
 *  follow every run. The card starts folded (opening it is remembered per
 *  browser), and is
 *  also where the store sets its own figures for an item (Edit Reordering
 *  Settings), next to the assumptions they replace. */
function ReorderMethodCard({ m, adjusted, onEdit }) {
  const [open, setOpen] = useState(() => {
    // Folded unless this browser left it open: the formulas are reference,
    // not something read on every visit.
    try { return localStorage.getItem(FORMULAS_OPEN_KEY) === '1'; } catch { return false; }
  });
  const toggle = e => {
    setOpen(e.currentTarget.open);
    try { localStorage.setItem(FORMULAS_OPEN_KEY, e.currentTarget.open ? '1' : '0'); } catch { /* private window */ }
  };

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
    <details className="card card__pad collapse collapse--card formulas-card" open={open} onToggle={toggle}>
      <summary>
        <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          Reorder Formulas &amp; Calculation
          {adjusted > 0 && <span className="count-pill"><b>{num(adjusted)}</b> adjusted</span>}
        </span>
        {/* In the summary so it stays in reach while the card is folded;
            preventDefault keeps its click from folding the card too. */}
        <EditSettingsButton onClick={e => { e.preventDefault(); onEdit(); }} />
      </summary>

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
    </details>
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
 *  actions are, so it is not read as an explanation like the formulas' (i). */
function EditSettingsButton({ onClick }) {
  return (
    <button type="button" className="btn btn--ink btn--sm settings-btn" onClick={onClick}
            title="Set an item's lead time, Safety Stock or costs. Saved, unlike What if.">
      <Icon name="pencil" size={13} /> Edit Reordering Settings
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

/* ------------------------------------------------------------- advisory */

/** "What do we buy today, and how many."
 *
 *  The tables further down are the evidence; this is the answer. It exists
 *  because the screen used to open with a formula reference and a 208-row
 *  table, which is the right material for the write-up and the wrong thing to
 *  hand someone who has to place an order this morning.
 *
 *  Built from the Batch Sales Report's markup on purpose - the same dark
 *  header bar, `.tbl` item table and gold total bar. Those two screens are the
 *  pair of things the store actually acts on (what to buy, what was sold), so
 *  they should read the same way. The title and supplier count sit inside the
 *  dark bar rather than in a card above it, so the whole advisory is one card.
 *  `.report-items` caps the body at ten rows and scrolls with the header
 *  pinned, exactly as it does on the report.
 *
 *  The quantity is the backend's `suggested_order_qty` (an order-up-to level:
 *  reorder point + one review period of demand), NOT EOQ. See the endpoint's
 *  ORDER_QTY_NOTE - under the provisional cost inputs EOQ comes out larger
 *  than a year of demand for 204 of 208 SKUs, so it would tell staff to buy
 *  years of stock. EOQ stays in the recommendations table below, flagged,
 *  rather than being quietly dropped. That note, and the count of items with
 *  no stock figure at all, are still on the API (`summary.order_qty_note`,
 *  `summary.no_stock_count`) - they are just not printed under this table. */
function ReorderAdvice({ items, summary }) {
  // Most urgent first: least days of cover left, then the fastest seller.
  // Items already at zero all tie at 0 cover, so demand rate breaks it.
  const due = items
    .filter(i => i.needs_reorder)
    .sort((a, b) =>
      (a.days_cover_remaining ?? 0) - (b.days_cover_remaining ?? 0)
      || (b.avg_daily_demand ?? 0) - (a.avg_daily_demand ?? 0));

  if (due.length === 0) {
    return (
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <Icon name="check" size={15} /> Nothing needs reordering today
          </span>
        </div>
        <div className="hint">
          Every item with a stock count is above its Reorder Point.
          {summary.no_stock_count > 0 && (
            <> {num(summary.no_stock_count)} of {num(summary.priced_skus)} priced items have no stock count,
            so they could not be checked — record counts in the Tally Interface to include them.</>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="card card__pad">
      {/* An ordinary card header and an ordinary table. This card used to be
          built from the Batch Report's markup - a black title bar and a solid
          gold total bar - which made the one screen a person reads every
          morning the loudest thing in the app. The only thing that keeps a
          tint now is the total, because it is the only figure here that is a
          conclusion rather than a row of data. */}
      <div className="card-h">
        <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <Icon name="alert" size={15} /> Priority Items to Reorder
        </span>
        <span className="hint">
          Most Urgent First · {num(summary.suppliers_affected)}{' '}
          Supplier{summary.suppliers_affected === 1 ? '' : 's'} Affected
        </span>
      </div>

      <div className="report-items">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 46 }}>#</th>
              <th>Item</th>
              <th>Supplier</th>
              <th className="num">On Hand</th>
              <th className="num">Reorder Point</th>
              <th className="num">Order</th>
            </tr>
          </thead>
          <tbody>
            {due.map((i, n) => (
              <tr key={i.product_id}>
                {/* The rank is the sort made visible. The list is already in
                    urgency order, but a reader scrolling a scrolled body has
                    no other way to tell where in that order they are. */}
                <td>
                  <span className={'rank' + (n < 3 ? ' is-top' : '')}>{n + 1}</span>
                </td>
                <td className="strong">
                  <span className="cell-trunc" style={{ '--trunc': '340px' }} title={i.item_name}>
                    {i.item_name}
                  </span>
                </td>
                <td>
                  <span className="cell-trunc" style={{ '--trunc': '240px' }} title={i.supplier_name || 'Unattributed'}>
                    {i.supplier_name || <span className="muted">Unattributed</span>}
                  </span>
                </td>
                <td className="num">{num(i.current_stock)}</td>
                <td className="num">{num(i.reorder_point)}</td>
                <td className="num report-order">{num(i.suggested_order_qty)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="order-total">
        <span className="order-total__label">
          Total to Order
          <span className="order-total__note"> · {num(due.length)} item{due.length === 1 ? '' : 's'} across{' '}
          {num(summary.suppliers_affected)} supplier{summary.suppliers_affected === 1 ? '' : 's'}</span>
        </span>
        <span className="order-total__val">{num(summary.suggested_units_total)} units</span>
      </div>
    </div>
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
