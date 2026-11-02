import React, { useEffect, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { ChevronDown, Moon, Sun } from 'lucide-react';
import toast, { Toaster } from 'react-hot-toast';
import { useAuth } from '../auth-context';

export const Layout: React.FC = () => {
  const location = useLocation();
  const { account, logout, loading } = useAuth();
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('distribuqueue-theme') === 'dark' ? 'dark' : 'light'; }
    catch { return 'light'; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('distribuqueue-theme', theme); } catch { /* Storage may be disabled. */ }
  }, [theme]);
  return <div className="min-h-screen flex flex-col">
    <Toaster position="bottom-right" toastOptions={{ duration: 6000, style: { background: 'var(--panel)', color: 'var(--fg)', border: '1px solid var(--border)', borderRadius: 5, fontSize: 13 } }} />
    <header className="masthead">
      <div className="shell">
        <div className="masthead-row">
          <Link to="/" className="wordmark"><span className="queue-mark" aria-hidden="true"><i /><i /><i /></span>DistribuQueue</Link>
          <div className="header-tools">
            {account && <details className="account-menu" key={location.pathname}>
              <summary aria-label={`Account: ${account.username}`}><span className="account-name">{account.username}</span><ChevronDown size={13} aria-hidden="true" /></summary>
              <div className="account-options">
                <button disabled={loading} onClick={() => { void logout().catch(error => toast.error(error.message)); }}>Sign out</button>
                <button disabled={loading} onClick={() => { void logout(true).catch(error => toast.error(error.message)); }}>Sign out all devices</button>
              </div>
            </details>}
            <button aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </div>
        {account && <nav aria-label="Main navigation" className="navigation">
          {[['/', 'Jobs'], ['/submit', 'Upload'], ['/dlq', 'Failed']].map(([path, label]) => <Link key={path} to={path} aria-current={location.pathname === path || (path === '/' && location.pathname.startsWith('/jobs/')) ? 'page' : undefined}>{label}</Link>)}
        </nav>}
      </div>
    </header>
    <main className="shell workspace flex-1 w-full"><Outlet /></main>
  </div>;
};
