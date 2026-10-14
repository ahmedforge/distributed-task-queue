import React, { lazy, Suspense, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useJobStats } from '../hooks/useJobStats';
import { PageState } from '../components/PageState';
import { StatusBadge } from '../components/StatusBadge';
import { apiClient, type JobStatus } from '../api/client';

const DashboardCharts = lazy(() => import('../components/DashboardCharts').then((module) => ({ default: module.DashboardCharts })));

const statuses: JobStatus[] = ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'];

export const JobListPage: React.FC<{ failedOnly?: boolean }> = ({ failedOnly = false }) => {
  const [params, setParams] = useSearchParams();
  const requestedPage = Number(params.get('page') || 1);
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const requestedStatus = params.get('status') as JobStatus | null;
  const status = failedOnly ? 'FAILED' : requestedStatus && statuses.includes(requestedStatus) ? requestedStatus : undefined;
  const queryClient = useQueryClient();
  const { data: stats, error: statsError, isLoading: statsLoading } = useJobStats();
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
        <Link to="/submit" className="rounded bg-indigo-600 text-white px-4 py-2">Upload PDF</Link>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          ['Total Jobs', stats?.total_jobs],
          ['Waiting in Queue', stats?.queue_depth],
          ['Processing', stats?.status_counts.PROCESSING],
          ['Failed Jobs', stats?.dlq_count],
        ].map(([label, value]) => (
          <div key={label} className="p-4 bg-panel border border-border rounded-lg">
            <p className="text-sm text-muted">{label}</p>
            <p className="text-3xl font-bold mt-1">{value ?? '—'}</p>
          </div>
        ))}
      </div>
      {statsError && <p role="alert" className="text-danger">Unable to load job counts.</p>}
      {!failedOnly && <Suspense fallback={<PageState kind="loading" title="Loading charts" />}><DashboardCharts stats={stats} loading={statsLoading} error={statsError} /></Suspense>}
      {!failedOnly && (
        <label className="flex items-center gap-3">
          Status
          <select className="rounded bg-panel-alt border border-border px-3 py-2" value={status ?? ''} onChange={(event) => {
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
      {failedOnly && <p className="text-muted">Review errors and retry jobs after addressing the cause.</p>}
      {isLoading && <PageState kind="loading" title="Loading jobs" description="Fetching the latest job states." />}
      {error && <PageState kind="error" title="Unable to refresh jobs" description="Check the API connection. Previously loaded jobs remain below." />}
      {requeue.error && <p role="alert" className="text-danger">Could not requeue this job. Refresh its details and try again.</p>}
      {data && (
        <>
          {data.items.length === 0 ? (
            <PageState kind="empty" title={status ? 'No jobs with this status' : 'No jobs yet'} description={failedOnly ? 'Failed jobs will appear here with their error details.' : 'Upload a PDF to start rendering pages.'} action={<Link to="/submit" className="text-primary hover:underline">Upload a PDF</Link>} />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Jobs, newest first</caption>
                <thead className="bg-panel-alt text-muted">
                  <tr>{['Job', 'Status', 'Progress', 'Failed attempts', 'Created', 'Actions'].map((heading) => <th key={heading} scope="col" className="p-3">{heading}</th>)}</tr>
                </thead>
                <tbody>
                  {data.items.map((job) => (
                    <tr key={job.id} className="border-t border-border align-top">
                      <td className="p-3 max-w-xs">
                        <Link to={`/jobs/${job.id}`} className="text-primary hover:underline break-all">#{job.id} {job.filename}</Link>
                        {job.error_message && <p className="mt-1 text-danger break-words">{job.error_message}</p>}
                      </td>
                      <td className="p-3"><StatusBadge status={job.status} /></td>
                      <td className="p-3">
                        <progress aria-label={`Job ${job.id} progress`} max={100} value={job.progress} className="w-24" />
                        <p>{job.progress}%</p>
                      </td>
                      <td className="p-3">{job.retry_count} / {job.max_retries}</td>
                      <td className="p-3 whitespace-nowrap"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time></td>
                      <td className="p-3 space-y-2">
                        <Link to={`/jobs/${job.id}`} className="block text-primary hover:underline">Details</Link>
                        {job.status === 'COMPLETED' && <a href={`/api/v1/jobs/${job.id}/result`} className="block text-primary hover:underline">Download ZIP</a>}
                        {job.status === 'FAILED' && <button aria-label={`Retry job ${job.id}`} disabled={requeue.isPending} className="rounded bg-indigo-600 text-white px-3 py-1 disabled:opacity-50" onClick={() => requeue.mutate(job.id)}>{requeue.isPending && requeue.variables === job.id ? 'Requeueing...' : 'Retry'}</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted">{data.total} matching jobs · Page {page} of {totalPages}</p>
            <div className="flex gap-2">
              <button disabled={page <= 1} className="rounded bg-panel-alt px-3 py-2 disabled:opacity-40" onClick={() => changePage(page - 1)}>Previous</button>
              <button disabled={page >= totalPages} className="rounded bg-panel-alt px-3 py-2 disabled:opacity-40" onClick={() => changePage(page + 1)}>Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
