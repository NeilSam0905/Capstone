/* USTore icons — minimal lucide-style stroke icons */
function Icon({ name, size = 18, stroke = 2 }) {
  const p = {
    dashboard: <><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></>,
    tag: <><path d="M20.59 13.41 13.42 20.6a2 2 0 0 1-2.83 0L3 13V3h10l7.59 7.59a2 2 0 0 1 0 2.82Z"/><circle cx="7.5" cy="7.5" r="1.5" fill="currentColor" stroke="none"/></>,
    trend: <><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></>,
    bell: <><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></>,
    peso: <><path d="M6 21V4h6a5 5 0 0 1 0 10H6"/><line x1="3.5" y1="9" x2="13" y2="9"/><line x1="3.5" y1="12.5" x2="13" y2="12.5"/></>,
    box: <><path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="m3 8 9 5 9-5"/><path d="M12 13v8"/></>,
    alert: <><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></>,
    info: <><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></>,
    arrow: <><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></>,
    db: <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></>,
    check: <><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></>,
    filter: <><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></>,
    cal: <><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></>,
  }[name];
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round">{p}</svg>;
}

const NAV = [
  { id: 'overview', label: 'Dashboard Overview', icon: 'dashboard' },
  { id: 'classification', label: 'FSN Classification', icon: 'tag' },
  { id: 'forecast', label: 'Demand Forecast', icon: 'trend' },
  { id: 'reorder', label: 'Reorder Alerts', icon: 'bell' },
  { id: 'report', label: 'Batch Sales Report', icon: 'file' },
];

/* ---------------- Sidebar ---------------- */
function Sidebar({ page, setPage, setView }) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <img src="assets/ustore-mark.png" alt="USTore" className="brand-mark" />
        <div>
          <div className="wordmark">USTore</div>
          <div className="brand-sub">Inventory Analytics</div>
        </div>
      </div>
      <nav className="nav">
        <div className="nav__label">Analytics</div>
        {NAV.map(n => (
          <button key={n.id} className={'nav__item' + (page === n.id ? ' active' : '')} onClick={() => setPage(n.id)}>
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
  );
}

/* ---------------- Topbar ---------------- */
const PAGE_TITLES = {
  overview: 'Dashboard Overview', classification: 'FSN Classification',
  forecast: 'Demand Forecast', reorder: 'Reorder Alerts', report: 'Monthly Batch Sales Report',
};
function Topbar({ page }) {
  return (
    <header className="topbar">
      <div>
        <div className="topbar__crumb">USTore · Forecasting</div>
        <div className="topbar__title">{PAGE_TITLES[page]}</div>
      </div>
      <div className="topbar__right">
        <span style={{ color: 'var(--muted)', display: 'grid', placeItems: 'center' }}><Icon name="filter" size={15} /></span>
        <Filter icon="cal" value="Last 12 Months" options={['Last 3 Months', 'Last 6 Months', 'Last 12 Months', 'All Time']} />
        <Filter value="All Suppliers" options={window.USTORE.SUPPLIERS} />
        <Filter value="All Categories" options={window.USTORE.CATEGORIES} />
      </div>
    </header>
  );
}
function Filter({ value, options, icon }) {
  const [v, setV] = React.useState(value);
  return (
    <div className="filter">
      {icon && <span style={{ position: 'absolute', left: 10, color: 'var(--muted)', pointerEvents: 'none' }}><Icon name={icon} size={13} /></span>}
      <select value={v} onChange={e => setV(e.target.value)} style={icon ? { paddingLeft: 30 } : null}>
        {options.map(o => <option key={o}>{o}</option>)}
      </select>
      <span className="filter__chev">▾</span>
    </div>
  );
}

Object.assign(window, { Icon, Sidebar, Topbar, NAV, PAGE_TITLES });
