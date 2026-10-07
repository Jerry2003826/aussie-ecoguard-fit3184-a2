import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SearchPage } from '../pages/SearchPage';
import { api } from '../api/client';
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  api: { post: vi.fn() },
}));
afterEach(cleanup);
describe('search UI preserves query intent', () => {
  it('submits all species with their independent minima and shows a truthful empty result', async () => {
    vi.mocked(api.post).mockResolvedValue({ items: [], nextCursor: null });
    const user = userEvent.setup();
    render(<SearchPage />);
    await user.type(screen.getByRole('combobox', { name: 'Species' }), 'feral pig');
    await user.click(screen.getByRole('button', { name: 'Add another species' }));
    await user.type(screen.getAllByRole('combobox', { name: 'Species' })[1], 'cane toad');
    fireEvent.change(screen.getAllByRole('spinbutton', { name: 'At least' })[1], {
      target: { value: '3' },
    });
    await user.click(screen.getByRole('button', { name: 'Search sightings' }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/search/tags', {
        minimumCounts: { feral_pig: 1, cane_toad: 3 },
        limit: 20,
      }),
    );
    expect(await screen.findByText('No sightings match all conditions.')).toBeVisible();
  });
  it('shows backend failure as an error rather than claiming there are no matching sightings', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('The search service is unavailable.'));
    const user = userEvent.setup();
    render(<SearchPage />);
    await user.type(screen.getByRole('combobox', { name: 'Species' }), 'fox');
    await user.click(screen.getByRole('button', { name: 'Search sightings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The search service is unavailable.',
    );
    expect(screen.queryByText('No sightings match all conditions.')).not.toBeInTheDocument();
  });
});
