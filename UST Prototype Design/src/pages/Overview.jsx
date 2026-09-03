import { useEffect, useMemo, useState } from 'react';
import { getProducts, getMonthlyUnits, getMeta, getStockPosition, getReorderAlerts, getAdvisories } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import Pending, { Loading, PendingValue } from '../components/Pending';
import { LineChart, Donut, HBars, FSNStat, StackBar } from '../components/charts';
import Modal from '../components/Modal';
import FsnBandModal from '../components/FsnBandModal';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import { num, shortMonth, usDate, FSN_TONE, FSN_LABEL } from '../lib/format';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';

export default function Overview({ filters, setPage }) {
  const [productsOpen, setProductsOpen] = useState(false);
  const [fsnBand, setFsnBand] = useState(null);   // 'F' | 'S' | 'N' | null
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `overview:products:${filters.supplier}|${filters.category}|${filters.dateRange}` });
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

  const showSupplier = filters.supplier === ALL_SUPPLIERS;
  const top10 = useMemo(() => [...products]
    .sort((a, b) => b.total_units - a.total_units)
    .slice(0, 10)
    .map(p => ({
      name: p.item_name,
      value: p.total_units,
      // Only worth showing while the chart spans every supplier; with one
      // selected it would just repeat that name down the whole card.
      sub: showSupplier ? p.supplier_name : undefined,
    })), [products, showSupplier]);

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

  // The span the headline figure actually covers. It has to follow the date
  // filter: quoting the full sales span under a windowed total is exactly how
  // "Last 3 Months" used to read as though it were still all-time.
  const spanLabel = monthly.length
    ? `${shortMonth(monthly[0].month)} → ${shortMonth(monthly[monthly.length - 1].month)}`
    : meta ? `${usDate(meta.sales_span[0])} → ${usDate(meta.sales_span[1])}` : '';

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
          sub={spanLabel}
          icon="box"
        />
        <KPICard
          label="Products"
          value={num(products.length)}
          sub={`${meta?.products_with_sales ?? 0} with sales history`}
          icon="tag"
          onClick={() => setProductsOpen(true)}
          linkLabel="Browse products"
        />
        <KPICard
          label="Items Below / Near ROP"
          value={alerts?.available ? num(reorderNow.length) : <PendingValue />}
          sub={alerts?.available
            ? `${reorderNow.length} item${reorderNow.length !== 1 ? 's' : ''} at or below reorder point`
            : alerts?.reason ?? 'Reorder data not yet available'}
          icon="alert"
          onClick={() => setPage?.('reorder')}
          linkLabel="Open Reorder Alerts"
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
        {/* The card is no longer one big click target. It has two different
            destinations now - the header goes to the Classification page, each
            band opens that band's items - and a card-level handler would have
            swallowed the band clicks or fired both. */}
        <div className="card card__pad card--fsn">
          <div className="card-h">
            <span className="section-h">FSN Classification</span>
            <button type="button" className="linkish" onClick={() => setPage?.('classification')}>
              Open Classification <Icon name="arrow" size={12} />
            </button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <FSNStat label="Fast-Moving" count={totals.fsn.F} pct={Math.round(totals.fsn.F / total * 100)} tone="ok"
                     onClick={() => setFsnBand('F')} />
            <FSNStat label="Slow-Moving" count={totals.fsn.S} pct={Math.round(totals.fsn.S / total * 100)} tone="warn"
                     onClick={() => setFsnBand('S')} />
            <FSNStat label="Non-Moving"  count={totals.fsn.N} pct={Math.round(totals.fsn.N / total * 100)} tone="crit"
                     onClick={() => setFsnBand('N')} />
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
        <AdvisoriesPanel advisories={advisories} setPage={setPage} />
      </div>

      <ProductsModal
        open={productsOpen}
        onClose={() => setProductsOpen(false)}
        products={products}
        stock={stock}
      />

      <FsnBandModal
        band={fsnBand}
        onClose={() => setFsnBand(null)}
        products={products}
        onOpenFullList={() => { setFsnBand(null); setPage?.('classification'); }}
      />
    </div>
  );
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

/* ---------------------------------------------------------------- advisories */

/* Severity drives one token set — icon tint, chip tone, accent rail — instead
   of the ad-hoc inline borderLeft/background pairs this used to carry. Keeping
   them in one map is what stops "high" meaning warn in one place and crit in
   another. */
const SEVERITY = {
  high:   { cls: 'is-high',   tag: 'warn', label: 'Act now' },
  medium: { cls: 'is-medium', tag: 'gold', label: 'Plan for' },
  low:    { cls: 'is-low',    tag: 'info', label: 'For info' },
};

