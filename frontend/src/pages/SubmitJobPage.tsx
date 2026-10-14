import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { PageState } from '../components/PageState';
import { apiClient, ApiError } from '../api/client';

export const SubmitJobPage: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [failedJobId, setFailedJobId] = useState<number | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setError('');
    setFailedJobId(undefined);
    try {
      const job = await apiClient.uploadJob(file, idempotencyKey || undefined);
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      if (err instanceof ApiError) setFailedJobId(err.jobId);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-md mx-auto bg-panel p-6 rounded-lg border border-border">
      <h2 className="text-xl font-bold mb-4">Submit PDF Render Task</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="pdf-file" className="block text-sm text-muted mb-1">PDF file</label>
          <input
            id="pdf-file"
            type="file"
            accept="application/pdf,.pdf"
            className="w-full bg-panel border border-border rounded px-3 py-2 text-fg"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(''); setFailedJobId(undefined); }}
            required
          />
        </div>
        <div>
          <label htmlFor="idempotency-key" className="block text-sm text-muted mb-1">Idempotency Key (Optional)</label>
          <input
            id="idempotency-key"
            type="text"
            className="w-full bg-panel border border-border rounded px-3 py-2 text-fg"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
            placeholder="unique-uuid-key"
          />
        </div>
        {error && <PageState kind="error" title="Upload could not finish" description={error} action={failedJobId ? <Link to={`/jobs/${failedJobId}`} className="text-primary hover:underline">Open saved job #{failedJobId} to retry</Link> : undefined} />}
        {submitting && <p role="status" className="text-muted">Uploading your PDF. Processing begins after the upload finishes.</p>}
        <p className="text-sm text-muted">Up to 20 MiB and 50 pages. Downloads contain one PNG per page.</p>
        <button
          disabled={submitting || !file}
          type="submit"
          className="w-full bg-indigo-600 hover:bg-indigo-500 py-2 rounded font-semibold text-white transition-colors disabled:opacity-50"
        >
          {submitting ? 'Uploading...' : 'Upload and Render'}
        </button>
      </form>
    </div>
  );
};
