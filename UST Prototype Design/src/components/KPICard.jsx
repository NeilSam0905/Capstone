import Icon from './Icon';

/** KPI tile — .kpi vocabulary from the redesign's design system.
 *
 *  Pass `onClick` to turn the tile into a link to the page that explains it.
 *  It then renders as a real <button> rather than a <div> with a handler, so
 *  it is reachable by Tab and fires on Enter/Space without extra wiring, and
 *  it gains the `is-link` affordance (pointer, hover lift, a chevron).
 *
 *  `sub` is where a tile says what its number MEANS. It is always visible on
 *  purpose: this briefly carried a hover-revealed panel instead, which put
 *  the explanation behind a gesture that a touch screen does not have and a
 *  reader has no reason to try.
 *
 *  `aside` puts a second column beside the figure, behind a hairline, for a
 *  wide tile that would otherwise be one number and empty space.
 */
export default function KPICard({ label, value, sub, icon, tone, accent = false, onClick, linkLabel, aside }) {
  const main = (
    <>
      <div className={'kpi__val' + (tone ? ` is-${tone}` : '')}>{value}</div>
      {sub && <div className="kpi__sub">{sub}</div>}
      {onClick && (
        <div className="kpi__link">
          {linkLabel || 'View'} <Icon name="arrow" size={13} />
        </div>
      )}
    </>
  );
  const inner = (
    <>
      <div className="kpi__top">
        <span className="kpi__label">{label}</span>
        {icon && <span className="kpi__icon"><Icon name={icon} size={17} /></span>}
      </div>
      {aside ? (
        <div className="kpi__body">
          <div className="kpi__main">{main}</div>
          <div className="kpi__aside">{aside}</div>
        </div>
      ) : main}
    </>
  );

  const cls = 'card kpi' + (accent ? ' is-accent' : '') + (onClick ? ' is-link' : '') + (aside ? ' has-aside' : '');

  if (!onClick) return <div className={cls}>{inner}</div>;
  return (
    <button type="button" className={cls} onClick={onClick}
            aria-label={`${label}: ${linkLabel || 'view details'}`}>
      {inner}
    </button>
  );
}

/** Figures for a tile's `aside`, one per line: [[value, label], …], the value
 *  already formatted ('1,136', '₱1.53M'). Labelled figures rather than a
 *  sentence, so the tile reads at a glance. */
export function KpiStats({ stats }) {
  return stats.map(([value, label]) => (
    <div className="kpi-stat" key={label}>
      <span className="kpi-stat__val">{value}</span>
      <span className="kpi-stat__label">{label}</span>
    </div>
  ));
}
