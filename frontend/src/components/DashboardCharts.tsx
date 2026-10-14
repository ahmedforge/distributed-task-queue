import { useQuery } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { JobStatsResponse } from '../api/client';
import type { QueueSample } from '../hooks/useJobStats';
import { PageState } from './PageState';

const timeLabel = (value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const minuteLabel = (value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const tooltipStyle = { background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--fg)' };

export function DashboardCharts({ stats, loading, error }: { stats?: JobStatsResponse; loading: boolean; error: Error | null }) {
  const { data: history = [] } = useQuery<QueueSample[]>({
    queryKey: ['queueHistory'], queryFn: async () => [], enabled: false, initialData: [], gcTime: Infinity,
  });
  if (!stats) return <PageState kind={loading ? 'loading' : 'error'} title={loading ? 'Loading metrics' : 'Metrics unavailable'} description={error ? 'Check the API connection. Metrics will refresh automatically.' : undefined} />;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="min-w-0 rounded-xl border border-border bg-panel p-4 sm:p-5">
        <h2 className="font-semibold">Queue depth</h2>
        <p className="text-sm text-muted mt-1 mb-4">Samples since opening the dashboard. Waiting includes delayed retries.</p>
        {stats.queue_error && <p role="alert" className="text-sm text-danger mb-3">{stats.queue_error}</p>}
        {history.some((sample) => sample.waiting !== null) ? (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={history} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="time" tickFormatter={timeLabel} minTickGap={40} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
              <Tooltip labelFormatter={(label) => timeLabel(String(label))} contentStyle={tooltipStyle} />
              <Legend />
              <Line type="linear" dataKey="waiting" name="Waiting" stroke="var(--primary)" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} connectNulls={false} />
              <Line type="linear" dataKey="active" name="Active" stroke="var(--success)" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : <PageState kind="empty" title="Waiting for queue metrics" description="Queue data will appear when Redis is available." />}
        <p className="text-xs text-muted mt-2">Up to 60 samples, refreshed every 3 seconds. History resets on reload.</p>
      </section>
      <section className="min-w-0 rounded-xl border border-border bg-panel p-4 sm:p-5">
        <h2 className="font-semibold">Jobs completed per minute</h2>
        <p className="text-sm text-muted mt-1 mb-4">Last 15 minutes from PostgreSQL. The current minute is still in progress.</p>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={stats.completed_per_minute} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="minute" tickFormatter={minuteLabel} minTickGap={30} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
            <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
            <Tooltip labelFormatter={(label) => minuteLabel(String(label))} contentStyle={tooltipStyle} />
            <Bar dataKey="completed" name="Completed jobs" fill="var(--primary)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
        {!stats.completed_per_minute.some((point) => point.completed) && <p className="text-xs text-muted mt-2">No jobs completed in this window.</p>}
      </section>
    </div>
  );
}
