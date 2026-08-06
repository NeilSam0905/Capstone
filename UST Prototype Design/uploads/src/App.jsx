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
import brandMark from './assets/ustore-mark.png';

// Shell markup and class names come from the redesign prototype
// ("UST Prototype Design/app/chrome.jsx"); only the routing is ours.
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
          <button className="nav__back" onClick={() => setView('tally')}>
            <Icon name="db" size={14} /> Back to Tally Interface
          </button>
        </div>
      </aside>

      <div className="main">
        <FilterBar filters={filters} setFilters={setFilters} pageTitle={PAGE_TITLES[page]} />
        <div className="scroll">
          <PageComponent filters={filters} />
        </div>
      </div>
    </div>
  );
}
