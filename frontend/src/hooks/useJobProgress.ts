import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { apiClient, type JobRead, type JobProgressUpdate } from '../api/client';

const terminal = (status?: string) => status === 'COMPLETED' || status === 'FAILED';
const validUpdate = (value: unknown, jobId: number): value is JobProgressUpdate => {
  if (!value || typeof value !== 'object') return false;
  const update = value as Partial<JobProgressUpdate>;
  return update.job_id === jobId && typeof update.status === 'string'
    && ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'].includes(update.status)
    && typeof update.progress === 'number' && Number.isInteger(update.progress)
    && update.progress >= 0 && update.progress <= 100
    && typeof update.updated_at === 'string' && Number.isFinite(Date.parse(update.updated_at));
};

type Connection = 'connecting' | 'live' | 'reconnecting' | 'idle';

export function useJobProgress(jobId: number) {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<Connection>('connecting');
  const latest = useRef<JobProgressUpdate | null>(null);
  const enabled = Number.isSafeInteger(jobId) && jobId > 0;
  const query = useQuery({
    queryKey: ['job', jobId],
    enabled,
    retry: 1,
    queryFn: async () => {
      let fetched = await apiClient.getJob(jobId);
      const incoming = latest.current;
      if (incoming?.job_id === jobId && Date.parse(incoming.updated_at) > Date.parse(fetched.updated_at)) {
        fetched = await apiClient.getJob(jobId);
      }
      const cached = queryClient.getQueryData<JobRead>(['job', jobId]);
      // An in-flight REST snapshot must not overwrite a newer socket event.
      let result = cached && Date.parse(cached.updated_at) > Date.parse(fetched.updated_at) ? cached : fetched;
      const update = latest.current;
      if (update?.job_id === jobId && Date.parse(update.updated_at) > Date.parse(result.updated_at)) {
        result = { ...result, status: update.status, progress: update.progress, updated_at: update.updated_at };
      }
      return result;
    },
    refetchInterval: (state) => terminal(state.state.data?.status) ? false : connection === 'live' ? 5000 : 1000,
  });
  const finished = terminal(query.data?.status);

  const observed = useRef<{ jobId: number; status: string } | null>(null);
  useEffect(() => {
    const job = query.data;
    if (!job) return;
    const previous = observed.current;
    if (previous?.jobId === jobId && !terminal(previous.status) && terminal(job.status)) {
      const options = { id: `job-${jobId}-${job.status}-${job.updated_at}` };
      if (job.status === 'COMPLETED') toast.success(`Job #${jobId} completed. Your ZIP is ready.`, options);
      else toast.error(`Job #${jobId} failed. Review the job details before retrying.`, options);
    }
    observed.current = { jobId, status: job.status };
  }, [jobId, query.data]);

  useEffect(() => {
    if (!enabled || finished) return;
    let stopped = false;
    let attempts = 0;
    let socket: WebSocket | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let connectTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (stopped) return;
      setConnection(attempts ? 'reconnecting' : 'connecting');
      const url = new URL(`/api/v1/jobs/${jobId}/ws`, window.location.href);
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url);
      const current = socket;
      connectTimer = setTimeout(() => current.close(), 5000);
      current.onopen = () => {
        clearTimeout(connectTimer);
        if (stopped) return;
        attempts = 0;
        setConnection('live');
      };
      current.onmessage = (event) => {
        if (stopped) return;
        let update: unknown;
        try { update = JSON.parse(event.data); } catch { return; }
        if (!validUpdate(update, jobId)) return;
        const previous = latest.current;
        if (previous?.job_id === jobId && Date.parse(previous.updated_at) > Date.parse(update.updated_at)) return;
        latest.current = update;
        queryClient.setQueryData<JobRead>(['job', jobId], (cached) => {
          if (!cached || Date.parse(cached.updated_at) > Date.parse(update.updated_at)) return cached;
          return { ...cached, status: update.status, progress: update.progress, updated_at: update.updated_at };
        });
        if (terminal(update.status) || update.status === 'PENDING') {
          // The event is small; REST supplies error details and retry counts.
          queryClient.invalidateQueries({ queryKey: ['job', jobId] });
          queryClient.invalidateQueries({ queryKey: ['jobs'] });
          queryClient.invalidateQueries({ queryKey: ['jobStats'] });
        }
        if (terminal(update.status)) {
          stopped = true;
          current.close();
          setConnection('idle');
        }
      };
      current.onerror = () => current.close();
      current.onclose = (event) => {
        clearTimeout(connectTimer);
        if (stopped) return;
        if (event.code === 4004) {
          stopped = true;
          setConnection('idle');
          return;
        }
        setConnection('reconnecting');
        const delay = Math.min(8000, 1000 * 2 ** Math.min(attempts++, 3));
        retryTimer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearTimeout(connectTimer);
      socket?.close();
    };
  }, [enabled, finished, jobId, queryClient]);

  return { ...query, connection: !enabled || finished ? 'idle' : connection };
}
