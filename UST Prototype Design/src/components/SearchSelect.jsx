import { useState, useRef, useEffect, useMemo, useId } from 'react';

/**
 * A closed dropdown (pick one of `options`, nothing new) whose open panel has
 * a search box above the list. For lists too long to scroll through - the
 * Demand Forecast item picker runs to dozens of names.
 *
 * Unlike ComboBox, which is free text with suggestions, this can only ever
 * hold one of its options, so it looks and behaves like the `.filter` selects
 * beside it until it is opened.
 *
 * `options` is [{ value, label }]. The search matches anywhere in the label,
 * ignoring case. `emptyLabel` is shown while nothing is picked. `variant`
 * "field" sizes it like the `.field` inputs of a form (the tally cards)
 * instead of like a topbar filter; `invalid` gives it the error border.
 *
 * The panel always opens BELOW the control. A native <select> opens wherever
 * the browser finds room, which near the bottom of the screen meant a list
 * flying up over the form it belongs to.
 */
export default function SearchSelect({
  id, value, options, onChange, minWidth = 200, placeholder = 'Search…',
  emptyLabel = '', variant, invalid = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const wrap = useRef(null);
  const input = useRef(null);
  const list = useRef(null);
  const listId = `${useId()}-list`;

  const selected = options.find(o => o.value === value);

  const needle = query.trim().toLowerCase();
  const matches = useMemo(
    () => (needle ? options.filter(o => o.label.toLowerCase().includes(needle)) : options),
    [options, needle]
  );

  // Close when a click lands outside, so the panel does not sit over the page
  // and swallow the next click.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = e => { if (!wrap.current?.contains(e.target)) close(); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Opening puts the cursor in the search box (see openPanel for the
  // highlight).
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  function openPanel() {
    setActive(Math.max(0, options.findIndex(o => o.value === value)));
    setOpen(true);
  }

  // Keep the highlighted row in view while arrowing through the list.
  useEffect(() => {
    list.current?.children[active]?.scrollIntoView?.({ block: 'nearest' });
  }, [active, open]);

  function close() {
    setOpen(false);
    setQuery('');
  }

  function pick(o) {
    onChange(o.value);
    close();
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(i => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (matches[active]) pick(matches[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  return (
    <div className={`sselect${variant ? ` sselect--${variant}` : ''}`} ref={wrap} style={{ minWidth }}>
      <button
        id={id}
        type="button"
        className={`sselect__btn${invalid ? ' is-err' : ''}${selected ? '' : ' is-empty'}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? close() : openPanel())}
        onKeyDown={e => { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); openPanel(); } }}
      >
        <span className="sselect__value">{selected ? selected.label : emptyLabel}</span>
        <span className="filter__chev">▾</span>
      </button>

      {open && (
        <div className="sselect__panel">
          <input
            ref={input}
            type="search"
            className="sselect__search"
            placeholder={placeholder}
            value={query}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
          />
          <ul className="sselect__list" id={listId} role="listbox" ref={list}>
            {matches.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                className={`sselect__opt${i === active ? ' is-active' : ''}${o.value === value ? ' is-selected' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={e => { e.preventDefault(); pick(o); }}
              >{o.label}</li>
            ))}
            {matches.length === 0 && <li className="sselect__empty">No items match “{query.trim()}”.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
