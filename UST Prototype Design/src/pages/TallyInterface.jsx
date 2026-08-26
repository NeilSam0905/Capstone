import { useState, useCallback, useEffect, useRef } from 'react';
import {
  addEntry, addEvent, setStoreClosed, getSellableProducts, getRecentEntries,
  getEntriesByDate, getEventLog, getClosedDates, getMeta, TRANSACTION_TYPES,
  runPipeline, stopPipeline, getPipelineStatus,
} from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import DataTable from '../components/DataTable';
import ErrorBanner from '../components/ErrorBanner';
import Icon from '../components/Icon';
import { num } from '../lib/format';
import brandMark from '../assets/ustore-mark.png';

const TYPE_TONE = { SALE: 'ok', DAMAGED: 'crit', PROMO: 'info', TRANSFER: 'hvl' };

const TYPE_HINT = {
  SALE:     'Units sold to a customer.',
  DAMAGED:  'Units removed as damaged or unsellable.',
  PROMO:    'Units released for a promotion or giveaway.',
  TRANSFER: 'Units moved to another storage location.',
};

const today = () => new Date().toISOString().slice(0, 10);

export default function TallyInterface({ setView }) {
  const [reloadKey, setReloadKey] = useState(0);
  const bump = useCallback(() => setReloadKey(k => k + 1), []);

  const { data: products } = useData(getSellableProducts, [], []);
  const { data: recent, loading: recentLoading } = useData(() => getRecentEntries(25), [reloadKey], []);
  const { data: meta, loading: metaLoading, error: connectionError } = useData(getMeta, []);

  return (
    <div className="tally-wrap">
      <header className="tally-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <img src={brandMark} alt="USTore" className="brand-mark" />
          <div>
            <div style={{ fontWeight: 800, fontSize: 17, letterSpacing: '-.01em' }}>USTore Digital Tally Interface</div>
            <div className="tally-head__sub">Internal Inventory Counting Tool — Non-Transactional</div>
          </div>
        </div>
        <button className="btn btn--gold" onClick={() => setView('dashboard')}>
          View Analytics Dashboard <Icon name="arrow" size={15} />
        </button>
      </header>

      <div className="tally-body">
        {connectionError && <ErrorBanner error={connectionError} />}
        <EntryForm products={products} onSaved={bump} />
        <CalendarControls onSaved={bump} reloadKey={reloadKey} />
        <RecentEntries entries={recent} loading={recentLoading} />
        <ByDate reloadKey={reloadKey} />
        <PipelineFooter meta={meta} loading={metaLoading} />
        <PipelineRunner onFinished={bump} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ entry */

function EntryForm({ products, onSaved }) {
  const [form, setForm] = useState({
    calendar_date: today(), transaction_type: 'SALE', product_id: '', quantity_sold: '',
  });
  const [errors, setErrors] = useState({});
  const [saved, setSaved] = useState(null);

  const selected = products.find(p => p.product_id === Number(form.product_id));
  const set = (key, value) => {
    setForm(f => ({ ...f, [key]: value }));
    setErrors(e => ({ ...e, [key]: undefined }));
    setSaved(null);
  };

  async function submit() {
    const result = await addEntry(form);
    if (!result.ok) { setErrors(result.errors); setSaved(null); return; }
    setErrors({});
    setSaved(result.entry);
    setForm(f => ({ ...f, product_id: '', quantity_sold: '' }));
    onSaved();
  }

  return (
    <div className="card card__pad">
      <div className="card-h"><span className="section-h">Record Inventory Entry</span></div>

      <div className="form-grid">
        <Field label="Date" error={errors.calendar_date}>
          <input type="date" value={form.calendar_date} max={today()}
                 className={errors.calendar_date ? 'is-err' : ''}
                 onChange={e => set('calendar_date', e.target.value)} />
        </Field>

        <Field label="Transaction Type" error={errors.transaction_type} hint={TYPE_HINT[form.transaction_type]}>
          <select value={form.transaction_type}
                  className={errors.transaction_type ? 'is-err' : ''}
                  onChange={e => set('transaction_type', e.target.value)}>
            {TRANSACTION_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>

        <div className="col-2">
          <Field label="Item" error={errors.product_id}>
            <select value={form.product_id}
                    className={errors.product_id ? 'is-err' : ''}
                    onChange={e => set('product_id', e.target.value)}>
              <option value="">— Select an item —</option>
              {products.map(p => (
                <option key={p.product_id} value={p.product_id}>
                  {p.item_name}{p.category !== 'Uncategorised' ? ` (${p.category})` : ''}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Quantity" error={errors.quantity_sold}>
          <input type="number" min="1" step="1" value={form.quantity_sold} placeholder="Enter quantity"
                 className={errors.quantity_sold ? 'is-err' : ''}
                 onChange={e => set('quantity_sold', e.target.value)} />
        </Field>

        <Field label="Supplier">
          <input type="text" readOnly value={selected ? selected.supplier_name : ''}
                 placeholder="Auto-filled on item selection" />
        </Field>
      </div>

      <div className="btn-row" style={{ marginTop: 18 }}>
        <button className="btn btn--ink" onClick={submit}>Record Entry</button>
        {saved ? (
          <span className="ok-text" style={{ fontSize: 12.5 }}>
            <Icon name="check" size={14} />
            Recorded {saved.quantity_sold} × {saved.item_name} ({saved.transaction_type})
          </span>
        ) : (
          <span className="hint">Unit counts only — this tool records what left the shelf, never a payment.</span>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------- closures and events */

function CalendarControls({ onSaved, reloadKey }) {
  const [closureDate, setClosureDate] = useState(today());
  const [event, setEvent] = useState({ calendar_date: today(), event_name: '', event_description: '' });
  const [errors, setErrors] = useState({});
  const [note, setNote] = useState(null);

  const { data: closed } = useData(getClosedDates, [reloadKey], []);
  const { data: events } = useData(getEventLog, [reloadKey], []);

  async function toggleClosed(isClosed) {
    const result = await setStoreClosed(closureDate, isClosed);
    if (!result.ok) { setErrors(result.errors); return; }
    setErrors({});
    setNote(`${closureDate} marked ${isClosed ? 'CLOSED' : 'open'}.`);
    onSaved();
  }

  async function submitEvent() {
    const result = await addEvent(event);
    if (!result.ok) { setErrors(result.errors); return; }
    setErrors({});
    setNote(`Event "${result.event.event_name}" flagged on ${result.event.calendar_date}.`);
    setEvent({ calendar_date: event.calendar_date, event_name: '', event_description: '' });
    onSaved();
  }

  return (
    <>
      <div className="grid-2">
        <div className="card card__pad">
          <div className="card-h" style={{ marginBottom: 4 }}>
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <Icon name="calOff" size={14} /> Store Closure / Suspension
            </span>
          </div>
          <div className="hint" style={{ marginBottom: 14 }}>
            Sets <span className="mono">is_store_closed</span> for a date. Separate from event flagging.
          </div>

          <Field label="Date" error={errors.calendar_date}>
            <input type="date" value={closureDate}
                   onChange={e => { setClosureDate(e.target.value); setNote(null); }} />
          </Field>

          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn--crit btn--sm" onClick={() => toggleClosed(true)}>Mark closed</button>
            <button className="btn btn--ghost btn--sm" onClick={() => toggleClosed(false)}>Mark open</button>
          </div>

          <div className="hint" style={{ marginTop: 14 }}>
            {closed.length === 0
              ? 'No dates flagged closed in this session.'
              : <>Flagged closed: <b style={{ color: 'var(--text-2)' }}>{closed.join(', ')}</b></>}
          </div>
        </div>

        <div className="card card__pad">
          <div className="card-h" style={{ marginBottom: 4 }}>
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <Icon name="calPlus" size={14} /> Flag an Event
            </span>
          </div>
          <div className="hint" style={{ marginBottom: 14 }}>
            Adds an <span className="mono">Event_Log</span> row and marks <span className="mono">is_event_day</span>.
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Field label="Date" error={errors.calendar_date}>
              <input type="date" value={event.calendar_date}
                     onChange={e => setEvent(v => ({ ...v, calendar_date: e.target.value }))} />
            </Field>
            <Field label="Label" error={errors.event_name}>
              <input type="text" value={event.event_name} placeholder="e.g. Paskuhan"
                     className={errors.event_name ? 'is-err' : ''}
                     onChange={e => { setEvent(v => ({ ...v, event_name: e.target.value })); setErrors(x => ({ ...x, event_name: undefined })); }} />
            </Field>
            <Field label="Description">
              <input type="text" value={event.event_description} placeholder="Optional"
                     onChange={e => setEvent(v => ({ ...v, event_description: e.target.value }))} />
            </Field>
          </div>

          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn--gold btn--sm" onClick={submitEvent}>Flag event</button>
          </div>

          <div className="hint" style={{ marginTop: 14 }}>
            {events.length === 0 ? 'No events flagged yet.' : events.slice(0, 3).map(e => (
              <div key={e.local_id ?? e.event_id}>
                <b style={{ color: 'var(--text-2)' }}>{e.calendar_date}</b> — {e.event_name}
              </div>
            ))}
          </div>
        </div>
      </div>
      {note && <div className="ok-text" style={{ fontSize: 12.5 }}><Icon name="check" size={14} />{note}</div>}
    </>
  );
}

/* ------------------------------------------------------------- read views */

function entryKey(e) {
  return e.local_id ? `l${e.local_id}` : `s${e.sale_id}`;
}

const TYPE_CELL = v => <span className={`tag tag--${TYPE_TONE[v] || 'info'}`}>{v}</span>;

function RecentEntries({ entries, loading }) {
  const columns = [
    { key: 'calendar_date', label: 'Date', width: '14%' },
    { key: 'item_name',     label: 'Item', strong: true, truncate: true, width: '30%' },
    { key: 'quantity_sold', label: 'Qty', num: true, strong: true, width: '8%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
    { key: 'transaction_type', label: 'Type', width: '13%', render: TYPE_CELL },
    {
      key: 'is_local', label: 'Origin', width: '11%',
      render: v => v ? <span className="badge-local">this session</span> : <span className="muted">tallied</span>,
    },
  ];
  const rows = entries.map(e => ({ ...e, rowKey: entryKey(e) }));

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Recent Entries</span>
        <span className="hint">newest first · &ldquo;this session&rdquo; rows are local only</span>
      </div>
      {loading ? <Loading /> : <DataTable columns={columns} data={rows} pageSize={10} minWidth={760} />}
    </div>
  );
}

function ByDate({ reloadKey }) {
  const [date, setDate] = useState(today());
  const { data: entries, loading } = useData(() => getEntriesByDate(date), [date, reloadKey], []);
  const total = entries.reduce((s, e) => s + e.quantity_sold, 0);

  const columns = [
    { key: 'item_name',        label: 'Item', strong: true, truncate: true, width: '46%' },
    { key: 'supplier_name',    label: 'Supplier', truncate: true, width: '28%' },
    { key: 'transaction_type', label: 'Type', width: '16%', render: TYPE_CELL },
    { key: 'quantity_sold',    label: 'Qty', num: true, strong: true, width: '10%' },
  ];
  const rows = entries.map(e => ({ ...e, rowKey: entryKey(e) }));

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h">Entries by Date</span>
        <div className="field" style={{ flexDirection: 'row', alignItems: 'center' }}>
          <input type="date" value={date} onChange={e => setDate(e.target.value)} />
        </div>
      </div>
      {loading ? <Loading /> : entries.length === 0 ? (
        <div className="empty">No entries recorded on {date}.</div>
      ) : (
        <>
          <div className="hint" style={{ marginBottom: 8 }}>
            {entries.length} entr{entries.length === 1 ? 'y' : 'ies'} · {num(total)} units
          </div>
          <DataTable columns={columns} data={rows} pageSize={10} minWidth={620} />
        </>
      )}
    </div>
  );
}

function PipelineFooter({ meta, loading }) {
  // Three real states, not two: `meta` is falsy both while the first
  // request is still in flight AND after it's failed - collapsing those
  // into one "disconnected" badge is what flashed a false "disconnected"
  // on every normal page load before the fetch had even resolved.
  const status = meta ? 'connected' : loading ? 'connecting…' : 'disconnected';
  const tone = meta ? 'tag--ok' : loading ? 'tag--warn' : 'tag--crit';
  return (
    <div className="card statusbar">
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="db" size={13} />
        {meta ? (
          <>
            Data source: <b>{meta.source}</b>
            <span className="muted">· {num(meta.fact_sales_rows)} Fact_Sales rows</span>
          </>
        ) : (
          <>Data source: <b>ustore.db</b></>
        )}
      </span>
      <span className={`tag ${tone}`}>{status}</span>
    </div>
  );
}

/* -------------------------------------------------------------- pipeline */

const STEP_TAG = {
  done:      { cls: 'tag--ok',   label: 'done' },
  error:     { cls: 'tag--crit', label: 'failed' },
  skipped:   { cls: 'tag--warn', label: 'skipped' },
  running:   { cls: 'tag--info', label: 'running' },
  cancelled: { cls: 'tag--warn', label: 'stopped' },
  pending:   { cls: '',          label: 'pending' },
};

/** create_schema.py -> step5_prescriptive.py, run and polled from the backend
 *  (see backend/pipeline.py). Always renders its progress bar / step list —
 *  even before the first successful poll — using this idle placeholder, so
 *  the card never silently goes blank if a request fails; every fetch below
 *  is caught and surfaced as `pipelineError` instead of failing silently. */
const IDLE_STEPS = [
  'Build database schema', 'Populate calendar dimension', 'Convert raw tally sheets',
  'Apply vocabulary + supplier mapping', 'Allocate price-grouped rows to SKUs',
  'Load Fact_Sales', 'Classify Fast / Slow / Non-moving', 'Forecast demand (Prophet)',
  'Set supplier lead times', 'Compute ROP / EOQ / safety stock',
].map((label, i) => ({
  id: `idle-${i}`, label, status: 'pending',
  optional: label.includes('Forecast') || label.includes('raw tally sheets'),
}));

function PipelineRunner({ onFinished }) {
  const [pipelineStatus, setPipelineStatus] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [pipelineError, setPipelineError] = useState(null);
  const timerRef = useRef(null);

  const poll = useCallback(async function pollOnce() {
    try {
      const s = await getPipelineStatus();
      setPipelineStatus(s);
      if (s.status === 'running') {
        timerRef.current = setTimeout(pollOnce, 1200);
        return;
      }
      setPipelineError(null);
      if (s.status === 'done') onFinished?.();
    } catch (err) {
      setPipelineError(err.message || 'Lost connection to the backend while polling pipeline status.');
    }
  }, [onFinished]);

  // Pick up an already-running pipeline (e.g. kicked off from another tab).
  useEffect(() => {
    getPipelineStatus()
      .then(s => { setPipelineStatus(s); if (s.status === 'running') poll(); })
      .catch(err => setPipelineError(err.message || 'Could not reach the backend.'));
    return () => clearTimeout(timerRef.current);
  }, [poll]);

  async function start() {
    setPipelineError(null);
    setStarting(true);
    try {
      const res = await runPipeline();
      if (!res.ok) { setPipelineError(res.error || 'Could not start the pipeline.'); return; }
      poll();
    } catch (err) {
      setPipelineError(err.message || 'Could not reach the backend.');
    } finally {
      setStarting(false);
    }
  }

  async function stop() {
    setStopping(true);
    try {
      const res = await stopPipeline();
      if (!res.ok) { setPipelineError(res.error || 'Could not stop the pipeline.'); return; }
      poll();
    } catch (err) {
      setPipelineError(err.message || 'Could not reach the backend.');
    } finally {
      setStopping(false);
    }
  }

  const running = pipelineStatus?.status === 'running';
  const steps = pipelineStatus?.steps?.length ? pipelineStatus.steps : IDLE_STEPS;
  const total = steps.length;
  const settled = steps.filter(s => ['done', 'skipped', 'error', 'cancelled'].includes(s.status)).length;
  const current = steps.find(s => s.status === 'running');
  const pct = total ? Math.round((settled / total) * 100) : 0;
  const failures = steps.filter(s => s.status === 'error' && s.error);

  return (
    <div className="card card__pad">
      <div className="card-h">
        <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <Icon name="zap" size={14} /> Full Pipeline Run
        </span>
      </div>

      <div className="btn-row">
        <button className="btn btn--ink" onClick={start} disabled={running || starting}>
          {running ? 'Running…' : 'Run Full Pipeline'}
        </button>
        <button className="btn btn--crit" onClick={stop} disabled={!running || stopping}>
          {stopping ? 'Stopping…' : 'Stop'}
        </button>
        {pipelineError && <span className="field__err">{pipelineError}</span>}
        {!running && !pipelineError && pipelineStatus?.status === 'done' && (
          <span className="ok-text" style={{ fontSize: 12.5 }}>
            <Icon name="check" size={14} /> Pipeline completed — data refreshed.
          </span>
        )}
        {!running && !pipelineError && pipelineStatus?.status === 'error' && (
          <span style={{ color: 'var(--crit)', fontWeight: 700, fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icon name="xCircle" size={14} /> Stopped on an error — see below.
          </span>
        )}
        {!running && !pipelineError && pipelineStatus?.status === 'cancelled' && (
          <span className="hint" style={{ fontWeight: 700 }}>Run stopped by request.</span>
        )}
      </div>

      <div className="progress" style={{ marginTop: 14 }}>
        <div className="progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="hint" style={{ margin: '8px 0 14px' }}>
        {settled}/{total} steps{current ? ` · running: ${current.label}` : ''}
      </div>

      <div className="pipeline-steps">
        {steps.map(s => (
          <div key={s.id} className="pipeline-step">
            <span className={`pipeline-step__dot pipeline-step__dot--${s.status}`} />
            <span className="pipeline-step__label">
              {s.label}{s.optional ? <span className="muted"> (optional)</span> : null}
            </span>
            {s.status !== 'pending' && (
              <span className={`tag ${STEP_TAG[s.status].cls}`}>{STEP_TAG[s.status].label}</span>
            )}
          </div>
        ))}
      </div>

      {failures.length > 0 && (
        <div className="notice notice--warn" style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>
          {failures.map(s => `${s.label}: ${s.error}`).join('\n\n')}
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- helpers */

function Field({ label, error, hint, children }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {error && <span className="field__err">{error}</span>}
      {!error && hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}
