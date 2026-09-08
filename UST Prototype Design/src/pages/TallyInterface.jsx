import { useState, useCallback, useEffect, useRef } from 'react';
import {
  addEntry, addEvent, setStoreClosed, getSellableProducts, getRecentEntries,
  getEntriesByDate, getEventLog, getClosedDates, getMeta, TRANSACTION_TYPES,
  runPipeline, stopPipeline, getPipelineStatus, getPipelineStaleness,
  getInventoryCounts, saveInventoryCount, deleteInventoryCount,
  getStockPosition, ALL_CATEGORIES, ALL_SUPPLIERS, UNATTRIBUTED, addProduct,
  importInventoryCounts, importTallyEntries,
} from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import DataTable from '../components/DataTable';
import ErrorBanner from '../components/ErrorBanner';
import Icon from '../components/Icon';
import Modal from '../components/Modal';
import { num, usDate, usDateTime, longMonth } from '../lib/format';
import brandMark from '../assets/ustore-mark.png';

const TYPE_TONE = { SALE: 'ok', DAMAGED: 'crit', PROMO: 'info', TRANSFER: 'hvl' };

const TYPE_HINT = {
  SALE:     'Units sold to a customer.',
  DAMAGED:  'Units removed as damaged or unsellable.',
  PROMO:    'Units released for a promotion or giveaway.',
  TRANSFER: 'Units moved to another storage location.',
};

const today = () => new Date().toISOString().slice(0, 10);

/* ------------------------------------------------- shared form helpers */

/** Categories present in the item list, so the dropdown can never offer one
 *  that would filter the list down to nothing. Derived from the products the
 *  form already has rather than fetched from /api/categories, which lists
 *  every category in the catalogue including those with no sellable item. */
function categoriesOf(products) {
  return [...new Set(products.map(p => p.category).filter(Boolean))].sort();
}

/** The item <select> and the category <select> are always paired, and the
 *  category one exists only to shorten the item one. Narrowing the category
 *  must therefore clear an item that is no longer in range — otherwise the
 *  form silently keeps a product_id the user can no longer see. */
function itemsInCategory(products, category) {
  return category === ALL_CATEGORIES ? products : products.filter(p => p.category === category);
}

/** Import a .csv/.xlsx and report what happened to every row.
 *
 *  The server archives the upload into rawdata/ and returns counts plus a
 *  per-row rejection list. Showing those rejections is the whole point: a
 *  silent partial import is the failure mode worth designing against, since
 *  an item name that misses the controlled vocabulary looks identical to one
 *  that was never in the file.
 *
 *  The <input type="file"> is hidden behind its own label — the native
 *  control cannot be styled to match the buttons around it, and it is reset
 *  after every pick so choosing the same file twice still fires onChange. */
function ImportButton({ onImport, onDone, label = 'Import CSV / Excel', hint }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function pick(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      setResult(await onImport(file));
    } catch (err) {
      setResult({ ok: false, error: err.message });
    } finally {
      setBusy(false);
    }
    onDone?.();
  }

  return (
    <>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <input ref={input} type="file" accept=".csv,.xlsx" onChange={pick} style={{ display: 'none' }} />
        <button className="btn btn--ghost btn--sm" disabled={busy}
                onClick={() => input.current?.click()}>
          <Icon name="download" size={13} /> {busy ? 'Importing…' : label}
        </button>
        {hint && <span className="hint">{hint}</span>}
      </span>
      {result && <ImportResult result={result} />}
    </>
  );
}

