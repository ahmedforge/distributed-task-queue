import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';

export interface QueueSample { time: string; waiting: number | null; active: number | null }

export function useJobStats() {
  const client = useQueryClient();
  return useQuery({
    queryKey: ['jobStats'], refetchInterval: 3000,
    queryFn: async () => {
      const stats = await apiClient.getJobStats();
      client.setQueryData<QueueSample[]>(['queueHistory'], (history = []) => [
        ...history, { time: stats.sampled_at, waiting: stats.queue_depth, active: stats.queue_counts?.active ?? null },
      ].slice(-60));
      return stats;
    },
  });
}
