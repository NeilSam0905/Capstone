/* USTore screens — Overview + Tally Interface */

function Overview() {
  const D = window.USTORE;
  const products = D.PRODUCTS_ENRICHED;
  const totalRevenue = products.reduce((s, p) => s + p.totalRevenue, 0);
  const totalUnits = products.reduce((s, p) => s + p.totalUnits, 0);
  const activeSKUs = products.length;
  const reorderNow = products.filter((p) => p.status === 'REORDER NOW').length;
  const approaching = products.filter((p) => p.status === 'APPROACHING').length;
  const belowROP = reorderNow + approaching;

  const top10 = [...products].sort((a, b) => b.totalRevenue - a.totalRevenue).slice(0, 10).
  map((p) => ({ name: p.name.length > 21 ? p.name.slice(0, 19) + '…' : p.name, value: p.totalRevenue }));

  const catMap = {};
  products.forEach((p) => {catMap[p.category] = (catMap[p.category] || 0) + p.totalRevenue;});
  const catData = Object.entries(catMap).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);

  const fsn = { F: 0, S: 0, N: 0 };
  products.forEach((p) => fsn[p.fsn_class]++);
  const t = activeSKUs || 1;

  return (
    <div className="scroll">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap)' }}>

        {/* KPIs */}
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          <KPI label="Total Revenue" value={pesoK(totalRevenue)} sub="Jan – Dec 2024" icon="peso" accent />
          <KPI label="Total Units Sold" value={totalUnits.toLocaleString()} sub="All products" icon="box" />
          <KPI label="Active SKUs" value={activeSKUs} sub="In catalog" icon="tag" />
          <KPI label="Items Below / Near ROP" value={belowROP} sub="Requiring attention" icon="alert" tone="crit" />
        </div>

        {/* Stock status banner */}
        <div className="banner">
          <span className="section-h">Stock Status</span>
          <div className="banner__item"><span className="dot" style={{ background: 'var(--crit)' }} /><b style={{ color: 'var(--crit)' }}>{reorderNow}</b> items REORDER NOW</div>
          <div className="banner__item"><span className="dot" style={{ background: 'var(--warn)' }} /><b style={{ color: 'var(--warn)' }}>{approaching}</b> items APPROACHING ROP</div>
          <div className="banner__item"><span className="dot" style={{ background: 'var(--ok)' }} /><b style={{ color: 'var(--ok)' }}>{activeSKUs - belowROP}</b> items OK</div>
        </div>

        {/* Charts row 1 */}
        <div className="grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
          <div className="card card__pad">
            <div className="section-h" style={{ marginBottom: 14 }}>Monthly Revenue Trend — 2024</div>
            <LineChart data={D.MONTHLY_REVENUE} />
          </div>
          <div className="card card__pad">
            <div className="section-h" style={{ marginBottom: 14 }}>Revenue by Category</div>
            <Donut data={catData} />
          </div>
        </div>

        {/* Charts row 2 */}
        <div className="grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
          <div className="card card__pad">
            <div className="section-h" style={{ marginBottom: 16 }}>Top 10 Products by Revenue</div>
            <HBars data={top10} />
          </div>
          <div className="card card__pad">
            <div className="section-h" style={{ marginBottom: 14 }}>FSN Classification</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <FSNStat label="Fast-Moving" count={fsn.F} pct={Math.round(fsn.F / t * 100)} tone="ok" />
              <FSNStat label="Slow-Moving" count={fsn.S} pct={Math.round(fsn.S / t * 100)} tone="warn" />
              <FSNStat label="Non-Moving" count={fsn.N} pct={Math.round(fsn.N / t * 100)} tone="crit" />
            </div>
            <div style={{ marginTop: 14, height: 14, borderRadius: 100, overflow: 'hidden', display: 'flex' }}>
              <div style={{ width: `${fsn.F / t * 100}%`, background: 'var(--ok)' }} />
              <div style={{ width: `${fsn.S / t * 100}%`, background: 'var(--warn)' }} />
              <div style={{ width: `${fsn.N / t * 100}%`, background: 'var(--crit)' }} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6, textAlign: 'right' }}>{activeSKUs} total SKUs</div>
          </div>
        </div>

        {/* Advisories */}
        <div>
          <div className="section-h" style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
            <span style={{ color: 'var(--info)' }}><Icon name="info" size={14} /></span> Upcoming Event Advisories
          </div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            {D.ADVISORIES.map((a, i) =>
            <div key={i} className={'card adv sev-' + a.severity}>
                <div className="adv__head">
                  <div className="adv__title">{a.event}</div>
                  <span className={'tag ' + (a.severity === 'high' ? 'tag--crit' : a.severity === 'moderate' ? 'tag--warn' : 'tag--info')}>{a.severity}</span>
                </div>
                <div className="adv__time">{a.timeframe}</div>
                <div className="adv__impact">{a.impact}</div>
                <ul className="adv__list">
                  {a.recommendations.map((r, j) => <li key={j}>{r}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>

      </div>
    </div>);

}

function KPI({ label, value, sub, icon, accent, tone }) {
  const valClass = tone === 'crit' && value > 0 ? 'is-crit' : '';
  return (
    <div className={'card kpi' + (accent ? ' is-accent' : '')}>
      <div className="kpi__top">
        <span className="kpi__label">{label}</span>
        <span className="kpi__icon"><Icon name={icon} size={18} /></span>
      </div>
      <div className={'kpi__val ' + valClass}>{value}</div>
      <div className="kpi__sub">{sub}</div>
    </div>);

}

/* ---------------- Tally Interface ---------------- */
const TYPE_TAG = { Sale: 'tag--ok', Return: 'tag--info', Damage: 'tag--crit', 'Internal Transfer': 'tag--hvl' };

function Tally({ setView }) {
  const D = window.USTORE;
  const [itemId, setItemId] = React.useState('');
  const [qty, setQty] = React.useState('');
  const [txType, setTxType] = React.useState('Sale');
  const [date, setDate] = React.useState('2026-05-05');
  const product = D.PRODUCTS.find((p) => p.id === Number(itemId));

  return (
    <div className="tally-wrap">
      <header className="tally-head" style={{ backgroundColor: "rgb(0, 0, 0)" }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <img src="app/assets/ustore-mark.png" alt="USTore" style={{ height: 40, width: 'auto', display: 'block' }} />
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span className="wordmark" style={{ fontSize: 22 }}>USTore</span>
              <span style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>Digital Tally Interface</span>
            </div>
            <div className="tally-head__sub">Internal Inventory Counting Tool — Non-Transactional</div>
          </div>
        </div>
        <button className="btn btn--gold" onClick={() => setView('dashboard')}>
          View Analytics Dashboard <Icon name="arrow" size={15} />
        </button>
      </header>

      <div className="tally-body">
        {/* Entry form */}
        <div className="card card__pad">
          <div className="section-h" style={{ marginBottom: 16 }}>Record Inventory Entry</div>
          <div className="form-grid">
            <div className="field">
              <label>Date</label>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <div className="field">
              <label>Transaction Type</label>
              <select value={txType} onChange={(e) => setTxType(e.target.value)}>
                {D.TRANSACTION_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div className="field col-2">
              <label>Item</label>
              <select value={itemId} onChange={(e) => setItemId(e.target.value)}>
                <option value="">— Select an item —</option>
                {D.PRODUCTS.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.category})</option>)}
              </select>
            </div>
            <div className="field">
              <label>Quantity</label>
              <input type="number" min="1" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Enter quantity" />
            </div>
            <div className="field">
              <label>Supplier</label>
              <input readOnly value={product ? product.supplier : ''} placeholder="Auto-filled on item selection" />
            </div>
          </div>
          <div style={{ marginTop: 18, display: 'flex', alignItems: 'center', gap: 14 }}>
            <button className="btn btn--ink">Record Entry</button>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>Fields will be validated before submission</span>
          </div>
        </div>

        {/* Recent entries */}
        <div className="card card__pad">
          <div className="section-h" style={{ marginBottom: 14 }}>Recent Entries</div>
          <table className="tbl">
            <thead>
              <tr>
                <th>Date</th><th>Item</th><th className="num">Qty</th><th>Supplier</th><th>Type</th>
              </tr>
            </thead>
            <tbody>
              {D.RECENT_ENTRIES.map((e, i) =>
              <tr key={i}>
                  <td className="mono" style={{ fontSize: 12 }}>{e.date}</td>
                  <td className="strong">{e.item}</td>
                  <td className="num strong">{e.qty}</td>
                  <td style={{ fontSize: 12 }}>{e.supplier}</td>
                  <td><span className={'tag ' + TYPE_TAG[e.type]}>{e.type}</span></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Status bar */}
        <div className="card statusbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--ok)', display: 'grid', placeItems: 'center' }}><Icon name="db" size={14} /></span>
            <span>Data Pipeline Status:</span>
            <b style={{ color: 'var(--ok)' }}>Connected to SQLite Star Schema</b>
            <span className="muted">|</span>
            <span>Last sync: May 5, 2026 — 08:14 AM</span>
          </div>
          <div className="ok-text"><Icon name="check" size={14} /> All systems operational</div>
        </div>
      </div>
    </div>);

}

Object.assign(window, { Overview, Tally, KPI });