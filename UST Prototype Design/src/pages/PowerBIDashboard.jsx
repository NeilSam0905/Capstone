import Icon from '../components/Icon';

/**
 * The analytics dashboard is Power BI, embedded in Phase 2.
 *
 * TODO: Phase 2 — replace the body of this component with the Power BI
 * embed (report container + the embed token supplied by the backend).
 * Nothing else in the app should need to change: this is already a route.
 *
 * The coded screens beside it are kept as-is. Whether they stay alongside
 * the embed or are replaced by it is a Phase 2 decision, so no design work
 * has been deleted.
 */
export default function PowerBIDashboard() {
  return (
    <div className="stack">
      <div className="pending" style={{ padding: '70px 32px', borderWidth: 2 }}>
        <span className="pending__icon" style={{ width: 54, height: 54 }}>
          <Icon name="chart" size={24} />
        </span>
        <div className="section-title" style={{ fontSize: 18 }}>
          Dashboard — Power BI embed configured in Phase 2
        </div>
        <div className="pending__body">
          The five analytics views (Stock Status, FSN, Demand Forecast, Reorder, Batch Sales Report) are authored in
          Power BI against the same SQLite star schema this app reads. This route is the container they will be embedded
          into; it is intentionally empty until then.
        </div>
        <span className="tag tag--gold">Phase 2 — not built in this pass</span>
      </div>

      <div className="card card__pad">
        <div className="card-h"><span className="section-h">Coded screens kept for now</span></div>
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55 }}>
          The sidebar's other screens are the design team's coded versions, now reading real pipeline data through
          <span className="mono"> dataService</span>. Phase 2 decides whether the Power BI embed replaces them or sits
          alongside them — until then they stay exactly where they are.
        </div>
      </div>
    </div>
  );
}
