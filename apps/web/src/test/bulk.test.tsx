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
afterEach(cleanup);
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
