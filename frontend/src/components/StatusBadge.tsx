import type { JobStatus } from '../api/client';
export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className="status-pill" data-status={status}>{status.charAt(0) + status.slice(1).toLowerCase()}</span>;
}
