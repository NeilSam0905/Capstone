import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { startPrefetch, stopPrefetch } from './services/prefetch';
import FilterBar from './components/FilterBar';
import Login from './pages/Login';
import Overview from './pages/Overview';
import Forecast from './pages/Forecast';
import Classification from './pages/Classification';
import Reorder from './pages/Reorder';
import BatchReport from './pages/BatchReport';
import TallyInterface from './pages/TallyInterface';
import PowerBIDashboard from './pages/PowerBIDashboard';
import Icon from './components/Icon';
import ErrorBanner from './components/ErrorBanner';
import { AdvisoriesButton, AdvisoriesModal } from './components/Advisories';
import { ADVISORIES_ANCHOR } from './config';
import { resetAdvisoriesJump } from './lib/advisoriesJump';
import AdminConfirm from './components/AdminConfirm';
import useData from './hooks/useData';
import {
  getMeta, getMonths, CUSTOM_RANGE, getSession, logout, onAuthExpired, clearApiCache,
} from './services/dataService';
import brandMark from './assets/ustore-mark.png';
import { displayUser } from './lib/format';
import { periodParams } from './lib/reportPeriod';

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
  report:         'Batch Sales Report',
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
  rangeFrom: null,   // 'YYYY-MM', only read while dateRange is "Custom Range"
  rangeTo: null,
};

/** Which topbar filters each page honours.
 *
 *  Only the pages whose queries actually read a filter offer it:
 *   - forecast has no date range because the model forecasts the next month
 *     and nothing else; a history window cannot change that horizon.
 *   - report takes supplier but no date range: it is a single day, week or
 *     month with its own period picker, and a second date control beside
 *     that one could only contradict it.
 *   - reorder and powerbi take no topbar filters at all. Reorder is a
 *     stock-position decision about every item, and the Power BI report
 *     carries its own slicers.
 *   - forecast is the ONLY page with no topbar category filter. Category is
 *     now the primary axis of that screen, chosen in the page itself, so a
 *     second selector in the topbar could only disagree with the one the user
 *     is looking at - and would empty the page under them by excluding the
 *     very category they had just selected. Overview and Classification keep
 *     theirs: there category is a refinement of what is shown, not the
 *     subject of it.
 *   - 'customRange' adds "Custom Range" (From/To months) to the date range.
 *     A page without it that inherits a custom range sees "All Time" instead,
 *     since its topbar could neither show nor clear the months. */
const PAGE_FILTERS = {
  overview:       ['dateRange', 'customRange', 'supplier', 'category'],
  classification: ['dateRange', 'supplier', 'category'],
  forecast:       ['supplier'],
  reorder:        [],
  report:         ['supplier'],
  powerbi:        [],
};

export default function App() {
  // undefined while the session is being checked, null when signed out.
  const [user, setUser] = useState(undefined);
  const [expired, setExpired] = useState(false);
  // Read by the expiry listener, which is registered once: a 401 that lands
  // after Sign out (a request already in flight) is not "your session ended".
  const signedIn = useRef(false);
  useEffect(() => { signedIn.current = !!user; }, [user]);

  useEffect(() => {
    let live = true;
    getSession()
      .then(s => { if (live) setUser(s.authenticated ? s.user : null); })
      // Backend unreachable: show the login form, whose submit reports why.
      .catch(() => { if (live) setUser(null); });
    // Any request answered "session ended" puts the login page back.
    const off = onAuthExpired(() => {
      stopPrefetch();
      if (signedIn.current) setExpired(true);
      signedIn.current = false;
      setUser(null);
    });
    return () => { live = false; off(); };
  }, []);

  const signOut = useCallback(async () => {
    signedIn.current = false;
    stopPrefetch();
    try { await logout(); } catch { /* signed out locally either way */ }
    clearApiCache();
    // The Overview's floating advisories pill comes back for whoever signs
    // in next, whatever this login did with it (lib/advisoriesJump.js).
    resetAdvisoriesJump();
    setExpired(false);
    setUser(null);
  }, []);

  if (user === undefined) return null;
  if (!user) {
    // Every sign-in starts with the floating advisories pill showing - even
    // when the last login ended by its session expiring, not by Sign out.
    return <Login expired={expired} onLogin={u => { setExpired(false); resetAdvisoriesJump(); setUser(u); }} />;
  }
  // Keyed on the user so signing in again starts from a fresh screen.
  return <Shell key={user} user={user} signOut={signOut} />;
}

