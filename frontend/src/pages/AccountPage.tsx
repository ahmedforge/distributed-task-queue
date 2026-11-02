import { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../auth-context';

export function AccountPage() {
  const { account, loading, authenticate } = useAuth();
  const location = useLocation();
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const destination = location.state?.from;
  if (account && !loading) return <Navigate to={typeof destination === 'string' && /^\/(submit|dlq|jobs\/\d+)?$/.test(destination) ? destination : '/'} replace />;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('');
    try { await authenticate(username, password, register); setPassword(''); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to sign in.'); }
  };
  return <div className="auth-layout">
    <section className="auth-intro">
      <h1>Render PDFs<br />in the background.</h1>
      <p>Submit a document, follow its progress, and download the rendered pages when they're ready.</p>
      <p className="auth-caption">PDF → background workers → PNG / ZIP</p>
    </section>
    <section className="form-panel auth-form" aria-labelledby="account-title">
      <h2 id="account-title">{register ? 'Create an account' : 'Sign in'}</h2>
      <form onSubmit={submit} className="form-stack">
        <div><label className="form-label" htmlFor="username">Username</label><input id="username" className="field" autoComplete="username" required minLength={3} maxLength={32} pattern="[A-Za-z][A-Za-z0-9_]{2,31}" value={username} onChange={event => setUsername(event.target.value)} aria-describedby={register ? 'username-help' : undefined} />
        {register && <p id="username-help" className="field-note">3–32 letters, digits or underscores. Start with a letter.</p>}</div>
        <div><label className="form-label" htmlFor="password">Password</label><input id="password" className="field" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={12} maxLength={128} value={password} onChange={event => setPassword(event.target.value)} aria-describedby={register ? 'password-help' : undefined} />
        {register && <p id="password-help" className="field-note">At least 12 characters. Save your password; account recovery isn't available yet.</p>}</div>
        {error && <p role="alert" className="notice">{error}</p>}
        <button disabled={loading} className="button button-primary">{loading ? 'Please wait…' : register ? 'Create account' : 'Sign in'}</button>
      </form>
      <div className="auth-switch">{register ? 'Already registered?' : 'New to DistribuQueue?'}<button disabled={loading} onClick={() => { setRegister(!register); setError(''); setPassword(''); }}>{register ? 'Sign in' : 'Create an account'}</button></div>
    </section>
  </div>;
}
