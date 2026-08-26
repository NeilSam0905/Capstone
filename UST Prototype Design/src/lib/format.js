/** Shared formatters and chart palette (kept out of charts.jsx so that file
 *  only exports components — see react-refresh/only-export-components). */
export const peso = n => '₱' + Math.round(n).toLocaleString();
export const pesoK = n => n >= 1e6 ? '₱' + (n / 1e6).toFixed(2) + 'M' : '₱' + (n / 1e3).toFixed(0) + 'K';
export const num = n => Math.round(n).toLocaleString();

export const DONUT_COLORS = ['var(--gold)', '#16140F', '#2C5E8A', '#2E7D55', '#C1452F', '#B5791A'];

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

/** '2025-08' -> 'Aug 25' */
export const shortMonth = m => {
  const [y, mo] = m.split('-');
  return `${MONTHS_SHORT[+mo - 1]} ${y.slice(2)}`;
};

/** '2025-08' -> 'August 2025' */
export const longMonth = m => {
  const [y, mo] = m.split('-');
  return `${MONTHS_LONG[+mo - 1]} ${y}`;
};

/** '2025-08-21' -> '08/21/2025'. Month/day/year for display only.
 *
 *  Everything in this project STORES dates as ISO 8601 (see the README's
 *  "every date in every CSV is ISO 8601" rule, and `<input type="date">`,
 *  whose value must be ISO) - this is purely a rendering step at the edge.
 *  Never feed its output back into an API call or an input value.
 *
 *  Anything that isn't an ISO date is returned untouched rather than
 *  mangled, so a null, an empty string or an already-formatted value
 *  passes through instead of rendering as "NaN/NaN/NaN". */
export const usDate = d => {
  if (typeof d !== 'string') return d ?? '';
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[2]}/${m[3]}/${m[1]}` : d;
};

/** '2025-08-21T19:07:53' -> '08/21/2025 19:07'. Same rules as usDate. */
export const usDateTime = d => {
  if (typeof d !== 'string') return d ?? '';
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  return m ? `${m[2]}/${m[3]}/${m[1]} ${m[4]}:${m[5]}` : usDate(d);
};

/** FSN presentation: tone maps onto the design system's status colours. */
export const FSN_TONE = { F: 'ok', S: 'warn', N: 'crit' };
export const FSN_LABEL = { F: 'Fast', S: 'Slow', N: 'Non-Moving' };
