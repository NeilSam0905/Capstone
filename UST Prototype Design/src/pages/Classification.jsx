import { useMemo, useState } from 'react';
import { getProducts, ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import { Loading } from '../components/Pending';
import { HBars } from '../components/charts';
import FsnBandModal from '../components/FsnBandModal';
import { num, FSN_TONE, FSN_LABEL } from '../lib/format';

/**
 * Classification (FSN).
 *
 * Written for whoever runs the store, not for whoever built the model. The
 * numbers are unchanged — what changed is that the page no longer asks the
 * reader to know what ADUS, CV%, a censored day or an 80th-percentile cutoff
 * is. Those live in step3_fsn_classification.py and in the docs; a person
 * deciding what to reorder does not need them on screen.
 *
 * Deliberately removed:
 *   - Threshold Sensitivity Analysis (how the F/S/N split moves at the 75th,
 *     80th and 85th percentile cutoffs). That is a methods-section exhibit,
 *     not an operating one.
 *   - The ADUS, CV% and Stockout columns, for the same reason.
 */
export default function Classification({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `classification:products:${filters.supplier}|${filters.category}|${filters.dateRange}` });

  const [band, setBand] = useState(null);       // 'F' | 'S' | 'N' | null
  const [bestBand, setBestBand] = useState('All');
  const [q, setQ] = useState('');
  const [fsn, setFsn] = useState('All');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);

  const fsnCounts = useMemo(() => {
    const c = { F: 0, S: 0, N: 0 };
    products.forEach(p => { if (c[p.fsn_class] !== undefined) c[p.fsn_class]++; });
    return c;
  }, [products]);
  const hvlCount = useMemo(() => products.filter(p => p.is_hvl).length, [products]);
  const total = products.length || 1;

  // Ranked on units rather than revenue because only part of the catalogue
  // carries a unit price - a revenue ranking would sort on a half-empty column.
  // `bestBand` is this card's own filter, deliberately separate from the item
  // list's: the two answer different questions and should not move together.
  const showSupplier = filters.supplier === ALL_SUPPLIERS;
  const topSellers = useMemo(() => [...products]
    .filter(p => p.total_units > 0 && (bestBand === 'All' || p.fsn_class === bestBand))
    .sort((a, b) => b.total_units - a.total_units)
    .slice(0, 15)
    .map(p => ({
      name: p.item_name,
      value: p.total_units,
      // See Overview: the supplier line is for the all-suppliers view only.
      sub: showSupplier ? p.supplier_name : undefined,
    })), [products, bestBand, showSupplier]);

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

  const columns = [
    { key: 'item_name',     label: 'Product Name', strong: true, truncate: true, width: '30%' },
    { key: 'category',      label: 'Category', truncate: true, width: '14%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '21%' },
    { key: 'total_units',   label: 'Units Sold', num: true, strong: true, width: '11%', render: num },
    {
      key: 'avg_monthly', label: 'Avg / Month', num: true, width: '11%',
      render: v => (v ?? 0).toFixed(1),
    },
    {
      key: 'fsn_class', label: 'How It Moves', width: '13%',
      render: (v, row) => (
        <span style={{ display: 'inline-flex', gap: 5 }}>
          <span className={`tag tag--${FSN_TONE[v]}`}>{FSN_LABEL[v]}</span>
          {row.is_hvl === 1 && <span className="tag tag--hvl" title="Sells quickly, but on few recorded days">Thin history</span>}
        </span>
      ),
    },
  ];

  const filtered = shown.length !== products.length;

  if (loading) return <Loading label="Loading classification…" />;

  return (
    <div className="stack">
      <div className="grid-3">
        <KPICard label="Fast-Moving" value={fsnCounts.F} tone="ok" icon="zap" accent
          sub={`${Math.round(fsnCounts.F / total * 100)}% of products · sell regularly`}
          onClick={() => setBand('F')} linkLabel="See the Fast-Moving items" />
        <KPICard label="Slow-Moving" value={fsnCounts.S} icon="clock"
          sub={`${Math.round(fsnCounts.S / total * 100)}% of products · sell occasionally`}
          onClick={() => setBand('S')} linkLabel="See the Slow-Moving items" />
        <KPICard label="Non-Moving" value={fsnCounts.N} tone="crit" icon="xCircle"
          sub={`${Math.round(fsnCounts.N / total * 100)}% of products · no recorded sales`}
          onClick={() => setBand('N')} linkLabel="See the Non-Moving items" />
      </div>

      {/* Replaces the two technical notices this page used to carry (the HVL
          "confidence modifier" note and the "ADUS denominator" one) with the
          same facts in plain words. Collapsible, open by default: it is the
          legend for the badges in the table below. Collapsed by default so the
          page opens on the data; expanding it is one click. */}
      <details className="card card__pad collapse collapse--card">
        <summary>
          <span className="section-h">What These Groups Mean</span>
        </summary>
        <div className="explain" style={{ marginTop: 14 }}>
          <div className="explain__row">
            <span className="tag tag--ok">Fast</span>
            <p>Sells regularly. Keep these in stock — running out costs the most sales.</p>
          </div>
          <div className="explain__row">
            <span className="tag tag--warn">Slow</span>
            <p>Sells now and then. Worth stocking, but in smaller quantities.</p>
          </div>
          <div className="explain__row">
            <span className="tag tag--crit">Non-Moving</span>
            <p>No sales recorded. Review before ordering more.</p>
          </div>
          {hvlCount > 0 && (
            <div className="explain__row">
              <span className="tag tag--hvl">Thin history</span>
              <p>
                {hvlCount} fast-selling item{hvlCount === 1 ? '' : 's'}{hvlCount === 1 ? ' has' : ' have'} only
                been counted on a handful of days. {hvlCount === 1 ? 'It sells' : 'They sell'} quickly, but there is
                less history behind that, so treat the figure as less certain.
              </p>
            </div>
          )}
        </div>
        <div className="hint" style={{ marginTop: 12 }}>
          Days when an item was out of stock are left out of the calculation, so an item is never
          called slow-moving just because there was nothing on the shelf to sell.
        </div>
      </details>

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Best Sellers</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="hint">
              top 15 by units sold{bestBand !== 'All' ? ` · ${FSN_LABEL[bestBand]} only` : ''}
            </span>
            <select className="inline-select" value={bestBand}
                    onChange={e => setBestBand(e.target.value)}
                    aria-label="Filter best sellers by group">
              <option value="All">All Groups</option>
              <option value="F">{FSN_LABEL.F}</option>
              <option value="S">{FSN_LABEL.S}</option>
              <option value="N">{FSN_LABEL.N}</option>
            </select>
          </span>
        </div>
        {topSellers.length === 0
          ? <div className="empty">No {FSN_LABEL[bestBand]?.toLowerCase() ?? ''} product has recorded sales.</div>
          : <HBars data={topSellers} color="var(--ink)" valueFmt={num} />}
      </div>

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">FSN Classification Item List</span>
          <span className="hint">
            {filtered
              ? <>showing {num(shown.length)} of {num(products.length)} products</>
              : <>{num(products.length)} products</>}
          </span>
        </div>

        <div className="filter-row">
          <input
            type="search"
            value={q}
            placeholder="Search Item, Supplier Or Category…"
            onChange={e => setQ(e.target.value)}
            aria-label="Search products"
          />
          <select value={fsn} onChange={e => setFsn(e.target.value)} aria-label="Filter by how it moves">
            <option value="All">All Groups</option>
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
            {suppliers.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        <div className="hint" style={{ margin: '2px 0 10px' }}>
          Click a column heading to sort. <b>Avg / Month</b> is the average number of units sold per month.
        </div>

        {shown.length === 0
          ? <div className="empty">No product matches those filters.</div>
          : <DataTable columns={columns} data={shown} pageSize={10} minWidth={880} />}
      </div>

      {/* No onOpenFullList: the full list is the card directly behind this
          modal, so the button would point at itself. */}
      <FsnBandModal band={band} onClose={() => setBand(null)} products={products} />
    </div>
  );
}
