/**
 * What a staff member has done with the Overview's floating "Event
 * Advisories" pill (components/Advisories.jsx AdvisoriesJump): pressed its X,
 * how often they used it, and which advisories they had seen at the time.
 *
 * It lasts one login. App.jsx clears it on every sign-in and sign-out, so the
 * pill is back at the start of each shift; within a login, X or three uses
 * hide it until a new advisory appears. Kept in localStorage so a page reload
 * mid-shift does not bring back a pill someone just closed. A preference
 * only: blocked or cleared storage simply shows the pill again.
 */
const PREFIX = 'ustore.advisoriesJump.';

export const jumpKey = user => `${PREFIX}${user || 'anonymous'}`;

export function readJumpPref(key) {
  try {
    return JSON.parse(window.localStorage.getItem(key)) || {};
  } catch {
    return {};
  }
}

export function writeJumpPref(key, pref) {
  try {
    window.localStorage.setItem(key, JSON.stringify(pref));
  } catch { /* storage blocked: the pill simply comes back next visit */ }
}

/** Forget every staff member's pill choices - called at sign-in and sign-out. */
export function resetAdvisoriesJump() {
  try {
    const store = window.localStorage;
    Object.keys(store).filter(k => k.startsWith(PREFIX)).forEach(k => store.removeItem(k));
  } catch { /* nothing stored, nothing to clear */ }
}
