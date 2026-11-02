import React from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { PageState } from '../components/PageState';
import { StatusBadge } from '../components/StatusBadge';
import { useJobProgress } from '../hooks/useJobProgress';

export const JobDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const jobId = Number(id);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: job, isLoading, error, connection } = useJobProgress(jobId);
  const requeue = useMutation({
    mutationFn: () => apiClient.requeueJob(jobId),
    onSuccess: (updated) => {
      queryClient.setQueryData(['job', jobId], updated);
      queryClient.invalidateQueries({ queryKey: ['jobStats'] });
      queryClient.invalidateQueries({ queryKey: ['accountUsage'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const deletion = useMutation({
    mutationFn: () => apiClient.deleteJob(jobId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['jobStats'] });
      queryClient.invalidateQueries({ queryKey: ['accountUsage'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.removeQueries({ queryKey: ['job', jobId] });
      toast.success(result.cleanup_pending ? 'Job removed. File cleanup will retry automatically.' : 'Job and saved files deleted.');
      navigate('/');
    },
  });

  if (!Number.isInteger(jobId) || jobId < 1) return <PageState kind="error" title="Invalid job ID" description="Choose a job from the dashboard." />;
  if (isLoading) return <PageState kind="loading" title="Loading job" description="Connecting to progress updates." />;
  if (!job) return <PageState kind="error" title="Unable to load this job" description="Check the job ID and API connection." />;

  return <div className="detail-layout">
    <Link to="/" className="breadcrumb">← All jobs</Link>
    <div className="page-heading"><div><p className="job-reference mb-2">JOB #{String(job.id).padStart(4, '0')}</p><h1>{job.filename}</h1></div>
      {job.status === 'COMPLETED' && <a className="button button-primary" href={`/api/v1/jobs/${job.id}/result`}>Download ZIP ↓</a>}
      {job.status === 'FAILED' && <button className="button button-primary" disabled={requeue.isPending || deletion.isPending} onClick={() => requeue.mutate()}>{requeue.isPending ? 'Retrying…' : 'Retry job'}</button>}
    </div>
    <div className="form-panel">
      <div className="detail-progress"><StatusBadge status={job.status} /><span className="numeric">{job.progress}%</span></div>
      <progress max={100} value={job.progress} aria-label="Rendering progress" />
      <dl className="detail-meta"><div><dt>Created</dt><dd><time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time></dd></div><div><dt>Failed attempts</dt><dd>{job.retry_count} / {job.max_retries}</dd></div><div><dt>Last updated</dt><dd><time dateTime={job.updated_at}>{new Date(job.updated_at).toLocaleTimeString()}</time></dd></div></dl>
      {job.status !== 'COMPLETED' && job.status !== 'FAILED' && <p role="status" className="field-note">{connection === 'live' ? 'Receiving live progress updates.' : 'Reconnecting. Status refreshes automatically.'}</p>}
      {job.expires_at && <p className="field-note">Job and files expire <time dateTime={job.expires_at}>{new Date(job.expires_at).toLocaleString()}</time>. {job.status === 'COMPLETED' ? 'Download your ZIP before then.' : 'Retry before then to keep working on this job.'}</p>}
      {job.status === 'COMPLETED' && <p className="field-note">All pages have been rendered. Your ZIP is ready to download.</p>}
      {error && <p role="alert" className="notice">Unable to refresh status. Showing the last known state.</p>}
      {job.error_message && <p role="alert" className="notice">{job.error_message}</p>}
      {requeue.error && <p role="alert" className="notice">{requeue.error.message}</p>}
    </div>
    {(job.status === 'COMPLETED' || job.status === 'FAILED') && <div className="form-footer"><p>Delete the original PDF, rendered files, and job record.</p><button className="button button-danger" disabled={deletion.isPending || requeue.isPending} onClick={() => {
      if (window.confirm('Delete this job, its original PDF, and all rendered files? This cannot be undone.')) deletion.mutate();
    }}>{deletion.isPending ? 'Deleting…' : 'Delete job'}</button></div>}
    {deletion.error && <p role="alert" className="notice mt-4">{deletion.error.message}</p>}
  </div>;
};
