import { useEffect, useRef, useState } from 'react';
import { getCacheVersion } from '../services/dataService';

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
 * a flash of empty state - and keeps every setState inside an async
 * callback rather than in the effect body.
 *
 * ## Snapshots (the optional `key`)
 *
 * dataService caches the HTTP response, so revisiting a page costs no network
 * request - but the hook still starts at `loading: true` and any screen that
 * does `if (loading) return <Loading/>` would flash a spinner for one frame on
 * every visit.
 *
 * Passing a `key` fixes that: the last successful value for that key is kept
 * in module scope and used as the INITIAL state, so a revisited page renders
 * its data on the first paint. The value is still refetched in the background,
 * so a stale snapshot corrects itself rather than sticking.
 *
 * Snapshots are tagged with dataService's cache generation. Any write bumps
 * that generation, which retires every snapshot taken before it - otherwise
 * saving a tally entry and going back to Overview would seed from figures the
 * save had just invalidated.
 */
const snapshots = new Map();   // key -> { version, data }

export default function useData(loader, deps = [], initial = null, { key } = {}) {
  const seed = key != null ? snapshots.get(key) : undefined;
  const fresh = seed && seed.version === getCacheVersion();

  const [state, setState] = useState(
    fresh ? { data: seed.data, loading: false, error: null }
          : { data: initial, loading: true, error: null }
  );

  // Which key the current state belongs to. When the key changes (a filter
  // moved, say) we re-seed from that key's snapshot instead of showing the
  // previous key's data as though it were the new one.
  const seededFor = useRef(fresh ? key : null);

  useEffect(() => {
    let cancelled = false;

    if (key != null && seededFor.current !== key) {
      const snap = snapshots.get(key);
      if (snap && snap.version === getCacheVersion()) {
        setState({ data: snap.data, loading: false, error: null });
      }
      seededFor.current = key;
    }

    Promise.resolve()
      .then(loader)
      .then(data => {
        if (key != null) snapshots.set(key, { version: getCacheVersion(), data });
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch(error => { if (!cancelled) setState(s => ({ ...s, loading: false, error })); });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
