/**
 * Charts ported from the redesign prototype
 * ("UST Prototype Design/app/charts.jsx"): hand-rolled SVG, no chart
 * library, all colour from the design tokens. Generalised only so the
 * value axis can be units as well as pesos.
 */

import { num, DONUT_COLORS } from '../lib/format';

/* ---------- Line chart ---------- */
export function LineChart({ data, height = 210, xKey = 'label', yKey = 'value', tickFmt = num }) {
  if (!data || data.length < 2) {
    return <div className="empty">Not enough data points to plot.</div>;
  }
  const W = 720, H = height, padL = 58, padR = 16, padT = 16, padB = 28;
  const xs = data.map(d => d[xKey]);
  const ys = data.map(d => d[yKey]);
  const rawMax = Math.max(...ys), rawMin = Math.min(...ys);
  const tickCount = 4;
  const range = rawMax - rawMin || 1;
  const rawStep = range / tickCount;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const min = Math.max(0, Math.floor(rawMin / step) * step - step);
  const max = Math.ceil(rawMax / step) * step || 1;
  const x = i => padL + (i / (data.length - 1)) * (W - padL - padR);
  const y = v => padT + (1 - (v - min) / (max - min || 1)) * (H - padT - padB);
  const line = ys.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${x(data.length - 1)},${H - padB} L${x(0)},${H - padB} Z`;
  const ticks = Math.max(1, Math.round((max - min) / step));
  // keep the x-axis readable when there are many periods
  const labelEvery = Math.ceil(data.length / 14);

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
            <text x={padL - 8} y={yy + 3.5} textAnchor="end" fontSize="10" fill="var(--muted)">{tickFmt(v)}</text>
          </g>
        );
      })}
      <path d={area} fill="url(#lcg)" />
      <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {ys.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="3" fill="var(--card)" stroke="var(--accent)" strokeWidth="2" />)}
      {xs.map((m, i) => (i % labelEvery === 0
        ? <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize="10.5" fill="var(--muted)">{m}</text>
        : null))}
    </svg>
  );
}

/* ---------- Donut ---------- */
export function Donut({ data, size = 180 }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  if (!total) return <div className="empty">No data.</div>;
  const R = size / 2, r = R * 0.6, cx = R, cy = R;
  const p = (ang, rad) => [cx + Math.cos(ang) * rad, cy + Math.sin(ang) * rad];
  // start angle of each slice, accumulated without mutating across the map
  const starts = data.reduce(
    (acc, d) => [...acc, acc[acc.length - 1] + (d.value / total) * Math.PI * 2],
    [-Math.PI / 2]
  );
  const arcs = data.map((d, i) => {
    const frac = d.value / total;
    const a0 = starts[i], a1 = starts[i + 1];
    const large = frac > 0.5 ? 1 : 0;
    const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R);
    const [x2, y2] = p(a1, r), [x3, y3] = p(a0, r);
    const dStr = `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r},${r} 0 ${large} 0 ${x3},${y3} Z`;
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

/* ---------- Horizontal bars ---------- */
export function HBars({ data, valueFmt = num, color = 'var(--ink)' }) {
  if (!data.length) return <div className="empty">No data.</div>;
  const max = Math.max(...data.map(d => d.value)) || 1;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      {data.map((d, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '170px 1fr auto', alignItems: 'center', gap: 12 }}>
          <span title={d.name} style={{ fontSize: 12, color: 'var(--text-2)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
          <div style={{ height: 14, background: 'var(--bg)', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ width: `${(d.value / max) * 100}%`, height: '100%', background: d.color || color, borderRadius: 4 }} />
          </div>
          <span style={{ fontSize: 11.5, color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', minWidth: 56, textAlign: 'right' }}>{valueFmt(d.value)}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------- Grouped vertical bars (FSN threshold sensitivity) ---------- */
export function GroupedBars({ groups, series, height = 190 }) {
  const W = 460, H = height, padL = 34, padR = 8, padT = 12, padB = 34;
  const max = Math.max(...groups.flatMap(g => series.map(s => g[s.key]))) || 1;
  const gw = (W - padL - padR) / groups.length;
  const bw = Math.min(26, (gw - 14) / series.length);
  const y = v => padT + (1 - v / max) * (H - padT - padB);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
      {[0, 0.5, 1].map((f, i) => (
        <g key={i}>
          <line x1={padL} y1={y(max * f)} x2={W - padR} y2={y(max * f)} stroke="var(--line)" />
          <text x={padL - 6} y={y(max * f) + 3.5} textAnchor="end" fontSize="9.5" fill="var(--muted)">{Math.round(max * f)}</text>
        </g>
      ))}
      {groups.map((g, gi) => {
        const x0 = padL + gi * gw + (gw - bw * series.length) / 2;
        return (
          <g key={gi}>
            {series.map((s, si) => (
              <rect key={s.key} x={x0 + si * bw} y={y(g[s.key])} width={bw - 3}
                    height={H - padB - y(g[s.key])} fill={s.color} rx="2" />
            ))}
            <text x={padL + gi * gw + gw / 2} y={H - 12} textAnchor="middle" fontSize="10" fill="var(--muted)">{g.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------- FSN stat row ---------- */
export function FSNStat({ label, count, pct, tone }) {
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

/* ---------- Stacked proportion bar (FSN split) ---------- */
export function StackBar({ parts }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  return (
    <div style={{ display: 'flex', height: 14, borderRadius: 100, overflow: 'hidden', marginTop: 4 }}>
      {parts.map((p, i) => (
        <div key={i} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
      ))}
    </div>
  );
}
