/**
 * App configuration. This is the one place a deployment-specific value goes.
 *
 * ── Power BI dashboard ────────────────────────────────────────────────────
 * The analytics dashboard is a Power BI report, embedded rather than rebuilt
 * in code. Its URL only exists after someone publishes the .pbix to the Power
 * BI Service, so it is configuration, not source: set it in `.env.local`
 * (gitignored) and leave this file alone.
 *
 *     VITE_POWERBI_EMBED_URL=https://app.powerbi.com/view?r=eyJrIjoi...
 *
 * Copy `.env.example` to `.env.local` to get started. The README's
 * "Set up the Power BI dashboard" section explains how to obtain the URL
 * (Method A: publish to web / Method B: secure embed).
 *
 * Unset is a supported state: the dashboard renders a clean placeholder, so
 * the frontend ships and demos fine before the report exists.
 */
export const POWERBI_EMBED_URL = (import.meta.env.VITE_POWERBI_EMBED_URL ?? '').trim();

/** Shown as the iframe's accessible title. */
export const POWERBI_REPORT_TITLE =
  (import.meta.env.VITE_POWERBI_REPORT_TITLE ?? 'USTore Analytics Dashboard').trim();

/** Where a human should go to set the above — quoted in the placeholder UI. */
export const POWERBI_CONFIG_LOCATION = '.env.local (VITE_POWERBI_EMBED_URL)';

/**
 * Accepted shapes:
 *   Method A (publish to web)  https://app.powerbi.com/view?r=...
 *   Method B (secure embed)    https://app.powerbi.com/reportEmbed?reportId=...
 * Anything else is almost certainly a workspace link or a pasted <iframe>
 * tag, which would render a broken frame — the UI says so instead.
 */
export function inspectEmbedUrl(url) {
  if (!url) return { state: 'unset' };
  if (/<\s*iframe/i.test(url)) {
    return {
      state: 'invalid',
      reason: 'That looks like a whole <iframe> tag. Paste only the URL inside its src="…" attribute.',
    };
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { state: 'invalid', reason: 'That is not a valid URL.' };
  }
  if (parsed.protocol !== 'https:') {
    return { state: 'invalid', reason: 'The embed URL must start with https://.' };
  }
  if (parsed.hostname !== 'app.powerbi.com') {
    return {
      state: 'invalid',
      reason: `Expected a link on app.powerbi.com, got ${parsed.hostname}.`,
    };
  }
  const isView = parsed.pathname.startsWith('/view');
  const isReportEmbed = parsed.pathname.startsWith('/reportEmbed');
  if (!isView && !isReportEmbed) {
    return {
      state: 'invalid',
      reason: 'Expected a /view (publish to web) or /reportEmbed (secure embed) link. '
            + 'A workspace or report page URL will not embed.',
    };
  }
  return { state: 'ok', method: isView ? 'Publish to web (public)' : 'Secure embed (login required)' };
}
