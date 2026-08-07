import { useMemo } from 'react';
import { getProducts, getMonthlyUnits, getMeta, getStockPosition } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { LineChart, Donut, HBars, FSNStat, StackBar } from '../components/charts';
import { num, shortMonth } from '../lib/format';

export default function Overview({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], []);
  const { data: monthly } = useData(() => getMonthlyUnits(filters), [filters], []);
  const { data: meta } = useData(getMeta, []);
  const { data: stock } = useData(() => getStockPosition(filters), [filters]);

  const totals = useMemo(() => {
    const units = products.reduce((s, p) => s + p.total_units, 0);
    const fsn = { F: 0, S: 0, N: 0 };
    products.forEach(p => { if (fsn[p.fsn_class] !== undefined) fsn[p.fsn_class]++; });
    return { units, fsn };
  }, [products]);

  const top10 = useMemo(() => [...products]
    .sort((a, b) => b.total_units - a.total_units)
    .slice(0, 10)
    .map(p => ({ name: p.item_name, value: p.total_units })), [products]);

  const catData = useMemo(() => {
    const map = {};
    products.forEach(p => { map[p.category] = (map[p.category] || 0) + p.total_units; });
    return Object.entries(map)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [products]);

  const trend = useMemo(
    () => monthly.map(m => ({ label: shortMonth(m.month), value: m.units })),
    [monthly]
  );

  const total = products.length || 1;

  if (loading) return <Loading label="Loading catalogue…" />;

  return (
    <div className="stack">
      {/* KPIs */}
      <div className="grid-3">
        <KPICard
          accent
          label="Total Units Sold"
          value={num(totals.units)}
          sub={meta ? `${meta.sales_span[0]} → ${meta.sales_span[1]}` : ''}
          icon="box"
        />
        <KPICard
          label="Products"
          value={num(products.length)}
          sub={`${meta?.products_with_sales ?? 0} with sales history`}
          icon="tag"
        />
        <KPICard
          label="Items Below / Near ROP"
          value={<PendingValue />}
          sub="Needs lead time & cost inputs"
          icon="alert"
        />
      </div>

      {/* Stock status banner — real coverage, no ROP to judge against */}
      <div className="banner">
        <span className="section-h">Stock Status</span>
        {stock ? (
          <>
            <div className="banner__item">
              <span className="dot" style={{ background: 'var(--ok)' }} />
              <b style={{ color: 'var(--ok)' }}>{stock.covered}</b> items with a stock count
            </div>
            <div className="banner__item">
              <span className="dot" style={{ background: 'var(--line)' }} />
              <b style={{ color: 'var(--muted)' }}>{stock.total - stock.covered}</b> items with no inventory record
            </div>
          </>
        ) : <span className="hint">Loading…</span>}
        <span className="hint" style={{ marginLeft: 'auto' }}>
          Reorder status needs a reorder point — see Reorder Alerts
        </span>
      </div>

      {/* Trend + category mix */}
      <div className="grid-2-1">
        <div className="card card__pad">
          <div className="card-h">
            <span className="section-h">Monthly Units Sold</span>
            <span className="hint">{trend.length} periods</span>
          </div>
          <LineChart data={trend} />
        </div>
        <div className="card card__pad">
          <div className="card-h"><span className="section-h">Units by Category</span></div>
          <Donut data={catData} />
        </div>
      </div>

      {/* Top products + FSN */}
      <div className="grid-2-1">
        <div className="card card__pad">
          <div className="card-h"><span className="section-h">Top 10 Products by Units Sold</span></div>
          <HBars data={top10} />
        </div>
        <div className="card card__pad">
          <div className="card-h"><span className="section-h">FSN Classification</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FSNStat label="Fast-Moving" count={totals.fsn.F} pct={Math.round(totals.fsn.F / total * 100)} tone="ok" />
            <FSNStat label="Slow-Moving" count={totals.fsn.S} pct={Math.round(totals.fsn.S / total * 100)} tone="warn" />
            <FSNStat label="Non-Moving"  count={totals.fsn.N} pct={Math.round(totals.fsn.N / total * 100)} tone="crit" />
          </div>
          <StackBar parts={[
            { value: totals.fsn.F, color: 'var(--ok)' },
            { value: totals.fsn.S, color: 'var(--warn)' },
            { value: totals.fsn.N, color: 'var(--crit)' },
          ]} />
          <div className="hint" style={{ textAlign: 'right', marginTop: 6 }}>{products.length} products</div>
        </div>
      </div>

      {/* Advisories — prescriptive output, not computed yet */}
      <div>
        <div className="card-h"><span className="section-h">Upcoming Event Advisories</span></div>
        <Pending
          title="Calendar-contextual advisories are not generated yet"
          reason="Advisories combine a forecast with a reorder point. Neither exists in the database yet, and writing plausible recommendations here would misrepresent the pipeline's state."
        />
      </div>
    </div>
  );
}
