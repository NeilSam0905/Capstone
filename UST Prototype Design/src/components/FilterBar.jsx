import { useId, useState } from 'react';
import { getSuppliers, getCategories, getMonths, CUSTOM_RANGE } from '../services/dataService';
import useData from '../hooks/useData';
import { shortMonth, longMonth } from '../lib/format';
import Icon from './Icon';
import Modal from './Modal';

// Shortest first. "This Month" is the most recent month carrying sales,
// which is not always the current calendar one — see catalog.range_cutoff.
const DATE_RANGES = ['This Month', 'Last 3 Months', 'Last 6 Months', 'Last 12 Months', 'All Time'];
// Months a freshly opened custom range starts with, ending at the latest one.
const CUSTOM_DEFAULT_MONTHS = 6;

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
  const { data: months } = useData(getMonths, [], []);

  const update = (key, val) => setFilters(f => ({ ...f, [key]: val }));
  const [rangeOpen, setRangeOpen] = useState(false);

  // "Custom Range" only where the page honours it (App.jsx PAGE_FILTERS). A
  // custom range carried over from another page reads as "All Time" here,
  // which is what App.jsx actually serves such a page.
  const allowCustom = show.includes('customRange');
  const isCustom = allowCustom && filters.dateRange === CUSTOM_RANGE;
  const rangeValue = !allowCustom && filters.dateRange === CUSTOM_RANGE ? 'All Time' : filters.dateRange;
  const customLabel = isCustom && filters.rangeFrom && filters.rangeTo
    ? `${shortMonth(filters.rangeFrom)} – ${shortMonth(filters.rangeTo)}`
    : CUSTOM_RANGE;

  // "Custom Range" opens the month picker instead of applying anything: the
  // filter only changes when the picker's Apply is pressed, so cancelling
  // leaves the previous range in force.
  function setDateRange(v) {
    if (v === CUSTOM_RANGE) setRangeOpen(true);
    else update('dateRange', v);
  }

  function applyRange(from, to) {
    setFilters(f => ({ ...f, dateRange: CUSTOM_RANGE, rangeFrom: from, rangeTo: to }));
    setRangeOpen(false);
  }

  // Open on the range in force, else the latest few months.
  const draftFrom = (isCustom && filters.rangeFrom)
    || months[Math.max(0, months.length - CUSTOM_DEFAULT_MONTHS)];
  const draftTo = (isCustom && filters.rangeTo) || months[months.length - 1];

  return (
    <header className="topbar">
      <div>
        <div className="topbar__crumb">USTore · Forecasting</div>
        <div className="topbar__title">{pageTitle}</div>
      </div>
      {show.length > 0 && (
        <div className="topbar__right">
          <span className="topbar__filter-icon">
            <Icon name="filter" size={15} />
          </span>
          {show.includes('dateRange') && (
            <Filter label="Date Range" icon="cal" value={rangeValue}
                    onChange={setDateRange}
                    options={allowCustom ? [...DATE_RANGES, CUSTOM_RANGE] : DATE_RANGES}
                    labels={{ [CUSTOM_RANGE]: customLabel }} />
          )}
          {/* Re-picking the option already selected fires no change event,
              so an active custom range gets its own way back into the picker. */}
          {isCustom && (
            <button type="button" className="btn btn--ghost btn--sm topbar__edit-range"
                    onClick={() => setRangeOpen(true)} title="Change the custom date range">
              <Icon name="cal" size={13} /> Change
            </button>
          )}
          {rangeOpen && (
            <CustomRangeModal months={months} initialFrom={draftFrom} initialTo={draftTo}
                              onApply={applyRange} onClose={() => setRangeOpen(false)} />
          )}
          {show.includes('supplier') && (
            <Filter label="Supplier" value={filters.supplier}
                    onChange={v => update('supplier', v)} options={suppliers} />
          )}
          {show.includes('category') && (
            <Filter label="Category" value={filters.category}
                    onChange={v => update('category', v)} options={categories} />
          )}
        </div>
      )}
    </header>
  );
}

/* The custom date range picker. Mounted only while open, so its draft starts
   from the range in force each time and a cancelled edit leaves no trace.
   From/To are whole months, inclusive, limited to months with sales; each
   list stops at the other end so the pair can never be reversed. */
function CustomRangeModal({ months, initialFrom, initialTo, onApply, onClose }) {
  const [from, setFrom] = useState(initialFrom);
  const [to, setTo] = useState(initialTo);
  const fromId = useId(), toId = useId();
  const count = months.filter(m => m >= from && m <= to).length;

  return (
    <Modal open onClose={onClose} width={440} title="Custom Date Range"
           subtitle="Whole months, from the first to the last one inclusive.">
      <div className="form-grid">
        <div className="field">
          <label htmlFor={fromId}>From</label>
          <select id={fromId} value={from ?? ''} onChange={e => setFrom(e.target.value)}>
            {months.filter(m => !to || m <= to).map(m => <option key={m} value={m}>{longMonth(m)}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor={toId}>To</label>
          <select id={toId} value={to ?? ''} onChange={e => setTo(e.target.value)}>
            {months.filter(m => !from || m >= from).map(m => <option key={m} value={m}>{longMonth(m)}</option>)}
          </select>
        </div>
      </div>
      <div className="hint" style={{ marginTop: 10 }}>
        {count} month{count === 1 ? '' : 's'} with sales in this range
      </div>
      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn btn--ink btn--sm" disabled={!from || !to} onClick={() => onApply(from, to)}>
          Apply
        </button>
        <button className="btn btn--ghost btn--sm" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/* The label sits above the control rather than inside it as a placeholder:
   a select shows its selected value, so "All" on its own never says all of
   WHAT. The <label> is bound to the select by id, so it is also what a
   screen reader announces. */
function Filter({ value, onChange, options, icon, label, labels = {} }) {
  const id = useId();

  // A selected value the list does not carry — a supplier held over from a
  // page with a wider list, or a list that has not loaded yet — would leave
  // the select displaying something other than the filter actually in force.
  const opts = value != null && !options.includes(value) ? [value, ...options] : options;

  return (
    <div className="filter-field">
      <label className="filter-field__label" htmlFor={id}>{label}</label>
      <div className="filter">
        {icon && (
          <span style={{ position: 'absolute', left: 10, color: 'var(--muted)', pointerEvents: 'none' }}>
            <Icon name={icon} size={13} />
          </span>
        )}
        <select
          id={id}
          value={value}
          onChange={e => onChange(e.target.value)}
          style={icon ? { paddingLeft: 30 } : undefined}
        >
          {opts.map(o => <option key={o} value={o}>{labels[o] ?? o}</option>)}
        </select>
        <span className="filter__chev">▾</span>
      </div>
    </div>
  );
}
