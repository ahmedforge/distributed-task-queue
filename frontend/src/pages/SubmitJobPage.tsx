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

  return <div className="upload-layout">
    <div className="page-heading"><div><h1>Upload a PDF</h1><p>Each page is rendered as a PNG and bundled into a ZIP.</p></div></div>
    <form onSubmit={handleSubmit} className="form-panel">
      <label htmlFor="pdf-file" className="form-label">Document</label>
      <div className="file-field"><input id="pdf-file" type="file" accept="application/pdf,.pdf" onChange={e => { setFile(e.target.files?.[0] ?? null); setError(''); setFailedJobId(undefined); }} required aria-describedby="file-help" /></div>
      <p id="file-help" className="field-note mb-6">PDF files only. Up to 20 MiB and 50 pages. Encrypted PDFs aren't supported.</p>
      <details className="form-advanced"><summary>Duplicate submission protection</summary>
        <label htmlFor="idempotency-key" className="form-label">Submission key <span className="text-muted font-normal">(optional)</span></label>
        <input id="idempotency-key" className="field" value={idempotencyKey} onChange={e => setIdempotencyKey(e.target.value)} aria-describedby="key-help" />
        <p id="key-help" className="field-note">Reuse a key to return the same saved job instead of uploading it twice. Use a new key for a different document.</p>
      </details>
      {error && <div className="mt-5"><PageState kind="error" title="Upload could not finish" description={error} action={failedJobId ? <Link to={`/jobs/${failedJobId}`} className="text-link">Open saved job #{failedJobId}</Link> : undefined} /></div>}
      {submitting && <p role="status" className="field-note mt-5">Uploading your document…</p>}
      <div className="form-footer"><p>Processing continues after you leave this page.</p><button disabled={submitting || !file} type="submit" className="button button-primary">{submitting ? 'Uploading…' : 'Upload & render'}</button></div>
    </form>
    <Link to="/" className="breadcrumb mt-5">← Back to jobs</Link>
  </div>;
};
