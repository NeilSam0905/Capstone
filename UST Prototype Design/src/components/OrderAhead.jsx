import { useState } from 'react';
import { getReorderSeason } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from './Pending';
import DataTable from './DataTable';
import SearchSelect from './SearchSelect';
import InfoTip from './InfoTip';
import Icon from './Icon';
import ActiveThumb from './ActiveThumb';
import Modal from './Modal';
import { num, windowDates, FSN_TONE, FSN_LABEL } from '../lib/format';

/** '1 item', '22 items' */
const count = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

/**
 * Order ahead of the busy periods (the adviser, Nov 2026: "predict what
 * should be ordered ahead of a date or seasonality", "predict ROP based on
 * calendar"). From /api/reorder/season (backend/season_ahead.py): for each
 * coming enrollment or start of the first term, every counted item's stock is
 * projected day by day - the usual rate on ordinary days, the measured lift on
 * the busy ones, a regular order whenever it reaches its reorder point - which
 * gives the day it will reach its reorder point, and any EXTRA it needs so the
 * rush does not take it under its safety stock, with the day to order that by.
 *
 * Dates past the published school calendar are last year's, a year on, and
 * say so.
 */

const fmt = (iso, opts = { month: 'short', day: 'numeric' }) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', opts);
const withYear = iso => fmt(iso, { month: 'short', day: 'numeric', year: 'numeric' });

const STATUS = {
  rush_now: { tone: 'crit', text: () => 'Order extra now' },
  rush_by:  { tone: 'warn', text: p => `Order extra by ${fmt(p.order_by)}` },
  reorder:  { tone: 'info', text: p => `Reorder ${fmt(p.next_reorder.date)}` },
  covered:  { tone: 'ok',   text: () => 'Covered' },
  uncounted: { tone: null,  text: () => 'No count' },
};

/** Plan Ahead, a dialog opened from Reorder Alerts (and the Overview's
 *  banner). Two tabs, so it opens on the one thing to act on: the Order Plan
 *  for a chosen busy period, and the Campus Events with their past years.
 *  `onHistory(item)` opens an item's sales history over it. */
export default function OrderAheadModal({ open, onClose, onHistory }) {
  const { data } = useData(getReorderSeason, []);
  const [pick, setPick] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [tab, setTab] = useState('plan');
  if (!open) return null;

  const windows = data?.windows ?? [];
  const events = data?.events ?? [];
  let body;
  if (!data) body = <Loading label="Projecting stock through the busy periods…" />;
  else if (!data.available) body = <div className="empty">{data.reason}</div>;
  else {
    body = (
      <>
        <div className="topsellers__bar" style={{ marginBottom: 12 }}>
          <div className="period-tabs" role="tablist" aria-label="Plan Ahead">
            <ActiveThumb />
            {[['plan', 'Order Plan'], ['events', `Campus Events (${events.length})`]].map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k}
                      className={'period-tab' + (tab === k ? ' is-active' : '')} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          <OrderAheadInfo />
        </div>
        <div className="modal-grow">
          {tab === 'events'
            ? <UpcomingEvents events={events} rules={data.rules}
                              onPlan={ev => {
                                const i = windows.findIndex(x => x.kind === 'event' && x.label === ev.name
                                  && x.end === ev.end);
                                if (i >= 0) { setPick(i); setTab('plan'); }
                              }} />
            : windows.length
              ? <OrderPlan data={data} w={windows[Math.min(pick, windows.length - 1)]} windows={windows}
                           pick={pick} setPick={setPick} showAll={showAll} setShowAll={setShowAll}
                           onHistory={onHistory} />
              : <div className="empty">No enrollment, start of term or rush event in the next 12 months.</div>}
        </div>
      </>
    );
  }
  return (
    <Modal open onClose={onClose} width={1180} title="Plan Ahead — Busy Periods"
           subtitle="What to order before enrollment, the start of term and Paskuhan, so stock is on the shelf in time.">
      {body}
    </Modal>
  );
}

