/**
 * Charts ported from the redesign prototype
 * ("design-reference/charts.jsx"): hand-rolled SVG, no chart
 * library, all colour from the design tokens. Generalised only so the
 * value axis can be units as well as pesos.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

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
  // Hover is tracked as an index, not a pixel: the pointer is mapped back
  // through the same x() the points were drawn with, so the highlighted point
  // is always the nearest one rather than whatever happens to be under the
  // cursor in a rescaled viewBox.
  // { i, left } — the snapped point index, plus where that point actually is
  // in pixels, so the tooltip sits exactly over the crosshair no matter how
  // the viewBox has been scaled or letterboxed.
  // Declared above the "not enough points" guard below: hooks must run in the
  // same order on every render, and a series can drop under two points when a
  // filter narrows.
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);

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
  // One hover index drives both the ring and the legend, so pointing at either
  // highlights the other - the legend is the label for the slice, and reading
  // a donut means pairing them.
  // Declared above the "no data" guard below: hooks must run in the same order
  // on every render, and the category set can empty out when a filter narrows.
  const [hover, setHover] = useState(null);

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
      {/* Capped and scrolled past the handful of categories that fit beside
          the ring. A legend free to grow set the height of the whole grid
          row, which left the chart next to it padded out with empty card. */}
      <div className="donut__legend">
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
              padding: '2px 4px',
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
  // Declared above the "no data" guard below: hooks must run in the same order
  // on every render, and an empty result set is an ordinary state here.
  const [hover, setHover] = useState(null);

  if (!data.length) return <div className="empty">No data.</div>;
  const max = Math.max(...data.map(d => d.value)) || 1;
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
          {/* `sub` is an optional second line under the label — the supplier,
              on the pages that rank items across all of them. Without it a
              bar chart of item names gives no way to tell whose stock it is. */}
          <span style={{ minWidth: 0 }}>
            <span title={d.name} style={{ display: 'block', fontSize: 12, color: hover === i ? 'var(--text)' : 'var(--text-2)', fontWeight: hover === i ? 700 : 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
            {d.sub && (
              <span title={d.sub} style={{ display: 'block', fontSize: 10.5, color: 'var(--muted)', fontWeight: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>{d.sub}</span>
            )}
          </span>
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
  // Declared above the "not enough points" guard below: hooks must run in the
  // same order on every render, and a horizon can come back with fewer than two
  // points.
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);

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

/* ---------- Scrollable timeline: monthly history, then the daily forecast ---------- */
const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ymIndex = ym => +ym.slice(0, 4) * 12 + (+ym.slice(5, 7) - 1);
const fmtYm = k => `${MONTH_ABBR[k % 12]} ${Math.floor(k / 12)}`;
const fmtIsoDay = s => `${MONTH_ABBR[+s.slice(5, 7) - 1]} ${+s.slice(8, 10)}, ${s.slice(0, 4)}`;

/** A "nice" axis ceiling and step for a maximum, four-ish ticks. */
function niceAxis(v) {
  const rawStep = (v || 1) / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = Math.max(1, (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag);
  return { max: Math.ceil((v || 1) / step) * step, step };
}

/** The months before the forecast as a light line, one point per month, then
 *  the forecast month in full, day by day - on one horizontally scrolling
 *  timeline that opens on the forecast.
 *
 *  The two parts share the value axis, so the history is plotted as each
 *  month's AVERAGE UNITS PER DAY (units / tally_days) rather than its total:
 *  a month's total is ~30x a day's forecast, and on one axis either the
 *  history would tower over the forecast or the forecast would lie on the
 *  floor. The month total is still in the tooltip.
 *
 *  `history` is [{ month, units, tally_days }] - a month missing from it was
 *  never tallied and breaks the line rather than dipping to zero.
 *  `forecast` is the Result_Forecast rows [{ forecast_date, yhat, yhat_lower,
 *  yhat_upper }]. */
export function ScrollForecastChart({ history, forecast, height = 290, monthW = 56, dayW = 22 }) {
  const scrollRef = useRef(null);
  const dragRef = useRef(null);
  const frameRef = useRef(0);
  const [hover, setHover] = useState(null);
  const [atStart, setAtStart] = useState(false);
  const [atEnd, setAtEnd] = useState(true);
  const [dragging, setDragging] = useState(false);

  // One slot per calendar month from the first to the last with history, so
  // an untallied month still takes its place on the timeline.
  const model = useMemo(() => {
    const byMonth = new Map((history ?? []).map(r => [ymIndex(r.month), r]));
    const keys = [...byMonth.keys()];
    const months = [];
    if (keys.length) {
      for (let k = Math.min(...keys); k <= Math.max(...keys); k++) {
        const r = byMonth.get(k);
        months.push({
          k,
          units: r ? r.units : null,
          tallyDays: r ? r.tally_days : 0,
          perDay: r && r.tally_days ? r.units / r.tally_days : null,
        });
      }
    }
    const days = (forecast ?? []).map(r => ({
      date: r.forecast_date,
      yhat: r.yhat,
      lo: r.yhat_lower ?? r.yhat,
      hi: r.yhat_upper ?? r.yhat,
    }));
    const total = days.reduce((s, d) => s + (d.yhat ?? 0), 0);
    const peak = Math.max(0, ...months.map(m => m.perDay ?? 0), ...days.map(d => d.hi ?? 0));
    return { months, days, total, peak };
  }, [history, forecast]);

  const { months, days, total, peak } = model;
  const padX = 12, padR = 20, padT = 34, padB = 44, AX = 50;
  const H = height;
  const X0 = padX + months.length * monthW;          // where the forecast begins
  const W = X0 + days.length * dayW + padR;
  const mx = i => padX + i * monthW + monthW / 2;
  const dx = j => X0 + j * dayW + dayW / 2;
  const { max: yMax, step } = niceAxis(peak);
  const plotBottom = H - padB;
  const y = v => padT + (1 - v / yMax) * (plotBottom - padT);
  const ticks = [];
  for (let v = 0; v <= yMax + 1e-9; v += step) ticks.push(v);

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setAtStart(el.scrollLeft <= 2);
    setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 2);
  }, []);

  // Open on the forecast: scrolled all the way to the right.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollLeft = el.scrollWidth;
    measure();
  }, [measure, W]);

  useEffect(() => {
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('resize', measure);
      cancelAnimationFrame(frameRef.current);
    };
  }, [measure]);

  if (!days.length && months.length < 2) {
    return <div className="empty">Not enough data to plot.</div>;
  }

  function onScroll() {
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(measure);
    if (hover) setHover(null);
  }

  function toForecast() {
    const el = scrollRef.current;
    el?.scrollTo({ left: el.scrollWidth, behavior: 'smooth' });
  }

  function onKeyDown(e) {
    const el = scrollRef.current;
    if (!el) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); el.scrollBy({ left: -3 * monthW, behavior: 'smooth' }); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); el.scrollBy({ left: 3 * monthW, behavior: 'smooth' }); }
    else if (e.key === 'Home') { e.preventDefault(); el.scrollTo({ left: 0, behavior: 'smooth' }); }
    else if (e.key === 'End') { e.preventDefault(); toForecast(); }
  }

  // Click-and-drag panning for a mouse: without it, a mouse user's only way
  // sideways is the thin scrollbar or Shift+wheel. Touch and trackpads already
  // scroll natively, so only the mouse is captured.
  function onPointerDown(e) {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    dragRef.current = { x: e.clientX, left: scrollRef.current.scrollLeft };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e) {
    const drag = dragRef.current;
    if (drag) {
      const delta = e.clientX - drag.x;
      if (Math.abs(delta) > 3 && !dragging) setDragging(true);
      scrollRef.current.scrollLeft = drag.left - delta;
      return;
    }
    const el = scrollRef.current;
    const svg = el?.querySelector('svg');
    if (!svg) return;
    const lx = e.clientX - svg.getBoundingClientRect().left;
    let target = null;
    if (lx >= X0 && days.length) {
      const j = Math.min(days.length - 1, Math.max(0, Math.floor((lx - X0) / dayW)));
      target = { kind: 'day', i: j, cx: dx(j) };
    } else if (lx < X0 && months.length) {
      const i = Math.min(months.length - 1, Math.max(0, Math.floor((lx - padX) / monthW)));
      if (months[i].perDay != null) target = { kind: 'month', i, cx: mx(i) };
    }
    if (!target) { setHover(null); return; }
    if (hover?.kind === target.kind && hover?.i === target.i) return;
    // Keep the tip inside the visible window: centred normally, pinned to
    // its left or right edge when the point is near either side.
    const rel = target.cx - el.scrollLeft;
    target.align = rel < 90 ? 'left' : rel > el.clientWidth - 90 ? 'right' : 'center';
    setHover(target);
  }
  function endDrag() {
    dragRef.current = null;
    if (dragging) setDragging(false);
  }

  // History line, broken wherever a month was never tallied.
  const segs = [];
  let cur = [];
  months.forEach((m, i) => {
    if (m.perDay == null) { if (cur.length) segs.push(cur); cur = []; return; }
    cur.push(`${cur.length ? 'L' : 'M'}${mx(i).toFixed(1)},${y(m.perDay).toFixed(1)}`);
  });
  if (cur.length) segs.push(cur);
  const histPath = segs.map(sg => sg.join(' ')).join(' ');

  const band = days.length
    ? days.map((d, j) => `${j === 0 ? 'M' : 'L'}${dx(j).toFixed(1)},${y(d.hi).toFixed(1)}`).join(' ')
      + ' ' + [...days].reverse().map((d, k) => `L${dx(days.length - 1 - k).toFixed(1)},${y(d.lo).toFixed(1)}`).join(' ')
      + ' Z'
    : '';
  const fcLine = days.map((d, j) => `${j === 0 ? 'M' : 'L'}${dx(j).toFixed(1)},${y(d.yhat).toFixed(1)}`).join(' ');
  const lastMonth = months.length ? months[months.length - 1] : null;

  let tip = null;
  if (hover?.kind === 'month') {
    const m = months[hover.i];
    tip = { label: fmtYm(m.k), value: `${num(m.units)} units`,
            sub: `about ${num(m.perDay)} a day · ${m.tallyDays} tally day${m.tallyDays === 1 ? '' : 's'}` };
  } else if (hover?.kind === 'day') {
    const d = days[hover.i];
    tip = { label: `${fmtIsoDay(d.date)} · forecast`, value: num(Math.round(d.yhat)),
            sub: `likely ${num(Math.round(d.lo))} – ${num(Math.round(d.hi))} units` };
  }

  // The horizon need not be a calendar month (the category model starts the
  // day after its last sale), so name its actual span when it crosses one.
  let fcLabel = '';
  if (days.length) {
    const first = days[0].date, last = days[days.length - 1].date;
    fcLabel = first.slice(0, 7) === last.slice(0, 7)
      ? fmtYm(ymIndex(first.slice(0, 7)))
      : `${MONTH_ABBR[+first.slice(5, 7) - 1]} ${+first.slice(8, 10)} – `
        + `${MONTH_ABBR[+last.slice(5, 7) - 1]} ${+last.slice(8, 10)}, ${last.slice(0, 4)}`;
  }

  return (
    <div className="sfc">
      <div className="sfc__bar">
        <span className="hint">
          {atEnd ? '← Scroll or drag left to see past months' : 'Past months · average units sold per day'}
        </span>
        {!atEnd && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={toForecast}>
            Back to forecast →
          </button>
        )}
      </div>

      <div className="sfc__body">
        {/* Pinned value axis: stays put while the timeline scrolls under it */}
        <svg width={AX} height={H} className="sfc__axis" aria-hidden="true">
          <text x={AX - 8} y={padT - 14} textAnchor="end" fontSize="9.5" fill="var(--muted)">units/day</text>
          {ticks.map(v => (
            <text key={v} x={AX - 8} y={y(v) + 3.5} textAnchor="end" fontSize="10" fill="var(--muted)">{num(v)}</text>
          ))}
        </svg>

        <div className="sfc__viewport">
          <div
            ref={scrollRef}
            className={`sfc__scroll${dragging ? ' is-dragging' : ''}`}
            tabIndex={0}
            role="region"
            aria-label="Demand timeline. Use the left and right arrow keys to move through past months."
            onScroll={onScroll}
            onKeyDown={onKeyDown}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onMouseLeave={() => setHover(null)}
          >
            <div style={{ position: 'relative', width: W }}>
              <svg width={W} height={H} style={{ display: 'block' }}>
                {/* Forecast region */}
                {days.length > 0 && (
                  <g>
                    <rect x={X0} y={4} width={W - X0} height={plotBottom - 4}
                          fill="var(--accent)" fillOpacity="0.09" rx="6" />
                    <line x1={X0} y1={4} x2={X0} y2={plotBottom}
                          stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="4 4" />
                    <text x={X0 + 10} y={20} fontSize="11" fontWeight="700" fill="var(--accent-deep)">
                      Forecast · next {days.length} days · {num(Math.round(total))} units
                    </text>
                    {X0 > 150 && (
                      <text x={X0 - 10} y={20} textAnchor="end" fontSize="11" fontWeight="600" fill="var(--muted)">
                        Past months
                      </text>
                    )}
                  </g>
                )}

                {/* Horizontal gridlines */}
                {ticks.map(v => (
                  <line key={v} x1={0} y1={y(v)} x2={W} y2={y(v)} stroke="var(--line)" strokeWidth="1" />
                ))}

                {/* Untallied months: shaded, so the break in the line reads
                    as missing data rather than as a drop in sales */}
                {months.map((m, i) => (m.perDay == null ? (
                  <g key={m.k}>
                    <rect x={mx(i) - monthW / 2} y={padT} width={monthW} height={plotBottom - padT}
                          fill="var(--muted)" fillOpacity="0.07" />
                    <text x={mx(i)} y={plotBottom - 8} textAnchor="middle" fontSize="9"
                          fontStyle="italic" fill="var(--muted)">no tally</text>
                  </g>
                ) : null))}

                {/* Hover guide */}
                {hover && (
                  <line x1={hover.cx} y1={padT} x2={hover.cx} y2={plotBottom}
                        stroke={hover.kind === 'day' ? 'var(--accent)' : 'var(--text-2)'}
                        strokeWidth="1" strokeDasharray="3 3" />
                )}

                {/* Past months: the light preview line */}
                <path d={histPath} fill="none" stroke="var(--text-2)" strokeOpacity="0.7" strokeWidth="2"
                      strokeLinejoin="round" strokeLinecap="round" />
                {months.map((m, i) => (m.perDay == null ? null : (
                  <circle key={m.k} cx={mx(i)} cy={y(m.perDay)}
                          r={hover?.kind === 'month' && hover.i === i ? 5 : 3}
                          fill={hover?.kind === 'month' && hover.i === i ? 'var(--text-2)' : 'var(--card)'}
                          stroke="var(--text-2)" strokeWidth="1.8" />
                )))}

                {/* Hand-off from the last month into the first forecast day */}
                {lastMonth?.perDay != null && days.length > 0 && (
                  <line x1={mx(months.length - 1)} y1={y(lastMonth.perDay)} x2={dx(0)} y2={y(days[0].yhat)}
                        stroke="var(--accent)" strokeWidth="1.8" strokeDasharray="4 4" strokeOpacity="0.8" />
                )}

                {/* The forecast month, in detail */}
                {days.length > 0 && (
                  <g>
                    <path d={band} fill="var(--accent)" fillOpacity="0.18" />
                    <path d={fcLine} fill="none" stroke="var(--accent)" strokeWidth="2.5"
                          strokeLinejoin="round" strokeLinecap="round" />
                    {days.map((d, j) => (
                      <circle key={d.date} cx={dx(j)} cy={y(d.yhat)}
                              r={hover?.kind === 'day' && hover.i === j ? 5 : 3}
                              fill={hover?.kind === 'day' && hover.i === j ? 'var(--accent)' : 'var(--card)'}
                              stroke="var(--accent)" strokeWidth="2" />
                    ))}
                  </g>
                )}

                {/* X axis - history: month, with the year under each January,
                    the first month and the last (the one on screen at open);
                    forecast: day numbers, then the month */}
                {months.map((m, i) => (
                  <g key={m.k}>
                    <text x={mx(i)} y={plotBottom + 14} textAnchor="middle" fontSize="10" fill="var(--muted)">
                      {MONTH_ABBR[m.k % 12]}
                    </text>
                    {(i === 0 || i === months.length - 1 || m.k % 12 === 0) && (
                      <text x={mx(i)} y={plotBottom + 32} textAnchor="middle" fontSize="11" fontWeight="700"
                            fill="var(--text-2)">{Math.floor(m.k / 12)}</text>
                    )}
                  </g>
                ))}
                {days.map((d, j) => {
                  const dom = +d.date.slice(8, 10);
                  // The first day and each 1st carry their month, so a horizon
                  // that crosses a month boundary reads "Jul 9 … Aug 1".
                  const withMonth = j === 0 || dom === 1;
                  return (withMonth || [8, 15, 22, 29].includes(dom)) ? (
                    <text key={d.date} x={dx(j)} y={plotBottom + 14} textAnchor="middle" fontSize="10"
                          fontWeight={withMonth ? 700 : 400} fill="var(--accent-deep)">
                      {withMonth ? `${MONTH_ABBR[+d.date.slice(5, 7) - 1]} ${dom}` : dom}
                    </text>
                  ) : null;
                })}
                {days.length > 0 && (
                  <text x={X0 + 6} y={plotBottom + 32} fontSize="11" fontWeight="700" fill="var(--accent-deep)">
                    {fcLabel} · day by day
                  </text>
                )}
              </svg>

              {tip && (
                <div className="charttip-wrap"
                     style={{
                       left: `${hover.cx}px`,
                       transform: hover.align === 'left' ? 'translateX(-12px)'
                         : hover.align === 'right' ? 'translateX(calc(-100% + 12px))' : undefined,
                     }}>
                  <ChartTip label={tip.label} value={tip.value} sub={tip.sub} />
                </div>
              )}
            </div>
          </div>
          {/* Edge fades: more timeline lies that way */}
          {!atStart && <div className="sfc__fade sfc__fade--l" />}
          {!atEnd && <div className="sfc__fade sfc__fade--r" />}
        </div>
      </div>
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
