import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Image, Film, LockKeyhole, Globe2, ArrowUpRight, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import type { Access, Media } from '../api/types';
import { label, mediaName, readableDate } from '../api/media';
import { StatusBadge } from './ui';
export function MediaCard({
  media,
  selected,
  onSelect,
}: {
  media: Media;
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const thumbnail = useQuery({
    queryKey: ['access', media.mediaId, 'thumbnail'],
    queryFn: () =>
      api.post<Access>(`/media/${encodeURIComponent(media.mediaId)}/access`, { kind: 'thumbnail' }),
    enabled: media.status === 'READY' && !media.thumbnailUrl,
    staleTime: 45000,
    retry: false,
  });
  const url = thumbnail.data?.thumbnailUrl || thumbnail.data?.url || media.thumbnailUrl;
  const video = media.fileType === 'video' || media.fileType.startsWith('video/');
  return (
    <article className={`media-card ${selected ? 'selected' : ''}`}>
      {onSelect && (
        <label className="card-select">
          <input
            type="checkbox"
            checked={Boolean(selected)}
            onChange={(e) => onSelect(e.target.checked)}
            aria-label={`Select ${mediaName(media)}`}
          />
        </label>
      )}
      <Link
        to={`/media/${encodeURIComponent(media.mediaId)}`}
        className="media-preview"
        aria-label={`Open ${mediaName(media)}`}
      >
        {url && !imageFailed ? (
          <img
            src={url}
            alt={mediaName(media)}
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="media-placeholder">
            {video ? <Film size={36} /> : <Image size={36} />}
            <span>
              {media.status === 'READY'
                ? 'Preview unavailable'
                : media.status === 'FAILED'
                  ? 'Processing needs attention'
                  : 'Your sighting is being prepared'}
            </span>
          </div>
        )}
        <span className="media-kind">
          {video ? <Film size={13} /> : <Image size={13} />} {video ? 'Video' : 'Photo'}
        </span>
        <span className="card-open">
          <ArrowUpRight size={18} />
        </span>
      </Link>
      <div className="card-content">
        <div className="card-title">
          <Link to={`/media/${encodeURIComponent(media.mediaId)}`}>{mediaName(media)}</Link>
          {media.visibility === 'public' ? (
            <Globe2 size={15} aria-label="Public sighting" />
          ) : (
            <LockKeyhole size={15} aria-label="Private" />
          )}
        </div>
        <p className="card-date">
          {readableDate(media.observedAt || media.uploadedAt || media.createdAt)}
        </p>
        <div className="tags">
          {(media.tags || []).slice(0, 3).map((tag) => (
            <span key={tag}>
              {label(tag)}
              {media.counts?.[tag] ? ` · ${media.counts[tag]}` : ''}
            </span>
          ))}
          {(media.tags?.length || 0) > 3 && <span>+{media.tags.length - 3}</span>}
        </div>
        {media.status !== 'READY' && <StatusBadge status={media.status} />}{' '}
        {media.status === 'READY' && media.tags?.length === 0 && (
          <p className="small muted">No animal tags detected</p>
        )}
        {typeof media.score === 'number' && (
          <p className="match-label">
            Possible match · {(media.score * 100).toFixed(0)}% similarity
          </p>
        )}
        {media.reasons?.length ? <p className="small muted">{media.reasons.join(' · ')}</p> : null}
        {(thumbnail.isError || imageFailed) && (
          <button
            className="text-button small"
            onClick={() => {
              setImageFailed(false);
              void thumbnail.refetch();
            }}
          >
            <RefreshCw size={13} /> Retry preview
          </button>
        )}
      </div>
    </article>
  );
}
export function MediaGrid({
  items,
  selected,
  onSelect,
}: {
  items: Media[];
  selected?: Set<string>;
  onSelect?: (id: string, checked: boolean) => void;
}) {
  return (
    <div className="media-grid">
      {items.map((media) => (
        <MediaCard
          key={media.mediaId}
          media={media}
          selected={selected?.has(media.mediaId)}
          onSelect={onSelect ? (checked) => onSelect(media.mediaId, checked) : undefined}
        />
      ))}
    </div>
  );
}