/** The Order Plan tab: one busy period, its figures and its items. */
function OrderPlan({ data, w, windows, pick, setPick, showAll, setShowAll, onHistory }) {
  const acting = w.items.filter(p => p.status !== 'covered' && p.status !== 'uncounted');
  const rows = (showAll ? w.items : acting).map(p => ({ ...p, rowKey: `oa${p.product_id}` }));

  const columns = [
    {
      key: 'item_name', label: 'Item', strong: true, width: '24%',
      render: (v, p) => (
        <button type="button" className="item-link cell-trunc" title={`${v}: show its sales history`}
                onClick={() => onHistory?.(p)}>{v}</button>
      ),
    },
    {
      key: 'fsn_class', label: 'Class', width: '9%',
      render: v => (v ? <span className={`tag tag--${FSN_TONE[v] || 'info'}`}>{FSN_LABEL[v] || v}</span> : '—'),
    },
    {
      key: 'current_stock', label: 'On Hand', num: true, width: '9%',
      render: (v, p) => (v == null ? <span className="muted">—</span>
        : <span title={p.stock_as_of ? `Counted ${p.stock_as_of}` : undefined}>{num(v)}</span>),
    },
    { key: 'reorder_point', label: 'ROP', num: true, width: '7%', render: v => num(Math.round(v)) },
    {
      key: 'next_reorder', label: 'Reaches ROP', width: '14%',
      render: v => (v
        ? <span title={`Order ${num(v.qty)} then; it arrives ${withYear(v.arrives)}`}>
            {fmt(v.date)} <span className="muted">· {num(v.qty)}</span>
          </span>
        : <span className="muted">—</span>),
    },
    {
      key: 'lowest_in_window', label: 'Lowest in Rush', num: true, width: '11%',
      render: (v, p) => (v == null ? <span className="muted">—</span>
        : <span className={v < p.safety_stock ? 'ahead__low' : undefined}
                title={`Safety stock ${num(Math.round(p.safety_stock))}`}>{num(Math.max(0, Math.round(v)))}</span>),
    },
    {
      key: 'rush_order', label: 'Extra for Rush', num: true, width: '11%',
      render: (v, p) => (p.status === 'uncounted'
        ? <span className="muted" title={`The rush itself needs about ${num(Math.round(p.window_need))}, safety stock included`}>
            count first</span>
        : v ? <b>{num(v)}</b> : <span className="muted">—</span>),
    },
    {
      key: 'status', label: 'What To Do', width: '15%', sortable: false,
      render: (v, p) => {
        const s = STATUS[v];
        return s.tone
          ? <span className={`tag tag--${s.tone}`} title={p.arrives_late && v.startsWith('rush')
              ? 'Ordered today it would still arrive after the rush starts' : undefined}>{s.text(p)}</span>
          : <span className="muted">{s.text(p)}</span>;
      },
    },
  ];

  return (
    <>
      <div className="filter-row" style={{ marginBottom: 10 }}>
        <div className="filter-field">
          <label className="filter-field__label" htmlFor="oa-window">Busy Period</label>
          <SearchSelect id="oa-window" value={String(pick)} onChange={v => setPick(+v)} minWidth={300}
                        searchable={false}
                        options={windows.map((x, i) => ({
                          value: String(i),
                          label: `${x.label} · ${windowDates(x)}${x.estimated ? ' (estimated)' : ''}`,
                        }))} />
        </div>
      </div>

      <p className="hint" style={{ margin: '0 0 12px' }}>
        {w.kind === 'event'
          ? <>{w.label} and the day before sold <b>{w.lift.toFixed(2)}×</b> a regular day on average
            ({count(w.past_days, 'tallied day')} in past years), a rough guide with so few days.</>
          : <>{w.label} days sell <b>{w.lift.toFixed(2)}×</b> a regular day ({num(w.past_days)} tallied days so far).</>}
        {w.estimated && <> The school calendar is published to {withYear(data.calendar_end)}, so these dates are last
          year&rsquo;s{w.from_start ? <> ({windowDates({ start: w.from_start, end: shiftBack(w.end) })})</> : null} a year
          on.</>}
      </p>

      <div className="avp-stats">
        <div className="avp-stat">
          <span className="avp-stat__label">Reach their reorder point first</span>
          <span className="avp-stat__val">{count(w.n_reorder, 'item')}</span>
          <span className="avp-stat__sub">before the rush ends · order them as usual, on the dates below</span>
        </div>
        <div className="avp-stat">
          <span className="avp-stat__label">Need extra for the rush</span>
          <span className={`avp-stat__val${w.n_rush ? ' is-crit' : ' is-ok'}`}>
            {w.n_rush ? `${count(w.n_rush, 'item')} · ${count(w.rush_units, 'unit')}` : 'None'}
          </span>
          <span className="avp-stat__sub">
            {w.n_rush
              ? <>order by {withYear(w.earliest_order_by)}{w.n_rush_now ? `, ${num(w.n_rush_now)} of them now` : ''}</>
              : 'the regular reorders cover it'}
          </span>
        </div>
        <div className="avp-stat">
          <span className="avp-stat__label">Not counted</span>
          <span className="avp-stat__val">{count(w.n_uncounted, 'item')}</span>
          <span className="avp-stat__sub">no stock to project from · count them in the Tally Interface</span>
        </div>
      </div>

      <div className="topsellers__bar" style={{ marginBottom: 8 }}>
        <div className="period-tabs period-tabs--sm" role="tablist" aria-label="Which items">
          <ActiveThumb />
          {[[false, `To act on (${acting.length})`], [true, `All ${w.items.length}`]].map(([k, label]) => (
            <button key={label} type="button" role="tab" aria-selected={showAll === k}
                    className={'period-tab' + (showAll === k ? ' is-active' : '')} onClick={() => setShowAll(k)}>
              {label}
            </button>
          ))}
        </div>
        <span className="hint">Click an item for its sales history</span>
      </div>
      {rows.length
        ? <DataTable columns={columns} data={rows} pageSize={10} minWidth={980} />
        : <div className="empty">Every counted item has enough stock through this period.</div>}
    </>
  );
}

