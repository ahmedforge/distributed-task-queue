import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../api/client';

export const SubmitJobPage: React.FC = () => {
  const [filename, setFilename] = useState('');
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!filename) return;
    const job = await apiClient.createJob({
      filename,
      idempotency_key: idempotencyKey || null,
    });
    navigate(`/jobs/${job.id}`);
  };

  return (
    <div className="max-w-md mx-auto bg-slate-800/50 p-6 rounded-lg border border-slate-700">
      <h2 className="text-xl font-bold mb-4">Submit PDF Render Task</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm text-slate-300 mb-1">Filename</label>
          <input
            type="text"
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-slate-100"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder="document.pdf"
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
        <button
          type="submit"
          className="w-full bg-indigo-600 hover:bg-indigo-500 py-2 rounded font-semibold text-white transition-colors"
        >
          Enqueue Job
        </button>
      </form>
    </div>
  );
};
