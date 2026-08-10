import { getStockPosition, getReorderAlerts, getMeta } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { num } from '../lib/format';

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

  const stockById = new Map(items.map(p => [p.product_id, p.current_stock]));
  const alertItems = (alerts?.available ? alerts.data.items : []).map(a => ({
    ...a,
    current_stock: stockById.get(a.product_id) ?? null,
  }));
  const reorderNow = alertItems.filter(a => a.current_stock != null && a.current_stock <= a.reorder_point);
  const approaching = alertItems.filter(
    a => a.current_stock != null && a.current_stock > a.reorder_point && a.current_stock <= a.reorder_point * 1.2
  );

  const columns = [
    { key: 'item_name',     label: 'Product Name', strong: true, truncate: true, width: '26%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
    { key: 'current_stock', label: 'Current Stock', num: true, width: '12%', render: v => num(v) },
    { key: 'stock_as_of',   label: 'Counted', width: '10%', render: v => <span className="muted">{v}</span> },
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
    { key: 'safety_stock', label: 'Safety Stock', num: true, width: '9%', render: v => num(v) },
    {
      key: 'reorder_point', label: 'ROP', num: true, width: '8%',
      render: (v, row) => (
        <span style={row.current_stock != null && row.current_stock <= v ? { color: 'var(--warn)', fontWeight: 700 } : undefined}>
          {num(v)}
        </span>
      ),
    },
    {
      key: 'eoq_low', label: 'EOQ · low admin', num: true, width: '10%',
      render: (_v, row) => num(row.scenarios.low_admin_cost.eoq),
    },
    {
      key: 'eoq_high', label: 'EOQ · high goods-value', num: true, width: '12%',
      render: (_v, row) => num(row.scenarios.high_goods_value.eoq),
    },
    {
      key: 'sigma_source', label: 'σ Source', width: '9%',
      render: v => v === 'cv_fallback' ? <span className="tag tag--warn">CV fallback</span> : <span className="muted">observed</span>,
    },
  ];

  if (loading) return <Loading label="Loading stock position…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard
          label="Reorder Now"
          value={alerts?.available ? num(reorderNow.length) : <PendingValue />}
          sub="On hand at or below ROP"
          icon="alert"
        />
        <KPICard
          label="Approaching ROP"
          value={alerts?.available ? num(approaching.length) : <PendingValue />}
          sub="Within 20% above ROP"
          icon="bell"
        />
        <KPICard
          label="Median Days of Supply"
          value={medianDos != null ? `${Math.round(medianDos)}d` : <PendingValue />}
          sub={`${withDos.length} of ${stock?.total ?? 0} products have stock data`}
          icon="clock"
          accent
        />
      </div>

      {!alerts?.available && (
        <Pending title="Reorder alerts are not computed yet" reason={alerts?.reason}>
          <div className="pending__body">
            ROP = (forecasted daily demand × lead time) + safety stock, and safety stock = Z × σ<sub>demand</sub> × √lead
            time. Both need a per-supplier lead time and a forecast; neither is in the database. The table below is what
            the data does support today: measured stock on hand and how long it lasts at the observed rate.
          </div>
        </Pending>
      )}

      <div className="card card__pad">
        <div className="card-h"><span className="section-h">ROP / Safety Stock Formula</span></div>
        <div className="grid-2">
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
                {' '}{Math.round((alertItems.length))} priced SKUs cover Fast/Slow items with a positive 30-day demand basis.
              </div>
            ) : (
              <div className="notice notice--warn">
                <b>Note:</b> Dim_Parameters is empty. Lead times, ordering cost and holding cost come from the USTore
                site visit; until then these formulas have no inputs and this screen shows no reorder recommendation.
              </div>
            )}
          </div>
        </div>
      </div>

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
          <DataTable columns={alertColumns} data={alertItems} minWidth={1080} />
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
