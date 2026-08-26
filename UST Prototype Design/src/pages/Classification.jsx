import { useMemo } from 'react';
import { getProducts, getFsnSensitivity } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import { Loading } from '../components/Pending';
import { HBars, GroupedBars } from '../components/charts';
import { num, FSN_TONE, FSN_LABEL } from '../lib/format';

export default function Classification({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], []);
  const { data: sensitivity } = useData(getFsnSensitivity, []);

  const fsnCounts = useMemo(() => {
    const c = { F: 0, S: 0, N: 0 };
    products.forEach(p => { if (c[p.fsn_class] !== undefined) c[p.fsn_class]++; });
    return c;
  }, [products]);
  const hvlCount = useMemo(() => products.filter(p => p.is_hvl).length, [products]);
  const censoredItems = useMemo(() => products.filter(p => p.censored_days > 0).length, [products]);
  const total = products.length || 1;

  // Pareto on units, not revenue: only part of the catalogue carries a price.
  const pareto = useMemo(() => {
    const sorted = [...products].filter(p => p.total_units > 0).sort((a, b) => b.total_units - a.total_units);
    const grand = sorted.reduce((s, p) => s + p.total_units, 0) || 1;
    const running = sorted.reduce((acc, p) => {
      acc.push((acc[acc.length - 1] ?? 0) + p.total_units);
      return acc;
    }, []);
    return sorted.slice(0, 15).map((p, i) => ({
      name: p.item_name,
      value: p.total_units,
      cumPct: Math.round((running[i] / grand) * 1000) / 10,
    }));
  }, [products]);

  // explicit widths + fixed layout: every column stays on screen, and the
  // supplier gets a generous share so the name reads before it ellipsises
  const columns = [
    { key: 'item_name',     label: 'Product Name', strong: true, truncate: true, width: '20%' },
    { key: 'category',      label: 'Category', truncate: true, width: '11%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
    { key: 'total_units',   label: 'Units Sold', num: true, width: '9%',  render: v => num(v) },
    { key: 'avg_monthly',   label: 'Avg/Month',  num: true, width: '9%',  render: v => v.toFixed(1) },
    { key: 'adus',          label: 'ADUS',       num: true, width: '8%',  render: v => v.toFixed(3) },
    { key: 'cv',            label: 'CV%',        num: true, width: '8%',  render: v => `${v.toFixed(1)}%` },
    {
      key: 'censored_days', label: 'Stockout', num: true, width: '8%',
      render: v => v > 0 ? <span style={{ color: 'var(--warn)', fontWeight: 700 }}>{v}</span> : <span className="muted">—</span>,
    },
    {
      key: 'fsn_class', label: 'FSN Class', width: '13%',
      render: (v, row) => (
        <span style={{ display: 'inline-flex', gap: 6 }}>
          <span className={`tag tag--${FSN_TONE[v]}`}>{FSN_LABEL[v]}</span>
          {row.is_hvl === 1 && <span className="tag tag--hvl">HVL</span>}
        </span>
      ),
    },
  ];

  if (loading) return <Loading label="Loading classification…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard label="Fast-Moving (F)" value={fsnCounts.F} tone="ok"
          sub={`${Math.round(fsnCounts.F / total * 100)}% of products${hvlCount ? ` · includes ${hvlCount} HVL` : ''}`}
          icon="zap" accent />
        <KPICard label="Slow-Moving (S)" value={fsnCounts.S}
          sub={`${Math.round(fsnCounts.S / total * 100)}% of products`} icon="clock" />
        <KPICard label="Non-Moving (N)" value={fsnCounts.N} tone="crit"
          sub={`${Math.round(fsnCounts.N / total * 100)}% · no sales history`} icon="xCircle" />
      </div>

      {hvlCount > 0 && (
        <div className="notice notice--hvl">
          <b>High-Velocity Limited</b> — {hvlCount} Fast-moving item{hvlCount > 1 ? 's are' : ' is'} carried on fewer
          than 30 active tally dates. HVL is a confidence modifier on F, not a fourth class, so these stay classified F
          while flagging that the history behind them is thin.
        </div>
      )}

      {censoredItems > 0 && (
        <div className="notice notice--warn">
          <b>Stockout-adjusted.</b> {censoredItems} product{censoredItems > 1 ? 's have' : ' has'} days where the stock
          model says the item was out. Those days are excluded from the ADUS denominator — a day with nothing to sell is
          not evidence of slow movement.
        </div>
      )}

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Pareto — Unit Contribution by Product</span>
          <span className="hint">top 15 · cumulative % of all units</span>
        </div>
        <HBars data={pareto} color="var(--ink)" valueFmt={num} />
        <div className="hint" style={{ marginTop: 10 }}>
          Ranked on units rather than revenue: only part of the catalogue carries a unit price, so a revenue Pareto
          would rank on a partially-populated column.
        </div>
      </div>

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Threshold Sensitivity Analysis</span>
          <span className="hint">★ = selected 80th percentile · whole catalogue, not the filtered view</span>
        </div>
        {!sensitivity ? <Loading /> : (
          <div className="grid-2">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Class</th>
                  <th className="num">75th pct</th>
                  <th className="num" style={{ color: 'var(--accent-deep)' }}>80th pct ★</th>
                  <th className="num">85th pct</th>
                </tr>
              </thead>
              <tbody>
                {['F', 'S', 'N'].map(k => (
                  <tr key={k}>
                    <td className="strong">{FSN_LABEL[k]} ({k})</td>
                    <td className="num">{sensitivity.p75[k]}</td>
                    <td className="num strong" style={{ background: 'var(--gold-wash)' }}>{sensitivity.p80[k]}</td>
                    <td className="num">{sensitivity.p85[k]}</td>
                  </tr>
                ))}
                <tr>
                  <td className="muted">ADUS cutoff</td>
                  <td className="num muted">{sensitivity.p75.cutoff}</td>
                  <td className="num strong">{sensitivity.p80.cutoff}</td>
                  <td className="num muted">{sensitivity.p85.cutoff}</td>
                </tr>
              </tbody>
            </table>
            <div>
              <GroupedBars
                groups={[
                  { label: 'p75', ...sensitivity.p75 },
                  { label: 'p80 ★', ...sensitivity.p80 },
                  { label: 'p85', ...sensitivity.p85 },
                ]}
                series={[
                  { key: 'F', color: 'var(--ok)' },
                  { key: 'S', color: 'var(--warn)' },
                  { key: 'N', color: 'var(--crit)' },
                ]}
              />
              <div className="legend" style={{ justifyContent: 'center', marginTop: 6 }}>
                <span><i style={{ background: 'var(--ok)' }} />Fast (F)</span>
                <span><i style={{ background: 'var(--warn)' }} />Slow (S)</span>
                <span><i style={{ background: 'var(--crit)' }} />Non (N)</span>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">FSN Classification — Full Item List</span>
          <span className="hint">ADUS = Average Daily Units Sold ({products.length}rows)</span>
        </div>
        <DataTable columns={columns} data={products} pageSize={10} minWidth={1040} />
      </div>
    </div>
  );
}
