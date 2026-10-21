import { useCallback, useEffect, useRef, useState } from 'react';
import { setAdminPrompt } from '../services/dataService';
import Modal from './Modal';
import Icon from './Icon';
import PasswordInput from './PasswordInput';

/**
 * The admin's confirmation, asked for whenever someone other than the admin
 * changes or removes a saved record (backend app.py _needs_admin).
 *
 * Mounted once, around both the Tally Interface and the dashboard (App.jsx),
 * and registered with dataService: a write that comes back `admin_required`
 * waits on this dialog, then is sent again with the password. A wrong
 * password brings the dialog back with the server's reason; Cancel gives the
 * write up, and the screen that made it says it was not saved.
 */
export default function AdminConfirm() {
  const [ask, setAsk] = useState(null);           // { action, error } while open
  const [password, setPassword] = useState('');
  const resolver = useRef(null);
  const input = useRef(null);

  // The prompt itself: opens the dialog and hands back a promise of the
  // password (null when cancelled). The returned unregister is the cleanup.
  useEffect(() => setAdminPrompt(({ action, error }) => new Promise(resolve => {
    resolver.current = resolve;
    setPassword('');
    setAsk({ action, error });
  })), []);

  // After the Modal has focused its own panel (its effect runs first, being
  // the child), put the cursor in the password box.
  useEffect(() => { if (ask) input.current?.focus(); }, [ask]);

  const finish = useCallback(value => {
    const resolve = resolver.current;
    resolver.current = null;
    setAsk(null);
    setPassword('');
    resolve?.(value);
  }, []);
  const cancel = useCallback(() => finish(null), [finish]);

  if (!ask) return null;

  return (
    <Modal open onClose={cancel} width={460} title="Admin Confirmation"
           subtitle="This change edits a record that is already saved.">
      <form onSubmit={e => { e.preventDefault(); if (password) finish(password); }}>
        <p className="admin-confirm__text">
          <Icon name="alert" size={15} />
          <span>
            <b>{ask.action || 'This change'}</b> needs the admin&rsquo;s approval. Ask the admin to type
            their password below to confirm it.
          </span>
        </p>
        {ask.error && (
          <div className="notice notice--crit" role="alert" style={{ marginBottom: 12 }}>{ask.error}</div>
        )}
        <div className="field" style={{ marginBottom: 16 }}>
          <label htmlFor="admin-confirm-pass">Admin password</label>
          <PasswordInput id="admin-confirm-pass" inputRef={input} autoComplete="off"
                         value={password} onChange={e => setPassword(e.target.value)} />
        </div>
        <div className="btn-row">
          <button type="submit" className="btn btn--ink btn--sm" disabled={!password}>Confirm</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={cancel}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
