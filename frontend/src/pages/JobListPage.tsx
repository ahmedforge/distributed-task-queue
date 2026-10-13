import React, { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { apiClient, type JobStatus } from '../api/client';

const statuses: JobStatus[] = ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'];

export const JobListPage: React.FC<{ failedOnly?: boolean }> = ({ failedOnly = false }) => {
  const [params, setParams] = useSearchParams();
  const requestedPage = Number(params.get('page') || 1);
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const requestedStatus = params.get('status') as JobStatus | null;
  const status = failedOnly ? 'FAILED' : requestedStatus && statuses.includes(requestedStatus) ? requestedStatus : undefined;
  const queryClient = useQueryClient();
  const { data: stats, error: statsError } = useQuery({
    queryKey: ['jobStats'], queryFn: apiClient.getJobStats, refetchInterval: 3000,
  });
  const { data, isLoading, error } = useQuery({
    queryKey: ['jobs', page, status],
    queryFn: () => apiClient.listJobs(page, status),
    refetchInterval: 3000,
  });
  const requeue = useMutation({
    mutationFn: apiClient.requeueJob,
    onSuccess: (job) => {
      queryClient.setQueryData(['job', job.id], job);
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['jobStats'] });
    },
  });
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 20));
  useEffect(() => {
    if (data && page > totalPages) {
      const updated = new URLSearchParams(params);
      updated.set('page', String(totalPages));
      setParams(updated, { replace: true });
    }
  }, [data, page, totalPages, params, setParams]);

  const changePage = (next: number) => {
    const updated = new URLSearchParams(params);
    updated.set('page', String(next));
    setParams(updated);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">{failedOnly ? 'Failed Jobs' : 'Job Dashboard'}</h1>
        <Link to="/submit" className="rounded bg-indigo-600 px-4 py-2">Upload PDF</Link>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          ['Total Jobs', stats?.total_jobs],
          ['Processing', stats?.status_counts.PROCESSING],
          ['Failed Jobs', stats?.dlq_count],
        ].map(([label, value]) => (
          <div key={label} className="p-4 bg-slate-800/50 border border-slate-700 rounded-lg">
            <p className="text-sm text-slate-400">{label}</p>
            <p className="text-3xl font-bold mt-1">{value ?? '—'}</p>
          </div>
        ))}
      </div>
      {statsError && <p role="alert" className="text-rose-400">Unable to load job counts.</p>}
      {!failedOnly && (
        <label className="flex items-center gap-3">
          Status
          <select className="rounded bg-slate-800 border border-slate-700 px-3 py-2" value={status ?? ''} onChange={(event) => {
            const updated = new URLSearchParams(params);
            updated.set('page', '1');
            if (event.target.value) updated.set('status', event.target.value);
            else updated.delete('status');
            setParams(updated);
          }}>
            <option value="">All statuses</option>
            {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      )}
      {failedOnly && <p className="text-slate-400">Review errors and retry jobs after addressing the cause.</p>}
      {isLoading && <p role="status">Loading jobs...</p>}
      {error && <p role="alert" className="text-rose-400">Unable to load jobs. Check the API connection.</p>}
      {requeue.error && <p role="alert" className="text-rose-400">Could not requeue this job. Refresh its details and try again.</p>}
      {data && !error && (
        <>
          {data.items.length === 0 ? (
            <p className="rounded border border-slate-700 p-6 text-slate-400">
              {status ? 'No jobs with this status.' : 'No jobs yet. Upload a PDF to get started.'}
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-700">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Jobs, newest first</caption>
                <thead className="bg-slate-800 text-slate-300">
                  <tr>{['Job', 'Status', 'Progress', 'Failed attempts', 'Created', 'Actions'].map((heading) => <th key={heading} scope="col" className="p-3">{heading}</th>)}</tr>
                </thead>
                <tbody>
                  {data.items.map((job) => (
                    <tr key={job.id} className="border-t border-slate-700 align-top">
                      <td className="p-3 max-w-xs">
                        <Link to={`/jobs/${job.id}`} className="text-indigo-400 hover:underline break-all">#{job.id} {job.filename}</Link>
                        {job.error_message && <p className="mt-1 text-rose-400 break-words">{job.error_message}</p>}
                      </td>
                      <td className="p-3">{job.status}</td>
                      <td className="p-3">
                        <progress aria-label={`Job ${job.id} progress`} max={100} value={job.progress} className="w-24" />
                        <p>{job.progress}%</p>
                      </td>
                      <td className="p-3">{job.retry_count} / {job.max_retries}</td>
                      <td className="p-3 whitespace-nowrap"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time></td>
                      <td className="p-3 space-y-2">
                        <Link to={`/jobs/${job.id}`} className="block text-indigo-400 hover:underline">Details</Link>
                        {job.status === 'COMPLETED' && <a href={`/api/v1/jobs/${job.id}/result`} className="block text-indigo-400 hover:underline">Download ZIP</a>}
                        {job.status === 'FAILED' && <button aria-label={`Retry job ${job.id}`} disabled={requeue.isPending} className="rounded bg-indigo-600 px-3 py-1 disabled:opacity-50" onClick={() => requeue.mutate(job.id)}>{requeue.isPending && requeue.variables === job.id ? 'Requeueing...' : 'Retry'}</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-400">{data.total} matching jobs · Page {page} of {totalPages}</p>
            <div className="flex gap-2">
              <button disabled={page <= 1} className="rounded bg-slate-800 px-3 py-2 disabled:opacity-40" onClick={() => changePage(page - 1)}>Previous</button>
              <button disabled={page >= totalPages} className="rounded bg-slate-800 px-3 py-2 disabled:opacity-40" onClick={() => changePage(page + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
