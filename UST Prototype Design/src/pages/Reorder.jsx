import { getStockPosition, getReorderAlerts, getMeta } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { num } from '../lib/format';

export default function Reorder({ filters }) {
  const { data: stock, loading } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: meta } = useData(getMeta, []);

  const items = stock?.items ?? [];
  const withDos = items.filter(p => p.days_of_supply != null);
  const medianDos = withDos.length
    ? [...withDos].sort((a, b) => a.days_of_supply - b.days_of_supply)[Math.floor(withDos.length / 2)].days_of_supply
    : null;

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

  if (loading) return <Loading label="Loading stock position…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard label="Reorder Now" value={<PendingValue />} sub="Needs a reorder point" icon="alert" />
        <KPICard label="Approaching ROP" value={<PendingValue />} sub="Needs a reorder point" icon="bell" />
        <KPICard
          label="Median Days of Supply"
          value={medianDos != null ? `${Math.round(medianDos)}d` : <PendingValue />}
          sub={`${withDos.length} of ${stock?.total ?? 0} products have stock data`}
          icon="clock"
          accent
        />
      </div>

      <Pending title="Reorder alerts are not computed yet" reason={alerts?.reason}>
        <div className="pending__body">
          ROP = (forecasted daily demand × lead time) + safety stock, and safety stock = Z × σ<sub>demand</sub> × √lead
          time. Both need a per-supplier lead time and a forecast; neither is in the database. The table below is what
          the data does support today: measured stock on hand and how long it lasts at the observed rate.
        </div>
      </Pending>

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
              <Param label="Z-score" value="—" note="F = 1.65 / S = 1.04, once FSN-differentiated" />
              <Param label="Lead Time" value="—" note="Not collected yet" />
            </div>
            <div className="notice notice--warn">
              <b>Note:</b> Dim_Parameters is empty. Lead times, ordering cost and holding cost come from the USTore
              site visit; until then these formulas have no inputs and this screen shows no reorder recommendation.
            </div>
          </div>
        </div>
      </div>

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
