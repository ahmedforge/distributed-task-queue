import React from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';

export const JobDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const jobId = Number(id);
  const queryClient = useQueryClient();
  const { data: job, isLoading, error } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => apiClient.getJob(jobId),
    enabled: Number.isInteger(jobId) && jobId > 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'COMPLETED' || status === 'FAILED' ? false : 1000;
    },
  });
  const requeue = useMutation({
    mutationFn: () => apiClient.requeueJob(jobId),
    onSuccess: (updated) => {
      queryClient.setQueryData(['job', jobId], updated);
      queryClient.invalidateQueries({ queryKey: ['jobStats'] });
    },
  });

  if (!Number.isInteger(jobId) || jobId < 1) return <p>Invalid job ID.</p>;
  if (isLoading) return <p>Loading job...</p>;
  if (error || !job) return <p role="alert">Unable to load this job.</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Job #{job.id}: {job.filename}</h1>
      <p>Status: {job.status}</p>
      <progress className="w-full" max={100} value={job.progress} aria-label="Rendering progress" />
      <p>{job.progress}% processed</p>
      <p>Failed attempts: {job.retry_count} / {job.max_retries}</p>
      {job.error_message && <p role="alert" className="text-rose-400">{job.error_message}</p>}
      {job.status === 'COMPLETED' && (
        <a className="inline-block bg-indigo-600 px-4 py-2 rounded" href={`/api/v1/jobs/${job.id}/result`}>
          Download rendered pages (ZIP)
        </a>
      )}
      {job.status === 'FAILED' && (
        <button className="bg-indigo-600 px-4 py-2 rounded" disabled={requeue.isPending} onClick={() => requeue.mutate()}>
          {requeue.isPending ? 'Requeueing...' : 'Retry job'}
        </button>
      )}
      {requeue.error && <p role="alert">Could not requeue this job.</p>}
    </div>
  );
};
