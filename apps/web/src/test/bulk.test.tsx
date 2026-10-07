import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BulkActions } from '../components/BulkActions';
import { api } from '../api/client';
import type { Media } from '../api/types';
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  api: { post: vi.fn() },
}));
vi.mock('../components/JobStatus', () => ({
  JobStatus: ({ id }: { id: string }) => <div role="status">Deletion task {id}</div>,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it('keeps a delete task visible if the library removes its selected record during processing', async () => {
  vi.mocked(api.post).mockResolvedValue({ items: [{ jobId: 'deletion-1', status: 'DELETING' }] });
  const client = new QueryClient();
  const clear = vi.fn();
  const user = userEvent.setup();
  const media: Media = {
    mediaId: 'media-1',
    fileType: 'image',
    status: 'READY',
    visibility: 'private',
    tags: [],
    url: 'http://localhost/media/media-1',
  };
  const { rerender } = render(
    <QueryClientProvider client={client}>
      <BulkActions items={[media]} onClear={clear} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole('button', { name: /^Delete$/ }));
  await user.click(screen.getByRole('button', { name: 'Delete sightings' }));
  expect(await screen.findByText('Deletion task deletion-1')).toBeVisible();
  rerender(
    <QueryClientProvider client={client}>
      <BulkActions items={[]} onClear={clear} />
    </QueryClientProvider>,
  );
  expect(screen.getByText('Delete 1 sighting?')).toBeVisible();
  expect(screen.getByText('Deletion task deletion-1')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(clear).toHaveBeenCalledWith('delete');
});

it('tracks accepted deletion jobs and retries only the failed media after a partial result', async () => {
  const media = ['accepted', 'retry'].map((id): Media => ({
    mediaId: id,
    fileType: 'image',
    status: 'READY',
    visibility: 'private',
    tags: [],
    url: `http://localhost/media/${id}`,
  }));
  vi.mocked(api.post)
    .mockResolvedValueOnce({
      items: [
        { mediaId: 'retry', status: 'failed', error: 'The record changed; retry this file.' },
        { mediaId: 'accepted', jobId: 'deletion-accepted', status: 'DELETING' },
      ],
    })
    .mockResolvedValueOnce({
      items: [{ mediaId: 'retry', jobId: 'deletion-retried', status: 'DELETING' }],
    });
  const clear = vi.fn();
  const user = userEvent.setup();
  const client = new QueryClient();
  const { rerender } = render(
    <QueryClientProvider client={client}>
      <BulkActions items={media} onClear={clear} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole('button', { name: /^Delete$/ }));
  await user.click(screen.getByRole('button', { name: 'Delete sightings' }));
  expect(await screen.findByText(/1 file\(s\) could not be updated/)).toBeVisible();
  expect(screen.getByText('Deletion task deletion-accepted')).toBeVisible();
  expect(api.post).toHaveBeenNthCalledWith(1, '/media/delete', {
    urls: media.map((item) => item.url),
  });

  // Accepted records can disappear from the gallery while this dialog remains open.
  rerender(
    <QueryClientProvider client={client}>
      <BulkActions items={[media[1]]} onClear={clear} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Retry failed files' }));
  expect(api.post).toHaveBeenNthCalledWith(2, '/media/delete', { urls: [media[1].url] });
  expect(await screen.findByText('Deletion task deletion-retried')).toBeVisible();
  expect(screen.getByText('Deletion task deletion-accepted')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Retry failed files' })).not.toBeInTheDocument();
  expect(screen.queryByText(/could not be updated/)).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(clear).toHaveBeenCalledWith('delete');
});

it('preserves accepted jobs and failed targets when a partial-deletion retry loses its connection', async () => {
  const media = ['accepted', 'retry'].map((id): Media => ({
    mediaId: id,
    fileType: 'image',
    status: 'READY',
    visibility: 'private',
    tags: [],
    url: `http://localhost/media/${id}`,
  }));
  vi.mocked(api.post)
    .mockResolvedValueOnce({
      items: [
        { mediaId: 'accepted', jobId: 'deletion-accepted', status: 'DELETING' },
        { mediaId: 'retry', status: 'failed', error: 'The record changed; retry this file.' },
      ],
    })
    .mockRejectedValueOnce(new Error('Connection unavailable'))
    .mockResolvedValueOnce({
      items: [{ mediaId: 'retry', jobId: 'deletion-retried', status: 'DELETING' }],
    });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BulkActions items={media} onClear={vi.fn()} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole('button', { name: /^Delete$/ }));
  await user.click(screen.getByRole('button', { name: 'Delete sightings' }));
  expect(await screen.findByText('Deletion task deletion-accepted')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry failed files' }));
  expect(await screen.findByText('Connection unavailable')).toBeVisible();
  expect(screen.getByText('Deletion task deletion-accepted')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry failed files' }));
  expect(api.post).toHaveBeenNthCalledWith(2, '/media/delete', { urls: [media[1].url] });
  expect(api.post).toHaveBeenNthCalledWith(3, '/media/delete', { urls: [media[1].url] });
  expect(await screen.findByText('Deletion task deletion-retried')).toBeVisible();
  expect(screen.getByText('Deletion task deletion-accepted')).toBeVisible();
});
