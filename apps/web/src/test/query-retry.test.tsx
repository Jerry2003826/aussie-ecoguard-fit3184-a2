import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, uploadFile } from '../api/client';
import type { Job } from '../api/types';
import { QueryUpload } from '../components/QueryUpload';

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { id: 'alice' } }) }));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  api: { get: vi.fn(), post: vi.fn() },
  uploadFile: vi.fn(),
}));

const queryId = 'saved-query';
const clients: QueryClient[] = [];
function failedJob(overrides = {}): Job {
  const deadline = Math.floor(Date.now() / 1000) + 120;
  return {
    queryId,
    jobId: queryId,
    status: 'FAILED',
    retryable: true,
    cleaned: false,
    cleanupDeadline: deadline,
    resultExpiresAt: deadline,
    error: { code: 'MODEL_BUSY', message: 'The model is temporarily busy.' },
    ...overrides,
  };
}
function renderSaved(job?: Job) {
  sessionStorage.setItem('ecoguard.query.alice.pet', queryId);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  if (job) client.setQueryData(['query-job', queryId], job);
  render(
    <QueryClientProvider client={client}>
      <QueryUpload pet />
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(uploadFile).mockReset();
  sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.useRealTimers();
});

describe('retrying a retained temporary query', () => {
  it('clears an expired saved query on GET 410 so a page reload can start a fresh search', async () => {
    vi.mocked(api.get).mockRejectedValue(
      new ApiError('The query results expired.', 'QUERY_EXPIRED', 410),
    );
    renderSaved();
    expect(screen.getByRole('button', { name: 'Find possible sightings' })).toBeDisabled();
    await screen.findByText(
      'That saved search is no longer available. Start a new search with your file or description.',
      {},
      { timeout: 3000 },
    );
    expect(sessionStorage.getItem('ecoguard.query.alice.pet')).toBeNull();
    expect(screen.getByRole('button', { name: 'Find possible sightings' })).toBeEnabled();
    expect(screen.getByRole('textbox', { name: /Describe the pet/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Cancel search' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry search' })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('retries the same job once, prevents duplicate clicks, and resumes polling without uploading or extending its deadline', async () => {
    const failed = failedJob();
    const queued = { ...failed, status: 'PREPROCESSED', retryable: false, error: undefined };
    vi.mocked(api.get).mockResolvedValueOnce(failed).mockResolvedValue(queued);
    let finish!: (job: Job) => void;
    vi.mocked(api.post).mockImplementation(
      () =>
        new Promise<Job>((resolve) => {
          finish = resolve;
        }),
    );
    const user = userEvent.setup();
    const client = renderSaved();
    await user.click(await screen.findByRole('button', { name: 'Retry search' }));
    expect(screen.getByRole('button', { name: 'Retrying search…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Start a new search' })).toBeDisabled();
    expect(api.post).toHaveBeenCalledExactlyOnceWith(`/jobs/${queryId}/retry`);
    await act(async () => finish(queued));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Cancel search' })).toBeEnabled(),
    );
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(['query-job', queryId])).toMatchObject({
      status: 'PREPROCESSED',
      cleanupDeadline: failed.cleanupDeadline,
      resultExpiresAt: failed.resultExpiresAt,
    });
    expect(sessionStorage.getItem('ecoguard.query.alice.pet')).toBe(queryId);
    expect(uploadFile).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Retry search' })).not.toBeInTheDocument();
  });

  it.each([
    ['permanent failure', { retryable: false }],
    ['already cleaned', { cleaned: true }],
    ['cancelled query', { status: 'CANCELLED' }],
    ['expired deadline', { cleanupDeadline: 1, resultExpiresAt: 1 }],
    ['missing deadline', { cleanupDeadline: undefined, resultExpiresAt: undefined }],
  ])('does not offer retry for %s', async (_name, override) => {
    const job = failedJob(override);
    vi.mocked(api.get).mockResolvedValue(job);
    renderSaved();
    await screen.findByRole('button', { name: 'Start a new search' });
    expect(screen.queryByRole('button', { name: 'Retry search' })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('removes retry when the original deadline passes while the failed task is no longer polling', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-08T00:00:00Z'));
    const deadline = Math.floor(Date.now() / 1000) + 2;
    const job = failedJob({ cleanupDeadline: deadline, resultExpiresAt: deadline });
    vi.mocked(api.get).mockResolvedValue(job);
    renderSaved(job);
    expect(screen.getByRole('button', { name: 'Retry search' })).toBeEnabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001);
    });
    expect(screen.queryByRole('button', { name: 'Retry search' })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('honours server expiry during a retry race and offers a new query instead', async () => {
    vi.mocked(api.get).mockResolvedValue(failedJob());
    vi.mocked(api.post).mockRejectedValue(
      new ApiError('The query expired. Submit a new query.', 'QUERY_EXPIRED', 410),
    );
    const user = userEvent.setup();
    renderSaved();
    await user.click(await screen.findByRole('button', { name: 'Retry search' }));
    await screen.findByText('The query expired. Submit a new query.');
    expect(screen.queryByRole('button', { name: 'Retry search' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start a new search' })).toBeEnabled();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledExactlyOnceWith(`/jobs/${queryId}/retry`);
  });

  it('keeps a transient request failure visible and waits for another user click without starting a new query', async () => {
    const failed = failedJob();
    vi.mocked(api.get).mockResolvedValue(failed);
    vi.mocked(api.post).mockRejectedValue(
      new ApiError('Network interrupted.', 'NETWORK_ERROR', 0, undefined, true),
    );
    const user = userEvent.setup();
    const client = renderSaved();
    await user.click(await screen.findByRole('button', { name: 'Retry search' }));
    await screen.findByText('Network interrupted.');
    expect(screen.getByRole('button', { name: 'Retry search' })).toBeEnabled();
    expect(client.getQueryData(['query-job', queryId])).toMatchObject({
      cleanupDeadline: failed.cleanupDeadline,
    });
    expect(api.post).toHaveBeenCalledExactlyOnceWith(`/jobs/${queryId}/retry`);
    expect(uploadFile).not.toHaveBeenCalled();
  });
});
