import { useEffect, useMemo, useState } from 'react';
import { ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';
import Modal from './Modal';
import DataTable from './DataTable';
import Icon from './Icon';
import { num } from '../lib/format';

/**
 * The items in one FSN band (Fast / Slow / Non-Moving).
 *
 * Shared by the Overview's FSN card and the Classification page's three KPI
 * tiles, so both open the identical list rather than two that drift apart.
 */
const BAND = {
  F: { label: 'Fast-Moving',  tone: 'ok',   blurb: 'Sell regularly. Keep these in stock - running out costs the most sales.' },
  S: { label: 'Slow-Moving',  tone: 'warn', blurb: 'Sell now and then. Worth stocking, but in smaller quantities.' },
  N: { label: 'Non-Moving',   tone: 'crit', blurb: 'No sales recorded. Review before ordering more.' },
};

export { BAND };

/** The items in one FSN band.
 *
 *  Separate from ProductsModal on purpose: this one answers "which items are
 *  Fast-moving", so the class is fixed and there is no class filter to offer -
 *  offering one would let the reader filter a Fast-moving list down to the
 *  Slow-moving items, which is nonsense.
 */
export default function FsnBandModal({ band, onClose, products, onOpenFullList }) {
  const [q, setQ] = useState('');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);

  useEffect(() => {
    if (band) { setQ(''); setCategory(ALL_CATEGORIES); setSupplier(ALL_SUPPLIERS); }
  }, [band]);

  const inBand = useMemo(
    () => products.filter(p => p.fsn_class === band), [products, band]);

  const categories = useMemo(
    () => [...new Set(inBand.map(p => p.category))].sort(), [inBand]);
  const suppliers = useMemo(
    () => [...new Set(inBand.map(p => p.supplier_name))].sort(), [inBand]);

  const needle = q.trim().toLowerCase();
  const shown = useMemo(() => inBand.filter(p =>
    (!needle
      || p.item_name.toLowerCase().includes(needle)
      || p.supplier_name.toLowerCase().includes(needle)
      || p.category.toLowerCase().includes(needle))
    && (category === ALL_CATEGORIES || p.category === category)
    && (supplier === ALL_SUPPLIERS || p.supplier_name === supplier)
  ), [inBand, needle, category, supplier]);

  if (!band) return null;
  const meta = BAND[band];

  const columns = [
    { key: 'item_name', label: 'Product Name', strong: true, truncate: true, width: '36%' },
    { key: 'category', label: 'Category', truncate: true, width: '17%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
    { key: 'total_units', label: 'Units Sold', num: true, strong: true, width: '12%', render: num },
    { key: 'avg_monthly', label: 'Avg / Month', num: true, width: '11%', render: v => (v ?? 0).toFixed(1) },
  ];

  const units = shown.reduce((sum, p) => sum + p.total_units, 0);
  const filtered = shown.length !== inBand.length;

  return (
    <Modal
      open={Boolean(band)}
      onClose={onClose}
      title={`${meta.label} Items`}
      subtitle={filtered
        ? `${num(shown.length)} of ${num(inBand.length)} items - ${num(units)} units sold`
        : `${num(inBand.length)} items - ${num(units)} units sold`}
      width={940}
    >
      <div className={`notice notice--${meta.tone === 'ok' ? 'info' : meta.tone}`} style={{ marginBottom: 12 }}>
        <b>{meta.label}.</b> {meta.blurb}
      </div>

      <div className="modal-filters">
        <input
          type="search"
          value={q}
          placeholder="Search Item, Supplier Or Category..."
          onChange={e => setQ(e.target.value)}
          aria-label={`Search ${meta.label} items`}
        />
        <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Filter by category">
          <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={supplier} onChange={e => setSupplier(e.target.value)} aria-label="Filter by supplier">
          <option value={ALL_SUPPLIERS}>{ALL_SUPPLIERS}</option>
          {suppliers.map(sup => <option key={sup} value={sup}>{sup}</option>)}
        </select>
      </div>

      <div className="modal-grow">
        {shown.length === 0
          ? <div className="empty">No {meta.label.toLowerCase()} item matches those filters.</div>
          : <DataTable columns={columns} data={shown.map(p => ({ ...p, rowKey: `fb${p.product_id}` }))}
                       pageSize={12} minWidth={860} />}
      </div>

      {/* Only shown when there is somewhere else to go. On the Classification
          page the full list is already on screen behind the modal, so the
          button would point at itself. */}
      {onOpenFullList && (
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button className="btn btn--ghost btn--sm" onClick={onOpenFullList}>
            Open the full item list <Icon name="arrow" size={12} />
          </button>
        </div>
      )}
    </Modal>
  );
}
