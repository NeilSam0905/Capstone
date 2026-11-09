import { useState } from 'react';
import { getSummaryPeriods, getSummaryReport, summaryReportPdfUrl } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import KPICard from '../components/KPICard';
import Icon from '../components/Icon';
import ActiveThumb from '../components/ActiveThumb';
import SearchSelect from '../components/SearchSelect';
import { peso, num, longMonth, usDate } from '../lib/format';

/* The Summary Report: an academic year, a term or a quarter on a page or two,
 * for the people the store reports to (client consultation, Nov 2026) - how
 * the store did, whether it reached its sales goal, and whether the new items
 * sold. The screen is the PDF's preview: every figure on it comes from
 * backend/summary_report.py, which draws the PDF from the same report.
 *
 * Comparisons are with the same days a year earlier, and only where those
 * days were tallied about as fully as now (the backend decides which). */

const KINDS = [['year', 'Academic Year'], ['term', 'Term'], ['quarter', 'Quarter']];

/* Terms and quarters are picked in two steps, the year from a dropdown and
 * then the part of it from buttons, rather than from one long list: "Special
 * Term" once per academic year, in a single dropdown, read like the same
 * thing repeated. Each part is always shown, greyed out when that year has no
 * sales in it. */
const PARTS = {
  term: [['T1', 'First Term'], ['T2', 'Second Term'], ['ST', 'Special Term']],
  quarter: [['Q1', 'Q1', 'January – March'], ['Q2', 'Q2', 'April – June'],
    ['Q3', 'Q3', 'July – September'], ['Q4', 'Q4', 'October – December']],
};

/** A period's year and part, from its key ('AY2627-T1', '2026-Q3', '2026'). */
function splitPeriod(p) {
  if (p.kind === 'term') return { group: p.key.slice(0, 6), groupLabel: p.label.split(', ')[1], part: p.key.slice(7) };
  if (p.kind === 'quarter') return { group: p.key.slice(0, 4), groupLabel: p.key.slice(0, 4), part: p.key.slice(5) };
  return { group: p.key, groupLabel: p.label.replace('Academic Year', 'A.Y.'), part: null };
}

function Tabs({ label, items, active, onPick }) {
  return (
    <div className="period-tabs" role="tablist" aria-label={label}>
      <ActiveThumb />
      {items.map(({ key, name, disabled, title }) => (
        <button key={key} type="button" role="tab" aria-selected={active === key} disabled={disabled} title={title}
                className={'period-tab' + (active === key ? ' is-active' : '')}
                onClick={() => onPick(key)}>
          {name}
        </button>
      ))}
    </div>
  );
}

/** The year dropdown, newest first, and the buttons for the parts of the year
 *  on show. Moving to another year keeps the part where that year has it,
 *  else opens on the year's latest. */
function PeriodPicker({ kind, options, period, onPick }) {
  const split = options.map(o => ({ ...splitPeriod(o), key: o.key }));
  const current = period ? splitPeriod(period) : null;
  const groups = [...new Map(split.map(s => [s.group, s.groupLabel])).entries()]
    .map(([value, label]) => ({ value, label }));
  const inGroup = split.filter(s => s.group === current?.group);

  function pickGroup(group) {
    const there = split.filter(s => s.group === group);
    onPick((there.find(s => s.part === current?.part) ?? there[0]).key);
  }

  return (
    <>
      <SearchSelect value={current?.group} options={groups} onChange={pickGroup} searchable={false}
                    minWidth={kind === 'quarter' ? 110 : 190} />
      {PARTS[kind] && (
        <Tabs label="Part of the year" active={current?.part}
              items={PARTS[kind].map(([key, name, months]) => {
                const has = inGroup.some(s => s.part === key);
                return { key, name, disabled: !has, title: has ? months : 'No sales recorded in this period' };
              })}
              onPick={part => onPick(inGroup.find(s => s.part === part).key)} />
      )}
    </>
  );
}

/** '+12%' / '−5%' / 'new', with the tone of the change; null when there is
 *  nothing to compare with. */
function change(now, then) {
  if (then == null) return null;
  if (!then) return now ? { text: 'new', tone: 'up' } : null;
  const pct = (now - then) / then * 100;
  if (Math.round(pct) === 0) return { text: '0%', tone: null };
  return { text: `${pct > 0 ? '+' : '−'}${Math.abs(pct).toFixed(0)}%`, tone: pct > 0 ? 'up' : 'down' };
}

