import { useState, useMemo } from 'react';
import Icon from './Icon';

/**
 * Sortable, paginated table on the design system's .tbl styling.
 *
 * Column options:
 *   width     column width, e.g. '22%' or '90px'. Rendered into a <colgroup>
 *             and honoured exactly, because the table uses `table-layout:
 *             fixed`. Without this the browser hands surplus width to the
 *             widest column, which is what left a gap after a truncated
 *             supplier name.
 *   num       right-align (tabular numerals)
 *   strong    darker body text
 *   truncate  ellipsise, keeping the full value in a title tooltip
 *   render    (value, row) => node
 *
 * `minWidth` keeps every column readable on a narrow viewport by letting the
 * wrapper scroll horizontally rather than crushing the columns.
 */
export default function DataTable({ columns, data, pageSize = 10, minWidth = 0 }) {
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [page, setPage] = useState(1);

  function handleSort(key) {
    if (sortCol === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortCol(key); setSortDir('asc'); }
    setPage(1);
  }

  const sorted = useMemo(() => {
    if (!sortCol) return data;
    return [...data].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;          // blanks last, either direction
      if (bv == null) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
      return sortDir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [data, sortCol, sortDir]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pageCount);
  const start = (current - 1) * pageSize;
  const rows = sorted.slice(start, start + pageSize);

  function sortIcon(key) {
    if (sortCol !== key) return 'sortNone';
    return sortDir === 'asc' ? 'sortAsc' : 'sortDesc';
  }

  return (
    <>
      <div className="tbl__scroll">
        <table className="tbl tbl--fixed" style={minWidth ? { minWidth } : undefined}>
          <colgroup>
            {columns.map(col => <col key={col.key} style={col.width ? { width: col.width } : undefined} />)}
          </colgroup>
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col.key} className={'sortable' + (col.num ? ' num' : '')} onClick={() => handleSort(col.key)}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    {col.label}
                    <Icon size={11} name={sortIcon(col.key)} />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.product_id ?? row.rowKey ?? start + i}>
                {columns.map(col => {
                  const value = col.render ? col.render(row[col.key], row) : row[col.key];
                  return (
                    <td
                      key={col.key}
                      className={(col.num ? 'num ' : '') + (col.strong ? 'strong' : '')}
                      title={col.truncate ? String(row[col.key] ?? '') : undefined}
                    >
                      {col.truncate ? <span className="cell-trunc">{value}</span> : value}
                    </td>
                  );
                })}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={columns.length}><div className="empty">Nothing to show.</div></td></tr>
            )}
          </tbody>
        </table>
      </div>

      {sorted.length > pageSize && (
        <div className="pager">
          <span className="hint">
            {start + 1}–{Math.min(start + pageSize, sorted.length)} of {sorted.length.toLocaleString()}
          </span>
          <div className="pager__pages">
            <button className="pager__btn" onClick={() => setPage(current - 1)} disabled={current === 1}>Prev</button>
            {pageNumbers(current, pageCount).map((p, i) => (
              p === '…'
                ? <span key={`gap${i}`} className="pager__gap">…</span>
                : <button
                    key={p}
                    className={'pager__btn' + (p === current ? ' active' : '')}
                    onClick={() => setPage(p)}
                  >{p}</button>
            ))}
            <button className="pager__btn" onClick={() => setPage(current + 1)} disabled={current === pageCount}>Next</button>
          </div>
        </div>
      )}
    </>
  );
}

/** Compact page list: 1 … 4 5 6 … 52 */
function pageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out = [1];
  const from = Math.max(2, current - 1);
  const to = Math.min(total - 1, current + 1);
  if (from > 2) out.push('…');
  for (let p = from; p <= to; p++) out.push(p);
  if (to < total - 1) out.push('…');
  out.push(total);
  return out;
}
