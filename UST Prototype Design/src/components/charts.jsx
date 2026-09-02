/**
 * Charts ported from the redesign prototype
 * ("design-reference/charts.jsx"): hand-rolled SVG, no chart
 * library, all colour from the design tokens. Generalised only so the
 * value axis can be units as well as pesos.
 */

import { useRef, useState } from 'react';

import { num, DONUT_COLORS } from '../lib/format';

/** Map a pointer event into an SVG's own viewBox coordinates, and back again.
 *
 *  `getScreenCTM()` is the only correct way to do this. Deriving the position
 *  from getBoundingClientRect() assumes the viewBox spans the full element
 *  width, which is false whenever preserveAspectRatio letterboxes the content
 *  — with `width="100%"` and a fixed `height`, any container wider than the
 *  viewBox draws the chart centred with empty margins either side, and a
 *  rect-based cursor mapping drifts further the wider the container gets.
 *
 *  Returns null when the SVG is not laid out yet (getScreenCTM() is null for a
 *  detached or display:none element).
 */
function svgPoint(svg, clientX, clientY) {
  const ctm = svg?.getScreenCTM?.();
  if (!ctm) return null;
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  return pt.matrixTransform(ctm.inverse());
}

/** Inverse of svgPoint: a viewBox x, as pixels from the SVG's left edge. */
function svgXToLocalPx(svg, vbX) {
  const ctm = svg?.getScreenCTM?.();
  if (!ctm) return null;
  const pt = svg.createSVGPoint();
  pt.x = vbX;
  pt.y = 0;
  return pt.matrixTransform(ctm).x - svg.getBoundingClientRect().left;
}

/** Shared hover readout. Positioned in the chart's own coordinate space by the
 *  caller, so it works the same in an SVG viewBox as in a flow layout. */
