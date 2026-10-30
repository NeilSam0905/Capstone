import { useState } from 'react';
import { getBatchReport, getMonths, getMeta, batchReportPdfUrl, batchReportCsvUrl, batchReportXlsxUrl } from '../services/dataService';
import useData from '../hooks/useData';
import { Loading } from '../components/Pending';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
import { peso, num, longMonth, usDate } from '../lib/format';
import { PERIOD_KINDS, periodParams, periodLabel, periodSlug } from '../lib/reportPeriod';
import { ALL_SUPPLIERS } from '../services/dataService';

/** `period` / `setPeriod` come from App: the topbar's supplier list is scoped
 *  to the period on show, so the two have to read the same value. `period` is
 *  { kind: 'day' | 'week' | 'month' | 'year', month, date, year }
 *  (lib/reportPeriod.js), and setPeriod merges a change into it, so switching
 *  Day / Week / Month / Year keeps the day, month and year already picked. */
export default function BatchReport({ filters, period, setPeriod }) {
  const { data: months } = useData(getMonths, [], []);
  const { data: meta } = useData(getMeta, []);
  const years = [...new Set(months.map(m => m.slice(0, 4)))];
  const selected = periodParams(period);
  const label = periodLabel(selected);
  const [firstSale, lastSale] = meta?.sales_span ?? [];

  // The topbar's supplier, or undefined for all of them. It is sent to the
  // server rather than applied to the response so the three exports below
  // carry the same cut as the table — see build_batch_report() in app.py.
  const supplier = filters?.supplier && filters.supplier !== ALL_SUPPLIERS
    ? filters.supplier
    : undefined;

  const { data: report, loading } = useData(
    () => (selected ? getBatchReport(selected, supplier) : Promise.resolve([])),
    [selected?.year, selected?.month, selected?.start, selected?.end, supplier],
    []
  );

  // The lines with no unit price on record, with their supplier - listed in
  // a tip opened from the notice's count.
  const unpriced = report.flatMap(r => r.items
    .filter(i => i.unit_price_php == null)
    .map(i => ({ ...i, supplier: r.supplier })));
  const unpricedItems = unpriced.length;
  // The "no item price" notice closes with its X for the report on show; a
  // different period or supplier brings it back if it applies there too.
  const [closedNotice, setClosedNotice] = useState(null);
  const noticeKey = `${periodSlug(selected)}|${supplier ?? ''}`;
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
  // Filtered exports get the supplier in the filename too, so a folder of
  // downloads does not end up with several different reports under one name.
  const stem = `USTore_Batch_Sales_Report_${periodSlug(selected)}`
    + (supplier ? `_${supplier.replace(/[^\w]+/g, '_').replace(/^_|_$/g, '')}` : '');
  const downloadPdf = () => download(batchReportPdfUrl(selected, { supplier }), `${stem}.pdf`);
  const downloadCsv = () => download(batchReportCsvUrl(selected, supplier), `${stem}.csv`);
  const downloadXlsx = () => download(batchReportXlsxUrl(selected, supplier), `${stem}.xlsx`);

  return (
    <div className="stack">
      {/* Controls */}
      <div className="card-h" style={{ marginBottom: 0 }}>
        <div className="report-period">
          <span className="section-h">Period</span>
          <div className="period-tabs" role="tablist" aria-label="Report period">
            {PERIOD_KINDS.map(([key, name]) => (
              <button key={key} type="button" role="tab" aria-selected={period.kind === key}
                      className={'period-tab' + (period.kind === key ? ' is-active' : '')}
                      onClick={() => setPeriod({ kind: key })}>
                {name}
              </button>
            ))}
          </div>
          {period.kind === 'year' ? (
            <div className="filter">
              <select value={period.year ?? ''} onChange={e => setPeriod({ year: e.target.value })}
                      aria-label="Year" style={{ minWidth: 110 }}>
                {years.map(y => <option key={y} value={y}>{y}</option>)}
              </select>
              <span className="filter__chev">▾</span>
            </div>
          ) : period.kind === 'month' ? (
            <div className="filter">
              <select value={period.month ?? ''} onChange={e => setPeriod({ month: e.target.value })}
                      aria-label="Month" style={{ minWidth: 170 }}>
                {months.map(m => <option key={m} value={m}>{longMonth(m)}</option>)}
              </select>
              <span className="filter__chev">▾</span>
            </div>
          ) : (
            // In Week, the day picked starts the week: it and the six days
            // after it, named beside the picker.
            <div className="filter">
              <input type="date" value={period.date ?? ''} min={firstSale} max={lastSale}
                     aria-label={period.kind === 'week' ? 'First day of the week' : 'Day'}
                     onChange={e => e.target.value && setPeriod({ date: e.target.value })} />
            </div>
          )}
          {period.kind === 'week' && selected && (
            <span className="hint">7 days, {label}</span>
          )}
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
            onClick={() => window.open(batchReportPdfUrl(selected, { inline: true, supplier }), '_blank', 'noopener')}
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
            <div className="section-title" style={{ fontSize: 16 }}>USTore Batch Sales Report</div>
            <div className="hint" style={{ marginTop: 4 }}>For UST Purchasing Office and Finance Department</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="hint">Period</div>
            <div style={{ fontWeight: 800, fontSize: 14 }}>{label}</div>
            {meta && <div className="hint" style={{ marginTop: 3 }}>As of: {usDate(meta.generated_at)}</div>}
          </div>
        </div>
      </div>

      {loading ? <Loading label="Building report…" /> : report.length === 0 ? (
        <div className="empty">
          {supplier
            ? `${supplier} recorded no sales for ${selected ? label : 'this period'}.`
            : `No sales were recorded for ${selected ? label : 'this period'}.`}
        </div>
      ) : (
        <>
          {unpricedItems > 0 && closedNotice !== noticeKey && (
            <div className="notice notice--warn notice--closable">
              <button type="button" className="notice__close" onClick={() => setClosedNotice(noticeKey)}
                      aria-label="Close this notice" title="Close">
                <Icon name="close" size={14} />
              </button>
              <InfoTip label="Items with no price" width={460}
                       link={`${num(unpricedItems)} Item${unpricedItems === 1 ? '' : 's'}`}>
                <div className="infotip__h">No item price on record</div>
                <p>{label}{supplier ? ` · ${supplier}` : ''}: shown as “—” in Item Price and For Remittance.</p>
                <div className="infotip__list-head infotip__list--wide">
                  <span>Item</span><span>Supplier</span><span>Units</span>
                </div>
                <ul className="infotip__list infotip__list--wide">
                  {unpriced.map(i => (
                    <li key={`${i.supplier}|${i.product_id ?? i.item_name}`}>
                      <span className="infotip__list-name" title={i.item_name}>{i.item_name}</span>
                      <span className="infotip__list-name" title={i.supplier}>{i.supplier}</span>
                      <span className="infotip__list-num">{num(i.quantity)}</span>
                    </li>
                  ))}
                </ul>
              </InfoTip> in this period
              {unpricedItems === 1 ? ' has' : ' have'} no item price on record, it is shown as “—” below.
            </div>
          )}

          {report.map(({ supplier, items, total_units }) => (
            <div key={supplier} className="card" style={{ overflow: 'hidden' }}>
              <div className="report-supplier"><span>{supplier}</span></div>
              {/* caps at 10 visible rows and scrolls; the header stays put */}
              <div className="report-items">
                {/* Fixed column widths, the same in every supplier's table, so
                    Quantity / Item Price / For Remittance line up down the page
                    instead of each table sizing its columns to its own figures. */}
                <table className="tbl tbl--fixed">
                  <colgroup>
                    <col />
                    <col style={{ width: '16%' }} />
                    <col style={{ width: '18%' }} />
                    <col style={{ width: '20%' }} />
                  </colgroup>
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
                          <span className="cell-trunc" title={item.item_name}>
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
                  {/* The API reports its source as "ustore.db (live)", and this
                      line used to append "(live API)" to it - so the footer read
                      "ustore.db (live) (live API)". The file is what a reader
                      needs; the parenthetical is stripped rather than the API
                      changed, since the value is the API's to report. */}
                  <Icon name="file" size={12} /> Source: {(meta?.source ?? 'ustore.db').replace(/\s*\(.*\)\s*$/, '')}
                </div>
                <div className="hint">Period: {label}</div>
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
