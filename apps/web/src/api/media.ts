import type { Job, Media, MediaList } from './types';
export const terminalJobStates = new Set([
  'READY',
  'QUERY_READY',
  'CLEANED',
  'COMPLETED',
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'EXPIRED',
  'DELETED',
]);
export function resultsOf(job: Job): Media[] {
  return Array.isArray(job.results)
    ? job.results
    : job.results?.items || job.items || job.result?.items || [];
}
export function matchDetails(media: Media) {
  const score = [media.matchScore, media.score].find(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  );
  const reason = media.matchReason?.trim();
  return { score, reasons: reason ? [reason] : media.reasons || [] };
}
export function asList(data: MediaList | Media[]): MediaList {
  return Array.isArray(data)
    ? { items: data }
    : { items: data.items || [], nextCursor: data.nextCursor };
}
export function label(tag: string) {
  return tag.replaceAll('_', ' ');
}
export function mediaName(media: Media) {
  return media.filename || media.fileName || `Sighting ${media.mediaId.slice(0, 8)}`;
}
export function mediaUrl(media: Media) {
  return media.url || `${window.location.origin}/media/${encodeURIComponent(media.mediaId)}`;
}
export function readableDate(date?: string) {
  if (!date) return 'Time not recorded';
  const parsed = new Date(date);
  return Number.isNaN(parsed.getTime())
    ? 'Time not recorded'
    : parsed.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
}
export function canonicalTag(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
}
export function parseTags(value: string) {
  return [...new Set(value.split(',').map(canonicalTag).filter(Boolean))];
}
export function minimumCounts(rows: { species: string; count: string }[]) {
  const output: Record<string, number> = {};
  for (const row of rows) {
    const species = canonicalTag(row.species);
    const count = Number(row.count);
    if (!species || !Number.isInteger(count) || count < 1)
      throw new Error(
        'Choose a species and a whole-number minimum of at least 1 for every condition.',
      );
    output[species] = Math.max(output[species] || 0, count);
  }
  return output;
}
export function validateFile(file: File, petOnly = false): string | null {
  const image = ['image/jpeg', 'image/png', 'image/webp'].includes(file.type);
  const video = file.type === 'video/mp4';
  if (!image && (!video || petOnly))
    return petOnly
      ? 'Choose a JPEG, PNG or WebP pet photo.'
      : 'Choose a JPEG, PNG, WebP image or MP4 video.';
  if (file.size === 0) return 'This file is empty.';
  if (file.size > (video ? 100 : 20) * 1024 * 1024)
    return video ? 'Videos must be 100 MB or smaller.' : 'Images must be 20 MB or smaller.';
  return null;
}
export async function checksum(file: File) {
  const buffer = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(buffer), (x) => x.toString(16).padStart(2, '0')).join('');
}
export async function videoDuration(file: File): Promise<number | undefined> {
  if (file.type !== 'video/mp4') return undefined;
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    const objectUrl = URL.createObjectURL(file);
    const cleanup = () => {
      URL.revokeObjectURL(objectUrl);
      video.removeAttribute('src');
      video.load();
    };
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new Error('We could not read this video. Try a different MP4 file.'));
    }, 10000);
    video.preload = 'metadata';
    video.onloadedmetadata = () => {
      clearTimeout(timer);
      const duration = video.duration;
      cleanup();
      if (!Number.isFinite(duration) || duration <= 0)
        reject(new Error('This video has an invalid duration.'));
      else if (duration > 120) reject(new Error('Videos must be 120 seconds or shorter.'));
      else resolve(duration);
    };
    video.onerror = () => {
      clearTimeout(timer);
      cleanup();
      reject(new Error('This MP4 could not be read. Check the video format.'));
    };
    video.src = objectUrl;
  });
}
