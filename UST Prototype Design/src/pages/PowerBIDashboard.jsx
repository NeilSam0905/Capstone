import Icon from '../components/Icon';

// Set in .env as VITE_POWERBI_EMBED_URL — see README "Set up the Power BI dashboard".
const EMBED_URL = import.meta.env.VITE_POWERBI_EMBED_URL;

/**
 * The analytics dashboard is Power BI, embedded here via a responsive
 * iframe. Falls back to a placeholder card when the embed URL isn't set,
 * so the app never renders a broken iframe.
 *
 * The coded screens beside it (Overview, Classification, Forecast,
 * Reorder, Batch Sales Report) are kept as-is. Whether they stay
 * alongside the embed or are eventually replaced by it is a separate
 * decision — nothing here deletes that design work.
 */
export default function PowerBIDashboard() {
  if (!EMBED_URL) {
    return (
      <div className="stack">
        <div className="pending" style={{ padding: '70px 32px', borderWidth: 2 }}>
          <span className="pending__icon" style={{ width: 54, height: 54 }}>
            <Icon name="chart" size={24} />
          </span>
          <div className="section-title" style={{ fontSize: 18 }}>
            Power BI report not configured
          </div>
          <div className="pending__body">
            Set <span className="mono">VITE_POWERBI_EMBED_URL</span> in your <span className="mono">.env</span> file
            to embed the published report here.
          </div>
          <span className="tag tag--gold">Awaiting embed URL</span>
        </div>

        <div className="card card__pad">
          <div className="card-h"><span className="section-h">Coded screens kept for now</span></div>
          <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55 }}>
            The sidebar's other screens are the design team's coded versions, now reading real pipeline data through
            <span className="mono"> dataService</span>. Once the embed URL is set, decide whether it replaces those
            screens or sits alongside them — until then they stay exactly where they are.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="card" style={{ overflow: 'hidden' }}>
        <iframe
          title="USTore Analytics — Power BI"
          src={EMBED_URL}
          allowFullScreen
          style={{ width: '100%', minHeight: 720, border: 0, display: 'block' }}
        />
      </div>
    </div>
  );
}