function Shell({ user, signOut }) {
  const [view, setView] = useState('tally');
  const [page, setPage] = useState('overview');
  const [filters, setFilters] = useState(UNFILTERED);
  // Warm every dashboard page's data in the background from the start, so
  // the first visit to each paints at once (services/prefetch.js). Only once
  // signed in: before that every request would be refused.
  useEffect(() => { startPrefetch(); }, []);

  // AdminConfirm sits beside whichever view is showing: an edit to a saved
  // record by anyone but the admin waits on it, from either side of the app.
  if (view === 'tally') {
    return (
      <>
        <TallyInterface setView={setView} user={user} signOut={signOut} />
        <AdminConfirm />
      </>
    );
  }

  // Dashboard-shell connectivity probe. If the backend is down, this is
  // what tells the user why every widget below is stuck loading — without
  // it, a failed fetch just leaves every screen spinning with no reason
  // given (dataService.js's request() throws a specific message for this).
  return (
    <>
      <Dashboard page={page} setPage={setPage} filters={filters} setFilters={setFilters}
                 setView={setView} user={user} signOut={signOut} />
      <AdminConfirm />
    </>
  );
}

function Dashboard({ page, setPage, filters, setFilters, setView, user, signOut }) {
  const { data: meta, error: connectionError } = useData(getMeta, []);

  // The batch report's period lives here rather than inside that page
  // because the topbar needs it too: its supplier list is scoped to the
  // period on show. Both readers therefore have to see the same value, and
  // the page alone cannot supply one to a sibling above it. (getMonths and
  // getMeta are cached, so BatchReport asking again costs no request.)
  //
  // Until the reader picks one, it is the latest month with sales, the
  // day/week views open on the last day with sales, and the year view on
  // that month's year.
  const { data: months } = useData(getMonths, [], []);
  const [reportChoice, setReportChoice] = useState({ kind: 'month', month: null, date: null, year: null });
  const reportPeriod = useMemo(() => ({
    kind: reportChoice.kind,
    month: reportChoice.month ?? months[months.length - 1] ?? null,
    date: reportChoice.date ?? meta?.sales_span?.[1] ?? null,
    year: reportChoice.year ?? months[months.length - 1]?.slice(0, 4) ?? null,
  }), [reportChoice, months, meta]);
  const reportParams = useMemo(() => periodParams(reportPeriod), [reportPeriod]);
  const setReportPeriod = useCallback(change => setReportChoice(p => ({ ...p, ...change })), []);

  // The advisories are the prescriptive half of the dashboard, so they are
  // one click away on every page, through the topbar's Advisories button.
  // The Overview already shows them in full,
  // so there the button scrolls down to them; anywhere else it opens them in
  // a dialog over the page. closeAdvisories is stable because Modal re-runs
  // its open effect (and so re-focuses) whenever onClose changes identity.
  const [advisoriesOpen, setAdvisoriesOpen] = useState(false);
  const closeAdvisories = useCallback(() => setAdvisoriesOpen(false), []);
  const openAdvisories = () => {
    const section = page === 'overview' && document.getElementById(ADVISORIES_ANCHOR);
    if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else setAdvisoriesOpen(true);
  };

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
    const keys = PAGE_FILTERS[page] ?? [];
    const out = { ...UNFILTERED };
    keys.forEach(k => { if (k in UNFILTERED) out[k] = filters[k]; });
    if (keys.includes('customRange')) {
      out.rangeFrom = filters.rangeFrom;
      out.rangeTo = filters.rangeTo;
    } else if (out.dateRange === CUSTOM_RANGE) {
      out.dateRange = UNFILTERED.dateRange;
    }
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
          <div className="nav__user">
            <span>Signed in as <b>{displayUser(user)}</b></span>
            <button className="nav__signout" onClick={signOut}>Sign out</button>
          </div>
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
          supplierPeriod={page === 'report' ? reportParams ?? undefined : undefined}
          action={<AdvisoriesButton onClick={openAdvisories} />}
        />
        <AdvisoriesModal open={advisoriesOpen} onClose={closeAdvisories} page={page} setPage={setPage} />
        <div className="scroll">
          {connectionError && <ErrorBanner error={connectionError} />}
          {/* setPage is handed to every page so a KPI or a chart can act as a
              link into the page that explains it — the ROP tile into Reorder
              Alerts, the FSN card into Classification. */}
          <PageComponent
            filters={pageFilters}
            setPage={setPage}
            setView={setView}
            user={user}
            {...(page === 'report' ? { period: reportPeriod, setPeriod: setReportPeriod } : {})}
          />
        </div>
      </div>
    </div>
  );
}
