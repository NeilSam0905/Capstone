import { getSuppliers, getCategories } from '../services/dataService';
import useData from '../hooks/useData';
import Icon from './Icon';

// Shortest first. "This Month" is the most recent month carrying sales,
// which is not always the current calendar one — see catalog.range_cutoff.
const DATE_RANGES = ['This Month', 'Last 3 Months', 'Last 6 Months', 'Last 12 Months', 'All Time'];

/**
 * Topbar + filters — markup and classes from the redesign prototype.
 *
 * `show` names the filters this page actually honours, and the bar renders
 * nothing but the title when it is empty. A control that changes nothing on
 * screen is worse than no control at all: it invites the reader to trust a
 * cut that was never applied. App.jsx owns the per-page list.
 */
export default function FilterBar({
  filters, setFilters, pageTitle, show = [],
  forecastableSuppliers = false, supplierMonth,
}) {
  const { data: suppliers } = useData(
    () => getSuppliers({ forecastable: forecastableSuppliers, month: supplierMonth }),
    [forecastableSuppliers, supplierMonth], []);
  const { data: categories } = useData(getCategories, [], []);

  const update = (key, val) => setFilters(f => ({ ...f, [key]: val }));

  return (
    <header className="topbar">
      <div>
        <div className="topbar__crumb">USTore · Forecasting</div>
        <div className="topbar__title">{pageTitle}</div>
      </div>
      {show.length > 0 && (
        <div className="topbar__right">
          <span style={{ color: 'var(--muted)', display: 'grid', placeItems: 'center' }}>
            <Icon name="filter" size={15} />
          </span>
          {show.includes('dateRange') && (
            <Filter icon="cal" value={filters.dateRange} onChange={v => update('dateRange', v)} options={DATE_RANGES} />
          )}
          {show.includes('supplier') && (
            <Filter value={filters.supplier} onChange={v => update('supplier', v)} options={suppliers} />
          )}
          {show.includes('category') && (
            <Filter value={filters.category} onChange={v => update('category', v)} options={categories} />
          )}
        </div>
      )}
    </header>
  );
}

function Filter({ value, onChange, options, icon }) {
  // A selected value the list does not carry — a supplier held over from a
  // page with a wider list, or a list that has not loaded yet — would leave
  // the select displaying something other than the filter actually in force.
  const opts = value != null && !options.includes(value) ? [value, ...options] : options;

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
        {opts.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
      <span className="filter__chev">▾</span>
    </div>
  );
}