function ChangeCell({ now, then }) {
  const c = change(now, then);
  if (!c) return <td className="num"><span className="muted">—</span></td>;
  return <td className={'num change-cell' + (c.tone ? ` is-${c.tone}` : '')}>{c.text}</td>;
}

/** 'October 2026', or 'October 2026 (1–18)' for part of a month. */
function monthLabel(m) {
  const from = +m.from.slice(8), to = +m.to.slice(8);
  const whole = from === 1 && m.to === monthEnd(m.month);
  if (whole) return longMonth(m.month);
  return `${longMonth(m.month)} (${from === to ? from : `${from}–${to}`})`;
}

function monthEnd(month) {
  const [y, mo] = month.split('-').map(Number);
  return new Date(Date.UTC(y, mo, 0)).toISOString().slice(0, 10);
}

const plural = (n, one, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;

/** A section's title with its icon beside it, so the report reads as
 *  distinct parts rather than a stack of alike cards. */
function SectionTitle({ icon, children, extra }) {
  return (
    <div className="card-h">
      <span className="rpt-title">
        <span className="rpt-title__icon"><Icon name={icon} size={17} /></span>
        <span className="section-title">{children}</span>
      </span>
      {extra}
    </div>
  );
}

export default function SummaryReport() {
  const { data: periods, loading: listing } = useData(getSummaryPeriods, [], []);
  const [kind, setKind] = useState('year');
  // The period picked in each kind, so switching Term and back keeps it.
  const [picked, setPicked] = useState({});
  const options = periods.filter(p => p.kind === kind);
  const period = options.find(p => p.key === picked[kind]) ?? options[0] ?? null;
  const { data: report, loading } = useData(
    () => (period ? getSummaryReport(period.kind, period.key) : Promise.resolve(null)),
    [period?.kind, period?.key]);

  // A temporary anchor, as the Batch Sales Report does: the response is an
  // attachment, so this downloads without leaving the page.
  function downloadPdf() {
    const a = document.createElement('a');
    a.href = summaryReportPdfUrl(period.kind, period.key);
    a.download = `USTore_Summary_Report_${period.label.replace(/[^\w]+/g, '_').replace(/^_|_$/g, '')}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const ready = !!period && !!report && !loading;

  return (
    <div className="stack summary-report">
      <div className="card-h" style={{ marginBottom: 0 }}>
        <div className="report-period">
          <span className="section-h">Period</span>
          <div className="period-tabs" role="tablist" aria-label="Kind of period">
            <ActiveThumb />
            {KINDS.map(([key, name]) => (
              <button key={key} type="button" role="tab" aria-selected={kind === key}
                      className={'period-tab' + (kind === key ? ' is-active' : '')}
                      onClick={() => setKind(key)}>
                {name}
              </button>
            ))}
          </div>
          {options.length > 0 && (
            <PeriodPicker kind={kind} options={options} period={period}
                          onPick={key => setPicked(p => ({ ...p, [kind]: key }))} />
          )}
        </div>
        <div className="btn-row">
          <button className="btn btn--ghost" disabled={!ready} title="Open the PDF in a new tab"
                  onClick={() => window.open(summaryReportPdfUrl(period.kind, period.key, { inline: true }),
                    '_blank', 'noopener')}>
            <Icon name="printer" size={14} /> Print Preview
          </button>
          <button className="btn btn--ink" disabled={!ready} onClick={downloadPdf} title="Download the PDF">
            <Icon name="download" size={14} /> Export as PDF
          </button>
        </div>
      </div>

      {listing || (loading && !report) ? <Loading label="Building report…" />
        : !period ? <div className="empty">No sales have been tallied yet.</div>
        : report && <Report report={report} />}
    </div>
  );
}

function Report({ report }) {
  const { period: p, totals: tot, last_year: ly } = report;
  const compare = !!ly?.complete;
  const vs = (now, then, money) => {
    if (!ly) return 'No records a year earlier';
    if (!compare) return "Last year's records incomplete";
    const c = change(now, then);
    return `${c ? c.text : '—'} vs last year (${money ? peso(then) : num(then)})`;
  };

  return (
    <>
      <div className="card card__pad">
        <div className="card-h" style={{ marginBottom: 0, alignItems: 'flex-start' }}>
          <div className="rpt-head__title">
            <span className="rpt-head__icon"><Icon name="file" size={22} /></span>
            <div>
              <div className="section-title" style={{ fontSize: 16 }}>USTore Summary Report</div>
              <div className="hint" style={{ marginTop: 4 }}>
                Sales and new items, for the people the store reports to
              </div>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontWeight: 800, fontSize: 14 }}>{p.label}</div>
            <div className="hint" style={{ marginTop: 3 }}>{p.span}</div>
            <div className="hint" style={{ marginTop: 3 }}>
              {p.records_from ? `Records begin ${usDate(p.records_from)}`
                : p.in_progress ? `In progress: sales to ${usDate(report.sales_through)} (${p.days} of ${p.days_total} days)`
                : 'Complete'}
            </div>
          </div>
        </div>
      </div>

      <div className="summary-tiles">
        <KPICard icon="peso" label="Sales" value={peso(tot.php)}
                 sub={vs(tot.php, ly?.php, true)} />
        <KPICard icon="box" label="Units sold" value={num(tot.units)}
                 sub={vs(tot.units, ly?.units)} />
        <KPICard icon="tag" label="Items that sold" value={num(tot.items_sold)}
                 sub={`of ${num(tot.listed)} on the tally sheet`} />
        <KPICard icon="cal" label="Days with sales" value={num(tot.selling_days)}
                 sub={`of ${num(tot.recorded_days)} days recorded`} />
      </div>
      {ly && (
        <div className="hint" style={{ marginTop: -6 }}>
          {compare
            ? `Last year means the same days a year earlier: ${usDate(ly.from)} – ${usDate(ly.to)}.`
            : `No change is shown against last year: part of ${usDate(ly.from)} – ${usDate(ly.to)} was barely tallied (see Month by Month).`}
        </div>
      )}

      <div className="card card__pad">
        <SectionTitle icon="chart">Month by Month</SectionTitle>
        <div style={{ overflowX: 'auto' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Month</th><th className="num">Units</th><th className="num">Sales</th>
                <th className="num">Last year</th><th className="num">Change</th>
              </tr>
            </thead>
            <tbody>
              {report.months.map(m => (
                <tr key={m.month}>
                  <td className="strong">{monthLabel(m)}</td>
                  {m.recorded_days ? (
                    <>
                      <td className="num">{num(m.units)}</td>
                      <td className="num">{peso(m.php)}</td>
                      {m.last_year_incomplete ? (
                        <>
                          <td className="num">
                            <span className="muted">{m.last_year_recorded_days ? 'incomplete' : 'no records'}</span>
                          </td>
                          <td className="num"></td>
                        </>
                      ) : (
                        <>
                          <td className="num">{m.last_year_php != null ? peso(m.last_year_php) : '—'}</td>
                          <ChangeCell now={m.php} then={m.last_year_php} />
                        </>
                      )}
                    </>
                  ) : (
                    <td colSpan={4}><span className="muted">No records this month</span></td>
                  )}
                </tr>
              ))}
              <tr>
                <td className="strong">Total</td>
                <td className="num strong">{num(tot.units)}</td>
                <td className="num strong">{peso(tot.php)}</td>
                <td className="num strong">{compare ? peso(ly.php) : '—'}</td>
                <ChangeCell now={tot.php} then={compare ? ly.php : null} />
              </tr>
            </tbody>
          </table>
        </div>
        {report.months.some(m => m.recorded_days && m.last_year_incomplete && m.last_year_recorded_days) && (
          <div className="hint" style={{ marginTop: 8 }}>
            Incomplete: far fewer days were tallied in that month a year earlier, so it is not compared.
          </div>
        )}
      </div>

      <div className="grid-2">
        <div className="card card__pad">
          <SectionTitle icon="dashboard">Sales by Category</SectionTitle>
          <div className="report-items">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Category</th><th className="num">Sales</th><th className="num">Share</th>
                  {compare && <th className="num">Change</th>}
                </tr>
              </thead>
              <tbody>
                {report.categories.map(r => (
                  <tr key={r.category}>
                    <td className="strong">{r.category}</td>
                    <td className="num">{peso(r.php)}</td>
                    <td className="num">
                      <span className="rpt-share">
                        <span className="minibar"><span className="minibar__fill" style={{ width: `${r.share * 100}%` }} /></span>
                        {(r.share * 100).toFixed(0)}%
                      </span>
                    </td>
                    {compare && <ChangeCell now={r.php} then={r.last_year_php} />}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card__pad">
          <SectionTitle icon="trend">Top {report.top_items.length} Items by Sales</SectionTitle>
          <div className="report-items">
            <table className="tbl tbl--fixed">
              <colgroup><col style={{ width: 46 }} /><col /><col style={{ width: 70 }} /><col style={{ width: 100 }} /></colgroup>
              <thead>
                <tr><th>#</th><th>Item</th><th className="num">Units</th><th className="num">Sales</th></tr>
              </thead>
              <tbody>
                {report.top_items.map((r, i) => (
                  <tr key={r.product_id}>
                    <td><span className="rpt-rank">{i + 1}</span></td>
                    <td className="strong"><span className="cell-trunc" title={r.item_name}>{r.item_name}</span></td>
                    <td className="num">{num(r.units)}</td>
                    <td className="num">{r.php != null ? peso(r.php) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <NewItems report={report} />

      <div className="card card__pad rpt-notes">
        <div className="rpt-title" style={{ marginBottom: 6 }}>
          <span className="rpt-title__icon"><Icon name="info" size={15} /></span>
          <span className="section-h">How these figures are made</span>
        </div>
        <ul className="hint summary-notes">
          <li>Sales in pesos are units sold times each item&apos;s listed price.</li>
          {tot.unpriced_items > 0 && (
            <li>
              {plural(tot.unpriced_items, 'item')} that sold ({num(tot.unpriced_units)} units)
              {tot.unpriced_items === 1 ? ' has' : ' have'} no price on record, so
              {tot.unpriced_items === 1 ? ' it counts' : ' they count'} in units but not in pesos.
            </li>
          )}
          <li>Figures come from the store&apos;s tally sheets, recorded through {usDate(report.sales_through)}.</li>
          <li>The PDF carries the same figures, with the new items that sold cut to the top ten.</li>
        </ul>
      </div>
    </>
  );
}

/** The items first listed on the tally sheet during the period: which have
 *  not sold - the ones to act on - and which have. */
function NewItems({ report }) {
  const { new_items: n, period: p } = report;
  const notYet = p.in_progress ? 'have not sold yet' : 'did not sell during the period';
  const to = new Date(`${p.to}T00:00:00Z`);
  const daysListed = iso => Math.round((to - new Date(`${iso}T00:00:00Z`)) / 86400000) + 1;

  return (
    <div className="card card__pad">
      <SectionTitle icon="calPlus">New Items</SectionTitle>
      {n.count === 0 ? (
        <div className="hint">No new item appeared on the tally sheet during this period.</div>
      ) : (
        <>
          <div style={{ fontSize: 13, marginBottom: 12 }}>
            <b>{plural(n.count, 'item')}</b> first appeared on the tally sheet during this period.{' '}
            <b>{num(n.sold.length)}</b> sold ({num(n.sold_units)} units, {peso(n.sold_php)});{' '}
            <b style={{ color: n.unsold.length ? 'var(--crit)' : undefined }}>{num(n.unsold.length)}</b> {notYet}.
          </div>
          <div className="grid-2">
            <div>
              <div className="section-h" style={{ marginBottom: 6 }}>Not sold{p.in_progress ? ' yet' : ''}</div>
              {n.unsold.length === 0 ? <div className="hint">Every new item has sold.</div> : (
                <div className="report-items">
                  <table className="tbl tbl--fixed">
                    <colgroup><col /><col style={{ width: 116 }} /><col style={{ width: 64 }} /></colgroup>
                    <thead><tr><th>Item</th><th className="num">First listed</th><th className="num">Days</th></tr></thead>
                    <tbody>
                      {n.unsold.map(r => (
                        <tr key={r.product_id}>
                          <td className="strong">
                            <span className="cell-trunc" title={`${r.item_name} · ${r.category} · ${r.supplier_name ?? ''}`}>
                              {r.item_name}
                            </span>
                          </td>
                          <td className="num">{usDate(r.first_listed)}</td>
                          <td className="num">{daysListed(r.first_listed)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div>
              <div className="section-h" style={{ marginBottom: 6 }}>Sold</div>
              {n.sold.length === 0 ? <div className="hint">None of the new items has sold yet.</div> : (
                <div className="report-items">
                  <table className="tbl tbl--fixed">
                    <colgroup><col /><col style={{ width: 116 }} /><col style={{ width: 64 }} /><col style={{ width: 116 }} /></colgroup>
                    <thead><tr><th>Item</th><th className="num">First sale</th><th className="num">Units</th><th className="num">Sales</th></tr></thead>
                    <tbody>
                      {n.sold.map(r => (
                        <tr key={r.product_id}>
                          <td className="strong"><span className="cell-trunc" title={r.item_name}>{r.item_name}</span></td>
                          <td className="num">{usDate(r.first_sale)}</td>
                          <td className="num">{num(r.units)}</td>
                          <td className="num">{r.php != null ? peso(r.php) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
