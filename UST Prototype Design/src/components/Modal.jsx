import { useEffect, useRef } from 'react';
import Icon from './Icon';

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
}) {
  const panel = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = e => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div
        className="modal-panel"
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
          <button className={closeClass} onClick={onClose} aria-label="Close">
            <Icon name="xCircle" size={13} /> Close
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
