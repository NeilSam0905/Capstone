import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import Icon from './Icon';
import { MODAL_EXIT_MS, reducedMotion } from '../lib/motion';

/**
 * Modal — a centred dialog over a scrim.
 *
 * Deliberately small: this app had no dialog at all before, and the one thing
 * that needed one (Check Current Inventory) is a read-only list. So this
 * covers the three behaviours a keyboard user expects and nothing more —
 * Escape closes, a click on the scrim closes, and focus moves into the dialog
 * on open so Tab lands inside it rather than behind it.
 *
 * Body scroll is locked while open, otherwise scrolling the list scrolls the
 * page underneath once the list bottoms out.
 */
export default function Modal({
  open, title, subtitle, onClose, children, width = 860,
  // Styling for the header's Close button. Black-and-white by default, which
  // matches the primary actions elsewhere in the app (Record Entry, Save
  // Count, Add Item). Still a prop, so one dialog can differ without the
  // change quietly reaching the others.
  closeClass = 'btn btn--ink btn--sm',
  // For a short form whose dropdowns must be able to hang past the dialog's
  // bottom edge (Add New Item). The default body clips, because a long list
  // dialog scrolls inside .modal-grow instead.
  overflowVisible = false,
  // An extra class on the panel, for one dialog's own sizing (the Reorder
  // what-if is taller and scrolls its body).
  className = '',
}) {
  const panel = useRef(null);

  // Closing from inside the dialog (Close, the scrim, Escape) plays a short
  // exit first - shorter than the entrance - then tells the caller. A dialog
  // its caller closes some other way (after Apply, say) just goes. `closing`
  // resets once onClose has run, for a caller that keeps the dialog mounted.
  const [closing, setClosing] = useState(false);
  const exitTimer = useRef(0);
  useEffect(() => () => clearTimeout(exitTimer.current), []);
  const requestClose = useCallback(() => {
    if (!onClose) return;
    if (reducedMotion()) { onClose(); return; }
    clearTimeout(exitTimer.current);
    setClosing(true);
    exitTimer.current = setTimeout(() => { setClosing(false); onClose(); }, MODAL_EXIT_MS);
  }, [onClose]);

  // Focus and the scroll lock follow `open` only. Callers often pass a new
  // onClose on every render, and re-running this with it pulled focus back to
  // this panel each time - away from a dialog opened on top of it.
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.focus();
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    // Escape closes only the dialog on top: one opened over another (the
    // admin's confirmation, a Saved notice) is the last panel in <body>.
    const onKey = e => {
      if (e.key !== 'Escape') return;
      const panels = document.querySelectorAll('.modal-panel');
      if (panels.length && panels[panels.length - 1] !== panel.current) return;
      requestClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, requestClose]);

  if (!open) return null;

  // Rendered into <body>, not where the dialog is declared: a page's own
  // layout must never reach the scrim. The Tally page caps every direct child
  // of .tally-body at 880px, so a dialog declared there darkened only that
  // middle column instead of the whole window.
  return createPortal(
    <div className={`modal-scrim${closing ? ' is-closing' : ''}`}
         onMouseDown={e => { if (e.target === e.currentTarget) requestClose(); }}>
      <div
        className={`modal-panel${className ? ` ${className}` : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={panel}
        style={{ maxWidth: width }}
      >
        <div className="modal-head">
          <div>
            <div className="section-h">{title}</div>
            {subtitle && <div className="hint" style={{ marginTop: 3 }}>{subtitle}</div>}
          </div>
          <button className={closeClass} onClick={requestClose} aria-label="Close">
            <Icon name="xCircle" size={13} /> Close
          </button>
        </div>
        <div className={`modal-body${overflowVisible ? ' modal-body--overflow' : ''}`}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}
