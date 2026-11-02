import { AccountUsage } from '../components/AccountUsage';
import React, { lazy, Suspense, useEffect, useState } from 'react';
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
  const [activityOpen, setActivityOpen] = useState(false);
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

  return <div>
    <div className="page-heading">
      <div><h1>{failedOnly ? 'Failed jobs' : 'Jobs'}</h1><p>{failedOnly ? 'Review errors and retry after fixing the cause.' : 'PDF rendering jobs in your account.'}</p></div>
      <Link to="/submit" className="button button-primary">Upload PDF <span aria-hidden="true">↗</span></Link>
    </div>
    <div className="stats-strip" aria-label="Job summary">
      {[
        ['Total jobs', stats?.total_jobs],
        [stats?.queue_scope === 'account' ? 'Pending' : 'Waiting', stats?.queue_scope === 'account' ? stats.status_counts.PENDING : stats?.queue_depth],
        ['Processing', stats?.status_counts.PROCESSING],
        ['Failed', stats?.dlq_count],
      ].map(([label, value]) => <div key={label} className="stat"><p className="stat-label">{label}</p><p className="stat-value">{value ?? '—'}</p></div>)}
    </div>
    {statsError && <p role="alert" className="notice">Unable to load job counts.</p>}
    <AccountUsage />
    <div className="job-toolbar">
      <span className="text-xs text-muted">{data ? `${data.total} ${data.total === 1 ? 'job' : 'jobs'}` : 'Loading…'} · newest first</span>
      {!failedOnly && <label>Status<select value={status ?? ''} onChange={(event) => {
        const updated = new URLSearchParams(params); updated.set('page', '1');
        if (event.target.value) updated.set('status', event.target.value); else updated.delete('status');
        setParams(updated);
      }}><option value="">All statuses</option>{statuses.map(item => <option key={item} value={item}>{item.charAt(0) + item.slice(1).toLowerCase()}</option>)}</select></label>}
    </div>
    {isLoading && <PageState kind="loading" title="Loading jobs" description="Fetching the latest job states." />}
    {error && <PageState kind="error" title="Unable to refresh jobs" description="Check the API connection. Previously loaded jobs remain below." />}
    {requeue.error && <p role="alert" className="notice">Could not retry this job. Refresh its details and try again.</p>}
    {data && <>
      {data.items.length === 0 ? <PageState kind="empty" title={status ? 'No jobs with this status' : 'No jobs yet'} description={failedOnly ? 'Jobs that need attention will appear here.' : status ? 'No jobs currently match this filter.' : 'Upload a PDF to render each page as a PNG.'} action={!status ? <Link to="/submit" className="text-link text-sm">Upload your first PDF</Link> : undefined} /> :
        <div className="table-frame" tabIndex={0} role="region" aria-label="Job list, scroll to view all columns"><table className="jobs-table">
          <caption className="sr-only">Jobs, newest first</caption>
          <thead><tr>{['Document', 'Status', 'Progress', 'Failed attempts', 'Created', ''].map(heading => <th key={heading} scope="col">{heading || <span className="sr-only">Actions</span>}</th>)}</tr></thead>
          <tbody>{data.items.map(job => <tr key={job.id}>
            <td><Link to={`/jobs/${job.id}`} className="file-name">{job.filename}</Link><span className="job-reference">#{String(job.id).padStart(4, '0')}</span></td>
            <td><StatusBadge status={job.status} /></td>
            <td><div className="w-20"><progress aria-label={`Job ${job.id} progress`} max={100} value={job.progress} /><span className="numeric block mt-1">{job.progress}%</span></div></td>
            <td className="numeric">{job.retry_count} / {job.max_retries}</td>
            <td className="text-muted whitespace-nowrap text-xs"><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}<span className="block numeric mt-1">{new Date(job.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></time></td>
            <td><div className="job-actions"><Link to={`/jobs/${job.id}`}>View<span className="sr-only"> job {job.id}</span></Link>
              {job.status === 'COMPLETED' && <a href={`/api/v1/jobs/${job.id}/result`}>ZIP<span className="sr-only"> for job {job.id}</span> ↓</a>}
              {job.status === 'FAILED' && <button aria-label={`Retry job ${job.id}`} disabled={requeue.isPending} onClick={() => requeue.mutate(job.id)}>{requeue.isPending && requeue.variables === job.id ? 'Retrying…' : 'Retry'}</button>}
            </div></td>
          </tr>)}</tbody>
        </table></div>}
      <div className="table-footer"><span>Page {page} of {totalPages}</span><div className="flex gap-2"><button disabled={page <= 1} className="button" onClick={() => changePage(page - 1)}>Previous</button><button disabled={page >= totalPages} className="button" onClick={() => changePage(page + 1)}>Next</button></div></div>
    </>}
    {!failedOnly && <details className="activity-section" onToggle={event => setActivityOpen(event.currentTarget.open)}><summary>Activity · last 15 minutes</summary>{activityOpen && <Suspense fallback={<PageState kind="loading" title="Loading activity" />}><DashboardCharts stats={stats} loading={statsLoading} error={statsError} /></Suspense>}</details>}
  </div>;
};
