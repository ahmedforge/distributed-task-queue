export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export interface JobRead {
  id: number;
  filename: string;
  status: JobStatus;
  progress: number;
  retry_count: number;
  max_retries: number;
  idempotency_key?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobListResponse {
  items: JobRead[];
  total: number;
  page: number;
  page_size: number;
}

export interface JobStatsResponse {
  total_jobs: number;
  status_counts: Record<JobStatus, number>;
  dlq_count: number;
  queue_name: string;
}

export interface JobProgressUpdate {
  job_id: number;
  status: JobStatus;
  progress: number;
  updated_at: string;
}

const API_BASE = '/api/v1/jobs';

export const apiClient = {
  listJobs: async (page: number, status?: JobStatus): Promise<JobListResponse> => {
    const params = new URLSearchParams({ page: String(page), page_size: '20' });
    if (status) params.set('status', status);
    const res = await fetch(`${API_BASE}?${params}`);
    if (!res.ok) throw new Error('Failed to fetch jobs');
    return res.json();
  },
  uploadJob: async (file: File, idempotencyKey?: string): Promise<JobRead> => {
    const form = new FormData();
    form.append('file', file);
    if (idempotencyKey) form.append('idempotency_key', idempotencyKey);
    const res = await fetch(`${API_BASE}/upload`, { method: 'POST', body: form });
    if (!res.ok) {
      const error = await res.json().catch(() => null);
      throw new Error(typeof error?.detail === 'string' ? error.detail : 'Failed to upload PDF');
    }
    return res.json();
  },

  getJobStats: async (): Promise<JobStatsResponse> => {
    const res = await fetch(`${API_BASE}/stats`);
    if (!res.ok) throw new Error('Failed to fetch job stats');
    return res.json();
  },

  getDLQJobs: async (): Promise<JobRead[]> => {
    const res = await fetch(`${API_BASE}/dlq`);
    if (!res.ok) throw new Error('Failed to fetch DLQ jobs');
    return res.json();
  },

  getJob: async (jobId: number): Promise<JobRead> => {
    const res = await fetch(`${API_BASE}/${jobId}`);
    if (!res.ok) throw new Error('Failed to fetch job');
    return res.json();
  },

  requeueJob: async (jobId: number): Promise<JobRead> => {
    const res = await fetch(`${API_BASE}/${jobId}/requeue`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to requeue job');
    return res.json();
  },
};
