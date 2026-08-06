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

/** FSN presentation: tone maps onto the design system's status colours. */
export const FSN_TONE = { F: 'ok', S: 'warn', N: 'crit' };
export const FSN_LABEL = { F: 'Fast', S: 'Slow', N: 'Non-Moving' };
