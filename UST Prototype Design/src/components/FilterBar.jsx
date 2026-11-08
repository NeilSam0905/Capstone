import { useId, useState } from 'react';
import { getSuppliers, getCategories, getMonths, CUSTOM_RANGE } from '../services/dataService';
import useData from '../hooks/useData';
import { shortMonth, longMonth } from '../lib/format';
import Icon from './Icon';
import Modal from './Modal';
import SearchSelect from './SearchSelect';

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
 *
 * `action` is a control that belongs on every page whatever its filters (the
 * Advisories button); it sits at the end of the row, after the filters.
 */
export default function FilterBar({
  filters, setFilters, pageTitle, show = [],
  forecastableSuppliers = false, supplierPeriod, action = null,
}) {
  const { data: suppliers } = useData(
    () => getSuppliers({ forecastable: forecastableSuppliers, period: supplierPeriod }),
    [forecastableSuppliers, supplierPeriod?.year, supplierPeriod?.month, supplierPeriod?.start,
      supplierPeriod?.end], []);
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
      {(show.length > 0 || action) && (
        <div className="topbar__right">
          {show.length > 0 && (
            <span className="topbar__filter-icon">
              <Icon name="filter" size={15} />
            </span>
          )}
          {show.includes('dateRange') && (
            <Filter label="Date Range" icon="cal" value={rangeValue}
                    onChange={setDateRange}
                    options={allowCustom ? [...DATE_RANGES, CUSTOM_RANGE] : DATE_RANGES}
                    labels={{ [CUSTOM_RANGE]: customLabel }} />
          )}
          {/* Re-picking "Custom Range" in the list also reopens the picker,
              but a visible button is easier to find than a repeated pick. */}
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
            <Filter label="Supplier" value={filters.supplier} wide
                    onChange={v => update('supplier', v)} options={suppliers} />
          )}
          {show.includes('category') && (
            <Filter label="Category" value={filters.category}
                    onChange={v => update('category', v)} options={categories} />
          )}
          {action}
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
    <Modal open onClose={onClose} width={440} title="Custom Date Range" overflowVisible
           subtitle="Whole months, from the first to the last one inclusive.">
      <div className="form-grid">
        <div className="field">
          <label htmlFor={fromId}>From</label>
          <SearchSelect id={fromId} variant="field" minWidth={0} searchable={false}
                        value={from ?? ''} onChange={setFrom}
                        options={months.filter(m => !to || m <= to).map(m => ({ value: m, label: longMonth(m) }))} />
        </div>
        <div className="field">
          <label htmlFor={toId}>To</label>
          <SearchSelect id={toId} variant="field" minWidth={0} searchable={false}
                        value={to ?? ''} onChange={setTo}
                        options={months.filter(m => !from || m >= from).map(m => ({ value: m, label: longMonth(m) }))} />
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
   a dropdown shows its selected value, so "All" on its own never says all of
   WHAT. The <label> is bound to the dropdown's button by id, so it is also
   what a screen reader announces.

   The app's own dropdown (SearchSelect), not a native <select>: the browser's
   list ignored the app's styling (a bright blue highlight, square corners)
   and looked like no other dropdown in the app. A list longer than
   SEARCH_FROM options gets a search box - the supplier list runs to a
   dozen and a half names. */
const SEARCH_FROM = 10;

function Filter({ value, onChange, options, icon, label, labels = {}, wide = false }) {
  const id = useId();

  // A selected value the list does not carry — a supplier held over from a
  // page with a wider list, or a list that has not loaded yet — would leave
  // the dropdown displaying something other than the filter actually in force.
  const opts = value != null && !options.includes(value) ? [value, ...options] : options;

  return (
    <div className={'filter-field' + (wide ? ' filter-field--wide' : '')}>
      <label className="filter-field__label" htmlFor={id}>{label}</label>
      <SearchSelect id={id} value={value} onChange={onChange} minWidth={0} icon={icon}
                    searchable={opts.length > SEARCH_FROM}
                    placeholder={`Search ${label.toLowerCase()}…`}
                    options={opts.map(o => ({ value: o, label: labels[o] ?? o }))} />
    </div>
  );
}
