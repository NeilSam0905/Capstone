import { useMemo } from 'react';
import { getProducts, getMonthlyUnits, getMeta, getStockPosition, getReorderAlerts, getAdvisories } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { LineChart, Donut, HBars, FSNStat, StackBar } from '../components/charts';
import Icon from '../components/Icon';
import { num, shortMonth, usDate } from '../lib/format';

export default function Overview({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], []);
  const { data: monthly } = useData(() => getMonthlyUnits(filters), [filters], []);
  const { data: meta } = useData(getMeta, []);
  const { data: stock } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: advisories } = useData(getAdvisories, []);

  // Compute reorder-now count by joining alerts to stock position
  const stockById = new Map((stock?.items ?? []).map(p => [p.product_id, p.current_stock]));
  const alertItems = (alerts?.available ? alerts.data.items : []).map(a => ({
    ...a,
    current_stock: stockById.get(a.product_id) ?? null,
  }));
  const reorderNow = alertItems.filter(a => a.current_stock != null && a.current_stock <= a.reorder_point);

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
          sub={meta ? `${usDate(meta.sales_span[0])} → ${usDate(meta.sales_span[1])}` : ''}
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
          value={alerts?.available ? num(reorderNow.length) : <PendingValue />}
          sub={alerts?.available
            ? `${reorderNow.length} item${reorderNow.length !== 1 ? 's' : ''} at or below reorder point`
            : alerts?.reason ?? 'Reorder data not yet available'}
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

      {/* Advisories — calendar-contextual, driven by real data */}
      <div>
        <div className="card-h"><span className="section-h">Upcoming Event Advisories</span></div>
        <AdvisoriesPanel advisories={advisories} />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- advisories */

const SEVERITY_STYLE = {
  high: { borderLeft: '4px solid var(--warn)', background: 'var(--warn-bg)' },
  medium: { borderLeft: '4px solid var(--accent)', background: 'var(--card)' },
  low: { borderLeft: '4px solid var(--line)', background: 'var(--card)' },
};

const TYPE_ICON = {
  enrollment: 'calPlus',
  exam_week: 'file',
  event: 'bell',
  forecast_alert: 'trend',
  info: 'clock',
};

function AdvisoriesPanel({ advisories }) {
  if (!advisories) return <Loading label="Loading advisories…" />;

  const items = advisories.advisories ?? [];

  if (items.length === 0) {
    return (
      <div className="card card__pad">
        <div className="hint">No upcoming calendar signals or events to advise on.</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {!advisories.has_forecast && (
        <div className="notice notice--warn">
          <b>Limited advisories:</b> The demand forecast has not been generated yet
          Advisories below are based on calendar signals only, Please run the forecast pipeline
          to enable demand-driven recommendations.
        </div>
      )}
      {items.map((advisory, i) => (
        <div
          key={i}
          className="card card__pad"
          style={{ ...SEVERITY_STYLE[advisory.severity], display: 'flex', gap: 14, alignItems: 'flex-start' }}
        >
          <span style={{ flexShrink: 0, marginTop: 2, color: advisory.severity === 'high' ? 'var(--warn)' : 'var(--muted)' }}>
            <Icon name={TYPE_ICON[advisory.type] || 'bell'} size={18} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text)', marginBottom: 3 }}>
              {advisory.title}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.5 }}>
              {advisory.description}
            </div>
            {advisory.date_range && (
              <div className="hint" style={{ marginTop: 6 }}>
                {advisory.date_range[0] === advisory.date_range[1]
                  ? usDate(advisory.date_range[0])
                  : `${usDate(advisory.date_range[0])} → ${usDate(advisory.date_range[1])}`}
              </div>
            )}
          </div>
          <span
            className={`tag tag--${advisory.severity === 'high' ? 'warn' : advisory.severity === 'medium' ? 'gold' : 'info'}`}
            style={{ flexShrink: 0 }}
          >
            {advisory.severity}
          </span>
        </div>
      ))}
    </div>
  );
}
