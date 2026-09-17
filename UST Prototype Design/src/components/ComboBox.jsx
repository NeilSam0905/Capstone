import { useState, useRef, useEffect, useMemo, useId } from 'react';

/**
 * A text input that also offers the values already in use.
 *
 * Replaces `<input list=...>` + `<datalist>`, which was the right idea with
 * the wrong widget: a datalist gives no affordance that a list exists, opens
 * inconsistently across browsers (Firefox only on focus, Safari barely at
 * all), and cannot be styled - so next to the app's own `.filter` selects it
 * read as a plain textbox and the suggestions were effectively invisible.
 *
 * The requirement it has to keep meeting is that a genuinely NEW value is
 * allowed: adding an item to the catalogue may well introduce a supplier or a
 * category nothing else uses yet, and a closed <select> would block exactly
 * the case the dialog exists for. So this stays free text, and says so - when
 * what is typed matches nothing, the list shows an explicit "use as new"
 * row rather than appearing empty and looking broken.
 */
export default function ComboBox({
  value, onChange, options = [], placeholder = '',
  invalid = false, newLabel = 'new', id,
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const wrap = useRef(null);
  const auto = useId();
  const listId = id || `combo-${auto}`;

  // Close when the click lands outside. Without this the list stays over the
  // rest of the form and swallows the next click.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = e => { if (!wrap.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const needle = (value || '').trim().toLowerCase();
  const matches = useMemo(() => {
    const list = options.filter(Boolean);
    if (!needle) return list;
    return list.filter(o => o.toLowerCase().includes(needle));
  }, [options, needle]);

  // Typed something that is not an existing option: offer it explicitly.
  const isNew = !!needle && !options.some(o => o.toLowerCase() === needle);

  function pick(v) {
    onChange(v);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive(i => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(i => Math.max(i - 1, -1));
    } else if (e.key === 'Enter' && open && active >= 0 && matches[active]) {
      e.preventDefault();
      pick(matches[active]);
    } else if (e.key === 'Escape') {
      setOpen(false);
      setActive(-1);
    }
  }

  return (
    <div className="combo" ref={wrap}>
      <input
        type="text"
        className={`combo__input${invalid ? ' is-err' : ''}`}
        value={value}
        placeholder={placeholder}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        onChange={e => { onChange(e.target.value); setOpen(true); setActive(-1); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className="combo__toggle"
        tabIndex={-1}
        aria-label="Show suggestions"
        onClick={() => setOpen(o => !o)}
      >▾</button>

      {open && (
        <ul className="combo__list" id={listId} role="listbox">
          {isNew && (
            <li
              className="combo__opt combo__opt--new"
              role="option"
              aria-selected={false}
              onMouseDown={e => { e.preventDefault(); pick(value.trim()); }}
            >
              Use “{value.trim()}” <span className="combo__badge">{newLabel}</span>
            </li>
          )}
          {matches.map((o, i) => (
            <li
              key={o}
              className={`combo__opt${i === active ? ' is-active' : ''}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={e => { e.preventDefault(); pick(o); }}
            >{o}</li>
          ))}
          {!isNew && matches.length === 0 && (
            <li className="combo__empty">No matches — type to add a new one.</li>
          )}
        </ul>
      )}
    </div>
  );
}