function ImportResult({ result }) {
  if (!result.ok) {
    return (
      <div className="notice notice--warn" style={{ marginTop: 12 }}>
        <b>Import failed:</b> {result.error || 'Unknown error.'}
      </div>
    );
  }
  const { imported = 0, updated = 0, rejected = [], rejected_total = 0, rows_read = 0 } = result;
  const clean = rejected_total === 0;
  return (
    <div className={`notice notice--${clean ? 'ok' : 'warn'}`} style={{ marginTop: 12 }}>
      <b>{clean ? 'Imported' : 'Imported with problems'}:</b>{' '}
      {num(rows_read)} row{rows_read === 1 ? '' : 's'} read · {num(imported)} added
      {updated > 0 && <> · {num(updated)} updated</>}
      {rejected_total > 0 && <> · <b>{num(rejected_total)} rejected</b></>}
      {result.saved_to && (
        <div className="hint" style={{ marginTop: 4 }}>File archived to rawdata/</div>
      )}
      {rejected.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>
            Show rejected rows ({num(rejected.length)}{rejected_total > rejected.length
              ? ` of ${num(rejected_total)}` : ''})
          </summary>
          <ul className="date-list" style={{ marginTop: 8 }}>
            {rejected.map(r => (
              <li key={r.row}>
                <b>Row {r.row}</b>{r.item ? ` — ${r.item}` : ''}: {r.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export default function TallyInterface({ setView }) {
  const [reloadKey, setReloadKey] = useState(0);
  const bump = useCallback(() => setReloadKey(k => k + 1), []);

  const { data: products } = useData(getSellableProducts, [], []);
  const { data: recent, loading: recentLoading } = useData(() => getRecentEntries(25), [reloadKey], []);
  // Only the error is read now — the connected/disconnected status bar this
  // also fed was removed. The call stays because it is what detects a
  // backend that is not running.
  const { error: connectionError } = useData(getMeta, []);
  // Re-read on every `bump()`: saving an entry, an event or a closure is
  // exactly what makes the analytics stale, and a finished pipeline run is
  // what clears it. Both already call bump().
  const { data: staleness } = useData(getPipelineStaleness, [reloadKey]);

  return (
    <div className="tally-wrap">
      <header className="tally-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <img src={brandMark} alt="USTore" className="brand-mark" />
          <div>
            <div style={{ fontWeight: 800, fontSize: 17, letterSpacing: '-.01em' }}>USTore Digital Tally Interface</div>
            <div className="tally-head__sub">Internal Inventory Tally Tool </div>
          </div>
        </div>
        <button className="btn btn--gold" onClick={() => setView('dashboard')}>
          View Analytics Dashboard <Icon name="arrow" size={15} />
        </button>
      </header>

      <div className="tally-body">
        {connectionError && <ErrorBanner error={connectionError} />}
        <StalenessBanner staleness={staleness} />
        <SalesInventoryTally products={products} onSaved={bump}
                             recent={recent} recentLoading={recentLoading} />
        <MonthlyInventoryCount products={products} onSaved={bump} />
        <ClosureAndEventCards onSaved={bump} reloadKey={reloadKey} />
        <EntriesByDate reloadKey={reloadKey} />
        <FullPipelineRun onChanged={bump} />
      </div>
    </div>
  );
}

/* ------------------------------------------------- Sales Inventory Tally */

function SalesInventoryTally({ products, onSaved, recent, recentLoading }) {
  const [form, setForm] = useState({
    calendar_date: today(), transaction_type: 'SALE', product_id: '', quantity_sold: '',
  });
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [errors, setErrors] = useState({});
  const [saved, setSaved] = useState(null);

  const categories = categoriesOf(products);
  const visible = itemsInCategory(products, category);
  const set = (key, value) => {
    setForm(f => ({ ...f, [key]: value }));
    setErrors(e => ({ ...e, [key]: undefined }));
    setSaved(null);
  };

  /** Narrowing the category drops the selected item if it falls outside the
   *  new range, so the form can never submit a product the list no longer
   *  shows. */
  function setCategoryAndPrune(next) {
    setCategory(next);
    setSaved(null);
    const stillVisible = itemsInCategory(products, next)
      .some(p => p.product_id === Number(form.product_id));
    if (!stillVisible) setForm(f => ({ ...f, product_id: '' }));
  }

  async function submit() {
    const result = await addEntry(form);
    if (!result.ok) { setErrors(result.errors); setSaved(null); return; }
    setErrors({});
    setSaved(result.entry);
    setForm(f => ({ ...f, product_id: '', quantity_sold: '' }));
    onSaved();
  }

  return (
    <div className="card card__pad card--sales-tally">
      <div className="card-h">
        <span className="section-h">Sales Inventory Tally</span>
        <ImportButton onImport={importTallyEntries} onDone={onSaved}
                      hint="Date · Item · Total Quantity" />
      </div>

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

        <Field label="Category" hint={category === ALL_CATEGORIES ? undefined
                 : `${visible.length} item${visible.length === 1 ? '' : 's'} in this category`}>
          <select value={category} onChange={e => setCategoryAndPrune(e.target.value)}>
            <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>

        <div className="col-2">
          <Field label="Item" error={errors.product_id}>
            <select value={form.product_id}
                    className={errors.product_id ? 'is-err' : ''}
                    onChange={e => set('product_id', e.target.value)}>
              <option value="">— Select an item —</option>
              {visible.map(p => (
                <option key={p.product_id} value={p.product_id}>
                  {p.item_name}{p.category !== 'Uncategorised' ? ` (${p.category})` : ''}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Quantity" error={errors.quantity_sold}>
          <input type="number" min="1" step="1" value={form.quantity_sold} placeholder="Enter Quantity"
                 className={errors.quantity_sold ? 'is-err' : ''}
                 onChange={e => set('quantity_sold', e.target.value)} />
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
          <span className="hint"></span>
        )}
      </div>

      {/* Collapsible: the form is what this card is for, and a ten-row table
          under it pushed Monthly Inventory Count off screen. Closed by
          default — <details> keeps the whole thing out of the layout until
          opened, and the summary still carries the counts so it is useful
          shut. Same header shape as Monthly Inventory Count's "Counted in
          <month>" block, so the two cards read the same way. */}
      <details className="collapse" style={{ marginTop: 20 }}>
        <summary>
          <span className="section-h">Recent Entries</span>
          <span className="hint">
            {recent.length === 0 ? 'nothing tallied yet'
              : `${num(recent.length)} most recent · ${num(recent.reduce((s, e) => s + e.quantity_sold, 0))} units`}
          </span>
        </summary>
        <div style={{ marginTop: 12 }}>
          {recentLoading
            ? <Loading />
            : recent.length === 0
              ? <div className="empty">No entries recorded yet.</div>
              : <DataTable columns={RECENT_COLUMNS} data={recent.map(e => ({ ...e, rowKey: entryKey(e) }))}
                           pageSize={10} minWidth={760} />}
        </div>
      </details>
    </div>
  );
}

/* ----------------------------------------------- Monthly Inventory Count */

const thisMonth = () => new Date().toISOString().slice(0, 7);

/** Monthly stock count, entered by whoever does the count.
 *
 *  This is the digital counterpart of the inventory workbook the store already
 *  keeps by hand, and it fills the project's largest data gap: only ~17% of
 *  Fact_Sales rows have any stock signal behind them, and the workbook stops
 *  at its last export month. Counts recorded here take over from the workbook
 *  as soon as they are more recent (see catalog.py's load_current_stock), so
 *  the Stock Status and Reorder screens start showing a real on-hand figure
 *  for items the workbook never covered.
 *
 *  A count is per product per month and REPLACES any earlier figure for that
 *  pair — a recount corrects a count, it does not add to one. The card says so
 *  when it overwrites something rather than silently changing a number.
 *
 *  Zero is a valid, and important, count: it is the store recording that it is
 *  out of an item. */
function MonthlyInventoryCount({ products, onSaved }) {
  const [month, setMonth] = useState(thisMonth());
  const [form, setForm] = useState({ product_id: '', quantity: '', note: '' });
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [errors, setErrors] = useState({});
  const [saved, setSaved] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [stockOpen, setStockOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  const { data, loading } = useData(() => getInventoryCounts(month), [month, reloadKey], null);
  const counts = data?.counts ?? [];

  const categories = categoriesOf(products);
  const visible = itemsInCategory(products, category);

  const set = (key, value) => {
    setForm(f => ({ ...f, [key]: value }));
    setErrors(e => ({ ...e, [key]: undefined }));
    setSaved(null);
  };

  function setCategoryAndPrune(next) {
    setCategory(next);
    setSaved(null);
    const stillVisible = itemsInCategory(products, next)
      .some(p => p.product_id === Number(form.product_id));
    if (!stillVisible) setForm(f => ({ ...f, product_id: '' }));
  }

  async function submit() {
    const result = await saveInventoryCount({ ...form, count_month: month });
    if (!result.ok) { setErrors(result.errors || {}); setSaved(null); return; }
    setErrors({});
    setSaved(result);
    setForm({ product_id: '', quantity: '', note: '' });
    setReloadKey(k => k + 1);
    onSaved?.();
  }

  async function remove(countId) {
    await deleteInventoryCount(countId);
    setSaved(null);
    setReloadKey(k => k + 1);
    onSaved?.();
  }

  const columns = [
    { key: 'item_name', label: 'Item', strong: true, truncate: true, width: '38%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '26%' },
    { key: 'quantity', label: 'On hand', num: true, strong: true, width: '12%' },
    { key: 'date_logged', label: 'Counted', width: '16%', render: usDateTime },
    {
      key: 'count_id', label: '', width: '8%',
      render: id => (
        <button className="btn btn--ghost btn--sm" onClick={() => remove(id)} title="Remove this count">
          Remove
        </button>
      ),
    },
  ];
  const rows = counts.map(c => ({ ...c, rowKey: `ic${c.count_id}` }));

  return (
    <div className="card card__pad card--inventory-count">
      <div className="card-h">
        <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <Icon name="db" size={14} /> Monthly Inventory Count
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {data?.workbook_month && (
            <span className="hint">Latest Inventory Data: {longMonth(data.workbook_month)}</span>
          )}
          <button className="btn btn--ghost btn--sm" onClick={() => setStockOpen(true)}>
            <Icon name="box" size={13} /> Check current inventory
          </button>
          <button className="btn btn--ghost btn--sm" onClick={() => setAddOpen(v => !v)}>
            <Icon name="calPlus" size={13} /> Add New Item
          </button>
          <ImportButton
            onImport={file => importInventoryCounts(file, month)}
            onDone={() => { setReloadKey(k => k + 1); onSaved?.(); }}
            hint={`Item · Units On Hand → ${longMonth(month)}`}
          />
        </span>
      </div>

      <AddItemModal
        open={addOpen}
        products={products}
        onClose={() => setAddOpen(false)}
        onAdded={p => {
          setAddOpen(false);
          setCategory(ALL_CATEGORIES);
          setForm(f => ({ ...f, product_id: String(p.product_id) }));
          onSaved?.();            // refreshes the item list this card was given
        }}
      />

      <div className="form-grid">
        <Field label="Count Month" error={errors.count_month}>
          <input type="month" value={month} max={thisMonth()}
                 className={errors.count_month ? 'is-err' : ''}
                 onChange={e => { setMonth(e.target.value); setSaved(null); }} />
        </Field>

        <Field label="Units on Hand" error={errors.quantity}>
          <input type="number" min="0" step="1" value={form.quantity} placeholder="Enter Units Counted"
                 className={errors.quantity ? 'is-err' : ''}
                 onChange={e => set('quantity', e.target.value)} />
        </Field>

        <Field label="Category" hint={category === ALL_CATEGORIES ? undefined
                 : `${visible.length} item${visible.length === 1 ? '' : 's'} in this category`}>
          <select value={category} onChange={e => setCategoryAndPrune(e.target.value)}>
            <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>

        <div className="col-2">
          <Field label="Item" error={errors.product_id}>
            <select value={form.product_id}
                    className={errors.product_id ? 'is-err' : ''}
                    onChange={e => set('product_id', e.target.value)}>
              <option value="">— Select an item —</option>
              {visible.map(p => (
                <option key={p.product_id} value={p.product_id}>
                  {p.item_name}{p.category !== 'Uncategorised' ? ` (${p.category})` : ''}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="col-2">
          <Field label="Note (optional)">
            <input type="text" value={form.note} placeholder="e.g. Damaged Units Excluded"
                   onChange={e => set('note', e.target.value)} />
          </Field>
        </div>
      </div>

      <div className="btn-row" style={{ marginTop: 18 }}>
        <button className="btn btn--ink" onClick={submit}>Save Count</button>
        {saved && (
          <span className="ok-text" style={{ fontSize: 12.5 }}>
            <Icon name="check" size={14} />
            {saved.replaced != null
              ? `Updated ${saved.count.item_name}: ${num(saved.replaced)} → ${num(saved.count.quantity)} units`
              : `Recorded ${num(saved.count.quantity)} × ${saved.count.item_name}`}
            {' '}for {longMonth(saved.count.count_month)}
          </span>
        )}
      </div>

      {/* Collapsible, matching Recent Entries in Sales Inventory Tally: the
          form is what the card is for, and the table under it pushed the rest
          of the page down. The summary keeps the counts so it stays useful
          shut. Re-keyed on `month` so switching month reopens it — a closed
          section would otherwise hide the fact that the list just changed. */}
      <details className="collapse" style={{ marginTop: 20 }} key={month}>
        <summary>
          <span className="section-h">Counted in {longMonth(month)}</span>
          <span className="hint">
            {counts.length === 0 ? 'nothing counted yet' :
              `${num(counts.length)} item${counts.length === 1 ? '' : 's'} · ${num(data.total_units)} units`}
          </span>
        </summary>
        <div style={{ marginTop: 12 }}>
          {loading
            ? <Loading />
            : counts.length === 0
              ? <div className="empty">No stock counts recorded for {longMonth(month)}.</div>
              : <DataTable columns={columns} data={rows} pageSize={10} minWidth={760} />}
        </div>
      </details>

      <CurrentStockModal open={stockOpen} onClose={() => setStockOpen(false)} />
    </div>
  );
}

/** Create an item the catalogue does not have yet, so today's count is not
 *  blocked on a vocabulary update.
 *
 *  The warning is not decoration. `step1_apply_mapping.py` opens with
 *  `DELETE FROM Dim_Product` and rebuilds it from
 *  `data/vocab_mapping_FINAL_v5.csv`, so an item added here disappears at the
 *  next full pipeline run unless it is also added to that file. Saying so at
 *  the point of creation is the only place a user would ever see it. */
function AddItemModal({ open, products, onClose, onAdded }) {
  const [item, setItem] = useState({ item_name: '', category: '', supplier_name: '' });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  // Clear on open so a cancelled attempt does not reappear half-filled.
  useEffect(() => {
    if (open) { setItem({ item_name: '', category: '', supplier_name: '' }); setErrors({}); }
  }, [open]);

  const categories = categoriesOf(products);
  const suppliers = [...new Set(products.map(p => p.supplier_name).filter(Boolean))].sort();

  async function submit() {
    setBusy(true);
    const result = await addProduct(item);
    setBusy(false);
    if (!result.ok) { setErrors(result.errors || {}); return; }
    setErrors({});
    onAdded(result.product);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add New Item"
      subtitle="Adds the item permanently — to the catalogue, the controlled vocabulary and the inventory source."
      width={620}
    >
      <div className="form-grid">
        <div className="col-2">
          <Field label="Item Name" error={errors.item_name}>
            <input type="text" value={item.item_name} placeholder="e.g. UST Tumbler 500ml (Gold)"
                   className={errors.item_name ? 'is-err' : ''}
                   autoFocus
                   onChange={e => { setItem(v => ({ ...v, item_name: e.target.value }));
                                    setErrors(x => ({ ...x, item_name: undefined })); }} />
          </Field>
        </div>

        {/* Free text with a datalist rather than a <select>: a genuinely new
            item may well belong to a category or supplier the catalogue has
            not seen either, and a closed list would block that. */}
        <Field label="Category">
          <input type="text" list="add-item-categories" value={item.category}
                 placeholder="Uncategorised"
                 onChange={e => setItem(v => ({ ...v, category: e.target.value }))} />
          <datalist id="add-item-categories">
            {categories.map(c => <option key={c} value={c} />)}
          </datalist>
        </Field>

        <Field label="Supplier">
          <input type="text" list="add-item-suppliers" value={item.supplier_name}
                 placeholder="Optional"
                 onChange={e => setItem(v => ({ ...v, supplier_name: e.target.value }))} />
          <datalist id="add-item-suppliers">
            {suppliers.map(s => <option key={s} value={s} />)}
          </datalist>
        </Field>
      </div>

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn btn--ink btn--sm" onClick={submit} disabled={busy}>
          {busy ? 'Adding…' : 'Add Item'}
        </button>
        <button className="btn btn--ghost btn--sm" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** Everything the store currently believes it holds — the same figures the
 *  Stock Status screen reads (/api/stock), which take a count entered above
 *  over the historical workbook as soon as it is more recent.
 *
 *  Read-only and deliberately unfiltered: the question this answers is "what
 *  do we have right now", asked mid-count, so it opens over the form rather
 *  than navigating away from it. Items with no stock record at all are absent
 *  rather than shown as zero — the backend only returns rows where
 *  current_stock is not null, and "we have none" and "we have never counted
 *  this" are different statements. */
function CurrentStockModal({ open, onClose }) {
  // Only fetch once the dialog is actually opened: this is the whole catalogue
  // with derived stock, and the card renders on every visit to the page.
  const { data, loading } = useData(() => (open ? getStockPosition() : Promise.resolve(null)), [open], null);
  const items = data?.items ?? [];

  const [q, setQ] = useState('');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);
  const [stockOnly, setStockOnly] = useState(false);

  // Reset the filters each time it opens: the question this answers is asked
  // fresh ("what do we have right now"), and reopening onto someone's stale
  // search looks like missing data.
  useEffect(() => { if (open) { setQ(''); setCategory(ALL_CATEGORIES); setSupplier(ALL_SUPPLIERS); setStockOnly(false); } }, [open]);

  const categories = [...new Set(items.map(i => i.category || 'Uncategorised'))].sort();
  const suppliers = [...new Set(items.map(i => i.supplier_name || UNATTRIBUTED))].sort();

  const needle = q.trim().toLowerCase();
  const shown = items.filter(i =>
    (!needle || i.item_name.toLowerCase().includes(needle)
      || (i.supplier_name || '').toLowerCase().includes(needle))
    && (category === ALL_CATEGORIES || (i.category || 'Uncategorised') === category)
    && (supplier === ALL_SUPPLIERS || (i.supplier_name || UNATTRIBUTED) === supplier)
    && (!stockOnly || i.current_stock > 0));

  const columns = [
    { key: 'item_name',      label: 'Item', strong: true, truncate: true, width: '34%' },
    { key: 'category',       label: 'Category', truncate: true, width: '18%' },
    { key: 'supplier_name',  label: 'Supplier', truncate: true, width: '22%' },
    { key: 'current_stock',  label: 'On hand', num: true, strong: true, width: '12%', render: num },
    {
      key: 'days_of_supply', label: 'Days left', num: true, width: '14%',
      render: v => v == null ? <span className="muted">—</span> : Math.round(v),
    },
  ];

  const inStock = items.filter(i => i.current_stock > 0);
  const out = items.length - inStock.length;
  const filtered = shown.length !== items.length;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Current inventory"
      subtitle={loading ? 'Loading…' : items.length === 0 ? 'No item has a stock record yet.'
        : `${num(inStock.length)} item${inStock.length === 1 ? '' : 's'} with stock on hand`
          + (out > 0 ? ` · ${num(out)} counted at zero` : '')
          + ` · ${num(data.total - data.covered)} of ${num(data.total)} never counted`}
    >
      {loading ? <Loading /> : items.length === 0
        ? <div className="empty">No stock counts or workbook figures exist yet.</div>
        : (
          <>
            <div className="modal-filters">
              <input
                type="search"
                value={q}
                placeholder="Search Item Or Supplier…"
                onChange={e => setQ(e.target.value)}
                aria-label="Search current inventory"
              />
              <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Filter by category">
                <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <select value={supplier} onChange={e => setSupplier(e.target.value)} aria-label="Filter by supplier">
                <option value={ALL_SUPPLIERS}>{ALL_SUPPLIERS}</option>
                {suppliers.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <label className="check">
                <input type="checkbox" checked={stockOnly} onChange={e => setStockOnly(e.target.checked)} />
                In stock only
              </label>
            </div>

            <div className="hint" style={{ margin: '2px 0 10px' }}>
              {filtered
                ? <>Showing <b>{num(shown.length)}</b> of {num(items.length)} items</>
                : <>Showing all {num(items.length)} items</>}
            </div>

            <div className="modal-grow">
              {shown.length === 0
                ? <div className="empty">No item matches those filters.</div>
                : <DataTable
                    columns={columns}
                    data={shown.map(i => ({
                      ...i,
                      category: i.category || 'Uncategorised',
                      supplier_name: i.supplier_name || UNATTRIBUTED,
                      rowKey: `st${i.product_id}`,
                    }))}
                    pageSize={12}
                    minWidth={720}
                  />}
            </div>
          </>
        )}
    </Modal>
  );
}

/* -------------------------- Store Closure / Suspension · Flag an Event */

function ClosureAndEventCards({ onSaved, reloadKey }) {
  const [closureDate, setClosureDate] = useState(today());
  const [closureReason, setClosureReason] = useState('');
  const [event, setEvent] = useState({ calendar_date: today(), event_name: '', event_description: '' });
  const [errors, setErrors] = useState({});
  const [note, setNote] = useState(null);

  const { data: closed } = useData(getClosedDates, [reloadKey], []);
  const { data: events } = useData(getEventLog, [reloadKey], []);

  async function toggleClosed(isClosed) {
    const result = await setStoreClosed(closureDate, isClosed, closureReason);
    if (!result.ok) { setErrors(result.errors); return; }
    setErrors({});
    setNote(`${closureDate} marked ${isClosed ? 'CLOSED' : 'open'}`
      + (closureReason.trim() ? ` — ${closureReason.trim()}` : '.'));
    setClosureReason('');
    onSaved();
  }

  async function submitEvent() {
    const result = await addEvent(event);
    if (!result.ok) { setErrors(result.errors); return; }
    setErrors({});
    setNote(`Event "${result.event.event_name}" flagged on ${usDate(result.event.calendar_date)}.`);
    setEvent({ calendar_date: event.calendar_date, event_name: '', event_description: '' });
    onSaved();
  }

  return (
    <>
      <div className="grid-2">
        <div className="card card__pad card--store-closure">
          <div className="card-h" style={{ marginBottom: 4 }}>
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <Icon name="calOff" size={14} /> Store Closure / Suspension
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Field label="Date" error={errors.calendar_date}>
              <input type="date" value={closureDate}
                     onChange={e => { setClosureDate(e.target.value); setNote(null); }} />
            </Field>

            {/* Closure_Log.reason has existed in the schema since the table was
                created but had no way in from the interface. Free text and
                optional — a closure is still recorded without one. */}
            <Field label="Reason for Store Closure">
              <input type="text" value={closureReason}
                     placeholder="Optional — e.g. University Holiday, Inventory Day, Typhoon"
                     onChange={e => { setClosureReason(e.target.value); setNote(null); }} />
            </Field>
          </div>

          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn--crit btn--sm" onClick={() => toggleClosed(true)}>Mark closed</button>
            <button className="btn btn--ghost btn--sm" onClick={() => toggleClosed(false)}>Mark open</button>
          </div>

          {/* A scrolling list rather than one comma-joined line: this is every
              date in Dim_Date flagged is_store_closed across 2023-2026, which
              is currently 43 dates and grows with each closure logged. As
              running prose it wrapped into an unreadable paragraph that pushed
              the rest of the card off screen. */}
          <div style={{ marginTop: 14 }}>
            <div className="hint" style={{ marginBottom: 6 }}>
              {closed.length === 0
                ? 'No dates are flagged closed.'
                : <>Flagged closed <span className="muted">({closed.length})</span></>}
            </div>
            {closed.length > 0 && (
              <ul className="date-list">
                {closed.map(d => <li key={d}>{usDate(d)}</li>)}
              </ul>
            )}
          </div>
        </div>

        <div className="card card__pad card--flag-event">
          <div className="card-h" style={{ marginBottom: 4 }}>
            <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <Icon name="calPlus" size={14} /> Flag an Event
            </span>
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
            <button className="btn btn--ink btn--sm" onClick={submitEvent}>Flag event</button>
          </div>

          <div className="hint" style={{ marginTop: 14 }}>
            {events.length === 0 ? 'No events flagged yet.' : events.slice(0, 3).map(e => (
              <div key={e.local_id ?? e.event_id}>
                <b style={{ color: 'var(--text-2)' }}>{usDate(e.calendar_date)}</b> — {e.event_name}
              </div>
            ))}
          </div>
        </div>
      </div>
      {note && <div className="ok-text" style={{ fontSize: 12.5 }}><Icon name="check" size={14} />{note}</div>}
    </>
  );
}

/* ------------------------------------------------------- Entries by Date */

function entryKey(e) {
  return e.local_id ? `l${e.local_id}` : `s${e.sale_id}`;
}

const TYPE_CELL = v => <span className={`tag tag--${TYPE_TONE[v] || 'info'}`}>{v}</span>;

/** Shared by the Recent Entries block inside Sales Inventory Tally. Lifted to
 *  module scope when that block moved into the form card, so the table shape
 *  is defined once. */
const RECENT_COLUMNS = [
  { key: 'calendar_date', label: 'Date', width: '14%', render: usDate },
  { key: 'item_name',     label: 'Item', strong: true, truncate: true, width: '30%' },
  { key: 'quantity_sold', label: 'Qty', num: true, strong: true, width: '8%' },
  { key: 'supplier_name', label: 'Supplier', truncate: true, width: '24%' },
  { key: 'transaction_type', label: 'Type', width: '13%', render: TYPE_CELL },
  {
    key: 'is_local', label: 'Origin', width: '11%',
    render: v => v ? <span className="badge-local">this session</span> : <span className="muted">tallied</span>,
  },
];

function EntriesByDate({ reloadKey }) {
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
    <div className="card card__pad card--entries-by-date">
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


/* ------------------------------------------------------------- staleness */

/** "The numbers on the dashboard are older than what you have typed in."
 *
 *  Everything this screen writes - a tally entry, a flagged event, a store
 *  closure - is in ustore.db the instant it is saved, and the Recent Entries
 *  table below reflects it immediately. The ANALYTICS do not: fsn_class,
 *  Result_Forecast and Result_Prescriptive are only recomputed by a pipeline
 *  run, so without this banner the Reorder Alerts screen will happily show
 *  reorder points computed before a fortnight of tallying, with nothing on
 *  screen saying so.
 *
 *  Deliberately only shown when there is something concrete to point at
 *  (`stale` is false when the pending counts are all zero), so it does not
 *  become a permanent decoration people learn to ignore. */
function StalenessBanner({ staleness }) {
  if (!staleness || !staleness.stale) return null;

  const { pending = {}, never_run: neverRun, running, interrupted, last_run: lastRun } = staleness;
  const parts = [
    [pending.tally_entries, 'tally entry', 'tally entries'],
    [pending.events, 'flagged event', 'flagged events'],
    [pending.closures, 'closure change', 'closure changes'],
  ]
    .filter(([n]) => n > 0)
    .map(([n, one, many]) => `${num(n)} ${n === 1 ? one : many}`);

  const what = parts.length ? parts.join(', ').replace(/, ([^,]*)$/, ' and $1') : 'new records';

  // A run that was stopped or failed part-way is its own kind of stale: no new
  // data is waiting, but the tables are half-rebuilt (step3 may have rewritten
  // fsn_class with step5 never reaching Result_Prescriptive). Say that rather
  // than reporting a pending count of nothing.
  if (interrupted && !running) {
    return (
      <div className="notice notice--warn" style={{ display: 'flex', gap: 10 }}>
        <Icon name="alert" size={16} />
        <div>
          <b>
            The last pipeline run {interrupted === 'cancelled' ? 'was stopped' : 'failed'} part-way —
            re-run it.
          </b>
          <div style={{ marginTop: 4 }}>
            Some steps completed and some did not, so FSN classes, forecasts and reorder points may not
            agree with each other or with the sales data. Run <b>Full Pipeline Run</b> at the bottom of
            this page to bring them back into step.
            {parts.length > 0 && <> There {parts.length === 1 ? 'is' : 'are'} also {what} waiting to be
            folded in.</>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`notice ${running ? 'notice--info' : 'notice--warn'}`} style={{ display: 'flex', gap: 10 }}>
      <Icon name={running ? 'clock' : 'alert'} size={16} />
      <div>
        <b>
          {running
            ? 'Pipeline running — the analytics below are still the previous run’s.'
            : 'Analytics are out of date — re-run the pipeline.'}
        </b>
        <div style={{ marginTop: 4 }}>
          {neverRun ? (
            <>
              {what} {parts.length === 1 && pending.tally_entries === 1 ? 'is' : 'are'} in the database, but no
              pipeline run has been recorded for it yet. FSN classes, forecasts and reorder points on the
              dashboard were not computed from this data.
            </>
          ) : (
            <>
              {what} recorded since the last completed run
              {lastRun?.finished_at ? <> on <span className="mono">{usDateTime(lastRun.finished_at)}</span></> : null}.
              FSN classes, forecasts and reorder points do not include {parts.length > 1 ? 'them' : 'it'} yet.
            </>
          )}
        </div>
        {!running && (
          <div style={{ marginTop: 4 }}>
            Run <b>Full Pipeline Run</b> at the bottom of this page to refresh them.
          </div>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- pipeline */

/** 12.4 -> "12s", 754 -> "12m 34s". Steps that run past a minute (step0 on a
 *  full rawdata/ folder, step3 on a large Fact_Sales) need more than a bare
 *  seconds count to stay readable. */
function dur(seconds) {
  if (seconds == null) return null;
  const s = Math.round(seconds);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

const STEP_TAG = {
  done:      { cls: 'tag--ok',   label: 'done' },
  error:     { cls: 'tag--crit', label: 'failed' },
  skipped:   { cls: 'tag--warn', label: 'skipped' },
  running:   { cls: 'tag--info', label: 'running' },
  cancelled: { cls: 'tag--warn', label: 'stopped' },
  pending:   { cls: '',          label: 'pending' },
  deselected: { cls: '',         label: 'not run' },
};

/** create_schema.py -> step5_prescriptive.py, run and polled from the backend
 *  (see backend/pipeline.py). Always renders its progress bar / step list —
 *  even before the first successful poll — using this idle placeholder, so
 *  the card never silently goes blank if a request fails; every fetch below
 *  is caught and surfaced as `pipelineError` instead of failing silently. */
const IDLE_STEPS = [
  ['Build database schema', 'seconds'],
  ['Populate calendar dimension', 'seconds'],
  ['Convert raw tally sheets', '~1 min'],
  ['Apply vocabulary + supplier mapping', '~10 s'],
  ['Allocate price-grouped rows to SKUs', '~10 s'],
  ['Load Fact_Sales', '~30 s'],
  ['Classify Fast / Slow / Non-moving', '~1 min'],
  ['Forecast demand (rolling mean)', '~10 s'],
  ['Set supplier lead times', '~5 s'],
  ['Compute ROP / EOQ / safety stock', '~10 s'],
].map(([label, estimate], i) => ({
  id: `idle-${i}`, label, estimate, status: 'pending',
  optional: label.includes('Forecast') || label.includes('raw tally sheets'),
}));

/** One failed or skipped step, as a sentence rather than a stack trace.
 *
 *  The backend reduces each failure to a plain-language line (see
 *  pipeline.py's _summarise_error) and keeps the raw stderr in
 *  `error_detail`. Showing the traceback by default meant the commonest
 *  outcome of all - step0 finding no rawdata/ folder, which is not a problem
 *  and does not affect the results - was reported as thirty lines of
 *  interpreter frames ending in a FileNotFoundError. The frames are still one
 *  click away for whoever wants them. */
function StepProblem({ step }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div><b>{step.label}:</b> {step.error}</div>
      {step.error_detail && (
        <details style={{ marginTop: 4 }}>
          <summary className="hint" style={{ cursor: 'pointer' }}>Technical details</summary>
          <pre className="pipeline-log" style={{ marginTop: 6 }}>{step.error_detail}</pre>
        </details>
      )}
    </div>
  );
}

function FullPipelineRun({ onChanged }) {
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
      // Refresh the page's other reads (Recent Entries, and the staleness
      // banner, which a completed run is exactly what clears).
      if (s.status === 'done') onChanged?.();
    } catch (err) {
      setPipelineError(err.message || 'Lost connection to the backend while polling pipeline status.');
    }
  }, [onChanged]);

  // Pick up an already-running pipeline (e.g. kicked off from another tab).
  useEffect(() => {
    getPipelineStatus()
      .then(s => { setPipelineStatus(s); if (s.status === 'running') poll(); })
      .catch(err => setPipelineError(err.message || 'Could not reach the backend.'));
    return () => clearTimeout(timerRef.current);
  }, [poll]);

  async function start(includeForecast) {
    setPipelineError(null);
    setStarting(true);
    try {
      const res = await runPipeline({ includeForecast });
      if (!res.ok) { setPipelineError(res.error || 'Could not start the pipeline.'); return; }
      onChanged?.();  // so the staleness banner switches to its "running" wording
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
  // Deselected steps are excluded from the denominator as well as the
  // numerator, so a forecast-less run reads 9/9 rather than stalling at 9/10.
  const scheduled = steps.filter(s => s.status !== 'deselected');
  const total = scheduled.length;
  const settled = scheduled.filter(s => ['done', 'skipped', 'error', 'cancelled'].includes(s.status)).length;
  const current = steps.find(s => s.status === 'running');
  const pct = total ? Math.round((settled / total) * 100) : 0;
  const failures = steps.filter(s => s.status === 'error' && s.error);
  const skipped = steps.filter(s => s.status === 'skipped' && s.error);
  // The backend streams each step's stdout line by line (backend/pipeline.py
  // runs the scripts with `python -u`), so this is a live tail, not a
  // post-mortem dump. It matters most on step3/step4, which print a line per
  // SKU and are otherwise many silent minutes of an unmoving progress bar.
  const tailStep = current || [...steps].reverse().find(s => s.output);

  return (
    <div className="card card__pad card--pipeline-run">
      <div className="card-h">
        <span className="section-h" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <Icon name="zap" size={14} /> Full Pipeline Run
        </span>
      </div>

      <div className="hint" style={{ marginBottom: 12 }}>
        Rebuilds <span className="mono">ustore.db</span> from the CSVs in <span className="mono">data/</span> and
        recomputes FSN classes, lead times and reorder points. Tally entries, events and closures recorded on
        this screen are preserved and folded back in.
      </div>

      <div className="btn-row">
        <button className="btn btn--ink" onClick={() => start(false)} disabled={running || starting}>
          {running ? 'Running…' : 'Run Pipeline (Without Forecast)'}
        </button>
        <button className="btn" onClick={() => start(true)} disabled={running || starting}>
          {running ? 'Running…' : 'Run Full Pipeline + Forecast'}
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
        {settled}/{total} steps
        {current ? ` · running: ${current.label}${current.duration_s != null ? ` (${dur(current.duration_s)})` : ''}` : ''}
      </div>

      <div className="pipeline-steps">
        {steps.map(s => (
          <div key={s.id} className="pipeline-step">
            <span className={`pipeline-step__dot pipeline-step__dot--${s.status}`} />
            <span className="pipeline-step__label">
              {s.label}{s.optional ? <span className="muted"> (optional)</span> : null}
            </span>
            {s.duration_s != null
              ? <span className="hint mono">{dur(s.duration_s)}</span>
              : s.estimate && <span className="hint">{s.estimate}</span>}
            {s.status !== 'pending' && (
              <span className={`tag ${STEP_TAG[s.status].cls}`}>{STEP_TAG[s.status].label}</span>
            )}
          </div>
        ))}
      </div>

      {tailStep?.output && (
        <div style={{ marginTop: 12 }}>
          <div className="hint" style={{ marginBottom: 6 }}>Output — <b>{tailStep.label}</b></div>
          <pre className="pipeline-log">{tailStep.output}</pre>
        </div>
      )}

      {failures.length > 0 && (
        <div className="notice notice--warn" style={{ marginTop: 12 }}>
          <b>The run stopped here.</b>
          {failures.map(s => <StepProblem key={s.id} step={s} />)}
        </div>
      )}

      {skipped.length > 0 && (
        <div className="notice notice--info" style={{ marginTop: 12 }}>
          <b>
            {skipped.length === 1 ? 'One optional step was' : `${skipped.length} optional steps were`}
            {' '}skipped — the run completed without {skipped.length === 1 ? 'it' : 'them'}.
          </b>
          {skipped.map(s => <StepProblem key={s.id} step={s} />)}
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
