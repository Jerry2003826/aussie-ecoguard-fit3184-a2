import { useMemo, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Plus, SlidersHorizontal, RefreshCw, Image, Film, LockKeyhole } from 'lucide-react';
import { api } from '../api/client';
import type { MediaList } from '../api/types';
import { MediaGrid } from '../components/MediaGrid';
import { UploadPanel } from '../components/UploadPanel';
import { BulkActions } from '../components/BulkActions';
import { EmptyState, ErrorNotice, PageHeader, Spinner } from '../components/ui';
export function Library() {
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [type, setType] = useState('all');
  const [selectionError, setSelectionError] = useState('');
  const query = useInfiniteQuery({
    queryKey: ['media', 'mine'],
    queryFn: ({ pageParam, signal }) =>
      api.get<MediaList>(
        `/media?scope=mine&limit=24${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`,
        signal,
      ),
    initialPageParam: '',
    getNextPageParam: (last) => last.nextCursor || undefined,
    refetchInterval: (q) =>
      q.state.data?.pages.some((page) =>
        page.items.some((item) => !['READY', 'FAILED', 'DELETED'].includes(item.status)),
      )
        ? document.hidden
          ? 15000
          : 5000
        : false,
  });
  const all = useMemo(
    () =>
      Array.from(
        new Map(
          (query.data?.pages.flatMap((page) => page.items) || []).map((media) => [
            media.mediaId,
            media,
          ]),
        ).values(),
      ),
    [query.data],
  );
  const visible = all.filter(
    (media) => type === 'all' || media.fileType === type || media.fileType.startsWith(`${type}/`),
  );
  function select(id: string, checked: boolean) {
    if (checked && selected.size >= 10) {
      setSelectionError('Select up to 10 sightings for each batch operation.');
      return;
    }
    setSelectionError('');
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  return (
    <>
      <PageHeader
        eyebrow="YOUR FIELD NOTEBOOK"
        title="My media"
        description="Every observation, thoughtfully organised."
        action={
          <button className="button primary" onClick={() => setUploadOpen(true)}>
            <Plus size={19} /> Add a sighting
          </button>
        }
      />
      <section className="library-intro">
        <div>
          <span className="intro-icon">
            <LeafMark />
          </span>
          <div>
            <h2>A closer look at the world around you.</h2>
            <p>Upload a sighting. Discover its species. Share it when you’re ready.</p>
          </div>
        </div>
        <span className="privacy-note">
          <LockKeyhole size={15} /> Private by default
        </span>
      </section>
      <div className="section-toolbar">
        <div className="filter-tabs" role="group" aria-label="Filter media type">
          {[
            { value: 'all', text: 'All media', icon: null },
            { value: 'image', text: 'Photos', icon: <Image size={15} /> },
            { value: 'video', text: 'Videos', icon: <Film size={15} /> },
          ].map((tab) => (
            <button
              key={tab.value}
              aria-pressed={type === tab.value}
              className={type === tab.value ? 'active' : ''}
              onClick={() => setType(tab.value)}
            >
              {tab.icon}
              {tab.text}
            </button>
          ))}
        </div>
        <div className="button-row">
          <span className="count-label">{visible.length} loaded</span>
          <button
            className="icon-button"
            aria-label="Refresh library"
            onClick={() => void query.refetch()}
          >
            <RefreshCw size={17} className={query.isFetching ? 'spin' : ''} />
          </button>
        </div>
      </div>
      <ErrorNotice error={query.error} onRetry={() => void query.refetch()} />
      {visible.length > 0 && (
        <div className="selection-row">
          <label className="inline-check">
            <input
              type="checkbox"
              checked={
                visible.length > 0 &&
                visible.slice(0, 10).every((media) => selected.has(media.mediaId))
              }
              onChange={(event) =>
                setSelected(
                  event.target.checked
                    ? new Set(visible.slice(0, 10).map((media) => media.mediaId))
                    : new Set(),
                )
              }
            />{' '}
            {visible.length > 10 ? 'Select first 10' : 'Select visible'}
          </label>
          <span>
            <SlidersHorizontal size={14} /> Newest observations first
          </span>
        </div>
      )}
      <ErrorNotice error={selectionError} />
      <BulkActions
        items={all.filter((media) => selected.has(media.mediaId))}
        onClear={() => {
          setSelected(new Set());
          void query.refetch();
        }}
      />
      {query.isPending ? (
        <Spinner text="Opening your field notebook…" />
      ) : visible.length ? (
        <MediaGrid items={visible} selected={selected} onSelect={select} />
      ) : (
        !query.error && (
          <EmptyState
            title={
              type === 'all'
                ? 'Your next discovery starts here.'
                : `No ${type === 'image' ? 'photos' : 'videos'} yet.`
            }
            action={
              <button className="button primary" onClick={() => setUploadOpen(true)}>
                <Plus size={17} /> Add your first sighting
              </button>
            }
          >
            Add an animal photo or video. Its species tags, preview and details will appear once
            processing finishes.
          </EmptyState>
        )
      )}
      {query.hasNextPage && (
        <div className="load-more">
          <button
            className="button secondary"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? 'Loading…' : 'Load more sightings'}
          </button>
        </div>
      )}
      {uploadOpen && <UploadPanel onClose={() => setUploadOpen(false)} />}
    </>
  );
}
function LeafMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="27"
      height="27"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <path d="M19 4c-8-1-14 4-12 10 2 6 11 4 12-10Z" />
      <path d="m4 21 10-11" />
    </svg>
  );
}
