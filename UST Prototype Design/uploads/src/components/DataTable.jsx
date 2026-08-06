import { useState } from 'react';
import Icon from './Icon';

/** Sortable table using the design system's .tbl styling. */
export default function DataTable({ columns, data }) {
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState('asc');

  function handleSort(key) {
    if (sortCol === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortCol(key); setSortDir('asc'); }
  }

  const sorted = sortCol
    ? [...data].sort((a, b) => {
        const av = a[sortCol], bv = b[sortCol];
        if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
        return sortDir === 'asc'
          ? String(av).localeCompare(String(bv))
          : String(bv).localeCompare(String(av));
      })
    : data;

  return (
    <div className="tbl__scroll">
      <table className="tbl">
        <thead>
          <tr>
            {columns.map(col => (
              <th
                key={col.key}
                className={'sortable' + (col.num ? ' num' : '')}
                onClick={() => handleSort(col.key)}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  {col.label}
                  <Icon size={11} name={sortCol === col.key ? (sortDir === 'asc' ? 'sortAsc' : 'sortDesc') : 'sortNone'} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr key={row.product_id ?? i}>
              {columns.map(col => (
                <td key={col.key} className={(col.num ? 'num ' : '') + (col.strong ? 'strong' : '')}>
                  {col.render ? col.render(row[col.key], row) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