const TYPE_ICON = {
  enrollment: 'calPlus',
  exam_week: 'file',
  event: 'bell',
  forecast_alert: 'trend',
  info: 'clock',
};

const TYPE_LABEL = {
  enrollment: 'Calendar',
  exam_week: 'Calendar',
  event: 'Event',
  forecast_alert: 'Forecast',
  info: 'Status',
};

function AdvisoriesPanel({ advisories, setPage }) {
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
    <div className="advisories">
      {!advisories.has_forecast && (
        <div className="notice notice--warn">
          <b>Limited advisories:</b> the demand forecast has not been generated yet, so these are
          based on calendar signals only. Run the forecast from the Tally Interface to enable
          demand-driven recommendations.
        </div>
      )}
      {items.map((advisory, i) => (
        <AdvisoryCard key={i} advisory={advisory} setPage={setPage} />
      ))}
    </div>
  );
}

/** One advisory.
 *
 *  Collapsed it is the sentence it always was, plus a scannable meta row.
 *  Expanded it answers the two questions the sentence raises and never used
 *  to: WHICH items, and on WHAT basis. Both come from the API (`items`,
 *  `basis`) rather than being inferred here — a screen must not invent an
 *  analytic the backend has not produced.
 *
 *  Only advisories that actually carry detail are expandable, so a chevron
 *  never promises something that is not there.
 */
function AdvisoryCard({ advisory, setPage }) {
  const [open, setOpen] = useState(false);
  const sev = SEVERITY[advisory.severity] ?? SEVERITY.low;
  const items = advisory.items ?? [];
  const expandable = items.length > 0 || Boolean(advisory.basis);
  const belowRop = items.filter(it => it.below_rop).length;

  const dateText = advisory.date_range
    && (advisory.date_range[0] === advisory.date_range[1]
      ? usDate(advisory.date_range[0])
      : `${usDate(advisory.date_range[0])} → ${usDate(advisory.date_range[1])}`);

  const head = (
    <>
      <span className="advisory__icon"><Icon name={TYPE_ICON[advisory.type] || 'bell'} size={17} /></span>

      <div className="advisory__main">
        <div className="advisory__title">{advisory.title}</div>
        <div className="advisory__desc">{advisory.description}</div>

        <div className="advisory__meta">
          <span className="advisory__kind">{TYPE_LABEL[advisory.type] || 'Advisory'}</span>
          {dateText && <span>{dateText}</span>}
          {items.length > 0 && (
            <span>{num(items.length)} item{items.length === 1 ? '' : 's'}</span>
          )}
          {belowRop > 0 && (
            <span className="advisory__flag">{num(belowRop)} below ROP</span>
          )}
        </div>
      </div>

      <span className="advisory__right">
        <span className={`tag tag--${sev.tag}`}>{sev.label}</span>
        {expandable && <span className={'advisory__chev' + (open ? ' is-open' : '')} aria-hidden="true" />}
      </span>
    </>
  );

  if (!expandable) {
    return <div className={`card advisory ${sev.cls}`}><div className="advisory__head">{head}</div></div>;
  }

  return (
    <div className={`card advisory ${sev.cls}` + (open ? ' is-open' : '')}>
      <button type="button" className="advisory__head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {head}
      </button>

      {open && (
        <div className="advisory__body">
          {advisory.basis && (
            <div className="advisory__basis">
              <span className="advisory__basis-label">Why this is showing</span>
              {advisory.basis}
            </div>
          )}

          {items.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table className="tbl" style={{ minWidth: 620 }}>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Class</th>
                    <th className="num">Forecast 30d</th>
                    <th className="num">On hand</th>
                    <th className="num">ROP</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(it => (
                    <tr key={it.product_id}>
                      <td>
                        <b>{it.item_name}</b>
                        <div className="hint">{it.supplier_name}</div>
                      </td>
                      <td><span className={`tag tag--${FSN_TONE[it.fsn_class] || 'info'}`}>{it.fsn_class || '—'}</span></td>
                      <td className="num">
                        {it.forecast_30d == null ? <span className="muted">—</span> : num(Math.round(it.forecast_30d))}
                      </td>
                      <td className="num">
                        {it.current_stock == null
                          ? <span className="muted">never counted</span>
                          : num(it.current_stock)}
                      </td>
                      <td className="num">
                        {it.reorder_point == null
                          ? <span className="muted">—</span>
                          : <span className={it.below_rop ? 'advisory__rop-hit' : undefined}>
                              {num(Math.round(it.reorder_point))}
                            </span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn--ghost btn--sm" onClick={() => setPage?.('reorder')}>
              Reorder Alerts <Icon name="arrow" size={12} />
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => setPage?.('forecast')}>
              Demand Forecast <Icon name="arrow" size={12} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
