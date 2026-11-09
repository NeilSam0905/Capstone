import Icon from './Icon';

/**
 * Shown where the pipeline has not produced a number yet.
 *
 * Renders the reason from dataService's meta rather than a hardcoded
 * string: a screen must never invent an analytic the ETL has not
 * computed. If you are tempted to put a plausible-looking figure here,
 * that is the thing this component exists to prevent.
 */
export default function Pending({ title, reason, children }) {
  return (
    <div className="pending">
      <span className="pending__icon"><Icon name="clock" size={19} /></span>
      <div className="pending__title">{title}</div>
      {reason && <div className="pending__body">{reason}</div>}
      {children}
    </div>
  );
}

/** Inline placeholder for a KPI slot that has no number yet. */
export function PendingValue() {
  return <span style={{ color: 'var(--line)', fontWeight: 700 }}>—</span>;
}

/** A section still fetching: a small spinner beside what it is fetching, so
 *  waiting reads as work under way rather than an empty card. It fades in a
 *  moment late, so a fast answer never flashes a spinner. */
export function Loading({ label = 'Loading…' }) {
  return (
    <div className="empty loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
