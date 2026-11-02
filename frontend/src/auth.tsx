import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { AuthContext, useAuth, type Account } from './auth-context';
import { apiFetch } from './api/client';
import { PageState } from './components/PageState';


export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery<Account | null>({
    queryKey: ['account'], retry: false, refetchInterval: 60000,
    queryFn: async () => {
      const res = await fetch('/api/v1/account/me', { cache: 'no-store' });
      if (res.status === 401) return null;
      if (!res.ok) throw new Error('Unable to check your session. Try again.');
      return res.json();
    },
  });
  const [changing, setChanging] = useState(false);
  useEffect(() => {
    const expire = () => { void client.cancelQueries().then(() => { client.clear(); client.setQueryData(['account'], null); }); };
    window.addEventListener('dtq:unauthorized', expire);
    return () => window.removeEventListener('dtq:unauthorized', expire);
  }, [client]);
  // Purge cached job data if another tab switches or ends the account session.
  useEffect(() => {
    const refresh = () => window.location.reload();
    window.addEventListener('dtq:account-change', refresh);
    const channel = new BroadcastChannel('dtq-account');
    channel.onmessage = refresh;
    return () => { channel.close(); window.removeEventListener('dtq:account-change', refresh); };
  }, [client]);
  const reset = async (account: Account | null) => {
    await client.cancelQueries(); client.clear(); client.setQueryData(['account'], account);
    const channel = new BroadcastChannel('dtq-account'); channel.postMessage('changed'); channel.close();
  };
  const authenticate = async (username: string, password: string, register: boolean) => {
    setChanging(true);
    try {
      const res = await apiFetch(`/api/v1/account/${register ? 'register' : 'login'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to sign in.');
      await reset(data);
    } finally { setChanging(false); }
  };
  const logout = async (all = false) => {
    setChanging(true);
    try {
      const res = await apiFetch(`/api/v1/account/${all ? 'logout-all' : 'logout'}`, { method: 'POST' });
      if (!res.ok) throw new Error('Unable to sign out. Try again.');
      await reset(null);
    } finally { setChanging(false); }
  };
  return <AuthContext.Provider value={{ account: query.data ?? null, loading: query.isPending || changing, error: query.error, retry: () => { void query.refetch(); }, authenticate, logout }}>{children}</AuthContext.Provider>;
}

export function RequireAccount() {
  const { account, loading, error, retry } = useAuth();
  const location = useLocation();
  if (loading) return <PageState kind="loading" title="Checking your session" />;
  if (error) return <div><PageState kind="error" title="Unable to check your session" description={error.message} /><button onClick={retry}>Try again</button></div>;
  if (!account) return <Navigate to="/account" state={{ from: location.pathname }} replace />;
  return <Outlet key={account.username} />;
}
