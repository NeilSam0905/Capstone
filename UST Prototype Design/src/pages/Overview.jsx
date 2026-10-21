import { useEffect, useMemo, useRef, useState } from 'react';
import { getProducts, getMonthlyUnits, getMeta, getStockPosition, getReorderAlerts, getAdvisories } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import { Loading, PendingValue } from '../components/Pending';
import { LineChart, Donut, HBars, FSNStat, StackBar } from '../components/charts';
import Modal from '../components/Modal';
import FsnBandModal from '../components/FsnBandModal';
import DataTable from '../components/DataTable';
import Icon from '../components/Icon';
import { AdvisoriesPanel, AdvisoriesJump } from '../components/Advisories';
import { ADVISORIES_ANCHOR } from '../config';
import { num, shortMonth, usDate, FSN_TONE, FSN_LABEL } from '../lib/format';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';

export default function Overview({ filters, setPage, user }) {
  const [productsOpen, setProductsOpen] = useState(false);
  const [fsnBand, setFsnBand] = useState(null);   // 'F' | 'S' | 'N' | null
  const advisoriesRef = useRef(null);
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `overview:products:${filters.supplier}|${filters.category}|${filters.dateRange}|${filters.rangeFrom}|${filters.rangeTo}` });
  const { data: monthly } = useData(() => getMonthlyUnits(filters), [filters], []);
  const { data: meta } = useData(getMeta, []);
  // Feeds the below-ROP count in the KPI row and the On-hand column in the
  // products modal. It has no panel of its own on this page.
  const { data: stock } = useData(() => getStockPosition(filters), [filters]);
  const { data: alerts } = useData(getReorderAlerts, []);
  const { data: advisories } = useData(getAdvisories, []);

  // Below and near the reorder point, as /api/reorder flags them
  // (needs_reorder: on hand <= ROP; approaching_rop: above ROP but within
  // 20% of it), so this tile and the Reorder Alerts page count the same
  // items. The stock position is only the filter: it holds the counted items
  // the topbar's supplier and category leave, and an item outside it is not
  // counted here.
  const inView = new Set((stock?.items ?? []).map(p => p.product_id));
  const alertItems = (alerts?.available ? alerts.data.items : [])
    .filter(a => inView.has(a.product_id));
  const belowRop = alertItems.filter(a => a.needs_reorder).length;
  const nearRop = alertItems.filter(a => a.approaching_rop).length;

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
    // A category that sold nothing in the window is not a slice of sales; left
    // in, it showed up inside "Others" as "Uncategorised 0%".
    return Object.entries(map)
      .filter(([, value]) => value > 0)
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
          value={alerts?.available ? num(belowRop + nearRop) : <PendingValue />}
          sub={alerts?.available
            ? `${num(belowRop)} at or below reorder point · ${num(nearRop)} within 20% of it`
            : alerts?.reason ?? 'Reorder data not yet available'}
          icon="alert"
          onClick={() => setPage?.('reorder')}
          linkLabel="Open Reorder Alerts"
        />
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
          {/* Every category at 5% of units or less goes into "Others". */}
          <Donut data={catData} foldAtOrBelow={0.05} />
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

      {/* Advisories — calendar-contextual, driven by real data. The id is
          what the topbar's Advisories button scrolls to on this page
          (App.jsx); on every other page that button opens them in a dialog. */}
      <div id={ADVISORIES_ANCHOR} ref={advisoriesRef} style={{ scrollMarginTop: 12 }}>
        <div className="card-h"><span className="section-h">Upcoming Event Advisories</span></div>
        <AdvisoriesPanel advisories={advisories} setPage={setPage} />
      </div>

      {/* Overview only: a floating pill that scrolls down to the section above,
          which is the last thing on a long page. */}
      <AdvisoriesJump target={advisoriesRef} user={user} />

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
