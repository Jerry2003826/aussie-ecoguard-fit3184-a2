import { useState } from 'react';
import type { FormEvent } from 'react';
import { Plus, Search, X, ArrowUpRight } from 'lucide-react';
import { api } from '../api/client';
import { minimumCounts } from '../api/media';
import type { Access, Media, MediaList } from '../api/types';
import { PageHeader, ErrorNotice, EmptyState, Spinner } from '../components/ui';
import { MediaGrid } from '../components/MediaGrid';
import { QueryUpload } from '../components/QueryUpload';
export function SearchPage() {
  const [mode, setMode] = useState('tags');
  const [rows, setRows] = useState([{ species: '', count: '1' }]);
  const [fileType, setFileType] = useState('all');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [items, setItems] = useState<Media[] | null>(null);
  const [cursor, setCursor] = useState<string | null>();
  const [lastQuery, setLastQuery] = useState<Record<string, unknown>>();
  const [thumbnail, setThumbnail] = useState('');
  const [original, setOriginal] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function tagsSearch(event?: FormEvent, more = false) {
    event?.preventDefault();
    setError(undefined);
    let body: Record<string, unknown>;
    try {
      body =
        more && lastQuery
          ? { ...lastQuery, cursor }
          : {
              minimumCounts: minimumCounts(rows),
              limit: 20,
              ...(fileType !== 'all' ? { fileType } : {}),
              ...(from ? { from: new Date(`${from}T00:00:00`).toISOString() } : {}),
              ...(to ? { to: new Date(`${to}T23:59:59.999`).toISOString() } : {}),
            };
      if (from && to && from > to)
        throw new Error('The start date must be on or before the end date.');
    } catch (e) {
      setError(e);
      return;
    }
    setBusy(true);
    try {
      const response = await api.post<MediaList>('/search/tags', body);
      setItems((current) => (more ? [...(current || []), ...response.items] : response.items));
      setCursor(response.nextCursor);
      if (!more) setLastQuery(body);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function lookup(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    setOriginal('');
    try {
      const result = await api.post<Access>('/search/thumbnail', {
        thumbnailUrl: thumbnail.trim(),
      });
      if (!result.originalUrl && !result.url)
        throw new Error('No original image URL was returned.');
      setOriginal(result.originalUrl || result.url || '');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="FOLLOW YOUR CURIOSITY"
        title="Explore sightings"
        description="Find the species, combinations and moments you’re looking for."
      />
      <div className="page-tabs" role="group" aria-label="Search method">
        {[
          { id: 'tags', name: 'Species & counts' },
          { id: 'file', name: 'Search by a file' },
          { id: 'thumbnail', name: 'Thumbnail lookup' },
        ].map((tab) => (
          <button
            key={tab.id}
            className={mode === tab.id ? 'active' : ''}
            aria-pressed={mode === tab.id}
            onClick={() => {
              setMode(tab.id);
              setError(undefined);
            }}
          >
            {tab.name}
          </button>
        ))}
      </div>
      {mode === 'file' ? (
        <QueryUpload />
      ) : mode === 'thumbnail' ? (
        <section className="panel">
          <h2>From a preview to the full picture.</h2>
          <p className="muted">Paste a thumbnail link from EcoGuard to find its original image.</p>
          <form onSubmit={lookup} className="form-stack">
            <label>
              Thumbnail URL
              <input
                type="url"
                value={thumbnail}
                required
                onChange={(e) => setThumbnail(e.target.value)}
                placeholder="https://…"
              />
            </label>
            <button className="button primary fit" disabled={busy}>
              {busy ? 'Looking up…' : 'Find original image'}
              <ArrowUpRight size={17} />
            </button>
          </form>
          <ErrorNotice error={error} />
          {original && (
            <div className="lookup-result">
              <a href={original} target="_blank" rel="noreferrer" className="button secondary">
                Open original image <ArrowUpRight size={17} />
              </a>
              <p className="small muted">
                The download link expires shortly. Run the lookup again for a fresh link.
              </p>
            </div>
          )}
        </section>
      ) : (
        <>
          <form className="panel" onSubmit={(e) => void tagsSearch(e)}>
            <div className="panel-heading">
              <div>
                <h2>What would you like to see?</h2>
                <p className="muted">Every condition must match. Counts are minimums.</p>
              </div>
              <span className="and-badge">MATCH ALL · AND</span>
            </div>
            <div className="conditions">
              {rows.map((row, index) => (
                <div className="condition-row" key={index}>
                  {index > 0 && <span className="condition-and">AND</span>}
                  <label>
                    Species
                    <input
                      list="species-options"
                      required
                      placeholder="e.g. fox"
                      value={row.species}
                      onChange={(e) =>
                        setRows((current) =>
                          current.map((x, i) =>
                            i === index ? { ...x, species: e.target.value } : x,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    At least
                    <input
                      type="number"
                      min="1"
                      step="1"
                      required
                      value={row.count}
                      onChange={(e) =>
                        setRows((current) =>
                          current.map((x, i) =>
                            i === index ? { ...x, count: e.target.value } : x,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    className="icon-button remove-condition"
                    type="button"
                    disabled={rows.length === 1}
                    aria-label={`Remove condition ${index + 1}`}
                    onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
                  >
                    <X size={18} />
                  </button>
                </div>
              ))}
            </div>
            <datalist id="species-options">
              {['cat', 'dog', 'fox', 'feral pig', 'cane toad', 'rabbit', 'bird'].map((x) => (
                <option key={x} value={x} />
              ))}
            </datalist>
            <button
              className="text-button"
              type="button"
              onClick={() => setRows((current) => [...current, { species: '', count: '1' }])}
            >
              <Plus size={16} /> Add another species
            </button>
            <div className="form-grid three search-options">
              <label>
                Media type
                <select value={fileType} onChange={(e) => setFileType(e.target.value)}>
                  <option value="all">Photos & videos</option>
                  <option value="image">Photos</option>
                  <option value="video">Videos</option>
                </select>
              </label>
              <label>
                Observed from
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <label>
                Observed to
                <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
              </label>
            </div>
            <button className="button primary" type="submit" disabled={busy}>
              <Search size={18} />
              {busy ? 'Searching…' : 'Search sightings'}
            </button>
          </form>
          <ErrorNotice error={error} />
          {busy && items === null && <Spinner text="Looking through sightings…" />}
          {items !== null && (
            <section className="search-results">
              <div className="results-heading">
                <h2>Matching sightings</h2>
                <span className="count-label">{items.length} loaded</span>
              </div>
              {items.length ? (
                <MediaGrid items={items} />
              ) : (
                !error && (
                  <EmptyState title="No sightings match all conditions.">
                    Try a lower minimum or remove a species. Private sightings from other users are
                    never included.
                  </EmptyState>
                )
              )}
              {cursor && (
                <div className="load-more">
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => void tagsSearch(undefined, true)}
                  >
                    Load more results
                  </button>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}
