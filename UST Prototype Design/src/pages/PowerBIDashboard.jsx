import PowerBIEmbed from '../components/PowerBIEmbed';
import { POWERBI_EMBED_URL, inspectEmbedUrl } from '../config';

/**
 * The analytics dashboard: a published Power BI report, embedded.
 *
 * Per the manuscript the five analytical views (stock status, FSN, demand
 * forecast, batch sales report, calendar cards) are authored in Power BI
 * against the same SQLite star schema this app reads — so they are embedded
 * here rather than rebuilt in code.
 *
 * The coded screens in the sidebar stay alongside this one; that was an
 * explicit call, not an oversight. They read real pipeline data through
 * dataService and remain useful while the .pbix is still being built.
 */
export default function PowerBIDashboard() {
  const check = inspectEmbedUrl(POWERBI_EMBED_URL);

  return (
    <div className="stack">
      {check.state === 'ok' && (
        <div className="card-h" style={{ marginBottom: 0 }}>
          <span className="section-h">USTore Analytics — Power BI</span>
          <span className="hint">
            {check.method}
            {check.method.startsWith('Publish to web') && ' · this report is publicly viewable'}
          </span>
        </div>
      )}

      <PowerBIEmbed />

      {check.state !== 'ok' && (
        <div className="card card__pad">
          <div className="card-h"><span className="section-h">What goes here</span></div>
          <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55 }}>
            The five analytical views are authored in Power BI Desktop against the same SQLite star
            schema this app reads, published to the Power BI Service, and embedded on this route.
            Building the <span className="mono">.pbix</span> is a separate task; this screen is the
            container that holds it. The sidebar’s coded screens stay where they are — Phase 2 adds
            the embed, it does not replace them.
          </div>
        </div>
      )}
    </div>
  );
}
