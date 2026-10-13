import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../api/client';

export const SubmitJobPage: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      const job = await apiClient.uploadJob(file, idempotencyKey || undefined);
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-md mx-auto bg-slate-800/50 p-6 rounded-lg border border-slate-700">
      <h2 className="text-xl font-bold mb-4">Submit PDF Render Task</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm text-slate-300 mb-1">PDF file</label>
          <input
            type="file"
            accept="application/pdf,.pdf"
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-slate-100"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </div>
        <div>
          <label className="block text-sm text-slate-300 mb-1">Idempotency Key (Optional)</label>
          <input
            type="text"
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-slate-100"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
            placeholder="unique-uuid-key"
          />
        </div>
        {error && <p role="alert" className="text-rose-400">{error}</p>}
        <p className="text-sm text-slate-400">Up to 20 MB and 50 pages. Downloads contain one PNG per page.</p>
        <button
          disabled={submitting}
          type="submit"
          className="w-full bg-indigo-600 hover:bg-indigo-500 py-2 rounded font-semibold text-white transition-colors"
        >
          {submitting ? 'Uploading...' : 'Upload and Render'}
        </button>
      </form>
    </div>
  );
};
