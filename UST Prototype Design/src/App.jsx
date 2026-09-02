import { useState } from 'react';
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
import { getMeta } from './services/dataService';
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
  powerbi:        'Analytics Dashboard — Power BI',
};

const DEFAULT_FILTERS = {
  dateRange: 'Last 12 Months',
  supplier: 'All Suppliers',
  category: 'All Categories',
};

export default function App() {
  const [view, setView] = useState('tally');
  const [page, setPage] = useState('overview');
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  if (view === 'tally') return <TallyInterface setView={setView} />;

  // Dashboard-shell connectivity probe. If the backend is down, this is
  // what tells the user why every widget below is stuck loading — without
  // it, a failed fetch just leaves every screen spinning with no reason
  // given (dataService.js's request() throws a specific message for this).
  return <Dashboard page={page} setPage={setPage} filters={filters} setFilters={setFilters} setView={setView} />;
}

function Dashboard({ page, setPage, filters, setFilters, setView }) {
  const { error: connectionError } = useData(getMeta, []);

  const PageComponent = {
    overview:       Overview,
    classification: Classification,
    forecast:       Forecast,
    reorder:        Reorder,
    report:         BatchReport,
    powerbi:        PowerBIDashboard,
  }[page];

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
        <FilterBar filters={filters} setFilters={setFilters} pageTitle={PAGE_TITLES[page]} />
        <div className="scroll">
          {connectionError && <ErrorBanner error={connectionError} />}
          {/* setPage is handed to every page so a KPI or a chart can act as a
              link into the page that explains it — the ROP tile into Reorder
              Alerts, the FSN card into Classification. */}
          <PageComponent filters={filters} setPage={setPage} />
        </div>
      </div>
    </div>
  );
}
