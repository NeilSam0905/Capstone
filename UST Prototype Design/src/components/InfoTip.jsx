import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from './Icon';
import { POP_EXIT_MS, reducedMotion } from '../lib/motion';

const GAP = 8;      // between the button and the panel
const MARGIN = 12;  // kept clear of the window's edges

/** An (i) button that opens an explanation, for text a reader only needs once.
 *
 *  It opens on click rather than hover: a touch screen has no hover, and an
 *  explanation that vanishes when the pointer slips off is hard to read. A
 *  second click, a click anywhere else or Escape closes it; scrolling or
 *  resizing moves it with its button instead.
 *
 *  The panel is portalled to <body> with fixed positioning, so no card or
 *  scrolling area can clip it. It opens below the button, or above when there
 *  is no room below, and is kept inside the window.
 *
 *  `text` puts a visible label beside the (i), for a tip that stands in for a
 *  whole section rather than explaining the word next to it.
 *
 *  `link` makes words in a sentence the button instead, underlined and with
 *  no (i) - "1 Item" in the Batch Sales Report opens the list of which.
 *
 *  Clicks stop at the tip, so an (i) in a sortable column heading or a
 *  clickable row opens its explanation and does nothing else. React passes a
 *  portal's events up to its parent components too, hence the panel as well. */
export default function InfoTip({ label = 'More information', text, link, width = 340, children }) {
  const [open, setOpen] = useState(false);
  // The panel stays mounted for its short exit back into the (i).
  const [closing, setClosing] = useState(false);
  const exitTimer = useRef(0);
  useEffect(() => () => clearTimeout(exitTimer.current), []);
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const id = useId();

  const show = () => { clearTimeout(exitTimer.current); setClosing(false); setOpen(true); };
  const hide = useCallback(() => {
    const finish = () => { setOpen(false); setClosing(false); };
    if (reducedMotion()) { finish(); return; }
    setClosing(true);
    clearTimeout(exitTimer.current);
    exitTimer.current = setTimeout(finish, POP_EXIT_MS);
  }, []);

  // Placed after the panel renders, so its real height decides above or
  // below. Written straight to the element: it is a measurement of the page,
  // not state, and it renders hidden until this has run. The side it opens
  // on is kept while it stays open (`side`), so following the button on a
  // scroll never makes it jump from below to above.
  const side = useRef(null);
  const place = useCallback(() => {
    const pop = popRef.current;
    if (!btnRef.current || !pop) return;
    const b = btnRef.current.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - 2 * MARGIN);
    pop.style.width = `${w}px`;
    const h = pop.offsetHeight;
    if (side.current == null) {
      side.current = b.bottom + GAP + h > window.innerHeight - MARGIN && b.top - GAP - h > MARGIN
        ? 'above' : 'below';
    }
    const top = side.current === 'above' ? b.top - GAP - h : b.bottom + GAP;
    const left = Math.max(MARGIN, Math.min(b.left, window.innerWidth - w - MARGIN));
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    // Grow out of the (i) and shrink back into it: the scale's origin is the
    // button's centre, on the edge of the panel that faces it.
    pop.style.transformOrigin = `${b.left + b.width / 2 - left}px ${side.current === 'above' ? '100%' : '0'}`;
    pop.style.visibility = 'visible';
  }, [width]);

  useLayoutEffect(() => {
    if (!open) { side.current = null; return; }
    place();
  }, [open, place]);

  // Closes only on a click outside it (or on the (i) again) and on Escape.
  // Scrolling or resizing moves it with its button instead: a fixed panel
  // would otherwise be left behind, and closing it there lost the reading
  // whenever the page moved under it.
  useEffect(() => {
    if (!open) return undefined;
    const close = hide;
    const onDown = e => {
      if (!btnRef.current?.contains(e.target) && !popRef.current?.contains(e.target)) close();
    };
    const onKey = e => {
      if (e.key === 'Escape') { close(); btnRef.current?.focus(); }
    };
    let frame = 0;
    const follow = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(place);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', follow, true);
    window.addEventListener('resize', follow);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', follow, true);
      window.removeEventListener('resize', follow);
    };
  }, [open, place, hide]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={(link ? 'infotip__link' : 'infotip__btn' + (text ? ' infotip__btn--text' : ''))
          + (open && !closing ? ' is-open' : '')}
        aria-label={text || link ? undefined : label}
        aria-expanded={open && !closing}
        aria-controls={open ? id : undefined}
        onClick={e => { e.stopPropagation(); if (open && !closing) hide(); else show(); }}
      >
        {link ?? <>{text}<Icon name="info" size={15} /></>}
      </button>
      {open && createPortal(
        <div id={id} ref={popRef} role="dialog" aria-label={label}
             className={'infotip__pop' + (closing ? ' is-closing' : '')}
             onClick={e => e.stopPropagation()}
             onWheel={e => passWheel(e, popRef.current, btnRef.current)}>
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}

/** Whether `el` can scroll further in the wheel's direction. */
function canScroll(el, dy) {
  const oy = getComputedStyle(el).overflowY;
  if (!(oy === 'auto' || oy === 'scroll') || el.scrollHeight <= el.clientHeight) return false;
  return dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1;
}

/** The nearest scrolling area holding `el` - the dashboard's .scroll, the
 *  Tally Interface's page - or the document. */
function scrollParent(el) {
  for (let n = el?.parentElement; n; n = n.parentElement) {
    if (n.scrollHeight > n.clientHeight && /(auto|scroll)/.test(getComputedStyle(n).overflowY)) return n;
  }
  return document.scrollingElement;
}

/** The panel is portalled to <body>, outside the page's scrolling area, so a
 *  wheel over it would scroll nothing. Hand the wheel to the area that holds
 *  its button instead - the panel follows it there (the scroll listener
 *  above) - unless a list inside the panel can still scroll that way, which
 *  then scrolls as usual. Line-based wheels (deltaMode 1, Firefox) are turned
 *  into pixels. */
function passWheel(e, pop, btn) {
  for (let n = e.target; n && n !== pop; n = n.parentElement) {
    if (n instanceof Element && canScroll(n, e.deltaY)) return;
  }
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1;
  scrollParent(btn)?.scrollBy({ top: e.deltaY * unit, left: 0 });
}
