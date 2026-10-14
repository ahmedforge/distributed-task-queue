import React, { useEffect, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { Layers, PlusCircle, AlertTriangle, List, Moon, Sun } from 'lucide-react';
import { Toaster } from 'react-hot-toast';

export const Layout: React.FC = () => {
  const location = useLocation();
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('distribuqueue-theme') === 'light' ? 'light' : 'dark'; }
    catch { return 'dark'; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('distribuqueue-theme', theme); } catch { /* Storage may be disabled. */ }
  }, [theme]);
  const navItems = [
    { path: '/', label: 'Job Dashboard', icon: List },
    { path: '/submit', label: 'Submit PDF Job', icon: PlusCircle },
    { path: '/dlq', label: 'Failed Jobs', icon: AlertTriangle },
  ];
  return (
    <div className="min-h-screen bg-surface text-fg flex flex-col">
      <Toaster position="top-right" toastOptions={{ duration: 6000, style: { background: 'var(--panel)', color: 'var(--fg)', border: '1px solid var(--border)' } }} />
      <header className="border-b border-border bg-panel">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center justify-between gap-4">
            <Link to="/" className="flex items-center gap-3 font-bold text-lg tracking-wide"><Layers className="h-6 w-6 text-primary" />DistribuQueue</Link>
            <button aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} className="p-2 rounded-lg border border-border hover:bg-panel-alt" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </button>
          </div>
          <nav aria-label="Main navigation" className="flex flex-wrap gap-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = location.pathname === item.path;
              return <Link key={item.path} to={item.path} aria-current={active ? 'page' : undefined} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border transition-colors ${active ? 'bg-panel-alt text-primary border-border' : 'text-muted border-transparent hover:text-fg hover:bg-panel-alt'}`}><Icon className="h-4 w-4" />{item.label}</Link>;
            })}
          </nav>
        </div>
      </header>
      <main className="flex-1 min-w-0 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8"><Outlet /></main>
    </div>
  );
};
