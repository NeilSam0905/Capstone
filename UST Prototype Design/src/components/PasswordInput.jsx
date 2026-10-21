import { useState } from 'react';
import Icon from './Icon';

/** A password field with an eye button that shows or hides what was typed -
 *  so someone can check a password before sending it. Used by the sign-in
 *  form and the admin confirmation dialog. Every other prop goes to the
 *  <input> itself; `inputRef` reaches it for focusing. */
export default function PasswordInput({ inputRef, ...props }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="pw-field">
      <input {...props} ref={inputRef} type={shown ? 'text' : 'password'} />
      <button type="button" className="pw-field__toggle"
              onClick={() => setShown(s => !s)}
              aria-label={shown ? 'Hide password' : 'Show password'}
              aria-pressed={shown}
              title={shown ? 'Hide password' : 'Show password'}>
        <Icon name={shown ? 'eyeOff' : 'eye'} size={16} />
      </button>
    </div>
  );
}
