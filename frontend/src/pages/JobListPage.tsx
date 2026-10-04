import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';

export const JobListPage: React.FC = () => {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['jobStats'],
    queryFn: apiClient.getJobStats,
    refetchInterval: 3000,
  });

  if (isLoading) return <div className="text-slate-400">Loading metrics...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">System Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 bg-slate-800/50 border border-slate-700 rounded-lg">
          <p className="text-sm text-slate-400">Total Jobs</p>
          <p className="text-3xl font-extrabold mt-1">{stats?.total_jobs ?? 0}</p>
        </div>
        <div className="p-4 bg-slate-800/50 border border-slate-700 rounded-lg">
          <p className="text-sm text-slate-400">Queue Name</p>
          <p className="text-xl font-semibold mt-1 text-indigo-400">{stats?.queue_name}</p>
        </div>
        <div className="p-4 bg-slate-800/50 border border-slate-700 rounded-lg">
          <p className="text-sm text-slate-400">DLQ Count (Failed)</p>
          <p className="text-3xl font-extrabold text-rose-400 mt-1">{stats?.dlq_count ?? 0}</p>
        </div>
      </div>
    </div>
  );
};
