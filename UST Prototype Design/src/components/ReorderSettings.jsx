import { useEffect, useRef, useState } from 'react';
import Modal from './Modal';
import SearchSelect from './SearchSelect';
import { getReorderSettings, saveReorderSettings, resetReorderSettings } from '../services/dataService';
import { num, FSN_LABEL } from '../lib/format';

/**
 * Reorder Alerts' "Edit settings": the store sets an item's lead time, safety
 * stock, ordering cost or holding cost, and from then on the Reorder table,
 * the suggested orders and the forecast guidance use them (backend
 * reorder_settings.py). Unlike Test a change (ReorderWhatIf), this is saved.
 *
 * Only the inputs are typed. The reorder point and EOQ are worked out from
 * them by the backend with the same formulas as the calculated figures, and
 * shown beside the calculated ones before saving. A blank field keeps the
 * calculated figure, which it shows as its placeholder. Saving and Back to
 * calculated are the admin's; anyone else is asked for the password.
 */
const FIELDS = ['lead_time_days', 'safety_stock', 'ordering_cost_php', 'holding_cost_php'];
const EMPTY = { lead_time_days: '', safety_stock: '', ordering_cost_php: '', holding_cost_php: '', note: '' };

/** What a saved setting changed, in words: "Lead time 10 days · Safety stock 30". */
function settingSummary(a) {
  const parts = [];
  if (a.lead_time_days != null) parts.push(`Lead time ${num(a.lead_time_days)} days`);
  if (a.safety_stock != null) parts.push(`Safety Stock ${num(Math.round(a.safety_stock))}`);
  if (a.ordering_cost_php != null) parts.push(`Ordering cost ₱${num(a.ordering_cost_php)}`);
  if (a.holding_cost_php != null) parts.push(`Holding cost ₱${Number(a.holding_cost_php).toLocaleString()}`);
  return parts.join(' · ');
}

/** A saved setting as the form's text. */
const asForm = saved => Object.fromEntries(
  [...FIELDS, 'note'].map(k => [k, saved?.[k] == null ? '' : String(saved[k])]));

