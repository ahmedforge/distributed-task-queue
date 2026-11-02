import type { ReactNode } from 'react';
import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react';

export function PageState({ kind, title, description, action }: {
  kind: 'loading' | 'empty' | 'error'; title: string; description?: string; action?: ReactNode;
}) {
  const Icon = kind === 'loading' ? LoaderCircle : kind === 'error' ? AlertCircle : Inbox;
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className="page-state">
      <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${kind === 'loading' ? 'animate-spin' : ''} ${kind === 'error' ? 'text-danger' : 'text-muted'}`} />
      <div className="min-w-0">
        <p className="page-state-title">{title}</p>
        {description && <p className="text-sm text-muted">{description}</p>}
        {action}
      </div>
    </div>
  );
}
