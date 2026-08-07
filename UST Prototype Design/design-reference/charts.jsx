/* USTore lightweight SVG charts (no chart lib) — exported to window */

const peso = n => '₱' + Math.round(n).toLocaleString();
const pesoK = n => n >= 1e6 ? '₱' + (n / 1e6).toFixed(2) + 'M' : '₱' + (n / 1e3).toFixed(0) + 'K';

/* ---------- Line chart: monthly revenue ---------- */
function LineChart({ data, height = 210 }) {
  const W = 720, H = height, padL = 52, padR = 16, padT = 16, padB = 28;
  const xs = data.map(d => d.month);
  const ys = data.map(d => d.revenue);
  const rawMax = Math.max(...ys), rawMin = Math.min(...ys);
  const tickCount = 4;
  const range = rawMax - rawMin || 1;
  const rawStep = range / tickCount;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const min = Math.max(0, Math.floor(rawMin / step) * step - step);
  const max = Math.ceil(rawMax / step) * step;
  const x = i => padL + (i / (data.length - 1)) * (W - padL - padR);
  const y = v => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);
  const line = ys.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${x(data.length - 1)},${H - padB} L${x(0)},${H - padB} Z`;
  const ticks = Math.round((max - min) / step);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
      <defs>
        <linearGradient id="lcg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {Array.from({ length: ticks + 1 }).map((_, i) => {
        const v = min + (i / ticks) * (max - min);
        const yy = y(v);
        return (
          <g key={i}>
            <line x1={padL} y1={yy} x2={W - padR} y2={yy} stroke="var(--line)" strokeWidth="1" />
            <text x={padL - 8} y={yy + 3.5} textAnchor="end" fontSize="10" fill="var(--muted)">{'₱' + (v / 1000).toFixed(0) + 'K'}</text>
          </g>
        );
      })}
      <path d={area} fill="url(#lcg)" />
      <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {ys.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="3" fill="var(--card)" stroke="var(--accent)" strokeWidth="2" />)}
      {xs.map((m, i) => <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize="10.5" fill="var(--muted)">{m}</text>)}
    </svg>
  );
}

/* ---------- Donut: revenue by category ---------- */
const DONUT_COLORS = ['var(--gold)', '#16140F', '#2C5E8A', '#2E7D55', '#C1452F'];
function Donut({ data, size = 180 }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const R = size / 2, r = R * 0.6, cx = R, cy = R;
  let a0 = -Math.PI / 2;
  const arcs = data.map((d, i) => {
    const frac = d.value / total;
    const a1 = a0 + frac * Math.PI * 2;
    const large = frac > 0.5 ? 1 : 0;
    const p = (ang, rad) => [cx + Math.cos(ang) * rad, cy + Math.sin(ang) * rad];
    const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R);
    const [x2, y2] = p(a1, r), [x3, y3] = p(a0, r);
    const dStr = `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r},${r} 0 ${large} 0 ${x3},${y3} Z`;
    a0 = a1;
    return { dStr, color: DONUT_COLORS[i % DONUT_COLORS.length], ...d, pct: Math.round(frac * 100) };
  });
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} style={{ flexShrink: 0 }}>
        {arcs.map((a, i) => <path key={i} d={a.dStr} fill={a.color} stroke="var(--card)" strokeWidth="2" />)}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9, minWidth: 130 }}>
        {arcs.map((a, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <i style={{ width: 11, height: 11, borderRadius: 3, background: a.color, flexShrink: 0 }} />
            <span style={{ color: 'var(--text-2)', fontWeight: 600, flex: 1 }}>{a.name}</span>
            <span style={{ color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>{a.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- Horizontal bars: top products by revenue ---------- */
function HBars({ data, valueFmt = peso, color = 'var(--ink)', height = 260 }) {
  const max = Math.max(...data.map(d => d.value));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      {data.map((d, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '150px 1fr auto', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--text-2)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
          <div style={{ height: 14, background: 'var(--bg)', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ width: `${(d.value / max) * 100}%`, height: '100%', background: d.color || color, borderRadius: 4 }} />
          </div>
          <span style={{ fontSize: 11.5, color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', minWidth: 56, textAlign: 'right' }}>{valueFmt(d.value)}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------- FSN stat row + stacked bar ---------- */
function FSNStat({ label, count, pct, tone }) {
  const map = { ok: ['var(--ok)', 'var(--ok-bg)'], warn: ['var(--warn)', 'var(--warn-bg)'], crit: ['var(--crit)', 'var(--crit-bg)'] };
  const [c, bg] = map[tone];
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: bg, borderRadius: 'var(--r-sm)', padding: '10px 13px' }}>
      <div>
        <div style={{ fontSize: 11.5, fontWeight: 700, color: c }}>{label}</div>
        <div style={{ fontSize: 24, fontWeight: 800, color: c, lineHeight: 1.1 }}>{count}</div>
      </div>
      <span className="tag" style={{ color: c, background: 'var(--card)', borderColor: c + '55' }}>{pct}%</span>
    </div>
  );
}

Object.assign(window, { LineChart, Donut, HBars, FSNStat, peso, pesoK, DONUT_COLORS });
