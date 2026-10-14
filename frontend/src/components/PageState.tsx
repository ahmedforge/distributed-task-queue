import type { ReactNode } from 'react';
import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react';

export function PageState({ kind, title, description, action }: {
  kind: 'loading' | 'empty' | 'error'; title: string; description?: string; action?: ReactNode;
}) {
  const Icon = kind === 'loading' ? LoaderCircle : kind === 'error' ? AlertCircle : Inbox;
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className="rounded-xl border border-border bg-panel p-6 flex items-start gap-4">
      <Icon aria-hidden="true" className={`h-6 w-6 shrink-0 ${kind === 'loading' ? 'animate-spin' : ''} ${kind === 'error' ? 'text-danger' : 'text-muted'}`} />
      <div className="space-y-2 min-w-0">
        <p className="font-semibold">{title}</p>
        {description && <p className="text-sm text-muted">{description}</p>}
        {action}
      </div>
    </div>
  );
}
