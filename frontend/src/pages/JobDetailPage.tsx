import React from 'react';
import { useParams } from 'react-router-dom';

export const JobDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Job #{id} Progress</h1>
      <p className="text-slate-400">WebSocket progress hook will connect here in Phase 6.</p>
    </div>
  );
};
