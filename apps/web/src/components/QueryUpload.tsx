import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Search, Upload, X, ShieldCheck } from 'lucide-react';
import { api, uploadFile } from '../api/client';
import { checksum, resultsOf, terminalJobStates, validateFile, videoDuration } from '../api/media';
import type { Job, UploadAuthorization } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { JobStatus } from './JobStatus';
import { MediaGrid } from './MediaGrid';
import { EmptyState, ErrorNotice } from './ui';
export function QueryUpload({ pet = false }: { pet?: boolean }) {
  const { user } = useAuth();
  const key = `ecoguard.query.${user?.id}.${pet ? 'pet' : 'file'}`;
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [description, setDescription] = useState('');
  const [days, setDays] = useState('30');
  const [queryId, setQueryId] = useState(() => sessionStorage.getItem(key) || '');
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const client = useQueryClient();
  useEffect(() => {
    if (!file?.type.startsWith('image/')) {
      setPreview('');
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const update = useCallback((next: Job) => setJob(next), []);
  const unavailable = useCallback(() => {
    sessionStorage.removeItem(key);
    setQueryId('');
    setJob(null);
    setError(
      new Error(
        'That saved search is no longer available. Start a new search with your file or description.',
      ),
    );
  }, [key]);
  async function search(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setJob(null);
    if (!file && (!pet || !description.trim())) {
      setError(
        new Error(
          pet
            ? 'Add a pet photo or describe the pet you are looking for.'
            : 'Choose a photo or video to search.',
        ),
      );
      return;
    }
    if (file) {
      const problem = validateFile(file, pet);
      if (problem) {
        setError(new Error(problem));
        return;
      }
    }
    setBusy(true);
    setProgress(null);
    try {
      if (file) await videoDuration(file);
      const created = await api.post<UploadAuthorization>('/query-jobs', {
        type: pet ? 'pet' : 'file',
        ...(file
          ? {
              filename: file.name,
              contentType: file.type,
              sizeBytes: file.size,
              sha256: await checksum(file),
            }
          : {}),
        ...(pet
          ? {
              description: description.trim(),
              from: new Date(Date.now() - Number(days) * 86400000).toISOString(),
              to: new Date().toISOString(),
            }
          : {}),
      });
      if (!created.queryId) throw new Error('The service did not return a query identifier.');
      setQueryId(created.queryId);
      sessionStorage.setItem(key, created.queryId);
      if (file) {
        if (!created.uploadUrl) throw new Error('The service did not provide an upload link.');
        setProgress(0);
        await uploadFile(created.uploadUrl, file, created.headers || {}, setProgress);
        await api.post(`/query-jobs/${encodeURIComponent(created.queryId)}/complete`);
      }
      await client.invalidateQueries({ queryKey: ['query-job', created.queryId] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }
  async function cancel() {
    if (!queryId) return;
    setError(undefined);
    setBusy(true);
    try {
      await api.post(`/query-jobs/${encodeURIComponent(queryId)}/cancel`);
      await client.invalidateQueries({ queryKey: ['query-job', queryId] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const active = Boolean(queryId && (!job || !terminalJobStates.has(job.status)));
  const results = job ? resultsOf(job) : [];
  const complete = job && ['READY', 'QUERY_READY', 'CLEANED', 'COMPLETED'].includes(job.status);
  return (
    <>
      <form className={`panel query-form ${pet ? 'pet-query' : ''}`} onSubmit={search}>
        <div className="query-upload-area">
          <label className="query-file-picker">
            {preview ? (
              <img
                className="query-preview"
                src={preview}
                alt="Photo selected for this temporary query"
              />
            ) : (
              <span className="upload-icon">
                <Upload size={27} />
              </span>
            )}
            <strong>
              {file ? file.name : pet ? 'Add a photo of your pet' : 'Choose a photo or video'}
            </strong>
            <span>
              {pet
                ? 'JPEG, PNG or WebP · up to 20 MB'
                : 'Find sightings containing all species detected in your file'}
            </span>
            <input
              type="file"
              accept={
                pet
                  ? 'image/jpeg,image/png,image/webp'
                  : 'image/jpeg,image/png,image/webp,video/mp4'
              }
              disabled={busy || active}
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setError(undefined);
              }}
            />
          </label>
          {file && (
            <button
              type="button"
              className="text-button"
              disabled={busy || active}
              onClick={() => setFile(null)}
            >
              <X size={14} /> Remove selected file
            </button>
          )}
        </div>
        {pet && (
          <div className="form-stack">
            <label>
              Describe the pet
              <textarea
                rows={4}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={busy || active}
                placeholder="A ginger cat with a white chest, short fur and green eyes…"
              />
              <small>A photo, a description, or both. Add only details you know.</small>
            </label>
            <label>
              Search public sightings from
              <select
                value={days}
                onChange={(e) => setDays(e.target.value)}
                disabled={busy || active}
              >
                <option value="7">The past 7 days</option>
                <option value="30">The past 30 days</option>
                <option value="90">The past 90 days</option>
                <option value="365">The past year</option>
              </select>
            </label>
          </div>
        )}
        <div className="query-submit">
          <p className="privacy-note">
            <ShieldCheck size={17} /> Query files stay out of the media library and are cleared
            after processing.
          </p>
          {progress !== null && (
            <progress value={progress} max={100} aria-label="Query upload progress" />
          )}
          <div className="button-row">
            <button className="button primary" disabled={busy || active} type="submit">
              <Search size={18} />
              {busy ? 'Preparing search…' : pet ? 'Find possible sightings' : 'Search by this file'}
            </button>
            {active && (
              <button
                className="button secondary"
                type="button"
                disabled={busy}
                onClick={() => void cancel()}
              >
                Cancel search
              </button>
            )}
          </div>
        </div>
      </form>
      <ErrorNotice error={error} />
      {queryId && (
        <section className="query-results">
          <h2>{pet ? 'Possible sightings' : 'Matching sightings'}</h2>
          <JobStatus id={queryId} query onUpdate={update} onUnavailable={unavailable} />
          {pet && results.length > 0 && (
            <p className="muted">
              These are visual or descriptive similarities, not a confirmed identification. Check
              each sighting carefully.
            </p>
          )}
          {results.length ? (
            <MediaGrid items={results} />
          ) : (
            complete && (
              <EmptyState title="No matching sightings this time.">
                {pet
                  ? 'Try another photo, adjust the description or expand the time range.'
                  : 'Try a different file with clearly visible animals. No detected species will never return the entire library.'}
              </EmptyState>
            )
          )}
          {job && terminalJobStates.has(job.status) && (
            <button
              className="text-button"
              onClick={() => {
                sessionStorage.removeItem(key);
                setQueryId('');
                setJob(null);
              }}
            >
              Start a new search
            </button>
          )}
        </section>
      )}
    </>
  );
}
