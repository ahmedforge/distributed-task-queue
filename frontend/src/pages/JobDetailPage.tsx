import React from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { PageState } from '../components/PageState';
import { StatusBadge } from '../components/StatusBadge';
import { useJobProgress } from '../hooks/useJobProgress';

export const JobDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const jobId = Number(id);
  const queryClient = useQueryClient();
  const { data: job, isLoading, error, connection } = useJobProgress(jobId);
  const requeue = useMutation({
    mutationFn: () => apiClient.requeueJob(jobId),
    onSuccess: (updated) => {
      queryClient.setQueryData(['job', jobId], updated);
      queryClient.invalidateQueries({ queryKey: ['jobStats'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  if (!Number.isInteger(jobId) || jobId < 1) return <PageState kind="error" title="Invalid job ID" description="Choose a job from the dashboard." />;
  if (isLoading) return <PageState kind="loading" title="Loading job" description="Connecting to progress updates." />;
  if (!job) return <PageState kind="error" title="Unable to load this job" description="Check the job ID and API connection." />;

  return (
    <div className="space-y-4 rounded-xl border border-border bg-panel p-5 sm:p-8">
      <h1 className="text-2xl font-bold break-words">Job #{job.id}: {job.filename}</h1>
      <p>Status: <StatusBadge status={job.status} /></p>
      {job.status !== 'COMPLETED' && job.status !== 'FAILED' && (
        <p role="status" className="text-sm text-muted">
          {connection === 'live' ? 'Live updates connected' : 'Reconnecting to live updates; refreshing status automatically'}
        </p>
      )}
      {error && <p role="alert" className="text-danger">Unable to refresh status. Showing the last known state.</p>}
      <progress className="w-full" max={100} value={job.progress} aria-label="Rendering progress" />
      <p>{job.progress}% processed</p>
      <p>Failed attempts: {job.retry_count} / {job.max_retries}</p>
      {job.error_message && <p role="alert" className="text-danger">{job.error_message}</p>}
      {job.status === 'COMPLETED' && (
        <a className="inline-block bg-indigo-600 text-white px-4 py-2 rounded" href={`/api/v1/jobs/${job.id}/result`}>
          Download rendered pages (ZIP)
        </a>
      )}
      {job.status === 'FAILED' && (
        <button className="bg-indigo-600 text-white px-4 py-2 rounded" disabled={requeue.isPending} onClick={() => requeue.mutate()}>
          {requeue.isPending ? 'Requeueing...' : 'Retry job'}
        </button>
      )}
      {requeue.error && <p role="alert">Could not requeue this job.</p>}
    </div>
  );
};
