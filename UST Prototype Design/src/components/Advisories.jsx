import { useEffect, useState } from 'react';
import { getAdvisories } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from './Pending';
import Modal from './Modal';
import Icon from './Icon';
import { num, usDate, FSN_TONE, FSN_LABEL } from '../lib/format';
import { jumpKey, readJumpPref, writeJumpPref } from '../lib/advisoriesJump';

/**
 * The advisories: calendar signals and the FSN decision rules, from
 * /api/advisories, as cards that open onto the items they name.
 *
 * Shown in full on the Overview, and on every other dashboard page through
 * the topbar's Advisories button (AdvisoriesButton + AdvisoriesModal), so the
 * prescriptive side of the dashboard is one click away wherever the reader is.
 * One component for both, so the two can never word an advisory differently.
 */

/* Severity drives one token set — icon tint, chip tone, accent rail — instead
   of the ad-hoc inline borderLeft/background pairs this used to carry. Keeping
   them in one map is what stops "high" meaning warn in one place and crit in
   another. */
const SEVERITY = {
  high:   { cls: 'is-high',   tag: 'warn', label: 'Act now' },
  medium: { cls: 'is-medium', tag: 'gold', label: 'Plan for' },
  low:    { cls: 'is-low',    tag: 'info', label: 'For info' },
};

const TYPE_ICON = {
  enrollment: 'calPlus',
  exam_week: 'file',
  event: 'bell',
  forecast_alert: 'trend',
  info: 'clock',
  slow_moving: 'tag',
  discontinuation: 'xCircle',
  non_moving: 'box',
};

/* The last three are the FSN decision rules (manuscript Sec. 3.3.1), raised
   by /api/advisories from backend/decision_rules.py. */
const TYPE_LABEL = {
  enrollment: 'Calendar',
  exam_week: 'Calendar',
  event: 'Event',
  forecast_alert: 'Forecast',
  info: 'Status',
  slow_moving: 'FSN rule',
  discontinuation: 'FSN rule',
  non_moving: 'FSN rule',
};

export function AdvisoriesPanel({ advisories, setPage }) {
  if (!advisories) return <Loading label="Loading advisories…" />;

  const items = advisories.advisories ?? [];

  if (items.length === 0) {
    return (
      <div className="card card__pad">
        <div className="hint">No upcoming calendar signals or events to advise on.</div>
      </div>
    );
  }

  return (
    <div className="advisories">
      {!advisories.has_forecast && (
        <div className="notice notice--warn">
          <b>Limited advisories:</b> the demand forecast has not been generated yet, so these are
          based on calendar signals only. Run the forecast from the Tally Interface to enable
          demand-driven recommendations.
        </div>
      )}
      {items.map((advisory, i) => (
        <AdvisoryCard key={i} advisory={advisory} setPage={setPage} />
      ))}
    </div>
  );
}

/** One advisory.
 *
 *  Collapsed it is the sentence it always was, plus a scannable meta row.
 *  Expanded it answers the two questions the sentence raises and never used
 *  to: WHICH items, and on WHAT basis. Both come from the API (`items`,
 *  `basis`) rather than being inferred here — a screen must not invent an
 *  analytic the backend has not produced.
 *
 *  Only advisories that actually carry detail are expandable, so a chevron
 *  never promises something that is not there.
 */
