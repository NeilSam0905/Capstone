import { useEffect, useState } from 'react';

/**
 * Read through dataService without every screen hand-rolling loading state.
 *
 *   const { data, loading, error } = useData(() => getProducts(filters), [filters]);
 *
 * The service is async on purpose (see dataService.js), so when Phase 3
 * turns those calls into HTTP requests the screens do not change at all.
 *
 * `loading` is true until the first resolve. On a dependency change the
 * previous data stays on screen until the new data arrives, which avoids
 * a flash of empty state — and keeps every setState inside an async
 * callback rather than in the effect body.
 */
export default function useData(loader, deps = [], initial = null) {
  const [state, setState] = useState({ data: initial, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(loader)
      .then(data => { if (!cancelled) setState({ data, loading: false, error: null }); })
      .catch(error => { if (!cancelled) setState(s => ({ ...s, loading: false, error })); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
