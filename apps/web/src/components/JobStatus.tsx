import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { terminalJobStates } from '../api/media';
import type { Job } from '../api/types';
import { ErrorNotice, Spinner, StatusBadge } from './ui';
export function JobStatus({
  id,
  query = false,
  onUpdate,
  onUnavailable,
}: {
  id: string;
  query?: boolean;
  onUpdate?: (job: Job) => void;
  onUnavailable?: () => void;
}) {
  const result = useQuery({
    queryKey: [query ? 'query-job' : 'job', id],
    queryFn: ({ signal }) =>
      api.get<Job>(`/${query ? 'query-jobs' : 'jobs'}/${encodeURIComponent(id)}`, signal),
    refetchInterval: (q) =>
      terminalJobStates.has(q.state.data?.status || '')
        ? false
        : document.hidden
          ? 15000
          : q.state.dataUpdateCount > 5
            ? 5000
            : 2000,
    retry: 1,
  });
  useEffect(() => {
    if (result.data) onUpdate?.(result.data);
  }, [result.data, onUpdate]);
  useEffect(() => {
    if (
      result.error instanceof ApiError &&
      (result.error.status === 404 || (query && result.error.status === 410))
    )
      onUnavailable?.();
  }, [result.error, query, onUnavailable]);
  return (
    <div className="job-status" aria-live="polite">
      {result.isPending ? (
        <Spinner text="Checking task…" />
      ) : (
        result.data && (
          <>
            <div className="job-line">
              <StatusBadge status={result.data.status} />
              <span>
                {result.data.stage?.replaceAll('_', ' ') || result.data.message || 'Task status'}
              </span>
            </div>
            {typeof result.data.progress === 'number' && (
              <progress value={result.data.progress} max={100} />
            )}
            <small>
              {result.data.cleanupStatus &&
                `Temporary files: ${result.data.cleanupStatus.toLowerCase()}`}
            </small>
            {result.data.error && (
              <ErrorNotice error={result.data.error.message || 'This task could not complete.'} />
            )}
          </>
        )
      )}
      <ErrorNotice error={result.error} onRetry={() => void result.refetch()} />
      {result.error && (
        <p className="muted small">
          The last known task state is preserved. This connection error does not mean processing
          failed.
        </p>
      )}
    </div>
  );
}