export default function ReorderSettings({ open, items = [], initialId = null, onClose, onSaved }) {
  // The item is picked in the dialog itself (it opens from the Reorder
  // Settings card, not from a row); `initialId` opens it on one item.
  const [pid, setPid] = useState(initialId);
  useEffect(() => { if (open) setPid(initialId); }, [open, initialId]);
  const item = items.find(i => i.product_id === pid) ?? null;
  const [form, setForm] = useState(EMPTY);
  const [start, setStart] = useState(EMPTY);   // the form as opened, to tell an edit apart
  const [data, setData] = useState(null);
  const [errors, setErrors] = useState({});
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const seq = useRef(0);

  // Open on what is saved, if anything.
  useEffect(() => {
    setData(null); setErrors({}); setError(null); setForm(EMPTY); setStart(EMPTY);
    // Anything still on its way was for the item before.
    const mine = ++seq.current;
    if (pid == null || !open) return;
    getReorderSettings(pid)
      .then(r => {
        if (mine !== seq.current) return;
        if (!r.ok) { setError(r.error); return; }
        const f = asForm(r.saved);
        setForm(f); setStart(f); setData(r);
      })
      .catch(e => { if (mine === seq.current) setError(e.message); });
  }, [pid, open]);

  // The preview follows the form, a moment after typing stops.
  useEffect(() => {
    // Only once this item's own figures have loaded, never on the last one's.
    if (pid == null || data?.product_id !== pid) return undefined;
    const t = setTimeout(async () => {
      const mine = ++seq.current;
      try {
        const params = Object.fromEntries(FIELDS.map(k => [k, form[k]]));
        const r = await getReorderSettings(pid, params);
        if (mine !== seq.current) return;
        if (r.ok) { setData(r); setErrors({}); } else setErrors(r.errors || {});
      } catch (e) {
        if (mine === seq.current) setError(e.message);
      }
    }, 300);
    return () => clearTimeout(t);
    // `data` is only the gate for the first fetch; re-running on it would loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, form]);

  if (!open) return null;
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const picker = (
    <div className="field" style={{ marginBottom: 14 }}>
      <label htmlFor="rs-item">Item</label>
      <SearchSelect id="rs-item" variant="field" minWidth={0} value={pid ?? ''} onChange={setPid}
                    emptyLabel="Select an Item" placeholder="Search items…"
                    options={items.map(i => ({
                      value: i.product_id,
                      label: i.item_name + (i.adjusted ? ' · Adjusted' : ''),
                    }))} />
    </div>
  );
  if (!item) {
    const adjusted = items.filter(i => i.adjusted);
    return (
      <Modal open title="Edit reordering settings" width={780} onClose={onClose} className="whatif-modal"
             subtitle="Set an item's lead time, Safety Stock or costs. Saved, unlike What if.">
        {picker}
        {/* Also the room the item list opens into: the dialog's body scrolls. */}
        <div style={{ minHeight: 300 }}>
          <div className="hint" style={{ marginBottom: 14 }}>
            Pick an item to set the figures the store knows better than the calculation. The Reorder Point
            and EOQ are worked out again from them. Only items with a Reorder Point can be set.
          </div>
          <div className="card-h" style={{ marginBottom: 6 }}>
            <span className="section-h">Items using saved settings</span>
            <span className="count-pill"><b>{num(adjusted.length)}</b></span>
          </div>
          {adjusted.length === 0
            ? <div className="hint">None yet. Every figure on the Reorder page is calculated.</div>
            : (
              <div className="tbl__scroll">
                <table className="tbl whatif-tbl">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Saved settings</th>
                      <th className="num">Reorder Point</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {adjusted.map(i => (
                      <tr key={i.product_id}>
                        <td className="strong">{i.item_name}</td>
                        <td title={i.adjusted.note || undefined}>{settingSummary(i.adjusted)}</td>
                        <td className="num">
                          <span className="muted">{num(Math.round(i.calculated?.reorder_point ?? 0))} → </span>
                          <b>{num(Math.round(i.reorder_point))}</b>
                        </td>
                        <td className="num">
                          <button type="button" className="linkish" onClick={() => setPid(i.product_id)}>
                            Edit
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </Modal>
    );
  }
  const calc = data?.calculated;
  const changed = JSON.stringify(form) !== JSON.stringify(start);
  const anySet = FIELDS.some(k => form[k] !== '');

  async function save() {
    setSaving(true); setError(null);
    try {
      const r = await saveReorderSettings(pid, form);
      if (!r.ok) {
        if (r.errors) setErrors(r.errors); else setError(r.error);
        return;
      }
      onSaved?.(`${item.item_name}: settings saved. The Reorder table now uses them.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setSaving(true); setError(null);
    try {
      const r = await resetReorderSettings(pid);
      if (!r.ok) { setError(r.error); return; }
      onSaved?.(`${item.item_name} is back to its calculated figures.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const ph = (k, digits = 0) => (calc?.[k] == null ? ''
    : String(Number(calc[k].toFixed(digits))));

  return (
    <Modal open title="Edit reordering settings" width={780} onClose={onClose} className="whatif-modal"
           subtitle={`${item.item_name} · ${FSN_LABEL[item.fsn_class] || item.fsn_class}`
             + ` · sells ${(item.avg_daily_demand ?? 0).toFixed(2)} a day`}>
      {picker}
      <div className="hint" style={{ marginBottom: 14 }}>
        Set the figures the store knows better than the calculation. They are <b>saved</b>: the Reorder
        table, the suggested orders and the forecast use them, through every Run Analytics, until you click
        Back to calculated. Leave a field blank to keep the calculated figure.
      </div>

      {data?.saved && (
        <div className="notice notice--info" style={{ marginBottom: 14 }}>
          Saved settings in use{data.saved.changed_by ? ` · set by ${data.saved.changed_by}` : ''}
          {data.saved.changed_at ? ` on ${data.saved.changed_at.slice(0, 10)}` : ''}
          {data.saved.note ? <> · “{data.saved.note}”</> : null}
        </div>
      )}

      <div className="form-grid whatif-form">
        <NumField id="lead_time_days" label="Lead time (days)" form={form} set={set} errors={errors}
                  placeholder={ph('lead_time_days')}
                  hint="Days from ordering to the delivery arriving." />
        <NumField id="safety_stock" label="Safety Stock (units)" form={form} set={set} errors={errors}
                  step="any" placeholder={ph('safety_stock')}
                  hint="Extra kept for a slow delivery or a busy week." />
        <NumField id="ordering_cost_php" label="Ordering cost (₱ per order)" form={form} set={set}
                  errors={errors} step="any" placeholder={ph('ordering_cost_php', 2)}
                  hint="Used for EOQ only." />
        <NumField id="holding_cost_php" label="Holding cost (₱ per unit per year)" form={form} set={set}
                  errors={errors} step="any" placeholder={ph('holding_cost_php', 4)}
                  hint="Used for EOQ only." />
        <div className="field col-2">
          <label htmlFor="rs-note">Reason (optional)</label>
          <input id="rs-note" type="text" maxLength={300} value={form.note}
                 placeholder="e.g. Supplier confirmed 10-day delivery"
                 className={errors.note ? 'is-err' : ''} onChange={e => set('note', e.target.value)} />
          {errors.note && <span className="field__err">{errors.note}</span>}
        </div>
      </div>

      {error && <div className="field__err" style={{ marginTop: 12 }}>{error}</div>}

      <div className="whatif-results">
        <div className="card-h" style={{ marginBottom: 6 }}>
          <span className="section-h">Calculated and with your settings</span>
        </div>
        {!data
          ? <div className="hint">{error ? '' : 'Loading…'}</div>
          : <Compare calc={data.calculated} next={data.preview} />}
        {form.lead_time_days !== '' && form.safety_stock === '' && (
          <ul className="whatif-notes">
            <li>A new lead time with no Safety Stock of its own scales the buffer with the square root of the
              lead time. Type a Safety Stock to set it exactly.</li>
          </ul>
        )}
      </div>

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button type="button" className="btn btn--ink btn--sm" onClick={save}
                disabled={saving || !changed || !anySet || Object.keys(errors).length > 0}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={saving}>
          Cancel
        </button>
        {data?.saved && (
          <button type="button" className="btn btn--crit btn--sm" style={{ marginLeft: 'auto' }}
                  onClick={reset} disabled={saving}
                  title="Remove the saved settings and use the calculated figures again">
            Back to calculated
          </button>
        )}
      </div>
    </Modal>
  );
}

function NumField({ id, label, form, set, errors, placeholder, hint, step = '1' }) {
  return (
    <div className="field">
      <label htmlFor={`rs-${id}`}>{label}</label>
      <input id={`rs-${id}`} type="number" min="0" step={step} inputMode="decimal"
             value={form[id]} placeholder={placeholder}
             className={errors[id] ? 'is-err' : ''}
             onChange={e => set(id, e.target.value)} />
      {errors[id]
        ? <span className="field__err">{errors[id]}</span>
        : hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

const units = v => (v == null ? '—' : num(Math.round(v)));
const ROWS = [
  ['Lead time (days)', 'lead_time_days', v => (v == null ? '—' : num(v))],
  ['Demand during the lead time', 'lead_time_demand', units],
  ['Safety Stock', 'safety_stock', units],
  ['Reorder Point', 'reorder_point', units],
  ['EOQ', 'eoq', units],
  ['Stock on hand', 'stock_on_hand', units],
  ['Reorder now?', 'needs_reorder', v => (v == null ? '—' : v ? 'Yes' : 'No')],
  ['Suggested order (units)', 'suggested_order_qty', units],
];

function Compare({ calc, next }) {
  return (
    <div className="tbl__scroll">
      <table className="tbl whatif-tbl">
        <thead>
          <tr>
            <th>Figure</th>
            <th className="num">Calculated</th>
            <th className="num">With your settings</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([label, key, fmt]) => {
            const a = fmt(calc[key]);
            const b = fmt(next[key]);
            return (
              <tr key={key}>
                <td className="strong">
                  {label}
                  {key === 'eoq' && <span className="muted"> · grey when over a year of demand</span>}
                </td>
                <td className="num">
                  <span className={key === 'eoq' && calc.eoq_exceeds_annual_demand ? 'muted' : undefined}>{a}</span>
                </td>
                <td className="num">
                  <span className={(a !== b ? 'whatif-changed' : '')
                    + (key === 'eoq' && next.eoq_exceeds_annual_demand ? ' muted' : '')}>{b}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
