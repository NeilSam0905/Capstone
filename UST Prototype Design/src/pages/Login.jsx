import { useState } from 'react';
import { login } from '../services/dataService';
import Icon from '../components/Icon';
import brandMark from '../assets/ustore-mark.png';

/** The sign-in screen, shown until the backend has a session for this browser
 *  (backend/auth.py). Same chrome as the rest of the app: the black brand
 *  panel the sidebar and tally header use, and a .card form on the page
 *  background. `expired` says why it is back, when it is. */
export default function Login({ onLogin, expired = false }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter your username and password.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await login(username.trim(), password);
      if (r.ok) onLogin(r.user);
      else {
        setError(r.error || 'Could not sign in.');
        setPassword('');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <aside className="login__brand">
        <div className="login__brand-top">
          <img src={brandMark} alt="USTore" className="brand-mark" />
          <div>
            <div className="wordmark">USTore</div>
            <div className="brand-sub">Inventory Analytics</div>
          </div>
        </div>
      </aside>

      <main className="login__main">
        <form className="card card__pad login__card" onSubmit={submit} noValidate>
          <div className="topbar__crumb">Sign in</div>
          <div className="login__title">Welcome back</div>
          <div className="hint" style={{ marginBottom: 18 }}>
            Sign in to use the tally interface and the dashboard.
          </div>

          {expired && !error && (
            <div className="notice notice--info" style={{ marginBottom: 14 }}>
              Your session ended. Sign in again to continue.
            </div>
          )}
          {error && (
            <div className="notice notice--crit" role="alert" style={{ marginBottom: 14 }}>
              {error}
            </div>
          )}

          <div className="field" style={{ marginBottom: 14 }}>
            <label htmlFor="login-user">Username</label>
            <input id="login-user" autoComplete="username" autoFocus
                   value={username} onChange={e => setUsername(e.target.value)}
                   className={error && !username.trim() ? 'is-err' : undefined} />
          </div>
          <div className="field" style={{ marginBottom: 20 }}>
            <label htmlFor="login-pass">Password</label>
            <input id="login-pass" type="password" autoComplete="current-password"
                   value={password} onChange={e => setPassword(e.target.value)}
                   className={error && !password ? 'is-err' : undefined} />
          </div>

          <button type="submit" className="btn btn--gold login__submit" disabled={busy}>
            {busy ? 'Signing in…' : <>Sign in <Icon name="arrow" size={15} /></>}
          </button>
        </form>
      </main>
    </div>
  );
}
