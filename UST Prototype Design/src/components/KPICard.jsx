import Icon from './Icon';

/** KPI tile — .kpi vocabulary from the redesign's design system.
 *
 *  Pass `onClick` to turn the tile into a link to the page that explains it.
 *  It then renders as a real <button> rather than a <div> with a handler, so
 *  it is reachable by Tab and fires on Enter/Space without extra wiring, and
 *  it gains the `is-link` affordance (pointer, hover lift, a chevron).
 */
export default function KPICard({ label, value, sub, icon, tone, accent = false, onClick, linkLabel }) {
  const inner = (
    <>
      <div className="kpi__top">
        <span className="kpi__label">{label}</span>
        {icon && <span className="kpi__icon"><Icon name={icon} size={17} /></span>}
      </div>
      <div className={'kpi__val' + (tone ? ` is-${tone}` : '')}>{value}</div>
      {sub && <div className="kpi__sub">{sub}</div>}
      {onClick && (
        <div className="kpi__link">
          {linkLabel || 'View'} <Icon name="arrow" size={13} />
        </div>
      )}
    </>
  );

  const cls = 'card kpi' + (accent ? ' is-accent' : '') + (onClick ? ' is-link' : '');

  if (!onClick) return <div className={cls}>{inner}</div>;
  return (
    <button type="button" className={cls} onClick={onClick}
            aria-label={`${label}: ${linkLabel || 'view details'}`}>
      {inner}
    </button>
  );
}
