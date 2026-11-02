import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';

const space = (bytes: number) => {
  if (bytes > 0 && bytes < 1024) return `${bytes} B`;
  const small = bytes > 0 && bytes < 1048576;
  return `${(bytes / (small ? 1024 : 1048576)).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${small ? 'KiB' : 'MiB'}`;
};

export function AccountUsage({ upload = false }: { upload?: boolean }) {
  const { data, error } = useQuery({ queryKey: ['accountUsage'], queryFn: apiClient.getUsage, refetchInterval: 5000 });
  if (!data) return <p className="field-note" role={error ? 'alert' : 'status'}>{error ? 'Account limits are unavailable. Uploads will still be checked by the server.' : 'Checking account limits…'}</p>;
  return <div className="usage-summary">
    <p><span>Active <strong>{data.active_jobs} / {data.limits.active_jobs}</strong></span><span>Saved <strong>{data.retained_jobs} / {data.limits.retained_jobs}</strong></span><span>Storage <strong>{data.storage_accounted ? `${space(data.storage_bytes + data.reserved_bytes)} / ${space(data.limits.storage_bytes)}` : 'Unmeasured files'}</strong></span></p>
    {data.reserved_bytes > 0 && <p className="field-note">Includes {space(data.reserved_bytes)} reserved for results. Space is released after successful rendering or file cleanup.</p>}
    {!data.storage_accounted && <p role="alert" className="field-note text-danger">Existing files need measuring before another upload or retry. Ask the operator to update storage accounting, or delete finished jobs.</p>}
    {upload && <p className="field-note">Up to {space(data.limits.max_result_bytes)} is reserved per new ZIP. {data.limits.retention_days ? `New jobs and their files are deleted ${data.limits.retention_days} days after they finish, including failed jobs.` : 'Automatic expiry is disabled for new jobs.'} Download results you want to keep.</p>}
    {error && <p role="alert" className="field-note text-danger">Unable to refresh limits. Showing the last recorded usage.</p>}
  </div>;
}
