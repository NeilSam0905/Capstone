import Icon from './Icon';

/** KPI tile — .kpi vocabulary from the redesign's design system. */
export default function KPICard({ label, value, sub, icon, tone, accent = false }) {
  return (
    <div className={'card kpi' + (accent ? ' is-accent' : '')}>
      <div className="kpi__top">
        <span className="kpi__label">{label}</span>
        {icon && <span className="kpi__icon"><Icon name={icon} size={17} /></span>}
      </div>
      <div className={'kpi__val' + (tone ? ` is-${tone}` : '')}>{value}</div>
      {sub && <div className="kpi__sub">{sub}</div>}
    </div>
  );
}
