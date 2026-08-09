import Icon from './Icon';
import {
  POWERBI_EMBED_URL, POWERBI_REPORT_TITLE, POWERBI_CONFIG_LOCATION, inspectEmbedUrl,
} from '../config';

/**
 * Renders a published Power BI report in a responsive iframe.
 *
 * The URL is configuration, never source (see src/config.js). Three states:
 *
 *   unset    → a clean "not configured" card naming where to put the URL, so
 *              the app ships and demos before the report exists
 *   invalid  → says what is wrong with the value rather than rendering a
 *              frame that would silently fail to load
 *   ok       → the iframe, filling its container
 *
 * Deliberately NOT implemented: Power BI Embedded / Azure app-owns-data with
 * a service principal and embed tokens. That is the production upgrade path
 * (see the README); it needs a backend to mint tokens, which is Phase 3 at
 * the earliest and a paid Azure capacity in reality.
 */
export default function PowerBIEmbed({
  url = POWERBI_EMBED_URL,
  title = POWERBI_REPORT_TITLE,
  configLocation = POWERBI_CONFIG_LOCATION,
}) {
  const check = inspectEmbedUrl(url);

  if (check.state === 'unset') {
    return (
      <div className="pending" style={{ padding: '64px 32px' }}>
        <span className="pending__icon" style={{ width: 54, height: 54 }}>
          <Icon name="chart" size={24} />
        </span>
        <div className="section-title" style={{ fontSize: 18 }}>
          Power BI report not configured
        </div>
        <div className="pending__body">
          Add the embed URL in <span className="mono">{configLocation}</span>, then restart the dev
          server. See <b>“Set up the Power BI dashboard”</b> in the README for how to publish the
          report and where to copy the URL from.
        </div>
        <span className="tag tag--gold">Awaiting embed URL</span>
      </div>
    );
  }

  if (check.state === 'invalid') {
    return (
      <div className="pending" style={{ padding: '48px 32px', borderColor: 'var(--crit-line)' }}>
        <span className="pending__icon" style={{ width: 54, height: 54, background: 'var(--crit-bg)', color: 'var(--crit)' }}>
          <Icon name="alert" size={24} />
        </span>
        <div className="section-title" style={{ fontSize: 18 }}>Embed URL doesn’t look right</div>
        <div className="pending__body">{check.reason}</div>
        <div className="pending__body mono" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>{url}</div>
        <span className="tag tag--crit">Fix it in {configLocation}</span>
      </div>
    );
  }

  return (
    <div className="pbi">
      <iframe
        className="pbi__frame"
        title={title}
        src={url}
        allowFullScreen
        loading="lazy"
        referrerPolicy="strict-origin-when-cross-origin"
      />
    </div>
  );
}
