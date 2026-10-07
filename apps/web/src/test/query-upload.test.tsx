import { useEffect } from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryUpload } from '../components/QueryUpload';
import { api, uploadFile } from '../api/client';
import type { Job } from '../api/types';

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { id: 'alice' } }) }));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  api: { post: vi.fn() },
  uploadFile: vi.fn(),
}));
vi.mock('../api/media', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/media')>()),
  checksum: vi.fn().mockResolvedValue('test-file-checksum'),
}));
vi.mock('../components/JobStatus', () => ({
  JobStatus: ({ id, onUpdate }: { id: string; onUpdate?: (job: Job) => void }) => {
    useEffect(() => {
      onUpdate?.({ jobId: id, status: 'COMPLETED', items: [] });
    }, [id, onUpdate]);
    return <span>Completed test query</span>;
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn().mockReturnValue('blob:test-query-photo');
      static revokeObjectURL = vi.fn();
    },
  );
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

function renderQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QueryUpload pet />
    </QueryClientProvider>,
  );
}

describe('temporary pet query workflow', () => {
  it('remains editable if the next query fails after a completed saved search', async () => {
    sessionStorage.setItem('ecoguard.query.alice.pet', 'completed-query');
    vi.mocked(api.post).mockRejectedValue(new Error('Inference is unavailable.'));
    const user = userEvent.setup();
    renderQuery();
    await screen.findByText('No matching sightings this time.');
    await user.type(screen.getByRole('textbox', { name: /Describe the pet/ }), 'Ginger cat');
    await user.click(screen.getByRole('button', { name: 'Find possible sightings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Inference is unavailable.');
    expect(screen.getByRole('button', { name: 'Find possible sightings' })).toBeEnabled();
    expect(screen.getByRole('textbox', { name: /Describe the pet/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Cancel search' })).not.toBeInTheDocument();
  });

  it('sends both photo metadata and the description without silently dropping either input', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        queryId: 'joint-query',
        uploadUrl: 'http://127.0.0.1/upload',
        headers: {},
      })
      .mockResolvedValueOnce({});
    vi.mocked(uploadFile).mockResolvedValue(undefined);
    const user = userEvent.setup();
    const { container } = renderQuery();
    const photo = new File(['test-photo-bytes'], 'ginger-cat.png', { type: 'image/png' });
    await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, photo);
    await user.type(
      screen.getByRole('textbox', { name: /Describe the pet/ }),
      'White chest, ginger fur',
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Search public sightings from' }),
      '7',
    );
    await user.click(screen.getByRole('button', { name: 'Find possible sightings' }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/query-jobs',
        expect.objectContaining({
          type: 'pet',
          filename: 'ginger-cat.png',
          contentType: 'image/png',
          sizeBytes: photo.size,
          sha256: 'test-file-checksum',
          description: 'White chest, ginger fur',
          from: expect.any(String),
          to: expect.any(String),
        }),
      ),
    );
    expect(uploadFile).toHaveBeenCalledWith(
      'http://127.0.0.1/upload',
      photo,
      {},
      expect.any(Function),
    );
    expect(api.post).toHaveBeenCalledWith('/query-jobs/joint-query/complete');
  });
});
