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

export interface JobCreate {
  filename: string;
  max_retries?: number;
  idempotency_key?: string | null;
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
  createJob: async (data: JobCreate): Promise<JobRead> => {
    const res = await fetch(`${API_BASE}/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error('Failed to create job');
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
