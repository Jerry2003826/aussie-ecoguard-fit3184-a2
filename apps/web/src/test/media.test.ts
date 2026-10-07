import { describe, expect, it } from 'vitest';
import { minimumCounts, parseTags, resultsOf, validateFile } from '../api/media';
describe('query contract and file boundaries', () => {
  it('preserves AND clauses and keeps the strictest repeated species minimum', () => {
    expect(
      minimumCounts([
        { species: 'Feral pig', count: '1' },
        { species: 'cane toad', count: '3' },
        { species: 'FERAL PIG', count: '2' },
      ]),
    ).toEqual({ feral_pig: 2, cane_toad: 3 });
  });
  it.each(['0', '-1', '1.5', '', 'no'])(
    'rejects an invalid minimum %s instead of weakening the query',
    (count) => {
      expect(() => minimumCounts([{ species: 'fox', count }])).toThrow();
    },
  );
  it('rejects blank species', () => {
    expect(() => minimumCounts([{ species: ' ', count: '1' }])).toThrow();
  });
  it('canonicalises and deduplicates manual tags', () => {
    expect(parseTags('Ginger, ginger, cane toad, ,Cat')).toEqual(['ginger', 'cane_toad', 'cat']);
  });
  it('does not accept video as a pet photo', () => {
    expect(validateFile(new File(['x'], 'pet.mp4', { type: 'video/mp4' }), true)).toContain(
      'photo',
    );
  });
  it('rejects empty and unsupported files before requesting authorization', () => {
    expect(validateFile(new File([], 'empty.png', { type: 'image/png' }))).toContain('empty');
    expect(validateFile(new File(['x'], 'file.pdf', { type: 'application/pdf' }))).toContain(
      'Choose',
    );
  });
  it('reads results from the agreed async query envelope', () => {
    expect(resultsOf({ status: 'QUERY_READY', items: [] })).toEqual([]);
  });
});
