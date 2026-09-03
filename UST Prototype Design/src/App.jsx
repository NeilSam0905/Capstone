import { useMemo, useState } from 'react';
import FilterBar from './components/FilterBar';
import Overview from './pages/Overview';
import Forecast from './pages/Forecast';
import Classification from './pages/Classification';
import Reorder from './pages/Reorder';
import BatchReport from './pages/BatchReport';
import TallyInterface from './pages/TallyInterface';
import PowerBIDashboard from './pages/PowerBIDashboard';
import Icon from './components/Icon';
import ErrorBanner from './components/ErrorBanner';
import useData from './hooks/useData';
import { getMeta, getMonths } from './services/dataService';
import brandMark from './assets/ustore-mark.png';

// Shell markup and class names come from the redesign prototype
// ("design-reference/chrome.jsx"); only the routing is ours.
const PAGES = [
  { id: 'overview',       label: 'Dashboard Overview', icon: 'dashboard' },
  { id: 'classification', label: 'FSN Classification', icon: 'tag' },
  { id: 'forecast',       label: 'Demand Forecast',    icon: 'trend' },
  { id: 'reorder',        label: 'Reorder Alerts',     icon: 'bell' },
  { id: 'report',         label: 'Batch Sales Report', icon: 'file' },
  // TODO: Phase 2 — the Power BI report gets embedded into this route
  { id: 'powerbi',        label: 'Analytics (Power BI)', icon: 'chart' },
];

const PAGE_TITLES = {
  overview:       'Dashboard Overview',
  classification: 'FSN Classification',
  forecast:       'Demand Forecast',
  reorder:        'Reorder Alerts',
  report:         'Monthly Batch Sales Report',
  powerbi:        'Analytics Dashboard (Power BI)',
};

/** What every filter means when it is not applied. A page that hides a
 *  filter is served this value for it, so a supplier left selected on
 *  Overview cannot go on silently cutting a page whose topbar has no way to
 *  show it — or to clear it. */
const UNFILTERED = {
  dateRange: 'All Time',
  supplier: 'All Suppliers',
  category: 'All Categories',
};

/** Which topbar filters each page honours.
 *
 *  Only the pages whose queries actually read a filter offer it:
 *   - forecast has no date range because the model forecasts the next month
 *     and nothing else; a history window cannot change that horizon.
 *   - report takes supplier but no date range: it is a single-month document
 *     with its own month picker, and a second date control beside that one
 *     could only contradict it.
 *   - reorder and powerbi take no topbar filters at all. Reorder is a
 *     stock-position decision about every item, and the Power BI report
 *     carries its own slicers. */
const PAGE_FILTERS = {
  overview:       ['dateRange', 'supplier', 'category'],
  classification: ['dateRange', 'supplier', 'category'],
  forecast:       ['supplier', 'category'],
  reorder:        [],
  report:         ['supplier'],
  powerbi:        [],
};

export default function App() {
  const [view, setView] = useState('tally');
  const [page, setPage] = useState('overview');
  const [filters, setFilters] = useState(UNFILTERED);
  if (view === 'tally') return <TallyInterface setView={setView} />;

  // Dashboard-shell connectivity probe. If the backend is down, this is
  // what tells the user why every widget below is stuck loading — without
  // it, a failed fetch just leaves every screen spinning with no reason
  // given (dataService.js's request() throws a specific message for this).
  return <Dashboard page={page} setPage={setPage} filters={filters} setFilters={setFilters} setView={setView} />;
}

function Dashboard({ page, setPage, filters, setFilters, setView }) {
  const { error: connectionError } = useData(getMeta, []);

  // The batch report's month lives here rather than inside that page because
  // the topbar needs it too: its supplier list is scoped to the month on
  // show. Both readers therefore have to see the same value, and the page
  // alone cannot supply one to a sibling above it. (getMonths is cached, so
  // BatchReport asking for the same list again costs no request.)
  const { data: months } = useData(getMonths, [], []);
  const [reportMonth, setReportMonth] = useState(null);
  const selectedMonth = reportMonth ?? months[months.length - 1] ?? null;

  const PageComponent = {
    overview:       Overview,
    classification: Classification,
    forecast:       Forecast,
    reorder:        Reorder,
    report:         BatchReport,
    powerbi:        PowerBIDashboard,
  }[page];

  // The page sees only the filters its topbar offers; everything else comes
  // through unfiltered. Filter state itself is kept whole across navigation,
  // so returning to Overview finds the cut you left there.
  //
  // Memoised on purpose: every page passes this object straight into a
  // useData dependency array, which compares by identity. Rebuilding it on
  // each render would refetch the whole page every time anything above it
  // re-rendered.
  const shown = PAGE_FILTERS[page] ?? [];
  const pageFilters = useMemo(() => {
    const out = { ...UNFILTERED };
    (PAGE_FILTERS[page] ?? []).forEach(k => { out[k] = filters[k]; });
    return out;
  }, [page, filters]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar__brand">
          <img src={brandMark} alt="USTore" className="brand-mark" />
          <div>
            <div className="wordmark">USTore</div>
            <div className="brand-sub">Inventory Analytics</div>
          </div>
        </div>

        <nav className="nav">
          <div className="nav__label">Analytics</div>
          {PAGES.map(n => (
            <button
              key={n.id}
              className={'nav__item' + (page === n.id ? ' active' : '')}
              onClick={() => setPage(n.id)}
            >
              <Icon name={n.icon} />
              <span>{n.label}</span>
            </button>
          ))}
        </nav>

        <div className="nav__foot">
          <button className="nav__back" onClick={() => setView('tally')} title="Back to Tally Interface">
            <Icon name="db" size={14} /><span>Back to Tally Interface</span>
          </button>
        </div>
      </aside>

      <div className="main">
        <FilterBar
          filters={filters}
          setFilters={setFilters}
          pageTitle={PAGE_TITLES[page]}
          show={shown}
          forecastableSuppliers={page === 'forecast'}
          supplierMonth={page === 'report' ? selectedMonth : undefined}
        />
        <div className="scroll">
          {connectionError && <ErrorBanner error={connectionError} />}
          {/* setPage is handed to every page so a KPI or a chart can act as a
              link into the page that explains it — the ROP tile into Reorder
              Alerts, the FSN card into Classification. */}
          <PageComponent
            filters={pageFilters}
            setPage={setPage}
            {...(page === 'report' ? { month: selectedMonth, setMonth: setReportMonth } : {})}
          />
        </div>
      </div>
    </div>
  );
}
