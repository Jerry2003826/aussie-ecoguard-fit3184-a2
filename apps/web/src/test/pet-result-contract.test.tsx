import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { MediaGrid } from '../components/MediaGrid';
import { resultsOf } from '../api/media';
import type { Job, Media } from '../api/types';
import { loadPetContract } from './pet-contract-loader.js';

vi.mock('../api/client', () => ({ api: { post: vi.fn().mockResolvedValue({}) } }));
afterEach(cleanup);
let jobs: Record<string, Job>;

beforeAll(() => {
  // Consume the real Python job_view output, with explicitly local fixture records.
  jobs = loadPetContract();
});

function show(items: Media[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <MediaGrid items={items} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('real backend pet result display contract', () => {
  it.each([
    ['image', 'Image similarity to a detected pet.'],
    ['text', 'Description similarity to a detected pet.'],
    ['combined', 'Image and description similarity, weighted equally for the same detected pet.'],
  ])('shows the %s score and reason from job_view without a probability claim', (mode, reason) => {
    const { container } = show(resultsOf(jobs[mode]));
    expect(screen.getByText(/Similarity score 0\.556/)).toBeInTheDocument();
    expect(
      screen.getByText(`${reason} Possible sighting, not confirmed identity.`),
    ).toBeInTheDocument();
    expect(container.textContent).not.toContain('%');
  });

  it('keeps score/reasons clients compatible without rendering percent confidence', () => {
    const base = resultsOf(jobs.image)[0];
    const {
      matchScore: _oldScore,
      matchReason: _oldReason,
      ...legacy
    } = base as Media & { matchScore?: number; matchReason?: string };
    const { container } = show([
      { ...legacy, score: 0.7, reasons: ['Legacy similarity explanation.'] },
    ]);
    expect(screen.getByText(/Similarity score 0\.700/)).toBeInTheDocument();
    expect(screen.getByText('Legacy similarity explanation.')).toBeInTheDocument();
    expect(container.textContent).not.toContain('%');
  });

  it('does not display a nonfinite result score', () => {
    const base = resultsOf(jobs.image)[0];
    show([{ ...base, matchScore: Number.NaN, score: Number.POSITIVE_INFINITY } as Media]);
    expect(screen.queryByText(/Similarity score|% similarity/)).not.toBeInTheDocument();
  });
});