function AdvisoryCard({ advisory, setPage }) {
  const [open, setOpen] = useState(false);
  const sev = SEVERITY[advisory.severity] ?? SEVERITY.low;
  const items = advisory.items ?? [];
  const expandable = items.length > 0 || Boolean(advisory.basis);
  const belowRop = items.filter(it => it.below_rop).length;
  // Slow and Non-moving items are never forecast, so on the decision-rule
  // advisories this column would be a dash on every row.
  const showForecast = items.some(it => it.forecast_30d != null);

  const dateText = advisory.date_range
    && (advisory.date_range[0] === advisory.date_range[1]
      ? usDate(advisory.date_range[0])
      : `${usDate(advisory.date_range[0])} → ${usDate(advisory.date_range[1])}`);

  const head = (
    <>
      <span className="advisory__icon"><Icon name={TYPE_ICON[advisory.type] || 'bell'} size={17} /></span>

      <div className="advisory__main">
        <div className="advisory__title">{advisory.title}</div>
        <div className="advisory__desc">{advisory.description}</div>

        <div className="advisory__meta">
          <span className="advisory__kind">{TYPE_LABEL[advisory.type] || 'Advisory'}</span>
          {dateText && <span>{dateText}</span>}
          {items.length > 0 && (
            <span>{num(items.length)} item{items.length === 1 ? '' : 's'}</span>
          )}
          {belowRop > 0 && (
            <span className="advisory__flag">{num(belowRop)} below ROP</span>
          )}
        </div>
      </div>

      <span className="advisory__right">
        <span className={`tag tag--${sev.tag}`}>{sev.label}</span>
        {expandable && <span className={'advisory__chev' + (open ? ' is-open' : '')} aria-hidden="true" />}
      </span>
    </>
  );

  if (!expandable) {
    return <div className={`card advisory ${sev.cls}`}><div className="advisory__head">{head}</div></div>;
  }

  return (
    <div className={`card advisory ${sev.cls}` + (open ? ' is-open' : '')}>
      <button type="button" className="advisory__head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {head}
      </button>

      {open && (
        <div className="advisory__body">
          {advisory.basis && (
            <div className="advisory__basis">
              <span className="advisory__basis-label">Why this is showing</span>
              {advisory.basis}
            </div>
          )}

          {items.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table className="tbl" style={{ minWidth: 620 }}>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Class</th>
                    {showForecast && <th className="num">Forecast 30d</th>}
                    <th className="num">On hand</th>
                    <th className="num">ROP</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(it => (
                    <tr key={it.product_id}>
                      <td>
                        <b>{it.item_name}</b>
                        <div className="hint">{it.supplier_name}</div>
                      </td>
                      <td><span className={`tag tag--${FSN_TONE[it.fsn_class] || 'info'}`}>{FSN_LABEL[it.fsn_class] || '—'}</span></td>
                      {showForecast && (
                        <td className="num">
                          {it.forecast_30d == null ? <span className="muted">—</span> : num(Math.round(it.forecast_30d))}
                        </td>
                      )}
                      <td className="num">
                        {it.current_stock == null
                          ? <span className="muted">never counted</span>
                          : num(it.current_stock)}
                      </td>
                      <td className="num">
                        {it.reorder_point == null
                          ? <span className="muted">—</span>
                          : <span className={it.below_rop ? 'advisory__rop-hit' : undefined}>
                              {num(Math.round(it.reorder_point))}
                            </span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn--ghost btn--sm" onClick={() => setPage?.('reorder')}>
              Reorder Alerts <Icon name="arrow" size={12} />
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => setPage?.('forecast')}>
              Demand Forecast <Icon name="arrow" size={12} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ everywhere */

/** Advisory types to list first on each page: the ones about what that page
 *  shows. The rest follow in the API's own order. */
const RELEVANT = {
  classification: ['slow_moving', 'discontinuation', 'non_moving'],
  forecast:       ['forecast_alert', 'enrollment', 'exam_week', 'event'],
  reorder:        ['forecast_alert', 'enrollment', 'non_moving', 'discontinuation'],
};

/** The "no upcoming calendar signals" card is a status line, not something
 *  to act on, so it is not counted on the button. */
const actionable = list => (list ?? []).filter(a => a.type !== 'info');

/** How many advisories there are to act on, and how many say "Act now". */
function useAdvisoryCount() {
  const { data } = useData(getAdvisories, []);
  const items = actionable(data?.advisories);
  const urgent = items.filter(a => a.severity === 'high').length;
  const title = items.length === 0
    ? 'No advisories right now'
    : `${items.length} advisor${items.length === 1 ? 'y' : 'ies'}`
      + (urgent ? `, ${urgent} to act on now` : '');
  return { count: items.length, title };
}

/** The topbar button, on every dashboard page. The red circle counts the
 *  advisories. */
export function AdvisoriesButton({ onClick }) {
  const { count, title } = useAdvisoryCount();
  return (
    <button type="button" className="btn btn--ghost btn--sm topbar__advisories" onClick={onClick} title={title}
            aria-label={`Advisories - ${title}`}>
      <Icon name="bell" size={14} />
      <span>Advisories</span>
      {count > 0 && <span className="topbar__badge">{num(count)}</span>}
    </button>
  );
}

/** The Overview's floating "jump to the advisories" pill.
 *
 *  The advisories are the last section of a long page, so this sits at the
 *  bottom of the screen and scrolls down to them, and steps out of the way
 *  once the section itself is in view - at that point it would only cover the
 *  cards it leads to. `target` is a ref to that section.
 *
 *  Absolutely positioned against .main, which does not scroll (.scroll inside
 *  it does), so it stays put while the page moves under it.
 *
 *  It retires, per staff member (`user`), once they have either pressed its X
 *  or used it JUMP_RETIRE_AFTER times - by then they know where the
 *  advisories are, and the topbar's Advisories button is still there. But it
 *  retires only for the advisories they had already seen: the set on screen
 *  at that moment is remembered, and when a NEW advisory appears (a new
 *  calendar window, an event, a rule that newly applies) the pill comes back,
 *  counting afresh. Retiring for good hid new advice behind a click someone
 *  made weeks earlier about different advice.
 *  All of it lasts one login: App.jsx clears it at every sign-in and
 *  sign-out (lib/advisoriesJump.js), so the pill is back each shift. */
const JUMP_RETIRE_AFTER = 3;

/** One advisory's identity across reloads: what it is about, and when. */
const adviceKey = a => `${a.type}|${a.title}|${a.date_range?.[0] ?? ''}`;

export function AdvisoriesJump({ target, user }) {
  const { count, title } = useAdvisoryCount();
  const { data } = useData(getAdvisories, []);
  const [inView, setInView] = useState(false);
  const key = jumpKey(user);
  const [pref, setPref] = useState(() => readJumpPref(key));

  // The advisories on screen now, and whether any is one the staff member
  // has not seen since they last retired the pill. A preference saved before
  // `seen` existed has none recorded, so everything counts as new once.
  const current = actionable(data?.advisories).map(adviceKey);
  const seen = new Set(pref.seen ?? []);
  const hasNew = current.some(k => !seen.has(k));
  const wasRetired = pref.dismissed || (pref.uses ?? 0) >= JUMP_RETIRE_AFTER;
  // New advice re-arms it: X and the use count start over for that set.
  const uses = wasRetired && hasNew ? 0 : (pref.uses ?? 0);
  const retired = wasRetired && !hasNew;

  useEffect(() => {
    const el = target.current;
    if (retired || !el || typeof IntersectionObserver === 'undefined') return undefined;
    // "In view" once the section's top edge is well inside the screen.
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting),
      { rootMargin: '0px 0px -35% 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [target, retired]);

  if (count === 0 || retired) return null;

  // Every save records the advisories on screen, so retiring covers exactly
  // the set the staff member has seen.
  const save = next => {
    const full = { ...next, seen: current };
    setPref(full);
    writeJumpPref(key, full);
  };
  const jump = () => {
    target.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    save({ dismissed: false, uses: uses + 1 });
  };
  const usesLeft = JUMP_RETIRE_AFTER - uses;

  return (
    <div className={'advisories-jump' + (inView ? ' is-hidden' : '')} aria-hidden={inView}>
      <div className="advisories-jump__body">
        <button type="button" className="advisories-jump__go" onClick={jump}
                title={`${title}. Hides itself after ${usesLeft} more use${usesLeft === 1 ? '' : 's'}.`}
                tabIndex={inView ? -1 : 0}>
          <span className="advisories-jump__count">{num(count)}</span>
          <span>Event Advisories</span>
        </button>
        <button type="button" className="advisories-jump__close"
                onClick={() => save({ dismissed: true, uses })}
                title="Hide this until there is a new advisory - the Advisories button at the top stays"
                aria-label="Don't show this again" tabIndex={inView ? -1 : 0}>
          <Icon name="close" size={13} stroke={2.4} />
        </button>
      </div>
    </div>
  );
}

/** The advisories over whichever page is open. Following a card's link to
 *  another page closes the dialog on the way, so the page it opens is not
 *  hidden behind it. */
export function AdvisoriesModal({ open, onClose, page, setPage }) {
  const { data } = useData(getAdvisories, []);
  if (!open) return null;

  const first = RELEVANT[page] ?? [];
  const list = data?.advisories ?? [];
  const ordered = [
    ...list.filter(a => first.includes(a.type)),
    ...list.filter(a => !first.includes(a.type)),
  ];
  const go = p => { onClose(); setPage?.(p); };

  return (
    <Modal open onClose={onClose} width={860} title="Advisories"
           subtitle={first.length && ordered.some(a => first.includes(a.type))
             ? 'What to stock, promote or clear out - the ones about this page first.'
             : 'What to stock, promote or clear out, from the latest analytics update.'}>
      <div className="modal-grow">
        <div className="advisories-scroll">
          <AdvisoriesPanel advisories={data && { ...data, advisories: ordered }} setPage={go} />
        </div>
      </div>
    </Modal>
  );
}
