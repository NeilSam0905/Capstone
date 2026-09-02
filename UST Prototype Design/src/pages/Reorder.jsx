import { getStockPosition, getReorderAlerts, getMeta } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { num, usDate } from '../lib/format';

const SCENARIO_LABEL = { low_admin_cost: 'Low (admin cost)', high_goods_value: 'High (goods value)' };

export default function Reorder({ filters }) {
  const { data: stock, loading } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: meta } = useData(getMeta, []);

  const items = stock?.items ?? [];
  const withDos = items.filter(p => p.days_of_supply != null);
  const medianDos = withDos.length
    ? [...withDos].sort((a, b) => a.days_of_supply - b.days_of_supply)[Math.floor(withDos.length / 2)].days_of_supply
    : null;

  // current_stock, needs_reorder and suggested_order_qty now come from
  // /api/reorder itself (it joins catalog stats server-side), so this screen
  // no longer re-derives them from /api/stock.
  const alertItems = alerts?.available ? alerts.data.items : [];
  const summary = alerts?.available ? alerts.data.summary : null;

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
    { key: 'item_name', label: 'Product Name', strong: true, truncate: true, width: '20%' },
    { key: 'fsn_class', label: 'Class', width: '6%' },
    { key: 'lead_time_days', label: 'Lead Time', num: true, width: '8%', render: v => `${v}d` },
    {
      key: 'current_stock', label: 'On Hand', num: true, width: '9%',
      render: v => v == null ? <span className="muted">—</span> : num(v),
    },
    { key: 'safety_stock', label: 'Buffer Stock', num: true, width: '9%', render: v => num(Math.round(v)) },
    {
      key: 'reorder_point', label: 'ROP', num: true, width: '8%',
      render: (v, row) => (
        <span style={row.current_stock != null && row.current_stock <= v ? { color: 'var(--warn)', fontWeight: 700 } : undefined}>
          {num(Math.round(v))}
        </span>
      ),
    },
    {
      key: 'suggested_order_qty', label: 'Order Qty', num: true, width: '9%',
      render: (v, row) => row.needs_reorder
        ? <span style={{ color: 'var(--warn)', fontWeight: 800 }}>{num(v)}</span>
        : <span className="muted">—</span>,
    },
    {
      key: 'eoq_low', label: 'EOQ · low admin', num: true, width: '10%',
      render: (_v, row) => <Eoq scenario={row.scenarios.low_admin_cost} />,
    },
    {
      key: 'eoq_high', label: 'EOQ · high goods-value', num: true, width: '12%',
      render: (_v, row) => <Eoq scenario={row.scenarios.high_goods_value} />,
    },
    {
      key: 'sigma_source', label: 'Demand Estimate', width: '9%',
      render: v => v === 'cv_fallback'
        ? <span className="tag tag--warn" title="Not enough sales history to measure variability directly - estimated from typical variation instead">Estimated</span>
        : <span className="muted">Measured</span>,
    },
  ];

  if (loading) return <Loading label="Loading stock position…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard
          label="Reorder Now"
          value={summary ? num(summary.reorder_now) : <PendingValue />}
          sub="On hand at or below ROP"
          icon="alert"
        />
        <KPICard
          label="Approaching ROP"
          value={summary ? num(summary.approaching_rop) : <PendingValue />}
          sub="Within 20% above ROP"
          icon="bell"
        />
        <KPICard
          label="Median Days of Supply"
          value={medianDos != null ? `${Math.round(medianDos)}d` : <PendingValue />}
          sub={`${withDos.length} of ${items.length} counted items have a days-of-supply figure`}
          icon="clock"
          accent
        />
      </div>

      {summary && <ReorderAdvice items={alertItems} summary={summary} />}

      {!alerts?.available && (
        <Pending title="Reorder alerts are not computed yet" reason={alerts?.reason}>
          <div className="pending__body">
            ROP = (forecasted daily demand × lead time) + safety stock, and safety stock = Z × σ<sub>demand</sub> × √lead
            time. Both need a per-supplier lead time and a forecast; neither is in the database. The table below is what
            the data does support today: measured stock on hand and how long it lasts at the observed rate.
          </div>
        </Pending>
      )}

      {/* Collapsed by default. The formulas matter for the write-up and the
          defence, but they are reference material - opening the screen with
          them buried the one thing a store manager needs, which is now the
          card at the top. */}
      <details className="card card__pad">
        <summary className="section-h" style={{ cursor: 'pointer', listStyle: 'revert' }}>
          How these numbers are calculated
        </summary>
        <div className="grid-2" style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <FormulaLine label="Reorder Point (ROP)" formula="(Forecasted Daily Demand × Lead Time) + Safety Stock" />
            <FormulaLine label="Safety Stock" formula="Z × σ_demand × √Lead Time" />
            <FormulaLine label="EOQ" formula="√(2 × Annual Demand × Ordering Cost / Holding Cost)" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="grid-2">
              <Param label="Z-score" value={alerts?.available ? 'F 1.65 / S 1.04' : '—'} note="95% service (Fast) / 85% (Slow)" />
              <Param label="Lead Time" value={alerts?.available ? '14–28 days' : '—'} note="By garment category, per SKU" />
            </div>
            {alerts?.available ? (
              <div className="notice notice--warn">
                <b>Provisional:</b> lead time, holding cost and both ordering-cost interpretations below are estimates
                pending confirmation at the USTore site visit (Block 5) — not yet what the store actually pays. The
                {' '}{num(summary?.priced_skus ?? alertItems.length)} priced SKUs cover Fast/Slow items with a
                positive 30-day demand basis.
              </div>
            ) : (
              <div className="notice notice--warn">
                <b>Note:</b> Dim_Parameters is empty. Lead times, ordering cost and holding cost come from the USTore
                site visit; until then these formulas have no inputs and this screen shows no reorder recommendation.
              </div>
            )}
          </div>
        </div>
      </details>

      <Glossary />

      {alerts?.available && (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Reorder Recommendations</span>
            <span className="hint">
              ROP and Safety Stock don't depend on ordering cost, so they're the same either way — EOQ is shown under
              both interpretations because it swings ~12.6× between them ({SCENARIO_LABEL.low_admin_cost} vs.{' '}
              {SCENARIO_LABEL.high_goods_value})
            </span>
          </div>
          <DataTable columns={alertColumns} data={alertItems} minWidth={1180} />
        </div>
      )}

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Stock Position</span>
          <span className="hint">
            {items.length} items with an inventory count
            {meta && ` · inventory covers ${meta.products_with_stock} of ${meta.products} products`}
          </span>
        </div>
        {items.length === 0
          ? <div className="empty">No inventory counts match this filter.</div>
          : <DataTable columns={columns} data={items} minWidth={900} />}
      </div>
    </div>
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
          Every item with a stock count is above its reorder point.
          {summary.no_stock_count > 0 && (
            <> {num(summary.no_stock_count)} of {num(summary.priced_skus)} priced items have no stock count,
            so they could not be checked — record counts in the Tally Interface to include them.</>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ overflow: 'hidden' }}>
      {/* Header lives in the dark bar itself rather than in a card above it,
          so the whole advisory is one card. */}
      <div className="report-supplier"
           style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Icon name="alert" size={14} /> Reorder Today — most urgent first
        </span>
        <span style={{ opacity: .75 }}>
          {num(summary.suppliers_affected)} supplier{summary.suppliers_affected === 1 ? '' : 's'} affected
        </span>
      </div>

      <div className="report-items">
        <table className="tbl">
          <thead>
            <tr>
              <th>Item</th>
              <th>Supplier</th>
              <th className="num">On Hand</th>
              <th className="num">Reorder Point</th>
              <th className="num">Order</th>
            </tr>
          </thead>
          <tbody>
            {due.map(i => (
              <tr key={i.product_id}>
                <td className="strong">
                  <span className="cell-trunc" style={{ '--trunc': '360px' }} title={i.item_name}>
                    {i.item_name}
                  </span>
                </td>
                <td>
                  <span className="cell-trunc" style={{ '--trunc': '260px' }} title={i.supplier_name || 'Unattributed'}>
                    {i.supplier_name || <span className="muted">Unattributed</span>}
                  </span>
                </td>
                <td className="num">{num(i.current_stock)}</td>
                <td className="num">{num(i.reorder_point)}</td>
                <td className="num strong" style={{ color: 'var(--gold-deep)' }}>
                  {num(i.suggested_order_qty)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="report-subtotal">
        <span className="report-subtotal__label">
          Total to Order
          <span className="report-subtotal__note"> · {num(due.length)} item{due.length === 1 ? '' : 's'} across{' '}
          {num(summary.suppliers_affected)} supplier{summary.suppliers_affected === 1 ? '' : 's'}</span>
        </span>
        <span className="report-subtotal__val">{num(summary.suggested_units_total)} units</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- glossary */

const GLOSSARY_TERMS = [
  { term: 'ROP (Reorder Point)', def: 'The stock level that means "order now." Once on-hand stock drops to or below this number, a new order should be placed.' },
  { term: 'Buffer Stock', def: 'Extra stock kept on top of expected need, as a cushion against demand being higher than usual or a delivery arriving late. Also called "safety stock."' },
  { term: 'EOQ (Economic Order Quantity)', def: 'The theoretical "ideal" quantity to order at once that minimizes total ordering + holding cost. Shown for reference — see the note above the table for why it isn\'t the suggested order quantity here.' },
  { term: 'Lead Time', def: "How many days it takes for a new order to arrive after it's placed, counted from the supplier." },
  { term: 'ADUS (Average Daily Unit Sales)', def: 'The average number of units sold per day, based on observed sales history.' },
  { term: 'Days of Supply', def: 'How many more days the current stock is expected to last, at the recent selling rate. Lower means it runs out sooner.' },
  { term: 'Median Days of Supply', def: "The \"typical\" item's days of supply — take every item's days-of-supply figure, sort them, and pick the middle one. Used instead of an average so one unusual item (e.g. a huge overstock) doesn't skew the picture." },
  { term: 'Demand Estimate: Measured vs. Estimated', def: 'Whether the demand-variability figure behind Buffer Stock came from enough real sales history ("Measured") or had to be approximated because the item hasn\'t sold enough yet ("Estimated").' },
  { term: 'On Hand', def: 'The most recently counted stock quantity for this item.' },
  { term: 'Order Qty', def: "The suggested amount to order right now: enough to bring stock up to the reorder point plus one month's worth of expected demand." },
];

/** Plain-language reference for the technical column headers on this page.
 *  Collapsed by default, same treatment as "How these numbers are
 *  calculated" below it - the glossary is what a term MEANS, the formulas
 *  card is how it's CALCULATED; kept as two separate sections so neither
 *  gets long enough to bury the other. */
function Glossary() {
  return (
    <details className="card card__pad">
      <summary className="section-h" style={{ cursor: 'pointer', listStyle: 'revert' }}>
        What do these terms mean?
      </summary>
      <div className="grid-2" style={{ marginTop: 14, columnGap: 24, rowGap: 14 }}>
        {GLOSSARY_TERMS.map(({ term, def }) => (
          <div key={term}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{term}</div>
            <div className="hint" style={{ marginTop: 2 }}>{def}</div>
          </div>
        ))}
      </div>
    </details>
  );
}

/** EOQ cell. Struck through in muted type when it exceeds a year of demand,
 *  which under the current provisional costs is nearly every SKU - the point
 *  being that the figure is present and honest, not that it is orderable. */
function Eoq({ scenario }) {
  if (!scenario) return <span className="muted">—</span>;
  return scenario.exceeds_annual_demand
    ? <span className="muted" title="Exceeds a full year of demand — not a usable order quantity yet">
        {num(scenario.eoq)}
      </span>
    : <span>{num(scenario.eoq)}</span>;
}

function FormulaLine({ label, formula }) {
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>{label}</div>
      <div className="mono" style={{ fontSize: 12, color: 'var(--text)', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 'var(--r-xs)', padding: '7px 10px', marginTop: 4 }}>
        {formula}
      </div>
    </div>
  );
}

function Param({ label, value, note }) {
  return (
    <div style={{ background: 'var(--bg)', borderRadius: 'var(--r-sm)', padding: '10px 12px' }}>
      <div className="hint">{label}</div>
      <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--text)' }}>{value}</div>
      <div className="hint">{note}</div>
    </div>
  );
}
