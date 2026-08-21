import Icon from './Icon';

/**
 * Shown when the backend is unreachable or returned something unusable.
 * Without this, a failed fetch left `useData` in its `error` state forever
 * and every screen just showed its permanent loading spinner with no
 * explanation — see dataService.js's request() for what throws here.
 */
export default function ErrorBanner({ error }) {
  if (!error) return null;
  return (
    <div className="notice notice--warn" style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
      <span style={{ flexShrink: 0, marginTop: 1 }}><Icon name="alert" size={15} /></span>
      <span>
        <b>Connection problem:</b> {error.message}
      </span>
    </div>
  );
}
