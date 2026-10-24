import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from './Icon';

const GAP = 8;      // between the button and the panel
const MARGIN = 12;  // kept clear of the window's edges

/** An (i) button that opens an explanation, for text a reader only needs once.
 *
 *  It opens on click rather than hover: a touch screen has no hover, and an
 *  explanation that vanishes when the pointer slips off is hard to read. A
 *  second click, a click anywhere else, Escape, scrolling or resizing closes it.
 *
 *  The panel is portalled to <body> with fixed positioning, so no card or
 *  scrolling area can clip it. It opens below the button, or above when there
 *  is no room below, and is kept inside the window.
 *
 *  `text` puts a visible label beside the (i), for a tip that stands in for a
 *  whole section rather than explaining the word next to it. */
export default function InfoTip({ label = 'More information', text, width = 340, children }) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const id = useId();

  // Placed after the panel renders, so its real height decides above or
  // below. Written straight to the element: it is a measurement of the page,
  // not state, and it renders hidden until this has run.
  useLayoutEffect(() => {
    const pop = popRef.current;
    if (!open || !btnRef.current || !pop) return;
    const b = btnRef.current.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - 2 * MARGIN);
    pop.style.width = `${w}px`;
    const h = pop.offsetHeight;
    const below = b.bottom + GAP;
    const top = below + h > window.innerHeight - MARGIN && b.top - GAP - h > MARGIN
      ? b.top - GAP - h
      : below;
    pop.style.left = `${Math.max(MARGIN, Math.min(b.left, window.innerWidth - w - MARGIN))}px`;
    pop.style.top = `${top}px`;
    pop.style.visibility = 'visible';
  }, [open, width]);

  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    const onDown = e => {
      if (!btnRef.current?.contains(e.target) && !popRef.current?.contains(e.target)) close();
    };
    const onKey = e => {
      if (e.key === 'Escape') { close(); btnRef.current?.focus(); }
    };
    // Scrolling would leave a fixed panel behind its button, so it closes.
    const onScroll = e => { if (!popRef.current?.contains(e.target)) close(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={'infotip__btn' + (text ? ' infotip__btn--text' : '') + (open ? ' is-open' : '')}
        aria-label={text ? undefined : label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(o => !o)}
      >
        {text}
        <Icon name="info" size={15} />
      </button>
      {open && createPortal(
        <div id={id} ref={popRef} role="dialog" aria-label={label} className="infotip__pop">
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}
