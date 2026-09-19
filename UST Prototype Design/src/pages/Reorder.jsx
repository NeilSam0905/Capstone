import { getStockPosition, getReorderAlerts, getMeta } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { num, usDate } from '../lib/format';

export default function Reorder({ filters }) {
  const { data: stock, loading } = useData(() => getStockPosition(filters), [filters], null,
    { key: `reorder:stock:${filters.supplier}|${filters.category}` });
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
            ROP = (forecasted daily demand × lead time) + safety stock, and safety stock = Z × σ<sub>demand</sub> × √lead
            time. Both need a per-supplier lead time and a forecast; neither is in the database. The table below is what
            the data does support today: measured stock on hand and how long it lasts at the observed rate.
          </div>
        </Pending>
      )}

      {alerts?.available && (
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Reorder Recommendations</span>
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
    ? <span className="muted" title="Exceeds a full year of demand — not a usable order quantity yet">
        {num(scenario.eoq)}
      </span>
    : <span>{num(scenario.eoq)}</span>;
}
