import { useState, useRef, useEffect, useLayoutEffect, useMemo, useId } from 'react';
import Icon from './Icon';
import { POP_EXIT_MS, reducedMotion } from '../lib/motion';

/**
 * A closed dropdown (pick one of `options`, nothing new) whose open panel has
 * a search box above the list. For lists too long to scroll through - the
 * Demand Forecast item picker runs to dozens of names.
 *
 * Unlike ComboBox, which is free text with suggestions, this can only ever
 * hold one of its options, so it looks and behaves like the `.filter` selects
 * beside it until it is opened.
 *
 * `options` is [{ value, label, disabled?, title?, tag? }]. `tag` is a small
 * pill after the label, in the list and in the closed control - { text, tone }
 * with tone one of the .tag colours (ok, info, warn...) - for a status such as
 * "Recommended" that is not part of the name. A disabled option is
 * listed, greyed out, and cannot be picked; `title` is shown on hover (a
 * native <option> shows neither reliably). The search matches anywhere in the
 * label, ignoring case; `searchable={false}` leaves the search box out, for a
 * list short enough to read at a glance. `emptyLabel` is shown while nothing
 * is picked. `variant`
 * "field" sizes it like the `.field` inputs of a form (the tally cards)
 * instead of like a topbar filter; `invalid` gives it the error border.
 * `icon` names an Icon shown before the value (the topbar's Date Range).
 *
 * The panel always opens BELOW the control. A native <select> opens wherever
 * the browser finds room, which near the bottom of the screen meant a list
 * flying up over the form it belongs to.
 */
/** An option's status pill (see `tag` above). */
function OptTag({ tag }) {
  return <span className={`tag tag--${tag.tone ?? 'info'} sselect__tag`}>{tag.text}</span>;
}

export default function SearchSelect({
  id, value, options, onChange, minWidth = 200, placeholder = 'Search…',
  emptyLabel = '', variant, invalid = false, searchable = true, icon,
}) {
  const [open, setOpen] = useState(false);
  // The panel stays mounted for its short exit back into the control.
  const [closing, setClosing] = useState(false);
  const exitTimer = useRef(0);
  useEffect(() => () => clearTimeout(exitTimer.current), []);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const wrap = useRef(null);
  const input = useRef(null);
  const list = useRef(null);
  const panel = useRef(null);
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

  // Opening puts the cursor in the search box, or on the list when there is
  // none, so the arrow keys work at once (see openPanel for the highlight).
  useEffect(() => {
    if (open) (input.current ?? list.current)?.focus();
  }, [open]);

  function openPanel() {
    clearTimeout(exitTimer.current);
    setClosing(false);
    setActive(Math.max(0, options.findIndex(o => o.value === value)));
    setOpen(true);
  }

  // A list wider than its control hangs to the right, and near the right edge
  // of the page that ran it under the page's edge, cut off (the Reorder
  // table's Supplier filter). Measured once it renders: when it would pass the
  // nearest area that clips it, it opens leftwards from the control instead.
  useLayoutEffect(() => {
    const p = panel.current;
    if (!open || !p) return;
    p.classList.remove('sselect__panel--end');
    let limit = window.innerWidth;
    for (let n = wrap.current?.parentElement; n; n = n.parentElement) {
      if (getComputedStyle(n).overflowX !== 'visible') {
        limit = Math.min(limit, n.getBoundingClientRect().right);
        break;
      }
    }
    if (p.getBoundingClientRect().right > limit - 8) p.classList.add('sselect__panel--end');
  }, [open]);

  // Keep the highlighted row in view while arrowing through the list.
  useEffect(() => {
    list.current?.children[active]?.scrollIntoView?.({ block: 'nearest' });
  }, [active, open]);

  function close() {
    const finish = () => { setOpen(false); setClosing(false); setQuery(''); };
    if (reducedMotion()) { finish(); return; }
    setClosing(true);
    clearTimeout(exitTimer.current);
    exitTimer.current = setTimeout(finish, POP_EXIT_MS);
  }

  function pick(o) {
    if (o.disabled) return;
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
        className={`sselect__btn${invalid ? ' is-err' : ''}${selected ? '' : ' is-empty'}`
          + (icon ? ' sselect__btn--icon' : '')}
        aria-haspopup="listbox"
        aria-expanded={open && !closing}
        onClick={() => (open && !closing ? close() : openPanel())}
        onKeyDown={e => { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); openPanel(); } }}
      >
        {icon && <span className="sselect__icon"><Icon name={icon} size={13} /></span>}
        <span className="sselect__value">{selected ? selected.label : emptyLabel}</span>
        {selected?.tag && <OptTag tag={selected.tag} />}
        <span className="filter__chev">▾</span>
      </button>

      {open && (
        <div ref={panel} className={`sselect__panel${searchable ? '' : ' sselect__panel--plain'}`
          + (closing ? ' is-closing' : '')}>
          {searchable && (
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
          )}
          <ul className={`sselect__list${searchable ? '' : ' sselect__list--plain'}`} id={listId}
              role="listbox" ref={list} tabIndex={searchable ? undefined : -1}
              onKeyDown={searchable ? undefined : onKeyDown}>
            {matches.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                aria-disabled={o.disabled || undefined}
                title={o.title}
                className={`sselect__opt${i === active ? ' is-active' : ''}${o.value === value ? ' is-selected' : ''}`
                  + (o.disabled ? ' is-disabled' : '') + (o.tag ? ' has-tag' : '')}
                // The first rows arrive 30ms apart; the rest with the last of them.
                style={{ '--i': Math.min(i, 8) }}
                onMouseEnter={() => setActive(i)}
                onMouseDown={e => { e.preventDefault(); pick(o); }}
              >{o.tag ? <><span className="sselect__opt-label">{o.label}</span><OptTag tag={o.tag} /></> : o.label}</li>
            ))}
            {matches.length === 0 && <li className="sselect__empty">No items match “{query.trim()}”.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
