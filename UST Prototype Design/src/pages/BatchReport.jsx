import { useState } from 'react';
import { getBatchReport, getMonths, getMeta, batchReportPdfUrl, batchReportCsvUrl, batchReportXlsxUrl } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import Icon from '../components/Icon';
import { peso, num, longMonth, usDate } from '../lib/format';

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

  const unpricedItems = report.reduce(
    (s, r) => s + r.items.filter(i => i.unit_price_php == null).length, 0
  );
  // Subtotals and the grand total are unit counts, not peso figures. This
  // is an inventory counting tool; unit prices appear only as per-item
  // reference data for supplier remittance.
  const grandUnits = report.reduce((s, r) => s + r.total_units, 0);

  // A temporary anchor rather than assigning location: the response carries
  // Content-Disposition: attachment, so this downloads without navigating
  // the app away and losing the current filter state. Shared by all three
  // export formats — they differ only in the URL and the filename.
  function download(url, filename) {
    if (!selected) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  const downloadPdf = () => download(batchReportPdfUrl(selected), `USTore_Batch_Sales_Report_${selected}.pdf`);
  const downloadCsv = () => download(batchReportCsvUrl(selected), `USTore_Batch_Sales_Report_${selected}.csv`);
  const downloadXlsx = () => download(batchReportXlsxUrl(selected), `USTore_Batch_Sales_Report_${selected}.xlsx`);

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
          {/* Both hit the same server-rendered PDF (backend/batch_pdf.py, fpdf2
             — pure Python, no native dependency, which is what had blocked
             this). Preview opens it inline in the browser's PDF viewer;
             Export sends it as a download. Rendering server-side rather than
             printing the DOM means the document is identical however it was
             produced, and carries its own header, page numbers and signature
             block. */}
          <button
            className="btn btn--ghost"
            disabled={!selected || loading}
            onClick={() => window.open(batchReportPdfUrl(selected, { inline: true }), '_blank', 'noopener')}
            title="Open the PDF in a new tab"
          >
            <Icon name="printer" size={14} /> Print Preview
          </button>
          <button
            className="btn btn--ghost"
            disabled={!selected || loading}
            onClick={downloadCsv}
            title="Download as CSV"
          >
            <Icon name="download" size={14} /> Export as CSV
          </button>
          <button
            className="btn btn--ghost"
            disabled={!selected || loading}
            onClick={downloadXlsx}
            title="Download as Excel (.xlsx)"
          >
            <Icon name="download" size={14} /> Export as Excel
          </button>
          <button
            className="btn btn--ink"
            disabled={!selected || loading}
            onClick={downloadPdf}
            title="Download the PDF"
          >
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
            {meta && <div className="hint" style={{ marginTop: 3 }}>As of: {usDate(meta.generated_at)}</div>}
          </div>
        </div>
      </div>

      {loading ? <Loading label="Building report…" /> : (
        <>
          {unpricedItems > 0 && (
            <div className="notice notice--warn">
              <b>{num(unpricedItems)} line item{unpricedItems === 1 ? '' : 's'}</b> in this period have no item price on
              record, shown as “—” below (no Item Price, no For Remittance figure). Quantities and unit-count subtotals
              are unaffected.
            </div>
          )}

          {report.map(({ supplier, items, total_units }) => (
            <div key={supplier} className="card" style={{ overflow: 'hidden' }}>
              <div className="report-supplier"><span>{supplier}</span></div>
              {/* caps at 10 visible rows and scrolls; the header stays put */}
              <div className="report-items">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th className="num">Quantity</th>
                      <th className="num">Item Price</th>
                      <th className="num">For Remittance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(item => (
                      <tr key={item.item_name}>
                        <td className="strong">
                          <span className="cell-trunc" style={{ '--trunc': '380px' }} title={item.item_name}>
                            {item.item_name}
                          </span>
                        </td>
                        <td className="num">{num(item.quantity)}</td>
                        <td className="num">
                          {item.unit_price_php != null
                            ? peso(item.unit_price_php)
                            : <span className="muted" title="No unit price on record for this item">—</span>}
                        </td>
                        <td className="num">
                          {item.line_total != null
                            ? <span className="strong">{peso(item.line_total)}</span>
                            : <span className="muted">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="report-subtotal">
                <span className="report-subtotal__label">
                  Subtotal — {supplier}
                  <span className="report-subtotal__note"> · {items.length} line item{items.length === 1 ? '' : 's'}</span>
                </span>
                <span className="report-subtotal__val">{num(total_units)} units</span>
              </div>
            </div>
          ))}

          <div className="report-total">
            <span className="report-total__label">Grand Total Units Sold — All Suppliers</span>
            <span className="report-total__val">{num(grandUnits)} units</span>
          </div>

          <div className="card card__pad">
            <div className="card-h" style={{ marginBottom: 0, alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div className="hint" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Icon name="file" size={12} /> Source: {meta?.source ?? 'ustore.db'} (live API)
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
