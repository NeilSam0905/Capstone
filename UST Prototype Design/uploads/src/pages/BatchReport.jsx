import { useState } from 'react';
import { getBatchReport, getMonths, getMeta } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import Icon from '../components/Icon';
import { peso, num, longMonth } from '../lib/format';

export default function BatchReport() {
  const { data: months } = useData(getMonths, [], []);
  const { data: meta } = useData(getMeta, []);
  const [month, setMonth] = useState(null);
  const selected = month ?? months[months.length - 1] ?? null;

  const { data: report, loading } = useData(
    () => (selected ? getBatchReport(selected) : Promise.resolve([])),
    [selected],
    []
  );

  const grandTotal = report.reduce((s, r) => s + r.subtotal, 0);
  const unpricedUnits = report.reduce((s, r) => s + r.unpriced_units, 0);

  return (
    <div className="stack">
      {/* Controls */}
      <div className="card-h" style={{ marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="section-h">Month</span>
          <div className="filter">
            <select value={selected ?? ''} onChange={e => setMonth(e.target.value)} style={{ minWidth: 170 }}>
              {months.map(m => <option key={m} value={m}>{longMonth(m)}</option>)}
            </select>
            <span className="filter__chev">▾</span>
          </div>
        </div>
        <div className="btn-row">
          {/* TODO: backend — export/print are Phase 3 (server-rendered PDF) */}
          <button className="btn btn--ghost" disabled title="PDF export arrives with the Phase 3 backend">
            <Icon name="printer" size={14} /> Print Preview
          </button>
          <button className="btn btn--ink" disabled title="PDF export arrives with the Phase 3 backend">
            <Icon name="download" size={14} /> Export as PDF
          </button>
        </div>
      </div>

      {/* Report header */}
      <div className="card card__pad">
        <div className="card-h" style={{ marginBottom: 0, alignItems: 'flex-start' }}>
          <div>
            <div className="section-title" style={{ fontSize: 16 }}>USTore Monthly Batch Sales Report</div>
            <div className="hint" style={{ marginTop: 4 }}>
              For UST Purchasing Office and Finance Department — internal supplier remittance reference
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="hint">Period</div>
            <div style={{ fontWeight: 800, fontSize: 14 }}>{selected ? longMonth(selected) : '—'}</div>
            {meta && <div className="hint" style={{ marginTop: 3 }}>Fixtures generated: {meta.generated_at}</div>}
          </div>
        </div>
      </div>

      {loading ? <Loading label="Building report…" /> : (
        <>
          {unpricedUnits > 0 && (
            <div className="notice notice--warn">
              <b>{num(unpricedUnits)} units</b> in this period belong to items with no unit price on record, so they are
              counted but carry no line total. Subtotals below cover priced items only.
            </div>
          )}

          {report.map(({ supplier, items, subtotal, unpriced_units }) => (
            <div key={supplier} className="card" style={{ overflow: 'hidden' }}>
              <div className="report-supplier"><span>{supplier}</span></div>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th className="num">Quantity</th>
                    <th className="num">Unit Price</th>
                    <th className="num">Line Total</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, i) => (
                    <tr key={i}>
                      <td className="strong">{item.item_name}</td>
                      <td className="num">{num(item.quantity)}</td>
                      <td className="num">{item.unit_price_php != null ? peso(item.unit_price_php) : <span className="muted">no price</span>}</td>
                      <td className="num strong">{item.line_total != null ? peso(item.line_total) : <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td className="num subtotal" colSpan={3}>
                      Subtotal — {supplier}
                      {unpriced_units > 0 && <span className="muted" style={{ fontWeight: 500 }}> · {num(unpriced_units)} unpriced units excluded</span>}
                    </td>
                    <td className="num subtotal">{peso(subtotal)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          ))}

          <div className="report-total">
            <span className="report-total__label">Grand Total — All Suppliers</span>
            <span className="report-total__val">{peso(grandTotal)}</span>
          </div>

          <div className="card card__pad">
            <div className="card-h" style={{ marginBottom: 0, alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {/* TODO: backend — Phase 3 serves this straight from the star schema */}
                <div className="hint" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Icon name="file" size={12} /> Source: local fixtures generated from the SQLite star schema
                </div>
                <div className="hint">Period: {selected ? longMonth(selected) : '—'}</div>
                <div className="hint">Prepared for: UST Purchasing Office / Finance Department</div>
              </div>
              <div className="hint" style={{ textAlign: 'right' }}>
                <div>Suppliers: {report.length}</div>
                <div>Line items: {report.reduce((s, r) => s + r.items.length, 0)}</div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
