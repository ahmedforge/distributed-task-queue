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
  return <section className="max-w-md mx-auto rounded-xl border border-border bg-panel p-6 sm:p-8 space-y-5">
    <div><p className="text-sm text-primary mb-2">Your PDFs. Your queue.</p><h1 className="text-2xl font-bold">{register ? 'Create your account' : 'Welcome back'}</h1><p className="text-muted mt-2">Sign in to submit PDFs and access your saved jobs from another device.</p></div>
    <form onSubmit={submit} className="space-y-4">
      <label className="block">Username<input className="mt-1 block w-full rounded border border-border bg-surface p-3" autoComplete="username" required minLength={3} maxLength={32} pattern="[A-Za-z][A-Za-z0-9_]{2,31}" value={username} onChange={event => setUsername(event.target.value)} /></label>
      <p className="text-xs text-muted">3–32 letters, digits or underscores; start with a letter. Usernames are case insensitive.</p>
      <label className="block">Password<input className="mt-1 block w-full rounded border border-border bg-surface p-3" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={12} maxLength={128} value={password} onChange={event => setPassword(event.target.value)} /></label>
      {register && <p className="text-sm text-muted">Use a unique password of at least 12 characters and save it securely. Password recovery is not available yet.</p>}
      {error && <p role="alert" className="text-danger">{error}</p>}
      <button disabled={loading} className="w-full bg-indigo-600 text-white rounded-lg p-3 disabled:opacity-50">{loading ? 'Please wait…' : register ? 'Create account' : 'Sign in'}</button>
    </form>
    <button className="text-primary text-sm" disabled={loading} onClick={() => { setRegister(!register); setError(''); setPassword(''); }}>{register ? 'Already have an account? Sign in' : 'New here? Create an account'}</button>
  </section>;
}
