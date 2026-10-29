/** The Batch Sales Report's period: one day, one week or one month.
 *
 *  The page keeps what the reader picked as { kind, month, date } - a month
 *  ('YYYY-MM') for the month view, a day ('YYYY-MM-DD') for the other two,
 *  where a week is the Monday-to-Sunday week that holds that day. The API
 *  takes it as query params: month=YYYY-MM, or start= and end=YYYY-MM-DD
 *  (backend/app.py _batch_period). The labels match the ones the PDF and the
 *  sheet print (backend/batch_pdf.py "periods"). */
import { longMonth } from './format';

export const PERIOD_KINDS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month']];

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

// Dates as UTC midnights, so no time zone or daylight-saving shift can move
// a day across the boundary.
const parse = iso => new Date(`${iso}T00:00:00Z`);
const iso = d => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = parse(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };

/** The Monday and Sunday of the week holding `day`. */
export function weekOf(day) {
  const back = (parse(day).getUTCDay() + 6) % 7;   // days since Monday
  const monday = addDays(day, -back);
  return [monday, addDays(monday, 6)];
}

/** The period as API query params, or null while it has nothing to show. */
export function periodParams({ kind, month, date }) {
  if (kind === 'month') return month ? { month } : null;
  if (!date) return null;
  const [start, end] = kind === 'week' ? weekOf(date) : [date, date];
  return { start, end };
}

const md = s => { const d = parse(s); return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`; };
const year = s => s.slice(0, 4);

/** 'July 2026', 'Tuesday, July 28, 2026', 'July 27 – August 2, 2026'. */
export function periodLabel(params) {
  if (!params) return '—';
  if (params.month) return longMonth(params.month);
  const { start, end } = params;
  if (start === end) return `${WEEKDAYS[parse(start).getUTCDay()]}, ${md(start)}, ${year(start)}`;
  if (year(start) !== year(end)) return `${md(start)}, ${year(start)} – ${md(end)}, ${year(end)}`;
  const tail = start.slice(0, 7) === end.slice(0, 7) ? String(parse(end).getUTCDate()) : md(end);
  return `${md(start)} – ${tail}, ${year(end)}`;
}

/** For filenames, as the server names them: '2026-07', '2026-07-28',
 *  '2026-07-27_to_2026-08-02'. */
export function periodSlug(params) {
  if (!params) return '';
  if (params.month) return params.month;
  return params.start === params.end ? params.start : `${params.start}_to_${params.end}`;
}