function ChartTip({ label, value, sub }) {
  return (
    <div className="charttip">
      <div className="charttip__label">{label}</div>
      <div className="charttip__value">{value}</div>
      {sub && <div className="charttip__sub">{sub}</div>}
    </div>
  );
}

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

  // Hover is tracked as an index, not a pixel: the pointer is mapped back
  // through the same x() the points were drawn with, so the highlighted point
  // is always the nearest one rather than whatever happens to be under the
  // cursor in a rescaled viewBox.
  // { i, left } — the snapped point index, plus where that point actually is
  // in pixels, so the tooltip sits exactly over the crosshair no matter how
  // the viewBox has been scaled or letterboxed.
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);

  function onMove(e) {
    const local = svgPoint(svgRef.current, e.clientX, e.clientY);
    if (!local) return;
    const t = (local.x - padL) / (W - padL - padR);
    const i = Math.round(t * (data.length - 1));
    if (i < 0 || i >= data.length) { setHover(null); return; }
    setHover({ i, left: svgXToLocalPx(svgRef.current, x(i)) });
  }

  return (
    <div style={{ position: 'relative' }}>
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMidYMid meet"
         style={{ display: 'block' }}
         onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
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
      {hover && (
        <g pointerEvents="none">
          <line x1={x(hover.i)} y1={padT} x2={x(hover.i)} y2={H - padB}
                stroke="var(--accent)" strokeWidth="1" strokeDasharray="3 3" />
          <circle cx={x(hover.i)} cy={y(ys[hover.i])} r="5.5"
                  fill="var(--accent)" stroke="var(--card)" strokeWidth="2" />
        </g>
      )}
    </svg>
    {hover && hover.left != null && (
      <div className="charttip-wrap" style={{ left: `${hover.left}px` }}>
        <ChartTip label={xs[hover.i]} value={tickFmt(ys[hover.i])} sub="units" />
      </div>
    )}
    </div>
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

    // A slice covering the whole circle has IDENTICAL start and end points,
    // and the SVG spec says an elliptical arc whose endpoints coincide is
    // "equivalent to omitting the arc segment entirely" - so a lone 100%
    // category rendered a blank ring with only the legend beside it. Draw it
    // as two half-circles instead, which has no degenerate case.
    const dStr = frac >= 0.9999
      ? [
          `M${cx},${cy - R}`,
          `A${R},${R} 0 1 1 ${cx},${cy + R}`,
          `A${R},${R} 0 1 1 ${cx},${cy - R}`,
          `M${cx},${cy - r}`,
          `A${r},${r} 0 1 0 ${cx},${cy + r}`,
          `A${r},${r} 0 1 0 ${cx},${cy - r}`,
          'Z',
        ].join(' ')
      : `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r},${r} 0 ${large} 0 ${x3},${y3} Z`;

    return {
      dStr,
      full: frac >= 0.9999,
      color: DONUT_COLORS[i % DONUT_COLORS.length],
      ...d,
      // Round to 1dp below 1% so a tiny-but-real category is not shown as 0%.
      pct: frac >= 0.01 ? Math.round(frac * 100) : Math.round(frac * 1000) / 10,
    };
  });
  // One hover index drives both the ring and the legend, so pointing at either
  // highlights the other - the legend is the label for the slice, and reading
  // a donut means pairing them.
  const [hover, setHover] = useState(null);
  const active = hover != null ? arcs[hover] : null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
      <div style={{ position: 'relative', flexShrink: 0 }}>
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
          {arcs.map((a, i) => (
            <path
              key={i}
              d={a.dStr}
              fill={a.color}
              fillRule={a.full ? 'evenodd' : undefined}
              stroke="var(--card)"
              strokeWidth={a.full ? 0 : 2}
              opacity={hover == null || hover === i ? 1 : 0.35}
              style={{ cursor: 'pointer', transition: 'opacity .12s' }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
        </svg>
        {/* Centre readout rather than a floating tip: the donut has a hole,
            and the hole is exactly where the eye already is. */}
        <div className="donut__centre">
          <div className="donut__centre-val">{active ? `${active.pct}%` : num(total)}</div>
          <div className="donut__centre-lbl">{active ? active.name : 'total units'}</div>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9, minWidth: 130 }}>
        {arcs.map((a, i) => (
          <div
            key={i}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
              cursor: 'pointer', borderRadius: 4,
              background: hover === i ? 'var(--bg)' : 'transparent',
              opacity: hover == null || hover === i ? 1 : 0.5,
              padding: '2px 4px', margin: '-2px -4px',
              transition: 'opacity .12s, background .12s',
            }}
          >
            <i style={{ width: 11, height: 11, borderRadius: 3, background: a.color, flexShrink: 0 }} />
            <span style={{ color: 'var(--text-2)', fontWeight: 600, flex: 1 }}>{a.name}</span>
            <span style={{ color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>
              {hover === i ? num(a.value) : `${a.pct}%`}
            </span>
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
  const [hover, setHover] = useState(null);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      {data.map((d, i) => (
        <div
          key={i}
          onMouseEnter={() => setHover(i)}
          onMouseLeave={() => setHover(null)}
          style={{
            display: 'grid', gridTemplateColumns: '170px 1fr auto', alignItems: 'center', gap: 12,
            background: hover === i ? 'var(--bg)' : 'transparent',
            borderRadius: 6, padding: '3px 5px', margin: '-3px -5px',
            transition: 'background .12s',
          }}
        >
          <span title={d.name} style={{ fontSize: 12, color: hover === i ? 'var(--text)' : 'var(--text-2)', fontWeight: hover === i ? 700 : 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
          <div style={{ height: 14, background: 'var(--bg)', borderRadius: 4, overflow: 'hidden', outline: hover === i ? '1px solid var(--line)' : 'none' }}>
            <div style={{
              width: `${(d.value / max) * 100}%`, height: '100%',
              background: d.color || color, borderRadius: 4,
              filter: hover === i ? 'none' : hover == null ? 'none' : 'saturate(.4) opacity(.55)',
              transition: 'filter .12s',
            }} />
          </div>
          <span style={{ fontSize: 11.5, color: hover === i ? 'var(--text)' : 'var(--muted)', fontWeight: hover === i ? 700 : 400, fontVariantNumeric: 'tabular-nums', minWidth: 56, textAlign: 'right' }}>
            {valueFmt(d.value)}{hover === i && max ? ` · ${Math.round((d.value / max) * 100)}%` : ''}
          </span>
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
/** One FSN band. Pass `onClick` to make it open that band's item list.
 *
 *  The call-to-action line is always in the DOM and only fades in on hover, so
 *  the row never changes height - a CTA that appears on hover and pushes the
 *  rows below it down makes the thing you were aiming at move away from the
 *  cursor. */
export function FSNStat({ label, count, pct, tone, onClick }) {
  const map = { ok: ['var(--ok)', 'var(--ok-bg)'], warn: ['var(--warn)', 'var(--warn-bg)'], crit: ['var(--crit)', 'var(--crit-bg)'] };
  const [c, bg] = map[tone];

  const body = (
    <>
      <span className="fsnstat__top">
        <span>
          <span className="fsnstat__label" style={{ color: c }}>{label}</span>
          <span className="fsnstat__count" style={{ color: c }}>{count}</span>
        </span>
        <span className="tag" style={{ color: c, background: 'var(--card)', borderColor: c + '55' }}>{pct}%</span>
      </span>
      {onClick && (
        <span className="fsnstat__cta" style={{ color: c }}>
          Click to see the {label} items &rarr;
        </span>
      )}
    </>
  );

  if (!onClick) {
    return <div className="fsnstat" style={{ background: bg }}>{body}</div>;
  }
  return (
    <button type="button" className="fsnstat is-clickable" style={{ background: bg }}
            onClick={onClick} title={`Click to see the ${label} items`}>
      {body}
    </button>
  );
}

/* ---------- Forecast chart with confidence band ---------- */
export function ForecastChart({ data, height = 260 }) {
  if (!data || data.length < 2) {
    return <div className="empty">Not enough forecast points to plot.</div>;
  }
  const W = 720, H = height, padL = 58, padR = 16, padT = 16, padB = 28;
  const allVals = data.flatMap(d => [d.yhat, d.yhat_lower, d.yhat_upper].filter(v => v != null));
  const rawMax = Math.max(...allVals), rawMin = Math.min(0, Math.min(...allVals));
  const range = rawMax - rawMin || 1;
  const tickCount = 4;
  const rawStep = range / tickCount;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const min = Math.max(0, Math.floor(rawMin / step) * step);
  const max = Math.ceil(rawMax / step) * step || 1;

  const x = i => padL + (i / (data.length - 1)) * (W - padL - padR);
  const y = v => padT + (1 - (v - min) / (max - min || 1)) * (H - padT - padB);

  // Confidence band (polygon from yhat_upper forward, yhat_lower backward)
  const bandPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.yhat_upper ?? d.yhat).toFixed(1)}`)
    .join(' ')
    + ' ' + [...data].reverse().map((d, i) => `L${x(data.length - 1 - i).toFixed(1)},${y(d.yhat_lower ?? d.yhat).toFixed(1)}`)
    .join(' ') + ' Z';

  // Main forecast line
  const forecastLine = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.yhat).toFixed(1)}`).join(' ');

  const ticks = Math.max(1, Math.round((max - min) / step));
  const labelEvery = Math.ceil(data.length / 10);

  // Format date labels: 'Aug 21' style
  const fmtDate = d => {
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const parts = d.split('-');
    if (parts.length >= 3) return `${months[+parts[1]-1]} ${+parts[2]}`;
    return d;
  };

  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);

  function onMove(e) {
    const local = svgPoint(svgRef.current, e.clientX, e.clientY);
    if (!local) return;
    const t = (local.x - padL) / (W - padL - padR);
    const i = Math.round(t * (data.length - 1));
    if (i < 0 || i >= data.length) { setHover(null); return; }
    setHover({ i, left: svgXToLocalPx(svgRef.current, x(i)) });
  }

  const hd = hover ? data[hover.i] : null;

  return (
    <div style={{ position: 'relative' }}>
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMidYMid meet"
         style={{ display: 'block' }}
         onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <defs>
        <linearGradient id="fcg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.15" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.03" />
        </linearGradient>
      </defs>
      {Array.from({ length: ticks + 1 }).map((_, i) => {
        const v = min + (i / ticks) * (max - min);
        const yy = y(v);
        return (
          <g key={i}>
            <line x1={padL} y1={yy} x2={W - padR} y2={yy} stroke="var(--line)" strokeWidth="1" />
            <text x={padL - 8} y={yy + 3.5} textAnchor="end" fontSize="10" fill="var(--muted)">{num(v)}</text>
          </g>
        );
      })}
      {/* Confidence band */}
      <path d={bandPath} fill="url(#fcg)" />
      {/* Forecast line */}
      <path d={forecastLine} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {/* Data points */}
      {data.map((d, i) => (
        <circle key={i} cx={x(i)} cy={y(d.yhat)} r="3" fill="var(--card)" stroke="var(--accent)" strokeWidth="2" />
      ))}
      {/* X-axis labels */}
      {data.map((d, i) => (i % labelEvery === 0
        ? <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize="10.5" fill="var(--muted)">{fmtDate(d.forecast_date)}</text>
        : null))}
      {hd && (
        <g pointerEvents="none">
          <line x1={x(hover.i)} y1={padT} x2={x(hover.i)} y2={H - padB}
                stroke="var(--accent)" strokeWidth="1" strokeDasharray="3 3" />
          {hd.yhat_upper != null && hd.yhat_lower != null && (
            <line x1={x(hover.i)} y1={y(hd.yhat_upper)} x2={x(hover.i)} y2={y(hd.yhat_lower)}
                  stroke="var(--accent)" strokeWidth="6" strokeOpacity=".18" strokeLinecap="round" />
          )}
          <circle cx={x(hover.i)} cy={y(hd.yhat)} r="5.5"
                  fill="var(--accent)" stroke="var(--card)" strokeWidth="2" />
        </g>
      )}
    </svg>
    {hd && hover.left != null && (
      <div className="charttip-wrap" style={{ left: `${hover.left}px` }}>
        <ChartTip
          label={fmtDate(hd.forecast_date)}
          value={num(Math.round(hd.yhat))}
          sub={hd.yhat_lower != null && hd.yhat_upper != null
            ? `${num(Math.round(hd.yhat_lower))} – ${num(Math.round(hd.yhat_upper))}`
            : 'units'}
        />
      </div>
    )}
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
