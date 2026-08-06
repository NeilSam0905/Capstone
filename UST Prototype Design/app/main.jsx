/* USTore main app — view routing + Tweaks */

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "gold",
  "chrome": "dark",
  "font": "Plus Jakarta Sans",
  "density": "regular",
  "corners": "soft"
}/*EDITMODE-END*/;

const FONTS = {
  'Plus Jakarta Sans': "'Plus Jakarta Sans', system-ui, sans-serif",
  'Public Sans': "'Public Sans', system-ui, sans-serif",
  'Mulish': "'Mulish', system-ui, sans-serif",
  'Figtree': "'Figtree', system-ui, sans-serif",
};

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [view, setView] = React.useState('dashboard');
  const [page, setPage] = React.useState('overview');

  React.useEffect(() => {
    const r = document.documentElement;
    r.dataset.accent = t.accent;
    r.dataset.chrome = t.chrome;
    r.dataset.density = t.density;
    r.dataset.corners = t.corners;
    r.style.setProperty('--font', FONTS[t.font] || FONTS['Plus Jakarta Sans']);
  }, [t]);

  return (
    <>
      {view === 'tally'
        ? <Tally setView={setView} />
        : (
          <div className="app">
            <Sidebar page={page} setPage={setPage} setView={setView} />
            <div className="main">
              <Topbar page={page} />
              {page === 'overview'
                ? <Overview />
                : <PowerBIEmbed title={PAGE_TITLES[page]} />}
            </div>
          </div>
        )}

      <TweaksPanel>
        <TweakSection label="Brand accent" />
        <TweakColor label="Accent" value={t.accent === 'gold' ? '#F4B400' : t.accent === 'amber' ? '#E89611' : '#8E2A2A'}
          options={['#F4B400', '#E89611', '#8E2A2A']}
          onChange={(hex) => setTweak('accent', hex === '#F4B400' ? 'gold' : hex === '#E89611' ? 'amber' : 'maroon')} />
        <TweakRadio label="Chrome" value={t.chrome} options={['dark', 'light']} onChange={v => setTweak('chrome', v)} />

        <TweakSection label="Typography" />
        <TweakSelect label="Font" value={t.font} options={Object.keys(FONTS)} onChange={v => setTweak('font', v)} />

        <TweakSection label="Layout" />
        <TweakRadio label="Density" value={t.density} options={['compact', 'regular', 'comfy']} onChange={v => setTweak('density', v)} />
        <TweakRadio label="Corners" value={t.corners} options={['sharp', 'soft', 'round']} onChange={v => setTweak('corners', v)} />
      </TweaksPanel>
    </>
  );
}

/* Lightweight placeholder for the four not-yet-built analytics pages */
function Placeholder({ page }) {
  return (
    <div className="scroll">
      <div className="card card__pad" style={{ textAlign: 'center', padding: '64px 32px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
        <span className="kpi__icon" style={{ width: 46, height: 46 }}><Icon name={NAV.find(n => n.id === page).icon} size={22} /></span>
        <div className="section-title" style={{ fontSize: 18 }}>{PAGE_TITLES[page]}</div>
        <div style={{ color: 'var(--muted)', maxWidth: 420, fontSize: 13.5, lineHeight: 1.5 }}>
          This screen is part of the full roll-out. The redesign direction is set by the
          <b style={{ color: 'var(--text-2)' }}> Dashboard Overview</b> and <b style={{ color: 'var(--text-2)' }}>Tally Interface</b> —
          once approved, this page gets the same treatment.
        </div>
        <span className="tag tag--gold" style={{ marginTop: 4 }}>Coming in full build</span>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
