/**
 * TEMPORARY client-side persistence. NOT the system of record.
 *
 * TODO: backend — every function here disappears in Phase 3. Tallies,
 * closures and event flags must be written to the server (Fact_Sales,
 * Dim_Date.is_store_closed, Event_Log) and re-validated there. localStorage
 * is used only so a page refresh during a demo does not lose what was just
 * typed; it is per-browser, unauthenticated and trivially editable, so
 * nothing here may ever be treated as authoritative.
 *
 * Only dataService.js imports this module. Screens never do.
 */
const KEY = 'ustore.tally.v1';

const EMPTY = { entries: [], events: [], dateOverrides: {}, nextId: 1 };

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...EMPTY };
    const parsed = JSON.parse(raw);
    return { ...EMPTY, ...parsed };
  } catch {
    // corrupt or unavailable storage (private mode) — fall back to empty
    return { ...EMPTY };
  }
}

function write(state) {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // storage full or blocked; state stays in memory for this page view only
  }
  return state;
}

export function getEntries() {
  return read().entries;
}

export function addEntry(entry) {
  const state = read();
  const saved = {
    ...entry,
    // local_id, not sale_id: the server assigns the real key in Phase 3
    local_id: state.nextId,
    recorded_at: new Date().toISOString(),
  };
  state.entries.unshift(saved);
  state.nextId += 1;
  write(state);
  return saved;
}

export function getEvents() {
  return read().events;
}

export function addEvent(event) {
  const state = read();
  const saved = { ...event, local_id: state.nextId, created_by: 'local', date_logged: new Date().toISOString() };
  state.events.unshift(saved);
  state.nextId += 1;
  write(state);
  return saved;
}

/** Per-date flag overrides: { '2026-07-31': { is_store_closed: 1 } } */
export function getDateOverrides() {
  return read().dateOverrides;
}

export function setDateOverride(isoDate, patch) {
  const state = read();
  state.dateOverrides[isoDate] = { ...(state.dateOverrides[isoDate] || {}), ...patch };
  write(state);
  return state.dateOverrides[isoDate];
}

export function reset() {
  write({ ...EMPTY });
}
