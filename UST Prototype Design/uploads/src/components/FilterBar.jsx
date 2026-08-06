import { getSuppliers, getCategories } from '../services/dataService';
import useData from '../hooks/useData';
import Icon from './Icon';

const DATE_RANGES = ['Last 3 Months', 'Last 6 Months', 'Last 12 Months', 'All Time'];

/** Topbar + filters — markup and classes from the redesign prototype. */
export default function FilterBar({ filters, setFilters, pageTitle }) {
  const { data: suppliers } = useData(getSuppliers, [], []);
  const { data: categories } = useData(getCategories, [], []);

  const update = (key, val) => setFilters(f => ({ ...f, [key]: val }));

  return (
    <header className="topbar">
      <div>
        <div className="topbar__crumb">USTore · Forecasting</div>
        <div className="topbar__title">{pageTitle}</div>
      </div>
      <div className="topbar__right">
        <span style={{ color: 'var(--muted)', display: 'grid', placeItems: 'center' }}>
          <Icon name="filter" size={15} />
        </span>
        <Filter icon="cal" value={filters.dateRange} onChange={v => update('dateRange', v)} options={DATE_RANGES} />
        <Filter value={filters.supplier} onChange={v => update('supplier', v)} options={suppliers} />
        <Filter value={filters.category} onChange={v => update('category', v)} options={categories} />
      </div>
    </header>
  );
}

function Filter({ value, onChange, options, icon }) {
  return (
    <div className="filter">
      {icon && (
        <span style={{ position: 'absolute', left: 10, color: 'var(--muted)', pointerEvents: 'none' }}>
          <Icon name={icon} size={13} />
        </span>
      )}
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={icon ? { paddingLeft: 30 } : undefined}
      >
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
      <span className="filter__chev">▾</span>
    </div>
  );
}
