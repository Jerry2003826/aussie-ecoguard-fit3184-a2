import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ExternalLink, RefreshCw, Copy, LockKeyhole, Globe2 } from 'lucide-react';
import { api } from '../api/client';
import type { Access, Media } from '../api/types';
import { label, mediaName, readableDate } from '../api/media';
import { useAuth } from '../auth/AuthProvider';
import { ErrorNotice, Notice, PageHeader, Spinner, StatusBadge } from '../components/ui';
import { BulkActions } from '../components/BulkActions';
import { JobStatus } from '../components/JobStatus';
export function MediaDetail() {
  const navigate = useNavigate();
  const { id = '' } = useParams();
  const auth = useAuth();
  const client = useQueryClient();
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [linkFailed, setLinkFailed] = useState(false);
  const query = useQuery({
    queryKey: ['media', id],
    queryFn: ({ signal }) => api.get<Media>(`/media/${encodeURIComponent(id)}`, signal),
    refetchInterval: (q) =>
      q.state.data && !['READY', 'FAILED', 'DELETED'].includes(q.state.data.status) ? 5000 : false,
  });
  const access = useQuery({
    queryKey: ['access', id, 'original'],
    queryFn: () =>
      api.post<Access>(`/media/${encodeURIComponent(id)}/access`, { kind: 'original' }),
    enabled: query.data?.status === 'READY',
    staleTime: 45000,
    retry: false,
  });
  async function visibility(value: string) {
    setBusy(true);
    setError(undefined);
    try {
      await api.patch(`/media/${encodeURIComponent(id)}`, {
        visibility: value,
        recordVersion: query.data?.recordVersion,
      });
      await client.invalidateQueries({ queryKey: ['media'] });
      setMessage(
        value === 'public'
          ? 'This sighting is now visible to signed-in community members.'
          : 'This sighting is now private. Previously issued short-lived media links expire shortly.',
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function retry() {
    if (!query.data?.jobId) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.post(`/jobs/${encodeURIComponent(query.data.jobId)}/retry`);
      await query.refetch();
      setMessage('A new processing attempt has been requested.');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function copyThumbnail() {
    setError(undefined);
    try {
      const result = await api.post<Access>(`/media/${encodeURIComponent(id)}/access`, {
        kind: 'thumbnail',
      });
      if (!result.url && !result.thumbnailUrl)
        throw new Error('No thumbnail link is available yet.');
      await navigator.clipboard.writeText(result.thumbnailUrl || result.url || '');
      setMessage('Thumbnail link copied. You can use it in thumbnail lookup.');
    } catch (e) {
      setError(e);
    }
  }
  const media = query.data;
  const source = access.data?.originalUrl || access.data?.url;
  const owned = media?.canEdit ?? media?.ownerSub === auth.user?.id;
  return (
    <>
      <Link className="back-link" to="/media">
        <ArrowLeft size={16} /> Back to my media
      </Link>
      {query.isPending ? (
        <Spinner text="Opening sighting…" />
      ) : query.error ? (
        <ErrorNotice error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        media && (
          <>
            <PageHeader
              eyebrow="SIGHTING DETAILS"
              title={mediaName(media)}
              description={`Observed ${readableDate(media.observedAt || media.uploadedAt || media.createdAt)}`}
            />
            <ErrorNotice error={error} />
            {message && <Notice>{message}</Notice>}
            <div className="detail-layout">
              <section className="detail-preview panel">
                {source && !linkFailed ? (
                  media.fileType === 'video' ? (
                    <video
                      src={source}
                      controls
                      preload="metadata"
                      onError={() => setLinkFailed(true)}
                      aria-label={mediaName(media)}
                    />
                  ) : (
                    <img src={source} alt={mediaName(media)} onError={() => setLinkFailed(true)} />
                  )
                ) : (
                  <div className="detail-pending">
                    <StatusBadge status={media.status} />
                    <p>
                      {media.status === 'READY'
                        ? 'Open a fresh link to view the original media.'
                        : 'The original media will be available when processing completes.'}
                    </p>
                  </div>
                )}
                <div className="preview-actions">
                  <button
                    className="button secondary small-button"
                    disabled={media.status !== 'READY' || access.isFetching}
                    onClick={() => {
                      setLinkFailed(false);
                      void access.refetch();
                    }}
                  >
                    <RefreshCw size={15} /> Refresh media link
                  </button>
                  {source && (
                    <a
                      className="button secondary small-button"
                      target="_blank"
                      rel="noreferrer"
                      href={source}
                    >
                      Open original <ExternalLink size={14} />
                    </a>
                  )}
                </div>
                <ErrorNotice error={access.error} />
                {linkFailed && (
                  <ErrorNotice error="This media link may have expired. Refresh it to continue." />
                )}
              </section>
              <aside className="detail-sidebar">
                <section className="panel">
                  <h2>Observation</h2>
                  <StatusBadge status={media.status} />
                  <dl>
                    <div>
                      <dt>Media type</dt>
                      <dd>{media.fileType}</dd>
                    </div>
                    <div>
                      <dt>Category</dt>
                      <dd>{label(media.classification || 'unclassified')}</dd>
                    </div>
                    <div>
                      <dt>Model version</dt>
                      <dd>{media.modelVersion || 'Not available yet'}</dd>
                    </div>
                  </dl>
                  {owned ? (
                    <label>
                      Who can see this?
                      <select
                        value={media.visibility}
                        onChange={(e) => void visibility(e.target.value)}
                        disabled={busy}
                      >
                        <option value="private">Private — only you</option>
                        <option value="public">Signed-in community</option>
                      </select>
                    </label>
                  ) : (
                    <p className="privacy-note">
                      {media.visibility === 'public' ? (
                        <Globe2 size={16} />
                      ) : (
                        <LockKeyhole size={16} />
                      )}{' '}
                      {media.visibility === 'public' ? 'Public sighting' : 'Private sighting'}
                    </p>
                  )}
                </section>
                <section className="panel">
                  <h2>Species & tags</h2>
                  <div className="tags large-tags">
                    {media.tags?.map((tag) => (
                      <span key={tag}>
                        {label(tag)}
                        {media.counts?.[tag] ? ` · ${media.counts[tag]}` : ''}
                      </span>
                    ))}
                  </div>
                  {!media.tags?.length && <p className="muted">No animal tags detected yet.</p>}
                  {media.fileType === 'video' && (
                    <p className="small muted">
                      Counts show the largest number visible in one 10-second sample. They are not a
                      count of distinct animals across the video.
                    </p>
                  )}
                  <button
                    className="text-button"
                    disabled={media.status !== 'READY'}
                    onClick={() => void copyThumbnail()}
                  >
                    <Copy size={15} /> Copy thumbnail URL
                  </button>
                </section>
              </aside>
            </div>
            {media.error && (
              <ErrorNotice error={media.error.message || 'Processing needs attention.'} />
            )}{' '}
            {owned && media.status === 'FAILED' && media.jobId && (
              <button className="button secondary" disabled={busy} onClick={() => void retry()}>
                <RefreshCw size={16} /> Retry processing
              </button>
            )}
            {media.jobId && !['READY', 'DELETED'].includes(media.status) && (
              <JobStatus id={media.jobId} />
            )}{' '}
            {owned && (
              <BulkActions
                items={[media]}
                onClear={(action) => {
                  if (action === 'delete') navigate('/media');
                  else void query.refetch();
                }}
              />
            )}
          </>
        )
      )}
    </>
  );
}