/** What each event's track record says, as a tag. */
const EVENT_TAG = {
  rush:    { tone: 'warn', text: 'Rush' },
  mixed:   { tone: 'info', text: 'Mixed' },
  quieter: { tone: 'ok',   text: 'Quieter' },
  unknown: { tone: null,   text: 'No data yet' },
};

/** Every campus event in the next year with how the store sold on it in past
 *  years, so staff know what is coming. Only a Rush event gets an order plan
 *  (below, in the Busy Period list); the rest are for awareness, because a
 *  campus event is as likely to sell less than a regular day as more. */
function UpcomingEvents({ events, rules, onPlan }) {
  if (!events.length) return null;
  const rushOver = rules?.rush_lift ?? 1.5;
  return (
    <div className="ahead-events">
      <div className="ahead-events__h">
        <span className="advisory__basis-label" style={{ margin: 0 }}>Upcoming campus events</span>
        <span className="hint">
          Each past year: sales on the event and the day before, against a regular day. Rush means every tallied
          year sold {rushOver}× or more ({rules?.min_rush_years ?? 2} years at least); only those are planned for.
        </span>
      </div>
      <div className="tbl__scroll">
        <table className="tbl ahead-events__tbl">
          <tbody>
            {events.map(ev => {
              const tag = EVENT_TAG[ev.tag] ?? EVENT_TAG.unknown;
              const planned = ev.tag === 'rush' && !ev.part_of;
              return (
                <tr key={`${ev.start}${ev.name}`}>
                  <td className="ahead-events__date">
                    {windowDates(ev)}
                    {ev.estimated && <span className="ahead-sub">estimated</span>}
                  </td>
                  <td><span className="cell-trunc" style={{ '--trunc': '360px' }} title={ev.name}>{ev.name}</span></td>
                  <td className="ahead-events__years">
                    {ev.years.length
                      ? ev.years.map(y => (
                        <span key={y.start} className={'ahead-year' + (y.value == null ? ' is-none' : '')}
                              title={y.value == null ? `${y.start.slice(0, 4)}: nothing tallied around it`
                                : `${y.start}: ${y.value.toFixed(2)}× a regular day over ${y.recorded_days} tallied day(s)`}>
                          {y.start.slice(0, 4)} <b>{y.value == null ? '—' : `${y.value.toFixed(1)}×`}</b>
                        </span>
                      ))
                      : <span className="muted">first time on record</span>}
                  </td>
                  <td className="ahead-events__tag">
                    {tag.tone
                      ? <span className={`tag tag--${tag.tone}`}>{tag.text}</span>
                      : <span className="muted">{tag.text}</span>}
                    {ev.part_of && <span className="ahead-sub">planned in {ev.part_of}</span>}
                    {planned && (
                      <button type="button" className="linkish" style={{ marginLeft: 8 }} onClick={() => onPlan(ev)}>
                        See the plan
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The window a year back, for the "last year's dates" note. */
function shiftBack(iso) {
  return `${+iso.slice(0, 4) - 1}${iso.slice(4)}`;
}

function OrderAheadInfo() {
  return (
    <InfoTip label="How is this worked out?" width={440}>
      <div className="infotip__h">Order ahead of a busy period</div>
      <p>
        The reorder point is set for a regular day&rsquo;s sales, so it cannot see a rush coming. For each counted item,
        its stock is projected day by day up to the end of the busy period:
      </p>
      <ul className="infotip__bullets">
        <li>it sells its usual amount a day (ADUS), and the measured multiple of it on the busy days;</li>
        <li>whenever it reaches its reorder point, it is reordered as the Reorder table says, arriving a lead time later.</li>
      </ul>
      <p>
        <b>Reaches ROP</b> is the day that first happens. If the rush would still take the stock under its safety stock,
        <b> Extra for Rush</b> is the difference, ordered a lead time before the rush starts so it is on the shelf in time.
      </p>
      <p>
        The busy periods are enrollment, the start of the first term, and any campus event that sold well above a
        regular day every year it was tallied (Paskuhan). Other events are listed for awareness only.
      </p>
      <p>Stock is as of the last inventory count, as in the Reorder table.</p>
    </InfoTip>
  );
}

/** One line for the Overview's reorder banner: the next busy period and what
 *  it asks for, with the way to the full list. */
export function OrderAheadLine({ onOpen }) {
  const { data } = useData(getReorderSeason, []);
  const w = data?.available ? data.windows?.[0] : null;
  if (!w) return null;
  return (
    <div className="hero__ahead">
      <span className="hero__ahead-icon"><Icon name="cal" size={15} /></span>
      <span>
        <b>Next rush: {w.label}</b>, {w.estimated ? 'around ' : ''}{windowDates(w)}
        {w.estimated ? ' (estimated)' : ''} · <b>{num(w.n_reorder)}</b>{' '}
        {w.n_reorder === 1 ? 'item reaches its' : 'items reach their'} reorder point before it ends
        {' · '}
        {w.n_rush
          ? <><b className="is-crit">{num(w.n_rush)}</b> {w.n_rush === 1 ? 'needs' : 'need'} extra stock,
            order by {withYear(w.earliest_order_by)}</>
          : 'the regular reorders cover the rush'}
      </span>
      {onOpen && (
        <button type="button" className="linkish" onClick={onOpen}>
          Plan ahead <Icon name="arrow" size={12} />
        </button>
      )}
    </div>
  );
}
