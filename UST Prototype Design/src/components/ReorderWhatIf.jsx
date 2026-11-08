import { useEffect, useRef, useState } from 'react';
import Modal from './Modal';
import SearchSelect from './SearchSelect';
import { getReorderWhatIf } from '../services/dataService';
import { num, peso, FSN_LABEL } from '../lib/format';

/**
 * Reorder Alerts' what-if: one item, one or more assumptions changed, the
 * figures now and with the change side by side. Every figure is the
 * backend's (whatif.py), which uses the same formulas as the Reorder table,
 * so with nothing changed the two columns match the table. Nothing is saved.
 *
 * A field left blank keeps the item's current figure, which it shows as its
 * placeholder. The busy-period options carry the lift the backend measured
 * from the store's own sales.
 */
const BLANK = {
  scenario: 'none', scenario_days: '', bulk_units: '',
  lead_time_days: '', stock_on_hand: '', ordering_cost_php: '', holding_cost_php: '',
};
const BUSY = ['enrollment', 'term_start'];

export default function ReorderWhatIf({ item, onClose }) {
  const [form, setForm] = useState(BLANK);
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const pid = item?.product_id;

  useEffect(() => { setForm(BLANK); setResult(null); setErrors({}); setError(null); }, [pid]);

  useEffect(() => {
    if (pid == null) return undefined;
    const t = setTimeout(async () => {
      const mine = ++seq.current;
      const params = { ...form };
      if (params.scenario !== 'bulk') delete params.bulk_units;
      if (!BUSY.includes(params.scenario)) delete params.scenario_days;
      setBusy(true);
      try {
        const r = await getReorderWhatIf(pid, params);
        if (mine !== seq.current) return;
        if (r.ok) { setResult(r); setErrors({}); setError(null); }
        else { setErrors(r.errors || {}); setError(r.errors ? null : r.error); }
      } catch (e) {
        if (mine === seq.current) setError(e.message);
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    }, 350);
    return () => clearTimeout(t);
  }, [pid, form]);

  if (!item) return null;
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const cur = result?.current;
  const presets = Object.fromEntries((result?.presets ?? []).map(p => [p.key, p]));
  const preset = presets[form.scenario];
  const low = item.scenarios?.low_admin_cost ?? {};

  return (
    <Modal open title="Test a change" width={780} onClose={onClose} className="whatif-modal"
           subtitle={`${item.item_name} · ${FSN_LABEL[item.fsn_class] || item.fsn_class}`
             + ` · sells ${(item.avg_daily_demand ?? 0).toFixed(2)} a day`
             + (result?.price_php != null ? ` · ${pesoExact(result.price_php)} each` : '')}>
      <div className="hint" style={{ marginBottom: 14 }}>
        Change any assumption and see what it does to this item. Nothing is saved: the Reorder table keeps
        the real figures. A blank field keeps the current figure.
      </div>

      <div className="form-grid whatif-form">
        <div className="field">
          <label htmlFor="wi-scenario">What is coming</label>
          {/* The app's own dropdown (as on the tally cards), not a native
              <select>, so it matches the fields around it and opens below. */}
          <SearchSelect id="wi-scenario" variant="field" minWidth={0} searchable={false}
                        value={form.scenario} onChange={v => set('scenario', v)}
                        invalid={!!errors.scenario}
                        options={[
                          { value: 'none', label: 'Nothing extra' },
                          ...BUSY.map(k => ({
                            value: k,
                            label: (presets[k]?.label
                              ?? (k === 'enrollment' ? 'Enrollment days' : 'Start of the first term'))
                              + (presets[k]?.lift != null ? ` · ${presets[k].lift.toFixed(2)}× a regular day` : ''),
                          })),
                          { value: 'bulk', label: 'A bulk or organization order' },
                        ]} />
          {errors.scenario && <span className="field__err">{errors.scenario}</span>}
        </div>

        {BUSY.includes(form.scenario) && (
          <NumField id="scenario_days" label="How many busy days" form={form} set={set} errors={errors}
                    placeholder={preset ? String(preset.default_days) : ''} />
        )}
        {form.scenario === 'bulk' && (
          <NumField id="bulk_units" label="Units in the order" form={form} set={set} errors={errors}
                    placeholder="e.g. 50" />
        )}
        {form.scenario === 'none' && <div aria-hidden="true" />}

        <NumField id="lead_time_days" label="Lead time (days)" form={form} set={set} errors={errors}
                  placeholder={String(item.lead_time_days)} />
        <NumField id="stock_on_hand" label="Stock on hand (units)" form={form} set={set} errors={errors}
                  placeholder={item.current_stock != null ? String(item.current_stock) : 'No count yet'} />
        <NumField id="ordering_cost_php" label="Ordering cost (₱ per order, EOQ only)" form={form} set={set}
                  errors={errors} step="any"
                  placeholder={low.ordering_cost_php != null ? String(low.ordering_cost_php) : ''} />
        <NumField id="holding_cost_php" label="Holding cost (₱ per unit per year, EOQ only)" form={form}
                  set={set} errors={errors} step="any"
                  placeholder={low.holding_cost_php_per_unit_year != null
                    ? String(Number(low.holding_cost_php_per_unit_year.toFixed(4))) : ''} />
      </div>

      {error && <div className="field__err" style={{ marginTop: 12 }}>{error}</div>}

      <div className="whatif-results">
        <div className="card-h" style={{ marginBottom: 6 }}>
          <span className="section-h">Now and what if</span>
          <span className="hint">
            {busy && result ? 'Updating…' : ''}
            <button type="button" className="btn btn--ghost btn--sm" style={{ marginLeft: 10 }}
                    onClick={() => setForm(BLANK)} disabled={form === BLANK}>
              Reset
            </button>
          </span>
        </div>
        {!result
          ? <div className="hint">{busy ? 'Working it out…' : ''}</div>
          : <ResultTable cur={cur} next={result.what_if} />}
        {result?.notes?.length > 0 && (
          <ul className="whatif-notes">
            {result.notes.map(n => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>
    </Modal>
  );
}

/** ₱120 or ₱120.50: a price as it is listed. */
const pesoExact = v => `₱${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

function NumField({ id, label, form, set, errors, placeholder, step = '1' }) {
  return (
    <div className="field">
      <label htmlFor={`wi-${id}`}>{label}</label>
      <input id={`wi-${id}`} type="number" min="0" step={step} inputMode="decimal"
             value={form[id]} placeholder={placeholder}
             className={errors[id] ? 'is-err' : ''}
             onChange={e => set(id, e.target.value)} />
      {errors[id] && <span className="field__err">{errors[id]}</span>}
    </div>
  );
}

const units = v => (v == null ? '—' : num(v));
const yesNo = v => (v == null ? '—' : v ? 'Yes' : 'No');

const ROWS = [
  ['Demand during the lead time', 'lead_time_demand', units],
  ['Extra from what is coming', 'extra_units', v => (v ? num(v) : '—')],
  ['Safety stock', 'safety_stock', units],
  ['Reorder point', 'reorder_point', units],
  ['Stock on hand', 'stock_on_hand', units],
  ['Reorder now?', 'needs_reorder', yesNo],
  ['Suggested order (units)', 'suggested_order_qty', units],
  ['Short before a delivery ordered today arrives', 'units_short', units],
  ['Sales at stake', 'php_at_stake', v => (v == null ? '—' : peso(v))],
  ['EOQ', 'eoq', units],
];

function ResultTable({ cur, next }) {
  return (
    <div className="tbl__scroll">
      <table className="tbl whatif-tbl">
        <thead>
          <tr>
            <th>Figure</th>
            <th className="num">Now</th>
            <th className="num">What if</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([label, key, fmt]) => {
            const a = fmt(cur[key]);
            const b = fmt(next[key]);
            const eoqMuted = key === 'eoq';
            return (
              <tr key={key}>
                <td className="strong">
                  {label}
                  {key === 'eoq' && <span className="muted"> · grey when over a year of demand</span>}
                </td>
                <td className="num">
                  <span className={eoqMuted && cur.eoq_exceeds_annual_demand ? 'muted' : undefined}>{a}</span>
                </td>
                <td className="num">
                  <span className={(a !== b ? 'whatif-changed' : '')
                    + (eoqMuted && next.eoq_exceeds_annual_demand ? ' muted' : '')}>{b}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
